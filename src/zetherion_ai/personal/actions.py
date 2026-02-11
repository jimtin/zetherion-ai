"""Action control framework for the personal understanding system.

Trust-gated execution — the bot starts cautious and learns what the user
wants automated. Each domain/action pair has a policy mode and trust score.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from zetherion_ai.logging import get_logger
from zetherion_ai.personal.models import PersonalPolicy, PolicyDomain, PolicyMode
from zetherion_ai.personal.storage import PersonalStorage

log = get_logger("zetherion_ai.personal.actions")

# ---------------------------------------------------------------------------
# Trust evolution constants
# ---------------------------------------------------------------------------

TRUST_APPROVAL_DELTA = 0.05
TRUST_MINOR_EDIT_DELTA = -0.02
TRUST_MAJOR_EDIT_DELTA = -0.10
TRUST_REJECTION_DELTA = -0.20
TRUST_CAP = 0.95
TRUST_FLOOR = 0.0

# Threshold above which the bot can auto-execute (if mode allows)
AUTO_TRUST_THRESHOLD = 0.85


class ActionOutcome(StrEnum):
    """Possible outcomes when an action is reviewed by the user."""

    APPROVED = "approved"
    MINOR_EDIT = "minor_edit"
    MAJOR_EDIT = "major_edit"
    REJECTED = "rejected"


OUTCOME_DELTAS: dict[ActionOutcome, float] = {
    ActionOutcome.APPROVED: TRUST_APPROVAL_DELTA,
    ActionOutcome.MINOR_EDIT: TRUST_MINOR_EDIT_DELTA,
    ActionOutcome.MAJOR_EDIT: TRUST_MAJOR_EDIT_DELTA,
    ActionOutcome.REJECTED: TRUST_REJECTION_DELTA,
}


@dataclass
class ActionDecision:
    """The bot's decision about how to handle an action."""

    domain: str
    action: str
    mode: str  # 'auto', 'draft', 'ask', 'never'
    trust_score: float
    should_execute: bool
    reason: str


class ActionController:
    """Controls what the bot can do autonomously.

    Checks policies and trust scores to determine whether an action
    should be auto-executed, drafted, asked about, or blocked.
    """

    def __init__(self, storage: PersonalStorage) -> None:
        self._storage = storage

    async def decide(self, user_id: int, domain: str, action: str) -> ActionDecision:
        """Determine how to handle an action based on policy and trust.

        Args:
            user_id: The user this action is for.
            domain: Action domain (e.g., 'email', 'tasks').
            action: Specific action (e.g., 'auto_reply_ack').

        Returns:
            ActionDecision with the mode and whether to execute.
        """
        policy = await self._storage.get_policy(user_id, domain, action)

        if policy is None:
            # No policy → default to 'ask'
            return ActionDecision(
                domain=domain,
                action=action,
                mode=PolicyMode.ASK.value,
                trust_score=0.0,
                should_execute=False,
                reason="No policy configured, defaulting to ask",
            )

        mode = policy.mode.value
        trust = policy.trust_score

        if mode == PolicyMode.NEVER.value:
            return ActionDecision(
                domain=domain,
                action=action,
                mode=mode,
                trust_score=trust,
                should_execute=False,
                reason="Blocked by policy (mode=never)",
            )

        if mode == PolicyMode.AUTO.value:
            return ActionDecision(
                domain=domain,
                action=action,
                mode=mode,
                trust_score=trust,
                should_execute=True,
                reason="Auto-execute (mode=auto)",
            )

        if mode == PolicyMode.DRAFT.value:
            # Draft mode: auto-execute only if trust is high enough
            if trust >= AUTO_TRUST_THRESHOLD:
                return ActionDecision(
                    domain=domain,
                    action=action,
                    mode=mode,
                    trust_score=trust,
                    should_execute=True,
                    reason=(
                        f"Auto-execute (draft mode, trust={trust:.2f} >= {AUTO_TRUST_THRESHOLD})"
                    ),
                )
            return ActionDecision(
                domain=domain,
                action=action,
                mode=mode,
                trust_score=trust,
                should_execute=False,
                reason=f"Draft for review (trust={trust:.2f} < {AUTO_TRUST_THRESHOLD})",
            )

        # mode == 'ask'
        return ActionDecision(
            domain=domain,
            action=action,
            mode=mode,
            trust_score=trust,
            should_execute=False,
            reason="Waiting for user approval (mode=ask)",
        )

    async def record_outcome(
        self,
        user_id: int,
        domain: str,
        action: str,
        outcome: ActionOutcome,
    ) -> float | None:
        """Record the outcome of a reviewed action and update trust.

        Args:
            user_id: The user.
            domain: Action domain.
            action: Specific action.
            outcome: How the user responded.

        Returns:
            The new trust score, or None if the policy doesn't exist.
        """
        delta = OUTCOME_DELTAS[outcome]
        new_score = await self._storage.update_trust_score(user_id, domain, action, delta)

        if new_score is not None:
            log.info(
                "action_outcome_recorded",
                user_id=user_id,
                domain=domain,
                action=action,
                outcome=outcome.value,
                delta=delta,
                new_trust=new_score,
            )

        return new_score

    async def set_mode(
        self,
        user_id: int,
        domain: str,
        action: str,
        mode: PolicyMode,
    ) -> int:
        """Set the mode for a domain/action pair.

        Creates the policy if it doesn't exist.

        Returns:
            The policy ID.
        """
        policy = PersonalPolicy(
            user_id=user_id,
            domain=PolicyDomain(domain),
            action=action,
            mode=mode,
        )
        return await self._storage.upsert_policy(policy)

    async def reset_domain(self, user_id: int, domain: str) -> int:
        """Reset all trust scores for a domain to 0.0.

        Returns:
            Number of policies affected.
        """
        return await self._storage.reset_domain_trust(user_id, domain)
