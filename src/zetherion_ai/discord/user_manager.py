"""PostgreSQL-backed RBAC user manager for Discord.

Provides role-based access control with an audit trail, backed by PostgreSQL
via asyncpg. Roles follow a strict hierarchy: owner > admin > user > restricted.
"""

from __future__ import annotations

from typing import Any

import asyncpg  # type: ignore[import-not-found]

from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.discord.user_manager")

# ---------------------------------------------------------------------------
# Role hierarchy – higher integer means more privilege
# ---------------------------------------------------------------------------
ROLE_HIERARCHY: dict[str, int] = {
    "owner": 4,
    "admin": 3,
    "user": 2,
    "restricted": 1,
}

VALID_ROLES = frozenset(ROLE_HIERARCHY.keys())

# ---------------------------------------------------------------------------
# SQL schema
# ---------------------------------------------------------------------------
_SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS users (
    discord_user_id  BIGINT       PRIMARY KEY,
    role             VARCHAR(20)  NOT NULL
                     CHECK (role IN ('owner', 'admin', 'user', 'restricted')),
    added_by         BIGINT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS audit_log (
    id               SERIAL       PRIMARY KEY,
    action           VARCHAR(50)  NOT NULL,
    target_user_id   BIGINT       NOT NULL,
    performed_by     BIGINT       NOT NULL,
    old_role         VARCHAR(20),
    new_role         VARCHAR(20),
    reason           TEXT,
    created_at       TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS settings (
    namespace    VARCHAR(50)  NOT NULL,
    key          VARCHAR(100) NOT NULL,
    value        TEXT,
    data_type    VARCHAR(20)  NOT NULL DEFAULT 'string'
                 CHECK (data_type IN ('string', 'int', 'float', 'bool', 'json')),
    description  TEXT,
    updated_at   TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_by   BIGINT,
    PRIMARY KEY (namespace, key)
);

CREATE INDEX IF NOT EXISTS idx_audit_log_target_user_id
    ON audit_log (target_user_id);
CREATE INDEX IF NOT EXISTS idx_audit_log_created_at
    ON audit_log (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_settings_namespace
    ON settings (namespace);

-- Phase 9: Personal understanding tables
CREATE TABLE IF NOT EXISTS personal_profile (
    user_id             BIGINT       PRIMARY KEY,
    display_name        TEXT,
    timezone            TEXT         NOT NULL DEFAULT 'UTC',
    locale              TEXT         NOT NULL DEFAULT 'en',
    working_hours       JSONB,
    communication_style JSONB,
    goals               JSONB        DEFAULT '[]'::jsonb,
    preferences         JSONB        DEFAULT '{}'::jsonb,
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS personal_contacts (
    id                  SERIAL       PRIMARY KEY,
    user_id             BIGINT       NOT NULL,
    contact_email       TEXT,
    contact_name        TEXT,
    relationship        TEXT         NOT NULL DEFAULT 'other',
    importance          FLOAT        NOT NULL DEFAULT 0.5,
    company             TEXT,
    notes               TEXT,
    last_interaction    TIMESTAMPTZ,
    interaction_count   INT          NOT NULL DEFAULT 0,
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, contact_email)
);

CREATE TABLE IF NOT EXISTS personal_policies (
    id                  SERIAL       PRIMARY KEY,
    user_id             BIGINT       NOT NULL,
    domain              TEXT         NOT NULL,
    action              TEXT         NOT NULL,
    mode                TEXT         NOT NULL DEFAULT 'ask',
    conditions          JSONB,
    trust_score         FLOAT        NOT NULL DEFAULT 0.0,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    UNIQUE (user_id, domain, action)
);

CREATE TABLE IF NOT EXISTS personal_learnings (
    id                  SERIAL       PRIMARY KEY,
    user_id             BIGINT       NOT NULL,
    category            TEXT         NOT NULL,
    content             TEXT         NOT NULL,
    confidence          FLOAT        NOT NULL,
    source              TEXT         NOT NULL,
    confirmed           BOOLEAN      NOT NULL DEFAULT FALSE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_personal_contacts_user_id
    ON personal_contacts (user_id);
CREATE INDEX IF NOT EXISTS idx_personal_policies_user_id
    ON personal_policies (user_id);
CREATE INDEX IF NOT EXISTS idx_personal_learnings_user_id
    ON personal_learnings (user_id);
CREATE INDEX IF NOT EXISTS idx_personal_learnings_category
    ON personal_learnings (user_id, category);

-- Phase 8: Gmail integration tables
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

CREATE TABLE IF NOT EXISTS gmail_drafts (
    id               SERIAL PRIMARY KEY,
    email_id         INT REFERENCES gmail_emails(id),
    account_id       INT REFERENCES gmail_accounts(id),
    draft_text       TEXT NOT NULL,
    reply_type       TEXT,
    confidence       FLOAT NOT NULL,
    status           TEXT DEFAULT 'pending',
    sent_at          TIMESTAMPTZ,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS gmail_type_trust (
    user_id          BIGINT NOT NULL,
    reply_type       TEXT NOT NULL,
    score            FLOAT DEFAULT 0.0,
    approvals        INT DEFAULT 0,
    rejections       INT DEFAULT 0,
    edits            INT DEFAULT 0,
    total_interactions INT DEFAULT 0,
    PRIMARY KEY (user_id, reply_type)
);

CREATE TABLE IF NOT EXISTS gmail_contact_trust (
    user_id          BIGINT NOT NULL,
    contact_email    TEXT NOT NULL,
    score            FLOAT DEFAULT 0.0,
    approvals        INT DEFAULT 0,
    rejections       INT DEFAULT 0,
    edits            INT DEFAULT 0,
    total_interactions INT DEFAULT 0,
    PRIMARY KEY (user_id, contact_email)
);
"""


class UserManager:
    """Manage Discord users with PostgreSQL-backed RBAC.

    The manager maintains a ``users`` table with role assignments, an
    ``audit_log`` for every mutation, and a ``settings`` key-value store.
    On first start (empty ``users`` table) it bootstraps the owner and
    seed users from the application configuration.
    """

    def __init__(self, dsn: str, *, allow_all: bool = False) -> None:
        """Initialise the user manager.

        Args:
            dsn: PostgreSQL connection string.
            allow_all: When ``True``, :meth:`is_allowed` always returns
                ``True`` regardless of database contents.  Used for test
                environments (``ALLOW_ALL_USERS=true``).
        """
        self._dsn = dsn
        self._allow_all = allow_all
        self._pool: asyncpg.Pool | None = None  # type: ignore[type-arg]

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> None:
        """Create the connection pool, ensure the schema exists, and bootstrap seed users."""
        try:
            self._pool = await asyncpg.create_pool(dsn=self._dsn)
            log.info("postgres_pool_created", dsn=self._dsn.split("@")[-1])
        except (asyncpg.PostgresError, OSError) as exc:
            log.error("postgres_pool_creation_failed", error=str(exc))
            raise

        await self._ensure_schema()
        await self._bootstrap()

    async def close(self) -> None:
        """Gracefully close the connection pool."""
        if self._pool is not None:
            await self._pool.close()
            self._pool = None
            log.info("postgres_pool_closed")

    # ------------------------------------------------------------------
    # Public API – queries
    # ------------------------------------------------------------------

    async def is_allowed(self, user_id: int) -> bool:
        """Return ``True`` if *user_id* exists in the users table (any role).

        When ``allow_all`` was set at construction time this always returns
        ``True``.

        Args:
            user_id: Discord user ID to check.

        Returns:
            Whether the user is present in the RBAC table.
        """
        if self._allow_all:
            return True
        try:
            row = await self._fetchval(
                "SELECT 1 FROM users WHERE discord_user_id = $1",
                user_id,
            )
            return row is not None
        except asyncpg.PostgresError as exc:
            log.error("is_allowed_query_failed", user_id=user_id, error=str(exc))
            return False

    async def get_role(self, user_id: int) -> str | None:
        """Return the role string for *user_id*, or ``None`` if not found.

        Args:
            user_id: Discord user ID.

        Returns:
            Role name or ``None``.
        """
        try:
            role: str | None = await self._fetchval(
                "SELECT role FROM users WHERE discord_user_id = $1",
                user_id,
            )
            return role
        except asyncpg.PostgresError as exc:
            log.error("get_role_query_failed", user_id=user_id, error=str(exc))
            return None

    async def list_users(self, role_filter: str | None = None) -> list[dict[str, Any]]:
        """Return a list of user records, optionally filtered by role.

        Args:
            role_filter: If provided, only return users with this role.

        Returns:
            List of dicts with keys ``discord_user_id``, ``role``,
            ``added_by``, ``created_at``, ``updated_at``.
        """
        try:
            if role_filter is not None:
                if role_filter not in VALID_ROLES:
                    log.warning("list_users_invalid_role_filter", role=role_filter)
                    return []
                rows = await self._fetch(
                    "SELECT * FROM users WHERE role = $1 ORDER BY created_at",
                    role_filter,
                )
            else:
                rows = await self._fetch("SELECT * FROM users ORDER BY created_at")
            return [dict(row) for row in rows]
        except asyncpg.PostgresError as exc:
            log.error("list_users_query_failed", error=str(exc))
            return []

    async def get_audit_log(self, limit: int = 50) -> list[dict[str, Any]]:
        """Return the most recent audit log entries.

        Args:
            limit: Maximum number of entries to return (default 50).

        Returns:
            List of audit-log dicts ordered newest-first.
        """
        try:
            rows = await self._fetch(
                "SELECT * FROM audit_log ORDER BY created_at DESC LIMIT $1",
                limit,
            )
            return [dict(row) for row in rows]
        except asyncpg.PostgresError as exc:
            log.error("get_audit_log_query_failed", error=str(exc))
            return []

    # ------------------------------------------------------------------
    # Public API – mutations
    # ------------------------------------------------------------------

    async def add_user(self, user_id: int, role: str, added_by: int) -> bool:
        """Insert a new user with the given role.

        The caller (``added_by``) must hold a strictly higher role level
        than the target role being assigned.

        Args:
            user_id: Discord user ID to add.
            role: Role to assign (must be in :data:`VALID_ROLES`).
            added_by: Discord user ID of the caller performing the action.

        Returns:
            ``True`` if the user was successfully added, ``False`` otherwise.
        """
        if role not in VALID_ROLES:
            log.warning("add_user_invalid_role", role=role, user_id=user_id)
            return False

        caller_role = await self.get_role(added_by)
        if caller_role is None:
            log.warning("add_user_caller_not_found", added_by=added_by)
            return False

        if ROLE_HIERARCHY.get(caller_role, 0) <= ROLE_HIERARCHY[role]:
            log.warning(
                "add_user_insufficient_privilege",
                caller_role=caller_role,
                target_role=role,
                added_by=added_by,
            )
            return False

        try:
            async with self._pool.acquire() as conn, conn.transaction():  # type: ignore[union-attr]
                await conn.execute(
                    """
                        INSERT INTO users (discord_user_id, role, added_by)
                        VALUES ($1, $2, $3)
                        ON CONFLICT (discord_user_id) DO NOTHING
                        """,
                    user_id,
                    role,
                    added_by,
                )
                await conn.execute(
                    """
                        INSERT INTO audit_log (action, target_user_id, performed_by, new_role)
                        VALUES ('add_user', $1, $2, $3)
                        """,
                    user_id,
                    added_by,
                    role,
                )
            log.info("user_added", user_id=user_id, role=role, added_by=added_by)
            return True
        except asyncpg.PostgresError as exc:
            log.error("add_user_failed", user_id=user_id, error=str(exc))
            return False

    async def remove_user(self, user_id: int, removed_by: int) -> bool:
        """Remove a user from the RBAC table.

        Owners cannot be removed.

        Args:
            user_id: Discord user ID to remove.
            removed_by: Discord user ID of the caller performing the action.

        Returns:
            ``True`` if the user was removed, ``False`` otherwise.
        """
        target_role = await self.get_role(user_id)
        if target_role is None:
            log.warning("remove_user_not_found", user_id=user_id)
            return False

        if target_role == "owner":
            log.warning("remove_user_owner_rejected", user_id=user_id, removed_by=removed_by)
            return False

        caller_role = await self.get_role(removed_by)
        if caller_role is None:
            log.warning("remove_user_caller_not_found", removed_by=removed_by)
            return False

        if ROLE_HIERARCHY.get(caller_role, 0) <= ROLE_HIERARCHY.get(target_role, 0):
            log.warning(
                "remove_user_insufficient_privilege",
                caller_role=caller_role,
                target_role=target_role,
                removed_by=removed_by,
            )
            return False

        try:
            async with self._pool.acquire() as conn, conn.transaction():  # type: ignore[union-attr]
                await conn.execute(
                    "DELETE FROM users WHERE discord_user_id = $1",
                    user_id,
                )
                await conn.execute(
                    """
                        INSERT INTO audit_log
                            (action, target_user_id, performed_by, old_role)
                        VALUES ('remove_user', $1, $2, $3)
                        """,
                    user_id,
                    removed_by,
                    target_role,
                )
            log.info("user_removed", user_id=user_id, old_role=target_role, removed_by=removed_by)
            return True
        except asyncpg.PostgresError as exc:
            log.error("remove_user_failed", user_id=user_id, error=str(exc))
            return False

    async def set_role(self, user_id: int, new_role: str, changed_by: int) -> bool:
        """Change a user's role.

        The caller must hold a strictly higher role level than **both** the
        user's current role and the requested new role (i.e. you cannot
        promote someone to your own level or above).

        Args:
            user_id: Discord user ID whose role to change.
            new_role: New role to assign.
            changed_by: Discord user ID of the caller performing the action.

        Returns:
            ``True`` if the role was changed, ``False`` otherwise.
        """
        if new_role not in VALID_ROLES:
            log.warning("set_role_invalid_role", new_role=new_role, user_id=user_id)
            return False

        old_role = await self.get_role(user_id)
        if old_role is None:
            log.warning("set_role_user_not_found", user_id=user_id)
            return False

        if old_role == new_role:
            log.info("set_role_no_change", user_id=user_id, role=old_role)
            return True

        caller_role = await self.get_role(changed_by)
        if caller_role is None:
            log.warning("set_role_caller_not_found", changed_by=changed_by)
            return False

        caller_level = ROLE_HIERARCHY.get(caller_role, 0)
        if (
            caller_level <= ROLE_HIERARCHY.get(old_role, 0)
            or caller_level <= ROLE_HIERARCHY[new_role]
        ):
            log.warning(
                "set_role_insufficient_privilege",
                caller_role=caller_role,
                old_role=old_role,
                new_role=new_role,
                changed_by=changed_by,
            )
            return False

        try:
            async with self._pool.acquire() as conn, conn.transaction():  # type: ignore[union-attr]
                await conn.execute(
                    """
                        UPDATE users
                           SET role = $1, updated_at = now()
                         WHERE discord_user_id = $2
                        """,
                    new_role,
                    user_id,
                )
                await conn.execute(
                    """
                        INSERT INTO audit_log
                            (action, target_user_id, performed_by, old_role, new_role)
                        VALUES ('set_role', $1, $2, $3, $4)
                        """,
                    user_id,
                    changed_by,
                    old_role,
                    new_role,
                )
            log.info(
                "role_changed",
                user_id=user_id,
                old_role=old_role,
                new_role=new_role,
                changed_by=changed_by,
            )
            return True
        except asyncpg.PostgresError as exc:
            log.error("set_role_failed", user_id=user_id, error=str(exc))
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _ensure_schema(self) -> None:
        """Run the DDL statements to create tables and indexes if absent."""
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                await conn.execute(_SCHEMA_SQL)
            log.info("schema_ensured")
        except asyncpg.PostgresError as exc:
            log.error("schema_creation_failed", error=str(exc))
            raise

    async def _bootstrap(self) -> None:
        """Seed the users table from application settings when it is empty.

        If the table already contains rows the bootstrap is skipped entirely,
        making this safe to call on every startup.
        """
        try:
            count = await self._fetchval("SELECT count(*) FROM users")
            if count and int(count) > 0:
                log.info("bootstrap_skipped", existing_user_count=count)
                return

            settings = get_settings()

            owner_id = settings.owner_user_id
            if owner_id is not None:
                await self._execute(
                    """
                    INSERT INTO users (discord_user_id, role, added_by)
                    VALUES ($1, 'owner', $1)
                    ON CONFLICT (discord_user_id) DO NOTHING
                    """,
                    owner_id,
                )
                log.info("bootstrap_owner_created", owner_id=owner_id)

            seed_ids = settings.allowed_user_ids
            for uid in seed_ids:
                if uid == owner_id:
                    continue  # already inserted as owner
                await self._execute(
                    """
                    INSERT INTO users (discord_user_id, role, added_by)
                    VALUES ($1, 'user', $2)
                    ON CONFLICT (discord_user_id) DO NOTHING
                    """,
                    uid,
                    owner_id or 0,
                )
            if seed_ids:
                log.info(
                    "bootstrap_seed_users_created",
                    count=len([uid for uid in seed_ids if uid != owner_id]),
                )

            log.info("bootstrap_complete")
        except asyncpg.PostgresError as exc:
            log.error("bootstrap_failed", error=str(exc))
            raise

    # ------------------------------------------------------------------
    # Pool convenience wrappers
    # ------------------------------------------------------------------

    async def _fetchval(self, query: str, *args: Any) -> Any:
        """Execute *query* and return the first column of the first row."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            return await conn.fetchval(query, *args)

    async def _fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        """Execute *query* and return all result rows."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            result: list[asyncpg.Record] = await conn.fetch(query, *args)
            return result

    async def _execute(self, query: str, *args: Any) -> str:
        """Execute *query* and return the status string."""
        async with self._pool.acquire() as conn:  # type: ignore[union-attr]
            result: str = await conn.execute(query, *args)
            return result
