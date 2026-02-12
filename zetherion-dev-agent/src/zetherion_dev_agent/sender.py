"""Discord webhook sender for dev events."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx

# Discord embed colour (blue)
EMBED_COLOR = 3447003

# Embed colours per event type
EVENT_COLORS = {
    "commit": 3447003,  # Blue
    "annotation": 16776960,  # Yellow
    "session": 10181046,  # Purple
    "tag": 3066993,  # Green
}


async def send_event(
    webhook_url: str,
    agent_name: str,
    event_type: str,
    description: str,
    fields: dict[str, str],
    *,
    timestamp: datetime | None = None,
) -> bool:
    """Send a dev event to Discord via webhook.

    Args:
        webhook_url: Discord webhook URL.
        agent_name: Username to display (used for bot-side filtering).
        event_type: Type of event (commit, annotation, session, tag).
        description: Human-readable description.
        fields: Structured fields to include in the embed.
        timestamp: Event timestamp (defaults to now UTC).

    Returns:
        True if the webhook was sent successfully.
    """
    ts = timestamp or datetime.now(UTC)

    embed: dict[str, Any] = {
        "title": event_type,
        "description": description,
        "fields": [{"name": k, "value": str(v), "inline": True} for k, v in fields.items()],
        "timestamp": ts.isoformat(),
        "color": EVENT_COLORS.get(event_type, EMBED_COLOR),
    }

    payload = {
        "username": agent_name,
        "embeds": [embed],
    }

    async with httpx.AsyncClient(timeout=10.0) as client:
        resp = await client.post(webhook_url, json=payload)
        return resp.status_code in (200, 204)
