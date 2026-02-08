"""Unit tests for the relationship tracker module."""

from datetime import datetime, timedelta

import pytest

from zetherion_ai.profile.employment import (
    EmploymentProfile,
    Milestone,
)
from zetherion_ai.profile.models import ProfileUpdate
from zetherion_ai.profile.relationship import (
    MilestoneProgress,
    RelationshipEvent,
    RelationshipState,
    RelationshipTracker,
)


class TestRelationshipState:
    """Tests for RelationshipState dataclass."""

    def test_default_values(self):
        """Test default state values."""
        state = RelationshipState()

        assert state.messages_today == 0
        assert state.messages_this_week == 0
        assert state.streak_days == 0
        assert state.positive_ratio == 0.5
        assert state.correction_ratio == 0.0

    def test_record_message(self):
        """Test recording a message."""
        state = RelationshipState()
        state.record_message()

        assert state.messages_today == 1
        assert state.messages_this_week == 1
        assert state.messages_this_month == 1
        assert state.last_message_at is not None

    def test_record_response_time(self):
        """Test recording response time."""
        state = RelationshipState()

        state.record_response_time(1000)
        assert state.average_response_time_ms == 1000

        # Second sample should use EMA
        state.record_response_time(2000)
        assert state.average_response_time_ms > 1000
        assert state.average_response_time_ms < 2000

    def test_update_streak_first_interaction(self):
        """Test streak update on first interaction."""
        state = RelationshipState()
        state.update_streak()

        assert state.streak_days == 1
        assert state.longest_streak == 1

    def test_reset_daily_counters(self):
        """Test resetting daily counters."""
        state = RelationshipState(messages_today=10)
        state.reset_daily_counters()

        assert state.messages_today == 0

    def test_to_dict_and_from_dict(self):
        """Test serialization and deserialization."""
        state = RelationshipState(
            messages_today=5,
            messages_this_week=20,
            streak_days=3,
            positive_ratio=0.8,
        )

        data = state.to_dict()
        restored = RelationshipState.from_dict(data)

        assert restored.messages_today == state.messages_today
        assert restored.messages_this_week == state.messages_this_week
        assert restored.streak_days == state.streak_days
        assert restored.positive_ratio == state.positive_ratio


class TestMilestoneProgress:
    """Tests for MilestoneProgress dataclass."""

    def test_progress_calculation(self):
        """Test progress percentage calculation."""
        progress = MilestoneProgress(
            milestone=Milestone.HUNDRED_INTERACTIONS,
            current_value=50,
            target_value=100,
        )

        assert progress.progress == 0.5

    def test_progress_capped_at_one(self):
        """Test that progress is capped at 1.0."""
        progress = MilestoneProgress(
            milestone=Milestone.HUNDRED_INTERACTIONS,
            current_value=150,
            target_value=100,
        )

        assert progress.progress == 1.0

    def test_is_achieved_false(self):
        """Test is_achieved when not achieved."""
        progress = MilestoneProgress(
            milestone=Milestone.HUNDRED_INTERACTIONS,
            current_value=50,
            target_value=100,
        )

        assert progress.is_achieved is False

    def test_is_achieved_true_by_value(self):
        """Test is_achieved when reached by value."""
        progress = MilestoneProgress(
            milestone=Milestone.HUNDRED_INTERACTIONS,
            current_value=100,
            target_value=100,
        )

        assert progress.is_achieved is True

    def test_is_achieved_true_by_timestamp(self):
        """Test is_achieved when marked with timestamp."""
        progress = MilestoneProgress(
            milestone=Milestone.FIRST_INTERACTION,
            current_value=0,
            target_value=1,
            achieved_at=datetime.now(),
        )

        assert progress.is_achieved is True


class TestRelationshipEvent:
    """Tests for RelationshipEvent enum."""

    def test_all_events_exist(self):
        """Test that all expected events are defined."""
        assert RelationshipEvent.MESSAGE_RECEIVED.value == "message_received"
        assert RelationshipEvent.TASK_COMPLETED.value == "task_completed"
        assert RelationshipEvent.POSITIVE_FEEDBACK.value == "positive_feedback"
        assert RelationshipEvent.TRUST_EXPRESSED.value == "trust_expressed"
        assert RelationshipEvent.DELEGATION.value == "delegation"


class TestRelationshipTracker:
    """Tests for RelationshipTracker class."""

    @pytest.fixture
    def tracker(self):
        """Create a tracker with an employment profile."""
        profile = EmploymentProfile(user_id="user123", trust_level=0.3)
        return RelationshipTracker(user_id="user123", employment_profile=profile)

    def test_init_creates_milestone_tracking(self, tracker):
        """Test that initialization sets up milestone tracking."""
        assert len(tracker.milestone_progress) > 0
        assert Milestone.FIRST_INTERACTION in tracker.milestone_progress
        assert Milestone.HUNDRED_INTERACTIONS in tracker.milestone_progress

    def test_record_message_received(self, tracker):
        """Test recording message received event."""
        tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)

        assert tracker.state.messages_today == 1
        assert tracker.state.streak_days == 1
        assert Milestone.FIRST_INTERACTION in tracker.get_achieved_milestones()

    def test_record_task_completed_achieves_milestone(self, tracker):
        """Test that task completion achieves milestone."""
        tracker.record_event(RelationshipEvent.TASK_COMPLETED)

        assert Milestone.FIRST_TASK_COMPLETED in tracker.get_achieved_milestones()

    def test_record_task_completed_increases_trust(self, tracker):
        """Test that task completion increases trust."""
        initial_trust = tracker.employment_profile.trust_level

        tracker.record_event(RelationshipEvent.TASK_COMPLETED)

        assert tracker.employment_profile.trust_level > initial_trust

    def test_record_task_failed_decreases_trust(self, tracker):
        """Test that task failure decreases trust."""
        initial_trust = tracker.employment_profile.trust_level

        tracker.record_event(RelationshipEvent.TASK_FAILED)

        assert tracker.employment_profile.trust_level < initial_trust

    def test_record_positive_feedback_increases_trust(self, tracker):
        """Test that positive feedback increases trust."""
        initial_trust = tracker.employment_profile.trust_level

        tracker.record_event(RelationshipEvent.POSITIVE_FEEDBACK)

        assert tracker.employment_profile.trust_level > initial_trust
        assert tracker.state.positive_ratio > 0.5

    def test_record_negative_feedback_decreases_trust(self, tracker):
        """Test that negative feedback decreases trust."""
        initial_trust = tracker.employment_profile.trust_level

        tracker.record_event(RelationshipEvent.NEGATIVE_FEEDBACK)

        assert tracker.employment_profile.trust_level < initial_trust
        assert tracker.state.positive_ratio < 0.5

    def test_record_trust_expressed_achieves_milestone(self, tracker):
        """Test that trust expressed achieves milestone."""
        tracker.record_event(RelationshipEvent.TRUST_EXPRESSED)

        assert Milestone.TRUST_GRANTED in tracker.get_achieved_milestones()

    def test_record_delegation_achieves_milestone(self, tracker):
        """Test that delegation achieves milestone."""
        tracker.record_event(RelationshipEvent.DELEGATION)

        assert Milestone.FIRST_DELEGATION in tracker.get_achieved_milestones()

    def test_record_delegation_increases_proactivity(self, tracker):
        """Test that delegation increases bot proactivity."""
        initial_proactivity = tracker.employment_profile.style.proactivity

        tracker.record_event(RelationshipEvent.DELEGATION)

        assert tracker.employment_profile.style.proactivity > initial_proactivity

    def test_record_boundary_set(self, tracker):
        """Test that boundary set event adds boundary."""
        tracker.record_event(
            RelationshipEvent.BOUNDARY_SET,
            metadata={"boundary": "no financial advice"},
        )

        assert "no financial advice" in tracker.employment_profile.role.boundaries

    def test_record_skill_used(self, tracker):
        """Test that skill used event records usage."""
        tracker.record_event(
            RelationshipEvent.SKILL_USED,
            metadata={"skill_name": "task_manager", "success": True},
        )

        assert "task_manager" in tracker.employment_profile.skill_usage

    def test_get_milestone_progress(self, tracker):
        """Test getting milestone progress."""
        # Record some interactions
        for _ in range(50):
            tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)

        progress = tracker.get_milestone_progress(Milestone.HUNDRED_INTERACTIONS)
        assert progress == 0.5

    def test_get_achieved_milestones(self, tracker):
        """Test getting achieved milestones."""
        tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)
        tracker.record_event(RelationshipEvent.TASK_COMPLETED)

        achieved = tracker.get_achieved_milestones()

        assert Milestone.FIRST_INTERACTION in achieved
        assert Milestone.FIRST_TASK_COMPLETED in achieved

    def test_get_pending_milestones(self, tracker):
        """Test getting pending milestones."""
        tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)

        pending = tracker.get_pending_milestones()

        # Should have pending milestones (hundred interactions, first month, etc.)
        milestone_names = [m for m, _ in pending]
        assert Milestone.HUNDRED_INTERACTIONS in milestone_names

    def test_get_relationship_summary(self, tracker):
        """Test getting relationship summary."""
        tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)
        tracker.record_event(RelationshipEvent.POSITIVE_FEEDBACK)

        summary = tracker.get_relationship_summary()

        assert summary["user_id"] == "user123"
        assert "achieved_milestones" in summary
        assert "pending_milestones" in summary
        assert "trust_level" in summary

    def test_should_increase_proactivity_low_trust(self, tracker):
        """Test proactivity increase check with low trust."""
        # With low trust, should not increase proactivity
        assert tracker.should_increase_proactivity() is False

    def test_should_increase_proactivity_high_delegation(self, tracker):
        """Test proactivity increase with high delegation ratio."""
        tracker.employment_profile.trust_level = 0.5

        # Simulate high delegation ratio
        tracker.state.delegation_ratio = 0.3

        assert tracker.should_increase_proactivity() is True

    def test_should_decrease_proactivity_high_corrections(self, tracker):
        """Test proactivity decrease with high correction ratio."""
        tracker.state.correction_ratio = 0.4

        assert tracker.should_decrease_proactivity() is True

    def test_should_decrease_proactivity_low_positive(self, tracker):
        """Test proactivity decrease with low positive ratio."""
        tracker.state.positive_ratio = 0.2

        assert tracker.should_decrease_proactivity() is True

    def test_get_engagement_level_high(self, tracker):
        """Test high engagement detection."""
        tracker.state.streak_days = 10

        assert tracker.get_engagement_level() == "high"

    def test_get_engagement_level_medium(self, tracker):
        """Test medium engagement detection."""
        tracker.state.messages_this_week = 10

        assert tracker.get_engagement_level() == "medium"

    def test_get_engagement_level_low(self, tracker):
        """Test low engagement detection."""
        tracker.state.messages_this_week = 2
        tracker.state.streak_days = 1

        assert tracker.get_engagement_level() == "low"

    def test_days_since_last_interaction_none(self, tracker):
        """Test days since interaction when never interacted."""
        assert tracker.days_since_last_interaction() is None

    def test_days_since_last_interaction_today(self, tracker):
        """Test days since interaction when interacted today."""
        tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)

        assert tracker.days_since_last_interaction() == 0

    def test_to_dict_and_from_dict(self, tracker):
        """Test serialization and deserialization."""
        tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)
        tracker.record_event(RelationshipEvent.TASK_COMPLETED)

        data = tracker.to_dict()

        # Create new profile for restored tracker
        profile = EmploymentProfile(user_id="user123", trust_level=0.3)
        restored = RelationshipTracker.from_dict(data, employment_profile=profile)

        assert restored.user_id == tracker.user_id
        assert restored.state.messages_today == tracker.state.messages_today

    def test_on_event_decorator(self, tracker):
        """Test event handler registration via decorator."""
        events_received = []

        @tracker.on_event(RelationshipEvent.MESSAGE_RECEIVED)
        def handler(event, metadata):
            events_received.append(event)
            return None

        tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)

        assert len(events_received) == 1
        assert events_received[0] == RelationshipEvent.MESSAGE_RECEIVED

    def test_event_handler_returns_updates(self, tracker):
        """Test that event handlers can return profile updates."""
        from zetherion_ai.profile.models import ProfileUpdate

        @tracker.on_event(RelationshipEvent.POSITIVE_FEEDBACK)
        def handler(event, metadata):
            return ProfileUpdate(
                profile="user",
                field_name="satisfaction",
                action="increase",
                value=0.1,
                confidence=0.7,
                source_tier=1,
            )

        updates = tracker.record_event(RelationshipEvent.POSITIVE_FEEDBACK)

        assert len(updates) >= 1
        update_fields = [u.field_name for u in updates]
        assert "satisfaction" in update_fields


class TestRelationshipTrackerWithoutProfile:
    """Tests for RelationshipTracker without employment profile."""

    def test_tracker_works_without_profile(self):
        """Test that tracker works without employment profile."""
        tracker = RelationshipTracker(user_id="user123")

        # Should not raise
        tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)

        assert tracker.state.messages_today == 1

    def test_proactivity_checks_return_false(self):
        """Test proactivity checks return False without profile."""
        tracker = RelationshipTracker(user_id="user123")

        assert tracker.should_increase_proactivity() is False
        assert tracker.should_decrease_proactivity() is False

    def test_relationship_summary_has_default_trust(self):
        """Test summary has default trust without profile."""
        tracker = RelationshipTracker(user_id="user123")
        summary = tracker.get_relationship_summary()

        assert summary["trust_level"] == 0.3
        assert summary["trust_enum"] == "building"


class TestRelationshipStateExtended:
    """Extended tests for RelationshipState."""

    def test_update_streak_same_day(self):
        """Test streak update on same day does not increment."""
        state = RelationshipState()
        state.update_streak()
        assert state.streak_days == 1

        # Same day, streak should not change
        state.update_streak()
        assert state.streak_days == 1

    def test_update_streak_next_day(self):
        """Test streak update on consecutive day increments."""
        state = RelationshipState()
        yesterday = datetime.now() - timedelta(days=1)
        state.last_streak_check = yesterday
        state.streak_days = 3

        state.update_streak()
        assert state.streak_days == 4
        assert state.longest_streak == 4

    def test_update_streak_broken(self):
        """Test streak resets when days are missed."""
        state = RelationshipState()
        three_days_ago = datetime.now() - timedelta(days=3)
        state.last_streak_check = three_days_ago
        state.streak_days = 10
        state.longest_streak = 10

        state.update_streak()
        assert state.streak_days == 1
        assert state.longest_streak == 10  # Longest streak preserved

    def test_reset_weekly_counters(self):
        """Test resetting weekly counters."""
        state = RelationshipState(messages_this_week=50)
        state.reset_weekly_counters()
        assert state.messages_this_week == 0

    def test_reset_monthly_counters(self):
        """Test resetting monthly counters."""
        state = RelationshipState(messages_this_month=200)
        state.reset_monthly_counters()
        assert state.messages_this_month == 0

    def test_to_dict_with_timestamps(self):
        """Test to_dict with all timestamp fields populated."""
        now = datetime.now()
        state = RelationshipState(
            last_message_at=now,
            last_positive_at=now,
            last_correction_at=now,
            last_streak_check=now,
        )
        data = state.to_dict()
        assert data["last_message_at"] == now.isoformat()
        assert data["last_positive_at"] == now.isoformat()
        assert data["last_correction_at"] == now.isoformat()
        assert data["last_streak_check"] == now.isoformat()

    def test_from_dict_with_timestamps(self):
        """Test from_dict restores timestamp fields."""
        now = datetime.now()
        data = {
            "messages_today": 5,
            "messages_this_week": 25,
            "messages_this_month": 100,
            "last_message_at": now.isoformat(),
            "average_response_time_ms": 500.0,
            "response_time_samples": 10,
            "streak_days": 5,
            "longest_streak": 10,
            "positive_ratio": 0.7,
            "correction_ratio": 0.1,
            "delegation_ratio": 0.2,
            "last_positive_at": now.isoformat(),
            "last_correction_at": now.isoformat(),
            "last_streak_check": now.isoformat(),
        }
        state = RelationshipState.from_dict(data)
        assert state.last_positive_at is not None
        assert state.last_correction_at is not None
        assert state.last_streak_check is not None
        assert state.delegation_ratio == 0.2


class TestMilestoneProgressExtended:
    """Extended tests for MilestoneProgress."""

    def test_progress_zero_target(self):
        """Test progress returns 1.0 when target_value is 0."""
        progress = MilestoneProgress(
            milestone=Milestone.FIRST_INTERACTION,
            current_value=0,
            target_value=0,
        )
        assert progress.progress == 1.0


class TestRelationshipTrackerExtended:
    """Extended tests for RelationshipTracker."""

    @pytest.fixture
    def tracker(self):
        """Create a tracker with an employment profile."""
        profile = EmploymentProfile(user_id="user123", trust_level=0.3)
        return RelationshipTracker(user_id="user123", employment_profile=profile)

    def test_record_correction_received(self, tracker):
        """Test correction received updates correction ratio and milestone."""
        tracker.record_event(RelationshipEvent.CORRECTION_RECEIVED)
        assert tracker.state.correction_ratio > 0.0
        assert tracker.state.last_correction_at is not None
        assert Milestone.CORRECTION_ACCEPTED in tracker.get_achieved_milestones()

    def test_record_positive_feedback_updates_ratio(self, tracker):
        """Test positive feedback updates positive ratio."""
        initial_ratio = tracker.state.positive_ratio
        tracker.record_event(RelationshipEvent.POSITIVE_FEEDBACK)
        assert tracker.state.positive_ratio > initial_ratio
        assert tracker.state.last_positive_at is not None

    def test_record_negative_feedback_updates_ratio(self, tracker):
        """Test negative feedback decreases positive ratio."""
        initial_ratio = tracker.state.positive_ratio
        tracker.record_event(RelationshipEvent.NEGATIVE_FEEDBACK)
        assert tracker.state.positive_ratio < initial_ratio

    def test_record_trust_expressed_increases_trust(self, tracker):
        """Test trust expressed significantly increases trust."""
        initial_trust = tracker.employment_profile.trust_level
        tracker.record_event(RelationshipEvent.TRUST_EXPRESSED)
        assert tracker.employment_profile.trust_level > initial_trust + 0.05

    def test_record_delegation_updates_delegation_ratio(self, tracker):
        """Test delegation updates delegation ratio."""
        tracker.record_event(RelationshipEvent.DELEGATION)
        assert tracker.state.delegation_ratio > 0.0

    def test_record_proactive_action_achieves_milestone(self, tracker):
        """Test proactive action achieves milestone."""
        tracker.record_event(RelationshipEvent.PROACTIVE_ACTION_TAKEN)
        assert Milestone.FIRST_PROACTIVE_ACTION in tracker.get_achieved_milestones()

    def test_record_user_preference_stated(self, tracker):
        """Test user preference stated achieves milestone."""
        tracker.record_event(RelationshipEvent.USER_PREFERENCE_STATED)
        assert Milestone.PREFERENCE_LEARNED in tracker.get_achieved_milestones()

    def test_record_boundary_set_without_boundary(self, tracker):
        """Test boundary set without boundary metadata does nothing."""
        initial_boundaries = list(tracker.employment_profile.role.boundaries)
        tracker.record_event(
            RelationshipEvent.BOUNDARY_SET,
            metadata={},
        )
        assert tracker.employment_profile.role.boundaries == initial_boundaries

    def test_record_skill_used_without_skill_name(self, tracker):
        """Test skill used without skill_name metadata does nothing."""
        initial_usage = dict(tracker.employment_profile.skill_usage)
        tracker.record_event(
            RelationshipEvent.SKILL_USED,
            metadata={"success": True},
        )
        assert tracker.employment_profile.skill_usage == initial_usage

    def test_record_message_time_based_milestones_first_week(self, tracker):
        """Test time-based milestones after 7 days."""
        tracker.employment_profile.relationship_started = datetime.now() - timedelta(days=8)
        tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)
        assert Milestone.FIRST_WEEK in tracker.get_achieved_milestones()

    def test_record_message_time_based_milestones_first_month(self, tracker):
        """Test time-based milestones after 30 days."""
        tracker.employment_profile.relationship_started = datetime.now() - timedelta(days=31)
        tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)
        assert Milestone.FIRST_MONTH in tracker.get_achieved_milestones()

    def test_event_handler_returning_list(self, tracker):
        """Test event handler that returns a list of updates."""

        @tracker.on_event(RelationshipEvent.MESSAGE_RECEIVED)
        def handler(event, metadata):
            return [
                ProfileUpdate(
                    profile="user",
                    field_name="custom_field_1",
                    value="val1",
                    confidence=0.7,
                    source_tier=1,
                ),
                ProfileUpdate(
                    profile="user",
                    field_name="custom_field_2",
                    value="val2",
                    confidence=0.7,
                    source_tier=1,
                ),
            ]

        updates = tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)
        field_names = [u.field_name for u in updates]
        assert "custom_field_1" in field_names
        assert "custom_field_2" in field_names

    def test_event_handler_error_handling(self, tracker):
        """Test that event handler errors are caught and logged."""

        @tracker.on_event(RelationshipEvent.MESSAGE_RECEIVED)
        def broken_handler(event, metadata):
            raise ValueError("Handler failed")

        # Should not raise, error should be caught
        updates = tracker.record_event(RelationshipEvent.MESSAGE_RECEIVED)
        # Updates should still work (no handler result)
        assert isinstance(updates, list)

    def test_advance_milestone_already_achieved(self, tracker):
        """Test advancing an already achieved milestone does nothing."""
        tracker.record_event(RelationshipEvent.TASK_COMPLETED)
        assert Milestone.FIRST_TASK_COMPLETED in tracker.get_achieved_milestones()

        # Advance again should not change anything
        tracker._advance_milestone(Milestone.FIRST_TASK_COMPLETED)
        # Still achieved
        assert Milestone.FIRST_TASK_COMPLETED in tracker.get_achieved_milestones()

    def test_advance_milestone_unknown(self, tracker):
        """Test advancing unknown milestone does nothing."""
        # Create a fake milestone entry by trying to advance a non-existent one
        # Remove a milestone from progress to test the guard
        milestone_to_remove = Milestone.FIRST_INTERACTION
        del tracker.milestone_progress[milestone_to_remove]
        tracker._advance_milestone(milestone_to_remove)
        # Should not raise

    def test_achieve_milestone_unknown(self, tracker):
        """Test achieving unknown milestone returns False."""
        milestone_to_remove = Milestone.FIRST_INTERACTION
        del tracker.milestone_progress[milestone_to_remove]
        result = tracker._achieve_milestone(milestone_to_remove)
        assert result is False

    def test_achieve_milestone_already_achieved(self, tracker):
        """Test achieving an already-achieved milestone returns False."""
        tracker._achieve_milestone(Milestone.FIRST_TASK_COMPLETED)
        result = tracker._achieve_milestone(Milestone.FIRST_TASK_COMPLETED)
        assert result is False

    def test_update_ratio_delegation(self, tracker):
        """Test updating delegation ratio."""
        tracker._update_ratio("delegation", positive=True)
        assert tracker.state.delegation_ratio > 0.0

    def test_update_ratio_correction(self, tracker):
        """Test updating correction ratio."""
        tracker._update_ratio("correction", positive=True)
        assert tracker.state.correction_ratio > 0.0

    def test_update_ratio_positive_false(self, tracker):
        """Test updating positive ratio with negative signal."""
        tracker.state.positive_ratio = 0.8
        tracker._update_ratio("positive", positive=False)
        assert tracker.state.positive_ratio < 0.8

    def test_get_milestone_progress_unknown(self, tracker):
        """Test getting progress for unknown milestone returns 0.0."""
        milestone_to_remove = Milestone.FIRST_INTERACTION
        del tracker.milestone_progress[milestone_to_remove]
        progress = tracker.get_milestone_progress(milestone_to_remove)
        assert progress == 0.0

    def test_should_increase_proactivity_high_positive_and_streak(self, tracker):
        """Test proactivity increase with high positive ratio and streak."""
        tracker.employment_profile.trust_level = 0.5
        tracker.state.delegation_ratio = 0.1  # Not enough delegation
        tracker.state.positive_ratio = 0.8
        tracker.state.streak_days = 7
        assert tracker.should_increase_proactivity() is True

    def test_should_increase_proactivity_low_positive(self, tracker):
        """Test no proactivity increase with low positive ratio."""
        tracker.employment_profile.trust_level = 0.5
        tracker.state.delegation_ratio = 0.1
        tracker.state.positive_ratio = 0.5
        tracker.state.streak_days = 7
        assert tracker.should_increase_proactivity() is False

    def test_should_decrease_proactivity_not_low_not_high(self, tracker):
        """Test no proactivity decrease with moderate ratios."""
        tracker.state.correction_ratio = 0.1
        tracker.state.positive_ratio = 0.5
        assert tracker.should_decrease_proactivity() is False

    def test_detect_style_preferences_quick_responses(self, tracker):
        """Test style detection from quick response times."""
        tracker.state.average_response_time_ms = 2000.0
        tracker.state.response_time_samples = 15
        updates = tracker.detect_style_preferences()
        verbosity_updates = [u for u in updates if u.field_name == "verbosity"]
        assert len(verbosity_updates) == 1
        assert verbosity_updates[0].action == "decrease"

    def test_detect_style_preferences_not_enough_samples(self, tracker):
        """Test no style detection with too few samples."""
        tracker.state.average_response_time_ms = 2000.0
        tracker.state.response_time_samples = 5
        updates = tracker.detect_style_preferences()
        verbosity_updates = [u for u in updates if u.field_name == "verbosity"]
        assert len(verbosity_updates) == 0

    def test_detect_style_preferences_validated(self, tracker):
        """Test style validated when positive ratio is high with many samples."""
        tracker.state.positive_ratio = 0.85
        tracker.state.response_time_samples = 25
        updates = tracker.detect_style_preferences()
        validated_updates = [u for u in updates if u.field_name == "style_validated"]
        assert len(validated_updates) == 1
        assert validated_updates[0].value is True

    def test_detect_style_preferences_zero_response_time(self, tracker):
        """Test no verbosity update when response time is exactly 0."""
        tracker.state.average_response_time_ms = 0.0
        tracker.state.response_time_samples = 15
        updates = tracker.detect_style_preferences()
        verbosity_updates = [u for u in updates if u.field_name == "verbosity"]
        # 0 is not > 0, so should not trigger
        assert len(verbosity_updates) == 0

    def test_get_engagement_level_high_weekly_messages(self, tracker):
        """Test high engagement from weekly message count."""
        tracker.state.streak_days = 3
        tracker.state.messages_this_week = 25
        assert tracker.get_engagement_level() == "high"

    def test_to_dict_includes_milestones(self, tracker):
        """Test to_dict includes milestone progress."""
        tracker.record_event(RelationshipEvent.TASK_COMPLETED)
        data = tracker.to_dict()
        assert "milestone_progress" in data
        assert Milestone.FIRST_TASK_COMPLETED.value in data["milestone_progress"]

    def test_from_dict_restores_milestones(self, tracker):
        """Test from_dict restores milestone progress including achieved_at."""
        tracker.record_event(RelationshipEvent.TASK_COMPLETED)
        data = tracker.to_dict()

        profile = EmploymentProfile(user_id="user123", trust_level=0.3)
        restored = RelationshipTracker.from_dict(data, employment_profile=profile)

        assert restored.milestone_progress[Milestone.FIRST_TASK_COMPLETED].achieved_at is not None

    def test_from_dict_unknown_milestone(self):
        """Test from_dict handles unknown milestone values gracefully."""
        data = {
            "user_id": "user123",
            "state": RelationshipState().to_dict(),
            "milestone_progress": {
                "nonexistent_milestone": {
                    "current_value": 5,
                    "target_value": 10,
                    "achieved_at": None,
                },
            },
        }
        # Should not raise
        tracker = RelationshipTracker.from_dict(data)
        assert tracker.user_id == "user123"

    def test_record_task_completed_without_profile(self):
        """Test task completed without employment profile."""
        tracker = RelationshipTracker(user_id="user123")
        tracker.record_event(RelationshipEvent.TASK_COMPLETED)
        assert Milestone.FIRST_TASK_COMPLETED in tracker.get_achieved_milestones()

    def test_record_task_failed_without_profile(self):
        """Test task failed without employment profile."""
        tracker = RelationshipTracker(user_id="user123")
        # Should not raise
        tracker.record_event(RelationshipEvent.TASK_FAILED)

    def test_record_negative_feedback_without_profile(self):
        """Test negative feedback without employment profile."""
        tracker = RelationshipTracker(user_id="user123")
        tracker.record_event(RelationshipEvent.NEGATIVE_FEEDBACK)
        assert tracker.state.positive_ratio < 0.5
