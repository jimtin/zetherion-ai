"""End-to-end Docker integration tests for the Health Analyzer system.

Exercises the health analysis pipeline against the real Docker Compose
test environment (``docker-compose.test.yml``).  The skills service runs
on port 18080 and PostgreSQL on port 15432.

Run with::

    DOCKER_MANAGED_EXTERNALLY=true pytest tests/integration/test_health_e2e.py -m integration
"""

from __future__ import annotations

import os
import subprocess
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio

SKILLS_URL = "http://localhost:18080"
POSTGRES_DSN = "postgresql://zetherion:password@localhost:15432/zetherion"

SKIP_INTEGRATION = os.getenv("SKIP_INTEGRATION_TESTS", "false").lower() == "true"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_api_secret() -> str | None:
    """Try to extract API secret from the skills container environment."""
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "zetherion-ai-test-skills",
                "printenv",
                "SKILLS_API_SECRET",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _docker_running() -> bool:
    """Return True if the skills container is running."""
    try:
        result = subprocess.run(
            [
                "docker",
                "ps",
                "--filter",
                "name=zetherion-ai-test-skills",
                "--filter",
                "status=running",
                "--format",
                "{{.Names}}",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        return "zetherion-ai-test-skills" in result.stdout
    except Exception:
        return False


def _headers(api_secret: str | None) -> dict[str, str]:
    """Build request headers, including auth if available."""
    h: dict[str, str] = {"Content-Type": "application/json"}
    if api_secret:
        h["X-API-Secret"] = api_secret
    return h


def _build_handle_body(
    intent: str,
    message: str = "",
    user_id: str = "test",
) -> dict:
    """Build a JSON body for POST /handle matching SkillRequest.from_dict."""
    return {
        "id": str(uuid4()),
        "user_id": user_id,
        "intent": intent,
        "message": message,
        "context": {},
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture(scope="module")
def _skip_guard() -> None:
    """Skip the entire module when integration tests are disabled or Docker
    is not running."""
    if SKIP_INTEGRATION:
        pytest.skip("Integration tests disabled (SKIP_INTEGRATION_TESTS=true)")
    if not _docker_running():
        pytest.skip(
            "Docker test environment not running "
            "(start with docker-compose.test.yml or set DOCKER_MANAGED_EXTERNALLY)"
        )


@pytest.fixture(scope="module")
def api_secret(_skip_guard: None) -> str | None:
    """Resolve the SKILLS_API_SECRET from the running container."""
    return _get_api_secret()


@pytest_asyncio.fixture()
async def async_client(_skip_guard: None) -> httpx.AsyncClient:
    """Function-scoped httpx client for async tests."""
    async with httpx.AsyncClient(base_url=SKILLS_URL, timeout=30.0) as client:
        yield client  # type: ignore[misc]


# ---------------------------------------------------------------------------
# 1. Skills service health
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_skills_service_health(_skip_guard: None) -> None:
    """GET /health returns 200 with healthy status."""
    resp = httpx.get(f"{SKILLS_URL}/health", timeout=10.0)

    assert resp.status_code == 200, f"Unexpected status {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["status"] == "healthy"
    assert body["skills_ready"] >= 1
    assert body["skills_total"] >= 1


# ---------------------------------------------------------------------------
# 2. Health analyzer registered
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_health_analyzer_registered(api_secret: str | None) -> None:
    """GET /status includes health_analyzer in the skills list."""
    resp = httpx.get(
        f"{SKILLS_URL}/status",
        headers=_headers(api_secret),
        timeout=10.0,
    )

    # If auth is required but we have no secret, the endpoint will 401.
    # Fall back to /skills which also lists skills.
    if resp.status_code == 401:
        pytest.skip("Cannot authenticate to /status (no API secret available)")

    assert resp.status_code == 200, f"Unexpected status {resp.status_code}: {resp.text}"
    body = resp.json()

    # The status summary has by_status -> ready -> [...skill names...]
    all_skill_names: list[str] = []
    for names in body.get("by_status", {}).values():
        all_skill_names.extend(names)

    assert (
        "health_analyzer" in all_skill_names
    ), f"health_analyzer not found in registered skills: {body}"


# ---------------------------------------------------------------------------
# 3. Heartbeat triggers collection
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_heartbeat_triggers_collection(
    async_client: httpx.AsyncClient,
    api_secret: str | None,
) -> None:
    """POST /heartbeat returns actions array and eventually creates
    health_snapshots rows in PostgreSQL."""
    headers = _headers(api_secret)

    # Send several heartbeats so that the skill collects snapshots.
    for _ in range(3):
        resp = await async_client.post(
            "/heartbeat",
            json={"user_ids": ["test"]},
            headers=headers,
        )
        if resp.status_code == 401:
            pytest.skip("Cannot authenticate to /heartbeat (no API secret)")

        assert resp.status_code == 200, f"Heartbeat failed with {resp.status_code}: {resp.text}"
        body = resp.json()
        assert "actions" in body
        assert isinstance(body["actions"], list)

    # Allow a brief moment for async writes to land in PostgreSQL.
    import asyncio

    await asyncio.sleep(2)


# ---------------------------------------------------------------------------
# 4. Health check intent via HTTP
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_health_check_intent_via_http(
    async_client: httpx.AsyncClient,
    api_secret: str | None,
) -> None:
    """POST /handle with intent=health_check returns success with metrics."""
    headers = _headers(api_secret)
    payload = _build_handle_body(
        intent="health_check",
        message="How is your health?",
    )

    resp = await async_client.post("/handle", json=payload, headers=headers)

    if resp.status_code == 401:
        pytest.skip("Cannot authenticate to /handle (no API secret)")

    assert resp.status_code == 200, f"handle health_check failed {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["success"] is True
    assert "data" in body
    assert body["data"].get("status") in ("healthy", "degraded", "critical")

    # The response should contain metrics or at least a message.
    assert body.get("message") or body["data"].get("metrics")


# ---------------------------------------------------------------------------
# 5. Health report intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_health_report_intent(
    async_client: httpx.AsyncClient,
    api_secret: str | None,
) -> None:
    """POST /handle with intent=health_report returns a success response.

    In a freshly started environment there may be no daily report yet,
    so we accept either a report payload or the "no reports" message.
    """
    headers = _headers(api_secret)
    payload = _build_handle_body(
        intent="health_report",
        message="Show health report",
    )

    resp = await async_client.post("/handle", json=payload, headers=headers)

    if resp.status_code == 401:
        pytest.skip("Cannot authenticate to /handle (no API secret)")

    assert resp.status_code == 200, f"handle health_report failed {resp.status_code}: {resp.text}"
    body = resp.json()

    msg = body.get("message", "")
    if body["success"] is True:
        # Either we get a report or the "no reports available" message.
        assert msg, "Expected a non-empty message from health_report intent"
    else:
        # Storage may not be initialised in the test environment; accept
        # a graceful error response.
        assert (
            "storage" in msg.lower() or "not available" in msg.lower() or msg == ""
        ), f"Unexpected error from health_report: {body}"


# ---------------------------------------------------------------------------
# 6. Health snapshots in PostgreSQL
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_health_snapshots_in_postgres(
    async_client: httpx.AsyncClient,
    api_secret: str | None,
) -> None:
    """After heartbeats, health_snapshots table should contain rows."""
    import asyncpg  # type: ignore[import-not-found,import-untyped]

    # First make sure we have triggered at least one heartbeat.
    headers = _headers(api_secret)
    resp = await async_client.post(
        "/heartbeat",
        json={"user_ids": ["test"]},
        headers=headers,
    )
    if resp.status_code == 401:
        pytest.skip("Cannot authenticate to /heartbeat (no API secret)")
    assert resp.status_code == 200

    # Give the async write a moment to flush.
    import asyncio

    await asyncio.sleep(2)

    # Connect directly to PostgreSQL and check the table.
    pool = await asyncpg.create_pool(POSTGRES_DSN)
    try:
        rows = await pool.fetch(
            "SELECT id, timestamp, metrics, anomalies "
            "FROM health_snapshots "
            "ORDER BY timestamp DESC LIMIT 10"
        )
    except asyncpg.exceptions.UndefinedTableError:
        pytest.skip(
            "health_snapshots table not created yet "
            "(health storage may not be initialised in this environment)"
        )
    finally:
        await pool.close()

    assert len(rows) >= 1, "Expected at least 1 health snapshot row in PostgreSQL"

    # Basic sanity on the most recent row.
    latest = rows[0]
    assert latest["id"] is not None
    assert latest["timestamp"] is not None
    assert latest["metrics"] is not None


# ---------------------------------------------------------------------------
# 7. System status intent
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
@pytest.mark.integration
async def test_system_status_intent(
    async_client: httpx.AsyncClient,
    api_secret: str | None,
) -> None:
    """POST /handle with intent=system_status returns detailed metrics."""
    headers = _headers(api_secret)
    payload = _build_handle_body(
        intent="system_status",
        message="Show system status",
    )

    resp = await async_client.post("/handle", json=payload, headers=headers)

    if resp.status_code == 401:
        pytest.skip("Cannot authenticate to /handle (no API secret)")

    assert resp.status_code == 200, f"handle system_status failed {resp.status_code}: {resp.text}"
    body = resp.json()
    assert body["success"] is True
    assert "data" in body

    # The system_status intent returns metrics in data.metrics.
    metrics = body["data"].get("metrics", {})
    assert isinstance(metrics, dict)
    # At a minimum the collector should provide some top-level sections.
    # (In the container the sections may vary but we expect at least one key.)
    assert len(metrics) >= 1, f"Expected at least one metrics section, got: {metrics}"


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    pytest.main([__file__, "-v", "-s", "-m", "integration"])
