"""Tests for Task Manager Skill."""

from datetime import datetime, timedelta
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
