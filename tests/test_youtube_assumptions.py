"""Tests for YouTube assumption tracking and validation."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from zetherion_ai.skills.youtube.assumptions import (
    _CONFIRMED_VALIDATION_DAYS,
    _DEFAULT_VALIDATION_DAYS,
    AssumptionTracker,
)
from zetherion_ai.skills.youtube.models import (
    AssumptionCategory,
    AssumptionSource,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_storage() -> AsyncMock:
    """Return an AsyncMock that mimics YouTubeStorage."""
    storage = AsyncMock()
    storage.save_assumption = AsyncMock(return_value={"id": uuid4()})
    storage.get_assumptions = AsyncMock(return_value=[])
    storage.get_assumption = AsyncMock(return_value=None)
    storage.update_assumption = AsyncMock(return_value={"id": uuid4()})
    storage.get_stale_assumptions = AsyncMock(return_value=[])
    return storage


def _make_assumption(
    *,
    source: str = AssumptionSource.CONFIRMED.value,
    category: str = AssumptionCategory.AUDIENCE.value,
    confidence: float = 1.0,
    extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Build a minimal assumption dict."""
    row: dict[str, Any] = {
        "id": uuid4(),
        "channel_id": uuid4(),
        "category": category,
        "statement": "Test statement",
        "evidence": [],
        "confidence": confidence,
        "source": source,
        "confirmed_at": datetime.utcnow().isoformat() if source == "confirmed" else None,
        "next_validation": (datetime.utcnow() + timedelta(days=7)).isoformat(),
    }
    if extra:
        row.update(extra)
    return row


# ---------------------------------------------------------------------------
# Initialization
# ---------------------------------------------------------------------------


class TestAssumptionTrackerInit:
    """Tests for AssumptionTracker construction."""

    def test_init_stores_storage_reference(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)
        assert tracker._storage is storage


# ---------------------------------------------------------------------------
# add_confirmed
# ---------------------------------------------------------------------------


class TestAddConfirmed:
    """Tests for AssumptionTracker.add_confirmed."""

    @pytest.mark.asyncio
    async def test_calls_save_assumption(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)
        channel_id = uuid4()

        await tracker.add_confirmed(
            channel_id=channel_id,
            category="audience",
            statement="Our viewers are aged 18-24",
            evidence=["Survey results"],
        )

        storage.save_assumption.assert_awaited_once()
        payload = storage.save_assumption.call_args[0][0]
        assert payload["channel_id"] == channel_id
        assert payload["category"] == "audience"
        assert payload["statement"] == "Our viewers are aged 18-24"
        assert payload["evidence"] == ["Survey results"]
        assert payload["confidence"] == 1.0
        assert payload["source"] == AssumptionSource.CONFIRMED.value

    @pytest.mark.asyncio
    async def test_confirmed_at_is_set(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)

        before = datetime.utcnow()
        await tracker.add_confirmed(
            channel_id=uuid4(), category="tone", statement="Friendly"
        )
        after = datetime.utcnow()

        payload = storage.save_assumption.call_args[0][0]
        confirmed_at = datetime.fromisoformat(payload["confirmed_at"])
        assert before <= confirmed_at <= after

    @pytest.mark.asyncio
    async def test_next_validation_uses_confirmed_interval(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)

        before = datetime.utcnow()
        await tracker.add_confirmed(
            channel_id=uuid4(), category="tone", statement="Friendly"
        )

        payload = storage.save_assumption.call_args[0][0]
        next_val = datetime.fromisoformat(payload["next_validation"])
        expected_earliest = before + timedelta(days=_CONFIRMED_VALIDATION_DAYS)
        # Allow a small tolerance for execution time
        assert next_val >= expected_earliest - timedelta(seconds=2)

    @pytest.mark.asyncio
    async def test_evidence_defaults_to_empty_list(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)

        await tracker.add_confirmed(
            channel_id=uuid4(), category="content", statement="Tutorials"
        )

        payload = storage.save_assumption.call_args[0][0]
        assert payload["evidence"] == []

    @pytest.mark.asyncio
    async def test_returns_storage_result(self) -> None:
        storage = _make_storage()
        expected = {"id": uuid4(), "category": "audience"}
        storage.save_assumption.return_value = expected
        tracker = AssumptionTracker(storage)

        result = await tracker.add_confirmed(
            channel_id=uuid4(), category="audience", statement="Young"
        )
        assert result is expected


# ---------------------------------------------------------------------------
# add_inferred
# ---------------------------------------------------------------------------


class TestAddInferred:
    """Tests for AssumptionTracker.add_inferred."""

    @pytest.mark.asyncio
    async def test_calls_save_assumption(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)
        channel_id = uuid4()

        await tracker.add_inferred(
            channel_id=channel_id,
            category="topic",
            statement="Cooking content",
            evidence=["High engagement on cooking videos"],
            confidence=0.8,
        )

        storage.save_assumption.assert_awaited_once()
        payload = storage.save_assumption.call_args[0][0]
        assert payload["channel_id"] == channel_id
        assert payload["category"] == "topic"
        assert payload["statement"] == "Cooking content"
        assert payload["evidence"] == ["High engagement on cooking videos"]
        assert payload["confidence"] == 0.8
        assert payload["source"] == AssumptionSource.INFERRED.value

    @pytest.mark.asyncio
    async def test_confirmed_at_is_none(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)

        await tracker.add_inferred(
            channel_id=uuid4(), category="topic", statement="Cooking"
        )

        payload = storage.save_assumption.call_args[0][0]
        assert payload["confirmed_at"] is None

    @pytest.mark.asyncio
    async def test_next_validation_uses_default_interval(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)

        before = datetime.utcnow()
        await tracker.add_inferred(
            channel_id=uuid4(), category="topic", statement="Cooking"
        )

        payload = storage.save_assumption.call_args[0][0]
        next_val = datetime.fromisoformat(payload["next_validation"])
        expected_earliest = before + timedelta(days=_DEFAULT_VALIDATION_DAYS)
        assert next_val >= expected_earliest - timedelta(seconds=2)

    @pytest.mark.asyncio
    async def test_default_confidence_is_half(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)

        await tracker.add_inferred(
            channel_id=uuid4(), category="audience", statement="Young"
        )

        payload = storage.save_assumption.call_args[0][0]
        assert payload["confidence"] == 0.5

    @pytest.mark.asyncio
    async def test_evidence_defaults_to_empty_list(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)

        await tracker.add_inferred(
            channel_id=uuid4(), category="audience", statement="Young"
        )

        payload = storage.save_assumption.call_args[0][0]
        assert payload["evidence"] == []


# ---------------------------------------------------------------------------
# get_all
# ---------------------------------------------------------------------------


class TestGetAll:
    """Tests for AssumptionTracker.get_all."""

    @pytest.mark.asyncio
    async def test_returns_all_active(self) -> None:
        storage = _make_storage()
        active = _make_assumption(source=AssumptionSource.CONFIRMED.value)
        inferred = _make_assumption(source=AssumptionSource.INFERRED.value)
        storage.get_assumptions.return_value = [active, inferred]

        tracker = AssumptionTracker(storage)
        channel_id = uuid4()
        result = await tracker.get_all(channel_id)

        storage.get_assumptions.assert_awaited_once_with(channel_id)
        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_excludes_invalidated_by_default(self) -> None:
        storage = _make_storage()
        active = _make_assumption(source=AssumptionSource.CONFIRMED.value)
        invalidated = _make_assumption(source=AssumptionSource.INVALIDATED.value)
        storage.get_assumptions.return_value = [active, invalidated]

        tracker = AssumptionTracker(storage)
        result = await tracker.get_all(uuid4())

        assert len(result) == 1
        assert result[0]["source"] == AssumptionSource.CONFIRMED.value

    @pytest.mark.asyncio
    async def test_includes_invalidated_when_active_only_false(self) -> None:
        storage = _make_storage()
        active = _make_assumption(source=AssumptionSource.CONFIRMED.value)
        invalidated = _make_assumption(source=AssumptionSource.INVALIDATED.value)
        storage.get_assumptions.return_value = [active, invalidated]

        tracker = AssumptionTracker(storage)
        result = await tracker.get_all(uuid4(), active_only=False)

        assert len(result) == 2

    @pytest.mark.asyncio
    async def test_returns_empty_list_when_none(self) -> None:
        storage = _make_storage()
        storage.get_assumptions.return_value = []
        tracker = AssumptionTracker(storage)

        result = await tracker.get_all(uuid4())
        assert result == []


# ---------------------------------------------------------------------------
# get_confirmed
# ---------------------------------------------------------------------------


class TestGetConfirmed:
    """Tests for AssumptionTracker.get_confirmed."""

    @pytest.mark.asyncio
    async def test_passes_confirmed_source_filter(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)
        channel_id = uuid4()

        await tracker.get_confirmed(channel_id)

        storage.get_assumptions.assert_awaited_once_with(
            channel_id, source=AssumptionSource.CONFIRMED.value
        )

    @pytest.mark.asyncio
    async def test_returns_storage_result(self) -> None:
        storage = _make_storage()
        confirmed = [_make_assumption(source=AssumptionSource.CONFIRMED.value)]
        storage.get_assumptions.return_value = confirmed
        tracker = AssumptionTracker(storage)

        result = await tracker.get_confirmed(uuid4())
        assert result == confirmed


# ---------------------------------------------------------------------------
# get_high_confidence
# ---------------------------------------------------------------------------


class TestGetHighConfidence:
    """Tests for AssumptionTracker.get_high_confidence."""

    @pytest.mark.asyncio
    async def test_includes_confirmed_regardless_of_confidence(self) -> None:
        storage = _make_storage()
        confirmed_low = _make_assumption(
            source=AssumptionSource.CONFIRMED.value, confidence=0.3
        )
        storage.get_assumptions.return_value = [confirmed_low]
        tracker = AssumptionTracker(storage)

        result = await tracker.get_high_confidence(uuid4())
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_includes_inferred_above_threshold(self) -> None:
        storage = _make_storage()
        high = _make_assumption(
            source=AssumptionSource.INFERRED.value, confidence=0.8
        )
        storage.get_assumptions.return_value = [high]
        tracker = AssumptionTracker(storage)

        result = await tracker.get_high_confidence(uuid4())
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_excludes_inferred_below_threshold(self) -> None:
        storage = _make_storage()
        low = _make_assumption(
            source=AssumptionSource.INFERRED.value, confidence=0.5
        )
        storage.get_assumptions.return_value = [low]
        tracker = AssumptionTracker(storage)

        result = await tracker.get_high_confidence(uuid4())
        assert len(result) == 0

    @pytest.mark.asyncio
    async def test_custom_threshold(self) -> None:
        storage = _make_storage()
        mid = _make_assumption(
            source=AssumptionSource.INFERRED.value, confidence=0.5
        )
        storage.get_assumptions.return_value = [mid]
        tracker = AssumptionTracker(storage)

        result = await tracker.get_high_confidence(uuid4(), threshold=0.5)
        assert len(result) == 1

    @pytest.mark.asyncio
    async def test_mixed_sources_filtered_correctly(self) -> None:
        storage = _make_storage()
        confirmed = _make_assumption(
            source=AssumptionSource.CONFIRMED.value, confidence=0.2
        )
        high_inferred = _make_assumption(
            source=AssumptionSource.INFERRED.value, confidence=0.9
        )
        low_inferred = _make_assumption(
            source=AssumptionSource.INFERRED.value, confidence=0.3
        )
        needs_review = _make_assumption(
            source=AssumptionSource.NEEDS_REVIEW.value, confidence=0.8
        )
        storage.get_assumptions.return_value = [
            confirmed, high_inferred, low_inferred, needs_review,
        ]
        tracker = AssumptionTracker(storage)

        result = await tracker.get_high_confidence(uuid4())
        # confirmed (always), high_inferred (0.9>=0.7), needs_review (0.8>=0.7)
        # low_inferred excluded (0.3 < 0.7)
        assert len(result) == 3
        sources = {r["source"] for r in result}
        assert AssumptionSource.CONFIRMED.value in sources
        assert AssumptionSource.INFERRED.value in sources
        assert AssumptionSource.NEEDS_REVIEW.value in sources


# ---------------------------------------------------------------------------
# confirm
# ---------------------------------------------------------------------------


class TestConfirm:
    """Tests for AssumptionTracker.confirm."""

    @pytest.mark.asyncio
    async def test_calls_update_with_correct_fields(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)
        assumption_id = uuid4()

        before = datetime.utcnow()
        await tracker.confirm(assumption_id)
        after = datetime.utcnow()

        storage.update_assumption.assert_awaited_once()
        call_args = storage.update_assumption.call_args
        assert call_args[0][0] == assumption_id
        assert call_args[1]["source"] == AssumptionSource.CONFIRMED.value
        assert call_args[1]["confidence"] == 1.0

        confirmed_at = datetime.fromisoformat(call_args[1]["confirmed_at"])
        assert before <= confirmed_at <= after

    @pytest.mark.asyncio
    async def test_next_validation_uses_confirmed_interval(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)

        before = datetime.utcnow()
        await tracker.confirm(uuid4())

        call_args = storage.update_assumption.call_args
        next_val = datetime.fromisoformat(call_args[1]["next_validation"])
        expected = before + timedelta(days=_CONFIRMED_VALIDATION_DAYS)
        assert next_val >= expected - timedelta(seconds=2)

    @pytest.mark.asyncio
    async def test_returns_storage_result(self) -> None:
        storage = _make_storage()
        expected = {"id": uuid4(), "source": "confirmed"}
        storage.update_assumption.return_value = expected
        tracker = AssumptionTracker(storage)

        result = await tracker.confirm(uuid4())
        assert result is expected


# ---------------------------------------------------------------------------
# invalidate
# ---------------------------------------------------------------------------


class TestInvalidate:
    """Tests for AssumptionTracker.invalidate."""

    @pytest.mark.asyncio
    async def test_invalidate_without_reason(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)
        assumption_id = uuid4()

        await tracker.invalidate(assumption_id)

        storage.update_assumption.assert_awaited_once()
        call_args = storage.update_assumption.call_args
        assert call_args[0][0] == assumption_id
        assert call_args[1]["source"] == AssumptionSource.INVALIDATED.value
        assert call_args[1]["confidence"] == 0.0
        # No evidence key when no reason given
        assert "evidence" not in call_args[1]

    @pytest.mark.asyncio
    async def test_invalidate_with_reason_appends_to_evidence(self) -> None:
        storage = _make_storage()
        existing = _make_assumption(extra={"evidence": ["original evidence"]})
        storage.get_assumption.return_value = existing
        tracker = AssumptionTracker(storage)
        assumption_id = uuid4()

        await tracker.invalidate(assumption_id, reason="Data contradicts this")

        storage.get_assumption.assert_awaited_once_with(assumption_id)
        call_args = storage.update_assumption.call_args
        assert call_args[1]["source"] == AssumptionSource.INVALIDATED.value
        assert call_args[1]["confidence"] == 0.0
        assert call_args[1]["evidence"] == [
            "original evidence",
            "Invalidated: Data contradicts this",
        ]

    @pytest.mark.asyncio
    async def test_invalidate_with_reason_but_no_existing_assumption(self) -> None:
        storage = _make_storage()
        storage.get_assumption.return_value = None
        tracker = AssumptionTracker(storage)
        assumption_id = uuid4()

        await tracker.invalidate(assumption_id, reason="Gone")

        # When the assumption is not found, evidence is not set
        call_args = storage.update_assumption.call_args
        assert "evidence" not in call_args[1]

    @pytest.mark.asyncio
    async def test_invalidate_with_reason_and_no_existing_evidence(self) -> None:
        storage = _make_storage()
        existing = _make_assumption(extra={"evidence": None})
        storage.get_assumption.return_value = existing
        tracker = AssumptionTracker(storage)

        await tracker.invalidate(uuid4(), reason="Wrong")

        call_args = storage.update_assumption.call_args
        assert call_args[1]["evidence"] == ["Invalidated: Wrong"]


# ---------------------------------------------------------------------------
# mark_needs_review
# ---------------------------------------------------------------------------


class TestMarkNeedsReview:
    """Tests for AssumptionTracker.mark_needs_review."""

    @pytest.mark.asyncio
    async def test_calls_update_with_needs_review_source(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)
        assumption_id = uuid4()

        await tracker.mark_needs_review(assumption_id)

        storage.update_assumption.assert_awaited_once_with(
            assumption_id,
            source=AssumptionSource.NEEDS_REVIEW.value,
        )

    @pytest.mark.asyncio
    async def test_returns_storage_result(self) -> None:
        storage = _make_storage()
        expected = {"id": uuid4(), "source": "needs_review"}
        storage.update_assumption.return_value = expected
        tracker = AssumptionTracker(storage)

        result = await tracker.mark_needs_review(uuid4())
        assert result is expected


# ---------------------------------------------------------------------------
# refresh_validation
# ---------------------------------------------------------------------------


class TestRefreshValidation:
    """Tests for AssumptionTracker.refresh_validation."""

    @pytest.mark.asyncio
    async def test_high_confidence_uses_confirmed_interval(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)
        assumption_id = uuid4()

        before = datetime.utcnow()
        await tracker.refresh_validation(assumption_id, new_confidence=0.95)

        call_args = storage.update_assumption.call_args
        assert call_args[0][0] == assumption_id
        assert call_args[1]["confidence"] == 0.95

        next_val = datetime.fromisoformat(call_args[1]["next_validation"])
        expected = before + timedelta(days=_CONFIRMED_VALIDATION_DAYS)
        assert next_val >= expected - timedelta(seconds=2)

    @pytest.mark.asyncio
    async def test_low_confidence_uses_default_interval(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)
        assumption_id = uuid4()

        before = datetime.utcnow()
        await tracker.refresh_validation(assumption_id, new_confidence=0.6)

        call_args = storage.update_assumption.call_args
        assert call_args[1]["confidence"] == 0.6

        next_val = datetime.fromisoformat(call_args[1]["next_validation"])
        expected = before + timedelta(days=_DEFAULT_VALIDATION_DAYS)
        assert next_val >= expected - timedelta(seconds=2)

    @pytest.mark.asyncio
    async def test_boundary_confidence_0_9_uses_confirmed_interval(self) -> None:
        """Confidence of exactly 0.9 should use the confirmed (longer) interval."""
        storage = _make_storage()
        tracker = AssumptionTracker(storage)

        before = datetime.utcnow()
        await tracker.refresh_validation(uuid4(), new_confidence=0.9)

        call_args = storage.update_assumption.call_args
        next_val = datetime.fromisoformat(call_args[1]["next_validation"])
        expected = before + timedelta(days=_CONFIRMED_VALIDATION_DAYS)
        assert next_val >= expected - timedelta(seconds=2)

    @pytest.mark.asyncio
    async def test_boundary_confidence_below_0_9_uses_default_interval(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)

        before = datetime.utcnow()
        await tracker.refresh_validation(uuid4(), new_confidence=0.89)

        call_args = storage.update_assumption.call_args
        next_val = datetime.fromisoformat(call_args[1]["next_validation"])
        expected_max = before + timedelta(days=_DEFAULT_VALIDATION_DAYS) + timedelta(seconds=2)
        assert next_val <= expected_max

    @pytest.mark.asyncio
    async def test_last_validated_is_set(self) -> None:
        storage = _make_storage()
        tracker = AssumptionTracker(storage)

        before = datetime.utcnow()
        await tracker.refresh_validation(uuid4(), new_confidence=0.7)
        after = datetime.utcnow()

        call_args = storage.update_assumption.call_args
        last_validated = datetime.fromisoformat(call_args[1]["last_validated"])
        assert before <= last_validated <= after


# ---------------------------------------------------------------------------
# get_stale
# ---------------------------------------------------------------------------


class TestGetStale:
    """Tests for AssumptionTracker.get_stale."""

    @pytest.mark.asyncio
    async def test_delegates_to_storage(self) -> None:
        storage = _make_storage()
        stale = [_make_assumption(), _make_assumption()]
        storage.get_stale_assumptions.return_value = stale
        tracker = AssumptionTracker(storage)

        result = await tracker.get_stale()

        storage.get_stale_assumptions.assert_awaited_once()
        assert result == stale

    @pytest.mark.asyncio
    async def test_returns_empty_when_none_stale(self) -> None:
        storage = _make_storage()
        storage.get_stale_assumptions.return_value = []
        tracker = AssumptionTracker(storage)

        result = await tracker.get_stale()
        assert result == []


# ---------------------------------------------------------------------------
# has_category
# ---------------------------------------------------------------------------


class TestHasCategory:
    """Tests for AssumptionTracker.has_category."""

    @pytest.mark.asyncio
    async def test_returns_true_when_category_exists(self) -> None:
        storage = _make_storage()
        storage.get_assumptions.return_value = [
            _make_assumption(category=AssumptionCategory.AUDIENCE.value),
        ]
        tracker = AssumptionTracker(storage)

        result = await tracker.has_category(uuid4(), AssumptionCategory.AUDIENCE.value)
        assert result is True

    @pytest.mark.asyncio
    async def test_returns_false_when_category_missing(self) -> None:
        storage = _make_storage()
        storage.get_assumptions.return_value = [
            _make_assumption(category=AssumptionCategory.AUDIENCE.value),
        ]
        tracker = AssumptionTracker(storage)

        result = await tracker.has_category(uuid4(), AssumptionCategory.TONE.value)
        assert result is False

    @pytest.mark.asyncio
    async def test_returns_false_when_no_confirmed(self) -> None:
        storage = _make_storage()
        storage.get_assumptions.return_value = []
        tracker = AssumptionTracker(storage)

        result = await tracker.has_category(uuid4(), AssumptionCategory.AUDIENCE.value)
        assert result is False


# ---------------------------------------------------------------------------
# get_missing_categories
# ---------------------------------------------------------------------------


class TestGetMissingCategories:
    """Tests for AssumptionTracker.get_missing_categories."""

    @pytest.mark.asyncio
    async def test_returns_all_non_performance_when_nothing_confirmed(self) -> None:
        storage = _make_storage()
        storage.get_assumptions.return_value = []
        tracker = AssumptionTracker(storage)

        missing = await tracker.get_missing_categories(uuid4())

        # All categories except PERFORMANCE should be returned
        expected = sorted(
            c.value
            for c in AssumptionCategory
            if c != AssumptionCategory.PERFORMANCE
        )
        assert missing == expected

    @pytest.mark.asyncio
    async def test_excludes_confirmed_categories(self) -> None:
        storage = _make_storage()
        storage.get_assumptions.return_value = [
            _make_assumption(category=AssumptionCategory.AUDIENCE.value),
            _make_assumption(category=AssumptionCategory.TONE.value),
        ]
        tracker = AssumptionTracker(storage)

        missing = await tracker.get_missing_categories(uuid4())

        assert AssumptionCategory.AUDIENCE.value not in missing
        assert AssumptionCategory.TONE.value not in missing
        # Remaining categories should be present
        assert AssumptionCategory.CONTENT.value in missing
        assert AssumptionCategory.SCHEDULE.value in missing
        assert AssumptionCategory.TOPIC.value in missing
        assert AssumptionCategory.COMPETITOR.value in missing

    @pytest.mark.asyncio
    async def test_performance_never_required(self) -> None:
        storage = _make_storage()
        storage.get_assumptions.return_value = []
        tracker = AssumptionTracker(storage)

        missing = await tracker.get_missing_categories(uuid4())

        assert AssumptionCategory.PERFORMANCE.value not in missing

    @pytest.mark.asyncio
    async def test_returns_empty_when_all_covered(self) -> None:
        storage = _make_storage()
        required = [
            c for c in AssumptionCategory if c != AssumptionCategory.PERFORMANCE
        ]
        storage.get_assumptions.return_value = [
            _make_assumption(category=c.value) for c in required
        ]
        tracker = AssumptionTracker(storage)

        missing = await tracker.get_missing_categories(uuid4())
        assert missing == []

    @pytest.mark.asyncio
    async def test_result_is_sorted(self) -> None:
        storage = _make_storage()
        storage.get_assumptions.return_value = []
        tracker = AssumptionTracker(storage)

        missing = await tracker.get_missing_categories(uuid4())
        assert missing == sorted(missing)
