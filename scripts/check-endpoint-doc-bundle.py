#!/usr/bin/env python3
"""Ensure API route changes include the required endpoint documentation bundle."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

ROUTE_CHANGE_PREFIXES = (
    "src/zetherion_ai/api/server.py",
    "src/zetherion_ai/api/routes/",
    "src/zetherion_ai/cgs_gateway/routes/",
)

REQUIRED_DOC_BUNDLE = {
    "docs/technical/public-api-reference.md",
    "docs/technical/cgs-public-api-endpoint-build-spec.md",
    "docs/technical/cgs-zetherion-service-draft.md",
    "docs/technical/openapi-public-api.yaml",
    "docs/technical/openapi-cgs-gateway.yaml",
    "docs/technical/api-error-matrix.md",
    "docs/technical/api-auth-matrix.md",
    "docs/technical/frontend-route-wiring.md",
    "docs/technical/zetherion-document-intelligence-component.md",
    "docs/technical/cgs-client-onboarding-kit.md",
    "docs/technical/cgs-email-monitoring-onboarding-kit.md",
    "docs/development/changelog.md",
}


def _run_git(*args: str) -> str:
    proc = subprocess.run(
        ["git", *args],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"git {' '.join(args)} failed")
    return proc.stdout.strip()


def _rev_exists(ref: str) -> bool:
    proc = subprocess.run(
        ["git", "rev-parse", "--verify", "--quiet", ref],
        cwd=REPO_ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    return proc.returncode == 0


def _resolve_base_ref() -> str | None:
    explicit = os.environ.get("DOCS_BUNDLE_BASE_SHA", "").strip()
    if explicit and _rev_exists(explicit):
        return explicit

    event_before = os.environ.get("GITHUB_EVENT_BEFORE", "").strip()
    if (
        event_before
        and event_before != "0000000000000000000000000000000000000000"
        and _rev_exists(event_before)
    ):
        return event_before

    base_ref = os.environ.get("GITHUB_BASE_REF", "").strip()
    if base_ref:
        candidate = f"origin/{base_ref}"
        if _rev_exists(candidate):
            return candidate

    if _rev_exists("HEAD~1"):
        return "HEAD~1"
    return None


def _changed_files(base_ref: str | None) -> set[str]:
    if base_ref is None:
        return set()

    for diff_range in (f"{base_ref}...HEAD", f"{base_ref}..HEAD"):
        proc = subprocess.run(
            ["git", "diff", "--name-only", diff_range],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return {line.strip() for line in proc.stdout.splitlines() if line.strip()}

    raise RuntimeError(f"Unable to diff against base ref: {base_ref}")


def _is_route_change(path: str) -> bool:
    return path.startswith(ROUTE_CHANGE_PREFIXES)


def main() -> int:
    try:
        base_ref = _resolve_base_ref()
        changed = _changed_files(base_ref)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    route_changes = sorted(path for path in changed if _is_route_change(path))
    if not route_changes:
        print("Endpoint docs bundle check passed (no API route changes detected).")
        return 0

    missing_on_disk = sorted(
        path for path in REQUIRED_DOC_BUNDLE if not (REPO_ROOT / path).exists()
    )
    if missing_on_disk:
        print("Endpoint docs bundle check failed. Missing required docs files on disk:")
        for path in missing_on_disk:
            print(f"  - {path}")
        return 1

    docs_touched = {path for path in changed if path in REQUIRED_DOC_BUNDLE}
    missing_updates = sorted(REQUIRED_DOC_BUNDLE - docs_touched)
    if missing_updates:
        print("Endpoint docs bundle check failed.")
        print("API route files changed:")
        for path in route_changes:
            print(f"  - {path}")
        print("Required docs not updated in this change:")
        for path in missing_updates:
            print(f"  - {path}")
        return 1

    print("Endpoint docs bundle check passed.")
    print(f"Base ref: {base_ref or 'none'}")
    print(f"Route files changed: {len(route_changes)}")
    print(f"Required docs updated: {len(docs_touched)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
