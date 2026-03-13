# ruff: noqa: E402, I001
"""Tests for dev-agent worker runtime and guarded job execution."""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_AGENT_SRC = PROJECT_ROOT / "zetherion-dev-agent" / "src"
if str(DEV_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(DEV_AGENT_SRC))

import zetherion_dev_agent.worker_runtime as worker_runtime_module
from zetherion_dev_agent.config import AgentConfig
from zetherion_dev_agent.policy_store import PolicyStore
from zetherion_dev_agent.worker_runtime import (
    INFLIGHT_META_KEY,
    RESTART_RECOVERY_ERROR_CODE,
    SESSION_META_KEY,
    DockerRunner,
    WorkerCycleOutcome,
    WorkerApiError,
    WorkerGuardrails,
    WorkerJob,
    WorkerRuntime,
    WorkerSession,
    _translate_windows_path_to_wsl,
)


class _FakeWorkerBridge:
    def __init__(
        self,
        *,
        tenant_id: str,
        node_id: str,
        bootstrap_secret: str,
        jobs: list[dict[str, Any] | None],
        base_path: str = "/worker/v1",
        scope_field_name: str = "tenant_id",
        scope_id: str | None = None,
        required_headers: dict[str, str] | None = None,
        session_id: str = "session-1",
        bootstrap_token: str = "bootstrap-token",
        bootstrap_signing_secret: str = "bootstrap-signing",
        active_token: str = "active-token",
        active_signing_secret: str = "active-signing",
    ) -> None:
        self.tenant_id = tenant_id
        self.node_id = node_id
        self.bootstrap_secret = bootstrap_secret
        self.jobs = list(jobs)
        self.base_path = base_path.rstrip("/")
        self.scope_field_name = scope_field_name
        self.scope_id = scope_id or tenant_id
        self.required_headers = {
            key.lower(): value for key, value in dict(required_headers or {}).items()
        }
        self.session_id = session_id
        self.bootstrap_token = bootstrap_token
        self.bootstrap_signing_secret = bootstrap_signing_secret
        self.active_token = active_token
        self.active_signing_secret = active_signing_secret
        self.claim_count = 0
        self.heartbeat_count = 0
        self.result_payloads: list[dict[str, Any]] = []

    def create_app(self) -> web.Application:
        app = web.Application()
        app.router.add_post(f"{self.base_path}/bootstrap", self.handle_bootstrap)
        app.router.add_post(f"{self.base_path}/nodes/register", self.handle_register)
        app.router.add_post(
            f"{self.base_path}/nodes/{self.node_id}/heartbeat",
            self.handle_heartbeat,
        )
        app.router.add_post(
            f"{self.base_path}/nodes/{self.node_id}/jobs/claim",
            self.handle_claim,
        )
        app.router.add_post(
            f"{self.base_path}/nodes/{self.node_id}/jobs/{{job_id}}/result",
            self.handle_result,
        )
        return app

    async def handle_bootstrap(self, request: web.Request) -> web.Response:
        assert request.headers.get("X-Worker-Bootstrap-Secret") == self.bootstrap_secret
        self._assert_required_headers(request)
        payload = await request.json()
        assert payload[self.scope_field_name] == self.scope_id
        assert payload["node_id"] == self.node_id
        return web.json_response(
            {
                "ok": True,
                "session": {
                    "session_id": self.session_id,
                    "token": self.bootstrap_token,
                    "signing_secret": self.bootstrap_signing_secret,
                },
            },
            status=201,
        )

    async def handle_register(self, request: web.Request) -> web.Response:
        raw = await request.text()
        payload = json.loads(raw)
        assert payload[self.scope_field_name] == self.scope_id
        assert payload["node_id"] == self.node_id
        self._assert_required_headers(request)
        self._assert_signed(
            request=request,
            raw_body=raw,
            token=self.bootstrap_token,
            signing_secret=self.bootstrap_signing_secret,
        )
        return web.json_response(
            {
                "ok": True,
                "session": {
                    "session_id": self.session_id,
                    "token": self.active_token,
                    "signing_secret": self.active_signing_secret,
                },
            }
        )

    async def handle_heartbeat(self, request: web.Request) -> web.Response:
        raw = await request.text()
        self._assert_required_headers(request)
        self._assert_signed(
            request=request,
            raw_body=raw,
            token=self.active_token,
            signing_secret=self.active_signing_secret,
        )
        self.heartbeat_count += 1
        return web.json_response({"ok": True, "node": {"status": "active"}})

    async def handle_claim(self, request: web.Request) -> web.Response:
        raw = await request.text()
        payload = json.loads(raw)
        assert payload[self.scope_field_name] == self.scope_id
        self._assert_required_headers(request)
        self._assert_signed(
            request=request,
            raw_body=raw,
            token=self.active_token,
            signing_secret=self.active_signing_secret,
        )
        self.claim_count += 1
        job = self.jobs.pop(0) if self.jobs else None
        return web.json_response(
            {
                "ok": True,
                self.scope_field_name: self.scope_id,
                "node_id": self.node_id,
                "job": job,
                "poll_after_seconds": 5,
            }
        )

    async def handle_result(self, request: web.Request) -> web.Response:
        raw = await request.text()
        payload = json.loads(raw)
        self._assert_required_headers(request)
        self._assert_signed(
            request=request,
            raw_body=raw,
            token=self.active_token,
            signing_secret=self.active_signing_secret,
        )
        self.result_payloads.append(payload)
        return web.json_response({"ok": True, "accepted": True}, status=202)

    def _assert_required_headers(self, request: web.Request) -> None:
        for name, value in self.required_headers.items():
            assert request.headers.get(name) == value

    def _assert_signed(
        self,
        *,
        request: web.Request,
        raw_body: str,
        token: str,
        signing_secret: str,
    ) -> None:
        assert request.headers.get("Authorization") == f"Bearer {token}"
        session_id = request.headers.get("X-Worker-Session-Id", "")
        timestamp = request.headers.get("X-Worker-Timestamp", "")
        nonce = request.headers.get("X-Worker-Nonce", "")
        provided_signature = request.headers.get("X-Worker-Signature", "")
        assert session_id == self.session_id
        assert timestamp
        assert nonce
        canonical = f"{self.scope_id}.{self.node_id}.{session_id}.{timestamp}.{nonce}.{raw_body}"
        expected_signature = hmac.new(
            signing_secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert hmac.compare_digest(provided_signature, expected_signature)


def test_worker_api_error_allows_traceback_assignment() -> None:
    error = WorkerApiError(
        message="claim failed",
        status_code=400,
        payload={"error": "claim failed"},
    )

    error.__traceback__ = None

    assert str(error) == "claim failed"
    assert error.status_code == 400


def test_guardrail_error_allows_traceback_assignment() -> None:
    error = worker_runtime_module.GuardrailError(
        code="WORKER_GUARDRAIL_TEST",
        message="guardrail failed",
        details={"cause": "test"},
    )

    error.__traceback__ = None

    assert str(error) == "guardrail failed"
    assert error.code == "WORKER_GUARDRAIL_TEST"


def _base_config(*, tmp_path: Path, base_url: str, tenant_id: str, node_id: str) -> AgentConfig:
    return AgentConfig(
        repos=[],
        database_path=str(tmp_path / "worker.db"),
        worker_base_url=f"{base_url}/worker/v1",
        worker_tenant_id=tenant_id,
        worker_node_id=node_id,
        worker_node_name="laptop-worker",
        worker_bootstrap_secret="bootstrap-secret",
        worker_capabilities=["repo.patch", "repo.pr.open"],
        worker_claim_required_capabilities=["repo.patch"],
        worker_runner="noop",
        worker_allowed_actions=["worker.noop", "repo.patch"],
        worker_allowed_repo_roots=[str(tmp_path)],
        worker_allowed_commands=["git", "python", "python3", "pytest"],
        worker_max_runtime_seconds=30,
        worker_max_memory_mb=256,
        worker_max_artifact_bytes=32_768,
        worker_log_dir=str(tmp_path / "worker-logs"),
    )


class _FakeProcess:
    def __init__(self, *, stdout: bytes = b"", stderr: bytes = b"", returncode: int = 0) -> None:
        self._stdout = stdout
        self._stderr = stderr
        self.returncode = returncode

    async def communicate(self) -> tuple[bytes, bytes]:
        return self._stdout, self._stderr

    def kill(self) -> None:
        self.returncode = -9


class _SessionResetApiClient:
    def __init__(self) -> None:
        self.bootstrap_calls = 0
        self.register_calls = 0
        self.heartbeat_sessions: list[str] = []
        self.claim_sessions: list[str] = []

    async def close(self) -> None:
        return None

    async def bootstrap(
        self,
        *,
        bootstrap_secret: str,
        node_name: str | None,
        capabilities: list[str],
        metadata: dict[str, Any] | None = None,
    ) -> WorkerSession:
        self.bootstrap_calls += 1
        assert bootstrap_secret == "bootstrap-secret"
        return WorkerSession(
            session_id="fresh-session",
            token="fresh-token",
            signing_secret="fresh-signing",
            expires_at=None,
        )

    async def register(
        self,
        *,
        session: WorkerSession,
        node_name: str | None,
        capabilities: list[str],
        metadata: dict[str, Any] | None = None,
        rotate_credentials: bool = True,
    ) -> WorkerSession:
        self.register_calls += 1
        assert session.session_id == "fresh-session"
        return session

    async def heartbeat(
        self,
        *,
        session: WorkerSession,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        self.heartbeat_sessions.append(session.session_id)
        if session.session_id == "stale-session":
            raise WorkerApiError(
                message="Worker session not found",
                status_code=404,
                payload={"error": "Worker session not found"},
            )
        return {"ok": True}

    async def claim_job(
        self,
        *,
        session: WorkerSession,
        required_capabilities: list[str],
        poll_after_seconds: int,
    ) -> dict[str, Any]:
        self.claim_sessions.append(session.session_id)
        return {"job": None, "poll_after_seconds": poll_after_seconds}

    async def submit_result(
        self,
        *,
        session: WorkerSession,
        job_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        return {"ok": True}


def test_translate_windows_path_to_wsl_mount_path() -> None:
    assert _translate_windows_path_to_wsl(r"C:\ZetherionCI\workspaces\repo") == (
        "/mnt/c/ZetherionCI/workspaces/repo"
    )
    assert _translate_windows_path_to_wsl("D:\\") == "/mnt/d"
    assert _translate_windows_path_to_wsl("/already/linux") == "/already/linux"


@pytest.mark.asyncio
async def test_docker_runner_uses_wsl_executor_and_translated_mounts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_calls: list[dict[str, Any]] = []

    async def _fake_create_subprocess_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        captured_calls.append({"args": list(args), "kwargs": kwargs})
        return _FakeProcess(stdout=b"ok\n")

    monkeypatch.setattr(worker_runtime_module, "_wsl_socket_path_available", lambda *_: True)
    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)
    runtime_env_file = tmp_path / "live-runtime.env"
    runtime_env_file.write_text("OPENAI_API_KEY=test\n", encoding="utf-8")
    monkeypatch.setenv("ZETHERION_WORKER_RUNTIME_ENV_FILE", str(runtime_env_file))

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    runner = DockerRunner(
        execution_backend="wsl_docker",
        docker_backend="wsl_docker",
        wsl_distribution="Ubuntu",
    )
    guardrails = WorkerGuardrails(
        allowed_repo_roots=(tmp_path.resolve(),),
        denied_repo_roots=(),
        allowed_actions=("ci.test.run",),
        allowed_commands=("docker", "wsl"),
        max_runtime_seconds=30,
        max_memory_mb=256,
        max_artifact_bytes=32_768,
    )
    job = WorkerJob(
        job_id="job-docker-1",
        execution_mode="live",
        run_id=None,
        shard_id=None,
        execution_target="cgs",
        action="ci.test.run",
        runner="docker",
        required_capabilities=("ci.test.run",),
        artifact_contract={},
        delegation_access=None,
        payload={
            "workspace_root": str(workspace_root),
            "container_spec": {
                "image": "cgs-ci:latest",
                "command": ["yarn", "test"],
                "mounts": [
                    {
                        "source": r"C:\ZetherionCI\workspaces\catalyst-group-solutions",
                        "target": "/workspace",
                    }
                ],
            },
        },
    )

    result = await runner.run(job, guardrails)
    run_call = next(
        call
        for call in captured_calls
        if call["args"][:6] == ["wsl.exe", "-d", "Ubuntu", "--", "docker", "run"]
    )

    assert result.status == "succeeded"
    assert run_call["args"][:4] == ["wsl.exe", "-d", "Ubuntu", "--"]
    assert "/mnt/c/ZetherionCI/workspaces/catalyst-group-solutions:/workspace" in run_call["args"]
    assert "/var/run/docker.sock:/var/run/docker.sock" in run_call["args"]
    assert "--add-host" in run_call["args"]
    assert "host.docker.internal:host-gateway" in run_call["args"]
    assert "-e" in run_call["args"]
    assert (
        "ZETHERION_HOST_WORKSPACE_ROOT=/mnt/c/ZetherionCI/workspaces/catalyst-group-solutions"
        in run_call["args"]
    )
    assert "ZETHERION_WORKSPACE_MOUNT_TARGET=/workspace" in run_call["args"]
    assert (
        "E2E_STACK_STORAGE_ROOT=/mnt/c/ZetherionCI/workspaces/catalyst-group-solutions/.artifacts/ci-e2e-stacks"
        in run_call["args"]
    )
    assert "E2E_RUNTIME_HOST=host.docker.internal" in run_call["args"]
    assert (
        f"ZETHERION_ENV_FILE={runtime_env_file}"
        in run_call["args"]
    )
    assert f"{runtime_env_file}:{runtime_env_file}:ro" in run_call["args"]
    assert (
        "/mnt/c/ZetherionCI/workspaces/catalyst-group-solutions:/mnt/c/ZetherionCI/workspaces/catalyst-group-solutions"
        in run_call["args"]
    )
    assert run_call["kwargs"]["cwd"] is None
    assert result.output is not None
    assert result.output["execution_backend"] == "wsl_docker"
    assert result.output["docker_backend"] == "wsl_docker"
    resolved_mounts = result.output["resolved_mounts"]
    assert resolved_mounts[0]["source"] == "/mnt/c/ZetherionCI/workspaces/catalyst-group-solutions"
    assert any(
        mount["source"] == str(runtime_env_file)
        and mount["target"] == str(runtime_env_file)
        and mount["read_only"] is True
        for mount in resolved_mounts
    )
    assert any(
        mount["source"] == "/mnt/c/ZetherionCI/workspaces/catalyst-group-solutions"
        and mount["target"] == "/mnt/c/ZetherionCI/workspaces/catalyst-group-solutions"
        and mount["read_only"] is False
        for mount in resolved_mounts
    )
    assert any(mount["source"] == "/var/run/docker.sock" for mount in resolved_mounts)


@pytest.mark.asyncio
async def test_worker_noop_job_end_to_end(tmp_path: Path) -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    node_id = "worker-laptop-1"
    bridge = _FakeWorkerBridge(
        tenant_id=tenant_id,
        node_id=node_id,
        bootstrap_secret="bootstrap-secret",
        jobs=[
            {
                "job_id": "job-noop-1",
                "action": "worker.noop",
                "runner": "noop",
                "required_capabilities": ["repo.patch"],
                "payload": {"message": "hello"},
            }
        ],
    )

    server = TestServer(bridge.create_app())
    await server.start_server()
    try:
        config = _base_config(
            tmp_path=tmp_path,
            base_url=str(server.make_url("")).rstrip("/"),
            tenant_id=tenant_id,
            node_id=node_id,
        )
        runtime = WorkerRuntime(config)
        try:
            outcome = await runtime.run_once()
        finally:
            await runtime.close()
    finally:
        await server.close()

    assert outcome.claimed_job is True
    assert outcome.job_id == "job-noop-1"
    assert outcome.status == "succeeded"
    assert bridge.heartbeat_count >= 1
    assert bridge.claim_count == 1
    assert len(bridge.result_payloads) == 1
    assert bridge.result_payloads[0]["status"] == "succeeded"

    log_path = tmp_path / "worker-logs" / "job-noop-1.jsonl"
    assert log_path.exists()
    previous_hash = "GENESIS"
    for line in log_path.read_text(encoding="utf-8").splitlines():
        payload = json.loads(line)
        assert payload["previous_hash"] == previous_hash
        previous_hash = payload["entry_hash"]


@pytest.mark.asyncio
async def test_worker_guardrail_violation_returns_structured_error(tmp_path: Path) -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    node_id = "worker-laptop-1"
    bridge = _FakeWorkerBridge(
        tenant_id=tenant_id,
        node_id=node_id,
        bootstrap_secret="bootstrap-secret",
        jobs=[
            {
                "job_id": "job-command-1",
                "action": "repo.patch",
                "runner": "codex",
                "required_capabilities": ["repo.patch"],
                "payload": {
                    "repo_root": str(tmp_path / "outside"),
                    "command": ["git", "status"],
                    "worker_delegation_access": {
                        "grant_id": "grant-1",
                        "resource_scope": f"repo:{tmp_path / 'outside'}",
                        "permission": "repo.patch",
                    },
                },
            }
        ],
    )
    (tmp_path / "outside").mkdir(parents=True, exist_ok=True)

    server = TestServer(bridge.create_app())
    await server.start_server()
    try:
        config = _base_config(
            tmp_path=tmp_path,
            base_url=str(server.make_url("")).rstrip("/"),
            tenant_id=tenant_id,
            node_id=node_id,
        )
        config.worker_runner = "codex"
        config.worker_allowed_actions = ["repo.patch"]
        config.worker_allowed_repo_roots = [str(tmp_path / "allowed-only")]
        (tmp_path / "allowed-only").mkdir(parents=True, exist_ok=True)

        runtime = WorkerRuntime(config)
        try:
            outcome = await runtime.run_once()
        finally:
            await runtime.close()
    finally:
        await server.close()

    assert outcome.claimed_job is True
    assert outcome.job_id == "job-command-1"
    assert outcome.status == "failed"
    assert len(bridge.result_payloads) == 1
    assert bridge.result_payloads[0]["status"] == "failed"
    assert bridge.result_payloads[0]["error"]["code"] == "WORKER_GUARDRAIL_REPO_NOT_ALLOWED"


@pytest.mark.asyncio
async def test_worker_test_mode_job_is_simulated_without_running_codex(tmp_path: Path) -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    node_id = "worker-laptop-1"
    outside_repo = tmp_path / "outside"
    outside_repo.mkdir(parents=True, exist_ok=True)

    bridge = _FakeWorkerBridge(
        tenant_id=tenant_id,
        node_id=node_id,
        bootstrap_secret="bootstrap-secret",
        jobs=[
            {
                "job_id": "job-test-1",
                "execution_mode": "test",
                "action": "repo.patch",
                "runner": "codex",
                "required_capabilities": ["repo.patch"],
                "payload": {
                    "repo_root": str(outside_repo),
                    "command": ["git", "status"],
                },
            }
        ],
    )

    server = TestServer(bridge.create_app())
    await server.start_server()
    try:
        config = _base_config(
            tmp_path=tmp_path,
            base_url=str(server.make_url("")).rstrip("/"),
            tenant_id=tenant_id,
            node_id=node_id,
        )
        config.worker_runner = "codex"
        config.worker_allowed_actions = ["repo.patch"]
        config.worker_allowed_repo_roots = [str(tmp_path / "allowed-only")]
        (tmp_path / "allowed-only").mkdir(parents=True, exist_ok=True)

        runtime = WorkerRuntime(config)
        try:
            outcome = await runtime.run_once()
        finally:
            await runtime.close()
    finally:
        await server.close()

    assert outcome.claimed_job is True
    assert outcome.job_id == "job-test-1"
    assert outcome.status == "succeeded"
    assert bridge.result_payloads[0]["status"] == "succeeded"
    assert bridge.result_payloads[0]["output"]["simulated"] is True
    assert bridge.result_payloads[0]["output"]["execution_mode"] == "test"


@pytest.mark.asyncio
async def test_worker_delegation_guardrail_requires_matching_scope(tmp_path: Path) -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    node_id = "worker-laptop-1"
    allowed_repo = tmp_path / "allowed-repo"
    allowed_repo.mkdir(parents=True, exist_ok=True)

    bridge = _FakeWorkerBridge(
        tenant_id=tenant_id,
        node_id=node_id,
        bootstrap_secret="bootstrap-secret",
        jobs=[
            {
                "job_id": "job-command-2",
                "action": "repo.patch",
                "runner": "codex",
                "required_capabilities": ["repo.patch"],
                "payload": {
                    "repo_root": str(allowed_repo),
                    "command": ["git", "status"],
                },
            }
        ],
    )

    server = TestServer(bridge.create_app())
    await server.start_server()
    try:
        config = _base_config(
            tmp_path=tmp_path,
            base_url=str(server.make_url("")).rstrip("/"),
            tenant_id=tenant_id,
            node_id=node_id,
        )
        config.worker_runner = "codex"
        config.worker_allowed_actions = ["repo.patch"]
        config.worker_allowed_repo_roots = [str(allowed_repo)]

        runtime = WorkerRuntime(config)
        try:
            outcome = await runtime.run_once()
        finally:
            await runtime.close()
    finally:
        await server.close()

    assert outcome.claimed_job is True
    assert outcome.job_id == "job-command-2"
    assert outcome.status == "failed"
    assert bridge.result_payloads[0]["error"]["code"] == "WORKER_GUARDRAIL_DELEGATION_REQUIRED"


@pytest.mark.asyncio
async def test_worker_restart_recovery_submits_failure_and_resumes_claim(tmp_path: Path) -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    node_id = "worker-laptop-1"
    session_id = "session-reused"
    active_token = "active-token"
    active_signing = "active-signing"
    bridge = _FakeWorkerBridge(
        tenant_id=tenant_id,
        node_id=node_id,
        bootstrap_secret="bootstrap-secret",
        jobs=[None],
        session_id=session_id,
        active_token=active_token,
        active_signing_secret=active_signing,
    )

    db_path = tmp_path / "worker.db"
    store = PolicyStore(str(db_path))
    try:
        store.set_meta(
            SESSION_META_KEY,
            json.dumps(
                {
                    "session_id": session_id,
                    "token": active_token,
                    "signing_secret": active_signing,
                    "expires_at": None,
                }
            ),
        )
        store.set_meta(
            INFLIGHT_META_KEY,
            json.dumps(
                {
                    "job_id": "job-stale-1",
                    "required_capabilities": ["repo.patch"],
                    "claimed_at": "2026-03-05T00:00:00+00:00",
                }
            ),
        )
    finally:
        store.close()

    server = TestServer(bridge.create_app())
    await server.start_server()
    try:
        config = _base_config(
            tmp_path=tmp_path,
            base_url=str(server.make_url("")).rstrip("/"),
            tenant_id=tenant_id,
            node_id=node_id,
        )
        config.database_path = str(db_path)
        config.worker_bootstrap_secret = ""

        runtime = WorkerRuntime(config)
        try:
            outcome = await runtime.run_once()
        finally:
            await runtime.close()
    finally:
        await server.close()

    assert outcome.claimed_job is False
    assert outcome.status == "idle"
    assert bridge.claim_count == 1
    assert len(bridge.result_payloads) == 1
    recovery = bridge.result_payloads[0]
    assert recovery["status"] == "failed"
    assert recovery["error"]["code"] == RESTART_RECOVERY_ERROR_CODE

    verify_store = PolicyStore(str(db_path))
    try:
        assert verify_store.get_meta(INFLIGHT_META_KEY) in {"", None}
    finally:
        verify_store.close()


@pytest.mark.asyncio
async def test_worker_rebootstrap_on_stale_session_not_found(tmp_path: Path) -> None:
    scope_id = "owner:operator-1:repo:zetherion-ai"
    node_id = "worker-laptop-1"
    db_path = tmp_path / "worker.db"
    store = PolicyStore(str(db_path))
    try:
        store.set_meta(
            SESSION_META_KEY,
            json.dumps(
                {
                    "session_id": "stale-session",
                    "token": "stale-token",
                    "signing_secret": "stale-signing",
                    "expires_at": None,
                }
            ),
        )
    finally:
        store.close()

    config = _base_config(
        tmp_path=tmp_path,
        base_url="http://127.0.0.1:9/owner/ci/worker/v1",
        tenant_id=scope_id,
        node_id=node_id,
    )
    config.database_path = str(db_path)
    config.worker_control_plane = "owner_ci"
    config.worker_scope_id = scope_id
    config.worker_tenant_id = ""
    config.worker_bootstrap_secret = "bootstrap-secret"
    config.worker_capabilities = ["ci.test.run"]
    config.worker_claim_required_capabilities = ["ci.test.run"]
    config.worker_allowed_actions = ["worker.noop", "ci.test.run"]

    api_client = _SessionResetApiClient()
    runtime = WorkerRuntime(config, api_client=api_client)
    try:
        outcome = await runtime.run_once()
    finally:
        await runtime.close()

    assert outcome == WorkerCycleOutcome(
        claimed_job=False,
        job_id=None,
        status="idle",
        poll_after_seconds=15,
    )
    assert api_client.bootstrap_calls == 1
    assert api_client.register_calls == 1
    assert api_client.heartbeat_sessions == ["stale-session", "fresh-session"]
    assert api_client.claim_sessions == ["fresh-session"]

    verify_store = PolicyStore(str(db_path))
    try:
        payload = json.loads(str(verify_store.get_meta(SESSION_META_KEY) or "{}"))
    finally:
        verify_store.close()
    assert payload["session_id"] == "fresh-session"


@pytest.mark.asyncio
async def test_worker_owner_ci_relay_fallback_uses_scope_id_and_relay_secret(
    tmp_path: Path,
) -> None:
    scope_id = "owner:operator-1:repo:zetherion-ai"
    node_id = "worker-laptop-1"
    relay_secret = "relay-secret"
    bridge = _FakeWorkerBridge(
        tenant_id=scope_id,
        scope_id=scope_id,
        scope_field_name="scope_id",
        base_path="/owner/ci/worker/v1",
        node_id=node_id,
        bootstrap_secret="bootstrap-secret",
        required_headers={"x-ci-relay-secret": relay_secret},
        jobs=[
            {
                "job_id": "job-owner-ci-1",
                "action": "ci.test.run",
                "runner": "noop",
                "required_capabilities": ["ci.test.run"],
                "payload": {"message": "relay"},
            }
        ],
    )

    server = TestServer(bridge.create_app())
    await server.start_server()
    try:
        config = _base_config(
            tmp_path=tmp_path,
            base_url="http://127.0.0.1:9",
            tenant_id=scope_id,
            node_id=node_id,
        )
        config.worker_base_url = "http://127.0.0.1:9/owner/ci/worker/v1"
        config.worker_relay_base_url = f"{str(server.make_url('')).rstrip('/')}/owner/ci/worker/v1"
        config.worker_relay_secret = relay_secret
        config.worker_control_plane = "owner_ci"
        config.worker_scope_id = scope_id
        config.worker_tenant_id = ""
        config.worker_capabilities = ["ci.test.run"]
        config.worker_claim_required_capabilities = ["ci.test.run"]
        config.worker_allowed_actions = ["worker.noop", "ci.test.run"]

        runtime = WorkerRuntime(config)
        try:
            outcome = await runtime.run_once()
        finally:
            await runtime.close()
    finally:
        await server.close()

    assert outcome.claimed_job is True
    assert outcome.job_id == "job-owner-ci-1"
    assert outcome.status == "succeeded"
    assert bridge.claim_count == 1
    assert bridge.heartbeat_count >= 1
    assert bridge.result_payloads[0]["scope_id"] == scope_id
    assert bridge.result_payloads[0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_docker_runner_writes_real_cleanup_receipt_and_prunes_workspace_artifacts(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_commands: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        captured_commands.append([str(arg) for arg in args])
        command_text = " ".join(str(arg) for arg in args)
        if (
            "docker ps -aq" in command_text
            and "com.docker.compose.project=owner-ci-z-e2e-dm-sim" in command_text
        ):
            return _FakeProcess(stdout=b"container-1\n")
        if (
            "docker network ls -q" in command_text
            and "com.docker.compose.project=owner-ci-z-e2e-dm-sim" in command_text
        ):
            return _FakeProcess(stdout=b"network-1\n")
        if (
            "docker volume ls -q" in command_text
            and "com.docker.compose.project=owner-ci-z-e2e-dm-sim" in command_text
        ):
            return _FakeProcess(stdout=b"volume-1\n")
        if "docker ps -aq" in command_text and "zetherion.owner_ci=true" in command_text:
            return _FakeProcess(stdout=b"container-1\n")
        if "docker network ls -q" in command_text and "zetherion.owner_ci=true" in command_text:
            return _FakeProcess(stdout=b"")
        if "docker volume ls -q" in command_text and "zetherion.owner_ci=true" in command_text:
            return _FakeProcess(stdout=b"")
        return _FakeProcess(stdout=b"ok\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    workspace_root = tmp_path / "workspace"
    (workspace_root / "test-results").mkdir(parents=True)
    (workspace_root / "playwright-report").mkdir()
    artifacts_dir = workspace_root / ".artifacts"
    (artifacts_dir / "z-e2e-dm-sim").mkdir(parents=True)
    (artifacts_dir / "local-readiness-receipt.json").write_text("{}", encoding="utf-8")
    (workspace_root / ".coverage.integration").write_text("coverage", encoding="utf-8")

    worker_logs = tmp_path / "worker-logs"
    worker_logs.mkdir()
    old_log = worker_logs / "old-job.jsonl"
    old_log.write_text("old\n", encoding="utf-8")
    old_timestamp = (datetime.now(UTC) - timedelta(days=5)).timestamp()
    os.utime(old_log, (old_timestamp, old_timestamp))

    runner = worker_runtime_module.DockerRunner(
        execution_backend="wsl_docker",
        docker_backend="wsl_docker",
        wsl_distribution="Ubuntu",
        cleanup_enabled=True,
        cleanup_artifact_retention_hours=1,
        cleanup_log_retention_days=1,
        worker_log_dir=str(worker_logs),
    )
    guardrails = WorkerGuardrails(
        allowed_repo_roots=(tmp_path.resolve(),),
        denied_repo_roots=(),
        allowed_actions=("ci.test.run",),
        allowed_commands=("docker", "wsl"),
        max_runtime_seconds=30,
        max_memory_mb=256,
        max_artifact_bytes=131_072,
    )
    job = WorkerJob(
        job_id="job-cleanup-1",
        execution_mode="live",
        run_id="run-1",
        shard_id="shard-1",
        execution_target="cgs",
        action="ci.test.run",
        runner="docker",
        required_capabilities=("ci.test.run",),
        artifact_contract={},
        delegation_access=None,
        payload={
            "workspace_root": str(workspace_root),
            "compose_project": "owner-ci-z-e2e-dm-sim",
            "cleanup_labels": {
                "zetherion.owner_ci": "true",
                "zetherion.repo": "zetherion-ai",
                "zetherion.lane_id": "z-e2e-dm-sim",
            },
            "container_spec": {
                "image": "node:22-bookworm",
                "command": ["yarn", "test"],
            },
        },
    )

    result = await runner.run(job, guardrails)

    cleanup_receipt = result.cleanup_receipt or {}
    cleanup_path = Path(str(cleanup_receipt["path"]))

    assert result.status == "succeeded"
    assert cleanup_receipt["status"] == "cleaned"
    assert cleanup_receipt["compose_project"] == "owner-ci-z-e2e-dm-sim"
    assert cleanup_receipt["cleanup_labels"]["zetherion.owner_ci"] == "true"
    assert cleanup_path.exists()
    assert not (workspace_root / "test-results").exists()
    assert not (workspace_root / "playwright-report").exists()
    assert not (workspace_root / ".coverage.integration").exists()
    assert not (artifacts_dir / "z-e2e-dm-sim").exists()
    assert (artifacts_dir / "local-readiness-receipt.json").exists()
    assert not old_log.exists()
    docker_actions = cleanup_receipt["docker_actions"]
    assert docker_actions[0]["action"] == "compose_project_containers_query"
    assert any(action["action"] == "compose_project_containers_remove" for action in docker_actions)
    assert any(action["action"] == "labeled_containers_remove" for action in docker_actions)
    assert any(action["action"] == "container_prune" for action in docker_actions)
    assert any(command[:3] == ["wsl.exe", "-d", "Ubuntu"] for command in captured_commands[1:])
    assert any(
        "com.docker.compose.project=owner-ci-z-e2e-dm-sim" in " ".join(command)
        for command in captured_commands
    )


@pytest.mark.asyncio
async def test_docker_runner_builds_missing_tool_image_before_execution(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_commands: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        command = [str(arg) for arg in args]
        captured_commands.append(command)
        command_text = " ".join(command)
        if "docker image inspect zetherion-ci:latest" in command_text:
            return _FakeProcess(
                stdout=b"",
                stderr=b"Error: No such image: zetherion-ci:latest\n",
                returncode=1,
            )
        if "docker build" in command_text and "zetherion-ci:latest" in command_text:
            return _FakeProcess(stdout=b"Successfully built test-image\n", returncode=0)
        return _FakeProcess(stdout=b"ok\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    (workspace_root / "Dockerfile.dev-tools").write_text("FROM python:3.12-alpine3.20\n")

    runner = worker_runtime_module.DockerRunner(
        execution_backend="wsl_docker",
        docker_backend="wsl_docker",
        wsl_distribution="Ubuntu",
        cleanup_enabled=False,
    )
    guardrails = WorkerGuardrails(
        allowed_repo_roots=(tmp_path.resolve(),),
        denied_repo_roots=(),
        allowed_actions=("ci.test.run",),
        allowed_commands=("docker", "wsl"),
        max_runtime_seconds=30,
        max_memory_mb=256,
        max_artifact_bytes=131_072,
    )
    job = WorkerJob(
        job_id="job-build-image-1",
        execution_mode="live",
        run_id="run-1",
        shard_id="shard-1",
        execution_target="windows_local",
        action="ci.test.run",
        runner="docker",
        required_capabilities=("ci.test.run",),
        artifact_contract={},
        delegation_access=None,
        payload={
            "workspace_root": str(workspace_root),
            "container_spec": {
                "image": "zetherion-ci:latest",
                "command": ["node", "--version"],
            },
        },
    )

    result = await runner.run(job, guardrails)

    assert result.status == "succeeded"
    assert any(
        "docker image inspect zetherion-ci:latest" in " ".join(command)
        for command in captured_commands
    )
    assert any(
        "docker build" in " ".join(command) and "zetherion-ci:latest" in " ".join(command)
        for command in captured_commands
    )
    image_prepare_actions = result.output.get("image_prepare_actions") or []
    assert image_prepare_actions[0]["action"] == "image_inspect"
    assert image_prepare_actions[1]["action"] == "image_build"


@pytest.mark.asyncio
async def test_docker_runner_rebuilds_stale_tool_image_when_context_hash_changes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_commands: list[list[str]] = []

    async def _fake_create_subprocess_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        command = [str(arg) for arg in args]
        captured_commands.append(command)
        command_text = " ".join(command)
        if (
            "docker image inspect zetherion-ci:latest" in command_text
            and "--format" not in command_text
        ):
            return _FakeProcess(stdout=b"[{}]\n", returncode=0)
        if (
            "docker image inspect --format" in command_text
            and "zetherion-ci:latest" in command_text
        ):
            return _FakeProcess(stdout=b"stale-hash\n", returncode=0)
        if "docker build" in command_text and "zetherion-ci:latest" in command_text:
            return _FakeProcess(stdout=b"Successfully rebuilt test-image\n", returncode=0)
        return _FakeProcess(stdout=b"ok\n", returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True)
    (workspace_root / "docs").mkdir(parents=True)
    (workspace_root / "Dockerfile.dev-tools").write_text("FROM python:3.12-alpine3.20\n")
    (workspace_root / "requirements.txt").write_text("aiohttp==3.13.3\n")
    (workspace_root / "requirements-dev.txt").write_text("pytest==9.0.2\n")
    (workspace_root / "docs" / "requirements.txt").write_text("mkdocs==1.6.1\n")

    runner = worker_runtime_module.DockerRunner(
        execution_backend="wsl_docker",
        docker_backend="wsl_docker",
        wsl_distribution="Ubuntu",
        cleanup_enabled=False,
    )
    guardrails = WorkerGuardrails(
        allowed_repo_roots=(tmp_path.resolve(),),
        denied_repo_roots=(),
        allowed_actions=("ci.test.run",),
        allowed_commands=("docker", "wsl"),
        max_runtime_seconds=30,
        max_memory_mb=256,
        max_artifact_bytes=131_072,
    )
    job = WorkerJob(
        job_id="job-rebuild-image-1",
        execution_mode="live",
        run_id="run-1",
        shard_id="shard-1",
        execution_target="windows_local",
        action="ci.test.run",
        runner="docker",
        required_capabilities=("ci.test.run",),
        artifact_contract={},
        delegation_access=None,
        payload={
            "workspace_root": str(workspace_root),
            "container_spec": {
                "image": "zetherion-ci:latest",
                "command": ["node", "--version"],
            },
        },
    )

    result = await runner.run(job, guardrails)

    assert result.status == "succeeded"
    assert any(
        "docker image inspect --format" in " ".join(command)
        and "zetherion.tool_context_hash" in " ".join(command)
        for command in captured_commands
    )
    assert any(
        "docker build" in " ".join(command)
        and "zetherion.tool_context_hash=" in " ".join(command)
        for command in captured_commands
    )
    image_prepare_actions = result.output.get("image_prepare_actions") or []
    assert image_prepare_actions[1]["action"] == "image_label_inspect"
    assert image_prepare_actions[1]["label_matches"] is False
    assert image_prepare_actions[2]["action"] == "image_build"


@pytest.mark.asyncio
async def test_docker_runner_includes_local_readiness_receipt_in_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _fake_create_subprocess_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        return _FakeProcess(stdout=b"ok\n")

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    workspace_root = tmp_path / "workspace"
    artifacts_dir = workspace_root / ".artifacts"
    artifacts_dir.mkdir(parents=True)
    (artifacts_dir / "local-readiness-receipt.json").write_text(
        json.dumps(
            {
                "repo_id": "catalyst-group-solutions",
                "merge_ready": True,
                "deploy_ready": True,
                "summary": "ready",
                "failed_required_paths": [],
                "missing_evidence": [],
                "shard_receipts": [],
            }
        ),
        encoding="utf-8",
    )

    runner = worker_runtime_module.DockerRunner(
        execution_backend="wsl_docker",
        docker_backend="wsl_docker",
        wsl_distribution="Ubuntu",
        cleanup_enabled=False,
    )
    guardrails = WorkerGuardrails(
        allowed_repo_roots=(tmp_path.resolve(),),
        denied_repo_roots=(),
        allowed_actions=("ci.test.run",),
        allowed_commands=("docker", "wsl"),
        max_runtime_seconds=30,
        max_memory_mb=256,
        max_artifact_bytes=131_072,
    )
    job = WorkerJob(
        job_id="job-readiness-1",
        execution_mode="live",
        run_id="run-1",
        shard_id="shard-1",
        execution_target="windows_local",
        action="ci.test.run",
        runner="docker",
        required_capabilities=("ci.test.run",),
        artifact_contract={},
        delegation_access=None,
        payload={
            "workspace_root": str(workspace_root),
            "container_spec": {
                "image": "node:22-bookworm",
                "command": ["yarn", "test"],
            },
        },
    )

    result = await runner.run(job, guardrails)

    assert result.output is not None
    assert result.output["local_readiness_receipt"]["merge_ready"] is True
    assert result.debug_bundle["artifact_receipt_paths"]["local_readiness_receipt"].endswith(
        ".artifacts/local-readiness-receipt.json"
    )


@pytest.mark.asyncio
async def test_docker_runner_truncates_large_output_instead_of_failing_guardrail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    call_count = 0

    async def _fake_create_subprocess_exec(*args: str, **kwargs: Any) -> _FakeProcess:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            return _FakeProcess(
                stdout=("stdout-line\n" * 8000).encode("utf-8"),
                stderr=("stderr-line\n" * 8000).encode("utf-8"),
                returncode=1,
            )
        return _FakeProcess(stdout=b"cleanup\n", stderr=b"", returncode=0)

    monkeypatch.setattr(asyncio, "create_subprocess_exec", _fake_create_subprocess_exec)

    workspace_root = tmp_path / "workspace"
    workspace_root.mkdir(parents=True, exist_ok=True)
    runner = worker_runtime_module.DockerRunner(
        execution_backend="wsl_docker",
        docker_backend="wsl_docker",
        wsl_distribution="Ubuntu",
    )
    async def _fake_ensure_container_image(
        *,
        image: str,
        workspace_root: Path,
    ) -> list[dict[str, Any]]:
        return [{"action": "image_inspect", "image": image, "success": True, "exit_code": 0}]

    monkeypatch.setattr(runner, "_ensure_container_image", _fake_ensure_container_image)
    guardrails = WorkerGuardrails(
        allowed_repo_roots=(tmp_path.resolve(),),
        denied_repo_roots=(),
        allowed_actions=("ci.test.run",),
        allowed_commands=("docker", "wsl"),
        max_runtime_seconds=30,
        max_memory_mb=256,
        max_artifact_bytes=8192,
    )
    job = WorkerJob(
        job_id="job-large-output-1",
        execution_mode="live",
        run_id="run-large-1",
        shard_id="shard-large-1",
        execution_target="windows_local",
        action="ci.test.run",
        runner="docker",
        required_capabilities=("ci.test.run",),
        artifact_contract={},
        delegation_access=None,
        payload={
            "workspace_root": str(workspace_root),
            "container_spec": {
                "image": "node:22-bookworm",
                "command": ["bash", "-lc", "echo large"],
            },
        },
    )

    result = await runner.run(job, guardrails)

    assert result.status == "failed"
    assert result.error is not None
    assert result.error["code"] == "WORKER_RUNNER_EXIT_NON_ZERO"
    assert result.output is not None
    assert result.output["artifacts_truncated"] is True
    assert result.output["artifact_bytes"] > guardrails.max_artifact_bytes
    assert len(str(result.output["stdout"]).encode("utf-8")) < guardrails.max_artifact_bytes
    assert len(str(result.output["stderr"]).encode("utf-8")) < guardrails.max_artifact_bytes


@pytest.mark.asyncio
async def test_bounded_result_payload_preserves_underlying_job_failure_when_truncated(
    tmp_path: Path,
) -> None:
    runtime = WorkerRuntime(
        _base_config(
            tmp_path=tmp_path,
            base_url="http://127.0.0.1:8787",
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="worker-laptop-1",
        )
    )
    try:
        huge_message = "x" * 200_000
        payload = {
            **runtime._scope_payload(),
            "status": "failed",
            "error": {
                "code": "WORKER_RUNNER_EXIT_NON_ZERO",
                "message": "docker lane failed",
            },
            "output": {
                "runner": "docker_runner",
                "job_id": "job-bounded-1",
                "stdout": huge_message,
                "stderr": huge_message,
            },
            "required_capabilities": ["ci.test.run"],
            "log_chunks": [
                {"stream": "stdout", "message": huge_message, "metadata": {"runner": "docker"}},
                {"stream": "stderr", "message": huge_message, "metadata": {"runner": "docker"}},
            ],
            "cleanup_receipt": {"path": str(tmp_path / "cleanup.json")},
            "debug_bundle": {"reproduce_command": ["docker", "run"]},
        }

        bounded = runtime._bounded_result_payload(payload)
        encoded = json.dumps(bounded, separators=(",", ":"), default=str).encode("utf-8")

        assert bounded["status"] == "failed"
        assert bounded["error"]["code"] == "WORKER_RUNNER_EXIT_NON_ZERO"
        assert bounded["output"]["artifacts_truncated"] is True
        assert b"WORKER_GUARDRAIL_ARTIFACT_TOO_LARGE" not in encoded
        assert len(encoded) <= runtime._guardrails.max_artifact_bytes
    finally:
        await runtime.close()
