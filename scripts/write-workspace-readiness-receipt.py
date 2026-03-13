#!/usr/bin/env python3
"""Aggregate repo-local readiness receipts into one workspace receipt."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _load_json(path: str) -> dict[str, Any] | None:
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


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-receipt", action="append", default=[])
    parser.add_argument("--summary", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    repo_receipts = [
        payload
        for payload in (_load_json(candidate) for candidate in args.repo_receipt)
        if isinstance(payload, dict)
    ]

    failed_required_paths = sorted(
        {
            str(path).strip()
            for receipt in repo_receipts
            for path in list(receipt.get("failed_required_paths") or [])
            if str(path).strip()
        }
    )
    missing_evidence = sorted(
        {
            str(path).strip()
            for receipt in repo_receipts
            for path in list(receipt.get("missing_evidence") or [])
            if str(path).strip()
        }
    )
    merge_ready = bool(repo_receipts) and all(bool(receipt.get("merge_ready")) for receipt in repo_receipts)
    deploy_ready = bool(repo_receipts) and all(bool(receipt.get("deploy_ready")) for receipt in repo_receipts)

    payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "merge_ready": merge_ready,
        "deploy_ready": deploy_ready,
        "repo_receipts": repo_receipts,
        "failed_required_paths": failed_required_paths,
        "missing_evidence": missing_evidence,
        "summary": args.summary.strip()
        or (
            "ready"
            if merge_ready and deploy_ready and not failed_required_paths and not missing_evidence
            else "workspace has blockers"
        ),
        "external_status_contexts": [
            "zetherion/merge-readiness",
            "zetherion/deploy-readiness",
        ],
    }

    output_path = Path(args.output)
    if not output_path.is_absolute():
        output_path = Path.cwd() / output_path
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
