"""Request and response models for the updater sidecar."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any


@dataclass
class UpdateRequest:
    """Request to apply an update."""

    tag: str
    version: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> UpdateRequest:
        tag = data.get("tag", "")
        version = data.get("version", "")
        if not tag:
            msg = "Missing required field: tag"
            raise ValueError(msg)
        if not version:
            msg = "Missing required field: version"
            raise ValueError(msg)
        return cls(tag=tag, version=version)


@dataclass
class RollbackRequest:
    """Request to rollback to a previous SHA."""

    previous_sha: str

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RollbackRequest:
        sha = data.get("previous_sha", "")
        if not sha:
            msg = "Missing required field: previous_sha"
            raise ValueError(msg)
        return cls(previous_sha=sha)


@dataclass
class UpdateResult:
    """Result of an update or rollback operation."""

    status: str  # success, failed, rolled_back
    previous_sha: str | None = None
    new_sha: str | None = None
    active_color: str | None = None
    target_color: str | None = None
    paused: bool = False
    pause_reason: str | None = None
    steps_completed: list[str] = field(default_factory=list)
    error: str | None = None
    duration_seconds: float = 0.0
    started_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    completed_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "previous_sha": self.previous_sha,
            "new_sha": self.new_sha,
            "active_color": self.active_color,
            "target_color": self.target_color,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "steps_completed": self.steps_completed,
            "error": self.error,
            "duration_seconds": self.duration_seconds,
            "started_at": self.started_at,
            "completed_at": self.completed_at,
        }


@dataclass
class SidecarStatus:
    """Current status of the updater sidecar."""

    state: str  # idle, updating, rolling_back
    current_operation: str | None = None
    last_result: UpdateResult | None = None
    uptime_seconds: float = 0.0
    active_color: str | None = None
    paused: bool = False
    pause_reason: str | None = None
    last_checked_at: str | None = None
    last_attempted_tag: str | None = None
    last_good_tag: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "state": self.state,
            "current_operation": self.current_operation,
            "last_result": self.last_result.to_dict() if self.last_result else None,
            "uptime_seconds": self.uptime_seconds,
            "active_color": self.active_color,
            "paused": self.paused,
            "pause_reason": self.pause_reason,
            "last_checked_at": self.last_checked_at,
            "last_attempted_tag": self.last_attempted_tag,
            "last_good_tag": self.last_good_tag,
        }


@dataclass
class HistoryEntry:
    """A single entry in the update history."""

    tag: str
    version: str
    result: UpdateResult
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def to_dict(self) -> dict[str, Any]:
        return {
            "tag": self.tag,
            "version": self.version,
            "result": self.result.to_dict(),
            "timestamp": self.timestamp,
        }
