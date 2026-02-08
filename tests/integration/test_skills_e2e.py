"""End-to-end integration tests for built-in skills.

Exercises TaskManagerSkill, CalendarSkill, and ProfileSkill through the
SkillRegistry, verifying request handling, response structure, and intent
routing. All skills run with ``memory=None`` so no external services are
required.
"""

from datetime import datetime, timedelta
from uuid import UUID

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.calendar import CalendarSkill
from zetherion_ai.skills.profile_skill import ProfileSkill
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.task_manager import TaskManagerSkill

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_USER = "test-user-42"


@pytest.fixture()
async def registry() -> SkillRegistry:
    """Build a SkillRegistry with all three built-in skills registered and initialised."""
    reg = SkillRegistry()

    reg.register(TaskManagerSkill(memory=None))
    reg.register(CalendarSkill(memory=None))
    reg.register(ProfileSkill(memory=None))

    init_results = await reg.initialize_all()
    # All three skills should initialise successfully
    assert all(init_results.values()), f"Some skills failed to initialise: {init_results}"

    return reg


# ---------------------------------------------------------------------------
# TaskManagerSkill
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_task_create(registry: SkillRegistry) -> None:
    """create_task intent should create a task and return its data."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="create_task",
        message="Write integration tests",
        context={
            "title": "Write integration tests",
            "description": "Cover all three skills",
            "priority": "high",
            "project": "zetherion",
        },
    )

    response = await registry.handle_request(request)

    assert response.success is True
    assert "Created task" in response.message
    assert "task" in response.data
    task = response.data["task"]
    assert task["title"] == "Write integration tests"
    assert task["description"] == "Cover all three skills"
    assert task["priority"] == 3  # HIGH = 3
    assert task["project"] == "zetherion"
    assert task["status"] == "todo"
    # The id should be a valid UUID string
    UUID(task["id"])


@pytest.mark.integration
async def test_task_list(registry: SkillRegistry) -> None:
    """list_tasks intent should return tasks for the user."""
    # First create a task so there is something to list
    create_req = SkillRequest(
        user_id=TEST_USER,
        intent="create_task",
        message="Listable task",
        context={"title": "Listable task"},
    )
    await registry.handle_request(create_req)

    # Now list
    list_req = SkillRequest(
        user_id=TEST_USER,
        intent="list_tasks",
        message="Show my tasks",
        context={},
    )
    response = await registry.handle_request(list_req)

    assert response.success is True
    assert "tasks" in response.data
    assert response.data["count"] >= 1
    assert any(t["title"] == "Listable task" for t in response.data["tasks"])


@pytest.mark.integration
async def test_task_complete(registry: SkillRegistry) -> None:
    """complete_task intent should mark a task as done."""
    # Create
    create_resp = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="create_task",
            message="Completable task",
            context={"title": "Completable task"},
        )
    )
    task_id = create_resp.data["task"]["id"]

    # Complete
    complete_resp = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="complete_task",
            message="Done",
            context={"task_id": task_id},
        )
    )

    assert complete_resp.success is True
    assert "Completed task" in complete_resp.message
    assert complete_resp.data["task"]["status"] == "done"
    assert complete_resp.data["task"]["completed_at"] is not None


@pytest.mark.integration
async def test_task_summary(registry: SkillRegistry) -> None:
    """task_summary intent should return aggregate stats."""
    # Seed a task
    await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="create_task",
            message="Summary seed",
            context={"title": "Summary seed"},
        )
    )

    response = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="task_summary",
            message="Summarise my tasks",
            context={},
        )
    )

    assert response.success is True
    assert "summary" in response.data
    summary = response.data["summary"]
    assert summary["total"] >= 1
    assert "by_status" in summary


@pytest.mark.integration
async def test_task_unknown_intent(registry: SkillRegistry) -> None:
    """An unknown task intent should return an error response."""
    skill = registry.get_skill("task_manager")
    assert skill is not None

    response = await skill.safe_handle(
        SkillRequest(
            user_id=TEST_USER,
            intent="nonexistent_intent",
            message="bad intent",
            context={},
        )
    )

    assert response.success is False
    assert "Unknown intent" in (response.error or response.message)


# ---------------------------------------------------------------------------
# CalendarSkill
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_calendar_schedule_event(registry: SkillRegistry) -> None:
    """schedule_event intent should create a calendar event."""
    start = (datetime.now() + timedelta(hours=2)).isoformat()
    end = (datetime.now() + timedelta(hours=3)).isoformat()

    request = SkillRequest(
        user_id=TEST_USER,
        intent="schedule_event",
        message="Team standup",
        context={
            "title": "Team standup",
            "description": "Daily sync",
            "event_type": "meeting",
            "start_time": start,
            "end_time": end,
            "location": "Zoom",
            "participants": ["alice", "bob"],
        },
    )

    response = await registry.handle_request(request)

    assert response.success is True
    assert "Scheduled" in response.message
    event = response.data["event"]
    assert event["title"] == "Team standup"
    assert event["event_type"] == "meeting"
    assert event["location"] == "Zoom"
    assert event["participants"] == ["alice", "bob"]
    UUID(event["id"])


@pytest.mark.integration
async def test_calendar_today_schedule(registry: SkillRegistry) -> None:
    """today_schedule intent should return today's events."""
    # Seed an event happening today
    now_plus_1h = (datetime.now() + timedelta(hours=1)).isoformat()
    await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="schedule_event",
            message="Today event",
            context={
                "title": "Today event",
                "start_time": now_plus_1h,
            },
        )
    )

    response = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="today_schedule",
            message="What's on today?",
            context={},
        )
    )

    assert response.success is True
    assert "events" in response.data
    assert "count" in response.data
    # The seeded event should appear
    assert response.data["count"] >= 1
    assert any(e["title"] == "Today event" for e in response.data["events"])


@pytest.mark.integration
async def test_calendar_list_events(registry: SkillRegistry) -> None:
    """list_events intent should return upcoming events within a window."""
    # Seed a future event
    future_time = (datetime.now() + timedelta(days=2)).isoformat()
    await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="schedule_event",
            message="Future event",
            context={
                "title": "Future event",
                "start_time": future_time,
            },
        )
    )

    response = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="list_events",
            message="List my events",
            context={"days": 7},
        )
    )

    assert response.success is True
    assert "events" in response.data
    assert response.data["count"] >= 1


@pytest.mark.integration
async def test_calendar_check_availability(registry: SkillRegistry) -> None:
    """check_availability intent should report conflicts."""
    # Seed an event
    base = datetime.now() + timedelta(hours=5)
    start = base.isoformat()
    end = (base + timedelta(hours=1)).isoformat()

    await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="schedule_event",
            message="Blocker event",
            context={
                "title": "Blocker event",
                "start_time": start,
                "end_time": end,
            },
        )
    )

    # Check overlapping time
    response = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="check_availability",
            message="Am I free?",
            context={
                "start_time": start,
                "end_time": end,
            },
        )
    )

    assert response.success is True
    assert response.data["available"] is False
    assert len(response.data["conflicts"]) >= 1


# ---------------------------------------------------------------------------
# ProfileSkill
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_profile_summary_empty(registry: SkillRegistry) -> None:
    """profile_summary with no data should return empty summary."""
    response = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="profile_summary",
            message="What do you know about me?",
            context={},
        )
    )

    assert response.success is True
    # With memory=None and no profile_builder the skill returns an empty summary
    assert "summary" in response.data


@pytest.mark.integration
async def test_profile_export_empty(registry: SkillRegistry) -> None:
    """profile_export with no data should return empty export."""
    response = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="profile_export",
            message="Export my data",
            context={},
        )
    )

    assert response.success is True
    assert "export" in response.data
    assert response.data["export"] == []


@pytest.mark.integration
async def test_profile_confidence_empty(registry: SkillRegistry) -> None:
    """profile_confidence with no data should return empty report."""
    response = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="profile_confidence",
            message="Show confidence",
            context={},
        )
    )

    assert response.success is True
    assert "report" in response.data


@pytest.mark.integration
async def test_profile_update_without_builder(registry: SkillRegistry) -> None:
    """profile_update without a profile_builder should return an error."""
    response = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="profile_update",
            message="Update profile",
            context={
                "category": "preferences",
                "key": "theme",
                "value": "dark",
            },
        )
    )

    # Without a profile_builder the skill cannot perform updates
    assert response.success is False
    assert "Profile builder not available" in (response.error or "")


# ---------------------------------------------------------------------------
# Intent routing through the registry
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_registry_routes_task_intents(registry: SkillRegistry) -> None:
    """Registry should route task intents to TaskManagerSkill."""
    intents = registry.list_intents()
    assert intents.get("create_task") == "task_manager"
    assert intents.get("list_tasks") == "task_manager"
    assert intents.get("complete_task") == "task_manager"
    assert intents.get("task_summary") == "task_manager"


@pytest.mark.integration
async def test_registry_routes_calendar_intents(registry: SkillRegistry) -> None:
    """Registry should route calendar intents to CalendarSkill."""
    intents = registry.list_intents()
    assert intents.get("schedule_event") == "calendar"
    assert intents.get("list_events") == "calendar"
    assert intents.get("today_schedule") == "calendar"
    assert intents.get("check_availability") == "calendar"


@pytest.mark.integration
async def test_registry_routes_profile_intents(registry: SkillRegistry) -> None:
    """Registry should route profile intents to ProfileSkill."""
    intents = registry.list_intents()
    assert intents.get("profile_summary") == "profile_manager"
    assert intents.get("profile_view") == "profile_manager"
    assert intents.get("profile_export") == "profile_manager"
    assert intents.get("profile_confidence") == "profile_manager"


@pytest.mark.integration
async def test_registry_unknown_intent_returns_error(registry: SkillRegistry) -> None:
    """Routing an unknown intent should yield an error response."""
    response = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="totally_unknown_intent",
            message="???",
            context={},
        )
    )

    assert response.success is False
    assert "No skill found" in (response.error or "")


@pytest.mark.integration
async def test_registry_skill_count(registry: SkillRegistry) -> None:
    """The registry should contain exactly 3 skills."""
    assert registry.skill_count == 3


@pytest.mark.integration
async def test_all_skills_ready(registry: SkillRegistry) -> None:
    """After initialize_all, every skill should be in READY state."""
    ready = registry.list_ready_skills()
    assert len(ready) == 3
    for meta in ready:
        skill = registry.get_skill(meta.name)
        assert skill is not None
        assert skill.status == SkillStatus.READY


@pytest.mark.integration
async def test_registry_status_summary(registry: SkillRegistry) -> None:
    """get_status_summary should report all skills as ready."""
    summary = registry.get_status_summary()
    assert summary["total_skills"] == 3
    assert summary["ready_count"] == 3
    assert summary["error_count"] == 0
    assert summary["total_intents"] > 0
