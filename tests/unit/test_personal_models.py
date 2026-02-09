"""Unit tests for the personal understanding layer models."""

from __future__ import annotations

import json
from datetime import datetime

import pytest
from pydantic import ValidationError

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

# ---------------------------------------------------------------------------
# Enum tests
# ---------------------------------------------------------------------------


class TestRelationship:
    """Tests for the Relationship enum."""

    def test_all_members_exist(self):
        expected = [
            "COLLEAGUE",
            "CLIENT",
            "FRIEND",
            "MANAGER",
            "VENDOR",
            "FAMILY",
            "ACQUAINTANCE",
            "OTHER",
        ]
        for name in expected:
            assert hasattr(Relationship, name)

    def test_values_are_lowercase_strings(self):
        assert Relationship.COLLEAGUE.value == "colleague"
        assert Relationship.CLIENT.value == "client"
        assert Relationship.FRIEND.value == "friend"
        assert Relationship.MANAGER.value == "manager"
        assert Relationship.VENDOR.value == "vendor"
        assert Relationship.FAMILY.value == "family"
        assert Relationship.ACQUAINTANCE.value == "acquaintance"
        assert Relationship.OTHER.value == "other"

    def test_member_count(self):
        assert len(Relationship) == 8

    def test_is_str_enum(self):
        assert isinstance(Relationship.COLLEAGUE, str)


class TestPolicyDomain:
    """Tests for the PolicyDomain enum."""

    def test_all_members_exist(self):
        expected = ["EMAIL", "CALENDAR", "TASKS", "GENERAL", "DISCORD_OBSERVE"]
        for name in expected:
            assert hasattr(PolicyDomain, name)

    def test_values(self):
        assert PolicyDomain.EMAIL.value == "email"
        assert PolicyDomain.CALENDAR.value == "calendar"
        assert PolicyDomain.TASKS.value == "tasks"
        assert PolicyDomain.GENERAL.value == "general"
        assert PolicyDomain.DISCORD_OBSERVE.value == "discord_observe"

    def test_member_count(self):
        assert len(PolicyDomain) == 5


class TestPolicyMode:
    """Tests for the PolicyMode enum."""

    def test_all_members_exist(self):
        expected = ["AUTO", "DRAFT", "ASK", "NEVER"]
        for name in expected:
            assert hasattr(PolicyMode, name)

    def test_values(self):
        assert PolicyMode.AUTO.value == "auto"
        assert PolicyMode.DRAFT.value == "draft"
        assert PolicyMode.ASK.value == "ask"
        assert PolicyMode.NEVER.value == "never"

    def test_member_count(self):
        assert len(PolicyMode) == 4


class TestLearningCategory:
    """Tests for the LearningCategory enum."""

    def test_all_members_exist(self):
        expected = ["PREFERENCE", "CONTACT", "SCHEDULE", "POLICY", "CORRECTION", "FACT"]
        for name in expected:
            assert hasattr(LearningCategory, name)

    def test_values(self):
        assert LearningCategory.PREFERENCE.value == "preference"
        assert LearningCategory.CONTACT.value == "contact"
        assert LearningCategory.SCHEDULE.value == "schedule"
        assert LearningCategory.POLICY.value == "policy"
        assert LearningCategory.CORRECTION.value == "correction"
        assert LearningCategory.FACT.value == "fact"

    def test_member_count(self):
        assert len(LearningCategory) == 6


class TestLearningSource:
    """Tests for the LearningSource enum."""

    def test_all_members_exist(self):
        expected = ["EXPLICIT", "INFERRED", "EMAIL", "CALENDAR", "DISCORD"]
        for name in expected:
            assert hasattr(LearningSource, name)

    def test_values(self):
        assert LearningSource.EXPLICIT.value == "explicit"
        assert LearningSource.INFERRED.value == "inferred"
        assert LearningSource.EMAIL.value == "email"
        assert LearningSource.CALENDAR.value == "calendar"
        assert LearningSource.DISCORD.value == "discord"

    def test_member_count(self):
        assert len(LearningSource) == 5


# ---------------------------------------------------------------------------
# CommunicationStyle tests
# ---------------------------------------------------------------------------


class TestCommunicationStyle:
    """Tests for the CommunicationStyle model."""

    def test_defaults(self):
        style = CommunicationStyle()
        assert style.formality == 0.5
        assert style.verbosity == 0.5
        assert style.emoji_usage == 0.3
        assert style.humor == 0.3

    def test_custom_values(self):
        style = CommunicationStyle(formality=0.8, verbosity=0.2, emoji_usage=0.9, humor=0.1)
        assert style.formality == 0.8
        assert style.verbosity == 0.2
        assert style.emoji_usage == 0.9
        assert style.humor == 0.1

    @pytest.mark.parametrize("field", ["formality", "verbosity", "emoji_usage", "humor"])
    def test_boundary_zero(self, field: str):
        style = CommunicationStyle(**{field: 0.0})
        assert getattr(style, field) == 0.0

    @pytest.mark.parametrize("field", ["formality", "verbosity", "emoji_usage", "humor"])
    def test_boundary_one(self, field: str):
        style = CommunicationStyle(**{field: 1.0})
        assert getattr(style, field) == 1.0

    @pytest.mark.parametrize("field", ["formality", "verbosity", "emoji_usage", "humor"])
    def test_rejects_below_zero(self, field: str):
        with pytest.raises(ValidationError):
            CommunicationStyle(**{field: -0.01})

    @pytest.mark.parametrize("field", ["formality", "verbosity", "emoji_usage", "humor"])
    def test_rejects_above_one(self, field: str):
        with pytest.raises(ValidationError):
            CommunicationStyle(**{field: 1.01})


# ---------------------------------------------------------------------------
# WorkingHours tests
# ---------------------------------------------------------------------------


class TestWorkingHours:
    """Tests for the WorkingHours model."""

    def test_defaults(self):
        wh = WorkingHours()
        assert wh.start == "09:00"
        assert wh.end == "17:00"
        assert wh.days == [1, 2, 3, 4, 5]

    def test_custom_valid_times(self):
        wh = WorkingHours(start="08:30", end="18:45")
        assert wh.start == "08:30"
        assert wh.end == "18:45"

    def test_boundary_times(self):
        wh = WorkingHours(start="00:00", end="23:59")
        assert wh.start == "00:00"
        assert wh.end == "23:59"

    @pytest.mark.parametrize(
        "bad_time",
        [
            "25:00",
            "24:00",
            "12:60",
            "99:99",
        ],
    )
    def test_rejects_out_of_range_time(self, bad_time: str):
        with pytest.raises(ValidationError, match="Invalid time"):
            WorkingHours(start=bad_time)

    def test_rejects_non_numeric_time(self):
        with pytest.raises(ValidationError, match="numeric HH:MM"):
            WorkingHours(start="xx:yy")

    def test_rejects_no_colon_separator(self):
        with pytest.raises(ValidationError, match="HH:MM"):
            WorkingHours(start="abc")

    @pytest.mark.parametrize(
        "bad_time",
        [
            "1:2:3",
            "12:30:00",
            "12",
        ],
    )
    def test_rejects_wrong_segment_count(self, bad_time: str):
        with pytest.raises(ValidationError, match="HH:MM"):
            WorkingHours(start=bad_time)

    def test_rejects_day_zero(self):
        with pytest.raises(ValidationError, match="1-7"):
            WorkingHours(days=[0, 1, 2])

    def test_rejects_day_eight(self):
        with pytest.raises(ValidationError, match="1-7"):
            WorkingHours(days=[1, 8])

    def test_days_deduplicated_and_sorted(self):
        wh = WorkingHours(days=[5, 3, 1, 3, 5])
        assert wh.days == [1, 3, 5]

    def test_weekend_days(self):
        wh = WorkingHours(days=[6, 7])
        assert wh.days == [6, 7]

    def test_all_seven_days(self):
        wh = WorkingHours(days=[7, 6, 5, 4, 3, 2, 1])
        assert wh.days == [1, 2, 3, 4, 5, 6, 7]


# ---------------------------------------------------------------------------
# PersonalProfile tests
# ---------------------------------------------------------------------------


class TestPersonalProfile:
    """Tests for the PersonalProfile model."""

    def test_creation_with_all_fields(self):
        ts = datetime(2024, 1, 15, 12, 0, 0)
        profile = PersonalProfile(
            user_id=12345,
            display_name="Alice",
            timezone="America/New_York",
            locale="en-US",
            working_hours=WorkingHours(start="08:00", end="16:00", days=[1, 2, 3, 4, 5]),
            communication_style=CommunicationStyle(formality=0.9),
            goals=["learn rust", "ship v2"],
            preferences={"theme": "dark", "notifications": True},
            updated_at=ts,
        )
        assert profile.user_id == 12345
        assert profile.display_name == "Alice"
        assert profile.timezone == "America/New_York"
        assert profile.locale == "en-US"
        assert profile.working_hours is not None
        assert profile.working_hours.start == "08:00"
        assert profile.communication_style is not None
        assert profile.communication_style.formality == 0.9
        assert profile.goals == ["learn rust", "ship v2"]
        assert profile.preferences == {"theme": "dark", "notifications": True}
        assert profile.updated_at == ts

    def test_creation_with_defaults(self):
        profile = PersonalProfile(user_id=1)
        assert profile.user_id == 1
        assert profile.display_name is None
        assert profile.timezone == "UTC"
        assert profile.locale == "en"
        assert profile.working_hours is None
        assert profile.communication_style is None
        assert profile.goals == []
        assert profile.preferences == {}
        assert isinstance(profile.updated_at, datetime)

    def test_to_db_row(self):
        profile = PersonalProfile(
            user_id=42,
            display_name="Bob",
            goals=["goal1"],
            preferences={"key": "val"},
            working_hours=WorkingHours(),
            communication_style=CommunicationStyle(),
        )
        row = profile.to_db_row()

        assert row["user_id"] == 42
        assert row["display_name"] == "Bob"
        assert row["timezone"] == "UTC"
        assert row["locale"] == "en"
        assert row["goals"] == ["goal1"]
        assert row["preferences"] == {"key": "val"}
        # working_hours should be serialised as dict
        assert isinstance(row["working_hours"], dict)
        assert row["working_hours"]["start"] == "09:00"
        # communication_style should be serialised as dict
        assert isinstance(row["communication_style"], dict)
        assert row["communication_style"]["formality"] == 0.5

    def test_to_db_row_none_nested(self):
        profile = PersonalProfile(user_id=1)
        row = profile.to_db_row()
        assert row["working_hours"] is None
        assert row["communication_style"] is None

    def test_to_db_row_excludes_updated_at(self):
        """to_db_row should not include updated_at (managed by DB)."""
        profile = PersonalProfile(user_id=1)
        row = profile.to_db_row()
        assert "updated_at" not in row

    def test_from_db_row(self):
        ts = datetime(2024, 6, 1, 10, 30)
        row = {
            "user_id": 99,
            "display_name": "Carol",
            "timezone": "Europe/London",
            "locale": "en-GB",
            "working_hours": {"start": "10:00", "end": "18:00", "days": [1, 2, 3, 4, 5]},
            "communication_style": {
                "formality": 0.8,
                "verbosity": 0.6,
                "emoji_usage": 0.1,
                "humor": 0.4,
            },
            "goals": ["read more"],
            "preferences": {"dark_mode": True},
            "updated_at": ts,
        }
        profile = PersonalProfile.from_db_row(row)

        assert profile.user_id == 99
        assert profile.display_name == "Carol"
        assert profile.timezone == "Europe/London"
        assert profile.locale == "en-GB"
        assert profile.working_hours is not None
        assert profile.working_hours.start == "10:00"
        assert profile.communication_style is not None
        assert profile.communication_style.formality == 0.8
        assert profile.goals == ["read more"]
        assert profile.preferences == {"dark_mode": True}
        assert profile.updated_at == ts

    def test_from_db_row_minimal(self):
        """from_db_row with only required field (user_id)."""
        profile = PersonalProfile.from_db_row({"user_id": 1})

        assert profile.user_id == 1
        assert profile.display_name is None
        assert profile.timezone == "UTC"
        assert profile.locale == "en"
        assert profile.working_hours is None
        assert profile.communication_style is None
        assert profile.goals == []
        assert profile.preferences == {}

    def test_from_db_row_none_nested_fields(self):
        """from_db_row gracefully handles None for nested models."""
        row = {
            "user_id": 5,
            "working_hours": None,
            "communication_style": None,
            "goals": None,
            "preferences": None,
        }
        profile = PersonalProfile.from_db_row(row)
        assert profile.working_hours is None
        assert profile.communication_style is None
        assert profile.goals == []
        assert profile.preferences == {}

    def test_from_db_row_goals_as_json_string(self):
        row = {
            "user_id": 7,
            "goals": json.dumps(["a", "b", "c"]),
        }
        profile = PersonalProfile.from_db_row(row)
        assert profile.goals == ["a", "b", "c"]

    def test_from_db_row_preferences_as_json_string(self):
        row = {
            "user_id": 7,
            "preferences": json.dumps({"x": 1}),
        }
        profile = PersonalProfile.from_db_row(row)
        assert profile.preferences == {"x": 1}

    def test_roundtrip_to_db_and_back(self):
        """Model -> to_db_row -> from_db_row preserves data."""
        original = PersonalProfile(
            user_id=42,
            display_name="Roundtrip",
            timezone="Asia/Tokyo",
            locale="ja",
            working_hours=WorkingHours(start="10:00", end="19:00", days=[1, 2, 3, 4, 5]),
            communication_style=CommunicationStyle(
                formality=0.7,
                verbosity=0.8,
                emoji_usage=0.0,
                humor=1.0,
            ),
            goals=["g1", "g2"],
            preferences={"a": "b"},
        )
        row = original.to_db_row()
        # Simulate the DB adding updated_at
        row["updated_at"] = original.updated_at
        restored = PersonalProfile.from_db_row(row)

        assert restored.user_id == original.user_id
        assert restored.display_name == original.display_name
        assert restored.timezone == original.timezone
        assert restored.locale == original.locale
        assert restored.working_hours is not None
        assert restored.working_hours.start == original.working_hours.start
        assert restored.working_hours.end == original.working_hours.end
        assert restored.working_hours.days == original.working_hours.days
        assert restored.communication_style is not None
        assert restored.communication_style.formality == original.communication_style.formality
        assert restored.communication_style.humor == original.communication_style.humor
        assert restored.goals == original.goals
        assert restored.preferences == original.preferences


# ---------------------------------------------------------------------------
# PersonalContact tests
# ---------------------------------------------------------------------------


class TestPersonalContact:
    """Tests for the PersonalContact model."""

    def test_creation_with_all_fields(self):
        ts = datetime(2024, 3, 10, 15, 0, 0)
        contact = PersonalContact(
            id=1,
            user_id=100,
            contact_email="alice@example.com",
            contact_name="Alice Wonderland",
            relationship=Relationship.COLLEAGUE,
            importance=0.9,
            company="Acme Corp",
            notes="Met at conference",
            last_interaction=ts,
            interaction_count=5,
            updated_at=ts,
        )
        assert contact.id == 1
        assert contact.user_id == 100
        assert contact.contact_email == "alice@example.com"
        assert contact.contact_name == "Alice Wonderland"
        assert contact.relationship == Relationship.COLLEAGUE
        assert contact.importance == 0.9
        assert contact.company == "Acme Corp"
        assert contact.notes == "Met at conference"
        assert contact.last_interaction == ts
        assert contact.interaction_count == 5

    def test_creation_with_defaults(self):
        contact = PersonalContact(user_id=200)
        assert contact.id is None
        assert contact.contact_email is None
        assert contact.contact_name is None
        assert contact.relationship == Relationship.OTHER
        assert contact.importance == 0.5
        assert contact.company is None
        assert contact.notes is None
        assert contact.last_interaction is None
        assert contact.interaction_count == 0
        assert isinstance(contact.updated_at, datetime)

    def test_importance_boundary_zero(self):
        contact = PersonalContact(user_id=1, importance=0.0)
        assert contact.importance == 0.0

    def test_importance_boundary_one(self):
        contact = PersonalContact(user_id=1, importance=1.0)
        assert contact.importance == 1.0

    def test_importance_rejects_below_zero(self):
        with pytest.raises(ValidationError):
            PersonalContact(user_id=1, importance=-0.1)

    def test_importance_rejects_above_one(self):
        with pytest.raises(ValidationError):
            PersonalContact(user_id=1, importance=1.1)

    def test_interaction_count_rejects_negative(self):
        with pytest.raises(ValidationError):
            PersonalContact(user_id=1, interaction_count=-1)

    def test_to_db_row(self):
        ts = datetime(2024, 5, 1)
        contact = PersonalContact(
            id=10,
            user_id=300,
            contact_email="bob@example.com",
            contact_name="Bob",
            relationship=Relationship.FRIEND,
            importance=0.7,
            company="Widgets Inc",
            notes="Good guy",
            last_interaction=ts,
            interaction_count=12,
        )
        row = contact.to_db_row()

        assert row["user_id"] == 300
        assert row["contact_email"] == "bob@example.com"
        assert row["contact_name"] == "Bob"
        assert row["relationship"] == "friend"
        assert row["importance"] == 0.7
        assert row["company"] == "Widgets Inc"
        assert row["notes"] == "Good guy"
        assert row["last_interaction"] == ts
        assert row["interaction_count"] == 12
        # id and updated_at should not be in the db row
        assert "id" not in row
        assert "updated_at" not in row

    def test_from_db_row(self):
        ts = datetime(2024, 7, 1)
        row = {
            "id": 55,
            "user_id": 400,
            "contact_email": "carol@example.com",
            "contact_name": "Carol",
            "relationship": "client",
            "importance": 0.8,
            "company": "BigCo",
            "notes": "VIP",
            "last_interaction": ts,
            "interaction_count": 20,
            "updated_at": ts,
        }
        contact = PersonalContact.from_db_row(row)

        assert contact.id == 55
        assert contact.user_id == 400
        assert contact.contact_email == "carol@example.com"
        assert contact.relationship == Relationship.CLIENT
        assert contact.importance == 0.8
        assert contact.interaction_count == 20

    def test_from_db_row_unknown_relationship_falls_back_to_other(self):
        row = {
            "user_id": 500,
            "relationship": "nemesis",
        }
        contact = PersonalContact.from_db_row(row)
        assert contact.relationship == Relationship.OTHER

    def test_from_db_row_missing_relationship_defaults_to_other(self):
        row = {"user_id": 500}
        contact = PersonalContact.from_db_row(row)
        assert contact.relationship == Relationship.OTHER

    def test_roundtrip_to_db_and_back(self):
        original = PersonalContact(
            user_id=600,
            contact_email="rt@test.com",
            contact_name="Roundtrip",
            relationship=Relationship.MANAGER,
            importance=0.95,
            company="RT Corp",
            interaction_count=3,
        )
        row = original.to_db_row()
        row["updated_at"] = original.updated_at
        restored = PersonalContact.from_db_row(row)

        assert restored.user_id == original.user_id
        assert restored.contact_email == original.contact_email
        assert restored.relationship == original.relationship
        assert restored.importance == original.importance
        assert restored.company == original.company
        assert restored.interaction_count == original.interaction_count


# ---------------------------------------------------------------------------
# PersonalPolicy tests
# ---------------------------------------------------------------------------


class TestPersonalPolicy:
    """Tests for the PersonalPolicy model."""

    def test_creation_with_all_fields(self):
        ts = datetime(2024, 4, 1, 9, 0)
        policy = PersonalPolicy(
            id=1,
            user_id=100,
            domain=PolicyDomain.EMAIL,
            action="send_reply",
            mode=PolicyMode.AUTO,
            conditions={"sender": "boss@example.com"},
            trust_score=0.85,
            created_at=ts,
            updated_at=ts,
        )
        assert policy.id == 1
        assert policy.user_id == 100
        assert policy.domain == PolicyDomain.EMAIL
        assert policy.action == "send_reply"
        assert policy.mode == PolicyMode.AUTO
        assert policy.conditions == {"sender": "boss@example.com"}
        assert policy.trust_score == 0.85

    def test_creation_with_defaults(self):
        policy = PersonalPolicy(
            user_id=200,
            domain=PolicyDomain.TASKS,
            action="create_task",
        )
        assert policy.id is None
        assert policy.mode == PolicyMode.ASK
        assert policy.conditions is None
        assert policy.trust_score == 0.0
        assert isinstance(policy.created_at, datetime)
        assert isinstance(policy.updated_at, datetime)

    def test_trust_score_boundary_zero(self):
        policy = PersonalPolicy(user_id=1, domain=PolicyDomain.GENERAL, action="a", trust_score=0.0)
        assert policy.trust_score == 0.0

    def test_trust_score_boundary_one(self):
        policy = PersonalPolicy(user_id=1, domain=PolicyDomain.GENERAL, action="a", trust_score=1.0)
        assert policy.trust_score == 1.0

    def test_trust_score_rejects_below_zero(self):
        with pytest.raises(ValidationError):
            PersonalPolicy(user_id=1, domain=PolicyDomain.GENERAL, action="a", trust_score=-0.1)

    def test_trust_score_rejects_above_one(self):
        with pytest.raises(ValidationError):
            PersonalPolicy(user_id=1, domain=PolicyDomain.GENERAL, action="a", trust_score=1.1)

    def test_to_db_row(self):
        policy = PersonalPolicy(
            id=5,
            user_id=300,
            domain=PolicyDomain.CALENDAR,
            action="schedule_meeting",
            mode=PolicyMode.DRAFT,
            conditions={"attendees_max": 5},
            trust_score=0.6,
        )
        row = policy.to_db_row()

        assert row["user_id"] == 300
        assert row["domain"] == "calendar"
        assert row["action"] == "schedule_meeting"
        assert row["mode"] == "draft"
        assert row["conditions"] == {"attendees_max": 5}
        assert row["trust_score"] == 0.6
        # id and timestamps should not be in db row
        assert "id" not in row
        assert "created_at" not in row
        assert "updated_at" not in row

    def test_from_db_row(self):
        ts = datetime(2024, 8, 1)
        row = {
            "id": 10,
            "user_id": 400,
            "domain": "email",
            "action": "archive",
            "mode": "never",
            "conditions": {"older_than_days": 30},
            "trust_score": 0.3,
            "created_at": ts,
            "updated_at": ts,
        }
        policy = PersonalPolicy.from_db_row(row)

        assert policy.id == 10
        assert policy.user_id == 400
        assert policy.domain == PolicyDomain.EMAIL
        assert policy.action == "archive"
        assert policy.mode == PolicyMode.NEVER
        assert policy.conditions == {"older_than_days": 30}
        assert policy.trust_score == 0.3

    def test_from_db_row_unknown_domain_falls_back_to_general(self):
        row = {
            "user_id": 500,
            "domain": "teleportation",
            "action": "beam_up",
        }
        policy = PersonalPolicy.from_db_row(row)
        assert policy.domain == PolicyDomain.GENERAL

    def test_from_db_row_unknown_mode_falls_back_to_ask(self):
        row = {
            "user_id": 500,
            "domain": "email",
            "action": "send",
            "mode": "yolo",
        }
        policy = PersonalPolicy.from_db_row(row)
        assert policy.mode == PolicyMode.ASK

    def test_from_db_row_conditions_as_json_string(self):
        conditions = {"max_retries": 3}
        row = {
            "user_id": 600,
            "domain": "tasks",
            "action": "retry",
            "conditions": json.dumps(conditions),
        }
        policy = PersonalPolicy.from_db_row(row)
        assert policy.conditions == conditions

    def test_from_db_row_conditions_none(self):
        row = {
            "user_id": 700,
            "domain": "general",
            "action": "noop",
            "conditions": None,
        }
        policy = PersonalPolicy.from_db_row(row)
        assert policy.conditions is None

    def test_roundtrip_to_db_and_back(self):
        original = PersonalPolicy(
            user_id=800,
            domain=PolicyDomain.DISCORD_OBSERVE,
            action="log_messages",
            mode=PolicyMode.AUTO,
            conditions={"channel": "general"},
            trust_score=0.99,
        )
        row = original.to_db_row()
        row["created_at"] = original.created_at
        row["updated_at"] = original.updated_at
        restored = PersonalPolicy.from_db_row(row)

        assert restored.user_id == original.user_id
        assert restored.domain == original.domain
        assert restored.action == original.action
        assert restored.mode == original.mode
        assert restored.conditions == original.conditions
        assert restored.trust_score == original.trust_score


# ---------------------------------------------------------------------------
# PersonalLearning tests
# ---------------------------------------------------------------------------


class TestPersonalLearning:
    """Tests for the PersonalLearning model."""

    def test_creation_with_all_fields(self):
        ts = datetime(2024, 2, 20, 14, 0)
        learning = PersonalLearning(
            id=1,
            user_id=100,
            category=LearningCategory.PREFERENCE,
            content="User prefers dark mode",
            confidence=0.9,
            source=LearningSource.EXPLICIT,
            confirmed=True,
            created_at=ts,
        )
        assert learning.id == 1
        assert learning.user_id == 100
        assert learning.category == LearningCategory.PREFERENCE
        assert learning.content == "User prefers dark mode"
        assert learning.confidence == 0.9
        assert learning.source == LearningSource.EXPLICIT
        assert learning.confirmed is True
        assert learning.created_at == ts

    def test_creation_with_defaults(self):
        learning = PersonalLearning(
            user_id=200,
            category=LearningCategory.FACT,
            content="Some fact",
            confidence=0.5,
            source=LearningSource.INFERRED,
        )
        assert learning.id is None
        assert learning.confirmed is False
        assert isinstance(learning.created_at, datetime)

    def test_empty_content_rejected(self):
        with pytest.raises(ValidationError):
            PersonalLearning(
                user_id=1,
                category=LearningCategory.FACT,
                content="",
                confidence=0.5,
                source=LearningSource.INFERRED,
            )

    def test_confidence_boundary_zero(self):
        learning = PersonalLearning(
            user_id=1,
            category=LearningCategory.FACT,
            content="x",
            confidence=0.0,
            source=LearningSource.INFERRED,
        )
        assert learning.confidence == 0.0

    def test_confidence_boundary_one(self):
        learning = PersonalLearning(
            user_id=1,
            category=LearningCategory.FACT,
            content="x",
            confidence=1.0,
            source=LearningSource.INFERRED,
        )
        assert learning.confidence == 1.0

    def test_confidence_rejects_below_zero(self):
        with pytest.raises(ValidationError):
            PersonalLearning(
                user_id=1,
                category=LearningCategory.FACT,
                content="x",
                confidence=-0.1,
                source=LearningSource.INFERRED,
            )

    def test_confidence_rejects_above_one(self):
        with pytest.raises(ValidationError):
            PersonalLearning(
                user_id=1,
                category=LearningCategory.FACT,
                content="x",
                confidence=1.1,
                source=LearningSource.INFERRED,
            )

    def test_to_db_row(self):
        learning = PersonalLearning(
            id=5,
            user_id=300,
            category=LearningCategory.SCHEDULE,
            content="Works 9-5 Mon-Fri",
            confidence=0.85,
            source=LearningSource.CALENDAR,
            confirmed=True,
        )
        row = learning.to_db_row()

        assert row["user_id"] == 300
        assert row["category"] == "schedule"
        assert row["content"] == "Works 9-5 Mon-Fri"
        assert row["confidence"] == 0.85
        assert row["source"] == "calendar"
        assert row["confirmed"] is True
        # id and created_at should not be in db row
        assert "id" not in row
        assert "created_at" not in row

    def test_from_db_row(self):
        ts = datetime(2024, 9, 1)
        row = {
            "id": 20,
            "user_id": 400,
            "category": "correction",
            "content": "Actually prefers light mode",
            "confidence": 0.95,
            "source": "explicit",
            "confirmed": True,
            "created_at": ts,
        }
        learning = PersonalLearning.from_db_row(row)

        assert learning.id == 20
        assert learning.user_id == 400
        assert learning.category == LearningCategory.CORRECTION
        assert learning.content == "Actually prefers light mode"
        assert learning.confidence == 0.95
        assert learning.source == LearningSource.EXPLICIT
        assert learning.confirmed is True
        assert learning.created_at == ts

    def test_from_db_row_unknown_category_falls_back_to_fact(self):
        row = {
            "user_id": 500,
            "category": "telepathy",
            "content": "Something unknown",
            "confidence": 0.5,
        }
        learning = PersonalLearning.from_db_row(row)
        assert learning.category == LearningCategory.FACT

    def test_from_db_row_unknown_source_falls_back_to_inferred(self):
        row = {
            "user_id": 500,
            "category": "fact",
            "content": "Something",
            "source": "crystal_ball",
        }
        learning = PersonalLearning.from_db_row(row)
        assert learning.source == LearningSource.INFERRED

    def test_from_db_row_missing_source_defaults_to_inferred(self):
        row = {
            "user_id": 500,
            "category": "fact",
            "content": "Something",
        }
        learning = PersonalLearning.from_db_row(row)
        assert learning.source == LearningSource.INFERRED

    def test_from_db_row_defaults(self):
        """from_db_row uses sensible defaults for missing optional fields."""
        row = {
            "user_id": 600,
            "category": "preference",
            "content": "Likes coffee",
        }
        learning = PersonalLearning.from_db_row(row)

        assert learning.id is None
        assert learning.confidence == 0.5
        assert learning.source == LearningSource.INFERRED
        assert learning.confirmed is False
        assert isinstance(learning.created_at, datetime)

    def test_roundtrip_to_db_and_back(self):
        original = PersonalLearning(
            user_id=700,
            category=LearningCategory.CONTACT,
            content="Knows John from Acme",
            confidence=0.75,
            source=LearningSource.DISCORD,
            confirmed=False,
        )
        row = original.to_db_row()
        row["created_at"] = original.created_at
        restored = PersonalLearning.from_db_row(row)

        assert restored.user_id == original.user_id
        assert restored.category == original.category
        assert restored.content == original.content
        assert restored.confidence == original.confidence
        assert restored.source == original.source
        assert restored.confirmed == original.confirmed
