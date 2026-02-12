"""Tests for tenant_intelligence skill — L1b/L2 extraction."""

from __future__ import annotations

import json
from dataclasses import dataclass
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.tenant_intelligence import TenantIntelligenceSkill

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_EXTRACTION_JSON = json.dumps(
    {
        "contact": {"name": "Dave Smith", "email": "dave@example.com", "phone": "07700123456"},
        "intent": "enquiry",
        "sentiment": "positive",
        "purchase_signal": True,
        "products_mentioned": ["bathroom renovation"],
        "communication_preference": "email",
    }
)

_SESSION_SUMMARY_JSON = json.dumps(
    {
        "summary": "Customer enquired about bathroom renovation pricing.",
        "outcome": "resolved",
        "customer_profile": "Homeowner interested in bathroom renovation",
        "topics": ["bathroom renovation", "pricing"],
        "unmet_needs": [],
        "follow_up_needed": False,
        "follow_up_action": None,
    }
)


@dataclass
class _FakeInferenceResult:
    content: str = _EXTRACTION_JSON
    model: str = "llama3.2:3b"
    provider: str = "ollama"
    input_tokens: int = 100
    output_tokens: int = 50
    latency_ms: float = 200.0
    estimated_cost_usd: float = 0.0


def _make_broker(response_content: str = _EXTRACTION_JSON) -> MagicMock:
    broker = MagicMock()
    broker.infer = AsyncMock(return_value=_FakeInferenceResult(content=response_content))
    return broker


def _make_tenant_manager() -> AsyncMock:
    tm = AsyncMock()
    tm.upsert_contact = AsyncMock(
        return_value={"contact_id": uuid4(), "name": "Dave Smith", "email": "dave@example.com"}
    )
    tm.add_interaction = AsyncMock(return_value={"interaction_id": uuid4(), "tenant_id": uuid4()})
    tm.get_interactions = AsyncMock(return_value=[])
    return tm


@pytest.fixture
def skill() -> TenantIntelligenceSkill:
    return TenantIntelligenceSkill(
        inference_broker=_make_broker(),
        tenant_manager=_make_tenant_manager(),
    )


@pytest.fixture
def skill_no_broker() -> TenantIntelligenceSkill:
    return TenantIntelligenceSkill(
        inference_broker=None,
        tenant_manager=_make_tenant_manager(),
    )


@pytest.fixture
def skill_no_tm() -> TenantIntelligenceSkill:
    return TenantIntelligenceSkill(
        inference_broker=_make_broker(),
        tenant_manager=None,
    )


# ---------------------------------------------------------------------------
# Metadata & init
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_name(self, skill: TenantIntelligenceSkill) -> None:
        assert skill.metadata.name == "tenant_intelligence"

    def test_intents(self, skill: TenantIntelligenceSkill) -> None:
        assert "extract_message_entities" in skill.metadata.intents
        assert "summarise_session" in skill.metadata.intents


class TestInitialize:
    @pytest.mark.asyncio
    async def test_init_success(self, skill: TenantIntelligenceSkill) -> None:
        assert await skill.initialize() is True

    @pytest.mark.asyncio
    async def test_init_no_broker(self, skill_no_broker: TenantIntelligenceSkill) -> None:
        assert await skill_no_broker.initialize() is True

    @pytest.mark.asyncio
    async def test_safe_init_ready(self, skill: TenantIntelligenceSkill) -> None:
        await skill.safe_initialize()
        assert skill.status == SkillStatus.READY


# ---------------------------------------------------------------------------
# L1b — extract_message_entities
# ---------------------------------------------------------------------------


class TestExtractMessageEntities:
    @pytest.mark.asyncio
    async def test_extract_basic(self, skill: TenantIntelligenceSkill) -> None:
        result = await skill.extract_message_entities(
            "Hi, I'm Dave Smith, email dave@example.com. "
            "I'd like a quote for a bathroom renovation."
        )
        assert result["contact"]["name"] == "Dave Smith"
        assert result["contact"]["email"] == "dave@example.com"
        assert result["intent"] == "enquiry"
        assert result["sentiment"] == "positive"
        assert result["purchase_signal"] is True

    @pytest.mark.asyncio
    async def test_extract_no_broker(self, skill_no_broker: TenantIntelligenceSkill) -> None:
        result = await skill_no_broker.extract_message_entities("Hello")
        assert result["intent"] == "other"
        assert result["contact"]["name"] is None

    @pytest.mark.asyncio
    async def test_extract_handles_invalid_json(self, skill: TenantIntelligenceSkill) -> None:
        skill._broker.infer = AsyncMock(return_value=_FakeInferenceResult(content="not json"))
        result = await skill.extract_message_entities("Hello")
        assert result["intent"] == "other"  # Falls back to empty

    @pytest.mark.asyncio
    async def test_extract_strips_markdown_fences(self, skill: TenantIntelligenceSkill) -> None:
        fenced = f"```json\n{_EXTRACTION_JSON}\n```"
        skill._broker.infer = AsyncMock(return_value=_FakeInferenceResult(content=fenced))
        result = await skill.extract_message_entities("Hello")
        assert result["contact"]["name"] == "Dave Smith"


# ---------------------------------------------------------------------------
# L2 — summarise_session
# ---------------------------------------------------------------------------


class TestSummariseSession:
    @pytest.mark.asyncio
    async def test_summarise_basic(self, skill: TenantIntelligenceSkill) -> None:
        skill._broker.infer = AsyncMock(
            return_value=_FakeInferenceResult(content=_SESSION_SUMMARY_JSON)
        )
        messages = [
            {"role": "user", "content": "How much for a bathroom renovation?"},
            {"role": "assistant", "content": "Our prices start from..."},
        ]
        result = await skill.summarise_session(messages)
        assert "bathroom" in result["summary"].lower()
        assert result["outcome"] == "resolved"
        assert result["follow_up_needed"] is False

    @pytest.mark.asyncio
    async def test_summarise_no_broker(self, skill_no_broker: TenantIntelligenceSkill) -> None:
        result = await skill_no_broker.summarise_session(
            [
                {"role": "user", "content": "Hello"},
            ]
        )
        assert result["outcome"] == "unresolved"
        assert result["summary"] is None


# ---------------------------------------------------------------------------
# handle() — skill registry
# ---------------------------------------------------------------------------


class TestHandleExtract:
    @pytest.mark.asyncio
    async def test_handle_extract(self, skill: TenantIntelligenceSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(
            intent="extract_message_entities",
            message="I'm Dave, dave@example.com",
            context={"tenant_id": str(uuid4()), "session_id": str(uuid4())},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert resp.data["extraction"]["contact"]["name"] == "Dave Smith"
        # Contact should have been persisted
        assert resp.data["contact_id"] is not None
        skill._tenant_manager.upsert_contact.assert_called_once()
        skill._tenant_manager.add_interaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_extract_empty_message(self, skill: TenantIntelligenceSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(
            intent="extract_message_entities",
            message="",
            context={},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "required" in resp.error.lower()

    @pytest.mark.asyncio
    async def test_handle_extract_no_tm(self, skill_no_tm: TenantIntelligenceSkill) -> None:
        await skill_no_tm.safe_initialize()
        req = SkillRequest(
            intent="extract_message_entities",
            message="Hello",
            context={"tenant_id": str(uuid4())},
        )
        resp = await skill_no_tm.safe_handle(req)
        assert resp.success is True
        assert resp.data["contact_id"] is None  # No TM, no persistence


class TestHandleSummarise:
    @pytest.mark.asyncio
    async def test_handle_summarise(self, skill: TenantIntelligenceSkill) -> None:
        await skill.safe_initialize()
        skill._broker.infer = AsyncMock(
            return_value=_FakeInferenceResult(content=_SESSION_SUMMARY_JSON)
        )
        req = SkillRequest(
            intent="summarise_session",
            context={
                "tenant_id": str(uuid4()),
                "session_id": str(uuid4()),
                "messages": [
                    {"role": "user", "content": "Hello"},
                    {"role": "assistant", "content": "Hi!"},
                ],
            },
        )
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert "bathroom" in resp.data["summary"]["summary"].lower()
        skill._tenant_manager.add_interaction.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_summarise_no_messages(self, skill: TenantIntelligenceSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(
            intent="summarise_session",
            context={"tenant_id": str(uuid4())},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "required" in resp.error.lower()


# ---------------------------------------------------------------------------
# Unknown intent
# ---------------------------------------------------------------------------


class TestUnknownIntent:
    @pytest.mark.asyncio
    async def test_unknown_intent(self, skill: TenantIntelligenceSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(intent="bogus_intent")
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "Unknown" in resp.error


# ---------------------------------------------------------------------------
# JSON parsing edge cases
# ---------------------------------------------------------------------------


class TestParseJsonResponse:
    def test_plain_json(self) -> None:
        d = TenantIntelligenceSkill._parse_json_response('{"a": 1}')
        assert d == {"a": 1}

    def test_fenced_json(self) -> None:
        d = TenantIntelligenceSkill._parse_json_response('```json\n{"a": 1}\n```')
        assert d == {"a": 1}

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(json.JSONDecodeError):
            TenantIntelligenceSkill._parse_json_response("not json at all")
