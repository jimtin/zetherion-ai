"""Provisioning and reconciliation helpers for CGS tenant mappings."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from zetherion_ai.cgs_gateway.errors import GatewayError, map_upstream_error
from zetherion_ai.cgs_gateway.storage import (
    DEFAULT_TENANT_ISOLATION_STAGE,
    TENANT_ISOLATION_STAGES,
    CGSGatewayStorage,
)

PROVISIONING_BASELINE_VERSION = 1


class CGSTenantProvisioningOrchestrator:
    """Idempotent tenant provisioning and reconciliation for CGS mappings."""

    def __init__(self, *, storage: CGSGatewayStorage, skills_client: Any) -> None:
        self._storage = storage
        self._skills_client = skills_client

    async def provision_tenant(
        self,
        *,
        cgs_tenant_id: str,
        name: str,
        domain: str | None,
        config: dict[str, Any] | None,
        user_id: str,
        request_id: str,
    ) -> tuple[dict[str, Any], str, bool]:
        """Create a new upstream tenant or reconcile an existing mapping."""
        existing = await self._storage.get_tenant_mapping(cgs_tenant_id)
        if existing is not None:
            updated = await self._reconcile_existing_mapping(
                mapping=existing,
                name=name,
                domain=domain,
                config=config,
                user_id=user_id,
                request_id=request_id,
                source="cgs_internal_create",
                desired_isolation_stage=str(
                    existing.get("isolation_stage", DEFAULT_TENANT_ISOLATION_STAGE)
                ),
                owner_portfolio_ready=self._owner_portfolio_ready(existing.get("metadata")),
            )
            return updated, str(existing["zetherion_api_key"]), False

        status, skill_response = await self._skills_client.handle_intent(
            intent="client_create",
            user_id=user_id,
            message="",
            request_id=request_id,
            context={
                "name": name,
                "domain": domain,
                "config": config or {},
            },
        )
        if status >= 400:
            raise map_upstream_error(status=status, payload=skill_response, source="skills")

        skill_data = _extract_skill_data(skill_response)
        zetherion_tenant_id = str(skill_data.get("tenant_id", ""))
        api_key = str(skill_data.get("api_key", ""))
        if not zetherion_tenant_id or not api_key:
            raise GatewayError(
                code="AI_SKILLS_UPSTREAM_ERROR",
                message="Skills API response missing tenant_id/api_key",
                status=502,
                details={"upstream": skill_response},
            )

        mapping = await self._storage.upsert_tenant_mapping(
            cgs_tenant_id=cgs_tenant_id,
            zetherion_tenant_id=zetherion_tenant_id,
            name=name,
            domain=domain,
            zetherion_api_key=api_key,
            metadata=self._build_metadata(
                existing=None,
                config=config,
                source="cgs_internal_create",
                owner_portfolio_ready=False,
                issues=[],
            ),
            isolation_stage=DEFAULT_TENANT_ISOLATION_STAGE,
        )
        return mapping, api_key, True

    async def reconcile_tenant(
        self,
        *,
        cgs_tenant_id: str,
        user_id: str,
        request_id: str,
        desired_isolation_stage: str | None = None,
        expected_key_version: int | None = None,
        owner_portfolio_ready: bool | None = None,
        name: str | None = None,
        domain: str | None = None,
        config: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Refresh mapping metadata and optional upstream profile state for one tenant."""
        mapping = await self._storage.get_tenant_mapping(cgs_tenant_id)
        if mapping is None:
            return None

        updated = await self._reconcile_existing_mapping(
            mapping=mapping,
            name=name or str(mapping.get("name", "")),
            domain=domain if domain is not None else _string_or_none(mapping.get("domain")),
            config=config,
            user_id=user_id,
            request_id=request_id,
            source="cgs_internal_reconcile",
            desired_isolation_stage=(
                desired_isolation_stage
                or str(mapping.get("isolation_stage", DEFAULT_TENANT_ISOLATION_STAGE))
            ),
            owner_portfolio_ready=(
                owner_portfolio_ready
                if owner_portfolio_ready is not None
                else self._owner_portfolio_ready(mapping.get("metadata"))
            ),
            expected_key_version=expected_key_version,
        )
        return updated

    async def list_reconciliation_candidates(self) -> list[dict[str, Any]]:
        """List mappings that still need isolation or portfolio reconciliation."""
        return await self._storage.list_tenant_reconciliation_candidates()

    async def _reconcile_existing_mapping(
        self,
        *,
        mapping: dict[str, Any],
        name: str,
        domain: str | None,
        config: dict[str, Any] | None,
        user_id: str,
        request_id: str,
        source: str,
        desired_isolation_stage: str,
        owner_portfolio_ready: bool,
        expected_key_version: int | None = None,
    ) -> dict[str, Any]:
        issues = self._collect_reconciliation_issues(
            mapping=mapping,
            desired_isolation_stage=desired_isolation_stage,
            owner_portfolio_ready=owner_portfolio_ready,
            expected_key_version=expected_key_version,
        )

        current_name = str(mapping.get("name", ""))
        current_domain = _string_or_none(mapping.get("domain"))
        current_config = _config_from_metadata(mapping.get("metadata"))
        if name != current_name or domain != current_domain or config is not None:
            status, skill_response = await self._skills_client.handle_intent(
                intent="client_configure",
                user_id=user_id,
                message="",
                request_id=request_id,
                context={
                    "tenant_id": str(mapping["zetherion_tenant_id"]),
                    "name": name,
                    "domain": domain,
                    "config": config,
                },
            )
            if status >= 400:
                raise map_upstream_error(status=status, payload=skill_response, source="skills")

        metadata = self._build_metadata(
            existing=mapping.get("metadata"),
            config=config if config is not None else current_config,
            source=source,
            owner_portfolio_ready=owner_portfolio_ready,
            issues=issues,
        )
        updated = await self._storage.update_tenant_profile(
            cgs_tenant_id=str(mapping["cgs_tenant_id"]),
            name=name,
            domain=domain,
            metadata=metadata,
            isolation_stage=_normalize_stage(desired_isolation_stage),
        )
        if updated is None:
            raise GatewayError(
                code="AI_TENANT_NOT_FOUND",
                message="Tenant mapping not found",
                status=404,
            )
        updated["reconciliation_issues"] = issues
        return updated

    def _build_metadata(
        self,
        *,
        existing: Any,
        config: dict[str, Any] | None,
        source: str,
        owner_portfolio_ready: bool,
        issues: list[str],
    ) -> dict[str, Any]:
        metadata = deepcopy(existing) if isinstance(existing, dict) else {}
        provisioning = deepcopy(metadata.get("provisioning", {}))
        now_value = _now_iso()
        provisioning.setdefault("baseline_seeded_at", now_value)
        provisioning["baseline_version"] = max(
            int(provisioning.get("baseline_version", 0)),
            PROVISIONING_BASELINE_VERSION,
        )
        provisioning["default_trust_policy_seeded"] = True
        provisioning["default_profile_seeded"] = True
        provisioning["default_config_seeded"] = True
        provisioning["last_reconciled_at"] = now_value
        provisioning["source"] = source
        provisioning["owner_portfolio_ready"] = owner_portfolio_ready
        provisioning["reconciliation_issues"] = issues
        metadata["provisioning"] = provisioning
        if config is not None:
            metadata["config"] = config
        return metadata

    def _collect_reconciliation_issues(
        self,
        *,
        mapping: dict[str, Any],
        desired_isolation_stage: str,
        owner_portfolio_ready: bool,
        expected_key_version: int | None,
    ) -> list[str]:
        issues: list[str] = []
        current_stage = _normalize_stage(
            str(mapping.get("isolation_stage", DEFAULT_TENANT_ISOLATION_STAGE))
        )
        target_stage = _normalize_stage(desired_isolation_stage)
        if current_stage == DEFAULT_TENANT_ISOLATION_STAGE:
            issues.append("unmigrated_isolation_stage")
        if target_stage != current_stage:
            issues.append(f"isolation_stage_update:{current_stage}->{target_stage}")
        if (
            expected_key_version is not None
            and int(mapping.get("key_version", 0)) < expected_key_version
        ):
            issues.append("stale_key_version")
        if not owner_portfolio_ready:
            issues.append("missing_owner_portfolio_dataset")
        return issues

    @staticmethod
    def _owner_portfolio_ready(metadata: Any) -> bool:
        if not isinstance(metadata, dict):
            return False
        provisioning = metadata.get("provisioning")
        if not isinstance(provisioning, dict):
            return False
        return bool(provisioning.get("owner_portfolio_ready", False))


def _extract_skill_data(payload: Any) -> dict[str, Any]:
    if not isinstance(payload, dict):
        return {}
    data = payload.get("data")
    if isinstance(data, dict):
        return data
    return payload


def _config_from_metadata(metadata: Any) -> dict[str, Any] | None:
    if not isinstance(metadata, dict):
        return None
    value = metadata.get("config")
    return value if isinstance(value, dict) else None


def _string_or_none(value: Any) -> str | None:
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _normalize_stage(value: str) -> str:
    normalized = value.strip().lower()
    if normalized not in TENANT_ISOLATION_STAGES:
        raise GatewayError(
            code="AI_BAD_REQUEST",
            message="Unsupported isolation stage",
            status=400,
            details={"isolation_stage": value},
        )
    return normalized


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
