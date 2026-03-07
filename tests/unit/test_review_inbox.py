"""Unit tests for the canonical owner review inbox."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.personal.models import (
    PersonalReviewItem,
    PersonalReviewItemStatus,
    PersonalReviewItemType,
)
from zetherion_ai.personal.review_inbox import (
    OwnerReviewInbox,
    ReviewFeedbackOutcome,
    ReviewTrustFeedbackTarget,
)
from zetherion_ai.trust.storage import TrustFeedbackEventRecord, TrustScorecardRecord


@pytest.mark.asyncio
async def test_enqueue_item_dedupes_pending_related_resource() -> None:
    storage = AsyncMock()
    storage.get_review_item_by_related_resource = AsyncMock(
        return_value=PersonalReviewItem(
            id=7,
            user_id=42,
            item_type=PersonalReviewItemType.OVERNIGHT_SUMMARY,
            title="Old summary",
            related_resource="execution_plan:plan-1:summary",
            source="plan_executor",
        )
    )
    storage.upsert_review_item = AsyncMock(side_effect=lambda item: item)

    inbox = OwnerReviewInbox(storage=storage)
    item = await inbox.enqueue_overnight_summary(
        user_id=42,
        tenant_id="tenant-1",
        plan_id="plan-1",
        plan_title="Ship the worker",
        goal="Finish the overnight coding run",
        summary="Opened the PR and attached test results.",
        step_index=2,
        total_steps=3,
    )

    assert item.id == 7
    assert item.item_type is PersonalReviewItemType.OVERNIGHT_SUMMARY
    assert item.related_resource == "execution_plan:plan-1:summary"
    assert item.metadata["plan_id"] == "plan-1"
    storage.get_review_item_by_related_resource.assert_awaited_once()
    storage.upsert_review_item.assert_awaited_once()


@pytest.mark.asyncio
async def test_resolve_item_records_canonical_trust_feedback() -> None:
    storage = AsyncMock()
    storage.resolve_review_item = AsyncMock(
        return_value=PersonalReviewItem(
            id=11,
            user_id=42,
            item_type=PersonalReviewItemType.APPROVAL_REQUIRED,
            title="Approve calendar write",
            status=PersonalReviewItemStatus.RESOLVED,
            source="planner",
            metadata={
                "trust_feedback_target": {
                    "subject_id": "owner-1",
                    "subject_type": "owner",
                    "resource_scope": "owner_personal:calendar:write",
                    "action": "calendar.write",
                    "metadata": {"domain": "calendar"},
                }
            },
            resolved_at=datetime.now(UTC),
        )
    )
    trust_storage = AsyncMock()
    trust_storage.record_feedback_outcome = AsyncMock(
        return_value=(
            TrustFeedbackEventRecord(
                event_id="event-1",
                subject_id="owner-1",
                subject_type="owner",
                resource_scope="owner_personal:calendar:write",
                action="calendar.write",
                outcome="approved",
                delta=0.05,
                source_system="review_inbox",
            ),
            TrustScorecardRecord(
                scorecard_id="score-1",
                subject_id="owner-1",
                subject_type="owner",
                resource_scope="owner_personal:calendar:write",
                action="calendar.write",
                score=0.65,
                approvals=4,
                rejections=1,
                edits=0,
                total_interactions=5,
                source_system="review_inbox",
            ),
        )
    )

    inbox = OwnerReviewInbox(storage=storage, trust_storage=trust_storage)
    result = await inbox.resolve_item(
        11,
        user_id=42,
        feedback_outcome=ReviewFeedbackOutcome.APPROVED,
        feedback_metadata={"reviewer": "owner"},
    )

    assert result is not None
    assert result.trust_event is not None
    assert result.trust_scorecard is not None
    trust_storage.record_feedback_outcome.assert_awaited_once()
    kwargs = trust_storage.record_feedback_outcome.await_args.kwargs
    assert kwargs["subject_id"] == "owner-1"
    assert kwargs["action"] == "calendar.write"
    assert kwargs["metadata"]["review_item_id"] == 11
    assert kwargs["metadata"]["reviewer"] == "owner"


@pytest.mark.asyncio
async def test_enqueue_item_attaches_feedback_target_without_dedupe_lookup() -> None:
    storage = AsyncMock()
    storage.get_review_item_by_related_resource = AsyncMock(return_value=None)
    storage.upsert_review_item = AsyncMock(side_effect=lambda item: item)

    inbox = OwnerReviewInbox(storage=storage)
    item = await inbox.enqueue_item(
        PersonalReviewItem(
            user_id=42,
            item_type=PersonalReviewItemType.REVIEW,
            title="Review task",
            source="planner",
            related_resource="task:1",
        ),
        feedback_target=ReviewTrustFeedbackTarget(
            subject_id="owner-1",
            subject_type="owner",
            resource_scope="owner_personal:tasks:write",
            action="tasks.write",
            tenant_id="tenant-1",
            metadata={"domain": "tasks"},
        ),
        dedupe_related_resource=False,
    )

    assert item.metadata["trust_feedback_target"]["subject_id"] == "owner-1"
    assert item.metadata["trust_feedback_target"]["tenant_id"] == "tenant-1"
    storage.upsert_review_item.assert_awaited_once()
    storage.get_review_item_by_related_resource.assert_not_awaited()


@pytest.mark.asyncio
async def test_list_pending_items_delegates_to_storage() -> None:
    storage = AsyncMock()
    storage.list_review_items = AsyncMock(return_value=[])

    inbox = OwnerReviewInbox(storage=storage)
    items = await inbox.list_pending_items(42, limit=9)

    assert items == []
    storage.list_review_items.assert_awaited_once_with(42, pending_only=True, limit=9)


@pytest.mark.asyncio
async def test_resolve_item_returns_none_when_missing() -> None:
    storage = AsyncMock()
    storage.resolve_review_item = AsyncMock(return_value=None)
    trust_storage = AsyncMock()

    inbox = OwnerReviewInbox(storage=storage, trust_storage=trust_storage)
    result = await inbox.resolve_item(404, user_id=42)

    assert result is None
    trust_storage.record_feedback_outcome.assert_not_awaited()


@pytest.mark.asyncio
async def test_resolve_item_without_target_skips_trust_feedback() -> None:
    storage = AsyncMock()
    storage.resolve_review_item = AsyncMock(
        return_value=PersonalReviewItem(
            id=12,
            user_id=42,
            item_type=PersonalReviewItemType.REVIEW,
            title="Review task",
            status=PersonalReviewItemStatus.RESOLVED,
            source="planner",
            metadata={},
            resolved_at=datetime.now(UTC),
        )
    )
    trust_storage = AsyncMock()

    inbox = OwnerReviewInbox(storage=storage, trust_storage=trust_storage)
    result = await inbox.resolve_item(
        12,
        user_id=42,
        feedback_outcome=ReviewFeedbackOutcome.APPROVED,
    )

    assert result is not None
    assert result.trust_event is None
    assert result.trust_scorecard is None
    trust_storage.record_feedback_outcome.assert_not_awaited()


def test_review_feedback_target_round_trip_and_invalid_payloads() -> None:
    target = ReviewTrustFeedbackTarget(
        subject_id="owner-1",
        subject_type="owner",
        resource_scope="owner_personal:calendar:write",
        action="calendar.write",
        tenant_id="tenant-1",
        metadata={"domain": "calendar"},
    )

    payload = target.to_metadata()
    restored = ReviewTrustFeedbackTarget.from_metadata(payload)

    assert restored == target
    assert ReviewTrustFeedbackTarget.from_metadata(None) is None
    assert ReviewTrustFeedbackTarget.from_metadata({"subject_id": "owner-1"}) is None


@pytest.mark.asyncio
async def test_enqueue_execution_failure_builds_blocked_review_item() -> None:
    storage = AsyncMock()
    storage.get_review_item_by_related_resource = AsyncMock(return_value=None)
    storage.upsert_review_item = AsyncMock(side_effect=lambda item: item)
    inbox = OwnerReviewInbox(storage=storage)

    item = await inbox.enqueue_execution_failure(
        user_id=42,
        tenant_id="tenant-1",
        plan_id="plan-1",
        plan_title="Ship worker",
        step_id=None,
        step_title="Open PR",
        failure_category="dependency",
        failure_detail="  Provider unavailable.  ",
        item_type=PersonalReviewItemType.FAILED,
    )

    assert item.item_type is PersonalReviewItemType.FAILED
    assert item.related_resource == "execution_plan:plan-1:blocked"
    assert "Provider unavailable." in (item.detail or "")
    assert item.metadata["failure_category"] == "dependency"


@pytest.mark.asyncio
async def test_enqueue_overnight_summary_omits_blank_goal_and_summary() -> None:
    storage = AsyncMock()
    storage.get_review_item_by_related_resource = AsyncMock(return_value=None)
    storage.upsert_review_item = AsyncMock(side_effect=lambda item: item)
    inbox = OwnerReviewInbox(storage=storage)

    item = await inbox.enqueue_overnight_summary(
        user_id=42,
        tenant_id="tenant-1",
        plan_id="plan-2",
        plan_title="Ship worker",
        goal=None,
        summary="   ",
        status="completed",
    )

    assert item.related_resource == "execution_plan:plan-2:summary"
    assert "Goal:" not in (item.detail or "")
    assert "Latest output:" not in (item.detail or "")
