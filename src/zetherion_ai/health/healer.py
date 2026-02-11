"""Safe, in-process self-healing actions.

Every action in this module operates *inside* the running Python process.
No Docker restarts, no subprocess calls, no destructive operations.
Container-level recovery is left to Docker's ``restart: unless-stopped``
policy.
"""

from __future__ import annotations

from datetime import datetime
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

    from zetherion_ai.health.storage import HealthStorage
    from zetherion_ai.skills.registry import SkillRegistry

log = get_logger("zetherion_ai.health.healer")

# Minimum seconds between repeated attempts of the same action type
_DEFAULT_COOLDOWN_SECONDS = 300


class SelfHealer:
    """Execute recovery actions when anomalies are detected.

    Each public method is a self-contained healing action.  All actions:
    - Are logged to ``HealthStorage.health_healing_actions``
    - Respect a configurable cooldown to prevent flapping
    - Never touch containers or processes outside the current runtime
    """

    def __init__(
        self,
        storage: HealthStorage | None = None,
        skill_registry: SkillRegistry | None = None,
        db_pool: asyncpg.Pool | None = None,
        cooldown_seconds: int = _DEFAULT_COOLDOWN_SECONDS,
        enabled: bool = True,
    ) -> None:
        self._storage = storage
        self._registry = skill_registry
        self._pool = db_pool
        self._cooldown = cooldown_seconds
        self._enabled = enabled

    @property
    def enabled(self) -> bool:
        return self._enabled

    @enabled.setter
    def enabled(self, value: bool) -> None:
        self._enabled = value

    # ------------------------------------------------------------------
    # Public healing actions
    # ------------------------------------------------------------------

    async def restart_skill(self, skill_name: str, trigger: str = "anomaly") -> bool:
        """Re-initialise a skill via ``SkillRegistry.safe_initialize()``.

        This is the safest form of "restart" — it calls the skill's own
        ``safe_initialize()`` which resets internal state without any
        process-level restart.
        """
        if not self._enabled:
            log.debug("healer_disabled", action="restart_skill")
            return False

        if await self._in_cooldown("restart_skill"):
            log.debug("healer_cooldown", action="restart_skill", skill=skill_name)
            return False

        success = False
        details: dict[str, Any] = {"skill_name": skill_name}

        try:
            if self._registry is None:
                details["error"] = "no_registry"
            else:
                skill = self._registry.get_skill(skill_name)
                if skill is None:
                    details["error"] = "skill_not_found"
                else:
                    success = await skill.safe_initialize()
                    details["initialized"] = success
                    log.info("healer_restart_skill", skill=skill_name, success=success)

        except Exception as exc:
            details["error"] = str(exc)
            log.error("healer_restart_skill_failed", skill=skill_name, error=str(exc))

        await self._record_action("restart_skill", trigger, success, details)
        return success

    async def clear_stale_connections(self, trigger: str = "anomaly") -> bool:
        """Reset the asyncpg connection pool.

        Terminates idle connections and resets the pool, which can resolve
        stale-connection errors without a full restart.
        """
        if not self._enabled:
            return False

        if await self._in_cooldown("clear_stale_connections"):
            return False

        success = False
        details: dict[str, Any] = {}

        try:
            if self._pool is None:
                details["error"] = "no_pool"
                return False

            # asyncpg.Pool.expire_connections() marks all current connections
            # for replacement on next acquire.
            self._pool.expire_connections()
            success = True
            details["action"] = "connections_expired"
            log.info("healer_clear_stale_connections")

        except Exception as exc:
            details["error"] = str(exc)
            log.error("healer_clear_stale_connections_failed", error=str(exc))

        await self._record_action("clear_stale_connections", trigger, success, details)
        return success

    async def vacuum_databases(self, trigger: str = "maintenance") -> bool:
        """Run PostgreSQL VACUUM on health tables and SQLite VACUUM on costs.db."""
        if not self._enabled:
            return False

        if await self._in_cooldown("vacuum_databases"):
            return False

        success = False
        details: dict[str, Any] = {}

        try:
            # PostgreSQL VACUUM (requires connection outside a transaction)
            if self._pool is not None:
                conn = await self._pool.acquire()
                try:
                    # VACUUM cannot run inside a transaction block
                    await conn.execute("VACUUM (ANALYZE) health_snapshots")
                    await conn.execute("VACUUM (ANALYZE) health_healing_actions")
                    details["postgres"] = "vacuumed"
                finally:
                    await self._pool.release(conn)

            success = True
            log.info("healer_vacuum_databases")

        except Exception as exc:
            details["error"] = str(exc)
            log.error("healer_vacuum_databases_failed", error=str(exc))

        await self._record_action("vacuum_databases", trigger, success, details)
        return success

    async def warm_ollama_models(self, trigger: str = "anomaly") -> bool:
        """Send a keepalive request to Ollama to keep models loaded in memory.

        Uses a lightweight generate call with no actual prompt to warm
        the model into GPU memory if it was evicted.
        """
        if not self._enabled:
            return False

        if await self._in_cooldown("warm_ollama_models"):
            return False

        success = False
        details: dict[str, Any] = {}

        try:
            import httpx

            # Use the ollama-router or direct ollama endpoint
            ollama_url = "http://ollama:11434"
            async with httpx.AsyncClient(timeout=30) as client:
                # List loaded models
                resp = await client.get(f"{ollama_url}/api/tags")
                if resp.status_code == 200:
                    models = resp.json().get("models", [])
                    details["models_found"] = len(models)

                    # Send a minimal keepalive for each model
                    for model_info in models:
                        model_name = model_info.get("name", "")
                        if model_name:
                            await client.post(
                                f"{ollama_url}/api/generate",
                                json={
                                    "model": model_name,
                                    "prompt": "",
                                    "keep_alive": "10m",
                                },
                            )
                    success = True
                else:
                    details["error"] = f"ollama_status_{resp.status_code}"

            log.info("healer_warm_ollama_models", models=details.get("models_found", 0))

        except ImportError:
            details["error"] = "httpx_not_available"
            log.warning("healer_warm_ollama_missing_httpx")
        except Exception as exc:
            details["error"] = str(exc)
            log.error("healer_warm_ollama_failed", error=str(exc))

        await self._record_action("warm_ollama_models", trigger, success, details)
        return success

    async def adjust_rate_limits(self, trigger: str = "anomaly") -> bool:
        """Temporarily reduce request throughput when rate-limited.

        This adjusts the heartbeat interval to slow down proactive actions,
        giving the rate-limited provider time to recover.
        """
        if not self._enabled:
            return False

        if await self._in_cooldown("adjust_rate_limits"):
            return False

        success = False
        details: dict[str, Any] = {}

        try:
            from zetherion_ai.config import get_settings_manager

            mgr = get_settings_manager()
            if mgr is None:
                details["error"] = "no_settings_manager"
                return False

            # Double the heartbeat interval temporarily
            current_interval = mgr.get("scheduler", "interval_seconds", default=300)
            new_interval = min(int(current_interval) * 2, 1800)  # cap at 30 min
            await mgr.set("scheduler", "interval_seconds", new_interval, changed_by=0)

            details["previous_interval"] = current_interval
            details["new_interval"] = new_interval
            success = True
            log.info(
                "healer_adjust_rate_limits",
                previous=current_interval,
                new=new_interval,
            )

        except Exception as exc:
            details["error"] = str(exc)
            log.error("healer_adjust_rate_limits_failed", error=str(exc))

        await self._record_action("adjust_rate_limits", trigger, success, details)
        return success

    async def flush_log_buffer(self, trigger: str = "maintenance") -> bool:
        """Force-flush structured log handlers."""
        if not self._enabled:
            return False

        if await self._in_cooldown("flush_log_buffer"):
            return False

        success = False
        details: dict[str, Any] = {}

        try:
            import logging

            for handler in logging.root.handlers:
                handler.flush()

            success = True
            details["handlers_flushed"] = len(logging.root.handlers)
            log.info("healer_flush_log_buffer")

        except Exception as exc:
            details["error"] = str(exc)
            log.error("healer_flush_log_buffer_failed", error=str(exc))

        await self._record_action("flush_log_buffer", trigger, success, details)
        return success

    # ------------------------------------------------------------------
    # Dispatch helper (used by the health skill)
    # ------------------------------------------------------------------

    async def execute_recommended(
        self,
        actions: list[str],
        trigger: str = "anomaly",
    ) -> dict[str, bool]:
        """Execute a list of recommended action names.

        Returns a mapping of action name → success.
        """
        results: dict[str, bool] = {}
        dispatch = {
            "restart_skill": self._restart_any_errored_skill,
            "clear_stale_connections": self.clear_stale_connections,
            "vacuum_databases": self.vacuum_databases,
            "warm_ollama_models": self.warm_ollama_models,
            "adjust_rate_limits": self.adjust_rate_limits,
            "flush_log_buffer": self.flush_log_buffer,
        }

        for action_name in actions:
            handler = dispatch.get(action_name)
            if handler is None:
                results[action_name] = False
                continue
            results[action_name] = await handler(trigger=trigger)

        return results

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _restart_any_errored_skill(self, trigger: str = "anomaly") -> bool:
        """Find the first errored skill and restart it."""
        if self._registry is None:
            return False

        from zetherion_ai.skills.base import SkillStatus

        for skill in self._registry._skills.values():
            if skill.status == SkillStatus.ERROR:
                return await self.restart_skill(skill.name, trigger=trigger)

        return False  # No errored skills found

    async def _in_cooldown(self, action_type: str) -> bool:
        """Check if an action was taken recently (within the cooldown window)."""
        if self._storage is None:
            return False  # No storage means we can't track cooldowns

        try:
            recent = await self._storage.get_recent_healing_action(
                action_type, within_seconds=self._cooldown
            )
            return recent is not None
        except Exception:
            return False  # On error, allow the action

    async def _record_action(
        self,
        action_type: str,
        trigger: str,
        success: bool,
        details: dict[str, Any],
    ) -> None:
        """Log a healing action to storage."""
        if self._storage is None:
            return

        try:
            from zetherion_ai.health.storage import HealingAction

            action = HealingAction(
                timestamp=datetime.now(),
                action_type=action_type,
                trigger=trigger,
                result="success" if success else "failed",
                details=details,
            )
            await self._storage.save_healing_action(action)
        except Exception as exc:
            log.warning("healer_record_failed", action=action_type, error=str(exc))
