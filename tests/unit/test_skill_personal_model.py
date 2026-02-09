"""Unit tests for the PersonalModelSkill."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from zetherion_ai.personal.models import (
    CommunicationStyle,
    LearningCategory,
    LearningSource,
    PersonalContact,
    PersonalLearning,
    PersonalPolicy,
    PersonalProfile,
    PolicyDomain,
    PolicyMode,
    Relationship,
    WorkingHours,
)
from zetherion_ai.personal.storage import PersonalStorage
from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.permissions import Permission
from zetherion_ai.skills.personal_model import (
    INTENT_CONTACTS,
    INTENT_EXPORT,
    INTENT_FORGET,
    INTENT_POLICIES,
    INTENT_SUMMARY,
    INTENT_UPDATE,
    TIMEZONE_ALIASES,
    PersonalModelSkill,
)

# ---------------------------------------------------------------------------
# Helpers and fixtures
# ---------------------------------------------------------------------------

USER_ID = "12345"
USER_ID_INT = 12345


@pytest.fixture
def mock_storage():
    """Create a mock PersonalStorage with default return values."""
    storage = AsyncMock(spec=PersonalStorage)
    storage.get_profile = AsyncMock(return_value=None)
    storage.list_contacts = AsyncMock(return_value=[])
    storage.list_learnings = AsyncMock(return_value=[])
    storage.list_policies = AsyncMock(return_value=[])
    storage.upsert_profile = AsyncMock()
    storage.add_learning = AsyncMock(return_value=1)
    storage.delete_learning = AsyncMock(return_value=True)
    storage.delete_learnings_by_category = AsyncMock(return_value=0)
    return storage


@pytest.fixture
def skill(mock_storage):
    """Create a PersonalModelSkill with mocked storage."""
    return PersonalModelSkill(memory=None, storage=mock_storage)


@pytest.fixture
def skill_no_storage():
    """Create a PersonalModelSkill with no storage."""
    return PersonalModelSkill(memory=None, storage=None)


def _make_request(
    intent: str = "",
    message: str = "",
    context: dict | None = None,
    user_id: str = USER_ID,
) -> SkillRequest:
    """Create a SkillRequest with sensible defaults."""
    return SkillRequest(
        id=uuid4(),
        user_id=user_id,
        intent=intent,
        message=message,
        context=context or {},
    )


def _make_profile(
    user_id: int = USER_ID_INT,
    display_name: str | None = "TestUser",
    timezone: str = "UTC",
    locale: str = "en",
    goals: list[str] | None = None,
    communication_style: CommunicationStyle | None = None,
    working_hours: WorkingHours | None = None,
) -> PersonalProfile:
    """Create a PersonalProfile for testing."""
    return PersonalProfile(
        user_id=user_id,
        display_name=display_name,
        timezone=timezone,
        locale=locale,
        goals=goals or [],
        communication_style=communication_style,
        working_hours=working_hours,
    )


def _make_contact(
    user_id: int = USER_ID_INT,
    contact_name: str | None = "Alice",
    contact_email: str | None = "alice@example.com",
    relationship: Relationship = Relationship.COLLEAGUE,
    importance: float = 0.8,
    company: str | None = "Acme",
    interaction_count: int = 5,
) -> PersonalContact:
    """Create a PersonalContact for testing."""
    return PersonalContact(
        id=1,
        user_id=user_id,
        contact_name=contact_name,
        contact_email=contact_email,
        relationship=relationship,
        importance=importance,
        company=company,
        interaction_count=interaction_count,
    )


def _make_learning(
    user_id: int = USER_ID_INT,
    learning_id: int | None = 1,
    content: str = "User prefers dark mode",
    category: LearningCategory = LearningCategory.PREFERENCE,
    confidence: float = 0.9,
    source: LearningSource = LearningSource.EXPLICIT,
    confirmed: bool = True,
) -> PersonalLearning:
    """Create a PersonalLearning for testing."""
    return PersonalLearning(
        id=learning_id,
        user_id=user_id,
        category=category,
        content=content,
        confidence=confidence,
        source=source,
        confirmed=confirmed,
    )


def _make_policy(
    user_id: int = USER_ID_INT,
    domain: PolicyDomain = PolicyDomain.EMAIL,
    action: str = "auto_reply",
    mode: PolicyMode = PolicyMode.ASK,
    trust_score: float = 0.5,
) -> PersonalPolicy:
    """Create a PersonalPolicy for testing."""
    return PersonalPolicy(
        id=1,
        user_id=user_id,
        domain=domain,
        action=action,
        mode=mode,
        trust_score=trust_score,
    )


# ---------------------------------------------------------------------------
# 1. Metadata tests
# ---------------------------------------------------------------------------


class TestMetadata:
    """Tests for PersonalModelSkill metadata."""

    def test_skill_name_is_personal_model(self, skill):
        """Verify the skill name is 'personal_model'."""
        assert skill.metadata.name == "personal_model"

    def test_intents_list(self, skill):
        """Verify INTENTS contains all expected intents."""
        intents = skill.metadata.intents
        assert INTENT_SUMMARY in intents
        assert INTENT_UPDATE in intents
        assert INTENT_FORGET in intents
        assert INTENT_CONTACTS in intents
        assert INTENT_EXPORT in intents
        assert INTENT_POLICIES in intents
        assert len(intents) == 6

    def test_permissions_include_required(self, skill):
        """Verify permissions include READ_PROFILE, WRITE_PROFILE, DELETE_PROFILE, SEND_MESSAGES."""
        perms = skill.metadata.permissions
        assert Permission.READ_PROFILE in perms
        assert Permission.WRITE_PROFILE in perms
        assert Permission.DELETE_PROFILE in perms
        assert Permission.SEND_MESSAGES in perms


# ---------------------------------------------------------------------------
# 2. Initialize tests
# ---------------------------------------------------------------------------


class TestInitialize:
    """Tests for PersonalModelSkill.initialize()."""

    @pytest.mark.asyncio
    async def test_returns_true_when_storage_available(self, skill):
        """Initialize returns True when storage is set."""
        result = await skill.initialize()
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_storage_is_none(self, skill_no_storage):
        """Initialize returns False when storage is None."""
        result = await skill_no_storage.initialize()
        assert result is False


# ---------------------------------------------------------------------------
# 3. Handle dispatch tests
# ---------------------------------------------------------------------------


class TestHandleDispatch:
    """Tests for PersonalModelSkill.handle() dispatch logic."""

    @pytest.mark.asyncio
    async def test_routes_to_summary_handler(self, skill, mock_storage):
        """Dispatches to _handle_summary for personal_summary intent."""
        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill.handle(request)
        assert response.success is True
        mock_storage.get_profile.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_returns_error_for_unknown_intent(self, skill):
        """Returns error response for an unrecognized intent."""
        request = _make_request(intent="unknown_intent")
        response = await skill.handle(request)
        assert response.success is False
        assert "Unknown intent" in response.error

    @pytest.mark.asyncio
    async def test_returns_error_when_storage_is_none(self, skill_no_storage):
        """Returns error when storage is None."""
        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill_no_storage.handle(request)
        assert response.success is False
        assert "not available" in response.error


# ---------------------------------------------------------------------------
# 4. Summary intent tests
# ---------------------------------------------------------------------------


class TestSummaryIntent:
    """Tests for the personal_summary intent."""

    @pytest.mark.asyncio
    async def test_full_profile_with_contacts_learnings_policies(self, skill, mock_storage):
        """Summary with full profile, contacts, learnings, and policies."""
        profile = _make_profile(
            goals=["learn Rust", "read more"],
            communication_style=CommunicationStyle(formality=0.8, verbosity=0.6),
        )
        contacts = [_make_contact()]
        learnings = [
            _make_learning(confirmed=True),
            _make_learning(learning_id=2, confirmed=False, content="User likes coffee"),
        ]
        policies = [_make_policy()]

        mock_storage.get_profile.return_value = profile
        mock_storage.list_contacts.return_value = contacts
        mock_storage.list_learnings.return_value = learnings
        mock_storage.list_policies.return_value = policies

        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill.handle(request)

        assert response.success is True
        assert "TestUser" in response.message
        assert "learn Rust" in response.message
        assert "formality=0.8" in response.message
        assert "Alice" in response.message
        assert "Learnings" in response.message
        assert "1 confirmed" in response.message
        assert "Policies" in response.message
        assert response.data["has_profile"] is True
        assert response.data["contact_count"] == 1
        assert response.data["learning_count"] == 2
        assert response.data["policy_count"] == 1

    @pytest.mark.asyncio
    async def test_empty_profile_no_data(self, skill, mock_storage):
        """Summary with no profile or data returns appropriate message."""
        mock_storage.get_profile.return_value = None
        mock_storage.list_contacts.return_value = []
        mock_storage.list_learnings.return_value = []
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill.handle(request)

        assert response.success is True
        assert "don't have a profile" in response.message
        assert response.data["has_profile"] is False
        assert response.data["contact_count"] == 0

    @pytest.mark.asyncio
    async def test_profile_no_contacts_no_learnings(self, skill, mock_storage):
        """Summary with profile but no contacts or learnings."""
        mock_storage.get_profile.return_value = _make_profile()
        mock_storage.list_contacts.return_value = []
        mock_storage.list_learnings.return_value = []
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill.handle(request)

        assert response.success is True
        assert "TestUser" in response.message
        assert "Contacts" not in response.message
        assert "Learnings" not in response.message

    @pytest.mark.asyncio
    async def test_confirmed_vs_unconfirmed_learnings_count(self, skill, mock_storage):
        """Verify confirmed vs unconfirmed learnings are counted correctly."""
        learnings = [
            _make_learning(learning_id=1, confirmed=True, content="Fact A"),
            _make_learning(learning_id=2, confirmed=True, content="Fact B"),
            _make_learning(learning_id=3, confirmed=False, content="Fact C"),
        ]
        mock_storage.get_profile.return_value = _make_profile()
        mock_storage.list_learnings.return_value = learnings
        mock_storage.list_contacts.return_value = []
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill.handle(request)

        assert "3 total" in response.message
        assert "2 confirmed" in response.message

    @pytest.mark.asyncio
    async def test_profile_with_no_display_name(self, skill, mock_storage):
        """Summary with profile that has no display name."""
        mock_storage.get_profile.return_value = _make_profile(display_name=None)
        mock_storage.list_contacts.return_value = []
        mock_storage.list_learnings.return_value = []
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill.handle(request)

        assert "No name set" in response.message


# ---------------------------------------------------------------------------
# 5. Update intent tests
# ---------------------------------------------------------------------------


class TestUpdateIntent:
    """Tests for the personal_update intent."""

    @pytest.mark.asyncio
    async def test_update_timezone_via_context(self, skill, mock_storage):
        """Update timezone through context field/value."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "timezone", "value": "America/New_York"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "timezone" in response.message
        assert "America/New_York" in response.message
        mock_storage.upsert_profile.assert_awaited_once()
        mock_storage.add_learning.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_timezone_via_message_parsing(self, skill, mock_storage):
        """Update timezone by parsing 'My timezone is PST' from message."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            message="My timezone is PST",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "America/Los_Angeles" in response.message
        assert response.data["field"] == "timezone"

    @pytest.mark.asyncio
    async def test_update_locale_via_message(self, skill, mock_storage):
        """Update locale by parsing 'Set my locale to fr' from message."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            message="Set my locale to fr",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "locale" in response.message
        assert response.data["value"] == "fr"

    @pytest.mark.asyncio
    async def test_update_display_name_via_message(self, skill, mock_storage):
        """Update display_name by parsing 'My name is James' from message."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            message="My name is James",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "display_name" in response.message
        assert response.data["value"] == "James"

    @pytest.mark.asyncio
    async def test_update_goals_via_message(self, skill, mock_storage):
        """Update goals by parsing 'Add goal: learn Rust' from message."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            message="Add goal: learn Rust",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "goal" in response.message
        assert response.data["value"] == "learn Rust"

    @pytest.mark.asyncio
    async def test_update_formality(self, skill, mock_storage):
        """Update formality communication style via context."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "formality", "value": 0.9},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "formality" in response.message
        assert response.data["value"] == 0.9

    @pytest.mark.asyncio
    async def test_update_verbosity(self, skill, mock_storage):
        """Update verbosity communication style via context."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "verbosity", "value": 0.3},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "verbosity" in response.message
        assert response.data["value"] == 0.3

    @pytest.mark.asyncio
    async def test_update_working_hours_start(self, skill, mock_storage):
        """Update working_hours_start via context."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "working_hours_start", "value": "08:00"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "working_hours_start" in response.message

    @pytest.mark.asyncio
    async def test_update_working_hours_end(self, skill, mock_storage):
        """Update working_hours_end via context."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "working_hours_end", "value": "18:00"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "working_hours_end" in response.message

    @pytest.mark.asyncio
    async def test_unknown_field_returns_error(self, skill, mock_storage):
        """Unknown field in context returns error response."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "nonexistent_field", "value": "whatever"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "Unknown field" in response.error

    @pytest.mark.asyncio
    async def test_missing_field_and_unparseable_message(self, skill, mock_storage):
        """Missing field with unparseable message returns error."""
        request = _make_request(
            intent=INTENT_UPDATE,
            message="something completely unrelated",
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "Could not determine" in response.error

    @pytest.mark.asyncio
    async def test_update_creates_profile_if_none(self, skill, mock_storage):
        """Update creates a new profile when none exists."""
        mock_storage.get_profile.return_value = None

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "timezone", "value": "Europe/London"},
        )
        response = await skill.handle(request)

        assert response.success is True
        mock_storage.upsert_profile.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_update_formality_creates_communication_style_if_none(self, skill, mock_storage):
        """Update formality creates CommunicationStyle when profile has none."""
        profile = _make_profile(communication_style=None)
        mock_storage.get_profile.return_value = profile

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "formality", "value": 0.7},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert profile.communication_style is not None
        assert profile.communication_style.formality == 0.7

    @pytest.mark.asyncio
    async def test_update_verbosity_creates_communication_style_if_none(self, skill, mock_storage):
        """Update verbosity creates CommunicationStyle when profile has none."""
        profile = _make_profile(communication_style=None)
        mock_storage.get_profile.return_value = profile

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "verbosity", "value": 0.2},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert profile.communication_style is not None
        assert profile.communication_style.verbosity == 0.2

    @pytest.mark.asyncio
    async def test_update_working_hours_start_creates_working_hours_if_none(
        self, skill, mock_storage
    ):
        """Update working_hours_start creates WorkingHours when profile has none."""
        profile = _make_profile(working_hours=None)
        mock_storage.get_profile.return_value = profile

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "working_hours_start", "value": "07:00"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert profile.working_hours is not None
        assert profile.working_hours.start == "07:00"

    @pytest.mark.asyncio
    async def test_update_working_hours_end_creates_working_hours_if_none(
        self, skill, mock_storage
    ):
        """Update working_hours_end creates WorkingHours when profile has none."""
        profile = _make_profile(working_hours=None)
        mock_storage.get_profile.return_value = profile

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "working_hours_end", "value": "19:00"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert profile.working_hours is not None
        assert profile.working_hours.end == "19:00"

    @pytest.mark.asyncio
    async def test_update_goal_does_not_duplicate(self, skill, mock_storage):
        """Adding an existing goal does not duplicate it."""
        profile = _make_profile(goals=["learn Rust"])
        mock_storage.get_profile.return_value = profile

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "goal", "value": "learn Rust"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert profile.goals.count("learn Rust") == 1

    @pytest.mark.asyncio
    async def test_update_formality_clamped_to_range(self, skill, mock_storage):
        """Formality values are clamped to [0.0, 1.0]."""
        profile = _make_profile()
        mock_storage.get_profile.return_value = profile

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "formality", "value": 1.5},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["value"] == 1.0


# ---------------------------------------------------------------------------
# 6. Forget intent tests
# ---------------------------------------------------------------------------


class TestForgetIntent:
    """Tests for the personal_forget intent."""

    @pytest.mark.asyncio
    async def test_delete_by_learning_id_success(self, skill, mock_storage):
        """Delete learning by ID successfully."""
        mock_storage.delete_learning.return_value = True

        request = _make_request(
            intent=INTENT_FORGET,
            context={"learning_id": 42},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "Forgotten" in response.message
        assert response.data["deleted"] is True
        mock_storage.delete_learning.assert_awaited_once_with(42)

    @pytest.mark.asyncio
    async def test_delete_by_learning_id_not_found(self, skill, mock_storage):
        """Delete learning by ID that does not exist returns error."""
        mock_storage.delete_learning.return_value = False

        request = _make_request(
            intent=INTENT_FORGET,
            context={"learning_id": 999},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "#999" in response.error

    @pytest.mark.asyncio
    async def test_delete_by_category(self, skill, mock_storage):
        """Delete learnings by category."""
        mock_storage.delete_learnings_by_category.return_value = 3

        request = _make_request(
            intent=INTENT_FORGET,
            context={"category": "preference"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "3" in response.message
        assert response.data["category"] == "preference"

    @pytest.mark.asyncio
    async def test_delete_by_content_match_from_message(self, skill, mock_storage):
        """Delete learnings matching content from message."""
        learnings = [
            _make_learning(learning_id=10, content="User likes dark mode"),
            _make_learning(learning_id=11, content="User prefers Python"),
        ]
        mock_storage.list_learnings.return_value = learnings
        mock_storage.delete_learning.return_value = True

        request = _make_request(
            intent=INTENT_FORGET,
            message="dark mode",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "1" in response.message
        mock_storage.delete_learning.assert_awaited_once_with(10)

    @pytest.mark.asyncio
    async def test_delete_no_matches_found(self, skill, mock_storage):
        """No matching learnings returns appropriate message."""
        mock_storage.list_learnings.return_value = [
            _make_learning(content="unrelated fact"),
        ]

        request = _make_request(
            intent=INTENT_FORGET,
            message="nonexistent topic",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "couldn't find" in response.message
        assert response.data["deleted"] is False

    @pytest.mark.asyncio
    async def test_delete_content_match_with_none_learning_id(self, skill, mock_storage):
        """Content match with learning that has None id is skipped."""
        learnings = [
            _make_learning(learning_id=None, content="User likes dark mode"),
        ]
        mock_storage.list_learnings.return_value = learnings

        request = _make_request(
            intent=INTENT_FORGET,
            message="dark mode",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "0" in response.message
        mock_storage.delete_learning.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_delete_content_match_uses_content_match_context(self, skill, mock_storage):
        """Uses content_match from context when available."""
        learnings = [
            _make_learning(learning_id=10, content="User likes tea"),
        ]
        mock_storage.list_learnings.return_value = learnings
        mock_storage.delete_learning.return_value = True

        request = _make_request(
            intent=INTENT_FORGET,
            message="forget something",
            context={"content_match": "tea"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["count"] == 1


# ---------------------------------------------------------------------------
# 7. Contacts intent tests
# ---------------------------------------------------------------------------


class TestContactsIntent:
    """Tests for the personal_contacts intent."""

    @pytest.mark.asyncio
    async def test_list_contacts_with_data(self, skill, mock_storage):
        """List contacts returns formatted contact list."""
        contacts = [
            _make_contact(contact_name="Alice", contact_email="alice@test.com"),
            _make_contact(
                contact_name=None,
                contact_email="bob@test.com",
                relationship=Relationship.FRIEND,
            ),
        ]
        mock_storage.list_contacts.return_value = contacts

        request = _make_request(intent=INTENT_CONTACTS)
        response = await skill.handle(request)

        assert response.success is True
        assert "Alice" in response.message
        assert "bob@test.com" in response.message
        assert response.data["count"] == 2

    @pytest.mark.asyncio
    async def test_empty_contacts_list(self, skill, mock_storage):
        """Empty contacts list returns 'No contacts found.'"""
        mock_storage.list_contacts.return_value = []

        request = _make_request(intent=INTENT_CONTACTS)
        response = await skill.handle(request)

        assert response.success is True
        assert "No contacts found" in response.message
        assert response.data["count"] == 0

    @pytest.mark.asyncio
    async def test_contacts_with_relationship_and_importance_filters(self, skill, mock_storage):
        """Contacts with relationship/importance filters passed to storage."""
        mock_storage.list_contacts.return_value = [_make_contact()]

        request = _make_request(
            intent=INTENT_CONTACTS,
            context={
                "relationship": "colleague",
                "min_importance": 0.7,
                "limit": 10,
            },
        )
        response = await skill.handle(request)

        assert response.success is True
        mock_storage.list_contacts.assert_awaited_once_with(
            USER_ID_INT,
            relationship="colleague",
            min_importance=0.7,
            limit=10,
        )

    @pytest.mark.asyncio
    async def test_contact_with_no_name_and_no_email(self, skill, mock_storage):
        """Contact with no name and no email shows 'unknown'."""
        contact = _make_contact(contact_name=None, contact_email=None)
        mock_storage.list_contacts.return_value = [contact]

        request = _make_request(intent=INTENT_CONTACTS)
        response = await skill.handle(request)

        assert "unknown" in response.message


# ---------------------------------------------------------------------------
# 8. Export intent tests
# ---------------------------------------------------------------------------


class TestExportIntent:
    """Tests for the personal_export intent."""

    @pytest.mark.asyncio
    async def test_full_export_with_all_data(self, skill, mock_storage):
        """Export with profile, contacts, learnings, and policies."""
        profile = _make_profile()
        contacts = [_make_contact()]
        learnings = [_make_learning()]
        policies = [_make_policy()]

        mock_storage.get_profile.return_value = profile
        mock_storage.list_contacts.return_value = contacts
        mock_storage.list_learnings.return_value = learnings
        mock_storage.list_policies.return_value = policies

        request = _make_request(intent=INTENT_EXPORT)
        response = await skill.handle(request)

        assert response.success is True
        assert "4 items" in response.message
        assert "profile=yes" in response.message
        assert response.data["total_items"] == 4
        export = response.data["export"]
        assert export["profile"] is not None
        assert len(export["contacts"]) == 1
        assert len(export["learnings"]) == 1
        assert len(export["policies"]) == 1

    @pytest.mark.asyncio
    async def test_export_with_no_data(self, skill, mock_storage):
        """Export with no data returns zero items."""
        mock_storage.get_profile.return_value = None
        mock_storage.list_contacts.return_value = []
        mock_storage.list_learnings.return_value = []
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_EXPORT)
        response = await skill.handle(request)

        assert response.success is True
        assert "0 items" in response.message
        assert "profile=no" in response.message
        assert response.data["total_items"] == 0
        assert response.data["export"]["profile"] is None

    @pytest.mark.asyncio
    async def test_export_includes_exported_at_and_user_id(self, skill, mock_storage):
        """Export data includes exported_at and user_id fields."""
        mock_storage.get_profile.return_value = None
        mock_storage.list_contacts.return_value = []
        mock_storage.list_learnings.return_value = []
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_EXPORT)
        response = await skill.handle(request)

        export = response.data["export"]
        assert "exported_at" in export
        assert export["user_id"] == USER_ID_INT


# ---------------------------------------------------------------------------
# 9. Policies intent tests
# ---------------------------------------------------------------------------


class TestPoliciesIntent:
    """Tests for the personal_policies intent."""

    @pytest.mark.asyncio
    async def test_list_policies_with_data(self, skill, mock_storage):
        """List policies returns formatted policy list."""
        policies = [
            _make_policy(
                domain=PolicyDomain.EMAIL,
                action="auto_reply",
                mode=PolicyMode.AUTO,
                trust_score=0.85,
            ),
        ]
        mock_storage.list_policies.return_value = policies

        request = _make_request(intent=INTENT_POLICIES)
        response = await skill.handle(request)

        assert response.success is True
        assert "email" in response.message
        assert "auto_reply" in response.message
        assert response.data["count"] == 1
        assert response.data["policies"][0]["trust_score"] == 0.85

    @pytest.mark.asyncio
    async def test_no_policies(self, skill, mock_storage):
        """No policies returns 'No policies configured.'"""
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_POLICIES)
        response = await skill.handle(request)

        assert response.success is True
        assert "No policies configured" in response.message
        assert response.data["count"] == 0

    @pytest.mark.asyncio
    async def test_filter_by_domain(self, skill, mock_storage):
        """Policies filter by domain is passed to storage."""
        mock_storage.list_policies.return_value = []

        request = _make_request(
            intent=INTENT_POLICIES,
            context={"domain": "email"},
        )
        await skill.handle(request)

        mock_storage.list_policies.assert_awaited_once_with(USER_ID_INT, domain="email")


# ---------------------------------------------------------------------------
# 10. Parse update from message tests
# ---------------------------------------------------------------------------


class TestParseUpdateFromMessage:
    """Tests for _parse_update_from_message helper."""

    def test_timezone_pattern_my_timezone_is(self, skill):
        """Parses 'My timezone is EST'."""
        result = skill._parse_update_from_message("My timezone is EST")
        assert result == ("timezone", "EST")

    def test_timezone_pattern_set_timezone_to(self, skill):
        """Parses 'set timezone to Europe/London'."""
        result = skill._parse_update_from_message("set timezone to Europe/London")
        assert result == ("timezone", "Europe/London")

    def test_timezone_pattern_set_my_timezone_to(self, skill):
        """Parses 'set my timezone to JST'."""
        result = skill._parse_update_from_message("set my timezone to JST")
        assert result == ("timezone", "JST")

    def test_locale_pattern_my_locale_is(self, skill):
        """Parses 'my locale is fr'."""
        result = skill._parse_update_from_message("my locale is fr")
        assert result == ("locale", "fr")

    def test_locale_pattern_set_my_language_to(self, skill):
        """Parses 'set my language to de'."""
        result = skill._parse_update_from_message("set my language to de")
        assert result == ("locale", "de")

    def test_locale_pattern_my_language_is(self, skill):
        """Parses 'my language is es'."""
        result = skill._parse_update_from_message("my language is es")
        assert result == ("locale", "es")

    def test_name_pattern_my_name_is(self, skill):
        """Parses 'My name is James'."""
        result = skill._parse_update_from_message("My name is James")
        assert result == ("display_name", "James")

    def test_name_pattern_call_me(self, skill):
        """Parses 'call me James'."""
        result = skill._parse_update_from_message("call me James")
        assert result == ("display_name", "James")

    def test_name_pattern_set_my_name_to(self, skill):
        """Parses 'set my name to Alice'."""
        result = skill._parse_update_from_message("set my name to Alice")
        assert result == ("display_name", "Alice")

    def test_name_pattern_my_display_name_is(self, skill):
        """Parses 'my display name is Bob'."""
        result = skill._parse_update_from_message("my display name is Bob")
        assert result == ("display_name", "Bob")

    def test_goal_pattern_add_goal_colon(self, skill):
        """Parses 'Add goal: learn Rust'."""
        result = skill._parse_update_from_message("Add goal: learn Rust")
        assert result == ("goal", "learn Rust")

    def test_goal_pattern_new_goal_colon(self, skill):
        """Parses 'New goal: read more'."""
        result = skill._parse_update_from_message("New goal: read more")
        assert result == ("goal", "read more")

    def test_goal_pattern_my_goal_is(self, skill):
        """Parses 'my goal is exercise daily'."""
        result = skill._parse_update_from_message("my goal is exercise daily")
        assert result == ("goal", "exercise daily")

    def test_unparseable_returns_none(self, skill):
        """Unparseable message returns None."""
        result = skill._parse_update_from_message("Hello, how are you?")
        assert result is None


# ---------------------------------------------------------------------------
# 11. TIMEZONE_ALIASES tests
# ---------------------------------------------------------------------------


class TestTimezoneAliases:
    """Tests for TIMEZONE_ALIASES mapping."""

    def test_pst_maps_to_los_angeles(self):
        """Verify PST maps to America/Los_Angeles."""
        assert TIMEZONE_ALIASES["pst"] == "America/Los_Angeles"

    def test_est_maps_to_new_york(self):
        """Verify EST maps to America/New_York."""
        assert TIMEZONE_ALIASES["est"] == "America/New_York"

    @pytest.mark.asyncio
    async def test_unknown_timezone_passed_through(self, skill, mock_storage):
        """Unknown timezone alias is passed through unchanged."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "timezone", "value": "Europe/Berlin"},
        )
        # "Europe/Berlin" is not in TIMEZONE_ALIASES, so it should be passed through
        response = await skill.handle(request)
        assert response.data["value"] == "Europe/Berlin"

    @pytest.mark.asyncio
    async def test_alias_resolution_in_update(self, skill, mock_storage):
        """Timezone alias in update is resolved via TIMEZONE_ALIASES."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "timezone", "value": "CST"},
        )
        response = await skill.handle(request)

        assert response.data["value"] == "America/Chicago"


# ---------------------------------------------------------------------------
# 12. Cleanup and system prompt tests
# ---------------------------------------------------------------------------


class TestCleanupAndSystemPrompt:
    """Tests for cleanup() and get_system_prompt_fragment()."""

    @pytest.mark.asyncio
    async def test_cleanup_runs_without_error(self, skill):
        """cleanup() runs without raising an exception."""
        await skill.cleanup()

    def test_get_system_prompt_fragment_returns_none(self, skill):
        """get_system_prompt_fragment returns None."""
        result = skill.get_system_prompt_fragment("12345")
        assert result is None


# ---------------------------------------------------------------------------
# Additional edge-case tests for coverage
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Additional edge-case tests for branch coverage."""

    @pytest.mark.asyncio
    async def test_summary_learnings_marker_confirmed(self, skill, mock_storage):
        """Confirmed learnings show [+] marker in summary."""
        learnings = [_make_learning(confirmed=True, content="A confirmed fact")]
        mock_storage.get_profile.return_value = _make_profile()
        mock_storage.list_contacts.return_value = []
        mock_storage.list_learnings.return_value = learnings
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill.handle(request)

        assert "[+]" in response.message

    @pytest.mark.asyncio
    async def test_summary_learnings_marker_unconfirmed(self, skill, mock_storage):
        """Unconfirmed learnings show [?] marker in summary."""
        learnings = [_make_learning(confirmed=False, content="An unconfirmed fact")]
        mock_storage.get_profile.return_value = _make_profile()
        mock_storage.list_contacts.return_value = []
        mock_storage.list_learnings.return_value = learnings
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill.handle(request)

        assert "[?]" in response.message

    @pytest.mark.asyncio
    async def test_summary_contact_with_no_name_uses_email(self, skill, mock_storage):
        """Contact without name falls back to email in summary."""
        contact = _make_contact(contact_name=None, contact_email="test@example.com")
        mock_storage.get_profile.return_value = _make_profile()
        mock_storage.list_contacts.return_value = [contact]
        mock_storage.list_learnings.return_value = []
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill.handle(request)

        assert "test@example.com" in response.message

    @pytest.mark.asyncio
    async def test_all_intents_are_routable(self, skill, mock_storage):
        """Verify each known intent is dispatched without unknown-intent error."""
        mock_storage.get_profile.return_value = _make_profile()

        for intent in [
            INTENT_SUMMARY,
            INTENT_UPDATE,
            INTENT_FORGET,
            INTENT_CONTACTS,
            INTENT_EXPORT,
            INTENT_POLICIES,
        ]:
            request = _make_request(
                intent=intent,
                context={"field": "timezone", "value": "UTC"},
            )
            response = await skill.handle(request)
            # None of these should be "Unknown intent" errors
            if response.error:
                assert "Unknown intent" not in response.error

    @pytest.mark.asyncio
    async def test_forget_content_match_delete_returns_false(self, skill, mock_storage):
        """Content match delete where delete_learning returns False."""
        learnings = [_make_learning(learning_id=10, content="User likes tea")]
        mock_storage.list_learnings.return_value = learnings
        mock_storage.delete_learning.return_value = False

        request = _make_request(
            intent=INTENT_FORGET,
            message="tea",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["count"] == 0

    @pytest.mark.asyncio
    async def test_summary_more_than_five_learnings_truncated(self, skill, mock_storage):
        """Summary only shows first 5 learnings in detail."""
        learnings = [
            _make_learning(learning_id=i, content=f"Learning {i}", confirmed=True) for i in range(8)
        ]
        mock_storage.get_profile.return_value = _make_profile()
        mock_storage.list_contacts.return_value = []
        mock_storage.list_learnings.return_value = learnings
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill.handle(request)

        assert "8 total" in response.message
        # Learning 0-4 should be shown, 5-7 should not
        assert "Learning 4" in response.message
        assert "Learning 5" not in response.message

    @pytest.mark.asyncio
    async def test_update_locale_set_locale_to(self, skill, mock_storage):
        """Parses 'set locale to it' from message."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            message="set locale to it",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["value"] == "it"

    @pytest.mark.asyncio
    async def test_update_timezone_is_prefix(self, skill, mock_storage):
        """Parses 'timezone is America/Chicago' from message."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            message="timezone is America/Chicago",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["value"] == "America/Chicago"

    @pytest.mark.asyncio
    async def test_update_goal_add_goal_no_colon(self, skill, mock_storage):
        """Parses 'add goal exercise more' from message."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            message="add goal exercise more",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["value"] == "exercise more"

    @pytest.mark.asyncio
    async def test_update_goal_new_goal_no_colon(self, skill, mock_storage):
        """Parses 'new goal read books' from message."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            message="new goal read books",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["value"] == "read books"

    @pytest.mark.asyncio
    async def test_update_locale_locale_is(self, skill, mock_storage):
        """Parses 'locale is ja' from message."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            message="locale is ja",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["value"] == "ja"

    @pytest.mark.asyncio
    async def test_update_with_field_and_no_value_falls_to_parse(self, skill, mock_storage):
        """When context has field but value is None, falls to message parsing."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            message="My timezone is EST",
            context={"field": "timezone"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "America/New_York" in response.message

    @pytest.mark.asyncio
    async def test_update_with_no_field_falls_to_parse(self, skill, mock_storage):
        """When context has no field, falls to message parsing."""
        mock_storage.get_profile.return_value = _make_profile()

        request = _make_request(
            intent=INTENT_UPDATE,
            message="My name is Alice",
            context={},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["value"] == "Alice"

    @pytest.mark.asyncio
    async def test_profile_with_goals_no_communication_style(self, skill, mock_storage):
        """Summary for profile with goals but no communication_style."""
        profile = _make_profile(goals=["learn Python"], communication_style=None)
        mock_storage.get_profile.return_value = profile
        mock_storage.list_contacts.return_value = []
        mock_storage.list_learnings.return_value = []
        mock_storage.list_policies.return_value = []

        request = _make_request(intent=INTENT_SUMMARY)
        response = await skill.handle(request)

        assert "learn Python" in response.message
        assert "Style:" not in response.message

    @pytest.mark.asyncio
    async def test_update_formality_with_existing_communication_style(self, skill, mock_storage):
        """Update formality when CommunicationStyle already exists on profile."""
        profile = _make_profile(
            communication_style=CommunicationStyle(formality=0.3, verbosity=0.4)
        )
        mock_storage.get_profile.return_value = profile

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "formality", "value": 0.8},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert profile.communication_style.formality == 0.8
        # verbosity should be unchanged
        assert profile.communication_style.verbosity == 0.4

    @pytest.mark.asyncio
    async def test_update_verbosity_with_existing_communication_style(self, skill, mock_storage):
        """Update verbosity when CommunicationStyle already exists on profile."""
        profile = _make_profile(
            communication_style=CommunicationStyle(formality=0.6, verbosity=0.5)
        )
        mock_storage.get_profile.return_value = profile

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "verbosity", "value": 0.9},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert profile.communication_style.verbosity == 0.9
        assert profile.communication_style.formality == 0.6

    @pytest.mark.asyncio
    async def test_update_working_hours_start_with_existing_working_hours(
        self, skill, mock_storage
    ):
        """Update working_hours_start when WorkingHours already exists."""
        profile = _make_profile(working_hours=WorkingHours(start="09:00", end="17:00"))
        mock_storage.get_profile.return_value = profile

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "working_hours_start", "value": "08:00"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert profile.working_hours.start == "08:00"
        assert profile.working_hours.end == "17:00"

    @pytest.mark.asyncio
    async def test_update_working_hours_end_with_existing_working_hours(self, skill, mock_storage):
        """Update working_hours_end when WorkingHours already exists."""
        profile = _make_profile(working_hours=WorkingHours(start="09:00", end="17:00"))
        mock_storage.get_profile.return_value = profile

        request = _make_request(
            intent=INTENT_UPDATE,
            context={"field": "working_hours_end", "value": "20:00"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert profile.working_hours.end == "20:00"
        assert profile.working_hours.start == "09:00"
