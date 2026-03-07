#!/usr/bin/env python3
"""Ensure API route changes include the required endpoint documentation bundle."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]

DOC_RULES: dict[str, dict[str, object]] = {
    "zetherion_public_api": {
        "prefixes": (
            "src/zetherion_ai/api/server.py",
            "src/zetherion_ai/api/routes/",
        ),
        "required_docs": {
            "docs/technical/public-api-reference.md",
            "docs/technical/openapi-public-api.yaml",
            "docs/technical/api-error-matrix.md",
            "docs/technical/api-auth-matrix.md",
            "docs/technical/zetherion-document-intelligence-component.md",
            ".agent-handoff/zetherion/ZETHERION_DOCUMENT_ARCHIVE_DELETE_SPEC.md",
            "docs/development/changelog.md",
        },
    },
    "cgs_gateway_routes": {
        "prefixes": (
            "src/zetherion_ai/cgs_gateway/routes/",
            "src/zetherion_ai/cgs_gateway/server.py",
        ),
        "required_docs": {
            "docs/technical/cgs-public-api-endpoint-build-spec.md",
            "docs/technical/cgs-zetherion-service-draft.md",
            "docs/technical/openapi-cgs-gateway.yaml",
            "docs/technical/frontend-route-wiring.md",
            "docs/technical/cgs-client-onboarding-kit.md",
            "docs/technical/cgs-email-monitoring-onboarding-kit.md",
            "docs/development/changelog.md",
        },
    },
}

CONDITIONAL_ROUTE_MARKERS: dict[str, tuple[str, ...]] = {
    "src/zetherion_ai/api/server.py": (
        "app.router.add_",
        '"/api/v1/',
        "'/api/v1/",
        "register_youtube_routes",
    ),
    "src/zetherion_ai/cgs_gateway/server.py": (
        "app.router.add_",
        '"/service/ai/',
        "'/service/ai/",
        "register_",
    ),
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


def _diff_ranges(base_ref: str | None) -> tuple[str, ...]:
    if base_ref is None:
        return ()
    return (f"{base_ref}...HEAD", f"{base_ref}..HEAD")


def _changed_files(base_ref: str | None) -> set[str]:
    if base_ref is None:
        return set()

    for diff_range in _diff_ranges(base_ref):
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


def _changed_content_lines(base_ref: str | None, path: str) -> tuple[str, ...]:
    if base_ref is None:
        return ()

    for diff_range in _diff_ranges(base_ref):
        proc = subprocess.run(
            ["git", "diff", "--unified=0", diff_range, "--", path],
            cwd=REPO_ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if proc.returncode == 0:
            return tuple(
                line
                for line in proc.stdout.splitlines()
                if line.startswith(("+", "-")) and not line.startswith(("+++", "---"))
            )

    raise RuntimeError(f"Unable to diff content for {path} against base ref: {base_ref}")


def _rule_matches_path(path: str, prefixes: tuple[str, ...]) -> bool:
    return path.startswith(prefixes)


def _path_requires_doc_bundle(path: str, *, base_ref: str | None) -> bool:
    markers = CONDITIONAL_ROUTE_MARKERS.get(path)
    if not markers:
        return True

    changed_lines = _changed_content_lines(base_ref, path)
    return any(marker in line for line in changed_lines for marker in markers)


def _matched_rule_changes(
    rule_name: str,
    rule: dict[str, object],
    changed: set[str],
    base_ref: str | None,
) -> list[str]:
    del rule_name  # reserved for future rule-specific heuristics
    prefixes = tuple(rule["prefixes"])  # type: ignore[arg-type]
    return [
        path
        for path in sorted(changed)
        if _rule_matches_path(path, prefixes) and _path_requires_doc_bundle(path, base_ref=base_ref)
    ]


def main() -> int:
    try:
        base_ref = _resolve_base_ref()
        changed = _changed_files(base_ref)
    except Exception as exc:
        print(f"ERROR: {exc}")
        return 1

    matched_rules: list[tuple[str, list[str], set[str]]] = []
    for rule_name, rule in DOC_RULES.items():
        required_docs = set(rule["required_docs"])  # type: ignore[arg-type]
        matched = _matched_rule_changes(rule_name, rule, changed, base_ref)
        if matched:
            matched_rules.append((rule_name, matched, required_docs))

    if not matched_rules:
        print("Endpoint docs bundle check passed (no API route changes detected).")
        return 0

    failed = False
    print(f"Base ref: {base_ref or 'none'}")
    for rule_name, route_changes, required_docs in matched_rules:
        missing_on_disk = sorted(path for path in required_docs if not (REPO_ROOT / path).exists())
        if missing_on_disk:
            failed = True
            print(f"Endpoint docs bundle check failed [{rule_name}].")
            print("Missing required docs files on disk:")
            for path in missing_on_disk:
                print(f"  - {path}")
            continue

        docs_touched = {path for path in changed if path in required_docs}
        missing_updates = sorted(required_docs - docs_touched)
        if missing_updates:
            failed = True
            print(f"Endpoint docs bundle check failed [{rule_name}].")
            print("API route files changed:")
            for path in route_changes:
                print(f"  - {path}")
            print("Required docs not updated in this change:")
            for path in missing_updates:
                print(f"  - {path}")
            continue

        print(
            "Endpoint docs bundle check passed "
            f"[{rule_name}] (routes={len(route_changes)}, docs={len(docs_touched)})."
        )

    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
