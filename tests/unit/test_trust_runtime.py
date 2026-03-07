"""Unit tests for canonical trust runtime helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.personal.actions import ActionDecision
from zetherion_ai.personal.models import PersonalPolicy, PolicyDomain, PolicyMode
from zetherion_ai.routing.models import RouteDecision, RouteMode, RouteTag
from zetherion_ai.trust.engine import TrustMode, TrustOutcome, TrustRiskClass
from zetherion_ai.trust.runtime import (
    _personal_risk,
    _personal_score_level,
    _policy_mode_to_trust_mode,
    _routing_mode,
    _routing_outcome,
    _routing_reason_code,
    _routing_resource_scope,
    _routing_risk,
    build_personal_action_trust_decision,
    build_routing_trust_decision,
    personal_policy_as_decision,
    record_personal_action_decision,
    record_personal_action_feedback,
    record_routing_trust_decision,
    sync_personal_policy_to_trust,
)


def test_build_personal_action_trust_decision_maps_draft_review() -> None:
    decision = ActionDecision(
        domain="email",
        action="auto_reply_ack",
        mode="draft",
        trust_score=0.42,
        should_execute=False,
        reason="Draft for review",
    )

    canonical = build_personal_action_trust_decision(user_id=12345, decision=decision)

    assert canonical.outcome == TrustOutcome.APPROVAL_REQUIRED
    assert canonical.mode == TrustMode.DRAFT
    assert canonical.risk_class == TrustRiskClass.HIGH
    assert canonical.resource is not None
    assert canonical.resource.resource_id == "owner_personal:email:auto_reply_ack"


def test_build_routing_trust_decision_maps_blocked_email() -> None:
    decision = RouteDecision(
        mode=RouteMode.BLOCK,
        route_tag=RouteTag.IGNORE,
        reason="Blocked by email security policy",
        provider="google",
        metadata={"score": 0.92},
    )

    canonical = build_routing_trust_decision(
        user_id=12345,
        action="routing.email.process",
        source_type="email",
        decision=decision,
    )

    assert canonical.outcome == TrustOutcome.DENY
    assert canonical.mode == TrustMode.BLOCK
    assert canonical.risk_class == TrustRiskClass.CRITICAL
    assert canonical.reason_code == "routing_email_block_ignore"
    assert canonical.resource is not None
    assert canonical.resource.resource_id == "owner_personal:email:route"


@pytest.mark.asyncio
async def test_record_personal_action_decision_persists_audit() -> None:
    trust_storage = AsyncMock()
    decision = ActionDecision(
        domain="tasks",
        action="create_task",
        mode="auto",
        trust_score=0.93,
        should_execute=True,
        reason="Auto-execute (mode=auto)",
    )

    await record_personal_action_decision(trust_storage, user_id=12345, decision=decision)

    trust_storage.record_shadow_decision.assert_awaited_once()
    recorded_decision = trust_storage.record_shadow_decision.await_args.args[0]
    assert recorded_decision.action == "create_task"
    assert recorded_decision.mode == TrustMode.AUTO


@pytest.mark.asyncio
async def test_record_personal_action_decision_returns_none_without_storage() -> None:
    decision = ActionDecision(
        domain="general",
        action="nudge",
        mode="ask",
        trust_score=0.0,
        should_execute=False,
        reason="Waiting for approval",
    )

    assert await record_personal_action_decision(None, user_id=12345, decision=decision) is None


@pytest.mark.asyncio
async def test_record_personal_action_decision_handles_storage_failure() -> None:
    trust_storage = AsyncMock()
    trust_storage.record_shadow_decision.side_effect = RuntimeError("boom")
    decision = ActionDecision(
        domain="general",
        action="nudge",
        mode="ask",
        trust_score=0.0,
        should_execute=False,
        reason="Waiting for approval",
    )

    assert await record_personal_action_decision(
        trust_storage,
        user_id=12345,
        decision=decision,
    ) is None


@pytest.mark.asyncio
async def test_sync_personal_policy_to_trust_uses_owner_scope_and_stable_ids() -> None:
    trust_storage = AsyncMock()
    trust_storage.upsert_policy.return_value = SimpleNamespace(policy_id="policy-1")
    trust_storage.upsert_scorecard.return_value = SimpleNamespace(scorecard_id="scorecard-1")
    policy = PersonalPolicy(
        id=77,
        user_id=12345,
        domain=PolicyDomain.CALENDAR,
        action="schedule_meeting",
        mode=PolicyMode.ASK,
        trust_score=0.35,
    )

    await sync_personal_policy_to_trust(trust_storage, policy=policy)

    policy_input = trust_storage.upsert_policy.await_args.args[0]
    scorecard_input = trust_storage.upsert_scorecard.await_args.args[0]
    assert policy_input.resource_scope == "owner_personal:calendar:schedule_meeting"
    assert policy_input.source_record_id == "12345:calendar:schedule_meeting:policy"
    assert scorecard_input.resource_scope == "owner_personal:calendar:schedule_meeting"
    assert scorecard_input.source_record_id == (
        "owner:owner:12345:owner_personal:calendar:schedule_meeting:schedule_meeting"
    )


@pytest.mark.asyncio
async def test_sync_personal_policy_to_trust_returns_none_without_storage() -> None:
    policy = PersonalPolicy(
        user_id=12345,
        domain=PolicyDomain.TASKS,
        action="create_task",
        mode=PolicyMode.DRAFT,
        trust_score=0.5,
    )

    assert await sync_personal_policy_to_trust(None, policy=policy) is None


@pytest.mark.asyncio
async def test_sync_personal_policy_to_trust_handles_storage_failure() -> None:
    trust_storage = AsyncMock()
    trust_storage.upsert_policy.side_effect = RuntimeError("boom")
    policy = PersonalPolicy(
        user_id=12345,
        domain=PolicyDomain.TASKS,
        action="create_task",
        mode=PolicyMode.DRAFT,
        trust_score=0.5,
    )

    assert await sync_personal_policy_to_trust(trust_storage, policy=policy) is None


@pytest.mark.asyncio
async def test_record_personal_action_feedback_resyncs_scorecard(monkeypatch) -> None:
    trust_storage = AsyncMock()
    event = SimpleNamespace(event_id="event-1")
    intermediate_scorecard = SimpleNamespace(
        approvals=2,
        rejections=1,
        edits=1,
        total_interactions=4,
    )
    synced_scorecard = SimpleNamespace(scorecard_id="scorecard-2")
    trust_storage.record_feedback_outcome.return_value = (event, intermediate_scorecard)
    sync = AsyncMock(return_value=(SimpleNamespace(policy_id="policy-1"), synced_scorecard))
    monkeypatch.setattr("zetherion_ai.trust.runtime.sync_personal_policy_to_trust", sync)
    policy = PersonalPolicy(
        user_id=12345,
        domain=PolicyDomain.EMAIL,
        action="auto_reply_ack",
        mode=PolicyMode.DRAFT,
        trust_score=0.7,
    )

    result = await record_personal_action_feedback(
        trust_storage,
        policy=policy,
        outcome="approved",
        metadata={"origin": "test"},
    )

    assert result == (event, synced_scorecard)
    sync.assert_awaited_once_with(
        trust_storage,
        policy=policy,
        source_system="personal_action_controller",
        approvals=2,
        rejections=1,
        edits=1,
        total_interactions=4,
    )


@pytest.mark.asyncio
async def test_record_personal_action_feedback_returns_intermediate_scorecard_when_sync_skips(
    monkeypatch,
) -> None:
    trust_storage = AsyncMock()
    event = SimpleNamespace(event_id="event-1")
    intermediate_scorecard = SimpleNamespace(
        approvals=1,
        rejections=0,
        edits=0,
        total_interactions=1,
    )
    trust_storage.record_feedback_outcome.return_value = (event, intermediate_scorecard)
    sync = AsyncMock(return_value=None)
    monkeypatch.setattr("zetherion_ai.trust.runtime.sync_personal_policy_to_trust", sync)
    policy = PersonalPolicy(
        user_id=12345,
        domain=PolicyDomain.EMAIL,
        action="auto_reply_ack",
        mode=PolicyMode.ASK,
        trust_score=0.1,
    )

    result = await record_personal_action_feedback(
        trust_storage,
        policy=policy,
        outcome="minor_edit",
    )

    assert result == (event, intermediate_scorecard)


@pytest.mark.asyncio
async def test_record_personal_action_feedback_returns_none_without_storage() -> None:
    policy = PersonalPolicy(
        user_id=12345,
        domain=PolicyDomain.EMAIL,
        action="auto_reply_ack",
        mode=PolicyMode.ASK,
        trust_score=0.1,
    )

    assert await record_personal_action_feedback(None, policy=policy, outcome="approved") is None


@pytest.mark.asyncio
async def test_record_personal_action_feedback_handles_failure() -> None:
    trust_storage = AsyncMock()
    trust_storage.record_feedback_outcome.side_effect = RuntimeError("boom")
    policy = PersonalPolicy(
        user_id=12345,
        domain=PolicyDomain.EMAIL,
        action="auto_reply_ack",
        mode=PolicyMode.ASK,
        trust_score=0.1,
    )

    assert (
        await record_personal_action_feedback(
            trust_storage,
            policy=policy,
            outcome="approved",
        )
        is None
    )


@pytest.mark.asyncio
async def test_record_routing_trust_decision_returns_none_without_storage() -> None:
    decision = RouteDecision(
        mode=RouteMode.SKIP,
        route_tag=RouteTag.IGNORE,
        reason="Ignored",
        provider="google",
    )

    assert (
        await record_routing_trust_decision(
            None,
            user_id=12345,
            action="routing.email.process",
            source_type="email",
            decision=decision,
            source_system="email_router",
        )
        is None
    )


@pytest.mark.asyncio
async def test_record_routing_trust_decision_handles_storage_failure() -> None:
    trust_storage = AsyncMock()
    trust_storage.record_shadow_decision.side_effect = RuntimeError("boom")
    decision = RouteDecision(
        mode=RouteMode.REVIEW,
        route_tag=RouteTag.IGNORE,
        reason="Review",
        provider="google",
    )

    assert (
        await record_routing_trust_decision(
            trust_storage,
            user_id=12345,
            action="routing.email.process",
            source_type="email",
            decision=decision,
            source_system="email_router",
        )
        is None
    )


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (PolicyMode.AUTO, TrustMode.AUTO),
        (PolicyMode.DRAFT, TrustMode.DRAFT),
        (PolicyMode.NEVER, TrustMode.BLOCK),
        (PolicyMode.ASK, TrustMode.ASK),
    ],
)
def test_policy_mode_to_trust_mode_maps_all_values(mode: PolicyMode, expected: TrustMode) -> None:
    assert _policy_mode_to_trust_mode(mode) == expected


@pytest.mark.parametrize(
    ("score", "mode", "expected"),
    [
        (0.10, PolicyMode.NEVER, TrustMode.BLOCK.value),
        (0.90, PolicyMode.ASK, TrustMode.AUTO.value),
        (0.60, PolicyMode.ASK, TrustMode.DRAFT.value),
        (0.30, PolicyMode.ASK, TrustMode.ASK.value),
        (0.10, PolicyMode.ASK, TrustMode.REVIEW.value),
    ],
)
def test_personal_score_level_thresholds(score: float, mode: PolicyMode, expected: str) -> None:
    assert _personal_score_level(score, mode) == expected


def test_personal_risk_distinguishes_high_and_moderate_actions() -> None:
    assert _personal_risk(domain="email", action="send_reply") == TrustRiskClass.HIGH
    assert _personal_risk(domain="tasks", action="create_task") == TrustRiskClass.MODERATE


@pytest.mark.parametrize(
    ("policy", "should_execute"),
    [
        (
            PersonalPolicy(
                user_id=12345,
                domain=PolicyDomain.GENERAL,
                action="nudge",
                mode=PolicyMode.AUTO,
                trust_score=0.2,
            ),
            True,
        ),
        (
            PersonalPolicy(
                user_id=12345,
                domain=PolicyDomain.GENERAL,
                action="nudge",
                mode=PolicyMode.DRAFT,
                trust_score=0.9,
            ),
            True,
        ),
        (
            PersonalPolicy(
                user_id=12345,
                domain=PolicyDomain.GENERAL,
                action="nudge",
                mode=PolicyMode.DRAFT,
                trust_score=0.2,
            ),
            False,
        ),
    ],
)
def test_personal_policy_as_decision_sets_execution_flag(
    policy: PersonalPolicy,
    should_execute: bool,
) -> None:
    decision = personal_policy_as_decision(policy)
    assert decision.should_execute is should_execute
    assert decision.mode == policy.mode.value


@pytest.mark.parametrize(
    ("action", "expected_scope"),
    [
        ("routing.email.process", "owner_personal:email:route"),
        ("routing.tasks.route", "owner_personal:tasks:route"),
        ("routing.calendar.route", "owner_personal:calendar:route"),
    ],
)
def test_routing_resource_scope_maps_domains(action: str, expected_scope: str) -> None:
    assert _routing_resource_scope(action) == expected_scope


@pytest.mark.parametrize(
    ("route_mode", "expected_outcome", "expected_mode"),
    [
        (RouteMode.AUTO, TrustOutcome.ALLOW, TrustMode.AUTO),
        (RouteMode.DRAFT, TrustOutcome.APPROVAL_REQUIRED, TrustMode.DRAFT),
        (RouteMode.ASK, TrustOutcome.APPROVAL_REQUIRED, TrustMode.ASK),
        (RouteMode.REVIEW, TrustOutcome.APPROVAL_REQUIRED, TrustMode.REVIEW),
        (RouteMode.BLOCK, TrustOutcome.DENY, TrustMode.BLOCK),
        (RouteMode.SKIP, TrustOutcome.DENY, TrustMode.BLOCK),
    ],
)
def test_routing_mode_and_outcome_cover_all_route_modes(
    route_mode: RouteMode,
    expected_outcome: TrustOutcome,
    expected_mode: TrustMode,
) -> None:
    assert _routing_outcome(route_mode) == expected_outcome
    assert _routing_mode(route_mode) == expected_mode


@pytest.mark.parametrize(
    ("action", "decision", "expected_risk", "expected_reason"),
    [
        (
            "routing.email.process",
            RouteDecision(
                mode=RouteMode.ASK,
                route_tag=RouteTag.REPLY_CANDIDATE,
                reason="Ask",
                provider="google",
            ),
            TrustRiskClass.HIGH,
            "routing_email_ask_reply_candidate",
        ),
        (
            "routing.email.process",
            RouteDecision(
                mode=RouteMode.DRAFT,
                route_tag=RouteTag.REPLY_CANDIDATE,
                reason="Draft",
                provider="google",
            ),
            TrustRiskClass.HIGH,
            "routing_email_draft_reply_candidate",
        ),
        (
            "routing.tasks.route",
            RouteDecision(
                mode=RouteMode.DRAFT,
                route_tag=RouteTag.TASK_CANDIDATE,
                reason="Draft",
                provider="google",
            ),
            TrustRiskClass.MODERATE,
            "routing_tasks_draft_task_candidate",
        ),
        (
            "routing.calendar.route",
            RouteDecision(
                mode=RouteMode.AUTO,
                route_tag=RouteTag.CALENDAR_CANDIDATE,
                reason="Auto",
                provider="google",
            ),
            TrustRiskClass.MODERATE,
            "routing_calendar_auto_calendar_candidate",
        ),
        (
            "routing.email.process",
            RouteDecision(
                mode=RouteMode.SKIP,
                route_tag=RouteTag.IGNORE,
                reason="Skip",
                provider="google",
            ),
            TrustRiskClass.LOW,
            "routing_email_skip_ignore",
        ),
    ],
)
def test_routing_risk_and_reason_code_cover_branches(
    action: str,
    decision: RouteDecision,
    expected_risk: TrustRiskClass,
    expected_reason: str,
) -> None:
    assert _routing_risk(action=action, decision=decision) == expected_risk
    assert _routing_reason_code(action=action, decision=decision) == expected_reason
