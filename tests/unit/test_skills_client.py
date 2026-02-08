"""Tests for skills client module."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zetherion_ai.skills.base import HeartbeatAction, SkillMetadata, SkillRequest, SkillResponse
from zetherion_ai.skills.client import (
    SkillsAuthError,
    SkillsClient,
    SkillsClientError,
    SkillsConnectionError,
    create_skills_client,
)


class TestSkillsClient:
    """Tests for SkillsClient class."""

    def test_init_default(self) -> None:
        """SkillsClient should have default values."""
        client = SkillsClient()
        assert client._base_url == "http://zetherion_ai-skills:8080"
        assert client._api_secret is None
        assert client._timeout == 30.0

    def test_init_custom(self) -> None:
        """SkillsClient should accept custom values."""
        client = SkillsClient(
            base_url="http://custom:9000/",
            api_secret="secret123",
            timeout=60.0,
        )
        assert client._base_url == "http://custom:9000"  # Trailing slash stripped
        assert client._api_secret == "secret123"
        assert client._timeout == 60.0

    @pytest.mark.asyncio
    async def test_get_client_creates_client(self) -> None:
        """_get_client() should create client on first call."""
        client = SkillsClient()
        http_client = await client._get_client()
        assert http_client is not None
        assert isinstance(http_client, httpx.AsyncClient)
        await client.close()

    @pytest.mark.asyncio
    async def test_get_client_with_api_secret(self) -> None:
        """_get_client() should set X-API-Secret header when secret provided."""
        client = SkillsClient(api_secret="test_secret")
        http_client = await client._get_client()
        assert "X-API-Secret" in http_client.headers
        assert http_client.headers["X-API-Secret"] == "test_secret"
        await client.close()

    @pytest.mark.asyncio
    async def test_close(self) -> None:
        """close() should close the HTTP client."""
        client = SkillsClient()
        await client._get_client()  # Create client
        assert client._client is not None
        await client.close()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_health_check_success(self) -> None:
        """health_check() should return True on 200 response."""
        client = SkillsClient()
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            result = await client.health_check()
            assert result is True
            mock_http_client.get.assert_called_once_with("/health")

    @pytest.mark.asyncio
    async def test_health_check_failure(self) -> None:
        """health_check() should return False on connection error."""
        client = SkillsClient()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(side_effect=httpx.RequestError("Connection failed"))
            mock_get.return_value = mock_http_client

            result = await client.health_check()
            assert result is False

    @pytest.mark.asyncio
    async def test_handle_request_success(self) -> None:
        """handle_request() should return SkillResponse on success."""
        client = SkillsClient()
        request = SkillRequest(
            user_id="user123",
            intent="test_intent",
            message="test message",
        )

        response_data = {
            "request_id": str(request.id),
            "success": True,
            "message": "Handled successfully",
            "data": {"result": "ok"},
            "error": None,
            "actions": [],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = response_data
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            result = await client.handle_request(request)
            assert isinstance(result, SkillResponse)
            assert result.success is True
            assert result.message == "Handled successfully"

    @pytest.mark.asyncio
    async def test_handle_request_auth_error_401(self) -> None:
        """handle_request() should raise SkillsAuthError on 401."""
        client = SkillsClient()
        request = SkillRequest()

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsAuthError, match="Authentication failed"):
                await client.handle_request(request)

    @pytest.mark.asyncio
    async def test_handle_request_auth_error_403(self) -> None:
        """handle_request() should raise SkillsAuthError on 403."""
        client = SkillsClient()
        request = SkillRequest()

        mock_response = MagicMock()
        mock_response.status_code = 403

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsAuthError, match="Authorization failed"):
                await client.handle_request(request)

    @pytest.mark.asyncio
    async def test_handle_request_connection_error(self) -> None:
        """handle_request() should raise SkillsConnectionError on connect failure."""
        client = SkillsClient()
        request = SkillRequest()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsConnectionError, match="Unable to connect"):
                await client.handle_request(request)

    @pytest.mark.asyncio
    async def test_handle_request_general_error(self) -> None:
        """handle_request() should raise SkillsClientError on other errors."""
        client = SkillsClient()
        request = SkillRequest()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(side_effect=httpx.RequestError("Timeout"))
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsClientError, match="Request failed"):
                await client.handle_request(request)

    @pytest.mark.asyncio
    async def test_trigger_heartbeat_success(self) -> None:
        """trigger_heartbeat() should return actions on success."""
        client = SkillsClient()

        actions_data = {
            "actions": [
                {
                    "skill_name": "task_manager",
                    "action_type": "reminder",
                    "user_id": "user1",
                    "data": {},
                    "priority": 5,
                },
                {
                    "skill_name": "calendar",
                    "action_type": "briefing",
                    "user_id": "user1",
                    "data": {},
                    "priority": 10,
                },
            ]
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = actions_data
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            result = await client.trigger_heartbeat(["user1", "user2"])
            assert len(result) == 2
            assert all(isinstance(a, HeartbeatAction) for a in result)
            mock_http_client.post.assert_called_once_with(
                "/heartbeat",
                json={"user_ids": ["user1", "user2"]},
            )

    @pytest.mark.asyncio
    async def test_trigger_heartbeat_connection_error(self) -> None:
        """trigger_heartbeat() should raise SkillsConnectionError on connect failure."""
        client = SkillsClient()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(side_effect=httpx.ConnectError("Refused"))
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsConnectionError):
                await client.trigger_heartbeat(["user1"])

    @pytest.mark.asyncio
    async def test_list_skills_success(self) -> None:
        """list_skills() should return skill metadata list."""
        client = SkillsClient()

        skills_data = {
            "skills": [
                {
                    "name": "task_manager",
                    "description": "Task management",
                    "version": "1.0.0",
                    "author": "Zetherion AI",
                    "permissions": ["READ_PROFILE"],
                    "collections": [],
                    "intents": ["create_task"],
                },
            ]
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = skills_data
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            result = await client.list_skills()
            assert len(result) == 1
            assert isinstance(result[0], SkillMetadata)
            assert result[0].name == "task_manager"

    @pytest.mark.asyncio
    async def test_get_skill_found(self) -> None:
        """get_skill() should return metadata when skill exists."""
        client = SkillsClient()

        skill_data = {
            "name": "calendar",
            "description": "Calendar skill",
            "version": "2.0.0",
            "author": "Zetherion AI",
            "permissions": [],
            "collections": [],
            "intents": [],
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = skill_data
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            result = await client.get_skill("calendar")
            assert isinstance(result, SkillMetadata)
            assert result.name == "calendar"

    @pytest.mark.asyncio
    async def test_get_skill_not_found(self) -> None:
        """get_skill() should return None when skill not found."""
        client = SkillsClient()

        mock_response = MagicMock()
        mock_response.status_code = 404

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            result = await client.get_skill("unknown")
            assert result is None

    @pytest.mark.asyncio
    async def test_get_status_success(self) -> None:
        """get_status() should return status dict."""
        client = SkillsClient()

        status_data = {
            "total_skills": 3,
            "ready_count": 2,
            "error_count": 1,
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = status_data
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            result = await client.get_status()
            assert result == status_data

    @pytest.mark.asyncio
    async def test_get_prompt_fragments_success(self) -> None:
        """get_prompt_fragments() should return fragment list."""
        client = SkillsClient()

        fragments_data = {
            "fragments": [
                "You have 3 pending tasks.",
                "Today's schedule: 2 meetings.",
            ]
        }

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = fragments_data
        mock_response.raise_for_status = MagicMock()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            result = await client.get_prompt_fragments("user123")
            assert len(result) == 2
            assert "pending tasks" in result[0]
            mock_http_client.get.assert_called_once_with(
                "/prompt-fragments",
                params={"user_id": "user123"},
            )


class TestCreateSkillsClient:
    """Tests for create_skills_client factory function."""

    @pytest.mark.asyncio
    async def test_create_with_defaults(self) -> None:
        """create_skills_client() should use settings defaults."""
        mock_settings = MagicMock()
        mock_settings.skills_service_url = "http://test:8080"
        mock_settings.skills_api_secret = None

        with patch("zetherion_ai.config.get_settings", return_value=mock_settings):
            client = await create_skills_client()
            assert client._base_url == "http://test:8080"
            assert client._api_secret is None

    @pytest.mark.asyncio
    async def test_create_with_custom_values(self) -> None:
        """create_skills_client() should allow overrides."""
        mock_settings = MagicMock()
        mock_settings.skills_service_url = "http://default:8080"
        mock_settings.skills_api_secret = None

        with patch("zetherion_ai.config.get_settings", return_value=mock_settings):
            client = await create_skills_client(
                base_url="http://custom:9000",
                api_secret="custom_secret",
            )
            assert client._base_url == "http://custom:9000"
            assert client._api_secret == "custom_secret"

    @pytest.mark.asyncio
    async def test_create_with_secret_str(self) -> None:
        """create_skills_client() should handle SecretStr."""
        mock_secret = MagicMock()
        mock_secret.get_secret_value.return_value = "secret_value"

        mock_settings = MagicMock()
        mock_settings.skills_service_url = "http://test:8080"
        mock_settings.skills_api_secret = mock_secret

        with patch("zetherion_ai.config.get_settings", return_value=mock_settings):
            client = await create_skills_client()
            assert client._api_secret == "secret_value"


class TestSkillsClientExceptions:
    """Tests for skills client exception classes."""

    def test_skills_client_error_inheritance(self) -> None:
        """SkillsClientError should be base exception."""
        error = SkillsClientError("test")
        assert isinstance(error, Exception)

    def test_skills_connection_error_inheritance(self) -> None:
        """SkillsConnectionError should inherit from SkillsClientError."""
        error = SkillsConnectionError("connection failed")
        assert isinstance(error, SkillsClientError)

    def test_skills_auth_error_inheritance(self) -> None:
        """SkillsAuthError should inherit from SkillsClientError."""
        error = SkillsAuthError("auth failed")
        assert isinstance(error, SkillsClientError)


class TestSkillsClientAuthErrors:
    """Tests for 401 response handling across all client methods."""

    @pytest.mark.asyncio
    async def test_trigger_heartbeat_auth_error(self) -> None:
        """trigger_heartbeat() should raise SkillsAuthError on 401."""
        client = SkillsClient()

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.post = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsAuthError, match="Authentication failed"):
                await client.trigger_heartbeat(["user1"])

    @pytest.mark.asyncio
    async def test_list_skills_auth_error(self) -> None:
        """list_skills() should raise SkillsAuthError on 401."""
        client = SkillsClient()

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsAuthError, match="Authentication failed"):
                await client.list_skills()

    @pytest.mark.asyncio
    async def test_get_skill_auth_error(self) -> None:
        """get_skill() should raise SkillsAuthError on 401."""
        client = SkillsClient()

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsAuthError, match="Authentication failed"):
                await client.get_skill("calendar")

    @pytest.mark.asyncio
    async def test_get_status_auth_error(self) -> None:
        """get_status() should raise SkillsAuthError on 401."""
        client = SkillsClient()

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsAuthError, match="Authentication failed"):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_get_prompt_fragments_auth_error(self) -> None:
        """get_prompt_fragments() should raise SkillsAuthError on 401."""
        client = SkillsClient()

        mock_response = MagicMock()
        mock_response.status_code = 401

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(return_value=mock_response)
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsAuthError, match="Authentication failed"):
                await client.get_prompt_fragments("user1")


class TestSkillsClientConnectionErrors:
    """Tests for connection error handling across client methods."""

    @pytest.mark.asyncio
    async def test_list_skills_connection_error(self) -> None:
        """list_skills() should raise SkillsConnectionError on ConnectError."""
        client = SkillsClient()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsConnectionError, match="Unable to connect"):
                await client.list_skills()

    @pytest.mark.asyncio
    async def test_get_skill_connection_error(self) -> None:
        """get_skill() should raise SkillsConnectionError on ConnectError."""
        client = SkillsClient()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsConnectionError, match="Unable to connect"):
                await client.get_skill("calendar")

    @pytest.mark.asyncio
    async def test_get_status_connection_error(self) -> None:
        """get_status() should raise SkillsConnectionError on ConnectError."""
        client = SkillsClient()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsConnectionError, match="Unable to connect"):
                await client.get_status()

    @pytest.mark.asyncio
    async def test_get_prompt_fragments_connection_error(self) -> None:
        """get_prompt_fragments() should raise SkillsConnectionError on ConnectError."""
        client = SkillsClient()

        with patch.object(client, "_get_client") as mock_get:
            mock_http_client = AsyncMock()
            mock_http_client.get = AsyncMock(side_effect=httpx.ConnectError("Connection refused"))
            mock_get.return_value = mock_http_client

            with pytest.raises(SkillsConnectionError, match="Unable to connect"):
                await client.get_prompt_fragments("user1")


class TestSkillsClientRecreation:
    """Tests for client recreation after close."""

    @pytest.mark.asyncio
    async def test_get_client_after_close(self) -> None:
        """_get_client() should create a new client after close."""
        client = SkillsClient()

        # Create the initial HTTP client
        first_http_client = await client._get_client()
        assert first_http_client is not None

        # Close and verify it is cleared
        await client.close()
        assert client._client is None

        # Get a new client - should create a fresh one
        second_http_client = await client._get_client()
        assert second_http_client is not None
        assert second_http_client is not first_http_client

        # Clean up
        await client.close()
