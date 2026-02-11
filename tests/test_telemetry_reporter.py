"""Comprehensive unit tests for TelemetryReporter.

All HTTP interactions are mocked via patching httpx.AsyncClient, and
HealthStorage is an AsyncMock.  No network or database access needed.
"""

from __future__ import annotations

# =====================================================================
# Fixtures
# =====================================================================
from datetime import datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from zetherion_ai.health.storage import MetricsSnapshot
from zetherion_ai.telemetry.models import TelemetryConsent, TelemetryReport
from zetherion_ai.telemetry.reporter import TelemetryReporter


def _snap(
    metrics: dict | None = None,
    anomalies: dict | None = None,
) -> MetricsSnapshot:
    """Build a MetricsSnapshot for test mocks."""
    return MetricsSnapshot(
        timestamp=datetime(2026, 2, 11, 12, 0, 0),
        metrics=metrics or {},
        anomalies=anomalies or {},
    )


INSTANCE_ID = "test-instance-001"
CENTRAL_URL = "https://central.example.com"
API_KEY = "zt_inst_test_key_abc123"


@pytest.fixture()
def consent_all() -> TelemetryConsent:
    """Consent with all categories enabled."""
    return TelemetryConsent(categories={"health", "performance", "usage", "cost", "quality"})


@pytest.fixture()
def consent_health_only() -> TelemetryConsent:
    """Consent with only health enabled."""
    return TelemetryConsent(categories={"health"})


@pytest.fixture()
def consent_none() -> TelemetryConsent:
    """Consent with no categories enabled."""
    return TelemetryConsent(categories=set())


@pytest.fixture()
def mock_storage() -> AsyncMock:
    """A mock HealthStorage that returns realistic snapshot data."""
    storage = AsyncMock()
    storage.get_snapshots = AsyncMock(
        return_value=[
            _snap(
                metrics={
                    "system": {"cpu_percent": 42.5, "memory_mb": 1024},
                    "performance": {"latency": {"avg_ms": 150}},
                    "reliability": {"error_rates": {"ollama": 0.02}},
                    "usage": {
                        "messages_total": 500,
                        "intent_distribution": {"general": 300, "code": 200},
                        "active_users_count": 12,
                    },
                    "cost": {"total_usd": 1.50, "by_provider": {"ollama": 0.0, "gemini": 1.50}},
                    "quality": {"avg_confidence": 0.87, "low_confidence_pct": 0.05},
                },
                anomalies={"latency_warning": {"metric": "latency", "severity": "warning"}},
            )
        ]
    )
    return storage


@pytest.fixture()
def reporter_no_storage(consent_all: TelemetryConsent) -> TelemetryReporter:
    """Reporter with all consents but no storage backend."""
    return TelemetryReporter(
        instance_id=INSTANCE_ID,
        central_url=CENTRAL_URL,
        api_key=API_KEY,
        consent=consent_all,
        storage=None,
    )


@pytest.fixture()
def reporter_full(
    consent_all: TelemetryConsent,
    mock_storage: AsyncMock,
) -> TelemetryReporter:
    """Reporter with all consents and a mock storage backend."""
    return TelemetryReporter(
        instance_id=INSTANCE_ID,
        central_url=CENTRAL_URL,
        api_key=API_KEY,
        consent=consent_all,
        storage=mock_storage,
    )


@pytest.fixture()
def reporter_health_only(
    consent_health_only: TelemetryConsent,
    mock_storage: AsyncMock,
) -> TelemetryReporter:
    """Reporter with health-only consent and a mock storage backend."""
    return TelemetryReporter(
        instance_id=INSTANCE_ID,
        central_url=CENTRAL_URL,
        api_key=API_KEY,
        consent=consent_health_only,
        storage=mock_storage,
    )


def _make_report(metrics: dict | None = None) -> TelemetryReport:
    """Helper to create a TelemetryReport for send_report tests."""
    return TelemetryReport(
        instance_id=INSTANCE_ID,
        timestamp="2026-02-11T12:00:00",
        version="0.1.0",
        consent=TelemetryConsent(categories={"health"}),
        metrics=metrics or {},
    )


# =====================================================================
# generate_report
# =====================================================================


class TestGenerateReport:
    """Tests for TelemetryReporter.generate_report."""

    async def test_no_storage_returns_empty_metrics(
        self, reporter_no_storage: TelemetryReporter
    ) -> None:
        """With no storage, metrics dict should be empty."""
        report = await reporter_no_storage.generate_report()

        assert report.instance_id == INSTANCE_ID
        assert report.metrics == {}
        assert isinstance(report, TelemetryReport)

    async def test_health_consent_collects_health(
        self, reporter_health_only: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        """With health consent only, only health metrics are collected."""
        report = await reporter_health_only.generate_report()

        assert "health" in report.metrics
        assert "performance" not in report.metrics
        assert "usage" not in report.metrics
        assert "cost" not in report.metrics
        assert "quality" not in report.metrics

    async def test_all_consents_collects_all_categories(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        """With all consents, all metric categories should be present."""
        report = await reporter_full.generate_report()

        assert "health" in report.metrics
        assert "performance" in report.metrics
        assert "usage" in report.metrics
        assert "cost" in report.metrics
        assert "quality" in report.metrics

    async def test_respects_consent_only_opted_in(self, mock_storage: AsyncMock) -> None:
        """Only opted-in categories should appear in the report."""
        consent = TelemetryConsent(categories={"usage", "cost"})
        reporter = TelemetryReporter(
            instance_id=INSTANCE_ID,
            central_url=CENTRAL_URL,
            api_key=API_KEY,
            consent=consent,
            storage=mock_storage,
        )
        report = await reporter.generate_report()

        assert "usage" in report.metrics
        assert "cost" in report.metrics
        assert "health" not in report.metrics
        assert "performance" not in report.metrics
        assert "quality" not in report.metrics

    async def test_report_has_correct_instance_id(self, reporter_full: TelemetryReporter) -> None:
        """Report should carry the configured instance_id."""
        report = await reporter_full.generate_report()
        assert report.instance_id == INSTANCE_ID

    async def test_report_has_version(self, reporter_full: TelemetryReporter) -> None:
        """Report should carry the package version."""
        report = await reporter_full.generate_report()
        assert report.version  # non-empty

    async def test_report_has_timestamp(self, reporter_full: TelemetryReporter) -> None:
        """Report should carry an ISO timestamp."""
        report = await reporter_full.generate_report()
        assert report.timestamp  # non-empty ISO string

    async def test_no_consent_no_storage_empty_metrics(
        self, consent_none: TelemetryConsent
    ) -> None:
        """With no consent and no storage, metrics should be empty."""
        reporter = TelemetryReporter(
            instance_id=INSTANCE_ID,
            central_url=CENTRAL_URL,
            api_key=API_KEY,
            consent=consent_none,
            storage=None,
        )
        report = await reporter.generate_report()
        assert report.metrics == {}

    async def test_consent_but_no_storage_empty_metrics(
        self, consent_all: TelemetryConsent
    ) -> None:
        """With all consent but no storage, metrics should be empty."""
        reporter = TelemetryReporter(
            instance_id=INSTANCE_ID,
            central_url=CENTRAL_URL,
            api_key=API_KEY,
            consent=consent_all,
            storage=None,
        )
        report = await reporter.generate_report()
        assert report.metrics == {}


# =====================================================================
# send_report
# =====================================================================


class TestSendReport:
    """Tests for TelemetryReporter.send_report."""

    async def test_send_success_returns_true(self, reporter_full: TelemetryReporter) -> None:
        """A 200 response from the central instance should return True."""
        report = _make_report({"health": {"system": {}}})
        mock_response = httpx.Response(200, json={"status": "ok"})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await reporter_full.send_report(report)

        assert result is True
        mock_client.post.assert_awaited_once()

        # Verify correct URL and headers
        call_kwargs = mock_client.post.call_args
        assert "/api/v1/telemetry/ingest" in call_kwargs.args[0]
        assert call_kwargs.kwargs["headers"]["X-Instance-Key"] == API_KEY

    async def test_send_rejected_401_returns_false(self, reporter_full: TelemetryReporter) -> None:
        """A 401 response should return False."""
        report = _make_report()
        mock_response = httpx.Response(401, json={"error": "unauthorized"})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await reporter_full.send_report(report)

        assert result is False

    async def test_send_rejected_500_returns_false(self, reporter_full: TelemetryReporter) -> None:
        """A 500 server error should return False."""
        report = _make_report()
        mock_response = httpx.Response(500, json={"error": "internal"})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await reporter_full.send_report(report)

        assert result is False

    async def test_send_network_error_returns_false(self, reporter_full: TelemetryReporter) -> None:
        """A network error (ConnectionError) should return False."""
        report = _make_report()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await reporter_full.send_report(report)

        assert result is False

    async def test_send_timeout_returns_false(self, reporter_full: TelemetryReporter) -> None:
        """A timeout should return False."""
        report = _make_report()

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(side_effect=httpx.ReadTimeout("read timed out"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await reporter_full.send_report(report)

        assert result is False

    async def test_send_posts_correct_url(self, reporter_full: TelemetryReporter) -> None:
        """The POST should target {central_url}/api/v1/telemetry/ingest."""
        report = _make_report()
        mock_response = httpx.Response(200, json={})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            await reporter_full.send_report(report)

        url = mock_client.post.call_args.args[0]
        assert url == f"{CENTRAL_URL}/api/v1/telemetry/ingest"

    async def test_send_strips_trailing_slash_from_url(self) -> None:
        """A trailing slash on central_url should be stripped."""
        reporter = TelemetryReporter(
            instance_id=INSTANCE_ID,
            central_url="https://central.example.com/",
            api_key=API_KEY,
            consent=TelemetryConsent(),
        )
        report = _make_report()
        mock_response = httpx.Response(200, json={})

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            await reporter.send_report(report)

        url = mock_client.post.call_args.args[0]
        assert not url.startswith("https://central.example.com//")
        assert url == "https://central.example.com/api/v1/telemetry/ingest"


# =====================================================================
# request_deletion
# =====================================================================


class TestRequestDeletion:
    """Tests for TelemetryReporter.request_deletion."""

    async def test_deletion_success_returns_true(self, reporter_full: TelemetryReporter) -> None:
        """A 200 response should return True."""
        mock_response = httpx.Response(200, json={"deleted": True})

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await reporter_full.request_deletion()

        assert result is True
        mock_client.delete.assert_awaited_once()

    async def test_deletion_sends_correct_url(self, reporter_full: TelemetryReporter) -> None:
        """DELETE should target /api/v1/telemetry/instances/{instance_id}."""
        mock_response = httpx.Response(200, json={})

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            await reporter_full.request_deletion()

        url = mock_client.delete.call_args.args[0]
        assert url == f"{CENTRAL_URL}/api/v1/telemetry/instances/{INSTANCE_ID}"

    async def test_deletion_sends_api_key_header(self, reporter_full: TelemetryReporter) -> None:
        """DELETE request should include the X-Instance-Key header."""
        mock_response = httpx.Response(200, json={})

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            await reporter_full.request_deletion()

        headers = mock_client.delete.call_args.kwargs["headers"]
        assert headers["X-Instance-Key"] == API_KEY

    async def test_deletion_failure_returns_false(self, reporter_full: TelemetryReporter) -> None:
        """A 404 response should return False."""
        mock_response = httpx.Response(404, json={"error": "not found"})

        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(return_value=mock_response)
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await reporter_full.request_deletion()

        assert result is False

    async def test_deletion_network_error_returns_false(
        self, reporter_full: TelemetryReporter
    ) -> None:
        """A network error during deletion should return False."""
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=httpx.ConnectError("connection refused"))
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "zetherion_ai.telemetry.reporter.httpx.AsyncClient",
            return_value=mock_client,
        ):
            result = await reporter_full.request_deletion()

        assert result is False


# =====================================================================
# _collect_health_metrics
# =====================================================================


class TestCollectHealthMetrics:
    """Tests for the _collect_health_metrics private method."""

    async def test_no_storage_returns_empty(self, reporter_no_storage: TelemetryReporter) -> None:
        """With no storage, should return empty dict."""
        result = await reporter_no_storage._collect_health_metrics()
        assert result == {}

    async def test_no_snapshots_returns_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        """With empty snapshot list, should return empty dict."""
        mock_storage.get_snapshots = AsyncMock(return_value=[])
        result = await reporter_full._collect_health_metrics()
        assert result == {}

    async def test_snapshot_extracts_system_and_anomaly_count(
        self, reporter_full: TelemetryReporter
    ) -> None:
        """Should extract system metrics and count anomalies."""
        result = await reporter_full._collect_health_metrics()

        assert "system" in result
        assert result["system"]["cpu_percent"] == 42.5
        assert result["system"]["memory_mb"] == 1024
        assert result["anomaly_count"] == 1

    async def test_snapshot_with_no_anomalies(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        """Anomaly count should be 0 when no anomalies exist."""
        mock_storage.get_snapshots = AsyncMock(
            return_value=[_snap(metrics={"system": {"cpu": 10}})]
        )
        result = await reporter_full._collect_health_metrics()
        assert result["anomaly_count"] == 0

    async def test_snapshot_missing_anomalies_key(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        """Missing anomalies key should default to 0 count."""
        mock_storage.get_snapshots = AsyncMock(
            return_value=[_snap(metrics={"system": {"cpu": 10}})]
        )
        result = await reporter_full._collect_health_metrics()
        assert result["anomaly_count"] == 0

    async def test_storage_exception_returns_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        """Storage exceptions should be caught, returning empty dict."""
        mock_storage.get_snapshots = AsyncMock(side_effect=RuntimeError("db error"))
        result = await reporter_full._collect_health_metrics()
        assert result == {}


# =====================================================================
# _collect_performance_metrics
# =====================================================================


class TestCollectPerformanceMetrics:
    """Tests for the _collect_performance_metrics private method."""

    async def test_no_storage_returns_empty(self, reporter_no_storage: TelemetryReporter) -> None:
        result = await reporter_no_storage._collect_performance_metrics()
        assert result == {}

    async def test_no_snapshots_returns_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        mock_storage.get_snapshots = AsyncMock(return_value=[])
        result = await reporter_full._collect_performance_metrics()
        assert result == {}

    async def test_extracts_latency_and_error_rates(self, reporter_full: TelemetryReporter) -> None:
        """Should extract latency and error rate data from the snapshot."""
        result = await reporter_full._collect_performance_metrics()

        assert "latency" in result
        assert result["latency"]["avg_ms"] == 150
        assert "error_rates" in result
        assert result["error_rates"]["ollama"] == 0.02

    async def test_missing_performance_key(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        """Missing performance sub-key should default to empty dict."""
        mock_storage.get_snapshots = AsyncMock(
            return_value=[_snap(metrics={"reliability": {"error_rates": {"x": 0.01}}})]
        )
        result = await reporter_full._collect_performance_metrics()
        assert result["latency"] == {}

    async def test_storage_exception_returns_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        mock_storage.get_snapshots = AsyncMock(side_effect=RuntimeError("db error"))
        result = await reporter_full._collect_performance_metrics()
        assert result == {}


# =====================================================================
# _collect_usage_metrics
# =====================================================================


class TestCollectUsageMetrics:
    """Tests for the _collect_usage_metrics private method."""

    async def test_no_storage_returns_empty(self, reporter_no_storage: TelemetryReporter) -> None:
        result = await reporter_no_storage._collect_usage_metrics()
        assert result == {}

    async def test_no_snapshots_returns_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        mock_storage.get_snapshots = AsyncMock(return_value=[])
        result = await reporter_full._collect_usage_metrics()
        assert result == {}

    async def test_extracts_usage_data(self, reporter_full: TelemetryReporter) -> None:
        """Should extract message counts, intent distribution, and active users."""
        result = await reporter_full._collect_usage_metrics()

        assert result["messages_total"] == 500
        assert result["intent_distribution"] == {"general": 300, "code": 200}
        assert result["active_users_count"] == 12

    async def test_missing_usage_defaults_to_zero(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        """Missing usage sub-keys should default to 0 or empty dict."""
        mock_storage.get_snapshots = AsyncMock(return_value=[_snap()])
        result = await reporter_full._collect_usage_metrics()
        assert result["messages_total"] == 0
        assert result["intent_distribution"] == {}
        assert result["active_users_count"] == 0

    async def test_storage_exception_returns_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        mock_storage.get_snapshots = AsyncMock(side_effect=RuntimeError("db error"))
        result = await reporter_full._collect_usage_metrics()
        assert result == {}


# =====================================================================
# _collect_cost_metrics
# =====================================================================


class TestCollectCostMetrics:
    """Tests for the _collect_cost_metrics private method."""

    async def test_no_storage_returns_empty(self, reporter_no_storage: TelemetryReporter) -> None:
        result = await reporter_no_storage._collect_cost_metrics()
        assert result == {}

    async def test_no_snapshots_returns_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        mock_storage.get_snapshots = AsyncMock(return_value=[])
        result = await reporter_full._collect_cost_metrics()
        assert result == {}

    async def test_extracts_cost_data(self, reporter_full: TelemetryReporter) -> None:
        """Should extract the cost metrics from the snapshot."""
        result = await reporter_full._collect_cost_metrics()
        assert result["total_usd"] == 1.50
        assert result["by_provider"]["gemini"] == 1.50

    async def test_missing_cost_defaults_to_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        mock_storage.get_snapshots = AsyncMock(return_value=[_snap()])
        result = await reporter_full._collect_cost_metrics()
        assert result == {}

    async def test_storage_exception_returns_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        mock_storage.get_snapshots = AsyncMock(side_effect=RuntimeError("db error"))
        result = await reporter_full._collect_cost_metrics()
        assert result == {}


# =====================================================================
# _collect_quality_metrics
# =====================================================================


class TestCollectQualityMetrics:
    """Tests for the _collect_quality_metrics private method."""

    async def test_no_storage_returns_empty(self, reporter_no_storage: TelemetryReporter) -> None:
        result = await reporter_no_storage._collect_quality_metrics()
        assert result == {}

    async def test_no_snapshots_returns_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        mock_storage.get_snapshots = AsyncMock(return_value=[])
        result = await reporter_full._collect_quality_metrics()
        assert result == {}

    async def test_extracts_quality_data(self, reporter_full: TelemetryReporter) -> None:
        """Should extract quality metrics from the snapshot."""
        result = await reporter_full._collect_quality_metrics()
        assert result["avg_confidence"] == 0.87
        assert result["low_confidence_pct"] == 0.05

    async def test_missing_quality_defaults_to_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        mock_storage.get_snapshots = AsyncMock(return_value=[_snap()])
        result = await reporter_full._collect_quality_metrics()
        assert result == {}

    async def test_storage_exception_returns_empty(
        self, reporter_full: TelemetryReporter, mock_storage: AsyncMock
    ) -> None:
        mock_storage.get_snapshots = AsyncMock(side_effect=RuntimeError("db error"))
        result = await reporter_full._collect_quality_metrics()
        assert result == {}


# =====================================================================
# Initialization edge cases
# =====================================================================


class TestReporterInit:
    """Tests for TelemetryReporter initialization."""

    def test_trailing_slash_stripped(self) -> None:
        """Trailing slash should be stripped from central_url."""
        reporter = TelemetryReporter(
            instance_id="id",
            central_url="https://example.com/",
            api_key="key",
            consent=TelemetryConsent(),
        )
        assert reporter._central_url == "https://example.com"

    def test_no_trailing_slash_unchanged(self) -> None:
        """URL without trailing slash should remain unchanged."""
        reporter = TelemetryReporter(
            instance_id="id",
            central_url="https://example.com",
            api_key="key",
            consent=TelemetryConsent(),
        )
        assert reporter._central_url == "https://example.com"

    def test_storage_defaults_to_none(self) -> None:
        """Storage parameter should default to None."""
        reporter = TelemetryReporter(
            instance_id="id",
            central_url="https://example.com",
            api_key="key",
            consent=TelemetryConsent(),
        )
        assert reporter._storage is None
