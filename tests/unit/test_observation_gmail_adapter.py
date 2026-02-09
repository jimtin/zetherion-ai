"""Unit tests for the Gmail observation adapter."""

from datetime import datetime
from unittest.mock import patch

from zetherion_ai.observation.adapters.gmail import (
    EMAIL_PATTERN,
    MAX_BODY_LENGTH,
    GmailObservationAdapter,
)
from zetherion_ai.observation.models import ObservationEvent
from zetherion_ai.skills.gmail.client import EmailMessage

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

OWNER_ID = 12345
ACCOUNT_EMAIL = "user@example.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_email(**kwargs) -> EmailMessage:
    """Build an EmailMessage with sensible defaults."""
    defaults = {
        "gmail_id": "msg123",
        "thread_id": "thread456",
        "subject": "Test Subject",
        "from_email": "sender@example.com",
        "to_emails": ["user@example.com"],
        "body_text": "Hello, this is a test.",
        "is_read": False,
        "snippet": "Hello, this is...",
        "received_at": datetime(2025, 1, 15, 10, 30),
    }
    defaults.update(kwargs)
    return EmailMessage(**defaults)


def _make_adapter(**kwargs) -> GmailObservationAdapter:
    """Create a GmailObservationAdapter with sensible defaults."""
    defaults = {"owner_user_id": OWNER_ID}
    defaults.update(kwargs)
    return GmailObservationAdapter(**defaults)


# ===========================================================================
# TestConstructor
# ===========================================================================


class TestConstructor:
    """Tests for GmailObservationAdapter.__init__."""

    def test_stores_owner_user_id(self):
        """Constructor stores the owner_user_id."""
        adapter = _make_adapter()
        assert adapter._owner_user_id == OWNER_ID


# ===========================================================================
# TestAdaptBasic
# ===========================================================================


class TestAdaptBasic:
    """Basic tests for the adapt() method."""

    def test_returns_observation_event(self):
        """adapt() returns an ObservationEvent instance."""
        adapter = _make_adapter()
        email = _make_email()
        result = adapter.adapt(email, ACCOUNT_EMAIL)
        assert isinstance(result, ObservationEvent)

    def test_source_is_gmail(self):
        """Event source is always 'gmail'."""
        adapter = _make_adapter()
        email = _make_email()
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.source == "gmail"

    def test_source_id_is_gmail_id(self):
        """Event source_id matches the email's gmail_id."""
        adapter = _make_adapter()
        email = _make_email(gmail_id="abc789")
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.source_id == "abc789"

    def test_content_contains_subject_and_body(self):
        """Event content includes both subject and body text."""
        adapter = _make_adapter()
        email = _make_email(subject="Important", body_text="Details here")
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert "Subject: Important" in event.content
        assert "Details here" in event.content

    def test_context_includes_expected_keys(self):
        """Event context contains account_email, thread_id, and others."""
        adapter = _make_adapter()
        email = _make_email(
            thread_id="t999",
            from_email="alice@example.com",
            to_emails=["bob@example.com"],
            cc_emails=["carol@example.com"],
            labels=["INBOX", "UNREAD"],
            is_read=False,
        )
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        ctx = event.context
        assert ctx["account_email"] == ACCOUNT_EMAIL
        assert ctx["thread_id"] == "t999"
        assert ctx["subject"] == "Test Subject"
        assert ctx["from_email"] == "alice@example.com"
        assert ctx["to_emails"] == ["bob@example.com"]
        assert ctx["cc_emails"] == ["carol@example.com"]
        assert ctx["labels"] == ["INBOX", "UNREAD"]
        assert ctx["is_read"] is False

    def test_user_id_set_to_owner(self):
        """Event user_id is set to the adapter's owner_user_id."""
        adapter = _make_adapter(owner_user_id=42)
        email = _make_email()
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.user_id == 42


# ===========================================================================
# TestAdaptContentHandling
# ===========================================================================


class TestAdaptContentHandling:
    """Tests for content construction in adapt()."""

    def test_subject_and_body_combined(self):
        """Content is 'Subject: ...\nbody' when both present."""
        adapter = _make_adapter()
        email = _make_email(subject="Re: Meeting", body_text="Sounds good.")
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.content == "Subject: Re: Meeting\nSounds good."

    def test_subject_only_no_body(self):
        """Content is just the subject line when body is empty."""
        adapter = _make_adapter()
        email = _make_email(subject="Heads up", body_text="", snippet="")
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.content == "Subject: Heads up"

    def test_body_only_no_subject(self):
        """Content is just the body when subject is empty."""
        adapter = _make_adapter()
        email = _make_email(subject="", body_text="Just the body text.")
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.content == "Just the body text."

    def test_long_body_truncated_at_max_body_length(self):
        """Body text exceeding MAX_BODY_LENGTH is truncated with '...'."""
        adapter = _make_adapter()
        long_body = "x" * (MAX_BODY_LENGTH + 500)
        email = _make_email(body_text=long_body)
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        # Content has subject + newline + truncated body
        lines = event.content.split("\n", 1)
        body_part = lines[1]
        assert len(body_part) == MAX_BODY_LENGTH + 3  # +3 for "..."
        assert body_part.endswith("...")

    def test_body_exactly_at_max_length_not_truncated(self):
        """Body text exactly at MAX_BODY_LENGTH is NOT truncated."""
        adapter = _make_adapter()
        exact_body = "y" * MAX_BODY_LENGTH
        email = _make_email(subject="", body_text=exact_body)
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.content == exact_body
        assert not event.content.endswith("...")

    def test_empty_email_content(self):
        """Content is '(empty email)' when subject, body, and snippet are all empty."""
        adapter = _make_adapter()
        email = _make_email(subject="", body_text="", snippet="")
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.content == "(empty email)"

    def test_snippet_used_when_body_text_empty(self):
        """Snippet is used as body when body_text is empty."""
        adapter = _make_adapter()
        email = _make_email(body_text="", snippet="A preview snippet")
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert "A preview snippet" in event.content


# ===========================================================================
# TestAdaptAuthorDetection
# ===========================================================================


class TestAdaptAuthorDetection:
    """Tests for author_is_owner logic in adapt()."""

    def test_owner_sends_email_author_is_owner_true(self):
        """When account email matches sender, author_is_owner is True."""
        adapter = _make_adapter()
        email = _make_email(from_email="user@example.com")
        event = adapter.adapt(email, "user@example.com")
        assert event.author_is_owner is True

    def test_someone_else_sends_author_is_owner_false(self):
        """When sender differs from account email, author_is_owner is False."""
        adapter = _make_adapter()
        email = _make_email(from_email="stranger@other.com")
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.author_is_owner is False

    def test_case_insensitive_email_matching(self):
        """Email comparison for author_is_owner is case-insensitive."""
        adapter = _make_adapter()
        email = _make_email(from_email="User@EXAMPLE.com")
        event = adapter.adapt(email, "user@example.com")
        assert event.author_is_owner is True

    def test_name_angle_bracket_format_extracts_email(self):
        """'Name <email>' format extracts email for ownership check."""
        adapter = _make_adapter()
        email = _make_email(from_email="John Doe <user@example.com>")
        event = adapter.adapt(email, "user@example.com")
        assert event.author_is_owner is True

    def test_author_field_uses_from_email(self):
        """Event author is set to the email's from_email field."""
        adapter = _make_adapter()
        email = _make_email(from_email="alice@test.com")
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.author == "alice@test.com"

    def test_empty_from_email_author_defaults_to_unknown(self):
        """When from_email is empty, author falls back to 'unknown'."""
        adapter = _make_adapter()
        email = _make_email(from_email="")
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.author == "unknown"


# ===========================================================================
# TestAdaptThreadHistory
# ===========================================================================


class TestAdaptThreadHistory:
    """Tests for thread_messages → conversation_history in adapt()."""

    def test_thread_messages_passed_through(self):
        """thread_messages become conversation_history on the event."""
        adapter = _make_adapter()
        email = _make_email()
        history = ["msg1", "msg2", "msg3"]
        event = adapter.adapt(email, ACCOUNT_EMAIL, thread_messages=history)
        assert event.conversation_history == ["msg1", "msg2", "msg3"]

    def test_no_thread_messages_empty_list(self):
        """Without thread_messages, conversation_history is an empty list."""
        adapter = _make_adapter()
        email = _make_email()
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.conversation_history == []

    def test_thread_messages_none_gives_empty_list(self):
        """Explicit None for thread_messages yields empty history."""
        adapter = _make_adapter()
        email = _make_email()
        event = adapter.adapt(email, ACCOUNT_EMAIL, thread_messages=None)
        assert event.conversation_history == []


# ===========================================================================
# TestAdaptTimestamp
# ===========================================================================


class TestAdaptTimestamp:
    """Tests for timestamp handling in adapt()."""

    def test_uses_email_received_at(self):
        """Event timestamp uses the email's received_at datetime."""
        adapter = _make_adapter()
        dt = datetime(2025, 6, 1, 12, 0, 0)
        email = _make_email(received_at=dt)
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.timestamp == dt

    @patch("zetherion_ai.observation.adapters.gmail.datetime")
    def test_falls_back_to_now_when_received_at_none(self, mock_datetime):
        """When received_at is None, timestamp falls back to datetime.now()."""
        fake_now = datetime(2025, 7, 4, 8, 0, 0)
        mock_datetime.now.return_value = fake_now
        # Ensure the mock's class check still works for dataclass
        mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)

        adapter = _make_adapter()
        email = _make_email(received_at=None)
        event = adapter.adapt(email, ACCOUNT_EMAIL)
        assert event.timestamp == fake_now
        mock_datetime.now.assert_called_once()


# ===========================================================================
# TestExtractEmail
# ===========================================================================


class TestExtractEmail:
    """Tests for the _extract_email private method."""

    def test_plain_email_returned_as_is(self):
        """A plain email address is returned unchanged."""
        adapter = _make_adapter()
        assert adapter._extract_email("alice@example.com") == "alice@example.com"

    def test_name_angle_bracket_format(self):
        """'Name <email@test.com>' extracts just the email."""
        adapter = _make_adapter()
        result = adapter._extract_email("Alice Smith <alice@test.com>")
        assert result == "alice@test.com"

    def test_empty_string_returns_empty(self):
        """Empty string input returns empty string."""
        adapter = _make_adapter()
        assert adapter._extract_email("") == ""

    def test_no_match_returns_stripped_input(self):
        """When no email pattern matches, return stripped input."""
        adapter = _make_adapter()
        result = adapter._extract_email("  not an email  ")
        assert result == "not an email"

    def test_email_with_plus_and_dots(self):
        """Email with plus addressing and dots is extracted correctly."""
        adapter = _make_adapter()
        result = adapter._extract_email("User <user.name+tag@sub.example.com>")
        assert result == "user.name+tag@sub.example.com"


# ===========================================================================
# TestEmailPattern
# ===========================================================================


class TestEmailPattern:
    """Tests for the EMAIL_PATTERN regex constant."""

    def test_matches_standard_email(self):
        """Regex matches a standard email in text."""
        match = EMAIL_PATTERN.search("contact me at hello@world.com please")
        assert match is not None
        assert match.group(0) == "hello@world.com"

    def test_matches_email_in_angle_brackets(self):
        """Regex matches email inside angle brackets."""
        match = EMAIL_PATTERN.search("John <john@example.org>")
        assert match is not None
        assert match.group(0) == "john@example.org"
