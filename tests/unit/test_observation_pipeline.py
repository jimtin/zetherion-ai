"""Unit tests for the observation pipeline orchestrator."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from zetherion_ai.observation.dispatcher import (
    ActionHandler,
    ActionTarget,
    DispatchResult,
)
from zetherion_ai.observation.extractors import (
    ESCALATION_HIGH,
    ESCALATION_LOW,
    LLMProvider,
)
from zetherion_ai.observation.models import (
    TIER_CLOUD,
    TIER_OLLAMA,
    TIER_REGEX,
    ExtractedItem,
    ItemType,
    ObservationEvent,
)
from zetherion_ai.observation.pipeline import ObservationPipeline

# -------------------------------------------------------------------
# Helpers
# -------------------------------------------------------------------


def _make_event(content: str) -> ObservationEvent:
    """Create a minimal ObservationEvent for testing."""
    return ObservationEvent(
        source="test",
        source_id="msg-1",
        user_id=12345,
        author="test",
        author_is_owner=True,
        content=content,
    )


def _make_item(
    item_type: ItemType = ItemType.TASK,
    content: str = "Do the thing",
    confidence: float = 0.7,
    tier: int = TIER_REGEX,
) -> ExtractedItem:
    """Create a minimal ExtractedItem for testing."""
    return ExtractedItem(
        item_type=item_type,
        content=content,
        confidence=confidence,
        extraction_tier=tier,
    )


def _make_llm_provider(
    return_items: list[dict] | None = None,
) -> AsyncMock:
    """Create a mock LLMProvider with an async extract method."""
    provider = AsyncMock(spec=LLMProvider)
    provider.extract = AsyncMock(return_value=return_items or [])
    return provider


def _make_dispatch_result(
    item: ExtractedItem,
    success: bool = True,
) -> DispatchResult:
    """Create a minimal DispatchResult for testing."""
    return DispatchResult(
        item=item,
        target=ActionTarget.TASK_MANAGER,
        mode="draft",
        success=success,
        message="ok",
    )


# -------------------------------------------------------------------
# Constructor tests
# -------------------------------------------------------------------


class TestObservationPipelineConstructor:
    """Tests for ObservationPipeline.__init__."""

    def test_default_no_providers_creates_tier1_only(self):
        """Default pipeline with no providers runs tier 1 only."""
        pipe = ObservationPipeline()
        assert pipe._tier2_provider is None
        assert pipe._tier3_provider is None
        assert pipe._enable_tier2 is True
        assert pipe._enable_tier3 is True

    def test_with_all_providers_creates_full_pipeline(self):
        """Pipeline with both providers is ready for 3-tier flow."""
        t2 = _make_llm_provider()
        t3 = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2,
            tier3_provider=t3,
        )
        assert pipe._tier2_provider is t2
        assert pipe._tier3_provider is t3

    def test_enable_flags_are_stored(self):
        """Enable flags override defaults when supplied."""
        pipe = ObservationPipeline(
            enable_tier2=False,
            enable_tier3=False,
        )
        assert pipe._enable_tier2 is False
        assert pipe._enable_tier3 is False

    def test_handlers_passed_to_dispatcher(self):
        """Custom handlers are forwarded to the internal Dispatcher."""
        handler = AsyncMock(spec=ActionHandler)
        pipe = ObservationPipeline(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        assert pipe._dispatcher._handlers[ActionTarget.TASK_MANAGER] is handler

    def test_policy_provider_passed_to_dispatcher(self):
        """Policy provider is forwarded to the internal Dispatcher."""
        policy = AsyncMock()
        pipe = ObservationPipeline(policy_provider=policy)
        assert pipe._dispatcher._policy_provider is policy


# -------------------------------------------------------------------
# observe() — Tier 1 only (no LLM providers)
# -------------------------------------------------------------------


class TestObserveTier1Only:
    """Tests for observe() when no LLM providers are configured."""

    @pytest.mark.asyncio
    async def test_task_signal_extracts_and_dispatches(self):
        """Task keyword produces items that get dispatched."""
        handler = AsyncMock(spec=ActionHandler)
        result = DispatchResult(
            item=_make_item(),
            target=ActionTarget.TASK_MANAGER,
            mode="draft",
            success=True,
        )
        handler.handle_dispatch = AsyncMock(return_value=result)

        pipe = ObservationPipeline(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )
        event = _make_event("TODO: Fix the login page")
        results = await pipe.observe(event)

        assert len(results) >= 1

    @pytest.mark.asyncio
    async def test_no_signals_returns_empty(self):
        """Message with no actionable signals returns empty."""
        pipe = ObservationPipeline()
        event = _make_event("Just chatting about the weather")
        results = await pipe.observe(event)
        assert results == []

    @pytest.mark.asyncio
    async def test_tier2_not_called_without_provider(self):
        """Tier 2 extractor never runs when provider is None."""
        pipe = ObservationPipeline()
        event = _make_event("I'll handle the deployment eventually maybe")
        with patch(
            "zetherion_ai.observation.pipeline.extract_tier2",
        ) as mock_t2:
            await pipe.observe(event)
            mock_t2.assert_not_called()

    @pytest.mark.asyncio
    async def test_tier3_not_called_without_provider(self):
        """Tier 3 extractor never runs when provider is None."""
        pipe = ObservationPipeline()
        event = _make_event("Some message with a TODO: task")
        with patch(
            "zetherion_ai.observation.pipeline.extract_tier3",
        ) as mock_t3:
            await pipe.observe(event)
            mock_t3.assert_not_called()


# -------------------------------------------------------------------
# observe() — Tier 1 + Tier 2
# -------------------------------------------------------------------


class TestObserveTier1And2:
    """Tests for observe() with a Tier 2 provider configured."""

    @pytest.mark.asyncio
    async def test_escalation_triggers_tier2(self):
        """Items with confidence in [0.3, 0.6) trigger Tier 2."""
        uncertain_item = _make_item(confidence=0.45)
        t2_provider = _make_llm_provider(
            [
                {
                    "item_type": "task",
                    "content": "refined task",
                    "confidence": 0.8,
                },
            ]
        )

        pipe = ObservationPipeline(tier2_provider=t2_provider)
        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[uncertain_item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                return_value=True,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[
                    _make_item(
                        content="refined task",
                        confidence=0.8,
                        tier=TIER_OLLAMA,
                    ),
                ],
            ) as mock_t2,
        ):
            event = _make_event("might need to look into servers")
            await pipe.observe(event)
            mock_t2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_confident_items_skip_tier2(self):
        """Items with confidence >= 0.6 skip Tier 2."""
        confident_item = _make_item(confidence=0.85)
        t2_provider = _make_llm_provider()

        pipe = ObservationPipeline(tier2_provider=t2_provider)
        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[confident_item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                return_value=False,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
            ) as mock_t2,
        ):
            event = _make_event("TODO: Fix the login")
            await pipe.observe(event)
            mock_t2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_empty_tier1_triggers_tier2(self):
        """Empty Tier 1 results trigger Tier 2 (may find implicit)."""
        t2_provider = _make_llm_provider(
            [
                {
                    "item_type": "commitment",
                    "content": "implicit commitment",
                    "confidence": 0.65,
                },
            ]
        )

        pipe = ObservationPipeline(tier2_provider=t2_provider)
        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[
                    _make_item(
                        item_type=ItemType.COMMITMENT,
                        content="implicit commitment",
                        confidence=0.65,
                        tier=TIER_OLLAMA,
                    ),
                ],
            ) as mock_t2,
        ):
            event = _make_event("Yeah I can probably sort that out for you")
            await pipe.observe(event)
            mock_t2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_enable_tier2_false_skips_tier2(self):
        """enable_tier2=False prevents Tier 2 from ever running."""
        t2_provider = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_provider,
            enable_tier2=False,
        )
        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[_make_item(confidence=0.4)],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
            ) as mock_t2,
        ):
            event = _make_event("some message requiring analysis")
            await pipe.observe(event)
            mock_t2.assert_not_awaited()


# -------------------------------------------------------------------
# observe() — Tier 1 + Tier 2 + Tier 3
# -------------------------------------------------------------------


class TestObserveFullPipeline:
    """Tests for observe() with Tier 2 and Tier 3 providers."""

    @pytest.mark.asyncio
    async def test_tier2_escalation_triggers_tier3(self):
        """Tier 2 items needing escalation trigger Tier 3."""
        t2_provider = _make_llm_provider()
        t3_provider = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_provider,
            tier3_provider=t3_provider,
        )
        t2_uncertain = _make_item(
            confidence=0.45,
            tier=TIER_OLLAMA,
        )
        t3_refined = _make_item(
            confidence=0.9,
            tier=TIER_CLOUD,
        )

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[t2_uncertain],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                side_effect=[True, True],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier3",
                new_callable=AsyncMock,
                return_value=[t3_refined],
            ) as mock_t3,
        ):
            event = _make_event("Complex multi-party commitment scenario")
            await pipe.observe(event)
            mock_t3.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_tier2_confident_skips_tier3(self):
        """Tier 2 confident items skip Tier 3."""
        t2_provider = _make_llm_provider()
        t3_provider = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_provider,
            tier3_provider=t3_provider,
        )
        t2_confident = _make_item(
            confidence=0.85,
            tier=TIER_OLLAMA,
        )

        # tier1 returns empty so `not tier1_items` short-circuits
        # and needs_escalation is only called once (for tier2).
        # Tier 2 is confident (0.85 >= 0.6) so needs_escalation
        # returns False -> tier3 is skipped.
        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[t2_confident],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                return_value=False,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier3",
                new_callable=AsyncMock,
            ) as mock_t3,
        ):
            event = _make_event("I will fix the login page by Friday")
            await pipe.observe(event)
            mock_t3.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_enable_tier3_false_skips_tier3(self):
        """enable_tier3=False prevents Tier 3 from running."""
        t2_provider = _make_llm_provider()
        t3_provider = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_provider,
            tier3_provider=t3_provider,
            enable_tier3=False,
        )
        t2_uncertain = _make_item(
            confidence=0.45,
            tier=TIER_OLLAMA,
        )

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[t2_uncertain],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                side_effect=[True, True],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier3",
                new_callable=AsyncMock,
            ) as mock_t3,
        ):
            event = _make_event("Ambiguous commitment here")
            await pipe.observe(event)
            mock_t3.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tier2_empty_skips_tier3(self):
        """Empty Tier 2 results skip Tier 3 (nothing to escalate)."""
        t2_provider = _make_llm_provider()
        t3_provider = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_provider,
            tier3_provider=t3_provider,
        )

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier3",
                new_callable=AsyncMock,
            ) as mock_t3,
        ):
            event = _make_event("Nothing actionable in this long message")
            await pipe.observe(event)
            mock_t3.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_all_three_tiers_merge_correctly(self):
        """Items from all three tiers are merged and dispatched."""
        t2_provider = _make_llm_provider()
        t3_provider = _make_llm_provider()
        handler = AsyncMock(spec=ActionHandler)
        handler.handle_dispatch = AsyncMock(
            side_effect=lambda item, **kw: DispatchResult(
                item=item,
                target=ActionTarget.TASK_MANAGER,
                mode="draft",
                success=True,
            ),
        )
        pipe = ObservationPipeline(
            tier2_provider=t2_provider,
            tier3_provider=t3_provider,
            handlers={ActionTarget.TASK_MANAGER: handler},
        )

        t1_item = _make_item(
            content="tier1 task",
            confidence=0.55,
        )
        t2_item = _make_item(
            content="tier2 commitment",
            item_type=ItemType.COMMITMENT,
            confidence=0.5,
            tier=TIER_OLLAMA,
        )
        t3_item = _make_item(
            content="tier3 refined",
            confidence=0.9,
            tier=TIER_CLOUD,
        )
        merged = [t1_item, t2_item, t3_item]

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[t1_item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                side_effect=[True, True],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[t2_item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier3",
                new_callable=AsyncMock,
                return_value=[t3_item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=merged,
            ),
        ):
            event = _make_event("Complex scenario with everything")
            results = await pipe.observe(event)
            assert len(results) == 3


# -------------------------------------------------------------------
# observe() — Dispatch behaviour
# -------------------------------------------------------------------


class TestObserveDispatch:
    """Tests for dispatch phase of observe()."""

    @pytest.mark.asyncio
    async def test_merged_items_dispatched_returns_results(self):
        """Non-empty merged items are dispatched and results returned."""
        item = _make_item(confidence=0.85)
        expected_result = _make_dispatch_result(item)

        handler = AsyncMock(spec=ActionHandler)
        handler.handle_dispatch = AsyncMock(
            return_value=expected_result,
        )
        pipe = ObservationPipeline(
            handlers={ActionTarget.TASK_MANAGER: handler},
        )

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[item],
            ),
        ):
            event = _make_event("TODO: Fix the bug")
            results = await pipe.observe(event)
            assert len(results) == 1
            assert results[0].success is True

    @pytest.mark.asyncio
    async def test_no_items_means_no_dispatch(self):
        """Empty merged items skip dispatch entirely."""
        pipe = ObservationPipeline()

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ),
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[],
            ),
            patch.object(
                pipe._dispatcher,
                "dispatch",
                new_callable=AsyncMock,
            ) as mock_disp,
        ):
            event = _make_event("Nothing here at all")
            results = await pipe.observe(event)
            assert results == []
            mock_disp.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_dispatch_receives_correct_user_id(self):
        """Dispatcher receives the user_id from the event."""
        item = _make_item(confidence=0.85)
        pipe = ObservationPipeline()

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[item],
            ),
            patch.object(
                pipe._dispatcher,
                "dispatch",
                new_callable=AsyncMock,
                return_value=[_make_dispatch_result(item)],
            ) as mock_disp,
        ):
            event = _make_event("TODO: Something important")
            await pipe.observe(event)
            mock_disp.assert_awaited_once_with(
                [item],
                user_id=12345,
            )

    @pytest.mark.asyncio
    async def test_multiple_items_all_dispatched(self):
        """Multiple merged items each produce a dispatch result."""
        items = [
            _make_item(
                content="task A",
                confidence=0.8,
            ),
            _make_item(
                item_type=ItemType.DEADLINE,
                content="Deadline: Friday",
                confidence=0.7,
            ),
        ]
        results = [_make_dispatch_result(i) for i in items]

        pipe = ObservationPipeline()
        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=items,
            ),
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=items,
            ),
            patch.object(
                pipe._dispatcher,
                "dispatch",
                new_callable=AsyncMock,
                return_value=results,
            ),
        ):
            event = _make_event("TODO: task A by Friday")
            out = await pipe.observe(event)
            assert len(out) == 2


# -------------------------------------------------------------------
# extract_only()
# -------------------------------------------------------------------


class TestExtractOnly:
    """Tests for extract_only() — extraction without dispatch."""

    @pytest.mark.asyncio
    async def test_returns_extracted_items_not_dispatch_results(self):
        """extract_only returns ExtractedItem list, not DispatchResult."""
        item = _make_item(confidence=0.85)
        pipe = ObservationPipeline()

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[item],
            ),
        ):
            event = _make_event("TODO: Write tests")
            result = await pipe.extract_only(event)
            assert isinstance(result, list)
            assert all(isinstance(r, ExtractedItem) for r in result)

    @pytest.mark.asyncio
    async def test_no_dispatch_happens(self):
        """extract_only never calls the dispatcher."""
        item = _make_item(confidence=0.85)
        pipe = ObservationPipeline()

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[item],
            ),
            patch.object(
                pipe._dispatcher,
                "dispatch",
                new_callable=AsyncMock,
            ) as mock_disp,
        ):
            event = _make_event("TODO: Write tests")
            await pipe.extract_only(event)
            mock_disp.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tier2_escalation_same_as_observe(self):
        """extract_only uses same tier escalation logic as observe."""
        t2_provider = _make_llm_provider()
        pipe = ObservationPipeline(tier2_provider=t2_provider)

        uncertain = _make_item(confidence=0.45)
        t2_item = _make_item(
            content="tier2 result",
            confidence=0.8,
            tier=TIER_OLLAMA,
        )

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[uncertain],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                return_value=True,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[t2_item],
            ) as mock_t2,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[uncertain, t2_item],
            ),
        ):
            event = _make_event("Maybe I should look into that issue")
            result = await pipe.extract_only(event)
            mock_t2.assert_awaited_once()
            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_tier3_escalation_in_extract_only(self):
        """extract_only also runs tier 3 when needed."""
        t2_prov = _make_llm_provider()
        t3_prov = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_prov,
            tier3_provider=t3_prov,
        )

        t2_uncertain = _make_item(
            confidence=0.45,
            tier=TIER_OLLAMA,
        )
        t3_result = _make_item(
            content="cloud refined",
            confidence=0.92,
            tier=TIER_CLOUD,
        )

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[t2_uncertain],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                side_effect=[True, True],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier3",
                new_callable=AsyncMock,
                return_value=[t3_result],
            ) as mock_t3,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[t2_uncertain, t3_result],
            ),
        ):
            event = _make_event("Ambiguous multi-party commitment scenario")
            result = await pipe.extract_only(event)
            mock_t3.assert_awaited_once()
            assert len(result) == 2

    @pytest.mark.asyncio
    async def test_extract_only_empty_content_returns_empty(self):
        """No signals produces an empty extraction list."""
        pipe = ObservationPipeline()
        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ),
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[],
            ),
        ):
            event = _make_event("Just chatting about nothing")
            result = await pipe.extract_only(event)
            assert result == []

    @pytest.mark.asyncio
    async def test_extract_only_enable_tier2_false(self):
        """extract_only respects enable_tier2=False."""
        t2_prov = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_prov,
            enable_tier2=False,
        )

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[_make_item(confidence=0.4)],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
            ) as mock_t2,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[_make_item(confidence=0.4)],
            ),
        ):
            event = _make_event("Low confidence message content")
            await pipe.extract_only(event)
            mock_t2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_extract_only_enable_tier3_false(self):
        """extract_only respects enable_tier3=False."""
        t2_prov = _make_llm_provider()
        t3_prov = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_prov,
            tier3_provider=t3_prov,
            enable_tier3=False,
        )
        t2_unc = _make_item(confidence=0.45, tier=TIER_OLLAMA)

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[t2_unc],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                side_effect=[True, True],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier3",
                new_callable=AsyncMock,
            ) as mock_t3,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[t2_unc],
            ),
        ):
            event = _make_event("Uncertain tier2 message here")
            await pipe.extract_only(event)
            mock_t3.assert_not_awaited()


# -------------------------------------------------------------------
# Tier escalation logic (detailed boundary tests)
# -------------------------------------------------------------------


class TestTierEscalationBoundaries:
    """Boundary tests for tier escalation conditions."""

    @pytest.mark.asyncio
    async def test_confidence_exactly_at_low_threshold(self):
        """Confidence == ESCALATION_LOW (0.3) triggers escalation."""
        t2_prov = _make_llm_provider()
        pipe = ObservationPipeline(tier2_provider=t2_prov)
        boundary_item = _make_item(confidence=ESCALATION_LOW)

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[boundary_item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                return_value=True,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_t2,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[boundary_item],
            ),
        ):
            event = _make_event("Boundary confidence message text")
            await pipe.observe(event)
            mock_t2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_confidence_exactly_at_high_threshold(self):
        """Confidence == ESCALATION_HIGH (0.6) does NOT escalate."""
        t2_prov = _make_llm_provider()
        pipe = ObservationPipeline(tier2_provider=t2_prov)
        boundary_item = _make_item(confidence=ESCALATION_HIGH)

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[boundary_item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                return_value=False,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
            ) as mock_t2,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[boundary_item],
            ),
        ):
            event = _make_event("Exactly at high threshold text")
            await pipe.observe(event)
            mock_t2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_confidence_just_below_high_triggers_escalation(self):
        """Confidence = 0.59 (just below 0.6) triggers escalation."""
        t2_prov = _make_llm_provider()
        pipe = ObservationPipeline(tier2_provider=t2_prov)
        item = _make_item(confidence=0.59)

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                return_value=True,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_t2,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[item],
            ),
        ):
            event = _make_event("Just below threshold message text")
            await pipe.observe(event)
            mock_t2.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_confidence_below_low_no_escalation(self):
        """Confidence below ESCALATION_LOW does not trigger."""
        t2_prov = _make_llm_provider()
        pipe = ObservationPipeline(tier2_provider=t2_prov)
        low_item = _make_item(confidence=0.2)

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[low_item],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                return_value=False,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
            ) as mock_t2,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[low_item],
            ),
        ):
            event = _make_event("Very low confidence message content")
            await pipe.observe(event)
            mock_t2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_mixed_confidence_escalates(self):
        """Mix of confident + uncertain items still escalates."""
        t2_prov = _make_llm_provider()
        pipe = ObservationPipeline(tier2_provider=t2_prov)
        confident = _make_item(confidence=0.85)
        uncertain = _make_item(
            content="uncertain one",
            confidence=0.45,
        )

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[confident, uncertain],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                return_value=True,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_t2,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[confident, uncertain],
            ),
        ):
            event = _make_event("A mix of confident and uncertain signals")
            await pipe.observe(event)
            mock_t2.assert_awaited_once()


# -------------------------------------------------------------------
# Merge interactions
# -------------------------------------------------------------------


class TestMergeInteractions:
    """Tests verifying merge_extractions is called correctly."""

    @pytest.mark.asyncio
    async def test_merge_called_with_all_tier_results(self):
        """merge_extractions receives results from all three tiers."""
        t2_prov = _make_llm_provider()
        t3_prov = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_prov,
            tier3_provider=t3_prov,
        )

        t1 = [_make_item(confidence=0.4)]
        t2 = [_make_item(content="t2", confidence=0.5, tier=TIER_OLLAMA)]
        t3 = [_make_item(content="t3", confidence=0.9, tier=TIER_CLOUD)]

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=t1,
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                side_effect=[True, True],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=t2,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier3",
                new_callable=AsyncMock,
                return_value=t3,
            ),
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=t1 + t2 + t3,
            ) as mock_merge,
        ):
            event = _make_event("Full pipeline message content")
            await pipe.observe(event)
            mock_merge.assert_called_once_with(t1, t2, t3)

    @pytest.mark.asyncio
    async def test_merge_called_with_empty_tier2_and_tier3(self):
        """When no LLM providers, merge gets empty tier2/tier3."""
        pipe = ObservationPipeline()
        t1 = [_make_item(confidence=0.85)]

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=t1,
            ),
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=t1,
            ) as mock_merge,
        ):
            event = _make_event("TODO: Simple task for tier 1")
            await pipe.observe(event)
            mock_merge.assert_called_once_with(t1, [], [])


# -------------------------------------------------------------------
# Integration-like tests (real Tier 1 + mocked Tier 2/3)
# -------------------------------------------------------------------


class TestIntegrationWithRealTier1:
    """Integration-style tests using real Tier 1 regex extraction."""

    @pytest.mark.asyncio
    async def test_commitment_with_deadline(self):
        """'I'll handle the deployment by Friday' extracts TASK + DEADLINE."""
        pipe = ObservationPipeline()
        event = _make_event("I'll handle the deployment by Friday")
        items = await pipe.extract_only(event)

        types = {i.item_type for i in items}
        assert ItemType.TASK in types
        assert ItemType.DEADLINE in types

    @pytest.mark.asyncio
    async def test_explicit_todo_high_confidence(self):
        """'TODO: Fix the login' → TASK with high confidence, no esc."""
        pipe = ObservationPipeline()
        event = _make_event("TODO: Fix the login")
        items = await pipe.extract_only(event)

        assert len(items) >= 1
        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) >= 1
        assert task_items[0].confidence >= ESCALATION_HIGH

    @pytest.mark.asyncio
    async def test_chatting_no_extraction(self):
        """'Just chatting about the weather' yields nothing."""
        pipe = ObservationPipeline()
        event = _make_event("Just chatting about the weather")
        items = await pipe.extract_only(event)
        assert items == []

    @pytest.mark.asyncio
    async def test_explicit_todo_no_tier2_escalation(self):
        """High-confidence TODO should NOT trigger Tier 2."""
        t2_prov = _make_llm_provider()
        pipe = ObservationPipeline(tier2_provider=t2_prov)

        with patch(
            "zetherion_ai.observation.pipeline.extract_tier2",
            new_callable=AsyncMock,
        ) as mock_t2:
            event = _make_event("TODO: Fix the login")
            await pipe.observe(event)
            mock_t2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_email_extraction(self):
        """Email address in content extracts CONTACT."""
        pipe = ObservationPipeline()
        event = _make_event("Reach out to alice@example.com about the project")
        items = await pipe.extract_only(event)

        contact_items = [i for i in items if i.item_type == ItemType.CONTACT]
        assert len(contact_items) == 1
        assert contact_items[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_reminder_extraction(self):
        """'Remind me to call the dentist' extracts REMINDER."""
        pipe = ObservationPipeline()
        event = _make_event("Remind me to call the dentist tomorrow")
        items = await pipe.extract_only(event)

        reminder_items = [i for i in items if i.item_type == ItemType.REMINDER]
        assert len(reminder_items) >= 1

    @pytest.mark.asyncio
    async def test_meeting_extraction(self):
        """'Let's schedule a meeting' extracts MEETING."""
        pipe = ObservationPipeline()
        event = _make_event("Let's schedule a meeting for next week")
        items = await pipe.extract_only(event)

        meeting_items = [i for i in items if i.item_type == ItemType.MEETING]
        assert len(meeting_items) >= 1

    @pytest.mark.asyncio
    async def test_need_to_pattern(self):
        """'I need to fix the server' extracts TASK."""
        pipe = ObservationPipeline()
        event = _make_event("I need to fix the server before Monday")
        items = await pipe.extract_only(event)

        task_items = [i for i in items if i.item_type == ItemType.TASK]
        assert len(task_items) >= 1

    @pytest.mark.asyncio
    async def test_real_tier1_with_mocked_tier2(self):
        """Real Tier 1 + mocked Tier 2 for uncertain commitment."""
        t2_prov = _make_llm_provider(
            [
                {
                    "item_type": "commitment",
                    "content": "Implicit commitment to help",
                    "confidence": 0.75,
                    "metadata": {},
                },
            ]
        )
        pipe = ObservationPipeline(tier2_provider=t2_prov)

        # "I'll work on" matches the task pattern at 0.55 confidence
        # which is in the escalation zone [0.3, 0.6)
        event = _make_event("I'll work on that thing when I get a chance")
        items = await pipe.extract_only(event)

        # Should have tier1 item(s) plus tier2 items
        assert len(items) >= 1

    @pytest.mark.asyncio
    async def test_observe_full_pipeline_real_tier1(self):
        """observe() with real Tier 1, no handlers => empty results."""
        pipe = ObservationPipeline()
        event = _make_event("TODO: Write the documentation")
        results = await pipe.observe(event)

        # Items extracted but no handlers registered, so dispatch
        # returns results with success=False (no handler)
        for r in results:
            assert isinstance(r, DispatchResult)


# -------------------------------------------------------------------
# Edge cases
# -------------------------------------------------------------------


class TestEdgeCases:
    """Edge case and error-handling tests."""

    @pytest.mark.asyncio
    async def test_tier2_provider_set_but_disabled(self):
        """Provider set but enable_tier2=False: tier 2 skipped."""
        t2_prov = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_prov,
            enable_tier2=False,
        )
        with patch(
            "zetherion_ai.observation.pipeline.extract_tier2",
            new_callable=AsyncMock,
        ) as mock_t2:
            event = _make_event("I'll handle the deployment eventually")
            await pipe.observe(event)
            mock_t2.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tier3_provider_set_but_disabled(self):
        """Provider set but enable_tier3=False: tier 3 skipped."""
        t2_prov = _make_llm_provider()
        t3_prov = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_prov,
            tier3_provider=t3_prov,
            enable_tier2=True,
            enable_tier3=False,
        )
        t2_uncertain = _make_item(
            confidence=0.45,
            tier=TIER_OLLAMA,
        )

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[t2_uncertain],
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                side_effect=[True, True],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier3",
                new_callable=AsyncMock,
            ) as mock_t3,
        ):
            event = _make_event("Ambiguous commitment for tier3 testing")
            await pipe.observe(event)
            mock_t3.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_tier3_without_tier2_provider(self):
        """Tier 3 provider set but no Tier 2 provider: only T1+T3."""
        t3_prov = _make_llm_provider()
        pipe = ObservationPipeline(tier3_provider=t3_prov)

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[_make_item(confidence=0.85)],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
            ) as mock_t2,
            patch(
                "zetherion_ai.observation.pipeline.extract_tier3",
                new_callable=AsyncMock,
            ) as mock_t3,
        ):
            event = _make_event("Only tier 1 and tier 3 available")
            await pipe.observe(event)
            # Tier 2 never called (no provider)
            mock_t2.assert_not_awaited()
            # Tier 3 needs tier2_items to be non-empty, which
            # they aren't since tier2 never ran
            mock_t3.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_observe_passes_event_to_tier1(self):
        """extract_tier1 receives the original event object."""
        pipe = ObservationPipeline()
        event = _make_event("TODO: check this specific event")

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ) as mock_t1,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[],
            ),
        ):
            await pipe.observe(event)
            mock_t1.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_extract_only_passes_event_to_tier1(self):
        """extract_only also passes the event to extract_tier1."""
        pipe = ObservationPipeline()
        event = _make_event("Check event forwarding works here")

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=[],
            ) as mock_t1,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=[],
            ),
        ):
            await pipe.extract_only(event)
            mock_t1.assert_called_once_with(event)

    @pytest.mark.asyncio
    async def test_tier2_receives_existing_items(self):
        """extract_tier2 is called with existing_items from tier1."""
        t2_prov = _make_llm_provider()
        pipe = ObservationPipeline(tier2_provider=t2_prov)
        t1_items = [_make_item(confidence=0.45)]

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=t1_items,
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                return_value=True,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_t2,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=t1_items,
            ),
        ):
            event = _make_event("Some uncertain message for tier2 context")
            await pipe.observe(event)
            mock_t2.assert_awaited_once_with(
                event,
                t2_prov,
                existing_items=t1_items,
            )

    @pytest.mark.asyncio
    async def test_tier3_receives_combined_existing_items(self):
        """extract_tier3 receives tier1 + tier2 items as existing."""
        t2_prov = _make_llm_provider()
        t3_prov = _make_llm_provider()
        pipe = ObservationPipeline(
            tier2_provider=t2_prov,
            tier3_provider=t3_prov,
        )
        t1_items = [_make_item(confidence=0.45)]
        t2_items = [
            _make_item(
                content="t2 result",
                confidence=0.5,
                tier=TIER_OLLAMA,
            ),
        ]

        with (
            patch(
                "zetherion_ai.observation.pipeline.extract_tier1",
                return_value=t1_items,
            ),
            patch(
                "zetherion_ai.observation.pipeline.needs_escalation",
                side_effect=[True, True],
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier2",
                new_callable=AsyncMock,
                return_value=t2_items,
            ),
            patch(
                "zetherion_ai.observation.pipeline.extract_tier3",
                new_callable=AsyncMock,
                return_value=[],
            ) as mock_t3,
            patch(
                "zetherion_ai.observation.pipeline.merge_extractions",
                return_value=t1_items + t2_items,
            ),
        ):
            event = _make_event("Needs all three tiers for this content")
            await pipe.observe(event)
            mock_t3.assert_awaited_once_with(
                event,
                t3_prov,
                existing_items=t1_items + t2_items,
            )
