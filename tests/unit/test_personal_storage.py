"""Unit tests for PostgreSQL-backed PersonalStorage.

Every test mocks asyncpg thoroughly so no real database connection is needed.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import asyncpg
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
from zetherion_ai.personal.storage import PERSONAL_SCHEMA_SQL, PersonalStorage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_pool():
    """Build a mock asyncpg pool with an acquirable connection.

    Returns (pool, conn) where ``pool.acquire()`` used as an async
    context manager yields ``conn``.
    """
    pool = AsyncMock()
    conn = AsyncMock()

    acq_cm = AsyncMock()
    acq_cm.__aenter__.return_value = conn
    pool.acquire = MagicMock(return_value=acq_cm)

    tx_cm = AsyncMock()
    tx_cm.__aenter__.return_value = conn
    conn.transaction = MagicMock(return_value=tx_cm)

    return pool, conn


def _profile_row(user_id: int = 12345) -> dict:
    """Return a dict mimicking a personal_profile DB row."""
    return {
        "user_id": user_id,
        "display_name": "Test User",
        "timezone": "America/New_York",
        "locale": "en",
        "working_hours": {"start": "09:00", "end": "17:00", "days": [1, 2, 3, 4, 5]},
        "communication_style": {
            "formality": 0.7,
            "verbosity": 0.5,
            "emoji_usage": 0.3,
            "humor": 0.3,
        },
        "goals": ["Learn Rust", "Ship feature"],
        "preferences": {"theme": "dark"},
        "updated_at": datetime(2025, 1, 15, 12, 0, 0),
    }


def _contact_row(
    *,
    id: int = 1,
    user_id: int = 12345,
    email: str = "alice@example.com",
    relationship: str = "colleague",
) -> dict:
    """Return a dict mimicking a personal_contacts DB row."""
    return {
        "id": id,
        "user_id": user_id,
        "contact_email": email,
        "contact_name": "Alice Smith",
        "relationship": relationship,
        "importance": 0.8,
        "company": "Acme Corp",
        "notes": "Met at conference",
        "last_interaction": datetime(2025, 1, 10, 9, 0, 0),
        "interaction_count": 5,
        "updated_at": datetime(2025, 1, 15, 12, 0, 0),
    }


def _policy_row(
    *,
    id: int = 1,
    user_id: int = 12345,
    domain: str = "email",
    action: str = "send_reply",
) -> dict:
    """Return a dict mimicking a personal_policies DB row."""
    return {
        "id": id,
        "user_id": user_id,
        "domain": domain,
        "action": action,
        "mode": "ask",
        "conditions": {"max_length": 500},
        "trust_score": 0.5,
        "created_at": datetime(2025, 1, 1, 0, 0, 0),
        "updated_at": datetime(2025, 1, 15, 12, 0, 0),
    }


def _learning_row(
    *,
    id: int = 1,
    user_id: int = 12345,
    category: str = "preference",
    confirmed: bool = False,
) -> dict:
    """Return a dict mimicking a personal_learnings DB row."""
    return {
        "id": id,
        "user_id": user_id,
        "category": category,
        "content": "User prefers dark mode",
        "confidence": 0.85,
        "source": "explicit",
        "confirmed": confirmed,
        "created_at": datetime(2025, 1, 15, 12, 0, 0),
    }


# ---------------------------------------------------------------------------
# Tests: ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    """Tests for PersonalStorage.ensure_schema."""

    @pytest.mark.asyncio
    async def test_executes_schema_sql(self):
        """ensure_schema calls conn.execute with PERSONAL_SCHEMA_SQL."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)

        await storage.ensure_schema()

        conn.execute.assert_awaited_once_with(PERSONAL_SCHEMA_SQL)

    @pytest.mark.asyncio
    async def test_raises_on_postgres_error(self):
        """ensure_schema re-raises asyncpg.PostgresError."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.side_effect = asyncpg.PostgresError("syntax error in DDL")

        with pytest.raises(asyncpg.PostgresError):
            await storage.ensure_schema()


# ---------------------------------------------------------------------------
# Tests: Profile CRUD
# ---------------------------------------------------------------------------


class TestProfileCRUD:
    """Tests for profile get/upsert/delete operations."""

    @pytest.mark.asyncio
    async def test_get_profile_returns_none_when_not_found(self):
        """get_profile returns None when fetchrow returns None."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchrow.return_value = None

        result = await storage.get_profile(12345)

        assert result is None
        conn.fetchrow.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_get_profile_returns_profile_when_found(self):
        """get_profile returns a PersonalProfile when a row is found."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchrow.return_value = _profile_row(user_id=12345)

        result = await storage.get_profile(12345)

        assert result is not None
        assert isinstance(result, PersonalProfile)
        assert result.user_id == 12345
        assert result.display_name == "Test User"
        assert result.timezone == "America/New_York"
        assert result.locale == "en"
        assert isinstance(result.working_hours, WorkingHours)
        assert result.working_hours.start == "09:00"
        assert result.working_hours.end == "17:00"
        assert result.working_hours.days == [1, 2, 3, 4, 5]
        assert isinstance(result.communication_style, CommunicationStyle)
        assert result.communication_style.formality == 0.7
        assert result.goals == ["Learn Rust", "Ship feature"]
        assert result.preferences == {"theme": "dark"}

    @pytest.mark.asyncio
    async def test_upsert_profile_full(self):
        """upsert_profile calls execute with correct params for a full profile."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "INSERT 0 1"

        profile = PersonalProfile(
            user_id=12345,
            display_name="Test User",
            timezone="America/New_York",
            locale="en",
            working_hours=WorkingHours(start="09:00", end="17:00", days=[1, 2, 3, 4, 5]),
            communication_style=CommunicationStyle(
                formality=0.7, verbosity=0.5, emoji_usage=0.3, humor=0.3
            ),
            goals=["Learn Rust", "Ship feature"],
            preferences={"theme": "dark"},
        )

        await storage.upsert_profile(profile)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args
        # Positional args: query, user_id, display_name, timezone, locale,
        # working_hours_json, communication_style_json, goals_json, preferences_json
        args = call_args[0]
        assert args[1] == 12345
        assert args[2] == "Test User"
        assert args[3] == "America/New_York"
        assert args[4] == "en"
        # working_hours and communication_style should be JSON strings
        assert '"start": "09:00"' in args[5]
        assert '"formality": 0.7' in args[6]
        # goals and preferences should be JSON strings
        assert "Learn Rust" in args[7]
        assert "dark" in args[8]

    @pytest.mark.asyncio
    async def test_upsert_profile_none_nested_objects(self):
        """upsert_profile handles None working_hours and communication_style."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "INSERT 0 1"

        profile = PersonalProfile(
            user_id=99999,
            display_name=None,
            timezone="UTC",
            locale="en",
            working_hours=None,
            communication_style=None,
            goals=[],
            preferences={},
        )

        await storage.upsert_profile(profile)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args
        args = call_args[0]
        assert args[1] == 99999
        assert args[2] is None  # display_name
        assert args[5] is None  # working_hours is None
        assert args[6] is None  # communication_style is None

    @pytest.mark.asyncio
    async def test_delete_profile_returns_true_on_delete_1(self):
        """delete_profile returns True when result is 'DELETE 1'."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "DELETE 1"

        result = await storage.delete_profile(12345)

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_profile_returns_false_on_delete_0(self):
        """delete_profile returns False when result is 'DELETE 0'."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "DELETE 0"

        result = await storage.delete_profile(99999)

        assert result is False


# ---------------------------------------------------------------------------
# Tests: Contact CRUD
# ---------------------------------------------------------------------------


class TestContactCRUD:
    """Tests for contact get/list/upsert/delete operations."""

    @pytest.mark.asyncio
    async def test_get_contact_returns_none(self):
        """get_contact returns None when fetchrow returns None."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchrow.return_value = None

        result = await storage.get_contact(12345, "nobody@example.com")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_contact_returns_contact(self):
        """get_contact returns a PersonalContact when a row is found."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchrow.return_value = _contact_row()

        result = await storage.get_contact(12345, "alice@example.com")

        assert result is not None
        assert isinstance(result, PersonalContact)
        assert result.user_id == 12345
        assert result.contact_email == "alice@example.com"
        assert result.contact_name == "Alice Smith"
        assert result.relationship == Relationship.COLLEAGUE
        assert result.importance == 0.8
        assert result.company == "Acme Corp"
        assert result.interaction_count == 5

    @pytest.mark.asyncio
    async def test_get_contact_by_id_returns_contact(self):
        """get_contact_by_id returns a PersonalContact when found."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchrow.return_value = _contact_row(id=42)

        result = await storage.get_contact_by_id(42)

        assert result is not None
        assert isinstance(result, PersonalContact)
        assert result.id == 42
        conn.fetchrow.assert_awaited_once()
        call_args = conn.fetchrow.call_args[0]
        assert "WHERE id = $1" in call_args[0]
        assert call_args[1] == 42

    @pytest.mark.asyncio
    async def test_get_contact_by_id_returns_none(self):
        """get_contact_by_id returns None when not found."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchrow.return_value = None

        result = await storage.get_contact_by_id(999)

        assert result is None

    @pytest.mark.asyncio
    async def test_list_contacts_no_filters(self):
        """list_contacts with no filters returns all contacts for user."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = [
            _contact_row(id=1, email="alice@example.com"),
            _contact_row(id=2, email="bob@example.com"),
        ]

        result = await storage.list_contacts(12345)

        assert len(result) == 2
        assert all(isinstance(c, PersonalContact) for c in result)
        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        query = call_args[0]
        assert "WHERE user_id = $1" in query
        where_clause = query.split("WHERE")[1].split("ORDER")[0]
        assert "AND relationship" not in where_clause
        # Params: user_id, limit
        assert call_args[1] == 12345
        assert call_args[2] == 100  # default limit

    @pytest.mark.asyncio
    async def test_list_contacts_with_relationship_filter(self):
        """list_contacts with relationship filter adds the AND clause."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = [_contact_row(relationship="friend")]

        result = await storage.list_contacts(12345, relationship="friend")

        assert len(result) == 1
        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        query = call_args[0]
        assert "AND relationship = $2" in query
        assert call_args[1] == 12345
        assert call_args[2] == "friend"
        assert call_args[3] == 100  # limit is $3

    @pytest.mark.asyncio
    async def test_list_contacts_with_min_importance(self):
        """list_contacts with min_importance filter adds the AND clause."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = [_contact_row()]

        result = await storage.list_contacts(12345, min_importance=0.7)

        assert len(result) == 1
        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        query = call_args[0]
        assert "AND importance >= $2" in query
        assert call_args[2] == 0.7
        assert call_args[3] == 100  # limit is $3

    @pytest.mark.asyncio
    async def test_list_contacts_with_both_filters(self):
        """list_contacts with relationship and min_importance filters."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = []

        result = await storage.list_contacts(12345, relationship="colleague", min_importance=0.5)

        assert result == []
        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        query = call_args[0]
        assert "AND relationship = $2" in query
        assert "AND importance >= $3" in query
        assert call_args[1] == 12345
        assert call_args[2] == "colleague"
        assert call_args[3] == 0.5
        assert call_args[4] == 100  # limit is $4

    @pytest.mark.asyncio
    async def test_upsert_contact_returns_id(self):
        """upsert_contact returns the contact ID from fetchval."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchval.return_value = 42

        contact = PersonalContact(
            user_id=12345,
            contact_email="alice@example.com",
            contact_name="Alice Smith",
            relationship=Relationship.COLLEAGUE,
            importance=0.8,
            company="Acme Corp",
            notes="Met at conference",
            last_interaction=datetime(2025, 1, 10, 9, 0, 0),
            interaction_count=5,
        )

        result = await storage.upsert_contact(contact)

        assert result == 42
        conn.fetchval.assert_awaited_once()
        call_args = conn.fetchval.call_args[0]
        assert "RETURNING id" in call_args[0]
        assert call_args[1] == 12345
        assert call_args[2] == "alice@example.com"
        assert call_args[3] == "Alice Smith"
        assert call_args[4] == "colleague"
        assert call_args[5] == 0.8

    @pytest.mark.asyncio
    async def test_delete_contact_returns_true(self):
        """delete_contact returns True when result is 'DELETE 1'."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "DELETE 1"

        result = await storage.delete_contact(12345, "alice@example.com")

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_contact_returns_false(self):
        """delete_contact returns False when result is 'DELETE 0'."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "DELETE 0"

        result = await storage.delete_contact(12345, "nobody@example.com")

        assert result is False

    @pytest.mark.asyncio
    async def test_increment_contact_interaction_calls_execute(self):
        """increment_contact_interaction calls execute with correct SQL."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "UPDATE 1"

        await storage.increment_contact_interaction(12345, "alice@example.com")

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        query = call_args[0]
        assert "interaction_count = interaction_count + 1" in query
        assert "last_interaction = now()" in query
        assert call_args[1] == 12345
        assert call_args[2] == "alice@example.com"


# ---------------------------------------------------------------------------
# Tests: Policy CRUD
# ---------------------------------------------------------------------------


class TestPolicyCRUD:
    """Tests for policy get/list/upsert/delete operations."""

    @pytest.mark.asyncio
    async def test_get_policy_returns_none(self):
        """get_policy returns None when fetchrow returns None."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchrow.return_value = None

        result = await storage.get_policy(12345, "email", "send_reply")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_policy_returns_policy(self):
        """get_policy returns a PersonalPolicy when a row is found."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchrow.return_value = _policy_row()

        result = await storage.get_policy(12345, "email", "send_reply")

        assert result is not None
        assert isinstance(result, PersonalPolicy)
        assert result.user_id == 12345
        assert result.domain == PolicyDomain.EMAIL
        assert result.action == "send_reply"
        assert result.mode == PolicyMode.ASK
        assert result.conditions == {"max_length": 500}
        assert result.trust_score == 0.5

    @pytest.mark.asyncio
    async def test_list_policies_without_domain_filter(self):
        """list_policies without domain returns all policies for user."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = [
            _policy_row(id=1, domain="email", action="send_reply"),
            _policy_row(id=2, domain="calendar", action="create_event"),
        ]

        result = await storage.list_policies(12345)

        assert len(result) == 2
        assert all(isinstance(p, PersonalPolicy) for p in result)
        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        query = call_args[0]
        assert "WHERE user_id = $1" in query
        assert "ORDER BY domain, action" in query
        assert call_args[1] == 12345

    @pytest.mark.asyncio
    async def test_list_policies_with_domain_filter(self):
        """list_policies with domain filter adds the AND clause."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = [_policy_row(domain="email")]

        result = await storage.list_policies(12345, domain="email")

        assert len(result) == 1
        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        query = call_args[0]
        assert "AND domain = $2" in query
        assert "ORDER BY action" in query
        assert call_args[1] == 12345
        assert call_args[2] == "email"

    @pytest.mark.asyncio
    async def test_upsert_policy_returns_id(self):
        """upsert_policy returns the policy ID from fetchval."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchval.return_value = 7

        policy = PersonalPolicy(
            user_id=12345,
            domain=PolicyDomain.EMAIL,
            action="send_reply",
            mode=PolicyMode.ASK,
            conditions={"max_length": 500},
            trust_score=0.5,
        )

        result = await storage.upsert_policy(policy)

        assert result == 7
        conn.fetchval.assert_awaited_once()
        call_args = conn.fetchval.call_args[0]
        assert "RETURNING id" in call_args[0]
        assert call_args[1] == 12345
        assert call_args[2] == "email"
        assert call_args[3] == "send_reply"
        assert call_args[4] == "ask"
        # conditions should be JSON string
        assert "max_length" in call_args[5]
        assert call_args[6] == 0.5

    @pytest.mark.asyncio
    async def test_upsert_policy_none_conditions(self):
        """upsert_policy handles None conditions."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchval.return_value = 8

        policy = PersonalPolicy(
            user_id=12345,
            domain=PolicyDomain.GENERAL,
            action="do_thing",
            mode=PolicyMode.NEVER,
            conditions=None,
            trust_score=0.0,
        )

        result = await storage.upsert_policy(policy)

        assert result == 8
        call_args = conn.fetchval.call_args[0]
        assert call_args[5] is None  # conditions is None

    @pytest.mark.asyncio
    async def test_delete_policy_returns_true(self):
        """delete_policy returns True when result is 'DELETE 1'."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "DELETE 1"

        result = await storage.delete_policy(12345, "email", "send_reply")

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_policy_returns_false(self):
        """delete_policy returns False when result is 'DELETE 0'."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "DELETE 0"

        result = await storage.delete_policy(12345, "email", "nonexistent")

        assert result is False

    @pytest.mark.asyncio
    async def test_update_trust_score_returns_new_score(self):
        """update_trust_score returns the new trust score."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchval.return_value = 0.65

        result = await storage.update_trust_score(12345, "email", "send_reply", 0.15)

        assert result == 0.65
        conn.fetchval.assert_awaited_once()
        call_args = conn.fetchval.call_args[0]
        query = call_args[0]
        assert "GREATEST(0.0, LEAST(0.95, trust_score + $4))" in query
        assert "RETURNING trust_score" in query
        assert call_args[1] == 12345
        assert call_args[2] == "email"
        assert call_args[3] == "send_reply"
        assert call_args[4] == 0.15

    @pytest.mark.asyncio
    async def test_update_trust_score_returns_none_for_nonexistent(self):
        """update_trust_score returns None when the policy doesn't exist."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchval.return_value = None

        result = await storage.update_trust_score(12345, "email", "nonexistent", 0.1)

        assert result is None

    @pytest.mark.asyncio
    async def test_reset_domain_trust_returns_count(self):
        """reset_domain_trust returns count of affected rows."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "UPDATE 3"

        result = await storage.reset_domain_trust(12345, "email")

        assert result == 3
        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        query = call_args[0]
        assert "SET trust_score = 0.0" in query
        assert call_args[1] == 12345
        assert call_args[2] == "email"

    @pytest.mark.asyncio
    async def test_reset_domain_trust_returns_zero_when_none_affected(self):
        """reset_domain_trust returns 0 when no rows match."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "UPDATE 0"

        result = await storage.reset_domain_trust(12345, "nonexistent")

        assert result == 0


# ---------------------------------------------------------------------------
# Tests: Learning CRUD
# ---------------------------------------------------------------------------


class TestLearningCRUD:
    """Tests for learning add/list/confirm/delete operations."""

    @pytest.mark.asyncio
    async def test_add_learning_returns_id(self):
        """add_learning returns the learning ID from fetchval."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchval.return_value = 10

        learning = PersonalLearning(
            user_id=12345,
            category=LearningCategory.PREFERENCE,
            content="User prefers dark mode",
            confidence=0.85,
            source=LearningSource.EXPLICIT,
            confirmed=False,
        )

        result = await storage.add_learning(learning)

        assert result == 10
        conn.fetchval.assert_awaited_once()
        call_args = conn.fetchval.call_args[0]
        assert "RETURNING id" in call_args[0]
        assert call_args[1] == 12345
        assert call_args[2] == "preference"
        assert call_args[3] == "User prefers dark mode"
        assert call_args[4] == 0.85
        assert call_args[5] == "explicit"
        assert call_args[6] is False

    @pytest.mark.asyncio
    async def test_list_learnings_no_filters(self):
        """list_learnings with no filters returns all learnings for user."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = [
            _learning_row(id=1, category="preference"),
            _learning_row(id=2, category="fact"),
        ]

        result = await storage.list_learnings(12345)

        assert len(result) == 2
        assert all(isinstance(item, PersonalLearning) for item in result)
        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        query = call_args[0]
        assert "WHERE user_id = $1" in query
        assert "AND category" not in query
        assert "AND confirmed" not in query
        assert "AND confidence" not in query
        assert call_args[1] == 12345
        assert call_args[2] == 100  # default limit

    @pytest.mark.asyncio
    async def test_list_learnings_with_category_filter(self):
        """list_learnings with category filter adds the AND clause."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = [_learning_row(category="preference")]

        result = await storage.list_learnings(12345, category="preference")

        assert len(result) == 1
        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        query = call_args[0]
        assert "AND category = $2" in query
        assert call_args[2] == "preference"
        assert call_args[3] == 100  # limit is $3

    @pytest.mark.asyncio
    async def test_list_learnings_with_confirmed_only(self):
        """list_learnings with confirmed_only adds AND confirmed = TRUE."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = [_learning_row(confirmed=True)]

        result = await storage.list_learnings(12345, confirmed_only=True)

        assert len(result) == 1
        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        query = call_args[0]
        assert "AND confirmed = TRUE" in query
        # confirmed_only doesn't add a param, so limit is still $2
        assert call_args[1] == 12345
        assert call_args[2] == 100

    @pytest.mark.asyncio
    async def test_list_learnings_with_min_confidence(self):
        """list_learnings with min_confidence filter adds the AND clause."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = [_learning_row()]

        result = await storage.list_learnings(12345, min_confidence=0.8)

        assert len(result) == 1
        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        query = call_args[0]
        assert "AND confidence >= $2" in query
        assert call_args[2] == 0.8
        assert call_args[3] == 100  # limit is $3

    @pytest.mark.asyncio
    async def test_list_learnings_with_all_filters(self):
        """list_learnings with category, confirmed_only, and min_confidence."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = []

        result = await storage.list_learnings(
            12345,
            category="preference",
            confirmed_only=True,
            min_confidence=0.9,
            limit=50,
        )

        assert result == []
        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        query = call_args[0]
        assert "AND category = $2" in query
        assert "AND confirmed = TRUE" in query
        assert "AND confidence >= $3" in query
        assert call_args[1] == 12345
        assert call_args[2] == "preference"
        assert call_args[3] == 0.9
        assert call_args[4] == 50  # custom limit

    @pytest.mark.asyncio
    async def test_confirm_learning_returns_true(self):
        """confirm_learning returns True when result is 'UPDATE 1'."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "UPDATE 1"

        result = await storage.confirm_learning(10)

        assert result is True
        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        assert "SET confirmed = TRUE" in call_args[0]
        assert call_args[1] == 10

    @pytest.mark.asyncio
    async def test_confirm_learning_returns_false(self):
        """confirm_learning returns False when result is 'UPDATE 0'."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "UPDATE 0"

        result = await storage.confirm_learning(999)

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_learning_returns_true(self):
        """delete_learning returns True when result is 'DELETE 1'."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "DELETE 1"

        result = await storage.delete_learning(10)

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_learning_returns_false(self):
        """delete_learning returns False when result is 'DELETE 0'."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "DELETE 0"

        result = await storage.delete_learning(999)

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_learnings_by_category_returns_count(self):
        """delete_learnings_by_category returns count of deleted rows."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "DELETE 5"

        result = await storage.delete_learnings_by_category(12345, "preference")

        assert result == 5
        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        query = call_args[0]
        assert "WHERE user_id = $1 AND category = $2" in query
        assert call_args[1] == 12345
        assert call_args[2] == "preference"

    @pytest.mark.asyncio
    async def test_delete_learnings_by_category_returns_zero(self):
        """delete_learnings_by_category returns 0 when no rows match."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "DELETE 0"

        result = await storage.delete_learnings_by_category(12345, "nonexistent")

        assert result == 0


# ---------------------------------------------------------------------------
# Tests: Convenience wrappers
# ---------------------------------------------------------------------------


class TestConvenienceWrappers:
    """Tests for _fetchval, _fetchrow, _fetch, and _execute pool wrappers."""

    @pytest.mark.asyncio
    async def test_fetchval_delegates_to_conn(self):
        """_fetchval acquires a connection and calls conn.fetchval."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchval.return_value = 42

        result = await storage._fetchval("SELECT 1")

        assert result == 42
        conn.fetchval.assert_awaited_once_with("SELECT 1")

    @pytest.mark.asyncio
    async def test_fetchval_passes_args(self):
        """_fetchval forwards positional arguments."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetchval.return_value = "ok"

        await storage._fetchval("SELECT $1", 123)

        conn.fetchval.assert_awaited_once_with("SELECT $1", 123)

    @pytest.mark.asyncio
    async def test_fetchrow_delegates_to_conn(self):
        """_fetchrow acquires a connection and calls conn.fetchrow."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        row = {"id": 1, "name": "test"}
        conn.fetchrow.return_value = row

        result = await storage._fetchrow("SELECT * FROM t WHERE id = $1", 1)

        assert result == row
        conn.fetchrow.assert_awaited_once_with("SELECT * FROM t WHERE id = $1", 1)

    @pytest.mark.asyncio
    async def test_fetch_delegates_to_conn(self):
        """_fetch acquires a connection and calls conn.fetch."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        rows = [{"id": 1}, {"id": 2}]
        conn.fetch.return_value = rows

        result = await storage._fetch("SELECT * FROM t")

        assert result == rows
        conn.fetch.assert_awaited_once_with("SELECT * FROM t")

    @pytest.mark.asyncio
    async def test_execute_delegates_to_conn(self):
        """_execute acquires a connection and calls conn.execute."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = "INSERT 0 1"

        result = await storage._execute("INSERT INTO t VALUES ($1)", "val")

        assert result == "INSERT 0 1"
        conn.execute.assert_awaited_once_with("INSERT INTO t VALUES ($1)", "val")


# ---------------------------------------------------------------------------
# Tests: Edge cases and additional coverage
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Edge-case tests for additional coverage."""

    @pytest.mark.asyncio
    async def test_get_profile_row_with_string_goals(self):
        """get_profile handles goals stored as a JSON string."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        row = _profile_row()
        # Simulate goals stored as a JSON string (some DB drivers do this)
        row["goals"] = '["Learn Rust", "Ship feature"]'
        conn.fetchrow.return_value = row

        result = await storage.get_profile(12345)

        assert result is not None
        assert result.goals == ["Learn Rust", "Ship feature"]

    @pytest.mark.asyncio
    async def test_get_profile_row_with_string_preferences(self):
        """get_profile handles preferences stored as a JSON string."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        row = _profile_row()
        row["preferences"] = '{"theme": "dark"}'
        conn.fetchrow.return_value = row

        result = await storage.get_profile(12345)

        assert result is not None
        assert result.preferences == {"theme": "dark"}

    @pytest.mark.asyncio
    async def test_get_profile_row_with_null_nested_objects(self):
        """get_profile handles None working_hours and communication_style."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        row = _profile_row()
        row["working_hours"] = None
        row["communication_style"] = None
        conn.fetchrow.return_value = row

        result = await storage.get_profile(12345)

        assert result is not None
        assert result.working_hours is None
        assert result.communication_style is None

    @pytest.mark.asyncio
    async def test_get_contact_with_invalid_relationship(self):
        """get_contact falls back to OTHER for unknown relationship string."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        row = _contact_row(relationship="unknown_type")
        conn.fetchrow.return_value = row

        result = await storage.get_contact(12345, "alice@example.com")

        assert result is not None
        assert result.relationship == Relationship.OTHER

    @pytest.mark.asyncio
    async def test_get_policy_with_string_conditions(self):
        """get_policy handles conditions stored as a JSON string."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        row = _policy_row()
        row["conditions"] = '{"max_length": 500}'
        conn.fetchrow.return_value = row

        result = await storage.get_policy(12345, "email", "send_reply")

        assert result is not None
        assert result.conditions == {"max_length": 500}

    @pytest.mark.asyncio
    async def test_get_policy_with_invalid_domain(self):
        """get_policy falls back to GENERAL for unknown domain string."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        row = _policy_row(domain="unknown_domain")
        conn.fetchrow.return_value = row

        result = await storage.get_policy(12345, "unknown_domain", "send_reply")

        assert result is not None
        assert result.domain == PolicyDomain.GENERAL

    @pytest.mark.asyncio
    async def test_get_policy_with_invalid_mode(self):
        """get_policy falls back to ASK for unknown mode string."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        row = _policy_row()
        row["mode"] = "unknown_mode"
        conn.fetchrow.return_value = row

        result = await storage.get_policy(12345, "email", "send_reply")

        assert result is not None
        assert result.mode == PolicyMode.ASK

    @pytest.mark.asyncio
    async def test_reset_domain_trust_handles_empty_result(self):
        """reset_domain_trust returns 0 when execute returns empty string."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = ""

        result = await storage.reset_domain_trust(12345, "email")

        assert result == 0

    @pytest.mark.asyncio
    async def test_delete_learnings_by_category_handles_empty_result(self):
        """delete_learnings_by_category returns 0 when execute returns empty string."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.execute.return_value = ""

        result = await storage.delete_learnings_by_category(12345, "preference")

        assert result == 0

    @pytest.mark.asyncio
    async def test_list_contacts_custom_limit(self):
        """list_contacts respects a custom limit parameter."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = []

        await storage.list_contacts(12345, limit=10)

        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        # With no filters, limit is $2
        assert call_args[2] == 10

    @pytest.mark.asyncio
    async def test_list_learnings_custom_limit(self):
        """list_learnings respects a custom limit parameter."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        conn.fetch.return_value = []

        await storage.list_learnings(12345, limit=25)

        conn.fetch.assert_awaited_once()
        call_args = conn.fetch.call_args[0]
        # With no filters, limit is $2
        assert call_args[2] == 25

    @pytest.mark.asyncio
    async def test_get_learning_with_invalid_category(self):
        """Learning from_db_row falls back to FACT for unknown category."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        row = _learning_row(category="unknown_category")
        conn.fetch.return_value = [row]

        result = await storage.list_learnings(12345)

        assert len(result) == 1
        assert result[0].category == LearningCategory.FACT

    @pytest.mark.asyncio
    async def test_get_learning_with_invalid_source(self):
        """Learning from_db_row falls back to INFERRED for unknown source."""
        pool, conn = _make_mock_pool()
        storage = PersonalStorage(pool)
        row = _learning_row()
        row["source"] = "unknown_source"
        conn.fetch.return_value = [row]

        result = await storage.list_learnings(12345)

        assert len(result) == 1
        assert result[0].source == LearningSource.INFERRED

    def test_constructor_stores_pool(self):
        """PersonalStorage constructor stores the pool reference."""
        pool = AsyncMock()
        storage = PersonalStorage(pool)
        assert storage._pool is pool
