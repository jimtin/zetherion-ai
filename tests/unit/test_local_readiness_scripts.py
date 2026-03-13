"""Tests for local and workspace readiness receipt writers."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_script_module(name: str, filename: str):
    module_path = Path(__file__).resolve().parents[2] / "scripts" / filename
    spec = importlib.util.spec_from_file_location(name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_write_local_readiness_receipt_synthesizes_shards_from_execution_log(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / ".ci").mkdir()
    (tmp_path / "docs" / "migration").mkdir(parents=True)
    (tmp_path / ".artifacts" / "z-int-runtime-queue").mkdir(parents=True)

    manifest = {
        "lane_catalog": {
            "z-int-runtime-queue": {
                "resource_class": "service",
                "service_slot": "slot_a",
                "release_blocking": True,
                "covered_required_paths": [
                    "queue_reliability",
                    "runtime_status_persistence",
                ],
            },
            "z-e2e-discord-live": {
                "resource_class": "serial",
                "release_blocking": True,
                "covered_required_paths": [
                    "discord_dm_reply",
                    "discord_channel_reply",
                ],
            },
        }
    }
    (tmp_path / ".ci" / "local_gate_manifest.json").write_text(
        json.dumps(manifest),
        encoding="utf-8",
    )
    (tmp_path / "docs" / "migration" / "test-execution-log.md").write_text(
        "\n".join(
            [
                "# Test Execution Log",
                "| Timestamp (UTC) | Lane | Command | Result | Duration (s) | Reason | Diagnostics |",
                "|---|---|---|---|---:|---|---|",
                "| 2026-03-13T00:00:00Z | z-int-runtime-queue | `pytest runtime` | passed | 8 |  | - |",
                "| 2026-03-13T00:02:00Z | z-e2e-discord-live | `bash ./scripts/local-required-e2e-receipt.sh` | passed | 120 |  | - |",
                "",
            ]
        ),
        encoding="utf-8",
    )
    (tmp_path / ".ci" / "e2e-receipt.json").write_text(
        json.dumps(
            {
                "status": "success",
                "reason_code": "required_suites_passed",
                "suites": {
                    "docker_e2e": {"status": "passed"},
                    "discord_required_e2e": {"status": "passed"},
                },
            }
        ),
        encoding="utf-8",
    )

    module = _load_script_module(
        "write_local_readiness_receipt_module",
        "write-local-readiness-receipt.py",
    )
    output_path = tmp_path / ".artifacts" / "local-readiness-receipt.json"
    argv = [
        "write-local-readiness-receipt.py",
        "--repo-id",
        "zetherion-ai",
        "--output",
        str(output_path),
        "--status",
        "failed",
        "--summary",
        "Zetherion local gate passed.",
        "--merge-ready",
        "false",
        "--deploy-ready",
        "false",
        "--release-receipt",
        str(tmp_path / ".ci" / "e2e-receipt.json"),
        "--manifest",
        str(tmp_path / ".ci" / "local_gate_manifest.json"),
        "--execution-log",
        str(tmp_path / "docs" / "migration" / "test-execution-log.md"),
        "--lane",
        "z-int-runtime-queue",
        "--lane",
        "z-e2e-discord-live",
    ]
    monkeypatch.setattr(sys, "argv", argv)

    assert module.main() == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["status"] == "success"
    assert payload["merge_ready"] is True
    assert payload["deploy_ready"] is True
    assert {item["lane_id"] for item in payload["shard_receipts"]} == {
        "z-int-runtime-queue",
        "z-e2e-discord-live",
    }
    assert payload["release_verification"]["delivery_canary_passed"] is True
    assert payload["release_verification"]["queue_worker_healthy"] is True
    assert payload["release_verification"]["runtime_status_persistence"] is True


def test_write_workspace_readiness_receipt_aggregates_repo_receipts(
    tmp_path, monkeypatch
) -> None:
    monkeypatch.chdir(tmp_path)
    repo_a = tmp_path / "repo-a.json"
    repo_b = tmp_path / "repo-b.json"
    repo_a.write_text(
        json.dumps(
            {
                "repo_id": "zetherion-ai",
                "merge_ready": True,
                "deploy_ready": True,
                "failed_required_paths": [],
                "missing_evidence": [],
            }
        ),
        encoding="utf-8",
    )
    repo_b.write_text(
        json.dumps(
            {
                "repo_id": "catalyst-group-solutions",
                "merge_ready": True,
                "deploy_ready": False,
                "failed_required_paths": ["cgs_owner_ci_reporting"],
                "missing_evidence": ["playwright-report"],
            }
        ),
        encoding="utf-8",
    )

    module = _load_script_module(
        "write_workspace_readiness_receipt_module",
        "write-workspace-readiness-receipt.py",
    )
    output_path = tmp_path / "workspace.json"
    argv = [
        "write-workspace-readiness-receipt.py",
        "--output",
        str(output_path),
        "--repo-receipt",
        str(repo_a),
        "--repo-receipt",
        str(repo_b),
    ]
    monkeypatch.setattr(sys, "argv", argv)

    assert module.main() == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))

    assert payload["merge_ready"] is True
    assert payload["deploy_ready"] is False
    assert payload["failed_required_paths"] == ["cgs_owner_ci_reporting"]
    assert payload["missing_evidence"] == ["playwright-report"]
    assert payload["external_status_contexts"] == [
        "zetherion/merge-readiness",
        "zetherion/deploy-readiness",
    ]
