"""Unit tests for the personal context layer.

Tests DecisionContext dataclass and DecisionContextBuilder for building
compact decision packs from personal storage.
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zetherion_ai.personal.context import DecisionContext, DecisionContextBuilder
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


def _make_storage() -> AsyncMock:
    """Create a mock PersonalStorage with all methods configured."""
    storage = AsyncMock(spec=PersonalStorage)
    storage.get_profile = AsyncMock(return_value=None)
    storage.get_contact = AsyncMock(return_value=None)
    storage.list_contacts = AsyncMock(return_value=[])
    storage.list_policies = AsyncMock(return_value=[])
    storage.list_learnings = AsyncMock(return_value=[])
    return storage


class TestDecisionContext:
    """Tests for DecisionContext dataclass."""

    def test_empty_context_has_defaults(self) -> None:
        """Empty context has all empty defaults."""
        ctx = DecisionContext()
        assert ctx.user_profile == {}
        assert ctx.relevant_contacts == []
        assert ctx.schedule_constraints == []
        assert ctx.active_policies == []
        assert ctx.recent_learnings == []

    def test_is_empty_true_when_all_fields_empty(self) -> None:
        """is_empty returns True when all fields are empty."""
        ctx = DecisionContext()
        assert ctx.is_empty is True

    def test_is_empty_false_with_profile(self) -> None:
        """is_empty returns False when user_profile has data."""
        ctx = DecisionContext(user_profile={"display_name": "Alice"})
        assert ctx.is_empty is False

    def test_is_empty_false_with_contacts(self) -> None:
        """is_empty returns False when relevant_contacts has data."""
        ctx = DecisionContext(relevant_contacts=[{"contact_name": "Bob"}])
        assert ctx.is_empty is False

    def test_is_empty_false_with_constraints(self) -> None:
        """is_empty returns False when schedule_constraints has data."""
        ctx = DecisionContext(schedule_constraints=[{"event": "Meeting"}])
        assert ctx.is_empty is False

    def test_is_empty_false_with_policies(self) -> None:
        """is_empty returns False when active_policies has data."""
        ctx = DecisionContext(active_policies=[{"domain": "email"}])
        assert ctx.is_empty is False

    def test_is_empty_false_with_learnings(self) -> None:
        """is_empty returns False when recent_learnings has data."""
        ctx = DecisionContext(recent_learnings=[{"content": "Prefers coffee"}])
        assert ctx.is_empty is False

    def test_to_prompt_fragment_empty_returns_empty_string(self) -> None:
        """to_prompt_fragment with empty context returns empty string."""
        ctx = DecisionContext()
        assert ctx.to_prompt_fragment() == ""

    def test_to_prompt_fragment_with_full_profile(self) -> None:
        """to_prompt_fragment with full profile includes name, timezone,
        style, goals."""
        ctx = DecisionContext(
            user_profile={
                "display_name": "Alice Johnson",
                "timezone": "America/New_York",
                "communication_style": {"formality": 0.5},
                "goals": ["Learn Python", "Build a bot", "Deploy to prod"],
            }
        )
        fragment = ctx.to_prompt_fragment()
        assert "Alice Johnson" in fragment
        assert "America/New_York" in fragment
        assert "balanced" in fragment
        assert "Learn Python, Build a bot, Deploy to prod" in fragment

    def test_to_prompt_fragment_formal_style(self) -> None:
        """to_prompt_fragment with formality > 0.6 shows 'formal'."""
        ctx = DecisionContext(
            user_profile={
                "display_name": "Dr. Smith",
                "timezone": "UTC",
                "communication_style": {"formality": 0.8},
            }
        )
        fragment = ctx.to_prompt_fragment()
        assert "formal" in fragment

    def test_to_prompt_fragment_casual_style(self) -> None:
        """to_prompt_fragment with formality < 0.4 shows 'casual'."""
        ctx = DecisionContext(
            user_profile={
                "display_name": "Jake",
                "timezone": "UTC",
                "communication_style": {"formality": 0.2},
            }
        )
        fragment = ctx.to_prompt_fragment()
        assert "casual" in fragment

    def test_to_prompt_fragment_balanced_style(self) -> None:
        """to_prompt_fragment with formality 0.4-0.6 shows 'balanced'."""
        ctx = DecisionContext(
            user_profile={
                "display_name": "Sam",
                "timezone": "UTC",
                "communication_style": {"formality": 0.5},
            }
        )
        fragment = ctx.to_prompt_fragment()
        assert "balanced" in fragment

    def test_to_prompt_fragment_with_contacts(self) -> None:
        """to_prompt_fragment with contacts includes contact names."""
        ctx = DecisionContext(
            relevant_contacts=[
                {
                    "contact_name": "Bob Smith",
                    "contact_email": "bob@example.com",
                    "relationship": "colleague",
                },
                {
                    "contact_name": "Jane Doe",
                    "contact_email": "jane@example.com",
                    "relationship": "client",
                },
            ]
        )
        fragment = ctx.to_prompt_fragment()
        assert "Bob Smith" in fragment
        assert "colleague" in fragment
        assert "Jane Doe" in fragment
        assert "client" in fragment

    def test_to_prompt_fragment_with_policies(self) -> None:
        """to_prompt_fragment with policies includes domain/action/mode."""
        ctx = DecisionContext(
            active_policies=[
                {"domain": "email", "action": "reply", "mode": "draft"},
                {"domain": "calendar", "action": "schedule", "mode": "ask"},
            ]
        )
        fragment = ctx.to_prompt_fragment()
        assert "email/reply: draft" in fragment
        assert "calendar/schedule: ask" in fragment

    def test_to_prompt_fragment_with_learnings(self) -> None:
        """to_prompt_fragment with learnings includes content."""
        ctx = DecisionContext(
            recent_learnings=[
                {"content": "User prefers coffee in the morning"},
                {"content": "User dislikes meetings before 10am"},
            ]
        )
        fragment = ctx.to_prompt_fragment()
        assert "User prefers coffee in the morning" in fragment
        assert "User dislikes meetings before 10am" in fragment

    def test_to_prompt_fragment_limits_contacts(self) -> None:
        """to_prompt_fragment shows max 3 contacts."""
        ctx = DecisionContext(
            relevant_contacts=[
                {"contact_name": f"Contact {i}", "relationship": "colleague"} for i in range(10)
            ]
        )
        fragment = ctx.to_prompt_fragment()
        lines = fragment.split("\n")
        contact_line = [ln for ln in lines if "Relevant contacts:" in ln][0]
        # Should only show first 3
        assert "Contact 0" in contact_line
        assert "Contact 1" in contact_line
        assert "Contact 2" in contact_line
        assert "Contact 3" not in contact_line

    def test_to_prompt_fragment_limits_policies(self) -> None:
        """to_prompt_fragment shows max 3 policies."""
        ctx = DecisionContext(
            active_policies=[
                {
                    "domain": f"domain{i}",
                    "action": f"action{i}",
                    "mode": "auto",
                }
                for i in range(10)
            ]
        )
        fragment = ctx.to_prompt_fragment()
        lines = fragment.split("\n")
        policy_line = [ln for ln in lines if "Active policies:" in ln][0]
        # Should only show first 3
        assert "domain0" in policy_line
        assert "domain1" in policy_line
        assert "domain2" in policy_line
        assert "domain3" not in policy_line

    def test_to_prompt_fragment_limits_learnings(self) -> None:
        """to_prompt_fragment shows max 3 learnings."""
        ctx = DecisionContext(recent_learnings=[{"content": f"Learning {i}"} for i in range(10)])
        fragment = ctx.to_prompt_fragment()
        lines = fragment.split("\n")
        learning_line = [ln for ln in lines if "Recent learnings:" in ln][0]
        # Should only show first 3
        assert "Learning 0" in learning_line
        assert "Learning 1" in learning_line
        assert "Learning 2" in learning_line
        assert "Learning 3" not in learning_line


class TestDecisionContextBuilder:
    """Tests for DecisionContextBuilder."""

    @pytest.mark.asyncio
    async def test_build_with_profile(self) -> None:
        """build with profile populates user_profile."""
        storage = _make_storage()
        profile = PersonalProfile(
            user_id=123,
            display_name="Alice",
            timezone="America/New_York",
            locale="en",
        )
        storage.get_profile.return_value = profile

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123)

        assert ctx.user_profile["display_name"] == "Alice"
        assert ctx.user_profile["timezone"] == "America/New_York"
        storage.get_profile.assert_awaited_once_with(123)

    @pytest.mark.asyncio
    async def test_build_with_no_profile(self) -> None:
        """build with no profile leaves user_profile empty."""
        storage = _make_storage()
        storage.get_profile.return_value = None

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123)

        assert ctx.user_profile == {}
        storage.get_profile.assert_awaited_once_with(123)

    @pytest.mark.asyncio
    async def test_build_with_mentioned_emails(self) -> None:
        """build with mentioned_emails fetches specific contacts."""
        storage = _make_storage()
        contact1 = PersonalContact(
            user_id=123,
            contact_email="alice@example.com",
            contact_name="Alice",
            relationship=Relationship.COLLEAGUE,
        )
        contact2 = PersonalContact(
            user_id=123,
            contact_email="bob@example.com",
            contact_name="Bob",
            relationship=Relationship.CLIENT,
        )
        storage.get_contact.side_effect = [contact1, contact2]

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123, mentioned_emails=["alice@example.com", "bob@example.com"])

        assert len(ctx.relevant_contacts) == 2
        assert ctx.relevant_contacts[0]["contact_name"] == "Alice"
        assert ctx.relevant_contacts[1]["contact_name"] == "Bob"
        assert storage.get_contact.await_count == 2

    @pytest.mark.asyncio
    async def test_build_with_no_mentioned_emails(self) -> None:
        """build with no mentioned_emails fetches top contacts by
        importance."""
        storage = _make_storage()
        contacts = [
            PersonalContact(
                user_id=123,
                contact_email="alice@example.com",
                contact_name="Alice",
                relationship=Relationship.COLLEAGUE,
                importance=0.9,
            ),
            PersonalContact(
                user_id=123,
                contact_email="bob@example.com",
                contact_name="Bob",
                relationship=Relationship.CLIENT,
                importance=0.8,
            ),
        ]
        storage.list_contacts.return_value = contacts

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123)

        assert len(ctx.relevant_contacts) == 2
        assert ctx.relevant_contacts[0]["contact_name"] == "Alice"
        assert ctx.relevant_contacts[1]["contact_name"] == "Bob"
        storage.list_contacts.assert_awaited_once_with(123, limit=5)

    @pytest.mark.asyncio
    async def test_build_contact_not_found_for_email(self) -> None:
        """build with contact not found for email skips it."""
        storage = _make_storage()
        contact1 = PersonalContact(
            user_id=123,
            contact_email="alice@example.com",
            contact_name="Alice",
            relationship=Relationship.COLLEAGUE,
        )
        storage.get_contact.side_effect = [contact1, None]

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(
            123,
            mentioned_emails=["alice@example.com", "notfound@example.com"],
        )

        # Only Alice should be in the list
        assert len(ctx.relevant_contacts) == 1
        assert ctx.relevant_contacts[0]["contact_name"] == "Alice"

    @pytest.mark.asyncio
    async def test_build_with_policies(self) -> None:
        """build with policies populates active_policies."""
        storage = _make_storage()
        policies = [
            PersonalPolicy(
                user_id=123,
                domain=PolicyDomain.EMAIL,
                action="reply",
                mode=PolicyMode.DRAFT,
            ),
            PersonalPolicy(
                user_id=123,
                domain=PolicyDomain.CALENDAR,
                action="schedule",
                mode=PolicyMode.ASK,
            ),
        ]
        storage.list_policies.return_value = policies

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123)

        assert len(ctx.active_policies) == 2
        assert ctx.active_policies[0]["domain"] == "email"
        assert ctx.active_policies[0]["action"] == "reply"
        assert ctx.active_policies[1]["domain"] == "calendar"
        storage.list_policies.assert_awaited_once_with(123)

    @pytest.mark.asyncio
    async def test_build_with_no_policies(self) -> None:
        """build with no policies leaves active_policies empty."""
        storage = _make_storage()
        storage.list_policies.return_value = []

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123)

        assert ctx.active_policies == []
        storage.list_policies.assert_awaited_once_with(123)

    @pytest.mark.asyncio
    async def test_build_with_learnings(self) -> None:
        """build with learnings populates recent_learnings."""
        storage = _make_storage()
        learnings = [
            PersonalLearning(
                user_id=123,
                category=LearningCategory.PREFERENCE,
                content="User prefers coffee",
                confidence=0.9,
                source=LearningSource.EXPLICIT,
            ),
            PersonalLearning(
                user_id=123,
                category=LearningCategory.SCHEDULE,
                content="No meetings before 10am",
                confidence=0.8,
                source=LearningSource.INFERRED,
            ),
        ]
        storage.list_learnings.return_value = learnings

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123)

        assert len(ctx.recent_learnings) == 2
        assert ctx.recent_learnings[0]["content"] == "User prefers coffee"
        assert ctx.recent_learnings[1]["content"] == "No meetings before 10am"
        storage.list_learnings.assert_awaited_once_with(123, limit=10)

    @pytest.mark.asyncio
    async def test_build_with_no_learnings(self) -> None:
        """build with no learnings leaves recent_learnings empty."""
        storage = _make_storage()
        storage.list_learnings.return_value = []

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123)

        assert ctx.recent_learnings == []
        storage.list_learnings.assert_awaited_once_with(123, limit=10)

    @pytest.mark.asyncio
    async def test_build_with_all_data(self) -> None:
        """build with all data returns full context."""
        storage = _make_storage()
        profile = PersonalProfile(
            user_id=123,
            display_name="Alice",
            timezone="America/New_York",
        )
        contacts = [
            PersonalContact(
                user_id=123,
                contact_email="bob@example.com",
                contact_name="Bob",
                relationship=Relationship.COLLEAGUE,
            )
        ]
        policies = [
            PersonalPolicy(
                user_id=123,
                domain=PolicyDomain.EMAIL,
                action="reply",
                mode=PolicyMode.DRAFT,
            )
        ]
        learnings = [
            PersonalLearning(
                user_id=123,
                category=LearningCategory.PREFERENCE,
                content="User prefers tea",
                confidence=0.9,
                source=LearningSource.EXPLICIT,
            )
        ]

        storage.get_profile.return_value = profile
        storage.list_contacts.return_value = contacts
        storage.list_policies.return_value = policies
        storage.list_learnings.return_value = learnings

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123)

        assert ctx.user_profile["display_name"] == "Alice"
        assert len(ctx.relevant_contacts) == 1
        assert len(ctx.active_policies) == 1
        assert len(ctx.recent_learnings) == 1
        assert ctx.is_empty is False

    @pytest.mark.asyncio
    async def test_build_with_no_data(self) -> None:
        """build with no data returns empty context with is_empty True."""
        storage = _make_storage()

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123)

        assert ctx.user_profile == {}
        assert ctx.relevant_contacts == []
        assert ctx.active_policies == []
        assert ctx.recent_learnings == []
        assert ctx.is_empty is True

    @pytest.mark.asyncio
    async def test_profile_with_communication_style(self) -> None:
        """build includes communication_style in user_profile dict."""
        storage = _make_storage()
        profile = PersonalProfile(
            user_id=123,
            display_name="Alice",
            timezone="UTC",
            communication_style=CommunicationStyle(
                formality=0.8, verbosity=0.6, emoji_usage=0.2, humor=0.3
            ),
        )
        storage.get_profile.return_value = profile

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123)

        assert "communication_style" in ctx.user_profile
        assert ctx.user_profile["communication_style"]["formality"] == 0.8
        assert ctx.user_profile["communication_style"]["verbosity"] == 0.6

    @pytest.mark.asyncio
    async def test_profile_with_working_hours(self) -> None:
        """build includes working_hours in user_profile dict."""
        storage = _make_storage()
        profile = PersonalProfile(
            user_id=123,
            display_name="Alice",
            timezone="UTC",
            working_hours=WorkingHours(start="09:00", end="17:00", days=[1, 2, 3, 4, 5]),
        )
        storage.get_profile.return_value = profile

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(123)

        assert "working_hours" in ctx.user_profile
        assert ctx.user_profile["working_hours"]["start"] == "09:00"
        assert ctx.user_profile["working_hours"]["end"] == "17:00"
        assert ctx.user_profile["working_hours"]["days"] == [1, 2, 3, 4, 5]

    @pytest.mark.asyncio
    async def test_build_respects_max_contacts_limit(self) -> None:
        """build with mentioned_emails respects MAX_CONTACTS limit of 5."""
        storage = _make_storage()
        # Create 10 contacts
        contacts = [
            PersonalContact(
                user_id=123,
                contact_email=f"contact{i}@example.com",
                contact_name=f"Contact {i}",
                relationship=Relationship.COLLEAGUE,
            )
            for i in range(10)
        ]
        storage.get_contact.side_effect = contacts

        builder = DecisionContextBuilder(storage)
        ctx = await builder.build(
            123,
            mentioned_emails=[f"contact{i}@example.com" for i in range(10)],
        )

        # Should only fetch first 5
        assert len(ctx.relevant_contacts) == 5
        assert storage.get_contact.await_count == 5
