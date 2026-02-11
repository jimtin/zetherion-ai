"""HTTP integration tests for the Fleet Insights skill and telemetry layer.

Exercises real HTTP communication with the SkillsServer hosting a
FleetInsightsSkill.  Uses ``aiohttp.test_utils.TestServer`` in-process
(no Docker required).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
import pytest_asyncio
from aiohttp.test_utils import TestServer

from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.client import SkillsClient
from zetherion_ai.skills.fleet_insights import FleetInsightsSkill
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_USER = "user-fleet"

MOCK_FLEET_SUMMARY_EMPTY: dict = {
    "total_instances": 0,
    "versions": {},
    "last_report": None,
}

MOCK_FLEET_SUMMARY_3: dict = {
    "total_instances": 3,
    "versions": {"0.1.0": 2, "0.2.0": 1},
    "last_report": "2026-02-11T12:00:00",
}

MOCK_REPORTS: list[dict] = [
    {"instance_id": "inst-1", "timestamp": "2026-02-11T11:00:00", "version": "0.1.0"},
    {"instance_id": "inst-2", "timestamp": "2026-02-11T11:30:00", "version": "0.1.0"},
    {"instance_id": "inst-3", "timestamp": "2026-02-11T12:00:00", "version": "0.2.0"},
]

MOCK_AGGREGATES: list[dict] = [
    {"metric_name": "latency_p95", "period_start": "2026-02-10", "value": 450},
    {"metric_name": "error_rate", "period_start": "2026-02-10", "value": 0.02},
]


def _build_mock_receiver(summary: dict | None = None) -> MagicMock:
    """Create a mock TelemetryReceiver with sensible defaults."""
    receiver = MagicMock()
    receiver.get_fleet_summary = AsyncMock(
        return_value=summary if summary is not None else MOCK_FLEET_SUMMARY_EMPTY,
    )
    return receiver


def _build_mock_storage(
    reports: list[dict] | None = None,
    aggregates: list[dict] | None = None,
) -> MagicMock:
    """Create a mock TelemetryStorage with sensible defaults."""
    storage = MagicMock()
    storage._pool = MagicMock()
    storage.get_reports = AsyncMock(return_value=reports if reports is not None else [])
    storage.get_aggregates = AsyncMock(
        return_value=aggregates if aggregates is not None else [],
    )
    return storage


def _mock_skill_internals(
    skill: FleetInsightsSkill,
    *,
    receiver: MagicMock | None = None,
    storage: MagicMock | None = None,
) -> None:
    """Replace internals with mocks AFTER the skill has been initialized."""
    if receiver is not None:
        skill._receiver = receiver
    if storage is not None:
        skill._storage = storage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def registry_with_fleet() -> SkillRegistry:
    """Build a SkillRegistry with the FleetInsightsSkill."""
    reg = SkillRegistry()
    skill = FleetInsightsSkill()
    reg.register(skill)
    await reg.initialize_all()

    # Replace internals with mocks AFTER initialization
    _mock_skill_internals(
        skill,
        receiver=_build_mock_receiver(MOCK_FLEET_SUMMARY_EMPTY),
        storage=_build_mock_storage(),
    )
    return reg


@pytest_asyncio.fixture()
async def server_and_client(
    registry_with_fleet: SkillRegistry,
) -> tuple[SkillsServer, SkillsClient, str]:
    """Start SkillsServer on a random port; return (server, client, base_url)."""
    server = SkillsServer(registry=registry_with_fleet, api_secret="test-fleet")
    app = server.create_app()

    test_server = TestServer(app)
    await test_server.start_server()

    base_url = f"http://{test_server.host}:{test_server.port}"
    client = SkillsClient(base_url=base_url, api_secret="test-fleet")

    yield server, client, base_url  # type: ignore[misc]

    await client.close()
    await test_server.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_health_check_with_fleet_skill(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """GET /health with fleet_insights skill registered should be healthy."""
    _server, client, _url = server_and_client
    result = await client.health_check()
    assert result is True


@pytest.mark.integration
async def test_fleet_status_no_instances(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
    registry_with_fleet: SkillRegistry,
) -> None:
    """POST /handle fleet_status with no instances returns 0 count."""
    skill = registry_with_fleet.get_skill("fleet_insights")
    assert skill is not None
    skill._receiver = _build_mock_receiver(MOCK_FLEET_SUMMARY_EMPTY)

    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="fleet_status",
        message="Fleet status please",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert response.data["total_instances"] == 0
    assert "0 instance" in response.message


@pytest.mark.integration
async def test_fleet_status_with_instances(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
    registry_with_fleet: SkillRegistry,
) -> None:
    """POST /handle fleet_status with 3 instances returns correct counts."""
    skill = registry_with_fleet.get_skill("fleet_insights")
    assert skill is not None
    skill._receiver = _build_mock_receiver(MOCK_FLEET_SUMMARY_3)

    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="fleet_status",
        message="How many instances?",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert response.data["total_instances"] == 3
    assert response.data["versions"] == {"0.1.0": 2, "0.2.0": 1}
    assert "3 instance" in response.message


@pytest.mark.integration
async def test_fleet_report_no_data(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
    registry_with_fleet: SkillRegistry,
) -> None:
    """POST /handle fleet_report with empty storage returns 0 counts."""
    skill = registry_with_fleet.get_skill("fleet_insights")
    assert skill is not None
    skill._storage = _build_mock_storage(reports=[], aggregates=[])

    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="fleet_report",
        message="Show fleet report",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert response.data["recent_reports"] == 0
    assert response.data["recent_aggregates"] == 0


@pytest.mark.integration
async def test_fleet_report_with_data(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
    registry_with_fleet: SkillRegistry,
) -> None:
    """POST /handle fleet_report with aggregates/reports returns correct counts."""
    skill = registry_with_fleet.get_skill("fleet_insights")
    assert skill is not None
    skill._storage = _build_mock_storage(
        reports=MOCK_REPORTS,
        aggregates=MOCK_AGGREGATES,
    )

    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="fleet_report",
        message="Full fleet report",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert response.data["recent_reports"] == 3
    assert response.data["recent_aggregates"] == 2
    assert "3 recent report" in response.message
    assert "2 aggregate" in response.message


@pytest.mark.integration
async def test_fleet_health_overview(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
    registry_with_fleet: SkillRegistry,
) -> None:
    """POST /handle fleet_health returns health overview from receiver."""
    skill = registry_with_fleet.get_skill("fleet_insights")
    assert skill is not None
    skill._receiver = _build_mock_receiver(MOCK_FLEET_SUMMARY_3)

    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="fleet_health",
        message="Fleet health?",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert response.data["total_instances"] == 3
    assert response.data["versions"] == {"0.1.0": 2, "0.2.0": 1}
    assert response.data["last_report"] == "2026-02-11T12:00:00"
    assert "health overview" in response.message.lower()


@pytest.mark.integration
async def test_fleet_unknown_intent(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /handle with unknown intent should return error response."""
    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="fleet_teleport",
        message="Beam me up",
    )
    response = await client.handle_request(request)

    assert response.success is False


@pytest.mark.integration
async def test_heartbeat_not_weekly(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /heartbeat on beat 1 returns empty actions (weekly interval not reached)."""
    _server, _client, base_url = server_and_client

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{base_url}/heartbeat",
            json={"user_ids": [TEST_USER]},
            headers={"X-API-Secret": "test-fleet"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "actions" in data
    # Beat 1 is not a multiple of the weekly interval (2016), so no actions
    fleet_actions = [a for a in data["actions"] if a.get("skill_name") == "fleet_insights"]
    assert len(fleet_actions) == 0


@pytest.mark.integration
async def test_status_includes_fleet_insights(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """GET /status should include fleet_insights in ready skills."""
    _server, client, _url = server_and_client
    status = await client.get_status()

    assert status is not None
    assert status["total_skills"] == 1
    assert status["ready_count"] == 1
    assert "fleet_insights" in status["by_status"]["ready"]


@pytest.mark.integration
async def test_intents_include_fleet(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """GET /intents should include fleet_status, fleet_report, fleet_health."""
    _server, _client, base_url = server_and_client

    async with httpx.AsyncClient() as http:
        resp = await http.get(
            f"{base_url}/intents",
            headers={"X-API-Secret": "test-fleet"},
        )

    assert resp.status_code == 200
    data = resp.json()
    intents = data["intents"]
    assert "fleet_status" in intents
    assert "fleet_report" in intents
    assert "fleet_health" in intents
