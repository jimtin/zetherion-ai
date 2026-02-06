"""Profile Management Skill for SecureClaw.

Provides user profile management capabilities:
- View what the bot knows about the user
- Update profile entries explicitly
- Delete specific profile entries (forget)
- Export profile data (GDPR-style)
- View confidence reports on inferred data

Uses the existing user_profiles collection from Phase 5C.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.memory.qdrant import QdrantMemory
    from zetherion_ai.profile.builder import ProfileBuilder

log = get_logger("zetherion_ai.skills.profile_skill")

# Uses existing collection from Phase 5C
PROFILES_COLLECTION = "user_profiles"


@dataclass
class ProfileSummary:
    """Summary of user profile data."""

    total_entries: int = 0
    by_category: dict[str, int] | None = None
    high_confidence: int = 0  # >= 0.8
    medium_confidence: int = 0  # 0.5 - 0.8
    low_confidence: int = 0  # < 0.5
    oldest_entry: datetime | None = None
    newest_entry: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "total_entries": self.total_entries,
            "by_category": self.by_category or {},
            "high_confidence": self.high_confidence,
            "medium_confidence": self.medium_confidence,
            "low_confidence": self.low_confidence,
            "oldest_entry": self.oldest_entry.isoformat() if self.oldest_entry else None,
            "newest_entry": self.newest_entry.isoformat() if self.newest_entry else None,
        }


class ProfileSkill(Skill):
    """Skill for managing user profile data.

    Intents handled:
    - profile_summary: Get overview of what the bot knows
    - profile_view: View specific profile entries
    - profile_update: Explicitly update a profile entry
    - profile_delete: Delete/forget specific entries
    - profile_export: Export all profile data
    - profile_confidence: View confidence report

    Heartbeat actions:
    - confirm_low_confidence: Ask user to confirm uncertain entries
    - profile_gap: Ask about missing important info
    - decay_check: Notify about stale entries
    """

    INTENTS = [
        "profile_summary",
        "profile_view",
        "profile_update",
        "profile_delete",
        "profile_export",
        "profile_confidence",
    ]

    def __init__(
        self,
        memory: "QdrantMemory | None" = None,
        profile_builder: "ProfileBuilder | None" = None,
    ):
        """Initialize the profile skill."""
        super().__init__(memory=memory)
        self._profile_builder = profile_builder

    @property
    def metadata(self) -> SkillMetadata:
        """Return skill metadata."""
        return SkillMetadata(
            name="profile_manager",
            description="View and manage what the bot knows about you",
            version="1.0.0",
            permissions=PermissionSet(
                {
                    Permission.READ_PROFILE,
                    Permission.WRITE_PROFILE,
                    Permission.DELETE_PROFILE,
                    Permission.SEND_MESSAGES,
                }
            ),
            collections=[PROFILES_COLLECTION],
            intents=self.INTENTS,
        )

    async def initialize(self) -> bool:
        """Initialize the skill."""
        # Collection already exists from Phase 5C
        log.info("profile_skill_initialized")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle a profile management request."""
        handlers = {
            "profile_summary": self._handle_summary,
            "profile_view": self._handle_view,
            "profile_update": self._handle_update,
            "profile_delete": self._handle_delete,
            "profile_export": self._handle_export,
            "profile_confidence": self._handle_confidence,
        }

        handler = handlers.get(request.intent)
        if not handler:
            return SkillResponse.error_response(
                request.id,
                f"Unknown intent: {request.intent}",
            )

        return await handler(request)

    async def _handle_summary(self, request: SkillRequest) -> SkillResponse:
        """Handle profile summary request."""
        entries = await self._get_profile_entries(request.user_id)

        if not entries:
            return SkillResponse(
                request_id=request.id,
                message="I don't have any profile data for you yet.",
                data={"summary": ProfileSummary().to_dict()},
            )

        # Calculate summary
        summary = ProfileSummary(total_entries=len(entries))
        summary.by_category = {}

        for entry in entries:
            category = entry.get("category", "unknown")
            summary.by_category[category] = summary.by_category.get(category, 0) + 1

            confidence = entry.get("confidence", 0.5)
            if confidence >= 0.8:
                summary.high_confidence += 1
            elif confidence >= 0.5:
                summary.medium_confidence += 1
            else:
                summary.low_confidence += 1

            created = entry.get("created_at")
            if created:
                try:
                    created_dt = datetime.fromisoformat(created)
                    if not summary.oldest_entry or created_dt < summary.oldest_entry:
                        summary.oldest_entry = created_dt
                    if not summary.newest_entry or created_dt > summary.newest_entry:
                        summary.newest_entry = created_dt
                except ValueError:
                    pass

        # Generate message
        message = f"I know {len(entries)} thing(s) about you"
        if summary.by_category:
            top_categories = sorted(summary.by_category.items(), key=lambda x: x[1], reverse=True)[
                :3
            ]
            category_str = ", ".join(f"{cat}: {count}" for cat, count in top_categories)
            message += f" ({category_str})"

        return SkillResponse(
            request_id=request.id,
            message=message,
            data={"summary": summary.to_dict()},
        )

    async def _handle_view(self, request: SkillRequest) -> SkillResponse:
        """Handle viewing profile entries."""
        context = request.context
        category = context.get("category")
        key = context.get("key")

        entries = await self._get_profile_entries(request.user_id)

        # Filter by category if specified
        if category:
            entries = [e for e in entries if e.get("category") == category]

        # Filter by key if specified
        if key:
            entries = [e for e in entries if e.get("key") == key]

        if not entries:
            return SkillResponse(
                request_id=request.id,
                message="No matching profile entries found.",
                data={"entries": []},
            )

        # Sort by confidence (highest first)
        entries.sort(key=lambda e: e.get("confidence", 0), reverse=True)

        # Format entries for display
        formatted = []
        for entry in entries:
            formatted.append(
                {
                    "category": entry.get("category"),
                    "key": entry.get("key"),
                    "value": entry.get("value"),
                    "confidence": entry.get("confidence"),
                    "source": entry.get("source"),
                }
            )

        return SkillResponse(
            request_id=request.id,
            message=f"Found {len(entries)} profile entry/entries.",
            data={"entries": formatted, "count": len(entries)},
        )

    async def _handle_update(self, request: SkillRequest) -> SkillResponse:
        """Handle explicit profile update."""
        context = request.context

        category = context.get("category")
        key = context.get("key")
        value = context.get("value")

        if not all([category, key, value]):
            return SkillResponse.error_response(
                request.id,
                "Missing required fields: category, key, value",
            )

        # Use profile builder if available
        if self._profile_builder:
            try:
                await self._profile_builder.update_profile_entry(
                    user_id=request.user_id,
                    category=category,
                    key=key,
                    value=value,
                    confidence=1.0,  # Explicit updates get full confidence
                    source="explicit",
                )
                log.info(
                    "profile_entry_updated",
                    user_id=request.user_id,
                    category=category,
                    key=key,
                )
                return SkillResponse(
                    request_id=request.id,
                    message=f"Updated {category}/{key} to: {value}",
                    data={"category": category, "key": key, "value": value},
                )
            except Exception as e:
                log.error("profile_update_failed", error=str(e))
                return SkillResponse.error_response(request.id, f"Update failed: {e}")

        return SkillResponse.error_response(
            request.id,
            "Profile builder not available",
        )

    async def _handle_delete(self, request: SkillRequest) -> SkillResponse:
        """Handle profile entry deletion (forget)."""
        context = request.context

        category = context.get("category")
        key = context.get("key")
        entry_id = context.get("entry_id")

        if not entry_id and not (category and key):
            return SkillResponse.error_response(
                request.id,
                "Provide either entry_id or both category and key",
            )

        if self._profile_builder:
            try:
                if entry_id:
                    await self._profile_builder.delete_profile_entry(
                        user_id=request.user_id,
                        entry_id=entry_id,
                    )
                else:
                    await self._profile_builder.delete_profile_entry_by_key(
                        user_id=request.user_id,
                        category=category,
                        key=key,
                    )

                log.info(
                    "profile_entry_deleted",
                    user_id=request.user_id,
                    category=category,
                    key=key,
                )

                return SkillResponse(
                    request_id=request.id,
                    message=f"Forgotten: {category}/{key}" if key else "Entry deleted",
                    data={"deleted": True},
                )
            except Exception as e:
                log.error("profile_delete_failed", error=str(e))
                return SkillResponse.error_response(request.id, f"Delete failed: {e}")

        return SkillResponse.error_response(
            request.id,
            "Profile builder not available",
        )

    async def _handle_export(self, request: SkillRequest) -> SkillResponse:
        """Handle profile data export (GDPR-style)."""
        entries = await self._get_profile_entries(request.user_id)

        if not entries:
            return SkillResponse(
                request_id=request.id,
                message="No profile data to export.",
                data={"export": []},
            )

        # Format all entries for export
        export_data = []
        for entry in entries:
            export_data.append(
                {
                    "id": entry.get("id"),
                    "category": entry.get("category"),
                    "key": entry.get("key"),
                    "value": entry.get("value"),
                    "confidence": entry.get("confidence"),
                    "source": entry.get("source"),
                    "created_at": entry.get("created_at"),
                    "updated_at": entry.get("updated_at"),
                }
            )

        log.info("profile_exported", user_id=request.user_id, count=len(export_data))

        return SkillResponse(
            request_id=request.id,
            message=f"Exported {len(export_data)} profile entries.",
            data={
                "export": export_data,
                "count": len(export_data),
                "exported_at": datetime.now().isoformat(),
            },
        )

    async def _handle_confidence(self, request: SkillRequest) -> SkillResponse:
        """Handle confidence report request."""
        entries = await self._get_profile_entries(request.user_id)

        if not entries:
            return SkillResponse(
                request_id=request.id,
                message="No profile data to analyze.",
                data={"report": {}},
            )

        # Group by confidence level
        high = []  # >= 0.8
        medium = []  # 0.5 - 0.8
        low = []  # < 0.5

        for entry in entries:
            confidence = entry.get("confidence", 0.5)
            entry_summary = {
                "category": entry.get("category"),
                "key": entry.get("key"),
                "value": entry.get("value"),
                "confidence": confidence,
            }

            if confidence >= 0.8:
                high.append(entry_summary)
            elif confidence >= 0.5:
                medium.append(entry_summary)
            else:
                low.append(entry_summary)

        report = {
            "high_confidence": {
                "count": len(high),
                "entries": high,
            },
            "medium_confidence": {
                "count": len(medium),
                "entries": medium,
            },
            "low_confidence": {
                "count": len(low),
                "entries": low,
            },
        }

        message = (
            f"Confidence report: {len(high)} high, "
            f"{len(medium)} medium, {len(low)} low confidence entries."
        )
        if low:
            message += f" {len(low)} entries need confirmation."

        return SkillResponse(
            request_id=request.id,
            message=message,
            data={"report": report},
        )

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Check for profile-related actions."""
        actions: list[HeartbeatAction] = []

        for user_id in user_ids:
            entries = await self._get_profile_entries(user_id)

            # Find low-confidence entries that need confirmation
            low_confidence = [e for e in entries if e.get("confidence", 0.5) < 0.5]
            if low_confidence:
                # Pick the most important one to confirm
                to_confirm = low_confidence[0]
                actions.append(
                    HeartbeatAction(
                        skill_name=self.name,
                        action_type="confirm_low_confidence",
                        user_id=user_id,
                        data={
                            "entry": {
                                "category": to_confirm.get("category"),
                                "key": to_confirm.get("key"),
                                "value": to_confirm.get("value"),
                                "confidence": to_confirm.get("confidence"),
                            },
                            "total_needing_confirmation": len(low_confidence),
                        },
                        priority=4,
                    )
                )

            # Check for stale entries (not updated in 30 days)
            now = datetime.now()
            stale = []
            for entry in entries:
                updated = entry.get("updated_at")
                if updated:
                    try:
                        updated_dt = datetime.fromisoformat(updated)
                        if (now - updated_dt).days > 30:
                            stale.append(entry)
                    except ValueError:
                        pass

            if stale:
                actions.append(
                    HeartbeatAction(
                        skill_name=self.name,
                        action_type="decay_check",
                        user_id=user_id,
                        data={
                            "stale_count": len(stale),
                            "categories": list({e.get("category") for e in stale}),
                        },
                        priority=2,
                    )
                )

        return actions

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        """Return context about user's profile for the system prompt."""
        # This would typically pull from cache
        # For now, return None since we don't have sync access to entries
        return None

    # Helper methods

    async def _get_profile_entries(self, user_id: str) -> list[dict[str, Any]]:
        """Get all profile entries for a user."""
        if self._memory:
            results = await self._memory.filter_by_field(
                collection_name=PROFILES_COLLECTION,
                field="user_id",
                value=user_id,
            )
            return results

        if self._profile_builder:
            # Try to get from profile builder, fallback to empty on any error
            try:
                entries = await self._profile_builder.get_all_profile_entries(user_id)
                return [e.to_dict() for e in entries]
            except Exception:  # nosec B110 - Graceful fallback to empty list
                pass

        return []

    async def cleanup(self) -> None:
        """Clean up resources."""
        log.info("profile_skill_cleanup_complete")
