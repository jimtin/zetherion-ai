"""Dispatcher routes extracted items to action targets.

Routes ExtractedItem instances to the appropriate skill or storage
system based on item type and the user's action policies.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from typing import Any, Protocol

from zetherion_ai.logging import get_logger
from zetherion_ai.observation.models import ExtractedItem, ItemType
from zetherion_ai.routing.models import (
    NormalizedEmail,
    RouteDecision,
    RouteTag,
)

log = get_logger("zetherion_ai.observation.dispatcher")


# ---------------------------------------------------------------------------
# Action targets
# ---------------------------------------------------------------------------


class ActionTarget(StrEnum):
    """Where an extracted item gets dispatched to."""

    TASK_MANAGER = "task_manager"
    CALENDAR = "calendar"
    PERSONAL_MODEL = "personal_model"
    CONTACT_GRAPH = "contact_graph"
    MEMORY = "memory"
    NOTIFICATION = "notification"
    DEV_JOURNAL = "dev_journal"
    MILESTONE_TRACKER = "milestone_tracker"


class DefaultMode(StrEnum):
    """Default policy modes for each item type."""

    AUTO = "auto"
    DRAFT = "draft"
    ASK = "ask"
    NEVER = "never"


# Default routing table: item_type → (target, default_mode)
DEFAULT_ROUTES: dict[ItemType, tuple[ActionTarget, DefaultMode]] = {
    ItemType.TASK: (ActionTarget.TASK_MANAGER, DefaultMode.DRAFT),
    ItemType.DEADLINE: (ActionTarget.CALENDAR, DefaultMode.DRAFT),
    ItemType.COMMITMENT: (ActionTarget.TASK_MANAGER, DefaultMode.DRAFT),
    ItemType.CONTACT: (ActionTarget.CONTACT_GRAPH, DefaultMode.AUTO),
    ItemType.FACT: (ActionTarget.PERSONAL_MODEL, DefaultMode.AUTO),
    ItemType.MEETING: (ActionTarget.CALENDAR, DefaultMode.ASK),
    ItemType.REMINDER: (ActionTarget.CALENDAR, DefaultMode.DRAFT),
    ItemType.ACTION_ITEM: (ActionTarget.TASK_MANAGER, DefaultMode.DRAFT),
    ItemType.DEV_IDEA: (ActionTarget.DEV_JOURNAL, DefaultMode.AUTO),
    ItemType.DEV_PROGRESS: (ActionTarget.DEV_JOURNAL, DefaultMode.AUTO),
    ItemType.MILESTONE: (ActionTarget.MILESTONE_TRACKER, DefaultMode.DRAFT),
}

# Minimum confidence to dispatch (below this, items are logged but not acted on)
MIN_DISPATCH_CONFIDENCE = 0.4


# ---------------------------------------------------------------------------
# Action handler protocol
# ---------------------------------------------------------------------------


class ActionHandler(Protocol):
    """Protocol for action target handlers."""

    async def handle_dispatch(
        self,
        item: ExtractedItem,
        *,
        user_id: int,
        mode: str,
    ) -> DispatchResult:
        """Handle a dispatched item.

        Args:
            item: The extracted item to act on.
            user_id: The user this action is for.
            mode: The execution mode ('auto', 'draft', 'ask', 'never').

        Returns:
            Result of the dispatch action.
        """
        ...


# ---------------------------------------------------------------------------
# Policy provider protocol
# ---------------------------------------------------------------------------


class PolicyProvider(Protocol):
    """Protocol for checking user action policies."""

    async def get_mode(self, user_id: int, domain: str, action: str) -> str | None:
        """Get the user's preferred mode for a domain/action.

        Returns the mode string ('auto', 'draft', 'ask', 'never'),
        or None if no policy is set (use default).
        """
        ...


# ---------------------------------------------------------------------------
# Dispatch result
# ---------------------------------------------------------------------------


@dataclass
class DispatchResult:
    """Result of dispatching an item to an action target."""

    item: ExtractedItem
    target: ActionTarget
    mode: str
    success: bool
    message: str = ""
    data: dict[str, Any] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------


class Dispatcher:
    """Routes extracted items to action targets based on routing rules and user policies."""

    def __init__(
        self,
        *,
        handlers: dict[ActionTarget, ActionHandler] | None = None,
        policy_provider: PolicyProvider | None = None,
        email_router: Any | None = None,
        email_provider_default: str = "google",
    ) -> None:
        self._handlers = handlers or {}
        self._policy_provider = policy_provider
        self._email_router = email_router
        self._email_provider_default = email_provider_default

    async def dispatch(self, items: list[ExtractedItem], *, user_id: int) -> list[DispatchResult]:
        """Dispatch a list of extracted items to their action targets.

        Args:
            items: Items extracted by the pipeline.
            user_id: The user these items belong to.

        Returns:
            List of dispatch results.
        """
        results: list[DispatchResult] = []

        for item in items:
            result = await self._dispatch_single(item, user_id=user_id)
            results.append(result)

        dispatched_count = sum(1 for r in results if r.success)
        skipped_count = len(results) - dispatched_count
        log.info(
            "dispatch_complete",
            user_id=user_id,
            total=len(items),
            dispatched=dispatched_count,
            skipped=skipped_count,
        )

        return results

    async def dispatch_email_event(
        self,
        event: Any,
        *,
        user_id: int,
    ) -> list[DispatchResult]:
        """Route an email observation event via the shared EmailRouter."""
        if self._email_router is None:
            return []

        context = event.context if isinstance(event.context, dict) else {}
        provider = str(context.get("provider") or self._email_provider_default).lower()
        account_ref = str(context.get("account_ref") or context.get("account_email") or "default")
        email = self._event_to_normalized_email(event)
        decision: RouteDecision = await self._email_router.process_email(
            user_id=user_id,
            provider=provider,
            account_ref=account_ref,
            email=email,
        )
        return [self._decision_to_dispatch_result(event, decision)]

    async def _dispatch_single(self, item: ExtractedItem, *, user_id: int) -> DispatchResult:
        """Dispatch a single item."""
        # Look up route
        route = DEFAULT_ROUTES.get(item.item_type)
        if route is None:
            log.warning(
                "no_route_for_item_type",
                item_type=item.item_type,
                user_id=user_id,
            )
            return DispatchResult(
                item=item,
                target=ActionTarget.MEMORY,
                mode="never",
                success=False,
                message=f"No route for item type: {item.item_type}",
            )

        target, default_mode = route

        # Check confidence threshold
        if item.confidence < MIN_DISPATCH_CONFIDENCE:
            log.debug(
                "item_below_confidence_threshold",
                item_type=item.item_type,
                confidence=item.confidence,
                threshold=MIN_DISPATCH_CONFIDENCE,
            )
            return DispatchResult(
                item=item,
                target=target,
                mode="never",
                success=False,
                message=f"Confidence {item.confidence:.2f} below threshold "
                f"{MIN_DISPATCH_CONFIDENCE}",
            )

        # Determine mode from user policy or default
        mode = default_mode.value
        if self._policy_provider is not None:
            user_mode = await self._policy_provider.get_mode(
                user_id, target.value, item.item_type.value
            )
            if user_mode is not None:
                mode = user_mode

        # Block if mode is 'never'
        if mode == "never":
            log.info(
                "dispatch_blocked_by_policy",
                item_type=item.item_type,
                target=target,
                user_id=user_id,
            )
            return DispatchResult(
                item=item,
                target=target,
                mode=mode,
                success=False,
                message="Blocked by user policy",
            )

        # Find handler
        handler = self._handlers.get(target)
        if handler is None:
            log.warning(
                "no_handler_for_target",
                target=target,
                item_type=item.item_type,
            )
            return DispatchResult(
                item=item,
                target=target,
                mode=mode,
                success=False,
                message=f"No handler registered for target: {target}",
            )

        # Dispatch to handler
        try:
            result = await handler.handle_dispatch(item, user_id=user_id, mode=mode)
            log.info(
                "item_dispatched",
                item_type=item.item_type,
                target=target,
                mode=mode,
                success=result.success,
                user_id=user_id,
            )
            return result
        except Exception as exc:
            log.error(
                "dispatch_handler_error",
                item_type=item.item_type,
                target=target,
                error=str(exc),
                user_id=user_id,
            )
            return DispatchResult(
                item=item,
                target=target,
                mode=mode,
                success=False,
                message=f"Handler error: {exc}",
            )

    def _event_to_normalized_email(self, event: Any) -> NormalizedEmail:
        context = event.context if isinstance(event.context, dict) else {}
        received_at = event.timestamp if isinstance(event.timestamp, datetime) else datetime.now()
        external_id = str(context.get("external_id") or event.source_id or "")
        subject = str(context.get("subject") or "")
        body_text = str(context.get("body_text") or event.content or "")
        from_email = str(context.get("from_email") or event.author or "")
        to_emails_raw = context.get("to_emails")
        to_emails = (
            [str(v) for v in to_emails_raw if isinstance(v, str)]
            if isinstance(to_emails_raw, list)
            else []
        )
        return NormalizedEmail(
            external_id=external_id,
            thread_id=str(context.get("thread_id") or ""),
            subject=subject,
            body_text=body_text,
            from_email=from_email,
            to_emails=to_emails,
            received_at=received_at,
            metadata={
                "source": event.source,
                "source_id": event.source_id,
                "provider": context.get("provider"),
                "account_ref": context.get("account_ref"),
                "account_email": context.get("account_email"),
            },
        )

    def _decision_to_dispatch_result(self, event: Any, decision: RouteDecision) -> DispatchResult:
        context = event.context if isinstance(event.context, dict) else {}
        route_item_type = {
            RouteTag.TASK_CANDIDATE: ItemType.TASK,
            RouteTag.CALENDAR_CANDIDATE: ItemType.MEETING,
            RouteTag.REPLY_CANDIDATE: ItemType.ACTION_ITEM,
            RouteTag.DIGEST_ONLY: ItemType.FACT,
            RouteTag.IGNORE: ItemType.FACT,
        }.get(decision.route_tag, ItemType.FACT)

        target = {
            RouteTag.TASK_CANDIDATE: ActionTarget.TASK_MANAGER,
            RouteTag.CALENDAR_CANDIDATE: ActionTarget.CALENDAR,
            RouteTag.REPLY_CANDIDATE: ActionTarget.NOTIFICATION,
            RouteTag.DIGEST_ONLY: ActionTarget.MEMORY,
            RouteTag.IGNORE: ActionTarget.MEMORY,
        }.get(decision.route_tag, ActionTarget.MEMORY)

        extracted = ExtractedItem(
            item_type=route_item_type,
            content=str(context.get("subject") or event.content or "(email)"),
            confidence=1.0,
            metadata=decision.to_dict(),
            source_event=event,
        )
        success = decision.mode.value not in {"block"}
        return DispatchResult(
            item=extracted,
            target=target,
            mode=decision.mode.value,
            success=success,
            message=decision.reason,
            data=decision.to_dict(),
        )
