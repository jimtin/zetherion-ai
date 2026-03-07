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
async def test_storage_initialize_tolerates_concurrent_schema_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeUniqueViolationError(Exception):
        pass

    pool = _DummyPool()
    pool.conn.execute.side_effect = _FakeUniqueViolationError("pg_type_typname_nsp_index")
    create_pool = AsyncMock(return_value=pool)
    monkeypatch.setattr(storage_mod.asyncpg, "create_pool", create_pool)
    monkeypatch.setattr(storage_mod.asyncpg, "UniqueViolationError", _FakeUniqueViolationError)

    storage = CGSGatewayStorage(dsn="postgres://test")
    await storage.initialize()
    assert storage._pool is pool
    create_pool.assert_awaited_once()
    pool.conn.execute.assert_awaited_once()


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
            "isolation_stage": "legacy",
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
                "isolation_stage": "legacy",
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
            "isolation_stage": "legacy",
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
        isolation_stage="shadow",
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
async def test_storage_reconciliation_helpers() -> None:
    storage = CGSGatewayStorage(dsn="postgres://test", encryptor=_DummyEncryptor())
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
                "isolation_stage": "legacy",
                "created_at": "2026-02-27T00:00:00Z",
                "updated_at": "2026-02-27T00:00:00Z",
            }
        ]
    )
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
            "isolation_stage": "legacy",
            "created_at": "2026-02-27T00:00:00Z",
            "updated_at": "2026-02-27T00:00:00Z",
        }
    )

    candidates = await storage.list_tenant_reconciliation_candidates()
    assert candidates[0]["cgs_tenant_id"] == "tenant-a"

    mapping = await storage.get_tenant_mapping_by_zetherion_tenant_id(
        "11111111-1111-1111-1111-111111111111"
    )
    assert mapping is not None
    assert mapping["zetherion_api_key"] == "sk-live"
    assert mapping["isolation_stage"] == "legacy"


@pytest.mark.asyncio
async def test_storage_upsert_tenant_mapping_raises_when_no_row() -> None:
    storage = CGSGatewayStorage(dsn="postgres://test", encryptor=_DummyEncryptor())
    storage._fetchrow = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="Upsert tenant mapping returned no row"):
        await storage.upsert_tenant_mapping(
            cgs_tenant_id="tenant-a",
            zetherion_tenant_id="11111111-1111-1111-1111-111111111111",
            name="Tenant A",
            domain="tenant.example",
            zetherion_api_key="sk-live",
        )


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
async def test_storage_create_conversation_raises_when_no_row() -> None:
    storage = CGSGatewayStorage(dsn="postgres://test", encryptor=_DummyEncryptor())
    storage._fetchrow = AsyncMock(return_value=None)

    with pytest.raises(RuntimeError, match="Create conversation returned no row"):
        await storage.create_conversation(
            cgs_tenant_id="tenant-a",
            zetherion_session_id="11111111-1111-1111-1111-111111111111",
            zetherion_session_token="sess-token",
            app_user_id="app-user",
            external_user_id="ext-user",
            metadata={"source": "test"},
            conversation_id="cgs_conv_abc",
        )


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


@pytest.mark.asyncio
async def test_storage_admin_change_lifecycle_and_duplicate_paths() -> None:
    storage = CGSGatewayStorage(dsn="postgres://test", encryptor=_DummyEncryptor())

    pending_row = {
        "change_id": "chg_1",
        "cgs_tenant_id": "tenant-a",
        "action": "secret.rotate",
        "target": "OPENAI_API_KEY",
        "payload": {"name": "OPENAI_API_KEY"},
        "payload_fingerprint": "fp-1",
        "status": "pending",
        "requested_by": "op@example.com",
        "approved_by": None,
        "reviewed_at": None,
        "applied_at": None,
        "request_id": "req-1",
        "reason": None,
        "result": None,
        "created_at": "2026-03-01T00:00:00Z",
        "updated_at": "2026-03-01T00:00:00Z",
    }
    approved_row = {**pending_row, "status": "approved", "approved_by": "approver@example.com"}
    rejected_row = {**pending_row, "status": "rejected", "approved_by": "approver@example.com"}
    applied_row = {**approved_row, "status": "applied"}
    failed_row = {**approved_row, "status": "failed"}

    storage._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            None,  # duplicate lookup for create
            pending_row,  # insert create
            pending_row,  # get_admin_change
            approved_row,  # approve
            rejected_row,  # reject
            applied_row,  # mark applied update
            failed_row,  # mark failed update
            pending_row,  # duplicate lookup for create
        ]
    )
    storage._fetch = AsyncMock(return_value=[pending_row])  # type: ignore[method-assign]

    created = await storage.create_admin_change(
        cgs_tenant_id="tenant-a",
        action="secret.rotate",
        target="OPENAI_API_KEY",
        payload={"name": "OPENAI_API_KEY"},
        requested_by="op@example.com",
        request_id="req-1",
        reason=None,
    )
    assert created["change_id"] == "chg_1"

    fetched = await storage.get_admin_change("chg_1")
    assert fetched is not None
    assert fetched["status"] == "pending"

    listed = await storage.list_admin_changes(cgs_tenant_id="tenant-a", status="pending", limit=10)
    assert listed[0]["status"] == "pending"

    approved = await storage.approve_admin_change(
        change_id="chg_1",
        approved_by="approver@example.com",
        reason="approved",
    )
    assert approved is not None
    assert approved["status"] == "approved"

    rejected = await storage.reject_admin_change(
        change_id="chg_1",
        approved_by="approver@example.com",
        reason="rejected",
    )
    assert rejected is not None
    assert rejected["status"] == "rejected"

    applied = await storage.mark_admin_change_applied(
        change_id="chg_1",
        result={"ok": True},
    )
    assert applied is not None
    assert applied["status"] == "applied"

    failed = await storage.mark_admin_change_failed(
        change_id="chg_1",
        result={"ok": False},
    )
    assert failed is not None
    assert failed["status"] == "failed"

    duplicate = await storage.create_admin_change(
        cgs_tenant_id="tenant-a",
        action="secret.rotate",
        target="OPENAI_API_KEY",
        payload={"name": "OPENAI_API_KEY"},
        requested_by="op@example.com",
        request_id="req-1",
        reason=None,
    )
    assert duplicate["duplicate"] is True


@pytest.mark.asyncio
async def test_storage_admin_change_mark_paths_with_current_state_fallback() -> None:
    storage = CGSGatewayStorage(dsn="postgres://test", encryptor=_DummyEncryptor())
    applied_row = {
        "change_id": "chg_2",
        "cgs_tenant_id": "tenant-a",
        "action": "secret.rotate",
        "target": "OPENAI_API_KEY",
        "payload": {},
        "payload_fingerprint": "fp-2",
        "status": "applied",
        "requested_by": "op@example.com",
        "approved_by": "approver@example.com",
        "reviewed_at": None,
        "applied_at": None,
        "request_id": "req-2",
        "reason": None,
        "result": {"ok": True},
        "created_at": "2026-03-01T00:00:00Z",
        "updated_at": "2026-03-01T00:00:00Z",
    }

    storage._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    storage.get_admin_change = AsyncMock(return_value=applied_row)  # type: ignore[method-assign]

    applied = await storage.mark_admin_change_applied(
        change_id="chg_2",
        result={"ok": True},
    )
    assert applied is not None
    assert applied["status"] == "applied"

    storage.get_admin_change = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert (
        await storage.mark_admin_change_failed(
            change_id="chg_2",
            result={"ok": False},
        )
        is None
    )


@pytest.mark.asyncio
async def test_storage_blog_publish_receipt_create_find_and_duplicate_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = CGSGatewayStorage(dsn="postgres://test", encryptor=_DummyEncryptor())

    receipt = {
        "receipt_id": "blog_1",
        "idempotency_key": "blog-sha1",
        "sha": "sha1",
        "payload_fingerprint": "fp-1",
        "source": "windows-worker",
        "repo": "jimtin/zetherion-ai",
        "release_tag": "v0.4.5",
        "title": "Release notes",
        "slug": "release-notes",
        "meta_description": "desc",
        "excerpt": "excerpt",
        "primary_keyword": "zetherion",
        "content_markdown": "body",
        "json_ld": {},
        "models": {"primary": "gpt-5.2"},
        "published_at": "2026-03-04T00:00:00Z",
        "request_id": "req-1",
        "created_at": "2026-03-04T00:00:00Z",
    }

    storage._fetchrow = AsyncMock(return_value=receipt)  # type: ignore[method-assign]
    found = await storage.find_blog_publish_receipt(idempotency_key="blog-sha1", sha="sha1")
    assert found is not None
    assert found["receipt_id"] == "blog_1"

    created = await storage.create_blog_publish_receipt(
        idempotency_key="blog-sha1",
        payload_fingerprint="fp-1",
        source="windows-worker",
        sha="sha1",
        repo="jimtin/zetherion-ai",
        release_tag="v0.4.5",
        title="Release notes",
        slug="release-notes",
        meta_description="desc",
        excerpt="excerpt",
        primary_keyword="zetherion",
        content_markdown="body",
        json_ld={},
        models={"primary": "gpt-5.2"},
        published_at="2026-03-04T00:00:00Z",
        request_id="req-1",
    )
    assert created["receipt_id"] == "blog_1"

    class _UniqueViolationError(Exception):
        pass

    monkeypatch.setattr(storage_mod.asyncpg, "UniqueViolationError", _UniqueViolationError)
    storage._fetchrow = AsyncMock(side_effect=_UniqueViolationError())  # type: ignore[method-assign]
    storage.find_blog_publish_receipt = AsyncMock(return_value=receipt)  # type: ignore[method-assign]

    duplicate = await storage.create_blog_publish_receipt(
        idempotency_key="blog-sha1",
        payload_fingerprint="fp-1",
        source="windows-worker",
        sha="sha1",
        repo="jimtin/zetherion-ai",
        release_tag="v0.4.5",
        title="Release notes",
        slug="release-notes",
        meta_description="desc",
        excerpt="excerpt",
        primary_keyword="zetherion",
        content_markdown="body",
        json_ld={},
        models={"primary": "gpt-5.2"},
        published_at="2026-03-04T00:00:00Z",
        request_id="req-1",
    )
    assert duplicate["receipt_id"] == "blog_1"
