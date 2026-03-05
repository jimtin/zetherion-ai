"""Tenant-scoped admin manager for Discord access, settings, secrets, and audit."""

from __future__ import annotations

import asyncio
import json
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

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
VALID_MESSAGING_PROVIDERS = frozenset({"whatsapp"})
VALID_MESSAGING_DIRECTIONS = frozenset({"inbound", "outbound", "system"})
VALID_MESSAGING_ACTION_TYPES = frozenset({"send"})
VALID_MESSAGING_QUEUE_STATUSES = frozenset(
    {"queued", "processing", "sent", "failed", "blocked", "cancelled"}
)
DEFAULT_MESSAGING_RETENTION_DAYS = 14
VALID_EXECUTION_PLAN_STATUSES = frozenset(
    {"queued", "running", "paused", "completed", "failed", "cancelled"}
)
VALID_EXECUTION_STEP_STATUSES = frozenset(
    {"pending", "running", "completed", "failed", "blocked", "cancelled"}
)
VALID_EXECUTION_RETRY_OUTCOMES = frozenset(
    {"succeeded", "retryable_failed", "terminal_failed", "cancelled", "interrupted"}
)
RETRYABLE_EXECUTION_FAILURE_CATEGORIES = frozenset(
    {"timeout", "transient", "dependency", "rate_limit", "interrupted"}
)
DEFAULT_EXECUTION_MAX_STEP_ATTEMPTS = 3
DEFAULT_EXECUTION_CONTINUATION_INTERVAL_SECONDS = 60
DEFAULT_EXECUTION_LEASE_SECONDS = 90
DEFAULT_EXECUTION_STALE_STEP_SECONDS = 300
EXECUTION_RETRY_BACKOFF_SECONDS = (60, 300, 900, 1800)

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

CREATE TABLE IF NOT EXISTS tenant_messaging_provider_configs (
    tenant_id            UUID         NOT NULL,
    provider             VARCHAR(40)  NOT NULL,
    enabled              BOOLEAN      NOT NULL DEFAULT TRUE,
    bridge_mode          VARCHAR(30)  NOT NULL DEFAULT 'local_sidecar'
                         CHECK (bridge_mode IN ('local_sidecar', 'cloud_bridge')),
    account_ref          TEXT,
    session_ref          TEXT,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_by           TEXT,
    updated_by           TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider)
);

CREATE INDEX IF NOT EXISTS idx_tenant_messaging_provider_configs_lookup
    ON tenant_messaging_provider_configs (tenant_id, provider);

CREATE TABLE IF NOT EXISTS tenant_messaging_accounts (
    account_id           UUID         PRIMARY KEY,
    tenant_id            UUID         NOT NULL,
    provider             VARCHAR(40)  NOT NULL,
    external_account_id  TEXT         NOT NULL,
    session_id           TEXT,
    status               VARCHAR(20)  NOT NULL DEFAULT 'active'
                         CHECK (status IN ('active', 'paused', 'revoked', 'disconnected')),
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_by           TEXT,
    updated_by           TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider, external_account_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_messaging_accounts_lookup
    ON tenant_messaging_accounts (tenant_id, provider, status, updated_at DESC);

CREATE TABLE IF NOT EXISTS tenant_messaging_chat_policies (
    tenant_id            UUID         NOT NULL,
    provider             VARCHAR(40)  NOT NULL,
    chat_id              TEXT         NOT NULL,
    read_enabled         BOOLEAN      NOT NULL DEFAULT FALSE,
    send_enabled         BOOLEAN      NOT NULL DEFAULT FALSE,
    retention_days       INT          NOT NULL DEFAULT 14
                         CHECK (retention_days > 0 AND retention_days <= 365),
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_by           TEXT,
    updated_by           TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (tenant_id, provider, chat_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_messaging_chat_policies_lookup
    ON tenant_messaging_chat_policies (
        tenant_id,
        provider,
        read_enabled,
        send_enabled,
        updated_at DESC
    );

CREATE TABLE IF NOT EXISTS tenant_messaging_messages (
    message_pk           BIGSERIAL    PRIMARY KEY,
    message_id           UUID         NOT NULL,
    tenant_id            UUID         NOT NULL,
    provider             VARCHAR(40)  NOT NULL,
    chat_id              TEXT         NOT NULL,
    direction            VARCHAR(20)  NOT NULL
                         CHECK (direction IN ('inbound', 'outbound', 'system')),
    sender_id            TEXT,
    sender_name          TEXT,
    body_enc             TEXT         NOT NULL,
    metadata             JSONB        NOT NULL DEFAULT '{}'::jsonb,
    action_id            UUID,
    event_type           TEXT,
    observed_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    expires_at           TIMESTAMPTZ  NOT NULL,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, provider, message_id)
);

CREATE INDEX IF NOT EXISTS idx_tenant_messaging_messages_lookup
    ON tenant_messaging_messages (tenant_id, provider, chat_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_tenant_messaging_messages_expiry
    ON tenant_messaging_messages (expires_at);

CREATE TABLE IF NOT EXISTS tenant_messaging_action_queue (
    action_id            UUID         PRIMARY KEY,
    tenant_id            UUID         NOT NULL,
    provider             VARCHAR(40)  NOT NULL,
    chat_id              TEXT         NOT NULL,
    action_type          VARCHAR(20)  NOT NULL
                         CHECK (action_type IN ('send')),
    payload_enc          TEXT,
    payload_json         JSONB        NOT NULL DEFAULT '{}'::jsonb,
    status               VARCHAR(20)  NOT NULL DEFAULT 'queued'
                         CHECK (
                             status IN (
                                 'queued',
                                 'processing',
                                 'sent',
                                 'failed',
                                 'blocked',
                                 'cancelled'
                             )
                         ),
    created_by           TEXT,
    request_id           TEXT,
    change_ticket_id     TEXT,
    error_code           TEXT,
    error_detail         TEXT,
    created_at           TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_messaging_action_queue_lookup
    ON tenant_messaging_action_queue (tenant_id, provider, chat_id, status, created_at DESC);

CREATE TABLE IF NOT EXISTS tenant_execution_plans (
    plan_id                        UUID         PRIMARY KEY,
    tenant_id                      UUID         NOT NULL,
    title                          TEXT         NOT NULL,
    goal                           TEXT         NOT NULL,
    status                         VARCHAR(20)  NOT NULL DEFAULT 'queued'
                                   CHECK (
                                       status IN (
                                           'queued',
                                           'running',
                                           'paused',
                                           'completed',
                                           'failed',
                                           'cancelled'
                                       )
                                   ),
    current_step_index             INT          NOT NULL DEFAULT 0,
    total_steps                    INT          NOT NULL DEFAULT 0,
    max_step_attempts              INT          NOT NULL DEFAULT 3,
    continuation_interval_seconds  INT          NOT NULL DEFAULT 60,
    next_run_at                    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    lease_owner                    TEXT,
    lease_expires_at               TIMESTAMPTZ,
    metadata                       JSONB        NOT NULL DEFAULT '{}'::jsonb,
    last_error_category            TEXT,
    last_error_detail              TEXT,
    created_by                     TEXT,
    updated_by                     TEXT,
    created_at                     TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at                     TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_execution_plans_lookup
    ON tenant_execution_plans (tenant_id, status, next_run_at, updated_at DESC);

CREATE TABLE IF NOT EXISTS tenant_execution_steps (
    step_id               UUID         PRIMARY KEY,
    plan_id               UUID         NOT NULL
                         REFERENCES tenant_execution_plans(plan_id) ON DELETE CASCADE,
    tenant_id             UUID         NOT NULL,
    step_index            INT          NOT NULL,
    title                 TEXT         NOT NULL,
    prompt_text           TEXT         NOT NULL,
    idempotency_key       TEXT         NOT NULL,
    status                VARCHAR(20)  NOT NULL DEFAULT 'pending'
                         CHECK (
                             status IN (
                                 'pending',
                                 'running',
                                 'completed',
                                 'failed',
                                 'blocked',
                                 'cancelled'
                             )
                         ),
    attempt_count         INT          NOT NULL DEFAULT 0,
    max_attempts          INT          NOT NULL DEFAULT 3,
    next_retry_at         TIMESTAMPTZ,
    last_error_category   TEXT,
    last_error_detail     TEXT,
    output_json           JSONB        NOT NULL DEFAULT '{}'::jsonb,
    metadata              JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (plan_id, step_index),
    UNIQUE (plan_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_tenant_execution_steps_lookup
    ON tenant_execution_steps (tenant_id, plan_id, status, step_index);
CREATE INDEX IF NOT EXISTS idx_tenant_execution_steps_retry
    ON tenant_execution_steps (tenant_id, plan_id, next_retry_at)
    WHERE status = 'failed';

CREATE TABLE IF NOT EXISTS tenant_execution_step_retries (
    retry_id               UUID         PRIMARY KEY,
    tenant_id              UUID         NOT NULL,
    plan_id                UUID         NOT NULL
                          REFERENCES tenant_execution_plans(plan_id) ON DELETE CASCADE,
    step_id                UUID         NOT NULL
                          REFERENCES tenant_execution_steps(step_id) ON DELETE CASCADE,
    attempt_number         INT          NOT NULL,
    worker_id              TEXT,
    lease_token            TEXT,
    outcome                VARCHAR(30)
                          CHECK (
                              outcome IS NULL OR outcome IN (
                                  'succeeded',
                                  'retryable_failed',
                                  'terminal_failed',
                                  'cancelled',
                                  'interrupted'
                              )
                          ),
    failure_category       TEXT,
    failure_detail         TEXT,
    retry_backoff_seconds  INT,
    started_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    finished_at            TIMESTAMPTZ,
    metadata               JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_execution_step_retries_lookup
    ON tenant_execution_step_retries (tenant_id, plan_id, step_id, attempt_number DESC);

CREATE TABLE IF NOT EXISTS tenant_execution_artifacts (
    artifact_id            UUID         PRIMARY KEY,
    tenant_id              UUID         NOT NULL,
    plan_id                UUID         NOT NULL
                          REFERENCES tenant_execution_plans(plan_id) ON DELETE CASCADE,
    step_id                UUID
                          REFERENCES tenant_execution_steps(step_id) ON DELETE CASCADE,
    retry_id               UUID
                          REFERENCES tenant_execution_step_retries(retry_id) ON DELETE SET NULL,
    artifact_type          VARCHAR(40)  NOT NULL,
    artifact_ref           TEXT,
    artifact_json          JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_execution_artifacts_lookup
    ON tenant_execution_artifacts (tenant_id, plan_id, created_at DESC);

CREATE TABLE IF NOT EXISTS tenant_execution_transitions (
    transition_id          BIGSERIAL    PRIMARY KEY,
    tenant_id              UUID         NOT NULL,
    plan_id                UUID         NOT NULL
                          REFERENCES tenant_execution_plans(plan_id) ON DELETE CASCADE,
    step_id                UUID
                          REFERENCES tenant_execution_steps(step_id) ON DELETE CASCADE,
    from_status            VARCHAR(30),
    to_status              VARCHAR(30)  NOT NULL,
    reason                 TEXT,
    actor_sub              TEXT,
    metadata               JSONB        NOT NULL DEFAULT '{}'::jsonb,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_tenant_execution_transitions_lookup
    ON tenant_execution_transitions (tenant_id, plan_id, created_at DESC);
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

    # ------------------------------------------------------------------
    # Tenant messaging control-plane domain
    # ------------------------------------------------------------------

    @staticmethod
    def _normalize_messaging_provider(provider: str) -> str:
        normalized = provider.strip().lower()
        if normalized not in VALID_MESSAGING_PROVIDERS:
            raise ValueError(f"Unsupported messaging provider '{provider}'")
        return normalized

    @staticmethod
    def _coerce_retention_days(raw: Any, *, default: int = DEFAULT_MESSAGING_RETENTION_DAYS) -> int:
        try:
            days = int(raw)
        except (TypeError, ValueError):
            days = default
        return max(1, min(days, 365))

    def _resolve_messaging_retention_days(
        self,
        *,
        tenant_id: str,
        policy_days: int | None = None,
    ) -> int:
        if policy_days is not None:
            return self._coerce_retention_days(
                policy_days,
                default=DEFAULT_MESSAGING_RETENTION_DAYS,
            )
        configured = self.get_setting_cached(
            tenant_id,
            "security",
            "messaging_retention_days",
            default=DEFAULT_MESSAGING_RETENTION_DAYS,
        )
        return self._coerce_retention_days(
            configured,
            default=DEFAULT_MESSAGING_RETENTION_DAYS,
        )

    async def get_messaging_provider_config(
        self,
        *,
        tenant_id: str,
        provider: str = "whatsapp",
    ) -> dict[str, Any] | None:
        provider_norm = self._normalize_messaging_provider(provider)
        row = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id,
                   provider,
                   enabled,
                   bridge_mode,
                   account_ref,
                   session_ref,
                   metadata,
                   created_by,
                   updated_by,
                   created_at,
                   updated_at
            FROM tenant_messaging_provider_configs
            WHERE tenant_id = $1::uuid
              AND provider = $2
            LIMIT 1
            """,
            tenant_id,
            provider_norm,
        )
        if row is None:
            return None
        return dict(row)

    async def put_messaging_provider_config(
        self,
        *,
        tenant_id: str,
        provider: str,
        enabled: bool,
        actor: AdminActorContext,
        bridge_mode: str = "local_sidecar",
        account_ref: str | None = None,
        session_ref: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider_norm = self._normalize_messaging_provider(provider)
        mode = str(bridge_mode or "local_sidecar").strip().lower()
        if mode not in {"local_sidecar", "cloud_bridge"}:
            raise ValueError("bridge_mode must be one of: local_sidecar, cloud_bridge")
        before = await self.get_messaging_provider_config(
            tenant_id=tenant_id,
            provider=provider_norm,
        )
        row = await self._fetchrow(
            """
            INSERT INTO tenant_messaging_provider_configs (
                tenant_id,
                provider,
                enabled,
                bridge_mode,
                account_ref,
                session_ref,
                metadata,
                created_by,
                updated_by
            ) VALUES (
                $1::uuid, $2, $3, $4, $5, $6, $7::jsonb, $8, $8
            )
            ON CONFLICT (tenant_id, provider)
            DO UPDATE SET enabled = EXCLUDED.enabled,
                          bridge_mode = EXCLUDED.bridge_mode,
                          account_ref = EXCLUDED.account_ref,
                          session_ref = EXCLUDED.session_ref,
                          metadata = EXCLUDED.metadata,
                          updated_by = EXCLUDED.updated_by,
                          updated_at = now()
            RETURNING tenant_id::text AS tenant_id,
                      provider,
                      enabled,
                      bridge_mode,
                      account_ref,
                      session_ref,
                      metadata,
                      created_by,
                      updated_by,
                      created_at,
                      updated_at
            """,
            tenant_id,
            provider_norm,
            enabled,
            mode,
            account_ref,
            session_ref,
            json.dumps(metadata or {}),
            actor.actor_sub,
        )
        if row is None:
            raise RuntimeError("Failed to store tenant messaging provider config")
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_messaging_provider_config_upsert",
            actor=actor,
            before=before,
            after=after,
        )
        return after

    async def get_messaging_chat_policy(
        self,
        *,
        tenant_id: str,
        chat_id: str,
        provider: str = "whatsapp",
    ) -> dict[str, Any] | None:
        provider_norm = self._normalize_messaging_provider(provider)
        chat = str(chat_id or "").strip()
        if not chat:
            raise ValueError("Missing chat_id")
        row = await self._fetchrow(
            """
            SELECT tenant_id::text AS tenant_id,
                   provider,
                   chat_id,
                   read_enabled,
                   send_enabled,
                   retention_days,
                   metadata,
                   created_by,
                   updated_by,
                   created_at,
                   updated_at
            FROM tenant_messaging_chat_policies
            WHERE tenant_id = $1::uuid
              AND provider = $2
              AND chat_id = $3
            LIMIT 1
            """,
            tenant_id,
            provider_norm,
            chat,
        )
        if row is None:
            return None
        return dict(row)

    async def _sync_messaging_allowlist_setting(
        self,
        *,
        tenant_id: str,
        provider: str,
        actor: AdminActorContext,
    ) -> None:
        rows = await self._fetch(
            """
            SELECT chat_id
            FROM tenant_messaging_chat_policies
            WHERE tenant_id = $1::uuid
              AND provider = $2
              AND read_enabled = TRUE
            ORDER BY chat_id ASC
            """,
            tenant_id,
            provider,
        )
        allowlisted = sorted(str(row["chat_id"]) for row in rows)
        cached = self.get_setting_cached(
            tenant_id,
            "security",
            "messaging_allowlisted_chats",
            default=[],
        )
        current = []
        if isinstance(cached, list):
            current = sorted(str(item) for item in cached if str(item).strip())
        elif isinstance(cached, str) and cached.strip():
            current = sorted(part.strip() for part in cached.split(",") if part.strip())
        if current == allowlisted:
            return
        await self.set_setting(
            tenant_id=tenant_id,
            namespace="security",
            key="messaging_allowlisted_chats",
            value=allowlisted,
            data_type="json",
            actor=actor,
        )

    async def put_messaging_chat_policy(
        self,
        *,
        tenant_id: str,
        chat_id: str,
        provider: str,
        read_enabled: bool,
        send_enabled: bool,
        actor: AdminActorContext,
        retention_days: int | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider_norm = self._normalize_messaging_provider(provider)
        chat = str(chat_id or "").strip()
        if not chat:
            raise ValueError("Missing chat_id")
        resolved_retention = self._resolve_messaging_retention_days(
            tenant_id=tenant_id,
            policy_days=retention_days,
        )
        before = await self.get_messaging_chat_policy(
            tenant_id=tenant_id,
            provider=provider_norm,
            chat_id=chat,
        )
        row = await self._fetchrow(
            """
            INSERT INTO tenant_messaging_chat_policies (
                tenant_id,
                provider,
                chat_id,
                read_enabled,
                send_enabled,
                retention_days,
                metadata,
                created_by,
                updated_by
            ) VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb, $8, $8)
            ON CONFLICT (tenant_id, provider, chat_id)
            DO UPDATE SET read_enabled = EXCLUDED.read_enabled,
                          send_enabled = EXCLUDED.send_enabled,
                          retention_days = EXCLUDED.retention_days,
                          metadata = EXCLUDED.metadata,
                          updated_by = EXCLUDED.updated_by,
                          updated_at = now()
            RETURNING tenant_id::text AS tenant_id,
                      provider,
                      chat_id,
                      read_enabled,
                      send_enabled,
                      retention_days,
                      metadata,
                      created_by,
                      updated_by,
                      created_at,
                      updated_at
            """,
            tenant_id,
            provider_norm,
            chat,
            read_enabled,
            send_enabled,
            resolved_retention,
            json.dumps(metadata or {}),
            actor.actor_sub,
        )
        if row is None:
            raise RuntimeError("Failed to store tenant messaging chat policy")
        await self._sync_messaging_allowlist_setting(
            tenant_id=tenant_id,
            provider=provider_norm,
            actor=actor,
        )
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_messaging_chat_policy_upsert",
            actor=actor,
            before=before,
            after=after,
        )
        return after

    async def list_messaging_chats(
        self,
        *,
        tenant_id: str,
        provider: str | None = "whatsapp",
        include_inactive: bool = True,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        filters: list[str] = ["p.tenant_id = $1::uuid"]
        args: list[Any] = [tenant_id]
        idx = 2
        if provider is not None:
            provider_norm = self._normalize_messaging_provider(provider)
            filters.append(f"p.provider = ${idx}")
            args.append(provider_norm)
            idx += 1
        if not include_inactive:
            filters.append("(p.read_enabled = TRUE OR p.send_enabled = TRUE)")
        args.append(max(1, min(limit, 500)))
        rows = await self._fetch(
            f"""
            SELECT p.tenant_id::text AS tenant_id,
                   p.provider,
                   p.chat_id,
                   p.read_enabled,
                   p.send_enabled,
                   p.retention_days,
                   p.metadata,
                   p.created_by,
                   p.updated_by,
                   p.created_at,
                   p.updated_at,
                   COALESCE(stats.message_count, 0)::int AS message_count,
                   stats.last_message_at
            FROM tenant_messaging_chat_policies p
            LEFT JOIN LATERAL (
                SELECT COUNT(*) AS message_count,
                       MAX(created_at) AS last_message_at
                FROM tenant_messaging_messages m
                WHERE m.tenant_id = p.tenant_id
                  AND m.provider = p.provider
                  AND m.chat_id = p.chat_id
                  AND m.expires_at > now()
            ) stats ON TRUE
            WHERE {" AND ".join(filters)}
            ORDER BY COALESCE(stats.last_message_at, p.updated_at) DESC
            LIMIT ${idx}
            """,  # nosec B608
            *args,
        )
        return [dict(row) for row in rows]

    async def is_messaging_chat_allowed(
        self,
        *,
        tenant_id: str,
        provider: str = "whatsapp",
        chat_id: str,
        action: str = "read",
    ) -> bool:
        provider_norm = self._normalize_messaging_provider(provider)
        chat = str(chat_id or "").strip()
        if not chat:
            return False
        action_norm = str(action or "read").strip().lower()
        if action_norm not in {"read", "send"}:
            raise ValueError("Unsupported messaging action")
        row = await self._fetchrow(
            """
            SELECT read_enabled, send_enabled
            FROM tenant_messaging_chat_policies
            WHERE tenant_id = $1::uuid
              AND provider = $2
              AND chat_id = $3
            LIMIT 1
            """,
            tenant_id,
            provider_norm,
            chat,
        )
        if row is None:
            return False
        if action_norm == "send":
            return bool(row["send_enabled"])
        return bool(row["read_enabled"])

    async def ingest_messaging_message(
        self,
        *,
        tenant_id: str,
        provider: str,
        chat_id: str,
        direction: str = "inbound",
        event_type: str,
        body_text: str,
        metadata: dict[str, Any] | None = None,
        sender_id: str | None = None,
        sender_name: str | None = None,
        message_id: str | None = None,
        observed_at: datetime | None = None,
        action_id: str | None = None,
    ) -> dict[str, Any]:
        provider_norm = self._normalize_messaging_provider(provider)
        chat = str(chat_id or "").strip()
        if not chat:
            raise ValueError("Missing chat_id")
        direction_norm = str(direction or "inbound").strip().lower()
        if direction_norm not in VALID_MESSAGING_DIRECTIONS:
            raise ValueError(f"Invalid messaging direction '{direction}'")
        event = str(event_type or "").strip()
        if not event:
            raise ValueError("Missing event_type")
        body = str(body_text or "")
        if not body:
            body = "{}" if metadata else ""
        policy = await self.get_messaging_chat_policy(
            tenant_id=tenant_id,
            provider=provider_norm,
            chat_id=chat,
        )
        retention_days = self._resolve_messaging_retention_days(
            tenant_id=tenant_id,
            policy_days=(
                int(policy["retention_days"])
                if isinstance(policy, dict) and policy.get("retention_days") is not None
                else None
            ),
        )
        observed = observed_at or datetime.now(UTC)
        expires_at = observed + timedelta(days=retention_days)
        try:
            message_uuid = str(UUID(str(message_id))) if message_id else str(uuid4())
        except ValueError as exc:
            raise ValueError("Invalid message_id") from exc
        action_uuid: str | None = None
        if action_id:
            try:
                action_uuid = str(UUID(str(action_id)))
            except ValueError as exc:
                raise ValueError("Invalid action_id") from exc
        row = await self._fetchrow(
            """
            INSERT INTO tenant_messaging_messages (
                message_id,
                tenant_id,
                provider,
                chat_id,
                direction,
                sender_id,
                sender_name,
                body_enc,
                metadata,
                action_id,
                event_type,
                observed_at,
                expires_at
            ) VALUES (
                $1::uuid, $2::uuid, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::uuid, $11, $12, $13
            )
            ON CONFLICT (tenant_id, provider, message_id)
            DO UPDATE SET body_enc = EXCLUDED.body_enc,
                          metadata = EXCLUDED.metadata,
                          sender_id = EXCLUDED.sender_id,
                          sender_name = EXCLUDED.sender_name,
                          observed_at = EXCLUDED.observed_at,
                          expires_at = EXCLUDED.expires_at,
                          action_id = COALESCE(
                              EXCLUDED.action_id,
                              tenant_messaging_messages.action_id
                          ),
                          event_type = EXCLUDED.event_type
            RETURNING message_id::text AS message_id,
                      tenant_id::text AS tenant_id,
                      provider,
                      chat_id,
                      direction,
                      sender_id,
                      sender_name,
                      body_enc,
                      metadata,
                      action_id::text AS action_id,
                      event_type,
                      observed_at,
                      expires_at,
                      created_at
            """,
            message_uuid,
            tenant_id,
            provider_norm,
            chat,
            direction_norm,
            sender_id,
            sender_name,
            self._encrypt(body),
            json.dumps(metadata or {}),
            action_uuid,
            event,
            observed,
            expires_at,
        )
        if row is None:
            raise RuntimeError("Failed to store tenant messaging message")
        result = dict(row)
        result["body_text"] = self._decrypt(str(result.get("body_enc") or "")) or ""
        return result

    async def list_messaging_messages(
        self,
        *,
        tenant_id: str,
        provider: str | None = "whatsapp",
        chat_id: str | None = None,
        direction: str | None = None,
        limit: int = 200,
        include_expired: bool = False,
    ) -> list[dict[str, Any]]:
        filters: list[str] = ["tenant_id = $1::uuid"]
        args: list[Any] = [tenant_id]
        idx = 2
        if provider is not None:
            provider_norm = self._normalize_messaging_provider(provider)
            filters.append(f"provider = ${idx}")
            args.append(provider_norm)
            idx += 1
        if chat_id:
            chat = str(chat_id).strip()
            if not chat:
                raise ValueError("Invalid chat_id")
            filters.append(f"chat_id = ${idx}")
            args.append(chat)
            idx += 1
        if direction:
            direction_norm = str(direction).strip().lower()
            if direction_norm not in VALID_MESSAGING_DIRECTIONS:
                raise ValueError(f"Invalid direction '{direction}'")
            filters.append(f"direction = ${idx}")
            args.append(direction_norm)
            idx += 1
        if not include_expired:
            filters.append("expires_at > now()")
        args.append(max(1, min(limit, 500)))
        rows = await self._fetch(
            f"""
            SELECT message_id::text AS message_id,
                   tenant_id::text AS tenant_id,
                   provider,
                   chat_id,
                   direction,
                   sender_id,
                   sender_name,
                   body_enc,
                   metadata,
                   action_id::text AS action_id,
                   event_type,
                   observed_at,
                   expires_at,
                   created_at
            FROM tenant_messaging_messages
            WHERE {" AND ".join(filters)}
            ORDER BY created_at DESC
            LIMIT ${idx}
            """,  # nosec B608
            *args,
        )
        result: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            data["body_text"] = self._decrypt(str(data.get("body_enc") or "")) or ""
            result.append(data)
        return result

    async def queue_messaging_send(
        self,
        *,
        tenant_id: str,
        provider: str,
        chat_id: str,
        body_text: str,
        actor: AdminActorContext,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        provider_norm = self._normalize_messaging_provider(provider)
        chat = str(chat_id or "").strip()
        if not chat:
            raise ValueError("Missing chat_id")
        body = str(body_text or "").strip()
        if not body:
            raise ValueError("Missing non-empty message body")
        allowed = await self.is_messaging_chat_allowed(
            tenant_id=tenant_id,
            provider=provider_norm,
            chat_id=chat,
            action="send",
        )
        if not allowed:
            raise ValueError("Chat send policy is not enabled")

        action_id = str(uuid4())
        payload = {"text": body, "metadata": metadata or {}}
        action_row = await self._fetchrow(
            """
            INSERT INTO tenant_messaging_action_queue (
                action_id,
                tenant_id,
                provider,
                chat_id,
                action_type,
                payload_enc,
                payload_json,
                status,
                created_by,
                request_id,
                change_ticket_id
            ) VALUES (
                $1::uuid, $2::uuid, $3, $4, 'send', $5, $6::jsonb, 'queued', $7, $8, $9
            )
            RETURNING action_id::text AS action_id,
                      tenant_id::text AS tenant_id,
                      provider,
                      chat_id,
                      action_type,
                      payload_json,
                      status,
                      created_by,
                      request_id,
                      change_ticket_id,
                      error_code,
                      error_detail,
                      created_at,
                      updated_at
            """,
            action_id,
            tenant_id,
            provider_norm,
            chat,
            self._encrypt(json.dumps(payload)),
            json.dumps(payload),
            actor.actor_sub,
            actor.request_id,
            actor.change_ticket_id,
        )
        if action_row is None:
            raise RuntimeError("Failed to queue messaging send action")

        message_row = await self.ingest_messaging_message(
            tenant_id=tenant_id,
            provider=provider_norm,
            chat_id=chat,
            direction="outbound",
            event_type="messaging.send.queued",
            body_text=body,
            metadata={"queued_action_id": action_id, **(metadata or {})},
            sender_id=actor.actor_sub,
            sender_name=actor.actor_email or actor.actor_sub,
            action_id=action_id,
        )
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_messaging_send_queued",
            actor=actor,
            before=None,
            after={
                "action_id": action_id,
                "provider": provider_norm,
                "chat_id": chat,
                "message_id": message_row["message_id"],
            },
        )
        return {"action": dict(action_row), "message": message_row}

    async def purge_expired_messaging_messages(
        self,
        *,
        tenant_id: str | None = None,
        limit: int = 5000,
    ) -> int:
        max_rows = max(1, min(limit, 20000))
        if tenant_id:
            result = await self._execute(
                """
                WITH doomed AS (
                    SELECT message_pk
                    FROM tenant_messaging_messages
                    WHERE tenant_id = $1::uuid
                      AND expires_at <= now()
                    ORDER BY expires_at ASC
                    LIMIT $2
                )
                DELETE FROM tenant_messaging_messages m
                USING doomed d
                WHERE m.message_pk = d.message_pk
                """,
                tenant_id,
                max_rows,
            )
        else:
            result = await self._execute(
                """
                WITH doomed AS (
                    SELECT message_pk
                    FROM tenant_messaging_messages
                    WHERE expires_at <= now()
                    ORDER BY expires_at ASC
                    LIMIT $1
                )
                DELETE FROM tenant_messaging_messages m
                USING doomed d
                WHERE m.message_pk = d.message_pk
                """,
                max_rows,
            )
        return _rowcount_from_execute(result)

    # ------------------------------------------------------------------
    # Tenant execution ledger + overnight continuation domain
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_execution_steps(raw_steps: Any) -> list[dict[str, str]]:
        if not isinstance(raw_steps, list) or not raw_steps:
            raise ValueError("steps must be a non-empty array")

        normalized: list[dict[str, str]] = []
        for idx, raw in enumerate(raw_steps, start=1):
            title = f"Step {idx}"
            prompt = ""
            idempotency_key = f"step-{idx}"

            if isinstance(raw, str):
                prompt = raw.strip()
            elif isinstance(raw, dict):
                title = str(raw.get("title") or title).strip() or title
                prompt = str(
                    raw.get("prompt")
                    or raw.get("instruction")
                    or raw.get("text")
                    or raw.get("message")
                    or ""
                ).strip()
                candidate = str(raw.get("idempotency_key") or "").strip().lower()
                if candidate:
                    idempotency_key = re.sub(r"[^a-z0-9_.:-]+", "-", candidate).strip("-") or (
                        f"step-{idx}"
                    )
            else:
                raise ValueError(f"steps[{idx - 1}] must be a string or object")

            if not prompt:
                raise ValueError(f"steps[{idx - 1}] is missing non-empty prompt text")
            normalized.append(
                {
                    "title": title[:200],
                    "prompt_text": prompt,
                    "idempotency_key": idempotency_key[:120],
                }
            )
        return normalized

    @staticmethod
    def _coerce_execution_actor_user_id(actor_sub: str | None) -> int:
        raw = str(actor_sub or "").strip()
        if not raw:
            return 0
        try:
            return max(0, int(raw))
        except ValueError:
            return 0

    @staticmethod
    def _execution_retry_backoff_seconds(attempt_count: int) -> int:
        idx = min(max(attempt_count - 1, 0), len(EXECUTION_RETRY_BACKOFF_SECONDS) - 1)
        return int(EXECUTION_RETRY_BACKOFF_SECONDS[idx])

    async def schedule_execution_continuation(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        scheduled_for: datetime | None = None,
        reason: str = "continuation",
        requested_by: str | None = None,
        priority: int = 2,
    ) -> str | None:
        run_at = scheduled_for or datetime.now(UTC)
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=UTC)

        queue_item_id = str(uuid4())
        payload = {
            "tenant_id": tenant_id,
            "plan_id": plan_id,
            "reason": str(reason or "continuation"),
            "requested_by": str(requested_by or ""),
            "requested_at": datetime.now(UTC).isoformat(),
        }
        user_id = self._coerce_execution_actor_user_id(requested_by)
        try:
            await self._execute(
                """
                INSERT INTO message_queue (
                    id,
                    priority,
                    status,
                    task_type,
                    user_id,
                    payload,
                    max_attempts,
                    scheduled_for,
                    correlation_id
                ) VALUES (
                    $1::uuid, $2, 'queued', 'plan_continuation', $3, $4::jsonb, 5, $5, $6
                )
                """,
                queue_item_id,
                max(0, min(int(priority), 3)),
                user_id,
                json.dumps(payload),
                run_at,
                f"execution-plan:{plan_id}",
            )
            return queue_item_id
        except Exception:
            log.warning(
                "execution_continuation_enqueue_failed",
                tenant_id=tenant_id,
                plan_id=plan_id,
                scheduled_for=run_at.isoformat(),
            )
            return None

    async def create_execution_plan(
        self,
        *,
        tenant_id: str,
        title: str,
        goal: str,
        steps: Any,
        actor: AdminActorContext,
        metadata: dict[str, Any] | None = None,
        max_step_attempts: int = DEFAULT_EXECUTION_MAX_STEP_ATTEMPTS,
        continuation_interval_seconds: int = DEFAULT_EXECUTION_CONTINUATION_INTERVAL_SECONDS,
        start_at: datetime | None = None,
    ) -> dict[str, Any]:
        plan_title = str(title or "").strip()
        if not plan_title:
            raise ValueError("Missing non-empty title")
        plan_goal = str(goal or "").strip()
        if not plan_goal:
            raise ValueError("Missing non-empty goal")
        normalized_steps = self._coerce_execution_steps(steps)
        max_attempts = max(1, min(int(max_step_attempts), 10))
        continuation_seconds = max(1, min(int(continuation_interval_seconds), 3600))
        run_at = start_at or datetime.now(UTC)
        if run_at.tzinfo is None:
            run_at = run_at.replace(tzinfo=UTC)

        plan_id = str(uuid4())
        step_rows: list[dict[str, Any]] = []
        plan_row: dict[str, Any] | None = None

        async with self._pool.acquire() as conn, conn.transaction():
            inserted_plan = await conn.fetchrow(
                """
                INSERT INTO tenant_execution_plans (
                    plan_id,
                    tenant_id,
                    title,
                    goal,
                    status,
                    current_step_index,
                    total_steps,
                    max_step_attempts,
                    continuation_interval_seconds,
                    next_run_at,
                    metadata,
                    created_by,
                    updated_by
                ) VALUES (
                    $1::uuid,
                    $2::uuid,
                    $3,
                    $4,
                    'queued',
                    0,
                    $5,
                    $6,
                    $7,
                    $8,
                    $9::jsonb,
                    $10,
                    $10
                )
                RETURNING plan_id::text AS plan_id,
                          tenant_id::text AS tenant_id,
                          title,
                          goal,
                          status,
                          current_step_index,
                          total_steps,
                          max_step_attempts,
                          continuation_interval_seconds,
                          next_run_at,
                          lease_owner,
                          lease_expires_at,
                          metadata,
                          last_error_category,
                          last_error_detail,
                          created_by,
                          updated_by,
                          created_at,
                          updated_at
                """,
                plan_id,
                tenant_id,
                plan_title,
                plan_goal,
                len(normalized_steps),
                max_attempts,
                continuation_seconds,
                run_at,
                json.dumps(metadata or {}),
                actor.actor_sub,
            )
            if inserted_plan is None:
                raise RuntimeError("Failed to create execution plan")
            plan_row = dict(inserted_plan)

            await conn.execute(
                """
                INSERT INTO tenant_execution_transitions (
                    tenant_id,
                    plan_id,
                    step_id,
                    from_status,
                    to_status,
                    reason,
                    actor_sub,
                    metadata
                ) VALUES (
                    $1::uuid,
                    $2::uuid,
                    NULL,
                    NULL,
                    'queued',
                    'plan_created',
                    $3,
                    $4::jsonb
                )
                """,
                tenant_id,
                plan_id,
                actor.actor_sub,
                json.dumps({"step_count": len(normalized_steps)}),
            )

            for idx, step in enumerate(normalized_steps):
                step_id = str(uuid4())
                inserted_step = await conn.fetchrow(
                    """
                    INSERT INTO tenant_execution_steps (
                        step_id,
                        plan_id,
                        tenant_id,
                        step_index,
                        title,
                        prompt_text,
                        idempotency_key,
                        status,
                        attempt_count,
                        max_attempts,
                        next_retry_at,
                        output_json,
                        metadata
                    ) VALUES (
                        $1::uuid,
                        $2::uuid,
                        $3::uuid,
                        $4,
                        $5,
                        $6,
                        $7,
                        'pending',
                        0,
                        $8,
                        NULL,
                        '{}'::jsonb,
                        '{}'::jsonb
                    )
                    RETURNING step_id::text AS step_id,
                              plan_id::text AS plan_id,
                              tenant_id::text AS tenant_id,
                              step_index,
                              title,
                              prompt_text,
                              idempotency_key,
                              status,
                              attempt_count,
                              max_attempts,
                              next_retry_at,
                              last_error_category,
                              last_error_detail,
                              output_json,
                              metadata,
                              created_at,
                              updated_at
                    """,
                    step_id,
                    plan_id,
                    tenant_id,
                    idx,
                    step["title"],
                    step["prompt_text"],
                    step["idempotency_key"],
                    max_attempts,
                )
                if inserted_step is None:
                    raise RuntimeError("Failed to create execution step")
                step_dict = dict(inserted_step)
                step_rows.append(step_dict)

                await conn.execute(
                    """
                    INSERT INTO tenant_execution_transitions (
                        tenant_id,
                        plan_id,
                        step_id,
                        from_status,
                        to_status,
                        reason,
                        actor_sub,
                        metadata
                    ) VALUES (
                        $1::uuid,
                        $2::uuid,
                        $3::uuid,
                        NULL,
                        'pending',
                        'step_created',
                        $4,
                        $5::jsonb
                    )
                    """,
                    tenant_id,
                    plan_id,
                    step_id,
                    actor.actor_sub,
                    json.dumps({"step_index": idx}),
                )

        await self.schedule_execution_continuation(
            tenant_id=tenant_id,
            plan_id=plan_id,
            scheduled_for=run_at,
            reason="plan_created",
            requested_by=actor.actor_sub,
        )

        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_execution_plan_created",
            actor=actor,
            before=None,
            after={
                "plan_id": plan_id,
                "title": plan_title,
                "goal": plan_goal,
                "status": "queued",
                "steps": len(step_rows),
            },
        )
        return {"plan": plan_row or {}, "steps": step_rows}

    async def list_execution_plans(
        self,
        *,
        tenant_id: str,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        filters = ["tenant_id = $1::uuid"]
        args: list[Any] = [tenant_id]
        idx = 2
        if status is not None and str(status).strip():
            status_norm = str(status).strip().lower()
            if status_norm not in VALID_EXECUTION_PLAN_STATUSES:
                raise ValueError(f"Invalid execution plan status '{status}'")
            filters.append(f"status = ${idx}")
            args.append(status_norm)
            idx += 1
        args.append(max(1, min(limit, 500)))
        rows = await self._fetch(
            f"""
            SELECT plan_id::text AS plan_id,
                   tenant_id::text AS tenant_id,
                   title,
                   goal,
                   status,
                   current_step_index,
                   total_steps,
                   max_step_attempts,
                   continuation_interval_seconds,
                   next_run_at,
                   lease_owner,
                   lease_expires_at,
                   metadata,
                   last_error_category,
                   last_error_detail,
                   created_by,
                   updated_by,
                   created_at,
                   updated_at
            FROM tenant_execution_plans
            WHERE {" AND ".join(filters)}
            ORDER BY updated_at DESC
            LIMIT ${idx}
            """,  # nosec B608
            *args,
        )
        return [dict(row) for row in rows]

    async def get_execution_plan(
        self,
        *,
        tenant_id: str,
        plan_id: str,
    ) -> dict[str, Any] | None:
        row = await self._fetchrow(
            """
            SELECT plan_id::text AS plan_id,
                   tenant_id::text AS tenant_id,
                   title,
                   goal,
                   status,
                   current_step_index,
                   total_steps,
                   max_step_attempts,
                   continuation_interval_seconds,
                   next_run_at,
                   lease_owner,
                   lease_expires_at,
                   metadata,
                   last_error_category,
                   last_error_detail,
                   created_by,
                   updated_by,
                   created_at,
                   updated_at
            FROM tenant_execution_plans
            WHERE tenant_id = $1::uuid
              AND plan_id = $2::uuid
            LIMIT 1
            """,
            tenant_id,
            plan_id,
        )
        if row is None:
            return None
        return dict(row)

    async def list_execution_plan_steps(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        include_prompt: bool = True,
    ) -> list[dict[str, Any]]:
        rows = await self._fetch(
            """
            SELECT step_id::text AS step_id,
                   plan_id::text AS plan_id,
                   tenant_id::text AS tenant_id,
                   step_index,
                   title,
                   prompt_text,
                   idempotency_key,
                   status,
                   attempt_count,
                   max_attempts,
                   next_retry_at,
                   last_error_category,
                   last_error_detail,
                   output_json,
                   metadata,
                   created_at,
                   updated_at
            FROM tenant_execution_steps
            WHERE tenant_id = $1::uuid
              AND plan_id = $2::uuid
            ORDER BY step_index ASC
            """,
            tenant_id,
            plan_id,
        )
        output: list[dict[str, Any]] = []
        for row in rows:
            data = dict(row)
            if not include_prompt:
                data.pop("prompt_text", None)
            output.append(data)
        return output

    async def pause_execution_plan(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        actor: AdminActorContext,
    ) -> dict[str, Any]:
        before = await self.get_execution_plan(tenant_id=tenant_id, plan_id=plan_id)
        if before is None:
            raise ValueError("Execution plan not found")
        row = await self._fetchrow(
            """
            UPDATE tenant_execution_plans
            SET status = 'paused',
                lease_owner = NULL,
                lease_expires_at = NULL,
                next_run_at = NULL,
                updated_by = $3,
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND plan_id = $2::uuid
              AND status IN ('queued', 'running', 'failed', 'paused')
            RETURNING plan_id::text AS plan_id,
                      tenant_id::text AS tenant_id,
                      title,
                      goal,
                      status,
                      current_step_index,
                      total_steps,
                      max_step_attempts,
                      continuation_interval_seconds,
                      next_run_at,
                      lease_owner,
                      lease_expires_at,
                      metadata,
                      last_error_category,
                      last_error_detail,
                      created_by,
                      updated_by,
                      created_at,
                      updated_at
            """,
            tenant_id,
            plan_id,
            actor.actor_sub,
        )
        if row is None:
            raise ValueError("Execution plan could not be paused")
        await self._execute(
            """
            UPDATE tenant_execution_steps
            SET status = 'pending',
                next_retry_at = NULL,
                last_error_category = COALESCE(last_error_category, 'paused'),
                last_error_detail = COALESCE(last_error_detail, 'paused by operator'),
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND plan_id = $2::uuid
              AND status = 'running'
            """,
            tenant_id,
            plan_id,
        )
        await self._execute(
            """
            INSERT INTO tenant_execution_transitions (
                tenant_id,
                plan_id,
                step_id,
                from_status,
                to_status,
                reason,
                actor_sub,
                metadata
            ) VALUES (
                $1::uuid, $2::uuid, NULL, $3, 'paused', 'plan_paused', $4, '{}'::jsonb
            )
            """,
            tenant_id,
            plan_id,
            str(before.get("status") or ""),
            actor.actor_sub,
        )
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_execution_plan_paused",
            actor=actor,
            before=before,
            after=after,
        )
        return after

    async def resume_execution_plan(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        actor: AdminActorContext,
        immediately: bool = True,
    ) -> dict[str, Any]:
        before = await self.get_execution_plan(tenant_id=tenant_id, plan_id=plan_id)
        if before is None:
            raise ValueError("Execution plan not found")
        run_at = (
            datetime.now(UTC)
            if immediately
            else (before.get("next_run_at") or datetime.now(UTC))
        )
        row = await self._fetchrow(
            """
            UPDATE tenant_execution_plans
            SET status = 'queued',
                lease_owner = NULL,
                lease_expires_at = NULL,
                next_run_at = $3,
                updated_by = $4,
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND plan_id = $2::uuid
              AND status IN ('paused', 'failed', 'queued', 'running')
            RETURNING plan_id::text AS plan_id,
                      tenant_id::text AS tenant_id,
                      title,
                      goal,
                      status,
                      current_step_index,
                      total_steps,
                      max_step_attempts,
                      continuation_interval_seconds,
                      next_run_at,
                      lease_owner,
                      lease_expires_at,
                      metadata,
                      last_error_category,
                      last_error_detail,
                      created_by,
                      updated_by,
                      created_at,
                      updated_at
            """,
            tenant_id,
            plan_id,
            run_at,
            actor.actor_sub,
        )
        if row is None:
            raise ValueError("Execution plan could not be resumed")
        await self._execute(
            """
            UPDATE tenant_execution_steps
            SET status = 'pending',
                next_retry_at = NULL,
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND plan_id = $2::uuid
              AND status = 'running'
            """,
            tenant_id,
            plan_id,
        )
        await self._execute(
            """
            INSERT INTO tenant_execution_transitions (
                tenant_id,
                plan_id,
                step_id,
                from_status,
                to_status,
                reason,
                actor_sub,
                metadata
            ) VALUES (
                $1::uuid, $2::uuid, NULL, $3, 'queued', 'plan_resumed', $4, '{}'::jsonb
            )
            """,
            tenant_id,
            plan_id,
            str(before.get("status") or ""),
            actor.actor_sub,
        )
        await self.schedule_execution_continuation(
            tenant_id=tenant_id,
            plan_id=plan_id,
            scheduled_for=run_at,
            reason="plan_resumed",
            requested_by=actor.actor_sub,
        )
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_execution_plan_resumed",
            actor=actor,
            before=before,
            after=after,
        )
        return after

    async def cancel_execution_plan(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        actor: AdminActorContext,
    ) -> dict[str, Any]:
        before = await self.get_execution_plan(tenant_id=tenant_id, plan_id=plan_id)
        if before is None:
            raise ValueError("Execution plan not found")
        row = await self._fetchrow(
            """
            UPDATE tenant_execution_plans
            SET status = 'cancelled',
                lease_owner = NULL,
                lease_expires_at = NULL,
                next_run_at = NULL,
                updated_by = $3,
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND plan_id = $2::uuid
              AND status <> 'completed'
            RETURNING plan_id::text AS plan_id,
                      tenant_id::text AS tenant_id,
                      title,
                      goal,
                      status,
                      current_step_index,
                      total_steps,
                      max_step_attempts,
                      continuation_interval_seconds,
                      next_run_at,
                      lease_owner,
                      lease_expires_at,
                      metadata,
                      last_error_category,
                      last_error_detail,
                      created_by,
                      updated_by,
                      created_at,
                      updated_at
            """,
            tenant_id,
            plan_id,
            actor.actor_sub,
        )
        if row is None:
            raise ValueError("Execution plan could not be cancelled")
        await self._execute(
            """
            UPDATE tenant_execution_steps
            SET status = 'cancelled',
                next_retry_at = NULL,
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND plan_id = $2::uuid
              AND status IN ('pending', 'running', 'failed', 'blocked')
            """,
            tenant_id,
            plan_id,
        )
        await self._execute(
            """
            INSERT INTO tenant_execution_transitions (
                tenant_id,
                plan_id,
                step_id,
                from_status,
                to_status,
                reason,
                actor_sub,
                metadata
            ) VALUES (
                $1::uuid, $2::uuid, NULL, $3, 'cancelled', 'plan_cancelled', $4, '{}'::jsonb
            )
            """,
            tenant_id,
            plan_id,
            str(before.get("status") or ""),
            actor.actor_sub,
        )
        after = dict(row)
        await self._write_audit(
            tenant_id=tenant_id,
            action="tenant_execution_plan_cancelled",
            actor=actor,
            before=before,
            after=after,
        )
        return after

    async def claim_execution_plan_lease(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        worker_id: str,
        lease_seconds: int = DEFAULT_EXECUTION_LEASE_SECONDS,
    ) -> dict[str, Any] | None:
        worker = str(worker_id or "").strip()
        if not worker:
            raise ValueError("Missing worker_id")
        lease_window = max(15, min(int(lease_seconds), 3600))
        row = await self._fetchrow(
            """
            UPDATE tenant_execution_plans
            SET lease_owner = $3,
                lease_expires_at = now() + ($4 * interval '1 second'),
                status = CASE WHEN status = 'queued' THEN 'running' ELSE status END,
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND plan_id = $2::uuid
              AND status IN ('queued', 'running')
              AND next_run_at <= now()
              AND (
                  lease_expires_at IS NULL
                  OR lease_expires_at < now()
                  OR lease_owner = $3
              )
            RETURNING plan_id::text AS plan_id,
                      tenant_id::text AS tenant_id,
                      title,
                      goal,
                      status,
                      current_step_index,
                      total_steps,
                      max_step_attempts,
                      continuation_interval_seconds,
                      next_run_at,
                      lease_owner,
                      lease_expires_at,
                      metadata,
                      last_error_category,
                      last_error_detail,
                      created_by,
                      updated_by,
                      created_at,
                      updated_at
            """,
            tenant_id,
            plan_id,
            worker,
            lease_window,
        )
        if row is None:
            return None
        return dict(row)

    async def claim_next_execution_step(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        worker_id: str,
        lease_token: str,
        stale_running_seconds: int = DEFAULT_EXECUTION_STALE_STEP_SECONDS,
    ) -> dict[str, Any] | None:
        stale_seconds = max(30, min(int(stale_running_seconds), 7200))
        worker = str(worker_id or "").strip()
        if not worker:
            raise ValueError("Missing worker_id")
        token = str(lease_token or "").strip() or uuid4().hex

        async with self._pool.acquire() as conn, conn.transaction():
            candidate = await conn.fetchrow(
                """
                SELECT step_id::text AS step_id,
                       step_index,
                       status
                FROM tenant_execution_steps
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                  AND (
                      status = 'pending'
                      OR (
                          status = 'failed'
                          AND attempt_count < max_attempts
                          AND COALESCE(next_retry_at, now()) <= now()
                      )
                      OR (
                          status = 'running'
                          AND updated_at <= now() - ($3 * interval '1 second')
                      )
                  )
                ORDER BY step_index ASC
                LIMIT 1
                FOR UPDATE SKIP LOCKED
                """,
                tenant_id,
                plan_id,
                stale_seconds,
            )
            if candidate is None:
                return None

            step_id = str(candidate["step_id"])
            from_status = str(candidate["status"])
            updated_step = await conn.fetchrow(
                """
                UPDATE tenant_execution_steps
                SET status = 'running',
                    attempt_count = attempt_count + 1,
                    next_retry_at = NULL,
                    updated_at = now()
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                  AND step_id = $3::uuid
                RETURNING step_id::text AS step_id,
                          plan_id::text AS plan_id,
                          tenant_id::text AS tenant_id,
                          step_index,
                          title,
                          prompt_text,
                          idempotency_key,
                          status,
                          attempt_count,
                          max_attempts,
                          next_retry_at,
                          last_error_category,
                          last_error_detail,
                          output_json,
                          metadata,
                          created_at,
                          updated_at
                """,
                tenant_id,
                plan_id,
                step_id,
            )
            if updated_step is None:
                return None
            step_data = dict(updated_step)
            attempt_number = int(step_data["attempt_count"])
            retry_id = str(uuid4())

            await conn.execute(
                """
                INSERT INTO tenant_execution_step_retries (
                    retry_id,
                    tenant_id,
                    plan_id,
                    step_id,
                    attempt_number,
                    worker_id,
                    lease_token,
                    metadata
                ) VALUES (
                    $1::uuid,
                    $2::uuid,
                    $3::uuid,
                    $4::uuid,
                    $5,
                    $6,
                    $7,
                    $8::jsonb
                )
                """,
                retry_id,
                tenant_id,
                plan_id,
                step_id,
                attempt_number,
                worker,
                token,
                json.dumps({"reclaimed_running_step": from_status == "running"}),
            )

            await conn.execute(
                """
                UPDATE tenant_execution_plans
                SET current_step_index = $3,
                    updated_at = now()
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                """,
                tenant_id,
                plan_id,
                int(step_data["step_index"]),
            )

            await conn.execute(
                """
                INSERT INTO tenant_execution_transitions (
                    tenant_id,
                    plan_id,
                    step_id,
                    from_status,
                    to_status,
                    reason,
                    actor_sub,
                    metadata
                ) VALUES (
                    $1::uuid,
                    $2::uuid,
                    $3::uuid,
                    $4,
                    'running',
                    'step_claimed',
                    $5,
                    $6::jsonb
                )
                """,
                tenant_id,
                plan_id,
                step_id,
                from_status,
                worker,
                json.dumps({"lease_token": token, "attempt_number": attempt_number}),
            )

        return {
            "step": step_data,
            "retry": {
                "retry_id": retry_id,
                "attempt_number": attempt_number,
                "lease_token": token,
                "worker_id": worker,
            },
        }

    async def release_execution_plan_lease(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        worker_id: str | None = None,
    ) -> None:
        worker = str(worker_id or "").strip()
        if worker:
            await self._execute(
                """
                UPDATE tenant_execution_plans
                SET lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                  AND (lease_owner = $3 OR lease_owner IS NULL)
                """,
                tenant_id,
                plan_id,
                worker,
            )
            return
        await self._execute(
            """
            UPDATE tenant_execution_plans
            SET lease_owner = NULL,
                lease_expires_at = NULL,
                updated_at = now()
            WHERE tenant_id = $1::uuid
              AND plan_id = $2::uuid
            """,
            tenant_id,
            plan_id,
        )

    async def reconcile_execution_plan_status(
        self,
        *,
        tenant_id: str,
        plan_id: str,
    ) -> dict[str, Any] | None:
        async with self._pool.acquire() as conn, conn.transaction():
            plan_row = await conn.fetchrow(
                """
                SELECT status,
                       continuation_interval_seconds
                FROM tenant_execution_plans
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                FOR UPDATE
                """,
                tenant_id,
                plan_id,
            )
            if plan_row is None:
                return None
            status = str(plan_row["status"])
            if status in {"completed", "cancelled"}:
                final_row = await conn.fetchrow(
                    """
                    SELECT plan_id::text AS plan_id,
                           tenant_id::text AS tenant_id,
                           title,
                           goal,
                           status,
                           current_step_index,
                           total_steps,
                           max_step_attempts,
                           continuation_interval_seconds,
                           next_run_at,
                           lease_owner,
                           lease_expires_at,
                           metadata,
                           last_error_category,
                           last_error_detail,
                           created_by,
                           updated_by,
                           created_at,
                           updated_at
                    FROM tenant_execution_plans
                    WHERE tenant_id = $1::uuid
                      AND plan_id = $2::uuid
                    LIMIT 1
                    """,
                    tenant_id,
                    plan_id,
                )
                return dict(final_row) if final_row is not None else None

            remaining = await conn.fetchval(
                """
                SELECT COUNT(*)::int
                FROM tenant_execution_steps
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                  AND status NOT IN ('completed', 'cancelled')
                """,
                tenant_id,
                plan_id,
            )
            if int(remaining or 0) == 0:
                await conn.execute(
                    """
                    UPDATE tenant_execution_plans
                    SET status = 'completed',
                        next_run_at = NULL,
                        lease_owner = NULL,
                        lease_expires_at = NULL,
                        updated_at = now()
                    WHERE tenant_id = $1::uuid
                      AND plan_id = $2::uuid
                    """,
                    tenant_id,
                    plan_id,
                )
                await conn.execute(
                    """
                    INSERT INTO tenant_execution_transitions (
                        tenant_id,
                        plan_id,
                        step_id,
                        from_status,
                        to_status,
                        reason,
                        actor_sub,
                        metadata
                    ) VALUES (
                        $1::uuid,
                        $2::uuid,
                        NULL,
                        $3,
                        'completed',
                        'plan_reconciled_complete',
                        NULL,
                        '{}'::jsonb
                    )
                    """,
                    tenant_id,
                    plan_id,
                    status,
                )

            final_row = await conn.fetchrow(
                """
                SELECT plan_id::text AS plan_id,
                       tenant_id::text AS tenant_id,
                       title,
                       goal,
                       status,
                       current_step_index,
                       total_steps,
                       max_step_attempts,
                       continuation_interval_seconds,
                       next_run_at,
                       lease_owner,
                       lease_expires_at,
                       metadata,
                       last_error_category,
                       last_error_detail,
                       created_by,
                       updated_by,
                       created_at,
                       updated_at
                FROM tenant_execution_plans
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                LIMIT 1
                """,
                tenant_id,
                plan_id,
            )
            return dict(final_row) if final_row is not None else None

    async def complete_execution_step(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        step_id: str,
        retry_id: str,
        worker_id: str,
        output_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        async with self._pool.acquire() as conn, conn.transaction():
            plan_row = await conn.fetchrow(
                """
                SELECT status,
                       continuation_interval_seconds
                FROM tenant_execution_plans
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                FOR UPDATE
                """,
                tenant_id,
                plan_id,
            )
            if plan_row is None:
                raise ValueError("Execution plan not found")
            plan_status_before = str(plan_row["status"])
            continuation_seconds = int(plan_row["continuation_interval_seconds"])

            step_before = await conn.fetchrow(
                """
                SELECT step_index, status
                FROM tenant_execution_steps
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                  AND step_id = $3::uuid
                FOR UPDATE
                """,
                tenant_id,
                plan_id,
                step_id,
            )
            if step_before is None:
                raise ValueError("Execution step not found")
            step_status_before = str(step_before["status"])
            step_index = int(step_before["step_index"])

            updated_step = await conn.fetchrow(
                """
                UPDATE tenant_execution_steps
                SET status = 'completed',
                    output_json = $4::jsonb,
                    next_retry_at = NULL,
                    last_error_category = NULL,
                    last_error_detail = NULL,
                    updated_at = now()
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                  AND step_id = $3::uuid
                RETURNING step_id::text AS step_id,
                          plan_id::text AS plan_id,
                          tenant_id::text AS tenant_id,
                          step_index,
                          title,
                          prompt_text,
                          idempotency_key,
                          status,
                          attempt_count,
                          max_attempts,
                          next_retry_at,
                          last_error_category,
                          last_error_detail,
                          output_json,
                          metadata,
                          created_at,
                          updated_at
                """,
                tenant_id,
                plan_id,
                step_id,
                json.dumps(output_json or {}),
            )
            if updated_step is None:
                raise RuntimeError("Failed to complete execution step")

            await conn.execute(
                """
                UPDATE tenant_execution_step_retries
                SET outcome = 'succeeded',
                    finished_at = now(),
                    failure_category = NULL,
                    failure_detail = NULL,
                    retry_backoff_seconds = NULL
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                  AND step_id = $3::uuid
                  AND retry_id = $4::uuid
                """,
                tenant_id,
                plan_id,
                step_id,
                retry_id,
            )
            await conn.execute(
                """
                INSERT INTO tenant_execution_transitions (
                    tenant_id,
                    plan_id,
                    step_id,
                    from_status,
                    to_status,
                    reason,
                    actor_sub,
                    metadata
                ) VALUES (
                    $1::uuid, $2::uuid, $3::uuid, $4, 'completed', 'step_completed', $5, '{}'::jsonb
                )
                """,
                tenant_id,
                plan_id,
                step_id,
                step_status_before,
                worker_id,
            )

            next_step = await conn.fetchrow(
                """
                SELECT step_index
                FROM tenant_execution_steps
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                  AND step_index > $3
                  AND status IN ('pending', 'failed', 'running', 'blocked')
                ORDER BY step_index ASC
                LIMIT 1
                """,
                tenant_id,
                plan_id,
                step_index,
            )

            if next_step is None:
                next_status = "completed"
                next_run_at: datetime | None = None
                next_step_index = step_index + 1
            else:
                next_status = "running"
                next_run_at = datetime.now(UTC) + timedelta(
                    seconds=max(1, continuation_seconds),
                )
                next_step_index = int(next_step["step_index"])

            updated_plan = await conn.fetchrow(
                """
                UPDATE tenant_execution_plans
                SET status = $3,
                    current_step_index = $4,
                    next_run_at = $5,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    updated_at = now()
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                RETURNING plan_id::text AS plan_id,
                          tenant_id::text AS tenant_id,
                          title,
                          goal,
                          status,
                          current_step_index,
                          total_steps,
                          max_step_attempts,
                          continuation_interval_seconds,
                          next_run_at,
                          lease_owner,
                          lease_expires_at,
                          metadata,
                          last_error_category,
                          last_error_detail,
                          created_by,
                          updated_by,
                          created_at,
                          updated_at
                """,
                tenant_id,
                plan_id,
                next_status,
                next_step_index,
                next_run_at,
            )
            if updated_plan is None:
                raise RuntimeError("Failed to update execution plan state")

            if plan_status_before != next_status:
                await conn.execute(
                    """
                    INSERT INTO tenant_execution_transitions (
                        tenant_id,
                        plan_id,
                        step_id,
                        from_status,
                        to_status,
                        reason,
                        actor_sub,
                        metadata
                    ) VALUES (
                        $1::uuid, $2::uuid, NULL, $3, $4, 'plan_progressed', $5, '{}'::jsonb
                    )
                    """,
                    tenant_id,
                    plan_id,
                    plan_status_before,
                    next_status,
                    worker_id,
                )

            return {
                "plan": dict(updated_plan),
                "step": dict(updated_step),
                "has_more": next_step is not None,
                "next_run_at": next_run_at,
            }

    async def fail_execution_step(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        step_id: str,
        retry_id: str,
        worker_id: str,
        failure_category: str,
        failure_detail: str,
        retryable: bool | None = None,
    ) -> dict[str, Any]:
        category = str(failure_category or "transient").strip().lower()
        if not category:
            category = "transient"
        detail = str(failure_detail or "").strip()[:4000]
        if retryable is None:
            retryable = category in RETRYABLE_EXECUTION_FAILURE_CATEGORIES

        async with self._pool.acquire() as conn, conn.transaction():
            plan_row = await conn.fetchrow(
                """
                SELECT status
                FROM tenant_execution_plans
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                FOR UPDATE
                """,
                tenant_id,
                plan_id,
            )
            if plan_row is None:
                raise ValueError("Execution plan not found")
            plan_status_before = str(plan_row["status"])

            step_row = await conn.fetchrow(
                """
                SELECT status,
                       attempt_count,
                       max_attempts
                FROM tenant_execution_steps
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                  AND step_id = $3::uuid
                FOR UPDATE
                """,
                tenant_id,
                plan_id,
                step_id,
            )
            if step_row is None:
                raise ValueError("Execution step not found")
            step_status_before = str(step_row["status"])
            attempts = int(step_row["attempt_count"])
            max_attempts = int(step_row["max_attempts"])
            should_retry = bool(retryable) and attempts < max_attempts

            backoff_seconds: int | None = None
            next_retry_at: datetime | None = None
            if should_retry:
                backoff_seconds = self._execution_retry_backoff_seconds(attempts)
                next_retry_at = datetime.now(UTC) + timedelta(seconds=backoff_seconds)
                step_status = "failed"
                plan_status = "running"
                retry_outcome = "retryable_failed"
            else:
                step_status = "blocked"
                plan_status = "failed"
                retry_outcome = "terminal_failed"

            updated_step = await conn.fetchrow(
                """
                UPDATE tenant_execution_steps
                SET status = $4,
                    next_retry_at = $5,
                    last_error_category = $6,
                    last_error_detail = $7,
                    updated_at = now()
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                  AND step_id = $3::uuid
                RETURNING step_id::text AS step_id,
                          plan_id::text AS plan_id,
                          tenant_id::text AS tenant_id,
                          step_index,
                          title,
                          prompt_text,
                          idempotency_key,
                          status,
                          attempt_count,
                          max_attempts,
                          next_retry_at,
                          last_error_category,
                          last_error_detail,
                          output_json,
                          metadata,
                          created_at,
                          updated_at
                """,
                tenant_id,
                plan_id,
                step_id,
                step_status,
                next_retry_at,
                category,
                detail,
            )
            if updated_step is None:
                raise RuntimeError("Failed to update execution step failure state")

            updated_plan = await conn.fetchrow(
                """
                UPDATE tenant_execution_plans
                SET status = $3,
                    next_run_at = $4,
                    lease_owner = NULL,
                    lease_expires_at = NULL,
                    last_error_category = $5,
                    last_error_detail = $6,
                    updated_at = now()
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                RETURNING plan_id::text AS plan_id,
                          tenant_id::text AS tenant_id,
                          title,
                          goal,
                          status,
                          current_step_index,
                          total_steps,
                          max_step_attempts,
                          continuation_interval_seconds,
                          next_run_at,
                          lease_owner,
                          lease_expires_at,
                          metadata,
                          last_error_category,
                          last_error_detail,
                          created_by,
                          updated_by,
                          created_at,
                          updated_at
                """,
                tenant_id,
                plan_id,
                plan_status,
                next_retry_at,
                category,
                detail,
            )
            if updated_plan is None:
                raise RuntimeError("Failed to update execution plan failure state")

            await conn.execute(
                """
                UPDATE tenant_execution_step_retries
                SET outcome = $5,
                    failure_category = $6,
                    failure_detail = $7,
                    retry_backoff_seconds = $8,
                    finished_at = now()
                WHERE tenant_id = $1::uuid
                  AND plan_id = $2::uuid
                  AND step_id = $3::uuid
                  AND retry_id = $4::uuid
                """,
                tenant_id,
                plan_id,
                step_id,
                retry_id,
                retry_outcome,
                category,
                detail,
                backoff_seconds,
            )

            await conn.execute(
                """
                INSERT INTO tenant_execution_transitions (
                    tenant_id,
                    plan_id,
                    step_id,
                    from_status,
                    to_status,
                    reason,
                    actor_sub,
                    metadata
                ) VALUES (
                    $1::uuid,
                    $2::uuid,
                    $3::uuid,
                    $4,
                    $5,
                    'step_failed',
                    $6,
                    $7::jsonb
                )
                """,
                tenant_id,
                plan_id,
                step_id,
                step_status_before,
                step_status,
                worker_id,
                json.dumps(
                    {
                        "failure_category": category,
                        "retryable": should_retry,
                        "backoff_seconds": backoff_seconds,
                    }
                ),
            )

            if plan_status_before != plan_status:
                await conn.execute(
                    """
                    INSERT INTO tenant_execution_transitions (
                        tenant_id,
                        plan_id,
                        step_id,
                        from_status,
                        to_status,
                        reason,
                        actor_sub,
                        metadata
                    ) VALUES (
                        $1::uuid, $2::uuid, NULL, $3, $4, 'plan_failure_state', $5, $6::jsonb
                    )
                    """,
                    tenant_id,
                    plan_id,
                    plan_status_before,
                    plan_status,
                    worker_id,
                    json.dumps({"failure_category": category}),
                )

            return {
                "plan": dict(updated_plan),
                "step": dict(updated_step),
                "retry_scheduled": should_retry,
                "next_run_at": next_retry_at,
                "backoff_seconds": backoff_seconds,
                "failure_category": category,
            }

    async def record_execution_artifact(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        step_id: str | None = None,
        retry_id: str | None = None,
        artifact_type: str,
        artifact_ref: str | None = None,
        artifact_json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        artifact_kind = str(artifact_type or "").strip().lower()
        if not artifact_kind:
            raise ValueError("Missing artifact_type")
        artifact_row = await self._fetchrow(
            """
            INSERT INTO tenant_execution_artifacts (
                artifact_id,
                tenant_id,
                plan_id,
                step_id,
                retry_id,
                artifact_type,
                artifact_ref,
                artifact_json
            ) VALUES (
                $1::uuid,
                $2::uuid,
                $3::uuid,
                $4::uuid,
                $5::uuid,
                $6,
                $7,
                $8::jsonb
            )
            RETURNING artifact_id::text AS artifact_id,
                      tenant_id::text AS tenant_id,
                      plan_id::text AS plan_id,
                      step_id::text AS step_id,
                      retry_id::text AS retry_id,
                      artifact_type,
                      artifact_ref,
                      artifact_json,
                      created_at
            """,
            str(uuid4()),
            tenant_id,
            plan_id,
            step_id,
            retry_id,
            artifact_kind,
            artifact_ref,
            json.dumps(artifact_json or {}),
        )
        if artifact_row is None:
            raise RuntimeError("Failed to record execution artifact")
        return dict(artifact_row)

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
