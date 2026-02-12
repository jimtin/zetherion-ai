"""Tests for updater_sidecar.health_checker — health check logic."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx

from updater_sidecar.health_checker import (
    HealthCheckConfig,
    check_all_services,
    check_service_health,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _mock_response(status_code: int = 200) -> MagicMock:
    """Return a mock httpx.Response with the given status code."""
    resp = MagicMock()
    resp.status_code = status_code
    return resp


def _make_fast_config(retries: int = 3) -> HealthCheckConfig:
    """Return a HealthCheckConfig with zero delay for fast tests."""
    return HealthCheckConfig(retries=retries, delay_seconds=0, timeout_seconds=5)


# ---------------------------------------------------------------------------
# TestCheckServiceHealth
# ---------------------------------------------------------------------------


class TestCheckServiceHealth:
    """Tests for check_service_health()."""

    async def test_passes_on_200(self) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await check_service_health(
                "http://localhost:8080/health",
                _make_fast_config(retries=3),
            )

        assert result is True
        # Should succeed on first attempt
        assert mock_client.get.await_count == 1

    async def test_retries_on_non_200(self) -> None:
        """Non-200 response should trigger retries."""
        fail_resp = _mock_response(503)
        ok_resp = _mock_response(200)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[fail_resp, fail_resp, ok_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await check_service_health(
                "http://localhost:8080/health",
                _make_fast_config(retries=5),
            )

        assert result is True
        assert mock_client.get.await_count == 3

    async def test_exhausts_retries_on_persistent_failure(self) -> None:
        """All retries fail, returns False."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(500))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await check_service_health(
                "http://localhost:8080/health",
                _make_fast_config(retries=3),
            )

        assert result is False
        assert mock_client.get.await_count == 3

    async def test_retries_on_request_error(self) -> None:
        """httpx.RequestError should trigger retries."""
        ok_resp = _mock_response(200)

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=[httpx.ConnectError("refused"), ok_resp])
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await check_service_health(
                "http://localhost:8080/health",
                _make_fast_config(retries=3),
            )

        assert result is True
        assert mock_client.get.await_count == 2

    async def test_exhausts_retries_on_persistent_connection_error(self) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await check_service_health(
                "http://localhost:8080/health",
                _make_fast_config(retries=2),
            )

        assert result is False
        assert mock_client.get.await_count == 2

    async def test_uses_default_config_when_none(self) -> None:
        """When config is None, default HealthCheckConfig is used.

        We patch sleep to avoid the 10-second default delay.
        """
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await check_service_health(
                "http://localhost:8080/health",
                config=None,
            )

        assert result is True

    async def test_single_retry_no_sleep_after_last_attempt(self) -> None:
        """With 1 retry, asyncio.sleep should not be called."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(500))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        sleep_path = "updater_sidecar.health_checker.asyncio.sleep"
        with (
            patch("httpx.AsyncClient", return_value=mock_client),
            patch(sleep_path, new_callable=AsyncMock) as mock_sleep,
        ):
            result = await check_service_health(
                "http://localhost:8080/health",
                HealthCheckConfig(retries=1, delay_seconds=10, timeout_seconds=5),
            )

        assert result is False
        mock_sleep.assert_not_awaited()


# ---------------------------------------------------------------------------
# TestCheckAllServices
# ---------------------------------------------------------------------------


class TestCheckAllServices:
    """Tests for check_all_services()."""

    async def test_all_pass(self) -> None:
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(200))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        urls = [
            "http://svc1:8080/health",
            "http://svc2:8443/health",
        ]

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await check_all_services(urls, _make_fast_config(retries=1))

        assert result is True

    async def test_one_fails(self) -> None:
        ok_resp = _mock_response(200)
        fail_resp = _mock_response(500)

        call_count = 0

        async def mock_get(url: str) -> MagicMock:
            nonlocal call_count
            call_count += 1
            # First service passes, second fails
            if "svc1" in url:
                return ok_resp
            return fail_resp

        mock_client = AsyncMock()
        mock_client.get = mock_get
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        urls = [
            "http://svc1:8080/health",
            "http://svc2:8443/health",
        ]

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await check_all_services(urls, _make_fast_config(retries=1))

        assert result is False

    async def test_empty_url_list(self) -> None:
        result = await check_all_services([], _make_fast_config(retries=1))
        assert result is True

    async def test_first_failure_short_circuits(self) -> None:
        """If the first service fails, second is not checked."""
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=_mock_response(500))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        urls = [
            "http://svc1:8080/health",
            "http://svc2:8443/health",
        ]

        with patch("httpx.AsyncClient", return_value=mock_client):
            result = await check_all_services(urls, _make_fast_config(retries=1))

        assert result is False
        # Only the first service should have been checked (1 retry attempt)
        assert mock_client.get.await_count == 1
