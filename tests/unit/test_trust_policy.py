"""Unit tests for tenant trust-policy evaluator."""

from __future__ import annotations

from typing import Any

from zetherion_ai.security.trust_policy import (
    TrustActionClass,
    TrustDecisionOutcome,
    TrustPolicyEvaluator,
    TrustTier,
)


def _resolver_factory(values: dict[tuple[str | None, str, str], Any]):
    def _resolver(tenant_id: str | None, namespace: str, key: str, default: Any) -> Any:
        return values.get((tenant_id, namespace, key), default)

    return _resolver


def test_messaging_read_requires_allowlisted_chat() -> None:
    evaluator = TrustPolicyEvaluator(
        setting_resolver=_resolver_factory(
            {
                ("tenant-1", "security", "trust_tier"): "tier3",
                ("tenant-1", "security", "messaging_allowlisted_chats"): ["chat-1", "chat-2"],
            }
        )
    )

    denied = evaluator.evaluate(
        tenant_id="tenant-1",
        action="messaging.read",
        context={"chat_id": "chat-unknown"},
    )
    assert denied.outcome == TrustDecisionOutcome.DENY
    assert denied.code == "AI_MESSAGING_CHAT_NOT_ALLOWLISTED"

    allowed = evaluator.evaluate(
        tenant_id="tenant-1",
        action="messaging.read",
        context={"chat_id": "chat-1"},
    )
    assert allowed.outcome == TrustDecisionOutcome.ALLOW


def test_messaging_send_requires_approval_unless_explicitly_elevated() -> None:
    evaluator = TrustPolicyEvaluator(
        setting_resolver=_resolver_factory(
            {
                ("tenant-1", "security", "trust_tier"): "tier3",
                ("tenant-1", "security", "messaging_allowlisted_chats"): ["chat-9"],
            }
        )
    )
    approval = evaluator.evaluate(
        tenant_id="tenant-1",
        action="messaging.send",
        context={"chat_id": "chat-9"},
    )
    assert approval.outcome == TrustDecisionOutcome.APPROVAL_REQUIRED
    assert approval.code == "AI_APPROVAL_REQUIRED"

    allowed = evaluator.evaluate(
        tenant_id="tenant-1",
        action="messaging.send",
        context={"chat_id": "chat-9", "explicitly_elevated": True},
    )
    assert allowed.outcome == TrustDecisionOutcome.ALLOW


def test_kill_switch_blocks_messaging_ingest() -> None:
    evaluator = TrustPolicyEvaluator(
        setting_resolver=_resolver_factory(
            {
                ("tenant-1", "security", "trust_tier"): "tier3",
                ("tenant-1", "security", "messaging_ingestion_kill_switch"): True,
            }
        )
    )
    decision = evaluator.evaluate(tenant_id="tenant-1", action="messaging.ingest", context={})
    assert decision.outcome == TrustDecisionOutcome.DENY
    assert decision.code == "AI_KILL_SWITCH_ACTIVE"


def test_automerge_requires_policy_enablement_and_guards() -> None:
    base = {
        ("tenant-1", "security", "trust_tier"): "tier4",
    }
    evaluator = TrustPolicyEvaluator(setting_resolver=_resolver_factory(base))
    denied_policy = evaluator.evaluate(
        tenant_id="tenant-1",
        action="automerge.execute",
        context={"branch_guard_passed": True, "risk_guard_passed": True},
    )
    assert denied_policy.outcome == TrustDecisionOutcome.DENY
    assert denied_policy.code == "AI_TRUST_POLICY_DENIED"

    enabled = dict(base)
    enabled[("tenant-1", "security", "auto_merge_policy_enabled")] = True
    evaluator_enabled = TrustPolicyEvaluator(setting_resolver=_resolver_factory(enabled))

    denied_guard = evaluator_enabled.evaluate(
        tenant_id="tenant-1",
        action="automerge.execute",
        context={"branch_guard_passed": True, "risk_guard_passed": False},
    )
    assert denied_guard.outcome == TrustDecisionOutcome.DENY
    assert denied_guard.code == "AI_TRUST_POLICY_GUARD_FAILED"

    allowed = evaluator_enabled.evaluate(
        tenant_id="tenant-1",
        action="automerge.execute",
        context={"branch_guard_passed": True, "risk_guard_passed": True},
    )
    assert allowed.outcome == TrustDecisionOutcome.ALLOW


def test_unknown_sensitive_actions_are_denied_by_default() -> None:
    evaluator = TrustPolicyEvaluator(setting_resolver=_resolver_factory({}))
    decision = evaluator.evaluate(
        tenant_id="tenant-1",
        action="messaging.super-secret",
        context={},
    )
    assert decision.outcome == TrustDecisionOutcome.DENY
    assert decision.code == "AI_TRUST_POLICY_DENIED"


def test_unknown_non_sensitive_actions_allow_with_method_classification() -> None:
    evaluator = TrustPolicyEvaluator(setting_resolver=_resolver_factory({}))

    read_decision = evaluator.evaluate(
        tenant_id="tenant-1",
        action="tenant_admin.unknown_read",
        context={"method": "GET"},
    )
    assert read_decision.outcome == TrustDecisionOutcome.ALLOW
    assert read_decision.action_class == TrustActionClass.READ

    mutate_decision = evaluator.evaluate(
        tenant_id="tenant-1",
        action="tenant_admin.unknown_mutate",
        context={"method": "POST"},
    )
    assert mutate_decision.outcome == TrustDecisionOutcome.ALLOW
    assert mutate_decision.action_class == TrustActionClass.MUTATE


def test_numeric_trust_tier_aliases_and_low_tier_denial() -> None:
    assert TrustTier.coerce("1", default=TrustTier.TIER3) == TrustTier.TIER1

    evaluator = TrustPolicyEvaluator(
        setting_resolver=_resolver_factory(
            {
                ("tenant-1", "security", "trust_tier"): "1",
                ("tenant-1", "security", "messaging_allowlisted_chats"): ["chat-1"],
            }
        )
    )
    decision = evaluator.evaluate(
        tenant_id="tenant-1",
        action="messaging.send",
        context={"chat_id": "chat-1"},
    )
    assert decision.outcome == TrustDecisionOutcome.DENY
    assert decision.code == "AI_TRUST_TIER_TOO_LOW"


def test_coercion_helpers_cover_string_and_numeric_paths() -> None:
    assert TrustPolicyEvaluator._as_bool(1) is True
    assert TrustPolicyEvaluator._as_bool("yes") is True
    assert TrustPolicyEvaluator._as_bool("off") is False

    assert TrustPolicyEvaluator._coerce_allowlist('["chat-a","chat-b"]') == {"chat-a", "chat-b"}
    assert TrustPolicyEvaluator._coerce_allowlist("chat-c, chat-d") == {"chat-c", "chat-d"}
    assert TrustPolicyEvaluator._coerce_allowlist("[not-json]") == {"[not-json]"}
    assert TrustPolicyEvaluator._coerce_allowlist("   ") == set()
    assert TrustPolicyEvaluator._coerce_allowlist(None) == set()
