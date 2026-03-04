"""Unit tests for tenant admin manager control-plane logic."""

from __future__ import annotations

from datetime import UTC, datetime
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
