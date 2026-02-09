"""PostgreSQL storage for the personal understanding layer.

Provides CRUD operations for personal profiles, contacts, action policies,
and learning records. Shares the asyncpg connection pool with UserManager.
"""

from __future__ import annotations

import json
from typing import Any

import asyncpg  # type: ignore[import-not-found]

from zetherion_ai.logging import get_logger
from zetherion_ai.personal.models import (
    PersonalContact,
    PersonalLearning,
    PersonalPolicy,
    PersonalProfile,
)

log = get_logger("zetherion_ai.personal.storage")

# ---------------------------------------------------------------------------
# SQL schema for Phase 9 tables
# ---------------------------------------------------------------------------

PERSONAL_SCHEMA_SQL = """\
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
"""


class PersonalStorage:
    """PostgreSQL-backed storage for the personal model.

    Accepts an existing asyncpg pool (shared with UserManager) so
    there is only one connection pool per process.
    """

    def __init__(self, pool: asyncpg.Pool) -> None:  # type: ignore[type-arg]
        self._pool: asyncpg.Pool = pool  # type: ignore[type-arg]

    async def ensure_schema(self) -> None:
        """Create Phase 9 tables and indexes if they don't exist."""
        try:
            async with self._pool.acquire() as conn:
                await conn.execute(PERSONAL_SCHEMA_SQL)
            log.info("personal_schema_ensured")
        except asyncpg.PostgresError as exc:
            log.error("personal_schema_creation_failed", error=str(exc))
            raise

    # ------------------------------------------------------------------
    # Profile CRUD
    # ------------------------------------------------------------------

    async def get_profile(self, user_id: int) -> PersonalProfile | None:
        """Fetch the profile for *user_id*, or ``None`` if not found."""
        row = await self._fetchrow(
            "SELECT * FROM personal_profile WHERE user_id = $1",
            user_id,
        )
        if row is None:
            return None
        return PersonalProfile.from_db_row(dict(row))

    async def upsert_profile(self, profile: PersonalProfile) -> None:
        """Insert or update a personal profile."""
        data = profile.to_db_row()
        await self._execute(
            """
            INSERT INTO personal_profile
                (user_id, display_name, timezone, locale, working_hours,
                 communication_style, goals, preferences, updated_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6::jsonb, $7::jsonb, $8::jsonb, now())
            ON CONFLICT (user_id) DO UPDATE SET
                display_name = EXCLUDED.display_name,
                timezone = EXCLUDED.timezone,
                locale = EXCLUDED.locale,
                working_hours = EXCLUDED.working_hours,
                communication_style = EXCLUDED.communication_style,
                goals = EXCLUDED.goals,
                preferences = EXCLUDED.preferences,
                updated_at = now()
            """,
            data["user_id"],
            data["display_name"],
            data["timezone"],
            data["locale"],
            json.dumps(data["working_hours"]) if data["working_hours"] else None,
            json.dumps(data["communication_style"]) if data["communication_style"] else None,
            json.dumps(data["goals"]),
            json.dumps(data["preferences"]),
        )
        log.info("profile_upserted", user_id=profile.user_id)

    async def delete_profile(self, user_id: int) -> bool:
        """Delete a profile. Returns ``True`` if a row was deleted."""
        result = await self._execute(
            "DELETE FROM personal_profile WHERE user_id = $1",
            user_id,
        )
        deleted: bool = result == "DELETE 1"
        if deleted:
            log.info("profile_deleted", user_id=user_id)
        return deleted

    # ------------------------------------------------------------------
    # Contact CRUD
    # ------------------------------------------------------------------

    async def get_contact(self, user_id: int, contact_email: str) -> PersonalContact | None:
        """Fetch a specific contact by email."""
        row = await self._fetchrow(
            "SELECT * FROM personal_contacts WHERE user_id = $1 AND contact_email = $2",
            user_id,
            contact_email,
        )
        if row is None:
            return None
        return PersonalContact.from_db_row(dict(row))

    async def get_contact_by_id(self, contact_id: int) -> PersonalContact | None:
        """Fetch a specific contact by its primary key."""
        row = await self._fetchrow(
            "SELECT * FROM personal_contacts WHERE id = $1",
            contact_id,
        )
        if row is None:
            return None
        return PersonalContact.from_db_row(dict(row))

    async def list_contacts(
        self,
        user_id: int,
        *,
        relationship: str | None = None,
        min_importance: float | None = None,
        limit: int = 100,
    ) -> list[PersonalContact]:
        """List contacts for a user with optional filters."""
        query = "SELECT * FROM personal_contacts WHERE user_id = $1"
        params: list[Any] = [user_id]
        idx = 2

        if relationship is not None:
            query += f" AND relationship = ${idx}"
            params.append(relationship)
            idx += 1

        if min_importance is not None:
            query += f" AND importance >= ${idx}"
            params.append(min_importance)
            idx += 1

        query += f" ORDER BY importance DESC, interaction_count DESC LIMIT ${idx}"
        params.append(limit)

        rows = await self._fetch(query, *params)
        return [PersonalContact.from_db_row(dict(r)) for r in rows]

    async def upsert_contact(self, contact: PersonalContact) -> int:
        """Insert or update a contact. Returns the contact ID."""
        data = contact.to_db_row()
        row_id = await self._fetchval(
            """
            INSERT INTO personal_contacts
                (user_id, contact_email, contact_name, relationship, importance,
                 company, notes, last_interaction, interaction_count, updated_at)
            VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, now())
            ON CONFLICT (user_id, contact_email) DO UPDATE SET
                contact_name = COALESCE(EXCLUDED.contact_name, personal_contacts.contact_name),
                relationship = EXCLUDED.relationship,
                importance = EXCLUDED.importance,
                company = COALESCE(EXCLUDED.company, personal_contacts.company),
                notes = COALESCE(EXCLUDED.notes, personal_contacts.notes),
                last_interaction = EXCLUDED.last_interaction,
                interaction_count = EXCLUDED.interaction_count,
                updated_at = now()
            RETURNING id
            """,
            data["user_id"],
            data["contact_email"],
            data["contact_name"],
            data["relationship"],
            data["importance"],
            data["company"],
            data["notes"],
            data["last_interaction"],
            data["interaction_count"],
        )
        log.info(
            "contact_upserted",
            user_id=contact.user_id,
            contact_email=contact.contact_email,
            contact_id=row_id,
        )
        return int(row_id)

    async def delete_contact(self, user_id: int, contact_email: str) -> bool:
        """Delete a contact by email. Returns ``True`` if deleted."""
        result = await self._execute(
            "DELETE FROM personal_contacts WHERE user_id = $1 AND contact_email = $2",
            user_id,
            contact_email,
        )
        deleted: bool = result == "DELETE 1"
        if deleted:
            log.info("contact_deleted", user_id=user_id, contact_email=contact_email)
        return deleted

    async def increment_contact_interaction(self, user_id: int, contact_email: str) -> None:
        """Bump the interaction count and last_interaction timestamp."""
        await self._execute(
            """
            UPDATE personal_contacts
               SET interaction_count = interaction_count + 1,
                   last_interaction = now(),
                   updated_at = now()
             WHERE user_id = $1 AND contact_email = $2
            """,
            user_id,
            contact_email,
        )

    # ------------------------------------------------------------------
    # Policy CRUD
    # ------------------------------------------------------------------

    async def get_policy(self, user_id: int, domain: str, action: str) -> PersonalPolicy | None:
        """Fetch a specific policy."""
        row = await self._fetchrow(
            "SELECT * FROM personal_policies WHERE user_id = $1 AND domain = $2 AND action = $3",
            user_id,
            domain,
            action,
        )
        if row is None:
            return None
        return PersonalPolicy.from_db_row(dict(row))

    async def list_policies(
        self, user_id: int, *, domain: str | None = None
    ) -> list[PersonalPolicy]:
        """List policies for a user, optionally filtered by domain."""
        if domain is not None:
            rows = await self._fetch(
                "SELECT * FROM personal_policies"
                " WHERE user_id = $1 AND domain = $2 ORDER BY action",
                user_id,
                domain,
            )
        else:
            rows = await self._fetch(
                "SELECT * FROM personal_policies WHERE user_id = $1 ORDER BY domain, action",
                user_id,
            )
        return [PersonalPolicy.from_db_row(dict(r)) for r in rows]

    async def upsert_policy(self, policy: PersonalPolicy) -> int:
        """Insert or update an action policy. Returns the policy ID."""
        data = policy.to_db_row()
        row_id = await self._fetchval(
            """
            INSERT INTO personal_policies
                (user_id, domain, action, mode, conditions, trust_score, created_at, updated_at)
            VALUES ($1, $2, $3, $4, $5::jsonb, $6, now(), now())
            ON CONFLICT (user_id, domain, action) DO UPDATE SET
                mode = EXCLUDED.mode,
                conditions = EXCLUDED.conditions,
                trust_score = EXCLUDED.trust_score,
                updated_at = now()
            RETURNING id
            """,
            data["user_id"],
            data["domain"],
            data["action"],
            data["mode"],
            json.dumps(data["conditions"]) if data["conditions"] else None,
            data["trust_score"],
        )
        log.info(
            "policy_upserted",
            user_id=policy.user_id,
            domain=policy.domain.value,
            action=policy.action,
            policy_id=row_id,
        )
        return int(row_id)

    async def delete_policy(self, user_id: int, domain: str, action: str) -> bool:
        """Delete a specific policy. Returns ``True`` if deleted."""
        result = await self._execute(
            "DELETE FROM personal_policies WHERE user_id = $1 AND domain = $2 AND action = $3",
            user_id,
            domain,
            action,
        )
        deleted: bool = result == "DELETE 1"
        if deleted:
            log.info("policy_deleted", user_id=user_id, domain=domain, action=action)
        return deleted

    async def update_trust_score(
        self, user_id: int, domain: str, action: str, delta: float
    ) -> float | None:
        """Adjust the trust score for a policy by *delta*, clamped to [0.0, 0.95].

        Returns the new trust score, or ``None`` if the policy doesn't exist.
        """
        row = await self._fetchval(
            """
            UPDATE personal_policies
               SET trust_score = GREATEST(0.0, LEAST(0.95, trust_score + $4)),
                   updated_at = now()
             WHERE user_id = $1 AND domain = $2 AND action = $3
            RETURNING trust_score
            """,
            user_id,
            domain,
            action,
            delta,
        )
        if row is not None:
            new_score = float(row)
            log.info(
                "trust_score_updated",
                user_id=user_id,
                domain=domain,
                action=action,
                delta=delta,
                new_score=new_score,
            )
            return new_score
        return None

    async def reset_domain_trust(self, user_id: int, domain: str) -> int:
        """Reset all trust scores for a domain to 0.0. Returns count of affected rows."""
        result = await self._execute(
            """
            UPDATE personal_policies
               SET trust_score = 0.0, updated_at = now()
             WHERE user_id = $1 AND domain = $2
            """,
            user_id,
            domain,
        )
        # result looks like "UPDATE N"
        count = int(result.split()[-1]) if result else 0
        if count > 0:
            log.info("domain_trust_reset", user_id=user_id, domain=domain, count=count)
        return count

    # ------------------------------------------------------------------
    # Learning CRUD
    # ------------------------------------------------------------------

    async def add_learning(self, learning: PersonalLearning) -> int:
        """Insert a learning record. Returns the learning ID."""
        data = learning.to_db_row()
        row_id = await self._fetchval(
            """
            INSERT INTO personal_learnings
                (user_id, category, content, confidence, source, confirmed, created_at)
            VALUES ($1, $2, $3, $4, $5, $6, now())
            RETURNING id
            """,
            data["user_id"],
            data["category"],
            data["content"],
            data["confidence"],
            data["source"],
            data["confirmed"],
        )
        log.info(
            "learning_added",
            user_id=learning.user_id,
            category=learning.category.value,
            learning_id=row_id,
        )
        return int(row_id)

    async def list_learnings(
        self,
        user_id: int,
        *,
        category: str | None = None,
        confirmed_only: bool = False,
        min_confidence: float | None = None,
        limit: int = 100,
    ) -> list[PersonalLearning]:
        """List learning records with optional filters."""
        query = "SELECT * FROM personal_learnings WHERE user_id = $1"
        params: list[Any] = [user_id]
        idx = 2

        if category is not None:
            query += f" AND category = ${idx}"
            params.append(category)
            idx += 1

        if confirmed_only:
            query += " AND confirmed = TRUE"

        if min_confidence is not None:
            query += f" AND confidence >= ${idx}"
            params.append(min_confidence)
            idx += 1

        query += f" ORDER BY created_at DESC LIMIT ${idx}"
        params.append(limit)

        rows = await self._fetch(query, *params)
        return [PersonalLearning.from_db_row(dict(r)) for r in rows]

    async def confirm_learning(self, learning_id: int) -> bool:
        """Mark a learning as confirmed by the user."""
        result = await self._execute(
            "UPDATE personal_learnings SET confirmed = TRUE WHERE id = $1",
            learning_id,
        )
        confirmed: bool = result == "UPDATE 1"
        if confirmed:
            log.info("learning_confirmed", learning_id=learning_id)
        return confirmed

    async def delete_learning(self, learning_id: int) -> bool:
        """Delete a specific learning record."""
        result = await self._execute(
            "DELETE FROM personal_learnings WHERE id = $1",
            learning_id,
        )
        deleted: bool = result == "DELETE 1"
        if deleted:
            log.info("learning_deleted", learning_id=learning_id)
        return deleted

    async def delete_learnings_by_category(self, user_id: int, category: str) -> int:
        """Delete all learnings in a category. Returns count of deleted rows."""
        result = await self._execute(
            "DELETE FROM personal_learnings WHERE user_id = $1 AND category = $2",
            user_id,
            category,
        )
        count = int(result.split()[-1]) if result else 0
        if count > 0:
            log.info(
                "learnings_deleted_by_category",
                user_id=user_id,
                category=category,
                count=count,
            )
        return count

    # ------------------------------------------------------------------
    # Pool convenience wrappers
    # ------------------------------------------------------------------

    async def _fetchval(self, query: str, *args: Any) -> Any:
        """Execute *query* and return the first column of the first row."""
        async with self._pool.acquire() as conn:
            return await conn.fetchval(query, *args)

    async def _fetchrow(self, query: str, *args: Any) -> asyncpg.Record | None:
        """Execute *query* and return the first row."""
        async with self._pool.acquire() as conn:
            return await conn.fetchrow(query, *args)

    async def _fetch(self, query: str, *args: Any) -> list[asyncpg.Record]:
        """Execute *query* and return all result rows."""
        async with self._pool.acquire() as conn:
            result: list[asyncpg.Record] = await conn.fetch(query, *args)
            return result

    async def _execute(self, query: str, *args: Any) -> str:
        """Execute *query* and return the status string."""
        async with self._pool.acquire() as conn:
            result: str = await conn.execute(query, *args)
            return result
