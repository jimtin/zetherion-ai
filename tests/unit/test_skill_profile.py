"""Tests for Profile Management Skill."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.memory.embeddings import get_embedding_dimension
from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.permissions import Permission
from zetherion_ai.skills.profile_skill import (
    LONG_TERM_MEMORY_COLLECTION,
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
    async def test_initialize_with_memory(self) -> None:
        """Skill should ensure the profile collection exists when memory is configured."""
        mock_memory = AsyncMock()
        mock_memory.ensure_scoped_collection = AsyncMock()

        skill = ProfileSkill(memory=mock_memory)

        result = await skill.initialize()

        assert result is True
        mock_memory.ensure_scoped_collection.assert_awaited_once_with(
            PROFILES_COLLECTION,
            vector_size=get_embedding_dimension(),
        )

    @pytest.mark.asyncio
    async def test_initialize_with_memory_failure(self) -> None:
        """Skill should fail initialization when the profile collection cannot be ensured."""
        mock_memory = AsyncMock()
        mock_memory.ensure_scoped_collection = AsyncMock(
            side_effect=RuntimeError("qdrant unavailable")
        )

        skill = ProfileSkill(memory=mock_memory)

        result = await skill.initialize()

        assert result is False

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
        mock_memory.filter_scoped_by_field = AsyncMock(
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
    async def test_handle_summary_uses_long_term_memories_when_profile_entries_missing(
        self,
    ) -> None:
        """Summary should not be empty when profile-style memories exist in long-term memory."""
        now = datetime.now().isoformat()

        async def _filter_by_field(*, collection_name, field, value, limit=100):
            if field != "user_id":
                return []
            if collection_name == PROFILES_COLLECTION:
                return []
            if collection_name == LONG_TERM_MEMORY_COLLECTION and value in ("123", 123):
                return [
                    {
                        "id": "m1",
                        "type": "user_request",
                        "content": "I work as a software engineer",
                        "timestamp": now,
                    },
                    {
                        "id": "m2",
                        "type": "general",
                        "content": "my favorite color is green-42",
                        "timestamp": now,
                    },
                ]
            return []

        mock_memory = AsyncMock()
        mock_memory.filter_scoped_by_field = AsyncMock(side_effect=_filter_by_field)

        skill = ProfileSkill(memory=mock_memory)
        await skill.safe_initialize()

        request = SkillRequest(
            user_id="123",
            intent="profile_summary",
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "don't have any profile data" not in response.message.lower()
        summary = response.data["summary"]
        assert summary["total_entries"] == 2
        assert summary["by_category"].get("memory") == 2

    @pytest.mark.asyncio
    async def test_handle_view_all(self) -> None:
        """Skill should view all profile entries."""
        mock_memory = AsyncMock()
        mock_memory.filter_scoped_by_field = AsyncMock(
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
        mock_memory.filter_scoped_by_field = AsyncMock(
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
    async def test_handle_update_without_builder_reports_error(self) -> None:
        skill = ProfileSkill()
        await skill.safe_initialize()

        response = await skill.handle(
            SkillRequest(
                user_id="user123",
                intent="profile_update",
                context={"category": "preferences", "key": "timezone", "value": "UTC"},
            )
        )

        assert response.success is False
        assert "Profile builder not available" in str(response.error)

    @pytest.mark.asyncio
    async def test_handle_update_builder_failure_returns_error(self) -> None:
        mock_builder = MagicMock()
        mock_builder.update_profile_entry = AsyncMock(side_effect=RuntimeError("boom"))

        skill = ProfileSkill(profile_builder=mock_builder)
        await skill.safe_initialize()

        response = await skill.handle(
            SkillRequest(
                user_id="user123",
                intent="profile_update",
                context={"category": "preferences", "key": "timezone", "value": "UTC"},
            )
        )

        assert response.success is False
        assert "Update failed: boom" in str(response.error)

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
    async def test_handle_delete_with_entry_id_and_failure_paths(self) -> None:
        mock_builder = MagicMock()
        mock_builder.delete_profile_entry = AsyncMock()
        mock_builder.delete_profile_entry_by_key = AsyncMock(side_effect=RuntimeError("nope"))

        skill = ProfileSkill(profile_builder=mock_builder)
        await skill.safe_initialize()

        by_id = await skill.handle(
            SkillRequest(
                user_id="user123",
                intent="profile_delete",
                context={"entry_id": "entry-1"},
            )
        )
        assert by_id.success is True
        mock_builder.delete_profile_entry.assert_awaited_once()

        by_key = await skill.handle(
            SkillRequest(
                user_id="user123",
                intent="profile_delete",
                context={"category": "preferences", "key": "timezone"},
            )
        )
        assert by_key.success is False
        assert "Delete failed: nope" in str(by_key.error)

    @pytest.mark.asyncio
    async def test_handle_delete_without_builder_reports_error(self) -> None:
        skill = ProfileSkill()
        await skill.safe_initialize()

        response = await skill.handle(
            SkillRequest(
                user_id="user123",
                intent="profile_delete",
                context={"category": "preferences", "key": "timezone"},
            )
        )

        assert response.success is False
        assert "Profile builder not available" in str(response.error)

    @pytest.mark.asyncio
    async def test_handle_export(self) -> None:
        """Skill should export all profile data."""
        mock_memory = AsyncMock()
        mock_memory.filter_scoped_by_field = AsyncMock(
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
        mock_memory.filter_scoped_by_field = AsyncMock(
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
        mock_memory.filter_scoped_by_field = AsyncMock(
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
        mock_memory.filter_scoped_by_field = AsyncMock(
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
    async def test_profile_helper_methods_dedupe_fallback_and_filter_memory_entries(self) -> None:
        now = datetime.now().isoformat()
        mock_memory = AsyncMock()

        async def _filter_by_field(*, collection_name, field, value, limit=100):
            assert field == "user_id"
            if collection_name == PROFILES_COLLECTION:
                if value == "123":
                    return [
                        {
                            "id": "entry-1",
                            "category": "identity",
                            "key": "name",
                            "value": "John",
                            "confidence": 0.9,
                        }
                    ]
                return [
                    {
                        "id": "entry-1",
                        "category": "identity",
                        "key": "name",
                        "value": "John",
                        "confidence": 0.9,
                    }
                ]
            if value not in ("123", 123):
                return []
            return [
                {"id": "m1", "type": "profile", "content": "John", "timestamp": now},
                {"id": "m2", "type": "other", "content": "skip me", "timestamp": now},
                {"id": "m3", "type": "general", "content": "", "timestamp": now},
                {"id": "m4", "type": "general", "content": "Prefers tea", "timestamp": now},
            ]

        mock_memory.filter_scoped_by_field = AsyncMock(side_effect=_filter_by_field)

        skill = ProfileSkill(memory=mock_memory)
        await skill.safe_initialize()

        profile_entries = await skill._get_profile_entries("123")  # noqa: SLF001
        assert len(profile_entries) == 1

        summary_entries = await skill._get_summary_entries("123")  # noqa: SLF001
        assert len(summary_entries) == 3
        assert {entry["value"] for entry in summary_entries} == {"John", "Prefers tea"}

        memory_entries = await skill._get_profile_memory_entries("123")  # noqa: SLF001
        assert len(memory_entries) == 2
        assert {entry["key"] for entry in memory_entries} == {"profile", "general"}
        assert skill._entry_fingerprint({"value": ""}) == ""  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_get_profile_entries_builder_fallback_returns_empty_on_error(self) -> None:
        mock_builder = MagicMock()
        mock_builder.get_all_profile_entries = AsyncMock(side_effect=RuntimeError("broken"))

        skill = ProfileSkill(profile_builder=mock_builder)
        await skill.safe_initialize()

        assert await skill._get_profile_entries("123") == []  # noqa: SLF001

    @pytest.mark.asyncio
    async def test_cleanup(self) -> None:
        """Skill should clean up resources."""
        skill = ProfileSkill()
        await skill.safe_initialize()
        await skill.cleanup()  # Should not raise
