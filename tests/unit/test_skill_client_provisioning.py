"""Tests for client_provisioning skill."""

from __future__ import annotations

from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.client_provisioning import ClientProvisioningSkill

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_tenant_manager() -> AsyncMock:
    """Return an AsyncMock TenantManager with sensible defaults."""
    tm = AsyncMock()
    tm.create_tenant = AsyncMock(
        return_value=(
            {
                "tenant_id": uuid4(),
                "name": "Bob's Plumbing",
                "domain": "bobsplumbing.com",
                "is_active": True,
                "rate_limit_rpm": 60,
                "config": {},
            },
            "sk_live_test_key_1234",
        )
    )
    tm.get_tenant = AsyncMock(
        return_value={
            "tenant_id": uuid4(),
            "name": "Bob's Plumbing",
            "domain": "bobsplumbing.com",
            "is_active": True,
            "config": {},
        }
    )
    tm.list_tenants = AsyncMock(return_value=[])
    tm.deactivate_tenant = AsyncMock(return_value=True)
    tm.rotate_api_key = AsyncMock(return_value="sk_live_new_key_5678")
    tm.update_tenant = AsyncMock(
        return_value={
            "tenant_id": uuid4(),
            "name": "Bob's Updated Plumbing",
            "domain": "bobsplumbing.com",
            "is_active": True,
            "config": {"tone": "formal"},
        }
    )
    return tm


@pytest.fixture
def skill() -> ClientProvisioningSkill:
    return ClientProvisioningSkill(tenant_manager=_make_tenant_manager())


@pytest.fixture
def skill_no_tm() -> ClientProvisioningSkill:
    return ClientProvisioningSkill(tenant_manager=None)


# ---------------------------------------------------------------------------
# Metadata & init
# ---------------------------------------------------------------------------


class TestMetadata:
    def test_skill_name(self, skill: ClientProvisioningSkill) -> None:
        assert skill.metadata.name == "client_provisioning"

    def test_intents(self, skill: ClientProvisioningSkill) -> None:
        intents = skill.metadata.intents
        assert "client_create" in intents
        assert "client_configure" in intents
        assert "client_deactivate" in intents
        assert "client_rotate_key" in intents
        assert "client_list" in intents

    def test_version(self, skill: ClientProvisioningSkill) -> None:
        assert skill.metadata.version == "0.1.0"


class TestInitialize:
    @pytest.mark.asyncio
    async def test_init_with_manager(self, skill: ClientProvisioningSkill) -> None:
        result = await skill.initialize()
        assert result is True

    @pytest.mark.asyncio
    async def test_init_without_manager(self, skill_no_tm: ClientProvisioningSkill) -> None:
        result = await skill_no_tm.initialize()
        assert result is True  # Non-fatal

    @pytest.mark.asyncio
    async def test_safe_initialize_sets_ready(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        assert skill.status == SkillStatus.READY


# ---------------------------------------------------------------------------
# Handle — no TenantManager
# ---------------------------------------------------------------------------


class TestNoTenantManager:
    @pytest.mark.asyncio
    async def test_handle_errors_without_manager(
        self, skill_no_tm: ClientProvisioningSkill
    ) -> None:
        await skill_no_tm.safe_initialize()
        req = SkillRequest(intent="client_list")
        resp = await skill_no_tm.safe_handle(req)
        assert resp.success is False
        assert "not configured" in resp.error


# ---------------------------------------------------------------------------
# client_create
# ---------------------------------------------------------------------------


class TestClientCreate:
    @pytest.mark.asyncio
    async def test_create_success(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(
            intent="client_create",
            context={"name": "Bob's Plumbing", "domain": "bobsplumbing.com"},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert "Bob's Plumbing" in resp.message
        assert resp.data["api_key"] == "sk_live_test_key_1234"
        assert resp.data["name"] == "Bob's Plumbing"
        assert resp.data["domain"] == "bobsplumbing.com"

    @pytest.mark.asyncio
    async def test_create_missing_name(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(intent="client_create", context={})
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "name is required" in resp.error

    @pytest.mark.asyncio
    async def test_create_with_config(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(
            intent="client_create",
            context={
                "name": "Sarah's Salon",
                "config": {"tone": "friendly", "greeting": "Hello!"},
            },
        )
        resp = await skill.safe_handle(req)
        assert resp.success is True
        skill._tenant_manager.create_tenant.assert_called_once_with(
            name="Sarah's Salon",
            domain=None,
            config={"tone": "friendly", "greeting": "Hello!"},
        )


# ---------------------------------------------------------------------------
# client_configure
# ---------------------------------------------------------------------------


class TestClientConfigure:
    @pytest.mark.asyncio
    async def test_configure_success(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        tid = str(uuid4())
        req = SkillRequest(
            intent="client_configure",
            context={
                "tenant_id": tid,
                "name": "Bob's Updated Plumbing",
                "config": {"tone": "formal"},
            },
        )
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert "updated" in resp.message.lower()
        assert "name" in resp.message

    @pytest.mark.asyncio
    async def test_configure_missing_tenant_id(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(
            intent="client_configure",
            context={"name": "Updated Name"},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "tenant_id is required" in resp.error

    @pytest.mark.asyncio
    async def test_configure_no_fields(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(
            intent="client_configure",
            context={"tenant_id": str(uuid4())},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "at least one field" in resp.error.lower()

    @pytest.mark.asyncio
    async def test_configure_not_found(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        skill._tenant_manager.update_tenant = AsyncMock(return_value=None)
        req = SkillRequest(
            intent="client_configure",
            context={"tenant_id": str(uuid4()), "name": "Ghost"},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "not found" in resp.error.lower()


# ---------------------------------------------------------------------------
# client_deactivate
# ---------------------------------------------------------------------------


class TestClientDeactivate:
    @pytest.mark.asyncio
    async def test_deactivate_success(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        tid = str(uuid4())
        req = SkillRequest(
            intent="client_deactivate",
            context={"tenant_id": tid},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert "deactivated" in resp.message.lower()

    @pytest.mark.asyncio
    async def test_deactivate_missing_tenant_id(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(intent="client_deactivate", context={})
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "tenant_id is required" in resp.error

    @pytest.mark.asyncio
    async def test_deactivate_not_found(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        skill._tenant_manager.deactivate_tenant = AsyncMock(return_value=False)
        req = SkillRequest(
            intent="client_deactivate",
            context={"tenant_id": str(uuid4())},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "not found" in resp.error.lower()


# ---------------------------------------------------------------------------
# client_rotate_key
# ---------------------------------------------------------------------------


class TestClientRotateKey:
    @pytest.mark.asyncio
    async def test_rotate_success(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        tid = str(uuid4())
        req = SkillRequest(
            intent="client_rotate_key",
            context={"tenant_id": tid},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert resp.data["api_key"] == "sk_live_new_key_5678"
        assert "rotated" in resp.message.lower()

    @pytest.mark.asyncio
    async def test_rotate_missing_tenant_id(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(intent="client_rotate_key", context={})
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "tenant_id is required" in resp.error

    @pytest.mark.asyncio
    async def test_rotate_not_found(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        skill._tenant_manager.rotate_api_key = AsyncMock(return_value=None)
        req = SkillRequest(
            intent="client_rotate_key",
            context={"tenant_id": str(uuid4())},
        )
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "not found" in resp.error.lower()


# ---------------------------------------------------------------------------
# client_list
# ---------------------------------------------------------------------------


class TestClientList:
    @pytest.mark.asyncio
    async def test_list_empty(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(intent="client_list")
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert resp.data["count"] == 0
        assert "No clients found" in resp.message

    @pytest.mark.asyncio
    async def test_list_with_tenants(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        skill._tenant_manager.list_tenants = AsyncMock(
            return_value=[
                {
                    "tenant_id": uuid4(),
                    "name": "Bob's Plumbing",
                    "domain": "bobsplumbing.com",
                    "is_active": True,
                },
                {
                    "tenant_id": uuid4(),
                    "name": "Sarah's Salon",
                    "domain": None,
                    "is_active": True,
                },
            ]
        )
        req = SkillRequest(intent="client_list")
        resp = await skill.safe_handle(req)
        assert resp.success is True
        assert resp.data["count"] == 2
        assert "Bob's Plumbing" in resp.message
        assert "Sarah's Salon" in resp.message

    @pytest.mark.asyncio
    async def test_list_include_inactive(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(
            intent="client_list",
            context={"include_inactive": True},
        )
        await skill.safe_handle(req)
        skill._tenant_manager.list_tenants.assert_called_once_with(
            active_only=False,
        )


# ---------------------------------------------------------------------------
# Unknown intent
# ---------------------------------------------------------------------------


class TestUnknownIntent:
    @pytest.mark.asyncio
    async def test_unknown_intent(self, skill: ClientProvisioningSkill) -> None:
        await skill.safe_initialize()
        req = SkillRequest(intent="client_bogus")
        resp = await skill.safe_handle(req)
        assert resp.success is False
        assert "Unknown" in resp.error
