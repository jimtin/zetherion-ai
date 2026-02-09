"""Gmail source adapter for the observation pipeline.

Converts Gmail emails into ObservationEvent format for the
channel-agnostic observation pipeline.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.observation.models import ObservationEvent
from zetherion_ai.skills.gmail.client import EmailMessage

log = get_logger("zetherion_ai.observation.adapters.gmail")

# Regex to extract plain email from "Name <email>" format
EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+\.[\w.]+")

# Maximum body length to send to the pipeline
MAX_BODY_LENGTH = 4000


class GmailObservationAdapter:
    """Converts Gmail emails into ObservationEvents.

    Each email is converted into a single ObservationEvent with:
    - source="gmail"
    - source_id=gmail message ID
    - content=subject + body text (truncated)
    - context includes account email, thread info, recipients
    """

    def __init__(self, owner_user_id: int) -> None:
        """Initialize the adapter.

        Args:
            owner_user_id: Discord user ID of the bot owner.
        """
        self._owner_user_id = owner_user_id

    def adapt(
        self,
        email_msg: EmailMessage,
        account_email: str,
        *,
        thread_messages: list[str] | None = None,
    ) -> ObservationEvent:
        """Convert a Gmail email to an ObservationEvent.

        Args:
            email_msg: The email message to convert.
            account_email: The Gmail account this email belongs to.
            thread_messages: Optional list of prior messages in the thread.

        Returns:
            An ObservationEvent ready for the pipeline.
        """
        # Build content: subject + body
        content_parts: list[str] = []
        if email_msg.subject:
            content_parts.append(f"Subject: {email_msg.subject}")

        body = email_msg.body_text or email_msg.snippet or ""
        if body:
            if len(body) > MAX_BODY_LENGTH:
                body = body[:MAX_BODY_LENGTH] + "..."
            content_parts.append(body)

        content = "\n".join(content_parts) if content_parts else "(empty email)"

        # Determine if the owner sent this email
        sender_email = self._extract_email(email_msg.from_email)
        author_is_owner = sender_email.lower() == account_email.lower()

        # Build context
        context: dict[str, Any] = {
            "account_email": account_email,
            "thread_id": email_msg.thread_id,
            "subject": email_msg.subject,
            "from_email": email_msg.from_email,
            "to_emails": email_msg.to_emails,
            "cc_emails": email_msg.cc_emails,
            "labels": email_msg.labels,
            "is_read": email_msg.is_read,
        }

        # Build conversation history from thread
        history = thread_messages or []

        event = ObservationEvent(
            source="gmail",
            source_id=email_msg.gmail_id,
            user_id=self._owner_user_id,
            author=email_msg.from_email or "unknown",
            author_is_owner=author_is_owner,
            content=content,
            timestamp=email_msg.received_at or datetime.now(),
            context=context,
            conversation_history=history,
        )

        log.debug(
            "gmail_email_adapted",
            source_id=event.source_id,
            subject=email_msg.subject[:50] if email_msg.subject else "",
            from_email=email_msg.from_email,
        )

        return event

    def _extract_email(self, raw: str) -> str:
        """Extract plain email address from a 'Name <email>' string."""
        if not raw:
            return ""
        match = EMAIL_PATTERN.search(raw)
        if match:
            return match.group(0)
        return raw.strip()
