"""Owner-scoped CI controller skill."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from copy import deepcopy
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.owner_ci import OwnerCiStorage
from zetherion_ai.owner_ci.profiles import default_repo_profile, default_repo_profiles
from zetherion_ai.skills.base import Skill, SkillMetadata, SkillRequest, SkillResponse
from zetherion_ai.skills.permissions import Permission, PermissionSet

log = get_logger("zetherion_ai.skills.ci_controller")

_RUN_MODES = {"fast", "full", "certification"}
_DISCONNECTED_STATUSES = {"queued_local", "running_disconnected", "awaiting_sync"}
_PROFILE_EXTENSION_KEYS = {
    "mandatory_static_gates",
    "shard_templates",
    "scheduling_policy",
    "resource_classes",
    "windows_execution_mode",
    "certification_requirements",
    "scheduled_canaries",
    "debug_policy",
    "agent_bootstrap_profile",
}


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


def _normalize_repo_profile_input(payload: dict[str, Any]) -> dict[str, Any]:
    repo_id = str(payload.get("repo_id") or "").strip()
    if not repo_id:
        raise ValueError("repo_id is required")
    display_name = str(payload.get("display_name") or payload.get("name") or repo_id).strip()
    github_repo = str(payload.get("github_repo") or "").strip()
    if not github_repo:
        raise ValueError("github_repo is required")
    stack_kind = str(payload.get("stack_kind") or "").strip()
    if not stack_kind:
        raise ValueError("stack_kind is required")

    metadata = dict(payload.get("metadata") or {})
    for key in _PROFILE_EXTENSION_KEYS:
        if key in payload:
            metadata[key] = payload.get(key)

    return {
        "repo_id": repo_id,
        "display_name": display_name,
        "github_repo": github_repo,
        "default_branch": str(payload.get("default_branch") or "main").strip() or "main",
        "stack_kind": stack_kind,
        "mandatory_static_gates": list(metadata.get("mandatory_static_gates") or []),
        "local_fast_lanes": list(payload.get("local_fast_lanes") or []),
        "windows_full_lanes": list(payload.get("windows_full_lanes") or []),
        "shard_templates": list(metadata.get("shard_templates") or []),
        "scheduling_policy": dict(metadata.get("scheduling_policy") or {}),
        "resource_classes": dict(metadata.get("resource_classes") or {}),
        "windows_execution_mode": str(metadata.get("windows_execution_mode") or "command").strip()
        or "command",
        "certification_requirements": list(metadata.get("certification_requirements") or []),
        "scheduled_canaries": list(metadata.get("scheduled_canaries") or []),
        "debug_policy": dict(metadata.get("debug_policy") or {}),
        "agent_bootstrap_profile": dict(metadata.get("agent_bootstrap_profile") or {}),
        "review_policy": dict(payload.get("review_policy") or {}),
        "promotion_policy": dict(payload.get("promotion_policy") or {}),
        "allowed_paths": [str(item).strip() for item in list(payload.get("allowed_paths") or [])],
        "secrets_profile": str(payload.get("secrets_profile") or "").strip() or None,
        "active": bool(payload.get("active", True)),
        "metadata": metadata,
    }


def _coerce_lane_objects(raw_lanes: list[Any]) -> list[dict[str, Any]]:
    lanes: list[dict[str, Any]] = []
    for index, entry in enumerate(raw_lanes):
        if isinstance(entry, dict):
            lane = dict(entry)
        else:
            lane_id = str(entry or "").strip()
            if not lane_id:
                continue
            lane = {"lane_id": lane_id, "lane_label": lane_id}
        lane_id = str(lane.get("lane_id") or lane.get("id") or f"lane-{index + 1}").strip()
        if not lane_id:
            continue
        lane["lane_id"] = lane_id
        lane["lane_label"] = str(lane.get("lane_label") or lane_id).strip() or lane_id
        lanes.append(lane)
    return lanes


class CiControllerSkill(Skill):
    """Owner-scoped CI control plane primitives for repo registry and runs."""

    def __init__(self, *, storage: OwnerCiStorage) -> None:
        super().__init__(memory=None)
        self._storage = storage

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="ci_controller",
            description="Owner-scoped CI controller for repo registry, plans, runs, and promotion",
            version="0.2.0",
            permissions=PermissionSet({Permission.ADMIN, Permission.READ_CONFIG}),
            intents=[
                "ci_repo_seed_defaults",
                "ci_repo_upsert",
                "ci_repo_list",
                "ci_repo_get",
                "ci_plan_save",
                "ci_plan_get",
                "ci_plan_versions",
                "ci_plan_compile",
                "ci_schedule_upsert",
                "ci_schedule_list",
                "ci_run_start",
                "ci_run_get",
                "ci_run_list",
                "ci_run_rebalance",
                "ci_run_store_github_receipt",
                "ci_run_promote",
            ],
        )

    async def initialize(self) -> bool:
        log.info("ci_controller_initialized")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        handlers: dict[str, Callable[[SkillRequest], Awaitable[SkillResponse]]] = {
            "ci_repo_seed_defaults": self._handle_seed_defaults,
            "ci_repo_upsert": self._handle_repo_upsert,
            "ci_repo_list": self._handle_repo_list,
            "ci_repo_get": self._handle_repo_get,
            "ci_plan_save": self._handle_plan_save,
            "ci_plan_get": self._handle_plan_get,
            "ci_plan_versions": self._handle_plan_versions,
            "ci_plan_compile": self._handle_plan_compile,
            "ci_schedule_upsert": self._handle_schedule_upsert,
            "ci_schedule_list": self._handle_schedule_list,
            "ci_run_start": self._handle_run_start,
            "ci_run_get": self._handle_run_get,
            "ci_run_list": self._handle_run_list,
            "ci_run_rebalance": self._handle_run_rebalance,
            "ci_run_store_github_receipt": self._handle_store_github_receipt,
            "ci_run_promote": self._handle_run_promote,
        }
        handler = handlers.get(request.intent)
        if handler is None:
            return SkillResponse.error_response(
                request.id,
                f"Unknown CI controller intent: {request.intent}",
            )
        try:
            return await handler(request)
        except ValueError as exc:
            return SkillResponse.error_response(request.id, str(exc))

    async def _handle_seed_defaults(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        seeded: list[dict[str, Any]] = []
        for profile in default_repo_profiles():
            seeded.append(await self._storage.upsert_repo_profile(owner_id, profile))
        return SkillResponse(
            request_id=request.id,
            message=f"Seeded {len(seeded)} default repo profiles.",
            data={"owner_id": owner_id, "repos": seeded},
        )

    async def _handle_repo_upsert(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        profile = _normalize_repo_profile_input(dict(request.context))
        stored = await self._storage.upsert_repo_profile(owner_id, profile)
        return SkillResponse(
            request_id=request.id,
            message=f"Updated repo profile `{stored['repo_id']}`.",
            data={"repo": stored},
        )

    async def _handle_repo_list(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        repos = await self._storage.list_repo_profiles(owner_id)
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(repos)} repo profiles.",
            data={"repos": repos},
        )

    async def _handle_repo_get(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        repo_id = str(request.context.get("repo_id") or "").strip()
        if not repo_id:
            raise ValueError("repo_id is required")
        repo = await self._storage.get_repo_profile(owner_id, repo_id)
        if repo is None:
            repo = default_repo_profile(repo_id)
        if repo is None:
            raise ValueError(f"Repo profile `{repo_id}` not found")
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded repo profile `{repo_id}`.",
            data={"repo": repo},
        )

    async def _handle_plan_save(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        repo_id = str(request.context.get("repo_id") or "").strip()
        if not repo_id:
            raise ValueError("repo_id is required")
        title = str(request.context.get("title") or "Plan").strip() or "Plan"
        content_markdown = str(request.context.get("content_markdown") or "").strip()
        if not content_markdown:
            raise ValueError("content_markdown is required")
        plan_id_raw = str(request.context.get("plan_id") or "").strip() or None
        snapshot = await self._storage.create_plan_snapshot(
            owner_id=owner_id,
            repo_id=repo_id,
            title=title,
            content_markdown=content_markdown,
            tags=[
                str(tag).strip()
                for tag in list(request.context.get("tags") or [])
                if str(tag).strip()
            ],
            plan_id=plan_id_raw,
            metadata=dict(request.context.get("metadata") or {}),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Saved plan `{snapshot['plan_id']}` version {snapshot['version']}.",
            data={"plan": snapshot},
        )

    async def _handle_plan_get(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        plan_id = str(request.context.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("plan_id is required")
        version_raw = request.context.get("version")
        version = int(version_raw) if version_raw is not None else None
        snapshot = await self._storage.get_plan_snapshot(owner_id, plan_id, version=version)
        if snapshot is None:
            raise ValueError(f"Plan `{plan_id}` not found")
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded plan `{plan_id}`.",
            data={"plan": snapshot},
        )

    async def _handle_plan_versions(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        plan_id = str(request.context.get("plan_id") or "").strip()
        if not plan_id:
            raise ValueError("plan_id is required")
        versions = await self._storage.list_plan_versions(owner_id, plan_id)
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(versions)} versions for `{plan_id}`.",
            data={"versions": versions},
        )

    async def _handle_plan_compile(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        repo = await self._resolve_repo_profile(
            owner_id,
            str(request.context.get("repo_id") or "").strip(),
        )
        mode = self._normalize_mode(request)
        git_ref = str(
            request.context.get("git_ref") or repo.get("default_branch") or "main"
        ).strip()
        compiled = self._compile_run_plan(repo=repo, mode=mode, git_ref=git_ref)
        stored = await self._storage.create_compiled_plan(
            owner_id=owner_id,
            repo_id=str(repo["repo_id"]),
            git_ref=git_ref,
            mode=mode,
            plan=compiled,
            metadata={
                "windows_execution_mode": repo.get("windows_execution_mode"),
                "resource_classes": repo.get("resource_classes"),
            },
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Compiled plan `{stored['compiled_plan_id']}` for `{repo['repo_id']}`.",
            data={"compiled_plan": stored},
        )

    async def _handle_schedule_upsert(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        repo = await self._resolve_repo_profile(
            owner_id,
            str(request.context.get("repo_id") or "").strip(),
        )
        schedule_name = (
            str(
                request.context.get("name") or request.context.get("schedule_id") or "Schedule"
            ).strip()
            or "Schedule"
        )
        schedule = await self._storage.upsert_schedule(
            owner_id=owner_id,
            repo_id=str(repo["repo_id"]),
            name=schedule_name,
            schedule_kind=str(request.context.get("schedule_kind") or "manual").strip() or "manual",
            schedule_spec=dict(request.context.get("schedule_spec") or {}),
            active=bool(request.context.get("active", True)),
            schedule_id=str(request.context.get("schedule_id") or "").strip() or None,
            metadata=dict(request.context.get("metadata") or {}),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Upserted schedule `{schedule['schedule_id']}`.",
            data={"schedule": schedule},
        )

    async def _handle_schedule_list(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        repo_id = str(request.context.get("repo_id") or "").strip() or None
        schedules = await self._storage.list_schedules(owner_id, repo_id=repo_id)
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(schedules)} schedules.",
            data={"schedules": schedules},
        )

    async def _handle_run_start(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        repo = await self._resolve_repo_profile(
            owner_id,
            str(request.context.get("repo_id") or "").strip(),
        )
        mode = self._normalize_mode(request)
        git_ref = str(
            request.context.get("git_ref") or repo.get("default_branch") or "main"
        ).strip()
        trigger = str(request.context.get("trigger") or "manual").strip() or "manual"
        metadata = dict(request.context.get("metadata") or {})
        plan = dict(request.context.get("plan") or {})
        if not plan and request.context.get("plan_id"):
            plan_snapshot = await self._storage.get_plan_snapshot(
                owner_id,
                str(request.context.get("plan_id") or "").strip(),
            )
            if plan_snapshot is not None:
                plan = {"plan_snapshot": plan_snapshot}
        compiled = self._compile_run_plan(repo=repo, mode=mode, git_ref=git_ref)
        stored_compiled_plan = await self._storage.create_compiled_plan(
            owner_id=owner_id,
            repo_id=str(repo["repo_id"]),
            git_ref=git_ref,
            mode=mode,
            plan=compiled,
            metadata={
                "trigger": trigger,
                "requested_by": owner_id,
            },
        )
        plan["compiled_plan"] = stored_compiled_plan
        shards = list(compiled.get("shards") or [])
        if mode in {"full", "certification"} and not shards:
            raise ValueError(f"Repo `{repo['repo_id']}` has no configured full shards")
        run = await self._storage.create_run(
            owner_id=owner_id,
            scope_id=self._scope_id(owner_id, str(repo["repo_id"])),
            repo_id=str(repo["repo_id"]),
            git_ref=git_ref,
            trigger=trigger,
            plan=plan,
            metadata={
                **metadata,
                "mode": mode,
                "compiled_plan_id": stored_compiled_plan["compiled_plan_id"],
                "windows_execution_mode": repo.get("windows_execution_mode"),
                "required_static_gates": [
                    str(gate.get("lane_id") or "")
                    for gate in list(repo.get("mandatory_static_gates") or [])
                ],
                "certification_required": mode == "certification",
                "certification_requirements": list(repo.get("certification_requirements") or []),
                "platform_canary": bool((repo.get("metadata") or {}).get("platform_canary")),
            },
            shards=shards,
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Created run `{run['run_id']}` for `{repo['repo_id']}`.",
            data={"run": run, "compiled_plan": stored_compiled_plan},
        )

    async def _handle_run_get(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        run_id = str(request.context.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        run = await self._storage.get_run(owner_id, run_id)
        if run is None:
            raise ValueError(f"Run `{run_id}` not found")
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded run `{run_id}`.",
            data={"run": run},
        )

    async def _handle_run_list(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        repo_id = str(request.context.get("repo_id") or "").strip() or None
        limit = int(request.context.get("limit") or 50)
        runs = await self._storage.list_runs(owner_id, repo_id=repo_id, limit=limit)
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(runs)} runs.",
            data={"runs": runs},
        )

    async def _handle_run_rebalance(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        run_id = str(request.context.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        run = await self._storage.get_run(owner_id, run_id)
        if run is None:
            raise ValueError(f"Run `{run_id}` not found")
        pending = [
            shard
            for shard in list(run.get("shards") or [])
            if str(shard.get("status") or "").strip().lower() in {"queued_local", "planned"}
        ]
        busy_groups = sorted(
            {
                str((shard.get("metadata") or {}).get("parallel_group") or "")
                for shard in list(run.get("shards") or [])
                if str(shard.get("status") or "").strip().lower() == "running"
            }
            - {""}
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Prepared rebalance guidance for `{run_id}`.",
            data={
                "run": run,
                "rebalance": {
                    "requested": True,
                    "pending_shards": [
                        {
                            "shard_id": shard.get("shard_id"),
                            "lane_id": shard.get("lane_id"),
                            "resource_class": (shard.get("metadata") or {}).get("resource_class"),
                            "parallel_group": (shard.get("metadata") or {}).get("parallel_group"),
                        }
                        for shard in pending
                    ],
                    "busy_parallel_groups": busy_groups,
                },
            },
        )

    async def _handle_store_github_receipt(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        run_id = str(request.context.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        receipt = dict(request.context.get("receipt") or {})
        run = await self._storage.store_run_github_receipt(owner_id, run_id, receipt)
        if run is None:
            raise ValueError(f"Run `{run_id}` not found")
        return SkillResponse(
            request_id=request.id,
            message=f"Stored GitHub receipt for `{run_id}`.",
            data={"run": run},
        )

    async def _handle_run_promote(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        run_id = str(request.context.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        run = await self._storage.get_run(owner_id, run_id)
        if run is None:
            raise ValueError(f"Run `{run_id}` not found")
        review = dict(run.get("review_receipts") or {})
        if bool(review.get("merge_blocked", True)):
            updated = await self._storage.set_run_status(owner_id, run_id, "promotion_blocked")
            return SkillResponse(
                request_id=request.id,
                message=f"Run `{run_id}` is blocked from promotion.",
                data={"run": updated or run, "promoted": False},
            )
        if any(
            str(shard.get("status") or "").strip().lower() in _DISCONNECTED_STATUSES
            for shard in list(run.get("shards") or [])
        ):
            updated = await self._storage.set_run_status(owner_id, run_id, "promotion_blocked")
            return SkillResponse(
                request_id=request.id,
                message=f"Run `{run_id}` is waiting on synced worker receipts.",
                data={"run": updated or run, "promoted": False},
            )
        receipt = {
            "status": "ready_to_merge",
            "mode": "github_only",
            "requested_by": owner_id,
        }
        updated = await self._storage.store_run_github_receipt(owner_id, run_id, receipt)
        if updated is not None:
            await self._storage.set_run_status(owner_id, run_id, "ready_to_merge")
        final_run = await self._storage.get_run(owner_id, run_id)
        return SkillResponse(
            request_id=request.id,
            message=f"Run `{run_id}` is ready for GitHub promotion.",
            data={"run": final_run or updated or run, "promoted": True},
        )

    async def _resolve_repo_profile(self, owner_id: str, repo_id: str) -> dict[str, Any]:
        if not repo_id:
            raise ValueError("repo_id is required")
        profile = await self._storage.get_repo_profile(owner_id, repo_id)
        if profile is None:
            default_profile = default_repo_profile(repo_id)
            if default_profile is None:
                raise ValueError(f"Repo profile `{repo_id}` not found")
            profile = await self._storage.upsert_repo_profile(owner_id, default_profile)
        return profile

    @staticmethod
    def _normalize_mode(request: SkillRequest) -> str:
        mode = str(request.context.get("mode") or "fast").strip().lower()
        if mode not in _RUN_MODES:
            raise ValueError(f"Unsupported run mode: {mode}")
        return mode

    def _compile_run_plan(self, *, repo: dict[str, Any], mode: str, git_ref: str) -> dict[str, Any]:
        mandatory_static_gates = _coerce_lane_objects(
            list(repo.get("mandatory_static_gates") or [])
        )
        local_lanes = _coerce_lane_objects(list(repo.get("local_fast_lanes") or []))
        windows_lanes = _coerce_lane_objects(list(repo.get("windows_full_lanes") or []))
        workspace_root = str((repo.get("allowed_paths") or [None])[0] or "").strip()
        selected = [*mandatory_static_gates, *local_lanes]
        if mode in {"full", "certification"}:
            selected.extend(windows_lanes)

        shards: list[dict[str, Any]] = []
        static_gate_ids = [str(lane.get("lane_id") or "") for lane in mandatory_static_gates]
        windows_execution_mode = str(repo.get("windows_execution_mode") or "command").strip()

        for lane in selected:
            shard = deepcopy(lane)
            shard.setdefault("execution_target", "local_mac")
            shard.setdefault("runner", "command")
            shard.setdefault("action", "ci.test.run")
            shard.setdefault("relay_mode", "direct")
            shard.setdefault("artifact_contract", {"kind": "ci_shard"})
            shard.setdefault("required_capabilities", [])
            shard.setdefault("workspace_root", workspace_root)
            shard.setdefault("payload", {})
            shard.setdefault("metadata", {})
            shard["metadata"].setdefault("workspace_root", shard.get("workspace_root"))

            execution_target = str(shard.get("execution_target") or "local_mac").strip().lower()
            if execution_target in {"windows_local", "any_worker"}:
                shard["required_capabilities"] = list(
                    shard.get("required_capabilities") or ["ci.test.run"]
                )
                if windows_execution_mode == "docker_only":
                    shard["runner"] = "docker"
                    container_spec = dict((shard.get("payload") or {}).get("container_spec") or {})
                    if not container_spec:
                        raise ValueError(
                            "Windows shard "
                            f"`{shard['lane_id']}` is missing container_spec for docker_only mode"
                        )
                if static_gate_ids:
                    shard["metadata"].setdefault("depends_on", static_gate_ids)
            if shard["lane_id"] in static_gate_ids:
                shard["metadata"]["gate_kind"] = "static"
            if mode == "certification":
                shard["payload"]["certification_matrix"] = list(
                    (repo.get("metadata") or {}).get("certification_matrix") or []
                )
                shard["payload"]["certification_requirements"] = list(
                    repo.get("certification_requirements") or []
                )
                shard["metadata"]["certification_mode"] = True
            shards.append(shard)

        schedule_policy = dict(repo.get("scheduling_policy") or {})
        return {
            "repo_id": repo["repo_id"],
            "git_ref": git_ref,
            "mode": mode,
            "windows_execution_mode": windows_execution_mode,
            "resource_budget": dict(schedule_policy.get("resource_budgets") or {}),
            "schedule_tags": [mode, str(repo.get("stack_kind") or "")],
            "retry_policy": {
                "rerun_failed_shards": True,
                "max_attempts": 2 if mode == "certification" else 1,
            },
            "debug_bundle_contract": {
                "redact_display_logs": bool(
                    (repo.get("debug_policy") or {}).get("redact_display_logs", True)
                ),
                "retain_debug_bundle_days": int(
                    (repo.get("debug_policy") or {}).get("retain_debug_bundle_days", 14)
                ),
            },
            "required_static_gate_ids": static_gate_ids,
            "certification_requirements": list(repo.get("certification_requirements") or []),
            "scheduled_canaries": list(repo.get("scheduled_canaries") or []),
            "shards": shards,
        }

    @staticmethod
    def _scope_id(owner_id: str, repo_id: str) -> str:
        return f"owner:{owner_id}:repo:{repo_id}"
