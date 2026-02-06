"""Unit tests for the employment profile module."""

from datetime import datetime
from unittest.mock import patch

import pytest

from zetherion_ai.profile.employment import (
    CommunicationStyle,
    EmploymentProfile,
    Milestone,
    RoleDefinition,
    SkillUsage,
    TrustLevel,
    create_default_profile,
)


class TestRoleDefinition:
    """Tests for RoleDefinition dataclass."""

    def test_add_primary_role(self):
        """Test adding a primary role."""
        role = RoleDefinition()
        role.add_role("assistant", primary=True)

        assert "assistant" in role.primary_roles
        assert "assistant" not in role.secondary_capabilities

    def test_add_secondary_role(self):
        """Test adding a secondary role."""
        role = RoleDefinition()
        role.add_role("code review", primary=False)

        assert "code review" not in role.primary_roles
        assert "code review" in role.secondary_capabilities

    def test_add_duplicate_role(self):
        """Test that duplicate roles are not added."""
        role = RoleDefinition()
        role.add_role("assistant", primary=True)
        role.add_role("assistant", primary=True)

        assert role.primary_roles.count("assistant") == 1

    def test_remove_role(self):
        """Test removing a role."""
        role = RoleDefinition(primary_roles=["assistant", "coder"])
        role.remove_role("assistant")

        assert "assistant" not in role.primary_roles
        assert "coder" in role.primary_roles

    def test_add_boundary(self):
        """Test adding a boundary."""
        role = RoleDefinition()
        role.add_boundary("no financial advice")

        assert "no financial advice" in role.boundaries

    def test_to_dict_and_from_dict(self):
        """Test serialization and deserialization."""
        role = RoleDefinition(
            primary_roles=["assistant"],
            secondary_capabilities=["code review"],
            boundaries=["no financial advice"],
            current_focus="testing",
        )

        data = role.to_dict()
        restored = RoleDefinition.from_dict(data)

        assert restored.primary_roles == role.primary_roles
        assert restored.secondary_capabilities == role.secondary_capabilities
        assert restored.boundaries == role.boundaries
        assert restored.current_focus == role.current_focus


class TestCommunicationStyle:
    """Tests for CommunicationStyle dataclass."""

    def test_default_values(self):
        """Test default communication style values."""
        style = CommunicationStyle()

        assert style.formality == 0.5
        assert style.verbosity == 0.5
        assert style.proactivity == 0.3
        assert style.tone == "professional"
        assert style.humor_level == 0.2
        assert style.emoji_usage == 0.0

    def test_validation_rejects_invalid_values(self):
        """Test that invalid float values are rejected."""
        with pytest.raises(ValueError, match="formality must be between"):
            CommunicationStyle(formality=1.5)

        with pytest.raises(ValueError, match="verbosity must be between"):
            CommunicationStyle(verbosity=-0.1)

    def test_adjust_increases_value(self):
        """Test adjusting a style attribute upward."""
        style = CommunicationStyle(verbosity=0.5)
        new_value = style.adjust("verbosity", 0.2)

        assert new_value == pytest.approx(0.7)
        assert style.verbosity == pytest.approx(0.7)

    def test_adjust_decreases_value(self):
        """Test adjusting a style attribute downward."""
        style = CommunicationStyle(formality=0.5)
        new_value = style.adjust("formality", -0.3)

        assert new_value == pytest.approx(0.2)
        assert style.formality == pytest.approx(0.2)

    def test_adjust_clamps_to_max(self):
        """Test that adjust clamps to maximum of 1.0."""
        style = CommunicationStyle(verbosity=0.9)
        new_value = style.adjust("verbosity", 0.5)

        assert new_value == 1.0

    def test_adjust_clamps_to_min(self):
        """Test that adjust clamps to minimum of 0.0."""
        style = CommunicationStyle(formality=0.1)
        new_value = style.adjust("formality", -0.5)

        assert new_value == 0.0

    def test_adjust_unknown_attribute(self):
        """Test that adjusting unknown attribute raises error."""
        style = CommunicationStyle()

        with pytest.raises(ValueError, match="Unknown attribute"):
            style.adjust("nonexistent", 0.1)

    def test_describe_casual(self):
        """Test description for casual style."""
        style = CommunicationStyle(formality=0.2, verbosity=0.2)
        description = style.describe()

        assert "casual" in description
        assert "concise" in description

    def test_describe_formal(self):
        """Test description for formal style."""
        style = CommunicationStyle(formality=0.8, verbosity=0.8)
        description = style.describe()

        assert "formal" in description
        assert "detailed" in description

    def test_to_dict_and_from_dict(self):
        """Test serialization and deserialization."""
        style = CommunicationStyle(
            formality=0.7,
            verbosity=0.3,
            proactivity=0.5,
            tone="friendly",
            humor_level=0.4,
            emoji_usage=0.1,
        )

        data = style.to_dict()
        restored = CommunicationStyle.from_dict(data)

        assert restored.formality == style.formality
        assert restored.verbosity == style.verbosity
        assert restored.proactivity == style.proactivity
        assert restored.tone == style.tone
        assert restored.humor_level == style.humor_level
        assert restored.emoji_usage == style.emoji_usage


class TestSkillUsage:
    """Tests for SkillUsage dataclass."""

    def test_record_use_increments_count(self):
        """Test that recording use increments the count."""
        usage = SkillUsage(skill_name="task_manager")
        usage.record_use()
        usage.record_use()

        assert usage.invocation_count == 2

    def test_record_use_updates_last_used(self):
        """Test that recording use updates last_used timestamp."""
        usage = SkillUsage(skill_name="task_manager")
        assert usage.last_used is None

        usage.record_use()
        assert usage.last_used is not None

    def test_record_use_updates_success_rate(self):
        """Test that success rate is updated on use."""
        usage = SkillUsage(skill_name="task_manager", success_rate=1.0)

        # Record a failure
        usage.record_use(success=False)

        # Success rate should decrease (EMA with alpha=0.1)
        assert usage.success_rate < 1.0

    def test_days_since_used_never_used(self):
        """Test days_since_used for never-used skill."""
        usage = SkillUsage(skill_name="task_manager")
        assert usage.days_since_used() is None

    def test_days_since_used_recently(self):
        """Test days_since_used for recently used skill."""
        usage = SkillUsage(skill_name="task_manager")
        usage.record_use()

        assert usage.days_since_used() == 0

    def test_to_dict_and_from_dict(self):
        """Test serialization and deserialization."""
        usage = SkillUsage(
            skill_name="task_manager",
            invocation_count=10,
            last_used=datetime.now(),
            success_rate=0.9,
        )

        data = usage.to_dict()
        restored = SkillUsage.from_dict(data)

        assert restored.skill_name == usage.skill_name
        assert restored.invocation_count == usage.invocation_count
        assert restored.success_rate == usage.success_rate


class TestTrustLevel:
    """Tests for TrustLevel enum."""

    def test_all_levels_exist(self):
        """Test that all trust levels are defined."""
        assert TrustLevel.MINIMAL.value == "minimal"
        assert TrustLevel.BUILDING.value == "building"
        assert TrustLevel.ESTABLISHED.value == "established"
        assert TrustLevel.HIGH.value == "high"
        assert TrustLevel.FULL.value == "full"


class TestMilestone:
    """Tests for Milestone enum."""

    def test_key_milestones_exist(self):
        """Test that key milestones are defined."""
        assert Milestone.FIRST_INTERACTION.value == "first_interaction"
        assert Milestone.FIRST_TASK_COMPLETED.value == "first_task_completed"
        assert Milestone.TRUST_GRANTED.value == "trust_granted"
        assert Milestone.FIRST_DELEGATION.value == "first_delegation"


class TestEmploymentProfile:
    """Tests for EmploymentProfile dataclass."""

    def test_default_values(self):
        """Test default employment profile values."""
        profile = EmploymentProfile(user_id="user123")

        assert profile.user_id == "user123"
        assert profile.trust_level == 0.3
        assert profile.trust_enum == TrustLevel.BUILDING
        assert profile.total_interactions == 0
        assert isinstance(profile.role, RoleDefinition)
        assert isinstance(profile.style, CommunicationStyle)

    def test_validation_rejects_invalid_trust(self):
        """Test that invalid trust level is rejected."""
        with pytest.raises(ValueError, match="Trust level must be between"):
            EmploymentProfile(user_id="user123", trust_level=1.5)

    def test_record_interaction_increments_count(self):
        """Test that recording interaction increments count."""
        profile = EmploymentProfile(user_id="user123")
        profile.record_interaction()
        profile.record_interaction()

        assert profile.total_interactions == 2

    def test_record_first_interaction_achieves_milestone(self):
        """Test that first interaction achieves milestone."""
        profile = EmploymentProfile(user_id="user123")
        profile.record_interaction()

        assert Milestone.FIRST_INTERACTION.value in profile.milestones_achieved

    def test_record_100_interactions_achieves_milestone(self):
        """Test that 100 interactions achieves milestone."""
        profile = EmploymentProfile(user_id="user123")

        for _ in range(100):
            profile.record_interaction()

        assert Milestone.HUNDRED_INTERACTIONS.value in profile.milestones_achieved

    def test_achieve_milestone_boosts_trust(self):
        """Test that achieving milestones boosts trust."""
        profile = EmploymentProfile(user_id="user123", trust_level=0.3)
        initial_trust = profile.trust_level

        profile.achieve_milestone(Milestone.TRUST_GRANTED)

        assert profile.trust_level > initial_trust

    def test_achieve_milestone_only_once(self):
        """Test that milestones can only be achieved once."""
        profile = EmploymentProfile(user_id="user123", trust_level=0.3)

        first = profile.achieve_milestone(Milestone.FIRST_TASK_COMPLETED)
        second = profile.achieve_milestone(Milestone.FIRST_TASK_COMPLETED)

        assert first is True
        assert second is False

    def test_adjust_trust_updates_enum(self):
        """Test that adjusting trust updates the trust enum."""
        profile = EmploymentProfile(user_id="user123", trust_level=0.3)

        profile.adjust_trust(0.2)  # Now at 0.5 (< 0.6 = ESTABLISHED)
        assert profile.trust_enum == TrustLevel.ESTABLISHED

        profile.adjust_trust(0.35)  # Now at 0.85 (>= 0.8 = FULL)
        assert profile.trust_enum == TrustLevel.FULL

    def test_adjust_trust_clamps_values(self):
        """Test that trust adjustment clamps to valid range."""
        profile = EmploymentProfile(user_id="user123", trust_level=0.9)
        profile.adjust_trust(0.5)
        assert profile.trust_level == 1.0

        profile.adjust_trust(-2.0)
        assert profile.trust_level == 0.0

    def test_record_skill_use(self):
        """Test recording skill usage."""
        profile = EmploymentProfile(user_id="user123")

        profile.record_skill_use("task_manager")
        profile.record_skill_use("task_manager")
        profile.record_skill_use("calendar")

        assert "task_manager" in profile.skill_usage
        assert profile.skill_usage["task_manager"].invocation_count == 2
        assert "calendar" in profile.skill_usage

    def test_skill_priority_order(self):
        """Test that skill priority order is updated."""
        profile = EmploymentProfile(user_id="user123")

        # Use task_manager more than calendar
        for _ in range(5):
            profile.record_skill_use("task_manager")
        for _ in range(2):
            profile.record_skill_use("calendar")

        assert profile.priority_order[0] == "task_manager"
        assert "calendar" in profile.priority_order

    def test_get_trust_description(self):
        """Test trust level descriptions."""
        profile = EmploymentProfile(user_id="user123", trust_level=0.1)
        profile.trust_enum = TrustLevel.MINIMAL
        assert "New relationship" in profile.get_trust_description()

        profile.adjust_trust(0.35)  # Now at 0.45, ESTABLISHED
        assert "Established trust" in profile.get_trust_description()

    def test_to_prompt_fragment(self):
        """Test system prompt fragment generation."""
        profile = EmploymentProfile(user_id="user123")
        profile.role.add_role("assistant")
        profile.role.current_focus = "testing"

        fragment = profile.to_prompt_fragment()

        assert "assistant" in fragment
        assert "testing" in fragment
        assert "Trust level" in fragment

    def test_to_dict_and_from_dict(self):
        """Test serialization and deserialization."""
        profile = EmploymentProfile(
            user_id="user123",
            trust_level=0.6,
            total_interactions=50,
        )
        profile.role.add_role("assistant")
        profile.record_skill_use("task_manager")
        profile.achieve_milestone(Milestone.FIRST_TASK_COMPLETED)

        data = profile.to_dict()
        restored = EmploymentProfile.from_dict(data)

        assert restored.user_id == profile.user_id
        assert restored.trust_level == profile.trust_level
        assert restored.total_interactions == profile.total_interactions
        assert "assistant" in restored.role.primary_roles
        assert "task_manager" in restored.skill_usage
        assert Milestone.FIRST_TASK_COMPLETED.value in restored.milestones_achieved


class TestCreateDefaultProfile:
    """Tests for create_default_profile function."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings for tests."""
        with patch("zetherion_ai.config.get_settings") as mock_get:
            mock_obj = mock_get.return_value
            mock_obj.default_formality = 0.5
            mock_obj.default_verbosity = 0.5
            mock_obj.default_proactivity = 0.3
            yield mock_get

    def test_creates_profile_with_user_id(self, mock_settings):
        """Test that default profile has correct user ID."""
        profile = create_default_profile("user123")
        assert profile.user_id == "user123"

    def test_creates_profile_with_default_roles(self, mock_settings):
        """Test that default profile has default roles."""
        profile = create_default_profile("user123")
        assert "assistant" in profile.role.primary_roles

    def test_creates_profile_with_building_trust(self, mock_settings):
        """Test that default profile starts with building trust."""
        profile = create_default_profile("user123")

        assert profile.trust_level == 0.3
        assert profile.trust_enum == TrustLevel.BUILDING
