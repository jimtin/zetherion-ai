"""Tests for local receipt fallback in owner-CI storage."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.owner_ci.storage import (
    OwnerCiStorage,
    _expand_local_candidate_roots,
    _load_local_repo_readiness,
    _load_local_worker_certification,
)


def test_load_local_repo_readiness_reads_repo_receipt(tmp_path) -> None:
    repo_root = tmp_path / "catalyst-group-solutions"
    receipt_path = repo_root / ".artifacts" / "local-readiness-receipt.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "repo_id": "catalyst-group-solutions",
                "merge_ready": True,
                "deploy_ready": True,
                "failed_required_paths": [],
                "missing_evidence": [],
                "summary": "ready",
                "recorded_at": "2026-03-13T08:00:00Z",
                "metadata": {"git_sha": "abc123"},
                "shard_receipts": [
                    {
                        "lane_id": "c-int-owner-ci",
                        "shard_id": "c-int-owner-ci",
                        "status": "succeeded",
                        "metadata": {
                            "covered_required_paths": ["cgs_owner_ci_reporting"],
                            "resource_class": "cpu",
                        },
                        "artifact_contract": {"expects": []},
                        "result": {"evidence_paths": []},
                    }
                ],
                "release_verification": {
                    "status": "healthy",
                    "cgs_auth_flow_passed": True,
                    "ai_ops_schema_passed": True,
                },
            }
        ),
        encoding="utf-8",
    )

    receipt, payload = _load_local_repo_readiness(
        {
            "repo_id": "catalyst-group-solutions",
            "allowed_paths": [str(repo_root)],
        }
    )

    assert receipt is not None
    assert payload is not None
    assert receipt.repo_id == "catalyst-group-solutions"
    assert receipt.merge_ready is True
    assert receipt.deploy_ready is True
    assert receipt.release_verification is not None
    assert receipt.release_verification.ai_ops_schema_passed is True
    assert len(receipt.shard_receipts) == 1
    assert receipt.shard_receipts[0].required_paths == ["cgs_owner_ci_reporting"]


def test_expand_local_candidate_roots_translates_windows_paths_for_posix_runtime() -> None:
    roots = _expand_local_candidate_roots(r"C:\ZetherionCI\workspaces\catalyst-group-solutions")

    assert Path("/mnt/c/ZetherionCI/workspaces/catalyst-group-solutions") in roots


def test_expand_local_candidate_roots_handles_blank_and_absolute_paths(tmp_path) -> None:
    assert _expand_local_candidate_roots("") == []
    assert tmp_path in _expand_local_candidate_roots(str(tmp_path))


def test_load_local_repo_readiness_skips_invalid_candidates_before_valid_receipt(tmp_path) -> None:
    repo_root = tmp_path / "zetherion-ai"
    invalid_path = repo_root / ".artifacts" / "local-readiness-receipt.json"
    valid_path = repo_root / ".ci" / "local-readiness-receipt.json"
    invalid_path.parent.mkdir(parents=True)
    valid_path.parent.mkdir(parents=True)
    invalid_path.write_text("{invalid", encoding="utf-8")
    valid_path.write_text(
        json.dumps(
            {
                "repo_id": "zetherion-ai",
                "merge_ready": True,
                "deploy_ready": False,
                "summary": "loaded from ci directory",
            }
        ),
        encoding="utf-8",
    )

    receipt, payload = _load_local_repo_readiness(
        {
            "repo_id": "zetherion-ai",
            "allowed_paths": [str(repo_root)],
        }
    )

    assert receipt is not None
    assert payload is not None
    assert receipt.repo_id == "zetherion-ai"
    assert receipt.summary == "loaded from ci directory"


def test_load_local_repo_readiness_continues_past_non_normalizable_payloads(tmp_path) -> None:
    repo_root = tmp_path / "zetherion-ai"
    first_path = repo_root / ".artifacts" / "local-readiness-receipt.json"
    second_path = repo_root / ".ci" / "local-readiness-receipt.json"
    first_path.parent.mkdir(parents=True)
    second_path.parent.mkdir(parents=True)
    first_path.write_text(
        json.dumps(
            {
                "merge_ready": True,
                "deploy_ready": False,
                "summary": "missing repo id should be skipped",
            }
        ),
        encoding="utf-8",
    )
    second_path.write_text(
        json.dumps(
            {
                "repo_id": "zetherion-ai",
                "merge_ready": True,
                "deploy_ready": True,
                "summary": "fallback receipt",
            }
        ),
        encoding="utf-8",
    )

    receipt, payload = _load_local_repo_readiness(
        {
            "repo_id": "",
            "allowed_paths": [str(repo_root)],
        }
    )

    assert receipt is not None
    assert payload is not None
    assert receipt.repo_id == "zetherion-ai"
    assert receipt.summary == "fallback receipt"


def test_load_local_worker_certification_skips_invalid_candidates_before_valid_receipt(
    tmp_path,
) -> None:
    repo_root = tmp_path / "zetherion-ai"
    invalid_path = repo_root / ".artifacts" / "worker-certification-receipt.json"
    nondict_path = repo_root / ".artifacts" / "ci-worker-connectivity.json"
    valid_path = repo_root / ".ci" / "worker-certification-receipt.json"
    invalid_path.parent.mkdir(parents=True)
    valid_path.parent.mkdir(parents=True)
    invalid_path.write_text("{invalid", encoding="utf-8")
    nondict_path.write_text('["skip"]', encoding="utf-8")
    valid_path.write_text(
        json.dumps(
            {
                "status": "healthy",
                "execution_backend": "wsl_docker",
                "docker_backend": "wsl_docker",
                "bootstrap_succeeded": True,
                "registration_succeeded": True,
                "heartbeat_succeeded": True,
            }
        ),
        encoding="utf-8",
    )

    receipt, payload = _load_local_worker_certification(
        {
            "repo_id": "zetherion-ai",
            "allowed_paths": [str(repo_root)],
        }
    )

    assert receipt is not None
    assert payload is not None
    assert receipt["status"] == "healthy"
    assert payload["execution_backend"] == "wsl_docker"


def test_load_local_worker_certification_continues_past_nondict_json_payloads(tmp_path) -> None:
    repo_root = tmp_path / "zetherion-ai"
    first_path = repo_root / ".artifacts" / "worker-certification-receipt.json"
    second_path = repo_root / ".ci" / "ci-worker-connectivity.json"
    first_path.parent.mkdir(parents=True)
    second_path.parent.mkdir(parents=True)
    first_path.write_text('"skip-me"', encoding="utf-8")
    second_path.write_text(
        json.dumps(
            {
                "status": "healthy",
                "execution_backend": "docker",
                "docker_backend": "docker",
                "bootstrap_succeeded": True,
                "registration_succeeded": True,
                "heartbeat_succeeded": True,
            }
        ),
        encoding="utf-8",
    )

    receipt, payload = _load_local_worker_certification(
        {
            "repo_id": "zetherion-ai",
            "allowed_paths": ["", str(repo_root)],
        }
    )

    assert receipt is not None
    assert payload is not None
    assert receipt["status"] == "healthy"
    assert payload["execution_backend"] == "docker"


@pytest.mark.asyncio
async def test_get_reporting_readiness_uses_local_receipt_when_no_run_exists(tmp_path) -> None:
    repo_root = tmp_path / "zetherion-ai"
    receipt_path = repo_root / ".artifacts" / "local-readiness-receipt.json"
    worker_receipt_path = repo_root / ".artifacts" / "worker-certification-receipt.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "repo_id": "zetherion-ai",
                "merge_ready": True,
                "deploy_ready": True,
                "failed_required_paths": [],
                "missing_evidence": [],
                "summary": "ready",
                "recorded_at": "2026-03-13T08:00:00Z",
                "metadata": {"git_sha": "abc123"},
                "release_verification": {
                    "status": "healthy",
                    "delivery_canary_passed": True,
                    "queue_worker_healthy": True,
                },
            }
        ),
        encoding="utf-8",
    )
    worker_receipt_path.write_text(
        json.dumps(
            {
                "status": "pending",
                "execution_backend": "wsl_docker",
                "docker_backend": "wsl_docker",
                "wsl_distribution": "Ubuntu",
                "workspace_root": r"C:\ZetherionCI\workspaces",
                "runtime_root": r"C:\ZetherionCI\agent-runtime",
                "bootstrap_succeeded": True,
                "registration_succeeded": True,
                "heartbeat_succeeded": True,
            }
        ),
        encoding="utf-8",
    )

    storage = OwnerCiStorage.__new__(OwnerCiStorage)
    storage.list_repo_profiles = AsyncMock(
        return_value=[
            {
                "repo_id": "zetherion-ai",
                "display_name": "Zetherion AI",
                "active": True,
                "stack_kind": "python",
                "metadata": {},
                "allowed_paths": [str(repo_root)],
            }
        ]
    )
    storage.list_runs = AsyncMock(return_value=[])
    storage.get_run = AsyncMock(return_value=None)

    readiness = await OwnerCiStorage.get_reporting_readiness(storage, "owner-1")

    assert readiness["workspace_readiness"]["merge_ready"] is True
    assert readiness["workspace_readiness"]["deploy_ready"] is True
    assert readiness["repo_readiness"][0]["receipt_source"] == "local_file"
    assert readiness["worker_certification_source"] == "local_file"
    assert readiness["worker_certification"]["execution_backend"] == "wsl_docker"
    assert readiness["worker_certification"]["wsl_distribution"] == "Ubuntu"
    assert readiness["worker_certification"]["bootstrap_succeeded"] is True


@pytest.mark.asyncio
async def test_get_reporting_readiness_builds_owner_ci_run_receipts_and_unknown_repo_entries(
) -> None:
    storage = OwnerCiStorage.__new__(OwnerCiStorage)
    storage.list_repo_profiles = AsyncMock(
        return_value=[
            {
                "repo_id": "zetherion-ai",
                "display_name": "Zetherion AI",
                "active": True,
                "stack_kind": "python",
                "metadata": {"platform_canary": True},
                "allowed_paths": [],
            }
        ]
    )
    storage.list_runs = AsyncMock(
        return_value=[
            {"repo_id": "zetherion-ai", "run_id": "run-1"},
            {"repo_id": "catalyst-group-solutions", "run_id": "run-2"},
        ]
    )
    storage.get_run = AsyncMock(
        side_effect=[
            {
                "run_id": "run-1",
                "repo_id": "zetherion-ai",
                "status": "ready_to_merge",
                "git_ref": "main",
                "review_receipts": {"merge_blocked": False},
                "metadata": {
                    "release_verification": {
                        "status": "healthy",
                        "delivery_canary_passed": True,
                        "queue_worker_healthy": True,
                    }
                },
                "shards": [
                    {
                        "lane_id": "z-e2e-discord-sim",
                        "shard_id": "z-e2e-discord-sim#1",
                        "status": "succeeded",
                        "execution_target": "local_mac",
                        "artifact_contract": {"expects": ["stdout"]},
                        "result": {"evidence_paths": ["/tmp/discord.txt"]},
                        "metadata": {
                            "covered_required_paths": ["discord_dm_reply_path"],
                            "resource_class": "cpu",
                        },
                    }
                ],
                "created_at": "2026-03-13T08:00:00Z",
                "updated_at": "2026-03-13T08:05:00Z",
            },
            {
                "run_id": "run-2",
                "repo_id": "catalyst-group-solutions",
                "status": "failed",
                "git_ref": "feature/auth-fix",
                "review_receipts": {"merge_blocked": True},
                "metadata": {
                    "release_verification": {
                        "status": "deployed_but_unhealthy",
                        "cgs_auth_flow_passed": False,
                        "ai_ops_schema_passed": False,
                        "missing_evidence": ["playwright-report"],
                    }
                },
                "shards": [
                    {
                        "lane_id": "c-e2e-browser",
                        "shard_id": "c-e2e-browser#1",
                        "status": "failed",
                        "execution_target": "local_mac",
                        "artifact_contract": {"expects": ["playwright-report"]},
                        "result": {
                            "typed_incidents": ["auth_completion_failed"],
                        },
                        "metadata": {
                            "covered_required_paths": ["cgs_auth_flow_passed"],
                            "resource_class": "serial",
                        },
                    }
                ],
                "created_at": "2026-03-13T09:00:00Z",
                "updated_at": "2026-03-13T09:05:00Z",
            },
        ]
    )

    readiness = await OwnerCiStorage.get_reporting_readiness(storage, "owner-1")

    assert readiness["workspace_readiness"]["merge_ready"] is False
    assert readiness["workspace_readiness"]["deploy_ready"] is False
    assert [entry["repo_id"] for entry in readiness["repo_readiness"]] == [
        "zetherion-ai",
        "catalyst-group-solutions",
    ]
    assert readiness["repo_readiness"][0]["receipt_source"] == "owner_ci_run"
    assert readiness["repo_readiness"][0]["readiness"]["merge_ready"] is True
    assert readiness["repo_readiness"][1]["display_name"] == "catalyst-group-solutions"
    assert readiness["repo_readiness"][1]["readiness"]["failed_required_paths"] == [
        "cgs_auth_flow_passed"
    ]


@pytest.mark.asyncio
async def test_get_reporting_readiness_skips_invalid_entries_and_marks_pending_repo() -> None:
    storage = OwnerCiStorage.__new__(OwnerCiStorage)
    storage.list_repo_profiles = AsyncMock(
        return_value=[
            {
                "repo_id": "repo-pending",
                "display_name": "Pending Repo",
                "active": True,
                "stack_kind": "python",
                "metadata": {},
                "allowed_paths": [],
            },
            {
                "repo_id": "   ",
                "display_name": "Skip Me",
                "active": True,
                "stack_kind": "python",
                "metadata": {},
                "allowed_paths": [],
            },
        ]
    )
    storage.list_runs = AsyncMock(
        return_value=[
            {"repo_id": "", "run_id": "skip-blank-repo"},
            {"repo_id": "repo-pending", "run_id": ""},
            {"repo_id": "repo-known", "run_id": "run-1"},
            {"repo_id": "repo-known", "run_id": "run-2"},
        ]
    )
    storage.get_run = AsyncMock(
        return_value={
            "run_id": "run-1",
            "repo_id": "repo-known",
            "status": "ready_to_merge",
            "git_ref": "main",
            "review_receipts": {"merge_blocked": False},
            "metadata": {
                "release_verification": {
                    "status": "healthy",
                }
            },
            "shards": [],
            "created_at": "2026-03-13T10:00:00Z",
            "updated_at": "2026-03-13T10:05:00Z",
        }
    )

    readiness = await OwnerCiStorage.get_reporting_readiness(storage, "owner-1")

    assert [entry["repo_id"] for entry in readiness["repo_readiness"]] == [
        "repo-pending",
        "repo-known",
    ]
    assert readiness["repo_readiness"][0]["receipt_source"] == "missing"
    assert readiness["repo_readiness"][0]["latest_run"] is None
    assert readiness["repo_readiness"][0]["readiness"]["missing_evidence"] == [
        "owner_ci_run_missing"
    ]
    assert readiness["repo_readiness"][0]["readiness"]["summary"] == (
        "No owner-CI run has produced readiness receipts yet."
    )
    assert readiness["repo_readiness"][1]["display_name"] == "repo-known"
    assert readiness["repo_readiness"][1]["receipt_source"] == "owner_ci_run"
    storage.get_run.assert_awaited_once_with("owner-1", "run-1")


@pytest.mark.asyncio
async def test_get_reporting_readiness_overlays_local_receipt_onto_latest_run(tmp_path) -> None:
    repo_root = tmp_path / "catalyst-group-solutions"
    receipt_path = repo_root / ".artifacts" / "local-readiness-receipt.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "repo_id": "catalyst-group-solutions",
                "merge_ready": True,
                "deploy_ready": True,
                "failed_required_paths": [],
                "missing_evidence": [],
                "summary": "ready",
                "shard_receipts": [
                    {
                        "lane_id": "c-release",
                        "shard_id": "c-release",
                        "status": "succeeded",
                        "execution_target": "local_mac",
                        "metadata": {
                            "covered_required_paths": [
                                "cgs_release_verification",
                                "owner_ci_receipts",
                            ]
                        },
                        "artifact_contract": {"expects": []},
                        "result": {
                            "evidence_paths": [".artifacts/local-readiness-receipt.json"]
                        },
                    }
                ],
                "release_verification": {
                    "status": "healthy",
                    "blocker_count": 0,
                    "cgs_release_verification": True,
                },
            }
        ),
        encoding="utf-8",
    )

    storage = OwnerCiStorage.__new__(OwnerCiStorage)
    storage.list_repo_profiles = AsyncMock(
        return_value=[
            {
                "repo_id": "catalyst-group-solutions",
                "display_name": "Catalyst Group Solutions",
                "active": True,
                "stack_kind": "nextjs",
                "metadata": {},
                "allowed_paths": [str(repo_root)],
            }
        ]
    )
    storage.list_runs = AsyncMock(
        return_value=[{"repo_id": "catalyst-group-solutions", "run_id": "run-1"}]
    )
    storage.get_run = AsyncMock(
        return_value={
            "run_id": "run-1",
            "repo_id": "catalyst-group-solutions",
            "status": "queued_local",
            "review_receipts": {"merge_blocked": False},
            "metadata": {
                "release_verification": {
                    "status": "healthy",
                    "blocker_count": 0,
                }
            },
            "shards": [
                {
                    "lane_id": "c-release",
                    "shard_id": "c-release",
                    "status": "planned",
                    "execution_target": "local_mac",
                    "artifact_contract": {"expects": []},
                    "result": {},
                    "metadata": {
                        "covered_required_paths": [
                            "cgs_release_verification",
                            "owner_ci_receipts",
                        ]
                    },
                }
            ],
        }
    )

    readiness = await OwnerCiStorage.get_reporting_readiness(storage, "owner-1")

    assert readiness["repo_readiness"][0]["readiness"]["merge_ready"] is True
    assert readiness["repo_readiness"][0]["readiness"]["deploy_ready"] is True
    assert readiness["repo_readiness"][0]["readiness"]["shard_receipts"][0]["status"] == "succeeded"


@pytest.mark.asyncio
async def test_get_reporting_readiness_can_overlay_aggregate_lane_from_multiple_local_receipts(
    tmp_path,
) -> None:
    repo_root = tmp_path / "catalyst-group-solutions"
    receipt_path = repo_root / ".artifacts" / "local-readiness-receipt.json"
    receipt_path.parent.mkdir(parents=True)
    receipt_path.write_text(
        json.dumps(
            {
                "repo_id": "catalyst-group-solutions",
                "merge_ready": True,
                "deploy_ready": True,
                "failed_required_paths": [],
                "missing_evidence": [],
                "summary": "ready",
                "shard_receipts": [
                    {
                        "lane_id": "c-unit-coverage",
                        "shard_id": "c-unit-coverage",
                        "status": "succeeded",
                        "execution_target": "local_mac",
                        "metadata": {
                            "covered_required_paths": ["cgs_release_verification"]
                        },
                        "artifact_contract": {"expects": []},
                        "result": {
                            "evidence_paths": [".artifacts/test-logs/c-unit-coverage.log"]
                        },
                    },
                    {
                        "lane_id": "c-int-owner-ci",
                        "shard_id": "c-int-owner-ci",
                        "status": "succeeded",
                        "execution_target": "local_mac",
                        "metadata": {
                            "covered_required_paths": ["owner_ci_receipts"]
                        },
                        "artifact_contract": {"expects": []},
                        "result": {
                            "evidence_paths": [".artifacts/test-logs/c-int-owner-ci.log"]
                        },
                    },
                ],
                "release_verification": {
                    "status": "healthy",
                    "blocker_count": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    storage = OwnerCiStorage.__new__(OwnerCiStorage)
    storage.list_repo_profiles = AsyncMock(
        return_value=[
            {
                "repo_id": "catalyst-group-solutions",
                "display_name": "Catalyst Group Solutions",
                "active": True,
                "stack_kind": "nextjs",
                "metadata": {},
                "allowed_paths": [str(repo_root)],
            }
        ]
    )
    storage.list_runs = AsyncMock(
        return_value=[{"repo_id": "catalyst-group-solutions", "run_id": "run-1"}]
    )
    storage.get_run = AsyncMock(
        return_value={
            "run_id": "run-1",
            "repo_id": "catalyst-group-solutions",
            "status": "queued_local",
            "review_receipts": {"merge_blocked": False},
            "metadata": {
                "release_verification": {
                    "status": "healthy",
                    "blocker_count": 0,
                }
            },
            "shards": [
                {
                    "lane_id": "c-release",
                    "shard_id": "c-release",
                    "status": "planned",
                    "execution_target": "local_mac",
                    "artifact_contract": {"expects": []},
                    "result": {},
                    "metadata": {
                        "covered_required_paths": [
                            "cgs_release_verification",
                            "owner_ci_receipts",
                        ]
                    },
                }
            ],
        }
    )

    readiness = await OwnerCiStorage.get_reporting_readiness(storage, "owner-1")

    shard = readiness["repo_readiness"][0]["readiness"]["shard_receipts"][0]
    assert shard["status"] == "succeeded"
    assert shard["metadata"]["local_receipt_lane_ids"] == [
        "c-unit-coverage",
        "c-int-owner-ci",
    ]


@pytest.mark.asyncio
async def test_get_reporting_readiness_uses_embedded_local_receipt_from_latest_run() -> None:
    storage = OwnerCiStorage.__new__(OwnerCiStorage)
    storage.list_repo_profiles = AsyncMock(
        return_value=[
            {
                "repo_id": "catalyst-group-solutions",
                "display_name": "Catalyst Group Solutions",
                "active": True,
                "stack_kind": "nextjs",
                "metadata": {},
                "allowed_paths": [],
            }
        ]
    )
    storage.list_runs = AsyncMock(
        return_value=[{"repo_id": "catalyst-group-solutions", "run_id": "run-1"}]
    )
    storage.get_run = AsyncMock(
        return_value={
            "run_id": "run-1",
            "repo_id": "catalyst-group-solutions",
            "status": "review_pending",
            "review_receipts": {"merge_blocked": False},
            "metadata": {},
            "shards": [
                {
                    "lane_id": "golive-gate",
                    "shard_id": "golive-gate",
                    "status": "succeeded",
                    "execution_target": "windows_local",
                    "artifact_contract": {"expects": []},
                    "metadata": {
                        "covered_required_paths": ["cgs_release_verification"]
                    },
                    "result": {
                        "local_readiness_receipt": {
                            "repo_id": "catalyst-group-solutions",
                            "merge_ready": True,
                            "deploy_ready": True,
                            "failed_required_paths": [],
                            "missing_evidence": [],
                            "summary": "embedded readiness",
                            "shard_receipts": [
                                {
                                    "lane_id": "golive-gate",
                                    "shard_id": "golive-gate",
                                    "status": "succeeded",
                                    "execution_target": "windows_local",
                                    "metadata": {
                                        "covered_required_paths": [
                                            "cgs_release_verification"
                                        ]
                                    },
                                    "artifact_contract": {"expects": []},
                                    "result": {
                                        "evidence_paths": [
                                            ".artifacts/local-readiness-receipt.json"
                                        ]
                                    },
                                }
                            ],
                            "release_verification": {
                                "status": "healthy",
                                "blocker_count": 0,
                                "cgs_release_verification": True,
                            },
                        }
                    },
                    "error": {},
                }
            ],
        }
    )

    readiness = await OwnerCiStorage.get_reporting_readiness(storage, "owner-1")

    repo_readiness = readiness["repo_readiness"][0]["readiness"]
    assert repo_readiness["deploy_ready"] is True
    assert repo_readiness["release_verification"]["status"] == "healthy"


@pytest.mark.asyncio
async def test_get_reporting_summary_aggregates_repo_gap_operation_and_incident_counts() -> None:
    storage = OwnerCiStorage.__new__(OwnerCiStorage)
    storage.list_repo_profiles = AsyncMock(
        return_value=[
            {
                "repo_id": "zetherion-ai",
                "display_name": "Zetherion AI",
                "active": True,
                "stack_kind": "python",
                "metadata": {"platform_canary": True},
            }
        ]
    )
    storage.list_runs = AsyncMock(
        return_value=[
            {"repo_id": "zetherion-ai", "status": "failed"},
            {"repo_id": "catalyst-group-solutions", "status": "ready_to_merge"},
        ]
    )
    storage.list_agent_gap_events = AsyncMock(
        return_value=[
            {
                "repo_id": "zetherion-ai",
                "blocker": True,
                "occurrence_count": 2,
            },
            {
                "repo_id": "catalyst-group-solutions",
                "blocker": False,
                "occurrence_count": 1,
            },
            {
                "repo_id": "",
                "blocker": True,
                "occurrence_count": 5,
            },
        ]
    )
    storage.list_managed_operations = AsyncMock(
        return_value=[
            {
                "operation_id": "op-1",
                "repo_id": "zetherion-ai",
                "status": "failed",
            },
            {
                "operation_id": "op-2",
                "repo_id": "catalyst-group-solutions",
                "status": "succeeded",
            },
        ]
    )
    storage.list_operation_incidents_for_owner = AsyncMock(
        return_value=[
            {"operation_id": "op-1", "blocking": True},
            {"operation_id": "op-2", "blocking": False},
            {"operation_id": "missing-op", "blocking": True},
        ]
    )

    summary = await OwnerCiStorage.get_reporting_summary(storage, "owner-1")

    repo_summaries = {entry["repo_id"]: entry for entry in summary["repos"]}
    assert summary["owner_id"] == "owner-1"
    assert summary["run_count"] == 2
    assert summary["gaps"] == {
        "open_total": 3,
        "blocker_total": 2,
        "recurring_total": 2,
    }
    assert summary["operations"] == {
        "total": 2,
        "failed_total": 1,
        "active_total": 1,
        "incident_total": 3,
        "blocking_incident_total": 2,
    }
    assert repo_summaries["zetherion-ai"]["failed_runs"] == 1
    assert repo_summaries["zetherion-ai"]["blocker_gaps"] == 1
    assert repo_summaries["zetherion-ai"]["failed_operations"] == 1
    assert repo_summaries["zetherion-ai"]["open_incidents"] == 1
    assert repo_summaries["catalyst-group-solutions"]["ready_to_merge_runs"] == 1
    assert repo_summaries["catalyst-group-solutions"]["open_gaps"] == 1
