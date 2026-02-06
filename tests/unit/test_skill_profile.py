"""Tests for Profile Management Skill."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.permissions import Permission
from zetherion_ai.skills.profile_skill import (
    PROFILES_COLLECTION,
    ProfileSkill,
    ProfileSummary,
)


class TestProfileSummary:
    """Tests for ProfileSummary dataclass."""

    def test_default_values(self) -> None:
        """ProfileSummary should have sensible defaults."""
        summary = ProfileSummary()
        assert summary.total_entries == 0
        assert summary.by_category is None
        assert summary.high_confidence == 0
        assert summary.medium_confidence == 0
        assert summary.low_confidence == 0
        assert summary.oldest_entry is None
        assert summary.newest_entry is None

    def test_to_dict(self) -> None:
        """to_dict should serialize properly."""
        now = datetime.now()
        summary = ProfileSummary(
            total_entries=10,
            by_category={"identity": 3, "preferences": 5, "goals": 2},
            high_confidence=5,
            medium_confidence=3,
            low_confidence=2,
            oldest_entry=now - timedelta(days=30),
            newest_entry=now,
        )
        data = summary.to_dict()
        assert data["total_entries"] == 10
        assert data["by_category"]["identity"] == 3
        assert data["high_confidence"] == 5
        assert data["oldest_entry"] is not None


class TestProfileSkill:
    """Tests for ProfileSkill."""

    def test_metadata(self) -> None:
        """Skill should have correct metadata."""
        skill = ProfileSkill()
        meta = skill.metadata
        assert meta.name == "profile_manager"
        assert meta.version == "1.0.0"
        assert Permission.READ_PROFILE in meta.permissions
        assert Permission.WRITE_PROFILE in meta.permissions
        assert Permission.DELETE_PROFILE in meta.permissions
        assert PROFILES_COLLECTION in meta.collections
        assert "profile_summary" in meta.intents
        assert "profile_view" in meta.intents

    def test_initial_status(self) -> None:
        """Skill should start uninitialized."""
        skill = ProfileSkill()
        assert skill.status == SkillStatus.UNINITIALIZED

    @pytest.mark.asyncio
    async def test_initialize(self) -> None:
        """Skill should initialize successfully."""
        skill = ProfileSkill()
        result = await skill.safe_initialize()
        assert result is True
        assert skill.status == SkillStatus.READY

    @pytest.mark.asyncio
    async def test_handle_summary_no_entries(self) -> None:
        """Skill should handle empty profile."""
        skill = ProfileSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="profile_summary",
        )
        response = await skill.handle(request)
        assert response.success is True
        assert "don't have any profile data" in response.message

    @pytest.mark.asyncio
    async def test_handle_summary_with_entries(self) -> None:
        """Skill should summarize profile entries."""
        mock_memory = AsyncMock()
        mock_memory.filter_by_field = AsyncMock(
            return_value=[
                {
                    "id": "1",
                    "category": "identity",
                    "key": "name",
                    "value": "John",
                    "confidence": 0.9,
                    "created_at": datetime.now().isoformat(),
                },
                {
                    "id": "2",
                    "category": "preferences",
                    "key": "timezone",
                    "value": "UTC",
                    "confidence": 0.7,
                    "created_at": datetime.now().isoformat(),
                },
                {
                    "id": "3",
                    "category": "preferences",
                    "key": "verbosity",
                    "value": "concise",
                    "confidence": 0.4,
                    "created_at": datetime.now().isoformat(),
                },
            ]
        )

        skill = ProfileSkill(memory=mock_memory)
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="profile_summary",
        )
        response = await skill.handle(request)
        assert response.success is True
        assert "3 thing(s)" in response.message
        summary = response.data["summary"]
        assert summary["total_entries"] == 3
        assert summary["high_confidence"] == 1
        assert summary["medium_confidence"] == 1
        assert summary["low_confidence"] == 1

    @pytest.mark.asyncio
    async def test_handle_view_all(self) -> None:
        """Skill should view all profile entries."""
        mock_memory = AsyncMock()
        mock_memory.filter_by_field = AsyncMock(
            return_value=[
                {
                    "category": "identity",
                    "key": "name",
                    "value": "John",
                    "confidence": 0.9,
                    "source": "explicit",
                },
            ]
        )

        skill = ProfileSkill(memory=mock_memory)
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="profile_view",
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["count"] == 1
        entry = response.data["entries"][0]
        assert entry["key"] == "name"
        assert entry["value"] == "John"

    @pytest.mark.asyncio
    async def test_handle_view_by_category(self) -> None:
        """Skill should filter by category."""
        mock_memory = AsyncMock()
        mock_memory.filter_by_field = AsyncMock(
            return_value=[
                {"category": "identity", "key": "name", "value": "John", "confidence": 0.9},
                {"category": "preferences", "key": "timezone", "value": "UTC", "confidence": 0.8},
            ]
        )

        skill = ProfileSkill(memory=mock_memory)
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="profile_view",
            context={"category": "identity"},
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["count"] == 1
        assert response.data["entries"][0]["category"] == "identity"

    @pytest.mark.asyncio
    async def test_handle_update_missing_fields(self) -> None:
        """Skill should error on missing update fields."""
        skill = ProfileSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="profile_update",
            context={"category": "identity"},  # Missing key and value
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "Missing required fields" in response.error

    @pytest.mark.asyncio
    async def test_handle_update_with_builder(self) -> None:
        """Skill should update profile with builder."""
        mock_builder = MagicMock()
        mock_builder.update_profile_entry = AsyncMock()

        skill = ProfileSkill(profile_builder=mock_builder)
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="profile_update",
            context={
                "category": "preferences",
                "key": "timezone",
                "value": "America/New_York",
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        mock_builder.update_profile_entry.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_delete_missing_params(self) -> None:
        """Skill should error on missing delete params."""
        skill = ProfileSkill()
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="profile_delete",
            context={},
        )
        response = await skill.handle(request)
        assert response.success is False
        assert "entry_id or both category and key" in response.error

    @pytest.mark.asyncio
    async def test_handle_delete_with_builder(self) -> None:
        """Skill should delete profile entry with builder."""
        mock_builder = MagicMock()
        mock_builder.delete_profile_entry_by_key = AsyncMock()

        skill = ProfileSkill(profile_builder=mock_builder)
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="profile_delete",
            context={
                "category": "preferences",
                "key": "timezone",
            },
        )
        response = await skill.handle(request)
        assert response.success is True
        assert "Forgotten" in response.message

    @pytest.mark.asyncio
    async def test_handle_export(self) -> None:
        """Skill should export all profile data."""
        mock_memory = AsyncMock()
        mock_memory.filter_by_field = AsyncMock(
            return_value=[
                {
                    "id": "1",
                    "category": "identity",
                    "key": "name",
                    "value": "John",
                    "confidence": 0.9,
                    "source": "explicit",
                    "created_at": datetime.now().isoformat(),
                    "updated_at": datetime.now().isoformat(),
                },
            ]
        )

        skill = ProfileSkill(memory=mock_memory)
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="profile_export",
        )
        response = await skill.handle(request)
        assert response.success is True
        assert response.data["count"] == 1
        assert "exported_at" in response.data

    @pytest.mark.asyncio
    async def test_handle_confidence_report(self) -> None:
        """Skill should generate confidence report."""
        mock_memory = AsyncMock()
        mock_memory.filter_by_field = AsyncMock(
            return_value=[
                {"category": "identity", "key": "name", "value": "John", "confidence": 0.95},
                {"category": "preferences", "key": "style", "value": "casual", "confidence": 0.6},
                {"category": "goals", "key": "learn", "value": "Python", "confidence": 0.3},
            ]
        )

        skill = ProfileSkill(memory=mock_memory)
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="user123",
            intent="profile_confidence",
        )
        response = await skill.handle(request)
        assert response.success is True
        report = response.data["report"]
        assert report["high_confidence"]["count"] == 1
        assert report["medium_confidence"]["count"] == 1
        assert report["low_confidence"]["count"] == 1
        assert "need confirmation" in response.message

    @pytest.mark.asyncio
    async def test_handle_unknown_intent(self) -> None:
        """Skill should error on unknown intent."""
        skill = ProfileSkill()
        await skill.safe_initialize()

        request = SkillRequest(intent="unknown_intent")
        response = await skill.handle(request)
        assert response.success is False
        assert "Unknown intent" in response.error

    @pytest.mark.asyncio
    async def test_heartbeat_low_confidence(self) -> None:
        """Skill should flag low confidence entries for confirmation."""
        mock_memory = AsyncMock()
        mock_memory.filter_by_field = AsyncMock(
            return_value=[
                {"category": "preferences", "key": "style", "value": "formal", "confidence": 0.3},
            ]
        )

        skill = ProfileSkill(memory=mock_memory)
        await skill.safe_initialize()

        actions = await skill.on_heartbeat(["user123"])
        confirm_actions = [a for a in actions if a.action_type == "confirm_low_confidence"]
        assert len(confirm_actions) == 1
        assert confirm_actions[0].priority == 4

    @pytest.mark.asyncio
    async def test_heartbeat_stale_entries(self) -> None:
        """Skill should detect stale entries."""
        old_date = (datetime.now() - timedelta(days=60)).isoformat()
        mock_memory = AsyncMock()
        mock_memory.filter_by_field = AsyncMock(
            return_value=[
                {
                    "category": "preferences",
                    "key": "timezone",
                    "value": "UTC",
                    "confidence": 0.9,
                    "updated_at": old_date,
                },
            ]
        )

        skill = ProfileSkill(memory=mock_memory)
        await skill.safe_initialize()

        actions = await skill.on_heartbeat(["user123"])
        decay_actions = [a for a in actions if a.action_type == "decay_check"]
        assert len(decay_actions) == 1
        assert decay_actions[0].data["stale_count"] == 1

    def test_get_system_prompt_fragment(self) -> None:
        """get_system_prompt_fragment should return None (sync access limitation)."""
        skill = ProfileSkill()
        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is None

    @pytest.mark.asyncio
    async def test_cleanup(self) -> None:
        """Skill should clean up resources."""
        skill = ProfileSkill()
        await skill.safe_initialize()
        await skill.cleanup()  # Should not raise
