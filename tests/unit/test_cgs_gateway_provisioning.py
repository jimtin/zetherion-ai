"""Unit tests for CGS tenant provisioning orchestration."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.cgs_gateway.provisioning import CGSTenantProvisioningOrchestrator


@pytest.mark.asyncio
async def test_provision_tenant_creates_new_mapping_with_defaults() -> None:
    storage = MagicMock()
    storage.get_tenant_mapping = AsyncMock(return_value=None)
    storage.upsert_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "Tenant A",
            "domain": "tenant-a.example",
            "key_version": 1,
            "isolation_stage": "legacy",
            "metadata": {},
        }
    )
    skills_client = MagicMock()
    skills_client.handle_intent = AsyncMock(
        return_value=(
            200,
            {
                "success": True,
                "data": {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "api_key": "sk_live_new",
                },
            },
        )
    )

    orchestrator = CGSTenantProvisioningOrchestrator(storage=storage, skills_client=skills_client)
    mapping, api_key, created = await orchestrator.provision_tenant(
        cgs_tenant_id="tenant-a",
        name="Tenant A",
        domain="tenant-a.example",
        config={"tone": "formal"},
        user_id="operator-1",
        request_id="req-1",
    )

    assert created is True
    assert api_key == "sk_live_new"
    assert mapping["isolation_stage"] == "legacy"
    skills_client.handle_intent.assert_awaited_once()
    upsert_kwargs = storage.upsert_tenant_mapping.await_args.kwargs
    assert upsert_kwargs["isolation_stage"] == "legacy"
    assert upsert_kwargs["metadata"]["config"] == {"tone": "formal"}
    provisioning = upsert_kwargs["metadata"]["provisioning"]
    assert provisioning["baseline_version"] == 1
    assert provisioning["default_trust_policy_seeded"] is True
    assert provisioning["owner_portfolio_ready"] is False


@pytest.mark.asyncio
async def test_provision_tenant_is_idempotent_for_existing_mapping() -> None:
    storage = MagicMock()
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "Tenant A",
            "domain": "tenant-a.example",
            "zetherion_api_key": "sk_live_existing",
            "key_version": 3,
            "isolation_stage": "shadow",
            "metadata": {"provisioning": {"owner_portfolio_ready": True}},
        }
    )
    storage.update_tenant_profile = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "Tenant A",
            "domain": "tenant-a.example",
            "key_version": 3,
            "isolation_stage": "shadow",
            "metadata": {"provisioning": {"owner_portfolio_ready": True}},
        }
    )
    skills_client = MagicMock()
    skills_client.handle_intent = AsyncMock()

    orchestrator = CGSTenantProvisioningOrchestrator(storage=storage, skills_client=skills_client)
    mapping, api_key, created = await orchestrator.provision_tenant(
        cgs_tenant_id="tenant-a",
        name="Tenant A",
        domain="tenant-a.example",
        config=None,
        user_id="operator-1",
        request_id="req-1",
    )

    assert created is False
    assert api_key == "sk_live_existing"
    assert mapping["isolation_stage"] == "shadow"
    skills_client.handle_intent.assert_not_awaited()
    update_kwargs = storage.update_tenant_profile.await_args.kwargs
    assert update_kwargs["isolation_stage"] == "shadow"
    assert update_kwargs["metadata"]["provisioning"]["owner_portfolio_ready"] is True


@pytest.mark.asyncio
async def test_reconcile_tenant_flags_issues_and_updates_upstream() -> None:
    storage = MagicMock()
    storage.get_tenant_mapping = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "Tenant A",
            "domain": "tenant-a.example",
            "zetherion_api_key": "sk_live_existing",
            "key_version": 1,
            "isolation_stage": "legacy",
            "metadata": {"config": {"tone": "formal"}},
        }
    )
    storage.update_tenant_profile = AsyncMock(
        return_value={
            "cgs_tenant_id": "tenant-a",
            "zetherion_tenant_id": "11111111-1111-1111-1111-111111111111",
            "name": "Tenant A2",
            "domain": "tenant-a.example",
            "key_version": 1,
            "isolation_stage": "shadow",
            "metadata": {},
        }
    )
    skills_client = MagicMock()
    skills_client.handle_intent = AsyncMock(return_value=(200, {"success": True}))

    orchestrator = CGSTenantProvisioningOrchestrator(storage=storage, skills_client=skills_client)
    updated = await orchestrator.reconcile_tenant(
        cgs_tenant_id="tenant-a",
        user_id="operator-1",
        request_id="req-1",
        desired_isolation_stage="shadow",
        expected_key_version=2,
        owner_portfolio_ready=False,
        name="Tenant A2",
        config={"tone": "friendly"},
    )

    assert updated is not None
    assert updated["reconciliation_issues"] == [
        "unmigrated_isolation_stage",
        "isolation_stage_update:legacy->shadow",
        "stale_key_version",
        "missing_owner_portfolio_dataset",
    ]
    skills_client.handle_intent.assert_awaited_once()
    assert skills_client.handle_intent.await_args.kwargs["intent"] == "client_configure"
    update_kwargs = storage.update_tenant_profile.await_args.kwargs
    assert update_kwargs["isolation_stage"] == "shadow"
    assert update_kwargs["metadata"]["config"] == {"tone": "friendly"}
    assert update_kwargs["metadata"]["provisioning"]["owner_portfolio_ready"] is False


@pytest.mark.asyncio
async def test_list_reconciliation_candidates_passthrough() -> None:
    storage = MagicMock()
    storage.list_tenant_reconciliation_candidates = AsyncMock(
        return_value=[{"cgs_tenant_id": "tenant-a"}]
    )
    orchestrator = CGSTenantProvisioningOrchestrator(storage=storage, skills_client=MagicMock())

    candidates = await orchestrator.list_reconciliation_candidates()

    assert candidates == [{"cgs_tenant_id": "tenant-a"}]
    storage.list_tenant_reconciliation_candidates.assert_awaited_once()
