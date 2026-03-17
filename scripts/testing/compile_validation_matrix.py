#!/usr/bin/env python3
"""Compile a machine-readable local validation matrix across Zetherion and CGS."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

from zetherion_ai.owner_ci.profiles import default_repo_profile
from zetherion_ai.skills.ci_controller import CiControllerSkill

REPO_ROOT = Path(__file__).resolve().parents[2]
WORKSPACE_ROOT = REPO_ROOT.parent
CGS_WORKSPACE_ROOT = Path(
    os.environ.get("CGS_WORKSPACE_ROOT")
    or WORKSPACE_ROOT / "catalyst-group-solutions"
)
DEFAULT_CGS_MANIFEST_PATH = (
    CGS_WORKSPACE_ROOT
    / "scripts"
    / "cgs-ai"
    / "shard-manifest.json"
)
DEFAULT_COMBINED_MANIFEST_PATH = REPO_ROOT / ".ci" / "system_validation_manifest.json"


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _unavailable_mode(
    mode_id: str,
    mode_label: str,
    *,
    description: str,
    repo_ids: list[str],
    reason: str,
) -> dict[str, Any]:
    return {
        "mode_id": mode_id,
        "mode_label": mode_label,
        "description": description,
        "available": False,
        "repo_ids": repo_ids,
        "lane_families": [],
        "blocking_categories": [],
        "shards": [],
        "metadata": {"unavailable_reason": reason},
    }


def build_zetherion_mode() -> dict[str, Any]:
    repo = default_repo_profile("zetherion-ai")
    skill = CiControllerSkill(storage=cast(Any, None))
    plan = skill._compile_run_plan(repo=repo, mode="full", git_ref="HEAD")
    shards = []
    lane_families = {
        str((shard.get("metadata") or {}).get("gate_family") or "integration")
        for shard in list(plan.get("shards") or [])
    }
    for shard in list(plan.get("shards") or []):
        metadata = dict(shard.get("metadata") or {})
        shards.append(
            {
                "repo_id": "zetherion-ai",
                "shard_id": str(shard.get("shard_id") or shard.get("lane_id") or ""),
                "lane_id": str(shard.get("lane_id") or ""),
                "lane_label": str(shard.get("lane_label") or shard.get("lane_id") or ""),
                "validation_mode": str(shard.get("validation_mode") or "zetherion_alone"),
                "lane_family": str(metadata.get("gate_family") or "integration"),
                "purpose": str(
                    shard.get("shard_purpose")
                    or shard.get("lane_label")
                    or shard.get("lane_id")
                    or ""
                ),
                "blocking": bool(shard.get("blocking", True)),
                "resource_class": str(metadata.get("resource_class") or "cpu"),
                "depends_on": list(metadata.get("depends_on") or shard.get("depends_on") or []),
                "expected_artifacts": list(shard.get("expected_artifacts") or []),
                "command": list(shard.get("command") or []),
                "execution_target": str(shard.get("execution_target") or "local_mac"),
                "required_paths": list(metadata.get("covered_required_paths") or []),
            }
        )
    return {
        "mode_id": "zetherion_alone",
        "mode_label": "Zetherion alone",
        "description": (
            "Repo-local validation for Zetherion owner-CI, runtime health, "
            "diagnostics, and scheduler logic."
        ),
        "available": True,
        "repo_ids": ["zetherion-ai"],
        "lane_families": sorted(lane_families),
        "blocking_categories": ["static", "security", "unit", "integration"],
        "shards": shards,
        "metadata": {
            "git_ref": str(plan.get("git_ref") or "HEAD"),
            "resource_budget": dict(plan.get("resource_budget") or {}),
            "windows_execution_mode": str(plan.get("windows_execution_mode") or "command"),
        },
    }


def build_cgs_mode(*, manifest_path: Path) -> dict[str, Any]:
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
    shards = []
    lane_families = set()
    for shard in list(manifest.get("shards") or []):
        if not isinstance(shard, dict):
            continue
        lane_family = str(shard.get("lane_family") or "integration")
        lane_families.add(lane_family)
        shards.append(
            {
                "repo_id": str(manifest.get("repo_id") or "catalyst-group-solutions"),
                "shard_id": str(shard.get("lane_id") or ""),
                "lane_id": str(shard.get("lane_id") or ""),
                "lane_label": str(shard.get("label") or shard.get("lane_id") or ""),
                "validation_mode": str(
                    shard.get("validation_mode") or manifest.get("validation_mode") or "cgs_alone"
                ),
                "lane_family": lane_family,
                "purpose": str(shard.get("shard_purpose") or shard.get("label") or ""),
                "blocking": bool(shard.get("release_blocking", True)),
                "resource_class": str(shard.get("resource_class") or "cpu"),
                "depends_on": list(shard.get("depends_on") or []),
                "expected_artifacts": list(shard.get("expected_artifacts") or []),
                "command": str(shard.get("command") or ""),
                "required_paths": list(shard.get("required_paths") or []),
            }
        )
    return {
        "mode_id": "cgs_alone",
        "mode_label": "CGS alone",
        "description": (
            "Repo-local validation for CGS public/admin behavior, AI Ops "
            "routes, and owner-CI surfaces."
        ),
        "available": True,
        "repo_ids": [str(manifest.get("repo_id") or "catalyst-group-solutions")],
        "lane_families": sorted(lane_families),
        "blocking_categories": ["static", "security", "unit", "integration"],
        "shards": shards,
        "metadata": {
            "resource_limits": dict(manifest.get("resource_limits") or {}),
            "manifest_path": str(manifest_path),
        },
    }


def build_combined_mode(*, manifest_path: Path) -> dict[str, Any]:
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
    shards = []
    for shard in list(manifest.get("shards") or []):
        if not isinstance(shard, dict):
            continue
        shards.append(
            {
                "repo_ids": list(manifest.get("repo_ids") or []),
                "shard_id": str(shard.get("shard_id") or ""),
                "lane_id": str(shard.get("shard_id") or ""),
                "lane_label": str(shard.get("purpose") or shard.get("shard_id") or ""),
                "validation_mode": "combined_system",
                "lane_family": str(shard.get("lane_family") or "combined_system"),
                "scenario_family": str(shard.get("scenario_family") or ""),
                "purpose": str(shard.get("purpose") or ""),
                "blocking": bool(shard.get("blocking", True)),
                "resource_class": str(shard.get("resource_class") or "cpu"),
                "depends_on": list(shard.get("depends_on") or []),
                "expected_artifacts": list(shard.get("expected_artifacts") or []),
                "commands": list(shard.get("commands") or []),
            }
        )
    return {
        "mode_id": "combined_system",
        "mode_label": str(manifest.get("mode_label") or "CGS + Zetherion together"),
        "description": str(manifest.get("description") or ""),
        "available": True,
        "repo_ids": list(manifest.get("repo_ids") or []),
        "lane_families": sorted(
            {
                str(shard.get("lane_family") or "combined_system")
                for shard in list(manifest.get("shards") or [])
                if isinstance(shard, dict)
            }
        ),
        "blocking_categories": ["combined_system"],
        "shards": shards,
        "metadata": {"manifest_path": str(manifest_path)},
    }


def build_validation_matrix(
    *,
    cgs_manifest_path: Path = DEFAULT_CGS_MANIFEST_PATH,
    combined_manifest_path: Path = DEFAULT_COMBINED_MANIFEST_PATH,
) -> dict[str, Any]:
    modes = [
        build_zetherion_mode(),
        build_cgs_mode(manifest_path=cgs_manifest_path),
        build_combined_mode(manifest_path=combined_manifest_path),
    ]
    return {
        "generated_at": _utc_now_iso(),
        "workspace_root": str(WORKSPACE_ROOT),
        "modes": modes,
        "metadata": {
            "cgs_manifest_path": str(cgs_manifest_path),
            "combined_manifest_path": str(combined_manifest_path),
        },
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cgs-manifest", default=str(DEFAULT_CGS_MANIFEST_PATH))
    parser.add_argument("--combined-manifest", default=str(DEFAULT_COMBINED_MANIFEST_PATH))
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    payload = build_validation_matrix(
        cgs_manifest_path=Path(args.cgs_manifest),
        combined_manifest_path=Path(args.combined_manifest),
    )
    rendered = f"{json.dumps(payload, indent=2)}\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
