"""Unit tests for PostgreSQL-backed RBAC UserManager.

Every test mocks asyncpg thoroughly so no real database connection is needed.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from zetherion_ai.discord.user_manager import (
    _SCHEMA_SQL,
    ROLE_HIERARCHY,
    VALID_ROLES,
    UserManager,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

DSN = "postgresql://test:test@localhost:5432/testdb"


def _make_mock_pool():
    """Build a mock asyncpg pool with an acquirable connection.

    Returns (pool, conn) where ``pool.acquire()`` used as an async
    context manager yields ``conn``, and ``conn.transaction()`` also
    acts as an async context manager.

    asyncpg's ``pool.acquire()`` returns an async context manager
    *synchronously* (not a coroutine), so ``acquire`` must be a
    regular ``MagicMock`` whose return value supports ``__aenter__``
    / ``__aexit__``.  Same applies to ``conn.transaction()``.
    """
    pool = AsyncMock()
    conn = AsyncMock()

    # pool.acquire() returns an async-CM synchronously.
    # Override the auto-generated AsyncMock attribute with a plain
    # MagicMock so that pool.acquire() does NOT return a coroutine.
    acq_cm = AsyncMock()
    acq_cm.__aenter__.return_value = conn
    pool.acquire = MagicMock(return_value=acq_cm)

    # conn.transaction() returns an async-CM synchronously.
    tx_cm = AsyncMock()
    tx_cm.__aenter__.return_value = conn
    conn.transaction = MagicMock(return_value=tx_cm)

    return pool, conn


def _record(mapping: dict) -> MagicMock:
    """Create a mock asyncpg.Record that supports ``dict(record)``."""
    rec = MagicMock()
    rec.keys.return_value = list(mapping.keys())
    rec.__iter__ = lambda self: iter(mapping.values())
    rec.__getitem__ = lambda self, k: mapping[k]
    # dict(record) uses keys() + __getitem__
    rec.items.return_value = mapping.items()
    # Make dict() work correctly
    rec.__class__ = dict  # Make isinstance check pass is not needed
    # The simplest way: make the mock behave like a mapping
    rec.keys.return_value = mapping.keys()
    rec.values.return_value = mapping.values()
    rec.items.return_value = mapping.items()
    # dict(record) calls __iter__ on keys and then __getitem__
    return rec


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestInit:
    """Tests for UserManager.__init__."""

    def test_stores_dsn(self):
        """__init__ stores the DSN string."""
        mgr = UserManager(DSN)
        assert mgr._dsn == DSN

    def test_pool_initially_none(self):
        """Pool is None before initialize() is called."""
        mgr = UserManager(DSN)
        assert mgr._pool is None


class TestInitialize:
    """Tests for UserManager.initialize."""

    @pytest.mark.asyncio
    async def test_creates_pool_and_bootstraps(self):
        """initialize() creates pool, ensures schema, and runs bootstrap."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()

        with (
            patch(
                "zetherion_ai.discord.user_manager.asyncpg.create_pool", new_callable=AsyncMock
            ) as mock_create,
            patch.object(mgr, "_ensure_schema", new_callable=AsyncMock) as mock_schema,
            patch.object(mgr, "_bootstrap", new_callable=AsyncMock) as mock_boot,
        ):
            mock_create.return_value = mock_pool
            await mgr.initialize()

            mock_create.assert_awaited_once_with(dsn=DSN)
            assert mgr._pool is mock_pool
            mock_schema.assert_awaited_once()
            mock_boot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_raises_on_connection_failure(self):
        """initialize() re-raises when pool creation fails."""
        mgr = UserManager(DSN)

        with patch(
            "zetherion_ai.discord.user_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            side_effect=OSError("connection refused"),
        ):
            with pytest.raises(OSError, match="connection refused"):
                await mgr.initialize()

    @pytest.mark.asyncio
    async def test_raises_on_postgres_error(self):
        """initialize() re-raises asyncpg.PostgresError."""
        mgr = UserManager(DSN)

        with patch(
            "zetherion_ai.discord.user_manager.asyncpg.create_pool",
            new_callable=AsyncMock,
            side_effect=asyncpg.PostgresError("auth failed"),
        ):
            with pytest.raises(asyncpg.PostgresError):
                await mgr.initialize()


class TestClose:
    """Tests for UserManager.close."""

    @pytest.mark.asyncio
    async def test_closes_pool_and_sets_none(self):
        """close() calls pool.close() and sets _pool to None."""
        mgr = UserManager(DSN)
        mock_pool = AsyncMock()
        mgr._pool = mock_pool

        await mgr.close()

        mock_pool.close.assert_awaited_once()
        assert mgr._pool is None

    @pytest.mark.asyncio
    async def test_close_when_already_none_is_noop(self):
        """close() when pool is already None does nothing."""
        mgr = UserManager(DSN)
        mgr._pool = None

        # Should not raise
        await mgr.close()
        assert mgr._pool is None


class TestIsAllowed:
    """Tests for UserManager.is_allowed."""

    @pytest.mark.asyncio
    async def test_returns_true_when_user_exists(self):
        """is_allowed returns True when fetchval returns a value."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetchval.return_value = 1

        result = await mgr.is_allowed(12345)

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_user_missing(self):
        """is_allowed returns False when fetchval returns None."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetchval.return_value = None

        result = await mgr.is_allowed(99999)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_postgres_error(self):
        """is_allowed returns False when a PostgresError occurs."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetchval.side_effect = asyncpg.PostgresError("db down")

        result = await mgr.is_allowed(12345)

        assert result is False


class TestGetRole:
    """Tests for UserManager.get_role."""

    @pytest.mark.asyncio
    async def test_returns_role_string(self):
        """get_role returns the role when user exists."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetchval.return_value = "admin"

        result = await mgr.get_role(12345)

        assert result == "admin"

    @pytest.mark.asyncio
    async def test_returns_none_when_user_missing(self):
        """get_role returns None when user is not found."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetchval.return_value = None

        result = await mgr.get_role(99999)

        assert result is None

    @pytest.mark.asyncio
    async def test_returns_none_on_postgres_error(self):
        """get_role returns None when a PostgresError occurs."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetchval.side_effect = asyncpg.PostgresError("timeout")

        result = await mgr.get_role(12345)

        assert result is None


class TestListUsers:
    """Tests for UserManager.list_users."""

    @pytest.mark.asyncio
    async def test_returns_all_users_no_filter(self):
        """list_users with no filter returns all users."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        row1 = {"discord_user_id": 1, "role": "owner", "added_by": 1}
        row2 = {"discord_user_id": 2, "role": "user", "added_by": 1}
        mock_conn.fetch.return_value = [row1, row2]

        result = await mgr.list_users()

        assert len(result) == 2
        assert result[0] == row1
        assert result[1] == row2

    @pytest.mark.asyncio
    async def test_returns_filtered_users_with_valid_role(self):
        """list_users with a valid role_filter passes it to the query."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        row = {"discord_user_id": 2, "role": "admin", "added_by": 1}
        mock_conn.fetch.return_value = [row]

        result = await mgr.list_users(role_filter="admin")

        assert len(result) == 1
        assert result[0] == row
        # Verify the filter was passed to the query
        call_args = mock_conn.fetch.call_args
        assert "role = $1" in call_args[0][0]
        assert call_args[0][1] == "admin"

    @pytest.mark.asyncio
    async def test_invalid_role_filter_returns_empty(self):
        """list_users with an invalid role_filter returns []."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        result = await mgr.list_users(role_filter="superadmin")

        assert result == []
        # The pool should NOT have been queried
        mock_conn.fetch.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_returns_empty_on_postgres_error(self):
        """list_users returns [] when a PostgresError occurs."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetch.side_effect = asyncpg.PostgresError("query failed")

        result = await mgr.list_users()

        assert result == []


class TestGetAuditLog:
    """Tests for UserManager.get_audit_log."""

    @pytest.mark.asyncio
    async def test_returns_entries(self):
        """get_audit_log returns audit entries as dicts."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        entry = {"id": 1, "action": "add_user", "target_user_id": 100, "performed_by": 1}
        mock_conn.fetch.return_value = [entry]

        result = await mgr.get_audit_log(limit=10)

        assert len(result) == 1
        assert result[0] == entry
        # Verify the limit is passed
        call_args = mock_conn.fetch.call_args
        assert call_args[0][1] == 10

    @pytest.mark.asyncio
    async def test_default_limit(self):
        """get_audit_log uses default limit of 50."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetch.return_value = []

        await mgr.get_audit_log()

        call_args = mock_conn.fetch.call_args
        assert call_args[0][1] == 50

    @pytest.mark.asyncio
    async def test_returns_empty_on_postgres_error(self):
        """get_audit_log returns [] when a PostgresError occurs."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetch.side_effect = asyncpg.PostgresError("error")

        result = await mgr.get_audit_log()

        assert result == []


class TestAddUser:
    """Tests for UserManager.add_user."""

    @pytest.mark.asyncio
    async def test_succeeds_with_valid_role_and_privilege(self):
        """add_user succeeds when caller has strictly higher privilege."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        # get_role for the caller (owner, level 4) -- adding a "user" (level 2)
        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value="owner"):
            result = await mgr.add_user(user_id=200, role="user", added_by=100)

        assert result is True
        # Two execute calls: INSERT user + INSERT audit_log
        assert mock_conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_invalid_role_returns_false(self):
        """add_user returns False for an invalid role."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        result = await mgr.add_user(user_id=200, role="superuser", added_by=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_caller_returns_false(self):
        """add_user returns False when the caller is not in the DB."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value=None):
            result = await mgr.add_user(user_id=200, role="user", added_by=999)

        assert result is False

    @pytest.mark.asyncio
    async def test_insufficient_privilege_returns_false(self):
        """add_user returns False when admin tries to add another admin (equal level)."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        # Caller is admin (level 3), trying to add admin (level 3) -- not strictly higher
        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value="admin"):
            result = await mgr.add_user(user_id=200, role="admin", added_by=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_admin_can_add_user(self):
        """add_user succeeds when admin adds a user (level 3 > level 2)."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value="admin"):
            result = await mgr.add_user(user_id=200, role="user", added_by=100)

        assert result is True

    @pytest.mark.asyncio
    async def test_admin_cannot_add_owner(self):
        """add_user returns False when admin tries to add owner (level 3 < level 4)."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value="admin"):
            result = await mgr.add_user(user_id=200, role="owner", added_by=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_postgres_error(self):
        """add_user returns False when a PostgresError occurs during insert."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.execute.side_effect = asyncpg.PostgresError("insert failed")

        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value="owner"):
            result = await mgr.add_user(user_id=200, role="user", added_by=100)

        assert result is False


class TestRemoveUser:
    """Tests for UserManager.remove_user."""

    @pytest.mark.asyncio
    async def test_succeeds_with_sufficient_privilege(self):
        """remove_user succeeds when caller has strictly higher privilege."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        # First call: get_role(user_id) -> "user", second call: get_role(removed_by) -> "owner"
        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["user", "owner"]):
            result = await mgr.remove_user(user_id=200, removed_by=100)

        assert result is True
        # DELETE user + INSERT audit_log
        assert mock_conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_refuses_to_remove_owners(self):
        """remove_user returns False when target is an owner."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value="owner"):
            result = await mgr.remove_user(user_id=100, removed_by=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_nonexistent_user_returns_false(self):
        """remove_user returns False when the target user does not exist."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value=None):
            result = await mgr.remove_user(user_id=999, removed_by=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_unknown_caller_returns_false(self):
        """remove_user returns False when the caller is not in the DB."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        # First call: target exists with role "user"; second: caller not found
        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["user", None]):
            result = await mgr.remove_user(user_id=200, removed_by=999)

        assert result is False

    @pytest.mark.asyncio
    async def test_insufficient_privilege_returns_false(self):
        """remove_user returns False when caller's level is not strictly higher."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        # Both are admin (level 3) -- not strictly higher
        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["admin", "admin"]):
            result = await mgr.remove_user(user_id=200, removed_by=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_user_cannot_remove_admin(self):
        """remove_user returns False when a user tries to remove an admin."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        # Target is admin (3), caller is user (2) -- 2 <= 3
        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["admin", "user"]):
            result = await mgr.remove_user(user_id=200, removed_by=300)

        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_on_postgres_error(self):
        """remove_user returns False when a PostgresError occurs during delete."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.execute.side_effect = asyncpg.PostgresError("delete failed")

        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["user", "owner"]):
            result = await mgr.remove_user(user_id=200, removed_by=100)

        assert result is False


class TestSetRole:
    """Tests for UserManager.set_role."""

    @pytest.mark.asyncio
    async def test_succeeds_with_sufficient_privilege(self):
        """set_role succeeds when caller has strictly higher privilege than both roles."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        # get_role(user_id) -> "user" (old), get_role(changed_by) -> "owner" (caller)
        # Promoting user (2) to admin (3), caller is owner (4): 4 > 2 and 4 > 3
        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["user", "owner"]):
            result = await mgr.set_role(user_id=200, new_role="admin", changed_by=100)

        assert result is True
        # UPDATE user + INSERT audit_log
        assert mock_conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_invalid_role_returns_false(self):
        """set_role returns False for an invalid new_role."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        result = await mgr.set_role(user_id=200, new_role="superuser", changed_by=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_nonexistent_user_returns_false(self):
        """set_role returns False when the target user does not exist."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value=None):
            result = await mgr.set_role(user_id=999, new_role="admin", changed_by=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_noop_when_role_unchanged(self):
        """set_role returns True without DB write when old_role == new_role."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        # get_role(user_id) returns "admin" -- same as new_role
        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value="admin"):
            result = await mgr.set_role(user_id=200, new_role="admin", changed_by=100)

        assert result is True
        # No transaction should have been started
        mock_pool.acquire.assert_not_called()

    @pytest.mark.asyncio
    async def test_unknown_caller_returns_false(self):
        """set_role returns False when the caller is not in the DB."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        # get_role(user_id) -> "user", get_role(changed_by) -> None
        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["user", None]):
            result = await mgr.set_role(user_id=200, new_role="admin", changed_by=999)

        assert result is False

    @pytest.mark.asyncio
    async def test_insufficient_privilege_old_role(self):
        """set_role returns False when caller level <= old_role level."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        # Demoting admin (3) to user (2), caller is admin (3): 3 <= 3 for old_role check
        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["admin", "admin"]):
            result = await mgr.set_role(user_id=200, new_role="user", changed_by=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_insufficient_privilege_new_role(self):
        """set_role returns False when caller level <= new_role level."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        # Promoting restricted (1) to admin (3), caller is admin (3): 3 <= 3 for new_role
        with patch.object(
            mgr, "get_role", new_callable=AsyncMock, side_effect=["restricted", "admin"]
        ):
            result = await mgr.set_role(user_id=200, new_role="admin", changed_by=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_owner_can_promote_to_admin(self):
        """set_role succeeds: owner (4) promoting user (2) to admin (3)."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["user", "owner"]):
            result = await mgr.set_role(user_id=200, new_role="admin", changed_by=100)

        assert result is True

    @pytest.mark.asyncio
    async def test_owner_can_demote_admin_to_restricted(self):
        """set_role succeeds: owner (4) demoting admin (3) to restricted (1)."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["admin", "owner"]):
            result = await mgr.set_role(user_id=200, new_role="restricted", changed_by=100)

        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_on_postgres_error(self):
        """set_role returns False when a PostgresError occurs during update."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.execute.side_effect = asyncpg.PostgresError("update failed")

        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["user", "owner"]):
            result = await mgr.set_role(user_id=200, new_role="admin", changed_by=100)

        assert result is False


class TestEnsureSchema:
    """Tests for UserManager._ensure_schema."""

    @pytest.mark.asyncio
    async def test_executes_schema_sql(self):
        """_ensure_schema executes the DDL SQL."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        await mgr._ensure_schema()

        mock_conn.execute.assert_awaited_once_with(_SCHEMA_SQL)

    @pytest.mark.asyncio
    async def test_raises_on_error(self):
        """_ensure_schema re-raises PostgresError."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.execute.side_effect = asyncpg.PostgresError("syntax error")

        with pytest.raises(asyncpg.PostgresError):
            await mgr._ensure_schema()


class TestBootstrap:
    """Tests for UserManager._bootstrap."""

    @pytest.mark.asyncio
    async def test_seeds_owner_and_users_when_empty(self):
        """_bootstrap inserts owner and seed users when table is empty."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        # fetchval returns 0 for count(*) -- table is empty
        mock_conn.fetchval.return_value = 0

        mock_settings = MagicMock()
        mock_settings.owner_user_id = 111
        mock_settings.allowed_user_ids = [111, 222, 333]

        with patch(
            "zetherion_ai.discord.user_manager.get_settings",
            return_value=mock_settings,
        ):
            await mgr._bootstrap()

        # _execute is called via pool.acquire context manager
        # Owner insert + 2 seed user inserts (222 and 333, not 111 since it matches owner)
        assert mock_conn.execute.await_count == 3

    @pytest.mark.asyncio
    async def test_skips_when_table_has_data(self):
        """_bootstrap skips seeding when the users table already has rows."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        # Table already has users
        mock_conn.fetchval.return_value = 5

        await mgr._bootstrap()

        # Only the count query should have been issued (via fetchval);
        # no execute calls for inserts
        mock_conn.execute.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_handles_no_owner_user_id(self):
        """_bootstrap handles owner_user_id being None."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        mock_conn.fetchval.return_value = 0

        mock_settings = MagicMock()
        mock_settings.owner_user_id = None
        mock_settings.allowed_user_ids = [222, 333]

        with patch(
            "zetherion_ai.discord.user_manager.get_settings",
            return_value=mock_settings,
        ):
            await mgr._bootstrap()

        # No owner insert, but 2 seed user inserts
        # added_by will be 0 since owner_id is None (owner_id or 0)
        assert mock_conn.execute.await_count == 2

    @pytest.mark.asyncio
    async def test_handles_empty_seed_ids(self):
        """_bootstrap with owner but no seed users only creates the owner."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        mock_conn.fetchval.return_value = 0

        mock_settings = MagicMock()
        mock_settings.owner_user_id = 111
        mock_settings.allowed_user_ids = []

        with patch(
            "zetherion_ai.discord.user_manager.get_settings",
            return_value=mock_settings,
        ):
            await mgr._bootstrap()

        # Only owner insert
        assert mock_conn.execute.await_count == 1

    @pytest.mark.asyncio
    async def test_raises_on_postgres_error(self):
        """_bootstrap re-raises PostgresError from the count query."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        mock_conn.fetchval.side_effect = asyncpg.PostgresError("table missing")

        with pytest.raises(asyncpg.PostgresError):
            await mgr._bootstrap()


class TestRoleHierarchyConstants:
    """Tests for module-level role constants."""

    def test_valid_roles(self):
        """VALID_ROLES contains exactly the expected roles."""
        assert frozenset({"owner", "admin", "user", "restricted"}) == VALID_ROLES

    def test_role_hierarchy_ordering(self):
        """Role hierarchy has correct ordering: owner > admin > user > restricted."""
        assert ROLE_HIERARCHY["owner"] > ROLE_HIERARCHY["admin"]
        assert ROLE_HIERARCHY["admin"] > ROLE_HIERARCHY["user"]
        assert ROLE_HIERARCHY["user"] > ROLE_HIERARCHY["restricted"]

    def test_role_hierarchy_values(self):
        """Role hierarchy values match expected integers."""
        assert ROLE_HIERARCHY["owner"] == 4
        assert ROLE_HIERARCHY["admin"] == 3
        assert ROLE_HIERARCHY["user"] == 2
        assert ROLE_HIERARCHY["restricted"] == 1

    def test_valid_roles_matches_hierarchy_keys(self):
        """VALID_ROLES and ROLE_HIERARCHY have the same keys."""
        assert frozenset(ROLE_HIERARCHY.keys()) == VALID_ROLES


class TestConvenienceWrappers:
    """Tests for _fetchval, _fetch, and _execute pool wrappers."""

    @pytest.mark.asyncio
    async def test_fetchval_delegates_to_conn(self):
        """_fetchval acquires a connection and calls conn.fetchval."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetchval.return_value = 42

        result = await mgr._fetchval("SELECT 1")

        assert result == 42
        mock_conn.fetchval.assert_awaited_once_with("SELECT 1")

    @pytest.mark.asyncio
    async def test_fetchval_passes_args(self):
        """_fetchval forwards positional arguments."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetchval.return_value = "admin"

        await mgr._fetchval("SELECT role FROM users WHERE id = $1", 123)

        mock_conn.fetchval.assert_awaited_once_with("SELECT role FROM users WHERE id = $1", 123)

    @pytest.mark.asyncio
    async def test_fetch_delegates_to_conn(self):
        """_fetch acquires a connection and calls conn.fetch."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.fetch.return_value = [{"id": 1}]

        result = await mgr._fetch("SELECT * FROM users")

        assert result == [{"id": 1}]
        mock_conn.fetch.assert_awaited_once_with("SELECT * FROM users")

    @pytest.mark.asyncio
    async def test_execute_delegates_to_conn(self):
        """_execute acquires a connection and calls conn.execute."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool
        mock_conn.execute.return_value = "INSERT 0 1"

        result = await mgr._execute("INSERT INTO users VALUES ($1, $2)", 1, "owner")

        assert result == "INSERT 0 1"
        mock_conn.execute.assert_awaited_once_with("INSERT INTO users VALUES ($1, $2)", 1, "owner")


class TestAddUserEdgeCases:
    """Additional edge-case tests for add_user."""

    @pytest.mark.asyncio
    async def test_all_valid_roles_accepted(self):
        """add_user accepts every role in VALID_ROLES when caller is owner."""
        mgr = UserManager(DSN)

        for role in VALID_ROLES:
            mock_pool, mock_conn = _make_mock_pool()
            mgr._pool = mock_pool

            with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value="owner"):
                result = await mgr.add_user(user_id=200, role=role, added_by=100)

            # Owner (4) > all roles (max 4) -- but wait, owner adding "owner" is 4 <= 4
            if role == "owner":
                assert result is False, f"Owner should not be able to add role '{role}'"
            else:
                assert result is True, f"Owner should be able to add role '{role}'"

    @pytest.mark.asyncio
    async def test_user_cannot_add_restricted(self):
        """A user (level 2) cannot add restricted (level 1) -- 2 > 1, so it succeeds."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value="user"):
            result = await mgr.add_user(user_id=200, role="restricted", added_by=100)

        # user level 2 > restricted level 1, so this succeeds
        assert result is True

    @pytest.mark.asyncio
    async def test_restricted_cannot_add_anyone(self):
        """A restricted user (level 1) cannot add even a restricted user (1 <= 1)."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, return_value="restricted"):
            result = await mgr.add_user(user_id=200, role="restricted", added_by=100)

        assert result is False


class TestRemoveUserEdgeCases:
    """Additional edge-case tests for remove_user."""

    @pytest.mark.asyncio
    async def test_owner_can_remove_admin(self):
        """Owner (4) can remove an admin (3)."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["admin", "owner"]):
            result = await mgr.remove_user(user_id=200, removed_by=100)

        assert result is True

    @pytest.mark.asyncio
    async def test_admin_can_remove_user(self):
        """Admin (3) can remove a user (2)."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["user", "admin"]):
            result = await mgr.remove_user(user_id=200, removed_by=100)

        assert result is True

    @pytest.mark.asyncio
    async def test_admin_can_remove_restricted(self):
        """Admin (3) can remove a restricted user (1)."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(
            mgr, "get_role", new_callable=AsyncMock, side_effect=["restricted", "admin"]
        ):
            result = await mgr.remove_user(user_id=200, removed_by=100)

        assert result is True


class TestSetRoleEdgeCases:
    """Additional edge-case tests for set_role."""

    @pytest.mark.asyncio
    async def test_admin_can_change_user_to_restricted(self):
        """Admin (3) can demote user (2) to restricted (1): 3 > 2 and 3 > 1."""
        mgr = UserManager(DSN)
        mock_pool, mock_conn = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["user", "admin"]):
            result = await mgr.set_role(user_id=200, new_role="restricted", changed_by=100)

        assert result is True

    @pytest.mark.asyncio
    async def test_admin_cannot_promote_to_owner(self):
        """Admin (3) cannot promote user (2) to owner (4): 3 <= 4 for new_role."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(mgr, "get_role", new_callable=AsyncMock, side_effect=["user", "admin"]):
            result = await mgr.set_role(user_id=200, new_role="owner", changed_by=100)

        assert result is False

    @pytest.mark.asyncio
    async def test_user_cannot_change_restricted_to_admin(self):
        """User (2) cannot promote restricted (1) to admin (3): 2 <= 3 for new_role."""
        mgr = UserManager(DSN)
        mock_pool, _ = _make_mock_pool()
        mgr._pool = mock_pool

        with patch.object(
            mgr, "get_role", new_callable=AsyncMock, side_effect=["restricted", "user"]
        ):
            result = await mgr.set_role(user_id=200, new_role="admin", changed_by=300)

        assert result is False
