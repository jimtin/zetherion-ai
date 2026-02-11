"""Tests for HealthAnalyzerSkill."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.skills.base import (
    HeartbeatAction,
    SkillRequest,
)
from zetherion_ai.skills.health_analyzer import HealthAnalyzerSkill
from zetherion_ai.skills.permissions import Permission

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_pool_mock() -> MagicMock:
    """Create a mock asyncpg.Pool with the acquire() context-manager pattern."""
    pool = MagicMock()
    conn = AsyncMock()
    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return pool


def _make_initialized_skill(
    db_pool: MagicMock | None = None,
) -> HealthAnalyzerSkill:
    """Return a HealthAnalyzerSkill with subsystems replaced by mocks.

    This bypasses the real ``initialize()`` and wires in lightweight
    mocks for storage, collector, analyzer, and healer so each test
    can configure return values independently.
    """
    skill = HealthAnalyzerSkill(db_pool=db_pool)

    skill._storage = MagicMock()
    skill._storage._pool = db_pool  # used by guard checks in handle()
    skill._storage.save_snapshot = AsyncMock()
    skill._storage.get_snapshots = AsyncMock(return_value=[])
    skill._storage.save_daily_report = AsyncMock()
    skill._storage.get_daily_report = AsyncMock(return_value=None)

    skill._collector = MagicMock()
    skill._collector.collect_all = MagicMock(
        return_value={
            "reliability": {
                "uptime_seconds": 3600,
                "error_rate_by_provider": {},
            },
            "usage": {"total_cost_usd_today": 0.05},
            "skills": {
                "ready_count": 3,
                "total_skills": 4,
                "error_count": 0,
            },
            "performance": {},
            "system": {},
        }
    )

    skill._analyzer = MagicMock()
    skill._healer = MagicMock()
    skill._healer.execute_recommended = AsyncMock(return_value={})

    return skill


# ---------------------------------------------------------------------------
# 1. metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    """Tests for the metadata property."""

    def test_metadata_name(self) -> None:
        """metadata.name should be 'health_analyzer'."""
        skill = HealthAnalyzerSkill()
        assert skill.metadata.name == "health_analyzer"

    def test_metadata_intents(self) -> None:
        """metadata.intents should list the three supported intents."""
        skill = HealthAnalyzerSkill()
        assert set(skill.metadata.intents) == {
            "health_check",
            "health_report",
            "system_status",
        }

    def test_metadata_permissions(self) -> None:
        """metadata.permissions should contain READ_CONFIG, SEND_MESSAGES, SEND_DM."""
        skill = HealthAnalyzerSkill()
        perms = skill.metadata.permissions
        assert Permission.READ_CONFIG in perms
        assert Permission.SEND_MESSAGES in perms
        assert Permission.SEND_DM in perms

    def test_metadata_version(self) -> None:
        """metadata.version should be set."""
        skill = HealthAnalyzerSkill()
        assert skill.metadata.version == "0.1.0"

    def test_metadata_description(self) -> None:
        """metadata.description should be a non-empty string."""
        skill = HealthAnalyzerSkill()
        assert len(skill.metadata.description) > 0


# ---------------------------------------------------------------------------
# 2. initialize()
# ---------------------------------------------------------------------------


class TestInitialize:
    """Tests for the initialize() async method."""

    @pytest.mark.asyncio
    async def test_initialize_creates_components(self) -> None:
        """initialize() should set up collector, analyzer, storage, healer."""
        pool = _make_pool_mock()
        skill = HealthAnalyzerSkill(db_pool=pool)

        with (
            patch("zetherion_ai.health.storage.HealthStorage") as mock_storage_cls,
            patch("zetherion_ai.health.collector.MetricsCollector") as mock_collector_cls,
            patch("zetherion_ai.health.analyzer.HealthAnalyzer") as mock_analyzer_cls,
            patch("zetherion_ai.health.healer.SelfHealer") as mock_healer_cls,
            patch("zetherion_ai.health.collector.CollectorSources"),
        ):
            storage_instance = MagicMock()
            storage_instance.initialize = AsyncMock()
            mock_storage_cls.return_value = storage_instance

            result = await skill.initialize()

        assert result is True
        assert skill._storage is not None
        assert skill._collector is not None
        assert skill._analyzer is not None
        assert skill._healer is not None
        mock_storage_cls.assert_called_once()
        mock_collector_cls.assert_called_once()
        mock_analyzer_cls.assert_called_once()
        mock_healer_cls.assert_called_once()

    @pytest.mark.asyncio
    async def test_initialize_without_db_pool(self) -> None:
        """initialize() should succeed even when db_pool is None."""
        skill = HealthAnalyzerSkill(db_pool=None)

        with (
            patch("zetherion_ai.health.storage.HealthStorage") as mock_storage_cls,
            patch("zetherion_ai.health.collector.MetricsCollector"),
            patch("zetherion_ai.health.analyzer.HealthAnalyzer"),
            patch("zetherion_ai.health.healer.SelfHealer"),
            patch("zetherion_ai.health.collector.CollectorSources"),
        ):
            storage_instance = MagicMock()
            storage_instance.initialize = AsyncMock()
            mock_storage_cls.return_value = storage_instance

            result = await skill.initialize()

        assert result is True
        # storage.initialize should NOT have been awaited (no pool)
        storage_instance.initialize.assert_not_awaited()


# ---------------------------------------------------------------------------
# 3-6. on_heartbeat()
# ---------------------------------------------------------------------------


class TestOnHeartbeat:
    """Tests for the on_heartbeat() async method."""

    @pytest.mark.asyncio
    async def test_heartbeat_collects_snapshot(self) -> None:
        """on_heartbeat() should always collect and store a snapshot."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        await skill.on_heartbeat(["user1"])

        skill._collector.collect_all.assert_called_once()
        skill._storage.save_snapshot.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_heartbeat_increments_beat_count(self) -> None:
        """on_heartbeat() should increment _beat_count each call."""
        skill = _make_initialized_skill()
        assert skill._beat_count == 0

        await skill.on_heartbeat(["user1"])
        assert skill._beat_count == 1

        await skill.on_heartbeat(["user1"])
        assert skill._beat_count == 2

    @pytest.mark.asyncio
    async def test_heartbeat_triggers_analysis_on_6th_beat(self) -> None:
        """on_heartbeat() should run analysis every 6th beat."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        # Prepare analyzer result with no anomalies
        analysis_result = MagicMock()
        analysis_result.anomalies = []
        analysis_result.has_critical = False
        analysis_result.recommended_actions = []
        skill._analyzer.analyze_snapshot.return_value = analysis_result

        # Beats 1-5: no analysis
        for _ in range(5):
            await skill.on_heartbeat(["user1"])
        skill._analyzer.analyze_snapshot.assert_not_called()

        # Beat 6: analysis triggered
        await skill.on_heartbeat(["user1"])
        skill._analyzer.analyze_snapshot.assert_called_once()

    @pytest.mark.asyncio
    async def test_heartbeat_no_analysis_before_6th_beat(self) -> None:
        """on_heartbeat() should NOT run analysis on beats 1-5."""
        skill = _make_initialized_skill()
        skill._beat_count = 4  # next beat will be 5

        await skill.on_heartbeat(["user1"])

        assert skill._beat_count == 5
        skill._analyzer.analyze_snapshot.assert_not_called()

    @pytest.mark.asyncio
    async def test_heartbeat_returns_alert_on_critical_anomaly(self) -> None:
        """on_heartbeat() should return HeartbeatAction when critical anomaly detected."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        # Create a mock critical anomaly
        critical_anomaly = MagicMock()
        critical_anomaly.severity = "critical"
        critical_anomaly.description = "CPU usage extremely high"

        analysis_result = MagicMock()
        analysis_result.anomalies = [critical_anomaly]
        analysis_result.has_critical = True
        analysis_result.recommended_actions = ["restart_skill"]
        analysis_result.to_dict.return_value = {
            "anomalies": [{"description": "CPU usage extremely high"}],
            "has_critical": True,
        }

        skill._analyzer.analyze_snapshot.return_value = analysis_result

        # Set beat count to 5 so next heartbeat is the 6th
        skill._beat_count = 5

        actions = await skill.on_heartbeat(["owner_user"])

        assert len(actions) == 1
        action = actions[0]
        assert isinstance(action, HeartbeatAction)
        assert action.skill_name == "health_analyzer"
        assert action.action_type == "send_message"
        assert action.user_id == "owner_user"
        assert action.priority == 9
        assert "Health Alert" in action.data["message"]
        assert "CPU usage extremely high" in action.data["message"]

    @pytest.mark.asyncio
    async def test_heartbeat_no_alert_on_non_critical(self) -> None:
        """on_heartbeat() should NOT return alert when anomalies are only warnings."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        warning_anomaly = MagicMock()
        warning_anomaly.severity = "warning"
        warning_anomaly.description = "Latency slightly above baseline"

        analysis_result = MagicMock()
        analysis_result.anomalies = [warning_anomaly]
        analysis_result.has_critical = False
        analysis_result.recommended_actions = []
        analysis_result.to_dict.return_value = {"anomalies": [], "has_critical": False}

        skill._analyzer.analyze_snapshot.return_value = analysis_result
        skill._beat_count = 5

        actions = await skill.on_heartbeat(["owner_user"])

        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_heartbeat_generates_daily_report_on_288th_beat(self) -> None:
        """on_heartbeat() should generate a daily report on every 288th beat."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        # Mock the snapshots returned for daily report generation
        mock_snapshot = MagicMock()
        mock_snapshot.metrics = {"reliability": {}, "usage": {}, "skills": {}}
        skill._storage.get_snapshots.return_value = [mock_snapshot]

        # Mock the analyzer's daily report generation
        mock_report_data = MagicMock()
        mock_report_data.date = "2026-02-11"
        mock_report_data.summary = {"snapshot_count": 1}
        mock_report_data.recommendations = {"items": ["All good"]}
        mock_report_data.overall_score = 95.0
        skill._analyzer.generate_daily_report.return_value = mock_report_data

        # Also mock analyze_snapshot for the 288th beat (which is divisible by 6)
        analysis_result = MagicMock()
        analysis_result.anomalies = []
        analysis_result.has_critical = False
        analysis_result.recommended_actions = []
        skill._analyzer.analyze_snapshot.return_value = analysis_result

        # Set beat count to 287 so next beat is 288
        skill._beat_count = 287

        await skill.on_heartbeat(["user1"])

        assert skill._beat_count == 288
        skill._analyzer.generate_daily_report.assert_called_once()
        skill._storage.save_daily_report.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_heartbeat_no_daily_report_before_288(self) -> None:
        """on_heartbeat() should NOT generate a daily report on beat 144."""
        skill = _make_initialized_skill()

        # Also need analyze_snapshot mock since 144 is divisible by 6
        analysis_result = MagicMock()
        analysis_result.anomalies = []
        analysis_result.has_critical = False
        analysis_result.recommended_actions = []
        skill._analyzer.analyze_snapshot.return_value = analysis_result

        skill._beat_count = 143  # next beat is 144

        await skill.on_heartbeat(["user1"])

        assert skill._beat_count == 144
        skill._analyzer.generate_daily_report.assert_not_called()

    @pytest.mark.asyncio
    async def test_heartbeat_executes_self_healing(self) -> None:
        """on_heartbeat() should invoke the healer when analysis recommends actions."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        analysis_result = MagicMock()
        analysis_result.anomalies = []
        analysis_result.has_critical = False
        analysis_result.recommended_actions = ["restart_skill", "warm_ollama_models"]
        analysis_result.to_dict.return_value = {"anomalies": [], "has_critical": False}

        skill._analyzer.analyze_snapshot.return_value = analysis_result
        skill._beat_count = 5

        await skill.on_heartbeat(["user1"])

        skill._healer.execute_recommended.assert_awaited_once_with(
            ["restart_skill", "warm_ollama_models"], trigger="anomaly_detection"
        )

    @pytest.mark.asyncio
    async def test_heartbeat_returns_empty_on_normal_beat(self) -> None:
        """on_heartbeat() should return empty list on a normal (non-analysis) beat."""
        skill = _make_initialized_skill()

        actions = await skill.on_heartbeat(["user1"])

        assert actions == []


# ---------------------------------------------------------------------------
# 7-10. handle()
# ---------------------------------------------------------------------------


class TestHandle:
    """Tests for the handle() async method."""

    @pytest.mark.asyncio
    async def test_handle_health_check_healthy(self) -> None:
        """handle() with 'health_check' returns metrics and 'healthy' status."""
        skill = _make_initialized_skill()
        request = SkillRequest(user_id="123", intent="health_check", message="How is your health?")

        response = await skill.handle(request)

        assert response.success is True
        assert response.data["status"] == "healthy"
        assert "metrics" in response.data
        assert "healthy" in response.message

    @pytest.mark.asyncio
    async def test_handle_health_check_degraded_error_count(self) -> None:
        """handle() should report 'degraded' when skills have error_count > 0."""
        skill = _make_initialized_skill()
        skill._collector.collect_all.return_value = {
            "reliability": {"error_rate_by_provider": {}},
            "skills": {"error_count": 2, "ready_count": 3, "total_skills": 5},
        }
        request = SkillRequest(user_id="123", intent="health_check", message="status")

        response = await skill.handle(request)

        assert response.data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_handle_health_check_degraded_high_error_rate(self) -> None:
        """handle() should report 'degraded' when a provider error rate > 0.1."""
        skill = _make_initialized_skill()
        skill._collector.collect_all.return_value = {
            "reliability": {"error_rate_by_provider": {"ollama": 0.15}},
            "skills": {"error_count": 0, "ready_count": 3, "total_skills": 3},
        }
        request = SkillRequest(user_id="123", intent="health_check", message="status")

        response = await skill.handle(request)

        assert response.data["status"] == "degraded"

    @pytest.mark.asyncio
    async def test_handle_health_check_critical(self) -> None:
        """handle() should report 'critical' when no skills are ready."""
        skill = _make_initialized_skill()
        skill._collector.collect_all.return_value = {
            "reliability": {"error_rate_by_provider": {}},
            "skills": {"error_count": 0, "ready_count": 0, "total_skills": 5},
        }
        request = SkillRequest(user_id="123", intent="health_check", message="status")

        response = await skill.handle(request)

        assert response.data["status"] == "critical"

    @pytest.mark.asyncio
    async def test_handle_health_check_no_collector(self) -> None:
        """handle() with health_check should return empty metrics when collector is None."""
        skill = HealthAnalyzerSkill()
        request = SkillRequest(user_id="123", intent="health_check", message="status")

        response = await skill.handle(request)

        assert response.success is True
        assert response.data["status"] == "healthy"
        assert response.data["metrics"] == {}

    @pytest.mark.asyncio
    async def test_handle_health_report_with_report(self) -> None:
        """handle() with 'health_report' intent should return the daily report."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        mock_report = MagicMock()
        mock_report.date = "2026-02-11"
        mock_report.overall_score = 92.5
        mock_report.to_dict.return_value = {
            "date": "2026-02-11",
            "overall_score": 92.5,
            "summary": {"snapshot_count": 288},
            "recommendations": {"items": ["All good"]},
        }
        skill._storage.get_daily_report.return_value = mock_report

        request = SkillRequest(user_id="123", intent="health_report", message="Show health report")
        response = await skill.handle(request)

        assert response.success is True
        assert "92.5" in response.message
        assert response.data["date"] == "2026-02-11"

    @pytest.mark.asyncio
    async def test_handle_health_report_no_report(self) -> None:
        """handle() with 'health_report' returns info when no report exists."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)
        skill._storage.get_daily_report.return_value = None

        request = SkillRequest(user_id="123", intent="health_report", message="Show report")
        response = await skill.handle(request)

        assert response.success is True
        assert "No health reports available" in response.message

    @pytest.mark.asyncio
    async def test_handle_health_report_no_storage(self) -> None:
        """handle() with 'health_report' should return error when storage is unavailable."""
        skill = HealthAnalyzerSkill()
        # _storage is None

        request = SkillRequest(user_id="123", intent="health_report", message="Report?")
        response = await skill.handle(request)

        assert response.success is False
        assert "not available" in response.error

    @pytest.mark.asyncio
    async def test_handle_health_report_storage_no_pool(self) -> None:
        """handle() with 'health_report' should return error when storage has no pool."""
        skill = _make_initialized_skill(db_pool=None)
        skill._storage._pool = None

        request = SkillRequest(user_id="123", intent="health_report", message="Report?")
        response = await skill.handle(request)

        assert response.success is False
        assert "not available" in response.error

    @pytest.mark.asyncio
    async def test_handle_health_report_fallback_to_yesterday(self) -> None:
        """handle() should try yesterday's report if today's is not found."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        mock_report = MagicMock()
        mock_report.date = "2026-02-10"
        mock_report.overall_score = 88.0
        mock_report.to_dict.return_value = {
            "date": "2026-02-10",
            "overall_score": 88.0,
        }
        # First call (today) returns None, second call (yesterday) returns report
        skill._storage.get_daily_report.side_effect = [None, mock_report]

        request = SkillRequest(user_id="123", intent="health_report", message="report")
        response = await skill.handle(request)

        assert response.success is True
        assert "88.0" in response.message
        assert skill._storage.get_daily_report.call_count == 2

    @pytest.mark.asyncio
    async def test_handle_system_status(self) -> None:
        """handle() with 'system_status' intent should return detailed metrics."""
        skill = _make_initialized_skill()
        request = SkillRequest(
            user_id="123", intent="system_status", message="Give me system details"
        )

        response = await skill.handle(request)

        assert response.success is True
        assert "metrics" in response.data
        assert response.data["metrics"]["reliability"]["uptime_seconds"] == 3600
        assert "Detailed system status" in response.message

    @pytest.mark.asyncio
    async def test_handle_system_status_no_collector(self) -> None:
        """handle() with system_status should return empty metrics when collector is None."""
        skill = HealthAnalyzerSkill()
        request = SkillRequest(user_id="123", intent="system_status", message="system info")

        response = await skill.handle(request)

        assert response.success is True
        assert response.data["metrics"] == {}

    @pytest.mark.asyncio
    async def test_handle_unknown_intent(self) -> None:
        """handle() with unknown intent should return error response."""
        skill = _make_initialized_skill()
        request = SkillRequest(user_id="123", intent="do_magic", message="Do some magic")

        response = await skill.handle(request)

        assert response.success is False
        assert "Unknown health intent" in response.error
        assert "do_magic" in response.error


# ---------------------------------------------------------------------------
# 11-12. get_system_prompt_fragment()
# ---------------------------------------------------------------------------


class TestGetSystemPromptFragment:
    """Tests for the get_system_prompt_fragment() method."""

    def test_returns_health_summary_string(self) -> None:
        """get_system_prompt_fragment() should return a formatted health summary."""
        skill = _make_initialized_skill()

        fragment = skill.get_system_prompt_fragment("user1")

        assert fragment is not None
        assert "[Health]" in fragment
        assert "Uptime:" in fragment
        assert "Cost today:" in fragment
        assert "Skills:" in fragment
        # Values from the default mock: uptime 3600s -> 1.0h, cost $0.05, 3/4 ready
        assert "1.0h" in fragment
        assert "$0.05" in fragment
        assert "3/4 ready" in fragment

    def test_returns_none_when_collector_is_none(self) -> None:
        """get_system_prompt_fragment() should return None when collector is not set."""
        skill = HealthAnalyzerSkill()
        assert skill._collector is None

        fragment = skill.get_system_prompt_fragment("user1")

        assert fragment is None

    def test_returns_none_on_collector_exception(self) -> None:
        """get_system_prompt_fragment() should return None if collector.collect_all() raises."""
        skill = _make_initialized_skill()
        skill._collector.collect_all.side_effect = RuntimeError("boom")

        fragment = skill.get_system_prompt_fragment("user1")

        assert fragment is None

    def test_handles_missing_metric_keys_gracefully(self) -> None:
        """get_system_prompt_fragment() should handle empty metrics without error."""
        skill = _make_initialized_skill()
        skill._collector.collect_all.return_value = {}

        fragment = skill.get_system_prompt_fragment("user1")

        assert fragment is not None
        assert "0.0h" in fragment
        assert "$0" in fragment
        assert "0/0 ready" in fragment


# ---------------------------------------------------------------------------
# Edge cases and snapshot storage
# ---------------------------------------------------------------------------


class TestSnapshotStorage:
    """Tests for snapshot collection and storage edge cases."""

    @pytest.mark.asyncio
    async def test_snapshot_save_failure_does_not_raise(self) -> None:
        """_collect_snapshot() should not raise even if save_snapshot() fails."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)
        skill._storage.save_snapshot.side_effect = RuntimeError("DB down")

        # Should not raise
        actions = await skill.on_heartbeat(["user1"])

        assert isinstance(actions, list)

    @pytest.mark.asyncio
    async def test_snapshot_not_saved_when_pool_is_none(self) -> None:
        """_collect_snapshot() should skip save when storage pool is None."""
        skill = _make_initialized_skill(db_pool=None)
        skill._storage._pool = None

        await skill.on_heartbeat(["user1"])

        skill._storage.save_snapshot.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_analysis_with_baseline_fetch_failure(self) -> None:
        """_run_analysis() should gracefully handle baseline fetch failure."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)
        skill._storage.get_snapshots.side_effect = RuntimeError("DB error")

        skill._beat_count = 5

        actions = await skill.on_heartbeat(["user1"])

        # Should return empty actions (analysis failed gracefully)
        assert actions == []

    @pytest.mark.asyncio
    async def test_daily_report_generation_failure(self) -> None:
        """_generate_daily_report() should not raise on failure."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        # Make get_snapshots raise for daily report
        skill._storage.get_snapshots.side_effect = RuntimeError("DB error")

        # Also mock analyze_snapshot for the analysis path (288 is divisible by 6)
        analysis_result = MagicMock()
        analysis_result.anomalies = []
        analysis_result.has_critical = False
        analysis_result.recommended_actions = []
        skill._analyzer.analyze_snapshot.return_value = analysis_result

        skill._beat_count = 287

        # Should not raise
        actions = await skill.on_heartbeat(["user1"])
        assert isinstance(actions, list)

    @pytest.mark.asyncio
    async def test_multiple_critical_anomalies_limited_to_5(self) -> None:
        """Alert message should include at most 5 anomaly descriptions."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        # Create 7 critical anomalies
        critical_anomalies = []
        for i in range(7):
            a = MagicMock()
            a.severity = "critical"
            a.description = f"Critical issue {i}"
            critical_anomalies.append(a)

        analysis_result = MagicMock()
        analysis_result.anomalies = critical_anomalies
        analysis_result.has_critical = True
        analysis_result.recommended_actions = []
        analysis_result.to_dict.return_value = {"anomalies": [], "has_critical": True}

        skill._analyzer.analyze_snapshot.return_value = analysis_result
        skill._beat_count = 5

        actions = await skill.on_heartbeat(["owner"])

        assert len(actions) == 1
        msg = actions[0].data["message"]
        # Should include at most 5 summaries ([:5] in the source)
        assert "Critical issue 0" in msg
        assert "Critical issue 4" in msg
        assert "Critical issue 5" not in msg
        assert "Critical issue 6" not in msg

    @pytest.mark.asyncio
    async def test_heartbeat_no_alert_with_empty_user_ids(self) -> None:
        """on_heartbeat() should not create alert when user_ids is empty."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        critical_anomaly = MagicMock()
        critical_anomaly.severity = "critical"
        critical_anomaly.description = "Something bad"

        analysis_result = MagicMock()
        analysis_result.anomalies = [critical_anomaly]
        analysis_result.has_critical = True
        analysis_result.recommended_actions = []
        analysis_result.to_dict.return_value = {"anomalies": [], "has_critical": True}

        skill._analyzer.analyze_snapshot.return_value = analysis_result
        skill._beat_count = 5

        actions = await skill.on_heartbeat([])

        assert len(actions) == 0

    @pytest.mark.asyncio
    async def test_self_healing_failure_does_not_propagate(self) -> None:
        """on_heartbeat() should not raise when self-healer throws."""
        pool = _make_pool_mock()
        skill = _make_initialized_skill(db_pool=pool)

        analysis_result = MagicMock()
        analysis_result.anomalies = []
        analysis_result.has_critical = False
        analysis_result.recommended_actions = ["restart_skill"]
        analysis_result.to_dict.return_value = {"anomalies": [], "has_critical": False}

        skill._analyzer.analyze_snapshot.return_value = analysis_result
        skill._healer.execute_recommended.side_effect = RuntimeError("Heal failed")
        skill._beat_count = 5

        # Should not raise
        actions = await skill.on_heartbeat(["user1"])
        assert isinstance(actions, list)
