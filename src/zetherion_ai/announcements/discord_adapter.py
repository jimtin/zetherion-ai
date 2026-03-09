"""Discord DM channel adapter for announcement delivery."""

from __future__ import annotations

from datetime import UTC

import discord

from zetherion_ai.announcements.dispatcher import AnnouncementDispatchError
from zetherion_ai.announcements.storage import AnnouncementEvent
from zetherion_ai.constants import MAX_DISCORD_MESSAGE_LENGTH
from zetherion_ai.logging import get_logger
from zetherion_ai.utils import split_text_chunks

log = get_logger("zetherion_ai.announcements.discord_adapter")


class DiscordDMChannelAdapter:
    """Adapter that sends announcement events via Discord DM."""

    def __init__(
        self,
        bot: discord.Client,
        *,
        max_message_length: int = MAX_DISCORD_MESSAGE_LENGTH,
    ) -> None:
        self._bot = bot
        self._max_message_length = max(200, int(max_message_length))

    async def send(self, event: AnnouncementEvent) -> None:
        target_user_id = self._target_user_id(event)
        if not self._bot.is_ready():
            raise AnnouncementDispatchError(
                code="discord_bot_not_ready",
                detail="Discord client is not ready",
                retryable=True,
            )

        if target_user_id <= 0:
            raise AnnouncementDispatchError(
                code="invalid_target_user_id",
                detail=f"Invalid target user id: {target_user_id}",
                retryable=False,
            )

        user = self._bot.get_user(target_user_id)
        if user is None:
            try:
                user = await self._bot.fetch_user(target_user_id)
            except discord.NotFound as exc:
                raise AnnouncementDispatchError(
                    code="discord_user_not_found",
                    detail=str(exc),
                    retryable=False,
                ) from exc
            except discord.Forbidden as exc:
                raise AnnouncementDispatchError(
                    code="discord_lookup_forbidden",
                    detail=str(exc),
                    retryable=False,
                ) from exc
            except discord.HTTPException as exc:
                raise AnnouncementDispatchError(
                    code=f"discord_lookup_http_{exc.status or 'unknown'}",
                    detail=str(exc),
                    retryable=self._is_retryable_status(exc.status),
                ) from exc

        if user is None:
            raise AnnouncementDispatchError(
                code="discord_user_not_found",
                detail=f"Discord user {event.target_user_id} not found",
                retryable=False,
            )

        message = self.format_message(event)
        try:
            await self._send_long_message(user, message)
        except discord.Forbidden as exc:
            raise AnnouncementDispatchError(
                code="discord_dm_forbidden",
                detail=str(exc),
                retryable=False,
            ) from exc
        except discord.NotFound as exc:
            raise AnnouncementDispatchError(
                code="discord_user_not_found",
                detail=str(exc),
                retryable=False,
            ) from exc
        except discord.HTTPException as exc:
            raise AnnouncementDispatchError(
                code=f"discord_send_http_{exc.status or 'unknown'}",
                detail=str(exc),
                retryable=self._is_retryable_status(exc.status),
            ) from exc

        log.debug(
            "announcement_discord_dm_sent",
            event_id=event.event_id,
            target_user_id=target_user_id,
        )

    async def _send_long_message(self, user: discord.abc.Messageable, message: str) -> None:
        if len(message) <= self._max_message_length:
            await user.send(message)
            return
        parts = split_text_chunks(message, max_length=self._max_message_length)
        for part in parts:
            if part:
                await user.send(part)

    @staticmethod
    def format_message(event: AnnouncementEvent) -> str:
        severity_label = {
            "critical": "CRITICAL",
            "high": "HIGH",
            "normal": "INFO",
        }.get(event.severity.value, event.severity.value.upper())
        title = event.title.strip() or "Announcement"
        body = event.body.strip() or "No details provided."
        occurred_at = event.occurred_at or event.created_at
        timestamp_line = ""
        if occurred_at is not None:
            timestamp_line = occurred_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M:%S UTC")

        lines = [f"[{severity_label}] **{title}**", body]
        if timestamp_line:
            lines.extend(["", f"*{timestamp_line}*"])
        return "\n".join(lines)

    @staticmethod
    def _is_retryable_status(status: int | None) -> bool:
        return status in {429, 500, 502, 503, 504}

    @staticmethod
    def _target_user_id(event: AnnouncementEvent) -> int:
        if event.recipient is not None and (event.recipient.target_user_id or 0) > 0:
            return int(event.recipient.target_user_id or 0)
        return int(event.target_user_id or 0)
