"""Legacy-to-canonical trust adapters used in shadow mode."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from zetherion_ai.trust.engine import (
    TrustDecision,
    TrustDecisionSignature,
    TrustMode,
    TrustOutcome,
    TrustPrincipal,
    TrustResource,
    TrustRiskClass,
)


def build_shadow_adapters() -> dict[str, Any]:
    """Build the default shadow adapter registry."""

    return {
        "trust_policy": adapt_trust_policy,
        "personal_action": adapt_personal_action,
        "gmail_trust": adapt_gmail_trust,
        "github_autonomy": adapt_github_autonomy,
        "youtube_trust": adapt_youtube_trust,
    }


def build_trust_policy_signature(legacy_decision: Any) -> TrustDecisionSignature:
    """Build a canonical signature from a trust-policy decision."""

    outcome = _legacy_value(getattr(legacy_decision, "outcome", "deny"))
    action_class = _legacy_value(getattr(legacy_decision, "action_class", "mutate"))
    return TrustDecisionSignature(
        outcome=_map_policy_outcome(outcome),
        mode=_map_policy_mode(outcome),
        risk_class=_map_policy_risk(action_class),
        requires_two_person=bool(getattr(legacy_decision, "requires_two_person", False)),
    )


def build_personal_action_signature(legacy_decision: Any) -> TrustDecisionSignature:
    """Build a canonical signature from a personal action decision."""

    mode = _normalize_text(getattr(legacy_decision, "mode", "ask"))
    should_execute = bool(getattr(legacy_decision, "should_execute", False))
    domain = _normalize_text(getattr(legacy_decision, "domain", "general"))
    action = _normalize_text(getattr(legacy_decision, "action", ""))
    return TrustDecisionSignature(
        outcome=_map_personal_outcome(mode=mode, should_execute=should_execute),
        mode=_map_personal_mode(mode=mode, should_execute=should_execute),
        risk_class=_map_personal_risk(domain=domain, action=action),
    )


def build_gmail_trust_signature(
    *,
    reply_type: Any,
    auto_send: bool,
) -> TrustDecisionSignature:
    """Build a canonical signature from Gmail trust evaluation inputs."""

    normalized_reply_type = _legacy_value(reply_type)
    return TrustDecisionSignature(
        outcome=TrustOutcome.ALLOW if auto_send else TrustOutcome.APPROVAL_REQUIRED,
        mode=TrustMode.AUTO if auto_send else TrustMode.DRAFT,
        risk_class=_map_gmail_risk(normalized_reply_type),
    )


def build_github_autonomy_signature(*, action_type: Any, level: Any) -> TrustDecisionSignature:
    """Build a canonical signature from GitHub autonomy inputs."""

    normalized_level = _legacy_value(level)
    normalized_action = _legacy_value(action_type)
    return TrustDecisionSignature(
        outcome=_map_github_outcome(normalized_level),
        mode=_map_github_mode(normalized_level),
        risk_class=_map_github_risk(normalized_action),
    )


def build_youtube_trust_signature(*, category: str, auto_approved: bool) -> TrustDecisionSignature:
    """Build a canonical signature from YouTube trust evaluation inputs."""

    normalized_category = _normalize_text(category)
    if normalized_category == "spam":
        return TrustDecisionSignature(
            outcome=TrustOutcome.DENY,
            mode=TrustMode.BLOCK,
            risk_class=TrustRiskClass.CRITICAL,
        )
    return TrustDecisionSignature(
        outcome=TrustOutcome.ALLOW if auto_approved else TrustOutcome.APPROVAL_REQUIRED,
        mode=TrustMode.AUTO if auto_approved else TrustMode.REVIEW,
        risk_class=_map_youtube_risk(normalized_category),
    )


def adapt_trust_policy(
    *,
    action: str,
    principal: TrustPrincipal | None,
    resource: TrustResource | None,
    context: Mapping[str, Any],
) -> TrustDecision:
    """Adapt TrustPolicyEvaluator output into the canonical model."""

    legacy_decision = context["legacy_decision"]
    outcome = _legacy_value(getattr(legacy_decision, "outcome", "deny"))
    action_class = _legacy_value(getattr(legacy_decision, "action_class", "mutate"))
    code = _normalize_text(getattr(legacy_decision, "code", "ai_unknown")) or "ai_unknown"
    return TrustDecision(
        adapter_name="trust_policy",
        action=action,
        outcome=_map_policy_outcome(outcome),
        mode=_map_policy_mode(outcome),
        risk_class=_map_policy_risk(action_class),
        reason_code=code,
        principal=principal,
        resource=resource,
        requires_two_person=bool(getattr(legacy_decision, "requires_two_person", False)),
        trace=(f"legacy_outcome={outcome}", f"action_class={action_class}"),
        metadata={
            "status": getattr(legacy_decision, "status", None),
            "message": getattr(legacy_decision, "message", None),
        },
    )


def adapt_personal_action(
    *,
    action: str,
    principal: TrustPrincipal | None,
    resource: TrustResource | None,
    context: Mapping[str, Any],
) -> TrustDecision:
    """Adapt personal action controller decisions into the canonical model."""

    legacy_decision = context["legacy_decision"]
    mode = _normalize_text(getattr(legacy_decision, "mode", "ask"))
    should_execute = bool(getattr(legacy_decision, "should_execute", False))
    domain = _normalize_text(getattr(legacy_decision, "domain", "general"))
    legacy_action = _normalize_text(getattr(legacy_decision, "action", action))
    return TrustDecision(
        adapter_name="personal_action",
        action=legacy_action,
        outcome=_map_personal_outcome(mode=mode, should_execute=should_execute),
        mode=_map_personal_mode(mode=mode, should_execute=should_execute),
        risk_class=_map_personal_risk(domain=domain, action=legacy_action),
        reason_code=_normalize_reason_code(getattr(legacy_decision, "reason", mode)),
        principal=principal,
        resource=resource,
        trace=(f"mode={mode}", f"should_execute={str(should_execute).lower()}"),
        metadata={
            "domain": domain,
            "trust_score": getattr(legacy_decision, "trust_score", None),
        },
    )


def adapt_gmail_trust(
    *,
    action: str,
    principal: TrustPrincipal | None,
    resource: TrustResource | None,
    context: Mapping[str, Any],
) -> TrustDecision:
    """Adapt Gmail trust decisions into the canonical model."""

    reply_type = _legacy_value(context.get("reply_type"))
    auto_send = bool(context.get("auto_send"))
    return TrustDecision(
        adapter_name="gmail_trust",
        action=action,
        outcome=TrustOutcome.ALLOW if auto_send else TrustOutcome.APPROVAL_REQUIRED,
        mode=TrustMode.AUTO if auto_send else TrustMode.DRAFT,
        risk_class=_map_gmail_risk(reply_type),
        reason_code="gmail_auto_send_allowed" if auto_send else "gmail_review_required",
        principal=principal,
        resource=resource,
        trace=(f"reply_type={reply_type}", f"auto_send={str(auto_send).lower()}"),
        metadata={
            "confidence": context.get("confidence"),
            "auto_threshold": context.get("auto_threshold"),
        },
    )


def adapt_github_autonomy(
    *,
    action: str,
    principal: TrustPrincipal | None,
    resource: TrustResource | None,
    context: Mapping[str, Any],
) -> TrustDecision:
    """Adapt GitHub autonomy decisions into the canonical model."""

    normalized_action = _legacy_value(context.get("action_type") or action)
    level = _legacy_value(context.get("level") or "ask")
    return TrustDecision(
        adapter_name="github_autonomy",
        action=normalized_action,
        outcome=_map_github_outcome(level),
        mode=_map_github_mode(level),
        risk_class=_map_github_risk(normalized_action),
        reason_code=f"github_autonomy_{level}",
        principal=principal,
        resource=resource,
        trace=(f"level={level}",),
    )


def adapt_youtube_trust(
    *,
    action: str,
    principal: TrustPrincipal | None,
    resource: TrustResource | None,
    context: Mapping[str, Any],
) -> TrustDecision:
    """Adapt YouTube trust decisions into the canonical model."""

    category = _normalize_text(context.get("category", action))
    auto_approved = bool(context.get("auto_approved"))
    if category == "spam":
        return TrustDecision(
            adapter_name="youtube_trust",
            action=action,
            outcome=TrustOutcome.DENY,
            mode=TrustMode.BLOCK,
            risk_class=TrustRiskClass.CRITICAL,
            reason_code="youtube_spam_blocked",
            principal=principal,
            resource=resource,
            trace=("category=spam",),
        )
    return TrustDecision(
        adapter_name="youtube_trust",
        action=action,
        outcome=TrustOutcome.ALLOW if auto_approved else TrustOutcome.APPROVAL_REQUIRED,
        mode=TrustMode.AUTO if auto_approved else TrustMode.REVIEW,
        risk_class=_map_youtube_risk(category),
        reason_code="youtube_auto_approved" if auto_approved else "youtube_review_required",
        principal=principal,
        resource=resource,
        trace=(f"category={category}", f"auto_approved={str(auto_approved).lower()}"),
        metadata={"level": context.get("level")},
    )


def _legacy_value(raw: Any) -> str:
    if raw is None:
        return ""
    value = getattr(raw, "value", raw)
    return _normalize_text(value)


def _normalize_text(raw: Any) -> str:
    return str(raw or "").strip().lower()


def _normalize_reason_code(raw: Any) -> str:
    text = _normalize_text(raw)
    if not text:
        return "unknown"
    pieces = [piece for piece in text.replace("-", " ").replace("/", " ").split() if piece]
    return "_".join(pieces) or "unknown"


def _map_policy_outcome(outcome: str) -> TrustOutcome:
    if outcome == "allow":
        return TrustOutcome.ALLOW
    if outcome == "approval_required":
        return TrustOutcome.APPROVAL_REQUIRED
    return TrustOutcome.DENY


def _map_policy_mode(outcome: str) -> TrustMode:
    if outcome == "allow":
        return TrustMode.AUTO
    if outcome == "approval_required":
        return TrustMode.REVIEW
    return TrustMode.BLOCK


def _map_policy_risk(action_class: str) -> TrustRiskClass:
    mapping = {
        "read": TrustRiskClass.LOW,
        "mutate": TrustRiskClass.MODERATE,
        "sensitive": TrustRiskClass.HIGH,
        "critical": TrustRiskClass.CRITICAL,
    }
    return mapping.get(action_class, TrustRiskClass.MODERATE)


def _map_personal_outcome(*, mode: str, should_execute: bool) -> TrustOutcome:
    if mode == "never":
        return TrustOutcome.DENY
    if should_execute:
        return TrustOutcome.ALLOW
    return TrustOutcome.APPROVAL_REQUIRED


def _map_personal_mode(*, mode: str, should_execute: bool) -> TrustMode:
    if mode == "never":
        return TrustMode.BLOCK
    if should_execute:
        return TrustMode.AUTO
    if mode == "draft":
        return TrustMode.DRAFT
    if mode == "ask":
        return TrustMode.ASK
    return TrustMode.REVIEW


def _map_personal_risk(*, domain: str, action: str) -> TrustRiskClass:
    combined = f"{domain}:{action}"
    if any(token in combined for token in ("delete", "send", "reply", "email", "payment")):
        return TrustRiskClass.HIGH
    if domain in {"calendar", "tasks", "general", "discord_observe"}:
        return TrustRiskClass.MODERATE
    return TrustRiskClass.MODERATE


def _map_gmail_risk(reply_type: str) -> TrustRiskClass:
    if reply_type == "sensitive":
        return TrustRiskClass.HIGH
    if reply_type in {"acknowledgment", "general", "follow_up", "clarification"}:
        return TrustRiskClass.MODERATE
    return TrustRiskClass.MODERATE


def _map_github_outcome(level: str) -> TrustOutcome:
    if level == "autonomous":
        return TrustOutcome.ALLOW
    return TrustOutcome.APPROVAL_REQUIRED


def _map_github_mode(level: str) -> TrustMode:
    if level == "autonomous":
        return TrustMode.AUTO
    if level == "always_ask":
        return TrustMode.REVIEW
    return TrustMode.ASK


def _map_github_risk(action: str) -> TrustRiskClass:
    if action in {
        "force_push",
        "delete_repo",
        "transfer_repo",
        "update_branch_protection",
    }:
        return TrustRiskClass.CRITICAL
    if action in {
        "create_issue",
        "update_issue",
        "close_issue",
        "reopen_issue",
        "create_pr",
        "merge_pr",
        "close_pr",
        "create_release",
        "delete_branch",
        "create_label",
        "delete_label",
    }:
        return TrustRiskClass.HIGH
    if action in {
        "add_label",
        "remove_label",
        "add_comment",
        "assign_issue",
        "unassign_issue",
        "request_review",
        "add_reaction",
    }:
        return TrustRiskClass.MODERATE
    return TrustRiskClass.LOW


def _map_youtube_risk(category: str) -> TrustRiskClass:
    if category == "complaint":
        return TrustRiskClass.HIGH
    if category in {"question", "feedback"}:
        return TrustRiskClass.MODERATE
    return TrustRiskClass.LOW
