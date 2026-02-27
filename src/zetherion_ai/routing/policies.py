"""Policy helpers for work-router rollout and conflict decisioning."""

from __future__ import annotations

from dataclasses import dataclass

from zetherion_ai.routing.models import RouteMode


@dataclass(frozen=True)
class ConflictPolicyThresholds:
    """Default conflict severity thresholds for calendar routing."""

    ask_always: float = 0.60
    ask_or_draft_floor: float = 0.25


def conflict_mode(
    *,
    severity: float,
    high_priority: bool,
    attendee_impacting: bool,
    thresholds: ConflictPolicyThresholds | None = None,
) -> RouteMode:
    """Return route mode based on severity and conflict context."""
    active = thresholds or ConflictPolicyThresholds()

    if severity >= active.ask_always:
        return RouteMode.ASK
    if severity >= active.ask_or_draft_floor:
        if high_priority or attendee_impacting:
            return RouteMode.ASK
        return RouteMode.DRAFT
    return RouteMode.AUTO
