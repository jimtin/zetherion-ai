"""Milestone & Promotion Skill for Zetherion AI.

Detects significant development milestones from dev watcher events and
generates platform-specific social media drafts (X/Twitter, LinkedIn, GitHub).

Capabilities:
- Detect milestones from commits, tags, and cumulative progress
- Generate draft posts tailored to each platform
- Draft approval workflow (review, approve, reject)
- Heartbeat: notify when milestones are detected
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.skills.milestone")

MILESTONES_COLLECTION = "skill_milestones"

# Minimum significance score to generate drafts (1-10 scale)
DRAFT_THRESHOLD = 6

PLATFORMS = ["x", "linkedin", "github"]


@dataclass
class Milestone:
    """A detected development milestone."""

    id: UUID = field(default_factory=uuid4)
    user_id: str = ""
    title: str = ""
    description: str = ""
    category: str = ""  # "feature", "architecture", "release", "coverage", "integration"
    significance: int = 0  # 1-10 scale
    detected_from: str = ""  # "commit", "tag", "cumulative", "annotation"
    source_entries: list[str] = field(default_factory=list)  # DevEntry IDs
    status: str = "detected"  # "detected", "drafts_ready", "posted", "dismissed"
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "title": self.title,
            "description": self.description,
            "category": self.category,
            "significance": self.significance,
            "detected_from": self.detected_from,
            "source_entries": self.source_entries,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Milestone":
        return cls(
            id=UUID(data["id"]) if data.get("id") else uuid4(),
            user_id=data.get("user_id", ""),
            title=data.get("title", ""),
            description=data.get("description", ""),
            category=data.get("category", ""),
            significance=data.get("significance", 0),
            detected_from=data.get("detected_from", ""),
            source_entries=data.get("source_entries", []),
            status=data.get("status", "detected"),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now(),
        )


@dataclass
class PromoDraft:
    """A platform-specific promotional draft for a milestone."""

    id: UUID = field(default_factory=uuid4)
    milestone_id: UUID = field(default_factory=uuid4)
    user_id: str = ""
    platform: str = ""  # "x", "linkedin", "github"
    content: str = ""
    status: str = "pending"  # "pending", "approved", "rejected", "posted"
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": str(self.id),
            "milestone_id": str(self.milestone_id),
            "user_id": self.user_id,
            "platform": self.platform,
            "content": self.content,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "PromoDraft":
        return cls(
            id=UUID(data["id"]) if data.get("id") else uuid4(),
            milestone_id=UUID(data["milestone_id"]) if data.get("milestone_id") else uuid4(),
            user_id=data.get("user_id", ""),
            platform=data.get("platform", ""),
            content=data.get("content", ""),
            status=data.get("status", "pending"),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now(),
        )


class MilestoneSkill(Skill):
    """Skill for detecting milestones and generating promotional content.

    Intents:
    - milestone_list: Show detected milestones
    - milestone_drafts: Show pending promo drafts
    - milestone_approve: Approve a draft
    - milestone_reject: Reject/dismiss a draft
    - milestone_detect: Evaluate a dev event for milestone significance
    """

    INTENTS = [
        "milestone_list",
        "milestone_drafts",
        "milestone_approve",
        "milestone_reject",
        "milestone_detect",
    ]

    def __init__(self, memory: "QdrantMemory | None" = None):
        super().__init__(memory=memory)
        self._milestones_cache: dict[str, dict[UUID, Milestone]] = {}
        self._drafts_cache: dict[str, dict[UUID, PromoDraft]] = {}

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="milestone_tracker",
            description="Detect dev milestones and generate promotional social media drafts",
            version="1.0.0",
            permissions=PermissionSet(
                {
                    Permission.READ_OWN_COLLECTION,
                    Permission.WRITE_OWN_COLLECTION,
                    Permission.SEND_MESSAGES,
                    Permission.READ_PROFILE,
                }
            ),
            collections=[MILESTONES_COLLECTION],
            intents=self.INTENTS,
        )

    async def initialize(self) -> bool:
        if not self._memory:
            log.warning("milestone_no_memory", msg="No memory provided, using in-memory only")
            return True
        try:
            await self._memory.ensure_collection(
                MILESTONES_COLLECTION,
                vector_size=768,
            )
            log.info("milestone_initialized", collection=MILESTONES_COLLECTION)
            return True
        except Exception as e:
            log.error("milestone_init_failed", error=str(e))
            return False

    async def handle(self, request: SkillRequest) -> SkillResponse:
        handlers = {
            "milestone_list": self._handle_list,
            "milestone_drafts": self._handle_drafts,
            "milestone_approve": self._handle_approve,
            "milestone_reject": self._handle_reject,
            "milestone_detect": self._handle_detect,
        }
        handler = handlers.get(request.intent)
        if not handler:
            return SkillResponse.error_response(request.id, f"Unknown intent: {request.intent}")
        return await handler(request)

    # ------------------------------------------------------------------
    # Handlers
    # ------------------------------------------------------------------

    async def _handle_list(self, request: SkillRequest) -> SkillResponse:
        """List detected milestones."""
        milestones = await self._get_user_milestones(request.user_id)
        if not milestones:
            return SkillResponse(
                request_id=request.id,
                message="No milestones detected yet. Keep coding — I'll spot them as they happen!",
            )

        milestones.sort(key=lambda m: m.created_at, reverse=True)
        parts = [f"**Milestones ({len(milestones)}):**\n"]
        for m in milestones[:10]:
            status_icon = _status_icon(m.status)
            date_str = m.created_at.strftime("%Y-%m-%d")
            parts.append(
                f"  {status_icon} **{m.title}** (significance: {m.significance}/10, "
                f"{m.category}, {date_str})"
            )
            if m.description:
                parts.append(f"     {m.description[:100]}")

        return SkillResponse(
            request_id=request.id,
            message="\n".join(parts),
            data={"milestones": [m.to_dict() for m in milestones[:10]]},
        )

    async def _handle_drafts(self, request: SkillRequest) -> SkillResponse:
        """Show pending promotional drafts."""
        drafts = await self._get_user_drafts(request.user_id)
        pending = [d for d in drafts if d.status == "pending"]

        if not pending:
            return SkillResponse(
                request_id=request.id,
                message="No pending promo drafts. "
                "Drafts are generated when milestones are detected.",
            )

        # Group drafts by milestone
        by_milestone: dict[UUID, list[PromoDraft]] = {}
        for d in pending:
            by_milestone.setdefault(d.milestone_id, []).append(d)

        parts = [f"**Pending Promo Drafts ({len(pending)}):**\n"]
        milestones = await self._get_user_milestones(request.user_id)
        milestone_map = {m.id: m for m in milestones}

        for ms_id, ms_drafts in by_milestone.items():
            ms = milestone_map.get(ms_id)
            ms_title = ms.title if ms else "Unknown milestone"
            parts.append(f"\n**{ms_title}:**")
            for d in ms_drafts:
                platform_label = _platform_label(d.platform)
                parts.append(f"\n  {platform_label} (ID: `{str(d.id)[:8]}`):")
                # Truncate content for display
                content_preview = d.content[:200] + "..." if len(d.content) > 200 else d.content
                parts.append(f"  > {content_preview}")

        parts.append(
            "\nTo approve: tell me to approve with the draft ID. "
            "To reject: tell me to reject with the draft ID."
        )

        return SkillResponse(
            request_id=request.id,
            message="\n".join(parts),
            data={"drafts": [d.to_dict() for d in pending]},
        )

    async def _handle_approve(self, request: SkillRequest) -> SkillResponse:
        """Approve a draft for posting."""
        draft_id_str = request.context.get("draft_id", "")
        if not draft_id_str:
            # Try to find ID in the message
            return SkillResponse.error_response(
                request.id, "Please specify which draft to approve (include the draft ID)."
            )

        draft = await self._find_draft(request.user_id, draft_id_str)
        if not draft:
            return SkillResponse.error_response(request.id, f"Draft not found: {draft_id_str}")

        draft.status = "approved"
        await self._store_draft(draft)

        return SkillResponse(
            request_id=request.id,
            message=f"Approved {_platform_label(draft.platform)} draft. "
            f"Content is ready to post:\n\n{draft.content}",
            data={"draft": draft.to_dict()},
        )

    async def _handle_reject(self, request: SkillRequest) -> SkillResponse:
        """Reject/dismiss a draft."""
        draft_id_str = request.context.get("draft_id", "")
        if not draft_id_str:
            return SkillResponse.error_response(
                request.id, "Please specify which draft to reject (include the draft ID)."
            )

        draft = await self._find_draft(request.user_id, draft_id_str)
        if not draft:
            return SkillResponse.error_response(request.id, f"Draft not found: {draft_id_str}")

        draft.status = "rejected"
        await self._store_draft(draft)

        return SkillResponse(
            request_id=request.id,
            message=f"Rejected {_platform_label(draft.platform)} draft.",
            data={"draft": draft.to_dict()},
        )

    async def _handle_detect(self, request: SkillRequest) -> SkillResponse:
        """Evaluate a dev event for milestone significance.

        This is called internally when the dev_watcher ingests significant events.
        Uses heuristics to score significance; LLM evaluation can be added later.
        """
        ctx = request.context
        event_type = ctx.get("event_type", "commit")
        title = ctx.get("title", request.message[:200])
        description = ctx.get("description", request.message)

        significance, category = _score_significance(event_type, title, description, ctx)

        if significance < DRAFT_THRESHOLD:
            return SkillResponse(
                request_id=request.id,
                message=f"Event scored {significance}/10 — below milestone threshold.",
                data={"significance": significance, "milestone": False},
            )

        # Create milestone
        milestone = Milestone(
            user_id=request.user_id,
            title=title,
            description=description[:500],
            category=category,
            significance=significance,
            detected_from=event_type,
            source_entries=ctx.get("source_entries", []),
            status="drafts_ready",
        )
        await self._store_milestone(milestone)

        # Generate drafts for each platform
        drafts_created = []
        for platform in PLATFORMS:
            draft_content = _generate_draft(platform, milestone)
            draft = PromoDraft(
                milestone_id=milestone.id,
                user_id=request.user_id,
                platform=platform,
                content=draft_content,
            )
            await self._store_draft(draft)
            drafts_created.append(draft)

        log.info(
            "milestone_detected",
            title=title[:80],
            significance=significance,
            category=category,
            drafts=len(drafts_created),
        )

        return SkillResponse(
            request_id=request.id,
            message=f"Milestone detected: **{title}** (significance: {significance}/10). "
            f"Generated {len(drafts_created)} draft(s). Use `show milestone drafts` to review.",
            data={
                "milestone": milestone.to_dict(),
                "drafts": [d.to_dict() for d in drafts_created],
            },
        )

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        actions: list[HeartbeatAction] = []

        for user_id in user_ids:
            drafts = await self._get_user_drafts(user_id)
            pending = [d for d in drafts if d.status == "pending"]
            if pending:
                actions.append(
                    HeartbeatAction(
                        skill_name=self.name,
                        action_type="milestone_drafts_pending",
                        user_id=user_id,
                        data={
                            "count": len(pending),
                            "platforms": list({d.platform for d in pending}),
                        },
                        priority=4,
                    )
                )

        return actions

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    async def _store_milestone(self, milestone: Milestone) -> None:
        self._milestones_cache.setdefault(milestone.user_id, {})[milestone.id] = milestone
        if self._memory:
            search_text = (
                f"milestone {milestone.category} {milestone.title} {milestone.description}"
            )
            await self._memory.store_with_payload(
                collection_name=MILESTONES_COLLECTION,
                text=search_text,
                payload={**milestone.to_dict(), "_type": "milestone"},
                point_id=str(milestone.id),
            )

    async def _store_draft(self, draft: PromoDraft) -> None:
        self._drafts_cache.setdefault(draft.user_id, {})[draft.id] = draft
        if self._memory:
            search_text = f"draft {draft.platform} {draft.content[:200]}"
            await self._memory.store_with_payload(
                collection_name=MILESTONES_COLLECTION,
                text=search_text,
                payload={**draft.to_dict(), "_type": "draft"},
                point_id=str(draft.id),
            )

    async def _get_user_milestones(self, user_id: str) -> list[Milestone]:
        if self._memory:
            results = await self._memory.filter_by_field(
                collection_name=MILESTONES_COLLECTION,
                field="_type",
                value="milestone",
            )
            milestones = [Milestone.from_dict(r) for r in results if r.get("user_id") == user_id]
            self._milestones_cache[user_id] = {m.id: m for m in milestones}
            return milestones
        return list(self._milestones_cache.get(user_id, {}).values())

    async def _get_user_drafts(self, user_id: str) -> list[PromoDraft]:
        if self._memory:
            results = await self._memory.filter_by_field(
                collection_name=MILESTONES_COLLECTION,
                field="_type",
                value="draft",
            )
            drafts = [PromoDraft.from_dict(r) for r in results if r.get("user_id") == user_id]
            self._drafts_cache[user_id] = {d.id: d for d in drafts}
            return drafts
        return list(self._drafts_cache.get(user_id, {}).values())

    async def _find_draft(self, user_id: str, draft_id_prefix: str) -> PromoDraft | None:
        """Find a draft by ID or ID prefix."""
        drafts = await self._get_user_drafts(user_id)
        for d in drafts:
            if str(d.id).startswith(draft_id_prefix):
                return d
        return None

    async def cleanup(self) -> None:
        self._milestones_cache.clear()
        self._drafts_cache.clear()
        log.info("milestone_cleanup_complete")


# ------------------------------------------------------------------
# Heuristic significance scoring
# ------------------------------------------------------------------


def _score_significance(
    event_type: str,
    title: str,
    description: str,
    ctx: dict[str, Any],
) -> tuple[int, str]:
    """Score an event's milestone significance using heuristics.

    Returns (significance 1-10, category string).
    LLM-based scoring can augment this later.
    """
    title_lower = title.lower()
    desc_lower = description.lower()
    combined = f"{title_lower} {desc_lower}"

    # Tags/releases are always significant
    if event_type == "tag":
        return 8, "release"

    # Feature commits
    if title_lower.startswith("feat:") or title_lower.startswith("feat("):
        files_changed = int(ctx.get("files_changed", 0) or 0)
        if files_changed >= 10:
            return 8, "feature"
        if files_changed >= 5:
            return 7, "feature"
        return 6, "feature"

    # Architecture changes
    architecture_signals = [
        "refactor",
        "architecture",
        "restructure",
        "migrate",
        "docker",
        "compose",
        "ci/cd",
        "pipeline",
    ]
    if any(s in combined for s in architecture_signals):
        files_changed = int(ctx.get("files_changed", 0) or 0)
        if files_changed >= 10:
            return 8, "architecture"
        return 6, "architecture"

    # Integration keywords
    integration_signals = [
        "integration",
        "api",
        "webhook",
        "oauth",
        "gmail",
        "github",
        "discord",
        "slack",
        "database",
    ]
    if any(s in combined for s in integration_signals):
        return 7, "integration"

    # Security improvements
    security_signals = ["security", "encrypt", "auth", "rbac", "permission"]
    if any(s in combined for s in security_signals):
        return 6, "security"

    # Test/coverage improvements
    test_signals = ["test", "coverage", "ci", "pytest"]
    if any(s in combined for s in test_signals):
        return 5, "coverage"

    # Performance
    perf_signals = ["performance", "optimiz", "cache", "speed"]
    if any(s in combined for s in perf_signals):
        return 5, "performance"

    # Default: routine work
    return 3, "maintenance"


# ------------------------------------------------------------------
# Draft generation (template-based; LLM generation can replace later)
# ------------------------------------------------------------------


def _generate_draft(platform: str, milestone: Milestone) -> str:
    """Generate a platform-specific draft post for a milestone.

    Uses templates for now. LLM-based generation can augment this later.
    """
    if platform == "x":
        return _generate_x_draft(milestone)
    elif platform == "linkedin":
        return _generate_linkedin_draft(milestone)
    elif platform == "github":
        return _generate_github_draft(milestone)
    return f"[{platform}] {milestone.title}: {milestone.description[:200]}"


def _generate_x_draft(milestone: Milestone) -> str:
    """Generate a tweet-length draft."""
    category_hashtags = {
        "feature": "#buildinpublic #devlife",
        "architecture": "#softwarearchitecture #engineering",
        "release": "#release #opensource",
        "integration": "#api #integration",
        "security": "#security #infosec",
        "coverage": "#testing #quality",
        "performance": "#performance #optimization",
        "maintenance": "#coding #devlife",
    }
    hashtags = category_hashtags.get(milestone.category, "#buildinpublic")

    title = milestone.title
    # Remove conventional commit prefixes for cleaner tweets
    for prefix in ("feat: ", "feat(", "fix: ", "refactor: ", "chore: "):
        if title.lower().startswith(prefix):
            title = title[len(prefix) :]
            if title.startswith(")"):
                # Handle feat(scope): format
                title = title.split("): ", 1)[-1] if "): " in title else title[2:]
            break

    desc = milestone.description[:150] if milestone.description else ""
    tweet = f"{title.strip().capitalize()}"
    if desc and desc != title:
        tweet += f" — {desc}"

    # Trim to leave room for hashtags
    max_content = 280 - len(hashtags) - 2
    if len(tweet) > max_content:
        tweet = tweet[: max_content - 3] + "..."

    return f"{tweet}\n\n{hashtags}"


def _generate_linkedin_draft(milestone: Milestone) -> str:
    """Generate a LinkedIn-style post."""
    title = milestone.title
    desc = milestone.description or "No description"

    return (
        f"Excited to share a development milestone on my AI assistant project:\n\n"
        f"**{title}**\n\n"
        f"{desc}\n\n"
        f"This falls under {milestone.category} work and represents a "
        f"significant step forward. Building an AI assistant that truly "
        f"understands its user requires getting these foundations right.\n\n"
        f"More updates to come as the project evolves.\n\n"
        f"#AI #SoftwareEngineering #BuildInPublic"
    )


def _generate_github_draft(milestone: Milestone) -> str:
    """Generate a GitHub release note / discussion post."""
    return (
        f"## {milestone.title}\n\n"
        f"{milestone.description or 'No description provided.'}\n\n"
        f"**Category:** {milestone.category}\n"
        f"**Significance:** {milestone.significance}/10\n\n"
        f"### What changed\n\n"
        f"See the associated commits for details.\n\n"
        f"### What's next\n\n"
        f"Stay tuned for more updates."
    )


def _status_icon(status: str) -> str:
    icons = {
        "detected": "[new]",
        "drafts_ready": "[drafts]",
        "posted": "[posted]",
        "dismissed": "[dismissed]",
    }
    return icons.get(status, "[?]")


def _platform_label(platform: str) -> str:
    labels = {
        "x": "X/Twitter",
        "linkedin": "LinkedIn",
        "github": "GitHub",
    }
    return labels.get(platform, platform)
