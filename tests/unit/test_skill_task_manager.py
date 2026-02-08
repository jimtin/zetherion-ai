"""Tests for Task Manager Skill."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.permissions import Permission
from zetherion_ai.skills.task_manager import (
    TASKS_COLLECTION,
    Task,
    TaskManagerSkill,
    TaskPriority,
    TaskStatus,
)


class TestTaskStatus:
    """Tests for TaskStatus enum."""

    def test_status_values(self) -> None:
        """TaskStatus should have expected values."""
        assert TaskStatus.BACKLOG.value == "backlog"
        assert TaskStatus.TODO.value == "todo"
        assert TaskStatus.IN_PROGRESS.value == "in_progress"
        assert TaskStatus.BLOCKED.value == "blocked"
        assert TaskStatus.DONE.value == "done"
        assert TaskStatus.CANCELLED.value == "cancelled"


class TestTaskPriority:
    """Tests for TaskPriority enum."""

    def test_priority_values(self) -> None:
        """TaskPriority should have numeric values for sorting."""
        assert TaskPriority.CRITICAL.value == 4
        assert TaskPriority.HIGH.value == 3
        assert TaskPriority.MEDIUM.value == 2
        assert TaskPriority.LOW.value == 1

    def test_from_string(self) -> None:
        """from_string should parse priority names."""
        assert TaskPriority.from_string("critical") == TaskPriority.CRITICAL
        assert TaskPriority.from_string("high") == TaskPriority.HIGH
        assert TaskPriority.from_string("medium") == TaskPriority.MEDIUM
        assert TaskPriority.from_string("low") == TaskPriority.LOW

    def test_from_string_aliases(self) -> None:
        """from_string should handle aliases."""
        assert TaskPriority.from_string("urgent") == TaskPriority.CRITICAL
        assert TaskPriority.from_string("important") == TaskPriority.HIGH
        assert TaskPriority.from_string("normal") == TaskPriority.MEDIUM

    def test_from_string_unknown(self) -> None:
        """from_string should default to MEDIUM for unknown values."""
        assert TaskPriority.from_string("unknown") == TaskPriority.MEDIUM
        assert TaskPriority.from_string("") == TaskPriority.MEDIUM


class TestTask:
    """Tests for Task dataclass."""

    def test_default_values(self) -> None:
        """Task should have sensible defaults."""
        task = Task()
        assert isinstance(task.id, UUID)
        assert task.user_id == ""
        assert task.title == ""
        assert task.status == TaskStatus.TODO
        assert task.priority == TaskPriority.MEDIUM
        assert task.project is None
        assert task.tags == []
        assert task.deadline is None

    def test_to_dict(self) -> None:
        """to_dict should serialize properly."""
        task = Task(
            user_id="user123",
            title="Test task",
            description="A test",
            priority=TaskPriority.HIGH,
            project="TestProject",
            tags=["urgent"],
        )
        data = task.to_dict()
        assert data["user_id"] == "user123"
        assert data["title"] == "Test task"
        assert data["priority"] == 3
        assert data["project"] == "TestProject"
        assert data["tags"] == ["urgent"]

    def test_from_dict(self) -> None:
        """from_dict should deserialize properly."""
        data = {
            "id": str(uuid4()),
            "user_id": "user456",
            "title": "Parsed task",
            "status": "in_progress",
            "priority": 4,
            "project": "Work",
            "tags": ["review"],
            "created_at": "2026-02-06T10:00:00",
            "updated_at": "2026-02-06T11:00:00",
        }
        task = Task.from_dict(data)
        assert task.user_id == "user456"
        assert task.title == "Parsed task"
        assert task.status == TaskStatus.IN_PROGRESS
        assert task.priority == TaskPriority.CRITICAL
        assert task.project == "Work"

    def test_is_overdue_no_deadline(self) -> None:
        """is_overdue should return False without deadline."""
        task = Task()
        assert task.is_overdue() is False

    def test_is_overdue_future_deadline(self) -> None:
        """is_overdue should return False for future deadline."""
        task = Task(deadline=datetime.now() + timedelta(days=1))
        assert task.is_overdue() is False

    def test_is_overdue_past_deadline(self) -> None:
        """is_overdue should return True for past deadline."""
        task = Task(deadline=datetime.now() - timedelta(days=1))
        assert task.is_overdue() is True

    def test_is_overdue_completed_task(self) -> None:
        """is_overdue should return False for completed tasks."""
        task = Task(
            deadline=datetime.now() - timedelta(days=1),
            status=TaskStatus.DONE,
        )
        assert task.is_overdue() is False

    def test_is_stale(self) -> None:
        """is_stale should detect old tasks."""
        old_task = Task(updated_at=datetime.now() - timedelta(days=10))
        assert old_task.is_stale(days=7) is True

    def test_is_stale_recent(self) -> None:
        """is_stale should return False for recent tasks."""
        recent_task = Task(updated_at=datetime.now() - timedelta(days=1))
        assert recent_task.is_stale(days=7) is False

    def test_is_stale_done(self) -> None:
        """is_stale should return False for completed tasks."""
        done_task = Task(
            updated_at=datetime.now() - timedelta(days=10),
            status=TaskStatus.DONE,
        )
        assert done_task.is_stale(days=7) is False


class TestTaskManagerSkill:
    """Tests for TaskManagerSkill."""

    def test_metadata(self) -> None:
        """Skill should have correct metadata."""
        skill = TaskManagerSkill()
        meta = skill.metadata
        assert meta.name == "task_manager"
        assert meta.version == "1.0.0"
        assert Permission.READ_OWN_COLLECTION in meta.permissions
        assert Permission.WRITE_OWN_COLLECTION in meta.permissions
        assert Permission.SEND_MESSAGES in meta.permissions
        assert TASKS_COLLECTION in meta.collections
        assert "create_task" in meta.intents
        assert "list_tasks" in meta.intents

    def test_initial_status(self) -> None:
        """Skill should start uninitialized."""
        skill = TaskManagerSkill()
        assert skill.status == SkillStatus.UNINITIALIZED

    @pytest.mark.asyncio
    async def test_initialize_no_memory(self) -> None:
        """Skill should initialize without memory."""
        skill = TaskManagerSkill()
        result = await skill.safe_initialize()
        assert result is True
        assert skill.status == SkillStatus.READY

    @pytest.mark.asyncio
    async def test_handle_create_task(self) -> None:
        """Skill should handle task creation."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="create_task",
            message="Buy groceries",
            context={
                "title": "Buy groceries",
                "priority": "high",
                "project": "Personal",
            },
        )

        response = await skill.handle(request)
        assert response.success is True
        assert "Buy groceries" in response.message
        assert response.data["task"]["title"] == "Buy groceries"
        assert response.data["task"]["priority"] == TaskPriority.HIGH.value

    @pytest.mark.asyncio
    async def test_handle_list_tasks(self) -> None:
        """Skill should handle task listing."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create a task first
        create_request = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={"title": "Task 1"},
        )
        await skill.handle(create_request)

        # List tasks
        list_request = SkillRequest(
            user_id="user123",
            intent="list_tasks",
        )
        response = await skill.handle(list_request)
        assert response.success is True
        assert response.data["count"] == 1

    @pytest.mark.asyncio
    async def test_handle_complete_task(self) -> None:
        """Skill should handle task completion."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create a task
        create_request = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={"title": "Complete me"},
        )
        create_response = await skill.handle(create_request)
        task_id = create_response.data["task"]["id"]

        # Complete the task
        complete_request = SkillRequest(
            user_id="user123",
            intent="complete_task",
            context={"task_id": task_id},
        )
        response = await skill.handle(complete_request)
        assert response.success is True
        assert response.data["task"]["status"] == TaskStatus.DONE.value

    @pytest.mark.asyncio
    async def test_handle_delete_task(self) -> None:
        """Skill should handle task deletion."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create a task
        create_request = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={"title": "Delete me"},
        )
        create_response = await skill.handle(create_request)
        task_id = create_response.data["task"]["id"]

        # Delete the task
        delete_request = SkillRequest(
            user_id="user123",
            intent="delete_task",
            context={"task_id": task_id},
        )
        response = await skill.handle(delete_request)
        assert response.success is True

        # Verify deletion
        list_request = SkillRequest(
            user_id="user123",
            intent="list_tasks",
        )
        list_response = await skill.handle(list_request)
        assert list_response.data["count"] == 0

    @pytest.mark.asyncio
    async def test_handle_task_summary(self) -> None:
        """Skill should handle task summary."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create tasks
        for title in ["Task 1", "Task 2"]:
            request = SkillRequest(
                user_id="user123",
                intent="create_task",
                context={"title": title},
            )
            await skill.handle(request)

        # Get summary
        summary_request = SkillRequest(
            user_id="user123",
            intent="task_summary",
        )
        response = await skill.handle(summary_request)
        assert response.success is True
        assert response.data["summary"]["total"] == 2
        assert response.data["summary"]["active"] == 2

    @pytest.mark.asyncio
    async def test_handle_update_task(self) -> None:
        """Skill should handle task updates."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create a task
        create_request = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={"title": "Original title"},
        )
        create_response = await skill.handle(create_request)
        task_id = create_response.data["task"]["id"]

        # Update the task
        update_request = SkillRequest(
            user_id="user123",
            intent="update_task",
            context={
                "task_id": task_id,
                "title": "Updated title",
                "priority": "critical",
            },
        )
        response = await skill.handle(update_request)
        assert response.success is True
        assert response.data["task"]["title"] == "Updated title"
        assert response.data["task"]["priority"] == TaskPriority.CRITICAL.value

    @pytest.mark.asyncio
    async def test_handle_unknown_intent(self) -> None:
        """Skill should error on unknown intent."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(intent="unknown_intent")
        response = await skill.handle(request)
        assert response.success is False
        assert "Unknown intent" in response.error

    @pytest.mark.asyncio
    async def test_heartbeat_deadline_reminder(self) -> None:
        """Skill should generate deadline reminders."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create task with upcoming deadline
        deadline = datetime.now() + timedelta(hours=12)
        request = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={
                "title": "Urgent task",
                "deadline": deadline.isoformat(),
            },
        )
        await skill.handle(request)

        # Run heartbeat
        actions = await skill.on_heartbeat(["user123"])
        reminder_actions = [a for a in actions if a.action_type == "deadline_reminder"]
        assert len(reminder_actions) == 1
        assert reminder_actions[0].priority == 8

    @pytest.mark.asyncio
    async def test_heartbeat_overdue_alert(self) -> None:
        """Skill should generate overdue alerts."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create overdue task
        request = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={"title": "Overdue task"},
        )
        response = await skill.handle(request)

        # Manually set deadline to past
        task_id = UUID(response.data["task"]["id"])
        skill._tasks_cache["user123"][task_id].deadline = datetime.now() - timedelta(days=1)

        # Run heartbeat
        actions = await skill.on_heartbeat(["user123"])
        overdue_actions = [a for a in actions if a.action_type == "overdue_alert"]
        assert len(overdue_actions) == 1
        assert overdue_actions[0].priority == 9

    def test_get_system_prompt_fragment_no_tasks(self) -> None:
        """get_system_prompt_fragment should return None without tasks."""
        skill = TaskManagerSkill()
        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is None

    def test_get_system_prompt_fragment_with_tasks(self) -> None:
        """get_system_prompt_fragment should describe active tasks."""
        skill = TaskManagerSkill()
        task_id = uuid4()
        skill._tasks_cache["user123"] = {
            task_id: Task(
                id=task_id,
                user_id="user123",
                title="Test",
                priority=TaskPriority.HIGH,
            )
        }

        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is not None
        assert "1 active" in fragment
        assert "high-priority" in fragment

    @pytest.mark.asyncio
    async def test_cleanup(self) -> None:
        """Skill should clean up resources."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create a task
        request = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={"title": "Test"},
        )
        await skill.handle(request)
        assert len(skill._tasks_cache) > 0

        # Cleanup
        await skill.cleanup()
        assert len(skill._tasks_cache) == 0

    @pytest.mark.asyncio
    async def test_handle_create_invalid_deadline(self) -> None:
        """_handle_create should skip invalid deadline format without error."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={
                "title": "Task with bad deadline",
                "deadline": "not-a-date",
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["task"]["title"] == "Task with bad deadline"
        # Deadline should remain None because parsing failed
        assert response.data["task"]["deadline"] is None

    @pytest.mark.asyncio
    async def test_handle_list_filter_by_status(self) -> None:
        """_handle_list should filter tasks by status."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create two tasks
        for title in ["Todo task", "Done task"]:
            req = SkillRequest(
                user_id="user123",
                intent="create_task",
                context={"title": title},
            )
            await skill.handle(req)

        # Complete the second task
        tasks = list(skill._tasks_cache["user123"].values())
        done_task = [t for t in tasks if t.title == "Done task"][0]
        done_task.status = TaskStatus.DONE

        # Filter by todo status
        list_req = SkillRequest(
            user_id="user123",
            intent="list_tasks",
            context={"status": "todo"},
        )
        response = await skill.handle(list_req)
        assert response.success is True
        assert response.data["count"] == 1
        assert response.data["tasks"][0]["title"] == "Todo task"

    @pytest.mark.asyncio
    async def test_handle_list_filter_by_priority(self) -> None:
        """_handle_list should filter tasks by priority."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create tasks with different priorities
        for title, priority in [("Low task", "low"), ("High task", "high")]:
            req = SkillRequest(
                user_id="user123",
                intent="create_task",
                context={"title": title, "priority": priority},
            )
            await skill.handle(req)

        # Filter by high priority
        list_req = SkillRequest(
            user_id="user123",
            intent="list_tasks",
            context={"priority": "high"},
        )
        response = await skill.handle(list_req)
        assert response.success is True
        assert response.data["count"] == 1
        assert response.data["tasks"][0]["title"] == "High task"

    @pytest.mark.asyncio
    async def test_handle_list_filter_by_project(self) -> None:
        """_handle_list should filter tasks by project."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create tasks in different projects
        for title, project in [("Work task", "Work"), ("Home task", "Home")]:
            req = SkillRequest(
                user_id="user123",
                intent="create_task",
                context={"title": title, "project": project},
            )
            await skill.handle(req)

        # Filter by project
        list_req = SkillRequest(
            user_id="user123",
            intent="list_tasks",
            context={"project": "Work"},
        )
        response = await skill.handle(list_req)
        assert response.success is True
        assert response.data["count"] == 1
        assert response.data["tasks"][0]["title"] == "Work task"

    @pytest.mark.asyncio
    async def test_handle_update_no_task_id(self) -> None:
        """_handle_update should error when no task_id provided."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="update_task",
            context={},
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "No task_id" in response.error

    @pytest.mark.asyncio
    async def test_handle_update_invalid_task_id(self) -> None:
        """_handle_update should error on invalid task_id format."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="update_task",
            context={"task_id": "not-a-uuid"},
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "Invalid task_id" in response.error

    @pytest.mark.asyncio
    async def test_handle_update_task_not_found(self) -> None:
        """_handle_update should error when task does not exist."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="update_task",
            context={"task_id": str(uuid4())},
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "Task not found" in response.error

    @pytest.mark.asyncio
    async def test_handle_update_individual_fields(self) -> None:
        """_handle_update should update individual fields independently."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create a task
        create_req = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={"title": "Original", "priority": "low"},
        )
        create_resp = await skill.handle(create_req)
        task_id = create_resp.data["task"]["id"]

        # Update description only
        update_req = SkillRequest(
            user_id="user123",
            intent="update_task",
            context={"task_id": task_id, "description": "New description"},
        )
        resp = await skill.handle(update_req)
        assert resp.success is True
        assert resp.data["task"]["description"] == "New description"
        assert resp.data["task"]["title"] == "Original"

        # Update status
        update_req2 = SkillRequest(
            user_id="user123",
            intent="update_task",
            context={"task_id": task_id, "status": "in_progress"},
        )
        resp2 = await skill.handle(update_req2)
        assert resp2.success is True
        assert resp2.data["task"]["status"] == "in_progress"

        # Update project
        update_req3 = SkillRequest(
            user_id="user123",
            intent="update_task",
            context={"task_id": task_id, "project": "NewProject"},
        )
        resp3 = await skill.handle(update_req3)
        assert resp3.success is True
        assert resp3.data["task"]["project"] == "NewProject"

        # Update tags
        update_req4 = SkillRequest(
            user_id="user123",
            intent="update_task",
            context={"task_id": task_id, "tags": ["tag1", "tag2"]},
        )
        resp4 = await skill.handle(update_req4)
        assert resp4.success is True
        assert resp4.data["task"]["tags"] == ["tag1", "tag2"]

        # Update deadline
        deadline = datetime.now() + timedelta(days=5)
        update_req5 = SkillRequest(
            user_id="user123",
            intent="update_task",
            context={"task_id": task_id, "deadline": deadline.isoformat()},
        )
        resp5 = await skill.handle(update_req5)
        assert resp5.success is True
        assert resp5.data["task"]["deadline"] is not None

    @pytest.mark.asyncio
    async def test_handle_complete_no_task_id(self) -> None:
        """_handle_complete should error when no task_id provided."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="complete_task",
            context={},
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "No task_id" in response.error

    @pytest.mark.asyncio
    async def test_handle_complete_invalid_task_id(self) -> None:
        """_handle_complete should error on invalid task_id format."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="complete_task",
            context={"task_id": "bad-uuid"},
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "Invalid task_id" in response.error

    @pytest.mark.asyncio
    async def test_handle_complete_task_not_found(self) -> None:
        """_handle_complete should error when task does not exist."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="complete_task",
            context={"task_id": str(uuid4())},
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "Task not found" in response.error

    @pytest.mark.asyncio
    async def test_handle_delete_no_task_id(self) -> None:
        """_handle_delete should error when no task_id provided."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="delete_task",
            context={},
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "No task_id" in response.error

    @pytest.mark.asyncio
    async def test_handle_delete_invalid_task_id(self) -> None:
        """_handle_delete should error on invalid task_id format."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="delete_task",
            context={"task_id": "invalid"},
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "Invalid task_id" in response.error

    @pytest.mark.asyncio
    async def test_handle_delete_task_not_found(self) -> None:
        """_handle_delete should error when task does not exist."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="delete_task",
            context={"task_id": str(uuid4())},
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "Task not found" in response.error

    @pytest.mark.asyncio
    async def test_handle_summary_overdue_and_stale(self) -> None:
        """_handle_summary should count overdue and stale tasks."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create an overdue task
        req1 = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={"title": "Overdue task"},
        )
        resp1 = await skill.handle(req1)
        overdue_id = UUID(resp1.data["task"]["id"])
        skill._tasks_cache["user123"][overdue_id].deadline = datetime.now() - timedelta(days=1)

        # Create a stale task
        req2 = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={"title": "Stale task"},
        )
        resp2 = await skill.handle(req2)
        stale_id = UUID(resp2.data["task"]["id"])
        skill._tasks_cache["user123"][stale_id].updated_at = datetime.now() - timedelta(days=10)

        # Get summary
        summary_req = SkillRequest(
            user_id="user123",
            intent="task_summary",
        )
        response = await skill.handle(summary_req)
        assert response.success is True
        assert response.data["summary"]["overdue"] == 1
        assert response.data["summary"]["stale"] == 1
        assert "overdue" in response.message.lower()
        assert "stale" in response.message.lower()

    @pytest.mark.asyncio
    async def test_handle_summary_by_project(self) -> None:
        """_handle_summary should count tasks by project."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create tasks in different projects
        for title, project in [
            ("T1", "Alpha"),
            ("T2", "Alpha"),
            ("T3", "Beta"),
        ]:
            req = SkillRequest(
                user_id="user123",
                intent="create_task",
                context={"title": title, "project": project},
            )
            await skill.handle(req)

        summary_req = SkillRequest(
            user_id="user123",
            intent="task_summary",
        )
        response = await skill.handle(summary_req)
        assert response.data["summary"]["by_project"]["Alpha"] == 2
        assert response.data["summary"]["by_project"]["Beta"] == 1

    @pytest.mark.asyncio
    async def test_heartbeat_stale_tasks(self) -> None:
        """on_heartbeat should generate stale task check actions."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create a stale task
        req = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={"title": "Stale task"},
        )
        resp = await skill.handle(req)
        stale_id = UUID(resp.data["task"]["id"])
        skill._tasks_cache["user123"][stale_id].updated_at = datetime.now() - timedelta(days=10)

        actions = await skill.on_heartbeat(["user123"])
        stale_actions = [a for a in actions if a.action_type == "stale_task_check"]
        assert len(stale_actions) == 1
        assert stale_actions[0].priority == 3
        assert stale_actions[0].data["count"] == 1

    @pytest.mark.asyncio
    async def test_heartbeat_no_actions_for_completed_tasks(self) -> None:
        """on_heartbeat should not flag completed tasks as overdue or stale."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create a task that is done with past deadline and old update
        req = SkillRequest(
            user_id="user123",
            intent="create_task",
            context={"title": "Done task"},
        )
        resp = await skill.handle(req)
        task_id = UUID(resp.data["task"]["id"])
        task = skill._tasks_cache["user123"][task_id]
        task.status = TaskStatus.DONE
        task.deadline = datetime.now() - timedelta(days=2)
        task.updated_at = datetime.now() - timedelta(days=10)

        actions = await skill.on_heartbeat(["user123"])
        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_heartbeat_multiple_users(self) -> None:
        """on_heartbeat should check all provided user IDs."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        # Create overdue task for user1
        req1 = SkillRequest(
            user_id="user1",
            intent="create_task",
            context={"title": "Overdue task"},
        )
        resp1 = await skill.handle(req1)
        tid1 = UUID(resp1.data["task"]["id"])
        skill._tasks_cache["user1"][tid1].deadline = datetime.now() - timedelta(days=1)

        # Create approaching-deadline task for user2
        req2 = SkillRequest(
            user_id="user2",
            intent="create_task",
            context={"title": "Soon task"},
        )
        resp2 = await skill.handle(req2)
        tid2 = UUID(resp2.data["task"]["id"])
        skill._tasks_cache["user2"][tid2].deadline = datetime.now() + timedelta(hours=6)

        actions = await skill.on_heartbeat(["user1", "user2"])
        user1_actions = [a for a in actions if a.user_id == "user1"]
        user2_actions = [a for a in actions if a.user_id == "user2"]
        assert len(user1_actions) >= 1
        assert len(user2_actions) >= 1

    def test_get_system_prompt_fragment_with_overdue(self) -> None:
        """get_system_prompt_fragment should mention overdue tasks."""
        skill = TaskManagerSkill()
        tid = uuid4()
        skill._tasks_cache["user123"] = {
            tid: Task(
                id=tid,
                user_id="user123",
                title="Overdue task",
                priority=TaskPriority.MEDIUM,
                deadline=datetime.now() - timedelta(days=1),
            )
        }

        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is not None
        assert "overdue" in fragment.lower()

    def test_get_system_prompt_fragment_only_done_tasks(self) -> None:
        """get_system_prompt_fragment should return None if all tasks are done."""
        skill = TaskManagerSkill()
        tid = uuid4()
        skill._tasks_cache["user123"] = {
            tid: Task(
                id=tid,
                user_id="user123",
                title="Done task",
                status=TaskStatus.DONE,
            )
        }

        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is None

    def test_get_system_prompt_fragment_empty_cache(self) -> None:
        """get_system_prompt_fragment should return None for empty task dict."""
        skill = TaskManagerSkill()
        skill._tasks_cache["user123"] = {}

        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is None

    def test_get_system_prompt_fragment_high_priority_count(self) -> None:
        """get_system_prompt_fragment should count high-priority and critical tasks."""
        skill = TaskManagerSkill()
        tid1 = uuid4()
        tid2 = uuid4()
        tid3 = uuid4()
        skill._tasks_cache["user123"] = {
            tid1: Task(
                id=tid1,
                user_id="user123",
                title="Critical",
                priority=TaskPriority.CRITICAL,
            ),
            tid2: Task(
                id=tid2,
                user_id="user123",
                title="High",
                priority=TaskPriority.HIGH,
            ),
            tid3: Task(
                id=tid3,
                user_id="user123",
                title="Low",
                priority=TaskPriority.LOW,
            ),
        }

        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is not None
        assert "3 active" in fragment
        assert "2 high-priority" in fragment


class TestTaskManagerSkillQdrantStorage:
    """Tests for Qdrant storage paths in TaskManagerSkill."""

    @pytest.mark.asyncio
    async def test_store_task_with_memory(self) -> None:
        """_store_task should call Qdrant when memory is available."""
        mock_memory = AsyncMock()
        skill = TaskManagerSkill(memory=mock_memory)
        await skill.safe_initialize()

        task = Task(
            user_id="user123",
            title="Test task",
            description="A description",
            project="TestProj",
            tags=["tag1"],
        )
        await skill._store_task(task)

        mock_memory.store_with_payload.assert_called_once()
        call_kwargs = mock_memory.store_with_payload.call_args
        assert call_kwargs.kwargs["collection_name"] == TASKS_COLLECTION
        assert call_kwargs.kwargs["point_id"] == str(task.id)
        assert "Test task" in call_kwargs.kwargs["text"]
        assert "project:TestProj" in call_kwargs.kwargs["text"]
        assert "tag1" in call_kwargs.kwargs["text"]

    @pytest.mark.asyncio
    async def test_store_task_without_memory(self) -> None:
        """_store_task should only update cache when no memory."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        task = Task(user_id="user123", title="Cache-only task")
        await skill._store_task(task)

        assert "user123" in skill._tasks_cache
        assert task.id in skill._tasks_cache["user123"]

    @pytest.mark.asyncio
    async def test_get_task_from_qdrant(self) -> None:
        """_get_task should fall back to Qdrant when not in cache."""
        mock_memory = AsyncMock()
        task_id = uuid4()
        mock_memory.get_by_id = AsyncMock(
            return_value={
                "id": str(task_id),
                "user_id": "user123",
                "title": "From Qdrant",
                "status": "todo",
                "priority": 2,
                "created_at": datetime.now().isoformat(),
                "updated_at": datetime.now().isoformat(),
            }
        )
        skill = TaskManagerSkill(memory=mock_memory)
        await skill.safe_initialize()

        result = await skill._get_task("user123", task_id)
        assert result is not None
        assert result.title == "From Qdrant"
        mock_memory.get_by_id.assert_called_once_with(
            collection_name=TASKS_COLLECTION,
            point_id=str(task_id),
        )
        # Should also be cached now
        assert task_id in skill._tasks_cache["user123"]

    @pytest.mark.asyncio
    async def test_get_task_not_found_anywhere(self) -> None:
        """_get_task should return None when task is not in cache or Qdrant."""
        mock_memory = AsyncMock()
        mock_memory.get_by_id = AsyncMock(return_value=None)
        skill = TaskManagerSkill(memory=mock_memory)
        await skill.safe_initialize()

        result = await skill._get_task("user123", uuid4())
        assert result is None

    @pytest.mark.asyncio
    async def test_get_user_tasks_from_qdrant(self) -> None:
        """_get_user_tasks should query Qdrant when memory is available."""
        mock_memory = AsyncMock()
        task_id = uuid4()
        mock_memory.filter_by_field = AsyncMock(
            return_value=[
                {
                    "id": str(task_id),
                    "user_id": "user123",
                    "title": "Qdrant task",
                    "status": "todo",
                    "priority": 2,
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                }
            ]
        )
        skill = TaskManagerSkill(memory=mock_memory)
        await skill.safe_initialize()

        tasks = await skill._get_user_tasks("user123")
        assert len(tasks) == 1
        assert tasks[0].title == "Qdrant task"
        mock_memory.filter_by_field.assert_called_once_with(
            collection_name=TASKS_COLLECTION,
            field="user_id",
            value="user123",
        )

    @pytest.mark.asyncio
    async def test_get_user_tasks_from_cache(self) -> None:
        """_get_user_tasks should return from cache when no memory."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        tid = uuid4()
        skill._tasks_cache["user123"] = {
            tid: Task(id=tid, user_id="user123", title="Cached task"),
        }

        tasks = await skill._get_user_tasks("user123")
        assert len(tasks) == 1
        assert tasks[0].title == "Cached task"

    @pytest.mark.asyncio
    async def test_get_user_tasks_empty(self) -> None:
        """_get_user_tasks should return empty list for unknown user."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        tasks = await skill._get_user_tasks("unknown_user")
        assert tasks == []

    @pytest.mark.asyncio
    async def test_delete_task_with_memory(self) -> None:
        """_delete_task should call Qdrant delete when memory is available."""
        mock_memory = AsyncMock()
        skill = TaskManagerSkill(memory=mock_memory)
        await skill.safe_initialize()

        tid = uuid4()
        skill._tasks_cache["user123"] = {
            tid: Task(id=tid, user_id="user123", title="To delete"),
        }

        await skill._delete_task("user123", tid)

        assert tid not in skill._tasks_cache.get("user123", {})
        mock_memory.delete_by_id.assert_called_once_with(
            collection_name=TASKS_COLLECTION,
            point_id=str(tid),
        )

    @pytest.mark.asyncio
    async def test_delete_task_without_memory(self) -> None:
        """_delete_task should only remove from cache when no memory."""
        skill = TaskManagerSkill()
        await skill.safe_initialize()

        tid = uuid4()
        skill._tasks_cache["user123"] = {
            tid: Task(id=tid, user_id="user123", title="To delete"),
        }

        await skill._delete_task("user123", tid)
        assert tid not in skill._tasks_cache["user123"]

    @pytest.mark.asyncio
    async def test_initialize_with_memory(self) -> None:
        """initialize should create Qdrant collection when memory is provided."""
        mock_memory = AsyncMock()
        skill = TaskManagerSkill(memory=mock_memory)

        result = await skill.initialize()
        assert result is True
        mock_memory.ensure_collection.assert_called_once_with(
            TASKS_COLLECTION,
            vector_size=768,
        )

    @pytest.mark.asyncio
    async def test_initialize_with_memory_failure(self) -> None:
        """initialize should return False when Qdrant fails."""
        mock_memory = AsyncMock()
        mock_memory.ensure_collection = AsyncMock(side_effect=Exception("Connection refused"))
        skill = TaskManagerSkill(memory=mock_memory)

        result = await skill.initialize()
        assert result is False
