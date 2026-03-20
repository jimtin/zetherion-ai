"""Shared system-validation helpers for multi-repo local and control-plane use."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from zetherion_ai.owner_ci.models import (
    AgentCoachingFeedback,
    AgentCoachingFinding,
    AgentInstructionRecommendation,
    CorrelationContext,
    RunGraph,
    RunGraphArtifactRef,
    RunGraphDiagnosticRef,
    RunGraphNode,
    SystemCandidateRepoRef,
    SystemCandidateSet,
    SystemReadinessReceipt,
    SystemRun,
    SystemRunPlan,
    SystemRunUsageSummary,
    SystemShard,
    SystemValidationProfile,
)
from zetherion_ai.owner_ci.profiles import default_repo_profile
from zetherion_ai.skills.ci_controller import CiControllerSkill

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = REPO_ROOT.parent
CGS_WORKSPACE_ROOT = Path(
    os.environ.get("CGS_WORKSPACE_ROOT") or WORKSPACE_ROOT / "catalyst-group-solutions"
)
DEFAULT_CGS_MANIFEST_PATH = CGS_WORKSPACE_ROOT / "scripts" / "cgs-ai" / "shard-manifest.json"
DEFAULT_COMBINED_MANIFEST_PATH = REPO_ROOT / ".ci" / "system_validation_manifest.json"
DEFAULT_SYSTEM_ID = "cgs-zetherion"
REPO_MODE_IDS = {
    "zetherion-ai": "zetherion_alone",
    "catalyst-group-solutions": "cgs_alone",
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _normalized_state(value: str | None) -> str:
    candidate = str(value or "").strip().lower()
    if candidate in {"passed", "success", "succeeded", "ready", "healthy"}:
        return "succeeded"
    if candidate in {"failed", "blocked", "cancelled", "error"}:
        return "failed"
    if candidate in {"running", "claimed", "in_progress"}:
        return "running"
    if candidate in {"planned", "queued", "pending"}:
        return "queued"
    if candidate in {"skipped"}:
        return "skipped"
    return candidate or "unknown"


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_mode(
    mode: dict[str, Any],
    *,
    default_validation_mode: str | None = None,
) -> SystemValidationProfile:
    mode_repo_ids = [
        str(repo_id).strip() for repo_id in list(mode.get("repo_ids") or []) if str(repo_id).strip()
    ]
    shards: list[SystemShard] = []
    for raw_shard in list(mode.get("shards") or []):
        if not isinstance(raw_shard, dict):
            continue
        repo_ids = [
            str(repo_id).strip()
            for repo_id in list(raw_shard.get("repo_ids") or [])
            if str(repo_id).strip()
        ]
        if not repo_ids and raw_shard.get("repo_id"):
            repo_ids = [str(raw_shard.get("repo_id") or "").strip()]
        if not repo_ids:
            repo_ids = list(mode_repo_ids)
        shards.append(
            SystemShard(
                shard_id=str(
                    raw_shard.get("shard_id")
                    or raw_shard.get("lane_id")
                    or raw_shard.get("purpose")
                    or ""
                ),
                lane_id=str(raw_shard.get("lane_id") or raw_shard.get("shard_id") or "") or None,
                lane_label=str(
                    raw_shard.get("lane_label")
                    or raw_shard.get("label")
                    or raw_shard.get("purpose")
                    or raw_shard.get("shard_id")
                    or ""
                )
                or None,
                lane_family=str(raw_shard.get("lane_family") or "integration"),
                validation_mode=str(
                    raw_shard.get("validation_mode")
                    or default_validation_mode
                    or mode.get("mode_id")
                    or "combined_system"
                ),
                purpose=str(raw_shard.get("purpose") or raw_shard.get("shard_purpose") or ""),
                blocking=bool(raw_shard.get("blocking", True)),
                repo_ids=repo_ids,
                depends_on=[
                    str(dependency).strip()
                    for dependency in list(raw_shard.get("depends_on") or [])
                    if str(dependency).strip()
                ],
                expected_artifacts=[
                    str(item).strip()
                    for item in list(raw_shard.get("expected_artifacts") or [])
                    if str(item).strip()
                ],
                required_paths=[
                    str(item).strip()
                    for item in list(raw_shard.get("required_paths") or [])
                    if str(item).strip()
                ],
                resource_class=str(raw_shard.get("resource_class") or "cpu"),
                scenario_family=str(raw_shard.get("scenario_family") or "").strip() or None,
                metadata={
                    "command": raw_shard.get("command"),
                    "commands": raw_shard.get("commands"),
                    "execution_target": raw_shard.get("execution_target"),
                    "required_paths": list(raw_shard.get("required_paths") or []),
                },
            )
        )

    return SystemValidationProfile(
        mode_id=str(mode.get("mode_id") or "combined_system"),
        mode_label=str(mode.get("mode_label") or mode.get("mode_id") or "Validation mode"),
        description=str(mode.get("description") or ""),
        available=bool(mode.get("available", True)),
        repo_ids=mode_repo_ids,
        lane_families=[
            str(family).strip()
            for family in list(mode.get("lane_families") or [])
            if str(family).strip()
        ],
        blocking_categories=[
            str(category).strip()
            for category in list(mode.get("blocking_categories") or [])
            if str(category).strip()
        ],
        shards=shards,
        metadata=dict(mode.get("metadata") or {}),
    )


def _unavailable_mode(
    mode_id: str,
    mode_label: str,
    *,
    description: str,
    repo_ids: list[str],
    reason: str,
) -> SystemValidationProfile:
    return SystemValidationProfile(
        mode_id=mode_id,
        mode_label=mode_label,
        description=description,
        available=False,
        repo_ids=repo_ids,
        metadata={"unavailable_reason": reason},
    )


def _build_zetherion_mode() -> SystemValidationProfile:
    repo = default_repo_profile("zetherion-ai")
    skill = CiControllerSkill(storage=cast(Any, None))
    plan = skill._compile_run_plan(repo=repo, mode="full", git_ref="HEAD")
    shards: list[SystemShard] = []
    lane_families = {
        str((shard.get("metadata") or {}).get("gate_family") or "integration")
        for shard in list(plan.get("shards") or [])
    }
    for shard in list(plan.get("shards") or []):
        metadata = dict(shard.get("metadata") or {})
        shards.append(
            SystemShard(
                shard_id=str(shard.get("shard_id") or shard.get("lane_id") or ""),
                lane_id=str(shard.get("lane_id") or ""),
                lane_label=str(shard.get("lane_label") or shard.get("lane_id") or "") or None,
                validation_mode=str(shard.get("validation_mode") or "zetherion_alone"),
                lane_family=str(metadata.get("gate_family") or "integration"),
                purpose=str(
                    shard.get("shard_purpose")
                    or shard.get("lane_label")
                    or shard.get("lane_id")
                    or ""
                ),
                blocking=bool(shard.get("blocking", True)),
                repo_ids=["zetherion-ai"],
                depends_on=[
                    str(dependency).strip()
                    for dependency in list(
                        metadata.get("depends_on") or shard.get("depends_on") or []
                    )
                    if str(dependency).strip()
                ],
                expected_artifacts=[
                    str(item).strip()
                    for item in list(shard.get("expected_artifacts") or [])
                    if str(item).strip()
                ],
                required_paths=[
                    str(item).strip()
                    for item in list(metadata.get("covered_required_paths") or [])
                    if str(item).strip()
                ],
                resource_class=str(metadata.get("resource_class") or "cpu"),
                metadata={
                    "command": list(shard.get("command") or []),
                    "execution_target": str(shard.get("execution_target") or "local_mac"),
                    "windows_execution_mode": str(plan.get("windows_execution_mode") or "command"),
                },
            )
        )
    return SystemValidationProfile(
        mode_id="zetherion_alone",
        mode_label="Zetherion alone",
        description=(
            "Repo-local validation for Zetherion owner-CI, runtime health, "
            "diagnostics, and scheduler logic."
        ),
        available=True,
        repo_ids=["zetherion-ai"],
        lane_families=sorted(lane_families),
        blocking_categories=["static", "security", "unit", "integration"],
        shards=shards,
        metadata={
            "git_ref": str(plan.get("git_ref") or "HEAD"),
            "resource_budget": dict(plan.get("resource_budget") or {}),
            "windows_execution_mode": str(plan.get("windows_execution_mode") or "command"),
        },
    )


def _build_cgs_mode(*, manifest_path: Path) -> SystemValidationProfile:
    if not manifest_path.is_file():
        return _unavailable_mode(
            "cgs_alone",
            "CGS alone",
            description=(
                "Repo-local validation for CGS admin/public app behavior "
                "and control-plane routes."
            ),
            repo_ids=["catalyst-group-solutions"],
            reason=f"missing manifest: {manifest_path}",
        )
    manifest = _load_json(manifest_path)
    mode = _normalize_mode(
        {
            "mode_id": "cgs_alone",
            "mode_label": "CGS alone",
            "description": (
                "Repo-local validation for CGS public/admin behavior, AI Ops "
                "routes, and owner-CI surfaces."
            ),
            "available": True,
            "repo_ids": [str(manifest.get("repo_id") or "catalyst-group-solutions")],
            "lane_families": sorted(
                {
                    str(shard.get("lane_family") or "integration")
                    for shard in list(manifest.get("shards") or [])
                    if isinstance(shard, dict)
                }
            ),
            "blocking_categories": ["static", "security", "unit", "integration"],
            "shards": list(manifest.get("shards") or []),
            "metadata": {
                "resource_limits": dict(manifest.get("resource_limits") or {}),
                "manifest_path": str(manifest_path),
            },
        },
        default_validation_mode=str(manifest.get("validation_mode") or "cgs_alone"),
    )
    return mode


def _build_combined_mode(*, manifest_path: Path) -> SystemValidationProfile:
    if not manifest_path.is_file():
        return _unavailable_mode(
            "combined_system",
            "CGS + Zetherion together",
            description=(
                "Controlled local cross-repo validation across the CGS and "
                "Zetherion contract boundary."
            ),
            repo_ids=["zetherion-ai", "catalyst-group-solutions"],
            reason=f"missing manifest: {manifest_path}",
        )
    manifest = _load_json(manifest_path)
    mode = _normalize_mode(manifest, default_validation_mode="combined_system")
    if not mode.mode_id:
        mode.mode_id = "combined_system"
    if not mode.mode_label:
        mode.mode_label = "CGS + Zetherion together"
    if not mode.description:
        mode.description = (
            "Controlled local contract validation across the CGS and Zetherion "
            "control-plane boundary."
        )
    if not mode.blocking_categories:
        mode.blocking_categories = ["combined_system"]
    mode.metadata.setdefault("manifest_path", str(manifest_path))
    return mode


def build_validation_matrix(
    *,
    cgs_manifest_path: Path = DEFAULT_CGS_MANIFEST_PATH,
    combined_manifest_path: Path = DEFAULT_COMBINED_MANIFEST_PATH,
) -> dict[str, Any]:
    modes = [
        _build_zetherion_mode(),
        _build_cgs_mode(manifest_path=cgs_manifest_path),
        _build_combined_mode(manifest_path=combined_manifest_path),
    ]
    return {
        "generated_at": _utc_now_iso(),
        "workspace_root": str(WORKSPACE_ROOT),
        "modes": [mode.model_dump(mode="json") for mode in modes],
        "metadata": {
            "cgs_manifest_path": str(cgs_manifest_path),
            "combined_manifest_path": str(combined_manifest_path),
        },
    }


def _candidate_set_from_input(value: Any) -> SystemCandidateSet:
    if isinstance(value, SystemCandidateSet):
        return value
    if isinstance(value, dict):
        return SystemCandidateSet.model_validate(value)
    if isinstance(value, list):
        repos = []
        for entry in value:
            if isinstance(entry, dict):
                repos.append(entry)
        return SystemCandidateSet(
            repos=[SystemCandidateRepoRef.model_validate(item) for item in repos]
        )
    return SystemCandidateSet()


def _repo_refs(candidate_set: SystemCandidateSet) -> dict[str, SystemCandidateRepoRef]:
    return {repo.repo_id: repo for repo in candidate_set.repos if repo.repo_id}


def _system_run_node_id(system_run_id: str) -> str:
    return f"system-run:{system_run_id}"


def _system_shard_node_id(system_run_id: str, shard_id: str) -> str:
    return f"system-shard:{system_run_id}:{shard_id}"


def _system_step_node_id(system_run_id: str, shard_id: str, step_id: str) -> str:
    return f"system-step:{system_run_id}:{shard_id}:{step_id}"


def _system_artifact_id(system_run_id: str, shard_id: str, artifact_key: str) -> str:
    return f"system-artifact:{system_run_id}:{shard_id}:{artifact_key}"


def _system_diagnostic_id(system_run_id: str, shard_id: str, code: str, index: int) -> str:
    return f"system-diagnostic:{system_run_id}:{shard_id}:{code}:{index}"


def _parse_iso_datetime(value: Any) -> datetime | None:
    candidate = str(value or "").strip()
    if not candidate:
        return None
    try:
        normalized = candidate.replace("Z", "+00:00")
        return datetime.fromisoformat(normalized)
    except ValueError:
        return None


def _duration_seconds(started_at: Any, completed_at: Any) -> float:
    started = _parse_iso_datetime(started_at)
    completed = _parse_iso_datetime(completed_at)
    if started is None or completed is None:
        return 0.0
    return max((completed - started).total_seconds(), 0.0)


def resolve_system_run_batches(
    shards: list[SystemShard] | list[dict[str, Any]],
) -> list[list[dict[str, Any]]]:
    shard_map = {
        str(
            SystemShard.model_validate(shard).shard_id
            if not isinstance(shard, SystemShard)
            else shard.shard_id
        ): (
            SystemShard.model_validate(shard).model_dump(mode="json")
            if not isinstance(shard, SystemShard)
            else shard.model_dump(mode="json")
        )
        for shard in shards
    }
    known_ids = set(shard_map)
    dependency_map: dict[str, set[str]] = {
        shard_id: {
            dependency
            for dependency in list(shard.get("depends_on") or [])
            if str(dependency).strip() in known_ids
        }
        for shard_id, shard in shard_map.items()
    }
    dependents: dict[str, set[str]] = {shard_id: set() for shard_id in shard_map}
    indegree = {shard_id: len(dependencies) for shard_id, dependencies in dependency_map.items()}

    for shard_id, dependencies in dependency_map.items():
        for dependency in dependencies:
            dependents.setdefault(dependency, set()).add(shard_id)

    ready = sorted(shard_id for shard_id, degree in indegree.items() if degree == 0)
    batches: list[list[dict[str, Any]]] = []
    resolved_count = 0

    while ready:
        current_ids = list(ready)
        ready = []
        batches.append([dict(shard_map[shard_id]) for shard_id in current_ids])
        resolved_count += len(current_ids)
        for shard_id in current_ids:
            for dependent_id in sorted(dependents.get(shard_id) or set()):
                indegree[dependent_id] -= 1
                if indegree[dependent_id] == 0:
                    ready.append(dependent_id)
        ready.sort()

    if resolved_count != len(shard_map):
        unresolved = sorted(shard_id for shard_id, degree in indegree.items() if degree > 0)
        raise ValueError(
            "System run plan contains dependency cycles involving: " + ", ".join(unresolved)
        )
    return batches


def build_system_run_plan(
    *,
    candidate_set: SystemCandidateSet | dict[str, Any],
    cgs_manifest_path: Path = DEFAULT_CGS_MANIFEST_PATH,
    combined_manifest_path: Path = DEFAULT_COMBINED_MANIFEST_PATH,
) -> dict[str, Any]:
    normalized_candidates = _candidate_set_from_input(candidate_set)
    matrix = build_validation_matrix(
        cgs_manifest_path=cgs_manifest_path,
        combined_manifest_path=combined_manifest_path,
    )
    profiles = [
        _normalize_mode(mode) for mode in list(matrix.get("modes") or []) if isinstance(mode, dict)
    ]
    profile_by_mode = {profile.mode_id: profile for profile in profiles}
    selected_profiles: list[SystemValidationProfile] = []
    selected_shards: list[SystemShard] = []
    blocking_categories: list[str] = []
    seen_modes: set[str] = set()
    repo_refs = _repo_refs(normalized_candidates)

    for repo in normalized_candidates.repos:
        mode_id = REPO_MODE_IDS.get(repo.repo_id)
        if not mode_id:
            continue
        profile = profile_by_mode.get(mode_id)
        if profile is None:
            continue
        if mode_id not in seen_modes:
            selected_profiles.append(profile)
            seen_modes.add(mode_id)
            blocking_categories.extend(profile.blocking_categories)
        for shard in profile.shards:
            shard_payload = shard.model_dump(mode="json")
            shard_payload["metadata"] = {
                **dict(shard.metadata or {}),
                "candidate_repo_ref": repo.model_dump(mode="json"),
            }
            selected_shards.append(SystemShard.model_validate(shard_payload))

    combined_profile = profile_by_mode.get("combined_system")
    combined_repo_ids = set(combined_profile.repo_ids if combined_profile else [])
    candidate_repo_ids = {repo.repo_id for repo in normalized_candidates.repos if repo.repo_id}
    if combined_profile and combined_repo_ids and combined_repo_ids.issubset(candidate_repo_ids):
        if combined_profile.mode_id not in seen_modes:
            selected_profiles.append(combined_profile)
            seen_modes.add(combined_profile.mode_id)
            blocking_categories.extend(combined_profile.blocking_categories)
        candidate_ref_map = {
            repo_id: repo_refs[repo_id].model_dump(mode="json")
            for repo_id in sorted(combined_repo_ids)
            if repo_id in repo_refs
        }
        for shard in combined_profile.shards:
            shard_payload = shard.model_dump(mode="json")
            shard_payload["metadata"] = {
                **dict(shard.metadata or {}),
                "candidate_repo_refs": candidate_ref_map,
            }
            selected_shards.append(SystemShard.model_validate(shard_payload))

    unique_blocking_categories = list(dict.fromkeys(blocking_categories))
    summary = (
        f"Compiled {len(selected_shards)} validation shard(s) across "
        f"{len(selected_profiles)} profile(s) for `{normalized_candidates.system_id}`."
    )
    plan = SystemRunPlan(
        system_id=normalized_candidates.system_id or DEFAULT_SYSTEM_ID,
        mode_id=normalized_candidates.mode_id or "combined_system",
        candidate_set=normalized_candidates,
        profiles=selected_profiles,
        shards=selected_shards,
        blocking_categories=unique_blocking_categories,
        summary=summary,
        metadata={
            "generated_at": _utc_now_iso(),
            "available_mode_ids": sorted(profile_by_mode),
            "selected_mode_ids": [profile.mode_id for profile in selected_profiles],
        },
    )
    return plan.model_dump(mode="json")


def build_system_rollout_readiness(
    *,
    candidate_set: SystemCandidateSet | dict[str, Any],
    cgs_manifest_path: Path = DEFAULT_CGS_MANIFEST_PATH,
    combined_manifest_path: Path = DEFAULT_COMBINED_MANIFEST_PATH,
) -> dict[str, Any]:
    normalized_candidates = _candidate_set_from_input(candidate_set)
    validation_matrix = build_validation_matrix(
        cgs_manifest_path=cgs_manifest_path,
        combined_manifest_path=combined_manifest_path,
    )
    available_profiles = {
        profile.mode_id: profile
        for profile in [
            _normalize_mode(mode)
            for mode in list(validation_matrix.get("modes") or [])
            if isinstance(mode, dict)
        ]
    }
    plan = SystemRunPlan.model_validate(
        build_system_run_plan(
            candidate_set=normalized_candidates,
            cgs_manifest_path=cgs_manifest_path,
            combined_manifest_path=combined_manifest_path,
        )
    )
    profile_by_mode = {profile.mode_id: profile for profile in plan.profiles}
    combined_catalog_profile = available_profiles.get("combined_system")
    required_repo_ids = set(
        combined_catalog_profile.repo_ids
        if combined_catalog_profile and combined_catalog_profile.repo_ids
        else [repo.repo_id for repo in normalized_candidates.repos]
    )
    candidate_repo_ids = {repo.repo_id for repo in normalized_candidates.repos if repo.repo_id}
    missing_repo_ids = sorted(required_repo_ids - candidate_repo_ids)
    missing_mode_ids = sorted(
        mode_id
        for repo_id, mode_id in REPO_MODE_IDS.items()
        if repo_id in candidate_repo_ids
        and (profile_by_mode.get(mode_id) is None or not bool(profile_by_mode[mode_id].available))
    )
    combined_missing = (
        combined_catalog_profile is None
        or not bool(combined_catalog_profile.available)
        or not required_repo_ids.issubset(candidate_repo_ids)
    )

    blocking_shards = sorted(
        {
            shard.shard_id
            for shard in plan.shards
            if shard.blocking and shard.validation_mode == "combined_system"
        }
    )
    blocking = bool(missing_repo_ids or missing_mode_ids or combined_missing)
    recommended_next_steps = []
    if missing_repo_ids:
        recommended_next_steps.append(
            {
                "step_id": "attach-missing-repos",
                "title": "Attach the missing repo candidates",
                "instructions": [
                    "Provide a candidate ref for each required repo in the system run.",
                    f"Missing repos: {', '.join(missing_repo_ids)}.",
                ],
                "blocking": True,
            }
        )
    if missing_mode_ids or combined_missing:
        recommended_next_steps.append(
            {
                "step_id": "restore-system-validation-manifests",
                "title": "Restore the required validation profiles",
                "instructions": [
                    "Ensure repo-local shard manifests are present and readable.",
                    "Ensure the combined-system validation manifest is available before promotion.",
                ],
                "blocking": True,
            }
        )
    if not recommended_next_steps:
        recommended_next_steps.append(
            {
                "step_id": "run-system-validation",
                "title": "Run the compiled repo and combined-system shards",
                "instructions": [
                    "Run repo-local validation first for each candidate repo.",
                    "Run the combined-system blocking shards before merge or release.",
                ],
                "blocking": False,
            }
        )

    status = "blocked" if blocking else "ready"
    summary = (
        "System validation is blocked until all required repo candidates and "
        "validation profiles are present."
        if blocking
        else (
            f"Combined-system validation is ready with {len(blocking_shards)} " "blocking shard(s)."
        )
    )
    receipt = SystemReadinessReceipt(
        system_id=normalized_candidates.system_id or DEFAULT_SYSTEM_ID,
        mode_id=normalized_candidates.mode_id or "combined_system",
        status=status,
        blocking=blocking,
        summary=summary,
        blocker_count=len(missing_repo_ids) + len(missing_mode_ids) + int(combined_missing),
        blocking_shards=blocking_shards,
        missing_repo_ids=missing_repo_ids,
        recommended_next_steps=recommended_next_steps,
        metadata={
            "missing_mode_ids": missing_mode_ids,
            "selected_profile_count": len(plan.profiles),
            "selected_shard_count": len(plan.shards),
        },
        checked_at=_utc_now_iso(),
    )
    return receipt.model_dump(mode="json")


def build_system_coaching(
    *,
    candidate_set: SystemCandidateSet | dict[str, Any],
    principal_id: str | None = None,
    cgs_manifest_path: Path = DEFAULT_CGS_MANIFEST_PATH,
    combined_manifest_path: Path = DEFAULT_COMBINED_MANIFEST_PATH,
) -> list[dict[str, Any]]:
    normalized_candidates = _candidate_set_from_input(candidate_set)
    readiness = SystemReadinessReceipt.model_validate(
        build_system_rollout_readiness(
            candidate_set=normalized_candidates,
            cgs_manifest_path=cgs_manifest_path,
            combined_manifest_path=combined_manifest_path,
        )
    )
    findings: list[AgentCoachingFinding] = []
    recommendations: list[AgentInstructionRecommendation] = []

    if readiness.missing_repo_ids:
        findings.append(
            AgentCoachingFinding(
                finding_id="system-missing-repo-candidates",
                coaching_kind="system_validation",
                rule_code="missing_system_repo_candidates",
                summary=(
                    "The multi-repo candidate set is incomplete for combined-system validation."
                ),
                remediation=(
                    "Attach candidate refs for every required repo before relying on a "
                    "combined-system readiness result."
                ),
                blocking=True,
                metadata={"missing_repo_ids": readiness.missing_repo_ids},
            )
        )
        recommendations.append(
            AgentInstructionRecommendation(
                title="Add the missing system repo candidates",
                instructions=[
                    f"Attach repo refs for: {', '.join(readiness.missing_repo_ids)}.",
                    "Recompile the system run plan once all candidate refs are present.",
                ],
                agents_md_update=(
                    "When planning cross-repo work, always provide candidate refs for every "
                    "required repo before requesting combined-system validation."
                ),
            )
        )

    if readiness.blocking and not findings:
        findings.append(
            AgentCoachingFinding(
                finding_id="system-validation-profile-unavailable",
                coaching_kind="system_validation",
                rule_code="missing_system_validation_profile",
                summary=("The combined-system validation profile is unavailable or incomplete."),
                remediation=(
                    "Restore the repo-local and combined-system manifests so the system run "
                    "can compile deterministically."
                ),
                blocking=True,
                metadata=dict(readiness.metadata or {}),
            )
        )
        recommendations.append(
            AgentInstructionRecommendation(
                title="Restore the system validation manifests",
                instructions=[
                    "Verify the repo-local shard manifests are checked in and readable.",
                    "Verify the combined-system manifest is available before promotion.",
                ],
                agents_md_update=(
                    "Always keep repo-local shard manifests and the combined-system manifest "
                    "in sync before promoting cross-repo changes."
                ),
            )
        )

    if not findings:
        findings.append(
            AgentCoachingFinding(
                finding_id="system-validation-ready",
                coaching_kind="system_validation",
                rule_code="system_validation_ready",
                summary="The multi-repo candidate set is ready for combined-system validation.",
                remediation=(
                    "Run the repo-local and combined-system blocking shards before merge or "
                    "release."
                ),
                blocking=False,
                metadata={"blocking_shards": readiness.blocking_shards},
            )
        )
        recommendations.append(
            AgentInstructionRecommendation(
                title="Run the combined-system shards",
                instructions=[
                    "Run repo-local validation first for each candidate repo.",
                    (
                        "Run the combined-system blocking shards and inspect any "
                        "node-scoped diagnostics."
                    ),
                ],
                agents_md_update=(
                    "For tightly coupled repos, treat repo-local validation and combined-system "
                    "validation as separate mandatory checkpoints."
                ),
            )
        )

    feedback = AgentCoachingFeedback(
        feedback_id=f"{normalized_candidates.system_id}:system-validation:coaching",
        principal_id=principal_id,
        scope="system_run",
        status="open",
        blocking=readiness.blocking,
        recurrence_count=1,
        confidence=1.0,
        summary=readiness.summary,
        findings=findings,
        recommendations=recommendations,
        metadata={
            "system_id": normalized_candidates.system_id,
            "candidate_set": normalized_candidates.model_dump(mode="json"),
            "rollout_readiness": readiness.model_dump(mode="json"),
        },
        created_at=_utc_now_iso(),
        updated_at=_utc_now_iso(),
    )
    return [feedback.model_dump(mode="json", exclude_none=True)]


def build_system_run_usage_summary(
    *,
    system_run_id: str,
    system_id: str,
    mode_id: str,
    candidate_set: SystemCandidateSet | dict[str, Any],
    execution: dict[str, Any] | None,
) -> dict[str, Any]:
    normalized_candidates = _candidate_set_from_input(candidate_set)
    execution_payload = dict(execution or {})
    shard_results = [
        dict(item) for item in list(execution_payload.get("shards") or []) if isinstance(item, dict)
    ]
    step_results = [
        dict(step)
        for shard in shard_results
        for step in list(shard.get("steps") or [])
        if isinstance(step, dict)
    ]
    total_shard_seconds = round(
        sum(
            _duration_seconds(shard.get("started_at"), shard.get("completed_at"))
            for shard in shard_results
        ),
        3,
    )
    total_step_seconds = round(
        sum(
            _duration_seconds(step.get("started_at"), step.get("completed_at"))
            for step in step_results
        ),
        3,
    )
    artifact_count = sum(
        len(list(shard.get("expected_artifacts") or [])) for shard in shard_results
    )
    usage = SystemRunUsageSummary(
        system_run_id=system_run_id,
        system_id=system_id,
        mode_id=mode_id,
        repo_ids=[repo.repo_id for repo in normalized_candidates.repos if repo.repo_id],
        shard_count=len(shard_results),
        passed_shard_count=sum(
            1 for shard in shard_results if str(shard.get("status")) == "passed"
        ),
        failed_shard_count=sum(
            1 for shard in shard_results if str(shard.get("status")) == "failed"
        ),
        skipped_shard_count=sum(
            1 for shard in shard_results if str(shard.get("status")) == "skipped"
        ),
        step_count=len(step_results),
        total_runtime_seconds=total_shard_seconds,
        total_step_seconds=total_step_seconds,
        billable_minutes=round(total_step_seconds / 60.0, 2),
        artifact_count=artifact_count,
        metadata={
            "all_passed": bool(execution_payload.get("all_passed", False)),
            "batch_count": len(list(execution_payload.get("batches") or [])),
        },
        generated_at=_utc_now_iso(),
    )
    return usage.model_dump(mode="json")


def build_system_run_report(system_run: dict[str, Any]) -> dict[str, Any]:
    normalized = SystemRun.model_validate(system_run)
    run_node_id = _system_run_node_id(normalized.system_run_id)
    nodes: list[RunGraphNode] = [
        RunGraphNode(
            node_id=run_node_id,
            kind="run",
            label=normalized.system_id,
            state=_normalized_state(normalized.status),
            run_id=normalized.system_run_id,
            started_at=normalized.started_at or normalized.created_at,
            completed_at=normalized.completed_at,
            metadata={
                "system_id": normalized.system_id,
                "mode_id": normalized.mode_id,
                "repo_ids": [repo.repo_id for repo in normalized.candidate_set.repos],
            },
        )
    ]
    artifacts: list[RunGraphArtifactRef] = []
    diagnostics: list[RunGraphDiagnosticRef] = []
    findings: list[dict[str, Any]] = []
    artifact_ids_by_node: dict[str, list[str]] = {}
    diagnostic_ids_by_node: dict[str, list[str]] = {}
    shard_results = [
        dict(item)
        for item in list(normalized.execution.get("shards") or [])
        if isinstance(item, dict)
    ]
    shard_result_by_id = {
        str(shard.get("shard_id") or "").strip(): shard
        for shard in shard_results
        if str(shard.get("shard_id") or "").strip()
    }
    processed_shard_ids: set[str] = set()

    def append_shard_payload(
        *,
        shard_id: str,
        lane_id: str | None,
        lane_label: str | None,
        lane_family: str,
        validation_mode: str,
        purpose: str,
        blocking: bool,
        repo_ids: list[str],
        depends_on: list[str],
        required_paths: list[str],
        expected_artifacts: list[str],
        shard_result: dict[str, Any],
    ) -> None:
        processed_shard_ids.add(shard_id)
        shard_node_id = _system_shard_node_id(normalized.system_run_id, shard_id)
        nodes.append(
            RunGraphNode(
                node_id=shard_node_id,
                kind="shard",
                label=lane_label or lane_id or shard_id,
                parent_id=run_node_id,
                dependency_ids=[
                    _system_shard_node_id(normalized.system_run_id, dependency)
                    for dependency in depends_on
                ],
                state=_normalized_state(
                    str(shard_result.get("status") or normalized.status or "planned")
                ),
                run_id=normalized.system_run_id,
                shard_id=shard_id,
                started_at=str(shard_result.get("started_at") or "").strip() or None,
                completed_at=str(shard_result.get("completed_at") or "").strip() or None,
                metadata={
                    "validation_mode": validation_mode,
                    "lane_family": lane_family,
                    "repo_ids": repo_ids,
                    "required_paths": required_paths,
                    "blocking": blocking,
                },
            )
        )
        for artifact_key in expected_artifacts:
            artifact_id = _system_artifact_id(
                normalized.system_run_id,
                shard_id,
                artifact_key,
            )
            artifacts.append(
                RunGraphArtifactRef(
                    artifact_id=artifact_id,
                    node_id=shard_node_id,
                    kind="expected_artifact",
                    title=artifact_key,
                    state="ready" if shard_result else "queued",
                    metadata={"validation_mode": validation_mode},
                )
            )
            artifact_ids_by_node.setdefault(shard_node_id, []).append(artifact_id)

        for step_index, raw_step in enumerate(list(shard_result.get("steps") or []), start=1):
            if not isinstance(raw_step, dict):
                continue
            step_id = (
                str(raw_step.get("step_id") or f"step-{step_index}").strip() or f"step-{step_index}"
            )
            step_node_id = _system_step_node_id(normalized.system_run_id, shard_id, step_id)
            nodes.append(
                RunGraphNode(
                    node_id=step_node_id,
                    kind="step",
                    label=str(raw_step.get("label") or step_id).strip() or step_id,
                    parent_id=shard_node_id,
                    state=_normalized_state(str(raw_step.get("status") or "unknown")),
                    run_id=normalized.system_run_id,
                    shard_id=shard_id,
                    step_id=step_id,
                    started_at=str(raw_step.get("started_at") or "").strip() or None,
                    completed_at=str(raw_step.get("completed_at") or "").strip() or None,
                    metadata={
                        "repo_id": str(raw_step.get("repo_id") or "").strip() or None,
                        "cwd": str(raw_step.get("cwd") or "").strip() or None,
                        "command": list(raw_step.get("command") or []),
                        "return_code": raw_step.get("return_code"),
                    },
                )
            )

        shard_status = str(shard_result.get("status") or "planned").strip() or "planned"
        if shard_status in {"failed", "skipped"}:
            code = "system_shard_failed" if shard_status == "failed" else "system_shard_skipped"
            diagnostic_id = _system_diagnostic_id(
                normalized.system_run_id,
                shard_id,
                code,
                len(diagnostics) + 1,
            )
            diagnostics.append(
                RunGraphDiagnosticRef(
                    diagnostic_id=diagnostic_id,
                    node_id=shard_node_id,
                    code=code,
                    severity="high" if blocking else "medium",
                    summary=(
                        f"System shard `{shard_id}` {shard_status} while validating "
                        f"{', '.join(repo_ids) or normalized.system_id}."
                    ),
                    blocking=blocking,
                    created_at=(
                        str(shard_result.get("completed_at") or "").strip() or normalized.updated_at
                    ),
                    metadata={
                        "validation_mode": validation_mode,
                        "required_paths": required_paths,
                    },
                )
            )
            diagnostic_ids_by_node.setdefault(shard_node_id, []).append(diagnostic_id)
            findings.append(
                {
                    "finding_id": diagnostic_id,
                    "type": code,
                    "code": code,
                    "severity": "high" if blocking else "medium",
                    "blocking": blocking,
                    "summary": f"Shard `{shard_id}` {shard_status} during system validation.",
                    "root_cause_summary": purpose or lane_label or shard_id,
                    "recommended_fix": (
                        "Inspect the failing shard steps and rerun the blocking "
                        "combined-system path after correcting the contract or "
                        "validation issue."
                    ),
                    "node_id": shard_node_id,
                    "shard_id": shard_id,
                    "repo_ids": repo_ids,
                    "required_paths": required_paths,
                    "evidence_ref_ids": [],
                }
            )

    for shard in normalized.plan.shards:
        shard_result = dict(shard_result_by_id.get(shard.shard_id) or {})
        append_shard_payload(
            shard_id=shard.shard_id,
            lane_id=shard.lane_id,
            lane_label=shard.lane_label,
            lane_family=shard.lane_family,
            validation_mode=shard.validation_mode,
            purpose=shard.purpose,
            blocking=bool(shard.blocking),
            repo_ids=list(shard.repo_ids),
            depends_on=list(shard.depends_on),
            required_paths=list(shard.required_paths),
            expected_artifacts=list(shard.expected_artifacts),
            shard_result=shard_result,
        )

    for shard_id, shard_result in shard_result_by_id.items():
        if shard_id in processed_shard_ids:
            continue
        append_shard_payload(
            shard_id=shard_id,
            lane_id=str(shard_result.get("lane_id") or "").strip() or None,
            lane_label=str(shard_result.get("lane_label") or "").strip() or None,
            lane_family=str(shard_result.get("lane_family") or "combined_system"),
            validation_mode=str(shard_result.get("validation_mode") or "combined_system"),
            purpose=str(shard_result.get("purpose") or "").strip(),
            blocking=bool(shard_result.get("blocking", True)),
            repo_ids=[
                str(repo_id).strip()
                for repo_id in list(shard_result.get("repo_ids") or [])
                if str(repo_id).strip()
            ],
            depends_on=[
                str(dependency).strip()
                for dependency in list(shard_result.get("depends_on") or [])
                if str(dependency).strip()
            ],
            required_paths=[
                str(path).strip()
                for path in list(shard_result.get("required_paths") or [])
                if str(path).strip()
            ],
            expected_artifacts=[
                str(artifact).strip()
                for artifact in list(shard_result.get("expected_artifacts") or [])
                if str(artifact).strip()
            ],
            shard_result=shard_result,
        )

    if normalized.readiness.blocking:
        readiness_diag_id = _system_diagnostic_id(
            normalized.system_run_id,
            "system",
            "system_run_readiness_blocked",
            len(diagnostics) + 1,
        )
        diagnostics.append(
            RunGraphDiagnosticRef(
                diagnostic_id=readiness_diag_id,
                node_id=run_node_id,
                code="system_run_readiness_blocked",
                severity="high",
                summary=normalized.readiness.summary,
                blocking=True,
                created_at=normalized.readiness.checked_at or normalized.updated_at,
                metadata=dict(normalized.readiness.metadata or {}),
            )
        )
        diagnostic_ids_by_node.setdefault(run_node_id, []).append(readiness_diag_id)
        findings.append(
            {
                "finding_id": readiness_diag_id,
                "type": "system_run_readiness_blocked",
                "code": "system_run_readiness_blocked",
                "severity": "high",
                "blocking": True,
                "summary": normalized.readiness.summary,
                "root_cause_summary": normalized.readiness.summary,
                "recommended_fix": (
                    "Attach the missing repo candidates or restore the required "
                    "validation profiles before rerunning the system validation."
                ),
                "node_id": run_node_id,
                "shard_id": None,
                "repo_ids": normalized.readiness.missing_repo_ids,
                "required_paths": [],
                "evidence_ref_ids": [],
            }
        )

    for node in nodes:
        node.artifact_ids = artifact_ids_by_node.get(node.node_id, [])
        node.diagnostic_ids = diagnostic_ids_by_node.get(node.node_id, [])

    graph = RunGraph(
        run_id=normalized.system_run_id,
        generated_at=_utc_now_iso(),
        state=_normalized_state(normalized.status),
        nodes=nodes,
        artifacts=artifacts,
        diagnostics=diagnostics,
        evidence_references=[],
        metadata={
            "system_id": normalized.system_id,
            "mode_id": normalized.mode_id,
            "repo_ids": [repo.repo_id for repo in normalized.candidate_set.repos],
        },
    ).model_dump(mode="json")
    correlation_context = CorrelationContext(
        run_id=normalized.system_run_id,
        commit_sha=None,
        environment=str(normalized.metadata.get("environment") or "").strip() or None,
        trace_ids=[],
        request_ids=[],
        services=[repo.repo_id for repo in normalized.candidate_set.repos if repo.repo_id],
        containers=[],
        metadata={
            "system_id": normalized.system_id,
            "mode_id": normalized.mode_id,
        },
    ).model_dump(mode="json")
    recommended_next_actions = [
        step.title for step in normalized.readiness.recommended_next_steps if step.title
    ]
    diagnostic_summary = {
        "system_run_id": normalized.system_run_id,
        "system_id": normalized.system_id,
        "status": normalized.status,
        "blocking": normalized.readiness.blocking
        or any(bool(item.get("blocking")) for item in findings),
        "finding_count": len(findings),
        "blocking_finding_count": sum(1 for item in findings if bool(item.get("blocking"))),
        "recommended_next_actions": recommended_next_actions,
        "generated_at": _utc_now_iso(),
    }
    coaching_payload = [
        feedback.model_dump(mode="json", exclude_none=True)
        for feedback in normalized.coaching
    ]
    package_files = [
        {"kind": "run_graph", "path": "run_report/run_graph.json"},
        {"kind": "correlation_context", "path": "run_report/correlation_context.json"},
        {"kind": "diagnostic_summary", "path": "run_report/diagnostic_summary.json"},
        {"kind": "diagnostic_findings", "path": "run_report/diagnostic_findings.json"},
        {"kind": "artifacts_index", "path": "run_report/artifacts/index.json"},
        {"kind": "evidence_index", "path": "run_report/evidence/index.json"},
        {"kind": "coaching", "path": "run_report/coaching.json"},
    ]
    return {
        "system_run_id": normalized.system_run_id,
        "system_id": normalized.system_id,
        "mode_id": normalized.mode_id,
        "status": normalized.status,
        "candidate_set": normalized.candidate_set.model_dump(mode="json"),
        "plan": normalized.plan.model_dump(mode="json"),
        "readiness": normalized.readiness.model_dump(mode="json"),
        "run_graph": graph,
        "correlation_context": correlation_context,
        "diagnostic_summary": diagnostic_summary,
        "diagnostic_findings": findings,
        "diagnostic_artifacts": [],
        "coverage_summary": {},
        "coverage_gaps": {},
        "correlated_incidents": [],
        "artifacts": [artifact.model_dump(mode="json") for artifact in artifacts],
        "evidence": [],
        "all_evidence_references": [],
        "coaching": coaching_payload,
        "usage_summary": (
            normalized.usage_summary.model_dump(mode="json")
            if normalized.usage_summary is not None
            else None
        ),
        "package": {
            "root": "run_report",
            "files": package_files,
        },
        "generated_at": _utc_now_iso(),
    }
