"""PostgreSQL-backed runtime settings manager with in-memory caching.

The ``settings`` and ``audit_log`` tables are created by UserManager (which
shares the same database pool).  This module therefore does **not** issue any
``CREATE TABLE`` statements -- it only reads from and writes to those tables.

Typical lifecycle::

    pool = await asyncpg.create_pool(...)
    mgr  = SettingsManager()
    await mgr.initialize(pool)

    # Fast synchronous reads (never blocks on the network)
    val = mgr.get("models", "default_provider", default="anthropic")

    # Async writes (UPSERT + audit log)
    await mgr.set("models", "default_provider", "openai", changed_by=1)
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

log = get_logger("zetherion_ai.settings_manager")

# ------------------------------------------------------------------
# Allowed namespaces -- every setting must belong to one of these.
# ------------------------------------------------------------------
VALID_NAMESPACES: frozenset[str] = frozenset(
    {
        "models",
        "budgets",
        "notifications",
        "profile",
        "scheduler",
        "tuning",
        "logging",
        "security",
        "queue",
    }
)


class SettingsManager:
    """In-memory-cached, PostgreSQL-backed runtime settings store.

    All *reads* go through the cache and are synchronous.  All *writes*
    persist to the database first and then update the cache, so the two
    never diverge under normal operation.
    """

    # ------------------------------------------------------------------
    # Construction / initialisation
    # ------------------------------------------------------------------

    def __init__(self) -> None:
        self._cache: dict[tuple[str, str], Any] = {}
        self._cache_loaded: bool = False
        self._pool: asyncpg.Pool | None = None

    async def initialize(self, pool: asyncpg.Pool) -> None:
        """Store the connection-pool reference and pre-load the cache.

        Parameters
        ----------
        pool:
            An ``asyncpg.Pool`` that is already connected to the database
            containing the ``settings`` and ``audit_log`` tables.
        """
        self._pool = pool
        await self.refresh()
        log.info("settings_manager.initialized", cached_keys=len(self._cache))

    # ------------------------------------------------------------------
    # Synchronous read
    # ------------------------------------------------------------------

    def get(self, namespace: str, key: str, default: Any = None) -> Any:
        """Return the cached value for *namespace*/*key*, or *default*.

        This method **never** blocks on the database.  If the cache has
        not yet been loaded the caller simply receives *default*.
        """
        return self._cache.get((namespace, key), default)

    # ------------------------------------------------------------------
    # Async write (UPSERT + audit)
    # ------------------------------------------------------------------

    async def set(
        self,
        namespace: str,
        key: str,
        value: Any,
        changed_by: int,
        data_type: str = "string",
        description: str | None = None,
    ) -> None:
        """Persist a setting via UPSERT and update the local cache.

        Parameters
        ----------
        namespace:
            Must be one of :data:`VALID_NAMESPACES`.
        key:
            Arbitrary setting key within the namespace.
        value:
            The value to store.  It is converted to ``str`` for storage.
        changed_by:
            Discord user-id (or internal id) of the person making the change.
        data_type:
            One of ``"string"``, ``"int"``, ``"float"``, ``"bool"``,
            ``"json"``.  Used when reading the value back to coerce the
            stored string into the correct Python type.
        description:
            Optional human-readable description of the setting.
        """
        if namespace not in VALID_NAMESPACES:
            raise ValueError(
                f"Invalid namespace {namespace!r}. Must be one of {sorted(VALID_NAMESPACES)}"
            )

        str_value = json.dumps(value) if data_type == "json" else str(value)

        try:
            async with self._pool.acquire() as conn, conn.transaction():  # type: ignore[union-attr]
                await conn.execute(
                    """
                        INSERT INTO settings (
                            namespace,
                            key,
                            value,
                            data_type,
                            description,
                            updated_by
                        )
                        VALUES ($1, $2, $3, $4, $5, $6)
                        ON CONFLICT (namespace, key) DO UPDATE
                            SET value       = EXCLUDED.value,
                                data_type   = EXCLUDED.data_type,
                                description = COALESCE(EXCLUDED.description, settings.description),
                                updated_by  = EXCLUDED.updated_by,
                                updated_at  = NOW()
                        """,
                    namespace,
                    key,
                    str_value,
                    data_type,
                    description,
                    changed_by,
                )

                await conn.execute(
                    """
                        INSERT INTO audit_log
                            (action, target_user_id, performed_by, reason)
                        VALUES ('setting_changed', 0, $1, $2)
                        """,
                    changed_by,
                    json.dumps(
                        {
                            "namespace": namespace,
                            "key": key,
                            "value": str_value,
                            "data_type": data_type,
                        }
                    ),
                )

            # Update cache only after the DB transaction succeeds.
            self._cache[(namespace, key)] = self._coerce_value(str_value, data_type)

            log.info(
                "settings_manager.set",
                namespace=namespace,
                key=key,
                data_type=data_type,
                changed_by=changed_by,
            )
        except Exception:
            log.exception(
                "settings_manager.set_failed",
                namespace=namespace,
                key=key,
            )
            raise

    # ------------------------------------------------------------------
    # Cache refresh
    # ------------------------------------------------------------------

    async def refresh(self) -> None:
        """Reload the full settings table into the in-memory cache."""
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                rows = await conn.fetch("SELECT namespace, key, value, data_type FROM settings")

            new_cache: dict[tuple[str, str], Any] = {}
            for row in rows:
                try:
                    coerced = self._coerce_value(row["value"], row["data_type"])
                except Exception:
                    log.warning(
                        "settings_manager.coerce_failed",
                        namespace=row["namespace"],
                        key=row["key"],
                        data_type=row["data_type"],
                    )
                    coerced = row["value"]  # fall back to raw string

                new_cache[(row["namespace"], row["key"])] = coerced

            self._cache = new_cache
            self._cache_loaded = True

            log.debug("settings_manager.refreshed", total_keys=len(self._cache))
        except Exception:
            log.exception("settings_manager.refresh_failed")
            raise

    # ------------------------------------------------------------------
    # Bulk read
    # ------------------------------------------------------------------

    async def get_all(self, namespace: str | None = None) -> dict[str, dict[str, Any]]:
        """Return all settings, optionally filtered by *namespace*.

        Returns
        -------
        dict[str, dict[str, Any]]
            ``{namespace: {key: value, ...}, ...}``
        """
        try:
            async with self._pool.acquire() as conn:  # type: ignore[union-attr]
                if namespace is not None:
                    rows = await conn.fetch(
                        "SELECT namespace, key, value, data_type FROM settings "
                        "WHERE namespace = $1",
                        namespace,
                    )
                else:
                    rows = await conn.fetch("SELECT namespace, key, value, data_type FROM settings")

            result: dict[str, dict[str, Any]] = {}
            for row in rows:
                ns = row["namespace"]
                try:
                    coerced = self._coerce_value(row["value"], row["data_type"])
                except Exception:
                    coerced = row["value"]
                result.setdefault(ns, {})[row["key"]] = coerced

            return result
        except Exception:
            log.exception("settings_manager.get_all_failed", namespace=namespace)
            raise

    # ------------------------------------------------------------------
    # Delete
    # ------------------------------------------------------------------

    async def delete(self, namespace: str, key: str, deleted_by: int) -> bool:
        """Delete a setting from the database and the cache.

        Returns ``True`` if a row was actually deleted, ``False`` if the
        setting did not exist.
        """
        try:
            async with self._pool.acquire() as conn, conn.transaction():  # type: ignore[union-attr]
                result = await conn.execute(
                    "DELETE FROM settings WHERE namespace = $1 AND key = $2",
                    namespace,
                    key,
                )

                await conn.execute(
                    """
                        INSERT INTO audit_log
                            (action, target_user_id, performed_by, reason)
                        VALUES ('setting_deleted', 0, $1, $2)
                        """,
                    deleted_by,
                    json.dumps(
                        {
                            "namespace": namespace,
                            "key": key,
                        }
                    ),
                )

            self._cache.pop((namespace, key), None)

            deleted: bool = result == "DELETE 1"
            log.info(
                "settings_manager.deleted",
                namespace=namespace,
                key=key,
                deleted_by=deleted_by,
                existed=deleted,
            )
            return deleted
        except Exception:
            log.exception(
                "settings_manager.delete_failed",
                namespace=namespace,
                key=key,
            )
            raise

    # ------------------------------------------------------------------
    # Type coercion
    # ------------------------------------------------------------------

    @staticmethod
    def _coerce_value(value: str, data_type: str) -> Any:
        """Coerce a raw string *value* into the Python type indicated by *data_type*.

        Supported ``data_type`` values:

        * ``"string"`` -- pass-through
        * ``"int"``    -- ``int(value)``
        * ``"float"``  -- ``float(value)``
        * ``"bool"``   -- ``value.lower() in ("true", "1", "yes")``
        * ``"json"``   -- ``json.loads(value)``
        """
        if data_type == "string":
            return value
        if data_type == "int":
            return int(value)
        if data_type == "float":
            return float(value)
        if data_type == "bool":
            return value.lower() in ("true", "1", "yes")
        if data_type == "json":
            return json.loads(value)
        # Unknown data_type -- return the raw string as a safe default.
        return value
