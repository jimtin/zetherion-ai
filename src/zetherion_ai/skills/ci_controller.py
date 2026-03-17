"""Owner-scoped CI controller skill."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Awaitable, Callable
from copy import deepcopy
from pathlib import Path
from typing import Any

from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger
from zetherion_ai.owner_ci import (
    AdmissionDecision,
    HostCapacitySnapshot,
    LocalGatePlan,
    OwnerCiStorage,
    ResourceReservation,
    build_repo_readiness_receipt,
    build_workspace_readiness_receipt,
    normalize_release_verification_receipt,
)
from zetherion_ai.owner_ci.profiles import default_repo_profile, default_repo_profiles
from zetherion_ai.owner_ci.run_reports import (
    build_agent_coaching_feedback,
    build_preflight_coaching_payloads,
)
from zetherion_ai.skills.base import Skill, SkillMetadata, SkillRequest, SkillResponse
from zetherion_ai.skills.github.client import GitHubClient
from zetherion_ai.skills.permissions import Permission, PermissionSet

log = get_logger("zetherion_ai.skills.ci_controller")

_RUN_MODES = {"fast", "full", "certification"}
_DISCONNECTED_STATUSES = {"queued_local", "running_disconnected", "awaiting_sync"}
_ACTIVE_SHARD_STATUSES = {"running", "claimed", "awaiting_sync", "running_disconnected"}
_REQUIRED_CERTIFICATION_GATE_CATEGORIES = ("static", "security", "unit", "integration", "e2e")
_PROFILE_EXTENSION_KEYS = {
    "mandatory_static_gates",
    "mandatory_security_gates",
    "shard_templates",
    "scheduling_policy",
    "resource_classes",
    "windows_execution_mode",
    "certification_requirements",
    "scheduled_canaries",
    "debug_policy",
    "agent_bootstrap_profile",
}
_FULL_SHA_RE = re.compile(r"^[0-9a-f]{40}$")
_DEFAULT_MERGE_STATUS_CONTEXT = "zetherion/merge-readiness"
_DEFAULT_DEPLOY_STATUS_CONTEXT = "zetherion/deploy-readiness"


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
        "mandatory_security_gates": list(metadata.get("mandatory_security_gates") or []),
        "local_fast_lanes": list(payload.get("local_fast_lanes") or []),
        "local_full_lanes": list(payload.get("local_full_lanes") or []),
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


def _parse_github_repo(value: str) -> tuple[str, str]:
    candidate = str(value or "").strip()
    if "/" not in candidate:
        raise ValueError("github_repo must be in owner/repo format")
    owner, repo = candidate.split("/", 1)
    owner = owner.strip()
    repo = repo.strip()
    if not owner or not repo:
        raise ValueError("github_repo must be in owner/repo format")
    return owner, repo


def _status_contexts_for(repo: dict[str, Any]) -> tuple[str, str]:
    promotion = dict(repo.get("promotion_policy") or {})
    contexts = dict(promotion.get("status_contexts") or {})
    merge_context = str(contexts.get("merge") or _DEFAULT_MERGE_STATUS_CONTEXT).strip()
    deploy_context = str(contexts.get("deploy") or _DEFAULT_DEPLOY_STATUS_CONTEXT).strip()
    return merge_context, deploy_context


def _infer_git_sha(run: dict[str, Any]) -> str | None:
    metadata = dict(run.get("metadata") or {})
    for candidate in (
        metadata.get("git_sha"),
        metadata.get("head_sha"),
        run.get("git_ref"),
    ):
        value = str(candidate or "").strip().lower()
        if _FULL_SHA_RE.fullmatch(value):
            return value
    return None


def _dedupe_strings(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for raw in values:
        value = str(raw or "").strip()
        if not value or value in seen:
            continue
        deduped.append(value)
        seen.add(value)
    return deduped


def _stable_coaching_key(parts: list[str | None]) -> str:
    normalized = "||".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


def _passed_preflight_check(value: Any) -> bool:
    candidate = str(value or "").strip().lower()
    return candidate in {"passed", "success", "succeeded", "healthy", "green", "true", "1"}


def _expected_ruff_version(repo: dict[str, Any]) -> str | None:
    allowed_paths = [
        str(item).strip()
        for item in list(repo.get("allowed_paths") or [])
        if str(item).strip()
    ]
    for root in allowed_paths:
        requirements_path = Path(root) / "requirements-dev.txt"
        if not requirements_path.is_file():
            continue
        try:
            for line in requirements_path.read_text(encoding="utf-8").splitlines():
                candidate = line.strip()
                if candidate.startswith("ruff=="):
                    return candidate.split("==", 1)[1].strip() or None
        except OSError:
            continue
    return None


def _collect_preflight_violations(
    *,
    repo: dict[str, Any],
    compiled: dict[str, Any],
    mode: str,
    request_context: dict[str, Any],
) -> list[dict[str, Any]]:
    if mode != "certification":
        return []
    raw_preflight = dict(
        request_context.get("preflight_checks")
        or dict(request_context.get("metadata") or {}).get("preflight_checks")
        or {}
    )
    required_gate_ids = _dedupe_strings(
        [
            *list(compiled.get("required_static_gate_ids") or []),
            *list(compiled.get("required_security_gate_ids") or []),
        ]
    )
    if not raw_preflight:
        return [
            {
                "rule_code": "missing_preflight_attestation",
                "summary": (
                    "Certification was rejected because no preflight attestation "
                    "was provided."
                ),
                "remediation": (
                    "Run the mandatory static and security checks first, then include a "
                    "`preflight_checks` payload with completed check ids, statuses, "
                    "and tool versions."
                ),
                "evidence_references": [],
            }
        ]

    checks_by_id: dict[str, dict[str, Any]] = {}
    for raw_check in list(raw_preflight.get("checks") or []):
        if isinstance(raw_check, dict):
            check = dict(raw_check)
        else:
            check_id = str(raw_check or "").strip()
            if not check_id:
                continue
            check = {"id": check_id, "status": "passed"}
        check_id = str(check.get("id") or check.get("lane_id") or "").strip()
        if check_id:
            checks_by_id[check_id] = check

    categories = {
        str(item).strip().lower()
        for item in list(raw_preflight.get("categories_completed") or [])
        if str(item).strip()
    }
    categories.update(
        key.strip().lower()
        for key, value in dict(raw_preflight.get("attestations") or {}).items()
        if _passed_preflight_check(value)
    )

    violations: list[dict[str, Any]] = []
    if not {"static", "security"} <= categories:
        violations.append(
            {
                "rule_code": "missing_preflight_attestation",
                "summary": "Certification requires both static and security preflight attestation.",
                "remediation": (
                    "Record `static` and `security` in `preflight_checks.categories_completed` "
                    "or mark them true in `preflight_checks.attestations` before "
                    "starting certification."
                ),
                "evidence_references": [],
            }
        )

    missing_gate_ids = [
        gate_id
        for gate_id in required_gate_ids
        if not _passed_preflight_check((checks_by_id.get(gate_id) or {}).get("status"))
    ]
    for gate_id in missing_gate_ids:
        violations.append(
            {
                "rule_code": "missing_preflight_check",
                "summary": (
                    f"Mandatory certification preflight check `{gate_id}` is "
                    "missing or not marked passed."
                ),
                "remediation": (
                    f"Run `{gate_id}` before certification and include it in "
                    "`preflight_checks.checks` "
                    "with a passed status."
                ),
                "evidence_references": [],
            }
        )

    expected_ruff_version = _expected_ruff_version(repo)
    actual_ruff_version = str(
        dict(raw_preflight.get("tool_versions") or {}).get("ruff")
        or next(
            (
                dict(item).get("version")
                for item in checks_by_id.values()
                if str(dict(item).get("tool") or "").strip().lower() == "ruff"
                and str(dict(item).get("version") or "").strip()
            ),
            "",
        )
        or ""
    ).strip()
    if (
        expected_ruff_version
        and actual_ruff_version
        and actual_ruff_version != expected_ruff_version
    ):
        violations.append(
            {
                "rule_code": "tool_version_mismatch",
                "summary": (
                    f"Ruff version `{actual_ruff_version}` does not match the CI-pinned "
                    f"version `{expected_ruff_version}`."
                ),
                "remediation": (
                    f"Use Ruff `{expected_ruff_version}` for local preflight checks "
                    "and record that "
                    "version in the attestation before certification."
                ),
                "evidence_references": [],
            }
        )
    return violations


def _gate_family_for_lane(
    lane: dict[str, Any],
    *,
    static_gate_ids: set[str],
    security_gate_ids: set[str],
) -> str:
    lane_id = str(lane.get("lane_id") or "").strip()
    metadata = dict(lane.get("metadata") or {})
    explicit = str(metadata.get("gate_family") or metadata.get("gate_kind") or "").strip().lower()
    if lane_id in static_gate_ids:
        return "static"
    if lane_id in security_gate_ids:
        return "security"
    lowered = lane_id.lower()
    if explicit in {"static", "security", "unit", "integration", "e2e", "release"}:
        return explicit
    if "release" in lowered or "golive" in lowered:
        return "release"
    if "e2e" in lowered or "playwright" in lowered:
        return "e2e"
    if lowered.startswith(("z-unit", "c-unit")) or "unit" in lowered:
        return "unit"
    if lowered.startswith(("z-int", "c-int")) or "integration" in lowered:
        return "integration"
    return "integration"


def _required_category_for_family(gate_family: str) -> str | None:
    normalized = str(gate_family or "").strip().lower()
    if normalized in set(_REQUIRED_CERTIFICATION_GATE_CATEGORIES):
        return normalized
    return None


def _validation_mode_for_repo(repo_id: str) -> str:
    normalized = str(repo_id or "").strip().lower()
    if normalized == "zetherion-ai":
        return "zetherion_alone"
    if normalized == "catalyst-group-solutions":
        return "cgs_alone"
    return "repo_only"


def _resource_reservation_for_shard(repo_id: str, shard: dict[str, Any]) -> dict[str, Any]:
    metadata = dict(shard.get("metadata") or {})
    return {
        "repo_id": repo_id,
        "shard_id": str(shard.get("shard_id") or shard.get("lane_id") or "").strip() or None,
        "resource_class": str(metadata.get("resource_class") or "cpu").strip() or "cpu",
        "units": 1,
        "parallel_group": str(metadata.get("parallel_group") or "").strip() or None,
        "workspace_root": (
            str(metadata.get("workspace_root") or shard.get("workspace_root") or "").strip()
            or None
        ),
        "metadata": {
            "execution_target": str(shard.get("execution_target") or "").strip() or None,
            "gate_family": str(metadata.get("gate_family") or "").strip() or None,
        },
    }


def _host_capacity_policy_for(
    repo: dict[str, Any],
    *,
    resource_budget: dict[str, int],
    windows_execution_mode: str,
) -> dict[str, Any]:
    scheduling_policy = dict(repo.get("scheduling_policy") or {})
    resource_classes = dict(repo.get("resource_classes") or {})
    windows_lanes = _coerce_lane_objects(list(repo.get("windows_full_lanes") or []))
    runtime_root = next(
        (
            str((lane.get("metadata") or {}).get("runtime_root") or "").strip()
            for lane in windows_lanes
            if str((lane.get("metadata") or {}).get("runtime_root") or "").strip()
        ),
        "",
    )
    workspace_root = next(
        (
            str(
                (lane.get("metadata") or {}).get("workspace_root")
                or lane.get("workspace_root")
                or ""
            ).strip()
            for lane in windows_lanes
            if str(
                (lane.get("metadata") or {}).get("workspace_root")
                or lane.get("workspace_root")
                or ""
            ).strip()
        ),
        "",
    )
    return {
        "host_id": "windows-owner-ci",
        "admission_mode": "dynamic_resource_based",
        "resource_budget": dict(resource_budget),
        "storage_budget_policy": dict(scheduling_policy.get("storage_budget_policy") or {}),
        "resource_classes": resource_classes,
        "reserve_runtime_headroom": True,
        "runtime_root": runtime_root or None,
        "workspace_root": workspace_root or None,
        "max_parallel_windows": int(scheduling_policy.get("max_parallel_windows") or 0),
        "rebalance_enabled": bool(scheduling_policy.get("rebalance_enabled", True)),
        "windows_execution_mode": windows_execution_mode,
        "cleanup_policy": {
            "stale_workspace_prune": True,
            "artifact_retention_enforced": True,
            "docker_cleanup_required": True,
        },
    }


def _reservation_from_shard(repo_id: str, shard: dict[str, Any]) -> ResourceReservation:
    metadata = dict(shard.get("metadata") or {})
    if isinstance(metadata.get("resource_reservation"), dict):
        return ResourceReservation.model_validate(
            dict(metadata.get("resource_reservation") or {})
        )
    return ResourceReservation.model_validate(_resource_reservation_for_shard(repo_id, shard))


def _active_resource_usage(
    runs: list[dict[str, Any]],
) -> tuple[dict[str, int], set[str], int]:
    usage = {"cpu": 0, "service": 0, "serial": 0}
    busy_parallel_groups: set[str] = set()
    active_run_count = 0
    for run in runs:
        run_has_active_shard = False
        repo_id = str(run.get("repo_id") or "").strip()
        for shard in list(run.get("shards") or []):
            status = str(shard.get("status") or "").strip().lower()
            if status not in _ACTIVE_SHARD_STATUSES:
                continue
            run_has_active_shard = True
            reservation = _reservation_from_shard(repo_id, shard)
            resource_class = reservation.resource_class.strip() or "cpu"
            usage[resource_class] = usage.get(resource_class, 0) + max(
                1, int(reservation.units or 1)
            )
            if reservation.parallel_group:
                busy_parallel_groups.add(reservation.parallel_group)
        if run_has_active_shard:
            active_run_count += 1
    return usage, busy_parallel_groups, active_run_count


def _capacity_snapshot_from_policy(
    *,
    policy: dict[str, Any],
    usage: dict[str, int],
    blocking_reasons: list[str] | None = None,
    metadata: dict[str, Any] | None = None,
) -> HostCapacitySnapshot:
    resource_budget = dict(policy.get("resource_budget") or {})
    return HostCapacitySnapshot(
        host_id=str(policy.get("host_id") or "windows-owner-ci"),
        cpu_slots_total=int(resource_budget.get("cpu") or 0),
        cpu_slots_used=int(usage.get("cpu") or 0),
        service_slots_total=int(resource_budget.get("service") or 0),
        service_slots_used=int(usage.get("service") or 0),
        serial_slots_total=int(resource_budget.get("serial") or 0),
        serial_slots_used=int(usage.get("serial") or 0),
        runtime_headroom_reserved=bool(policy.get("reserve_runtime_headroom", True)),
        blocking_reasons=list(blocking_reasons or []),
        metadata=dict(metadata or {}),
    )


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
                "ci_run_retry",
                "ci_run_cancel",
                "ci_run_store_github_receipt",
                "ci_run_store_release_receipt",
                "ci_run_publish_statuses",
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
            "ci_run_retry": self._handle_run_retry,
            "ci_run_cancel": self._handle_run_cancel,
            "ci_run_store_github_receipt": self._handle_store_github_receipt,
            "ci_run_store_release_receipt": self._handle_store_release_receipt,
            "ci_run_publish_statuses": self._handle_publish_statuses,
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
                "required_gate_categories": compiled.get("required_gate_categories") or [],
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
        preflight_violations = _collect_preflight_violations(
            repo=repo,
            compiled=compiled,
            mode=mode,
            request_context=dict(request.context),
        )
        if preflight_violations:
            principal_id = str(request.context.get("principal_id") or "").strip() or None
            session_id = str(request.context.get("session_id") or "").strip() or None
            app_id = str(request.context.get("app_id") or "").strip() or None
            commit_sha = str(
                request.context.get("git_sha")
                or metadata.get("git_sha")
                or metadata.get("head_sha")
                or ""
            ).strip() or None
            stored_feedback: list[dict[str, Any]] = []
            for payload in build_preflight_coaching_payloads(
                principal_id=principal_id,
                repo_id=str(repo["repo_id"]),
                commit_sha=commit_sha,
                violations=preflight_violations,
            ):
                stored_feedback.append(
                    await self._storage.record_agent_gap_event(
                        owner_id,
                        dedupe_key=_stable_coaching_key(
                            [
                                str(payload.get("gap_type") or ""),
                                principal_id,
                                str(payload.get("repo_id") or ""),
                                str((payload.get("metadata") or {}).get("rule_code") or ""),
                            ]
                        ),
                        session_id=session_id,
                        principal_id=principal_id,
                        app_id=app_id,
                        repo_id=str(payload.get("repo_id") or "").strip() or None,
                        run_id=None,
                        gap_type=str(payload.get("gap_type") or "agent_preflight"),
                        severity=str(payload.get("severity") or "high"),
                        blocker=bool(payload.get("blocker", True)),
                        detected_from=str(payload.get("detected_from") or "ci_run_preflight"),
                        required_capability=(
                            str(payload.get("required_capability") or "").strip() or None
                        ),
                        observed_request=dict(payload.get("observed_request") or {}),
                        suggested_fix=str(payload.get("suggested_fix") or "").strip() or None,
                        metadata=dict(payload.get("metadata") or {}),
                    )
                )
            return SkillResponse(
                request_id=request.id,
                success=True,
                message=(
                    f"Certification preflight rejected run start for `{repo['repo_id']}` "
                    "until the required common checks are attested."
                ),
                data={
                    "preflight": {
                        "accepted": False,
                        "mode": mode,
                        "violations": preflight_violations,
                    },
                    "coaching": build_agent_coaching_feedback(stored_feedback),
                },
            )
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
        plan["required_gate_categories"] = list(compiled.get("required_gate_categories") or [])
        plan["required_security_gate_ids"] = list(compiled.get("required_security_gate_ids") or [])
        plan["required_static_gate_ids"] = list(compiled.get("required_static_gate_ids") or [])
        plan["host_capacity_policy"] = dict(compiled.get("host_capacity_policy") or {})
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
                "principal_id": str(request.context.get("principal_id") or "").strip() or None,
                "session_id": str(request.context.get("session_id") or "").strip() or None,
                "app_id": str(request.context.get("app_id") or "").strip() or None,
                "git_sha": str(
                    request.context.get("git_sha")
                    or metadata.get("git_sha")
                    or metadata.get("head_sha")
                    or ""
                ).strip()
                or None,
                "preflight_checks": dict(
                    request.context.get("preflight_checks")
                    or metadata.get("preflight_checks")
                    or {}
                ),
                "compiled_plan_id": stored_compiled_plan["compiled_plan_id"],
                "windows_execution_mode": repo.get("windows_execution_mode"),
                "required_static_gates": list(compiled.get("required_static_gate_ids") or []),
                "required_security_gates": list(compiled.get("required_security_gate_ids") or []),
                "required_gate_categories": list(compiled.get("required_gate_categories") or []),
                "certification_required": mode == "certification",
                "certification_requirements": list(repo.get("certification_requirements") or []),
                "host_capacity_policy": dict(compiled.get("host_capacity_policy") or {}),
                "platform_canary": bool((repo.get("metadata") or {}).get("platform_canary")),
                "promotion_control_plane": "zetherion",
                "status_contexts": {
                    "merge": _status_contexts_for(repo)[0],
                    "deploy": _status_contexts_for(repo)[1],
                },
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
        repo_id = str(run.get("repo_id") or "").strip()
        pending = [
            shard
            for shard in list(run.get("shards") or [])
            if str(shard.get("status") or "").strip().lower() in {"queued_local", "planned"}
        ]
        active_runs = [
            run,
            *[
                item
                for item in await self._storage.list_runs(owner_id, limit=200)
                if str(item.get("run_id") or "").strip() != run_id
            ],
        ]
        usage, busy_parallel_groups, active_run_count = _active_resource_usage(active_runs)
        host_capacity_policy = dict(
            run.get("plan", {}).get("host_capacity_policy")
            or (run.get("metadata") or {}).get("host_capacity_policy")
            or {}
        )
        busy_groups = sorted(busy_parallel_groups)
        snapshot = _capacity_snapshot_from_policy(
            policy=host_capacity_policy,
            usage=usage,
            metadata={
                "active_run_count": active_run_count,
                "source": "owner_ci_runs",
            },
        )
        planned_usage = dict(usage)
        active_parallel_groups = set(busy_parallel_groups)
        admission_decisions: list[dict[str, Any]] = []
        admitted_shard_ids: list[str] = []
        blocked_shard_ids: list[str] = []
        for shard in pending:
            reservation = _reservation_from_shard(repo_id, shard)
            resource_class = reservation.resource_class.strip() or "cpu"
            units = max(1, int(reservation.units or 1))
            resource_budget = dict(host_capacity_policy.get("resource_budget") or {})
            total_slots = int(resource_budget.get(resource_class) or 0)
            blocking_reasons: list[str] = []
            if total_slots and planned_usage.get(resource_class, 0) + units > total_slots:
                current_usage = planned_usage.get(resource_class, 0)
                blocking_reasons.append(
                    f"{resource_class} budget exhausted ({current_usage}/{total_slots})"
                )
            if reservation.parallel_group and reservation.parallel_group in active_parallel_groups:
                blocking_reasons.append(
                    f"parallel group `{reservation.parallel_group}` already active"
                )
            decision = AdmissionDecision(
                admitted=not blocking_reasons,
                summary=(
                    f"Shard `{shard.get('lane_id')}` is ready for admission."
                    if not blocking_reasons
                    else f"Shard `{shard.get('lane_id')}` is blocked from admission."
                ),
                blocking_reasons=blocking_reasons,
                capacity_snapshot=_capacity_snapshot_from_policy(
                    policy=host_capacity_policy,
                    usage=planned_usage,
                    blocking_reasons=blocking_reasons,
                    metadata={
                        "resource_class": resource_class,
                        "parallel_group": reservation.parallel_group,
                    },
                ),
                reservations=[reservation],
                metadata={
                    "run_id": run_id,
                    "repo_id": repo_id,
                    "lane_id": str(shard.get("lane_id") or "").strip(),
                    "shard_id": str(shard.get("shard_id") or "").strip(),
                    "execution_target": str(shard.get("execution_target") or "").strip(),
                },
            )
            if decision.admitted:
                planned_usage[resource_class] = planned_usage.get(resource_class, 0) + units
                if reservation.parallel_group:
                    active_parallel_groups.add(reservation.parallel_group)
                admitted_shard_ids.append(str(shard.get("shard_id") or "").strip())
            else:
                blocked_shard_ids.append(str(shard.get("shard_id") or "").strip())
            admission_decisions.append(decision.model_dump(mode="json"))
        return SkillResponse(
            request_id=request.id,
            message=f"Prepared rebalance guidance for `{run_id}`.",
            data={
                "run": run,
                "rebalance": {
                    "requested": True,
                    "host_capacity_policy": host_capacity_policy,
                    "capacity_snapshot": snapshot.model_dump(mode="json"),
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
                    "admission_decisions": admission_decisions,
                    "admitted_shard_ids": admitted_shard_ids,
                    "blocked_shard_ids": blocked_shard_ids,
                },
            },
        )

    async def _handle_run_retry(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        run_id = str(request.context.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        run = await self._storage.get_run(owner_id, run_id)
        if run is None:
            raise ValueError(f"Run `{run_id}` not found")
        metadata = dict(run.get("metadata") or {})
        retry_metadata = {
            **metadata,
            **dict(request.context.get("metadata") or {}),
            "retry_of_run_id": run_id,
            "retry_requested_by": owner_id,
        }
        retry_request = SkillRequest(
            id=request.id,
            user_id=request.user_id,
            intent="ci_run_start",
            message=request.message,
            context={
                "owner_id": owner_id,
                "repo_id": str(run.get("repo_id") or "").strip(),
                "mode": str(
                    request.context.get("mode")
                    or metadata.get("mode")
                    or run.get("plan", {}).get("mode")
                    or "full"
                ).strip()
                or "full",
                "git_ref": str(
                    request.context.get("git_ref") or run.get("git_ref") or "main"
                ).strip()
                or "main",
                "git_sha": str(
                    request.context.get("git_sha") or metadata.get("git_sha") or ""
                ).strip()
                or None,
                "trigger": str(request.context.get("trigger") or "retry").strip() or "retry",
                "metadata": retry_metadata,
                "preflight_checks": (
                    request.context.get("preflight_checks")
                    or metadata.get("preflight_checks")
                    or None
                ),
            },
        )
        response = await self._handle_run_start(retry_request)
        return SkillResponse(
            request_id=request.id,
            message=(
                f"Retried run `{run_id}` as "
                f"`{((response.data.get('run') or {}).get('run_id') or '')}`."
            ),
            data={
                **dict(response.data or {}),
                "retried_run_id": run_id,
            },
        )

    async def _handle_run_cancel(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        run_id = str(request.context.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        run = await self._storage.get_run(owner_id, run_id)
        if run is None:
            raise ValueError(f"Run `{run_id}` not found")
        cancel_reason = str(request.context.get("reason") or "").strip() or None
        await self._storage.merge_run_metadata(
            owner_id,
            run_id,
            {
                "cancel_requested_by": owner_id,
                "cancel_requested_at": request.timestamp.isoformat(),
                "cancel_reason": cancel_reason,
                "cancel_mode": "best_effort_control_plane",
            },
        )
        updated = await self._storage.set_run_status(owner_id, run_id, "cancelled")
        return SkillResponse(
            request_id=request.id,
            message=f"Cancelled run `{run_id}`.",
            data={"run": updated or run, "cancelled": True},
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

    async def _handle_store_release_receipt(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        run_id = str(request.context.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        receipt = normalize_release_verification_receipt(
            dict(request.context.get("receipt") or {})
        ).model_dump(mode="json")
        run = await self._storage.merge_run_metadata(
            owner_id,
            run_id,
            {"release_verification": receipt},
        )
        if run is None:
            raise ValueError(f"Run `{run_id}` not found")
        return SkillResponse(
            request_id=request.id,
            message=f"Stored release verification receipt for `{run_id}`.",
            data={"run": run, "release_verification": receipt},
        )

    async def _handle_publish_statuses(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        run_id = str(request.context.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        run = await self._storage.get_run(owner_id, run_id)
        if run is None:
            raise ValueError(f"Run `{run_id}` not found")
        repo = await self._resolve_repo_profile(owner_id, str(run.get("repo_id") or "").strip())
        publish_result = await self._publish_github_statuses(repo=repo, run=run)
        stored = await self._storage.store_run_github_receipt(
            owner_id,
            run_id,
            {"published_statuses": publish_result},
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Published readiness statuses for `{run_id}`.",
            data={"run": stored or run, "published_statuses": publish_result},
        )

    async def _handle_run_promote(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        run_id = str(request.context.get("run_id") or "").strip()
        if not run_id:
            raise ValueError("run_id is required")
        run = await self._storage.get_run(owner_id, run_id)
        if run is None:
            raise ValueError(f"Run `{run_id}` not found")
        repo = await self._resolve_repo_profile(owner_id, str(run.get("repo_id") or "").strip())
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
        release_receipt = dict((run.get("metadata") or {}).get("release_verification") or {})
        local_repo_readiness, _ = await self._storage.get_local_repo_readiness(repo)
        merge_receipt, deploy_receipt, repo_readiness, workspace_readiness = (
            await self._build_readiness_receipts(
            repo=repo,
            run=run,
            review=review,
            release_receipt=release_receipt,
            requested_by=owner_id,
            local_receipt=local_repo_readiness,
            )
        )
        require_release_receipt = bool(
            (repo.get("promotion_policy") or {}).get("require_release_receipt")
        )
        if merge_receipt["state"] != "success" or (
            require_release_receipt and deploy_receipt["state"] != "success"
        ):
            updated = await self._storage.store_run_github_receipt(
                owner_id,
                run_id,
                {
                    "merge_readiness": merge_receipt,
                    "deploy_readiness": deploy_receipt,
                    "repo_readiness": repo_readiness,
                    "workspace_readiness": workspace_readiness,
                },
            )
            await self._storage.set_run_status(owner_id, run_id, "promotion_blocked")
            return SkillResponse(
                request_id=request.id,
                message=f"Run `{run_id}` failed readiness checks and cannot be promoted.",
                data={
                    "run": updated or run,
                    "promoted": False,
                    "merge_readiness": merge_receipt,
                    "deploy_readiness": deploy_receipt,
                    "repo_readiness": repo_readiness,
                    "workspace_readiness": workspace_readiness,
                },
            )
        updated = await self._storage.store_run_github_receipt(
            owner_id,
            run_id,
            {
                "merge_readiness": merge_receipt,
                "deploy_readiness": deploy_receipt,
                "repo_readiness": repo_readiness,
                "workspace_readiness": workspace_readiness,
                "promotion": {
                    "status": "zetherion_control_plane",
                    "requested_by": owner_id,
                },
            },
        )
        publish_result = await self._publish_github_statuses(repo=repo, run=updated or run)
        if publish_result:
            updated = await self._storage.store_run_github_receipt(
                owner_id,
                run_id,
                {"published_statuses": publish_result},
            )
        if updated is not None:
            await self._storage.set_run_status(owner_id, run_id, "ready_to_merge")
        final_run = await self._storage.get_run(owner_id, run_id)
        return SkillResponse(
            request_id=request.id,
            message=f"Run `{run_id}` is ready for Zetherion-controlled promotion.",
            data={
                "run": final_run or updated or run,
                "promoted": True,
                "merge_readiness": merge_receipt,
                "deploy_readiness": deploy_receipt,
                "repo_readiness": repo_readiness,
                "workspace_readiness": workspace_readiness,
                "published_statuses": publish_result,
            },
        )

    async def _build_readiness_receipts(
        self,
        *,
        repo: dict[str, Any],
        run: dict[str, Any],
        review: dict[str, Any],
        release_receipt: dict[str, Any],
        requested_by: str,
        local_receipt: Any | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], dict[str, Any]]:
        merge_context, deploy_context = _status_contexts_for(repo)
        sha = _infer_git_sha(run)
        repo_readiness = build_repo_readiness_receipt(
            repo=repo,
            run=run,
            review=review,
            release_receipt=release_receipt,
            local_receipt=local_receipt,
        )
        workspace_readiness = build_workspace_readiness_receipt([repo_readiness])

        merge_state = "success"
        merge_description = "Merge readiness approved by Zetherion."
        if bool(review.get("merge_blocked", True)):
            merge_state = "failure"
            merge_description = "Merge readiness is blocked by reviewer findings."
        elif repo_readiness.missing_categories:
            merge_state = "failure"
            merge_description = (
                "Required gate categories are incomplete: "
                + ", ".join(repo_readiness.missing_categories[:3])
            )
        elif repo_readiness.failed_required_paths:
            merge_state = "failure"
            merge_description = (
                "Required local shards failed: "
                + ", ".join(repo_readiness.failed_required_paths[:3])
            )
        elif any(
            str(shard.get("status") or "").strip().lower() in _DISCONNECTED_STATUSES
            for shard in list(run.get("shards") or [])
        ):
            merge_state = "pending"
            merge_description = "Worker receipts are still syncing to Zetherion."
        elif repo_readiness.missing_evidence:
            merge_state = "pending"
            merge_description = (
                "Readiness evidence is incomplete: "
                + ", ".join(repo_readiness.missing_evidence[:3])
            )
        merge_receipt = {
            "context": merge_context,
            "state": merge_state,
            "description": merge_description,
            "sha": sha,
            "requested_by": requested_by,
            "review_verdict": review.get("verdict"),
        }

        normalized_release = normalize_release_verification_receipt(
            release_receipt
        ).model_dump(mode="json")
        deploy_state = "pending"
        deploy_description = "Release verification receipt is still pending."
        release_status = str(normalized_release.get("status") or "").strip().lower()
        if repo_readiness.missing_categories:
            deploy_state = "failure"
            deploy_description = (
                "Deploy readiness is blocked by incomplete gate categories: "
                + ", ".join(repo_readiness.missing_categories[:3])
            )
        elif release_status in {"healthy", "success"} and int(
            normalized_release.get("blocker_count") or 0
        ) == 0:
            deploy_state = "success"
            deploy_description = "Deploy readiness receipt is green."
        elif release_status in {"deployed_but_unhealthy", "blocked", "failed"} or int(
            normalized_release.get("blocker_count") or 0
        ) > 0:
            deploy_state = "failure"
            deploy_description = (
                str(normalized_release.get("summary") or "").strip()
                or "Deploy readiness is blocked by release verification."
            )
        elif release_status == "degraded":
            deploy_state = "pending"
            deploy_description = (
                str(normalized_release.get("summary") or "").strip()
                or "Deploy readiness is degraded and awaiting verification."
            )
        deploy_receipt = {
            "context": deploy_context,
            "state": deploy_state,
            "description": deploy_description,
            "sha": sha,
            "requested_by": requested_by,
            "release_verification": normalized_release,
        }
        return (
            merge_receipt,
            deploy_receipt,
            repo_readiness.model_dump(mode="json"),
            workspace_readiness.model_dump(mode="json"),
        )

    async def _publish_github_statuses(
        self,
        *,
        repo: dict[str, Any],
        run: dict[str, Any],
    ) -> dict[str, Any]:
        github_repo = str(repo.get("github_repo") or "").strip()
        if not github_repo:
            return {"published": False, "reason": "github_repo_missing"}
        sha = _infer_git_sha(run)
        if sha is None:
            return {"published": False, "reason": "git_sha_missing"}
        settings = get_settings()
        token = settings.github_token.get_secret_value().strip() if settings.github_token else ""
        if not token:
            return {"published": False, "reason": "github_token_missing"}

        owner, repo_name = _parse_github_repo(github_repo)
        receipts = dict(run.get("github_receipts") or {})
        statuses = [
            dict(receipts.get("merge_readiness") or {}),
            dict(receipts.get("deploy_readiness") or {}),
        ]
        publish_targets = [status for status in statuses if status]
        if not publish_targets:
            return {"published": False, "reason": "receipts_missing"}

        target_url = str((run.get("metadata") or {}).get("status_target_url") or "").strip() or None
        client = GitHubClient(token)
        try:
            published_contexts: list[str] = []
            for status in publish_targets:
                context = str(status.get("context") or "").strip()
                state = str(status.get("state") or "").strip().lower()
                description = str(status.get("description") or "").strip()
                if not context or not state or not description:
                    continue
                await client.create_commit_status(
                    owner,
                    repo_name,
                    sha,
                    state=state,
                    context=context,
                    description=description,
                    target_url=target_url,
                )
                published_contexts.append(context)
            return {
                "published": bool(published_contexts),
                "sha": sha,
                "contexts": published_contexts,
            }
        finally:
            await client.close()

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
        mandatory_security_gates = _coerce_lane_objects(
            list(repo.get("mandatory_security_gates") or [])
        )
        local_fast_lanes = _coerce_lane_objects(list(repo.get("local_fast_lanes") or []))
        local_full_lanes = _coerce_lane_objects(list(repo.get("local_full_lanes") or []))
        windows_lanes = _coerce_lane_objects(list(repo.get("windows_full_lanes") or []))
        workspace_root = str((repo.get("allowed_paths") or [None])[0] or "").strip()
        selected = [*mandatory_static_gates, *mandatory_security_gates, *local_fast_lanes]
        if mode in {"full", "certification"}:
            selected.extend(local_full_lanes)
        if mode == "certification":
            selected.extend(windows_lanes)

        shards: list[dict[str, Any]] = []
        static_gate_ids = [str(lane.get("lane_id") or "") for lane in mandatory_static_gates]
        security_gate_ids = [
            str(lane.get("lane_id") or "") for lane in mandatory_security_gates
        ]
        static_gate_id_set = {lane_id for lane_id in static_gate_ids if lane_id}
        security_gate_id_set = {lane_id for lane_id in security_gate_ids if lane_id}
        windows_execution_mode = str(repo.get("windows_execution_mode") or "command").strip()
        schedule_policy = dict(repo.get("scheduling_policy") or {})
        resource_budget = {
            key: int(value)
            for key, value in dict(schedule_policy.get("resource_budgets") or {}).items()
        }
        host_capacity_policy = _host_capacity_policy_for(
            repo,
            resource_budget=resource_budget,
            windows_execution_mode=windows_execution_mode,
        )
        required_gate_categories = (
            list(_REQUIRED_CERTIFICATION_GATE_CATEGORIES) if mode == "certification" else []
        )
        validation_mode = _validation_mode_for_repo(str(repo["repo_id"]))

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
            shard.setdefault("validation_mode", validation_mode)
            shard.setdefault(
                "shard_purpose",
                str(shard.get("lane_label") or shard.get("lane_id") or "").strip(),
            )
            shard["metadata"].setdefault("workspace_root", shard.get("workspace_root"))
            resource_class = str(
                shard["metadata"].get("resource_class") or shard.get("resource_class") or "cpu"
            ).strip()
            shard["metadata"]["resource_class"] = resource_class
            shard["metadata"]["covered_required_paths"] = [
                str(value).strip()
                for value in list(
                    shard["metadata"].get("covered_required_paths")
                    or shard["metadata"].get("required_paths")
                    or []
                )
                if str(value).strip()
            ]
            if shard.get("timeout_seconds") is None:
                shard["timeout_seconds"] = (
                    int(shard["metadata"].get("timeout_seconds") or 0) or None
                )

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
                dependency_gate_ids = _dedupe_strings([*static_gate_ids, *security_gate_ids])
                if dependency_gate_ids:
                    shard["metadata"].setdefault("depends_on", dependency_gate_ids)
            gate_family = _gate_family_for_lane(
                shard,
                static_gate_ids=static_gate_id_set,
                security_gate_ids=security_gate_id_set,
            )
            required_category = _required_category_for_family(gate_family)
            shard["metadata"]["gate_family"] = gate_family
            shard["metadata"]["gate_kind"] = gate_family
            shard["metadata"]["blocking"] = True
            shard["blocking"] = True
            shard["expected_artifacts"] = list(
                dict(shard.get("artifact_contract") or {}).get("expects")
                or ["stdout", "stderr"]
            )
            if required_category is not None:
                shard["metadata"]["required_category"] = required_category
            shard["metadata"]["resource_reservation"] = _resource_reservation_for_shard(
                str(repo["repo_id"]),
                shard,
            )
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
        compiled = LocalGatePlan(
            repo_id=str(repo["repo_id"]),
            git_ref=git_ref,
            mode=mode,
            validation_mode=validation_mode,
            mode_label=validation_mode.replace("_", " "),
            windows_execution_mode=windows_execution_mode,
            resource_budget=resource_budget,
            schedule_tags=[mode, str(repo.get("stack_kind") or "")],
            retry_policy={
                "rerun_failed_shards": True,
                "max_attempts": 2 if mode == "certification" else 1,
            },
            debug_bundle_contract={
                "redact_display_logs": bool(
                    (repo.get("debug_policy") or {}).get("redact_display_logs", True)
                ),
                "retain_debug_bundle_days": int(
                    (repo.get("debug_policy") or {}).get("retain_debug_bundle_days", 14)
                ),
            },
            required_static_gate_ids=static_gate_ids,
            required_security_gate_ids=security_gate_ids,
            required_gate_categories=required_gate_categories,
            certification_requirements=list(repo.get("certification_requirements") or []),
            scheduled_canaries=list(repo.get("scheduled_canaries") or []),
            host_capacity_policy=host_capacity_policy,
            required_paths=sorted(
                {
                    path
                    for shard in shards
                    for path in list(
                        (shard.get("metadata") or {}).get("covered_required_paths") or []
                    )
                    if path
                }
            ),
            shards=shards,
        )
        return compiled.model_dump(mode="json")

    @staticmethod
    def _scope_id(owner_id: str, repo_id: str) -> str:
        return f"owner:{owner_id}:repo:{repo_id}"
