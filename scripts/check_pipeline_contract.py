#!/usr/bin/env python3
"""Validate that CI pipeline jobs, workflow triggers, and policy docs match the contract."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / ".ci/pipeline_contract.json"
WORKSTREAM_MANIFEST_PATH = REPO_ROOT / ".ci/ci_hardening_workstream_manifest.json"
REQUIRED_JOBS = {
    "detect-changes",
    "risk-classifier",
    "lint",
    "type-check",
    "security",
    "semgrep",
    "secret-scan",
    "dependency-audit",
    "license-check",
    "pre-commit",
    "docs-contract",
    "pipeline-contract",
    "zetherion-boundary-check",
    "unit-test",
    "required-e2e-gate",
    "integration-test",
    "docker-build-test",
}
DOCS_DEPLOY_REQUIRED_PATHS = {
    "docs/**",
    "README.md",
    "mkdocs.yml",
    "docs/requirements.txt",
    ".github/workflows/docs.yml",
}
CI_MAINTENANCE_REQUIRED_TOKENS = (
    "name: CI Maintenance",
    "scripts/ci_cost_report.py summary",
    "scripts/ci_cache_hygiene.py",
    "actions: write",
)


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def validate_pipeline_contract(contract_path: Path) -> list[str]:
    errors: list[str] = []
    if not contract_path.exists():
        return [".ci/pipeline_contract.json is missing"]

    data = _load_json(contract_path)
    jobs = data.get("jobs", {})
    missing = sorted(job for job in REQUIRED_JOBS if job not in jobs)
    if missing:
        errors.append("Pipeline contract missing jobs: " + ", ".join(missing))
    return errors


def validate_workflow_contracts(repo_root: Path) -> list[str]:
    errors: list[str] = []

    ci_text = (repo_root / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    if '- cron: "30 2 * * 0"' not in ci_text:
        errors.append("CI workflow must keep the weekly scheduled heavy verification cron.")
    if "group: ci-${{ github.workflow }}-${{ github.ref }}" not in ci_text:
        errors.append("CI workflow must keep concurrency grouping for superseded PR cancellation.")
    if "cancel-in-progress: true" not in ci_text:
        errors.append("CI workflow must cancel superseded in-progress runs.")
    if "name: CI Cost Report" not in ci_text:
        errors.append("CI workflow must publish the CI Cost Report job.")
    if "scripts/ci_cost_report.py run" not in ci_text:
        errors.append("CI workflow must generate a per-run CI cost report artifact.")

    codeql_text = (repo_root / ".github/workflows/codeql.yml").read_text(encoding="utf-8")
    if re.search(r"(?m)^  push:\s*$", codeql_text):
        errors.append("CodeQL workflow must not trigger on push.")
    if re.search(r"(?m)^  pull_request:\s*$", codeql_text):
        errors.append("CodeQL workflow must not trigger on pull_request.")
    if not re.search(r"(?m)^  schedule:\s*$", codeql_text):
        errors.append("CodeQL workflow must keep a weekly schedule trigger.")
    if not re.search(r"(?m)^  workflow_dispatch:\s*$", codeql_text):
        errors.append("CodeQL workflow must keep manual dispatch support.")

    docs_text = (repo_root / ".github/workflows/docs.yml").read_text(encoding="utf-8")
    if not re.search(r"(?m)^  push:\s*$", docs_text):
        errors.append("Docs deploy workflow must trigger on push.")
    if not re.search(r"(?m)^  workflow_dispatch:\s*$", docs_text):
        errors.append("Docs deploy workflow must keep manual dispatch support.")
    if not re.search(r"(?m)^    paths:\s*$", docs_text):
        errors.append("Docs deploy workflow must scope push triggers to docs-related paths.")
    for path_glob in sorted(DOCS_DEPLOY_REQUIRED_PATHS):
        single = f"      - '{path_glob}'"
        double = f'      - "{path_glob}"'
        if single not in docs_text and double not in docs_text:
            errors.append(f"Docs deploy workflow must include path filter {path_glob!r}.")
    if "Validate docs contracts" in docs_text:
        errors.append(
            "Docs deploy workflow must not duplicate docs-contract validation "
            "already enforced elsewhere."
        )
    if "pip install -r docs/requirements.txt" not in docs_text:
        errors.append(
            "Docs deploy workflow must install documentation dependencies "
            "from docs/requirements.txt."
        )
    for command in ("mkdocs build --strict", "mkdocs gh-deploy --force"):
        if command not in docs_text:
            errors.append(f"Docs deploy workflow must run `{command}`.")

    maintenance_path = repo_root / ".github/workflows/ci-maintenance.yml"
    if not maintenance_path.exists():
        errors.append("CI maintenance workflow must exist.")
        return errors

    maintenance_text = maintenance_path.read_text(encoding="utf-8")
    if not re.search(r"(?m)^  schedule:\s*$", maintenance_text):
        errors.append("CI maintenance workflow must keep a weekly schedule trigger.")
    if not re.search(r"(?m)^  workflow_dispatch:\s*$", maintenance_text):
        errors.append("CI maintenance workflow must keep manual dispatch support.")
    for token in CI_MAINTENANCE_REQUIRED_TOKENS:
        if token not in maintenance_text:
            errors.append(f"CI maintenance workflow missing required token: {token}")

    return errors


def validate_policy_docs(repo_root: Path, manifest_path: Path) -> list[str]:
    errors: list[str] = []
    if not manifest_path.exists():
        return [".ci/ci_hardening_workstream_manifest.json is missing"]

    manifest = _load_json(manifest_path)
    current_contract = manifest.get("current_contract", {})
    policy = manifest.get("policy_enforcement", {})
    contract_docs = policy.get("contract_docs", [])
    doc_specific_tokens = policy.get("doc_specific_tokens", {})
    pr_template_path = policy.get("pr_template_path")
    required_pr_template_tokens = policy.get("required_pr_template_tokens", [])

    if not contract_docs:
        errors.append("CI hardening manifest must list contract_docs for policy enforcement.")
        return errors

    required_checks = current_contract.get("github_required_check_inventory", [])
    pr_fast_path_jobs = current_contract.get("pr_fast_path_jobs", [])
    heavy_gate = current_contract.get("local_heavy_gate_entrypoint")
    receipt_entrypoint = current_contract.get("local_required_e2e_receipt_entrypoint")

    common_tokens = [heavy_gate, receipt_entrypoint, *required_checks, *pr_fast_path_jobs]
    common_tokens = [token for token in common_tokens if token]

    for rel_path in contract_docs:
        doc_path = repo_root / rel_path
        if not doc_path.exists():
            errors.append(f"Policy contract doc missing: {rel_path}")
            continue
        text = doc_path.read_text(encoding="utf-8")
        for token in common_tokens:
            if token not in text:
                errors.append(f"{rel_path} missing required contract token: {token}")
        for token in doc_specific_tokens.get(rel_path, []):
            if token not in text:
                errors.append(f"{rel_path} missing required policy token: {token}")

    if not pr_template_path:
        errors.append("CI hardening manifest must declare pr_template_path.")
        return errors

    template_path = repo_root / pr_template_path
    if not template_path.exists():
        errors.append(f"Pull request template missing: {pr_template_path}")
        return errors

    template_text = template_path.read_text(encoding="utf-8")
    for token in required_pr_template_tokens:
        if token not in template_text:
            errors.append(f"{pr_template_path} missing required template token: {token}")

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
    print("Workflow trigger contract is aligned.")
    print("Policy docs are aligned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
