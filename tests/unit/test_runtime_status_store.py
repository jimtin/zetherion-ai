"""Tests for shared runtime status storage."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.runtime.status_store import (
    RuntimeStatusStore,
    _encode_details,
    _row_to_status,
)


def _make_pool() -> tuple[MagicMock, AsyncMock]:
    pool = MagicMock()
    conn = AsyncMock()

    ctx = AsyncMock()
    ctx.__aenter__ = AsyncMock(return_value=conn)
    ctx.__aexit__ = AsyncMock(return_value=False)
    pool.acquire.return_value = ctx
    return pool, conn


@pytest.mark.asyncio
async def test_initialize_creates_runtime_service_status_table() -> None:
    pool, conn = _make_pool()
    store = RuntimeStatusStore(pool)

    await store.initialize()

    conn.execute.assert_awaited_once()
    assert "CREATE TABLE IF NOT EXISTS runtime_service_status" in conn.execute.call_args.args[0]


@pytest.mark.asyncio
async def test_upsert_and_get_status_round_trip_shape() -> None:
    pool, conn = _make_pool()
    store = RuntimeStatusStore(pool)

    await store.upsert_status(
        service_name="discord_bot",
        status="healthy",
        summary="Discord bot is connected and ready.",
        details={"guild_count": 3},
        release_revision="abc123",
        instance_id="instance-1",
    )

    updated_at = datetime.now(UTC)
    conn.fetchrow.return_value = {
        "service_name": "discord_bot",
        "status": "healthy",
        "summary": "Discord bot is connected and ready.",
        "details": {"guild_count": 3},
        "release_revision": "abc123",
        "instance_id": "instance-1",
        "updated_at": updated_at,
    }

    status = await store.get_status("discord_bot")

    assert status is not None
    assert status["service_name"] == "discord_bot"
    assert status["status"] == "healthy"
    assert status["details"] == {"guild_count": 3}
    assert status["release_revision"] == "abc123"
    assert status["updated_at"] == updated_at.isoformat()


def test_row_to_status_handles_invalid_json_and_naive_datetimes() -> None:
    row = {
        "service_name": "discord_bot",
        "status": "degraded",
        "summary": "Waiting on queue worker.",
        "details": "{not-json}",
        "release_revision": "abc123",
        "instance_id": "instance-1",
        "updated_at": datetime(2026, 3, 13, 8, 30),
    }

    status = _row_to_status(row)

    assert status == {
        "service_name": "discord_bot",
        "status": "degraded",
        "summary": "Waiting on queue worker.",
        "details": {},
        "release_revision": "abc123",
        "instance_id": "instance-1",
        "updated_at": "2026-03-13T08:30:00+00:00",
    }
    assert _row_to_status(None) is None
    assert _encode_details(None) == "{}"


def test_row_to_status_handles_non_mapping_details_and_missing_datetime() -> None:
    row = {
        "service_name": "skills_api",
        "status": "starting",
        "summary": "Booting services.",
        "details": ["unexpected", "list"],
        "release_revision": "rev-2",
        "instance_id": "api-1",
        "updated_at": "not-a-datetime",
    }

    status = _row_to_status(row)

    assert status == {
        "service_name": "skills_api",
        "status": "starting",
        "summary": "Booting services.",
        "details": {},
        "release_revision": "rev-2",
        "instance_id": "api-1",
        "updated_at": None,
    }


@pytest.mark.asyncio
async def test_list_statuses_filters_none_rows_and_preserves_dict_details() -> None:
    pool, conn = _make_pool()
    store = RuntimeStatusStore(pool)
    conn.fetch.return_value = [
        {
            "service_name": "bot",
            "status": "healthy",
            "summary": "ok",
            "details": {"guilds": 4},
            "release_revision": "rev-1",
            "instance_id": "bot-1",
            "updated_at": datetime.now(UTC),
        },
        {
            "service_name": "worker",
            "status": "healthy",
            "summary": "ok",
            "details": '{"jobs":2}',
            "release_revision": "rev-1",
            "instance_id": "worker-1",
            "updated_at": datetime.now(UTC),
        },
    ]

    statuses = await store.list_statuses()

    assert [item["service_name"] for item in statuses] == ["bot", "worker"]
    assert statuses[0]["details"] == {"guilds": 4}
    assert statuses[1]["details"] == {"jobs": 2}
