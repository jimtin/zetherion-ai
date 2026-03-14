"""Owner-scoped CI observability skill."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.owner_ci import (
    OwnerCiStorage,
    ResourceReservation,
    SchedulerCandidate,
    SchedulerHostSummary,
    SchedulerOverview,
)
from zetherion_ai.skills.base import Skill, SkillMetadata, SkillRequest, SkillResponse
from zetherion_ai.skills.permissions import Permission, PermissionSet

log = get_logger("zetherion_ai.skills.ci_observer")
_ACTIVE_SHARD_STATUSES = {"running", "claimed", "awaiting_sync", "running_disconnected"}
_PENDING_SHARD_STATUSES = {"queued_local", "planned"}


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
            str(host_policy.get("host_id") or "windows-owner-ci").strip()
            or "windows-owner-ci"
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
            resource_class = str(
                reservation.get("resource_class") or shard_metadata.get("resource_class") or "cpu"
            ).strip() or "cpu"
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
            str(host_policy.get("host_id") or "windows-owner-ci").strip()
            or "windows-owner-ci"
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
        queue_order = _round_robin_candidates_by_repo(
            dict(host_entry.get("pending_by_repo") or {})
        )
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


class CiObserverSkill(Skill):
    """Read-only observability and reporting for owner CI."""

    def __init__(self, *, storage: OwnerCiStorage) -> None:
        super().__init__(memory=None)
        self._storage = storage

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
                "ci_reporting_readiness",
                "ci_reporting_summary",
                "ci_reporting_capacity",
                "ci_reporting_scheduler",
                "ci_reporting_project_resources",
                "ci_reporting_project_failures",
                "ci_reporting_worker_resources",
            ],
        )

    async def initialize(self) -> bool:
        log.info("ci_observer_initialized")
        return True

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
                    "runtime_headroom_reserved": bool(
                        host.get("runtime_headroom_reserved", True)
                    ),
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
                                int(host.get("active_shard_count") or 0)
                                for host in host_catalog
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
        if intent == "ci_reporting_readiness":
            readiness = await self._storage.get_reporting_readiness(owner_id)
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
