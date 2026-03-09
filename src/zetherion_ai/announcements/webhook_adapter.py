"""Webhook channel adapter for announcement delivery."""

from __future__ import annotations

from typing import Any

import httpx

from zetherion_ai.announcements.dispatcher import AnnouncementDispatchError
from zetherion_ai.announcements.storage import AnnouncementEvent


class WebhookChannelAdapter:
    """Adapter that POSTs announcement events to a recipient webhook."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 15.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._client = client
        self._timeout_seconds = max(1.0, float(timeout_seconds))

    async def send(self, event: AnnouncementEvent) -> None:
        recipient = event.recipient
        webhook_url = (
            str(recipient.webhook_url).strip() if recipient and recipient.webhook_url else ""
        )
        if not webhook_url:
            raise AnnouncementDispatchError(
                code="missing_webhook_url",
                detail="Webhook recipient is missing webhook_url",
                retryable=False,
            )

        payload: dict[str, Any] = {
            "event_id": event.event_id,
            "source": event.source,
            "category": event.category,
            "severity": event.severity.value,
            "tenant_id": event.tenant_id,
            "title": event.title,
            "body": event.body,
            "payload": event.payload,
            "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
            "recipient": {
                "channel": recipient.channel,
                "routing_key": recipient.routing_key,
            },
        }

        client = self._client or httpx.AsyncClient(timeout=self._timeout_seconds)
        owns_client = self._client is None
        try:
            response = await client.post(webhook_url, json=payload)
            if 200 <= response.status_code < 300:
                return
            raise AnnouncementDispatchError(
                code=f"webhook_http_{response.status_code}",
                detail=response.text[:500],
                retryable=response.status_code in {408, 409, 425, 429, 500, 502, 503, 504},
            )
        except httpx.RequestError as exc:
            raise AnnouncementDispatchError(
                code="webhook_request_error",
                detail=str(exc),
                retryable=True,
            ) from exc
        finally:
            if owns_client:
                await client.aclose()
