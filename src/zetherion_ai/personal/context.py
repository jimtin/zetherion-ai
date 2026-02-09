"""Decision context layer for the personal understanding system.

Builds a compact "decision pack" before generating responses or taking
actions. Includes user profile, relevant contacts, schedule constraints,
active policies, and recent learnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.personal.storage import PersonalStorage

log = get_logger("zetherion_ai.personal.context")

# Maximum items to include in each context section
MAX_CONTACTS = 5
MAX_POLICIES = 10
MAX_LEARNINGS = 10


@dataclass
class DecisionContext:
    """Compact context pack used by the LLM for decision-making."""

    user_profile: dict[str, Any] = field(default_factory=dict)
    relevant_contacts: list[dict[str, Any]] = field(default_factory=list)
    schedule_constraints: list[dict[str, Any]] = field(default_factory=list)
    active_policies: list[dict[str, Any]] = field(default_factory=list)
    recent_learnings: list[dict[str, Any]] = field(default_factory=list)

    def to_prompt_fragment(self) -> str:
        """Convert the decision context into a text fragment for the LLM system prompt."""
        parts: list[str] = []

        if self.user_profile:
            name = self.user_profile.get("display_name", "the user")
            tz = self.user_profile.get("timezone", "UTC")
            parts.append(f"User: {name} (timezone: {tz})")

            style = self.user_profile.get("communication_style")
            if style:
                formality = style.get("formality", 0.5)
                label = "formal" if formality > 0.6 else "casual" if formality < 0.4 else "balanced"
                parts.append(f"Communication style: {label}")

            goals = self.user_profile.get("goals", [])
            if goals:
                parts.append(f"Current goals: {', '.join(goals[:3])}")

        if self.relevant_contacts:
            contact_strs = []
            for c in self.relevant_contacts[:3]:
                name = c.get("contact_name") or c.get("contact_email", "unknown")
                rel = c.get("relationship", "")
                contact_strs.append(f"{name} ({rel})")
            parts.append(f"Relevant contacts: {', '.join(contact_strs)}")

        if self.active_policies:
            policy_strs = []
            for p in self.active_policies[:3]:
                policy_strs.append(
                    f"{p.get('domain', '?')}/{p.get('action', '?')}: {p.get('mode', '?')}"
                )
            parts.append(f"Active policies: {', '.join(policy_strs)}")

        if self.recent_learnings:
            learning_strs = [lr.get("content", "") for lr in self.recent_learnings[:3]]
            parts.append(f"Recent learnings: {'; '.join(learning_strs)}")

        return "\n".join(parts) if parts else ""

    @property
    def is_empty(self) -> bool:
        """Check if the context has any useful data."""
        return (
            not self.user_profile
            and not self.relevant_contacts
            and not self.schedule_constraints
            and not self.active_policies
            and not self.recent_learnings
        )


class DecisionContextBuilder:
    """Builds decision context from the personal model storage."""

    def __init__(self, storage: PersonalStorage) -> None:
        self._storage = storage

    async def build(
        self,
        user_id: int,
        *,
        message: str | None = None,
        mentioned_emails: list[str] | None = None,
    ) -> DecisionContext:
        """Build a decision context for the given user.

        Args:
            user_id: The user to build context for.
            message: Optional current message for extracting relevant context.
            mentioned_emails: Optional list of emails mentioned in context.

        Returns:
            A populated DecisionContext.
        """
        ctx = DecisionContext()

        # Fetch profile
        profile = await self._storage.get_profile(user_id)
        if profile:
            ctx.user_profile = profile.to_db_row()
            # Include nested model data
            if profile.communication_style:
                ctx.user_profile["communication_style"] = profile.communication_style.model_dump()
            if profile.working_hours:
                ctx.user_profile["working_hours"] = profile.working_hours.model_dump()

        # Fetch relevant contacts
        if mentioned_emails:
            for email in mentioned_emails[:MAX_CONTACTS]:
                contact = await self._storage.get_contact(user_id, email)
                if contact:
                    ctx.relevant_contacts.append(contact.to_db_row())
        else:
            # Get top contacts by importance
            contacts = await self._storage.list_contacts(user_id, limit=MAX_CONTACTS)
            ctx.relevant_contacts = [c.to_db_row() for c in contacts]

        # Fetch active policies
        policies = await self._storage.list_policies(user_id)
        ctx.active_policies = [p.to_db_row() for p in policies[:MAX_POLICIES]]

        # Fetch recent learnings
        learnings = await self._storage.list_learnings(user_id, limit=MAX_LEARNINGS)
        ctx.recent_learnings = [lr.to_db_row() for lr in learnings]

        log.info(
            "decision_context_built",
            user_id=user_id,
            has_profile=bool(profile),
            contacts=len(ctx.relevant_contacts),
            policies=len(ctx.active_policies),
            learnings=len(ctx.recent_learnings),
        )

        return ctx
