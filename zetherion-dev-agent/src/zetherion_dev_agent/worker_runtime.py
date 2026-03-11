"""Worker-mode runtime for laptop sub-worker execution."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import secrets
import shutil
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


class WorkerRuntimeError(RuntimeError):
    """Structured runtime error for worker lifecycle failures."""


@dataclass(frozen=True)
class WorkerApiError(Exception):
    """HTTP/API error with status and parsed response payload."""

    message: str
    status_code: int
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class GuardrailError(Exception):
    """Fail-closed guardrail violation with stable machine-readable code."""

    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)

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

        # Ensure captured artifacts remain bounded before result submission.
        captured_size = len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))
        if captured_size > guardrails.max_artifact_bytes:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_ARTIFACT_TOO_LARGE",
                message="Command output exceeds artifact byte limit",
                details={
                    "job_id": job.job_id,
                    "artifact_bytes": captured_size,
                    "max_artifact_bytes": guardrails.max_artifact_bytes,
                },
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

        docker_command = ["docker", "run", "--rm"]
        cleanup_labels = dict(payload.get("cleanup_labels") or {})
        for key, value in cleanup_labels.items():
            label_key = str(key).strip()
            if not label_key:
                continue
            docker_command.extend(["--label", f"{label_key}={value}"])

        for mount in mounts:
            source = str(mount.get("source") or "").strip()
            target = str(mount.get("target") or "").strip()
            if not source or not target:
                continue
            docker_command.extend(["-v", f"{source}:{target}"])

        env_payload = {}
        if isinstance(container_spec.get("env"), dict):
            env_payload.update(dict(container_spec.get("env") or {}))
        if isinstance(payload.get("env"), dict):
            env_payload.update(dict(payload.get("env") or {}))
        for key, value in env_payload.items():
            env_key = str(key).strip()
            if not env_key:
                continue
            docker_command.extend(["-e", f"{env_key}={value}"])

        workdir = str(container_spec.get("workdir") or "/workspace").strip() or "/workspace"
        docker_command.extend(["-w", workdir, image, *command])
        started_at = time.monotonic()
        try:
            process = await asyncio.create_subprocess_exec(
                *docker_command,
                cwd=str(workspace_root),
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
        captured_size = len(stdout.encode("utf-8")) + len(stderr.encode("utf-8"))
        if captured_size > guardrails.max_artifact_bytes:
            raise GuardrailError(
                code="WORKER_GUARDRAIL_ARTIFACT_TOO_LARGE",
                message="Docker output exceeds artifact byte limit",
                details={
                    "job_id": job.job_id,
                    "artifact_bytes": captured_size,
                    "max_artifact_bytes": guardrails.max_artifact_bytes,
                },
            )

        disk = shutil.disk_usage(str(workspace_root))
        resource_sample = {
            "memory_mb": 0.0,
            "disk_used_bytes": max(0, int(disk.total - disk.free)),
            "disk_free_bytes": int(disk.free),
            "container_count": 1,
            "elapsed_ms": elapsed_ms,
        }
        output = {
            "runner": self.name,
            "job_id": job.job_id,
            "command": command,
            "docker_command": docker_command,
            "repo_root": str(workspace_root),
            "execution_target": job.execution_target,
            "exit_code": int(process.returncode or 0),
            "elapsed_ms": elapsed_ms,
            "stdout": stdout,
            "stderr": stderr,
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
                "cleanup_labels": cleanup_labels,
                "artifact_contract": job.artifact_contract,
            },
            cleanup_receipt={"status": "requested", "docker_only": True},
            container_receipts=[
                {
                    "image": image,
                    "project": str(payload.get("compose_project") or ""),
                }
            ],
        )
        return result


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
        self._runners: dict[str, WorkerRunner] = {
            "noop": NoopRunner(),
            "noop_runner": NoopRunner(),
            "codex": CodexRunner(),
            "codex_runner": CodexRunner(),
            "command": CodexRunner(),
            "docker": DockerRunner(),
            "docker_runner": DockerRunner(),
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
        await self._heartbeat_once(session)
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
            await self._heartbeat_once(session)
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

    async def _heartbeat_once(self, session: WorkerSession) -> None:
        try:
            await self._direct_api.heartbeat(
                session=session,
                metadata={"runner": self._default_runner},
            )
            return
        except WorkerApiError as exc:
            if exc.status_code in {401, 403}:
                self._clear_session()
                raise
            if self._relay_api is None:
                raise
        except httpx.HTTPError:
            if self._relay_api is None:
                raise
        await self._relay_api.heartbeat(
            session=session,
            metadata={"runner": self._default_runner, "transport": "relay"},
        )

    async def _claim_once(self, session: WorkerSession) -> dict[str, Any]:
        try:
            return await self._direct_api.claim_job(
                session=session,
                required_capabilities=list(self._config.worker_claim_required_capabilities),
                poll_after_seconds=self._poll_after_seconds,
            )
        except WorkerApiError as exc:
            if exc.status_code in {401, 403}:
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
        bounded = {
            **self._scope_payload(),
            "status": "failed",
            "error": {
                "code": "WORKER_GUARDRAIL_ARTIFACT_TOO_LARGE",
                "message": "Result payload exceeded worker artifact limit",
                "details": {
                    "payload_bytes": len(encoded),
                    "max_artifact_bytes": self._guardrails.max_artifact_bytes,
                },
            },
            "output": {
                "truncated": True,
            },
            "required_capabilities": payload.get("required_capabilities") or [],
        }
        fallback = json.dumps(bounded, separators=(",", ":"), default=str).encode("utf-8")
        if len(fallback) <= self._guardrails.max_artifact_bytes:
            return bounded
        return {
            **self._scope_payload(),
            "status": "failed",
            "error": {
                "code": "WORKER_GUARDRAIL_ARTIFACT_TOO_LARGE",
                "message": "Result payload exceeded worker artifact limit",
            },
            "output": {"truncated": True},
            "required_capabilities": [],
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
            if exc.status_code in {401, 403}:
                self._clear_session()
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
