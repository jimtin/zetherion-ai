"""Unit tests for legacy-to-canonical trust backfill mapping."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.personal.models import PersonalPolicy, PolicyDomain, PolicyMode
from zetherion_ai.skills.github.models import ActionType, AutonomyConfig, AutonomyLevel
from zetherion_ai.skills.gmail.replies import ReplyType
from zetherion_ai.skills.gmail.trust import TrustScore
from zetherion_ai.trust.backfill import (
    TrustBackfillService,
    _map_github_mode,
    _map_github_risk,
    _map_personal_mode,
    _map_personal_risk,
)
from zetherion_ai.trust.engine import TrustMode, TrustRiskClass
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
async def test_backfill_personal_policy_without_id_uses_fallback_source_ids() -> None:
    storage = AsyncMock()
    storage.upsert_policy = AsyncMock(
        return_value=TrustPolicyRecord(
            policy_id="policy-2",
            principal_id="42",
            principal_type="owner",
            tenant_id=None,
            resource_scope="owner_personal:calendar:review_request",
            action="review_request",
            mode="block",
            risk_class="moderate",
        )
    )
    storage.upsert_scorecard = AsyncMock(
        return_value=TrustScorecardRecord(
            scorecard_id="score-2",
            subject_id="42",
            subject_type="owner",
            tenant_id=None,
            resource_scope="owner_personal:calendar:review_request",
            action="review_request",
            score=0.1,
        )
    )
    service = TrustBackfillService(storage)

    policy = PersonalPolicy(
        id=None,
        user_id=42,
        domain=PolicyDomain.CALENDAR,
        action="review_request",
        mode=PolicyMode.NEVER,
        trust_score=0.1,
    )

    await service.backfill_personal_policy(policy)

    policy_input = storage.upsert_policy.await_args.args[0]
    assert policy_input.source_record_id == "42:calendar:review_request"
    assert policy_input.mode == "block"
    scorecard_input = storage.upsert_scorecard.await_args.args[0]
    assert scorecard_input.source_record_id == "score:42:calendar:review_request"


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
            permissions=["read", "draft"],
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
            "allow_draft": True,
            "allow_send": False,
            "redacted_payload": True,
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "created_by": "owner-1",
        }
    )

    assert record.permissions == ["read", "draft"]
    grant_input = storage.upsert_grant.await_args.args[0]
    assert grant_input.resource_scope == "messaging.chat:whatsapp:chat-1"
    assert grant_input.permissions == ["read", "draft"]
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


@pytest.mark.asyncio
async def test_backfill_gmail_type_trust_keeps_reply_type_metadata() -> None:
    storage = AsyncMock()
    storage.upsert_scorecard = AsyncMock(
        return_value=TrustScorecardRecord(
            scorecard_id="score-3",
            subject_id="7",
            subject_type="owner",
            tenant_id=None,
            resource_scope="owner_personal:gmail:type:follow_up",
            action="gmail.reply.send",
            score=0.75,
            approvals=3,
            rejections=1,
            edits=1,
            total_interactions=5,
        )
    )
    service = TrustBackfillService(storage)

    await service.backfill_gmail_type_trust(
        user_id=7,
        reply_type=ReplyType.TASK_UPDATE,
        trust_score=TrustScore(
            score=0.75,
            approvals=3,
            rejections=1,
            edits=1,
            total_interactions=5,
        ),
    )

    scorecard_input = storage.upsert_scorecard.await_args.args[0]
    assert scorecard_input.resource_scope == "owner_personal:gmail:type:task_update"
    assert scorecard_input.metadata["reply_type"] == "task_update"
    assert scorecard_input.source_record_id == "7:task_update"


@pytest.mark.asyncio
async def test_backfill_youtube_trust_handles_zero_total_and_level_metadata() -> None:
    storage = AsyncMock()
    storage.upsert_scorecard = AsyncMock(
        return_value=TrustScorecardRecord(
            scorecard_id="score-4",
            subject_id="channel-1",
            subject_type="tenant_channel",
            tenant_id="tenant-a",
            resource_scope="tenant:tenant-a:youtube:channel:channel-1",
            action="youtube.reply.approve",
            score=0.0,
            level="high",
        )
    )
    service = TrustBackfillService(storage)

    await service.backfill_youtube_trust(
        tenant_id="tenant-a",
        channel_id="channel-1",
        trust_level=2,
        trust_stats={"approved": 0, "rejected": 2, "total": 0},
    )

    scorecard_input = storage.upsert_scorecard.await_args.args[0]
    assert scorecard_input.score == 0.0
    assert scorecard_input.level == "autonomous"
    assert scorecard_input.metadata["trust_stats"]["rejected"] == 2


@pytest.mark.asyncio
async def test_backfill_worker_messaging_grant_defaults_blank_fields_and_send_permission() -> None:
    storage = AsyncMock()
    storage.upsert_grant = AsyncMock(
        return_value=TrustGrantRecord(
            grant_id="grant-2",
            tenant_id=None,
            grantee_id="node-2",
            grantee_type="worker_node",
            resource_scope="messaging.chat:discord:dm-1",
            permissions=["send"],
            issued_at=datetime.now(UTC),
        )
    )
    service = TrustBackfillService(storage)

    await service.backfill_worker_messaging_grant(
        {
            "grant_id": "legacy-grant-2",
            "tenant_id": "   ",
            "node_id": "node-2",
            "provider": "discord",
            "chat_id": "dm-1",
            "allow_send": True,
            "updated_by": "owner-2",
        }
    )

    grant_input = storage.upsert_grant.await_args.args[0]
    assert grant_input.permissions == ["send"]
    assert grant_input.tenant_id is None
    assert grant_input.granted_by_id == "owner-2"
    assert grant_input.metadata["provider"] == "discord"


@pytest.mark.parametrize(
    ("mode", "expected"),
    [
        (PolicyMode.AUTO, TrustMode.AUTO),
        (PolicyMode.DRAFT, TrustMode.DRAFT),
        (PolicyMode.NEVER, TrustMode.BLOCK),
        (PolicyMode.ASK, TrustMode.ASK),
    ],
)
def test_map_personal_mode_covers_all_branches(mode: PolicyMode, expected: TrustMode) -> None:
    assert _map_personal_mode(mode) == expected


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        ("send_email", TrustRiskClass.HIGH),
        ("archive_item", TrustRiskClass.MODERATE),
    ],
)
def test_map_personal_risk_distinguishes_high_and_moderate(
    action: str,
    expected: TrustRiskClass,
) -> None:
    assert _map_personal_risk(action) == expected


@pytest.mark.parametrize(
    ("level", "expected"),
    [
        (AutonomyLevel.AUTONOMOUS, TrustMode.AUTO),
        (AutonomyLevel.ALWAYS_ASK, TrustMode.REVIEW),
        (AutonomyLevel.ASK, TrustMode.ASK),
    ],
)
def test_map_github_mode_covers_all_branches(level: AutonomyLevel, expected: TrustMode) -> None:
    assert _map_github_mode(level) == expected


@pytest.mark.parametrize(
    ("action", "expected"),
    [
        (ActionType.DELETE_REPO, TrustRiskClass.CRITICAL),
        (ActionType.CREATE_PR, TrustRiskClass.HIGH),
        (ActionType.ADD_COMMENT, TrustRiskClass.MODERATE),
        (ActionType.LIST_PRS, TrustRiskClass.LOW),
    ],
)
def test_map_github_risk_covers_critical_high_moderate_and_low(
    action: ActionType,
    expected: TrustRiskClass,
) -> None:
    assert _map_github_risk(action) == expected
