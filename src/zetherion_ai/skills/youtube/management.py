"""YouTube Channel Management Skill.

Automates channel operations with a scaling trust model:

  - Auto-reply generation for new comments
  - Tag recommendation via SEO analysis
  - Channel health audit
  - Onboarding flow (questionnaire → assumptions)

The trust model controls how aggressively replies are auto-approved.
See ``trust.py`` for level definitions and promotion/demotion rules.
"""

from __future__ import annotations

import json
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
    ManagementState,
    OnboardingQuestion,
    ReplyCategory,
    ReplyStatus,
)
from zetherion_ai.skills.youtube.prompts import (
    CHANNEL_HEALTH_SYSTEM,
    CHANNEL_HEALTH_USER,
    ONBOARDING_FOLLOWUP_SYSTEM,
    ONBOARDING_FOLLOWUP_USER,
    REPLY_GENERATION_SYSTEM,
    REPLY_GENERATION_USER,
    TAG_SUGGESTION_SYSTEM,
    TAG_SUGGESTION_USER,
)
from zetherion_ai.skills.youtube.trust import TrustModel

if TYPE_CHECKING:
    from zetherion_ai.agent.inference import InferenceBroker
    from zetherion_ai.memory.qdrant import QdrantMemory
    from zetherion_ai.skills.youtube.storage import YouTubeStorage

log = get_logger("zetherion_ai.skills.youtube.management")

# Intent → handler mapping
INTENT_HANDLERS: dict[str, str] = {
    "yt_manage_channel": "_handle_manage",
    "yt_get_management_state": "_handle_get_state",
    "yt_configure_management": "_handle_configure",
    "yt_review_replies": "_handle_review_replies",
    "yt_get_tag_recommendations": "_handle_tag_recommendations",
    "yt_channel_health": "_handle_channel_health",
}

# Default onboarding questions
_ONBOARDING_QUESTIONS = [
    OnboardingQuestion(
        category="topics",
        question="What are the main topics of your channel?",
        hint="e.g., tech reviews, cooking tutorials, fitness tips",
    ),
    OnboardingQuestion(
        category="audience",
        question="Who is your target audience?",
        hint="e.g., developers aged 25-40, home cooks, fitness beginners",
    ),
    OnboardingQuestion(
        category="tone",
        question="What tone should replies use?",
        hint="e.g., professional, casual, friendly, technical, humorous",
    ),
    OnboardingQuestion(
        category="exclusions",
        question="Are there topics or competitors that should never be mentioned?",
        hint="e.g., brand X, political topics, specific products",
    ),
    OnboardingQuestion(
        category="schedule",
        question="What is your posting frequency goal?",
        hint="e.g., 2 videos per week, daily shorts, monthly deep-dives",
    ),
]


class YouTubeManagementSkill(Skill):
    """Automate YouTube channel operations with a scaling trust model."""

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
            name="youtube_management",
            description="Automate YouTube channel management with trust-scaled auto-replies",
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
            log.error("management_init_failed", reason="No storage provided")
            self._set_status(SkillStatus.ERROR, "No storage provided")
            return False
        if not self._broker:
            log.error("management_init_failed", reason="No inference broker")
            self._set_status(SkillStatus.ERROR, "No inference broker")
            return False

        self._assumptions = AssumptionTracker(self._storage)
        log.info("youtube_management_initialized")
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
            log.error("management_handle_error", error=str(e))
            return SkillResponse.error_response(request.id, f"Management error: {e}")

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    async def _handle_manage(self, request: SkillRequest) -> SkillResponse:
        """Generate reply drafts for new comments on a channel."""
        assert self._storage is not None
        channel_id = _resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        channel = await self._storage.get_channel(channel_id)
        if channel is None:
            return SkillResponse.error_response(request.id, "Channel not found")

        drafts = await self.generate_reply_drafts(channel_id, channel)
        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Generated {len(drafts)} reply draft(s).",
            data={"drafts": drafts},
        )

    async def _handle_get_state(self, request: SkillRequest) -> SkillResponse:
        """Return the current management state for a channel."""
        assert self._storage is not None
        channel_id = _resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        state = await self.get_management_state(channel_id)
        if state is None:
            return SkillResponse.error_response(request.id, "Channel not found")

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Management state.",
            data=state.to_dict(),
        )

    async def _handle_configure(self, request: SkillRequest) -> SkillResponse:
        """Process onboarding answers and/or update channel config."""
        assert self._storage is not None
        assert self._assumptions is not None
        channel_id = _resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        channel = await self._storage.get_channel(channel_id)
        if channel is None:
            return SkillResponse.error_response(request.id, "Channel not found")

        answers = request.context.get("answers", {})
        config_updates = request.context.get("config", {})

        # Store each answer as a confirmed assumption
        for category, answer in answers.items():
            await self._assumptions.add_confirmed(
                channel_id=channel_id,
                category=category,
                statement=str(answer),
                evidence=[f"User answer during onboarding: {answer}"],
            )

        # Apply config updates if provided
        if config_updates:
            existing_config = channel.get("config") or {}
            existing_config.update(config_updates)
            await self._storage.update_channel(channel_id, config=existing_config)

        # Check onboarding completeness
        missing = await self._assumptions.get_missing_categories(channel_id)
        onboarding_complete = len(missing) == 0

        if onboarding_complete and not channel.get("onboarding_complete"):
            await self._storage.update_channel(channel_id, onboarding_complete=True)

        # Generate follow-up questions if onboarding not complete
        follow_ups: list[dict[str, Any]] = []
        if missing:
            follow_ups = await self._generate_followup_questions(
                answers_so_far=answers,
                missing_categories=missing,
            )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="Configuration updated." if not missing else "More information needed.",
            data={
                "onboarding_complete": onboarding_complete,
                "missing_categories": missing,
                "follow_up_questions": follow_ups,
                "initial_questions": [q.to_dict() for q in _ONBOARDING_QUESTIONS]
                if not answers
                else [],
            },
        )

    async def _handle_review_replies(self, request: SkillRequest) -> SkillResponse:
        """Approve, reject, or mark-posted a reply draft."""
        assert self._storage is not None
        channel_id = _resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        action = request.context.get("action")  # approve / reject / posted
        reply_id_raw = request.context.get("reply_id")

        # If no action, return list of pending replies
        if not action or not reply_id_raw:
            status_filter = request.context.get("status", "pending")
            drafts = await self._storage.get_reply_drafts(
                channel_id, status=status_filter
            )
            return SkillResponse(
                request_id=request.id,
                success=True,
                message=f"{len(drafts)} reply draft(s).",
                data={"drafts": [_serialise(d) for d in drafts]},
            )

        try:
            reply_id = UUID(str(reply_id_raw))
        except ValueError:
            return SkillResponse.error_response(request.id, "Invalid reply_id")

        # Process the action
        channel = await self._storage.get_channel(channel_id)
        if channel is None:
            return SkillResponse.error_response(request.id, "Channel not found")

        trust = TrustModel.from_channel(channel)

        if action == "approve":
            new_status = ReplyStatus.APPROVED.value
            trust.record_approval()
        elif action == "reject":
            new_status = ReplyStatus.REJECTED.value
            trust.record_rejection()
        elif action == "posted":
            new_status = ReplyStatus.POSTED.value
        else:
            return SkillResponse.error_response(
                request.id, f"Unknown action: {action}"
            )

        updated = await self._storage.update_reply_status(reply_id, new_status)

        # Persist trust changes back to channel
        if action in ("approve", "reject"):
            await self._storage.update_channel(
                channel_id,
                trust_level=trust.level,
                trust_stats=trust.stats,
            )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Reply {reply_id} marked as {new_status}. Trust: {trust.label}",
            data={
                "reply": _serialise(updated) if updated else {},
                "trust": trust.to_dict(),
            },
        )

    async def _handle_tag_recommendations(self, request: SkillRequest) -> SkillResponse:
        """Generate or retrieve tag recommendations for a video."""
        assert self._storage is not None
        channel_id = _resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        video_youtube_id = request.context.get("video_id")
        if video_youtube_id:
            rec = await self._generate_tag_recommendation(channel_id, video_youtube_id)
            return SkillResponse(
                request_id=request.id,
                success=True,
                message="Tag recommendation generated.",
                data={"recommendation": _serialise(rec) if rec else {}},
            )

        # Return existing recommendations
        recs = await self._storage.get_tag_recommendations(channel_id)
        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"{len(recs)} tag recommendation(s).",
            data={"recommendations": [_serialise(r) for r in recs]},
        )

    async def _handle_channel_health(self, request: SkillRequest) -> SkillResponse:
        """Run a channel health audit."""
        assert self._storage is not None
        channel_id = _resolve_channel_id(request)
        if channel_id is None:
            return SkillResponse.error_response(request.id, "channel_id required")

        channel = await self._storage.get_channel(channel_id)
        if channel is None:
            return SkillResponse.error_response(request.id, "Channel not found")

        issues = await self._run_health_audit(channel_id, channel)
        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Health audit: {len(issues)} issue(s) found.",
            data={"issues": issues},
        )

    # ------------------------------------------------------------------
    # Reply generation pipeline
    # ------------------------------------------------------------------

    async def generate_reply_drafts(
        self,
        channel_id: UUID,
        channel: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Generate reply drafts for comments that don't have one yet."""
        assert self._storage is not None
        assert self._broker is not None

        # Get comments that have been analyzed but don't have a reply draft
        comments = await self._storage.get_comments(channel_id, limit=200)
        existing_drafts = await self._storage.get_reply_drafts(channel_id, limit=500)
        replied_ids = {d["comment_id"] for d in existing_drafts}

        trust = TrustModel.from_channel(channel)
        config = channel.get("config") or {}
        tone = config.get("tone", "friendly and professional")
        topics = config.get("topics", "general")
        exclusions = config.get("exclusions", "nothing specific")

        drafts: list[dict[str, Any]] = []

        for comment in comments:
            yt_comment_id = comment.get("comment_youtube_id", "")
            if yt_comment_id in replied_ids:
                continue

            # Skip spam
            category = comment.get("category", "feedback")
            if category == ReplyCategory.SPAM.value:
                continue

            # Skip if no analysis yet
            if not comment.get("sentiment"):
                continue

            # Determine model tier based on category
            if category == ReplyCategory.COMPLAINT.value:
                task_type = TaskType.CONVERSATION  # → Claude
            elif category in (ReplyCategory.QUESTION.value, ReplyCategory.FEEDBACK.value):
                task_type = TaskType.SUMMARIZATION  # → Gemini
            else:
                task_type = TaskType.SIMPLE_QA  # → Ollama

            # Get video title for context
            video_title = ""
            if comment.get("video_id"):
                videos = await self._storage.get_videos(channel_id, limit=200)
                for v in videos:
                    if v["id"] == comment["video_id"]:
                        video_title = v.get("title", "")
                        break

            try:
                result = await self._broker.infer(
                    prompt=REPLY_GENERATION_USER.format(
                        video_title=video_title or "Unknown",
                        author=comment.get("author", "User"),
                        comment_text=comment["text"][:500],
                        category=category,
                    ),
                    task_type=task_type,
                    system_prompt=REPLY_GENERATION_SYSTEM.format(
                        tone=tone,
                        topics=topics,
                        exclusions=exclusions,
                    ),
                    max_tokens=256,
                    temperature=0.6,
                )
                reply_text = result.content.strip()
                model_used = result.model
                confidence = 0.85 if task_type == TaskType.SIMPLE_QA else 0.7
            except Exception:
                log.exception("reply_generation_failed", comment_id=yt_comment_id)
                continue

            # Apply trust model
            auto_approved = trust.should_auto_approve(category)
            status = ReplyStatus.APPROVED.value if auto_approved else ReplyStatus.PENDING.value

            draft = await self._storage.save_reply_draft(
                {
                    "channel_id": channel_id,
                    "comment_id": yt_comment_id,
                    "video_id": comment.get("video_youtube_id", ""),
                    "original_comment": comment["text"][:1000],
                    "draft_reply": reply_text,
                    "confidence": confidence,
                    "category": category,
                    "status": status,
                    "auto_approved": auto_approved,
                    "model_used": model_used,
                }
            )
            drafts.append(_serialise(draft))

        return drafts

    # ------------------------------------------------------------------
    # Management state
    # ------------------------------------------------------------------

    async def get_management_state(
        self, channel_id: UUID
    ) -> ManagementState | None:
        """Build the current management state for a channel."""
        assert self._storage is not None

        channel = await self._storage.get_channel(channel_id)
        if channel is None:
            return None

        trust = TrustModel.from_channel(channel)
        pending = await self._storage.get_reply_drafts(channel_id, status="pending")
        posted_today = await self._storage.count_replies_today(channel_id)

        return ManagementState(
            channel_id=channel_id,
            updated_at=channel.get("updated_at", datetime.utcnow()),
            onboarding_complete=channel.get("onboarding_complete", False),
            trust_level=trust.level,
            trust_label=trust.label,
            trust_stats={**trust.stats, "rate": round(trust.approval_rate, 4)},
            next_level_at=trust.next_level_at or 0,
            auto_reply_enabled=channel.get("config", {}).get("auto_reply", False),
            auto_categories=sorted(trust.auto_categories),
            review_categories=sorted(trust.review_categories),
            pending_count=len(pending),
            posted_today=posted_today,
        )

    # ------------------------------------------------------------------
    # Tag recommendation
    # ------------------------------------------------------------------

    async def _generate_tag_recommendation(
        self,
        channel_id: UUID,
        video_youtube_id: str,
    ) -> dict[str, Any] | None:
        """Generate tag suggestions for a specific video."""
        assert self._storage is not None
        assert self._broker is not None

        video = await self._storage.get_video_by_youtube_id(channel_id, video_youtube_id)
        if video is None:
            return None

        # Gather channel context
        assumptions = []
        if self._assumptions:
            assumptions = await self._assumptions.get_high_confidence(channel_id)

        top_topics = [
            a.get("statement", "")
            for a in assumptions
            if a.get("category") == "topic"
        ]

        # Check if a strategy exists for keyword targets
        strategy_row = await self._storage.get_latest_strategy(channel_id)
        keyword_targets = ""
        if strategy_row:
            seo = (strategy_row.get("strategy") or {}).get("seo", {})
            keyword_targets = seo.get("tag_strategy", "")

        try:
            result = await self._broker.infer(
                prompt=TAG_SUGGESTION_USER.format(
                    video_title=video.get("title", ""),
                    video_description=(video.get("description") or "")[:500],
                    current_tags=json.dumps(video.get("tags", []), default=str),
                    top_topics=", ".join(top_topics) or "None available",
                    keyword_targets=keyword_targets or "None set",
                ),
                task_type=TaskType.SUMMARIZATION,  # → Gemini
                system_prompt=TAG_SUGGESTION_SYSTEM,
                max_tokens=512,
                temperature=0.3,
            )
            parsed = _parse_json(result.content)
        except Exception:
            log.exception("tag_recommendation_failed", video_id=video_youtube_id)
            return None

        rec = await self._storage.save_tag_recommendation(
            {
                "channel_id": channel_id,
                "video_id": video_youtube_id,
                "current_tags": video.get("tags", []),
                "suggested_tags": parsed.get("suggested_tags", []),
                "reason": parsed.get("reason", ""),
            }
        )
        return _serialise(rec)

    # ------------------------------------------------------------------
    # Channel health audit
    # ------------------------------------------------------------------

    async def _run_health_audit(
        self,
        channel_id: UUID,
        channel: dict[str, Any],
    ) -> list[dict[str, Any]]:
        """Run a channel health audit via Gemini."""
        assert self._storage is not None
        assert self._broker is not None

        stats_row = await self._storage.get_latest_stats(channel_id)
        snapshot = stats_row.get("snapshot", {}) if stats_row else {}
        videos = await self._storage.get_videos(channel_id, limit=50)
        config = channel.get("config") or {}

        try:
            result = await self._broker.infer(
                prompt=CHANNEL_HEALTH_USER.format(
                    channel_name=channel.get("channel_name", ""),
                    description=config.get("description", "Not set"),
                    video_count=len(videos),
                    subscriber_count=snapshot.get("subscriberCount", "Unknown"),
                    has_playlists=config.get("has_playlists", "Unknown"),
                    upload_frequency=config.get("upload_frequency", "Unknown"),
                    default_tags=json.dumps(config.get("default_tags", []), default=str),
                    about_section=config.get("about_section", "Not set"),
                ),
                task_type=TaskType.SUMMARIZATION,  # → Gemini
                system_prompt=CHANNEL_HEALTH_SYSTEM,
                max_tokens=1024,
                temperature=0.3,
            )
            parsed = _parse_json(result.content)
        except Exception:
            log.exception("channel_health_audit_failed")
            return []

        if isinstance(parsed, list):
            return parsed
        return parsed.get("issues", [parsed]) if parsed else []

    # ------------------------------------------------------------------
    # Onboarding follow-up questions
    # ------------------------------------------------------------------

    async def _generate_followup_questions(
        self,
        answers_so_far: dict[str, Any],
        missing_categories: list[str],
    ) -> list[dict[str, Any]]:
        """Ask the LLM for context-aware follow-up questions."""
        assert self._broker is not None

        try:
            result = await self._broker.infer(
                prompt=ONBOARDING_FOLLOWUP_USER.format(
                    answers_so_far=json.dumps(answers_so_far, default=str),
                    missing_categories=", ".join(missing_categories),
                ),
                task_type=TaskType.SUMMARIZATION,  # → Gemini
                system_prompt=ONBOARDING_FOLLOWUP_SYSTEM,
                max_tokens=512,
                temperature=0.5,
            )
            parsed = _parse_json(result.content)
        except Exception:
            log.exception("followup_question_generation_failed")
            return []

        if isinstance(parsed, list):
            return parsed
        return []

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Check for new comments needing reply drafts."""
        if not self._storage:
            return []

        actions: list[HeartbeatAction] = []
        try:
            # Process all channels that have unanalyzed comments
            # (Intelligence heartbeat handles analysis; Management handles replies)
            # We re-use the "channels due" query to find active channels
            due = await self._storage.get_channels_due_for_analysis()
            for ch in due:
                channel_id = ch["id"]
                tenant_id = str(ch.get("tenant_id", ""))

                if not ch.get("onboarding_complete"):
                    continue

                try:
                    drafts = await self.generate_reply_drafts(channel_id, ch)
                    if drafts:
                        auto_count = sum(
                            1 for d in drafts if d.get("auto_approved")
                        )
                        actions.append(
                            HeartbeatAction(
                                skill_name=self.name,
                                action_type="reply_drafts_generated",
                                user_id=tenant_id,
                                data={
                                    "channel_id": str(channel_id),
                                    "total": len(drafts),
                                    "auto_approved": auto_count,
                                    "pending_review": len(drafts) - auto_count,
                                },
                                priority=3,
                            )
                        )
                except Exception:
                    log.exception(
                        "heartbeat_reply_gen_failed", channel_id=str(channel_id)
                    )
        except Exception:
            log.exception("management_heartbeat_failed")

        return actions

    # ------------------------------------------------------------------
    # System prompt fragment
    # ------------------------------------------------------------------

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        if self._status != SkillStatus.READY:
            return None
        return "[YouTube Management: Ready — auto-replies, tags, health audits]"


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
    """Convert a DB row into an API-friendly dict."""
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


def _parse_json(raw: str) -> Any:
    """Parse JSON from LLM output, stripping markdown fences."""
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        lines = cleaned.splitlines()
        cleaned = "\n".join(lines[1:])
        if cleaned.rstrip().endswith("```"):
            cleaned = cleaned.rstrip()[:-3]
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        log.warning("json_parse_failed", raw=raw[:200])
        return {}
