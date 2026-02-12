"""Tests for Dev Watcher Skill."""

from datetime import datetime, timedelta
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.dev_watcher import (
    DEV_JOURNAL_COLLECTION,
    IDEA_REMINDER_DAYS,
    STALE_ANNOTATION_DAYS,
    DevEntry,
    DevWatcherSkill,
    _entry_type_icon,
    _format_age,
)
from zetherion_ai.skills.permissions import Permission

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_request(
    intent: str,
    message: str = "",
    user_id: str = "user123",
    context: dict | None = None,
) -> SkillRequest:
    return SkillRequest(
        id=uuid4(),
        user_id=user_id,
        intent=intent,
        message=message,
        context=context or {},
    )


def _make_commit_request(
    message: str = "feat: Add new feature",
    user_id: str = "user123",
    project: str = "zetherion-ai",
    sha: str = "abc1234def5678",
) -> SkillRequest:
    return _make_request(
        intent="dev_ingest_commit",
        message=message,
        user_id=user_id,
        context={
            "project": project,
            "sha": sha,
            "files_changed": "5",
            "diff_summary": "+100 -20",
            "branch": "main",
        },
    )


def _make_annotation_request(
    message: str = "Refactor auth module",
    user_id: str = "user123",
    annotation_type: str = "TODO",
    action: str = "added",
    file: str = "src/auth.py",
    line: int = 42,
    project: str = "zetherion-ai",
) -> SkillRequest:
    return _make_request(
        intent="dev_ingest_annotation",
        message=message,
        user_id=user_id,
        context={
            "project": project,
            "annotation_type": annotation_type,
            "file": file,
            "line": line,
            "action": action,
        },
    )


def _make_session_request(
    message: str = "Implemented auth middleware and added unit tests",
    user_id: str = "user123",
    project: str = "zetherion-ai",
) -> SkillRequest:
    return _make_request(
        intent="dev_ingest_session",
        message=message,
        user_id=user_id,
        context={
            "project": project,
            "summary": "Auth middleware implementation",
            "session_id": "sess-001",
            "duration_minutes": 45,
            "tools_used": 12,
        },
    )


def _make_tag_request(
    message: str = "Release v1.2.0",
    user_id: str = "user123",
    project: str = "zetherion-ai",
    tag_name: str = "v1.2.0",
) -> SkillRequest:
    return _make_request(
        intent="dev_ingest_tag",
        message=message,
        user_id=user_id,
        context={
            "project": project,
            "tag_name": tag_name,
            "sha": "def5678",
        },
    )


async def _seed_skill(skill: DevWatcherSkill, user_id: str = "user123") -> None:
    """Seed the skill with a variety of entries for query tests."""
    await skill.handle(_make_commit_request(user_id=user_id))
    await skill.handle(
        _make_commit_request(
            message="fix: Resolve login bug",
            user_id=user_id,
            sha="bbb2222",
        )
    )
    await skill.handle(_make_annotation_request(user_id=user_id))
    await skill.handle(
        _make_annotation_request(
            message="Build caching layer",
            annotation_type="IDEA",
            user_id=user_id,
            file="src/cache.py",
        )
    )
    await skill.handle(_make_session_request(user_id=user_id))
    await skill.handle(_make_tag_request(user_id=user_id))


# ===================================================================
# 1. TestDevEntryDataModel
# ===================================================================


class TestDevEntryDataModel:
    """Tests for DevEntry dataclass serialisation and helpers."""

    def test_default_values(self) -> None:
        entry = DevEntry()
        assert isinstance(entry.id, UUID)
        assert entry.user_id == ""
        assert entry.entry_type == ""
        assert entry.project == ""
        assert entry.title == ""
        assert entry.content == ""
        assert entry.metadata == {}
        assert entry.status == "active"
        assert isinstance(entry.created_at, datetime)

    def test_to_dict(self) -> None:
        entry_id = uuid4()
        now = datetime.now()
        entry = DevEntry(
            id=entry_id,
            user_id="user123",
            entry_type="commit",
            project="proj",
            title="feat: something",
            content="Full message",
            metadata={"sha": "abc123"},
            status="active",
            created_at=now,
        )
        data = entry.to_dict()
        assert data["id"] == str(entry_id)
        assert data["user_id"] == "user123"
        assert data["entry_type"] == "commit"
        assert data["project"] == "proj"
        assert data["title"] == "feat: something"
        assert data["content"] == "Full message"
        assert data["metadata"] == {"sha": "abc123"}
        assert data["status"] == "active"
        assert data["created_at"] == now.isoformat()

    def test_from_dict(self) -> None:
        entry_id = uuid4()
        now = datetime.now()
        data = {
            "id": str(entry_id),
            "user_id": "user456",
            "entry_type": "annotation",
            "project": "proj2",
            "title": "TODO: Fix this",
            "content": "Fix this thing",
            "metadata": {"annotation_type": "TODO"},
            "status": "active",
            "created_at": now.isoformat(),
        }
        entry = DevEntry.from_dict(data)
        assert entry.id == entry_id
        assert entry.user_id == "user456"
        assert entry.entry_type == "annotation"
        assert entry.project == "proj2"
        assert entry.title == "TODO: Fix this"
        assert entry.content == "Fix this thing"
        assert entry.metadata == {"annotation_type": "TODO"}
        assert entry.status == "active"

    def test_from_dict_missing_optional_fields(self) -> None:
        data: dict = {}
        entry = DevEntry.from_dict(data)
        assert isinstance(entry.id, UUID)
        assert entry.user_id == ""
        assert entry.entry_type == ""
        assert entry.status == "active"
        assert isinstance(entry.created_at, datetime)

    def test_roundtrip(self) -> None:
        original = DevEntry(
            user_id="u1",
            entry_type="session",
            project="p",
            title="t",
            content="c",
            metadata={"k": "v"},
            status="resolved",
        )
        restored = DevEntry.from_dict(original.to_dict())
        assert restored.id == original.id
        assert restored.user_id == original.user_id
        assert restored.entry_type == original.entry_type
        assert restored.status == original.status

    def test_is_stale_active_and_old(self) -> None:
        entry = DevEntry(
            status="active",
            created_at=datetime.now() - timedelta(days=STALE_ANNOTATION_DAYS + 1),
        )
        assert entry.is_stale() is True

    def test_is_stale_resolved_entry(self) -> None:
        entry = DevEntry(
            status="resolved",
            created_at=datetime.now() - timedelta(days=STALE_ANNOTATION_DAYS + 1),
        )
        assert entry.is_stale() is False

    def test_is_stale_recent_entry(self) -> None:
        entry = DevEntry(
            status="active",
            created_at=datetime.now() - timedelta(days=1),
        )
        assert entry.is_stale() is False

    def test_is_stale_custom_days(self) -> None:
        entry = DevEntry(
            status="active",
            created_at=datetime.now() - timedelta(days=3),
        )
        assert entry.is_stale(days=2) is True
        assert entry.is_stale(days=5) is False

    def test_is_stale_archived_entry(self) -> None:
        entry = DevEntry(
            status="archived",
            created_at=datetime.now() - timedelta(days=100),
        )
        assert entry.is_stale() is False


# ===================================================================
# 2. TestDevWatcherMetadata
# ===================================================================


class TestDevWatcherMetadata:
    """Tests for DevWatcherSkill metadata."""

    def test_name(self) -> None:
        skill = DevWatcherSkill()
        assert skill.name == "dev_watcher"

    def test_version(self) -> None:
        skill = DevWatcherSkill()
        assert skill.metadata.version == "1.0.0"

    def test_intents(self) -> None:
        skill = DevWatcherSkill()
        expected = [
            "dev_ingest_commit",
            "dev_ingest_annotation",
            "dev_ingest_session",
            "dev_ingest_tag",
            "dev_status",
            "dev_next",
            "dev_ideas",
            "dev_journal",
            "dev_summary",
        ]
        assert skill.metadata.intents == expected

    def test_permissions(self) -> None:
        skill = DevWatcherSkill()
        perms = skill.metadata.permissions
        assert Permission.READ_OWN_COLLECTION in perms
        assert Permission.WRITE_OWN_COLLECTION in perms
        assert Permission.SEND_MESSAGES in perms
        assert Permission.READ_PROFILE in perms

    def test_collections(self) -> None:
        skill = DevWatcherSkill()
        assert skill.metadata.collections == [DEV_JOURNAL_COLLECTION]
        assert DEV_JOURNAL_COLLECTION == "skill_dev_journal"


# ===================================================================
# 3. TestDevWatcherInitialization
# ===================================================================


class TestDevWatcherInitialization:
    """Tests for DevWatcherSkill initialization."""

    @pytest.mark.asyncio
    async def test_init_without_memory(self) -> None:
        skill = DevWatcherSkill()
        result = await skill.safe_initialize()
        assert result is True
        assert skill.status == SkillStatus.READY

    @pytest.mark.asyncio
    async def test_init_with_memory(self) -> None:
        mock_memory = AsyncMock()
        skill = DevWatcherSkill(memory=mock_memory)
        result = await skill.safe_initialize()
        assert result is True
        assert skill.status == SkillStatus.READY
        mock_memory.ensure_collection.assert_called_once_with(
            DEV_JOURNAL_COLLECTION,
            vector_size=768,
        )

    @pytest.mark.asyncio
    async def test_init_memory_failure(self) -> None:
        mock_memory = AsyncMock()
        mock_memory.ensure_collection.side_effect = RuntimeError("Connection refused")
        skill = DevWatcherSkill(memory=mock_memory)
        result = await skill.safe_initialize()
        assert result is False
        assert skill.status == SkillStatus.ERROR


# ===================================================================
# 4. TestDevWatcherHandleRouting
# ===================================================================


class TestDevWatcherHandleRouting:
    """Tests for intent routing."""

    @pytest.mark.asyncio
    async def test_unknown_intent_returns_error(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_request(intent="nonexistent_intent", message="hello")
        resp = await skill.handle(req)
        assert resp.success is False
        assert "Unknown intent" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_all_known_intents_are_routable(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        for intent in skill.INTENTS:
            req = _make_request(
                intent=intent,
                message="test msg",
                context={
                    "project": "proj",
                    "sha": "abc",
                    "annotation_type": "TODO",
                    "action": "added",
                    "file": "f.py",
                    "tag_name": "v1",
                },
            )
            resp = await skill.handle(req)
            assert resp.success is True, f"Intent {intent} should succeed"


# ===================================================================
# 5. TestDevWatcherIngestCommit
# ===================================================================


class TestDevWatcherIngestCommit:
    """Tests for dev_ingest_commit intent."""

    @pytest.mark.asyncio
    async def test_basic_ingest(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_commit_request()
        resp = await skill.handle(req)
        assert resp.success is True
        assert "Ingested commit" in resp.message

    @pytest.mark.asyncio
    async def test_stores_in_cache(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_commit_request()
        await skill.handle(req)
        entries = skill._entries_cache.get("user123", {})
        assert len(entries) == 1
        entry = next(iter(entries.values()))
        assert entry.entry_type == "commit"
        assert entry.project == "zetherion-ai"
        assert entry.metadata["sha"] == "abc1234def5678"
        assert entry.metadata["branch"] == "main"

    @pytest.mark.asyncio
    async def test_response_message_contains_title(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_commit_request(message="feat: Add new feature")
        resp = await skill.handle(req)
        assert "feat: Add new feature" in resp.message

    @pytest.mark.asyncio
    async def test_title_truncated_to_200_chars(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        long_msg = "x" * 300
        req = _make_commit_request(message=long_msg)
        await skill.handle(req)
        entry = next(iter(skill._entries_cache["user123"].values()))
        assert len(entry.title) == 200

    @pytest.mark.asyncio
    async def test_uses_message_field_from_context(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_request(
            intent="dev_ingest_commit",
            message="fallback message",
            context={"message": "context message", "project": "p"},
        )
        await skill.handle(req)
        entry = next(iter(skill._entries_cache["user123"].values()))
        assert entry.title == "context message"


# ===================================================================
# 6. TestDevWatcherIngestAnnotation
# ===================================================================


class TestDevWatcherIngestAnnotation:
    """Tests for dev_ingest_annotation intent."""

    @pytest.mark.asyncio
    async def test_added_annotation(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_annotation_request()
        resp = await skill.handle(req)
        assert resp.success is True
        assert "TODO" in resp.message
        entry = next(iter(skill._entries_cache["user123"].values()))
        assert entry.entry_type == "annotation"
        assert entry.status == "active"
        assert entry.metadata["annotation_type"] == "TODO"
        assert entry.metadata["file"] == "src/auth.py"
        assert entry.metadata["line"] == 42
        assert entry.metadata["action"] == "added"

    @pytest.mark.asyncio
    async def test_removed_annotation_resolves_existing(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        # First add an annotation
        add_req = _make_annotation_request(
            message="Fix login",
            action="added",
            file="src/login.py",
        )
        await skill.handle(add_req)

        # Then remove the same annotation
        remove_req = _make_annotation_request(
            message="Fix login",
            action="removed",
            file="src/login.py",
        )
        await skill.handle(remove_req)

        # The original should be resolved
        entries = list(skill._entries_cache["user123"].values())
        resolved = [e for e in entries if e.status == "resolved"]
        assert len(resolved) >= 1

    @pytest.mark.asyncio
    async def test_idea_type(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_annotation_request(
            message="Build caching layer",
            annotation_type="IDEA",
        )
        resp = await skill.handle(req)
        assert resp.success is True
        assert "IDEA" in resp.message
        entry = next(iter(skill._entries_cache["user123"].values()))
        assert entry.metadata["annotation_type"] == "IDEA"

    @pytest.mark.asyncio
    async def test_fixme_type(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_annotation_request(
            message="Memory leak in pool",
            annotation_type="FIXME",
        )
        resp = await skill.handle(req)
        assert "FIXME" in resp.message

    @pytest.mark.asyncio
    async def test_title_format(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_annotation_request(
            message="Refactor auth module",
            annotation_type="TODO",
        )
        await skill.handle(req)
        entry = next(iter(skill._entries_cache["user123"].values()))
        assert entry.title.startswith("TODO:")


# ===================================================================
# 7. TestDevWatcherIngestSession
# ===================================================================


class TestDevWatcherIngestSession:
    """Tests for dev_ingest_session intent."""

    @pytest.mark.asyncio
    async def test_basic_ingest(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_session_request()
        resp = await skill.handle(req)
        assert resp.success is True
        assert "Ingested Claude Code session" in resp.message

    @pytest.mark.asyncio
    async def test_stores_session_metadata(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_session_request())
        entry = next(iter(skill._entries_cache["user123"].values()))
        assert entry.entry_type == "session"
        assert entry.metadata["session_id"] == "sess-001"
        assert entry.metadata["duration_minutes"] == 45
        assert entry.metadata["tools_used"] == 12
        assert entry.title == "Auth middleware implementation"


# ===================================================================
# 8. TestDevWatcherIngestTag
# ===================================================================


class TestDevWatcherIngestTag:
    """Tests for dev_ingest_tag intent."""

    @pytest.mark.asyncio
    async def test_basic_ingest(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_tag_request()
        resp = await skill.handle(req)
        assert resp.success is True
        assert "v1.2.0" in resp.message

    @pytest.mark.asyncio
    async def test_stores_tag_metadata(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_tag_request())
        entry = next(iter(skill._entries_cache["user123"].values()))
        assert entry.entry_type == "tag"
        assert entry.metadata["tag_name"] == "v1.2.0"
        assert entry.metadata["sha"] == "def5678"
        assert entry.title == "Tag: v1.2.0"


# ===================================================================
# 9. TestDevWatcherQueryStatus
# ===================================================================


class TestDevWatcherQueryStatus:
    """Tests for dev_status intent."""

    @pytest.mark.asyncio
    async def test_no_entries(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_request(intent="dev_status")
        resp = await skill.handle(req)
        assert resp.success is True
        assert "No recent development activity" in resp.message

    @pytest.mark.asyncio
    async def test_with_commits_grouped_by_project(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_commit_request(message="feat: A", project="proj-a", sha="aaa1111"))
        await skill.handle(_make_commit_request(message="fix: B", project="proj-b", sha="bbb2222"))
        req = _make_request(intent="dev_status")
        resp = await skill.handle(req)
        assert "proj-a" in resp.message
        assert "proj-b" in resp.message
        assert "Current Dev Activity" in resp.message

    @pytest.mark.asyncio
    async def test_with_annotations(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_commit_request())
        await skill.handle(_make_annotation_request())
        req = _make_request(intent="dev_status")
        resp = await skill.handle(req)
        assert "Active Annotations" in resp.message
        assert "TODO" in resp.message

    @pytest.mark.asyncio
    async def test_status_includes_sessions(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_session_request())
        req = _make_request(intent="dev_status")
        resp = await skill.handle(req)
        assert "Session:" in resp.message

    @pytest.mark.asyncio
    async def test_status_data_field(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_commit_request())
        req = _make_request(intent="dev_status")
        resp = await skill.handle(req)
        assert "entries" in resp.data
        assert len(resp.data["entries"]) >= 1


# ===================================================================
# 10. TestDevWatcherQueryNext
# ===================================================================


class TestDevWatcherQueryNext:
    """Tests for dev_next intent."""

    @pytest.mark.asyncio
    async def test_no_items(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_request(intent="dev_next")
        resp = await skill.handle(req)
        assert resp.success is True
        assert "No open items found" in resp.message

    @pytest.mark.asyncio
    async def test_with_todos(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(
            _make_annotation_request(
                message="Fix the broken auth",
                annotation_type="TODO",
            )
        )
        req = _make_request(intent="dev_next")
        resp = await skill.handle(req)
        assert "Open TODOs" in resp.message
        assert "Fix the broken auth" in resp.message

    @pytest.mark.asyncio
    async def test_with_ideas(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(
            _make_annotation_request(
                message="Build caching layer",
                annotation_type="IDEA",
            )
        )
        req = _make_request(intent="dev_next")
        resp = await skill.handle(req)
        assert "Captured Ideas" in resp.message
        assert "Build caching layer" in resp.message

    @pytest.mark.asyncio
    async def test_with_sessions(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_session_request())
        req = _make_request(intent="dev_next")
        resp = await skill.handle(req)
        assert "Recent Session Context" in resp.message

    @pytest.mark.asyncio
    async def test_stale_annotation_marked(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        # Ingest, then manually age the entry
        await skill.handle(_make_annotation_request(message="Old todo", annotation_type="TODO"))
        entry = next(iter(skill._entries_cache["user123"].values()))
        entry.created_at = datetime.now() - timedelta(days=STALE_ANNOTATION_DAYS + 1)

        req = _make_request(intent="dev_next")
        resp = await skill.handle(req)
        assert "(stale!)" in resp.message

    @pytest.mark.asyncio
    async def test_fixme_and_hack_appear(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(
            _make_annotation_request(message="Fix mem leak", annotation_type="FIXME")
        )
        await skill.handle(
            _make_annotation_request(message="Temporary workaround", annotation_type="HACK")
        )
        req = _make_request(intent="dev_next")
        resp = await skill.handle(req)
        assert "[FIXME]" in resp.message
        assert "[HACK]" in resp.message


# ===================================================================
# 11. TestDevWatcherQueryIdeas
# ===================================================================


class TestDevWatcherQueryIdeas:
    """Tests for dev_ideas intent."""

    @pytest.mark.asyncio
    async def test_no_ideas(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_request(intent="dev_ideas")
        resp = await skill.handle(req)
        assert resp.success is True
        assert "No active ideas" in resp.message

    @pytest.mark.asyncio
    async def test_with_active_ideas(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(
            _make_annotation_request(
                message="Build caching layer",
                annotation_type="IDEA",
                file="src/cache.py",
            )
        )
        await skill.handle(
            _make_annotation_request(
                message="Add GraphQL endpoint",
                annotation_type="IDEA",
                file="src/api.py",
            )
        )
        req = _make_request(intent="dev_ideas")
        resp = await skill.handle(req)
        assert "Captured Ideas (2)" in resp.message
        assert "Build caching layer" in resp.message
        assert "Add GraphQL endpoint" in resp.message
        assert "ideas" in resp.data

    @pytest.mark.asyncio
    async def test_resolved_ideas_excluded(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(
            _make_annotation_request(
                message="Idea one",
                annotation_type="IDEA",
                file="src/one.py",
                action="added",
            )
        )
        # Manually resolve the idea
        entry = next(iter(skill._entries_cache["user123"].values()))
        entry.status = "resolved"

        req = _make_request(intent="dev_ideas")
        resp = await skill.handle(req)
        assert "No active ideas" in resp.message

    @pytest.mark.asyncio
    async def test_todos_not_counted_as_ideas(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(
            _make_annotation_request(
                message="Fix this",
                annotation_type="TODO",
            )
        )
        req = _make_request(intent="dev_ideas")
        resp = await skill.handle(req)
        assert "No active ideas" in resp.message


# ===================================================================
# 12. TestDevWatcherQueryJournal
# ===================================================================


class TestDevWatcherQueryJournal:
    """Tests for dev_journal intent."""

    @pytest.mark.asyncio
    async def test_no_entries(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_request(intent="dev_journal")
        resp = await skill.handle(req)
        assert resp.success is True
        assert "No journal entries" in resp.message

    @pytest.mark.asyncio
    async def test_entries_grouped_by_date(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await _seed_skill(skill)
        req = _make_request(intent="dev_journal")
        resp = await skill.handle(req)
        today_str = datetime.now().strftime("%Y-%m-%d")
        assert "Dev Journal" in resp.message
        assert today_str in resp.message
        assert "entries" in resp.data

    @pytest.mark.asyncio
    async def test_journal_shows_icons(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_commit_request())
        await skill.handle(_make_session_request())
        await skill.handle(_make_tag_request())
        req = _make_request(intent="dev_journal")
        resp = await skill.handle(req)
        assert "[commit]" in resp.message
        assert "[session]" in resp.message
        assert "[tag]" in resp.message

    @pytest.mark.asyncio
    async def test_journal_shows_timestamps(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_commit_request())
        req = _make_request(intent="dev_journal")
        resp = await skill.handle(req)
        # Should contain an HH:MM style time
        hour = datetime.now().strftime("%H:")
        assert hour in resp.message


# ===================================================================
# 13. TestDevWatcherQuerySummary
# ===================================================================


class TestDevWatcherQuerySummary:
    """Tests for dev_summary intent."""

    @pytest.mark.asyncio
    async def test_no_entries(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        req = _make_request(intent="dev_summary")
        resp = await skill.handle(req)
        assert resp.success is True
        assert "No dev activity" in resp.message

    @pytest.mark.asyncio
    async def test_with_mixed_entries(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await _seed_skill(skill)
        req = _make_request(intent="dev_summary")
        resp = await skill.handle(req)
        assert "Dev Summary" in resp.message
        assert "Commits: 2" in resp.message
        assert "Sessions: 1" in resp.message
        assert "Tags: 1" in resp.message
        assert "zetherion-ai" in resp.message

    @pytest.mark.asyncio
    async def test_summary_data_field(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await _seed_skill(skill)
        req = _make_request(intent="dev_summary")
        resp = await skill.handle(req)
        assert resp.data["commits"] == 2
        assert resp.data["sessions"] == 1
        assert resp.data["tags"] == 1
        assert "zetherion-ai" in resp.data["projects"]

    @pytest.mark.asyncio
    async def test_summary_recent_commits(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await _seed_skill(skill)
        req = _make_request(intent="dev_summary")
        resp = await skill.handle(req)
        assert "Recent Commits" in resp.message

    @pytest.mark.asyncio
    async def test_summary_tags_section(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await _seed_skill(skill)
        req = _make_request(intent="dev_summary")
        resp = await skill.handle(req)
        assert "Tags/Releases" in resp.message

    @pytest.mark.asyncio
    async def test_summary_open_ideas(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await _seed_skill(skill)
        req = _make_request(intent="dev_summary")
        resp = await skill.handle(req)
        assert "Open Ideas" in resp.message

    @pytest.mark.asyncio
    async def test_summary_annotations_count(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await _seed_skill(skill)
        req = _make_request(intent="dev_summary")
        resp = await skill.handle(req)
        # 2 annotations total: 1 TODO (active), 1 IDEA (active)
        assert "2 open" in resp.message
        assert "0 resolved" in resp.message


# ===================================================================
# 14. TestDevWatcherHeartbeat
# ===================================================================


class TestDevWatcherHeartbeat:
    """Tests for on_heartbeat method."""

    @pytest.mark.asyncio
    async def test_no_actions_for_empty_user(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        actions = await skill.on_heartbeat(["user123"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_stale_annotations_generate_action(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        # Ingest an annotation and age it past the stale threshold
        await skill.handle(_make_annotation_request(message="Old todo", annotation_type="TODO"))
        entry = next(
            e
            for e in skill._entries_cache["user123"].values()
            if e.metadata.get("annotation_type") == "TODO"
        )
        entry.created_at = datetime.now() - timedelta(days=STALE_ANNOTATION_DAYS + 1)

        actions = await skill.on_heartbeat(["user123"])
        stale_actions = [a for a in actions if a.action_type == "dev_stale_annotation"]
        assert len(stale_actions) == 1
        assert stale_actions[0].user_id == "user123"
        assert stale_actions[0].data["count"] == 1
        assert "TODO" in stale_actions[0].data["by_type"]
        assert stale_actions[0].priority == 2

    @pytest.mark.asyncio
    async def test_old_ideas_generate_action(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(
            _make_annotation_request(
                message="Build caching layer",
                annotation_type="IDEA",
            )
        )
        # Age the idea past the reminder threshold
        entry = next(
            e
            for e in skill._entries_cache["user123"].values()
            if e.metadata.get("annotation_type") == "IDEA"
        )
        entry.created_at = datetime.now() - timedelta(days=IDEA_REMINDER_DAYS + 1)

        actions = await skill.on_heartbeat(["user123"])
        idea_actions = [a for a in actions if a.action_type == "dev_idea_reminder"]
        assert len(idea_actions) == 1
        assert idea_actions[0].data["count"] == 1
        assert idea_actions[0].priority == 3

    @pytest.mark.asyncio
    async def test_multiple_users(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        # Seed user1 with a stale annotation
        await skill.handle(
            _make_annotation_request(message="Old todo", annotation_type="TODO", user_id="user1")
        )
        entry = next(iter(skill._entries_cache["user1"].values()))
        entry.created_at = datetime.now() - timedelta(days=STALE_ANNOTATION_DAYS + 1)

        # user2 has nothing
        actions = await skill.on_heartbeat(["user1", "user2"])
        user1_actions = [a for a in actions if a.user_id == "user1"]
        user2_actions = [a for a in actions if a.user_id == "user2"]
        assert len(user1_actions) >= 1
        assert len(user2_actions) == 0

    @pytest.mark.asyncio
    async def test_heartbeat_skill_name(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_annotation_request(message="Stale", annotation_type="TODO"))
        entry = next(iter(skill._entries_cache["user123"].values()))
        entry.created_at = datetime.now() - timedelta(days=STALE_ANNOTATION_DAYS + 1)

        actions = await skill.on_heartbeat(["user123"])
        assert all(a.skill_name == "dev_watcher" for a in actions)

    @pytest.mark.asyncio
    async def test_recent_annotations_no_stale_action(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_annotation_request(message="Fresh todo", annotation_type="TODO"))
        actions = await skill.on_heartbeat(["user123"])
        stale_actions = [a for a in actions if a.action_type == "dev_stale_annotation"]
        assert len(stale_actions) == 0


# ===================================================================
# 15. TestDevWatcherSystemPrompt
# ===================================================================


class TestDevWatcherSystemPrompt:
    """Tests for get_system_prompt_fragment."""

    @pytest.mark.asyncio
    async def test_no_data_returns_none(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        result = skill.get_system_prompt_fragment("user123")
        assert result is None

    @pytest.mark.asyncio
    async def test_empty_cache_returns_none(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        skill._entries_cache["user123"] = {}
        result = skill.get_system_prompt_fragment("user123")
        assert result is None

    @pytest.mark.asyncio
    async def test_with_commits_returns_string(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_commit_request())
        result = skill.get_system_prompt_fragment("user123")
        assert result is not None
        assert "1 recent commit(s)" in result
        assert result.endswith(".")

    @pytest.mark.asyncio
    async def test_with_commits_and_ideas(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_commit_request())
        await skill.handle(
            _make_annotation_request(
                message="Some idea",
                annotation_type="IDEA",
            )
        )
        result = skill.get_system_prompt_fragment("user123")
        assert result is not None
        assert "1 recent commit(s)" in result
        assert "1 open idea(s)" in result

    @pytest.mark.asyncio
    async def test_resolved_ideas_not_counted(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(
            _make_annotation_request(
                message="Some idea",
                annotation_type="IDEA",
            )
        )
        # Resolve the idea
        entry = next(iter(skill._entries_cache["user123"].values()))
        entry.status = "resolved"

        result = skill.get_system_prompt_fragment("user123")
        assert result is not None
        assert "idea" not in result

    @pytest.mark.asyncio
    async def test_unknown_user_returns_none(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_commit_request(user_id="userA"))
        result = skill.get_system_prompt_fragment("userB")
        assert result is None


# ===================================================================
# 16. TestDevWatcherStorage
# ===================================================================


class TestDevWatcherStorage:
    """Tests for _store_entry and _get_user_entries."""

    @pytest.mark.asyncio
    async def test_store_and_get_cache_only(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_commit_request())
        await skill.handle(_make_commit_request(sha="222"))
        entries = await skill._get_user_entries("user123")
        assert len(entries) == 2

    @pytest.mark.asyncio
    async def test_get_user_entries_empty(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        entries = await skill._get_user_entries("nobody")
        assert entries == []

    @pytest.mark.asyncio
    async def test_store_with_mock_memory(self) -> None:
        mock_memory = AsyncMock()
        mock_memory.filter_by_field = AsyncMock(return_value=[])
        skill = DevWatcherSkill(memory=mock_memory)
        await skill.safe_initialize()

        req = _make_commit_request()
        await skill.handle(req)

        mock_memory.store_with_payload.assert_called_once()
        call_kwargs = mock_memory.store_with_payload.call_args
        assert call_kwargs.kwargs["collection_name"] == DEV_JOURNAL_COLLECTION
        assert "commit" in call_kwargs.kwargs["text"]
        assert call_kwargs.kwargs["payload"]["entry_type"] == "commit"

    @pytest.mark.asyncio
    async def test_get_user_entries_from_memory(self) -> None:
        now = datetime.now()
        mock_memory = AsyncMock()
        mock_memory.filter_by_field = AsyncMock(
            return_value=[
                {
                    "id": str(uuid4()),
                    "user_id": "user123",
                    "entry_type": "commit",
                    "project": "proj",
                    "title": "feat: from memory",
                    "content": "content",
                    "metadata": {},
                    "status": "active",
                    "created_at": now.isoformat(),
                }
            ]
        )
        skill = DevWatcherSkill(memory=mock_memory)
        await skill.safe_initialize()
        entries = await skill._get_user_entries("user123")
        assert len(entries) == 1
        assert entries[0].title == "feat: from memory"
        mock_memory.filter_by_field.assert_called_once_with(
            collection_name=DEV_JOURNAL_COLLECTION,
            field="user_id",
            value="user123",
        )

    @pytest.mark.asyncio
    async def test_get_recent_entries_sorted(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        # Insert entries with different timestamps
        entry_old = DevEntry(
            user_id="user123",
            entry_type="commit",
            title="old",
            created_at=datetime.now() - timedelta(hours=2),
        )
        entry_new = DevEntry(
            user_id="user123",
            entry_type="commit",
            title="new",
            created_at=datetime.now(),
        )
        await skill._store_entry(entry_old)
        await skill._store_entry(entry_new)

        entries = await skill._get_recent_entries("user123", limit=10)
        assert entries[0].title == "new"
        assert entries[1].title == "old"

    @pytest.mark.asyncio
    async def test_get_recent_entries_filters_by_type(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_commit_request())
        await skill.handle(_make_session_request())
        await skill.handle(_make_tag_request())

        entries = await skill._get_recent_entries("user123", entry_types=["commit"])
        assert all(e.entry_type == "commit" for e in entries)
        assert len(entries) == 1

    @pytest.mark.asyncio
    async def test_get_active_annotations(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_annotation_request(message="active todo", annotation_type="TODO"))
        await skill.handle(_make_annotation_request(message="another", annotation_type="FIXME"))
        # Resolve the second one
        entries = list(skill._entries_cache["user123"].values())
        fixme = next(e for e in entries if e.metadata.get("annotation_type") == "FIXME")
        fixme.status = "resolved"

        active = await skill._get_active_annotations("user123")
        assert len(active) == 1
        assert active[0].metadata["annotation_type"] == "TODO"

    @pytest.mark.asyncio
    async def test_get_entries_by_type(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await _seed_skill(skill)
        commits = await skill._get_entries_by_type("user123", "commit")
        assert all(e.entry_type == "commit" for e in commits)
        assert len(commits) == 2

    @pytest.mark.asyncio
    async def test_resolve_annotation_matches_file_and_content(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(
            _make_annotation_request(
                message="Fix login",
                file="src/login.py",
                action="added",
            )
        )
        await skill.handle(
            _make_annotation_request(
                message="Other todo",
                file="src/other.py",
                action="added",
            )
        )
        # Resolve only the first one
        await skill._resolve_annotation("user123", "src/login.py", "Fix login")

        entries = list(skill._entries_cache["user123"].values())
        login_entry = next(e for e in entries if e.content == "Fix login")
        other_entry = next(e for e in entries if e.content == "Other todo")
        assert login_entry.status == "resolved"
        assert other_entry.status == "active"


# ===================================================================
# 17. TestDevWatcherCleanup
# ===================================================================


class TestDevWatcherCleanup:
    """Tests for cleanup method."""

    @pytest.mark.asyncio
    async def test_clears_cache(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.handle(_make_commit_request())
        assert len(skill._entries_cache) > 0

        await skill.cleanup()
        assert skill._entries_cache == {}

    @pytest.mark.asyncio
    async def test_cleanup_idempotent(self) -> None:
        skill = DevWatcherSkill()
        await skill.safe_initialize()
        await skill.cleanup()
        await skill.cleanup()  # Should not raise
        assert skill._entries_cache == {}


# ===================================================================
# 18. TestHelperFunctions
# ===================================================================


class TestHelperFunctions:
    """Tests for module-level helper functions."""

    def test_format_age_days(self) -> None:
        dt = datetime.now() - timedelta(days=3)
        assert _format_age(dt) == "3d ago"

    def test_format_age_hours(self) -> None:
        dt = datetime.now() - timedelta(hours=5)
        assert _format_age(dt) == "5h ago"

    def test_format_age_minutes(self) -> None:
        dt = datetime.now() - timedelta(minutes=30)
        assert _format_age(dt) == "30m ago"

    def test_format_age_just_now(self) -> None:
        dt = datetime.now()
        result = _format_age(dt)
        assert "m ago" in result

    def test_format_age_one_day(self) -> None:
        dt = datetime.now() - timedelta(days=1)
        assert _format_age(dt) == "1d ago"

    def test_entry_type_icon_commit(self) -> None:
        assert _entry_type_icon("commit") == "[commit]"

    def test_entry_type_icon_annotation(self) -> None:
        assert _entry_type_icon("annotation") == "[note]"

    def test_entry_type_icon_session(self) -> None:
        assert _entry_type_icon("session") == "[session]"

    def test_entry_type_icon_tag(self) -> None:
        assert _entry_type_icon("tag") == "[tag]"

    def test_entry_type_icon_idea(self) -> None:
        assert _entry_type_icon("idea") == "[idea]"

    def test_entry_type_icon_unknown(self) -> None:
        assert _entry_type_icon("something_else") == "[?]"

    def test_entry_type_icon_empty(self) -> None:
        assert _entry_type_icon("") == "[?]"
