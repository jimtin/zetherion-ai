"""Owner-scoped CI observability skill."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger
from zetherion_ai.owner_ci import (
    OwnerCiStorage,
    ResourceReservation,
    SchedulerCandidate,
    SchedulerHostSummary,
    SchedulerOverview,
    StorageBudgetPolicy,
    StorageCategoryUsage,
    StorageCleanupReceipt,
    StorageInventorySnapshot,
    StoragePressureIncident,
)
from zetherion_ai.owner_ci.coaching_synthesis import CoachingSynthesizer
from zetherion_ai.skills.base import Skill, SkillMetadata, SkillRequest, SkillResponse
from zetherion_ai.skills.github.client import GitHubAPIError, GitHubClient
from zetherion_ai.skills.permissions import Permission, PermissionSet

log = get_logger("zetherion_ai.skills.ci_observer")
_ACTIVE_SHARD_STATUSES = {"running", "claimed", "awaiting_sync", "running_disconnected"}
_PENDING_SHARD_STATUSES = {"queued_local", "planned"}
_DEFAULT_LOW_DISK_FREE_BYTES = 21_474_836_480
_DEFAULT_TARGET_FREE_BYTES = 42_949_672_960
_DEFAULT_ARTIFACT_RETENTION_HOURS = 24
_DEFAULT_LOG_RETENTION_DAYS = 7


def _normalize_owner_id(request: SkillRequest) -> str:
    for candidate in (
        request.context.get("owner_id"),
        request.context.get("operator_id"),
        request.context.get("actor_sub"),
        request.user_id,
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return "owner"


def _repo_scope_id(owner_id: str, repo_id: str) -> str:
    return f"owner:{owner_id}:repo:{repo_id}"


def _parse_github_repo(value: Any) -> tuple[str, str] | None:
    candidate = str(value or "").strip()
    if "/" not in candidate:
        return None
    owner, repo = candidate.split("/", 1)
    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        return None
    return owner, repo


def _normalize_security_severity(value: Any) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in {"critical", "high", "medium", "moderate", "low"}:
        return "medium" if candidate == "moderate" else candidate
    if candidate == "error":
        return "high"
    if candidate == "warning":
        return "medium"
    if candidate == "note":
        return "low"
    return "unknown"


def _increment_severity_bucket(target: dict[str, int], severity: str) -> None:
    if severity not in target:
        target[severity] = 0
    target[severity] += 1


def _summarize_dependabot_alerts(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    ecosystems: dict[str, int] = {}
    for alert in alerts:
        vulnerability = dict(alert.get("security_vulnerability") or {})
        severity = _normalize_security_severity(vulnerability.get("severity"))
        _increment_severity_bucket(counts, severity)
        package = dict(vulnerability.get("package") or {})
        ecosystem = str(package.get("ecosystem") or "").strip().lower()
        if ecosystem:
            ecosystems[ecosystem] = ecosystems.get(ecosystem, 0) + 1
    return {"open_count": len(alerts), "severity_counts": counts, "ecosystems": ecosystems}


def _summarize_code_scanning_alerts(alerts: list[dict[str, Any]]) -> dict[str, Any]:
    counts = {"critical": 0, "high": 0, "medium": 0, "low": 0, "unknown": 0}
    tools: dict[str, int] = {}
    for alert in alerts:
        rule = dict(alert.get("rule") or {})
        severity = _normalize_security_severity(
            rule.get("security_severity_level") or rule.get("severity")
        )
        _increment_severity_bucket(counts, severity)
        tool_name = str(dict(alert.get("tool") or {}).get("name") or "").strip().lower()
        if tool_name:
            tools[tool_name] = tools.get(tool_name, 0) + 1
    return {"open_count": len(alerts), "severity_counts": counts, "tools": tools}


async def _build_github_security_summary(
    repo_profiles: list[dict[str, Any]],
) -> dict[str, Any]:
    token_secret = getattr(get_settings(), "github_token", None)
    token = token_secret.get_secret_value().strip() if token_secret is not None else ""
    if not token:
        return {
            "status": "unavailable",
            "available": False,
            "blocking": False,
            "reason": "github_token_missing",
            "summary": (
                "GitHub security alerts could not be checked because " "GITHUB_TOKEN is missing."
            ),
            "totals": {
                "open_dependabot": 0,
                "open_code_scanning": 0,
                "critical_or_high": 0,
            },
            "repos": [],
        }

    client = GitHubClient(token)
    repo_summaries: list[dict[str, Any]] = []
    totals = {"open_dependabot": 0, "open_code_scanning": 0, "critical_or_high": 0}
    try:
        for repo in repo_profiles:
            repo_id = str(repo.get("repo_id") or "").strip()
            github_repo = _parse_github_repo(repo.get("github_repo"))
            if github_repo is None:
                continue
            owner, repo_name = github_repo
            errors: list[str] = []
            try:
                dependabot_alerts = await client.list_dependabot_alerts(owner, repo_name)
            except GitHubAPIError as exc:
                dependabot_alerts = []
                errors.append(f"dependabot:{exc.status_code or 'error'}")
            try:
                code_scanning_alerts = await client.list_code_scanning_alerts(owner, repo_name)
            except GitHubAPIError as exc:
                code_scanning_alerts = []
                errors.append(f"code_scanning:{exc.status_code or 'error'}")

            dependabot_summary = _summarize_dependabot_alerts(dependabot_alerts)
            code_scanning_summary = _summarize_code_scanning_alerts(code_scanning_alerts)
            critical_or_high = (
                int(dependabot_summary["severity_counts"]["critical"])
                + int(dependabot_summary["severity_counts"]["high"])
                + int(code_scanning_summary["severity_counts"]["critical"])
                + int(code_scanning_summary["severity_counts"]["high"])
            )
            totals["open_dependabot"] += int(dependabot_summary["open_count"])
            totals["open_code_scanning"] += int(code_scanning_summary["open_count"])
            totals["critical_or_high"] += critical_or_high
            blocking = critical_or_high > 0
            degraded = (
                int(dependabot_summary["open_count"]) > 0
                or int(code_scanning_summary["open_count"]) > 0
                or bool(errors)
            )
            repo_summaries.append(
                {
                    "repo_id": repo_id,
                    "github_repo": f"{owner}/{repo_name}",
                    "status": "blocked" if blocking else ("degraded" if degraded else "healthy"),
                    "blocking": blocking,
                    "errors": errors,
                    "dependabot": dependabot_summary,
                    "code_scanning": code_scanning_summary,
                }
            )
    finally:
        await client.close()

    status = "healthy"
    if totals["critical_or_high"] > 0:
        status = "blocked"
    elif totals["open_dependabot"] > 0 or totals["open_code_scanning"] > 0:
        status = "degraded"

    return {
        "status": status,
        "available": True,
        "blocking": status == "blocked",
        "summary": (
            "GitHub security alerts include blocking high-severity findings."
            if status == "blocked"
            else (
                "GitHub security alerts require triage."
                if status == "degraded"
                else "GitHub security alerts are clear for the configured repos."
            )
        ),
        "totals": totals,
        "repos": repo_summaries,
    }


def _resource_reservation_for_shard(
    repo_id: str,
    shard: dict[str, Any],
) -> ResourceReservation:
    metadata = dict(shard.get("metadata") or {})
    payload = dict(metadata.get("resource_reservation") or {})
    if payload:
        payload.setdefault("repo_id", repo_id or None)
        payload.setdefault("shard_id", str(shard.get("shard_id") or "").strip() or None)
        return ResourceReservation.model_validate(payload)
    return ResourceReservation.model_validate(
        {
            "repo_id": repo_id or None,
            "shard_id": str(shard.get("shard_id") or "").strip() or None,
            "resource_class": str(metadata.get("resource_class") or "cpu").strip() or "cpu",
            "units": max(1, int(metadata.get("resource_units") or metadata.get("units") or 1)),
            "parallel_group": str(metadata.get("parallel_group") or "").strip() or None,
            "workspace_root": str(metadata.get("workspace_root") or "").strip() or None,
            "metadata": {
                "lane_id": str(shard.get("lane_id") or "").strip() or None,
                "execution_target": str(shard.get("execution_target") or "").strip() or None,
            },
        }
    )


def _resource_available(
    resource_budget: dict[str, Any],
    resource_usage: dict[str, Any],
) -> dict[str, int]:
    return {
        resource_class: max(
            0,
            int(resource_budget.get(resource_class) or 0)
            - int(resource_usage.get(resource_class) or 0),
        )
        for resource_class in ("cpu", "service", "serial")
    }


def _host_blocking_reasons(
    resource_budget: dict[str, Any],
    resource_usage: dict[str, Any],
) -> list[str]:
    blocking_reasons: list[str] = []
    for resource_class in ("cpu", "service", "serial"):
        total = int(resource_budget.get(resource_class) or 0)
        used = int(resource_usage.get(resource_class) or 0)
        if total and used >= total:
            blocking_reasons.append(f"{resource_class} budget exhausted ({used}/{total})")
    return blocking_reasons


def _active_capacity_by_host(
    runs: list[dict[str, object]],
) -> dict[str, dict[str, object]]:
    hosts: dict[str, dict[str, object]] = {}
    for run in runs:
        repo_id = str(run.get("repo_id") or "").strip()
        plan = dict(run.get("plan") or {})
        metadata = dict(run.get("metadata") or {})
        host_policy = dict(
            plan.get("host_capacity_policy") or metadata.get("host_capacity_policy") or {}
        )
        host_id = (
            str(host_policy.get("host_id") or "windows-owner-ci").strip() or "windows-owner-ci"
        )
        host_entry = hosts.setdefault(
            host_id,
            {
                "host_id": host_id,
                "resource_budget": {"cpu": 0, "service": 0, "serial": 0},
                "resource_usage": {"cpu": 0, "service": 0, "serial": 0},
                "busy_parallel_groups": set(),
                "active_runs": set(),
                "active_shards": 0,
                "runtime_headroom_reserved": bool(
                    host_policy.get("reserve_runtime_headroom", True)
                ),
                "repo_ids": set(),
                "admission_mode": str(host_policy.get("admission_mode") or "").strip()
                or "dynamic_resource_based",
            },
        )
        resource_budget = dict(host_policy.get("resource_budget") or {})
        for resource_class in ("cpu", "service", "serial"):
            host_entry["resource_budget"][resource_class] = max(
                int(host_entry["resource_budget"].get(resource_class) or 0),
                int(resource_budget.get(resource_class) or 0),
            )
        host_entry["repo_ids"].add(repo_id)
        run_has_active_shard = False
        for shard in list(run.get("shards") or []):
            status = str(shard.get("status") or "").strip().lower()
            if status not in _ACTIVE_SHARD_STATUSES:
                continue
            run_has_active_shard = True
            shard_metadata = dict(shard.get("metadata") or {})
            reservation = dict(shard_metadata.get("resource_reservation") or {})
            resource_class = (
                str(
                    reservation.get("resource_class")
                    or shard_metadata.get("resource_class")
                    or "cpu"
                ).strip()
                or "cpu"
            )
            units = max(1, int(reservation.get("units") or 1))
            host_entry["resource_usage"][resource_class] = (
                int(host_entry["resource_usage"].get(resource_class) or 0) + units
            )
            parallel_group = str(
                reservation.get("parallel_group") or shard_metadata.get("parallel_group") or ""
            ).strip()
            if parallel_group:
                host_entry["busy_parallel_groups"].add(parallel_group)
            host_entry["active_shards"] = int(host_entry["active_shards"] or 0) + 1
        if run_has_active_shard:
            host_entry["active_runs"].add(str(run.get("run_id") or "").strip())

    return hosts


def _round_robin_candidates_by_repo(
    pending_by_repo: dict[str, list[dict[str, Any]]],
) -> list[dict[str, Any]]:
    queue = {
        repo_id: list(candidates)
        for repo_id, candidates in sorted(pending_by_repo.items())
        if list(candidates)
    }
    ordered: list[dict[str, Any]] = []
    while queue:
        exhausted: list[str] = []
        for repo_id in list(queue):
            candidates = queue[repo_id]
            if not candidates:
                exhausted.append(repo_id)
                continue
            ordered.append(candidates.pop(0))
            if not candidates:
                exhausted.append(repo_id)
        for repo_id in exhausted:
            queue.pop(repo_id, None)
    return ordered


def _scheduler_overview(runs: list[dict[str, Any]]) -> SchedulerOverview:
    hosts: dict[str, dict[str, Any]] = {}
    for run in runs:
        repo_id = str(run.get("repo_id") or "").strip()
        run_id = str(run.get("run_id") or "").strip()
        plan = dict(run.get("plan") or {})
        metadata = dict(run.get("metadata") or {})
        host_policy = dict(
            plan.get("host_capacity_policy") or metadata.get("host_capacity_policy") or {}
        )
        host_id = (
            str(host_policy.get("host_id") or "windows-owner-ci").strip() or "windows-owner-ci"
        )
        host_entry = hosts.setdefault(
            host_id,
            {
                "host_id": host_id,
                "resource_budget": {"cpu": 0, "service": 0, "serial": 0},
                "resource_usage": {"cpu": 0, "service": 0, "serial": 0},
                "busy_parallel_groups": set(),
                "active_runs": set(),
                "active_shards": 0,
                "pending_runs": set(),
                "pending_by_repo": {},
                "runtime_headroom_reserved": bool(
                    host_policy.get("reserve_runtime_headroom", True)
                ),
                "repo_ids": set(),
                "admission_mode": str(host_policy.get("admission_mode") or "").strip()
                or "dynamic_resource_based",
                "windows_execution_mode": str(
                    host_policy.get("windows_execution_mode") or ""
                ).strip()
                or None,
                "workspace_root": str(host_policy.get("workspace_root") or "").strip() or None,
                "runtime_root": str(host_policy.get("runtime_root") or "").strip() or None,
            },
        )
        resource_budget = dict(host_policy.get("resource_budget") or {})
        for resource_class in ("cpu", "service", "serial"):
            host_entry["resource_budget"][resource_class] = max(
                int(host_entry["resource_budget"].get(resource_class) or 0),
                int(resource_budget.get(resource_class) or 0),
            )
        if repo_id:
            host_entry["repo_ids"].add(repo_id)
        for shard in list(run.get("shards") or []):
            status = str(shard.get("status") or "").strip().lower()
            reservation = _resource_reservation_for_shard(repo_id, shard)
            resource_class = reservation.resource_class.strip() or "cpu"
            units = max(1, int(reservation.units or 1))
            if status in _ACTIVE_SHARD_STATUSES:
                host_entry["resource_usage"][resource_class] = (
                    int(host_entry["resource_usage"].get(resource_class) or 0) + units
                )
                if reservation.parallel_group:
                    host_entry["busy_parallel_groups"].add(reservation.parallel_group)
                host_entry["active_shards"] = int(host_entry["active_shards"] or 0) + 1
                if run_id:
                    host_entry["active_runs"].add(run_id)
                continue
            if status not in _PENDING_SHARD_STATUSES:
                continue
            if run_id:
                host_entry["pending_runs"].add(run_id)
            host_entry["pending_by_repo"].setdefault(repo_id or "unknown", []).append(
                {
                    "run_id": run_id or None,
                    "repo_id": repo_id or None,
                    "shard_id": str(shard.get("shard_id") or "").strip() or None,
                    "lane_id": str(shard.get("lane_id") or "").strip() or None,
                    "reservation": reservation,
                    "metadata": {
                        "status": status,
                        "execution_target": str(shard.get("execution_target") or "").strip()
                        or None,
                    },
                }
            )

    host_summaries: list[SchedulerHostSummary] = []
    total_pending = 0
    total_admitted = 0
    total_blocked = 0
    for host_id, host_entry in sorted(hosts.items()):
        resource_budget = dict(host_entry.get("resource_budget") or {})
        resource_usage = dict(host_entry.get("resource_usage") or {})
        current_blockers = _host_blocking_reasons(resource_budget, resource_usage)
        planned_usage = dict(resource_usage)
        active_parallel_groups = set(host_entry.get("busy_parallel_groups") or set())
        queue_order = _round_robin_candidates_by_repo(dict(host_entry.get("pending_by_repo") or {}))
        admitted_candidates: list[SchedulerCandidate] = []
        blocked_candidates: list[SchedulerCandidate] = []
        for candidate in queue_order:
            reservation = candidate["reservation"]
            resource_class = reservation.resource_class.strip() or "cpu"
            units = max(1, int(reservation.units or 1))
            total_slots = int(resource_budget.get(resource_class) or 0)
            current_usage = int(planned_usage.get(resource_class) or 0)
            blocking_reasons: list[str] = []
            if total_slots and current_usage + units > total_slots:
                blocking_reasons.append(
                    f"{resource_class} budget exhausted ({current_usage}/{total_slots})"
                )
            if reservation.parallel_group and reservation.parallel_group in active_parallel_groups:
                blocking_reasons.append(
                    f"parallel group `{reservation.parallel_group}` already active"
                )
            candidate_record = SchedulerCandidate(
                run_id=candidate.get("run_id"),
                repo_id=candidate.get("repo_id"),
                shard_id=candidate.get("shard_id"),
                lane_id=candidate.get("lane_id"),
                resource_class=resource_class,
                units=units,
                parallel_group=reservation.parallel_group,
                admitted=not blocking_reasons,
                blocking_reasons=blocking_reasons,
                metadata=dict(candidate.get("metadata") or {}),
            )
            if candidate_record.admitted:
                planned_usage[resource_class] = current_usage + units
                if reservation.parallel_group:
                    active_parallel_groups.add(reservation.parallel_group)
                admitted_candidates.append(candidate_record)
            else:
                blocked_candidates.append(candidate_record)

        blocking_reasons = list(current_blockers)
        if queue_order and not admitted_candidates:
            for candidate in blocked_candidates:
                for reason in candidate.blocking_reasons:
                    if reason not in blocking_reasons:
                        blocking_reasons.append(reason)
        summary = SchedulerHostSummary(
            host_id=host_id,
            admission_mode=str(host_entry.get("admission_mode") or "").strip()
            or "dynamic_resource_based",
            fairness_mode="round_robin_by_repo",
            resource_budget={key: int(value or 0) for key, value in resource_budget.items()},
            resource_usage={key: int(value or 0) for key, value in resource_usage.items()},
            resource_available=_resource_available(resource_budget, resource_usage),
            runtime_headroom_reserved=bool(host_entry.get("runtime_headroom_reserved", True)),
            active_run_count=len(set(host_entry.get("active_runs") or set())),
            active_shard_count=int(host_entry.get("active_shards") or 0),
            pending_run_count=len(set(host_entry.get("pending_runs") or set())),
            pending_shard_count=len(queue_order),
            busy_parallel_groups=sorted(
                str(group).strip()
                for group in set(host_entry.get("busy_parallel_groups") or set())
                if str(group).strip()
            ),
            repo_ids=sorted(
                str(repo_id).strip()
                for repo_id in set(host_entry.get("repo_ids") or set())
                if str(repo_id).strip()
            ),
            blocking_reasons=blocking_reasons,
            admitted_candidates=admitted_candidates,
            blocked_candidates=blocked_candidates,
            metadata={
                "windows_execution_mode": host_entry.get("windows_execution_mode"),
                "workspace_root": host_entry.get("workspace_root"),
                "runtime_root": host_entry.get("runtime_root"),
                "can_admit_more": bool(admitted_candidates),
            },
        )
        total_pending += summary.pending_shard_count
        total_admitted += len(summary.admitted_candidates)
        total_blocked += len(summary.blocked_candidates)
        host_summaries.append(summary)

    return SchedulerOverview(
        generated_at=datetime.now(UTC).isoformat(),
        fairness_mode="round_robin_by_repo",
        hosts=host_summaries,
        totals={
            "host_count": len(host_summaries),
            "pending_shard_count": total_pending,
            "admitted_candidate_count": total_admitted,
            "blocked_candidate_count": total_blocked,
        },
        metadata={"source": "owner_ci_runs"},
    )


def _storage_budget_policy_from_receipts(
    cleanup_receipts: list[StorageCleanupReceipt],
) -> StorageBudgetPolicy:
    first_receipt = cleanup_receipts[0] if cleanup_receipts else None
    metadata = dict(first_receipt.metadata or {}) if first_receipt is not None else {}
    return StorageBudgetPolicy(
        low_disk_free_bytes=int(
            metadata.get("low_disk_free_bytes") or _DEFAULT_LOW_DISK_FREE_BYTES
        ),
        target_free_bytes=int(metadata.get("target_free_bytes") or _DEFAULT_TARGET_FREE_BYTES),
        artifact_retention_hours=int(
            metadata.get("artifact_retention_hours") or _DEFAULT_ARTIFACT_RETENTION_HOURS
        ),
        log_retention_days=int(metadata.get("log_retention_days") or _DEFAULT_LOG_RETENTION_DAYS),
        cleanup_enabled=bool(metadata.get("cleanup_enabled", True)),
        metadata=metadata,
    )


def _storage_cleanup_receipt_from_bundle(
    bundle: dict[str, Any] | None,
) -> StorageCleanupReceipt | None:
    payload = dict((bundle or {}).get("bundle") or {})
    cleanup = dict(payload.get("cleanup_receipt") or {})
    if not cleanup:
        return None
    return StorageCleanupReceipt(
        status=str(cleanup.get("status") or "unknown").strip() or "unknown",
        path=str(cleanup.get("path") or "").strip() or None,
        deleted_paths=[
            str(item).strip()
            for item in list(cleanup.get("deleted_paths") or [])
            if str(item).strip()
        ],
        pruned_logs=[
            str(item).strip()
            for item in list(cleanup.get("pruned_logs") or [])
            if str(item).strip()
        ],
        docker_actions=[
            dict(item)
            for item in list(cleanup.get("docker_actions") or [])
            if isinstance(item, dict)
        ],
        warnings=[
            str(item).strip() for item in list(cleanup.get("warnings") or []) if str(item).strip()
        ],
        metadata={
            "low_disk_free_bytes": int(
                cleanup.get("low_disk_free_bytes") or _DEFAULT_LOW_DISK_FREE_BYTES
            ),
            "target_free_bytes": int(
                cleanup.get("target_free_bytes") or _DEFAULT_TARGET_FREE_BYTES
            ),
            "artifact_retention_hours": int(
                cleanup.get("artifact_retention_hours") or _DEFAULT_ARTIFACT_RETENTION_HOURS
            ),
            "log_retention_days": int(
                cleanup.get("log_retention_days") or _DEFAULT_LOG_RETENTION_DAYS
            ),
            "cleanup_enabled": bool(cleanup.get("cleanup_enabled", True)),
        },
    )


def _storage_categories_for_worker(
    *,
    worker: dict[str, Any],
    latest_sample: dict[str, Any],
    cleanup_receipt: StorageCleanupReceipt | None,
) -> list[StorageCategoryUsage]:
    metadata = dict(worker.get("metadata") or {})
    workspace_root = str(metadata.get("workspace_root") or "").strip() or None
    runtime_root = str(metadata.get("runtime_root") or "").strip() or None
    categories: list[StorageCategoryUsage] = [
        StorageCategoryUsage(
            category="workspace_roots",
            label="Workspace Roots",
            path=workspace_root,
            used_bytes=int(latest_sample.get("disk_used_bytes") or 0) or None,
            free_bytes=int(latest_sample.get("disk_free_bytes") or 0) or None,
            metadata={"node_id": worker.get("node_id")},
        ),
        StorageCategoryUsage(
            category="runtime_roots",
            label="Runtime Roots",
            path=runtime_root,
            metadata={"node_id": worker.get("node_id")},
        ),
    ]
    if cleanup_receipt is not None:
        categories.extend(
            [
                StorageCategoryUsage(
                    category="retained_artifacts",
                    label="Retained Artifacts",
                    path=f"{workspace_root}/.artifacts" if workspace_root else None,
                    item_count=len(cleanup_receipt.deleted_paths),
                    cleanup_action_count=len(cleanup_receipt.deleted_paths),
                    metadata={"cleanup_status": cleanup_receipt.status},
                ),
                StorageCategoryUsage(
                    category="worker_logs",
                    label="Worker Logs",
                    item_count=len(cleanup_receipt.pruned_logs),
                    cleanup_action_count=len(cleanup_receipt.pruned_logs),
                    metadata={"cleanup_status": cleanup_receipt.status},
                ),
                StorageCategoryUsage(
                    category="docker_cache",
                    label="Docker Cache",
                    item_count=len(cleanup_receipt.docker_actions),
                    cleanup_action_count=len(cleanup_receipt.docker_actions),
                    metadata={"cleanup_status": cleanup_receipt.status},
                ),
            ]
        )
    return categories


def _storage_incidents_for_worker(
    *,
    worker: dict[str, Any],
    latest_sample: dict[str, Any],
    policy: StorageBudgetPolicy,
    cleanup_receipt: StorageCleanupReceipt | None,
) -> list[StoragePressureIncident]:
    incidents: list[StoragePressureIncident] = []
    node_id = str(worker.get("node_id") or "").strip() or None
    free_bytes = int(latest_sample.get("disk_free_bytes") or 0)
    if free_bytes and free_bytes < int(policy.low_disk_free_bytes or 0):
        incidents.append(
            StoragePressureIncident(
                incident_id=f"storage:{node_id or uuid4().hex}:low-watermark",
                severity="high",
                blocking=True,
                summary="Disk headroom is below the low-watermark threshold.",
                node_id=node_id,
                recommended_fix=(
                    "Prune retained artifacts, worker logs, and Docker resources before "
                    "admitting additional heavy jobs."
                ),
                metadata={
                    "disk_free_bytes": free_bytes,
                    "low_disk_free_bytes": int(policy.low_disk_free_bytes or 0),
                },
            )
        )
    elif free_bytes and free_bytes < int(policy.target_free_bytes or 0):
        incidents.append(
            StoragePressureIncident(
                incident_id=f"storage:{node_id or uuid4().hex}:target-watermark",
                severity="medium",
                blocking=False,
                summary="Disk headroom is below the target free-space threshold.",
                node_id=node_id,
                recommended_fix=(
                    "Run cleanup proactively and reduce retention before the host reaches the "
                    "blocking watermark."
                ),
                metadata={
                    "disk_free_bytes": free_bytes,
                    "target_free_bytes": int(policy.target_free_bytes or 0),
                },
            )
        )
    if cleanup_receipt is not None and (
        cleanup_receipt.status == "cleanup_degraded" or cleanup_receipt.warnings
    ):
        incidents.append(
            StoragePressureIncident(
                incident_id=f"storage:{node_id or uuid4().hex}:cleanup",
                severity="medium",
                blocking=False,
                summary="Worker cleanup completed with warnings or degraded status.",
                node_id=node_id,
                evidence_refs=([cleanup_receipt.path] if cleanup_receipt.path else []),
                recommended_fix=(
                    "Review the cleanup receipt and adjust retention, Docker prune scope, or "
                    "workspace cleanup rules."
                ),
                metadata={
                    "cleanup_status": cleanup_receipt.status,
                    "warnings": list(cleanup_receipt.warnings),
                },
            )
        )
    return incidents


async def _build_storage_report(
    *,
    storage: OwnerCiStorage,
    owner_id: str,
    limit: int,
) -> StorageInventorySnapshot:
    repos = await storage.list_repo_profiles(owner_id)
    runs = await storage.list_runs(owner_id, limit=max(100, len(repos) * 20, 50))
    cleanup_receipts: list[StorageCleanupReceipt] = []
    for run in runs[: min(len(runs), 30)]:
        run_id = str(run.get("run_id") or "").strip()
        if not run_id:
            continue
        bundle = await storage.get_run_debug_bundle(owner_id, run_id)
        cleanup_receipt = _storage_cleanup_receipt_from_bundle(bundle)
        if cleanup_receipt is not None:
            cleanup_receipts.append(cleanup_receipt)

    policy = _storage_budget_policy_from_receipts(cleanup_receipts)
    projects: list[dict[str, Any]] = []
    for repo in repos:
        repo_id = str(repo.get("repo_id") or "").strip()
        if not repo_id:
            continue
        report = await storage.get_project_resource_report(owner_id, repo_id, limit=limit)
        totals = dict(report.get("totals") or {})
        items = list(report.get("items") or [])
        projects.append(
            {
                "repo_id": repo_id,
                "display_name": str(repo.get("display_name") or repo_id).strip() or repo_id,
                "peak_disk_used_bytes": int(totals.get("peak_disk_used_bytes") or 0),
                "compute_minutes": float(totals.get("compute_minutes") or 0.0),
                "cleanup_degraded_runs": sum(
                    1
                    for item in items
                    if str(item.get("cleanup_status") or "") == "cleanup_degraded"
                ),
                "latest_cleanup_status": (
                    str(items[0].get("cleanup_status") or "").strip() if items else ""
                )
                or None,
            }
        )

    workers_by_node: dict[str, dict[str, Any]] = {}
    for repo in repos:
        repo_id = str(repo.get("repo_id") or "").strip()
        if not repo_id:
            continue
        scope_id = _repo_scope_id(owner_id, repo_id)
        for node in await storage.list_worker_nodes(scope_id):
            node_id = str(node.get("node_id") or "").strip()
            if not node_id:
                continue
            worker = workers_by_node.setdefault(
                node_id,
                {
                    "node_id": node_id,
                    "node_name": str(node.get("node_name") or "").strip() or None,
                    "status": str(node.get("status") or "").strip() or "unknown",
                    "health_status": str(node.get("health_status") or "").strip() or "unknown",
                    "repos": set(),
                    "metadata": dict(node.get("metadata") or {}),
                },
            )
            worker["repos"].add(repo_id)

    worker_catalog: list[dict[str, Any]] = []
    incidents: list[StoragePressureIncident] = []
    categories: list[StorageCategoryUsage] = []
    for node_id, worker in sorted(workers_by_node.items()):
        report = await storage.get_worker_resource_report(owner_id, node_id, limit=limit)
        samples = list(report.get("samples") or [])
        latest_sample = dict((samples[0].get("sample") or {}) if samples else {})
        worker_incidents = _storage_incidents_for_worker(
            worker=worker,
            latest_sample=latest_sample,
            policy=policy,
            cleanup_receipt=cleanup_receipts[0] if cleanup_receipts else None,
        )
        worker_categories = _storage_categories_for_worker(
            worker=worker,
            latest_sample=latest_sample,
            cleanup_receipt=cleanup_receipts[0] if cleanup_receipts else None,
        )
        incidents.extend(worker_incidents)
        categories.extend(worker_categories)
        storage_status = "healthy"
        if any(incident.blocking for incident in worker_incidents):
            storage_status = "blocked"
        elif worker_incidents:
            storage_status = "degraded"
        worker_catalog.append(
            {
                "node_id": node_id,
                "node_name": worker.get("node_name"),
                "status": worker.get("status"),
                "health_status": worker.get("health_status"),
                "repos": sorted(worker.get("repos") or []),
                "latest_sample": latest_sample or None,
                "storage_status": storage_status,
                "categories": [item.model_dump(mode="json") for item in worker_categories],
                "incidents": [item.model_dump(mode="json") for item in worker_incidents],
            }
        )

    top_consumers = sorted(
        projects,
        key=lambda item: int(item.get("peak_disk_used_bytes") or 0),
        reverse=True,
    )[:5]
    blocked_count = sum(1 for incident in incidents if incident.blocking)
    degraded_count = len(incidents) - blocked_count
    status = "healthy"
    summary = "Storage headroom is healthy across the current owner-CI inventory."
    if blocked_count > 0:
        status = "blocked"
        summary = "Storage pressure is blocking or should block new heavy work."
    elif degraded_count > 0:
        status = "degraded"
        summary = "Storage pressure needs cleanup or retention tuning."

    coaching: list[str] = []
    if blocked_count > 0:
        coaching.append(
            "Disk headroom is below the blocking watermark on at least one worker. "
            "Reduce artifact retention and prune Docker resources before admitting heavy runs."
        )
    if any(int(item.get("cleanup_degraded_runs") or 0) > 0 for item in projects):
        coaching.append(
            "Cleanup is degrading for recent runs. Review cleanup receipts and tighten retention "
            "before disk growth becomes chronic."
        )
    if top_consumers:
        top_repo = top_consumers[0]
        coaching.append(
            f"Top disk consumer right now is `{top_repo['repo_id']}`. Start retention tuning there "
            "before broad policy changes."
        )
    alerts: list[str] = []
    if blocked_count > 0:
        alerts.append(
            "Storage pressure is actively blocking heavy owner-CI work on at least one worker."
        )
    elif degraded_count > 0:
        alerts.append(
            "Storage pressure is trending in the wrong direction. "
            "Clean up before the next heavy run."
        )
    if top_consumers:
        top_repo = top_consumers[0]
        alerts.append(
            f"Highest current disk consumer is {top_repo['repo_id']} at "
            f"{int(top_repo.get('peak_disk_used_bytes') or 0)} bytes peak usage."
        )

    announcement_events: list[dict[str, Any]] = []
    if alerts:
        primary_incident = incidents[0] if incidents else None
        announcement_events.append(
            {
                "source": "owner_ci.storage",
                "category": "ops.storage_pressure",
                "severity": "critical" if blocked_count > 0 else "high",
                "title": (
                    "Owner-CI storage pressure is blocking new work"
                    if blocked_count > 0
                    else "Owner-CI storage pressure needs attention"
                ),
                "body": alerts[0],
                "fingerprint": (
                    primary_incident.incident_id
                    if primary_incident is not None
                    else f"storage:{owner_id}:{status}"
                ),
                "payload": {
                    "status": status,
                    "summary": summary,
                    "blocked_incident_count": blocked_count,
                    "degraded_incident_count": degraded_count,
                    "top_consumers": top_consumers,
                    "worker_count": len(worker_catalog),
                    "repo_count": len(repos),
                },
            }
        )

    return StorageInventorySnapshot(
        generated_at=datetime.now(UTC).isoformat(),
        status=status,
        summary=summary,
        budget_policy=policy,
        categories=categories,
        workers=worker_catalog,
        projects=projects,
        top_consumers=top_consumers,
        cleanup_receipts=cleanup_receipts[:10],
        incidents=incidents,
        coaching=coaching,
        alerts=alerts,
        announcement_events=announcement_events,
        metadata={
            "repo_count": len(repos),
            "worker_count": len(worker_catalog),
        },
    )


async def _build_vercel_report(
    *,
    storage: OwnerCiStorage,
    owner_id: str,
    limit: int,
) -> dict[str, Any]:
    operations = await storage.list_managed_operations(
        owner_id,
        service_kind="vercel",
        limit=max(10, limit),
    )
    hydrated: list[dict[str, Any]] = []
    for operation in operations:
        operation_id = str(operation.get("operation_id") or "").strip()
        hydrated_operation = await storage.get_operation_hydrated(owner_id, operation_id)
        hydrated.append(hydrated_operation or operation)

    deployments: list[dict[str, Any]] = []
    route_counts: dict[str, dict[str, int]] = {}
    incident_type_counts: dict[str, int] = {}
    incident_total = 0
    blocking_incident_total = 0
    for operation in hydrated:
        refs = list(operation.get("refs") or [])
        incidents = list(operation.get("incidents") or [])
        summary = dict(operation.get("summary") or {})
        metadata = dict(operation.get("metadata") or {})
        deployment_id = next(
            (
                str(ref.get("ref_value") or "").strip()
                for ref in refs
                if str(ref.get("ref_kind") or "").strip() == "vercel_deployment_id"
                and str(ref.get("ref_value") or "").strip()
            ),
            "",
        )
        branch = next(
            (
                str(ref.get("ref_value") or "").strip()
                for ref in refs
                if str(ref.get("ref_kind") or "").strip() == "branch"
                and str(ref.get("ref_value") or "").strip()
            ),
            "",
        )
        route_path = (
            str(summary.get("route_path") or metadata.get("route_path") or "").strip() or None
        )
        for incident in incidents:
            incident_total += 1
            if bool(incident.get("blocking")):
                blocking_incident_total += 1
            incident_type = str(incident.get("incident_type") or "").strip() or "unknown"
            incident_type_counts[incident_type] = incident_type_counts.get(incident_type, 0) + 1
            if route_path:
                route_counts.setdefault(route_path, {"incident_count": 0, "blocking_count": 0})
                route_counts[route_path]["incident_count"] += 1
                if bool(incident.get("blocking")):
                    route_counts[route_path]["blocking_count"] += 1
        deployments.append(
            {
                "operation_id": str(operation.get("operation_id") or "").strip(),
                "app_id": str(operation.get("app_id") or "").strip() or None,
                "repo_id": str(operation.get("repo_id") or "").strip() or None,
                "status": str(operation.get("status") or "").strip() or "unknown",
                "created_at": operation.get("created_at"),
                "updated_at": operation.get("updated_at"),
                "deployment_id": deployment_id or None,
                "branch": branch or None,
                "route_path": route_path,
                "incident_count": len(incidents),
                "top_incident": incidents[0] if incidents else None,
            }
        )

    failed_operations = sum(
        1 for operation in hydrated if str(operation.get("status") or "") in {"failed", "error"}
    )
    active_operations = sum(
        1
        for operation in hydrated
        if str(operation.get("status") or "") not in {"resolved", "succeeded", "failed", "error"}
    )
    alerts: list[str] = []
    if failed_operations > 0:
        alerts.append(
            "Recent Vercel operations are failing. Start with the newest blocked deployment and "
            "inspect its correlated incidents before retrying."
        )
    if blocking_incident_total > 0:
        alerts.append(
            "Blocking Vercel incidents are open. Route-level or deployment-level errors should be "
            "resolved before promoting further changes."
        )
    announcement_events: list[dict[str, Any]] = []
    if alerts:
        announcement_events.append(
            {
                "source": "owner_ci.vercel",
                "category": "ops.vercel_reporting",
                "severity": "critical" if blocking_incident_total > 0 else "high",
                "title": (
                    "Vercel reporting shows blocking deployment incidents"
                    if blocking_incident_total > 0
                    else "Vercel reporting needs attention"
                ),
                "body": alerts[0],
                "fingerprint": f"vercel:{owner_id}:{failed_operations}:{blocking_incident_total}",
                "payload": {
                    "summary": {
                        "total_operations": len(hydrated),
                        "failed_operations": failed_operations,
                        "active_operations": active_operations,
                        "incident_total": incident_total,
                        "blocking_incident_total": blocking_incident_total,
                    },
                    "deployments": deployments[:5],
                    "routes": [
                        {
                            "route_path": route_path,
                            **counts,
                        }
                        for route_path, counts in sorted(
                            route_counts.items(),
                            key=lambda item: (
                                int(item[1].get("blocking_count") or 0),
                                int(item[1].get("incident_count") or 0),
                                item[0],
                            ),
                            reverse=True,
                        )[:5]
                    ],
                },
            }
        )

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "summary": {
            "total_operations": len(hydrated),
            "failed_operations": failed_operations,
            "active_operations": active_operations,
            "incident_total": incident_total,
            "blocking_incident_total": blocking_incident_total,
        },
        "deployments": deployments[:10],
        "routes": [
            {
                "route_path": route_path,
                **counts,
            }
            for route_path, counts in sorted(
                route_counts.items(),
                key=lambda item: (
                    int(item[1].get("blocking_count") or 0),
                    int(item[1].get("incident_count") or 0),
                    item[0],
                ),
                reverse=True,
            )[:10]
        ],
        "incident_types": [
            {"incident_type": incident_type, "count": count}
            for incident_type, count in sorted(
                incident_type_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )
        ],
        "alerts": alerts,
        "announcement_events": announcement_events,
    }


class CiObserverSkill(Skill):
    """Read-only observability and reporting for owner CI."""

    def __init__(
        self,
        *,
        storage: OwnerCiStorage,
        coaching_synthesizer: CoachingSynthesizer | None = None,
    ) -> None:
        super().__init__(memory=None)
        self._storage = storage
        self._coaching_synthesizer = coaching_synthesizer

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="ci_observer",
            description="Owner CI observability, logs, telemetry, and reporting",
            version="0.1.0",
            permissions=PermissionSet({Permission.ADMIN, Permission.READ_CONFIG}),
            intents=[
                "ci_run_events",
                "ci_run_logs",
                "ci_run_debug_bundle",
                "ci_run_report",
                "ci_run_graph",
                "ci_run_correlation_context",
                "ci_run_diagnostics",
                "ci_run_artifacts",
                "ci_run_evidence",
                "ci_run_coaching",
                "ci_reporting_readiness",
                "ci_reporting_summary",
                "ci_reporting_capacity",
                "ci_reporting_scheduler",
                "ci_reporting_storage",
                "ci_reporting_vercel",
                "ci_reporting_project_resources",
                "ci_reporting_project_failures",
                "ci_reporting_worker_resources",
            ],
        )

    async def initialize(self) -> bool:
        log.info("ci_observer_initialized")
        return True

    async def _prepare_coaching_payload(
        self,
        coaching: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        if self._coaching_synthesizer is None:
            return coaching
        return await self._coaching_synthesizer.synthesize_many(coaching)

    async def handle(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        intent = request.intent
        if intent == "ci_run_events":
            run_id = str(request.context.get("run_id") or "").strip()
            if not run_id:
                return SkillResponse.error_response(request.id, "run_id is required")
            events = await self._storage.get_run_events(
                owner_id,
                run_id,
                shard_id=str(request.context.get("shard_id") or "").strip() or None,
                limit=int(request.context.get("limit") or 200),
            )
            return SkillResponse(
                request_id=request.id,
                message=f"Loaded {len(events)} events.",
                data={"events": events},
            )
        if intent == "ci_run_logs":
            run_id = str(request.context.get("run_id") or "").strip()
            if not run_id:
                return SkillResponse.error_response(request.id, "run_id is required")
            logs = await self._storage.get_run_log_chunks(
                owner_id,
                run_id,
                shard_id=str(request.context.get("shard_id") or "").strip() or None,
                query_text=str(request.context.get("query") or "").strip() or None,
                limit=int(request.context.get("limit") or 500),
            )
            return SkillResponse(
                request_id=request.id,
                message=f"Loaded {len(logs)} log chunks.",
                data={"logs": logs},
            )
        if intent == "ci_run_debug_bundle":
            run_id = str(request.context.get("run_id") or "").strip()
            if not run_id:
                return SkillResponse.error_response(request.id, "run_id is required")
            bundle = await self._storage.get_run_debug_bundle(
                owner_id,
                run_id,
                shard_id=str(request.context.get("shard_id") or "").strip() or None,
            )
            return SkillResponse(
                request_id=request.id,
                message="Loaded debug bundle.",
                data={"debug_bundle": bundle},
            )
        if intent == "ci_run_report":
            run_id = str(request.context.get("run_id") or "").strip()
            if not run_id:
                return SkillResponse.error_response(request.id, "run_id is required")
            report = await self._storage.get_run_report(
                owner_id,
                run_id,
                shard_id=str(request.context.get("shard_id") or "").strip() or None,
                node_id=str(request.context.get("node_id") or "").strip() or None,
                log_limit=int(request.context.get("limit") or 500),
            )
            return SkillResponse(
                request_id=request.id,
                message="Loaded run report.",
                data={"report": report},
            )
        if intent == "ci_run_graph":
            run_id = str(request.context.get("run_id") or "").strip()
            if not run_id:
                return SkillResponse.error_response(request.id, "run_id is required")
            graph = await self._storage.get_run_graph(owner_id, run_id)
            return SkillResponse(
                request_id=request.id,
                message="Loaded run graph.",
                data={"run_graph": graph},
            )
        if intent == "ci_run_correlation_context":
            run_id = str(request.context.get("run_id") or "").strip()
            if not run_id:
                return SkillResponse.error_response(request.id, "run_id is required")
            correlation_context = await self._storage.get_run_correlation_context(owner_id, run_id)
            return SkillResponse(
                request_id=request.id,
                message="Loaded correlation context.",
                data={"correlation_context": correlation_context},
            )
        if intent == "ci_run_diagnostics":
            run_id = str(request.context.get("run_id") or "").strip()
            if not run_id:
                return SkillResponse.error_response(request.id, "run_id is required")
            diagnostics = await self._storage.get_run_diagnostics(
                owner_id,
                run_id,
                node_id=str(request.context.get("node_id") or "").strip() or None,
            )
            return SkillResponse(
                request_id=request.id,
                message="Loaded run diagnostics.",
                data={"diagnostics": diagnostics},
            )
        if intent == "ci_run_artifacts":
            run_id = str(request.context.get("run_id") or "").strip()
            if not run_id:
                return SkillResponse.error_response(request.id, "run_id is required")
            artifacts = await self._storage.get_run_artifacts(
                owner_id,
                run_id,
                node_id=str(request.context.get("node_id") or "").strip() or None,
            )
            return SkillResponse(
                request_id=request.id,
                message=f"Loaded {len(artifacts)} run artifacts.",
                data={"artifacts": artifacts},
            )
        if intent == "ci_run_evidence":
            run_id = str(request.context.get("run_id") or "").strip()
            if not run_id:
                return SkillResponse.error_response(request.id, "run_id is required")
            evidence = await self._storage.get_run_evidence(
                owner_id,
                run_id,
                node_id=str(request.context.get("node_id") or "").strip() or None,
            )
            return SkillResponse(
                request_id=request.id,
                message=f"Loaded {len(evidence)} evidence references.",
                data={"evidence": evidence},
            )
        if intent == "ci_run_coaching":
            run_id = str(request.context.get("run_id") or "").strip()
            if not run_id:
                return SkillResponse.error_response(request.id, "run_id is required")
            coaching = await self._storage.list_agent_coaching_feedback(
                owner_id,
                principal_id=str(request.context.get("principal_id") or "").strip() or None,
                run_id=run_id,
                limit=int(request.context.get("limit") or 50),
            )
            coaching = await self._prepare_coaching_payload(coaching)
            return SkillResponse(
                request_id=request.id,
                message=f"Loaded {len(coaching)} coaching items.",
                data={"coaching": coaching},
            )
        if intent == "ci_reporting_summary":
            summary = await self._storage.get_reporting_summary(owner_id)
            return SkillResponse(
                request_id=request.id,
                message="Loaded CI reporting summary.",
                data={"summary": summary},
            )
        if intent == "ci_reporting_capacity":
            repos = await self._storage.list_repo_profiles(owner_id)
            runs = await self._storage.list_runs(
                owner_id,
                limit=max(200, len(repos) * 50, 50),
            )
            workers_by_node: dict[str, dict[str, object]] = {}
            for repo in repos:
                repo_id = str(repo.get("repo_id") or "").strip()
                if not repo_id:
                    continue
                scope_id = _repo_scope_id(owner_id, repo_id)
                for node in await self._storage.list_worker_nodes(scope_id):
                    node_id = str(node.get("node_id") or "").strip()
                    if not node_id:
                        continue
                    worker = workers_by_node.setdefault(
                        node_id,
                        {
                            "node_id": node_id,
                            "node_name": str(node.get("node_name") or "").strip() or None,
                            "status": str(node.get("status") or "").strip() or "unknown",
                            "health_status": str(node.get("health_status") or "").strip()
                            or "unknown",
                            "capabilities": set(),
                            "repos": set(),
                            "scopes": set(),
                            "metadata": dict(node.get("metadata") or {}),
                        },
                    )
                    worker["repos"].add(repo_id)
                    worker["scopes"].add(scope_id)
                    worker["capabilities"].update(list(node.get("capabilities") or []))
                    if not worker.get("node_name") and node.get("node_name"):
                        worker["node_name"] = str(node.get("node_name") or "").strip() or None
                    worker["status"] = str(node.get("status") or worker.get("status") or "").strip()
                    worker["health_status"] = str(
                        node.get("health_status") or worker.get("health_status") or ""
                    ).strip()

            hosts = _active_capacity_by_host(runs)
            worker_catalog: list[dict[str, object]] = []
            for node_id, worker in sorted(workers_by_node.items()):
                report = await self._storage.get_worker_resource_report(
                    owner_id,
                    node_id,
                    limit=int(request.context.get("limit") or 20),
                )
                worker_catalog.append(
                    {
                        "node_id": node_id,
                        "node_name": worker.get("node_name"),
                        "status": worker.get("status"),
                        "health_status": worker.get("health_status"),
                        "capabilities": sorted(
                            str(capability).strip()
                            for capability in set(worker.get("capabilities") or set())
                            if str(capability).strip()
                        ),
                        "repos": sorted(
                            str(repo_id).strip()
                            for repo_id in set(worker.get("repos") or set())
                            if str(repo_id).strip()
                        ),
                        "scopes": sorted(
                            str(scope).strip()
                            for scope in set(worker.get("scopes") or set())
                            if str(scope).strip()
                        ),
                        "metadata": dict(worker.get("metadata") or {}),
                        "latest_sample": (
                            dict((report.get("samples") or [])[0].get("sample") or {})
                            if list(report.get("samples") or [])
                            else None
                        ),
                        "resource_report": report,
                    }
                )

            host_catalog = [
                {
                    "host_id": host_id,
                    "resource_budget": dict(host.get("resource_budget") or {}),
                    "resource_usage": dict(host.get("resource_usage") or {}),
                    "busy_parallel_groups": sorted(
                        str(group).strip()
                        for group in set(host.get("busy_parallel_groups") or set())
                        if str(group).strip()
                    ),
                    "active_run_count": len(set(host.get("active_runs") or set())),
                    "active_shard_count": int(host.get("active_shards") or 0),
                    "runtime_headroom_reserved": bool(host.get("runtime_headroom_reserved", True)),
                    "repo_ids": sorted(
                        str(repo_id).strip()
                        for repo_id in set(host.get("repo_ids") or set())
                        if str(repo_id).strip()
                    ),
                    "admission_mode": str(host.get("admission_mode") or "").strip()
                    or "dynamic_resource_based",
                }
                for host_id, host in sorted(hosts.items())
            ]
            return SkillResponse(
                request_id=request.id,
                message="Loaded CI capacity report.",
                data={
                    "capacity": {
                        "generated_at": datetime.now(UTC).isoformat(),
                        "hosts": host_catalog,
                        "workers": worker_catalog,
                        "totals": {
                            "host_count": len(host_catalog),
                            "worker_count": len(worker_catalog),
                            "active_run_count": sum(
                                int(host.get("active_run_count") or 0) for host in host_catalog
                            ),
                            "active_shard_count": sum(
                                int(host.get("active_shard_count") or 0) for host in host_catalog
                            ),
                        },
                    }
                },
            )
        if intent == "ci_reporting_scheduler":
            scheduler_limit = int(request.context.get("limit") or 200)
            scheduler = _scheduler_overview(
                await self._storage.list_runs(owner_id, limit=scheduler_limit)
            )
            return SkillResponse(
                request_id=request.id,
                message="Loaded CI scheduler overview.",
                data={"scheduler": scheduler.model_dump(mode="json")},
            )
        if intent == "ci_reporting_storage":
            report = await _build_storage_report(
                storage=self._storage,
                owner_id=owner_id,
                limit=int(request.context.get("limit") or 20),
            )
            return SkillResponse(
                request_id=request.id,
                message="Loaded CI storage report.",
                data={"report": report.model_dump(mode="json")},
            )
        if intent == "ci_reporting_vercel":
            report = await _build_vercel_report(
                storage=self._storage,
                owner_id=owner_id,
                limit=int(request.context.get("limit") or 25),
            )
            return SkillResponse(
                request_id=request.id,
                message="Loaded Vercel reporting summary.",
                data={"report": report},
            )
        if intent == "ci_reporting_readiness":
            readiness = await self._storage.get_reporting_readiness(owner_id)
            repo_profiles = await self._storage.list_repo_profiles(owner_id)
            github_security = await _build_github_security_summary(
                [dict(repo) for repo in repo_profiles if isinstance(repo, dict)]
            )
            readiness = dict(readiness or {})
            readiness["github_security"] = github_security
            repo_security_by_id = {
                str(entry.get("repo_id") or "").strip(): entry
                for entry in list(github_security.get("repos") or [])
                if str(entry.get("repo_id") or "").strip()
            }
            repo_readiness = [
                {
                    **dict(entry),
                    "github_security": repo_security_by_id.get(
                        str(dict(entry).get("repo_id") or "").strip()
                    ),
                }
                for entry in list(readiness.get("repo_readiness") or [])
                if isinstance(entry, dict)
            ]
            readiness["repo_readiness"] = repo_readiness
            return SkillResponse(
                request_id=request.id,
                message="Loaded CI readiness summary.",
                data={"readiness": readiness},
            )
        if intent == "ci_reporting_project_resources":
            repo_id = str(request.context.get("repo_id") or "").strip()
            if not repo_id:
                return SkillResponse.error_response(request.id, "repo_id is required")
            report = await self._storage.get_project_resource_report(
                owner_id,
                repo_id,
                limit=int(request.context.get("limit") or 30),
            )
            return SkillResponse(
                request_id=request.id,
                message="Loaded project resource report.",
                data={"report": report},
            )
        if intent == "ci_reporting_project_failures":
            repo_id = str(request.context.get("repo_id") or "").strip()
            if not repo_id:
                return SkillResponse.error_response(request.id, "repo_id is required")
            report = await self._storage.get_project_failure_report(
                owner_id,
                repo_id,
                limit=int(request.context.get("limit") or 20),
            )
            return SkillResponse(
                request_id=request.id,
                message="Loaded project failure report.",
                data={"report": report},
            )
        if intent == "ci_reporting_worker_resources":
            node_id = str(request.context.get("node_id") or "").strip()
            if not node_id:
                return SkillResponse.error_response(request.id, "node_id is required")
            report = await self._storage.get_worker_resource_report(
                owner_id,
                node_id,
                limit=int(request.context.get("limit") or 100),
            )
            return SkillResponse(
                request_id=request.id,
                message="Loaded worker resource report.",
                data={"report": report},
            )
        return SkillResponse.error_response(request.id, f"Unknown CI observer intent: {intent}")
