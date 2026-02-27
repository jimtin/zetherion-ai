"""Integration tests for email personality persistence and context round-trip."""

from __future__ import annotations

import asyncio
from datetime import datetime

import pytest

from zetherion_ai.agent.providers import Provider, TaskType
from zetherion_ai.personal.context import DecisionContextBuilder
from zetherion_ai.routing.email_router import EmailRouter
from zetherion_ai.routing.models import NormalizedEmail

from .email_rollout_helpers import (
    AllowSecurityPipeline,
    InferenceBrokerStub,
    InMemoryIntegrationStorage,
    InMemoryPersonalStorage,
    ProvidersWithEmailStub,
    TaskCalendarRouterStub,
)

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_persists_owner_profile_when_account_ref_is_numeric() -> None:
    """Owner role detection should use account_email even when account_ref is numeric."""
    storage = InMemoryIntegrationStorage()
    personal_storage = InMemoryPersonalStorage()
    inference = InferenceBrokerStub()

    router = EmailRouter(
        storage=storage,
        providers=ProvidersWithEmailStub([]),
        security=AllowSecurityPipeline(),
        task_calendar_router=TaskCalendarRouterStub(),
        inference=inference,
        local_extraction_required=False,
    )
    router.set_personal_storage(personal_storage)

    email = NormalizedEmail(
        external_id="msg-owner-1",
        thread_id="thr-owner-1",
        subject="Weekly status",
        body_text="I will send a recap before Friday.",
        from_email="owner@example.com",
        to_emails=["teammate@example.com"],
        received_at=datetime.now(),
        metadata={"account_email": "owner@example.com"},
    )

    decision = await router.process_email(
        user_id=7,
        provider="google",
        account_ref="123456789",
        email=email,
    )

    assert decision.metadata["classification_provider"] == "groq"
    assert decision.metadata["personality_provider"] == "groq"
    await asyncio.sleep(0)

    assert personal_storage.signal_log
    assert personal_storage.signal_log[0]["author_role"] == "owner"

    owner_profile = await personal_storage.get_personality_profile(
        7,
        "owner@example.com",
        "owner",
    )
    assert owner_profile is not None
    assert owner_profile.subject_role == "owner"


@pytest.mark.asyncio
async def test_context_builder_round_trip_after_email_ingest() -> None:
    """Persisted personality profiles should flow through DecisionContextBuilder."""
    storage = InMemoryIntegrationStorage()
    personal_storage = InMemoryPersonalStorage()
    inference = InferenceBrokerStub()

    router = EmailRouter(
        storage=storage,
        providers=ProvidersWithEmailStub([]),
        security=AllowSecurityPipeline(),
        task_calendar_router=TaskCalendarRouterStub(),
        inference=inference,
        local_extraction_required=False,
    )
    router.set_personal_storage(personal_storage)

    owner_email = NormalizedEmail(
        external_id="msg-owner-2",
        thread_id="thr-owner-2",
        subject="Owner note",
        body_text="I prefer concise updates.",
        from_email="owner@example.com",
        to_emails=["boss@example.com"],
        received_at=datetime.now(),
        metadata={"account_email": "owner@example.com"},
    )
    contact_email = NormalizedEmail(
        external_id="msg-contact-1",
        thread_id="thr-contact-1",
        subject="Client follow-up",
        body_text="Please review before Friday.",
        from_email="boss@example.com",
        to_emails=["owner@example.com"],
        received_at=datetime.now(),
        metadata={"account_email": "owner@example.com"},
    )

    await router.process_email(
        user_id=9,
        provider="google",
        account_ref="9999",
        email=owner_email,
    )
    await router.process_email(
        user_id=9,
        provider="google",
        account_ref="9999",
        email=contact_email,
    )
    await asyncio.sleep(0)

    context = await DecisionContextBuilder(personal_storage).build(
        9,
        mentioned_emails=["boss@example.com"],
    )

    assert context.owner_personality["subject_email"] == "owner@example.com"
    assert context.owner_personality["subject_role"] == "owner"
    assert len(context.contact_personalities) == 1
    assert context.contact_personalities[0]["subject_email"] == "boss@example.com"


@pytest.mark.asyncio
async def test_cloud_first_metadata_records_fallback_provider() -> None:
    """When extraction lands on a fallback cloud provider, metadata should reflect it."""
    storage = InMemoryIntegrationStorage()
    personal_storage = InMemoryPersonalStorage()
    inference = InferenceBrokerStub(extraction_provider=Provider.GEMINI)

    router = EmailRouter(
        storage=storage,
        providers=ProvidersWithEmailStub([]),
        security=AllowSecurityPipeline(),
        task_calendar_router=TaskCalendarRouterStub(),
        inference=inference,
        local_extraction_required=False,
    )
    router.set_personal_storage(personal_storage)

    email = NormalizedEmail(
        external_id="msg-fallback-1",
        thread_id="thr-fallback-1",
        subject="FYI",
        body_text="No action needed.",
        from_email="boss@example.com",
        to_emails=["owner@example.com"],
        received_at=datetime.now(),
        metadata={"account_email": "owner@example.com"},
    )

    decision = await router.process_email(
        user_id=11,
        provider="google",
        account_ref="1001",
        email=email,
    )

    assert decision.metadata["classification_provider"] == "groq"
    assert decision.metadata["extractor_provider"] == "gemini"
    assert any(task == TaskType.DATA_EXTRACTION for task, _ in inference.calls)
