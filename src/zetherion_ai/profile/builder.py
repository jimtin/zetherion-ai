"""Profile builder for extracting and managing user profiles.

Orchestrates the profile extraction pipeline:
1. Run inference engines on messages
2. Filter and merge updates
3. Apply high-confidence updates
4. Queue low-confidence updates for confirmation
5. Persist to storage and invalidate cache
6. Track relationship events and update employment profile
"""

import asyncio
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.profile.cache import (
    EmploymentProfileSummary,
    ProfileCache,
    UserProfileSummary,
)
from zetherion_ai.profile.employment import (
    EmploymentProfile,
    create_default_profile,
)
from zetherion_ai.profile.inference import ProfileInferencePipeline
from zetherion_ai.profile.models import (
    CONFIDENCE_AUTO_APPLY,
    CONFIDENCE_FLAG_CONFIRM,
    CONFIDENCE_LOG_ONLY,
    CONFIDENCE_QUEUE_CONFIRM,
    ProfileCategory,
    ProfileEntry,
    ProfileSource,
    ProfileUpdate,
)
from zetherion_ai.profile.relationship import (
    RelationshipEvent,
    RelationshipTracker,
)
from zetherion_ai.profile.storage import ProfileStats, ProfileStorage

if TYPE_CHECKING:
    from zetherion_ai.agent.inference import InferenceBroker
    from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.profile.builder")

# Qdrant collection for user profiles
USER_PROFILES_COLLECTION = "user_profiles"


class ProfileBuilder:
    """Builds and manages user profiles from conversations.

    Runs inference engines on each message, applies updates based on
    confidence thresholds, and maintains profile cache and storage.
    """

    def __init__(
        self,
        memory: "QdrantMemory | None" = None,
        inference_broker: "InferenceBroker | None" = None,
        storage: ProfileStorage | None = None,
        cache: ProfileCache | None = None,
        tier1_only: bool = False,
        auto_apply_threshold: float = 0.6,
        confirmation_expiry_hours: int = 72,
        max_pending_confirmations: int = 5,
    ):
        """Initialize the profile builder.

        Args:
            memory: Qdrant memory for profile storage.
            inference_broker: Inference broker for LLM calls.
            storage: SQLite storage for operational data.
            cache: In-memory profile cache.
            tier1_only: If True, only use Tier 1 (free) inference.
            auto_apply_threshold: Minimum confidence for auto-apply.
            confirmation_expiry_hours: Hours before pending confirmations expire.
            max_pending_confirmations: Maximum confirmations to queue per user.
        """
        self._memory = memory
        self._storage = storage or ProfileStorage()
        self._cache = cache or ProfileCache()

        self._pipeline = ProfileInferencePipeline(
            inference_broker=inference_broker,
            memory=memory,
            tier1_only=tier1_only,
        )

        self._auto_apply_threshold = auto_apply_threshold
        self._confirmation_expiry_hours = confirmation_expiry_hours
        self._max_pending_confirmations = max_pending_confirmations

        log.info(
            "profile_builder_initialized",
            tier1_only=tier1_only,
            auto_apply_threshold=auto_apply_threshold,
        )

    async def process_message(
        self,
        user_id: str,
        message: str,
        context: str | None = None,
        response_time_ms: int | None = None,
    ) -> list[ProfileUpdate]:
        """Process a message for profile updates.

        This is the main entry point, typically called as a background task
        after generating a response (fire-and-forget).

        Args:
            user_id: The user's ID.
            message: The user's message.
            context: Optional conversation context.
            response_time_ms: Time since last bot response.

        Returns:
            List of updates that were applied or queued.
        """
        try:
            # Run inference pipeline
            updates = await self._pipeline.extract_all(
                message=message,
                context=context,
                response_time_ms=response_time_ms,
            )

            if not updates:
                return []

            # Process updates based on confidence
            applied_updates = await self._process_updates(user_id, updates)

            # Update storage stats
            await self._update_stats(user_id)

            return applied_updates

        except Exception as e:
            log.error("profile_extraction_failed", user_id=user_id, error=str(e))
            return []

    async def _process_updates(
        self,
        user_id: str,
        updates: list[ProfileUpdate],
    ) -> list[ProfileUpdate]:
        """Process updates based on confidence thresholds.

        Args:
            user_id: The user's ID.
            updates: List of proposed updates.

        Returns:
            List of updates that were applied or queued.
        """
        applied: list[ProfileUpdate] = []

        for update in updates:
            # Record tier usage for cost monitoring
            self._storage.record_tier_usage(update.source_tier)

            if update.confidence >= CONFIDENCE_AUTO_APPLY:
                # Apply immediately, no confirmation needed
                await self._apply_update(user_id, update)
                applied.append(update)
                log.debug(
                    "profile_update_auto_applied",
                    user_id=user_id,
                    field=update.field_name,
                    confidence=update.confidence,
                )

            elif update.confidence >= CONFIDENCE_LOG_ONLY:
                # Apply immediately, log for review
                await self._apply_update(user_id, update)
                applied.append(update)
                log.info(
                    "profile_update_applied_for_review",
                    user_id=user_id,
                    field=update.field_name,
                    confidence=update.confidence,
                )

            elif update.confidence >= CONFIDENCE_FLAG_CONFIRM:
                # Apply but flag for confirmation in next heartbeat
                await self._apply_update(user_id, update, needs_confirmation=True)
                applied.append(update)

            elif update.confidence >= CONFIDENCE_QUEUE_CONFIRM:
                # Don't apply, queue for explicit confirmation
                await self._queue_for_confirmation(user_id, update)
                applied.append(update)

            # Below CONFIDENCE_QUEUE_CONFIRM is discarded

        return applied

    async def _apply_update(
        self,
        user_id: str,
        update: ProfileUpdate,
        needs_confirmation: bool = False,
    ) -> None:
        """Apply a profile update.

        Args:
            user_id: The user's ID.
            update: The update to apply.
            needs_confirmation: Whether to flag for later confirmation.
        """
        # Get current value for history
        old_value = await self._get_current_value(user_id, update.profile, update.field_name)

        # Determine the new value based on action
        new_value = self._compute_new_value(old_value, update)

        # Record in update history
        update_id = self._storage.record_update(
            user_id=user_id,
            profile=update.profile,
            field=update.field_name,
            old_value=old_value,
            new_value=new_value,
            confidence=update.confidence,
            source_tier=update.source_tier,
        )

        # Queue for confirmation if needed
        if needs_confirmation:
            pending = self._storage.get_pending_confirmations(user_id)
            if len(pending) < self._max_pending_confirmations:
                expires_at = datetime.now() + timedelta(hours=self._confirmation_expiry_hours)
                self._storage.add_pending_confirmation(
                    user_id=user_id,
                    update_id=update_id,
                    expires_at=expires_at,
                    priority=int(update.confidence * 10),
                )

        # Apply to Qdrant if this is a user profile update
        if update.profile == "user" and self._memory is not None:
            await self._persist_to_qdrant(user_id, update, new_value)

        # Invalidate cache
        self._cache.invalidate(user_id)

    def _compute_new_value(self, old_value: Any, update: ProfileUpdate) -> Any:
        """Compute the new value based on the update action.

        Args:
            old_value: The current value.
            update: The update to apply.

        Returns:
            The new value.
        """
        if update.action == "set":
            return update.value
        elif update.action == "increase":
            current = float(old_value) if old_value is not None else 0.5
            return min(1.0, current + float(update.value or 0.1))
        elif update.action == "decrease":
            current = float(old_value) if old_value is not None else 0.5
            return max(0.0, current - float(update.value or 0.1))
        elif update.action == "append":
            if old_value is None:
                return [update.value]
            elif isinstance(old_value, list):
                return old_value + [update.value]
            else:
                return [old_value, update.value]
        elif update.action == "increment":
            current = int(old_value) if old_value is not None else 0
            return current + int(update.value or 1)
        return update.value

    async def _get_current_value(
        self,
        user_id: str,
        profile: str,
        field: str,
    ) -> Any:
        """Get the current value of a profile field.

        Args:
            user_id: The user's ID.
            profile: 'user' or 'employment'.
            field: The field name.

        Returns:
            The current value, or None.
        """
        if profile == "user":
            entries = self._cache.get_user_profile(user_id)
            if entries:
                for entry in entries:
                    if entry.key == field:
                        return entry.value
        elif profile == "employment":
            emp_profile = self._cache.get_employment_profile(user_id)
            if emp_profile:
                return getattr(emp_profile, field, None)
        return None

    async def _persist_to_qdrant(
        self,
        user_id: str,
        update: ProfileUpdate,
        value: Any,
    ) -> None:
        """Persist a profile update to Qdrant.

        Args:
            user_id: The user's ID.
            update: The update being applied.
            value: The new value.
        """
        if self._memory is None:
            return

        # Determine source based on confidence
        source = ProfileSource.INFERRED if update.confidence < 0.8 else ProfileSource.CONVERSATION

        entry = ProfileEntry.create(
            user_id=user_id,
            category=update.category or ProfileCategory.PREFERENCES,
            key=update.field_name,
            value=value,
            confidence=update.confidence,
            source=source,
        )

        # Store using memory's store_memory method
        # The memory module will handle encryption
        content = f"{entry.key}: {entry.value}"
        await self._memory.store_memory(
            content=content,
            memory_type="profile",
            metadata=entry.to_dict(),
        )

    async def _queue_for_confirmation(
        self,
        user_id: str,
        update: ProfileUpdate,
    ) -> None:
        """Queue an update for user confirmation.

        Args:
            user_id: The user's ID.
            update: The update to queue.
        """
        # Check if we're at the limit
        pending = self._storage.get_pending_confirmations(user_id)
        if len(pending) >= self._max_pending_confirmations:
            log.debug(
                "confirmation_queue_full",
                user_id=user_id,
                max=self._max_pending_confirmations,
            )
            return

        # Record the update but don't apply it
        update_id = self._storage.record_update(
            user_id=user_id,
            profile=update.profile,
            field=update.field_name,
            old_value=None,
            new_value=update.value,
            confidence=update.confidence,
            source_tier=update.source_tier,
        )

        expires_at = datetime.now() + timedelta(hours=self._confirmation_expiry_hours)
        self._storage.add_pending_confirmation(
            user_id=user_id,
            update_id=update_id,
            expires_at=expires_at,
            priority=int(update.confidence * 10),
        )

        log.debug(
            "update_queued_for_confirmation",
            user_id=user_id,
            field=update.field_name,
            expires=expires_at.isoformat(),
        )

    async def _update_stats(self, user_id: str) -> None:
        """Update profile stats in storage.

        Args:
            user_id: The user's ID.
        """
        # Get current entries from cache or load from Qdrant
        entries = self._cache.get_user_profile(user_id)
        total = len(entries) if entries else 0
        high_conf = len([e for e in (entries or []) if e.get_current_confidence() >= 0.7])

        pending = len(self._storage.get_pending_confirmations(user_id))

        current = self._storage.get_stats(user_id)
        version = (current.profile_version + 1) if current else 1

        stats = ProfileStats(
            user_id=user_id,
            profile_version=version,
            last_updated=datetime.now(),
            total_entries=total,
            high_confidence_entries=high_conf,
            pending_confirmations=pending,
        )
        self._storage.upsert_stats(stats)

    # === Public API ===

    async def get_profile_summary(self, user_id: str) -> UserProfileSummary:
        """Get a profile summary for system prompt injection.

        Args:
            user_id: The user's ID.

        Returns:
            UserProfileSummary for the user.
        """
        # Check cache first
        cached = self._cache.get_summary(user_id)
        if cached:
            return cached

        # Load from storage/Qdrant and build summary
        entries = await self._load_profile(user_id)
        summary = self._cache.build_summary(entries)
        self._cache.set_summary(user_id, summary)

        return summary

    async def get_employment_profile(self, user_id: str) -> EmploymentProfileSummary:
        """Get the employment profile summary for a user.

        Args:
            user_id: The user's ID.

        Returns:
            EmploymentProfileSummary for the user.
        """
        # Check cache first
        cached = self._cache.get_employment_profile(user_id)
        if cached:
            return cached

        # Try to get from full profile
        full_profile = await self.get_full_employment_profile(user_id)

        # Create summary from full profile
        summary = EmploymentProfileSummary(
            user_id=user_id,
            primary_roles=full_profile.role.primary_roles,
            formality=full_profile.style.formality,
            verbosity=full_profile.style.verbosity,
            proactivity=full_profile.style.proactivity,
            trust_level=full_profile.trust_level,
            tone=full_profile.style.tone,
            relationship_started=full_profile.relationship_started,
            total_interactions=full_profile.total_interactions,
        )

        self._cache.set_employment_profile(user_id, summary)
        return summary

    async def get_full_employment_profile(self, user_id: str) -> EmploymentProfile:
        """Get the full EmploymentProfile for a user.

        Args:
            user_id: The user's ID.

        Returns:
            Full EmploymentProfile for the user.
        """
        # Check cache first
        cached = self._cache.get_full_employment_profile(user_id)
        if cached:
            return cached

        # Load from Qdrant or create default
        profile = await self._load_employment_profile(user_id)
        if profile is None:
            profile = create_default_profile(user_id)

        self._cache.set_full_employment_profile(user_id, profile)
        return profile

    async def _load_employment_profile(self, user_id: str) -> EmploymentProfile | None:
        """Load employment profile from Qdrant.

        Args:
            user_id: The user's ID.

        Returns:
            EmploymentProfile if found, None otherwise.
        """
        if self._memory is None:
            return None

        try:
            results = await self._memory.search_memories(
                query=f"employment profile {user_id}",
                memory_type="employment_profile",
                limit=1,
            )

            if results:
                metadata = results[0].get("metadata", {})
                if metadata.get("user_id") == user_id:
                    return EmploymentProfile.from_dict(metadata)
        except Exception as e:
            log.error("load_employment_profile_failed", user_id=user_id, error=str(e))

        return None

    async def save_employment_profile(self, profile: EmploymentProfile) -> None:
        """Save an employment profile to Qdrant.

        Args:
            profile: The EmploymentProfile to save.
        """
        if self._memory is None:
            return

        try:
            content = f"employment profile for {profile.user_id}"
            await self._memory.store_memory(
                content=content,
                memory_type="employment_profile",
                metadata=profile.to_dict(),
            )

            # Update cache
            self._cache.set_full_employment_profile(profile.user_id, profile)
            # Invalidate summary cache to force refresh
            self._cache._employment_cache.pop(profile.user_id, None)

            log.debug("employment_profile_saved", user_id=profile.user_id)
        except Exception as e:
            log.error("save_employment_profile_failed", user_id=profile.user_id, error=str(e))

    async def get_relationship_tracker(self, user_id: str) -> RelationshipTracker:
        """Get the RelationshipTracker for a user.

        Args:
            user_id: The user's ID.

        Returns:
            RelationshipTracker for the user.
        """
        # Check cache first
        cached = self._cache.get_relationship_tracker(user_id)
        if cached:
            return cached

        # Get employment profile first
        emp_profile = await self.get_full_employment_profile(user_id)

        # Load from storage or create new
        tracker = await self._load_relationship_tracker(user_id, emp_profile)
        if tracker is None:
            tracker = RelationshipTracker(user_id=user_id, employment_profile=emp_profile)

        self._cache.set_relationship_tracker(user_id, tracker)
        return tracker

    async def _load_relationship_tracker(
        self,
        user_id: str,
        employment_profile: EmploymentProfile,
    ) -> RelationshipTracker | None:
        """Load relationship tracker from Qdrant.

        Args:
            user_id: The user's ID.
            employment_profile: The user's employment profile.

        Returns:
            RelationshipTracker if found, None otherwise.
        """
        if self._memory is None:
            return None

        try:
            results = await self._memory.search_memories(
                query=f"relationship tracker {user_id}",
                memory_type="relationship_tracker",
                limit=1,
            )

            if results:
                metadata = results[0].get("metadata", {})
                if metadata.get("user_id") == user_id:
                    return RelationshipTracker.from_dict(
                        metadata,
                        employment_profile=employment_profile,
                    )
        except Exception as e:
            log.error("load_relationship_tracker_failed", user_id=user_id, error=str(e))

        return None

    async def save_relationship_tracker(self, tracker: RelationshipTracker) -> None:
        """Save a relationship tracker to Qdrant.

        Args:
            tracker: The RelationshipTracker to save.
        """
        if self._memory is None:
            return

        try:
            content = f"relationship tracker for {tracker.user_id}"
            await self._memory.store_memory(
                content=content,
                memory_type="relationship_tracker",
                metadata=tracker.to_dict(),
            )

            # Update cache
            self._cache.set_relationship_tracker(tracker.user_id, tracker)

            log.debug("relationship_tracker_saved", user_id=tracker.user_id)
        except Exception as e:
            log.error("save_relationship_tracker_failed", user_id=tracker.user_id, error=str(e))

    async def record_relationship_event(
        self,
        user_id: str,
        event: RelationshipEvent,
        metadata: dict[str, Any] | None = None,
    ) -> list[ProfileUpdate]:
        """Record a relationship event and get any resulting profile updates.

        Args:
            user_id: The user's ID.
            event: The relationship event.
            metadata: Optional event metadata.

        Returns:
            List of profile updates triggered by the event.
        """
        tracker = await self.get_relationship_tracker(user_id)
        updates = tracker.record_event(event, metadata)

        # Save the tracker if employment profile was modified
        if tracker.employment_profile:
            await self.save_employment_profile(tracker.employment_profile)

        # Save the tracker state
        await self.save_relationship_tracker(tracker)

        return updates

    async def _load_profile(self, user_id: str) -> list[ProfileEntry]:
        """Load profile entries from Qdrant.

        Args:
            user_id: The user's ID.

        Returns:
            List of profile entries.
        """
        if self._memory is None:
            return []

        # Search for profile entries
        results = await self._memory.search_memories(
            query=f"user profile {user_id}",
            memory_type="profile",
            limit=100,
        )

        entries = []
        for result in results:
            metadata = result.get("metadata", {})
            if metadata.get("user_id") == user_id:
                try:
                    entry = ProfileEntry.from_dict(metadata)
                    entries.append(entry)
                except (KeyError, ValueError):
                    pass

        # Cache the loaded entries
        self._cache.set_user_profile(user_id, entries)

        return entries

    async def confirm_update(
        self,
        user_id: str,
        confirmation_id: int,
        confirmed: bool,
    ) -> None:
        """Confirm or reject a pending update.

        Args:
            user_id: The user's ID.
            confirmation_id: The confirmation ID.
            confirmed: Whether to apply the update.
        """
        pending = self._storage.get_pending_confirmations(user_id)
        for p in pending:
            if p.id == confirmation_id:
                self._storage.confirm_update(p.update_id, confirmed)
                self._storage.remove_pending_confirmation(confirmation_id)

                if confirmed:
                    # Get the update and apply it
                    updates = self._storage.get_recent_updates(user_id, limit=100)
                    for u in updates:
                        if u.id == p.update_id:
                            update = ProfileUpdate(
                                profile=u.profile,  # type: ignore[arg-type]
                                field_name=u.field,
                                value=u.new_value,
                                confidence=1.0,  # User confirmed
                                source_tier=u.source_tier,
                            )
                            await self._apply_update(user_id, update)
                            break

                log.info(
                    "update_confirmation_processed",
                    user_id=user_id,
                    confirmation_id=confirmation_id,
                    confirmed=confirmed,
                )
                break

    async def get_pending_confirmations(
        self,
        user_id: str,
    ) -> list[tuple[int, str]]:
        """Get pending confirmations as user-friendly prompts.

        Args:
            user_id: The user's ID.

        Returns:
            List of (confirmation_id, prompt) tuples.
        """
        pending = self._storage.get_pending_confirmations(user_id)
        updates = self._storage.get_recent_updates(user_id, limit=100)

        result = []
        for p in pending:
            for u in updates:
                if u.id == p.update_id:
                    update = ProfileUpdate(
                        profile=u.profile,  # type: ignore[arg-type]
                        field_name=u.field,
                        value=u.new_value,
                        confidence=u.confidence,
                        source_tier=u.source_tier,
                    )
                    result.append((p.id, update.to_confirmation_prompt()))
                    break

        return result

    async def cleanup(self) -> None:
        """Run cleanup tasks.

        - Remove expired confirmations
        - Apply confidence decay to stale entries
        """
        # Cleanup expired confirmations
        cleaned = self._storage.cleanup_expired_confirmations()
        if cleaned > 0:
            log.info("expired_confirmations_cleaned", count=cleaned)

    def get_tier_usage_report(self, days: int = 7) -> dict[str, Any]:
        """Get a report on inference tier usage.

        Args:
            days: Number of days to report on.

        Returns:
            Dictionary with usage statistics.
        """
        usage = self._storage.get_tier_usage(days)
        daily = self._storage.get_daily_tier_usage(days)

        total = sum(usage.values())
        percentages = {
            tier: (count / total * 100) if total > 0 else 0 for tier, count in usage.items()
        }

        return {
            "period_days": days,
            "total_invocations": total,
            "by_tier": usage,
            "percentages": percentages,
            "daily_breakdown": daily,
        }

    async def update_profile_entry(
        self,
        user_id: str,
        category: str,
        key: str,
        value: Any,
        confidence: float = 1.0,
        source: str = "explicit",
    ) -> None:
        """Update or create a profile entry.

        Args:
            user_id: The user's ID.
            category: Profile category (e.g., "identity", "preferences").
            key: The entry key (e.g., "timezone").
            value: The value to set.
            confidence: Confidence score (0.0-1.0).
            source: Source of the update.
        """
        if self._memory is None:
            return

        # Convert string category/source to enums
        try:
            cat_enum = ProfileCategory(category)
        except ValueError:
            cat_enum = ProfileCategory.PREFERENCES

        try:
            src_enum = ProfileSource(source)
        except ValueError:
            src_enum = ProfileSource.EXPLICIT

        entry = ProfileEntry.create(
            user_id=user_id,
            category=cat_enum,
            key=key,
            value=value,
            confidence=confidence,
            source=src_enum,
        )

        # Store using memory's store_memory method
        content = f"{entry.key}: {entry.value}"
        await self._memory.store_memory(
            content=content,
            memory_type="profile",
            metadata=entry.to_dict(),
        )
        log.info("profile_entry_updated", user_id=user_id, category=category, key=key)

    async def delete_profile_entry(
        self,
        user_id: str,
        entry_id: str,
    ) -> bool:
        """Delete a profile entry by ID.

        Args:
            user_id: The user's ID.
            entry_id: The entry ID to delete.

        Returns:
            True if deleted successfully.
        """
        if self._memory:
            return await self._memory.delete_by_id(USER_PROFILES_COLLECTION, entry_id)
        return False

    async def delete_profile_entry_by_key(
        self,
        user_id: str,
        category: str,
        key: str,
    ) -> bool:
        """Delete a profile entry by category and key.

        Args:
            user_id: The user's ID.
            category: Profile category.
            key: The entry key.

        Returns:
            True if deleted successfully.
        """
        if not self._memory:
            return False

        # Find the entry first
        entries = await self._memory.filter_by_field(
            USER_PROFILES_COLLECTION,
            "user_id",
            user_id,
        )

        for entry in entries:
            if entry.get("category") == category and entry.get("key") == key:
                return await self._memory.delete_by_id(
                    USER_PROFILES_COLLECTION,
                    entry["id"],
                )
        return False

    async def get_all_profile_entries(
        self,
        user_id: str,
    ) -> list[dict[str, Any]]:
        """Get all profile entries for a user.

        Args:
            user_id: The user's ID.

        Returns:
            List of profile entries.
        """
        if not self._memory:
            return []

        return await self._memory.filter_by_field(
            USER_PROFILES_COLLECTION,
            "user_id",
            user_id,
        )


async def extract_profile_updates_background(
    builder: ProfileBuilder,
    user_id: str,
    message: str,
    context: str | None = None,
    response_time_ms: int | None = None,
) -> None:
    """Background task to extract profile updates.

    This is the fire-and-forget function to call after generating a response.
    It won't block the response to the user.

    Args:
        builder: The profile builder instance.
        user_id: The user's ID.
        message: The user's message.
        context: Optional conversation context.
        response_time_ms: Time since last bot response.
    """
    try:
        await builder.process_message(
            user_id=user_id,
            message=message,
            context=context,
            response_time_ms=response_time_ms,
        )
    except Exception as e:
        log.error(
            "background_profile_extraction_failed",
            user_id=user_id,
            error=str(e),
        )


def schedule_profile_extraction(
    builder: ProfileBuilder,
    user_id: str,
    message: str,
    context: str | None = None,
    response_time_ms: int | None = None,
) -> asyncio.Task[None]:
    """Schedule profile extraction as a background task.

    Args:
        builder: The profile builder instance.
        user_id: The user's ID.
        message: The user's message.
        context: Optional conversation context.
        response_time_ms: Time since last bot response.

    Returns:
        The asyncio Task for the background extraction.
    """
    return asyncio.create_task(
        extract_profile_updates_background(
            builder=builder,
            user_id=user_id,
            message=message,
            context=context,
            response_time_ms=response_time_ms,
        )
    )
