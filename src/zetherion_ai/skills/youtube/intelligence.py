"""YouTube Channel Intelligence Skill.

Analyzes channel data pushed from CGS and produces structured intelligence
reports.  The analysis pipeline runs:

  1. Comment classification  — Ollama (CLASSIFICATION, free)
  2. Content performance     — computation + Gemini (SUMMARIZATION)
  3. Audience synthesis       — Claude via OpenAI provider (COMPLEX_REASONING)
  4. Report assembly         — structured IntelligenceReport

The heartbeat triggers re-analysis every 12-24 h (configurable per channel)
whenever new data has been ingested since the last run.
"""

from __future__ import annotations

import json
from collections import Counter
from datetime import datetime
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
from zetherion_ai.skills.youtube.models import (
    CommentAnalysis,
    IntelligenceReportType,
)
from zetherion_ai.skills.youtube.prompts import (
    AUDIENCE_SYNTHESIS_SYSTEM,
    AUDIENCE_SYNTHESIS_USER,
    COMMENT_BATCH_SYSTEM,
    COMMENT_BATCH_USER,
)

if TYPE_CHECKING:
    from zetherion_ai.agent.inference import InferenceBroker
    from zetherion_ai.memory.qdrant import QdrantMemory
    from zetherion_ai.skills.youtube.storage import YouTubeStorage

log = get_logger("zetherion_ai.skills.youtube.intelligence")

# Maximum comments per Ollama batch call
_COMMENT_BATCH_SIZE = 20


# Intent → handler mapping (mirrors GitHubSkill pattern)
INTENT_HANDLERS: dict[str, str] = {
    "yt_analyze_channel": "_handle_analyze",
    "yt_get_intelligence": "_handle_get_intelligence",
    "yt_intelligence_history": "_handle_intelligence_history",
}


class YouTubeIntelligenceSkill(Skill):
    """Analyze YouTube channel data and produce structured reports."""

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
            name="youtube_intelligence",
            description="Analyze YouTube channels and produce structured intelligence reports",
            version="1.0.0",
            permissions=PermissionSet(
                {
                    Permission.READ_PROFILE,
                    Permission.WRITE_MEMORIES,
                    Permission.SEND_MESSAGES,
                }
            ),
            collections=["yt_comments"],
            intents=list(INTENT_HANDLERS.keys()),
        )

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def initialize(self) -> bool:
        if not self._storage:
            log.error("intelligence_init_failed", reason="No storage provided")
            self._set_status(SkillStatus.ERROR, "No storage provided")
            return False
        if not self._broker:
            log.error("intelligence_init_failed", reason="No inference broker")
            self._set_status(SkillStatus.ERROR, "No inference broker")
            return False

        self._assumptions = AssumptionTracker(self._storage)
        await self._storage.initialize()
        log.info("youtube_intelligence_initialized")
        self._set_status(SkillStatus.READY)
        return True

    # ------------------------------------------------------------------
    # Handle dispatch
    # ------------------------------------------------------------------

    async def handle(self, request: SkillRequest) -> SkillResponse:
        handler_name = INTENT_HANDLERS.get(request.intent)
        if not handler_name:
            return SkillResponse.error_response(
                request.id,
                f"Unknown intent: {request.intent}",
            )
        handler = getattr(self, handler_name, None)
        if not handler:
            return SkillResponse.error_response(
                request.id,
                f"Handler not implemented: {handler_name}",
            )
        try:
            return await handler(request)  # type: ignore[no-any-return]
        except Exception as e:
            log.error("intelligence_handle_error", error=str(e))
            return SkillResponse.error_response(request.id, f"Intelligence error: {e}")

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    async def _handle_analyze(self, request: SkillRequest) -> SkillResponse:
        """Trigger a full intelligence analysis for a channel."""
        assert self._storage is not None
        channel_id = self._resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        channel = await self._storage.get_channel(channel_id)
        if channel is None:
            return SkillResponse.error_response(request.id, "Channel not found")

        report = await self.run_analysis(channel_id, channel)
        if report is None:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message="No new data to analyze for this channel.",
                data={},
            )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Intelligence report generated.",
            data=report,
        )

    async def _handle_get_intelligence(self, request: SkillRequest) -> SkillResponse:
        """Return the latest intelligence report for a channel."""
        assert self._storage is not None
        channel_id = self._resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        row = await self._storage.get_latest_report(channel_id)
        if row is None:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message="No intelligence report available yet.",
                data={},
            )
        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Latest intelligence report.",
            data=_serialise_report(row),
        )

    async def _handle_intelligence_history(self, request: SkillRequest) -> SkillResponse:
        """Return historical intelligence reports for a channel."""
        assert self._storage is not None
        channel_id = self._resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        limit = request.context.get("limit", 10)
        rows = await self._storage.get_report_history(channel_id, limit=limit)
        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"{len(rows)} report(s) found.",
            data={"reports": [_serialise_report(r) for r in rows]},
        )

    # ------------------------------------------------------------------
    # Core analysis pipeline
    # ------------------------------------------------------------------

    async def run_analysis(
        self,
        channel_id: UUID,
        channel: dict[str, Any],
    ) -> dict[str, Any] | None:
        """Execute the full intelligence pipeline for *channel_id*.

        Returns the saved report dict, or ``None`` if there is no data to
        process.
        """
        assert self._storage is not None
        assert self._broker is not None

        # 1. Classify unanalyzed comments via Ollama
        await self._classify_comments(channel_id)

        # 2. Compute content performance from stored videos
        perf = await self._compute_performance(channel_id)

        # 3. Build the aggregated comment summary
        comment_summary = await self._aggregate_comments(channel_id)

        # If we have no comments AND no videos, nothing useful to report
        if comment_summary["total"] == 0 and not perf:
            return None

        # 4. Synthesise audience insight via Claude (COMPLEX_REASONING)
        stats_row = await self._storage.get_latest_stats(channel_id)
        assumptions = []
        if self._assumptions:
            assumptions = await self._assumptions.get_high_confidence(channel_id)

        report_body = await self._synthesise(
            channel_name=channel.get("channel_name", ""),
            comment_summary=comment_summary,
            video_performance=perf,
            channel_stats=stats_row.get("snapshot", {}) if stats_row else {},
            assumptions=assumptions,
        )

        # 5. Persist the report
        saved = await self._storage.save_report(
            channel_id=channel_id,
            report_type=IntelligenceReportType.FULL.value,
            report=report_body,
            model_used="multi",
        )

        # 6. Update last_analysis_at on the channel
        await self._storage.update_channel(channel_id, last_analysis_at=datetime.utcnow())

        # 7. Infer new assumptions from the report
        if self._assumptions:
            await self._infer_assumptions(channel_id, report_body)

        return _serialise_report(saved)

    # ------------------------------------------------------------------
    # Step 1: comment classification (Ollama — CLASSIFICATION)
    # ------------------------------------------------------------------

    async def _classify_comments(self, channel_id: UUID) -> list[CommentAnalysis]:
        """Classify unanalyzed comments in batches via Ollama."""
        assert self._storage is not None
        assert self._broker is not None

        unanalyzed = await self._storage.get_unanalyzed_comments(channel_id)
        if not unanalyzed:
            return []

        all_analyses: list[CommentAnalysis] = []

        for batch_start in range(0, len(unanalyzed), _COMMENT_BATCH_SIZE):
            batch = unanalyzed[batch_start : batch_start + _COMMENT_BATCH_SIZE]
            batch_json = json.dumps(
                [{"comment_id": str(c["id"]), "text": c["text"]} for c in batch],
                default=str,
            )

            try:
                result = await self._broker.infer(
                    prompt=COMMENT_BATCH_USER.format(comments_json=batch_json),
                    task_type=TaskType.CLASSIFICATION,
                    system_prompt=COMMENT_BATCH_SYSTEM,
                    temperature=0.1,
                )
                analyses = self._parse_batch_response(result.content, batch)
            except Exception:
                log.exception("comment_batch_classification_failed")
                analyses = self._fallback_analyses(batch)

            # Persist analysis results back into the comments table
            for a in analyses:
                try:
                    await self._storage.update_comment_analysis(
                        comment_id=UUID(a.comment_id),
                        sentiment=a.sentiment,
                        category=a.category,
                        topics=a.topics,
                    )
                except Exception:
                    log.exception("comment_analysis_persist_failed", comment_id=a.comment_id)

            all_analyses.extend(analyses)

        return all_analyses

    @staticmethod
    def _parse_batch_response(raw: str, batch: list[dict[str, Any]]) -> list[CommentAnalysis]:
        """Parse the LLM JSON array response into CommentAnalysis objects."""
        # Strip markdown fences if present
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:])
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3]

        try:
            items = json.loads(cleaned)
        except json.JSONDecodeError:
            log.warning("batch_response_json_invalid", raw=raw[:200])
            return YouTubeIntelligenceSkill._fallback_analyses(batch)

        if not isinstance(items, list):
            items = [items]

        analyses: list[CommentAnalysis] = []
        for item in items:
            analyses.append(
                CommentAnalysis(
                    comment_id=str(item.get("comment_id", "")),
                    sentiment=item.get("sentiment", "neutral"),
                    category=item.get("category", "feedback"),
                    topics=item.get("topics", [])[:3],
                    is_question=bool(item.get("is_question", False)),
                    entities=item.get("entities", []),
                )
            )
        return analyses

    @staticmethod
    def _fallback_analyses(batch: list[dict[str, Any]]) -> list[CommentAnalysis]:
        """Return neutral/feedback fallback for every comment in *batch*."""
        return [
            CommentAnalysis(
                comment_id=str(c["id"]),
                sentiment="neutral",
                category="feedback",
            )
            for c in batch
        ]

    # ------------------------------------------------------------------
    # Step 2: content performance (computation)
    # ------------------------------------------------------------------

    async def _compute_performance(self, channel_id: UUID) -> list[dict[str, Any]]:
        """Rank videos by engagement and return performance data."""
        assert self._storage is not None

        videos = await self._storage.get_videos(channel_id, limit=200)
        if not videos:
            return []

        perf: list[dict[str, Any]] = []
        for v in videos:
            stats = v.get("stats") or {}
            views = stats.get("viewCount", stats.get("views", 0))
            likes = stats.get("likeCount", stats.get("likes", 0))
            comments = stats.get("commentCount", stats.get("comments", 0))

            views = int(views) if views else 0
            likes = int(likes) if likes else 0
            comments = int(comments) if comments else 0

            engagement = (likes + comments) / max(views, 1)

            perf.append(
                {
                    "video_id": v.get("video_youtube_id", ""),
                    "title": v.get("title", ""),
                    "views": views,
                    "likes": likes,
                    "comments": comments,
                    "engagement_rate": round(engagement, 5),
                    "published_at": v["published_at"].isoformat()
                    if v.get("published_at")
                    else None,
                    "tags": v.get("tags", []),
                }
            )

        perf.sort(key=lambda x: x["engagement_rate"], reverse=True)
        return perf

    # ------------------------------------------------------------------
    # Step 3: aggregate comment data
    # ------------------------------------------------------------------

    async def _aggregate_comments(self, channel_id: UUID) -> dict[str, Any]:
        """Build an aggregated summary of all analyzed comments."""
        assert self._storage is not None

        comments = await self._storage.get_comments(channel_id, limit=2000)

        total = len(comments)
        sentiments: Counter[str] = Counter()
        categories: Counter[str] = Counter()
        topics: Counter[str] = Counter()
        questions: list[str] = []
        complaints: list[str] = []

        for c in comments:
            if c.get("sentiment"):
                sentiments[c["sentiment"]] += 1
            if c.get("category"):
                categories[c["category"]] += 1
            for t in c.get("topics") or []:
                topics[t] += 1
            if c.get("category") == "question":
                questions.append(c["text"][:200])
            if c.get("category") == "complaint":
                complaints.append(c["text"][:200])

        return {
            "total": total,
            "sentiments": dict(sentiments),
            "categories": dict(categories),
            "top_topics": topics.most_common(20),
            "questions": questions[:30],
            "complaints": complaints[:20],
        }

    # ------------------------------------------------------------------
    # Step 4: audience synthesis (Claude — COMPLEX_REASONING)
    # ------------------------------------------------------------------

    async def _synthesise(
        self,
        channel_name: str,
        comment_summary: dict[str, Any],
        video_performance: list[dict[str, Any]],
        channel_stats: dict[str, Any],
        assumptions: list[dict[str, Any]],
    ) -> dict[str, Any]:
        """Use a frontier model to synthesise the intelligence report."""
        assert self._broker is not None

        # Format the data for the prompt
        sentiment_str = ", ".join(
            f"{k}: {v}" for k, v in comment_summary.get("sentiments", {}).items()
        )
        topics_str = ", ".join(f"{t} ({n})" for t, n in comment_summary.get("top_topics", [])[:10])
        perf_str = json.dumps(video_performance[:20], indent=2, default=str)
        stats_str = json.dumps(channel_stats, indent=2, default=str)
        assumptions_str = (
            "\n".join(
                f"- [{a.get('category', '')}] {a.get('statement', '')}"
                f" (confidence: {a.get('confidence', 0):.1f})"
                for a in assumptions
            )
            or "None yet."
        )

        user_prompt = AUDIENCE_SYNTHESIS_USER.format(
            channel_name=channel_name,
            total_comments=comment_summary["total"],
            sentiment_summary=sentiment_str or "No data",
            top_topics=topics_str or "No data",
            questions=json.dumps(comment_summary.get("questions", [])[:15], default=str),
            complaints=json.dumps(comment_summary.get("complaints", [])[:10], default=str),
            video_performance=perf_str,
            channel_stats=stats_str,
            assumptions=assumptions_str,
        )

        try:
            result = await self._broker.infer(
                prompt=user_prompt,
                task_type=TaskType.COMPLEX_REASONING,
                system_prompt=AUDIENCE_SYNTHESIS_SYSTEM,
                max_tokens=4096,
                temperature=0.4,
            )
            return self._parse_synthesis(result.content)
        except Exception:
            log.exception("audience_synthesis_failed")
            # Return a minimal fallback report
            return self._fallback_report(
                channel_name, comment_summary, video_performance, channel_stats
            )

    @staticmethod
    def _parse_synthesis(raw: str) -> dict[str, Any]:
        """Parse the synthesis JSON, stripping markdown fences."""
        cleaned = raw.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            cleaned = "\n".join(lines[1:])
            if cleaned.rstrip().endswith("```"):
                cleaned = cleaned.rstrip()[:-3]

        try:
            return json.loads(cleaned)  # type: ignore[no-any-return]
        except json.JSONDecodeError:
            log.warning("synthesis_json_invalid", raw=raw[:300])
            return {"raw_response": raw}

    @staticmethod
    def _fallback_report(
        channel_name: str,
        comment_summary: dict[str, Any],
        video_performance: list[dict[str, Any]],
        channel_stats: dict[str, Any],
    ) -> dict[str, Any]:
        """Build a minimal report when LLM synthesis fails."""
        total_sent = comment_summary.get("sentiments", {})
        total_comments = comment_summary["total"]

        return {
            "overview": {
                "channel_name": channel_name,
                "subscriber_count": channel_stats.get("subscriberCount", 0),
                "total_views": channel_stats.get("viewCount", 0),
                "video_count": channel_stats.get("videoCount", 0),
                "growth_trend": "stable",
                "growth_rate_percent": 0.0,
                "period": {},
            },
            "content_performance": {
                "top_performing": video_performance[:5],
                "underperforming": video_performance[-3:] if len(video_performance) > 5 else [],
                "categories": [],
                "optimal_length_minutes": {},
                "best_posting_times": [],
            },
            "audience": {
                "sentiment": {
                    "positive": total_sent.get("positive", 0) / max(total_comments, 1),
                    "neutral": total_sent.get("neutral", 0) / max(total_comments, 1),
                    "negative": total_sent.get("negative", 0) / max(total_comments, 1),
                },
                "top_requests": [],
                "unanswered_questions": [
                    {"question": q, "frequency": 1}
                    for q in comment_summary.get("questions", [])[:5]
                ],
                "complaints": [
                    {"issue": c, "frequency": 1, "severity": "medium"}
                    for c in comment_summary.get("complaints", [])[:5]
                ],
            },
            "recommendations": [],
            "_fallback": True,
        }

    # ------------------------------------------------------------------
    # Step 7: infer assumptions from the report
    # ------------------------------------------------------------------

    async def _infer_assumptions(self, channel_id: UUID, report: dict[str, Any]) -> None:
        """Extract high-confidence inferences from a report and store them."""
        assert self._assumptions is not None

        # Infer content performance assumption
        top = (report.get("content_performance") or {}).get("top_performing", [])
        if top:
            titles = ", ".join(v.get("title", "?")[:50] for v in top[:3])
            await self._assumptions.add_inferred(
                channel_id=channel_id,
                category="performance",
                statement=f"Top performing content: {titles}",
                evidence=[f"Engagement rates: {[v.get('engagement_rate') for v in top[:3]]}"],
                confidence=0.8,
            )

        # Infer audience sentiment assumption
        audience = report.get("audience") or {}
        sentiment = audience.get("sentiment") or {}
        if sentiment:
            dominant = max(sentiment, key=lambda k: sentiment[k], default="neutral")
            pct = round(sentiment.get(dominant, 0) * 100, 1)
            await self._assumptions.add_inferred(
                channel_id=channel_id,
                category="audience",
                statement=f"Dominant audience sentiment is {dominant} ({pct}%)",
                evidence=[f"Sentiment breakdown: {sentiment}"],
                confidence=0.7,
            )

        # Infer top topic assumption
        top_requests = audience.get("top_requests", [])
        if top_requests:
            top_topic = top_requests[0].get("topic", "")
            if top_topic:
                await self._assumptions.add_inferred(
                    channel_id=channel_id,
                    category="topic",
                    statement=f"Most requested topic: {top_topic}",
                    evidence=[f"{top_requests[0].get('mentions', 0)} mentions"],
                    confidence=0.7,
                )

    # ------------------------------------------------------------------
    # Heartbeat: time-based re-analysis
    # ------------------------------------------------------------------

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Check for channels due for re-analysis."""
        if not self._storage:
            return []

        actions: list[HeartbeatAction] = []
        try:
            due = await self._storage.get_channels_due_for_analysis()
            for ch in due:
                channel_id = ch["id"]
                tenant_id = str(ch.get("tenant_id", ""))
                log.info(
                    "heartbeat_analysis_due",
                    channel_id=str(channel_id),
                    channel_name=ch.get("channel_name", ""),
                )

                try:
                    report = await self.run_analysis(channel_id, ch)
                    if report:
                        actions.append(
                            HeartbeatAction(
                                skill_name=self.name,
                                action_type="intelligence_report_generated",
                                user_id=tenant_id,
                                data={
                                    "channel_id": str(channel_id),
                                    "channel_name": ch.get("channel_name", ""),
                                    "report_id": report.get("report_id", ""),
                                },
                                priority=5,
                            )
                        )
                except Exception:
                    log.exception(
                        "heartbeat_analysis_failed",
                        channel_id=str(channel_id),
                    )
        except Exception:
            log.exception("heartbeat_channels_due_query_failed")

        return actions

    # ------------------------------------------------------------------
    # System prompt fragment
    # ------------------------------------------------------------------

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        if self._status != SkillStatus.READY:
            return None
        return "[YouTube Intelligence: Ready — can analyze channels and generate reports]"

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _resolve_channel_id(request: SkillRequest) -> UUID | None:
        raw = request.context.get("channel_id")
        if raw is None:
            return None
        try:
            return UUID(str(raw))
        except ValueError:
            return None


# ---------------------------------------------------------------------------
# Module-level helpers
# ---------------------------------------------------------------------------


def _serialise_report(row: dict[str, Any]) -> dict[str, Any]:
    """Convert a DB report row into an API-friendly dict."""
    out: dict[str, Any] = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "hex"):
            out[k] = str(v)
        else:
            out[k] = v
    return out
