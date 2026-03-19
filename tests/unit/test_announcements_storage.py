from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

import zetherion_ai.announcements.storage as storage_mod
from zetherion_ai.announcements.storage import AnnouncementRepository


class _DummyAcquire:
    def __init__(self, conn: object) -> None:
        self._conn = conn

    async def __aenter__(self) -> object:
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _DummyPool:
    def __init__(self) -> None:
        self.conn = type(
            "Conn",
            (),
            {
                "execute": AsyncMock(return_value="OK"),
            },
        )()

    def acquire(self) -> _DummyAcquire:
        return _DummyAcquire(self.conn)


@pytest.mark.asyncio
async def test_announcement_repository_initialize_tolerates_concurrent_schema_race(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeUniqueViolationError(Exception):
        pass

    pool = _DummyPool()
    pool.conn.execute.side_effect = _FakeUniqueViolationError("pg_type_typname_nsp_index")
    monkeypatch.setattr(storage_mod.asyncpg, "UniqueViolationError", _FakeUniqueViolationError)

    repository = AnnouncementRepository()
    await repository.initialize(pool)  # type: ignore[arg-type]

    assert repository._pool is pool
    pool.conn.execute.assert_awaited_once()
