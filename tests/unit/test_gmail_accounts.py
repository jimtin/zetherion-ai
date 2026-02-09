"""Unit tests for Gmail multi-account management.

Every test mocks asyncpg and FieldEncryptor so no real database or
encryption key is required.
"""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import asyncpg
import pytest

from zetherion_ai.security.encryption import FieldEncryptor
from zetherion_ai.skills.gmail.accounts import (
    GMAIL_SCHEMA_SQL,
    GmailAccount,
    GmailAccountManager,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_mock_pool():
    """Build a mock asyncpg pool with an acquirable connection.

    Returns (pool, conn) where ``pool.acquire()`` used as an async
    context manager yields ``conn``.
    """
    pool = AsyncMock()
    conn = AsyncMock()

    acq_cm = AsyncMock()
    acq_cm.__aenter__.return_value = conn
    pool.acquire = MagicMock(return_value=acq_cm)

    tx_cm = AsyncMock()
    tx_cm.__aenter__.return_value = conn
    conn.transaction = MagicMock(return_value=tx_cm)

    return pool, conn


def _make_mock_encryptor():
    """Build a mock FieldEncryptor with deterministic encrypt/decrypt."""
    enc = MagicMock(spec=FieldEncryptor)
    enc.encrypt_value = MagicMock(side_effect=lambda v: f"enc:{v}")
    enc.decrypt_value = MagicMock(side_effect=lambda v: v.replace("enc:", ""))
    return enc


def _account_row(
    *,
    id: int = 1,
    user_id: int = 12345,
    email: str = "user@gmail.com",
    access_token: str = "enc:access_tok",
    refresh_token: str = "enc:refresh_tok",
    token_expiry: datetime | None = None,
    scopes: list[str] | None = None,
    is_primary: bool = True,
    last_sync: datetime | None = None,
    created_at: datetime | None = None,
) -> dict:
    """Return a dict mimicking a gmail_accounts DB row."""
    return {
        "id": id,
        "user_id": user_id,
        "email": email,
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_expiry": token_expiry,
        "scopes": scopes or ["https://mail.google.com/"],
        "is_primary": is_primary,
        "last_sync": last_sync,
        "created_at": created_at,
    }


def _sync_state_row(
    *,
    account_id: int = 1,
    history_id: str | None = "12345",
    last_full_sync: datetime | None = None,
    last_partial_sync: datetime | None = None,
) -> dict:
    """Return a dict mimicking a gmail_sync_state DB row."""
    return {
        "account_id": account_id,
        "history_id": history_id,
        "last_full_sync": last_full_sync,
        "last_partial_sync": last_partial_sync,
    }


# ---------------------------------------------------------------------------
# Tests: GmailAccount dataclass
# ---------------------------------------------------------------------------


class TestGmailAccountDataclass:
    """Tests for the GmailAccount dataclass itself."""

    def test_default_field_values(self):
        """GmailAccount has sensible defaults for all fields."""
        account = GmailAccount()

        assert account.id is None
        assert account.user_id == 0
        assert account.email == ""
        assert account.access_token == ""
        assert account.refresh_token == ""
        assert account.token_expiry is None
        assert account.scopes == []
        assert account.is_primary is False
        assert account.last_sync is None
        assert account.created_at is None

    def test_to_dict_excludes_tokens(self):
        """to_dict() omits access_token and refresh_token for safety."""
        account = GmailAccount(
            id=1,
            user_id=12345,
            email="user@gmail.com",
            access_token="secret_access",
            refresh_token="secret_refresh",
            is_primary=True,
            scopes=["https://mail.google.com/"],
        )

        result = account.to_dict()

        assert "access_token" not in result
        assert "refresh_token" not in result
        assert result["id"] == 1
        assert result["user_id"] == 12345
        assert result["email"] == "user@gmail.com"
        assert result["is_primary"] is True
        assert result["scopes"] == ["https://mail.google.com/"]
        assert result["last_sync"] is None
        assert result["created_at"] is None

    def test_to_dict_with_all_fields_set(self):
        """to_dict() formats datetime fields as ISO strings."""
        now = datetime(2025, 6, 15, 10, 30, 0)
        account = GmailAccount(
            id=42,
            user_id=99999,
            email="test@gmail.com",
            access_token="tok",
            refresh_token="ref",
            token_expiry=now,
            scopes=["scope1", "scope2"],
            is_primary=False,
            last_sync=now,
            created_at=now,
        )

        result = account.to_dict()

        assert result["last_sync"] == now.isoformat()
        assert result["created_at"] == now.isoformat()
        assert "token_expiry" not in result


# ---------------------------------------------------------------------------
# Tests: ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    """Tests for GmailAccountManager.ensure_schema."""

    @pytest.mark.asyncio
    async def test_executes_schema_sql(self):
        """ensure_schema calls conn.execute with GMAIL_SCHEMA_SQL."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        await mgr.ensure_schema()

        conn.execute.assert_awaited_once_with(GMAIL_SCHEMA_SQL)

    @pytest.mark.asyncio
    async def test_raises_on_postgres_error(self):
        """ensure_schema re-raises asyncpg.PostgresError."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)
        conn.execute.side_effect = asyncpg.PostgresError("syntax error")

        with pytest.raises(asyncpg.PostgresError):
            await mgr.ensure_schema()


# ---------------------------------------------------------------------------
# Tests: add_account
# ---------------------------------------------------------------------------


class TestAddAccount:
    """Tests for GmailAccountManager.add_account."""

    @pytest.mark.asyncio
    async def test_first_account_becomes_primary(self):
        """The first account added for a user is auto-set as primary."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        # list_accounts returns empty -> first account
        conn.fetch.return_value = []
        conn.fetchval.return_value = 1
        conn.execute.return_value = "INSERT 0 1"

        account_id = await mgr.add_account(12345, "user@gmail.com", "access", "refresh")

        assert account_id == 1
        # The INSERT should have is_primary=True (7th positional arg)
        insert_call = conn.fetchval.call_args[0]
        assert insert_call[7] is True  # is_primary

    @pytest.mark.asyncio
    async def test_second_account_is_not_primary(self):
        """A subsequent account is not set as primary."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        # list_accounts returns one existing account
        conn.fetch.return_value = [_account_row()]
        conn.fetchval.return_value = 2
        conn.execute.return_value = "INSERT 0 1"

        account_id = await mgr.add_account(12345, "second@gmail.com", "access2", "refresh2")

        assert account_id == 2
        insert_call = conn.fetchval.call_args[0]
        assert insert_call[7] is False  # is_primary

    @pytest.mark.asyncio
    async def test_encrypts_tokens_before_storage(self):
        """add_account encrypts access and refresh tokens before INSERT."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetch.return_value = []
        conn.fetchval.return_value = 1
        conn.execute.return_value = "INSERT 0 1"

        await mgr.add_account(12345, "user@gmail.com", "my_access", "my_refresh")

        enc.encrypt_value.assert_any_call("my_access")
        enc.encrypt_value.assert_any_call("my_refresh")
        insert_call = conn.fetchval.call_args[0]
        assert insert_call[3] == "enc:my_access"
        assert insert_call[4] == "enc:my_refresh"

    @pytest.mark.asyncio
    async def test_creates_sync_state_entry(self):
        """add_account inserts a gmail_sync_state row after account creation."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetch.return_value = []
        conn.fetchval.return_value = 7
        conn.execute.return_value = "INSERT 0 1"

        await mgr.add_account(12345, "user@gmail.com", "a", "r")

        # The second execute call should be the sync state INSERT
        execute_calls = conn.execute.await_args_list
        assert len(execute_calls) == 1
        sync_sql = execute_calls[0][0][0]
        assert "gmail_sync_state" in sync_sql
        assert execute_calls[0][0][1] == 7

    @pytest.mark.asyncio
    async def test_add_account_with_scopes_and_expiry(self):
        """add_account passes token_expiry and scopes to INSERT."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        expiry = datetime(2025, 12, 31, 23, 59, 59)
        scopes = ["scope1", "scope2"]
        conn.fetch.return_value = []
        conn.fetchval.return_value = 3
        conn.execute.return_value = "INSERT 0 1"

        account_id = await mgr.add_account(
            12345,
            "user@gmail.com",
            "a",
            "r",
            token_expiry=expiry,
            scopes=scopes,
        )

        assert account_id == 3
        insert_call = conn.fetchval.call_args[0]
        assert insert_call[5] == expiry
        assert insert_call[6] == scopes


# ---------------------------------------------------------------------------
# Tests: get_account
# ---------------------------------------------------------------------------


class TestGetAccount:
    """Tests for GmailAccountManager.get_account."""

    @pytest.mark.asyncio
    async def test_returns_decrypted_account(self):
        """get_account returns a GmailAccount with decrypted tokens."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetchrow.return_value = _account_row()

        result = await mgr.get_account(1)

        assert result is not None
        assert isinstance(result, GmailAccount)
        assert result.id == 1
        assert result.email == "user@gmail.com"
        assert result.access_token == "access_tok"
        assert result.refresh_token == "refresh_tok"
        enc.decrypt_value.assert_any_call("enc:access_tok")
        enc.decrypt_value.assert_any_call("enc:refresh_tok")

    @pytest.mark.asyncio
    async def test_returns_none_for_missing(self):
        """get_account returns None when the account ID does not exist."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetchrow.return_value = None

        result = await mgr.get_account(999)

        assert result is None

    @pytest.mark.asyncio
    async def test_handles_decryption_failure_gracefully(self):
        """get_account returns empty tokens when decryption fails."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        enc.decrypt_value.side_effect = ValueError("bad ciphertext")
        conn.fetchrow.return_value = _account_row()

        result = await mgr.get_account(1)

        assert result is not None
        assert result.access_token == ""
        assert result.refresh_token == ""


# ---------------------------------------------------------------------------
# Tests: get_account_by_email
# ---------------------------------------------------------------------------


class TestGetAccountByEmail:
    """Tests for GmailAccountManager.get_account_by_email."""

    @pytest.mark.asyncio
    async def test_found(self):
        """get_account_by_email returns account when found."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetchrow.return_value = _account_row()

        result = await mgr.get_account_by_email(12345, "user@gmail.com")

        assert result is not None
        assert result.email == "user@gmail.com"
        conn.fetchrow.assert_awaited_once()
        call_args = conn.fetchrow.call_args[0]
        assert "user_id = $1" in call_args[0]
        assert "email = $2" in call_args[0]
        assert call_args[1] == 12345
        assert call_args[2] == "user@gmail.com"

    @pytest.mark.asyncio
    async def test_not_found(self):
        """get_account_by_email returns None when no match."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetchrow.return_value = None

        result = await mgr.get_account_by_email(12345, "missing@gmail.com")

        assert result is None


# ---------------------------------------------------------------------------
# Tests: list_accounts
# ---------------------------------------------------------------------------


class TestListAccounts:
    """Tests for GmailAccountManager.list_accounts."""

    @pytest.mark.asyncio
    async def test_returns_accounts_without_decrypted_tokens(self):
        """list_accounts returns accounts with tokens masked (empty strings)."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetch.return_value = [
            _account_row(id=1, email="a@gmail.com"),
            _account_row(id=2, email="b@gmail.com", is_primary=False),
        ]

        result = await mgr.list_accounts(12345)

        assert len(result) == 2
        assert all(isinstance(a, GmailAccount) for a in result)
        # decrypt_tokens=False means tokens should be empty strings
        for account in result:
            assert account.access_token == ""
            assert account.refresh_token == ""
        # decrypt_value should NOT have been called
        enc.decrypt_value.assert_not_called()

    @pytest.mark.asyncio
    async def test_empty_list(self):
        """list_accounts returns an empty list when user has no accounts."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetch.return_value = []

        result = await mgr.list_accounts(12345)

        assert result == []


# ---------------------------------------------------------------------------
# Tests: get_primary_account
# ---------------------------------------------------------------------------


class TestGetPrimaryAccount:
    """Tests for GmailAccountManager.get_primary_account."""

    @pytest.mark.asyncio
    async def test_found(self):
        """get_primary_account returns the primary account."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetchrow.return_value = _account_row(is_primary=True)

        result = await mgr.get_primary_account(12345)

        assert result is not None
        assert result.is_primary is True
        conn.fetchrow.assert_awaited_once()
        call_args = conn.fetchrow.call_args[0]
        assert "is_primary = TRUE" in call_args[0]

    @pytest.mark.asyncio
    async def test_not_found(self):
        """get_primary_account returns None when no primary is set."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetchrow.return_value = None

        result = await mgr.get_primary_account(12345)

        assert result is None


# ---------------------------------------------------------------------------
# Tests: set_primary
# ---------------------------------------------------------------------------


class TestSetPrimary:
    """Tests for GmailAccountManager.set_primary."""

    @pytest.mark.asyncio
    async def test_success(self):
        """set_primary returns True when the account is updated."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        # The second execute call returns "UPDATE 1"
        conn.execute.side_effect = ["UPDATE 1", "UPDATE 1"]

        result = await mgr.set_primary(12345, 1)

        assert result is True

    @pytest.mark.asyncio
    async def test_failure(self):
        """set_primary returns False when account is not found."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        # First call unsets all primaries, second fails to find the target
        conn.execute.side_effect = ["UPDATE 1", "UPDATE 0"]

        result = await mgr.set_primary(12345, 999)

        assert result is False


# ---------------------------------------------------------------------------
# Tests: remove_account
# ---------------------------------------------------------------------------


class TestRemoveAccount:
    """Tests for GmailAccountManager.remove_account."""

    @pytest.mark.asyncio
    async def test_success(self):
        """remove_account returns True when a row is deleted."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.execute.return_value = "DELETE 1"

        result = await mgr.remove_account(12345, "user@gmail.com")

        assert result is True

    @pytest.mark.asyncio
    async def test_not_found(self):
        """remove_account returns False when no row matches."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.execute.return_value = "DELETE 0"

        result = await mgr.remove_account(12345, "missing@gmail.com")

        assert result is False


# ---------------------------------------------------------------------------
# Tests: update_tokens
# ---------------------------------------------------------------------------


class TestUpdateTokens:
    """Tests for GmailAccountManager.update_tokens."""

    @pytest.mark.asyncio
    async def test_access_token_only(self):
        """update_tokens with only access_token updates one column."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.execute.return_value = "UPDATE 1"
        expiry = datetime(2025, 12, 31)

        await mgr.update_tokens(1, "new_access", token_expiry=expiry)

        enc.encrypt_value.assert_called_once_with("new_access")
        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        sql = call_args[0]
        assert "access_token = $1" in sql
        assert "refresh_token" not in sql
        assert call_args[1] == "enc:new_access"
        assert call_args[2] == expiry
        assert call_args[3] == 1

    @pytest.mark.asyncio
    async def test_both_access_and_refresh_tokens(self):
        """update_tokens with both tokens updates two columns."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.execute.return_value = "UPDATE 1"

        await mgr.update_tokens(1, "new_access", refresh_token="new_refresh", token_expiry=None)

        assert enc.encrypt_value.call_count == 2
        enc.encrypt_value.assert_any_call("new_access")
        enc.encrypt_value.assert_any_call("new_refresh")
        call_args = conn.execute.call_args[0]
        sql = call_args[0]
        assert "access_token = $1" in sql
        assert "refresh_token = $2" in sql
        assert call_args[1] == "enc:new_access"
        assert call_args[2] == "enc:new_refresh"
        assert call_args[3] is None  # token_expiry
        assert call_args[4] == 1  # account_id

    @pytest.mark.asyncio
    async def test_tokens_are_encrypted(self):
        """update_tokens always encrypts tokens before writing to DB."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.execute.return_value = "UPDATE 1"

        await mgr.update_tokens(5, "my_access_tok", refresh_token="my_refresh_tok")

        # Verify encrypt_value was called for both tokens
        assert enc.encrypt_value.call_count == 2
        enc.encrypt_value.assert_any_call("my_access_tok")
        enc.encrypt_value.assert_any_call("my_refresh_tok")
        # Verify the DB receives encrypted (prefixed) values, not raw plaintext
        call_args = conn.execute.call_args[0]
        assert call_args[1] == "enc:my_access_tok"
        assert call_args[2] == "enc:my_refresh_tok"


# ---------------------------------------------------------------------------
# Tests: update_last_sync
# ---------------------------------------------------------------------------


class TestUpdateLastSync:
    """Tests for GmailAccountManager.update_last_sync."""

    @pytest.mark.asyncio
    async def test_updates_timestamp(self):
        """update_last_sync issues an UPDATE with now()."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.execute.return_value = "UPDATE 1"

        await mgr.update_last_sync(1)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        assert "last_sync = now()" in call_args[0]
        assert call_args[1] == 1


# ---------------------------------------------------------------------------
# Tests: get_sync_state / update_sync_state
# ---------------------------------------------------------------------------


class TestSyncState:
    """Tests for get_sync_state and update_sync_state."""

    @pytest.mark.asyncio
    async def test_get_sync_state_returns_dict(self):
        """get_sync_state returns a dict when a row exists."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetchrow.return_value = _sync_state_row(history_id="99999")

        result = await mgr.get_sync_state(1)

        assert result is not None
        assert isinstance(result, dict)
        assert result["history_id"] == "99999"
        assert result["account_id"] == 1

    @pytest.mark.asyncio
    async def test_get_sync_state_returns_none(self):
        """get_sync_state returns None when no sync state exists."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.fetchrow.return_value = None

        result = await mgr.get_sync_state(999)

        assert result is None

    @pytest.mark.asyncio
    async def test_update_sync_state_partial(self):
        """update_sync_state without full_sync updates last_partial_sync only."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.execute.return_value = "UPDATE 1"

        await mgr.update_sync_state(1, history_id="55555")

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        sql = call_args[0]
        assert "last_partial_sync = now()" in sql
        assert "last_full_sync" not in sql
        assert call_args[1] == 1
        assert call_args[2] == "55555"

    @pytest.mark.asyncio
    async def test_update_sync_state_full_sync(self):
        """update_sync_state with full_sync=True updates both timestamps."""
        pool, conn = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        conn.execute.return_value = "UPDATE 1"

        await mgr.update_sync_state(1, history_id="77777", full_sync=True)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        sql = call_args[0]
        assert "last_full_sync = now()" in sql
        assert "last_partial_sync = now()" in sql
        assert call_args[1] == 1
        assert call_args[2] == "77777"


# ---------------------------------------------------------------------------
# Tests: _row_to_account edge cases
# ---------------------------------------------------------------------------


class TestRowToAccount:
    """Tests for internal _row_to_account conversion edge cases."""

    def test_row_with_none_scopes(self):
        """_row_to_account defaults scopes to [] when DB returns None."""
        pool, _ = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        row = _account_row(scopes=None)
        row["scopes"] = None

        account = mgr._row_to_account(row, decrypt_tokens=False)

        assert account.scopes == []

    def test_decrypt_tokens_false_skips_decryption(self):
        """_row_to_account with decrypt_tokens=False leaves tokens empty."""
        pool, _ = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        row = _account_row()

        account = mgr._row_to_account(row, decrypt_tokens=False)

        assert account.access_token == ""
        assert account.refresh_token == ""
        enc.decrypt_value.assert_not_called()

    def test_partial_decryption_failure(self):
        """_row_to_account handles access decrypt failing but refresh succeeding."""
        pool, _ = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        # First call (access_token) raises, second call (refresh_token) succeeds
        enc.decrypt_value.side_effect = [
            ValueError("bad access token"),
            "good_refresh",
        ]
        row = _account_row()

        account = mgr._row_to_account(row)

        assert account.access_token == ""
        assert account.refresh_token == "good_refresh"

    def test_refresh_decryption_failure_only(self):
        """_row_to_account handles refresh decrypt failing but access succeeding."""
        pool, _ = _make_mock_pool()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        # First call (access_token) succeeds, second call (refresh_token) raises
        enc.decrypt_value.side_effect = [
            "good_access",
            Exception("bad refresh token"),
        ]
        row = _account_row()

        account = mgr._row_to_account(row)

        assert account.access_token == "good_access"
        assert account.refresh_token == ""


# ---------------------------------------------------------------------------
# Tests: constructor
# ---------------------------------------------------------------------------


class TestConstructor:
    """Tests for GmailAccountManager constructor."""

    def test_stores_pool_and_encryptor(self):
        """Constructor stores pool and encryptor references."""
        pool = AsyncMock()
        enc = _make_mock_encryptor()
        mgr = GmailAccountManager(pool, enc)

        assert mgr._pool is pool
        assert mgr._encryptor is enc
