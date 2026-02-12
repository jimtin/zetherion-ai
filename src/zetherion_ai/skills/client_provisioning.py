"""Client provisioning skill — tenant lifecycle management.

Allows James (or any authorised user) to create, configure, deactivate,
and rotate API keys for client tenants via natural-language commands
from any platform (Discord, Slack, web, etc.).

Intents handled:
    client_create     — Create a new client tenant
    client_configure  — Update a tenant's config / name / domain
    client_deactivate — Soft-delete a tenant
    client_rotate_key — Generate a new API key for a tenant
    client_list       — List all active tenants
"""

from __future__ import annotations

from typing import Any

from zetherion_ai.api.tenant import TenantManager
from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

log = get_logger("zetherion_ai.skills.client_provisioning")


class ClientProvisioningSkill(Skill):
    """Skill for managing client tenant lifecycle."""

    def __init__(
        self,
        tenant_manager: TenantManager | None = None,
    ) -> None:
        super().__init__(memory=None)
        self._tenant_manager = tenant_manager

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="client_provisioning",
            description="Create and manage client tenants, API keys, and configuration",
            version="0.1.0",
            permissions=PermissionSet(
                {
                    Permission.ADMIN,
                    Permission.READ_CONFIG,
                    Permission.SEND_MESSAGES,
                }
            ),
            intents=[
                "client_create",
                "client_configure",
                "client_deactivate",
                "client_rotate_key",
                "client_list",
            ],
        )

    async def initialize(self) -> bool:
        if self._tenant_manager is None:
            log.warning("client_provisioning_no_tenant_manager")
            return True  # Non-fatal — manager can be set later
        log.info("client_provisioning_initialized")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        if self._tenant_manager is None:
            return SkillResponse.error_response(
                request.id,
                "Client provisioning is not configured (no TenantManager).",
            )

        intent = request.intent
        handlers: dict[str, Any] = {
            "client_create": self._handle_create,
            "client_configure": self._handle_configure,
            "client_deactivate": self._handle_deactivate,
            "client_rotate_key": self._handle_rotate_key,
            "client_list": self._handle_list,
        }

        handler = handlers.get(intent)
        if handler is None:
            return SkillResponse.error_response(
                request.id,
                f"Unknown client provisioning intent: {intent}",
            )

        return await handler(request)  # type: ignore[no-any-return]

    # ------------------------------------------------------------------
    # Intent handlers
    # ------------------------------------------------------------------

    async def _handle_create(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        name: str = ctx.get("name", "")
        domain: str | None = ctx.get("domain")
        config: dict[str, Any] | None = ctx.get("config")

        if not name:
            return SkillResponse.error_response(
                request.id,
                "A client name is required to create a tenant.",
            )

        assert self._tenant_manager is not None
        tenant, api_key = await self._tenant_manager.create_tenant(
            name=name,
            domain=domain,
            config=config,
        )

        tenant_id = str(tenant["tenant_id"])
        msg = (
            f"Client **{name}** created successfully.\n"
            f"- Tenant ID: `{tenant_id}`\n"
            f"- API Key: `{api_key}`\n"
            f"  (store this key securely — it cannot be retrieved later)"
        )
        if domain:
            msg += f"\n- Domain: {domain}"

        log.info("client_created", tenant_id=tenant_id, name=name)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=msg,
            data={
                "tenant_id": tenant_id,
                "name": name,
                "domain": domain,
                "api_key": api_key,
            },
        )

    async def _handle_configure(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        tenant_id: str = ctx.get("tenant_id", "")

        if not tenant_id:
            return SkillResponse.error_response(
                request.id,
                "A tenant_id is required to configure a client.",
            )

        name: str | None = ctx.get("name")
        domain: str | None = ctx.get("domain")
        config: dict[str, Any] | None = ctx.get("config")

        if name is None and domain is None and config is None:
            return SkillResponse.error_response(
                request.id,
                "Provide at least one field to update (name, domain, or config).",
            )

        assert self._tenant_manager is not None
        updated = await self._tenant_manager.update_tenant(
            tenant_id,
            name=name,
            domain=domain,
            config=config,
        )

        if updated is None:
            return SkillResponse.error_response(
                request.id,
                f"Tenant `{tenant_id}` not found.",
            )

        changes = []
        if name is not None:
            changes.append(f"name → **{name}**")
        if domain is not None:
            changes.append(f"domain → **{domain}**")
        if config is not None:
            changes.append("config updated")

        log.info("client_configured", tenant_id=tenant_id)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Client `{tenant_id}` updated: {', '.join(changes)}.",
            data={"tenant_id": tenant_id, "updated_fields": changes},
        )

    async def _handle_deactivate(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        tenant_id: str = ctx.get("tenant_id", "")

        if not tenant_id:
            return SkillResponse.error_response(
                request.id,
                "A tenant_id is required to deactivate a client.",
            )

        assert self._tenant_manager is not None
        ok = await self._tenant_manager.deactivate_tenant(tenant_id)

        if not ok:
            return SkillResponse.error_response(
                request.id,
                f"Tenant `{tenant_id}` not found or already deactivated.",
            )

        log.info("client_deactivated", tenant_id=tenant_id)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Client `{tenant_id}` has been deactivated.",
            data={"tenant_id": tenant_id, "deactivated": True},
        )

    async def _handle_rotate_key(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        tenant_id: str = ctx.get("tenant_id", "")

        if not tenant_id:
            return SkillResponse.error_response(
                request.id,
                "A tenant_id is required to rotate an API key.",
            )

        assert self._tenant_manager is not None
        new_key = await self._tenant_manager.rotate_api_key(tenant_id)

        if new_key is None:
            return SkillResponse.error_response(
                request.id,
                f"Tenant `{tenant_id}` not found.",
            )

        log.info("client_key_rotated", tenant_id=tenant_id)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=(
                f"API key rotated for `{tenant_id}`.\n"
                f"- New API Key: `{new_key}`\n"
                f"  (store this key securely — the old key is now invalid)"
            ),
            data={"tenant_id": tenant_id, "api_key": new_key},
        )

    async def _handle_list(self, request: SkillRequest) -> SkillResponse:
        ctx = request.context
        include_inactive = ctx.get("include_inactive", False)

        assert self._tenant_manager is not None
        tenants = await self._tenant_manager.list_tenants(
            active_only=not include_inactive,
        )

        if not tenants:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message="No clients found.",
                data={"tenants": [], "count": 0},
            )

        lines = [f"**{len(tenants)} client(s):**\n"]
        for t in tenants:
            status = "active" if t.get("is_active") else "inactive"
            line = f"- **{t['name']}** (`{t['tenant_id']}`) — {status}"
            if t.get("domain"):
                line += f" — {t['domain']}"
            lines.append(line)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message="\n".join(lines),
            data={
                "tenants": [
                    {
                        "tenant_id": str(t["tenant_id"]),
                        "name": t["name"],
                        "domain": t.get("domain"),
                        "is_active": t.get("is_active", True),
                    }
                    for t in tenants
                ],
                "count": len(tenants),
            },
        )
