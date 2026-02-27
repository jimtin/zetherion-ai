"""Unit tests for CGS gateway storage logic."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import zetherion_ai.cgs_gateway.storage as storage_mod
from zetherion_ai.cgs_gateway.storage import CGSGatewayStorage


class _DummyEncryptor:
    def encrypt_value(self, value: str) -> str:
        return f"enc::{value}"

    def decrypt_value(self, value: str) -> str:
        return value.removeprefix("enc::")


class _DummyAcquire:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _DummyPool:
    def __init__(self) -> None:
        self.conn = type(
            "Conn",
            (),
            {
                "execute": AsyncMock(return_value="OK"),
                "fetch": AsyncMock(return_value=[]),
                "fetchrow": AsyncMock(return_value=None),
            },
        )()
        self.close = AsyncMock()

    def acquire(self) -> _DummyAcquire:
        return _DummyAcquire(self.conn)


@pytest.mark.asyncio
async def test_storage_initialize_and_close(monkeypatch: pytest.MonkeyPatch) -> None:
    pool = _DummyPool()
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(storage_mod.asyncpg, "create_pool", create_pool)

    storage = CGSGatewayStorage(dsn="postgres://test")
    await storage.initialize()
    assert storage._pool is pool
    create_pool.assert_awaited_once()
    pool.conn.execute.assert_awaited()

    await storage.close()
    pool.close.assert_awaited_once()
    assert storage._pool is None


@pytest.mark.asyncio
async def test_storage_tenant_mapping_methods() -> None:
    storage = CGSGatewayStorage(dsn="postgres://test", encryptor=_DummyEncryptor())
    storage._fetchrow = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "Tenant A",
            "domain": "tenant.example",
            "key_version": 2,
            "is_active": True,
            "metadata": {},
            "created_at": "2026-02-27T00:00:00Z",
            "updated_at": "2026-02-27T00:00:00Z",
        }
    )
    storage._fetch = AsyncMock(
        return_value=[
            {
                "cgs_tenant_id": "tenant-a",
                "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
                "name": "Tenant A",
                "domain": "tenant.example",
                "key_version": 2,
                "is_active": True,
                "metadata": {},
                "created_at": "2026-02-27T00:00:00Z",
                "updated_at": "2026-02-27T00:00:00Z",
            }
        ]
    )
    storage._execute = AsyncMock(return_value="UPDATE 1")

    upserted = await storage.upsert_tenant_mapping(
        cgs_tenant_id="tenant-a",
        zetherion_tenant_id="11111111-1111-1111-1111-111111111111",
        name="Tenant A",
        domain="tenant.example",
        zetherion_api_key="sk-live",
    )
    assert upserted["cgs_tenant_id"] == "tenant-a"
    assert storage._fetchrow.await_args.args[5] == "enc::sk-live"

    storage._fetchrow = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "Tenant A",
            "domain": "tenant.example",
            "zetherion_api_key_enc": "enc::sk-live",
            "key_version": 2,
            "is_active": True,
            "metadata": {},
            "created_at": "2026-02-27T00:00:00Z",
            "updated_at": "2026-02-27T00:00:00Z",
        }
    )
    fetched = await storage.get_tenant_mapping("tenant-a")
    assert fetched is not None
    assert fetched["zetherion_api_key"] == "sk-live"

    listed_all = await storage.list_tenant_mappings(active_only=False)
    assert listed_all[0]["cgs_tenant_id"] == "tenant-a"
    listed_active = await storage.list_tenant_mappings(active_only=True)
    assert listed_active[0]["is_active"] is True

    updated_no_fields = await storage.update_tenant_profile(cgs_tenant_id="tenant-a")
    assert updated_no_fields is not None
    updated_with_fields = await storage.update_tenant_profile(
        cgs_tenant_id="tenant-a",
        name="Tenant A2",
        domain="tenant2.example",
        metadata={"tier": "gold"},
    )
    assert updated_with_fields is not None

    rotated = await storage.rotate_tenant_api_key(
        cgs_tenant_id="tenant-a",
        new_api_key="sk-new",
    )
    assert rotated is not None
    deactivated = await storage.deactivate_tenant_mapping("tenant-a")
    assert deactivated is True


@pytest.mark.asyncio
async def test_storage_conversation_methods() -> None:
    storage = CGSGatewayStorage(dsn="postgres://test", encryptor=_DummyEncryptor())
    storage._fetchrow = AsyncMock(
        return_value={
            "conversation_id": "cgs_conv_abc",
            "cgs_tenant_id": "tenant-a",
            "app_user_id": "app-user",
            "external_user_id": "ext-user",
            "zetherion_session_id": "11111111-1111-1111-1111-111111111111",
            "is_closed": False,
            "closed_at": None,
            "metadata": {},
            "created_at": "2026-02-27T00:00:00Z",
            "updated_at": "2026-02-27T00:00:00Z",
        }
    )
    storage._execute = AsyncMock(return_value="UPDATE 1")

    created = await storage.create_conversation(
        cgs_tenant_id="tenant-a",
        zetherion_session_id="11111111-1111-1111-1111-111111111111",
        zetherion_session_token="sess-token",
        app_user_id="app-user",
        external_user_id="ext-user",
        metadata={"source": "test"},
        conversation_id="cgs_conv_abc",
    )
    assert created["conversation_id"] == "cgs_conv_abc"
    assert storage._fetchrow.await_args.args[6] == "enc::sess-token"

    storage._fetchrow = AsyncMock(
        return_value={
            "conversation_id": "cgs_conv_abc",
            "cgs_tenant_id": "tenant-a",
            "app_user_id": "app-user",
            "external_user_id": "ext-user",
            "zetherion_session_id": "11111111-1111-1111-1111-111111111111",
            "zetherion_session_token_enc": "enc::sess-token",
            "is_closed": False,
            "closed_at": None,
            "metadata": {},
            "created_at": "2026-02-27T00:00:00Z",
            "updated_at": "2026-02-27T00:00:00Z",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "Tenant A",
            "domain": "tenant.example",
            "zetherion_api_key_enc": "enc::sk-live",
            "key_version": 2,
            "is_active": True,
        }
    )
    loaded = await storage.get_conversation("cgs_conv_abc")
    assert loaded is not None
    assert loaded["zetherion_session_token"] == "sess-token"
    assert loaded["zetherion_api_key"] == "sk-live"

    assert await storage.close_conversation("cgs_conv_abc") is True


@pytest.mark.asyncio
async def test_storage_idempotency_and_request_log() -> None:
    storage = CGSGatewayStorage(dsn="postgres://test", encryptor=_DummyEncryptor())
    storage._fetchrow = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "endpoint": "/service/ai/v1/conversations",
            "method": "POST",
            "idempotency_key": "idem-1",
            "request_fingerprint": "abc123",
            "response_status": 200,
            "response_body": {"request_id": "req-1", "data": {"ok": True}, "error": None},
            "created_at": "2026-02-27T00:00:00Z",
        }
    )
    storage._execute = AsyncMock(return_value="INSERT 0 1")

    record = await storage.get_idempotency_record(
        cgs_tenant_id="tenant-a",
        endpoint="/service/ai/v1/conversations",
        method="POST",
        idempotency_key="idem-1",
    )
    assert record is not None
    assert record["idempotency_key"] == "idem-1"

    await storage.save_idempotency_record(
        cgs_tenant_id="tenant-a",
        endpoint="/service/ai/v1/conversations",
        method="POST",
        idempotency_key="idem-1",
        request_fingerprint="abc123",
        response_status=200,
        response_body={"request_id": "req-1", "data": {"ok": True}, "error": None},
    )
    await storage.log_request(
        request_id="req-1",
        endpoint="/service/ai/v1/conversations",
        method="POST",
        cgs_tenant_id="tenant-a",
        conversation_id="cgs_conv_abc",
        upstream_status=200,
        duration_ms=42,
        error_code=None,
        details={"source": "unit-test"},
    )
    assert storage._execute.await_count == 2


@pytest.mark.asyncio
async def test_storage_raises_when_not_initialized() -> None:
    storage = CGSGatewayStorage(dsn="postgres://test")
    with pytest.raises(RuntimeError, match="not initialized"):
        await storage._fetchrow("SELECT 1")
    with pytest.raises(RuntimeError, match="not initialized"):
        await storage._fetch("SELECT 1")
    with pytest.raises(RuntimeError, match="not initialized"):
        await storage._execute("SELECT 1")
