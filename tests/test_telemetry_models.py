"""Tests for telemetry data models."""

from __future__ import annotations

import uuid
from datetime import datetime

from zetherion_ai.telemetry.models import (
    VALID_CATEGORIES,
    InstanceRegistration,
    TelemetryConsent,
    TelemetryReport,
    generate_instance_id,
)


class TestTelemetryConsent:
    """Tests for TelemetryConsent dataclass."""

    def test_default_empty_categories(self) -> None:
        """Test that a new TelemetryConsent has no categories by default."""
        consent = TelemetryConsent()
        assert consent.categories == set()

    def test_allows_opted_in_category(self) -> None:
        """Test that allows() returns True for an opted-in category."""
        consent = TelemetryConsent(categories={"performance", "usage"})
        assert consent.allows("performance") is True
        assert consent.allows("usage") is True

    def test_allows_non_opted_category(self) -> None:
        """Test that allows() returns False for a category not opted in."""
        consent = TelemetryConsent(categories={"performance"})
        assert consent.allows("cost") is False
        assert consent.allows("health") is False

    def test_allows_empty_categories(self) -> None:
        """Test that allows() returns False when categories are empty."""
        consent = TelemetryConsent()
        assert consent.allows("performance") is False

    def test_to_dict_produces_sorted_categories(self) -> None:
        """Test that to_dict() returns categories in sorted order."""
        consent = TelemetryConsent(categories={"usage", "cost", "performance"})
        result = consent.to_dict()
        assert result == {"categories": ["cost", "performance", "usage"]}

    def test_to_dict_empty_categories(self) -> None:
        """Test that to_dict() returns an empty list when no categories."""
        consent = TelemetryConsent()
        result = consent.to_dict()
        assert result == {"categories": []}

    def test_from_dict_filters_invalid_categories(self) -> None:
        """Test that from_dict() removes categories not in VALID_CATEGORIES."""
        data = {"categories": ["performance", "invalid_cat", "usage", "bogus"]}
        consent = TelemetryConsent.from_dict(data)
        assert consent.categories == {"performance", "usage"}

    def test_from_dict_with_empty_data(self) -> None:
        """Test that from_dict() with empty dict produces empty categories."""
        consent = TelemetryConsent.from_dict({})
        assert consent.categories == set()

    def test_from_dict_with_no_categories_key(self) -> None:
        """Test that from_dict() with missing 'categories' key defaults to empty."""
        consent = TelemetryConsent.from_dict({"other_key": "value"})
        assert consent.categories == set()

    def test_from_dict_all_valid_categories(self) -> None:
        """Test that from_dict() keeps all valid categories."""
        data = {"categories": list(VALID_CATEGORIES)}
        consent = TelemetryConsent.from_dict(data)
        assert consent.categories == VALID_CATEGORIES

    def test_round_trip_to_dict_from_dict(self) -> None:
        """Test that to_dict -> from_dict produces equivalent consent."""
        original = TelemetryConsent(categories={"performance", "cost", "health"})
        serialized = original.to_dict()
        restored = TelemetryConsent.from_dict(serialized)
        assert restored.categories == original.categories

    def test_round_trip_empty_consent(self) -> None:
        """Test round-trip serialization with empty consent."""
        original = TelemetryConsent()
        serialized = original.to_dict()
        restored = TelemetryConsent.from_dict(serialized)
        assert restored.categories == original.categories


class TestTelemetryReport:
    """Tests for TelemetryReport dataclass."""

    def test_creation_with_all_fields(self) -> None:
        """Test creating a TelemetryReport with all fields specified."""
        consent = TelemetryConsent(categories={"performance"})
        metrics = {"latency": {"p50": 120, "p99": 500}}
        report = TelemetryReport(
            instance_id="inst-001",
            timestamp="2025-01-15T10:30:00Z",
            version="1.2.3",
            consent=consent,
            metrics=metrics,
        )
        assert report.instance_id == "inst-001"
        assert report.timestamp == "2025-01-15T10:30:00Z"
        assert report.version == "1.2.3"
        assert report.consent is consent
        assert report.metrics == metrics

    def test_metrics_defaults_to_empty_dict(self) -> None:
        """Test that metrics defaults to an empty dict when not provided."""
        consent = TelemetryConsent()
        report = TelemetryReport(
            instance_id="inst-002",
            timestamp="2025-01-15T10:30:00Z",
            version="1.0.0",
            consent=consent,
        )
        assert report.metrics == {}

    def test_to_dict_structure(self) -> None:
        """Test that to_dict() produces the expected dictionary structure."""
        consent = TelemetryConsent(categories={"usage", "cost"})
        metrics = {"requests": {"total": 42}}
        report = TelemetryReport(
            instance_id="inst-003",
            timestamp="2025-06-01T00:00:00Z",
            version="2.0.0",
            consent=consent,
            metrics=metrics,
        )
        result = report.to_dict()
        assert result == {
            "instance_id": "inst-003",
            "timestamp": "2025-06-01T00:00:00Z",
            "version": "2.0.0",
            "consent": {"categories": ["cost", "usage"]},
            "metrics": {"requests": {"total": 42}},
        }

    def test_to_dict_empty_metrics(self) -> None:
        """Test to_dict() with default empty metrics."""
        consent = TelemetryConsent()
        report = TelemetryReport(
            instance_id="inst-004",
            timestamp="2025-01-01T00:00:00Z",
            version="0.1.0",
            consent=consent,
        )
        result = report.to_dict()
        assert result["metrics"] == {}

    def test_from_dict_round_trip(self) -> None:
        """Test that to_dict -> from_dict produces equivalent report."""
        consent = TelemetryConsent(categories={"performance", "quality"})
        metrics = {"errors": {"count": 3, "rate": 0.01}}
        original = TelemetryReport(
            instance_id="inst-005",
            timestamp="2025-03-20T12:00:00Z",
            version="3.1.4",
            consent=consent,
            metrics=metrics,
        )
        serialized = original.to_dict()
        restored = TelemetryReport.from_dict(serialized)

        assert restored.instance_id == original.instance_id
        assert restored.timestamp == original.timestamp
        assert restored.version == original.version
        assert restored.consent.categories == original.consent.categories
        assert restored.metrics == original.metrics

    def test_from_dict_missing_optional_metrics(self) -> None:
        """Test from_dict() when 'metrics' key is absent."""
        data = {
            "instance_id": "inst-006",
            "timestamp": "2025-01-01T00:00:00Z",
            "version": "1.0.0",
        }
        report = TelemetryReport.from_dict(data)
        assert report.metrics == {}

    def test_from_dict_missing_optional_consent(self) -> None:
        """Test from_dict() when 'consent' key is absent."""
        data = {
            "instance_id": "inst-007",
            "timestamp": "2025-01-01T00:00:00Z",
            "version": "1.0.0",
        }
        report = TelemetryReport.from_dict(data)
        assert report.consent.categories == set()

    def test_from_dict_missing_both_optionals(self) -> None:
        """Test from_dict() when both 'metrics' and 'consent' keys are absent."""
        data = {
            "instance_id": "inst-008",
            "timestamp": "2025-02-01T00:00:00Z",
            "version": "0.0.1",
        }
        report = TelemetryReport.from_dict(data)
        assert report.consent.categories == set()
        assert report.metrics == {}


class TestInstanceRegistration:
    """Tests for InstanceRegistration dataclass."""

    def test_creation_with_required_fields(self) -> None:
        """Test creating InstanceRegistration with only required fields."""
        reg = InstanceRegistration(
            instance_id="inst-100",
            api_key_hash="$2b$12$somehash",
        )
        assert reg.instance_id == "inst-100"
        assert reg.api_key_hash == "$2b$12$somehash"
        assert isinstance(reg.first_seen, datetime)
        assert isinstance(reg.last_seen, datetime)
        assert reg.current_version == ""
        assert reg.consent.categories == set()

    def test_default_empty_consent(self) -> None:
        """Test that default consent is an empty TelemetryConsent."""
        reg = InstanceRegistration(
            instance_id="inst-101",
            api_key_hash="hash",
        )
        assert isinstance(reg.consent, TelemetryConsent)
        assert reg.consent.categories == set()
        assert reg.consent.allows("performance") is False

    def test_default_datetimes_are_recent(self) -> None:
        """Test that first_seen and last_seen default to approximately now."""
        before = datetime.now()
        reg = InstanceRegistration(
            instance_id="inst-102",
            api_key_hash="hash",
        )
        after = datetime.now()
        assert before <= reg.first_seen <= after
        assert before <= reg.last_seen <= after

    def test_to_dict_includes_iso_format_datetimes(self) -> None:
        """Test that to_dict() serializes datetimes as ISO-8601 strings."""
        fixed_time = datetime(2025, 6, 15, 10, 30, 0)
        consent = TelemetryConsent(categories={"health"})
        reg = InstanceRegistration(
            instance_id="inst-103",
            api_key_hash="$2b$12$hashvalue",
            first_seen=fixed_time,
            last_seen=fixed_time,
            current_version="2.0.0",
            consent=consent,
        )
        result = reg.to_dict()
        assert result == {
            "instance_id": "inst-103",
            "api_key_hash": "$2b$12$hashvalue",
            "first_seen": "2025-06-15T10:30:00",
            "last_seen": "2025-06-15T10:30:00",
            "current_version": "2.0.0",
            "consent": {"categories": ["health"]},
        }

    def test_to_dict_datetime_format_is_isoformat(self) -> None:
        """Test that datetime values can be parsed back via fromisoformat."""
        reg = InstanceRegistration(
            instance_id="inst-104",
            api_key_hash="hash",
        )
        result = reg.to_dict()
        # Verify the datetime strings are valid ISO format
        parsed_first = datetime.fromisoformat(result["first_seen"])
        parsed_last = datetime.fromisoformat(result["last_seen"])
        assert isinstance(parsed_first, datetime)
        assert isinstance(parsed_last, datetime)

    def test_from_dict_round_trip(self) -> None:
        """Test that to_dict -> from_dict produces equivalent registration."""
        fixed_time = datetime(2025, 3, 10, 8, 0, 0)
        consent = TelemetryConsent(categories={"performance", "usage"})
        original = InstanceRegistration(
            instance_id="inst-105",
            api_key_hash="$2b$12$abcdef",
            first_seen=fixed_time,
            last_seen=fixed_time,
            current_version="1.5.0",
            consent=consent,
        )
        serialized = original.to_dict()
        restored = InstanceRegistration.from_dict(serialized)

        assert restored.instance_id == original.instance_id
        assert restored.api_key_hash == original.api_key_hash
        assert restored.first_seen == original.first_seen
        assert restored.last_seen == original.last_seen
        assert restored.current_version == original.current_version
        assert restored.consent.categories == original.consent.categories

    def test_from_dict_without_first_seen(self) -> None:
        """Test from_dict() when 'first_seen' is absent defaults to now."""
        before = datetime.now()
        data = {
            "instance_id": "inst-106",
            "api_key_hash": "hash",
            "last_seen": "2025-01-01T00:00:00",
        }
        reg = InstanceRegistration.from_dict(data)
        after = datetime.now()
        assert before <= reg.first_seen <= after
        assert reg.last_seen == datetime(2025, 1, 1, 0, 0, 0)

    def test_from_dict_without_last_seen(self) -> None:
        """Test from_dict() when 'last_seen' is absent defaults to now."""
        before = datetime.now()
        data = {
            "instance_id": "inst-107",
            "api_key_hash": "hash",
            "first_seen": "2025-01-01T00:00:00",
        }
        reg = InstanceRegistration.from_dict(data)
        after = datetime.now()
        assert reg.first_seen == datetime(2025, 1, 1, 0, 0, 0)
        assert before <= reg.last_seen <= after

    def test_from_dict_without_consent(self) -> None:
        """Test from_dict() when 'consent' key is absent."""
        data = {
            "instance_id": "inst-108",
            "api_key_hash": "hash",
            "first_seen": "2025-01-01T00:00:00",
            "last_seen": "2025-01-01T00:00:00",
        }
        reg = InstanceRegistration.from_dict(data)
        assert reg.consent.categories == set()

    def test_from_dict_without_api_key_hash(self) -> None:
        """Test from_dict() when 'api_key_hash' is absent defaults to empty string."""
        data = {
            "instance_id": "inst-109",
            "first_seen": "2025-01-01T00:00:00",
            "last_seen": "2025-01-01T00:00:00",
        }
        reg = InstanceRegistration.from_dict(data)
        assert reg.api_key_hash == ""

    def test_from_dict_without_current_version(self) -> None:
        """Test from_dict() when 'current_version' is absent defaults to empty string."""
        data = {
            "instance_id": "inst-110",
            "api_key_hash": "hash",
            "first_seen": "2025-01-01T00:00:00",
            "last_seen": "2025-01-01T00:00:00",
        }
        reg = InstanceRegistration.from_dict(data)
        assert reg.current_version == ""

    def test_from_dict_minimal_data(self) -> None:
        """Test from_dict() with only the required instance_id field."""
        before = datetime.now()
        data = {"instance_id": "inst-111"}
        reg = InstanceRegistration.from_dict(data)
        after = datetime.now()
        assert reg.instance_id == "inst-111"
        assert reg.api_key_hash == ""
        assert before <= reg.first_seen <= after
        assert before <= reg.last_seen <= after
        assert reg.current_version == ""
        assert reg.consent.categories == set()


class TestGenerateInstanceId:
    """Tests for generate_instance_id utility function."""

    def test_returns_valid_uuid4_string(self) -> None:
        """Test that generate_instance_id returns a valid UUID4 string."""
        instance_id = generate_instance_id()
        # uuid.UUID will raise ValueError if the string is not a valid UUID
        parsed = uuid.UUID(instance_id)
        assert parsed.version == 4
        assert str(parsed) == instance_id

    def test_returns_string_type(self) -> None:
        """Test that the return type is str."""
        instance_id = generate_instance_id()
        assert isinstance(instance_id, str)

    def test_two_calls_produce_different_ids(self) -> None:
        """Test that successive calls produce unique identifiers."""
        id1 = generate_instance_id()
        id2 = generate_instance_id()
        assert id1 != id2

    def test_multiple_calls_all_unique(self) -> None:
        """Test that many generated IDs are all unique."""
        ids = {generate_instance_id() for _ in range(100)}
        assert len(ids) == 100
