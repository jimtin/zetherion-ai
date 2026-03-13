#!/usr/bin/env python3
"""Validate the local-first owner-CI contract and manual GitHub helper posture."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / ".ci/pipeline_contract.json"
WORKSTREAM_MANIFEST_PATH = REPO_ROOT / ".ci/ci_hardening_workstream_manifest.json"
REQUIRED_STATUS_CONTEXTS = {
    "zetherion/merge-readiness",
    "zetherion/deploy-readiness",
}
MANUAL_ONLY_WORKFLOWS = {
    ".github/workflows/ci.yml": "Owner CI Bridge",
    ".github/workflows/codeql.yml": "CodeQL",
    ".github/workflows/ci-maintenance.yml": "CI Maintenance",
    ".github/workflows/docs.yml": "Deploy Documentation",
    ".github/workflows/docs-gap-triage.yml": "Weekly Docs Gap Triage",
    ".github/workflows/sync-wiki.yml": "Sync Documentation to Wiki",
    ".github/workflows/auto-merge-main.yml": "Auto Merge Main (Deprecated)",
    ".github/workflows/revert-failed-main-deploy.yml": "Revert Failed Main Deploy (Deprecated)",
}


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_pipeline_contract(contract_path: Path) -> list[str]:
    errors: list[str] = []
    if not contract_path.exists():
        return [".ci/pipeline_contract.json is missing"]

    data = _load_json(contract_path)
    control_plane = dict(data.get("control_plane") or {})

    if control_plane.get("ci_authority") != "zetherion":
        errors.append("Pipeline contract must declare Zetherion as the CI authority.")
    if control_plane.get("github_actions_role") != "manual_helpers_only":
        errors.append("Pipeline contract must declare GitHub Actions as manual helpers only.")
    if control_plane.get("local_heavy_gate_entrypoint") != "./scripts/test-full.sh":
        errors.append("Pipeline contract must point local_heavy_gate_entrypoint to ./scripts/test-full.sh.")

    statuses = {
        str(item).strip()
        for item in list(control_plane.get("required_status_contexts") or [])
        if str(item).strip()
    }
    missing_statuses = sorted(REQUIRED_STATUS_CONTEXTS - statuses)
    if missing_statuses:
        errors.append(
            "Pipeline contract missing required status contexts: " + ", ".join(missing_statuses)
        )

    return errors


def _has_only_manual_trigger(text: str) -> bool:
    return bool(re.search(r"(?m)^on:\s*\n\s+workflow_dispatch:\s*$", text))


def _contains_automatic_trigger(text: str) -> bool:
    return any(
        re.search(pattern, text)
        for pattern in (
            r"(?m)^\s+push:\s*$",
            r"(?m)^\s+pull_request:\s*$",
            r"(?m)^\s+schedule:\s*$",
            r"(?m)^\s+workflow_run:\s*$",
        )
    )


def validate_workflow_contracts(repo_root: Path) -> list[str]:
    errors: list[str] = []

    for rel_path, expected_name in MANUAL_ONLY_WORKFLOWS.items():
        path = repo_root / rel_path
        if not path.exists():
            errors.append(f"Required manual helper workflow missing: {rel_path}")
            continue
        text = path.read_text(encoding="utf-8")
        if not re.search(rf'(?m)^name:\s+"?{re.escape(expected_name)}"?\s*$', text):
            errors.append(f"{rel_path} must keep workflow name {expected_name!r}.")
        if not _has_only_manual_trigger(text):
            errors.append(f"{rel_path} must be workflow_dispatch only.")
        if _contains_automatic_trigger(text):
            errors.append(f"{rel_path} must not use automatic push/pull_request/schedule/workflow_run triggers.")

    deploy_text = (repo_root / ".github/workflows/deploy-windows.yml").read_text(encoding="utf-8")
    if not re.search(r"(?m)^  workflow_dispatch:\s*$", deploy_text):
        errors.append("deploy-windows.yml must keep workflow_dispatch support.")
    if re.search(r"(?m)^  workflow_run:\s*$", deploy_text):
        errors.append("deploy-windows.yml must not trigger from workflow_run.")

    release_text = (repo_root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    if not re.search(r"(?m)^  workflow_dispatch:\s*$", release_text):
        errors.append("release.yml must keep workflow_dispatch support.")
    if "uses: ./.github/workflows/ci.yml" in release_text:
        errors.append("release.yml must not reuse ci.yml as a release gate.")

    return errors


def validate_policy_docs(repo_root: Path, manifest_path: Path) -> list[str]:
    errors: list[str] = []
    if not manifest_path.exists():
        return [".ci/ci_hardening_workstream_manifest.json is missing"]

    manifest = _load_json(manifest_path)
    current_contract = dict(manifest.get("current_contract") or {})
    policy = dict(manifest.get("policy_enforcement") or {})
    contract_docs = list(policy.get("contract_docs") or [])
    doc_specific_tokens = dict(policy.get("doc_specific_tokens") or {})

    common_tokens = [
        str(current_contract.get("local_heavy_gate_entrypoint") or "").strip(),
        *[
            str(token).strip()
            for token in list(current_contract.get("required_status_contexts") or [])
            if str(token).strip()
        ],
    ]
    common_tokens = [token for token in common_tokens if token]

    for rel_path in contract_docs:
        path = repo_root / rel_path
        if not path.exists():
            errors.append(f"Policy contract doc missing: {rel_path}")
            continue
        text = path.read_text(encoding="utf-8")
        for token in common_tokens:
            if token not in text:
                errors.append(f"{rel_path} missing required contract token: {token}")
        for token in list(doc_specific_tokens.get(rel_path) or []):
            if token not in text:
                errors.append(f"{rel_path} missing required policy token: {token}")

    return errors


def main() -> int:
    errors = [
        *validate_pipeline_contract(CONTRACT_PATH),
        *validate_workflow_contracts(REPO_ROOT),
        *validate_policy_docs(REPO_ROOT, WORKSTREAM_MANIFEST_PATH),
    ]

    if errors:
        print("ERROR: Pipeline contract validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("Pipeline contract is complete.")
    print("Workflow helper contract is aligned.")
    print("Policy docs are aligned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
