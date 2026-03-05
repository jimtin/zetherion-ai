"""Focused tests for bridge messaging ingest endpoint on SkillsServer."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock, MagicMock

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
async def bridge_client(monkeypatch: pytest.MonkeyPatch, mock_registry: SkillRegistry):
    monkeypatch.setenv("WHATSAPP_BRIDGE_SIGNING_SECRET", "bridge-signing-secret")
    tenant_admin_manager = MagicMock()
    tenant_admin_manager.get_secret_cached = MagicMock(return_value="")
    tenant_admin_manager.is_messaging_chat_allowed = AsyncMock(return_value=True)
    tenant_admin_manager.ingest_messaging_message = AsyncMock(
        return_value={"message_id": "99999999-9999-9999-9999-999999999999"}
    )
    tenant_admin_manager.purge_expired_messaging_messages = AsyncMock(return_value=0)
    tenant_admin_manager.record_security_event = AsyncMock(return_value={"event_id": 1})
    server = SkillsServer(
        registry=mock_registry,
        api_secret="skills-secret",
        tenant_admin_manager=tenant_admin_manager,
    )
    app = server.create_app()
    async with TestClient(TestServer(app)) as client:
        yield client


def _sign(*, tenant_id: str, timestamp: str, nonce: str, raw_body: str, secret: str) -> str:
    canonical = f"{tenant_id}.{timestamp}.{nonce}.{raw_body}"
    return hmac.new(secret.encode("utf-8"), canonical.encode("utf-8"), hashlib.sha256).hexdigest()


class TestSkillsBridgeIngest:
    async def test_bridge_ingest_requires_api_secret(self, bridge_client: TestClient) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        raw_body = json.dumps({"event_type": "whatsapp.message.send", "chat_id": "chat-1"})
        resp = await bridge_client.post(
            f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
            data=raw_body,
            headers={"content-type": "application/json"},
        )
        assert resp.status == 401

    async def test_bridge_ingest_rejects_missing_signature_headers(
        self,
        bridge_client: TestClient,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        raw_body = json.dumps({"event_type": "whatsapp.message.send", "chat_id": "chat-1"})
        resp = await bridge_client.post(
            f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
            data=raw_body,
            headers={
                "content-type": "application/json",
                "x-api-secret": "skills-secret",
            },
        )
        assert resp.status == 401

    async def test_bridge_ingest_accepts_valid_signed_event(
        self,
        bridge_client: TestClient,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        timestamp = str(int(time.time()))
        nonce = "nonce-1"
        raw_body = json.dumps({"event_type": "whatsapp.message.send", "chat_id": "chat-1"})
        signature = _sign(
            tenant_id=tenant_id,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=raw_body,
            secret="bridge-signing-secret",
        )
        resp = await bridge_client.post(
            f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
            data=raw_body,
            headers={
                "content-type": "application/json",
                "x-api-secret": "skills-secret",
                "x-bridge-timestamp": timestamp,
                "x-bridge-nonce": nonce,
                "x-bridge-signature": signature,
            },
        )
        assert resp.status == 202
        payload = await resp.json()
        assert payload["accepted"] is True
        assert payload["event_type"] == "whatsapp.message.send"

    async def test_bridge_ingest_rejects_replayed_nonce(self, bridge_client: TestClient) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        timestamp = str(int(time.time()))
        nonce = "nonce-replay"
        raw_body = json.dumps({"event_type": "whatsapp.message.send", "chat_id": "chat-1"})
        signature = _sign(
            tenant_id=tenant_id,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=raw_body,
            secret="bridge-signing-secret",
        )
        headers = {
            "content-type": "application/json",
            "x-api-secret": "skills-secret",
            "x-bridge-timestamp": timestamp,
            "x-bridge-nonce": nonce,
            "x-bridge-signature": signature,
        }

        first = await bridge_client.post(
            f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
            data=raw_body,
            headers=headers,
        )
        assert first.status == 202

        second = await bridge_client.post(
            f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
            data=raw_body,
            headers=headers,
        )
        assert second.status == 409

    async def test_bridge_ingest_respects_trust_policy_denial(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_registry: SkillRegistry,
    ) -> None:
        monkeypatch.setenv("WHATSAPP_BRIDGE_SIGNING_SECRET", "bridge-signing-secret")
        tenant_admin_manager = MagicMock()
        tenant_admin_manager.get_secret_cached = MagicMock(return_value="")
        tenant_admin_manager.is_messaging_chat_allowed = AsyncMock(return_value=True)
        tenant_admin_manager.ingest_messaging_message = AsyncMock(
            return_value={"message_id": "99999999-9999-9999-9999-999999999999"}
        )
        tenant_admin_manager.purge_expired_messaging_messages = AsyncMock(return_value=0)
        tenant_admin_manager.record_security_event = AsyncMock(return_value={"event_id": 1})
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=tenant_admin_manager,
        )

        class _DenyAll:
            @staticmethod
            def evaluate(**_kwargs):
                return TrustPolicyDecision(
                    action="messaging.ingest",
                    action_class=TrustActionClass.SENSITIVE,
                    outcome=TrustDecisionOutcome.DENY,
                    status=423,
                    code="AI_KILL_SWITCH_ACTIVE",
                    message="Action is disabled by kill switch",
                    details={"kill_switch": "messaging_ingestion_kill_switch"},
                )

        server._trust_policy_evaluator = _DenyAll()
        app = server.create_app()

        tenant_id = "11111111-1111-1111-1111-111111111111"
        raw_body = json.dumps({"event_type": "whatsapp.message.send", "chat_id": "chat-1"})
        timestamp = str(int(time.time()))
        nonce = "nonce-kill-switch"
        signature = _sign(
            tenant_id=tenant_id,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=raw_body,
            secret="bridge-signing-secret",
        )

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
                data=raw_body,
                headers={
                    "content-type": "application/json",
                    "x-api-secret": "skills-secret",
                    "x-bridge-timestamp": timestamp,
                    "x-bridge-nonce": nonce,
                    "x-bridge-signature": signature,
                },
            )
            assert resp.status == 423
            payload = await resp.json()
            assert payload["code"] == "AI_KILL_SWITCH_ACTIVE"

    async def test_bridge_ingest_rejects_non_allowlisted_chat(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_registry: SkillRegistry,
    ) -> None:
        monkeypatch.setenv("WHATSAPP_BRIDGE_SIGNING_SECRET", "bridge-signing-secret")
        tenant_admin_manager = MagicMock()
        tenant_admin_manager.get_secret_cached = MagicMock(return_value="")
        tenant_admin_manager.is_messaging_chat_allowed = AsyncMock(return_value=False)
        tenant_admin_manager.ingest_messaging_message = AsyncMock(
            return_value={"message_id": "99999999-9999-9999-9999-999999999999"}
        )
        tenant_admin_manager.purge_expired_messaging_messages = AsyncMock(return_value=0)
        tenant_admin_manager.record_security_event = AsyncMock(return_value={"event_id": 1})
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=tenant_admin_manager,
        )
        app = server.create_app()

        tenant_id = "11111111-1111-1111-1111-111111111111"
        raw_body = json.dumps({"event_type": "whatsapp.message.send", "chat_id": "chat-1"})
        timestamp = str(int(time.time()))
        nonce = "nonce-not-allowlisted"
        signature = _sign(
            tenant_id=tenant_id,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=raw_body,
            secret="bridge-signing-secret",
        )

        async with TestClient(TestServer(app)) as client:
            resp = await client.post(
                f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
                data=raw_body,
                headers={
                    "content-type": "application/json",
                    "x-api-secret": "skills-secret",
                    "x-bridge-timestamp": timestamp,
                    "x-bridge-nonce": nonce,
                    "x-bridge-signature": signature,
                },
            )
            assert resp.status == 403
            payload = await resp.json()
            assert payload["code"] == "AI_MESSAGING_CHAT_NOT_ALLOWLISTED"

    async def test_admin_tenant_ingest_alias_accepts_signed_request_without_actor(
        self,
        bridge_client: TestClient,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        timestamp = str(int(time.time()))
        nonce = "nonce-admin-alias"
        raw_body = json.dumps({"event_type": "whatsapp.message.send", "chat_id": "chat-1"})
        signature = _sign(
            tenant_id=tenant_id,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=raw_body,
            secret="bridge-signing-secret",
        )
        resp = await bridge_client.post(
            f"/admin/tenants/{tenant_id}/messaging/ingest",
            data=raw_body,
            headers={
                "content-type": "application/json",
                "x-api-secret": "skills-secret",
                "x-bridge-timestamp": timestamp,
                "x-bridge-nonce": nonce,
                "x-bridge-signature": signature,
            },
        )
        assert resp.status == 202

    async def test_bridge_ingest_missing_event_or_chat_validation(
        self,
        bridge_client: TestClient,
    ) -> None:
        tenant_id = "11111111-1111-1111-1111-111111111111"
        timestamp = str(int(time.time()))

        def _headers(nonce: str, raw_body: str) -> dict[str, str]:
            signature = _sign(
                tenant_id=tenant_id,
                timestamp=timestamp,
                nonce=nonce,
                raw_body=raw_body,
                secret="bridge-signing-secret",
            )
            return {
                "content-type": "application/json",
                "x-api-secret": "skills-secret",
                "x-bridge-timestamp": timestamp,
                "x-bridge-nonce": nonce,
                "x-bridge-signature": signature,
            }

        missing_event_body = json.dumps({"chat_id": "chat-1"})
        missing_event = await bridge_client.post(
            f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
            data=missing_event_body,
            headers=_headers("nonce-missing-event", missing_event_body),
        )
        assert missing_event.status == 400

        missing_chat_body = json.dumps({"event_type": "whatsapp.message.inbound"})
        missing_chat = await bridge_client.post(
            f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
            data=missing_chat_body,
            headers=_headers("nonce-missing-chat", missing_chat_body),
        )
        assert missing_chat.status == 400

    async def test_bridge_ingest_metadata_validation_and_observed_at_normalization(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_registry: SkillRegistry,
    ) -> None:
        monkeypatch.setenv("WHATSAPP_BRIDGE_SIGNING_SECRET", "bridge-signing-secret")
        tenant_admin_manager = MagicMock()
        tenant_admin_manager.get_secret_cached = MagicMock(return_value="")
        tenant_admin_manager.is_messaging_chat_allowed = AsyncMock(return_value=True)
        tenant_admin_manager.purge_expired_messaging_messages = AsyncMock(return_value=0)
        tenant_admin_manager.ingest_messaging_message = AsyncMock(
            return_value={"message_id": "99999999-9999-9999-9999-999999999999"}
        )
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=tenant_admin_manager,
        )
        app = server.create_app()
        tenant_id = "11111111-1111-1111-1111-111111111111"
        timestamp = str(int(time.time()))

        async with TestClient(TestServer(app)) as client:
            bad_meta_raw = json.dumps(
                {
                    "event_type": "whatsapp.message.inbound",
                    "chat_id": "chat-1",
                    "metadata": "bad-metadata",
                }
            )
            bad_meta_sig = _sign(
                tenant_id=tenant_id,
                timestamp=timestamp,
                nonce="nonce-bad-meta",
                raw_body=bad_meta_raw,
                secret="bridge-signing-secret",
            )
            bad_meta = await client.post(
                f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
                data=bad_meta_raw,
                headers={
                    "content-type": "application/json",
                    "x-api-secret": "skills-secret",
                    "x-bridge-timestamp": timestamp,
                    "x-bridge-nonce": "nonce-bad-meta",
                    "x-bridge-signature": bad_meta_sig,
                },
            )
            assert bad_meta.status == 400

            good_raw = json.dumps(
                {
                    "event_type": "whatsapp.message.inbound",
                    "chat_id": "chat-1",
                    "observed_at": "2026-03-05T00:00:00",
                    "metadata": {"source": "bridge"},
                }
            )
            good_sig = _sign(
                tenant_id=tenant_id,
                timestamp=timestamp,
                nonce="nonce-good-observed",
                raw_body=good_raw,
                secret="bridge-signing-secret",
            )
            good = await client.post(
                f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
                data=good_raw,
                headers={
                    "content-type": "application/json",
                    "x-api-secret": "skills-secret",
                    "x-bridge-timestamp": timestamp,
                    "x-bridge-nonce": "nonce-good-observed",
                    "x-bridge-signature": good_sig,
                },
            )
            assert good.status == 202

    async def test_bridge_ingest_handles_manager_validation_and_store_failures(
        self,
        monkeypatch: pytest.MonkeyPatch,
        mock_registry: SkillRegistry,
    ) -> None:
        monkeypatch.setenv("WHATSAPP_BRIDGE_SIGNING_SECRET", "bridge-signing-secret")
        tenant_admin_manager = MagicMock()
        tenant_admin_manager.get_secret_cached = MagicMock(return_value="")
        tenant_admin_manager.purge_expired_messaging_messages = AsyncMock(return_value=0)
        tenant_admin_manager.is_messaging_chat_allowed = AsyncMock(
            side_effect=ValueError("bad provider")
        )
        tenant_admin_manager.ingest_messaging_message = AsyncMock(
            return_value={"message_id": "99999999-9999-9999-9999-999999999999"}
        )
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=tenant_admin_manager,
        )
        app = server.create_app()

        tenant_id = "11111111-1111-1111-1111-111111111111"
        raw_body = json.dumps({"event_type": "whatsapp.message.send", "chat_id": "chat-1"})
        timestamp = str(int(time.time()))
        nonce = "nonce-provider-error"
        signature = _sign(
            tenant_id=tenant_id,
            timestamp=timestamp,
            nonce=nonce,
            raw_body=raw_body,
            secret="bridge-signing-secret",
        )
        headers = {
            "content-type": "application/json",
            "x-api-secret": "skills-secret",
            "x-bridge-timestamp": timestamp,
            "x-bridge-nonce": nonce,
            "x-bridge-signature": signature,
        }

        async with TestClient(TestServer(app)) as client:
            provider_error = await client.post(
                f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
                data=raw_body,
                headers=headers,
            )
            assert provider_error.status == 400

        tenant_admin_manager.is_messaging_chat_allowed = AsyncMock(return_value=True)
        tenant_admin_manager.ingest_messaging_message = AsyncMock(side_effect=RuntimeError("boom"))
        server = SkillsServer(
            registry=mock_registry,
            api_secret="skills-secret",
            tenant_admin_manager=tenant_admin_manager,
        )
        app = server.create_app()
        nonce_2 = "nonce-store-error"
        signature_2 = _sign(
            tenant_id=tenant_id,
            timestamp=timestamp,
            nonce=nonce_2,
            raw_body=raw_body,
            secret="bridge-signing-secret",
        )
        headers_2 = {
            "content-type": "application/json",
            "x-api-secret": "skills-secret",
            "x-bridge-timestamp": timestamp,
            "x-bridge-nonce": nonce_2,
            "x-bridge-signature": signature_2,
        }
        async with TestClient(TestServer(app)) as client:
            store_error = await client.post(
                f"/bridge/v1/tenants/{tenant_id}/messaging/ingest",
                data=raw_body,
                headers=headers_2,
            )
            assert store_error.status == 502
