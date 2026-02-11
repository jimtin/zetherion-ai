"""Tests for FleetInsightsSkill."""

from __future__ import annotations

from unittest.mock import AsyncMock

from zetherion_ai.skills.base import HeartbeatAction, SkillRequest
from zetherion_ai.skills.fleet_insights import _WEEKLY_BEAT_INTERVAL, FleetInsightsSkill
from zetherion_ai.skills.permissions import Permission

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_receiver() -> AsyncMock:
    """Create a mock TelemetryReceiver with a default fleet summary."""
    receiver = AsyncMock()
    receiver.get_fleet_summary = AsyncMock(
        return_value={
            "total_instances": 3,
            "versions": {"0.1.0": 2, "0.2.0": 1},
            "last_report": "2026-02-11T10:00:00Z",
        }
    )
    return receiver


def _make_storage() -> AsyncMock:
    """Create a mock TelemetryStorage with default aggregates/reports."""
    storage = AsyncMock()
    storage.get_aggregates = AsyncMock(return_value=[{"id": 1}, {"id": 2}])
    storage.get_reports = AsyncMock(return_value=[{"id": 10}, {"id": 20}, {"id": 30}])
    return storage


def _req(intent: str = "fleet_status") -> SkillRequest:
    """Create a minimal SkillRequest with the given intent."""
    return SkillRequest(intent=intent, user_id="user-1", message="test")


# ---------------------------------------------------------------------------
# 1. metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    """Tests for the metadata property."""

    def test_metadata_name(self) -> None:
        """metadata.name should be 'fleet_insights'."""
        skill = FleetInsightsSkill()
        assert skill.metadata.name == "fleet_insights"

    def test_metadata_intents(self) -> None:
        """metadata.intents should list the three supported intents."""
        skill = FleetInsightsSkill()
        assert set(skill.metadata.intents) == {
            "fleet_status",
            "fleet_report",
            "fleet_health",
        }

    def test_metadata_permissions(self) -> None:
        """metadata.permissions should contain READ_CONFIG and SEND_MESSAGES."""
        skill = FleetInsightsSkill()
        perms = skill.metadata.permissions
        assert Permission.READ_CONFIG in perms
        assert Permission.SEND_MESSAGES in perms

    def test_metadata_version(self) -> None:
        """metadata.version should be set."""
        skill = FleetInsightsSkill()
        assert skill.metadata.version == "0.1.0"

    def test_metadata_description(self) -> None:
        """metadata.description should be a non-empty string."""
        skill = FleetInsightsSkill()
        assert len(skill.metadata.description) > 0


# ---------------------------------------------------------------------------
# 2. initialize()
# ---------------------------------------------------------------------------


class TestInitialize:
    """Tests for the initialize() async method."""

    async def test_initialize_returns_true(self) -> None:
        """initialize() should always return True."""
        skill = FleetInsightsSkill(receiver=_make_receiver(), storage=_make_storage())
        result = await skill.initialize()
        assert result is True

    async def test_initialize_with_no_receiver(self) -> None:
        """initialize() should return True even when receiver is None."""
        skill = FleetInsightsSkill(receiver=None, storage=None)
        result = await skill.initialize()
        assert result is True


# ---------------------------------------------------------------------------
# 3. handle()
# ---------------------------------------------------------------------------


class TestHandle:
    """Tests for the handle() async method — intent routing."""

    async def test_handle_routes_fleet_status(self) -> None:
        """handle() should route 'fleet_status' to _handle_status."""
        receiver = _make_receiver()
        skill = FleetInsightsSkill(receiver=receiver)
        request = _req("fleet_status")

        response = await skill.handle(request)

        assert response.success is True
        assert "reporting to central" in response.message
        receiver.get_fleet_summary.assert_awaited_once()

    async def test_handle_routes_fleet_report(self) -> None:
        """handle() should route 'fleet_report' to _handle_report."""
        storage = _make_storage()
        skill = FleetInsightsSkill(storage=storage)
        request = _req("fleet_report")

        response = await skill.handle(request)

        assert response.success is True
        assert "report(s)" in response.message
        storage.get_reports.assert_awaited_once()

    async def test_handle_routes_fleet_health(self) -> None:
        """handle() should route 'fleet_health' to _handle_health."""
        receiver = _make_receiver()
        skill = FleetInsightsSkill(receiver=receiver)
        request = _req("fleet_health")

        response = await skill.handle(request)

        assert response.success is True
        assert response.message == "Fleet health overview"
        receiver.get_fleet_summary.assert_awaited_once()

    async def test_handle_unknown_intent_returns_error(self) -> None:
        """handle() with an unknown intent should return an error response."""
        skill = FleetInsightsSkill()
        request = _req("fleet_destroy")

        response = await skill.handle(request)

        assert response.success is False
        assert "Unknown fleet intent" in response.error
        assert "fleet_destroy" in response.error


# ---------------------------------------------------------------------------
# 4. on_heartbeat()
# ---------------------------------------------------------------------------


class TestOnHeartbeat:
    """Tests for the on_heartbeat() async method."""

    async def test_no_receiver_returns_empty(self) -> None:
        """on_heartbeat() should return empty actions when receiver is None."""
        skill = FleetInsightsSkill(receiver=None)
        actions = await skill.on_heartbeat(["user-1"])
        assert actions == []

    async def test_beat_not_divisible_by_interval_returns_empty(self) -> None:
        """on_heartbeat() should return empty when beat count is not on the weekly boundary."""
        receiver = _make_receiver()
        skill = FleetInsightsSkill(receiver=receiver)

        # Beats 1 through _WEEKLY_BEAT_INTERVAL - 1 should all return empty
        for _ in range(5):
            actions = await skill.on_heartbeat(["user-1"])
            assert actions == []

        # Receiver should never have been called for summary
        receiver.get_fleet_summary.assert_not_awaited()

    async def test_beat_divisible_but_zero_instances_returns_empty(self) -> None:
        """on_heartbeat() should return empty when fleet has 0 instances."""
        receiver = AsyncMock()
        receiver.get_fleet_summary = AsyncMock(return_value={"total_instances": 0})
        skill = FleetInsightsSkill(receiver=receiver)
        skill._beat_count = _WEEKLY_BEAT_INTERVAL - 1  # next beat will be exactly 2016

        actions = await skill.on_heartbeat(["user-1"])

        assert actions == []
        receiver.get_fleet_summary.assert_awaited_once()

    async def test_beat_divisible_with_instances_generates_action(self) -> None:
        """on_heartbeat() should generate a HeartbeatAction at the weekly interval."""
        receiver = _make_receiver()
        skill = FleetInsightsSkill(receiver=receiver)
        skill._beat_count = _WEEKLY_BEAT_INTERVAL - 1  # next beat triggers

        actions = await skill.on_heartbeat(["owner-1"])

        assert len(actions) == 1
        action = actions[0]
        assert isinstance(action, HeartbeatAction)
        assert action.skill_name == "fleet_insights"
        assert action.action_type == "send_message"
        assert action.user_id == "owner-1"
        assert action.priority == 5
        assert "Weekly Fleet Report" in action.data["message"]
        assert "3 instances reporting" in action.data["message"]

    async def test_empty_user_ids_uses_empty_string(self) -> None:
        """on_heartbeat() should use empty string for user_id when user_ids is empty."""
        receiver = _make_receiver()
        skill = FleetInsightsSkill(receiver=receiver)
        skill._beat_count = _WEEKLY_BEAT_INTERVAL - 1

        actions = await skill.on_heartbeat([])

        assert len(actions) == 1
        assert actions[0].user_id == ""

    async def test_increments_beat_count_each_call(self) -> None:
        """on_heartbeat() should increment _beat_count by 1 each call."""
        skill = FleetInsightsSkill()
        assert skill._beat_count == 0

        await skill.on_heartbeat(["u"])
        assert skill._beat_count == 1

        await skill.on_heartbeat(["u"])
        assert skill._beat_count == 2

    async def test_action_includes_versions_in_message(self) -> None:
        """on_heartbeat() action message should include version info from the summary."""
        receiver = _make_receiver()
        skill = FleetInsightsSkill(receiver=receiver)
        skill._beat_count = _WEEKLY_BEAT_INTERVAL - 1

        actions = await skill.on_heartbeat(["user-1"])

        msg = actions[0].data["message"]
        assert "Versions:" in msg
        assert "0.1.0" in msg

    async def test_second_weekly_boundary_also_triggers(self) -> None:
        """on_heartbeat() should trigger again at 2 * _WEEKLY_BEAT_INTERVAL."""
        receiver = _make_receiver()
        skill = FleetInsightsSkill(receiver=receiver)
        skill._beat_count = 2 * _WEEKLY_BEAT_INTERVAL - 1

        actions = await skill.on_heartbeat(["user-1"])

        assert len(actions) == 1
        assert skill._beat_count == 2 * _WEEKLY_BEAT_INTERVAL


# ---------------------------------------------------------------------------
# 5. get_system_prompt_fragment()
# ---------------------------------------------------------------------------


class TestGetSystemPromptFragment:
    """Tests for the get_system_prompt_fragment() method."""

    def test_returns_none(self) -> None:
        """get_system_prompt_fragment() should always return None."""
        skill = FleetInsightsSkill()
        assert skill.get_system_prompt_fragment("user-1") is None

    def test_returns_none_with_receiver(self) -> None:
        """get_system_prompt_fragment() should return None even when receiver is present."""
        skill = FleetInsightsSkill(receiver=_make_receiver(), storage=_make_storage())
        assert skill.get_system_prompt_fragment("user-1") is None


# ---------------------------------------------------------------------------
# 6. _handle_status()
# ---------------------------------------------------------------------------


class TestHandleStatus:
    """Tests for the _handle_status() internal handler."""

    async def test_no_receiver_returns_error(self) -> None:
        """_handle_status() should return error when receiver is None."""
        skill = FleetInsightsSkill(receiver=None)
        request = _req("fleet_status")

        response = await skill.handle(request)

        assert response.success is False
        assert "not configured" in response.error

    async def test_with_receiver_returns_summary(self) -> None:
        """_handle_status() should return the fleet summary from the receiver."""
        receiver = _make_receiver()
        skill = FleetInsightsSkill(receiver=receiver)
        request = _req("fleet_status")

        response = await skill.handle(request)

        assert response.success is True
        assert response.data["total_instances"] == 3
        assert "3 instance(s) reporting to central" in response.message
        assert response.request_id == request.id

    async def test_with_zero_instances(self) -> None:
        """_handle_status() should report 0 when summary has zero instances."""
        receiver = AsyncMock()
        receiver.get_fleet_summary = AsyncMock(return_value={"total_instances": 0})
        skill = FleetInsightsSkill(receiver=receiver)
        request = _req("fleet_status")

        response = await skill.handle(request)

        assert response.success is True
        assert "0 instance(s)" in response.message


# ---------------------------------------------------------------------------
# 7. _handle_report()
# ---------------------------------------------------------------------------


class TestHandleReport:
    """Tests for the _handle_report() internal handler."""

    async def test_no_storage_returns_error(self) -> None:
        """_handle_report() should return error when storage is None."""
        skill = FleetInsightsSkill(storage=None)
        request = _req("fleet_report")

        response = await skill.handle(request)

        assert response.success is False
        assert "not configured" in response.error

    async def test_with_storage_returns_counts(self) -> None:
        """_handle_report() should return aggregate and report counts."""
        storage = _make_storage()
        skill = FleetInsightsSkill(storage=storage)
        request = _req("fleet_report")

        response = await skill.handle(request)

        assert response.success is True
        assert response.data["recent_aggregates"] == 2
        assert response.data["recent_reports"] == 3
        assert "3 recent report(s)" in response.message
        assert "2 aggregate(s)" in response.message
        storage.get_aggregates.assert_awaited_once_with(limit=10)
        storage.get_reports.assert_awaited_once_with(limit=10)

    async def test_with_empty_storage(self) -> None:
        """_handle_report() should handle empty aggregates and reports."""
        storage = AsyncMock()
        storage.get_aggregates = AsyncMock(return_value=[])
        storage.get_reports = AsyncMock(return_value=[])
        skill = FleetInsightsSkill(storage=storage)
        request = _req("fleet_report")

        response = await skill.handle(request)

        assert response.success is True
        assert response.data["recent_aggregates"] == 0
        assert response.data["recent_reports"] == 0
        assert "0 recent report(s)" in response.message


# ---------------------------------------------------------------------------
# 8. _handle_health()
# ---------------------------------------------------------------------------


class TestHandleHealth:
    """Tests for the _handle_health() internal handler."""

    async def test_no_receiver_returns_error(self) -> None:
        """_handle_health() should return error when receiver is None."""
        skill = FleetInsightsSkill(receiver=None)
        request = _req("fleet_health")

        response = await skill.handle(request)

        assert response.success is False
        assert "not configured" in response.error

    async def test_with_receiver_returns_health_data(self) -> None:
        """_handle_health() should return fleet health data from the receiver."""
        receiver = _make_receiver()
        skill = FleetInsightsSkill(receiver=receiver)
        request = _req("fleet_health")

        response = await skill.handle(request)

        assert response.success is True
        assert response.message == "Fleet health overview"
        assert response.data["total_instances"] == 3
        assert response.data["versions"] == {"0.1.0": 2, "0.2.0": 1}
        assert response.data["last_report"] == "2026-02-11T10:00:00Z"
        assert response.request_id == request.id

    async def test_health_with_missing_summary_fields(self) -> None:
        """_handle_health() should use defaults for missing summary fields."""
        receiver = AsyncMock()
        receiver.get_fleet_summary = AsyncMock(return_value={})
        skill = FleetInsightsSkill(receiver=receiver)
        request = _req("fleet_health")

        response = await skill.handle(request)

        assert response.success is True
        assert response.data["total_instances"] == 0
        assert response.data["versions"] == {}
        assert response.data["last_report"] is None
