"""Dev agent source adapter for the observation pipeline.

Converts dev-agent webhook events (commits, annotations, sessions, tags)
into ObservationEvent format for the observation pipeline.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import uuid4

from zetherion_ai.logging import get_logger
from zetherion_ai.observation.models import ObservationEvent

log = get_logger("zetherion_ai.observation.adapters.dev")


class DevObservationAdapter:
    """Converts dev-agent webhook events into ObservationEvents.

    Each webhook embed is converted into a single ObservationEvent with:
    - source="dev_agent"
    - source_id=commit SHA or generated UUID
    - content=embed description + field values
    - context includes event_type, project, and all embed fields
    """

    def __init__(self, owner_user_id: int) -> None:
        self._owner_user_id = owner_user_id

    def adapt(
        self,
        event_type: str,
        fields: dict[str, Any],
        content: str,
        *,
        timestamp: datetime | None = None,
    ) -> ObservationEvent:
        """Convert a dev-agent event to an ObservationEvent.

        Args:
            event_type: Type of event (commit, annotation, session, tag).
            fields: Structured fields from the webhook embed.
            content: Human-readable description of the event.
            timestamp: When the event occurred (defaults to now).

        Returns:
            An ObservationEvent for the pipeline.
        """
        source_id = fields.get("sha", str(uuid4()))

        context: dict[str, Any] = {
            "event_type": event_type,
            **fields,
        }

        event = ObservationEvent(
            source="dev_agent",
            source_id=str(source_id),
            user_id=self._owner_user_id,
            author="dev-agent",
            author_is_owner=True,
            content=content,
            timestamp=timestamp or datetime.now(),
            context=context,
        )

        log.debug(
            "dev_event_adapted",
            event_type=event_type,
            source_id=event.source_id,
            content_length=len(content),
        )

        return event
