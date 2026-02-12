"""PostgreSQL + Qdrant storage for the YouTube skills.

Follows the same pool/schema pattern as TenantManager.  All tables
live in the same database and reference tenants(tenant_id).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from uuid import UUID

import asyncpg  # type: ignore[import-untyped,import-not-found]

from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.skills.youtube.storage")

# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """\
-- YouTube channel registry
CREATE TABLE IF NOT EXISTS youtube_channels (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id           UUID         NOT NULL,
    channel_youtube_id  TEXT         NOT NULL,
    channel_name        TEXT,
    config              JSONB        DEFAULT '{}'::jsonb,
    onboarding_complete BOOLEAN      DEFAULT FALSE,
    trust_level         INTEGER      DEFAULT 0,
    trust_stats         JSONB        DEFAULT '{"total":0,"approved":0,"rejected":0}'::jsonb,
    last_analysis_at    TIMESTAMPTZ,
    analysis_interval_h INTEGER      DEFAULT 24,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE(tenant_id, channel_youtube_id)
);

CREATE INDEX IF NOT EXISTS idx_yt_channels_tenant
    ON youtube_channels (tenant_id);

-- Ingested videos
CREATE TABLE IF NOT EXISTS youtube_videos (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id          UUID         NOT NULL REFERENCES youtube_channels(id) ON DELETE CASCADE,
    video_youtube_id    TEXT         NOT NULL,
    title               TEXT,
    description         TEXT,
    tags                JSONB        DEFAULT '[]'::jsonb,
    stats               JSONB        DEFAULT '{}'::jsonb,
    published_at        TIMESTAMPTZ,
    ingested_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE(channel_id, video_youtube_id)
);

CREATE INDEX IF NOT EXISTS idx_yt_videos_channel
    ON youtube_videos (channel_id, published_at DESC);

-- Ingested comments
CREATE TABLE IF NOT EXISTS youtube_comments (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id          UUID         NOT NULL REFERENCES youtube_channels(id) ON DELETE CASCADE,
    video_id            UUID         REFERENCES youtube_videos(id) ON DELETE SET NULL,
    comment_youtube_id  TEXT         NOT NULL,
    author              TEXT,
    text                TEXT         NOT NULL,
    like_count          INTEGER      DEFAULT 0,
    published_at        TIMESTAMPTZ,
    parent_comment_id   TEXT,
    ingested_at         TIMESTAMPTZ  NOT NULL DEFAULT now(),
    sentiment           TEXT,
    category            TEXT,
    topics              JSONB        DEFAULT '[]'::jsonb,
    UNIQUE(channel_id, comment_youtube_id)
);

CREATE INDEX IF NOT EXISTS idx_yt_comments_channel
    ON youtube_comments (channel_id, ingested_at DESC);
CREATE INDEX IF NOT EXISTS idx_yt_comments_video
    ON youtube_comments (video_id) WHERE video_id IS NOT NULL;

-- Channel stats snapshots
CREATE TABLE IF NOT EXISTS youtube_channel_stats (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id          UUID         NOT NULL REFERENCES youtube_channels(id) ON DELETE CASCADE,
    snapshot            JSONB        NOT NULL,
    recorded_at         TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_yt_stats_channel
    ON youtube_channel_stats (channel_id, recorded_at DESC);

-- Intelligence reports
CREATE TABLE IF NOT EXISTS youtube_intelligence_reports (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id          UUID         NOT NULL REFERENCES youtube_channels(id) ON DELETE CASCADE,
    report_type         TEXT         NOT NULL DEFAULT 'full',
    report              JSONB        NOT NULL,
    model_used          TEXT,
    generated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_yt_reports_channel
    ON youtube_intelligence_reports (channel_id, generated_at DESC);

-- Reply drafts
CREATE TABLE IF NOT EXISTS youtube_reply_drafts (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id          UUID         NOT NULL REFERENCES youtube_channels(id) ON DELETE CASCADE,
    comment_id          TEXT         NOT NULL,
    video_id            TEXT,
    original_comment    TEXT         NOT NULL,
    draft_reply         TEXT         NOT NULL,
    confidence          REAL         DEFAULT 0.0,
    category            TEXT         DEFAULT 'feedback',
    status              TEXT         DEFAULT 'pending',
    auto_approved       BOOLEAN      DEFAULT FALSE,
    model_used          TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    reviewed_at         TIMESTAMPTZ,
    posted_at           TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_yt_replies_channel_status
    ON youtube_reply_drafts (channel_id, status);

-- Tag recommendations
CREATE TABLE IF NOT EXISTS youtube_tag_recommendations (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id          UUID         NOT NULL REFERENCES youtube_channels(id) ON DELETE CASCADE,
    video_id            TEXT         NOT NULL,
    current_tags        JSONB        DEFAULT '[]'::jsonb,
    suggested_tags      JSONB        DEFAULT '[]'::jsonb,
    reason              TEXT,
    status              TEXT         DEFAULT 'pending',
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_yt_tags_channel
    ON youtube_tag_recommendations (channel_id, status);

-- Strategy documents
CREATE TABLE IF NOT EXISTS youtube_strategy_documents (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id          UUID         NOT NULL REFERENCES youtube_channels(id) ON DELETE CASCADE,
    strategy_type       TEXT         NOT NULL DEFAULT 'full',
    strategy            JSONB        NOT NULL,
    model_used          TEXT,
    valid_until         TIMESTAMPTZ,
    generated_at        TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_yt_strategy_channel
    ON youtube_strategy_documents (channel_id, generated_at DESC);

-- Assumptions
CREATE TABLE IF NOT EXISTS youtube_assumptions (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id          UUID         NOT NULL REFERENCES youtube_channels(id) ON DELETE CASCADE,
    category            TEXT         NOT NULL,
    statement           TEXT         NOT NULL,
    evidence            JSONB        DEFAULT '[]'::jsonb,
    confidence          REAL         DEFAULT 0.0,
    source              TEXT         NOT NULL DEFAULT 'inferred',
    confirmed_at        TIMESTAMPTZ,
    last_validated      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    next_validation     TIMESTAMPTZ  NOT NULL DEFAULT (now() + INTERVAL '7 days')
);

CREATE INDEX IF NOT EXISTS idx_yt_assumptions_channel
    ON youtube_assumptions (channel_id);
CREATE INDEX IF NOT EXISTS idx_yt_assumptions_validation
    ON youtube_assumptions (next_validation) WHERE source != 'invalidated';

-- Client documents for RAG
CREATE TABLE IF NOT EXISTS youtube_channel_documents (
    id                  UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
    channel_id          UUID         NOT NULL REFERENCES youtube_channels(id) ON DELETE CASCADE,
    title               TEXT         NOT NULL,
    content             TEXT         NOT NULL,
    doc_type            TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_yt_docs_channel
    ON youtube_channel_documents (channel_id);
"""


class YouTubeStorage:
    """PostgreSQL + Qdrant storage backend for all YouTube skills."""

    def __init__(
        self,
        dsn: str | None = None,
        pool: asyncpg.Pool | None = None,
        memory: QdrantMemory | None = None,
    ) -> None:
        if dsn is None and pool is None:
            raise ValueError("Either dsn or pool must be provided")
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = pool
        self._memory = memory

    @property
    def _db(self) -> asyncpg.Pool:
        """Return the connection pool, raising if not yet initialised."""
        assert self._pool is not None, "Storage not initialized — call initialize() first"
        return self._pool

    async def initialize(self) -> None:
        """Create connection pool (if needed) and tables."""
        if self._pool is None:
            if self._dsn is None:
                raise RuntimeError("Cannot initialize without dsn or pool")
            self._pool = await asyncpg.create_pool(dsn=self._dsn)
            log.info("youtube_storage_pool_created")
        async with self._db.acquire() as conn:
            await conn.execute(_SCHEMA_SQL)
        log.info("youtube_storage_initialized")

    # ------------------------------------------------------------------
    # Channels
    # ------------------------------------------------------------------

    async def create_channel(
        self,
        tenant_id: UUID,
        channel_youtube_id: str,
        channel_name: str = "",
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO youtube_channels
                       (tenant_id, channel_youtube_id, channel_name, config)
                   VALUES ($1, $2, $3, $4::jsonb)
                   ON CONFLICT (tenant_id, channel_youtube_id)
                   DO UPDATE SET channel_name = EXCLUDED.channel_name,
                                 updated_at = now()
                   RETURNING *""",
                tenant_id,
                channel_youtube_id,
                channel_name,
                _json_str(config or {}),
            )
        return dict(row) if row else {}

    async def get_channel(self, channel_id: UUID) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM youtube_channels WHERE id = $1", channel_id)
        return dict(row) if row else None

    async def get_channel_by_youtube_id(
        self, tenant_id: UUID, channel_youtube_id: str
    ) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM youtube_channels
                   WHERE tenant_id = $1 AND channel_youtube_id = $2""",
                tenant_id,
                channel_youtube_id,
            )
        return dict(row) if row else None

    async def list_channels(self, tenant_id: UUID) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM youtube_channels
                   WHERE tenant_id = $1 ORDER BY created_at""",
                tenant_id,
            )
        return [dict(r) for r in rows]

    async def update_channel(self, channel_id: UUID, **kwargs: Any) -> dict[str, Any] | None:
        sets: list[str] = []
        args: list[Any] = []
        i = 2  # $1 = channel_id
        for key, val in kwargs.items():
            if key in (
                "channel_name",
                "config",
                "onboarding_complete",
                "trust_level",
                "trust_stats",
                "last_analysis_at",
                "analysis_interval_h",
            ):
                if key in ("config", "trust_stats"):
                    sets.append(f"{key} = ${i}::jsonb")
                    args.append(_json_str(val))
                else:
                    sets.append(f"{key} = ${i}")
                    args.append(val)
                i += 1
        if not sets:
            return await self.get_channel(channel_id)
        sets.append("updated_at = now()")
        sql = f"UPDATE youtube_channels SET {', '.join(sets)} WHERE id = $1 RETURNING *"  # nosec B608
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(sql, channel_id, *args)
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Videos
    # ------------------------------------------------------------------

    async def upsert_videos(self, channel_id: UUID, videos: list[dict[str, Any]]) -> int:
        """Upsert a batch of videos.  Returns count of rows affected."""
        if not videos:
            return 0
        sql = """INSERT INTO youtube_videos
                     (channel_id, video_youtube_id, title, description, tags, stats, published_at)
                 VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7)
                 ON CONFLICT (channel_id, video_youtube_id)
                 DO UPDATE SET title = EXCLUDED.title,
                               description = EXCLUDED.description,
                               tags = EXCLUDED.tags,
                               stats = EXCLUDED.stats,
                               published_at = EXCLUDED.published_at"""
        async with self._db.acquire() as conn:
            count = 0
            for v in videos:
                await conn.execute(
                    sql,
                    channel_id,
                    v["video_youtube_id"],
                    v.get("title", ""),
                    v.get("description", ""),
                    _json_str(v.get("tags", [])),
                    _json_str(v.get("stats", {})),
                    v.get("published_at"),
                )
                count += 1
        return count

    async def get_videos(self, channel_id: UUID, *, limit: int = 100) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM youtube_videos
                   WHERE channel_id = $1
                   ORDER BY published_at DESC NULLS LAST
                   LIMIT $2""",
                channel_id,
                limit,
            )
        return [dict(r) for r in rows]

    async def get_video_by_youtube_id(
        self, channel_id: UUID, video_youtube_id: str
    ) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM youtube_videos
                   WHERE channel_id = $1 AND video_youtube_id = $2""",
                channel_id,
                video_youtube_id,
            )
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Comments
    # ------------------------------------------------------------------

    async def upsert_comments(self, channel_id: UUID, comments: list[dict[str, Any]]) -> int:
        if not comments:
            return 0
        sql = """INSERT INTO youtube_comments
                     (channel_id, video_id, comment_youtube_id, author, text,
                      like_count, published_at, parent_comment_id)
                 VALUES ($1, $2, $3, $4, $5, $6, $7, $8)
                 ON CONFLICT (channel_id, comment_youtube_id)
                 DO UPDATE SET text = EXCLUDED.text,
                               like_count = EXCLUDED.like_count"""
        async with self._db.acquire() as conn:
            count = 0
            for c in comments:
                # Resolve video_id from YouTube video ID if provided
                video_id = c.get("video_id")
                if video_id is None and c.get("video_youtube_id"):
                    vid = await self.get_video_by_youtube_id(channel_id, c["video_youtube_id"])
                    if vid:
                        video_id = vid["id"]
                await conn.execute(
                    sql,
                    channel_id,
                    video_id,
                    c["comment_youtube_id"],
                    c.get("author", ""),
                    c["text"],
                    c.get("like_count", 0),
                    c.get("published_at"),
                    c.get("parent_comment_id"),
                )
                count += 1
        return count

    async def get_comments(
        self,
        channel_id: UUID,
        *,
        since: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        if since:
            sql = """SELECT * FROM youtube_comments
                     WHERE channel_id = $1 AND ingested_at > $2::timestamptz
                     ORDER BY ingested_at DESC LIMIT $3"""
            args: list[Any] = [channel_id, since, limit]
        else:
            sql = """SELECT * FROM youtube_comments
                     WHERE channel_id = $1
                     ORDER BY ingested_at DESC LIMIT $2"""
            args = [channel_id, limit]
        async with self._db.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def get_unanalyzed_comments(
        self, channel_id: UUID, *, limit: int = 500
    ) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM youtube_comments
                   WHERE channel_id = $1 AND sentiment IS NULL
                   ORDER BY ingested_at DESC LIMIT $2""",
                channel_id,
                limit,
            )
        return [dict(r) for r in rows]

    async def update_comment_analysis(
        self,
        comment_id: UUID,
        sentiment: str,
        category: str,
        topics: list[str],
    ) -> None:
        async with self._db.acquire() as conn:
            await conn.execute(
                """UPDATE youtube_comments
                   SET sentiment = $2, category = $3, topics = $4::jsonb
                   WHERE id = $1""",
                comment_id,
                sentiment,
                category,
                _json_str(topics),
            )

    async def count_comments_since(self, channel_id: UUID, since: str) -> int:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT count(*) as cnt FROM youtube_comments
                   WHERE channel_id = $1 AND ingested_at > $2::timestamptz""",
                channel_id,
                since,
            )
        return int(row["cnt"]) if row else 0

    # ------------------------------------------------------------------
    # Stats
    # ------------------------------------------------------------------

    async def insert_stats(self, channel_id: UUID, snapshot: dict[str, Any]) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO youtube_channel_stats (channel_id, snapshot)
                   VALUES ($1, $2::jsonb)
                   RETURNING *""",
                channel_id,
                _json_str(snapshot),
            )
        return dict(row) if row else {}

    async def get_latest_stats(self, channel_id: UUID) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM youtube_channel_stats
                   WHERE channel_id = $1 ORDER BY recorded_at DESC LIMIT 1""",
                channel_id,
            )
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # Intelligence reports
    # ------------------------------------------------------------------

    async def save_report(
        self,
        channel_id: UUID,
        report_type: str,
        report: dict[str, Any],
        model_used: str = "",
    ) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO youtube_intelligence_reports
                       (channel_id, report_type, report, model_used)
                   VALUES ($1, $2, $3::jsonb, $4)
                   RETURNING *""",
                channel_id,
                report_type,
                _json_str(report),
                model_used,
            )
        return dict(row) if row else {}

    async def get_latest_report(self, channel_id: UUID) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM youtube_intelligence_reports
                   WHERE channel_id = $1 ORDER BY generated_at DESC LIMIT 1""",
                channel_id,
            )
        return dict(row) if row else None

    async def get_report_history(
        self, channel_id: UUID, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM youtube_intelligence_reports
                   WHERE channel_id = $1 ORDER BY generated_at DESC LIMIT $2""",
                channel_id,
                limit,
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Reply drafts
    # ------------------------------------------------------------------

    async def save_reply_draft(self, draft: dict[str, Any]) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO youtube_reply_drafts
                       (channel_id, comment_id, video_id, original_comment,
                        draft_reply, confidence, category, status, auto_approved, model_used)
                   VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
                   RETURNING *""",
                draft["channel_id"],
                draft["comment_id"],
                draft.get("video_id", ""),
                draft["original_comment"],
                draft["draft_reply"],
                draft.get("confidence", 0.0),
                draft.get("category", "feedback"),
                draft.get("status", "pending"),
                draft.get("auto_approved", False),
                draft.get("model_used", ""),
            )
        return dict(row) if row else {}

    async def get_reply_drafts(
        self,
        channel_id: UUID,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if status:
            sql = """SELECT * FROM youtube_reply_drafts
                     WHERE channel_id = $1 AND status = $2
                     ORDER BY created_at DESC LIMIT $3"""
            args: list[Any] = [channel_id, status, limit]
        else:
            sql = """SELECT * FROM youtube_reply_drafts
                     WHERE channel_id = $1
                     ORDER BY created_at DESC LIMIT $2"""
            args = [channel_id, limit]
        async with self._db.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def update_reply_status(self, reply_id: UUID, status: str) -> dict[str, Any] | None:
        ts_field = ""
        if status == "approved" or status == "rejected":
            ts_field = ", reviewed_at = now()"
        elif status == "posted":
            ts_field = ", posted_at = now()"
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                f"""UPDATE youtube_reply_drafts
                    SET status = $2{ts_field}
                    WHERE id = $1
                    RETURNING *""",  # nosec B608
                reply_id,
                status,
            )
        return dict(row) if row else None

    async def count_replies_today(self, channel_id: UUID) -> int:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT count(*) as cnt FROM youtube_reply_drafts
                   WHERE channel_id = $1
                     AND status = 'posted'
                     AND posted_at >= CURRENT_DATE""",
                channel_id,
            )
        return int(row["cnt"]) if row else 0

    # ------------------------------------------------------------------
    # Tag recommendations
    # ------------------------------------------------------------------

    async def save_tag_recommendation(self, rec: dict[str, Any]) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO youtube_tag_recommendations
                       (channel_id, video_id, current_tags, suggested_tags, reason)
                   VALUES ($1, $2, $3::jsonb, $4::jsonb, $5)
                   RETURNING *""",
                rec["channel_id"],
                rec["video_id"],
                _json_str(rec.get("current_tags", [])),
                _json_str(rec.get("suggested_tags", [])),
                rec.get("reason", ""),
            )
        return dict(row) if row else {}

    async def get_tag_recommendations(
        self,
        channel_id: UUID,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        if status:
            sql = """SELECT * FROM youtube_tag_recommendations
                     WHERE channel_id = $1 AND status = $2
                     ORDER BY created_at DESC LIMIT $3"""
            args: list[Any] = [channel_id, status, limit]
        else:
            sql = """SELECT * FROM youtube_tag_recommendations
                     WHERE channel_id = $1
                     ORDER BY created_at DESC LIMIT $2"""
            args = [channel_id, limit]
        async with self._db.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Strategy documents
    # ------------------------------------------------------------------

    async def save_strategy(
        self,
        channel_id: UUID,
        strategy_type: str,
        strategy: dict[str, Any],
        model_used: str = "",
        valid_until: str | None = None,
    ) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO youtube_strategy_documents
                       (channel_id, strategy_type, strategy, model_used, valid_until)
                   VALUES ($1, $2, $3::jsonb, $4, $5::timestamptz)
                   RETURNING *""",
                channel_id,
                strategy_type,
                _json_str(strategy),
                model_used,
                valid_until,
            )
        return dict(row) if row else {}

    async def get_latest_strategy(self, channel_id: UUID) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """SELECT * FROM youtube_strategy_documents
                   WHERE channel_id = $1 ORDER BY generated_at DESC LIMIT 1""",
                channel_id,
            )
        return dict(row) if row else None

    async def get_strategy_history(
        self, channel_id: UUID, *, limit: int = 10
    ) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM youtube_strategy_documents
                   WHERE channel_id = $1 ORDER BY generated_at DESC LIMIT $2""",
                channel_id,
                limit,
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Assumptions
    # ------------------------------------------------------------------

    async def save_assumption(self, a: dict[str, Any]) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO youtube_assumptions
                       (channel_id, category, statement, evidence, confidence,
                        source, confirmed_at, next_validation)
                   VALUES ($1, $2, $3, $4::jsonb, $5, $6, $7::timestamptz,
                           $8::timestamptz)
                   RETURNING *""",
                a["channel_id"],
                a["category"],
                a["statement"],
                _json_str(a.get("evidence", [])),
                a.get("confidence", 0.5),
                a.get("source", "inferred"),
                a.get("confirmed_at"),
                a.get("next_validation"),
            )
        return dict(row) if row else {}

    async def get_assumptions(
        self, channel_id: UUID, *, source: str | None = None
    ) -> list[dict[str, Any]]:
        if source:
            sql = """SELECT * FROM youtube_assumptions
                     WHERE channel_id = $1 AND source = $2
                     ORDER BY confidence DESC"""
            args: list[Any] = [channel_id, source]
        else:
            sql = """SELECT * FROM youtube_assumptions
                     WHERE channel_id = $1 ORDER BY confidence DESC"""
            args = [channel_id]
        async with self._db.acquire() as conn:
            rows = await conn.fetch(sql, *args)
        return [dict(r) for r in rows]

    async def get_assumption(self, assumption_id: UUID) -> dict[str, Any] | None:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM youtube_assumptions WHERE id = $1",
                assumption_id,
            )
        return dict(row) if row else None

    async def update_assumption(self, assumption_id: UUID, **kwargs: Any) -> dict[str, Any] | None:
        sets: list[str] = []
        args: list[Any] = []
        i = 2
        for key, val in kwargs.items():
            if key in (
                "category",
                "statement",
                "confidence",
                "source",
                "confirmed_at",
                "last_validated",
                "next_validation",
            ):
                if key in ("evidence",):
                    sets.append(f"{key} = ${i}::jsonb")
                    args.append(_json_str(val))
                elif key in ("confirmed_at", "last_validated", "next_validation"):
                    sets.append(f"{key} = ${i}::timestamptz")
                    args.append(val)
                else:
                    sets.append(f"{key} = ${i}")
                    args.append(val)
                i += 1
            elif key == "evidence":
                sets.append(f"evidence = ${i}::jsonb")
                args.append(_json_str(val))
                i += 1
        if not sets:
            return await self.get_assumption(assumption_id)
        sql = f"UPDATE youtube_assumptions SET {', '.join(sets)} WHERE id = $1 RETURNING *"  # nosec B608
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(sql, assumption_id, *args)
        return dict(row) if row else None

    async def get_stale_assumptions(self) -> list[dict[str, Any]]:
        """Return all assumptions past their next_validation date."""
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT a.*, c.tenant_id
                   FROM youtube_assumptions a
                   JOIN youtube_channels c ON a.channel_id = c.id
                   WHERE a.source NOT IN ('invalidated')
                     AND a.next_validation <= now()
                   ORDER BY a.next_validation"""
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Documents
    # ------------------------------------------------------------------

    async def save_document(
        self,
        channel_id: UUID,
        title: str,
        content: str,
        doc_type: str = "",
    ) -> dict[str, Any]:
        async with self._db.acquire() as conn:
            row = await conn.fetchrow(
                """INSERT INTO youtube_channel_documents
                       (channel_id, title, content, doc_type)
                   VALUES ($1, $2, $3, $4)
                   RETURNING *""",
                channel_id,
                title,
                content,
                doc_type,
            )
        return dict(row) if row else {}

    async def get_documents(self, channel_id: UUID) -> list[dict[str, Any]]:
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT * FROM youtube_channel_documents
                   WHERE channel_id = $1 ORDER BY created_at DESC""",
                channel_id,
            )
        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Channels needing analysis (heartbeat)
    # ------------------------------------------------------------------

    async def get_channels_due_for_analysis(self) -> list[dict[str, Any]]:
        """Return channels where interval has elapsed since last analysis."""
        async with self._db.acquire() as conn:
            rows = await conn.fetch(
                """SELECT c.*
                   FROM youtube_channels c
                   WHERE (
                       c.last_analysis_at IS NULL
                       OR c.last_analysis_at
                           + (c.analysis_interval_h || ' hours')::interval <= now()
                   )
                   AND EXISTS (
                       SELECT 1 FROM youtube_comments cm
                       WHERE cm.channel_id = c.id
                         AND (c.last_analysis_at IS NULL OR cm.ingested_at > c.last_analysis_at)
                   )
                   ORDER BY c.last_analysis_at NULLS FIRST"""
            )
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _json_str(obj: Any) -> str:
    """Serialize *obj* to a JSON string for asyncpg JSONB parameters."""
    import json

    return json.dumps(obj, default=str)
