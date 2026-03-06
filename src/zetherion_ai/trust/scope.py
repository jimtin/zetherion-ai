"""Scope-aware prompt assembly primitives for trust-domain isolation."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.trust.scope")


class TrustDomain(StrEnum):
    """Canonical trust domains for storage, prompts, and delegation."""

    OWNER_PERSONAL = "owner_personal"
    OWNER_PORTFOLIO = "owner_portfolio"
    TENANT_RAW = "tenant_raw"
    TENANT_DERIVED = "tenant_derived"
    CONTROL_PLANE = "control_plane"
    WORKER_ARTIFACT = "worker_artifact"


class DataScope(StrEnum):
    """Prompt/data scope labels used for fail-closed composition checks."""

    OWNER_PERSONAL = "owner_personal"
    OWNER_PORTFOLIO = "owner_portfolio"
    TENANT_RAW = "tenant_raw"
    TENANT_DERIVED = "tenant_derived"
    CONTROL_PLANE = "control_plane"
    WORKER_ARTIFACT = "worker_artifact"

    @classmethod
    def from_domain(cls, domain: TrustDomain) -> DataScope:
        return cls(domain.value)


@dataclass(frozen=True)
class ScopeLabel:
    """Declarative scope metadata for one prompt fragment."""

    scope: DataScope
    source: str
    detail: str | None = None


@dataclass(frozen=True)
class ScopedPrincipal:
    """Identity context for prompt or action evaluation."""

    principal_id: str
    principal_type: str
    trust_domain: TrustDomain
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScopedResource:
    """Resource context for prompt or action evaluation."""

    resource_id: str
    resource_type: str
    trust_domain: TrustDomain
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ScopeDecision:
    """Allow or deny decision for a scope composition attempt."""

    outcome: str
    reason_code: str
    trace: tuple[str, ...] = ()
    scopes: tuple[DataScope, ...] = ()
    security_event_type: str | None = None

    @property
    def allowed(self) -> bool:
        return self.outcome == "allow"


@dataclass(frozen=True)
class PromptFragment:
    """Text plus scope metadata used for prompt assembly."""

    text: str
    label: ScopeLabel


class PromptScopeError(ValueError):
    """Raised when prompt fragments cross forbidden trust-domain boundaries."""

    def __init__(self, decision: ScopeDecision) -> None:
        super().__init__(f"prompt scope violation: {decision.reason_code}")
        self.decision = decision


def prompt_fragment(
    text: str,
    *,
    scope: DataScope,
    source: str,
    detail: str | None = None,
) -> PromptFragment:
    """Construct a prompt fragment with explicit scope metadata."""

    return PromptFragment(text=text, label=ScopeLabel(scope=scope, source=source, detail=detail))


def evaluate_prompt_scope(
    fragments: list[PromptFragment] | tuple[PromptFragment, ...],
    *,
    purpose: str,
    principal: ScopedPrincipal | None = None,
    resource: ScopedResource | None = None,
) -> ScopeDecision:
    """Evaluate whether the supplied prompt fragments may be combined."""

    active_fragments = [fragment for fragment in fragments if fragment.text.strip()]
    active_scopes = sorted(
        {fragment.label.scope for fragment in active_fragments},
        key=lambda item: item.value,
    )
    trace = [f"purpose={purpose}", f"scopes={','.join(scope.value for scope in active_scopes)}"]
    if principal is not None:
        trace.append(f"principal={principal.principal_type}:{principal.principal_id}")
    if resource is not None:
        trace.append(f"resource={resource.resource_type}:{resource.resource_id}")

    if not active_scopes:
        return ScopeDecision(outcome="allow", reason_code="no_fragments", trace=tuple(trace))

    data_scopes = {scope for scope in active_scopes if scope != DataScope.CONTROL_PLANE}

    if DataScope.OWNER_PERSONAL in data_scopes and (
        DataScope.TENANT_RAW in data_scopes or DataScope.TENANT_DERIVED in data_scopes
    ):
        return ScopeDecision(
            outcome="deny",
            reason_code="owner_personal_cannot_mix_tenant_data",
            trace=tuple(trace),
            scopes=tuple(active_scopes),
            security_event_type="prompt_scope_violation",
        )

    if DataScope.OWNER_PORTFOLIO in data_scopes:
        disallowed_scopes = {
            scope
            for scope in data_scopes
            if scope not in {DataScope.OWNER_PORTFOLIO, DataScope.TENANT_DERIVED}
        }
        if disallowed_scopes:
            return ScopeDecision(
                outcome="deny",
                reason_code="owner_portfolio_requires_tenant_derived_only",
                trace=tuple(trace),
                scopes=tuple(active_scopes),
                security_event_type="prompt_scope_violation",
            )

    if DataScope.WORKER_ARTIFACT in data_scopes:
        disallowed_scopes = {scope for scope in data_scopes if scope != DataScope.WORKER_ARTIFACT}
        if disallowed_scopes:
            return ScopeDecision(
                outcome="deny",
                reason_code="worker_artifact_requires_scoped_job_artifacts",
                trace=tuple(trace),
                scopes=tuple(active_scopes),
                security_event_type="prompt_scope_violation",
            )

    return ScopeDecision(
        outcome="allow",
        reason_code="scopes_allowed",
        trace=tuple(trace),
        scopes=tuple(active_scopes),
    )


def assemble_prompt_fragments(
    fragments: list[PromptFragment] | tuple[PromptFragment, ...],
    *,
    purpose: str,
    principal: ScopedPrincipal | None = None,
    resource: ScopedResource | None = None,
) -> str:
    """Join scope-labeled prompt fragments after fail-closed validation."""

    decision = evaluate_prompt_scope(
        fragments,
        purpose=purpose,
        principal=principal,
        resource=resource,
    )
    if not decision.allowed:
        active_fragments = [fragment for fragment in fragments if fragment.text.strip()]
        log.warning(
            "prompt_scope_violation",
            reason_code=decision.reason_code,
            purpose=purpose,
            scopes=[scope.value for scope in decision.scopes],
            fragment_sources=[fragment.label.source for fragment in active_fragments],
            principal_id=principal.principal_id if principal else None,
            principal_type=principal.principal_type if principal else None,
            resource_id=resource.resource_id if resource else None,
            resource_type=resource.resource_type if resource else None,
            trace=list(decision.trace),
        )
        raise PromptScopeError(decision)
    return "\n\n".join(fragment.text for fragment in fragments if fragment.text.strip())
