"""Tests for Gmail Skill entry point."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from zetherion_ai.skills.base import HeartbeatAction, SkillRequest, SkillStatus
from zetherion_ai.skills.gmail.skill import (
    ALL_INTENTS,
    INTENT_CALENDAR,
    INTENT_CHECK,
    INTENT_DIGEST,
    INTENT_DRAFTS,
    INTENT_SEARCH,
    INTENT_STATUS,
    INTENT_UNREAD,
    GmailSkill,
)
from zetherion_ai.skills.permissions import PermissionSet

# ---------------------------------------------------------------------------
# Mock helpers
# ---------------------------------------------------------------------------


def make_account(
    email: str = "test@gmail.com",
    account_id: int = 1,
    is_primary: bool = True,
    last_sync: datetime | None = None,
) -> MagicMock:
    """Build a mock GmailAccount."""
    account = MagicMock()
    account.email = email
    account.id = account_id
    account.is_primary = is_primary
    account.last_sync = last_sync
    return account


def make_inbox_email(
    subject: str = "Test Subject",
    from_email: str = "sender@example.com",
) -> MagicMock:
    """Build a mock InboxEmail."""
    email = MagicMock()
    email.message.subject = subject
    email.message.from_email = from_email
    return email


def make_request(
    intent: str = "",
    user_id: str = "42",
    message: str = "",
    context: dict | None = None,
) -> SkillRequest:
    """Build a SkillRequest with sensible defaults."""
    return SkillRequest(
        id=uuid4(),
        user_id=user_id,
        intent=intent,
        message=message,
        context=context or {},
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def account_manager() -> AsyncMock:
    mgr = AsyncMock()
    mgr.list_accounts = AsyncMock(return_value=[])
    return mgr


@pytest.fixture
def inbox() -> MagicMock:
    return MagicMock()


@pytest.fixture
def draft_store() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def digest_generator() -> AsyncMock:
    return AsyncMock()


@pytest.fixture
def analytics() -> MagicMock:
    return MagicMock()


@pytest.fixture
def skill(account_manager, inbox, draft_store, digest_generator, analytics) -> GmailSkill:
    """Fully-wired GmailSkill instance."""
    return GmailSkill(
        memory=None,
        account_manager=account_manager,
        inbox=inbox,
        draft_store=draft_store,
        analytics=analytics,
        digest_generator=digest_generator,
    )


@pytest.fixture
def bare_skill() -> GmailSkill:
    """GmailSkill with no dependencies injected."""
    return GmailSkill()


# ===========================================================================
# 1. Metadata
# ===========================================================================


class TestGmailSkillMetadata:
    """Verify the skill exposes correct metadata."""

    def test_name(self, skill: GmailSkill) -> None:
        assert skill.metadata.name == "gmail"

    def test_version(self, skill: GmailSkill) -> None:
        assert skill.metadata.version == "1.0.0"

    def test_intents_match_module_level(self, skill: GmailSkill) -> None:
        assert skill.metadata.intents == ALL_INTENTS

    def test_intents_contains_all_expected(self, skill: GmailSkill) -> None:
        expected = {
            INTENT_CHECK,
            INTENT_UNREAD,
            INTENT_DRAFTS,
            INTENT_DIGEST,
            INTENT_STATUS,
            INTENT_SEARCH,
            INTENT_CALENDAR,
        }
        assert set(skill.metadata.intents) == expected

    def test_permissions_set_is_present(self, skill: GmailSkill) -> None:
        meta = skill.metadata
        assert isinstance(meta.permissions, PermissionSet)

    def test_description(self, skill: GmailSkill) -> None:
        assert "Gmail" in skill.metadata.description

    def test_class_intents_attribute(self) -> None:
        assert GmailSkill.INTENTS is ALL_INTENTS


# ===========================================================================
# 2. Initialization
# ===========================================================================


class TestGmailSkillInitialization:
    """Verify initialize() behaviour."""

    async def test_initialize_returns_true(self, skill: GmailSkill) -> None:
        result = await skill.initialize()
        assert result is True

    async def test_initialize_sets_ready_status(self, skill: GmailSkill) -> None:
        await skill.initialize()
        assert skill.status == SkillStatus.READY

    async def test_initialize_exception_path(self, skill: GmailSkill) -> None:
        """When an exception occurs during initialize, status becomes ERROR."""
        # Make log.info raise to trigger the except branch.
        with patch("zetherion_ai.skills.gmail.skill.log") as mock_log:
            mock_log.info.side_effect = RuntimeError("log failed")
            result = await skill.initialize()

        assert result is False
        assert skill.status == SkillStatus.ERROR
        assert "log failed" in (skill._error or "")


# ===========================================================================
# 3. handle() routing
# ===========================================================================


class TestHandleRouting:
    """Verify that handle() dispatches to the correct handler."""

    async def test_unknown_intent_returns_error(self, skill: GmailSkill) -> None:
        req = make_request(intent="totally_unknown")
        resp = await skill.handle(req)
        assert resp.success is False
        assert "Unknown intent" in resp.error

    async def test_empty_user_id_defaults_to_zero(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        """When user_id is empty string, it should default to 0."""
        account_manager.list_accounts.return_value = []
        req = make_request(intent="email_status", user_id="")
        await skill.handle(req)
        # Should have called list_accounts with 0
        account_manager.list_accounts.assert_awaited_with(0)

    async def test_exception_in_handler_returns_error_response(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        account_manager.list_accounts.side_effect = RuntimeError("boom")
        req = make_request(intent="email_check", user_id="1")
        resp = await skill.handle(req)
        assert resp.success is False
        assert "boom" in resp.error

    async def test_all_intents_are_routed(self, skill: GmailSkill) -> None:
        """Ensure every declared intent routes to a handler, not the unknown branch."""
        for intent in ALL_INTENTS:
            req = make_request(intent=intent, user_id="1", message="hello")
            resp = await skill.handle(req)
            # None of the known intents should yield "Unknown intent"
            if resp.error:
                assert "Unknown intent" not in resp.error


# ===========================================================================
# 4. _handle_check
# ===========================================================================


class TestHandleCheck:
    """Tests for the email_check intent handler."""

    async def test_no_account_manager_returns_error(self, bare_skill: GmailSkill) -> None:
        req = make_request(intent="email_check")
        resp = await bare_skill.handle(req)
        assert resp.success is False
        assert "not configured" in resp.error.lower()

    async def test_no_inbox_returns_error(self, account_manager: AsyncMock) -> None:
        s = GmailSkill(account_manager=account_manager, inbox=None)
        req = make_request(intent="email_check")
        resp = await s.handle(req)
        assert resp.success is False
        assert "not configured" in resp.error.lower()

    async def test_no_accounts_returns_connect_message(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        account_manager.list_accounts.return_value = []
        req = make_request(intent="email_check")
        resp = await skill.handle(req)
        assert "No Gmail accounts connected" in resp.message

    async def test_with_accounts_returns_summary(
        self, skill: GmailSkill, account_manager: AsyncMock, inbox: MagicMock
    ) -> None:
        account_manager.list_accounts.return_value = [
            make_account("a@g.com", 1),
            make_account("b@g.com", 2),
        ]
        summary = MagicMock()
        summary.total_emails = 15
        summary.unread_count = 3
        summary.high_priority = 1
        summary.to_dict.return_value = {"total": 15}
        inbox.get_summary.return_value = summary

        req = make_request(intent="email_check")
        resp = await skill.handle(req)

        assert resp.success is True
        assert "15 emails" in resp.message
        assert "2 account(s)" in resp.message
        assert "Unread: 3" in resp.message
        assert "High priority: 1" in resp.message
        assert resp.data == {"total": 15}


# ===========================================================================
# 5. _handle_unread
# ===========================================================================


class TestHandleUnread:
    """Tests for the email_unread intent handler."""

    async def test_no_inbox_returns_error(self, bare_skill: GmailSkill) -> None:
        req = make_request(intent="email_unread")
        resp = await bare_skill.handle(req)
        assert resp.success is False
        assert "not configured" in resp.error.lower()

    async def test_no_unread_emails(self, skill: GmailSkill, inbox: MagicMock) -> None:
        inbox.get_emails.return_value = []
        req = make_request(intent="email_unread")
        resp = await skill.handle(req)
        assert "No unread emails!" in resp.message

    async def test_unread_emails_under_five(self, skill: GmailSkill, inbox: MagicMock) -> None:
        emails = [make_inbox_email(f"Subject {i}", f"s{i}@x.com") for i in range(3)]
        inbox.get_emails.return_value = emails

        req = make_request(intent="email_unread")
        resp = await skill.handle(req)

        assert "3 unread email(s)" in resp.message
        assert "Subject 0" in resp.message
        assert "s0@x.com" in resp.message
        assert resp.data == {"count": 3}
        # Should NOT have the "... and N more" line
        assert "more" not in resp.message

    async def test_unread_emails_more_than_five(self, skill: GmailSkill, inbox: MagicMock) -> None:
        emails = [make_inbox_email(f"Subject {i}", f"s{i}@x.com") for i in range(8)]
        inbox.get_emails.return_value = emails

        req = make_request(intent="email_unread")
        resp = await skill.handle(req)

        assert "8 unread email(s)" in resp.message
        # First 5 listed
        assert "Subject 4" in resp.message
        # 6th+ not listed individually
        assert "Subject 5" not in resp.message
        assert "... and 3 more" in resp.message
        assert resp.data == {"count": 8}

    async def test_unread_email_no_subject(self, skill: GmailSkill, inbox: MagicMock) -> None:
        email = MagicMock()
        email.message.subject = None
        email.message.from_email = "nobody@example.com"
        inbox.get_emails.return_value = [email]

        req = make_request(intent="email_unread")
        resp = await skill.handle(req)
        assert "(no subject)" in resp.message


# ===========================================================================
# 6. _handle_drafts
# ===========================================================================


class TestHandleDrafts:
    """Tests for the email_drafts intent handler."""

    async def test_no_draft_store_returns_error(self) -> None:
        s = GmailSkill(account_manager=AsyncMock(), draft_store=None)
        req = make_request(intent="email_drafts")
        resp = await s.handle(req)
        assert resp.success is False
        assert "not configured" in resp.error.lower()

    async def test_no_account_manager_returns_error(self) -> None:
        s = GmailSkill(account_manager=None, draft_store=AsyncMock())
        req = make_request(intent="email_drafts")
        resp = await s.handle(req)
        assert resp.success is False

    async def test_no_drafts_returns_clean_message(
        self, skill: GmailSkill, account_manager: AsyncMock, draft_store: AsyncMock
    ) -> None:
        account_manager.list_accounts.return_value = [make_account()]
        draft_store.list_pending.return_value = []

        req = make_request(intent="email_drafts")
        resp = await skill.handle(req)
        assert "No pending drafts" in resp.message

    async def test_with_drafts_returns_count(
        self, skill: GmailSkill, account_manager: AsyncMock, draft_store: AsyncMock
    ) -> None:
        account_manager.list_accounts.return_value = [make_account()]
        d1, d2 = MagicMock(), MagicMock()
        d1.to_dict.return_value = {"id": 1}
        d2.to_dict.return_value = {"id": 2}
        draft_store.list_pending.return_value = [d1, d2]

        req = make_request(intent="email_drafts")
        resp = await skill.handle(req)

        assert "2 pending draft(s)" in resp.message
        assert resp.data["count"] == 2
        assert len(resp.data["drafts"]) == 2

    async def test_account_with_none_id_is_skipped(
        self, skill: GmailSkill, account_manager: AsyncMock, draft_store: AsyncMock
    ) -> None:
        acct_no_id = make_account(account_id=None)  # type: ignore[arg-type]
        acct_no_id.id = None
        acct_with_id = make_account(account_id=5)
        account_manager.list_accounts.return_value = [acct_no_id, acct_with_id]
        draft_store.list_pending.return_value = []

        req = make_request(intent="email_drafts")
        await skill.handle(req)

        # list_pending should only be called for the account with an id
        draft_store.list_pending.assert_awaited_once_with(5)


# ===========================================================================
# 7. _handle_digest
# ===========================================================================


class TestHandleDigest:
    """Tests for the email_digest intent handler."""

    async def test_no_digest_generator_returns_error(self) -> None:
        s = GmailSkill(account_manager=AsyncMock(), digest_generator=None)
        req = make_request(intent="email_digest", message="morning")
        resp = await s.handle(req)
        assert resp.success is False
        assert "not configured" in resp.error.lower()

    async def test_no_account_manager_returns_error(self) -> None:
        s = GmailSkill(account_manager=None, digest_generator=AsyncMock())
        req = make_request(intent="email_digest", message="morning")
        resp = await s.handle(req)
        assert resp.success is False

    async def test_no_accounts_returns_message(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        account_manager.list_accounts.return_value = []
        req = make_request(intent="email_digest", message="morning digest")
        resp = await skill.handle(req)
        assert "No Gmail accounts connected" in resp.message

    async def test_weekly_digest(
        self, skill: GmailSkill, account_manager: AsyncMock, digest_generator: AsyncMock
    ) -> None:
        acct = make_account()
        account_manager.list_accounts.return_value = [acct]
        digest = MagicMock()
        digest.to_text.return_value = "Weekly summary"
        digest.to_dict.return_value = {"type": "weekly"}
        digest_generator.generate_weekly.return_value = digest

        req = make_request(intent="email_digest", message="Give me a weekly digest")
        resp = await skill.handle(req)

        digest_generator.generate_weekly.assert_awaited_once_with(acct.id, acct.email)
        assert resp.message == "Weekly summary"
        assert resp.data == {"type": "weekly"}

    async def test_evening_digest(
        self, skill: GmailSkill, account_manager: AsyncMock, digest_generator: AsyncMock
    ) -> None:
        acct = make_account()
        account_manager.list_accounts.return_value = [acct]
        digest = MagicMock()
        digest.to_text.return_value = "Evening summary"
        digest.to_dict.return_value = {"type": "evening"}
        digest_generator.generate_evening.return_value = digest

        req = make_request(intent="email_digest", message="evening recap please")
        resp = await skill.handle(req)

        digest_generator.generate_evening.assert_awaited_once()
        assert resp.message == "Evening summary"

    async def test_end_of_day_triggers_evening(
        self, skill: GmailSkill, account_manager: AsyncMock, digest_generator: AsyncMock
    ) -> None:
        acct = make_account()
        account_manager.list_accounts.return_value = [acct]
        digest = MagicMock()
        digest.to_text.return_value = "EOD"
        digest.to_dict.return_value = {}
        digest_generator.generate_evening.return_value = digest

        req = make_request(intent="email_digest", message="end of day wrap-up")
        await skill.handle(req)
        digest_generator.generate_evening.assert_awaited_once()

    async def test_morning_digest_is_default(
        self, skill: GmailSkill, account_manager: AsyncMock, digest_generator: AsyncMock
    ) -> None:
        acct = make_account()
        account_manager.list_accounts.return_value = [acct]
        digest = MagicMock()
        digest.to_text.return_value = "Morning summary"
        digest.to_dict.return_value = {"type": "morning"}
        digest_generator.generate_morning.return_value = digest

        req = make_request(intent="email_digest", message="give me a digest")
        resp = await skill.handle(req)

        digest_generator.generate_morning.assert_awaited_once()
        assert resp.message == "Morning summary"

    async def test_primary_account_with_none_id_returns_error(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        acct = make_account()
        acct.id = None
        account_manager.list_accounts.return_value = [acct]

        req = make_request(intent="email_digest", message="morning digest")
        resp = await skill.handle(req)

        assert resp.success is False
        assert "not properly configured" in resp.error.lower()


# ===========================================================================
# 8. _handle_status
# ===========================================================================


class TestHandleStatus:
    """Tests for the email_status intent handler."""

    async def test_no_account_manager_returns_error(self, bare_skill: GmailSkill) -> None:
        req = make_request(intent="email_status")
        resp = await bare_skill.handle(req)
        assert resp.success is False
        assert "not configured" in resp.error.lower()

    async def test_no_accounts(self, skill: GmailSkill, account_manager: AsyncMock) -> None:
        account_manager.list_accounts.return_value = []
        req = make_request(intent="email_status")
        resp = await skill.handle(req)
        assert "No Gmail accounts connected" in resp.message

    async def test_with_accounts_primary_and_last_sync(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        sync_dt = datetime(2025, 6, 15, 9, 30)
        acct1 = make_account("primary@g.com", 1, is_primary=True, last_sync=sync_dt)
        acct2 = make_account("secondary@g.com", 2, is_primary=False, last_sync=None)
        account_manager.list_accounts.return_value = [acct1, acct2]

        req = make_request(intent="email_status")
        resp = await skill.handle(req)

        assert "Connected accounts (2)" in resp.message
        assert "primary@g.com (primary)" in resp.message
        assert "2025-06-15 09:30" in resp.message
        assert "secondary@g.com" in resp.message
        assert "never" in resp.message
        assert resp.data["accounts"][0]["email"] == "primary@g.com"
        assert resp.data["accounts"][0]["is_primary"] is True
        assert resp.data["accounts"][1]["is_primary"] is False


# ===========================================================================
# 9. _handle_search
# ===========================================================================


class TestHandleSearch:
    """Tests for the email_search intent handler."""

    async def test_no_inbox_returns_error(self, bare_skill: GmailSkill) -> None:
        req = make_request(intent="email_search", message="search for hello")
        resp = await bare_skill.handle(req)
        assert resp.success is False
        assert "not configured" in resp.error.lower()

    async def test_no_matches(self, skill: GmailSkill, inbox: MagicMock) -> None:
        inbox.get_emails.return_value = [
            make_inbox_email("Unrelated", "other@example.com"),
        ]
        req = make_request(intent="email_search", message="search for xyz_missing")
        resp = await skill.handle(req)
        assert "No emails found matching" in resp.message

    async def test_matches_by_subject(self, skill: GmailSkill, inbox: MagicMock) -> None:
        inbox.get_emails.return_value = [
            make_inbox_email("Invoice for June", "billing@corp.com"),
            make_inbox_email("Meeting notes", "boss@corp.com"),
        ]
        req = make_request(intent="email_search", message="search for invoice")
        resp = await skill.handle(req)
        assert "Found 1 email(s)" in resp.message
        assert "Invoice for June" in resp.message

    async def test_matches_by_sender(self, skill: GmailSkill, inbox: MagicMock) -> None:
        inbox.get_emails.return_value = [
            make_inbox_email("Hello", "alice@example.com"),
        ]
        req = make_request(intent="email_search", message="find alice")
        resp = await skill.handle(req)
        assert "Found 1 email(s)" in resp.message

    async def test_prefix_stripping_search_for(self, skill: GmailSkill, inbox: MagicMock) -> None:
        inbox.get_emails.return_value = [make_inbox_email("Report", "mgr@c.com")]
        req = make_request(intent="email_search", message="search for report")
        resp = await skill.handle(req)
        assert "report" in resp.message.lower()

    async def test_prefix_find_email_matches_before_find_emails(
        self, skill: GmailSkill, inbox: MagicMock
    ) -> None:
        """'find email' is checked before 'find emails' in the prefix list,
        so 'find emails budget' strips to 's budget', not 'budget'.
        This verifies the actual prefix-order behaviour of the code."""
        inbox.get_emails.return_value = [
            make_inbox_email("Budget Plan", "cfo@c.com"),
        ]
        req = make_request(intent="email_search", message="find emails budget")
        resp = await skill.handle(req)
        # Because "find email" matches first, query becomes "s budget"
        assert "No emails found matching 's budget'" in resp.message

    async def test_prefix_stripping_find_email(self, skill: GmailSkill, inbox: MagicMock) -> None:
        inbox.get_emails.return_value = [make_inbox_email("Budget Plan", "cfo@c.com")]
        req = make_request(intent="email_search", message="find email budget")
        resp = await skill.handle(req)
        assert "Found 1 email(s)" in resp.message

    async def test_prefix_stripping_search(self, skill: GmailSkill, inbox: MagicMock) -> None:
        inbox.get_emails.return_value = [make_inbox_email("Budget Plan", "cfo@c.com")]
        req = make_request(intent="email_search", message="search budget")
        resp = await skill.handle(req)
        assert "Found 1 email(s)" in resp.message

    async def test_prefix_stripping_find(self, skill: GmailSkill, inbox: MagicMock) -> None:
        inbox.get_emails.return_value = [make_inbox_email("Budget Plan", "cfo@c.com")]
        req = make_request(intent="email_search", message="find budget")
        resp = await skill.handle(req)
        assert "Found 1 email(s)" in resp.message

    async def test_search_with_no_subject(self, skill: GmailSkill, inbox: MagicMock) -> None:
        """Email with None subject should still match by sender."""
        email = MagicMock()
        email.message.subject = None
        email.message.from_email = "test@match.com"
        inbox.get_emails.return_value = [email]

        req = make_request(intent="email_search", message="search for match")
        resp = await skill.handle(req)
        assert "Found 1 email(s)" in resp.message
        assert "(no subject)" in resp.message


# ===========================================================================
# 10. _handle_calendar
# ===========================================================================


class TestHandleCalendar:
    """Tests for the email_calendar intent handler."""

    async def test_returns_placeholder(self, skill: GmailSkill) -> None:
        req = make_request(intent="email_calendar")
        resp = await skill.handle(req)
        assert "Calendar integration requires" in resp.message
        assert "/gmail connect" in resp.message


# ===========================================================================
# 11. on_heartbeat
# ===========================================================================


class TestOnHeartbeat:
    """Tests for the on_heartbeat method."""

    async def test_no_account_manager_returns_empty(self, bare_skill: GmailSkill) -> None:
        actions = await bare_skill.on_heartbeat(["1", "2"])
        assert actions == []

    async def test_user_with_accounts_gets_action(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        account_manager.list_accounts.return_value = [make_account()]
        actions = await skill.on_heartbeat(["99"])

        assert len(actions) == 1
        assert isinstance(actions[0], HeartbeatAction)
        assert actions[0].skill_name == "gmail"
        assert actions[0].action_type == "send_message"
        assert actions[0].user_id == "99"
        assert actions[0].data == {"type": "email_digest"}
        assert actions[0].priority == 3

    async def test_user_without_accounts_no_action(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        account_manager.list_accounts.return_value = []
        actions = await skill.on_heartbeat(["42"])
        assert actions == []

    async def test_non_numeric_user_id_skipped(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        account_manager.list_accounts.return_value = [make_account()]
        actions = await skill.on_heartbeat(["not_a_number"])
        assert actions == []
        account_manager.list_accounts.assert_not_awaited()

    async def test_zero_string_user_id_skipped(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        """'0' is digit but converts to 0, which is falsy, so it is skipped."""
        account_manager.list_accounts.return_value = [make_account()]
        actions = await skill.on_heartbeat(["0"])
        assert actions == []
        account_manager.list_accounts.assert_not_awaited()

    async def test_mixed_user_ids(self, skill: GmailSkill, account_manager: AsyncMock) -> None:
        """Only valid numeric user_ids with accounts produce actions."""

        async def _list(uid: int):
            if uid == 10:
                return [make_account()]
            return []

        account_manager.list_accounts.side_effect = _list
        actions = await skill.on_heartbeat(["abc", "0", "10", "20"])
        assert len(actions) == 1
        assert actions[0].user_id == "10"


# ===========================================================================
# 12. Edge cases and additional coverage
# ===========================================================================


class TestEdgeCases:
    """Additional tests for edge/corner cases."""

    async def test_handle_check_routes_correctly(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        """Verify email_check goes through _handle_check path."""
        account_manager.list_accounts.return_value = []
        req = make_request(intent="email_check")
        resp = await skill.handle(req)
        # Not an "Unknown intent" error
        assert resp.error is None or "Unknown intent" not in resp.error

    async def test_handle_unread_calls_inbox_correctly(
        self, skill: GmailSkill, inbox: MagicMock
    ) -> None:
        inbox.get_emails.return_value = []
        req = make_request(intent="email_unread")
        await skill.handle(req)
        inbox.get_emails.assert_called_once_with(unread_only=True, limit=10)

    async def test_handle_search_calls_inbox_correctly(
        self, skill: GmailSkill, inbox: MagicMock
    ) -> None:
        inbox.get_emails.return_value = []
        req = make_request(intent="email_search", message="something")
        await skill.handle(req)
        inbox.get_emails.assert_called_once_with(limit=10)

    async def test_drafts_across_multiple_accounts(
        self, skill: GmailSkill, account_manager: AsyncMock, draft_store: AsyncMock
    ) -> None:
        """Drafts from multiple accounts are aggregated."""
        acct1 = make_account("a@g.com", 1)
        acct2 = make_account("b@g.com", 2)
        account_manager.list_accounts.return_value = [acct1, acct2]

        d1 = MagicMock()
        d1.to_dict.return_value = {"id": "d1"}
        d2 = MagicMock()
        d2.to_dict.return_value = {"id": "d2"}

        async def _list_pending(account_id: int):
            if account_id == 1:
                return [d1]
            return [d2]

        draft_store.list_pending.side_effect = _list_pending

        req = make_request(intent="email_drafts")
        resp = await skill.handle(req)

        assert resp.data["count"] == 2
        assert "2 pending draft(s)" in resp.message

    async def test_exactly_five_unread_no_more_line(
        self, skill: GmailSkill, inbox: MagicMock
    ) -> None:
        """Exactly 5 unread emails should NOT show '... and N more'."""
        emails = [make_inbox_email(f"S{i}", f"s{i}@x.com") for i in range(5)]
        inbox.get_emails.return_value = emails

        req = make_request(intent="email_unread")
        resp = await skill.handle(req)

        assert "5 unread email(s)" in resp.message
        assert "more" not in resp.message

    async def test_search_no_prefix_match_uses_full_message(
        self, skill: GmailSkill, inbox: MagicMock
    ) -> None:
        """When message doesn't start with any prefix, the full message is used as query."""
        inbox.get_emails.return_value = [
            make_inbox_email("some random thing", "me@x.com"),
        ]
        req = make_request(intent="email_search", message="random")
        resp = await skill.handle(req)
        assert "Found 1 email(s)" in resp.message

    async def test_search_email_with_none_from_email(
        self, skill: GmailSkill, inbox: MagicMock
    ) -> None:
        """Email with None from_email should not crash."""
        email = MagicMock()
        email.message.subject = "Important"
        email.message.from_email = None
        inbox.get_emails.return_value = [email]

        req = make_request(intent="email_search", message="search important")
        resp = await skill.handle(req)
        assert "Found 1 email(s)" in resp.message

    async def test_status_account_without_last_sync_shows_never(
        self, skill: GmailSkill, account_manager: AsyncMock
    ) -> None:
        acct = make_account("solo@g.com", 1, is_primary=False, last_sync=None)
        account_manager.list_accounts.return_value = [acct]

        req = make_request(intent="email_status")
        resp = await skill.handle(req)
        assert "last sync: never" in resp.message
