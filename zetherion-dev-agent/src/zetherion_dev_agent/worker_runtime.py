"""Worker-mode runtime for laptop sub-worker execution."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import re
import secrets
import shutil
import subprocess
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import httpx

from zetherion_dev_agent.config import AgentConfig
from zetherion_dev_agent.policy_store import PolicyStore

SESSION_META_KEY = "worker_session"
INFLIGHT_META_KEY = "worker_inflight_job"
RESTART_RECOVERY_ERROR_CODE = "WORKER_RECOVERED_AFTER_RESTART"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_now_iso() -> str:
    return _utc_now().isoformat()


def _canonical_json(payload: dict[str, Any]) -> str:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True)


def _parse_string_list(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return []
    parsed: list[str] = []
    for item in value:
        text = str(item).strip()
        if text:
            parsed.append(text)
    return parsed


def _sanitize_for_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "_" for ch in value)
    trimmed = safe.strip("._")
    return trimmed or "job"


def _is_stale_worker_session_error(exc: WorkerApiError) -> bool:
    if int(getattr(exc, "status_code", 0)) in {401, 403, 404}:
        return True
    message = str(exc).strip().lower()
    if "worker session not found" in message:
        return True
    payload = getattr(exc, "payload", {}) or {}
    payload_message = str(payload.get("error") or "").strip().lower()
    return "worker session not found" in payload_message


_WINDOWS_DRIVE_PATH_RE = re.compile(r"^(?P<drive>[a-zA-Z]):[\\/]*(?P<rest>.*)$")
_WORKSPACE_EPHEMERAL_DIR_GLOBS = (
    "test-results",
    "test-results-*",
    "playwright-report",
    "playwright-report-*",
    "htmlcov",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".next",
    ".swc",
)
_WORKSPACE_EPHEMERAL_FILE_GLOBS = (
    ".coverage*",
    "coverage*.xml",
    "pytestdebug.log",
    "._*",
)
_PRESERVED_ARTIFACT_FILES = {
    "ci-worker-connectivity.json",
    "coverage-gaps.json",
    "coverage-summary.json",
    "diagnostic-findings.json",
    "diagnostic-summary.json",
    "e2e-receipt.json",
    "local-readiness-receipt.json",
    "worker-certification-receipt.json",
    "workspace-readiness-receipt.json",
}
_JSON_ARTIFACT_FILES = {
    "ci_worker_connectivity_receipt": "ci-worker-connectivity.json",
    "coverage_gaps": "coverage-gaps.json",
    "coverage_summary": "coverage-summary.json",
    "diagnostic_findings": "diagnostic-findings.json",
    "diagnostic_summary": "diagnostic-summary.json",
    "e2e_receipt": "e2e-receipt.json",
    "local_readiness_receipt": "local-readiness-receipt.json",
    "worker_certification_receipt": "worker-certification-receipt.json",
    "workspace_readiness_receipt": "workspace-readiness-receipt.json",
}
_TOOL_IMAGE_CONTEXT_RELATIVE_PATHS = (
    "Dockerfile.dev-tools",
    "requirements.txt",
    "requirements-dev.txt",
    "docs/requirements.txt",
)


def _translate_windows_path_to_wsl(path: str) -> str:
    """Translate a Windows host path into the Linux mount path used inside WSL."""

    raw = str(path).strip()
    if not raw:
        return raw
    match = _WINDOWS_DRIVE_PATH_RE.match(raw)
    if not match:
        return raw.replace("\\", "/")
    drive = match.group("drive").lower()
    rest = match.group("rest").replace("\\", "/").lstrip("/")
    return f"/mnt/{drive}/{rest}" if rest else f"/mnt/{drive}"


def _compute_tool_image_context_hash(workspace_root: Path) -> str:
    digest = hashlib.sha256()
    seen_any = False
    for relative_path in _TOOL_IMAGE_CONTEXT_RELATIVE_PATHS:
        candidate = workspace_root / relative_path
        if not candidate.exists() or not candidate.is_file():
            continue
        seen_any = True
        digest.update(relative_path.encode("utf-8"))
        digest.update(b"\0")
        digest.update(candidate.read_bytes())
        digest.update(b"\0")
    return digest.hexdigest() if seen_any else ""


def _wsl_socket_path_available(
    distribution: str,
    socket_path: str = "/var/run/docker.sock",
) -> bool:
    distro = str(distribution).strip()
    if not distro:
        return False
    try:
        result = subprocess.run(
            ["wsl.exe", "-d", distro, "--", "test", "-S", socket_path],
            check=False,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        return False
    return int(result.returncode) == 0


def _truncate_text_to_bytes(value: str, max_bytes: int) -> tuple[str, bool]:
    """Bound text by UTF-8 byte size while preserving head/tail context."""

    text = str(value or "")
    budget = max(64, int(max_bytes))
    encoded = text.encode("utf-8")
    if len(encoded) <= budget:
        return text, False

    marker = "\n...[truncated]...\n"
    marker_bytes = marker.encode("utf-8")
    if budget <= len(marker_bytes) + 32:
        clipped = encoded[:budget].decode("utf-8", errors="replace")
        return clipped, True

    keep_each_side = max(16, (budget - len(marker_bytes)) // 2)
    head = encoded[:keep_each_side].decode("utf-8", errors="replace")
    tail = encoded[-keep_each_side:].decode("utf-8", errors="replace")
    truncated = f"{head}{marker}{tail}"
    truncated_encoded = truncated.encode("utf-8")
    if len(truncated_encoded) <= budget:
        return truncated, True
    return truncated_encoded[:budget].decode("utf-8", errors="replace"), True


def _load_workspace_json_artifacts(
    workspace_root: Path,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Load known JSON artifact receipts from the workspace when present."""

    payloads: dict[str, Any] = {}
    paths: dict[str, str] = {}
    for key, filename in _JSON_ARTIFACT_FILES.items():
        for candidate in (
            workspace_root / ".artifacts" / filename,
            workspace_root / ".ci" / filename,
        ):
            if not candidate.is_file():
                continue
            try:
                payload = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            payloads[key] = payload
            paths[key] = str(candidate)
            break
    return payloads, paths


def _bounded_stream_output(
    *,
    stdout: str,
    stderr: str,
    max_artifact_bytes: int,
) -> tuple[str, str, dict[str, Any]]:
    """Return bounded stdout/stderr previews plus capture metadata."""

    stdout_bytes = len(stdout.encode("utf-8"))
    stderr_bytes = len(stderr.encode("utf-8"))
    captured_size = stdout_bytes + stderr_bytes
    metadata: dict[str, Any] = {
        "stdout_bytes": stdout_bytes,
        "stderr_bytes": stderr_bytes,
        "artifact_bytes": captured_size,
        "max_artifact_bytes": int(max_artifact_bytes),
        "artifacts_truncated": False,
    }
    if captured_size <= int(max_artifact_bytes):
        return stdout, stderr, metadata

    preview_budget = max(1024, int(max_artifact_bytes) // 4)
    bounded_stdout, stdout_truncated = _truncate_text_to_bytes(stdout, preview_budget)
    bounded_stderr, stderr_truncated = _truncate_text_to_bytes(stderr, preview_budget)
    metadata.update(
        {
            "artifacts_truncated": True,
            "stdout_truncated": stdout_truncated,
            "stderr_truncated": stderr_truncated,
        }
    )
    return bounded_stdout, bounded_stderr, metadata


def _bounded_log_chunks(
    chunks: list[dict[str, Any]],
    *,
    max_chunks: int = 12,
    message_budget_bytes: int = 4096,
) -> list[dict[str, Any]]:
    """Trim log chunks for result submission while preserving recent evidence."""

    bounded: list[dict[str, Any]] = []
    trimmed = list(chunks[-max_chunks:])
    skipped_count = max(0, len(chunks) - len(trimmed))
    if skipped_count:
        bounded.append(
            {
                "stream": "system",
                "message": f"{skipped_count} earlier log chunk(s) omitted from bounded payload",
                "metadata": {"source": "worker_runtime", "truncated": True},
            }
        )
    for chunk in trimmed:
        bounded_chunk = dict(chunk)
        message, was_truncated = _truncate_text_to_bytes(
            str(chunk.get("message") or ""),
            message_budget_bytes,
        )
        bounded_chunk["message"] = message
        if was_truncated:
            metadata = dict(chunk.get("metadata") or {})
            metadata["truncated"] = True
            bounded_chunk["metadata"] = metadata
        bounded.append(bounded_chunk)
    return bounded


class WorkerRuntimeError(RuntimeError):
    """Structured runtime error for worker lifecycle failures."""


@dataclass
class WorkerApiError(Exception):
    """HTTP/API error with status and parsed response payload."""

    message: str
    status_code: int
    payload: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message


@dataclass
class GuardrailError(Exception):
    """Fail-closed guardrail violation with stable machine-readable code."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        super().__init__(self.message)

    def __str__(self) -> str:
        return self.message

    def to_payload(self) -> dict[str, Any]:
        return {
            "code": self.code,
            "message": self.message,
            "details": self.details,
        }


@dataclass
class WorkerSession:
    """Authenticated worker session from bootstrap/register flow."""

    session_id: str
    token: str
    signing_secret: str
    expires_at: str | None = None

    def is_expired(self, *, grace_seconds: int = 60) -> bool:
        raw = (self.expires_at or "").strip()
        if not raw:
            return False
        try:
            expiry = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=UTC)
        return expiry <= (_utc_now() + timedelta(seconds=grace_seconds))

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "token": self.token,
            "signing_secret": self.signing_secret,
            "expires_at": self.expires_at,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> WorkerSession:
        session_id = str(payload.get("session_id") or "").strip()
        token = str(payload.get("token") or "").strip()
        signing_secret = str(payload.get("signing_secret") or "").strip()
        expires_at_raw = payload.get("expires_at")
        expires_at = str(expires_at_raw).strip() if expires_at_raw is not None else None
        if not session_id or not token or not signing_secret:
            raise ValueError("worker session payload is missing credentials")
        return cls(
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            expires_at=expires_at,
        )


@dataclass(frozen=True)
class WorkerGuardrails:
    """Local enforcement limits for worker job execution."""

    allowed_repo_roots: tuple[Path, ...]
    denied_repo_roots: tuple[Path, ...]
    allowed_actions: tuple[str, ...]
    allowed_commands: tuple[str, ...]
    max_runtime_seconds: int
    max_memory_mb: int
    max_artifact_bytes: int

    @classmethod
    def from_config(cls, config: AgentConfig) -> WorkerGuardrails:
        roots = tuple(
            Path(path).expanduser().resolve() for path in config.worker_allowed_repo_roots
        )
        denied_roots = tuple(
            Path(path).expanduser().resolve() for path in config.worker_denied_repo_roots
        )
        actions = tuple(item.strip() for item in config.worker_allowed_actions if item.strip())
        commands = tuple(item.strip() for item in config.worker_allowed_commands if item.strip())
        return cls(
            allowed_repo_roots=roots,
            denied_repo_roots=denied_roots,
            allowed_actions=actions,
            allowed_commands=commands,
            max_runtime_seconds=max(5, int(config.worker_max_runtime_seconds)),
            max_memory_mb=max(32, int(config.worker_max_memory_mb)),
            max_artifact_bytes=max(1024, int(config.worker_max_artifact_bytes)),
        )


@dataclass(frozen=True)
class WorkerJob:
    """Canonical claimed job shape for local runner dispatch."""

    job_id: str
    execution_mode: str
    run_id: str | None
    shard_id: str | None
    execution_target: str
    action: str
    runner: str
    payload: dict[str, Any]
    required_capabilities: tuple[str, ...]
    artifact_contract: dict[str, Any]
    delegation_access: dict[str, Any] | None = None

    @classmethod
    def from_claim_payload(cls, payload: dict[str, Any]) -> WorkerJob:
        job_id = str(payload.get("job_id") or payload.get("id") or "").strip()
        if not job_id:
            raise ValueError("claimed job is missing job_id")
        execution_mode = str(payload.get("execution_mode") or "live").strip().lower() or "live"
        action = str(payload.get("action") or payload.get("action_name") or "worker.noop").strip()
        runner = str(payload.get("runner") or payload.get("kind") or "noop").strip().lower()
        raw_payload = payload.get("payload")
        if raw_payload is None:
            raw_payload = {}
        if not isinstance(raw_payload, dict):
            raise ValueError("job payload must be an object")
        delegation_access_raw = raw_payload.get("worker_delegation_access")
        delegation_access = (
            dict(delegation_access_raw) if isinstance(delegation_access_raw, dict) else None
        )
        required_capabilities = tuple(_parse_string_list(payload.get("required_capabilities")))
        return cls(
            job_id=job_id,
            execution_mode=execution_mode,
            run_id=str(payload.get("run_id") or "").strip() or None,
            shard_id=str(payload.get("shard_id") or "").strip() or None,
            execution_target=str(payload.get("execution_target") or "").strip() or "unknown",
            action=action,
            runner=runner,
            payload=raw_payload,
            required_capabilities=required_capabilities,
            artifact_contract=dict(payload.get("artifact_contract") or {}),
            delegation_access=delegation_access,
        )


@dataclass(frozen=True)
class WorkerRunResult:
    """Runner output passed back to control plane result endpoint."""

    status: str
    output: dict[str, Any] | None = None
    error: dict[str, Any] | None = None
    events: list[dict[str, Any]] = field(default_factory=list)
    log_chunks: list[dict[str, Any]] = field(default_factory=list)
    resource_samples: list[dict[str, Any]] = field(default_factory=list)
    debug_bundle: dict[str, Any] | None = None
    cleanup_receipt: dict[str, Any] | None = None
    container_receipts: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class WorkerCycleOutcome:
    """One claim/execute cycle result."""

    claimed_job: bool
    job_id: str | None
    status: str
    poll_after_seconds: int


class WorkerRunner(Protocol):
    """Runner contract for pluggable execution engines."""

    name: str

    async def run(self, job: WorkerJob, guardrails: WorkerGuardrails) -> WorkerRunResult:
        """Execute one claimed job and return status/output payload."""


class NoopRunner:
    """Deterministic no-op runner for smoke/integration lanes."""

    name = "noop_runner"

    async def run(self, job: WorkerJob, guardrails: WorkerGuardrails) -> WorkerRunResult:
        _ = guardrails
        await asyncio.sleep(0)
        return WorkerRunResult(
            status="succeeded",
            output={
                "runner": self.name,
                "job_id": job.job_id,
                "action": job.action,
                "acknowledged": True,
                "payload": job.payload,
            },
        )


class CodexRunner:
    """Guarded local command runner for coding jobs."""

    name = "codex_runner"

    async def run(self, job: WorkerJob, guardrails: WorkerGuardrails) -> WorkerRunResult:
        payload = job.payload
        command_raw = payload.get("command")
        repo_root_raw = str(payload.get("repo_root") or "").strip()
        env_raw = payload.get("env")

        if not isinstance(command_raw, list) or not command_raw:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_COMMAND_MISSING",
                message="Job command must be a non-empty array",
                details={"job_id": job.job_id},
            )
        command = [str(item).strip() for item in command_raw if str(item).strip()]
        if not command:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_COMMAND_EMPTY",
                message="Job command cannot be empty",
                details={"job_id": job.job_id},
            )

        command_name = command[0]
        if guardrails.allowed_commands and command_name not in guardrails.allowed_commands:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_COMMAND_NOT_ALLOWED",
                message=f"Command '{command_name}' is not allowlisted",
                details={"command": command_name, "job_id": job.job_id},
            )

        if not repo_root_raw:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_REPO_ROOT_MISSING",
                message="Job is missing repo_root",
                details={"job_id": job.job_id},
            )
        repo_root = Path(repo_root_raw).expanduser().resolve()
        if not repo_root.exists() or not repo_root.is_dir():
            raise GuardrailError(
                code="WORKER_GUARDRAIL_REPO_ROOT_INVALID",
                message=f"repo_root does not exist: {repo_root}",
                details={"repo_root": str(repo_root), "job_id": job.job_id},
            )

        if not guardrails.allowed_repo_roots:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_REPO_ALLOWLIST_EMPTY",
                message="No allowlisted repo roots configured",
                details={"job_id": job.job_id},
            )
        if not any(
            repo_root == allowed_root or allowed_root in repo_root.parents
            for allowed_root in guardrails.allowed_repo_roots
        ):
            raise GuardrailError(
                code="WORKER_GUARDRAIL_REPO_NOT_ALLOWED",
                message=f"repo_root is outside allowlisted roots: {repo_root}",
                details={"repo_root": str(repo_root), "job_id": job.job_id},
            )
        if any(
            repo_root == denied_root or denied_root in repo_root.parents
            for denied_root in guardrails.denied_repo_roots
        ):
            raise GuardrailError(
                code="WORKER_GUARDRAIL_REPO_DENIED",
                message=f"repo_root is explicitly denied: {repo_root}",
                details={"repo_root": str(repo_root), "job_id": job.job_id},
            )

        env: dict[str, str] = {}
        if isinstance(env_raw, dict):
            for key, value in env_raw.items():
                env_key = str(key).strip()
                if not env_key:
                    continue
                env[env_key] = str(value)

        process_env = dict(os.environ)
        process_env.update(env)

        preexec_fn: Any | None = None
        if guardrails.max_memory_mb > 0 and os.name != "nt":
            try:
                import resource
            except ImportError as exc:  # pragma: no cover - depends on platform
                raise GuardrailError(
                    code="WORKER_GUARDRAIL_MEMORY_CAP_UNSUPPORTED",
                    message="Memory cap support is unavailable",
                    details={"job_id": job.job_id},
                ) from exc

            memory_bytes = int(guardrails.max_memory_mb) * 1024 * 1024

            def _apply_limit() -> None:
                resource.setrlimit(resource.RLIMIT_AS, (memory_bytes, memory_bytes))

            preexec_fn = _apply_limit

        started_at = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *command,
                cwd=str(repo_root),
                env=process_env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                preexec_fn=preexec_fn,
            )
        except FileNotFoundError as exc:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_COMMAND_NOT_FOUND",
                message=f"Executable not found: {command_name}",
                details={"command": command_name, "job_id": job.job_id},
            ) from exc

        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                process.communicate(),
                timeout=max(1, guardrails.max_runtime_seconds),
            )
        except TimeoutError as exc:
            process.kill()
            _ = await process.communicate()
            raise GuardrailError(
                code="WORKER_GUARDRAIL_RUNTIME_EXCEEDED",
                message="Job runtime exceeded max_runtime_seconds",
                details={"job_id": job.job_id, "limit_seconds": guardrails.max_runtime_seconds},
            ) from exc

        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        stdout = stdout_raw.decode("utf-8", errors="replace")
        stderr = stderr_raw.decode("utf-8", errors="replace")

        stdout, stderr, capture_metadata = _bounded_stream_output(
            stdout=stdout,
            stderr=stderr,
            max_artifact_bytes=guardrails.max_artifact_bytes,
        )

        output = {
            "runner": self.name,
            "job_id": job.job_id,
            "command": command,
            "repo_root": str(repo_root),
            "exit_code": int(process.returncode or 0),
            "elapsed_ms": elapsed_ms,
            "stdout": stdout,
            "stderr": stderr,
            **capture_metadata,
        }
        if int(process.returncode or 0) == 0:
            return WorkerRunResult(status="succeeded", output=output)
        return WorkerRunResult(
            status="failed",
            output=output,
            error={
                "code": "WORKER_RUNNER_EXIT_NON_ZERO",
                "message": f"Command exited with status {process.returncode}",
            },
        )


class DockerRunner:
    """Docker-only runner for isolated Windows CI shards."""

    name = "docker_runner"

    def __init__(
        self,
        *,
        execution_backend: str = "native_windows_docker",
        docker_backend: str = "native_windows_docker",
        wsl_distribution: str = "Ubuntu",
        cleanup_enabled: bool = True,
        cleanup_low_disk_free_bytes: int = 21_474_836_480,
        cleanup_target_free_bytes: int = 42_949_672_960,
        cleanup_artifact_retention_hours: int = 24,
        cleanup_log_retention_days: int = 7,
        worker_log_dir: str | None = None,
    ) -> None:
        self._execution_backend = str(execution_backend or "native_windows_docker").strip()
        self._docker_backend = str(docker_backend or self._execution_backend).strip()
        self._wsl_distribution = str(wsl_distribution or "Ubuntu").strip() or "Ubuntu"
        self._cleanup_enabled = bool(cleanup_enabled)
        self._cleanup_low_disk_free_bytes = max(0, int(cleanup_low_disk_free_bytes))
        self._cleanup_target_free_bytes = max(
            self._cleanup_low_disk_free_bytes,
            int(cleanup_target_free_bytes),
        )
        self._cleanup_artifact_retention_hours = max(1, int(cleanup_artifact_retention_hours))
        self._cleanup_log_retention_days = max(1, int(cleanup_log_retention_days))
        self._worker_log_dir = (
            Path(worker_log_dir).expanduser().resolve()
            if worker_log_dir
            else None
        )

    async def run(self, job: WorkerJob, guardrails: WorkerGuardrails) -> WorkerRunResult:
        payload = job.payload
        container_spec = dict(payload.get("container_spec") or {})
        if not container_spec:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_CONTAINER_SPEC_MISSING",
                message="Docker runner requires container_spec",
                details={"job_id": job.job_id},
            )

        workspace_root_raw = str(
            payload.get("workspace_root") or payload.get("repo_root") or ""
        ).strip()
        if not workspace_root_raw:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_REPO_ROOT_MISSING",
                message="Docker job is missing workspace_root",
                details={"job_id": job.job_id},
            )
        workspace_root = Path(workspace_root_raw).expanduser().resolve()
        if not workspace_root.exists() or not workspace_root.is_dir():
            raise GuardrailError(
                code="WORKER_GUARDRAIL_REPO_ROOT_INVALID",
                message=f"workspace_root does not exist: {workspace_root}",
                details={"workspace_root": str(workspace_root), "job_id": job.job_id},
            )
        if not guardrails.allowed_repo_roots or not any(
            workspace_root == allowed_root or allowed_root in workspace_root.parents
            for allowed_root in guardrails.allowed_repo_roots
        ):
            raise GuardrailError(
                code="WORKER_GUARDRAIL_REPO_NOT_ALLOWED",
                message=f"workspace_root is outside allowlisted roots: {workspace_root}",
                details={"workspace_root": str(workspace_root), "job_id": job.job_id},
            )
        if any(
            workspace_root == denied_root or denied_root in workspace_root.parents
            for denied_root in guardrails.denied_repo_roots
        ):
            raise GuardrailError(
                code="WORKER_GUARDRAIL_REPO_DENIED",
                message=f"workspace_root is explicitly denied: {workspace_root}",
                details={"workspace_root": str(workspace_root), "job_id": job.job_id},
            )
        if guardrails.allowed_commands and "docker" not in guardrails.allowed_commands:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_COMMAND_NOT_ALLOWED",
                message="docker is not allowlisted for this worker",
                details={"job_id": job.job_id},
            )

        image = str(container_spec.get("image") or "").strip()
        if not image:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_CONTAINER_IMAGE_MISSING",
                message="container_spec.image is required",
                details={"job_id": job.job_id},
            )
        command_raw = container_spec.get("command") or payload.get("command") or []
        if not isinstance(command_raw, list) or not command_raw:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_COMMAND_MISSING",
                message="Docker job command must be a non-empty array",
                details={"job_id": job.job_id},
            )
        command = [str(item).strip() for item in command_raw if str(item).strip()]
        if not command:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_COMMAND_EMPTY",
                message="Docker job command cannot be empty",
                details={"job_id": job.job_id},
            )

        mounts_raw = container_spec.get("mounts")
        mounts: list[dict[str, Any]] = []
        if isinstance(mounts_raw, list):
            mounts = [dict(item) for item in mounts_raw if isinstance(item, dict)]
        if not mounts:
            mounts = [{"source": str(workspace_root), "target": "/workspace", "read_only": False}]

        docker_inner_command = ["docker", "run", "--rm"]
        cleanup_labels = dict(payload.get("cleanup_labels") or {})
        for key, value in cleanup_labels.items():
            label_key = str(key).strip()
            if not label_key:
                continue
            docker_inner_command.extend(["--label", f"{label_key}={value}"])

        workspace_mount_source = str(workspace_root)
        resolved_mounts: list[dict[str, Any]] = []
        workspace_mount_target = "/workspace"
        workspace_mount_source_resolved = (
            _translate_windows_path_to_wsl(workspace_mount_source)
            if self._docker_backend == "wsl_docker"
            else workspace_mount_source
        )
        for mount in mounts:
            source = str(mount.get("source") or "").strip()
            target = str(mount.get("target") or "").strip()
            if not source or not target:
                continue
            if source == str(workspace_root):
                workspace_mount_source = source
                workspace_mount_target = target
                break
        else:
            for mount in mounts:
                source = str(mount.get("source") or "").strip()
                target = str(mount.get("target") or "").strip()
                if target == workspace_mount_target and source:
                    workspace_mount_source = source
                    break
        for mount in mounts:
            source = str(mount.get("source") or "").strip()
            target = str(mount.get("target") or "").strip()
            if not source or not target:
                continue
            resolved_source = source
            if self._docker_backend == "wsl_docker":
                resolved_source = _translate_windows_path_to_wsl(source)
            if source == workspace_mount_source and target == workspace_mount_target:
                workspace_mount_source_resolved = resolved_source
            resolved_mounts.append(
                {
                    "source": resolved_source,
                    "target": target,
                    "read_only": bool(mount.get("read_only", False)),
                }
            )
            docker_inner_command.extend(["-v", f"{resolved_source}:{target}"])

        if (
            self._docker_backend == "wsl_docker"
            and workspace_mount_source_resolved
            and workspace_mount_source_resolved != workspace_mount_target
            and not any(
                mount["source"] == workspace_mount_source_resolved
                and mount["target"] == workspace_mount_source_resolved
                for mount in resolved_mounts
            )
        ):
            resolved_mounts.append(
                {
                    "source": workspace_mount_source_resolved,
                    "target": workspace_mount_source_resolved,
                    "read_only": False,
                }
            )
            docker_inner_command.extend(
                ["-v", f"{workspace_mount_source_resolved}:{workspace_mount_source_resolved}"]
            )

        runtime_env_source = ""
        runtime_env_target = ""
        if self._docker_backend == "wsl_docker":
            runtime_env_source = os.environ.get(
                "ZETHERION_WORKER_RUNTIME_ENV_FILE",
                r"C:\ZetherionAI\.env",
            ).strip()
            if runtime_env_source and Path(runtime_env_source).exists():
                runtime_env_target = _translate_windows_path_to_wsl(runtime_env_source)
                if not any(
                    mount["source"] == runtime_env_target and mount["target"] == runtime_env_target
                    for mount in resolved_mounts
                ):
                    resolved_mounts.append(
                        {
                            "source": runtime_env_target,
                            "target": runtime_env_target,
                            "read_only": True,
                        }
                    )
                    docker_inner_command.extend(
                        ["-v", f"{runtime_env_target}:{runtime_env_target}:ro"]
                    )

        docker_socket_path = "/var/run/docker.sock"
        docker_socket_available = False
        if self._docker_backend == "wsl_docker":
            docker_socket_available = _wsl_socket_path_available(self._wsl_distribution)
        else:
            docker_socket_available = Path(docker_socket_path).exists()
        if docker_socket_available:
            resolved_mounts.append(
                {
                    "source": docker_socket_path,
                    "target": docker_socket_path,
                    "read_only": False,
                }
            )
            docker_inner_command.extend(
                ["-v", f"{docker_socket_path}:{docker_socket_path}"]
            )
        if self._docker_backend == "wsl_docker":
            docker_inner_command.extend(
                ["--add-host", "host.docker.internal:host-gateway"]
            )

        env_payload = {}
        if isinstance(container_spec.get("env"), dict):
            env_payload.update(dict(container_spec.get("env") or {}))
        if isinstance(payload.get("env"), dict):
            env_payload.update(dict(payload.get("env") or {}))
        if self._docker_backend == "wsl_docker":
            env_payload.setdefault(
                "ZETHERION_HOST_WORKSPACE_ROOT",
                workspace_mount_source_resolved,
            )
            env_payload.setdefault("ZETHERION_WORKSPACE_MOUNT_TARGET", workspace_mount_target)
            env_payload.setdefault(
                "E2E_STACK_STORAGE_ROOT",
                f"{workspace_mount_source_resolved}/.artifacts/ci-e2e-stacks",
            )
            env_payload.setdefault("E2E_RUNTIME_HOST", "host.docker.internal")
            if runtime_env_target:
                env_payload.setdefault("ZETHERION_ENV_FILE", runtime_env_target)
        for key, value in env_payload.items():
            env_key = str(key).strip()
            if not env_key:
                continue
            docker_inner_command.extend(["-e", f"{env_key}={value}"])

        workdir = str(container_spec.get("workdir") or "/workspace").strip() or "/workspace"
        image_prepare_actions = await self._ensure_container_image(
            image=image,
            workspace_root=workspace_root,
        )
        docker_inner_command.extend(["-w", workdir, image, *command])
        docker_command = docker_inner_command
        process_cwd = str(workspace_root)
        if self._execution_backend == "wsl_docker":
            docker_command = ["wsl.exe", "-d", self._wsl_distribution, "--", *docker_inner_command]
            process_cwd = None
        started_at = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *docker_command,
                cwd=process_cwd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError as exc:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_COMMAND_NOT_FOUND",
                message="docker is not installed on the worker",
                details={"job_id": job.job_id},
            ) from exc

        try:
            stdout_raw, stderr_raw = await asyncio.wait_for(
                process.communicate(),
                timeout=max(1, guardrails.max_runtime_seconds),
            )
        except TimeoutError as exc:
            process.kill()
            _ = await process.communicate()
            raise GuardrailError(
                code="WORKER_GUARDRAIL_RUNTIME_EXCEEDED",
                message="Docker job runtime exceeded max_runtime_seconds",
                details={"job_id": job.job_id, "limit_seconds": guardrails.max_runtime_seconds},
            ) from exc

        elapsed_ms = int((time.monotonic() - started_at) * 1000)
        stdout = stdout_raw.decode("utf-8", errors="replace")
        stderr = stderr_raw.decode("utf-8", errors="replace")
        stdout, stderr, capture_metadata = _bounded_stream_output(
            stdout=stdout,
            stderr=stderr,
            max_artifact_bytes=guardrails.max_artifact_bytes,
        )

        disk = shutil.disk_usage(str(workspace_root))
        cleanup_receipt = await self._cleanup_after_run(
            job_id=job.job_id,
            workspace_root=workspace_root,
            free_bytes_before=int(disk.free),
            compose_project=str(payload.get("compose_project") or ""),
            cleanup_labels=dict(payload.get("cleanup_labels") or {}),
        )
        disk_after_cleanup = shutil.disk_usage(str(workspace_root))
        artifact_receipts, artifact_receipt_paths = _load_workspace_json_artifacts(
            workspace_root
        )
        resource_sample = {
            "memory_mb": 0.0,
            "disk_used_bytes": max(0, int(disk_after_cleanup.total - disk_after_cleanup.free)),
            "disk_free_bytes": int(disk_after_cleanup.free),
            "container_count": 1,
            "elapsed_ms": elapsed_ms,
        }
        output = {
            "runner": self.name,
            "job_id": job.job_id,
            "command": command,
            "docker_command": docker_command,
            "docker_inner_command": docker_inner_command,
            "repo_root": str(workspace_root),
            "execution_target": job.execution_target,
            "execution_backend": self._execution_backend,
            "docker_backend": self._docker_backend,
            "wsl_distribution": (
                self._wsl_distribution if self._docker_backend == "wsl_docker" else ""
            ),
            "image_prepare_actions": image_prepare_actions,
            "resolved_mounts": resolved_mounts,
            "exit_code": int(process.returncode or 0),
            "elapsed_ms": elapsed_ms,
            "disk_free_bytes_before_cleanup": int(disk.free),
            "disk_free_bytes_after_cleanup": int(disk_after_cleanup.free),
            "stdout": stdout,
            "stderr": stderr,
            **artifact_receipts,
            **capture_metadata,
        }
        result = WorkerRunResult(
            status="succeeded" if int(process.returncode or 0) == 0 else "failed",
            output=output,
            error=(
                None
                if int(process.returncode or 0) == 0
                else {
                    "code": "WORKER_RUNNER_EXIT_NON_ZERO",
                    "message": f"Docker command exited with status {process.returncode}",
                }
            ),
            events=[
                {
                    "event_type": "docker.run.completed",
                    "level": "info" if int(process.returncode or 0) == 0 else "error",
                    "payload": {
                        "image": image,
                        "elapsed_ms": elapsed_ms,
                        "exit_code": int(process.returncode or 0),
                    },
                }
            ],
            log_chunks=[
                {"stream": "stdout", "message": stdout, "metadata": {"runner": self.name}}
                for _ in [0]
                if stdout
            ]
            + [
                {"stream": "stderr", "message": stderr, "metadata": {"runner": self.name}}
                for _ in [0]
                if stderr
            ],
            resource_samples=[resource_sample],
            debug_bundle={
                "reproduce_command": docker_command,
                "docker_inner_command": docker_inner_command,
                "resolved_mounts": resolved_mounts,
                "execution_backend": self._execution_backend,
                "docker_backend": self._docker_backend,
                "wsl_distribution": self._wsl_distribution,
                "cleanup_labels": cleanup_labels,
                "image_prepare_actions": image_prepare_actions,
                "artifact_contract": job.artifact_contract,
                "cleanup_receipt_path": cleanup_receipt.get("path"),
                "artifact_receipt_paths": artifact_receipt_paths,
            },
            cleanup_receipt=cleanup_receipt,
            container_receipts=[
                {
                    "image": image,
                    "project": str(payload.get("compose_project") or ""),
                    "execution_backend": self._execution_backend,
                    "docker_backend": self._docker_backend,
                    "wsl_distribution": self._wsl_distribution,
                }
            ],
        )
        return result

    async def _ensure_container_image(
        self,
        *,
        image: str,
        workspace_root: Path,
    ) -> list[dict[str, Any]]:
        if image != "zetherion-ci:latest":
            return []

        expected_context_hash = _compute_tool_image_context_hash(workspace_root)
        inspect_command = ["docker", "image", "inspect", image]
        inspect_output, inspect_exit_code = await self._run_backend_command(inspect_command)
        actions = [
            {
                "action": "image_inspect",
                "image": image,
                "command": inspect_command,
                "success": inspect_exit_code == 0,
                "exit_code": int(inspect_exit_code),
                "output": inspect_output.strip(),
            }
        ]
        if inspect_exit_code == 0:
            label_command = [
                "docker",
                "image",
                "inspect",
                "--format",
                "{{ index .Config.Labels \"zetherion.tool_context_hash\" }}",
                image,
            ]
            label_output, label_exit_code = await self._run_backend_command(label_command)
            current_context_hash = label_output.strip()
            label_matches = (
                bool(expected_context_hash)
                and current_context_hash == expected_context_hash
            )
            actions.append(
                {
                    "action": "image_label_inspect",
                    "image": image,
                    "command": label_command,
                    "success": label_exit_code == 0,
                    "exit_code": int(label_exit_code),
                    "output": current_context_hash,
                    "expected_context_hash": expected_context_hash,
                    "label_matches": label_matches,
                }
            )
            if label_exit_code == 0 and label_matches:
                return actions

        dockerfile = workspace_root / "Dockerfile.dev-tools"
        if image == "zetherion-ci:latest" and dockerfile.exists():
            build_context = str(workspace_root)
            dockerfile_path = str(dockerfile)
            if self._docker_backend == "wsl_docker":
                build_context = _translate_windows_path_to_wsl(build_context)
                dockerfile_path = _translate_windows_path_to_wsl(dockerfile_path)
            build_command = [
                "docker",
                "build",
                "--label",
                f"zetherion.tool_context_hash={expected_context_hash}",
                "-f",
                dockerfile_path,
                "-t",
                image,
                build_context,
            ]
            build_output, build_exit_code = await self._run_backend_command(build_command)
            actions.append(
                {
                    "action": "image_build",
                    "image": image,
                    "command": build_command,
                    "success": build_exit_code == 0,
                    "exit_code": int(build_exit_code),
                    "output": build_output.strip(),
                }
            )
            if build_exit_code == 0:
                return actions
            raise GuardrailError(
                code="WORKER_GUARDRAIL_CONTAINER_IMAGE_BUILD_FAILED",
                message=f"Failed to build worker image `{image}`",
                details={
                    "image": image,
                    "workspace_root": str(workspace_root),
                    "dockerfile": str(dockerfile),
                    "expected_context_hash": expected_context_hash,
                    "output": build_output.strip(),
                },
            )

        raise GuardrailError(
            code="WORKER_GUARDRAIL_CONTAINER_IMAGE_UNAVAILABLE",
            message=f"Worker image `{image}` is unavailable on the executor",
            details={
                "image": image,
                "workspace_root": str(workspace_root),
                "inspect_output": inspect_output.strip(),
            },
        )

    async def _cleanup_after_run(
        self,
        *,
        job_id: str,
        workspace_root: Path,
        free_bytes_before: int,
        compose_project: str = "",
        cleanup_labels: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        receipt_dir = workspace_root / ".artifacts" / "cleanup"
        receipt_path = receipt_dir / f"{_sanitize_for_filename(job_id)}.json"
        cleanup_labels = {
            str(key).strip(): str(value).strip()
            for key, value in dict(cleanup_labels or {}).items()
            if str(key).strip() and str(value).strip()
        }
        receipt: dict[str, Any] = {
            "status": "skipped",
            "path": str(receipt_path),
            "cleanup_enabled": self._cleanup_enabled,
            "compose_project": compose_project,
            "cleanup_labels": cleanup_labels,
            "disk_free_bytes_before": int(free_bytes_before),
            "disk_free_bytes_after": int(free_bytes_before),
            "low_disk_free_bytes": int(self._cleanup_low_disk_free_bytes),
            "target_free_bytes": int(self._cleanup_target_free_bytes),
            "workspace_deleted_paths": [],
            "worker_logs_pruned": [],
            "docker_actions": [],
            "warnings": [],
        }
        if not self._cleanup_enabled:
            self._write_cleanup_receipt(receipt_path, receipt)
            return receipt

        warnings: list[str] = []
        deleted_paths = self._cleanup_workspace_artifacts(
            workspace_root=workspace_root,
            warnings=warnings,
        )
        pruned_logs = self._cleanup_worker_logs(warnings=warnings)
        docker_actions = await self._run_docker_cleanup(
            aggressive=free_bytes_before < self._cleanup_low_disk_free_bytes,
            compose_project=compose_project,
            cleanup_labels=cleanup_labels,
        )
        disk_after = shutil.disk_usage(str(workspace_root))
        status = "cleaned"
        if any(not bool(action.get("success", False)) for action in docker_actions):
            status = "cleanup_degraded"
        if warnings:
            status = "cleanup_degraded"
        if int(disk_after.free) < self._cleanup_low_disk_free_bytes:
            status = "cleanup_degraded"
            warnings.append("disk_headroom_below_low_watermark_after_cleanup")
        receipt.update(
            {
                "status": status,
                "disk_free_bytes_after": int(disk_after.free),
                "workspace_deleted_paths": deleted_paths,
                "worker_logs_pruned": pruned_logs,
                "docker_actions": docker_actions,
                "warnings": warnings,
            }
        )
        self._write_cleanup_receipt(receipt_path, receipt)
        return receipt

    def _cleanup_workspace_artifacts(
        self,
        *,
        workspace_root: Path,
        warnings: list[str],
    ) -> list[str]:
        deleted_paths: list[str] = []
        for pattern in _WORKSPACE_EPHEMERAL_DIR_GLOBS:
            for candidate in workspace_root.glob(pattern):
                if not candidate.exists():
                    continue
                try:
                    if candidate.is_dir():
                        shutil.rmtree(candidate)
                    else:
                        candidate.unlink(missing_ok=True)
                    deleted_paths.append(str(candidate))
                except OSError as exc:
                    warnings.append(f"workspace_cleanup_failed:{candidate}:{exc}")

        for pattern in _WORKSPACE_EPHEMERAL_FILE_GLOBS:
            for candidate in workspace_root.glob(pattern):
                if not candidate.exists():
                    continue
                try:
                    if candidate.is_dir():
                        shutil.rmtree(candidate)
                    else:
                        candidate.unlink(missing_ok=True)
                    deleted_paths.append(str(candidate))
                except OSError as exc:
                    warnings.append(f"workspace_cleanup_failed:{candidate}:{exc}")

        artifacts_dir = workspace_root / ".artifacts"
        if artifacts_dir.is_dir():
            cutoff = _utc_now() - timedelta(hours=self._cleanup_artifact_retention_hours)
            for candidate in artifacts_dir.iterdir():
                if candidate.name in _PRESERVED_ARTIFACT_FILES:
                    continue
                try:
                    stat = candidate.stat()
                except OSError as exc:
                    warnings.append(f"artifact_stat_failed:{candidate}:{exc}")
                    continue
                is_old_enough = datetime.fromtimestamp(stat.st_mtime, tz=UTC) <= cutoff
                if candidate.is_dir() or is_old_enough:
                    try:
                        if candidate.is_dir():
                            shutil.rmtree(candidate)
                        else:
                            candidate.unlink(missing_ok=True)
                        deleted_paths.append(str(candidate))
                    except OSError as exc:
                        warnings.append(f"artifact_cleanup_failed:{candidate}:{exc}")

        return deleted_paths

    def _cleanup_worker_logs(self, *, warnings: list[str]) -> list[str]:
        if self._worker_log_dir is None or not self._worker_log_dir.exists():
            return []
        cutoff = _utc_now() - timedelta(days=self._cleanup_log_retention_days)
        pruned: list[str] = []
        for candidate in self._worker_log_dir.rglob("*"):
            if not candidate.is_file():
                continue
            try:
                stat = candidate.stat()
            except OSError as exc:
                warnings.append(f"worker_log_stat_failed:{candidate}:{exc}")
                continue
            if datetime.fromtimestamp(stat.st_mtime, tz=UTC) > cutoff:
                continue
            try:
                candidate.unlink(missing_ok=True)
                pruned.append(str(candidate))
            except OSError as exc:
                warnings.append(f"worker_log_cleanup_failed:{candidate}:{exc}")
        return pruned

    async def _run_docker_cleanup(
        self,
        *,
        aggressive: bool,
        compose_project: str = "",
        cleanup_labels: dict[str, str] | None = None,
    ) -> list[dict[str, Any]]:
        cleanup_labels = dict(cleanup_labels or {})
        results: list[dict[str, Any]] = []
        if compose_project:
            results.extend(await self._remove_compose_project_resources(compose_project))
        if cleanup_labels:
            results.extend(await self._remove_labeled_resources(cleanup_labels))

        commands: list[tuple[str, list[str]]] = [
            ("container_prune", ["docker", "container", "prune", "-f"]),
            ("network_prune", ["docker", "network", "prune", "-f"]),
            ("image_prune_dangling", ["docker", "image", "prune", "-f"]),
            (
                "builder_prune_standard",
                ["docker", "builder", "prune", "-f", "--filter", "until=24h"],
            ),
        ]
        if aggressive:
            commands.extend(
                [
                    (
                        "image_prune_unused",
                        ["docker", "image", "prune", "-af", "--filter", "until=168h"],
                    ),
                    ("builder_prune_all", ["docker", "builder", "prune", "-af"]),
                    ("volume_prune_unused", ["docker", "volume", "prune", "-f"]),
                ]
            )

        for action_name, command in commands:
            output, exit_code = await self._run_backend_command(command)
            results.append(
                {
                    "action": action_name,
                    "command": command,
                    "success": exit_code == 0,
                    "exit_code": int(exit_code),
                    "output": output.strip(),
                }
            )
        return results

    async def _remove_compose_project_resources(
        self,
        compose_project: str,
    ) -> list[dict[str, Any]]:
        return await self._remove_filtered_docker_resources(
            resource_kind="compose_project",
            action_prefix="compose_project",
            filters=[f"label=com.docker.compose.project={compose_project}"],
        )

    async def _remove_labeled_resources(
        self,
        cleanup_labels: dict[str, str],
    ) -> list[dict[str, Any]]:
        filters = [f"label={key}={value}" for key, value in sorted(cleanup_labels.items())]
        return await self._remove_filtered_docker_resources(
            resource_kind="cleanup_labels",
            action_prefix="labeled",
            filters=filters,
        )

    async def _remove_filtered_docker_resources(
        self,
        *,
        resource_kind: str,
        action_prefix: str,
        filters: list[str],
    ) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        resource_specs = (
            (
                "containers",
                ["docker", "ps", "-aq"],
                ["docker", "rm", "-f"],
            ),
            (
                "networks",
                ["docker", "network", "ls", "-q"],
                ["docker", "network", "rm"],
            ),
            (
                "volumes",
                ["docker", "volume", "ls", "-q"],
                ["docker", "volume", "rm", "-f"],
            ),
        )
        filter_args = [arg for value in filters for arg in ("--filter", value)]
        for suffix, list_command, remove_command in resource_specs:
            query_command = [*list_command, *filter_args]
            output, exit_code = await self._run_backend_command(query_command)
            query_action = f"{action_prefix}_{suffix}_query"
            results.append(
                {
                    "action": query_action,
                    "resource_kind": resource_kind,
                    "filters": filters,
                    "command": query_command,
                    "success": exit_code == 0,
                    "exit_code": int(exit_code),
                    "output": output.strip(),
                }
            )
            if exit_code != 0:
                continue
            resource_ids = [line.strip() for line in output.splitlines() if line.strip()]
            if not resource_ids:
                continue
            final_command = [*remove_command, *resource_ids]
            remove_output, remove_exit_code = await self._run_backend_command(final_command)
            results.append(
                {
                    "action": f"{action_prefix}_{suffix}_remove",
                    "resource_kind": resource_kind,
                    "filters": filters,
                    "command": final_command,
                    "targets": resource_ids,
                    "success": remove_exit_code == 0,
                    "exit_code": int(remove_exit_code),
                    "output": remove_output.strip(),
                }
            )
        return results

    async def _run_backend_command(self, command: list[str]) -> tuple[str, int]:
        args = list(command)
        if self._docker_backend == "wsl_docker":
            args = ["wsl.exe", "-d", self._wsl_distribution, "--", *command]
        process = await asyncio.create_subprocess_exec(
            *args,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_raw, stderr_raw = await process.communicate()
        output = stdout_raw.decode("utf-8", errors="replace")
        error = stderr_raw.decode("utf-8", errors="replace")
        text = "\n".join(part for part in (output.strip(), error.strip()) if part).strip()
        return text, int(process.returncode or 0)

    @staticmethod
    def _write_cleanup_receipt(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n",
            encoding="utf-8",
        )


class TamperEvidentJobLog:
    """Append-only hash-chained execution log for one worker job."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._last_hash = self._load_last_hash()

    def _load_last_hash(self) -> str:
        if not self._path.exists():
            return "GENESIS"
        last_hash = "GENESIS"
        for line in self._path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError:
                continue
            candidate = str(payload.get("entry_hash") or "").strip()
            if candidate:
                last_hash = candidate
        return last_hash

    def append(self, event_type: str, payload: dict[str, Any]) -> None:
        record = {
            "timestamp": _utc_now_iso(),
            "event_type": event_type,
            "previous_hash": self._last_hash,
            "payload": payload,
        }
        digest = hashlib.sha256(_canonical_json(record).encode("utf-8")).hexdigest()
        record["entry_hash"] = digest
        with self._path.open("a", encoding="utf-8") as handle:
            handle.write(_canonical_json(record) + "\n")
        self._last_hash = digest


class WorkerApiClient:
    """Signed worker bridge API client for control-plane calls."""

    def __init__(
        self,
        *,
        base_url: str,
        scope_id: str,
        node_id: str,
        timeout_seconds: int = 30,
        scope_field_name: str = "tenant_id",
        extra_headers: dict[str, str] | None = None,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._scope_id = scope_id
        self._node_id = node_id
        self._scope_field_name = scope_field_name
        self._extra_headers = dict(extra_headers or {})
        self._client = httpx.AsyncClient(timeout=float(max(5, timeout_seconds)))

    async def close(self) -> None:
        await self._client.aclose()

    def _identity_payload(self) -> dict[str, str]:
        return {self._scope_field_name: self._scope_id}

    async def bootstrap(
        self,
        *,
        bootstrap_secret: str,
        node_name: str | None,
        capabilities: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> WorkerSession:
        payload: dict[str, Any] = {
            **self._identity_payload(),
            "node_id": self._node_id,
            "capabilities": capabilities,
        }
        if node_name:
            payload["node_name"] = node_name
        if metadata:
            payload["metadata"] = metadata
        response = await self._client.post(
            f"{self._base_url}/bootstrap",
            headers={
                "X-Worker-Bootstrap-Secret": bootstrap_secret,
                **self._extra_headers,
            },
            json=payload,
        )
        data = self._decode_response(response)
        session_payload = data.get("session")
        if not isinstance(session_payload, dict):
            raise WorkerRuntimeError("worker bootstrap response missing session payload")
        return WorkerSession.from_dict(session_payload)

    async def register(
        self,
        *,
        session: WorkerSession,
        node_name: str | None,
        capabilities: list[str],
        metadata: dict[str, Any] | None = None,
        rotate_credentials: bool = True,
    ) -> WorkerSession:
        payload: dict[str, Any] = {
            **self._identity_payload(),
            "node_id": self._node_id,
            "capabilities": capabilities,
            "rotate_credentials": bool(rotate_credentials),
        }
        if node_name:
            payload["node_name"] = node_name
        if metadata:
            payload["metadata"] = metadata
        data = await self._signed_post("/nodes/register", session=session, payload=payload)
        session_payload = data.get("session")
        if not isinstance(session_payload, dict):
            raise WorkerRuntimeError("worker register response missing session payload")
        merged = dict(session.to_dict())
        merged["session_id"] = str(session_payload.get("session_id") or session.session_id)
        if "token" in session_payload:
            merged["token"] = str(session_payload.get("token") or "")
        if "signing_secret" in session_payload:
            merged["signing_secret"] = str(session_payload.get("signing_secret") or "")
        if "expires_at" in session_payload:
            merged["expires_at"] = str(session_payload.get("expires_at") or "")
        return WorkerSession.from_dict(merged)

    async def heartbeat(
        self,
        *,
        session: WorkerSession,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload: dict[str, Any] = {
            **self._identity_payload(),
            "node_id": self._node_id,
            "health_status": "healthy",
        }
        if metadata:
            payload["metadata"] = metadata
        return await self._signed_post(
            f"/nodes/{self._node_id}/heartbeat",
            session=session,
            payload=payload,
        )

    async def claim_job(
        self,
        *,
        session: WorkerSession,
        required_capabilities: list[str],
        poll_after_seconds: int,
    ) -> dict[str, Any]:
        payload = {
            **self._identity_payload(),
            "required_capabilities": required_capabilities,
            "poll_after_seconds": max(5, int(poll_after_seconds)),
        }
        return await self._signed_post(
            f"/nodes/{self._node_id}/jobs/claim",
            session=session,
            payload=payload,
        )

    async def submit_result(
        self,
        *,
        session: WorkerSession,
        job_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return await self._signed_post(
            f"/nodes/{self._node_id}/jobs/{job_id}/result",
            session=session,
            payload=payload,
        )

    async def _signed_post(
        self,
        path: str,
        *,
        session: WorkerSession,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        raw_body = json.dumps(payload, separators=(",", ":"))
        timestamp = str(int(time.time()))
        nonce = secrets.token_hex(16)
        canonical = (
            f"{self._scope_id}.{self._node_id}.{session.session_id}.{timestamp}.{nonce}.{raw_body}"
        )
        signature = hmac.new(
            session.signing_secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        response = await self._client.post(
            f"{self._base_url}{path}",
            headers={
                "Authorization": f"Bearer {session.token}",
                "X-Worker-Session-Id": session.session_id,
                "X-Worker-Timestamp": timestamp,
                "X-Worker-Nonce": nonce,
                "X-Worker-Signature": signature,
                "Content-Type": "application/json",
                **self._extra_headers,
            },
            content=raw_body.encode("utf-8"),
        )
        return self._decode_response(response)

    @staticmethod
    def _decode_response(response: httpx.Response) -> dict[str, Any]:
        payload: dict[str, Any] = {}
        try:
            parsed = response.json()
            if isinstance(parsed, dict):
                payload = parsed
        except ValueError:
            payload = {}
        if response.status_code >= 400:
            message = str(payload.get("error") or f"Worker API error ({response.status_code})")
            raise WorkerApiError(
                message=message,
                status_code=int(response.status_code),
                payload=payload,
            )
        return payload


class WorkerRuntime:
    """Persistent sub-worker execution loop for laptop-side jobs."""

    def __init__(
        self,
        config: AgentConfig,
        *,
        store: PolicyStore | None = None,
        api_client: WorkerApiClient | None = None,
    ) -> None:
        self._config = config
        self._store = store or PolicyStore(config.database_path)
        self._guardrails = WorkerGuardrails.from_config(config)
        self._session: WorkerSession | None = None
        docker_runner = DockerRunner(
            execution_backend=config.worker_execution_backend,
            docker_backend=config.worker_docker_backend,
            wsl_distribution=config.worker_wsl_distribution,
            cleanup_enabled=config.worker_cleanup_enabled,
            cleanup_low_disk_free_bytes=config.worker_cleanup_low_disk_free_bytes,
            cleanup_target_free_bytes=config.worker_cleanup_target_free_bytes,
            cleanup_artifact_retention_hours=config.worker_cleanup_artifact_retention_hours,
            cleanup_log_retention_days=config.worker_cleanup_log_retention_days,
            worker_log_dir=config.worker_log_dir,
        )
        self._runners: dict[str, WorkerRunner] = {
            "noop": NoopRunner(),
            "noop_runner": NoopRunner(),
            "codex": CodexRunner(),
            "codex_runner": CodexRunner(),
            "command": CodexRunner(),
            "docker": docker_runner,
            "docker_runner": docker_runner,
        }
        self._default_runner = str(config.worker_runner or "noop").strip().lower() or "noop"
        self._logs_dir = Path(config.worker_log_dir).expanduser()
        self._logs_dir.mkdir(parents=True, exist_ok=True)
        self._poll_after_seconds = max(5, int(config.worker_poll_after_seconds))
        base_url = str(config.worker_base_url).strip()
        if not base_url:
            base_url = "http://127.0.0.1:8000/worker/v1"
        self._scope_id = str(config.worker_scope_id or config.worker_tenant_id).strip()
        if not self._scope_id:
            raise WorkerRuntimeError(
                "worker_scope_id or worker_tenant_id is required for worker mode"
            )
        node_id = str(config.worker_node_id).strip()
        if not node_id:
            raise WorkerRuntimeError("worker_node_id is required for worker mode")
        scope_field_name = (
            "scope_id"
            if str(config.worker_control_plane or "tenant").strip().lower() == "owner_ci"
            else "tenant_id"
        )
        self._direct_api = api_client or WorkerApiClient(
            base_url=base_url,
            scope_id=self._scope_id,
            node_id=node_id,
            scope_field_name=scope_field_name,
        )
        relay_base_url = str(config.worker_relay_base_url).strip()
        relay_headers = {}
        relay_secret = str(config.worker_relay_secret).strip()
        if relay_secret:
            relay_headers["X-CI-Relay-Secret"] = relay_secret
        self._relay_api = (
            WorkerApiClient(
                base_url=relay_base_url,
                scope_id=self._scope_id,
                node_id=node_id,
                scope_field_name=scope_field_name,
                extra_headers=relay_headers,
            )
            if relay_base_url
            else None
        )

    async def close(self) -> None:
        await self._direct_api.close()
        if self._relay_api is not None:
            await self._relay_api.close()
        self._store.close()

    async def run_forever(self) -> None:
        await self._ensure_session()
        heartbeat = asyncio.create_task(self._heartbeat_loop())
        try:
            while True:
                outcome = await self.run_once()
                await asyncio.sleep(max(1, outcome.poll_after_seconds))
        finally:
            heartbeat.cancel()
            await asyncio.gather(heartbeat, return_exceptions=True)

    async def run_once(self) -> WorkerCycleOutcome:
        session = await self._ensure_session()
        await self._flush_pending_results(session)
        await self._recover_inflight(session)
        session = await self._heartbeat_once(session)
        claim_response = await self._claim_once(session)
        job_payload = claim_response.get("job")
        poll_after_seconds = max(
            5, int(claim_response.get("poll_after_seconds", self._poll_after_seconds))
        )
        if not isinstance(job_payload, dict):
            return WorkerCycleOutcome(
                claimed_job=False,
                job_id=None,
                status="idle",
                poll_after_seconds=poll_after_seconds,
            )
        job = WorkerJob.from_claim_payload(job_payload)
        result = await self._execute_job(job)
        await self._submit_result(job=job, result=result)
        return WorkerCycleOutcome(
            claimed_job=True,
            job_id=job.job_id,
            status=result.status,
            poll_after_seconds=1,
        )

    async def _heartbeat_loop(self) -> None:
        interval = max(10, int(self._config.worker_heartbeat_interval_seconds))
        while True:
            session = await self._ensure_session()
            await self._flush_pending_results(session)
            _ = await self._heartbeat_once(session)
            await asyncio.sleep(interval)

    async def _ensure_session(self) -> WorkerSession:
        if self._session is not None and not self._session.is_expired():
            return self._session

        loaded = self._load_session()
        if loaded is not None and not loaded.is_expired():
            self._session = loaded
            return loaded
        self._clear_session()

        bootstrap_secret = str(self._config.worker_bootstrap_secret).strip()
        if not bootstrap_secret:
            raise WorkerRuntimeError(
                "worker_bootstrap_secret is required when no cached worker session exists"
            )
        capabilities = list(self._config.worker_capabilities)
        registered = await self._bootstrap_and_register(
            bootstrap_secret=bootstrap_secret,
            capabilities=capabilities,
        )
        self._save_session(registered)
        self._session = registered
        return registered

    async def _recover_inflight(self, session: WorkerSession) -> None:
        raw = self._store.get_meta(INFLIGHT_META_KEY)
        if raw is None:
            return
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            self._store.set_meta(INFLIGHT_META_KEY, "")
            return
        if not isinstance(payload, dict):
            self._store.set_meta(INFLIGHT_META_KEY, "")
            return
        job_id = str(payload.get("job_id") or "").strip()
        if not job_id:
            self._store.set_meta(INFLIGHT_META_KEY, "")
            return
        required_capabilities = _parse_string_list(payload.get("required_capabilities"))
        logger = self._job_logger(job_id)
        logger.append(
            "recovery_started",
            {
                "reason": "process_restart",
                "job_id": job_id,
            },
        )
        recovery_payload = {
            **self._scope_payload(),
            "status": "failed",
            "required_capabilities": required_capabilities,
            "error": {
                "code": RESTART_RECOVERY_ERROR_CODE,
                "message": "Worker restarted while job was in-flight",
            },
            "output": {
                "recovered_at": _utc_now_iso(),
            },
        }
        _ = await self._deliver_result_payload(
            session=session,
            job_id=job_id,
            payload=recovery_payload,
            logger=logger,
        )
        self._store.set_meta(INFLIGHT_META_KEY, "")

    async def _heartbeat_once(self, session: WorkerSession) -> WorkerSession:
        try:
            await self._direct_api.heartbeat(
                session=session,
                metadata={"runner": self._default_runner},
            )
            return session
        except WorkerApiError as exc:
            if _is_stale_worker_session_error(exc):
                self._clear_session()
                refreshed = await self._ensure_session()
                await self._direct_api.heartbeat(
                    session=refreshed,
                    metadata={"runner": self._default_runner},
                )
                return refreshed
            if self._relay_api is None:
                raise
        except httpx.HTTPError:
            if self._relay_api is None:
                raise
        await self._relay_api.heartbeat(
            session=session,
            metadata={"runner": self._default_runner, "transport": "relay"},
        )
        return session

    async def _claim_once(self, session: WorkerSession) -> dict[str, Any]:
        try:
            return await self._direct_api.claim_job(
                session=session,
                required_capabilities=list(self._config.worker_claim_required_capabilities),
                poll_after_seconds=self._poll_after_seconds,
            )
        except WorkerApiError as exc:
            if _is_stale_worker_session_error(exc):
                self._clear_session()
                refreshed = await self._ensure_session()
                return await self._direct_api.claim_job(
                    session=refreshed,
                    required_capabilities=list(self._config.worker_claim_required_capabilities),
                    poll_after_seconds=self._poll_after_seconds,
                )
            if self._relay_api is None:
                raise
        except httpx.HTTPError:
            if self._relay_api is None:
                raise
        return await self._relay_api.claim_job(
            session=session,
            required_capabilities=list(self._config.worker_claim_required_capabilities),
            poll_after_seconds=self._poll_after_seconds,
        )

    async def _execute_job(self, job: WorkerJob) -> WorkerRunResult:
        logger = self._job_logger(job.job_id)
        logger.append(
            "job_claimed",
            {
                "job_id": job.job_id,
                "execution_mode": job.execution_mode,
                "execution_target": job.execution_target,
                "action": job.action,
                "runner": job.runner,
                "required_capabilities": list(job.required_capabilities),
            },
        )
        self._store.set_meta(
            INFLIGHT_META_KEY,
            _canonical_json(
                {
                    "job_id": job.job_id,
                    "required_capabilities": list(job.required_capabilities),
                    "claimed_at": _utc_now_iso(),
                }
            ),
        )

        try:
            if job.execution_mode == "test":
                logger.append(
                    "job_simulated",
                    {
                        "job_id": job.job_id,
                        "execution_mode": job.execution_mode,
                        "execution_target": job.execution_target,
                    },
                )
                return WorkerRunResult(
                    status="succeeded",
                    output={
                        "runner": "sandbox_simulated_worker",
                        "job_id": job.job_id,
                        "execution_mode": job.execution_mode,
                        "execution_target": job.execution_target,
                        "action": job.action,
                        "simulated": True,
                        "message": (
                            "Test-mode worker job was acknowledged locally without "
                            "running any live command or external mutation."
                        ),
                    },
                )
            self._enforce_action(job.action)
            self._enforce_worker_delegation_access(job)
            runner = self._resolve_runner(job.runner)
            logger.append(
                "job_started",
                {
                    "job_id": job.job_id,
                    "runner": runner.name,
                    "execution_target": job.execution_target,
                },
            )
            result = await runner.run(job, self._guardrails)
        except GuardrailError as exc:
            logger.append(
                "job_guardrail_failed",
                {
                    "job_id": job.job_id,
                    "code": exc.code,
                    "message": exc.message,
                },
            )
            result = WorkerRunResult(status="failed", error=exc.to_payload())
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.append(
                "job_execution_failed",
                {
                    "job_id": job.job_id,
                    "error": str(exc),
                },
            )
            result = WorkerRunResult(
                status="failed",
                error={
                    "code": "WORKER_RUNNER_UNHANDLED_EXCEPTION",
                    "message": str(exc),
                },
            )

        logger.append(
            "job_finished",
            {
                "job_id": job.job_id,
                "status": result.status,
                "error_code": (result.error or {}).get("code"),
            },
        )
        return result

    async def _submit_result(self, *, job: WorkerJob, result: WorkerRunResult) -> None:
        session = await self._ensure_session()
        log_chunks = result.log_chunks or self._read_job_log_chunks(job.job_id)
        payload: dict[str, Any] = {
            **self._scope_payload(),
            "status": result.status,
            "required_capabilities": list(job.required_capabilities),
            "output": result.output,
            "error": result.error,
            "events": result.events,
            "log_chunks": log_chunks,
            "resource_samples": result.resource_samples,
            "debug_bundle": result.debug_bundle,
            "cleanup_receipt": result.cleanup_receipt,
            "container_receipts": result.container_receipts,
        }
        payload = self._bounded_result_payload(payload)
        logger = self._job_logger(job.job_id)
        try:
            _ = await self._deliver_result_payload(
                session=session,
                job_id=job.job_id,
                payload=payload,
                logger=logger,
            )
        finally:
            self._store.set_meta(INFLIGHT_META_KEY, "")

    def _bounded_result_payload(self, payload: dict[str, Any]) -> dict[str, Any]:
        encoded = json.dumps(payload, separators=(",", ":"), default=str).encode("utf-8")
        if len(encoded) <= self._guardrails.max_artifact_bytes:
            return payload
        max_bytes = self._guardrails.max_artifact_bytes
        output = dict(payload.get("output") or {})
        bounded_output: dict[str, Any] = {
            "runner": output.get("runner"),
            "job_id": output.get("job_id"),
            "exit_code": output.get("exit_code"),
            "elapsed_ms": output.get("elapsed_ms"),
            "artifacts_truncated": True,
            "payload_bytes": len(encoded),
            "max_artifact_bytes": max_bytes,
            "cleanup_receipt_path": ((payload.get("cleanup_receipt") or {}) or {}).get("path"),
        }
        if output.get("artifact_bytes") is not None:
            bounded_output["artifact_bytes"] = output.get("artifact_bytes")
        if output.get("stdout"):
            bounded_output["stdout"], _ = _truncate_text_to_bytes(
                str(output.get("stdout") or ""),
                max(512, max_bytes // 8),
            )
        if output.get("stderr"):
            bounded_output["stderr"], _ = _truncate_text_to_bytes(
                str(output.get("stderr") or ""),
                max(512, max_bytes // 8),
            )
        for receipt_key in (
            "ci_worker_connectivity_receipt",
            "coverage_gaps",
            "coverage_summary",
            "diagnostic_findings",
            "diagnostic_summary",
            "e2e_receipt",
            "local_readiness_receipt",
            "worker_certification_receipt",
            "workspace_readiness_receipt",
        ):
            if output.get(receipt_key) is not None:
                bounded_output[receipt_key] = output.get(receipt_key)

        bounded_payload = {
            **self._scope_payload(),
            "status": str(payload.get("status") or "failed"),
            "error": payload.get("error"),
            "output": bounded_output,
            "required_capabilities": payload.get("required_capabilities") or [],
            "events": list(payload.get("events") or [])[-5:],
            "log_chunks": _bounded_log_chunks(
                list(payload.get("log_chunks") or []),
                max_chunks=8,
                message_budget_bytes=max(1024, max_bytes // 8),
            ),
            "resource_samples": list(payload.get("resource_samples") or [])[-3:],
            "cleanup_receipt": payload.get("cleanup_receipt"),
            "container_receipts": payload.get("container_receipts") or [],
            "debug_bundle": payload.get("debug_bundle"),
        }
        bounded_encoded = json.dumps(
            bounded_payload, separators=(",", ":"), default=str
        ).encode("utf-8")
        if len(bounded_encoded) <= max_bytes:
            return bounded_payload

        return {
            **self._scope_payload(),
            "status": str(payload.get("status") or "failed"),
            "error": payload.get("error"),
            "output": bounded_output,
            "required_capabilities": payload.get("required_capabilities") or [],
        }

    def _resolve_runner(self, requested: str) -> WorkerRunner:
        key = requested.strip().lower() if requested else ""
        if not key:
            key = self._default_runner
        runner = self._runners.get(key)
        if runner is None:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_RUNNER_NOT_ALLOWED",
                message=f"Runner '{key}' is not available",
                details={"runner": key},
            )
        return runner

    def _enforce_action(self, action: str) -> None:
        allowed = set(self._guardrails.allowed_actions)
        if not allowed:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_ACTION_ALLOWLIST_EMPTY",
                message="No allowed actions configured",
                details={},
            )
        normalized = action.strip()
        if normalized not in allowed:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_ACTION_NOT_ALLOWED",
                message=f"Action '{normalized}' is not allowlisted",
                details={"action": normalized},
            )

    def _enforce_worker_delegation_access(self, job: WorkerJob) -> None:
        normalized_action = job.action.strip().lower()
        if normalized_action not in {
            "repo.patch",
            "repo.commit",
            "repo.pr.open",
            "codex.session.control",
        }:
            return
        access = job.delegation_access or {}
        if not access:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_DELEGATION_REQUIRED",
                message="Worker delegation access metadata is required for this action",
                details={"action": normalized_action, "job_id": job.job_id},
            )
        permission = str(access.get("permission") or "").strip().lower()
        resource_scope = str(access.get("resource_scope") or "").strip()
        if permission != normalized_action:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_DELEGATION_PERMISSION_MISMATCH",
                message="Delegation permission does not match the claimed action",
                details={
                    "action": normalized_action,
                    "permission": permission,
                    "job_id": job.job_id,
                },
            )
        if normalized_action in {"repo.patch", "repo.commit", "repo.pr.open"}:
            repo_root = str(
                job.payload.get("repo_root")
                or job.payload.get("workspace_root")
                or job.payload.get("workdir")
                or ""
            ).strip()
            expected_scope = f"repo:{repo_root}" if repo_root else ""
            if not repo_root or resource_scope not in {expected_scope, "repo:*"}:
                raise GuardrailError(
                    code="WORKER_GUARDRAIL_DELEGATION_SCOPE_MISMATCH",
                    message="Delegation scope does not match repo_root",
                    details={
                        "action": normalized_action,
                        "resource_scope": resource_scope,
                        "expected_scope": expected_scope,
                        "job_id": job.job_id,
                    },
                )
            return
        session_id = str(
            job.payload.get("session_id") or job.payload.get("codex_session_id") or ""
        ).strip()
        expected_scope = f"codex.session:{session_id}" if session_id else ""
        if not session_id or resource_scope not in {expected_scope, "codex.session:*"}:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_DELEGATION_SCOPE_MISMATCH",
                message="Delegation scope does not match codex session",
                details={
                    "action": normalized_action,
                    "resource_scope": resource_scope,
                    "expected_scope": expected_scope,
                    "job_id": job.job_id,
                },
            )

    def _job_logger(self, job_id: str) -> TamperEvidentJobLog:
        filename = f"{_sanitize_for_filename(job_id)}.jsonl"
        return TamperEvidentJobLog(self._logs_dir / filename)

    def _read_job_log_chunks(self, job_id: str) -> list[dict[str, Any]]:
        path = self._logs_dir / f"{_sanitize_for_filename(job_id)}.jsonl"
        if not path.exists():
            return []
        lines = path.read_text(encoding="utf-8").splitlines()[-50:]
        chunks: list[dict[str, Any]] = []
        for line in lines:
            if not line.strip():
                continue
            chunks.append({"stream": "system", "message": line, "metadata": {"source": "job_log"}})
        return chunks

    def _load_session(self) -> WorkerSession | None:
        raw = self._store.get_meta(SESSION_META_KEY)
        if raw is None:
            return None
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        try:
            session = WorkerSession.from_dict(payload)
        except ValueError:
            return None
        return session

    def _save_session(self, session: WorkerSession) -> None:
        self._store.set_meta(SESSION_META_KEY, _canonical_json(session.to_dict()))

    def _clear_session(self) -> None:
        self._session = None
        self._store.set_meta(SESSION_META_KEY, "")

    def _scope_payload(self) -> dict[str, str]:
        key = (
            "scope_id"
            if str(self._config.worker_control_plane or "tenant").strip().lower() == "owner_ci"
            else "tenant_id"
        )
        return {key: self._scope_id}

    async def _bootstrap_and_register(
        self,
        *,
        bootstrap_secret: str,
        capabilities: list[str],
    ) -> WorkerSession:
        errors: list[str] = []
        clients = [("direct", self._direct_api)]
        if self._relay_api is not None:
            clients.append(("relay", self._relay_api))
        for label, client in clients:
            try:
                session = await client.bootstrap(
                    bootstrap_secret=bootstrap_secret,
                    node_name=self._config.worker_node_name or None,
                    capabilities=capabilities,
                    metadata={"source": "dev-agent-worker", "transport": label},
                )
                return await client.register(
                    session=session,
                    node_name=self._config.worker_node_name or None,
                    capabilities=capabilities,
                    metadata={"source": "dev-agent-worker", "transport": label},
                    rotate_credentials=True,
                )
            except (WorkerApiError, httpx.HTTPError) as exc:
                errors.append(f"{label}: {exc}")
        raise WorkerRuntimeError(
            "Failed to bootstrap/register worker session: " + "; ".join(errors)
        )

    async def _deliver_result_payload(
        self,
        *,
        session: WorkerSession,
        job_id: str,
        payload: dict[str, Any],
        logger: TamperEvidentJobLog,
    ) -> bool:
        try:
            await self._direct_api.submit_result(session=session, job_id=job_id, payload=payload)
            logger.append(
                "result_submitted",
                {
                    "job_id": job_id,
                    "status": str(payload.get("status") or ""),
                    "transport": "direct",
                    "error_code": ((payload.get("error") or {}) or {}).get("code"),
                },
            )
            return True
        except WorkerApiError as exc:
            if _is_stale_worker_session_error(exc):
                self._clear_session()
                try:
                    refreshed = await self._ensure_session()
                    await self._direct_api.submit_result(
                        session=refreshed,
                        job_id=job_id,
                        payload=payload,
                    )
                    logger.append(
                        "result_submitted",
                        {
                            "job_id": job_id,
                            "status": str(payload.get("status") or ""),
                            "transport": "direct",
                            "error_code": ((payload.get("error") or {}) or {}).get("code"),
                            "session_recovered": True,
                        },
                    )
                    return True
                except (WorkerApiError, httpx.HTTPError) as retry_exc:
                    logger.append(
                        "result_submit_retry_failed",
                        {"job_id": job_id, "transport": "direct", "error": str(retry_exc)},
                    )
            logger.append(
                "result_submit_failed",
                {"job_id": job_id, "transport": "direct", "error": str(exc)},
            )
        except httpx.HTTPError as exc:
            logger.append(
                "result_submit_failed",
                {"job_id": job_id, "transport": "direct", "error": str(exc)},
            )

        if self._relay_api is not None:
            try:
                await self._relay_api.submit_result(session=session, job_id=job_id, payload=payload)
                logger.append(
                    "result_submitted",
                    {
                        "job_id": job_id,
                        "status": str(payload.get("status") or ""),
                        "transport": "relay",
                        "error_code": ((payload.get("error") or {}) or {}).get("code"),
                    },
                )
                return True
            except (WorkerApiError, httpx.HTTPError) as exc:
                logger.append(
                    "result_submit_failed",
                    {"job_id": job_id, "transport": "relay", "error": str(exc)},
                )

        self._store.enqueue_worker_result(job_id=job_id, payload=payload)
        logger.append(
            "result_spooled",
            {
                "job_id": job_id,
                "status": str(payload.get("status") or ""),
            },
        )
        return False

    async def _flush_pending_results(self, session: WorkerSession) -> None:
        retry_delay_seconds = max(30, self._poll_after_seconds)
        for row in self._store.list_pending_worker_results(limit=25):
            logger = self._job_logger(str(row["job_id"]))
            try:
                submitted = await self._deliver_result_payload(
                    session=session,
                    job_id=str(row["job_id"]),
                    payload=dict(row["payload"]),
                    logger=logger,
                )
                if submitted:
                    self._store.mark_worker_result_sent(int(row["id"]))
                else:
                    self._store.mark_worker_result_failed(
                        int(row["id"]),
                        "worker result remained offline",
                        _utc_now() + timedelta(seconds=retry_delay_seconds),
                    )
            except Exception as exc:  # pragma: no cover - defensive fallback
                self._store.mark_worker_result_failed(
                    int(row["id"]),
                    str(exc),
                    _utc_now() + timedelta(seconds=retry_delay_seconds),
                )
