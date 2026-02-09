"""Gmail email sync module.

Handles polling Gmail accounts for new emails, tracking sync state,
and feeding new emails into the observation pipeline.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import asyncpg  # type: ignore[import-not-found]

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.gmail.accounts import GmailAccount, GmailAccountManager
from zetherion_ai.skills.gmail.auth import GmailAuth, OAuthError
from zetherion_ai.skills.gmail.client import GmailClient, GmailClientError

log = get_logger("zetherion_ai.skills.gmail.sync")

# SQL for email storage
GMAIL_EMAILS_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS gmail_emails (
    id               SERIAL       PRIMARY KEY,
    account_id       INT          NOT NULL,
    gmail_id         TEXT         NOT NULL,
    thread_id        TEXT,
    subject          TEXT,
    from_email       TEXT,
    to_emails        TEXT[],
    received_at      TIMESTAMPTZ,
    classification   TEXT,
    priority_score   FLOAT,
    is_read          BOOLEAN      DEFAULT FALSE,
    is_processed     BOOLEAN      DEFAULT FALSE,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (account_id, gmail_id)
);

CREATE INDEX IF NOT EXISTS idx_gmail_emails_account_id
    ON gmail_emails (account_id);
CREATE INDEX IF NOT EXISTS idx_gmail_emails_unprocessed
    ON gmail_emails (account_id, is_processed) WHERE NOT is_processed;
"""

# Default sync settings
DEFAULT_MAX_MESSAGES = 50
DEFAULT_SYNC_QUERY = "is:inbox newer_than:1d"


@dataclass
class SyncResult:
    """Result of a sync operation."""

    account_email: str
    account_id: int
    new_emails: int = 0
    errors: int = 0
    history_id: str | None = None
    sync_type: str = "full"  # 'full' or 'incremental'

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "account_email": self.account_email,
            "account_id": self.account_id,
            "new_emails": self.new_emails,
            "errors": self.errors,
            "history_id": self.history_id,
            "sync_type": self.sync_type,
        }


class GmailSync:
    """Manages email synchronization for Gmail accounts.

    Supports two sync modes:
    - Full sync: Fetches recent emails via search query.
    - Incremental sync: Uses Gmail history API for changes since
      the last sync.

    New emails are stored in gmail_emails table and can be
    fed into the observation pipeline.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,  # type: ignore[type-arg]
        account_manager: GmailAccountManager,
        auth: GmailAuth,
    ) -> None:
        """Initialize the sync module.

        Args:
            pool: asyncpg connection pool.
            account_manager: For managing account tokens.
            auth: For refreshing expired tokens.
        """
        self._pool: asyncpg.Pool = pool  # type: ignore[type-arg]
        self._account_manager = account_manager
        self._auth = auth

    async def ensure_schema(self) -> None:
        """Create the gmail_emails table if it doesn't exist."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(GMAIL_EMAILS_SCHEMA_SQL)
            log.info("gmail_emails_schema_ensured")
        except asyncpg.PostgresError as exc:
            log.error("gmail_emails_schema_failed", error=str(exc))
            raise

    async def sync_account(
        self,
        account: GmailAccount,
        *,
        max_messages: int = DEFAULT_MAX_MESSAGES,
        query: str = DEFAULT_SYNC_QUERY,
    ) -> SyncResult:
        """Sync a single Gmail account.

        Attempts incremental sync first (if history_id exists),
        falls back to full sync.

        Args:
            account: The Gmail account to sync.
            max_messages: Maximum messages to fetch.
            query: Gmail search query for full sync.

        Returns:
            SyncResult with counts.
        """
        assert account.id is not None  # noqa: S101
        result = SyncResult(
            account_email=account.email,
            account_id=account.id,
        )

        # Get a valid access token (refresh if needed)
        access_token = await self._ensure_valid_token(account)
        if not access_token:
            result.errors = 1
            return result

        client = GmailClient(access_token)

        # Check sync state for incremental sync
        sync_state = await self._account_manager.get_sync_state(account.id)

        if sync_state and sync_state.get("history_id"):
            # Try incremental sync
            try:
                result = await self._incremental_sync(client, account, sync_state["history_id"])
                return result
            except GmailClientError:
                # Fall back to full sync (e.g., history expired)
                log.warning(
                    "incremental_sync_failed_falling_back",
                    account=account.email,
                )

        # Full sync
        result = await self._full_sync(client, account, max_messages, query)
        return result

    async def sync_all_accounts(self, user_id: int, **kwargs: Any) -> list[SyncResult]:
        """Sync all Gmail accounts for a user.

        Args:
            user_id: Discord user ID.
            **kwargs: Passed to sync_account.

        Returns:
            List of SyncResults.
        """
        accounts = await self._account_manager.list_accounts(user_id)
        results: list[SyncResult] = []

        for account in accounts:
            # Need full account with tokens
            if account.id is None:
                continue
            full_account = await self._account_manager.get_account(account.id)
            if full_account is None:
                continue
            result = await self.sync_account(full_account, **kwargs)
            results.append(result)

        return results

    async def get_unprocessed_emails(
        self, account_id: int, *, limit: int = 50
    ) -> list[dict[str, Any]]:
        """Get unprocessed emails for an account.

        Args:
            account_id: The account ID.
            limit: Maximum emails to return.

        Returns:
            List of email records.
        """
        rows = await self._fetch(
            """
            SELECT * FROM gmail_emails
            WHERE account_id = $1 AND is_processed = FALSE
            ORDER BY received_at DESC
            LIMIT $2
            """,
            account_id,
            limit,
        )
        return [dict(r) for r in rows]

    async def mark_processed(self, email_id: int) -> None:
        """Mark an email as processed."""
        await self._execute(
            "UPDATE gmail_emails SET is_processed = TRUE WHERE id = $1",
            email_id,
        )

    async def store_email(
        self,
        account_id: int,
        gmail_id: str,
        *,
        thread_id: str | None = None,
        subject: str | None = None,
        from_email: str | None = None,
        to_emails: list[str] | None = None,
        received_at: datetime | None = None,
        classification: str | None = None,
        priority_score: float | None = None,
        is_read: bool = False,
    ) -> int | None:
        """Store an email record (upsert).

        Returns:
            The email record ID, or None if already exists.
        """
        row_id = await self._fetchval(
            """
            INSERT INTO gmail_emails
                (account_id, gmail_id, thread_id, subject, from_email,
                 to_emails, received_at, classification, priority_score,
                 is_read)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10)
            ON CONFLICT (account_id, gmail_id) DO NOTHING
            RETURNING id
            """,
            account_id,
            gmail_id,
            thread_id,
            subject,
            from_email,
            to_emails or [],
            received_at,
            classification,
            priority_score,
            is_read,
        )
        return int(row_id) if row_id is not None else None

    # ------------------------------------------------------------------
    # Internal sync methods
    # ------------------------------------------------------------------

    async def _full_sync(
        self,
        client: GmailClient,
        account: GmailAccount,
        max_messages: int,
        query: str,
    ) -> SyncResult:
        """Perform a full sync by listing recent messages."""
        assert account.id is not None  # noqa: S101
        result = SyncResult(
            account_email=account.email,
            account_id=account.id,
            sync_type="full",
        )

        try:
            stubs, _ = await client.list_messages(query=query, max_results=max_messages)

            for stub in stubs:
                msg_id = stub.get("id", "")
                try:
                    msg = await client.get_message(msg_id)
                    stored_id = await self.store_email(
                        account.id,
                        msg.gmail_id,
                        thread_id=msg.thread_id,
                        subject=msg.subject,
                        from_email=msg.from_email,
                        to_emails=msg.to_emails,
                        received_at=msg.received_at,
                        is_read=msg.is_read,
                    )
                    if stored_id is not None:
                        result.new_emails += 1
                except GmailClientError as exc:
                    log.warning(
                        "full_sync_message_error",
                        message_id=msg_id,
                        error=str(exc),
                    )
                    result.errors += 1

            # Update history ID from profile
            try:
                profile = await client.get_profile()
                result.history_id = profile.get("historyId")
            except GmailClientError:
                pass

            # Update sync state
            await self._account_manager.update_sync_state(
                account.id, history_id=result.history_id, full_sync=True
            )
            await self._account_manager.update_last_sync(account.id)

        except GmailClientError as exc:
            log.error("full_sync_failed", account=account.email, error=str(exc))
            result.errors += 1

        log.info(
            "full_sync_complete",
            account=account.email,
            new_emails=result.new_emails,
            errors=result.errors,
        )
        return result

    async def _incremental_sync(
        self,
        client: GmailClient,
        account: GmailAccount,
        history_id: str,
    ) -> SyncResult:
        """Perform incremental sync using Gmail history API."""
        assert account.id is not None  # noqa: S101
        result = SyncResult(
            account_email=account.email,
            account_id=account.id,
            sync_type="incremental",
        )

        history, new_history_id = await client.get_history(history_id)

        for record in history:
            for added in record.get("messagesAdded", []):
                msg_data = added.get("message", {})
                msg_id = msg_data.get("id", "")
                if not msg_id:
                    continue

                try:
                    msg = await client.get_message(msg_id)
                    stored_id = await self.store_email(
                        account.id,
                        msg.gmail_id,
                        thread_id=msg.thread_id,
                        subject=msg.subject,
                        from_email=msg.from_email,
                        to_emails=msg.to_emails,
                        received_at=msg.received_at,
                        is_read=msg.is_read,
                    )
                    if stored_id is not None:
                        result.new_emails += 1
                except GmailClientError as exc:
                    log.warning(
                        "incremental_sync_message_error",
                        message_id=msg_id,
                        error=str(exc),
                    )
                    result.errors += 1

        result.history_id = new_history_id

        # Update sync state
        await self._account_manager.update_sync_state(account.id, history_id=new_history_id)
        await self._account_manager.update_last_sync(account.id)

        log.info(
            "incremental_sync_complete",
            account=account.email,
            new_emails=result.new_emails,
            errors=result.errors,
        )
        return result

    async def _ensure_valid_token(self, account: GmailAccount) -> str | None:
        """Ensure the account has a valid access token, refreshing if needed.

        Returns:
            Valid access token, or None if refresh failed.
        """
        # Check if token is expired
        if account.token_expiry and account.token_expiry > datetime.now():
            return account.access_token

        # Token expired or no expiry info — try refreshing
        if not account.refresh_token:
            log.error("no_refresh_token", account=account.email)
            return None

        try:
            tokens = await self._auth.refresh_access_token(account.refresh_token)
            new_access: str = tokens["access_token"]
            new_expiry = None
            if "expires_in" in tokens:
                from datetime import timedelta

                new_expiry = datetime.now() + timedelta(seconds=tokens["expires_in"])

            assert account.id is not None  # noqa: S101
            await self._account_manager.update_tokens(
                account.id,
                new_access,
                refresh_token=tokens.get("refresh_token"),
                token_expiry=new_expiry,
            )
            return new_access

        except OAuthError as exc:
            log.error(
                "token_refresh_failed",
                account=account.email,
                error=str(exc),
            )
            return None

    # ------------------------------------------------------------------
    # Pool wrappers
    # ------------------------------------------------------------------

    async def _fetchval(self, query: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def _fetchrow(self, query: str, *args: Any) -> Any:
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def _fetch(self, query: str, *args: Any) -> list[Any]:
        async with self._pool.acquire() as conn:
            result: list[Any] = await conn.fetch(query, *args)
            return result

    async def _execute(self, query: str, *args: Any) -> str:
        async with self._pool.acquire() as conn:
            result: str = await conn.execute(query, *args)
            return result
