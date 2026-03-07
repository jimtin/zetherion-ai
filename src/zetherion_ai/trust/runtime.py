"""Runtime helpers for canonical trust audits during staged migration."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.personal.models import PersonalPolicy, PolicyMode
from zetherion_ai.routing.models import RouteDecision, RouteMode, RouteTag
from zetherion_ai.trust.adapters import adapt_personal_action
from zetherion_ai.trust.engine import (
    TrustDecision,
    TrustMode,
    TrustOutcome,
    TrustPrincipal,
    TrustResource,
    TrustRiskClass,
)
from zetherion_ai.trust.storage import (
    TrustFeedbackEventRecord,
    TrustPolicyInput,
    TrustPolicyRecord,
    TrustScorecardInput,
    TrustScorecardRecord,
    TrustStorage,
)

if TYPE_CHECKING:
    from zetherion_ai.personal.actions import ActionDecision

log = get_logger("zetherion_ai.trust.runtime")

_PERSONAL_SOURCE_SYSTEM = "personal_action_controller"
_ROUTING_EMAIL_ACTION = "routing.email.process"
_ROUTING_TASK_ACTION = "routing.tasks.route"
_ROUTING_CALENDAR_ACTION = "routing.calendar.route"


def build_personal_action_trust_decision(
    *,
    user_id: int,
    decision: ActionDecision,
) -> TrustDecision:
    """Convert one personal action decision into a canonical trust decision."""

    principal = TrustPrincipal(
        principal_id=str(user_id),
        principal_type="owner",
        metadata={"domain": str(decision.domain)},
    )
    resource = TrustResource(
        resource_id=_personal_resource_scope(
            domain=str(decision.domain),
            action=str(decision.action),
        ),
        resource_type="personal_action",
        metadata={"domain": str(decision.domain)},
    )
    return adapt_personal_action(
        action=str(decision.action),
        principal=principal,
        resource=resource,
        context={"legacy_decision": decision},
    )


async def record_personal_action_decision(
    trust_storage: TrustStorage | None,
    *,
    user_id: int,
    decision: ActionDecision,
    source_system: str = _PERSONAL_SOURCE_SYSTEM,
) -> Any | None:
    """Persist one canonical audit for a personal action decision."""

    if trust_storage is None:
        return None
    try:
        canonical = build_personal_action_trust_decision(user_id=user_id, decision=decision)
        return await trust_storage.record_decision(canonical, source_system=source_system)
    except Exception as exc:
        log.warning(
            "personal_action_trust_audit_failed",
            user_id=user_id,
            domain=decision.domain,
            action=decision.action,
            error=str(exc),
        )
        return None


async def sync_personal_policy_to_trust(
    trust_storage: TrustStorage | None,
    *,
    policy: PersonalPolicy,
    source_system: str = _PERSONAL_SOURCE_SYSTEM,
    approvals: int = 0,
    rejections: int = 0,
    edits: int = 0,
    total_interactions: int = 0,
) -> tuple[TrustPolicyRecord, TrustScorecardRecord] | None:
    """Sync the current legacy personal policy into canonical trust storage."""

    if trust_storage is None:
        return None

    resource_scope = _personal_resource_scope(domain=policy.domain.value, action=policy.action)
    try:
        stored_policy = await trust_storage.upsert_policy(
            TrustPolicyInput(
                principal_id=str(policy.user_id),
                principal_type="owner",
                resource_scope=resource_scope,
                action=policy.action,
                mode=_policy_mode_to_trust_mode(policy.mode).value,
                risk_class=_personal_risk(domain=policy.domain.value, action=policy.action).value,
                source_system=source_system,
                source_record_id=(f"{policy.user_id}:{policy.domain.value}:{policy.action}:policy"),
                metadata={
                    "domain": policy.domain.value,
                    "conditions": policy.conditions or {},
                    "policy_mode": policy.mode.value,
                },
            )
        )
        stored_scorecard = await trust_storage.upsert_scorecard(
            TrustScorecardInput(
                subject_id=str(policy.user_id),
                subject_type="owner",
                resource_scope=resource_scope,
                action=policy.action,
                score=float(policy.trust_score),
                approvals=approvals,
                rejections=rejections,
                edits=edits,
                total_interactions=total_interactions,
                level=_personal_score_level(policy.trust_score, policy.mode),
                ceiling=0.95,
                source_system=source_system,
                source_record_id=_personal_scorecard_source_record_id(
                    user_id=policy.user_id,
                    resource_scope=resource_scope,
                    action=policy.action,
                ),
                metadata={
                    "domain": policy.domain.value,
                    "policy_mode": policy.mode.value,
                },
            )
        )
        return stored_policy, stored_scorecard
    except Exception as exc:
        log.warning(
            "personal_policy_trust_sync_failed",
            user_id=policy.user_id,
            domain=policy.domain.value,
            action=policy.action,
            error=str(exc),
        )
        return None


async def record_personal_action_feedback(
    trust_storage: TrustStorage | None,
    *,
    policy: PersonalPolicy,
    outcome: str,
    source_system: str = _PERSONAL_SOURCE_SYSTEM,
    metadata: dict[str, Any] | None = None,
) -> tuple[TrustFeedbackEventRecord, TrustScorecardRecord] | None:
    """Persist canonical trust feedback and resync the score to the legacy policy."""

    if trust_storage is None:
        return None

    resource_scope = _personal_resource_scope(domain=policy.domain.value, action=policy.action)
    try:
        event, scorecard = await trust_storage.record_feedback_outcome(
            subject_id=str(policy.user_id),
            subject_type="owner",
            resource_scope=resource_scope,
            action=policy.action,
            outcome=outcome,
            source_system=source_system,
            metadata={"domain": policy.domain.value, **(metadata or {})},
        )
        sync_result = await sync_personal_policy_to_trust(
            trust_storage,
            policy=policy,
            source_system=source_system,
            approvals=scorecard.approvals,
            rejections=scorecard.rejections,
            edits=scorecard.edits,
            total_interactions=scorecard.total_interactions,
        )
        if sync_result is None:
            return event, scorecard
        return event, sync_result[1]
    except Exception as exc:
        log.warning(
            "personal_action_trust_feedback_failed",
            user_id=policy.user_id,
            domain=policy.domain.value,
            action=policy.action,
            outcome=outcome,
            error=str(exc),
        )
        return None


def build_routing_trust_decision(
    *,
    user_id: int,
    action: str,
    source_type: str,
    decision: RouteDecision,
) -> TrustDecision:
    """Convert one route decision into a canonical trust decision."""

    resource_scope = _routing_resource_scope(action)
    return TrustDecision(
        adapter_name="routing",
        action=action,
        outcome=_routing_outcome(decision.mode),
        mode=_routing_mode(decision.mode),
        risk_class=_routing_risk(action=action, decision=decision),
        reason_code=_routing_reason_code(action=action, decision=decision),
        principal=TrustPrincipal(
            principal_id=str(user_id),
            principal_type="owner",
            metadata={"source_type": source_type, "provider": decision.provider},
        ),
        resource=TrustResource(
            resource_id=resource_scope,
            resource_type="routing_decision",
            metadata={"source_type": source_type},
        ),
        trace=(
            f"source_type={source_type}",
            f"route_tag={decision.route_tag.value}",
            f"mode={decision.mode.value}",
        ),
        metadata={
            "source_type": source_type,
            "provider": decision.provider,
            "decision": decision.to_dict(),
        },
    )


async def record_routing_trust_decision(
    trust_storage: TrustStorage | None,
    *,
    user_id: int,
    action: str,
    source_type: str,
    decision: RouteDecision,
    source_system: str,
) -> Any | None:
    """Persist one canonical routing audit without affecting runtime behavior."""

    if trust_storage is None:
        return None
    try:
        canonical = build_routing_trust_decision(
            user_id=user_id,
            action=action,
            source_type=source_type,
            decision=decision,
        )
        return await trust_storage.record_decision(canonical, source_system=source_system)
    except Exception as exc:
        log.warning(
            "routing_trust_audit_failed",
            user_id=user_id,
            action=action,
            source_type=source_type,
            provider=decision.provider,
            route_tag=decision.route_tag.value,
            mode=decision.mode.value,
            error=str(exc),
        )
        return None


def personal_policy_as_decision(policy: PersonalPolicy) -> Any:
    """Represent a legacy personal policy as an action decision-like object."""

    should_execute = policy.mode == PolicyMode.AUTO
    if policy.mode == PolicyMode.DRAFT and policy.trust_score >= 0.85:
        should_execute = True
    return SimpleNamespace(
        domain=policy.domain.value,
        action=policy.action,
        mode=policy.mode.value,
        trust_score=float(policy.trust_score),
        should_execute=should_execute,
        reason=f"Policy sync ({policy.mode.value})",
    )


def _personal_resource_scope(*, domain: str, action: str) -> str:
    return f"owner_personal:{domain}:{action}"


def _policy_mode_to_trust_mode(mode: PolicyMode) -> TrustMode:
    if mode == PolicyMode.AUTO:
        return TrustMode.AUTO
    if mode == PolicyMode.DRAFT:
        return TrustMode.DRAFT
    if mode == PolicyMode.NEVER:
        return TrustMode.BLOCK
    return TrustMode.ASK


def _personal_score_level(score: float, mode: PolicyMode) -> str:
    if mode == PolicyMode.NEVER:
        return TrustMode.BLOCK.value
    if score >= 0.85:
        return TrustMode.AUTO.value
    if score >= 0.60:
        return TrustMode.DRAFT.value
    if score >= 0.25:
        return TrustMode.ASK.value
    return TrustMode.REVIEW.value


def _personal_risk(*, domain: str, action: str) -> TrustRiskClass:
    normalized = f"{domain}:{action}".lower()
    if any(token in normalized for token in ("send", "delete", "reply", "email", "payment")):
        return TrustRiskClass.HIGH
    return TrustRiskClass.MODERATE


def _personal_scorecard_source_record_id(
    *,
    user_id: int,
    resource_scope: str,
    action: str,
) -> str:
    return f"owner:owner:{user_id}:{resource_scope}:{action}"


def _routing_resource_scope(action: str) -> str:
    if action == _ROUTING_TASK_ACTION:
        return "owner_personal:tasks:route"
    if action == _ROUTING_CALENDAR_ACTION:
        return "owner_personal:calendar:route"
    return "owner_personal:email:route"


def _routing_outcome(mode: RouteMode) -> TrustOutcome:
    if mode == RouteMode.AUTO:
        return TrustOutcome.ALLOW
    if mode in {RouteMode.DRAFT, RouteMode.ASK, RouteMode.REVIEW}:
        return TrustOutcome.APPROVAL_REQUIRED
    return TrustOutcome.DENY


def _routing_mode(mode: RouteMode) -> TrustMode:
    if mode == RouteMode.AUTO:
        return TrustMode.AUTO
    if mode == RouteMode.DRAFT:
        return TrustMode.DRAFT
    if mode == RouteMode.ASK:
        return TrustMode.ASK
    if mode == RouteMode.REVIEW:
        return TrustMode.REVIEW
    return TrustMode.BLOCK


def _routing_risk(*, action: str, decision: RouteDecision) -> TrustRiskClass:
    if decision.mode == RouteMode.BLOCK:
        return TrustRiskClass.CRITICAL
    if decision.mode == RouteMode.REVIEW:
        return TrustRiskClass.HIGH
    if decision.mode == RouteMode.ASK:
        return TrustRiskClass.HIGH if action == _ROUTING_EMAIL_ACTION else TrustRiskClass.MODERATE
    if decision.mode == RouteMode.DRAFT:
        if action == _ROUTING_EMAIL_ACTION and decision.route_tag == RouteTag.REPLY_CANDIDATE:
            return TrustRiskClass.HIGH
        return TrustRiskClass.MODERATE
    if decision.mode == RouteMode.AUTO:
        return TrustRiskClass.MODERATE
    return TrustRiskClass.LOW


def _routing_reason_code(*, action: str, decision: RouteDecision) -> str:
    domain = "email"
    if action == _ROUTING_TASK_ACTION:
        domain = "tasks"
    elif action == _ROUTING_CALENDAR_ACTION:
        domain = "calendar"
    return f"routing_{domain}_{decision.mode.value}_{decision.route_tag.value}"


__all__ = [
    "build_personal_action_trust_decision",
    "build_routing_trust_decision",
    "personal_policy_as_decision",
    "record_personal_action_decision",
    "record_personal_action_feedback",
    "record_routing_trust_decision",
    "sync_personal_policy_to_trust",
]
