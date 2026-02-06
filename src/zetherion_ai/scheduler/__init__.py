"""Heartbeat scheduler for proactive behavior.

This package provides:
- HeartbeatScheduler: Async scheduler for periodic skill checks
- ActionExecutor: Executes actions returned by skills
- ScheduledEvent: One-time scheduled actions
"""

from zetherion_ai.scheduler.actions import (
    ActionExecutor,
    ActionResult,
    ScheduledEvent,
    ScheduledEventStatus,
)
from zetherion_ai.scheduler.heartbeat import (
    HeartbeatConfig,
    HeartbeatScheduler,
)

__all__ = [
    # Heartbeat
    "HeartbeatScheduler",
    "HeartbeatConfig",
    # Actions
    "ActionExecutor",
    "ActionResult",
    "ScheduledEvent",
    "ScheduledEventStatus",
]
