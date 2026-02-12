"""PostgreSQL-backed tenant manager for multi-tenant API.

Provides CRUD operations for tenants and chat sessions, with API key
management. Follows the same pool/schema pattern as UserManager.
"""

from __future__ import annotations

from typing import Any

import asyncpg  # type: ignore[import-untyped,import-not-found]

from zetherion_ai.api.auth import generate_api_key, verify_api_key
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
            self._pool = await asyncpg.create_pool(dsn=self._dsn)
            log.info("tenant_pool_created", dsn=self._dsn.split("@")[-1])
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
            UPDATE tenants SET {', '.join(sets)}
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

    async def get_interactions(
        self,
        tenant_id: str,
        *,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        """Get recent interactions for a tenant."""
        rows = await self._fetch(
            """
            SELECT interaction_id, tenant_id, contact_id, session_id,
                   interaction_type, summary, entities, sentiment, intent,
                   outcome, created_at
            FROM tenant_interactions
            WHERE tenant_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT $2
            """,
            tenant_id,
            limit,
        )
        return [dict(r) for r in rows]

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
