#!/usr/bin/env python3
"""Classify CI failures by whether local canonical gates should have caught them."""

from __future__ import annotations

import argparse
import json
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_CONTRACT = Path(".ci/pipeline_contract.json")


@dataclass(frozen=True)
class JobFailure:
    job: str
    result: str
    reason_code: str
    explanation: str


def parse_job_results(raw: str) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for item in raw.split(","):
        item = item.strip()
        if not item or "=" not in item:
            continue
        name, value = item.split("=", 1)
        mapping[name.strip()] = value.strip()
    return mapping


def classify_failure(job: str, result: str, contract: dict[str, Any]) -> JobFailure:
    if job == "required-e2e-gate":
        return JobFailure(
            job=job,
            result=result,
            reason_code="AGENTS_POLICY_BREACH_REQUIRED_E2E",
            explanation=(
                "The required E2E gate failed. For substantial PRs this indicates the "
                "server-side AGENTS policy contract was not satisfied "
                "(required suites failed, skipped, or required credentials were missing)."
            ),
        )

    job_contract = contract.get("jobs", {}).get(job)
    if not job_contract:
        return JobFailure(
            job=job,
            result=result,
            reason_code="PIPELINE_CONTRACT_GAP",
            explanation=(
                "This job is not mapped in .ci/pipeline_contract.json, so we cannot "
                "prove whether local gates should have caught it."
            ),
        )

    local_equivalent = bool(job_contract.get("local_equivalent", False))
    note = str(job_contract.get("note", "")).strip()

    if local_equivalent:
        reason_code = "SHOULD_HAVE_BEEN_CAUGHT_LOCALLY"
        explanation = (
            "This CI job has a local equivalent in the canonical test-full pipeline. "
            "If CI failed here, the local gate was likely bypassed or not run on the same commit."
        )
    else:
        reason_code = "CI_ONLY_ENVIRONMENT_DIFF"
        explanation = (
            "This CI job is intentionally CI-only/deferred for cost control, so local full runs "
            "do not cover this failure path."
        )

    if note:
        explanation = f"{explanation} {note}"

    return JobFailure(job=job, result=result, reason_code=reason_code, explanation=explanation)


def write_step_summary(text: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as fh:
        fh.write(text)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--job-results", required=True, help="CSV like 'job=success,job2=failure'")
    parser.add_argument(
        "--contract",
        default=str(DEFAULT_CONTRACT),
        help="Pipeline contract JSON file",
    )
    parser.add_argument("--output", required=True, help="Path to write JSON attribution report")
    args = parser.parse_args()

    results = parse_job_results(args.job_results)
    if not results:
        raise SystemExit("No job results were provided.")

    contract_path = Path(args.contract)
    with contract_path.open("r", encoding="utf-8") as fh:
        contract = json.load(fh)

    failures: list[JobFailure] = []
    for job, result in results.items():
        normalized = result.lower()
        if normalized in {"success", "skipped"}:
            continue
        failures.append(classify_failure(job, normalized, contract))

    payload = {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "failed_job_count": len(failures),
        "failures": [
            {
                "job": item.job,
                "result": item.result,
                "reason_code": item.reason_code,
                "explanation": item.explanation,
            }
            for item in failures
        ],
    }

    output_path = Path(args.output)
    output_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    lines = ["### CI Failure Attribution", ""]
    if not failures:
        lines.append("No failed CI jobs detected.")
    else:
        lines.append("| Job | Reason | Explanation |")
        lines.append("|-----|--------|-------------|")
        for failure in failures:
            explanation = failure.explanation.replace("|", "\\|")
            lines.append(f"| `{failure.job}` | `{failure.reason_code}` | {explanation} |")
    lines.append("")

    summary = "\n".join(lines)
    print(summary)
    write_step_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
