"""Unit tests for provider-agnostic EmailRouter."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import zetherion_ai.routing.email_router as email_router_module
from zetherion_ai.agent.inference import InferenceResult
from zetherion_ai.agent.providers import Provider, TaskType
from zetherion_ai.discord.security.models import ThreatAction, ThreatVerdict
from zetherion_ai.routing.classification import EmailAction, EmailClassification
from zetherion_ai.routing.email_router import (
    ERROR_ROUTER_UNAVAILABLE,
    ClassificationOutput,
    EmailRouter,
    EmailRoutingUnavailableError,
    ModelUnavailableError,
    PersonalityOutput,
)
from zetherion_ai.routing.models import (
    IngestionSource,
    NormalizedEmail,
    RouteDecision,
    RouteMode,
    RouteTag,
)


@dataclass
class _SecResult:
    verdict: ThreatVerdict
    payload_hash: str = "abc123"


class _StorageStub:
    def __init__(self) -> None:
        self.store_email_message = AsyncMock()
        self.record_security_event = AsyncMock()
        self.record_routing_decision = AsyncMock()
        self.upsert_object_link = AsyncMock()
        self.get_object_link_by_external = AsyncMock(return_value=None)
        self.enqueue_ingestion_batch = AsyncMock(return_value=("batch-1", 0))
        self.claim_ingestion_queue_items = AsyncMock(return_value=[])
        self.mark_ingestion_items_done = AsyncMock()
        self.mark_ingestion_items_blocked_unhealthy = AsyncMock()
        self.move_ingestion_item_to_dead_letter = AsyncMock()
        self.get_ingestion_queue_counts = AsyncMock(return_value={})


class _ProvidersStub:
    def adapters(self, provider: str) -> Any:
        return None


class _ProvidersWithEmailStub:
    def __init__(self, messages: list[dict[str, Any]]) -> None:
        self._adapter = MagicMock()
        self._adapter.email = MagicMock()
        self._adapter.email.list_unread = AsyncMock(return_value=messages)

    def adapters(self, provider: str) -> Any:
        return self._adapter


class _TaskCalendarStub:
    def __init__(self) -> None:
        self.route_task = AsyncMock(
            return_value=RouteDecision(
                mode=RouteMode.AUTO,
                route_tag=RouteTag.TASK_CANDIDATE,
                reason="ok",
                provider="google",
            )
        )
        self.route_event = AsyncMock(
            return_value=RouteDecision(
                mode=RouteMode.AUTO,
                route_tag=RouteTag.CALENDAR_CANDIDATE,
                reason="ok",
                provider="google",
            )
        )


class _HTTPResponse:
    def __init__(self, payload: Any, *, raise_exc: Exception | None = None) -> None:
        self._payload = payload
        self._raise_exc = raise_exc

    def raise_for_status(self) -> None:
        if self._raise_exc is not None:
            raise self._raise_exc

    def json(self) -> Any:
        return self._payload


class _HTTPClient:
    def __init__(
        self,
        response: _HTTPResponse | None = None,
        *,
        exc: Exception | None = None,
    ) -> None:
        self._response = response
        self._exc = exc

    async def __aenter__(self) -> _HTTPClient:
        return self

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False

    async def get(self, url: str) -> _HTTPResponse:
        del url
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response

    async def post(self, url: str, json: Any) -> _HTTPResponse:
        del url, json
        if self._exc is not None:
            raise self._exc
        assert self._response is not None
        return self._response


def _email() -> NormalizedEmail:
    return NormalizedEmail(
        external_id="m1",
        thread_id="t1",
        subject="Please do this task",
        body_text="Please follow up on the roadmap by Friday.",
        from_email="boss@example.com",
        to_emails=["me@example.com"],
        received_at=datetime.now(),
    )


@pytest.mark.asyncio
async def test_blocked_email_is_terminal_and_not_routed() -> None:
    storage = _StorageStub()
    providers = _ProvidersStub()
    security = MagicMock()
    security.analyze = AsyncMock(
        return_value=_SecResult(ThreatVerdict(action=ThreatAction.BLOCK, score=0.9, tier_reached=2))
    )
    task_calendar = _TaskCalendarStub()
    inference = MagicMock()

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
    )

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="default",
        email=_email(),
    )

    assert decision.mode == RouteMode.BLOCK
    assert decision.route_tag == RouteTag.IGNORE
    task_calendar.route_task.assert_not_awaited()
    task_calendar.route_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_flagged_email_goes_to_review_without_routing() -> None:
    storage = _StorageStub()
    providers = _ProvidersStub()
    security = MagicMock()
    security.analyze = AsyncMock(
        return_value=_SecResult(ThreatVerdict(action=ThreatAction.FLAG, score=0.42, tier_reached=2))
    )
    task_calendar = _TaskCalendarStub()
    inference = MagicMock()

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
    )

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="default",
        email=_email(),
    )

    assert decision.mode == RouteMode.REVIEW
    assert decision.route_tag == RouteTag.IGNORE
    task_calendar.route_task.assert_not_awaited()
    task_calendar.route_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_local_extraction_success_skips_cloud_fallback() -> None:
    storage = _StorageStub()
    providers = _ProvidersStub()
    security = MagicMock()
    security.analyze = AsyncMock(
        return_value=_SecResult(ThreatVerdict(action=ThreatAction.ALLOW, score=0.0, tier_reached=1))
    )
    task_calendar = _TaskCalendarStub()

    local_result = MagicMock()
    local_result.content = '{"kind":"task","title":"Follow up"}'
    local_result.provider.value = "ollama"

    inference = MagicMock()
    inference._call_ollama = AsyncMock(return_value=local_result)
    inference._call_provider = AsyncMock()
    inference._gemini_client = object()
    inference._claude_client = object()
    inference._openai_client = object()

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
    )
    router._triage_route_tag = AsyncMock(return_value=RouteTag.TASK_CANDIDATE)  # type: ignore[assignment]

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="default",
        email=_email(),
    )

    assert decision.mode == RouteMode.AUTO
    inference._call_provider.assert_not_awaited()
    task_calendar.route_task.assert_awaited_once()


@pytest.mark.asyncio
async def test_extraction_parse_failure_returns_draft_instead_of_error() -> None:
    storage = _StorageStub()
    providers = _ProvidersStub()
    security = MagicMock()
    security.analyze = AsyncMock(
        return_value=_SecResult(ThreatVerdict(action=ThreatAction.ALLOW, score=0.0, tier_reached=1))
    )
    task_calendar = _TaskCalendarStub()

    local_result = MagicMock()
    local_result.content = "not valid json output"
    local_result.provider.value = "ollama"

    inference = MagicMock()
    inference._call_ollama = AsyncMock(return_value=local_result)
    inference._call_provider = AsyncMock()

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
    )
    router._triage_route_tag = AsyncMock(return_value=RouteTag.TASK_CANDIDATE)  # type: ignore[assignment]

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="default",
        email=_email(),
    )

    assert decision.mode == RouteMode.DRAFT
    assert decision.route_tag == RouteTag.TASK_CANDIDATE
    task_calendar.route_task.assert_not_awaited()
    task_calendar.route_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_cloud_first_extraction_uses_inference_broker() -> None:
    storage = _StorageStub()
    providers = _ProvidersStub()
    security = MagicMock()
    security.analyze = AsyncMock(
        return_value=_SecResult(ThreatVerdict(action=ThreatAction.ALLOW, score=0.0, tier_reached=1))
    )
    task_calendar = _TaskCalendarStub()

    inference = MagicMock()
    inference.infer = AsyncMock(
        side_effect=[
            RuntimeError("classification unavailable"),
            RuntimeError("personality unavailable"),
            MagicMock(
                content='{"kind":"task","title":"From cloud extractor"}',
                provider=Provider.GEMINI,
                model="gemini-2.5-flash",
                latency_ms=123.0,
            ),
        ]
    )
    inference._gemini_client = object()

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
        local_extraction_required=False,
    )
    router._triage_route_tag = AsyncMock(return_value=RouteTag.TASK_CANDIDATE)  # type: ignore[assignment]

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="default",
        email=_email(),
    )

    assert decision.mode == RouteMode.AUTO
    assert inference.infer.await_count == 3
    extraction_call = inference.infer.await_args_list[2]
    assert extraction_call.args[1] == TaskType.DATA_EXTRACTION


@pytest.mark.asyncio
async def test_local_extraction_required_disables_cloud_fallback() -> None:
    storage = _StorageStub()
    providers = _ProvidersStub()
    security = MagicMock()
    security.analyze = AsyncMock(
        return_value=_SecResult(ThreatVerdict(action=ThreatAction.ALLOW, score=0.0, tier_reached=1))
    )
    task_calendar = _TaskCalendarStub()

    inference = MagicMock()
    inference._call_ollama = AsyncMock(side_effect=RuntimeError("ollama unavailable"))
    inference._call_provider = AsyncMock()
    inference._gemini_client = object()
    inference._claude_client = object()
    inference._openai_client = object()

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
        local_extraction_required=True,
        user_context_resolver=AsyncMock(return_value={"timezone": "Australia/Sydney"}),
    )
    router._triage_route_tag = AsyncMock(return_value=RouteTag.CALENDAR_CANDIDATE)  # type: ignore[assignment]

    with pytest.raises(ModelUnavailableError) as exc_info:
        await router.process_email(
            user_id=123,
            provider="google",
            account_ref="default",
            email=_email(),
        )

    assert exc_info.value.error_code == "LOCAL_MODEL_UNAVAILABLE"
    inference._call_provider.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_email_passes_account_email_to_personality_extractor() -> None:
    """When account_ref is numeric, process_email still resolves canonical owner email."""
    storage = _StorageStub()
    providers = _ProvidersStub()
    task_calendar = _TaskCalendarStub()
    inference = MagicMock()
    inference._call_ollama = AsyncMock(
        return_value=MagicMock(content='{"kind":"none"}', provider=Provider.OLLAMA)
    )

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    router._classify_email = AsyncMock(return_value=ClassificationOutput())  # type: ignore[assignment]
    router._extract_personality = AsyncMock(return_value=PersonalityOutput())  # type: ignore[assignment]
    router._triage_route_tag = AsyncMock(return_value=RouteTag.IGNORE)  # type: ignore[assignment]

    email = _email()
    email.from_email = "me@example.com"
    email.metadata = {"account_email": "me@example.com"}

    await router.process_email(
        user_id=123,
        provider="google",
        account_ref="42",
        email=email,
    )

    router._extract_personality.assert_awaited_once_with(email, "me@example.com")


@pytest.mark.asyncio
async def test_cloud_readiness_does_not_require_local_models() -> None:
    """Cloud-first mode reports ready when Groq is healthy, even if local models are down."""
    storage = _StorageStub()
    providers = _ProvidersStub()
    security = _allow_security()
    task_calendar = _TaskCalendarStub()

    inference = MagicMock()
    inference.available_providers = {Provider.GROQ}
    inference.health_check = AsyncMock(return_value=True)

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
        local_extraction_required=False,
    )

    ready, error_code, detail = await router._check_pipeline_readiness()

    assert ready is True
    assert error_code is None
    assert detail is None


@pytest.mark.asyncio
async def test_attachment_emails_are_processed_with_attachment_metadata() -> None:
    storage = _StorageStub()
    providers = _ProvidersStub()
    security = MagicMock()
    security.analyze = AsyncMock(
        return_value=_SecResult(ThreatVerdict(action=ThreatAction.ALLOW, score=0.0, tier_reached=1))
    )
    task_calendar = _TaskCalendarStub()
    local_result = MagicMock()
    local_result.content = '{"kind":"task","title":"Follow up"}'
    local_result.provider.value = "ollama"
    inference = MagicMock()
    inference._call_ollama = AsyncMock(return_value=local_result)

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
        attachment_handling_enabled=False,
    )
    router._triage_route_tag = AsyncMock(return_value=RouteTag.TASK_CANDIDATE)  # type: ignore[assignment]

    email = _email()
    email.metadata = {
        "has_attachments": True,
        "attachment_count": 1,
        "attachment_filenames": ["agenda.pdf"],
    }

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="default",
        email=email,
    )

    assert decision.mode == RouteMode.AUTO
    security.analyze.assert_awaited_once()
    task_calendar.route_task.assert_awaited_once()
    task_calendar.route_event.assert_not_awaited()
    upsert_meta = storage.upsert_object_link.await_args.kwargs["metadata"]
    assert upsert_meta["attachment_filtered"] is True
    assert upsert_meta["attachment_count"] == 1


@pytest.mark.asyncio
async def test_dedupe_skips_second_pass() -> None:
    storage = _StorageStub()
    storage.get_object_link_by_external = AsyncMock(return_value={"external_id": "x"})
    providers = _ProvidersStub()
    security = MagicMock()
    security.analyze = AsyncMock()
    task_calendar = _TaskCalendarStub()
    inference = MagicMock()

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
    )

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="default",
        email=_email(),
    )

    assert decision.mode == RouteMode.SKIP
    assert decision.reason == "Duplicate email already processed"
    security.analyze.assert_not_awaited()
    task_calendar.route_task.assert_not_awaited()
    task_calendar.route_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_ingest_unread_without_adapter_returns_skip() -> None:
    storage = _StorageStub()
    providers = _ProvidersStub()
    security = MagicMock()
    security.analyze = AsyncMock()
    task_calendar = _TaskCalendarStub()
    inference = MagicMock()

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
    )

    decisions = await router.ingest_unread(user_id=123, provider="google", limit=5)

    assert len(decisions) == 1
    assert decisions[0].mode == RouteMode.SKIP
    assert decisions[0].route_tag == RouteTag.IGNORE
    assert decisions[0].reason.startswith("No email adapter configured")
    assert decisions[0].provider == "google"
    assert decisions[0].metadata == {}
    assert decisions[0].target is None
    assert decisions[0].conflict is None
    assert decisions[0].to_dict()["provider"] == "google"
    assert IngestionSource.EMAIL.value == "email"


@pytest.mark.asyncio
async def test_ingest_unread_queues_all_and_errors_when_router_unhealthy() -> None:
    storage = _StorageStub()
    messages = [
        {
            "account_ref": "default",
            "external_id": "m-1",
            "subject": "hello",
            "from_email": "a@example.com",
            "to_emails": ["b@example.com"],
            "body_preview": "test",
        },
        {
            "account_ref": "default",
            "external_id": "m-2",
            "subject": "hello again",
            "from_email": "a@example.com",
            "to_emails": ["b@example.com"],
            "body_preview": "test2",
        },
    ]
    storage.enqueue_ingestion_batch = AsyncMock(return_value=("batch-x", 2))
    providers = _ProvidersWithEmailStub(messages)
    security = MagicMock()
    security.analyze = AsyncMock()
    task_calendar = _TaskCalendarStub()
    inference = MagicMock()

    router = EmailRouter(
        storage=storage,
        providers=providers,  # type: ignore[arg-type]
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
    )
    router._check_pipeline_readiness = AsyncMock(  # type: ignore[assignment]
        return_value=(False, ERROR_ROUTER_UNAVAILABLE, "router model missing")
    )

    with pytest.raises(EmailRoutingUnavailableError) as exc_info:
        await router.ingest_unread(user_id=123, provider="google", limit=10)

    assert exc_info.value.error_code == ERROR_ROUTER_UNAVAILABLE
    assert exc_info.value.queued_count == 2
    assert exc_info.value.queue_batch_id == "batch-x"
    storage.enqueue_ingestion_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_unread_queues_messages_when_drain_hits_model_unavailable() -> None:
    storage = _StorageStub()
    storage.enqueue_ingestion_batch = AsyncMock(return_value=("batch-drain", 2))
    messages = [
        {
            "account_ref": "default",
            "external_id": "m-1",
            "subject": "hello",
            "from_email": "a@example.com",
            "to_emails": ["b@example.com"],
            "body_preview": "test",
        },
        {
            "account_ref": "default",
            "external_id": "m-2",
            "subject": "hello again",
            "from_email": "a@example.com",
            "to_emails": ["b@example.com"],
            "body_preview": "test2",
        },
    ]
    router = EmailRouter(
        storage=storage,
        providers=_ProvidersWithEmailStub(messages),  # type: ignore[arg-type]
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
    )
    router._check_pipeline_readiness = AsyncMock(return_value=(True, None, None))  # type: ignore[assignment]
    router._drain_ingestion_queue = AsyncMock(  # type: ignore[assignment]
        side_effect=ModelUnavailableError(
            error_code="LOCAL_MODEL_UNAVAILABLE",
            message="router unavailable",
        )
    )

    with pytest.raises(EmailRoutingUnavailableError) as exc_info:
        await router.ingest_unread(user_id=123, provider="google", limit=10)

    assert exc_info.value.error_code == "LOCAL_MODEL_UNAVAILABLE"
    assert exc_info.value.queued_count == 2
    assert exc_info.value.queue_batch_id == "batch-drain"
    assert exc_info.value.processed_count == 0
    storage.enqueue_ingestion_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_ingest_unread_queues_remaining_messages_when_processing_fails_mid_loop() -> None:
    storage = _StorageStub()
    storage.enqueue_ingestion_batch = AsyncMock(return_value=("batch-mid", 1))
    messages = [
        {
            "account_ref": "default",
            "external_id": "m-1",
            "thread_id": "t-1",
            "subject": "hello",
            "from_email": "a@example.com",
            "to_emails": ["b@example.com"],
            "body_text": "one",
            "received_at": datetime.now().isoformat(),
        },
        {
            "account_ref": "default",
            "external_id": "m-2",
            "thread_id": "t-2",
            "subject": "hello again",
            "from_email": "a@example.com",
            "to_emails": ["b@example.com"],
            "body_text": "two",
            "received_at": datetime.now().isoformat(),
        },
    ]
    router = EmailRouter(
        storage=storage,
        providers=_ProvidersWithEmailStub(messages),  # type: ignore[arg-type]
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
    )
    router._check_pipeline_readiness = AsyncMock(return_value=(True, None, None))  # type: ignore[assignment]
    router._drain_ingestion_queue = AsyncMock(return_value=[])  # type: ignore[assignment]
    router.process_email = AsyncMock(  # type: ignore[assignment]
        side_effect=[
            RouteDecision(
                mode=RouteMode.AUTO,
                route_tag=RouteTag.TASK_CANDIDATE,
                reason="ok",
                provider="google",
            ),
            ModelUnavailableError(
                error_code="LOCAL_MODEL_UNAVAILABLE",
                message="router unavailable",
            ),
        ]
    )

    with pytest.raises(EmailRoutingUnavailableError) as exc_info:
        await router.ingest_unread(user_id=123, provider="google", limit=10)

    assert exc_info.value.error_code == "LOCAL_MODEL_UNAVAILABLE"
    assert exc_info.value.queued_count == 1
    assert exc_info.value.queue_batch_id == "batch-mid"
    assert exc_info.value.processed_count == 1
    storage.enqueue_ingestion_batch.assert_awaited_once()


@pytest.mark.asyncio
async def test_resume_queue_raises_when_pipeline_unhealthy() -> None:
    storage = _StorageStub()
    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
    )
    router._check_pipeline_readiness = AsyncMock(  # type: ignore[assignment]
        return_value=(False, ERROR_ROUTER_UNAVAILABLE, "router unavailable")
    )

    with pytest.raises(EmailRoutingUnavailableError) as exc_info:
        await router.resume_queue(user_id=123, provider="google")

    assert exc_info.value.error_code == ERROR_ROUTER_UNAVAILABLE
    assert exc_info.value.processed_count == 0


@pytest.mark.asyncio
async def test_resume_queue_drains_when_pipeline_is_ready() -> None:
    router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
    )
    decision = RouteDecision(
        mode=RouteMode.AUTO,
        route_tag=RouteTag.TASK_CANDIDATE,
        reason="ok",
        provider="google",
    )
    router._check_pipeline_readiness = AsyncMock(return_value=(True, None, None))  # type: ignore[assignment]
    router._drain_ingestion_queue = AsyncMock(return_value=[decision])  # type: ignore[assignment]

    decisions = await router.resume_queue(user_id=123, provider="google", limit=7)

    assert decisions == [decision]
    router._drain_ingestion_queue.assert_awaited_once_with(  # type: ignore[attr-defined]
        user_id=123,
        provider="google",
        limit=7,
    )


@pytest.mark.asyncio
async def test_process_email_skips_security_gate_when_disabled() -> None:
    storage = _StorageStub()
    security = _allow_security()
    inference = MagicMock()
    local_result = MagicMock()
    local_result.content = '{"kind":"none"}'
    local_result.provider.value = "ollama"
    inference._call_ollama = AsyncMock(return_value=local_result)
    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=security,
        task_calendar_router=_TaskCalendarStub(),
        inference=inference,
        email_security_gate_enabled=False,
    )
    router._classify_email = AsyncMock(return_value=ClassificationOutput())  # type: ignore[assignment]
    router._extract_personality = AsyncMock(return_value=PersonalityOutput())  # type: ignore[assignment]
    router._triage_route_tag = AsyncMock(return_value=RouteTag.IGNORE)  # type: ignore[assignment]

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="default",
        email=_email(),
    )

    assert decision.route_tag == RouteTag.IGNORE
    security.analyze.assert_not_awaited()


@pytest.mark.asyncio
async def test_process_email_routes_event_extractions_to_calendar_router() -> None:
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()
    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=MagicMock(),
    )
    router._classify_email = AsyncMock(return_value=ClassificationOutput())  # type: ignore[assignment]
    router._extract_personality = AsyncMock(return_value=PersonalityOutput())  # type: ignore[assignment]
    router._triage_route_tag = AsyncMock(return_value=RouteTag.CALENDAR_CANDIDATE)  # type: ignore[assignment]
    router._extract_for_route = AsyncMock(  # type: ignore[assignment]
        return_value=MagicMock(event=MagicMock(), task=None, metadata={"kind": "event"})
    )

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="default",
        email=_email(),
    )

    assert decision.route_tag == RouteTag.CALENDAR_CANDIDATE
    task_calendar.route_event.assert_awaited_once()
    task_calendar.route_task.assert_not_awaited()


@pytest.mark.asyncio
async def test_extract_for_route_event_paths_cover_missing_and_present_times() -> None:
    router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
    )
    router._extract_with_fallback = AsyncMock(  # type: ignore[assignment]
        side_effect=[
            (
                '{"kind":"event","title":"Missing times"}',
                "groq",
                "llama",
                12.0,
            ),
            (
                json.dumps(
                    {
                        "kind": "event",
                        "title": "Planning Session",
                        "start": "2026-03-18T09:00:00+00:00",
                        "end": "2026-03-18T10:00:00+00:00",
                        "attendees": ["a@example.com"],
                    }
                ),
                "groq",
                "llama",
                14.0,
            ),
        ]
    )

    missing = await router._extract_for_route(  # noqa: SLF001
        123,
        _email(),
        RouteTag.CALENDAR_CANDIDATE,
    )
    present = await router._extract_for_route(  # noqa: SLF001
        123,
        _email(),
        RouteTag.CALENDAR_CANDIDATE,
    )

    assert missing.event is None
    assert missing.metadata["error"] == "missing_times"
    assert present.event is not None
    assert present.event.title == "Planning Session"


@pytest.mark.asyncio
async def test_extract_for_route_handles_provider_failures_and_parse_failures() -> None:
    router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
    )
    router._extract_with_fallback = AsyncMock(side_effect=RuntimeError("boom"))  # type: ignore[assignment]

    with pytest.raises(
        ModelUnavailableError,
        match="Email extraction unavailable: boom",
    ) as exc_info:
        await router._extract_for_route(123, _email(), RouteTag.TASK_CANDIDATE)  # noqa: SLF001

    assert exc_info.value.error_code == email_router_module.ERROR_LOCAL_MODEL_UNAVAILABLE

    router._extract_with_fallback = AsyncMock(  # type: ignore[assignment]
        return_value=("definitely not json", "groq", "llama", 12.0)
    )
    parsed = await router._extract_for_route(123, _email(), RouteTag.TASK_CANDIDATE)  # noqa: SLF001

    assert parsed.task is None
    assert parsed.metadata["error"] == "parse_failed"
    assert parsed.metadata["extractor_provider"] == "groq"


@pytest.mark.asyncio
async def test_extract_with_fallback_covers_missing_local_client_and_empty_results() -> None:
    router_without_ollama = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=object(),  # type: ignore[arg-type]
    )

    with pytest.raises(ModelUnavailableError, match="Ollama client is unavailable"):
        await router_without_ollama._extract_with_fallback("prompt")  # noqa: SLF001

    empty_local = MagicMock()
    empty_local.content = ""
    empty_local.provider.value = "ollama"
    empty_local.model = "llama"
    empty_local.latency_ms = 1.0
    local_inference = MagicMock()
    local_inference._call_ollama = AsyncMock(return_value=empty_local)
    router_empty_local = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=local_inference,
    )
    with pytest.raises(ModelUnavailableError, match="empty response"):
        await router_empty_local._extract_with_fallback("prompt")  # noqa: SLF001

    cloud_result = MagicMock()
    cloud_result.content = ""
    cloud_result.provider = Provider.GROQ
    cloud_result.model = "llama-3.3"
    cloud_result.latency_ms = 2.0
    cloud_inference = MagicMock()
    cloud_inference.infer = AsyncMock(return_value=cloud_result)
    router_empty_cloud = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=cloud_inference,
        local_extraction_required=False,
    )
    with pytest.raises(RuntimeError, match="empty content"):
        await router_empty_cloud._extract_with_fallback("prompt")  # noqa: SLF001


def test_provider_available_covers_set_and_client_branches() -> None:
    router_from_set = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(available_providers={Provider.GROQ}),
        local_extraction_required=False,
    )
    assert router_from_set._provider_available(Provider.GROQ) is True  # noqa: SLF001
    assert router_from_set._provider_available(Provider.GEMINI) is False  # noqa: SLF001

    inference = MagicMock()
    inference.available_providers = None
    inference._gemini_client = object()
    inference._claude_client = lambda: None
    inference._openai_client = object()
    inference._groq_client = None
    router_from_clients = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=inference,
        local_extraction_required=False,
    )

    assert router_from_clients._provider_available(Provider.GEMINI) is True  # noqa: SLF001
    assert router_from_clients._provider_available(Provider.CLAUDE) is False  # noqa: SLF001
    assert router_from_clients._provider_available(Provider.OPENAI) is True  # noqa: SLF001
    assert router_from_clients._provider_available(Provider.GROQ) is False  # noqa: SLF001


@pytest.mark.asyncio
async def test_check_pipeline_readiness_uses_local_mode_and_reports_missing_providers() -> None:
    local_router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
        local_extraction_required=True,
    )
    local_router._check_local_pipeline_readiness = AsyncMock(  # type: ignore[assignment]
        side_effect=[
            (False, ERROR_ROUTER_UNAVAILABLE, "router down"),
            (True, None, None),
        ]
    )

    ready, error_code, detail = await local_router._check_pipeline_readiness()  # noqa: SLF001
    assert (ready, error_code, detail) == (False, ERROR_ROUTER_UNAVAILABLE, "router down")

    ready, error_code, detail = await local_router._check_pipeline_readiness()  # noqa: SLF001
    assert (ready, error_code, detail) == (True, None, None)

    no_provider_router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(available_providers=None),
        local_extraction_required=False,
    )
    ready, detail = await no_provider_router._check_cloud_pipeline_readiness()  # noqa: SLF001
    assert (ready, detail) == (False, "No cloud providers configured")


@pytest.mark.asyncio
async def test_check_pipeline_readiness_and_cloud_readiness_cover_fallback_paths() -> None:
    no_health_router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(available_providers=None, _groq_client=object()),
        local_extraction_required=False,
    )
    ready, detail = await no_health_router._check_cloud_pipeline_readiness()  # noqa: SLF001
    assert ready is True
    assert detail is None

    unhealthy_inference = MagicMock()
    unhealthy_inference.available_providers = {Provider.GROQ}
    unhealthy_inference.health_check = AsyncMock(return_value=False)
    unhealthy_router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=unhealthy_inference,
        local_extraction_required=False,
    )
    ready, detail = await unhealthy_router._check_cloud_pipeline_readiness()  # noqa: SLF001
    assert ready is False
    assert detail == "groq unhealthy"

    fallback_router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
        local_extraction_required=False,
    )
    fallback_router._check_cloud_pipeline_readiness = AsyncMock(  # type: ignore[assignment]
        return_value=(False, "groq unhealthy")
    )
    fallback_router._check_local_pipeline_readiness = AsyncMock(  # type: ignore[assignment]
        return_value=(True, None, None)
    )
    ready, error_code, detail = await fallback_router._check_pipeline_readiness()  # noqa: SLF001
    assert (ready, error_code, detail) == (True, None, None)

    blocked_router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
        local_extraction_required=False,
    )
    blocked_router._check_cloud_pipeline_readiness = AsyncMock(  # type: ignore[assignment]
        return_value=(False, "groq unhealthy")
    )
    blocked_router._check_local_pipeline_readiness = AsyncMock(  # type: ignore[assignment]
        return_value=(False, "LOCAL_MODEL_UNAVAILABLE", "router model missing")
    )
    ready, error_code, detail = await blocked_router._check_pipeline_readiness()  # noqa: SLF001
    assert ready is False
    assert error_code == "LOCAL_MODEL_UNAVAILABLE"
    assert detail == "groq unhealthy | router model missing"


@pytest.mark.asyncio
async def test_check_local_pipeline_readiness_and_ollama_model_ready_paths(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    readiness_router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
    )
    readiness_router._check_ollama_model_ready = AsyncMock(  # type: ignore[assignment]
        return_value=(False, "router down")
    )
    ready, error_code, detail = await readiness_router._check_local_pipeline_readiness()  # noqa: SLF001
    assert (ready, error_code, detail) == (False, ERROR_ROUTER_UNAVAILABLE, "router down")

    readiness_router._check_ollama_model_ready = AsyncMock(  # type: ignore[assignment]
        side_effect=[(True, None), (False, "extractor missing")]
    )
    ready, error_code, detail = await readiness_router._check_local_pipeline_readiness()  # noqa: SLF001
    assert (ready, error_code, detail) == (
        False,
        "LOCAL_MODEL_UNAVAILABLE",
        "extractor missing",
    )

    readiness_router._check_ollama_model_ready = AsyncMock(  # type: ignore[assignment]
        side_effect=[(True, None), (True, None)]
    )
    ready, error_code, detail = await readiness_router._check_local_pipeline_readiness()  # noqa: SLF001
    assert (ready, error_code, detail) == (True, None, None)

    router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
    )

    def unreachable_client(timeout: float) -> _HTTPClient:
        del timeout
        return _HTTPClient(exc=RuntimeError("offline"))

    monkeypatch.setattr(email_router_module.httpx, "AsyncClient", unreachable_client)
    ready, detail = await router._check_ollama_model_ready(  # noqa: SLF001
        base_url="http://ollama",
        model_name="llama",
        purpose="router",
    )
    assert ready is False
    assert "endpoint unreachable" in str(detail)

    def malformed_client(timeout: float) -> _HTTPClient:
        del timeout
        return _HTTPClient(_HTTPResponse({"models": "bad"}))

    monkeypatch.setattr(email_router_module.httpx, "AsyncClient", malformed_client)
    ready, detail = await router._check_ollama_model_ready(  # noqa: SLF001
        base_url="http://ollama",
        model_name="llama",
        purpose="router",
    )
    assert (ready, detail) == (False, "router returned malformed /api/tags payload")

    def missing_model_client(timeout: float) -> _HTTPClient:
        del timeout
        return _HTTPClient(_HTTPResponse({"models": ["skip-me", {"name": "other-model"}]}))

    monkeypatch.setattr(email_router_module.httpx, "AsyncClient", missing_model_client)
    ready, detail = await router._check_ollama_model_ready(  # noqa: SLF001
        base_url="http://ollama",
        model_name="llama",
        purpose="router",
    )
    assert (ready, detail) == (False, "router model 'llama' is not loaded")

    def healthy_client(timeout: float) -> _HTTPClient:
        del timeout
        return _HTTPClient(_HTTPResponse({"models": [{"model": "llama"}]}))

    monkeypatch.setattr(email_router_module.httpx, "AsyncClient", healthy_client)
    ready, detail = await router._check_ollama_model_ready(  # noqa: SLF001
        base_url="http://ollama",
        model_name="llama",
        purpose="router",
    )
    assert (ready, detail) == (True, None)


@pytest.mark.asyncio
async def test_enqueue_and_drain_queue_cover_empty_blocked_and_dead_letter_paths() -> None:
    storage = _StorageStub()
    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
    )

    assert await router._enqueue_messages(  # noqa: SLF001
        user_id=123,
        provider="google",
        messages=[],
        error_code=ERROR_ROUTER_UNAVAILABLE,
        error_detail="offline",
    ) == (None, 0)

    storage.claim_ingestion_queue_items = AsyncMock(return_value=[])
    assert await router._drain_ingestion_queue(user_id=123, provider="google") == []  # noqa: SLF001

    payload = {
        "external_id": "m1",
        "thread_id": "t1",
        "subject": "Please do this task",
        "body_text": "Please follow up on the roadmap by Friday.",
        "from_email": "boss@example.com",
        "to_emails": ["me@example.com"],
        "received_at": datetime.now().isoformat(),
    }
    queued = [
        MagicMock(id=1, payload=payload, account_ref="default"),
        MagicMock(id=2, payload=payload, account_ref="default"),
    ]
    storage.claim_ingestion_queue_items = AsyncMock(return_value=queued)
    router.process_email = AsyncMock(  # type: ignore[assignment]
        side_effect=[
            RouteDecision(
                mode=RouteMode.AUTO,
                route_tag=RouteTag.TASK_CANDIDATE,
                reason="ok",
                provider="google",
            ),
            ModelUnavailableError(
                error_code="LOCAL_MODEL_UNAVAILABLE",
                message="router unavailable",
            ),
        ]
    )

    with pytest.raises(ModelUnavailableError):
        await router._drain_ingestion_queue(user_id=123, provider="google")  # noqa: SLF001

    storage.mark_ingestion_items_done.assert_awaited_once_with([1])
    storage.mark_ingestion_items_blocked_unhealthy.assert_awaited_once()

    queued = [
        MagicMock(id=3, payload=payload, account_ref="default"),
        MagicMock(id=4, payload=payload, account_ref="default"),
    ]
    storage.claim_ingestion_queue_items = AsyncMock(return_value=queued)
    storage.mark_ingestion_items_done = AsyncMock()
    storage.move_ingestion_item_to_dead_letter = AsyncMock()
    router.process_email = AsyncMock(  # type: ignore[assignment]
        side_effect=[
            RuntimeError("boom"),
            RouteDecision(
                mode=RouteMode.AUTO,
                route_tag=RouteTag.TASK_CANDIDATE,
                reason="ok",
                provider="google",
            ),
        ]
    )

    decisions = await router._drain_ingestion_queue(user_id=123, provider="google")  # noqa: SLF001

    assert len(decisions) == 1
    storage.move_ingestion_item_to_dead_letter.assert_awaited_once()
    storage.mark_ingestion_items_done.assert_awaited_once_with([4])


@pytest.mark.asyncio
async def test_queue_status_and_triage_failure_paths() -> None:
    storage = _StorageStub()
    storage.get_ingestion_queue_counts = AsyncMock(
        return_value={
            "pending": 2,
            "blocked_unhealthy": 3,
            "processing": 1,
            "done": 9,
        }
    )
    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
    )
    router._check_pipeline_readiness = AsyncMock(  # type: ignore[assignment]
        return_value=(False, ERROR_ROUTER_UNAVAILABLE, "router missing")
    )

    status = await router.queue_status(user_id=123, provider="google")

    assert status["pending_total"] == 6
    assert status["counts"]["done"] == 9
    assert status["error_code"] == ERROR_ROUTER_UNAVAILABLE

    def failing_post_client(timeout: float) -> _HTTPClient:
        del timeout
        return _HTTPClient(exc=RuntimeError("router down"))

    original_client = email_router_module.httpx.AsyncClient
    email_router_module.httpx.AsyncClient = failing_post_client
    try:
        with pytest.raises(ModelUnavailableError, match="AI router is unavailable") as exc_info:
            await router._triage_route_tag(_email())  # noqa: SLF001
    finally:
        email_router_module.httpx.AsyncClient = original_client

    assert exc_info.value.error_code == ERROR_ROUTER_UNAVAILABLE


@pytest.mark.asyncio
async def test_timezone_attachment_and_parsing_helpers_cover_fallback_paths() -> None:
    router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
        user_context_resolver=AsyncMock(
            side_effect=[RuntimeError("boom"), {"timezone": ""}, {"timezone": "Australia/Sydney"}]
        ),
        attachment_handling_enabled=False,
    )

    assert await router._resolve_user_timezone(123) == "UTC"  # noqa: SLF001
    assert await router._resolve_user_timezone(123) == "UTC"  # noqa: SLF001
    assert await router._resolve_user_timezone(123) == "Australia/Sydney"  # noqa: SLF001

    no_attachment_email = _email()
    no_attachment_email.metadata = {"attachment_count": 0, "attachment_filenames": []}
    assert router._has_attachments(no_attachment_email) is False  # noqa: SLF001
    assert router._attachment_filter_metadata(no_attachment_email) == {}  # noqa: SLF001

    attachment_email = _email()
    attachment_email.metadata = {
        "has_attachments": True,
        "attachment_count": "2",
        "attachment_filenames": "agenda.pdf",
    }
    assert router._has_attachments(attachment_email) is True  # noqa: SLF001
    assert router._attachment_filter_metadata(attachment_email) == {  # noqa: SLF001
        "has_attachments": True,
        "attachment_count": 0,
        "attachment_filenames": [],
        "attachment_filtered": True,
    }

    fenced = router._extract_json('```json\n{"route_tag":"ignore"}\n```')  # noqa: SLF001
    embedded = router._extract_json('prefix {"kind":"task"} suffix')  # noqa: SLF001
    assert fenced == {"route_tag": "ignore"}
    assert embedded == {"kind": "task"}
    with pytest.raises(ValueError, match="Could not parse JSON response"):
        router._extract_json("still not json")  # noqa: SLF001

    assert router._parse_datetime(None) is None  # noqa: SLF001
    assert router._parse_datetime(123) is None  # noqa: SLF001
    assert router._parse_datetime("   ") is None  # noqa: SLF001
    assert router._parse_datetime("not-a-date") is None  # noqa: SLF001
    parsed = router._parse_datetime("2026-03-18T09:00:00Z")  # noqa: SLF001
    assert parsed == datetime(2026, 3, 18, 9, 0, 0)


def test_extraction_context_falls_back_to_builtin_utc_without_tzdata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _StorageStub()
    providers = _ProvidersStub()
    security = MagicMock()
    task_calendar = _TaskCalendarStub()
    inference = MagicMock()

    class _BrokenZoneInfo:
        def __init__(self, key: str) -> None:
            raise RuntimeError(f"missing tzdata: {key}")

    monkeypatch.setattr(email_router_module, "ZoneInfo", _BrokenZoneInfo)

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
    )

    context = router._extraction_context("UTC")

    assert context["user_timezone"] == "UTC"
    assert context["current_date"]
    assert context["current_time"]
    assert context["current_datetime"]


@pytest.mark.asyncio
async def test_enrich_owner_profile_returns_early_without_storage_or_profile() -> None:
    router = EmailRouter(
        storage=_StorageStub(),
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=_TaskCalendarStub(),
        inference=MagicMock(),
    )
    await router._enrich_owner_profile(123, MagicMock())  # noqa: SLF001

    personal_storage = _make_personal_storage()
    personal_storage.get_profile.return_value = None
    router.set_personal_storage(personal_storage)

    await router._enrich_owner_profile(123, MagicMock())  # noqa: SLF001

    personal_storage.get_profile.assert_awaited_once_with(123)
    personal_storage.upsert_profile.assert_not_awaited()


# ---------------------------------------------------------------------------
# Concurrent classification + personality extraction tests
# ---------------------------------------------------------------------------


def _allow_security() -> MagicMock:
    sec = MagicMock()
    sec.analyze = AsyncMock(
        return_value=_SecResult(ThreatVerdict(action=ThreatAction.ALLOW, score=0.0, tier_reached=1))
    )
    return sec


def _make_inference_result(
    content: str,
    *,
    provider: Provider = Provider.GROQ,
    model: str = "llama-3.3-70b-versatile",
    latency_ms: float = 42.0,
) -> MagicMock:
    """Build an InferenceResult-shaped mock with required metadata fields."""
    result = MagicMock(spec=InferenceResult)
    result.content = content
    result.provider = provider
    result.model = model
    result.latency_ms = latency_ms
    return result


def _minimal_personality_json(
    *,
    author_role: str = "contact",
    author_email: str = "boss@example.com",
) -> str:
    """Build a minimal valid personality payload."""
    return json.dumps(
        {
            "author_role": author_role,
            "author_email": author_email,
            "writing_style": {},
            "communication": {},
            "relationship": {},
            "confidence": 0.7,
        }
    )


def _inference_with_classification(
    classification_json: str,
    extraction_json: str = '{"kind":"none"}',
) -> MagicMock:
    """Build inference mock that returns classification on first infer call."""
    inference = MagicMock()

    # _classify_email and _extract_personality both call inference.infer()
    # First call is classification, second is personality
    classification_result = _make_inference_result(classification_json)
    personality_result = _make_inference_result(
        _minimal_personality_json(),
        provider=Provider.GEMINI,
        model="gemini-2.5-flash",
    )

    inference.infer = AsyncMock(side_effect=[classification_result, personality_result])

    # Extraction still uses _call_ollama
    local_result = MagicMock()
    local_result.content = extraction_json
    local_result.provider.value = "ollama"
    inference._call_ollama = AsyncMock(return_value=local_result)
    inference._call_provider = AsyncMock()

    return inference


@pytest.mark.asyncio
async def test_classification_replaces_triage_route_tag() -> None:
    """When Groq classification succeeds, _triage_route_tag is NOT called."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()

    classification = {
        "category": "work_client",
        "action": "reply_normal",
        "urgency": 0.3,
        "confidence": 0.9,
        "sentiment": "neutral",
        "topics": ["roadmap"],
        "contact": {"name": "Boss", "email": "boss@example.com"},
        "reasoning": "Client email about roadmap",
    }
    inference = _inference_with_classification(json.dumps(classification))

    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    # This should NOT be called because classification succeeds
    triage_mock = AsyncMock(return_value=RouteTag.IGNORE)
    router._triage_route_tag = triage_mock  # type: ignore[assignment]

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    # Classification maps reply_normal -> reply_candidate
    assert decision.route_tag == RouteTag.REPLY_CANDIDATE
    triage_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_classification_failure_falls_back_to_triage() -> None:
    """When classification fails, falls back to _triage_route_tag."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()

    inference = MagicMock()
    # Both infer calls fail
    inference.infer = AsyncMock(side_effect=RuntimeError("Groq API down"))

    local_result = MagicMock()
    local_result.content = '{"kind":"none"}'
    local_result.provider.value = "ollama"
    inference._call_ollama = AsyncMock(return_value=local_result)

    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    triage_mock = AsyncMock(return_value=RouteTag.DIGEST_ONLY)
    router._triage_route_tag = triage_mock  # type: ignore[assignment]

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    assert decision.route_tag == RouteTag.DIGEST_ONLY
    triage_mock.assert_awaited_once()


@pytest.mark.asyncio
async def test_personality_failure_does_not_block_routing() -> None:
    """Personality extraction failure does not affect email routing."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()

    classification = {
        "category": "personal",
        "action": "archive",
        "urgency": 0.1,
        "confidence": 0.95,
        "sentiment": "neutral",
        "topics": [],
        "contact": {},
        "reasoning": "Low-priority email",
    }

    # First call (classification) succeeds, second (personality) fails
    classification_result = _make_inference_result(json.dumps(classification))

    inference = MagicMock()
    inference.infer = AsyncMock(side_effect=[classification_result, RuntimeError("Gemini timeout")])

    local_result = MagicMock()
    local_result.content = '{"kind":"none"}'
    local_result.provider.value = "ollama"
    inference._call_ollama = AsyncMock(return_value=local_result)

    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    # archive -> RouteTag.IGNORE via to_route_tag()
    assert decision.route_tag == RouteTag.IGNORE
    assert decision.mode == RouteMode.SKIP


@pytest.mark.asyncio
async def test_classification_metadata_stored_with_email() -> None:
    """Classification metadata is passed to _store_email."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()

    classification = {
        "category": "financial",
        "action": "action_required",
        "urgency": 0.7,
        "confidence": 0.85,
        "sentiment": "neutral",
        "topics": ["tax", "hmrc"],
        "contact": {"name": "HMRC", "email": "noreply@hmrc.gov.uk"},
        "reasoning": "Tax return deadline",
    }
    inference = _inference_with_classification(json.dumps(classification))

    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )

    await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    store_call = storage.store_email_message.await_args
    metadata = store_call.kwargs["metadata"]
    assert metadata["classification_category"] == "financial"
    assert metadata["classification_action"] == "action_required"
    assert metadata["classification_urgency"] == 0.7
    assert "tax" in metadata["classification_topics"]


@pytest.mark.asyncio
async def test_persist_signals_called_with_personal_storage() -> None:
    """When PersonalStorage is set, _persist_signals fires as background task."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()

    classification = {
        "category": "work_colleague",
        "action": "read_only",
        "urgency": 0.1,
        "confidence": 0.9,
        "sentiment": "positive",
        "topics": ["standup"],
        "contact": {"name": "Sarah Chen", "email": "sarah@example.com", "company": "Acme"},
        "reasoning": "Team standup notes",
    }
    inference = _inference_with_classification(json.dumps(classification))

    personal_storage = MagicMock()
    personal_storage.upsert_contact = AsyncMock(return_value=1)
    personal_storage.increment_contact_interaction = AsyncMock()
    personal_storage.add_learning = AsyncMock(return_value=1)

    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    router.set_personal_storage(personal_storage)

    await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    # Give the background task a chance to complete
    import asyncio

    await asyncio.sleep(0.1)

    personal_storage.upsert_contact.assert_awaited_once()
    personal_storage.increment_contact_interaction.assert_awaited_once()
    contact_call = personal_storage.upsert_contact.await_args.args[0]
    assert contact_call.contact_email == "sarah@example.com"
    assert contact_call.user_id == 123


@pytest.mark.asyncio
async def test_persist_signals_stores_personality_preferences_and_schedule() -> None:
    """_persist_signals persists preferences_revealed and schedule_signals as learnings."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()

    classification = {
        "category": "personal",
        "action": "read_only",
        "urgency": 0.1,
        "confidence": 0.9,
        "sentiment": "positive",
        "topics": ["coffee"],
        "contact": {"name": "Dan", "email": "dan@example.com"},
        "reasoning": "Casual chat",
    }

    # Build personality with preferences and schedule signals
    personality_json = {
        "author_role": "contact",
        "author_name": "Dan",
        "author_email": "dan@example.com",
        "writing_style": {},
        "communication": {},
        "relationship": {},
        "preferences_revealed": ["prefers morning meetings", "likes dark roast coffee"],
        "schedule_signals": ["works late evenings", "unavailable on Fridays"],
    }

    classification_result = _make_inference_result(json.dumps(classification))
    personality_result = _make_inference_result(
        json.dumps(personality_json),
        provider=Provider.GEMINI,
        model="gemini-2.5-flash",
    )

    inference = MagicMock()
    inference.infer = AsyncMock(side_effect=[classification_result, personality_result])

    local_result = MagicMock()
    local_result.content = '{"kind":"none"}'
    local_result.provider.value = "ollama"
    inference._call_ollama = AsyncMock(return_value=local_result)

    personal_storage = MagicMock()
    personal_storage.upsert_contact = AsyncMock(return_value=1)
    personal_storage.increment_contact_interaction = AsyncMock()
    personal_storage.add_learning = AsyncMock(return_value=1)

    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    router.set_personal_storage(personal_storage)

    await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    import asyncio

    await asyncio.sleep(0.1)

    # 2 preferences + 2 schedule signals = 4 add_learning calls
    assert personal_storage.add_learning.await_count == 4

    # Check learning categories
    calls = personal_storage.add_learning.await_args_list
    categories = [c.args[0].category.value for c in calls]
    assert categories.count("preference") == 2
    assert categories.count("schedule") == 2

    # Check content
    contents = [c.args[0].content for c in calls]
    assert "prefers morning meetings" in contents
    assert "works late evenings" in contents


@pytest.mark.asyncio
async def test_persist_signals_noop_without_personal_storage() -> None:
    """_persist_signals returns early when no PersonalStorage is configured."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()

    classification = {
        "category": "personal",
        "action": "archive",
        "urgency": 0.1,
        "confidence": 0.9,
        "sentiment": "neutral",
        "topics": [],
        "contact": {"name": "X", "email": "x@example.com"},
        "reasoning": "Test",
    }
    inference = _inference_with_classification(json.dumps(classification))

    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    # Do NOT call set_personal_storage — it should be None

    await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    import asyncio

    await asyncio.sleep(0.1)

    # No crash, no personal storage calls — smoke test


@pytest.mark.asyncio
async def test_classify_email_returns_none_on_parse_failure() -> None:
    """_classify_email returns None when JSON parsing fails."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()

    # Classification returns unparseable text, personality returns valid
    classification_result = _make_inference_result("NOT VALID JSON AT ALL")
    personality_result = _make_inference_result(
        _minimal_personality_json(),
        provider=Provider.GEMINI,
        model="gemini-2.5-flash",
    )

    inference = MagicMock()
    inference.infer = AsyncMock(side_effect=[classification_result, personality_result])

    local_result = MagicMock()
    local_result.content = '{"kind":"none"}'
    local_result.provider.value = "ollama"
    inference._call_ollama = AsyncMock(return_value=local_result)

    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    triage_mock = AsyncMock(return_value=RouteTag.DIGEST_ONLY)
    router._triage_route_tag = triage_mock  # type: ignore[assignment]

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    # Classification parse failure → fallback to triage
    assert decision.route_tag == RouteTag.DIGEST_ONLY
    triage_mock.assert_awaited_once()


def test_to_route_tag_maps_all_actions() -> None:
    """EmailClassification.to_route_tag() maps all actions to valid RouteTag values."""
    action_tag_pairs = [
        (EmailAction.REPLY_URGENT, "reply_candidate"),
        (EmailAction.REPLY_NORMAL, "reply_candidate"),
        (EmailAction.ACTION_REQUIRED, "task_candidate"),
        (EmailAction.CREATE_TASK, "task_candidate"),
        (EmailAction.CREATE_EVENT, "calendar_candidate"),
        (EmailAction.READ_ONLY, "digest_only"),
        (EmailAction.ARCHIVE, "ignore"),
        (EmailAction.IGNORE, "ignore"),
    ]
    for action, expected_tag in action_tag_pairs:
        c = EmailClassification(action=action)
        assert c.to_route_tag() == expected_tag, f"{action} should map to {expected_tag}"
        # Verify the tag is a valid RouteTag
        RouteTag(c.to_route_tag())


# ---------------------------------------------------------------------------
# Full personality persistence tests
# ---------------------------------------------------------------------------


def _full_personality_json(
    *,
    author_role: str = "contact",
    author_email: str = "boss@example.com",
    commitments: list[str] | None = None,
    expectations: list[str] | None = None,
) -> dict[str, Any]:
    """Build a complete personality signal dict."""
    return {
        "author_role": author_role,
        "author_name": "Boss Person",
        "author_email": author_email,
        "writing_style": {
            "formality": "formal",
            "avg_sentence_length": "medium",
            "uses_greeting": True,
            "greeting_style": "Hello,",
            "uses_signoff": True,
            "signoff_style": "Best regards,",
            "uses_emoji": False,
            "uses_bullet_points": True,
            "vocabulary_level": "standard",
        },
        "communication": {
            "primary_trait": "direct",
            "emotional_tone": "neutral",
            "assertiveness": 0.7,
        },
        "relationship": {
            "familiarity": 0.6,
            "power_dynamic": "superior",
            "trust_level": 0.7,
        },
        "preferences_revealed": ["prefers morning meetings"],
        "schedule_signals": ["works late evenings"],
        "commitments_made": commitments or [],
        "expectations_set": expectations or [],
        "confidence": 0.8,
    }


def _full_inference(
    classification_json: str,
    personality_json: dict[str, Any],
) -> MagicMock:
    """Build inference mock with classification + personality results."""
    inference = MagicMock()

    classification_result = _make_inference_result(classification_json)
    personality_result = _make_inference_result(
        json.dumps(personality_json),
        provider=Provider.GEMINI,
        model="gemini-2.5-flash",
    )

    inference.infer = AsyncMock(side_effect=[classification_result, personality_result])

    local_result = MagicMock()
    local_result.content = '{"kind":"none"}'
    local_result.provider.value = "ollama"
    inference._call_ollama = AsyncMock(return_value=local_result)
    inference._call_provider = AsyncMock()

    return inference


def _classification_json(**overrides: Any) -> str:
    """Build a classification JSON string."""
    data = {
        "category": "work_colleague",
        "action": "read_only",
        "urgency": 0.1,
        "confidence": 0.9,
        "sentiment": "neutral",
        "topics": ["standup"],
        "contact": {"name": "Boss Person", "email": "boss@example.com"},
        "reasoning": "Team update",
    }
    data.update(overrides)
    return json.dumps(data)


def _make_personal_storage() -> MagicMock:
    """Build a mock PersonalStorage with all required methods."""
    ps = MagicMock()
    ps.upsert_contact = AsyncMock(return_value=1)
    ps.increment_contact_interaction = AsyncMock()
    ps.add_learning = AsyncMock(return_value=1)
    ps.log_personality_signal = AsyncMock(return_value=1)
    ps.get_personality_profile = AsyncMock(return_value=None)
    ps.upsert_personality_profile = AsyncMock(return_value=1)
    ps.get_profile = AsyncMock(return_value=None)
    ps.upsert_profile = AsyncMock()
    return ps


@pytest.mark.asyncio
async def test_persist_signals_logs_full_signal() -> None:
    """_persist_signals calls log_personality_signal with complete data."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()
    personality = _full_personality_json()
    inference = _full_inference(_classification_json(), personality)

    personal_storage = _make_personal_storage()
    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    router.set_personal_storage(personal_storage)

    await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    import asyncio

    await asyncio.sleep(0.15)

    personal_storage.log_personality_signal.assert_awaited_once()
    call_kwargs = personal_storage.log_personality_signal.await_args.kwargs
    assert call_kwargs["user_id"] == 123
    assert call_kwargs["author_role"] == "contact"
    assert call_kwargs["author_email"] == "boss@example.com"
    assert "writing_style" in call_kwargs["signal_data"]


@pytest.mark.asyncio
async def test_persist_signals_aggregates_contact_profile() -> None:
    """_persist_signals performs get → aggregate → upsert cycle for contact."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()
    personality = _full_personality_json()
    inference = _full_inference(_classification_json(), personality)

    personal_storage = _make_personal_storage()
    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    router.set_personal_storage(personal_storage)

    await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    import asyncio

    await asyncio.sleep(0.15)

    personal_storage.get_personality_profile.assert_awaited_once_with(
        123, "boss@example.com", "contact"
    )
    personal_storage.upsert_personality_profile.assert_awaited_once()
    upserted = personal_storage.upsert_personality_profile.await_args.args[0]
    assert upserted.subject_email == "boss@example.com"
    assert upserted.subject_role == "contact"
    assert upserted.observation_count == 1


@pytest.mark.asyncio
async def test_persist_signals_aggregates_owner_profile() -> None:
    """_persist_signals aggregates owner profile when author_role=owner."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()
    personality = _full_personality_json(author_role="owner", author_email="me@example.com")
    inference = _full_inference(_classification_json(), personality)

    personal_storage = _make_personal_storage()
    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    router.set_personal_storage(personal_storage)

    await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    import asyncio

    await asyncio.sleep(0.15)

    personal_storage.get_personality_profile.assert_awaited_once_with(
        123, "me@example.com", "owner"
    )
    personal_storage.upsert_personality_profile.assert_awaited_once()
    upserted = personal_storage.upsert_personality_profile.await_args.args[0]
    assert upserted.subject_role == "owner"


@pytest.mark.asyncio
async def test_persist_signals_stores_commitments_as_learnings() -> None:
    """_persist_signals stores commitments and expectations as fact learnings."""
    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()
    personality = _full_personality_json(
        commitments=["deliver by Friday"],
        expectations=["send report by Monday"],
    )
    inference = _full_inference(_classification_json(), personality)

    personal_storage = _make_personal_storage()
    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    router.set_personal_storage(personal_storage)

    await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    import asyncio

    await asyncio.sleep(0.15)

    calls = personal_storage.add_learning.await_args_list
    contents = [c.args[0].content for c in calls]
    categories = [c.args[0].category.value for c in calls]

    # Preferences + schedule + commitment + expectation
    assert any("[commitment:" in c for c in contents)
    assert any("[expectation:" in c for c in contents)
    assert categories.count("fact") >= 2


@pytest.mark.asyncio
async def test_persist_signals_enriches_owner_communication_style() -> None:
    """_persist_signals enriches owner profile after 3+ observations."""
    from zetherion_ai.personal.models import (
        AggregatedCommunication,
        AggregatedWritingStyle,
        CommunicationStyle,
        PersonalProfile,
    )
    from zetherion_ai.personal.models import (
        PersonalityProfile as PersonalityProfileModel,
    )

    storage = _StorageStub()
    task_calendar = _TaskCalendarStub()
    personality = _full_personality_json(author_role="owner", author_email="me@example.com")
    inference = _full_inference(_classification_json(), personality)

    # Return an existing profile with 2 observations so new one will be 3
    existing_profile = PersonalityProfileModel(
        user_id=123,
        subject_email="me@example.com",
        subject_role="owner",
        observation_count=2,
        writing_style=AggregatedWritingStyle(
            formality_distribution={"formal": 2},
            formality_mode="formal",
        ),
        communication=AggregatedCommunication(
            primary_trait_distribution={"direct": 2},
        ),
    )

    user_profile = PersonalProfile(
        user_id=123,
        display_name="Test User",
        communication_style=CommunicationStyle(
            formality=0.5,
            verbosity=0.5,
            emoji_usage=0.0,
        ),
    )

    personal_storage = _make_personal_storage()
    personal_storage.get_personality_profile.return_value = existing_profile
    personal_storage.get_profile.return_value = user_profile

    router = EmailRouter(
        storage=storage,
        providers=_ProvidersStub(),
        security=_allow_security(),
        task_calendar_router=task_calendar,
        inference=inference,
    )
    router.set_personal_storage(personal_storage)

    await router.process_email(
        user_id=123,
        provider="google",
        account_ref="me@example.com",
        email=_email(),
    )

    import asyncio

    await asyncio.sleep(0.15)

    # Should call upsert_profile (enrichment)
    personal_storage.upsert_profile.assert_awaited_once()
    enriched = personal_storage.upsert_profile.await_args.args[0]
    # formality should be blended toward formal (0.8)
    assert enriched.communication_style.formality > 0.5


@pytest.mark.asyncio
async def test_blocked_email_records_canonical_trust_audit(monkeypatch) -> None:
    storage = _StorageStub()
    providers = _ProvidersStub()
    security = MagicMock()
    security.analyze = AsyncMock(
        return_value=_SecResult(ThreatVerdict(action=ThreatAction.BLOCK, score=0.9, tier_reached=2))
    )
    task_calendar = _TaskCalendarStub()
    inference = MagicMock()
    recorded = AsyncMock()
    monkeypatch.setattr(
        "zetherion_ai.routing.email_router.record_routing_trust_decision",
        recorded,
    )
    trust_storage = object()

    router = EmailRouter(
        storage=storage,
        providers=providers,
        security=security,
        task_calendar_router=task_calendar,
        inference=inference,
        trust_storage=trust_storage,
    )

    decision = await router.process_email(
        user_id=123,
        provider="google",
        account_ref="default",
        email=_email(),
    )

    assert decision.mode == RouteMode.BLOCK
    recorded.assert_awaited_once_with(
        trust_storage,
        user_id=123,
        action="routing.email.process",
        source_type="email",
        decision=decision,
        source_system="email_router",
    )
