"""Tests for typed owner-CI models and readiness aggregation."""

from __future__ import annotations

from zetherion_ai.owner_ci.models import (
    RepoReadinessReceipt,
    ShardReceipt,
    _as_bool,
    _bool_to_check_status,
    _check_passed,
    build_repo_readiness_receipt,
    build_workspace_readiness_receipt,
    normalize_release_verification_receipt,
    normalize_shard_receipt,
    normalize_worker_certification_receipt,
)


def test_normalize_release_verification_receipt_backfills_required_checks() -> None:
    receipt = normalize_release_verification_receipt(
        {
            "status": "healthy",
            "delivery_canary_passed": True,
            "queue_worker_healthy": True,
            "cgs_login_redirect_passed": True,
        }
    )

    assert receipt.status == "healthy"
    check_map = {check.key: check.status for check in receipt.required_checks}
    assert check_map["delivery_canary_passed"] == "passed"
    assert check_map["queue_worker_healthy"] == "passed"
    assert check_map["cgs_login_redirect_passed"] == "passed"
    assert check_map["security_canary_passed"] == "pending"


def test_build_repo_and_workspace_readiness_receipts_capture_failed_paths() -> None:
    repo_receipt = build_repo_readiness_receipt(
        repo={"repo_id": "zetherion-ai"},
        run={
            "repo_id": "zetherion-ai",
            "shards": [
                {
                    "shard_id": "shard-1",
                    "lane_id": "z-int-faults",
                    "status": "failed",
                    "metadata": {"covered_required_paths": ["queue_reliability"]},
                    "result": {},
                    "error": {},
                    "artifact_contract": {"expects": ["stdout"]},
                }
            ],
        },
        review={"merge_blocked": False},
        release_receipt={"status": "healthy", "blocker_count": 0},
    )
    workspace = build_workspace_readiness_receipt([repo_receipt])

    assert repo_receipt.merge_ready is False
    assert repo_receipt.failed_required_paths == ["queue_reliability"]
    assert workspace.merge_ready is False
    assert workspace.failed_required_paths == ["queue_reliability"]


def test_repo_and_workspace_readiness_receipts_track_required_categories_and_host_capacity() -> (
    None
):
    repo_receipt = build_repo_readiness_receipt(
        repo={"repo_id": "zetherion-ai"},
        run={
            "repo_id": "zetherion-ai",
            "metadata": {"mode": "certification"},
            "plan": {
                "required_gate_categories": ["static", "security", "unit"],
                "resource_budget": {"cpu": 8, "service": 2, "serial": 1},
                "host_capacity_policy": {
                    "host_id": "windows-owner-ci",
                    "reserve_runtime_headroom": True,
                },
            },
            "shards": [
                {
                    "lane_id": "ruff-check",
                    "status": "succeeded",
                    "metadata": {
                        "gate_family": "static",
                        "blocking": True,
                        "covered_required_paths": ["zetherion_repo_integrity"],
                    },
                    "result": {},
                    "error": {},
                    "artifact_contract": {},
                },
                {
                    "lane_id": "gitleaks",
                    "status": "succeeded",
                    "metadata": {
                        "gate_family": "security",
                        "blocking": True,
                        "covered_required_paths": ["zetherion_security_integrity"],
                    },
                    "result": {},
                    "error": {},
                    "artifact_contract": {},
                },
            ],
        },
        review={"merge_blocked": False},
        release_receipt={"status": "healthy", "blocker_count": 0},
    )

    workspace = build_workspace_readiness_receipt([repo_receipt])

    assert repo_receipt.category_complete == {
        "security": True,
        "static": True,
        "unit": False,
    }
    assert repo_receipt.missing_categories == ["unit"]
    assert repo_receipt.host_capacity_snapshot is not None
    assert repo_receipt.host_capacity_snapshot.cpu_slots_total == 8
    assert workspace.category_complete == {
        "security": True,
        "static": True,
        "unit": False,
    }
    assert workspace.missing_categories == ["unit"]
    assert workspace.host_capacity_snapshot is not None


def test_normalize_release_verification_receipt_derives_status_and_counts() -> None:
    receipt = normalize_release_verification_receipt(
        {
            "required_checks": [
                {"key": "delivery_canary_passed", "status": "passed"},
                {"key": "security_canary_passed", "status": "degraded"},
                {"key": "queue_worker_healthy", "status": "failed", "blocker": True},
            ],
            "missing_evidence": ["docker-compose.ps"],
        }
    )

    assert receipt.status == "deployed_but_unhealthy"
    assert receipt.blocker_count == 1
    assert receipt.degraded_count == 1
    assert receipt.delivery_canary_passed is True
    assert receipt.queue_worker_healthy is False
    assert receipt.missing_evidence == ["docker-compose.ps"]


def test_normalize_shard_receipt_backfills_missing_expected_artifacts() -> None:
    receipt = normalize_shard_receipt(
        "zetherion-ai",
        {
            "lane_id": "z-release",
            "status": "succeeded",
            "artifact_contract": {"expects": ["stdout.log", "receipt.json"]},
            "metadata": {
                "resource_class": "serial",
                "covered_required_paths": ["release_verification"],
                "service_slot": "slot_b",
                "release_blocking": False,
            },
            "result": {
                "typed_incidents": ["release_blocker"],
                "cleanup_receipt_path": ".artifacts/cleanup.json",
            },
            "error": {},
        },
    )

    assert receipt.repo_id == "zetherion-ai"
    assert receipt.shard_id == "z-release"
    assert receipt.required_paths == ["release_verification"]
    assert receipt.typed_incidents == ["release_blocker"]
    assert receipt.missing_evidence == ["stdout.log", "receipt.json"]
    assert receipt.cleanup_receipt_path == ".artifacts/cleanup.json"
    assert receipt.service_slot == "slot_b"
    assert receipt.release_blocking is False


def test_normalize_release_and_worker_receipts_cover_blank_keys_and_status_derivation() -> None:
    release_receipt = normalize_release_verification_receipt(
        {
            "required_checks": [
                {"key": "", "status": "passed"},
                {"key": "delivery_canary_passed", "status": "passed"},
            ],
            "delivery_canary_passed": True,
            "security_canary_passed": True,
            "queue_worker_healthy": True,
            "runtime_status_persistence": True,
            "skills_reachable": True,
            "cgs_auth_flow_passed": True,
            "cgs_login_redirect_passed": True,
            "ai_ops_schema_passed": True,
            "cgs_admin_ai_page_passed": True,
            "cgs_owner_ci_reporting_passed": True,
            "cgs_chatbot_runtime_proxy_passed": True,
            "runtime_drift_zero": True,
            "back_to_back_deploy_passed": True,
        }
    )
    assert release_receipt.status == "healthy"
    assert release_receipt.delivery_canary_passed is True

    worker_receipt = normalize_worker_certification_receipt(
        {
            "required_checks": [
                {"key": "", "status": "passed"},
                {"key": "bootstrap_succeeded", "status": "passed"},
                {"key": "status_publication_succeeded", "status": "degraded"},
            ],
        }
    )
    assert worker_receipt.status == "degraded"
    assert worker_receipt.bootstrap_succeeded is True
    assert worker_receipt.degraded_count == 1


def test_normalize_shard_receipt_merges_debug_artifacts_without_duplicates() -> None:
    receipt = normalize_shard_receipt(
        "zetherion-ai",
        {
            "lane_id": "z-int-runtime",
            "status": "failed",
            "metadata": {"resource_reservation": "skip"},
            "result": {
                "artifacts": [".artifacts/existing.log"],
                "debug_bundle": {
                    "artifact_receipt_paths": {
                        "existing": ".artifacts/existing.log",
                        "new": ".artifacts/new.json",
                    }
                },
            },
            "error": {},
            "artifact_contract": {"expects": ["stdout.log"]},
        },
    )

    assert receipt.evidence_paths == [
        ".artifacts/existing.log",
        ".artifacts/new.json",
    ]
    assert receipt.missing_evidence == []
    assert receipt.resource_reservation is None


def test_build_repo_readiness_receipt_uses_local_release_and_preserves_failed_local_paths() -> None:
    local_receipt = RepoReadinessReceipt(
        repo_id="catalyst-group-solutions",
        merge_ready=False,
        deploy_ready=False,
        failed_required_paths=["cgs_release_verification"],
        missing_evidence=[],
        shard_receipts=[
            ShardReceipt(
                repo_id="catalyst-group-solutions",
                lane_id="c-unit-coverage",
                shard_id="c-unit-coverage",
                status="failed",
                required_paths=["cgs_release_verification"],
                execution_target="local_mac",
            )
        ],
        release_verification=normalize_release_verification_receipt(
            {"status": "healthy", "blocker_count": 0}
        ),
        summary="local receipt failed",
    )

    repo_receipt = build_repo_readiness_receipt(
        repo={"repo_id": "catalyst-group-solutions"},
        run={
            "repo_id": "catalyst-group-solutions",
            "shards": [
                {
                    "lane_id": "c-release",
                    "status": "queued_local",
                    "execution_target": "local_mac",
                    "metadata": {"covered_required_paths": ["cgs_release_verification"]},
                    "result": {},
                    "error": {},
                    "artifact_contract": {},
                }
            ],
        },
        review={"merge_blocked": False},
        release_receipt={},
        local_receipt=local_receipt,
    )

    assert repo_receipt.release_verification.status == "healthy"
    assert repo_receipt.shard_receipts[0].status == "queued_local"
    assert repo_receipt.failed_required_paths == []


def test_build_repo_readiness_receipt_treats_pending_and_disconnected_shards_as_not_ready() -> None:
    repo_receipt = build_repo_readiness_receipt(
        repo={"repo_id": "catalyst-group-solutions"},
        run={
            "repo_id": "catalyst-group-solutions",
            "shards": [
                {
                    "lane_id": "c-release",
                    "status": "queued_local",
                    "metadata": {"covered_required_paths": ["release_verification"]},
                    "result": {},
                    "error": {},
                    "artifact_contract": {},
                }
            ],
        },
        review={"merge_blocked": False},
        release_receipt={"status": "healthy", "blocker_count": 0},
    )

    assert repo_receipt.merge_ready is False
    assert repo_receipt.deploy_ready is False
    assert "merge blocked" in repo_receipt.summary
    assert "deploy not ready" in repo_receipt.summary


def test_build_repo_readiness_receipt_uses_local_receipt_to_satisfy_pending_local_mac_shards() -> (
    None
):
    local_receipt = RepoReadinessReceipt(
        repo_id="catalyst-group-solutions",
        merge_ready=True,
        deploy_ready=True,
        failed_required_paths=[],
        missing_evidence=[],
        shard_receipts=[
            ShardReceipt(
                repo_id="catalyst-group-solutions",
                lane_id="c-release",
                shard_id="c-release",
                status="succeeded",
                required_paths=["cgs_release_verification", "owner_ci_receipts"],
                evidence_paths=[".artifacts/local-readiness-receipt.json"],
                execution_target="local_mac",
            )
        ],
        release_verification=normalize_release_verification_receipt(
            {"status": "healthy", "blocker_count": 0}
        ),
        summary="local receipt ready",
    )

    repo_receipt = build_repo_readiness_receipt(
        repo={"repo_id": "catalyst-group-solutions"},
        run={
            "repo_id": "catalyst-group-solutions",
            "shards": [
                {
                    "lane_id": "c-release",
                    "status": "queued_local",
                    "execution_target": "local_mac",
                    "metadata": {
                        "covered_required_paths": [
                            "cgs_release_verification",
                            "owner_ci_receipts",
                        ]
                    },
                    "result": {},
                    "error": {},
                    "artifact_contract": {},
                }
            ],
        },
        review={"merge_blocked": False},
        release_receipt={"status": "healthy", "blocker_count": 0},
        local_receipt=local_receipt,
    )

    assert repo_receipt.merge_ready is True
    assert repo_receipt.deploy_ready is True
    assert repo_receipt.shard_receipts[0].status == "succeeded"
    assert repo_receipt.shard_receipts[0].metadata["satisfied_by"] == "local_readiness_receipt"


def test_build_repo_readiness_receipt_can_overlay_one_pending_lane_from_multiple_local_shards() -> (
    None
):
    local_receipt = RepoReadinessReceipt(
        repo_id="catalyst-group-solutions",
        merge_ready=True,
        deploy_ready=True,
        failed_required_paths=[],
        missing_evidence=[],
        shard_receipts=[
            ShardReceipt(
                repo_id="catalyst-group-solutions",
                lane_id="c-unit-coverage",
                shard_id="c-unit-coverage",
                status="succeeded",
                required_paths=["cgs_release_verification"],
                evidence_paths=[".artifacts/test-logs/c-unit-coverage.log"],
                execution_target="local_mac",
            ),
            ShardReceipt(
                repo_id="catalyst-group-solutions",
                lane_id="c-int-owner-ci",
                shard_id="c-int-owner-ci",
                status="succeeded",
                required_paths=["owner_ci_receipts"],
                evidence_paths=[".artifacts/test-logs/c-int-owner-ci.log"],
                execution_target="local_mac",
            ),
        ],
        release_verification=normalize_release_verification_receipt(
            {"status": "healthy", "blocker_count": 0}
        ),
        summary="local receipt ready",
    )

    repo_receipt = build_repo_readiness_receipt(
        repo={"repo_id": "catalyst-group-solutions"},
        run={
            "repo_id": "catalyst-group-solutions",
            "shards": [
                {
                    "lane_id": "c-release",
                    "status": "queued_local",
                    "execution_target": "local_mac",
                    "metadata": {
                        "covered_required_paths": [
                            "cgs_release_verification",
                            "owner_ci_receipts",
                        ]
                    },
                    "result": {},
                    "error": {},
                    "artifact_contract": {},
                }
            ],
        },
        review={"merge_blocked": False},
        release_receipt={"status": "healthy", "blocker_count": 0},
        local_receipt=local_receipt,
    )

    assert repo_receipt.merge_ready is True
    assert repo_receipt.deploy_ready is True
    assert repo_receipt.shard_receipts[0].status == "succeeded"
    assert repo_receipt.shard_receipts[0].metadata["satisfied_by"] == "local_readiness_receipt"
    assert repo_receipt.shard_receipts[0].metadata["local_receipt_lane_ids"] == [
        "c-unit-coverage",
        "c-int-owner-ci",
    ]
    assert sorted(repo_receipt.shard_receipts[0].evidence_paths) == [
        ".artifacts/test-logs/c-int-owner-ci.log",
        ".artifacts/test-logs/c-unit-coverage.log",
    ]


def test_build_repo_readiness_receipt_uses_local_release_verification_when_run_missing() -> None:
    local_receipt = RepoReadinessReceipt(
        repo_id="catalyst-group-solutions",
        merge_ready=True,
        deploy_ready=True,
        failed_required_paths=[],
        missing_evidence=[],
        shard_receipts=[],
        release_verification=normalize_release_verification_receipt(
            {
                "status": "healthy",
                "blocker_count": 0,
                "cgs_release_verification": True,
            }
        ),
        summary="local receipt ready",
    )

    repo_receipt = build_repo_readiness_receipt(
        repo={"repo_id": "catalyst-group-solutions"},
        run={"repo_id": "catalyst-group-solutions", "shards": []},
        review={"merge_blocked": False},
        release_receipt={},
        local_receipt=local_receipt,
    )

    assert repo_receipt.deploy_ready is True
    assert repo_receipt.release_verification is not None
    assert repo_receipt.release_verification.status == "healthy"


def test_build_workspace_readiness_receipt_reports_ready_summary() -> None:
    repo_receipt = build_repo_readiness_receipt(
        repo={"repo_id": "zetherion-ai"},
        run={"repo_id": "zetherion-ai", "shards": []},
        review={"merge_blocked": False},
        release_receipt={
            "status": "healthy",
            "blocker_count": 0,
            "required_checks": [{"key": "delivery_canary_passed", "status": "passed"}],
        },
    )

    workspace = build_workspace_readiness_receipt([repo_receipt])

    assert workspace.merge_ready is True
    assert workspace.deploy_ready is True
    assert workspace.summary == "ready"


def test_private_release_helper_functions_cover_bool_and_status_normalization() -> None:
    assert _as_bool(True) is True
    assert _as_bool(0) is False
    assert _as_bool("green") is True
    assert _as_bool("blocked") is False
    assert _as_bool("maybe") is None
    assert _as_bool("") is None

    assert _bool_to_check_status(True) == "passed"
    assert _bool_to_check_status(False) == "failed"
    assert _bool_to_check_status(None) == "pending"

    assert _check_passed("success") is True
    assert _check_passed("failed") is False
    assert _check_passed("queued") is None


def test_normalize_release_verification_receipt_handles_blank_keys_and_degraded_status() -> None:
    receipt = normalize_release_verification_receipt(
        {
            "checks": [
                {"name": "", "status": "failed"},
                {
                    "name": "delivery_canary_passed",
                    "state": "passed",
                    "evidence": [" receipt.json "],
                },
                {
                    "name": "security_canary_passed",
                    "state": "degraded",
                    "description": "manual review pending",
                },
            ]
        }
    )

    assert receipt.status == "degraded"
    assert receipt.degraded_count == 1
    assert receipt.delivery_canary_passed is True
    security_check = next(
        check for check in receipt.required_checks if check.key == "security_canary_passed"
    )
    assert security_check.summary == "manual review pending"
    assert next(
        check for check in receipt.required_checks if check.key == "delivery_canary_passed"
    ).evidence_paths == ["receipt.json"]


def test_normalize_release_verification_receipt_defaults_to_healthy_when_checks_pass() -> None:
    receipt = normalize_release_verification_receipt(
        {
            "delivery_canary_passed": True,
            "security_canary_passed": True,
            "queue_worker_healthy": True,
            "runtime_status_persistence": True,
            "skills_reachable": True,
            "cgs_auth_flow_passed": True,
            "cgs_login_redirect_passed": True,
            "ai_ops_schema_passed": True,
            "cgs_admin_ai_page_passed": True,
            "cgs_owner_ci_reporting_passed": True,
            "cgs_chatbot_runtime_proxy_passed": True,
            "runtime_drift_zero": True,
            "back_to_back_deploy_passed": True,
        }
    )

    assert receipt.status == "healthy"
    assert receipt.blocker_count == 0
    assert receipt.degraded_count == 0
    assert all(check.status == "passed" for check in receipt.required_checks)


def test_normalize_worker_certification_receipt_backfills_required_checks() -> None:
    receipt = normalize_worker_certification_receipt(
        {
            "execution_backend": "wsl_docker",
            "docker_backend": "wsl_docker",
            "wsl_distribution": "Ubuntu",
            "workspace_root": r"C:\ZetherionCI\workspaces",
            "runtime_root": r"C:\ZetherionCI\agent-runtime",
            "bootstrap_succeeded": True,
            "heartbeat_succeeded": True,
            "status_publication_succeeded": False,
        }
    )

    assert receipt.status == "failed"
    assert receipt.execution_backend == "wsl_docker"
    assert receipt.docker_backend == "wsl_docker"
    assert receipt.wsl_distribution == "Ubuntu"
    assert receipt.bootstrap_succeeded is True
    assert receipt.heartbeat_succeeded is True
    assert receipt.status_publication_succeeded is False
    check_map = {check.key: check.status for check in receipt.required_checks}
    assert check_map["bootstrap_succeeded"] == "passed"
    assert check_map["registration_succeeded"] == "pending"
    assert check_map["status_publication_succeeded"] == "failed"


def test_normalize_worker_certification_receipt_derives_healthy_status_when_all_checks_pass() -> (
    None
):
    receipt = normalize_worker_certification_receipt(
        {
            "checks": [
                {"name": "bootstrap_succeeded", "state": "passed"},
                {"name": "registration_succeeded", "state": "passed"},
                {"name": "heartbeat_succeeded", "state": "passed"},
                {"name": "job_claim_succeeded", "state": "passed"},
                {"name": "noop_job_succeeded", "state": "passed"},
                {"name": "ci_test_run_succeeded", "state": "passed"},
                {"name": "artifacts_submitted", "state": "passed"},
                {"name": "cleanup_verified", "state": "passed"},
                {"name": "status_publication_succeeded", "state": "passed"},
            ]
        }
    )

    assert receipt.status == "healthy"
    assert receipt.blocker_count == 0
    assert receipt.degraded_count == 0
    assert all(check.status == "passed" for check in receipt.required_checks)
