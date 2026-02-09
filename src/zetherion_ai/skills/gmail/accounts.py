"""Multi-account Gmail management.

Handles storing, retrieving, and managing multiple Gmail accounts
per user. Tokens are encrypted at rest via FieldEncryptor.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import asyncpg  # type: ignore[import-not-found]

from zetherion_ai.logging import get_logger
from zetherion_ai.security.encryption import FieldEncryptor

log = get_logger("zetherion_ai.skills.gmail.accounts")

# SQL schema for Gmail account tables
GMAIL_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS gmail_accounts (
    id               SERIAL       PRIMARY KEY,
    user_id          BIGINT       NOT NULL,
    email            TEXT         NOT NULL,
    access_token     TEXT         NOT NULL,
    refresh_token    TEXT         NOT NULL,
    token_expiry     TIMESTAMPTZ,
    scopes           TEXT[],
    is_primary       BOOLEAN      DEFAULT FALSE,
    last_sync        TIMESTAMPTZ,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, email)
);

CREATE TABLE IF NOT EXISTS gmail_sync_state (
    account_id       INT          REFERENCES gmail_accounts(id) ON DELETE CASCADE,
    history_id       TEXT,
    last_full_sync   TIMESTAMPTZ,
    last_partial_sync TIMESTAMPTZ,
    PRIMARY KEY (account_id)
);

CREATE INDEX IF NOT EXISTS idx_gmail_accounts_user_id
    ON gmail_accounts (user_id);
"""


@dataclass
class GmailAccount:
    """Represents a connected Gmail account."""

    id: int | None = None
    user_id: int = 0
    email: str = ""
    access_token: str = ""  # nosec B105
    refresh_token: str = ""  # nosec B105
    token_expiry: datetime | None = None
    scopes: list[str] = field(default_factory=list)
    is_primary: bool = False
    last_sync: datetime | None = None
    created_at: datetime | None = None

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary (without tokens for safety)."""
        return {
            "id": self.id,
            "user_id": self.user_id,
            "email": self.email,
            "is_primary": self.is_primary,
            "last_sync": self.last_sync.isoformat() if self.last_sync else None,
            "scopes": self.scopes,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class GmailAccountManager:
    """Manages Gmail accounts in PostgreSQL with encrypted tokens.

    Each user can connect multiple Gmail accounts. One account
    can be designated as primary. Tokens are encrypted at rest
    using AES-256-GCM via FieldEncryptor.
    """

    def __init__(
        self,
        pool: asyncpg.Pool,  # type: ignore[type-arg]
        encryptor: FieldEncryptor,
    ) -> None:
        """Initialize the account manager.

        Args:
            pool: asyncpg connection pool.
            encryptor: FieldEncryptor for token encryption.
        """
        self._pool: asyncpg.Pool = pool  # type: ignore[type-arg]
        self._encryptor = encryptor

    async def ensure_schema(self) -> None:
        """Create Gmail tables if they don't exist."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(GMAIL_SCHEMA_SQL)
            log.info("gmail_schema_ensured")
        except asyncpg.PostgresError as exc:
            log.error("gmail_schema_creation_failed", error=str(exc))
            raise

    async def add_account(
        self,
        user_id: int,
        email_addr: str,
        access_token: str,
        refresh_token: str,
        *,
        token_expiry: datetime | None = None,
        scopes: list[str] | None = None,
    ) -> int:
        """Add or update a Gmail account.

        If the account already exists (same user_id + email), tokens
        are updated. The first account for a user is auto-set as primary.

        Args:
            user_id: Discord user ID.
            email_addr: Gmail email address.
            access_token: OAuth2 access token (will be encrypted).
            refresh_token: OAuth2 refresh token (will be encrypted).
            token_expiry: When the access token expires.
            scopes: Granted OAuth scopes.

        Returns:
            The account ID.
        """
        enc_access = self._encryptor.encrypt_value(access_token)
        enc_refresh = self._encryptor.encrypt_value(refresh_token)

        # Check if user has any existing accounts
        existing = await self.list_accounts(user_id)
        is_primary = len(existing) == 0

        account_id = await self._fetchval(
            """
            INSERT INTO gmail_accounts
                (user_id, email, access_token, refresh_token,
                 token_expiry, scopes, is_primary)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            ON CONFLICT (user_id, email) DO UPDATE SET
                access_token = EXCLUDED.access_token,
                refresh_token = EXCLUDED.refresh_token,
                token_expiry = EXCLUDED.token_expiry,
                scopes = EXCLUDED.scopes
            RETURNING id
            """,
            user_id,
            email_addr,
            enc_access,
            enc_refresh,
            token_expiry,
            scopes or [],
            is_primary,
        )

        # Create sync state entry
        await self._execute(
            """
            INSERT INTO gmail_sync_state (account_id)
            VALUES ($1)
            ON CONFLICT (account_id) DO NOTHING
            """,
            int(account_id),
        )

        log.info(
            "gmail_account_added",
            user_id=user_id,
            email=email_addr,
            account_id=account_id,
            is_primary=is_primary,
        )
        return int(account_id)

    async def get_account(self, account_id: int) -> GmailAccount | None:
        """Get an account by ID with decrypted tokens.

        Args:
            account_id: The account ID.

        Returns:
            GmailAccount with decrypted tokens, or None.
        """
        row = await self._fetchrow(
            "SELECT * FROM gmail_accounts WHERE id = $1",
            account_id,
        )
        if row is None:
            return None
        return self._row_to_account(dict(row))

    async def get_account_by_email(self, user_id: int, email_addr: str) -> GmailAccount | None:
        """Get an account by user ID and email.

        Args:
            user_id: Discord user ID.
            email_addr: Gmail email address.

        Returns:
            GmailAccount with decrypted tokens, or None.
        """
        row = await self._fetchrow(
            "SELECT * FROM gmail_accounts WHERE user_id = $1 AND email = $2",
            user_id,
            email_addr,
        )
        if row is None:
            return None
        return self._row_to_account(dict(row))

    async def list_accounts(self, user_id: int) -> list[GmailAccount]:
        """List all Gmail accounts for a user (without tokens).

        Args:
            user_id: Discord user ID.

        Returns:
            List of GmailAccount (tokens masked).
        """
        rows = await self._fetch(
            "SELECT * FROM gmail_accounts WHERE user_id = $1 ORDER BY is_primary DESC, email",
            user_id,
        )
        accounts = []
        for row in rows:
            account = self._row_to_account(dict(row), decrypt_tokens=False)
            accounts.append(account)
        return accounts

    async def get_primary_account(self, user_id: int) -> GmailAccount | None:
        """Get the primary account for a user with decrypted tokens.

        Args:
            user_id: Discord user ID.

        Returns:
            Primary GmailAccount, or None.
        """
        row = await self._fetchrow(
            "SELECT * FROM gmail_accounts WHERE user_id = $1 AND is_primary = TRUE",
            user_id,
        )
        if row is None:
            return None
        return self._row_to_account(dict(row))

    async def set_primary(self, user_id: int, account_id: int) -> bool:
        """Set an account as the primary for a user.

        Args:
            user_id: Discord user ID.
            account_id: Account ID to set as primary.

        Returns:
            True if successful.
        """
        async with self._pool.acquire() as conn, conn.transaction():
            # Unset all primary flags for this user
            await conn.execute(
                "UPDATE gmail_accounts SET is_primary = FALSE WHERE user_id = $1",
                user_id,
            )
            # Set the specified account as primary
            result = await conn.execute(
                "UPDATE gmail_accounts SET is_primary = TRUE WHERE id = $1 AND user_id = $2",
                account_id,
                user_id,
            )
        updated: bool = result == "UPDATE 1"
        if updated:
            log.info("gmail_primary_set", user_id=user_id, account_id=account_id)
        return updated

    async def remove_account(self, user_id: int, email_addr: str) -> bool:
        """Remove a Gmail account.

        Args:
            user_id: Discord user ID.
            email_addr: Gmail email to remove.

        Returns:
            True if the account was removed.
        """
        result = await self._execute(
            "DELETE FROM gmail_accounts WHERE user_id = $1 AND email = $2",
            user_id,
            email_addr,
        )
        deleted = result == "DELETE 1"
        if deleted:
            log.info("gmail_account_removed", user_id=user_id, email=email_addr)
        return deleted

    async def update_tokens(
        self,
        account_id: int,
        access_token: str,
        *,
        refresh_token: str | None = None,
        token_expiry: datetime | None = None,
    ) -> None:
        """Update tokens for an account (e.g., after refresh).

        Args:
            account_id: The account ID.
            access_token: New access token (will be encrypted).
            refresh_token: New refresh token (if provided).
            token_expiry: New token expiry time.
        """
        enc_access = self._encryptor.encrypt_value(access_token)

        if refresh_token is not None:
            enc_refresh = self._encryptor.encrypt_value(refresh_token)
            await self._execute(
                """
                UPDATE gmail_accounts
                SET access_token = $1, refresh_token = $2, token_expiry = $3
                WHERE id = $4
                """,
                enc_access,
                enc_refresh,
                token_expiry,
                account_id,
            )
        else:
            await self._execute(
                """
                UPDATE gmail_accounts
                SET access_token = $1, token_expiry = $2
                WHERE id = $3
                """,
                enc_access,
                token_expiry,
                account_id,
            )

        log.debug("tokens_updated", account_id=account_id)

    async def update_last_sync(self, account_id: int) -> None:
        """Update the last sync timestamp."""
        await self._execute(
            "UPDATE gmail_accounts SET last_sync = now() WHERE id = $1",
            account_id,
        )

    async def get_sync_state(self, account_id: int) -> dict[str, Any] | None:
        """Get the sync state for an account.

        Returns:
            Dict with history_id, last_full_sync, last_partial_sync.
        """
        row = await self._fetchrow(
            "SELECT * FROM gmail_sync_state WHERE account_id = $1",
            account_id,
        )
        if row is None:
            return None
        return dict(row)

    async def update_sync_state(
        self,
        account_id: int,
        *,
        history_id: str | None = None,
        full_sync: bool = False,
    ) -> None:
        """Update the sync state for an account.

        Args:
            account_id: The account ID.
            history_id: Latest history ID from Gmail.
            full_sync: Whether this was a full sync.
        """
        if full_sync:
            await self._execute(
                """
                UPDATE gmail_sync_state
                SET history_id = COALESCE($2, history_id),
                    last_full_sync = now(),
                    last_partial_sync = now()
                WHERE account_id = $1
                """,
                account_id,
                history_id,
            )
        else:
            await self._execute(
                """
                UPDATE gmail_sync_state
                SET history_id = COALESCE($2, history_id),
                    last_partial_sync = now()
                WHERE account_id = $1
                """,
                account_id,
                history_id,
            )

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _row_to_account(self, row: dict[str, Any], *, decrypt_tokens: bool = True) -> GmailAccount:
        """Convert a DB row to a GmailAccount."""
        access_token = ""  # nosec B105
        refresh_token = ""  # nosec B105

        if decrypt_tokens:
            try:
                access_token = self._encryptor.decrypt_value(row["access_token"])
            except (ValueError, Exception):
                log.warning("failed_to_decrypt_access_token", account_id=row.get("id"))
                access_token = ""  # nosec B105
            try:
                refresh_token = self._encryptor.decrypt_value(row["refresh_token"])
            except (ValueError, Exception):
                log.warning("failed_to_decrypt_refresh_token", account_id=row.get("id"))
                refresh_token = ""  # nosec B105

        return GmailAccount(
            id=row.get("id"),
            user_id=row["user_id"],
            email=row["email"],
            access_token=access_token,
            refresh_token=refresh_token,
            token_expiry=row.get("token_expiry"),
            scopes=row.get("scopes") or [],
            is_primary=row.get("is_primary", False),
            last_sync=row.get("last_sync"),
            created_at=row.get("created_at"),
        )

    async def _fetchval(self, query: str, *args: Any) -> Any:
        """Execute query and return first column of first row."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def _fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        """Execute query and return first row."""
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def _fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        """Execute query and return all rows."""
        async with self._pool.acquire() as conn:
            result: list[asyncpg.Record] = await conn.fetch(query, *args)
            return result

    async def _execute(self, query: str, *args: Any) -> str:
        """Execute query and return status string."""
        async with self._pool.acquire() as conn:
            result: str = await conn.execute(query, *args)
            return result
