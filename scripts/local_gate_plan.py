#!/usr/bin/env python3
"""Build a deterministic local gate plan from changed files."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = REPO_ROOT / ".ci" / "local_gate_manifest.json"


@dataclass(frozen=True)
class PlanRequirement:
    id: str
    kind: str
    description: str
    pytest_targets: tuple[str, ...] = ()
    lane_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class RequiredPath:
    id: str
    description: str
    lane_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class LanePlan:
    lane_id: str
    lane_label: str
    resource_class: str
    timeout_seconds: int
    depends_on: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    covered_required_paths: tuple[str, ...] = ()
    cleanup_receipt_path: str | None = None
    service_slot: str | None = None
    release_blocking: bool = True


def _lane_family_for_lane_id(lane_id: str) -> str:
    normalized = str(lane_id or "").strip().lower()
    if normalized.startswith("z-unit") or "unit" in normalized:
        return "unit"
    if normalized.startswith("z-int") or "integration" in normalized:
        return "integration"
    if normalized.startswith("z-e2e") or "e2e" in normalized:
        return "e2e_authoritative"
    if normalized in {"lint", "check"}:
        return "static"
    return "integration"


def _normalize_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    for path in paths:
        stripped = path.strip().replace("\\", "/")
        if stripped:
            normalized.append(stripped)
    return normalized


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def load_manifest(path: Path = MANIFEST_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _run_git(*args: str) -> str:
    completed = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def collect_changed_paths(*, base_ref: str, head_ref: str) -> list[str]:
    if not base_ref or not head_ref:
        return []

    for diff_range in (f"{base_ref}...{head_ref}", f"{base_ref}..{head_ref}"):
        completed = subprocess.run(
            ["git", "diff", "--name-only", "--diff-filter=ACMR", diff_range],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode == 0:
            return _normalize_paths(completed.stdout.splitlines())
    return []


def build_plan(*, changed_paths: list[str], manifest: dict[str, Any]) -> dict[str, Any]:
    normalized_paths = _normalize_paths(changed_paths)
    requirements_catalog = manifest.get("requirements", {})
    protected_globs = tuple(manifest.get("protected_globs", []))
    rules = manifest.get("rules", [])

    matched_rules: list[dict[str, Any]] = []
    requirement_ids: list[str] = []
    required_path_ids: list[str] = []
    protected_paths_by_rule: dict[str, set[str]] = {}

    for rule in rules:
        rule_id = str(rule["id"])
        patterns = tuple(rule.get("patterns", []))
        matched = sorted(path for path in normalized_paths if _matches_any(path, patterns))
        if not matched:
            continue
        if bool(rule.get("covers_protected", False)):
            protected_paths_by_rule[rule_id] = set(matched)
        for requirement_id in rule.get("requirements", []):
            requirement_ids.append(str(requirement_id))
        for required_path_id in rule.get("required_paths", []):
            required_path_ids.append(str(required_path_id))
        matched_rules.append(
            {
                "id": rule_id,
                "patterns": list(patterns),
                "matched_files": matched,
                "requirements": [str(value) for value in rule.get("requirements", [])],
                "required_paths": [str(value) for value in rule.get("required_paths", [])],
            }
        )

    ordered_requirement_ids = sorted(dict.fromkeys(requirement_ids))
    requirements: list[PlanRequirement] = []
    for requirement_id in ordered_requirement_ids:
        requirement = requirements_catalog[requirement_id]
        requirements.append(
            PlanRequirement(
                id=requirement_id,
                kind=str(requirement["kind"]),
                description=str(requirement["description"]),
                pytest_targets=tuple(str(value) for value in requirement.get("pytest_targets", [])),
                lane_ids=tuple(str(value) for value in requirement.get("lane_ids", [])),
            )
        )

    required_path_catalog = manifest.get("required_path_catalog", {})
    ordered_required_path_ids = sorted(dict.fromkeys(required_path_ids))
    required_paths: list[RequiredPath] = []
    for required_path_id in ordered_required_path_ids:
        payload = required_path_catalog.get(required_path_id)
        if not isinstance(payload, dict):
            continue
        required_paths.append(
            RequiredPath(
                id=required_path_id,
                description=str(payload.get("description") or required_path_id),
                lane_ids=tuple(str(value) for value in payload.get("lane_ids", [])),
            )
        )

    lane_catalog = manifest.get("lane_catalog", {})
    lane_ids = {
        lane_id
        for requirement in requirements
        for lane_id in requirement.lane_ids
        if lane_id
    } | {
        lane_id
        for required_path in required_paths
        for lane_id in required_path.lane_ids
        if lane_id
    }
    lanes: list[LanePlan] = []
    for lane_id in sorted(lane_ids):
        payload = lane_catalog.get(lane_id)
        if not isinstance(payload, dict):
            continue
        lanes.append(
            LanePlan(
                lane_id=lane_id,
                lane_label=str(payload.get("lane_label") or lane_id),
                resource_class=str(payload.get("resource_class") or "cpu"),
                timeout_seconds=int(payload.get("timeout_seconds") or 0),
                depends_on=tuple(str(value) for value in payload.get("depends_on", [])),
                artifact_paths=tuple(str(value) for value in payload.get("artifact_paths", [])),
                covered_required_paths=tuple(
                    str(value) for value in payload.get("covered_required_paths", [])
                ),
                cleanup_receipt_path=str(payload.get("cleanup_receipt_path") or "").strip()
                or None,
                service_slot=str(payload.get("service_slot") or "").strip() or None,
                release_blocking=bool(payload.get("release_blocking", True)),
            )
        )

    unmapped_protected_paths = sorted(
        path
        for path in normalized_paths
        if _matches_any(path, protected_globs)
        and not any(path in matched_paths for matched_paths in protected_paths_by_rule.values())
    )

    return {
        "changed_files": normalized_paths,
        "matched_rules": matched_rules,
        "requirements": [
            {
                "id": requirement.id,
                "kind": requirement.kind,
                "description": requirement.description,
                "pytest_targets": list(requirement.pytest_targets),
                "lane_ids": list(requirement.lane_ids),
            }
            for requirement in requirements
        ],
        "required_paths": [
            {
                "id": required_path.id,
                "description": required_path.description,
                "lane_ids": list(required_path.lane_ids),
            }
            for required_path in required_paths
        ],
        "lanes": [
            {
                "lane_id": lane.lane_id,
                "lane_label": lane.lane_label,
                "validation_mode": "zetherion_alone",
                "lane_family": _lane_family_for_lane_id(lane.lane_id),
                "shard_purpose": lane.lane_label,
                "blocking": lane.release_blocking,
                "resource_class": lane.resource_class,
                "timeout_seconds": lane.timeout_seconds,
                "depends_on": list(lane.depends_on),
                "artifact_paths": list(lane.artifact_paths),
                "expected_artifacts": list(lane.artifact_paths),
                "covered_required_paths": list(lane.covered_required_paths),
                "cleanup_receipt_path": lane.cleanup_receipt_path,
                "service_slot": lane.service_slot,
                "release_blocking": lane.release_blocking,
            }
            for lane in lanes
        ],
        "unmapped_protected_paths": unmapped_protected_paths,
    }


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-ref", default="origin/main")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--changed-file", action="append", default=[])
    parser.add_argument("--output", default="")
    parser.add_argument("--fail-on-unmapped", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    manifest = load_manifest()
    changed_paths = (
        _normalize_paths(args.changed_file)
        if args.changed_file
        else collect_changed_paths(base_ref=args.base_ref, head_ref=args.head_ref)
    )
    plan = build_plan(changed_paths=changed_paths, manifest=manifest)
    payload = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "base_ref": args.base_ref,
        "head_ref": args.head_ref,
        **plan,
    }

    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    print(json.dumps(payload, indent=2))

    if args.fail_on_unmapped and payload["unmapped_protected_paths"]:
        print("ERROR: changed protected paths are missing local-gate coverage:")
        for path in payload["unmapped_protected_paths"]:
            print(f"  - {path}")
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
