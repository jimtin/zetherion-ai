"""Unit tests for the PostgreSQL-backed SettingsManager."""

import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.settings_manager import SettingsManager

# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _make_mock_pool():
    """Create a mock asyncpg pool and connection.

    Returns a ``(pool, conn)`` tuple where *pool* is a ``MagicMock``
    that yields *conn* from ``async with pool.acquire()``, and *conn*
    supports ``async with conn.transaction()``.

    asyncpg's ``pool.acquire()`` returns a context-manager object
    directly (not a coroutine), so we use ``MagicMock`` for the pool
    and wire up the ``__aenter__`` / ``__aexit__`` protocol by hand.
    """
    pool = MagicMock()
    conn = AsyncMock()

    # pool.acquire() -> async context manager that yields conn
    acq_ctx = AsyncMock()
    acq_ctx.__aenter__.return_value = conn
    acq_ctx.__aexit__.return_value = False
    pool.acquire.return_value = acq_ctx

    # conn.transaction() returns an async context manager directly
    # (not a coroutine), so we use MagicMock with async dunder methods.
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=None)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    return pool, conn


def _make_row(namespace: str, key: str, value: str, data_type: str = "string") -> dict:
    """Build a dict that behaves like an asyncpg Record (dict-style access)."""
    return {
        "namespace": namespace,
        "key": key,
        "value": value,
        "data_type": data_type,
    }


# ------------------------------------------------------------------
# TestSettingsManagerInit
# ------------------------------------------------------------------


class TestSettingsManagerInit:
    """Tests for SettingsManager.__init__."""

    def test_init_sets_empty_cache_and_defaults(self):
        """__init__ creates an empty cache, cache_loaded=False, pool=None."""
        mgr = SettingsManager()

        assert mgr._cache == {}
        assert mgr._cache_loaded is False
        assert mgr._pool is None


# ------------------------------------------------------------------
# TestSettingsManagerGet
# ------------------------------------------------------------------


class TestSettingsManagerGet:
    """Tests for the synchronous SettingsManager.get method."""

    def test_get_returns_default_when_key_missing(self):
        """get() returns the caller-supplied default when the key is absent."""
        mgr = SettingsManager()
        assert mgr.get("models", "nonexistent", default="fallback") == "fallback"

    def test_get_returns_cached_value(self):
        """get() returns the cached value when the key exists."""
        mgr = SettingsManager()
        mgr._cache[("models", "provider")] = "anthropic"

        assert mgr.get("models", "provider") == "anthropic"

    def test_get_returns_none_as_default(self):
        """get() returns None when no explicit default is provided."""
        mgr = SettingsManager()
        assert mgr.get("models", "missing") is None


# ------------------------------------------------------------------
# TestSettingsManagerSet
# ------------------------------------------------------------------


class TestSettingsManagerSet:
    """Tests for the async SettingsManager.set method."""

    @pytest.mark.asyncio
    async def test_set_valid_namespace_persists_and_updates_cache(self):
        """set() with a valid namespace UPSERTs into DB and updates cache."""
        pool, conn = _make_mock_pool()
        mgr = SettingsManager()
        mgr._pool = pool

        await mgr.set("models", "default_provider", "openai", changed_by=1)

        # Two conn.execute calls: one UPSERT, one audit log insert
        assert conn.execute.call_count == 2

        # Cache should now contain the value
        assert mgr._cache[("models", "default_provider")] == "openai"

    @pytest.mark.asyncio
    async def test_set_invalid_namespace_raises_value_error(self):
        """set() with an invalid namespace raises ValueError immediately."""
        mgr = SettingsManager()

        with pytest.raises(ValueError, match="Invalid namespace"):
            await mgr.set("invalid_ns", "key", "value", changed_by=1)

    @pytest.mark.asyncio
    async def test_set_json_data_type_uses_json_dumps(self):
        """set() with data_type='json' serialises the value via json.dumps."""
        pool, conn = _make_mock_pool()
        mgr = SettingsManager()
        mgr._pool = pool

        payload = {"nested": [1, 2, 3]}
        await mgr.set("tuning", "params", payload, changed_by=1, data_type="json")

        # The first execute call is the UPSERT; args are
        # (sql, namespace, key, str_value, data_type, description).
        upsert_call = conn.execute.call_args_list[0]
        str_value = upsert_call.args[3]
        assert str_value == json.dumps(payload)

        # The cache should hold the deserialised object (via _coerce_value)
        assert mgr._cache[("tuning", "params")] == payload

    @pytest.mark.asyncio
    async def test_set_reraises_on_db_error(self):
        """set() logs and re-raises exceptions raised by the database."""
        pool, conn = _make_mock_pool()
        conn.execute.side_effect = RuntimeError("connection lost")
        mgr = SettingsManager()
        mgr._pool = pool

        with pytest.raises(RuntimeError, match="connection lost"):
            await mgr.set("models", "key", "val", changed_by=1)


# ------------------------------------------------------------------
# TestSettingsManagerRefresh
# ------------------------------------------------------------------


class TestSettingsManagerRefresh:
    """Tests for SettingsManager.refresh."""

    @pytest.mark.asyncio
    async def test_refresh_loads_rows_into_cache(self):
        """refresh() populates the in-memory cache from database rows."""
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = [
            _make_row("models", "provider", "anthropic"),
            _make_row("budgets", "daily_limit", "100", data_type="int"),
        ]
        mgr = SettingsManager()
        mgr._pool = pool

        await mgr.refresh()

        assert mgr._cache[("models", "provider")] == "anthropic"
        assert mgr._cache[("budgets", "daily_limit")] == 100

    @pytest.mark.asyncio
    async def test_refresh_handles_coercion_failure(self):
        """refresh() falls back to the raw string when coercion fails."""
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = [
            _make_row("models", "bad_int", "not_a_number", data_type="int"),
        ]
        mgr = SettingsManager()
        mgr._pool = pool

        await mgr.refresh()

        # Should fall back to the raw string value
        assert mgr._cache[("models", "bad_int")] == "not_a_number"

    @pytest.mark.asyncio
    async def test_refresh_sets_cache_loaded(self):
        """refresh() sets _cache_loaded to True upon success."""
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = []
        mgr = SettingsManager()
        mgr._pool = pool

        assert mgr._cache_loaded is False
        await mgr.refresh()
        assert mgr._cache_loaded is True

    @pytest.mark.asyncio
    async def test_refresh_reraises_on_db_error(self):
        """refresh() logs and re-raises exceptions from the database."""
        pool, conn = _make_mock_pool()
        conn.fetch.side_effect = RuntimeError("connection refused")
        mgr = SettingsManager()
        mgr._pool = pool

        with pytest.raises(RuntimeError, match="connection refused"):
            await mgr.refresh()


# ------------------------------------------------------------------
# TestSettingsManagerGetAll
# ------------------------------------------------------------------


class TestSettingsManagerGetAll:
    """Tests for SettingsManager.get_all."""

    @pytest.mark.asyncio
    async def test_get_all_without_namespace_returns_grouped(self):
        """get_all() without a namespace filter returns all settings grouped."""
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = [
            _make_row("models", "provider", "anthropic"),
            _make_row("models", "temperature", "0.7", data_type="float"),
            _make_row("budgets", "daily", "50", data_type="int"),
        ]
        mgr = SettingsManager()
        mgr._pool = pool

        result = await mgr.get_all()

        assert result == {
            "models": {"provider": "anthropic", "temperature": 0.7},
            "budgets": {"daily": 50},
        }

    @pytest.mark.asyncio
    async def test_get_all_with_namespace_filters(self):
        """get_all(namespace=...) only fetches settings for that namespace."""
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = [
            _make_row("models", "provider", "anthropic"),
        ]
        mgr = SettingsManager()
        mgr._pool = pool

        result = await mgr.get_all(namespace="models")

        assert result == {"models": {"provider": "anthropic"}}
        # Verify the filtered SQL query was used (second positional arg is the namespace)
        call_args = conn.fetch.call_args
        assert "models" in call_args.args

    @pytest.mark.asyncio
    async def test_get_all_handles_coercion_failure(self):
        """get_all() falls back to the raw string value on coercion failure."""
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = [
            _make_row("tuning", "bad_json", "not{json}", data_type="json"),
        ]
        mgr = SettingsManager()
        mgr._pool = pool

        result = await mgr.get_all()

        assert result == {"tuning": {"bad_json": "not{json}"}}

    @pytest.mark.asyncio
    async def test_get_all_reraises_on_error(self):
        """get_all() logs and re-raises exceptions from the database."""
        pool, conn = _make_mock_pool()
        conn.fetch.side_effect = RuntimeError("timeout")
        mgr = SettingsManager()
        mgr._pool = pool

        with pytest.raises(RuntimeError, match="timeout"):
            await mgr.get_all()


# ------------------------------------------------------------------
# TestSettingsManagerDelete
# ------------------------------------------------------------------


class TestSettingsManagerDelete:
    """Tests for SettingsManager.delete."""

    @pytest.mark.asyncio
    async def test_delete_returns_true_when_row_existed(self):
        """delete() returns True when the DB reports 'DELETE 1'."""
        pool, conn = _make_mock_pool()
        conn.execute.return_value = "DELETE 1"
        mgr = SettingsManager()
        mgr._pool = pool

        result = await mgr.delete("models", "provider", deleted_by=1)

        assert result is True

    @pytest.mark.asyncio
    async def test_delete_returns_false_when_row_not_found(self):
        """delete() returns False when the DB reports 'DELETE 0'."""
        pool, conn = _make_mock_pool()
        conn.execute.return_value = "DELETE 0"
        mgr = SettingsManager()
        mgr._pool = pool

        result = await mgr.delete("models", "nonexistent", deleted_by=1)

        assert result is False

    @pytest.mark.asyncio
    async def test_delete_removes_from_cache(self):
        """delete() pops the key from the in-memory cache."""
        pool, conn = _make_mock_pool()
        conn.execute.return_value = "DELETE 1"
        mgr = SettingsManager()
        mgr._pool = pool
        mgr._cache[("models", "provider")] = "anthropic"

        await mgr.delete("models", "provider", deleted_by=1)

        assert ("models", "provider") not in mgr._cache

    @pytest.mark.asyncio
    async def test_delete_reraises_on_error(self):
        """delete() logs and re-raises exceptions from the database."""
        pool, conn = _make_mock_pool()
        conn.execute.side_effect = RuntimeError("db down")
        mgr = SettingsManager()
        mgr._pool = pool

        with pytest.raises(RuntimeError, match="db down"):
            await mgr.delete("models", "provider", deleted_by=1)


# ------------------------------------------------------------------
# TestCoerceValue
# ------------------------------------------------------------------


class TestCoerceValue:
    """Tests for the static SettingsManager._coerce_value method."""

    def test_string_passthrough(self):
        """data_type='string' returns the raw value unchanged."""
        assert SettingsManager._coerce_value("hello", "string") == "hello"

    def test_int_conversion(self):
        """data_type='int' converts the string to int."""
        assert SettingsManager._coerce_value("42", "int") == 42

    def test_float_conversion(self):
        """data_type='float' converts the string to float."""
        assert SettingsManager._coerce_value("3.14", "float") == pytest.approx(3.14)

    @pytest.mark.parametrize("raw", ["true", "1", "yes"])
    def test_bool_true_values(self, raw):
        """data_type='bool' returns True for 'true', '1', 'yes'."""
        assert SettingsManager._coerce_value(raw, "bool") is True

    @pytest.mark.parametrize("raw", ["false", "0", "no"])
    def test_bool_false_values(self, raw):
        """data_type='bool' returns False for 'false', '0', 'no'."""
        assert SettingsManager._coerce_value(raw, "bool") is False

    def test_json_parsing(self):
        """data_type='json' deserialises a JSON string."""
        payload = {"key": [1, 2, 3]}
        raw = json.dumps(payload)
        assert SettingsManager._coerce_value(raw, "json") == payload

    def test_unknown_data_type_returns_raw_string(self):
        """An unrecognised data_type returns the raw string as a safe default."""
        assert SettingsManager._coerce_value("anything", "unknown_type") == "anything"


# ------------------------------------------------------------------
# TestInitialize
# ------------------------------------------------------------------


class TestInitialize:
    """Tests for SettingsManager.initialize."""

    @pytest.mark.asyncio
    async def test_initialize_stores_pool_and_calls_refresh(self):
        """initialize() stores the pool reference and calls refresh()."""
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = []
        mgr = SettingsManager()

        await mgr.initialize(pool)

        assert mgr._pool is pool
        # refresh() was called, which calls conn.fetch
        conn.fetch.assert_called_once()

    @pytest.mark.asyncio
    async def test_after_initialize_cache_is_loaded(self):
        """After initialize(), _cache_loaded is True."""
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = [
            _make_row("models", "provider", "openai"),
        ]
        mgr = SettingsManager()

        await mgr.initialize(pool)

        assert mgr._cache_loaded is True
        assert mgr._cache[("models", "provider")] == "openai"
