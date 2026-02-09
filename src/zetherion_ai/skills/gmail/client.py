"""Gmail API client wrapper.

Provides async methods for interacting with the Gmail API:
listing messages, reading message details, sending emails,
and managing labels.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import email.mime.text
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import httpx

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.skills.gmail.client")

GMAIL_API_BASE = "https://gmail.googleapis.com/gmail/v1"

# Rate limiting: max requests per second
MAX_REQUESTS_PER_SECOND = 10


class GmailClientError(Exception):
    """Raised when Gmail API operations fail."""


@dataclass
class EmailMessage:
    """Represents a Gmail email message."""

    gmail_id: str
    thread_id: str
    subject: str = ""
    from_email: str = ""
    to_emails: list[str] = field(default_factory=list)
    cc_emails: list[str] = field(default_factory=list)
    body_text: str = ""
    body_html: str = ""
    received_at: datetime | None = None
    labels: list[str] = field(default_factory=list)
    is_read: bool = False
    snippet: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "gmail_id": self.gmail_id,
            "thread_id": self.thread_id,
            "subject": self.subject,
            "from_email": self.from_email,
            "to_emails": self.to_emails,
            "cc_emails": self.cc_emails,
            "body_text": self.body_text,
            "received_at": self.received_at.isoformat() if self.received_at else None,
            "labels": self.labels,
            "is_read": self.is_read,
            "snippet": self.snippet,
        }


class GmailClient:
    """Async Gmail API client.

    Wraps the Gmail REST API with rate limiting and error handling.
    Each client instance is bound to a single Gmail account's
    access token.
    """

    def __init__(
        self,
        access_token: str,
        *,
        max_rps: int = MAX_REQUESTS_PER_SECOND,
        timeout: float = 30.0,
    ) -> None:
        """Initialize the Gmail client.

        Args:
            access_token: Google OAuth2 access token.
            max_rps: Maximum requests per second.
            timeout: HTTP request timeout in seconds.
        """
        if not access_token:
            raise ValueError("access_token is required")
        self._access_token = access_token
        self._max_rps = max_rps
        self._timeout = timeout
        self._request_count = 0
        self._window_start = 0.0
        self._semaphore = asyncio.Semaphore(max_rps)

    def _headers(self) -> dict[str, str]:
        """Return authorization headers."""
        return {"Authorization": f"Bearer {self._access_token}"}

    async def _rate_limit(self) -> None:
        """Simple rate limiter."""
        async with self._semaphore:
            now = asyncio.get_event_loop().time()
            if now - self._window_start >= 1.0:
                self._window_start = now
                self._request_count = 0
            self._request_count += 1

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        """Make an authenticated GET request."""
        await self._rate_limit()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.get(url, headers=self._headers(), params=params)
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                return result
            except httpx.HTTPStatusError as exc:
                raise GmailClientError(
                    f"Gmail API error {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                raise GmailClientError(f"Gmail API request failed: {exc}") from exc

    async def _post(self, url: str, json_data: dict[str, Any]) -> dict[str, Any]:
        """Make an authenticated POST request."""
        await self._rate_limit()
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            try:
                response = await client.post(url, headers=self._headers(), json=json_data)
                response.raise_for_status()
                result: dict[str, Any] = response.json()
                return result
            except httpx.HTTPStatusError as exc:
                raise GmailClientError(
                    f"Gmail API error {exc.response.status_code}: {exc.response.text}"
                ) from exc
            except httpx.RequestError as exc:
                raise GmailClientError(f"Gmail API request failed: {exc}") from exc

    async def list_messages(
        self,
        *,
        query: str = "",
        max_results: int = 20,
        page_token: str | None = None,
        label_ids: list[str] | None = None,
    ) -> tuple[list[dict[str, str]], str | None]:
        """List message IDs matching a query.

        Args:
            query: Gmail search query (e.g., "is:unread").
            max_results: Maximum messages to return.
            page_token: Token for pagination.
            label_ids: Filter by label IDs.

        Returns:
            Tuple of (list of message stubs, next_page_token or None).
        """
        url = f"{GMAIL_API_BASE}/users/me/messages"
        params: dict[str, Any] = {"maxResults": max_results}
        if query:
            params["q"] = query
        if page_token:
            params["pageToken"] = page_token
        if label_ids:
            params["labelIds"] = ",".join(label_ids)

        data = await self._get(url, params)
        messages = data.get("messages", [])
        next_token = data.get("nextPageToken")

        log.debug(
            "messages_listed",
            count=len(messages),
            has_more=next_token is not None,
        )
        return messages, next_token

    async def get_message(self, message_id: str, *, fmt: str = "full") -> EmailMessage:
        """Get a full message by ID.

        Args:
            message_id: The Gmail message ID.
            fmt: Response format ("full", "metadata", "minimal").

        Returns:
            Parsed EmailMessage.
        """
        url = f"{GMAIL_API_BASE}/users/me/messages/{message_id}"
        data = await self._get(url, {"format": fmt})
        return self._parse_message(data)

    async def send_message(
        self,
        to: str,
        subject: str,
        body: str,
        *,
        reply_to_message_id: str | None = None,
        thread_id: str | None = None,
    ) -> dict[str, Any]:
        """Send an email message.

        Args:
            to: Recipient email address.
            subject: Email subject.
            body: Plain text body.
            reply_to_message_id: Message ID to reply to.
            thread_id: Thread ID for threading.

        Returns:
            Send result with message ID.
        """
        msg = email.mime.text.MIMEText(body)
        msg["to"] = to
        msg["subject"] = subject

        if reply_to_message_id:
            msg["In-Reply-To"] = reply_to_message_id
            msg["References"] = reply_to_message_id

        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

        send_data: dict[str, Any] = {"raw": raw}
        if thread_id:
            send_data["threadId"] = thread_id

        url = f"{GMAIL_API_BASE}/users/me/messages/send"
        result = await self._post(url, send_data)
        log.info("message_sent", to=to, subject=subject[:50])
        return result

    async def modify_message(
        self,
        message_id: str,
        *,
        add_labels: list[str] | None = None,
        remove_labels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Modify message labels (mark read/unread, archive, etc.).

        Args:
            message_id: The Gmail message ID.
            add_labels: Label IDs to add.
            remove_labels: Label IDs to remove.

        Returns:
            Modified message data.
        """
        url = f"{GMAIL_API_BASE}/users/me/messages/{message_id}/modify"
        body: dict[str, Any] = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels

        result = await self._post(url, body)
        log.debug("message_modified", message_id=message_id)
        return result

    async def mark_as_read(self, message_id: str) -> dict[str, Any]:
        """Mark a message as read."""
        return await self.modify_message(message_id, remove_labels=["UNREAD"])

    async def get_profile(self) -> dict[str, Any]:
        """Get the authenticated user's Gmail profile."""
        url = f"{GMAIL_API_BASE}/users/me/profile"
        return await self._get(url)

    async def get_history(
        self,
        start_history_id: str,
        *,
        max_results: int = 100,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Get mailbox history changes since a history ID.

        Args:
            start_history_id: The history ID to start from.
            max_results: Maximum history records.

        Returns:
            Tuple of (history records, latest history ID).
        """
        url = f"{GMAIL_API_BASE}/users/me/history"
        params: dict[str, Any] = {
            "startHistoryId": start_history_id,
            "maxResults": max_results,
        }

        data = await self._get(url, params)
        history = data.get("history", [])
        history_id = data.get("historyId")
        return history, history_id

    # ------------------------------------------------------------------
    # Parsing helpers
    # ------------------------------------------------------------------

    def _parse_message(self, data: dict[str, Any]) -> EmailMessage:
        """Parse Gmail API message response into EmailMessage."""
        headers = {}
        payload = data.get("payload", {})
        for header in payload.get("headers", []):
            name = header.get("name", "").lower()
            headers[name] = header.get("value", "")

        # Parse recipients
        to_raw = headers.get("to", "")
        cc_raw = headers.get("cc", "")
        to_list = [e.strip() for e in to_raw.split(",") if e.strip()] if to_raw else []
        cc_list = [e.strip() for e in cc_raw.split(",") if e.strip()] if cc_raw else []

        # Parse body
        body_text, body_html = self._extract_body(payload)

        # Parse date
        received_at = None
        internal_date = data.get("internalDate")
        if internal_date:
            with contextlib.suppress(ValueError, TypeError, OSError):
                received_at = datetime.fromtimestamp(int(internal_date) / 1000)

        label_ids = data.get("labelIds", [])
        is_read = "UNREAD" not in label_ids

        return EmailMessage(
            gmail_id=data.get("id", ""),
            thread_id=data.get("threadId", ""),
            subject=headers.get("subject", ""),
            from_email=headers.get("from", ""),
            to_emails=to_list,
            cc_emails=cc_list,
            body_text=body_text,
            body_html=body_html,
            received_at=received_at,
            labels=label_ids,
            is_read=is_read,
            snippet=data.get("snippet", ""),
        )

    def _extract_body(self, payload: dict[str, Any]) -> tuple[str, str]:
        """Extract text and HTML body from message payload."""
        text_body = ""
        html_body = ""

        mime_type = payload.get("mimeType", "")

        if mime_type == "text/plain":
            text_body = self._decode_body_data(payload.get("body", {}))
        elif mime_type == "text/html":
            html_body = self._decode_body_data(payload.get("body", {}))
        elif "parts" in payload:
            for part in payload["parts"]:
                part_mime = part.get("mimeType", "")
                if part_mime == "text/plain" and not text_body:
                    text_body = self._decode_body_data(part.get("body", {}))
                elif part_mime == "text/html" and not html_body:
                    html_body = self._decode_body_data(part.get("body", {}))
                elif "parts" in part:
                    # Nested multipart
                    inner_text, inner_html = self._extract_body(part)
                    if not text_body:
                        text_body = inner_text
                    if not html_body:
                        html_body = inner_html

        return text_body, html_body

    def _decode_body_data(self, body: dict[str, Any]) -> str:
        """Decode base64url-encoded body data."""
        data = body.get("data", "")
        if not data:
            return ""
        try:
            return base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
        except Exception:
            return ""
