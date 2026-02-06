"""Relationship tracking and milestone management.

The RelationshipTracker monitors the evolving relationship between the bot
and user, tracks milestones, and triggers co-evolution between User Profile
and Employment Profile.
"""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.profile.employment import (
    EmploymentProfile,
    Milestone,
)
from zetherion_ai.profile.models import ProfileUpdate

log = get_logger("zetherion_ai.profile.relationship")


class RelationshipEvent(Enum):
    """Events that affect the relationship."""

    MESSAGE_RECEIVED = "message_received"
    MESSAGE_SENT = "message_sent"
    TASK_COMPLETED = "task_completed"
    TASK_FAILED = "task_failed"
    SKILL_USED = "skill_used"
    CORRECTION_RECEIVED = "correction_received"
    POSITIVE_FEEDBACK = "positive_feedback"
    NEGATIVE_FEEDBACK = "negative_feedback"
    TRUST_EXPRESSED = "trust_expressed"
    DELEGATION = "delegation"
    PROACTIVE_ACTION_TAKEN = "proactive_action_taken"
    USER_PREFERENCE_STATED = "user_preference_stated"
    BOUNDARY_SET = "boundary_set"


@dataclass
class RelationshipState:
    """Current state of the relationship."""

    # Activity metrics
    messages_today: int = 0
    messages_this_week: int = 0
    messages_this_month: int = 0
    last_message_at: datetime | None = None

    # Engagement metrics
    average_response_time_ms: float = 0.0
    response_time_samples: int = 0
    streak_days: int = 0  # Consecutive days of interaction
    longest_streak: int = 0

    # Sentiment metrics (rolling averages)
    positive_ratio: float = 0.5  # Ratio of positive interactions
    correction_ratio: float = 0.0  # Ratio of corrections
    delegation_ratio: float = 0.0  # Ratio of delegations

    # Timestamps
    last_positive_at: datetime | None = None
    last_correction_at: datetime | None = None
    last_streak_check: datetime | None = None

    def record_message(self) -> None:
        """Record a new message."""
        now = datetime.now()
        self.messages_today += 1
        self.messages_this_week += 1
        self.messages_this_month += 1
        self.last_message_at = now

    def record_response_time(self, time_ms: float) -> None:
        """Record a response time, updating the rolling average."""
        if self.response_time_samples == 0:
            self.average_response_time_ms = time_ms
        else:
            # Exponential moving average
            alpha = 0.1
            self.average_response_time_ms = (
                alpha * time_ms + (1 - alpha) * self.average_response_time_ms
            )
        self.response_time_samples += 1

    def update_streak(self) -> None:
        """Update the interaction streak."""
        now = datetime.now()

        if self.last_streak_check is None:
            self.streak_days = 1
        else:
            days_since = (now.date() - self.last_streak_check.date()).days
            if days_since == 0:
                # Same day, no change
                pass
            elif days_since == 1:
                # Next day, streak continues
                self.streak_days += 1
            else:
                # Streak broken
                self.streak_days = 1

        self.longest_streak = max(self.longest_streak, self.streak_days)
        self.last_streak_check = now

    def reset_daily_counters(self) -> None:
        """Reset daily counters (call at midnight)."""
        self.messages_today = 0

    def reset_weekly_counters(self) -> None:
        """Reset weekly counters."""
        self.messages_this_week = 0

    def reset_monthly_counters(self) -> None:
        """Reset monthly counters."""
        self.messages_this_month = 0

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "messages_today": self.messages_today,
            "messages_this_week": self.messages_this_week,
            "messages_this_month": self.messages_this_month,
            "last_message_at": self.last_message_at.isoformat() if self.last_message_at else None,
            "average_response_time_ms": self.average_response_time_ms,
            "response_time_samples": self.response_time_samples,
            "streak_days": self.streak_days,
            "longest_streak": self.longest_streak,
            "positive_ratio": self.positive_ratio,
            "correction_ratio": self.correction_ratio,
            "delegation_ratio": self.delegation_ratio,
            "last_positive_at": self.last_positive_at.isoformat()
            if self.last_positive_at
            else None,
            "last_correction_at": self.last_correction_at.isoformat()
            if self.last_correction_at
            else None,
            "last_streak_check": self.last_streak_check.isoformat()
            if self.last_streak_check
            else None,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RelationshipState":
        """Create from dictionary."""
        return cls(
            messages_today=data.get("messages_today", 0),
            messages_this_week=data.get("messages_this_week", 0),
            messages_this_month=data.get("messages_this_month", 0),
            last_message_at=datetime.fromisoformat(data["last_message_at"])
            if data.get("last_message_at")
            else None,
            average_response_time_ms=data.get("average_response_time_ms", 0.0),
            response_time_samples=data.get("response_time_samples", 0),
            streak_days=data.get("streak_days", 0),
            longest_streak=data.get("longest_streak", 0),
            positive_ratio=data.get("positive_ratio", 0.5),
            correction_ratio=data.get("correction_ratio", 0.0),
            delegation_ratio=data.get("delegation_ratio", 0.0),
            last_positive_at=datetime.fromisoformat(data["last_positive_at"])
            if data.get("last_positive_at")
            else None,
            last_correction_at=datetime.fromisoformat(data["last_correction_at"])
            if data.get("last_correction_at")
            else None,
            last_streak_check=datetime.fromisoformat(data["last_streak_check"])
            if data.get("last_streak_check")
            else None,
        )


@dataclass
class MilestoneProgress:
    """Tracks progress toward milestones."""

    milestone: Milestone
    current_value: int = 0
    target_value: int = 1
    achieved_at: datetime | None = None

    @property
    def progress(self) -> float:
        """Get progress as a percentage (0.0 to 1.0)."""
        if self.target_value == 0:
            return 1.0
        return min(1.0, self.current_value / self.target_value)

    @property
    def is_achieved(self) -> bool:
        """Check if milestone is achieved."""
        return self.achieved_at is not None or self.current_value >= self.target_value


class RelationshipTracker:
    """Tracks and manages the relationship between bot and user.

    Responsibilities:
    - Record relationship events
    - Track milestone progress
    - Trigger profile co-evolution
    - Detect relationship patterns
    """

    def __init__(
        self,
        user_id: str,
        employment_profile: EmploymentProfile | None = None,
    ) -> None:
        """Initialize the relationship tracker.

        Args:
            user_id: The user's ID
            employment_profile: The bot's employment profile for this user
        """
        self.user_id = user_id
        self.employment_profile = employment_profile
        self.state = RelationshipState()
        self.milestone_progress: dict[Milestone, MilestoneProgress] = {}
        self._event_handlers: dict[RelationshipEvent, list[Callable[..., Any]]] = {}

        # Initialize milestone tracking
        self._init_milestones()

        log.debug("relationship_tracker_init", user_id=user_id)

    def _init_milestones(self) -> None:
        """Initialize milestone progress tracking."""
        # Define milestone targets
        targets = {
            Milestone.FIRST_INTERACTION: 1,
            Milestone.FIRST_TASK_COMPLETED: 1,
            Milestone.FIRST_WEEK: 7,  # days
            Milestone.FIRST_MONTH: 30,  # days
            Milestone.HUNDRED_INTERACTIONS: 100,
            Milestone.FIRST_PROACTIVE_ACTION: 1,
            Milestone.FIRST_DELEGATION: 1,
            Milestone.TRUST_GRANTED: 1,
            Milestone.CORRECTION_ACCEPTED: 1,
            Milestone.PREFERENCE_LEARNED: 1,
        }

        for milestone, target in targets.items():
            self.milestone_progress[milestone] = MilestoneProgress(
                milestone=milestone,
                target_value=target,
            )

    def on_event(
        self, event: RelationshipEvent
    ) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        """Decorator to register an event handler."""

        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            if event not in self._event_handlers:
                self._event_handlers[event] = []
            self._event_handlers[event].append(func)
            return func

        return decorator

    def record_event(
        self,
        event: RelationshipEvent,
        metadata: dict[str, Any] | None = None,
    ) -> list[ProfileUpdate]:
        """Record a relationship event and return any profile updates.

        Args:
            event: The type of event that occurred
            metadata: Optional event-specific metadata

        Returns:
            List of profile updates triggered by this event
        """
        metadata = metadata or {}
        updates: list[ProfileUpdate] = []

        log.debug("relationship_event", event_type=event.value, metadata=metadata)

        # Update state based on event
        if event == RelationshipEvent.MESSAGE_RECEIVED:
            self.state.record_message()
            self.state.update_streak()
            self._advance_milestone(Milestone.FIRST_INTERACTION)
            self._advance_milestone(Milestone.HUNDRED_INTERACTIONS)

            # Check time-based milestones
            if self.employment_profile:
                days = (datetime.now() - self.employment_profile.relationship_started).days
                if days >= 7:
                    self._achieve_milestone(Milestone.FIRST_WEEK)
                if days >= 30:
                    self._achieve_milestone(Milestone.FIRST_MONTH)

        elif event == RelationshipEvent.TASK_COMPLETED:
            self._achieve_milestone(Milestone.FIRST_TASK_COMPLETED)
            if self.employment_profile:
                self.employment_profile.adjust_trust(0.01)

        elif event == RelationshipEvent.TASK_FAILED:
            if self.employment_profile:
                self.employment_profile.adjust_trust(-0.02)

        elif event == RelationshipEvent.CORRECTION_RECEIVED:
            self._update_ratio("correction", positive=True)
            self._achieve_milestone(Milestone.CORRECTION_ACCEPTED)
            self.state.last_correction_at = datetime.now()

        elif event == RelationshipEvent.POSITIVE_FEEDBACK:
            self._update_ratio("positive", positive=True)
            self.state.last_positive_at = datetime.now()
            if self.employment_profile:
                self.employment_profile.adjust_trust(0.02)

        elif event == RelationshipEvent.NEGATIVE_FEEDBACK:
            self._update_ratio("positive", positive=False)
            if self.employment_profile:
                self.employment_profile.adjust_trust(-0.01)

        elif event == RelationshipEvent.TRUST_EXPRESSED:
            self._achieve_milestone(Milestone.TRUST_GRANTED)
            if self.employment_profile:
                self.employment_profile.adjust_trust(0.1)

        elif event == RelationshipEvent.DELEGATION:
            self._achieve_milestone(Milestone.FIRST_DELEGATION)
            self._update_ratio("delegation", positive=True)
            if self.employment_profile:
                self.employment_profile.adjust_trust(0.05)
                # Increase proactivity when user delegates
                self.employment_profile.style.adjust("proactivity", 0.05)

        elif event == RelationshipEvent.PROACTIVE_ACTION_TAKEN:
            self._achieve_milestone(Milestone.FIRST_PROACTIVE_ACTION)

        elif event == RelationshipEvent.USER_PREFERENCE_STATED:
            self._achieve_milestone(Milestone.PREFERENCE_LEARNED)

        elif event == RelationshipEvent.BOUNDARY_SET:
            boundary = metadata.get("boundary")
            if boundary and self.employment_profile:
                self.employment_profile.role.add_boundary(boundary)

        elif event == RelationshipEvent.SKILL_USED:
            skill_name = metadata.get("skill_name")
            success = metadata.get("success", True)
            if skill_name and self.employment_profile:
                self.employment_profile.record_skill_use(skill_name, success)

        # Trigger registered handlers
        if event in self._event_handlers:
            for handler in self._event_handlers[event]:
                try:
                    result = handler(event, metadata)
                    if isinstance(result, list):
                        updates.extend(result)
                    elif isinstance(result, ProfileUpdate):
                        updates.append(result)
                except Exception as e:
                    log.error("event_handler_error", event_type=event.value, error=str(e))

        return updates

    def _advance_milestone(self, milestone: Milestone, amount: int = 1) -> None:
        """Advance progress on a milestone."""
        if milestone not in self.milestone_progress:
            return

        progress = self.milestone_progress[milestone]
        if progress.is_achieved:
            return

        progress.current_value += amount

        if progress.current_value >= progress.target_value:
            self._achieve_milestone(milestone)

    def _achieve_milestone(self, milestone: Milestone) -> bool:
        """Mark a milestone as achieved.

        Returns True if this was a new achievement.
        """
        if milestone not in self.milestone_progress:
            return False

        progress = self.milestone_progress[milestone]
        if progress.achieved_at is not None:
            return False

        progress.achieved_at = datetime.now()
        progress.current_value = progress.target_value

        # Update employment profile
        if self.employment_profile:
            self.employment_profile.achieve_milestone(milestone)

        log.info("milestone_achieved", milestone=milestone.value, user_id=self.user_id)
        return True

    def _update_ratio(self, ratio_type: str, positive: bool) -> None:
        """Update a rolling ratio (positive, correction, delegation)."""
        alpha = 0.1  # Weight for new observation

        if ratio_type == "positive":
            new_val = 1.0 if positive else 0.0
            self.state.positive_ratio = alpha * new_val + (1 - alpha) * self.state.positive_ratio
        elif ratio_type == "correction":
            new_val = 1.0 if positive else 0.0
            old_val = self.state.correction_ratio
            self.state.correction_ratio = alpha * new_val + (1 - alpha) * old_val
        elif ratio_type == "delegation":
            new_val = 1.0 if positive else 0.0
            old_val = self.state.delegation_ratio
            self.state.delegation_ratio = alpha * new_val + (1 - alpha) * old_val

    def get_milestone_progress(self, milestone: Milestone) -> float:
        """Get progress on a milestone (0.0 to 1.0)."""
        if milestone in self.milestone_progress:
            return self.milestone_progress[milestone].progress
        return 0.0

    def get_achieved_milestones(self) -> list[Milestone]:
        """Get list of achieved milestones."""
        return [m.milestone for m in self.milestone_progress.values() if m.is_achieved]

    def get_pending_milestones(self) -> list[tuple[Milestone, float]]:
        """Get list of pending milestones with their progress."""
        return [
            (m.milestone, m.progress) for m in self.milestone_progress.values() if not m.is_achieved
        ]

    def get_relationship_summary(self) -> dict[str, Any]:
        """Get a summary of the relationship state."""
        return {
            "user_id": self.user_id,
            "state": self.state.to_dict(),
            "achieved_milestones": [m.value for m in self.get_achieved_milestones()],
            "pending_milestones": [
                {"milestone": m.value, "progress": p} for m, p in self.get_pending_milestones()
            ],
            "trust_level": self.employment_profile.trust_level if self.employment_profile else 0.3,
            "trust_enum": self.employment_profile.trust_enum.value
            if self.employment_profile
            else "building",
        }

    def should_increase_proactivity(self) -> bool:
        """Determine if the bot should become more proactive.

        Based on:
        - High delegation ratio
        - Positive feedback ratio
        - Trust level
        - Streak length
        """
        if not self.employment_profile:
            return False

        # Need established trust
        if self.employment_profile.trust_level < 0.4:
            return False

        # Check delegation signals
        if self.state.delegation_ratio > 0.2:
            return True

        # High positive ratio with good streak
        return self.state.positive_ratio > 0.7 and self.state.streak_days >= 5

    def should_decrease_proactivity(self) -> bool:
        """Determine if the bot should become less proactive.

        Based on:
        - High correction ratio
        - Low positive ratio
        - Recent negative feedback
        """
        if not self.employment_profile:
            return False

        # High correction rate
        if self.state.correction_ratio > 0.3:
            return True

        # Low positive ratio
        return self.state.positive_ratio < 0.3

    def detect_style_preferences(self) -> list[ProfileUpdate]:
        """Detect user style preferences from relationship patterns.

        Returns profile updates based on detected patterns.
        """
        updates: list[ProfileUpdate] = []

        # Quick responses suggest concise preferences
        is_quick = 0 < self.state.average_response_time_ms < 3000
        has_enough_samples = self.state.response_time_samples >= 10
        if is_quick and has_enough_samples:
            updates.append(
                ProfileUpdate(
                    profile="employment",
                    field_name="verbosity",
                    action="decrease",
                    value=0.05,
                    confidence=0.6,
                    source_tier=1,
                )
            )

        # High positive ratio with established relationship suggests current style is good
        if self.state.positive_ratio > 0.8 and self.state.response_time_samples >= 20:
            updates.append(
                ProfileUpdate(
                    profile="employment",
                    field_name="style_validated",
                    action="set",
                    value=True,
                    confidence=0.8,
                    source_tier=1,
                )
            )

        return updates

    def get_engagement_level(self) -> str:
        """Determine the current engagement level.

        Returns:
            "high", "medium", or "low"
        """
        # Check streak
        if self.state.streak_days >= 7:
            return "high"

        # Check recent activity
        if self.state.messages_this_week >= 20:
            return "high"
        elif self.state.messages_this_week >= 5:
            return "medium"

        return "low"

    def days_since_last_interaction(self) -> int | None:
        """Get days since last interaction, or None if never."""
        if self.state.last_message_at is None:
            return None
        delta = datetime.now() - self.state.last_message_at
        return delta.days

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "user_id": self.user_id,
            "state": self.state.to_dict(),
            "milestone_progress": {
                m.value: {
                    "current_value": p.current_value,
                    "target_value": p.target_value,
                    "achieved_at": p.achieved_at.isoformat() if p.achieved_at else None,
                }
                for m, p in self.milestone_progress.items()
            },
        }

    @classmethod
    def from_dict(
        cls,
        data: dict[str, Any],
        employment_profile: EmploymentProfile | None = None,
    ) -> "RelationshipTracker":
        """Create from dictionary."""
        tracker = cls(
            user_id=data.get("user_id", ""),
            employment_profile=employment_profile,
        )

        # Restore state
        if "state" in data:
            tracker.state = RelationshipState.from_dict(data["state"])

        # Restore milestone progress
        for milestone_value, progress_data in data.get("milestone_progress", {}).items():
            try:
                milestone = Milestone(milestone_value)
                if milestone in tracker.milestone_progress:
                    tracker.milestone_progress[milestone].current_value = progress_data.get(
                        "current_value", 0
                    )
                    tracker.milestone_progress[milestone].target_value = progress_data.get(
                        "target_value", 1
                    )
                    achieved_at = progress_data.get("achieved_at")
                    if achieved_at:
                        tracker.milestone_progress[milestone].achieved_at = datetime.fromisoformat(
                            achieved_at
                        )
            except ValueError:
                log.warning("unknown_milestone", milestone=milestone_value)

        return tracker
