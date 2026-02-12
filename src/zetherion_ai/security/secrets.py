"""PostgreSQL-backed encrypted secrets manager with in-memory caching.

Stores secrets encrypted with AES-256-GCM in a ``secrets`` table (created by
UserManager).  All reads go through the cache and are synchronous.  All writes
persist to the database first and then update the cache.

Typical lifecycle::

    encryptor = FieldEncryptor(key=key_manager.key)
    mgr = SecretsManager(encryptor=encryptor)
    await mgr.initialize(pool)

    # Fast synchronous reads (never blocks on the network)
    val = mgr.get("anthropic_api_key")

    # Async writes (encrypt + UPSERT + audit log)
    await mgr.set("anthropic_api_key", "sk-...", changed_by=123)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.security.encryption import FieldEncryptor

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

log = get_logger("zetherion_ai.security.secrets")


class SecretsManager:
    """In-memory-cached, encrypted PostgreSQL-backed secrets store.

    All *reads* go through the cache and are synchronous.  All *writes*
    encrypt the value, persist to the database, and then update the cache.
    """

    def __init__(self, encryptor: FieldEncryptor) -> None:
        self._encryptor = encryptor
        self._cache: dict[str, str] = {}  # name -> decrypted value
        self._pool: asyncpg.Pool | None = None

    async def initialize(self, pool: asyncpg.Pool) -> None:
        """Store the connection-pool reference and pre-load the cache.

        The ``secrets`` table is created by UserManager's schema DDL.

        Args:
            pool: An asyncpg.Pool already connected to the database.
        """
        self._pool = pool
        await self.refresh()
        log.info("secrets_manager_initialized", cached_secrets=len(self._cache))

    # ------------------------------------------------------------------
    # Synchronous read
    # ------------------------------------------------------------------

    def get(self, name: str, default: str | None = None) -> str | None:
        """Return the cached decrypted value, or *default*.

        This method **never** blocks on the database.
        """
        return self._cache.get(name, default)

    def list_names(self) -> list[str]:
        """Return the names of all stored secrets (never values)."""
        return sorted(self._cache.keys())

    # ------------------------------------------------------------------
    # Async write (encrypt + UPSERT + audit)
    # ------------------------------------------------------------------

    async def set(
        self,
        name: str,
        value: str,
        changed_by: int,
        description: str | None = None,
    ) -> None:
        """Encrypt and persist a secret, then update the cache.

        Args:
            name: Secret name (e.g. ``"anthropic_api_key"``).
            value: Plaintext secret value.
            changed_by: Discord user-id (or internal id) of who made the change.
            description: Optional human-readable description.
        """
        encrypted = self._encryptor.encrypt_value(value)

        try:
            async with self._pool.acquire() as conn, conn.transaction():  # type: ignore[union-attr]
                await conn.execute(
                    """
                    INSERT INTO secrets (name, value, description, updated_by)
                    VALUES ($1, $2, $3, $4)
                    ON CONFLICT (name) DO UPDATE
                        SET value       = EXCLUDED.value,
                            description = COALESCE(EXCLUDED.description, secrets.description),
                            updated_at  = NOW(),
                            updated_by  = EXCLUDED.updated_by
                    """,
                    name,
                    encrypted,
                    description,
                    changed_by,
                )

                await conn.execute(
                    """
                    INSERT INTO audit_log
                        (action, target_user_id, performed_by, reason)
                    VALUES ('secret_changed', 0, $1, $2)
                    """,
                    changed_by,
                    json.dumps({"name": name}),
                )

            # Update cache only after the DB transaction succeeds.
            self._cache[name] = value

            log.info("secret_set", name=name, changed_by=changed_by)
        except Exception:
            log.exception("secret_set_failed", name=name)
            raise

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete(self, name: str, deleted_by: int) -> bool:
        """Delete a secret from the database and the cache.

        Returns ``True`` if a row was actually deleted.
        """
        try:
            async with self._pool.acquire() as conn, conn.transaction():  # type: ignore[union-attr]
                result: str = await conn.execute(
                    "DELETE FROM secrets WHERE name = $1",
                    name,
                )

                await conn.execute(
                    """
                    INSERT INTO audit_log
                        (action, target_user_id, performed_by, reason)
                    VALUES ('secret_deleted', 0, $1, $2)
                    """,
                    deleted_by,
                    json.dumps({"name": name}),
                )

            self._cache.pop(name, None)

            deleted = result == "DELETE 1"
            log.info("secret_deleted", name=name, deleted_by=deleted_by, existed=deleted)
            return deleted
        except Exception:
            log.exception("secret_delete_failed", name=name)
            raise

    # ------------------------------------------------------------------
    # Cache refresh
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Reload all secrets from DB into the in-memory cache."""
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                rows = await conn.fetch("SELECT name, value FROM secrets")

            new_cache: dict[str, str] = {}
            for row in rows:
                try:
                    new_cache[row["name"]] = self._encryptor.decrypt_value(row["value"])
                except ValueError:
                    log.warning("secret_decrypt_failed", name=row["name"])

            self._cache = new_cache
            log.debug("secrets_cache_refreshed", count=len(self._cache))
        except Exception:
            log.exception("secrets_refresh_failed")
            raise

    # ------------------------------------------------------------------
    # Introspection (for admin commands)
    # ------------------------------------------------------------------

    async def get_metadata(self) -> list[dict[str, Any]]:
        """Return metadata about stored secrets (never the values).

        Returns:
            List of dicts with ``name``, ``description``, ``updated_at``,
            ``updated_by``.
        """
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                rows = await conn.fetch(
                    "SELECT name, description, created_at, updated_at, updated_by "
                    "FROM secrets ORDER BY name"
                )
            return [dict(row) for row in rows]
        except Exception:
            log.exception("secrets_get_metadata_failed")
            return []
