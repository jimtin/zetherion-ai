"""PostgreSQL storage for CGS gateway tenant/conversation/idempotency state."""

from __future__ import annotations

import json
import uuid
from collections.abc import Mapping
from typing import Any, cast

import asyncpg  # type: ignore[import-not-found,import-untyped]

from zetherion_ai.logging import get_logger
from zetherion_ai.security.encryption import FieldEncryptor

log = get_logger("zetherion_ai.cgs_gateway.storage")

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
    created_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at              TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_cgs_ai_tenants_zt_tenant
    ON cgs_ai_tenants (zetherion_tenant_id);

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
        await self._execute(_SCHEMA_SQL)
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
    ) -> dict[str, Any]:
        """Create or update a tenant mapping row."""
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
                is_active
            ) VALUES ($1, $2::uuid, $3, $4, $5, $6, $7::jsonb, TRUE)
            ON CONFLICT (cgs_tenant_id)
            DO UPDATE SET
                zetherion_tenant_id = EXCLUDED.zetherion_tenant_id,
                name = EXCLUDED.name,
                domain = EXCLUDED.domain,
                zetherion_api_key_enc = EXCLUDED.zetherion_api_key_enc,
                key_version = EXCLUDED.key_version,
                metadata = EXCLUDED.metadata,
                is_active = TRUE,
                updated_at = now()
            RETURNING cgs_tenant_id, zetherion_tenant_id, name, domain,
                      key_version, is_active, metadata, created_at, updated_at
            """,
            cgs_tenant_id,
            zetherion_tenant_id,
            name,
            domain,
            self._encrypt(zetherion_api_key),
            key_version,
            json.dumps(metadata or {}),
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
                   metadata, created_at, updated_at
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
                       key_version, is_active, metadata, created_at, updated_at
                FROM cgs_ai_tenants
                WHERE is_active = TRUE
                ORDER BY created_at
                """
            )
        else:
            rows = await self._fetch(
                """
                SELECT cgs_tenant_id, zetherion_tenant_id, name, domain,
                       key_version, is_active, metadata, created_at, updated_at
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

        if not sets:
            row = await self._fetchrow(
                """
                SELECT cgs_tenant_id, zetherion_tenant_id, name, domain,
                       key_version, is_active, metadata, created_at, updated_at
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
                      key_version, is_active, metadata, created_at, updated_at
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
                      key_version, is_active, metadata, created_at, updated_at
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
