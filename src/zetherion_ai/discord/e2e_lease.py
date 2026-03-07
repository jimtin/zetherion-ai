"""Shared helpers for Discord E2E lease metadata."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

TOPIC_PREFIX = "zetherion-e2e:"
_THREAD_MODE_TO_TOKEN = {
    "local_required": "lr",
    "windows_prod_canary": "wc",
}
_THREAD_TOKEN_TO_MODE = {value: key for key, value in _THREAD_MODE_TO_TOKEN.items()}


@dataclass(frozen=True)
class DiscordE2ELease:
    """Lease metadata encoded into ephemeral Discord E2E resources."""

    run_id: str
    mode: str
    target_bot_id: int
    author_id: int
    created_at: datetime
    expires_at: datetime
    guild_id: int
    category_id: int | None
    channel_prefix: str
    parent_run_id: str | None = None

    def to_payload(self) -> dict[str, Any]:
        """Return a JSON-serializable payload."""
        payload: dict[str, Any] = {
            "version": 1,
            "run_id": self.run_id,
            "mode": self.mode,
            "target_bot_id": self.target_bot_id,
            "author_id": self.author_id,
            "created_at": self.created_at.astimezone(UTC).isoformat(),
            "expires_at": self.expires_at.astimezone(UTC).isoformat(),
            "guild_id": self.guild_id,
            "category_id": self.category_id,
            "channel_prefix": self.channel_prefix,
        }
        if self.parent_run_id:
            payload["parent_run_id"] = self.parent_run_id
        return payload

    def to_topic(self) -> str:
        """Encode the lease into a Discord channel topic."""
        return TOPIC_PREFIX + json.dumps(self.to_payload(), separators=(",", ":"), sort_keys=True)

    def to_thread_name(self) -> str:
        """Encode the lease into a Discord thread name when topics are unavailable."""
        mode_token = _THREAD_MODE_TO_TOKEN.get(self.mode, "lr")
        expires_at_epoch = int(self.expires_at.astimezone(UTC).timestamp())
        return (
            f"{self.channel_prefix}-m-{mode_token}-r-{self.run_id}-a-{self.author_id}"
            f"-t-{self.target_bot_id}-e-{expires_at_epoch}"
        )[:100]

    def is_active(self, *, now: datetime | None = None) -> bool:
        """Return True when the lease has not expired yet."""
        effective_now = now or datetime.now(tz=UTC)
        return effective_now <= self.expires_at.astimezone(UTC)

    @classmethod
    def from_payload(cls, payload: dict[str, Any]) -> DiscordE2ELease:
        """Decode a lease payload into a typed lease object."""
        return cls(
            run_id=str(payload["run_id"]),
            mode=str(payload["mode"]),
            target_bot_id=int(payload["target_bot_id"]),
            author_id=int(payload["author_id"]),
            created_at=_parse_datetime(payload["created_at"]),
            expires_at=_parse_datetime(payload["expires_at"]),
            guild_id=int(payload["guild_id"]),
            category_id=(
                None if payload.get("category_id") in (None, "") else int(payload["category_id"])
            ),
            channel_prefix=str(payload["channel_prefix"]),
            parent_run_id=(str(payload["parent_run_id"]) if payload.get("parent_run_id") else None),
        )

    @classmethod
    def from_topic(cls, topic: str | None) -> DiscordE2ELease | None:
        """Decode a lease from a channel topic, if present."""
        if not topic or not topic.startswith(TOPIC_PREFIX):
            return None
        try:
            payload = json.loads(topic[len(TOPIC_PREFIX) :])
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, dict):
            return None
        try:
            return cls.from_payload(payload)
        except (KeyError, TypeError, ValueError):
            return None

    @classmethod
    def from_thread_name(
        cls,
        name: str | None,
        *,
        channel_prefix: str,
        guild_id: int,
    ) -> DiscordE2ELease | None:
        """Decode a lease from a thread name, if present."""
        if not name:
            return None
        prefix = channel_prefix.strip().lower()
        if not prefix or not name.lower().startswith(prefix):
            return None
        pattern = re.compile(
            rf"^{re.escape(channel_prefix)}-m-(?P<mode>[a-z]{{2}})-r-(?P<run_id>.+?)-a-(?P<author_id>\d+)-t-(?P<target_bot_id>\d+)-e-(?P<expires_at>\d+)$",
            re.IGNORECASE,
        )
        match = pattern.match(name)
        if match is None:
            return None
        try:
            mode = _THREAD_TOKEN_TO_MODE.get(match.group("mode").lower(), "local_required")
            expires_at = datetime.fromtimestamp(int(match.group("expires_at")), tz=UTC)
            return cls(
                run_id=match.group("run_id"),
                mode=mode,
                target_bot_id=int(match.group("target_bot_id")),
                author_id=int(match.group("author_id")),
                created_at=expires_at,
                expires_at=expires_at,
                guild_id=guild_id,
                category_id=None,
                channel_prefix=channel_prefix,
            )
        except (TypeError, ValueError):
            return None

    @classmethod
    def from_channel_metadata(
        cls,
        *,
        topic: str | None,
        name: str | None,
        channel_prefix: str,
        guild_id: int,
    ) -> DiscordE2ELease | None:
        """Decode a lease from topic first, then thread name."""
        lease = cls.from_topic(topic)
        if lease is not None:
            return lease
        return cls.from_thread_name(name, channel_prefix=channel_prefix, guild_id=guild_id)


def _parse_datetime(raw: Any) -> datetime:
    if not isinstance(raw, str) or not raw.strip():
        raise ValueError("timestamp must be a non-empty string")
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)
