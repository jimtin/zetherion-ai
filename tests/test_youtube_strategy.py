"""Tests for YouTubeStrategySkill."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

import pytest

from zetherion_ai.agent.providers import TaskType
from zetherion_ai.skills.base import (
    SkillRequest,
    SkillStatus,
)
from zetherion_ai.skills.permissions import Permission
from zetherion_ai.skills.youtube.strategy import (
    INTENT_HANDLERS,
    YouTubeStrategySkill,
    _parse_json,
    _resolve_channel_id,
    _serialise,
)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_CHANNEL_ID = UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa")
_TENANT_ID = UUID("bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_storage() -> AsyncMock:
    """Create a mock YouTubeStorage with sensible defaults."""
    storage = AsyncMock()

    storage.get_channel = AsyncMock(
        return_value={
            "id": _CHANNEL_ID,
            "channel_name": "TestChannel",
            "trust_level": 0,
            "trust_stats": {"total": 0, "approved": 0, "rejected": 0},
        }
    )
    storage.get_latest_report = AsyncMock(return_value={"report": {"summary": "all good"}})
    storage.get_reply_drafts = AsyncMock(
        return_value=[
            {"status": "posted"},
            {"status": "posted"},
            {"status": "pending"},
        ]
    )
    storage.get_documents = AsyncMock(
        return_value=[{"title": "Brand Guide", "doc_type": "brand_guide", "content": "Be cool."}]
    )
    storage.get_latest_strategy = AsyncMock(
        return_value={
            "strategy": {"positioning": {"niche": "tech"}},
            "valid_until": datetime.utcnow() + timedelta(days=10),
        }
    )
    storage.save_strategy = AsyncMock(
        return_value={
            "id": uuid4(),
            "channel_id": _CHANNEL_ID,
            "strategy_type": "full",
            "strategy": {"positioning": {"niche": "tech"}},
            "model_used": "multi",
            "valid_until": (datetime.utcnow() + timedelta(days=30)).isoformat(),
        }
    )
    storage.get_strategy_history = AsyncMock(return_value=[])
    storage.list_channels = AsyncMock(
        return_value=[
            {"id": _CHANNEL_ID, "channel_name": "TestChannel"},
        ]
    )

    # Assumptions storage
    storage.get_assumptions = AsyncMock(return_value=[])
    storage.save_assumption = AsyncMock(return_value={"id": str(uuid4())})

    return storage


@dataclass
class _FakeInferenceResult:
    """Minimal stand-in for InferenceResult."""

    content: str


def _make_broker(content: str | None = None) -> AsyncMock:
    """Create a mock InferenceBroker that returns valid JSON."""
    broker = AsyncMock()
    result_content = content or json.dumps(
        {
            "positioning": {
                "niche": "tech reviews",
                "target_audience": "developers",
                "tone": "casual",
            },
            "content_strategy": {
                "pillars": [
                    {"name": "tutorials"},
                    {"name": "reviews"},
                    {"name": "news"},
                ]
            },
        }
    )
    broker.infer = AsyncMock(return_value=_FakeInferenceResult(content=result_content))
    return broker


def _req(
    intent: str = "yt_generate_strategy",
    channel_id: str | None = None,
    **ctx: object,
) -> SkillRequest:
    """Create a SkillRequest with the given intent and channel_id in context."""
    context: dict = dict(ctx)
    if channel_id is not None:
        context["channel_id"] = channel_id
    elif "channel_id" not in context:
        context["channel_id"] = str(_CHANNEL_ID)
    return SkillRequest(
        intent=intent,
        user_id="user-1",
        message="test",
        context=context,
    )


async def _init_skill(
    storage: AsyncMock | None = None,
    broker: AsyncMock | None = None,
) -> YouTubeStrategySkill:
    """Create, initialize, and return a YouTubeStrategySkill."""
    s = storage or _make_storage()
    b = broker or _make_broker()
    skill = YouTubeStrategySkill(storage=s, broker=b)
    result = await skill.initialize()
    assert result is True
    return skill


# ---------------------------------------------------------------------------
# 1. Metadata
# ---------------------------------------------------------------------------


class TestMetadata:
    """Tests for the metadata property."""

    def test_metadata_name(self) -> None:
        skill = YouTubeStrategySkill()
        assert skill.metadata.name == "youtube_strategy"

    def test_metadata_description(self) -> None:
        skill = YouTubeStrategySkill()
        assert "strategies" in skill.metadata.description.lower()

    def test_metadata_version(self) -> None:
        skill = YouTubeStrategySkill()
        assert skill.metadata.version == "1.0.0"

    def test_metadata_permissions(self) -> None:
        skill = YouTubeStrategySkill()
        assert skill.has_permission(Permission.READ_PROFILE)
        assert skill.has_permission(Permission.WRITE_MEMORIES)

    def test_metadata_collections(self) -> None:
        skill = YouTubeStrategySkill()
        assert "yt_docs" in skill.metadata.collections

    def test_metadata_intents(self) -> None:
        skill = YouTubeStrategySkill()
        assert set(skill.metadata.intents) == {
            "yt_generate_strategy",
            "yt_get_strategy",
            "yt_strategy_history",
        }


# ---------------------------------------------------------------------------
# 2. initialize()
# ---------------------------------------------------------------------------


class TestInitialize:
    """Tests for initialize()."""

    @pytest.mark.asyncio
    async def test_initialize_success(self) -> None:
        skill = YouTubeStrategySkill(storage=_make_storage(), broker=_make_broker())
        result = await skill.initialize()
        assert result is True
        assert skill.status == SkillStatus.READY

    @pytest.mark.asyncio
    async def test_initialize_no_storage(self) -> None:
        skill = YouTubeStrategySkill(storage=None, broker=_make_broker())
        result = await skill.initialize()
        assert result is False
        assert skill.status == SkillStatus.ERROR

    @pytest.mark.asyncio
    async def test_initialize_no_broker(self) -> None:
        skill = YouTubeStrategySkill(storage=_make_storage(), broker=None)
        result = await skill.initialize()
        assert result is False
        assert skill.status == SkillStatus.ERROR

    @pytest.mark.asyncio
    async def test_initialize_no_storage_no_broker(self) -> None:
        skill = YouTubeStrategySkill()
        result = await skill.initialize()
        assert result is False
        assert skill.status == SkillStatus.ERROR


# ---------------------------------------------------------------------------
# 3. handle() dispatch
# ---------------------------------------------------------------------------


class TestHandleDispatch:
    """Tests for the handle() dispatcher."""

    @pytest.mark.asyncio
    async def test_dispatch_unknown_intent(self) -> None:
        skill = await _init_skill()
        resp = await skill.handle(_req(intent="yt_unknown"))
        assert resp.success is False
        assert "Unknown intent" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_dispatch_generate(self) -> None:
        skill = await _init_skill()
        resp = await skill.handle(_req(intent="yt_generate_strategy"))
        assert resp.success is True
        assert resp.message == "Strategy generated."

    @pytest.mark.asyncio
    async def test_dispatch_get_strategy(self) -> None:
        skill = await _init_skill()
        resp = await skill.handle(_req(intent="yt_get_strategy"))
        assert resp.success is True

    @pytest.mark.asyncio
    async def test_dispatch_strategy_history(self) -> None:
        skill = await _init_skill()
        resp = await skill.handle(_req(intent="yt_strategy_history"))
        assert resp.success is True
        assert "strategies" in resp.data

    @pytest.mark.asyncio
    async def test_dispatch_handler_exception_returns_error(self) -> None:
        """If a handler raises, handle() returns an error response."""
        storage = _make_storage()
        storage.get_channel = AsyncMock(side_effect=RuntimeError("db down"))
        skill = await _init_skill(storage=storage)
        resp = await skill.handle(_req(intent="yt_generate_strategy"))
        assert resp.success is False
        assert "Strategy error" in (resp.error or "")


# ---------------------------------------------------------------------------
# 4. _handle_generate
# ---------------------------------------------------------------------------


class TestHandleGenerate:
    """Tests for _handle_generate."""

    @pytest.mark.asyncio
    async def test_generate_no_channel_id(self) -> None:
        skill = await _init_skill()
        req = SkillRequest(intent="yt_generate_strategy", context={})
        resp = await skill.handle(req)
        assert resp.success is False
        assert "channel_id required" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_generate_invalid_channel_id(self) -> None:
        skill = await _init_skill()
        req = _req(intent="yt_generate_strategy", channel_id="not-a-uuid")
        resp = await skill.handle(req)
        assert resp.success is False
        assert "channel_id required" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_generate_channel_not_found(self) -> None:
        storage = _make_storage()
        storage.get_channel = AsyncMock(return_value=None)
        skill = await _init_skill(storage=storage)
        resp = await skill.handle(_req(intent="yt_generate_strategy"))
        assert resp.success is False
        assert "Channel not found" in (resp.error or "")

    @pytest.mark.asyncio
    async def test_generate_calls_full_pipeline(self) -> None:
        """_handle_generate should call generate_strategy which calls
        _gather_context, _synthesise_strategy, save_strategy, _infer_assumptions."""
        storage = _make_storage()
        broker = _make_broker()
        skill = await _init_skill(storage=storage, broker=broker)

        resp = await skill.handle(_req(intent="yt_generate_strategy"))
        assert resp.success is True
        assert resp.message == "Strategy generated."

        # save_strategy was called
        storage.save_strategy.assert_awaited_once()
        # broker.infer was called (synthesis)
        broker.infer.assert_awaited_once()


# ---------------------------------------------------------------------------
# 5. _handle_get_strategy
# ---------------------------------------------------------------------------


class TestHandleGetStrategy:
    """Tests for _handle_get_strategy."""

    @pytest.mark.asyncio
    async def test_get_strategy_returns_latest(self) -> None:
        storage = _make_storage()
        storage.get_latest_strategy = AsyncMock(
            return_value={
                "id": uuid4(),
                "strategy": {"positioning": {"niche": "tech"}},
            }
        )
        skill = await _init_skill(storage=storage)
        resp = await skill.handle(_req(intent="yt_get_strategy"))
        assert resp.success is True
        assert resp.message == "Latest strategy."
        assert resp.data.get("strategy") == {"positioning": {"niche": "tech"}}

    @pytest.mark.asyncio
    async def test_get_strategy_returns_empty_when_none(self) -> None:
        storage = _make_storage()
        storage.get_latest_strategy = AsyncMock(return_value=None)
        skill = await _init_skill(storage=storage)
        resp = await skill.handle(_req(intent="yt_get_strategy"))
        assert resp.success is True
        assert resp.message == "No strategy available yet."
        assert resp.data == {}

    @pytest.mark.asyncio
    async def test_get_strategy_no_channel_id(self) -> None:
        skill = await _init_skill()
        req = SkillRequest(intent="yt_get_strategy", context={})
        resp = await skill.handle(req)
        assert resp.success is False


# ---------------------------------------------------------------------------
# 6. _handle_strategy_history
# ---------------------------------------------------------------------------


class TestHandleStrategyHistory:
    """Tests for _handle_strategy_history."""

    @pytest.mark.asyncio
    async def test_history_returns_list(self) -> None:
        storage = _make_storage()
        storage.get_strategy_history = AsyncMock(
            return_value=[
                {"id": uuid4(), "strategy": {"a": 1}},
                {"id": uuid4(), "strategy": {"b": 2}},
            ]
        )
        skill = await _init_skill(storage=storage)
        resp = await skill.handle(_req(intent="yt_strategy_history"))
        assert resp.success is True
        assert "2 strategy(s) found" in resp.message
        assert len(resp.data["strategies"]) == 2

    @pytest.mark.asyncio
    async def test_history_empty(self) -> None:
        storage = _make_storage()
        storage.get_strategy_history = AsyncMock(return_value=[])
        skill = await _init_skill(storage=storage)
        resp = await skill.handle(_req(intent="yt_strategy_history"))
        assert resp.success is True
        assert resp.data["strategies"] == []

    @pytest.mark.asyncio
    async def test_history_passes_limit(self) -> None:
        storage = _make_storage()
        storage.get_strategy_history = AsyncMock(return_value=[])
        skill = await _init_skill(storage=storage)
        req = _req(intent="yt_strategy_history", limit=5)
        await skill.handle(req)
        storage.get_strategy_history.assert_awaited_once_with(_CHANNEL_ID, limit=5)

    @pytest.mark.asyncio
    async def test_history_default_limit(self) -> None:
        storage = _make_storage()
        storage.get_strategy_history = AsyncMock(return_value=[])
        skill = await _init_skill(storage=storage)
        req = _req(intent="yt_strategy_history")
        await skill.handle(req)
        storage.get_strategy_history.assert_awaited_once_with(_CHANNEL_ID, limit=10)


# ---------------------------------------------------------------------------
# 7. generate_strategy (core pipeline)
# ---------------------------------------------------------------------------


class TestGenerateStrategy:
    """Tests for generate_strategy."""

    @pytest.mark.asyncio
    async def test_pipeline_context_synthesis_save_assumptions(self) -> None:
        """generate_strategy should:
        1. gather context
        2. synthesise strategy via broker
        3. save to storage
        4. infer assumptions
        """
        storage = _make_storage()
        broker = _make_broker()
        skill = await _init_skill(storage=storage, broker=broker)

        channel = {"trust_level": 0, "trust_stats": {"total": 0, "approved": 0, "rejected": 0}}
        result = await skill.generate_strategy(_CHANNEL_ID, channel)

        # Broker was called for synthesis
        broker.infer.assert_awaited_once()
        call_kwargs = broker.infer.call_args
        assert call_kwargs.kwargs["task_type"] == TaskType.COMPLEX_REASONING

        # Strategy was saved
        storage.save_strategy.assert_awaited_once()
        save_kwargs = storage.save_strategy.call_args.kwargs
        assert save_kwargs["channel_id"] == _CHANNEL_ID
        assert save_kwargs["strategy_type"] == "full"
        assert save_kwargs["model_used"] == "multi"
        assert "valid_until" in save_kwargs

        # Assumptions were inferred (niche + audience + tone + pillars = 4 calls)
        assert storage.save_assumption.await_count == 4

        # Result is serialised
        assert isinstance(result, dict)

    @pytest.mark.asyncio
    async def test_pipeline_synthesis_failure_returns_fallback(self) -> None:
        """If broker.infer raises, strategy body should contain _fallback flag."""
        storage = _make_storage()
        broker = _make_broker()
        broker.infer = AsyncMock(side_effect=RuntimeError("LLM down"))
        skill = await _init_skill(storage=storage, broker=broker)

        channel = {"trust_level": 0, "trust_stats": {"total": 0, "approved": 0, "rejected": 0}}
        await skill.generate_strategy(_CHANNEL_ID, channel)

        # save_strategy is still called, with the fallback body
        storage.save_strategy.assert_awaited_once()
        saved_strategy = storage.save_strategy.call_args.kwargs["strategy"]
        assert saved_strategy.get("_fallback") is True


# ---------------------------------------------------------------------------
# 8. _gather_context
# ---------------------------------------------------------------------------


class TestGatherContext:
    """Tests for _gather_context."""

    @pytest.mark.asyncio
    async def test_assembles_all_context_pieces(self) -> None:
        storage = _make_storage()
        broker = _make_broker()
        skill = await _init_skill(storage=storage, broker=broker)

        channel = {"trust_level": 0, "trust_stats": {"total": 0, "approved": 0, "rejected": 0}}
        ctx = await skill._gather_context(_CHANNEL_ID, channel)

        assert "channel" in ctx
        assert "intelligence" in ctx
        assert "trust" in ctx
        assert "reply_stats" in ctx
        assert "documents" in ctx
        assert "assumptions" in ctx
        assert "previous_strategy" in ctx

    @pytest.mark.asyncio
    async def test_reply_stats_counts(self) -> None:
        storage = _make_storage()
        storage.get_reply_drafts = AsyncMock(
            return_value=[
                {"status": "posted"},
                {"status": "posted"},
                {"status": "posted"},
                {"status": "pending"},
                {"status": "pending"},
            ]
        )
        skill = await _init_skill(storage=storage)

        channel = {"trust_level": 0, "trust_stats": {"total": 0, "approved": 0, "rejected": 0}}
        ctx = await skill._gather_context(_CHANNEL_ID, channel)
        assert ctx["reply_stats"]["posted"] == 3
        assert ctx["reply_stats"]["pending"] == 2
        assert ctx["reply_stats"]["total_drafts"] == 5

    @pytest.mark.asyncio
    async def test_no_intelligence_report(self) -> None:
        storage = _make_storage()
        storage.get_latest_report = AsyncMock(return_value=None)
        skill = await _init_skill(storage=storage)

        channel = {"trust_level": 0, "trust_stats": {"total": 0, "approved": 0, "rejected": 0}}
        ctx = await skill._gather_context(_CHANNEL_ID, channel)
        assert ctx["intelligence"] == {}

    @pytest.mark.asyncio
    async def test_no_documents(self) -> None:
        storage = _make_storage()
        storage.get_documents = AsyncMock(return_value=[])
        skill = await _init_skill(storage=storage)

        channel = {"trust_level": 0, "trust_stats": {"total": 0, "approved": 0, "rejected": 0}}
        ctx = await skill._gather_context(_CHANNEL_ID, channel)
        assert ctx["documents"] == "No client documents uploaded."

    @pytest.mark.asyncio
    async def test_no_previous_strategy(self) -> None:
        storage = _make_storage()
        storage.get_latest_strategy = AsyncMock(return_value=None)
        skill = await _init_skill(storage=storage)

        channel = {"trust_level": 0, "trust_stats": {"total": 0, "approved": 0, "rejected": 0}}
        ctx = await skill._gather_context(_CHANNEL_ID, channel)
        assert ctx["previous_strategy"] == {}

    @pytest.mark.asyncio
    async def test_document_summaries_truncated(self) -> None:
        storage = _make_storage()
        long_content = "x" * 5000
        storage.get_documents = AsyncMock(
            return_value=[{"title": "Big Doc", "doc_type": "guide", "content": long_content}]
        )
        skill = await _init_skill(storage=storage)

        channel = {"trust_level": 0, "trust_stats": {"total": 0, "approved": 0, "rejected": 0}}
        ctx = await skill._gather_context(_CHANNEL_ID, channel)
        # Content should be truncated to 2000 chars
        assert len(ctx["documents"]) < len(long_content)


# ---------------------------------------------------------------------------
# 9. _synthesise_strategy
# ---------------------------------------------------------------------------


class TestSynthesiseStrategy:
    """Tests for _synthesise_strategy."""

    @pytest.mark.asyncio
    async def test_calls_broker_with_correct_task_type(self) -> None:
        broker = _make_broker()
        skill = await _init_skill(broker=broker)

        context = {
            "intelligence": {},
            "trust": {"level": 0, "label": "SUPERVISED"},
            "reply_stats": {"posted": 0, "pending": 0, "total_drafts": 0},
            "assumptions": [],
            "previous_strategy": {},
            "documents": "No docs.",
        }
        await skill._synthesise_strategy(context)

        broker.infer.assert_awaited_once()
        call_kwargs = broker.infer.call_args.kwargs
        assert call_kwargs["task_type"] == TaskType.COMPLEX_REASONING
        assert call_kwargs["max_tokens"] == 8192
        assert call_kwargs["temperature"] == 0.5
        assert call_kwargs["system_prompt"] is not None

    @pytest.mark.asyncio
    async def test_formats_assumptions_into_prompt(self) -> None:
        broker = _make_broker()
        skill = await _init_skill(broker=broker)

        context = {
            "intelligence": {},
            "trust": {"level": 1, "label": "GUIDED"},
            "reply_stats": {"posted": 5, "pending": 2, "total_drafts": 7},
            "assumptions": [
                {"category": "content", "statement": "Tech niche", "confidence": 0.9},
            ],
            "previous_strategy": {"positioning": {"niche": "tech"}},
            "documents": "Brand guide here.",
        }
        await skill._synthesise_strategy(context)

        prompt_used = broker.infer.call_args.kwargs["prompt"]
        assert "Tech niche" in prompt_used
        assert "content" in prompt_used

    @pytest.mark.asyncio
    async def test_synthesis_exception_returns_fallback(self) -> None:
        broker = _make_broker()
        broker.infer = AsyncMock(side_effect=Exception("boom"))
        skill = await _init_skill(broker=broker)

        context = {
            "intelligence": {},
            "trust": {"level": 0, "label": "SUPERVISED"},
            "reply_stats": {},
            "assumptions": [],
            "previous_strategy": {},
            "documents": "",
        }
        result = await skill._synthesise_strategy(context)
        assert result.get("_fallback") is True
        assert "error" in result


# ---------------------------------------------------------------------------
# 10. _infer_assumptions
# ---------------------------------------------------------------------------


class TestInferAssumptions:
    """Tests for _infer_assumptions."""

    @pytest.mark.asyncio
    async def test_infers_niche(self) -> None:
        storage = _make_storage()
        skill = await _init_skill(storage=storage)

        strategy = {"positioning": {"niche": "cooking"}}
        await skill._infer_assumptions(_CHANNEL_ID, strategy)

        calls = storage.save_assumption.call_args_list
        statements = [c.args[0]["statement"] for c in calls]
        assert any("cooking" in s for s in statements)

    @pytest.mark.asyncio
    async def test_infers_audience(self) -> None:
        storage = _make_storage()
        skill = await _init_skill(storage=storage)

        strategy = {"positioning": {"target_audience": "home cooks"}}
        await skill._infer_assumptions(_CHANNEL_ID, strategy)

        calls = storage.save_assumption.call_args_list
        statements = [c.args[0]["statement"] for c in calls]
        assert any("home cooks" in s for s in statements)

    @pytest.mark.asyncio
    async def test_infers_tone(self) -> None:
        storage = _make_storage()
        skill = await _init_skill(storage=storage)

        strategy = {"positioning": {"tone": "friendly"}}
        await skill._infer_assumptions(_CHANNEL_ID, strategy)

        calls = storage.save_assumption.call_args_list
        categories = [c.args[0]["category"] for c in calls]
        assert "tone" in categories

    @pytest.mark.asyncio
    async def test_infers_pillars(self) -> None:
        storage = _make_storage()
        skill = await _init_skill(storage=storage)

        strategy = {
            "content_strategy": {
                "pillars": [
                    {"name": "tutorials"},
                    {"name": "vlogs"},
                ]
            }
        }
        await skill._infer_assumptions(_CHANNEL_ID, strategy)

        calls = storage.save_assumption.call_args_list
        statements = [c.args[0]["statement"] for c in calls]
        assert any("tutorials" in s and "vlogs" in s for s in statements)

    @pytest.mark.asyncio
    async def test_no_positioning_skips_all(self) -> None:
        storage = _make_storage()
        skill = await _init_skill(storage=storage)

        strategy = {"growth": {"timeline": "6 months"}}
        await skill._infer_assumptions(_CHANNEL_ID, strategy)
        storage.save_assumption.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confidence_values(self) -> None:
        """Niche and audience get 0.75, tone gets 0.6, pillars get 0.7."""
        storage = _make_storage()
        skill = await _init_skill(storage=storage)

        strategy = {
            "positioning": {
                "niche": "tech",
                "target_audience": "devs",
                "tone": "casual",
            },
            "content_strategy": {
                "pillars": [{"name": "tutorials"}],
            },
        }
        await skill._infer_assumptions(_CHANNEL_ID, strategy)

        calls = storage.save_assumption.call_args_list
        confidence_by_category = {}
        for c in calls:
            cat = c.args[0]["category"]
            conf = c.args[0]["confidence"]
            confidence_by_category[cat] = conf

        # niche -> category "content" with confidence 0.75 (first content entry)
        # pillars also -> category "content" with confidence 0.7 (overrides in dict)
        # audience -> 0.75
        assert confidence_by_category["audience"] == 0.75
        assert confidence_by_category["tone"] == 0.6

    @pytest.mark.asyncio
    async def test_pillars_limited_to_three(self) -> None:
        storage = _make_storage()
        skill = await _init_skill(storage=storage)

        strategy = {
            "content_strategy": {
                "pillars": [
                    {"name": "a"},
                    {"name": "b"},
                    {"name": "c"},
                    {"name": "d"},
                    {"name": "e"},
                ]
            }
        }
        await skill._infer_assumptions(_CHANNEL_ID, strategy)

        calls = storage.save_assumption.call_args_list
        assert len(calls) == 1
        statement = calls[0].args[0]["statement"]
        # Only first 3 pillars
        assert "a" in statement
        assert "b" in statement
        assert "c" in statement
        assert "d" not in statement


# ---------------------------------------------------------------------------
# 11. on_heartbeat
# ---------------------------------------------------------------------------


class TestOnHeartbeat:
    """Tests for on_heartbeat stale strategy detection."""

    @pytest.mark.asyncio
    async def test_heartbeat_no_storage_returns_empty(self) -> None:
        skill = YouTubeStrategySkill(storage=None, broker=None)
        actions = await skill.on_heartbeat(["user-1"])
        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_stale_strategy(self) -> None:
        storage = _make_storage()
        expired = datetime.utcnow() - timedelta(days=1)
        storage.get_latest_strategy = AsyncMock(
            return_value={"valid_until": expired, "strategy": {}}
        )
        skill = YouTubeStrategySkill(storage=storage, broker=_make_broker())
        await skill.initialize()

        actions = await skill.on_heartbeat([str(_TENANT_ID)])
        assert len(actions) == 1
        assert actions[0].action_type == "strategy_stale"
        assert actions[0].skill_name == "youtube_strategy"
        assert actions[0].priority == 2
        assert actions[0].data["channel_id"] == str(_CHANNEL_ID)

    @pytest.mark.asyncio
    async def test_heartbeat_fresh_strategy_no_action(self) -> None:
        storage = _make_storage()
        future = datetime.utcnow() + timedelta(days=15)
        storage.get_latest_strategy = AsyncMock(
            return_value={"valid_until": future, "strategy": {}}
        )
        skill = YouTubeStrategySkill(storage=storage, broker=_make_broker())
        await skill.initialize()

        actions = await skill.on_heartbeat([str(_TENANT_ID)])
        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_no_strategy_for_channel(self) -> None:
        storage = _make_storage()
        storage.get_latest_strategy = AsyncMock(return_value=None)
        skill = YouTubeStrategySkill(storage=storage, broker=_make_broker())
        await skill.initialize()

        actions = await skill.on_heartbeat([str(_TENANT_ID)])
        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_invalid_user_id_skipped(self) -> None:
        storage = _make_storage()
        skill = YouTubeStrategySkill(storage=storage, broker=_make_broker())
        await skill.initialize()

        actions = await skill.on_heartbeat(["not-a-uuid"])
        assert actions == []
        storage.list_channels.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_heartbeat_valid_until_string_not_datetime(self) -> None:
        """If valid_until is a string (not datetime), no action is produced
        because the isinstance(valid_until, datetime) check fails."""
        storage = _make_storage()
        storage.get_latest_strategy = AsyncMock(
            return_value={"valid_until": "2020-01-01T00:00:00", "strategy": {}}
        )
        skill = YouTubeStrategySkill(storage=storage, broker=_make_broker())
        await skill.initialize()

        actions = await skill.on_heartbeat([str(_TENANT_ID)])
        # String is not isinstance(datetime), so no action
        assert actions == []

    @pytest.mark.asyncio
    async def test_heartbeat_multiple_channels(self) -> None:
        storage = _make_storage()
        ch_id_2 = uuid4()
        storage.list_channels = AsyncMock(
            return_value=[
                {"id": _CHANNEL_ID, "channel_name": "Ch1"},
                {"id": ch_id_2, "channel_name": "Ch2"},
            ]
        )
        expired = datetime.utcnow() - timedelta(days=5)
        storage.get_latest_strategy = AsyncMock(
            return_value={"valid_until": expired, "strategy": {}}
        )
        skill = YouTubeStrategySkill(storage=storage, broker=_make_broker())
        await skill.initialize()

        actions = await skill.on_heartbeat([str(_TENANT_ID)])
        assert len(actions) == 2

    @pytest.mark.asyncio
    async def test_heartbeat_exception_returns_empty(self) -> None:
        storage = _make_storage()
        storage.list_channels = AsyncMock(side_effect=RuntimeError("db failure"))
        skill = YouTubeStrategySkill(storage=storage, broker=_make_broker())
        await skill.initialize()

        actions = await skill.on_heartbeat([str(_TENANT_ID)])
        assert actions == []


# ---------------------------------------------------------------------------
# 12. _parse_json helper
# ---------------------------------------------------------------------------


class TestParseJson:
    """Tests for the _parse_json module-level helper."""

    def test_plain_json(self) -> None:
        raw = '{"key": "value"}'
        assert _parse_json(raw) == {"key": "value"}

    def test_json_with_markdown_fence(self) -> None:
        raw = '```json\n{"key": "value"}\n```'
        assert _parse_json(raw) == {"key": "value"}

    def test_json_with_generic_fence(self) -> None:
        raw = '```\n{"key": "value"}\n```'
        assert _parse_json(raw) == {"key": "value"}

    def test_json_with_leading_whitespace(self) -> None:
        raw = '  \n  {"key": "value"}  \n  '
        assert _parse_json(raw) == {"key": "value"}

    def test_invalid_json_returns_raw_response(self) -> None:
        raw = "this is not json at all"
        result = _parse_json(raw)
        assert result == {"raw_response": raw}

    def test_invalid_json_in_fences_returns_raw(self) -> None:
        raw = "```json\nnot valid json\n```"
        result = _parse_json(raw)
        assert "raw_response" in result

    def test_nested_json(self) -> None:
        nested = {"a": {"b": [1, 2, 3]}, "c": True}
        raw = json.dumps(nested)
        assert _parse_json(raw) == nested


# ---------------------------------------------------------------------------
# 13. _serialise helper
# ---------------------------------------------------------------------------


class TestSerialise:
    """Tests for the _serialise module-level helper."""

    def test_none_returns_empty(self) -> None:
        assert _serialise(None) == {}

    def test_empty_dict_returns_empty(self) -> None:
        assert _serialise({}) == {}

    def test_datetime_converted_to_isoformat(self) -> None:
        dt = datetime(2025, 1, 15, 12, 0, 0)
        result = _serialise({"created": dt})
        assert result["created"] == "2025-01-15T12:00:00"

    def test_uuid_converted_to_string(self) -> None:
        uid = UUID("12345678-1234-5678-1234-567812345678")
        result = _serialise({"id": uid})
        assert result["id"] == "12345678-1234-5678-1234-567812345678"

    def test_plain_values_passed_through(self) -> None:
        row = {"name": "test", "count": 42, "active": True}
        assert _serialise(row) == row

    def test_mixed_types(self) -> None:
        uid = uuid4()
        dt = datetime(2025, 6, 1)
        row = {"id": uid, "created": dt, "name": "test", "count": 5}
        result = _serialise(row)
        assert result["id"] == str(uid)
        assert result["created"] == "2025-06-01T00:00:00"
        assert result["name"] == "test"
        assert result["count"] == 5


# ---------------------------------------------------------------------------
# 14. _resolve_channel_id
# ---------------------------------------------------------------------------


class TestResolveChannelId:
    """Tests for the _resolve_channel_id helper."""

    def test_valid_uuid_string(self) -> None:
        req = _req(channel_id=str(_CHANNEL_ID))
        assert _resolve_channel_id(req) == _CHANNEL_ID

    def test_uuid_object_in_context(self) -> None:
        req = SkillRequest(context={"channel_id": _CHANNEL_ID})
        assert _resolve_channel_id(req) == _CHANNEL_ID

    def test_missing_channel_id(self) -> None:
        req = SkillRequest(context={})
        assert _resolve_channel_id(req) is None

    def test_invalid_uuid_string(self) -> None:
        req = SkillRequest(context={"channel_id": "not-a-uuid"})
        assert _resolve_channel_id(req) is None

    def test_none_channel_id(self) -> None:
        req = SkillRequest(context={"channel_id": None})
        assert _resolve_channel_id(req) is None


# ---------------------------------------------------------------------------
# 15. get_system_prompt_fragment
# ---------------------------------------------------------------------------


class TestSystemPromptFragment:
    """Tests for get_system_prompt_fragment."""

    @pytest.mark.asyncio
    async def test_returns_fragment_when_ready(self) -> None:
        skill = await _init_skill()
        frag = skill.get_system_prompt_fragment("user-1")
        assert frag is not None
        assert "YouTube Strategy" in frag
        assert "Ready" in frag

    def test_returns_none_when_not_ready(self) -> None:
        skill = YouTubeStrategySkill()
        assert skill.status == SkillStatus.UNINITIALIZED
        assert skill.get_system_prompt_fragment("user-1") is None


# ---------------------------------------------------------------------------
# 16. INTENT_HANDLERS constant
# ---------------------------------------------------------------------------


class TestIntentHandlers:
    """Tests for the INTENT_HANDLERS mapping."""

    def test_all_handlers_exist_on_class(self) -> None:
        """Every handler name in INTENT_HANDLERS should be a method on the skill."""
        skill = YouTubeStrategySkill()
        for intent, handler_name in INTENT_HANDLERS.items():
            assert hasattr(skill, handler_name), (
                f"Handler {handler_name!r} for intent {intent!r} not found"
            )

    def test_handler_count(self) -> None:
        assert len(INTENT_HANDLERS) == 3


# ---------------------------------------------------------------------------
# 17. handle() — handler not implemented (line 126)
# ---------------------------------------------------------------------------


class TestHandlerNotImplemented:
    """Tests for the 'handler not implemented' branch in handle()."""

    @pytest.mark.asyncio
    async def test_handle_handler_not_implemented(self) -> None:
        """handle() should return error when getattr returns None for a valid intent."""
        skill = await _init_skill()
        req = _req(intent="yt_generate_strategy")

        # Patch the handler method to None so getattr returns None
        with patch.object(YouTubeStrategySkill, "_handle_generate", new=None):
            resp = await skill.handle(req)

        assert resp.success is False
        assert "Handler not implemented" in (resp.error or "")


# ---------------------------------------------------------------------------
# 18. _handle_strategy_history — channel_id required (line 185)
# ---------------------------------------------------------------------------


class TestStrategyHistoryChannelIdRequired:
    """Test that _handle_strategy_history returns error when channel_id missing."""

    @pytest.mark.asyncio
    async def test_strategy_history_requires_channel_id(self) -> None:
        """Should return error when channel_id is missing."""
        skill = await _init_skill()
        req = SkillRequest(intent="yt_strategy_history", context={})
        resp = await skill.handle(req)

        assert resp.success is False
        assert "channel_id required" in (resp.error or "")
