"""Unit tests for the Agent core module."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from zetherion_ai.agent.core import Agent
from zetherion_ai.agent.router import MessageIntent, RoutingDecision
from zetherion_ai.skills.base import SkillResponse
from zetherion_ai.skills.client import SkillsClientError

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_agent(mock_memory=None, mock_router=None):
    """Create an Agent with mocked dependencies.

    Args:
        mock_memory: Optional pre-built mock for QdrantMemory.
        mock_router: Optional pre-built mock for MessageRouter.

    Returns:
        Agent instance with injected mocks.
    """
    memory = mock_memory or AsyncMock()
    router = mock_router or AsyncMock()
    with (
        patch("zetherion_ai.agent.core.create_router_sync", return_value=router),
        patch("zetherion_ai.agent.core.InferenceBroker"),
        patch("zetherion_ai.agent.core.get_settings") as mock_get_settings,
    ):
        mock_settings = MagicMock()
        mock_settings.docs_knowledge_enabled = False
        mock_get_settings.return_value = mock_settings
        agent = Agent(memory=memory)
    return agent


def _routing(intent, confidence=0.9, use_claude=False, reasoning="test"):
    """Shortcut to build a RoutingDecision."""
    return RoutingDecision(
        intent=intent,
        confidence=confidence,
        reasoning=reasoning,
        use_claude=use_claude,
    )


# ===========================================================================
# warmup / keep_warm
# ===========================================================================


class TestAgentWarmup:
    """Tests for Agent.warmup."""

    async def test_warmup_delegates_to_backend(self):
        """Warmup should call backend.warmup when available."""
        agent = _make_agent()
        backend = AsyncMock()
        backend.warmup = AsyncMock(return_value=True)
        agent._router._backend = backend

        result = await agent.warmup()
        assert result is True
        backend.warmup.assert_awaited_once()

    async def test_warmup_returns_true_when_no_backend(self):
        """Warmup returns True when _backend is missing."""
        agent = _make_agent()
        # Remove _backend attribute entirely
        del agent._router._backend

        result = await agent.warmup()
        assert result is True

    async def test_warmup_returns_true_when_backend_has_no_warmup(self):
        """Warmup returns True when backend lacks warmup method."""
        agent = _make_agent()
        backend = MagicMock(spec=[])  # no warmup attribute
        agent._router._backend = backend

        result = await agent.warmup()
        assert result is True

    async def test_warmup_returns_false_on_failure(self):
        """Warmup returns False when backend.warmup returns False."""
        agent = _make_agent()
        backend = AsyncMock()
        backend.warmup = AsyncMock(return_value=False)
        agent._router._backend = backend

        result = await agent.warmup()
        assert result is False


class TestAgentKeepWarm:
    """Tests for Agent.keep_warm."""

    async def test_keep_warm_delegates_to_backend(self):
        """keep_warm should call backend.keep_warm when available."""
        agent = _make_agent()
        backend = AsyncMock()
        backend.keep_warm = AsyncMock(return_value=True)
        agent._router._backend = backend

        result = await agent.keep_warm()
        assert result is True
        backend.keep_warm.assert_awaited_once()

    async def test_keep_warm_returns_true_when_no_backend(self):
        """keep_warm returns True when _backend is missing."""
        agent = _make_agent()
        del agent._router._backend

        result = await agent.keep_warm()
        assert result is True

    async def test_keep_warm_returns_true_when_backend_has_no_keep_warm(self):
        """keep_warm returns True when backend lacks the method."""
        agent = _make_agent()
        backend = MagicMock(spec=[])
        agent._router._backend = backend

        result = await agent.keep_warm()
        assert result is True

    async def test_keep_warm_returns_false_on_failure(self):
        """keep_warm returns False when backend.keep_warm returns False."""
        agent = _make_agent()
        backend = AsyncMock()
        backend.keep_warm = AsyncMock(return_value=False)
        agent._router._backend = backend

        result = await agent.keep_warm()
        assert result is False


# ===========================================================================
# _get_skills_client
# ===========================================================================


class TestGetSkillsClient:
    """Tests for Agent._get_skills_client."""

    async def test_creates_client_on_first_call(self):
        """First call should create a SkillsClient and return it."""
        agent = _make_agent()
        mock_settings = MagicMock()
        mock_settings.skills_api_secret = None
        mock_settings.skills_service_url = "http://skills:8080"
        mock_settings.skills_request_timeout = 30

        with patch("zetherion_ai.agent.core.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.core.SkillsClient") as mock_cls:
                mock_client = MagicMock()
                mock_cls.return_value = mock_client

                result = await agent._get_skills_client()

        assert result is mock_client
        assert agent._skills_enabled is True

    async def test_returns_cached_client_on_second_call(self):
        """Second call should return the cached client."""
        agent = _make_agent()
        sentinel = MagicMock()
        agent._skills_client = sentinel

        result = await agent._get_skills_client()
        assert result is sentinel

    async def test_creates_client_with_api_secret(self):
        """Client should be created with api_secret when configured."""
        agent = _make_agent()
        mock_settings = MagicMock()
        mock_secret = MagicMock()
        mock_secret.get_secret_value.return_value = "my-secret"
        mock_settings.skills_api_secret = mock_secret
        mock_settings.skills_service_url = "http://skills:8080"
        mock_settings.skills_request_timeout = 30

        with patch("zetherion_ai.agent.core.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.agent.core.SkillsClient") as mock_cls:
                mock_cls.return_value = MagicMock()
                await agent._get_skills_client()

        mock_cls.assert_called_once_with(
            base_url="http://skills:8080",
            api_secret="my-secret",
            timeout=30.0,
        )

    async def test_returns_none_on_exception(self):
        """Should return None and disable skills when init fails."""
        agent = _make_agent()
        mock_settings = MagicMock()
        mock_settings.skills_api_secret = None
        mock_settings.skills_service_url = "http://skills:8080"
        mock_settings.skills_request_timeout = 30

        with patch("zetherion_ai.agent.core.get_settings", return_value=mock_settings):
            with patch(
                "zetherion_ai.agent.core.SkillsClient",
                side_effect=RuntimeError("boom"),
            ):
                result = await agent._get_skills_client()

        assert result is None
        assert agent._skills_enabled is False


# ===========================================================================
# _handle_skill_intent
# ===========================================================================


class TestHandleSkillIntent:
    """Tests for Agent._handle_skill_intent."""

    async def test_returns_fallback_when_no_client(self):
        """Should return connection-trouble message when client is None."""
        agent = _make_agent()
        agent._get_skills_client = AsyncMock(return_value=None)

        result = await agent._handle_skill_intent(123, "add task", "task_manager")
        assert "trouble connecting" in result

    async def test_success_response(self):
        """Should return the skill response message on success."""
        agent = _make_agent()

        mock_client = AsyncMock()
        mock_response = SkillResponse(
            request_id=uuid4(),
            success=True,
            message="Task created!",
        )
        mock_client.handle_request = AsyncMock(return_value=mock_response)
        agent._get_skills_client = AsyncMock(return_value=mock_client)

        result = await agent._handle_skill_intent(123, "add task groceries", "task_manager")
        assert result == "Task created!"

    async def test_success_response_empty_message(self):
        """Should return 'Done!' when response.message is empty."""
        agent = _make_agent()

        mock_client = AsyncMock()
        mock_response = SkillResponse(
            request_id=uuid4(),
            success=True,
            message="",
        )
        mock_client.handle_request = AsyncMock(return_value=mock_response)
        agent._get_skills_client = AsyncMock(return_value=mock_client)

        result = await agent._handle_skill_intent(123, "add task", "task_manager")
        assert result == "Done!"

    async def test_failure_response(self):
        """Should return error message on non-success response."""
        agent = _make_agent()

        mock_client = AsyncMock()
        mock_response = SkillResponse(
            request_id=uuid4(),
            success=False,
            error="Not found",
        )
        mock_client.handle_request = AsyncMock(return_value=mock_response)
        agent._get_skills_client = AsyncMock(return_value=mock_client)

        result = await agent._handle_skill_intent(123, "delete task", "task_manager")
        assert "Not found" in result

    async def test_skills_client_error(self):
        """Should return generic error on SkillsClientError."""
        agent = _make_agent()

        mock_client = AsyncMock()
        mock_client.handle_request = AsyncMock(
            side_effect=SkillsClientError("connection refused"),
        )
        agent._get_skills_client = AsyncMock(return_value=mock_client)

        result = await agent._handle_skill_intent(123, "list tasks", "task_manager")
        assert "trouble processing" in result

    async def test_routes_to_calendar_skill(self):
        """Should use calendar intent parsing for calendar skill."""
        agent = _make_agent()

        mock_client = AsyncMock()
        mock_response = SkillResponse(
            request_id=uuid4(),
            success=True,
            message="Here is your schedule.",
        )
        mock_client.handle_request = AsyncMock(return_value=mock_response)
        agent._get_skills_client = AsyncMock(return_value=mock_client)

        result = await agent._handle_skill_intent(
            123,
            "schedule meeting for Friday",
            "calendar",
        )
        assert result == "Here is your schedule."

        # Verify the intent was set correctly
        call_args = mock_client.handle_request.call_args
        request = call_args[0][0]
        assert request.intent == "schedule_event"

    async def test_routes_to_profile_skill(self):
        """Should use profile intent parsing for profile_manager skill."""
        agent = _make_agent()

        mock_client = AsyncMock()
        mock_response = SkillResponse(
            request_id=uuid4(),
            success=True,
            message="Profile exported.",
        )
        mock_client.handle_request = AsyncMock(return_value=mock_response)
        agent._get_skills_client = AsyncMock(return_value=mock_client)

        result = await agent._handle_skill_intent(
            123,
            "export my data",
            "profile_manager",
        )
        assert result == "Profile exported."

        call_args = mock_client.handle_request.call_args
        request = call_args[0][0]
        assert request.intent == "profile_export"

    async def test_unknown_skill_uses_unknown_intent(self):
        """Unknown skill names should get 'unknown' as the intent."""
        agent = _make_agent()

        mock_client = AsyncMock()
        mock_response = SkillResponse(
            request_id=uuid4(),
            success=True,
            message="OK",
        )
        mock_client.handle_request = AsyncMock(return_value=mock_response)
        agent._get_skills_client = AsyncMock(return_value=mock_client)

        await agent._handle_skill_intent(123, "hello", "nonexistent_skill")

        call_args = mock_client.handle_request.call_args
        request = call_args[0][0]
        assert request.intent == "unknown"


# ===========================================================================
# _parse_task_intent
# ===========================================================================


class TestParseTaskIntent:
    """Tests for Agent._parse_task_intent."""

    def test_create_task(self):
        agent = _make_agent()
        assert agent._parse_task_intent("add a new task") == "create_task"
        assert agent._parse_task_intent("create todo") == "create_task"
        assert agent._parse_task_intent("make a task") == "create_task"

    def test_list_tasks(self):
        agent = _make_agent()
        assert agent._parse_task_intent("list my tasks") == "list_tasks"
        assert agent._parse_task_intent("show tasks") == "list_tasks"
        assert agent._parse_task_intent("what are my tasks") == "list_tasks"

    def test_complete_task(self):
        agent = _make_agent()
        assert agent._parse_task_intent("mark task as done") == "complete_task"
        assert agent._parse_task_intent("complete the task") == "complete_task"
        assert agent._parse_task_intent("finish the report") == "complete_task"

    def test_delete_task(self):
        agent = _make_agent()
        assert agent._parse_task_intent("delete the shopping task") == "delete_task"
        assert agent._parse_task_intent("remove that task") == "delete_task"
        assert agent._parse_task_intent("cancel the task") == "delete_task"

    def test_update_task(self):
        agent = _make_agent()
        assert agent._parse_task_intent("update the task deadline") == "update_task"
        assert agent._parse_task_intent("change task priority") == "update_task"
        assert agent._parse_task_intent("modify task description") == "update_task"
        assert agent._parse_task_intent("edit task") == "update_task"

    def test_task_summary(self):
        agent = _make_agent()
        assert agent._parse_task_intent("give me a summary") == "task_summary"
        assert agent._parse_task_intent("task overview") == "task_summary"
        assert agent._parse_task_intent("what is my status") == "task_summary"

    def test_default_fallback(self):
        """Unrecognised messages should fall back to list_tasks."""
        agent = _make_agent()
        assert agent._parse_task_intent("something random") == "list_tasks"


# ===========================================================================
# _parse_calendar_intent
# ===========================================================================


class TestParseCalendarIntent:
    """Tests for Agent._parse_calendar_intent."""

    def test_schedule_event(self):
        agent = _make_agent()
        assert agent._parse_calendar_intent("schedule a meeting") == "schedule_event"
        assert agent._parse_calendar_intent("book a room") == "schedule_event"
        assert agent._parse_calendar_intent("add an event") == "schedule_event"
        assert agent._parse_calendar_intent("create a meeting") == "schedule_event"

    def test_check_availability(self):
        agent = _make_agent()
        assert agent._parse_calendar_intent("am I free at 3pm") == "check_availability"
        assert agent._parse_calendar_intent("check availability") == "check_availability"
        assert agent._parse_calendar_intent("is 5pm available") == "check_availability"

    def test_today_schedule(self):
        agent = _make_agent()
        assert agent._parse_calendar_intent("what's on today") == "today_schedule"
        assert agent._parse_calendar_intent("today's events") == "today_schedule"

    def test_set_work_hours(self):
        agent = _make_agent()
        assert agent._parse_calendar_intent("set my work hours to 9-5") == "set_work_hours"
        assert agent._parse_calendar_intent("update working hours") == "set_work_hours"

    def test_list_events(self):
        agent = _make_agent()
        assert agent._parse_calendar_intent("list my events") == "list_events"
        assert agent._parse_calendar_intent("show calendar") == "list_events"
        assert agent._parse_calendar_intent("what events do I have") == "list_events"

    def test_default_fallback(self):
        """Unrecognised messages should fall back to today_schedule."""
        agent = _make_agent()
        assert agent._parse_calendar_intent("something random") == "today_schedule"


# ===========================================================================
# _parse_profile_intent
# ===========================================================================


class TestParseProfileIntent:
    """Tests for Agent._parse_profile_intent."""

    def test_profile_update(self):
        agent = _make_agent()
        assert agent._parse_profile_intent("update my timezone") == "profile_update"
        assert agent._parse_profile_intent("change my name") == "profile_update"
        assert agent._parse_profile_intent("set my location") == "profile_update"

    def test_profile_delete(self):
        agent = _make_agent()
        assert agent._parse_profile_intent("forget my location") == "profile_delete"
        assert agent._parse_profile_intent("delete my data") == "profile_delete"
        assert agent._parse_profile_intent("remove my info") == "profile_delete"

    def test_profile_export(self):
        agent = _make_agent()
        assert agent._parse_profile_intent("export my data") == "profile_export"
        assert agent._parse_profile_intent("download my info") == "profile_export"
        assert agent._parse_profile_intent("gdpr data request") == "profile_export"

    def test_profile_confidence(self):
        agent = _make_agent()
        assert agent._parse_profile_intent("how confidence are you") == "profile_confidence"
        assert agent._parse_profile_intent("are you certain") == "profile_confidence"
        assert agent._parse_profile_intent("are you sure about that") == "profile_confidence"

    def test_profile_summary(self):
        agent = _make_agent()
        assert agent._parse_profile_intent("what do you know about me") == "profile_summary"
        assert agent._parse_profile_intent("show my profile") == "profile_summary"
        assert agent._parse_profile_intent("you know my preferences?") == "profile_summary"

    def test_default_fallback(self):
        """Unrecognised messages should fall back to profile_summary."""
        agent = _make_agent()
        assert agent._parse_profile_intent("something random") == "profile_summary"


# ===========================================================================
# _parse_update_intent
# ===========================================================================


class TestParseUpdateIntent:
    """Tests for Agent._parse_update_intent."""

    def test_resume_updates(self):
        agent = _make_agent()
        assert agent._parse_update_intent("resume updates") == "resume_updates"
        assert agent._parse_update_intent("unpause rollout") == "resume_updates"

    def test_rollback_update(self):
        agent = _make_agent()
        assert agent._parse_update_intent("rollback now") == "rollback_update"
        assert agent._parse_update_intent("revert the release") == "rollback_update"

    def test_apply_update(self):
        agent = _make_agent()
        assert agent._parse_update_intent("apply latest update") == "apply_update"
        assert agent._parse_update_intent("install release") == "apply_update"

    def test_update_status(self):
        agent = _make_agent()
        assert agent._parse_update_intent("what version are you") == "update_status"
        assert agent._parse_update_intent("update status") == "update_status"

    def test_default_check_update(self):
        agent = _make_agent()
        assert agent._parse_update_intent("check for updates") == "check_update"


# ===========================================================================
# generate_response — skill intents
# ===========================================================================


class TestGenerateResponseSkillIntents:
    """Tests for generate_response routing to skill intents."""

    async def test_task_management_intent(self):
        """TASK_MANAGEMENT routes to _handle_skill_intent(task_manager)."""
        mock_memory = AsyncMock()
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(MessageIntent.TASK_MANAGEMENT),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._handle_skill_intent = AsyncMock(return_value="Task created!")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="add task buy milk",
        )

        assert result == "Task created!"
        agent._handle_skill_intent.assert_awaited_once_with(
            123,
            "add task buy milk",
            "task_manager",
        )
        # Skill intents store messages in memory
        assert mock_memory.store_message.await_count == 2

    async def test_calendar_query_intent(self):
        """CALENDAR_QUERY routes to _handle_skill_intent(calendar)."""
        mock_memory = AsyncMock()
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(MessageIntent.CALENDAR_QUERY),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._handle_skill_intent = AsyncMock(return_value="Your schedule for today.")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="what is on my calendar",
        )

        assert result == "Your schedule for today."
        agent._handle_skill_intent.assert_awaited_once_with(
            123,
            "what is on my calendar",
            "calendar",
        )

    async def test_profile_query_intent(self):
        """PROFILE_QUERY routes to _handle_skill_intent(profile_manager)."""
        mock_memory = AsyncMock()
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(MessageIntent.PROFILE_QUERY),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._handle_skill_intent = AsyncMock(return_value="Here is your profile.")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="show my profile",
        )

        assert result == "Here is your profile."
        agent._handle_skill_intent.assert_awaited_once_with(
            123,
            "show my profile",
            "profile_manager",
        )

    async def test_update_management_intent(self):
        """UPDATE_MANAGEMENT routes to _handle_skill_intent(update_checker)."""
        mock_memory = AsyncMock()
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(MessageIntent.UPDATE_MANAGEMENT),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._handle_skill_intent = AsyncMock(return_value="Update status.")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="check for updates",
        )

        assert result == "Update status."
        agent._handle_skill_intent.assert_awaited_once_with(
            123,
            "check for updates",
            "update_checker",
        )

    async def test_docs_answer_short_circuits_email_skill(self):
        """Docs-backed answer should be preferred for setup/how-to email questions."""
        mock_memory = AsyncMock()
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(MessageIntent.EMAIL_MANAGEMENT),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._docs_knowledge = AsyncMock()
        agent._docs_knowledge.maybe_answer = AsyncMock(return_value="Use `/gmail connect`.")
        agent._handle_skill_intent = AsyncMock(return_value="skill fallback")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="How do I add an email account?",
        )

        assert result == "Use `/gmail connect`."
        agent._docs_knowledge.maybe_answer.assert_awaited_once()
        agent._handle_skill_intent.assert_not_awaited()

    async def test_docs_fallback_to_email_skill_when_unknown(self):
        """When docs don't have an answer, email skill should still handle the message."""
        mock_memory = AsyncMock()
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(MessageIntent.EMAIL_MANAGEMENT),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._docs_knowledge = AsyncMock()
        agent._docs_knowledge.maybe_answer = AsyncMock(return_value=None)
        agent._handle_skill_intent = AsyncMock(return_value="Email status.")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="How do I add an email account?",
        )

        assert result == "Email status."
        agent._docs_knowledge.maybe_answer.assert_awaited_once()
        agent._handle_skill_intent.assert_awaited_once_with(
            123,
            "How do I add an email account?",
            "gmail",
        )

    async def test_simple_query_does_not_store_messages(self):
        """SIMPLE_QUERY should not store messages in memory."""
        mock_memory = AsyncMock()
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(MessageIntent.SIMPLE_QUERY),
        )
        mock_router.generate_simple_response = AsyncMock(return_value="Hi there!")

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="hello",
        )

        assert result == "Hi there!"
        mock_memory.store_message.assert_not_awaited()

    async def test_system_command_does_not_store_messages(self):
        """SYSTEM_COMMAND should not store messages in memory."""
        mock_memory = AsyncMock()
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(MessageIntent.SYSTEM_COMMAND),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._handle_system_command = AsyncMock(return_value="Help text")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="help",
        )

        assert result == "Help text"
        mock_memory.store_message.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_generate_response_dev_watcher_intent(self):
        """DEV_WATCHER routes to _handle_skill_intent(dev_watcher)."""
        agent = _make_agent()
        agent._router.classify = AsyncMock(return_value=_routing(MessageIntent.DEV_WATCHER))
        agent._handle_skill_intent = AsyncMock(return_value="dev status response")

        await agent.generate_response(user_id=123, channel_id=456, message="dev status")

        agent._handle_skill_intent.assert_called_once()
        call_args = agent._handle_skill_intent.call_args
        assert call_args[0][2] == "dev_watcher"

    @pytest.mark.asyncio
    async def test_generate_response_milestone_intent(self):
        """MILESTONE_MANAGEMENT routes to _handle_skill_intent(milestone_tracker)."""
        agent = _make_agent()
        agent._router.classify = AsyncMock(
            return_value=_routing(MessageIntent.MILESTONE_MANAGEMENT)
        )
        agent._handle_skill_intent = AsyncMock(return_value="milestone response")

        await agent.generate_response(user_id=123, channel_id=456, message="show milestones")

        agent._handle_skill_intent.assert_called_once()
        call_args = agent._handle_skill_intent.call_args
        assert call_args[0][2] == "milestone_tracker"


# ===========================================================================
# _parse_dev_watcher_intent
# ===========================================================================


class TestParseDevWatcherIntent:
    """Tests for Agent._parse_dev_watcher_intent."""

    def test_next_keywords(self):
        agent = _make_agent()
        assert agent._parse_dev_watcher_intent("what should i work on next") == "dev_next"
        assert agent._parse_dev_watcher_intent("what to do") == "dev_next"

    def test_ideas_keywords(self):
        agent = _make_agent()
        assert agent._parse_dev_watcher_intent("show my ideas") == "dev_ideas"
        assert agent._parse_dev_watcher_intent("what idea did I have") == "dev_ideas"

    def test_journal_keywords(self):
        agent = _make_agent()
        assert agent._parse_dev_watcher_intent("show my journal") == "dev_journal"
        assert agent._parse_dev_watcher_intent("what did I do this week") == "dev_journal"
        assert agent._parse_dev_watcher_intent("what did I do today") == "dev_journal"
        assert agent._parse_dev_watcher_intent("what did I do yesterday") == "dev_journal"

    def test_summary_keywords(self):
        agent = _make_agent()
        assert agent._parse_dev_watcher_intent("give me a dev summary") == "dev_summary"
        assert agent._parse_dev_watcher_intent("dev overview") == "dev_summary"
        assert agent._parse_dev_watcher_intent("weekly recap") == "dev_summary"

    def test_default_fallback(self):
        agent = _make_agent()
        assert agent._parse_dev_watcher_intent("random text") == "dev_status"


# ===========================================================================
# _parse_milestone_intent
# ===========================================================================


class TestParseMilestoneIntent:
    """Tests for Agent._parse_milestone_intent."""

    def test_drafts_keywords(self):
        agent = _make_agent()
        assert agent._parse_milestone_intent("show me the drafts") == "milestone_drafts"
        assert agent._parse_milestone_intent("promo posts") == "milestone_drafts"
        assert agent._parse_milestone_intent("show draft content") == "milestone_drafts"

    def test_approve_keywords(self):
        agent = _make_agent()
        assert agent._parse_milestone_intent("approve this draft") == "milestone_approve"
        assert agent._parse_milestone_intent("publish the post") == "milestone_approve"
        assert agent._parse_milestone_intent("accept the tweet") == "milestone_approve"

    def test_reject_keywords(self):
        agent = _make_agent()
        assert agent._parse_milestone_intent("reject this draft") == "milestone_reject"
        assert agent._parse_milestone_intent("dismiss it") == "milestone_reject"
        assert agent._parse_milestone_intent("skip this one") == "milestone_reject"

    def test_settings_keywords(self):
        agent = _make_agent()
        assert agent._parse_milestone_intent("milestone settings") == "milestone_settings"
        assert agent._parse_milestone_intent("change threshold config") == "milestone_settings"

    def test_default_fallback(self):
        agent = _make_agent()
        assert agent._parse_milestone_intent("show milestones") == "milestone_list"
