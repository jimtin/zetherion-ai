"""Unit tests for owner-personal operational and review state storage."""

from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.personal.models import (
    PersonalOperationalItem,
    PersonalOperationalItemStatus,
    PersonalOperationalItemType,
    PersonalReviewItem,
    PersonalReviewItemStatus,
    PersonalReviewItemType,
)
from zetherion_ai.personal.operational_storage import (
    OwnerPersonalIntelligenceStorage,
    _schema_sql,
    ensure_owner_personal_intelligence_schema,
)


class _DummyEncryptor:
    def encrypt_value(self, value: str) -> str:
        return f"enc::{value}"

    def decrypt_value(self, value: str) -> str:
        if value.startswith("enc::"):
            return value[5:]
        return value


def _make_mock_pool():
    pool = AsyncMock()
    conn = AsyncMock()

    acq_cm = AsyncMock()
    acq_cm.__aenter__.return_value = conn
    acq_cm.__aexit__.return_value = False
    pool.acquire = MagicMock(return_value=acq_cm)
    return pool, conn


class TestOwnerPersonalIntelligenceStorage:
    @pytest.mark.asyncio
    async def test_ensure_schema_executes_owner_schema_sql(self) -> None:
        pool, conn = _make_mock_pool()
        storage = OwnerPersonalIntelligenceStorage(pool, schema="owner_personal")

        await storage.ensure_schema()

        conn.execute.assert_awaited_once_with(_schema_sql("owner_personal"))

    @pytest.mark.asyncio
    async def test_upsert_operational_item_encrypts_and_decrypts_round_trip(self) -> None:
        pool, conn = _make_mock_pool()
        conn.fetchrow.return_value = {
            "id": 7,
            "user_id": 42,
            "item_type": "commitment",
            "title_value": "enc::Ship Segment 8",
            "detail_value": "enc::Finish owner-personal operational storage",
            "status": "in_progress",
            "due_at": datetime(2026, 3, 8, 12, 0, 0),
            "tags": ["segment-8", "owner-personal"],
            "metadata_json": {"plan_id": "plan-1"},
            "source": "execution_ledger",
            "external_ref": "plan:plan-1",
            "created_at": datetime(2026, 3, 7, 10, 0, 0),
            "updated_at": datetime(2026, 3, 7, 11, 0, 0),
            "completed_at": None,
        }
        storage = OwnerPersonalIntelligenceStorage(
            pool,
            schema="owner_personal",
            encryptor=_DummyEncryptor(),
        )

        item = await storage.upsert_operational_item(
            PersonalOperationalItem(
                user_id=42,
                item_type=PersonalOperationalItemType.COMMITMENT,
                title="Ship Segment 8",
                detail="Finish owner-personal operational storage",
                status=PersonalOperationalItemStatus.IN_PROGRESS,
                tags=["segment-8", "owner-personal"],
                metadata={"plan_id": "plan-1"},
                source="execution_ledger",
                external_ref="plan:plan-1",
            )
        )

        assert item.title == "Ship Segment 8"
        assert item.detail == "Finish owner-personal operational storage"
        sql, *args = conn.fetchrow.await_args.args
        assert '"owner_personal".personal_operational_items' in sql
        assert args[2] == "enc::Ship Segment 8"
        assert args[3] == "enc::Finish owner-personal operational storage"

    @pytest.mark.asyncio
    async def test_list_operational_items_active_only_uses_active_statuses(self) -> None:
        pool, conn = _make_mock_pool()
        conn.fetch.return_value = [
            {
                "id": 9,
                "user_id": 42,
                "item_type": "blocker",
                "title_value": "Waiting on WhatsApp desktop pairing",
                "detail_value": None,
                "status": "blocked",
                "due_at": None,
                "tags": [],
                "metadata_json": {},
                "source": "manual",
                "external_ref": None,
                "created_at": datetime(2026, 3, 7, 11, 0, 0),
                "updated_at": datetime(2026, 3, 7, 11, 0, 0),
                "completed_at": None,
            }
        ]
        storage = OwnerPersonalIntelligenceStorage(pool, schema="owner_personal")

        items = await storage.list_operational_items(42, active_only=True, limit=5)

        assert len(items) == 1
        sql, *args = conn.fetch.await_args.args
        assert "status = ANY($2::text[])" in sql
        assert args[1] == ["active", "in_progress", "blocked"]

    @pytest.mark.asyncio
    async def test_upsert_and_resolve_review_item_round_trip(self) -> None:
        pool, conn = _make_mock_pool()
        created_at = datetime(2026, 3, 7, 12, 0, 0)
        resolved_at = datetime(2026, 3, 7, 12, 30, 0)
        conn.fetchrow.side_effect = [
            {
                "id": 11,
                "user_id": 42,
                "item_type": "approval_required",
                "title_value": "enc::Approve overnight worker promotion",
                "detail_value": "enc::Worker produced a low-risk PR and is waiting for approval",
                "status": "pending",
                "source": "queue_manager",
                "related_resource": "plan:42",
                "priority": 90,
                "metadata_json": {"plan_id": "plan-42"},
                "due_at": None,
                "created_at": created_at,
                "updated_at": created_at,
                "resolved_at": None,
            },
            {
                "id": 11,
                "user_id": 42,
                "item_type": "approval_required",
                "title_value": "enc::Approve overnight worker promotion",
                "detail_value": "enc::Worker produced a low-risk PR and is waiting for approval",
                "status": "resolved",
                "source": "queue_manager",
                "related_resource": "plan:42",
                "priority": 90,
                "metadata_json": {"plan_id": "plan-42"},
                "due_at": None,
                "created_at": created_at,
                "updated_at": resolved_at,
                "resolved_at": resolved_at,
            },
        ]
        storage = OwnerPersonalIntelligenceStorage(
            pool,
            schema="owner_personal",
            encryptor=_DummyEncryptor(),
        )

        item = await storage.upsert_review_item(
            PersonalReviewItem(
                user_id=42,
                item_type=PersonalReviewItemType.APPROVAL_REQUIRED,
                title="Approve overnight worker promotion",
                detail="Worker produced a low-risk PR and is waiting for approval",
                source="queue_manager",
                related_resource="plan:42",
                priority=90,
                metadata={"plan_id": "plan-42"},
            )
        )
        resolved = await storage.resolve_review_item(11, user_id=42)

        assert item.status is PersonalReviewItemStatus.PENDING
        assert resolved is not None
        assert resolved.status is PersonalReviewItemStatus.RESOLVED
        assert resolved.resolved_at == resolved_at


@pytest.mark.asyncio
async def test_ensure_owner_personal_intelligence_schema_skips_invalid_pool() -> None:
    assert await ensure_owner_personal_intelligence_schema(object()) == ()
