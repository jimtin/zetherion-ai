"""Tests for updater_sidecar.server — aiohttp REST API endpoints."""

from __future__ import annotations

from unittest.mock import AsyncMock, PropertyMock

from aiohttp.test_utils import TestClient, TestServer

from updater_sidecar.models import UpdateResult
from updater_sidecar.server import create_app

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_executor(
    state: str = "idle",
    current_operation: str | None = None,
    is_busy: bool = False,
) -> AsyncMock:
    """Return a mock UpdateExecutor with configurable properties."""
    executor = AsyncMock()
    type(executor).state = PropertyMock(return_value=state)
    type(executor).current_operation = PropertyMock(return_value=current_operation)
    type(executor).is_busy = PropertyMock(return_value=is_busy)
    return executor


async def _make_client(
    executor: AsyncMock | None = None,
    secret: str = "",
) -> TestClient:
    """Create an aiohttp TestClient wrapping our server app."""
    ex = executor or _make_mock_executor()
    app = create_app(executor=ex, secret=secret)
    client = TestClient(TestServer(app))
    await client.start_server()
    return client


# ---------------------------------------------------------------------------
# TestHealthEndpoint
# ---------------------------------------------------------------------------


class TestHealthEndpoint:
    """Tests for GET /health."""

    async def test_health_returns_200(self) -> None:
        client = await _make_client()
        try:
            resp = await client.get("/health")
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "ok"
        finally:
            await client.close()

    async def test_health_no_auth_required(self) -> None:
        """Health endpoint does not require auth even when secret is set."""
        client = await _make_client(secret="super-secret")
        try:
            resp = await client.get("/health")
            assert resp.status == 200
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# TestStatusEndpoint
# ---------------------------------------------------------------------------


class TestStatusEndpoint:
    """Tests for GET /status."""

    async def test_status_returns_sidecar_state(self) -> None:
        client = await _make_client()
        try:
            resp = await client.get("/status")
            assert resp.status == 200
            data = await resp.json()
            assert data["state"] == "idle"
            assert data["current_operation"] is None
            assert "uptime_seconds" in data
        finally:
            await client.close()

    async def test_status_requires_auth(self) -> None:
        client = await _make_client(secret="my-secret")
        try:
            resp = await client.get("/status")
            assert resp.status == 401
        finally:
            await client.close()

    async def test_status_with_valid_auth(self) -> None:
        client = await _make_client(secret="my-secret")
        try:
            resp = await client.get(
                "/status",
                headers={"X-Updater-Secret": "my-secret"},
            )
            assert resp.status == 200
        finally:
            await client.close()

    async def test_status_with_wrong_auth(self) -> None:
        client = await _make_client(secret="my-secret")
        try:
            resp = await client.get(
                "/status",
                headers={"X-Updater-Secret": "wrong-token"},
            )
            assert resp.status == 401
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# TestApplyEndpoint
# ---------------------------------------------------------------------------


class TestApplyEndpoint:
    """Tests for POST /update/apply."""

    async def test_apply_success(self) -> None:
        ex = _make_mock_executor()
        ex.apply_update = AsyncMock(
            return_value=UpdateResult(status="success", previous_sha="abc", new_sha="def")
        )
        client = await _make_client(executor=ex)
        try:
            resp = await client.post(
                "/update/apply",
                json={"tag": "v1.0.0", "version": "1.0.0"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "success"
            ex.apply_update.assert_awaited_once_with("v1.0.0", "1.0.0")
        finally:
            await client.close()

    async def test_apply_returns_500_on_failure(self) -> None:
        ex = _make_mock_executor()
        ex.apply_update = AsyncMock(
            return_value=UpdateResult(status="failed", error="git fetch failed")
        )
        client = await _make_client(executor=ex)
        try:
            resp = await client.post(
                "/update/apply",
                json={"tag": "v1.0.0", "version": "1.0.0"},
            )
            assert resp.status == 500
            data = await resp.json()
            assert data["status"] == "failed"
            assert data["error"] == "git fetch failed"
        finally:
            await client.close()

    async def test_apply_requires_auth(self) -> None:
        ex = _make_mock_executor()
        client = await _make_client(executor=ex, secret="my-secret")
        try:
            resp = await client.post(
                "/update/apply",
                json={"tag": "v1.0.0", "version": "1.0.0"},
            )
            assert resp.status == 401
        finally:
            await client.close()

    async def test_apply_with_valid_auth(self) -> None:
        ex = _make_mock_executor()
        ex.apply_update = AsyncMock(return_value=UpdateResult(status="success"))
        client = await _make_client(executor=ex, secret="my-secret")
        try:
            resp = await client.post(
                "/update/apply",
                json={"tag": "v1.0.0", "version": "1.0.0"},
                headers={"X-Updater-Secret": "my-secret"},
            )
            assert resp.status == 200
        finally:
            await client.close()

    async def test_apply_409_when_busy(self) -> None:
        ex = _make_mock_executor(is_busy=True)
        client = await _make_client(executor=ex)
        try:
            resp = await client.post(
                "/update/apply",
                json={"tag": "v1.0.0", "version": "1.0.0"},
            )
            assert resp.status == 409
            data = await resp.json()
            assert "already in progress" in data["error"].lower()
        finally:
            await client.close()

    async def test_apply_400_missing_tag(self) -> None:
        ex = _make_mock_executor()
        client = await _make_client(executor=ex)
        try:
            resp = await client.post(
                "/update/apply",
                json={"version": "1.0.0"},
            )
            assert resp.status == 400
            data = await resp.json()
            assert "tag" in data["error"].lower()
        finally:
            await client.close()

    async def test_apply_400_missing_version(self) -> None:
        ex = _make_mock_executor()
        client = await _make_client(executor=ex)
        try:
            resp = await client.post(
                "/update/apply",
                json={"tag": "v1.0.0"},
            )
            assert resp.status == 400
        finally:
            await client.close()

    async def test_apply_400_empty_body(self) -> None:
        ex = _make_mock_executor()
        client = await _make_client(executor=ex)
        try:
            resp = await client.post(
                "/update/apply",
                json={},
            )
            assert resp.status == 400
        finally:
            await client.close()

    async def test_apply_400_invalid_json(self) -> None:
        ex = _make_mock_executor()
        client = await _make_client(executor=ex)
        try:
            resp = await client.post(
                "/update/apply",
                data=b"not json",
                headers={"Content-Type": "application/json"},
            )
            assert resp.status == 400
        finally:
            await client.close()

    async def test_apply_records_history(self) -> None:
        ex = _make_mock_executor()
        ex.apply_update = AsyncMock(return_value=UpdateResult(status="success"))
        client = await _make_client(executor=ex)
        try:
            await client.post(
                "/update/apply",
                json={"tag": "v1.0.0", "version": "1.0.0"},
            )

            # Check history
            resp = await client.get("/update/history")
            data = await resp.json()
            assert len(data["entries"]) == 1
            assert data["entries"][0]["tag"] == "v1.0.0"
            assert data["entries"][0]["version"] == "1.0.0"
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# TestRollbackEndpoint
# ---------------------------------------------------------------------------


class TestRollbackEndpoint:
    """Tests for POST /update/rollback."""

    async def test_rollback_success(self) -> None:
        ex = _make_mock_executor()
        ex.rollback = AsyncMock(return_value=UpdateResult(status="success", new_sha="abc123"))
        client = await _make_client(executor=ex)
        try:
            resp = await client.post(
                "/update/rollback",
                json={"previous_sha": "abc123def456"},
            )
            assert resp.status == 200
            data = await resp.json()
            assert data["status"] == "success"
        finally:
            await client.close()

    async def test_rollback_requires_auth(self) -> None:
        ex = _make_mock_executor()
        client = await _make_client(executor=ex, secret="my-secret")
        try:
            resp = await client.post(
                "/update/rollback",
                json={"previous_sha": "abc123"},
            )
            assert resp.status == 401
        finally:
            await client.close()

    async def test_rollback_409_when_busy(self) -> None:
        ex = _make_mock_executor(is_busy=True)
        client = await _make_client(executor=ex)
        try:
            resp = await client.post(
                "/update/rollback",
                json={"previous_sha": "abc123"},
            )
            assert resp.status == 409
        finally:
            await client.close()

    async def test_rollback_400_missing_sha(self) -> None:
        ex = _make_mock_executor()
        client = await _make_client(executor=ex)
        try:
            resp = await client.post(
                "/update/rollback",
                json={},
            )
            assert resp.status == 400
        finally:
            await client.close()

    async def test_rollback_returns_500_on_failure(self) -> None:
        ex = _make_mock_executor()
        ex.rollback = AsyncMock(return_value=UpdateResult(status="failed", error="Rollback failed"))
        client = await _make_client(executor=ex)
        try:
            resp = await client.post(
                "/update/rollback",
                json={"previous_sha": "abc123"},
            )
            assert resp.status == 500
        finally:
            await client.close()

    async def test_rollback_records_history(self) -> None:
        ex = _make_mock_executor()
        ex.rollback = AsyncMock(return_value=UpdateResult(status="success"))
        client = await _make_client(executor=ex)
        try:
            await client.post(
                "/update/rollback",
                json={"previous_sha": "abc123def456"},
            )

            resp = await client.get("/update/history")
            data = await resp.json()
            assert len(data["entries"]) == 1
            assert data["entries"][0]["tag"].startswith("rollback:")
            assert data["entries"][0]["version"] == "rollback"
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# TestHistoryEndpoint
# ---------------------------------------------------------------------------


class TestHistoryEndpoint:
    """Tests for GET /update/history."""

    async def test_empty_history(self) -> None:
        client = await _make_client()
        try:
            resp = await client.get("/update/history")
            assert resp.status == 200
            data = await resp.json()
            assert data["entries"] == []
        finally:
            await client.close()

    async def test_history_requires_auth(self) -> None:
        client = await _make_client(secret="my-secret")
        try:
            resp = await client.get("/update/history")
            assert resp.status == 401
        finally:
            await client.close()

    async def test_history_with_valid_auth(self) -> None:
        client = await _make_client(secret="my-secret")
        try:
            resp = await client.get(
                "/update/history",
                headers={"X-Updater-Secret": "my-secret"},
            )
            assert resp.status == 200
        finally:
            await client.close()

    async def test_history_accumulates(self) -> None:
        ex = _make_mock_executor()
        ex.apply_update = AsyncMock(return_value=UpdateResult(status="success"))
        client = await _make_client(executor=ex)
        try:
            # Apply two updates
            await client.post(
                "/update/apply",
                json={"tag": "v1.0.0", "version": "1.0.0"},
            )
            await client.post(
                "/update/apply",
                json={"tag": "v2.0.0", "version": "2.0.0"},
            )

            resp = await client.get("/update/history")
            data = await resp.json()
            assert len(data["entries"]) == 2
            assert data["entries"][0]["tag"] == "v1.0.0"
            assert data["entries"][1]["tag"] == "v2.0.0"
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# TestDiagnosticsEndpoint
# ---------------------------------------------------------------------------


class TestDiagnosticsEndpoint:
    """Tests for GET /diagnostics."""

    async def test_diagnostics_returns_data(self) -> None:
        ex = _make_mock_executor()
        ex.get_diagnostics = AsyncMock(
            return_value={
                "git_sha": "abc123",
                "git_ref": "v1.0.0",
                "git_clean": True,
                "containers_raw": "{}",
                "disk_usage": "50%",
            }
        )
        client = await _make_client(executor=ex)
        try:
            resp = await client.get("/diagnostics")
            assert resp.status == 200
            data = await resp.json()
            assert data["git_sha"] == "abc123"
            assert data["git_clean"] is True
        finally:
            await client.close()

    async def test_diagnostics_requires_auth(self) -> None:
        client = await _make_client(secret="my-secret")
        try:
            resp = await client.get("/diagnostics")
            assert resp.status == 401
        finally:
            await client.close()

    async def test_diagnostics_with_valid_auth(self) -> None:
        ex = _make_mock_executor()
        ex.get_diagnostics = AsyncMock(return_value={"git_sha": "abc"})
        client = await _make_client(executor=ex, secret="my-secret")
        try:
            resp = await client.get(
                "/diagnostics",
                headers={"X-Updater-Secret": "my-secret"},
            )
            assert resp.status == 200
        finally:
            await client.close()


# ---------------------------------------------------------------------------
# TestAuthBehavior
# ---------------------------------------------------------------------------


class TestAuthBehavior:
    """Tests for authentication behavior across endpoints."""

    async def test_no_secret_configured_allows_all(self) -> None:
        """When no secret is set, all authenticated endpoints are accessible."""
        ex = _make_mock_executor()
        ex.get_diagnostics = AsyncMock(return_value={})
        client = await _make_client(executor=ex, secret="")
        try:
            assert (await client.get("/status")).status == 200
            assert (await client.get("/update/history")).status == 200
            assert (await client.get("/diagnostics")).status == 200
        finally:
            await client.close()

    async def test_secret_configured_blocks_without_header(self) -> None:
        """When secret is set, requests without the header are rejected."""
        ex = _make_mock_executor()
        client = await _make_client(executor=ex, secret="my-secret")
        try:
            assert (await client.get("/status")).status == 401
            assert (await client.get("/update/history")).status == 401
            assert (await client.get("/diagnostics")).status == 401
            body_apply = {"tag": "v1", "version": "1"}
            assert (await client.post("/update/apply", json=body_apply)).status == 401
            body_rb = {"previous_sha": "abc"}
            assert (await client.post("/update/rollback", json=body_rb)).status == 401
        finally:
            await client.close()

    async def test_health_never_requires_auth(self) -> None:
        """Health endpoint is always accessible regardless of auth config."""
        client = await _make_client(secret="super-secret")
        try:
            resp = await client.get("/health")
            assert resp.status == 200
        finally:
            await client.close()
