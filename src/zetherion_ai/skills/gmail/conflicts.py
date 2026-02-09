"""Calendar conflict detection and resolution.

Detects overlapping events, scores conflict severity,
and suggests resolution strategies.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.gmail.calendar_sync import CalendarEvent

log = get_logger("zetherion_ai.skills.gmail.conflicts")


@dataclass
class Conflict:
    """Represents a conflict between two calendar events."""

    event_a: CalendarEvent
    event_b: CalendarEvent
    overlap_minutes: int = 0
    severity: float = 0.0  # 0.0 (minor) to 1.0 (full overlap)
    suggestion: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_a": self.event_a.to_dict(),
            "event_b": self.event_b.to_dict(),
            "overlap_minutes": self.overlap_minutes,
            "severity": self.severity,
            "suggestion": self.suggestion,
        }


class ConflictDetector:
    """Detects scheduling conflicts between calendar events.

    Supports:
    - Overlap detection between any pair of events
    - Severity scoring based on overlap proportion
    - Basic resolution suggestions
    """

    def detect_conflicts(self, events: list[CalendarEvent]) -> list[Conflict]:
        """Detect all pairwise conflicts in a list of events.

        Args:
            events: List of calendar events to check.

        Returns:
            List of detected Conflicts.
        """
        conflicts: list[Conflict] = []

        # Filter to events with valid start/end times
        timed = [e for e in events if e.start and e.end and not e.all_day]

        # Sort by start time
        timed.sort(key=lambda e: e.start)  # type: ignore[arg-type, return-value]

        for i in range(len(timed)):
            for j in range(i + 1, len(timed)):
                conflict = self._check_overlap(timed[i], timed[j])
                if conflict:
                    conflicts.append(conflict)

        log.debug(
            "conflicts_detected",
            event_count=len(events),
            conflict_count=len(conflicts),
        )
        return conflicts

    def detect_conflicts_for_new_event(
        self,
        new_event: CalendarEvent,
        existing_events: list[CalendarEvent],
    ) -> list[Conflict]:
        """Check if a new event conflicts with existing events.

        Args:
            new_event: The proposed new event.
            existing_events: List of existing events.

        Returns:
            List of conflicts with the new event.
        """
        conflicts: list[Conflict] = []

        if not new_event.start or not new_event.end:
            return conflicts

        for existing in existing_events:
            if existing.all_day or not existing.start or not existing.end:
                continue
            conflict = self._check_overlap(new_event, existing)
            if conflict:
                conflicts.append(conflict)

        return conflicts

    def find_free_slots(
        self,
        events: list[CalendarEvent],
        *,
        day_start_hour: int = 9,
        day_end_hour: int = 17,
        min_duration_minutes: int = 30,
    ) -> list[tuple[int, int]]:
        """Find free time slots in a day's events.

        Args:
            events: Events for the day.
            day_start_hour: Start of workday (hour).
            day_end_hour: End of workday (hour).
            min_duration_minutes: Minimum free slot duration.

        Returns:
            List of (start_hour_minutes, end_hour_minutes) tuples
            where hour_minutes is hours*60 + minutes.
        """
        # Convert events to minute intervals
        busy: list[tuple[int, int]] = []
        for event in events:
            if event.all_day or not event.start or not event.end:
                continue
            start_mins = event.start.hour * 60 + event.start.minute
            end_mins = event.end.hour * 60 + event.end.minute
            if end_mins > start_mins:
                busy.append((start_mins, end_mins))

        # Sort and merge overlapping busy periods
        busy.sort()
        merged: list[tuple[int, int]] = []
        for start, end in busy:
            if merged and start <= merged[-1][1]:
                merged[-1] = (merged[-1][0], max(merged[-1][1], end))
            else:
                merged.append((start, end))

        # Find gaps
        day_start = day_start_hour * 60
        day_end = day_end_hour * 60
        free: list[tuple[int, int]] = []

        current = day_start
        for start, end in merged:
            if start > current and start - current >= min_duration_minutes:
                free.append((current, min(start, day_end)))
            current = max(current, end)

        # Check for free time after last event
        if current < day_end and day_end - current >= min_duration_minutes:
            free.append((current, day_end))

        return free

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _check_overlap(self, event_a: CalendarEvent, event_b: CalendarEvent) -> Conflict | None:
        """Check if two events overlap.

        Returns:
            Conflict if overlap detected, None otherwise.
        """
        if not event_a.start or not event_a.end:
            return None
        if not event_b.start or not event_b.end:
            return None

        # No overlap if one ends before the other starts
        if event_a.end <= event_b.start or event_b.end <= event_a.start:
            return None

        # Calculate overlap
        overlap_start = max(event_a.start, event_b.start)
        overlap_end = min(event_a.end, event_b.end)
        overlap_minutes = int((overlap_end - overlap_start).total_seconds() / 60)

        if overlap_minutes <= 0:
            return None

        # Calculate severity
        duration_a = event_a.duration_minutes or 1
        duration_b = event_b.duration_minutes or 1
        max_duration = max(duration_a, duration_b)
        severity = min(1.0, overlap_minutes / max_duration)

        # Generate suggestion
        suggestion = self._suggest_resolution(event_a, event_b, severity)

        return Conflict(
            event_a=event_a,
            event_b=event_b,
            overlap_minutes=overlap_minutes,
            severity=severity,
            suggestion=suggestion,
        )

    def _suggest_resolution(
        self,
        event_a: CalendarEvent,
        event_b: CalendarEvent,
        severity: float,
    ) -> str:
        """Generate a resolution suggestion based on severity."""
        if severity >= 0.8:
            return (
                f"Major conflict: '{event_a.summary}' and"
                f" '{event_b.summary}' overlap significantly."
                " Consider rescheduling one."
            )

        if severity >= 0.4:
            return (
                f"Partial overlap between '{event_a.summary}' and"
                f" '{event_b.summary}'."
                " You may need to leave one early or join the other late."
            )

        return (
            f"Minor overlap between '{event_a.summary}' and"
            f" '{event_b.summary}'."
            " Likely manageable with slight adjustment."
        )
