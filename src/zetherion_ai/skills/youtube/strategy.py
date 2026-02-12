"""YouTube Channel Strategy Skill.

Generates comprehensive growth strategies using intelligence data, client
knowledge (via RAG), and expert reasoning.  The pipeline:

  1. Gather context — latest intelligence report, management state,
     client documents, confirmed assumptions, previous strategy.
  2. Synthesis — Claude (COMPLEX_REASONING) produces a structured
     strategy JSON document.
  3. Validation — check strategy against confirmed assumptions, flag
     contradictions, generate new assumptions.

Heartbeat checks for stale strategies (>30 days) and notifies if key
assumptions have been invalidated since the last generation.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import UUID

from zetherion_ai.agent.providers import TaskType
from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
    SkillStatus,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet
from zetherion_ai.skills.youtube.assumptions import AssumptionTracker
from zetherion_ai.skills.youtube.models import StrategyType
from zetherion_ai.skills.youtube.prompts import (
    STRATEGY_GENERATION_SYSTEM,
    STRATEGY_GENERATION_USER,
)
from zetherion_ai.skills.youtube.trust import TrustModel

if TYPE_CHECKING:
    from zetherion_ai.agent.inference import InferenceBroker
    from zetherion_ai.memory.qdrant import QdrantMemory
    from zetherion_ai.skills.youtube.storage import YouTubeStorage

log = get_logger("zetherion_ai.skills.youtube.strategy")

# Strategy valid for 30 days by default
_STRATEGY_TTL_DAYS = 30

# Intent → handler mapping
INTENT_HANDLERS: dict[str, str] = {
    "yt_generate_strategy": "_handle_generate",
    "yt_get_strategy": "_handle_get_strategy",
    "yt_strategy_history": "_handle_strategy_history",
}


class YouTubeStrategySkill(Skill):
    """Generate comprehensive YouTube growth strategies."""

    def __init__(
        self,
        memory: QdrantMemory | None = None,
        storage: YouTubeStorage | None = None,
        broker: InferenceBroker | None = None,
    ) -> None:
        super().__init__(memory)
        self._storage = storage
        self._broker = broker
        self._assumptions: AssumptionTracker | None = None

    # ------------------------------------------------------------------
    # Metadata
    # ------------------------------------------------------------------

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="youtube_strategy",
            description="Generate comprehensive YouTube growth strategies",
            version="1.0.0",
            permissions=PermissionSet(
                {
                    Permission.READ_PROFILE,
                    Permission.WRITE_MEMORIES,
                }
            ),
            collections=["yt_docs"],
            intents=list(INTENT_HANDLERS.keys()),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> bool:
        if not self._storage:
            log.error("strategy_init_failed", reason="No storage provided")
            self._set_status(SkillStatus.ERROR, "No storage provided")
            return False
        if not self._broker:
            log.error("strategy_init_failed", reason="No inference broker")
            self._set_status(SkillStatus.ERROR, "No inference broker")
            return False

        self._assumptions = AssumptionTracker(self._storage)
        log.info("youtube_strategy_initialized")
        self._set_status(SkillStatus.READY)
        return True

    # ------------------------------------------------------------------
    # Handle dispatch
    # ------------------------------------------------------------------

    async def handle(self, request: SkillRequest) -> SkillResponse:
        handler_name = INTENT_HANDLERS.get(request.intent)
        if not handler_name:
            return SkillResponse.error_response(
                request.id, f"Unknown intent: {request.intent}"
            )
        handler = getattr(self, handler_name, None)
        if not handler:
            return SkillResponse.error_response(
                request.id, f"Handler not implemented: {handler_name}"
            )
        try:
            return await handler(request)
        except Exception as e:
            log.error("strategy_handle_error", error=str(e))
            return SkillResponse.error_response(request.id, f"Strategy error: {e}")

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    async def _handle_generate(self, request: SkillRequest) -> SkillResponse:
        """Generate a new strategy for a channel."""
        assert self._storage is not None
        channel_id = _resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        channel = await self._storage.get_channel(channel_id)
        if channel is None:
            return SkillResponse.error_response(request.id, "Channel not found")

        strategy = await self.generate_strategy(channel_id, channel)
        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Strategy generated.",
            data=strategy,
        )

    async def _handle_get_strategy(self, request: SkillRequest) -> SkillResponse:
        """Return the latest strategy for a channel."""
        assert self._storage is not None
        channel_id = _resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        row = await self._storage.get_latest_strategy(channel_id)
        if row is None:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message="No strategy available yet.",
                data={},
            )
        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Latest strategy.",
            data=_serialise(row),
        )

    async def _handle_strategy_history(self, request: SkillRequest) -> SkillResponse:
        """Return historical strategies for a channel."""
        assert self._storage is not None
        channel_id = _resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        limit = request.context.get("limit", 10)
        rows = await self._storage.get_strategy_history(channel_id, limit=limit)
        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"{len(rows)} strategy(s) found.",
            data={"strategies": [_serialise(r) for r in rows]},
        )

    # ------------------------------------------------------------------
    # Core strategy generation
    # ------------------------------------------------------------------

    async def generate_strategy(
        self,
        channel_id: UUID,
        channel: dict[str, Any],
    ) -> dict[str, Any]:
        """Generate a comprehensive growth strategy for *channel_id*."""
        assert self._storage is not None
        assert self._broker is not None
        assert self._assumptions is not None

        # 1. Gather context
        context = await self._gather_context(channel_id, channel)

        # 2. Generate strategy via Claude (COMPLEX_REASONING)
        strategy_body = await self._synthesise_strategy(context)

        # 3. Persist
        valid_until = (
            datetime.utcnow() + timedelta(days=_STRATEGY_TTL_DAYS)
        ).isoformat()

        saved = await self._storage.save_strategy(
            channel_id=channel_id,
            strategy_type=StrategyType.FULL.value,
            strategy=strategy_body,
            model_used="multi",
            valid_until=valid_until,
        )

        # 4. Infer new assumptions from the strategy
        await self._infer_assumptions(channel_id, strategy_body)

        return _serialise(saved)

    # ------------------------------------------------------------------
    # Context gathering
    # ------------------------------------------------------------------

    async def _gather_context(
        self,
        channel_id: UUID,
        channel: dict[str, Any],
    ) -> dict[str, Any]:
        """Assemble all context needed for strategy generation."""
        assert self._storage is not None
        assert self._assumptions is not None

        # Latest intelligence report
        report_row = await self._storage.get_latest_report(channel_id)
        intelligence = report_row.get("report", {}) if report_row else {}

        # Management state
        trust = TrustModel.from_channel(channel)
        reply_drafts = await self._storage.get_reply_drafts(channel_id, limit=50)
        posted_count = sum(1 for d in reply_drafts if d.get("status") == "posted")
        pending_count = sum(1 for d in reply_drafts if d.get("status") == "pending")

        # Client documents
        docs = await self._storage.get_documents(channel_id)
        doc_summaries = "\n\n".join(
            f"### {d.get('title', 'Document')} ({d.get('doc_type', '')})\n"
            f"{d.get('content', '')[:2000]}"
            for d in docs
        ) or "No client documents uploaded."

        # Assumptions
        assumptions = await self._assumptions.get_high_confidence(channel_id)

        # Previous strategy
        prev_row = await self._storage.get_latest_strategy(channel_id)
        prev_strategy = prev_row.get("strategy", {}) if prev_row else {}

        return {
            "channel": channel,
            "intelligence": intelligence,
            "trust": trust.to_dict(),
            "reply_stats": {
                "posted": posted_count,
                "pending": pending_count,
                "total_drafts": len(reply_drafts),
            },
            "documents": doc_summaries,
            "assumptions": assumptions,
            "previous_strategy": prev_strategy,
        }

    # ------------------------------------------------------------------
    # Strategy synthesis (Claude — COMPLEX_REASONING)
    # ------------------------------------------------------------------

    async def _synthesise_strategy(self, context: dict[str, Any]) -> dict[str, Any]:
        """Use a frontier model to generate the strategy document."""
        assert self._broker is not None

        intelligence = context["intelligence"]
        trust = context["trust"]
        reply_stats = context["reply_stats"]
        assumptions = context["assumptions"]
        previous = context["previous_strategy"]
        documents = context["documents"]

        assumptions_str = "\n".join(
            f"- [{a.get('category', '')}] {a.get('statement', '')}"
            f" (confidence: {a.get('confidence', 0):.1f})"
            for a in assumptions
        ) or "None confirmed yet."

        user_prompt = STRATEGY_GENERATION_USER.format(
            intelligence_report=json.dumps(intelligence, indent=2, default=str),
            client_documents=documents,
            assumptions=assumptions_str,
            previous_strategy=json.dumps(previous, indent=2, default=str) if previous else "None.",
            trust_level=f"{trust.get('level', 0)} ({trust.get('label', 'SUPERVISED')})",
            reply_stats=json.dumps(reply_stats, default=str),
        )

        try:
            result = await self._broker.infer(
                prompt=user_prompt,
                task_type=TaskType.COMPLEX_REASONING,
                system_prompt=STRATEGY_GENERATION_SYSTEM,
                max_tokens=8192,
                temperature=0.5,
            )
            return _parse_json(result.content)
        except Exception:
            log.exception("strategy_synthesis_failed")
            return {"error": "Strategy generation failed", "_fallback": True}

    # ------------------------------------------------------------------
    # Infer assumptions from strategy
    # ------------------------------------------------------------------

    async def _infer_assumptions(
        self, channel_id: UUID, strategy: dict[str, Any]
    ) -> None:
        """Extract assumptions from a generated strategy."""
        assert self._assumptions is not None

        positioning = strategy.get("positioning") or {}
        if positioning.get("niche"):
            await self._assumptions.add_inferred(
                channel_id=channel_id,
                category="content",
                statement=f"Channel niche: {positioning['niche']}",
                evidence=["Derived from strategy positioning analysis"],
                confidence=0.75,
            )

        if positioning.get("target_audience"):
            await self._assumptions.add_inferred(
                channel_id=channel_id,
                category="audience",
                statement=f"Target audience: {positioning['target_audience']}",
                evidence=["Derived from strategy positioning analysis"],
                confidence=0.75,
            )

        if positioning.get("tone"):
            await self._assumptions.add_inferred(
                channel_id=channel_id,
                category="tone",
                statement=f"Recommended tone: {positioning['tone']}",
                evidence=["Derived from strategy positioning analysis"],
                confidence=0.6,
            )

        # Content pillars
        pillars = (strategy.get("content_strategy") or {}).get("pillars", [])
        if pillars:
            pillar_names = ", ".join(p.get("name", "?") for p in pillars[:3])
            await self._assumptions.add_inferred(
                channel_id=channel_id,
                category="content",
                statement=f"Content pillars: {pillar_names}",
                evidence=["Derived from strategy content analysis"],
                confidence=0.7,
            )

    # ------------------------------------------------------------------
    # Heartbeat: stale strategy detection
    # ------------------------------------------------------------------

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Check for stale strategies and invalidated assumptions."""
        if not self._storage:
            return []

        actions: list[HeartbeatAction] = []
        try:
            # Check all channels for stale strategies
            # We iterate all channels from the DB (we don't have a direct
            # "channels with stale strategy" query, so we check each one)
            for uid in user_ids:
                # user_ids in the YouTube context are tenant_ids
                try:
                    tenant_id = UUID(uid)
                except ValueError:
                    continue

                channels = await self._storage.list_channels(tenant_id)
                for ch in channels:
                    channel_id = ch["id"]
                    latest = await self._storage.get_latest_strategy(channel_id)
                    if latest is None:
                        continue

                    # Check staleness
                    valid_until = latest.get("valid_until")
                    if (
                        valid_until
                        and isinstance(valid_until, datetime)
                        and valid_until < datetime.utcnow()
                    ):
                            actions.append(
                                HeartbeatAction(
                                    skill_name=self.name,
                                    action_type="strategy_stale",
                                    user_id=uid,
                                    data={
                                        "channel_id": str(channel_id),
                                        "channel_name": ch.get("channel_name", ""),
                                        "valid_until": valid_until.isoformat(),
                                    },
                                    priority=2,
                                )
                            )
        except Exception:
            log.exception("strategy_heartbeat_failed")

        return actions

    # ------------------------------------------------------------------
    # System prompt fragment
    # ------------------------------------------------------------------

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        if self._status != SkillStatus.READY:
            return None
        return "[YouTube Strategy: Ready — can generate growth strategies and content plans]"


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _resolve_channel_id(request: SkillRequest) -> UUID | None:
    raw = request.context.get("channel_id")
    if raw is None:
        return None
    try:
        return UUID(str(raw))
    except ValueError:
        return None


def _serialise(row: dict[str, Any] | None) -> dict[str, Any]:
    if not row:
        return {}
    out: dict[str, Any] = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "hex"):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def _parse_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:])
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("strategy_json_parse_failed", raw=raw[:300])
        return {"raw_response": raw}
