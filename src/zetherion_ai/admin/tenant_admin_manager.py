"""Tenant-scoped admin manager for Discord access, settings, secrets, and audit."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from zetherion_ai.logging import get_logger
from zetherion_ai.settings_manager import VALID_NAMESPACES

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

    from zetherion_ai.memory.qdrant import QdrantMemory
    from zetherion_ai.security.encryption import FieldEncryptor

log = get_logger("zetherion_ai.admin.tenant_admin_manager")

VALID_TENANT_ROLES = frozenset({"owner", "admin", "user", "restricted"})
VALID_EMAIL_ACCOUNT_STATUSES = frozenset(
    {"pending", "connected", "degraded", "revoked", "disconnected"}
)
VALID_EMAIL_SYNC_DIRECTIONS = frozenset(
    {"email", "calendar_read", "calendar_write", "bi_directional"}
)
VALID_EMAIL_SYNC_STATUSES = frozenset({"queued", "running", "succeeded", "failed", "retrying"})
VALID_CRITICAL_SEVERITIES = frozenset({"critical", "high", "normal"})
VALID_CRITICAL_STATUSES = frozenset({"open", "resolved", "dismissed"})

_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS tenant_discord_users (
    tenant_id        UUID         NOT NULL,
    discord_user_id  BIGINT       NOT NULL,
    role             VARCHAR(20)  NOT NULL
                     CHECK (role IN ('owner', 'admin', 'user', 'restricted')),
    created_by       TEXT,
    updated_by       TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, discord_user_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_discord_users_role
    ON tenant_discord_users (tenant_id, role);

CREATE TABLE IF NOT EXISTS tenant_discord_bindings (
    id               BIGSERIAL    PRIMARY KEY,
    tenant_id        UUID         NOT NULL,
    guild_id         BIGINT       NOT NULL,
    channel_id       BIGINT,
    priority         INT          NOT NULL DEFAULT 100,
    is_active        BOOLEAN      NOT NULL DEFAULT TRUE,
    created_by       TEXT,
    updated_by       TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_discord_bindings_guild_default
    ON tenant_discord_bindings (tenant_id, guild_id)
    WHERE channel_id IS NULL;
CREATE UNIQUE INDEX IF NOT EXISTS uq_tenant_discord_bindings_channel_override
    ON tenant_discord_bindings (tenant_id, channel_id)
    WHERE channel_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_tenant_discord_bindings_lookup
    ON tenant_discord_bindings (guild_id, channel_id, priority, updated_at DESC)
    WHERE is_active = TRUE;

CREATE TABLE IF NOT EXISTS tenant_settings_overrides (
    tenant_id        UUID         NOT NULL,
    namespace        VARCHAR(50)  NOT NULL,
    key              VARCHAR(100) NOT NULL,
    value            TEXT,
    data_type        VARCHAR(20)  NOT NULL DEFAULT 'string'
                     CHECK (data_type IN ('string', 'int', 'float', 'bool', 'json')),
    updated_by       TEXT,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, namespace, key)
);

CREATE INDEX IF NOT EXISTS idx_tenant_settings_namespace
    ON tenant_settings_overrides (tenant_id, namespace);

CREATE TABLE IF NOT EXISTS tenant_setting_versions (
    id               BIGSERIAL    PRIMARY KEY,
    tenant_id        UUID         NOT NULL,
    namespace        VARCHAR(50)  NOT NULL,
    key              VARCHAR(100) NOT NULL,
    version          INT          NOT NULL,
    value            TEXT,
    data_type        VARCHAR(20)  NOT NULL DEFAULT 'string'
                     CHECK (data_type IN ('string', 'int', 'float', 'bool', 'json')),
    operation        VARCHAR(20)  NOT NULL DEFAULT 'upsert'
                     CHECK (operation IN ('upsert', 'delete', 'rollback')),
    updated_by       TEXT,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, namespace, key, version)
);

CREATE TABLE IF NOT EXISTS tenant_secrets (
    tenant_id        UUID         NOT NULL,
    name             VARCHAR(100) NOT NULL,
    value_enc        TEXT         NOT NULL,
    version          INT          NOT NULL DEFAULT 1,
    updated_by       TEXT,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    description      TEXT,
    PRIMARY KEY (tenant_id, name)
);

CREATE TABLE IF NOT EXISTS tenant_secret_versions (
    id               BIGSERIAL    PRIMARY KEY,
    tenant_id        UUID         NOT NULL,
    name             VARCHAR(100) NOT NULL,
    value_enc        TEXT,
    version          INT          NOT NULL,
    operation        VARCHAR(20)  NOT NULL DEFAULT 'upsert'
                     CHECK (operation IN ('upsert', 'delete', 'rollback')),
    updated_by       TEXT,
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    description      TEXT,
    UNIQUE (tenant_id, name, version)
);

CREATE INDEX IF NOT EXISTS idx_tenant_secret_versions_lookup
    ON tenant_secret_versions (tenant_id, name, version DESC);

CREATE TABLE IF NOT EXISTS tenant_admin_audit_log (
    id               BIGSERIAL    PRIMARY KEY,
    tenant_id        UUID         NOT NULL,
    action           VARCHAR(80)  NOT NULL,
    actor_sub        TEXT         NOT NULL,
    actor_roles      JSONB        NOT NULL DEFAULT '[]'::jsonb,
    actor_email      TEXT,
    request_id       TEXT,
    change_ticket_id TEXT,
    before_json      JSONB,
    after_json       JSONB,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_admin_audit_tenant_created
    ON tenant_admin_audit_log (tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_admin_audit_request
    ON tenant_admin_audit_log (request_id);

CREATE TABLE IF NOT EXISTS tenant_email_provider_configs (
    tenant_id            UUID         NOT NULL,
    provider             VARCHAR(40)  NOT NULL,
    client_id_ref        VARCHAR(140) NOT NULL,
    client_secret_ref    VARCHAR(140) NOT NULL,
    redirect_uri         TEXT         NOT NULL,
    enabled              BOOLEAN      NOT NULL DEFAULT TRUE,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_by           TEXT,
    updated_by           TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_tenant_email_provider_configs_lookup
    ON tenant_email_provider_configs (tenant_id, provider);

CREATE TABLE IF NOT EXISTS tenant_email_oauth_states (
    state                VARCHAR(140) PRIMARY KEY,
    tenant_id            UUID         NOT NULL,
    provider             VARCHAR(40)  NOT NULL,
    account_hint         TEXT,
    created_by           TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at           TIMESTAMPTZ  NOT NULL,
    consumed_at          TIMESTAMPTZ
);

CREATE INDEX IF NOT EXISTS idx_tenant_email_oauth_states_lookup
    ON tenant_email_oauth_states (tenant_id, provider, expires_at DESC);

CREATE TABLE IF NOT EXISTS tenant_email_accounts (
    account_id           UUID         PRIMARY KEY,
    tenant_id            UUID         NOT NULL,
    provider             VARCHAR(40)  NOT NULL,
    external_account_id  TEXT,
    email_address        TEXT         NOT NULL,
    oauth_subject        TEXT,
    status               VARCHAR(20)  NOT NULL DEFAULT 'connected'
                         CHECK (
                             status IN (
                                 'pending',
                                 'connected',
                                 'degraded',
                                 'revoked',
                                 'disconnected'
                             )
                         ),
    scopes               TEXT[]       NOT NULL DEFAULT '{}',
    access_token_enc     TEXT         NOT NULL,
    refresh_token_enc    TEXT         NOT NULL,
    token_expiry         TIMESTAMPTZ,
    sync_cursor          TEXT,
    primary_calendar_id  TEXT,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_by           TEXT,
    updated_by           TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider, email_address)
);

CREATE INDEX IF NOT EXISTS idx_tenant_email_accounts_lookup
    ON tenant_email_accounts (tenant_id, provider, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS tenant_email_sync_jobs (
    job_id               UUID         PRIMARY KEY,
    tenant_id            UUID         NOT NULL,
    account_id           UUID         NOT NULL
                         REFERENCES tenant_email_accounts(account_id) ON DELETE CASCADE,
    direction            VARCHAR(30)  NOT NULL
                         CHECK (
                             direction IN (
                                 'email',
                                 'calendar_read',
                                 'calendar_write',
                                 'bi_directional'
                             )
                         ),
    status               VARCHAR(20)  NOT NULL DEFAULT 'queued'
                         CHECK (status IN ('queued', 'running', 'succeeded', 'failed', 'retrying')),
    started_at           TIMESTAMPTZ,
    completed_at         TIMESTAMPTZ,
    error_code           TEXT,
    error_detail         TEXT,
    retry_count          INT          NOT NULL DEFAULT 0,
    idempotency_key      TEXT,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_by           TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_email_sync_jobs_lookup
    ON tenant_email_sync_jobs (tenant_id, account_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tenant_email_message_cache (
    id                   BIGSERIAL    PRIMARY KEY,
    tenant_id            UUID         NOT NULL,
    account_id           UUID         NOT NULL
                         REFERENCES tenant_email_accounts(account_id) ON DELETE CASCADE,
    message_id           TEXT         NOT NULL,
    subject              TEXT,
    from_email           TEXT,
    body_preview         TEXT,
    received_at          TIMESTAMPTZ,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, account_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_email_message_cache_retention
    ON tenant_email_message_cache (created_at);

CREATE TABLE IF NOT EXISTS tenant_email_critical_items (
    item_id              UUID         PRIMARY KEY,
    tenant_id            UUID         NOT NULL,
    account_id           UUID         NOT NULL
                         REFERENCES tenant_email_accounts(account_id) ON DELETE CASCADE,
    message_id           TEXT         NOT NULL,
    severity             VARCHAR(20)  NOT NULL
                         CHECK (severity IN ('critical', 'high', 'normal')),
    score                FLOAT        NOT NULL DEFAULT 0,
    reason_codes         TEXT[]       NOT NULL DEFAULT '{}',
    entities_json        JSONB        NOT NULL DEFAULT '{}'::jsonb,
    action_required_by   TIMESTAMPTZ,
    status               VARCHAR(20)  NOT NULL DEFAULT 'open'
                         CHECK (status IN ('open', 'resolved', 'dismissed')),
    source_json          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, account_id, message_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_email_critical_lookup
    ON tenant_email_critical_items (tenant_id, status, severity, created_at DESC);

CREATE TABLE IF NOT EXISTS tenant_email_insights (
    insight_id           UUID         PRIMARY KEY,
    tenant_id            UUID         NOT NULL,
    account_id           UUID
                         REFERENCES tenant_email_accounts(account_id) ON DELETE SET NULL,
    insight_type         TEXT         NOT NULL,
    confidence           FLOAT        NOT NULL DEFAULT 0,
    payload_json         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    source_message_ids   TEXT[]       NOT NULL DEFAULT '{}',
    vector_id            TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_email_insights_lookup
    ON tenant_email_insights (tenant_id, insight_type, created_at DESC);

CREATE TABLE IF NOT EXISTS tenant_email_events (
    event_id             BIGSERIAL    PRIMARY KEY,
    tenant_id            UUID         NOT NULL,
    account_id           UUID,
    event_type           TEXT         NOT NULL,
    payload_json         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_email_events_lookup
    ON tenant_email_events (tenant_id, event_type, created_at DESC);
"""


@dataclass(frozen=True)
class AdminActorContext:
    """Authenticated admin actor context propagated from CGS."""

    actor_sub: str
    actor_roles: tuple[str, ...]
    request_id: str
    timestamp: datetime
    nonce: str
    actor_email: str | None = None
    change_ticket_id: str | None = None


class TenantAdminManager:
    """Tenant-scoped admin storage + business logic."""

    def __init__(
        self,
        *,
        pool: asyncpg.Pool,
        encryptor: FieldEncryptor | None = None,
        setting_key_allowlist: dict[str, frozenset[str]] | None = None,
        critical_scorer: Callable[[str, str], Awaitable[dict[str, Any] | None]] | None = None,
        vector_memory: QdrantMemory | None = None,
    ) -> None:
        self._pool = pool
        self._encryptor = encryptor
        self._setting_key_allowlist = setting_key_allowlist or {}
        self._settings_cache: dict[tuple[str, str, str], Any] = {}
        self._secrets_cache: dict[tuple[str, str], str] = {}
        self._critical_scorer = critical_scorer
        self._vector_memory = vector_memory
        self._oauth_state_ttl = timedelta(minutes=15)

    def set_critical_scorer(
        self, scorer: Callable[[str, str], Awaitable[dict[str, Any] | None]] | None
    ) -> None:
        """Inject/update the optional model scorer for criticality enrichment."""
        self._critical_scorer = scorer

    def set_vector_memory(self, memory: QdrantMemory | None) -> None:
        """Inject/update optional vector-memory sink for insights."""
        self._vector_memory = memory

    async def initialize(self) -> None:
        """Ensure schema and load caches."""
        await self.ensure_schema()
        await self.refresh()

    async def ensure_schema(self) -> None:
        """Create tenant-admin schema when missing."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(_SCHEMA_SQL)
        except Exception:
            log.exception("tenant_admin_schema_ensure_failed")
            raise
        log.info("tenant_admin_schema_ensured")

    async def refresh(self) -> None:
        """Refresh in-memory tenant settings and secrets caches."""
        async with self._pool.acquire() as conn:
            setting_rows = await conn.fetch(
                "SELECT tenant_id::text AS tenant_id, namespace, key, value, data_type "
                "FROM tenant_settings_overrides"
            )
            secret_rows = await conn.fetch(
                "SELECT tenant_id::text AS tenant_id, name, value_enc FROM tenant_secrets"
            )

        settings_cache: dict[tuple[str, str, str], Any] = {}
        for row in setting_rows:
            tenant_id = str(row["tenant_id"])
            namespace = str(row["namespace"])
            key = str(row["key"])
            coerced = self._coerce_setting_value(str(row["value"]), str(row["data_type"]))
            settings_cache[(tenant_id, namespace, key)] = coerced
        self._settings_cache = settings_cache

        secrets_cache: dict[tuple[str, str], str] = {}
        for row in secret_rows:
            tenant_id = str(row["tenant_id"])
            name = str(row["name"])
            decrypted = self._decrypt(str(row["value_enc"]))
            if decrypted is not None:
                secrets_cache[(tenant_id, name)] = decrypted
        self._secrets_cache = secrets_cache

    def get_setting_cached(
        self, tenant_id: str, namespace: str, key: str, default: Any = None
    ) -> Any:
        """Get tenant setting from in-memory cache."""
        return self._settings_cache.get((tenant_id, namespace, key), default)

    def get_secret_cached(
        self, tenant_id: str, name: str, default: str | None = None
    ) -> str | None:
        """Get tenant secret from in-memory cache."""
        return self._secrets_cache.get((tenant_id, name), default)

    async def list_discord_users(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            SELECT tenant_id::text AS tenant_id, discord_user_id, role,
                   created_by, updated_by, created_at, updated_at
            FROM tenant_discord_users
            WHERE tenant_id = $1::uuid
            ORDER BY created_at
            """,
            tenant_id,
        )
        return [dict(row) for row in rows]

    async def is_discord_user_allowed(self, tenant_id: str, discord_user_id: int) -> bool:
        row = await self._fetchrow(
            """
            SELECT 1
            FROM tenant_discord_users
            WHERE tenant_id = $1::uuid
              AND discord_user_id = $2
            """,
            tenant_id,
            discord_user_id,
        )
        return row is not None

    async def get_discord_user_role(self, tenant_id: str, discord_user_id: int) -> str | None:
        role = await self._fetchval(
            """
            SELECT role
            FROM tenant_discord_users
            WHERE tenant_id = $1::uuid
              AND discord_user_id = $2
            """,
            tenant_id,
            discord_user_id,
        )
        if role is None:
            return None
        return str(role)

    async def upsert_discord_user(
        self,
        *,
        tenant_id: str,
        discord_user_id: int,
        role: str,
        actor: AdminActorContext,
    ) -> dict[str, Any]:
        if role not in VALID_TENANT_ROLES:
            raise ValueError(f"Invalid role '{role}'")

        before = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, discord_user_id, role,
                   created_by, updated_by, created_at, updated_at
            FROM tenant_discord_users
            WHERE tenant_id = $1::uuid
              AND discord_user_id = $2
            """,
            tenant_id,
            discord_user_id,
        )
        row = await self._fetchrow(
            """
            INSERT INTO tenant_discord_users (
                tenant_id,
                discord_user_id,
                role,
                created_by,
                updated_by
            ) VALUES ($1::uuid, $2, $3, $4, $4)
            ON CONFLICT (tenant_id, discord_user_id)
            DO UPDATE SET role = EXCLUDED.role,
                          updated_by = EXCLUDED.updated_by,
                          updated_at = now()
            RETURNING tenant_id::text AS tenant_id, discord_user_id, role,
                      created_by, updated_by, created_at, updated_at
            """,
            tenant_id,
            discord_user_id,
            role,
            actor.actor_sub,
        )
        if row is None:
            raise RuntimeError("Failed to upsert tenant discord user")
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_discord_user_upsert",
            actor=actor,
            before=dict(before) if before is not None else None,
            after=after,
        )
        return after

    async def delete_discord_user(
        self,
        *,
        tenant_id: str,
        discord_user_id: int,
        actor: AdminActorContext,
    ) -> bool:
        before = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, discord_user_id, role,
                   created_by, updated_by, created_at, updated_at
            FROM tenant_discord_users
            WHERE tenant_id = $1::uuid
              AND discord_user_id = $2
            """,
            tenant_id,
            discord_user_id,
        )
        result = await self._execute(
            """
            DELETE FROM tenant_discord_users
            WHERE tenant_id = $1::uuid
              AND discord_user_id = $2
            """,
            tenant_id,
            discord_user_id,
        )
        deleted = result == "DELETE 1"
        if deleted:
            await self._write_audit(
                tenant_id=tenant_id,
                action="tenant_discord_user_delete",
                actor=actor,
                before=dict(before) if before is not None else None,
                after=None,
            )
        return deleted

    async def update_discord_user_role(
        self,
        *,
        tenant_id: str,
        discord_user_id: int,
        role: str,
        actor: AdminActorContext,
    ) -> bool:
        if role not in VALID_TENANT_ROLES:
            raise ValueError(f"Invalid role '{role}'")

        before = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, discord_user_id, role,
                   created_by, updated_by, created_at, updated_at
            FROM tenant_discord_users
            WHERE tenant_id = $1::uuid
              AND discord_user_id = $2
            """,
            tenant_id,
            discord_user_id,
        )
        if before is None:
            return False

        row = await self._fetchrow(
            """
            UPDATE tenant_discord_users
            SET role = $3,
                updated_by = $4,
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND discord_user_id = $2
            RETURNING tenant_id::text AS tenant_id, discord_user_id, role,
                      created_by, updated_by, created_at, updated_at
            """,
            tenant_id,
            discord_user_id,
            role,
            actor.actor_sub,
        )
        if row is None:
            return False
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_discord_user_role_update",
            actor=actor,
            before=dict(before),
            after=dict(row),
        )
        return True

    async def list_discord_bindings(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            SELECT tenant_id::text AS tenant_id, guild_id, channel_id, priority,
                   is_active, created_by, updated_by, created_at, updated_at
            FROM tenant_discord_bindings
            WHERE tenant_id = $1::uuid
            ORDER BY CASE WHEN channel_id IS NULL THEN 1 ELSE 0 END,
                     priority ASC,
                     updated_at DESC
            """,
            tenant_id,
        )
        return [dict(row) for row in rows]

    async def put_guild_binding(
        self,
        *,
        tenant_id: str,
        guild_id: int,
        priority: int,
        is_active: bool,
        actor: AdminActorContext,
    ) -> dict[str, Any]:
        before = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, guild_id, channel_id, priority,
                   is_active, created_by, updated_by, created_at, updated_at
            FROM tenant_discord_bindings
            WHERE tenant_id = $1::uuid
              AND guild_id = $2
              AND channel_id IS NULL
            """,
            tenant_id,
            guild_id,
        )
        if before is None:
            row = await self._fetchrow(
                """
                INSERT INTO tenant_discord_bindings (
                    tenant_id,
                    guild_id,
                    channel_id,
                    priority,
                    is_active,
                    created_by,
                    updated_by
                ) VALUES ($1::uuid, $2, NULL, $3, $4, $5, $5)
                RETURNING tenant_id::text AS tenant_id, guild_id, channel_id, priority,
                          is_active, created_by, updated_by, created_at, updated_at
                """,
                tenant_id,
                guild_id,
                priority,
                is_active,
                actor.actor_sub,
            )
        else:
            row = await self._fetchrow(
                """
                UPDATE tenant_discord_bindings
                SET priority = $3,
                    is_active = $4,
                    updated_by = $5,
                    updated_at = now()
                WHERE tenant_id = $1::uuid
                  AND guild_id = $2
                  AND channel_id IS NULL
                RETURNING tenant_id::text AS tenant_id, guild_id, channel_id, priority,
                          is_active, created_by, updated_by, created_at, updated_at
                """,
                tenant_id,
                guild_id,
                priority,
                is_active,
                actor.actor_sub,
            )
        if row is None:
            raise RuntimeError("Failed to upsert guild binding")
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_discord_binding_guild_upsert",
            actor=actor,
            before=dict(before) if before is not None else None,
            after=after,
        )
        return after

    async def put_channel_binding(
        self,
        *,
        tenant_id: str,
        guild_id: int,
        channel_id: int,
        priority: int,
        is_active: bool,
        actor: AdminActorContext,
    ) -> dict[str, Any]:
        before = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, guild_id, channel_id, priority,
                   is_active, created_by, updated_by, created_at, updated_at
            FROM tenant_discord_bindings
            WHERE tenant_id = $1::uuid
              AND channel_id = $2
            """,
            tenant_id,
            channel_id,
        )
        if before is None:
            row = await self._fetchrow(
                """
                INSERT INTO tenant_discord_bindings (
                    tenant_id,
                    guild_id,
                    channel_id,
                    priority,
                    is_active,
                    created_by,
                    updated_by
                ) VALUES ($1::uuid, $2, $3, $4, $5, $6, $6)
                RETURNING tenant_id::text AS tenant_id, guild_id, channel_id, priority,
                          is_active, created_by, updated_by, created_at, updated_at
                """,
                tenant_id,
                guild_id,
                channel_id,
                priority,
                is_active,
                actor.actor_sub,
            )
        else:
            row = await self._fetchrow(
                """
                UPDATE tenant_discord_bindings
                SET guild_id = $3,
                    priority = $4,
                    is_active = $5,
                    updated_by = $6,
                    updated_at = now()
                WHERE tenant_id = $1::uuid
                  AND channel_id = $2
                RETURNING tenant_id::text AS tenant_id, guild_id, channel_id, priority,
                          is_active, created_by, updated_by, created_at, updated_at
                """,
                tenant_id,
                channel_id,
                guild_id,
                priority,
                is_active,
                actor.actor_sub,
            )
        if row is None:
            raise RuntimeError("Failed to upsert channel binding")
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_discord_binding_channel_upsert",
            actor=actor,
            before=dict(before) if before is not None else None,
            after=after,
        )
        return after

    async def delete_channel_binding(
        self,
        *,
        tenant_id: str,
        channel_id: int,
        actor: AdminActorContext,
    ) -> bool:
        before = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, guild_id, channel_id, priority,
                   is_active, created_by, updated_by, created_at, updated_at
            FROM tenant_discord_bindings
            WHERE tenant_id = $1::uuid
              AND channel_id = $2
            """,
            tenant_id,
            channel_id,
        )
        result = await self._execute(
            """
            DELETE FROM tenant_discord_bindings
            WHERE tenant_id = $1::uuid
              AND channel_id = $2
            """,
            tenant_id,
            channel_id,
        )
        deleted = result == "DELETE 1"
        if deleted:
            await self._write_audit(
                tenant_id=tenant_id,
                action="tenant_discord_binding_channel_delete",
                actor=actor,
                before=dict(before) if before is not None else None,
                after=None,
            )
        return deleted

    async def resolve_tenant_for_discord(
        self, *, guild_id: int | None, channel_id: int | None
    ) -> str | None:
        """Resolve tenant using channel override first, then guild default."""
        if channel_id is not None:
            row = await self._fetchrow(
                """
                SELECT tenant_id::text AS tenant_id
                FROM tenant_discord_bindings
                WHERE channel_id = $1
                  AND is_active = TRUE
                ORDER BY priority ASC, updated_at DESC
                LIMIT 1
                """,
                channel_id,
            )
            if row is not None:
                return str(row["tenant_id"])
        if guild_id is not None:
            row = await self._fetchrow(
                """
                SELECT tenant_id::text AS tenant_id
                FROM tenant_discord_bindings
                WHERE guild_id = $1
                  AND channel_id IS NULL
                  AND is_active = TRUE
                ORDER BY priority ASC, updated_at DESC
                LIMIT 1
                """,
                guild_id,
            )
            if row is not None:
                return str(row["tenant_id"])
        return None

    async def list_settings(self, tenant_id: str, namespace: str | None = None) -> dict[str, Any]:
        if namespace is None:
            rows = await self._fetch(
                """
                SELECT namespace, key, value, data_type
                FROM tenant_settings_overrides
                WHERE tenant_id = $1::uuid
                ORDER BY namespace, key
                """,
                tenant_id,
            )
        else:
            rows = await self._fetch(
                """
                SELECT namespace, key, value, data_type
                FROM tenant_settings_overrides
                WHERE tenant_id = $1::uuid
                  AND namespace = $2
                ORDER BY key
                """,
                tenant_id,
                namespace,
            )

        out: dict[str, dict[str, Any]] = {}
        for row in rows:
            ns = str(row["namespace"])
            key = str(row["key"])
            out.setdefault(ns, {})[key] = self._coerce_setting_value(
                str(row["value"]), str(row["data_type"])
            )
        return out

    async def set_setting(
        self,
        *,
        tenant_id: str,
        namespace: str,
        key: str,
        value: Any,
        data_type: str,
        actor: AdminActorContext,
    ) -> None:
        self._validate_setting_key(namespace=namespace, key=key)
        if data_type not in {"string", "int", "float", "bool", "json"}:
            raise ValueError(f"Invalid data_type '{data_type}'")

        before = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, namespace, key, value, data_type,
                   updated_by, updated_at
            FROM tenant_settings_overrides
            WHERE tenant_id = $1::uuid
              AND namespace = $2
              AND key = $3
            """,
            tenant_id,
            namespace,
            key,
        )
        raw_value = json.dumps(value) if data_type == "json" else str(value)
        old_version = await self._fetchval(
            """
            SELECT COALESCE(MAX(version), 0)
            FROM tenant_setting_versions
            WHERE tenant_id = $1::uuid
              AND namespace = $2
              AND key = $3
            """,
            tenant_id,
            namespace,
            key,
        )
        next_version = int(old_version or 0) + 1

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO tenant_settings_overrides (
                    tenant_id,
                    namespace,
                    key,
                    value,
                    data_type,
                    updated_by
                ) VALUES ($1::uuid, $2, $3, $4, $5, $6)
                ON CONFLICT (tenant_id, namespace, key)
                DO UPDATE SET value = EXCLUDED.value,
                              data_type = EXCLUDED.data_type,
                              updated_by = EXCLUDED.updated_by,
                              updated_at = now()
                """,
                tenant_id,
                namespace,
                key,
                raw_value,
                data_type,
                actor.actor_sub,
            )
            await conn.execute(
                """
                INSERT INTO tenant_setting_versions (
                    tenant_id,
                    namespace,
                    key,
                    version,
                    value,
                    data_type,
                    operation,
                    updated_by
                ) VALUES ($1::uuid, $2, $3, $4, $5, $6, 'upsert', $7)
                """,
                tenant_id,
                namespace,
                key,
                next_version,
                raw_value,
                data_type,
                actor.actor_sub,
            )

        self._settings_cache[(tenant_id, namespace, key)] = self._coerce_setting_value(
            raw_value, data_type
        )

        after = {
            "tenant_id": tenant_id,
            "namespace": namespace,
            "key": key,
            "value": raw_value,
            "data_type": data_type,
            "updated_by": actor.actor_sub,
            "version": next_version,
        }
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_setting_upsert",
            actor=actor,
            before=dict(before) if before is not None else None,
            after=after,
        )

    async def delete_setting(
        self,
        *,
        tenant_id: str,
        namespace: str,
        key: str,
        actor: AdminActorContext,
    ) -> bool:
        before = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, namespace, key, value, data_type,
                   updated_by, updated_at
            FROM tenant_settings_overrides
            WHERE tenant_id = $1::uuid
              AND namespace = $2
              AND key = $3
            """,
            tenant_id,
            namespace,
            key,
        )
        if before is None:
            return False

        old_version = await self._fetchval(
            """
            SELECT COALESCE(MAX(version), 0)
            FROM tenant_setting_versions
            WHERE tenant_id = $1::uuid
              AND namespace = $2
              AND key = $3
            """,
            tenant_id,
            namespace,
            key,
        )
        next_version = int(old_version or 0) + 1
        before_value = str(before["value"])
        before_type = str(before["data_type"])

        async with self._pool.acquire() as conn, conn.transaction():
            result = str(
                await conn.execute(
                    """
                DELETE FROM tenant_settings_overrides
                WHERE tenant_id = $1::uuid
                  AND namespace = $2
                  AND key = $3
                """,
                    tenant_id,
                    namespace,
                    key,
                )
            )
            await conn.execute(
                """
                INSERT INTO tenant_setting_versions (
                    tenant_id,
                    namespace,
                    key,
                    version,
                    value,
                    data_type,
                    operation,
                    updated_by
                ) VALUES ($1::uuid, $2, $3, $4, $5, $6, 'delete', $7)
                """,
                tenant_id,
                namespace,
                key,
                next_version,
                before_value,
                before_type,
                actor.actor_sub,
            )

        self._settings_cache.pop((tenant_id, namespace, key), None)
        deleted = result == "DELETE 1"
        if deleted:
            await self._write_audit(
                tenant_id=tenant_id,
                action="tenant_setting_delete",
                actor=actor,
                before=dict(before),
                after=None,
            )
        return deleted

    async def list_secret_metadata(self, tenant_id: str) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            SELECT tenant_id::text AS tenant_id, name, version, updated_by,
                   updated_at, description
            FROM tenant_secrets
            WHERE tenant_id = $1::uuid
            ORDER BY name
            """,
            tenant_id,
        )
        return [dict(row) for row in rows]

    async def set_secret(
        self,
        *,
        tenant_id: str,
        name: str,
        value: str,
        description: str | None,
        actor: AdminActorContext,
    ) -> dict[str, Any]:
        if not value:
            raise ValueError("Secret value must be non-empty")

        before = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, name, version, updated_by,
                   updated_at, description
            FROM tenant_secrets
            WHERE tenant_id = $1::uuid
              AND name = $2
            """,
            tenant_id,
            name,
        )
        old_version = int(before["version"]) if before is not None else 0
        next_version = old_version + 1
        encrypted = self._encrypt(value)

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO tenant_secrets (
                    tenant_id,
                    name,
                    value_enc,
                    version,
                    updated_by,
                    description
                ) VALUES ($1::uuid, $2, $3, $4, $5, $6)
                ON CONFLICT (tenant_id, name)
                DO UPDATE SET value_enc = EXCLUDED.value_enc,
                              version = EXCLUDED.version,
                              updated_by = EXCLUDED.updated_by,
                              description = COALESCE(
                                  EXCLUDED.description,
                                  tenant_secrets.description
                              ),
                              updated_at = now()
                """,
                tenant_id,
                name,
                encrypted,
                next_version,
                actor.actor_sub,
                description,
            )
            await conn.execute(
                """
                INSERT INTO tenant_secret_versions (
                    tenant_id,
                    name,
                    value_enc,
                    version,
                    operation,
                    updated_by,
                    description
                ) VALUES ($1::uuid, $2, $3, $4, 'upsert', $5, $6)
                """,
                tenant_id,
                name,
                encrypted,
                next_version,
                actor.actor_sub,
                description,
            )
            row = await conn.fetchrow(
                """
                SELECT tenant_id::text AS tenant_id, name, version, updated_by,
                       updated_at, description
                FROM tenant_secrets
                WHERE tenant_id = $1::uuid
                  AND name = $2
                """,
                tenant_id,
                name,
            )
        if row is None:
            raise RuntimeError("Failed to store tenant secret")
        self._secrets_cache[(tenant_id, name)] = value
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_secret_upsert",
            actor=actor,
            before=dict(before) if before is not None else None,
            after=after,
        )
        return after

    async def delete_secret(
        self,
        *,
        tenant_id: str,
        name: str,
        actor: AdminActorContext,
    ) -> bool:
        before = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, name, value_enc, version, updated_by,
                   updated_at, description
            FROM tenant_secrets
            WHERE tenant_id = $1::uuid
              AND name = $2
            """,
            tenant_id,
            name,
        )
        if before is None:
            return False

        next_version = int(before["version"]) + 1
        async with self._pool.acquire() as conn, conn.transaction():
            result = str(
                await conn.execute(
                    """
                DELETE FROM tenant_secrets
                WHERE tenant_id = $1::uuid
                  AND name = $2
                """,
                    tenant_id,
                    name,
                )
            )
            await conn.execute(
                """
                INSERT INTO tenant_secret_versions (
                    tenant_id,
                    name,
                    value_enc,
                    version,
                    operation,
                    updated_by,
                    description
                ) VALUES ($1::uuid, $2, $3, $4, 'delete', $5, $6)
                """,
                tenant_id,
                name,
                str(before["value_enc"]),
                next_version,
                actor.actor_sub,
                str(before["description"]) if before["description"] is not None else None,
            )

        self._secrets_cache.pop((tenant_id, name), None)
        deleted = result == "DELETE 1"
        if deleted:
            before_log = dict(before)
            before_log.pop("value_enc", None)
            await self._write_audit(
                tenant_id=tenant_id,
                action="tenant_secret_delete",
                actor=actor,
                before=before_log,
                after=None,
            )
        return deleted

    async def rollback_secret_to_version(
        self,
        *,
        tenant_id: str,
        name: str,
        version: int,
        actor: AdminActorContext,
    ) -> dict[str, Any]:
        version_row = await self._fetchrow(
            """
            SELECT value_enc, description
            FROM tenant_secret_versions
            WHERE tenant_id = $1::uuid
              AND name = $2
              AND version = $3
            """,
            tenant_id,
            name,
            version,
        )
        if version_row is None:
            raise ValueError("Secret version not found")
        value_enc = version_row["value_enc"]
        if value_enc is None:
            raise ValueError("Requested version does not contain secret material")
        before = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, name, version, updated_by, updated_at, description
            FROM tenant_secrets
            WHERE tenant_id = $1::uuid
              AND name = $2
            """,
            tenant_id,
            name,
        )
        old_version = int(before["version"]) if before is not None else 0
        next_version = old_version + 1
        description = (
            str(version_row["description"]) if version_row["description"] is not None else None
        )

        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                """
                INSERT INTO tenant_secrets (
                    tenant_id,
                    name,
                    value_enc,
                    version,
                    updated_by,
                    description
                ) VALUES ($1::uuid, $2, $3, $4, $5, $6)
                ON CONFLICT (tenant_id, name)
                DO UPDATE SET value_enc = EXCLUDED.value_enc,
                              version = EXCLUDED.version,
                              updated_by = EXCLUDED.updated_by,
                              description = COALESCE(
                                  EXCLUDED.description,
                                  tenant_secrets.description
                              ),
                              updated_at = now()
                """,
                tenant_id,
                name,
                str(value_enc),
                next_version,
                actor.actor_sub,
                description,
            )
            await conn.execute(
                """
                INSERT INTO tenant_secret_versions (
                    tenant_id,
                    name,
                    value_enc,
                    version,
                    operation,
                    updated_by,
                    description
                ) VALUES ($1::uuid, $2, $3, $4, 'rollback', $5, $6)
                """,
                tenant_id,
                name,
                str(value_enc),
                next_version,
                actor.actor_sub,
                description,
            )
            row = await conn.fetchrow(
                """
                SELECT tenant_id::text AS tenant_id, name, version, updated_by,
                       updated_at, description
                FROM tenant_secrets
                WHERE tenant_id = $1::uuid
                  AND name = $2
                """,
                tenant_id,
                name,
            )
        if row is None:
            raise RuntimeError("Failed to rollback secret")
        decrypted = self._decrypt(str(value_enc))
        if decrypted is not None:
            self._secrets_cache[(tenant_id, name)] = decrypted

        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_secret_rollback",
            actor=actor,
            before=dict(before) if before is not None else None,
            after=dict(row),
        )
        return dict(row)

    async def list_audit(self, tenant_id: str, limit: int = 200) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            SELECT id, tenant_id::text AS tenant_id, action, actor_sub, actor_roles,
                   actor_email, request_id, change_ticket_id, before_json, after_json, created_at
            FROM tenant_admin_audit_log
            WHERE tenant_id = $1::uuid
            ORDER BY created_at DESC
            LIMIT $2
            """,
            tenant_id,
            limit,
        )
        return [dict(row) for row in rows]

    # ------------------------------------------------------------------
    # Tenant email control-plane domain
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_provider(provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized != "google":
            raise ValueError(f"Unsupported email provider '{provider}'")
        return normalized

    async def _load_tenant_secret_value(self, tenant_id: str, name: str) -> str | None:
        cached = self._secrets_cache.get((tenant_id, name))
        if cached:
            return cached
        row = await self._fetchrow(
            """
            SELECT value_enc
            FROM tenant_secrets
            WHERE tenant_id = $1::uuid
              AND name = $2
            LIMIT 1
            """,
            tenant_id,
            name,
        )
        if row is None:
            return None
        decrypted = self._decrypt(str(row["value_enc"]))
        if decrypted is not None:
            self._secrets_cache[(tenant_id, name)] = decrypted
        return decrypted

    async def _resolve_provider_oauth_credentials(
        self,
        *,
        tenant_id: str,
        provider: str,
    ) -> dict[str, str]:
        provider_norm = self._normalize_provider(provider)
        row = await self._fetchrow(
            """
            SELECT provider, client_id_ref, client_secret_ref, redirect_uri, enabled
            FROM tenant_email_provider_configs
            WHERE tenant_id = $1::uuid
              AND provider = $2
            LIMIT 1
            """,
            tenant_id,
            provider_norm,
        )
        if row is None:
            raise ValueError(f"Email provider '{provider_norm}' is not configured for tenant")
        if not bool(row["enabled"]):
            raise ValueError(f"Email provider '{provider_norm}' is disabled for tenant")

        client_id_ref = str(row["client_id_ref"])
        client_secret_ref = str(row["client_secret_ref"])
        client_id = await self._load_tenant_secret_value(tenant_id, client_id_ref)
        client_secret = await self._load_tenant_secret_value(tenant_id, client_secret_ref)
        redirect_uri = str(row["redirect_uri"]).strip()
        if not client_id:
            raise ValueError(f"Tenant OAuth client id secret '{client_id_ref}' is missing")
        if not client_secret:
            raise ValueError(f"Tenant OAuth client secret '{client_secret_ref}' is missing")
        if not redirect_uri:
            raise ValueError("Tenant OAuth redirect URI is missing")
        return {
            "provider": provider_norm,
            "client_id_ref": client_id_ref,
            "client_secret_ref": client_secret_ref,
            "client_id": client_id,
            "client_secret": client_secret,
            "redirect_uri": redirect_uri,
        }

    async def get_email_provider_config(
        self,
        *,
        tenant_id: str,
        provider: str = "google",
    ) -> dict[str, Any] | None:
        provider_norm = self._normalize_provider(provider)
        row = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id, provider, client_id_ref, client_secret_ref,
                   redirect_uri, enabled, metadata, created_by, updated_by, created_at, updated_at
            FROM tenant_email_provider_configs
            WHERE tenant_id = $1::uuid
              AND provider = $2
            LIMIT 1
            """,
            tenant_id,
            provider_norm,
        )
        if row is None:
            return None
        data = dict(row)
        client_id_ref = str(data["client_id_ref"])
        client_secret_ref = str(data["client_secret_ref"])
        data["has_client_id"] = bool(await self._load_tenant_secret_value(tenant_id, client_id_ref))
        data["has_client_secret"] = bool(
            await self._load_tenant_secret_value(tenant_id, client_secret_ref)
        )
        return data

    async def put_email_provider_config(
        self,
        *,
        tenant_id: str,
        provider: str,
        client_id: str | None,
        client_secret: str | None,
        redirect_uri: str,
        enabled: bool,
        actor: AdminActorContext,
        metadata: dict[str, Any] | None = None,
        client_id_ref: str | None = None,
        client_secret_ref: str | None = None,
    ) -> dict[str, Any]:
        provider_norm = self._normalize_provider(provider)
        before = await self.get_email_provider_config(tenant_id=tenant_id, provider=provider_norm)

        resolved_client_id_ref = (
            client_id_ref.strip()
            if isinstance(client_id_ref, str) and client_id_ref.strip()
            else f"email.{provider_norm}.oauth_client_id"
        )
        resolved_client_secret_ref = (
            client_secret_ref.strip()
            if isinstance(client_secret_ref, str) and client_secret_ref.strip()
            else f"email.{provider_norm}.oauth_client_secret"
        )

        if client_id is not None and client_id.strip():
            await self.set_secret(
                tenant_id=tenant_id,
                name=resolved_client_id_ref,
                value=client_id.strip(),
                description=f"{provider_norm} oauth client id",
                actor=actor,
            )
        if client_secret is not None and client_secret.strip():
            await self.set_secret(
                tenant_id=tenant_id,
                name=resolved_client_secret_ref,
                value=client_secret.strip(),
                description=f"{provider_norm} oauth client secret",
                actor=actor,
            )

        if not await self._load_tenant_secret_value(tenant_id, resolved_client_id_ref):
            raise ValueError("OAuth client_id must be provided on first configuration")
        if not await self._load_tenant_secret_value(tenant_id, resolved_client_secret_ref):
            raise ValueError("OAuth client_secret must be provided on first configuration")

        row = await self._fetchrow(
            """
            INSERT INTO tenant_email_provider_configs (
                tenant_id,
                provider,
                client_id_ref,
                client_secret_ref,
                redirect_uri,
                enabled,
                metadata,
                created_by,
                updated_by
            ) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb, $8, $8)
            ON CONFLICT (tenant_id, provider)
            DO UPDATE SET client_id_ref = EXCLUDED.client_id_ref,
                          client_secret_ref = EXCLUDED.client_secret_ref,
                          redirect_uri = EXCLUDED.redirect_uri,
                          enabled = EXCLUDED.enabled,
                          metadata = EXCLUDED.metadata,
                          updated_by = EXCLUDED.updated_by,
                          updated_at = now()
            RETURNING tenant_id::text AS tenant_id, provider, client_id_ref,
                      client_secret_ref, redirect_uri, enabled, metadata, created_by,
                      updated_by, created_at, updated_at
            """,
            tenant_id,
            provider_norm,
            resolved_client_id_ref,
            resolved_client_secret_ref,
            redirect_uri,
            enabled,
            json.dumps(metadata or {}),
            actor.actor_sub,
        )
        if row is None:
            raise RuntimeError("Failed to store tenant email provider config")

        after = dict(row)
        after["has_client_id"] = True
        after["has_client_secret"] = True
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_email_provider_config_upsert",
            actor=actor,
            before=before,
            after=after,
        )
        return after

    async def create_email_oauth_start(
        self,
        *,
        tenant_id: str,
        provider: str,
        actor: AdminActorContext,
        account_hint: str | None = None,
    ) -> dict[str, Any]:
        from urllib.parse import urlencode

        from zetherion_ai.skills.gmail.auth import DEFAULT_SCOPES, GOOGLE_AUTH_URL

        creds = await self._resolve_provider_oauth_credentials(
            tenant_id=tenant_id,
            provider=provider,
        )
        state = f"email_{uuid4().hex}"
        expires_at = datetime.now(UTC) + self._oauth_state_ttl
        await self._execute(
            """
            INSERT INTO tenant_email_oauth_states (
                state, tenant_id, provider, account_hint, created_by, expires_at
            ) VALUES ($1, $2::uuid, $3, $4, $5, $6)
            """,
            state,
            tenant_id,
            creds["provider"],
            account_hint,
            actor.actor_sub,
            expires_at,
        )
        params = {
            "client_id": creds["client_id"],
            "redirect_uri": creds["redirect_uri"],
            "response_type": "code",
            "scope": " ".join(DEFAULT_SCOPES),
            "access_type": "offline",
            "prompt": "consent",
            "state": state,
        }
        auth_url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
        return {
            "provider": creds["provider"],
            "auth_url": auth_url,
            "state": state,
            "expires_at": expires_at.isoformat(),
        }

    async def consume_email_oauth_state(
        self,
        *,
        tenant_id: str,
        provider: str,
        state: str,
    ) -> dict[str, Any]:
        provider_norm = self._normalize_provider(provider)
        row = await self._fetchrow(
            """
            UPDATE tenant_email_oauth_states
            SET consumed_at = now()
            WHERE state = $1
              AND tenant_id = $2::uuid
              AND provider = $3
              AND consumed_at IS NULL
              AND expires_at > now()
            RETURNING state, tenant_id::text AS tenant_id, provider, account_hint, created_by,
                      created_at, expires_at, consumed_at
            """,
            state,
            tenant_id,
            provider_norm,
        )
        if row is None:
            raise ValueError("Invalid or expired OAuth state")
        return dict(row)

    async def exchange_google_oauth_code(
        self,
        *,
        tenant_id: str,
        code: str,
        state: str,
        actor: AdminActorContext,
    ) -> dict[str, Any]:
        from zetherion_ai.skills.gmail.auth import GmailAuth

        state_row = await self.consume_email_oauth_state(
            tenant_id=tenant_id,
            provider="google",
            state=state,
        )
        creds = await self._resolve_provider_oauth_credentials(
            tenant_id=tenant_id,
            provider="google",
        )
        auth = GmailAuth(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            redirect_uri=creds["redirect_uri"],
        )
        token_data = await auth.exchange_code(code)
        access_token = str(token_data.get("access_token") or "").strip()
        refresh_token = str(token_data.get("refresh_token") or "").strip()
        if not access_token:
            raise ValueError("Google OAuth did not return an access token")

        email_address = await auth.get_user_email(access_token)
        if not email_address:
            raise ValueError("Google OAuth did not return account email")

        existing = await self._fetchrow(
            """
            SELECT account_id::text AS account_id, refresh_token_enc
            FROM tenant_email_accounts
            WHERE tenant_id = $1::uuid
              AND provider = 'google'
              AND email_address = $2
            LIMIT 1
            """,
            tenant_id,
            email_address.lower(),
        )
        if not refresh_token and existing is not None:
            refresh_token = self._decrypt(str(existing["refresh_token_enc"])) or ""
        if not refresh_token:
            raise ValueError("Google OAuth did not return refresh_token")

        expires_in = int(token_data.get("expires_in") or 3600)
        token_expiry = datetime.now(UTC) + timedelta(seconds=expires_in)
        scopes_raw = str(token_data.get("scope") or "").strip()
        scopes = [scope for scope in scopes_raw.split() if scope]

        metadata = {
            "oauth_state": str(state_row["state"]),
            "account_hint": state_row.get("account_hint"),
        }
        row = await self._upsert_email_account(
            tenant_id=tenant_id,
            provider="google",
            email_address=email_address,
            access_token=access_token,
            refresh_token=refresh_token,
            scopes=scopes,
            token_expiry=token_expiry,
            actor=actor,
            external_account_id=email_address.lower(),
            oauth_subject=email_address.lower(),
            status="connected",
            metadata=metadata,
        )
        await self._emit_email_event(
            tenant_id=tenant_id,
            account_id=row["account_id"],
            event_type="email.account.connected",
            payload={
                "provider": "google",
                "email_address": row["email_address"],
            },
        )
        return row

    async def _upsert_email_account(
        self,
        *,
        tenant_id: str,
        provider: str,
        email_address: str,
        access_token: str,
        refresh_token: str,
        scopes: list[str],
        token_expiry: datetime | None,
        actor: AdminActorContext,
        external_account_id: str | None,
        oauth_subject: str | None,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider_norm = self._normalize_provider(provider)
        if status not in VALID_EMAIL_ACCOUNT_STATUSES:
            raise ValueError(f"Invalid account status '{status}'")
        account_id = await self._fetchval(
            """
            SELECT account_id::text
            FROM tenant_email_accounts
            WHERE tenant_id = $1::uuid
              AND provider = $2
              AND email_address = $3
            LIMIT 1
            """,
            tenant_id,
            provider_norm,
            email_address.lower(),
        )
        resolved_account_id = str(account_id) if account_id else str(uuid4())
        before = await self._fetchrow(
            """
            SELECT account_id::text AS account_id, tenant_id::text AS tenant_id, provider,
                   external_account_id, email_address, oauth_subject, status, scopes,
                   token_expiry, sync_cursor, primary_calendar_id, metadata,
                   created_by, updated_by, created_at, updated_at
            FROM tenant_email_accounts
            WHERE account_id = $1::uuid
            """,
            resolved_account_id,
        )

        row = await self._fetchrow(
            """
            INSERT INTO tenant_email_accounts (
                account_id,
                tenant_id,
                provider,
                external_account_id,
                email_address,
                oauth_subject,
                status,
                scopes,
                access_token_enc,
                refresh_token_enc,
                token_expiry,
                metadata,
                created_by,
                updated_by
            ) VALUES (
                $1::uuid,
                $2::uuid,
                $3,
                $4,
                $5,
                $6,
                $7,
                $8,
                $9,
                $10,
                $11,
                $12::jsonb,
                $13,
                $13
            )
            ON CONFLICT (account_id)
            DO UPDATE SET external_account_id = EXCLUDED.external_account_id,
                          email_address = EXCLUDED.email_address,
                          oauth_subject = EXCLUDED.oauth_subject,
                          status = EXCLUDED.status,
                          scopes = EXCLUDED.scopes,
                          access_token_enc = EXCLUDED.access_token_enc,
                          refresh_token_enc = EXCLUDED.refresh_token_enc,
                          token_expiry = EXCLUDED.token_expiry,
                          metadata = EXCLUDED.metadata,
                          updated_by = EXCLUDED.updated_by,
                          updated_at = now()
            RETURNING account_id::text AS account_id, tenant_id::text AS tenant_id, provider,
                      external_account_id, email_address, oauth_subject, status, scopes,
                      token_expiry, sync_cursor, primary_calendar_id, metadata,
                      created_by, updated_by, created_at, updated_at
            """,
            resolved_account_id,
            tenant_id,
            provider_norm,
            external_account_id,
            email_address.lower(),
            oauth_subject,
            status,
            scopes,
            self._encrypt(access_token),
            self._encrypt(refresh_token),
            token_expiry,
            json.dumps(metadata or {}),
            actor.actor_sub,
        )
        if row is None:
            raise RuntimeError("Failed to upsert tenant email account")
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_email_account_upsert",
            actor=actor,
            before=dict(before) if before is not None else None,
            after=after,
        )
        return after

    async def list_email_accounts(
        self,
        *,
        tenant_id: str,
        provider: str = "google",
    ) -> list[dict[str, Any]]:
        provider_norm = self._normalize_provider(provider)
        rows = await self._fetch(
            """
            SELECT account_id::text AS account_id, tenant_id::text AS tenant_id, provider,
                   external_account_id, email_address, oauth_subject, status, scopes,
                   token_expiry, sync_cursor, primary_calendar_id, metadata,
                   created_by, updated_by, created_at, updated_at, last_sync_at
            FROM (
                SELECT a.account_id, a.tenant_id, a.provider, a.external_account_id,
                       a.email_address, a.oauth_subject, a.status, a.scopes,
                       a.token_expiry, a.sync_cursor, a.primary_calendar_id, a.metadata,
                       a.created_by, a.updated_by, a.created_at, a.updated_at,
                       (
                           SELECT MAX(j.completed_at)
                           FROM tenant_email_sync_jobs j
                           WHERE j.account_id = a.account_id
                       ) AS last_sync_at
                FROM tenant_email_accounts a
                WHERE a.tenant_id = $1::uuid
                  AND a.provider = $2
            ) q
            ORDER BY email_address
            """,
            tenant_id,
            provider_norm,
        )
        return [dict(row) for row in rows]

    async def patch_email_account(
        self,
        *,
        tenant_id: str,
        account_id: str,
        actor: AdminActorContext,
        status: str | None = None,
        metadata: dict[str, Any] | None = None,
        sync_cursor: str | None = None,
    ) -> dict[str, Any]:
        before = await self._fetchrow(
            """
            SELECT account_id::text AS account_id, tenant_id::text AS tenant_id, provider,
                   external_account_id, email_address, oauth_subject, status, scopes,
                   token_expiry, sync_cursor, primary_calendar_id, metadata,
                   created_by, updated_by, created_at, updated_at
            FROM tenant_email_accounts
            WHERE tenant_id = $1::uuid
              AND account_id = $2::uuid
            LIMIT 1
            """,
            tenant_id,
            account_id,
        )
        if before is None:
            raise ValueError("Email account not found")
        next_status = str(status or before["status"]).strip().lower()
        if next_status not in VALID_EMAIL_ACCOUNT_STATUSES:
            raise ValueError(f"Invalid account status '{next_status}'")
        merged_metadata = dict(before["metadata"] or {})
        if metadata:
            merged_metadata.update(metadata)
        next_cursor = sync_cursor if sync_cursor is not None else before["sync_cursor"]

        row = await self._fetchrow(
            """
            UPDATE tenant_email_accounts
            SET status = $3,
                metadata = $4::jsonb,
                sync_cursor = $5,
                updated_by = $6,
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND account_id = $2::uuid
            RETURNING account_id::text AS account_id, tenant_id::text AS tenant_id, provider,
                      external_account_id, email_address, oauth_subject, status, scopes,
                      token_expiry, sync_cursor, primary_calendar_id, metadata,
                      created_by, updated_by, created_at, updated_at
            """,
            tenant_id,
            account_id,
            next_status,
            json.dumps(merged_metadata),
            next_cursor,
            actor.actor_sub,
        )
        if row is None:
            raise RuntimeError("Failed to patch email account")
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_email_account_patch",
            actor=actor,
            before=dict(before),
            after=after,
        )
        return after

    async def delete_email_account(
        self,
        *,
        tenant_id: str,
        account_id: str,
        actor: AdminActorContext,
    ) -> bool:
        before = await self._fetchrow(
            """
            SELECT account_id::text AS account_id, tenant_id::text AS tenant_id, provider,
                   email_address, status, metadata, created_at, updated_at
            FROM tenant_email_accounts
            WHERE tenant_id = $1::uuid
              AND account_id = $2::uuid
            LIMIT 1
            """,
            tenant_id,
            account_id,
        )
        if before is None:
            return False
        result = await self._execute(
            """
            DELETE FROM tenant_email_accounts
            WHERE tenant_id = $1::uuid
              AND account_id = $2::uuid
            """,
            tenant_id,
            account_id,
        )
        deleted = result == "DELETE 1"
        if deleted:
            await self._write_audit(
                tenant_id=tenant_id,
                action="tenant_email_account_delete",
                actor=actor,
                before=dict(before),
                after=None,
            )
            await self._emit_email_event(
                tenant_id=tenant_id,
                account_id=account_id,
                event_type="email.account.disconnected",
                payload={
                    "email_address": before.get("email_address"),
                },
            )
        return deleted

    async def _get_email_account_record(
        self,
        *,
        tenant_id: str,
        account_id: str,
    ) -> dict[str, Any]:
        row = await self._fetchrow(
            """
            SELECT account_id::text AS account_id, tenant_id::text AS tenant_id, provider,
                   external_account_id, email_address, oauth_subject, status, scopes,
                   access_token_enc, refresh_token_enc, token_expiry, sync_cursor,
                   primary_calendar_id, metadata, created_by, updated_by, created_at, updated_at
            FROM tenant_email_accounts
            WHERE tenant_id = $1::uuid
              AND account_id = $2::uuid
            LIMIT 1
            """,
            tenant_id,
            account_id,
        )
        if row is None:
            raise ValueError("Email account not found")
        return dict(row)

    async def _refresh_google_access_token_if_needed(
        self,
        *,
        tenant_id: str,
        account_id: str,
    ) -> dict[str, Any]:
        from zetherion_ai.skills.gmail.auth import GmailAuth

        account = await self._get_email_account_record(tenant_id=tenant_id, account_id=account_id)
        access_token = self._decrypt(str(account["access_token_enc"])) or ""
        refresh_token = self._decrypt(str(account["refresh_token_enc"])) or ""
        if not access_token:
            raise ValueError("Account access token is missing")
        expiry = account.get("token_expiry")
        now = datetime.now(UTC)
        if isinstance(expiry, datetime) and expiry > now + timedelta(seconds=90):
            account["access_token"] = access_token
            account["refresh_token"] = refresh_token
            return account
        if not refresh_token:
            raise ValueError("Account refresh token is missing")

        creds = await self._resolve_provider_oauth_credentials(
            tenant_id=tenant_id,
            provider=str(account["provider"]),
        )
        auth = GmailAuth(
            client_id=creds["client_id"],
            client_secret=creds["client_secret"],
            redirect_uri=creds["redirect_uri"],
        )
        refreshed = await auth.refresh_access_token(refresh_token)
        next_access = str(refreshed.get("access_token") or "").strip()
        if not next_access:
            raise ValueError("Google refresh did not return access token")
        next_refresh = str(refreshed.get("refresh_token") or "").strip() or refresh_token
        expires_in = int(refreshed.get("expires_in") or 3600)
        next_expiry = now + timedelta(seconds=expires_in)
        next_scopes_raw = str(refreshed.get("scope") or "").strip()
        next_scopes = (
            [scope for scope in next_scopes_raw.split() if scope]
            if next_scopes_raw
            else list(account.get("scopes") or [])
        )

        await self._execute(
            """
            UPDATE tenant_email_accounts
            SET access_token_enc = $3,
                refresh_token_enc = $4,
                token_expiry = $5,
                scopes = $6,
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND account_id = $2::uuid
            """,
            tenant_id,
            account_id,
            self._encrypt(next_access),
            self._encrypt(next_refresh),
            next_expiry,
            next_scopes,
        )
        account["access_token"] = next_access
        account["refresh_token"] = next_refresh
        account["token_expiry"] = next_expiry
        account["scopes"] = next_scopes
        return account

    async def list_google_calendars(
        self,
        *,
        tenant_id: str,
        account_id: str,
    ) -> list[dict[str, Any]]:
        import httpx

        account = await self._refresh_google_access_token_if_needed(
            tenant_id=tenant_id,
            account_id=account_id,
        )
        access_token = str(account["access_token"])
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(
                "https://www.googleapis.com/calendar/v3/users/me/calendarList",
                headers={"Authorization": f"Bearer {access_token}"},
            )
            response.raise_for_status()
            payload = response.json()
        items = payload.get("items", [])
        if not isinstance(items, list):
            return []
        calendars: list[dict[str, Any]] = []
        for item in items:
            if not isinstance(item, dict):
                continue
            calendars.append(
                {
                    "id": item.get("id"),
                    "summary": item.get("summary"),
                    "primary": bool(item.get("primary")),
                    "time_zone": item.get("timeZone"),
                    "access_role": item.get("accessRole"),
                }
            )
        return calendars

    async def set_email_primary_calendar(
        self,
        *,
        tenant_id: str,
        account_id: str,
        calendar_id: str,
        actor: AdminActorContext,
    ) -> dict[str, Any]:
        before = await self._get_email_account_record(tenant_id=tenant_id, account_id=account_id)
        row = await self._fetchrow(
            """
            UPDATE tenant_email_accounts
            SET primary_calendar_id = $3,
                updated_by = $4,
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND account_id = $2::uuid
            RETURNING account_id::text AS account_id, tenant_id::text AS tenant_id, provider,
                      external_account_id, email_address, oauth_subject, status, scopes,
                      token_expiry, sync_cursor, primary_calendar_id, metadata,
                      created_by, updated_by, created_at, updated_at
            """,
            tenant_id,
            account_id,
            calendar_id,
            actor.actor_sub,
        )
        if row is None:
            raise RuntimeError("Failed to set primary calendar")
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_email_primary_calendar_set",
            actor=actor,
            before={
                "account_id": before["account_id"],
                "primary_calendar_id": before.get("primary_calendar_id"),
            },
            after={
                "account_id": after["account_id"],
                "primary_calendar_id": after.get("primary_calendar_id"),
            },
        )
        return after

    async def _create_sync_job(
        self,
        *,
        tenant_id: str,
        account_id: str,
        direction: str,
        actor: AdminActorContext,
        idempotency_key: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        if direction not in VALID_EMAIL_SYNC_DIRECTIONS:
            raise ValueError(f"Invalid sync direction '{direction}'")
        job_id = str(uuid4())
        await self._execute(
            """
            INSERT INTO tenant_email_sync_jobs (
                job_id,
                tenant_id,
                account_id,
                direction,
                status,
                started_at,
                idempotency_key,
                metadata,
                created_by
            ) VALUES ($1::uuid, $2::uuid, $3::uuid, $4, 'running', now(), $5, $6::jsonb, $7)
            """,
            job_id,
            tenant_id,
            account_id,
            direction,
            idempotency_key,
            json.dumps(metadata or {}),
            actor.actor_sub,
        )
        return job_id

    async def _complete_sync_job(
        self,
        *,
        job_id: str,
        status: str,
        metadata: dict[str, Any] | None = None,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> None:
        if status not in VALID_EMAIL_SYNC_STATUSES:
            raise ValueError(f"Invalid sync status '{status}'")
        await self._execute(
            """
            UPDATE tenant_email_sync_jobs
            SET status = $2,
                completed_at = now(),
                error_code = $3,
                error_detail = $4,
                metadata = COALESCE($5::jsonb, metadata),
                updated_at = now()
            WHERE job_id = $1::uuid
            """,
            job_id,
            status,
            error_code,
            error_detail,
            json.dumps(metadata) if metadata is not None else None,
        )

    async def _emit_email_event(
        self,
        *,
        tenant_id: str,
        account_id: str | None,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        await self._execute(
            """
            INSERT INTO tenant_email_events (tenant_id, account_id, event_type, payload_json)
            VALUES ($1::uuid, $2::uuid, $3, $4::jsonb)
            """,
            tenant_id,
            account_id,
            event_type,
            json.dumps(payload),
        )

    async def _apply_retention_policies(self) -> dict[str, int]:
        body_delete = await self._execute(
            """
            DELETE FROM tenant_email_message_cache
            WHERE created_at < now() - interval '90 days'
            """
        )
        insight_delete = await self._execute(
            """
            DELETE FROM tenant_email_insights
            WHERE created_at < now() - interval '365 days'
            """
        )
        critical_delete = await self._execute(
            """
            DELETE FROM tenant_email_critical_items
            WHERE created_at < now() - interval '365 days'
            """
        )
        return {
            "messages_purged": _rowcount_from_execute(body_delete),
            "insights_purged": _rowcount_from_execute(insight_delete),
            "critical_items_purged": _rowcount_from_execute(critical_delete),
        }

    async def _google_list_unread_messages(
        self,
        *,
        access_token: str,
        max_results: int = 20,
    ) -> list[dict[str, Any]]:
        import httpx

        async with httpx.AsyncClient(timeout=20.0) as client:
            listing = await client.get(
                "https://gmail.googleapis.com/gmail/v1/users/me/messages",
                params={"q": "is:unread", "maxResults": max_results},
                headers={"Authorization": f"Bearer {access_token}"},
            )
            listing.raise_for_status()
            listing_payload = listing.json()
            messages = listing_payload.get("messages", [])
            if not isinstance(messages, list):
                return []

            async def fetch_detail(message_id: str) -> dict[str, Any] | None:
                detail = await client.get(
                    f"https://gmail.googleapis.com/gmail/v1/users/me/messages/{message_id}",
                    params={
                        "format": "metadata",
                        "metadataHeaders": ["Subject", "From", "Date"],
                    },
                    headers={"Authorization": f"Bearer {access_token}"},
                )
                if detail.status_code >= 400:
                    return None
                payload = detail.json()
                if not isinstance(payload, dict):
                    return None
                return payload

            detail_payloads = await asyncio.gather(
                *[
                    fetch_detail(str(msg.get("id")))
                    for msg in messages
                    if isinstance(msg, dict) and msg.get("id")
                ]
            )
        normalized: list[dict[str, Any]] = []
        for payload in detail_payloads:
            if not isinstance(payload, dict):
                continue
            headers = payload.get("payload", {}).get("headers", [])
            subject = ""
            from_email = ""
            date_header = ""
            if isinstance(headers, list):
                for header in headers:
                    if not isinstance(header, dict):
                        continue
                    name = str(header.get("name") or "").strip().lower()
                    value = str(header.get("value") or "")
                    if name == "subject":
                        subject = value
                    elif name == "from":
                        from_email = value
                    elif name == "date":
                        date_header = value
            internal_date = payload.get("internalDate")
            received_at: str | None = None
            if isinstance(internal_date, str) and internal_date.isdigit():
                received_at = datetime.fromtimestamp(int(internal_date) / 1000, tz=UTC).isoformat()
            normalized.append(
                {
                    "message_id": str(payload.get("id") or ""),
                    "thread_id": str(payload.get("threadId") or ""),
                    "subject": subject,
                    "from_email": from_email,
                    "body_preview": str(payload.get("snippet") or ""),
                    "received_at": received_at,
                    "date_header": date_header,
                }
            )
        return [row for row in normalized if row["message_id"]]

    async def _criticality_score(
        self,
        *,
        subject: str,
        body_preview: str,
        sender: str,
    ) -> tuple[str, float, list[str], dict[str, Any]]:
        haystack = f"{subject}\n{body_preview}".lower()
        reasons: list[str] = []
        score = 0.05

        urgent_keywords = {
            "urgent",
            "asap",
            "immediately",
            "critical",
            "emergency",
            "outage",
            "incident",
            "breach",
            "deadline",
            "today",
        }
        finance_keywords = {"invoice", "payment", "billing", "overdue"}
        if any(term in haystack for term in urgent_keywords):
            score += 0.45
            reasons.append("urgent_keywords")
        if any(term in haystack for term in finance_keywords):
            score += 0.2
            reasons.append("finance_keywords")
        if any(token in haystack for token in ("security", "password", "mfa", "fraud")):
            score += 0.2
            reasons.append("security_keywords")
        sender_lower = sender.lower()
        if sender_lower.endswith("@google.com") or sender_lower.endswith("@github.com"):
            score += 0.1
            reasons.append("trusted_sender")

        if self._critical_scorer is not None:
            try:
                model_signal = await self._critical_scorer(subject, body_preview)
            except Exception:
                model_signal = None
            if isinstance(model_signal, dict):
                model_score_raw = model_signal.get("score")
                model_score: float | None = None
                if isinstance(model_score_raw, int | float):
                    model_score = max(0.0, min(1.0, float(model_score_raw)))
                elif isinstance(model_score_raw, str):
                    try:
                        model_score = max(0.0, min(1.0, float(model_score_raw)))
                    except ValueError:
                        model_score = None
                if model_score is not None:
                    score = (score * 0.6) + (model_score * 0.4)
                    reasons.append("model_score")
                model_reasons = model_signal.get("reason_codes")
                if isinstance(model_reasons, list):
                    for code in model_reasons:
                        code_str = str(code).strip()
                        if code_str and code_str not in reasons:
                            reasons.append(code_str)

        score = max(0.0, min(1.0, score))
        if score >= 0.8:
            severity = "critical"
        elif score >= 0.55:
            severity = "high"
        else:
            severity = "normal"

        email_pattern = r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
        entities = {
            "emails": sorted(set(re.findall(email_pattern, haystack))),
            "has_deadline_hint": any(
                term in haystack for term in ("today", "tomorrow", "deadline")
            ),
        }
        return severity, score, sorted(set(reasons)), entities

    async def _store_insight_vector(
        self,
        *,
        tenant_id: str,
        account_id: str,
        insight_type: str,
        summary: str,
        metadata: dict[str, Any],
    ) -> str | None:
        if self._vector_memory is None:
            return None
        try:
            return await self._vector_memory.store_memory(
                summary,
                memory_type="tenant_email_insight",
                metadata={
                    "tenant_id": tenant_id,
                    "account_id": account_id,
                    "insight_type": insight_type,
                    **metadata,
                },
            )
        except Exception:
            log.exception("tenant_email_insight_vector_store_failed")
            return None

    async def sync_email_account(
        self,
        *,
        tenant_id: str,
        account_id: str,
        actor: AdminActorContext,
        direction: str = "bi_directional",
        idempotency_key: str | None = None,
        source: str = "cgs-admin",
        calendar_operations: list[dict[str, Any]] | None = None,
        max_results: int = 20,
    ) -> dict[str, Any]:
        direction_norm = direction.strip().lower()
        if direction_norm not in VALID_EMAIL_SYNC_DIRECTIONS:
            raise ValueError(f"Invalid sync direction '{direction}'")

        account = await self._refresh_google_access_token_if_needed(
            tenant_id=tenant_id,
            account_id=account_id,
        )
        job_id = await self._create_sync_job(
            tenant_id=tenant_id,
            account_id=account_id,
            direction=direction_norm,
            actor=actor,
            idempotency_key=idempotency_key,
            metadata={"source": source},
        )

        counts = {
            "messages_scanned": 0,
            "critical_created": 0,
            "insights_created": 0,
            "calendar_reads": 0,
            "calendar_writes": 0,
        }
        try:
            retention_stats = await self._apply_retention_policies()
            access_token = str(account["access_token"])

            if direction_norm in {"email", "bi_directional"}:
                unread = await self._google_list_unread_messages(
                    access_token=access_token,
                    max_results=max(1, min(max_results, 100)),
                )
                counts["messages_scanned"] = len(unread)
                for message in unread:
                    received_at: datetime | None = None
                    received_raw = message.get("received_at")
                    if isinstance(received_raw, str) and received_raw.strip():
                        try:
                            received_at = datetime.fromisoformat(
                                received_raw.replace("Z", "+00:00")
                            )
                        except ValueError:
                            received_at = None

                    await self._execute(
                        """
                        INSERT INTO tenant_email_message_cache (
                            tenant_id, account_id, message_id, subject, from_email,
                            body_preview, received_at, metadata
                        ) VALUES ($1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8::jsonb)
                        ON CONFLICT (tenant_id, account_id, message_id)
                        DO UPDATE SET subject = EXCLUDED.subject,
                                      from_email = EXCLUDED.from_email,
                                      body_preview = EXCLUDED.body_preview,
                                      received_at = EXCLUDED.received_at,
                                      metadata = EXCLUDED.metadata
                        """,
                        tenant_id,
                        account_id,
                        message["message_id"],
                        message.get("subject"),
                        message.get("from_email"),
                        message.get("body_preview"),
                        received_at,
                        json.dumps(message),
                    )

                    severity, score, reasons, entities = await self._criticality_score(
                        subject=str(message.get("subject") or ""),
                        body_preview=str(message.get("body_preview") or ""),
                        sender=str(message.get("from_email") or ""),
                    )
                    if severity in {"critical", "high"}:
                        critical_id = await self._fetchval(
                            """
                            SELECT item_id::text
                            FROM tenant_email_critical_items
                            WHERE tenant_id = $1::uuid
                              AND account_id = $2::uuid
                              AND message_id = $3
                            LIMIT 1
                            """,
                            tenant_id,
                            account_id,
                            message["message_id"],
                        )
                        item_id = str(critical_id) if critical_id else str(uuid4())
                        await self._execute(
                            """
                            INSERT INTO tenant_email_critical_items (
                                item_id, tenant_id, account_id, message_id, severity, score,
                                reason_codes, entities_json, status, source_json
                            ) VALUES (
                                $1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7,
                                $8::jsonb, 'open', $9::jsonb
                            )
                            ON CONFLICT (tenant_id, account_id, message_id)
                            DO UPDATE SET severity = EXCLUDED.severity,
                                          score = EXCLUDED.score,
                                          reason_codes = EXCLUDED.reason_codes,
                                          entities_json = EXCLUDED.entities_json,
                                          source_json = EXCLUDED.source_json,
                                          updated_at = now()
                            """,
                            item_id,
                            tenant_id,
                            account_id,
                            message["message_id"],
                            severity,
                            score,
                            reasons,
                            json.dumps(entities),
                            json.dumps(message),
                        )
                        counts["critical_created"] += 1
                        await self._emit_email_event(
                            tenant_id=tenant_id,
                            account_id=account_id,
                            event_type="email.critical.detected",
                            payload={
                                "message_id": message["message_id"],
                                "severity": severity,
                                "score": score,
                                "reason_codes": reasons,
                            },
                        )

                        insight_payload = {
                            "summary": (
                                f"{severity.upper()}: "
                                f"{message.get('subject') or '(no subject)'}"
                            ),
                            "from_email": message.get("from_email"),
                            "reason_codes": reasons,
                            "entities": entities,
                            "message_id": message["message_id"],
                        }
                        vector_id = await self._store_insight_vector(
                            tenant_id=tenant_id,
                            account_id=account_id,
                            insight_type="critical_email",
                            summary=str(insight_payload["summary"]),
                            metadata={
                                "message_id": message["message_id"],
                                "severity": severity,
                            },
                        )
                        await self._execute(
                            """
                            INSERT INTO tenant_email_insights (
                                insight_id, tenant_id, account_id, insight_type, confidence,
                                payload_json, source_message_ids, vector_id
                            ) VALUES (
                                $1::uuid, $2::uuid, $3::uuid, $4, $5, $6::jsonb, $7, $8
                            )
                            """,
                            str(uuid4()),
                            tenant_id,
                            account_id,
                            "critical_email",
                            float(score),
                            json.dumps(insight_payload),
                            [message["message_id"]],
                            vector_id,
                        )
                        counts["insights_created"] += 1
                        await self._emit_email_event(
                            tenant_id=tenant_id,
                            account_id=account_id,
                            event_type="email.insight.extracted",
                            payload={
                                "message_id": message["message_id"],
                                "insight_type": "critical_email",
                                "vector_id": vector_id,
                            },
                        )

            calendars: list[dict[str, Any]] = []
            if direction_norm in {"calendar_read", "bi_directional"}:
                calendars = await self.list_google_calendars(
                    tenant_id=tenant_id,
                    account_id=account_id,
                )
                counts["calendar_reads"] = len(calendars)
                await self._emit_email_event(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    event_type="calendar.sync.updated",
                    payload={"direction": "read", "calendar_count": len(calendars)},
                )

            if direction_norm in {"calendar_write", "bi_directional"}:
                counts["calendar_writes"] = await self._apply_calendar_operations(
                    tenant_id=tenant_id,
                    account_id=account_id,
                    access_token=access_token,
                    source=source,
                    operations=calendar_operations or [],
                )
                if counts["calendar_writes"] > 0:
                    await self._emit_email_event(
                        tenant_id=tenant_id,
                        account_id=account_id,
                        event_type="calendar.sync.updated",
                        payload={
                            "direction": "write",
                            "operation_count": counts["calendar_writes"],
                        },
                    )

            await self._execute(
                """
                UPDATE tenant_email_accounts
                SET status = 'connected',
                    updated_by = $3,
                    updated_at = now()
                WHERE tenant_id = $1::uuid
                  AND account_id = $2::uuid
                """,
                tenant_id,
                account_id,
                actor.actor_sub,
            )
            await self._complete_sync_job(
                job_id=job_id,
                status="succeeded",
                metadata={
                    "counts": counts,
                    "retention": retention_stats,
                    "source": source,
                },
            )
            return {
                "job_id": job_id,
                "status": "succeeded",
                "counts": counts,
                "retention": retention_stats,
            }
        except Exception as exc:
            await self._execute(
                """
                UPDATE tenant_email_accounts
                SET status = 'degraded',
                    updated_by = $3,
                    updated_at = now()
                WHERE tenant_id = $1::uuid
                  AND account_id = $2::uuid
                """,
                tenant_id,
                account_id,
                actor.actor_sub,
            )
            await self._complete_sync_job(
                job_id=job_id,
                status="failed",
                error_code="SYNC_FAILED",
                error_detail=str(exc)[:500],
                metadata={"counts": counts, "source": source},
            )
            raise

    async def _apply_calendar_operations(
        self,
        *,
        tenant_id: str,
        account_id: str,
        access_token: str,
        source: str,
        operations: list[dict[str, Any]],
    ) -> int:
        import httpx

        write_count = 0
        if not operations:
            return write_count

        async with httpx.AsyncClient(timeout=20.0) as client:
            for operation in operations:
                if not isinstance(operation, dict):
                    continue
                action = str(operation.get("action") or "").strip().lower()
                if action == "delete":
                    raise ValueError("Calendar delete operations are disabled by policy")
                idempotency_key = str(operation.get("idempotency_key") or "").strip()
                if not idempotency_key:
                    raise ValueError("Calendar write operation requires idempotency_key")
                op_source = str(operation.get("source") or source).strip()
                if not op_source:
                    raise ValueError("Calendar write operation requires source")
                calendar_id = str(operation.get("calendar_id") or "").strip()
                if not calendar_id:
                    raise ValueError("Calendar write operation requires calendar_id")
                event_payload = operation.get("event")
                if not isinstance(event_payload, dict):
                    raise ValueError("Calendar write operation requires event object")

                event_id = str(operation.get("event_id") or "").strip()
                if action == "create":
                    url = f"https://www.googleapis.com/calendar/v3/calendars/{calendar_id}/events"
                    response = await client.post(
                        url,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "X-Idempotency-Key": idempotency_key,
                            "Content-Type": "application/json",
                        },
                        json=event_payload,
                    )
                elif action == "update":
                    if not event_id:
                        raise ValueError("Calendar update operation requires event_id")
                    url = (
                        "https://www.googleapis.com/calendar/v3/calendars/"
                        f"{calendar_id}/events/{event_id}"
                    )
                    response = await client.patch(
                        url,
                        headers={
                            "Authorization": f"Bearer {access_token}",
                            "X-Idempotency-Key": idempotency_key,
                            "Content-Type": "application/json",
                        },
                        json=event_payload,
                    )
                else:
                    raise ValueError(f"Unsupported calendar operation action '{action}'")

                response.raise_for_status()
                write_count += 1

        await self._emit_email_event(
            tenant_id=tenant_id,
            account_id=account_id,
            event_type="calendar.write.applied",
            payload={"count": write_count, "source": source},
        )
        return write_count

    async def list_email_critical_items(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        severity: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        filters: list[str] = ["tenant_id = $1::uuid"]
        args: list[Any] = [tenant_id]
        idx = 2
        if status:
            status_norm = status.strip().lower()
            if status_norm not in VALID_CRITICAL_STATUSES:
                raise ValueError(f"Invalid critical status '{status}'")
            filters.append(f"status = ${idx}")
            args.append(status_norm)
            idx += 1
        if severity:
            severity_norm = severity.strip().lower()
            if severity_norm not in VALID_CRITICAL_SEVERITIES:
                raise ValueError(f"Invalid critical severity '{severity}'")
            filters.append(f"severity = ${idx}")
            args.append(severity_norm)
            idx += 1
        args.append(max(1, min(limit, 500)))
        rows = await self._fetch(
            f"""
            SELECT item_id::text AS item_id,
                   tenant_id::text AS tenant_id,
                   account_id::text AS account_id,
                   message_id,
                   severity,
                   score,
                   reason_codes,
                   entities_json,
                   action_required_by,
                   status,
                   source_json,
                   created_at,
                   updated_at
            FROM tenant_email_critical_items
            WHERE {" AND ".join(filters)}
            ORDER BY created_at DESC
            LIMIT ${idx}
            """,  # nosec B608
            *args,
        )
        return [dict(row) for row in rows]

    async def list_email_insights(
        self,
        *,
        tenant_id: str,
        insight_type: str | None = None,
        min_confidence: float | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        filters: list[str] = ["tenant_id = $1::uuid"]
        args: list[Any] = [tenant_id]
        idx = 2
        if insight_type:
            filters.append(f"insight_type = ${idx}")
            args.append(insight_type.strip())
            idx += 1
        if min_confidence is not None:
            filters.append(f"confidence >= ${idx}")
            args.append(float(min_confidence))
            idx += 1
        args.append(max(1, min(limit, 500)))
        rows = await self._fetch(
            f"""
            SELECT insight_id::text AS insight_id,
                   tenant_id::text AS tenant_id,
                   account_id::text AS account_id,
                   insight_type,
                   confidence,
                   payload_json,
                   source_message_ids,
                   vector_id,
                   created_at
            FROM tenant_email_insights
            WHERE {" AND ".join(filters)}
            ORDER BY created_at DESC
            LIMIT ${idx}
            """,  # nosec B608
            *args,
        )
        return [dict(row) for row in rows]

    async def reindex_email_insights(
        self,
        *,
        tenant_id: str,
        actor: AdminActorContext,
        insight_type: str | None = None,
    ) -> dict[str, Any]:
        rows = await self.list_email_insights(
            tenant_id=tenant_id,
            insight_type=insight_type,
            min_confidence=None,
            limit=500,
        )
        if self._vector_memory is None:
            return {"reindexed": 0, "skipped": len(rows), "reason": "vector_memory_unavailable"}
        reindexed = 0
        for row in rows:
            payload = row.get("payload_json")
            if not isinstance(payload, dict):
                continue
            summary = str(payload.get("summary") or "").strip()
            if not summary:
                summary = json.dumps(payload, sort_keys=True)[:500]
            vector_id = await self._store_insight_vector(
                tenant_id=tenant_id,
                account_id=str(row.get("account_id") or ""),
                insight_type=str(row.get("insight_type") or "email"),
                summary=summary,
                metadata={"insight_id": row["insight_id"]},
            )
            if not vector_id:
                continue
            await self._execute(
                """
                UPDATE tenant_email_insights
                SET vector_id = $2
                WHERE insight_id = $1::uuid
                """,
                row["insight_id"],
                vector_id,
            )
            reindexed += 1
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_email_insights_reindex",
            actor=actor,
            before=None,
            after={"reindexed": reindexed, "scanned": len(rows)},
        )
        return {"reindexed": reindexed, "scanned": len(rows)}

    async def list_email_events(
        self,
        *,
        tenant_id: str,
        event_type: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        if event_type:
            rows = await self._fetch(
                """
                SELECT event_id, tenant_id::text AS tenant_id, account_id::text AS account_id,
                       event_type, payload_json, created_at
                FROM tenant_email_events
                WHERE tenant_id = $1::uuid
                  AND event_type = $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                tenant_id,
                event_type,
                max(1, min(limit, 500)),
            )
        else:
            rows = await self._fetch(
                """
                SELECT event_id, tenant_id::text AS tenant_id, account_id::text AS account_id,
                       event_type, payload_json, created_at
                FROM tenant_email_events
                WHERE tenant_id = $1::uuid
                ORDER BY created_at DESC
                LIMIT $2
                """,
                tenant_id,
                max(1, min(limit, 500)),
            )
        return [dict(row) for row in rows]

    async def _write_audit(
        self,
        *,
        tenant_id: str,
        action: str,
        actor: AdminActorContext,
        before: dict[str, Any] | None,
        after: dict[str, Any] | None,
    ) -> None:
        await self._execute(
            """
            INSERT INTO tenant_admin_audit_log (
                tenant_id,
                action,
                actor_sub,
                actor_roles,
                actor_email,
                request_id,
                change_ticket_id,
                before_json,
                after_json
            ) VALUES (
                $1::uuid,
                $2,
                $3,
                $4::jsonb,
                $5,
                $6,
                $7,
                $8::jsonb,
                $9::jsonb
            )
            """,
            tenant_id,
            action,
            actor.actor_sub,
            json.dumps(list(actor.actor_roles)),
            actor.actor_email,
            actor.request_id,
            actor.change_ticket_id,
            json.dumps(before) if before is not None else None,
            json.dumps(after) if after is not None else None,
        )

    def _validate_setting_key(self, *, namespace: str, key: str) -> None:
        if namespace not in VALID_NAMESPACES:
            raise ValueError(f"Invalid namespace '{namespace}'")
        allowed = self._setting_key_allowlist.get(namespace)
        if allowed is not None and key not in allowed:
            raise ValueError(f"Setting '{namespace}.{key}' is not mutable")

    @staticmethod
    def _coerce_setting_value(value: str, data_type: str) -> Any:
        if data_type == "int":
            return int(value)
        if data_type == "float":
            return float(value)
        if data_type == "bool":
            return str(value).strip().lower() in {"1", "true", "yes", "on"}
        if data_type == "json":
            return json.loads(value)
        return value

    def _encrypt(self, plaintext: str) -> str:
        if self._encryptor is None:
            return plaintext
        return self._encryptor.encrypt_value(plaintext)

    def _decrypt(self, ciphertext: str) -> str | None:
        if self._encryptor is None:
            return ciphertext
        try:
            return self._encryptor.decrypt_value(ciphertext)
        except Exception:
            log.warning("tenant_secret_decrypt_failed")
            return None

    async def _fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def _fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return list(rows)

    async def _fetchval(self, query: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def _execute(self, query: str, *args: Any) -> str:
        async with self._pool.acquire() as conn:
            return str(await conn.execute(query, *args))


def _rowcount_from_execute(result: str) -> int:
    """Parse PostgreSQL execute result string (for example: 'DELETE 3')."""
    parts = result.strip().split()
    if not parts:
        return 0
    try:
        return int(parts[-1])
    except ValueError:
        return 0


def admin_actor_from_payload(payload: dict[str, Any]) -> AdminActorContext:
    """Create validated admin actor context from envelope payload."""
    actor_sub = str(payload.get("actor_sub") or "").strip()
    if not actor_sub:
        raise ValueError("Missing actor_sub")
    request_id = str(payload.get("request_id") or "").strip()
    if not request_id:
        raise ValueError("Missing request_id")
    nonce = str(payload.get("nonce") or "").strip()
    if not nonce:
        raise ValueError("Missing nonce")
    timestamp_raw = payload.get("timestamp")
    if not isinstance(timestamp_raw, str) or not timestamp_raw.strip():
        raise ValueError("Missing timestamp")
    try:
        timestamp = datetime.fromisoformat(timestamp_raw.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid timestamp") from exc
    if timestamp.tzinfo is None:
        timestamp = timestamp.replace(tzinfo=UTC)
    roles_raw = payload.get("actor_roles", [])
    if isinstance(roles_raw, list):
        actor_roles = tuple(str(role) for role in roles_raw if str(role).strip())
    else:
        actor_roles = ()
    actor_email = payload.get("actor_email")
    email_value = str(actor_email) if actor_email is not None else None
    change_ticket = payload.get("change_ticket_id")
    change_ticket_id = str(change_ticket) if change_ticket is not None else None
    return AdminActorContext(
        actor_sub=actor_sub,
        actor_roles=actor_roles,
        request_id=request_id,
        timestamp=timestamp,
        nonce=nonce,
        actor_email=email_value,
        change_ticket_id=change_ticket_id,
    )
