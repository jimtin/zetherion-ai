"""Personal Model Skill for Zetherion AI.

Exposes the personal understanding layer as a skill for Discord queries.
Handles profile summaries, updates, contact listing, data export, and
learning management through the PersonalStorage backend.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.personal.models import (
    CommunicationStyle,
    LearningCategory,
    LearningSource,
    PersonalLearning,
    PersonalProfile,
    WorkingHours,
)
from zetherion_ai.skills.base import (
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.memory.qdrant import QdrantMemory
    from zetherion_ai.personal.storage import PersonalStorage

log = get_logger("zetherion_ai.skills.personal_model")


# Intent constants
INTENT_SUMMARY = "personal_summary"
INTENT_UPDATE = "personal_update"
INTENT_FORGET = "personal_forget"
INTENT_CONTACTS = "personal_contacts"
INTENT_EXPORT = "personal_export"
INTENT_POLICIES = "personal_policies"

# Timezone aliases for common shorthand
TIMEZONE_ALIASES: dict[str, str] = {
    "pst": "America/Los_Angeles",
    "pdt": "America/Los_Angeles",
    "mst": "America/Denver",
    "mdt": "America/Denver",
    "cst": "America/Chicago",
    "cdt": "America/Chicago",
    "est": "America/New_York",
    "edt": "America/New_York",
    "gmt": "Etc/GMT",
    "bst": "Europe/London",
    "cet": "Europe/Paris",
    "cest": "Europe/Paris",
    "jst": "Asia/Tokyo",
    "aest": "Australia/Sydney",
    "aedt": "Australia/Sydney",
    "nzst": "Pacific/Auckland",
    "nzdt": "Pacific/Auckland",
    "ist": "Asia/Kolkata",
}


class PersonalModelSkill(Skill):
    """Skill for querying and managing the personal understanding model.

    Intents handled:
    - personal_summary: What do you know about me?
    - personal_update: Update profile fields (timezone, locale, etc.)
    - personal_forget: Forget/delete a specific learning
    - personal_contacts: List known contacts + importance scores
    - personal_export: GDPR-style data export
    - personal_policies: Show current policies and trust scores
    """

    INTENTS = [
        INTENT_SUMMARY,
        INTENT_UPDATE,
        INTENT_FORGET,
        INTENT_CONTACTS,
        INTENT_EXPORT,
        INTENT_POLICIES,
    ]

    def __init__(
        self,
        memory: QdrantMemory | None = None,
        storage: PersonalStorage | None = None,
    ) -> None:
        """Initialize the personal model skill.

        Args:
            memory: Optional Qdrant memory (not used directly but required by ABC).
            storage: PersonalStorage for PostgreSQL-backed personal data.
        """
        super().__init__(memory=memory)
        self._storage = storage

    @property
    def metadata(self) -> SkillMetadata:
        """Return skill metadata."""
        return SkillMetadata(
            name="personal_model",
            description="Query and manage your personal understanding model",
            version="1.0.0",
            permissions=PermissionSet(
                {
                    Permission.READ_PROFILE,
                    Permission.WRITE_PROFILE,
                    Permission.DELETE_PROFILE,
                    Permission.SEND_MESSAGES,
                }
            ),
            intents=self.INTENTS,
        )

    async def initialize(self) -> bool:
        """Initialize the skill.

        Returns:
            True if storage is available, False otherwise.
        """
        if self._storage is None:
            log.warning("personal_model_skill_no_storage")
            return False
        log.info("personal_model_skill_initialized")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle a personal model request.

        Args:
            request: The incoming skill request.

        Returns:
            Response with results or error.
        """
        if self._storage is None:
            return SkillResponse.error_response(
                request.id,
                "Personal storage is not available",
            )

        handlers = {
            INTENT_SUMMARY: self._handle_summary,
            INTENT_UPDATE: self._handle_update,
            INTENT_FORGET: self._handle_forget,
            INTENT_CONTACTS: self._handle_contacts,
            INTENT_EXPORT: self._handle_export,
            INTENT_POLICIES: self._handle_policies,
        }

        handler = handlers.get(request.intent)
        if not handler:
            return SkillResponse.error_response(
                request.id,
                f"Unknown intent: {request.intent}",
            )

        return await handler(request)

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    async def _handle_summary(self, request: SkillRequest) -> SkillResponse:
        """Handle 'What do you know about me?' queries."""
        assert self._storage is not None  # noqa: S101
        user_id = int(request.user_id)

        profile = await self._storage.get_profile(user_id)
        contacts = await self._storage.list_contacts(user_id, limit=5)
        learnings = await self._storage.list_learnings(user_id, limit=10)
        policies = await self._storage.list_policies(user_id)

        parts: list[str] = []

        if profile:
            parts.append(f"**Profile**: {profile.display_name or 'No name set'}")
            parts.append(f"Timezone: {profile.timezone}, Locale: {profile.locale}")
            if profile.goals:
                parts.append(f"Goals: {', '.join(profile.goals)}")
            if profile.communication_style:
                cs = profile.communication_style
                parts.append(
                    f"Style: formality={cs.formality:.1f}," f" verbosity={cs.verbosity:.1f}"
                )
        else:
            parts.append("I don't have a profile for you yet.")

        if contacts:
            parts.append(f"\n**Contacts** ({len(contacts)}):")
            for c in contacts:
                name = c.contact_name or c.contact_email or "unknown"
                parts.append(
                    f"  - {name} ({c.relationship.value}," f" importance={c.importance:.1f})"
                )

        if learnings:
            confirmed = sum(1 for lr in learnings if lr.confirmed)
            parts.append(f"\n**Learnings**: {len(learnings)} total" f" ({confirmed} confirmed)")
            for lr in learnings[:5]:
                marker = "+" if lr.confirmed else "?"
                parts.append(f"  [{marker}] {lr.content}" f" (confidence={lr.confidence:.1f})")

        if policies:
            parts.append(f"\n**Policies**: {len(policies)} configured")

        message = "\n".join(parts)

        return SkillResponse(
            request_id=request.id,
            message=message,
            data={
                "has_profile": profile is not None,
                "contact_count": len(contacts),
                "learning_count": len(learnings),
                "policy_count": len(policies),
            },
        )

    async def _handle_update(self, request: SkillRequest) -> SkillResponse:
        """Handle profile field updates (timezone, locale, goals, etc.)."""
        assert self._storage is not None  # noqa: S101
        user_id = int(request.user_id)
        context = request.context

        # Determine what field to update
        field_name = context.get("field")
        value = context.get("value")

        if not field_name or value is None:
            # Try to parse from message
            result = self._parse_update_from_message(request.message)
            if result:
                field_name, value = result
            else:
                return SkillResponse.error_response(
                    request.id,
                    "Could not determine what to update."
                    " Try: 'My timezone is PST' or"
                    " 'Set my locale to en'.",
                )

        # Get or create profile
        profile = await self._storage.get_profile(user_id)
        if profile is None:
            profile = PersonalProfile(user_id=user_id)

        # Apply update
        updated_field, display_value = self._apply_field_update(profile, field_name, value)
        if updated_field is None:
            return SkillResponse.error_response(
                request.id,
                f"Unknown field: {field_name}. Supported:"
                " timezone, locale, display_name, goals,"
                " formality, verbosity, working_hours_start,"
                " working_hours_end.",
            )

        await self._storage.upsert_profile(profile)

        # Also log as a learning
        learning = PersonalLearning(
            user_id=user_id,
            category=LearningCategory.PREFERENCE,
            content=f"User set {updated_field} to {display_value}",
            confidence=1.0,
            source=LearningSource.EXPLICIT,
            confirmed=True,
        )
        await self._storage.add_learning(learning)

        log.info(
            "personal_profile_updated",
            user_id=user_id,
            field=updated_field,
            value=str(display_value),
        )

        return SkillResponse(
            request_id=request.id,
            message=f"Updated {updated_field} to: {display_value}",
            data={"field": updated_field, "value": display_value},
        )

    async def _handle_forget(self, request: SkillRequest) -> SkillResponse:
        """Handle 'forget that...' requests."""
        assert self._storage is not None  # noqa: S101
        user_id = int(request.user_id)
        context = request.context

        learning_id = context.get("learning_id")
        category = context.get("category")
        content_match = context.get("content_match") or request.message

        if learning_id is not None:
            deleted = await self._storage.delete_learning(int(learning_id))
            if deleted:
                return SkillResponse(
                    request_id=request.id,
                    message="Forgotten! That learning has been deleted.",
                    data={"deleted": True, "learning_id": learning_id},
                )
            return SkillResponse.error_response(
                request.id,
                f"Learning #{learning_id} not found.",
            )

        if category:
            count = await self._storage.delete_learnings_by_category(user_id, category)
            return SkillResponse(
                request_id=request.id,
                message=(f"Forgotten! Deleted {count} learning(s)" f" in category '{category}'."),
                data={"deleted": True, "count": count, "category": category},
            )

        # Search learnings for content match
        learnings = await self._storage.list_learnings(user_id, limit=100)
        content_lower = content_match.lower()
        matches = [lr for lr in learnings if content_lower in lr.content.lower()]

        if not matches:
            return SkillResponse(
                request_id=request.id,
                message="I couldn't find any matching learnings to forget.",
                data={"deleted": False, "matches": 0},
            )

        deleted_count = 0
        for lr in matches:
            if lr.id is not None and await self._storage.delete_learning(lr.id):
                deleted_count += 1

        return SkillResponse(
            request_id=request.id,
            message=f"Forgotten! Deleted {deleted_count} matching learning(s).",
            data={"deleted": True, "count": deleted_count},
        )

    async def _handle_contacts(self, request: SkillRequest) -> SkillResponse:
        """Handle 'show my contacts' requests."""
        assert self._storage is not None  # noqa: S101
        user_id = int(request.user_id)
        context = request.context

        relationship = context.get("relationship")
        min_importance = context.get("min_importance")
        limit = context.get("limit", 20)

        contacts = await self._storage.list_contacts(
            user_id,
            relationship=relationship,
            min_importance=float(min_importance) if min_importance else None,
            limit=int(limit),
        )

        if not contacts:
            return SkillResponse(
                request_id=request.id,
                message="No contacts found.",
                data={"contacts": [], "count": 0},
            )

        parts = [f"**Your contacts** ({len(contacts)}):"]
        contact_data = []
        for c in contacts:
            name = c.contact_name or c.contact_email or "unknown"
            parts.append(
                f"  - {name} ({c.relationship.value})"
                f" importance={c.importance:.1f},"
                f" interactions={c.interaction_count}"
            )
            contact_data.append(
                {
                    "name": c.contact_name,
                    "email": c.contact_email,
                    "relationship": c.relationship.value,
                    "importance": c.importance,
                    "company": c.company,
                    "interaction_count": c.interaction_count,
                }
            )

        return SkillResponse(
            request_id=request.id,
            message="\n".join(parts),
            data={"contacts": contact_data, "count": len(contacts)},
        )

    async def _handle_export(self, request: SkillRequest) -> SkillResponse:
        """Handle GDPR-style data export."""
        assert self._storage is not None  # noqa: S101
        user_id = int(request.user_id)

        profile = await self._storage.get_profile(user_id)
        contacts = await self._storage.list_contacts(user_id, limit=1000)
        learnings = await self._storage.list_learnings(user_id, limit=1000)
        policies = await self._storage.list_policies(user_id)

        export: dict[str, Any] = {
            "exported_at": datetime.now().isoformat(),
            "user_id": user_id,
            "profile": profile.to_db_row() if profile else None,
            "contacts": [c.to_db_row() for c in contacts],
            "learnings": [lr.to_db_row() for lr in learnings],
            "policies": [p.to_db_row() for p in policies],
        }

        total_items = (1 if profile else 0) + len(contacts) + len(learnings) + len(policies)

        log.info("personal_data_exported", user_id=user_id, total_items=total_items)

        return SkillResponse(
            request_id=request.id,
            message=(
                f"Exported {total_items} items:"
                f" profile={'yes' if profile else 'no'},"
                f" {len(contacts)} contacts,"
                f" {len(learnings)} learnings,"
                f" {len(policies)} policies."
            ),
            data={"export": export, "total_items": total_items},
        )

    async def _handle_policies(self, request: SkillRequest) -> SkillResponse:
        """Handle policy listing requests."""
        assert self._storage is not None  # noqa: S101
        user_id = int(request.user_id)
        domain = request.context.get("domain")

        policies = await self._storage.list_policies(user_id, domain=domain)

        if not policies:
            return SkillResponse(
                request_id=request.id,
                message="No policies configured.",
                data={"policies": [], "count": 0},
            )

        parts = [f"**Your policies** ({len(policies)}):"]
        policy_data = []
        for p in policies:
            parts.append(
                f"  - {p.domain.value}/{p.action}:"
                f" mode={p.mode.value},"
                f" trust={p.trust_score:.2f}"
            )
            policy_data.append(
                {
                    "domain": p.domain.value,
                    "action": p.action,
                    "mode": p.mode.value,
                    "trust_score": p.trust_score,
                }
            )

        return SkillResponse(
            request_id=request.id,
            message="\n".join(parts),
            data={"policies": policy_data, "count": len(policies)},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _parse_update_from_message(self, message: str) -> tuple[str, str] | None:
        """Try to extract a field/value pair from a natural language message.

        Supports patterns like:
        - "My timezone is PST"
        - "Set my locale to fr"
        - "My name is James"
        - "Add goal: learn Rust"

        Returns:
            A (field_name, value) tuple, or None if unparseable.
        """
        msg_lower = message.lower().strip()

        # Timezone patterns
        for prefix in (
            "my timezone is ",
            "timezone is ",
            "set timezone to ",
            "set my timezone to ",
        ):
            if msg_lower.startswith(prefix):
                tz = message[len(prefix) :].strip()
                return ("timezone", tz)

        # Locale patterns
        for prefix in (
            "my locale is ",
            "locale is ",
            "set locale to ",
            "set my locale to ",
            "my language is ",
            "set my language to ",
        ):
            if msg_lower.startswith(prefix):
                locale_val = message[len(prefix) :].strip()
                return ("locale", locale_val)

        # Name patterns
        for prefix in ("my name is ", "call me ", "set my name to ", "my display name is "):
            if msg_lower.startswith(prefix):
                name = message[len(prefix) :].strip()
                return ("display_name", name)

        # Goal patterns
        for prefix in ("add goal: ", "add goal ", "my goal is ", "new goal: ", "new goal "):
            if msg_lower.startswith(prefix):
                goal = message[len(prefix) :].strip()
                return ("goal", goal)

        return None

    def _apply_field_update(
        self,
        profile: PersonalProfile,
        field_name: str,
        value: Any,
    ) -> tuple[str | None, Any]:
        """Apply a field update to a profile.

        Returns:
            (field_name, display_value) on success, (None, None) on failure.
        """
        field_lower = field_name.lower()

        if field_lower == "timezone":
            resolved = TIMEZONE_ALIASES.get(str(value).lower(), str(value))
            profile.timezone = resolved
            return ("timezone", resolved)

        if field_lower == "locale":
            profile.locale = str(value)
            return ("locale", str(value))

        if field_lower == "display_name":
            profile.display_name = str(value)
            return ("display_name", str(value))

        if field_lower == "goal":
            goal_str = str(value)
            if goal_str not in profile.goals:
                profile.goals.append(goal_str)
            return ("goal", goal_str)

        if field_lower == "formality":
            val = float(value)
            if profile.communication_style is None:
                profile.communication_style = CommunicationStyle()
            profile.communication_style.formality = max(0.0, min(1.0, val))
            return ("formality", profile.communication_style.formality)

        if field_lower == "verbosity":
            val = float(value)
            if profile.communication_style is None:
                profile.communication_style = CommunicationStyle()
            profile.communication_style.verbosity = max(0.0, min(1.0, val))
            return ("verbosity", profile.communication_style.verbosity)

        if field_lower == "working_hours_start":
            if profile.working_hours is None:
                profile.working_hours = WorkingHours()
            profile.working_hours.start = str(value)
            return ("working_hours_start", str(value))

        if field_lower == "working_hours_end":
            if profile.working_hours is None:
                profile.working_hours = WorkingHours()
            profile.working_hours.end = str(value)
            return ("working_hours_end", str(value))

        return (None, None)

    async def cleanup(self) -> None:
        """Clean up resources."""
        log.info("personal_model_skill_cleanup")

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        """Return context fragment for LLM system prompt.

        The actual context injection is handled by DecisionContextBuilder
        in core.py, so this returns None.
        """
        return None
