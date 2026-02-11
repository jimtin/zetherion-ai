"""Tests for the SelfHealer class in zetherion_ai.health.healer."""

from __future__ import annotations

import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.health.healer import SelfHealer
from zetherion_ai.health.storage import HealingAction

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_storage() -> AsyncMock:
    """Return a mock HealthStorage with all relevant async methods."""
    storage = AsyncMock()
    # By default, no recent action (cooldown check returns None)
    storage.get_recent_healing_action = AsyncMock(return_value=None)
    storage.save_healing_action = AsyncMock()
    return storage


@pytest.fixture()
def mock_skill() -> MagicMock:
    """Return a mock Skill that succeeds on safe_initialize."""
    skill = MagicMock()
    skill.name = "test_skill"
    skill.safe_initialize = AsyncMock(return_value=True)
    return skill


@pytest.fixture()
def mock_errored_skill() -> MagicMock:
    """Return a mock Skill in ERROR status."""
    from zetherion_ai.skills.base import SkillStatus

    skill = MagicMock()
    skill.name = "broken_skill"
    skill.status = SkillStatus.ERROR
    skill.safe_initialize = AsyncMock(return_value=True)
    return skill


@pytest.fixture()
def mock_registry(mock_skill: MagicMock) -> MagicMock:
    """Return a mock SkillRegistry that knows about one skill."""
    registry = MagicMock()
    registry.get_skill = MagicMock(return_value=mock_skill)
    registry._skills = {"test_skill": mock_skill}
    return registry


@pytest.fixture()
def mock_pool() -> MagicMock:
    """Return a mock asyncpg.Pool with expire_connections and acquire."""
    pool = MagicMock()
    pool.expire_connections = MagicMock()

    # acquire() returns an awaitable that yields a mock connection
    mock_conn = AsyncMock()
    mock_conn.execute = AsyncMock()

    pool.acquire = AsyncMock(return_value=mock_conn)
    pool.release = AsyncMock()
    return pool


@pytest.fixture()
def healer(
    mock_storage: AsyncMock,
    mock_registry: MagicMock,
    mock_pool: MagicMock,
) -> SelfHealer:
    """Return a fully-wired SelfHealer for testing."""
    return SelfHealer(
        storage=mock_storage,
        skill_registry=mock_registry,
        db_pool=mock_pool,
        cooldown_seconds=300,
        enabled=True,
    )


@pytest.fixture()
def disabled_healer(
    mock_storage: AsyncMock,
    mock_registry: MagicMock,
    mock_pool: MagicMock,
) -> SelfHealer:
    """Return a SelfHealer with enabled=False."""
    return SelfHealer(
        storage=mock_storage,
        skill_registry=mock_registry,
        db_pool=mock_pool,
        cooldown_seconds=300,
        enabled=False,
    )


# ---------------------------------------------------------------------------
# restart_skill
# ---------------------------------------------------------------------------


class TestRestartSkill:
    """Tests for SelfHealer.restart_skill()."""

    @pytest.mark.asyncio()
    async def test_restart_skill_calls_registry_and_initialize(
        self,
        healer: SelfHealer,
        mock_registry: MagicMock,
        mock_skill: MagicMock,
    ) -> None:
        """restart_skill should get the skill from the registry and call safe_initialize."""
        result = await healer.restart_skill("test_skill", trigger="anomaly")

        assert result is True
        mock_registry.get_skill.assert_called_once_with("test_skill")
        mock_skill.safe_initialize.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_restart_skill_missing_skill_returns_false(
        self,
        healer: SelfHealer,
        mock_registry: MagicMock,
    ) -> None:
        """restart_skill returns False when the skill is not found."""
        mock_registry.get_skill.return_value = None

        result = await healer.restart_skill("nonexistent", trigger="anomaly")

        assert result is False

    @pytest.mark.asyncio()
    async def test_restart_skill_no_registry_returns_false(
        self,
        mock_storage: AsyncMock,
    ) -> None:
        """restart_skill returns False when no SkillRegistry is provided."""
        healer = SelfHealer(storage=mock_storage, skill_registry=None, enabled=True)

        result = await healer.restart_skill("any_skill", trigger="anomaly")

        assert result is False

    @pytest.mark.asyncio()
    async def test_restart_skill_records_action_to_storage(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """restart_skill records the healing action to storage."""
        await healer.restart_skill("test_skill", trigger="test_trigger")

        mock_storage.save_healing_action.assert_awaited_once()
        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.action_type == "restart_skill"
        assert saved_action.trigger == "test_trigger"
        assert saved_action.result == "success"
        assert saved_action.details["skill_name"] == "test_skill"

    @pytest.mark.asyncio()
    async def test_restart_skill_records_failure_on_missing_skill(
        self,
        healer: SelfHealer,
        mock_registry: MagicMock,
        mock_storage: AsyncMock,
    ) -> None:
        """When skill is not found, the recorded action should show failure."""
        mock_registry.get_skill.return_value = None

        await healer.restart_skill("missing_skill", trigger="anomaly")

        mock_storage.save_healing_action.assert_awaited_once()
        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.result == "failed"
        assert saved_action.details.get("error") == "skill_not_found"

    @pytest.mark.asyncio()
    async def test_restart_skill_safe_initialize_fails(
        self,
        healer: SelfHealer,
        mock_skill: MagicMock,
        mock_storage: AsyncMock,
    ) -> None:
        """restart_skill returns False when safe_initialize returns False."""
        mock_skill.safe_initialize = AsyncMock(return_value=False)

        result = await healer.restart_skill("test_skill", trigger="anomaly")

        assert result is False
        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.result == "failed"

    @pytest.mark.asyncio()
    async def test_restart_skill_safe_initialize_raises(
        self,
        healer: SelfHealer,
        mock_skill: MagicMock,
        mock_storage: AsyncMock,
    ) -> None:
        """restart_skill catches exceptions from safe_initialize and returns False."""
        mock_skill.safe_initialize = AsyncMock(side_effect=RuntimeError("init boom"))

        result = await healer.restart_skill("test_skill", trigger="anomaly")

        assert result is False
        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.result == "failed"
        assert "init boom" in saved_action.details["error"]


# ---------------------------------------------------------------------------
# clear_stale_connections
# ---------------------------------------------------------------------------


class TestClearStaleConnections:
    """Tests for SelfHealer.clear_stale_connections()."""

    @pytest.mark.asyncio()
    async def test_calls_expire_connections(
        self,
        healer: SelfHealer,
        mock_pool: MagicMock,
    ) -> None:
        """clear_stale_connections should call pool.expire_connections()."""
        result = await healer.clear_stale_connections(trigger="anomaly")

        assert result is True
        mock_pool.expire_connections.assert_called_once()

    @pytest.mark.asyncio()
    async def test_records_action(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """clear_stale_connections records the healing action."""
        await healer.clear_stale_connections(trigger="anomaly")

        mock_storage.save_healing_action.assert_awaited_once()
        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.action_type == "clear_stale_connections"
        assert saved_action.result == "success"

    @pytest.mark.asyncio()
    async def test_no_pool_returns_false(
        self,
        mock_storage: AsyncMock,
    ) -> None:
        """clear_stale_connections returns False when no pool is available."""
        healer = SelfHealer(storage=mock_storage, db_pool=None, enabled=True)

        result = await healer.clear_stale_connections(trigger="anomaly")

        assert result is False

    @pytest.mark.asyncio()
    async def test_expire_connections_raises(
        self,
        healer: SelfHealer,
        mock_pool: MagicMock,
        mock_storage: AsyncMock,
    ) -> None:
        """clear_stale_connections catches pool exceptions and returns False."""
        mock_pool.expire_connections.side_effect = RuntimeError("pool error")

        result = await healer.clear_stale_connections(trigger="anomaly")

        assert result is False
        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.result == "failed"
        assert "pool error" in saved_action.details["error"]


# ---------------------------------------------------------------------------
# vacuum_databases
# ---------------------------------------------------------------------------


class TestVacuumDatabases:
    """Tests for SelfHealer.vacuum_databases()."""

    @pytest.mark.asyncio()
    async def test_vacuum_runs_sql_commands(
        self,
        healer: SelfHealer,
        mock_pool: MagicMock,
    ) -> None:
        """vacuum_databases should execute VACUUM on health tables."""
        mock_conn = AsyncMock()
        mock_pool.acquire = AsyncMock(return_value=mock_conn)
        mock_pool.release = AsyncMock()

        result = await healer.vacuum_databases(trigger="maintenance")

        assert result is True
        # Two VACUUM commands should be issued
        assert mock_conn.execute.await_count == 2
        calls = [c.args[0] for c in mock_conn.execute.call_args_list]
        assert "VACUUM (ANALYZE) health_snapshots" in calls
        assert "VACUUM (ANALYZE) health_healing_actions" in calls

    @pytest.mark.asyncio()
    async def test_vacuum_no_pool_still_succeeds(
        self,
        mock_storage: AsyncMock,
    ) -> None:
        """vacuum_databases succeeds even without a pool (skips PG vacuum)."""
        healer = SelfHealer(storage=mock_storage, db_pool=None, enabled=True)

        result = await healer.vacuum_databases(trigger="maintenance")

        assert result is True

    @pytest.mark.asyncio()
    async def test_vacuum_records_action(
        self,
        healer: SelfHealer,
        mock_pool: MagicMock,
        mock_storage: AsyncMock,
    ) -> None:
        """vacuum_databases records a success action."""
        mock_conn = AsyncMock()
        mock_pool.acquire = AsyncMock(return_value=mock_conn)
        mock_pool.release = AsyncMock()

        await healer.vacuum_databases(trigger="maintenance")

        mock_storage.save_healing_action.assert_awaited_once()
        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.action_type == "vacuum_databases"
        assert saved_action.result == "success"
        assert saved_action.details.get("postgres") == "vacuumed"

    @pytest.mark.asyncio()
    async def test_vacuum_execute_raises(
        self,
        healer: SelfHealer,
        mock_pool: MagicMock,
        mock_storage: AsyncMock,
    ) -> None:
        """vacuum_databases catches SQL exceptions and returns False."""
        mock_conn = AsyncMock()
        mock_conn.execute = AsyncMock(side_effect=RuntimeError("SQL error"))
        mock_pool.acquire = AsyncMock(return_value=mock_conn)
        mock_pool.release = AsyncMock()

        result = await healer.vacuum_databases(trigger="maintenance")

        assert result is False
        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.result == "failed"


# ---------------------------------------------------------------------------
# warm_ollama_models
# ---------------------------------------------------------------------------


class TestWarmOllamaModels:
    """Tests for SelfHealer.warm_ollama_models()."""

    @pytest.mark.asyncio()
    async def test_warm_ollama_sends_keepalive(
        self,
        healer: SelfHealer,
    ) -> None:
        """warm_ollama_models should GET /api/tags then POST keepalive for each model."""
        mock_tags_response = MagicMock()
        mock_tags_response.status_code = 200
        mock_tags_response.json.return_value = {
            "models": [
                {"name": "llama3.2:3b"},
                {"name": "nomic-embed-text"},
            ]
        }

        mock_generate_response = MagicMock()
        mock_generate_response.status_code = 200

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_tags_response)
        mock_client.post = AsyncMock(return_value=mock_generate_response)

        # Mock the async context manager
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await healer.warm_ollama_models(trigger="anomaly")

        assert result is True
        mock_client.get.assert_awaited_once_with("http://ollama:11434/api/tags")
        assert mock_client.post.await_count == 2

        # Check model names in the POST calls
        post_calls = mock_client.post.call_args_list
        posted_models = [c.kwargs["json"]["model"] for c in post_calls]
        assert "llama3.2:3b" in posted_models
        assert "nomic-embed-text" in posted_models

        # Verify keepalive is sent
        for call in post_calls:
            assert call.kwargs["json"]["keep_alive"] == "10m"

    @pytest.mark.asyncio()
    async def test_warm_ollama_non_200_status(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """warm_ollama_models returns False when /api/tags returns non-200."""
        mock_response = MagicMock()
        mock_response.status_code = 503

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await healer.warm_ollama_models(trigger="anomaly")

        assert result is False
        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.result == "failed"
        assert "ollama_status_503" in saved_action.details.get("error", "")

    @pytest.mark.asyncio()
    async def test_warm_ollama_no_models(
        self,
        healer: SelfHealer,
    ) -> None:
        """warm_ollama_models succeeds with an empty model list."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await healer.warm_ollama_models(trigger="anomaly")

        assert result is True
        mock_client.post.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_warm_ollama_connection_error(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """warm_ollama_models catches connection errors."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=ConnectionError("no route to host"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await healer.warm_ollama_models(trigger="anomaly")

        assert result is False
        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.result == "failed"

    @pytest.mark.asyncio()
    async def test_warm_ollama_records_action(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """warm_ollama_models records the action with model count."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": [{"name": "llama3.2:3b"}]}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.post = AsyncMock(return_value=MagicMock(status_code=200))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            await healer.warm_ollama_models(trigger="anomaly")

        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.action_type == "warm_ollama_models"
        assert saved_action.result == "success"
        assert saved_action.details["models_found"] == 1


# ---------------------------------------------------------------------------
# adjust_rate_limits
# ---------------------------------------------------------------------------


class TestAdjustRateLimits:
    """Tests for SelfHealer.adjust_rate_limits()."""

    @pytest.mark.asyncio()
    async def test_doubles_interval(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """adjust_rate_limits doubles the current interval."""
        mock_mgr = MagicMock()
        mock_mgr.get.return_value = 300
        mock_mgr.set = AsyncMock()

        with patch(
            "zetherion_ai.config.get_settings_manager",
            return_value=mock_mgr,
        ):
            result = await healer.adjust_rate_limits(trigger="anomaly")

        assert result is True
        mock_mgr.set.assert_awaited_once_with("scheduler", "interval_seconds", 600, changed_by=0)

    @pytest.mark.asyncio()
    async def test_caps_at_1800(
        self,
        healer: SelfHealer,
    ) -> None:
        """adjust_rate_limits caps the interval at 1800 seconds (30 min)."""
        mock_mgr = MagicMock()
        mock_mgr.get.return_value = 1200  # doubling would give 2400, capped at 1800
        mock_mgr.set = AsyncMock()

        with patch(
            "zetherion_ai.config.get_settings_manager",
            return_value=mock_mgr,
        ):
            result = await healer.adjust_rate_limits(trigger="anomaly")

        assert result is True
        mock_mgr.set.assert_awaited_once_with("scheduler", "interval_seconds", 1800, changed_by=0)

    @pytest.mark.asyncio()
    async def test_no_settings_manager_returns_false(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """adjust_rate_limits returns False when get_settings_manager() returns None."""
        with patch(
            "zetherion_ai.config.get_settings_manager",
            return_value=None,
        ):
            result = await healer.adjust_rate_limits(trigger="anomaly")

        assert result is False

    @pytest.mark.asyncio()
    async def test_records_action_with_intervals(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """adjust_rate_limits records previous and new interval."""
        mock_mgr = MagicMock()
        mock_mgr.get.return_value = 300
        mock_mgr.set = AsyncMock()

        with patch(
            "zetherion_ai.config.get_settings_manager",
            return_value=mock_mgr,
        ):
            await healer.adjust_rate_limits(trigger="anomaly")

        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.action_type == "adjust_rate_limits"
        assert saved_action.result == "success"
        assert saved_action.details["previous_interval"] == 300
        assert saved_action.details["new_interval"] == 600

    @pytest.mark.asyncio()
    async def test_exception_returns_false(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """adjust_rate_limits returns False on exception."""
        mock_mgr = MagicMock()
        mock_mgr.get.side_effect = RuntimeError("settings exploded")

        with patch(
            "zetherion_ai.config.get_settings_manager",
            return_value=mock_mgr,
        ):
            result = await healer.adjust_rate_limits(trigger="anomaly")

        assert result is False
        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.result == "failed"


# ---------------------------------------------------------------------------
# flush_log_buffer
# ---------------------------------------------------------------------------


class TestFlushLogBuffer:
    """Tests for SelfHealer.flush_log_buffer()."""

    @pytest.mark.asyncio()
    async def test_flushes_root_handlers(
        self,
        healer: SelfHealer,
    ) -> None:
        """flush_log_buffer should call flush() on all root logger handlers."""
        mock_handler = MagicMock(spec=logging.Handler)
        with patch.object(logging.root, "handlers", [mock_handler]):
            result = await healer.flush_log_buffer(trigger="maintenance")

        assert result is True
        mock_handler.flush.assert_called_once()

    @pytest.mark.asyncio()
    async def test_records_handlers_count(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """flush_log_buffer records the number of handlers flushed."""
        handler_a = MagicMock(spec=logging.Handler)
        handler_b = MagicMock(spec=logging.Handler)
        with patch.object(logging.root, "handlers", [handler_a, handler_b]):
            await healer.flush_log_buffer(trigger="maintenance")

        saved_action: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved_action.details["handlers_flushed"] == 2

    @pytest.mark.asyncio()
    async def test_flush_no_handlers(
        self,
        healer: SelfHealer,
    ) -> None:
        """flush_log_buffer succeeds even with zero handlers."""
        with patch.object(logging.root, "handlers", []):
            result = await healer.flush_log_buffer(trigger="maintenance")

        assert result is True


# ---------------------------------------------------------------------------
# Enabled / disabled
# ---------------------------------------------------------------------------


class TestEnabledDisabled:
    """Tests for the enabled flag."""

    @pytest.mark.asyncio()
    async def test_disabled_restart_skill(self, disabled_healer: SelfHealer) -> None:
        result = await disabled_healer.restart_skill("test_skill", trigger="anomaly")
        assert result is False

    @pytest.mark.asyncio()
    async def test_disabled_clear_stale_connections(self, disabled_healer: SelfHealer) -> None:
        result = await disabled_healer.clear_stale_connections(trigger="anomaly")
        assert result is False

    @pytest.mark.asyncio()
    async def test_disabled_vacuum_databases(self, disabled_healer: SelfHealer) -> None:
        result = await disabled_healer.vacuum_databases(trigger="maintenance")
        assert result is False

    @pytest.mark.asyncio()
    async def test_disabled_warm_ollama_models(self, disabled_healer: SelfHealer) -> None:
        result = await disabled_healer.warm_ollama_models(trigger="anomaly")
        assert result is False

    @pytest.mark.asyncio()
    async def test_disabled_adjust_rate_limits(self, disabled_healer: SelfHealer) -> None:
        result = await disabled_healer.adjust_rate_limits(trigger="anomaly")
        assert result is False

    @pytest.mark.asyncio()
    async def test_disabled_flush_log_buffer(self, disabled_healer: SelfHealer) -> None:
        result = await disabled_healer.flush_log_buffer(trigger="maintenance")
        assert result is False

    @pytest.mark.asyncio()
    async def test_disabled_does_not_record_action(
        self,
        disabled_healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """When disabled, no action should be recorded to storage."""
        await disabled_healer.restart_skill("test_skill", trigger="anomaly")
        mock_storage.save_healing_action.assert_not_awaited()

    def test_enabled_property_getter(self, healer: SelfHealer) -> None:
        assert healer.enabled is True

    def test_enabled_property_setter(self, healer: SelfHealer) -> None:
        healer.enabled = False
        assert healer.enabled is False


# ---------------------------------------------------------------------------
# Cooldown
# ---------------------------------------------------------------------------


class TestCooldown:
    """Tests for the cooldown mechanism (_in_cooldown)."""

    @pytest.mark.asyncio()
    async def test_skips_action_within_cooldown(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """When storage reports a recent action, the healer should skip."""
        # Return a mock HealingAction to indicate recent activity
        mock_storage.get_recent_healing_action.return_value = MagicMock(spec=HealingAction)

        result = await healer.restart_skill("test_skill", trigger="anomaly")

        assert result is False
        # No save because the method returned early due to cooldown
        mock_storage.save_healing_action.assert_not_awaited()

    @pytest.mark.asyncio()
    async def test_allows_action_outside_cooldown(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """When storage reports no recent action, the healer should proceed."""
        mock_storage.get_recent_healing_action.return_value = None

        result = await healer.restart_skill("test_skill", trigger="anomaly")

        assert result is True

    @pytest.mark.asyncio()
    async def test_cooldown_passes_correct_seconds(
        self,
        mock_storage: AsyncMock,
        mock_registry: MagicMock,
    ) -> None:
        """_in_cooldown passes the configured cooldown_seconds to storage."""
        healer = SelfHealer(
            storage=mock_storage,
            skill_registry=mock_registry,
            cooldown_seconds=600,
            enabled=True,
        )

        await healer.restart_skill("test_skill", trigger="anomaly")

        mock_storage.get_recent_healing_action.assert_awaited_with(
            "restart_skill", within_seconds=600
        )

    @pytest.mark.asyncio()
    async def test_cooldown_no_storage_allows_action(self) -> None:
        """Without storage, cooldown check should not block the action."""
        mock_registry = MagicMock()
        mock_skill = MagicMock()
        mock_skill.safe_initialize = AsyncMock(return_value=True)
        mock_registry.get_skill.return_value = mock_skill

        healer = SelfHealer(storage=None, skill_registry=mock_registry, enabled=True)

        result = await healer.restart_skill("test_skill", trigger="anomaly")

        assert result is True

    @pytest.mark.asyncio()
    async def test_cooldown_storage_error_allows_action(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """If storage raises during cooldown check, the action should proceed."""
        mock_storage.get_recent_healing_action.side_effect = RuntimeError("db down")

        result = await healer.restart_skill("test_skill", trigger="anomaly")

        assert result is True


# ---------------------------------------------------------------------------
# _record_action
# ---------------------------------------------------------------------------


class TestRecordAction:
    """Tests for SelfHealer._record_action()."""

    @pytest.mark.asyncio()
    async def test_record_creates_healing_action(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """_record_action should create a HealingAction and save it."""
        await healer._record_action("test_action", "manual", True, {"detail_key": "value"})

        mock_storage.save_healing_action.assert_awaited_once()
        saved: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved.action_type == "test_action"
        assert saved.trigger == "manual"
        assert saved.result == "success"
        assert saved.details == {"detail_key": "value"}

    @pytest.mark.asyncio()
    async def test_record_failure_result_string(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """_record_action uses 'failed' for unsuccessful actions."""
        await healer._record_action("test_action", "manual", False, {})

        saved: HealingAction = mock_storage.save_healing_action.call_args[0][0]
        assert saved.result == "failed"

    @pytest.mark.asyncio()
    async def test_record_no_storage_does_nothing(self) -> None:
        """_record_action is a no-op when storage is None."""
        healer = SelfHealer(storage=None, enabled=True)

        # Should not raise
        await healer._record_action("test_action", "manual", True, {})

    @pytest.mark.asyncio()
    async def test_record_storage_error_is_swallowed(
        self,
        healer: SelfHealer,
        mock_storage: AsyncMock,
    ) -> None:
        """_record_action catches storage exceptions without propagating."""
        mock_storage.save_healing_action.side_effect = RuntimeError("storage down")

        # Should not raise
        await healer._record_action("test_action", "manual", True, {})


# ---------------------------------------------------------------------------
# execute_recommended
# ---------------------------------------------------------------------------


class TestExecuteRecommended:
    """Tests for SelfHealer.execute_recommended()."""

    @pytest.mark.asyncio()
    async def test_dispatches_clear_stale_connections(
        self,
        healer: SelfHealer,
        mock_pool: MagicMock,
    ) -> None:
        """execute_recommended dispatches 'clear_stale_connections' correctly."""
        results = await healer.execute_recommended(["clear_stale_connections"], trigger="anomaly")

        assert results["clear_stale_connections"] is True
        mock_pool.expire_connections.assert_called_once()

    @pytest.mark.asyncio()
    async def test_dispatches_flush_log_buffer(
        self,
        healer: SelfHealer,
    ) -> None:
        """execute_recommended dispatches 'flush_log_buffer' correctly."""
        with patch.object(logging.root, "handlers", [MagicMock(spec=logging.Handler)]):
            results = await healer.execute_recommended(["flush_log_buffer"], trigger="maintenance")

        assert results["flush_log_buffer"] is True

    @pytest.mark.asyncio()
    async def test_dispatches_multiple_actions(
        self,
        healer: SelfHealer,
        mock_pool: MagicMock,
    ) -> None:
        """execute_recommended handles multiple actions in sequence."""
        with patch.object(logging.root, "handlers", [MagicMock(spec=logging.Handler)]):
            results = await healer.execute_recommended(
                ["clear_stale_connections", "flush_log_buffer"],
                trigger="anomaly",
            )

        assert results["clear_stale_connections"] is True
        assert results["flush_log_buffer"] is True

    @pytest.mark.asyncio()
    async def test_unknown_action_returns_false(
        self,
        healer: SelfHealer,
    ) -> None:
        """execute_recommended returns False for unknown action names."""
        results = await healer.execute_recommended(["nonexistent_action"], trigger="anomaly")

        assert results["nonexistent_action"] is False

    @pytest.mark.asyncio()
    async def test_restart_skill_dispatches_to_restart_any_errored(
        self,
        healer: SelfHealer,
    ) -> None:
        """execute_recommended('restart_skill') calls _restart_any_errored_skill."""
        with patch.object(
            healer, "_restart_any_errored_skill", new_callable=AsyncMock
        ) as mock_restart:
            mock_restart.return_value = True

            results = await healer.execute_recommended(["restart_skill"], trigger="anomaly")

        assert results["restart_skill"] is True
        mock_restart.assert_awaited_once_with(trigger="anomaly")

    @pytest.mark.asyncio()
    async def test_dispatches_vacuum_databases(
        self,
        healer: SelfHealer,
        mock_pool: MagicMock,
    ) -> None:
        """execute_recommended dispatches 'vacuum_databases' correctly."""
        mock_conn = AsyncMock()
        mock_pool.acquire = AsyncMock(return_value=mock_conn)
        mock_pool.release = AsyncMock()

        results = await healer.execute_recommended(["vacuum_databases"], trigger="maintenance")

        assert results["vacuum_databases"] is True

    @pytest.mark.asyncio()
    async def test_dispatches_adjust_rate_limits(
        self,
        healer: SelfHealer,
    ) -> None:
        """execute_recommended dispatches 'adjust_rate_limits' correctly."""
        mock_mgr = MagicMock()
        mock_mgr.get.return_value = 300
        mock_mgr.set = AsyncMock()

        with patch(
            "zetherion_ai.config.get_settings_manager",
            return_value=mock_mgr,
        ):
            results = await healer.execute_recommended(["adjust_rate_limits"], trigger="anomaly")

        assert results["adjust_rate_limits"] is True

    @pytest.mark.asyncio()
    async def test_dispatches_warm_ollama_models(
        self,
        healer: SelfHealer,
    ) -> None:
        """execute_recommended dispatches 'warm_ollama_models' correctly."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            results = await healer.execute_recommended(["warm_ollama_models"], trigger="anomaly")

        assert results["warm_ollama_models"] is True

    @pytest.mark.asyncio()
    async def test_empty_actions_returns_empty_dict(
        self,
        healer: SelfHealer,
    ) -> None:
        """execute_recommended with an empty list returns an empty dict."""
        results = await healer.execute_recommended([], trigger="anomaly")

        assert results == {}


# ---------------------------------------------------------------------------
# _restart_any_errored_skill
# ---------------------------------------------------------------------------


class TestRestartAnyErroredSkill:
    """Tests for SelfHealer._restart_any_errored_skill()."""

    @pytest.mark.asyncio()
    async def test_finds_and_restarts_errored_skill(
        self,
        mock_storage: AsyncMock,
    ) -> None:
        """_restart_any_errored_skill finds the first errored skill and restarts it."""
        from zetherion_ai.skills.base import SkillStatus

        errored_skill = MagicMock()
        errored_skill.name = "broken_skill"
        errored_skill.status = SkillStatus.ERROR
        errored_skill.safe_initialize = AsyncMock(return_value=True)

        healthy_skill = MagicMock()
        healthy_skill.name = "healthy_skill"
        healthy_skill.status = SkillStatus.READY

        registry = MagicMock()
        registry._skills = {
            "healthy_skill": healthy_skill,
            "broken_skill": errored_skill,
        }
        registry.get_skill = MagicMock(return_value=errored_skill)

        healer = SelfHealer(
            storage=mock_storage,
            skill_registry=registry,
            enabled=True,
        )

        result = await healer._restart_any_errored_skill(trigger="anomaly")

        assert result is True
        errored_skill.safe_initialize.assert_awaited_once()

    @pytest.mark.asyncio()
    async def test_no_errored_skills_returns_false(
        self,
        mock_storage: AsyncMock,
    ) -> None:
        """_restart_any_errored_skill returns False when all skills are healthy."""
        from zetherion_ai.skills.base import SkillStatus

        healthy_skill = MagicMock()
        healthy_skill.name = "healthy_skill"
        healthy_skill.status = SkillStatus.READY

        registry = MagicMock()
        registry._skills = {"healthy_skill": healthy_skill}

        healer = SelfHealer(
            storage=mock_storage,
            skill_registry=registry,
            enabled=True,
        )

        result = await healer._restart_any_errored_skill(trigger="anomaly")

        assert result is False

    @pytest.mark.asyncio()
    async def test_no_registry_returns_false(
        self,
        mock_storage: AsyncMock,
    ) -> None:
        """_restart_any_errored_skill returns False when there is no registry."""
        healer = SelfHealer(
            storage=mock_storage,
            skill_registry=None,
            enabled=True,
        )

        result = await healer._restart_any_errored_skill(trigger="anomaly")

        assert result is False

    @pytest.mark.asyncio()
    async def test_restarts_only_first_errored(
        self,
        mock_storage: AsyncMock,
    ) -> None:
        """_restart_any_errored_skill only restarts the first errored skill it finds."""
        from zetherion_ai.skills.base import SkillStatus

        errored_a = MagicMock()
        errored_a.name = "errored_a"
        errored_a.status = SkillStatus.ERROR
        errored_a.safe_initialize = AsyncMock(return_value=True)

        errored_b = MagicMock()
        errored_b.name = "errored_b"
        errored_b.status = SkillStatus.ERROR
        errored_b.safe_initialize = AsyncMock(return_value=True)

        registry = MagicMock()
        registry._skills = {"errored_a": errored_a, "errored_b": errored_b}
        registry.get_skill = MagicMock(return_value=errored_a)

        healer = SelfHealer(
            storage=mock_storage,
            skill_registry=registry,
            enabled=True,
        )

        result = await healer._restart_any_errored_skill(trigger="anomaly")

        assert result is True
        # Only the first errored skill should be restarted
        errored_a.safe_initialize.assert_awaited_once()
        errored_b.safe_initialize.assert_not_awaited()


# ---------------------------------------------------------------------------
# Constructor defaults
# ---------------------------------------------------------------------------


class TestConstructorDefaults:
    """Tests for SelfHealer constructor behavior."""

    def test_default_constructor_has_no_dependencies(self) -> None:
        """SelfHealer can be constructed with all defaults."""
        healer = SelfHealer()

        assert healer._storage is None
        assert healer._registry is None
        assert healer._pool is None
        assert healer._cooldown == 300
        assert healer._enabled is True

    def test_custom_cooldown(self) -> None:
        """Constructor accepts custom cooldown_seconds."""
        healer = SelfHealer(cooldown_seconds=60)

        assert healer._cooldown == 60

    def test_enabled_false(self) -> None:
        """Constructor accepts enabled=False."""
        healer = SelfHealer(enabled=False)

        assert healer._enabled is False
