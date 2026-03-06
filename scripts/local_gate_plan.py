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
        matched_rules.append(
            {
                "id": rule_id,
                "patterns": list(patterns),
                "matched_files": matched,
                "requirements": [str(value) for value in rule.get("requirements", [])],
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
            }
            for requirement in requirements
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
