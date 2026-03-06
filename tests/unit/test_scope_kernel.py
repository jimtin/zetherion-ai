"""Unit coverage for scope-aware prompt assembly."""

from __future__ import annotations

import pytest

from zetherion_ai.trust.scope import (
    DataScope,
    PromptScopeError,
    ScopedPrincipal,
    TrustDomain,
    assemble_prompt_fragments,
    evaluate_prompt_scope,
    prompt_fragment,
)


def test_control_plane_can_wrap_owner_personal_prompt() -> None:
    prompt = assemble_prompt_fragments(
        [
            prompt_fragment(
                "system instructions",
                scope=DataScope.CONTROL_PLANE,
                source="test.instructions",
            ),
            prompt_fragment(
                "user context",
                scope=DataScope.OWNER_PERSONAL,
                source="test.user_context",
            ),
        ],
        purpose="test.owner_prompt",
        principal=ScopedPrincipal(
            principal_id="owner-1",
            principal_type="owner_user",
            trust_domain=TrustDomain.OWNER_PERSONAL,
        ),
    )

    assert prompt == "system instructions\n\nuser context"


def test_owner_personal_cannot_mix_tenant_raw() -> None:
    decision = evaluate_prompt_scope(
        [
            prompt_fragment(
                "owner context",
                scope=DataScope.OWNER_PERSONAL,
                source="test.owner_context",
            ),
            prompt_fragment(
                "tenant payload",
                scope=DataScope.TENANT_RAW,
                source="test.tenant_payload",
            ),
        ],
        purpose="test.owner_tenant_mix",
    )

    assert not decision.allowed
    assert decision.reason_code == "owner_personal_cannot_mix_tenant_data"
    assert decision.security_event_type == "prompt_scope_violation"


def test_owner_portfolio_can_consume_tenant_derived_only() -> None:
    decision = evaluate_prompt_scope(
        [
            prompt_fragment(
                "portfolio instructions",
                scope=DataScope.CONTROL_PLANE,
                source="test.instructions",
            ),
            prompt_fragment(
                "derived tenant metrics",
                scope=DataScope.TENANT_DERIVED,
                source="test.tenant_derived",
            ),
            prompt_fragment(
                "portfolio summary",
                scope=DataScope.OWNER_PORTFOLIO,
                source="test.owner_portfolio",
            ),
        ],
        purpose="test.owner_portfolio_allowed",
    )

    assert decision.allowed
    assert decision.reason_code == "scopes_allowed"


def test_owner_portfolio_rejects_owner_personal_data() -> None:
    with pytest.raises(PromptScopeError) as excinfo:
        assemble_prompt_fragments(
            [
                prompt_fragment(
                    "portfolio summary",
                    scope=DataScope.OWNER_PORTFOLIO,
                    source="test.owner_portfolio",
                ),
                prompt_fragment(
                    "personal note",
                    scope=DataScope.OWNER_PERSONAL,
                    source="test.owner_personal",
                ),
            ],
            purpose="test.owner_portfolio_rejects_personal",
        )

    assert excinfo.value.decision.reason_code == "owner_portfolio_requires_tenant_derived_only"


def test_worker_artifact_rejects_non_worker_data() -> None:
    decision = evaluate_prompt_scope(
        [
            prompt_fragment(
                "worker instructions",
                scope=DataScope.CONTROL_PLANE,
                source="test.instructions",
            ),
            prompt_fragment(
                "artifact excerpt",
                scope=DataScope.WORKER_ARTIFACT,
                source="test.worker_artifact",
            ),
            prompt_fragment(
                "owner context",
                scope=DataScope.OWNER_PERSONAL,
                source="test.owner_personal",
            ),
        ],
        purpose="test.worker_artifact_rejects_personal",
    )

    assert not decision.allowed
    assert decision.reason_code == "worker_artifact_requires_scoped_job_artifacts"
