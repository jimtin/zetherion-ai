"""Owner-scoped CI observability skill."""

from __future__ import annotations

from zetherion_ai.logging import get_logger
from zetherion_ai.owner_ci import OwnerCiStorage
from zetherion_ai.skills.base import Skill, SkillMetadata, SkillRequest, SkillResponse
from zetherion_ai.skills.permissions import Permission, PermissionSet

log = get_logger("zetherion_ai.skills.ci_observer")


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
