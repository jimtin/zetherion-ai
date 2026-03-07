"""Canonical owner review inbox and trust-feedback loop."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.personal.models import (
    PersonalReviewItem,
    PersonalReviewItemStatus,
    PersonalReviewItemType,
)
from zetherion_ai.personal.operational_storage import OwnerPersonalIntelligenceStorage
from zetherion_ai.trust.storage import TrustFeedbackEventRecord, TrustScorecardRecord, TrustStorage

log = get_logger("zetherion_ai.personal.review_inbox")

_TRUST_FEEDBACK_TARGET_KEY = "trust_feedback_target"
_MAX_DETAIL_LENGTH = 4000
_MAX_TITLE_LENGTH = 200


class ReviewFeedbackOutcome(StrEnum):
    """Canonical outcomes for owner review feedback."""

    APPROVED = "approved"
    MINOR_EDIT = "minor_edit"
    MAJOR_EDIT = "major_edit"
    REJECTED = "rejected"
    ESCALATED = "escalated"


@dataclass(frozen=True)
class ReviewTrustFeedbackTarget:
    """Canonical trust target stored alongside a review item."""

    subject_id: str
    subject_type: str
    resource_scope: str
    action: str
    tenant_id: str | None = None
    metadata: dict[str, Any] | None = None

    def to_metadata(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "subject_id": self.subject_id,
            "subject_type": self.subject_type,
            "resource_scope": self.resource_scope,
            "action": self.action,
        }
        if self.tenant_id is not None:
            payload["tenant_id"] = self.tenant_id
        if self.metadata:
            payload["metadata"] = dict(self.metadata)
        return payload

    @classmethod
    def from_metadata(cls, metadata: dict[str, Any] | None) -> ReviewTrustFeedbackTarget | None:
        if not isinstance(metadata, dict):
            return None
        subject_id = str(metadata.get("subject_id") or "").strip()
        subject_type = str(metadata.get("subject_type") or "").strip()
        resource_scope = str(metadata.get("resource_scope") or "").strip()
        action = str(metadata.get("action") or "").strip()
        if not all((subject_id, subject_type, resource_scope, action)):
            return None
        tenant_id = metadata.get("tenant_id")
        raw_nested = metadata.get("metadata")
        nested = dict(raw_nested) if isinstance(raw_nested, dict) else None
        return cls(
            subject_id=subject_id,
            subject_type=subject_type,
            resource_scope=resource_scope,
            action=action,
            tenant_id=str(tenant_id).strip() if tenant_id is not None else None,
            metadata=nested,
        )


@dataclass(frozen=True)
class ReviewResolutionResult:
    """Resolved review item plus any canonical trust updates."""

    item: PersonalReviewItem
    trust_event: TrustFeedbackEventRecord | None = None
    trust_scorecard: TrustScorecardRecord | None = None


class OwnerReviewInbox:
    """Owner-facing canonical review queue backed by owner-personal storage."""

    def __init__(
        self,
        *,
        storage: OwnerPersonalIntelligenceStorage,
        trust_storage: TrustStorage | None = None,
        source_system: str = "review_inbox",
    ) -> None:
        self._storage = storage
        self._trust_storage = trust_storage
        self._source_system = str(source_system or "review_inbox").strip() or "review_inbox"

    async def enqueue_item(
        self,
        item: PersonalReviewItem,
        *,
        feedback_target: ReviewTrustFeedbackTarget | None = None,
        dedupe_related_resource: bool = True,
    ) -> PersonalReviewItem:
        """Insert or update one canonical review item."""

        metadata = dict(item.metadata)
        if feedback_target is not None:
            metadata[_TRUST_FEEDBACK_TARGET_KEY] = feedback_target.to_metadata()

        payload = item.model_dump()
        payload["metadata"] = metadata

        if dedupe_related_resource and item.related_resource:
            existing = await self._storage.get_review_item_by_related_resource(
                item.user_id,
                item.related_resource,
                source=item.source,
                pending_only=True,
            )
            if existing is not None:
                payload["id"] = existing.id
                payload["status"] = existing.status
                payload["created_at"] = existing.created_at

        return await self._storage.upsert_review_item(PersonalReviewItem(**payload))

    async def list_pending_items(
        self, user_id: int, *, limit: int = 25
    ) -> list[PersonalReviewItem]:
        """List pending owner review items in canonical priority order."""
        return await self._storage.list_review_items(user_id, pending_only=True, limit=limit)

    async def resolve_item(
        self,
        review_item_id: int,
        *,
        user_id: int,
        status: PersonalReviewItemStatus = PersonalReviewItemStatus.RESOLVED,
        feedback_outcome: ReviewFeedbackOutcome | None = None,
        feedback_metadata: dict[str, Any] | None = None,
    ) -> ReviewResolutionResult | None:
        """Resolve one review item and optionally apply trust feedback."""

        item = await self._storage.resolve_review_item(
            review_item_id, user_id=user_id, status=status
        )
        if item is None:
            return None

        trust_event = None
        trust_scorecard = None
        if feedback_outcome is not None and self._trust_storage is not None:
            target = ReviewTrustFeedbackTarget.from_metadata(
                item.metadata.get(_TRUST_FEEDBACK_TARGET_KEY)
                if isinstance(item.metadata, dict)
                else None
            )
            if target is not None:
                event_metadata = dict(target.metadata or {})
                if feedback_metadata:
                    event_metadata.update(feedback_metadata)
                event_metadata.update(
                    {
                        "review_item_id": item.id,
                        "review_item_type": item.item_type.value,
                        "review_item_status": item.status.value,
                        "resolved_at": (
                            item.resolved_at.astimezone(UTC).isoformat()
                            if item.resolved_at is not None
                            else datetime.now(UTC).isoformat()
                        ),
                    }
                )
                trust_event, trust_scorecard = await self._trust_storage.record_feedback_outcome(
                    subject_id=target.subject_id,
                    subject_type=target.subject_type,
                    resource_scope=target.resource_scope,
                    action=target.action,
                    outcome=feedback_outcome.value,
                    tenant_id=target.tenant_id,
                    source_system=self._source_system,
                    metadata=event_metadata,
                )
        return ReviewResolutionResult(
            item=item,
            trust_event=trust_event,
            trust_scorecard=trust_scorecard,
        )

    async def enqueue_overnight_summary(
        self,
        *,
        user_id: int,
        tenant_id: str,
        plan_id: str,
        plan_title: str,
        goal: str | None,
        summary: str,
        step_id: str | None = None,
        step_index: int | None = None,
        total_steps: int | None = None,
        status: str = "completed",
    ) -> PersonalReviewItem:
        """Create or update the canonical overnight summary item for one plan."""

        detail_parts = [
            f"Plan: {_compact_text(plan_title, fallback=plan_id)}",
            f"Status: {_compact_text(status, fallback='completed')}",
        ]
        goal_text = _compact_text(goal)
        if goal_text:
            detail_parts.append(f"Goal: {goal_text}")
        if step_index is not None and total_steps is not None:
            detail_parts.append(f"Completed step: {step_index + 1}/{total_steps}")
        summary_text = _normalize_detail(summary)
        if summary_text:
            detail_parts.extend(("", "Latest output:", summary_text))

        return await self.enqueue_item(
            PersonalReviewItem(
                user_id=user_id,
                item_type=PersonalReviewItemType.OVERNIGHT_SUMMARY,
                title=_compact_text(f"Overnight summary: {plan_title}", fallback=f"Plan {plan_id}"),
                detail="\n".join(detail_parts).strip(),
                status=PersonalReviewItemStatus.PENDING,
                source="plan_executor",
                related_resource=f"execution_plan:{plan_id}:summary",
                priority=35,
                metadata={
                    "tenant_id": tenant_id,
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "step_index": step_index,
                    "total_steps": total_steps,
                    "status": status,
                },
            )
        )

    async def enqueue_execution_failure(
        self,
        *,
        user_id: int,
        tenant_id: str,
        plan_id: str,
        plan_title: str,
        step_id: str | None,
        step_title: str,
        failure_category: str,
        failure_detail: str,
        item_type: PersonalReviewItemType = PersonalReviewItemType.BLOCKED,
    ) -> PersonalReviewItem:
        """Create or update the canonical failed/blocked execution review item."""

        detail = "\n".join(
            part
            for part in (
                f"Plan: {_compact_text(plan_title, fallback=plan_id)}",
                f"Step: {_compact_text(step_title, fallback='unnamed step')}",
                f"Failure category: {_compact_text(failure_category, fallback='transient')}",
                "",
                _normalize_detail(failure_detail),
            )
            if part is not None
        ).strip()
        related_resource = (
            f"execution_plan:{plan_id}:step:{step_id}:blocked"
            if step_id
            else f"execution_plan:{plan_id}:blocked"
        )

        return await self.enqueue_item(
            PersonalReviewItem(
                user_id=user_id,
                item_type=item_type,
                title=_compact_text(
                    f"Plan blocked: {step_title}", fallback=f"Blocked plan {plan_id}"
                ),
                detail=detail,
                status=PersonalReviewItemStatus.PENDING,
                source="plan_executor",
                related_resource=related_resource,
                priority=90,
                metadata={
                    "tenant_id": tenant_id,
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "failure_category": _compact_text(failure_category, fallback="transient"),
                },
            )
        )


def _compact_text(value: str | None, *, fallback: str | None = None) -> str:
    raw = str(value or fallback or "").strip()
    if not raw:
        return ""
    compacted = " ".join(raw.split())
    return compacted[:_MAX_TITLE_LENGTH]


def _normalize_detail(value: str | None) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    return text[:_MAX_DETAIL_LENGTH]
