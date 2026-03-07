"""Unit tests for canonical trust persistence."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.trust.engine import (
    TrustDecision,
    TrustMode,
    TrustOutcome,
    TrustPrincipal,
    TrustResource,
    TrustRiskClass,
)
from zetherion_ai.trust.storage import (
    TrustDecisionAuditInput,
    TrustFeedbackEventInput,
    TrustGrantInput,
    TrustPolicyInput,
    TrustScorecardInput,
    TrustStorage,
    ensure_trust_storage_schema,
    normalize_grant_permissions,
    normalize_resource_scope,
)


@pytest.fixture
def mock_pool():
    pool = MagicMock()
    conn = AsyncMock()
    pool.acquire.return_value.__aenter__ = AsyncMock(return_value=conn)
    pool.acquire.return_value.__aexit__ = AsyncMock(return_value=None)
    return pool, conn


def test_normalize_resource_scope_accepts_supported_prefixes() -> None:
    assert normalize_resource_scope("tenant:*") == "tenant:*"
    assert normalize_resource_scope("owner_personal:gmail:type:acknowledgment") == (
        "owner_personal:gmail:type:acknowledgment"
    )
    assert normalize_resource_scope("repo:*") == "repo:*"
    assert normalize_resource_scope("messaging.chat:whatsapp:chat-1") == (
        "messaging.chat:whatsapp:chat-1"
    )
    assert normalize_resource_scope("worker_artifact:tenant-a:plan-1:step-1:retry-1") == (
        "worker_artifact:tenant-a:plan-1:step-1:retry-1"
    )


def test_normalize_resource_scope_rejects_unknown_prefix() -> None:
    with pytest.raises(ValueError, match="Unsupported trust resource scope"):
        normalize_resource_scope("unknown:thing")


def test_normalize_grant_permissions_rejects_empty_values() -> None:
    with pytest.raises(ValueError, match="At least one grant permission"):
        normalize_grant_permissions([])


@pytest.mark.asyncio
async def test_ensure_trust_storage_schema_bootstraps_tables(mock_pool) -> None:
    pool, conn = mock_pool

    tables = await ensure_trust_storage_schema(pool, schema="control_plane")

    conn.execute.assert_awaited_once()
    schema_sql = conn.execute.await_args.args[0]
    assert 'CREATE SCHEMA IF NOT EXISTS "control_plane"' in schema_sql
    assert 'CREATE TABLE IF NOT EXISTS "control_plane".trust_policies' in schema_sql
    assert tables == (
        "control_plane.trust_policies",
        "control_plane.trust_grants",
        "control_plane.trust_scorecards",
        "control_plane.trust_feedback_events",
        "control_plane.trust_decision_audit",
    )


@pytest.mark.asyncio
async def test_upsert_grant_normalizes_permissions_and_scope(mock_pool) -> None:
    pool, conn = mock_pool
    conn.fetchrow.return_value = {
        "grant_id": "55555555-5555-5555-5555-555555555555",
        "tenant_id": "tenant-a",
        "grantee_id": "node-1",
        "grantee_type": "worker_node",
        "resource_scope": "messaging.chat:whatsapp:chat-1",
        "permissions_json": ["read", "send"],
        "granted_by_id": "owner-1",
        "granted_by_type": "owner",
        "source_system": "worker_messaging_grant",
        "source_record_id": "legacy-grant-1",
        "metadata_json": {"redacted_payload": True},
        "issued_at": datetime.now(UTC),
        "expires_at": datetime.now(UTC) + timedelta(hours=1),
        "revoked_at": None,
        "revoke_reason": None,
    }

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    grant = await storage.upsert_grant(
        TrustGrantInput(
            tenant_id="tenant-a",
            grantee_id="node-1",
            grantee_type="worker_node",
            resource_scope="messaging.chat:whatsapp:chat-1",
            permissions=["send", "read", "read"],
            granted_by_id="owner-1",
            granted_by_type="owner",
            expires_at=datetime.now(UTC) + timedelta(hours=1),
            source_system="worker_messaging_grant",
            source_record_id="legacy-grant-1",
            metadata={"redacted_payload": True},
        )
    )

    assert grant.permissions == ["read", "send"]
    args = conn.fetchrow.await_args.args
    assert args[6] == '["read","send"]'


@pytest.mark.asyncio
async def test_record_shadow_decision_persists_canonical_decision(mock_pool) -> None:
    pool, conn = mock_pool
    now = datetime.now(UTC)
    conn.fetchrow.return_value = {
        "decision_id": "66666666-6666-6666-6666-666666666666",
        "tenant_id": "tenant-a",
        "adapter_name": "trust_policy",
        "principal_id": "tenant-a",
        "principal_type": "tenant",
        "resource_scope": "messaging.read",
        "action": "messaging.read",
        "outcome": "allow",
        "mode": "auto",
        "risk_class": "high",
        "reason_code": "AI_OK",
        "requires_two_person": False,
        "source_system": "shadow_engine",
        "trace_json": ["legacy_outcome=allow"],
        "metadata_json": {"status": 200},
        "created_at": now,
    }

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    audit = await storage.record_shadow_decision(
        TrustDecision(
            adapter_name="trust_policy",
            action="messaging.read",
            outcome=TrustOutcome.ALLOW,
            mode=TrustMode.AUTO,
            risk_class=TrustRiskClass.HIGH,
            reason_code="AI_OK",
            principal=TrustPrincipal(
                principal_id="tenant-a",
                principal_type="tenant",
                tenant_id="tenant-a",
            ),
            resource=TrustResource(
                resource_id="messaging.read",
                resource_type="trust_action",
                tenant_id="tenant-a",
            ),
            trace=("legacy_outcome=allow",),
            metadata={"status": 200},
        )
    )

    assert audit.adapter_name == "trust_policy"
    assert audit.trace == ("legacy_outcome=allow",)


@pytest.mark.asyncio
async def test_upsert_policy_normalizes_scope_and_metadata(mock_pool) -> None:
    pool, conn = mock_pool
    now = datetime.now(UTC)
    conn.fetchrow.return_value = {
        "policy_id": "11111111-1111-1111-1111-111111111111",
        "tenant_id": None,
        "principal_id": "owner-1",
        "principal_type": "owner",
        "resource_scope": "owner_personal:calendar:write",
        "action": "calendar.write",
        "mode": "ask",
        "risk_class": "moderate",
        "source_system": "personal_policy",
        "source_record_id": "policy-1",
        "metadata_json": {"domain": "calendar"},
        "created_at": now,
        "updated_at": now,
    }

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    policy = await storage.upsert_policy(
        TrustPolicyInput(
            principal_id="owner-1",
            principal_type="owner",
            resource_scope="owner_personal:calendar:write",
            action="calendar.write",
            mode="ask",
            risk_class="moderate",
            source_system="personal_policy",
            source_record_id="policy-1",
            metadata={"domain": "calendar"},
        )
    )

    assert policy.resource_scope == "owner_personal:calendar:write"
    args = conn.fetchrow.await_args.args
    assert args[5] == "owner_personal:calendar:write"
    assert args[11] == '{"domain":"calendar"}'


@pytest.mark.asyncio
async def test_revoke_grant_updates_existing_record(mock_pool) -> None:
    pool, conn = mock_pool
    revoked_at = datetime.now(UTC)
    conn.fetchrow.return_value = {
        "grant_id": "55555555-5555-5555-5555-555555555555",
        "tenant_id": "tenant-a",
        "grantee_id": "node-1",
        "grantee_type": "worker_node",
        "resource_scope": "messaging.chat:whatsapp:chat-1",
        "permissions_json": ["read"],
        "granted_by_id": "owner-1",
        "granted_by_type": "owner",
        "source_system": "worker_messaging_grant",
        "source_record_id": "legacy-grant-1",
        "metadata_json": {},
        "issued_at": datetime.now(UTC),
        "expires_at": None,
        "revoked_at": revoked_at,
        "revoke_reason": "expired",
    }

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    grant = await storage.revoke_grant(
        "55555555-5555-5555-5555-555555555555",
        revoke_reason="expired",
    )

    assert grant.revoked_at == revoked_at
    assert grant.revoke_reason == "expired"
    sql, grant_id, reason = conn.fetchrow.await_args.args
    assert 'UPDATE "control_plane".trust_grants' in sql
    assert grant_id == "55555555-5555-5555-5555-555555555555"
    assert reason == "expired"


@pytest.mark.asyncio
async def test_list_active_grants_applies_optional_filters(mock_pool) -> None:
    pool, conn = mock_pool
    conn.fetch.return_value = [
        {
            "grant_id": "55555555-5555-5555-5555-555555555555",
            "tenant_id": "tenant-a",
            "grantee_id": "node-1",
            "grantee_type": "worker_node",
            "resource_scope": "messaging.chat:whatsapp:chat-1",
            "permissions_json": ["read"],
            "granted_by_id": "owner-1",
            "granted_by_type": "owner",
            "source_system": "worker_messaging_grant",
            "source_record_id": "legacy-grant-1",
            "metadata_json": {},
            "issued_at": datetime.now(UTC),
            "expires_at": None,
            "revoked_at": None,
            "revoke_reason": None,
        }
    ]

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    grants = await storage.list_active_grants(
        grantee_id="node-1",
        grantee_type="worker_node",
        tenant_id="tenant-a",
        resource_scope_prefix="messaging.chat:whatsapp:*",
    )

    assert len(grants) == 1
    sql, *params = conn.fetch.await_args.args
    assert "tenant_id = $3" in sql
    assert "resource_scope LIKE $4" in sql
    assert params == ["node-1", "worker_node", "tenant-a", "messaging.chat:whatsapp:%"]


@pytest.mark.asyncio
async def test_record_feedback_event_persists_delta_and_metadata(mock_pool) -> None:
    pool, conn = mock_pool
    now = datetime.now(UTC)
    conn.fetchrow.return_value = {
        "event_id": "77777777-7777-7777-7777-777777777777",
        "tenant_id": "tenant-a",
        "subject_id": "owner-1",
        "subject_type": "owner",
        "resource_scope": "owner_personal:calendar:write",
        "action": "calendar.write",
        "outcome": "approved",
        "delta": 0.5,
        "source_system": "manual",
        "metadata_json": {"reason": "approved"},
        "created_at": now,
    }

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    event = await storage.record_feedback_event(
        TrustFeedbackEventInput(
            tenant_id="tenant-a",
            subject_id="owner-1",
            subject_type="owner",
            resource_scope="owner_personal:calendar:write",
            action="calendar.write",
            outcome="approved",
            delta=0.5,
            metadata={"reason": "approved"},
        )
    )

    assert event.delta == 0.5
    args = conn.fetchrow.await_args.args
    assert args[5] == "owner_personal:calendar:write"
    assert args[10] == '{"reason":"approved"}'


@pytest.mark.asyncio
async def test_record_decision_audit_persists_canonical_scope(mock_pool) -> None:
    pool, conn = mock_pool
    now = datetime.now(UTC)
    conn.fetchrow.return_value = {
        "decision_id": "88888888-8888-8888-8888-888888888888",
        "tenant_id": "tenant-a",
        "adapter_name": "github_autonomy",
        "principal_id": "owner-1",
        "principal_type": "owner",
        "resource_scope": "repo:*",
        "action": "github.pr.open",
        "outcome": "allow",
        "mode": "auto",
        "risk_class": "moderate",
        "reason_code": "ok",
        "requires_two_person": False,
        "source_system": "shadow_engine",
        "trace_json": ["matched"],
        "metadata_json": {"status": 200},
        "created_at": now,
    }

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    audit = await storage.record_decision_audit(
        TrustDecisionAuditInput(
            tenant_id="tenant-a",
            adapter_name="github_autonomy",
            principal_id="owner-1",
            principal_type="owner",
            resource_scope="repo:*",
            action="github.pr.open",
            outcome="allow",
            mode="auto",
            risk_class="moderate",
            reason_code="ok",
            trace=("matched",),
            metadata={"status": 200},
        )
    )

    assert audit.resource_scope == "repo:*"
    args = conn.fetchrow.await_args.args
    assert args[6] == "repo:*"
    assert args[14] == '["matched"]'


@pytest.mark.asyncio
async def test_record_shadow_decision_omits_noncanonical_resource_scope(mock_pool) -> None:
    pool, conn = mock_pool
    now = datetime.now(UTC)
    conn.fetchrow.return_value = {
        "decision_id": "99999999-9999-9999-9999-999999999999",
        "tenant_id": "tenant-a",
        "adapter_name": "trust_policy",
        "principal_id": "tenant-a",
        "principal_type": "tenant",
        "resource_scope": None,
        "action": "messaging.read",
        "outcome": "allow",
        "mode": "auto",
        "risk_class": "high",
        "reason_code": "AI_OK",
        "requires_two_person": False,
        "source_system": "shadow_engine",
        "trace_json": ["legacy_outcome=allow"],
        "metadata_json": {"status": 200},
        "created_at": now,
    }

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    await storage.record_shadow_decision(
        TrustDecision(
            adapter_name="trust_policy",
            action="messaging.read",
            outcome=TrustOutcome.ALLOW,
            mode=TrustMode.AUTO,
            risk_class=TrustRiskClass.HIGH,
            reason_code="AI_OK",
            principal=TrustPrincipal(
                principal_id="tenant-a",
                principal_type="tenant",
                tenant_id="tenant-a",
            ),
            resource=TrustResource(
                resource_id="messaging.read",
                resource_type="trust_action",
                tenant_id="tenant-a",
            ),
            trace=("legacy_outcome=allow",),
            metadata={"status": 200},
        )
    )

    args = conn.fetchrow.await_args.args
    assert args[6] is None


def test_normalize_resource_scope_rejects_blank_suffix() -> None:
    with pytest.raises(ValueError, match="Resource scope suffix is required"):
        normalize_resource_scope("owner_personal:")


def test_trust_storage_requires_initialize_before_use() -> None:
    storage = TrustStorage(schema="control_plane")

    with pytest.raises(RuntimeError, match="TrustStorage is not initialized"):
        storage._require_pool()


@pytest.mark.asyncio
async def test_ensure_trust_storage_schema_skips_invalid_pool_object() -> None:
    assert await ensure_trust_storage_schema(object(), schema="control_plane") == ()


@pytest.mark.asyncio
async def test_upsert_scorecard_persists_level_and_ceiling(mock_pool) -> None:
    pool, conn = mock_pool
    now = datetime.now(UTC)
    conn.fetchrow.return_value = {
        "scorecard_id": "12121212-1212-1212-1212-121212121212",
        "tenant_id": "tenant-a",
        "subject_id": "channel-1",
        "subject_type": "tenant_channel",
        "resource_scope": "tenant:tenant-a:youtube:channel:channel-1",
        "action": "youtube.reply.approve",
        "score": 0.8,
        "approvals": 8,
        "rejections": 1,
        "edits": 0,
        "total_interactions": 9,
        "level": "guided",
        "ceiling": 0.9,
        "source_system": "youtube_trust",
        "source_record_id": "tenant-a:channel-1",
        "metadata_json": {"trust_level": 2},
        "updated_at": now,
    }

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    scorecard = await storage.upsert_scorecard(
        TrustScorecardInput(
            tenant_id="tenant-a",
            subject_id="channel-1",
            subject_type="tenant_channel",
            resource_scope="tenant:tenant-a:youtube:channel:channel-1",
            action="youtube.reply.approve",
            score=0.8,
            approvals=8,
            rejections=1,
            total_interactions=9,
            level="guided",
            ceiling=0.9,
            source_system="youtube_trust",
            source_record_id="tenant-a:channel-1",
            metadata={"trust_level": 2},
        )
    )

    assert scorecard.level == "guided"
    assert scorecard.ceiling == 0.9
    args = conn.fetchrow.await_args.args
    assert args[5] == "tenant:tenant-a:youtube:channel:channel-1"
    assert args[16] == '{"trust_level":2}'


@pytest.mark.asyncio
async def test_get_scorecard_returns_latest_matching_record(mock_pool) -> None:
    pool, conn = mock_pool
    now = datetime.now(UTC)
    conn.fetchrow.return_value = {
        "scorecard_id": "13131313-1313-1313-1313-131313131313",
        "tenant_id": None,
        "subject_id": "owner-1",
        "subject_type": "owner",
        "resource_scope": "owner_personal:calendar:write",
        "action": "calendar.write",
        "score": 0.6,
        "approvals": 3,
        "rejections": 1,
        "edits": 1,
        "total_interactions": 5,
        "level": "draft",
        "ceiling": 0.9,
        "source_system": "review_inbox",
        "source_record_id": "owner:owner:owner-1:owner_personal:calendar:write:calendar.write",
        "metadata_json": {"domain": "calendar"},
        "updated_at": now,
    }

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    scorecard = await storage.get_scorecard(
        subject_id="owner-1",
        subject_type="owner",
        resource_scope="owner_personal:calendar:write",
        action="calendar.write",
    )

    assert scorecard is not None
    assert scorecard.level == "draft"
    sql, *params = conn.fetchrow.await_args.args
    assert 'FROM "control_plane".trust_scorecards' in sql
    assert params == ["owner-1", "owner", "owner_personal:calendar:write", "calendar.write"]


@pytest.mark.asyncio
async def test_record_feedback_outcome_updates_existing_scorecard(mock_pool) -> None:
    pool, conn = mock_pool
    now = datetime.now(UTC)
    conn.fetchrow.side_effect = [
        {
            "scorecard_id": "14141414-1414-1414-1414-141414141414",
            "tenant_id": None,
            "subject_id": "owner-1",
            "subject_type": "owner",
            "resource_scope": "owner_personal:calendar:write",
            "action": "calendar.write",
            "score": 0.6,
            "approvals": 3,
            "rejections": 1,
            "edits": 1,
            "total_interactions": 5,
            "level": "draft",
            "ceiling": 0.9,
            "source_system": "review_inbox",
            "source_record_id": "existing-scorecard",
            "metadata_json": {"domain": "calendar"},
            "updated_at": now,
        },
        {
            "scorecard_id": "14141414-1414-1414-1414-141414141414",
            "tenant_id": None,
            "subject_id": "owner-1",
            "subject_type": "owner",
            "resource_scope": "owner_personal:calendar:write",
            "action": "calendar.write",
            "score": 0.65,
            "approvals": 4,
            "rejections": 1,
            "edits": 1,
            "total_interactions": 6,
            "level": "draft",
            "ceiling": 0.9,
            "source_system": "review_inbox",
            "source_record_id": "existing-scorecard",
            "metadata_json": {"domain": "calendar", "review_item_id": 11},
            "updated_at": now,
        },
        {
            "event_id": "15151515-1515-1515-1515-151515151515",
            "tenant_id": None,
            "subject_id": "owner-1",
            "subject_type": "owner",
            "resource_scope": "owner_personal:calendar:write",
            "action": "calendar.write",
            "outcome": "approved",
            "delta": 0.05,
            "source_system": "review_inbox",
            "metadata_json": {"review_item_id": 11, "score_after": 0.65},
            "created_at": now,
        },
    ]

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    event, scorecard = await storage.record_feedback_outcome(
        subject_id="owner-1",
        subject_type="owner",
        resource_scope="owner_personal:calendar:write",
        action="calendar.write",
        outcome="approved",
        metadata={"review_item_id": 11},
    )

    assert event.outcome == "approved"
    assert scorecard.score == 0.65
    update_call = conn.fetchrow.await_args_list[1]
    update_sql, *update_args = update_call.args
    assert 'UPDATE "control_plane".trust_scorecards' in update_sql
    assert update_args[1:7] == [0.65, 4, 1, 1, 6, "draft"]


@pytest.mark.asyncio
async def test_get_scorecard_applies_tenant_filter(mock_pool) -> None:
    pool, conn = mock_pool
    now = datetime.now(UTC)
    conn.fetchrow.return_value = {
        "scorecard_id": "16161616-1616-1616-1616-161616161616",
        "tenant_id": "tenant-a",
        "subject_id": "worker-1",
        "subject_type": "worker_node",
        "resource_scope": "repo:allowed",
        "action": "repo.patch",
        "score": 0.55,
        "approvals": 1,
        "rejections": 0,
        "edits": 0,
        "total_interactions": 1,
        "level": "ask",
        "ceiling": None,
        "source_system": "worker_policy",
        "source_record_id": "worker-scorecard",
        "metadata_json": {},
        "updated_at": now,
    }

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    scorecard = await storage.get_scorecard(
        subject_id="worker-1",
        subject_type="worker_node",
        resource_scope="repo:allowed",
        action="repo.patch",
        tenant_id="tenant-a",
    )

    assert scorecard is not None
    sql, *params = conn.fetchrow.await_args.args
    assert "tenant_id = $5" in sql
    assert params == ["worker-1", "worker_node", "repo:allowed", "repo.patch", "tenant-a"]


@pytest.mark.asyncio
async def test_record_feedback_outcome_creates_new_scorecard(mock_pool) -> None:
    pool, conn = mock_pool
    now = datetime.now(UTC)
    conn.fetchrow.side_effect = [
        None,
        {
            "scorecard_id": "17171717-1717-1717-1717-171717171717",
            "tenant_id": "tenant-a",
            "subject_id": "worker-1",
            "subject_type": "worker_node",
            "resource_scope": "repo:allowed",
            "action": "repo.patch",
            "score": 0.05,
            "approvals": 1,
            "rejections": 0,
            "edits": 0,
            "total_interactions": 1,
            "level": "review",
            "ceiling": 0.5,
            "source_system": "review_inbox",
            "source_record_id": "tenant-a:worker_node:worker-1:repo:allowed:repo.patch",
            "metadata_json": {"review_item_id": 99},
            "updated_at": now,
        },
        {
            "event_id": "18181818-1818-1818-1818-181818181818",
            "tenant_id": "tenant-a",
            "subject_id": "worker-1",
            "subject_type": "worker_node",
            "resource_scope": "repo:allowed",
            "action": "repo.patch",
            "outcome": "approved",
            "delta": 0.05,
            "source_system": "review_inbox",
            "metadata_json": {"review_item_id": 99, "score_after": 0.05},
            "created_at": now,
        },
    ]

    storage = TrustStorage(schema="control_plane")
    storage._pool = pool  # type: ignore[attr-defined]
    event, scorecard = await storage.record_feedback_outcome(
        subject_id="worker-1",
        subject_type="worker_node",
        resource_scope="repo:allowed",
        action="repo.patch",
        outcome="approved",
        tenant_id="tenant-a",
        metadata={"review_item_id": 99},
        ceiling=0.5,
    )

    assert event.delta == 0.05
    assert scorecard.total_interactions == 1
    insert_call = conn.fetchrow.await_args_list[1]
    insert_sql, *insert_args = insert_call.args
    assert 'INSERT INTO "control_plane".trust_scorecards' in insert_sql
    assert insert_args[2] == "worker-1"
    assert insert_args[5] == "repo.patch"
    assert insert_args[12] == 0.5
