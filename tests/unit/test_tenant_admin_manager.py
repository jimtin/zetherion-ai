"""Unit tests for tenant admin manager control-plane logic."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.admin.tenant_admin_manager import (
    AdminActorContext,
    TenantAdminManager,
    admin_actor_from_payload,
)
from zetherion_ai.security.encryption import FieldEncryptor


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
