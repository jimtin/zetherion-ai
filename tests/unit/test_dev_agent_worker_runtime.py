# ruff: noqa: E402
"""Tests for dev-agent worker runtime and guarded job execution."""

from __future__ import annotations

import hashlib
import hmac
import json
import sys
from pathlib import Path
from typing import Any

import pytest
from aiohttp import web
from aiohttp.test_utils import TestServer

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_AGENT_SRC = PROJECT_ROOT / "zetherion-dev-agent" / "src"
if str(DEV_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(DEV_AGENT_SRC))

from zetherion_dev_agent.config import AgentConfig
from zetherion_dev_agent.policy_store import PolicyStore
from zetherion_dev_agent.worker_runtime import (
    INFLIGHT_META_KEY,
    RESTART_RECOVERY_ERROR_CODE,
    SESSION_META_KEY,
    WorkerRuntime,
)


class _FakeWorkerBridge:
    def __init__(
        self,
        *,
        tenant_id: str,
        node_id: str,
        bootstrap_secret: str,
        jobs: list[dict[str, Any] | None],
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
        app.router.add_post("/worker/v1/bootstrap", self.handle_bootstrap)
        app.router.add_post("/worker/v1/nodes/register", self.handle_register)
        app.router.add_post(
            f"/worker/v1/nodes/{self.node_id}/heartbeat",
            self.handle_heartbeat,
        )
        app.router.add_post(
            f"/worker/v1/nodes/{self.node_id}/jobs/claim",
            self.handle_claim,
        )
        app.router.add_post(
            f"/worker/v1/nodes/{self.node_id}/jobs/{{job_id}}/result",
            self.handle_result,
        )
        return app

    async def handle_bootstrap(self, request: web.Request) -> web.Response:
        assert request.headers.get("X-Worker-Bootstrap-Secret") == self.bootstrap_secret
        payload = await request.json()
        assert payload["tenant_id"] == self.tenant_id
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
        assert payload["tenant_id"] == self.tenant_id
        assert payload["node_id"] == self.node_id
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
        assert payload["tenant_id"] == self.tenant_id
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
                "tenant_id": self.tenant_id,
                "node_id": self.node_id,
                "job": job,
                "poll_after_seconds": 5,
            }
        )

    async def handle_result(self, request: web.Request) -> web.Response:
        raw = await request.text()
        payload = json.loads(raw)
        self._assert_signed(
            request=request,
            raw_body=raw,
            token=self.active_token,
            signing_secret=self.active_signing_secret,
        )
        self.result_payloads.append(payload)
        return web.json_response({"ok": True, "accepted": True}, status=202)

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
        canonical = f"{self.tenant_id}.{self.node_id}.{session_id}.{timestamp}.{nonce}.{raw_body}"
        expected_signature = hmac.new(
            signing_secret.encode("utf-8"),
            canonical.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        assert hmac.compare_digest(provided_signature, expected_signature)


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
