"""Unit tests for routing policy helpers."""

from zetherion_ai.routing.models import RouteMode
from zetherion_ai.routing.policies import ConflictPolicyThresholds, conflict_mode


def test_conflict_mode_asks_at_high_severity() -> None:
    assert (
        conflict_mode(
            severity=0.9,
            high_priority=False,
            attendee_impacting=False,
        )
        == RouteMode.ASK
    )


def test_conflict_mode_asks_for_mid_severity_when_high_priority_or_attendee_impacting() -> None:
    assert (
        conflict_mode(
            severity=0.4,
            high_priority=True,
            attendee_impacting=False,
        )
        == RouteMode.ASK
    )
    assert (
        conflict_mode(
            severity=0.4,
            high_priority=False,
            attendee_impacting=True,
        )
        == RouteMode.ASK
    )


def test_conflict_mode_drafts_for_mid_severity_without_extra_impact() -> None:
    assert (
        conflict_mode(
            severity=0.4,
            high_priority=False,
            attendee_impacting=False,
        )
        == RouteMode.DRAFT
    )


def test_conflict_mode_auto_below_threshold_and_respects_overrides() -> None:
    assert (
        conflict_mode(
            severity=0.1,
            high_priority=True,
            attendee_impacting=True,
        )
        == RouteMode.AUTO
    )

    custom = ConflictPolicyThresholds(ask_always=0.8, ask_or_draft_floor=0.5)
    assert (
        conflict_mode(
            severity=0.6,
            high_priority=False,
            attendee_impacting=False,
            thresholds=custom,
        )
        == RouteMode.DRAFT
    )
