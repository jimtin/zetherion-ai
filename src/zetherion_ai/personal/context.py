"""Decision context layer for the personal understanding system.

Builds a compact "decision pack" before generating responses or taking
actions. Includes user profile, relevant contacts, schedule constraints,
active policies, and recent learnings.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.personal.operational_storage import OwnerPersonalIntelligenceStorage
from zetherion_ai.personal.storage import PersonalStorage

log = get_logger("zetherion_ai.personal.context")

# Maximum items to include in each context section
MAX_CONTACTS = 5
MAX_POLICIES = 10
MAX_LEARNINGS = 10
MAX_OPERATIONAL_ITEMS = 6
MAX_REVIEW_ITEMS = 6


@dataclass
class DecisionContext:
    """Compact context pack used by the LLM for decision-making."""

    user_profile: dict[str, Any] = field(default_factory=dict)
    relevant_contacts: list[dict[str, Any]] = field(default_factory=list)
    schedule_constraints: list[dict[str, Any]] = field(default_factory=list)
    active_policies: list[dict[str, Any]] = field(default_factory=list)
    recent_learnings: list[dict[str, Any]] = field(default_factory=list)
    operational_state: list[dict[str, Any]] = field(default_factory=list)
    review_state: list[dict[str, Any]] = field(default_factory=list)
    owner_personality: dict[str, Any] = field(default_factory=dict)
    contact_personalities: list[dict[str, Any]] = field(default_factory=list)

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

        if self.operational_state:
            operational_strs = []
            for item in self.operational_state[:3]:
                item_type = str(item.get("item_type", "item")).replace("_", " ")
                title = str(item.get("title", "")).strip()
                status = str(item.get("status", "active")).replace("_", " ")
                due_at = item.get("due_at")
                due_suffix = ""
                if isinstance(due_at, datetime):
                    due_suffix = f" due {due_at.strftime('%Y-%m-%d')}"
                elif isinstance(due_at, str) and due_at.strip():
                    due_suffix = f" due {due_at[:10]}"
                operational_strs.append(f"{item_type}: {title} [{status}{due_suffix}]")
            parts.append(f"Operational state: {'; '.join(operational_strs)}")

        if self.review_state:
            review_strs = []
            for item in self.review_state[:3]:
                item_type = str(item.get("item_type", "review")).replace("_", " ")
                title = str(item.get("title", "")).strip()
                review_strs.append(f"{item_type}: {title}")
            parts.append(f"Pending review queue: {'; '.join(review_strs)}")

        if self.owner_personality:
            ws = self.owner_personality.get("writing_style", {})
            comm = self.owner_personality.get("communication", {})
            parts.append(
                f"Owner style: {ws.get('formality_mode')} formality, "
                f"{comm.get('primary_trait_mode')} communication, "
                f"assertiveness: {comm.get('assertiveness_ema', 0.5):.2f}"
            )

        for cp in self.contact_personalities[:3]:
            rel = cp.get("relationship", {})
            parts.append(
                f"Contact {cp.get('subject_email')}: "
                f"familiarity={rel.get('familiarity_ema', 0.5):.2f}, "
                f"dynamic={rel.get('power_dynamic_mode')}, "
                f"obs={cp.get('observation_count', 0)}"
            )

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
            and not self.operational_state
            and not self.review_state
            and not self.owner_personality
            and not self.contact_personalities
        )


class DecisionContextBuilder:
    """Builds decision context from the personal model storage."""

    def __init__(
        self,
        storage: PersonalStorage,
        *,
        operational_storage: OwnerPersonalIntelligenceStorage | None = None,
    ) -> None:
        self._storage = storage
        self._operational_storage = operational_storage

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

        if self._operational_storage is not None:
            operational_items = await self._operational_storage.list_operational_items(
                user_id,
                active_only=True,
                limit=MAX_OPERATIONAL_ITEMS,
            )
            ctx.operational_state = [item.to_db_row() for item in operational_items]

            review_items = await self._operational_storage.list_review_items(
                user_id,
                pending_only=True,
                limit=MAX_REVIEW_ITEMS,
            )
            ctx.review_state = [item.to_db_row() for item in review_items]

        # Owner personality profile
        owner_profiles = await self._storage.list_personality_profiles(
            user_id,
            subject_role="owner",
            limit=1,
        )
        if owner_profiles:
            ctx.owner_personality = owner_profiles[0].to_db_row()

        # Contact personalities for mentioned emails
        if mentioned_emails:
            for email in mentioned_emails[:MAX_CONTACTS]:
                cp = await self._storage.get_personality_profile(user_id, email, "contact")
                if cp:
                    ctx.contact_personalities.append(cp.to_db_row())

        log.info(
            "decision_context_built",
            user_id=user_id,
            has_profile=bool(profile),
            contacts=len(ctx.relevant_contacts),
            policies=len(ctx.active_policies),
            learnings=len(ctx.recent_learnings),
            operational_items=len(ctx.operational_state),
            review_items=len(ctx.review_state),
        )

        return ctx
