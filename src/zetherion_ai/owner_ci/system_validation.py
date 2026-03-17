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
    SystemCandidateRepoRef,
    SystemCandidateSet,
    SystemReadinessReceipt,
    SystemRunPlan,
    SystemShard,
    SystemValidationProfile,
)
from zetherion_ai.owner_ci.profiles import default_repo_profile
from zetherion_ai.skills.ci_controller import CiControllerSkill

REPO_ROOT = Path(__file__).resolve().parents[3]
WORKSPACE_ROOT = REPO_ROOT.parent
CGS_WORKSPACE_ROOT = Path(
    os.environ.get("CGS_WORKSPACE_ROOT")
    or WORKSPACE_ROOT / "catalyst-group-solutions"
)
DEFAULT_CGS_MANIFEST_PATH = (
    CGS_WORKSPACE_ROOT / "scripts" / "cgs-ai" / "shard-manifest.json"
)
DEFAULT_COMBINED_MANIFEST_PATH = REPO_ROOT / ".ci" / "system_validation_manifest.json"
DEFAULT_SYSTEM_ID = "cgs-zetherion"
REPO_MODE_IDS = {
    "zetherion-ai": "zetherion_alone",
    "catalyst-group-solutions": "cgs_alone",
}


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _normalize_mode(
    mode: dict[str, Any],
    *,
    default_validation_mode: str | None = None,
) -> SystemValidationProfile:
    mode_repo_ids = [
        str(repo_id).strip()
        for repo_id in list(mode.get("repo_ids") or [])
        if str(repo_id).strip()
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
                lane_id=str(raw_shard.get("lane_id") or raw_shard.get("shard_id") or "")
                or None,
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
                    "windows_execution_mode": str(
                        plan.get("windows_execution_mode") or "command"
                    ),
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
        _normalize_mode(mode)
        for mode in list(matrix.get("modes") or [])
        if isinstance(mode, dict)
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
            selected_shards.append(
                SystemShard.model_validate(shard_payload)
            )

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
            selected_shards.append(
                SystemShard.model_validate(shard_payload)
            )

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
        and (
            profile_by_mode.get(mode_id) is None
            or not bool(profile_by_mode[mode_id].available)
        )
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
            f"Combined-system validation is ready with {len(blocking_shards)} "
            "blocking shard(s)."
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
                summary=(
                    "The combined-system validation profile is unavailable or incomplete."
                ),
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
    return [feedback.model_dump(mode="json")]
