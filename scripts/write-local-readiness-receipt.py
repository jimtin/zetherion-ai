#!/usr/bin/env python3
"""Write a repo-local readiness receipt for owner-CI fallback."""

from __future__ import annotations

import argparse
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


_LOG_ROW_RE = re.compile(
    r"^\|\s*(?P<timestamp>[^|]+?)\s*\|\s*(?P<lane>[^|]+?)\s*\|\s*`(?P<command>(?:\\\||[^`])*)`\s*\|\s*"
    r"(?P<result>[^|]+?)\s*\|\s*(?P<duration>[^|]+?)\s*\|\s*(?P<reason>[^|]*?)\s*\|\s*(?P<diagnostics>[^|]*?)\s*\|$"
)
_DEFAULT_MANIFEST = Path(__file__).resolve().parents[1] / ".ci" / "local_gate_manifest.json"
_DEFAULT_LOG = Path(__file__).resolve().parents[1] / "docs" / "migration" / "test-execution-log.md"


def _parse_bool(value: str) -> bool:
    normalized = value.strip().lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value: {value!r}")


def _as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return bool(value)
    normalized = str(value).strip().lower()
    if normalized in {"1", "true", "yes", "on", "passed", "healthy", "success", "green"}:
        return True
    if normalized in {"0", "false", "no", "off", "failed", "blocked", "red"}:
        return False
    return None


def _load_json(path: str | None) -> dict[str, Any] | None:
    if not path:
        return None
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if not candidate.is_file():
        return None
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return payload if isinstance(payload, dict) else None


def _load_manifest(path: str | None) -> dict[str, Any]:
    candidate = Path(path) if path else _DEFAULT_MANIFEST
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if not candidate.is_file():
        return {}
    try:
        payload = json.loads(candidate.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_execution_log(path: str | None) -> dict[str, dict[str, str]]:
    candidate = Path(path) if path else _DEFAULT_LOG
    if not candidate.is_absolute():
        candidate = Path.cwd() / candidate
    if not candidate.is_file():
        return {}

    latest_by_lane: dict[str, dict[str, str]] = {}
    for raw_line in candidate.read_text(encoding="utf-8").splitlines():
        match = _LOG_ROW_RE.match(raw_line)
        if not match:
            continue
        lane_id = match.group("lane").strip()
        if not lane_id or lane_id == "Lane":
            continue
        latest_by_lane[lane_id] = {
            "timestamp": match.group("timestamp").strip(),
            "lane_id": lane_id,
            "command": match.group("command").replace("\\|", "|").strip(),
            "result": match.group("result").strip(),
            "duration_seconds": match.group("duration").strip(),
            "reason": match.group("reason").strip(),
            "diagnostics": match.group("diagnostics").strip(),
            "log_path": str(candidate),
        }
    return latest_by_lane


def _stage_for_lane(lane_id: str) -> str:
    if lane_id.startswith("z-unit"):
        return "unit"
    if lane_id.startswith("z-int"):
        return "integration"
    if lane_id.startswith("z-e2e"):
        return "e2e"
    if lane_id.startswith("z-release"):
        return "release"
    return "check"


def _normalize_lane_status(value: str) -> str:
    normalized = value.strip().lower()
    if normalized == "passed":
        return "succeeded"
    if normalized in {"failed", "stalled", "timed_out"}:
        return normalized
    return "planned"


def _collect_evidence_paths(
    *,
    lane_id: str,
    lane_metadata: dict[str, Any],
    execution_entry: dict[str, str],
    release_receipt_path: str,
) -> list[str]:
    repo_root = Path.cwd()
    evidence: list[str] = []

    def add_if_exists(candidate: Path | None) -> None:
        if candidate is None:
            return
        target = candidate if candidate.is_absolute() else repo_root / candidate
        if target.exists():
            evidence.append(str(target))

    log_path = execution_entry.get("log_path", "").strip()
    if log_path:
        add_if_exists(Path(log_path))

    diagnostics = execution_entry.get("diagnostics", "").strip()
    if diagnostics and diagnostics != "-":
        add_if_exists(Path(diagnostics))

    cleanup_receipt = str(lane_metadata.get("cleanup_receipt_path") or "").strip()
    if cleanup_receipt:
        add_if_exists(Path(cleanup_receipt))

    if lane_id.startswith(("z-int-", "z-e2e-")):
        add_if_exists(Path(".artifacts") / lane_id)
        add_if_exists(Path(".artifacts") / lane_id / "cleanup-receipt.json")

    if lane_id in {"z-e2e-discord-live", "z-e2e-discord-real", "z-release"}:
        add_if_exists(Path(release_receipt_path))
        add_if_exists(Path(".artifacts") / "discord-e2e-last-run.json")
        add_if_exists(Path(".artifacts") / "discord-e2e-local-run.log")
        add_if_exists(Path(".artifacts") / "docker-e2e-local-run.log")

    deduped: list[str] = []
    seen: set[str] = set()
    for path in evidence:
        if path not in seen:
            seen.add(path)
            deduped.append(path)
    return deduped


def _synthesize_shard_receipt(
    *,
    repo_id: str,
    lane_id: str,
    lane_metadata: dict[str, Any],
    execution_entry: dict[str, str],
    release_receipt_path: str,
) -> dict[str, Any]:
    status = _normalize_lane_status(execution_entry.get("result", "planned"))
    evidence_paths = _collect_evidence_paths(
        lane_id=lane_id,
        lane_metadata=lane_metadata,
        execution_entry=execution_entry,
        release_receipt_path=release_receipt_path,
    )
    release_blocking = bool(lane_metadata.get("release_blocking", True))
    typed_incidents: list[str] = []
    if status != "succeeded":
        typed_incidents.append("release_blocker" if release_blocking else "service_evidence_incomplete")

    try:
        duration_seconds = float(execution_entry.get("duration_seconds", "").strip() or 0)
    except ValueError:
        duration_seconds = None

    cleanup_receipt_path = str(lane_metadata.get("cleanup_receipt_path") or "").strip() or None
    resolved_cleanup_receipt_path = None
    if cleanup_receipt_path:
        candidate = Path(cleanup_receipt_path)
        if not candidate.is_absolute():
            candidate = Path.cwd() / candidate
        if candidate.exists():
            resolved_cleanup_receipt_path = str(candidate)

    return {
        "repo_id": repo_id,
        "lane_id": lane_id,
        "shard_id": lane_id,
        "stage": _stage_for_lane(lane_id),
        "status": status,
        "metadata": {
            "resource_class": str(lane_metadata.get("resource_class") or "cpu"),
            "service_slot": str(lane_metadata.get("service_slot") or "").strip() or None,
            "covered_required_paths": [
                str(value).strip()
                for value in list(lane_metadata.get("covered_required_paths") or [])
                if str(value).strip()
            ],
            "required_paths": [
                str(value).strip()
                for value in list(lane_metadata.get("covered_required_paths") or [])
                if str(value).strip()
            ],
            "release_blocking": release_blocking,
            "cleanup_receipt_path": resolved_cleanup_receipt_path,
        },
        "artifact_contract": {
            "expects": evidence_paths,
        },
        "result": {
            "duration_seconds": duration_seconds,
            "evidence_paths": evidence_paths,
            "missing_evidence": [],
            "cleanup_receipt_path": resolved_cleanup_receipt_path,
            "typed_incidents": typed_incidents,
            "release_blocking": release_blocking,
            "log_path": execution_entry.get("log_path", "").strip() or None,
            "timed_out": status == "timed_out",
        },
        "error": {} if status == "succeeded" else {"typed_incidents": typed_incidents},
    }


def _unique_sorted(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def _merge_release_verification(
    *,
    release_receipt: dict[str, Any] | None,
    path_results: dict[str, bool],
    missing_evidence: list[str],
    deploy_ready: bool,
) -> dict[str, Any]:
    payload = dict(release_receipt or {})
    payload.setdefault("status", "healthy" if deploy_ready else "deployed_but_unhealthy")
    payload["status"] = "healthy" if deploy_ready else "deployed_but_unhealthy"
    payload["missing_evidence"] = _unique_sorted(
        [str(item).strip() for item in list(payload.get("missing_evidence") or [])] + missing_evidence
    )
    payload["delivery_canary_passed"] = (
        path_results.get("discord_dm_reply", False) and path_results.get("discord_channel_reply", False)
    )
    payload["queue_worker_healthy"] = path_results.get("queue_reliability")
    payload["runtime_status_persistence"] = path_results.get("runtime_status_persistence")
    payload["skills_reachable"] = path_results.get("skills_reachability")
    payload["runtime_drift_zero"] = path_results.get("runtime_drift_zero")
    payload["back_to_back_deploy_passed"] = path_results.get("back_to_back_deploys")
    if "security_canary_passed" not in payload:
        payload["security_canary_passed"] = None
    return payload


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo-id", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--summary", required=True)
    parser.add_argument("--merge-ready", required=True)
    parser.add_argument("--deploy-ready", required=True)
    parser.add_argument("--git-sha", default="")
    parser.add_argument("--failed-path", action="append", default=[])
    parser.add_argument("--missing-evidence", action="append", default=[])
    parser.add_argument("--release-receipt", default="")
    parser.add_argument("--recorded-at", default="")
    parser.add_argument("--source", default="local_gate")
    parser.add_argument("--shard-receipt", action="append", default=[])
    parser.add_argument("--manifest", default="")
    parser.add_argument("--execution-log", default="")
    parser.add_argument("--lane", action="append", default=[])
    return parser


def main() -> int:
    args = build_parser().parse_args()
    recorded_at = args.recorded_at.strip() or datetime.now(timezone.utc).isoformat()
    release_receipt = _load_json(args.release_receipt)

    shard_receipts = [
        receipt
        for receipt in (_load_json(path) for path in args.shard_receipt)
        if isinstance(receipt, dict)
    ]

    if args.lane:
        manifest = _load_manifest(args.manifest)
        lane_catalog = dict(manifest.get("lane_catalog") or {})
        execution_log = _parse_execution_log(args.execution_log)
        for lane_id in args.lane:
            normalized_lane = str(lane_id or "").strip()
            if not normalized_lane or normalized_lane in {receipt.get("lane_id") for receipt in shard_receipts}:
                continue
            execution_entry = execution_log.get(normalized_lane)
            lane_metadata = dict(lane_catalog.get(normalized_lane) or {})
            if not execution_entry or not lane_metadata:
                continue
            shard_receipts.append(
                _synthesize_shard_receipt(
                    repo_id=args.repo_id,
                    lane_id=normalized_lane,
                    lane_metadata=lane_metadata,
                    execution_entry=execution_entry,
                    release_receipt_path=args.release_receipt,
                )
            )

    aggregated_failed_paths = set(item for item in args.failed_path if item)
    aggregated_missing_evidence = set(item for item in args.missing_evidence if item)
    path_results: dict[str, bool] = {}

    for shard in shard_receipts:
        metadata = dict(shard.get("metadata") or {})
        result = dict(shard.get("result") or {})
        required_paths = [
            str(item).strip()
            for item in list(
                metadata.get("covered_required_paths")
                or metadata.get("required_paths")
                or []
            )
            if str(item).strip()
        ]
        passed = str(shard.get("status") or "").lower() == "succeeded"
        release_blocking = bool(metadata.get("release_blocking", True))
        for required_path in required_paths:
            path_results[required_path] = path_results.get(required_path, False) or passed
            if not passed and release_blocking:
                aggregated_failed_paths.add(required_path)

        for evidence_path in list(result.get("missing_evidence") or []):
            normalized = str(evidence_path).strip()
            if normalized:
                aggregated_missing_evidence.add(normalized)

    blocking_shard_failed = any(
        bool(dict(shard.get("metadata") or {}).get("release_blocking", True))
        and str(shard.get("status") or "").lower() != "succeeded"
        for shard in shard_receipts
    )

    merge_ready = _parse_bool(args.merge_ready)
    deploy_ready = _parse_bool(args.deploy_ready)
    if shard_receipts:
        merge_ready = not blocking_shard_failed
        deploy_ready = merge_ready and not aggregated_failed_paths and not aggregated_missing_evidence

    failed_paths = _unique_sorted(list(aggregated_failed_paths))
    missing_evidence = _unique_sorted(list(aggregated_missing_evidence))

    payload = {
        "repo_id": args.repo_id,
        "status": "success" if deploy_ready else ("failed" if shard_receipts else args.status),
        "merge_ready": merge_ready,
        "deploy_ready": deploy_ready,
        "failed_required_paths": failed_paths,
        "missing_evidence": missing_evidence,
        "summary": args.summary,
        "recorded_at": recorded_at,
        "source": args.source,
        "metadata": {
            "git_sha": args.git_sha.strip() or None,
        },
    }

    if shard_receipts:
        payload["shard_receipts"] = shard_receipts

    merged_release = _merge_release_verification(
        release_receipt=release_receipt,
        path_results=path_results,
        missing_evidence=missing_evidence,
        deploy_ready=deploy_ready,
    )
    if merged_release:
        payload["release_verification"] = merged_release

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
