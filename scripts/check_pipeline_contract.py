#!/usr/bin/env python3
"""Validate that CI pipeline jobs and workflow triggers match the contract."""

from __future__ import annotations

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_PATH = REPO_ROOT / ".ci/pipeline_contract.json"
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


def validate_pipeline_contract(contract_path: Path) -> list[str]:
    errors: list[str] = []
    if not contract_path.exists():
        return [".ci/pipeline_contract.json is missing"]

    data = json.loads(contract_path.read_text(encoding="utf-8"))
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

    return errors


def main() -> int:
    errors = [
        *validate_pipeline_contract(CONTRACT_PATH),
        *validate_workflow_contracts(REPO_ROOT),
    ]

    if errors:
        print("ERROR: Pipeline contract validation failed:")
        for error in errors:
            print(f"  - {error}")
        return 1

    print("Pipeline contract is complete.")
    print("Workflow trigger contract is aligned.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
