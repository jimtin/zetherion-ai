"""Optional e2e tests for Groq-first inbound rollout across channels."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from zetherion_ai.agent.providers import Provider, TaskType
from zetherion_ai.agent.router import GroqRouterBackend, MessageIntent, MessageRouter
from zetherion_ai.personal.context import DecisionContextBuilder
from zetherion_ai.routing.email_router import EmailRouter
from zetherion_ai.routing.models import NormalizedEmail
from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.email import INTENT_ROUTE, EmailSkill

from .email_rollout_helpers import (
    AllowSecurityPipeline,
    ChatInferenceStub,
    InferenceBrokerStub,
    InMemoryIntegrationStorage,
    InMemoryPersonalStorage,
    ProvidersWithEmailStub,
    TaskCalendarRouterStub,
    make_unread_message,
)


@pytest.mark.asyncio
@pytest.mark.optional_e2e
async def test_email_skill_e2e_cloud_first_persists_personality_context() -> None:
    """Email skill flow should ingest, route, persist, then expose context data."""
    messages = [
        make_unread_message(
            account_ref="42",
            account_email="owner@example.com",
            external_id="msg-e2e-1",
            from_email="boss@example.com",
            subject="Weekly check-in",
            body_preview="Please review this by Friday.",
        )
    ]
    providers = ProvidersWithEmailStub(messages)
    integration_storage = InMemoryIntegrationStorage()
    personal_storage = InMemoryPersonalStorage()
    inference = InferenceBrokerStub(extraction_provider=Provider.GEMINI)

    router = EmailRouter(
        storage=integration_storage,
        providers=providers,
        security=AllowSecurityPipeline(),
        task_calendar_router=TaskCalendarRouterStub(),
        inference=inference,
        local_extraction_required=False,
    )
    router.set_personal_storage(personal_storage)

    skill = EmailSkill(router=router, storage=integration_storage, providers=providers)

    response = await skill.handle(
        SkillRequest(
            user_id="21",
            intent=INTENT_ROUTE,
            context={"provider": "google", "limit": 1},
        )
    )

    assert response.success is True
    assert response.data["count"] == 1
    assert response.data["provider"] == "google"

    await asyncio.sleep(0)
    context = await DecisionContextBuilder(personal_storage).build(
        21,
        mentioned_emails=["boss@example.com"],
    )
    assert context.contact_personalities
    assert context.contact_personalities[0]["subject_email"] == "boss@example.com"


@pytest.mark.asyncio
@pytest.mark.optional_e2e
async def test_chat_router_e2e_uses_groq_backend_classification() -> None:
    """Chat classification path should run through GroqRouterBackend."""
    inference = ChatInferenceStub()
    router = MessageRouter(GroqRouterBackend(inference=inference))

    decision = await router.classify("Hey there")

    assert decision.intent == MessageIntent.SIMPLE_QUERY
    assert decision.confidence == pytest.approx(0.95)
    assert inference.calls
    assert inference.calls[0][0] == TaskType.CLASSIFICATION


@pytest.mark.asyncio
@pytest.mark.optional_e2e
async def test_multi_channel_e2e_has_no_local_model_dependency_in_cloud_mode() -> None:
    """Email + chat ingress should work when only cloud providers are present."""
    integration_storage = InMemoryIntegrationStorage()
    personal_storage = InMemoryPersonalStorage()
    email_inference = InferenceBrokerStub(
        extraction_provider=Provider.GEMINI,
        available_providers={Provider.GROQ, Provider.GEMINI},
    )

    email_router = EmailRouter(
        storage=integration_storage,
        providers=ProvidersWithEmailStub([]),
        security=AllowSecurityPipeline(),
        task_calendar_router=TaskCalendarRouterStub(),
        inference=email_inference,
        local_extraction_required=False,
    )
    email_router.set_personal_storage(personal_storage)

    ready, error_code, error_detail = await email_router._check_pipeline_readiness()
    assert ready is True
    assert error_code is None
    assert error_detail is None

    email_decision = await email_router.process_email(
        user_id=33,
        provider="google",
        account_ref="3344",
        email=NormalizedEmail(
            external_id="msg-multi-1",
            thread_id="thr-multi-1",
            subject="FYI",
            body_text="No action needed.",
            from_email="boss@example.com",
            to_emails=["owner@example.com"],
            received_at=datetime.now(),
            metadata={"account_email": "owner@example.com"},
        ),
    )
    assert email_decision.metadata["extractor_provider"] == "gemini"

    chat_inference = ChatInferenceStub()
    chat_router = MessageRouter(GroqRouterBackend(inference=chat_inference))
    chat_decision = await chat_router.classify("What time is it?")

    assert chat_decision.intent == MessageIntent.SIMPLE_QUERY
    assert any(task == TaskType.DATA_EXTRACTION for task, _ in email_inference.calls)
