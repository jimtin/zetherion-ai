"""Owner-scoped CI observability skill."""

from __future__ import annotations

from datetime import UTC, datetime

from zetherion_ai.logging import get_logger
from zetherion_ai.owner_ci import OwnerCiStorage
from zetherion_ai.skills.base import Skill, SkillMetadata, SkillRequest, SkillResponse
from zetherion_ai.skills.permissions import Permission, PermissionSet

log = get_logger("zetherion_ai.skills.ci_observer")
_ACTIVE_SHARD_STATUSES = {"running", "claimed", "awaiting_sync", "running_disconnected"}


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
