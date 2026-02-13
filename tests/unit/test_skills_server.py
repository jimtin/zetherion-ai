"""Tests for skills server."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.skills.base import (
    HeartbeatAction,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer


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

    async def test_auth_middleware_allows_valid_secret(self, auth_client, mock_registry):
        """Authenticated server should allow requests with valid auth header."""
        mock_registry.list_skills.return_value = []

        resp = await auth_client.get(
            "/skills",
            headers={"X-API-Secret": "test-secret"},
        )
        assert resp.status == 200


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
