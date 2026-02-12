"""HTTP integration tests for the Update Checker skill.

Exercises real HTTP communication with the SkillsServer hosting an
UpdateCheckerSkill.  Uses ``aiohttp.test_utils.TestServer`` in-process
(no Docker required).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestServer

from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.client import SkillsClient
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer
from zetherion_ai.skills.update_checker import UpdateCheckerSkill

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_USER = "user-update"


def _mock_skill_internals(skill: UpdateCheckerSkill) -> None:
    """Replace internals with mocks AFTER the skill has been initialized."""
    mock_manager = AsyncMock()
    mock_manager.current_version = "0.1.0"
    mock_manager.check_for_update = AsyncMock(return_value=None)
    skill._manager = mock_manager

    mock_storage = MagicMock()
    mock_storage._pool = MagicMock()
    mock_storage.get_update_history = AsyncMock(return_value=[])
    skill._storage = mock_storage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def registry_with_update() -> SkillRegistry:
    """Build a SkillRegistry with the UpdateCheckerSkill."""
    reg = SkillRegistry()
    skill = UpdateCheckerSkill(
        github_repo="owner/repo",
        enabled=True,
        updater_url="http://test-updater:9090",
        updater_secret="test-secret",
    )
    reg.register(skill)
    await reg.initialize_all()

    # Replace internals with mocks AFTER initialization
    _mock_skill_internals(skill)
    return reg


@pytest_asyncio.fixture()
async def server_and_client(
    registry_with_update: SkillRegistry,
) -> tuple[SkillsServer, SkillsClient, str]:
    """Start SkillsServer on a random port; return (server, client, base_url)."""
    server = SkillsServer(registry=registry_with_update, api_secret="test-update")
    app = server.create_app()

    test_server = TestServer(app)
    await test_server.start_server()

    base_url = f"http://{test_server.host}:{test_server.port}"
    client = SkillsClient(base_url=base_url, api_secret="test-update")

    yield server, client, base_url  # type: ignore[misc]

    await client.close()
    await test_server.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_health_check_endpoint(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """GET /health should include update_checker in the ready count."""
    _server, client, _url = server_and_client
    result = await client.health_check()
    assert result is True


@pytest.mark.integration
async def test_handle_check_update_intent(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /handle with check_update intent returns up-to-date status."""
    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="check_update",
        message="Check for updates",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert "up to date" in response.message.lower()
    assert response.data["up_to_date"] is True


@pytest.mark.integration
async def test_handle_update_status_intent(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /handle with update_status intent returns version info."""
    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="update_status",
        message="What version?",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert "current_version" in response.data
    assert response.data["enabled"] is True
    assert response.data["repo"] == "owner/repo"


@pytest.mark.integration
async def test_handle_apply_update_no_pending(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /handle with apply_update when no update → 'No update available'."""
    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="apply_update",
        message="Apply the update",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert "no update" in response.message.lower()


@pytest.mark.integration
async def test_handle_rollback_no_history(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /handle with rollback_update and no history."""
    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="rollback_update",
        message="Rollback",
    )
    response = await client.handle_request(request)

    assert response.success is False
    assert "no update history" in response.message.lower()


@pytest.mark.integration
async def test_handle_unknown_intent(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /handle with unknown intent should return error."""
    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="magic_intent",
        message="Do magic",
    )
    response = await client.handle_request(request)

    assert response.success is False


@pytest.mark.integration
async def test_heartbeat_not_on_6th_beat(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /heartbeat on first beat should not trigger update check."""
    _server, _client, base_url = server_and_client

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{base_url}/heartbeat",
            json={"user_ids": [TEST_USER]},
            headers={"X-API-Secret": "test-update"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert data["actions"] == []


@pytest.mark.integration
async def test_status_includes_update_checker(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """GET /status should include update_checker in the status summary."""
    _server, client, _url = server_and_client
    status = await client.get_status()

    assert status is not None
    assert status["total_skills"] == 1
    assert status["ready_count"] == 1
    assert "update_checker" in status["by_status"]["ready"]


@pytest.mark.integration
async def test_prompt_fragments_include_version(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """GET /prompt-fragments should include version info."""
    _server, client, _url = server_and_client
    fragments = await client.get_prompt_fragments(TEST_USER)

    assert isinstance(fragments, list)
    version_frags = [f for f in fragments if "Version" in f]
    assert len(version_frags) >= 1


@pytest.mark.integration
async def test_intents_include_update(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """GET /intents should include update-related intents."""
    _server, _client, base_url = server_and_client

    async with httpx.AsyncClient() as http:
        resp = await http.get(
            f"{base_url}/intents",
            headers={"X-API-Secret": "test-update"},
        )

    assert resp.status_code == 200
    data = resp.json()
    intents = data["intents"]
    assert "check_update" in intents
    assert "apply_update" in intents
    assert "rollback_update" in intents
    assert "update_status" in intents
