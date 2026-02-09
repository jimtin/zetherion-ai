"""Unit tests for Gmail sync module.

Every test mocks asyncpg, GmailAccountManager, GmailAuth, and GmailClient
so no real database, network calls, or credentials are required.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, MagicMock, patch

import asyncpg
import pytest

from zetherion_ai.skills.gmail.accounts import GmailAccount, GmailAccountManager
from zetherion_ai.skills.gmail.auth import GmailAuth, OAuthError
from zetherion_ai.skills.gmail.client import EmailMessage, GmailClientError
from zetherion_ai.skills.gmail.sync import (
    GMAIL_EMAILS_SCHEMA_SQL,
    GmailSync,
    SyncResult,
)

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------


def _make_mock_pool():
    """Build a mock asyncpg pool with an acquirable connection."""
    pool = AsyncMock()
    conn = AsyncMock()
    acq_cm = AsyncMock()
    acq_cm.__aenter__.return_value = conn
    pool.acquire = MagicMock(return_value=acq_cm)
    return pool, conn


def _make_account(**kwargs) -> GmailAccount:
    """Build a GmailAccount with sensible defaults."""
    defaults = {
        "id": 1,
        "user_id": 12345,
        "email": "test@gmail.com",
        "access_token": "access_token",
        "refresh_token": "refresh_token",
        "token_expiry": datetime.now() + timedelta(hours=1),
    }
    defaults.update(kwargs)
    return GmailAccount(**defaults)


def _make_email_message(**kwargs) -> EmailMessage:
    """Build an EmailMessage with sensible defaults."""
    defaults = {
        "gmail_id": "msg_001",
        "thread_id": "thread_001",
        "subject": "Test Subject",
        "from_email": "sender@gmail.com",
        "to_emails": ["test@gmail.com"],
        "received_at": datetime(2025, 6, 15, 10, 0, 0),
        "is_read": False,
    }
    defaults.update(kwargs)
    return EmailMessage(**defaults)


@pytest.fixture
def mock_pool():
    """Fixture providing (pool, conn)."""
    return _make_mock_pool()


@pytest.fixture
def mock_account_manager():
    """Fixture providing a mocked GmailAccountManager."""
    mgr = AsyncMock(spec=GmailAccountManager)
    return mgr


@pytest.fixture
def mock_auth():
    """Fixture providing a mocked GmailAuth."""
    auth = AsyncMock(spec=GmailAuth)
    return auth


@pytest.fixture
def sync(mock_pool, mock_account_manager, mock_auth):
    """Fixture providing a GmailSync instance with mocked dependencies."""
    pool, _ = mock_pool
    return GmailSync(pool, mock_account_manager, mock_auth)


# ---------------------------------------------------------------------------
# Tests: SyncResult dataclass
# ---------------------------------------------------------------------------


class TestSyncResult:
    """Tests for the SyncResult dataclass."""

    def test_default_values(self):
        """SyncResult has correct default field values."""
        result = SyncResult(account_email="a@gmail.com", account_id=1)

        assert result.account_email == "a@gmail.com"
        assert result.account_id == 1
        assert result.new_emails == 0
        assert result.errors == 0
        assert result.history_id is None
        assert result.sync_type == "full"

    def test_to_dict(self):
        """to_dict() includes all fields."""
        result = SyncResult(
            account_email="a@gmail.com",
            account_id=42,
            new_emails=5,
            errors=1,
            history_id="12345",
            sync_type="incremental",
        )

        d = result.to_dict()

        assert d == {
            "account_email": "a@gmail.com",
            "account_id": 42,
            "new_emails": 5,
            "errors": 1,
            "history_id": "12345",
            "sync_type": "incremental",
        }


# ---------------------------------------------------------------------------
# Tests: ensure_schema
# ---------------------------------------------------------------------------


class TestEnsureSchema:
    """Tests for GmailSync.ensure_schema."""

    @pytest.mark.asyncio
    async def test_executes_schema_sql(self, mock_pool, mock_account_manager, mock_auth):
        """ensure_schema calls conn.execute with GMAIL_EMAILS_SCHEMA_SQL."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)

        await gs.ensure_schema()

        conn.execute.assert_awaited_once_with(GMAIL_EMAILS_SCHEMA_SQL)

    @pytest.mark.asyncio
    async def test_raises_on_postgres_error(self, mock_pool, mock_account_manager, mock_auth):
        """ensure_schema re-raises asyncpg.PostgresError."""
        pool, conn = mock_pool
        conn.execute.side_effect = asyncpg.PostgresError("syntax error")
        gs = GmailSync(pool, mock_account_manager, mock_auth)

        with pytest.raises(asyncpg.PostgresError):
            await gs.ensure_schema()


# ---------------------------------------------------------------------------
# Tests: sync_account - full sync
# ---------------------------------------------------------------------------


class TestSyncAccountFullSync:
    """Tests for full sync path in sync_account."""

    @pytest.mark.asyncio
    async def test_full_sync_fetches_and_stores_messages(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """Full sync lists messages, gets each, stores, and updates state."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()

        # No sync state -> full sync
        mock_account_manager.get_sync_state.return_value = None

        msg = _make_email_message()

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.list_messages.return_value = ([{"id": "msg_001"}], None)
            client.get_message.return_value = msg
            client.get_profile.return_value = {"historyId": "99999"}
            # store_email returns an ID (new row)
            conn.fetchval.return_value = 1

            result = await gs.sync_account(account)

        assert result.sync_type == "full"
        assert result.new_emails == 1
        assert result.errors == 0
        assert result.history_id == "99999"
        mock_account_manager.update_sync_state.assert_awaited_once()
        mock_account_manager.update_last_sync.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_no_sync_state_defaults_to_full_sync(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """When get_sync_state returns None, full sync is performed."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = None

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.list_messages.return_value = ([], None)
            client.get_profile.return_value = {"historyId": "100"}
            conn.fetchval.return_value = None

            result = await gs.sync_account(account)

        assert result.sync_type == "full"

    @pytest.mark.asyncio
    async def test_sync_state_without_history_id_defaults_to_full_sync(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """Sync state with no history_id triggers full sync."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = {"history_id": None}

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.list_messages.return_value = ([], None)
            client.get_profile.return_value = {"historyId": "200"}
            conn.fetchval.return_value = None

            result = await gs.sync_account(account)

        assert result.sync_type == "full"

    @pytest.mark.asyncio
    async def test_get_profile_history_id_saved(self, mock_pool, mock_account_manager, mock_auth):
        """Profile historyId is saved in the result and passed to update_sync_state."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = None

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.list_messages.return_value = ([], None)
            client.get_profile.return_value = {"historyId": "HIST123"}
            conn.fetchval.return_value = None

            result = await gs.sync_account(account)

        assert result.history_id == "HIST123"
        mock_account_manager.update_sync_state.assert_awaited_once_with(
            account.id, history_id="HIST123", full_sync=True
        )

    @pytest.mark.asyncio
    async def test_individual_message_error_does_not_stop_sync(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """An error fetching one message increments errors but continues."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = None

        good_msg = _make_email_message(gmail_id="msg_002")

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.list_messages.return_value = (
                [{"id": "msg_001"}, {"id": "msg_002"}],
                None,
            )
            # First message fails, second succeeds
            client.get_message.side_effect = [
                GmailClientError("404"),
                good_msg,
            ]
            client.get_profile.return_value = {"historyId": "300"}
            conn.fetchval.return_value = 2

            result = await gs.sync_account(account)

        assert result.errors == 1
        assert result.new_emails == 1

    @pytest.mark.asyncio
    async def test_list_messages_error(self, mock_pool, mock_account_manager, mock_auth):
        """GmailClientError from list_messages is caught, increments errors."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = None

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.list_messages.side_effect = GmailClientError("rate limit")

            result = await gs.sync_account(account)

        assert result.errors >= 1
        assert result.sync_type == "full"

    @pytest.mark.asyncio
    async def test_token_refresh_before_sync_when_expired(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """When token is expired, it is refreshed before syncing."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account(
            token_expiry=datetime.now() - timedelta(hours=1),
        )
        mock_account_manager.get_sync_state.return_value = None
        mock_auth.refresh_access_token.return_value = {
            "access_token": "new_access",
            "expires_in": 3600,
        }

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.list_messages.return_value = ([], None)
            client.get_profile.return_value = {"historyId": "400"}
            conn.fetchval.return_value = None

            result = await gs.sync_account(account)

        mock_auth.refresh_access_token.assert_awaited_once_with("refresh_token")
        mock_client_cls.assert_called_once_with("new_access")
        assert result.errors == 0

    @pytest.mark.asyncio
    async def test_get_profile_error_does_not_fail_sync(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """GmailClientError from get_profile is caught; sync still succeeds."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = None

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.list_messages.return_value = ([], None)
            client.get_profile.side_effect = GmailClientError("profile fail")
            conn.fetchval.return_value = None

            result = await gs.sync_account(account)

        assert result.history_id is None
        assert result.errors == 0

    @pytest.mark.asyncio
    async def test_duplicate_email_not_counted_as_new(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """When store_email returns None (duplicate), new_emails is not incremented."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = None

        msg = _make_email_message()

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.list_messages.return_value = ([{"id": "msg_001"}], None)
            client.get_message.return_value = msg
            client.get_profile.return_value = {"historyId": "500"}
            # store_email returns None -> ON CONFLICT DO NOTHING
            conn.fetchval.return_value = None

            result = await gs.sync_account(account)

        assert result.new_emails == 0


# ---------------------------------------------------------------------------
# Tests: sync_account - incremental sync
# ---------------------------------------------------------------------------


class TestSyncAccountIncrementalSync:
    """Tests for incremental sync path in sync_account."""

    @pytest.mark.asyncio
    async def test_incremental_sync_when_history_id_exists(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """Incremental sync is used when sync state has a history_id."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = {"history_id": "OLD_HIST"}

        msg = _make_email_message()

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.get_history.return_value = (
                [
                    {
                        "messagesAdded": [
                            {"message": {"id": "msg_001"}},
                        ]
                    }
                ],
                "NEW_HIST",
            )
            client.get_message.return_value = msg
            conn.fetchval.return_value = 10

            result = await gs.sync_account(account)

        assert result.sync_type == "incremental"
        assert result.new_emails == 1
        assert result.history_id == "NEW_HIST"
        mock_account_manager.update_sync_state.assert_awaited_once_with(
            account.id, history_id="NEW_HIST"
        )
        mock_account_manager.update_last_sync.assert_awaited_once_with(account.id)

    @pytest.mark.asyncio
    async def test_falls_back_to_full_sync_on_history_error(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """Falls back to full sync when incremental sync raises GmailClientError."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = {"history_id": "OLD_HIST"}

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            # Incremental fails
            client.get_history.side_effect = GmailClientError("history expired")
            # Full sync succeeds
            client.list_messages.return_value = ([], None)
            client.get_profile.return_value = {"historyId": "FALLBACK_HIST"}
            conn.fetchval.return_value = None

            result = await gs.sync_account(account)

        assert result.sync_type == "full"

    @pytest.mark.asyncio
    async def test_empty_history_records(self, mock_pool, mock_account_manager, mock_auth):
        """Incremental sync with no history records yields zero new emails."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = {"history_id": "OLD_HIST"}

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.get_history.return_value = ([], "SAME_HIST")

            result = await gs.sync_account(account)

        assert result.sync_type == "incremental"
        assert result.new_emails == 0
        assert result.errors == 0

    @pytest.mark.asyncio
    async def test_processes_messages_added_records(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """Incremental sync processes messagesAdded from multiple history records."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = {"history_id": "OLD_HIST"}

        msg1 = _make_email_message(gmail_id="msg_001")
        msg2 = _make_email_message(gmail_id="msg_002")

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.get_history.return_value = (
                [
                    {"messagesAdded": [{"message": {"id": "msg_001"}}]},
                    {"messagesAdded": [{"message": {"id": "msg_002"}}]},
                ],
                "NEW_HIST2",
            )
            client.get_message.side_effect = [msg1, msg2]
            conn.fetchval.side_effect = [10, 11]

            result = await gs.sync_account(account)

        assert result.sync_type == "incremental"
        assert result.new_emails == 2

    @pytest.mark.asyncio
    async def test_individual_message_error_in_incremental(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """An error fetching one message in incremental sync increments errors."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = {"history_id": "OLD_HIST"}

        good_msg = _make_email_message(gmail_id="msg_002")

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.get_history.return_value = (
                [
                    {
                        "messagesAdded": [
                            {"message": {"id": "msg_001"}},
                            {"message": {"id": "msg_002"}},
                        ]
                    }
                ],
                "NEW_HIST3",
            )
            client.get_message.side_effect = [
                GmailClientError("fetch error"),
                good_msg,
            ]
            conn.fetchval.return_value = 12

            result = await gs.sync_account(account)

        assert result.errors == 1
        assert result.new_emails == 1

    @pytest.mark.asyncio
    async def test_skips_empty_message_id_in_incremental(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """Messages with empty or missing id are skipped in incremental sync."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account()
        mock_account_manager.get_sync_state.return_value = {"history_id": "OLD_HIST"}

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.get_history.return_value = (
                [
                    {
                        "messagesAdded": [
                            {"message": {"id": ""}},
                            {"message": {}},
                        ]
                    }
                ],
                "NEW_HIST4",
            )

            result = await gs.sync_account(account)

        assert result.new_emails == 0
        assert result.errors == 0
        client.get_message.assert_not_awaited()


# ---------------------------------------------------------------------------
# Tests: sync_all_accounts
# ---------------------------------------------------------------------------


class TestSyncAllAccounts:
    """Tests for GmailSync.sync_all_accounts."""

    @pytest.mark.asyncio
    async def test_syncs_multiple_accounts(self, mock_pool, mock_account_manager, mock_auth):
        """sync_all_accounts iterates over all accounts and syncs each."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)

        acct1 = _make_account(id=1, email="a@gmail.com")
        acct2 = _make_account(id=2, email="b@gmail.com")

        mock_account_manager.list_accounts.return_value = [acct1, acct2]
        mock_account_manager.get_account.side_effect = [acct1, acct2]
        mock_account_manager.get_sync_state.return_value = None

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.list_messages.return_value = ([], None)
            client.get_profile.return_value = {"historyId": "100"}
            conn.fetchval.return_value = None

            results = await gs.sync_all_accounts(12345)

        assert len(results) == 2

    @pytest.mark.asyncio
    async def test_skips_none_accounts(self, mock_pool, mock_account_manager, mock_auth):
        """sync_all_accounts skips accounts where get_account returns None."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)

        acct1 = _make_account(id=1, email="a@gmail.com")
        acct2 = _make_account(id=2, email="b@gmail.com")

        mock_account_manager.list_accounts.return_value = [acct1, acct2]
        # First get_account returns None, second returns the account
        mock_account_manager.get_account.side_effect = [None, acct2]
        mock_account_manager.get_sync_state.return_value = None

        with patch("zetherion_ai.skills.gmail.sync.GmailClient") as mock_client_cls:
            client = AsyncMock()
            mock_client_cls.return_value = client
            client.list_messages.return_value = ([], None)
            client.get_profile.return_value = {"historyId": "200"}
            conn.fetchval.return_value = None

            results = await gs.sync_all_accounts(12345)

        assert len(results) == 1

    @pytest.mark.asyncio
    async def test_empty_account_list(self, mock_pool, mock_account_manager, mock_auth):
        """sync_all_accounts returns empty list when no accounts exist."""
        pool, _ = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        mock_account_manager.list_accounts.return_value = []

        results = await gs.sync_all_accounts(12345)

        assert results == []


# ---------------------------------------------------------------------------
# Tests: get_unprocessed_emails
# ---------------------------------------------------------------------------


class TestGetUnprocessedEmails:
    """Tests for GmailSync.get_unprocessed_emails."""

    @pytest.mark.asyncio
    async def test_returns_unprocessed_emails(self, mock_pool, mock_account_manager, mock_auth):
        """get_unprocessed_emails returns list of email dicts."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)

        # Mock rows as dict-like objects
        row1 = {"id": 1, "gmail_id": "m1", "is_processed": False}
        row2 = {"id": 2, "gmail_id": "m2", "is_processed": False}
        conn.fetch.return_value = [row1, row2]

        result = await gs.get_unprocessed_emails(1)

        assert len(result) == 2
        assert result[0]["gmail_id"] == "m1"
        assert result[1]["gmail_id"] == "m2"

    @pytest.mark.asyncio
    async def test_empty_result(self, mock_pool, mock_account_manager, mock_auth):
        """get_unprocessed_emails returns empty list when none found."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        conn.fetch.return_value = []

        result = await gs.get_unprocessed_emails(1)

        assert result == []


# ---------------------------------------------------------------------------
# Tests: mark_processed
# ---------------------------------------------------------------------------


class TestMarkProcessed:
    """Tests for GmailSync.mark_processed."""

    @pytest.mark.asyncio
    async def test_updates_flag(self, mock_pool, mock_account_manager, mock_auth):
        """mark_processed executes UPDATE setting is_processed=TRUE."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        conn.execute.return_value = "UPDATE 1"

        await gs.mark_processed(42)

        conn.execute.assert_awaited_once()
        call_args = conn.execute.call_args[0]
        assert "is_processed = TRUE" in call_args[0]
        assert call_args[1] == 42


# ---------------------------------------------------------------------------
# Tests: store_email
# ---------------------------------------------------------------------------


class TestStoreEmail:
    """Tests for GmailSync.store_email."""

    @pytest.mark.asyncio
    async def test_new_email_stored(self, mock_pool, mock_account_manager, mock_auth):
        """store_email returns int ID when a new row is inserted."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        conn.fetchval.return_value = 7

        result = await gs.store_email(
            1,
            "gmail_123",
            thread_id="t1",
            subject="Hello",
            from_email="from@test.com",
            to_emails=["to@test.com"],
            received_at=datetime(2025, 6, 15),
            classification="personal",
            priority_score=0.8,
            is_read=True,
        )

        assert result == 7

    @pytest.mark.asyncio
    async def test_duplicate_returns_none(self, mock_pool, mock_account_manager, mock_auth):
        """store_email returns None when ON CONFLICT DO NOTHING fires."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        conn.fetchval.return_value = None

        result = await gs.store_email(1, "gmail_123")

        assert result is None

    @pytest.mark.asyncio
    async def test_all_fields_passed_correctly(self, mock_pool, mock_account_manager, mock_auth):
        """store_email passes all arguments to the SQL query in correct order."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        conn.fetchval.return_value = 1

        received = datetime(2025, 6, 15, 12, 0, 0)
        await gs.store_email(
            10,
            "gid_abc",
            thread_id="tid_xyz",
            subject="Subj",
            from_email="from@x.com",
            to_emails=["a@x.com", "b@x.com"],
            received_at=received,
            classification="work",
            priority_score=0.5,
            is_read=False,
        )

        call_args = conn.fetchval.call_args[0]
        # Args order: query, account_id, gmail_id, thread_id, subject,
        #             from_email, to_emails, received_at, classification,
        #             priority_score, is_read
        assert call_args[1] == 10  # account_id
        assert call_args[2] == "gid_abc"  # gmail_id
        assert call_args[3] == "tid_xyz"  # thread_id
        assert call_args[4] == "Subj"  # subject
        assert call_args[5] == "from@x.com"  # from_email
        assert call_args[6] == ["a@x.com", "b@x.com"]  # to_emails
        assert call_args[7] == received  # received_at
        assert call_args[8] == "work"  # classification
        assert call_args[9] == 0.5  # priority_score
        assert call_args[10] is False  # is_read

    @pytest.mark.asyncio
    async def test_none_to_emails_defaults_to_empty_list(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """store_email defaults to_emails to [] when None is passed."""
        pool, conn = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        conn.fetchval.return_value = 1

        await gs.store_email(1, "g1", to_emails=None)

        call_args = conn.fetchval.call_args[0]
        assert call_args[6] == []  # to_emails defaults to []


# ---------------------------------------------------------------------------
# Tests: _ensure_valid_token
# ---------------------------------------------------------------------------


class TestEnsureValidToken:
    """Tests for GmailSync._ensure_valid_token."""

    @pytest.mark.asyncio
    async def test_token_not_expired_returns_as_is(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """Valid (non-expired) token is returned without refresh."""
        pool, _ = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account(
            token_expiry=datetime.now() + timedelta(hours=1),
        )

        result = await gs._ensure_valid_token(account)

        assert result == "access_token"
        mock_auth.refresh_access_token.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_token_expired_refreshes(self, mock_pool, mock_account_manager, mock_auth):
        """Expired token triggers refresh and returns new access token."""
        pool, _ = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account(
            token_expiry=datetime.now() - timedelta(hours=1),
        )
        mock_auth.refresh_access_token.return_value = {
            "access_token": "new_token",
            "expires_in": 3600,
        }

        result = await gs._ensure_valid_token(account)

        assert result == "new_token"
        mock_auth.refresh_access_token.assert_awaited_once_with("refresh_token")
        mock_account_manager.update_tokens.assert_awaited_once()
        # Verify new expiry was calculated
        call_kwargs = mock_account_manager.update_tokens.call_args
        assert call_kwargs[1]["token_expiry"] is not None

    @pytest.mark.asyncio
    async def test_no_refresh_token_returns_none(self, mock_pool, mock_account_manager, mock_auth):
        """Returns None when token is expired and no refresh_token exists."""
        pool, _ = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account(
            token_expiry=datetime.now() - timedelta(hours=1),
            refresh_token="",
        )

        result = await gs._ensure_valid_token(account)

        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_fails_returns_none(self, mock_pool, mock_account_manager, mock_auth):
        """Returns None when token refresh raises OAuthError."""
        pool, _ = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account(
            token_expiry=datetime.now() - timedelta(hours=1),
        )
        mock_auth.refresh_access_token.side_effect = OAuthError("invalid grant")

        result = await gs._ensure_valid_token(account)

        assert result is None

    @pytest.mark.asyncio
    async def test_refresh_without_expires_in(self, mock_pool, mock_account_manager, mock_auth):
        """Refresh response without expires_in sets token_expiry to None."""
        pool, _ = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account(
            token_expiry=datetime.now() - timedelta(hours=1),
        )
        mock_auth.refresh_access_token.return_value = {
            "access_token": "new_token",
        }

        result = await gs._ensure_valid_token(account)

        assert result == "new_token"
        call_kwargs = mock_account_manager.update_tokens.call_args
        assert call_kwargs[1]["token_expiry"] is None

    @pytest.mark.asyncio
    async def test_refresh_with_new_refresh_token(self, mock_pool, mock_account_manager, mock_auth):
        """Refresh response with new refresh_token passes it to update_tokens."""
        pool, _ = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account(
            token_expiry=datetime.now() - timedelta(hours=1),
        )
        mock_auth.refresh_access_token.return_value = {
            "access_token": "new_access",
            "refresh_token": "new_refresh",
            "expires_in": 7200,
        }

        result = await gs._ensure_valid_token(account)

        assert result == "new_access"
        call_kwargs = mock_account_manager.update_tokens.call_args
        assert call_kwargs[1]["refresh_token"] == "new_refresh"

    @pytest.mark.asyncio
    async def test_no_expiry_info_triggers_refresh(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """When token_expiry is None, refresh is attempted."""
        pool, _ = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account(token_expiry=None)
        mock_auth.refresh_access_token.return_value = {
            "access_token": "refreshed_tok",
        }

        result = await gs._ensure_valid_token(account)

        assert result == "refreshed_tok"
        mock_auth.refresh_access_token.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_sync_account_returns_error_when_token_invalid(
        self, mock_pool, mock_account_manager, mock_auth
    ):
        """sync_account returns result with errors=1 when token cannot be obtained."""
        pool, _ = mock_pool
        gs = GmailSync(pool, mock_account_manager, mock_auth)
        account = _make_account(
            token_expiry=datetime.now() - timedelta(hours=1),
            refresh_token="",
        )

        result = await gs.sync_account(account)

        assert result.errors == 1
        assert result.new_emails == 0
