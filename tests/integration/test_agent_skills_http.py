"""Integration tests for Agent → Skills HTTP path.

Validates that the Agent's intent parsing, SkillRequest construction, and
response interpretation work correctly when connected to a real SkillsServer
over HTTP (no Docker needed).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestServer

from zetherion_ai.agent.core import Agent
from zetherion_ai.skills.calendar import CalendarSkill
from zetherion_ai.skills.client import SkillsClient, SkillsClientError
from zetherion_ai.skills.profile_skill import ProfileSkill
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer
from zetherion_ai.skills.task_manager import TaskManagerSkill

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_USER_ID = 12345


@pytest_asyncio.fixture()
async def agent_with_skills() -> Agent:
    """Agent with mocked memory/router but real SkillsClient → HTTP → SkillsServer."""
    # Real registry with all three built-in skills
    reg = SkillRegistry()
    reg.register(TaskManagerSkill(memory=None))
    reg.register(CalendarSkill(memory=None))
    reg.register(ProfileSkill(memory=None))
    init_results = await reg.initialize_all()
    assert all(init_results.values())

    # Real server + client over HTTP
    server = SkillsServer(registry=reg, api_secret="test-secret")
    app = server.create_app()
    test_server = TestServer(app)
    await test_server.start_server()

    client = SkillsClient(
        base_url=f"http://{test_server.host}:{test_server.port}",
        api_secret="test-secret",
    )

    # Build agent with mocked internals, inject real skills client
    mock_memory = MagicMock()
    with (
        patch("zetherion_ai.agent.core.create_router_sync"),
        patch("zetherion_ai.agent.core.InferenceBroker"),
    ):
        agent = Agent(memory=mock_memory)

    agent._skills_client = client
    agent._skills_enabled = True

    yield agent  # type: ignore[misc]

    await client.close()
    await test_server.close()


# ---------------------------------------------------------------------------
# Skill intent via Agent (HTTP round-trip)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_task_create_via_agent(agent_with_skills: Agent) -> None:
    """Creating a task via _handle_skill_intent should succeed."""
    response = await agent_with_skills._handle_skill_intent(
        TEST_USER_ID,
        "add a task to buy groceries",
        "task_manager",
    )
    # Should return a success message (not an error fallback)
    assert "trouble" not in response.lower()


@pytest.mark.integration
async def test_task_list_via_agent(agent_with_skills: Agent) -> None:
    """Listing tasks via _handle_skill_intent should succeed after creation."""
    # Create a task first
    await agent_with_skills._handle_skill_intent(
        TEST_USER_ID,
        "add a task to review docs",
        "task_manager",
    )
    # Now list
    response = await agent_with_skills._handle_skill_intent(
        TEST_USER_ID,
        "show my tasks",
        "task_manager",
    )
    assert "trouble" not in response.lower()


@pytest.mark.integration
async def test_calendar_schedule_via_agent(agent_with_skills: Agent) -> None:
    """Scheduling a calendar event via the agent should succeed."""
    response = await agent_with_skills._handle_skill_intent(
        TEST_USER_ID,
        "schedule a meeting tomorrow at 2pm",
        "calendar",
    )
    assert "trouble" not in response.lower()


@pytest.mark.integration
async def test_calendar_today_via_agent(agent_with_skills: Agent) -> None:
    """Querying today's schedule via the agent should succeed."""
    response = await agent_with_skills._handle_skill_intent(
        TEST_USER_ID,
        "what's on today",
        "calendar",
    )
    assert "trouble" not in response.lower()


@pytest.mark.integration
async def test_profile_summary_via_agent(agent_with_skills: Agent) -> None:
    """Requesting profile summary via the agent should succeed."""
    response = await agent_with_skills._handle_skill_intent(
        TEST_USER_ID,
        "what do you know about me",
        "profile_manager",
    )
    assert "trouble" not in response.lower()


# ---------------------------------------------------------------------------
# Intent parsing (no HTTP needed)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_parse_task_create(agent_with_skills: Agent) -> None:
    """_parse_task_intent should map 'add' keywords to create_task."""
    assert agent_with_skills._parse_task_intent("add a new task") == "create_task"


@pytest.mark.integration
async def test_parse_task_list(agent_with_skills: Agent) -> None:
    """_parse_task_intent should map 'show' keywords to list_tasks."""
    assert agent_with_skills._parse_task_intent("show my tasks") == "list_tasks"


@pytest.mark.integration
async def test_parse_calendar_availability(agent_with_skills: Agent) -> None:
    """_parse_calendar_intent should map 'free' keywords to check_availability."""
    assert agent_with_skills._parse_calendar_intent("am I free tomorrow") == "check_availability"


# ---------------------------------------------------------------------------
# Error / fallback paths
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_skill_error_user_message(agent_with_skills: Agent) -> None:
    """A SkillsClientError should produce a user-friendly fallback message."""
    broken_client = AsyncMock(spec=SkillsClient)
    broken_client.handle_request = AsyncMock(side_effect=SkillsClientError("boom"))
    agent_with_skills._skills_client = broken_client

    response = await agent_with_skills._handle_skill_intent(
        TEST_USER_ID,
        "add a task",
        "task_manager",
    )
    assert "trouble" in response.lower()


@pytest.mark.integration
async def test_skills_disconnected_fallback(agent_with_skills: Agent) -> None:
    """When skills client is unavailable, should return connection error message."""
    agent_with_skills._skills_client = None
    agent_with_skills._skills_enabled = False

    with patch.object(agent_with_skills, "_get_skills_client", return_value=None):
        response = await agent_with_skills._handle_skill_intent(
            TEST_USER_ID,
            "add a task",
            "task_manager",
        )
    assert "connecting" in response.lower() or "trouble" in response.lower()
