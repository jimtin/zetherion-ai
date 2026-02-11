"""HTTP integration tests for the Health Analyzer skill.

Exercises real HTTP communication with the SkillsServer hosting a
HealthAnalyzerSkill.  Uses ``aiohttp.test_utils.TestServer`` in-process
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
from zetherion_ai.skills.health_analyzer import HealthAnalyzerSkill
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TEST_USER = "user-health"

MOCK_METRICS = {
    "performance": {
        "avg_latency_ms": {"ollama": 350.0},
        "p95_latency_ms": {"ollama": 600.0},
    },
    "reliability": {
        "error_rate_by_provider": {"ollama": 0.01},
        "rate_limit_count": 0,
        "uptime_seconds": 7200.0,
    },
    "usage": {"total_cost_usd_today": 0.25},
    "system": {"memory_rss_mb": 512.0},
    "skills": {"total_skills": 4, "ready_count": 4, "error_count": 0},
}


def _mock_skill_internals(skill: HealthAnalyzerSkill) -> None:
    """Replace internals with mocks AFTER the skill has been initialized."""
    skill._collector = MagicMock()
    skill._collector.collect_all.return_value = MOCK_METRICS

    skill._analyzer = MagicMock()
    skill._healer = MagicMock()

    mock_storage = MagicMock()
    mock_storage._pool = MagicMock()
    mock_storage.get_daily_report = AsyncMock(return_value=None)
    mock_storage.save_snapshot = AsyncMock()
    mock_storage.get_snapshots = AsyncMock(return_value=[])
    skill._storage = mock_storage


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def registry_with_health() -> SkillRegistry:
    """Build a SkillRegistry with the HealthAnalyzerSkill."""
    reg = SkillRegistry()
    skill = HealthAnalyzerSkill()
    reg.register(skill)
    await reg.initialize_all()

    # Replace internals with mocks AFTER initialization
    _mock_skill_internals(skill)
    return reg


@pytest_asyncio.fixture()
async def server_and_client(
    registry_with_health: SkillRegistry,
) -> tuple[SkillsServer, SkillsClient, str]:
    """Start SkillsServer on a random port; return (server, client, base_url)."""
    server = SkillsServer(registry=registry_with_health, api_secret="test-health")
    app = server.create_app()

    test_server = TestServer(app)
    await test_server.start_server()

    base_url = f"http://{test_server.host}:{test_server.port}"
    client = SkillsClient(base_url=base_url, api_secret="test-health")

    yield server, client, base_url  # type: ignore[misc]

    await client.close()
    await test_server.close()


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_health_check_endpoint(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """GET /health should include health_analyzer in the ready count."""
    _server, client, _url = server_and_client
    result = await client.health_check()
    assert result is True


@pytest.mark.integration
async def test_handle_health_check_intent(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /handle with health_check intent returns metrics."""
    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="health_check",
        message="How is your health?",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert response.data["status"] == "healthy"
    assert "metrics" in response.data
    assert response.data["metrics"]["reliability"]["uptime_seconds"] == 7200.0


@pytest.mark.integration
async def test_handle_system_status_intent(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /handle with system_status intent returns detailed metrics."""
    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="system_status",
        message="Show system status",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert "metrics" in response.data
    metrics = response.data["metrics"]
    assert "performance" in metrics
    assert "reliability" in metrics
    assert "system" in metrics


@pytest.mark.integration
async def test_handle_health_report_no_data(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /handle with health_report intent when no reports exist."""
    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="health_report",
        message="Show health report",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert "No health reports available" in response.message


@pytest.mark.integration
async def test_handle_health_report_with_data(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
    registry_with_health: SkillRegistry,
) -> None:
    """POST /handle with health_report when a report exists."""
    skill = registry_with_health.get_skill("health_analyzer")
    assert skill is not None

    mock_report = MagicMock()
    mock_report.date = "2026-02-11"
    mock_report.overall_score = 95.0
    mock_report.to_dict.return_value = {
        "date": "2026-02-11",
        "overall_score": 95.0,
        "summary": {"snapshot_count": 288},
        "recommendations": {"items": ["All good"]},
    }
    skill._storage.get_daily_report = AsyncMock(return_value=mock_report)

    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="health_report",
        message="Show health report",
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert "95.0" in response.message
    assert response.data["date"] == "2026-02-11"


@pytest.mark.integration
async def test_handle_unknown_intent(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """POST /handle with unknown intent should return error."""
    _server, client, _url = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="magic_intent",
        message="Do magic",
    )
    response = await client.handle_request(request)

    assert response.success is False


@pytest.mark.integration
async def test_heartbeat_collects_snapshot(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
    registry_with_health: SkillRegistry,
) -> None:
    """POST /heartbeat should trigger snapshot collection."""
    _server, _client, base_url = server_and_client

    async with httpx.AsyncClient() as http:
        resp = await http.post(
            f"{base_url}/heartbeat",
            json={"user_ids": [TEST_USER]},
            headers={"X-API-Secret": "test-health"},
        )

    assert resp.status_code == 200
    data = resp.json()
    assert "actions" in data

    skill = registry_with_health.get_skill("health_analyzer")
    assert skill is not None
    skill._collector.collect_all.assert_called()


@pytest.mark.integration
async def test_status_includes_health_analyzer(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """GET /status should include health_analyzer in the status summary."""
    _server, client, _url = server_and_client
    status = await client.get_status()

    assert status is not None
    assert status["total_skills"] == 1
    assert status["ready_count"] == 1
    assert "health_analyzer" in status["by_status"]["ready"]


@pytest.mark.integration
async def test_prompt_fragments_include_health(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """GET /prompt-fragments should include health summary."""
    _server, client, _url = server_and_client
    fragments = await client.get_prompt_fragments(TEST_USER)

    assert isinstance(fragments, list)
    health_frags = [f for f in fragments if "Health" in f or "Uptime" in f]
    assert len(health_frags) >= 1


@pytest.mark.integration
async def test_intents_include_health(
    server_and_client: tuple[SkillsServer, SkillsClient, str],
) -> None:
    """GET /intents should include health-related intents."""
    _server, _client, base_url = server_and_client

    async with httpx.AsyncClient() as http:
        resp = await http.get(
            f"{base_url}/intents",
            headers={"X-API-Secret": "test-health"},
        )

    assert resp.status_code == 200
    data = resp.json()
    intents = data["intents"]
    assert "health_check" in intents
    assert "health_report" in intents
    assert "system_status" in intents
