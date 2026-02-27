#!/usr/bin/env python3
"""Validate that CI pipeline jobs are mapped in the attribution contract."""

from __future__ import annotations

import json
from pathlib import Path

REQUIRED_JOBS = {
    "lint",
    "type-check",
    "security",
    "semgrep",
    "dependency-audit",
    "license-check",
    "pre-commit",
    "docs-contract",
    "pipeline-contract",
    "unit-test",
    "integration-test",
}


def main() -> int:
    contract_path = Path(".ci/pipeline_contract.json")
    if not contract_path.exists():
        print("ERROR: .ci/pipeline_contract.json is missing")
        return 1

    data = json.loads(contract_path.read_text(encoding="utf-8"))
    jobs = data.get("jobs", {})

    missing = sorted(job for job in REQUIRED_JOBS if job not in jobs)
    if missing:
        print("ERROR: Pipeline contract missing jobs:")
        for job in missing:
            print(f"  - {job}")
        return 1

    print("Pipeline contract is complete.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
