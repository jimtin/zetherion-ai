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

    async def test_task_list_formats_summary_and_items(self):
        """list_tasks should include a summary plus concrete task entries."""
        agent = _make_agent()

        mock_client = AsyncMock()
        mock_response = SkillResponse(
            request_id=uuid4(),
            success=True,
            message="Found 1 task(s)",
            data={
                "tasks": [
                    {
                        "title": "Review docs",
                        "status": "todo",
                        "priority": 3,
                        "deadline": "2026-03-10T09:30:00",
                    }
                ],
                "count": 1,
            },
        )
        mock_client.handle_request = AsyncMock(return_value=mock_response)
        agent._get_skills_client = AsyncMock(return_value=mock_client)

        result = await agent._handle_skill_intent(123, "show my tasks", "task_manager")
        assert "You have 1 active task(s)." in result
        assert "Here are your tasks:" in result
        assert "1. Review docs - Todo - High - due 2026-03-10" in result
        assert "Found 1 task(s)" not in result

    async def test_task_list_empty_state(self):
        """list_tasks should return an explicit empty-state message."""
        agent = _make_agent()

        mock_client = AsyncMock()
        mock_response = SkillResponse(
            request_id=uuid4(),
            success=True,
            message="Found 0 task(s)",
            data={"tasks": [], "count": 0},
        )
        mock_client.handle_request = AsyncMock(return_value=mock_response)
        agent._get_skills_client = AsyncMock(return_value=mock_client)

        result = await agent._handle_skill_intent(123, "show my tasks", "task_manager")
        assert result == "You have no tasks right now."

    async def test_task_list_truncates_to_limit(self):
        """list_tasks should render up to 10 tasks and summarize the remainder."""
        agent = _make_agent()

        tasks = [{"title": f"Task {i}", "status": "todo", "priority": 2} for i in range(1, 12)]
        mock_client = AsyncMock()
        mock_response = SkillResponse(
            request_id=uuid4(),
            success=True,
            message="Found 11 task(s)",
            data={"tasks": tasks, "count": 11},
        )
        mock_client.handle_request = AsyncMock(return_value=mock_response)
        agent._get_skills_client = AsyncMock(return_value=mock_client)

        result = await agent._handle_skill_intent(123, "show my tasks", "task_manager")
        assert "10. Task 10 - Todo - Medium" in result
        assert "11. Task 11 - Todo - Medium" not in result
        assert "+1 more" in result

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
# _parse_health_intent
# ===========================================================================


class TestParseHealthIntent:
    """Tests for Agent._parse_health_intent."""

    def test_health_report_keywords(self):
        agent = _make_agent()
        assert agent._parse_health_intent("show daily report") == "health_report"
        assert agent._parse_health_intent("health report for yesterday") == "health_report"

    def test_system_status_keywords(self):
        agent = _make_agent()
        assert agent._parse_health_intent("show system status") == "system_status"
        assert agent._parse_health_intent("run diagnostics with metrics") == "system_status"

    def test_default_health_check(self):
        agent = _make_agent()
        assert agent._parse_health_intent("are you online") == "health_check"


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

    async def test_user_knowledge_summary_intent_routes_to_unified_handler(self):
        """USER_KNOWLEDGE_SUMMARY should route to unified summary handler."""
        mock_memory = AsyncMock()
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(MessageIntent.USER_KNOWLEDGE_SUMMARY),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._handle_user_knowledge_summary = AsyncMock(return_value="Unified profile summary")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="what do you know about me",
        )

        assert result == "Unified profile summary"
        agent._handle_user_knowledge_summary.assert_awaited_once_with(
            123,
            "what do you know about me",
        )

    async def test_profile_query_knowledge_phrase_uses_unified_handler(self):
        """PROFILE_QUERY with knowledge-summary phrasing should use unified handler."""
        mock_memory = AsyncMock()
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(MessageIntent.PROFILE_QUERY),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._handle_user_knowledge_summary = AsyncMock(return_value="Unified summary")
        agent._handle_skill_intent = AsyncMock(return_value="Skill summary")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="what do you know about me",
        )

        assert result == "Unified summary"
        agent._handle_user_knowledge_summary.assert_awaited_once_with(
            123,
            "what do you know about me",
        )
        agent._handle_skill_intent.assert_not_awaited()

    async def test_unified_summary_includes_profile_and_memory_facts(self):
        """Unified summary should include profile entries and remembered long-term facts."""
        mock_memory = AsyncMock()

        async def _filter_by_field(*, collection_name, field, value, limit=100):
            if collection_name != "long_term_memory" or field != "user_id":
                return []
            if value not in (123, "123"):
                return []
            return [
                {"id": "m1", "type": "user_request", "content": "I work as a software engineer"},
                {"id": "m2", "type": "general", "content": "my favorite color is teal-abc123"},
            ]

        mock_memory.filter_scoped_by_field = AsyncMock(side_effect=_filter_by_field)
        agent = _make_agent(mock_memory=mock_memory)

        mock_client = AsyncMock()
        mock_client.handle_request = AsyncMock(
            side_effect=[
                SkillResponse(
                    request_id=uuid4(),
                    success=True,
                    data={
                        "entries": [
                            {"key": "occupation", "value": "software engineer"},
                            {"key": "favorite_color", "value": "teal-abc123"},
                        ]
                    },
                ),
                SkillResponse(
                    request_id=uuid4(),
                    success=True,
                    message="I don't have a profile for you yet.",
                ),
            ]
        )
        agent._get_skills_client = AsyncMock(return_value=mock_client)

        result = await agent._handle_user_knowledge_summary(123, "what do you know about me")

        assert "Here's what I know about you" in result
        assert "occupation: software engineer" in result
        assert "favorite_color: teal-abc123" in result
        assert "I work as a software engineer" in result
        assert "my favorite color is teal-abc123" in result

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

    async def test_system_health_intent(self):
        """SYSTEM_HEALTH routes to _handle_skill_intent(health_analyzer)."""
        mock_memory = AsyncMock()
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(MessageIntent.SYSTEM_HEALTH),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._handle_skill_intent = AsyncMock(return_value="All systems healthy.")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="are you online?",
        )

        assert result == "All systems healthy."
        agent._handle_skill_intent.assert_awaited_once_with(
            123,
            "are you online?",
            "health_analyzer",
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
            "email",
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

    async def test_low_confidence_dev_watcher_followup_falls_back_to_conversation(self):
        """Owner follow-up turns should not stay on low-confidence dev_watcher routes."""
        mock_memory = AsyncMock()
        mock_memory.get_recent_context = AsyncMock(
            return_value=[
                {"role": "user", "content": "I'm editing the recording of Bev."},
                {"role": "assistant", "content": "Tell me more about what needs changing."},
            ]
        )
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(
                MessageIntent.DEV_WATCHER,
                confidence=0.76,
                reasoning="might be asking what to work on next",
            ),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._handle_complex_task = AsyncMock(
            return_value="Let's make the intro sound warmer and more natural."
        )
        agent._handle_skill_intent = AsyncMock(return_value="dev status")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="I need the Bev recording to sound more conversational.",
        )

        assert "warmer" in result
        agent._handle_skill_intent.assert_not_awaited()
        agent._handle_complex_task.assert_awaited_once()
        routed_call = agent._handle_complex_task.await_args.args
        assert routed_call[3].intent == MessageIntent.COMPLEX_TASK
        metadata = mock_memory.store_message.await_args_list[0].kwargs["metadata"]
        assert metadata["routing_trace"]["original_intent"] == "dev_watcher"
        assert metadata["routing_trace"]["final_intent"] == "complex_task"
        assert metadata["routing_trace"]["guardrail_action"] == "fallback_to_conversation"

    async def test_low_confidence_memory_recall_followup_falls_back_to_conversation(self):
        """Low-confidence memory-recall follow-ups should stay conversational."""
        mock_memory = AsyncMock()
        mock_memory.get_recent_context = AsyncMock(
            return_value=[
                {"role": "user", "content": "That rewrite still feels stiff."},
                {"role": "assistant", "content": "I can try another version."},
            ]
        )
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(
                MessageIntent.MEMORY_RECALL,
                confidence=0.79,
                reasoning="asking about prior response",
            ),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._handle_complex_task = AsyncMock(
            return_value="Sure. I'll rewrite it with a warmer, more natural tone."
        )
        agent._handle_skill_intent = AsyncMock(return_value="memory recall")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="Can you try that again but make it more natural?",
        )

        assert "rewrite" in result.lower()
        agent._handle_skill_intent.assert_not_awaited()
        agent._handle_complex_task.assert_awaited_once()
        routed_call = agent._handle_complex_task.await_args.args
        assert routed_call[3].intent == MessageIntent.COMPLEX_TASK

    async def test_repair_followup_explains_previous_bad_route(self):
        """Repair follow-ups should explain the previous bad route instead of re-misrouting."""
        mock_memory = AsyncMock()
        mock_memory.get_recent_context = AsyncMock(
            return_value=[
                {
                    "role": "user",
                    "content": "I'm editing the recording of Bev and need help with the tone.",
                },
                {
                    "role": "assistant",
                    "content": "Here's your dev status summary.",
                    "routing_trace": {
                        "original_intent": "dev_watcher",
                        "final_intent": "dev_watcher",
                        "original_reasoning": "development status request",
                    },
                },
            ]
        )
        mock_router = AsyncMock()
        mock_router.classify = AsyncMock(
            return_value=_routing(
                MessageIntent.MEMORY_RECALL,
                confidence=0.73,
                reasoning="asking about prior answer",
            ),
        )

        agent = _make_agent(mock_memory=mock_memory, mock_router=mock_router)
        agent._handle_complex_task = AsyncMock(return_value="unexpected complex task")
        agent._handle_skill_intent = AsyncMock(return_value="unexpected skill")

        result = await agent.generate_response(
            user_id=123,
            channel_id=456,
            message="What happened with that response?",
        )

        assert "dev watcher request" in result.lower()
        assert "current conversation" in result.lower()
        agent._handle_skill_intent.assert_not_awaited()
        agent._handle_complex_task.assert_not_awaited()
        assert mock_memory.store_message.await_count == 2
        metadata = mock_memory.store_message.await_args_list[0].kwargs["metadata"]
        assert metadata["routing_trace"]["guardrail_action"] == "repair_response_explanation"
        assert metadata["routing_trace"]["final_intent"] == "simple_query"


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

    def test_release_keywords(self):
        agent = _make_agent()
        assert agent._parse_dev_watcher_intent("show release status") == "dev_release_summary"
        assert agent._parse_dev_watcher_intent("latest deploy summary") == "dev_release_summary"
        assert agent._parse_dev_watcher_intent("ci pipeline health") == "dev_release_summary"

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


class TestOwnerGuardrailHelpers:
    """Tests for owner-only conversational guardrail helpers."""

    async def test_load_recent_messages_for_guardrails_handles_exception(self):
        """Guardrail context loading should degrade to an empty list on failure."""
        agent = _make_agent()
        agent._memory.get_recent_context = AsyncMock(side_effect=RuntimeError("qdrant offline"))

        result = await agent._load_recent_messages_for_guardrails(
            user_id=123,
            channel_id=456,
        )

        assert result == []

    async def test_load_recent_messages_for_guardrails_rejects_non_list(self):
        """Guardrail context loading should reject non-list payloads."""
        agent = _make_agent()
        agent._memory.get_recent_context = AsyncMock(return_value={"role": "assistant"})

        result = await agent._load_recent_messages_for_guardrails(
            user_id=123,
            channel_id=456,
        )

        assert result == []

    def test_owner_repair_request_is_meta_only(self):
        """Repair detection should catch meta-questions, not normal rewrites."""
        agent = _make_agent()

        assert agent._is_owner_repair_request("What happened with that response?") is True
        assert agent._is_owner_repair_request("Why did you answer like that?") is True
        assert agent._is_owner_repair_request("Can you try that again but warmer?") is False

    def test_owner_conversation_continuation_and_trace_helpers(self):
        """Continuation helpers should recognize short referential follow-ups."""
        agent = _make_agent()
        recent_messages = [
            {"role": "user", "content": "That script still sounds stiff."},
            {"role": "assistant", "content": "I can revise it."},
        ]

        assert (
            agent._is_owner_conversation_continuation(
                "Can you make that sound warmer?",
                recent_messages,
            )
            is True
        )
        assert (
            agent._is_owner_conversation_continuation(
                "Write a full launch plan for me",
                [],
            )
            is False
        )
        assert agent._find_last_message_by_role(recent_messages, "assistant") == recent_messages[1]
        assert (
            agent._find_previous_user_turn(
                recent_messages,
                before_message=recent_messages[1],
            )
            == recent_messages[0]
        )
        assert agent._extract_routing_trace({"routing_trace": {"final_intent": "dev_watcher"}}) == {
            "final_intent": "dev_watcher"
        }
        assert agent._extract_routing_trace("bad") is None

    def test_owner_repair_response_without_trace_uses_generic_explanation(self):
        """Repair explanation should still work when no routing trace was stored yet."""
        agent = _make_agent()

        response = agent._build_owner_repair_response(
            recent_messages=[
                {"role": "user", "content": "I needed help rewriting this intro."},
                {"role": "assistant", "content": "Here is an unrelated answer."},
            ]
        )

        assert "over-interpreted" in response
        assert "current conversation" in response


class TestAdditionalIntentParsers:
    """Additional coverage for parser branches in Agent core."""

    def test_parse_deadline_and_format_deadline(self):
        """Deadline helpers should parse valid ISO values and ignore invalid ones."""
        agent = _make_agent()

        assert agent._parse_deadline("") is None
        assert agent._parse_deadline("not-a-date") is None
        assert agent._format_deadline("2026-03-10T09:30:00Z") == "2026-03-10"

    def test_parse_personal_model_intent_branches(self):
        """Personal-model parser should cover all explicit branch families."""
        agent = _make_agent()

        assert agent._parse_personal_model_intent("show my contacts") == "personal_contacts"
        assert agent._parse_personal_model_intent("forget this learning") == "personal_forget"
        assert agent._parse_personal_model_intent("export my personal data") == "personal_export"
        assert agent._parse_personal_model_intent("show my policies") == "personal_policies"
        assert agent._parse_personal_model_intent("set my timezone to PST") == "personal_update"
        assert agent._parse_personal_model_intent("what do you know about me") == "personal_summary"

    def test_parse_email_intent_branches(self):
        """Email parser should cover branch-specific intents."""
        agent = _make_agent()

        assert agent._parse_email_intent("review draft emails") == "email_drafts"
        assert agent._parse_email_intent("give me a weekly digest") == "email_digest"
        assert agent._parse_email_intent("gmail account status") == "email_status"
        assert agent._parse_email_intent("find email from Alice") == "email_search"
        assert agent._parse_email_intent("show my calendar events today") == "email_calendar"
        assert agent._parse_email_intent("any unread or urgent mail?") == "email_unread"
        assert agent._parse_email_intent("check email") == "email_check"

    def test_parse_email_router_intent_branches(self):
        """Provider-agnostic email router parser should cover management actions."""
        agent = _make_agent()

        assert agent._parse_email_router_intent("connect gmail") == "email_connect"
        assert agent._parse_email_router_intent("remove email account") == "email_disconnect"
        assert (
            agent._parse_email_router_intent("set the primary calendar")
            == "email_set_primary_calendar"
        )
        assert (
            agent._parse_email_router_intent("set the primary task list")
            == "email_set_primary_task_list"
        )
        assert agent._parse_email_router_intent("show email queue status") == "email_queue_status"
        assert agent._parse_email_router_intent("resume email queue") == "email_queue_resume"
        assert agent._parse_email_router_intent("email account status") == "email_status"
        assert agent._parse_email_router_intent("route my inbox") == "email_route"

    def test_parse_health_update_and_youtube_intents(self):
        """Health, update, and YouTube parsers should cover their explicit branches."""
        agent = _make_agent()

        assert agent._parse_health_intent("show yesterday's report") == "health_report"
        assert agent._parse_health_intent("run system diagnostics") == "system_status"
        assert agent._parse_update_intent("resume updates") == "resume_updates"
        assert agent._parse_update_intent("rollback the update") == "rollback_update"
        assert agent._parse_update_intent("install the latest release") == "apply_update"
        assert agent._parse_update_intent("what version are you on") == "update_status"
        assert (
            agent._parse_youtube_intent("channel analysis report", "intelligence")
            == "yt_analyze_channel"
        )
        assert (
            agent._parse_youtube_intent("show strategy history", "strategy")
            == "yt_strategy_history"
        )
        assert (
            agent._parse_youtube_intent("setup YouTube management", "management")
            == "yt_configure_management"
        )
        assert agent._parse_youtube_intent("health audit", "management") == "yt_channel_health"
        assert (
            agent._parse_youtube_intent("state status", "management") == "yt_get_management_state"
        )
        assert agent._parse_youtube_intent("unknown", "unknown") == "unknown"
