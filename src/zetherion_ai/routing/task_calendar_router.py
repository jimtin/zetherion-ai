"""Task/calendar routing with security gating and cross-calendar conflict checks."""

from __future__ import annotations

import hashlib
from datetime import timedelta
from typing import Any

from zetherion_ai.discord.security.models import ThreatAction
from zetherion_ai.integrations.storage import IntegrationStorage
from zetherion_ai.logging import get_logger
from zetherion_ai.routing.models import (
    ConflictCandidate,
    ConflictDecision,
    DestinationRef,
    DestinationType,
    IngestionEnvelope,
    NormalizedEvent,
    NormalizedTask,
    RouteDecision,
    RouteMode,
    RouteTag,
)
from zetherion_ai.routing.policies import conflict_mode
from zetherion_ai.routing.registry import ProviderRegistry
from zetherion_ai.security.content_pipeline import ContentSecurityPipeline

log = get_logger("zetherion_ai.routing.task_calendar_router")


class TaskCalendarRouter:
    """Routes extracted tasks/events to provider destinations."""

    def __init__(
        self,
        *,
        storage: IntegrationStorage,
        providers: ProviderRegistry,
        security: ContentSecurityPipeline,
    ) -> None:
        self._storage = storage
        self._providers = providers
        self._security = security

    async def route_task(
        self,
        *,
        user_id: int,
        provider: str,
        envelope: IngestionEnvelope,
        task: NormalizedTask,
        security_checked: bool = False,
    ) -> RouteDecision:
        """Route a normalized task, scheduling an event when time-bound."""
        if not security_checked:
            security_decision = await self._security_gate(
                user_id=user_id,
                provider=provider,
                source_type=envelope.source_type.value,
                content=self._content_from_envelope(envelope),
            )
            if security_decision is not None:
                return security_decision

        if task.scheduled_start and task.scheduled_end:
            event = NormalizedEvent(
                title=task.title,
                start=task.scheduled_start,
                end=task.scheduled_end,
                description=task.description,
                metadata={"derived_from": "task", "priority": task.priority},
            )
            return await self.route_event(
                user_id=user_id,
                provider=provider,
                envelope=envelope,
                event=event,
                route_tag=RouteTag.CALENDAR_CANDIDATE,
                security_checked=True,
            )

        adapters = self._providers.adapters(provider)
        if adapters is None or adapters.task is None:
            return RouteDecision(
                mode=RouteMode.DRAFT,
                route_tag=RouteTag.TASK_CANDIDATE,
                reason=f"No task adapter configured for provider '{provider}'",
                provider=provider,
            )

        task_lists = await adapters.task.list_task_lists(user_id)
        await self._sync_destinations(
            user_id=user_id,
            provider=provider,
            destination_type=DestinationType.TASK_LIST,
            records=task_lists,
        )

        primary = await self._storage.get_primary_destination(
            user_id,
            provider,
            DestinationType.TASK_LIST,
        )
        if primary is None:
            return RouteDecision(
                mode=RouteMode.ASK,
                route_tag=RouteTag.TASK_CANDIDATE,
                reason="No primary task list configured. Ask the user to choose one.",
                provider=provider,
                metadata={
                    "needs_primary_selection": True,
                    "task_list_options": [
                        {
                            "id": t.destination_id,
                            "name": t.display_name,
                            "writable": t.writable,
                            "is_primary": t.is_primary,
                        }
                        for t in task_lists
                    ],
                },
            )

        created = await adapters.task.create_task(user_id, primary.destination_id, task)
        local_id = self._stable_local_id(task.title, task.description)
        await self._storage.upsert_object_link(
            user_id=user_id,
            provider=provider,
            object_type="task",
            local_id=local_id,
            external_id=created.task_id,
            destination_id=created.list_id,
            metadata={"title": created.title},
        )

        decision = RouteDecision(
            mode=RouteMode.AUTO,
            route_tag=RouteTag.TASK_CANDIDATE,
            reason="Task routed to primary task list",
            provider=provider,
            target=DestinationRef(
                provider=provider,
                destination_id=primary.destination_id,
                destination_type=DestinationType.TASK_LIST,
                display_name=primary.display_name,
            ),
            metadata={"external_task_id": created.task_id},
        )
        await self._storage.record_routing_decision(
            user_id,
            provider,
            envelope.source_type.value,
            decision,
        )
        return decision

    async def route_event(
        self,
        *,
        user_id: int,
        provider: str,
        envelope: IngestionEnvelope,
        event: NormalizedEvent,
        route_tag: RouteTag = RouteTag.CALENDAR_CANDIDATE,
        security_checked: bool = False,
    ) -> RouteDecision:
        """Route a normalized event with conflict checks across calendars."""
        if not security_checked:
            security_decision = await self._security_gate(
                user_id=user_id,
                provider=provider,
                source_type=envelope.source_type.value,
                content=self._content_from_envelope(envelope),
            )
            if security_decision is not None:
                return security_decision

        adapters = self._providers.adapters(provider)
        if adapters is None or adapters.calendar is None:
            return RouteDecision(
                mode=RouteMode.DRAFT,
                route_tag=route_tag,
                reason=f"No calendar adapter configured for provider '{provider}'",
                provider=provider,
            )

        calendars = await adapters.calendar.list_calendars(user_id)
        await self._sync_destinations(
            user_id=user_id,
            provider=provider,
            destination_type=DestinationType.CALENDAR,
            records=calendars,
        )

        primary = await self._storage.get_primary_destination(
            user_id,
            provider,
            DestinationType.CALENDAR,
        )
        if primary is None:
            return RouteDecision(
                mode=RouteMode.ASK,
                route_tag=route_tag,
                reason="No primary calendar configured. Ask the user to choose one.",
                provider=provider,
                metadata={
                    "needs_primary_selection": True,
                    "calendar_options": [
                        {
                            "id": c.destination_id,
                            "name": c.display_name,
                            "writable": c.writable,
                            "is_primary": c.is_primary,
                        }
                        for c in calendars
                    ],
                },
            )

        calendar_ids = [c.destination_id for c in calendars] or [primary.destination_id]
        events = await adapters.calendar.list_events(
            user_id,
            calendar_ids,
            window_start=event.start - timedelta(hours=2),
            window_end=event.end + timedelta(hours=2),
        )

        high_priority = str(event.metadata.get("priority", "medium")).lower() in {
            "high",
            "urgent",
            "critical",
        }
        attendee_impacting = bool(event.attendees) or bool(event.metadata.get("attendee_impacting"))
        conflict = self._detect_conflict(
            event.start,
            event.end,
            events,
            high_priority=high_priority,
            attendee_impacting=attendee_impacting,
        )
        if conflict and conflict.requires_confirmation:
            decision = RouteDecision(
                mode=RouteMode.ASK,
                route_tag=route_tag,
                reason=conflict.suggestion,
                provider=provider,
                target=DestinationRef(
                    provider=provider,
                    destination_id=primary.destination_id,
                    destination_type=DestinationType.CALENDAR,
                    display_name=primary.display_name,
                ),
                conflict=conflict,
            )
            await self._storage.record_routing_decision(
                user_id,
                provider,
                envelope.source_type.value,
                decision,
            )
            return decision

        if conflict and conflict.severity >= 0.25:
            decision = RouteDecision(
                mode=RouteMode.DRAFT,
                route_tag=route_tag,
                reason=conflict.suggestion,
                provider=provider,
                target=DestinationRef(
                    provider=provider,
                    destination_id=primary.destination_id,
                    destination_type=DestinationType.CALENDAR,
                    display_name=primary.display_name,
                ),
                conflict=conflict,
                metadata={"draft_only": True},
            )
            await self._storage.record_routing_decision(
                user_id,
                provider,
                envelope.source_type.value,
                decision,
            )
            return decision

        created = await adapters.calendar.create_event(user_id, primary.destination_id, event)
        local_id = self._stable_local_id(event.title, event.start.isoformat())
        await self._storage.upsert_object_link(
            user_id=user_id,
            provider=provider,
            object_type="event",
            local_id=local_id,
            external_id=created.event_id,
            destination_id=created.calendar_id,
            metadata={"title": created.title},
        )

        decision = RouteDecision(
            mode=RouteMode.AUTO,
            route_tag=route_tag,
            reason="Event routed to primary calendar",
            provider=provider,
            target=DestinationRef(
                provider=provider,
                destination_id=primary.destination_id,
                destination_type=DestinationType.CALENDAR,
                display_name=primary.display_name,
            ),
            conflict=conflict,
            metadata={"external_event_id": created.event_id},
        )
        await self._storage.record_routing_decision(
            user_id,
            provider,
            envelope.source_type.value,
            decision,
        )
        return decision

    async def _security_gate(
        self,
        *,
        user_id: int,
        provider: str,
        source_type: str,
        content: str,
    ) -> RouteDecision | None:
        sec = await self._security.analyze(
            content,
            source=source_type,
            user_id=user_id,
            context_id=0,
        )

        if sec.verdict.action == ThreatAction.ALLOW:
            return None

        await self._storage.record_security_event(
            user_id=user_id,
            provider=provider,
            source_type=source_type,
            action=sec.verdict.action.value,
            score=sec.verdict.score,
            reason=sec.verdict.ai_reasoning or "security pipeline verdict",
            payload_hash=sec.payload_hash,
            metadata={"tier": sec.verdict.tier_reached},
        )

        if sec.verdict.action == ThreatAction.BLOCK:
            return RouteDecision(
                mode=RouteMode.BLOCK,
                route_tag=RouteTag.IGNORE,
                reason="Blocked by security policy",
                provider=provider,
                metadata={"score": sec.verdict.score},
            )

        return RouteDecision(
            mode=RouteMode.REVIEW,
            route_tag=RouteTag.IGNORE,
            reason="Flagged by security policy; moved to review queue",
            provider=provider,
            metadata={"score": sec.verdict.score},
        )

    def _detect_conflict(
        self,
        start: Any,
        end: Any,
        existing_events: list[Any],
        *,
        high_priority: bool = False,
        attendee_impacting: bool = False,
    ) -> ConflictDecision | None:
        conflicts: list[ConflictCandidate] = []
        max_severity = 0.0

        for existing in existing_events:
            overlap_start = max(start, existing.start)
            overlap_end = min(end, existing.end)
            overlap_minutes = (overlap_end - overlap_start).total_seconds() / 60
            if overlap_minutes <= 0:
                continue

            duration_minutes = max((end - start).total_seconds() / 60, 1)
            severity = min(1.0, overlap_minutes / duration_minutes)
            if severity > max_severity:
                max_severity = severity
            conflicts.append(
                ConflictCandidate(
                    calendar_id=existing.calendar_id,
                    event_id=existing.event_id,
                    title=existing.title,
                    start=existing.start,
                    end=existing.end,
                )
            )

        if not conflicts:
            return None

        mode = conflict_mode(
            severity=max_severity,
            high_priority=high_priority,
            attendee_impacting=attendee_impacting,
        )
        requires_confirmation = mode is RouteMode.ASK
        suggestion = "Minor overlap detected; event drafted."
        if mode is RouteMode.ASK and max_severity >= 0.6:
            suggestion = (
                "Major conflict detected across connected calendars; ask user confirmation."
            )
        elif mode is RouteMode.ASK:
            suggestion = (
                "Meaningful conflict on a high-priority/attendee event; ask user confirmation."
            )
        elif mode is RouteMode.DRAFT:
            suggestion = "Moderate overlap detected; create draft instead of auto-writing."

        return ConflictDecision(
            severity=max_severity,
            requires_confirmation=requires_confirmation,
            suggestion=suggestion,
            conflicts=conflicts,
        )

    async def _sync_destinations(
        self,
        *,
        user_id: int,
        provider: str,
        destination_type: DestinationType,
        records: list[Any],
    ) -> None:
        for rec in records:
            await self._storage.upsert_destination(
                user_id=user_id,
                provider=provider,
                account_ref=rec.metadata.get("account_ref", "default"),
                destination_id=rec.destination_id,
                destination_type=destination_type,
                display_name=rec.display_name,
                is_primary=rec.is_primary,
                writable=rec.writable,
                metadata=rec.metadata,
            )

    def _content_from_envelope(self, envelope: IngestionEnvelope) -> str:
        payload = envelope.payload
        text = str(
            payload.get("body_text") or payload.get("content") or payload.get("subject") or ""
        )
        return text[:6000]

    def _stable_local_id(self, a: str, b: str) -> str:
        raw = f"{a}|{b}".encode()
        return hashlib.sha1(raw, usedforsecurity=False).hexdigest()
