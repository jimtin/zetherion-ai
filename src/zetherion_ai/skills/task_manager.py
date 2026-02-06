"""Task Manager Skill for SecureClaw.

Provides task and project management capabilities:
- Create, update, complete, and delete tasks
- Group tasks by project
- Priority levels and status tracking
- Deadline monitoring with reminders
- Weekly summaries and stale task detection
"""

import contextlib
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.skills.task_manager")

# Collection name for task storage
TASKS_COLLECTION = "skill_tasks"


class TaskStatus(Enum):
    """Status of a task."""

    BACKLOG = "backlog"
    TODO = "todo"
    IN_PROGRESS = "in_progress"
    BLOCKED = "blocked"
    DONE = "done"
    CANCELLED = "cancelled"


class TaskPriority(Enum):
    """Priority levels for tasks."""

    CRITICAL = 4
    HIGH = 3
    MEDIUM = 2
    LOW = 1

    @classmethod
    def from_string(cls, value: str) -> "TaskPriority":
        """Parse priority from string."""
        mapping = {
            "critical": cls.CRITICAL,
            "high": cls.HIGH,
            "medium": cls.MEDIUM,
            "low": cls.LOW,
            "urgent": cls.CRITICAL,
            "important": cls.HIGH,
            "normal": cls.MEDIUM,
        }
        return mapping.get(value.lower(), cls.MEDIUM)


@dataclass
class Task:
    """A task or todo item."""

    id: UUID = field(default_factory=uuid4)
    user_id: str = ""
    title: str = ""
    description: str = ""
    status: TaskStatus = TaskStatus.TODO
    priority: TaskPriority = TaskPriority.MEDIUM
    project: str | None = None
    tags: list[str] = field(default_factory=list)
    deadline: datetime | None = None
    created_at: datetime = field(default_factory=datetime.now)
    updated_at: datetime = field(default_factory=datetime.now)
    completed_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "title": self.title,
            "description": self.description,
            "status": self.status.value,
            "priority": self.priority.value,
            "project": self.project,
            "tags": self.tags,
            "deadline": self.deadline.isoformat() if self.deadline else None,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Task":
        """Create from dictionary."""
        return cls(
            id=UUID(data["id"]) if data.get("id") else uuid4(),
            user_id=data.get("user_id", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            status=TaskStatus(data["status"]) if data.get("status") else TaskStatus.TODO,
            priority=TaskPriority(data["priority"])
            if data.get("priority")
            else TaskPriority.MEDIUM,
            project=data.get("project"),
            tags=data.get("tags", []),
            deadline=datetime.fromisoformat(data["deadline"]) if data.get("deadline") else None,
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now(),
            updated_at=datetime.fromisoformat(data["updated_at"])
            if data.get("updated_at")
            else datetime.now(),
            completed_at=datetime.fromisoformat(data["completed_at"])
            if data.get("completed_at")
            else None,
        )

    def is_overdue(self) -> bool:
        """Check if task is past its deadline."""
        if not self.deadline or self.status == TaskStatus.DONE:
            return False
        return datetime.now() > self.deadline

    def is_stale(self, days: int = 7) -> bool:
        """Check if task hasn't been updated recently."""
        if self.status in (TaskStatus.DONE, TaskStatus.CANCELLED):
            return False
        stale_threshold = datetime.now() - timedelta(days=days)
        return self.updated_at < stale_threshold


class TaskManagerSkill(Skill):
    """Skill for managing tasks and projects.

    Intents handled:
    - create_task: Create a new task
    - list_tasks: List tasks (optionally filtered)
    - update_task: Update task fields
    - complete_task: Mark task as done
    - delete_task: Delete a task
    - task_summary: Get a summary of tasks

    Heartbeat actions:
    - deadline_reminder: Warn about approaching deadlines
    - daily_digest: Morning task digest
    - stale_task_check: Detect tasks without updates
    """

    # Intents this skill handles
    INTENTS = [
        "create_task",
        "list_tasks",
        "update_task",
        "complete_task",
        "delete_task",
        "task_summary",
    ]

    def __init__(self, memory: "QdrantMemory | None" = None):
        """Initialize the task manager skill."""
        super().__init__(memory=memory)
        self._tasks_cache: dict[str, dict[UUID, Task]] = {}  # user_id -> {task_id -> Task}

    @property
    def metadata(self) -> SkillMetadata:
        """Return skill metadata."""
        return SkillMetadata(
            name="task_manager",
            description="Manage tasks, projects, and deadlines with reminders",
            version="1.0.0",
            permissions=PermissionSet(
                {
                    Permission.READ_OWN_COLLECTION,
                    Permission.WRITE_OWN_COLLECTION,
                    Permission.SEND_MESSAGES,
                    Permission.READ_PROFILE,
                }
            ),
            collections=[TASKS_COLLECTION],
            intents=self.INTENTS,
        )

    async def initialize(self) -> bool:
        """Initialize the skill and create collection if needed."""
        if not self._memory:
            log.warning("task_manager_no_memory", msg="No memory provided, using in-memory only")
            return True

        try:
            # Create collection if it doesn't exist
            await self._memory.ensure_collection(
                TASKS_COLLECTION,
                vector_size=768,  # Gemini embedding size
            )
            log.info("task_manager_initialized", collection=TASKS_COLLECTION)
            return True
        except Exception as e:
            log.error("task_manager_init_failed", error=str(e))
            return False

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle a task management request."""
        intent = request.intent
        handlers = {
            "create_task": self._handle_create,
            "list_tasks": self._handle_list,
            "update_task": self._handle_update,
            "complete_task": self._handle_complete,
            "delete_task": self._handle_delete,
            "task_summary": self._handle_summary,
        }

        handler = handlers.get(intent)
        if not handler:
            return SkillResponse.error_response(
                request.id,
                f"Unknown intent: {intent}",
            )

        return await handler(request)

    async def _handle_create(self, request: SkillRequest) -> SkillResponse:
        """Handle task creation."""
        context = request.context

        # Extract task fields from context
        task = Task(
            user_id=request.user_id,
            title=context.get("title", request.message),
            description=context.get("description", ""),
            priority=TaskPriority.from_string(context.get("priority", "medium")),
            project=context.get("project"),
            tags=context.get("tags", []),
        )

        # Parse deadline if provided
        deadline_str = context.get("deadline")
        if deadline_str:
            try:
                task.deadline = datetime.fromisoformat(deadline_str)
            except ValueError:
                log.warning("invalid_deadline_format", deadline=deadline_str)

        # Store task
        await self._store_task(task)

        log.info(
            "task_created",
            task_id=str(task.id),
            user_id=request.user_id,
            title=task.title,
        )

        return SkillResponse(
            request_id=request.id,
            message=f"Created task: {task.title}",
            data={"task": task.to_dict()},
        )

    async def _handle_list(self, request: SkillRequest) -> SkillResponse:
        """Handle task listing."""
        context = request.context

        # Get filter criteria
        status_filter = context.get("status")
        project_filter = context.get("project")
        priority_filter = context.get("priority")

        # Get all tasks for user
        tasks = await self._get_user_tasks(request.user_id)

        # Apply filters
        if status_filter:
            try:
                status = TaskStatus(status_filter)
                tasks = [t for t in tasks if t.status == status]
            except ValueError:
                pass

        if project_filter:
            tasks = [t for t in tasks if t.project == project_filter]

        if priority_filter:
            try:
                priority = TaskPriority.from_string(priority_filter)
                tasks = [t for t in tasks if t.priority == priority]
            except ValueError:
                pass

        # Sort by priority (highest first), then by deadline
        tasks.sort(
            key=lambda t: (
                -t.priority.value,
                t.deadline or datetime.max,
            )
        )

        task_list = [t.to_dict() for t in tasks]

        return SkillResponse(
            request_id=request.id,
            message=f"Found {len(tasks)} task(s)",
            data={"tasks": task_list, "count": len(tasks)},
        )

    async def _handle_update(self, request: SkillRequest) -> SkillResponse:
        """Handle task update."""
        context = request.context
        task_id_str = context.get("task_id")

        if not task_id_str:
            return SkillResponse.error_response(request.id, "No task_id provided")

        try:
            task_id = UUID(task_id_str)
        except ValueError:
            return SkillResponse.error_response(request.id, "Invalid task_id format")

        # Get the task
        task = await self._get_task(request.user_id, task_id)
        if not task:
            return SkillResponse.error_response(request.id, "Task not found")

        # Update fields
        if "title" in context:
            task.title = context["title"]
        if "description" in context:
            task.description = context["description"]
        if "status" in context:
            with contextlib.suppress(ValueError):
                task.status = TaskStatus(context["status"])
        if "priority" in context:
            task.priority = TaskPriority.from_string(context["priority"])
        if "project" in context:
            task.project = context["project"]
        if "tags" in context:
            task.tags = context["tags"]
        if "deadline" in context:
            with contextlib.suppress(ValueError, TypeError):
                task.deadline = datetime.fromisoformat(context["deadline"])

        task.updated_at = datetime.now()

        # Store updated task
        await self._store_task(task)

        log.info("task_updated", task_id=str(task_id), user_id=request.user_id)

        return SkillResponse(
            request_id=request.id,
            message=f"Updated task: {task.title}",
            data={"task": task.to_dict()},
        )

    async def _handle_complete(self, request: SkillRequest) -> SkillResponse:
        """Handle task completion."""
        context = request.context
        task_id_str = context.get("task_id")

        if not task_id_str:
            return SkillResponse.error_response(request.id, "No task_id provided")

        try:
            task_id = UUID(task_id_str)
        except ValueError:
            return SkillResponse.error_response(request.id, "Invalid task_id format")

        task = await self._get_task(request.user_id, task_id)
        if not task:
            return SkillResponse.error_response(request.id, "Task not found")

        task.status = TaskStatus.DONE
        task.completed_at = datetime.now()
        task.updated_at = datetime.now()

        await self._store_task(task)

        log.info("task_completed", task_id=str(task_id), user_id=request.user_id)

        return SkillResponse(
            request_id=request.id,
            message=f"Completed task: {task.title}",
            data={"task": task.to_dict()},
        )

    async def _handle_delete(self, request: SkillRequest) -> SkillResponse:
        """Handle task deletion."""
        context = request.context
        task_id_str = context.get("task_id")

        if not task_id_str:
            return SkillResponse.error_response(request.id, "No task_id provided")

        try:
            task_id = UUID(task_id_str)
        except ValueError:
            return SkillResponse.error_response(request.id, "Invalid task_id format")

        task = await self._get_task(request.user_id, task_id)
        if not task:
            return SkillResponse.error_response(request.id, "Task not found")

        await self._delete_task(request.user_id, task_id)

        log.info("task_deleted", task_id=str(task_id), user_id=request.user_id)

        return SkillResponse(
            request_id=request.id,
            message=f"Deleted task: {task.title}",
            data={"task_id": str(task_id)},
        )

    async def _handle_summary(self, request: SkillRequest) -> SkillResponse:
        """Handle task summary request."""
        tasks = await self._get_user_tasks(request.user_id)

        # Calculate summary stats
        by_status: dict[str, int] = {}
        by_priority: dict[str, int] = {}
        by_project: dict[str, int] = {}
        overdue_count = 0
        stale_count = 0

        for task in tasks:
            by_status[task.status.value] = by_status.get(task.status.value, 0) + 1
            by_priority[task.priority.name.lower()] = (
                by_priority.get(task.priority.name.lower(), 0) + 1
            )
            if task.project:
                by_project[task.project] = by_project.get(task.project, 0) + 1
            if task.is_overdue():
                overdue_count += 1
            if task.is_stale():
                stale_count += 1

        active_count = sum(
            count
            for status, count in by_status.items()
            if status not in (TaskStatus.DONE.value, TaskStatus.CANCELLED.value)
        )

        summary = {
            "total": len(tasks),
            "active": active_count,
            "by_status": by_status,
            "by_priority": by_priority,
            "by_project": by_project,
            "overdue": overdue_count,
            "stale": stale_count,
        }

        # Generate human-readable message
        message_parts = [f"You have {active_count} active task(s)"]
        if overdue_count > 0:
            message_parts.append(f"{overdue_count} overdue")
        if stale_count > 0:
            message_parts.append(f"{stale_count} stale")

        return SkillResponse(
            request_id=request.id,
            message=". ".join(message_parts) + ".",
            data={"summary": summary},
        )

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Check for tasks needing attention."""
        actions: list[HeartbeatAction] = []

        for user_id in user_ids:
            tasks = await self._get_user_tasks(user_id)

            # Check for approaching deadlines (within 24 hours)
            deadline_soon = []
            for task in tasks:
                if task.deadline and task.status not in (TaskStatus.DONE, TaskStatus.CANCELLED):
                    time_until = task.deadline - datetime.now()
                    if timedelta(0) < time_until < timedelta(hours=24):
                        deadline_soon.append(task)

            if deadline_soon:
                actions.append(
                    HeartbeatAction(
                        skill_name=self.name,
                        action_type="deadline_reminder",
                        user_id=user_id,
                        data={
                            "tasks": [t.to_dict() for t in deadline_soon],
                            "count": len(deadline_soon),
                        },
                        priority=8,  # High priority
                    )
                )

            # Check for overdue tasks
            overdue = [t for t in tasks if t.is_overdue()]
            if overdue:
                actions.append(
                    HeartbeatAction(
                        skill_name=self.name,
                        action_type="overdue_alert",
                        user_id=user_id,
                        data={
                            "tasks": [t.to_dict() for t in overdue],
                            "count": len(overdue),
                        },
                        priority=9,  # Very high priority
                    )
                )

            # Check for stale tasks (weekly)
            stale = [t for t in tasks if t.is_stale()]
            if stale:
                actions.append(
                    HeartbeatAction(
                        skill_name=self.name,
                        action_type="stale_task_check",
                        user_id=user_id,
                        data={
                            "tasks": [t.to_dict() for t in stale],
                            "count": len(stale),
                        },
                        priority=3,  # Lower priority
                    )
                )

        return actions

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        """Return context about user's tasks for the system prompt."""
        # Check cache for quick access
        if user_id not in self._tasks_cache:
            return None

        tasks = list(self._tasks_cache[user_id].values())
        if not tasks:
            return None

        active_tasks = [t for t in tasks if t.status not in (TaskStatus.DONE, TaskStatus.CANCELLED)]

        if not active_tasks:
            return None

        overdue = sum(1 for t in active_tasks if t.is_overdue())
        high_priority = sum(
            1 for t in active_tasks if t.priority in (TaskPriority.CRITICAL, TaskPriority.HIGH)
        )

        fragment = f"The user has {len(active_tasks)} active task(s)"
        if overdue > 0:
            fragment += f" ({overdue} overdue)"
        if high_priority > 0:
            fragment += f", {high_priority} high-priority"
        fragment += "."

        return fragment

    # Helper methods for task storage

    async def _store_task(self, task: Task) -> None:
        """Store a task in memory and cache."""
        # Update cache
        if task.user_id not in self._tasks_cache:
            self._tasks_cache[task.user_id] = {}
        self._tasks_cache[task.user_id][task.id] = task

        # Store in Qdrant if available
        if self._memory:
            # Create searchable text for embedding
            search_text = f"{task.title} {task.description}"
            if task.project:
                search_text += f" project:{task.project}"
            if task.tags:
                search_text += " " + " ".join(task.tags)

            await self._memory.store_with_payload(
                collection_name=TASKS_COLLECTION,
                text=search_text,
                payload=task.to_dict(),
                point_id=str(task.id),
            )

    async def _get_task(self, user_id: str, task_id: UUID) -> Task | None:
        """Get a specific task."""
        # Check cache first
        if user_id in self._tasks_cache and task_id in self._tasks_cache[user_id]:
            return self._tasks_cache[user_id][task_id]

        # Query Qdrant if available
        if self._memory:
            result = await self._memory.get_by_id(
                collection_name=TASKS_COLLECTION,
                point_id=str(task_id),
            )
            if result:
                task = Task.from_dict(result)
                # Update cache
                if user_id not in self._tasks_cache:
                    self._tasks_cache[user_id] = {}
                self._tasks_cache[user_id][task_id] = task
                return task

        return None

    async def _get_user_tasks(self, user_id: str) -> list[Task]:
        """Get all tasks for a user."""
        # If we have Qdrant, query it
        if self._memory:
            results = await self._memory.filter_by_field(
                collection_name=TASKS_COLLECTION,
                field="user_id",
                value=user_id,
            )
            tasks = [Task.from_dict(r) for r in results]

            # Update cache
            self._tasks_cache[user_id] = {t.id: t for t in tasks}
            return tasks

        # Return from cache
        if user_id in self._tasks_cache:
            return list(self._tasks_cache[user_id].values())

        return []

    async def _delete_task(self, user_id: str, task_id: UUID) -> None:
        """Delete a task."""
        # Remove from cache
        if user_id in self._tasks_cache and task_id in self._tasks_cache[user_id]:
            del self._tasks_cache[user_id][task_id]

        # Remove from Qdrant if available
        if self._memory:
            await self._memory.delete_by_id(
                collection_name=TASKS_COLLECTION,
                point_id=str(task_id),
            )

    async def cleanup(self) -> None:
        """Clean up resources."""
        self._tasks_cache.clear()
        log.info("task_manager_cleanup_complete")
