"""Comprehensive unit tests for YouTubeStorage.

All database interactions are mocked via AsyncMock so no real PostgreSQL
connection is needed.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import UUID, uuid4

import pytest

from zetherion_ai.skills.youtube.storage import _SCHEMA_SQL, YouTubeStorage, _json_str

# ------------------------------------------------------------------
# Fixtures
# ------------------------------------------------------------------


@pytest.fixture
def mock_pool():
    """Create a mock asyncpg pool that yields an async connection context."""
    pool = MagicMock()
    conn = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return pool, conn


@pytest.fixture
def tenant_id():
    """A deterministic tenant UUID for tests."""
    return UUID("00000000-0000-0000-0000-000000000001")


@pytest.fixture
def channel_id():
    """A deterministic channel UUID for tests."""
    return UUID("00000000-0000-0000-0000-000000000010")


@pytest.fixture
def comment_id():
    """A deterministic comment UUID for tests."""
    return UUID("00000000-0000-0000-0000-000000000020")


@pytest.fixture
def reply_id():
    """A deterministic reply UUID for tests."""
    return UUID("00000000-0000-0000-0000-000000000030")


@pytest.fixture
def video_id():
    """A deterministic video UUID for tests."""
    return UUID("00000000-0000-0000-0000-000000000040")


@pytest.fixture
def assumption_id():
    """A deterministic assumption UUID for tests."""
    return UUID("00000000-0000-0000-0000-000000000050")


# ------------------------------------------------------------------
# 1. __init__
# ------------------------------------------------------------------


class TestInit:
    """Tests for YouTubeStorage.__init__()."""

    def test_init_with_dsn(self):
        """YouTubeStorage can be created with a DSN string."""
        s = YouTubeStorage(dsn="postgresql://localhost/test")
        assert s._dsn == "postgresql://localhost/test"
        assert s._pool is None
        assert s._memory is None

    def test_init_with_pool(self, mock_pool):
        """YouTubeStorage can be created with an existing pool."""
        pool, _ = mock_pool
        s = YouTubeStorage(pool=pool)
        assert s._dsn is None
        assert s._pool is pool

    def test_init_with_dsn_and_pool(self, mock_pool):
        """YouTubeStorage can be created with both dsn and pool."""
        pool, _ = mock_pool
        s = YouTubeStorage(dsn="postgresql://localhost/test", pool=pool)
        assert s._dsn == "postgresql://localhost/test"
        assert s._pool is pool

    def test_init_with_memory(self, mock_pool):
        """YouTubeStorage stores the memory reference."""
        pool, _ = mock_pool
        memory = MagicMock()
        s = YouTubeStorage(pool=pool, memory=memory)
        assert s._memory is memory

    def test_init_raises_without_dsn_or_pool(self):
        """YouTubeStorage raises ValueError if neither dsn nor pool provided."""
        with pytest.raises(ValueError, match="Either dsn or pool must be provided"):
            YouTubeStorage()


# ------------------------------------------------------------------
# 2. initialize()
# ------------------------------------------------------------------


class TestInitialize:
    """Tests for YouTubeStorage.initialize()."""

    @pytest.mark.asyncio
    async def test_initialize_with_existing_pool(self, mock_pool):
        """initialize() uses existing pool and runs schema SQL."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        await s.initialize()

        assert s._pool is pool
        conn.execute.assert_awaited_once_with(_SCHEMA_SQL)

    @pytest.mark.asyncio
    async def test_initialize_creates_pool_from_dsn(self, mock_pool):
        """initialize() creates a pool from DSN when no pool is provided."""
        pool, conn = mock_pool

        with patch("zetherion_ai.skills.youtube.storage.asyncpg") as mock_asyncpg:
            mock_asyncpg.create_pool = AsyncMock(return_value=pool)
            s = YouTubeStorage(dsn="postgresql://localhost/test")

            await s.initialize()

            mock_asyncpg.create_pool.assert_awaited_once_with(
                dsn="postgresql://localhost/test"
            )
            assert s._pool is pool
            conn.execute.assert_awaited_once_with(_SCHEMA_SQL)

    @pytest.mark.asyncio
    async def test_initialize_schema_contains_all_tables(self, mock_pool):
        """The schema SQL creates all required YouTube tables."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        await s.initialize()

        schema_sql = conn.execute.call_args[0][0]
        assert "CREATE TABLE IF NOT EXISTS youtube_channels" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS youtube_videos" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS youtube_comments" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS youtube_channel_stats" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS youtube_intelligence_reports" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS youtube_reply_drafts" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS youtube_tag_recommendations" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS youtube_strategy_documents" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS youtube_assumptions" in schema_sql
        assert "CREATE TABLE IF NOT EXISTS youtube_channel_documents" in schema_sql

    @pytest.mark.asyncio
    async def test_initialize_raises_without_dsn_or_pool(self):
        """initialize() raises RuntimeError when no dsn or pool."""
        s = YouTubeStorage(dsn="dummy")
        s._dsn = None  # Remove dsn after construction
        s._pool = None

        with pytest.raises(RuntimeError, match="Cannot initialize without dsn or pool"):
            await s.initialize()


# ------------------------------------------------------------------
# 3. create_channel()
# ------------------------------------------------------------------


class TestCreateChannel:
    """Tests for YouTubeStorage.create_channel()."""

    @pytest.mark.asyncio
    async def test_create_channel_returns_dict(self, mock_pool, tenant_id):
        """create_channel() inserts and returns dict from row."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {
            "id": uuid4(),
            "tenant_id": tenant_id,
            "channel_youtube_id": "UCxyz",
            "channel_name": "Test Channel",
            "config": "{}",
        }
        conn.fetchrow.return_value = row

        result = await s.create_channel(
            tenant_id=tenant_id,
            channel_youtube_id="UCxyz",
            channel_name="Test Channel",
            config={"lang": "en"},
        )

        assert result == dict(row)
        conn.fetchrow.assert_awaited_once()
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO youtube_channels" in args[0]
        assert "ON CONFLICT" in args[0]
        assert args[1] is tenant_id
        assert args[2] == "UCxyz"
        assert args[3] == "Test Channel"
        assert json.loads(args[4]) == {"lang": "en"}

    @pytest.mark.asyncio
    async def test_create_channel_default_config(self, mock_pool, tenant_id):
        """create_channel() uses empty dict when config is None."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = {"id": uuid4()}

        await s.create_channel(
            tenant_id=tenant_id,
            channel_youtube_id="UCabc",
        )

        args = conn.fetchrow.call_args[0]
        assert json.loads(args[4]) == {}

    @pytest.mark.asyncio
    async def test_create_channel_returns_empty_dict_on_none_row(
        self, mock_pool, tenant_id
    ):
        """create_channel() returns empty dict when fetchrow returns None."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.create_channel(
            tenant_id=tenant_id,
            channel_youtube_id="UCxyz",
        )

        assert result == {}


# ------------------------------------------------------------------
# 4. get_channel()
# ------------------------------------------------------------------


class TestGetChannel:
    """Tests for YouTubeStorage.get_channel()."""

    @pytest.mark.asyncio
    async def test_get_channel_found(self, mock_pool, channel_id):
        """get_channel() returns a dict when channel exists."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": channel_id, "channel_name": "My Channel"}
        conn.fetchrow.return_value = row

        result = await s.get_channel(channel_id)

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "SELECT * FROM youtube_channels WHERE id = $1" in args[0]
        assert args[1] is channel_id

    @pytest.mark.asyncio
    async def test_get_channel_not_found(self, mock_pool, channel_id):
        """get_channel() returns None when channel does not exist."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.get_channel(channel_id)

        assert result is None


# ------------------------------------------------------------------
# 5. get_channel_by_youtube_id()
# ------------------------------------------------------------------


class TestGetChannelByYoutubeId:
    """Tests for YouTubeStorage.get_channel_by_youtube_id()."""

    @pytest.mark.asyncio
    async def test_found(self, mock_pool, tenant_id):
        """Returns dict when channel exists."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        row = {"id": uuid4(), "channel_youtube_id": "UCxyz"}
        conn.fetchrow.return_value = row

        result = await s.get_channel_by_youtube_id(tenant_id, "UCxyz")

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "tenant_id = $1" in args[0]
        assert "channel_youtube_id = $2" in args[0]
        assert args[1] is tenant_id
        assert args[2] == "UCxyz"

    @pytest.mark.asyncio
    async def test_not_found(self, mock_pool, tenant_id):
        """Returns None when channel does not exist."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.get_channel_by_youtube_id(tenant_id, "UCnonexistent")

        assert result is None


# ------------------------------------------------------------------
# 6. upsert_videos()
# ------------------------------------------------------------------


class TestUpsertVideos:
    """Tests for YouTubeStorage.upsert_videos()."""

    @pytest.mark.asyncio
    async def test_upsert_videos_batch(self, mock_pool, channel_id):
        """upsert_videos() inserts each video and returns count."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        videos = [
            {
                "video_youtube_id": "vid1",
                "title": "First Video",
                "description": "desc1",
                "tags": ["tag1"],
                "stats": {"views": 100},
                "published_at": "2026-01-01T00:00:00Z",
            },
            {
                "video_youtube_id": "vid2",
                "title": "Second Video",
            },
        ]

        count = await s.upsert_videos(channel_id, videos)

        assert count == 2
        assert conn.execute.await_count == 2

        # Verify first call
        first_args = conn.execute.call_args_list[0][0]
        assert "INSERT INTO youtube_videos" in first_args[0]
        assert "ON CONFLICT" in first_args[0]
        assert first_args[1] is channel_id
        assert first_args[2] == "vid1"
        assert first_args[3] == "First Video"
        assert first_args[4] == "desc1"
        assert json.loads(first_args[5]) == ["tag1"]
        assert json.loads(first_args[6]) == {"views": 100}
        assert first_args[7] == "2026-01-01T00:00:00Z"

        # Verify second call uses defaults
        second_args = conn.execute.call_args_list[1][0]
        assert second_args[2] == "vid2"
        assert second_args[3] == "Second Video"
        assert second_args[4] == ""  # default description
        assert json.loads(second_args[5]) == []  # default tags
        assert json.loads(second_args[6]) == {}  # default stats
        assert second_args[7] is None  # no published_at

    @pytest.mark.asyncio
    async def test_upsert_videos_empty_list(self, mock_pool, channel_id):
        """upsert_videos() returns 0 for empty list without executing SQL."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        count = await s.upsert_videos(channel_id, [])

        assert count == 0
        conn.execute.assert_not_awaited()


# ------------------------------------------------------------------
# 7. upsert_comments()
# ------------------------------------------------------------------


class TestUpsertComments:
    """Tests for YouTubeStorage.upsert_comments()."""

    @pytest.mark.asyncio
    async def test_upsert_comments_batch(self, mock_pool, channel_id, video_id):
        """upsert_comments() inserts each comment and returns count."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        comments = [
            {
                "video_id": video_id,
                "comment_youtube_id": "cmt1",
                "author": "Alice",
                "text": "Great video!",
                "like_count": 5,
                "published_at": "2026-01-01T00:00:00Z",
                "parent_comment_id": None,
            },
            {
                "comment_youtube_id": "cmt2",
                "text": "Thanks!",
            },
        ]

        count = await s.upsert_comments(channel_id, comments)

        assert count == 2
        assert conn.execute.await_count == 2

        # Verify first call
        first_args = conn.execute.call_args_list[0][0]
        assert "INSERT INTO youtube_comments" in first_args[0]
        assert "ON CONFLICT" in first_args[0]
        assert first_args[1] is channel_id
        assert first_args[2] is video_id
        assert first_args[3] == "cmt1"
        assert first_args[4] == "Alice"
        assert first_args[5] == "Great video!"
        assert first_args[6] == 5
        assert first_args[7] == "2026-01-01T00:00:00Z"
        assert first_args[8] is None

        # Verify second call uses defaults
        second_args = conn.execute.call_args_list[1][0]
        assert second_args[2] is None  # no video_id
        assert second_args[3] == "cmt2"
        assert second_args[4] == ""  # default author
        assert second_args[5] == "Thanks!"
        assert second_args[6] == 0  # default like_count
        assert second_args[7] is None  # no published_at
        assert second_args[8] is None  # no parent_comment_id

    @pytest.mark.asyncio
    async def test_upsert_comments_resolves_video_youtube_id(
        self, mock_pool, channel_id, video_id
    ):
        """upsert_comments() resolves video_youtube_id to video_id via lookup."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        # The comment has video_youtube_id instead of video_id
        comments = [
            {
                "comment_youtube_id": "cmt_resolve",
                "video_youtube_id": "yt_vid_1",
                "text": "Lookup test",
            },
        ]

        # Mock get_video_by_youtube_id to return a video with an id
        # The method uses self._pool.acquire(), so we need to handle nested calls.
        # The first acquire context is for upsert_comments, and within the loop it calls
        # get_video_by_youtube_id which also uses self._pool.acquire().
        # Since both use the same mock context, we need to set up fetchrow
        # to be called by get_video_by_youtube_id and execute by upsert_comments.

        conn.fetchrow.return_value = {"id": video_id, "video_youtube_id": "yt_vid_1"}

        count = await s.upsert_comments(channel_id, comments)

        assert count == 1
        # fetchrow was called for the video lookup
        conn.fetchrow.assert_awaited()
        # execute was called for the insert
        insert_args = conn.execute.call_args[0]
        assert insert_args[2] is video_id  # resolved video_id

    @pytest.mark.asyncio
    async def test_upsert_comments_empty_list(self, mock_pool, channel_id):
        """upsert_comments() returns 0 for empty list."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        count = await s.upsert_comments(channel_id, [])

        assert count == 0
        conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_upsert_comments_video_youtube_id_not_found(
        self, mock_pool, channel_id
    ):
        """upsert_comments() sets video_id to None when video_youtube_id lookup fails."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        comments = [
            {
                "comment_youtube_id": "cmt_nolookup",
                "video_youtube_id": "nonexistent_yt_vid",
                "text": "No video found",
            },
        ]

        # get_video_by_youtube_id returns None
        conn.fetchrow.return_value = None

        count = await s.upsert_comments(channel_id, comments)

        assert count == 1
        insert_args = conn.execute.call_args[0]
        assert insert_args[2] is None  # video_id stays None


# ------------------------------------------------------------------
# 8. get_unanalyzed_comments()
# ------------------------------------------------------------------


class TestGetUnanalyzedComments:
    """Tests for YouTubeStorage.get_unanalyzed_comments()."""

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self, mock_pool, channel_id):
        """get_unanalyzed_comments() returns comments with sentiment IS NULL."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rows = [
            {"id": uuid4(), "text": "Hello", "sentiment": None},
            {"id": uuid4(), "text": "World", "sentiment": None},
        ]
        conn.fetch.return_value = rows

        result = await s.get_unanalyzed_comments(channel_id)

        assert len(result) == 2
        assert result[0]["text"] == "Hello"
        assert result[1]["text"] == "World"
        args = conn.fetch.call_args[0]
        assert "sentiment IS NULL" in args[0]
        assert args[1] is channel_id
        assert args[2] == 500  # default limit

    @pytest.mark.asyncio
    async def test_custom_limit(self, mock_pool, channel_id):
        """get_unanalyzed_comments() respects custom limit."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        await s.get_unanalyzed_comments(channel_id, limit=10)

        args = conn.fetch.call_args[0]
        assert args[2] == 10

    @pytest.mark.asyncio
    async def test_empty_result(self, mock_pool, channel_id):
        """get_unanalyzed_comments() returns empty list when no rows."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        result = await s.get_unanalyzed_comments(channel_id)

        assert result == []


# ------------------------------------------------------------------
# 9. update_comment_analysis()
# ------------------------------------------------------------------


class TestUpdateCommentAnalysis:
    """Tests for YouTubeStorage.update_comment_analysis()."""

    @pytest.mark.asyncio
    async def test_updates_sentiment_category_topics(self, mock_pool, comment_id):
        """update_comment_analysis() sets sentiment, category, and topics."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        await s.update_comment_analysis(
            comment_id=comment_id,
            sentiment="positive",
            category="feedback",
            topics=["content", "quality"],
        )

        conn.execute.assert_awaited_once()
        args = conn.execute.call_args[0]
        assert "UPDATE youtube_comments" in args[0]
        assert "sentiment = $2" in args[0]
        assert "category = $3" in args[0]
        assert "topics = $4::jsonb" in args[0]
        assert args[1] is comment_id
        assert args[2] == "positive"
        assert args[3] == "feedback"
        assert json.loads(args[4]) == ["content", "quality"]


# ------------------------------------------------------------------
# 10. save_report() / get_latest_report() / get_report_history()
# ------------------------------------------------------------------


class TestReports:
    """Tests for intelligence report methods."""

    @pytest.mark.asyncio
    async def test_save_report(self, mock_pool, channel_id):
        """save_report() inserts with correct SQL params."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        report_data = {"summary": "Good engagement", "score": 85}
        row = {"id": uuid4(), "channel_id": channel_id, "report": report_data}
        conn.fetchrow.return_value = row

        result = await s.save_report(
            channel_id=channel_id,
            report_type="full",
            report=report_data,
            model_used="gpt-4",
        )

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO youtube_intelligence_reports" in args[0]
        assert args[1] is channel_id
        assert args[2] == "full"
        assert json.loads(args[3]) == report_data
        assert args[4] == "gpt-4"

    @pytest.mark.asyncio
    async def test_save_report_default_model(self, mock_pool, channel_id):
        """save_report() defaults model_used to empty string."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = {"id": uuid4()}

        await s.save_report(
            channel_id=channel_id,
            report_type="summary",
            report={"data": "test"},
        )

        args = conn.fetchrow.call_args[0]
        assert args[4] == ""

    @pytest.mark.asyncio
    async def test_save_report_returns_empty_dict_on_none(self, mock_pool, channel_id):
        """save_report() returns empty dict when fetchrow returns None."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.save_report(
            channel_id=channel_id, report_type="full", report={}
        )

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_latest_report_found(self, mock_pool, channel_id):
        """get_latest_report() returns a dict when report exists."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": uuid4(), "channel_id": channel_id, "report_type": "full"}
        conn.fetchrow.return_value = row

        result = await s.get_latest_report(channel_id)

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "youtube_intelligence_reports" in args[0]
        assert "ORDER BY generated_at DESC LIMIT 1" in args[0]
        assert args[1] is channel_id

    @pytest.mark.asyncio
    async def test_get_latest_report_not_found(self, mock_pool, channel_id):
        """get_latest_report() returns None when no reports exist."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.get_latest_report(channel_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_report_history(self, mock_pool, channel_id):
        """get_report_history() returns list of dicts."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rows = [
            {"id": uuid4(), "report_type": "full"},
            {"id": uuid4(), "report_type": "summary"},
        ]
        conn.fetch.return_value = rows

        result = await s.get_report_history(channel_id)

        assert len(result) == 2
        args = conn.fetch.call_args[0]
        assert "ORDER BY generated_at DESC" in args[0]
        assert args[1] is channel_id
        assert args[2] == 10  # default limit

    @pytest.mark.asyncio
    async def test_get_report_history_custom_limit(self, mock_pool, channel_id):
        """get_report_history() respects custom limit."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        await s.get_report_history(channel_id, limit=5)

        args = conn.fetch.call_args[0]
        assert args[2] == 5

    @pytest.mark.asyncio
    async def test_get_report_history_empty(self, mock_pool, channel_id):
        """get_report_history() returns empty list when no reports."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        result = await s.get_report_history(channel_id)

        assert result == []


# ------------------------------------------------------------------
# 11. save_reply_draft() / get_reply_drafts() / update_reply_status()
# ------------------------------------------------------------------


class TestReplyDrafts:
    """Tests for reply draft methods."""

    @pytest.mark.asyncio
    async def test_save_reply_draft(self, mock_pool, channel_id):
        """save_reply_draft() inserts with correct SQL params."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        draft = {
            "channel_id": channel_id,
            "comment_id": "yt_comment_123",
            "video_id": "yt_video_456",
            "original_comment": "Nice content!",
            "draft_reply": "Thank you!",
            "confidence": 0.85,
            "category": "appreciation",
            "status": "pending",
            "auto_approved": False,
            "model_used": "gemini-pro",
        }
        row = {"id": uuid4(), **draft}
        conn.fetchrow.return_value = row

        result = await s.save_reply_draft(draft)

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO youtube_reply_drafts" in args[0]
        assert args[1] is channel_id
        assert args[2] == "yt_comment_123"
        assert args[3] == "yt_video_456"
        assert args[4] == "Nice content!"
        assert args[5] == "Thank you!"
        assert args[6] == 0.85
        assert args[7] == "appreciation"
        assert args[8] == "pending"
        assert args[9] is False
        assert args[10] == "gemini-pro"

    @pytest.mark.asyncio
    async def test_save_reply_draft_defaults(self, mock_pool, channel_id):
        """save_reply_draft() uses default values for optional fields."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        draft = {
            "channel_id": channel_id,
            "comment_id": "cmt_1",
            "original_comment": "Hello",
            "draft_reply": "Hi!",
        }
        conn.fetchrow.return_value = {"id": uuid4()}

        await s.save_reply_draft(draft)

        args = conn.fetchrow.call_args[0]
        assert args[3] == ""  # default video_id
        assert args[6] == 0.0  # default confidence
        assert args[7] == "feedback"  # default category
        assert args[8] == "pending"  # default status
        assert args[9] is False  # default auto_approved
        assert args[10] == ""  # default model_used

    @pytest.mark.asyncio
    async def test_get_reply_drafts_no_status_filter(self, mock_pool, channel_id):
        """get_reply_drafts() without status filter returns all drafts."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rows = [
            {"id": uuid4(), "status": "pending"},
            {"id": uuid4(), "status": "approved"},
        ]
        conn.fetch.return_value = rows

        result = await s.get_reply_drafts(channel_id)

        assert len(result) == 2
        args = conn.fetch.call_args[0]
        assert "status" not in args[0] or "status = $2" not in args[0]
        assert args[1] is channel_id
        assert args[2] == 50  # default limit

    @pytest.mark.asyncio
    async def test_get_reply_drafts_with_status_filter(self, mock_pool, channel_id):
        """get_reply_drafts() with status filter applies WHERE clause."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rows = [{"id": uuid4(), "status": "pending"}]
        conn.fetch.return_value = rows

        result = await s.get_reply_drafts(channel_id, status="pending")

        assert len(result) == 1
        args = conn.fetch.call_args[0]
        assert "status = $2" in args[0]
        assert args[1] is channel_id
        assert args[2] == "pending"
        assert args[3] == 50  # default limit

    @pytest.mark.asyncio
    async def test_get_reply_drafts_custom_limit(self, mock_pool, channel_id):
        """get_reply_drafts() respects custom limit."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        await s.get_reply_drafts(channel_id, limit=5)

        args = conn.fetch.call_args[0]
        assert args[2] == 5

    @pytest.mark.asyncio
    async def test_update_reply_status_approved(self, mock_pool, reply_id):
        """update_reply_status() with 'approved' sets reviewed_at."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": reply_id, "status": "approved"}
        conn.fetchrow.return_value = row

        result = await s.update_reply_status(reply_id, "approved")

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "status = $2" in args[0]
        assert "reviewed_at = now()" in args[0]
        assert args[1] is reply_id
        assert args[2] == "approved"

    @pytest.mark.asyncio
    async def test_update_reply_status_rejected(self, mock_pool, reply_id):
        """update_reply_status() with 'rejected' sets reviewed_at."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = {"id": reply_id, "status": "rejected"}

        await s.update_reply_status(reply_id, "rejected")

        args = conn.fetchrow.call_args[0]
        assert "reviewed_at = now()" in args[0]

    @pytest.mark.asyncio
    async def test_update_reply_status_posted(self, mock_pool, reply_id):
        """update_reply_status() with 'posted' sets posted_at."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": reply_id, "status": "posted"}
        conn.fetchrow.return_value = row

        result = await s.update_reply_status(reply_id, "posted")

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "posted_at = now()" in args[0]
        assert "reviewed_at" not in args[0]

    @pytest.mark.asyncio
    async def test_update_reply_status_other(self, mock_pool, reply_id):
        """update_reply_status() with other status does not add timestamp fields."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = {"id": reply_id, "status": "draft"}

        await s.update_reply_status(reply_id, "draft")

        args = conn.fetchrow.call_args[0]
        assert "reviewed_at" not in args[0]
        assert "posted_at" not in args[0]

    @pytest.mark.asyncio
    async def test_update_reply_status_not_found(self, mock_pool, reply_id):
        """update_reply_status() returns None when reply not found."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.update_reply_status(reply_id, "approved")

        assert result is None


# ------------------------------------------------------------------
# 12. save_strategy() / get_latest_strategy() / get_strategy_history()
# ------------------------------------------------------------------


class TestStrategy:
    """Tests for strategy document methods."""

    @pytest.mark.asyncio
    async def test_save_strategy(self, mock_pool, channel_id):
        """save_strategy() inserts with correct SQL params."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        strategy_data = {"goals": ["grow subscribers"], "actions": ["post weekly"]}
        row = {"id": uuid4(), "channel_id": channel_id, "strategy": strategy_data}
        conn.fetchrow.return_value = row

        result = await s.save_strategy(
            channel_id=channel_id,
            strategy_type="full",
            strategy=strategy_data,
            model_used="gpt-4",
            valid_until="2026-12-31T00:00:00Z",
        )

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO youtube_strategy_documents" in args[0]
        assert args[1] is channel_id
        assert args[2] == "full"
        assert json.loads(args[3]) == strategy_data
        assert args[4] == "gpt-4"
        assert args[5] == "2026-12-31T00:00:00Z"

    @pytest.mark.asyncio
    async def test_save_strategy_defaults(self, mock_pool, channel_id):
        """save_strategy() uses defaults for model_used and valid_until."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = {"id": uuid4()}

        await s.save_strategy(
            channel_id=channel_id,
            strategy_type="content",
            strategy={"plan": "test"},
        )

        args = conn.fetchrow.call_args[0]
        assert args[4] == ""  # default model_used
        assert args[5] is None  # default valid_until

    @pytest.mark.asyncio
    async def test_save_strategy_returns_empty_dict_on_none(
        self, mock_pool, channel_id
    ):
        """save_strategy() returns empty dict when fetchrow returns None."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.save_strategy(
            channel_id=channel_id,
            strategy_type="full",
            strategy={},
        )

        assert result == {}

    @pytest.mark.asyncio
    async def test_get_latest_strategy_found(self, mock_pool, channel_id):
        """get_latest_strategy() returns dict when strategy exists."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": uuid4(), "strategy_type": "full"}
        conn.fetchrow.return_value = row

        result = await s.get_latest_strategy(channel_id)

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "youtube_strategy_documents" in args[0]
        assert "ORDER BY generated_at DESC LIMIT 1" in args[0]
        assert args[1] is channel_id

    @pytest.mark.asyncio
    async def test_get_latest_strategy_not_found(self, mock_pool, channel_id):
        """get_latest_strategy() returns None when no strategies exist."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.get_latest_strategy(channel_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_strategy_history(self, mock_pool, channel_id):
        """get_strategy_history() returns list of dicts."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rows = [
            {"id": uuid4(), "strategy_type": "full"},
            {"id": uuid4(), "strategy_type": "content"},
        ]
        conn.fetch.return_value = rows

        result = await s.get_strategy_history(channel_id)

        assert len(result) == 2
        args = conn.fetch.call_args[0]
        assert "ORDER BY generated_at DESC" in args[0]
        assert args[1] is channel_id
        assert args[2] == 10  # default limit

    @pytest.mark.asyncio
    async def test_get_strategy_history_custom_limit(self, mock_pool, channel_id):
        """get_strategy_history() respects custom limit."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        await s.get_strategy_history(channel_id, limit=3)

        args = conn.fetch.call_args[0]
        assert args[2] == 3

    @pytest.mark.asyncio
    async def test_get_strategy_history_empty(self, mock_pool, channel_id):
        """get_strategy_history() returns empty list when no strategies."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        result = await s.get_strategy_history(channel_id)

        assert result == []


# ------------------------------------------------------------------
# 13. get_channels_due_for_analysis()
# ------------------------------------------------------------------


class TestChannelsDueForAnalysis:
    """Tests for YouTubeStorage.get_channels_due_for_analysis()."""

    @pytest.mark.asyncio
    async def test_returns_channels_list(self, mock_pool):
        """get_channels_due_for_analysis() returns list of dicts."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rows = [
            {"id": uuid4(), "channel_name": "Channel A", "last_analysis_at": None},
            {"id": uuid4(), "channel_name": "Channel B", "last_analysis_at": None},
        ]
        conn.fetch.return_value = rows

        result = await s.get_channels_due_for_analysis()

        assert len(result) == 2
        assert result[0]["channel_name"] == "Channel A"
        assert result[1]["channel_name"] == "Channel B"
        args = conn.fetch.call_args[0]
        sql = args[0]
        assert "youtube_channels" in sql
        assert "last_analysis_at IS NULL" in sql
        assert "analysis_interval_h" in sql
        assert "ORDER BY" in sql

    @pytest.mark.asyncio
    async def test_empty_result(self, mock_pool):
        """get_channels_due_for_analysis() returns empty list when no channels due."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        result = await s.get_channels_due_for_analysis()

        assert result == []

    @pytest.mark.asyncio
    async def test_sql_checks_for_new_comments(self, mock_pool):
        """get_channels_due_for_analysis() SQL requires new comments to exist."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        await s.get_channels_due_for_analysis()

        args = conn.fetch.call_args[0]
        sql = args[0]
        assert "EXISTS" in sql
        assert "youtube_comments" in sql


# ------------------------------------------------------------------
# 14. list_channels()
# ------------------------------------------------------------------


class TestListChannels:
    """Tests for YouTubeStorage.list_channels()."""

    @pytest.mark.asyncio
    async def test_returns_list_of_dicts(self, mock_pool, tenant_id):
        """list_channels() returns list of dicts for a tenant."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rows = [
            {"id": uuid4(), "channel_name": "Ch1"},
            {"id": uuid4(), "channel_name": "Ch2"},
        ]
        conn.fetch.return_value = rows

        result = await s.list_channels(tenant_id)

        assert len(result) == 2
        args = conn.fetch.call_args[0]
        assert "tenant_id = $1" in args[0]
        assert "ORDER BY created_at" in args[0]
        assert args[1] is tenant_id

    @pytest.mark.asyncio
    async def test_empty_result(self, mock_pool, tenant_id):
        """list_channels() returns empty list when no channels."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        result = await s.list_channels(tenant_id)

        assert result == []


# ------------------------------------------------------------------
# 15. update_channel()
# ------------------------------------------------------------------


class TestUpdateChannel:
    """Tests for YouTubeStorage.update_channel()."""

    @pytest.mark.asyncio
    async def test_update_channel_name(self, mock_pool, channel_id):
        """update_channel() generates correct UPDATE SQL for channel_name."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": channel_id, "channel_name": "New Name"}
        conn.fetchrow.return_value = row

        result = await s.update_channel(channel_id, channel_name="New Name")

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "UPDATE youtube_channels" in args[0]
        assert "channel_name = $2" in args[0]
        assert "updated_at = now()" in args[0]
        assert args[1] is channel_id
        assert args[2] == "New Name"

    @pytest.mark.asyncio
    async def test_update_config_as_jsonb(self, mock_pool, channel_id):
        """update_channel() serializes config as JSONB."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = {"id": channel_id}

        await s.update_channel(channel_id, config={"key": "value"})

        args = conn.fetchrow.call_args[0]
        assert "config = $2::jsonb" in args[0]
        assert json.loads(args[2]) == {"key": "value"}

    @pytest.mark.asyncio
    async def test_update_no_kwargs_returns_get(self, mock_pool, channel_id):
        """update_channel() with no valid kwargs falls back to get_channel()."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": channel_id, "channel_name": "Unchanged"}
        conn.fetchrow.return_value = row

        result = await s.update_channel(channel_id, invalid_field="ignored")

        # Falls back to get_channel, which calls fetchrow with SELECT
        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "SELECT * FROM youtube_channels" in args[0]

    @pytest.mark.asyncio
    async def test_update_not_found(self, mock_pool, channel_id):
        """update_channel() returns None when channel doesn't exist."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.update_channel(channel_id, channel_name="Test")

        assert result is None


# ------------------------------------------------------------------
# 16. get_videos() / get_video_by_youtube_id()
# ------------------------------------------------------------------


class TestVideos:
    """Tests for video retrieval methods."""

    @pytest.mark.asyncio
    async def test_get_videos(self, mock_pool, channel_id):
        """get_videos() returns list of dicts ordered by published_at."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rows = [{"id": uuid4(), "title": "Video 1"}, {"id": uuid4(), "title": "Video 2"}]
        conn.fetch.return_value = rows

        result = await s.get_videos(channel_id)

        assert len(result) == 2
        args = conn.fetch.call_args[0]
        assert "ORDER BY published_at DESC" in args[0]
        assert args[1] is channel_id
        assert args[2] == 100  # default limit

    @pytest.mark.asyncio
    async def test_get_videos_custom_limit(self, mock_pool, channel_id):
        """get_videos() respects custom limit."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        await s.get_videos(channel_id, limit=10)

        args = conn.fetch.call_args[0]
        assert args[2] == 10

    @pytest.mark.asyncio
    async def test_get_video_by_youtube_id_found(self, mock_pool, channel_id):
        """get_video_by_youtube_id() returns dict when found."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": uuid4(), "video_youtube_id": "vid1"}
        conn.fetchrow.return_value = row

        result = await s.get_video_by_youtube_id(channel_id, "vid1")

        assert result == dict(row)

    @pytest.mark.asyncio
    async def test_get_video_by_youtube_id_not_found(self, mock_pool, channel_id):
        """get_video_by_youtube_id() returns None when not found."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.get_video_by_youtube_id(channel_id, "nonexistent")

        assert result is None


# ------------------------------------------------------------------
# 17. get_comments() / count_comments_since()
# ------------------------------------------------------------------


class TestComments:
    """Tests for comment retrieval methods."""

    @pytest.mark.asyncio
    async def test_get_comments_no_since(self, mock_pool, channel_id):
        """get_comments() without since returns all comments."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rows = [{"id": uuid4(), "text": "Hello"}]
        conn.fetch.return_value = rows

        result = await s.get_comments(channel_id)

        assert len(result) == 1
        args = conn.fetch.call_args[0]
        assert "ingested_at >" not in args[0]
        assert args[1] is channel_id
        assert args[2] == 500  # default limit

    @pytest.mark.asyncio
    async def test_get_comments_with_since(self, mock_pool, channel_id):
        """get_comments() with since applies timestamp filter."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        await s.get_comments(channel_id, since="2026-01-01T00:00:00Z")

        args = conn.fetch.call_args[0]
        assert "ingested_at > $2" in args[0]
        assert args[1] is channel_id
        assert args[2] == "2026-01-01T00:00:00Z"
        assert args[3] == 500

    @pytest.mark.asyncio
    async def test_count_comments_since(self, mock_pool, channel_id):
        """count_comments_since() returns integer count."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        conn.fetchrow.return_value = {"cnt": 42}

        result = await s.count_comments_since(channel_id, "2026-01-01T00:00:00Z")

        assert result == 42
        args = conn.fetchrow.call_args[0]
        assert "count(*)" in args[0]
        assert args[1] is channel_id
        assert args[2] == "2026-01-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_count_comments_since_none_row(self, mock_pool, channel_id):
        """count_comments_since() returns 0 when fetchrow returns None."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.count_comments_since(channel_id, "2026-01-01T00:00:00Z")

        assert result == 0


# ------------------------------------------------------------------
# 18. insert_stats() / get_latest_stats()
# ------------------------------------------------------------------


class TestStats:
    """Tests for channel stats methods."""

    @pytest.mark.asyncio
    async def test_insert_stats(self, mock_pool, channel_id):
        """insert_stats() inserts with correct SQL params."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        snapshot = {"subscribers": 1000, "views": 50000}
        row = {"id": uuid4(), "channel_id": channel_id, "snapshot": snapshot}
        conn.fetchrow.return_value = row

        result = await s.insert_stats(channel_id, snapshot)

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO youtube_channel_stats" in args[0]
        assert args[1] is channel_id
        assert json.loads(args[2]) == snapshot

    @pytest.mark.asyncio
    async def test_get_latest_stats_found(self, mock_pool, channel_id):
        """get_latest_stats() returns dict when stats exist."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": uuid4(), "snapshot": {"subs": 500}}
        conn.fetchrow.return_value = row

        result = await s.get_latest_stats(channel_id)

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "ORDER BY recorded_at DESC LIMIT 1" in args[0]

    @pytest.mark.asyncio
    async def test_get_latest_stats_not_found(self, mock_pool, channel_id):
        """get_latest_stats() returns None when no stats exist."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.get_latest_stats(channel_id)

        assert result is None


# ------------------------------------------------------------------
# 19. count_replies_today()
# ------------------------------------------------------------------


class TestCountRepliesToday:
    """Tests for YouTubeStorage.count_replies_today()."""

    @pytest.mark.asyncio
    async def test_returns_count(self, mock_pool, channel_id):
        """count_replies_today() returns the count from the query."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        conn.fetchrow.return_value = {"cnt": 7}

        result = await s.count_replies_today(channel_id)

        assert result == 7
        args = conn.fetchrow.call_args[0]
        assert "count(*)" in args[0]
        assert "status = 'posted'" in args[0]
        assert "CURRENT_DATE" in args[0]
        assert args[1] is channel_id

    @pytest.mark.asyncio
    async def test_returns_zero_on_none(self, mock_pool, channel_id):
        """count_replies_today() returns 0 when fetchrow returns None."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.count_replies_today(channel_id)

        assert result == 0


# ------------------------------------------------------------------
# 20. Tag recommendations
# ------------------------------------------------------------------


class TestTagRecommendations:
    """Tests for tag recommendation methods."""

    @pytest.mark.asyncio
    async def test_save_tag_recommendation(self, mock_pool, channel_id):
        """save_tag_recommendation() inserts with correct params."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rec = {
            "channel_id": channel_id,
            "video_id": "yt_vid_1",
            "current_tags": ["tag1"],
            "suggested_tags": ["tag1", "tag2", "tag3"],
            "reason": "Better discoverability",
        }
        row = {"id": uuid4(), **rec}
        conn.fetchrow.return_value = row

        result = await s.save_tag_recommendation(rec)

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO youtube_tag_recommendations" in args[0]
        assert args[1] is channel_id
        assert args[2] == "yt_vid_1"
        assert json.loads(args[3]) == ["tag1"]
        assert json.loads(args[4]) == ["tag1", "tag2", "tag3"]
        assert args[5] == "Better discoverability"

    @pytest.mark.asyncio
    async def test_get_tag_recommendations_no_filter(self, mock_pool, channel_id):
        """get_tag_recommendations() without status returns all."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = [{"id": uuid4()}]

        result = await s.get_tag_recommendations(channel_id)

        assert len(result) == 1
        args = conn.fetch.call_args[0]
        assert args[1] is channel_id
        assert args[2] == 50  # default limit

    @pytest.mark.asyncio
    async def test_get_tag_recommendations_with_status(self, mock_pool, channel_id):
        """get_tag_recommendations() with status filter applies WHERE."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        await s.get_tag_recommendations(channel_id, status="pending")

        args = conn.fetch.call_args[0]
        assert "status = $2" in args[0]
        assert args[2] == "pending"


# ------------------------------------------------------------------
# 21. Assumptions
# ------------------------------------------------------------------


class TestAssumptions:
    """Tests for assumption methods."""

    @pytest.mark.asyncio
    async def test_save_assumption(self, mock_pool, channel_id):
        """save_assumption() inserts with correct params."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        assumption = {
            "channel_id": channel_id,
            "category": "audience",
            "statement": "Audience prefers short videos",
            "evidence": [{"type": "data", "source": "analytics"}],
            "confidence": 0.7,
            "source": "inferred",
            "confirmed_at": None,
            "next_validation": "2026-03-01T00:00:00Z",
        }
        row = {"id": uuid4(), **assumption}
        conn.fetchrow.return_value = row

        result = await s.save_assumption(assumption)

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO youtube_assumptions" in args[0]
        assert args[1] is channel_id
        assert args[2] == "audience"
        assert args[3] == "Audience prefers short videos"
        assert json.loads(args[4]) == [{"type": "data", "source": "analytics"}]
        assert args[5] == 0.7
        assert args[6] == "inferred"
        assert args[7] is None
        assert args[8] == "2026-03-01T00:00:00Z"

    @pytest.mark.asyncio
    async def test_get_assumptions_no_filter(self, mock_pool, channel_id):
        """get_assumptions() without source returns all."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = [{"id": uuid4(), "category": "audience"}]

        result = await s.get_assumptions(channel_id)

        assert len(result) == 1
        args = conn.fetch.call_args[0]
        assert "ORDER BY confidence DESC" in args[0]

    @pytest.mark.asyncio
    async def test_get_assumptions_with_source(self, mock_pool, channel_id):
        """get_assumptions() with source applies WHERE clause."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        await s.get_assumptions(channel_id, source="confirmed")

        args = conn.fetch.call_args[0]
        assert "source = $2" in args[0]
        assert args[2] == "confirmed"

    @pytest.mark.asyncio
    async def test_get_assumption_found(self, mock_pool, assumption_id):
        """get_assumption() returns dict when found."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        row = {"id": assumption_id, "statement": "Test"}
        conn.fetchrow.return_value = row

        result = await s.get_assumption(assumption_id)

        assert result == dict(row)

    @pytest.mark.asyncio
    async def test_get_assumption_not_found(self, mock_pool, assumption_id):
        """get_assumption() returns None when not found."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = None

        result = await s.get_assumption(assumption_id)

        assert result is None

    @pytest.mark.asyncio
    async def test_get_stale_assumptions(self, mock_pool):
        """get_stale_assumptions() returns assumptions past validation date."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rows = [
            {"id": uuid4(), "statement": "Old assumption", "tenant_id": uuid4()},
        ]
        conn.fetch.return_value = rows

        result = await s.get_stale_assumptions()

        assert len(result) == 1
        args = conn.fetch.call_args[0]
        assert "next_validation <= now()" in args[0]
        assert "NOT IN ('invalidated')" in args[0]

    @pytest.mark.asyncio
    async def test_update_assumption(self, mock_pool, assumption_id):
        """update_assumption() generates correct UPDATE SQL."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": assumption_id, "confidence": 0.9}
        conn.fetchrow.return_value = row

        result = await s.update_assumption(
            assumption_id, confidence=0.9, source="confirmed"
        )

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "UPDATE youtube_assumptions" in args[0]
        assert args[1] is assumption_id

    @pytest.mark.asyncio
    async def test_update_assumption_no_kwargs(self, mock_pool, assumption_id):
        """update_assumption() with no valid kwargs calls get_assumption()."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": assumption_id, "statement": "Test"}
        conn.fetchrow.return_value = row

        result = await s.update_assumption(assumption_id, invalid_key="ignored")

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "SELECT * FROM youtube_assumptions" in args[0]

    @pytest.mark.asyncio
    async def test_update_assumption_evidence_jsonb(self, mock_pool, assumption_id):
        """update_assumption() serializes evidence as JSONB."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = {"id": assumption_id}

        evidence = [{"source": "survey", "data": "positive"}]
        await s.update_assumption(assumption_id, evidence=evidence)

        args = conn.fetchrow.call_args[0]
        assert "evidence = $" in args[0]
        assert "::jsonb" in args[0]
        # evidence should be in the args as a json string
        json_args = [a for a in args[1:] if isinstance(a, str) and a.startswith("[")]
        assert len(json_args) == 1
        assert json.loads(json_args[0]) == evidence

    @pytest.mark.asyncio
    async def test_update_assumption_timestamp_fields(self, mock_pool, assumption_id):
        """update_assumption() handles confirmed_at, last_validated, next_validation
        as timestamptz (lines 780-782)."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = {"id": assumption_id, "confirmed_at": "2026-02-01"}

        await s.update_assumption(
            assumption_id,
            confirmed_at="2026-02-01T00:00:00Z",
            last_validated="2026-02-10T00:00:00Z",
            next_validation="2026-03-01T00:00:00Z",
        )

        args = conn.fetchrow.call_args[0]
        sql = args[0]
        assert "confirmed_at = $2::timestamptz" in sql
        assert "last_validated = $3::timestamptz" in sql
        assert "next_validation = $4::timestamptz" in sql
        assert args[1] is assumption_id
        assert args[2] == "2026-02-01T00:00:00Z"
        assert args[3] == "2026-02-10T00:00:00Z"
        assert args[4] == "2026-03-01T00:00:00Z"


# ------------------------------------------------------------------
# 22. Documents
# ------------------------------------------------------------------


class TestDocuments:
    """Tests for document methods."""

    @pytest.mark.asyncio
    async def test_save_document(self, mock_pool, channel_id):
        """save_document() inserts with correct params."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        row = {"id": uuid4(), "title": "Brand Guide"}
        conn.fetchrow.return_value = row

        result = await s.save_document(
            channel_id=channel_id,
            title="Brand Guide",
            content="Our brand values...",
            doc_type="brand",
        )

        assert result == dict(row)
        args = conn.fetchrow.call_args[0]
        assert "INSERT INTO youtube_channel_documents" in args[0]
        assert args[1] is channel_id
        assert args[2] == "Brand Guide"
        assert args[3] == "Our brand values..."
        assert args[4] == "brand"

    @pytest.mark.asyncio
    async def test_save_document_default_type(self, mock_pool, channel_id):
        """save_document() defaults doc_type to empty string."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetchrow.return_value = {"id": uuid4()}

        await s.save_document(
            channel_id=channel_id,
            title="Test",
            content="Content",
        )

        args = conn.fetchrow.call_args[0]
        assert args[4] == ""

    @pytest.mark.asyncio
    async def test_get_documents(self, mock_pool, channel_id):
        """get_documents() returns list of dicts."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)

        rows = [{"id": uuid4(), "title": "Doc 1"}, {"id": uuid4(), "title": "Doc 2"}]
        conn.fetch.return_value = rows

        result = await s.get_documents(channel_id)

        assert len(result) == 2
        args = conn.fetch.call_args[0]
        assert "youtube_channel_documents" in args[0]
        assert "ORDER BY created_at DESC" in args[0]
        assert args[1] is channel_id

    @pytest.mark.asyncio
    async def test_get_documents_empty(self, mock_pool, channel_id):
        """get_documents() returns empty list when no documents."""
        pool, conn = mock_pool
        s = YouTubeStorage(pool=pool)
        conn.fetch.return_value = []

        result = await s.get_documents(channel_id)

        assert result == []


# ------------------------------------------------------------------
# 23. _json_str helper
# ------------------------------------------------------------------


class TestJsonStr:
    """Tests for the _json_str helper function."""

    def test_dict_serialization(self):
        """_json_str serializes a dict to JSON string."""
        result = _json_str({"key": "value", "num": 42})
        assert json.loads(result) == {"key": "value", "num": 42}

    def test_list_serialization(self):
        """_json_str serializes a list to JSON string."""
        result = _json_str(["a", "b", "c"])
        assert json.loads(result) == ["a", "b", "c"]

    def test_empty_dict(self):
        """_json_str serializes empty dict."""
        result = _json_str({})
        assert result == "{}"

    def test_empty_list(self):
        """_json_str serializes empty list."""
        result = _json_str([])
        assert result == "[]"

    def test_uuid_serialization(self):
        """_json_str handles UUID via default=str."""
        uid = uuid4()
        result = _json_str({"id": uid})
        parsed = json.loads(result)
        assert parsed["id"] == str(uid)
