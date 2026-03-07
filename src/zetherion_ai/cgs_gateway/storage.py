"""PostgreSQL storage for CGS gateway tenant/conversation/idempotency state."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Mapping
from typing import Any, cast

import asyncpg  # type: ignore[import-not-found,import-untyped]

from zetherion_ai.logging import get_logger
from zetherion_ai.security.encryption import FieldEncryptor

log = get_logger("zetherion_ai.cgs_gateway.storage")

TENANT_ISOLATION_STAGES = (
    "legacy",
    "shadow",
    "dual_write",
    "cutover_ready",
    "isolated",
)
DEFAULT_TENANT_ISOLATION_STAGE = "legacy"


def _normalize_isolation_stage(value: str | None) -> str:
    stage = str(value or DEFAULT_TENANT_ISOLATION_STAGE).strip().lower()
    if stage not in TENANT_ISOLATION_STAGES:
        raise ValueError(f"Unsupported isolation stage: {value!r}")
    return stage


def _payload_fingerprint(payload: dict[str, Any]) -> str:
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS cgs_ai_tenants (
    id                      SERIAL PRIMARY KEY,
    cgs_tenant_id           VARCHAR(128) NOT NULL UNIQUE,
    zetherion_tenant_id     UUID NOT NULL,
    name                    TEXT NOT NULL,
    domain                  TEXT,
    zetherion_api_key_enc   TEXT NOT NULL,
    key_version             INT NOT NULL DEFAULT 1,
    is_active               BOOLEAN NOT NULL DEFAULT TRUE,
    metadata                JSONB DEFAULT '{}'::jsonb,
    isolation_stage         TEXT NOT NULL DEFAULT 'legacy'
                            CHECK (
                                isolation_stage IN (
                                    'legacy',
                                    'shadow',
                                    'dual_write',
                                    'cutover_ready',
                                    'isolated'
                                )
                            ),
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

ALTER TABLE cgs_ai_tenants
    ADD COLUMN IF NOT EXISTS isolation_stage TEXT NOT NULL DEFAULT 'legacy';
ALTER TABLE cgs_ai_tenants
    DROP CONSTRAINT IF EXISTS cgs_ai_tenants_isolation_stage_check;
ALTER TABLE cgs_ai_tenants
    ADD CONSTRAINT cgs_ai_tenants_isolation_stage_check
    CHECK (isolation_stage IN ('legacy', 'shadow', 'dual_write', 'cutover_ready', 'isolated'));

CREATE INDEX IF NOT EXISTS idx_cgs_ai_tenants_zt_tenant
    ON cgs_ai_tenants (zetherion_tenant_id);
CREATE INDEX IF NOT EXISTS idx_cgs_ai_tenants_isolation_stage
    ON cgs_ai_tenants (isolation_stage);

CREATE TABLE IF NOT EXISTS cgs_ai_conversations (
    id                          SERIAL PRIMARY KEY,
    conversation_id             VARCHAR(64) NOT NULL UNIQUE,
    cgs_tenant_id               VARCHAR(128) NOT NULL REFERENCES cgs_ai_tenants(cgs_tenant_id),
    app_user_id                 TEXT,
    external_user_id            TEXT,
    zetherion_session_id        UUID NOT NULL,
    zetherion_session_token_enc TEXT NOT NULL,
    metadata                    JSONB DEFAULT '{}'::jsonb,
    is_closed                   BOOLEAN NOT NULL DEFAULT FALSE,
    closed_at                   TIMESTAMPTZ,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cgs_ai_conversations_tenant
    ON cgs_ai_conversations (cgs_tenant_id, created_at DESC);

CREATE TABLE IF NOT EXISTS cgs_ai_idempotency (
    id                  SERIAL PRIMARY KEY,
    cgs_tenant_id       VARCHAR(128) NOT NULL REFERENCES cgs_ai_tenants(cgs_tenant_id),
    endpoint            TEXT NOT NULL,
    method              VARCHAR(10) NOT NULL,
    idempotency_key     VARCHAR(255) NOT NULL,
    request_fingerprint VARCHAR(64) NOT NULL,
    response_status     INT NOT NULL,
    response_body       JSONB NOT NULL,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (cgs_tenant_id, endpoint, method, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_cgs_ai_idempotency_created
    ON cgs_ai_idempotency (created_at DESC);

CREATE TABLE IF NOT EXISTS cgs_ai_request_log (
    id                  SERIAL PRIMARY KEY,
    request_id          VARCHAR(64) NOT NULL,
    cgs_tenant_id       VARCHAR(128),
    conversation_id     VARCHAR(64),
    endpoint            TEXT NOT NULL,
    method              VARCHAR(10) NOT NULL,
    upstream_status     INT,
    duration_ms         INT,
    error_code          VARCHAR(80),
    details             JSONB DEFAULT '{}'::jsonb,
    created_at          TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cgs_ai_request_log_request
    ON cgs_ai_request_log (request_id);

CREATE TABLE IF NOT EXISTS cgs_ai_admin_changes (
    change_id            VARCHAR(64) PRIMARY KEY,
    cgs_tenant_id        VARCHAR(128) NOT NULL REFERENCES cgs_ai_tenants(cgs_tenant_id),
    action               TEXT NOT NULL,
    target               TEXT,
    payload              JSONB NOT NULL DEFAULT '{}'::jsonb,
    payload_fingerprint  VARCHAR(64),
    status               VARCHAR(20) NOT NULL DEFAULT 'pending'
                         CHECK (status IN ('pending', 'approved', 'rejected', 'applied', 'failed')),
    requested_by         TEXT NOT NULL,
    approved_by          TEXT,
    reviewed_at          TIMESTAMPTZ,
    applied_at           TIMESTAMPTZ,
    request_id           VARCHAR(64),
    reason               TEXT,
    result               JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at           TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cgs_ai_admin_changes_tenant
    ON cgs_ai_admin_changes (cgs_tenant_id, created_at DESC);
CREATE INDEX IF NOT EXISTS idx_cgs_ai_admin_changes_status
    ON cgs_ai_admin_changes (status, created_at DESC);
ALTER TABLE cgs_ai_admin_changes
    ADD COLUMN IF NOT EXISTS payload_fingerprint VARCHAR(64);
CREATE UNIQUE INDEX IF NOT EXISTS idx_cgs_ai_admin_changes_pending_dedupe
    ON cgs_ai_admin_changes (cgs_tenant_id, action, COALESCE(target, ''), payload_fingerprint)
    WHERE status = 'pending';

CREATE TABLE IF NOT EXISTS cgs_ai_blog_publish_receipts (
    receipt_id            VARCHAR(64) PRIMARY KEY,
    idempotency_key       VARCHAR(80) NOT NULL UNIQUE,
    sha                   VARCHAR(64) NOT NULL UNIQUE,
    payload_fingerprint   VARCHAR(64) NOT NULL,
    source                TEXT NOT NULL,
    repo                  TEXT NOT NULL,
    release_tag           TEXT NOT NULL,
    title                 TEXT NOT NULL,
    slug                  TEXT NOT NULL,
    meta_description      TEXT NOT NULL,
    excerpt               TEXT,
    primary_keyword       TEXT NOT NULL,
    content_markdown      TEXT NOT NULL,
    json_ld               JSONB NOT NULL DEFAULT '{}'::jsonb,
    models                JSONB NOT NULL DEFAULT '{}'::jsonb,
    published_at          TIMESTAMPTZ NOT NULL,
    request_id            VARCHAR(64),
    created_at            TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cgs_ai_blog_publish_created
    ON cgs_ai_blog_publish_receipts (created_at DESC);
"""


class CGSGatewayStorage:
    """Storage abstraction for CGS gateway runtime/control-plane state."""

    def __init__(
        self,
        *,
        dsn: str,
        encryptor: FieldEncryptor | None = None,
        min_size: int = 1,
        max_size: int = 10,
    ) -> None:
        self._dsn = dsn
        self._encryptor = encryptor
        self._min_size = min_size
        self._max_size = max_size
        self._pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        self._pool = await asyncpg.create_pool(
            dsn=self._dsn,
            min_size=self._min_size,
            max_size=max(self._min_size, self._max_size),
        )
        await self._ensure_schema()
        log.info(
            "cgs_gateway_storage_initialized",
            min_size=self._min_size,
            max_size=self._max_size,
        )

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            log.info("cgs_gateway_storage_closed")

    def _encrypt(self, value: str) -> str:
        if self._encryptor is None:
            return value
        return self._encryptor.encrypt_value(value)

    def _decrypt(self, value: str) -> str:
        if self._encryptor is None:
            return value
        return self._encryptor.decrypt_value(value)

    async def upsert_tenant_mapping(
        self,
        *,
        cgs_tenant_id: str,
        zetherion_tenant_id: str,
        name: str,
        domain: str | None,
        zetherion_api_key: str,
        metadata: dict[str, Any] | None = None,
        key_version: int = 1,
        isolation_stage: str = DEFAULT_TENANT_ISOLATION_STAGE,
    ) -> dict[str, Any]:
        """Create or update a tenant mapping row."""
        normalized_stage = _normalize_isolation_stage(isolation_stage)
        row = await self._fetchrow(
            """
            INSERT INTO cgs_ai_tenants (
                cgs_tenant_id,
                zetherion_tenant_id,
                name,
                domain,
                zetherion_api_key_enc,
                key_version,
                metadata,
                isolation_stage,
                is_active
            ) VALUES ($1, $2::uuid, $3, $4, $5, $6, $7::jsonb, $8, TRUE)
            ON CONFLICT (cgs_tenant_id)
            DO UPDATE SET
                zetherion_tenant_id = EXCLUDED.zetherion_tenant_id,
                name = EXCLUDED.name,
                domain = EXCLUDED.domain,
                zetherion_api_key_enc = EXCLUDED.zetherion_api_key_enc,
                key_version = EXCLUDED.key_version,
                metadata = EXCLUDED.metadata,
                isolation_stage = EXCLUDED.isolation_stage,
                is_active = TRUE,
                updated_at = now()
            RETURNING cgs_tenant_id, zetherion_tenant_id, name, domain,
                      key_version, is_active, metadata, isolation_stage, created_at, updated_at
            """,
            cgs_tenant_id,
            zetherion_tenant_id,
            name,
            domain,
            self._encrypt(zetherion_api_key),
            key_version,
            json.dumps(metadata or {}),
            normalized_stage,
        )
        if row is None:
            raise RuntimeError("Upsert tenant mapping returned no row")
        return dict(cast(Mapping[str, Any], row))

    async def get_tenant_mapping(self, cgs_tenant_id: str) -> dict[str, Any] | None:
        """Fetch one tenant mapping including decrypted API key."""
        row = await self._fetchrow(
            """
            SELECT cgs_tenant_id, zetherion_tenant_id, name, domain,
                   zetherion_api_key_enc, key_version, is_active,
                   metadata, isolation_stage, created_at, updated_at
            FROM cgs_ai_tenants
            WHERE cgs_tenant_id = $1
            """,
            cgs_tenant_id,
        )
        if row is None:
            return None
        result = dict(row)
        result["zetherion_api_key"] = self._decrypt(str(result.pop("zetherion_api_key_enc")))
        return result

    async def list_tenant_mappings(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        """List tenant mappings (without exposing decrypted API keys)."""
        if active_only:
            rows = await self._fetch(
                """
                SELECT cgs_tenant_id, zetherion_tenant_id, name, domain,
                       key_version, is_active, metadata, isolation_stage, created_at, updated_at
                FROM cgs_ai_tenants
                WHERE is_active = TRUE
                ORDER BY created_at
                """
            )
        else:
            rows = await self._fetch(
                """
                SELECT cgs_tenant_id, zetherion_tenant_id, name, domain,
                       key_version, is_active, metadata, isolation_stage, created_at, updated_at
                FROM cgs_ai_tenants
                ORDER BY created_at
                """
            )
        return [dict(r) for r in rows]

    async def update_tenant_profile(
        self,
        *,
        cgs_tenant_id: str,
        name: str | None = None,
        domain: str | None = None,
        metadata: dict[str, Any] | None = None,
        isolation_stage: str | None = None,
    ) -> dict[str, Any] | None:
        """Update non-secret tenant mapping fields."""
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
        if metadata is not None:
            sets.append(f"metadata = ${idx}::jsonb")
            args.append(json.dumps(metadata))
            idx += 1
        if isolation_stage is not None:
            sets.append(f"isolation_stage = ${idx}")
            args.append(_normalize_isolation_stage(isolation_stage))
            idx += 1

        if not sets:
            row = await self._fetchrow(
                """
                SELECT cgs_tenant_id, zetherion_tenant_id, name, domain,
                       key_version, is_active, metadata, isolation_stage, created_at, updated_at
                FROM cgs_ai_tenants
                WHERE cgs_tenant_id = $1
                """,
                cgs_tenant_id,
            )
            return dict(row) if row else None

        sets.append("updated_at = now()")
        args.append(cgs_tenant_id)
        row = await self._fetchrow(
            f"""
            UPDATE cgs_ai_tenants
            SET {", ".join(sets)}
            WHERE cgs_tenant_id = ${idx}
            RETURNING cgs_tenant_id, zetherion_tenant_id, name, domain,
                      key_version, is_active, metadata, isolation_stage, created_at, updated_at
            """,  # nosec B608
            *args,
        )
        return dict(row) if row else None

    async def rotate_tenant_api_key(
        self,
        *,
        cgs_tenant_id: str,
        new_api_key: str,
    ) -> dict[str, Any] | None:
        """Persist new encrypted upstream API key and bump key version."""
        row = await self._fetchrow(
            """
            UPDATE cgs_ai_tenants
            SET zetherion_api_key_enc = $1,
                key_version = key_version + 1,
                updated_at = now()
            WHERE cgs_tenant_id = $2
            RETURNING cgs_tenant_id, zetherion_tenant_id, name, domain,
                      key_version, is_active, metadata, isolation_stage, created_at, updated_at
            """,
            self._encrypt(new_api_key),
            cgs_tenant_id,
        )
        return dict(row) if row else None

    async def deactivate_tenant_mapping(self, cgs_tenant_id: str) -> bool:
        """Deactivate a CGS tenant mapping row."""
        result = await self._execute(
            """
            UPDATE cgs_ai_tenants
            SET is_active = FALSE, updated_at = now()
            WHERE cgs_tenant_id = $1
            """,
            cgs_tenant_id,
        )
        return result == "UPDATE 1"

    async def list_tenant_reconciliation_candidates(self) -> list[dict[str, Any]]:
        """Return tenant mappings that still need isolation rollout or baseline checks."""
        rows = await self._fetch(
            """
            SELECT cgs_tenant_id, zetherion_tenant_id, name, domain,
                   key_version, is_active, metadata, isolation_stage, created_at, updated_at
            FROM cgs_ai_tenants
            WHERE is_active = TRUE
              AND (
                    isolation_stage <> 'isolated'
                    OR COALESCE(
                         metadata->'provisioning'->>'owner_portfolio_ready',
                         'false'
                       ) <> 'true'
                    OR metadata->'provisioning'->>'last_reconciled_at' IS NULL
                  )
            ORDER BY created_at
            """
        )
        return [dict(r) for r in rows]

    async def get_tenant_mapping_by_zetherion_tenant_id(
        self, zetherion_tenant_id: str
    ) -> dict[str, Any] | None:
        """Fetch a tenant mapping using the upstream tenant id."""
        row = await self._fetchrow(
            """
            SELECT cgs_tenant_id, zetherion_tenant_id, name, domain,
                   zetherion_api_key_enc, key_version, is_active,
                   metadata, isolation_stage, created_at, updated_at
            FROM cgs_ai_tenants
            WHERE zetherion_tenant_id = $1::uuid
            """,
            zetherion_tenant_id,
        )
        if row is None:
            return None
        result = dict(row)
        result["zetherion_api_key"] = self._decrypt(str(result.pop("zetherion_api_key_enc")))
        return result

    async def create_conversation(
        self,
        *,
        cgs_tenant_id: str,
        zetherion_session_id: str,
        zetherion_session_token: str,
        app_user_id: str | None = None,
        external_user_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        conversation_id: str | None = None,
    ) -> dict[str, Any]:
        """Store CGS conversation -> Zetherion session mapping."""
        conv_id = conversation_id or f"cgs_conv_{uuid.uuid4().hex[:24]}"
        row = await self._fetchrow(
            """
            INSERT INTO cgs_ai_conversations (
                conversation_id,
                cgs_tenant_id,
                app_user_id,
                external_user_id,
                zetherion_session_id,
                zetherion_session_token_enc,
                metadata
            ) VALUES ($1, $2, $3, $4, $5::uuid, $6, $7::jsonb)
            RETURNING conversation_id, cgs_tenant_id, app_user_id, external_user_id,
                      zetherion_session_id, is_closed, closed_at, metadata,
                      created_at, updated_at
            """,
            conv_id,
            cgs_tenant_id,
            app_user_id,
            external_user_id,
            zetherion_session_id,
            self._encrypt(zetherion_session_token),
            json.dumps(metadata or {}),
        )
        if row is None:
            raise RuntimeError("Create conversation returned no row")
        return dict(cast(Mapping[str, Any], row))

    async def get_conversation(self, conversation_id: str) -> dict[str, Any] | None:
        """Fetch conversation mapping with decrypted session token and API key."""
        row = await self._fetchrow(
            """
            SELECT c.conversation_id, c.cgs_tenant_id, c.app_user_id, c.external_user_id,
                   c.zetherion_session_id, c.zetherion_session_token_enc,
                   c.is_closed, c.closed_at, c.metadata, c.created_at, c.updated_at,
                   t.zetherion_tenant_id, t.name, t.domain, t.zetherion_api_key_enc,
                   t.key_version, t.is_active
            FROM cgs_ai_conversations c
            JOIN cgs_ai_tenants t
              ON t.cgs_tenant_id = c.cgs_tenant_id
            WHERE c.conversation_id = $1
            """,
            conversation_id,
        )
        if row is None:
            return None
        result = dict(row)
        result["zetherion_session_token"] = self._decrypt(
            str(result.pop("zetherion_session_token_enc"))
        )
        result["zetherion_api_key"] = self._decrypt(str(result.pop("zetherion_api_key_enc")))
        return result

    async def close_conversation(self, conversation_id: str) -> bool:
        """Mark conversation closed."""
        result = await self._execute(
            """
            UPDATE cgs_ai_conversations
            SET is_closed = TRUE, closed_at = now(), updated_at = now()
            WHERE conversation_id = $1
            """,
            conversation_id,
        )
        return result == "UPDATE 1"

    async def get_idempotency_record(
        self,
        *,
        cgs_tenant_id: str,
        endpoint: str,
        method: str,
        idempotency_key: str,
    ) -> dict[str, Any] | None:
        """Return existing idempotency record for this tenant/method/endpoint/key."""
        row = await self._fetchrow(
            """
            SELECT cgs_tenant_id, endpoint, method, idempotency_key,
                   request_fingerprint, response_status, response_body, created_at
            FROM cgs_ai_idempotency
            WHERE cgs_tenant_id = $1
              AND endpoint = $2
              AND method = $3
              AND idempotency_key = $4
            """,
            cgs_tenant_id,
            endpoint,
            method,
            idempotency_key,
        )
        return dict(row) if row else None

    async def save_idempotency_record(
        self,
        *,
        cgs_tenant_id: str,
        endpoint: str,
        method: str,
        idempotency_key: str,
        request_fingerprint: str,
        response_status: int,
        response_body: dict[str, Any],
    ) -> None:
        """Persist idempotent response."""
        await self._execute(
            """
            INSERT INTO cgs_ai_idempotency (
                cgs_tenant_id,
                endpoint,
                method,
                idempotency_key,
                request_fingerprint,
                response_status,
                response_body
            ) VALUES ($1, $2, $3, $4, $5, $6, $7::jsonb)
            ON CONFLICT (cgs_tenant_id, endpoint, method, idempotency_key)
            DO NOTHING
            """,
            cgs_tenant_id,
            endpoint,
            method,
            idempotency_key,
            request_fingerprint,
            int(response_status),
            json.dumps(response_body),
        )

    async def log_request(
        self,
        *,
        request_id: str,
        endpoint: str,
        method: str,
        cgs_tenant_id: str | None,
        conversation_id: str | None,
        upstream_status: int | None,
        duration_ms: int | None,
        error_code: str | None,
        details: dict[str, Any] | None = None,
    ) -> None:
        """Write a request trace row for attribution/debugging."""
        await self._execute(
            """
            INSERT INTO cgs_ai_request_log (
                request_id, cgs_tenant_id, conversation_id,
                endpoint, method, upstream_status, duration_ms,
                error_code, details
            ) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb)
            """,
            request_id,
            cgs_tenant_id,
            conversation_id,
            endpoint,
            method,
            upstream_status,
            duration_ms,
            error_code,
            json.dumps(details or {}),
        )

    async def create_admin_change(
        self,
        *,
        cgs_tenant_id: str,
        action: str,
        target: str | None,
        payload: dict[str, Any],
        requested_by: str,
        request_id: str | None,
        reason: str | None = None,
    ) -> dict[str, Any]:
        """Insert a pending admin change."""
        payload_fp = _payload_fingerprint(payload)
        existing = await self._fetchrow(
            """
            SELECT change_id, cgs_tenant_id, action, target, payload, payload_fingerprint, status,
                   requested_by, approved_by, reviewed_at, applied_at, request_id,
                   reason, result, created_at, updated_at
            FROM cgs_ai_admin_changes
            WHERE cgs_tenant_id = $1
              AND action = $2
              AND COALESCE(target, '') = COALESCE($3, '')
              AND payload_fingerprint = $4
              AND status = 'pending'
            ORDER BY created_at DESC
            LIMIT 1
            """,
            cgs_tenant_id,
            action,
            target,
            payload_fp,
        )
        if existing is not None:
            duplicate = dict(existing)
            duplicate["duplicate"] = True
            return duplicate

        change_id = f"chg_{uuid.uuid4().hex[:24]}"
        row = await self._fetchrow(
            """
            INSERT INTO cgs_ai_admin_changes (
                change_id,
                cgs_tenant_id,
                action,
                target,
                payload,
                payload_fingerprint,
                status,
                requested_by,
                request_id,
                reason
            ) VALUES ($1, $2, $3, $4, $5::jsonb, $6, 'pending', $7, $8, $9)
            RETURNING change_id, cgs_tenant_id, action, target, payload,
                      payload_fingerprint, status,
                      requested_by, approved_by, reviewed_at, applied_at, request_id,
                      reason, result, created_at, updated_at
            """,
            change_id,
            cgs_tenant_id,
            action,
            target,
            json.dumps(payload),
            payload_fp,
            requested_by,
            request_id,
            reason,
        )
        if row is None:
            raise RuntimeError("Failed to create admin change")
        return dict(row)

    async def get_admin_change(self, change_id: str) -> dict[str, Any] | None:
        """Fetch one admin change by id."""
        row = await self._fetchrow(
            """
            SELECT change_id, cgs_tenant_id, action, target, payload, payload_fingerprint, status,
                   requested_by, approved_by, reviewed_at, applied_at, request_id,
                   reason, result, created_at, updated_at
            FROM cgs_ai_admin_changes
            WHERE change_id = $1
            """,
            change_id,
        )
        return dict(row) if row else None

    async def list_admin_changes(
        self,
        *,
        cgs_tenant_id: str,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """List admin changes for one tenant, newest-first."""
        if status:
            rows = await self._fetch(
                """
                SELECT change_id, cgs_tenant_id, action, target, payload,
                       payload_fingerprint, status,
                       requested_by, approved_by, reviewed_at, applied_at, request_id,
                       reason, result, created_at, updated_at
                FROM cgs_ai_admin_changes
                WHERE cgs_tenant_id = $1
                  AND status = $2
                ORDER BY created_at DESC
                LIMIT $3
                """,
                cgs_tenant_id,
                status,
                limit,
            )
        else:
            rows = await self._fetch(
                """
                SELECT change_id, cgs_tenant_id, action, target, payload,
                       payload_fingerprint, status,
                       requested_by, approved_by, reviewed_at, applied_at, request_id,
                       reason, result, created_at, updated_at
                FROM cgs_ai_admin_changes
                WHERE cgs_tenant_id = $1
                ORDER BY created_at DESC
                LIMIT $2
                """,
                cgs_tenant_id,
                limit,
            )
        return [dict(row) for row in rows]

    async def approve_admin_change(
        self,
        *,
        change_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Mark a pending admin change approved."""
        row = await self._fetchrow(
            """
            UPDATE cgs_ai_admin_changes
            SET status = 'approved',
                approved_by = $2,
                reviewed_at = now(),
                reason = COALESCE($3, reason),
                updated_at = now()
            WHERE change_id = $1
              AND status = 'pending'
            RETURNING change_id, cgs_tenant_id, action, target, payload,
                      payload_fingerprint, status,
                      requested_by, approved_by, reviewed_at, applied_at, request_id,
                      reason, result, created_at, updated_at
            """,
            change_id,
            approved_by,
            reason,
        )
        return dict(row) if row else None

    async def reject_admin_change(
        self,
        *,
        change_id: str,
        approved_by: str,
        reason: str | None = None,
    ) -> dict[str, Any] | None:
        """Mark a pending admin change rejected."""
        row = await self._fetchrow(
            """
            UPDATE cgs_ai_admin_changes
            SET status = 'rejected',
                approved_by = $2,
                reviewed_at = now(),
                reason = COALESCE($3, reason),
                updated_at = now()
            WHERE change_id = $1
              AND status = 'pending'
            RETURNING change_id, cgs_tenant_id, action, target, payload,
                      payload_fingerprint, status,
                      requested_by, approved_by, reviewed_at, applied_at, request_id,
                      reason, result, created_at, updated_at
            """,
            change_id,
            approved_by,
            reason,
        )
        return dict(row) if row else None

    async def mark_admin_change_applied(
        self,
        *,
        change_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Mark an approved change as applied, idempotently."""
        row = await self._fetchrow(
            """
            UPDATE cgs_ai_admin_changes
            SET status = 'applied',
                applied_at = now(),
                result = $2::jsonb,
                updated_at = now()
            WHERE change_id = $1
              AND status = 'approved'
            RETURNING change_id, cgs_tenant_id, action, target, payload,
                      payload_fingerprint, status,
                      requested_by, approved_by, reviewed_at, applied_at, request_id,
                      reason, result, created_at, updated_at
            """,
            change_id,
            json.dumps(result),
        )
        if row is not None:
            return dict(row)
        current = await self.get_admin_change(change_id)
        if current is not None and str(current.get("status")) == "applied":
            return current
        return None

    async def mark_admin_change_failed(
        self,
        *,
        change_id: str,
        result: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Mark an approved change as failed, idempotently."""
        row = await self._fetchrow(
            """
            UPDATE cgs_ai_admin_changes
            SET status = 'failed',
                result = $2::jsonb,
                updated_at = now()
            WHERE change_id = $1
              AND status = 'approved'
            RETURNING change_id, cgs_tenant_id, action, target, payload,
                      payload_fingerprint, status,
                      requested_by, approved_by, reviewed_at, applied_at, request_id,
                      reason, result, created_at, updated_at
            """,
            change_id,
            json.dumps(result),
        )
        if row is not None:
            return dict(row)
        current = await self.get_admin_change(change_id)
        if current is not None and str(current.get("status")) == "failed":
            return current
        return None

    async def find_blog_publish_receipt(
        self,
        *,
        idempotency_key: str,
        sha: str,
    ) -> dict[str, Any] | None:
        """Lookup a publish receipt by idempotency key or SHA."""
        row = await self._fetchrow(
            """
            SELECT receipt_id, idempotency_key, sha, payload_fingerprint, source, repo,
                   release_tag, title, slug, meta_description, excerpt, primary_keyword,
                   content_markdown, json_ld, models, published_at, request_id, created_at
            FROM cgs_ai_blog_publish_receipts
            WHERE idempotency_key = $1 OR sha = $2
            ORDER BY created_at DESC
            LIMIT 1
            """,
            idempotency_key,
            sha,
        )
        return dict(row) if row else None

    async def create_blog_publish_receipt(
        self,
        *,
        idempotency_key: str,
        payload_fingerprint: str,
        source: str,
        sha: str,
        repo: str,
        release_tag: str,
        title: str,
        slug: str,
        meta_description: str,
        excerpt: str | None,
        primary_keyword: str,
        content_markdown: str,
        json_ld: dict[str, Any],
        models: dict[str, str],
        published_at: str,
        request_id: str | None,
    ) -> dict[str, Any]:
        """Insert publish receipt; return existing row if duplicate race occurs."""
        receipt_id = f"blog_{uuid.uuid4().hex[:24]}"
        try:
            row = await self._fetchrow(
                """
                INSERT INTO cgs_ai_blog_publish_receipts (
                    receipt_id,
                    idempotency_key,
                    sha,
                    payload_fingerprint,
                    source,
                    repo,
                    release_tag,
                    title,
                    slug,
                    meta_description,
                    excerpt,
                    primary_keyword,
                    content_markdown,
                    json_ld,
                    models,
                    published_at,
                    request_id
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14::jsonb, $15::jsonb, $16::timestamptz, $17
                )
                RETURNING receipt_id, idempotency_key, sha, payload_fingerprint, source, repo,
                          release_tag, title, slug, meta_description, excerpt, primary_keyword,
                          content_markdown, json_ld, models, published_at, request_id, created_at
                """,
                receipt_id,
                idempotency_key,
                sha,
                payload_fingerprint,
                source,
                repo,
                release_tag,
                title,
                slug,
                meta_description,
                excerpt,
                primary_keyword,
                content_markdown,
                json.dumps(json_ld),
                json.dumps(models),
                published_at,
                request_id,
            )
        except asyncpg.UniqueViolationError:
            existing = await self.find_blog_publish_receipt(
                idempotency_key=idempotency_key,
                sha=sha,
            )
            if existing is None:
                raise
            return existing

        if row is None:
            raise RuntimeError("Failed to create blog publish receipt")
        return dict(row)

    async def _ensure_schema(self) -> None:
        """Create gateway schema objects if absent.

        Multiple blue/green gateway instances can start at the same time during
        deploy and race on ``CREATE ... IF NOT EXISTS`` statements. PostgreSQL can
        surface this as ``UniqueViolationError`` on ``pg_type_typname_nsp_index``;
        treat it as safe because another process completed the DDL first.
        """
        try:
            await self._execute(_SCHEMA_SQL)
            log.info("cgs_gateway_schema_ensured")
        except asyncpg.UniqueViolationError:
            log.info("cgs_gateway_schema_ensured", note="concurrent_creation_resolved")
        except asyncpg.PostgresError as exc:
            log.error("cgs_gateway_schema_creation_failed", error=str(exc))
            raise

    async def _fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        if self._pool is None:
            raise RuntimeError("CGS gateway storage is not initialized")
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def _fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        if self._pool is None:
            raise RuntimeError("CGS gateway storage is not initialized")
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *args)
            return cast(list[asyncpg.Record], rows)

    async def _execute(self, query: str, *args: Any) -> str:
        if self._pool is None:
            raise RuntimeError("CGS gateway storage is not initialized")
        async with self._pool.acquire() as conn:
            result = await conn.execute(query, *args)
            return cast(str, result)
