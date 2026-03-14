"""Deterministic owner-CI diagnostic analysis."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_DATABASE_UNAVAILABLE_RE = re.compile(
    r"database (?:is )?unavailable|database pool is unavailable",
    re.I,
)
_PLAYWRIGHT_MISSING_RE = re.compile(
    r"playwright|browser.*not found|executable doesn't exist",
    re.I,
)
_CONTAINER_STARTUP_RE = re.compile(
    r"docker compose|container .* exited|failed to start container|unable to start container",
    re.I,
)
_TEST_HARNESS_UNAVAILABLE_RE = re.compile(
    r"test harness unavailable|runner unavailable|docker unavailable|wsl unavailable",
    re.I,
)
_CONNECTOR_AUTH_RE = re.compile(
    r"(github|vercel|clerk|stripe).*(auth failed|unauthorized|forbidden|401|403)|"
    r"connector.*(auth failed|unauthorized|forbidden)",
    re.I,
)
_WEBHOOK_CORRELATION_RE = re.compile(
    r"missing webhook correlation|delivery id missing|event id missing|"
    r"webhook correlation.*missing",
    re.I,
)
_RELEASE_VERIFICATION_RE = re.compile(
    r"release verification failed|release receipt missing|release verification.*malformed",
    re.I,
)
_SHARD_CONTRACT_RE = re.compile(
    (
        r"(missing .*shard|shard .*invalid|no tests collected|usage: pytest|"
        r"file or directory not found)"
    ),
    re.I,
)


def _utc_now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _relative_artifact(path: str) -> str:
    candidate = Path(str(path or "").strip())
    if not str(candidate):
        return ""
    return str(candidate)


def _log_excerpt(logs: list[dict[str, Any]], limit: int = 10) -> str:
    lines = [
        str(entry.get("message") or "").strip()
        for entry in logs[:limit]
        if str(entry.get("message") or "").strip()
    ]
    return "\n".join(lines)


def _normalized_string_list(value: Any) -> list[str]:
    if isinstance(value, list | tuple | set):
        return [str(item).strip() for item in value if str(item).strip()]
    if isinstance(value, str) and value.strip():
        return [value.strip()]
    return []


def _artifact_paths_from_debug_bundle(debug_bundle: dict[str, Any] | None) -> list[str]:
    return [
        _relative_artifact(path)
        for path in list(
            (
                dict((debug_bundle or {}).get("bundle") or {}).get("artifact_receipt_paths") or {}
            ).values()
        )
        if _relative_artifact(path)
    ]


def _diagnostic_artifacts(findings: list[dict[str, Any]]) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for finding in findings:
        kind = str(finding.get("code") or finding.get("type") or "diagnostic_artifact").strip()
        for path in _normalized_string_list(finding.get("artifact_paths")):
            key = (kind, path)
            if key in seen:
                continue
            seen.add(key)
            artifacts.append({"kind": kind, "path": path})
    return artifacts


def build_coverage_diagnostics(
    *,
    coverage_summary: dict[str, Any],
    coverage_gaps: dict[str, Any],
    run_id: str,
    repo_id: str,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    failing_metrics = [
        name
        for name, payload in dict(coverage_summary.get("metrics") or {}).items()
        if isinstance(payload, dict) and not bool(payload.get("passed", False))
    ]
    finding = {
        "type": "coverage_gate_failed",
        "code": "coverage_gate_failed",
        "repo_id": repo_id,
        "run_id": run_id,
        "blocking": True,
        "retryable": False,
        "confidence": 0.99,
        "severity": "high",
        "summary": "Coverage gate failed.",
        "root_cause_summary": (
            f"Coverage is below threshold for {', '.join(failing_metrics)}."
            if failing_metrics
            else "Coverage is below one or more required thresholds."
        ),
        "recommended_fix": (
            "Use coverage-gaps.json to target the highest-impact uncovered branches "
            "and functions, then rerun the affected suites before the full gate."
        ),
        "recommended_next_actions": [
            "Review coverage-summary.json for the exact metric deltas.",
            "Use coverage-gaps.json to pick the highest-ROI uncovered targets.",
            "Add targeted tests for those exact branches/functions before rerunning the full gate.",
        ],
        "evidence_refs": [],
        "artifact_paths": [
            _relative_artifact(
                dict(coverage_summary.get("artifacts") or {}).get("coverage_json") or ""
            ),
            _relative_artifact(
                dict(coverage_summary.get("artifacts") or {}).get("coverage_report") or ""
            ),
        ],
        "details": {
            "metrics": dict(coverage_summary.get("metrics") or {}),
            "top_gaps": list(dict(coverage_gaps).get("gaps") or [])[:10],
        },
    }
    artifacts = _diagnostic_artifacts([finding])
    summary = {
        "generated_at": _utc_now_iso(),
        "repo_id": repo_id,
        "run_id": run_id,
        "status": "failed",
        "finding_count": 1,
        "blocking": True,
        "confidence": 0.99,
        "recommended_next_actions": list(finding["recommended_next_actions"]),
        "diagnostic_artifacts": artifacts,
        "artifact_paths": [
            path
            for path in finding["artifact_paths"]
            if isinstance(path, str) and path.strip()
        ],
    }
    return summary, [finding]


def build_run_diagnostics(
    *,
    run: dict[str, Any],
    logs: list[dict[str, Any]],
    debug_bundle: dict[str, Any] | None,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    repo_id = str(run.get("repo_id") or "").strip()
    run_id = str(run.get("run_id") or "").strip()
    findings: list[dict[str, Any]] = []
    excerpt = _log_excerpt(logs)
    shards = list(run.get("shards") or [])
    debug_artifact_paths = _artifact_paths_from_debug_bundle(debug_bundle)

    def add_finding(payload: dict[str, Any]) -> None:
        key = (
            str(payload.get("code") or payload.get("type") or "").strip(),
            str(payload.get("shard_id") or "").strip(),
            str(payload.get("lane_id") or "").strip(),
        )
        for finding in findings:
            existing_key = (
                str(finding.get("code") or finding.get("type") or "").strip(),
                str(finding.get("shard_id") or "").strip(),
                str(finding.get("lane_id") or "").strip(),
            )
            if existing_key == key:
                return
        findings.append(payload)

    for shard in shards:
        shard_id = str(shard.get("shard_id") or shard.get("lane_id") or "").strip()
        lane_id = str(shard.get("lane_id") or "").strip()
        result = dict(shard.get("result") or {})
        error = dict(shard.get("error") or {})
        status = str(shard.get("status") or "").strip().lower()
        coverage_summary = dict(result.get("coverage_summary") or {})
        coverage_gaps = dict(result.get("coverage_gaps") or {})
        if coverage_summary and coverage_gaps and not bool(coverage_summary.get("passed", True)):
            _, coverage_findings = build_coverage_diagnostics(
                coverage_summary=coverage_summary,
                coverage_gaps=coverage_gaps,
                run_id=run_id,
                repo_id=repo_id,
            )
            for finding in coverage_findings:
                finding["shard_id"] = shard_id or None
                finding["lane_id"] = lane_id or None
                add_finding(finding)

        missing_evidence = [
            str(path).strip()
            for path in list(result.get("missing_evidence") or [])
            if str(path).strip()
        ]
        if missing_evidence:
            add_finding(
                {
                    "type": "artifact_contract_failed",
                    "code": "artifact_contract_failed",
                    "repo_id": repo_id,
                    "run_id": run_id,
                    "shard_id": shard_id or None,
                    "lane_id": lane_id or None,
                    "blocking": True,
                    "retryable": False,
                    "confidence": 0.97,
                    "severity": "high",
                    "summary": "Required shard artifacts are missing.",
                    "root_cause_summary": (
                        f"Shard `{lane_id or shard_id}` did not emit the required artifacts."
                    ),
                    "recommended_fix": (
                        "Fix the shard output contract so the required artifacts are written "
                        "before marking the shard complete."
                    ),
                    "recommended_next_actions": [
                        "Check the shard artifact contract and expected paths.",
                        "Inspect the shard logs for early exits before artifact generation.",
                        "Rerun the shard after the artifact contract is fixed.",
                    ],
                    "artifact_paths": missing_evidence,
                    "evidence_refs": [],
                    "details": {"missing_evidence": missing_evidence},
                }
            )

        failed_required_paths = _normalized_string_list(
            result.get("failed_required_paths") or shard.get("failed_required_paths")
        )
        if failed_required_paths:
            add_finding(
                {
                    "type": "required_path_not_covered",
                    "code": "required_path_not_covered",
                    "repo_id": repo_id,
                    "run_id": run_id,
                    "shard_id": shard_id or None,
                    "lane_id": lane_id or None,
                    "blocking": True,
                    "retryable": False,
                    "confidence": 0.96,
                    "severity": "high",
                    "summary": "A required certification path was not satisfied.",
                    "root_cause_summary": (
                        f"Shard `{lane_id or shard_id}` failed required paths: "
                        + ", ".join(failed_required_paths[:5])
                    ),
                    "recommended_fix": (
                        "Restore the failing required path coverage and rerun the affected shard "
                        "before the full certification gate."
                    ),
                    "recommended_next_actions": [
                        "Inspect the shard receipt for the missing required paths.",
                        "Run the targeted suite for those path IDs locally or in-container.",
                        "Rerun the shard once the required path is green.",
                    ],
                    "artifact_paths": debug_artifact_paths,
                    "evidence_refs": [],
                    "details": {"failed_required_paths": failed_required_paths},
                }
            )

        admission_decision = dict(
            result.get("admission_decision") or shard.get("admission_decision") or {}
        )
        blocking_reasons = _normalized_string_list(
            admission_decision.get("blocking_reasons")
            or result.get("blocking_reasons")
            or shard.get("blocking_reasons")
        )
        if blocking_reasons and admission_decision.get("admitted") is False:
            add_finding(
                {
                    "type": "host_capacity_blocked",
                    "code": "host_capacity_blocked",
                    "repo_id": repo_id,
                    "run_id": run_id,
                    "shard_id": shard_id or None,
                    "lane_id": lane_id or None,
                    "blocking": True,
                    "retryable": True,
                    "confidence": 0.97,
                    "severity": "high",
                    "summary": "The shard was blocked by host-capacity admission policy.",
                    "root_cause_summary": (
                        f"Shard `{lane_id or shard_id}` could not be admitted: "
                        + ", ".join(blocking_reasons[:5])
                    ),
                    "recommended_fix": (
                        "Reduce host pressure, wait for active work to finish, or rebalance the "
                        "reservation before retrying."
                    ),
                    "recommended_next_actions": [
                        "Inspect the host capacity snapshot and reservation for the shard.",
                        "Free capacity or reduce parallel contention on the Windows host.",
                        "Retry once the admission blockers are cleared.",
                    ],
                    "artifact_paths": debug_artifact_paths,
                    "evidence_refs": [],
                    "details": {
                        "blocking_reasons": blocking_reasons,
                        "admission_decision": admission_decision,
                    },
                }
            )

        log_text = "\n".join(
            str(item).strip()
            for item in (
                str(error.get("message") or "").strip(),
                str(error.get("details") or "").strip(),
                excerpt,
            )
            if str(item).strip()
        )
        if status == "failed" and _SHARD_CONTRACT_RE.search(log_text):
            add_finding(
                {
                    "type": "shard_contract_invalid",
                    "code": "shard_contract_invalid",
                    "repo_id": repo_id,
                    "run_id": run_id,
                    "shard_id": shard_id or None,
                    "lane_id": lane_id or None,
                    "blocking": True,
                    "retryable": False,
                    "confidence": 0.88,
                    "severity": "high",
                    "summary": "Shard structure or invocation is invalid.",
                    "root_cause_summary": (
                        f"Shard `{lane_id or shard_id}` failed before executing normally."
                    ),
                    "recommended_fix": (
                        "Validate the shard command, markers, test selection, and "
                        "artifact contract "
                        "before rerunning it."
                    ),
                    "recommended_next_actions": [
                        "Check the shard command and selected tests.",
                        "Verify the shard marker expression still resolves to real tests.",
                        "Confirm the shard writes the required artifacts and readiness receipts.",
                    ],
                    "artifact_paths": debug_artifact_paths,
                    "evidence_refs": [],
                    "details": {"log_excerpt": excerpt[:2000]},
                }
            )

        release_receipt = dict((run.get("metadata") or {}).get("release_verification") or {})
        if (
            "cgs_release_verification" in failed_required_paths
            or "release_verification" in failed_required_paths
            or (status == "failed" and _RELEASE_VERIFICATION_RE.search(log_text))
        ):
            add_finding(
                {
                    "type": "release_receipt_missing",
                    "code": "release_receipt_missing",
                    "repo_id": repo_id,
                    "run_id": run_id,
                    "shard_id": shard_id or None,
                    "lane_id": lane_id or None,
                    "blocking": True,
                    "retryable": False,
                    "confidence": 0.92,
                    "severity": "high",
                    "summary": "Release verification evidence is missing or malformed.",
                    "root_cause_summary": (
                        "Release verification was required but the receipt was missing, failed, "
                        "or malformed."
                    ),
                    "recommended_fix": (
                        "Ensure the release-verification lane writes a valid receipt and "
                        "publishes it into run metadata and evidence."
                    ),
                    "recommended_next_actions": [
                        "Inspect the release-verification shard and receipt payload.",
                        "Verify the receipt is persisted into run metadata.",
                        "Rerun release verification after the receipt contract is fixed.",
                    ],
                    "artifact_paths": debug_artifact_paths,
                    "evidence_refs": [],
                    "details": {
                        "failed_required_paths": failed_required_paths,
                        "release_verification": release_receipt,
                    },
                }
            )

    if excerpt and _DATABASE_UNAVAILABLE_RE.search(excerpt):
        add_finding(
            {
                "type": "database_unavailable",
                "code": "runtime_dependency_missing",
                "repo_id": repo_id,
                "run_id": run_id,
                "blocking": True,
                "retryable": True,
                "confidence": 0.9,
                "severity": "high",
                "summary": "The test runtime database is unavailable.",
                "root_cause_summary": "Runtime logs indicate the database or pool was unavailable.",
                "recommended_fix": (
                    "Restore database connectivity for the failing shard or runtime service "
                    "before retrying the run."
                ),
                "recommended_next_actions": [
                    "Check the runtime/service container health and database connectivity.",
                    "Verify required database env vars and migrations are present.",
                    "Retry the run only after the dependency is healthy.",
                ],
                "artifact_paths": debug_artifact_paths,
                "evidence_refs": [],
                "details": {"log_excerpt": excerpt[:2000]},
            }
        )
    if excerpt and _PLAYWRIGHT_MISSING_RE.search(excerpt):
        add_finding(
            {
                "type": "runtime_dependency_missing",
                "code": "runtime_dependency_missing",
                "repo_id": repo_id,
                "run_id": run_id,
                "blocking": True,
                "retryable": True,
                "confidence": 0.88,
                "severity": "high",
                "summary": "A browser/runtime dependency is missing.",
                "root_cause_summary": (
                    "Logs indicate Playwright or the required browser runtime is unavailable."
                ),
                "recommended_fix": (
                    "Install or provision the browser/runtime dependency inside "
                    "the container or worker image."
                ),
                "recommended_next_actions": [
                    "Check the container image or test harness for "
                    "Playwright/browser installation.",
                    "Verify the failing shard is running in the supported containerized path.",
                    "Retry after the runtime dependency is restored.",
                ],
                "artifact_paths": debug_artifact_paths,
                "evidence_refs": [],
                "details": {"log_excerpt": excerpt[:2000]},
            }
        )

    if excerpt and _TEST_HARNESS_UNAVAILABLE_RE.search(excerpt):
        add_finding(
            {
                "type": "test_harness_unavailable",
                "code": "test_harness_unavailable",
                "repo_id": repo_id,
                "run_id": run_id,
                "blocking": True,
                "retryable": True,
                "confidence": 0.87,
                "severity": "high",
                "summary": "The CI test harness is unavailable.",
                "root_cause_summary": (
                    "Logs indicate the required runner, container harness, or WSL environment "
                    "was unavailable."
                ),
                "recommended_fix": (
                    "Restore the required runner/container/WSL harness before retrying the run."
                ),
                "recommended_next_actions": [
                    "Inspect the failing runner image or host prerequisites.",
                    "Verify the expected harness is available in the supported containerized path.",
                    "Retry the shard once the runner is healthy.",
                ],
                "artifact_paths": debug_artifact_paths,
                "evidence_refs": [],
                "details": {"log_excerpt": excerpt[:2000]},
            }
        )

    if excerpt and _CONTAINER_STARTUP_RE.search(excerpt):
        add_finding(
            {
                "type": "container_startup_failed",
                "code": "container_startup_failed",
                "repo_id": repo_id,
                "run_id": run_id,
                "blocking": True,
                "retryable": True,
                "confidence": 0.9,
                "severity": "high",
                "summary": "A required CI container failed to start cleanly.",
                "root_cause_summary": (
                    "Logs indicate Docker Compose or a required test container failed during "
                    "startup."
                ),
                "recommended_fix": (
                    "Fix the container startup failure and confirm the shard can reach its "
                    "required runtime dependencies."
                ),
                "recommended_next_actions": [
                    "Inspect compose/container startup logs for the failing service.",
                    "Verify required env vars, ports, and dependent services are available.",
                    "Rerun the shard after the container startup issue is resolved.",
                ],
                "artifact_paths": debug_artifact_paths,
                "evidence_refs": [],
                "details": {"log_excerpt": excerpt[:2000]},
            }
        )

    if excerpt and _CONNECTOR_AUTH_RE.search(excerpt):
        add_finding(
            {
                "type": "connector_auth_failed",
                "code": "connector_auth_failed",
                "repo_id": repo_id,
                "run_id": run_id,
                "blocking": True,
                "retryable": False,
                "confidence": 0.84,
                "severity": "high",
                "summary": "A provider connector authentication failure blocked the run.",
                "root_cause_summary": (
                    "Logs indicate GitHub, Vercel, Clerk, Stripe, or another connector failed "
                    "authentication."
                ),
                "recommended_fix": (
                    "Re-authenticate or rotate the failing connector credentials, then rerun."
                ),
                "recommended_next_actions": [
                    "Inspect connector health and recent auth failures for the affected provider.",
                    "Rotate or reconnect the provider credentials if needed.",
                    "Retry the blocked operation after connector health is green.",
                ],
                "artifact_paths": debug_artifact_paths,
                "evidence_refs": [],
                "details": {"log_excerpt": excerpt[:2000]},
            }
        )

    if excerpt and _WEBHOOK_CORRELATION_RE.search(excerpt):
        add_finding(
            {
                "type": "webhook_correlation_missing",
                "code": "webhook_correlation_missing",
                "repo_id": repo_id,
                "run_id": run_id,
                "blocking": True,
                "retryable": False,
                "confidence": 0.82,
                "severity": "medium",
                "summary": "Webhook evidence could not be correlated to the expected operation.",
                "root_cause_summary": (
                    "Logs indicate a webhook delivery or event identifier was missing."
                ),
                "recommended_fix": (
                    "Persist the provider delivery/event identifiers and correlation metadata "
                    "before processing the webhook."
                ),
                "recommended_next_actions": [
                    "Inspect the provider event payload and webhook correlation metadata.",
                    "Ensure delivery IDs and event IDs are persisted into the operation refs.",
                    "Replay the webhook after correlation metadata is fixed.",
                ],
                "artifact_paths": debug_artifact_paths,
                "evidence_refs": [],
                "details": {"log_excerpt": excerpt[:2000]},
            }
        )

    blocking = any(bool(finding.get("blocking")) for finding in findings)
    artifacts = _diagnostic_artifacts(findings)
    summary = {
        "generated_at": _utc_now_iso(),
        "repo_id": repo_id,
        "run_id": run_id,
        "status": str(run.get("status") or "").strip().lower() or "unknown",
        "finding_count": len(findings),
        "blocking": blocking,
        "confidence": round(
            max((float(finding.get("confidence") or 0.0) for finding in findings), default=0.0),
            2,
        ),
        "recommended_next_actions": list(
            dict.fromkeys(
                action
                for finding in findings
                for action in list(finding.get("recommended_next_actions") or [])
            )
        )[:10],
        "diagnostic_artifacts": artifacts,
        "artifact_paths": debug_artifact_paths,
    }
    return summary, findings


def load_json_artifact(path: str | Path | None) -> dict[str, Any]:
    candidate = Path(path or "")
    if not str(candidate).strip() or not candidate.is_file():
        return {}
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return dict(payload) if isinstance(payload, dict) else {}
