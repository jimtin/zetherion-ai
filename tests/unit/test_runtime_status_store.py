"""Tests for shared runtime status storage."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.runtime.status_store import RuntimeStatusStore


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
