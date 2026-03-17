"""Unit tests for tenant admin manager control-plane logic."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.admin.tenant_admin_manager import (
    _SCHEMA_SQL,
    AdminActorContext,
    TenantAdminManager,
    admin_actor_from_payload,
)
from zetherion_ai.security.encryption import FieldEncryptor
from zetherion_ai.trust.storage import TrustGrantRecord


class _AsyncContext:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeConn:
    def __init__(self) -> None:
        self.execute = AsyncMock(return_value="OK")
        self.fetch = AsyncMock(return_value=[])
        self.fetchrow = AsyncMock(return_value=None)
        self.fetchval = AsyncMock(return_value=None)

    def transaction(self) -> _AsyncContext:
        return _AsyncContext(None)


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _AsyncContext:
        return _AsyncContext(self._conn)


def _actor(change_ticket_id: str | None = None) -> AdminActorContext:
    return AdminActorContext(
        actor_sub="operator-1",
        actor_roles=("operator",),
        request_id="req-1",
        timestamp=datetime.now(UTC),
        nonce="nonce-1",
        actor_email="ops@example.com",
        change_ticket_id=change_ticket_id,
    )


def test_schema_adds_execution_target_column_before_dispatch_index() -> None:
    add_column_sql = (
        "ALTER TABLE tenant_execution_steps\n"
        "    ADD COLUMN IF NOT EXISTS execution_target TEXT NOT NULL DEFAULT 'windows_local';"
    )
    dispatch_index_sql = (
        "CREATE INDEX IF NOT EXISTS idx_tenant_execution_steps_dispatch\n"
        "    ON tenant_execution_steps (tenant_id, plan_id, execution_target, "
        "status, updated_at DESC);"
    )

    assert _SCHEMA_SQL.index(add_column_sql) < _SCHEMA_SQL.index(dispatch_index_sql)


def test_schema_adds_worker_health_columns_before_dispatch_index() -> None:
    add_worker_health_sql = (
        "ALTER TABLE tenant_worker_nodes\n"
        "    ADD COLUMN IF NOT EXISTS health_score INT NOT NULL DEFAULT 100;"
    )
    dispatch_index_sql = (
        "CREATE INDEX IF NOT EXISTS idx_tenant_worker_nodes_dispatch\n"
        "    ON tenant_worker_nodes (\n"
        "        tenant_id,\n"
        "        status,\n"
        "        health_status,\n"
        "        health_score DESC,\n"
        "        last_heartbeat_at DESC,\n"
        "        updated_at DESC\n"
        "    );"
    )

    assert _SCHEMA_SQL.index(add_worker_health_sql) < _SCHEMA_SQL.index(dispatch_index_sql)


@pytest.mark.asyncio
async def test_initialize_refreshes_caches() -> None:
    conn = _FakeConn()
    conn.fetch.side_effect = [
        [
            {
                "tenant_id": "t1",
                "namespace": "models",
                "key": "default_provider",
                "value": "groq",
                "data_type": "string",
            },
            {
                "tenant_id": "t1",
                "namespace": "security",
                "key": "tenant_admin_enforcement_enabled",
                "value": "true",
                "data_type": "bool",
            },
        ],
        [{"tenant_id": "t1", "name": "OPENAI_API_KEY", "value_enc": "secret-value"}],
    ]
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    await manager.initialize()

    assert manager.get_setting_cached("t1", "models", "default_provider") == "groq"
    assert manager.get_setting_cached("t1", "security", "tenant_admin_enforcement_enabled") is True
    assert manager.get_secret_cached("t1", "OPENAI_API_KEY") == "secret-value"
    assert conn.execute.await_count >= 1  # schema ensure


@pytest.mark.asyncio
async def test_discord_user_and_binding_mutations_write_audit() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            None,
            {"tenant_id": "t1", "discord_user_id": 7, "role": "user"},
            {"tenant_id": "t1", "discord_user_id": 7, "role": "user"},
            {"tenant_id": "t1", "discord_user_id": 7, "role": "admin"},
            {"tenant_id": "t1", "discord_user_id": 7, "role": "admin"},
            None,
            {
                "tenant_id": "t1",
                "guild_id": 10,
                "channel_id": None,
                "priority": 1,
                "is_active": True,
            },
            None,
            {"tenant_id": "t1", "guild_id": 10, "channel_id": 20, "priority": 1, "is_active": True},
            {"tenant_id": "t1", "guild_id": 10, "channel_id": 20, "priority": 1, "is_active": True},
            {"tenant_id": "t1"},
            {"tenant_id": "t1"},
        ]
    )
    manager._execute = AsyncMock(return_value="DELETE 1")  # type: ignore[method-assign]

    added = await manager.upsert_discord_user(
        tenant_id="11111111-1111-1111-1111-111111111111",
        discord_user_id=7,
        role="user",
        actor=_actor(),
    )
    assert added["discord_user_id"] == 7

    changed = await manager.update_discord_user_role(
        tenant_id="11111111-1111-1111-1111-111111111111",
        discord_user_id=7,
        role="admin",
        actor=_actor(),
    )
    assert changed is True

    deleted = await manager.delete_discord_user(
        tenant_id="11111111-1111-1111-1111-111111111111",
        discord_user_id=7,
        actor=_actor(),
    )
    assert deleted is True

    guild_binding = await manager.put_guild_binding(
        tenant_id="11111111-1111-1111-1111-111111111111",
        guild_id=10,
        priority=1,
        is_active=True,
        actor=_actor(),
    )
    assert guild_binding["guild_id"] == 10

    channel_binding = await manager.put_channel_binding(
        tenant_id="11111111-1111-1111-1111-111111111111",
        guild_id=10,
        channel_id=20,
        priority=1,
        is_active=True,
        actor=_actor(),
    )
    assert channel_binding["channel_id"] == 20

    removed_channel = await manager.delete_channel_binding(
        tenant_id="11111111-1111-1111-1111-111111111111",
        channel_id=20,
        actor=_actor(),
    )
    assert removed_channel is True

    assert manager._write_audit.await_count >= 6

    resolved_channel = await manager.resolve_tenant_for_discord(guild_id=10, channel_id=20)
    assert resolved_channel == "t1"
    resolved_guild = await manager.resolve_tenant_for_discord(guild_id=10, channel_id=None)
    assert resolved_guild == "t1"


@pytest.mark.asyncio
async def test_settings_mutation_and_delete_update_cache() -> None:
    conn = _FakeConn()
    conn.fetchrow.side_effect = [
        None,
        {
            "tenant_id": "t1",
            "namespace": "models",
            "key": "default_provider",
            "value": "groq",
            "data_type": "string",
            "updated_by": "operator-1",
            "updated_at": datetime.now(UTC),
        },
    ]
    conn.fetchval.side_effect = [0, 1]
    conn.execute.side_effect = ["UPSERT 1", "VERSION 1", "DELETE 1", "VERSION 2"]
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    await manager.set_setting(
        tenant_id="11111111-1111-1111-1111-111111111111",
        namespace="models",
        key="default_provider",
        value="groq",
        data_type="string",
        actor=_actor(),
    )
    assert (
        manager.get_setting_cached(
            "11111111-1111-1111-1111-111111111111", "models", "default_provider"
        )
        == "groq"
    )

    deleted = await manager.delete_setting(
        tenant_id="11111111-1111-1111-1111-111111111111",
        namespace="models",
        key="default_provider",
        actor=_actor(),
    )
    assert deleted is True
    assert (
        manager.get_setting_cached(
            "11111111-1111-1111-1111-111111111111", "models", "default_provider"
        )
        is None
    )


@pytest.mark.asyncio
async def test_secret_set_delete_and_rollback_updates_cache() -> None:
    conn = _FakeConn()
    conn.fetchrow.side_effect = [
        None,
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "OPENAI_API_KEY",
            "version": 1,
            "updated_by": "operator-1",
            "updated_at": datetime.now(UTC),
            "description": "first",
        },
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "OPENAI_API_KEY",
            "value_enc": "enc-1",
            "version": 1,
            "updated_by": "operator-1",
            "updated_at": datetime.now(UTC),
            "description": "first",
        },
        {"value_enc": "enc-1", "description": "first"},
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "OPENAI_API_KEY",
            "version": 2,
            "updated_by": "operator-1",
            "updated_at": datetime.now(UTC),
            "description": "rollback",
        },
        {
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "OPENAI_API_KEY",
            "version": 3,
            "updated_by": "operator-1",
            "updated_at": datetime.now(UTC),
            "description": "rollback",
        },
    ]
    conn.execute.side_effect = [
        "UPSERT 1",
        "VERSION 1",
        "DELETE 1",
        "VERSION 2",
        "UPSERT 2",
        "VERSION 3",
    ]
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    record = await manager.set_secret(
        tenant_id="11111111-1111-1111-1111-111111111111",
        name="OPENAI_API_KEY",
        value="sk-live",
        description="first",
        actor=_actor("chg-1"),
    )
    assert record["name"] == "OPENAI_API_KEY"
    assert (
        manager.get_secret_cached("11111111-1111-1111-1111-111111111111", "OPENAI_API_KEY")
        == "sk-live"
    )

    removed = await manager.delete_secret(
        tenant_id="11111111-1111-1111-1111-111111111111",
        name="OPENAI_API_KEY",
        actor=_actor("chg-2"),
    )
    assert removed is True
    assert (
        manager.get_secret_cached("11111111-1111-1111-1111-111111111111", "OPENAI_API_KEY") is None
    )

    rolled_back = await manager.rollback_secret_to_version(
        tenant_id="11111111-1111-1111-1111-111111111111",
        name="OPENAI_API_KEY",
        version=1,
        actor=_actor("chg-3"),
    )
    assert rolled_back["version"] == 3


@pytest.mark.asyncio
async def test_list_helpers_and_audit_reader() -> None:
    conn = _FakeConn()
    conn.fetch.side_effect = [
        [
            {
                "namespace": "models",
                "key": "default_provider",
                "value": "groq",
                "data_type": "string",
            }
        ],
        [
            {
                "tenant_id": "t1",
                "name": "OPENAI_API_KEY",
                "version": 2,
                "updated_by": "op",
                "updated_at": datetime.now(UTC),
                "description": "desc",
            }
        ],
        [{"id": 1, "tenant_id": "t1", "action": "tenant_secret_upsert"}],
    ]
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    settings = await manager.list_settings("11111111-1111-1111-1111-111111111111")
    assert settings["models"]["default_provider"] == "groq"

    secrets = await manager.list_secret_metadata("11111111-1111-1111-1111-111111111111")
    assert secrets[0]["version"] == 2

    audit = await manager.list_audit("11111111-1111-1111-1111-111111111111", limit=10)
    assert audit[0]["action"] == "tenant_secret_upsert"


@pytest.mark.asyncio
async def test_secret_helper_missing_and_no_decrypt_cache_branches() -> None:
    conn = _FakeConn()
    conn.fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "OPENAI_API_KEY",
            "version": 2,
            "updated_by": "operator-1",
            "updated_at": datetime.now(UTC),
            "description": "rollback",
        }
    )
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"value_enc": "enc-1", "description": "rollback"},
            None,
            None,
        ]
    )
    manager._decrypt = MagicMock(return_value=None)  # type: ignore[method-assign]

    rolled_back = await manager.rollback_secret_to_version(
        tenant_id="11111111-1111-1111-1111-111111111111",
        name="OPENAI_API_KEY",
        version=1,
        actor=_actor(),
    )
    assert rolled_back["version"] == 2
    assert (
        manager.get_secret_cached("11111111-1111-1111-1111-111111111111", "OPENAI_API_KEY") is None
    )

    assert (
        await manager._load_tenant_secret_value(
            "11111111-1111-1111-1111-111111111111",
            "MISSING_SECRET",
        )
        is None
    )


@pytest.mark.asyncio
async def test_secret_encryption_path_round_trip() -> None:
    encryptor = FieldEncryptor(key=b"a" * 32)
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool, encryptor=encryptor)  # type: ignore[arg-type]

    encrypted = manager._encrypt("secret")
    assert encrypted != "secret"
    assert manager._decrypt(encrypted) == "secret"


def test_admin_actor_from_payload_validation() -> None:
    payload = {
        "actor_sub": "operator-1",
        "actor_roles": ["operator", "admin"],
        "request_id": "req-1",
        "timestamp": "2026-03-03T00:00:00+00:00",
        "nonce": "nonce-1",
        "actor_email": "ops@example.com",
        "change_ticket_id": "chg-1",
    }
    actor = admin_actor_from_payload(payload)
    assert actor.actor_sub == "operator-1"
    assert actor.actor_roles == ("operator", "admin")
    assert actor.change_ticket_id == "chg-1"

    with pytest.raises(ValueError, match="Missing actor_sub"):
        admin_actor_from_payload(
            {
                "actor_sub": "",
                "request_id": "req",
                "timestamp": "2026-03-03T00:00:00+00:00",
                "nonce": "n1",
            }
        )

    with pytest.raises(ValueError, match="Invalid timestamp"):
        admin_actor_from_payload(
            {
                "actor_sub": "operator",
                "request_id": "req",
                "timestamp": "not-a-time",
                "nonce": "n1",
            }
        )


@pytest.mark.asyncio
async def test_discord_lookup_helpers_cover_allowed_and_missing_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"tenant_id": "t1", "discord_user_id": 7, "role": "admin"}]
    )
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[{"one": 1}, None]
    )
    manager._fetchval = AsyncMock(side_effect=["owner", None])  # type: ignore[method-assign]

    users = await manager.list_discord_users("11111111-1111-1111-1111-111111111111")
    assert users[0]["role"] == "admin"
    assert (
        await manager.is_discord_user_allowed(
            "11111111-1111-1111-1111-111111111111",
            7,
        )
        is True
    )
    assert (
        await manager.is_discord_user_allowed(
            "11111111-1111-1111-1111-111111111111",
            8,
        )
        is False
    )
    assert (
        await manager.get_discord_user_role(
            "11111111-1111-1111-1111-111111111111",
            7,
        )
        == "owner"
    )
    assert (
        await manager.get_discord_user_role(
            "11111111-1111-1111-1111-111111111111",
            8,
        )
        is None
    )


@pytest.mark.asyncio
async def test_list_tenants_for_discord_user_role_filtering() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [{"tenant_id": "tenant-admin"}, {"tenant_id": "tenant-owner"}],
            [{"tenant_id": "tenant-owner"}],
            [{"tenant_id": "tenant-owner"}],
        ]
    )

    filtered = await manager.list_tenants_for_discord_user(
        7,
        roles=("OWNER", "admin", "invalid", ""),
    )
    unfiltered = await manager.list_tenants_for_discord_user(7)
    invalid_roles = await manager.list_tenants_for_discord_user(7, roles=("invalid",))

    assert filtered == ["tenant-admin", "tenant-owner"]
    assert unfiltered == ["tenant-owner"]
    assert invalid_roles == ["tenant-owner"]

    first_call = manager._fetch.await_args_list[0].args
    assert first_call[1] == 7
    assert first_call[2] == ["admin", "owner"]
    second_call = manager._fetch.await_args_list[1].args
    assert "tenant_discord_users" in second_call[0]
    assert "WHERE discord_user_id = $1" in second_call[0]
    assert second_call[1] == 7
    third_call = manager._fetch.await_args_list[2].args
    assert third_call[1] == 7
    assert len(third_call) == 2


@pytest.mark.asyncio
async def test_discord_mutation_error_and_false_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._execute = AsyncMock(return_value="DELETE 0")  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Invalid role"):
        await manager.upsert_discord_user(
            tenant_id="11111111-1111-1111-1111-111111111111",
            discord_user_id=1,
            role="invalid",
            actor=_actor(),
        )

    manager._fetchrow = AsyncMock(side_effect=[None, None])  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to upsert tenant discord user"):
        await manager.upsert_discord_user(
            tenant_id="11111111-1111-1111-1111-111111111111",
            discord_user_id=1,
            role="user",
            actor=_actor(),
        )

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert (
        await manager.update_discord_user_role(
            tenant_id="11111111-1111-1111-1111-111111111111",
            discord_user_id=1,
            role="user",
            actor=_actor(),
        )
        is False
    )
    assert (
        await manager.delete_discord_user(
            tenant_id="11111111-1111-1111-1111-111111111111",
            discord_user_id=1,
            actor=_actor(),
        )
        is False
    )


@pytest.mark.asyncio
async def test_binding_paths_cover_list_update_and_failure() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._execute = AsyncMock(return_value="DELETE 0")  # type: ignore[method-assign]
    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"tenant_id": "t1", "guild_id": 10, "channel_id": None}]
    )
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "tenant_id": "t1",
                "guild_id": 10,
                "channel_id": None,
                "priority": 1,
                "is_active": True,
            },
            {
                "tenant_id": "t1",
                "guild_id": 10,
                "channel_id": None,
                "priority": 2,
                "is_active": True,
            },
            {"tenant_id": "t1", "guild_id": 10, "channel_id": 20, "priority": 1, "is_active": True},
            {
                "tenant_id": "t1",
                "guild_id": 10,
                "channel_id": 20,
                "priority": 2,
                "is_active": False,
            },
            None,
            None,
        ]
    )

    listed = await manager.list_discord_bindings("11111111-1111-1111-1111-111111111111")
    assert listed[0]["guild_id"] == 10

    guild = await manager.put_guild_binding(
        tenant_id="11111111-1111-1111-1111-111111111111",
        guild_id=10,
        priority=2,
        is_active=True,
        actor=_actor(),
    )
    assert guild["priority"] == 2

    channel = await manager.put_channel_binding(
        tenant_id="11111111-1111-1111-1111-111111111111",
        guild_id=10,
        channel_id=20,
        priority=2,
        is_active=False,
        actor=_actor(),
    )
    assert channel["channel_id"] == 20
    assert (
        await manager.delete_channel_binding(
            tenant_id="11111111-1111-1111-1111-111111111111",
            channel_id=20,
            actor=_actor(),
        )
        is False
    )

    manager._fetchrow = AsyncMock(side_effect=[None, None])  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to upsert guild binding"):
        await manager.put_guild_binding(
            tenant_id="11111111-1111-1111-1111-111111111111",
            guild_id=99,
            priority=1,
            is_active=True,
            actor=_actor(),
        )
    manager._fetchrow = AsyncMock(side_effect=[None, None])  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to upsert channel binding"):
        await manager.put_channel_binding(
            tenant_id="11111111-1111-1111-1111-111111111111",
            guild_id=99,
            channel_id=88,
            priority=1,
            is_active=True,
            actor=_actor(),
        )


@pytest.mark.asyncio
async def test_resolve_tenant_paths_include_no_match() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[{"tenant_id": "t-channel"}, {"tenant_id": "t-guild"}]
    )
    assert await manager.resolve_tenant_for_discord(guild_id=10, channel_id=20) == "t-channel"
    assert await manager.resolve_tenant_for_discord(guild_id=10, channel_id=None) == "t-guild"
    assert await manager.resolve_tenant_for_discord(guild_id=None, channel_id=None) is None


@pytest.mark.asyncio
async def test_discord_setting_secret_false_paths_cover_fallback_and_non_delete() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"tenant_id": "t1", "discord_user_id": 9, "role": "user"},
            None,
            None,
            {"tenant_id": "t1"},
            {
                "tenant_id": "t1",
                "namespace": "models",
                "key": "default_provider",
                "value": "groq",
                "data_type": "string",
            },
            {
                "tenant_id": "t1",
                "name": "OPENAI_API_KEY",
                "value_enc": "enc",
                "version": 1,
                "description": None,
            },
        ]
    )
    manager._execute = AsyncMock(side_effect=["DELETE 0", "DELETE 0"])  # type: ignore[method-assign]
    manager._fetchval = AsyncMock(return_value=4)  # type: ignore[method-assign]

    assert (
        await manager.update_discord_user_role(
            tenant_id="11111111-1111-1111-1111-111111111111",
            discord_user_id=9,
            role="admin",
            actor=_actor(),
        )
        is False
    )
    manager._write_audit.assert_not_awaited()

    assert (
        await manager.resolve_tenant_for_discord(
            guild_id=10,
            channel_id=20,
        )
        == "t1"
    )

    assert (
        await manager.delete_setting(
            tenant_id="11111111-1111-1111-1111-111111111111",
            namespace="models",
            key="default_provider",
            actor=_actor(),
        )
        is False
    )
    assert (
        await manager.delete_secret(
            tenant_id="11111111-1111-1111-1111-111111111111",
            name="OPENAI_API_KEY",
            actor=_actor(),
        )
        is False
    )
    manager._write_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_setting_validation_namespace_filter_and_delete_not_found() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(
        pool=pool,  # type: ignore[arg-type]
        setting_key_allowlist={"models": frozenset({"default_provider"})},
    )
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {"namespace": "models", "key": "default_provider", "value": "7", "data_type": "int"},
            {"namespace": "models", "key": "temperature", "value": "0.5", "data_type": "float"},
            {"namespace": "security", "key": "enabled", "value": "true", "data_type": "bool"},
            {"namespace": "models", "key": "meta", "value": '{"a":1}', "data_type": "json"},
        ]
    )
    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]

    settings = await manager.list_settings(
        "11111111-1111-1111-1111-111111111111",
        namespace="models",
    )
    assert settings["models"]["default_provider"] == 7
    assert settings["models"]["temperature"] == 0.5
    assert settings["models"]["meta"] == {"a": 1}
    assert settings["security"]["enabled"] is True

    with pytest.raises(ValueError, match="Invalid namespace"):
        await manager.set_setting(
            tenant_id="11111111-1111-1111-1111-111111111111",
            namespace="invalid_ns",
            key="x",
            value="1",
            data_type="string",
            actor=_actor(),
        )
    with pytest.raises(ValueError, match="not mutable"):
        await manager.set_setting(
            tenant_id="11111111-1111-1111-1111-111111111111",
            namespace="models",
            key="temperature",
            value="1",
            data_type="string",
            actor=_actor(),
        )
    with pytest.raises(ValueError, match="Invalid data_type"):
        await manager.set_setting(
            tenant_id="11111111-1111-1111-1111-111111111111",
            namespace="models",
            key="default_provider",
            value="1",
            data_type="bad",
            actor=_actor(),
        )
    assert (
        await manager.delete_setting(
            tenant_id="11111111-1111-1111-1111-111111111111",
            namespace="models",
            key="default_provider",
            actor=_actor(),
        )
        is False
    )


@pytest.mark.asyncio
async def test_secret_validation_and_error_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="non-empty"):
        await manager.set_secret(
            tenant_id="11111111-1111-1111-1111-111111111111",
            name="OPENAI_API_KEY",
            value="",
            description=None,
            actor=_actor(),
        )

    conn.fetchrow.side_effect = [None, None]
    with pytest.raises(RuntimeError, match="Failed to store tenant secret"):
        await manager.set_secret(
            tenant_id="11111111-1111-1111-1111-111111111111",
            name="OPENAI_API_KEY",
            value="sk-live",
            description=None,
            actor=_actor(),
        )

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert (
        await manager.delete_secret(
            tenant_id="11111111-1111-1111-1111-111111111111",
            name="OPENAI_API_KEY",
            actor=_actor(),
        )
        is False
    )


@pytest.mark.asyncio
async def test_secret_rollback_error_paths_and_decrypt_failure() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)

    class _BrokenEncryptor:
        def encrypt_value(self, plaintext: str) -> str:  # pragma: no cover - passthrough helper
            return plaintext

        def decrypt_value(self, ciphertext: str) -> str:
            raise ValueError("boom")

    manager = TenantAdminManager(pool=pool, encryptor=_BrokenEncryptor())  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="Secret version not found"):
        await manager.rollback_secret_to_version(
            tenant_id="11111111-1111-1111-1111-111111111111",
            name="OPENAI_API_KEY",
            version=1,
            actor=_actor(),
        )

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[{"value_enc": None, "description": None}]
    )
    with pytest.raises(ValueError, match="does not contain secret material"):
        await manager.rollback_secret_to_version(
            tenant_id="11111111-1111-1111-1111-111111111111",
            name="OPENAI_API_KEY",
            version=1,
            actor=_actor(),
        )

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"value_enc": "enc-1", "description": None},
            {"tenant_id": "t1", "name": "OPENAI_API_KEY", "version": 1, "description": None},
        ]
    )
    conn.fetchrow.return_value = None
    with pytest.raises(RuntimeError, match="Failed to rollback secret"):
        await manager.rollback_secret_to_version(
            tenant_id="11111111-1111-1111-1111-111111111111",
            name="OPENAI_API_KEY",
            version=1,
            actor=_actor(),
        )

    assert manager._decrypt("not-encrypted") is None


def test_admin_actor_payload_extra_validation_paths() -> None:
    with pytest.raises(ValueError, match="Missing request_id"):
        admin_actor_from_payload(
            {
                "actor_sub": "operator",
                "request_id": "",
                "timestamp": "2026-03-03T00:00:00+00:00",
                "nonce": "n1",
            }
        )
    with pytest.raises(ValueError, match="Missing nonce"):
        admin_actor_from_payload(
            {
                "actor_sub": "operator",
                "request_id": "req",
                "timestamp": "2026-03-03T00:00:00+00:00",
                "nonce": "",
            }
        )
    with pytest.raises(ValueError, match="Missing timestamp"):
        admin_actor_from_payload(
            {
                "actor_sub": "operator",
                "request_id": "req",
                "nonce": "n1",
            }
        )

    actor = admin_actor_from_payload(
        {
            "actor_sub": "operator",
            "actor_roles": "not-a-list",
            "request_id": "req",
            "timestamp": "2026-03-03T00:00:00",
            "nonce": "n1",
        }
    )
    assert actor.actor_roles == ()
    assert actor.timestamp.tzinfo is not None


@pytest.mark.asyncio
async def test_email_provider_secret_resolution_and_config_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"value_enc": "enc-cached"},  # _load_tenant_secret_value
            {
                "provider": "google",
                "client_id_ref": "email.google.oauth_client_id",
                "client_secret_ref": "email.google.oauth_client_secret",
                "redirect_uri": "https://example.com/oauth/callback",
                "enabled": True,
            },
            {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "provider": "google",
                "client_id_ref": "email.google.oauth_client_id",
                "client_secret_ref": "email.google.oauth_client_secret",
                "redirect_uri": "https://example.com/oauth/callback",
                "enabled": True,
                "metadata": {},
                "created_by": "op",
                "updated_by": "op",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
            {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "provider": "google",
                "client_id_ref": "email.google.oauth_client_id",
                "client_secret_ref": "email.google.oauth_client_secret",
                "redirect_uri": "https://example.com/oauth/callback",
                "enabled": True,
                "metadata": {},
                "created_by": "op",
                "updated_by": "op",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
        ]
    )
    manager._decrypt = lambda ciphertext: "decrypted-secret"  # type: ignore[method-assign]
    manager.set_secret = AsyncMock(return_value={})  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    loaded = await manager._load_tenant_secret_value(
        "11111111-1111-1111-1111-111111111111",
        "email.google.oauth_client_id",
    )
    assert loaded == "decrypted-secret"
    # Cache path should bypass DB lookup.
    loaded_again = await manager._load_tenant_secret_value(
        "11111111-1111-1111-1111-111111111111",
        "email.google.oauth_client_id",
    )
    assert loaded_again == "decrypted-secret"

    manager._load_tenant_secret_value = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            "client-id",
            "client-secret",
            "client-id",
            "client-secret",
            "client-id",
            "client-secret",
        ]
    )
    creds = await manager._resolve_provider_oauth_credentials(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="google",
    )
    assert creds["client_id"] == "client-id"
    assert creds["client_secret"] == "client-secret"

    cfg = await manager.get_email_provider_config(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="google",
    )
    assert cfg is not None
    assert cfg["has_client_id"] is True
    assert cfg["has_client_secret"] is True

    manager._load_tenant_secret_value = AsyncMock(  # type: ignore[method-assign]
        return_value="resolved-secret"
    )
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "provider": "google",
            "client_id_ref": "email.google.oauth_client_id",
            "client_secret_ref": "email.google.oauth_client_secret",
            "redirect_uri": "https://example.com/oauth/callback",
            "enabled": True,
            "metadata": {"region": "us"},
            "created_by": "op",
            "updated_by": "op",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    stored = await manager.put_email_provider_config(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="google",
        client_id="client-id",
        client_secret="client-secret",
        redirect_uri="https://example.com/oauth/callback",
        enabled=True,
        actor=_actor(),
        metadata={"region": "us"},
    )
    assert stored["provider"] == "google"

    with pytest.raises(ValueError, match="Unsupported email provider"):
        manager._normalize_provider("microsoft")

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="not configured"):
        await manager._resolve_provider_oauth_credentials(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
        )

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "provider": "google",
            "client_id_ref": "email.google.oauth_client_id",
            "client_secret_ref": "email.google.oauth_client_secret",
            "redirect_uri": "https://example.com/oauth/callback",
            "enabled": True,
        }
    )
    manager._load_tenant_secret_value = AsyncMock(side_effect=[None, "client-secret"])  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="client id secret"):
        await manager._resolve_provider_oauth_credentials(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
        )


@pytest.mark.asyncio
async def test_email_oauth_start_consume_and_exchange_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    manager._resolve_provider_oauth_credentials = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "provider": "google",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "redirect_uri": "https://example.com/oauth/callback",
        }
    )
    manager._execute = AsyncMock(return_value="INSERT 0 1")  # type: ignore[method-assign]

    oauth_start = await manager.create_email_oauth_start(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="google",
        actor=_actor(),
        account_hint="owner@example.com",
    )
    assert oauth_start["provider"] == "google"
    assert "state=email_" in oauth_start["auth_url"]

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "state": "email_state",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "provider": "google",
            "account_hint": "owner@example.com",
            "created_by": "operator-1",
            "created_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(minutes=5),
            "consumed_at": datetime.now(UTC),
        }
    )
    consumed = await manager.consume_email_oauth_state(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="google",
        state="email_state",
    )
    assert consumed["provider"] == "google"

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="Invalid or expired OAuth state"):
        await manager.consume_email_oauth_state(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
            state="missing",
        )

    class _FakeGmailAuth:
        def __init__(self, *, client_id: str, client_secret: str, redirect_uri: str) -> None:
            self.client_id = client_id
            self.client_secret = client_secret
            self.redirect_uri = redirect_uri

        async def exchange_code(self, code: str) -> dict[str, Any]:
            return {
                "access_token": f"access-{code}",
                "refresh_token": "refresh-token",
                "expires_in": 3600,
                "scope": "email profile",
            }

        async def get_user_email(self, access_token: str) -> str:
            return "owner@example.com"

    import zetherion_ai.skills.gmail.auth as gmail_auth_mod

    monkeypatch.setattr(gmail_auth_mod, "GmailAuth", _FakeGmailAuth)
    manager.consume_email_oauth_state = AsyncMock(  # type: ignore[method-assign]
        return_value={"state": "email_state", "account_hint": "owner@example.com"}
    )
    manager._resolve_provider_oauth_credentials = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "provider": "google",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "redirect_uri": "https://example.com/oauth/callback",
        }
    )
    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._upsert_email_account = AsyncMock(  # type: ignore[method-assign]
        return_value={"account_id": "acc-1", "email_address": "owner@example.com"}
    )
    manager._emit_email_event = AsyncMock(return_value=None)  # type: ignore[method-assign]

    exchanged = await manager.exchange_google_oauth_code(
        tenant_id="11111111-1111-1111-1111-111111111111",
        code="abc123",
        state="email_state",
        actor=_actor(),
    )
    assert exchanged["account_id"] == "acc-1"


@pytest.mark.asyncio
async def test_email_oauth_and_provider_guard_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    manager._fetchrow = AsyncMock(return_value={"value_enc": "enc-missing"})  # type: ignore[method-assign]
    manager._decrypt = MagicMock(return_value=None)  # type: ignore[method-assign]
    assert (
        await manager._load_tenant_secret_value(
            "11111111-1111-1111-1111-111111111111",
            "email.google.oauth_client_id",
        )
        is None
    )
    assert manager.get_secret_cached(
        "11111111-1111-1111-1111-111111111111",
        "email.google.oauth_client_id",
    ) is None

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "provider": "google",
            "client_id_ref": "email.google.oauth_client_id",
            "client_secret_ref": "email.google.oauth_client_secret",
            "redirect_uri": "https://example.com/oauth/callback",
            "enabled": False,
        }
    )
    with pytest.raises(ValueError, match="disabled"):
        await manager._resolve_provider_oauth_credentials(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
        )

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "provider": "google",
            "client_id_ref": "email.google.oauth_client_id",
            "client_secret_ref": "email.google.oauth_client_secret",
            "redirect_uri": "https://example.com/oauth/callback",
            "enabled": True,
        }
    )
    manager._load_tenant_secret_value = AsyncMock(  # type: ignore[method-assign]
        side_effect=["client-id", None]
    )
    with pytest.raises(ValueError, match="client secret"):
        await manager._resolve_provider_oauth_credentials(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
        )

    manager._load_tenant_secret_value = AsyncMock(  # type: ignore[method-assign]
        side_effect=["client-id", "client-secret"]
    )
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "provider": "google",
            "client_id_ref": "email.google.oauth_client_id",
            "client_secret_ref": "email.google.oauth_client_secret",
            "redirect_uri": "   ",
            "enabled": True,
        }
    )
    with pytest.raises(ValueError, match="redirect URI is missing"):
        await manager._resolve_provider_oauth_credentials(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
        )

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert (
        await manager.get_email_provider_config(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
        )
        is None
    )

    manager.get_email_provider_config = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._load_tenant_secret_value = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="client_id must be provided"):
        await manager.put_email_provider_config(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
            client_id=None,
            client_secret=None,
            redirect_uri="https://example.com/oauth/callback",
            enabled=True,
            actor=_actor(),
        )

    manager._load_tenant_secret_value = AsyncMock(  # type: ignore[method-assign]
        side_effect=["client-id", None]
    )
    with pytest.raises(ValueError, match="client_secret must be provided"):
        await manager.put_email_provider_config(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
            client_id=None,
            client_secret=None,
            redirect_uri="https://example.com/oauth/callback",
            enabled=True,
            actor=_actor(),
        )

    manager._load_tenant_secret_value = AsyncMock(  # type: ignore[method-assign]
        side_effect=["client-id", "client-secret"]
    )
    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to store tenant email provider config"):
        await manager.put_email_provider_config(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
            client_id=None,
            client_secret=None,
            redirect_uri="https://example.com/oauth/callback",
            enabled=True,
            actor=_actor(),
        )

    class _MissingAccessTokenAuth:
        def __init__(self, *, client_id: str, client_secret: str, redirect_uri: str) -> None:
            self.client_id = client_id
            self.client_secret = client_secret
            self.redirect_uri = redirect_uri

        async def exchange_code(self, code: str) -> dict[str, Any]:
            return {"refresh_token": "refresh-token"}

        async def get_user_email(self, access_token: str) -> str:
            return "owner@example.com"

    import zetherion_ai.skills.gmail.auth as gmail_auth_mod

    monkeypatch.setattr(gmail_auth_mod, "GmailAuth", _MissingAccessTokenAuth)
    manager.consume_email_oauth_state = AsyncMock(  # type: ignore[method-assign]
        return_value={"state": "email_state", "account_hint": "owner@example.com"}
    )
    manager._resolve_provider_oauth_credentials = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "provider": "google",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "redirect_uri": "https://example.com/oauth/callback",
        }
    )
    with pytest.raises(ValueError, match="access token"):
        await manager.exchange_google_oauth_code(
            tenant_id="11111111-1111-1111-1111-111111111111",
            code="abc123",
            state="email_state",
            actor=_actor(),
        )

    class _MissingEmailAuth(_MissingAccessTokenAuth):
        async def exchange_code(self, code: str) -> dict[str, Any]:
            return {"access_token": "access-token", "refresh_token": "refresh-token"}

        async def get_user_email(self, access_token: str) -> str:
            return ""

    monkeypatch.setattr(gmail_auth_mod, "GmailAuth", _MissingEmailAuth)
    with pytest.raises(ValueError, match="account email"):
        await manager.exchange_google_oauth_code(
            tenant_id="11111111-1111-1111-1111-111111111111",
            code="abc123",
            state="email_state",
            actor=_actor(),
        )

    class _MissingRefreshTokenAuth(_MissingAccessTokenAuth):
        async def exchange_code(self, code: str) -> dict[str, Any]:
            return {"access_token": "access-token"}

    monkeypatch.setattr(gmail_auth_mod, "GmailAuth", _MissingRefreshTokenAuth)
    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="refresh_token"):
        await manager.exchange_google_oauth_code(
            tenant_id="11111111-1111-1111-1111-111111111111",
            code="abc123",
            state="email_state",
            actor=_actor(),
        )


@pytest.mark.asyncio
async def test_email_oauth_exchange_reuses_existing_refresh_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    class _ReuseRefreshTokenAuth:
        def __init__(self, *, client_id: str, client_secret: str, redirect_uri: str) -> None:
            self.client_id = client_id
            self.client_secret = client_secret
            self.redirect_uri = redirect_uri

        async def exchange_code(self, code: str) -> dict[str, Any]:
            return {"access_token": f"access-{code}", "expires_in": 900, "scope": "email"}

        async def get_user_email(self, access_token: str) -> str:
            return "owner@example.com"

    import zetherion_ai.skills.gmail.auth as gmail_auth_mod

    monkeypatch.setattr(gmail_auth_mod, "GmailAuth", _ReuseRefreshTokenAuth)
    manager.consume_email_oauth_state = AsyncMock(  # type: ignore[method-assign]
        return_value={"state": "email_state", "account_hint": "owner@example.com"}
    )
    manager._resolve_provider_oauth_credentials = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "provider": "google",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "redirect_uri": "https://example.com/oauth/callback",
        }
    )
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={"account_id": "acc-1", "refresh_token_enc": "enc-refresh"}
    )
    manager._decrypt = MagicMock(return_value="refresh-from-existing")  # type: ignore[method-assign]
    manager._upsert_email_account = AsyncMock(  # type: ignore[method-assign]
        return_value={"account_id": "acc-1", "email_address": "owner@example.com"}
    )
    manager._emit_email_event = AsyncMock(return_value=None)  # type: ignore[method-assign]

    exchanged = await manager.exchange_google_oauth_code(
        tenant_id="11111111-1111-1111-1111-111111111111",
        code="abc123",
        state="email_state",
        actor=_actor(),
    )

    assert exchanged["account_id"] == "acc-1"
    assert (
        manager._upsert_email_account.await_args.kwargs["refresh_token"]
        == "refresh-from-existing"
    )


@pytest.mark.asyncio
async def test_email_account_upsert_guard_branches() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Invalid account status"):
        await manager._upsert_email_account(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
            email_address="owner@example.com",
            access_token="access-token",
            refresh_token="refresh-token",
            scopes=["email"],
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
            actor=_actor(),
            external_account_id="owner@example.com",
            oauth_subject="owner@example.com",
            status="not-valid",
            metadata=None,
        )

    manager._fetchval = AsyncMock(return_value="acc-1")  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(side_effect=[None, None])  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to upsert tenant email account"):
        await manager._upsert_email_account(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="google",
            email_address="owner@example.com",
            access_token="access-token",
            refresh_token="refresh-token",
            scopes=["email"],
            token_expiry=datetime.now(UTC) + timedelta(hours=1),
            actor=_actor(),
            external_account_id="owner@example.com",
            oauth_subject="owner@example.com",
            status="connected",
            metadata={"source": "test"},
        )


@pytest.mark.asyncio
async def test_email_account_mutation_and_refresh_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._emit_email_event = AsyncMock(return_value=None)  # type: ignore[method-assign]

    before_account = {
        "account_id": "acc-1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "provider": "google",
        "external_account_id": "owner@example.com",
        "email_address": "owner@example.com",
        "oauth_subject": "owner@example.com",
        "status": "connected",
        "scopes": ["email"],
        "token_expiry": datetime.now(UTC) + timedelta(hours=1),
        "sync_cursor": None,
        "primary_calendar_id": None,
        "metadata": {},
        "created_by": "op",
        "updated_by": "op",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }

    manager._fetchval = AsyncMock(return_value="acc-1")  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            before_account,  # _upsert before
            before_account,  # _upsert returning row
            before_account,  # patch before
            {**before_account, "status": "degraded"},  # patch after
            before_account,  # delete before
        ]
    )
    manager._fetch = AsyncMock(return_value=[before_account])  # type: ignore[method-assign]
    manager._execute = AsyncMock(return_value="DELETE 1")  # type: ignore[method-assign]
    manager._get_email_account_record = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                **before_account,
                "access_token_enc": "access-token",
                "refresh_token_enc": "refresh-token",
                "token_expiry": datetime.now(UTC) + timedelta(hours=1),
            },
            {
                **before_account,
                "access_token_enc": "expired-access-token",
                "refresh_token_enc": "refresh-token",
                "token_expiry": datetime.now(UTC) - timedelta(seconds=1),
                "scopes": ["email"],
            },
        ]
    )

    upserted = await manager._upsert_email_account(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="google",
        email_address="owner@example.com",
        access_token="access-token",
        refresh_token="refresh-token",
        scopes=["email"],
        token_expiry=datetime.now(UTC) + timedelta(hours=1),
        actor=_actor(),
        external_account_id="owner@example.com",
        oauth_subject="owner@example.com",
        status="connected",
        metadata={"source": "test"},
    )
    assert upserted["account_id"] == "acc-1"

    listed = await manager.list_email_accounts(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="google",
    )
    assert listed[0]["email_address"] == "owner@example.com"

    patched = await manager.patch_email_account(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
        status="degraded",
        metadata={"note": "degraded"},
        actor=_actor(),
    )
    assert patched["status"] == "degraded"

    deleted = await manager.delete_email_account(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
        actor=_actor(),
    )
    assert deleted is True

    # Fresh token path (no refresh required).
    fresh = await manager._refresh_google_access_token_if_needed(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
    )
    assert fresh["access_token"] == "access-token"

    class _FakeGmailAuth:
        def __init__(self, *, client_id: str, client_secret: str, redirect_uri: str) -> None:
            self.client_id = client_id
            self.client_secret = client_secret
            self.redirect_uri = redirect_uri

        async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
            return {
                "access_token": "new-access-token",
                "refresh_token": "new-refresh-token",
                "expires_in": 1800,
                "scope": "email profile",
            }

    import zetherion_ai.skills.gmail.auth as gmail_auth_mod

    monkeypatch.setattr(gmail_auth_mod, "GmailAuth", _FakeGmailAuth)
    manager._resolve_provider_oauth_credentials = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "provider": "google",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "redirect_uri": "https://example.com/oauth/callback",
        }
    )

    refreshed = await manager._refresh_google_access_token_if_needed(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
    )
    assert refreshed["access_token"] == "new-access-token"

    manager._get_email_account_record = TenantAdminManager._get_email_account_record.__get__(  # type: ignore[method-assign]
        manager,
        TenantAdminManager,
    )
    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="Email account not found"):
        await manager._get_email_account_record(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="missing",
        )


@pytest.mark.asyncio
async def test_email_account_guard_branches_cover_patch_delete_refresh_and_sync_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._emit_email_event = AsyncMock(return_value=None)  # type: ignore[method-assign]

    undecryptable_conn = _FakeConn()
    undecryptable_conn.fetch.side_effect = [
        [],
        [{"tenant_id": "t1", "name": "OPENAI_API_KEY", "value_enc": "enc"}],
    ]
    undecryptable_manager = TenantAdminManager(pool=_FakePool(undecryptable_conn))  # type: ignore[arg-type]
    undecryptable_manager._decrypt = MagicMock(return_value=None)  # type: ignore[method-assign]
    await undecryptable_manager.refresh()
    assert undecryptable_manager.get_secret_cached("t1", "OPENAI_API_KEY") is None

    before_account = {
        "account_id": "acc-1",
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "provider": "google",
        "external_account_id": "owner@example.com",
        "email_address": "owner@example.com",
        "oauth_subject": "owner@example.com",
        "status": "connected",
        "scopes": ["email"],
        "token_expiry": datetime.now(UTC) + timedelta(hours=1),
        "sync_cursor": "cursor-1",
        "primary_calendar_id": None,
        "metadata": {"existing": True},
        "created_by": "op",
        "updated_by": "op",
        "created_at": datetime.now(UTC),
        "updated_at": datetime.now(UTC),
    }

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="Email account not found"):
        await manager.patch_email_account(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            actor=_actor(),
        )

    manager._fetchrow = AsyncMock(return_value=before_account)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="Invalid account status"):
        await manager.patch_email_account(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            actor=_actor(),
            status="bad-status",
        )

    manager._fetchrow = AsyncMock(side_effect=[before_account, None])  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to patch email account"):
        await manager.patch_email_account(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            actor=_actor(),
            metadata={"note": "x"},
        )

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert (
        await manager.delete_email_account(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            actor=_actor(),
        )
        is False
    )

    manager._fetchrow = AsyncMock(return_value=before_account)  # type: ignore[method-assign]
    manager._execute = AsyncMock(return_value="DELETE 0")  # type: ignore[method-assign]
    assert (
        await manager.delete_email_account(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            actor=_actor(),
        )
        is False
    )
    manager._write_audit.assert_not_awaited()
    manager._emit_email_event.assert_not_awaited()

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            before_account,
            {**before_account, "sync_cursor": "cursor-2", "metadata": {"existing": True}},
        ]
    )
    patched_cursor = await manager.patch_email_account(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
        actor=_actor(),
        metadata={},
        sync_cursor="cursor-2",
    )
    assert patched_cursor["sync_cursor"] == "cursor-2"
    patch_args = manager._fetchrow.await_args_list[-1].args
    assert json.loads(patch_args[4]) == {"existing": True}
    assert patch_args[5] == "cursor-2"

    manager._get_email_account_record = AsyncMock(  # type: ignore[method-assign]
        return_value={
            **before_account,
            "access_token_enc": "",
            "refresh_token_enc": "refresh-token",
            "token_expiry": datetime.now(UTC) - timedelta(minutes=5),
        }
    )
    with pytest.raises(ValueError, match="access token is missing"):
        await manager._refresh_google_access_token_if_needed(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
        )

    manager._get_email_account_record = AsyncMock(  # type: ignore[method-assign]
        return_value={
            **before_account,
            "access_token_enc": "expired-access-token",
            "refresh_token_enc": "",
            "token_expiry": datetime.now(UTC) - timedelta(minutes=5),
        }
    )
    with pytest.raises(ValueError, match="refresh token is missing"):
        await manager._refresh_google_access_token_if_needed(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
        )

    class _MissingRefreshAccessAuth:
        def __init__(self, *, client_id: str, client_secret: str, redirect_uri: str) -> None:
            self.client_id = client_id
            self.client_secret = client_secret
            self.redirect_uri = redirect_uri

        async def refresh_access_token(self, refresh_token: str) -> dict[str, Any]:
            return {"refresh_token": refresh_token, "expires_in": 900}

    import zetherion_ai.skills.gmail.auth as gmail_auth_mod

    monkeypatch.setattr(gmail_auth_mod, "GmailAuth", _MissingRefreshAccessAuth)
    manager._get_email_account_record = AsyncMock(  # type: ignore[method-assign]
        return_value={
            **before_account,
            "access_token_enc": "expired-access-token",
            "refresh_token_enc": "refresh-token",
            "token_expiry": datetime.now(UTC) - timedelta(minutes=5),
            "scopes": ["email"],
        }
    )
    manager._resolve_provider_oauth_credentials = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "provider": "google",
            "client_id": "client-id",
            "client_secret": "client-secret",
            "redirect_uri": "https://example.com/oauth/callback",
        }
    )
    manager._execute = AsyncMock(return_value="UPDATE 1")  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="did not return access token"):
        await manager._refresh_google_access_token_if_needed(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
        )

    class _FakeResponse:
        def __init__(self, payload: Any, status_code: int = 200) -> None:
            self._payload = payload
            self.status_code = status_code

        def json(self) -> Any:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("http error")

    class _CalendarHttpClient:
        async def __aenter__(self) -> _CalendarHttpClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            if "calendar/v3/users/me/calendarList" in url:
                return _FakeResponse({"items": ["bad", {"id": "primary", "summary": "Main"}]})
            raise AssertionError(f"unexpected url: {url}")

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _CalendarHttpClient())
    manager._refresh_google_access_token_if_needed = AsyncMock(  # type: ignore[method-assign]
        return_value={"access_token": "token"}
    )
    calendars = await manager.list_google_calendars(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
    )
    assert calendars == [
        {
            "id": "primary",
            "summary": "Main",
            "primary": False,
            "time_zone": None,
            "access_role": None,
        }
    ]

    manager._get_email_account_record = AsyncMock(return_value=before_account)  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to set primary calendar"):
        await manager.set_email_primary_calendar(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            calendar_id="primary",
            actor=_actor(),
        )

    class _CalendarItemsShapeHttpClient:
        async def __aenter__(self) -> _CalendarItemsShapeHttpClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse({"items": "not-a-list"})

    monkeypatch.setattr(
        httpx,
        "AsyncClient",
        lambda *args, **kwargs: _CalendarItemsShapeHttpClient(),
    )
    assert (
        await manager.list_google_calendars(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
        )
        == []
    )

    with pytest.raises(ValueError, match="Invalid sync direction"):
        await manager._create_sync_job(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            direction="sideways",
            actor=_actor(),
            idempotency_key=None,
        )

    with pytest.raises(ValueError, match="Invalid sync status"):
        await manager._complete_sync_job(
            job_id="11111111-1111-1111-1111-111111111111",
            status="sideways",
        )


@pytest.mark.asyncio
async def test_email_http_and_scoring_helpers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    class _FakeResponse:
        def __init__(self, payload: Any, status_code: int = 200) -> None:
            self._payload = payload
            self.status_code = status_code

        def json(self) -> Any:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("http error")

    class _FakeHttpClient:
        async def __aenter__(self) -> _FakeHttpClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            if "gmail/v1/users/me/messages/" in url:
                msg_id = url.rsplit("/", 1)[-1]
                return _FakeResponse(
                    {
                        "id": msg_id,
                        "threadId": "thread-1",
                        "snippet": "Urgent invoice due today",
                        "internalDate": "1730000000000",
                        "payload": {
                            "headers": [
                                {"name": "Subject", "value": "Urgent invoice"},
                                {"name": "From", "value": "billing@example.com"},
                                {"name": "Date", "value": "Tue, 01 Jan 2026 00:00:00 +0000"},
                            ]
                        },
                    }
                )
            if "calendar/v3/users/me/calendarList" in url:
                return _FakeResponse(
                    {"items": [{"id": "primary", "summary": "Main", "primary": True}]}
                )
            return _FakeResponse({"messages": [{"id": "m1"}]})

        async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse({"id": "event-1"})

        async def patch(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse({"id": "event-1"})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _FakeHttpClient())

    unread = await manager._google_list_unread_messages(access_token="token", max_results=5)
    assert unread
    assert unread[0]["message_id"] == "m1"

    manager.set_critical_scorer(
        AsyncMock(return_value={"score": 0.9, "reason_codes": ["model_high"]})  # type: ignore[arg-type]
    )
    severity, score, reasons, entities = await manager._criticality_score(
        subject="Urgent security incident",
        body_preview="Please pay invoice today",
        sender="alerts@github.com",
    )
    assert severity in {"high", "critical"}
    assert score > 0.5
    assert "model_high" in reasons
    assert isinstance(entities["emails"], list)

    no_vector = await manager._store_insight_vector(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
        insight_type="critical_email",
        summary="summary",
        metadata={},
    )
    assert no_vector is None

    class _Memory:
        def __init__(self) -> None:
            self.store_memory = AsyncMock(return_value="vec-1")

    manager.set_vector_memory(_Memory())  # type: ignore[arg-type]
    vector_id = await manager._store_insight_vector(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
        insight_type="critical_email",
        summary="summary",
        metadata={},
    )
    assert vector_id == "vec-1"

    manager._refresh_google_access_token_if_needed = AsyncMock(  # type: ignore[method-assign]
        return_value={"access_token": "token"}
    )
    calendars = await manager.list_google_calendars(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
    )
    assert calendars[0]["id"] == "primary"

    writes = await manager._apply_calendar_operations(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
        access_token="token",
        source="cgs-admin",
        operations=[
            {
                "action": "create",
                "idempotency_key": "idem-1",
                "source": "cgs-admin",
                "calendar_id": "primary",
                "event": {"summary": "New event"},
            },
            {
                "action": "update",
                "idempotency_key": "idem-2",
                "source": "cgs-admin",
                "calendar_id": "primary",
                "event_id": "event-1",
                "event": {"summary": "Updated event"},
            },
        ],
    )
    assert writes == 2

    with pytest.raises(ValueError, match="delete operations are disabled"):
        await manager._apply_calendar_operations(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            access_token="token",
            source="cgs-admin",
            operations=[
                {
                    "action": "delete",
                    "idempotency_key": "idem-3",
                    "source": "cgs-admin",
                    "calendar_id": "primary",
                    "event": {"summary": "X"},
                }
            ],
        )


@pytest.mark.asyncio
async def test_email_http_and_calendar_helper_additional_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._emit_email_event = AsyncMock(return_value=None)  # type: ignore[method-assign]

    before_account = {
        "account_id": "acc-1",
        "primary_calendar_id": None,
    }
    after_account = {"account_id": "acc-1", "primary_calendar_id": "primary"}
    manager._get_email_account_record = AsyncMock(return_value=before_account)  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(return_value=after_account)  # type: ignore[method-assign]

    updated = await manager.set_email_primary_calendar(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
        calendar_id="primary",
        actor=_actor(),
    )
    assert updated["primary_calendar_id"] == "primary"
    manager._write_audit.assert_awaited()

    class _FakeResponse:
        def __init__(self, payload: Any, status_code: int = 200) -> None:
            self._payload = payload
            self.status_code = status_code

        def json(self) -> Any:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("http error")

    class _UnreadHttpClient:
        async def __aenter__(self) -> _UnreadHttpClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            if "gmail/v1/users/me/messages/" in url:
                msg_id = url.rsplit("/", 1)[-1]
                if msg_id == "m1":
                    return _FakeResponse(["bad-detail"])
                return _FakeResponse(
                    {
                        "id": "m2",
                        "threadId": "thread-2",
                        "snippet": "Heads up",
                        "internalDate": "1730000000000",
                        "payload": {
                            "headers": [
                                {"name": "X-Test", "value": "skip"},
                                {"name": "Date", "value": "Tue, 01 Jan 2026 00:00:00 +0000"},
                            ]
                        },
                    }
                )
            return _FakeResponse({"messages": [{"id": "m1"}, {"id": "m2"}]})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _UnreadHttpClient())
    unread = await manager._google_list_unread_messages(access_token="token", max_results=5)
    assert unread == [
        {
            "message_id": "m2",
            "thread_id": "thread-2",
            "subject": "",
            "from_email": "",
            "body_preview": "Heads up",
            "received_at": datetime.fromtimestamp(1730000000, tz=UTC).isoformat(),
            "date_header": "Tue, 01 Jan 2026 00:00:00 +0000",
        }
    ]

    manager.set_critical_scorer(  # type: ignore[arg-type]
        AsyncMock(return_value={"score": "0.9", "reason_codes": ["", "trusted_sender", "custom"]})
    )
    severity, score, reasons, _ = await manager._criticality_score(
        subject="Routine follow up",
        body_preview="Just checking in",
        sender="alerts@github.com",
    )
    assert severity == "normal"
    assert score > 0
    assert "model_score" in reasons
    assert "custom" in reasons
    assert reasons.count("trusted_sender") == 1

    assert (
        await manager._apply_calendar_operations(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            access_token="token",
            source="cgs-admin",
            operations=[],
        )
        == 0
    )

    class _CalendarHttpClient:
        async def __aenter__(self) -> _CalendarHttpClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse({"id": "event-1"})

        async def patch(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse({"id": "event-1"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _CalendarHttpClient())
    with pytest.raises(ValueError, match="idempotency_key"):
        await manager._apply_calendar_operations(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            access_token="token",
            source="cgs-admin",
            operations=[{"action": "create", "calendar_id": "primary", "event": {"summary": "x"}}],
        )
    with pytest.raises(ValueError, match="requires source"):
        await manager._apply_calendar_operations(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            access_token="token",
            source="",
            operations=[
                {
                    "action": "create",
                    "idempotency_key": "idem-1",
                    "calendar_id": "primary",
                    "event": {"summary": "x"},
                }
            ],
        )
    with pytest.raises(ValueError, match="requires calendar_id"):
        await manager._apply_calendar_operations(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            access_token="token",
            source="cgs-admin",
            operations=[
                {
                    "action": "create",
                    "idempotency_key": "idem-1",
                    "event": {"summary": "x"},
                }
            ],
        )
    with pytest.raises(ValueError, match="requires event object"):
        await manager._apply_calendar_operations(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            access_token="token",
            source="cgs-admin",
            operations=[
                {
                    "action": "create",
                    "idempotency_key": "idem-1",
                    "calendar_id": "primary",
                    "event": "bad",
                }
            ],
        )
    with pytest.raises(ValueError, match="requires event_id"):
        await manager._apply_calendar_operations(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            access_token="token",
            source="cgs-admin",
            operations=[
                {
                    "action": "update",
                    "idempotency_key": "idem-1",
                    "calendar_id": "primary",
                    "event": {"summary": "x"},
                }
            ],
        )
    with pytest.raises(ValueError, match="Unsupported calendar operation action"):
        await manager._apply_calendar_operations(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            access_token="token",
            source="cgs-admin",
            operations=[
                {
                    "action": "archive",
                    "idempotency_key": "idem-1",
                    "calendar_id": "primary",
                    "event": {"summary": "x"},
                }
            ],
        )


@pytest.mark.asyncio
async def test_email_http_and_scoring_guard_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Invalid role"):
        await manager.update_discord_user_role(
            tenant_id="11111111-1111-1111-1111-111111111111",
            discord_user_id=1,
            role="bad-role",
            actor=_actor(),
        )

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await manager.resolve_tenant_for_discord(guild_id=10, channel_id=20) is None

    class _FakeResponse:
        def __init__(self, payload: Any, status_code: int = 200) -> None:
            self._payload = payload
            self.status_code = status_code

        def json(self) -> Any:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("http error")

    class _GuardHttpClient:
        def __init__(self) -> None:
            self._detail_calls = 0

        async def __aenter__(self) -> _GuardHttpClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            if "gmail/v1/users/me/messages/" in url:
                self._detail_calls += 1
                if self._detail_calls == 1:
                    return _FakeResponse({}, status_code=404)
                return _FakeResponse(
                    {
                        "id": "m2",
                        "threadId": "thread-2",
                        "snippet": "Heads up",
                        "internalDate": "not-a-timestamp",
                        "payload": {
                            "headers": [
                                "bad",
                                {"name": "Subject", "value": "Follow up"},
                                {"name": "From", "value": "team@example.com"},
                            ]
                        },
                    }
                )
            return _FakeResponse({"messages": [{"id": "m1"}, {"id": "m2"}]})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _GuardHttpClient())
    unread = await manager._google_list_unread_messages(access_token="token", max_results=5)
    assert unread == [
        {
            "message_id": "m2",
            "thread_id": "thread-2",
            "subject": "Follow up",
            "from_email": "team@example.com",
            "body_preview": "Heads up",
            "received_at": None,
            "date_header": "",
        }
    ]

    class _BadListingHttpClient:
        async def __aenter__(self) -> _BadListingHttpClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse({"messages": "bad-shape"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _BadListingHttpClient())
    assert await manager._google_list_unread_messages(access_token="token", max_results=5) == []

    manager.set_critical_scorer(AsyncMock(side_effect=RuntimeError("boom")))  # type: ignore[arg-type]
    severity, score, reasons, entities = await manager._criticality_score(
        subject="Critical invoice due today",
        body_preview="Security team needs payment immediately",
        sender="alerts@github.com",
    )
    assert severity in {"critical", "high"}
    assert "urgent_keywords" in reasons
    assert "finance_keywords" in reasons
    assert "security_keywords" in reasons
    assert "trusted_sender" in reasons
    assert isinstance(entities["emails"], list)
    assert entities["has_deadline_hint"] is True

    manager.set_critical_scorer(  # type: ignore[arg-type]
        AsyncMock(return_value={"score": "0.8", "reason_codes": ["trusted_sender", "extra_code"]})
    )
    severity, score, reasons, _ = await manager._criticality_score(
        subject="Routine follow up",
        body_preview="Just checking in",
        sender="ops@example.com",
    )
    assert severity == "normal"
    assert "model_score" in reasons
    assert "extra_code" in reasons
    assert reasons.count("trusted_sender") <= 1

    manager.set_critical_scorer(  # type: ignore[arg-type]
        AsyncMock(return_value={"score": "not-a-number", "reason_codes": "bad"})
    )
    severity, score, reasons, _ = await manager._criticality_score(
        subject="Routine follow up",
        body_preview="Just checking in",
        sender="ops@example.com",
    )
    assert severity == "normal"
    assert "model_score" not in reasons


@pytest.mark.asyncio
async def test_email_sync_and_listing_paths_cover_success_failure_and_reindex() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._fetchval = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._execute = AsyncMock(return_value="DELETE 1")  # type: ignore[method-assign]
    manager._refresh_google_access_token_if_needed = AsyncMock(  # type: ignore[method-assign]
        return_value={"access_token": "token"}
    )
    manager._google_list_unread_messages = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "message_id": "m1",
                "subject": "Urgent incident",
                "from_email": "alerts@github.com",
                "body_preview": "ASAP action required",
                "received_at": datetime.now(UTC).isoformat(),
                "thread_id": "t1",
                "date_header": "now",
            }
        ]
    )
    manager.list_google_calendars = AsyncMock(return_value=[{"id": "primary"}])  # type: ignore[method-assign]
    manager._apply_calendar_operations = AsyncMock(return_value=1)  # type: ignore[method-assign]

    result = await manager.sync_email_account(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="22222222-2222-2222-2222-222222222222",
        actor=_actor(),
        direction="bi_directional",
        idempotency_key="idem-1",
        source="cgs-admin",
        max_results=10,
    )
    assert result["status"] == "succeeded"
    assert result["counts"]["messages_scanned"] == 1

    manager._google_list_unread_messages = AsyncMock(side_effect=RuntimeError("sync failed"))  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="sync failed"):
        await manager.sync_email_account(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="22222222-2222-2222-2222-222222222222",
            actor=_actor(),
            direction="email",
            source="cgs-admin",
        )

    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"item_id": "1", "severity": "high", "status": "open"}]
    )
    critical = await manager.list_email_critical_items(
        tenant_id="11111111-1111-1111-1111-111111111111",
        status="open",
        severity="high",
        limit=10,
    )
    assert critical[0]["severity"] == "high"

    with pytest.raises(ValueError, match="Invalid critical status"):
        await manager.list_email_critical_items(
            tenant_id="11111111-1111-1111-1111-111111111111",
            status="bad",
        )

    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "insight_id": "33333333-3333-3333-3333-333333333333",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "account_id": "22222222-2222-2222-2222-222222222222",
                "insight_type": "critical_email",
                "confidence": 0.9,
                "payload_json": {"summary": "Critical message"},
                "source_message_ids": ["m1"],
                "vector_id": None,
                "created_at": datetime.now(UTC),
            }
        ]
    )
    listed_insights = await manager.list_email_insights(
        tenant_id="11111111-1111-1111-1111-111111111111",
        insight_type="critical_email",
        min_confidence=0.5,
        limit=5,
    )
    assert listed_insights[0]["insight_type"] == "critical_email"

    no_vector = await manager.reindex_email_insights(
        tenant_id="11111111-1111-1111-1111-111111111111",
        actor=_actor(),
        insight_type="critical_email",
    )
    assert no_vector["reason"] == "vector_memory_unavailable"

    class _VectorMemory:
        def __init__(self) -> None:
            self.store_memory = AsyncMock(return_value="vec-99")

    manager.set_vector_memory(_VectorMemory())  # type: ignore[arg-type]
    manager._execute = AsyncMock(return_value="UPDATE 1")  # type: ignore[method-assign]
    reindexed = await manager.reindex_email_insights(
        tenant_id="11111111-1111-1111-1111-111111111111",
        actor=_actor(),
        insight_type="critical_email",
    )
    assert reindexed["reindexed"] == 1

    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"event_id": "evt-1", "event_type": "email.critical.detected"}]
    )
    events = await manager.list_email_events(
        tenant_id="11111111-1111-1111-1111-111111111111",
        event_type="email.critical.detected",
        limit=5,
    )
    assert events[0]["event_id"] == "evt-1"


@pytest.mark.asyncio
async def test_email_sync_direction_specific_branches_and_vector_failures() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._fetchval = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._execute = AsyncMock(return_value="UPDATE 1")  # type: ignore[method-assign]
    manager._refresh_google_access_token_if_needed = AsyncMock(  # type: ignore[method-assign]
        return_value={"access_token": "token"}
    )
    manager._apply_retention_policies = AsyncMock(return_value={"deleted": 0})  # type: ignore[method-assign]
    manager._create_sync_job = AsyncMock(return_value="job-1")  # type: ignore[method-assign]
    manager._complete_sync_job = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._emit_email_event = AsyncMock(return_value=None)  # type: ignore[method-assign]

    class _BrokenVectorMemory:
        async def store_memory(self, *args: Any, **kwargs: Any) -> str:
            raise RuntimeError("vector unavailable")

    manager.set_vector_memory(_BrokenVectorMemory())  # type: ignore[arg-type]
    assert (
        await manager._store_insight_vector(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="acc-1",
            insight_type="critical_email",
            summary="summary",
            metadata={},
        )
        is None
    )

    manager._google_list_unread_messages = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "message_id": "m1",
                "subject": "Routine update",
                "from_email": "team@example.com",
                "body_preview": "Nothing urgent",
                "received_at": "not-a-date",
                "thread_id": "t1",
                "date_header": "",
            }
        ]
    )
    manager._criticality_score = AsyncMock(  # type: ignore[method-assign]
        return_value=("normal", 0.1, ["routine"], {"emails": [], "has_deadline_hint": False})
    )
    email_only = await manager.sync_email_account(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="22222222-2222-2222-2222-222222222222",
        actor=_actor(),
        direction="email",
        source="cgs-admin",
    )
    assert email_only["counts"]["messages_scanned"] == 1
    assert email_only["counts"]["critical_created"] == 0
    assert manager._fetchval.await_count == 0

    manager.list_google_calendars = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"id": "primary"}, {"id": "secondary"}]
    )
    calendar_read = await manager.sync_email_account(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="22222222-2222-2222-2222-222222222222",
        actor=_actor(),
        direction="calendar_read",
        source="cgs-admin",
    )
    assert calendar_read["counts"]["calendar_reads"] == 2
    assert calendar_read["counts"]["calendar_writes"] == 0

    manager._apply_calendar_operations = AsyncMock(return_value=0)  # type: ignore[method-assign]
    calendar_write = await manager.sync_email_account(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="22222222-2222-2222-2222-222222222222",
        actor=_actor(),
        direction="calendar_write",
        source="cgs-admin",
        calendar_operations=[],
    )
    assert calendar_write["counts"]["calendar_reads"] == 0
    assert calendar_write["counts"]["calendar_writes"] == 0

    manager._google_list_unread_messages = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "message_id": "m2",
                "subject": "Routine update",
                "from_email": "team@example.com",
                "body_preview": "Nothing urgent",
                "received_at": "",
                "thread_id": "t2",
                "date_header": "",
            }
        ]
    )
    email_blank_received = await manager.sync_email_account(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="22222222-2222-2222-2222-222222222222",
        actor=_actor(),
        direction="email",
        source="cgs-admin",
    )
    assert email_blank_received["counts"]["messages_scanned"] == 1

    with pytest.raises(ValueError, match="Invalid sync direction"):
        await manager.sync_email_account(
            tenant_id="11111111-1111-1111-1111-111111111111",
            account_id="22222222-2222-2222-2222-222222222222",
            actor=_actor(),
            direction="sideways",
            source="cgs-admin",
        )


@pytest.mark.asyncio
async def test_email_admin_listing_and_reindex_branch_variants() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._execute = AsyncMock(return_value="UPDATE 1")  # type: ignore[method-assign]

    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [{"item_id": "crit-1", "severity": "critical", "status": "open"}],
            [{"insight_id": "ins-1", "payload_json": {"summary": "hello"}}],
            [{"event_id": "evt-2", "event_type": "email.synced"}],
        ]
    )

    listed_critical = await manager.list_email_critical_items(
        tenant_id="11111111-1111-1111-1111-111111111111",
        severity="CRITICAL",
        limit=0,
    )
    assert listed_critical[0]["item_id"] == "crit-1"

    with pytest.raises(ValueError, match="Invalid critical severity"):
        await manager.list_email_critical_items(
            tenant_id="11111111-1111-1111-1111-111111111111",
            severity="urgent",
        )

    listed_insights = await manager.list_email_insights(
        tenant_id="11111111-1111-1111-1111-111111111111",
        limit=999,
    )
    assert listed_insights[0]["insight_id"] == "ins-1"

    listed_events = await manager.list_email_events(
        tenant_id="11111111-1111-1111-1111-111111111111",
        limit=999,
    )
    assert listed_events[0]["event_type"] == "email.synced"

    manager.set_vector_memory(MagicMock())  # type: ignore[arg-type]
    manager.list_email_insights = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "insight_id": "skip-1",
                "account_id": "acc-1",
                "insight_type": "critical_email",
                "payload_json": "not-a-dict",
            },
            {
                "insight_id": "skip-2",
                "account_id": "acc-2",
                "insight_type": "critical_email",
                "payload_json": {"alpha": 1, "summary": "   "},
            },
            {
                "insight_id": "keep-1",
                "account_id": "",
                "insight_type": "",
                "payload_json": {"beta": 2},
            },
        ]
    )
    manager._store_insight_vector = AsyncMock(side_effect=[None, "vec-2"])  # type: ignore[method-assign]

    reindexed = await manager.reindex_email_insights(
        tenant_id="11111111-1111-1111-1111-111111111111",
        actor=_actor(),
    )
    assert reindexed == {"reindexed": 1, "scanned": 3}
    assert manager._execute.await_count == 1
    first_summary = manager._store_insight_vector.await_args_list[0].kwargs["summary"]
    second_summary = manager._store_insight_vector.await_args_list[1].kwargs["summary"]
    assert first_summary == '{"alpha": 1, "summary": "   "}'
    assert second_summary == '{"beta": 2}'


@pytest.mark.asyncio
async def test_email_helper_remaining_branch_paths(monkeypatch: pytest.MonkeyPatch) -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._emit_email_event = AsyncMock(return_value=None)  # type: ignore[method-assign]

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "account_id": "acc-1",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "provider": "google",
            "external_account_id": "owner@example.com",
            "email_address": "owner@example.com",
            "oauth_subject": "owner@example.com",
            "status": "connected",
            "scopes": ["email"],
            "access_token_enc": "access-token",
            "refresh_token_enc": "refresh-token",
            "token_expiry": datetime.now(UTC),
            "sync_cursor": None,
            "primary_calendar_id": None,
            "metadata": {},
            "created_by": "op",
            "updated_by": "op",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    record = await manager._get_email_account_record(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
    )
    assert record["account_id"] == "acc-1"

    class _FakeResponse:
        def __init__(self, payload: Any, status_code: int = 200) -> None:
            self._payload = payload
            self.status_code = status_code

        def json(self) -> Any:
            return self._payload

        def raise_for_status(self) -> None:
            if self.status_code >= 400:
                raise RuntimeError("http error")

    class _HeadersShapeHttpClient:
        async def __aenter__(self) -> _HeadersShapeHttpClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def get(self, url: str, **kwargs: Any) -> _FakeResponse:
            if "gmail/v1/users/me/messages/" in url:
                return _FakeResponse(
                    {
                        "id": "m3",
                        "threadId": "thread-3",
                        "snippet": "Quiet update",
                        "internalDate": "1730000000000",
                        "payload": {"headers": {"name": "Date", "value": "ignored"}},
                    }
                )
            return _FakeResponse({"messages": [{"id": "m3"}]})

    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _HeadersShapeHttpClient())
    unread = await manager._google_list_unread_messages(access_token="token", max_results=5)
    assert unread == [
        {
            "message_id": "m3",
            "thread_id": "thread-3",
            "subject": "",
            "from_email": "",
            "body_preview": "Quiet update",
            "received_at": datetime.fromtimestamp(1730000000, tz=UTC).isoformat(),
            "date_header": "",
        }
    ]

    manager.set_critical_scorer(AsyncMock(return_value={"score": None, "reason_codes": []}))  # type: ignore[arg-type]
    severity, score, reasons, _ = await manager._criticality_score(
        subject="Routine follow up",
        body_preview="Just checking in",
        sender="ops@example.com",
    )
    assert severity == "normal"
    assert score > 0
    assert "model_score" not in reasons

    class _CalendarNoopHttpClient:
        async def __aenter__(self) -> _CalendarNoopHttpClient:
            return self

        async def __aexit__(self, exc_type, exc, tb) -> bool:
            return False

        async def post(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse({"id": "event-1"})

        async def patch(self, url: str, **kwargs: Any) -> _FakeResponse:
            return _FakeResponse({"id": "event-1"})

    monkeypatch.setattr(httpx, "AsyncClient", lambda *args, **kwargs: _CalendarNoopHttpClient())
    write_count = await manager._apply_calendar_operations(
        tenant_id="11111111-1111-1111-1111-111111111111",
        account_id="acc-1",
        access_token="token",
        source="cgs-admin",
        operations=["skip-me"],
    )
    assert write_count == 0


@pytest.mark.asyncio
async def test_messaging_provider_and_chat_policy_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager.set_setting = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._fetch = AsyncMock(return_value=[{"chat_id": "chat-1"}])  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            None,  # get_messaging_provider_config(before)
            {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "provider": "whatsapp",
                "enabled": True,
                "bridge_mode": "local_sidecar",
                "account_ref": "acct-1",
                "session_ref": "sess-1",
                "metadata": {"label": "phone-main"},
                "created_by": "operator-1",
                "updated_by": "operator-1",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
            None,  # get_messaging_chat_policy(before)
            {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "read_enabled": True,
                "send_enabled": True,
                "retention_days": 14,
                "metadata": {"label": "Family"},
                "created_by": "operator-1",
                "updated_by": "operator-1",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
        ]
    )

    provider = await manager.put_messaging_provider_config(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        enabled=True,
        bridge_mode="local_sidecar",
        account_ref="acct-1",
        session_ref="sess-1",
        metadata={"label": "phone-main"},
        actor=_actor(),
    )
    assert provider["provider"] == "whatsapp"

    policy = await manager.put_messaging_chat_policy(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        chat_id="chat-1",
        read_enabled=True,
        send_enabled=True,
        retention_days=14,
        metadata={"label": "Family"},
        actor=_actor(),
    )
    assert policy["chat_id"] == "chat-1"
    manager.set_setting.assert_awaited_once()
    kwargs = manager.set_setting.await_args.kwargs
    assert kwargs["namespace"] == "security"
    assert kwargs["key"] == "messaging_allowlisted_chats"
    assert kwargs["value"] == ["chat-1"]


@pytest.mark.asyncio
async def test_messaging_ingest_list_and_ttl_cleanup_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "read_enabled": True,
                "send_enabled": True,
                "retention_days": 14,
                "metadata": {},
                "created_by": "operator-1",
                "updated_by": "operator-1",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
            {
                "message_id": "99999999-9999-9999-9999-999999999999",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "direction": "inbound",
                "sender_id": "sender-1",
                "sender_name": "Sender",
                "body_enc": "hello",
                "metadata": {"source": "bridge"},
                "action_id": None,
                "event_type": "whatsapp.message.inbound",
                "observed_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(days=14),
                "created_at": datetime.now(UTC),
            },
        ]
    )
    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "message_id": "99999999-9999-9999-9999-999999999999",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "direction": "inbound",
                "sender_id": "sender-1",
                "sender_name": "Sender",
                "body_enc": "hello",
                "metadata": {"source": "bridge"},
                "action_id": None,
                "event_type": "whatsapp.message.inbound",
                "observed_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(days=14),
                "created_at": datetime.now(UTC),
            }
        ]
    )
    manager._execute = AsyncMock(return_value="DELETE 2")  # type: ignore[method-assign]

    stored = await manager.ingest_messaging_message(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        chat_id="chat-1",
        direction="inbound",
        event_type="whatsapp.message.inbound",
        body_text="hello",
        metadata={"source": "bridge"},
        sender_id="sender-1",
        sender_name="Sender",
    )
    assert stored["body_text"] == "hello"

    listed = await manager.list_messaging_messages(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        chat_id="chat-1",
        limit=10,
    )
    assert listed[0]["body_text"] == "hello"

    purged = await manager.purge_expired_messaging_messages(
        tenant_id="11111111-1111-1111-1111-111111111111",
        limit=1000,
    )
    assert purged == 2


@pytest.mark.asyncio
async def test_queue_messaging_send_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "action_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "provider": "whatsapp",
            "chat_id": "chat-1",
            "action_type": "send",
            "payload_json": {"text": "hello"},
            "status": "queued",
            "created_by": "operator-1",
            "request_id": "req-1",
            "change_ticket_id": None,
            "error_code": None,
            "error_detail": None,
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    manager.is_messaging_chat_allowed = AsyncMock(return_value=True)  # type: ignore[method-assign]
    manager.ingest_messaging_message = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "message_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            "direction": "outbound",
            "body_text": "hello",
        }
    )
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    queued = await manager.queue_messaging_send(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        chat_id="chat-1",
        body_text="hello",
        actor=_actor(),
        metadata={"reason": "test"},
    )
    assert queued["action"]["status"] == "queued"
    assert queued["message"]["direction"] == "outbound"

    with pytest.raises(ValueError, match="non-empty message body"):
        await manager.queue_messaging_send(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            body_text="",
            actor=_actor(),
        )

    manager.is_messaging_chat_allowed = AsyncMock(return_value=False)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="not enabled"):
        await manager.queue_messaging_send(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            body_text="hello again",
            actor=_actor(),
        )


@pytest.mark.asyncio
async def test_messaging_lookup_and_filter_helper_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "provider": "whatsapp",
                "enabled": True,
                "bridge_mode": "local_sidecar",
                "account_ref": "acct-1",
                "session_ref": "sess-1",
                "metadata": {},
                "created_by": "operator-1",
                "updated_by": "operator-1",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
            {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "read_enabled": True,
                "send_enabled": False,
                "retention_days": 14,
                "metadata": {},
                "created_by": "operator-1",
                "updated_by": "operator-1",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
            {"read_enabled": True, "send_enabled": False},
            {"read_enabled": False, "send_enabled": True},
            None,
        ]
    )
    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [
                {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "provider": "whatsapp",
                    "chat_id": "chat-1",
                    "read_enabled": True,
                    "send_enabled": False,
                    "retention_days": 14,
                    "metadata": {},
                    "created_by": "operator-1",
                    "updated_by": "operator-1",
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                    "message_count": 1,
                    "last_message_at": datetime.now(UTC),
                }
            ],
            [
                {
                    "message_id": "cccccccc-cccc-cccc-cccc-cccccccccccc",
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "provider": "whatsapp",
                    "chat_id": "chat-1",
                    "direction": "inbound",
                    "sender_id": "sender-1",
                    "sender_name": "Sender",
                    "body_enc": "payload",
                    "metadata": {"source": "bridge"},
                    "action_id": None,
                    "event_type": "whatsapp.message.inbound",
                    "observed_at": datetime.now(UTC),
                    "expires_at": datetime.now(UTC) + timedelta(days=14),
                    "created_at": datetime.now(UTC),
                }
            ],
        ]
    )
    manager._execute = AsyncMock(return_value="DELETE 5")  # type: ignore[method-assign]

    provider_cfg = await manager.get_messaging_provider_config(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
    )
    assert provider_cfg is not None
    assert provider_cfg["provider"] == "whatsapp"

    policy = await manager.get_messaging_chat_policy(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        chat_id="chat-1",
    )
    assert policy is not None
    assert policy["chat_id"] == "chat-1"

    assert (
        await manager.is_messaging_chat_allowed(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            action="read",
        )
        is True
    )
    assert (
        await manager.is_messaging_chat_allowed(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            action="send",
        )
        is True
    )
    assert (
        await manager.is_messaging_chat_allowed(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            action="read",
        )
        is False
    )

    chats = await manager.list_messaging_chats(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        include_inactive=False,
        limit=25,
    )
    assert chats[0]["message_count"] == 1

    messages = await manager.list_messaging_messages(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        chat_id="chat-1",
        direction="inbound",
        limit=10,
    )
    assert messages[0]["body_text"] == "payload"

    purged_global = await manager.purge_expired_messaging_messages(limit=100)
    assert purged_global == 5

    with pytest.raises(ValueError, match="Unsupported messaging action"):
        await manager.is_messaging_chat_allowed(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            action="unknown",
        )

    with pytest.raises(ValueError, match="Invalid direction"):
        await manager.list_messaging_messages(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            direction="sideways",
        )


@pytest.mark.asyncio
async def test_messaging_validation_error_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Unsupported messaging provider"):
        await manager.get_messaging_provider_config(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="telegram",
        )

    with pytest.raises(ValueError, match="Missing chat_id"):
        await manager.get_messaging_chat_policy(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="",
        )

    with pytest.raises(ValueError, match="Invalid messaging direction"):
        await manager.ingest_messaging_message(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            direction="bad-direction",
            event_type="whatsapp.message.inbound",
            body_text="hi",
        )

    with pytest.raises(ValueError, match="Missing event_type"):
        await manager.ingest_messaging_message(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            direction="inbound",
            event_type="",
            body_text="hi",
        )

    manager.get_messaging_chat_policy = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="Invalid message_id"):
        await manager.ingest_messaging_message(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            direction="inbound",
            event_type="whatsapp.message.inbound",
            body_text="hi",
            message_id="not-a-uuid",
        )

    with pytest.raises(ValueError, match="Invalid action_id"):
        await manager.ingest_messaging_message(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            direction="inbound",
            event_type="whatsapp.message.inbound",
            body_text="hi",
            action_id="not-a-uuid",
        )


@pytest.mark.asyncio
async def test_messaging_additional_runtime_and_filter_branches() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="bridge_mode"):
        await manager.put_messaging_provider_config(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            enabled=True,
            bridge_mode="invalid",
            actor=_actor(),
        )

    manager._fetchrow = AsyncMock(side_effect=[None, None])  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="provider config"):
        await manager.put_messaging_provider_config(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            enabled=True,
            actor=_actor(),
        )

    manager._fetchrow = AsyncMock(side_effect=[None, None])  # type: ignore[method-assign]
    manager._fetch = AsyncMock(return_value=[])  # type: ignore[method-assign]
    manager.set_setting = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="chat policy"):
        await manager.put_messaging_chat_policy(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            read_enabled=True,
            send_enabled=True,
            actor=_actor(),
        )

    manager._fetch = AsyncMock(return_value=[{"chat_id": "chat-1"}])  # type: ignore[method-assign]
    manager._settings_cache[
        ("11111111-1111-1111-1111-111111111111", "security", "messaging_allowlisted_chats")
    ] = "chat-1"
    manager.set_setting = AsyncMock(return_value=None)  # type: ignore[method-assign]
    await manager._sync_messaging_allowlist_setting(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        actor=_actor(),
    )
    manager.set_setting.assert_not_awaited()

    manager._fetch = AsyncMock(return_value=[])  # type: ignore[method-assign]
    chats = await manager.list_messaging_chats(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider=None,
        include_inactive=True,
        limit=10,
    )
    assert chats == []

    assert (
        await manager.is_messaging_chat_allowed(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="",
            action="read",
        )
        is False
    )

    with pytest.raises(ValueError, match="Missing chat_id"):
        await manager.ingest_messaging_message(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="",
            direction="inbound",
            event_type="whatsapp.message.inbound",
            body_text="hello",
        )

    manager.get_messaging_chat_policy = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to store tenant messaging message"):
        await manager.ingest_messaging_message(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            direction="inbound",
            event_type="whatsapp.message.inbound",
            body_text="",
            metadata={"source": "bridge"},
        )

    with pytest.raises(ValueError, match="Invalid chat_id"):
        await manager.list_messaging_messages(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="   ",
        )


@pytest.mark.asyncio
async def test_security_event_record_list_and_dashboard_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "event_id": 1,
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "event_type": "trust_policy_denied",
            "severity": "high",
            "action": "messaging.send",
            "source": "skills-admin",
            "payload_json": {"code": "AI_APPROVAL_REQUIRED"},
            "created_at": datetime.now(UTC),
        }
    )
    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [
                {
                    "event_id": 2,
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "event_type": "bridge_signature_invalid",
                    "severity": "high",
                    "action": "messaging.ingest",
                    "source": "bridge",
                    "payload_json": {"error": "Invalid bridge signature"},
                    "created_at": datetime.now(UTC),
                }
            ],
            [{"severity": "high", "count": 3}, {"severity": "medium", "count": 1}],
            [{"event_type": "trust_policy_denied", "count": 2}],
            [
                {
                    "event_id": 3,
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "event_type": "trust_policy_denied",
                    "severity": "high",
                    "action": "automerge.execute",
                    "source": "skills-admin",
                    "payload_json": {"code": "AI_TRUST_POLICY_DENIED"},
                    "created_at": datetime.now(UTC),
                }
            ],
        ]
    )

    created = await manager.record_security_event(
        tenant_id="11111111-1111-1111-1111-111111111111",
        event_type="trust_policy_denied",
        severity="high",
        action="messaging.send",
        source="skills-admin",
        payload={"code": "AI_APPROVAL_REQUIRED"},
    )
    assert created["event_type"] == "trust_policy_denied"

    listed = await manager.list_security_events(
        tenant_id="11111111-1111-1111-1111-111111111111",
        severity="high",
        limit=10,
    )
    assert listed[0]["event_type"] == "bridge_signature_invalid"

    dashboard = await manager.get_security_dashboard(
        tenant_id="11111111-1111-1111-1111-111111111111",
        window_hours=24,
        recent_limit=10,
    )
    assert dashboard["totals"]["events"] == 4
    assert dashboard["totals"]["by_severity"]["high"] == 3
    assert dashboard["top_event_types"][0]["event_type"] == "trust_policy_denied"
    assert len(dashboard["recent_events"]) == 1

    with pytest.raises(ValueError, match="severity must be one of"):
        await manager.list_security_events(
            tenant_id="11111111-1111-1111-1111-111111111111",
            severity="urgent",
        )


@pytest.mark.asyncio
async def test_security_and_messaging_helper_branch_variants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Missing event_type"):
        await manager.record_security_event(
            tenant_id="11111111-1111-1111-1111-111111111111",
            event_type="",
        )

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to record tenant security event"):
        await manager.record_security_event(
            tenant_id="11111111-1111-1111-1111-111111111111",
            event_type="auth.denied",
            severity="critical",
        )

    warnings: list[dict[str, Any]] = []

    def _capture_warning(event: str, **kwargs: Any) -> None:
        warnings.append({"event": event, **kwargs})

    monkeypatch.setattr("zetherion_ai.admin.tenant_admin_manager.log.warning", _capture_warning)
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "event_id": 10,
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "event_type": "auth.denied",
            "severity": "critical",
            "action": "login",
            "source": "gateway",
            "payload_json": {},
            "created_at": datetime.now(UTC),
        }
    )
    created = await manager.record_security_event(
        tenant_id="11111111-1111-1111-1111-111111111111",
        event_type=" AUTH.DENIED ",
        severity="CRITICAL",
        action=" Login ",
        source=" Gateway ",
    )
    assert created["event_type"] == "auth.denied"
    assert warnings and warnings[0]["severity"] == "critical"

    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [
                {
                    "event_id": 1,
                    "event_type": "auth.denied",
                    "severity": "critical",
                    "action": "login",
                }
            ],
            [{"severity": "critical", "count": 2}],
            [{"event_type": "auth.denied", "count": 2}],
            [
                {
                    "event_id": 3,
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "event_type": "auth.denied",
                    "severity": "critical",
                    "action": "login",
                    "source": "gateway",
                    "payload_json": {},
                    "created_at": "2026-03-17T00:00:00+00:00",
                }
            ],
        ]
    )
    listed = await manager.list_security_events(
        tenant_id="11111111-1111-1111-1111-111111111111",
        event_type=" AUTH.DENIED ",
        severity="critical",
        action=" LOGIN ",
        limit=5000,
    )
    assert listed[0]["action"] == "login"

    dashboard = await manager.get_security_dashboard(
        tenant_id="11111111-1111-1111-1111-111111111111",
        window_hours=99999,
        recent_limit=999,
    )
    assert dashboard["window_hours"] == 24 * 30
    assert dashboard["recent_events"][0]["created_at"] == "2026-03-17T00:00:00+00:00"

    assert TenantAdminManager._normalize_worker_messaging_provider(" email ") == "email"
    with pytest.raises(ValueError, match="Unsupported worker messaging provider"):
        TenantAdminManager._normalize_worker_messaging_provider("sms")

    assert TenantAdminManager._coerce_retention_days("not-a-number", default=14) == 14
    assert TenantAdminManager._coerce_retention_days(0, default=14) == 1
    assert TenantAdminManager._coerce_retention_days(9999, default=14) == 365
    assert manager._resolve_messaging_retention_days(
        tenant_id="11111111-1111-1111-1111-111111111111",
        policy_days="45",  # type: ignore[arg-type]
    ) == 45
    manager._settings_cache[
        ("11111111-1111-1111-1111-111111111111", "security", "messaging_retention_days")
    ] = "18"
    assert manager._resolve_messaging_retention_days(
        tenant_id="11111111-1111-1111-1111-111111111111",
    ) == 18

    with pytest.raises(ValueError, match="Missing chat_id"):
        await manager.put_messaging_chat_policy(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="",
            read_enabled=True,
            send_enabled=True,
            actor=_actor(),
        )

    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "message_id": "11111111-1111-1111-1111-111111111111",
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "direction": "outbound",
                "sender_id": "sender-1",
                "sender_name": "Sender",
                "body_enc": "hello",
                "metadata": {},
                "action_id": None,
                "event_type": "whatsapp.message.outbound",
                "observed_at": datetime.now(UTC),
                "expires_at": datetime.now(UTC) + timedelta(days=1),
                "created_at": datetime.now(UTC),
            }
        ]
    )
    exported = await manager.export_messaging_messages(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        sender_id=" sender-1 ",
        direction=" OUTBOUND ",
        include_expired=True,
        limit=99999,
    )
    assert exported[0]["direction"] == "outbound"

    with pytest.raises(ValueError, match="Invalid direction"):
        await manager.export_messaging_messages(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            sender_id="sender-1",
            direction="sideways",
        )


@pytest.mark.asyncio
async def test_security_and_messaging_list_filter_optional_branches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    warnings: list[dict[str, Any]] = []

    def _capture_warning(event: str, **kwargs: Any) -> None:
        warnings.append({"event": event, **kwargs})

    monkeypatch.setattr("zetherion_ai.admin.tenant_admin_manager.log.warning", _capture_warning)
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "event_id": 11,
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "event_type": "auth.allowed",
            "severity": "low",
            "action": "login",
            "source": "gateway",
            "payload_json": {},
            "created_at": datetime.now(UTC),
        }
    )

    created = await manager.record_security_event(
        tenant_id="11111111-1111-1111-1111-111111111111",
        event_type="AUTH.ALLOWED",
        severity="low",
        action="Login",
        source="Gateway",
    )
    assert created["severity"] == "low"
    assert warnings == []

    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [{"item_id": "item-1", "status": "open", "severity": "high"}],
            [{"event_id": 1, "event_type": "auth.allowed", "action": "login"}],
            [],
        ]
    )

    critical = await manager.list_email_critical_items(
        tenant_id="11111111-1111-1111-1111-111111111111",
        status=" OPEN ",
        severity=" HIGH ",
        limit=9999,
    )
    assert critical[0]["status"] == "open"
    critical_args = manager._fetch.await_args_list[0].args
    assert "status = $2" in critical_args[0]
    assert "severity = $3" in critical_args[0]
    assert critical_args[1:] == (
        "11111111-1111-1111-1111-111111111111",
        "open",
        "high",
        500,
    )

    listed = await manager.list_security_events(
        tenant_id="11111111-1111-1111-1111-111111111111",
        action=" LOGIN ",
        limit=0,
    )
    assert listed[0]["action"] == "login"
    security_args = manager._fetch.await_args_list[1].args
    assert "event_type =" not in security_args[0]
    assert "severity =" not in security_args[0]
    assert "action = $2" in security_args[0]
    assert security_args[1:] == ("11111111-1111-1111-1111-111111111111", "login", 1)

    messages = await manager.list_messaging_messages(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider=None,
        chat_id=" chat-1 ",
        direction=" OUTBOUND ",
        include_expired=True,
        limit=0,
    )
    assert messages == []
    message_args = manager._fetch.await_args_list[2].args
    assert "provider =" not in message_args[0]
    assert "chat_id = $2" in message_args[0]
    assert "direction = $3" in message_args[0]
    assert "expires_at > now()" not in message_args[0]
    assert message_args[1:] == (
        "11111111-1111-1111-1111-111111111111",
        "chat-1",
        "outbound",
        1,
    )


@pytest.mark.asyncio
async def test_email_and_messaging_filter_false_paths_and_allowlist_refresh() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [{"item_id": "item-2", "status": "open", "severity": "critical"}],
            [],
            [{"chat_id": "chat-2"}],
        ]
    )
    manager.set_setting = AsyncMock(return_value=None)  # type: ignore[method-assign]

    critical = await manager.list_email_critical_items(
        tenant_id="11111111-1111-1111-1111-111111111111",
        status=" OPEN ",
        limit=0,
    )
    assert critical[0]["item_id"] == "item-2"
    critical_args = manager._fetch.await_args_list[0].args
    assert "status = $2" in critical_args[0]
    assert "severity =" not in critical_args[0]
    assert critical_args[1:] == ("11111111-1111-1111-1111-111111111111", "open", 1)

    messages = await manager.list_messaging_messages(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        direction=None,
        include_expired=False,
        limit=600,
    )
    assert messages == []
    message_args = manager._fetch.await_args_list[1].args
    assert "provider = $2" in message_args[0]
    assert "chat_id =" not in message_args[0]
    assert "direction =" not in message_args[0]
    assert "expires_at > now()" in message_args[0]
    assert message_args[1:] == (
        "11111111-1111-1111-1111-111111111111",
        "whatsapp",
        500,
    )

    manager._settings_cache[
        ("11111111-1111-1111-1111-111111111111", "security", "messaging_allowlisted_chats")
    ] = "   "
    await manager._sync_messaging_allowlist_setting(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        actor=_actor(),
    )
    manager.set_setting.assert_awaited_once()
    assert manager.set_setting.await_args.kwargs["value"] == ["chat-2"]


@pytest.mark.asyncio
async def test_messaging_export_and_delete_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [
                {
                    "message_id": "11111111-1111-1111-1111-111111111111",
                    "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                    "provider": "whatsapp",
                    "chat_id": "chat-1",
                    "direction": "inbound",
                    "sender_id": "sender-1",
                    "sender_name": "Sender",
                    "body_enc": "hello",
                    "metadata": {"source": "bridge"},
                    "action_id": None,
                    "event_type": "whatsapp.message.inbound",
                    "observed_at": datetime.now(UTC),
                    "expires_at": datetime.now(UTC) + timedelta(days=14),
                    "created_at": datetime.now(UTC),
                }
            ],
            [
                {
                    "message_id": "11111111-1111-1111-1111-111111111111",
                    "chat_id": "chat-1",
                    "sender_id": "sender-1",
                }
            ],
        ]
    )

    exported = await manager.export_messaging_messages(
        tenant_id="11111111-1111-1111-1111-111111111111",
        provider="whatsapp",
        chat_id="chat-1",
        limit=50,
    )
    assert exported[0]["body_text"] == "hello"

    deleted = await manager.delete_messaging_messages(
        tenant_id="11111111-1111-1111-1111-111111111111",
        actor=_actor(),
        provider="whatsapp",
        chat_id="chat-1",
        message_ids=["11111111-1111-1111-1111-111111111111"],
        limit=100,
    )
    assert deleted["deleted_count"] == 1
    assert deleted["deleted_message_ids"] == ["11111111-1111-1111-1111-111111111111"]
    manager._write_audit.assert_awaited_once()

    with pytest.raises(ValueError, match="at least one export filter"):
        await manager.export_messaging_messages(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
        )

    with pytest.raises(ValueError, match="At least one delete filter"):
        await manager.delete_messaging_messages(
            tenant_id="11111111-1111-1111-1111-111111111111",
            actor=_actor(),
            provider="whatsapp",
        )

    with pytest.raises(ValueError, match="UUID"):
        await manager.delete_messaging_messages(
            tenant_id="11111111-1111-1111-1111-111111111111",
            actor=_actor(),
            provider="whatsapp",
            chat_id="chat-1",
            message_ids=["not-a-uuid"],
        )


@pytest.mark.asyncio
async def test_delete_messaging_messages_normalizes_optional_filters_and_blank_ids() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._fetch = AsyncMock(return_value=[])  # type: ignore[method-assign]

    cutoff = datetime(2026, 3, 16, 8, 30)
    deleted = await manager.delete_messaging_messages(
        tenant_id="11111111-1111-1111-1111-111111111111",
        actor=_actor(),
        provider="whatsapp",
        chat_id=" chat-1 ",
        sender_id=" sender-1 ",
        before_created_at=cutoff,
        message_ids=[
            "",
            "11111111-1111-1111-1111-111111111111",
            "   ",
        ],
        limit=999999,
    )

    assert deleted["deleted_count"] == 0
    assert deleted["filters"]["chat_id"] == "chat-1"
    assert deleted["filters"]["sender_id"] == "sender-1"
    assert deleted["filters"]["before_created_at"].tzinfo == UTC
    assert deleted["filters"]["message_ids"] == ["11111111-1111-1111-1111-111111111111"]

    fetch_args = manager._fetch.await_args.args
    assert "chat_id = $3" in fetch_args[0]
    assert "sender_id = $4" in fetch_args[0]
    assert "created_at <= $5" in fetch_args[0]
    assert "message_id = ANY($6::uuid[])" in fetch_args[0]
    assert fetch_args[1:] == (
        "11111111-1111-1111-1111-111111111111",
        "whatsapp",
        "chat-1",
        "sender-1",
        deleted["filters"]["before_created_at"],
        ["11111111-1111-1111-1111-111111111111"],
        20000,
    )
    manager._write_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_delete_messaging_messages_skips_optional_chat_and_message_id_filters() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._fetch = AsyncMock(return_value=[])  # type: ignore[method-assign]

    cutoff = datetime(2026, 3, 16, 8, 30)
    deleted = await manager.delete_messaging_messages(
        tenant_id="11111111-1111-1111-1111-111111111111",
        actor=_actor(),
        provider="whatsapp",
        sender_id=" sender-2 ",
        before_created_at=cutoff,
        limit=5,
    )

    assert deleted["deleted_count"] == 0
    fetch_args = manager._fetch.await_args.args
    assert "chat_id =" not in fetch_args[0]
    assert "sender_id = $3" in fetch_args[0]
    assert "created_at <= $4" in fetch_args[0]
    assert "message_id = ANY(" not in fetch_args[0]
    assert fetch_args[1:] == (
        "11111111-1111-1111-1111-111111111111",
        "whatsapp",
        "sender-2",
        cutoff.replace(tzinfo=UTC),
        5,
    )


def test_execution_target_and_worker_result_helper_paths() -> None:
    assert TenantAdminManager._normalize_execution_target(None) == "windows_local"
    assert TenantAdminManager._normalize_execution_target("any_worker") == "any_worker"
    assert TenantAdminManager._normalize_execution_target("worker:node-1") == "worker:node-1"

    with pytest.raises(ValueError, match="missing node id"):
        TenantAdminManager._normalize_execution_target("worker:")
    with pytest.raises(ValueError, match="must match"):
        TenantAdminManager._normalize_execution_target("worker:Node!")
    with pytest.raises(ValueError, match="execution_target must be one of"):
        TenantAdminManager._normalize_execution_target("moon-base")

    assert TenantAdminManager._coerce_execution_runtime_seconds(None) is None
    assert TenantAdminManager._coerce_execution_runtime_seconds("") is None
    assert TenantAdminManager._coerce_execution_runtime_seconds("15") == 30
    assert TenantAdminManager._coerce_execution_runtime_seconds("999999") == 86_400
    with pytest.raises(ValueError, match="max_runtime_seconds must be an integer"):
        TenantAdminManager._coerce_execution_runtime_seconds(object())

    assert TenantAdminManager._execution_target_node_id("worker:node-1") == "node-1"
    assert TenantAdminManager._execution_target_node_id("windows_local") is None

    assert TenantAdminManager._worker_result_outcome("ok") == ("succeeded", True)
    assert TenantAdminManager._worker_result_outcome("cancelled") == ("failed", False)
    assert TenantAdminManager._worker_result_outcome("mystery") == ("failed", False)

    assert (
        TenantAdminManager._worker_result_failure_category(
            output={"message": "Execution timeout waiting on dependency"},
        )
        == "timeout"
    )
    assert (
        TenantAdminManager._worker_result_failure_category(
            error={"detail": "Hit rate limit from provider"},
        )
        == "rate_limit"
    )
    assert (
        TenantAdminManager._worker_result_failure_category(
            output={"failure_category": "dependency unavailable"},
        )
        == "dependency"
    )
    assert (
        TenantAdminManager._worker_result_failure_category(
            error={"code": "job_cancelled_due_to_interrupt"},
        )
        == "interrupted"
    )
    assert (
        TenantAdminManager._worker_result_failure_category(output={"message": "boom"})
        == "transient"
    )


@pytest.mark.asyncio
async def test_queue_messaging_send_missing_chat_and_insert_failure_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager.is_messaging_chat_allowed = AsyncMock(return_value=True)  # type: ignore[method-assign]
    manager.ingest_messaging_message = AsyncMock()  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Missing chat_id"):
        await manager.queue_messaging_send(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="",
            body_text="hello",
            actor=_actor(),
        )

    with pytest.raises(RuntimeError, match="Failed to queue messaging send action"):
        await manager.queue_messaging_send(
            tenant_id="11111111-1111-1111-1111-111111111111",
            provider="whatsapp",
            chat_id="chat-1",
            body_text="hello",
            actor=_actor(),
        )
    manager.ingest_messaging_message.assert_not_awaited()


@pytest.mark.asyncio
async def test_create_execution_plan_schedules_continuation_and_audit() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]
    manager.schedule_execution_continuation = AsyncMock(return_value="queue-1")  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    plan_id = "11111111-1111-1111-1111-111111111111"
    step_1 = "22222222-2222-2222-2222-222222222222"
    step_2 = "33333333-3333-3333-3333-333333333333"
    conn.fetchrow.side_effect = [
        {
            "plan_id": plan_id,
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "title": "Night Build",
            "goal": "Ship overnight",
            "execution_mode": "test",
            "status": "queued",
            "current_step_index": 0,
            "total_steps": 2,
            "max_step_attempts": 3,
            "continuation_interval_seconds": 60,
            "next_run_at": datetime.now(UTC),
            "lease_owner": None,
            "lease_expires_at": None,
            "metadata": {},
            "last_error_category": None,
            "last_error_detail": None,
            "created_by": "operator-1",
            "updated_by": "operator-1",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
        {
            "step_id": step_1,
            "plan_id": plan_id,
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "step_index": 0,
            "title": "Step 1",
            "prompt_text": "Design schema",
            "idempotency_key": "step-1",
            "status": "pending",
            "attempt_count": 0,
            "max_attempts": 3,
            "next_retry_at": None,
            "last_error_category": None,
            "last_error_detail": None,
            "output_json": {},
            "metadata": {},
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
        {
            "step_id": step_2,
            "plan_id": plan_id,
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "step_index": 1,
            "title": "Step 2",
            "prompt_text": "Implement routes",
            "idempotency_key": "step-2",
            "status": "pending",
            "attempt_count": 0,
            "max_attempts": 3,
            "next_retry_at": None,
            "last_error_category": None,
            "last_error_detail": None,
            "output_json": {},
            "metadata": {},
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
    ]

    created = await manager.create_execution_plan(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="Night Build",
        goal="Ship overnight",
        steps=["Design schema", {"title": "Step 2", "prompt": "Implement routes"}],
        actor=_actor(),
    )

    assert created["plan"]["status"] == "queued"
    assert len(created["steps"]) == 2
    manager.schedule_execution_continuation.assert_awaited_once()
    manager._write_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_create_execution_plan_persists_step_metadata() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager.schedule_execution_continuation = AsyncMock(return_value="queue-1")  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    plan_id = "11111111-1111-1111-1111-111111111111"
    step_id = "22222222-2222-2222-2222-222222222222"
    conn.fetchrow.side_effect = [
        {
            "plan_id": plan_id,
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "title": "Night Build",
            "goal": "Ship overnight",
            "execution_mode": "test",
            "status": "queued",
            "current_step_index": 0,
            "total_steps": 1,
            "max_step_attempts": 3,
            "continuation_interval_seconds": 60,
            "next_run_at": datetime.now(UTC),
            "lease_owner": None,
            "lease_expires_at": None,
            "metadata": {},
            "last_error_category": None,
            "last_error_detail": None,
            "created_by": "operator-1",
            "updated_by": "operator-1",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
        {
            "step_id": step_id,
            "plan_id": plan_id,
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "execution_mode": "test",
            "step_index": 0,
            "title": "Step 1",
            "prompt_text": "Execute automerge",
            "idempotency_key": "step-1",
            "status": "pending",
            "attempt_count": 0,
            "max_attempts": 3,
            "next_retry_at": None,
            "last_error_category": None,
            "last_error_detail": None,
            "output_json": {},
            "metadata": {"executor": "automerge"},
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
    ]

    created = await manager.create_execution_plan(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="Night Build",
        goal="Ship overnight",
        steps=[{"prompt": "Execute automerge", "metadata": {"executor": "automerge"}}],
        actor=_actor(),
        execution_mode="test",
    )

    assert created["steps"][0]["metadata"] == {"executor": "automerge"}
    assert created["plan"]["execution_mode"] == "test"
    assert created["steps"][0]["execution_mode"] == "test"


@pytest.mark.asyncio
async def test_create_and_list_execution_plan_optional_branches() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager.schedule_execution_continuation = AsyncMock(return_value="queue-1")  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Missing non-empty title"):
        await manager.create_execution_plan(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            title="   ",
            goal="Ship overnight",
            steps=["Design schema"],
            actor=_actor(),
        )
    with pytest.raises(ValueError, match="Missing non-empty goal"):
        await manager.create_execution_plan(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            title="Night Build",
            goal="   ",
            steps=["Design schema"],
            actor=_actor(),
        )

    plan_id = "11111111-1111-1111-1111-111111111111"
    step_id = "22222222-2222-2222-2222-222222222222"
    conn.fetchrow.side_effect = [
        {
            "plan_id": plan_id,
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "title": "Night Build",
            "goal": "Ship overnight",
            "execution_mode": "live",
            "status": "queued",
            "current_step_index": 0,
            "total_steps": 1,
            "max_step_attempts": 3,
            "continuation_interval_seconds": 60,
            "next_run_at": datetime.now(UTC),
            "lease_owner": None,
            "lease_expires_at": None,
            "metadata": {},
            "last_error_category": None,
            "last_error_detail": None,
            "created_by": "operator-1",
            "updated_by": "operator-1",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
        {
            "step_id": step_id,
            "plan_id": plan_id,
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "execution_mode": "live",
            "step_index": 0,
            "title": "Step 1",
            "prompt_text": "Design schema",
            "idempotency_key": "step-1",
            "status": "pending",
            "attempt_count": 0,
            "max_attempts": 3,
            "next_retry_at": None,
            "last_error_category": None,
            "last_error_detail": None,
            "output_json": {},
            "metadata": {},
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
    ]

    created = await manager.create_execution_plan(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        title="Night Build",
        goal="Ship overnight",
        steps=["Design schema"],
        actor=_actor(),
        start_at=datetime(2026, 3, 17, 8, 30),
    )
    assert created["plan"]["plan_id"] == plan_id
    inserted_plan_args = conn.fetchrow.await_args_list[0].args
    assert inserted_plan_args[9].tzinfo == UTC

    manager._fetch = AsyncMock(return_value=[])  # type: ignore[method-assign]
    plans = await manager.list_execution_plans(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        status="   ",
        limit=0,
    )
    assert plans == []
    list_args = manager._fetch.await_args.args
    assert "status =" not in list_args[0]
    assert list_args[1:] == ("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", 1)


@pytest.mark.asyncio
async def test_create_execution_plan_insert_failure_paths() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager.schedule_execution_continuation = AsyncMock(return_value="queue-1")  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    conn.fetchrow.side_effect = [None]
    with pytest.raises(RuntimeError, match="Failed to create execution plan"):
        await manager.create_execution_plan(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            title="Night Build",
            goal="Ship overnight",
            steps=["Design schema"],
            actor=_actor(),
        )

    conn.fetchrow.side_effect = [_execution_plan_row(), None]
    with pytest.raises(RuntimeError, match="Failed to create execution step"):
        await manager.create_execution_plan(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            title="Night Build",
            goal="Ship overnight",
            steps=["Design schema"],
            actor=_actor(),
        )


@pytest.mark.asyncio
async def test_claim_next_execution_step_returns_retry_context() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    conn.fetchrow.side_effect = [
        {
            "step_id": "22222222-2222-2222-2222-222222222222",
            "step_index": 0,
            "status": "running",
        },
        {
            "step_id": "22222222-2222-2222-2222-222222222222",
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "step_index": 0,
            "title": "Step 1",
            "prompt_text": "Design schema",
            "idempotency_key": "step-1",
            "status": "running",
            "attempt_count": 2,
            "max_attempts": 3,
            "next_retry_at": None,
            "last_error_category": None,
            "last_error_detail": None,
            "output_json": {},
            "metadata": {},
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
    ]

    claimed = await manager.claim_next_execution_step(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        worker_id="worker-1",
        lease_token="lease-1",
    )

    assert claimed is not None
    assert claimed["step"]["status"] == "running"
    assert claimed["retry"]["attempt_number"] == 2
    assert conn.execute.await_count >= 3


@pytest.mark.asyncio
async def test_complete_execution_step_marks_plan_complete_on_last_step() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    conn.fetchrow.side_effect = [
        {"status": "running", "continuation_interval_seconds": 60},
        {"step_index": 1, "status": "running"},
        {
            "step_id": "22222222-2222-2222-2222-222222222222",
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "step_index": 1,
            "title": "Step 2",
            "prompt_text": "Implement routes",
            "idempotency_key": "step-2",
            "status": "completed",
            "attempt_count": 1,
            "max_attempts": 3,
            "next_retry_at": None,
            "last_error_category": None,
            "last_error_detail": None,
            "output_json": {"response_text": "done"},
            "metadata": {},
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
        None,
        {
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "title": "Night Build",
            "goal": "Ship overnight",
            "status": "completed",
            "current_step_index": 2,
            "total_steps": 2,
            "max_step_attempts": 3,
            "continuation_interval_seconds": 60,
            "next_run_at": None,
            "lease_owner": None,
            "lease_expires_at": None,
            "metadata": {},
            "last_error_category": None,
            "last_error_detail": None,
            "created_by": "operator-1",
            "updated_by": "operator-1",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
    ]

    completed = await manager.complete_execution_step(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        step_id="22222222-2222-2222-2222-222222222222",
        retry_id="33333333-3333-3333-3333-333333333333",
        worker_id="worker-1",
        output_json={"response_text": "done"},
    )

    assert completed["has_more"] is False
    assert completed["plan"]["status"] == "completed"


@pytest.mark.asyncio
async def test_fail_execution_step_retry_and_terminal_paths() -> None:
    conn = _FakeConn()
    pool = _FakePool(conn)
    manager = TenantAdminManager(pool=pool)  # type: ignore[arg-type]

    conn.fetchrow.side_effect = [
        {"status": "running"},
        {"status": "running", "attempt_count": 1, "max_attempts": 3},
        {
            "step_id": "22222222-2222-2222-2222-222222222222",
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "step_index": 0,
            "title": "Step 1",
            "prompt_text": "Design schema",
            "idempotency_key": "step-1",
            "status": "failed",
            "attempt_count": 1,
            "max_attempts": 3,
            "next_retry_at": datetime.now(UTC),
            "last_error_category": "timeout",
            "last_error_detail": "timeout",
            "output_json": {},
            "metadata": {},
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
        {
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "title": "Night Build",
            "goal": "Ship overnight",
            "status": "running",
            "current_step_index": 0,
            "total_steps": 2,
            "max_step_attempts": 3,
            "continuation_interval_seconds": 60,
            "next_run_at": datetime.now(UTC),
            "lease_owner": None,
            "lease_expires_at": None,
            "metadata": {},
            "last_error_category": "timeout",
            "last_error_detail": "timeout",
            "created_by": "operator-1",
            "updated_by": "operator-1",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
    ]

    failed_retryable = await manager.fail_execution_step(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        step_id="22222222-2222-2222-2222-222222222222",
        retry_id="33333333-3333-3333-3333-333333333333",
        worker_id="worker-1",
        failure_category="timeout",
        failure_detail="timeout",
    )

    assert failed_retryable["retry_scheduled"] is True
    assert failed_retryable["backoff_seconds"] == 60
    assert failed_retryable["plan"]["status"] == "running"


def _execution_plan_row(
    *,
    status: str = "queued",
    next_run_at: datetime | None = None,
    current_step_index: int = 0,
    execution_mode: str = "live",
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "plan_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "title": "Night Build",
        "goal": "Ship overnight",
        "execution_mode": execution_mode,
        "status": status,
        "current_step_index": current_step_index,
        "total_steps": 2,
        "max_step_attempts": 3,
        "continuation_interval_seconds": 60,
        "next_run_at": next_run_at,
        "lease_owner": None,
        "lease_expires_at": None,
        "metadata": {},
        "last_error_category": None,
        "last_error_detail": None,
        "created_by": "operator-1",
        "updated_by": "operator-1",
        "created_at": now,
        "updated_at": now,
    }


def _execution_step_row(
    *,
    status: str = "pending",
    attempt_count: int = 0,
    max_attempts: int = 3,
    step_index: int = 0,
    prompt_text: str = "Do the next task",
    execution_target: str = "windows_local",
    required_capabilities: list[str] | None = None,
    execution_mode: str = "live",
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "step_id": "22222222-2222-2222-2222-222222222222",
        "plan_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "execution_mode": execution_mode,
        "step_index": step_index,
        "title": f"Step {step_index + 1}",
        "prompt_text": prompt_text,
        "idempotency_key": f"step-{step_index + 1}",
        "status": status,
        "attempt_count": attempt_count,
        "max_attempts": max_attempts,
        "next_retry_at": None,
        "last_error_category": None,
        "last_error_detail": None,
        "execution_target": execution_target,
        "required_capabilities": required_capabilities or [],
        "max_runtime_seconds": 600,
        "artifact_contract": {},
        "output_json": {},
        "metadata": {},
        "created_at": now,
        "updated_at": now,
    }


def _worker_job_row(
    *,
    status: str = "queued",
    execution_target: str = "any_worker",
    required_capabilities: list[str] | None = None,
    claimed_by_node_id: str | None = None,
    action: str = "worker.noop",
    payload_json: dict[str, Any] | None = None,
    execution_mode: str = "live",
) -> dict[str, Any]:
    now = datetime.now(UTC)
    return {
        "job_id": "44444444-4444-4444-4444-444444444444",
        "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "plan_id": "11111111-1111-1111-1111-111111111111",
        "step_id": "22222222-2222-2222-2222-222222222222",
        "retry_id": "33333333-3333-3333-3333-333333333333",
        "execution_mode": execution_mode,
        "execution_target": execution_target,
        "target_node_id": None,
        "required_capabilities": required_capabilities or ["repo.patch"],
        "max_runtime_seconds": 600,
        "artifact_contract": {},
        "action": action,
        "payload_json": payload_json or {"runner": "noop", "worker_artifacts": []},
        "status": status,
        "claimed_by_node_id": claimed_by_node_id,
        "lease_token": None,
        "lease_expires_at": None,
        "started_at": now if status in {"running", "succeeded", "failed"} else None,
        "finished_at": now if status in {"succeeded", "failed"} else None,
        "result_json": {},
        "error_json": {},
        "created_at": now,
        "updated_at": now,
    }


def test_execution_step_coercion_and_actor_id_helpers() -> None:
    normalized = TenantAdminManager._coerce_execution_steps(
        [
            "Draft plan",
            {
                "title": "Build",
                "prompt": "Implement",
                "idempotency_key": "A B C",
                "execution_target": "worker:node-1",
                "required_capabilities": ["repo.patch", "repo.pr.open"],
                "max_runtime_seconds": 1200,
                "artifact_contract": {"expect": "diff-summary"},
                "metadata": {"executor": "automerge"},
            },
        ]
    )
    assert len(normalized) == 2
    assert normalized[1]["idempotency_key"] == "a-b-c"
    assert normalized[0]["metadata"] == {}
    assert normalized[1]["metadata"] == {"executor": "automerge"}
    assert normalized[0]["execution_target"] == "windows_local"
    assert normalized[1]["execution_target"] == "worker:node-1"
    assert normalized[1]["required_capabilities"] == ["repo.patch", "repo.pr.open"]
    assert normalized[1]["max_runtime_seconds"] == 1200
    assert normalized[1]["artifact_contract"] == {"expect": "diff-summary"}
    assert TenantAdminManager._coerce_execution_actor_user_id("42") == 42
    assert TenantAdminManager._coerce_execution_actor_user_id("not-a-number") == 0
    assert TenantAdminManager._execution_retry_backoff_seconds(999) > 0

    with pytest.raises(ValueError, match="steps must be a non-empty array"):
        TenantAdminManager._coerce_execution_steps([])
    with pytest.raises(ValueError, match=r"steps\[0\] must be a string or object"):
        TenantAdminManager._coerce_execution_steps([123])  # type: ignore[list-item]
    with pytest.raises(ValueError, match=r"steps\[0\] is missing non-empty prompt text"):
        TenantAdminManager._coerce_execution_steps(["   "])
    with pytest.raises(ValueError, match=r"steps\[0\]\.metadata must be an object"):
        TenantAdminManager._coerce_execution_steps(
            [{"prompt": "ship", "metadata": "bad"}]  # type: ignore[list-item]
        )
    with pytest.raises(ValueError, match="execution_target"):
        TenantAdminManager._coerce_execution_steps(
            [{"prompt": "ship", "execution_target": "bad-target"}]
        )
    with pytest.raises(ValueError, match=r"steps\[0\]\.artifact_contract must be an object"):
        TenantAdminManager._coerce_execution_steps(
            [{"prompt": "ship", "artifact_contract": "bad"}]  # type: ignore[list-item]
        )
    with pytest.raises(ValueError, match="status must be one of"):
        TenantAdminManager._normalize_worker_node_status("bad", default="active")
    with pytest.raises(ValueError, match="health_status must be one of"):
        TenantAdminManager._normalize_worker_health_status("bad", default="healthy")
    with pytest.raises(ValueError, match="capabilities must be an array of strings"):
        TenantAdminManager._normalize_worker_capabilities("bad")  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="capabilities entries must match"):
        TenantAdminManager._normalize_worker_capabilities(["bad capability"])
    assert TenantAdminManager._normalize_worker_capabilities(
        ["repo.patch", "repo.patch", " ", "repo.pr.open"]
    ) == ["repo.patch", "repo.pr.open"]


def test_worker_rollout_and_coercion_helper_paths() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    tenant_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    assert TenantAdminManager._coerce_worker_health_score("bad", default=88) == 88
    assert TenantAdminManager._coerce_worker_health_score(999) == 100
    assert TenantAdminManager._coerce_worker_health_score(-10) == 0

    assert TenantAdminManager._coerce_setting_bool("true") is True
    assert TenantAdminManager._coerce_setting_bool(1) is True
    assert TenantAdminManager._coerce_setting_bool("0") is False
    assert TenantAdminManager._coerce_setting_bool(0.0, default=True) is False
    assert TenantAdminManager._coerce_setting_bool(None, default=True) is True

    assert TenantAdminManager._coerce_setting_int("bad", default=7, minimum=1, maximum=10) == 7
    assert TenantAdminManager._coerce_setting_int(99, default=7, minimum=1, maximum=10) == 10
    assert TenantAdminManager._coerce_setting_int(-5, default=7, minimum=1, maximum=10) == 1

    assert TenantAdminManager._coerce_setting_string_set([" node-1 ", "", "node-2"]) == {
        "node-1",
        "node-2",
    }
    assert TenantAdminManager._coerce_setting_string_set("") == set()
    assert TenantAdminManager._coerce_setting_string_set("node-1,node-2") == {"node-1", "node-2"}
    assert TenantAdminManager._coerce_setting_string_set('["g-1","g-2"]') == {"g-1", "g-2"}
    assert TenantAdminManager._coerce_setting_string_set("[not-json]") == {"[not-json]"}

    manager._settings_cache[(tenant_id, "security", "worker_rollout_stage")] = "invalid"
    assert manager._worker_rollout_stage(tenant_id) == "general"

    manager._settings_cache[(tenant_id, "security", "worker_canary_enabled")] = "true"
    assert manager._worker_canary_global_enabled(tenant_id) is True

    assert (
        manager._worker_node_canary_enabled(
            tenant_id=tenant_id,
            node_id="node-1",
            metadata={"canary_enabled": True},
        )
        is True
    )
    manager._settings_cache[(tenant_id, "security", "worker_canary_node_ids")] = "node-2,node-3"
    assert (
        manager._worker_node_canary_enabled(
            tenant_id=tenant_id,
            node_id="node-2",
            metadata={},
        )
        is True
    )
    manager._settings_cache[(tenant_id, "security", "worker_canary_node_groups")] = '["blue"]'
    assert (
        manager.is_worker_node_canary_enabled(
            tenant_id=tenant_id,
            node_id="node-9",
            metadata={"node_group": "blue"},
        )
        is True
    )
    assert (
        manager.is_worker_node_canary_enabled(
            tenant_id=tenant_id,
            node_id="",
            metadata={"node_group": "blue"},
        )
        is False
    )

    manager._settings_cache[(tenant_id, "security", "worker_claim_min_health_score")] = 200
    manager._settings_cache[(tenant_id, "security", "worker_heartbeat_stale_seconds")] = 10
    assert manager._worker_claim_min_health_score(tenant_id) == 100
    assert manager._worker_heartbeat_stale_seconds(tenant_id) == 30

    manager._settings_cache[(tenant_id, "security", "worker_auto_quarantine_enabled")] = "true"
    manager._settings_cache[(tenant_id, "security", "worker_auto_quarantine_score_threshold")] = 45
    manager._settings_cache[
        (tenant_id, "security", "worker_auto_quarantine_consecutive_failures")
    ] = 4
    policy = manager._worker_auto_quarantine_policy(tenant_id)
    assert policy["enabled"] is True
    assert policy["score_threshold"] == 45
    assert policy["failure_threshold"] == 4
    assert (
        manager._worker_quarantine_reason(
            tenant_id=tenant_id,
            health_score=40,
            consecutive_failures=1,
        )
        == "health_score_threshold"
    )
    assert (
        manager._worker_quarantine_reason(
            tenant_id=tenant_id,
            health_score=60,
            consecutive_failures=4,
        )
        == "consecutive_failures_threshold"
    )
    manager._settings_cache[(tenant_id, "security", "worker_auto_quarantine_enabled")] = False
    assert (
        manager._worker_quarantine_reason(
            tenant_id=tenant_id,
            health_score=10,
            consecutive_failures=10,
        )
        is None
    )

    assert TenantAdminManager._normalize_worker_messaging_ttl_seconds("bad") == 3600
    assert TenantAdminManager._normalize_worker_messaging_ttl_seconds(99999999) == 60 * 60 * 24 * 14
    assert TenantAdminManager._normalize_worker_messaging_permission(" READ ") == "read"
    with pytest.raises(ValueError, match="permission must be one of"):
        TenantAdminManager._normalize_worker_messaging_permission("bogus")

    assert TenantAdminManager._normalize_worker_messaging_scope(
        provider="whatsapp",
        allow_draft=True,
    ) == (False, True, False)
    with pytest.raises(ValueError, match="At least one of allow_read"):
        TenantAdminManager._normalize_worker_messaging_scope(provider="whatsapp")
    with pytest.raises(ValueError, match="does not support worker messaging permissions"):
        TenantAdminManager._normalize_worker_messaging_scope(
            provider="email",
            allow_read=True,
        )


@pytest.mark.asyncio
async def test_worker_dispatch_helper_eligibility_and_selection_paths() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    tenant_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    now = datetime.now(UTC)

    assert (
        await manager._worker_node_has_required_capabilities(
            conn=conn,
            tenant_id=tenant_id,
            node_id="node-1",
            required_capabilities=[],
        )
        is True
    )
    conn.fetchval.return_value = 1
    assert (
        await manager._worker_node_has_required_capabilities(
            conn=conn,
            tenant_id=tenant_id,
            node_id="node-1",
            required_capabilities=["repo.patch", "repo.pr.open"],
        )
        is False
    )
    conn.fetchval.return_value = 2
    assert (
        await manager._worker_node_has_required_capabilities(
            conn=conn,
            tenant_id=tenant_id,
            node_id="node-1",
            required_capabilities=["repo.patch", "repo.pr.open"],
        )
        is True
    )

    eligible_row = {
        "node_id": "node-1",
        "status": "active",
        "health_status": "healthy",
        "health_score": 80,
        "metadata": {"canary_enabled": True},
        "last_heartbeat_at": now,
    }
    conn.fetchval.return_value = 1
    assert (
        await manager._worker_node_meets_dispatch_requirements(
            conn=conn,
            tenant_id=tenant_id,
            node_row=eligible_row,
            required_capabilities=["repo.patch"],
            require_canary=True,
            min_health_score=70,
            stale_seconds=120,
            now=now,
        )
        is True
    )
    assert (
        await manager._worker_node_meets_dispatch_requirements(
            conn=conn,
            tenant_id=tenant_id,
            node_row={**eligible_row, "status": "disabled"},
            required_capabilities=[],
            require_canary=False,
            min_health_score=70,
            stale_seconds=120,
            now=now,
        )
        is False
    )
    assert (
        await manager._worker_node_meets_dispatch_requirements(
            conn=conn,
            tenant_id=tenant_id,
            node_row={**eligible_row, "health_status": "unknown"},
            required_capabilities=[],
            require_canary=False,
            min_health_score=70,
            stale_seconds=120,
            now=now,
        )
        is False
    )
    assert (
        await manager._worker_node_meets_dispatch_requirements(
            conn=conn,
            tenant_id=tenant_id,
            node_row={**eligible_row, "health_score": 10},
            required_capabilities=[],
            require_canary=False,
            min_health_score=70,
            stale_seconds=120,
            now=now,
        )
        is False
    )
    assert (
        await manager._worker_node_meets_dispatch_requirements(
            conn=conn,
            tenant_id=tenant_id,
            node_row={**eligible_row, "last_heartbeat_at": now - timedelta(minutes=10)},
            required_capabilities=[],
            require_canary=False,
            min_health_score=70,
            stale_seconds=120,
            now=now,
        )
        is False
    )
    assert (
        await manager._worker_node_meets_dispatch_requirements(
            conn=conn,
            tenant_id=tenant_id,
            node_row={**eligible_row, "metadata": {}},
            required_capabilities=[],
            require_canary=True,
            min_health_score=70,
            stale_seconds=120,
            now=now,
        )
        is False
    )
    assert (
        await manager._worker_node_meets_dispatch_requirements(
            conn=conn,
            tenant_id=tenant_id,
            node_row={**eligible_row, "last_heartbeat_at": "not-a-datetime"},
            required_capabilities=[],
            require_canary=False,
            min_health_score=70,
            stale_seconds=120,
            now=now,
        )
        is False
    )
    assert (
        await manager._worker_node_meets_dispatch_requirements(
            conn=conn,
            tenant_id=tenant_id,
            node_row={
                **eligible_row,
                "last_heartbeat_at": datetime.now().replace(microsecond=0),
            },
            required_capabilities=[],
            require_canary=False,
            min_health_score=70,
            stale_seconds=120,
            now=now,
        )
        is True
    )

    manager._settings_cache[(tenant_id, "security", "worker_rollout_stage")] = "disabled"
    assert (
        await manager._is_worker_dispatch_target_eligible(
            conn=conn,
            tenant_id=tenant_id,
            node_id="node-1",
            required_capabilities=["repo.patch"],
        )
        is False
    )
    manager._settings_cache[(tenant_id, "security", "worker_rollout_stage")] = "canary"
    manager._settings_cache[(tenant_id, "security", "worker_canary_enabled")] = False
    assert (
        await manager._is_worker_dispatch_target_eligible(
            conn=conn,
            tenant_id=tenant_id,
            node_id="node-1",
            required_capabilities=[],
        )
        is False
    )
    manager._settings_cache[(tenant_id, "security", "worker_rollout_stage")] = "general"
    manager._worker_node_meets_dispatch_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    conn.fetchrow.return_value = dict(eligible_row)
    assert (
        await manager._is_worker_dispatch_target_eligible(
            conn=conn,
            tenant_id=tenant_id,
            node_id="node-1",
            required_capabilities=[],
        )
        is True
    )
    conn.fetchrow.return_value = None
    assert (
        await manager._is_worker_dispatch_target_eligible(
            conn=conn,
            tenant_id=tenant_id,
            node_id="node-1",
            required_capabilities=[],
        )
        is False
    )

    manager._settings_cache[(tenant_id, "security", "worker_rollout_stage")] = "disabled"
    assert (
        await manager._select_worker_dispatch_target_node(
            conn=conn,
            tenant_id=tenant_id,
            required_capabilities=["repo.patch"],
        )
        is None
    )
    manager._settings_cache[(tenant_id, "security", "worker_rollout_stage")] = "canary"
    manager._settings_cache[(tenant_id, "security", "worker_canary_enabled")] = False
    assert (
        await manager._select_worker_dispatch_target_node(
            conn=conn,
            tenant_id=tenant_id,
            required_capabilities=[],
        )
        is None
    )
    manager._settings_cache[(tenant_id, "security", "worker_rollout_stage")] = "general"
    manager._worker_node_meets_dispatch_requirements = AsyncMock(  # type: ignore[method-assign]
        side_effect=[False, True]
    )
    conn.fetch.return_value = [
        {
            "node_id": "node-0",
            "status": "active",
            "health_status": "healthy",
            "health_score": 90,
            "metadata": {},
            "last_heartbeat_at": now,
        },
        {
            "node_id": "node-1",
            "status": "active",
            "health_status": "healthy",
            "health_score": 80,
            "metadata": {},
            "last_heartbeat_at": now,
        },
    ]
    selected = await manager._select_worker_dispatch_target_node(
        conn=conn,
        tenant_id=tenant_id,
        required_capabilities=["repo.patch"],
    )
    assert selected == "node-1"

    manager._worker_node_meets_dispatch_requirements = AsyncMock(  # type: ignore[method-assign]
        side_effect=[True, True]
    )
    conn.fetch.return_value = [
        {
            "node_id": "",
            "status": "active",
            "health_status": "healthy",
            "health_score": 90,
            "metadata": {},
            "last_heartbeat_at": now,
        },
        {
            "node_id": "node-2",
            "status": "active",
            "health_status": "healthy",
            "health_score": 85,
            "metadata": {},
            "last_heartbeat_at": now,
        },
    ]
    selected = await manager._select_worker_dispatch_target_node(
        conn=conn,
        tenant_id=tenant_id,
        required_capabilities=[],
    )
    assert selected == "node-2"


@pytest.mark.asyncio
async def test_schedule_execution_continuation_success_and_failure_paths() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._execute = AsyncMock(return_value="INSERT 1")  # type: ignore[method-assign]
    conn.fetchval = AsyncMock(return_value="test")

    queued = await manager.schedule_execution_continuation(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        requested_by="42",
        scheduled_for=datetime(2026, 1, 1, 12, 0, 0),
        priority=9,
    )
    assert queued is not None
    manager._execute.assert_awaited_once()
    execute_args = manager._execute.await_args.args
    assert execute_args[3] == 42
    assert json.loads(execute_args[4])["execution_mode"] == "test"

    manager._execute.reset_mock()
    queued_live = await manager.schedule_execution_continuation(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        execution_mode="live",
    )
    assert queued_live is not None
    live_args = manager._execute.await_args.args
    assert json.loads(live_args[4])["execution_mode"] == "live"

    manager._execute = AsyncMock(side_effect=RuntimeError("db down"))  # type: ignore[method-assign]
    failed = await manager.schedule_execution_continuation(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
    )
    assert failed is None


@pytest.mark.asyncio
async def test_execution_plan_listing_get_and_steps_paths() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager._fetch = AsyncMock(return_value=[_execution_plan_row(status="running")])  # type: ignore[method-assign]

    plans = await manager.list_execution_plans(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        status="running",
        limit=9999,
    )
    assert len(plans) == 1
    with pytest.raises(ValueError, match="Invalid execution plan status"):
        await manager.list_execution_plans(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            status="bogus",
        )

    manager._fetchrow = AsyncMock(side_effect=[None, _execution_plan_row(status="queued")])  # type: ignore[method-assign]
    missing = await manager.get_execution_plan(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
    )
    assert missing is None
    found = await manager.get_execution_plan(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
    )
    assert found is not None
    assert found["status"] == "queued"

    manager._fetch = AsyncMock(return_value=[_execution_step_row(prompt_text="secret prompt")])  # type: ignore[method-assign]
    steps = await manager.list_execution_plan_steps(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        include_prompt=False,
    )
    assert "prompt_text" not in steps[0]

    manager._fetch = AsyncMock(return_value=[_execution_step_row(prompt_text="visible prompt")])  # type: ignore[method-assign]
    steps_with_prompt = await manager.list_execution_plan_steps(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        include_prompt=True,
    )
    assert steps_with_prompt[0]["prompt_text"] == "visible prompt"


@pytest.mark.asyncio
async def test_pause_resume_cancel_execution_plan_happy_paths() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager.get_execution_plan = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _execution_plan_row(status="running"),
            _execution_plan_row(
                status="paused", next_run_at=datetime.now(UTC) + timedelta(minutes=10)
            ),
            _execution_plan_row(status="queued"),
        ]
    )
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _execution_plan_row(status="paused"),
            _execution_plan_row(status="queued"),
            _execution_plan_row(status="cancelled"),
        ]
    )
    manager._execute = AsyncMock(return_value="UPDATE 1")  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager.schedule_execution_continuation = AsyncMock(return_value="queue-1")  # type: ignore[method-assign]

    paused = await manager.pause_execution_plan(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        actor=_actor(),
    )
    resumed = await manager.resume_execution_plan(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        actor=_actor(),
        immediately=False,
    )
    cancelled = await manager.cancel_execution_plan(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        actor=_actor(),
    )

    assert paused["status"] == "paused"
    assert resumed["status"] == "queued"
    assert cancelled["status"] == "cancelled"
    assert manager._execute.await_count == 6
    manager.schedule_execution_continuation.assert_awaited_once()
    assert manager._write_audit.await_count == 3


@pytest.mark.asyncio
async def test_pause_resume_cancel_execution_plan_error_paths() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]

    manager.get_execution_plan = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="Execution plan not found"):
        await manager.pause_execution_plan(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            actor=_actor(),
        )
    with pytest.raises(ValueError, match="Execution plan not found"):
        await manager.resume_execution_plan(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            actor=_actor(),
        )
    with pytest.raises(ValueError, match="Execution plan not found"):
        await manager.cancel_execution_plan(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            actor=_actor(),
        )

    manager.get_execution_plan = AsyncMock(return_value=_execution_plan_row(status="running"))  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="Execution plan could not be paused"):
        await manager.pause_execution_plan(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            actor=_actor(),
        )
    with pytest.raises(ValueError, match="Execution plan could not be resumed"):
        await manager.resume_execution_plan(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            actor=_actor(),
        )
    with pytest.raises(ValueError, match="Execution plan could not be cancelled"):
        await manager.cancel_execution_plan(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            actor=_actor(),
        )


@pytest.mark.asyncio
async def test_claim_and_release_execution_plan_lease_paths() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[None, _execution_plan_row(status="running")]
    )
    manager._execute = AsyncMock(return_value="UPDATE 1")  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Missing worker_id"):
        await manager.claim_execution_plan_lease(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            worker_id="",
        )

    miss = await manager.claim_execution_plan_lease(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        worker_id="worker-1",
    )
    assert miss is None

    claimed = await manager.claim_execution_plan_lease(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        worker_id="worker-1",
    )
    assert claimed is not None
    assert claimed["status"] == "running"

    await manager.release_execution_plan_lease(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        worker_id="worker-1",
    )
    await manager.release_execution_plan_lease(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
    )
    assert manager._execute.await_count == 2


@pytest.mark.asyncio
async def test_claim_next_execution_step_empty_and_partial_paths() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Missing worker_id"):
        await manager.claim_next_execution_step(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            worker_id="",
            lease_token="",
        )

    conn.fetchrow.side_effect = [None]
    assert (
        await manager.claim_next_execution_step(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            worker_id="worker-1",
            lease_token="token-1",
        )
        is None
    )

    conn.fetchrow.side_effect = [
        {"step_id": "22222222-2222-2222-2222-222222222222", "step_index": 0, "status": "pending"},
        None,
    ]
    assert (
        await manager.claim_next_execution_step(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            worker_id="worker-1",
            lease_token="token-1",
        )
        is None
    )


@pytest.mark.asyncio
async def test_reconcile_execution_plan_status_paths() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]

    conn.fetchrow.side_effect = [None]
    assert (
        await manager.reconcile_execution_plan_status(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
        )
        is None
    )

    conn.fetchrow.side_effect = [{"status": "completed"}, _execution_plan_row(status="completed")]
    done = await manager.reconcile_execution_plan_status(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
    )
    assert done is not None
    assert done["status"] == "completed"

    conn.fetchrow.side_effect = [{"status": "running"}, _execution_plan_row(status="completed")]
    conn.fetchval.side_effect = [0]
    reconciled = await manager.reconcile_execution_plan_status(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
    )
    assert reconciled is not None
    assert conn.execute.await_count >= 2

    conn.fetchrow.side_effect = [{"status": "running"}, None]
    conn.fetchval.side_effect = [2]
    assert (
        await manager.reconcile_execution_plan_status(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
        )
        is None
    )


@pytest.mark.asyncio
async def test_complete_execution_step_additional_paths() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]

    conn.fetchrow.side_effect = [None]
    with pytest.raises(ValueError, match="Execution plan not found"):
        await manager.complete_execution_step(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step_id="22222222-2222-2222-2222-222222222222",
            retry_id="33333333-3333-3333-3333-333333333333",
            worker_id="worker-1",
        )

    conn.fetchrow.side_effect = [{"status": "running", "continuation_interval_seconds": 60}, None]
    with pytest.raises(ValueError, match="Execution step not found"):
        await manager.complete_execution_step(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step_id="22222222-2222-2222-2222-222222222222",
            retry_id="33333333-3333-3333-3333-333333333333",
            worker_id="worker-1",
        )

    conn.fetchrow.side_effect = [
        {"status": "running", "continuation_interval_seconds": 60},
        {"step_index": 0, "status": "running"},
        None,
    ]
    with pytest.raises(RuntimeError, match="Failed to complete execution step"):
        await manager.complete_execution_step(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step_id="22222222-2222-2222-2222-222222222222",
            retry_id="33333333-3333-3333-3333-333333333333",
            worker_id="worker-1",
        )

    conn.fetchrow.side_effect = [
        {"status": "running", "continuation_interval_seconds": 60},
        {"step_index": 0, "status": "running"},
        _execution_step_row(status="completed", attempt_count=1, step_index=0),
        {"step_index": 1},
        _execution_plan_row(status="running", next_run_at=datetime.now(UTC), current_step_index=1),
    ]
    progressed = await manager.complete_execution_step(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        step_id="22222222-2222-2222-2222-222222222222",
        retry_id="33333333-3333-3333-3333-333333333333",
        worker_id="worker-1",
    )
    assert progressed["has_more"] is True
    assert progressed["plan"]["status"] == "running"

    conn.fetchrow.side_effect = [
        {"status": "running", "continuation_interval_seconds": 60},
        {"step_index": 0, "status": "running"},
        _execution_step_row(status="completed", attempt_count=1, step_index=0),
        {"step_index": 1},
        None,
    ]
    with pytest.raises(RuntimeError, match="Failed to update execution plan state"):
        await manager.complete_execution_step(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step_id="22222222-2222-2222-2222-222222222222",
            retry_id="33333333-3333-3333-3333-333333333333",
            worker_id="worker-1",
        )


@pytest.mark.asyncio
async def test_fail_execution_step_terminal_and_error_paths() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]

    conn.fetchrow.side_effect = [None]
    with pytest.raises(ValueError, match="Execution plan not found"):
        await manager.fail_execution_step(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step_id="22222222-2222-2222-2222-222222222222",
            retry_id="33333333-3333-3333-3333-333333333333",
            worker_id="worker-1",
            failure_category="timeout",
            failure_detail="timeout",
        )

    conn.fetchrow.side_effect = [{"status": "running"}, None]
    with pytest.raises(ValueError, match="Execution step not found"):
        await manager.fail_execution_step(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step_id="22222222-2222-2222-2222-222222222222",
            retry_id="33333333-3333-3333-3333-333333333333",
            worker_id="worker-1",
            failure_category="timeout",
            failure_detail="timeout",
        )

    conn.fetchrow.side_effect = [
        {"status": "running"},
        {"status": "running", "attempt_count": 3, "max_attempts": 3},
        _execution_step_row(status="blocked", attempt_count=3, max_attempts=3),
        _execution_plan_row(status="failed"),
    ]
    terminal = await manager.fail_execution_step(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        step_id="22222222-2222-2222-2222-222222222222",
        retry_id="33333333-3333-3333-3333-333333333333",
        worker_id="worker-1",
        failure_category="dependency",
        failure_detail="upstream unavailable",
        retryable=False,
    )
    assert terminal["retry_scheduled"] is False
    assert terminal["plan"]["status"] == "failed"

    conn.fetchrow.side_effect = [
        {"status": "running"},
        {"status": "running", "attempt_count": 1, "max_attempts": 3},
        None,
    ]
    with pytest.raises(RuntimeError, match="Failed to update execution step failure state"):
        await manager.fail_execution_step(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step_id="22222222-2222-2222-2222-222222222222",
            retry_id="33333333-3333-3333-3333-333333333333",
            worker_id="worker-1",
            failure_category="timeout",
            failure_detail="timeout",
        )

    conn.fetchrow.side_effect = [
        {"status": "running"},
        {"status": "running", "attempt_count": 1, "max_attempts": 3},
        _execution_step_row(status="failed", attempt_count=1, max_attempts=3),
        None,
    ]
    with pytest.raises(RuntimeError, match="Failed to update execution plan failure state"):
        await manager.fail_execution_step(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step_id="22222222-2222-2222-2222-222222222222",
            retry_id="33333333-3333-3333-3333-333333333333",
            worker_id="worker-1",
            failure_category="timeout",
            failure_detail="timeout",
        )


@pytest.mark.asyncio
async def test_fail_execution_step_blank_category_defaults_to_transient() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]

    conn.fetchrow.side_effect = [
        {"status": "running"},
        {"status": "running", "attempt_count": 1, "max_attempts": 3},
        _execution_step_row(status="failed", attempt_count=1, max_attempts=3),
        _execution_plan_row(status="running"),
    ]

    failed = await manager.fail_execution_step(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        step_id="22222222-2222-2222-2222-222222222222",
        retry_id="33333333-3333-3333-3333-333333333333",
        worker_id="worker-1",
        failure_category="   ",
        failure_detail="  temporary issue  ",
    )

    assert failed["retry_scheduled"] is True
    assert failed["failure_category"] == "transient"
    assert conn.fetchrow.await_args_list[2].args[6] == "transient"
    assert conn.fetchrow.await_args_list[3].args[5] == "transient"


@pytest.mark.asyncio
async def test_dispatch_execution_step_to_worker_and_claim_paths() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._select_worker_dispatch_target_node = AsyncMock(return_value="node-1")  # type: ignore[method-assign]
    manager._worker_node_meets_dispatch_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]

    queued = _worker_job_row(status="queued", execution_target="any_worker")
    queued["target_node_id"] = "node-1"
    claimed = _worker_job_row(
        status="running",
        execution_target="any_worker",
        claimed_by_node_id="node-1",
    )
    claimed["target_node_id"] = "node-1"
    conn.fetchrow.side_effect = [
        queued,
        {"node_id": "node-1"},
        claimed,
    ]
    conn.fetch.side_effect = [
        [
            {
                **_worker_job_row(status="queued", execution_target="any_worker"),
                "target_node_id": "node-1",
            }
        ]
    ]
    conn.fetchval.side_effect = [1]

    dispatch = await manager.dispatch_execution_step_to_worker(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        step=_execution_step_row(
            status="running",
            execution_target="any_worker",
            required_capabilities=["repo.patch"],
        ),
        retry={
            "retry_id": "33333333-3333-3333-3333-333333333333",
            "attempt_number": 1,
        },
        dispatcher_id="plan-worker-1",
    )
    assert dispatch["status"] == "queued"
    insert_call = conn.fetchrow.await_args_list[0]
    _, *insert_args = insert_call.args
    payload_json = json.loads(insert_args[-1])
    assert dispatch["execution_mode"] == "live"
    assert payload_json["execution_mode"] == "live"
    assert "prompt_text" not in payload_json
    worker_artifacts = payload_json["worker_artifacts"]
    assert len(worker_artifacts) == 1
    assert worker_artifacts[0]["artifact_type"] == "instruction"
    assert worker_artifacts[0]["content"]["text"] == "Do the next task"
    assert worker_artifacts[0]["resource_scope"].startswith("worker_artifact:")

    claimed_job = await manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        required_capabilities=["repo.patch", "repo.pr.open"],
    )
    assert claimed_job is not None
    assert claimed_job["status"] == "running"
    assert claimed_job["execution_mode"] == "live"
    assert conn.execute.await_count >= 3
    assert "AND execution_mode = 'live'" in conn.fetch.await_args_list[0].args[0]

    with pytest.raises(ValueError, match="windows_local"):
        await manager.dispatch_execution_step_to_worker(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step=_execution_step_row(status="running", execution_target="windows_local"),
            retry={"retry_id": "33333333-3333-3333-3333-333333333333"},
            dispatcher_id="plan-worker-1",
        )


@pytest.mark.asyncio
async def test_dispatch_and_claim_worker_job_error_paths() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="missing identifiers"):
        await manager.dispatch_execution_step_to_worker(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step={"execution_target": "any_worker"},
            retry={"retry_id": "33333333-3333-3333-3333-333333333333"},
            dispatcher_id="plan-worker-1",
        )

    with pytest.raises(ValueError, match="execution step metadata must be an object"):
        await manager.dispatch_execution_step_to_worker(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step={
                **_execution_step_row(status="running", execution_target="any_worker"),
                "metadata": "bad",  # type: ignore[dict-item]
            },
            retry={"retry_id": "33333333-3333-3333-3333-333333333333"},
            dispatcher_id="plan-worker-1",
        )

    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._select_worker_dispatch_target_node = AsyncMock(return_value="node-1")  # type: ignore[method-assign]
    manager._is_worker_dispatch_target_eligible = AsyncMock(return_value=True)  # type: ignore[method-assign]
    conn.fetchrow.side_effect = [
        _worker_job_row(status="queued"),
        _worker_job_row(status="queued"),
        None,
    ]

    step_default_runtime = _execution_step_row(status="running", execution_target="any_worker")
    step_default_runtime["max_runtime_seconds"] = None
    step_default_runtime["metadata"] = None
    dispatched = await manager.dispatch_execution_step_to_worker(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        step=step_default_runtime,
        retry={"retry_id": "33333333-3333-3333-3333-333333333333"},
        dispatcher_id="",
    )
    assert dispatched["status"] == "queued"

    step_custom_payload = _execution_step_row(status="running", execution_target="worker:node-1")
    step_custom_payload["metadata"] = {
        "runner": "codex",
        "payload": {"artifact_hint": "required"},
    }
    dispatched_payload = await manager.dispatch_execution_step_to_worker(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        step=step_custom_payload,
        retry={"retry_id": "44444444-4444-4444-4444-444444444444"},
        dispatcher_id="plan-worker-1",
    )
    assert dispatched_payload["status"] == "queued"

    with pytest.raises(RuntimeError, match="Failed to enqueue worker job"):
        await manager.dispatch_execution_step_to_worker(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            step=step_custom_payload,
            retry={"retry_id": "55555555-5555-5555-5555-555555555555"},
            dispatcher_id="plan-worker-1",
        )

    claim_conn = _FakeConn()
    claim_manager = TenantAdminManager(pool=_FakePool(claim_conn))  # type: ignore[arg-type]
    claim_manager._worker_node_meets_dispatch_requirements = AsyncMock(  # type: ignore[method-assign]
        return_value=True
    )

    with pytest.raises(ValueError, match="Missing node_id"):
        await claim_manager.claim_worker_dispatch_job(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="",
        )

    claim_conn.fetchrow.return_value = {"node_id": "node-1"}
    claim_conn.fetch.return_value = []
    no_candidates = await claim_manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        required_capabilities=["repo.patch"],
    )
    assert no_candidates is None

    candidate = _worker_job_row(status="queued", required_capabilities=["repo.patch"])
    claim_conn.fetch.return_value = [candidate]
    skipped_subset = await claim_manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        required_capabilities=["repo.pr.open"],
    )
    assert skipped_subset is None

    claim_conn.fetchval.return_value = 0
    skipped_allowlist = await claim_manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        required_capabilities=["repo.patch"],
    )
    assert skipped_allowlist is None

    candidate["max_runtime_seconds"] = None
    claim_conn.fetch.return_value = [candidate]
    claim_conn.fetchval.return_value = 1
    claim_conn.fetchrow.side_effect = [{"node_id": "node-1"}, None]
    no_claim = await claim_manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        required_capabilities=["repo.patch"],
    )
    assert no_claim is None


@pytest.mark.asyncio
async def test_claim_worker_dispatch_job_skips_test_mode_candidates() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._worker_node_meets_dispatch_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]

    conn.fetchrow.return_value = {"node_id": "node-1"}
    conn.fetch.return_value = [_worker_job_row(status="queued", execution_mode="test")]

    claimed = await manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        required_capabilities=["repo.patch"],
    )

    assert claimed is None
    conn.fetchval.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatch_and_claim_rollout_guard_paths() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    tenant_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    manager._select_worker_dispatch_target_node = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="No eligible worker node"):
        await manager.dispatch_execution_step_to_worker(
            tenant_id=tenant_id,
            plan_id="11111111-1111-1111-1111-111111111111",
            step=_execution_step_row(status="running", execution_target="any_worker"),
            retry={"retry_id": "33333333-3333-3333-3333-333333333333"},
            dispatcher_id="plan-worker-1",
        )

    manager._is_worker_dispatch_target_eligible = AsyncMock(return_value=False)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="not eligible for dispatch"):
        await manager.dispatch_execution_step_to_worker(
            tenant_id=tenant_id,
            plan_id="11111111-1111-1111-1111-111111111111",
            step=_execution_step_row(status="running", execution_target="worker:node-1"),
            retry={"retry_id": "44444444-4444-4444-4444-444444444444"},
            dispatcher_id="plan-worker-1",
        )

    manager._settings_cache[(tenant_id, "security", "worker_rollout_stage")] = "disabled"
    assert (
        await manager.claim_worker_dispatch_job(
            tenant_id=tenant_id,
            node_id="node-1",
            required_capabilities=["repo.patch"],
        )
        is None
    )
    manager._settings_cache[(tenant_id, "security", "worker_rollout_stage")] = "canary"
    manager._settings_cache[(tenant_id, "security", "worker_canary_enabled")] = False
    assert (
        await manager.claim_worker_dispatch_job(
            tenant_id=tenant_id,
            node_id="node-1",
            required_capabilities=["repo.patch"],
        )
        is None
    )


def test_worker_delegation_helper_methods() -> None:
    assert TenantAdminManager._normalize_worker_delegation_ttl_seconds("bad") == 3600
    assert TenantAdminManager._normalize_execution_mode("TEST") == "test"
    assert TenantAdminManager._normalize_worker_delegation_permission("repo.patch") == "repo.patch"
    assert TenantAdminManager._normalize_worker_delegation_permissions(
        ["repo.patch", "repo.patch", "repo.commit"]
    ) == ["repo.patch", "repo.commit"]
    assert (
        TenantAdminManager._normalize_worker_delegation_scope("repo:/workspace/repo")
        == "repo:/workspace/repo"
    )
    assert (
        TenantAdminManager._normalize_worker_artifact_scope(
            "worker_artifact:tenant-a:plan-1:step-1:retry-1"
        )
        == "worker_artifact:tenant-a:plan-1:step-1:retry-1"
    )
    with pytest.raises(ValueError, match="permission must be one of"):
        TenantAdminManager._normalize_worker_delegation_permission("bogus")
    with pytest.raises(ValueError, match="permissions must be one of"):
        TenantAdminManager._normalize_worker_delegation_permissions(["bogus"])
    with pytest.raises(ValueError, match="permissions must be a non-empty array"):
        TenantAdminManager._normalize_worker_delegation_permissions([])
    with pytest.raises(ValueError, match="worker delegation scope must start"):
        TenantAdminManager._normalize_worker_delegation_scope("worker_artifact:tenant:plan")
    with pytest.raises(ValueError, match="worker artifact scope must start"):
        TenantAdminManager._normalize_worker_artifact_scope("repo:/workspace/repo")
    with pytest.raises(ValueError, match="execution_mode must be one of"):
        TenantAdminManager._normalize_execution_mode("preview")
    with pytest.raises(ValueError, match="capabilities must be an array of strings"):
        TenantAdminManager._normalize_worker_capabilities(["repo.patch", 123])  # type: ignore[list-item]

    artifacts = TenantAdminManager._build_worker_artifacts(
        artifact_scope="worker_artifact:tenant-a:plan-1:step-1:retry-1",
        title="Step 1",
        prompt_text="Do the thing",
        metadata={
            "worker_artifacts": [
                {
                    "artifact_id": "context-1",
                    "content": "approved excerpt",
                    "resource_scope": "bad-scope",
                    "approved": False,
                },
                {
                    "artifact_id": "context-2",
                    "content": {"markdown": "## Context"},
                    "metadata": "skip-me",
                },
                "skip-me",
            ]
        },
    )
    assert len(artifacts) == 3
    assert artifacts[0]["artifact_type"] == "instruction"
    assert artifacts[1]["artifact_id"] == "context-1"
    assert artifacts[1]["resource_scope"] == "worker_artifact:tenant-a:plan-1:step-1:retry-1"
    assert artifacts[1]["content"]["text"] == "approved excerpt"
    assert artifacts[2]["artifact_id"] == "context-2"
    assert artifacts[2]["metadata"] == {}
    sanitized = TenantAdminManager._sanitize_worker_control_payload(
        {
            "prompt_text": "secret",
            "message": "also secret",
            "worker_artifacts": ["skip"],
            "repo_root": "/workspace/repo",
            "command": ["git", "status"],
        }
    )
    assert sanitized == {"repo_root": "/workspace/repo", "command": ["git", "status"]}
    assert (
        TenantAdminManager._worker_artifact_scope(
            tenant_id="tenant-a",
            plan_id="plan-1",
            step_id="step-1",
            retry_id="retry-1",
        )
        == "worker_artifact:tenant-a:plan-1:step-1:retry-1"
    )
    assert (
        TenantAdminManager._worker_artifact_scope(
            tenant_id="tenant-a",
            plan_id="plan-1",
            step_id="step-1",
            retry_id="retry-1",
            execution_mode="test",
        )
        == "worker_artifact:test:tenant-a:plan-1:step-1:retry-1"
    )
    assert TenantAdminManager._resolve_worker_delegation_scope_for_job(
        action="repo.patch",
        payload_json={"repo_root": "/workspace/repo"},
    ) == {
        "permission": "repo.patch",
        "resource_scope": "repo:/workspace/repo",
        "resource_id": "/workspace/repo",
    }
    assert TenantAdminManager._resolve_worker_delegation_scope_for_job(
        action="codex.session.control",
        payload_json={"session_id": "sess-1"},
    ) == {
        "permission": "codex.session.control",
        "resource_scope": "codex.session:sess-1",
        "resource_id": "sess-1",
    }
    assert (
        TenantAdminManager._resolve_worker_delegation_scope_for_job(
            action="worker.noop",
            payload_json={},
        )
        is None
    )
    assert TenantAdminManager._resolve_worker_messaging_scope_for_job(
        action="messaging.draft.open",
        payload_json={"provider": "WHATSAPP", "thread_id": "thread-1"},
    ) == {
        "permission": "draft",
        "provider": "whatsapp",
        "chat_id": "thread-1",
    }
    assert TenantAdminManager._resolve_worker_messaging_scope_for_job(
        action="messaging.send",
        payload_json={"provider": "sms", "conversation_id": "conv-1"},
    ) == {
        "permission": "send",
        "provider": "sms",
        "chat_id": "conv-1",
    }
    assert TenantAdminManager._resolve_worker_messaging_scope_for_job(
        action="email.draft.reply",
        payload_json={"account_id": "acct-1"},
    ) == {
        "permission": "draft",
        "provider": "email",
        "chat_id": "acct-1",
    }
    assert TenantAdminManager._redact_worker_messaging_payload(
        ["hello", {"body": "secret"}]
    ) == ["hello", {"body": "[REDACTED]"}]
    assert TenantAdminManager._worker_delegation_scope_matches(
        "repo:/workspace/repo", "repo:/workspace/repo"
    )
    assert TenantAdminManager._worker_delegation_scope_matches("repo:*", "repo:/workspace/repo")
    assert not TenantAdminManager._worker_delegation_scope_matches(
        "repo:/workspace/other", "repo:/workspace/repo"
    )


@pytest.mark.asyncio
async def test_get_trust_storage_initializes_once_and_caches() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]

    class _FakeTrustStorage:
        def __init__(self, *, schema: str) -> None:
            self.schema = schema
            self.initialize = AsyncMock(return_value=None)

    settings = type("Settings", (), {"postgres_control_plane_schema": "tenant_ci"})()

    with (
        patch("zetherion_ai.config.get_settings", return_value=settings),
        patch("zetherion_ai.trust.storage.TrustStorage", _FakeTrustStorage),
    ):
        storage_one = await manager._get_trust_storage()
        storage_two = await manager._get_trust_storage()

    assert storage_one is storage_two
    assert storage_one.schema == "tenant_ci"
    storage_one.initialize.assert_awaited_once_with(manager._pool)


@pytest.mark.asyncio
async def test_worker_delegation_grant_list_and_lookup_methods() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    wildcard = TrustGrantRecord(
        grant_id="11111111-1111-1111-1111-111111111111",
        tenant_id="tenant-a",
        grantee_id="node-1",
        grantee_type="worker_node",
        resource_scope="repo:*",
        permissions=["*"],
        source_system="worker_delegation_grant",
        source_record_id="tenant-a:node-1:repo:*",
        metadata={},
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    scoped = TrustGrantRecord(
        grant_id="22222222-2222-2222-2222-222222222222",
        tenant_id="tenant-a",
        grantee_id="node-1",
        grantee_type="worker_node",
        resource_scope="repo:/workspace/repo",
        permissions=["repo.patch"],
        source_system="worker_delegation_grant",
        source_record_id="tenant-a:node-1:repo:/workspace/repo",
        metadata={"ticket": "CHG-2"},
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    trust_storage = MagicMock()
    trust_storage.list_active_grants = AsyncMock(return_value=[wildcard, scoped])
    manager._get_trust_storage = AsyncMock(return_value=trust_storage)  # type: ignore[method-assign]

    listed = await manager.list_worker_delegation_grants(
        tenant_id="tenant-a",
        node_id="node-1",
        resource_scope_prefix="repo:*",
    )
    assert [grant["grant_id"] for grant in listed] == [
        "11111111-1111-1111-1111-111111111111",
        "22222222-2222-2222-2222-222222222222",
    ]

    found = await manager.get_active_worker_delegation_grant(
        tenant_id="tenant-a",
        node_id="node-1",
        resource_scope="repo:/workspace/repo",
        permission="repo.patch",
    )
    assert found is not None
    assert found["grant_id"] == "11111111-1111-1111-1111-111111111111"

    trust_storage.list_active_grants = AsyncMock(return_value=[scoped])
    missing = await manager.get_active_worker_delegation_grant(
        tenant_id="tenant-a",
        node_id="node-1",
        resource_scope="repo:/workspace/repo",
        permission="repo.commit",
    )
    assert missing is None

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.list_worker_delegation_grants(tenant_id="tenant-a", node_id="")
    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.get_active_worker_delegation_grant(
            tenant_id="tenant-a",
            node_id="",
            resource_scope="repo:/workspace/repo",
            permission="repo.patch",
        )


@pytest.mark.asyncio
async def test_put_worker_delegation_grant_validation_errors() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager.get_worker_node = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.put_worker_delegation_grant(
            tenant_id="tenant-a",
            node_id="",
            resource_scope="repo:/workspace/repo",
            permissions=["repo.patch"],
            ttl_seconds=3600,
        )

    with pytest.raises(ValueError, match="Worker node not found"):
        await manager.put_worker_delegation_grant(
            tenant_id="tenant-a",
            node_id="node-1",
            resource_scope="repo:/workspace/repo",
            permissions=["repo.patch"],
            ttl_seconds=3600,
        )


@pytest.mark.asyncio
async def test_claim_worker_job_enforces_delegation_grants_for_repo_actions() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._worker_node_meets_dispatch_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]

    candidate = _worker_job_row(
        status="queued",
        required_capabilities=[],
        action="repo.patch",
        payload_json={
            "runner": "codex",
            "repo_root": "/workspace/repo",
            "command": ["git", "status"],
            "worker_artifacts": [],
        },
    )

    denied_reasons: list[dict[str, Any]] = []
    conn.fetchrow.side_effect = [{"node_id": "node-1"}]
    conn.fetch.return_value = [candidate]
    conn.fetchval.return_value = 1
    manager.get_active_worker_delegation_grant = AsyncMock(return_value=None)  # type: ignore[method-assign]
    denied = await manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        required_capabilities=["repo.patch"],
        denied_reasons=denied_reasons,
    )
    assert denied is None
    assert denied_reasons
    assert denied_reasons[0]["kind"] == "delegation_grant"
    assert denied_reasons[0]["reason"] == "grant_required"
    assert denied_reasons[0]["resource_scope"] == "repo:/workspace/repo"

    conn.fetch.return_value = [candidate]
    conn.fetchrow.side_effect = [
        {"node_id": "node-1"},
        _worker_job_row(
            status="running",
            required_capabilities=[],
            action="repo.patch",
            payload_json=dict(candidate["payload_json"]),
        ),
    ]
    manager.get_active_worker_delegation_grant = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "grant_id": "99999999-9999-9999-9999-999999999999",
            "resource_scope": "repo:/workspace/repo",
            "permissions": ["repo.patch"],
        }
    )
    claimed = await manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        required_capabilities=["repo.patch"],
        denied_reasons=[],
    )
    assert claimed is not None
    access = claimed["payload_json"]["worker_delegation_access"]
    assert access["grant_id"] == "99999999-9999-9999-9999-999999999999"
    assert access["resource_scope"] == "repo:/workspace/repo"
    assert access["permission"] == "repo.patch"


@pytest.mark.asyncio
async def test_claim_worker_job_enforces_messaging_grants_and_redaction() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._worker_node_meets_dispatch_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    manager.get_active_worker_delegation_grant = AsyncMock(return_value={})  # type: ignore[method-assign]

    messaging_candidate = _worker_job_row(
        status="queued",
        required_capabilities=[],
        action="messaging.read",
        payload_json={
            "provider": "whatsapp",
            "chat_id": "chat-1",
            "text": "secret message",
            "nested": {"body": "also secret"},
        },
    )

    denied_reasons: list[dict[str, Any]] = []
    conn.fetchrow.side_effect = [{"node_id": "node-1"}]
    conn.fetch.return_value = [messaging_candidate]
    conn.fetchval.return_value = 1
    manager.get_active_worker_messaging_grant = AsyncMock(return_value=None)  # type: ignore[method-assign]
    denied = await manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        required_capabilities=["repo.patch"],
        denied_reasons=denied_reasons,
    )
    assert denied is None
    assert denied_reasons
    assert denied_reasons[0]["reason"] == "grant_required"

    conn.fetch.return_value = [messaging_candidate]
    conn.fetchrow.side_effect = [
        {"node_id": "node-1"},
        _worker_job_row(
            status="running",
            required_capabilities=[],
            action="messaging.read",
            payload_json=dict(messaging_candidate["payload_json"]),
        ),
    ]
    manager.get_active_worker_messaging_grant = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "grant_id": "55555555-5555-5555-5555-555555555555",
            "redacted_payload": True,
        }
    )
    granted = await manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        required_capabilities=["repo.patch"],
        denied_reasons=[],
    )
    assert granted is not None
    assert granted["status"] == "running"
    payload = granted["payload_json"]
    assert payload["text"] == "[REDACTED]"
    assert payload["nested"]["body"] == "[REDACTED]"
    assert payload["worker_messaging_access"]["redacted_payload"] is True

    plain_candidate = _worker_job_row(
        status="queued",
        action="messaging.read",
        payload_json="not-a-dict",
    )
    plain_candidate["required_capabilities"] = []
    claimed_plain_row = _worker_job_row(
        status="running",
        action="messaging.read",
        payload_json="not-a-dict",
    )
    claimed_plain_row["required_capabilities"] = []
    conn.fetch.return_value = [plain_candidate]
    conn.fetchrow.side_effect = [
        {"node_id": "node-1"},
        claimed_plain_row,
    ]
    manager.get_active_worker_messaging_grant = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "grant_id": "66666666-6666-6666-6666-666666666666",
            "redacted_payload": False,
        }
    )
    with (
        patch.object(
            TenantAdminManager,
            "_resolve_worker_delegation_scope_for_job",
            return_value=None,
        ),
        patch.object(
            TenantAdminManager,
            "_resolve_worker_messaging_scope_for_job",
            return_value={"permission": "read", "provider": "whatsapp", "chat_id": "chat-1"},
        ),
    ):
        granted_plain = await manager.claim_worker_dispatch_job(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            required_capabilities=["repo.patch"],
            denied_reasons=[],
        )
    assert granted_plain is not None
    plain_payload = granted_plain["payload_json"]
    assert plain_payload["worker_messaging_access"]["grant_id"] == (
        "66666666-6666-6666-6666-666666666666"
    )
    assert plain_payload["worker_messaging_access"]["redacted_payload"] is False
    assert "text" not in plain_payload


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delegation_scope", "messaging_scope", "expected_reason"),
    [
        (
            {"permission": "bogus", "resource_scope": "repo:/workspace/repo"},
            None,
            "invalid_permission",
        ),
        (
            {"permission": "repo.patch", "resource_scope": ""},
            None,
            "missing_resource_scope",
        ),
        (
            None,
            {"permission": "bogus", "provider": "whatsapp", "chat_id": "chat-1"},
            "invalid_permission",
        ),
        (
            None,
            {"permission": "read", "provider": "sms", "chat_id": "chat-1"},
            "invalid_provider",
        ),
        (
            None,
            {"permission": "send", "provider": "whatsapp", "chat_id": "chat-1"},
            "unsupported_permission",
        ),
        (
            None,
            {"permission": "read", "provider": "whatsapp", "chat_id": ""},
            "missing_chat_id",
        ),
    ],
)
async def test_claim_worker_dispatch_job_collects_scope_validation_denials(
    delegation_scope: dict[str, Any] | None,
    messaging_scope: dict[str, Any] | None,
    expected_reason: str,
) -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._worker_node_meets_dispatch_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    manager.get_active_worker_delegation_grant = AsyncMock(return_value={})  # type: ignore[method-assign]
    manager.get_active_worker_messaging_grant = AsyncMock(return_value={})  # type: ignore[method-assign]

    conn.fetchrow.return_value = {"node_id": "node-1"}
    conn.fetchval.return_value = 1
    conn.fetch.return_value = [
        _worker_job_row(
            status="queued",
            required_capabilities=[],
            action="worker.noop",
            payload_json={},
        )
    ]

    denied_reasons: list[dict[str, Any]] = []
    with (
        patch.object(
            TenantAdminManager,
            "_resolve_worker_delegation_scope_for_job",
            return_value=delegation_scope,
        ),
        patch.object(
            TenantAdminManager,
            "_resolve_worker_messaging_scope_for_job",
            return_value=messaging_scope,
        ),
    ):
        claimed = await manager.claim_worker_dispatch_job(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            required_capabilities=["repo.patch"],
            denied_reasons=denied_reasons,
        )

    assert claimed is None
    assert denied_reasons
    assert denied_reasons[0]["reason"] == expected_reason
    if delegation_scope is not None:
        assert denied_reasons[0]["kind"] == "delegation_grant"
    else:
        assert "kind" not in denied_reasons[0]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("delegation_scope", "messaging_scope", "delegation_grant", "messaging_grant"),
    [
        (
            {"permission": "bogus", "resource_scope": "repo:/workspace/repo"},
            None,
            {},
            {},
        ),
        (
            {"permission": "repo.patch", "resource_scope": ""},
            None,
            {},
            {},
        ),
        (
            {"permission": "repo.patch", "resource_scope": "repo:/workspace/repo"},
            None,
            None,
            {},
        ),
        (
            None,
            {"permission": "read", "provider": "sms", "chat_id": "chat-1"},
            {},
            {},
        ),
        (
            None,
            {"permission": "send", "provider": "whatsapp", "chat_id": "chat-1"},
            {},
            {},
        ),
        (
            None,
            {"permission": "read", "provider": "whatsapp", "chat_id": ""},
            {},
            {},
        ),
        (
            None,
            {"permission": "read", "provider": "whatsapp", "chat_id": "chat-1"},
            {},
            None,
        ),
    ],
)
async def test_claim_worker_dispatch_job_skips_invalid_scopes_without_denied_reason_collection(
    delegation_scope: dict[str, Any] | None,
    messaging_scope: dict[str, Any] | None,
    delegation_grant: dict[str, Any] | None,
    messaging_grant: dict[str, Any] | None,
) -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._worker_node_meets_dispatch_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]
    manager.get_active_worker_delegation_grant = AsyncMock(  # type: ignore[method-assign]
        return_value=delegation_grant
    )
    manager.get_active_worker_messaging_grant = AsyncMock(  # type: ignore[method-assign]
        return_value=messaging_grant
    )

    conn.fetchrow.return_value = {"node_id": "node-1"}
    conn.fetchval.return_value = 1
    conn.fetch.return_value = [
        _worker_job_row(
            status="queued",
            required_capabilities=[],
            action="worker.noop",
            payload_json={},
        )
    ]

    with (
        patch.object(
            TenantAdminManager,
            "_resolve_worker_delegation_scope_for_job",
            return_value=delegation_scope,
        ),
        patch.object(
            TenantAdminManager,
            "_resolve_worker_messaging_scope_for_job",
            return_value=messaging_scope,
        ),
    ):
        claimed = await manager.claim_worker_dispatch_job(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            required_capabilities=["repo.patch"],
        )

    assert claimed is None


@pytest.mark.asyncio
async def test_claim_worker_dispatch_job_returns_none_for_missing_or_ineligible_node_record(
) -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._worker_node_meets_dispatch_requirements = AsyncMock(side_effect=[False])  # type: ignore[method-assign]

    conn.fetchrow.return_value = None
    assert (
        await manager.claim_worker_dispatch_job(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
        )
        is None
    )

    conn.fetchrow.return_value = {"node_id": "node-1"}
    assert (
        await manager.claim_worker_dispatch_job(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
        )
        is None
    )


@pytest.mark.asyncio
async def test_claim_worker_dispatch_job_handles_lease_collision_between_nodes() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._worker_node_meets_dispatch_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]

    candidate = _worker_job_row(status="queued", execution_target="any_worker")
    claimed_by_node_2 = _worker_job_row(
        status="running",
        execution_target="any_worker",
        claimed_by_node_id="node-2",
    )
    conn.fetch.return_value = [candidate]
    conn.fetchval.return_value = 1
    conn.fetchrow.side_effect = [
        {"node_id": "node-1"},
        None,
        {"node_id": "node-2"},
        claimed_by_node_2,
    ]

    node_1_claim = await manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        required_capabilities=["repo.patch"],
    )
    node_2_claim = await manager.claim_worker_dispatch_job(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-2",
        required_capabilities=["repo.patch"],
    )

    assert node_1_claim is None
    assert node_2_claim is not None
    assert node_2_claim["claimed_by_node_id"] == "node-2"


@pytest.mark.asyncio
async def test_select_worker_dispatch_target_node_returns_none_for_blank_eligible_nodes() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._worker_node_meets_dispatch_requirements = AsyncMock(return_value=True)  # type: ignore[method-assign]

    conn.fetch.return_value = [
        {
            "node_id": "",
            "status": "active",
            "health_status": "healthy",
            "health_score": 90,
            "metadata": {},
            "last_heartbeat_at": datetime.now(UTC),
        }
    ]
    blank_only = await manager._select_worker_dispatch_target_node(
        conn=conn,
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        required_capabilities=[],
    )
    assert blank_only is None


@pytest.mark.asyncio
async def test_worker_delegation_grant_lifecycle_methods() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager.get_worker_node = AsyncMock(  # type: ignore[method-assign]
        return_value={"node_id": "node-1", "status": "active", "health_status": "healthy"}
    )
    manager.list_worker_delegation_grants = AsyncMock(return_value=[])  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    trust_storage = MagicMock()
    trust_storage.upsert_grant = AsyncMock(
        return_value=TrustGrantRecord(
            grant_id="77777777-7777-7777-7777-777777777777",
            tenant_id="tenant-a",
            grantee_id="node-1",
            grantee_type="worker_node",
            resource_scope="repo:/workspace/repo",
            permissions=["repo.patch", "repo.commit"],
            granted_by_id="operator-1",
            granted_by_type="admin_actor",
            source_system="worker_delegation_grant",
            source_record_id="tenant-a:node-1:repo:/workspace/repo",
            metadata={"ticket": "CHG-1"},
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    trust_storage.revoke_grant = AsyncMock(
        return_value=TrustGrantRecord(
            grant_id="77777777-7777-7777-7777-777777777777",
            tenant_id="tenant-a",
            grantee_id="node-1",
            grantee_type="worker_node",
            resource_scope="repo:/workspace/repo",
            permissions=["repo.patch", "repo.commit"],
            granted_by_id="operator-1",
            granted_by_type="admin_actor",
            source_system="worker_delegation_grant",
            source_record_id="tenant-a:node-1:repo:/workspace/repo",
            metadata={"ticket": "CHG-1"},
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            revoked_at=datetime.now(UTC),
            revoke_reason="expired",
        )
    )
    manager._get_trust_storage = AsyncMock(return_value=trust_storage)  # type: ignore[method-assign]

    created = await manager.put_worker_delegation_grant(
        tenant_id="tenant-a",
        node_id="node-1",
        resource_scope="repo:/workspace/repo",
        permissions=["repo.patch", "repo.commit"],
        ttl_seconds=3600,
        metadata={"ticket": "CHG-1"},
        actor=_actor(change_ticket_id="CHG-1"),
    )
    assert created["grant_id"] == "77777777-7777-7777-7777-777777777777"
    assert created["resource_scope"] == "repo:/workspace/repo"
    assert created["permissions"] == ["repo.patch", "repo.commit"]

    revoked = await manager.revoke_worker_delegation_grant(
        tenant_id="tenant-a",
        grant_id="77777777-7777-7777-7777-777777777777",
        actor=_actor(change_ticket_id="CHG-1"),
        reason="expired",
    )
    assert revoked["grant_id"] == "77777777-7777-7777-7777-777777777777"
    assert revoked["revoke_reason"] == "expired"
    assert manager._write_audit.await_count == 2


@pytest.mark.asyncio
async def test_worker_messaging_grant_lifecycle_methods() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager.get_worker_node = AsyncMock(  # type: ignore[method-assign]
        return_value={"node_id": "node-1", "status": "active", "health_status": "healthy"}
    )
    manager.list_worker_messaging_grants = AsyncMock(return_value=[])  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "grant_id": "55555555-5555-5555-5555-555555555555",
                "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "node_id": "node-1",
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "allow_read": True,
                "allow_send": False,
                "redacted_payload": True,
                "metadata": {},
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "revoked_at": None,
                "revoked_reason": None,
                "created_by": "operator-1",
                "revoked_by": None,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
            {
                "grant_id": "55555555-5555-5555-5555-555555555555",
                "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "node_id": "node-1",
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "allow_read": True,
                "allow_send": False,
                "redacted_payload": True,
                "metadata": {},
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "revoked_at": None,
                "revoked_reason": None,
                "created_by": "operator-1",
                "revoked_by": None,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
            {
                "grant_id": "55555555-5555-5555-5555-555555555555",
                "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "node_id": "node-1",
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "allow_read": True,
                "allow_send": False,
                "redacted_payload": True,
                "metadata": {},
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "revoked_at": datetime.now(UTC),
                "revoked_reason": "manual revoke",
                "created_by": "operator-1",
                "revoked_by": "operator-1",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
            {
                "grant_id": "55555555-5555-5555-5555-555555555555",
                "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
                "node_id": "node-1",
                "provider": "whatsapp",
                "chat_id": "chat-1",
                "allow_read": True,
                "allow_send": False,
                "redacted_payload": True,
                "metadata": {},
                "expires_at": datetime.now(UTC) + timedelta(hours=1),
                "revoked_at": datetime.now(UTC),
                "revoked_reason": "manual revoke",
                "created_by": "operator-1",
                "revoked_by": "operator-1",
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            },
        ]
    )
    manager._fetch = AsyncMock(return_value=[{"grant_id": "a"}, {"grant_id": "b"}])  # type: ignore[method-assign]

    created = await manager.put_worker_messaging_grant(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        provider="whatsapp",
        chat_id="chat-1",
        allow_read=True,
        allow_send=False,
        ttl_seconds=3600,
        redacted_payload=True,
        metadata={"scope": "test"},
        actor=_actor(),
    )
    assert created["grant_id"] == "55555555-5555-5555-5555-555555555555"

    revoked = await manager.revoke_worker_messaging_grant(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        grant_id="55555555-5555-5555-5555-555555555555",
        actor=_actor(),
        reason="manual revoke",
    )
    assert revoked["idempotent"] is False
    assert revoked["revoked_reason"] == "manual revoke"

    purged = await manager.purge_expired_worker_messaging_grants(limit=10)
    assert purged == 2


@pytest.mark.asyncio
async def test_worker_messaging_grant_list_and_active_lookup_filters() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager._fetch = AsyncMock(return_value=[{"grant_id": "g-1"}])  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"grant_id": "g-read", "allow_read": True},
            {"grant_id": "g-draft", "allow_draft": True},
        ]
    )

    listed = await manager.list_worker_messaging_grants(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        provider=" WhatsApp ",
        chat_id=" chat-1 ",
        include_expired=False,
        include_revoked=False,
        limit=5000,
    )
    assert listed == [{"grant_id": "g-1"}]
    list_sql, *_list_args = manager._fetch.await_args.args
    assert "revoked_at IS NULL" in list_sql
    assert "expires_at > now()" in list_sql
    assert _list_args[2] == "whatsapp"
    assert _list_args[3] == "chat-1"
    assert _list_args[-1] == 1000

    manager._fetch.reset_mock()
    manager._fetch.return_value = []
    await manager.list_worker_messaging_grants(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        include_expired=True,
        include_revoked=True,
        limit=0,
    )
    list_sql_unfiltered, *unfiltered_args = manager._fetch.await_args.args
    assert "revoked_at IS NULL" not in list_sql_unfiltered
    assert "expires_at > now()" not in list_sql_unfiltered
    assert unfiltered_args[-1] == 1

    active_read = await manager.get_active_worker_messaging_grant(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        provider="whatsapp",
        chat_id="chat-1",
        permission=" read ",
    )
    assert active_read == {"grant_id": "g-read", "allow_read": True}
    lookup_sql, *_lookup_args = manager._fetchrow.await_args_list[0].args
    assert "AND allow_read = TRUE" in lookup_sql

    active_draft = await manager.get_active_worker_messaging_grant(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        provider="whatsapp",
        chat_id="chat-1",
        permission="draft",
    )
    assert active_draft == {"grant_id": "g-draft", "allow_draft": True}
    lookup_draft_sql, *_lookup_draft_args = manager._fetchrow.await_args_list[1].args
    assert "AND allow_draft = TRUE" in lookup_draft_sql

    active_send = await manager.get_active_worker_messaging_grant(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        provider="whatsapp",
        chat_id="chat-1",
        permission="send",
    )
    assert active_send is None
    assert manager._fetchrow.await_count == 2

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.get_active_worker_messaging_grant(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id=" ",
            provider="whatsapp",
            chat_id="chat-1",
            permission="read",
        )
    with pytest.raises(ValueError, match="Missing chat_id"):
        await manager.get_active_worker_messaging_grant(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            provider="whatsapp",
            chat_id=" ",
            permission="send",
        )


@pytest.mark.asyncio
async def test_worker_messaging_grant_error_and_idempotent_paths() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager.get_worker_node = AsyncMock(return_value=None)  # type: ignore[method-assign]
    manager.list_worker_messaging_grants = AsyncMock(return_value=[])  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.put_worker_messaging_grant(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="",
            provider="whatsapp",
            chat_id="chat-1",
            allow_read=True,
            allow_send=False,
            ttl_seconds=60,
        )
    with pytest.raises(ValueError, match="Missing chat_id"):
        await manager.put_worker_messaging_grant(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            provider="whatsapp",
            chat_id="",
            allow_read=True,
            allow_send=False,
            ttl_seconds=60,
        )
    with pytest.raises(ValueError, match="Worker node not found"):
        await manager.put_worker_messaging_grant(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            provider="whatsapp",
            chat_id="chat-1",
            allow_read=True,
            allow_send=False,
            ttl_seconds=60,
        )

    manager.get_worker_node = AsyncMock(  # type: ignore[method-assign]
        return_value={"node_id": "node-1", "status": "active", "health_status": "healthy"}
    )
    with pytest.raises(ValueError, match="does not support worker messaging permissions: send"):
        await manager.put_worker_messaging_grant(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            provider="whatsapp",
            chat_id="chat-1",
            allow_send=True,
            ttl_seconds=60,
        )
    with pytest.raises(ValueError, match="does not support worker messaging permissions: read"):
        await manager.put_worker_messaging_grant(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            provider="email",
            chat_id="thread-1",
            allow_read=True,
            ttl_seconds=60,
        )

    manager.get_worker_node = AsyncMock(  # type: ignore[method-assign]
        return_value={"node_id": "node-1", "status": "active", "health_status": "healthy"}
    )
    with pytest.raises(RuntimeError, match="Failed to upsert worker messaging grant"):
        await manager.put_worker_messaging_grant(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            provider="whatsapp",
            chat_id="chat-1",
            allow_read=True,
            allow_send=False,
            ttl_seconds=60,
        )

    with pytest.raises(ValueError, match="Worker messaging grant not found"):
        await manager.revoke_worker_messaging_grant(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            grant_id="55555555-5555-5555-5555-555555555555",
        )

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "grant_id": "55555555-5555-5555-5555-555555555555",
                "revoked_at": datetime.now(UTC),
            },
            {
                "grant_id": "55555555-5555-5555-5555-555555555555",
                "revoked_at": None,
            },
            None,
        ]
    )
    already_revoked = await manager.revoke_worker_messaging_grant(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        grant_id="55555555-5555-5555-5555-555555555555",
    )
    assert already_revoked["idempotent"] is True

    with pytest.raises(RuntimeError, match="Failed to revoke worker messaging grant"):
        await manager.revoke_worker_messaging_grant(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            grant_id="55555555-5555-5555-5555-555555555555",
        )


@pytest.mark.asyncio
async def test_submit_worker_job_result_success_failure_and_idempotent() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._apply_worker_job_health_signal = AsyncMock(  # type: ignore[method-assign]
        return_value={"node_id": "node-1", "health_score": 95}
    )
    manager.complete_execution_step = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "plan": _execution_plan_row(status="completed"),
            "step": _execution_step_row(status="completed"),
            "has_more": True,
            "next_run_at": datetime.now(UTC),
        }
    )
    manager.fail_execution_step = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "plan": _execution_plan_row(status="running"),
            "step": _execution_step_row(status="failed"),
            "retry_scheduled": True,
            "next_run_at": datetime.now(UTC),
            "failure_category": "transient",
        }
    )
    manager.schedule_execution_continuation = AsyncMock(return_value="q-1")  # type: ignore[method-assign]

    conn.fetchrow.side_effect = [
        {
            "job_id": "44444444-4444-4444-4444-444444444444",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "step_id": "22222222-2222-2222-2222-222222222222",
            "retry_id": "33333333-3333-3333-3333-333333333333",
            "status": "running",
            "claimed_by_node_id": "node-1",
        },
        _worker_job_row(status="succeeded", claimed_by_node_id="node-1"),
        {
            "job_id": "44444444-4444-4444-4444-444444444444",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "step_id": "22222222-2222-2222-2222-222222222222",
            "retry_id": "33333333-3333-3333-3333-333333333333",
            "status": "running",
            "claimed_by_node_id": "node-1",
        },
        _worker_job_row(status="failed", claimed_by_node_id="node-1"),
        {
            "job_id": "44444444-4444-4444-4444-444444444444",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "step_id": "22222222-2222-2222-2222-222222222222",
            "retry_id": "33333333-3333-3333-3333-333333333333",
            "status": "succeeded",
            "claimed_by_node_id": "node-1",
        },
    ]

    success = await manager.submit_worker_job_result(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        job_id="44444444-4444-4444-4444-444444444444",
        completion_status="succeeded",
        output={"message": "ok"},
    )
    assert success["status"] == "succeeded"
    manager.complete_execution_step.assert_awaited_once()

    failure = await manager.submit_worker_job_result(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        job_id="44444444-4444-4444-4444-444444444444",
        completion_status="failed",
        error={"message": "dependency unavailable"},
    )
    assert failure["status"] == "failed"
    manager.fail_execution_step.assert_awaited_once()
    assert manager.schedule_execution_continuation.await_count == 2

    idempotent = await manager.submit_worker_job_result(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        job_id="44444444-4444-4444-4444-444444444444",
        completion_status="succeeded",
    )
    assert idempotent["idempotent"] is True


@pytest.mark.asyncio
async def test_submit_worker_job_result_skips_scheduling_without_next_run() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager._apply_worker_job_health_signal = AsyncMock(  # type: ignore[method-assign]
        return_value={"node_id": "node-1", "health_score": 90}
    )
    manager.complete_execution_step = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "plan": _execution_plan_row(status="running"),
            "step": _execution_step_row(status="completed"),
            "has_more": True,
            "next_run_at": None,
        }
    )
    manager.fail_execution_step = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "plan": _execution_plan_row(status="running"),
            "step": _execution_step_row(status="failed"),
            "retry_scheduled": True,
            "next_run_at": None,
            "failure_category": "transient",
        }
    )
    manager.schedule_execution_continuation = AsyncMock(return_value="q-1")  # type: ignore[method-assign]

    conn.fetchrow.side_effect = [
        {
            "job_id": "44444444-4444-4444-4444-444444444444",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "step_id": "22222222-2222-2222-2222-222222222222",
            "retry_id": "33333333-3333-3333-3333-333333333333",
            "status": "running",
            "claimed_by_node_id": "node-1",
        },
        _worker_job_row(status="succeeded", claimed_by_node_id="node-1"),
        {
            "job_id": "44444444-4444-4444-4444-444444444444",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "step_id": "22222222-2222-2222-2222-222222222222",
            "retry_id": "33333333-3333-3333-3333-333333333333",
            "status": "running",
            "claimed_by_node_id": "node-1",
        },
        _worker_job_row(status="failed", claimed_by_node_id="node-1"),
    ]

    success = await manager.submit_worker_job_result(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        job_id="44444444-4444-4444-4444-444444444444",
        completion_status="succeeded",
        output={"message": "ok"},
    )
    failure = await manager.submit_worker_job_result(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-1",
        job_id="44444444-4444-4444-4444-444444444444",
        completion_status="failed",
        error={"message": "dependency unavailable"},
    )

    assert success["status"] == "succeeded"
    assert failure["status"] == "failed"
    manager.schedule_execution_continuation.assert_not_awaited()


@pytest.mark.asyncio
async def test_submit_worker_job_result_error_paths() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.submit_worker_job_result(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="",
            job_id="44444444-4444-4444-4444-444444444444",
            completion_status="succeeded",
        )
    with pytest.raises(ValueError, match="Missing job_id"):
        await manager.submit_worker_job_result(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            job_id="",
            completion_status="succeeded",
        )

    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    running_lookup = {
        "job_id": "44444444-4444-4444-4444-444444444444",
        "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "plan_id": "11111111-1111-1111-1111-111111111111",
        "step_id": "22222222-2222-2222-2222-222222222222",
        "retry_id": "33333333-3333-3333-3333-333333333333",
        "status": "running",
        "claimed_by_node_id": "node-1",
    }

    conn.fetchrow.side_effect = [None]
    with pytest.raises(ValueError, match="Worker job not found"):
        await manager.submit_worker_job_result(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            job_id="44444444-4444-4444-4444-444444444444",
            completion_status="failed",
        )

    conn.fetchrow.side_effect = [{**running_lookup, "status": "queued"}]
    with pytest.raises(ValueError, match="Worker job is not running"):
        await manager.submit_worker_job_result(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            job_id="44444444-4444-4444-4444-444444444444",
            completion_status="failed",
        )

    conn.fetchrow.side_effect = [{**running_lookup, "claimed_by_node_id": "node-2"}]
    with pytest.raises(ValueError, match="does not own this job lease"):
        await manager.submit_worker_job_result(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            job_id="44444444-4444-4444-4444-444444444444",
            completion_status="failed",
        )

    conn.fetchrow.side_effect = [running_lookup, None]
    with pytest.raises(RuntimeError, match="Failed to update worker job result"):
        await manager.submit_worker_job_result(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            node_id="node-1",
            job_id="44444444-4444-4444-4444-444444444444",
            completion_status="failed",
        )


@pytest.mark.asyncio
async def test_submit_worker_job_result_duplicate_submission_is_idempotent_across_nodes() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    conn.fetchrow.side_effect = [
        {
            "job_id": "44444444-4444-4444-4444-444444444444",
            "tenant_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "plan_id": "11111111-1111-1111-1111-111111111111",
            "step_id": "22222222-2222-2222-2222-222222222222",
            "retry_id": "33333333-3333-3333-3333-333333333333",
            "status": "succeeded",
            "claimed_by_node_id": "node-1",
        }
    ]

    duplicate = await manager.submit_worker_job_result(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        node_id="node-2",
        job_id="44444444-4444-4444-4444-444444444444",
        completion_status="failed",
        error={"message": "late duplicate from peer node"},
    )
    assert duplicate["accepted"] is True
    assert duplicate["idempotent"] is True
    assert duplicate["status"] == "succeeded"


@pytest.mark.asyncio
async def test_apply_worker_job_health_signal_updates_and_auto_quarantine() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    tenant_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    now = datetime.now(UTC)

    conn.fetchrow.side_effect = [None]
    assert (
        await manager._apply_worker_job_health_signal(
            conn=conn,
            tenant_id=tenant_id,
            node_id="node-1",
            succeeded=True,
            actor_sub="worker:node-1",
        )
        is None
    )

    conn.fetchrow.side_effect = [
        {
            "node_id": "node-1",
            "status": "active",
            "health_status": "degraded",
            "health_score": 70,
            "consecutive_job_failures": 2,
            "metadata": {},
        },
        {
            "node_id": "node-1",
            "tenant_id": tenant_id,
            "node_name": "laptop",
            "status": "active",
            "health_status": "healthy",
            "health_score": 75,
            "consecutive_job_failures": 0,
            "metadata": {},
            "last_heartbeat_at": now,
            "created_by": "seed",
            "updated_by": "worker:node-1",
            "created_at": now,
            "updated_at": now,
        },
    ]
    success_snapshot = await manager._apply_worker_job_health_signal(
        conn=conn,
        tenant_id=tenant_id,
        node_id="node-1",
        succeeded=True,
        actor_sub="worker:node-1",
    )
    assert success_snapshot is not None
    assert success_snapshot["status"] == "active"
    assert success_snapshot["health_status"] == "healthy"
    assert success_snapshot["auto_quarantined"] is False

    manager._settings_cache[(tenant_id, "security", "worker_auto_quarantine_enabled")] = True
    manager._settings_cache[(tenant_id, "security", "worker_auto_quarantine_score_threshold")] = 50
    manager._settings_cache[
        (tenant_id, "security", "worker_auto_quarantine_consecutive_failures")
    ] = 3
    conn.execute = AsyncMock(return_value="UPDATE 1")
    conn.fetchrow.side_effect = [
        {
            "node_id": "node-1",
            "status": "active",
            "health_status": "healthy",
            "health_score": 65,
            "consecutive_job_failures": 2,
            "metadata": {},
        },
        {
            "node_id": "node-1",
            "tenant_id": tenant_id,
            "node_name": "laptop",
            "status": "quarantined",
            "health_status": "degraded",
            "health_score": 50,
            "consecutive_job_failures": 3,
            "metadata": {"auto_quarantine": {"reason": "consecutive_failures_threshold"}},
            "last_heartbeat_at": now,
            "created_by": "seed",
            "updated_by": "worker:node-1",
            "created_at": now,
            "updated_at": now,
        },
    ]
    failure_snapshot = await manager._apply_worker_job_health_signal(
        conn=conn,
        tenant_id=tenant_id,
        node_id="node-1",
        succeeded=False,
        actor_sub="worker:node-1",
    )
    assert failure_snapshot is not None
    assert failure_snapshot["status"] == "quarantined"
    assert failure_snapshot["auto_quarantined"] is True
    conn.execute.assert_awaited_once()

    conn.execute.reset_mock()
    conn.fetchrow.side_effect = [
        {
            "node_id": "node-1",
            "status": "active",
            "health_status": "healthy",
            "health_score": 40,
            "consecutive_job_failures": 0,
            "metadata": {},
        },
        {
            "node_id": "node-1",
            "tenant_id": tenant_id,
            "node_name": "laptop",
            "status": "quarantined",
            "health_status": "degraded",
            "health_score": 40,
            "consecutive_job_failures": 0,
            "metadata": {"auto_quarantine": {"reason": "health_score_threshold"}},
            "last_heartbeat_at": now,
            "created_by": "seed",
            "updated_by": "worker:node-1",
            "created_at": now,
            "updated_at": now,
        },
    ]
    healthy_but_quarantined = await manager._apply_worker_job_health_signal(
        conn=conn,
        tenant_id=tenant_id,
        node_id="node-1",
        succeeded=True,
        actor_sub="worker:node-1",
    )
    assert healthy_but_quarantined is not None
    assert healthy_but_quarantined["status"] == "quarantined"
    assert healthy_but_quarantined["health_status"] == "degraded"
    conn.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_heartbeat_worker_node_respects_health_score_hint_and_quarantine() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    tenant_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    now = datetime.now(UTC)

    manager._settings_cache[(tenant_id, "security", "worker_auto_quarantine_enabled")] = True
    manager._settings_cache[(tenant_id, "security", "worker_auto_quarantine_score_threshold")] = 50
    manager._settings_cache[
        (tenant_id, "security", "worker_auto_quarantine_consecutive_failures")
    ] = 3
    conn.fetchrow.side_effect = [
        {
            "status": "registered",
            "health_status": "healthy",
            "health_score": 80,
            "consecutive_job_failures": 0,
            "metadata": {},
        },
        {
            "node_id": "node-1",
            "tenant_id": tenant_id,
            "node_name": "laptop",
            "status": "quarantined",
            "health_status": "degraded",
            "health_score": 40,
            "consecutive_job_failures": 0,
            "metadata": {"auto_quarantine": {"reason": "health_score_threshold"}},
            "last_heartbeat_at": now,
            "created_by": "seed",
            "updated_by": "worker-heartbeat",
            "created_at": now,
            "updated_at": now,
        },
    ]

    heartbeat = await manager.heartbeat_worker_node(
        tenant_id=tenant_id,
        node_id="node-1",
        health_status="healthy",
        metadata={"health_score": 40},
        actor_sub="worker-heartbeat",
    )
    assert heartbeat["status"] == "quarantined"
    assert heartbeat["health_status"] == "degraded"
    assert heartbeat["health_score"] == 40


@pytest.mark.asyncio
async def test_record_execution_artifact_validation_and_failure_paths() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            None,
            {"artifact_id": "a1", "artifact_type": "step_prompt"},
            {"artifact_id": "a2", "artifact_type": "step_prompt", "execution_mode": "test"},
        ]
    )

    with pytest.raises(ValueError, match="Missing artifact_type"):
        await manager.record_execution_artifact(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            artifact_type="",
        )

    with pytest.raises(RuntimeError, match="Failed to record execution artifact"):
        await manager.record_execution_artifact(
            tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            plan_id="11111111-1111-1111-1111-111111111111",
            artifact_type="step_prompt",
        )

    recorded = await manager.record_execution_artifact(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        artifact_type="STEP_PROMPT",
        artifact_json={"k": "v"},
    )
    assert recorded["artifact_id"] == "a1"

    recorded_test = await manager.record_execution_artifact(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        plan_id="11111111-1111-1111-1111-111111111111",
        artifact_type="STEP_PROMPT",
        execution_mode="TEST",
    )
    assert recorded_test["execution_mode"] == "test"


@pytest.mark.asyncio
async def test_record_admin_event_wraps_audit_write() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    await manager.record_admin_event(
        tenant_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        action="tenant_automerge_execute",
        actor=_actor(),
        details={"status": "merged"},
    )
    manager._write_audit.assert_awaited_once()
