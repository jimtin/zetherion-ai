"""Tenant-scoped admin control-plane primitives."""

from zetherion_ai.admin.tenant_admin_manager import (
    VALID_TENANT_ROLES,
    AdminActorContext,
    TenantAdminManager,
)

__all__ = ["AdminActorContext", "TenantAdminManager", "VALID_TENANT_ROLES"]
