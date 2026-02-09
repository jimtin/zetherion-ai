"""Unit tests for calendar conflict detection and resolution.

Tests the Conflict dataclass, ConflictDetector methods including
overlap detection, severity scoring, resolution suggestions, and
free slot finding.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from zetherion_ai.skills.gmail.calendar_sync import CalendarEvent
from zetherion_ai.skills.gmail.conflicts import Conflict, ConflictDetector

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BASE_DATE = datetime(2025, 1, 15)


def make_event(
    summary: str = "Test",
    start_hour: int = 9,
    start_minute: int = 0,
    end_hour: int = 10,
    end_minute: int = 0,
    all_day: bool = False,
) -> CalendarEvent:
    """Create a CalendarEvent for testing with sensible defaults."""
    return CalendarEvent(
        event_id=f"evt_{summary.lower().replace(' ', '_')}",
        summary=summary,
        start=BASE_DATE.replace(hour=start_hour, minute=start_minute) if not all_day else None,
        end=BASE_DATE.replace(hour=end_hour, minute=end_minute) if not all_day else None,
        all_day=all_day,
    )


# ---------------------------------------------------------------------------
# 1. Conflict dataclass
# ---------------------------------------------------------------------------


class TestConflictDataclass:
    """Tests for the Conflict dataclass."""

    def test_default_values(self) -> None:
        """Default overlap_minutes, severity, and suggestion should be zero/empty."""
        event_a = make_event("A")
        event_b = make_event("B")
        conflict = Conflict(event_a=event_a, event_b=event_b)

        assert conflict.overlap_minutes == 0
        assert conflict.severity == 0.0
        assert conflict.suggestion == ""

    def test_to_dict_all_fields_present(self) -> None:
        """to_dict should include all expected keys."""
        event_a = make_event("A", start_hour=9, end_hour=10)
        event_b = make_event("B", start_hour=9, end_hour=11)
        conflict = Conflict(
            event_a=event_a,
            event_b=event_b,
            overlap_minutes=60,
            severity=0.75,
            suggestion="Reschedule one",
        )
        result = conflict.to_dict()

        assert set(result.keys()) == {
            "event_a",
            "event_b",
            "overlap_minutes",
            "severity",
            "suggestion",
        }
        assert result["overlap_minutes"] == 60
        assert result["severity"] == 0.75
        assert result["suggestion"] == "Reschedule one"

    def test_to_dict_event_serialization(self) -> None:
        """to_dict should serialize embedded events via their own to_dict."""
        event_a = make_event("Meeting A")
        event_b = make_event("Meeting B")
        conflict = Conflict(event_a=event_a, event_b=event_b)
        result = conflict.to_dict()

        assert result["event_a"]["summary"] == "Meeting A"
        assert result["event_b"]["summary"] == "Meeting B"
        assert "event_id" in result["event_a"]
        assert "event_id" in result["event_b"]


# ---------------------------------------------------------------------------
# 2. ConflictDetector.detect_conflicts
# ---------------------------------------------------------------------------


class TestDetectConflicts:
    """Tests for detecting all pairwise conflicts."""

    def setup_method(self) -> None:
        self.detector = ConflictDetector()

    def test_empty_list_returns_no_conflicts(self) -> None:
        """An empty event list should produce no conflicts."""
        assert self.detector.detect_conflicts([]) == []

    def test_single_event_returns_no_conflicts(self) -> None:
        """A single event cannot conflict with anything."""
        events = [make_event("Solo")]
        assert self.detector.detect_conflicts(events) == []

    def test_no_overlapping_events(self) -> None:
        """Non-overlapping events should produce no conflicts."""
        events = [
            make_event("Morning", start_hour=9, end_hour=10),
            make_event("Afternoon", start_hour=14, end_hour=15),
        ]
        assert self.detector.detect_conflicts(events) == []

    def test_two_overlapping_events(self) -> None:
        """Two overlapping events should produce one conflict."""
        events = [
            make_event("A", start_hour=9, end_hour=10),
            make_event("B", start_hour=9, start_minute=30, end_hour=10, end_minute=30),
        ]
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 1
        assert conflicts[0].overlap_minutes == 30

    def test_three_events_with_partial_overlaps(self) -> None:
        """Three overlapping events should produce the correct number of conflicts."""
        events = [
            make_event("A", start_hour=9, end_hour=10),
            make_event("B", start_hour=9, start_minute=30, end_hour=10, end_minute=30),
            make_event("C", start_hour=10, start_minute=0, end_hour=11),
        ]
        # A overlaps B (9:30-10:00), B overlaps C (10:00-10:30), A does not overlap C
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 2

    def test_all_day_events_are_filtered_out(self) -> None:
        """All-day events should not participate in conflict detection."""
        events = [
            make_event("AllDay", all_day=True),
            make_event("Timed", start_hour=9, end_hour=10),
        ]
        assert self.detector.detect_conflicts(events) == []

    def test_events_missing_start_are_filtered(self) -> None:
        """Events without start times should be filtered out."""
        event_no_start = CalendarEvent(
            event_id="no_start",
            summary="No Start",
            start=None,
            end=BASE_DATE.replace(hour=10),
        )
        events = [
            event_no_start,
            make_event("Normal", start_hour=9, end_hour=10),
        ]
        assert self.detector.detect_conflicts(events) == []

    def test_events_missing_end_are_filtered(self) -> None:
        """Events without end times should be filtered out."""
        event_no_end = CalendarEvent(
            event_id="no_end",
            summary="No End",
            start=BASE_DATE.replace(hour=9),
            end=None,
        )
        events = [
            event_no_end,
            make_event("Normal", start_hour=9, end_hour=10),
        ]
        assert self.detector.detect_conflicts(events) == []

    def test_events_sorted_before_comparison(self) -> None:
        """Events should be sorted by start time, not input order."""
        # Provide events in reverse order; the detector should still detect the conflict.
        events = [
            make_event("Later", start_hour=9, start_minute=30, end_hour=10, end_minute=30),
            make_event("Earlier", start_hour=9, end_hour=10),
        ]
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 1
        # After sorting, event_a should be the earlier event
        assert conflicts[0].event_a.summary == "Earlier"
        assert conflicts[0].event_b.summary == "Later"

    def test_adjacent_events_no_conflict(self) -> None:
        """Events that are exactly back-to-back should not conflict."""
        events = [
            make_event("First", start_hour=9, end_hour=10),
            make_event("Second", start_hour=10, end_hour=11),
        ]
        assert self.detector.detect_conflicts(events) == []


# ---------------------------------------------------------------------------
# 3. ConflictDetector.detect_conflicts_for_new_event
# ---------------------------------------------------------------------------


class TestDetectConflictsForNewEvent:
    """Tests for checking a new event against existing events."""

    def setup_method(self) -> None:
        self.detector = ConflictDetector()

    def test_new_event_conflicts_with_one_existing(self) -> None:
        """New event overlapping one existing event should produce one conflict."""
        new_event = make_event("New", start_hour=9, end_hour=10)
        existing = [
            make_event("Existing", start_hour=9, start_minute=30, end_hour=10, end_minute=30),
        ]
        conflicts = self.detector.detect_conflicts_for_new_event(new_event, existing)

        assert len(conflicts) == 1
        assert conflicts[0].overlap_minutes == 30

    def test_new_event_conflicts_with_multiple_existing(self) -> None:
        """New event overlapping multiple existing events."""
        new_event = make_event("New", start_hour=9, end_hour=12)
        existing = [
            make_event("E1", start_hour=9, end_hour=10),
            make_event("E2", start_hour=11, end_hour=12),
        ]
        conflicts = self.detector.detect_conflicts_for_new_event(new_event, existing)

        assert len(conflicts) == 2

    def test_new_event_no_conflicts(self) -> None:
        """New event with no overlap should return empty list."""
        new_event = make_event("New", start_hour=14, end_hour=15)
        existing = [
            make_event("Morning", start_hour=9, end_hour=10),
        ]
        assert self.detector.detect_conflicts_for_new_event(new_event, existing) == []

    def test_new_event_missing_start_returns_empty(self) -> None:
        """New event without start should return empty list."""
        new_event = CalendarEvent(
            event_id="no_start",
            summary="No Start",
            start=None,
            end=BASE_DATE.replace(hour=10),
        )
        existing = [make_event("Existing", start_hour=9, end_hour=10)]
        assert self.detector.detect_conflicts_for_new_event(new_event, existing) == []

    def test_new_event_missing_end_returns_empty(self) -> None:
        """New event without end should return empty list."""
        new_event = CalendarEvent(
            event_id="no_end",
            summary="No End",
            start=BASE_DATE.replace(hour=9),
            end=None,
        )
        existing = [make_event("Existing", start_hour=9, end_hour=10)]
        assert self.detector.detect_conflicts_for_new_event(new_event, existing) == []

    def test_existing_all_day_event_skipped(self) -> None:
        """All-day existing events should be skipped."""
        new_event = make_event("New", start_hour=9, end_hour=10)
        existing = [make_event("AllDay", all_day=True)]
        assert self.detector.detect_conflicts_for_new_event(new_event, existing) == []

    def test_existing_event_missing_start_skipped(self) -> None:
        """Existing events without start should be skipped."""
        new_event = make_event("New", start_hour=9, end_hour=10)
        existing = [
            CalendarEvent(
                event_id="no_start",
                summary="No Start",
                start=None,
                end=BASE_DATE.replace(hour=10),
            ),
        ]
        assert self.detector.detect_conflicts_for_new_event(new_event, existing) == []

    def test_existing_event_missing_end_skipped(self) -> None:
        """Existing events without end should be skipped."""
        new_event = make_event("New", start_hour=9, end_hour=10)
        existing = [
            CalendarEvent(
                event_id="no_end",
                summary="No End",
                start=BASE_DATE.replace(hour=9),
                end=None,
            ),
        ]
        assert self.detector.detect_conflicts_for_new_event(new_event, existing) == []


# ---------------------------------------------------------------------------
# 4. ConflictDetector.find_free_slots
# ---------------------------------------------------------------------------


class TestFindFreeSlots:
    """Tests for finding free time slots."""

    def setup_method(self) -> None:
        self.detector = ConflictDetector()

    def test_no_events_full_day_free(self) -> None:
        """No events means the entire work day is free."""
        slots = self.detector.find_free_slots([])

        assert slots == [(540, 1020)]  # 9*60=540, 17*60=1020

    def test_one_event_in_middle_two_free_slots(self) -> None:
        """One event in the middle should produce two free slots."""
        events = [make_event("Mid", start_hour=12, end_hour=13)]
        slots = self.detector.find_free_slots(events)

        # Free: 9:00-12:00 (540-720) and 13:00-17:00 (780-1020)
        assert (540, 720) in slots
        assert (780, 1020) in slots
        assert len(slots) == 2

    def test_event_at_start_of_day(self) -> None:
        """Event at the start of the day should leave free slot at end."""
        events = [make_event("Early", start_hour=9, end_hour=10)]
        slots = self.detector.find_free_slots(events)

        # Free: 10:00-17:00 (600-1020)
        assert (600, 1020) in slots

    def test_event_at_end_of_day(self) -> None:
        """Event at the end of the day should leave free slot at start."""
        events = [make_event("Late", start_hour=16, end_hour=17)]
        slots = self.detector.find_free_slots(events)

        # Free: 9:00-16:00 (540-960)
        assert (540, 960) in slots

    def test_overlapping_events_merged(self) -> None:
        """Overlapping busy periods should be merged correctly."""
        events = [
            make_event("A", start_hour=10, end_hour=12),
            make_event("B", start_hour=11, end_hour=13),
        ]
        slots = self.detector.find_free_slots(events)

        # Merged busy: 10:00-13:00. Free: 9:00-10:00 (540-600), 13:00-17:00 (780-1020)
        assert (540, 600) in slots
        assert (780, 1020) in slots
        assert len(slots) == 2

    def test_all_day_events_ignored(self) -> None:
        """All-day events should not affect free slot calculation."""
        events = [make_event("AllDay", all_day=True)]
        slots = self.detector.find_free_slots(events)

        assert slots == [(540, 1020)]

    def test_events_outside_work_hours(self) -> None:
        """Events entirely outside work hours should leave full day free."""
        events = [
            make_event("EarlyBird", start_hour=6, end_hour=8),
            make_event("NightOwl", start_hour=18, end_hour=20),
        ]
        slots = self.detector.find_free_slots(events)

        assert slots == [(540, 1020)]

    def test_custom_day_start_and_end(self) -> None:
        """Custom day_start_hour and day_end_hour should be respected."""
        events = [make_event("Mid", start_hour=12, end_hour=13)]
        slots = self.detector.find_free_slots(events, day_start_hour=8, day_end_hour=18)

        # 8*60=480, 18*60=1080
        # Free: 8:00-12:00 (480-720) and 13:00-18:00 (780-1080)
        assert (480, 720) in slots
        assert (780, 1080) in slots

    def test_custom_min_duration_filters_small_gaps(self) -> None:
        """Gaps smaller than min_duration_minutes should be excluded."""
        events = [
            make_event("A", start_hour=9, end_hour=10),
            # 10-minute gap here
            make_event("B", start_hour=10, start_minute=10, end_hour=11),
        ]
        # Default 30-min minimum should exclude the 10-minute gap
        slots = self.detector.find_free_slots(events)

        # Only free slot should be 11:00-17:00
        assert len(slots) == 1
        assert slots[0] == (660, 1020)

    def test_back_to_back_events_no_gap(self) -> None:
        """Back-to-back events should not produce a free slot between them."""
        events = [
            make_event("A", start_hour=9, end_hour=10),
            make_event("B", start_hour=10, end_hour=11),
        ]
        slots = self.detector.find_free_slots(events)

        # Free: 11:00-17:00 only
        assert len(slots) == 1
        assert slots[0] == (660, 1020)

    def test_events_missing_start_or_end_skipped(self) -> None:
        """Events with missing start or end should be ignored in free slot calc."""
        broken_event = CalendarEvent(event_id="broken", summary="Broken", start=None, end=None)
        slots = self.detector.find_free_slots([broken_event])

        assert slots == [(540, 1020)]

    def test_event_covering_entire_day(self) -> None:
        """An event covering the entire work day should leave no free slots."""
        events = [make_event("AllDay", start_hour=9, end_hour=17)]
        slots = self.detector.find_free_slots(events)

        assert slots == []

    def test_min_duration_exactly_at_boundary(self) -> None:
        """A gap exactly equal to min_duration_minutes should be included."""
        events = [
            make_event("A", start_hour=9, end_hour=10),
            make_event("B", start_hour=10, start_minute=30, end_hour=11),
        ]
        # Gap is exactly 30 minutes (10:00-10:30), default min_duration is 30
        slots = self.detector.find_free_slots(events)

        # Should include the 30-minute gap and the afternoon slot
        gap_found = any(s == (600, 630) for s in slots)
        assert gap_found


# ---------------------------------------------------------------------------
# 5. ConflictDetector._check_overlap (via public methods)
# ---------------------------------------------------------------------------


class TestCheckOverlap:
    """Tests for overlap detection logic (tested via detect_conflicts)."""

    def setup_method(self) -> None:
        self.detector = ConflictDetector()

    def test_no_overlap_a_ends_before_b_starts(self) -> None:
        """A ends before B starts: no overlap."""
        events = [
            make_event("A", start_hour=9, end_hour=10),
            make_event("B", start_hour=11, end_hour=12),
        ]
        assert self.detector.detect_conflicts(events) == []

    def test_no_overlap_b_ends_before_a_starts(self) -> None:
        """B ends before A starts: no overlap."""
        events = [
            make_event("A", start_hour=11, end_hour=12),
            make_event("B", start_hour=9, end_hour=10),
        ]
        assert self.detector.detect_conflicts(events) == []

    def test_full_overlap_same_times_severity_1(self) -> None:
        """Events with identical start/end should have severity 1.0."""
        events = [
            make_event("A", start_hour=9, end_hour=10),
            make_event("B", start_hour=9, end_hour=10),
        ]
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 1
        assert conflicts[0].severity == 1.0
        assert conflicts[0].overlap_minutes == 60

    def test_partial_overlap_correct_minutes_and_severity(self) -> None:
        """Partial overlap should compute correct minutes and severity."""
        # A: 9:00-10:00 (60 min), B: 9:30-11:00 (90 min)
        # Overlap: 9:30-10:00 = 30 min
        # max_duration = max(60, 90) = 90
        # severity = 30 / 90 = 0.333...
        events = [
            make_event("A", start_hour=9, end_hour=10),
            make_event("B", start_hour=9, start_minute=30, end_hour=11),
        ]
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 1
        assert conflicts[0].overlap_minutes == 30
        assert conflicts[0].severity == pytest.approx(30 / 90, abs=0.01)

    def test_event_a_missing_start_via_new_event(self) -> None:
        """_check_overlap returns None when event_a has no start."""
        new_event = CalendarEvent(
            event_id="no_start", summary="NoStart", start=None, end=BASE_DATE.replace(hour=10)
        )
        existing = [make_event("Existing", start_hour=9, end_hour=10)]
        conflicts = self.detector.detect_conflicts_for_new_event(new_event, existing)

        assert conflicts == []

    def test_event_b_missing_end_via_new_event(self) -> None:
        """_check_overlap returns None when event_b has no end."""
        new_event = make_event("New", start_hour=9, end_hour=10)
        existing = [
            CalendarEvent(
                event_id="no_end",
                summary="NoEnd",
                start=BASE_DATE.replace(hour=9),
                end=None,
            )
        ]
        conflicts = self.detector.detect_conflicts_for_new_event(new_event, existing)

        assert conflicts == []


# ---------------------------------------------------------------------------
# 6. ConflictDetector._suggest_resolution (via public methods)
# ---------------------------------------------------------------------------


class TestSuggestResolution:
    """Tests for resolution suggestions at different severity levels."""

    def setup_method(self) -> None:
        self.detector = ConflictDetector()

    def test_major_conflict_message_at_severity_gte_08(self) -> None:
        """Severity >= 0.8 should produce 'Major conflict' suggestion."""
        # A: 9:00-10:00 (60 min), B: 9:00-10:00 (60 min)
        # Overlap 60 min, severity = 1.0
        events = [
            make_event("Meeting A", start_hour=9, end_hour=10),
            make_event("Meeting B", start_hour=9, end_hour=10),
        ]
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 1
        assert "Major conflict" in conflicts[0].suggestion
        assert "Meeting A" in conflicts[0].suggestion
        assert "Meeting B" in conflicts[0].suggestion

    def test_partial_overlap_message_at_severity_04_to_08(self) -> None:
        """Severity >= 0.4 and < 0.8 should produce 'Partial overlap' suggestion."""
        # A: 9:00-10:00 (60 min), B: 9:30-11:00 (90 min)
        # Overlap: 30 min, max_duration: 90, severity: 0.333 -- too low
        # Need: severity in [0.4, 0.8)
        # A: 9:00-10:00 (60 min), B: 9:30-10:30 (60 min)
        # Overlap: 30 min, max_duration: 60, severity: 0.5
        events = [
            make_event("Call A", start_hour=9, end_hour=10),
            make_event("Call B", start_hour=9, start_minute=30, end_hour=10, end_minute=30),
        ]
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 1
        assert 0.4 <= conflicts[0].severity < 0.8
        assert "Partial overlap" in conflicts[0].suggestion
        assert "Call A" in conflicts[0].suggestion
        assert "Call B" in conflicts[0].suggestion

    def test_minor_overlap_message_at_severity_lt_04(self) -> None:
        """Severity < 0.4 should produce 'Minor overlap' suggestion."""
        # A: 9:00-10:00 (60 min), B: 9:50-12:00 (130 min)
        # Overlap: 10 min, max_duration: 130, severity: 10/130 ~ 0.077
        events = [
            make_event("Standup", start_hour=9, end_hour=10),
            make_event("Workshop", start_hour=9, start_minute=50, end_hour=12),
        ]
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 1
        assert conflicts[0].severity < 0.4
        assert "Minor overlap" in conflicts[0].suggestion
        assert "Standup" in conflicts[0].suggestion
        assert "Workshop" in conflicts[0].suggestion

    def test_event_names_appear_in_all_suggestions(self) -> None:
        """Event summaries should always appear in the suggestion text."""
        events = [
            make_event("Alpha", start_hour=9, end_hour=10),
            make_event("Beta", start_hour=9, end_hour=10),
        ]
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 1
        assert "Alpha" in conflicts[0].suggestion
        assert "Beta" in conflicts[0].suggestion

    def test_severity_exactly_08_is_major(self) -> None:
        """Severity exactly 0.8 should trigger 'Major conflict' message."""
        # A: 9:00-10:00 (60 min), B: 9:12-10:12 (60 min)
        # Overlap: 48 min, max_duration: 60, severity: 48/60 = 0.8
        events = [
            make_event("Evt A", start_hour=9, end_hour=10),
            make_event("Evt B", start_hour=9, start_minute=12, end_hour=10, end_minute=12),
        ]
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 1
        assert conflicts[0].severity == pytest.approx(0.8, abs=0.01)
        assert "Major conflict" in conflicts[0].suggestion

    def test_severity_exactly_04_is_partial(self) -> None:
        """Severity exactly 0.4 should trigger 'Partial overlap' message."""
        # A: 9:00-10:00 (60 min), B: 9:36-11:00 (84 min)
        # Overlap: 24 min, max_duration: 84, severity: 24/84 ~ 0.2857 -- too low
        # Need overlap/max_duration = 0.4
        # A: 9:00-10:00 (60 min), B: 9:36-10:36 (60 min)
        # Overlap: 24 min, max_duration: 60, severity: 24/60 = 0.4
        events = [
            make_event("Evt X", start_hour=9, end_hour=10),
            make_event("Evt Y", start_hour=9, start_minute=36, end_hour=10, end_minute=36),
        ]
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 1
        assert conflicts[0].severity == pytest.approx(0.4, abs=0.01)
        assert "Partial overlap" in conflicts[0].suggestion


# ---------------------------------------------------------------------------
# 7. Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    """Additional edge case tests for thorough coverage."""

    def setup_method(self) -> None:
        self.detector = ConflictDetector()

    def test_many_events_all_overlapping(self) -> None:
        """Four fully overlapping events should produce C(4,2) = 6 conflicts."""
        events = [
            make_event("A", start_hour=9, end_hour=10),
            make_event("B", start_hour=9, end_hour=10),
            make_event("C", start_hour=9, end_hour=10),
            make_event("D", start_hour=9, end_hour=10),
        ]
        conflicts = self.detector.detect_conflicts(events)

        assert len(conflicts) == 6

    def test_detect_conflicts_for_new_event_empty_existing(self) -> None:
        """New event against empty existing list should produce no conflicts."""
        new_event = make_event("New", start_hour=9, end_hour=10)
        assert self.detector.detect_conflicts_for_new_event(new_event, []) == []

    def test_find_free_slots_event_with_zero_duration_skipped(self) -> None:
        """Events where end == start (zero duration) should be skipped."""
        event = CalendarEvent(
            event_id="zero",
            summary="Zero",
            start=BASE_DATE.replace(hour=10),
            end=BASE_DATE.replace(hour=10),
        )
        slots = self.detector.find_free_slots([event])

        # Zero-duration event filtered by end_mins > start_mins check
        assert slots == [(540, 1020)]

    def test_find_free_slots_multiple_adjacent_busy_periods(self) -> None:
        """Multiple adjacent busy periods should merge into one."""
        events = [
            make_event("A", start_hour=10, end_hour=11),
            make_event("B", start_hour=11, end_hour=12),
            make_event("C", start_hour=12, end_hour=13),
        ]
        slots = self.detector.find_free_slots(events)

        # Busy: 10:00-13:00 merged. Free: 9:00-10:00, 13:00-17:00
        assert (540, 600) in slots
        assert (780, 1020) in slots
        assert len(slots) == 2

    def test_check_overlap_event_a_missing_end_returns_none(self) -> None:
        """_check_overlap returns None when event_a has start but no end."""
        event_a = CalendarEvent(
            event_id="a", summary="A", start=BASE_DATE.replace(hour=9), end=None
        )
        event_b = make_event("B", start_hour=9, end_hour=10)
        result = self.detector._check_overlap(event_a, event_b)

        assert result is None

    def test_check_overlap_event_b_missing_start_returns_none(self) -> None:
        """_check_overlap returns None when event_b has end but no start."""
        event_a = make_event("A", start_hour=9, end_hour=10)
        event_b = CalendarEvent(
            event_id="b", summary="B", start=None, end=BASE_DATE.replace(hour=10)
        )
        result = self.detector._check_overlap(event_a, event_b)

        assert result is None

    def test_check_overlap_both_missing_start_returns_none(self) -> None:
        """_check_overlap returns None when both events have missing times."""
        event_a = CalendarEvent(event_id="a", summary="A", start=None, end=None)
        event_b = CalendarEvent(event_id="b", summary="B", start=None, end=None)
        result = self.detector._check_overlap(event_a, event_b)

        assert result is None
