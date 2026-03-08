"""Unit tests for tenant API conversation runtime helpers."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zetherion_ai.api.conversation_runtime import TenantConversationRuntime


@pytest.fixture
def tenant_manager() -> AsyncMock:
    manager = AsyncMock()
    manager.list_subject_memories = AsyncMock(return_value=[])
    manager.upsert_subject_memory = AsyncMock()
    manager.persist_session_context = AsyncMock()
    return manager


def test_resolve_memory_subject_id_prefers_explicit_value(tenant_manager: AsyncMock) -> None:
    runtime = TenantConversationRuntime(tenant_manager=tenant_manager)
    assert (
        runtime.resolve_memory_subject_id(
            {
                "memory_subject_id": "subject-1",
                "external_user_id": "external-1",
            }
        )
        == "subject-1"
    )


def test_resolve_memory_subject_id_falls_back_to_external_user_id(
    tenant_manager: AsyncMock,
) -> None:
    runtime = TenantConversationRuntime(tenant_manager=tenant_manager)
    assert runtime.resolve_memory_subject_id({"external_user_id": "external-1"}) == "external-1"


def test_extract_memory_candidates_finds_style_name_and_favorite(
    tenant_manager: AsyncMock,
) -> None:
    runtime = TenantConversationRuntime(tenant_manager=tenant_manager)
    candidates = runtime.extract_memory_candidates(
        "My name is Jamie. Please keep responses brief. My favorite color is teal."
    )
    indexed = {
        (candidate.category, candidate.memory_key): candidate.value for candidate in candidates
    }
    assert indexed[("identity", "name")] == "Jamie"
    assert indexed[("preference", "response_style")] == "brief"
    assert indexed[("preference", "favorite_color")] == "teal"


@pytest.mark.asyncio
async def test_build_context_includes_summary_and_subject_memories(
    tenant_manager: AsyncMock,
) -> None:
    tenant_manager.list_subject_memories.return_value = [
        {
            "category": "preference",
            "memory_key": "response_style",
            "value": "brief",
            "confidence": 0.88,
        }
    ]
    runtime = TenantConversationRuntime(tenant_manager=tenant_manager)

    context = await runtime.build_context(
        tenant_id="tenant-1",
        session={
            "memory_subject_id": "subject-1",
            "conversation_summary": "Recent user requests: asked about pricing",
        },
        history=[{"role": "assistant", "content": "Hi there"}],
    )

    assert context.memory_subject_id == "subject-1"
    assert "Recent user requests" in str(context.context_notes)
    assert "response style: brief" in str(context.context_notes)
    tenant_manager.list_subject_memories.assert_awaited_once_with(
        tenant_id="tenant-1",
        memory_subject_id="subject-1",
        limit=6,
    )


@pytest.mark.asyncio
async def test_record_turn_persists_subject_memory_and_summary(tenant_manager: AsyncMock) -> None:
    runtime = TenantConversationRuntime(tenant_manager=tenant_manager)

    await runtime.record_turn(
        tenant_id="tenant-1",
        session={"memory_subject_id": "subject-1"},
        session_id="session-1",
        user_message="Please keep responses brief.",
        assistant_message="Understood, I'll keep it brief.",
        history=[
            {"role": "user", "content": "Please keep responses brief."},
            {"role": "assistant", "content": "Understood, I'll keep it brief."},
        ],
    )

    tenant_manager.upsert_subject_memory.assert_awaited_once_with(
        tenant_id="tenant-1",
        memory_subject_id="subject-1",
        category="preference",
        memory_key="response_style",
        value="brief",
        source_session_id="session-1",
        confidence=0.88,
    )
    tenant_manager.persist_session_context.assert_awaited_once()
    call = tenant_manager.persist_session_context.await_args.kwargs
    assert call["tenant_id"] == "tenant-1"
    assert call["session_id"] == "session-1"
    assert call["memory_subject_id"] == "subject-1"
    assert "Recent user requests" in call["conversation_summary"]
