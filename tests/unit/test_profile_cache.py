"""Unit tests for the profile cache."""

import time

import pytest

from secureclaw.profile.cache import (
    EmploymentProfileSummary,
    ProfileCache,
    UserProfileSummary,
)
from secureclaw.profile.models import (
    ProfileCategory,
    ProfileEntry,
    ProfileSource,
)


class TestUserProfileSummary:
    """Tests for UserProfileSummary dataclass."""

    def test_empty_summary(self):
        """Test empty summary creation."""
        summary = UserProfileSummary(user_id="user123")

        assert summary.user_id == "user123"
        assert summary.name is None
        assert summary.timezone is None
        assert summary.role is None
        assert summary.preferences == {}

    def test_to_prompt_fragment_empty(self):
        """Test prompt fragment for empty summary."""
        summary = UserProfileSummary(user_id="user123")
        fragment = summary.to_prompt_fragment()

        assert "No profile information" in fragment

    def test_to_prompt_fragment_with_data(self):
        """Test prompt fragment with data."""
        summary = UserProfileSummary(
            user_id="user123",
            name="John",
            timezone="EST",
            role="developer",
            current_focus="Phase 5",
            preferences={"theme": "dark"},
        )
        fragment = summary.to_prompt_fragment()

        assert "John" in fragment
        assert "EST" in fragment
        assert "developer" in fragment
        assert "Phase 5" in fragment
        assert "theme" in fragment


class TestEmploymentProfileSummary:
    """Tests for EmploymentProfileSummary dataclass."""

    def test_default_summary(self):
        """Test default summary creation."""
        summary = EmploymentProfileSummary(user_id="user123")

        assert summary.user_id == "user123"
        assert summary.formality == 0.5
        assert summary.verbosity == 0.5
        assert summary.proactivity == 0.3
        assert summary.trust_level == 0.3

    def test_to_prompt_fragment(self):
        """Test prompt fragment generation."""
        summary = EmploymentProfileSummary(
            user_id="user123",
            primary_roles=["assistant", "developer"],
            formality=0.8,
            verbosity=0.3,
            trust_level=0.9,
        )
        fragment = summary.to_prompt_fragment()

        assert "assistant" in fragment
        assert "developer" in fragment
        assert "formal" in fragment.lower()
        assert "concise" in fragment.lower()
        assert "High" in fragment  # High trust

    def test_to_prompt_fragment_low_trust(self):
        """Test prompt fragment for low trust."""
        summary = EmploymentProfileSummary(
            user_id="user123",
            trust_level=0.2,
        )
        fragment = summary.to_prompt_fragment()

        assert "Building" in fragment


class TestProfileCache:
    """Tests for ProfileCache."""

    @pytest.fixture
    def cache(self):
        """Create a profile cache with short TTLs for testing."""
        return ProfileCache(
            user_cache_ttl=1,  # 1 second for testing
            employment_cache_ttl=1,
            summary_cache_ttl=1,
            max_users=10,
        )

    def test_user_profile_cache(self, cache):
        """Test user profile caching."""
        entries = [
            ProfileEntry.create(
                user_id="user123",
                category=ProfileCategory.IDENTITY,
                key="name",
                value="John",
                confidence=0.9,
                source=ProfileSource.EXPLICIT,
            )
        ]

        # Initially not cached
        assert cache.get_user_profile("user123") is None

        # Set cache
        cache.set_user_profile("user123", entries)

        # Should be cached now
        cached = cache.get_user_profile("user123")
        assert cached is not None
        assert len(cached) == 1
        assert cached[0].value == "John"

    def test_employment_profile_cache(self, cache):
        """Test employment profile caching."""
        profile = EmploymentProfileSummary(
            user_id="user123",
            primary_roles=["assistant"],
        )

        # Initially not cached
        assert cache.get_employment_profile("user123") is None

        # Set cache
        cache.set_employment_profile("user123", profile)

        # Should be cached now
        cached = cache.get_employment_profile("user123")
        assert cached is not None
        assert cached.primary_roles == ["assistant"]

    def test_summary_cache(self, cache):
        """Test profile summary caching."""
        summary = UserProfileSummary(
            user_id="user123",
            name="John",
        )

        # Initially not cached
        assert cache.get_summary("user123") is None

        # Set cache
        cache.set_summary("user123", summary)

        # Should be cached now
        cached = cache.get_summary("user123")
        assert cached is not None
        assert cached.name == "John"

    def test_cache_ttl_expiry(self, cache):
        """Test cache TTL expiry."""
        entries = [
            ProfileEntry.create(
                user_id="user123",
                category=ProfileCategory.IDENTITY,
                key="name",
                value="John",
                confidence=0.9,
                source=ProfileSource.EXPLICIT,
            )
        ]

        cache.set_user_profile("user123", entries)
        assert cache.get_user_profile("user123") is not None

        # Wait for TTL to expire
        time.sleep(1.5)

        # Should be expired now
        assert cache.get_user_profile("user123") is None

    def test_invalidate_user(self, cache):
        """Test invalidating a specific user's cache."""
        entries = [
            ProfileEntry.create(
                user_id="user123",
                category=ProfileCategory.IDENTITY,
                key="name",
                value="John",
                confidence=0.9,
                source=ProfileSource.EXPLICIT,
            )
        ]
        profile = EmploymentProfileSummary(user_id="user123")
        summary = UserProfileSummary(user_id="user123")

        cache.set_user_profile("user123", entries)
        cache.set_employment_profile("user123", profile)
        cache.set_summary("user123", summary)

        # All should be cached
        assert cache.get_user_profile("user123") is not None
        assert cache.get_employment_profile("user123") is not None
        assert cache.get_summary("user123") is not None

        # Invalidate
        cache.invalidate("user123")

        # All should be gone
        assert cache.get_user_profile("user123") is None
        assert cache.get_employment_profile("user123") is None
        assert cache.get_summary("user123") is None

    def test_invalidate_all(self, cache):
        """Test invalidating all cached data."""
        for i in range(5):
            cache.set_summary(f"user{i}", UserProfileSummary(user_id=f"user{i}"))

        # All should be cached
        for i in range(5):
            assert cache.get_summary(f"user{i}") is not None

        # Invalidate all
        cache.invalidate_all()

        # All should be gone
        for i in range(5):
            assert cache.get_summary(f"user{i}") is None

    def test_build_summary(self, cache):
        """Test building summary from entries."""
        entries = [
            ProfileEntry.create(
                user_id="user123",
                category=ProfileCategory.IDENTITY,
                key="name",
                value="John",
                confidence=0.9,
                source=ProfileSource.EXPLICIT,
            ),
            ProfileEntry.create(
                user_id="user123",
                category=ProfileCategory.IDENTITY,
                key="timezone",
                value="EST",
                confidence=0.95,
                source=ProfileSource.EXPLICIT,
            ),
            ProfileEntry.create(
                user_id="user123",
                category=ProfileCategory.PREFERENCES,
                key="theme",
                value="dark",
                confidence=0.8,
                source=ProfileSource.INFERRED,
            ),
            ProfileEntry.create(
                user_id="user123",
                category=ProfileCategory.PROJECTS,
                key="current_focus",
                value="Phase 5",
                confidence=0.75,
                source=ProfileSource.CONVERSATION,
            ),
        ]

        summary = cache.build_summary(entries)

        assert summary.user_id == "user123"
        assert summary.name == "John"
        assert summary.timezone == "EST"
        assert summary.preferences.get("theme") == "dark"
        assert summary.current_focus == "Phase 5"
        assert summary.total_entries == 4
        assert summary.high_confidence_entries >= 3

    def test_build_summary_empty(self, cache):
        """Test building summary from empty entries."""
        summary = cache.build_summary([])

        assert summary.user_id == "unknown"
        assert summary.total_entries == 0

    def test_stats(self, cache):
        """Test cache statistics."""
        # Add some entries
        for i in range(5):
            cache.set_summary(f"user{i}", UserProfileSummary(user_id=f"user{i}"))

        stats = cache.stats()

        assert stats["summary_cache_size"] == 5
        assert stats["summary_cache_max"] == 10
        assert stats["user_cache_size"] == 0
        assert stats["employment_cache_size"] == 0
