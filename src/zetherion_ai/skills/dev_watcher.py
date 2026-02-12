"""Dev Watcher Skill for Zetherion AI.

Passively monitors development activity via Discord webhook events from the
zetherion-dev-agent and builds a queryable development journal.

Capabilities:
- Ingest commits, annotations, Claude Code sessions, and tags
- Query development status, ideas, journal, and summaries
- Heartbeat: daily summaries, idea reminders, stale annotation alerts
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
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

log = get_logger("zetherion_ai.skills.dev_watcher")

DEV_JOURNAL_COLLECTION = "skill_dev_journal"

# How many days before an annotation is considered stale
STALE_ANNOTATION_DAYS = 14
# How many days before surfacing an idea reminder
IDEA_REMINDER_DAYS = 5
# Max entries in query results
DEFAULT_QUERY_LIMIT = 20


@dataclass
class DevEntry:
    """A single development journal entry."""

    id: UUID = field(default_factory=uuid4)
    user_id: str = ""
    entry_type: str = ""  # "commit", "annotation", "session", "tag", "idea", "note"
    project: str = ""
    title: str = ""
    content: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    status: str = "active"  # "active", "resolved", "archived"
    created_at: datetime = field(default_factory=datetime.now)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary for storage."""
        return {
            "id": str(self.id),
            "user_id": self.user_id,
            "entry_type": self.entry_type,
            "project": self.project,
            "title": self.title,
            "content": self.content,
            "metadata": self.metadata,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "DevEntry":
        """Create from dictionary."""
        return cls(
            id=UUID(data["id"]) if data.get("id") else uuid4(),
            user_id=data.get("user_id", ""),
            entry_type=data.get("entry_type", ""),
            project=data.get("project", ""),
            title=data.get("title", ""),
            content=data.get("content", ""),
            metadata=data.get("metadata", {}),
            status=data.get("status", "active"),
            created_at=datetime.fromisoformat(data["created_at"])
            if data.get("created_at")
            else datetime.now(),
        )

    def is_stale(self, days: int = STALE_ANNOTATION_DAYS) -> bool:
        """Check if entry hasn't been resolved and is old."""
        if self.status != "active":
            return False
        return datetime.now() - self.created_at > timedelta(days=days)


class DevWatcherSkill(Skill):
    """Skill for monitoring and querying development activity.

    Ingestion intents (passive, from webhook events):
    - dev_ingest_commit: Store a commit
    - dev_ingest_annotation: Store a TODO/FIXME/IDEA annotation
    - dev_ingest_session: Store a Claude Code session summary
    - dev_ingest_tag: Store a version tag

    Query intents (active, from user messages):
    - dev_status: What am I working on?
    - dev_next: What should I work on next?
    - dev_ideas: What ideas have I had?
    - dev_journal: What did I do recently?
    - dev_summary: Give me a dev summary
    """

    INGEST_INTENTS = [
        "dev_ingest_commit",
        "dev_ingest_annotation",
        "dev_ingest_session",
        "dev_ingest_tag",
    ]

    QUERY_INTENTS = [
        "dev_status",
        "dev_next",
        "dev_ideas",
        "dev_journal",
        "dev_summary",
    ]

    INTENTS = INGEST_INTENTS + QUERY_INTENTS

    def __init__(self, memory: "QdrantMemory | None" = None):
        super().__init__(memory=memory)
        self._entries_cache: dict[str, dict[UUID, DevEntry]] = {}

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="dev_watcher",
            description="Monitor development activity and build a queryable dev journal",
            version="1.0.0",
            permissions=PermissionSet(
                {
                    Permission.READ_OWN_COLLECTION,
                    Permission.WRITE_OWN_COLLECTION,
                    Permission.SEND_MESSAGES,
                    Permission.READ_PROFILE,
                }
            ),
            collections=[DEV_JOURNAL_COLLECTION],
            intents=self.INTENTS,
        )

    async def initialize(self) -> bool:
        if not self._memory:
            log.warning("dev_watcher_no_memory", msg="No memory provided, using in-memory only")
            return True
        try:
            await self._memory.ensure_collection(
                DEV_JOURNAL_COLLECTION,
                vector_size=768,
            )
            log.info("dev_watcher_initialized", collection=DEV_JOURNAL_COLLECTION)
            return True
        except Exception as e:
            log.error("dev_watcher_init_failed", error=str(e))
            return False

    async def handle(self, request: SkillRequest) -> SkillResponse:
        handlers = {
            # Ingestion
            "dev_ingest_commit": self._handle_ingest_commit,
            "dev_ingest_annotation": self._handle_ingest_annotation,
            "dev_ingest_session": self._handle_ingest_session,
            "dev_ingest_tag": self._handle_ingest_tag,
            # Queries
            "dev_status": self._handle_status,
            "dev_next": self._handle_next,
            "dev_ideas": self._handle_ideas,
            "dev_journal": self._handle_journal,
            "dev_summary": self._handle_summary,
        }
        handler = handlers.get(request.intent)
        if not handler:
            return SkillResponse.error_response(request.id, f"Unknown intent: {request.intent}")
        return await handler(request)

    # ------------------------------------------------------------------
    # Ingestion handlers (passive)
    # ------------------------------------------------------------------

    async def _handle_ingest_commit(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        entry = DevEntry(
            user_id=request.user_id,
            entry_type="commit",
            project=ctx.get("project", ""),
            title=ctx.get("message", request.message)[:200],
            content=request.message,
            metadata={
                "sha": ctx.get("sha", ""),
                "files_changed": ctx.get("files_changed", ""),
                "diff_summary": ctx.get("diff_summary", ""),
                "branch": ctx.get("branch", ""),
            },
        )
        await self._store_entry(entry)
        log.info("dev_commit_ingested", sha=ctx.get("sha", "")[:8], project=entry.project)
        return SkillResponse(request_id=request.id, message=f"Ingested commit: {entry.title[:80]}")

    async def _handle_ingest_annotation(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        annotation_type = ctx.get("annotation_type", "TODO")  # TODO, FIXME, IDEA, HACK
        entry = DevEntry(
            user_id=request.user_id,
            entry_type="annotation",
            project=ctx.get("project", ""),
            title=f"{annotation_type}: {request.message[:150]}",
            content=request.message,
            metadata={
                "annotation_type": annotation_type,
                "file": ctx.get("file", ""),
                "line": ctx.get("line", 0),
                "action": ctx.get("action", "added"),  # "added" or "removed"
            },
        )

        # If an annotation was removed, mark existing ones as resolved
        if ctx.get("action") == "removed":
            await self._resolve_annotation(request.user_id, ctx.get("file", ""), request.message)
            entry.status = "resolved"

        await self._store_entry(entry)
        log.info(
            "dev_annotation_ingested",
            annotation_type=annotation_type,
            action=ctx.get("action", "added"),
        )
        return SkillResponse(
            request_id=request.id, message=f"Ingested {annotation_type} annotation"
        )

    async def _handle_ingest_session(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        entry = DevEntry(
            user_id=request.user_id,
            entry_type="session",
            project=ctx.get("project", ""),
            title=ctx.get("summary", request.message[:200]),
            content=request.message,
            metadata={
                "session_id": ctx.get("session_id", ""),
                "duration_minutes": ctx.get("duration_minutes", 0),
                "tools_used": ctx.get("tools_used", 0),
            },
        )
        await self._store_entry(entry)
        log.info("dev_session_ingested", project=entry.project)
        return SkillResponse(request_id=request.id, message="Ingested Claude Code session")

    async def _handle_ingest_tag(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        entry = DevEntry(
            user_id=request.user_id,
            entry_type="tag",
            project=ctx.get("project", ""),
            title=f"Tag: {ctx.get('tag_name', request.message)}",
            content=request.message,
            metadata={
                "tag_name": ctx.get("tag_name", ""),
                "sha": ctx.get("sha", ""),
            },
        )
        await self._store_entry(entry)
        log.info("dev_tag_ingested", tag=ctx.get("tag_name", ""))
        return SkillResponse(
            request_id=request.id,
            message=f"Ingested tag: {ctx.get('tag_name', '')}",
        )

    # ------------------------------------------------------------------
    # Query handlers (active)
    # ------------------------------------------------------------------

    async def _handle_status(self, request: SkillRequest) -> SkillResponse:
        """What am I currently working on?"""
        entries = await self._get_recent_entries(
            request.user_id, limit=10, entry_types=["commit", "session"]
        )
        if not entries:
            return SkillResponse(
                request_id=request.id,
                message="No recent development activity recorded yet. "
                "Make sure the dev agent is running and sending events.",
            )

        # Group by project
        by_project: dict[str, list[DevEntry]] = {}
        for e in entries:
            proj = e.project or "unknown"
            by_project.setdefault(proj, []).append(e)

        parts = ["**Current Dev Activity:**\n"]
        for project, proj_entries in by_project.items():
            parts.append(f"\n**{project}**")
            for e in proj_entries[:5]:
                age = _format_age(e.created_at)
                if e.entry_type == "commit":
                    sha = e.metadata.get("sha", "")[:7]
                    parts.append(f"  - `{sha}` {e.title} ({age})")
                elif e.entry_type == "session":
                    parts.append(f"  - Session: {e.title[:80]} ({age})")

        # Active annotations
        annotations = await self._get_active_annotations(request.user_id, limit=5)
        if annotations:
            parts.append("\n**Active Annotations:**")
            for a in annotations:
                atype = a.metadata.get("annotation_type", "TODO")
                afile = a.metadata.get("file", "")
                parts.append(f"  - [{atype}] {a.content[:60]} ({afile})")

        return SkillResponse(
            request_id=request.id,
            message="\n".join(parts),
            data={"entries": [e.to_dict() for e in entries]},
        )

    async def _handle_next(self, request: SkillRequest) -> SkillResponse:
        """What should I work on next?"""
        # Gather open items: active annotations, unresolved ideas, recent session decisions
        annotations = await self._get_active_annotations(request.user_id, limit=15)
        ideas = await self._get_entries_by_type(request.user_id, "annotation", limit=20)
        ideas = [
            i for i in ideas if i.status == "active" and i.metadata.get("annotation_type") == "IDEA"
        ]

        parts = ["**Suggestions for what to work on next:**\n"]

        # TODOs (highest priority — explicit work items)
        todos = [
            a for a in annotations if a.metadata.get("annotation_type") in ("TODO", "FIXME", "HACK")
        ]
        if todos:
            parts.append("**Open TODOs/FIXMEs:**")
            for t in todos[:7]:
                age = _format_age(t.created_at)
                afile = t.metadata.get("file", "")
                stale = " (stale!)" if t.is_stale() else ""
                parts.append(
                    f"  - [{t.metadata.get('annotation_type')}] {t.content[:60]} "
                    f"({afile}, {age}){stale}"
                )

        # Ideas
        if ideas:
            parts.append("\n**Captured Ideas:**")
            for i in ideas[:5]:
                age = _format_age(i.created_at)
                parts.append(f"  - {i.content[:80]} ({age})")

        # Recent sessions (decisions/open questions)
        sessions = await self._get_entries_by_type(request.user_id, "session", limit=5)
        if sessions:
            parts.append("\n**Recent Session Context:**")
            for s in sessions[:3]:
                age = _format_age(s.created_at)
                parts.append(f"  - {s.title[:80]} ({age})")

        if len(parts) == 1:
            parts.append(
                "No open items found. Either everything is done or "
                "the dev agent hasn't captured any annotations yet."
            )

        return SkillResponse(request_id=request.id, message="\n".join(parts))

    async def _handle_ideas(self, request: SkillRequest) -> SkillResponse:
        """What ideas have I had?"""
        all_annotations = await self._get_entries_by_type(request.user_id, "annotation", limit=50)
        ideas = [
            a
            for a in all_annotations
            if a.metadata.get("annotation_type") == "IDEA" and a.status == "active"
        ]

        if not ideas:
            return SkillResponse(
                request_id=request.id,
                message="No active ideas captured. Add `IDEA:` comments in your "
                "code to have them tracked.",
            )

        parts = [f"**Captured Ideas ({len(ideas)}):**\n"]
        for idea in ideas:
            age = _format_age(idea.created_at)
            afile = idea.metadata.get("file", "")
            loc = f" ({afile})" if afile else ""
            parts.append(f"  - {idea.content[:100]}{loc} — {age}")

        return SkillResponse(
            request_id=request.id,
            message="\n".join(parts),
            data={"ideas": [i.to_dict() for i in ideas]},
        )

    async def _handle_journal(self, request: SkillRequest) -> SkillResponse:
        """What did I do recently?"""
        entries = await self._get_recent_entries(request.user_id, limit=DEFAULT_QUERY_LIMIT)
        if not entries:
            return SkillResponse(request_id=request.id, message="No journal entries yet.")

        # Group by date
        by_date: dict[str, list[DevEntry]] = {}
        for e in entries:
            date_key = e.created_at.strftime("%Y-%m-%d")
            by_date.setdefault(date_key, []).append(e)

        parts = ["**Dev Journal:**\n"]
        for date_str, day_entries in sorted(by_date.items(), reverse=True):
            parts.append(f"\n**{date_str}**")
            for e in day_entries:
                time_str = e.created_at.strftime("%H:%M")
                icon = _entry_type_icon(e.entry_type)
                parts.append(f"  {icon} [{time_str}] {e.title[:80]}")

        return SkillResponse(
            request_id=request.id,
            message="\n".join(parts),
            data={"entries": [e.to_dict() for e in entries]},
        )

    async def _handle_summary(self, request: SkillRequest) -> SkillResponse:
        """Generate a narrative dev summary."""
        entries = await self._get_recent_entries(request.user_id, limit=30)
        if not entries:
            return SkillResponse(request_id=request.id, message="No dev activity to summarise.")

        commits = [e for e in entries if e.entry_type == "commit"]
        annotations = [e for e in entries if e.entry_type == "annotation"]
        sessions = [e for e in entries if e.entry_type == "session"]
        tags = [e for e in entries if e.entry_type == "tag"]

        active_annotations = [a for a in annotations if a.status == "active"]
        resolved_annotations = [a for a in annotations if a.status == "resolved"]

        projects = {e.project for e in entries if e.project}

        parts = ["**Dev Summary:**\n"]
        parts.append(
            f"Projects active: {', '.join(projects) if projects else 'none'}\n"
            f"Commits: {len(commits)} | "
            f"Sessions: {len(sessions)} | "
            f"Tags: {len(tags)}\n"
            f"Annotations: {len(active_annotations)} open, "
            f"{len(resolved_annotations)} resolved"
        )

        if commits:
            parts.append("\n**Recent Commits:**")
            for c in commits[:5]:
                sha = c.metadata.get("sha", "")[:7]
                parts.append(f"  - `{sha}` {c.title[:80]}")

        if tags:
            parts.append("\n**Tags/Releases:**")
            for t in tags[:3]:
                parts.append(f"  - {t.title}")

        ideas = [a for a in active_annotations if a.metadata.get("annotation_type") == "IDEA"]
        if ideas:
            parts.append(f"\n**Open Ideas:** {len(ideas)}")

        return SkillResponse(
            request_id=request.id,
            message="\n".join(parts),
            data={
                "commits": len(commits),
                "sessions": len(sessions),
                "tags": len(tags),
                "active_annotations": len(active_annotations),
                "projects": list(projects),
            },
        )

    # ------------------------------------------------------------------
    # Heartbeat
    # ------------------------------------------------------------------

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        actions: list[HeartbeatAction] = []

        for user_id in user_ids:
            # Stale annotations
            annotations = await self._get_active_annotations(user_id, limit=50)
            stale = [a for a in annotations if a.is_stale()]
            if stale:
                stale_types: dict[str, int] = {}
                for s in stale:
                    t = s.metadata.get("annotation_type", "TODO")
                    stale_types[t] = stale_types.get(t, 0) + 1
                actions.append(
                    HeartbeatAction(
                        skill_name=self.name,
                        action_type="dev_stale_annotation",
                        user_id=user_id,
                        data={
                            "count": len(stale),
                            "by_type": stale_types,
                            "entries": [s.to_dict() for s in stale[:5]],
                        },
                        priority=2,
                    )
                )

            # Idea reminders (ideas older than IDEA_REMINDER_DAYS)
            all_annotations = await self._get_entries_by_type(user_id, "annotation", limit=50)
            old_ideas = [
                a
                for a in all_annotations
                if a.status == "active"
                and a.metadata.get("annotation_type") == "IDEA"
                and datetime.now() - a.created_at > timedelta(days=IDEA_REMINDER_DAYS)
            ]
            if old_ideas:
                actions.append(
                    HeartbeatAction(
                        skill_name=self.name,
                        action_type="dev_idea_reminder",
                        user_id=user_id,
                        data={
                            "count": len(old_ideas),
                            "ideas": [i.to_dict() for i in old_ideas[:3]],
                        },
                        priority=3,
                    )
                )

        return actions

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        if user_id not in self._entries_cache:
            return None
        entries = list(self._entries_cache[user_id].values())
        if not entries:
            return None
        active = [e for e in entries if e.status == "active"]
        commits = [e for e in entries if e.entry_type == "commit"]
        ideas = [
            e
            for e in active
            if e.entry_type == "annotation" and e.metadata.get("annotation_type") == "IDEA"
        ]
        fragment = f"Dev activity: {len(commits)} recent commit(s)"
        if ideas:
            fragment += f", {len(ideas)} open idea(s)"
        return fragment + "."

    # ------------------------------------------------------------------
    # Storage helpers
    # ------------------------------------------------------------------

    async def _store_entry(self, entry: DevEntry) -> None:
        if entry.user_id not in self._entries_cache:
            self._entries_cache[entry.user_id] = {}
        self._entries_cache[entry.user_id][entry.id] = entry

        if self._memory:
            search_text = f"{entry.entry_type} {entry.project} {entry.title} {entry.content}"
            await self._memory.store_with_payload(
                collection_name=DEV_JOURNAL_COLLECTION,
                text=search_text,
                payload=entry.to_dict(),
                point_id=str(entry.id),
            )

    async def _get_recent_entries(
        self,
        user_id: str,
        limit: int = DEFAULT_QUERY_LIMIT,
        entry_types: list[str] | None = None,
    ) -> list[DevEntry]:
        entries = await self._get_user_entries(user_id)
        if entry_types:
            entries = [e for e in entries if e.entry_type in entry_types]
        entries.sort(key=lambda e: e.created_at, reverse=True)
        return entries[:limit]

    async def _get_active_annotations(self, user_id: str, limit: int = 20) -> list[DevEntry]:
        all_annotations = await self._get_entries_by_type(user_id, "annotation", limit=100)
        active = [a for a in all_annotations if a.status == "active"]
        active.sort(key=lambda e: e.created_at, reverse=True)
        return active[:limit]

    async def _get_entries_by_type(
        self, user_id: str, entry_type: str, limit: int = 50
    ) -> list[DevEntry]:
        entries = await self._get_user_entries(user_id)
        typed = [e for e in entries if e.entry_type == entry_type]
        typed.sort(key=lambda e: e.created_at, reverse=True)
        return typed[:limit]

    async def _get_user_entries(self, user_id: str) -> list[DevEntry]:
        if self._memory:
            results = await self._memory.filter_by_field(
                collection_name=DEV_JOURNAL_COLLECTION,
                field="user_id",
                value=user_id,
            )
            entries = [DevEntry.from_dict(r) for r in results]
            self._entries_cache[user_id] = {e.id: e for e in entries}
            return entries
        if user_id in self._entries_cache:
            return list(self._entries_cache[user_id].values())
        return []

    async def _resolve_annotation(self, user_id: str, file_path: str, content: str) -> None:
        """Mark matching active annotations as resolved."""
        annotations = await self._get_active_annotations(user_id, limit=100)
        for ann in annotations:
            if ann.metadata.get("file") == file_path and ann.content == content:
                ann.status = "resolved"
                await self._store_entry(ann)

    async def cleanup(self) -> None:
        self._entries_cache.clear()
        log.info("dev_watcher_cleanup_complete")


def _format_age(dt: datetime) -> str:
    """Format a datetime as a human-readable age string."""
    delta = datetime.now() - dt
    if delta.days > 0:
        return f"{delta.days}d ago"
    hours = delta.seconds // 3600
    if hours > 0:
        return f"{hours}h ago"
    minutes = delta.seconds // 60
    return f"{minutes}m ago"


def _entry_type_icon(entry_type: str) -> str:
    icons = {
        "commit": "[commit]",
        "annotation": "[note]",
        "session": "[session]",
        "tag": "[tag]",
        "idea": "[idea]",
    }
    return icons.get(entry_type, "[?]")
