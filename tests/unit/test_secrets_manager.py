"""Tests for SecretsManager and SecretResolver."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from pydantic import SecretStr

from zetherion_ai.security.encryption import FieldEncryptor
from zetherion_ai.security.secret_resolver import SecretResolver
from zetherion_ai.security.secrets import SecretsManager

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_encryptor() -> FieldEncryptor:
    """Create a real FieldEncryptor with a deterministic key."""
    key = b"\x01" * 32  # 32-byte key for AES-256
    return FieldEncryptor(key=key)


def _make_pool(rows: list[dict] | None = None) -> MagicMock:
    """Build a mock asyncpg.Pool that returns *rows* on fetch.

    ``pool.acquire()`` returns an async context manager (not a coroutine),
    matching asyncpg's real behaviour.
    """
    pool = MagicMock()
    conn = AsyncMock()

    # pool.acquire() -> async context manager yielding conn
    ctx = MagicMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx

    # conn.transaction() -> async context manager
    tx_ctx = MagicMock()
    tx_ctx.__aenter__ = AsyncMock(return_value=conn)
    tx_ctx.__aexit__ = AsyncMock(return_value=False)
    conn.transaction = MagicMock(return_value=tx_ctx)

    # Default fetch returns empty or supplied rows
    if rows is not None:
        conn.fetch.return_value = rows
    else:
        conn.fetch.return_value = []

    conn.execute.return_value = "INSERT 0 1"

    return pool


# ---------------------------------------------------------------------------
# SecretsManager tests
# ---------------------------------------------------------------------------


class TestSecretsManagerInit:
    """Tests for SecretsManager initialization."""

    @pytest.mark.asyncio
    async def test_initialize_loads_cache(self):
        """Empty secrets table -> empty cache."""
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)
        pool = _make_pool(rows=[])
        await mgr.initialize(pool)

        assert mgr.get("anything") is None
        assert mgr.list_names() == []

    @pytest.mark.asyncio
    async def test_initialize_decrypts_rows(self):
        """Secrets loaded from DB are decrypted into cache."""
        encryptor = _make_encryptor()
        encrypted_val = encryptor.encrypt_value("my-secret-key")

        mgr = SecretsManager(encryptor=encryptor)
        pool = _make_pool(rows=[{"name": "api_key", "value": encrypted_val}])
        await mgr.initialize(pool)

        assert mgr.get("api_key") == "my-secret-key"
        assert mgr.list_names() == ["api_key"]

    @pytest.mark.asyncio
    async def test_initialize_skips_corrupt_rows(self):
        """Rows that fail decryption are skipped, not raised."""
        encryptor = _make_encryptor()

        mgr = SecretsManager(encryptor=encryptor)
        pool = _make_pool(rows=[{"name": "bad", "value": "not-valid-base64!!!"}])
        await mgr.initialize(pool)

        assert mgr.get("bad") is None
        assert mgr.list_names() == []


class TestSecretsManagerGet:
    """Tests for synchronous get."""

    def test_get_returns_default_when_empty(self):
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)
        assert mgr.get("missing") is None
        assert mgr.get("missing", "fallback") == "fallback"

    def test_get_returns_cached_value(self):
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)
        mgr._cache["my_key"] = "my_value"
        assert mgr.get("my_key") == "my_value"


class TestSecretsManagerSet:
    """Tests for async set."""

    @pytest.mark.asyncio
    async def test_set_encrypts_and_stores(self):
        """set() encrypts the value, writes to DB, updates cache."""
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)
        mgr._pool = _make_pool()

        await mgr.set("new_secret", "secret_value", changed_by=42)

        # Cache updated
        assert mgr.get("new_secret") == "secret_value"

    @pytest.mark.asyncio
    async def test_set_calls_execute_twice(self):
        """set() should INSERT the secret + INSERT an audit log entry."""
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)
        pool = _make_pool()
        mgr._pool = pool

        await mgr.set("key", "value", changed_by=1)

        # The conn is yielded by pool.acquire().__aenter__()
        conn = pool.acquire.return_value.__aenter__.return_value
        assert conn.execute.call_count == 2  # secret UPSERT + audit log


class TestSecretsManagerDelete:
    """Tests for async delete."""

    @pytest.mark.asyncio
    async def test_delete_removes_from_cache(self):
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)
        pool = _make_pool()
        pool.acquire.return_value.__aenter__.return_value.execute.return_value = "DELETE 1"
        mgr._pool = pool
        mgr._cache["doomed"] = "value"

        result = await mgr.delete("doomed", deleted_by=1)

        assert result is True
        assert mgr.get("doomed") is None

    @pytest.mark.asyncio
    async def test_delete_nonexistent_returns_false(self):
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)
        pool = _make_pool()
        pool.acquire.return_value.__aenter__.return_value.execute.return_value = "DELETE 0"
        mgr._pool = pool

        result = await mgr.delete("nope", deleted_by=1)
        assert result is False


class TestSecretsManagerRefresh:
    """Tests for cache refresh."""

    @pytest.mark.asyncio
    async def test_refresh_replaces_cache(self):
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)
        mgr._cache["old"] = "stale"

        new_encrypted = encryptor.encrypt_value("fresh")
        pool = _make_pool(rows=[{"name": "new", "value": new_encrypted}])
        mgr._pool = pool

        await mgr.refresh()

        assert mgr.get("old") is None  # old key gone
        assert mgr.get("new") == "fresh"


# ---------------------------------------------------------------------------
# SecretResolver tests
# ---------------------------------------------------------------------------


class TestSecretResolverCascade:
    """Tests for SecretResolver cascade logic."""

    def test_db_value_takes_precedence(self):
        """DB value wins over .env value."""
        mgr = MagicMock()
        mgr.get.return_value = "from-db"

        settings = MagicMock()
        settings.anthropic_api_key = MagicMock()
        settings.anthropic_api_key.get_secret_value.return_value = "from-env"

        resolver = SecretResolver(secrets_manager=mgr, settings=settings)
        assert resolver.get_secret("anthropic_api_key") == "from-db"

    def test_falls_back_to_env(self):
        """When DB has no value, falls back to .env."""
        mgr = MagicMock()
        mgr.get.return_value = None

        settings = MagicMock()
        settings.anthropic_api_key = SecretStr("from-env")

        resolver = SecretResolver(secrets_manager=mgr, settings=settings)
        assert resolver.get_secret("anthropic_api_key") == "from-env"

    def test_falls_back_to_default(self):
        """When both DB and .env miss, returns default."""
        mgr = MagicMock()
        mgr.get.return_value = None

        settings = MagicMock()
        settings.anthropic_api_key = None  # Not set in .env

        resolver = SecretResolver(secrets_manager=mgr, settings=settings)
        assert resolver.get_secret("anthropic_api_key", "fallback") == "fallback"

    def test_no_secrets_manager(self):
        """When SecretsManager is None, falls directly to .env."""
        settings = MagicMock()
        settings.gemini_api_key = SecretStr("gemini-key")

        resolver = SecretResolver(secrets_manager=None, settings=settings)
        assert resolver.get_secret("gemini_api_key") == "gemini-key"

    def test_unmapped_name_returns_default(self):
        """Secret name not in _SETTINGS_FIELD_MAP returns default."""
        mgr = MagicMock()
        mgr.get.return_value = None

        settings = MagicMock()
        resolver = SecretResolver(secrets_manager=mgr, settings=settings)
        assert resolver.get_secret("unknown_secret", "default") == "default"

    def test_all_mapped_fields(self):
        """All expected secret names are mapped."""
        from zetherion_ai.security.secret_resolver import _SETTINGS_FIELD_MAP

        expected = {
            "discord_token",
            "gemini_api_key",
            "anthropic_api_key",
            "openai_api_key",
            "google_client_secret",
            "github_token",
            "skills_api_secret",
            "api_jwt_secret",
        }
        assert set(_SETTINGS_FIELD_MAP.keys()) == expected


# ---------------------------------------------------------------------------
# get_secret() module-level function
# ---------------------------------------------------------------------------


class TestGetSecretFunction:
    """Tests for the config.get_secret() convenience function."""

    def test_returns_default_when_no_resolver(self):
        from zetherion_ai.config import get_secret

        with patch("zetherion_ai.config._secret_resolver", None):
            assert get_secret("anything", "default") == "default"

    def test_delegates_to_resolver(self):
        from zetherion_ai.config import get_secret

        mock_resolver = MagicMock()
        mock_resolver.get_secret.return_value = "resolved"

        with patch("zetherion_ai.config._secret_resolver", mock_resolver):
            result = get_secret("api_key", "fallback")
            assert result == "resolved"
            mock_resolver.get_secret.assert_called_once_with("api_key", "fallback")


# ---------------------------------------------------------------------------
# Encryption round-trip
# ---------------------------------------------------------------------------


class TestSecretsEncryptionRoundTrip:
    """Verify secrets survive encrypt -> store -> decrypt cycle."""

    @pytest.mark.asyncio
    async def test_roundtrip_with_special_characters(self):
        """Secrets with special characters survive encryption."""
        encryptor = _make_encryptor()

        # Simulate set + refresh with the same encryptor
        test_values = [
            "sk-ant-api03-abc123",
            "key-with-unicode-\u00e9\u00e8\u00ea",
            "key=with&special?chars/and+more",
            "a" * 1000,  # Long key
        ]

        for val in test_values:
            encrypted = encryptor.encrypt_value(val)
            decrypted = encryptor.decrypt_value(encrypted)
            assert decrypted == val, f"Round-trip failed for: {val[:50]}"


# ---------------------------------------------------------------------------
# Additional coverage for uncovered paths
# ---------------------------------------------------------------------------


class TestSecretsManagerGetMetadata:
    """Tests for get_metadata() method."""

    @pytest.mark.asyncio
    async def test_get_metadata_returns_rows(self):
        """get_metadata returns list of metadata dicts."""
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)

        rows = [
            {
                "name": "api_key",
                "description": "API key for service",
                "created_at": "2024-01-01",
                "updated_at": "2024-01-02",
                "updated_by": 42,
            },
            {
                "name": "token",
                "description": None,
                "created_at": "2024-01-03",
                "updated_at": "2024-01-04",
                "updated_by": 100,
            },
        ]
        pool = _make_pool()
        pool.acquire.return_value.__aenter__.return_value.fetch.return_value = rows
        mgr._pool = pool

        result = await mgr.get_metadata()
        assert len(result) == 2
        assert result[0]["name"] == "api_key"
        assert result[1]["description"] is None

    @pytest.mark.asyncio
    async def test_get_metadata_exception_returns_empty(self):
        """get_metadata returns empty list on exception."""
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)

        pool = _make_pool()
        pool.acquire.return_value.__aenter__.return_value.fetch.side_effect = Exception("DB error")
        mgr._pool = pool

        result = await mgr.get_metadata()
        assert result == []


class TestSecretsManagerSetError:
    """Tests for set() error handling."""

    @pytest.mark.asyncio
    async def test_set_raises_on_db_error(self):
        """set() re-raises exceptions from database."""
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)

        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.execute.side_effect = Exception("DB error")
        mgr._pool = pool

        with pytest.raises(Exception, match="DB error"):
            await mgr.set("key", "value", changed_by=1)


class TestSecretsManagerDeleteError:
    """Tests for delete() error handling."""

    @pytest.mark.asyncio
    async def test_delete_raises_on_db_error(self):
        """delete() re-raises exceptions from database."""
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)

        pool = _make_pool()
        conn = pool.acquire.return_value.__aenter__.return_value
        conn.execute.side_effect = Exception("DB error")
        mgr._pool = pool

        with pytest.raises(Exception, match="DB error"):
            await mgr.delete("key", deleted_by=1)


class TestSecretsManagerRefreshError:
    """Tests for refresh() error handling."""

    @pytest.mark.asyncio
    async def test_refresh_raises_on_db_error(self):
        """refresh() re-raises exceptions from database."""
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)

        pool = _make_pool()
        pool.acquire.return_value.__aenter__.return_value.fetch.side_effect = Exception("DB error")
        mgr._pool = pool

        with pytest.raises(Exception, match="DB error"):
            await mgr.refresh()


class TestSecretsManagerListNames:
    """Tests for list_names() method."""

    def test_list_names_empty(self):
        """list_names returns empty list when cache is empty."""
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)
        assert mgr.list_names() == []

    def test_list_names_sorted(self):
        """list_names returns sorted list of secret names."""
        encryptor = _make_encryptor()
        mgr = SecretsManager(encryptor=encryptor)
        mgr._cache = {"zebra": "val1", "alpha": "val2", "beta": "val3"}
        assert mgr.list_names() == ["alpha", "beta", "zebra"]
