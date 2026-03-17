#!/usr/bin/env python3
"""Canonical four-metric coverage gate with structured artifacts."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
from pathlib import Path


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _display_path(path: Path, repo_root: Path) -> str:
    resolved_repo_root = repo_root.resolve()
    resolved_path = path.resolve()
    try:
        return str(resolved_path.relative_to(resolved_repo_root))
    except ValueError:
        pass

    workspace_root = os.environ.get("ZETHERION_WORKSPACE_ROOT", "").strip()
    host_workspace_root = os.environ.get("ZETHERION_HOST_WORKSPACE_ROOT", "").strip()
    if workspace_root and host_workspace_root:
        workspace_root_path = Path(workspace_root).expanduser()
        try:
            suffix = resolved_path.relative_to(workspace_root_path.resolve())
        except ValueError:
            suffix = None
        if suffix is not None:
            remapped = Path(host_workspace_root).expanduser().resolve() / suffix
            try:
                return str(remapped.relative_to(resolved_repo_root))
            except ValueError:
                return str(remapped)

    return str(resolved_path)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--artifacts-dir", required=True)
    parser.add_argument("--source-root", default="src/zetherion_ai")
    parser.add_argument("--coverage-file", default=".coverage")
    parser.add_argument("--repo-sha", default="")
    parser.add_argument("--run-id", default="")
    parser.add_argument("--lane-id", default="")
    parser.add_argument("--minimum-statements", type=float, default=90.0)
    parser.add_argument("--minimum-lines", type=float, default=90.0)
    parser.add_argument("--minimum-branches", type=float, default=90.0)
    parser.add_argument("--minimum-functions", type=float, default=90.0)
    return parser.parse_args()


def main() -> int:
    repo_root = _repo_root()
    if str(repo_root / "src") not in sys.path:
        sys.path.insert(0, str(repo_root / "src"))

    from zetherion_ai.owner_ci.coverage_artifacts import (  # noqa: PLC0415
        build_coverage_artifacts,
    )
    from zetherion_ai.owner_ci.diagnostics import (  # noqa: PLC0415
        build_coverage_diagnostics,
    )

    args = _parse_args()
    artifacts_dir = Path(args.artifacts_dir).expanduser().resolve()
    artifacts_dir.mkdir(parents=True, exist_ok=True)
    coverage_file = Path(args.coverage_file)
    if not coverage_file.is_absolute():
        coverage_file = repo_root / coverage_file
    if not coverage_file.is_file():
        print(f"Coverage data file not found: {coverage_file}", file=sys.stderr)
        return 2

    coverage_json_path = artifacts_dir / "coverage.json"
    html_dir = artifacts_dir / "html"
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "json",
            "-o",
            str(coverage_json_path),
        ],
        check=True,
        cwd=repo_root,
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "coverage",
            "html",
            "-d",
            str(html_dir),
        ],
        check=True,
        cwd=repo_root,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    coverage_payload = json.loads(coverage_json_path.read_text(encoding="utf-8"))
    thresholds = {
        "statements": args.minimum_statements,
        "lines": args.minimum_lines,
        "branches": args.minimum_branches,
        "functions": args.minimum_functions,
    }
    summary, gaps, exit_code = build_coverage_artifacts(
        coverage_payload=coverage_payload,
        repo_root=repo_root,
        source_root=(repo_root / args.source_root).resolve(),
        thresholds=thresholds,
        coverage_json_path=_display_path(coverage_json_path, repo_root),
        coverage_report_path=_display_path(artifacts_dir / "coverage-report.txt", repo_root),
        html_index_path=_display_path(html_dir / "index.html", repo_root),
        repo_sha=args.repo_sha or None,
        run_id=args.run_id or None,
        lane_id=args.lane_id or None,
    )
    summary_path = artifacts_dir / "coverage-summary.json"
    gaps_path = artifacts_dir / "coverage-gaps.json"
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    gaps_path.write_text(json.dumps(gaps, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    diagnostic_summary_path = artifacts_dir / "diagnostic-summary.json"
    diagnostic_findings_path = artifacts_dir / "diagnostic-findings.json"
    if summary.get("passed"):
        diagnostic_summary = {
            "generated_at": summary.get("generated_at"),
            "repo_id": "zetherion-ai",
            "run_id": args.run_id or None,
            "status": "passed",
            "finding_count": 0,
            "blocking": False,
            "confidence": 1.0,
            "recommended_next_actions": [],
            "artifact_paths": [
                _display_path(summary_path, repo_root),
                _display_path(gaps_path, repo_root),
            ],
        }
        diagnostic_findings: list[dict[str, object]] = []
    else:
        diagnostic_summary, diagnostic_findings = build_coverage_diagnostics(
            coverage_summary=summary,
            coverage_gaps=gaps,
            run_id=args.run_id or "",
            repo_id="zetherion-ai",
        )
    diagnostic_summary_path.write_text(
        json.dumps(diagnostic_summary, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    diagnostic_findings_path.write_text(
        json.dumps({"findings": diagnostic_findings}, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )

    metrics = dict(summary.get("metrics") or {})
    for name in ("statements", "lines", "branches", "functions"):
        metric = dict(metrics.get(name) or {})
        print(
            f"[coverage_gate] {name}: {metric.get('actual', 0):.2f}% "
            f"(threshold {metric.get('threshold', 0):.2f}%, "
            f"covered {metric.get('covered', 0)}/{metric.get('total', 0)})"
        )
    print(f"[coverage_gate] summary={_display_path(summary_path, repo_root)}")
    print(f"[coverage_gate] gaps={_display_path(gaps_path, repo_root)}")
    print(
        "[coverage_gate] diagnostic_summary="
        f"{_display_path(diagnostic_summary_path, repo_root)}"
    )
    print(
        "[coverage_gate] diagnostic_findings="
        f"{_display_path(diagnostic_findings_path, repo_root)}"
    )
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
