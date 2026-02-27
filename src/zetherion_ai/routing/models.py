"""Shared routing models for provider-agnostic work automation."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any


class IngestionSource(StrEnum):
    """Top-level source domains for routed content."""

    EMAIL = "email"
    TASK = "task"
    CALENDAR = "calendar"


class DestinationType(StrEnum):
    """Destination classes for provider adapters."""

    CALENDAR = "calendar"
    TASK_LIST = "task_list"
    MAILBOX = "mailbox"


class RouteTag(StrEnum):
    """Small-model triage tags."""

    TASK_CANDIDATE = "task_candidate"
    CALENDAR_CANDIDATE = "calendar_candidate"
    REPLY_CANDIDATE = "reply_candidate"
    DIGEST_ONLY = "digest_only"
    IGNORE = "ignore"


class RouteMode(StrEnum):
    """Execution mode for router decisions."""

    AUTO = "auto"
    ASK = "ask"
    DRAFT = "draft"
    REVIEW = "review"
    BLOCK = "block"
    SKIP = "skip"


@dataclass
class IngestionEnvelope:
    """Source-normalized envelope that enters routing."""

    source_type: IngestionSource
    provider: str
    account_ref: str
    payload: dict[str, Any]
    received_at: datetime = field(default_factory=datetime.now)


@dataclass
class DestinationRef:
    """Provider destination reference."""

    provider: str
    destination_id: str
    destination_type: DestinationType
    display_name: str = ""


@dataclass
class NormalizedTask:
    """Canonical task object emitted by extraction."""

    title: str
    description: str = ""
    due_at: datetime | None = None
    scheduled_start: datetime | None = None
    scheduled_end: datetime | None = None
    priority: str = "medium"
    tags: list[str] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedEvent:
    """Canonical calendar event object emitted by extraction."""

    title: str
    start: datetime
    end: datetime
    description: str = ""
    location: str = ""
    attendees: list[str] = field(default_factory=list)
    all_day: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class NormalizedEmail:
    """Canonical email representation for routing and audit."""

    external_id: str
    thread_id: str
    subject: str
    body_text: str
    from_email: str
    to_emails: list[str]
    received_at: datetime
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class ConflictCandidate:
    """A conflicting external event used in decisioning."""

    calendar_id: str
    event_id: str
    title: str
    start: datetime
    end: datetime


@dataclass
class ConflictDecision:
    """Conflict analysis output."""

    severity: float
    requires_confirmation: bool
    suggestion: str
    conflicts: list[ConflictCandidate] = field(default_factory=list)


@dataclass
class RouteDecision:
    """Final route decision emitted by routers."""

    mode: RouteMode
    route_tag: RouteTag
    reason: str
    provider: str
    target: DestinationRef | None = None
    conflict: ConflictDecision | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialize for storage and API responses."""
        return {
            "mode": self.mode.value,
            "route_tag": self.route_tag.value,
            "reason": self.reason,
            "provider": self.provider,
            "target": {
                "provider": self.target.provider,
                "destination_id": self.target.destination_id,
                "destination_type": self.target.destination_type.value,
                "display_name": self.target.display_name,
            }
            if self.target
            else None,
            "conflict": {
                "severity": self.conflict.severity,
                "requires_confirmation": self.conflict.requires_confirmation,
                "suggestion": self.conflict.suggestion,
                "conflicts": [
                    {
                        "calendar_id": c.calendar_id,
                        "event_id": c.event_id,
                        "title": c.title,
                        "start": c.start.isoformat(),
                        "end": c.end.isoformat(),
                    }
                    for c in self.conflict.conflicts
                ],
            }
            if self.conflict
            else None,
            "metadata": self.metadata,
        }
