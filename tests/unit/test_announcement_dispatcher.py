"""Unit tests for announcement dispatch worker and Discord adapter."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, call

import discord
import pytest

from zetherion_ai.announcements.discord_adapter import DiscordDMChannelAdapter
from zetherion_ai.announcements.dispatcher import (
    AnnouncementDispatcher,
    AnnouncementDispatchError,
)
from zetherion_ai.announcements.storage import (
    AnnouncementDelivery,
    AnnouncementEvent,
    AnnouncementSeverity,
)


def _delivery(*, retry_count: int = 0) -> AnnouncementDelivery:
    now = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
    return AnnouncementDelivery(
        delivery_id=1,
        event_id="evt-1",
        channel="discord_dm",
        scheduled_for=now,
        sent_at=None,
        status="processing",
        error_code=None,
        error_detail=None,
        retry_count=retry_count,
        created_at=now,
        updated_at=now,
    )


def _event(*, target_user_id: int = 42) -> AnnouncementEvent:
    now = datetime(2026, 3, 6, 10, 0, tzinfo=UTC)
    return AnnouncementEvent(
        event_id="evt-1",
        source="skills",
        category="skill.reminder",
        severity=AnnouncementSeverity.NORMAL,
        tenant_id=None,
        target_user_id=target_user_id,
        title="Reminder",
        body="Review your queue.",
        payload={},
        fingerprint=None,
        idempotency_key=None,
        occurred_at=now,
        created_at=now,
        state="digest",
    )


@pytest.mark.asyncio
async def test_dispatcher_run_once_marks_sent_on_success() -> None:
    repository = MagicMock()
    repository.claim_due_deliveries = AsyncMock(return_value=[_delivery()])
    repository.get_event = AsyncMock(return_value=_event())
    repository.mark_delivery_sent = AsyncMock(return_value=True)
    repository.mark_delivery_failed = AsyncMock(return_value=False)

    adapter = MagicMock()
    adapter.send = AsyncMock(return_value=None)

    dispatcher = AnnouncementDispatcher(repository, adapter)
    processed = await dispatcher.run_once()

    assert processed == 1
    adapter.send.assert_awaited_once()
    repository.mark_delivery_sent.assert_awaited_once()
    repository.mark_delivery_failed.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatcher_retryable_error_marks_retry() -> None:
    repository = MagicMock()
    repository.claim_due_deliveries = AsyncMock(return_value=[_delivery(retry_count=0)])
    repository.get_event = AsyncMock(return_value=_event())
    repository.mark_delivery_sent = AsyncMock(return_value=False)
    repository.mark_delivery_failed = AsyncMock(return_value=True)

    adapter = MagicMock()
    adapter.send = AsyncMock(
        side_effect=AnnouncementDispatchError(
            code="discord_send_http_503",
            detail="upstream unavailable",
            retryable=True,
        )
    )

    dispatcher = AnnouncementDispatcher(repository, adapter, max_retry_delay_seconds=3600)
    processed = await dispatcher.run_once()

    assert processed == 1
    repository.mark_delivery_sent.assert_not_awaited()
    repository.mark_delivery_failed.assert_awaited_once()
    kwargs = repository.mark_delivery_failed.await_args.kwargs
    assert kwargs["terminal"] is False
    assert kwargs["error_code"] == "discord_send_http_503"
    assert kwargs["retry_delay_seconds"] == 60


@pytest.mark.asyncio
async def test_dispatcher_missing_event_marks_terminal_failure() -> None:
    repository = MagicMock()
    repository.claim_due_deliveries = AsyncMock(return_value=[_delivery(retry_count=2)])
    repository.get_event = AsyncMock(return_value=None)
    repository.mark_delivery_sent = AsyncMock(return_value=False)
    repository.mark_delivery_failed = AsyncMock(return_value=True)

    adapter = MagicMock()
    adapter.send = AsyncMock(return_value=None)

    dispatcher = AnnouncementDispatcher(repository, adapter)
    processed = await dispatcher.run_once()

    assert processed == 1
    adapter.send.assert_not_awaited()
    repository.mark_delivery_sent.assert_not_awaited()
    repository.mark_delivery_failed.assert_awaited_once()
    kwargs = repository.mark_delivery_failed.await_args.kwargs
    assert kwargs["terminal"] is True
    assert kwargs["error_code"] == "event_not_found"


@pytest.mark.asyncio
async def test_dispatcher_unhandled_exception_marks_retry_failure() -> None:
    repository = MagicMock()
    repository.claim_due_deliveries = AsyncMock(return_value=[_delivery(retry_count=1)])
    repository.get_event = AsyncMock(return_value=_event())
    repository.mark_delivery_sent = AsyncMock(return_value=False)
    repository.mark_delivery_failed = AsyncMock(return_value=True)

    adapter = MagicMock()
    adapter.send = AsyncMock(side_effect=RuntimeError("boom"))

    dispatcher = AnnouncementDispatcher(repository, adapter)
    processed = await dispatcher.run_once()

    assert processed == 1
    repository.mark_delivery_sent.assert_not_awaited()
    repository.mark_delivery_failed.assert_awaited_once()
    kwargs = repository.mark_delivery_failed.await_args.kwargs
    assert kwargs["terminal"] is False
    assert kwargs["error_code"] == "dispatch_exception"
    assert kwargs["retry_delay_seconds"] == 120


@pytest.mark.asyncio
async def test_dispatcher_run_once_returns_zero_when_no_claims() -> None:
    repository = MagicMock()
    repository.claim_due_deliveries = AsyncMock(return_value=[])
    repository.get_event = AsyncMock(return_value=None)
    repository.mark_delivery_sent = AsyncMock(return_value=False)
    repository.mark_delivery_failed = AsyncMock(return_value=False)

    dispatcher = AnnouncementDispatcher(repository, MagicMock())
    processed = await dispatcher.run_once()

    assert processed == 0
    repository.get_event.assert_not_awaited()


@pytest.mark.asyncio
async def test_dispatcher_start_and_stop_lifecycle() -> None:
    repository = MagicMock()
    repository.claim_due_deliveries = AsyncMock(return_value=[])
    adapter = MagicMock()
    adapter.send = AsyncMock(return_value=None)

    dispatcher = AnnouncementDispatcher(repository, adapter, poll_interval_seconds=1)
    await dispatcher.start()
    assert dispatcher.is_running is True

    await dispatcher.start()  # idempotent start branch
    await dispatcher.stop()
    assert dispatcher.is_running is False


@pytest.mark.asyncio
async def test_dispatcher_stop_handles_completed_task() -> None:
    dispatcher = AnnouncementDispatcher(MagicMock(), MagicMock())
    dispatcher._running = True
    done_task = asyncio.create_task(asyncio.sleep(0))
    await done_task
    dispatcher._task = done_task

    await dispatcher.stop()

    assert dispatcher.is_running is False


@pytest.mark.asyncio
async def test_dispatcher_run_loop_handles_exception_and_recovers() -> None:
    repository = MagicMock()
    repository.claim_due_deliveries = AsyncMock(return_value=[])
    adapter = MagicMock()
    dispatcher = AnnouncementDispatcher(repository, adapter, poll_interval_seconds=1)
    dispatcher._running = True
    call_count = 0

    async def _run_once():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise RuntimeError("boom")
        dispatcher._running = False
        return 1

    dispatcher.run_once = _run_once  # type: ignore[method-assign]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("zetherion_ai.announcements.dispatcher.asyncio.sleep", AsyncMock())
        await dispatcher._run_loop()

    assert call_count == 2


@pytest.mark.asyncio
async def test_dispatcher_run_loop_propagates_cancellation() -> None:
    dispatcher = AnnouncementDispatcher(MagicMock(), MagicMock())
    dispatcher._running = True

    async def _cancel_once():
        raise asyncio.CancelledError

    dispatcher.run_once = _cancel_once  # type: ignore[method-assign]
    with pytest.raises(asyncio.CancelledError):
        await dispatcher._run_loop()


@pytest.mark.asyncio
async def test_dispatcher_run_loop_sleeps_when_no_work() -> None:
    dispatcher = AnnouncementDispatcher(MagicMock(), MagicMock(), poll_interval_seconds=1)
    dispatcher._running = True

    async def _no_work_once():
        return 0

    async def _sleep_and_stop(_seconds: int) -> None:
        dispatcher._running = False

    dispatcher.run_once = _no_work_once  # type: ignore[method-assign]
    with pytest.MonkeyPatch.context() as mp:
        mp.setattr("zetherion_ai.announcements.dispatcher.asyncio.sleep", _sleep_and_stop)
        await dispatcher._run_loop()

    assert dispatcher.is_running is False


def test_dispatcher_retry_delay_caps() -> None:
    dispatcher = AnnouncementDispatcher(MagicMock(), MagicMock(), max_retry_delay_seconds=180)

    assert dispatcher._retry_delay_seconds(1) == 60
    assert dispatcher._retry_delay_seconds(2) == 120
    assert dispatcher._retry_delay_seconds(3) == 180
    assert dispatcher._retry_delay_seconds(99) == 180


@pytest.mark.asyncio
async def test_discord_adapter_send_success() -> None:
    user = AsyncMock()
    user.send = AsyncMock(return_value=None)
    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=True)
    bot.get_user = MagicMock(return_value=user)
    bot.fetch_user = AsyncMock(return_value=None)

    adapter = DiscordDMChannelAdapter(bot)
    await adapter.send(_event())

    user.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_discord_adapter_fetch_user_not_found_raises_terminal() -> None:
    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=True)
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(return_value=None)

    adapter = DiscordDMChannelAdapter(bot)
    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await adapter.send(_event())

    assert exc_info.value.code == "discord_user_not_found"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_discord_adapter_lookup_not_found_exception_maps_terminal() -> None:
    response = MagicMock()
    response.status = 404
    response.reason = "Not Found"

    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=True)
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(side_effect=discord.NotFound(response, "missing"))

    adapter = DiscordDMChannelAdapter(bot)
    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await adapter.send(_event())

    assert exc_info.value.code == "discord_user_not_found"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_discord_adapter_handles_lookup_http_retryable() -> None:
    response = MagicMock()
    response.status = 503
    response.reason = "Service Unavailable"

    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=True)
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(side_effect=discord.HTTPException(response, "upstream"))

    adapter = DiscordDMChannelAdapter(bot)
    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await adapter.send(_event())

    assert exc_info.value.code == "discord_lookup_http_503"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_discord_adapter_handles_lookup_forbidden_terminal() -> None:
    response = MagicMock()
    response.status = 403
    response.reason = "Forbidden"

    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=True)
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(side_effect=discord.Forbidden(response, "forbidden"))

    adapter = DiscordDMChannelAdapter(bot)
    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await adapter.send(_event())

    assert exc_info.value.code == "discord_lookup_forbidden"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_discord_adapter_send_handles_forbidden_terminal() -> None:
    response = MagicMock()
    response.status = 403
    response.reason = "Forbidden"
    user = AsyncMock()
    user.send = AsyncMock(side_effect=discord.Forbidden(response, "forbidden"))

    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=True)
    bot.get_user = MagicMock(return_value=user)
    bot.fetch_user = AsyncMock(return_value=None)

    adapter = DiscordDMChannelAdapter(bot)
    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await adapter.send(_event())

    assert exc_info.value.code == "discord_dm_forbidden"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_discord_adapter_send_handles_not_found_terminal() -> None:
    response = MagicMock()
    response.status = 404
    response.reason = "Not Found"
    user = AsyncMock()
    user.send = AsyncMock(side_effect=discord.NotFound(response, "missing"))

    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=True)
    bot.get_user = MagicMock(return_value=user)
    bot.fetch_user = AsyncMock(return_value=None)

    adapter = DiscordDMChannelAdapter(bot)
    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await adapter.send(_event())

    assert exc_info.value.code == "discord_user_not_found"
    assert exc_info.value.retryable is False


@pytest.mark.asyncio
async def test_discord_adapter_send_handles_http_retryable() -> None:
    response = MagicMock()
    response.status = 429
    response.reason = "Too Many Requests"
    user = AsyncMock()
    user.send = AsyncMock(side_effect=discord.HTTPException(response, "ratelimited"))

    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=True)
    bot.get_user = MagicMock(return_value=user)
    bot.fetch_user = AsyncMock(return_value=None)

    adapter = DiscordDMChannelAdapter(bot)
    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await adapter.send(_event())

    assert exc_info.value.code == "discord_send_http_429"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_discord_adapter_splits_long_messages() -> None:
    user = AsyncMock()
    user.send = AsyncMock(return_value=None)
    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=True)
    bot.get_user = MagicMock(return_value=user)
    bot.fetch_user = AsyncMock(return_value=None)

    adapter = DiscordDMChannelAdapter(bot, max_message_length=200)
    long_event = _event()
    long_event.body = "x" * 600

    await adapter.send(long_event)

    assert user.send.await_count >= 2


@pytest.mark.asyncio
async def test_discord_adapter_skips_empty_long_message_chunks() -> None:
    user = AsyncMock()
    user.send = AsyncMock(return_value=None)
    adapter = DiscordDMChannelAdapter(MagicMock(), max_message_length=10)

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(
            "zetherion_ai.announcements.discord_adapter.split_text_chunks",
            lambda _message, max_length: ["", "part-1", "", "part-2"],
        )
        await adapter._send_long_message(user, "x" * 400)

    assert user.send.await_args_list == [call("part-1"), call("part-2")]


@pytest.mark.asyncio
async def test_discord_adapter_requires_ready_bot() -> None:
    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=False)
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(return_value=None)

    adapter = DiscordDMChannelAdapter(bot)
    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await adapter.send(_event())

    assert exc_info.value.code == "discord_bot_not_ready"
    assert exc_info.value.retryable is True


@pytest.mark.asyncio
async def test_discord_adapter_rejects_invalid_target_user() -> None:
    bot = MagicMock()
    bot.is_ready = MagicMock(return_value=True)
    bot.get_user = MagicMock(return_value=None)
    bot.fetch_user = AsyncMock(return_value=None)

    adapter = DiscordDMChannelAdapter(bot)
    with pytest.raises(AnnouncementDispatchError) as exc_info:
        await adapter.send(_event(target_user_id=0))

    assert exc_info.value.code == "invalid_target_user_id"
    assert exc_info.value.retryable is False


def test_discord_adapter_format_message_without_timestamp() -> None:
    event = _event()
    event.occurred_at = None
    event.created_at = None

    content = DiscordDMChannelAdapter.format_message(event)

    assert "[INFO]" in content


def test_discord_adapter_retryable_status_predicate() -> None:
    assert DiscordDMChannelAdapter._is_retryable_status(503) is True
    assert DiscordDMChannelAdapter._is_retryable_status(400) is False
