"""Email channel adapter for announcement delivery."""

from __future__ import annotations

from typing import Any, Protocol

from zetherion_ai.announcements.dispatcher import AnnouncementDispatchError
from zetherion_ai.announcements.storage import AnnouncementEvent


class AnnouncementEmailSender(Protocol):
    """Protocol for pluggable announcement email senders."""

    async def send(
        self,
        *,
        to_address: str,
        subject: str,
        body: str,
        metadata: dict[str, Any],
    ) -> None:
        """Send one email message."""


class EmailChannelAdapter:
    """Adapter that routes announcement events through a pluggable email sender."""

    def __init__(self, sender: AnnouncementEmailSender) -> None:
        self._sender = sender

    async def send(self, event: AnnouncementEvent) -> None:
        recipient = event.recipient
        email = str(recipient.email).strip().lower() if recipient and recipient.email else ""
        if not email:
            raise AnnouncementDispatchError(
                code="missing_email_recipient",
                detail="Email recipient is missing email address",
                retryable=False,
            )

        try:
            await self._sender.send(
                to_address=email,
                subject=event.title.strip() or "Announcement",
                body=event.body.strip() or "No details provided.",
                metadata={
                    "event_id": event.event_id,
                    "source": event.source,
                    "category": event.category,
                    "severity": event.severity.value,
                    "tenant_id": event.tenant_id,
                    "payload": event.payload,
                    "recipient_key": recipient.routing_key,
                },
            )
        except AnnouncementDispatchError:
            raise
        except ValueError as exc:
            raise AnnouncementDispatchError(
                code="invalid_email_recipient",
                detail=str(exc),
                retryable=False,
            ) from exc
        except Exception as exc:
            raise AnnouncementDispatchError(
                code="email_delivery_failed",
                detail=str(exc),
                retryable=True,
            ) from exc
