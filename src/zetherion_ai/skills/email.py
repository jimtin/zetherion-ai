"""Provider-agnostic email skill backed by the shared work router."""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from zetherion_ai.integrations.storage import IntegrationStorage
from zetherion_ai.logging import get_logger
from zetherion_ai.routing.email_router import EmailRouter, EmailRoutingUnavailableError
from zetherion_ai.routing.models import DestinationType, RouteDecision
from zetherion_ai.routing.registry import ProviderRegistry
from zetherion_ai.skills.base import (
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.skills.email")

INTENT_ROUTE = "email_route"
INTENT_STATUS = "email_status"
INTENT_CONNECT = "email_connect"
INTENT_DISCONNECT = "email_disconnect"
INTENT_SET_PRIMARY_CALENDAR = "email_set_primary_calendar"
INTENT_SET_PRIMARY_TASK_LIST = "email_set_primary_task_list"
INTENT_QUEUE_STATUS = "email_queue_status"
INTENT_QUEUE_RESUME = "email_queue_resume"

ALL_INTENTS = [
    INTENT_ROUTE,
    INTENT_STATUS,
    INTENT_CONNECT,
    INTENT_DISCONNECT,
    INTENT_SET_PRIMARY_CALENDAR,
    INTENT_SET_PRIMARY_TASK_LIST,
    INTENT_QUEUE_STATUS,
    INTENT_QUEUE_RESUME,
]

OAuthAuthorizer = Callable[..., Awaitable[dict[str, str]]]


class EmailSkill(Skill):
    """Provider-neutral email routing skill."""

    INTENTS = ALL_INTENTS

    def __init__(
        self,
        memory: QdrantMemory | None = None,
        *,
        router: EmailRouter | None = None,
        storage: IntegrationStorage | None = None,
        providers: ProviderRegistry | None = None,
        account_manager: Any | None = None,
        oauth_authorizer: OAuthAuthorizer | None = None,
        default_provider: str = "google",
        legacy_gmail_skill: Skill | None = None,
    ) -> None:
        super().__init__(memory=memory)
        self._router = router
        self._storage = storage
        self._providers = providers
        self._account_manager = account_manager
        self._oauth_authorizer = oauth_authorizer
        self._default_provider = default_provider
        self._legacy_gmail_skill = legacy_gmail_skill

    def configure(
        self,
        *,
        router: EmailRouter | None = None,
        storage: IntegrationStorage | None = None,
        providers: ProviderRegistry | None = None,
        account_manager: Any | None = None,
        oauth_authorizer: OAuthAuthorizer | None = None,
        default_provider: str | None = None,
        legacy_gmail_skill: Skill | None = None,
    ) -> None:
        """Inject runtime dependencies after async startup wiring."""
        if router is not None:
            self._router = router
        if storage is not None:
            self._storage = storage
        if providers is not None:
            self._providers = providers
        if account_manager is not None:
            self._account_manager = account_manager
        if oauth_authorizer is not None:
            self._oauth_authorizer = oauth_authorizer
        if default_provider is not None:
            self._default_provider = default_provider
        if legacy_gmail_skill is not None:
            self._legacy_gmail_skill = legacy_gmail_skill

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="email",
            description=(
                "Provider-agnostic email routing and extraction for " "tasks/calendar actions"
            ),
            version="1.0.0",
            permissions=PermissionSet.from_list(
                [
                    Permission.READ_MEMORIES.name,
                    Permission.WRITE_MEMORIES.name,
                    Permission.SEND_MESSAGES.name,
                ]
            ),
            intents=self.INTENTS,
        )

    async def initialize(self) -> bool:
        from zetherion_ai.skills.base import SkillStatus

        self._status = SkillStatus.READY
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        user_id = int(request.user_id) if request.user_id else 0
        provider = self._resolve_provider(request)

        if user_id <= 0:
            return SkillResponse.error_response(request.id, "Invalid user id")

        try:
            if request.intent == INTENT_ROUTE:
                return await self._handle_route(request, user_id, provider)
            if request.intent == INTENT_STATUS:
                return await self._handle_status(request, user_id, provider)
            if request.intent == INTENT_CONNECT:
                return await self._handle_connect(request, user_id, provider)
            if request.intent == INTENT_DISCONNECT:
                return await self._handle_disconnect(request, user_id, provider)
            if request.intent == INTENT_SET_PRIMARY_CALENDAR:
                return await self._handle_set_primary(
                    request,
                    user_id,
                    provider,
                    destination_type=DestinationType.CALENDAR,
                )
            if request.intent == INTENT_SET_PRIMARY_TASK_LIST:
                return await self._handle_set_primary(
                    request,
                    user_id,
                    provider,
                    destination_type=DestinationType.TASK_LIST,
                )
            if request.intent == INTENT_QUEUE_STATUS:
                return await self._handle_queue_status(request, user_id, provider)
            if request.intent == INTENT_QUEUE_RESUME:
                return await self._handle_queue_resume(request, user_id, provider)
            return SkillResponse.error_response(request.id, f"Unknown intent: {request.intent}")
        except Exception as exc:
            log.error(
                "email_skill_error",
                intent=request.intent,
                provider=provider,
                error=str(exc),
            )
            return SkillResponse.error_response(request.id, str(exc))

    async def _handle_route(
        self,
        request: SkillRequest,
        user_id: int,
        provider: str,
    ) -> SkillResponse:
        if self._router is None:
            if self._legacy_gmail_skill is not None:
                legacy_request = SkillRequest(
                    id=request.id,
                    user_id=request.user_id,
                    intent="email_check",
                    message=request.message,
                    context={**request.context, "skill_name": "gmail"},
                )
                return await self._legacy_gmail_skill.handle(legacy_request)
            return SkillResponse.error_response(
                request.id,
                "Email router is not configured",
            )

        limit_raw = request.context.get("limit")
        if isinstance(limit_raw, int | str) and str(limit_raw).isdigit():
            limit = int(limit_raw)
        else:
            limit = 20

        try:
            decisions = await self._router.ingest_unread(
                user_id=user_id,
                provider=provider,
                limit=limit,
            )
        except EmailRoutingUnavailableError as exc:
            return SkillResponse(
                request_id=request.id,
                success=False,
                error=str(exc),
                message=str(exc),
                data={
                    "provider": provider,
                    "error_code": exc.error_code,
                    "queued_count": exc.queued_count,
                    "queue_batch_id": exc.queue_batch_id,
                    "processed_count": exc.processed_count,
                },
            )
        if not decisions:
            return SkillResponse(
                request_id=request.id,
                message="No unread messages found to route.",
                data={"count": 0, "provider": provider},
            )

        mode_counts = Counter(d.mode.value for d in decisions)
        tag_counts = Counter(d.route_tag.value for d in decisions)
        routed = mode_counts.get("auto", 0)
        blocked = mode_counts.get("block", 0)
        review = mode_counts.get("review", 0)
        draft = mode_counts.get("draft", 0)
        ask = mode_counts.get("ask", 0)

        lines = [
            f"Processed {len(decisions)} unread email(s) via {provider}.",
            f"Auto-routed: {routed}",
            f"Review queued: {review}",
            f"Drafted: {draft}",
            f"Needs confirmation: {ask}",
            f"Blocked as malicious: {blocked}",
        ]

        primary_prompt = self._primary_selection_prompt(decisions)
        if primary_prompt:
            lines.append(primary_prompt)

        return SkillResponse(
            request_id=request.id,
            message="\n".join(lines),
            data={
                "provider": provider,
                "count": len(decisions),
                "mode_counts": dict(mode_counts),
                "route_tag_counts": dict(tag_counts),
                "decisions": [d.to_dict() for d in decisions],
            },
        )

    async def _handle_queue_status(
        self,
        request: SkillRequest,
        user_id: int,
        provider: str,
    ) -> SkillResponse:
        if self._router is None:
            return SkillResponse.error_response(request.id, "Email router is not configured")
        status = await self._router.queue_status(user_id=user_id, provider=provider)
        counts = status.get("counts", {})
        total = int(status.get("pending_total", 0))
        ready = bool(status.get("ready"))
        error_code = status.get("error_code")
        error_detail = status.get("error_detail")
        msg = (
            f"Email queue status for {provider}: {total} item(s) pending. "
            f"Pipeline ready: {'yes' if ready else 'no'}."
        )
        if error_code:
            msg = f"{msg}\nDependency issue: {error_code} ({error_detail or 'no details'})."
        return SkillResponse(
            request_id=request.id,
            message=msg,
            data={
                "provider": provider,
                "ready": ready,
                "error_code": error_code,
                "error_detail": error_detail,
                "counts": counts,
                "pending_total": total,
            },
        )

    async def _handle_queue_resume(
        self,
        request: SkillRequest,
        user_id: int,
        provider: str,
    ) -> SkillResponse:
        if self._router is None:
            return SkillResponse.error_response(request.id, "Email router is not configured")

        limit_raw = request.context.get("limit")
        if isinstance(limit_raw, int | str) and str(limit_raw).isdigit():
            limit = int(limit_raw)
        else:
            limit = 100

        try:
            decisions = await self._router.resume_queue(
                user_id=user_id,
                provider=provider,
                limit=limit,
            )
        except EmailRoutingUnavailableError as exc:
            return SkillResponse(
                request_id=request.id,
                success=False,
                error=str(exc),
                message=str(exc),
                data={
                    "provider": provider,
                    "error_code": exc.error_code,
                    "queued_count": exc.queued_count,
                    "queue_batch_id": exc.queue_batch_id,
                    "processed_count": exc.processed_count,
                },
            )

        mode_counts = Counter(d.mode.value for d in decisions)
        return SkillResponse(
            request_id=request.id,
            message=f"Queue resume processed {len(decisions)} email(s) for {provider}.",
            data={
                "provider": provider,
                "count": len(decisions),
                "mode_counts": dict(mode_counts),
                "decisions": [d.to_dict() for d in decisions],
            },
        )

    async def _handle_status(
        self,
        request: SkillRequest,
        user_id: int,
        provider: str,
    ) -> SkillResponse:
        if self._storage is None or self._providers is None:
            if self._legacy_gmail_skill is not None:
                legacy_request = SkillRequest(
                    id=request.id,
                    user_id=request.user_id,
                    intent="email_status",
                    message=request.message,
                    context={**request.context, "skill_name": "gmail"},
                )
                return await self._legacy_gmail_skill.handle(legacy_request)
            return SkillResponse.error_response(
                request.id,
                "Integration storage/providers are not configured",
            )

        adapters = self._providers.adapters(provider)
        if adapters is None:
            return SkillResponse(
                request_id=request.id,
                message=f"Provider '{provider}' is not configured.",
                data={"provider": provider, "configured": False},
            )

        source_count = 0
        if adapters.email is not None:
            sources = await adapters.email.list_sources(user_id)
            source_count = len(sources)

        primary_calendar = await self._storage.get_primary_destination(
            user_id,
            provider,
            DestinationType.CALENDAR,
        )
        primary_task_list = await self._storage.get_primary_destination(
            user_id,
            provider,
            DestinationType.TASK_LIST,
        )

        msg = (
            f"Provider: {provider}\n"
            f"Connected mailboxes: {source_count}\n"
            "Primary calendar: "
            f"{primary_calendar.display_name if primary_calendar else 'not set'}\n"
            "Primary task list: "
            f"{primary_task_list.display_name if primary_task_list else 'not set'}"
        )

        return SkillResponse(
            request_id=request.id,
            message=msg,
            data={
                "provider": provider,
                "connected_mailboxes": source_count,
                "primary_calendar": (
                    {
                        "id": primary_calendar.destination_id,
                        "name": primary_calendar.display_name,
                    }
                    if primary_calendar
                    else None
                ),
                "primary_task_list": (
                    {
                        "id": primary_task_list.destination_id,
                        "name": primary_task_list.display_name,
                    }
                    if primary_task_list
                    else None
                ),
            },
        )

    async def _handle_connect(
        self,
        request: SkillRequest,
        user_id: int,
        provider: str,
    ) -> SkillResponse:
        if self._oauth_authorizer is None:
            return SkillResponse.error_response(
                request.id,
                "Email account linking is not configured",
            )

        try:
            payload = await self._oauth_authorizer(user_id=user_id, provider=provider)
        except Exception as exc:
            return SkillResponse.error_response(
                request.id,
                f"Could not create OAuth link: {exc}",
            )

        auth_url = str(payload.get("auth_url", "")).strip()
        if not auth_url:
            return SkillResponse.error_response(
                request.id,
                "OAuth link was not generated",
            )

        return SkillResponse(
            request_id=request.id,
            message=f"Open this URL to connect your {provider} account:\n{auth_url}",
            data=payload,
        )

    async def _handle_disconnect(
        self,
        request: SkillRequest,
        user_id: int,
        provider: str,
    ) -> SkillResponse:
        if provider != "google":
            return SkillResponse.error_response(
                request.id,
                f"Provider '{provider}' disconnect is not implemented yet",
            )

        if self._account_manager is None:
            return SkillResponse.error_response(
                request.id,
                "Account manager is not configured",
            )

        account_email = str(request.context.get("account_email", "")).strip().lower()
        if not account_email:
            account_email = self._extract_email(request.message)
        if not account_email:
            return SkillResponse.error_response(
                request.id,
                "Missing account_email in context (or include an email in the message)",
            )

        account = None
        if hasattr(self._account_manager, "get_account_by_email"):
            account = await self._account_manager.get_account_by_email(user_id, account_email)

        removed = await self._account_manager.remove_account(user_id, account_email)
        if not removed:
            return SkillResponse.error_response(
                request.id,
                f"No connected account found for {account_email}",
            )

        if (
            self._storage is not None
            and account is not None
            and getattr(account, "id", None) is not None
        ):
            await self._storage.delete_account(
                user_id=user_id,
                provider=provider,
                account_ref=str(account.id),
            )
            await self._storage.delete_destination(
                user_id=user_id,
                provider=provider,
                destination_type=DestinationType.MAILBOX,
                destination_id=account_email,
            )

        return SkillResponse(
            request_id=request.id,
            message=f"Disconnected {account_email}. This mailbox is no longer monitored.",
            data={"provider": provider, "account_email": account_email, "removed": True},
        )

    async def _handle_set_primary(
        self,
        request: SkillRequest,
        user_id: int,
        provider: str,
        *,
        destination_type: DestinationType,
    ) -> SkillResponse:
        if self._storage is None:
            return SkillResponse.error_response(request.id, "Integration storage is not configured")

        destination_id = str(request.context.get("destination_id", "")).strip()
        if not destination_id:
            type_label = "calendar" if destination_type == DestinationType.CALENDAR else "task list"
            return SkillResponse.error_response(
                request.id,
                f"Missing destination_id in context for primary {type_label} selection",
            )

        ok = await self._storage.set_primary_destination(
            user_id=user_id,
            provider=provider,
            destination_type=destination_type,
            destination_id=destination_id,
        )
        if not ok:
            return SkillResponse.error_response(
                request.id,
                f"Could not set primary destination '{destination_id}'",
            )

        type_label = "calendar" if destination_type == DestinationType.CALENDAR else "task list"
        return SkillResponse(
            request_id=request.id,
            message=f"Primary {type_label} set to {destination_id} for {provider}.",
            data={
                "provider": provider,
                "destination_type": destination_type.value,
                "destination_id": destination_id,
            },
        )

    def _resolve_provider(self, request: SkillRequest) -> str:
        context_provider = request.context.get("provider")
        if isinstance(context_provider, str) and context_provider.strip():
            return context_provider.strip().lower()

        msg = request.message.lower()
        if "outlook" in msg:
            return "outlook"
        if "google" in msg or "gmail" in msg:
            return "google"
        return self._default_provider

    def _primary_selection_prompt(self, decisions: list[RouteDecision]) -> str:
        for decision in decisions:
            metadata = decision.metadata
            if not metadata.get("needs_primary_selection"):
                continue

            calendar_options = metadata.get("calendar_options")
            if isinstance(calendar_options, list) and calendar_options:
                rendered = ", ".join(
                    f"{item.get('name', item.get('id'))} ({item.get('id')})"
                    for item in calendar_options
                    if isinstance(item, dict)
                )
                if rendered:
                    return f"Choose a primary calendar before auto-routing: {rendered}"

            task_list_options = metadata.get("task_list_options")
            if isinstance(task_list_options, list) and task_list_options:
                rendered = ", ".join(
                    f"{item.get('name', item.get('id'))} ({item.get('id')})"
                    for item in task_list_options
                    if isinstance(item, dict)
                )
                if rendered:
                    return f"Choose a primary task list before auto-routing: {rendered}"

        return ""

    def _extract_email(self, text: str) -> str:
        match = re.search(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}", text)
        if not match:
            return ""
        return match.group(0).lower()
