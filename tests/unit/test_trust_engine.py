"""Unit tests for the unified trust-engine shadow mode."""

from __future__ import annotations

from types import SimpleNamespace

from zetherion_ai.trust.adapters import build_shadow_adapters
from zetherion_ai.trust.engine import (
    TrustDecision,
    TrustDecisionSignature,
    TrustEngine,
    TrustMode,
    TrustOutcome,
    TrustPrincipal,
    TrustResource,
    TrustRiskClass,
)


def test_shadow_engine_logs_diff_for_mismatched_signature(monkeypatch) -> None:
    captured_info: list[dict[str, object]] = []
    captured_warning: list[dict[str, object]] = []

    def _adapter(**_: object) -> TrustDecision:
        return TrustDecision(
            adapter_name="test",
            action="demo.action",
            outcome=TrustOutcome.ALLOW,
            mode=TrustMode.AUTO,
            risk_class=TrustRiskClass.MODERATE,
            reason_code="ok",
        )

    engine = TrustEngine(adapters={"test": _adapter})

    from zetherion_ai.trust import engine as trust_engine_module

    monkeypatch.setattr(
        trust_engine_module.log,
        "info",
        lambda event, **kw: captured_info.append({"event": event, **kw}),
    )
    monkeypatch.setattr(
        trust_engine_module.log,
        "warning",
        lambda event, **kw: captured_warning.append({"event": event, **kw}),
    )

    result = engine.shadow_evaluate(
        adapter_name="test",
        action="demo.action",
        principal=None,
        resource=None,
        legacy_signature=TrustDecisionSignature(
            outcome=TrustOutcome.APPROVAL_REQUIRED,
            mode=TrustMode.REVIEW,
            risk_class=TrustRiskClass.HIGH,
        ),
    )

    assert result is not None
    assert result.matched is False
    assert result.diff == {
        "outcome": {"legacy": "approval_required", "shadow": "allow"},
        "mode": {"legacy": "review", "shadow": "auto"},
        "risk_class": {"legacy": "high", "shadow": "moderate"},
    }
    assert captured_info[0]["event"] == "trust_decision"
    assert captured_warning[0]["event"] == "trust_decision_diff"


def test_shadow_engine_returns_none_for_unknown_adapter(monkeypatch) -> None:
    captured_warning: list[dict[str, object]] = []
    engine = TrustEngine()

    from zetherion_ai.trust import engine as trust_engine_module

    monkeypatch.setattr(
        trust_engine_module.log,
        "warning",
        lambda event, **kw: captured_warning.append({"event": event, **kw}),
    )

    result = engine.shadow_evaluate(
        adapter_name="missing",
        action="demo.action",
        principal=None,
        resource=None,
    )

    assert result is None
    assert captured_warning[0]["event"] == "trust_decision_error"
    assert captured_warning[0]["error"] == "adapter_not_registered"


def test_shadow_adapters_cover_all_segment6_surfaces() -> None:
    adapters = build_shadow_adapters()
    assert set(adapters) == {
        "trust_policy",
        "personal_action",
        "gmail_trust",
        "github_autonomy",
        "youtube_trust",
    }


def test_trust_policy_adapter_maps_approval_required_to_review() -> None:
    adapter = build_shadow_adapters()["trust_policy"]
    decision = adapter(
        action="messaging.send",
        principal=TrustPrincipal(principal_id="tenant-1", principal_type="tenant"),
        resource=TrustResource(resource_id="messaging.send", resource_type="trust_action"),
        context={
            "legacy_decision": SimpleNamespace(
                outcome="approval_required",
                action_class="critical",
                code="AI_APPROVAL_REQUIRED",
                requires_two_person=True,
                status=409,
                message="approval required",
            )
        },
    )

    assert decision.outcome == TrustOutcome.APPROVAL_REQUIRED
    assert decision.mode == TrustMode.REVIEW
    assert decision.risk_class == TrustRiskClass.CRITICAL
    assert decision.requires_two_person is True


def test_personal_action_adapter_maps_draft_review() -> None:
    adapter = build_shadow_adapters()["personal_action"]
    decision = adapter(
        action="auto_reply_ack",
        principal=None,
        resource=None,
        context={
            "legacy_decision": SimpleNamespace(
                domain="email",
                action="auto_reply_ack",
                mode="draft",
                should_execute=False,
                trust_score=0.4,
                reason="Draft for review",
            )
        },
    )

    assert decision.outcome == TrustOutcome.APPROVAL_REQUIRED
    assert decision.mode == TrustMode.DRAFT
    assert decision.risk_class == TrustRiskClass.HIGH


def test_github_adapter_maps_dangerous_action_to_critical_review() -> None:
    adapter = build_shadow_adapters()["github_autonomy"]
    decision = adapter(
        action="delete_repo",
        principal=None,
        resource=None,
        context={"action_type": "delete_repo", "level": "always_ask"},
    )

    assert decision.outcome == TrustOutcome.APPROVAL_REQUIRED
    assert decision.mode == TrustMode.REVIEW
    assert decision.risk_class == TrustRiskClass.CRITICAL


def test_youtube_adapter_blocks_spam() -> None:
    adapter = build_shadow_adapters()["youtube_trust"]
    decision = adapter(
        action="youtube.reply.approve",
        principal=None,
        resource=None,
        context={"category": "spam", "auto_approved": False, "level": 0},
    )

    assert decision.outcome == TrustOutcome.DENY
    assert decision.mode == TrustMode.BLOCK
    assert decision.risk_class == TrustRiskClass.CRITICAL
