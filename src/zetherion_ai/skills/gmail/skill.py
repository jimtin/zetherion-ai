"""Gmail Skill entry point for Zetherion AI.

Exposes Gmail integration as a skill: email checking, draft management,
digest generation, calendar queries, and account management through
the skills framework.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

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
    from zetherion_ai.skills.gmail.accounts import GmailAccountManager
    from zetherion_ai.skills.gmail.analytics import EmailAnalytics
    from zetherion_ai.skills.gmail.digest import DigestGenerator
    from zetherion_ai.skills.gmail.inbox import UnifiedInbox
    from zetherion_ai.skills.gmail.replies import ReplyDraftStore

log = get_logger("zetherion_ai.skills.gmail.skill")

# Intent constants
INTENT_CHECK = "email_check"
INTENT_UNREAD = "email_unread"
INTENT_DRAFTS = "email_drafts"
INTENT_DIGEST = "email_digest"
INTENT_STATUS = "email_status"
INTENT_SEARCH = "email_search"
INTENT_CALENDAR = "email_calendar"

ALL_INTENTS = [
    INTENT_CHECK,
    INTENT_UNREAD,
    INTENT_DRAFTS,
    INTENT_DIGEST,
    INTENT_STATUS,
    INTENT_SEARCH,
    INTENT_CALENDAR,
]


class GmailSkill(Skill):
    """Skill for Gmail integration.

    Intents handled:
    - email_check: Check for new emails across all accounts
    - email_unread: Show unread email count and summary
    - email_drafts: List pending reply drafts
    - email_digest: Generate a morning/evening/weekly digest
    - email_status: Show connected account status
    - email_search: Search emails by query
    - email_calendar: Show today's calendar events
    """

    INTENTS = ALL_INTENTS

    def __init__(
        self,
        memory: QdrantMemory | None = None,
        account_manager: GmailAccountManager | None = None,
        inbox: UnifiedInbox | None = None,
        draft_store: ReplyDraftStore | None = None,
        analytics: EmailAnalytics | None = None,
        digest_generator: DigestGenerator | None = None,
    ) -> None:
        """Initialize the Gmail skill.

        Args:
            memory: Optional Qdrant memory.
            account_manager: Gmail account manager.
            inbox: Unified inbox aggregator.
            draft_store: Reply draft store.
            analytics: Email analytics.
            digest_generator: Digest generator.
        """
        super().__init__(memory=memory)
        self._account_manager = account_manager
        self._inbox = inbox
        self._draft_store = draft_store
        self._analytics = analytics
        self._digest_generator = digest_generator

    @property
    def metadata(self) -> SkillMetadata:
        """Return skill metadata."""
        return SkillMetadata(
            name="gmail",
            description="Gmail integration: email management, drafts, digests, and calendar",
            version="1.0.0",
            permissions=PermissionSet.from_list(
                [Permission.READ_MEMORIES.name, Permission.WRITE_MEMORIES.name]
            ),
            intents=self.INTENTS,
        )

    async def initialize(self) -> bool:
        """Initialize the Gmail skill."""
        from zetherion_ai.skills.base import SkillStatus

        try:
            self._status = SkillStatus.READY
            log.info("gmail_skill_initialized")
            return True
        except Exception as e:
            self._status = SkillStatus.ERROR
            self._error = str(e)
            log.error("gmail_skill_init_failed", error=str(e))
            return False

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle a Gmail skill request.

        Args:
            request: The incoming skill request.

        Returns:
            SkillResponse with results.
        """
        intent = request.intent
        user_id = int(request.user_id) if request.user_id else 0

        log.debug(
            "gmail_skill_handle",
            intent=intent,
            user_id=user_id,
        )

        try:
            match intent:
                case "email_check":
                    return await self._handle_check(request, user_id)
                case "email_unread":
                    return await self._handle_unread(request, user_id)
                case "email_drafts":
                    return await self._handle_drafts(request, user_id)
                case "email_digest":
                    return await self._handle_digest(request, user_id)
                case "email_status":
                    return await self._handle_status(request, user_id)
                case "email_search":
                    return await self._handle_search(request, user_id)
                case "email_calendar":
                    return await self._handle_calendar(request, user_id)
                case _:
                    return SkillResponse(
                        request_id=request.id,
                        success=False,
                        error=f"Unknown intent: {intent}",
                    )
        except Exception as e:
            log.error("gmail_skill_error", intent=intent, error=str(e))
            return SkillResponse.error_response(request.id, str(e))

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Provide heartbeat actions for digest generation."""
        actions: list[HeartbeatAction] = []

        if not self._account_manager:
            return actions

        for uid_str in user_ids:
            user_id = int(uid_str) if uid_str.isdigit() else 0
            if not user_id:
                continue

            accounts = await self._account_manager.list_accounts(user_id)
            if accounts:
                actions.append(
                    HeartbeatAction(
                        skill_name="gmail",
                        action_type="send_message",
                        user_id=uid_str,
                        data={"type": "email_digest"},
                        priority=3,
                    )
                )

        return actions

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    async def _handle_check(self, request: SkillRequest, user_id: int) -> SkillResponse:
        """Check for new emails across connected accounts."""
        if not self._account_manager or not self._inbox:
            return SkillResponse(
                request_id=request.id,
                success=False,
                error="Gmail is not configured. Use /gmail connect to set up.",
            )

        accounts = await self._account_manager.list_accounts(user_id)
        if not accounts:
            return SkillResponse(
                request_id=request.id,
                message="No Gmail accounts connected. Use /gmail connect to add one.",
            )

        summary = self._inbox.get_summary()
        msg = (
            f"You have {summary.total_emails} emails across"
            f" {len(accounts)} account(s).\n"
            f"Unread: {summary.unread_count}\n"
            f"High priority: {summary.high_priority}"
        )

        return SkillResponse(
            request_id=request.id,
            message=msg,
            data=summary.to_dict(),
        )

    async def _handle_unread(self, request: SkillRequest, user_id: int) -> SkillResponse:
        """Show unread email summary."""
        if not self._inbox:
            return SkillResponse(
                request_id=request.id,
                success=False,
                error="Gmail is not configured.",
            )

        unread = self._inbox.get_emails(unread_only=True, limit=10)

        if not unread:
            return SkillResponse(
                request_id=request.id,
                message="No unread emails!",
            )

        lines = [f"You have {len(unread)} unread email(s):"]
        for email_item in unread[:5]:
            lines.append(
                f"  - {email_item.message.subject or '(no subject)'}"
                f" from {email_item.message.from_email}"
            )

        if len(unread) > 5:
            lines.append(f"  ... and {len(unread) - 5} more")

        return SkillResponse(
            request_id=request.id,
            message="\n".join(lines),
            data={"count": len(unread)},
        )

    async def _handle_drafts(self, request: SkillRequest, user_id: int) -> SkillResponse:
        """List pending reply drafts."""
        if not self._draft_store or not self._account_manager:
            return SkillResponse(
                request_id=request.id,
                success=False,
                error="Gmail is not configured.",
            )

        accounts = await self._account_manager.list_accounts(user_id)
        all_drafts: list[dict[str, Any]] = []

        for account in accounts:
            if account.id is not None:
                drafts = await self._draft_store.list_pending(account.id)
                for d in drafts:
                    all_drafts.append(d.to_dict())

        if not all_drafts:
            return SkillResponse(
                request_id=request.id,
                message="No pending drafts.",
            )

        msg = f"You have {len(all_drafts)} pending draft(s) awaiting review."
        return SkillResponse(
            request_id=request.id,
            message=msg,
            data={"drafts": all_drafts, "count": len(all_drafts)},
        )

    async def _handle_digest(self, request: SkillRequest, user_id: int) -> SkillResponse:
        """Generate an email digest."""
        if not self._digest_generator or not self._account_manager:
            return SkillResponse(
                request_id=request.id,
                success=False,
                error="Gmail is not configured.",
            )

        accounts = await self._account_manager.list_accounts(user_id)
        if not accounts:
            return SkillResponse(
                request_id=request.id,
                message="No Gmail accounts connected.",
            )

        # Determine digest type from message
        msg_lower = request.message.lower()
        digest_type = "morning"
        if "weekly" in msg_lower:
            digest_type = "weekly"
        elif "evening" in msg_lower or "end of day" in msg_lower:
            digest_type = "evening"

        primary = accounts[0]
        if primary.id is None:
            return SkillResponse(
                request_id=request.id,
                success=False,
                error="Account not properly configured.",
            )

        if digest_type == "weekly":
            digest = await self._digest_generator.generate_weekly(primary.id, primary.email)
        elif digest_type == "evening":
            digest = await self._digest_generator.generate_evening(primary.id, primary.email)
        else:
            digest = await self._digest_generator.generate_morning(primary.id, primary.email)

        return SkillResponse(
            request_id=request.id,
            message=digest.to_text(),
            data=digest.to_dict(),
        )

    async def _handle_status(self, request: SkillRequest, user_id: int) -> SkillResponse:
        """Show connected account status."""
        if not self._account_manager:
            return SkillResponse(
                request_id=request.id,
                success=False,
                error="Gmail is not configured.",
            )

        accounts = await self._account_manager.list_accounts(user_id)
        if not accounts:
            return SkillResponse(
                request_id=request.id,
                message="No Gmail accounts connected. Use /gmail connect to add one.",
            )

        lines = [f"Connected accounts ({len(accounts)}):"]
        for acct in accounts:
            primary = " (primary)" if acct.is_primary else ""
            last = acct.last_sync.strftime("%Y-%m-%d %H:%M") if acct.last_sync else "never"
            lines.append(f"  - {acct.email}{primary} (last sync: {last})")

        return SkillResponse(
            request_id=request.id,
            message="\n".join(lines),
            data={"accounts": [{"email": a.email, "is_primary": a.is_primary} for a in accounts]},
        )

    async def _handle_search(self, request: SkillRequest, user_id: int) -> SkillResponse:
        """Search emails by query."""
        if not self._inbox:
            return SkillResponse(
                request_id=request.id,
                success=False,
                error="Gmail is not configured.",
            )

        # Extract search terms (simple: use the message after common prefixes)
        query = request.message.lower()
        for prefix in ["search for", "search", "find email", "find emails", "find"]:
            if query.startswith(prefix):
                query = query[len(prefix) :].strip()
                break

        emails = self._inbox.get_emails(limit=10)
        # Simple client-side filter by subject/sender
        matched = [
            e
            for e in emails
            if query in (e.message.subject or "").lower()
            or query in (e.message.from_email or "").lower()
        ]

        if not matched:
            return SkillResponse(
                request_id=request.id,
                message=f"No emails found matching '{query}'.",
            )

        lines = [f"Found {len(matched)} email(s) matching '{query}':"]
        for e in matched[:5]:
            lines.append(
                f"  - {e.message.subject or '(no subject)'}" f" from {e.message.from_email}"
            )

        return SkillResponse(
            request_id=request.id,
            message="\n".join(lines),
            data={"count": len(matched)},
        )

    async def _handle_calendar(self, request: SkillRequest, user_id: int) -> SkillResponse:
        """Show today's calendar events (placeholder — requires CalendarClient)."""
        return SkillResponse(
            request_id=request.id,
            message=(
                "Calendar integration requires an active Gmail account "
                "with Calendar API access. Use /gmail connect first."
            ),
        )
