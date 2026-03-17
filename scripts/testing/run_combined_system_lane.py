#!/usr/bin/env python3
"""Run or inspect one combined-system local validation shard."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST_PATH = REPO_ROOT / ".ci" / "system_validation_manifest.json"
DEFAULT_WORKSPACE_ROOT = Path(os.environ.get("ZETHERION_WORKSPACE_ROOT") or REPO_ROOT.parent)
DEFAULT_CGS_WORKSPACE_ROOT = Path(
    os.environ.get("CGS_WORKSPACE_ROOT")
    or DEFAULT_WORKSPACE_ROOT / "catalyst-group-solutions"
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def load_manifest(path: Path = DEFAULT_MANIFEST_PATH) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def available_shards(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    return [dict(item) for item in list(manifest.get("shards") or []) if isinstance(item, dict)]


def validate_manifest(manifest: dict[str, Any]) -> list[dict[str, Any]]:
    shards = available_shards(manifest)
    shard_ids: list[str] = []
    seen_ids: set[str] = set()
    for shard in shards:
        shard_id = str(shard.get("shard_id") or "").strip()
        if not shard_id:
            raise ValueError("Combined-system manifest shard is missing shard_id")
        if shard_id in seen_ids:
            raise ValueError(f"Combined-system manifest contains duplicate shard_id `{shard_id}`")
        seen_ids.add(shard_id)
        shard_ids.append(shard_id)
        commands = list(shard.get("commands") or [])
        if not commands:
            raise ValueError(f"Combined-system shard `{shard_id}` has no commands")
        for raw_step in commands:
            if not isinstance(raw_step, dict):
                raise ValueError(
                    f"Combined-system shard `{shard_id}` has a non-object command entry"
                )
            repo_id = str(raw_step.get("repo_id") or "").strip()
            if not repo_id:
                raise ValueError(f"Combined-system shard `{shard_id}` is missing repo_id")
            command = [
                str(part)
                for part in list(raw_step.get("command") or [])
                if str(part).strip()
            ]
            if not command:
                raise ValueError(
                    f"Combined-system shard `{shard_id}` has an empty command for {repo_id}"
                )
    resolve_batches(shards)
    return shards


def _internal_dependencies(
    shard: dict[str, Any],
    *,
    known_ids: set[str],
) -> set[str]:
    return {
        str(dependency or "").strip()
        for dependency in list(shard.get("depends_on") or [])
        if str(dependency or "").strip() in known_ids
    }


def _select_shards(
    shards: list[dict[str, Any]],
    *,
    shard_ids: list[str] | None = None,
    include_dependencies: bool = False,
) -> list[dict[str, Any]]:
    if not shard_ids:
        return [dict(shard) for shard in shards]
    requested_ids = {str(shard_id).strip() for shard_id in shard_ids if str(shard_id).strip()}
    if not requested_ids:
        return []
    shard_map = {str(shard.get("shard_id") or "").strip(): dict(shard) for shard in shards}
    missing = sorted(shard_id for shard_id in requested_ids if shard_id not in shard_map)
    if missing:
        raise ValueError(f"Unknown combined-system shards: {', '.join(missing)}")

    selected_ids = set(requested_ids)
    if include_dependencies:
        pending = list(requested_ids)
        while pending:
            current = pending.pop()
            shard = shard_map[current]
            for dependency in list(shard.get("depends_on") or []):
                dependency_id = str(dependency or "").strip()
                if dependency_id and dependency_id not in selected_ids:
                    selected_ids.add(dependency_id)
                    pending.append(dependency_id)
    return [dict(shard_map[shard_id]) for shard_id in shard_map if shard_id in selected_ids]


def resolve_batches(shards: list[dict[str, Any]]) -> list[list[dict[str, Any]]]:
    shard_map = {str(shard.get("shard_id") or "").strip(): dict(shard) for shard in shards}
    known_ids = set(shard_map)
    dependency_map: dict[str, set[str]] = {
        shard_id: _internal_dependencies(shard, known_ids=known_ids)
        for shard_id, shard in shard_map.items()
    }
    dependents: dict[str, set[str]] = {shard_id: set() for shard_id in shard_map}
    indegree = {shard_id: len(dependencies) for shard_id, dependencies in dependency_map.items()}

    for shard_id, dependencies in dependency_map.items():
        for dependency in dependencies:
            dependents.setdefault(dependency, set()).add(shard_id)

    ready = [shard_id for shard_id, degree in indegree.items() if degree == 0]
    ready.sort()
    batches: list[list[dict[str, Any]]] = []
    resolved_count = 0

    while ready:
        current_ids = ready
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
            "Combined-system manifest contains dependency cycles involving: "
            + ", ".join(unresolved)
        )

    return batches


def _repo_root_for(repo_id: str, *, workspace_root: Path) -> Path:
    if repo_id == "zetherion-ai":
        zetherion_root = Path(os.environ.get("ZETHERION_WORKSPACE_ROOT") or REPO_ROOT)
        if zetherion_root.is_dir():
            return zetherion_root
        return workspace_root / "zetherion-ai"
    if repo_id == "catalyst-group-solutions":
        return Path(os.environ.get("CGS_WORKSPACE_ROOT") or DEFAULT_CGS_WORKSPACE_ROOT)
    raise ValueError(f"Unsupported repo_id for combined-system shard: {repo_id}")


def build_execution_steps(
    shard: dict[str, Any],
    *,
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    for raw_step in list(shard.get("commands") or []):
        if not isinstance(raw_step, dict):
            continue
        repo_id = str(raw_step.get("repo_id") or "").strip()
        if not repo_id:
            raise ValueError(f"Combined-system shard `{shard.get('shard_id')}` is missing repo_id")
        repo_root = _repo_root_for(repo_id, workspace_root=workspace_root)
        cwd_hint = str(raw_step.get("cwd") or "").strip()
        cwd = (repo_root / cwd_hint).resolve() if cwd_hint else repo_root.resolve()
        command = [str(part) for part in list(raw_step.get("command") or []) if str(part).strip()]
        if not command:
            raise ValueError(
                "Combined-system shard "
                f"`{shard.get('shard_id')}` has an empty command for {repo_id}"
            )
        steps.append(
            {
                "repo_id": repo_id,
                "cwd": str(cwd),
                "command": command,
            }
        )
    return steps


def _run_step(step: dict[str, Any]) -> dict[str, Any]:
    started_at = _utc_now_iso()
    completed = subprocess.run(step["command"], cwd=step["cwd"], check=False)
    completed_at = _utc_now_iso()
    return {
        **step,
        "status": "passed" if completed.returncode == 0 else "failed",
        "return_code": int(completed.returncode),
        "started_at": started_at,
        "completed_at": completed_at,
    }


def _execute_shard(
    shard: dict[str, Any],
    *,
    workspace_root: Path,
) -> dict[str, Any]:
    started_at = _utc_now_iso()
    step_results: list[dict[str, Any]] = []
    status = "passed"
    for step in build_execution_steps(shard, workspace_root=workspace_root):
        step_result = _run_step(step)
        step_results.append(step_result)
        if step_result["status"] != "passed":
            status = "failed"
            break
    return {
        "shard_id": str(shard.get("shard_id") or ""),
        "lane_family": str(shard.get("lane_family") or "combined_system"),
        "purpose": str(shard.get("purpose") or ""),
        "blocking": bool(shard.get("blocking", True)),
        "depends_on": list(shard.get("depends_on") or []),
        "expected_artifacts": list(shard.get("expected_artifacts") or []),
        "status": status,
        "started_at": started_at,
        "completed_at": _utc_now_iso(),
        "steps": step_results,
    }


def build_run_plan(
    manifest: dict[str, Any],
    *,
    shard_ids: list[str] | None = None,
    include_dependencies: bool = False,
) -> list[list[dict[str, Any]]]:
    shards = validate_manifest(manifest)
    selected = _select_shards(
        shards,
        shard_ids=shard_ids,
        include_dependencies=include_dependencies,
    )
    return resolve_batches(selected)


def run_shard(
    shard: dict[str, Any],
    *,
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
    dry_run: bool = False,
) -> int:
    for step in build_execution_steps(shard, workspace_root=workspace_root):
        if dry_run:
            continue
        subprocess.run(step["command"], cwd=step["cwd"], check=True)
    return 0


def run_manifest(
    manifest: dict[str, Any],
    *,
    workspace_root: Path = DEFAULT_WORKSPACE_ROOT,
    shard_ids: list[str] | None = None,
    include_dependencies: bool = False,
    max_parallel: int = 2,
) -> dict[str, Any]:
    batches = build_run_plan(
        manifest,
        shard_ids=shard_ids,
        include_dependencies=include_dependencies,
    )
    selected_ids = {
        str(shard.get("shard_id") or "")
        for batch in batches
        for shard in batch
    }
    summary: dict[str, Any] = {
        "generated_at": _utc_now_iso(),
        "mode_id": str(manifest.get("mode_id") or "combined_system"),
        "mode_label": str(manifest.get("mode_label") or "CGS + Zetherion together"),
        "workspace_root": str(workspace_root),
        "all_passed": True,
        "batches": [],
        "shards": [],
    }
    completed_ids: set[str] = set()
    failed = False

    for batch in batches:
        batch_ids = [str(shard.get("shard_id") or "") for shard in batch]
        batch_result: dict[str, Any] = {
            "batch_index": len(summary["batches"]),
            "shard_ids": batch_ids,
            "status": "passed",
            "shards": [],
        }
        if failed:
            skipped = []
            for shard in batch:
                skipped_result = {
                    "shard_id": str(shard.get("shard_id") or ""),
                    "lane_family": str(shard.get("lane_family") or "combined_system"),
                    "purpose": str(shard.get("purpose") or ""),
                    "blocking": bool(shard.get("blocking", True)),
                    "depends_on": list(shard.get("depends_on") or []),
                    "expected_artifacts": list(shard.get("expected_artifacts") or []),
                    "status": "skipped",
                    "skip_reason": "dependency batch failed",
                    "steps": [],
                }
                skipped.append(skipped_result)
                summary["shards"].append(skipped_result)
            batch_result["status"] = "skipped"
            batch_result["shards"] = skipped
            summary["batches"].append(batch_result)
            continue

        with ThreadPoolExecutor(max_workers=max(1, min(max_parallel, len(batch)))) as executor:
            future_map = {
                executor.submit(_execute_shard, shard, workspace_root=workspace_root): shard
                for shard in batch
            }
            results_by_id: dict[str, dict[str, Any]] = {}
            for future in as_completed(future_map):
                shard = future_map[future]
                shard_id = str(shard.get("shard_id") or "")
                result = future.result()
                results_by_id[shard_id] = result

        ordered_results = [results_by_id[shard_id] for shard_id in batch_ids]
        batch_result["shards"] = ordered_results
        if any(result["status"] != "passed" for result in ordered_results):
            batch_result["status"] = "failed"
            failed = True
            summary["all_passed"] = False
        summary["batches"].append(batch_result)
        summary["shards"].extend(ordered_results)
        completed_ids.update(batch_ids)

    missing_ids = sorted(selected_ids - completed_ids)
    for shard_id in missing_ids:
        summary["shards"].append(
            {
                "shard_id": shard_id,
                "status": "skipped",
                "skip_reason": "not scheduled",
                "steps": [],
            }
        )
        summary["all_passed"] = False
    return summary


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", default=str(DEFAULT_MANIFEST_PATH))
    parser.add_argument("--workspace-root", default=str(DEFAULT_WORKSPACE_ROOT))
    parser.add_argument("--shard-id", action="append", default=[])
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--include-dependencies", action="store_true")
    parser.add_argument("--max-parallel", type=int, default=2)
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    manifest = load_manifest(Path(args.manifest))
    shards = validate_manifest(manifest)
    if args.list:
        payload = [
            {
                "shard_id": str(shard.get("shard_id") or ""),
                "lane_family": str(shard.get("lane_family") or "combined_system"),
                "purpose": str(shard.get("purpose") or ""),
                "blocking": bool(shard.get("blocking", True)),
                "depends_on": list(shard.get("depends_on") or []),
                "expected_artifacts": list(shard.get("expected_artifacts") or []),
            }
            for shard in shards
        ]
        print(json.dumps(payload, indent=2))
        return 0

    shard_ids = [str(item).strip() for item in list(args.shard_id or []) if str(item).strip()]
    if not args.all and not shard_ids:
        raise SystemExit("--all or --shard-id is required unless --list is provided")
    if args.dry_run:
        batches = build_run_plan(
            manifest,
            shard_ids=shard_ids or None,
            include_dependencies=args.include_dependencies,
        )
        payload = {
            "mode_id": str(manifest.get("mode_id") or "combined_system"),
            "batches": [
                {
                    "batch_index": index,
                    "shards": [
                        {
                            "shard_id": str(shard.get("shard_id") or ""),
                            "steps": build_execution_steps(
                                shard,
                                workspace_root=Path(args.workspace_root),
                            ),
                        }
                        for shard in batch
                    ],
                }
                for index, batch in enumerate(batches)
            ],
        }
        print(json.dumps(payload, indent=2))
        return 0
    summary = run_manifest(
        manifest,
        workspace_root=Path(args.workspace_root),
        shard_ids=None if args.all else shard_ids,
        include_dependencies=args.include_dependencies,
        max_parallel=max(1, int(args.max_parallel or 1)),
    )
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    if not summary["all_passed"]:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
