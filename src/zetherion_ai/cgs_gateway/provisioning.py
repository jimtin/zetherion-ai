"""Provisioning, reconciliation, and staged migration helpers for CGS tenant mappings."""

from __future__ import annotations

from copy import deepcopy
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from zetherion_ai.cgs_gateway.errors import GatewayError, map_upstream_error
from zetherion_ai.cgs_gateway.storage import (
    DEFAULT_TENANT_ISOLATION_STAGE,
    TENANT_ISOLATION_STAGES,
    CGSGatewayStorage,
)

PROVISIONING_BASELINE_VERSION = 1
_DEFAULT_DOCUMENT_BACKFILL_LIMIT = 200
_STAGE_ORDER = {stage: index for index, stage in enumerate(TENANT_ISOLATION_STAGES)}
_RUNTIME_POLICY_BY_STAGE: dict[str, dict[str, Any]] = {
    "legacy": {
        "primary_read_plane": "legacy",
        "legacy_read_fallback": False,
        "tenant_read_fallback": False,
        "dual_read_enabled": False,
        "dual_write_enabled": False,
        "tenant_primary_write": False,
    },
    "shadow": {
        "primary_read_plane": "legacy",
        "legacy_read_fallback": False,
        "tenant_read_fallback": True,
        "dual_read_enabled": True,
        "dual_write_enabled": False,
        "tenant_primary_write": False,
    },
    "dual_write": {
        "primary_read_plane": "legacy",
        "legacy_read_fallback": False,
        "tenant_read_fallback": True,
        "dual_read_enabled": True,
        "dual_write_enabled": True,
        "tenant_primary_write": False,
    },
    "cutover_ready": {
        "primary_read_plane": "tenant",
        "legacy_read_fallback": True,
        "tenant_read_fallback": False,
        "dual_read_enabled": True,
        "dual_write_enabled": True,
        "tenant_primary_write": True,
    },
    "isolated": {
        "primary_read_plane": "tenant",
        "legacy_read_fallback": False,
        "tenant_read_fallback": False,
        "dual_read_enabled": False,
        "dual_write_enabled": False,
        "tenant_primary_write": True,
    },
}


class CGSTenantProvisioningOrchestrator:
    """Idempotent tenant provisioning, reconciliation, and staged migration."""

    def __init__(
        self,
        *,
        storage: CGSGatewayStorage,
        skills_client: Any,
        public_client: Any | None = None,
    ) -> None:
        self._storage = storage
        self._skills_client = skills_client
        self._public_client = public_client

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
        run_tenant_vector_backfill: bool = False,
        derive_owner_portfolio: bool = False,
        cutover_verified: bool = False,
        release_marker: dict[str, Any] | None = None,
        document_backfill_limit: int = _DEFAULT_DOCUMENT_BACKFILL_LIMIT,
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
            run_tenant_vector_backfill=run_tenant_vector_backfill,
            derive_owner_portfolio=derive_owner_portfolio,
            cutover_verified=cutover_verified,
            release_marker=release_marker,
            document_backfill_limit=document_backfill_limit,
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
        run_tenant_vector_backfill: bool = False,
        derive_owner_portfolio: bool = False,
        cutover_verified: bool = False,
        release_marker: dict[str, Any] | None = None,
        document_backfill_limit: int = _DEFAULT_DOCUMENT_BACKFILL_LIMIT,
    ) -> dict[str, Any]:
        current_stage = _normalize_stage(
            str(mapping.get("isolation_stage", DEFAULT_TENANT_ISOLATION_STAGE))
        )
        target_stage = _normalize_stage(desired_isolation_stage)

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

        vector_backfill = (
            await self._backfill_tenant_vectors(
                mapping=mapping,
                request_id=request_id,
                limit=document_backfill_limit,
            )
            if run_tenant_vector_backfill
            else self._migration_vector_backfill_state(mapping.get("metadata"))
        )
        vector_backfill_completed = self._vector_backfill_completed(vector_backfill)

        portfolio_snapshot = None
        if derive_owner_portfolio:
            portfolio_snapshot = await self._derive_owner_portfolio_snapshot(
                mapping=mapping,
                user_id=user_id,
                request_id=request_id,
                isolation_stage=target_stage,
                release_marker=release_marker,
            )
            owner_portfolio_ready = True
        elif owner_portfolio_ready:
            portfolio_snapshot = self._migration_owner_portfolio_snapshot(mapping.get("metadata"))

        published_release_marker = None
        if release_marker is not None:
            published_release_marker = await self._publish_release_marker(
                mapping=mapping,
                request_id=request_id,
                release_marker=release_marker,
            )

        (
            applied_stage,
            migration_status,
            stage_issues,
            runtime_policy,
        ) = self._resolve_stage_transition(
            current_stage=current_stage,
            target_stage=target_stage,
            vector_backfill_completed=vector_backfill_completed,
            owner_portfolio_ready=owner_portfolio_ready,
            cutover_verified=cutover_verified,
        )
        issues = self._collect_reconciliation_issues(
            mapping=mapping,
            desired_isolation_stage=target_stage,
            owner_portfolio_ready=owner_portfolio_ready,
            expected_key_version=expected_key_version,
        )
        for issue in stage_issues:
            if issue not in issues:
                issues.append(issue)

        receipt_id = None
        migration_requested = self._migration_requested(
            current_stage=current_stage,
            target_stage=target_stage,
            run_tenant_vector_backfill=run_tenant_vector_backfill,
            derive_owner_portfolio=derive_owner_portfolio,
            release_marker=release_marker,
            cutover_verified=cutover_verified,
        )
        if migration_requested:
            receipt_id = _new_migration_receipt_id()

        metadata = self._build_metadata(
            existing=mapping.get("metadata"),
            config=config if config is not None else current_config,
            source=source,
            owner_portfolio_ready=owner_portfolio_ready,
            issues=issues,
            migration_context=(
                {
                    "receipt_id": receipt_id,
                    "current_stage": current_stage,
                    "target_stage": target_stage,
                    "applied_stage": applied_stage,
                    "status": migration_status,
                    "runtime_policy": runtime_policy,
                    "vector_backfill": vector_backfill,
                    "owner_portfolio_snapshot": portfolio_snapshot,
                    "release_marker": published_release_marker or release_marker,
                    "cutover_verified": cutover_verified,
                }
                if migration_requested
                else None
            ),
        )
        updated = await self._storage.update_tenant_profile(
            cgs_tenant_id=str(mapping["cgs_tenant_id"]),
            name=name,
            domain=domain,
            metadata=metadata,
            isolation_stage=applied_stage,
        )
        if updated is None:
            raise GatewayError(
                code="AI_TENANT_NOT_FOUND",
                message="Tenant mapping not found",
                status=404,
            )

        if migration_requested and receipt_id is not None:
            receipt = await self._storage.create_tenant_migration_receipt(
                receipt_id=receipt_id,
                cgs_tenant_id=str(mapping["cgs_tenant_id"]),
                previous_stage=current_stage,
                desired_stage=target_stage,
                applied_stage=applied_stage,
                status=migration_status,
                runtime_policy=runtime_policy,
                vector_backfill=vector_backfill,
                owner_portfolio_snapshot=portfolio_snapshot,
                release_marker=published_release_marker or release_marker,
                metadata={"issues": issues, "cutover_verified": cutover_verified},
                requested_by=user_id,
                request_id=request_id,
            )
            updated["migration_receipt_id"] = receipt["receipt_id"]
            updated["migration_status"] = receipt["status"]
            updated["migration_runtime_policy"] = receipt["runtime_policy"]
        else:
            updated["migration_receipt_id"] = None
            updated["migration_status"] = None
            updated["migration_runtime_policy"] = None

        updated["reconciliation_issues"] = issues
        updated["tenant_vector_backfill"] = vector_backfill
        updated["owner_portfolio_snapshot"] = portfolio_snapshot
        updated["release_marker"] = published_release_marker
        return updated

    def _build_metadata(
        self,
        *,
        existing: Any,
        config: dict[str, Any] | None,
        source: str,
        owner_portfolio_ready: bool,
        issues: list[str],
        migration_context: dict[str, Any] | None = None,
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
        if migration_context is not None:
            migration = deepcopy(metadata.get("migration", {}))
            migration.update(
                {
                    "last_receipt_id": migration_context.get("receipt_id"),
                    "current_stage": migration_context.get("current_stage"),
                    "desired_stage": migration_context.get("target_stage"),
                    "applied_stage": migration_context.get("applied_stage"),
                    "status": migration_context.get("status"),
                    "runtime_policy": migration_context.get("runtime_policy"),
                    "last_migrated_at": now_value,
                    "cutover_verified": bool(migration_context.get("cutover_verified", False)),
                }
            )
            if migration_context.get("vector_backfill") is not None:
                migration["tenant_vector_backfill"] = migration_context["vector_backfill"]
            if migration_context.get("owner_portfolio_snapshot") is not None:
                migration["owner_portfolio_snapshot"] = migration_context[
                    "owner_portfolio_snapshot"
                ]
            if migration_context.get("release_marker") is not None:
                migration["release_marker"] = migration_context["release_marker"]
            metadata["migration"] = migration
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

    async def _backfill_tenant_vectors(
        self,
        *,
        mapping: dict[str, Any],
        request_id: str,
        limit: int,
    ) -> dict[str, Any]:
        if self._public_client is None:
            raise GatewayError(
                code="AI_UPSTREAM_UNAVAILABLE",
                message="Public API client is not configured for tenant vector backfill",
                status=503,
            )

        headers = {
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
            "X-API-Key": str(mapping["zetherion_api_key"]),
        }
        status, payload, _ = await self._public_client.list_documents(
            headers=headers,
            limit=max(1, int(limit or _DEFAULT_DOCUMENT_BACKFILL_LIMIT)),
            include_archived=False,
        )
        if status >= 400:
            raise map_upstream_error(status=status, payload=payload, source="upstream")

        documents = payload.get("documents") if isinstance(payload, dict) else []
        if not isinstance(documents, list):
            documents = []

        eligible: list[dict[str, Any]] = []
        for raw in documents:
            if not isinstance(raw, dict):
                continue
            doc_id = str(raw.get("document_id", "")).strip()
            if not doc_id:
                continue
            doc_status = str(raw.get("status", "")).strip().lower()
            if doc_status in {"archived", "purged", "archiving"}:
                continue
            eligible.append(raw)

        summary: dict[str, Any] = {
            "status": "skipped" if not eligible else "completed",
            "listed": len(documents),
            "eligible": len(eligible),
            "requested": 0,
            "reindexed": 0,
            "document_ids": [],
        }
        for raw in eligible:
            doc_id = str(raw.get("document_id", "")).strip()
            if not doc_id:
                continue
            summary["requested"] += 1
            summary["document_ids"].append(doc_id)
            reindex_status, reindex_payload, _ = await self._public_client.reindex_document(
                document_id=doc_id,
                headers=headers,
            )
            if reindex_status >= 400:
                raise map_upstream_error(
                    status=reindex_status,
                    payload=reindex_payload,
                    source="upstream",
                )
            summary["reindexed"] += 1
        return summary

    async def _derive_owner_portfolio_snapshot(
        self,
        *,
        mapping: dict[str, Any],
        user_id: str,
        request_id: str,
        isolation_stage: str,
        release_marker: dict[str, Any] | None,
    ) -> dict[str, Any]:
        status, skill_response = await self._skills_client.handle_intent(
            intent="client_health_check",
            user_id=user_id,
            message="",
            request_id=request_id,
            context={"tenant_id": str(mapping["zetherion_tenant_id"]), "source": "cgs-migration"},
        )
        if status >= 400:
            raise map_upstream_error(status=status, payload=skill_response, source="skills")

        skill_data = _extract_skill_data(skill_response)
        summary = skill_data.get("health") if isinstance(skill_data.get("health"), dict) else {}
        if not isinstance(summary, dict):
            summary = {}
        snapshot = await self._storage.upsert_owner_portfolio_snapshot(
            cgs_tenant_id=str(mapping["cgs_tenant_id"]),
            zetherion_tenant_id=str(mapping["zetherion_tenant_id"]),
            tenant_name=str(mapping.get("name", "")),
            isolation_stage=isolation_stage,
            source="cgs_internal_reconcile",
            summary=summary,
            release_marker=release_marker,
            snapshot_metadata={"request_id": request_id},
        )
        return snapshot

    async def _publish_release_marker(
        self,
        *,
        mapping: dict[str, Any],
        request_id: str,
        release_marker: dict[str, Any],
    ) -> dict[str, Any]:
        if self._public_client is None:
            raise GatewayError(
                code="AI_UPSTREAM_UNAVAILABLE",
                message="Public API client is not configured for release markers",
                status=503,
            )

        headers = {
            "Content-Type": "application/json",
            "X-Request-Id": request_id,
            "X-API-Key": str(mapping["zetherion_api_key"]),
        }
        status, payload, _ = await self._public_client.create_release_marker(
            headers=headers,
            payload=release_marker,
        )
        if status >= 400:
            raise map_upstream_error(status=status, payload=payload, source="upstream")
        return payload if isinstance(payload, dict) else {"payload": payload}

    def _resolve_stage_transition(
        self,
        *,
        current_stage: str,
        target_stage: str,
        vector_backfill_completed: bool,
        owner_portfolio_ready: bool,
        cutover_verified: bool,
    ) -> tuple[str, str, list[str], dict[str, Any]]:
        blocked_reasons: list[str] = []
        if _stage_rank(target_stage) > _stage_rank(current_stage):
            if (
                target_stage in {"dual_write", "cutover_ready", "isolated"}
                and not vector_backfill_completed
            ):
                blocked_reasons.append("tenant_vector_backfill_required")
            if target_stage in {"cutover_ready", "isolated"} and not owner_portfolio_ready:
                blocked_reasons.append("owner_portfolio_snapshot_required")
            if target_stage == "isolated" and not cutover_verified:
                blocked_reasons.append("cutover_verification_required")

        if blocked_reasons:
            applied_stage = current_stage
            status = "blocked"
        elif _stage_rank(target_stage) < _stage_rank(current_stage):
            applied_stage = target_stage
            status = "rolled_back"
        else:
            applied_stage = target_stage
            status = "applied"
        return applied_stage, status, blocked_reasons, _runtime_policy(applied_stage)

    @staticmethod
    def _migration_requested(
        *,
        current_stage: str,
        target_stage: str,
        run_tenant_vector_backfill: bool,
        derive_owner_portfolio: bool,
        release_marker: dict[str, Any] | None,
        cutover_verified: bool,
    ) -> bool:
        return any(
            (
                current_stage != target_stage,
                run_tenant_vector_backfill,
                derive_owner_portfolio,
                release_marker is not None,
                cutover_verified,
            )
        )

    @staticmethod
    def _owner_portfolio_ready(metadata: Any) -> bool:
        if not isinstance(metadata, dict):
            return False
        provisioning = metadata.get("provisioning")
        if not isinstance(provisioning, dict):
            return False
        return bool(provisioning.get("owner_portfolio_ready", False))

    @staticmethod
    def _migration_vector_backfill_state(metadata: Any) -> dict[str, Any] | None:
        if not isinstance(metadata, dict):
            return None
        migration = metadata.get("migration")
        if not isinstance(migration, dict):
            return None
        vector_backfill = migration.get("tenant_vector_backfill")
        return vector_backfill if isinstance(vector_backfill, dict) else None

    @staticmethod
    def _migration_owner_portfolio_snapshot(metadata: Any) -> dict[str, Any] | None:
        if not isinstance(metadata, dict):
            return None
        migration = metadata.get("migration")
        if not isinstance(migration, dict):
            return None
        snapshot = migration.get("owner_portfolio_snapshot")
        return snapshot if isinstance(snapshot, dict) else None

    @staticmethod
    def _vector_backfill_completed(summary: dict[str, Any] | None) -> bool:
        if not isinstance(summary, dict):
            return False
        return str(summary.get("status", "")).lower() in {"completed", "skipped"}


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


def _stage_rank(stage: str) -> int:
    return _STAGE_ORDER[_normalize_stage(stage)]


def _runtime_policy(stage: str) -> dict[str, Any]:
    normalized = _normalize_stage(stage)
    return deepcopy(_RUNTIME_POLICY_BY_STAGE[normalized])


def _new_migration_receipt_id() -> str:
    return f"mig_{uuid4().hex[:24]}"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()
