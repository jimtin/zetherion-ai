"""Focused tests for worker control API endpoints on SkillsServer."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.security.trust_policy import (
    TrustActionClass,
    TrustDecisionOutcome,
    TrustPolicyDecision,
)
from zetherion_ai.skills.base import SkillResponse
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _sign_worker(
    *,
    tenant_id: str,
    node_id: str,
    session_id: str,
    timestamp: str,
    nonce: str,
    raw_body: str,
    secret: str,
) -> str:
    canonical = f"{tenant_id}.{node_id}.{session_id}.{timestamp}.{nonce}.{raw_body}"
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


def _admin_headers(*, signing_secret: str) -> dict[str, str]:
    payload = {
        "actor_sub": "operator-1",
        "actor_roles": ["operator"],
        "request_id": f"req-{uuid4().hex[:8]}",
        "timestamp": datetime.now(UTC).isoformat(),
        "nonce": uuid4().hex,
        "actor_email": "ops@example.com",
    }
    encoded = (
        base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip("=")
    )
    signature = hmac.new(signing_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256)
    return {
        "X-Admin-Actor": encoded,
        "X-Admin-Signature": signature.hexdigest(),
    }


def _worker_headers(
    *,
    tenant_id: str,
    node_id: str,
    session_id: str,
    token: str,
    signing_secret: str,
    raw_body: str,
    nonce: str,
    timestamp: str | None = None,
) -> dict[str, str]:
    ts = timestamp or str(int(time.time()))
    return {
        "Authorization": f"Bearer {token}",
        "X-Worker-Session-Id": session_id,
        "X-Worker-Timestamp": ts,
        "X-Worker-Nonce": nonce,
        "X-Worker-Signature": _sign_worker(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            timestamp=ts,
            nonce=nonce,
            raw_body=raw_body,
            secret=signing_secret,
        ),
        "Content-Type": "application/json",
    }


def _decision(
    *,
    action: str,
    allowed: bool,
    status: int = 409,
    code: str = "AI_TRUST_POLICY_GUARD_FAILED",
    message: str = "denied",
) -> TrustPolicyDecision:
    return TrustPolicyDecision(
        action=action,
        action_class=TrustActionClass.SENSITIVE,
        outcome=TrustDecisionOutcome.ALLOW if allowed else TrustDecisionOutcome.DENY,
        status=200 if allowed else status,
        code="AI_TRUST_POLICY_ALLOWED" if allowed else code,
        message="allowed" if allowed else message,
        details={},
        requires_two_person=False,
    )


@pytest.fixture
def mock_registry() -> SkillRegistry:
    registry = MagicMock(spec=SkillRegistry)
    registry.list_ready_skills.return_value = []
    registry.skill_count = 0
    registry.handle_request = AsyncMock(
        return_value=SkillResponse(request_id="req", success=True, message="ok")
    )
    registry.run_heartbeat = AsyncMock(return_value=[])
    registry.list_skills.return_value = []
    registry.get_skill.return_value = None
    registry.get_status_summary.return_value = {"status": "ok"}
    registry.get_system_prompt_fragments.return_value = []
    registry.list_intents.return_value = {}
    return registry


@pytest.fixture
def worker_manager() -> MagicMock:
    mgr = MagicMock()
    mgr.get_secret_cached = MagicMock(return_value="")
    mgr.bootstrap_worker_node_session = AsyncMock(
        return_value={
            "node": {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "node_id": "node-1",
                "status": "bootstrap_pending",
                "health_status": "unknown",
                "metadata": {},
            },
            "session": {
                "session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "expires_at": datetime.now(UTC) + timedelta(hours=24),
            },
            "capabilities": ["repo.patch", "repo.pr.open"],
        }
    )
    mgr.get_worker_session_auth = AsyncMock(return_value=None)
    mgr.touch_worker_session = AsyncMock(return_value=True)
    mgr.register_worker_node = AsyncMock(
        return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "node_id": "node-1",
            "status": "registered",
            "health_status": "healthy",
            "capabilities": ["repo.patch", "repo.pr.open"],
            "metadata": {},
        }
    )
    mgr.rotate_worker_session_credentials = AsyncMock(
        return_value={
            "session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "expires_at": datetime.now(UTC) + timedelta(hours=24),
            "rotated_at": datetime.now(UTC),
        }
    )
    mgr.heartbeat_worker_node = AsyncMock(
        return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "node_id": "node-1",
            "status": "active",
            "health_status": "healthy",
            "metadata": {},
        }
    )
    mgr.has_worker_capabilities = AsyncMock(return_value=True)
    mgr.claim_worker_dispatch_job = AsyncMock(return_value=None)
    mgr.submit_worker_job_result = AsyncMock(
        return_value={
            "accepted": True,
            "idempotent": False,
            "status": "succeeded",
        }
    )
    mgr.record_worker_job_event = AsyncMock(return_value={"event_id": 1})
    mgr.list_worker_nodes = AsyncMock(return_value=[])
    mgr.get_worker_node = AsyncMock(return_value=None)
    mgr.set_worker_capabilities = AsyncMock(return_value={})
    return mgr


class TestSkillsWorkerControlAPI:
    async def test_worker_health_and_bootstrap_guard_paths(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        worker_manager.get_secret_cached = MagicMock(return_value="bootstrap-secret")
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
        )
        app = server.create_app()

        async with TestClient(TestServer(app)) as client:
            health = await client.get("/worker/v1/health")
            assert health.status == 200

            invalid_json = await client.post("/worker/v1/bootstrap", data="not-json")
            assert invalid_json.status == 400

            missing_tenant = await client.post(
                "/worker/v1/bootstrap",
                headers={"X-Worker-Bootstrap-Secret": "bootstrap-secret"},
                json={"node_id": "node-1"},
            )
            assert missing_tenant.status == 400

            invalid_secret = await client.post(
                "/worker/v1/bootstrap",
                headers={"X-Worker-Bootstrap-Secret": "wrong"},
                json={"tenant_id": tenant_id, "node_id": "node-1"},
            )
            assert invalid_secret.status == 401

            server._trust_policy_evaluator.evaluate = MagicMock(  # type: ignore[method-assign]
                return_value=_decision(action="worker.register", allowed=False)
            )
            denied = await client.post(
                "/worker/v1/bootstrap",
                headers={"X-Worker-Bootstrap-Secret": "bootstrap-secret"},
                json={"tenant_id": tenant_id, "node_id": "node-1"},
            )
            assert denied.status == 409
            denied_payload = await denied.json()
            assert denied_payload["code"] == "AI_TRUST_POLICY_GUARD_FAILED"

            worker_manager.bootstrap_worker_node_session.side_effect = ValueError(
                "metadata must be an object when provided"
            )
            server._trust_policy_evaluator.evaluate = MagicMock(  # type: ignore[method-assign]
                return_value=_decision(action="worker.register", allowed=True)
            )
            metadata_bad = await client.post(
                "/worker/v1/bootstrap",
                headers={"X-Worker-Bootstrap-Secret": "bootstrap-secret"},
                json={"tenant_id": tenant_id, "node_id": "node-1", "metadata": []},
            )
            assert metadata_bad.status == 400

    async def test_worker_bootstrap_register_heartbeat_claim_and_result(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        node_id = "node-1"
        session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        bootstrap_token = "bootstrap-token"
        bootstrap_signing_secret = "bootstrap-signing-secret"
        rotated_token = "rotated-token"
        rotated_signing_secret = "rotated-signing-secret"

        worker_manager.get_secret_cached = MagicMock(
            side_effect=lambda _tenant, name, default="": (
                "bootstrap-secret" if name == "WORKER_BRIDGE_BOOTSTRAP_SECRET" else default
            )
        )
        worker_manager.get_worker_session_auth = AsyncMock(
            side_effect=[
                {
                    "session_id": session_id,
                    "token_hash": _hash_token(bootstrap_token),
                    "signing_secret": bootstrap_signing_secret,
                    "status": "bootstrap_pending",
                    "health_status": "unknown",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    "revoked_at": None,
                },
                {
                    "session_id": session_id,
                    "token_hash": _hash_token(rotated_token),
                    "signing_secret": rotated_signing_secret,
                    "status": "registered",
                    "health_status": "healthy",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    "revoked_at": None,
                },
                {
                    "session_id": session_id,
                    "token_hash": _hash_token(rotated_token),
                    "signing_secret": rotated_signing_secret,
                    "status": "active",
                    "health_status": "healthy",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    "revoked_at": None,
                },
                {
                    "session_id": session_id,
                    "token_hash": _hash_token(rotated_token),
                    "signing_secret": rotated_signing_secret,
                    "status": "active",
                    "health_status": "healthy",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    "revoked_at": None,
                },
            ]
        )

        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
        )
        app = server.create_app()

        with patch(
            "zetherion_ai.skills.server.secrets.token_urlsafe",
            side_effect=[
                bootstrap_token,
                bootstrap_signing_secret,
                rotated_token,
                rotated_signing_secret,
            ],
        ):
            async with TestClient(TestServer(app)) as client:
                bootstrap_resp = await client.post(
                    "/worker/v1/bootstrap",
                    headers={"X-Worker-Bootstrap-Secret": "bootstrap-secret"},
                    json={
                        "tenant_id": tenant_id,
                        "node_id": node_id,
                        "capabilities": ["repo.patch", "repo.pr.open"],
                    },
                )
                assert bootstrap_resp.status == 201
                bootstrap_payload = await bootstrap_resp.json()
                assert bootstrap_payload["session"]["token"] == bootstrap_token
                assert bootstrap_payload["session"]["signing_secret"] == bootstrap_signing_secret

                timestamp = str(int(time.time()))
                register_body = {
                    "tenant_id": tenant_id,
                    "node_id": node_id,
                    "capabilities": ["repo.patch", "repo.pr.open"],
                    "rotate_credentials": True,
                }
                register_raw = json.dumps(register_body, separators=(",", ":"))
                register_headers = {
                    "Authorization": f"Bearer {bootstrap_token}",
                    "X-Worker-Session-Id": session_id,
                    "X-Worker-Timestamp": timestamp,
                    "X-Worker-Nonce": "nonce-register",
                    "X-Worker-Signature": _sign_worker(
                        tenant_id=tenant_id,
                        node_id=node_id,
                        session_id=session_id,
                        timestamp=timestamp,
                        nonce="nonce-register",
                        raw_body=register_raw,
                        secret=bootstrap_signing_secret,
                    ),
                    "Content-Type": "application/json",
                }
                register_resp = await client.post(
                    "/worker/v1/nodes/register",
                    headers=register_headers,
                    data=register_raw,
                )
                assert register_resp.status == 200
                register_payload = await register_resp.json()
                assert register_payload["session"]["token"] == rotated_token
                assert register_payload["session"]["signing_secret"] == rotated_signing_secret

                heartbeat_body = {"tenant_id": tenant_id, "health_status": "healthy"}
                heartbeat_raw = json.dumps(heartbeat_body, separators=(",", ":"))
                heartbeat_headers = {
                    "Authorization": f"Bearer {rotated_token}",
                    "X-Worker-Session-Id": session_id,
                    "X-Worker-Timestamp": timestamp,
                    "X-Worker-Nonce": "nonce-heartbeat",
                    "X-Worker-Signature": _sign_worker(
                        tenant_id=tenant_id,
                        node_id=node_id,
                        session_id=session_id,
                        timestamp=timestamp,
                        nonce="nonce-heartbeat",
                        raw_body=heartbeat_raw,
                        secret=rotated_signing_secret,
                    ),
                    "Content-Type": "application/json",
                }
                heartbeat_resp = await client.post(
                    f"/worker/v1/nodes/{node_id}/heartbeat",
                    headers=heartbeat_headers,
                    data=heartbeat_raw,
                )
                assert heartbeat_resp.status == 200

                claim_body = {"tenant_id": tenant_id, "required_capabilities": ["repo.patch"]}
                claim_raw = json.dumps(claim_body, separators=(",", ":"))
                claim_headers = {
                    "Authorization": f"Bearer {rotated_token}",
                    "X-Worker-Session-Id": session_id,
                    "X-Worker-Timestamp": timestamp,
                    "X-Worker-Nonce": "nonce-claim",
                    "X-Worker-Signature": _sign_worker(
                        tenant_id=tenant_id,
                        node_id=node_id,
                        session_id=session_id,
                        timestamp=timestamp,
                        nonce="nonce-claim",
                        raw_body=claim_raw,
                        secret=rotated_signing_secret,
                    ),
                    "Content-Type": "application/json",
                }
                claim_resp = await client.post(
                    f"/worker/v1/nodes/{node_id}/jobs/claim",
                    headers=claim_headers,
                    data=claim_raw,
                )
                assert claim_resp.status == 200
                claim_payload = await claim_resp.json()
                assert claim_payload["job"] is None

                result_body = {
                    "tenant_id": tenant_id,
                    "status": "succeeded",
                    "output": {"message": "done"},
                }
                result_raw = json.dumps(result_body, separators=(",", ":"))
                result_headers = {
                    "Authorization": f"Bearer {rotated_token}",
                    "X-Worker-Session-Id": session_id,
                    "X-Worker-Timestamp": timestamp,
                    "X-Worker-Nonce": "nonce-result",
                    "X-Worker-Signature": _sign_worker(
                        tenant_id=tenant_id,
                        node_id=node_id,
                        session_id=session_id,
                        timestamp=timestamp,
                        nonce="nonce-result",
                        raw_body=result_raw,
                        secret=rotated_signing_secret,
                    ),
                    "Content-Type": "application/json",
                }
                result_resp = await client.post(
                    f"/worker/v1/nodes/{node_id}/jobs/job-1/result",
                    headers=result_headers,
                    data=result_raw,
                )
                assert result_resp.status == 202

        worker_manager.bootstrap_worker_node_session.assert_awaited_once()
        worker_manager.register_worker_node.assert_awaited_once()
        worker_manager.rotate_worker_session_credentials.assert_awaited_once()
        worker_manager.heartbeat_worker_node.assert_awaited_once()
        assert worker_manager.record_worker_job_event.await_count >= 3

    async def test_worker_register_without_rotation_and_bad_timestamp(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        node_id = "node-1"
        session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        token = "bootstrap-token"
        signing_secret = "bootstrap-signing-secret"
        worker_manager.get_worker_session_auth = AsyncMock(
            side_effect=[
                {
                    "session_id": session_id,
                    "token_hash": _hash_token(token),
                    "signing_secret": signing_secret,
                    "status": "registered",
                    "health_status": "healthy",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    "revoked_at": None,
                },
                {
                    "session_id": session_id,
                    "token_hash": _hash_token(token),
                    "signing_secret": signing_secret,
                    "status": "registered",
                    "health_status": "healthy",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    "revoked_at": None,
                },
            ]
        )
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
        )
        app = server.create_app()

        register_body = {
            "tenant_id": tenant_id,
            "node_id": node_id,
            "capabilities": ["repo.patch"],
            "rotate_credentials": False,
        }
        register_raw = json.dumps(register_body, separators=(",", ":"))

        old_timestamp = str(int(time.time()) - 3600)
        stale_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=register_raw,
            nonce="nonce-stale",
            timestamp=old_timestamp,
        )

        valid_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=register_raw,
            nonce="nonce-register-no-rotate",
        )

        async with TestClient(TestServer(app)) as client:
            stale = await client.post(
                "/worker/v1/nodes/register",
                headers=stale_headers,
                data=register_raw,
            )
            assert stale.status == 401

            ok = await client.post(
                "/worker/v1/nodes/register",
                headers=valid_headers,
                data=register_raw,
            )
            assert ok.status == 200
            payload = await ok.json()
            assert payload["session"]["session_id"] == session_id
            assert "token" not in payload["session"]

        worker_manager.rotate_worker_session_credentials.assert_not_awaited()

    async def test_worker_nonce_replay_rejected(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        node_id = "node-1"
        session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        token = "rotated-token"
        signing_secret = "rotated-signing-secret"
        worker_manager.get_worker_session_auth = AsyncMock(
            return_value={
                "session_id": session_id,
                "token_hash": _hash_token(token),
                "signing_secret": signing_secret,
                "status": "active",
                "health_status": "healthy",
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "revoked_at": None,
            }
        )
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
        )
        app = server.create_app()
        timestamp = str(int(time.time()))
        body = {"tenant_id": tenant_id}
        raw = json.dumps(body, separators=(",", ":"))
        nonce = "nonce-replay"
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Worker-Session-Id": session_id,
            "X-Worker-Timestamp": timestamp,
            "X-Worker-Nonce": nonce,
            "X-Worker-Signature": _sign_worker(
                tenant_id=tenant_id,
                node_id=node_id,
                session_id=session_id,
                timestamp=timestamp,
                nonce=nonce,
                raw_body=raw,
                secret=signing_secret,
            ),
            "Content-Type": "application/json",
        }

        async with TestClient(TestServer(app)) as client:
            first = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/claim",
                headers=headers,
                data=raw,
            )
            assert first.status == 200
            second = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/claim",
                headers=headers,
                data=raw,
            )
            assert second.status == 409

    async def test_worker_claim_returns_job_payload_when_dispatch_available(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        node_id = "node-1"
        session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        token = "token"
        signing_secret = "signing-secret"
        worker_manager.get_worker_session_auth = AsyncMock(
            return_value={
                "session_id": session_id,
                "token_hash": _hash_token(token),
                "signing_secret": signing_secret,
                "status": "active",
                "health_status": "healthy",
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "revoked_at": None,
            }
        )
        worker_manager.claim_worker_dispatch_job = AsyncMock(
            return_value={
                "job_id": "job-123",
                "plan_id": "plan-1",
                "step_id": "step-1",
                "retry_id": "retry-1",
                "execution_target": "any_worker",
                "action": "worker.noop",
                "required_capabilities": ["repo.patch"],
                "max_runtime_seconds": 600,
                "artifact_contract": {"expect": "summary"},
                "payload_json": {"runner": "noop", "prompt_text": "Run task"},
            }
        )
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
        )
        app = server.create_app()

        claim_body = {"tenant_id": tenant_id, "required_capabilities": ["repo.patch"]}
        claim_raw = json.dumps(claim_body, separators=(",", ":"))
        headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=claim_raw,
            nonce="nonce-claim-job-payload",
        )

        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/claim",
                headers=headers,
                data=claim_raw,
            )
            assert response.status == 200
            payload = await response.json()
            assert payload["job"]["job_id"] == "job-123"
            assert payload["job"]["runner"] == "noop"
            assert payload["job"]["payload"]["prompt_text"] == "Run task"

    async def test_worker_claim_returns_502_when_dispatch_claim_raises_unexpected(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        node_id = "node-1"
        session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        token = "token"
        signing_secret = "signing-secret"
        worker_manager.get_worker_session_auth = AsyncMock(
            return_value={
                "session_id": session_id,
                "token_hash": _hash_token(token),
                "signing_secret": signing_secret,
                "status": "active",
                "health_status": "healthy",
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "revoked_at": None,
            }
        )
        worker_manager.claim_worker_dispatch_job = AsyncMock(side_effect=Exception("boom"))
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
        )
        app = server.create_app()

        claim_body = {"tenant_id": tenant_id, "required_capabilities": ["repo.patch"]}
        claim_raw = json.dumps(claim_body, separators=(",", ":"))
        headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=claim_raw,
            nonce="nonce-claim-failure",
        )

        async with TestClient(TestServer(app)) as client:
            response = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/claim",
                headers=headers,
                data=claim_raw,
            )
            assert response.status == 502

    async def test_worker_submit_result_validation_and_runtime_error_paths(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        node_id = "node-1"
        session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        token = "token"
        signing_secret = "signing-secret"
        worker_manager.get_worker_session_auth = AsyncMock(
            return_value={
                "session_id": session_id,
                "token_hash": _hash_token(token),
                "signing_secret": signing_secret,
                "status": "active",
                "health_status": "healthy",
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "revoked_at": None,
            }
        )
        worker_manager.submit_worker_job_result = AsyncMock(
            return_value={"accepted": True, "idempotent": False, "status": "succeeded"}
        )
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
        )
        app = server.create_app()

        async with TestClient(TestServer(app)) as client:
            missing_ids = await client.post(
                "/worker/v1/nodes/%20/jobs/%20/result",
                data='{"tenant_id":"x"}',
                headers={"Content-Type": "application/json"},
            )
            assert missing_ids.status == 400

            invalid_json = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/job-1/result",
                data="{",
                headers={"Content-Type": "application/json"},
            )
            assert invalid_json.status == 400

            missing_tenant = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/job-1/result",
                data='{"status":"succeeded"}',
                headers={"Content-Type": "application/json"},
            )
            assert missing_tenant.status == 400

            bad_output_raw = json.dumps(
                {"tenant_id": tenant_id, "status": "succeeded", "output": "bad"},
                separators=(",", ":"),
            )
            bad_output = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/job-1/result",
                data=bad_output_raw,
                headers=_worker_headers(
                    tenant_id=tenant_id,
                    node_id=node_id,
                    session_id=session_id,
                    token=token,
                    signing_secret=signing_secret,
                    raw_body=bad_output_raw,
                    nonce="nonce-result-bad-output",
                ),
            )
            assert bad_output.status == 400

            bad_error_raw = json.dumps(
                {"tenant_id": tenant_id, "status": "failed", "error": "bad"},
                separators=(",", ":"),
            )
            bad_error = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/job-1/result",
                data=bad_error_raw,
                headers=_worker_headers(
                    tenant_id=tenant_id,
                    node_id=node_id,
                    session_id=session_id,
                    token=token,
                    signing_secret=signing_secret,
                    raw_body=bad_error_raw,
                    nonce="nonce-result-bad-error",
                ),
            )
            assert bad_error.status == 400

            worker_manager.submit_worker_job_result = AsyncMock(side_effect=RuntimeError("db-down"))
            runtime_raw = json.dumps(
                {"tenant_id": tenant_id, "status": "succeeded"},
                separators=(",", ":"),
            )
            runtime_error = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/job-1/result",
                data=runtime_raw,
                headers=_worker_headers(
                    tenant_id=tenant_id,
                    node_id=node_id,
                    session_id=session_id,
                    token=token,
                    signing_secret=signing_secret,
                    raw_body=runtime_raw,
                    nonce="nonce-result-runtime-409",
                ),
            )
            assert runtime_error.status == 409

            worker_manager.submit_worker_job_result = AsyncMock(
                side_effect=ValueError("bad-result-payload")
            )
            value_raw = json.dumps(
                {"tenant_id": tenant_id, "status": "succeeded"},
                separators=(",", ":"),
            )
            value_error = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/job-1/result",
                data=value_raw,
                headers=_worker_headers(
                    tenant_id=tenant_id,
                    node_id=node_id,
                    session_id=session_id,
                    token=token,
                    signing_secret=signing_secret,
                    raw_body=value_raw,
                    nonce="nonce-result-value-400",
                ),
            )
            assert value_error.status == 400

    async def test_worker_signature_invalid_and_admin_route_not_accessible(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        node_id = "node-1"
        session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        token = "token"
        worker_manager.get_worker_session_auth = AsyncMock(
            return_value={
                "session_id": session_id,
                "token_hash": _hash_token(token),
                "signing_secret": "correct-secret",
                "status": "active",
                "health_status": "healthy",
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "revoked_at": None,
            }
        )
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
        )
        app = server.create_app()

        timestamp = str(int(time.time()))
        body = {"tenant_id": tenant_id}
        raw = json.dumps(body, separators=(",", ":"))
        headers = {
            "Authorization": f"Bearer {token}",
            "X-Worker-Session-Id": session_id,
            "X-Worker-Timestamp": timestamp,
            "X-Worker-Nonce": "nonce-1",
            "X-Worker-Signature": "bad-signature",
            "Content-Type": "application/json",
        }

        async with TestClient(TestServer(app)) as client:
            denied = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/claim",
                headers=headers,
                data=raw,
            )
            assert denied.status == 400

            admin_route = await client.get(
                f"/admin/tenants/{tenant_id}/workers/nodes",
                headers={
                    "Authorization": f"Bearer {token}",
                    "X-Worker-Session-Id": session_id,
                    "X-Worker-Timestamp": timestamp,
                    "X-Worker-Nonce": "nonce-admin",
                    "X-Worker-Signature": "bad-signature",
                },
            )
            assert admin_route.status == 401

    async def test_admin_worker_capability_update_guard_uses_trust_policy(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        node_id = "node-1"
        worker_manager.get_worker_node = AsyncMock(
            return_value={
                "tenant_id": tenant_id,
                "node_id": node_id,
                "status": "quarantined",
                "health_status": "healthy",
                "capabilities": ["repo.patch"],
            }
        )
        worker_manager.set_worker_capabilities = AsyncMock(
            side_effect=AssertionError("set_worker_capabilities should not be called on denial")
        )

        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
            admin_actor_secret="admin-secret",
        )
        app = server.create_app()
        headers = {"X-API-Secret": "skills-secret"}
        headers.update(_admin_headers(signing_secret="admin-secret"))

        async with TestClient(TestServer(app)) as client:
            response = await client.put(
                f"/admin/tenants/{tenant_id}/workers/nodes/{node_id}/capabilities",
                headers=headers,
                json={"capabilities": ["repo.patch"], "explicitly_elevated": False},
            )
            assert response.status == 409
            payload = await response.json()
            assert payload["code"] in {"AI_TRUST_POLICY_GUARD_FAILED", "AI_APPROVAL_REQUIRED"}

    async def test_worker_heartbeat_claim_result_and_admin_inventory_paths(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        node_id = "node-1"
        session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        token = "rotated-token"
        signing_secret = "rotated-signing-secret"
        worker_manager.get_worker_session_auth = AsyncMock(
            side_effect=[
                {
                    "session_id": session_id,
                    "token_hash": _hash_token(token),
                    "signing_secret": signing_secret,
                    "status": "active",
                    "health_status": "healthy",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    "revoked_at": None,
                },
                {
                    "session_id": session_id,
                    "token_hash": _hash_token(token),
                    "signing_secret": signing_secret,
                    "status": "quarantined",
                    "health_status": "degraded",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    "revoked_at": None,
                },
                {
                    "session_id": session_id,
                    "token_hash": _hash_token(token),
                    "signing_secret": signing_secret,
                    "status": "active",
                    "health_status": "healthy",
                    "expires_at": datetime.now(UTC) + timedelta(hours=1),
                    "revoked_at": None,
                },
            ]
        )
        worker_manager.has_worker_capabilities = AsyncMock(side_effect=[False, True])
        worker_manager.list_worker_nodes = AsyncMock(
            return_value=[
                {
                    "tenant_id": tenant_id,
                    "node_id": node_id,
                    "status": "active",
                    "health_status": "healthy",
                    "metadata": {},
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            ]
        )
        worker_manager.get_worker_node = AsyncMock(
            side_effect=[
                None,
                {
                    "tenant_id": tenant_id,
                    "node_id": node_id,
                    "status": "active",
                    "health_status": "healthy",
                    "capabilities": ["repo.patch"],
                },
            ]
        )
        worker_manager.set_worker_capabilities = AsyncMock(
            return_value={
                "tenant_id": tenant_id,
                "node_id": node_id,
                "status": "active",
                "health_status": "healthy",
                "capabilities": ["repo.patch", "repo.pr.open"],
            }
        )

        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
            admin_actor_secret="admin-secret",
        )
        app = server.create_app()

        heartbeat_body = {"tenant_id": tenant_id, "node_id": "different-node"}
        heartbeat_raw = json.dumps(heartbeat_body, separators=(",", ":"))
        heartbeat_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=heartbeat_raw,
            nonce="nonce-heartbeat-mismatch",
        )

        claim_body = {"tenant_id": tenant_id, "required_capabilities": ["repo.patch"]}
        claim_raw = json.dumps(claim_body, separators=(",", ":"))
        claim_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=claim_raw,
            nonce="nonce-claim-denied",
        )

        result_body = {"tenant_id": tenant_id, "status": "succeeded"}
        result_raw = json.dumps(result_body, separators=(",", ":"))
        result_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=result_raw,
            nonce="nonce-result-ok",
        )

        def _auth_admin_headers() -> dict[str, str]:
            headers = {"X-API-Secret": "skills-secret"}
            headers.update(_admin_headers(signing_secret="admin-secret"))
            return headers

        real_evaluate = server._trust_policy_evaluator.evaluate

        def _evaluate_override(*, tenant_id: str | None, action: str, context: dict | None = None):
            if action == "worker.job.claim":
                return _decision(action=action, allowed=False)
            if action == "worker.job.complete":
                return _decision(action=action, allowed=True)
            return real_evaluate(tenant_id=tenant_id, action=action, context=context)

        server._trust_policy_evaluator.evaluate = MagicMock(  # type: ignore[method-assign]
            side_effect=_evaluate_override
        )

        async with TestClient(TestServer(app)) as client:
            heartbeat = await client.post(
                f"/worker/v1/nodes/{node_id}/heartbeat",
                headers=heartbeat_headers,
                data=heartbeat_raw,
            )
            assert heartbeat.status == 400

            denied_claim = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/claim",
                headers=claim_headers,
                data=claim_raw,
            )
            assert denied_claim.status == 409

            ok_result = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/job-77/result",
                headers=result_headers,
                data=result_raw,
            )
            assert ok_result.status == 202

            listed = await client.get(
                f"/admin/tenants/{tenant_id}/workers/nodes?include_inactive=true&limit=20",
                headers=_auth_admin_headers(),
            )
            assert listed.status == 200

            missing = await client.get(
                f"/admin/tenants/{tenant_id}/workers/nodes/{node_id}",
                headers=_auth_admin_headers(),
            )
            assert missing.status == 404

            server._trust_policy_evaluator.evaluate = MagicMock(  # type: ignore[method-assign]
                return_value=_decision(action="worker.capability.update", allowed=True)
            )
            updated = await client.put(
                f"/admin/tenants/{tenant_id}/workers/nodes/{node_id}/capabilities",
                headers=_auth_admin_headers(),
                json={"capabilities": ["repo.patch", "repo.pr.open"], "explicitly_elevated": True},
            )
            assert updated.status == 200

        worker_manager.list_worker_nodes.assert_awaited_once()
        worker_manager.set_worker_capabilities.assert_awaited_once()

    async def test_worker_routes_require_tenant_admin_manager(
        self,
        mock_registry: SkillRegistry,
    ) -> None:
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=None,
            admin_actor_secret="admin-secret",
        )
        app = server.create_app()
        admin_headers = {"X-API-Secret": "skills-secret"}
        admin_headers.update(_admin_headers(signing_secret="admin-secret"))

        async with TestClient(TestServer(app)) as client:
            bootstrap = await client.post("/worker/v1/bootstrap", json={"tenant_id": "t"})
            assert bootstrap.status == 501

            register = await client.post("/worker/v1/nodes/register", json={"tenant_id": "t"})
            assert register.status == 501

            heartbeat = await client.post(
                "/worker/v1/nodes/node-1/heartbeat", json={"tenant_id": "t"}
            )
            assert heartbeat.status == 501

            claim = await client.post("/worker/v1/nodes/node-1/jobs/claim", json={"tenant_id": "t"})
            assert claim.status == 501

            result = await client.post(
                "/worker/v1/nodes/node-1/jobs/job-1/result",
                json={"tenant_id": "t"},
            )
            assert result.status == 501

            admin_list = await client.get(
                "/admin/tenants/11111111-1111-1111-1111-111111111111/workers/nodes",
                headers=admin_headers,
            )
            assert admin_list.status == 501

    async def test_worker_signature_validation_matrix(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        node_id = "node-1"
        session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        token = "token-1"
        signing_secret = "signing-secret-1"

        body = {"tenant_id": tenant_id, "required_capabilities": []}
        raw = json.dumps(body, separators=(",", ":"))
        valid_timestamp = str(int(time.time()))
        valid_session = {
            "session_id": session_id,
            "token_hash": _hash_token(token),
            "signing_secret": signing_secret,
            "status": "active",
            "health_status": "healthy",
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "revoked_at": None,
        }

        async def _post_claim(
            *,
            headers: dict[str, str],
            session_payload: dict[str, object] | None = None,
            has_caps: bool = True,
        ) -> tuple[int, dict[str, object]]:
            worker_manager.get_worker_session_auth = AsyncMock(return_value=session_payload)
            worker_manager.has_worker_capabilities = AsyncMock(return_value=has_caps)
            server = SkillsServer(
                registry=mock_registry,
                api_secret="skills-secret",
                tenant_admin_manager=worker_manager,
            )
            app = server.create_app()
            async with TestClient(TestServer(app)) as client:
                response = await client.post(
                    f"/worker/v1/nodes/{node_id}/jobs/claim",
                    headers=headers,
                    data=raw,
                )
                return response.status, await response.json()

        missing_headers_status, _ = await _post_claim(
            headers={"Content-Type": "application/json"},
            session_payload=valid_session,
        )
        assert missing_headers_status == 400

        bad_timestamp_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=raw,
            nonce="nonce-bad-ts",
            timestamp="not-an-int",
        )
        bad_timestamp_status, _ = await _post_claim(
            headers=bad_timestamp_headers,
            session_payload=valid_session,
        )
        assert bad_timestamp_status == 400

        expired_timestamp = str(int(time.time()) - 7200)
        expired_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=raw,
            nonce="nonce-expired-ts",
            timestamp=expired_timestamp,
        )
        expired_status, _ = await _post_claim(
            headers=expired_headers,
            session_payload=valid_session,
        )
        assert expired_status == 400

        no_session_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=raw,
            nonce="nonce-no-session",
            timestamp=valid_timestamp,
        )
        no_session_status, _ = await _post_claim(
            headers=no_session_headers,
            session_payload=None,
        )
        assert no_session_status == 400

        revoked_session = dict(valid_session)
        revoked_session["revoked_at"] = datetime.now(UTC)
        revoked_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=raw,
            nonce="nonce-revoked",
            timestamp=valid_timestamp,
        )
        revoked_status, _ = await _post_claim(
            headers=revoked_headers,
            session_payload=revoked_session,
        )
        assert revoked_status == 400

        empty_hash_session = dict(valid_session)
        empty_hash_session["token_hash"] = ""
        empty_hash_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=raw,
            nonce="nonce-empty-hash",
            timestamp=valid_timestamp,
        )
        empty_hash_status, _ = await _post_claim(
            headers=empty_hash_headers,
            session_payload=empty_hash_session,
        )
        assert empty_hash_status == 400

        missing_secret_session = dict(valid_session)
        missing_secret_session["signing_secret"] = ""
        missing_secret_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=raw,
            nonce="nonce-missing-secret",
            timestamp=valid_timestamp,
        )
        missing_secret_status, _ = await _post_claim(
            headers=missing_secret_headers,
            session_payload=missing_secret_session,
        )
        assert missing_secret_status == 400

        mismatched_token_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token="wrong-token",
            signing_secret=signing_secret,
            raw_body=raw,
            nonce="nonce-wrong-token",
            timestamp=valid_timestamp,
        )
        mismatched_token_status, _ = await _post_claim(
            headers=mismatched_token_headers,
            session_payload=valid_session,
        )
        assert mismatched_token_status == 400

        invalid_signature_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=raw,
            nonce="nonce-bad-signature",
            timestamp=valid_timestamp,
        )
        invalid_signature_headers["X-Worker-Signature"] = "bad-signature"
        invalid_signature_status, _ = await _post_claim(
            headers=invalid_signature_headers,
            session_payload=valid_session,
        )
        assert invalid_signature_status == 400

        replay_headers = _worker_headers(
            tenant_id=tenant_id,
            node_id=node_id,
            session_id=session_id,
            token=token,
            signing_secret=signing_secret,
            raw_body=raw,
            nonce="nonce-replay-check",
            timestamp=valid_timestamp,
        )
        worker_manager.get_worker_session_auth = AsyncMock(return_value=valid_session)
        worker_manager.has_worker_capabilities = AsyncMock(return_value=True)
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
        )
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            first = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/claim",
                headers=replay_headers,
                data=raw,
            )
            assert first.status == 200
            second = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/claim",
                headers=replay_headers,
                data=raw,
            )
            assert second.status == 409

    async def test_worker_and_admin_error_branches(
        self,
        mock_registry: SkillRegistry,
        worker_manager: MagicMock,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        node_id = "node-1"
        session_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        token = "token-1"
        signing_secret = "signing-secret-1"
        session_payload = {
            "session_id": session_id,
            "token_hash": _hash_token(token),
            "signing_secret": signing_secret,
            "status": "active",
            "health_status": "healthy",
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "revoked_at": None,
        }
        worker_manager.get_worker_session_auth = AsyncMock(return_value=session_payload)

        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=worker_manager,
            admin_actor_secret="admin-secret",
        )
        app = server.create_app()

        register_body = {
            "tenant_id": tenant_id,
            "node_id": node_id,
            "capabilities": ["repo.patch"],
            "rotate_credentials": False,
        }
        register_raw = json.dumps(register_body, separators=(",", ":"))

        heartbeat_body = {"tenant_id": tenant_id, "health_status": "healthy"}
        heartbeat_raw = json.dumps(heartbeat_body, separators=(",", ":"))

        result_body = {"tenant_id": tenant_id, "status": "succeeded"}
        result_raw = json.dumps(result_body, separators=(",", ":"))

        def _fresh_admin_headers() -> dict[str, str]:
            headers = {"X-API-Secret": "skills-secret"}
            headers.update(_admin_headers(signing_secret="admin-secret"))
            return headers

        async with TestClient(TestServer(app)) as client:
            first_register = await client.post(
                "/worker/v1/nodes/register",
                headers=_worker_headers(
                    tenant_id=tenant_id,
                    node_id=node_id,
                    session_id=session_id,
                    token=token,
                    signing_secret=signing_secret,
                    raw_body=register_raw,
                    nonce="nonce-register-replay",
                ),
                data=register_raw,
            )
            assert first_register.status == 200

            replay_register = await client.post(
                "/worker/v1/nodes/register",
                headers=_worker_headers(
                    tenant_id=tenant_id,
                    node_id=node_id,
                    session_id=session_id,
                    token=token,
                    signing_secret=signing_secret,
                    raw_body=register_raw,
                    nonce="nonce-register-replay",
                ),
                data=register_raw,
            )
            assert replay_register.status == 409

            worker_manager.register_worker_node = AsyncMock(side_effect=Exception("boom"))
            register_error = await client.post(
                "/worker/v1/nodes/register",
                headers=_worker_headers(
                    tenant_id=tenant_id,
                    node_id=node_id,
                    session_id=session_id,
                    token=token,
                    signing_secret=signing_secret,
                    raw_body=register_raw,
                    nonce="nonce-register-error",
                ),
                data=register_raw,
            )
            assert register_error.status == 502

            worker_manager.register_worker_node = AsyncMock(
                return_value={
                    "tenant_id": tenant_id,
                    "node_id": node_id,
                    "status": "active",
                    "health_status": "healthy",
                    "capabilities": ["repo.patch"],
                }
            )
            worker_manager.heartbeat_worker_node = AsyncMock(
                return_value={
                    "tenant_id": tenant_id,
                    "node_id": node_id,
                    "status": "active",
                    "health_status": "healthy",
                    "metadata": {},
                }
            )
            worker_manager.record_worker_job_event = AsyncMock(side_effect=RuntimeError("replay"))
            heartbeat_runtime = await client.post(
                f"/worker/v1/nodes/{node_id}/heartbeat",
                headers=_worker_headers(
                    tenant_id=tenant_id,
                    node_id=node_id,
                    session_id=session_id,
                    token=token,
                    signing_secret=signing_secret,
                    raw_body=heartbeat_raw,
                    nonce="nonce-heartbeat-runtime",
                ),
                data=heartbeat_raw,
            )
            assert heartbeat_runtime.status == 409

            worker_manager.record_worker_job_event = AsyncMock(return_value={"event_id": 1})
            worker_manager.heartbeat_worker_node = AsyncMock(side_effect=Exception("boom"))
            heartbeat_error = await client.post(
                f"/worker/v1/nodes/{node_id}/heartbeat",
                headers=_worker_headers(
                    tenant_id=tenant_id,
                    node_id=node_id,
                    session_id=session_id,
                    token=token,
                    signing_secret=signing_secret,
                    raw_body=heartbeat_raw,
                    nonce="nonce-heartbeat-error",
                ),
                data=heartbeat_raw,
            )
            assert heartbeat_error.status == 502

            worker_manager.heartbeat_worker_node = AsyncMock(
                return_value={
                    "tenant_id": tenant_id,
                    "node_id": node_id,
                    "status": "active",
                    "health_status": "healthy",
                    "metadata": {},
                }
            )
            worker_manager.has_worker_capabilities = AsyncMock(return_value=True)
            worker_manager.record_worker_job_event = AsyncMock(side_effect=RuntimeError("replay"))
            result_runtime = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/job-1/result",
                headers=_worker_headers(
                    tenant_id=tenant_id,
                    node_id=node_id,
                    session_id=session_id,
                    token=token,
                    signing_secret=signing_secret,
                    raw_body=result_raw,
                    nonce="nonce-result-runtime",
                ),
                data=result_raw,
            )
            assert result_runtime.status == 202
            runtime_payload = await result_runtime.json()
            assert runtime_payload["idempotent"] is True

            worker_manager.record_worker_job_event = AsyncMock(side_effect=Exception("boom"))
            result_error = await client.post(
                f"/worker/v1/nodes/{node_id}/jobs/job-2/result",
                headers=_worker_headers(
                    tenant_id=tenant_id,
                    node_id=node_id,
                    session_id=session_id,
                    token=token,
                    signing_secret=signing_secret,
                    raw_body=result_raw,
                    nonce="nonce-result-error",
                ),
                data=result_raw,
            )
            assert result_error.status == 502

            invalid_limit = await client.get(
                f"/admin/tenants/{tenant_id}/workers/nodes?limit=bad",
                headers=_fresh_admin_headers(),
            )
            assert invalid_limit.status == 400

            worker_manager.get_worker_node = AsyncMock(side_effect=ValueError("bad node"))
            get_error = await client.get(
                f"/admin/tenants/{tenant_id}/workers/nodes/{node_id}",
                headers=_fresh_admin_headers(),
            )
            assert get_error.status == 400

            put_invalid_json = await client.put(
                f"/admin/tenants/{tenant_id}/workers/nodes/{node_id}/capabilities",
                headers={**_fresh_admin_headers(), "Content-Type": "application/json"},
                data="{",
            )
            assert put_invalid_json.status == 400
