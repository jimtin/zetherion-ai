"""Tests for owner portfolio storage wrappers."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.portfolio.storage import PortfolioStorage, _schema_sql


class _AcquireContext:
    def __init__(self, connection: MagicMock) -> None:
        self._connection = connection

    async def __aenter__(self) -> MagicMock:
        return self._connection

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


def test_schema_sql_rejects_invalid_identifier() -> None:
    with pytest.raises(ValueError):
        _schema_sql("owner-portfolio")


@pytest.mark.asyncio
async def test_initialize_is_idempotent(monkeypatch: pytest.MonkeyPatch) -> None:
    fake_pool = MagicMock()
    fake_pool.close = AsyncMock()
    create_pool = AsyncMock(return_value=fake_pool)
    ensure_schema = AsyncMock()
    monkeypatch.setattr("zetherion_ai.portfolio.storage.asyncpg.create_pool", create_pool)
    monkeypatch.setattr(PortfolioStorage, "_ensure_schema", ensure_schema)

    storage = PortfolioStorage(dsn="postgresql://example")
    await storage.initialize()
    await storage.initialize()

    create_pool.assert_awaited_once()
    ensure_schema.assert_awaited_once()
    assert storage._pool is fake_pool


@pytest.mark.asyncio
async def test_close_handles_pool_and_noop() -> None:
    storage = PortfolioStorage(dsn="postgresql://example")
    await storage.close()

    fake_pool = MagicMock()
    fake_pool.close = AsyncMock()
    storage._pool = fake_pool

    await storage.close()

    fake_pool.close.assert_awaited_once()
    assert storage._pool is None


@pytest.mark.asyncio
async def test_internal_helpers_require_initialization() -> None:
    storage = PortfolioStorage(dsn="postgresql://example")

    with pytest.raises(RuntimeError, match="not initialized"):
        await storage._execute("SELECT 1")
    with pytest.raises(RuntimeError, match="not initialized"):
        await storage._fetchrow("SELECT 1")
    with pytest.raises(RuntimeError, match="not initialized"):
        await storage._fetch("SELECT 1")


@pytest.mark.asyncio
async def test_internal_helpers_use_connection_methods() -> None:
    storage = PortfolioStorage(dsn="postgresql://example")
    connection = MagicMock()
    connection.execute = AsyncMock(return_value="EXECUTED")
    connection.fetchrow = AsyncMock(return_value={"row": 1})
    connection.fetch = AsyncMock(return_value=[{"row": 2}])
    pool = MagicMock()
    pool.acquire.return_value = _AcquireContext(connection)
    storage._pool = pool

    await storage._ensure_schema()
    status = await storage._execute("SELECT 1", "arg")
    row = await storage._fetchrow("SELECT 2", "arg")
    rows = await storage._fetch("SELECT 3", "arg")

    assert status == "EXECUTED"
    assert row == {"row": 1}
    assert rows == [{"row": 2}]
    connection.execute.assert_any_await(_schema_sql("owner_portfolio"))
    connection.execute.assert_any_await("SELECT 1", "arg")
    connection.fetchrow.assert_awaited_once_with("SELECT 2", "arg")
    connection.fetch.assert_awaited_once_with("SELECT 3", "arg")


@pytest.mark.asyncio
async def test_upsert_tenant_derived_dataset_uses_scoped_table() -> None:
    storage = PortfolioStorage(dsn="postgresql://example", owner_portfolio_schema="owner_portfolio")
    storage._pool = object()
    storage._fetchrow = AsyncMock(
        return_value={
            "dataset_id": "tds_1",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "tenant_name": "Bob's Plumbing",
            "derivation_kind": "tenant_health_summary",
            "trust_domain": "tenant_derived",
            "source": "test",
            "summary": {"tenant_name": "Bob's Plumbing"},
            "provenance": {"input_trust_domain": "tenant_raw"},
            "created_at": "created",
            "updated_at": "updated",
        }
    )

    row = await storage.upsert_tenant_derived_dataset(
        zetherion_tenant_id="11111111-1111-1111-1111-111111111111",
        tenant_name="Bob's Plumbing",
        derivation_kind="tenant_health_summary",
        source="test",
        summary={"tenant_name": "Bob's Plumbing"},
        provenance={"input_trust_domain": "tenant_raw"},
    )

    query = storage._fetchrow.await_args.args[0]
    assert '"owner_portfolio".tenant_derived_datasets' in query
    assert row["dataset_id"] == "tds_1"


@pytest.mark.asyncio
async def test_get_and_list_tenant_derived_dataset_use_scoped_table() -> None:
    storage = PortfolioStorage(dsn="postgresql://example", owner_portfolio_schema="owner_portfolio")
    storage._pool = object()
    storage._fetchrow = AsyncMock(
        return_value={
            "dataset_id": "tds_1",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "tenant_name": "Bob's Plumbing",
            "derivation_kind": "tenant_health_summary",
            "trust_domain": "tenant_derived",
            "source": "test",
            "summary": {"tenant_name": "Bob's Plumbing"},
            "provenance": {"input_trust_domain": "tenant_raw"},
            "created_at": "created",
            "updated_at": "updated",
        }
    )
    storage._fetch = AsyncMock(
        return_value=[
            {
                "dataset_id": "tds_1",
                "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
                "tenant_name": "Bob's Plumbing",
                "derivation_kind": "tenant_health_summary",
                "trust_domain": "tenant_derived",
                "source": "test",
                "summary": {"tenant_name": "Bob's Plumbing"},
                "provenance": {"input_trust_domain": "tenant_raw"},
                "created_at": "created",
                "updated_at": "updated",
            }
        ]
    )

    fetched = await storage.get_tenant_derived_dataset(
        zetherion_tenant_id="11111111-1111-1111-1111-111111111111",
        derivation_kind="tenant_health_summary",
    )
    listed = await storage.list_tenant_derived_datasets(derivation_kind="tenant_health_summary")
    listed_unfiltered = await storage.list_tenant_derived_datasets()

    get_query = storage._fetchrow.await_args.args[0]
    list_query = storage._fetch.await_args_list[0].args[0]
    list_unfiltered_query = storage._fetch.await_args_list[1].args[0]
    assert 'FROM "owner_portfolio".tenant_derived_datasets' in get_query
    assert 'FROM "owner_portfolio".tenant_derived_datasets' in list_query
    assert "WHERE derivation_kind = $1" in list_query
    assert "WHERE derivation_kind = $1" not in list_unfiltered_query
    assert fetched is not None
    assert fetched["dataset_id"] == "tds_1"
    assert listed[0]["tenant_name"] == "Bob's Plumbing"
    assert listed_unfiltered[0]["tenant_name"] == "Bob's Plumbing"


@pytest.mark.asyncio
async def test_upsert_tenant_derived_dataset_requires_row() -> None:
    storage = PortfolioStorage(dsn="postgresql://example")
    storage._pool = object()
    storage._fetchrow = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="returned no row"):
        await storage.upsert_tenant_derived_dataset(
            zetherion_tenant_id="11111111-1111-1111-1111-111111111111",
            tenant_name="Bob's Plumbing",
            derivation_kind="tenant_health_summary",
            source="test",
            summary={},
            provenance=None,
        )


@pytest.mark.asyncio
async def test_upsert_owner_portfolio_snapshot_and_list() -> None:
    storage = PortfolioStorage(dsn="postgresql://example", owner_portfolio_schema="owner_portfolio")
    storage._pool = object()
    storage._fetchrow = AsyncMock(
        return_value={
            "snapshot_id": "ops_1",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "tenant_name": "Bob's Plumbing",
            "derivation_kind": "tenant_health_summary",
            "trust_domain": "owner_portfolio",
            "source_dataset_id": "tds_1",
            "source": "test",
            "summary": {"tenant_name": "Bob's Plumbing"},
            "provenance": {"output_trust_domain": "owner_portfolio"},
            "created_at": "created",
            "updated_at": "updated",
        }
    )
    storage._fetch = AsyncMock(
        return_value=[
            {
                "snapshot_id": "ops_1",
                "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
                "tenant_name": "Bob's Plumbing",
                "derivation_kind": "tenant_health_summary",
                "trust_domain": "owner_portfolio",
                "source_dataset_id": "tds_1",
                "source": "test",
                "summary": {"tenant_name": "Bob's Plumbing"},
                "provenance": {"output_trust_domain": "owner_portfolio"},
                "created_at": "created",
                "updated_at": "updated",
            }
        ]
    )

    snapshot = await storage.upsert_owner_portfolio_snapshot(
        zetherion_tenant_id="11111111-1111-1111-1111-111111111111",
        tenant_name="Bob's Plumbing",
        derivation_kind="tenant_health_summary",
        source_dataset_id="tds_1",
        source="test",
        summary={"tenant_name": "Bob's Plumbing"},
        provenance={"output_trust_domain": "owner_portfolio"},
    )
    fetched = await storage.get_owner_portfolio_snapshot(
        zetherion_tenant_id="11111111-1111-1111-1111-111111111111",
        derivation_kind="tenant_health_summary",
    )
    listed = await storage.list_owner_portfolio_snapshots(derivation_kind="tenant_health_summary")
    listed_unfiltered = await storage.list_owner_portfolio_snapshots()

    snapshot_query = storage._fetchrow.await_args_list[0].args[0]
    get_query = storage._fetchrow.await_args_list[1].args[0]
    list_query = storage._fetch.await_args_list[0].args[0]
    list_unfiltered_query = storage._fetch.await_args_list[1].args[0]
    assert '"owner_portfolio".owner_portfolio_tenant_snapshots' in snapshot_query
    assert '"owner_portfolio".owner_portfolio_tenant_snapshots' in get_query
    assert 'FROM "owner_portfolio".owner_portfolio_tenant_snapshots' in list_query
    assert "WHERE derivation_kind = $1" in list_query
    assert "WHERE derivation_kind = $1" not in list_unfiltered_query
    assert snapshot["snapshot_id"] == "ops_1"
    assert fetched is not None
    assert fetched["source_dataset_id"] == "tds_1"
    assert listed[0]["tenant_name"] == "Bob's Plumbing"
    assert listed_unfiltered[0]["tenant_name"] == "Bob's Plumbing"


@pytest.mark.asyncio
async def test_upsert_owner_portfolio_snapshot_requires_row() -> None:
    storage = PortfolioStorage(dsn="postgresql://example")
    storage._pool = object()
    storage._fetchrow = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="returned no row"):
        await storage.upsert_owner_portfolio_snapshot(
            zetherion_tenant_id="11111111-1111-1111-1111-111111111111",
            tenant_name="Bob's Plumbing",
            derivation_kind="tenant_health_summary",
            source_dataset_id="tds_1",
            source="test",
            summary={},
            provenance=None,
        )
