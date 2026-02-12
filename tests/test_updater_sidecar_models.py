"""Tests for updater_sidecar.models — dataclasses and serialization."""

from __future__ import annotations

from datetime import datetime

import pytest

from updater_sidecar.models import (
    HistoryEntry,
    RollbackRequest,
    SidecarStatus,
    UpdateRequest,
    UpdateResult,
)

# ---------------------------------------------------------------------------
# TestUpdateRequest
# ---------------------------------------------------------------------------


class TestUpdateRequest:
    """Tests for UpdateRequest dataclass."""

    def test_from_dict_valid(self) -> None:
        req = UpdateRequest.from_dict({"tag": "v1.0.0", "version": "1.0.0"})
        assert req.tag == "v1.0.0"
        assert req.version == "1.0.0"

    def test_from_dict_missing_tag(self) -> None:
        with pytest.raises(ValueError, match="tag"):
            UpdateRequest.from_dict({"version": "1.0.0"})

    def test_from_dict_empty_tag(self) -> None:
        with pytest.raises(ValueError, match="tag"):
            UpdateRequest.from_dict({"tag": "", "version": "1.0.0"})

    def test_from_dict_missing_version(self) -> None:
        with pytest.raises(ValueError, match="version"):
            UpdateRequest.from_dict({"tag": "v1.0.0"})

    def test_from_dict_empty_version(self) -> None:
        with pytest.raises(ValueError, match="version"):
            UpdateRequest.from_dict({"tag": "v1.0.0", "version": ""})

    def test_from_dict_both_missing(self) -> None:
        with pytest.raises(ValueError, match="tag"):
            UpdateRequest.from_dict({})

    def test_from_dict_extra_fields_ignored(self) -> None:
        req = UpdateRequest.from_dict({"tag": "v2.0.0", "version": "2.0.0", "extra": "ignored"})
        assert req.tag == "v2.0.0"
        assert req.version == "2.0.0"


# ---------------------------------------------------------------------------
# TestRollbackRequest
# ---------------------------------------------------------------------------


class TestRollbackRequest:
    """Tests for RollbackRequest dataclass."""

    def test_from_dict_valid(self) -> None:
        req = RollbackRequest.from_dict({"previous_sha": "abc123def456"})
        assert req.previous_sha == "abc123def456"

    def test_from_dict_missing_sha(self) -> None:
        with pytest.raises(ValueError, match="previous_sha"):
            RollbackRequest.from_dict({})

    def test_from_dict_empty_sha(self) -> None:
        with pytest.raises(ValueError, match="previous_sha"):
            RollbackRequest.from_dict({"previous_sha": ""})

    def test_from_dict_extra_fields_ignored(self) -> None:
        req = RollbackRequest.from_dict({"previous_sha": "abc123", "extra": "ignored"})
        assert req.previous_sha == "abc123"


# ---------------------------------------------------------------------------
# TestUpdateResult
# ---------------------------------------------------------------------------


class TestUpdateResult:
    """Tests for UpdateResult dataclass."""

    def test_default_values(self) -> None:
        result = UpdateResult(status="failed")
        assert result.status == "failed"
        assert result.previous_sha is None
        assert result.new_sha is None
        assert result.steps_completed == []
        assert result.error is None
        assert result.duration_seconds == 0.0
        assert result.completed_at is None
        # started_at should be a valid ISO timestamp
        datetime.fromisoformat(result.started_at)

    def test_to_dict_full(self) -> None:
        result = UpdateResult(
            status="success",
            previous_sha="abc123",
            new_sha="def456",
            steps_completed=["git_fetch", "git_checkout"],
            error=None,
            duration_seconds=12.5,
            started_at="2026-01-01T00:00:00+00:00",
            completed_at="2026-01-01T00:00:12+00:00",
        )
        d = result.to_dict()
        assert d == {
            "status": "success",
            "previous_sha": "abc123",
            "new_sha": "def456",
            "steps_completed": ["git_fetch", "git_checkout"],
            "error": None,
            "duration_seconds": 12.5,
            "started_at": "2026-01-01T00:00:00+00:00",
            "completed_at": "2026-01-01T00:00:12+00:00",
        }

    def test_to_dict_defaults(self) -> None:
        result = UpdateResult(status="failed")
        d = result.to_dict()
        assert d["status"] == "failed"
        assert d["previous_sha"] is None
        assert d["new_sha"] is None
        assert d["steps_completed"] == []
        assert d["error"] is None
        assert d["duration_seconds"] == 0.0
        assert d["completed_at"] is None
        assert "started_at" in d

    def test_to_dict_with_error(self) -> None:
        result = UpdateResult(status="failed", error="git fetch failed")
        d = result.to_dict()
        assert d["error"] == "git fetch failed"

    def test_steps_completed_is_mutable_list(self) -> None:
        """Each instance should get its own steps list (default_factory)."""
        r1 = UpdateResult(status="failed")
        r2 = UpdateResult(status="failed")
        r1.steps_completed.append("step1")
        assert r2.steps_completed == []


# ---------------------------------------------------------------------------
# TestSidecarStatus
# ---------------------------------------------------------------------------


class TestSidecarStatus:
    """Tests for SidecarStatus dataclass."""

    def test_to_dict_idle(self) -> None:
        status = SidecarStatus(state="idle")
        d = status.to_dict()
        assert d == {
            "state": "idle",
            "current_operation": None,
            "last_result": None,
            "uptime_seconds": 0.0,
        }

    def test_to_dict_with_last_result(self) -> None:
        result = UpdateResult(status="success", previous_sha="abc123")
        status = SidecarStatus(
            state="idle",
            last_result=result,
            uptime_seconds=123.45,
        )
        d = status.to_dict()
        assert d["state"] == "idle"
        assert d["uptime_seconds"] == 123.45
        assert d["last_result"] is not None
        assert d["last_result"]["status"] == "success"
        assert d["last_result"]["previous_sha"] == "abc123"

    def test_to_dict_updating_state(self) -> None:
        status = SidecarStatus(
            state="updating",
            current_operation="Fetching tag v1.0.0",
        )
        d = status.to_dict()
        assert d["state"] == "updating"
        assert d["current_operation"] == "Fetching tag v1.0.0"

    def test_default_values(self) -> None:
        status = SidecarStatus(state="idle")
        assert status.current_operation is None
        assert status.last_result is None
        assert status.uptime_seconds == 0.0


# ---------------------------------------------------------------------------
# TestHistoryEntry
# ---------------------------------------------------------------------------


class TestHistoryEntry:
    """Tests for HistoryEntry dataclass."""

    def test_to_dict(self) -> None:
        result = UpdateResult(status="success")
        entry = HistoryEntry(
            tag="v1.0.0",
            version="1.0.0",
            result=result,
            timestamp="2026-01-01T00:00:00+00:00",
        )
        d = entry.to_dict()
        assert d["tag"] == "v1.0.0"
        assert d["version"] == "1.0.0"
        assert d["timestamp"] == "2026-01-01T00:00:00+00:00"
        assert d["result"]["status"] == "success"

    def test_default_timestamp(self) -> None:
        result = UpdateResult(status="failed")
        entry = HistoryEntry(tag="v2.0.0", version="2.0.0", result=result)
        # timestamp should be a valid ISO timestamp
        datetime.fromisoformat(entry.timestamp)

    def test_to_dict_nests_result(self) -> None:
        result = UpdateResult(
            status="rolled_back",
            error="health check failed",
            steps_completed=["git_fetch", "git_checkout", "docker_build"],
        )
        entry = HistoryEntry(tag="v3.0.0", version="3.0.0", result=result)
        d = entry.to_dict()
        assert d["result"]["status"] == "rolled_back"
        assert d["result"]["error"] == "health check failed"
        assert d["result"]["steps_completed"] == [
            "git_fetch",
            "git_checkout",
            "docker_build",
        ]
