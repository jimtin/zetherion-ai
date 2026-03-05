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
    encoded = base64.urlsafe_b64encode(json.dumps(payload).encode("utf-8")).decode("ascii").rstrip(
        "="
    )
    signature = hmac.new(signing_secret.encode("utf-8"), encoded.encode("utf-8"), hashlib.sha256)
    return {
        "X-Admin-Actor": encoded,
        "X-Admin-Signature": signature.hexdigest(),
    }


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
    mgr.record_worker_job_event = AsyncMock(return_value={"event_id": 1})
    mgr.list_worker_nodes = AsyncMock(return_value=[])
    mgr.get_worker_node = AsyncMock(return_value=None)
    mgr.set_worker_capabilities = AsyncMock(return_value={})
    return mgr


class TestSkillsWorkerControlAPI:
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
