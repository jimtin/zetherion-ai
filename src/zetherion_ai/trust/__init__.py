"""Trust-domain primitives."""

from zetherion_ai.trust.scope import (
    DataScope,
    PromptFragment,
    PromptScopeError,
    ScopeDecision,
    ScopedPrincipal,
    ScopedResource,
    ScopeLabel,
    TrustDomain,
    assemble_prompt_fragments,
    evaluate_prompt_scope,
    prompt_fragment,
)

__all__ = [
    "DataScope",
    "PromptFragment",
    "PromptScopeError",
    "ScopeDecision",
    "ScopeLabel",
    "ScopedPrincipal",
    "ScopedResource",
    "TrustDomain",
    "assemble_prompt_fragments",
    "evaluate_prompt_scope",
    "prompt_fragment",
]
