"""Unit tests for legacy-to-canonical trust backfill mapping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.personal.models import PersonalPolicy, PolicyDomain, PolicyMode
from zetherion_ai.skills.github.models import ActionType, AutonomyConfig, AutonomyLevel
from zetherion_ai.skills.gmail.trust import TrustScore
from zetherion_ai.trust.backfill import TrustBackfillService
from zetherion_ai.trust.storage import (
    TrustGrantRecord,
    TrustPolicyRecord,
    TrustScorecardRecord,
)


@pytest.mark.asyncio
async def test_backfill_personal_policy_writes_policy_and_scorecard() -> None:
    storage = AsyncMock()
    storage.upsert_policy = AsyncMock(
        return_value=TrustPolicyRecord(
            policy_id="policy-1",
            principal_id="42",
            principal_type="owner",
            tenant_id=None,
            resource_scope="owner_personal:email:auto_reply_ack",
            action="auto_reply_ack",
            mode="draft",
            risk_class="high",
        )
    )
    storage.upsert_scorecard = AsyncMock(
        return_value=TrustScorecardRecord(
            scorecard_id="score-1",
            subject_id="42",
            subject_type="owner",
            tenant_id=None,
            resource_scope="owner_personal:email:auto_reply_ack",
            action="auto_reply_ack",
            score=0.6,
        )
    )
    service = TrustBackfillService(storage)

    policy = PersonalPolicy(
        id=7,
        user_id=42,
        domain=PolicyDomain.EMAIL,
        action="auto_reply_ack",
        mode=PolicyMode.DRAFT,
        trust_score=0.6,
    )

    stored_policy, scorecard = await service.backfill_personal_policy(policy)

    assert stored_policy.mode == "draft"
    assert scorecard.score == 0.6
    policy_input = storage.upsert_policy.await_args.args[0]
    assert policy_input.resource_scope == "owner_personal:email:auto_reply_ack"
    assert policy_input.source_record_id == "7"
    scorecard_input = storage.upsert_scorecard.await_args.args[0]
    assert scorecard_input.source_record_id == "score:7"


@pytest.mark.asyncio
async def test_backfill_github_autonomy_maps_dangerous_actions_to_review() -> None:
    captured_inputs = []

    async def _upsert_policy(input_record):
        captured_inputs.append(input_record)
        return TrustPolicyRecord(
            policy_id=f"policy-{input_record.action}",
            principal_id=input_record.principal_id,
            principal_type=input_record.principal_type,
            tenant_id=input_record.tenant_id,
            resource_scope=input_record.resource_scope,
            action=input_record.action,
            mode=input_record.mode,
            risk_class=input_record.risk_class,
        )

    storage = AsyncMock()
    storage.upsert_policy = AsyncMock(side_effect=_upsert_policy)
    service = TrustBackfillService(storage)

    config = AutonomyConfig()
    config.set_level(ActionType.CLOSE_ISSUE, AutonomyLevel.AUTONOMOUS)

    records = await service.backfill_github_autonomy(principal_id="42", config=config)

    assert len(records) == len(ActionType)
    dangerous = next(item for item in captured_inputs if item.action == "github.delete_repo")
    assert dangerous.mode == "review"
    assert dangerous.risk_class == "critical"
    close_issue = next(item for item in captured_inputs if item.action == "github.close_issue")
    assert close_issue.mode == "auto"
    assert close_issue.risk_class == "high"


@pytest.mark.asyncio
async def test_backfill_worker_messaging_grant_maps_permissions_and_scope() -> None:
    storage = AsyncMock()
    storage.upsert_grant = AsyncMock(
        return_value=TrustGrantRecord(
            grant_id="grant-1",
            tenant_id="tenant-a",
            grantee_id="node-1",
            grantee_type="worker_node",
            resource_scope="messaging.chat:whatsapp:chat-1",
            permissions=["read", "send"],
            issued_at=datetime.now(UTC),
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
    )
    service = TrustBackfillService(storage)

    record = await service.backfill_worker_messaging_grant(
        {
            "grant_id": "legacy-grant-1",
            "tenant_id": "tenant-a",
            "node_id": "node-1",
            "provider": "whatsapp",
            "chat_id": "chat-1",
            "allow_read": True,
            "allow_send": True,
            "redacted_payload": True,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "created_by": "owner-1",
        }
    )

    assert record.permissions == ["read", "send"]
    grant_input = storage.upsert_grant.await_args.args[0]
    assert grant_input.resource_scope == "messaging.chat:whatsapp:chat-1"
    assert grant_input.permissions == ["read", "send"]
    assert grant_input.metadata["redacted_payload"] is True


@pytest.mark.asyncio
async def test_backfill_gmail_contact_trust_keeps_score_metrics() -> None:
    storage = AsyncMock()
    storage.upsert_scorecard = AsyncMock(
        return_value=TrustScorecardRecord(
            scorecard_id="score-1",
            subject_id="42",
            subject_type="owner",
            tenant_id=None,
            resource_scope="owner_personal:gmail:contact:test@example.com",
            action="gmail.reply.send",
            score=0.9,
            approvals=9,
            rejections=1,
            edits=2,
            total_interactions=12,
        )
    )
    service = TrustBackfillService(storage)

    scorecard = await service.backfill_gmail_contact_trust(
        user_id=42,
        contact_email="Test@Example.com",
        trust_score=TrustScore(
            score=0.9, approvals=9, rejections=1, edits=2, total_interactions=12
        ),
    )

    assert scorecard.total_interactions == 12
    scorecard_input = storage.upsert_scorecard.await_args.args[0]
    assert scorecard_input.resource_scope == "owner_personal:gmail:contact:Test@Example.com"
    assert scorecard_input.source_record_id == "42:test@example.com"
