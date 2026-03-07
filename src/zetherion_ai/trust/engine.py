"""Canonical trust-engine evaluation primitives."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from threading import Lock
from typing import Any

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.trust.engine")


class TrustOutcome(StrEnum):
    """Canonical outcome returned by the trust engine."""

    ALLOW = "allow"
    APPROVAL_REQUIRED = "approval_required"
    DENY = "deny"


class TrustMode(StrEnum):
    """Canonical execution mode returned by the trust engine."""

    AUTO = "auto"
    DRAFT = "draft"
    ASK = "ask"
    REVIEW = "review"
    BLOCK = "block"


class TrustRiskClass(StrEnum):
    """Canonical action risk classification."""

    LOW = "low"
    MODERATE = "moderate"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True)
class TrustPrincipal:
    """Normalized actor identity for trust decisions."""

    principal_id: str
    principal_type: str
    tenant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrustResource:
    """Normalized resource identity for trust decisions."""

    resource_id: str
    resource_type: str
    tenant_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrustDecision:
    """Canonical trust-engine decision."""

    adapter_name: str
    action: str
    outcome: TrustOutcome
    mode: TrustMode
    risk_class: TrustRiskClass
    reason_code: str
    principal: TrustPrincipal | None = None
    resource: TrustResource | None = None
    requires_two_person: bool = False
    trace: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def allowed(self) -> bool:
        return self.outcome == TrustOutcome.ALLOW


@dataclass(frozen=True)
class TrustDecisionSignature:
    """Minimal decision signature for parity/diff checks."""

    outcome: TrustOutcome
    mode: TrustMode
    risk_class: TrustRiskClass
    requires_two_person: bool = False


@dataclass(frozen=True)
class TrustShadowResult:
    """Result of one canonical decision evaluation."""

    adapter_name: str
    action: str
    decision: TrustDecision
    matched: bool
    diff: dict[str, dict[str, Any]] = field(default_factory=dict)


TrustAdapter = Callable[..., TrustDecision]


class TrustEngine:
    """Registry-backed trust engine used for canonical decision evaluation."""

    def __init__(self, *, adapters: Mapping[str, TrustAdapter] | None = None) -> None:
        self._adapters: dict[str, TrustAdapter] = dict(adapters or {})

    def register_adapter(self, name: str, adapter: TrustAdapter) -> None:
        """Register or replace one adapter."""

        self._adapters[name] = adapter

    def evaluate(
        self,
        *,
        adapter_name: str,
        action: str,
        principal: TrustPrincipal | None,
        resource: TrustResource | None,
        context: Mapping[str, Any] | None = None,
        legacy_signature: TrustDecisionSignature | None = None,
    ) -> TrustShadowResult | None:
        """Run one non-throwing canonical decision evaluation."""

        adapter = self._adapters.get(adapter_name)
        if adapter is None:
            log.warning(
                "trust_decision_error",
                adapter_name=adapter_name,
                action=action,
                error="adapter_not_registered",
            )
            return None

        try:
            decision = adapter(
                action=action,
                principal=principal,
                resource=resource,
                context=context or {},
            )
            diff = self._build_diff(decision=decision, legacy_signature=legacy_signature)
            matched = not diff
            result = TrustShadowResult(
                adapter_name=adapter_name,
                action=action,
                decision=decision,
                matched=matched,
                diff=diff,
            )
            log.info(
                "trust_decision",
                adapter_name=adapter_name,
                action=action,
                outcome=decision.outcome.value,
                mode=decision.mode.value,
                risk_class=decision.risk_class.value,
                reason_code=decision.reason_code,
                requires_two_person=decision.requires_two_person,
                principal_id=decision.principal.principal_id if decision.principal else None,
                principal_type=decision.principal.principal_type if decision.principal else None,
                resource_id=decision.resource.resource_id if decision.resource else None,
                resource_type=decision.resource.resource_type if decision.resource else None,
                trace=list(decision.trace),
                metadata=decision.metadata,
                matched=matched,
            )
            if diff:
                log.warning(
                    "trust_decision_diff",
                    adapter_name=adapter_name,
                    action=action,
                    diff=diff,
                    outcome=decision.outcome.value,
                    mode=decision.mode.value,
                    risk_class=decision.risk_class.value,
                )
            return result
        except Exception as exc:  # pragma: no cover - guarded by caller contract tests
            log.warning(
                "trust_decision_error",
                adapter_name=adapter_name,
                action=action,
                error=str(exc),
            )
            return None

    def shadow_evaluate(
        self,
        *,
        adapter_name: str,
        action: str,
        principal: TrustPrincipal | None,
        resource: TrustResource | None,
        context: Mapping[str, Any] | None = None,
        legacy_signature: TrustDecisionSignature | None = None,
    ) -> TrustShadowResult | None:
        """Compatibility wrapper for legacy shadow-mode callers."""

        return self.evaluate(
            adapter_name=adapter_name,
            action=action,
            principal=principal,
            resource=resource,
            context=context,
            legacy_signature=legacy_signature,
        )

    @staticmethod
    def _build_diff(
        *,
        decision: TrustDecision,
        legacy_signature: TrustDecisionSignature | None,
    ) -> dict[str, dict[str, Any]]:
        if legacy_signature is None:
            return {}

        diff: dict[str, dict[str, Any]] = {}
        current_signature = {
            "outcome": decision.outcome.value,
            "mode": decision.mode.value,
            "risk_class": decision.risk_class.value,
            "requires_two_person": decision.requires_two_person,
        }
        previous_signature = {
            "outcome": legacy_signature.outcome.value,
            "mode": legacy_signature.mode.value,
            "risk_class": legacy_signature.risk_class.value,
            "requires_two_person": legacy_signature.requires_two_person,
        }
        for field_name, current_value in current_signature.items():
            previous_value = previous_signature[field_name]
            if current_value != previous_value:
                diff[field_name] = {
                    "legacy": previous_value,
                    "shadow": current_value,
                }
        return diff


_TRUST_ENGINE: TrustEngine | None = None
_TRUST_ENGINE_LOCK = Lock()


def get_trust_engine() -> TrustEngine:
    """Return the process-wide canonical trust engine singleton."""

    global _TRUST_ENGINE
    if _TRUST_ENGINE is None:
        with _TRUST_ENGINE_LOCK:
            if _TRUST_ENGINE is None:
                from zetherion_ai.trust.adapters import build_shadow_adapters

                _TRUST_ENGINE = TrustEngine(adapters=build_shadow_adapters())
    return _TRUST_ENGINE


def set_trust_engine(engine: TrustEngine | None) -> None:
    """Override the process-wide trust engine, primarily for tests."""

    global _TRUST_ENGINE
    with _TRUST_ENGINE_LOCK:
        _TRUST_ENGINE = engine


def record_decision(
    *,
    adapter_name: str,
    action: str,
    principal: TrustPrincipal | None,
    resource: TrustResource | None,
    context: Mapping[str, Any] | None = None,
    legacy_signature: TrustDecisionSignature | None = None,
) -> TrustShadowResult | None:
    """Convenience wrapper for non-throwing canonical decision recording."""

    return get_trust_engine().evaluate(
        adapter_name=adapter_name,
        action=action,
        principal=principal,
        resource=resource,
        context=context,
        legacy_signature=legacy_signature,
    )


def get_shadow_trust_engine() -> TrustEngine:
    """Compatibility wrapper for legacy shadow-mode imports."""

    return get_trust_engine()


def set_shadow_trust_engine(engine: TrustEngine | None) -> None:
    """Compatibility wrapper for legacy shadow-mode imports."""

    set_trust_engine(engine)


def record_shadow_decision(
    *,
    adapter_name: str,
    action: str,
    principal: TrustPrincipal | None,
    resource: TrustResource | None,
    context: Mapping[str, Any] | None = None,
    legacy_signature: TrustDecisionSignature | None = None,
) -> TrustShadowResult | None:
    """Compatibility wrapper for legacy shadow-mode imports."""

    return record_decision(
        adapter_name=adapter_name,
        action=action,
        principal=principal,
        resource=resource,
        context=context,
        legacy_signature=legacy_signature,
    )
