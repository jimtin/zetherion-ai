"""Typed owner-CI plan and readiness models."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

_READY_STATES = {"success", "healthy", "passed", "green"}
_FAILED_STATES = {"failed", "failure", "blocked", "deployed_but_unhealthy", "red"}
_PENDING_STATES = {"pending", "running", "queued", "planned", "degraded", "awaiting_sync"}
_REQUIRED_RELEASE_FIELDS = (
    "delivery_canary_passed",
    "security_canary_passed",
    "queue_worker_healthy",
    "runtime_status_persistence",
    "skills_reachable",
    "cgs_auth_flow_passed",
    "cgs_login_redirect_passed",
    "ai_ops_schema_passed",
    "cgs_admin_ai_page_passed",
    "cgs_owner_ci_reporting_passed",
    "cgs_chatbot_runtime_proxy_passed",
    "runtime_drift_zero",
    "back_to_back_deploy_passed",
)
_REQUIRED_WORKER_CERTIFICATION_FIELDS = (
    "bootstrap_succeeded",
    "registration_succeeded",
    "heartbeat_succeeded",
    "job_claim_succeeded",
    "noop_job_succeeded",
    "ci_test_run_succeeded",
    "artifacts_submitted",
    "cleanup_verified",
    "status_publication_succeeded",
)


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, int | float):
        return bool(value)
    candidate = str(value).strip().lower()
    if not candidate:
        return None
    if candidate in {"1", "true", "yes", "on", "passed", "healthy", "success", "green"}:
        return True
    if candidate in {"0", "false", "no", "off", "failed", "blocked", "red"}:
        return False
    return None


def _bool_to_check_status(value: bool | None) -> str:
    if value is True:
        return "passed"
    if value is False:
        return "failed"
    return "pending"


def _check_passed(status: str) -> bool | None:
    candidate = status.strip().lower()
    if candidate in _READY_STATES:
        return True
    if candidate in _FAILED_STATES:
        return False
    return None


class LocalGateShardSpec(BaseModel):
    """One executable shard in a local-first CI plan."""

    lane_id: str
    lane_label: str
    shard_id: str | None = None
    execution_target: str = "local_mac"
    runner: str = "command"
    action: str = "ci.test.run"
    command: list[str] = Field(default_factory=list)
    timeout_seconds: int | None = None
    resource_class: str = "cpu"
    parallel_group: str = ""
    depends_on: list[str] = Field(default_factory=list)
    artifact_contract: dict[str, Any] = Field(default_factory=dict)
    required_capabilities: list[str] = Field(default_factory=list)
    relay_mode: str = "direct"
    workspace_root: str | None = None
    covered_required_paths: list[str] = Field(default_factory=list)
    gate_family: str = "integration"
    blocking: bool = True
    required_category: str | None = None
    payload: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class HostCapacitySnapshot(BaseModel):
    """Coarse host-capacity snapshot for admission decisions."""

    host_id: str = "windows-owner-ci"
    cpu_slots_total: int = 0
    cpu_slots_used: int = 0
    service_slots_total: int = 0
    service_slots_used: int = 0
    serial_slots_total: int = 0
    serial_slots_used: int = 0
    runtime_headroom_reserved: bool = True
    blocking_reasons: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ResourceReservation(BaseModel):
    """Reservation request for one shard on the host scheduler."""

    repo_id: str | None = None
    shard_id: str | None = None
    resource_class: str = "cpu"
    units: int = 1
    parallel_group: str | None = None
    workspace_root: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class AdmissionDecision(BaseModel):
    """Result of a coarse host-admission evaluation."""

    admitted: bool = True
    summary: str = ""
    blocking_reasons: list[str] = Field(default_factory=list)
    capacity_snapshot: HostCapacitySnapshot | None = None
    reservations: list[ResourceReservation] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LocalGatePlan(BaseModel):
    """Compiled shard plan for a repo-local gate."""

    repo_id: str
    git_ref: str
    mode: str
    windows_execution_mode: str = "command"
    resource_budget: dict[str, int] = Field(default_factory=dict)
    schedule_tags: list[str] = Field(default_factory=list)
    retry_policy: dict[str, Any] = Field(default_factory=dict)
    debug_bundle_contract: dict[str, Any] = Field(default_factory=dict)
    required_static_gate_ids: list[str] = Field(default_factory=list)
    required_security_gate_ids: list[str] = Field(default_factory=list)
    required_gate_categories: list[str] = Field(default_factory=list)
    certification_requirements: list[str] = Field(default_factory=list)
    scheduled_canaries: list[dict[str, Any]] = Field(default_factory=list)
    host_capacity_policy: dict[str, Any] = Field(default_factory=dict)
    required_paths: list[str] = Field(default_factory=list)
    shards: list[LocalGateShardSpec] = Field(default_factory=list)


class ReleaseCheck(BaseModel):
    """One normalized release-verification check."""

    key: str
    label: str | None = None
    status: str = "pending"
    summary: str = ""
    evidence_paths: list[str] = Field(default_factory=list)
    blocker: bool = False
    metadata: dict[str, Any] = Field(default_factory=dict)


class ShardReceipt(BaseModel):
    """Normalized execution outcome for one shard."""

    repo_id: str
    lane_id: str
    shard_id: str
    status: str = "planned"
    duration_seconds: float | None = None
    evidence_paths: list[str] = Field(default_factory=list)
    typed_incidents: list[str] = Field(default_factory=list)
    resource_class: str | None = None
    execution_target: str | None = None
    required_paths: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    cleanup_receipt_path: str | None = None
    service_slot: str | None = None
    release_blocking: bool = True
    gate_family: str | None = None
    blocking: bool = True
    required_category: str | None = None
    resource_reservation: ResourceReservation | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class ReleaseVerificationReceipt(BaseModel):
    """Normalized release verification contract consumed by owner-CI."""

    status: str = "pending"
    summary: str = ""
    required_checks: list[ReleaseCheck] = Field(default_factory=list)
    blocker_count: int = 0
    degraded_count: int = 0
    deployed_revision: str | None = None
    source: str = "zetherion_runtime_health"
    recorded_at: str | None = None
    delivery_canary_passed: bool | None = None
    security_canary_passed: bool | None = None
    queue_worker_healthy: bool | None = None
    runtime_status_persistence: bool | None = None
    skills_reachable: bool | None = None
    cgs_auth_flow_passed: bool | None = None
    cgs_login_redirect_passed: bool | None = None
    ai_ops_schema_passed: bool | None = None
    cgs_admin_ai_page_passed: bool | None = None
    cgs_owner_ci_reporting_passed: bool | None = None
    cgs_chatbot_runtime_proxy_passed: bool | None = None
    runtime_drift_zero: bool | None = None
    back_to_back_deploy_passed: bool | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


class RepoReadinessReceipt(BaseModel):
    """Repo-scoped readiness aggregation."""

    repo_id: str
    merge_ready: bool
    deploy_ready: bool
    failed_required_paths: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    shard_receipts: list[ShardReceipt] = Field(default_factory=list)
    release_verification: ReleaseVerificationReceipt | None = None
    category_complete: dict[str, bool] = Field(default_factory=dict)
    missing_categories: list[str] = Field(default_factory=list)
    host_capacity_snapshot: HostCapacitySnapshot | None = None
    summary: str = ""


class WorkspaceReadinessReceipt(BaseModel):
    """Workspace-wide aggregation across repo receipts."""

    merge_ready: bool
    deploy_ready: bool
    repo_receipts: list[RepoReadinessReceipt] = Field(default_factory=list)
    failed_required_paths: list[str] = Field(default_factory=list)
    missing_evidence: list[str] = Field(default_factory=list)
    category_complete: dict[str, bool] = Field(default_factory=dict)
    missing_categories: list[str] = Field(default_factory=list)
    host_capacity_snapshot: HostCapacitySnapshot | None = None
    summary: str = ""


class WorkerCertificationReceipt(BaseModel):
    """Normalized native/WSL worker certification contract."""

    status: str = "pending"
    summary: str = ""
    execution_backend: str = "wsl_docker"
    docker_backend: str = "wsl_docker"
    wsl_distribution: str | None = None
    workspace_root: str | None = None
    runtime_root: str | None = None
    required_checks: list[ReleaseCheck] = Field(default_factory=list)
    blocker_count: int = 0
    degraded_count: int = 0
    bootstrap_succeeded: bool | None = None
    registration_succeeded: bool | None = None
    heartbeat_succeeded: bool | None = None
    job_claim_succeeded: bool | None = None
    noop_job_succeeded: bool | None = None
    ci_test_run_succeeded: bool | None = None
    artifacts_submitted: bool | None = None
    cleanup_verified: bool | None = None
    status_publication_succeeded: bool | None = None
    missing_evidence: list[str] = Field(default_factory=list)
    metadata: dict[str, Any] = Field(default_factory=dict)


def normalize_release_verification_receipt(payload: dict[str, Any]) -> ReleaseVerificationReceipt:
    """Normalize legacy release verification payloads into the typed contract."""

    raw_checks = [
        dict(entry)
        for entry in list(payload.get("required_checks") or payload.get("checks") or [])
        if isinstance(entry, dict)
    ]
    normalized_checks: list[ReleaseCheck] = []
    explicit_fields = {field: _as_bool(payload.get(field)) for field in _REQUIRED_RELEASE_FIELDS}

    for entry in raw_checks:
        key = str(entry.get("key") or entry.get("name") or "").strip()
        if not key:
            continue
        status = str(entry.get("status") or entry.get("state") or "pending").strip().lower()
        check = ReleaseCheck(
            key=key,
            label=str(entry.get("label") or entry.get("title") or key).strip() or key,
            status=status or "pending",
            summary=str(entry.get("summary") or entry.get("description") or "").strip(),
            evidence_paths=[
                str(path).strip()
                for path in list(entry.get("evidence_paths") or entry.get("evidence") or [])
                if str(path).strip()
            ],
            blocker=bool(entry.get("blocker", False)),
            metadata=dict(entry.get("metadata") or {}),
        )
        normalized_checks.append(check)
        if key in explicit_fields and explicit_fields[key] is None:
            explicit_fields[key] = _check_passed(check.status)

    for field in _REQUIRED_RELEASE_FIELDS:
        if not any(check.key == field for check in normalized_checks):
            normalized_checks.append(
                ReleaseCheck(
                    key=field,
                    label=field.replace("_", " "),
                    status=_bool_to_check_status(explicit_fields[field]),
                )
            )

    blocker_count = int(payload.get("blocker_count") or 0)
    degraded_count = int(payload.get("degraded_count") or 0)
    if blocker_count <= 0:
        blocker_count = sum(
            1
            for check in normalized_checks
            if check.blocker or _check_passed(check.status) is False
        )
    if degraded_count <= 0:
        degraded_count = sum(1 for check in normalized_checks if check.status == "degraded")

    status = str(payload.get("status") or "").strip().lower()
    if not status:
        if blocker_count > 0:
            status = "deployed_but_unhealthy"
        elif degraded_count > 0:
            status = "degraded"
        elif all(_check_passed(check.status) is True for check in normalized_checks):
            status = "healthy"
        else:
            status = "pending"

    return ReleaseVerificationReceipt(
        status=status,
        summary=str(payload.get("summary") or "").strip(),
        required_checks=normalized_checks,
        blocker_count=blocker_count,
        degraded_count=degraded_count,
        deployed_revision=str(payload.get("deployed_revision") or "").strip() or None,
        source=str(payload.get("source") or "zetherion_runtime_health").strip(),
        recorded_at=str(payload.get("recorded_at") or "").strip() or None,
        delivery_canary_passed=explicit_fields["delivery_canary_passed"],
        security_canary_passed=explicit_fields["security_canary_passed"],
        queue_worker_healthy=explicit_fields["queue_worker_healthy"],
        runtime_status_persistence=explicit_fields["runtime_status_persistence"],
        skills_reachable=explicit_fields["skills_reachable"],
        cgs_auth_flow_passed=explicit_fields["cgs_auth_flow_passed"],
        cgs_login_redirect_passed=explicit_fields["cgs_login_redirect_passed"],
        ai_ops_schema_passed=explicit_fields["ai_ops_schema_passed"],
        cgs_admin_ai_page_passed=explicit_fields["cgs_admin_ai_page_passed"],
        cgs_owner_ci_reporting_passed=explicit_fields["cgs_owner_ci_reporting_passed"],
        cgs_chatbot_runtime_proxy_passed=explicit_fields["cgs_chatbot_runtime_proxy_passed"],
        runtime_drift_zero=explicit_fields["runtime_drift_zero"],
        back_to_back_deploy_passed=explicit_fields["back_to_back_deploy_passed"],
        missing_evidence=[
            str(path).strip()
            for path in list(payload.get("missing_evidence") or [])
            if str(path).strip()
        ],
        metadata=dict(payload.get("metadata") or {}),
    )


def normalize_worker_certification_receipt(payload: dict[str, Any]) -> WorkerCertificationReceipt:
    """Normalize legacy worker-certification payloads into the typed contract."""

    raw_checks = [
        dict(entry)
        for entry in list(payload.get("required_checks") or payload.get("checks") or [])
        if isinstance(entry, dict)
    ]
    normalized_checks: list[ReleaseCheck] = []
    explicit_fields = {
        field: _as_bool(payload.get(field)) for field in _REQUIRED_WORKER_CERTIFICATION_FIELDS
    }

    for entry in raw_checks:
        key = str(entry.get("key") or entry.get("name") or "").strip()
        if not key:
            continue
        status = str(entry.get("status") or entry.get("state") or "pending").strip().lower()
        check = ReleaseCheck(
            key=key,
            label=str(entry.get("label") or entry.get("title") or key).strip() or key,
            status=status or "pending",
            summary=str(entry.get("summary") or entry.get("description") or "").strip(),
            evidence_paths=[
                str(path).strip()
                for path in list(entry.get("evidence_paths") or entry.get("evidence") or [])
                if str(path).strip()
            ],
            blocker=bool(entry.get("blocker", False)),
            metadata=dict(entry.get("metadata") or {}),
        )
        normalized_checks.append(check)
        if key in explicit_fields and explicit_fields[key] is None:
            explicit_fields[key] = _check_passed(check.status)

    for field in _REQUIRED_WORKER_CERTIFICATION_FIELDS:
        if not any(check.key == field for check in normalized_checks):
            normalized_checks.append(
                ReleaseCheck(
                    key=field,
                    label=field.replace("_", " "),
                    status=_bool_to_check_status(explicit_fields[field]),
                )
            )

    blocker_count = int(payload.get("blocker_count") or 0)
    degraded_count = int(payload.get("degraded_count") or 0)
    if blocker_count <= 0:
        blocker_count = sum(
            1
            for check in normalized_checks
            if check.blocker or _check_passed(check.status) is False
        )
    if degraded_count <= 0:
        degraded_count = sum(1 for check in normalized_checks if check.status == "degraded")

    status = str(payload.get("status") or "").strip().lower()
    if not status:
        if blocker_count > 0:
            status = "failed"
        elif degraded_count > 0:
            status = "degraded"
        elif all(_check_passed(check.status) is True for check in normalized_checks):
            status = "healthy"
        else:
            status = "pending"

    return WorkerCertificationReceipt(
        status=status,
        summary=str(payload.get("summary") or "").strip(),
        execution_backend=(
            str(payload.get("execution_backend") or "wsl_docker").strip()
            or "wsl_docker"
        ),
        docker_backend=(
            str(payload.get("docker_backend") or "wsl_docker").strip()
            or "wsl_docker"
        ),
        wsl_distribution=str(payload.get("wsl_distribution") or "").strip() or None,
        workspace_root=str(payload.get("workspace_root") or "").strip() or None,
        runtime_root=str(payload.get("runtime_root") or "").strip() or None,
        required_checks=normalized_checks,
        blocker_count=blocker_count,
        degraded_count=degraded_count,
        bootstrap_succeeded=explicit_fields["bootstrap_succeeded"],
        registration_succeeded=explicit_fields["registration_succeeded"],
        heartbeat_succeeded=explicit_fields["heartbeat_succeeded"],
        job_claim_succeeded=explicit_fields["job_claim_succeeded"],
        noop_job_succeeded=explicit_fields["noop_job_succeeded"],
        ci_test_run_succeeded=explicit_fields["ci_test_run_succeeded"],
        artifacts_submitted=explicit_fields["artifacts_submitted"],
        cleanup_verified=explicit_fields["cleanup_verified"],
        status_publication_succeeded=explicit_fields["status_publication_succeeded"],
        missing_evidence=[
            str(path).strip()
            for path in list(payload.get("missing_evidence") or [])
            if str(path).strip()
        ],
        metadata=dict(payload.get("metadata") or {}),
    )


def normalize_shard_receipt(repo_id: str, shard: dict[str, Any]) -> ShardReceipt:
    """Normalize one persisted shard row into a receipt."""

    metadata = dict(shard.get("metadata") or {})
    result = dict(shard.get("result") or {})
    error = dict(shard.get("error") or {})
    evidence_paths = [
        str(path).strip()
        for path in list(result.get("evidence_paths") or result.get("artifacts") or [])
        if str(path).strip()
    ]
    typed_incidents = [
        str(code).strip()
        for code in list(result.get("typed_incidents") or error.get("typed_incidents") or [])
        if str(code).strip()
    ]
    missing_evidence = [
        str(path).strip()
        for path in list(result.get("missing_evidence") or [])
        if str(path).strip()
    ]
    if not evidence_paths:
        expected = list(shard.get("artifact_contract", {}).get("expects") or [])
        if expected and str(shard.get("status") or "").strip().lower() == "succeeded":
            missing_evidence = [str(item).strip() for item in expected if str(item).strip()]

    return ShardReceipt(
        repo_id=repo_id,
        lane_id=str(shard.get("lane_id") or "").strip(),
        shard_id=str(shard.get("shard_id") or shard.get("lane_id") or "").strip(),
        status=str(shard.get("status") or "planned").strip().lower(),
        duration_seconds=result.get("duration_seconds"),
        evidence_paths=evidence_paths,
        typed_incidents=typed_incidents,
        resource_class=str(metadata.get("resource_class") or "").strip() or None,
        execution_target=str(shard.get("execution_target") or "").strip() or None,
        required_paths=[
            str(path).strip()
            for path in list(
                metadata.get("covered_required_paths")
                or metadata.get("required_paths")
                or []
            )
            if str(path).strip()
        ],
        missing_evidence=missing_evidence,
        cleanup_receipt_path=str(result.get("cleanup_receipt_path") or "").strip() or None,
        service_slot=str(metadata.get("service_slot") or "").strip() or None,
        release_blocking=bool(
            metadata.get("release_blocking", result.get("release_blocking", True))
        ),
        gate_family=str(metadata.get("gate_family") or metadata.get("gate_kind") or "").strip()
        or None,
        blocking=bool(metadata.get("blocking", True)),
        required_category=str(metadata.get("required_category") or "").strip() or None,
        resource_reservation=(
            ResourceReservation.model_validate(
                dict(metadata.get("resource_reservation") or {})
            )
            if isinstance(metadata.get("resource_reservation"), dict)
            else None
        ),
        metadata=metadata,
    )


def _compiled_plan_payload(run: dict[str, Any]) -> dict[str, Any]:
    plan = dict(run.get("plan") or {})
    compiled_plan = dict(plan.get("compiled_plan") or {})
    compiled_payload = dict(compiled_plan.get("plan") or {})
    return compiled_payload or plan


def _required_gate_categories(repo: dict[str, Any], run: dict[str, Any]) -> list[str]:
    plan = _compiled_plan_payload(run)
    plan_categories = [
        str(category).strip().lower()
        for category in list(plan.get("required_gate_categories") or [])
        if str(category).strip()
    ]
    if plan_categories:
        return sorted(dict.fromkeys(plan_categories))

    metadata = dict(repo.get("metadata") or {})
    repo_categories = [
        str(category).strip().lower()
        for category in list(metadata.get("required_gate_categories") or [])
        if str(category).strip()
    ]
    if repo_categories:
        return sorted(dict.fromkeys(repo_categories))

    plan_mode = str(plan.get("mode") or run.get("metadata", {}).get("mode") or "").strip().lower()
    if plan_mode == "certification":
        return ["e2e", "integration", "security", "static", "unit"]
    return []


def _host_capacity_snapshot(
    repo: dict[str, Any],
    run: dict[str, Any],
) -> HostCapacitySnapshot | None:
    plan = _compiled_plan_payload(run)
    host_policy = dict(
        plan.get("host_capacity_policy")
        or run.get("metadata", {}).get("host_capacity_policy")
        or {}
    )
    resource_budget = dict(plan.get("resource_budget") or host_policy.get("resource_budget") or {})
    if not resource_budget and not host_policy:
        return None
    return HostCapacitySnapshot(
        host_id=str(host_policy.get("host_id") or "windows-owner-ci").strip() or "windows-owner-ci",
        cpu_slots_total=int(resource_budget.get("cpu") or 0),
        cpu_slots_used=int(host_policy.get("cpu_slots_used") or 0),
        service_slots_total=int(resource_budget.get("service") or 0),
        service_slots_used=int(host_policy.get("service_slots_used") or 0),
        serial_slots_total=int(resource_budget.get("serial") or 0),
        serial_slots_used=int(host_policy.get("serial_slots_used") or 0),
        runtime_headroom_reserved=bool(host_policy.get("reserve_runtime_headroom", True)),
        blocking_reasons=[
            str(reason).strip()
            for reason in list(host_policy.get("blocking_reasons") or [])
            if str(reason).strip()
        ],
        metadata={
            "repo_id": str(repo.get("repo_id") or run.get("repo_id") or "").strip() or None,
            **host_policy,
        },
    )


def overlay_local_readiness_shards(
    shard_receipts: list[ShardReceipt],
    local_receipt: RepoReadinessReceipt | None,
) -> list[ShardReceipt]:
    """Treat matching local readiness lanes as satisfying pending local shards."""

    if local_receipt is None:
        return shard_receipts

    local_by_lane = {
        receipt.lane_id: receipt
        for receipt in local_receipt.shard_receipts
        if receipt.lane_id
    }
    successful_local_receipts = [
        receipt
        for receipt in local_receipt.shard_receipts
        if receipt.status in {"succeeded", "skipped"}
    ]
    successful_local_paths = {
        path
        for receipt in successful_local_receipts
        for path in (receipt.required_paths or [receipt.lane_id])
        if path
    }
    failed_local_paths = {
        path
        for path in local_receipt.failed_required_paths
        if path
    }

    overlaid: list[ShardReceipt] = []
    for receipt in shard_receipts:
        if receipt.execution_target != "local_mac" or receipt.status not in {
            "planned",
            "queued_local",
        }:
            overlaid.append(receipt)
            continue

        matched = local_by_lane.get(receipt.lane_id)
        receipt_paths = [path for path in receipt.required_paths if path]
        contributing_receipts: list[ShardReceipt] = []
        if matched is None and receipt_paths:
            matching_paths = set(receipt_paths)
            if matching_paths and matching_paths.issubset(successful_local_paths):
                contributing_receipts = [
                    candidate
                    for candidate in successful_local_receipts
                    if set(candidate.required_paths or [candidate.lane_id]) & matching_paths
                ]
                matched = next(
                    (
                        candidate
                        for candidate in contributing_receipts
                        if set(candidate.required_paths or [candidate.lane_id]) == matching_paths
                    ),
                    None,
                )
                if matched is None and contributing_receipts:
                    evidence_paths = sorted(
                        {
                            path
                            for candidate in contributing_receipts
                            for path in candidate.evidence_paths
                            if path
                        }
                    )
                    typed_incidents = sorted(
                        {
                            code
                            for candidate in contributing_receipts
                            for code in candidate.typed_incidents
                            if code
                        }
                    )
                    missing_evidence = sorted(
                        {
                            path
                            for candidate in contributing_receipts
                            for path in candidate.missing_evidence
                            if path
                        }
                    )
                    matched = ShardReceipt(
                        repo_id=receipt.repo_id,
                        lane_id=receipt.lane_id,
                        shard_id=receipt.shard_id,
                        status="succeeded",
                        duration_seconds=sum(
                            float(candidate.duration_seconds or 0.0)
                            for candidate in contributing_receipts
                        )
                        or receipt.duration_seconds,
                        evidence_paths=evidence_paths,
                        typed_incidents=typed_incidents,
                        resource_class=receipt.resource_class,
                        execution_target=receipt.execution_target,
                        required_paths=receipt_paths,
                        missing_evidence=missing_evidence,
                        cleanup_receipt_path=next(
                            (
                                candidate.cleanup_receipt_path
                                for candidate in contributing_receipts
                                if candidate.cleanup_receipt_path
                            ),
                            receipt.cleanup_receipt_path,
                        ),
                        service_slot=receipt.service_slot,
                        release_blocking=receipt.release_blocking,
                        metadata={
                            "satisfied_by": "local_readiness_receipt",
                            "local_receipt_lane_ids": [
                                candidate.lane_id for candidate in contributing_receipts
                            ],
                        },
                    )

        if matched is None:
            overlaid.append(receipt)
            continue

        if receipt_paths and any(path in failed_local_paths for path in receipt_paths):
            overlaid.append(receipt)
            continue

        metadata = {
            **receipt.metadata,
            **matched.metadata,
            "satisfied_by": "local_readiness_receipt",
            "local_receipt_summary": local_receipt.summary,
            "local_receipt_lane_id": matched.lane_id,
        }
        overlaid.append(
            receipt.model_copy(
                update={
                    "status": matched.status,
                    "duration_seconds": matched.duration_seconds or receipt.duration_seconds,
                    "evidence_paths": matched.evidence_paths or receipt.evidence_paths,
                    "typed_incidents": matched.typed_incidents or receipt.typed_incidents,
                    "required_paths": matched.required_paths or receipt.required_paths,
                    "missing_evidence": matched.missing_evidence,
                    "cleanup_receipt_path": (
                        matched.cleanup_receipt_path or receipt.cleanup_receipt_path
                    ),
                    "service_slot": matched.service_slot or receipt.service_slot,
                    "metadata": metadata,
                }
            )
        )
    return overlaid


def build_repo_readiness_receipt(
    *,
    repo: dict[str, Any],
    run: dict[str, Any],
    review: dict[str, Any],
    release_receipt: dict[str, Any],
    local_receipt: RepoReadinessReceipt | None = None,
) -> RepoReadinessReceipt:
    """Aggregate shard and release state into one repo readiness receipt."""

    repo_id = str(repo.get("repo_id") or run.get("repo_id") or "").strip()
    shard_receipts = [
        normalize_shard_receipt(repo_id, dict(shard))
        for shard in list(run.get("shards") or [])
        if isinstance(shard, dict)
    ]
    shard_receipts = overlay_local_readiness_shards(shard_receipts, local_receipt)
    effective_release_receipt = dict(release_receipt or {})
    if not effective_release_receipt and local_receipt is not None:
        local_release = local_receipt.release_verification
        if local_release is not None:
            effective_release_receipt = local_release.model_dump(mode="json")
    release = normalize_release_verification_receipt(effective_release_receipt)

    disconnected_statuses = {"queued_local", "running_disconnected", "awaiting_sync"}
    failed_statuses = {"failed", "cancelled"}
    pending_statuses = {
        "planned",
        "queued_local",
        "running",
        "awaiting_sync",
        "running_disconnected",
    }

    failed_required_paths = sorted(
        {
            required_path or shard.lane_id
            for shard in shard_receipts
            if shard.status in failed_statuses
            for required_path in (shard.required_paths or [shard.lane_id])
        }
    )
    missing_evidence = sorted(
        {
            path
            for shard in shard_receipts
            for path in shard.missing_evidence
            if path
        }
        | {path for path in release.missing_evidence if path}
    )
    required_categories = _required_gate_categories(repo, run)
    host_capacity_snapshot = _host_capacity_snapshot(repo, run)
    category_complete = {
        category: any(
            shard.gate_family == category
            and shard.blocking
            and shard.status in {"succeeded", "skipped"}
            for shard in shard_receipts
        )
        for category in required_categories
    }
    missing_categories = sorted(
        category for category, complete in category_complete.items() if not complete
    )

    merge_ready = not bool(review.get("merge_blocked", True))
    if merge_ready and any(shard.status in failed_statuses for shard in shard_receipts):
        merge_ready = False
    deploy_ready = release.status in {"healthy", "success"} and release.blocker_count <= 0

    if any(shard.status in disconnected_statuses for shard in shard_receipts):
        merge_ready = False
    if any(shard.status in pending_statuses for shard in shard_receipts):
        deploy_ready = False
    if missing_categories:
        merge_ready = False
        deploy_ready = False

    summary_bits: list[str] = []
    if not merge_ready:
        summary_bits.append("merge blocked")
    if not deploy_ready:
        summary_bits.append("deploy not ready")
    if failed_required_paths:
        summary_bits.append(f"failed paths: {', '.join(failed_required_paths[:4])}")
    if missing_evidence:
        summary_bits.append(f"missing evidence: {', '.join(missing_evidence[:4])}")
    if missing_categories:
        summary_bits.append(f"missing categories: {', '.join(missing_categories[:4])}")

    return RepoReadinessReceipt(
        repo_id=repo_id,
        merge_ready=merge_ready,
        deploy_ready=deploy_ready,
        failed_required_paths=failed_required_paths,
        missing_evidence=missing_evidence,
        shard_receipts=shard_receipts,
        release_verification=release,
        category_complete=category_complete,
        missing_categories=missing_categories,
        host_capacity_snapshot=host_capacity_snapshot,
        summary="; ".join(summary_bits) or "ready",
    )


def build_workspace_readiness_receipt(
    repo_receipts: list[RepoReadinessReceipt],
) -> WorkspaceReadinessReceipt:
    """Aggregate repo-level receipts into one workspace-level summary."""

    failed_required_paths = sorted(
        {
            path
            for receipt in repo_receipts
            for path in receipt.failed_required_paths
            if path
        }
    )
    missing_evidence = sorted(
        {
            path
            for receipt in repo_receipts
            for path in receipt.missing_evidence
            if path
        }
    )
    all_categories = sorted(
        {
            category
            for receipt in repo_receipts
            for category in receipt.category_complete
        }
    )
    category_complete = {
        category: all(receipt.category_complete.get(category, False) for receipt in repo_receipts)
        for category in all_categories
    }
    missing_categories = sorted(
        {
            category
            for receipt in repo_receipts
            for category in receipt.missing_categories
        }
    )
    merge_ready = all(receipt.merge_ready for receipt in repo_receipts) if repo_receipts else False
    deploy_ready = (
        all(receipt.deploy_ready for receipt in repo_receipts) if repo_receipts else False
    )
    host_capacity_snapshot = next(
        (
            receipt.host_capacity_snapshot
            for receipt in repo_receipts
            if receipt.host_capacity_snapshot is not None
        ),
        None,
    )
    return WorkspaceReadinessReceipt(
        merge_ready=merge_ready,
        deploy_ready=deploy_ready,
        repo_receipts=repo_receipts,
        failed_required_paths=failed_required_paths,
        missing_evidence=missing_evidence,
        category_complete=category_complete,
        missing_categories=missing_categories,
        host_capacity_snapshot=host_capacity_snapshot,
        summary=(
            "ready"
            if (
                merge_ready
                and deploy_ready
                and not failed_required_paths
                and not missing_evidence
                and not missing_categories
            )
            else "workspace has blockers"
        ),
    )
