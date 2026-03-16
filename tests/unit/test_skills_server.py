"""Tests for skills server."""

import asyncio
import base64
import hashlib
import hmac
import json
import os
from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.announcements.policy import AnnouncementPolicyDecision
from zetherion_ai.announcements.storage import (
    AnnouncementDelivery,
    AnnouncementPreferencePatch,
    AnnouncementReceipt,
    AnnouncementSeverity,
    AnnouncementUserPreferences,
)
from zetherion_ai.skills.base import (
    HeartbeatAction,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import (
    SkillsServer,
    _build_google_oauth_authorize_handler,
    _build_google_oauth_handler,
    _extract_json_object,
    _isoformat_if_datetime,
    _resolve_google_oauth,
    _resolve_updater_secret,
)


@pytest.fixture
def mock_registry():
    """Create a mock SkillRegistry with sensible defaults."""
    registry = MagicMock(spec=SkillRegistry)
    registry.list_ready_skills.return_value = []
    registry.skill_count = 0
    registry.handle_request = AsyncMock(
        return_value=SkillResponse(request_id=uuid4(), success=True, message="ok")
    )
    registry.run_heartbeat = AsyncMock(return_value=[])
    registry.list_skills.return_value = []
    registry.get_skill.return_value = None
    registry.get_status_summary.return_value = {"status": "ok"}
    registry.get_system_prompt_fragments.return_value = ["fragment1"]
    registry.list_intents.return_value = {"intent1": "skill1"}
    return registry


def _admin_headers(
    *,
    signing_secret: str,
    actor_sub: str = "operator-1",
    request_id: str = "req-test",
    change_ticket_id: str | None = None,
) -> dict[str, str]:
    payload = {
        "actor_sub": actor_sub,
        "actor_roles": ["operator"],
        "request_id": request_id,
        "timestamp": datetime.now(UTC).isoformat(),
        "nonce": uuid4().hex,
        "change_ticket_id": change_ticket_id,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    encoded = base64.urlsafe_b64encode(canonical.encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(
        signing_secret.encode("utf-8"),
        encoded.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return {"X-Admin-Actor": encoded, "X-Admin-Signature": signature}


class TestSkillsServerAuth:
    """Tests for SkillsServer authentication logic."""

    def test_check_auth_no_secret(self, mock_registry):
        """Server without secret should always authenticate."""
        server = SkillsServer(registry=mock_registry)
        mock_request = MagicMock()
        assert server._check_auth(mock_request) is True

    def test_check_auth_valid_secret(self, mock_registry):
        """Server with secret should authenticate matching header."""
        server = SkillsServer(registry=mock_registry, api_secret="my-secret")
        mock_request = MagicMock()
        mock_request.headers.get.return_value = "my-secret"
        assert server._check_auth(mock_request) is True

    def test_check_auth_invalid_secret(self, mock_registry):
        """Server with secret should reject non-matching header."""
        server = SkillsServer(registry=mock_registry, api_secret="my-secret")
        mock_request = MagicMock()
        mock_request.headers.get.return_value = "wrong-secret"
        assert server._check_auth(mock_request) is False


class TestSkillsServerHelpers:
    """Focused coverage for helper parsing and config fallback behavior."""

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("", {}),
            ("[]", {}),
            ('{"ok": true}', {"ok": True}),
            ('noise before {"k": 1} noise after', {"k": 1}),
            ("noise before {bad} noise after", {}),
            ("noise without braces", {}),
            ("broken {json", {}),
        ],
    )
    def test_extract_json_object(self, raw, expected):
        assert _extract_json_object(raw) == expected

    def test_isoformat_if_datetime(self):
        now = datetime.now(UTC)
        assert _isoformat_if_datetime(now) == now.isoformat()
        assert _isoformat_if_datetime("not-a-datetime") == "not-a-datetime"

    @pytest.mark.asyncio
    async def test_read_json_object_success(self):
        request = MagicMock()
        request.json = AsyncMock(return_value={"ok": True})
        assert await SkillsServer._read_json_object(request) == {"ok": True}

    @pytest.mark.asyncio
    async def test_read_json_object_rejects_non_object(self):
        request = MagicMock()
        request.json = AsyncMock(return_value=["not", "an", "object"])
        with pytest.raises(ValueError, match="must be an object"):
            await SkillsServer._read_json_object(request)

    @pytest.mark.asyncio
    async def test_read_json_object_invalid_json(self):
        request = MagicMock()
        request.json = AsyncMock(side_effect=json.JSONDecodeError("bad", "x", 0))
        with pytest.raises(ValueError, match="Invalid JSON body"):
            await SkillsServer._read_json_object(request)

    def test_parse_int_and_errors(self):
        assert SkillsServer._parse_int("12", "limit") == 12
        with pytest.raises(ValueError, match="Invalid integer for 'limit'"):
            SkillsServer._parse_int("abc", "limit")

    def test_parse_bool_paths(self):
        assert SkillsServer._parse_bool(None, "flag", default=False) is False
        assert SkillsServer._parse_bool(True, "flag") is True
        assert SkillsServer._parse_bool(0, "flag") is False
        assert SkillsServer._parse_bool(1, "flag") is True
        assert SkillsServer._parse_bool("yes", "flag") is True
        assert SkillsServer._parse_bool("off", "flag") is False
        with pytest.raises(ValueError, match="Invalid boolean for 'flag'"):
            SkillsServer._parse_bool("maybe", "flag")
        with pytest.raises(ValueError, match="Invalid boolean for 'flag'"):
            SkillsServer._parse_bool([], "flag")

    def test_parse_string_list_paths(self):
        assert SkillsServer._parse_string_list(None, "caps") == []
        assert SkillsServer._parse_string_list([" a ", "", "b"], "caps") == ["a", "b"]
        with pytest.raises(ValueError, match="caps must be an array of strings"):
            SkillsServer._parse_string_list("not-a-list", "caps")
        with pytest.raises(ValueError, match="caps must be an array of strings"):
            SkillsServer._parse_string_list(["ok", 42], "caps")

    def test_init_env_fallbacks_for_invalid_values(self, mock_registry):
        with patch.dict(
            os.environ,
            {
                "WORKER_SESSION_TTL_SECONDS": "invalid",
                "MESSAGING_TTL_CLEANUP_INTERVAL_SECONDS": "invalid",
            },
            clear=False,
        ):
            server = SkillsServer(registry=mock_registry)
        assert server._worker_session_ttl_seconds == 86400
        assert server._messaging_ttl_cleanup_interval_seconds == 300

    @pytest.mark.asyncio
    async def test_messaging_ttl_cleanup_loop_handles_purge_and_cancel(self, mock_registry):
        server = SkillsServer(registry=mock_registry, tenant_admin_manager=AsyncMock())
        server._tenant_admin_manager.purge_expired_messaging_messages.return_value = 5
        server._tenant_admin_manager.purge_expired_worker_messaging_grants = AsyncMock(
            return_value=2
        )
        with (
            patch(
                "zetherion_ai.skills.server.asyncio.sleep",
                side_effect=[None, asyncio.CancelledError()],
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await server._messaging_ttl_cleanup_loop()
        server._tenant_admin_manager.purge_expired_messaging_messages.assert_awaited_once_with(
            limit=2000
        )
        server._tenant_admin_manager.purge_expired_worker_messaging_grants.assert_awaited_once_with(
            limit=2000
        )

    @pytest.mark.asyncio
    async def test_messaging_ttl_cleanup_loop_ignores_purge_errors(self, mock_registry):
        server = SkillsServer(registry=mock_registry, tenant_admin_manager=AsyncMock())
        server._tenant_admin_manager.purge_expired_messaging_messages.side_effect = RuntimeError(
            "boom"
        )
        with (
            patch(
                "zetherion_ai.skills.server.asyncio.sleep",
                side_effect=[None, asyncio.CancelledError()],
            ),
            pytest.raises(asyncio.CancelledError),
        ):
            await server._messaging_ttl_cleanup_loop()


class TestSkillsServerEndpoints:
    """Tests for SkillsServer HTTP endpoints using aiohttp TestClient."""

    @pytest.fixture
    async def client(self, mock_registry):
        """Create a test client without authentication."""
        server = SkillsServer(registry=mock_registry)
        app = server.create_app()
        async with TestClient(TestServer(app)) as test_client:
            yield test_client

    @pytest.fixture
    async def auth_client(self, mock_registry):
        """Create a test client with authentication enabled."""
        server = SkillsServer(registry=mock_registry, api_secret="test-secret")
        app = server.create_app()
        async with TestClient(TestServer(app)) as test_client:
            yield test_client

    @pytest.fixture
    async def oauth_client(self, mock_registry):
        """Create client with OAuth handler configured."""
        handler = AsyncMock(return_value={"ok": True, "provider": "google"})
        server = SkillsServer(
            registry=mock_registry,
            api_secret="test-secret",
            oauth_handlers={"google": handler},
        )
        app = server.create_app()
        async with TestClient(TestServer(app)) as test_client:
            yield test_client, handler

    @pytest.fixture
    async def oauth_authorize_client(self, mock_registry):
        """Create client with OAuth authorize handler configured."""
        authorizer = AsyncMock(
            return_value={"ok": True, "provider": "google", "auth_url": "https://example.test/auth"}
        )
        server = SkillsServer(
            registry=mock_registry,
            api_secret="test-secret",
            oauth_authorizers={"google": authorizer},
        )
        app = server.create_app()
        async with TestClient(TestServer(app)) as test_client:
            yield test_client, authorizer

    async def test_health_endpoint(self, client, mock_registry):
        """GET /health should return 200 with healthy status."""
        mock_registry.list_ready_skills.return_value = []
        mock_registry.skill_count = 2

        resp = await client.get("/health")
        assert resp.status == 200

        data = await resp.json()
        assert data["status"] == "healthy"
        assert data["skills_ready"] == 0
        assert data["skills_total"] == 2

    async def test_health_endpoint_bypasses_auth(self, auth_client, mock_registry):
        """GET /health should work without auth header even when api_secret is set."""
        mock_registry.list_ready_skills.return_value = []
        mock_registry.skill_count = 0

        resp = await auth_client.get("/health")
        assert resp.status == 200

        data = await resp.json()
        assert data["status"] == "healthy"

    async def test_internal_runtime_health_endpoint(self, mock_registry):
        """GET /internal/runtime/health should report runtime domains."""
        fake_pool = object()
        bot_status = {
            "service_name": "discord_bot",
            "status": "healthy",
            "summary": "Discord bot is connected and ready.",
            "updated_at": datetime.now(UTC).isoformat(),
            "release_revision": "abc123",
            "details": {"guild_count": 2},
        }
        dispatcher_status = {
            "service_name": "announcement_dispatcher",
            "status": "healthy",
            "summary": "Announcement dispatcher is running.",
            "updated_at": datetime.now(UTC).isoformat(),
            "release_revision": "abc123",
            "details": {"running": True},
        }
        fake_store = SimpleNamespace(
            get_status=AsyncMock(
                side_effect=lambda service_name: (
                    bot_status
                    if service_name == "discord_bot"
                    else (
                        dispatcher_status
                        if service_name == "announcement_dispatcher"
                        else None
                    )
                )
            )
        )
        server = SkillsServer(
            registry=mock_registry,
            api_secret="test-secret",
            user_manager=SimpleNamespace(_pool=fake_pool),
        )
        app = server.create_app()

        with patch("zetherion_ai.skills.server.QueueStorage") as mock_queue_storage:
            queue_storage = mock_queue_storage.return_value
            queue_storage.get_status_counts = AsyncMock(
                return_value={"queued": 1, "processing": 0, "dead": 0}
            )
            queue_storage.count_stale_processing = AsyncMock(return_value=0)
            server._get_runtime_status_store = AsyncMock(return_value=fake_store)  # type: ignore[method-assign]
            server._runtime_external_services_domain = AsyncMock(  # type: ignore[method-assign]
                return_value={
                    "key": "external_services",
                    "label": "External Services",
                    "status": "healthy",
                    "summary": "Required external service dependencies are reachable.",
                    "details": {"available_collections": ["user_profiles"]},
                    "incident_type": None,
                }
            )

            async with TestClient(TestServer(app)) as client:
                resp = await client.get(
                    "/internal/runtime/health",
                    headers={"X-API-Secret": "test-secret"},
                )
                assert resp.status == 200
                data = await resp.json()

        assert data["summary"]["blocker_count"] == 0
        keys = [domain["key"] for domain in data["domains"]]
        assert "skills" in keys
        assert "message_queue" in keys
        assert "discord_bot" in keys
        assert "announcement_dispatcher" in keys
        assert "external_services" in keys
        assert "release_verification" in keys

    async def test_handle_request_success(self, client, mock_registry):
        """POST /handle should return 200 with skill response on success."""
        request_id = uuid4()
        mock_registry.handle_request = AsyncMock(
            return_value=SkillResponse(
                request_id=request_id,
                success=True,
                message="Handled",
                data={"result": "done"},
            )
        )

        skill_request = SkillRequest(
            user_id="user1",
            intent="test_intent",
            message="hello",
        )

        resp = await client.post("/handle", json=skill_request.to_dict())
        assert resp.status == 200

        data = await resp.json()
        assert data["success"] is True
        assert data["message"] == "Handled"
        assert data["request_id"] == str(request_id)

    async def test_handle_request_error(self, client, mock_registry):
        """POST /handle should return 500 when registry raises an error."""
        mock_registry.handle_request = AsyncMock(side_effect=RuntimeError("skill exploded"))

        skill_request = SkillRequest(
            user_id="user1",
            intent="test_intent",
            message="hello",
        )

        resp = await client.post("/handle", json=skill_request.to_dict())
        assert resp.status == 500

        data = await resp.json()
        assert data["error"] == "Internal server error"

    async def test_heartbeat_success(self, client, mock_registry):
        """POST /heartbeat should return 200 with actions list."""
        mock_registry.run_heartbeat = AsyncMock(
            return_value=[
                HeartbeatAction(
                    skill_name="task_manager",
                    action_type="reminder",
                    user_id="user1",
                    data={"task": "Buy milk"},
                    priority=5,
                ),
            ]
        )

        resp = await client.post("/heartbeat", json={"user_ids": ["user1"]})
        assert resp.status == 200

        data = await resp.json()
        assert "actions" in data
        assert len(data["actions"]) == 1
        assert data["actions"][0]["skill_name"] == "task_manager"
        assert data["actions"][0]["action_type"] == "reminder"

    async def test_heartbeat_error(self, client, mock_registry):
        """POST /heartbeat should return 500 when registry raises an error."""
        mock_registry.run_heartbeat = AsyncMock(side_effect=RuntimeError("heartbeat boom"))

        resp = await client.post("/heartbeat", json={"user_ids": ["user1"]})
        assert resp.status == 500

        data = await resp.json()
        assert data["error"] == "Internal server error"

    async def test_list_skills(self, client, mock_registry):
        """GET /skills should return 200 with skills list."""
        mock_registry.list_skills.return_value = [
            SkillMetadata(
                name="task_manager",
                description="Manage tasks",
                version="1.0.0",
            ),
            SkillMetadata(
                name="calendar",
                description="Calendar events",
                version="2.0.0",
            ),
        ]

        resp = await client.get("/skills")
        assert resp.status == 200

        data = await resp.json()
        assert "skills" in data
        assert len(data["skills"]) == 2
        assert data["skills"][0]["name"] == "task_manager"
        assert data["skills"][1]["name"] == "calendar"

    async def test_get_skill_found(self, client, mock_registry):
        """GET /skills/{name} should return 200 with metadata when skill exists."""
        mock_skill = MagicMock()
        mock_skill.metadata = SkillMetadata(
            name="calendar",
            description="Calendar events",
            version="2.0.0",
            intents=["create_event", "list_events"],
        )
        mock_registry.get_skill.return_value = mock_skill

        resp = await client.get("/skills/calendar")
        assert resp.status == 200

        data = await resp.json()
        assert data["name"] == "calendar"
        assert data["version"] == "2.0.0"
        assert "create_event" in data["intents"]

    async def test_get_skill_not_found(self, client, mock_registry):
        """GET /skills/{name} should return 404 when skill does not exist."""
        mock_registry.get_skill.return_value = None

        resp = await client.get("/skills/unknown")
        assert resp.status == 404

        data = await resp.json()
        assert "error" in data
        assert "not found" in data["error"].lower()

    async def test_status_endpoint(self, client, mock_registry):
        """GET /status should return 200 with status summary."""
        mock_registry.get_status_summary.return_value = {
            "total_skills": 3,
            "ready_count": 2,
            "error_count": 1,
        }

        resp = await client.get("/status")
        assert resp.status == 200

        data = await resp.json()
        assert data["total_skills"] == 3
        assert data["ready_count"] == 2

    async def test_prompt_fragments(self, client, mock_registry):
        """GET /prompt-fragments should return 200 with fragments list."""
        mock_registry.get_system_prompt_fragments.return_value = [
            "You have 3 pending tasks.",
            "Next meeting in 30 minutes.",
        ]

        resp = await client.get("/prompt-fragments", params={"user_id": "user1"})
        assert resp.status == 200

        data = await resp.json()
        assert "fragments" in data
        assert len(data["fragments"]) == 2
        assert "pending tasks" in data["fragments"][0]
        mock_registry.get_system_prompt_fragments.assert_called_once_with("user1")

    async def test_intents_endpoint(self, client, mock_registry):
        """GET /intents should return 200 with intents mapping."""
        mock_registry.list_intents.return_value = {
            "create_task": "task_manager",
            "list_events": "calendar",
        }

        resp = await client.get("/intents")
        assert resp.status == 200

        data = await resp.json()
        assert "intents" in data
        assert data["intents"]["create_task"] == "task_manager"
        assert data["intents"]["list_events"] == "calendar"

    async def test_auth_middleware_blocks(self, auth_client):
        """Authenticated server should return 401 for requests without auth header."""
        resp = await auth_client.get("/skills")
        assert resp.status == 401

        data = await resp.json()
        assert data["error"] == "Unauthorized"

    async def test_oauth_callback_bypasses_auth_and_calls_handler(self, oauth_client):
        """OAuth callback should bypass API-secret auth and invoke provider handler."""
        client, handler = oauth_client
        resp = await client.get("/oauth/google/callback", params={"code": "c", "state": "s"})
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        handler.assert_awaited_once()

    async def test_oauth_callback_unknown_provider(self, auth_client):
        """Unknown OAuth provider should return 404 (not 401)."""
        resp = await auth_client.get("/oauth/outlook/callback")
        assert resp.status == 404
        data = await resp.json()
        assert "not configured" in data["error"]

    async def test_gmail_callback_alias_uses_google_handler(self, oauth_client):
        """Legacy /gmail/callback should route through google OAuth handler."""
        client, handler = oauth_client
        resp = await client.get("/gmail/callback", params={"code": "c", "state": "s"})
        assert resp.status == 200
        data = await resp.json()
        assert data["provider"] == "google"
        assert handler.await_count == 1

    async def test_oauth_authorize_requires_auth_header(self, auth_client):
        """OAuth authorize route should require API-secret auth."""
        resp = await auth_client.get("/oauth/google/authorize", params={"user_id": "1"})
        assert resp.status == 401

    async def test_oauth_authorize_returns_url(self, oauth_authorize_client):
        """OAuth authorize route should return URL payload when configured."""
        client, authorizer = oauth_authorize_client
        resp = await client.get(
            "/oauth/google/authorize",
            params={"user_id": "1"},
            headers={"X-API-Secret": "test-secret"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["provider"] == "google"
        assert "auth_url" in data
        authorizer.assert_awaited_once()

    async def test_auth_middleware_allows_valid_secret(self, auth_client, mock_registry):
        """Authenticated server should allow requests with valid auth header."""
        mock_registry.list_skills.return_value = []

        resp = await auth_client.get(
            "/skills",
            headers={"X-API-Secret": "test-secret"},
        )
        assert resp.status == 200


class TestSkillsServerAnnouncements:
    """Tests for announcement helper and handler flows."""

    def test_announcement_helper_payloads_and_parsing(self, mock_registry):
        server = SkillsServer(registry=mock_registry)
        updated_at = datetime(2026, 3, 13, 12, 0, tzinfo=UTC)
        preferences = AnnouncementUserPreferences(
            user_id=42,
            timezone="Australia/Sydney",
            digest_enabled=False,
            digest_window_local="18:30",
            immediate_categories=["deploy.failed"],
            muted_categories=["insight.summary"],
            max_immediate_per_hour=3,
            updated_at=updated_at,
        )

        assert server._announcement_preferences_payload(preferences) == {
            "user_id": 42,
            "timezone": "Australia/Sydney",
            "digest_enabled": False,
            "digest_window_local": "18:30",
            "immediate_categories": ["deploy.failed"],
            "muted_categories": ["insight.summary"],
            "max_immediate_per_hour": 3,
            "updated_at": updated_at.isoformat(),
        }
        assert server._announcement_receipt_payload(
            status="scheduled",
            event_id="evt-1",
            scheduled_for=updated_at,
            reason_code="digest_window",
        ) == {
            "status": "scheduled",
            "event_id": "evt-1",
            "scheduled_for": updated_at.isoformat(),
            "reason_code": "digest_window",
        }

        parsed = server._parse_announcement_event(
            {
                "source": "owner-ci",
                "category": "deploy.failed",
                "severity": "high",
                "title": "Deploy failed",
                "body": "The deployment failed during verification.",
                "target_user_id": "42",
                "tenant_id": "tenant-1",
                "occurred_at": "2026-03-13T14:00:00",
                "fingerprint": "deploy-failed",
                "idempotency_key": "dedupe-1",
                "payload": {"sha": "abc123"},
                "state": "accepted",
                "recipient": {
                    "channel": "Email",
                    "routing_key": "ops",
                    "target_user_id": "44",
                    "webhook_url": "https://example.test/hook",
                    "email": "OWNER@EXAMPLE.COM",
                    "display_name": " Ops ",
                    "metadata": {"team": "ops"},
                },
            }
        )
        assert parsed.source == "owner-ci"
        assert parsed.category == "deploy.failed"
        assert parsed.target_user_id == 42
        assert parsed.recipient is not None
        assert parsed.recipient.channel == "email"
        assert parsed.recipient.target_user_id == 44
        assert parsed.recipient.email == "owner@example.com"
        assert parsed.recipient.display_name == "Ops"
        assert parsed.payload == {"sha": "abc123"}
        assert parsed.occurred_at is not None
        assert parsed.occurred_at.tzinfo is UTC

        with pytest.raises(ValueError, match="Missing source"):
            server._parse_announcement_event(
                {
                    "source": "",
                    "category": "deploy.failed",
                    "title": "x",
                    "body": "y",
                    "target_user_id": 1,
                }
            )
        with pytest.raises(ValueError, match="Invalid target_user_id"):
            server._parse_announcement_event(
                {
                    "source": "owner-ci",
                    "category": "deploy.failed",
                    "title": "x",
                    "body": "y",
                    "target_user_id": True,
                }
            )
        with pytest.raises(ValueError, match="Invalid recipient"):
            server._parse_announcement_event(
                {
                    "source": "owner-ci",
                    "category": "deploy.failed",
                    "title": "x",
                    "body": "y",
                    "target_user_id": 1,
                    "recipient": "not-a-dict",
                }
            )
        with pytest.raises(ValueError, match="Invalid recipient target_user_id"):
            server._parse_announcement_event(
                {
                    "source": "owner-ci",
                    "category": "deploy.failed",
                    "title": "x",
                    "body": "y",
                    "recipient": {"target_user_id": True},
                }
            )
        with pytest.raises(ValueError, match="Invalid occurred_at timestamp"):
            server._parse_announcement_event(
                {
                    "source": "owner-ci",
                    "category": "deploy.failed",
                    "title": "x",
                    "body": "y",
                    "target_user_id": 1,
                    "occurred_at": "not-a-time",
                }
            )

    def test_announcement_dependencies_raise_when_unconfigured(self, mock_registry):
        server = SkillsServer(registry=mock_registry)
        with pytest.raises(RuntimeError, match="Announcements are not configured"):
            server._announcement_dependencies()

    @pytest.mark.asyncio
    async def test_emit_announcement_event_schedules_immediate_delivery(self, mock_registry):
        repository = MagicMock()
        repository.create_event = AsyncMock(
            return_value=AnnouncementReceipt(status="accepted", event_id="evt-1")
        )
        repository.create_delivery = AsyncMock()
        policy_engine = MagicMock()
        decision_time = datetime(2026, 3, 13, 15, 30, tzinfo=UTC)
        policy_engine.evaluate_event = AsyncMock(
            return_value=AnnouncementPolicyDecision(
                status="scheduled",
                delivery_mode="immediate",
                severity=AnnouncementSeverity.HIGH,
                scheduled_for=decision_time,
                reason_code="user_immediate_category",
            )
        )
        server = SkillsServer(
            registry=mock_registry,
            announcement_repository=repository,
            announcement_policy_engine=policy_engine,
        )

        result = await server._emit_announcement_event(
            {
                "source": "owner-ci",
                "category": "deploy.failed",
                "title": "Deploy failed",
                "body": "The release verification failed.",
                "target_user_id": 42,
                "channel": "discord_dm",
                "personal_profile": {"timezone": "UTC"},
                "dedupe_window_minutes": 15,
            }
        )

        create_event_call = repository.create_event.await_args
        assert create_event_call.args[0].state == "immediate"
        assert create_event_call.kwargs["dedupe_window_minutes"] == 15
        repository.create_delivery.assert_awaited_once_with(
            event_id="evt-1",
            channel="discord_dm",
            scheduled_for=decision_time,
        )
        assert result["receipt"] == {
            "status": "scheduled",
            "event_id": "evt-1",
            "scheduled_for": decision_time.isoformat(),
            "reason_code": "user_immediate_category",
        }
        assert result["decision"] == {
            "delivery_mode": "immediate",
            "severity": "high",
        }

    @pytest.mark.asyncio
    async def test_emit_announcement_event_handles_deduped_and_deferred_paths(
        self,
        mock_registry,
    ):
        repository = MagicMock()
        repository.create_event = AsyncMock(
            side_effect=[
                AnnouncementReceipt(
                    status="deduped",
                    event_id="evt-deduped",
                    reason_code="deduped",
                ),
                AnnouncementReceipt(status="accepted", event_id="evt-deferred"),
            ]
        )
        repository.create_delivery = AsyncMock()
        policy_engine = MagicMock()
        deferred_time = datetime(2026, 3, 13, 18, 0, tzinfo=UTC)
        policy_engine.evaluate_event = AsyncMock(
            side_effect=[
                AnnouncementPolicyDecision(
                    status="scheduled",
                    delivery_mode="immediate",
                    severity=AnnouncementSeverity.HIGH,
                    scheduled_for=deferred_time,
                    reason_code="deduped",
                ),
                AnnouncementPolicyDecision(
                    status="deferred",
                    delivery_mode="deferred",
                    severity=AnnouncementSeverity.NORMAL,
                    scheduled_for=deferred_time,
                    reason_code="digest_window",
                ),
            ]
        )
        server = SkillsServer(
            registry=mock_registry,
            announcement_repository=repository,
            announcement_policy_engine=policy_engine,
        )

        deduped = await server._emit_announcement_event(
            {
                "source": "owner-ci",
                "category": "deploy.failed",
                "title": "Deploy failed",
                "body": "Deduped duplicate event.",
                "target_user_id": 7,
            }
        )
        deferred = await server._emit_announcement_event(
            {
                "source": "owner-ci",
                "category": "insight.summary",
                "title": "Daily summary",
                "body": "Digest this later.",
                "target_user_id": 7,
            }
        )

        repository.create_delivery.assert_not_awaited()
        assert deduped["receipt"]["status"] == "deduped"
        assert deduped["receipt"]["reason_code"] == "deduped"
        assert deferred["receipt"]["status"] == "deferred"
        assert deferred["receipt"]["scheduled_for"] == deferred_time.isoformat()
        assert deferred["receipt"]["reason_code"] == "digest_window"

    @pytest.mark.asyncio
    async def test_announcement_emit_handlers_map_success_and_errors(self, mock_registry):
        server = SkillsServer(registry=mock_registry)
        request = MagicMock()
        request.json = AsyncMock(return_value={"source": "owner-ci"})
        server._emit_announcement_event = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "receipt": {"status": "scheduled", "event_id": "evt-1"},
                "decision": {"delivery_mode": "immediate", "severity": "high"},
            }
        )

        resp = await server.handle_announcement_emit_event(request)
        assert resp.status == 200
        data = json.loads(resp.text)
        assert data["ok"] is True
        assert data["receipt"]["event_id"] == "evt-1"

        server._emit_announcement_event = AsyncMock(side_effect=RuntimeError("not configured"))  # type: ignore[method-assign]
        resp = await server.handle_announcement_emit_event(request)
        assert resp.status == 501
        assert json.loads(resp.text)["error"] == "not configured"

        server._emit_announcement_event = AsyncMock(side_effect=ValueError("bad payload"))  # type: ignore[method-assign]
        resp = await server.handle_announcement_emit_event(request)
        assert resp.status == 400
        assert json.loads(resp.text)["error"] == "bad payload"

        server._emit_announcement_event = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[method-assign]
        server._read_json_object = AsyncMock(side_effect=Exception("explode"))  # type: ignore[method-assign]
        resp = await server.handle_announcement_emit_event(request)
        assert resp.status == 500
        assert json.loads(resp.text)["error"] == "Failed to emit announcement"

    @pytest.mark.asyncio
    async def test_announcement_batch_preferences_and_flush_handlers(self, mock_registry):
        repository = MagicMock()
        repository.get_user_preferences = AsyncMock(return_value=None)
        updated_at = datetime(2026, 3, 13, 16, 0, tzinfo=UTC)
        repository.upsert_user_preferences = AsyncMock(
            return_value=AnnouncementUserPreferences(
                user_id=42,
                timezone="Australia/Sydney",
                digest_enabled=False,
                digest_window_local="19:45",
                immediate_categories=["deploy.failed"],
                muted_categories=["insight.summary"],
                max_immediate_per_hour=2,
                updated_at=updated_at,
            )
        )
        repository.list_due_deliveries = AsyncMock(
            return_value=[
                AnnouncementDelivery(
                    delivery_id=1,
                    event_id="evt-1",
                    channel="discord_dm",
                    scheduled_for=updated_at,
                    sent_at=None,
                    status="scheduled",
                    error_code=None,
                    error_detail=None,
                    retry_count=0,
                    created_at=updated_at,
                    updated_at=updated_at,
                )
            ]
        )
        server = SkillsServer(registry=mock_registry, announcement_repository=repository)

        batch_request = MagicMock()
        batch_request.json = AsyncMock(
            return_value={
                "events": [
                    {
                        "source": "owner-ci",
                        "category": "deploy.failed",
                        "title": "Deploy failed",
                        "body": "Release verification failed.",
                        "target_user_id": 42,
                    },
                    "not-a-dict",
                    {
                        "source": "owner-ci",
                        "category": "deploy.failed",
                        "title": "Missing body",
                    },
                ]
            }
        )
        server._emit_announcement_event = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                {
                    "receipt": {
                        "status": "scheduled",
                        "event_id": "evt-1",
                        "scheduled_for": updated_at.isoformat(),
                        "reason_code": "digest_window",
                    }
                },
                ValueError("Missing body"),
            ]
        )

        batch_resp = await server.handle_announcement_emit_batch(batch_request)
        batch_data = json.loads(batch_resp.text)
        assert batch_resp.status == 200
        assert batch_data["ok"] is False
        assert batch_data["count"] == 1
        assert len(batch_data["errors"]) == 2

        runtime_batch_request = MagicMock()
        runtime_batch_request.json = AsyncMock(
            return_value={"events": [{"source": "owner-ci", "category": "deploy.failed"}]}
        )
        server._emit_announcement_event = AsyncMock(side_effect=RuntimeError("not configured"))  # type: ignore[method-assign]
        runtime_batch_resp = await server.handle_announcement_emit_batch(runtime_batch_request)
        assert runtime_batch_resp.status == 501
        assert json.loads(runtime_batch_resp.text)["error"] == "not configured"

        get_request = MagicMock()
        get_request.match_info = {"user_id": "42"}
        get_resp = await server.handle_announcement_get_preferences(get_request)
        get_data = json.loads(get_resp.text)
        assert get_resp.status == 200
        assert get_data["preferences"]["user_id"] == 42
        assert get_data["preferences"]["timezone"] == "UTC"

        put_request = MagicMock()
        put_request.match_info = {"user_id": "42"}
        put_request.json = AsyncMock(
            return_value={
                "timezone": "Australia/Sydney",
                "digest_enabled": False,
                "digest_window_local": "19:45",
                "immediate_categories": ["deploy.failed"],
                "muted_categories": ["insight.summary"],
                "max_immediate_per_hour": 2,
            }
        )
        put_resp = await server.handle_announcement_put_preferences(put_request)
        put_data = json.loads(put_resp.text)
        assert put_resp.status == 200
        patch_arg = repository.upsert_user_preferences.await_args.kwargs["patch"]
        assert isinstance(patch_arg, AnnouncementPreferencePatch)
        assert patch_arg.timezone == "Australia/Sydney"
        assert patch_arg.digest_enabled is False
        assert put_data["preferences"]["digest_window_local"] == "19:45"

        flush_request = MagicMock()
        flush_request.json = AsyncMock(return_value={"limit": 2500})
        flush_resp = await server.handle_announcement_dispatch_flush(flush_request)
        flush_data = json.loads(flush_resp.text)
        assert flush_resp.status == 200
        repository.list_due_deliveries.assert_awaited_once_with(limit=1000)
        assert flush_data["count"] == 1
        assert flush_data["deliveries"][0]["delivery_id"] == 1


class TestSkillsServerWorkerBridge:
    """Tests for worker bridge handler success paths."""

    @pytest.mark.asyncio
    async def test_worker_bridge_handlers_cover_success_paths(self, mock_registry):
        tenant_admin_manager = MagicMock()
        now = datetime(2026, 3, 13, 17, 0, tzinfo=UTC)
        tenant_admin_manager.bootstrap_worker_node_session = AsyncMock(
            return_value={
                "node": {"node_id": "node-1", "status": "bootstrapped"},
                "capabilities": ["docker", "ci"],
                "session": {"session_id": "sess-1", "expires_at": now},
            }
        )
        tenant_admin_manager.register_worker_node = AsyncMock(
            return_value={"node_id": "node-1", "status": "registered"}
        )
        tenant_admin_manager.rotate_worker_session_credentials = AsyncMock(
            return_value={"expires_at": now, "rotated_at": now}
        )
        tenant_admin_manager.heartbeat_worker_node = AsyncMock(
            return_value={"node_id": "node-1", "health_status": "healthy"}
        )
        tenant_admin_manager.record_worker_job_event = AsyncMock()
        tenant_admin_manager.has_worker_capabilities = AsyncMock(return_value=True)
        tenant_admin_manager.claim_worker_dispatch_job = AsyncMock(
            return_value={
                "job_id": "job-1",
                "plan_id": "plan-1",
                "step_id": "step-1",
                "retry_id": "retry-1",
                "execution_mode": "live",
                "execution_target": "windows-worker",
                "action": "ci.test.run",
                "required_capabilities": ["docker", "ci"],
                "max_runtime_seconds": 900,
                "artifact_contract": {"required": ["logs"]},
                "payload_json": {"runner": "compose", "lane_id": "unit-full"},
            }
        )
        tenant_admin_manager.is_worker_node_canary_enabled = MagicMock(return_value=True)

        server = SkillsServer(registry=mock_registry, tenant_admin_manager=tenant_admin_manager)
        allowed_decision = SimpleNamespace(allowed=True, status=200, code="allowed")
        server._trust_policy_evaluator = SimpleNamespace(
            evaluate=MagicMock(return_value=allowed_decision)
        )
        server._resolve_worker_bootstrap_secret = MagicMock(  # type: ignore[method-assign]
            return_value="bootstrap-secret"
        )
        server._record_security_event = AsyncMock()  # type: ignore[method-assign]
        server._verify_worker_signature = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                {
                    "session_id": "sess-1",
                    "request_nonce": "nonce-register",
                    "status": "registered",
                    "health_status": "healthy",
                    "node_metadata": {"lane": "ci"},
                },
                {
                    "session_id": "sess-1",
                    "request_nonce": "nonce-heartbeat",
                    "status": "active",
                    "health_status": "healthy",
                },
                {
                    "session_id": "sess-1",
                    "request_nonce": "nonce-claim",
                    "status": "active",
                    "health_status": "healthy",
                    "node_metadata": {"lane": "ci"},
                },
            ]
        )
        server._emit_worker_lifecycle_announcement = AsyncMock()  # type: ignore[method-assign]

        bootstrap_request = MagicMock()
        bootstrap_request.text = AsyncMock(
            return_value=json.dumps(
                {
                    "tenant_id": "tenant-1",
                    "node_id": "node-1",
                    "node_name": "CI Worker",
                    "capabilities": ["docker", "ci"],
                    "metadata": {"role": "owner-ci"},
                }
            )
        )
        bootstrap_request.headers = {"X-Worker-Bootstrap-Secret": "bootstrap-secret"}
        bootstrap_resp = await server.handle_worker_bootstrap(bootstrap_request)
        bootstrap_data = json.loads(bootstrap_resp.text)
        assert bootstrap_resp.status == 201
        assert bootstrap_data["ok"] is True
        assert bootstrap_data["node"]["node_id"] == "node-1"
        assert bootstrap_data["session"]["session_id"] == "sess-1"

        register_request = MagicMock()
        register_request.text = AsyncMock(
            return_value=json.dumps(
                {
                    "tenant_id": "tenant-1",
                    "node_id": "node-1",
                    "node_name": "CI Worker",
                    "capabilities": ["docker", "ci"],
                    "metadata": {"role": "owner-ci"},
                    "rotate_credentials": True,
                }
            )
        )
        register_resp = await server.handle_worker_register_node(register_request)
        register_data = json.loads(register_resp.text)
        assert register_resp.status == 200
        assert register_data["ok"] is True
        assert register_data["session"]["session_id"] == "sess-1"
        assert register_data["session"]["token"]
        assert register_data["session"]["signing_secret"]

        heartbeat_request = MagicMock()
        heartbeat_request.match_info = {"node_id": "node-1"}
        heartbeat_request.text = AsyncMock(
            return_value=json.dumps(
                {
                    "tenant_id": "tenant-1",
                    "node_id": "node-1",
                    "health_status": "healthy",
                    "metadata": {"load": "normal"},
                }
            )
        )
        heartbeat_resp = await server.handle_worker_node_heartbeat(heartbeat_request)
        heartbeat_data = json.loads(heartbeat_resp.text)
        assert heartbeat_resp.status == 200
        assert heartbeat_data["node"]["health_status"] == "healthy"

        claim_request = MagicMock()
        claim_request.match_info = {"node_id": "node-1"}
        claim_request.text = AsyncMock(
            return_value=json.dumps(
                {
                    "tenant_id": "tenant-1",
                    "required_capabilities": ["docker", "ci"],
                    "poll_after_seconds": 250,
                }
            )
        )
        claim_resp = await server.handle_worker_claim_job(claim_request)
        claim_data = json.loads(claim_resp.text)
        assert claim_resp.status == 200
        assert claim_data["ok"] is True
        assert claim_data["reason"] == "claimed"
        assert claim_data["job"]["job_id"] == "job-1"
        assert claim_data["job"]["payload"]["lane_id"] == "unit-full"
        assert claim_data["poll_after_seconds"] == 120

        tenant_admin_manager.bootstrap_worker_node_session.assert_awaited_once()
        tenant_admin_manager.register_worker_node.assert_awaited_once()
        tenant_admin_manager.rotate_worker_session_credentials.assert_awaited_once()
        tenant_admin_manager.heartbeat_worker_node.assert_awaited_once()
        tenant_admin_manager.claim_worker_dispatch_job.assert_awaited_once()
        tenant_admin_manager.record_worker_job_event.assert_awaited()
        server._emit_worker_lifecycle_announcement.assert_awaited_once()


class TestSkillsServerLifecycle:
    """Tests for SkillsServer lifecycle methods."""

    def test_create_app(self, mock_registry):
        """create_app() should return a configured aiohttp Application."""
        server = SkillsServer(registry=mock_registry)
        app = server.create_app()

        assert isinstance(app, web.Application)
        assert server._app is app

        # Verify all expected routes are registered
        route_paths = {
            resource.get_info().get("path", resource.get_info().get("formatter"))
            for resource in app.router.resources()
        }
        expected_paths = {
            "/health",
            "/handle",
            "/heartbeat",
            "/skills",
            "/skills/{name}",
            "/status",
            "/prompt-fragments",
            "/intents",
        }
        assert expected_paths.issubset(route_paths)

    async def test_start_stop(self, mock_registry):
        """start() and stop() should manage the AppRunner lifecycle."""
        server = SkillsServer(registry=mock_registry)

        with (
            patch.object(web, "AppRunner") as mock_runner_cls,
            patch.object(web, "TCPSite") as mock_site_cls,
        ):
            mock_runner_instance = AsyncMock()
            mock_runner_cls.return_value = mock_runner_instance

            mock_site_instance = AsyncMock()
            mock_site_cls.return_value = mock_site_instance

            await server.start()

            mock_runner_cls.assert_called_once()
            mock_runner_instance.setup.assert_awaited_once()
            mock_site_cls.assert_called_once_with(mock_runner_instance, "0.0.0.0", 8080)
            mock_site_instance.start.assert_awaited_once()
            assert server._runner is mock_runner_instance

            await server.stop()

            mock_runner_instance.cleanup.assert_awaited_once()
            assert server._runner is None

    async def test_stop_without_start(self, mock_registry):
        """stop() should be a no-op when server was never started."""
        server = SkillsServer(registry=mock_registry)
        # Should not raise
        await server.stop()
        assert server._runner is None


class TestUserManagementEndpoints:
    """Tests for user management API endpoints (/users/*)."""

    @pytest.fixture
    def user_manager(self):
        """Create a mock user manager."""
        return AsyncMock()

    @pytest.fixture
    def server_with_user_manager(self, mock_registry, user_manager):
        """Create a SkillsServer with a mock user manager."""
        return SkillsServer(registry=mock_registry, user_manager=user_manager)

    @pytest.fixture
    async def client_with_users(self, server_with_user_manager):
        """Create a test client for user management endpoints."""
        app = server_with_user_manager.create_app()
        async with TestClient(TestServer(app)) as test_client:
            yield test_client

    async def test_list_users(self, client_with_users, user_manager):
        """GET /users should return the list of users."""
        user_manager.list_users.return_value = [
            {"discord_user_id": 123, "role": "user"},
        ]

        resp = await client_with_users.get("/users")
        assert resp.status == 200

        data = await resp.json()
        assert "users" in data
        assert len(data["users"]) == 1
        assert data["users"][0]["discord_user_id"] == 123
        assert data["users"][0]["role"] == "user"
        user_manager.list_users.assert_awaited_once_with(role_filter=None)

    async def test_list_users_with_role_filter(self, client_with_users, user_manager):
        """GET /users?role=admin should pass role filter to user manager."""
        user_manager.list_users.return_value = [
            {"discord_user_id": 456, "role": "admin"},
        ]

        resp = await client_with_users.get("/users", params={"role": "admin"})
        assert resp.status == 200

        data = await resp.json()
        assert len(data["users"]) == 1
        assert data["users"][0]["role"] == "admin"
        user_manager.list_users.assert_awaited_once_with(role_filter="admin")

    async def test_add_user_success(self, client_with_users, user_manager):
        """POST /users should return 201 when add_user succeeds."""
        user_manager.add_user.return_value = True

        resp = await client_with_users.post(
            "/users",
            json={"user_id": 789, "role": "user", "added_by": 1},
        )
        assert resp.status == 201

        data = await resp.json()
        assert data["ok"] is True
        user_manager.add_user.assert_awaited_once_with(user_id=789, role="user", added_by=1)

    async def test_add_user_failure(self, client_with_users, user_manager):
        """POST /users should return 403 when add_user returns False."""
        user_manager.add_user.return_value = False

        resp = await client_with_users.post(
            "/users",
            json={"user_id": 789, "role": "admin", "added_by": 1},
        )
        assert resp.status == 403

        data = await resp.json()
        assert data["ok"] is False
        assert "error" in data

    async def test_delete_user_success(self, client_with_users, user_manager):
        """DELETE /users/{user_id}?removed_by= should return 200 on success."""
        user_manager.remove_user.return_value = True

        resp = await client_with_users.delete("/users/123", params={"removed_by": "1"})
        assert resp.status == 200

        data = await resp.json()
        assert data["ok"] is True
        user_manager.remove_user.assert_awaited_once_with(user_id=123, removed_by=1)

    async def test_delete_user_failure(self, client_with_users, user_manager):
        """DELETE /users/{user_id} should return 403 when remove_user fails."""
        user_manager.remove_user.return_value = False

        resp = await client_with_users.delete("/users/123")
        assert resp.status == 403

        data = await resp.json()
        assert data["ok"] is False
        assert "error" in data

    async def test_delete_user_invalid_user_id_returns_400(self, client_with_users):
        """DELETE /users/{user_id} with invalid integer returns 400."""
        resp = await client_with_users.delete("/users/not-an-int")
        assert resp.status == 400

        data = await resp.json()
        assert "error" in data

    async def test_patch_user_role_success(self, client_with_users, user_manager):
        """PATCH /users/{user_id}/role should return 200 on success."""
        user_manager.set_role.return_value = True

        resp = await client_with_users.patch(
            "/users/123/role",
            json={"role": "admin", "changed_by": 1},
        )
        assert resp.status == 200

        data = await resp.json()
        assert data["ok"] is True
        user_manager.set_role.assert_awaited_once_with(user_id=123, new_role="admin", changed_by=1)

    async def test_patch_user_role_failure(self, client_with_users, user_manager):
        """PATCH /users/{user_id}/role should return 403 when set_role fails."""
        user_manager.set_role.return_value = False

        resp = await client_with_users.patch(
            "/users/123/role",
            json={"role": "admin", "changed_by": 1},
        )
        assert resp.status == 403

        data = await resp.json()
        assert data["ok"] is False
        assert "error" in data

    async def test_audit_log(self, client_with_users, user_manager):
        """GET /users/audit should return audit log entries."""
        user_manager.get_audit_log.return_value = [
            {"action": "add_user", "actor": 1, "target": 123},
            {"action": "set_role", "actor": 1, "target": 456},
        ]

        resp = await client_with_users.get("/users/audit", params={"limit": "10"})
        assert resp.status == 200

        data = await resp.json()
        assert "entries" in data
        assert len(data["entries"]) == 2
        assert data["entries"][0]["action"] == "add_user"
        user_manager.get_audit_log.assert_awaited_once_with(limit=10)

    async def test_audit_log_invalid_limit_returns_400(self, client_with_users):
        """GET /users/audit with invalid limit returns 400."""
        resp = await client_with_users.get("/users/audit", params={"limit": "bad"})
        assert resp.status == 400

        data = await resp.json()
        assert "error" in data

    async def test_list_users_returns_501_when_manager_is_none(self, mock_registry):
        """GET /users should return 501 when user_manager is None."""
        server = SkillsServer(registry=mock_registry, user_manager=None)
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/users")
            assert resp.status == 501

            data = await resp.json()
            assert "error" in data


class TestSettingsEndpoints:
    """Tests for settings API endpoints (/settings/*)."""

    @pytest.fixture
    def settings_manager(self):
        """Create a mock settings manager."""
        return AsyncMock()

    @pytest.fixture
    def server_with_settings(self, mock_registry, settings_manager):
        """Create a SkillsServer with a mock settings manager."""
        return SkillsServer(registry=mock_registry, settings_manager=settings_manager)

    @pytest.fixture
    async def client_with_settings(self, server_with_settings):
        """Create a test client for settings endpoints."""
        app = server_with_settings.create_app()
        async with TestClient(TestServer(app)) as test_client:
            yield test_client

    async def test_list_settings(self, client_with_settings, settings_manager):
        """GET /settings should return all settings."""
        settings_manager.get_all.return_value = {"models": {"key": "val"}}

        resp = await client_with_settings.get("/settings")
        assert resp.status == 200

        data = await resp.json()
        assert "settings" in data
        assert data["settings"] == {"models": {"key": "val"}}
        settings_manager.get_all.assert_awaited_once_with(namespace=None)

    async def test_list_settings_with_namespace_filter(
        self, client_with_settings, settings_manager
    ):
        """GET /settings?namespace=models should pass namespace filter."""
        settings_manager.get_all.return_value = {"models": {"key": "val"}}

        resp = await client_with_settings.get("/settings", params={"namespace": "models"})
        assert resp.status == 200

        data = await resp.json()
        assert data["settings"] == {"models": {"key": "val"}}
        settings_manager.get_all.assert_awaited_once_with(namespace="models")

    async def test_get_setting(self, client_with_settings, settings_manager):
        """GET /settings/{namespace}/{key} should return the setting value."""
        # settings_manager.get() is called synchronously in the handler
        settings_manager.get = MagicMock(return_value="value")

        resp = await client_with_settings.get("/settings/models/key")
        assert resp.status == 200

        data = await resp.json()
        assert data["namespace"] == "models"
        assert data["key"] == "key"
        assert data["value"] == "value"
        settings_manager.get.assert_called_once_with("models", "key")

    async def test_put_setting_success(self, client_with_settings, settings_manager):
        """PUT /settings/{namespace}/{key} should return 200 on success."""
        resp = await client_with_settings.put(
            "/settings/models/key",
            json={"value": "new_value", "changed_by": 1, "data_type": "string"},
        )
        assert resp.status == 200

        data = await resp.json()
        assert data["ok"] is True
        settings_manager.set.assert_awaited_once_with(
            namespace="models",
            key="key",
            value="new_value",
            changed_by=1,
            data_type="string",
        )

    async def test_put_setting_value_error(self, client_with_settings, settings_manager):
        """PUT /settings/{namespace}/{key} should return 400 on ValueError."""
        settings_manager.set.side_effect = ValueError("Invalid data type")

        resp = await client_with_settings.put(
            "/settings/models/key",
            json={"value": "bad", "changed_by": 1, "data_type": "invalid"},
        )
        assert resp.status == 400

        data = await resp.json()
        assert "error" in data
        assert "Invalid data type" in data["error"]

    async def test_put_setting_invalid_changed_by_returns_400(self, client_with_settings):
        """PUT /settings/{namespace}/{key} with invalid changed_by returns 400."""
        resp = await client_with_settings.put(
            "/settings/models/key",
            json={"value": "new_value", "changed_by": "not-int", "data_type": "string"},
        )
        assert resp.status == 400

        data = await resp.json()
        assert "error" in data

    async def test_delete_setting_success(self, client_with_settings, settings_manager):
        """DELETE /settings/{namespace}/{key} should return 200 on success."""
        settings_manager.delete.return_value = True

        resp = await client_with_settings.delete("/settings/models/key", params={"deleted_by": "1"})
        assert resp.status == 200

        data = await resp.json()
        assert data["ok"] is True
        assert data["existed"] is True
        settings_manager.delete.assert_awaited_once_with(
            namespace="models", key="key", deleted_by=1
        )

    async def test_list_settings_returns_501_when_manager_is_none(self, mock_registry):
        """GET /settings should return 501 when settings_manager is None."""
        server = SkillsServer(registry=mock_registry, settings_manager=None)
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/settings")
            assert resp.status == 501

            data = await resp.json()
            assert "error" in data


class TestSecretsEndpoints:
    """Tests for secrets API endpoints (/secrets/*)."""

    @pytest.fixture
    def secrets_manager(self):
        """Create a mock secrets manager."""
        mgr = MagicMock()
        mgr.get_metadata = AsyncMock(return_value=[])
        mgr.set = AsyncMock(return_value=None)
        mgr.delete = AsyncMock(return_value=True)
        return mgr

    @pytest.fixture
    def server_with_secrets(self, mock_registry, secrets_manager):
        """Create a SkillsServer with a mock secrets manager."""
        return SkillsServer(registry=mock_registry, secrets_manager=secrets_manager)

    @pytest.fixture
    async def client_with_secrets(self, server_with_secrets):
        """Create a test client for secrets endpoints."""
        app = server_with_secrets.create_app()
        async with TestClient(TestServer(app)) as test_client:
            yield test_client

    async def test_list_secrets(self, client_with_secrets, secrets_manager):
        """GET /secrets should return metadata list."""
        secrets_manager.get_metadata.return_value = [{"name": "google_client_secret"}]
        resp = await client_with_secrets.get("/secrets")
        assert resp.status == 200
        data = await resp.json()
        assert data["secrets"][0]["name"] == "google_client_secret"
        secrets_manager.get_metadata.assert_awaited_once()

    async def test_put_secret(self, client_with_secrets, secrets_manager):
        """PUT /secrets/{name} should store an encrypted secret."""
        resp = await client_with_secrets.put(
            "/secrets/google_client_secret",
            json={"value": "secret-value", "changed_by": 1, "description": "OAuth client secret"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        secrets_manager.set.assert_awaited_once_with(
            name="google_client_secret",
            value="secret-value",
            changed_by=1,
            description="OAuth client secret",
        )

    async def test_delete_secret(self, client_with_secrets, secrets_manager):
        """DELETE /secrets/{name} should delete a secret."""
        resp = await client_with_secrets.delete(
            "/secrets/google_client_secret",
            params={"deleted_by": "1"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["existed"] is True
        secrets_manager.delete.assert_awaited_once_with(
            name="google_client_secret",
            deleted_by=1,
        )

    async def test_list_secrets_returns_501_when_manager_is_none(self, mock_registry):
        """GET /secrets should return 501 when secrets_manager is None."""
        server = SkillsServer(registry=mock_registry, secrets_manager=None)
        app = server.create_app()
        async with TestClient(TestServer(app)) as test_client:
            resp = await test_client.get("/secrets")
            assert resp.status == 501
            data = await resp.json()
            assert "error" in data

    async def test_put_secret_returns_501_when_manager_is_none(self, mock_registry):
        """PUT /secrets/{name} should return 501 when secrets_manager is None."""
        server = SkillsServer(registry=mock_registry, secrets_manager=None)
        app = server.create_app()
        async with TestClient(TestServer(app)) as test_client:
            resp = await test_client.put(
                "/secrets/google_client_secret",
                json={"value": "v", "changed_by": 1},
            )
            assert resp.status == 501

    async def test_delete_secret_returns_501_when_manager_is_none(self, mock_registry):
        """DELETE /secrets/{name} should return 501 when secrets_manager is None."""
        server = SkillsServer(registry=mock_registry, secrets_manager=None)
        app = server.create_app()
        async with TestClient(TestServer(app)) as test_client:
            resp = await test_client.delete(
                "/secrets/google_client_secret",
                params={"deleted_by": "1"},
            )
            assert resp.status == 501

    async def test_put_secret_missing_value_returns_400(self, client_with_secrets):
        """PUT /secrets/{name} should reject empty secret values."""
        resp = await client_with_secrets.put(
            "/secrets/google_client_secret",
            json={"value": "", "changed_by": 1},
        )
        assert resp.status == 400
        data = await resp.json()
        assert "Missing non-empty secret" in data["error"]

    async def test_delete_secret_invalid_deleted_by_returns_400(self, client_with_secrets):
        """DELETE /secrets/{name} should validate deleted_by as int."""
        resp = await client_with_secrets.delete(
            "/secrets/google_client_secret",
            params={"deleted_by": "not-int"},
        )
        assert resp.status == 400


class TestTenantAdminEndpoints:
    """Tests for tenant-admin API endpoints (/admin/tenants/*)."""

    @pytest.fixture
    def tenant_admin_manager(self):
        mgr = MagicMock()
        mgr.list_discord_users = AsyncMock(return_value=[{"discord_user_id": 1, "role": "admin"}])
        mgr.upsert_discord_user = AsyncMock(return_value={"discord_user_id": 2, "role": "user"})
        mgr.delete_discord_user = AsyncMock(return_value=True)
        mgr.update_discord_user_role = AsyncMock(return_value=True)
        mgr.list_discord_bindings = AsyncMock(return_value=[])
        mgr.put_guild_binding = AsyncMock(return_value={"guild_id": 10, "channel_id": None})
        mgr.put_channel_binding = AsyncMock(return_value={"guild_id": 10, "channel_id": 20})
        mgr.delete_channel_binding = AsyncMock(return_value=True)
        mgr.list_settings = AsyncMock(return_value={"models": {"default_provider": "groq"}})
        mgr.set_setting = AsyncMock(return_value=None)
        mgr.delete_setting = AsyncMock(return_value=True)
        mgr.list_secret_metadata = AsyncMock(
            return_value=[{"name": "OPENAI_API_KEY", "version": 2}]
        )
        mgr.set_secret = AsyncMock(return_value={"name": "OPENAI_API_KEY", "version": 3})
        mgr.delete_secret = AsyncMock(return_value=True)
        mgr.list_audit = AsyncMock(return_value=[{"action": "tenant_secret_upsert"}])
        mgr.get_email_provider_config = AsyncMock(
            return_value={
                "provider": "google",
                "redirect_uri": "https://cgs.example.com/callback",
                "enabled": True,
                "has_client_id": True,
                "has_client_secret": True,
            }
        )
        mgr.put_email_provider_config = AsyncMock(
            return_value={
                "provider": "google",
                "redirect_uri": "https://cgs.example.com/callback",
                "enabled": True,
                "has_client_id": True,
                "has_client_secret": True,
            }
        )
        mgr.create_email_oauth_start = AsyncMock(
            return_value={
                "provider": "google",
                "auth_url": "https://accounts.google.com/o/oauth2/v2/auth?state=abc",
                "state": "abc",
                "expires_at": "2026-03-04T00:00:00+00:00",
            }
        )
        mgr.exchange_google_oauth_code = AsyncMock(
            return_value={"account_id": "acc-1", "email_address": "ops@example.com"}
        )
        mgr.list_email_accounts = AsyncMock(
            return_value=[
                {"account_id": "acc-1", "email_address": "ops@example.com", "status": "connected"}
            ]
        )
        mgr.patch_email_account = AsyncMock(
            return_value={
                "account_id": "acc-1",
                "email_address": "ops@example.com",
                "status": "degraded",
            }
        )
        mgr.delete_email_account = AsyncMock(return_value=True)
        mgr.sync_email_account = AsyncMock(return_value={"job_id": "job-1", "status": "succeeded"})
        mgr.list_email_critical_items = AsyncMock(
            return_value=[{"item_id": "crit-1", "severity": "high"}]
        )
        mgr.list_email_insights = AsyncMock(return_value=[{"insight_id": "ins-1"}])
        mgr.reindex_email_insights = AsyncMock(return_value={"reindexed": 1, "scanned": 1})
        mgr.list_google_calendars = AsyncMock(
            return_value=[{"id": "primary", "summary": "Primary"}]
        )
        mgr.set_email_primary_calendar = AsyncMock(
            return_value={"account_id": "acc-1", "primary_calendar_id": "primary"}
        )

        def _secret_lookup(_tenant_id: str, name: str, default: str | None = None):
            if name == "github_token":
                return "ghp_test_token"
            if name == "WHATSAPP_BRIDGE_SIGNING_SECRET":
                return "bridge-signing-secret"
            return "bridge-signing-secret" if default is None else default

        mgr.get_secret_cached = MagicMock(side_effect=_secret_lookup)
        mgr.get_messaging_provider_config = AsyncMock(
            return_value={
                "provider": "whatsapp",
                "enabled": True,
                "bridge_mode": "local_sidecar",
                "account_ref": "acct-1",
                "session_ref": "sess-1",
                "metadata": {},
            }
        )
        mgr.put_messaging_provider_config = AsyncMock(
            return_value={
                "provider": "whatsapp",
                "enabled": True,
                "bridge_mode": "local_sidecar",
                "account_ref": "acct-1",
                "session_ref": "sess-1",
                "metadata": {"label": "phone-main"},
            }
        )
        mgr.put_messaging_chat_policy = AsyncMock(
            return_value={
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "read_enabled": True,
                "send_enabled": True,
                "retention_days": 14,
                "metadata": {"label": "Family"},
            }
        )
        mgr.get_messaging_chat_policy = AsyncMock(
            return_value={
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "read_enabled": True,
                "send_enabled": True,
                "retention_days": 14,
                "metadata": {"label": "Family"},
            }
        )
        mgr.list_messaging_chats = AsyncMock(
            return_value=[
                {
                    "provider": "whatsapp",
                    "chat_id": "chat-1",
                    "read_enabled": True,
                    "send_enabled": True,
                    "retention_days": 14,
                    "message_count": 2,
                }
            ]
        )
        mgr.list_messaging_messages = AsyncMock(
            return_value=[
                {
                    "provider": "whatsapp",
                    "chat_id": "chat-1",
                    "message_id": "44444444-4444-4444-4444-444444444444",
                    "direction": "inbound",
                    "body_text": "hello",
                }
            ]
        )
        mgr.export_messaging_messages = AsyncMock(
            return_value=[
                {
                    "provider": "whatsapp",
                    "chat_id": "chat-1",
                    "message_id": "11111111-1111-1111-1111-111111111111",
                    "direction": "inbound",
                    "sender_id": "sender-1",
                    "body_text": "hello",
                }
            ]
        )
        mgr.delete_messaging_messages = AsyncMock(
            return_value={
                "deleted_count": 1,
                "deleted_message_ids": ["11111111-1111-1111-1111-111111111111"],
            }
        )
        mgr.queue_messaging_send = AsyncMock(
            return_value={
                "action": {
                    "action_id": "55555555-5555-5555-5555-555555555555",
                    "status": "queued",
                },
                "message": {
                    "message_id": "66666666-6666-6666-6666-666666666666",
                    "direction": "outbound",
                    "body_text": "hi there",
                },
            }
        )
        mgr.is_messaging_chat_allowed = AsyncMock(return_value=True)
        mgr.ingest_messaging_message = AsyncMock(
            return_value={"message_id": "77777777-7777-7777-7777-777777777777"}
        )
        mgr.purge_expired_messaging_messages = AsyncMock(return_value=0)
        mgr.record_security_event = AsyncMock(return_value={"event_id": 1})
        mgr.list_security_events = AsyncMock(
            return_value=[
                {
                    "event_id": 1,
                    "event_type": "trust_policy_denied",
                    "severity": "high",
                    "action": "messaging.send",
                }
            ]
        )
        mgr.get_security_dashboard = AsyncMock(
            return_value={
                "window_hours": 24,
                "window_started_at": "2026-03-05T00:00:00+00:00",
                "totals": {"events": 1, "by_severity": {"high": 1}},
                "top_event_types": [{"event_type": "trust_policy_denied", "count": 1}],
                "recent_events": [],
            }
        )
        mgr.create_execution_plan = AsyncMock(
            return_value={
                "plan": {
                    "plan_id": "99999999-9999-9999-9999-999999999999",
                    "status": "queued",
                    "title": "Night Build",
                    "goal": "Ship overnight",
                },
                "steps": [
                    {
                        "step_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                        "step_index": 0,
                        "status": "pending",
                    }
                ],
            }
        )
        mgr.list_execution_plans = AsyncMock(
            return_value=[
                {
                    "plan_id": "99999999-9999-9999-9999-999999999999",
                    "status": "queued",
                    "title": "Night Build",
                }
            ]
        )
        mgr.get_execution_plan = AsyncMock(
            return_value={
                "plan_id": "99999999-9999-9999-9999-999999999999",
                "status": "queued",
                "title": "Night Build",
                "goal": "Ship overnight",
            }
        )
        mgr.list_execution_plan_steps = AsyncMock(
            return_value=[
                {
                    "step_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "step_index": 0,
                    "status": "pending",
                    "prompt_text": "Design schema",
                }
            ]
        )
        mgr.pause_execution_plan = AsyncMock(
            return_value={
                "plan_id": "99999999-9999-9999-9999-999999999999",
                "status": "paused",
            }
        )
        mgr.resume_execution_plan = AsyncMock(
            return_value={
                "plan_id": "99999999-9999-9999-9999-999999999999",
                "status": "queued",
            }
        )
        mgr.cancel_execution_plan = AsyncMock(
            return_value={
                "plan_id": "99999999-9999-9999-9999-999999999999",
                "status": "cancelled",
            }
        )
        mgr.record_admin_event = AsyncMock(return_value=None)
        return mgr

    @pytest.fixture
    async def admin_client(self, mock_registry, tenant_admin_manager):
        server = SkillsServer(
            registry=mock_registry,
            api_secret="test-secret",
            tenant_admin_manager=tenant_admin_manager,
        )
        server._trust_policy_evaluator = SimpleNamespace(
            evaluate=lambda **_kwargs: SimpleNamespace(
                allowed=True,
                approval_required=False,
                status=200,
                code="AI_OK",
                message="Allowed",
                details={},
                requires_two_person=False,
            )
        )
        app = server.create_app()
        async with TestClient(TestServer(app)) as test_client:
            yield test_client

    async def test_tenant_admin_requires_actor_envelope(self, admin_client):
        resp = await admin_client.get(
            "/admin/tenants/11111111-1111-1111-1111-111111111111/discord-users",
            headers={"X-API-Secret": "test-secret"},
        )
        assert resp.status == 401
        data = await resp.json()
        assert "signature" in data["error"].lower()

    async def test_tenant_admin_rejects_replayed_nonce(self, admin_client):
        headers = {"X-API-Secret": "test-secret"}
        headers.update(_admin_headers(signing_secret="test-secret"))
        path = "/admin/tenants/11111111-1111-1111-1111-111111111111/discord-users"

        first = await admin_client.get(path, headers=headers)
        assert first.status == 200
        second = await admin_client.get(path, headers=headers)
        assert second.status == 401
        data = await second.json()
        assert "replayed" in data["error"].lower()

    async def test_tenant_admin_list_users_success(self, admin_client, tenant_admin_manager):
        headers = {"X-API-Secret": "test-secret"}
        headers.update(_admin_headers(signing_secret="test-secret"))
        resp = await admin_client.get(
            "/admin/tenants/11111111-1111-1111-1111-111111111111/discord-users",
            headers=headers,
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["users"][0]["role"] == "admin"
        tenant_admin_manager.list_discord_users.assert_awaited_once()

    async def test_tenant_admin_put_secret_success(self, admin_client, tenant_admin_manager):
        headers = {"X-API-Secret": "test-secret"}
        headers.update(_admin_headers(signing_secret="test-secret", change_ticket_id="chg_1"))
        resp = await admin_client.put(
            "/admin/tenants/11111111-1111-1111-1111-111111111111/secrets/OPENAI_API_KEY",
            headers=headers,
            json={"value": "sk-live", "description": "api key"},
        )
        assert resp.status == 200
        data = await resp.json()
        assert data["ok"] is True
        assert data["secret"]["name"] == "OPENAI_API_KEY"
        tenant_admin_manager.set_secret.assert_awaited_once()

    async def test_tenant_admin_invalid_payload_returns_400(self, admin_client):
        headers = {"X-API-Secret": "test-secret"}
        headers.update(_admin_headers(signing_secret="test-secret"))
        resp = await admin_client.put(
            "/admin/tenants/11111111-1111-1111-1111-111111111111/discord-bindings/channels/20",
            headers=headers,
            json={"priority": 1},
        )
        assert resp.status == 400

    async def test_tenant_admin_matrix_success_endpoints(self, admin_client, tenant_admin_manager):
        tenant_id = "11111111-1111-1111-1111-111111111111"

        async def _call(method: str, path: str, json_payload: dict | None = None):
            headers = {"X-API-Secret": "test-secret"}
            headers.update(_admin_headers(signing_secret="test-secret", change_ticket_id="chg-1"))
            kwargs = {"headers": headers}
            if json_payload is not None:
                kwargs["json"] = json_payload
            return await getattr(admin_client, method)(path, **kwargs)

        assert (await _call("get", f"/admin/tenants/{tenant_id}/discord-users")).status == 200
        assert (
            await _call(
                "post",
                f"/admin/tenants/{tenant_id}/discord-users",
                {"discord_user_id": 33, "role": "admin"},
            )
        ).status == 201
        assert (
            await _call(
                "delete",
                f"/admin/tenants/{tenant_id}/discord-users/33",
            )
        ).status == 200
        assert (
            await _call(
                "patch",
                f"/admin/tenants/{tenant_id}/discord-users/33/role",
                {"role": "restricted"},
            )
        ).status == 200
        assert (await _call("get", f"/admin/tenants/{tenant_id}/discord-bindings")).status == 200
        assert (
            await _call(
                "put",
                f"/admin/tenants/{tenant_id}/discord-bindings/guilds/10",
                {"priority": 10, "is_active": True},
            )
        ).status == 200
        assert (
            await _call(
                "put",
                f"/admin/tenants/{tenant_id}/discord-bindings/channels/20",
                {"guild_id": 10, "priority": 1, "is_active": True},
            )
        ).status == 200
        assert (
            await _call(
                "delete",
                f"/admin/tenants/{tenant_id}/discord-bindings/channels/20",
            )
        ).status == 200
        assert (await _call("get", f"/admin/tenants/{tenant_id}/settings")).status == 200
        assert (
            await _call(
                "put",
                f"/admin/tenants/{tenant_id}/settings/models/default_provider",
                {"value": "groq", "data_type": "string"},
            )
        ).status == 200
        assert (
            await _call(
                "delete",
                f"/admin/tenants/{tenant_id}/settings/models/default_provider",
            )
        ).status == 200
        assert (await _call("get", f"/admin/tenants/{tenant_id}/secrets")).status == 200
        assert (
            await _call(
                "delete",
                f"/admin/tenants/{tenant_id}/secrets/OPENAI_API_KEY",
            )
        ).status == 200
        assert (await _call("get", f"/admin/tenants/{tenant_id}/audit?limit=50")).status == 200

        tenant_admin_manager.upsert_discord_user.assert_awaited_once()
        tenant_admin_manager.delete_discord_user.assert_awaited_once()
        tenant_admin_manager.update_discord_user_role.assert_awaited_once()
        tenant_admin_manager.put_guild_binding.assert_awaited_once()
        tenant_admin_manager.put_channel_binding.assert_awaited_once()
        tenant_admin_manager.delete_channel_binding.assert_awaited_once()
        tenant_admin_manager.set_setting.assert_awaited_once()
        tenant_admin_manager.delete_setting.assert_awaited_once()
        tenant_admin_manager.delete_secret.assert_awaited_once()
        tenant_admin_manager.list_audit.assert_awaited_once()

    async def test_tenant_admin_email_matrix_success(self, admin_client, tenant_admin_manager):
        tenant_id = "11111111-1111-1111-1111-111111111111"

        async def _call(method: str, path: str, json_payload: dict | None = None):
            headers = {"X-API-Secret": "test-secret"}
            headers.update(_admin_headers(signing_secret="test-secret", change_ticket_id="chg-1"))
            kwargs = {"headers": headers}
            if json_payload is not None:
                kwargs["json"] = json_payload
            return await getattr(admin_client, method)(path, **kwargs)

        assert (
            await _call(
                "get",
                f"/admin/tenants/{tenant_id}/email/providers/google/oauth-app",
            )
        ).status == 200
        assert (
            await _call(
                "put",
                f"/admin/tenants/{tenant_id}/email/providers/google/oauth-app",
                {
                    "redirect_uri": "https://cgs.example.com/callback",
                    "client_id": "client-id",
                    "client_secret": "client-secret",
                    "enabled": True,
                },
            )
        ).status == 200
        assert (
            await _call(
                "post",
                f"/admin/tenants/{tenant_id}/email/oauth/google/start",
                {"provider": "google"},
            )
        ).status == 201
        assert (
            await _call(
                "post",
                f"/admin/tenants/{tenant_id}/email/oauth/google/exchange",
                {"code": "abc", "state": "state-1"},
            )
        ).status == 200
        assert (await _call("get", f"/admin/tenants/{tenant_id}/email/accounts")).status == 200
        assert (
            await _call(
                "patch",
                f"/admin/tenants/{tenant_id}/email/accounts/acc-1",
                {"status": "degraded"},
            )
        ).status == 200
        assert (
            await _call(
                "post",
                f"/admin/tenants/{tenant_id}/email/accounts/acc-1/sync",
                {"direction": "bi_directional"},
            )
        ).status == 200
        assert (await _call("get", f"/admin/tenants/{tenant_id}/email/critical")).status == 200
        assert (await _call("get", f"/admin/tenants/{tenant_id}/email/insights")).status == 200
        assert (
            await _call(
                "post",
                f"/admin/tenants/{tenant_id}/email/insights/reindex",
                {"insight_type": "critical_email"},
            )
        ).status == 200
        assert (
            await _call(
                "get",
                f"/admin/tenants/{tenant_id}/email/calendars?account_id=acc-1",
            )
        ).status == 200
        assert (
            await _call(
                "put",
                f"/admin/tenants/{tenant_id}/email/accounts/acc-1/calendar-primary",
                {"calendar_id": "primary"},
            )
        ).status == 200
        assert (
            await _call(
                "delete",
                f"/admin/tenants/{tenant_id}/email/accounts/acc-1",
            )
        ).status == 200

        tenant_admin_manager.put_email_provider_config.assert_awaited_once()
        tenant_admin_manager.create_email_oauth_start.assert_awaited_once()
        tenant_admin_manager.exchange_google_oauth_code.assert_awaited_once()
        tenant_admin_manager.list_email_accounts.assert_awaited_once()
        tenant_admin_manager.patch_email_account.assert_awaited_once()
        tenant_admin_manager.sync_email_account.assert_awaited_once()
        tenant_admin_manager.list_email_critical_items.assert_awaited_once()
        tenant_admin_manager.list_email_insights.assert_awaited_once()
        tenant_admin_manager.reindex_email_insights.assert_awaited_once()
        tenant_admin_manager.list_google_calendars.assert_awaited_once()
        tenant_admin_manager.set_email_primary_calendar.assert_awaited_once()
        tenant_admin_manager.delete_email_account.assert_awaited_once()

    async def test_tenant_admin_messaging_matrix_success(self, admin_client, tenant_admin_manager):
        tenant_id = "11111111-1111-1111-1111-111111111111"

        async def _call(method: str, path: str, json_payload: dict | None = None):
            headers = {"X-API-Secret": "test-secret"}
            headers.update(_admin_headers(signing_secret="test-secret", change_ticket_id="chg-1"))
            kwargs = {"headers": headers}
            if json_payload is not None:
                kwargs["json"] = json_payload
            return await getattr(admin_client, method)(path, **kwargs)

        assert (
            await _call(
                "get",
                f"/admin/tenants/{tenant_id}/messaging/providers/whatsapp/config",
            )
        ).status == 200
        assert (
            await _call(
                "put",
                f"/admin/tenants/{tenant_id}/messaging/providers/whatsapp/config",
                {
                    "enabled": True,
                    "bridge_mode": "local_sidecar",
                    "account_ref": "acct-1",
                    "session_ref": "sess-1",
                    "metadata": {"label": "phone-main"},
                },
            )
        ).status == 200
        assert (
            await _call(
                "put",
                f"/admin/tenants/{tenant_id}/messaging/chats/chat-1/policy",
                {
                    "provider": "whatsapp",
                    "read_enabled": True,
                    "send_enabled": True,
                    "retention_days": 14,
                    "metadata": {"label": "Family"},
                },
            )
        ).status == 200
        assert (
            await _call(
                "get",
                f"/admin/tenants/{tenant_id}/messaging/chats/chat-1/policy?provider=whatsapp",
            )
        ).status == 200
        assert (
            await _call(
                "get",
                f"/admin/tenants/{tenant_id}/messaging/chats?provider=whatsapp",
            )
        ).status == 200
        assert (
            await _call(
                "get",
                f"/admin/tenants/{tenant_id}/messaging/messages?provider=whatsapp&chat_id=chat-1",
            )
        ).status == 200
        assert (
            await _call(
                "get",
                f"/admin/tenants/{tenant_id}/messaging/messages/export"
                "?provider=whatsapp&chat_id=chat-1&limit=50",
            )
        ).status == 200
        assert (
            await _call(
                "delete",
                f"/admin/tenants/{tenant_id}/messaging/messages",
                {
                    "provider": "whatsapp",
                    "chat_id": "chat-1",
                    "message_ids": ["11111111-1111-1111-1111-111111111111"],
                    "explicitly_elevated": True,
                },
            )
        ).status == 200
        assert (
            await _call(
                "post",
                f"/admin/tenants/{tenant_id}/messaging/messages/chat-1/send",
                {
                    "provider": "whatsapp",
                    "text": "hello from cgs",
                    "metadata": {"reason": "test"},
                },
            )
        ).status == 202
        assert (
            await _call(
                "get",
                f"/admin/tenants/{tenant_id}/security/events?limit=20",
            )
        ).status == 200
        assert (
            await _call(
                "get",
                f"/admin/tenants/{tenant_id}/security/dashboard?window_hours=24",
            )
        ).status == 200

        bridge_payload = {
            "event_type": "whatsapp.message.inbound",
            "provider": "whatsapp",
            "chat_id": "chat-1",
            "message_id": "88888888-8888-8888-8888-888888888888",
            "body_text": "inbound text",
        }
        raw_body = json.dumps(bridge_payload, separators=(",", ":"))
        nonce = uuid4().hex
        timestamp = str(int(datetime.now(UTC).timestamp()))
        signature = hmac.new(
            b"bridge-signing-secret",
            f"{tenant_id}.{timestamp}.{nonce}.{raw_body}".encode(),
            hashlib.sha256,
        ).hexdigest()
        ingest_headers = {
            "X-API-Secret": "test-secret",
            "Content-Type": "application/json",
            "X-Bridge-Timestamp": timestamp,
            "X-Bridge-Nonce": nonce,
            "X-Bridge-Signature": signature,
        }
        ingest = await admin_client.post(
            f"/admin/tenants/{tenant_id}/messaging/ingest",
            headers=ingest_headers,
            data=raw_body,
        )
        assert ingest.status == 202

        tenant_admin_manager.get_messaging_provider_config.assert_awaited_once()
        tenant_admin_manager.put_messaging_provider_config.assert_awaited_once()
        tenant_admin_manager.put_messaging_chat_policy.assert_awaited_once()
        tenant_admin_manager.get_messaging_chat_policy.assert_awaited_once()
        tenant_admin_manager.list_messaging_chats.assert_awaited_once()
        tenant_admin_manager.list_messaging_messages.assert_awaited_once()
        tenant_admin_manager.export_messaging_messages.assert_awaited_once()
        tenant_admin_manager.delete_messaging_messages.assert_awaited_once()
        tenant_admin_manager.queue_messaging_send.assert_awaited_once()
        tenant_admin_manager.ingest_messaging_message.assert_awaited_once()
        tenant_admin_manager.list_security_events.assert_awaited_once()
        tenant_admin_manager.get_security_dashboard.assert_awaited_once()

    async def test_tenant_admin_execution_plan_matrix_success(
        self, admin_client, tenant_admin_manager
    ):
        tenant_id = "11111111-1111-1111-1111-111111111111"

        async def _call(method: str, path: str, json_payload: dict | None = None):
            headers = {"X-API-Secret": "test-secret"}
            headers.update(_admin_headers(signing_secret="test-secret", change_ticket_id="chg-1"))
            kwargs = {"headers": headers}
            if json_payload is not None:
                kwargs["json"] = json_payload
            return await getattr(admin_client, method)(path, **kwargs)

        created = await _call(
            "post",
            f"/admin/tenants/{tenant_id}/execution/plans",
            {
                "title": "Night Build",
                "goal": "Ship overnight",
                "steps": ["Design schema", "Implement routes"],
                "execution_mode": "test",
            },
        )
        assert created.status == 201

        listed = await _call(
            "get",
            f"/admin/tenants/{tenant_id}/execution/plans?status=queued&limit=50",
        )
        assert listed.status == 200

        plan_id = "99999999-9999-9999-9999-999999999999"
        detail = await _call(
            "get",
            f"/admin/tenants/{tenant_id}/execution/plans/{plan_id}?include_steps=true",
        )
        assert detail.status == 200

        paused = await _call(
            "post",
            f"/admin/tenants/{tenant_id}/execution/plans/{plan_id}/pause",
            {},
        )
        assert paused.status == 200

        resumed = await _call(
            "post",
            f"/admin/tenants/{tenant_id}/execution/plans/{plan_id}/resume",
            {"immediate": True},
        )
        assert resumed.status == 200

        cancelled = await _call(
            "post",
            f"/admin/tenants/{tenant_id}/execution/plans/{plan_id}/cancel",
            {},
        )
        assert cancelled.status == 200

        tenant_admin_manager.create_execution_plan.assert_awaited_once()
        assert (
            tenant_admin_manager.create_execution_plan.await_args.kwargs["execution_mode"] == "test"
        )
        tenant_admin_manager.list_execution_plans.assert_awaited_once()
        tenant_admin_manager.get_execution_plan.assert_awaited_once()
        tenant_admin_manager.list_execution_plan_steps.assert_awaited_once()
        tenant_admin_manager.pause_execution_plan.assert_awaited_once()
        tenant_admin_manager.resume_execution_plan.assert_awaited_once()
        tenant_admin_manager.cancel_execution_plan.assert_awaited_once()

    async def test_tenant_admin_automerge_execute_success(
        self,
        admin_client,
        tenant_admin_manager,
        monkeypatch: pytest.MonkeyPatch,
    ):
        from zetherion_ai.skills.github.models import PullRequest

        class _FakeGitHubClient:
            def __init__(self, token: str, timeout: float):
                self.token = token
                self.timeout = timeout

            async def ensure_branch(self, *_args, **_kwargs):
                return {"created": True, "ref": "refs/heads/codex/automerge-1", "sha": "abc123"}

            async def find_open_pull_request(self, *_args, **_kwargs):
                return None

            async def create_pull_request(self, *_args, **_kwargs):
                return PullRequest(
                    number=45,
                    title="Automerge",
                    head_ref="codex/automerge-1",
                    base_ref="main",
                    additions=12,
                    deletions=2,
                    changed_files=2,
                    html_url="https://example.com/pr/45",
                )

            async def get_pull_request(self, *_args, **_kwargs):
                return PullRequest(
                    number=45,
                    title="Automerge",
                    head_ref="codex/automerge-1",
                    base_ref="main",
                    additions=12,
                    deletions=2,
                    changed_files=2,
                    html_url="https://example.com/pr/45",
                )

            async def list_pull_request_files(self, *_args, **_kwargs):
                return [{"filename": "src/app.py"}, {"filename": "tests/test_app.py"}]

            async def list_check_runs(self, *_args, **_kwargs):
                return [
                    {
                        "name": "zetherion/merge-readiness",
                        "status": "completed",
                        "conclusion": "success",
                    }
                ]

            async def merge_pull_request(self, *_args, **_kwargs):
                return {"merged": True, "message": "Pull Request successfully merged"}

            async def close(self):
                return None

        monkeypatch.setattr(
            "zetherion_ai.skills.server.GitHubClient",
            _FakeGitHubClient,
        )

        tenant_id = "11111111-1111-1111-1111-111111111111"
        headers = {"X-API-Secret": "test-secret"}
        headers.update(_admin_headers(signing_secret="test-secret", change_ticket_id="chg-1"))
        response = await admin_client.post(
            f"/admin/tenants/{tenant_id}/automerge/execute",
            headers=headers,
            json={
                "repository": "openclaw/openclaw",
                "base_branch": "main",
                "source_ref": "main",
                "head_branch": "codex/automerge-1",
                "branch_guard_passed": True,
                "risk_guard_passed": True,
                "required_checks": ["zetherion/merge-readiness"],
                "allowed_paths": ["src/", "tests/"],
                "max_changed_files": 10,
                "max_additions": 500,
                "max_deletions": 250,
            },
        )
        assert response.status == 200
        payload = await response.json()
        assert payload["ok"] is True
        assert payload["result"]["status"] == "merged"
        assert payload["result"]["pr_number"] == 45
        tenant_admin_manager.record_admin_event.assert_awaited_once()

    async def test_tenant_admin_automerge_denied_by_policy(
        self,
        mock_registry,
        tenant_admin_manager,
        monkeypatch: pytest.MonkeyPatch,
    ):
        server = SkillsServer(
            registry=mock_registry,
            api_secret="test-secret",
            tenant_admin_manager=tenant_admin_manager,
        )
        server._trust_policy_evaluator = SimpleNamespace(
            evaluate=lambda **_kwargs: SimpleNamespace(
                allowed=False,
                approval_required=False,
                status=403,
                code="AI_TRUST_POLICY_DENIED",
                message="Action is blocked by trust policy",
                details={"action": "automerge.execute"},
                requires_two_person=False,
            )
        )
        app = server.create_app()
        tenant_id = "11111111-1111-1111-1111-111111111111"

        class _FakeGitHubClient:
            def __init__(self, *_args, **_kwargs):
                raise AssertionError("GitHub client should not be constructed on deny")

        monkeypatch.setattr("zetherion_ai.skills.server.GitHubClient", _FakeGitHubClient)

        headers = {"X-API-Secret": "test-secret"}
        headers.update(_admin_headers(signing_secret="test-secret"))
        async with TestClient(TestServer(app)) as client:
            denied = await client.post(
                f"/admin/tenants/{tenant_id}/automerge/execute",
                headers=headers,
                json={
                    "repository": "openclaw/openclaw",
                    "branch_guard_passed": True,
                    "risk_guard_passed": True,
                },
            )
            assert denied.status == 403
            payload = await denied.json()
            assert payload["code"] == "AI_TRUST_POLICY_DENIED"

    async def test_tenant_admin_validation_error_branches(self, admin_client):
        tenant_id = "11111111-1111-1111-1111-111111111111"

        def _headers() -> dict[str, str]:
            headers = {"X-API-Secret": "test-secret"}
            headers.update(_admin_headers(signing_secret="test-secret"))
            return headers

        missing_role = await admin_client.patch(
            f"/admin/tenants/{tenant_id}/discord-users/33/role",
            headers=_headers(),
            json={"role": ""},
        )
        assert missing_role.status == 400

        missing_setting_value = await admin_client.put(
            f"/admin/tenants/{tenant_id}/settings/models/default_provider",
            headers=_headers(),
            json={},
        )
        assert missing_setting_value.status == 400

        invalid_binding_bool = await admin_client.put(
            f"/admin/tenants/{tenant_id}/discord-bindings/guilds/10",
            headers=_headers(),
            json={"is_active": "maybe"},
        )
        assert invalid_binding_bool.status == 400

        empty_secret = await admin_client.put(
            f"/admin/tenants/{tenant_id}/secrets/OPENAI_API_KEY",
            headers=_headers(),
            json={"value": ""},
        )
        assert empty_secret.status == 400

        invalid_limit = await admin_client.get(
            f"/admin/tenants/{tenant_id}/audit?limit=abc",
            headers=_headers(),
        )
        assert invalid_limit.status == 400

        missing_messaging_chat = await admin_client.get(
            f"/admin/tenants/{tenant_id}/messaging/messages",
            headers=_headers(),
        )
        assert missing_messaging_chat.status == 400

        invalid_messaging_metadata = await admin_client.put(
            f"/admin/tenants/{tenant_id}/messaging/providers/whatsapp/config",
            headers=_headers(),
            json={"metadata": "bad"},
        )
        assert invalid_messaging_metadata.status == 400

        invalid_chat_policy_metadata = await admin_client.put(
            f"/admin/tenants/{tenant_id}/messaging/chats/chat-1/policy",
            headers=_headers(),
            json={"metadata": "bad"},
        )
        assert invalid_chat_policy_metadata.status == 400

        invalid_include_inactive = await admin_client.get(
            f"/admin/tenants/{tenant_id}/messaging/chats?include_inactive=not-a-bool",
            headers=_headers(),
        )
        assert invalid_include_inactive.status == 400

        invalid_messages_limit = await admin_client.get(
            f"/admin/tenants/{tenant_id}/messaging/messages?chat_id=chat-1&limit=bad",
            headers=_headers(),
        )
        assert invalid_messages_limit.status == 400

        invalid_export_limit = await admin_client.get(
            f"/admin/tenants/{tenant_id}/messaging/messages/export?chat_id=chat-1&limit=bad",
            headers=_headers(),
        )
        assert invalid_export_limit.status == 400

        invalid_delete_message_ids = await admin_client.delete(
            f"/admin/tenants/{tenant_id}/messaging/messages",
            headers=_headers(),
            json={"provider": "whatsapp", "chat_id": "chat-1", "message_ids": "bad"},
        )
        assert invalid_delete_message_ids.status == 400

        invalid_send_metadata = await admin_client.post(
            f"/admin/tenants/{tenant_id}/messaging/messages/chat-1/send",
            headers=_headers(),
            json={"text": "hello", "metadata": "bad"},
        )
        assert invalid_send_metadata.status == 400

        invalid_execution_metadata = await admin_client.post(
            f"/admin/tenants/{tenant_id}/execution/plans",
            headers=_headers(),
            json={
                "title": "Night Build",
                "goal": "Ship overnight",
                "steps": ["Design schema"],
                "metadata": "bad",
            },
        )
        assert invalid_execution_metadata.status == 400

        invalid_execution_limit = await admin_client.get(
            f"/admin/tenants/{tenant_id}/execution/plans?limit=bad",
            headers=_headers(),
        )
        assert invalid_execution_limit.status == 400

        invalid_security_events_limit = await admin_client.get(
            f"/admin/tenants/{tenant_id}/security/events?limit=bad",
            headers=_headers(),
        )
        assert invalid_security_events_limit.status == 400

        invalid_security_dashboard_window = await admin_client.get(
            f"/admin/tenants/{tenant_id}/security/dashboard?window_hours=bad",
            headers=_headers(),
        )
        assert invalid_security_dashboard_window.status == 400

        invalid_automerge_checks = await admin_client.post(
            f"/admin/tenants/{tenant_id}/automerge/execute",
            headers=_headers(),
            json={
                "repository": "openclaw/openclaw",
                "branch_guard_passed": True,
                "risk_guard_passed": True,
                "required_checks": "CI/CD Pipeline",
            },
        )
        assert invalid_automerge_checks.status == 400

    async def test_tenant_admin_messaging_policy_denial_paths(
        self,
        mock_registry,
        tenant_admin_manager,
    ):
        server = SkillsServer(
            registry=mock_registry,
            api_secret="test-secret",
            tenant_admin_manager=tenant_admin_manager,
        )

        class _DenyReadsApproveSend:
            @staticmethod
            def evaluate(*, action: str, **_kwargs):
                if action == "messaging.read":
                    return SimpleNamespace(
                        allowed=False,
                        approval_required=False,
                        status=403,
                        code="AI_MESSAGING_CHAT_NOT_ALLOWLISTED",
                        message="Chat is not allowlisted for this action",
                        details={"chat_id": "chat-1"},
                        requires_two_person=False,
                    )
                return SimpleNamespace(
                    allowed=False,
                    approval_required=True,
                    status=409,
                    code="AI_APPROVAL_REQUIRED",
                    message="This action requires approval before apply",
                    details={"action": action},
                    requires_two_person=True,
                )

        server._trust_policy_evaluator = _DenyReadsApproveSend()
        app = server.create_app()
        tenant_id = "11111111-1111-1111-1111-111111111111"

        def _headers() -> dict[str, str]:
            headers = {"X-API-Secret": "test-secret"}
            headers.update(_admin_headers(signing_secret="test-secret"))
            return headers

        async with TestClient(TestServer(app)) as client:
            denied_read = await client.get(
                f"/admin/tenants/{tenant_id}/messaging/messages?chat_id=chat-1",
                headers=_headers(),
            )
            assert denied_read.status == 403
            denied_payload = await denied_read.json()
            assert denied_payload["code"] == "AI_MESSAGING_CHAT_NOT_ALLOWLISTED"

            denied_export = await client.get(
                f"/admin/tenants/{tenant_id}/messaging/messages/export?chat_id=chat-1",
                headers=_headers(),
            )
            assert denied_export.status == 403
            export_payload = await denied_export.json()
            assert export_payload["code"] == "AI_MESSAGING_CHAT_NOT_ALLOWLISTED"

            approval = await client.post(
                f"/admin/tenants/{tenant_id}/messaging/messages/chat-1/send",
                headers=_headers(),
                json={"text": "hello"},
            )
            assert approval.status == 409
            approval_payload = await approval.json()
            assert approval_payload["code"] == "AI_APPROVAL_REQUIRED"
            assert approval_payload["requires_two_person"] is True

            delete_approval = await client.delete(
                f"/admin/tenants/{tenant_id}/messaging/messages",
                headers=_headers(),
                json={"provider": "whatsapp", "chat_id": "chat-1"},
            )
            assert delete_approval.status == 409
            delete_payload = await delete_approval.json()
            assert delete_payload["code"] == "AI_APPROVAL_REQUIRED"
            assert delete_payload["requires_two_person"] is True

        tenant_admin_manager.queue_messaging_send.assert_not_awaited()
        tenant_admin_manager.delete_messaging_messages.assert_not_awaited()

    async def test_tenant_admin_send_messaging_denied_without_approval(
        self,
        mock_registry,
        tenant_admin_manager,
    ):
        server = SkillsServer(
            registry=mock_registry,
            api_secret="test-secret",
            tenant_admin_manager=tenant_admin_manager,
        )

        class _DenySend:
            @staticmethod
            def evaluate(*, action: str, **_kwargs):
                if action == "messaging.send":
                    return SimpleNamespace(
                        allowed=False,
                        approval_required=False,
                        status=403,
                        code="AI_TRUST_POLICY_DENIED",
                        message="Action is blocked by trust policy",
                        details={"action": action},
                        requires_two_person=False,
                    )
                return SimpleNamespace(
                    allowed=True,
                    approval_required=False,
                    status=200,
                    code="AI_OK",
                    message="Allowed",
                    details={},
                    requires_two_person=False,
                )

        server._trust_policy_evaluator = _DenySend()
        app = server.create_app()
        tenant_id = "11111111-1111-1111-1111-111111111111"
        headers = {"X-API-Secret": "test-secret"}
        headers.update(_admin_headers(signing_secret="test-secret"))

        async with TestClient(TestServer(app)) as client:
            denied = await client.post(
                f"/admin/tenants/{tenant_id}/messaging/messages/chat-1/send",
                headers=headers,
                json={"text": "hello"},
            )
            assert denied.status == 403
            payload = await denied.json()
            assert payload["code"] == "AI_TRUST_POLICY_DENIED"

        tenant_admin_manager.queue_messaging_send.assert_not_awaited()

    async def test_tenant_admin_messaging_get_routes_not_found(
        self,
        mock_registry,
        tenant_admin_manager,
    ):
        tenant_admin_manager.get_messaging_provider_config = AsyncMock(return_value=None)
        tenant_admin_manager.get_messaging_chat_policy = AsyncMock(return_value=None)
        server = SkillsServer(
            registry=mock_registry,
            api_secret="test-secret",
            tenant_admin_manager=tenant_admin_manager,
        )
        server._trust_policy_evaluator = SimpleNamespace(
            evaluate=lambda **_kwargs: SimpleNamespace(
                allowed=True,
                approval_required=False,
                status=200,
                code="AI_OK",
                message="Allowed",
                details={},
                requires_two_person=False,
            )
        )
        app = server.create_app()
        tenant_id = "11111111-1111-1111-1111-111111111111"

        def _headers() -> dict[str, str]:
            headers = {"X-API-Secret": "test-secret"}
            headers.update(_admin_headers(signing_secret="test-secret"))
            return headers

        async with TestClient(TestServer(app)) as client:
            missing_provider = await client.get(
                f"/admin/tenants/{tenant_id}/messaging/providers/whatsapp/config",
                headers=_headers(),
            )
            assert missing_provider.status == 404

            missing_policy = await client.get(
                f"/admin/tenants/{tenant_id}/messaging/chats/chat-1/policy",
                headers=_headers(),
            )
            assert missing_policy.status == 404

    async def test_tenant_admin_get_messaging_chat_policy_bad_provider(
        self,
        mock_registry,
        tenant_admin_manager,
    ):
        tenant_admin_manager.get_messaging_chat_policy = AsyncMock(
            side_effect=ValueError("Unsupported messaging provider 'bad'")
        )
        server = SkillsServer(
            registry=mock_registry,
            api_secret="test-secret",
            tenant_admin_manager=tenant_admin_manager,
        )
        server._trust_policy_evaluator = SimpleNamespace(
            evaluate=lambda **_kwargs: SimpleNamespace(
                allowed=True,
                approval_required=False,
                status=200,
                code="AI_OK",
                message="Allowed",
                details={},
                requires_two_person=False,
            )
        )
        app = server.create_app()
        tenant_id = "11111111-1111-1111-1111-111111111111"
        headers = {"X-API-Secret": "test-secret"}
        headers.update(_admin_headers(signing_secret="test-secret"))

        async with TestClient(TestServer(app)) as client:
            bad_provider = await client.get(
                f"/admin/tenants/{tenant_id}/messaging/chats/chat-1/policy?provider=bad",
                headers=headers,
            )
            assert bad_provider.status == 400

    async def test_tenant_admin_returns_501_when_manager_missing(self, mock_registry):
        server = SkillsServer(
            registry=mock_registry, api_secret="test-secret", tenant_admin_manager=None
        )
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            tenant_id = "11111111-1111-1111-1111-111111111111"

            def _headers() -> dict[str, str]:
                headers = {"X-API-Secret": "test-secret"}
                headers.update(_admin_headers(signing_secret="test-secret"))
                return headers

            assert (
                await client.get(f"/admin/tenants/{tenant_id}/discord-users", headers=_headers())
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/discord-users",
                    headers=_headers(),
                    json={"discord_user_id": 1},
                )
            ).status == 501
            assert (
                await client.delete(
                    f"/admin/tenants/{tenant_id}/discord-users/1",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.patch(
                    f"/admin/tenants/{tenant_id}/discord-users/1/role",
                    headers=_headers(),
                    json={"role": "admin"},
                )
            ).status == 501
            assert (
                await client.get(f"/admin/tenants/{tenant_id}/discord-bindings", headers=_headers())
            ).status == 501
            assert (
                await client.put(
                    f"/admin/tenants/{tenant_id}/discord-bindings/guilds/10",
                    headers=_headers(),
                    json={"priority": 1},
                )
            ).status == 501
            assert (
                await client.put(
                    f"/admin/tenants/{tenant_id}/discord-bindings/channels/20",
                    headers=_headers(),
                    json={"guild_id": 10},
                )
            ).status == 501
            assert (
                await client.delete(
                    f"/admin/tenants/{tenant_id}/discord-bindings/channels/20",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.get(f"/admin/tenants/{tenant_id}/settings", headers=_headers())
            ).status == 501
            assert (
                await client.put(
                    f"/admin/tenants/{tenant_id}/settings/models/default_provider",
                    headers=_headers(),
                    json={"value": "groq"},
                )
            ).status == 501
            assert (
                await client.delete(
                    f"/admin/tenants/{tenant_id}/settings/models/default_provider",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.get(f"/admin/tenants/{tenant_id}/secrets", headers=_headers())
            ).status == 501
            assert (
                await client.put(
                    f"/admin/tenants/{tenant_id}/secrets/OPENAI_API_KEY",
                    headers=_headers(),
                    json={"value": "sk-live"},
                )
            ).status == 501
            assert (
                await client.delete(
                    f"/admin/tenants/{tenant_id}/secrets/OPENAI_API_KEY",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.get(f"/admin/tenants/{tenant_id}/audit", headers=_headers())
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/email/providers/google/oauth-app",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.put(
                    f"/admin/tenants/{tenant_id}/email/providers/google/oauth-app",
                    headers=_headers(),
                    json={"redirect_uri": "https://cgs.example.com/callback"},
                )
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/email/oauth/google/start",
                    headers=_headers(),
                    json={"provider": "google"},
                )
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/email/oauth/google/exchange",
                    headers=_headers(),
                    json={"code": "abc", "state": "xyz"},
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/email/accounts",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.patch(
                    f"/admin/tenants/{tenant_id}/email/accounts/acc-1",
                    headers=_headers(),
                    json={"status": "connected"},
                )
            ).status == 501
            assert (
                await client.delete(
                    f"/admin/tenants/{tenant_id}/email/accounts/acc-1",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/email/accounts/acc-1/sync",
                    headers=_headers(),
                    json={"direction": "bi_directional"},
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/email/critical",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/email/insights",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/email/insights/reindex",
                    headers=_headers(),
                    json={},
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/email/calendars?account_id=acc-1",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.put(
                    f"/admin/tenants/{tenant_id}/email/accounts/acc-1/calendar-primary",
                    headers=_headers(),
                    json={"calendar_id": "primary"},
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/messaging/providers/whatsapp/config",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.put(
                    f"/admin/tenants/{tenant_id}/messaging/providers/whatsapp/config",
                    headers=_headers(),
                    json={"enabled": True},
                )
            ).status == 501
            assert (
                await client.put(
                    f"/admin/tenants/{tenant_id}/messaging/chats/chat-1/policy",
                    headers=_headers(),
                    json={"read_enabled": True, "send_enabled": True},
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/messaging/chats/chat-1/policy",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/messaging/chats",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/messaging/messages?chat_id=chat-1",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/messaging/messages/export?chat_id=chat-1",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.delete(
                    f"/admin/tenants/{tenant_id}/messaging/messages",
                    headers=_headers(),
                    json={"chat_id": "chat-1"},
                )
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/messaging/messages/chat-1/send",
                    headers=_headers(),
                    json={"text": "hello"},
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/security/events",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/security/dashboard",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/automerge/execute",
                    headers=_headers(),
                    json={
                        "repository": "openclaw/openclaw",
                        "branch_guard_passed": True,
                        "risk_guard_passed": True,
                    },
                )
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/messaging/ingest",
                    headers={"X-API-Secret": "test-secret"},
                    json={"event_type": "whatsapp.message.inbound", "chat_id": "chat-1"},
                )
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/execution/plans",
                    headers=_headers(),
                    json={
                        "title": "Night Build",
                        "goal": "Ship overnight",
                        "steps": ["Design schema"],
                    },
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/execution/plans",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.get(
                    f"/admin/tenants/{tenant_id}/execution/plans/plan-1",
                    headers=_headers(),
                )
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/execution/plans/plan-1/pause",
                    headers=_headers(),
                    json={},
                )
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/execution/plans/plan-1/resume",
                    headers=_headers(),
                    json={},
                )
            ).status == 501
            assert (
                await client.post(
                    f"/admin/tenants/{tenant_id}/execution/plans/plan-1/cancel",
                    headers=_headers(),
                    json={},
                )
            ).status == 501


class TestServerHelperFunctions:
    """Tests for standalone skills.server helper functions."""

    def test_resolve_updater_secret_prefers_env(self, tmp_path):
        """Explicit UPDATER_SECRET should override file fallback."""
        secret_path = tmp_path / ".updater-secret"
        secret_path.write_text("file-secret", encoding="utf-8")
        with patch.dict(
            os.environ,
            {"UPDATER_SECRET": "env-secret", "UPDATER_SECRET_PATH": str(secret_path)},
            clear=False,
        ):
            assert _resolve_updater_secret() == "env-secret"

    def test_resolve_updater_secret_reads_file(self, tmp_path):
        """When env is absent, updater secret should be loaded from file."""
        secret_path = tmp_path / ".updater-secret"
        secret_path.write_text("file-secret", encoding="utf-8")
        with patch.dict(
            os.environ,
            {"UPDATER_SECRET": "", "UPDATER_SECRET_PATH": str(secret_path)},
            clear=False,
        ):
            assert _resolve_updater_secret() == "file-secret"

    def test_resolve_updater_secret_missing_file_returns_empty(self, tmp_path):
        """Missing fallback secret file should return an empty secret."""
        secret_path = tmp_path / ".missing-updater-secret"
        with patch.dict(
            os.environ,
            {"UPDATER_SECRET": "", "UPDATER_SECRET_PATH": str(secret_path)},
            clear=False,
        ):
            assert _resolve_updater_secret() == ""

    def test_resolve_updater_secret_read_error_returns_empty(self, tmp_path):
        """Read errors from secret file should fail closed to empty string."""
        secret_path = tmp_path / ".updater-secret"
        secret_path.write_text("file-secret", encoding="utf-8")
        with (
            patch.dict(
                os.environ,
                {"UPDATER_SECRET": "", "UPDATER_SECRET_PATH": str(secret_path)},
                clear=False,
            ),
            patch("pathlib.Path.read_text", side_effect=OSError("read failed")),
        ):
            assert _resolve_updater_secret() == ""

    def test_resolve_google_oauth_success(self):
        """Google OAuth resolver should merge dynamic settings and secrets."""
        secret_obj = MagicMock()
        secret_obj.get_secret_value.return_value = "env-secret"
        settings = SimpleNamespace(
            google_client_id="env-client-id",
            google_redirect_uri="https://env.example/callback",
            google_client_secret=secret_obj,
        )

        def _dynamic(_namespace: str, key: str, default=None):
            if key == "google_client_id":
                return "dynamic-client-id"
            if key == "google_redirect_uri":
                return "https://dynamic.example/callback"
            return default

        with (
            patch("zetherion_ai.config.get_dynamic", side_effect=_dynamic),
            patch("zetherion_ai.config.get_secret", return_value="dynamic-secret"),
            patch("zetherion_ai.skills.gmail.auth.GmailAuth") as mock_auth_cls,
        ):
            auth = _resolve_google_oauth(settings=settings)

        assert auth is mock_auth_cls.return_value
        mock_auth_cls.assert_called_once_with(
            client_id="dynamic-client-id",
            client_secret="dynamic-secret",
            redirect_uri="https://dynamic.example/callback",
        )

    def test_resolve_google_oauth_missing_client_id(self):
        """Missing client id should raise a clear ValueError."""
        settings = SimpleNamespace(
            google_client_id="",
            google_redirect_uri="https://example/callback",
            google_client_secret=SimpleNamespace(get_secret_value=lambda: "env-secret"),
        )
        with (
            patch(
                "zetherion_ai.config.get_dynamic",
                side_effect=(
                    lambda _n, key, default=None: "" if key == "google_client_id" else default
                ),
            ),
            patch("zetherion_ai.config.get_secret", return_value="dynamic-secret"),
            pytest.raises(ValueError, match="client id"),
        ):
            _resolve_google_oauth(settings=settings)

    def test_resolve_google_oauth_missing_secret(self):
        """Missing client secret should raise a clear ValueError."""
        settings = SimpleNamespace(
            google_client_id="client-id",
            google_redirect_uri="https://example/callback",
            google_client_secret=SimpleNamespace(get_secret_value=lambda: None),
        )
        with (
            patch(
                "zetherion_ai.config.get_dynamic",
                side_effect=lambda _n, _k, default=None: default,
            ),
            patch("zetherion_ai.config.get_secret", return_value=""),
            pytest.raises(ValueError, match="client secret"),
        ):
            _resolve_google_oauth(settings=settings)

    def test_resolve_google_oauth_missing_redirect(self):
        """Missing redirect URI should raise a clear ValueError."""
        settings = SimpleNamespace(
            google_client_id="client-id",
            google_redirect_uri="",
            google_client_secret=SimpleNamespace(get_secret_value=lambda: "env-secret"),
        )
        with (
            patch(
                "zetherion_ai.config.get_dynamic",
                side_effect=lambda _n, _k, default=None: default,
            ),
            patch("zetherion_ai.config.get_secret", return_value="dynamic-secret"),
            pytest.raises(ValueError, match="redirect URI"),
        ):
            _resolve_google_oauth(settings=settings)

    async def test_google_oauth_authorize_handler_success(self):
        """Authorize handler should return provider URL/state payload."""
        auth = MagicMock()
        auth.generate_auth_url.return_value = ("https://example.test/auth", "state-123")
        handler = _build_google_oauth_authorize_handler(auth_resolver=lambda: auth)

        request = MagicMock()
        request.query = {"user_id": "42"}
        result = await handler(request)

        assert result["ok"] is True
        assert result["provider"] == "google"
        assert result["user_id"] == 42
        assert result["auth_url"] == "https://example.test/auth"
        assert result["state"] == "state-123"

    async def test_google_oauth_authorize_handler_requires_user_id(self):
        """Authorize handler should require a user_id query parameter."""
        handler = _build_google_oauth_authorize_handler(auth_resolver=lambda: MagicMock())
        request = MagicMock()
        request.query = {}
        with pytest.raises(ValueError, match="Missing user_id"):
            await handler(request)

    async def test_google_oauth_authorize_handler_rejects_invalid_user_id(self):
        """Authorize handler should validate user_id as integer."""
        handler = _build_google_oauth_authorize_handler(auth_resolver=lambda: MagicMock())
        request = MagicMock()
        request.query = {"user_id": "abc"}
        with pytest.raises(ValueError, match="Invalid user_id"):
            await handler(request)

    async def test_google_oauth_callback_handler_success_with_refresh_fallback(self):
        """Callback handler should reuse existing refresh token when omitted."""
        auth = MagicMock()
        auth.validate_state_token.return_value = 77
        auth.exchange_code = AsyncMock(
            return_value={
                "access_token": "access-token",
                "expires_in": 1800,
                "scope": "email profile",
            }
        )
        auth.get_user_email = AsyncMock(return_value="dev@example.com")

        account_manager = AsyncMock()
        account_manager.get_account_by_email = AsyncMock(
            return_value=SimpleNamespace(refresh_token="existing-refresh-token")
        )
        account_manager.add_account = AsyncMock(return_value=1001)

        integration_storage = AsyncMock()
        handler = _build_google_oauth_handler(
            auth_resolver=lambda: auth,
            account_manager=account_manager,
            integration_storage=integration_storage,
        )

        request = MagicMock()
        request.query = {"code": "auth-code", "state": "state-token"}
        result = await handler(request)

        assert result["ok"] is True
        assert result["provider"] == "google"
        assert result["user_id"] == 77
        assert result["account_email"] == "dev@example.com"
        assert result["account_id"] == 1001

        kwargs = account_manager.add_account.call_args.kwargs
        assert kwargs["refresh_token"] == "existing-refresh-token"
        assert kwargs["scopes"] == ["email", "profile"]
        integration_storage.upsert_account.assert_awaited_once()
        integration_storage.upsert_destination.assert_awaited_once()

    async def test_google_oauth_callback_handler_uses_returned_refresh_token(self):
        """Callback handler should skip fallback lookup when refresh_token is returned."""
        auth = MagicMock()
        auth.validate_state_token.return_value = 77
        auth.exchange_code = AsyncMock(
            return_value={
                "access_token": "access-token",
                "refresh_token": "returned-refresh-token",
                "expires_in": 3600,
                "scope": "email",
            }
        )
        auth.get_user_email = AsyncMock(return_value="dev@example.com")

        account_manager = AsyncMock()
        account_manager.get_account_by_email = AsyncMock()
        account_manager.add_account = AsyncMock(return_value=1002)
        integration_storage = AsyncMock()

        handler = _build_google_oauth_handler(
            auth_resolver=lambda: auth,
            account_manager=account_manager,
            integration_storage=integration_storage,
        )
        request = MagicMock()
        request.query = {"code": "auth-code", "state": "state-token"}

        result = await handler(request)

        assert result["ok"] is True
        kwargs = account_manager.add_account.call_args.kwargs
        assert kwargs["refresh_token"] == "returned-refresh-token"
        account_manager.get_account_by_email.assert_not_awaited()

    async def test_google_oauth_callback_handler_rejects_oauth_error(self):
        """Callback handler should surface provider-side OAuth errors."""
        auth = MagicMock()
        handler = _build_google_oauth_handler(
            auth_resolver=lambda: auth,
            account_manager=AsyncMock(),
            integration_storage=AsyncMock(),
        )
        request = MagicMock()
        request.query = {"error": "access_denied"}
        with pytest.raises(ValueError, match="Google OAuth failed"):
            await handler(request)

    async def test_google_oauth_callback_handler_requires_code_and_state(self):
        """Callback handler should require both code and state."""
        auth = MagicMock()
        handler = _build_google_oauth_handler(
            auth_resolver=lambda: auth,
            account_manager=AsyncMock(),
            integration_storage=AsyncMock(),
        )
        request = MagicMock()
        request.query = {"code": "", "state": ""}
        with pytest.raises(ValueError, match="Missing OAuth code/state"):
            await handler(request)

    async def test_google_oauth_callback_handler_requires_access_token(self):
        """Callback handler should fail when access_token is missing."""
        auth = MagicMock()
        auth.validate_state_token.return_value = 1
        auth.exchange_code = AsyncMock(return_value={"refresh_token": "refresh-token"})
        handler = _build_google_oauth_handler(
            auth_resolver=lambda: auth,
            account_manager=AsyncMock(),
            integration_storage=AsyncMock(),
        )
        request = MagicMock()
        request.query = {"code": "auth-code", "state": "state-token"}
        with pytest.raises(ValueError, match="access_token"):
            await handler(request)

    async def test_google_oauth_callback_handler_requires_email(self):
        """Callback handler should fail when account email is missing."""
        auth = MagicMock()
        auth.validate_state_token.return_value = 1
        auth.exchange_code = AsyncMock(
            return_value={"access_token": "token", "refresh_token": "refresh"}
        )
        auth.get_user_email = AsyncMock(return_value="")
        handler = _build_google_oauth_handler(
            auth_resolver=lambda: auth,
            account_manager=AsyncMock(),
            integration_storage=AsyncMock(),
        )
        request = MagicMock()
        request.query = {"code": "auth-code", "state": "state-token"}
        with pytest.raises(ValueError, match="account email"):
            await handler(request)

    async def test_google_oauth_callback_handler_requires_refresh_token(self):
        """Callback handler should fail when refresh token cannot be resolved."""
        auth = MagicMock()
        auth.validate_state_token.return_value = 1
        auth.exchange_code = AsyncMock(return_value={"access_token": "token", "refresh_token": ""})
        auth.get_user_email = AsyncMock(return_value="dev@example.com")

        account_manager = AsyncMock()
        account_manager.get_account_by_email = AsyncMock(return_value=None)

        handler = _build_google_oauth_handler(
            auth_resolver=lambda: auth,
            account_manager=account_manager,
            integration_storage=AsyncMock(),
        )
        request = MagicMock()
        request.query = {"code": "auth-code", "state": "state-token"}
        with pytest.raises(ValueError, match="refresh_token"):
            await handler(request)


class TestAdditionalEndpointBranches:
    """Additional branch coverage for OAuth and manager-not-configured paths."""

    async def test_oauth_callback_value_error_returns_400(self, mock_registry):
        """Provider callback ValueError should map to HTTP 400."""
        handler = AsyncMock(side_effect=ValueError("bad oauth request"))
        server = SkillsServer(registry=mock_registry, oauth_handlers={"google": handler})
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/oauth/google/callback", params={"code": "c", "state": "s"})
            assert resp.status == 400
            data = await resp.json()
            assert "bad oauth request" in data["error"]

    async def test_oauth_callback_exception_returns_500(self, mock_registry):
        """Provider callback exceptions should map to HTTP 500."""
        handler = AsyncMock(side_effect=RuntimeError("unexpected"))
        server = SkillsServer(registry=mock_registry, oauth_handlers={"google": handler})
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/oauth/google/callback", params={"code": "c", "state": "s"})
            assert resp.status == 500
            data = await resp.json()
            assert "OAuth callback failed" in data["error"]

    async def test_oauth_authorize_value_error_returns_400(self, mock_registry):
        """Provider authorize ValueError should map to HTTP 400."""
        authorizer = AsyncMock(side_effect=ValueError("bad authorize request"))
        server = SkillsServer(
            registry=mock_registry,
            api_secret="test-secret",
            oauth_authorizers={"google": authorizer},
        )
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/oauth/google/authorize",
                params={"user_id": "1"},
                headers={"X-API-Secret": "test-secret"},
            )
            assert resp.status == 400
            data = await resp.json()
            assert "bad authorize request" in data["error"]

    async def test_oauth_authorize_exception_returns_500(self, mock_registry):
        """Provider authorize exceptions should map to HTTP 500."""
        authorizer = AsyncMock(side_effect=RuntimeError("unexpected"))
        server = SkillsServer(
            registry=mock_registry,
            api_secret="test-secret",
            oauth_authorizers={"google": authorizer},
        )
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/oauth/google/authorize",
                params={"user_id": "1"},
                headers={"X-API-Secret": "test-secret"},
            )
            assert resp.status == 500
            data = await resp.json()
            assert "OAuth authorize failed" in data["error"]

    async def test_handle_oauth_callback_missing_provider_returns_400(self, mock_registry):
        """Missing provider path segment should return HTTP 400."""
        server = SkillsServer(registry=mock_registry)
        request = MagicMock()
        request.match_info = {"provider": " "}
        resp = await server.handle_oauth_callback(request)
        assert resp.status == 400

    async def test_handle_oauth_authorize_missing_provider_returns_400(self, mock_registry):
        """Missing provider path segment should return HTTP 400."""
        server = SkillsServer(registry=mock_registry)
        request = MagicMock()
        request.match_info = {"provider": " "}
        resp = await server.handle_oauth_authorize(request)
        assert resp.status == 400

    async def test_gmail_alias_value_error_returns_400(self, mock_registry):
        """Legacy /gmail/callback should map ValueError to HTTP 400."""
        handler = AsyncMock(side_effect=ValueError("invalid grant"))
        server = SkillsServer(registry=mock_registry, oauth_handlers={"google": handler})
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/gmail/callback", params={"code": "c", "state": "s"})
            assert resp.status == 400
            data = await resp.json()
            assert "invalid grant" in data["error"]

    async def test_gmail_alias_exception_returns_500(self, mock_registry):
        """Legacy /gmail/callback should map unexpected errors to HTTP 500."""
        handler = AsyncMock(side_effect=RuntimeError("boom"))
        server = SkillsServer(registry=mock_registry, oauth_handlers={"google": handler})
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.get("/gmail/callback", params={"code": "c", "state": "s"})
            assert resp.status == 500
            data = await resp.json()
            assert "OAuth callback failed" in data["error"]

    async def test_handle_request_rejects_non_object_json(self, mock_registry):
        """POST /handle should reject JSON arrays and return HTTP 400."""
        server = SkillsServer(registry=mock_registry)
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/handle", json=["not", "an", "object"])
            assert resp.status == 400
            data = await resp.json()
            assert "JSON body must be an object" in data["error"]

    async def test_heartbeat_rejects_non_object_json(self, mock_registry):
        """POST /heartbeat should reject JSON arrays and return HTTP 400."""
        server = SkillsServer(registry=mock_registry)
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            resp = await client.post("/heartbeat", json=["not", "an", "object"])
            assert resp.status == 400
            data = await resp.json()
            assert "JSON body must be an object" in data["error"]

    async def test_user_routes_return_501_when_manager_missing(self, mock_registry):
        """User routes should fail with 501 when user manager is not configured."""
        server = SkillsServer(registry=mock_registry, user_manager=None)
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            add_resp = await client.post(
                "/users",
                json={"user_id": 1, "added_by": 1, "role": "user"},
            )
            del_resp = await client.delete("/users/1", params={"removed_by": "1"})
            role_resp = await client.patch("/users/1/role", json={"role": "admin", "changed_by": 1})
            audit_resp = await client.get("/users/audit")
            assert add_resp.status == 501
            assert del_resp.status == 501
            assert role_resp.status == 501
            assert audit_resp.status == 501

    async def test_settings_routes_return_501_when_manager_missing(self, mock_registry):
        """Settings routes should fail with 501 when settings manager is missing."""
        server = SkillsServer(registry=mock_registry, settings_manager=None)
        app = server.create_app()
        async with TestClient(TestServer(app)) as client:
            get_resp = await client.get("/settings/models/key")
            put_resp = await client.put(
                "/settings/models/key",
                json={"value": "x", "changed_by": 1, "data_type": "string"},
            )
            del_resp = await client.delete("/settings/models/key", params={"deleted_by": "1"})
            assert get_resp.status == 501
            assert put_resp.status == 501
            assert del_resp.status == 501


def _response_json(response: web.Response) -> dict[str, object]:
    return json.loads(response.text)


@pytest.mark.asyncio
async def test_runtime_external_services_domain_reports_healthy_blocked_and_exception(
    mock_registry,
):
    server = SkillsServer(registry=mock_registry)
    settings = SimpleNamespace(
        qdrant_owner_cert_path="",
        qdrant_owner_url="https://qdrant.internal",
    )

    healthy_response = MagicMock()
    healthy_response.raise_for_status.return_value = None
    healthy_response.json.return_value = {
        "result": {
            "collections": [
                {"name": "conversations"},
                {"name": "long_term_memory"},
                {"name": "user_profiles"},
            ]
        }
    }
    blocked_response = MagicMock()
    blocked_response.raise_for_status.return_value = None
    blocked_response.json.return_value = {
        "result": {
            "collections": [
                {"name": "conversations"},
                {"name": "long_term_memory"},
            ]
        }
    }

    async_client = AsyncMock()
    async_client.get = AsyncMock(
        side_effect=[healthy_response, blocked_response, RuntimeError("boom")]
    )
    async_client_ctx = MagicMock()
    async_client_ctx.__aenter__ = AsyncMock(return_value=async_client)
    async_client_ctx.__aexit__ = AsyncMock(return_value=False)

    with (
        patch("zetherion_ai.config.get_settings", return_value=settings),
        patch("zetherion_ai.skills.server.httpx.AsyncClient", return_value=async_client_ctx),
    ):
        healthy = await server._runtime_external_services_domain()  # noqa: SLF001
        blocked = await server._runtime_external_services_domain()  # noqa: SLF001
        failed = await server._runtime_external_services_domain()  # noqa: SLF001

    assert healthy["status"] == "healthy"
    assert blocked["status"] == "blocked"
    assert blocked["details"]["missing_collections"] == ["user_profiles"]
    assert failed["status"] == "blocked"
    assert failed["details"]["error"] == "boom"


@pytest.mark.asyncio
async def test_owner_ci_worker_register_and_heartbeat_handlers_cover_success_and_validation(
    mock_registry,
):
    storage = MagicMock()
    storage.register_worker_node = AsyncMock(
        return_value={"node_id": "node-1", "status": "active"}
    )
    storage.rotate_worker_session_credentials = AsyncMock(
        return_value={"session_id": "sess-1", "token": "secret"}
    )
    storage.heartbeat_worker_node = AsyncMock(
        return_value={"node_id": "node-1", "health_status": "healthy"}
    )
    server = SkillsServer(registry=mock_registry, owner_ci_storage=storage)
    server._verify_owner_ci_worker_signature = AsyncMock(  # type: ignore[method-assign]
        return_value={"session_id": "sess-1"}
    )

    register_request = MagicMock()
    register_request.text = AsyncMock(
        return_value=json.dumps(
            {
                "scope_id": "owner:owner-1",
                "node_id": "node-1",
                "node_name": "Windows worker",
                "capabilities": ["docker"],
                "rotate_credentials": True,
                "metadata": {"pool": "local"},
            }
        )
    )
    register_request.headers = {}

    register_response = await server.handle_owner_ci_worker_register_node(register_request)
    register_payload = _response_json(register_response)

    invalid_heartbeat_request = MagicMock()
    invalid_heartbeat_request.match_info = {"node_id": "node-1"}
    invalid_heartbeat_request.text = AsyncMock(
        return_value=json.dumps(
            {"scope_id": "owner:owner-1", "node_id": "node-2", "metadata": {}}
        )
    )
    invalid_heartbeat_request.headers = {}

    heartbeat_request = MagicMock()
    heartbeat_request.match_info = {"node_id": "node-1"}
    heartbeat_request.text = AsyncMock(
        return_value=json.dumps(
            {
                "scope_id": "owner:owner-1",
                "node_id": "node-1",
                "health_status": "healthy",
                "metadata": {"pool": "local"},
            }
        )
    )
    heartbeat_request.headers = {}

    invalid_heartbeat = await server.handle_owner_ci_worker_node_heartbeat(
        invalid_heartbeat_request
    )
    heartbeat_response = await server.handle_owner_ci_worker_node_heartbeat(
        heartbeat_request
    )

    assert register_response.status == 200
    assert register_payload["session"]["token"] == "secret"
    assert invalid_heartbeat.status == 400
    assert heartbeat_response.status == 200
    storage.register_worker_node.assert_awaited_once()
    storage.rotate_worker_session_credentials.assert_awaited_once()
    storage.heartbeat_worker_node.assert_awaited_once()


@pytest.mark.asyncio
async def test_owner_ci_worker_claim_and_submit_result_handlers_cover_success_and_replay(
    mock_registry,
):
    storage = MagicMock()
    storage.claim_worker_job = AsyncMock(
        return_value={
            "job_id": "job-1",
            "run_id": "run-1",
            "shard_id": "shard-1",
            "execution_target": "windows_local",
            "action": "ci.test.run",
            "runner": "docker",
            "required_capabilities": ["docker"],
            "artifact_contract": {"kind": "ci_shard"},
            "payload_json": {"runner": "docker", "execution_mode": "live"},
        }
    )
    storage.submit_worker_job_result = AsyncMock(
        side_effect=[
            {"accepted": True},
            RuntimeError("relay replay already accepted"),
        ]
    )
    server = SkillsServer(registry=mock_registry, owner_ci_storage=storage, api_secret="secret")
    server._verify_owner_ci_worker_signature = AsyncMock(  # type: ignore[method-assign]
        return_value={"session_id": "sess-1"}
    )
    server._check_auth = MagicMock(return_value=True)  # type: ignore[method-assign]

    claim_request = MagicMock()
    claim_request.match_info = {"node_id": "node-1"}
    claim_request.text = AsyncMock(
        return_value=json.dumps(
            {
                "scope_id": "owner:owner-1",
                "required_capabilities": ["docker"],
                "poll_after_seconds": 200,
            }
        )
    )
    claim_request.headers = {}

    claim_response = await server.handle_owner_ci_worker_claim_job(claim_request)
    claim_payload = _response_json(claim_response)

    submit_request = MagicMock()
    submit_request.match_info = {"node_id": "node-1", "job_id": "job-1"}
    submit_request.text = AsyncMock(
        return_value=json.dumps(
            {
                "scope_id": "owner:owner-1",
                "status": "succeeded",
                "output": {"summary": "ok"},
                "events": [{"event_type": "worker.result.accepted"}],
                "log_chunks": [{"stream": "stdout", "message": "ok"}],
                "resource_samples": [{"memory_mb": 512}],
                "debug_bundle": {"compose_state": {"bot": "running"}},
                "cleanup_receipt": {"status": "clean"},
                "container_receipts": [{"container": "bot"}],
                "steps": [{"step_id": "test", "status": "succeeded"}],
                "artifacts": [{"artifact_id": "coverage-summary"}],
                "evidence_references": [{"provider": "cloudwatch"}],
                "correlation_context": {"trace_ids": ["trace-1"]},
            }
        )
    )
    submit_request.headers = {}

    replay_request = MagicMock()
    replay_request.match_info = {"node_id": "node-1", "job_id": "job-1"}
    replay_request.text = AsyncMock(
        return_value=json.dumps({"scope_id": "owner:owner-1", "status": "succeeded"})
    )
    replay_request.headers = {"X-CI-Relay-Replay": "1"}

    submit_response = await server.handle_owner_ci_worker_submit_job_result(submit_request)
    replay_response = await server.handle_owner_ci_worker_submit_job_result(replay_request)
    replay_payload = _response_json(replay_response)

    assert claim_response.status == 200
    assert claim_payload["job"]["runner"] == "docker"
    assert claim_payload["poll_after_seconds"] == 120
    assert submit_response.status == 202
    assert replay_response.status == 202
    assert replay_payload["idempotent"] is True
    storage.claim_worker_job.assert_awaited_once()
    assert storage.submit_worker_job_result.await_count == 2
    first_submit_payload = storage.submit_worker_job_result.await_args_list[0].kwargs["payload"]
    assert first_submit_payload["output"]["steps"][0]["step_id"] == "test"
    assert first_submit_payload["output"]["artifacts"][0]["artifact_id"] == "coverage-summary"
    assert first_submit_payload["output"]["evidence_references"][0]["provider"] == "cloudwatch"
    assert first_submit_payload["output"]["correlation_context"]["trace_ids"] == ["trace-1"]


@pytest.mark.asyncio
async def test_owner_ci_worker_claim_handler_accepts_string_backed_json_fields(
    mock_registry,
):
    storage = MagicMock()
    storage.claim_worker_job = AsyncMock(
        return_value={
            "job_id": "job-1",
            "run_id": "run-1",
            "shard_id": "shard-1",
            "execution_target": "windows_local",
            "action": "worker.noop",
            "runner": "docker",
            "required_capabilities": json.dumps(["docker"]),
            "artifact_contract": json.dumps({"kind": "ci_shard"}),
            "payload_json": json.dumps({"runner": "docker", "execution_mode": "live"}),
        }
    )
    server = SkillsServer(registry=mock_registry, owner_ci_storage=storage, api_secret="secret")
    server._verify_owner_ci_worker_signature = AsyncMock(  # type: ignore[method-assign]
        return_value={"session_id": "sess-1"}
    )
    server._check_auth = MagicMock(return_value=True)  # type: ignore[method-assign]

    claim_request = MagicMock()
    claim_request.match_info = {"node_id": "node-1"}
    claim_request.text = AsyncMock(
        return_value=json.dumps(
            {
                "scope_id": "owner:owner-1",
                "required_capabilities": ["docker"],
            }
        )
    )
    claim_request.headers = {}

    claim_response = await server.handle_owner_ci_worker_claim_job(claim_request)
    claim_payload = _response_json(claim_response)

    assert claim_response.status == 200
    assert claim_payload["job"]["action"] == "worker.noop"
    assert claim_payload["job"]["required_capabilities"] == ["docker"]
    assert claim_payload["job"]["artifact_contract"] == {"kind": "ci_shard"}
    assert claim_payload["job"]["payload"]["execution_mode"] == "live"


@pytest.mark.asyncio
async def test_internal_runtime_health_endpoint_reports_blocked_queue_and_stale_bot(
    mock_registry,
):
    fake_pool = object()
    stale_updated_at = datetime(2025, 3, 13, 10, 0, tzinfo=UTC).isoformat()
    bot_status = {
        "service_name": "discord_bot",
        "status": "healthy",
        "summary": "Discord bot is connected.",
        "updated_at": stale_updated_at,
        "release_revision": "rev-123",
        "details": {
            "queue": {
                "accepting_work": False,
                "healthy": False,
                "workers": [{"worker_id": "worker-1", "status": "stale"}],
            }
        },
    }
    fake_store = SimpleNamespace(
        get_status=AsyncMock(
            side_effect=lambda service_name: bot_status if service_name == "discord_bot" else None
        )
    )
    server = SkillsServer(
        registry=mock_registry,
        api_secret="test-secret",
        user_manager=SimpleNamespace(_pool=fake_pool),
    )
    app = server.create_app()
    settings = SimpleNamespace(queue_stale_timeout_seconds=30)

    with (
        patch("zetherion_ai.skills.server.QueueStorage") as mock_queue_storage,
        patch("zetherion_ai.config.get_settings", return_value=settings),
        patch.dict(os.environ, {"APP_GIT_SHA": "rev-123"}, clear=False),
    ):
        queue_storage = mock_queue_storage.return_value
        queue_storage.get_status_counts = AsyncMock(
            return_value={"queued": 0, "processing": 2, "dead": 1}
        )
        queue_storage.count_stale_processing = AsyncMock(return_value=2)
        server._get_runtime_status_store = AsyncMock(return_value=fake_store)  # type: ignore[method-assign]
        server._runtime_external_services_domain = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "key": "external_services",
                "label": "External Services",
                "status": "blocked",
                "summary": "Missing required service evidence.",
                "details": {"missing_collections": ["user_profiles"]},
                "incident_type": "service_evidence_incomplete",
            }
        )

        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/internal/runtime/health",
                headers={"X-API-Secret": "test-secret"},
            )
            assert resp.status == 200
            data = await resp.json()

    domains = {domain["key"]: domain for domain in data["domains"]}
    assert domains["deploy_state"]["status"] == "healthy"
    assert domains["deploy_state"]["details"]["revision"] == "rev-123"
    assert domains["message_queue"]["status"] == "blocked"
    assert domains["message_queue"]["incident_type"] == "queue_lease_wedged"
    assert domains["message_queue"]["details"]["runtime"]["accepting_work"] is False
    assert domains["discord_bot"]["status"] == "blocked"
    assert domains["discord_bot"]["incident_type"] == "discord_delivery_failed"
    assert domains["external_services"]["status"] == "blocked"
    assert data["summary"]["blocker_count"] >= 2


@pytest.mark.asyncio
async def test_internal_runtime_health_uses_owner_ci_storage_pool_when_user_manager_missing(
    mock_registry,
):
    fake_pool = object()
    bot_status = {
        "service_name": "discord_bot",
        "status": "healthy",
        "summary": "Discord bot is connected.",
        "updated_at": datetime.now(UTC).isoformat(),
        "release_revision": "rev-owner-ci",
        "details": {
            "queue": {
                "accepting_work": True,
                "healthy": True,
            },
            "announcement_dispatcher": {
                "running": True,
            },
        },
    }
    fake_store = SimpleNamespace(
        get_status=AsyncMock(
            side_effect=lambda service_name: bot_status if service_name == "discord_bot" else None
        )
    )
    server = SkillsServer(
        registry=mock_registry,
        api_secret="test-secret",
        owner_ci_storage=SimpleNamespace(_pool=fake_pool),
    )
    app = server.create_app()
    settings = SimpleNamespace(queue_stale_timeout_seconds=30)

    with (
        patch("zetherion_ai.skills.server.QueueStorage") as mock_queue_storage,
        patch("zetherion_ai.config.get_settings", return_value=settings),
        patch.dict(os.environ, {"APP_GIT_SHA": "rev-owner-ci"}, clear=False),
    ):
        queue_storage = mock_queue_storage.return_value
        queue_storage.get_status_counts = AsyncMock(
            return_value={"queued": 1, "processing": 0, "dead": 0}
        )
        queue_storage.count_stale_processing = AsyncMock(return_value=0)
        server._get_runtime_status_store = AsyncMock(return_value=fake_store)  # type: ignore[method-assign]
        server._runtime_external_services_domain = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "key": "external_services",
                "label": "External Services",
                "status": "healthy",
                "summary": "Required external service dependencies are reachable.",
                "details": {},
                "incident_type": None,
            }
        )

        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/internal/runtime/health",
                headers={"X-API-Secret": "test-secret"},
            )
            assert resp.status == 200
            data = await resp.json()

    domains = {domain["key"]: domain for domain in data["domains"]}
    assert domains["message_queue"]["status"] == "healthy"
    assert domains["discord_bot"]["status"] == "healthy"
    assert domains["announcement_dispatcher"]["status"] == "healthy"
    assert domains["deploy_state"]["details"]["revision"] == "rev-owner-ci"
    assert domains["release_verification"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_internal_runtime_health_recovers_revision_from_runtime_status_when_env_is_missing(
    mock_registry,
):
    fake_pool = object()
    bot_status = {
        "service_name": "discord_bot",
        "status": "healthy",
        "summary": "Discord bot is connected.",
        "updated_at": datetime.now(UTC).isoformat(),
        "release_revision": "rev-from-status",
        "details": {
            "queue": {
                "accepting_work": True,
                "healthy": True,
            },
            "announcement_dispatcher": {
                "running": True,
            },
        },
    }
    fake_store = SimpleNamespace(
        get_status=AsyncMock(
            side_effect=lambda service_name: bot_status if service_name == "discord_bot" else None
        )
    )
    server = SkillsServer(
        registry=mock_registry,
        api_secret="test-secret",
        owner_ci_storage=SimpleNamespace(_pool=fake_pool),
    )
    app = server.create_app()
    settings = SimpleNamespace(queue_stale_timeout_seconds=30)

    with (
        patch("zetherion_ai.skills.server.QueueStorage") as mock_queue_storage,
        patch("zetherion_ai.config.get_settings", return_value=settings),
        patch.dict(
            os.environ,
            {"APP_GIT_SHA": "", "GITHUB_SHA": "", "RELEASE_SHA": ""},
            clear=False,
        ),
    ):
        queue_storage = mock_queue_storage.return_value
        queue_storage.get_status_counts = AsyncMock(
            return_value={"queued": 0, "processing": 0, "dead": 0}
        )
        queue_storage.count_stale_processing = AsyncMock(return_value=0)
        server._get_runtime_status_store = AsyncMock(return_value=fake_store)  # type: ignore[method-assign]
        server._runtime_external_services_domain = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "key": "external_services",
                "label": "External Services",
                "status": "healthy",
                "summary": "Required external service dependencies are reachable.",
                "details": {},
                "incident_type": None,
            }
        )

        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/internal/runtime/health",
                headers={"X-API-Secret": "test-secret"},
            )
            assert resp.status == 200
            data = await resp.json()

    domains = {domain["key"]: domain for domain in data["domains"]}
    assert domains["deploy_state"]["status"] == "healthy"
    assert domains["deploy_state"]["details"]["revision"] == "rev-from-status"
    assert domains["deploy_state"]["details"]["source"] == "discord_bot_runtime_status"


@pytest.mark.asyncio
async def test_internal_runtime_health_uses_direct_announcement_dispatcher_status(
    mock_registry,
):
    fake_pool = object()
    dispatcher_status = {
        "service_name": "announcement_dispatcher",
        "status": "healthy",
        "summary": "Announcement dispatcher is running.",
        "updated_at": datetime.now(UTC).isoformat(),
        "release_revision": "rev-dispatcher",
        "details": {
            "running": True,
            "last_processed_count": 2,
        },
    }
    fake_store = SimpleNamespace(
        get_status=AsyncMock(
            side_effect=lambda service_name: (
                dispatcher_status
                if service_name == "announcement_dispatcher"
                else None
            )
        )
    )
    server = SkillsServer(
        registry=mock_registry,
        api_secret="test-secret",
        owner_ci_storage=SimpleNamespace(_pool=fake_pool),
    )
    app = server.create_app()
    settings = SimpleNamespace(queue_stale_timeout_seconds=30)

    with (
        patch("zetherion_ai.skills.server.QueueStorage") as mock_queue_storage,
        patch("zetherion_ai.config.get_settings", return_value=settings),
        patch.dict(
            os.environ,
            {"APP_GIT_SHA": "", "GITHUB_SHA": "", "RELEASE_SHA": ""},
            clear=False,
        ),
    ):
        queue_storage = mock_queue_storage.return_value
        queue_storage.get_status_counts = AsyncMock(
            return_value={"queued": 0, "processing": 0, "dead": 0}
        )
        queue_storage.count_stale_processing = AsyncMock(return_value=0)
        server._get_runtime_status_store = AsyncMock(return_value=fake_store)  # type: ignore[method-assign]
        server._runtime_external_services_domain = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "key": "external_services",
                "label": "External Services",
                "status": "healthy",
                "summary": "Required external service dependencies are reachable.",
                "details": {},
                "incident_type": None,
            }
        )

        async with TestClient(TestServer(app)) as client:
            resp = await client.get(
                "/internal/runtime/health",
                headers={"X-API-Secret": "test-secret"},
            )
            assert resp.status == 200
            data = await resp.json()

    domains = {domain["key"]: domain for domain in data["domains"]}
    assert domains["announcement_dispatcher"]["status"] == "healthy"
    assert domains["announcement_dispatcher"]["details"]["service_status"] == "healthy"
    assert domains["deploy_state"]["status"] == "healthy"
    assert domains["deploy_state"]["details"]["revision"] == "rev-dispatcher"
    assert domains["deploy_state"]["details"]["source"] == "announcement_dispatcher_runtime_status"


@pytest.mark.asyncio
async def test_owner_ci_worker_bootstrap_handler_covers_success_and_error_paths(mock_registry):
    no_storage_server = SkillsServer(registry=mock_registry)
    no_storage_request = MagicMock()
    no_storage_request.text = AsyncMock(return_value="{}")
    no_storage_request.headers = {}
    no_storage_response = await no_storage_server.handle_owner_ci_worker_bootstrap(
        no_storage_request
    )
    assert no_storage_response.status == 501

    storage = MagicMock()
    storage.bootstrap_worker_node_session = AsyncMock(
        return_value={"session_id": "sess-1", "token": "bootstrap-token"}
    )
    storage.get_worker_node = AsyncMock(
        return_value={"node_id": "node-1", "status": "active", "label": "Worker"}
    )
    server = SkillsServer(registry=mock_registry, owner_ci_storage=storage)
    server._resolve_owner_ci_worker_bootstrap_secret = MagicMock(  # type: ignore[method-assign]
        return_value="bootstrap-secret"
    )

    invalid_json_request = MagicMock()
    invalid_json_request.text = AsyncMock(return_value="[]")
    invalid_json_request.headers = {}
    invalid_json_response = await server.handle_owner_ci_worker_bootstrap(invalid_json_request)

    missing_scope_request = MagicMock()
    missing_scope_request.text = AsyncMock(return_value=json.dumps({"node_id": "node-1"}))
    missing_scope_request.headers = {"X-Worker-Bootstrap-Secret": "bootstrap-secret"}
    missing_scope_response = await server.handle_owner_ci_worker_bootstrap(
        missing_scope_request
    )

    invalid_secret_request = MagicMock()
    invalid_secret_request.text = AsyncMock(
        return_value=json.dumps({"scope_id": "owner:owner-1", "node_id": "node-1"})
    )
    invalid_secret_request.headers = {"X-Worker-Bootstrap-Secret": "wrong"}
    invalid_secret_response = await server.handle_owner_ci_worker_bootstrap(
        invalid_secret_request
    )

    invalid_metadata_request = MagicMock()
    invalid_metadata_request.text = AsyncMock(
        return_value=json.dumps(
            {
                "scope_id": "owner:owner-1",
                "node_id": "node-1",
                "metadata": "not-an-object",
            }
        )
    )
    invalid_metadata_request.headers = {"X-Worker-Bootstrap-Secret": "bootstrap-secret"}
    invalid_metadata_response = await server.handle_owner_ci_worker_bootstrap(
        invalid_metadata_request
    )

    success_request = MagicMock()
    success_request.text = AsyncMock(
        return_value=json.dumps(
            {
                "scope_id": "owner:owner-1",
                "node_id": "node-1",
                "node_name": "Windows Worker",
                "capabilities": ["docker", "playwright"],
                "metadata": {"pool": "windows"},
            }
        )
    )
    success_request.headers = {"X-Worker-Bootstrap-Secret": "bootstrap-secret"}
    success_response = await server.handle_owner_ci_worker_bootstrap(success_request)
    success_payload = _response_json(success_response)

    storage.bootstrap_worker_node_session.side_effect = Exception("boom")
    failing_request = MagicMock()
    failing_request.text = AsyncMock(
        return_value=json.dumps(
            {
                "scope_id": "owner:owner-1",
                "node_id": "node-2",
                "capabilities": ["docker"],
            }
        )
    )
    failing_request.headers = {"X-Worker-Bootstrap-Secret": "bootstrap-secret"}
    failing_response = await server.handle_owner_ci_worker_bootstrap(failing_request)

    assert invalid_json_response.status == 400
    assert missing_scope_response.status == 400
    assert invalid_secret_response.status == 401
    assert invalid_metadata_response.status == 400
    assert success_response.status == 201
    assert success_payload["scope_id"] == "owner:owner-1"
    assert success_payload["capabilities"] == ["docker", "playwright"]
    assert success_payload["session"]["token"] == "bootstrap-token"
    assert failing_response.status == 502


@pytest.mark.asyncio
async def test_owner_ci_worker_bridge_error_paths_cover_registration_heartbeat_claim_and_submit(
    mock_registry,
):
    no_storage_server = SkillsServer(registry=mock_registry, api_secret="secret")

    register_request = MagicMock()
    register_request.text = AsyncMock(return_value=json.dumps({"scope_id": "owner:owner-1"}))
    register_request.headers = {}
    register_response = await no_storage_server.handle_owner_ci_worker_register_node(
        register_request
    )
    assert register_response.status == 501

    heartbeat_request = MagicMock()
    heartbeat_request.match_info = {"node_id": "node-1"}
    heartbeat_request.text = AsyncMock(return_value=json.dumps({"scope_id": "owner:owner-1"}))
    heartbeat_request.headers = {}
    heartbeat_response = await no_storage_server.handle_owner_ci_worker_node_heartbeat(
        heartbeat_request
    )
    assert heartbeat_response.status == 501

    claim_request = MagicMock()
    claim_request.match_info = {"node_id": "node-1"}
    claim_request.text = AsyncMock(return_value=json.dumps({"scope_id": "owner:owner-1"}))
    claim_request.headers = {}
    assert (await no_storage_server.handle_owner_ci_worker_claim_job(claim_request)).status == 501

    submit_request = MagicMock()
    submit_request.match_info = {"node_id": "node-1", "job_id": "job-1"}
    submit_request.text = AsyncMock(return_value=json.dumps({"scope_id": "owner:owner-1"}))
    submit_request.headers = {}
    assert (
        await no_storage_server.handle_owner_ci_worker_submit_job_result(submit_request)
    ).status == 501

    storage = MagicMock()
    storage.register_worker_node = AsyncMock(return_value={"node_id": "node-1"})
    storage.rotate_worker_session_credentials = AsyncMock(
        return_value={"session_id": "sess-1", "token": "token-1"}
    )
    storage.heartbeat_worker_node = AsyncMock(return_value={"node_id": "node-1"})
    storage.claim_worker_job = AsyncMock(return_value=None)
    storage.submit_worker_job_result = AsyncMock(return_value={"idempotent": False})
    server = SkillsServer(registry=mock_registry, owner_ci_storage=storage, api_secret="secret")
    server._verify_owner_ci_worker_signature = AsyncMock(  # type: ignore[method-assign]
        return_value={"session_id": "sess-1"}
    )
    server._check_auth = MagicMock(return_value=False)  # type: ignore[method-assign]

    bad_register_request = MagicMock()
    bad_register_request.text = AsyncMock(
        return_value=json.dumps(
            {
                "scope_id": "owner:owner-1",
                "node_id": "node-1",
                "metadata": "bad",
            }
        )
    )
    bad_register_request.headers = {}
    bad_register_response = await server.handle_owner_ci_worker_register_node(
        bad_register_request
    )

    storage.register_worker_node.side_effect = RuntimeError("session expired")
    runtime_register_request = MagicMock()
    runtime_register_request.text = AsyncMock(
        return_value=json.dumps({"scope_id": "owner:owner-1", "node_id": "node-1"})
    )
    runtime_register_request.headers = {}
    runtime_register_response = await server.handle_owner_ci_worker_register_node(
        runtime_register_request
    )
    storage.register_worker_node.side_effect = None

    storage.heartbeat_worker_node.side_effect = ValueError("heartbeat bad metadata")
    bad_heartbeat_request = MagicMock()
    bad_heartbeat_request.match_info = {"node_id": "node-1"}
    bad_heartbeat_request.text = AsyncMock(
        return_value=json.dumps({"scope_id": "owner:owner-1", "node_id": "node-1"})
    )
    bad_heartbeat_request.headers = {}
    bad_heartbeat_response = await server.handle_owner_ci_worker_node_heartbeat(
        bad_heartbeat_request
    )
    storage.heartbeat_worker_node.side_effect = None

    storage.claim_worker_job.side_effect = RuntimeError("lease lost")
    runtime_claim_request = MagicMock()
    runtime_claim_request.match_info = {"node_id": "node-1"}
    runtime_claim_request.text = AsyncMock(
        return_value=json.dumps({"scope_id": "owner:owner-1", "required_capabilities": []})
    )
    runtime_claim_request.headers = {}
    runtime_claim_response = await server.handle_owner_ci_worker_claim_job(runtime_claim_request)
    storage.claim_worker_job.side_effect = None

    idle_claim_request = MagicMock()
    idle_claim_request.match_info = {"node_id": "node-1"}
    idle_claim_request.text = AsyncMock(
        return_value=json.dumps(
            {"scope_id": "owner:owner-1", "required_capabilities": [], "poll_after_seconds": 1}
        )
    )
    idle_claim_request.headers = {}
    idle_claim_response = await server.handle_owner_ci_worker_claim_job(idle_claim_request)
    idle_claim_payload = _response_json(idle_claim_response)

    bad_submit_request = MagicMock()
    bad_submit_request.match_info = {"node_id": "node-1", "job_id": "job-1"}
    bad_submit_request.text = AsyncMock(
        return_value=json.dumps({"scope_id": "owner:owner-1", "output": "bad"})
    )
    bad_submit_request.headers = {}
    bad_submit_response = await server.handle_owner_ci_worker_submit_job_result(
        bad_submit_request
    )

    replay_submit_request = MagicMock()
    replay_submit_request.match_info = {"node_id": "node-1", "job_id": "job-1"}
    replay_submit_request.text = AsyncMock(
        return_value=json.dumps({"scope_id": "owner:owner-1", "status": "succeeded"})
    )
    replay_submit_request.headers = {"X-CI-Relay-Replay": "1"}
    replay_submit_response = await server.handle_owner_ci_worker_submit_job_result(
        replay_submit_request
    )

    server._check_auth = MagicMock(return_value=True)  # type: ignore[method-assign]
    storage.submit_worker_job_result.side_effect = RuntimeError("conflict")
    runtime_submit_request = MagicMock()
    runtime_submit_request.match_info = {"node_id": "node-1", "job_id": "job-1"}
    runtime_submit_request.text = AsyncMock(
        return_value=json.dumps({"scope_id": "owner:owner-1", "status": "failed"})
    )
    runtime_submit_request.headers = {}
    runtime_submit_response = await server.handle_owner_ci_worker_submit_job_result(
        runtime_submit_request
    )

    storage.submit_worker_job_result.side_effect = Exception("boom")
    exception_submit_request = MagicMock()
    exception_submit_request.match_info = {"node_id": "node-1", "job_id": "job-1"}
    exception_submit_request.text = AsyncMock(
        return_value=json.dumps({"scope_id": "owner:owner-1", "status": "failed"})
    )
    exception_submit_request.headers = {}
    exception_submit_response = await server.handle_owner_ci_worker_submit_job_result(
        exception_submit_request
    )

    assert bad_register_response.status == 400
    assert runtime_register_response.status == 409
    assert bad_heartbeat_response.status == 400
    assert runtime_claim_response.status == 409
    assert idle_claim_response.status == 200
    assert idle_claim_payload["reason"] == "no_jobs_available"
    assert idle_claim_payload["poll_after_seconds"] == 5
    assert bad_submit_response.status == 400
    assert replay_submit_response.status == 401
    assert runtime_submit_response.status == 409
    assert exception_submit_response.status == 502
