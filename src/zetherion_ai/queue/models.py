"""Queue models — priority, status, task type enums and QueueItem dataclass.

Defines the data structures for the priority message queue system. Items
flow through states: QUEUED -> PROCESSING -> COMPLETED | FAILED -> DEAD.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum, IntEnum
from typing import Any
from uuid import UUID, uuid4


class QueuePriority(IntEnum):
    """Priority levels for queue items (lower = higher priority)."""

    INTERACTIVE = 0  # P0: DMs, mentions, slash commands
    NEAR_INTERACTIVE = 1  # P1: Triggered events
    SCHEDULED = 2  # P2: Heartbeat actions
    BULK = 3  # P3: Email sync, YouTube onboarding


class QueueStatus(str, Enum):
    """Lifecycle states for a queue item."""

    QUEUED = "queued"
    PROCESSING = "processing"
    COMPLETED = "completed"
    FAILED = "failed"
    DEAD = "dead"


class QueueTaskType(str, Enum):
    """Well-known task types processed by the queue."""

    DISCORD_MESSAGE = "discord_message"
    SKILL_REQUEST = "skill_request"
    HEARTBEAT_ACTION = "heartbeat_action"
    BULK_INGESTION = "bulk_ingestion"


@dataclass
class QueueItem:
    """A single item in the priority message queue.

    Attributes:
        id: Unique item identifier.
        priority: Processing priority (0 = highest).
        status: Current lifecycle state.
        task_type: Categorisation of the work to perform.
        user_id: Discord user ID that originated the request.
        channel_id: Discord channel ID (optional).
        payload: Arbitrary JSON-serialisable task data.
        attempt_count: Number of processing attempts so far.
        max_attempts: Maximum attempts before moving to DEAD.
        last_error: Error message from the most recent failure.
        worker_id: Identifier of the worker currently processing.
        created_at: Timestamp when the item was enqueued.
        scheduled_for: Earliest time the item may be dequeued.
        started_at: Timestamp when processing began.
        completed_at: Timestamp when processing finished.
        correlation_id: Optional ID to correlate related items.
        parent_id: Optional parent item UUID for chained tasks.
    """

    id: UUID = field(default_factory=uuid4)
    priority: int = QueuePriority.INTERACTIVE
    status: str = QueueStatus.QUEUED
    task_type: str = QueueTaskType.DISCORD_MESSAGE
    user_id: int = 0
    channel_id: int | None = None
    payload: dict[str, Any] = field(default_factory=dict)
    attempt_count: int = 0
    max_attempts: int = 3
    last_error: str | None = None
    worker_id: str | None = None
    created_at: datetime = field(default_factory=datetime.now)
    scheduled_for: datetime = field(default_factory=datetime.now)
    started_at: datetime | None = None
    completed_at: datetime | None = None
    correlation_id: str | None = None
    parent_id: UUID | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for serialisation."""
        return {
            "id": str(self.id),
            "priority": self.priority,
            "status": self.status,
            "task_type": self.task_type,
            "user_id": self.user_id,
            "channel_id": self.channel_id,
            "payload": self.payload,
            "attempt_count": self.attempt_count,
            "max_attempts": self.max_attempts,
            "last_error": self.last_error,
            "worker_id": self.worker_id,
            "created_at": self.created_at.isoformat(),
            "scheduled_for": self.scheduled_for.isoformat(),
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "completed_at": self.completed_at.isoformat() if self.completed_at else None,
            "correlation_id": self.correlation_id,
            "parent_id": str(self.parent_id) if self.parent_id else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> QueueItem:
        """Create a QueueItem from a dictionary."""
        return cls(
            id=UUID(data["id"]) if data.get("id") else uuid4(),
            priority=data.get("priority", QueuePriority.INTERACTIVE),
            status=data.get("status", QueueStatus.QUEUED),
            task_type=data.get("task_type", QueueTaskType.DISCORD_MESSAGE),
            user_id=data.get("user_id", 0),
            channel_id=data.get("channel_id"),
            payload=data.get("payload", {}),
            attempt_count=data.get("attempt_count", 0),
            max_attempts=data.get("max_attempts", 3),
            last_error=data.get("last_error"),
            worker_id=data.get("worker_id"),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now(),
            scheduled_for=datetime.fromisoformat(data["scheduled_for"])
            if data.get("scheduled_for")
            else datetime.now(),
            started_at=datetime.fromisoformat(data["started_at"])
            if data.get("started_at")
            else None,
            completed_at=datetime.fromisoformat(data["completed_at"])
            if data.get("completed_at")
            else None,
            correlation_id=data.get("correlation_id"),
            parent_id=UUID(data["parent_id"]) if data.get("parent_id") else None,
        )
