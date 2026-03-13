"""Unit tests for trust data-plane helpers."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.trust.data_plane import (
    QdrantStoragePlane,
    _validate_schema_name,
    ensure_postgres_isolation_schemas,
    known_object_storage_prefixes,
    object_storage_prefix_for_domain,
    postgres_isolation_schema_map,
    qdrant_storage_plane_for_domain,
)
from zetherion_ai.trust.scope import TrustDomain


def test_qdrant_storage_plane_and_prefix_helpers_cover_owner_and_tenant_domains() -> None:
    assert qdrant_storage_plane_for_domain(TrustDomain.OWNER_PERSONAL) == QdrantStoragePlane.OWNER
    assert qdrant_storage_plane_for_domain(TrustDomain.OWNER_PORTFOLIO) == QdrantStoragePlane.OWNER
    assert qdrant_storage_plane_for_domain(TrustDomain.TENANT_RAW) == QdrantStoragePlane.TENANT
    assert object_storage_prefix_for_domain(TrustDomain.CONTROL_PLANE) == "control_plane"
    assert set(known_object_storage_prefixes()) == {domain.value for domain in TrustDomain}


def test_postgres_isolation_schema_map_supports_mapping_and_object_settings() -> None:
    mapping_result = postgres_isolation_schema_map(
        {
            "postgres_tenant_app_schema": " tenant_custom ",
            "postgres_owner_personal_schema": "",
            "postgres_owner_portfolio_schema": 123,
        }
    )
    assert mapping_result["tenant_app"] == "tenant_custom"
    assert mapping_result["owner_personal"] == "owner_personal"
    assert mapping_result["owner_portfolio"] == "owner_portfolio"

    object_result = postgres_isolation_schema_map(
        SimpleNamespace(
            postgres_tenant_app_schema="tenant_two",
            postgres_control_plane_schema=" control_two ",
        )
    )
    assert object_result["tenant_app"] == "tenant_two"
    assert object_result["control_plane"] == "control_two"
    assert object_result["cgs_gateway"] == "cgs_gateway"


def test_validate_schema_name_rejects_invalid_identifiers() -> None:
    assert _validate_schema_name(" tenant_app ") == "tenant_app"
    with pytest.raises(ValueError):
        _validate_schema_name("tenant-app")


class _AcquireContext:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


@pytest.mark.asyncio
async def test_ensure_postgres_isolation_schemas_handles_none_and_invalid_pools() -> None:
    assert await ensure_postgres_isolation_schemas(None, {}) == ()

    class MissingAcquire:
        pass

    class WrongAcquire:
        def acquire(self) -> None:
            return None

    assert await ensure_postgres_isolation_schemas(MissingAcquire(), {}) == ()
    assert await ensure_postgres_isolation_schemas(WrongAcquire(), {}) == ()


@pytest.mark.asyncio
async def test_ensure_postgres_isolation_schemas_creates_unique_validated_schemas() -> None:
    conn = AsyncMock()

    class Pool:
        def acquire(self) -> _AcquireContext:
            return _AcquireContext(conn)

    result = await ensure_postgres_isolation_schemas(
        Pool(),
        {
            "postgres_tenant_app_schema": "tenant_app",
            "postgres_owner_personal_schema": "owner_shared",
            "postgres_owner_portfolio_schema": "owner_shared",
            "postgres_control_plane_schema": "control_plane",
            "postgres_cgs_gateway_schema": "cgs_gateway",
        },
    )

    assert result == ("tenant_app", "owner_shared", "control_plane", "cgs_gateway")
    executed = [call.args[0] for call in conn.execute.await_args_list]
    assert executed == [
        'CREATE SCHEMA IF NOT EXISTS "tenant_app"',
        'CREATE SCHEMA IF NOT EXISTS "owner_shared"',
        'CREATE SCHEMA IF NOT EXISTS "control_plane"',
        'CREATE SCHEMA IF NOT EXISTS "cgs_gateway"',
    ]
