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
    TrustGrantInput,
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
