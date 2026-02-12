"""Tests for client_chat skill — L1a detection and chat response generation."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.client_chat import (
    ClientChatSkill,
    L1aSignals,
    build_system_prompt,
    detect_signals,
)

# ---------------------------------------------------------------------------
# L1a signal detection
# ---------------------------------------------------------------------------


class TestDetectSignals:
    def test_no_signals(self) -> None:
        signals = detect_signals("Hi, I'd like to book a bathroom renovation")
        assert not signals.is_urgent
        assert not signals.is_safety_concern
        assert not signals.needs_escalation
        assert not signals.has_signals

    def test_urgency_detection(self) -> None:
        signals = detect_signals("My pipe burst and water is flooding my kitchen!")
        assert signals.is_urgent
        assert signals.has_signals
        assert any("urgency" in p for p in signals.matched_patterns)

    def test_urgency_emergency(self) -> None:
        signals = detect_signals("This is an emergency, I need help immediately")
        assert signals.is_urgent

    def test_safety_concern(self) -> None:
        signals = detect_signals("I want to harm myself")
        assert signals.is_safety_concern
        assert signals.has_signals
        assert any("safety" in p for p in signals.matched_patterns)

    def test_escalation_request(self) -> None:
        signals = detect_signals("I want to speak to a real person please")
        assert signals.needs_escalation
        assert signals.has_signals
        assert any("escalation" in p for p in signals.matched_patterns)

    def test_escalation_talk_to_human(self) -> None:
        signals = detect_signals("Can I talk to a human agent?")
        assert signals.needs_escalation

    def test_returning_customer(self) -> None:
        signals = detect_signals("Last time we spoke you mentioned a discount")
        assert signals.is_returning
        assert any("returning" in p for p in signals.matched_patterns)

    def test_multiple_signals(self) -> None:
        signals = detect_signals("This is urgent! I want to speak to a real person right now!")
        assert signals.is_urgent
        assert signals.needs_escalation

    def test_no_heat(self) -> None:
        signals = detect_signals("I have no heating and it's freezing")
        assert signals.is_urgent

    def test_to_dict(self) -> None:
        signals = detect_signals("urgent help me!")
        d = signals.to_dict()
        assert d["is_urgent"] is True
        assert isinstance(d["matched_patterns"], list)


# ---------------------------------------------------------------------------
# System prompt construction
# ---------------------------------------------------------------------------


class TestBuildSystemPrompt:
    def test_default_prompt(self) -> None:
        tenant = {"name": "Bob's Plumbing", "config": {}}
        prompt = build_system_prompt(tenant)
        assert "Bob's Plumbing" in prompt

    def test_custom_prompt(self) -> None:
        tenant = {
            "name": "Bob's Plumbing",
            "config": {"system_prompt": "You are PlumbBot."},
        }
        prompt = build_system_prompt(tenant)
        assert prompt == "You are PlumbBot."

    def test_urgent_signal_adjusts_prompt(self) -> None:
        tenant = {"name": "Bob's Plumbing", "config": {}}
        signals = L1aSignals(is_urgent=True)
        prompt = build_system_prompt(tenant, signals)
        assert "urgent" in prompt.lower()

    def test_safety_signal_adjusts_prompt(self) -> None:
        tenant = {"name": "Bob's Plumbing", "config": {}}
        signals = L1aSignals(is_safety_concern=True)
        prompt = build_system_prompt(tenant, signals)
        assert "distress" in prompt.lower() or "crisis" in prompt.lower()

    def test_escalation_signal_adjusts_prompt(self) -> None:
        tenant = {"name": "Bob's Plumbing", "config": {}}
        signals = L1aSignals(needs_escalation=True)
        prompt = build_system_prompt(tenant, signals)
        assert "real person" in prompt.lower() or "contact" in prompt.lower()

    def test_no_signals_no_addendum(self) -> None:
        tenant = {"name": "Bob's Plumbing", "config": {}}
        signals = L1aSignals()
        prompt = build_system_prompt(tenant, signals)
        assert "IMPORTANT" not in prompt


# ---------------------------------------------------------------------------
# ChatResponse
# ---------------------------------------------------------------------------


class TestChatResponse:
    def test_to_dict_basic(self) -> None:
        from zetherion_ai.skills.client_chat import ChatResponse

        resp = ChatResponse(content="Hello!", model="llama3.2:3b")
        d = resp.to_dict()
        assert d["content"] == "Hello!"
        assert d["model"] == "llama3.2:3b"
        assert "signals" not in d  # No signals, not included

    def test_to_dict_with_signals(self) -> None:
        from zetherion_ai.skills.client_chat import ChatResponse

        signals = L1aSignals(is_urgent=True, matched_patterns=["urgency:urgent"])
        resp = ChatResponse(content="Help is on the way.", signals=signals)
        d = resp.to_dict()
        assert d["signals"]["is_urgent"] is True


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@dataclass
class _FakeResult:
    content: str = "Hello, how can I help?"
    model: str = "llama3.2:3b"
    provider: str = "ollama"
    input_tokens: int = 10
    output_tokens: int = 20
    latency_ms: float = 100.0
    estimated_cost_usd: float = 0.0


def _make_broker() -> MagicMock:
    broker = MagicMock()
    broker.infer = AsyncMock(return_value=_FakeResult())
    return broker


@pytest.fixture
def skill() -> ClientChatSkill:
    return ClientChatSkill(inference_broker=_make_broker())


@pytest.fixture
def skill_no_broker() -> ClientChatSkill:
    return ClientChatSkill(inference_broker=None)


_TENANT = {
    "tenant_id": uuid4(),
    "name": "Bob's Plumbing",
    "domain": "bobsplumbing.com",
    "config": {},
}


# ---------------------------------------------------------------------------
# Skill metadata & init
# ---------------------------------------------------------------------------


class TestSkillMetadata:
    def test_name(self, skill: ClientChatSkill) -> None:
        assert skill.metadata.name == "client_chat"

    def test_intents(self, skill: ClientChatSkill) -> None:
        assert "client_chat" in skill.metadata.intents


class TestSkillInitialize:
    @pytest.mark.asyncio
    async def test_init_with_broker(self, skill: ClientChatSkill) -> None:
        assert await skill.initialize() is True

    @pytest.mark.asyncio
    async def test_init_without_broker(self, skill_no_broker: ClientChatSkill) -> None:
        assert await skill_no_broker.initialize() is True

    @pytest.mark.asyncio
    async def test_safe_initialize_sets_ready(self, skill: ClientChatSkill) -> None:
        await skill.safe_initialize()
        assert skill.status == SkillStatus.READY


# ---------------------------------------------------------------------------
# generate_response
# ---------------------------------------------------------------------------


class TestGenerateResponse:
    @pytest.mark.asyncio
    async def test_basic_response(self, skill: ClientChatSkill) -> None:
        result = await skill.generate_response(
            tenant=_TENANT,
            message="What services do you offer?",
        )
        assert result.content == "Hello, how can I help?"
        assert result.model == "llama3.2:3b"
        assert not result.signals.has_signals

    @pytest.mark.asyncio
    async def test_urgent_message_adjusts_prompt(self, skill: ClientChatSkill) -> None:
        await skill.generate_response(
            tenant=_TENANT,
            message="My pipe burst! This is an emergency!",
        )
        # Verify the system prompt was adjusted for urgency
        call_kwargs = skill._broker.infer.call_args.kwargs
        assert "urgent" in call_kwargs["system_prompt"].lower()

    @pytest.mark.asyncio
    async def test_safety_message_adjusts_prompt(self, skill: ClientChatSkill) -> None:
        await skill.generate_response(
            tenant=_TENANT,
            message="I want to harm myself",
        )
        call_kwargs = skill._broker.infer.call_args.kwargs
        assert "distress" in call_kwargs["system_prompt"].lower()

    @pytest.mark.asyncio
    async def test_no_broker_returns_placeholder(self, skill_no_broker: ClientChatSkill) -> None:
        result = await skill_no_broker.generate_response(
            tenant=_TENANT,
            message="Hello",
        )
        assert "not configured" in result.content
        assert result.model is None

    @pytest.mark.asyncio
    async def test_passes_history(self, skill: ClientChatSkill) -> None:
        history = [
            {"role": "user", "content": "Hi"},
            {"role": "assistant", "content": "Hello!"},
        ]
        await skill.generate_response(
            tenant=_TENANT,
            message="What services?",
            history=history,
        )
        call_kwargs = skill._broker.infer.call_args.kwargs
        assert call_kwargs["messages"] == history


# ---------------------------------------------------------------------------
# generate_stream
# ---------------------------------------------------------------------------


class TestGenerateStream:
    @pytest.mark.asyncio
    async def test_returns_signals_and_stream(self, skill: ClientChatSkill) -> None:
        @dataclass
        class _FakeChunk:
            content: str = ""
            done: bool = False
            model: str = ""

        async def _fake_stream(**kwargs):
            yield _FakeChunk(content="Hello")
            yield _FakeChunk(content=" there")
            yield _FakeChunk(done=True, model="test-model")

        skill._broker.infer_stream = _fake_stream

        signals, stream = await skill.generate_stream(
            tenant=_TENANT,
            message="Hello",
        )
        assert isinstance(signals, L1aSignals)

        chunks = [c async for c in stream]
        assert len(chunks) == 3
        assert chunks[0].content == "Hello"
        assert chunks[2].done

    @pytest.mark.asyncio
    async def test_stream_no_broker_raises(self, skill_no_broker: ClientChatSkill) -> None:
        with pytest.raises(RuntimeError, match="No InferenceBroker"):
            await skill_no_broker.generate_stream(
                tenant=_TENANT,
                message="Hello",
            )

    @pytest.mark.asyncio
    async def test_stream_urgent_adjusts_prompt(self, skill: ClientChatSkill) -> None:
        @dataclass
        class _FakeChunk:
            content: str = ""
            done: bool = False
            model: str = ""

        captured_kwargs: dict = {}

        async def _fake_stream(**kwargs):
            captured_kwargs.update(kwargs)
            yield _FakeChunk(content="Help", done=False)
            yield _FakeChunk(done=True, model="test")

        skill._broker.infer_stream = _fake_stream

        signals, stream = await skill.generate_stream(
            tenant=_TENANT,
            message="This is an emergency, I need help immediately!",
        )
        assert signals.is_urgent
        # Consume the stream to trigger the call
        _ = [c async for c in stream]
        assert "urgent" in captured_kwargs["system_prompt"].lower()


# ---------------------------------------------------------------------------
# handle() via skill registry
# ---------------------------------------------------------------------------


class TestSkillHandle:
    @pytest.mark.asyncio
    async def test_handle_via_registry(self, skill: ClientChatSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(
            intent="client_chat",
            message="What do you offer?",
            context={"tenant": _TENANT},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert resp.data["content"] == "Hello, how can I help?"

    @pytest.mark.asyncio
    async def test_handle_empty_message(self, skill: ClientChatSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(
            intent="client_chat",
            message="",
            context={"tenant": _TENANT},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "required" in resp.error.lower()
