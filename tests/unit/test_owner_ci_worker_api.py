"""Focused tests for owner-scoped CI worker bridge routes on SkillsServer."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer


@pytest.fixture
def mock_registry() -> SkillRegistry:
    registry = MagicMock(spec=SkillRegistry)
    registry.list_ready_skills.return_value = []
    registry.skill_count = 0
    registry.run_heartbeat = AsyncMock(return_value=[])
    registry.list_skills.return_value = []
    registry.get_skill.return_value = None
    registry.get_status_summary.return_value = {"status": "ok"}
    registry.get_system_prompt_fragments.return_value = []
    registry.list_intents.return_value = {}
    return registry


@pytest.fixture
def owner_ci_storage() -> MagicMock:
    storage = MagicMock()
    storage.bootstrap_worker_node_session = AsyncMock(
        return_value={
            "session_id": "session-1",
            "token": "bootstrap-token",
            "signing_secret": "bootstrap-signing-secret",
            "expires_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat(),
        }
    )
    storage.get_worker_node = AsyncMock(
        return_value={
            "scope_id": "owner:operator-1:repo:zetherion-ai",
            "node_id": "node-1",
            "status": "registered",
        }
    )
    storage.submit_worker_job_result = AsyncMock(
        return_value={
            "job_id": "job-1",
            "submitted_at": datetime.now(UTC).isoformat(),
            "idempotent": True,
        }
    )
    storage.hash_worker_token = (
        lambda token: __import__("hashlib").sha256(token.encode()).hexdigest()
    )
    return storage


class TestOwnerCiWorkerApi:
    async def test_owner_ci_worker_bootstrap_accepts_scope_id(
        self,
        mock_registry: SkillRegistry,
        owner_ci_storage: MagicMock,
    ) -> None:
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            owner_ci_storage=owner_ci_storage,
        )
        server._owner_ci_worker_bootstrap_secret = "owner-ci-bootstrap"
        app = server.create_app()

        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/owner/ci/worker/v1/bootstrap",
                headers={"X-Worker-Bootstrap-Secret": "owner-ci-bootstrap"},
                json={
                    "scope_id": "owner:operator-1:repo:zetherion-ai",
                    "node_id": "node-1",
                    "capabilities": ["ci.test.run"],
                },
            )
            payload = await response.json()

        assert response.status == 201
        assert payload["scope_id"] == "owner:operator-1:repo:zetherion-ai"
        owner_ci_storage.bootstrap_worker_node_session.assert_awaited_once()

    async def test_owner_ci_worker_result_accepts_relay_replay_with_api_secret(
        self,
        mock_registry: SkillRegistry,
        owner_ci_storage: MagicMock,
    ) -> None:
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            owner_ci_storage=owner_ci_storage,
        )
        app = server.create_app()

        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/owner/ci/worker/v1/nodes/node-1/jobs/job-1/result",
                headers={
                    "X-API-Secret": "skills-secret",
                    "X-CI-Relay-Replay": "1",
                    "Content-Type": "application/json",
                },
                data=json.dumps(
                    {
                        "scope_id": "owner:operator-1:repo:zetherion-ai",
                        "status": "succeeded",
                        "output": {"message": "replayed"},
                        "idempotency_key": "run-1:job-1",
                    }
                ),
            )
            payload = await response.json()

        assert response.status == 202
        assert payload["accepted"] is True
        assert payload["idempotent"] is True
        owner_ci_storage.submit_worker_job_result.assert_awaited_once()

    async def test_owner_ci_worker_result_forwards_observability_payload(
        self,
        mock_registry: SkillRegistry,
        owner_ci_storage: MagicMock,
    ) -> None:
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            owner_ci_storage=owner_ci_storage,
        )
        server._owner_ci_worker_bootstrap_secret = "owner-ci-bootstrap"
        owner_ci_storage.get_worker_session_auth = AsyncMock(
            return_value={
                "scope_id": "owner:operator-1:repo:zetherion-ai",
                "node_id": "node-1",
                "session_id": "session-1",
                "token_hash": owner_ci_storage.hash_worker_token("worker-token"),
                "signing_secret": "signing-secret",
                "revoked_at": None,
                "expires_at": None,
            }
        )
        owner_ci_storage.touch_worker_session = AsyncMock()
        app = server.create_app()

        body = json.dumps(
            {
                "scope_id": "owner:operator-1:repo:zetherion-ai",
                "status": "succeeded",
                "output": {"message": "ok"},
                "events": [{"event_type": "worker.completed", "payload": {"ok": True}}],
                "log_chunks": [{"stream": "stdout", "message": "done"}],
                "resource_samples": [{"memory_mb": 10}],
                "debug_bundle": {"reproduce_command": ["docker", "run"]},
            },
            separators=(",", ":"),
        )
        timestamp = str(int(datetime.now(UTC).timestamp()))
        nonce = "nonce-1"
        import hashlib
        import hmac

        signature = hmac.new(
            b"signing-secret",
            (
                "owner:operator-1:repo:zetherion-ai." f"node-1.session-1.{timestamp}.{nonce}.{body}"
            ).encode(),
            hashlib.sha256,
        ).hexdigest()

        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                "/owner/ci/worker/v1/nodes/node-1/jobs/job-1/result",
                headers={
                    "Authorization": "Bearer worker-token",
                    "X-Worker-Session-Id": "session-1",
                    "X-Worker-Timestamp": timestamp,
                    "X-Worker-Nonce": nonce,
                    "X-Worker-Signature": signature,
                    "Content-Type": "application/json",
                },
                data=body,
            )

        assert response.status == 202
        forwarded = owner_ci_storage.submit_worker_job_result.await_args.kwargs["payload"]["output"]
        assert forwarded["events"][0]["event_type"] == "worker.completed"
        assert forwarded["log_chunks"][0]["message"] == "done"
        assert forwarded["resource_samples"][0]["memory_mb"] == 10
