"""Unit tests for the personal action control framework."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zetherion_ai.personal.actions import (
    AUTO_TRUST_THRESHOLD,
    OUTCOME_DELTAS,
    TRUST_APPROVAL_DELTA,
    TRUST_CAP,
    TRUST_FLOOR,
    TRUST_MAJOR_EDIT_DELTA,
    TRUST_MINOR_EDIT_DELTA,
    TRUST_REJECTION_DELTA,
    ActionController,
    ActionDecision,
    ActionOutcome,
)
from zetherion_ai.personal.models import (
    PersonalPolicy,
    PolicyDomain,
    PolicyMode,
)
from zetherion_ai.personal.storage import PersonalStorage

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


def _make_storage() -> AsyncMock:
    """Create a mock PersonalStorage with default return values."""
    storage = AsyncMock(spec=PersonalStorage)
    storage.get_policy = AsyncMock(return_value=None)
    storage.update_trust_score = AsyncMock(return_value=0.5)
    storage.upsert_policy = AsyncMock(return_value=1)
    storage.reset_domain_trust = AsyncMock(return_value=3)
    return storage


def _make_policy(
    mode: str = "ask",
    trust_score: float = 0.0,
    user_id: int = 12345,
    domain: PolicyDomain = PolicyDomain.EMAIL,
    action: str = "auto_reply_ack",
) -> PersonalPolicy:
    """Create a test PersonalPolicy."""
    return PersonalPolicy(
        user_id=user_id,
        domain=domain,
        action=action,
        mode=PolicyMode(mode),
        trust_score=trust_score,
    )


# ---------------------------------------------------------------------------
# Constants tests
# ---------------------------------------------------------------------------


class TestConstants:
    """Tests for trust evolution constants."""

    def test_trust_deltas_have_correct_values(self):
        """Verify all trust delta constants have expected values."""
        assert TRUST_APPROVAL_DELTA == 0.05
        assert TRUST_MINOR_EDIT_DELTA == -0.02
        assert TRUST_MAJOR_EDIT_DELTA == -0.10
        assert TRUST_REJECTION_DELTA == -0.20

    def test_outcome_deltas_maps_all_action_outcomes(self):
        """Verify OUTCOME_DELTAS contains all ActionOutcome values."""
        assert len(OUTCOME_DELTAS) == 4
        assert ActionOutcome.APPROVED in OUTCOME_DELTAS
        assert ActionOutcome.MINOR_EDIT in OUTCOME_DELTAS
        assert ActionOutcome.MAJOR_EDIT in OUTCOME_DELTAS
        assert ActionOutcome.REJECTED in OUTCOME_DELTAS

        assert OUTCOME_DELTAS[ActionOutcome.APPROVED] == 0.05
        assert OUTCOME_DELTAS[ActionOutcome.MINOR_EDIT] == -0.02
        assert OUTCOME_DELTAS[ActionOutcome.MAJOR_EDIT] == -0.10
        assert OUTCOME_DELTAS[ActionOutcome.REJECTED] == -0.20

    def test_auto_trust_threshold_is_correct(self):
        """Verify AUTO_TRUST_THRESHOLD is 0.85."""
        assert AUTO_TRUST_THRESHOLD == 0.85

    def test_trust_cap_and_floor(self):
        """Verify trust cap and floor constants."""
        assert TRUST_CAP == 0.95
        assert TRUST_FLOOR == 0.0


# ---------------------------------------------------------------------------
# ActionOutcome enum tests
# ---------------------------------------------------------------------------


class TestActionOutcome:
    """Tests for ActionOutcome enum."""

    def test_all_four_outcomes_exist(self):
        """Verify all four outcome enum members exist."""
        assert hasattr(ActionOutcome, "APPROVED")
        assert hasattr(ActionOutcome, "MINOR_EDIT")
        assert hasattr(ActionOutcome, "MAJOR_EDIT")
        assert hasattr(ActionOutcome, "REJECTED")

    def test_string_values_match_expected(self):
        """Verify enum string values match lowercase names."""
        assert ActionOutcome.APPROVED.value == "approved"
        assert ActionOutcome.MINOR_EDIT.value == "minor_edit"
        assert ActionOutcome.MAJOR_EDIT.value == "major_edit"
        assert ActionOutcome.REJECTED.value == "rejected"

    def test_outcome_is_str_enum(self):
        """Verify ActionOutcome values are strings."""
        assert isinstance(ActionOutcome.APPROVED, str)
        assert isinstance(ActionOutcome.MINOR_EDIT, str)


# ---------------------------------------------------------------------------
# ActionDecision dataclass tests
# ---------------------------------------------------------------------------


class TestActionDecision:
    """Tests for ActionDecision dataclass."""

    def test_creation_with_all_fields(self):
        """Verify ActionDecision can be created with all fields."""
        decision = ActionDecision(
            domain="email",
            action="auto_reply_ack",
            mode="auto",
            trust_score=0.9,
            should_execute=True,
            reason="High trust",
        )

        assert decision.domain == "email"
        assert decision.action == "auto_reply_ack"
        assert decision.mode == "auto"
        assert decision.trust_score == 0.9
        assert decision.should_execute is True
        assert decision.reason == "High trust"

    def test_should_execute_true(self):
        """Verify should_execute can be True."""
        decision = ActionDecision(
            domain="tasks",
            action="create_task",
            mode="auto",
            trust_score=0.95,
            should_execute=True,
            reason="Auto mode",
        )

        assert decision.should_execute is True

    def test_should_execute_false(self):
        """Verify should_execute can be False."""
        decision = ActionDecision(
            domain="email",
            action="send_email",
            mode="ask",
            trust_score=0.3,
            should_execute=False,
            reason="Ask mode",
        )

        assert decision.should_execute is False

    def test_dataclass_attributes_accessible(self):
        """Verify all dataclass attributes are accessible."""
        decision = ActionDecision(
            domain="calendar",
            action="schedule_meeting",
            mode="draft",
            trust_score=0.75,
            should_execute=False,
            reason="Trust below threshold",
        )

        assert hasattr(decision, "domain")
        assert hasattr(decision, "action")
        assert hasattr(decision, "mode")
        assert hasattr(decision, "trust_score")
        assert hasattr(decision, "should_execute")
        assert hasattr(decision, "reason")


# ---------------------------------------------------------------------------
# ActionController.decide() tests
# ---------------------------------------------------------------------------


class TestActionControllerDecide:
    """Tests for ActionController.decide() method."""

    @pytest.mark.asyncio
    async def test_no_policy_defaults_to_ask_mode(self):
        """When no policy exists, should default to 'ask' mode."""
        storage = _make_storage()
        storage.get_policy.return_value = None
        controller = ActionController(storage)

        decision = await controller.decide(12345, "email", "auto_reply")

        assert decision.mode == PolicyMode.ASK.value
        assert decision.should_execute is False
        assert decision.trust_score == 0.0
        assert "No policy configured" in decision.reason

    @pytest.mark.asyncio
    async def test_no_policy_returns_correct_domain_and_action(self):
        """Verify decision contains the correct domain and action."""
        storage = _make_storage()
        storage.get_policy.return_value = None
        controller = ActionController(storage)

        decision = await controller.decide(12345, "tasks", "create_task")

        assert decision.domain == "tasks"
        assert decision.action == "create_task"

    @pytest.mark.asyncio
    async def test_policy_mode_never_should_not_execute(self):
        """When policy mode is 'never', should_execute must be False."""
        storage = _make_storage()
        policy = _make_policy(mode="never", trust_score=0.8)
        storage.get_policy.return_value = policy
        controller = ActionController(storage)

        decision = await controller.decide(12345, "email", "auto_reply_ack")

        assert decision.mode == PolicyMode.NEVER.value
        assert decision.should_execute is False
        assert "Blocked by policy" in decision.reason

    @pytest.mark.asyncio
    async def test_policy_mode_auto_should_execute(self):
        """When policy mode is 'auto', should_execute must be True."""
        storage = _make_storage()
        policy = _make_policy(mode="auto", trust_score=0.5)
        storage.get_policy.return_value = policy
        controller = ActionController(storage)

        decision = await controller.decide(12345, "email", "auto_reply_ack")

        assert decision.mode == PolicyMode.AUTO.value
        assert decision.should_execute is True
        assert "Auto-execute (mode=auto)" in decision.reason

    @pytest.mark.asyncio
    async def test_policy_mode_draft_low_trust_should_not_execute(self):
        """Draft mode with trust < threshold should not execute."""
        storage = _make_storage()
        policy = _make_policy(mode="draft", trust_score=0.7)
        storage.get_policy.return_value = policy
        controller = ActionController(storage)

        decision = await controller.decide(12345, "email", "auto_reply_ack")

        assert decision.mode == PolicyMode.DRAFT.value
        assert decision.should_execute is False
        assert decision.trust_score == 0.7
        assert "Draft for review" in decision.reason

    @pytest.mark.asyncio
    async def test_policy_mode_draft_high_trust_should_execute(self):
        """Draft mode with trust >= threshold should execute."""
        storage = _make_storage()
        policy = _make_policy(mode="draft", trust_score=0.9)
        storage.get_policy.return_value = policy
        controller = ActionController(storage)

        decision = await controller.decide(12345, "email", "auto_reply_ack")

        assert decision.mode == PolicyMode.DRAFT.value
        assert decision.should_execute is True
        assert decision.trust_score == 0.9
        assert "Auto-execute" in decision.reason

    @pytest.mark.asyncio
    async def test_policy_mode_draft_exactly_at_threshold_should_execute(
        self,
    ):
        """Draft mode with trust exactly at 0.85 should execute."""
        storage = _make_storage()
        policy = _make_policy(mode="draft", trust_score=0.85)
        storage.get_policy.return_value = policy
        controller = ActionController(storage)

        decision = await controller.decide(12345, "email", "auto_reply_ack")

        assert decision.mode == PolicyMode.DRAFT.value
        assert decision.should_execute is True
        assert decision.trust_score == 0.85

    @pytest.mark.asyncio
    async def test_policy_mode_draft_just_below_threshold_should_not_execute(
        self,
    ):
        """Draft mode with trust just below 0.85 should not execute."""
        storage = _make_storage()
        policy = _make_policy(mode="draft", trust_score=0.84)
        storage.get_policy.return_value = policy
        controller = ActionController(storage)

        decision = await controller.decide(12345, "email", "auto_reply_ack")

        assert decision.mode == PolicyMode.DRAFT.value
        assert decision.should_execute is False
        assert decision.trust_score == 0.84

    @pytest.mark.asyncio
    async def test_policy_mode_ask_should_not_execute(self):
        """When policy mode is 'ask', should_execute must be False."""
        storage = _make_storage()
        policy = _make_policy(mode="ask", trust_score=0.6)
        storage.get_policy.return_value = policy
        controller = ActionController(storage)

        decision = await controller.decide(12345, "email", "auto_reply_ack")

        assert decision.mode == PolicyMode.ASK.value
        assert decision.should_execute is False
        assert "Waiting for user approval" in decision.reason

    @pytest.mark.asyncio
    async def test_decision_includes_trust_score_from_policy(self):
        """Verify decision includes the trust score from policy."""
        storage = _make_storage()
        policy = _make_policy(mode="ask", trust_score=0.42)
        storage.get_policy.return_value = policy
        controller = ActionController(storage)

        decision = await controller.decide(12345, "email", "auto_reply_ack")

        assert decision.trust_score == 0.42

    @pytest.mark.asyncio
    async def test_storage_get_policy_called_with_correct_args(self):
        """Verify storage.get_policy is called with correct arguments."""
        storage = _make_storage()
        controller = ActionController(storage)

        await controller.decide(12345, "tasks", "create_reminder")

        storage.get_policy.assert_awaited_once_with(12345, "tasks", "create_reminder")

    @pytest.mark.asyncio
    async def test_different_domains_handled_correctly(self):
        """Test decisions work across different policy domains."""
        storage = _make_storage()
        controller = ActionController(storage)

        calendar_policy = _make_policy(mode="auto", trust_score=0.9, domain=PolicyDomain.CALENDAR)
        storage.get_policy.return_value = calendar_policy

        decision = await controller.decide(12345, "calendar", "schedule_meeting")

        assert decision.domain == "calendar"
        assert decision.action == "schedule_meeting"
        assert decision.should_execute is True


# ---------------------------------------------------------------------------
# ActionController.record_outcome() tests
# ---------------------------------------------------------------------------


class TestActionControllerRecordOutcome:
    """Tests for ActionController.record_outcome() method."""

    @pytest.mark.asyncio
    async def test_approved_outcome_calls_update_trust_with_correct_delta(
        self,
    ):
        """Approved outcome should call update_trust_score with +0.05."""
        storage = _make_storage()
        storage.update_trust_score.return_value = 0.55
        controller = ActionController(storage)

        await controller.record_outcome(12345, "email", "auto_reply", ActionOutcome.APPROVED)

        storage.update_trust_score.assert_awaited_once_with(12345, "email", "auto_reply", 0.05)

    @pytest.mark.asyncio
    async def test_minor_edit_outcome_calls_update_trust_with_correct_delta(
        self,
    ):
        """Minor edit outcome should call update_trust_score with -0.02."""
        storage = _make_storage()
        storage.update_trust_score.return_value = 0.48
        controller = ActionController(storage)

        await controller.record_outcome(12345, "email", "auto_reply", ActionOutcome.MINOR_EDIT)

        storage.update_trust_score.assert_awaited_once_with(12345, "email", "auto_reply", -0.02)

    @pytest.mark.asyncio
    async def test_major_edit_outcome_calls_update_trust_with_correct_delta(
        self,
    ):
        """Major edit outcome should call update_trust_score with -0.10."""
        storage = _make_storage()
        storage.update_trust_score.return_value = 0.40
        controller = ActionController(storage)

        await controller.record_outcome(12345, "email", "auto_reply", ActionOutcome.MAJOR_EDIT)

        storage.update_trust_score.assert_awaited_once_with(12345, "email", "auto_reply", -0.10)

    @pytest.mark.asyncio
    async def test_rejected_outcome_calls_update_trust_with_correct_delta(
        self,
    ):
        """Rejected outcome should call update_trust_score with -0.20."""
        storage = _make_storage()
        storage.update_trust_score.return_value = 0.30
        controller = ActionController(storage)

        await controller.record_outcome(12345, "email", "auto_reply", ActionOutcome.REJECTED)

        storage.update_trust_score.assert_awaited_once_with(12345, "email", "auto_reply", -0.20)

    @pytest.mark.asyncio
    async def test_returns_new_score_from_storage(self):
        """Verify record_outcome returns the new score from storage."""
        storage = _make_storage()
        storage.update_trust_score.return_value = 0.65
        controller = ActionController(storage)

        result = await controller.record_outcome(
            12345, "email", "auto_reply", ActionOutcome.APPROVED
        )

        assert result == 0.65

    @pytest.mark.asyncio
    async def test_policy_does_not_exist_returns_none(self):
        """When storage returns None, record_outcome should return None."""
        storage = _make_storage()
        storage.update_trust_score.return_value = None
        controller = ActionController(storage)

        result = await controller.record_outcome(
            12345, "email", "nonexistent_action", ActionOutcome.APPROVED
        )

        assert result is None

    @pytest.mark.asyncio
    async def test_record_outcome_with_different_user_ids(self):
        """Verify record_outcome passes user_id correctly to storage."""
        storage = _make_storage()
        storage.update_trust_score.return_value = 0.75
        controller = ActionController(storage)

        await controller.record_outcome(99999, "tasks", "create_task", ActionOutcome.APPROVED)

        storage.update_trust_score.assert_awaited_once_with(99999, "tasks", "create_task", 0.05)


# ---------------------------------------------------------------------------
# ActionController.set_mode() tests
# ---------------------------------------------------------------------------


class TestActionControllerSetMode:
    """Tests for ActionController.set_mode() method."""

    @pytest.mark.asyncio
    async def test_creates_policy_with_correct_fields(self):
        """Verify set_mode creates a policy with correct fields."""
        storage = _make_storage()
        storage.upsert_policy.return_value = 42
        controller = ActionController(storage)

        policy_id = await controller.set_mode(12345, "email", "auto_reply", PolicyMode.AUTO)

        assert policy_id == 42
        storage.upsert_policy.assert_awaited_once()

        call_args = storage.upsert_policy.call_args[0][0]
        assert isinstance(call_args, PersonalPolicy)
        assert call_args.user_id == 12345
        assert call_args.action == "auto_reply"
        assert call_args.mode == PolicyMode.AUTO

    @pytest.mark.asyncio
    async def test_uses_policy_domain_enum_for_domain(self):
        """Verify set_mode converts domain string to PolicyDomain enum."""
        storage = _make_storage()
        storage.upsert_policy.return_value = 100
        controller = ActionController(storage)

        await controller.set_mode(12345, "calendar", "schedule_meeting", PolicyMode.DRAFT)

        call_args = storage.upsert_policy.call_args[0][0]
        assert call_args.domain == PolicyDomain.CALENDAR
        assert isinstance(call_args.domain, PolicyDomain)

    @pytest.mark.asyncio
    async def test_delegates_to_storage_upsert_policy(self):
        """Verify set_mode delegates to storage.upsert_policy."""
        storage = _make_storage()
        storage.upsert_policy.return_value = 7
        controller = ActionController(storage)

        result = await controller.set_mode(12345, "tasks", "create_task", PolicyMode.ASK)

        assert result == 7
        storage.upsert_policy.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_set_mode_with_all_policy_modes(self):
        """Test set_mode works with all PolicyMode enum values."""
        storage = _make_storage()
        controller = ActionController(storage)

        for mode in [
            PolicyMode.AUTO,
            PolicyMode.DRAFT,
            PolicyMode.ASK,
            PolicyMode.NEVER,
        ]:
            storage.upsert_policy.reset_mock()
            storage.upsert_policy.return_value = 1

            await controller.set_mode(12345, "email", "test_action", mode)

            call_args = storage.upsert_policy.call_args[0][0]
            assert call_args.mode == mode

    @pytest.mark.asyncio
    async def test_returns_policy_id_from_storage(self):
        """Verify set_mode returns the policy ID from storage."""
        storage = _make_storage()
        storage.upsert_policy.return_value = 999
        controller = ActionController(storage)

        policy_id = await controller.set_mode(12345, "general", "custom_action", PolicyMode.DRAFT)

        assert policy_id == 999


# ---------------------------------------------------------------------------
# ActionController.reset_domain() tests
# ---------------------------------------------------------------------------


class TestActionControllerResetDomain:
    """Tests for ActionController.reset_domain() method."""

    @pytest.mark.asyncio
    async def test_delegates_to_storage_reset_domain_trust(self):
        """Verify reset_domain delegates to storage.reset_domain_trust."""
        storage = _make_storage()
        storage.reset_domain_trust.return_value = 5
        controller = ActionController(storage)

        count = await controller.reset_domain(12345, "email")

        assert count == 5
        storage.reset_domain_trust.assert_awaited_once_with(12345, "email")

    @pytest.mark.asyncio
    async def test_returns_count_from_storage(self):
        """Verify reset_domain returns the count from storage."""
        storage = _make_storage()
        storage.reset_domain_trust.return_value = 12
        controller = ActionController(storage)

        result = await controller.reset_domain(99999, "tasks")

        assert result == 12

    @pytest.mark.asyncio
    async def test_reset_domain_with_different_domains(self):
        """Test reset_domain works with different domain strings."""
        storage = _make_storage()
        controller = ActionController(storage)

        domains = ["email", "calendar", "tasks", "general"]

        for domain in domains:
            storage.reset_domain_trust.reset_mock()
            storage.reset_domain_trust.return_value = 3

            await controller.reset_domain(12345, domain)

            storage.reset_domain_trust.assert_awaited_once_with(12345, domain)

    @pytest.mark.asyncio
    async def test_reset_domain_returns_zero_when_no_policies(self):
        """Verify reset_domain returns 0 when no policies affected."""
        storage = _make_storage()
        storage.reset_domain_trust.return_value = 0
        controller = ActionController(storage)

        count = await controller.reset_domain(12345, "nonexistent_domain")

        assert count == 0


# ---------------------------------------------------------------------------
# Integration-style tests
# ---------------------------------------------------------------------------


class TestActionControllerIntegration:
    """Integration-style tests for ActionController workflows."""

    @pytest.mark.asyncio
    async def test_controller_initialization(self):
        """Verify ActionController can be initialized with storage."""
        storage = _make_storage()
        controller = ActionController(storage)

        assert controller._storage is storage

    @pytest.mark.asyncio
    async def test_complete_workflow_auto_mode(self):
        """Test complete workflow with auto mode policy."""
        storage = _make_storage()
        policy = _make_policy(mode="auto", trust_score=0.95)
        storage.get_policy.return_value = policy
        controller = ActionController(storage)

        decision = await controller.decide(12345, "email", "auto_reply")

        assert decision.should_execute is True
        assert decision.mode == "auto"
        assert decision.trust_score == 0.95

    @pytest.mark.asyncio
    async def test_complete_workflow_trust_evolution(self):
        """Test workflow for recording outcomes and trust evolution."""
        storage = _make_storage()
        storage.update_trust_score.return_value = 0.60
        controller = ActionController(storage)

        new_score = await controller.record_outcome(
            12345, "email", "auto_reply", ActionOutcome.APPROVED
        )

        assert new_score == 0.60
        storage.update_trust_score.assert_awaited_once_with(12345, "email", "auto_reply", 0.05)

    @pytest.mark.asyncio
    async def test_multiple_domains_independent(self):
        """Test that different domains maintain independent state."""
        storage = _make_storage()
        controller = ActionController(storage)

        email_policy = _make_policy(mode="auto", trust_score=0.9, domain=PolicyDomain.EMAIL)
        tasks_policy = _make_policy(mode="ask", trust_score=0.3, domain=PolicyDomain.TASKS)

        storage.get_policy.side_effect = [email_policy, tasks_policy]

        email_decision = await controller.decide(12345, "email", "auto_reply")
        tasks_decision = await controller.decide(12345, "tasks", "create_task")

        assert email_decision.should_execute is True
        assert tasks_decision.should_execute is False
