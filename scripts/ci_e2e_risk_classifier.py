#!/usr/bin/env python3
"""Classify whether CI must run required E2E suites for a given change set."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from fnmatch import fnmatch
from pathlib import Path

LOW_RISK_GLOBS = (
    "docs/**",
    "README.md",
    "mkdocs.yml",
    "tests/unit/**",
    "scripts/ci_failure_attribution.py",
    "scripts/ci_e2e_risk_classifier.py",
    "scripts/ci-required-e2e-gate.sh",
    "scripts/ci_required_e2e_gate.py",
    "scripts/local-required-e2e-receipt.sh",
    ".ci/pipeline_contract.json",
    ".github/workflows/ci.yml",
    "AGENTS.md",
)

SUBSTANTIAL_GLOBS = (
    "src/**",
    "tests/integration/**",
    "docker-compose*.yml",
    "Dockerfile*",
    "scripts/windows/**",
    "scripts/pre-push-tests.sh",
    "scripts/test-full.sh",
    "pyproject.toml",
    "requirements*.txt",
)


@dataclass(frozen=True)
class Classification:
    e2e_required: bool
    reason_code: str
    reason: str


def _normalize_paths(paths: list[str]) -> list[str]:
    normalized: list[str] = []
    for path in paths:
        stripped = path.strip().replace("\\", "/")
        if stripped:
            normalized.append(stripped)
    return normalized


def _matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(fnmatch(path, pattern) for pattern in patterns)


def classify_changed_paths(*, event_name: str, changed_paths: list[str]) -> Classification:
    event = (event_name or "").strip().lower()
    normalized_paths = _normalize_paths(changed_paths)

    if event in {"schedule", "workflow_dispatch"}:
        return Classification(
            e2e_required=True,
            reason_code="scheduled_or_manual_run",
            reason="Scheduled/manual CI runs always require full E2E coverage.",
        )

    if event not in {"pull_request", "push"}:
        return Classification(
            e2e_required=True,
            reason_code="ambiguous_event_type",
            reason="Unknown event type; fail-safe requires full E2E.",
        )

    if not normalized_paths:
        return Classification(
            e2e_required=True,
            reason_code="ambiguous_no_changed_files",
            reason="No changed files were detected; fail-safe requires full E2E.",
        )

    if any(_matches_any(path, SUBSTANTIAL_GLOBS) for path in normalized_paths):
        return Classification(
            e2e_required=True,
            reason_code="substantial_path_match",
            reason="Substantial runtime/integration/deploy paths changed.",
        )

    if all(_matches_any(path, LOW_RISK_GLOBS) for path in normalized_paths):
        return Classification(
            e2e_required=False,
            reason_code="low_risk_paths_only",
            reason="Only low-risk docs/tests/CI contract paths changed.",
        )

    return Classification(
        e2e_required=True,
        reason_code="ambiguous_unclassified_path",
        reason="Changed paths were not fully classified as low risk.",
    )


def _run_git_command(args: list[str]) -> str:
    completed = subprocess.run(
        ["git", *args],
        check=True,
        capture_output=True,
        text=True,
    )
    return completed.stdout


def collect_changed_paths(
    *,
    event_name: str,
    base_ref: str,
    before_sha: str,
    head_sha: str,
) -> list[str]:
    event = (event_name or "").strip().lower()
    head = (head_sha or "").strip()

    if event == "pull_request":
        base = (base_ref or "").strip()
        if not base or not head:
            return []
        try:
            _run_git_command(["fetch", "origin", base, "--depth=1"])
            out = _run_git_command(
                [
                    "diff",
                    "--name-only",
                    "--diff-filter=ACMR",
                    f"origin/{base}...{head}",
                ]
            )
        except subprocess.CalledProcessError:
            return []
        return _normalize_paths(out.splitlines())

    if event == "push":
        before = (before_sha or "").strip()
        if not before or set(before) == {"0"} or not head:
            return []
        try:
            out = _run_git_command(
                [
                    "diff",
                    "--name-only",
                    "--diff-filter=ACMR",
                    f"{before}...{head}",
                ]
            )
        except subprocess.CalledProcessError:
            return []
        return _normalize_paths(out.splitlines())

    return []


def _append_github_output(path: str, *, classification: Classification, changed_count: int) -> None:
    output_path = Path(path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("a", encoding="utf-8") as fh:
        fh.write(f"e2e_required={'true' if classification.e2e_required else 'false'}\n")
        fh.write(f"decision_reason_code={classification.reason_code}\n")
        fh.write(f"decision_reason={classification.reason}\n")
        fh.write(f"changed_files_count={changed_count}\n")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event-name", required=True)
    parser.add_argument("--base-ref", default="")
    parser.add_argument("--before-sha", default="")
    parser.add_argument("--head-sha", default="")
    parser.add_argument("--output", required=True)
    parser.add_argument("--github-output", default="")
    args = parser.parse_args()

    changed_paths = collect_changed_paths(
        event_name=args.event_name,
        base_ref=args.base_ref,
        before_sha=args.before_sha,
        head_sha=args.head_sha,
    )
    classification = classify_changed_paths(
        event_name=args.event_name,
        changed_paths=changed_paths,
    )

    payload = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "event_name": args.event_name,
        "base_ref": args.base_ref,
        "before_sha": args.before_sha,
        "head_sha": args.head_sha,
        "changed_files": changed_paths,
        "classification": {
            "e2e_required": classification.e2e_required,
            "reason_code": classification.reason_code,
            "reason": classification.reason,
        },
    }
    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    if args.github_output:
        _append_github_output(
            args.github_output,
            classification=classification,
            changed_count=len(changed_paths),
        )

    print(json.dumps(payload, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
