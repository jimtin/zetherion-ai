"""Main observation pipeline orchestrator.

Coordinates the full observation flow:
1. Receive ObservationEvent from any source adapter
2. Run tiered extraction (Tier 1 → Tier 2 → Tier 3)
3. Merge and deduplicate results
4. Dispatch items to action targets
"""

from __future__ import annotations

from zetherion_ai.logging import get_logger
from zetherion_ai.observation.dispatcher import (
    ActionHandler,
    ActionTarget,
    Dispatcher,
    DispatchResult,
    PolicyProvider,
)
from zetherion_ai.observation.extractors import (
    LLMProvider,
    extract_tier1,
    extract_tier2,
    extract_tier3,
    merge_extractions,
    needs_escalation,
)
from zetherion_ai.observation.models import ExtractedItem, ObservationEvent

log = get_logger("zetherion_ai.observation.pipeline")


class ObservationPipeline:
    """Orchestrates the full observation → extraction → dispatch flow.

    Accepts optional LLM providers for Tier 2 and Tier 3 extraction.
    When providers are not set, only Tier 1 (regex) extraction runs.
    """

    def __init__(
        self,
        *,
        tier2_provider: LLMProvider | None = None,
        tier3_provider: LLMProvider | None = None,
        handlers: dict[ActionTarget, ActionHandler] | None = None,
        policy_provider: PolicyProvider | None = None,
        enable_tier2: bool = True,
        enable_tier3: bool = True,
    ) -> None:
        self._tier2_provider = tier2_provider
        self._tier3_provider = tier3_provider
        self._enable_tier2 = enable_tier2
        self._enable_tier3 = enable_tier3
        self._dispatcher = Dispatcher(
            handlers=handlers,
            policy_provider=policy_provider,
        )

    async def observe(self, event: ObservationEvent) -> list[DispatchResult]:
        """Process an observation event through the full pipeline.

        Args:
            event: The observation event to process.

        Returns:
            List of dispatch results from acting on extracted items.
        """
        log.info(
            "pipeline_observe_start",
            source=event.source,
            source_id=event.source_id,
            user_id=event.user_id,
            content_length=len(event.content),
        )

        # Step 1: Tier 1 extraction (always runs)
        tier1_items = extract_tier1(event)
        log.debug(
            "tier1_complete",
            source_id=event.source_id,
            item_count=len(tier1_items),
        )

        # Step 2: Tier 2 extraction (if enabled and provider available)
        tier2_items: list[ExtractedItem] = []
        if (
            self._enable_tier2
            and self._tier2_provider is not None
            and (not tier1_items or needs_escalation(tier1_items))
        ):
            tier2_items = await extract_tier2(
                event,
                self._tier2_provider,
                existing_items=tier1_items,
            )
            log.debug(
                "tier2_complete",
                source_id=event.source_id,
                item_count=len(tier2_items),
            )

        # Step 3: Tier 3 extraction (if enabled and T2 results need escalation)
        tier3_items: list[ExtractedItem] = []
        if (
            self._enable_tier3
            and self._tier3_provider is not None
            and tier2_items
            and needs_escalation(tier2_items)
        ):
            tier3_items = await extract_tier3(
                event,
                self._tier3_provider,
                existing_items=tier1_items + tier2_items,
            )
            log.debug(
                "tier3_complete",
                source_id=event.source_id,
                item_count=len(tier3_items),
            )

        # Step 4: Merge results
        merged = merge_extractions(tier1_items, tier2_items, tier3_items)
        log.info(
            "extraction_complete",
            source_id=event.source_id,
            tier1=len(tier1_items),
            tier2=len(tier2_items),
            tier3=len(tier3_items),
            merged=len(merged),
        )

        if not merged:
            return []

        # Step 5: Dispatch
        results = await self._dispatcher.dispatch(merged, user_id=event.user_id)

        return results

    async def extract_only(self, event: ObservationEvent) -> list[ExtractedItem]:
        """Run extraction without dispatching.

        Useful for preview/dry-run mode.
        """
        tier1_items = extract_tier1(event)

        tier2_items: list[ExtractedItem] = []
        if (
            self._enable_tier2
            and self._tier2_provider is not None
            and (not tier1_items or needs_escalation(tier1_items))
        ):
            tier2_items = await extract_tier2(
                event, self._tier2_provider, existing_items=tier1_items
            )

        tier3_items: list[ExtractedItem] = []
        if (
            self._enable_tier3
            and self._tier3_provider is not None
            and tier2_items
            and needs_escalation(tier2_items)
        ):
            tier3_items = await extract_tier3(
                event,
                self._tier3_provider,
                existing_items=tier1_items + tier2_items,
            )

        return merge_extractions(tier1_items, tier2_items, tier3_items)
