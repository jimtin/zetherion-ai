"""Unit tests for provider-agnostic EmailSkill."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from zetherion_ai.routing.email_router import EmailRoutingUnavailableError
from zetherion_ai.routing.models import DestinationType, RouteDecision, RouteMode, RouteTag
from zetherion_ai.skills.base import SkillRequest, SkillResponse, SkillStatus
from zetherion_ai.skills.email import (
    ALL_INTENTS,
    INTENT_CONNECT,
    INTENT_DISCONNECT,
    INTENT_QUEUE_RESUME,
    INTENT_QUEUE_STATUS,
    INTENT_ROUTE,
    INTENT_SET_PRIMARY_CALENDAR,
    INTENT_SET_PRIMARY_TASK_LIST,
    INTENT_STATUS,
    EmailSkill,
)


def _request(
    *,
    intent: str,
    user_id: str = "42",
    message: str = "",
    context: dict | None = None,
) -> SkillRequest:
    return SkillRequest(
        id=uuid4(),
        user_id=user_id,
        intent=intent,
        message=message,
        context=context or {},
    )


def _decision(
    *,
    mode: RouteMode = RouteMode.AUTO,
    route_tag: RouteTag = RouteTag.TASK_CANDIDATE,
    reason: str = "ok",
    metadata: dict | None = None,
) -> RouteDecision:
    return RouteDecision(
        mode=mode,
        route_tag=route_tag,
        reason=reason,
        provider="google",
        metadata=metadata or {},
    )


@pytest.fixture
def router() -> AsyncMock:
    stub = AsyncMock()
    stub.ingest_unread = AsyncMock(return_value=[])
    stub.queue_status = AsyncMock(
        return_value={
            "provider": "google",
            "ready": True,
            "error_code": None,
            "error_detail": None,
            "counts": {"pending": 2},
            "pending_total": 2,
        }
    )
    stub.resume_queue = AsyncMock(return_value=[])
    return stub


@pytest.fixture
def storage() -> AsyncMock:
    stub = AsyncMock()
    stub.get_primary_destination = AsyncMock(return_value=None)
    stub.set_primary_destination = AsyncMock(return_value=True)
    return stub


@pytest.fixture
def providers() -> SimpleNamespace:
    email_adapter = SimpleNamespace(list_sources=AsyncMock(return_value=[]))
    adapters = SimpleNamespace(email=email_adapter, task=None, calendar=None)
    return SimpleNamespace(adapters=lambda provider: adapters if provider == "google" else None)


@pytest.fixture
def skill(router: AsyncMock, storage: AsyncMock, providers: SimpleNamespace) -> EmailSkill:
    return EmailSkill(
        router=router,
        storage=storage,
        providers=providers,  # type: ignore[arg-type]
    )


class TestEmailSkillMetadata:
    def test_metadata_name_and_intents(self, skill: EmailSkill) -> None:
        assert skill.metadata.name == "email"
        assert skill.metadata.intents == ALL_INTENTS

    async def test_initialize_sets_ready_status(self, skill: EmailSkill) -> None:
        result = await skill.initialize()
        assert result is True
        assert skill.status == SkillStatus.READY


class TestEmailSkillRouting:
    async def test_invalid_user_id_returns_error(self, skill: EmailSkill) -> None:
        response = await skill.handle(_request(intent=INTENT_ROUTE, user_id="0"))
        assert response.success is False
        assert response.error == "Invalid user id"

    async def test_unknown_intent_returns_error(self, skill: EmailSkill) -> None:
        response = await skill.handle(_request(intent="bogus_intent"))
        assert response.success is False
        assert "Unknown intent" in (response.error or "")

    async def test_route_without_router_uses_legacy_gmail_alias(self) -> None:
        legacy = AsyncMock()
        expected = SkillResponse(request_id=uuid4(), message="legacy")
        legacy.handle = AsyncMock(return_value=expected)

        email_skill = EmailSkill(router=None, legacy_gmail_skill=legacy)
        req = _request(intent=INTENT_ROUTE, message="check")
        response = await email_skill.handle(req)

        assert response is expected
        legacy_req = legacy.handle.await_args.args[0]
        assert legacy_req.intent == "email_check"
        assert legacy_req.context["skill_name"] == "gmail"

    async def test_route_without_router_returns_error(self) -> None:
        email_skill = EmailSkill(router=None)
        response = await email_skill.handle(_request(intent=INTENT_ROUTE))
        assert response.success is False
        assert "not configured" in (response.error or "").lower()

    async def test_route_uses_default_limit_and_empty_result_message(
        self,
        skill: EmailSkill,
        router: AsyncMock,
    ) -> None:
        req = _request(intent=INTENT_ROUTE, context={"limit": "not-a-number"})
        response = await skill.handle(req)

        assert response.success is True
        assert response.message == "No unread messages found to route."
        assert response.data["count"] == 0
        router.ingest_unread.assert_awaited_once_with(
            user_id=42,
            provider="google",
            limit=20,
        )

    async def test_route_summary_includes_counts_and_primary_prompt(
        self,
        skill: EmailSkill,
        router: AsyncMock,
    ) -> None:
        router.ingest_unread = AsyncMock(
            return_value=[
                _decision(mode=RouteMode.AUTO, route_tag=RouteTag.TASK_CANDIDATE),
                _decision(mode=RouteMode.BLOCK, route_tag=RouteTag.IGNORE),
                _decision(mode=RouteMode.REVIEW, route_tag=RouteTag.IGNORE),
                _decision(
                    mode=RouteMode.DRAFT,
                    route_tag=RouteTag.CALENDAR_CANDIDATE,
                    metadata={
                        "needs_primary_selection": True,
                        "calendar_options": [
                            {"id": "cal-1", "name": "Work"},
                            {"id": "cal-2", "name": "Personal"},
                        ],
                    },
                ),
                _decision(mode=RouteMode.ASK, route_tag=RouteTag.CALENDAR_CANDIDATE),
            ]
        )

        req = _request(intent=INTENT_ROUTE, context={"limit": "5"})
        response = await skill.handle(req)

        assert response.success is True
        assert "Processed 5 unread email(s) via google." in response.message
        assert "Auto-routed: 1" in response.message
        assert "Review queued: 1" in response.message
        assert "Drafted: 1" in response.message
        assert "Needs confirmation: 1" in response.message
        assert "Blocked as malicious: 1" in response.message
        assert "Choose a primary calendar before auto-routing" in response.message
        assert response.data["mode_counts"]["auto"] == 1
        assert response.data["route_tag_counts"]["calendar_candidate"] == 2

    async def test_route_handler_exception_is_returned_as_error(
        self,
        skill: EmailSkill,
        router: AsyncMock,
    ) -> None:
        router.ingest_unread = AsyncMock(side_effect=RuntimeError("boom"))
        response = await skill.handle(_request(intent=INTENT_ROUTE))
        assert response.success is False
        assert response.error == "boom"

    async def test_route_returns_outage_contract_when_router_unavailable(
        self,
        skill: EmailSkill,
        router: AsyncMock,
    ) -> None:
        router.ingest_unread = AsyncMock(
            side_effect=EmailRoutingUnavailableError(
                error_code="ROUTER_UNAVAILABLE",
                message="router down",
                queued_count=3,
                queue_batch_id="batch-1",
                processed_count=0,
            )
        )

        response = await skill.handle(_request(intent=INTENT_ROUTE))

        assert response.success is False
        assert response.data["error_code"] == "ROUTER_UNAVAILABLE"
        assert response.data["queued_count"] == 3
        assert response.data["queue_batch_id"] == "batch-1"
        assert response.data["processed_count"] == 0


class TestEmailQueueIntents:
    async def test_queue_status_returns_counts(
        self,
        skill: EmailSkill,
        router: AsyncMock,
    ) -> None:
        response = await skill.handle(_request(intent=INTENT_QUEUE_STATUS))

        assert response.success is True
        assert "Pipeline ready: yes" in response.message
        assert response.data["pending_total"] == 2
        router.queue_status.assert_awaited_once_with(user_id=42, provider="google")

    async def test_queue_resume_returns_processed_summary(
        self,
        skill: EmailSkill,
        router: AsyncMock,
    ) -> None:
        router.resume_queue = AsyncMock(
            return_value=[
                _decision(mode=RouteMode.AUTO, route_tag=RouteTag.TASK_CANDIDATE),
                _decision(mode=RouteMode.DRAFT, route_tag=RouteTag.REPLY_CANDIDATE),
            ]
        )

        response = await skill.handle(_request(intent=INTENT_QUEUE_RESUME, context={"limit": "7"}))

        assert response.success is True
        assert "processed 2 email(s)" in response.message.lower()
        assert response.data["mode_counts"]["auto"] == 1
        assert response.data["mode_counts"]["draft"] == 1
        router.resume_queue.assert_awaited_once_with(user_id=42, provider="google", limit=7)

    async def test_queue_resume_returns_outage_contract(
        self,
        skill: EmailSkill,
        router: AsyncMock,
    ) -> None:
        router.resume_queue = AsyncMock(
            side_effect=EmailRoutingUnavailableError(
                error_code="LOCAL_MODEL_UNAVAILABLE",
                message="local model unavailable",
                queued_count=5,
                queue_batch_id="batch-2",
                processed_count=0,
            )
        )

        response = await skill.handle(_request(intent=INTENT_QUEUE_RESUME))

        assert response.success is False
        assert response.data["error_code"] == "LOCAL_MODEL_UNAVAILABLE"
        assert response.data["queued_count"] == 5
        assert response.data["queue_batch_id"] == "batch-2"


class TestEmailSkillStatus:
    async def test_status_without_storage_uses_legacy_alias(self) -> None:
        legacy = AsyncMock()
        expected = SkillResponse(request_id=uuid4(), message="legacy status")
        legacy.handle = AsyncMock(return_value=expected)
        email_skill = EmailSkill(storage=None, providers=None, legacy_gmail_skill=legacy)

        req = _request(intent=INTENT_STATUS)
        response = await email_skill.handle(req)

        assert response is expected
        legacy_req = legacy.handle.await_args.args[0]
        assert legacy_req.intent == "email_status"
        assert legacy_req.context["skill_name"] == "gmail"

    async def test_status_without_dependencies_returns_error(self) -> None:
        email_skill = EmailSkill(storage=None, providers=None)
        response = await email_skill.handle(_request(intent=INTENT_STATUS))
        assert response.success is False
        assert "not configured" in (response.error or "").lower()

    async def test_status_provider_not_configured(
        self,
        storage: AsyncMock,
    ) -> None:
        providers = SimpleNamespace(adapters=lambda provider: None)
        email_skill = EmailSkill(storage=storage, providers=providers)  # type: ignore[arg-type]
        response = await email_skill.handle(_request(intent=INTENT_STATUS))

        assert response.success is True
        assert "not configured" in response.message
        assert response.data["configured"] is False

    async def test_status_returns_connected_sources_and_primary_destinations(
        self,
        skill: EmailSkill,
        storage: AsyncMock,
        providers: SimpleNamespace,
    ) -> None:
        adapters = providers.adapters("google")
        assert adapters is not None
        adapters.email.list_sources = AsyncMock(return_value=[{"id": "inbox"}, {"id": "ops"}])
        storage.get_primary_destination = AsyncMock(
            side_effect=[
                SimpleNamespace(destination_id="cal-1", display_name="Main Calendar"),
                SimpleNamespace(destination_id="tasks-1", display_name="Main Tasks"),
            ]
        )

        response = await skill.handle(_request(intent=INTENT_STATUS))

        assert response.success is True
        assert "Connected mailboxes: 2" in response.message
        assert response.data["primary_calendar"]["id"] == "cal-1"
        assert response.data["primary_task_list"]["id"] == "tasks-1"


class TestEmailSkillPrimarySelection:
    async def test_set_primary_requires_storage(self) -> None:
        email_skill = EmailSkill(storage=None)
        req = _request(intent=INTENT_SET_PRIMARY_CALENDAR, context={"destination_id": "cal-1"})
        response = await email_skill.handle(req)
        assert response.success is False
        assert "not configured" in (response.error or "").lower()

    async def test_set_primary_requires_destination_id(self, skill: EmailSkill) -> None:
        response = await skill.handle(_request(intent=INTENT_SET_PRIMARY_TASK_LIST))
        assert response.success is False
        assert "Missing destination_id" in (response.error or "")

    async def test_set_primary_returns_error_when_storage_update_fails(
        self,
        skill: EmailSkill,
        storage: AsyncMock,
    ) -> None:
        storage.set_primary_destination = AsyncMock(return_value=False)
        req = _request(intent=INTENT_SET_PRIMARY_CALENDAR, context={"destination_id": "cal-1"})
        response = await skill.handle(req)
        assert response.success is False
        assert "Could not set primary destination" in (response.error or "")

    async def test_set_primary_success_response(
        self,
        skill: EmailSkill,
        storage: AsyncMock,
    ) -> None:
        storage.set_primary_destination = AsyncMock(return_value=True)
        req = _request(
            intent=INTENT_SET_PRIMARY_TASK_LIST,
            context={"destination_id": "tasks-1"},
        )
        response = await skill.handle(req)

        assert response.success is True
        assert response.message == "Primary task list set to tasks-1 for google."
        storage.set_primary_destination.assert_awaited_once_with(
            user_id=42,
            provider="google",
            destination_type=DestinationType.TASK_LIST,
            destination_id="tasks-1",
        )


class TestEmailSkillProviderResolution:
    def test_resolve_provider_from_context(self, skill: EmailSkill) -> None:
        provider = skill._resolve_provider(
            _request(intent=INTENT_ROUTE, context={"provider": "Outlook"})
        )
        assert provider == "outlook"

    def test_resolve_provider_from_message(self, skill: EmailSkill) -> None:
        assert (
            skill._resolve_provider(_request(intent=INTENT_ROUTE, message="please sync outlook"))
            == "outlook"
        )
        assert (
            skill._resolve_provider(_request(intent=INTENT_ROUTE, message="check my gmail"))
            == "google"
        )

    def test_resolve_provider_falls_back_to_default(self) -> None:
        email_skill = EmailSkill(default_provider="google")
        provider = email_skill._resolve_provider(
            _request(intent=INTENT_ROUTE, message="just do it")
        )
        assert provider == "google"

    def test_primary_selection_prompt_prefers_task_options_when_present(
        self,
        skill: EmailSkill,
    ) -> None:
        prompt = skill._primary_selection_prompt(
            [
                _decision(
                    mode=RouteMode.DRAFT,
                    route_tag=RouteTag.TASK_CANDIDATE,
                    metadata={
                        "needs_primary_selection": True,
                        "task_list_options": [{"id": "list-1", "name": "Inbox"}],
                    },
                )
            ]
        )
        assert prompt == "Choose a primary task list before auto-routing: Inbox (list-1)"


class TestEmailSkillConnectDisconnect:
    async def test_connect_requires_oauth_authorizer(self) -> None:
        email_skill = EmailSkill(oauth_authorizer=None)
        response = await email_skill.handle(_request(intent=INTENT_CONNECT))
        assert response.success is False
        assert "not configured" in (response.error or "").lower()

    async def test_connect_returns_oauth_url_payload(self) -> None:
        oauth_authorizer = AsyncMock(
            return_value={
                "provider": "google",
                "auth_url": "https://example.test/auth",
                "state": "s1",
            }
        )
        email_skill = EmailSkill(oauth_authorizer=oauth_authorizer)

        response = await email_skill.handle(_request(intent=INTENT_CONNECT))

        assert response.success is True
        assert "https://example.test/auth" in response.message
        assert response.data["provider"] == "google"
        oauth_authorizer.assert_awaited_once_with(user_id=42, provider="google")

    async def test_connect_returns_error_when_authorizer_raises(self) -> None:
        oauth_authorizer = AsyncMock(side_effect=RuntimeError("auth down"))
        email_skill = EmailSkill(oauth_authorizer=oauth_authorizer)

        response = await email_skill.handle(_request(intent=INTENT_CONNECT))

        assert response.success is False
        assert "Could not create OAuth link" in (response.error or "")

    async def test_connect_returns_error_when_auth_url_missing(self) -> None:
        oauth_authorizer = AsyncMock(return_value={"provider": "google"})
        email_skill = EmailSkill(oauth_authorizer=oauth_authorizer)

        response = await email_skill.handle(_request(intent=INTENT_CONNECT))

        assert response.success is False
        assert response.error == "OAuth link was not generated"

    async def test_disconnect_requires_google_provider(self) -> None:
        email_skill = EmailSkill(account_manager=AsyncMock())
        response = await email_skill.handle(
            _request(intent=INTENT_DISCONNECT, context={"provider": "outlook"})
        )
        assert response.success is False
        assert "not implemented yet" in (response.error or "")

    async def test_disconnect_requires_account_manager(self) -> None:
        email_skill = EmailSkill(account_manager=None)
        response = await email_skill.handle(
            _request(intent=INTENT_DISCONNECT, context={"account_email": "a@example.com"})
        )
        assert response.success is False
        assert response.error == "Account manager is not configured"

    async def test_disconnect_requires_email(self) -> None:
        account_manager = AsyncMock()
        account_manager.remove_account = AsyncMock(return_value=True)
        email_skill = EmailSkill(account_manager=account_manager)

        response = await email_skill.handle(_request(intent=INTENT_DISCONNECT, message="remove"))

        assert response.success is False
        assert "Missing account_email" in (response.error or "")

    async def test_disconnect_returns_error_when_account_missing(self) -> None:
        account_manager = AsyncMock()
        account_manager.get_account_by_email = AsyncMock(return_value=None)
        account_manager.remove_account = AsyncMock(return_value=False)
        email_skill = EmailSkill(account_manager=account_manager)

        response = await email_skill.handle(
            _request(intent=INTENT_DISCONNECT, context={"account_email": "gone@example.com"})
        )

        assert response.success is False
        assert response.error == "No connected account found for gone@example.com"

    async def test_disconnect_success_removes_storage_links(self) -> None:
        account = SimpleNamespace(id=55)
        account_manager = AsyncMock()
        account_manager.get_account_by_email = AsyncMock(return_value=account)
        account_manager.remove_account = AsyncMock(return_value=True)
        storage = AsyncMock()
        storage.delete_account = AsyncMock(return_value=True)
        storage.delete_destination = AsyncMock(return_value=True)

        email_skill = EmailSkill(account_manager=account_manager, storage=storage)
        response = await email_skill.handle(
            _request(intent=INTENT_DISCONNECT, message="disconnect me@example.com")
        )

        assert response.success is True
        assert "no longer monitored" in response.message
        account_manager.remove_account.assert_awaited_once_with(42, "me@example.com")
        storage.delete_account.assert_awaited_once_with(
            user_id=42,
            provider="google",
            account_ref="55",
        )
        storage.delete_destination.assert_awaited_once_with(
            user_id=42,
            provider="google",
            destination_type=DestinationType.MAILBOX,
            destination_id="me@example.com",
        )
