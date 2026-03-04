"""PostgreSQL-backed tenant manager for multi-tenant API.

Provides CRUD operations for tenants and chat sessions, with API key
management. Follows the same pool/schema pattern as UserManager.
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

import asyncpg  # type: ignore[import-untyped,import-not-found]

from zetherion_ai.api.auth import generate_api_key, verify_api_key
from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.api.tenant")

# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS tenants (
    id              SERIAL       PRIMARY KEY,
    tenant_id       UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    name            TEXT         NOT NULL,
    domain          TEXT,
    api_key_hash    TEXT         NOT NULL,
    api_key_prefix  VARCHAR(12)  NOT NULL,
    is_active       BOOLEAN      NOT NULL DEFAULT TRUE,
    rate_limit_rpm  INT          NOT NULL DEFAULT 60,
    allowed_skills  TEXT[]       DEFAULT '{}',
    config          JSONB        DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenants_api_key_prefix
    ON tenants (api_key_prefix);
CREATE INDEX IF NOT EXISTS idx_tenants_tenant_id
    ON tenants (tenant_id);

CREATE TABLE IF NOT EXISTS chat_sessions (
    id              SERIAL       PRIMARY KEY,
    session_id      UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id       UUID         NOT NULL REFERENCES tenants(tenant_id),
    external_user_id TEXT,
    metadata        JSONB        DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_active     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at      TIMESTAMPTZ  NOT NULL DEFAULT (now() + INTERVAL '24 hours')
);

CREATE INDEX IF NOT EXISTS idx_chat_sessions_tenant_id
    ON chat_sessions (tenant_id);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_session_id
    ON chat_sessions (session_id);
CREATE INDEX IF NOT EXISTS idx_chat_sessions_expires
    ON chat_sessions (expires_at);

CREATE TABLE IF NOT EXISTS chat_messages (
    id              SERIAL       PRIMARY KEY,
    message_id      UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    session_id      UUID         NOT NULL REFERENCES chat_sessions(session_id) ON DELETE CASCADE,
    tenant_id       UUID         NOT NULL,
    role            VARCHAR(20)  NOT NULL,  -- 'user' or 'assistant'
    content         TEXT         NOT NULL,
    metadata        JSONB        DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_chat_messages_session
    ON chat_messages (session_id, created_at);
CREATE INDEX IF NOT EXISTS idx_chat_messages_tenant
    ON chat_messages (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tenant_audit_log (
    id              SERIAL       PRIMARY KEY,
    tenant_id       UUID,
    action          VARCHAR(50)  NOT NULL,
    details         JSONB        DEFAULT '{}'::jsonb,
    ip_address      TEXT,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_audit_created
    ON tenant_audit_log (tenant_id, created_at DESC);

-- Tenant CRM tables (populated by tenant_intelligence skill)
CREATE TABLE IF NOT EXISTS tenant_contacts (
    id              SERIAL       PRIMARY KEY,
    contact_id      UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id       UUID         NOT NULL REFERENCES tenants(tenant_id),
    name            TEXT,
    email           TEXT,
    phone           TEXT,
    source          VARCHAR(50)  DEFAULT 'chat',
    tags            TEXT[]       DEFAULT '{}',
    custom_fields   JSONB        DEFAULT '{}'::jsonb,
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_contacts_tenant
    ON tenant_contacts (tenant_id);
CREATE INDEX IF NOT EXISTS idx_tenant_contacts_email
    ON tenant_contacts (tenant_id, email) WHERE email IS NOT NULL;

CREATE TABLE IF NOT EXISTS tenant_interactions (
    id              SERIAL       PRIMARY KEY,
    interaction_id  UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id       UUID         NOT NULL REFERENCES tenants(tenant_id),
    contact_id      UUID         REFERENCES tenant_contacts(contact_id),
    session_id      UUID,
    interaction_type VARCHAR(30) DEFAULT 'chat',
    summary         TEXT,
    entities        JSONB        DEFAULT '{}'::jsonb,
    sentiment       VARCHAR(20),
    intent          VARCHAR(50),
    outcome         VARCHAR(30),
    created_at      TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_interactions_tenant
    ON tenant_interactions (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_interactions_contact
    ON tenant_interactions (contact_id) WHERE contact_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tenant_interactions_session
    ON tenant_interactions (session_id) WHERE session_id IS NOT NULL;

-- App watcher: web session + behavior telemetry
CREATE TABLE IF NOT EXISTS tenant_web_sessions (
    id               SERIAL       PRIMARY KEY,
    web_session_id   UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    session_id       UUID         REFERENCES chat_sessions(session_id) ON DELETE SET NULL,
    external_user_id TEXT,
    consent_replay   BOOLEAN      NOT NULL DEFAULT FALSE,
    replay_sampled   BOOLEAN      NOT NULL DEFAULT FALSE,
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    ended_at         TIMESTAMPTZ,
    metadata         JSONB        DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_web_sessions_tenant
    ON tenant_web_sessions (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_web_sessions_chat
    ON tenant_web_sessions (session_id, created_at DESC) WHERE session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS tenant_web_events (
    id               SERIAL       PRIMARY KEY,
    event_id         UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    web_session_id   UUID         REFERENCES tenant_web_sessions(web_session_id) ON DELETE CASCADE,
    session_id       UUID         REFERENCES chat_sessions(session_id) ON DELETE SET NULL,
    event_type       VARCHAR(64)  NOT NULL,
    event_name       TEXT         DEFAULT '',
    page_url         TEXT,
    element_selector TEXT,
    properties       JSONB        DEFAULT '{}'::jsonb,
    occurred_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_web_events_tenant
    ON tenant_web_events (tenant_id, occurred_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_web_events_session
    ON tenant_web_events (web_session_id, occurred_at DESC) WHERE web_session_id IS NOT NULL;

CREATE TABLE IF NOT EXISTS tenant_replay_chunks (
    id               SERIAL       PRIMARY KEY,
    chunk_id         UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    web_session_id   UUID         NOT NULL
                                REFERENCES tenant_web_sessions(web_session_id) ON DELETE CASCADE,
    sequence_no      INT          NOT NULL,
    object_key       TEXT         NOT NULL,
    checksum_sha256  VARCHAR(64),
    chunk_size_bytes INT          NOT NULL DEFAULT 0,
    metadata         JSONB        DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, web_session_id, sequence_no)
);

CREATE INDEX IF NOT EXISTS idx_tenant_replay_chunks_session
    ON tenant_replay_chunks (web_session_id, sequence_no ASC);

CREATE TABLE IF NOT EXISTS tenant_documents (
    id               SERIAL       PRIMARY KEY,
    document_id      UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    file_name        TEXT         NOT NULL,
    mime_type        TEXT         NOT NULL DEFAULT 'application/octet-stream',
    object_key       TEXT         NOT NULL,
    status           VARCHAR(20)  NOT NULL DEFAULT 'uploaded',
    size_bytes       BIGINT       NOT NULL DEFAULT 0,
    checksum_sha256  VARCHAR(64),
    metadata         JSONB        DEFAULT '{}'::jsonb,
    extracted_text   TEXT,
    preview_html     TEXT,
    chunk_count      INT          NOT NULL DEFAULT 0,
    indexed_at       TIMESTAMPTZ,
    archived_at      TIMESTAMPTZ,
    purge_after      TIMESTAMPTZ,
    purged_at        TIMESTAMPTZ,
    archived_reason  TEXT,
    error_message    TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

ALTER TABLE tenant_documents
    ADD COLUMN IF NOT EXISTS archived_at TIMESTAMPTZ;
ALTER TABLE tenant_documents
    ADD COLUMN IF NOT EXISTS purge_after TIMESTAMPTZ;
ALTER TABLE tenant_documents
    ADD COLUMN IF NOT EXISTS purged_at TIMESTAMPTZ;
ALTER TABLE tenant_documents
    ADD COLUMN IF NOT EXISTS archived_reason TEXT;

CREATE INDEX IF NOT EXISTS idx_tenant_documents_tenant
    ON tenant_documents (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_documents_status
    ON tenant_documents (tenant_id, status, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_documents_purge_after
    ON tenant_documents (tenant_id, purge_after)
    WHERE status = 'archived';

CREATE TABLE IF NOT EXISTS tenant_document_uploads (
    id               SERIAL       PRIMARY KEY,
    upload_id        UUID         NOT NULL UNIQUE,
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    file_name        TEXT         NOT NULL,
    mime_type        TEXT         NOT NULL DEFAULT 'application/octet-stream',
    size_bytes       BIGINT       NOT NULL DEFAULT 0,
    metadata         JSONB        DEFAULT '{}'::jsonb,
    status           VARCHAR(20)  NOT NULL DEFAULT 'pending',
    document_id      UUID,
    expires_at       TIMESTAMPTZ  NOT NULL,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_document_uploads_tenant
    ON tenant_document_uploads (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_document_uploads_status
    ON tenant_document_uploads (tenant_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS document_ingestion_jobs (
    id               SERIAL       PRIMARY KEY,
    job_id           UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    document_id      UUID         NOT NULL
                                 REFERENCES tenant_documents(document_id) ON DELETE CASCADE,
    status           VARCHAR(20)  NOT NULL DEFAULT 'processing',
    error_message    TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    completed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_document_ingestion_jobs_tenant
    ON document_ingestion_jobs (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_ingestion_jobs_document
    ON document_ingestion_jobs (document_id, created_at DESC);

CREATE TABLE IF NOT EXISTS document_archive_jobs (
    id               SERIAL       PRIMARY KEY,
    job_id           UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    document_id      UUID         NOT NULL
                                 REFERENCES tenant_documents(document_id) ON DELETE CASCADE,
    status           VARCHAR(20)  NOT NULL DEFAULT 'queued',
    retry_count      INT          NOT NULL DEFAULT 0,
    next_attempt_at  TIMESTAMPTZ,
    error_message    TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    started_at       TIMESTAMPTZ,
    completed_at     TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_document_archive_jobs_ready
    ON document_archive_jobs (status, next_attempt_at, created_at ASC);
CREATE INDEX IF NOT EXISTS idx_document_archive_jobs_tenant
    ON document_archive_jobs (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_document_archive_jobs_document
    ON document_archive_jobs (document_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tenant_release_markers (
    id               SERIAL       PRIMARY KEY,
    marker_id        UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    source           VARCHAR(50)  NOT NULL DEFAULT 'api',
    environment      VARCHAR(30)  NOT NULL DEFAULT 'production',
    commit_sha       TEXT,
    branch           TEXT,
    tag_name         TEXT,
    deployed_at      TIMESTAMPTZ  NOT NULL DEFAULT now(),
    metadata         JSONB        DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_release_markers_tenant
    ON tenant_release_markers (tenant_id, deployed_at DESC);

CREATE TABLE IF NOT EXISTS tenant_release_nonces (
    id               SERIAL       PRIMARY KEY,
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    nonce            TEXT         NOT NULL,
    signature        TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, nonce)
);

CREATE INDEX IF NOT EXISTS idx_tenant_release_nonces_created
    ON tenant_release_nonces (tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tenant_funnel_daily (
    id               SERIAL       PRIMARY KEY,
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    metric_date      DATE         NOT NULL,
    funnel_name      TEXT         NOT NULL DEFAULT 'primary',
    stage_name       TEXT         NOT NULL,
    stage_order      INT          NOT NULL,
    users_count      INT          NOT NULL DEFAULT 0,
    drop_off_rate    FLOAT,
    conversion_rate  FLOAT,
    metadata         JSONB        DEFAULT '{}'::jsonb,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, metric_date, funnel_name, stage_name)
);

CREATE INDEX IF NOT EXISTS idx_tenant_funnel_daily_tenant
    ON tenant_funnel_daily (tenant_id, metric_date DESC);

CREATE TABLE IF NOT EXISTS tenant_recommendations (
    id               SERIAL       PRIMARY KEY,
    recommendation_id UUID        NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    recommendation_type VARCHAR(64) NOT NULL,
    title            TEXT         NOT NULL,
    description      TEXT         NOT NULL,
    evidence         JSONB        DEFAULT '{}'::jsonb,
    risk_class       VARCHAR(20)  NOT NULL DEFAULT 'low',
    confidence       FLOAT        NOT NULL DEFAULT 0,
    expected_impact  FLOAT,
    status           VARCHAR(30)  NOT NULL DEFAULT 'open',
    source           VARCHAR(30)  NOT NULL DEFAULT 'detector',
    generated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_recommendations_tenant
    ON tenant_recommendations (tenant_id, generated_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_recommendations_status
    ON tenant_recommendations (tenant_id, status, generated_at DESC);

CREATE TABLE IF NOT EXISTS tenant_recommendation_feedback (
    id               SERIAL       PRIMARY KEY,
    feedback_id      UUID         NOT NULL UNIQUE DEFAULT gen_random_uuid(),
    tenant_id        UUID         NOT NULL REFERENCES tenants(tenant_id),
    recommendation_id UUID        NOT NULL REFERENCES tenant_recommendations(recommendation_id)
                                       ON DELETE CASCADE,
    feedback_type    VARCHAR(30)  NOT NULL,
    note             TEXT,
    actor            TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_recommendation_feedback_tenant
    ON tenant_recommendation_feedback (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_recommendation_feedback_recommendation
    ON tenant_recommendation_feedback (recommendation_id, created_at DESC);
"""


class TenantManager:
    """Manage API tenants with PostgreSQL-backed storage.

    Handles tenant CRUD, API key lifecycle, and chat session management.
    """

    def __init__(self, dsn: str) -> None:
        self._dsn = dsn
        self._pool: asyncpg.Pool | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create connection pool and ensure schema exists."""
        try:
            settings = get_settings()
            pool_min = max(1, int(settings.postgres_pool_min_size))
            pool_max = max(pool_min, int(settings.postgres_pool_max_size))
            self._pool = await asyncpg.create_pool(
                dsn=self._dsn,
                min_size=pool_min,
                max_size=pool_max,
            )
            log.info(
                "tenant_pool_created",
                dsn=self._dsn.split("@")[-1],
                min_size=pool_min,
                max_size=pool_max,
            )
        except (asyncpg.PostgresError, OSError) as exc:
            log.error("tenant_pool_creation_failed", error=str(exc))
            raise

        await self._ensure_schema()

    async def close(self) -> None:
        """Close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            log.info("tenant_pool_closed")

    # ------------------------------------------------------------------
    # Tenant CRUD
    # ------------------------------------------------------------------

    async def create_tenant(
        self,
        name: str,
        domain: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> tuple[dict[str, Any], str]:
        """Create a new tenant and generate an API key.

        Returns:
            Tuple of (tenant_record_dict, plaintext_api_key).
            The API key is shown once and never stored.
        """
        import json

        full_key, key_prefix, key_hash = generate_api_key()

        row = await self._fetchrow(
            """
            INSERT INTO tenants (name, domain, api_key_hash, api_key_prefix, config)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            RETURNING tenant_id, name, domain, is_active, rate_limit_rpm,
                      config, created_at, updated_at
            """,
            name,
            domain,
            key_hash,
            key_prefix,
            json.dumps(config or {}),
        )

        await self._audit("tenant_created", tenant_id=str(row["tenant_id"]))
        log.info("tenant_created", tenant_id=str(row["tenant_id"]), name=name)

        return dict(row), full_key

    async def get_tenant(self, tenant_id: str) -> dict[str, Any] | None:
        """Get a tenant by UUID."""
        row = await self._fetchrow(
            """
            SELECT tenant_id, name, domain, is_active, rate_limit_rpm,
                   config, created_at, updated_at
            FROM tenants WHERE tenant_id = $1::uuid
            """,
            tenant_id,
        )
        return dict(row) if row else None

    async def list_tenants(self, *, active_only: bool = True) -> list[dict[str, Any]]:
        """List all tenants."""
        if active_only:
            rows = await self._fetch(
                """
                SELECT tenant_id, name, domain, is_active, rate_limit_rpm,
                       config, created_at, updated_at
                FROM tenants WHERE is_active = TRUE ORDER BY created_at
                """
            )
        else:
            rows = await self._fetch(
                """
                SELECT tenant_id, name, domain, is_active, rate_limit_rpm,
                       config, created_at, updated_at
                FROM tenants ORDER BY created_at
                """
            )
        return [dict(r) for r in rows]

    async def update_tenant(
        self,
        tenant_id: str,
        *,
        name: str | None = None,
        domain: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Update a tenant's name, domain, or config.

        Only provided fields are updated.  Returns the updated record or
        ``None`` if the tenant was not found.
        """
        import json as _json

        sets: list[str] = []
        args: list[Any] = []
        idx = 1

        if name is not None:
            sets.append(f"name = ${idx}")
            args.append(name)
            idx += 1
        if domain is not None:
            sets.append(f"domain = ${idx}")
            args.append(domain)
            idx += 1
        if config is not None:
            sets.append(f"config = ${idx}::jsonb")
            args.append(_json.dumps(config))
            idx += 1

        if not sets:
            return await self.get_tenant(tenant_id)

        sets.append("updated_at = now()")
        args.append(tenant_id)

        row = await self._fetchrow(
            f"""
            UPDATE tenants SET {", ".join(sets)}
            WHERE tenant_id = ${idx}::uuid
            RETURNING tenant_id, name, domain, is_active, rate_limit_rpm,
                      config, created_at, updated_at
            """,  # nosec B608
            *args,
        )

        if row:
            await self._audit("tenant_updated", tenant_id=tenant_id)
            log.info("tenant_updated", tenant_id=tenant_id)
            return dict(row)
        return None

    async def deactivate_tenant(self, tenant_id: str) -> bool:
        """Deactivate a tenant (soft delete)."""
        result = await self._execute(
            "UPDATE tenants SET is_active = FALSE, updated_at = now() WHERE tenant_id = $1::uuid",
            tenant_id,
        )
        if result == "UPDATE 1":
            await self._audit("tenant_deactivated", tenant_id=tenant_id)
            log.info("tenant_deactivated", tenant_id=tenant_id)
            return True
        return False

    async def rotate_api_key(self, tenant_id: str) -> str | None:
        """Generate a new API key for a tenant, invalidating the old one.

        Returns:
            The new plaintext API key, or None if tenant not found.
        """
        full_key, key_prefix, key_hash = generate_api_key()

        result = await self._execute(
            """
            UPDATE tenants
            SET api_key_hash = $1, api_key_prefix = $2, updated_at = now()
            WHERE tenant_id = $3::uuid AND is_active = TRUE
            """,
            key_hash,
            key_prefix,
            tenant_id,
        )
        if result == "UPDATE 1":
            await self._audit("api_key_rotated", tenant_id=tenant_id)
            log.info("api_key_rotated", tenant_id=tenant_id)
            return full_key
        return None

    # ------------------------------------------------------------------
    # API key lookup & validation
    # ------------------------------------------------------------------

    async def authenticate_api_key(self, provided_key: str) -> dict[str, Any] | None:
        """Look up and validate an API key.

        Returns:
            Tenant record dict if valid, None otherwise.
        """
        key_prefix = provided_key[:12]
        rows = await self._fetch(
            """
            SELECT tenant_id, name, domain, is_active, rate_limit_rpm,
                   config, api_key_hash, created_at, updated_at
            FROM tenants
            WHERE api_key_prefix = $1 AND is_active = TRUE
            """,
            key_prefix,
        )

        for row in rows:
            if verify_api_key(provided_key, row["api_key_hash"]):
                result = dict(row)
                del result["api_key_hash"]
                return result

        return None

    # ------------------------------------------------------------------
    # Session management
    # ------------------------------------------------------------------

    async def create_session(
        self,
        tenant_id: str,
        external_user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create a new chat session for a tenant.

        Returns:
            Session record dict.
        """
        import json

        row = await self._fetchrow(
            """
            INSERT INTO chat_sessions (tenant_id, external_user_id, metadata)
            VALUES ($1::uuid, $2, $3::jsonb)
            RETURNING session_id, tenant_id, external_user_id,
                      created_at, last_active, expires_at
            """,
            tenant_id,
            external_user_id,
            json.dumps(metadata or {}),
        )

        log.info(
            "session_created",
            session_id=str(row["session_id"]),
            tenant_id=tenant_id,
        )
        return dict(row)

    async def get_session(self, session_id: str) -> dict[str, Any] | None:
        """Get a session by UUID."""
        row = await self._fetchrow(
            """
            SELECT session_id, tenant_id, external_user_id,
                   created_at, last_active, expires_at
            FROM chat_sessions
            WHERE session_id = $1::uuid AND expires_at > now()
            """,
            session_id,
        )
        return dict(row) if row else None

    async def touch_session(self, session_id: str) -> None:
        """Update the last_active timestamp for a session."""
        await self._execute(
            "UPDATE chat_sessions SET last_active = now() WHERE session_id = $1::uuid",
            session_id,
        )

    async def delete_session(self, session_id: str, tenant_id: str) -> bool:
        """Delete a session (must belong to the given tenant)."""
        result = await self._execute(
            "DELETE FROM chat_sessions WHERE session_id = $1::uuid AND tenant_id = $2::uuid",
            session_id,
            tenant_id,
        )
        return result == "DELETE 1"

    # ------------------------------------------------------------------
    # Chat message management
    # ------------------------------------------------------------------

    async def add_message(
        self,
        session_id: str,
        tenant_id: str,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Store a chat message.

        Args:
            session_id: Session UUID.
            tenant_id: Tenant UUID.
            role: 'user' or 'assistant'.
            content: Message text.
            metadata: Optional JSON metadata.

        Returns:
            Message record dict.
        """
        import json

        row = await self._fetchrow(
            """
            INSERT INTO chat_messages (session_id, tenant_id, role, content, metadata)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5::jsonb)
            RETURNING message_id, session_id, tenant_id, role, content, created_at
            """,
            session_id,
            tenant_id,
            role,
            content,
            json.dumps(metadata or {}),
        )
        return dict(row)

    async def get_messages(
        self,
        session_id: str,
        tenant_id: str,
        *,
        limit: int = 50,
        before_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Retrieve chat messages for a session.

        Args:
            session_id: Session UUID.
            tenant_id: Tenant UUID (ensures tenant isolation).
            limit: Max messages to return (newest first).
            before_id: Cursor for pagination — return messages before this message_id.

        Returns:
            List of message dicts, ordered oldest-first.
        """
        if before_id:
            rows = await self._fetch(
                """
                SELECT message_id, session_id, role, content, created_at
                FROM chat_messages
                WHERE session_id = $1::uuid AND tenant_id = $2::uuid
                  AND created_at < (
                      SELECT created_at FROM chat_messages WHERE message_id = $3::uuid
                  )
                ORDER BY created_at DESC
                LIMIT $4
                """,
                session_id,
                tenant_id,
                before_id,
                limit,
            )
        else:
            rows = await self._fetch(
                """
                SELECT message_id, session_id, role, content, created_at
                FROM chat_messages
                WHERE session_id = $1::uuid AND tenant_id = $2::uuid
                ORDER BY created_at DESC
                LIMIT $3
                """,
                session_id,
                tenant_id,
                limit,
            )
        # Reverse so oldest is first (chronological order)
        return [dict(r) for r in reversed(rows)]

    # ------------------------------------------------------------------
    # CRM — Contacts & Interactions (populated by tenant_intelligence)
    # ------------------------------------------------------------------

    async def upsert_contact(
        self,
        tenant_id: str,
        *,
        name: str | None = None,
        email: str | None = None,
        phone: str | None = None,
        source: str = "chat",
        tags: list[str] | None = None,
        custom_fields: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Create or update a contact for a tenant.

        If a contact with the same email already exists for the tenant,
        update the existing record. Otherwise, create a new one.
        """
        import json as _json

        if email:
            existing = await self._fetchrow(
                """
                SELECT contact_id, name, phone, tags, custom_fields
                FROM tenant_contacts
                WHERE tenant_id = $1::uuid AND email = $2
                """,
                tenant_id,
                email,
            )
            if existing:
                # Merge tags
                merged_tags = list(set(list(existing.get("tags") or []) + (tags or [])))
                merged_fields = dict(existing.get("custom_fields") or {})
                merged_fields.update(custom_fields or {})
                row = await self._fetchrow(
                    """
                    UPDATE tenant_contacts
                    SET name = COALESCE($2, name),
                        phone = COALESCE($3, phone),
                        tags = $4,
                        custom_fields = $5::jsonb,
                        updated_at = now()
                    WHERE tenant_id = $1::uuid AND email = $6
                    RETURNING contact_id, tenant_id, name, email, phone,
                              source, tags, custom_fields, created_at, updated_at
                    """,
                    tenant_id,
                    name or existing.get("name"),
                    phone or existing.get("phone"),
                    merged_tags,
                    _json.dumps(merged_fields),
                    email,
                )
                return dict(row)

        row = await self._fetchrow(
            """
            INSERT INTO tenant_contacts (tenant_id, name, email, phone, source, tags, custom_fields)
            VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb)
            RETURNING contact_id, tenant_id, name, email, phone,
                      source, tags, custom_fields, created_at, updated_at
            """,
            tenant_id,
            name,
            email,
            phone,
            source,
            tags or [],
            _json.dumps(custom_fields or {}),
        )
        return dict(row)

    async def add_interaction(
        self,
        tenant_id: str,
        *,
        contact_id: str | None = None,
        session_id: str | None = None,
        interaction_type: str = "chat",
        summary: str | None = None,
        entities: dict[str, Any] | None = None,
        sentiment: str | None = None,
        intent: str | None = None,
        outcome: str | None = None,
    ) -> dict[str, Any]:
        """Record an interaction (L1b/L2 extraction result)."""
        import json as _json

        row = await self._fetchrow(
            """
            INSERT INTO tenant_interactions
                (tenant_id, contact_id, session_id, interaction_type,
                 summary, entities, sentiment, intent, outcome)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6::jsonb, $7, $8, $9)
            RETURNING interaction_id, tenant_id, contact_id, session_id,
                      interaction_type, summary, entities, sentiment, intent,
                      outcome, created_at
            """,
            tenant_id,
            contact_id,
            session_id,
            interaction_type,
            summary,
            _json.dumps(entities or {}),
            sentiment,
            intent,
            outcome,
        )
        return dict(row)

    async def list_contacts(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        email: str | None = None,
    ) -> list[dict[str, Any]]:
        """List tenant contacts, optionally filtered by exact email."""
        if email:
            rows = await self._fetch(
                """
                SELECT contact_id, tenant_id, name, email, phone, source,
                       tags, custom_fields, created_at, updated_at
                FROM tenant_contacts
                WHERE tenant_id = $1::uuid AND email = $2
                ORDER BY updated_at DESC
                LIMIT $3
                """,
                tenant_id,
                email,
                limit,
            )
        else:
            rows = await self._fetch(
                """
                SELECT contact_id, tenant_id, name, email, phone, source,
                       tags, custom_fields, created_at, updated_at
                FROM tenant_contacts
                WHERE tenant_id = $1::uuid
                ORDER BY updated_at DESC
                LIMIT $2
                """,
                tenant_id,
                limit,
            )
        return [dict(r) for r in rows]

    async def get_interactions(
        self,
        tenant_id: str,
        *,
        contact_id: str | None = None,
        session_id: str | None = None,
        interaction_type: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get recent interactions for a tenant with optional filters."""
        conditions = ["tenant_id = $1::uuid"]
        args: list[Any] = [tenant_id]
        idx = 2

        if contact_id:
            conditions.append(f"contact_id = ${idx}::uuid")
            args.append(contact_id)
            idx += 1
        if session_id:
            conditions.append(f"session_id = ${idx}::uuid")
            args.append(session_id)
            idx += 1
        if interaction_type:
            conditions.append(f"interaction_type = ${idx}")
            args.append(interaction_type)
            idx += 1

        args.append(limit)
        query = f"""
            SELECT interaction_id, tenant_id, contact_id, session_id,
                   interaction_type, summary, entities, sentiment, intent,
                   outcome, created_at
            FROM tenant_interactions
            WHERE {" AND ".join(conditions)}
            ORDER BY created_at DESC
            LIMIT ${idx}
        """  # nosec B608 - query uses controlled conditional fragments only

        rows = await self._fetch(query, *args)
        return [dict(r) for r in rows]

    async def update_contact_custom_fields(
        self,
        tenant_id: str,
        contact_id: str,
        custom_fields_patch: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Merge a patch into a contact's custom_fields JSON."""
        import json as _json

        row = await self._fetchrow(
            """
            UPDATE tenant_contacts
            SET custom_fields = COALESCE(custom_fields, '{}'::jsonb) || $3::jsonb,
                updated_at = now()
            WHERE tenant_id = $1::uuid AND contact_id = $2::uuid
            RETURNING contact_id, tenant_id, name, email, phone, source,
                      tags, custom_fields, created_at, updated_at
            """,
            tenant_id,
            contact_id,
            _json.dumps(custom_fields_patch),
        )
        return dict(row) if row else None

    # ------------------------------------------------------------------
    # App Watcher analytics storage
    # ------------------------------------------------------------------

    async def ensure_web_session(
        self,
        tenant_id: str,
        *,
        session_id: str | None = None,
        external_user_id: str | None = None,
        consent_replay: bool = False,
        replay_sampled: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Get or create an active tenant_web_session for a chat session."""
        import json as _json

        existing = None
        if session_id:
            existing = await self._fetchrow(
                """
                SELECT web_session_id, tenant_id, session_id, external_user_id,
                       consent_replay, replay_sampled, started_at, ended_at, metadata
                FROM tenant_web_sessions
                WHERE tenant_id = $1::uuid
                  AND session_id = $2::uuid
                  AND ended_at IS NULL
                ORDER BY created_at DESC
                LIMIT 1
                """,
                tenant_id,
                session_id,
            )
        if existing:
            return dict(existing)

        row = await self._fetchrow(
            """
            INSERT INTO tenant_web_sessions
                (tenant_id, session_id, external_user_id, consent_replay, replay_sampled, metadata)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6::jsonb)
            RETURNING web_session_id, tenant_id, session_id, external_user_id,
                      consent_replay, replay_sampled, started_at, ended_at, metadata
            """,
            tenant_id,
            session_id,
            external_user_id,
            consent_replay,
            replay_sampled,
            _json.dumps(metadata or {}),
        )
        return dict(row)

    async def get_web_session(
        self,
        tenant_id: str,
        web_session_id: str,
    ) -> dict[str, Any] | None:
        """Fetch one web session by ID and tenant."""
        row = await self._fetchrow(
            """
            SELECT web_session_id, tenant_id, session_id, external_user_id,
                   consent_replay, replay_sampled, started_at, ended_at, metadata
            FROM tenant_web_sessions
            WHERE tenant_id = $1::uuid AND web_session_id = $2::uuid
            """,
            tenant_id,
            web_session_id,
        )
        return dict(row) if row else None

    async def end_web_session(
        self,
        tenant_id: str,
        web_session_id: str,
        *,
        ended_at: datetime | None = None,
        metadata_patch: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Mark a web session ended and optionally merge metadata."""
        import json as _json

        row = await self._fetchrow(
            """
            UPDATE tenant_web_sessions
            SET ended_at = COALESCE($3::timestamptz, now()),
                metadata = COALESCE(metadata, '{}'::jsonb) || $4::jsonb,
                updated_at = now()
            WHERE tenant_id = $1::uuid AND web_session_id = $2::uuid
            RETURNING web_session_id, tenant_id, session_id, external_user_id,
                      consent_replay, replay_sampled, started_at, ended_at, metadata
            """,
            tenant_id,
            web_session_id,
            ended_at,
            _json.dumps(metadata_patch or {}),
        )
        return dict(row) if row else None

    async def add_web_event(
        self,
        tenant_id: str,
        *,
        web_session_id: str | None,
        session_id: str | None,
        event_type: str,
        event_name: str = "",
        page_url: str | None = None,
        element_selector: str | None = None,
        properties: dict[str, Any] | None = None,
        occurred_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Persist a single web behavior event."""
        import json as _json

        row = await self._fetchrow(
            """
            INSERT INTO tenant_web_events
                (tenant_id, web_session_id, session_id, event_type, event_name, page_url,
                 element_selector, properties, occurred_at)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7, $8::jsonb, COALESCE($9, now()))
            RETURNING event_id, tenant_id, web_session_id, session_id, event_type,
                      event_name, page_url, element_selector, properties, occurred_at
            """,
            tenant_id,
            web_session_id,
            session_id,
            event_type,
            event_name,
            page_url,
            element_selector,
            _json.dumps(properties or {}),
            occurred_at,
        )
        return dict(row)

    async def get_web_events(
        self,
        tenant_id: str,
        *,
        web_session_id: str | None = None,
        session_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Fetch recent web events, optionally scoped by session."""
        if web_session_id:
            rows = await self._fetch(
                """
                SELECT event_id, tenant_id, web_session_id, session_id, event_type,
                       event_name, page_url, element_selector, properties, occurred_at
                FROM tenant_web_events
                WHERE tenant_id = $1::uuid AND web_session_id = $2::uuid
                ORDER BY occurred_at ASC
                LIMIT $3
                """,
                tenant_id,
                web_session_id,
                limit,
            )
        elif session_id:
            rows = await self._fetch(
                """
                SELECT event_id, tenant_id, web_session_id, session_id, event_type,
                       event_name, page_url, element_selector, properties, occurred_at
                FROM tenant_web_events
                WHERE tenant_id = $1::uuid AND session_id = $2::uuid
                ORDER BY occurred_at ASC
                LIMIT $3
                """,
                tenant_id,
                session_id,
                limit,
            )
        else:
            rows = await self._fetch(
                """
                SELECT event_id, tenant_id, web_session_id, session_id, event_type,
                       event_name, page_url, element_selector, properties, occurred_at
                FROM tenant_web_events
                WHERE tenant_id = $1::uuid
                ORDER BY occurred_at DESC
                LIMIT $2
                """,
                tenant_id,
                limit,
            )
            rows = list(reversed(rows))
        return [dict(r) for r in rows]

    async def add_replay_chunk(
        self,
        tenant_id: str,
        *,
        web_session_id: str,
        sequence_no: int,
        object_key: str,
        checksum_sha256: str | None = None,
        chunk_size_bytes: int = 0,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Persist replay chunk metadata."""
        import json as _json

        row = await self._fetchrow(
            """
            INSERT INTO tenant_replay_chunks
                (tenant_id, web_session_id, sequence_no, object_key, checksum_sha256,
                 chunk_size_bytes, metadata)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7::jsonb)
            ON CONFLICT (tenant_id, web_session_id, sequence_no) DO UPDATE
            SET object_key = EXCLUDED.object_key,
                checksum_sha256 = EXCLUDED.checksum_sha256,
                chunk_size_bytes = EXCLUDED.chunk_size_bytes,
                metadata = EXCLUDED.metadata
            RETURNING chunk_id, tenant_id, web_session_id, sequence_no, object_key,
                      checksum_sha256, chunk_size_bytes, metadata, created_at
            """,
            tenant_id,
            web_session_id,
            sequence_no,
            object_key,
            checksum_sha256,
            chunk_size_bytes,
            _json.dumps(metadata or {}),
        )
        return dict(row)

    async def get_latest_replay_chunk(
        self,
        tenant_id: str,
        *,
        web_session_id: str,
    ) -> dict[str, Any] | None:
        """Fetch latest replay chunk metadata for a web session."""
        row = await self._fetchrow(
            """
            SELECT chunk_id, tenant_id, web_session_id, sequence_no, object_key,
                   checksum_sha256, chunk_size_bytes, metadata, created_at
            FROM tenant_replay_chunks
            WHERE tenant_id = $1::uuid AND web_session_id = $2::uuid
            ORDER BY sequence_no DESC
            LIMIT 1
            """,
            tenant_id,
            web_session_id,
        )
        return dict(row) if row else None

    async def get_replay_chunk(
        self,
        tenant_id: str,
        *,
        web_session_id: str,
        sequence_no: int,
    ) -> dict[str, Any] | None:
        """Fetch one replay chunk metadata row by sequence number."""
        row = await self._fetchrow(
            """
            SELECT chunk_id, tenant_id, web_session_id, sequence_no, object_key,
                   checksum_sha256, chunk_size_bytes, metadata, created_at
            FROM tenant_replay_chunks
            WHERE tenant_id = $1::uuid
              AND web_session_id = $2::uuid
              AND sequence_no = $3
            LIMIT 1
            """,
            tenant_id,
            web_session_id,
            sequence_no,
        )
        return dict(row) if row else None

    async def create_document_upload(
        self,
        tenant_id: str,
        *,
        upload_id: str,
        file_name: str,
        mime_type: str,
        size_bytes: int,
        metadata: dict[str, Any] | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a pending document upload token."""
        import json as _json

        row = await self._fetchrow(
            """
            INSERT INTO tenant_document_uploads (
                upload_id, tenant_id, file_name, mime_type, size_bytes, metadata, expires_at
            )
            VALUES (
                $1::uuid, $2::uuid, $3, $4, $5, $6::jsonb,
                COALESCE($7, now() + INTERVAL '1 hour')
            )
            RETURNING upload_id, tenant_id, file_name, mime_type, size_bytes, metadata,
                      status, document_id, expires_at, created_at, updated_at
            """,
            upload_id,
            tenant_id,
            file_name,
            mime_type,
            size_bytes,
            _json.dumps(metadata or {}),
            expires_at,
        )
        return dict(row)

    async def get_document_upload(self, tenant_id: str, upload_id: str) -> dict[str, Any] | None:
        """Fetch one tenant upload record."""
        row = await self._fetchrow(
            """
            SELECT upload_id, tenant_id, file_name, mime_type, size_bytes, metadata,
                   status, document_id, expires_at, created_at, updated_at
            FROM tenant_document_uploads
            WHERE tenant_id = $1::uuid AND upload_id = $2::uuid
            LIMIT 1
            """,
            tenant_id,
            upload_id,
        )
        return dict(row) if row else None

    async def mark_document_upload_completed(
        self,
        tenant_id: str,
        *,
        upload_id: str,
        document_id: str,
    ) -> None:
        """Mark upload as completed and bind created document id."""
        await self._execute(
            """
            UPDATE tenant_document_uploads
            SET status = 'completed',
                document_id = $3::uuid,
                updated_at = now()
            WHERE tenant_id = $1::uuid AND upload_id = $2::uuid
            """,
            tenant_id,
            upload_id,
            document_id,
        )

    async def create_document(
        self,
        tenant_id: str,
        *,
        document_id: str,
        file_name: str,
        mime_type: str,
        object_key: str,
        status: str,
        size_bytes: int,
        checksum_sha256: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Insert a tenant document metadata record."""
        import json as _json

        row = await self._fetchrow(
            """
            INSERT INTO tenant_documents (
                document_id, tenant_id, file_name, mime_type, object_key, status,
                size_bytes, checksum_sha256, metadata
            )
            VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9::jsonb)
            RETURNING document_id, tenant_id, file_name, mime_type, object_key, status,
                      size_bytes, checksum_sha256, metadata, extracted_text, preview_html,
                      chunk_count, indexed_at, archived_at, purge_after, purged_at,
                      archived_reason, error_message, created_at, updated_at
            """,
            document_id,
            tenant_id,
            file_name,
            mime_type,
            object_key,
            status,
            size_bytes,
            checksum_sha256,
            _json.dumps(metadata or {}),
        )
        return dict(row)

    async def get_document(self, tenant_id: str, document_id: str) -> dict[str, Any] | None:
        """Fetch one tenant document by id."""
        row = await self._fetchrow(
            """
            SELECT document_id, tenant_id, file_name, mime_type, object_key, status,
                   size_bytes, checksum_sha256, metadata, extracted_text, preview_html,
                   chunk_count, indexed_at, archived_at, purge_after, purged_at,
                   archived_reason, error_message, created_at, updated_at
            FROM tenant_documents
            WHERE tenant_id = $1::uuid AND document_id = $2::uuid
            LIMIT 1
            """,
            tenant_id,
            document_id,
        )
        return dict(row) if row else None

    async def list_documents(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
        include_archived: bool = False,
    ) -> list[dict[str, Any]]:
        """List tenant documents by newest first."""
        if include_archived:
            rows = await self._fetch(
                """
                SELECT document_id, tenant_id, file_name, mime_type, object_key, status,
                       size_bytes, checksum_sha256, metadata, extracted_text, preview_html,
                       chunk_count, indexed_at, archived_at, purge_after, purged_at,
                       archived_reason, error_message, created_at, updated_at
                FROM tenant_documents
                WHERE tenant_id = $1::uuid
                ORDER BY created_at DESC
                LIMIT $2
                """,
                tenant_id,
                limit,
            )
        else:
            rows = await self._fetch(
                """
                SELECT document_id, tenant_id, file_name, mime_type, object_key, status,
                       size_bytes, checksum_sha256, metadata, extracted_text, preview_html,
                       chunk_count, indexed_at, archived_at, purge_after, purged_at,
                       archived_reason, error_message, created_at, updated_at
                FROM tenant_documents
                WHERE tenant_id = $1::uuid
                  AND status NOT IN ('archiving', 'archived', 'purged')
                ORDER BY created_at DESC
                LIMIT $2
                """,
                tenant_id,
                limit,
            )
        return [dict(r) for r in rows]

    async def update_document_status(
        self,
        tenant_id: str,
        *,
        document_id: str,
        status: str,
        error_message: str | None,
    ) -> None:
        """Update document status/error fields."""
        await self._execute(
            """
            UPDATE tenant_documents
            SET status = $3,
                error_message = $4,
                updated_at = now()
            WHERE tenant_id = $1::uuid AND document_id = $2::uuid
            """,
            tenant_id,
            document_id,
            status,
            error_message,
        )

    async def update_document_index_payload(
        self,
        tenant_id: str,
        *,
        document_id: str,
        extracted_text: str,
        preview_html: str | None,
        chunk_count: int,
        status: str,
        error_message: str | None,
    ) -> None:
        """Update extracted/indexed document payload fields."""
        await self._execute(
            """
            UPDATE tenant_documents
            SET extracted_text = $3,
                preview_html = $4,
                chunk_count = $5,
                status = $6,
                error_message = $7,
                indexed_at = now(),
                updated_at = now()
            WHERE tenant_id = $1::uuid AND document_id = $2::uuid
            """,
            tenant_id,
            document_id,
            extracted_text,
            preview_html,
            chunk_count,
            status,
            error_message,
        )

    async def create_document_ingestion_job(
        self,
        tenant_id: str,
        *,
        document_id: str,
        status: str = "processing",
        error_message: str | None = None,
    ) -> dict[str, Any]:
        """Create a document ingestion job record."""
        row = await self._fetchrow(
            """
            INSERT INTO document_ingestion_jobs (
                tenant_id, document_id, status, error_message
            )
            VALUES ($1::uuid, $2::uuid, $3, $4)
            RETURNING job_id, tenant_id, document_id, status, error_message,
                      created_at, started_at, completed_at
            """,
            tenant_id,
            document_id,
            status,
            error_message,
        )
        return dict(row)

    async def update_document_ingestion_job(
        self,
        tenant_id: str,
        *,
        job_id: str,
        status: str,
        error_message: str | None,
    ) -> None:
        """Update document ingestion job terminal state."""
        await self._execute(
            """
            UPDATE document_ingestion_jobs
            SET status = $3,
                error_message = $4,
                completed_at = CASE
                    WHEN $3 IN ('indexed', 'failed') THEN now()
                    ELSE completed_at
                END
            WHERE tenant_id = $1::uuid AND job_id = $2::uuid
            """,
            tenant_id,
            job_id,
            status,
            error_message,
        )

    async def create_document_archive_job(
        self,
        tenant_id: str,
        *,
        document_id: str,
        status: str = "queued",
        error_message: str | None = None,
        next_attempt_at: datetime | None = None,
    ) -> dict[str, Any]:
        """Create a document archive job row."""
        row = await self._fetchrow(
            """
            INSERT INTO document_archive_jobs (
                tenant_id, document_id, status, error_message, next_attempt_at
            )
            VALUES ($1::uuid, $2::uuid, $3, $4, $5)
            RETURNING job_id, tenant_id, document_id, status, retry_count, next_attempt_at,
                      error_message, created_at, updated_at, started_at, completed_at
            """,
            tenant_id,
            document_id,
            status,
            error_message,
            next_attempt_at,
        )
        return dict(row)

    async def claim_document_archive_jobs(self, *, limit: int = 25) -> list[dict[str, Any]]:
        """Claim pending archive jobs and mark them as running."""
        rows = await self._fetch(
            """
            WITH candidate AS (
                SELECT id
                FROM document_archive_jobs
                WHERE status IN ('queued', 'failed')
                  AND (next_attempt_at IS NULL OR next_attempt_at <= now())
                ORDER BY created_at ASC
                LIMIT $1
                FOR UPDATE SKIP LOCKED
            )
            UPDATE document_archive_jobs jobs
            SET status = 'running',
                started_at = now(),
                updated_at = now(),
                error_message = NULL
            WHERE jobs.id IN (SELECT id FROM candidate)
            RETURNING job_id, tenant_id, document_id, status, retry_count, next_attempt_at,
                      error_message, created_at, updated_at, started_at, completed_at
            """,
            limit,
        )
        return [dict(r) for r in rows]

    async def mark_document_archive_job_succeeded(self, tenant_id: str, *, job_id: str) -> None:
        """Mark one archive job succeeded."""
        await self._execute(
            """
            UPDATE document_archive_jobs
            SET status = 'succeeded',
                completed_at = now(),
                updated_at = now(),
                error_message = NULL
            WHERE tenant_id = $1::uuid AND job_id = $2::uuid
            """,
            tenant_id,
            job_id,
        )

    async def mark_document_archive_job_failed(
        self,
        tenant_id: str,
        *,
        job_id: str,
        error_message: str,
        next_attempt_at: datetime | None = None,
    ) -> None:
        """Mark one archive job failed and increment retry count."""
        await self._execute(
            """
            UPDATE document_archive_jobs
            SET status = 'failed',
                retry_count = retry_count + 1,
                error_message = $3,
                next_attempt_at = $4,
                updated_at = now()
            WHERE tenant_id = $1::uuid AND job_id = $2::uuid
            """,
            tenant_id,
            job_id,
            error_message,
            next_attempt_at,
        )

    async def mark_document_archiving(
        self,
        tenant_id: str,
        *,
        document_id: str,
        archived_reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Set document lifecycle status to archiving."""
        row = await self._fetchrow(
            """
            UPDATE tenant_documents
            SET status = 'archiving',
                archived_reason = COALESCE($3, archived_reason),
                error_message = NULL,
                updated_at = now()
            WHERE tenant_id = $1::uuid AND document_id = $2::uuid
            RETURNING document_id, tenant_id, file_name, mime_type, object_key, status,
                      size_bytes, checksum_sha256, metadata, extracted_text, preview_html,
                      chunk_count, indexed_at, archived_at, purge_after, purged_at,
                      archived_reason, error_message, created_at, updated_at
            """,
            tenant_id,
            document_id,
            archived_reason,
        )
        return dict(row) if row else None

    async def mark_document_archived(
        self,
        tenant_id: str,
        *,
        document_id: str,
        archived_at: datetime | None = None,
        purge_after: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Mark document archived and set purge schedule."""
        row = await self._fetchrow(
            """
            UPDATE tenant_documents
            SET status = 'archived',
                archived_at = COALESCE($3, now()),
                purge_after = COALESCE($4, purge_after),
                updated_at = now()
            WHERE tenant_id = $1::uuid AND document_id = $2::uuid
            RETURNING document_id, tenant_id, file_name, mime_type, object_key, status,
                      size_bytes, checksum_sha256, metadata, extracted_text, preview_html,
                      chunk_count, indexed_at, archived_at, purge_after, purged_at,
                      archived_reason, error_message, created_at, updated_at
            """,
            tenant_id,
            document_id,
            archived_at,
            purge_after,
        )
        return dict(row) if row else None

    async def mark_document_purged(
        self,
        tenant_id: str,
        *,
        document_id: str,
        purged_at: datetime | None = None,
    ) -> dict[str, Any] | None:
        """Mark document purged and clear derived payload fields."""
        row = await self._fetchrow(
            """
            UPDATE tenant_documents
            SET status = 'purged',
                purged_at = COALESCE($3, now()),
                extracted_text = NULL,
                preview_html = NULL,
                chunk_count = 0,
                error_message = NULL,
                updated_at = now()
            WHERE tenant_id = $1::uuid AND document_id = $2::uuid
            RETURNING document_id, tenant_id, file_name, mime_type, object_key, status,
                      size_bytes, checksum_sha256, metadata, extracted_text, preview_html,
                      chunk_count, indexed_at, archived_at, purge_after, purged_at,
                      archived_reason, error_message, created_at, updated_at
            """,
            tenant_id,
            document_id,
            purged_at,
        )
        return dict(row) if row else None

    async def list_documents_due_for_purge(self, *, limit: int = 100) -> list[dict[str, Any]]:
        """List archived documents whose purge window has elapsed."""
        rows = await self._fetch(
            """
            SELECT document_id, tenant_id, file_name, mime_type, object_key, status,
                   size_bytes, checksum_sha256, metadata, extracted_text, preview_html,
                   chunk_count, indexed_at, archived_at, purge_after, purged_at,
                   archived_reason, error_message, created_at, updated_at
            FROM tenant_documents
            WHERE status = 'archived'
              AND purge_after IS NOT NULL
              AND purge_after <= now()
            ORDER BY purge_after ASC
            LIMIT $1
            """,
            limit,
        )
        return [dict(r) for r in rows]

    async def add_release_marker(
        self,
        tenant_id: str,
        *,
        source: str = "api",
        environment: str = "production",
        commit_sha: str | None = None,
        branch: str | None = None,
        tag_name: str | None = None,
        deployed_at: datetime | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Record a tenant deployment marker."""
        import json as _json

        row = await self._fetchrow(
            """
            INSERT INTO tenant_release_markers
                (
                    tenant_id,
                    source,
                    environment,
                    commit_sha,
                    branch,
                    tag_name,
                    deployed_at,
                    metadata
                )
            VALUES ($1::uuid, $2, $3, $4, $5, $6, COALESCE($7, now()), $8::jsonb)
            RETURNING marker_id, tenant_id, source, environment, commit_sha, branch,
                      tag_name, deployed_at, metadata, created_at
            """,
            tenant_id,
            source,
            environment,
            commit_sha,
            branch,
            tag_name,
            deployed_at,
            _json.dumps(metadata or {}),
        )
        return dict(row)

    async def get_release_markers(
        self,
        tenant_id: str,
        *,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Fetch recent release markers for a tenant."""
        rows = await self._fetch(
            """
            SELECT marker_id, tenant_id, source, environment, commit_sha, branch,
                   tag_name, deployed_at, metadata, created_at
            FROM tenant_release_markers
            WHERE tenant_id = $1::uuid
            ORDER BY deployed_at DESC
            LIMIT $2
            """,
            tenant_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def register_release_nonce(
        self,
        tenant_id: str,
        *,
        nonce: str,
        signature: str | None = None,
    ) -> bool:
        """Register a release ingest nonce, returning False on replay."""
        await self._execute(
            "DELETE FROM tenant_release_nonces WHERE created_at < now() - INTERVAL '30 days'"
        )
        result = await self._execute(
            """
            INSERT INTO tenant_release_nonces (tenant_id, nonce, signature)
            VALUES ($1::uuid, $2, $3)
            ON CONFLICT (tenant_id, nonce) DO NOTHING
            """,
            tenant_id,
            nonce,
            signature,
        )
        return result == "INSERT 0 1"

    async def prune_web_events(
        self,
        tenant_id: str,
        *,
        retention_days: int,
    ) -> int:
        """Delete web events older than retention window and return deleted count."""
        count = await self._fetchval(
            """
            WITH deleted AS (
                DELETE FROM tenant_web_events
                WHERE tenant_id = $1::uuid
                  AND occurred_at < now() - make_interval(days => $2)
                RETURNING 1
            )
            SELECT count(*) FROM deleted
            """,
            tenant_id,
            retention_days,
        )
        return int(count or 0)

    async def prune_replay_chunks(
        self,
        tenant_id: str,
        *,
        retention_days: int,
    ) -> list[str]:
        """Delete replay chunk metadata older than retention window and return object keys."""
        rows = await self._fetch(
            """
            DELETE FROM tenant_replay_chunks
            WHERE tenant_id = $1::uuid
              AND created_at < now() - make_interval(days => $2)
            RETURNING object_key
            """,
            tenant_id,
            retention_days,
        )
        return [str(r["object_key"]) for r in rows if r.get("object_key")]

    async def upsert_funnel_stage_daily(
        self,
        tenant_id: str,
        *,
        metric_date: date,
        funnel_name: str,
        stage_name: str,
        stage_order: int,
        users_count: int,
        drop_off_rate: float | None,
        conversion_rate: float | None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Upsert one daily funnel stage metric."""
        import json as _json

        row = await self._fetchrow(
            """
            INSERT INTO tenant_funnel_daily
                (tenant_id, metric_date, funnel_name, stage_name, stage_order,
                 users_count, drop_off_rate, conversion_rate, metadata)
            VALUES ($1::uuid, $2::date, $3, $4, $5, $6, $7, $8, $9::jsonb)
            ON CONFLICT (tenant_id, metric_date, funnel_name, stage_name) DO UPDATE
            SET stage_order = EXCLUDED.stage_order,
                users_count = EXCLUDED.users_count,
                drop_off_rate = EXCLUDED.drop_off_rate,
                conversion_rate = EXCLUDED.conversion_rate,
                metadata = EXCLUDED.metadata,
                updated_at = now()
            RETURNING tenant_id, metric_date, funnel_name, stage_name, stage_order,
                      users_count, drop_off_rate, conversion_rate, metadata, updated_at
            """,
            tenant_id,
            metric_date,
            funnel_name,
            stage_name,
            stage_order,
            users_count,
            drop_off_rate,
            conversion_rate,
            _json.dumps(metadata or {}),
        )
        return dict(row)

    async def get_funnel_daily(
        self,
        tenant_id: str,
        *,
        metric_date: date | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        """Fetch daily funnel metrics."""
        if metric_date is not None:
            rows = await self._fetch(
                """
                SELECT tenant_id, metric_date, funnel_name, stage_name, stage_order,
                       users_count, drop_off_rate, conversion_rate, metadata, updated_at
                FROM tenant_funnel_daily
                WHERE tenant_id = $1::uuid AND metric_date = $2::date
                ORDER BY funnel_name, stage_order
                """,
                tenant_id,
                metric_date,
            )
        else:
            rows = await self._fetch(
                """
                SELECT tenant_id, metric_date, funnel_name, stage_name, stage_order,
                       users_count, drop_off_rate, conversion_rate, metadata, updated_at
                FROM tenant_funnel_daily
                WHERE tenant_id = $1::uuid
                ORDER BY metric_date DESC, funnel_name, stage_order
                LIMIT $2
                """,
                tenant_id,
                limit,
            )
        return [dict(r) for r in rows]

    async def create_recommendation(
        self,
        tenant_id: str,
        *,
        recommendation_type: str,
        title: str,
        description: str,
        evidence: dict[str, Any] | None = None,
        risk_class: str = "low",
        confidence: float = 0.0,
        expected_impact: float | None = None,
        status: str = "open",
        source: str = "detector",
    ) -> dict[str, Any]:
        """Create a tenant recommendation."""
        import json as _json

        row = await self._fetchrow(
            """
            INSERT INTO tenant_recommendations
                (tenant_id, recommendation_type, title, description, evidence,
                 risk_class, confidence, expected_impact, status, source)
            VALUES ($1::uuid, $2, $3, $4, $5::jsonb, $6, $7, $8, $9, $10)
            RETURNING recommendation_id, tenant_id, recommendation_type, title, description,
                      evidence, risk_class, confidence, expected_impact, status, source,
                      generated_at, created_at, updated_at
            """,
            tenant_id,
            recommendation_type,
            title,
            description,
            _json.dumps(evidence or {}),
            risk_class,
            confidence,
            expected_impact,
            status,
            source,
        )
        return dict(row)

    async def list_recommendations(
        self,
        tenant_id: str,
        *,
        status: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """List tenant recommendations, optionally filtered by status."""
        if status:
            rows = await self._fetch(
                """
                SELECT recommendation_id, tenant_id, recommendation_type, title, description,
                       evidence, risk_class, confidence, expected_impact, status, source,
                       generated_at, created_at, updated_at
                FROM tenant_recommendations
                WHERE tenant_id = $1::uuid AND status = $2
                ORDER BY generated_at DESC
                LIMIT $3
                """,
                tenant_id,
                status,
                limit,
            )
        else:
            rows = await self._fetch(
                """
                SELECT recommendation_id, tenant_id, recommendation_type, title, description,
                       evidence, risk_class, confidence, expected_impact, status, source,
                       generated_at, created_at, updated_at
                FROM tenant_recommendations
                WHERE tenant_id = $1::uuid
                ORDER BY generated_at DESC
                LIMIT $2
                """,
                tenant_id,
                limit,
            )
        return [dict(r) for r in rows]

    async def add_recommendation_feedback(
        self,
        tenant_id: str,
        recommendation_id: str,
        *,
        feedback_type: str,
        note: str | None = None,
        actor: str | None = None,
    ) -> dict[str, Any]:
        """Record operator feedback for a recommendation."""
        row = await self._fetchrow(
            """
            INSERT INTO tenant_recommendation_feedback
                (tenant_id, recommendation_id, feedback_type, note, actor)
            VALUES ($1::uuid, $2::uuid, $3, $4, $5)
            RETURNING feedback_id, tenant_id, recommendation_id, feedback_type,
                      note, actor, created_at
            """,
            tenant_id,
            recommendation_id,
            feedback_type,
            note,
            actor,
        )

        status_map = {
            "accepted": "accepted",
            "rejected": "rejected",
            "implemented": "implemented",
        }
        mapped_status = status_map.get(feedback_type.lower())
        if mapped_status:
            await self._execute(
                """
                UPDATE tenant_recommendations
                SET status = $3, updated_at = now()
                WHERE tenant_id = $1::uuid AND recommendation_id = $2::uuid
                """,
                tenant_id,
                recommendation_id,
                mapped_status,
            )

        return dict(row)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_schema(self) -> None:
        """Create tables and indexes if they don't exist.

        Handles the PostgreSQL race condition where concurrent processes both
        try to ``CREATE TABLE IF NOT EXISTS`` at the same time, resulting in a
        ``UniqueViolationError`` on ``pg_type_typname_nsp_index``.  This is
        harmless — the table was successfully created by the other process.
        """
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                await conn.execute(_SCHEMA_SQL)
            log.info("tenant_schema_ensured")
        except asyncpg.UniqueViolationError:
            # Another process already created the schema concurrently — safe to proceed.
            log.info("tenant_schema_ensured", note="concurrent creation resolved")
        except asyncpg.PostgresError as exc:
            log.error("tenant_schema_creation_failed", error=str(exc))
            raise

    async def _audit(
        self,
        action: str,
        tenant_id: str | None = None,
        details: dict[str, Any] | None = None,
        ip_address: str | None = None,
    ) -> None:
        """Write an audit log entry."""
        import json

        await self._execute(
            """
            INSERT INTO tenant_audit_log (tenant_id, action, details, ip_address)
            VALUES ($1::uuid, $2, $3::jsonb, $4)
            """,
            tenant_id,
            action,
            json.dumps(details or {}),
            ip_address,
        )

    async def _fetchrow(self, query: str, *args: Any) -> asyncpg.Record:
        """Execute query and return a single row."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            result: asyncpg.Record = await conn.fetchrow(query, *args)
            return result

    async def _fetchval(self, query: str, *args: Any) -> Any:
        """Execute query and return first column of first row."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            return await conn.fetchval(query, *args)

    async def _fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        """Execute query and return all rows."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            result: list[asyncpg.Record] = await conn.fetch(query, *args)
            return result

    async def _execute(self, query: str, *args: Any) -> str:
        """Execute query and return status string."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            result: str = await conn.execute(query, *args)
            return result
