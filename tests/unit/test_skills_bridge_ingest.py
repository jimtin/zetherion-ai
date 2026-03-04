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
    server = SkillsServer(registry=mock_registry, api_secret="skills-secret")
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
        server = SkillsServer(registry=mock_registry, api_secret="skills-secret")

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
