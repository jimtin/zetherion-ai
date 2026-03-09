"""Unit tests for the tenant sandbox runtime."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from zetherion_ai.api.conversation_runtime import TenantConversationContext, TenantSubjectMemory
from zetherion_ai.api.test_runtime import SandboxSimulationError, TenantSandboxRuntime


def _context(
    *,
    history: list[dict[str, str]] | None = None,
    session_summary: str = "",
    subject_memories: list[TenantSubjectMemory] | None = None,
) -> TenantConversationContext:
    return TenantConversationContext(
        history=history or [],
        memory_subject_id="visitor-1",
        session_summary=session_summary,
        subject_memories=subject_memories or [],
        context_notes=None,
    )


@pytest.mark.asyncio
async def test_preview_returns_follow_up_fallback_with_subject_style() -> None:
    tenant_manager = AsyncMock()
    tenant_manager.resolve_test_profile = AsyncMock(
        return_value={"profile_id": "profile-1", "tenant_id": "tenant-1"}
    )
    tenant_manager.list_test_rules = AsyncMock(return_value=[])
    runtime = TenantSandboxRuntime(tenant_manager=tenant_manager)

    preview = await runtime.preview(
        tenant_id="tenant-1",
        profile_id="profile-1",
        method="POST",
        route_path="/api/v1/chat",
        body={"message": "What should we do next?"},
        session={"conversation_summary": "Asked about pricing earlier"},
        context=_context(
            session_summary="Asked about pricing earlier",
            subject_memories=[
                TenantSubjectMemory(category="identity", memory_key="name", value="Ava"),
                TenantSubjectMemory(category="preference", memory_key="response_style", value="brief"),
            ],
        ),
        history=[],
    )

    assert preview["preset_id"] == "follow_up"
    assert preview["matched_rule_id"] is None
    assert preview["chat_result"]["model"] == "sandbox-simulated"
    assert str(preview["chat_result"]["content"]).startswith("Ava,")
    assert preview["stream_events"][-1]["type"] == "done"


@pytest.mark.asyncio
async def test_simulate_chat_matches_rule_with_metadata_tool_and_ongoing_state() -> None:
    tenant_manager = AsyncMock()
    tenant_manager.resolve_test_profile = AsyncMock(
        return_value={"profile_id": "profile-1", "tenant_id": "tenant-1"}
    )
    tenant_manager.list_test_rules = AsyncMock(
        return_value=[
            {
                "rule_id": "rule-1",
                "profile_id": "profile-1",
                "method": "POST",
                "route_pattern": "/api/v1/chat",
                "enabled": True,
                "priority": 10,
                "match": {
                    "body_contains": ["availability"],
                    "metadata_contains": {"channel": "web"},
                    "tool_name": "calendar.lookup",
                    "conversation_state": "ongoing",
                },
                "response": {
                    "preset_id": "availability",
                    "json_body": {
                        "content": "Simulated availability reply",
                        "model": "sandbox-custom",
                    },
                },
                "latency_ms": 0,
            }
        ]
    )
    runtime = TenantSandboxRuntime(tenant_manager=tenant_manager)

    result = await runtime.simulate_chat(
        tenant_id="tenant-1",
        profile_id="profile-1",
        body={
            "message": "Can you check availability tomorrow?",
            "metadata": {"channel": "web", "tool_name": "calendar.lookup"},
        },
        session={},
        context=_context(history=[{"role": "user", "content": "Earlier"}]),
        history=[{"role": "user", "content": "Earlier"}],
    )

    assert result.content == "Simulated availability reply"
    assert result.model == "sandbox-custom"
    assert result.metadata["sandbox_rule_id"] == "rule-1"
    assert result.metadata["sandbox_profile_id"] == "profile-1"
    assert result.metadata["sandbox_preset_id"] == "availability"


@pytest.mark.asyncio
async def test_simulate_stream_uses_configured_sse_events_and_appends_done() -> None:
    tenant_manager = AsyncMock()
    tenant_manager.resolve_test_profile = AsyncMock(
        return_value={"profile_id": "profile-1", "tenant_id": "tenant-1"}
    )
    tenant_manager.list_test_rules = AsyncMock(
        return_value=[
            {
                "rule_id": "rule-1",
                "profile_id": "profile-1",
                "method": "POST",
                "route_pattern": "/api/v1/chat/stream",
                "enabled": True,
                "priority": 1,
                "match": {"body_contains": ["stream"]},
                "response": {
                    "sse_events": [
                        {"type": "token", "content": "stream "},
                        {"type": "token", "content": "reply"},
                    ]
                },
                "latency_ms": 0,
            }
        ]
    )
    runtime = TenantSandboxRuntime(tenant_manager=tenant_manager)

    chat_result, events = await runtime.simulate_stream(
        tenant_id="tenant-1",
        profile_id="profile-1",
        body={"message": "stream this please"},
        session={},
        context=_context(),
        history=[],
    )

    assert chat_result.content == "stream reply"
    assert chat_result.model == "sandbox-simulated"
    assert events[-1] == {"type": "done", "model": "sandbox-simulated"}


@pytest.mark.asyncio
async def test_simulate_chat_error_and_preview_error_are_structured() -> None:
    tenant_manager = AsyncMock()
    tenant_manager.resolve_test_profile = AsyncMock(
        return_value={"profile_id": "profile-1", "tenant_id": "tenant-1"}
    )
    tenant_manager.list_test_rules = AsyncMock(
        return_value=[
            {
                "rule_id": "rule-1",
                "profile_id": "profile-1",
                "method": "POST",
                "route_pattern": "/api/v1/chat",
                "enabled": True,
                "priority": 1,
                "match": {"body_contains": ["error"]},
                "response": {"error": {"status": 418, "body": {"error": "simulated failure"}}},
                "latency_ms": 0,
            }
        ]
    )
    runtime = TenantSandboxRuntime(tenant_manager=tenant_manager)

    with pytest.raises(SandboxSimulationError) as exc:
        await runtime.simulate_chat(
            tenant_id="tenant-1",
            profile_id="profile-1",
            body={"message": "trigger error"},
            session={},
            context=_context(),
            history=[],
        )

    assert exc.value.status == 418
    assert exc.value.body == {"error": "simulated failure"}

    preview = await runtime.preview(
        tenant_id="tenant-1",
        profile_id="profile-1",
        method="POST",
        route_path="/api/v1/chat",
        body={"message": "trigger error"},
        session={},
        context=_context(),
        history=[],
    )
    assert preview["error"] == {"status": 418, "body": {"error": "simulated failure"}}


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("Can I get pricing?", "pricing"),
        ("What hours are you open?", "availability"),
        ("Can I book this?", "booking"),
        ("This leak is urgent", "urgent_support"),
        ("Hello there", "default"),
    ],
)
def test_default_preset_selection_by_message_keyword(message: str, expected: str) -> None:
    runtime = TenantSandboxRuntime(tenant_manager=AsyncMock())
    assert runtime._default_preset_id({"message": message}, context=None, history=[]) == expected
