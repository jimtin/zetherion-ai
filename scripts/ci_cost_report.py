#!/usr/bin/env python3
"""Generate CI runtime cost observability reports for a run or recent history."""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import urlencode
from urllib.request import Request, urlopen

DEFAULT_CONTRACT = Path(".ci/pipeline_contract.json")
DEFAULT_API_URL = "https://api.github.com"
DEFAULT_DAYS = 7

JOB_NAME_TO_CONTRACT_ID = {
    "Detect Changes": "detect-changes",
    "Risk Classifier": "risk-classifier",
    "Linting & Formatting": "lint",
    "Type Checking": "type-check",
    "Security Scanning": "security",
    "SAST (Semgrep)": "semgrep",
    "Secret Scan (Gitleaks)": "secret-scan",
    "Dependency Vulnerability Audit": "dependency-audit",
    "License Compliance": "license-check",
    "Pre-commit Checks": "pre-commit",
    "Documentation Contracts": "docs-contract",
    "Pipeline Contract": "pipeline-contract",
    "Zetherion Boundary Check": "zetherion-boundary-check",
    "Docker Build Test": "docker-build-test",
    "Required E2E Gate": "required-e2e-gate",
    "CI Summary": "ci-summary",
    "CI Failure Attribution": "ci-failure-attribution",
    "CI Cost Report": "ci-cost-report",
}

INFO_ONLY_JOB_IDS = {"ci-summary", "ci-failure-attribution", "ci-cost-report"}


@dataclass(frozen=True)
class JobRecord:
    name: str
    contract_id: str | None
    execution_class: str
    status: str
    conclusion: str
    started_at: str
    completed_at: str
    duration_seconds: int
    html_url: str


@dataclass(frozen=True)
class WorkflowRunRecord:
    run_id: int
    name: str
    event: str
    status: str
    conclusion: str
    run_started_at: str
    updated_at: str
    head_branch: str
    duration_seconds: int
    html_url: str


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _parse_ts(raw: str | None) -> datetime | None:
    if not raw:
        return None
    value = raw.strip()
    if not value:
        return None
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(value).astimezone(UTC)
    except ValueError:
        return None


def _duration_seconds(started_at: str | None, completed_at: str | None) -> int:
    start = _parse_ts(started_at)
    end = _parse_ts(completed_at)
    if start is None or end is None or end < start:
        return 0
    return int((end - start).total_seconds())


def _round_minutes(seconds: int) -> float:
    return round(seconds / 60.0, 2)


def _job_contract_id(name: str) -> str | None:
    if name in JOB_NAME_TO_CONTRACT_ID:
        return JOB_NAME_TO_CONTRACT_ID[name]
    if name.startswith("Tests (Python "):
        return "unit-test"
    if name.startswith("Integration ("):
        return "integration-test"
    return None


def _execution_class(name: str, contract_id: str | None, contract: dict[str, Any]) -> str:
    if contract_id in INFO_ONLY_JOB_IDS:
        return "github_orchestration"
    if contract_id == "required-e2e-gate":
        return "local_receipt_validation"
    job_contract = contract.get("jobs", {}).get(contract_id or "")
    if not job_contract:
        return "unmapped"
    if not bool(job_contract.get("local_equivalent", False)):
        return "github_policy_or_heavy_only"
    return "github_executed_local_equivalent"


def _append_summary(text: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with Path(summary_path).open("a", encoding="utf-8") as handle:
        handle.write(text)


def _api_request_json(
    *, api_url: str, token: str, path: str, query: dict[str, Any] | None = None
) -> dict[str, Any]:
    url = f"{api_url.rstrip('/')}{path}"
    if query:
        url = f"{url}?{urlencode(query)}"
    request = Request(
        url,
        headers={
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {token}",
            "User-Agent": "Zetherion-CI-Cost-Report/1.0",
        },
    )
    with urlopen(request) as response:  # noqa: S310
        return json.load(response)


def fetch_workflows(
    *, repo: str, token: str, api_url: str = DEFAULT_API_URL
) -> list[dict[str, Any]]:
    workflows: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _api_request_json(
            api_url=api_url,
            token=token,
            path=f"/repos/{repo}/actions/workflows",
            query={"per_page": 100, "page": page},
        )
        items = payload.get("workflows", [])
        if not isinstance(items, list) or not items:
            break
        workflows.extend(item for item in items if isinstance(item, dict))
        if len(items) < 100:
            break
        page += 1
    return workflows


def fetch_run_jobs(
    *, repo: str, run_id: int, token: str, api_url: str = DEFAULT_API_URL
) -> list[dict[str, Any]]:
    jobs: list[dict[str, Any]] = []
    page = 1
    while True:
        payload = _api_request_json(
            api_url=api_url,
            token=token,
            path=f"/repos/{repo}/actions/runs/{run_id}/jobs",
            query={"per_page": 100, "page": page},
        )
        items = payload.get("jobs", [])
        if not isinstance(items, list) or not items:
            break
        jobs.extend(item for item in items if isinstance(item, dict))
        if len(items) < 100:
            break
        page += 1
    return jobs


def fetch_workflow_runs(
    *,
    repo: str,
    token: str,
    api_url: str = DEFAULT_API_URL,
    days: int = DEFAULT_DAYS,
    workflow_names: set[str] | None = None,
) -> list[dict[str, Any]]:
    since = _now() - timedelta(days=days)
    created_filter = f">={since.strftime('%Y-%m-%dT%H:%M:%SZ')}"
    runs: list[dict[str, Any]] = []

    workflow_ids: list[int | None]
    if workflow_names:
        workflow_lookup = {
            str(item.get("name", "")).strip(): int(item.get("id", 0) or 0)
            for item in fetch_workflows(repo=repo, token=token, api_url=api_url)
            if isinstance(item, dict)
        }
        workflow_ids = [workflow_lookup.get(name) for name in sorted(workflow_names)]
    else:
        workflow_ids = [None]

    for workflow_id in workflow_ids:
        page = 1
        while True:
            path = (
                f"/repos/{repo}/actions/workflows/{workflow_id}/runs"
                if workflow_id
                else f"/repos/{repo}/actions/runs"
            )
            payload = _api_request_json(
                api_url=api_url,
                token=token,
                path=path,
                query={"per_page": 100, "page": page, "created": created_filter},
            )
            items = payload.get("workflow_runs", [])
            if not isinstance(items, list) or not items:
                break
            for item in items:
                if not isinstance(item, dict):
                    continue
                workflow_name = str(item.get("name", "")).strip()
                if workflow_names and workflow_name not in workflow_names:
                    continue
                runs.append(item)
            if len(items) < 100:
                break
            page += 1
    return runs


def load_contract(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def build_job_records(jobs: list[dict[str, Any]], contract: dict[str, Any]) -> list[JobRecord]:
    records: list[JobRecord] = []
    for job in jobs:
        name = str(job.get("name", "")).strip()
        contract_id = _job_contract_id(name)
        status = str(job.get("status", "")).lower()
        conclusion = str(job.get("conclusion", "") or "").lower()
        started_at = str(job.get("started_at", "") or "")
        completed_at = str(job.get("completed_at", "") or "")
        records.append(
            JobRecord(
                name=name,
                contract_id=contract_id,
                execution_class=_execution_class(name, contract_id, contract),
                status=status,
                conclusion=conclusion,
                started_at=started_at,
                completed_at=completed_at,
                duration_seconds=_duration_seconds(started_at, completed_at),
                html_url=str(job.get("html_url", "") or ""),
            )
        )
    return records


def summarize_job_records(records: list[JobRecord]) -> dict[str, Any]:
    by_classification: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"job_count": 0, "duration_seconds": 0}
    )
    by_result: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"job_count": 0, "duration_seconds": 0}
    )
    total_duration_seconds = 0
    for record in records:
        total_duration_seconds += record.duration_seconds
        by_classification[record.execution_class]["job_count"] += 1
        by_classification[record.execution_class]["duration_seconds"] += record.duration_seconds
        result_key = record.conclusion or record.status or "unknown"
        by_result[result_key]["job_count"] += 1
        by_result[result_key]["duration_seconds"] += record.duration_seconds

    longest_jobs = sorted(records, key=lambda item: item.duration_seconds, reverse=True)[:10]
    return {
        "total_jobs": len(records),
        "total_duration_seconds": total_duration_seconds,
        "total_duration_minutes": _round_minutes(total_duration_seconds),
        "by_classification": {
            key: {
                **value,
                "duration_minutes": _round_minutes(int(value["duration_seconds"])),
            }
            for key, value in sorted(by_classification.items())
        },
        "by_result": {
            key: {
                **value,
                "duration_minutes": _round_minutes(int(value["duration_seconds"])),
            }
            for key, value in sorted(by_result.items())
        },
        "longest_jobs": [
            {
                "name": item.name,
                "contract_id": item.contract_id,
                "execution_class": item.execution_class,
                "status": item.status,
                "conclusion": item.conclusion,
                "duration_seconds": item.duration_seconds,
                "duration_minutes": _round_minutes(item.duration_seconds),
                "html_url": item.html_url,
            }
            for item in longest_jobs
        ],
    }


def summarize_runs(
    runs: list[dict[str, Any]], *, days: int, workflow_names: set[str] | None = None
) -> dict[str, Any]:
    records: list[WorkflowRunRecord] = []
    for run in runs:
        name = str(run.get("name", "")).strip()
        if workflow_names and name not in workflow_names:
            continue
        run_started_at = str(run.get("run_started_at", "") or "")
        updated_at = str(run.get("updated_at", "") or "")
        records.append(
            WorkflowRunRecord(
                run_id=int(run.get("id", 0) or 0),
                name=name,
                event=str(run.get("event", "") or ""),
                status=str(run.get("status", "") or "").lower(),
                conclusion=str(run.get("conclusion", "") or "").lower(),
                run_started_at=run_started_at,
                updated_at=updated_at,
                head_branch=str(run.get("head_branch", "") or ""),
                duration_seconds=_duration_seconds(run_started_at, updated_at),
                html_url=str(run.get("html_url", "") or ""),
            )
        )

    by_workflow: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"run_count": 0, "duration_seconds": 0}
    )
    by_event: dict[str, dict[str, Any]] = defaultdict(
        lambda: {"run_count": 0, "duration_seconds": 0}
    )
    total_duration_seconds = 0
    for record in records:
        total_duration_seconds += record.duration_seconds
        by_workflow[record.name]["run_count"] += 1
        by_workflow[record.name]["duration_seconds"] += record.duration_seconds
        by_event[record.event]["run_count"] += 1
        by_event[record.event]["duration_seconds"] += record.duration_seconds

    longest_runs = sorted(records, key=lambda item: item.duration_seconds, reverse=True)[:10]
    return {
        "generated_at": _now().isoformat(),
        "days": days,
        "total_runs": len(records),
        "total_duration_seconds": total_duration_seconds,
        "total_duration_minutes": _round_minutes(total_duration_seconds),
        "by_workflow": {
            key: {
                **value,
                "duration_minutes": _round_minutes(int(value["duration_seconds"])),
            }
            for key, value in sorted(by_workflow.items())
        },
        "by_event": {
            key: {
                **value,
                "duration_minutes": _round_minutes(int(value["duration_seconds"])),
            }
            for key, value in sorted(by_event.items())
        },
        "longest_runs": [
            {
                "run_id": item.run_id,
                "name": item.name,
                "event": item.event,
                "status": item.status,
                "conclusion": item.conclusion,
                "head_branch": item.head_branch,
                "duration_seconds": item.duration_seconds,
                "duration_minutes": _round_minutes(item.duration_seconds),
                "html_url": item.html_url,
            }
            for item in longest_runs
        ],
    }


def render_run_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "### CI Cost Report",
        "",
        f"- Workflow: `{payload['workflow_name']}`",
        f"- Event: `{payload['event_name']}`",
        f"- Run ID: `{payload['run_id']}`",
        f"- Total estimated runtime: `{payload['summary']['total_duration_minutes']}` minutes",
        "",
        "#### Execution Classes",
        "",
        "| Class | Jobs | Minutes |",
        "|-------|------|---------|",
    ]
    for key, value in payload["summary"]["by_classification"].items():
        lines.append(f"| `{key}` | {value['job_count']} | {value['duration_minutes']} |")
    lines.extend(
        [
            "",
            "#### Longest Jobs",
            "",
            "| Job | Class | Minutes | Result |",
            "|-----|-------|---------|--------|",
        ]
    )
    for item in payload["summary"]["longest_jobs"][:5]:
        result = item["conclusion"] or item["status"]
        lines.append(
            "| `{name}` | `{execution_class}` | {minutes} | `{result}` |".format(
                name=item["name"],
                execution_class=item["execution_class"],
                minutes=item["duration_minutes"],
                result=result,
            )
        )
    lines.append("")
    return "\n".join(lines)


def render_summary_markdown(payload: dict[str, Any]) -> str:
    lines = [
        "### Weekly CI Usage Summary",
        "",
        f"- Window: last `{payload['days']}` day(s)",
        f"- Total runs: `{payload['total_runs']}`",
        f"- Total estimated runtime: `{payload['total_duration_minutes']}` minutes",
        "",
        "#### By Workflow",
        "",
        "| Workflow | Runs | Minutes |",
        "|----------|------|---------|",
    ]
    for key, value in payload["by_workflow"].items():
        lines.append(f"| `{key}` | {value['run_count']} | {value['duration_minutes']} |")
    lines.extend(
        [
            "",
            "#### By Event",
            "",
            "| Event | Runs | Minutes |",
            "|-------|------|---------|",
        ]
    )
    for key, value in payload["by_event"].items():
        lines.append(f"| `{key}` | {value['run_count']} | {value['duration_minutes']} |")
    lines.append("")
    return "\n".join(lines)


def _write_output(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    run_parser = subparsers.add_parser("run")
    run_parser.add_argument("--repo")
    run_parser.add_argument("--run-id", type=int)
    run_parser.add_argument("--event-name", required=True)
    run_parser.add_argument("--workflow-name", required=True)
    run_parser.add_argument("--output", required=True)
    run_parser.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    run_parser.add_argument("--api-url", default=DEFAULT_API_URL)
    run_parser.add_argument("--token")
    run_parser.add_argument("--jobs-file")

    summary_parser = subparsers.add_parser("summary")
    summary_parser.add_argument("--repo")
    summary_parser.add_argument("--days", type=int, default=DEFAULT_DAYS)
    summary_parser.add_argument("--output", required=True)
    summary_parser.add_argument("--api-url", default=DEFAULT_API_URL)
    summary_parser.add_argument("--token")
    summary_parser.add_argument("--runs-file")
    summary_parser.add_argument("--workflow-name", action="append", default=[])

    return parser


def _require_value(value: str | None, env_name: str) -> str:
    resolved = (value or os.environ.get(env_name) or "").strip()
    if not resolved:
        raise SystemExit(f"{env_name} is required")
    return resolved


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "run":
        contract = load_contract(Path(args.contract))
        if args.jobs_file:
            jobs = json.loads(Path(args.jobs_file).read_text(encoding="utf-8"))
        else:
            repo = _require_value(args.repo, "GITHUB_REPOSITORY")
            token = _require_value(args.token, "GITHUB_TOKEN")
            run_id = args.run_id or int(_require_value(None, "GITHUB_RUN_ID"))
            jobs = fetch_run_jobs(repo=repo, run_id=run_id, token=token, api_url=args.api_url)
        records = build_job_records(jobs, contract)
        payload = {
            "generated_at": _now().isoformat(),
            "repo": (args.repo or os.environ.get("GITHUB_REPOSITORY") or "").strip(),
            "run_id": args.run_id or int(os.environ.get("GITHUB_RUN_ID", "0") or 0),
            "workflow_name": args.workflow_name,
            "event_name": args.event_name,
            "summary": summarize_job_records(records),
            "jobs": [
                {
                    "name": item.name,
                    "contract_id": item.contract_id,
                    "execution_class": item.execution_class,
                    "status": item.status,
                    "conclusion": item.conclusion,
                    "duration_seconds": item.duration_seconds,
                    "duration_minutes": _round_minutes(item.duration_seconds),
                    "html_url": item.html_url,
                }
                for item in records
            ],
        }
        _write_output(Path(args.output), payload)
        summary = render_run_markdown(payload)
        print(summary)
        _append_summary(summary)
        return 0

    if args.runs_file:
        runs = json.loads(Path(args.runs_file).read_text(encoding="utf-8"))
    else:
        repo = _require_value(args.repo, "GITHUB_REPOSITORY")
        token = _require_value(args.token, "GITHUB_TOKEN")
        workflow_names = {item for item in args.workflow_name if item}
        runs = fetch_workflow_runs(
            repo=repo,
            token=token,
            api_url=args.api_url,
            days=int(args.days),
            workflow_names=workflow_names or None,
        )
    payload = summarize_runs(
        runs,
        days=int(args.days),
        workflow_names={item for item in args.workflow_name if item} or None,
    )
    _write_output(Path(args.output), payload)
    summary = render_summary_markdown(payload)
    print(summary)
    _append_summary(summary)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
