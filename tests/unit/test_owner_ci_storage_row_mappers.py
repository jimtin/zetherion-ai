"""Coverage for owner-CI row mappers and serializer helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from zetherion_ai.owner_ci.storage import (
    OwnerCiStorage,
    _fair_claim_order,
    _filter_run_report_for_node,
    _job_host_capacity_policy,
    _job_host_id,
    _job_resource_reservation,
    _load_local_repo_readiness_from_shards,
    _merge_json_dict,
    _normalize_local_repo_readiness_payload,
    _pending_repo_readiness,
    _stable_feedback_key,
    _validate_schema_identifier,
)


class _Encryptor:
    def encrypt_value(self, value: str) -> str:
        return f"enc::{value}"

    def decrypt_value(self, value: str) -> str:
        if value == "bad-value":
            raise ValueError("cannot decrypt")
        return value.removeprefix("enc::")


def _storage(*, encrypted: bool = False) -> OwnerCiStorage:
    storage = OwnerCiStorage.__new__(OwnerCiStorage)
    storage._encryptor = _Encryptor() if encrypted else None
    return storage


def test_owner_ci_storage_encrypt_decrypt_and_core_row_mappers() -> None:
    storage = _storage(encrypted=True)
    now = datetime(2026, 3, 13, 6, 0, tzinfo=UTC)

    assert storage._encrypt_text(None) is None  # noqa: SLF001
    assert storage._encrypt_text("secret") == "enc::secret"  # noqa: SLF001
    assert storage._decrypt_text(None) is None  # noqa: SLF001
    assert storage._decrypt_text("enc::secret") == "secret"  # noqa: SLF001
    assert storage._decrypt_text("bad-value") == "bad-value"  # noqa: SLF001
    assert OwnerCiStorage._repo_profile_extensions(  # noqa: SLF001
        {"windows_execution_mode": "docker_only", "scheduled_canaries": ["discord_dm"]}
    ) == {
        "mandatory_static_gates": [],
        "mandatory_security_gates": [],
        "shard_templates": [],
        "scheduling_policy": {},
        "resource_classes": {},
        "windows_execution_mode": "docker_only",
        "certification_requirements": [],
        "scheduled_canaries": ["discord_dm"],
        "debug_policy": {},
        "agent_bootstrap_profile": {},
    }

    repo_profile = storage._repo_profile_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "repo_id": "zetherion-ai",
            "display_name_value": "enc::Zetherion AI",
            "github_repo": "jimtin/zetherion-ai",
            "default_branch": "main",
            "stack_kind": "python",
            "metadata_json": {"windows_execution_mode": "docker_only"},
            "local_fast_lanes": ["ruff-check"],
            "local_full_lanes": ["z-unit-core"],
            "windows_full_lanes": ["windows-e2e"],
            "review_policy": {"required_reviews": 1},
            "promotion_policy": {"target": "production"},
            "allowed_paths": ["/repo"],
            "secrets_profile": "prod",
            "active": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    assert repo_profile["display_name"] == "Zetherion AI"
    assert repo_profile["windows_execution_mode"] == "docker_only"
    assert repo_profile["local_full_lanes"] == ["z-unit-core"]

    plan_snapshot = storage._plan_snapshot_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "plan_id": "plan-1",
            "repo_id": "zetherion-ai",
            "version": 3,
            "title_value": "enc::Repair plan",
            "content_markdown_value": "enc::# Plan",
            "tags_json": ["repair"],
            "current_version": True,
            "metadata_json": {"phase": 1},
            "created_at": now,
        }
    )
    assert plan_snapshot["title"] == "Repair plan"
    assert plan_snapshot["content_markdown"] == "# Plan"

    run = storage._run_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "run_id": "run-1",
            "repo_id": "zetherion-ai",
            "git_ref": "abc123",
            "trigger_value": "push",
            "status": "running",
            "plan_json": {"lanes": []},
            "review_receipts": {"merge_blocked": False},
            "github_receipts": {"status": "pending"},
            "metadata_json": {"git_sha": "abc123"},
            "created_at": now,
            "updated_at": now,
        }
    )
    assert run["trigger"] == "push"
    assert run["metadata"]["git_sha"] == "abc123"

    shard = storage._shard_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "run_id": "run-1",
            "shard_id": "shard-1",
            "repo_id": "zetherion-ai",
            "lane_id": "z-unit-core",
            "lane_label_value": "enc::Unit core",
            "execution_target": "local_mac",
            "command_json": ["pytest"],
            "env_refs_json": ["env://discord"],
            "artifact_contract": {"expects": ["stdout.log"]},
            "required_capabilities": ["docker"],
            "relay_mode": "direct",
            "metadata_json": {"resource_class": "cpu"},
            "status": "succeeded",
            "result_json": {"duration_seconds": 12.5},
            "error_json": {},
            "started_at": now,
            "completed_at": now,
            "created_at": now,
            "updated_at": now,
        }
    )
    assert shard["lane_label"] == "Unit core"
    assert shard["metadata"]["resource_class"] == "cpu"

    worker_job = storage._worker_job_from_row(  # noqa: SLF001
        {
            "scope_id": "owner:owner-1",
            "job_id": "job-1",
            "owner_id": "owner-1",
            "run_id": "run-1",
            "shard_id": "shard-1",
            "repo_id": "zetherion-ai",
            "action_name": "ci.test.run",
            "runner_name": "docker",
            "payload_json": {"lane_id": "z-unit-core"},
            "required_capabilities": ["docker"],
            "artifact_contract": {"expects": ["receipt.json"]},
            "status": "queued",
            "idempotency_key": "idem-1",
            "execution_target": "windows_local",
            "claimed_by_node_id": None,
            "claimed_session_id": None,
            "result_json": {},
            "error_json": {},
            "created_at": now,
            "updated_at": now,
            "submitted_at": now,
        }
    )
    assert worker_job["execution_target"] == "windows_local"
    assert worker_job["claimed_by_node_id"] is None

    compiled_plan = storage._compiled_plan_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "compiled_plan_id": "compiled-1",
            "repo_id": "zetherion-ai",
            "git_ref": "main",
            "mode_value": "certification",
            "plan_json": {"shards": []},
            "metadata_json": {"source": "local"},
            "created_at": now,
            "updated_at": now,
        }
    )
    assert compiled_plan["mode"] == "certification"
    assert compiled_plan["metadata"]["source"] == "local"

    schedule = storage._schedule_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "schedule_id": "sched-1",
            "repo_id": "zetherion-ai",
            "name_value": "enc::Nightly",
            "schedule_kind": "weekly",
            "schedule_spec_json": {"weekday": "fri"},
            "active": True,
            "metadata_json": {"canary": True},
            "created_at": now,
            "updated_at": now,
        }
    )
    assert schedule["name"] == "Nightly"
    assert schedule["schedule_spec"]["weekday"] == "fri"

    event = storage._event_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "event_id": "evt-1",
            "repo_id": "zetherion-ai",
            "run_id": "run-1",
            "shard_id": "shard-1",
            "node_id": "node-1",
            "event_type": "worker_started",
            "level_value": "info",
            "payload_json": {"ok": True},
            "created_at": now,
        }
    )
    assert event["node_id"] == "node-1"
    assert event["payload"]["ok"] is True

    log_chunk = storage._log_chunk_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "chunk_id": "chunk-1",
            "repo_id": "zetherion-ai",
            "run_id": "run-1",
            "shard_id": None,
            "node_id": None,
            "stream_name": "stdout",
            "message_value": "enc::hello",
            "metadata_json": {"offset": 1},
            "created_at": now,
        }
    )
    assert log_chunk["message"] == "hello"
    assert log_chunk["stream"] == "stdout"

    resource_sample = storage._resource_sample_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "sample_id": "sample-1",
            "repo_id": "zetherion-ai",
            "run_id": None,
            "shard_id": None,
            "node_id": "node-1",
            "sample_json": {"cpu": 65},
            "created_at": now,
        }
    )
    assert resource_sample["sample"]["cpu"] == 65

    debug_bundle = storage._debug_bundle_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "bundle_id": "bundle-1",
            "repo_id": "zetherion-ai",
            "run_id": "run-1",
            "shard_id": "shard-1",
            "bundle_json": {"logs": []},
            "created_at": now,
            "updated_at": now,
        }
    )
    assert debug_bundle["bundle"]["logs"] == []


def test_owner_ci_storage_agent_and_bundle_row_mappers() -> None:
    storage = _storage(encrypted=True)
    now = datetime(2026, 3, 13, 6, 0, tzinfo=UTC)
    later = now + timedelta(minutes=15)

    principal = storage._agent_principal_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "principal_id": "codex-1",
            "display_name_value": "enc::Codex",
            "principal_type": "agent",
            "allowed_scopes_json": ["cgs:agent"],
            "metadata_json": {"team": "infra"},
            "active": True,
            "created_at": now,
            "updated_at": later,
        }
    )
    assert principal["display_name"] == "Codex"
    assert principal["allowed_scopes"] == ["cgs:agent"]

    connector = storage._external_connector_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "connector_id": "github-primary",
            "service_kind": "github",
            "display_name_value": "enc::GitHub",
            "auth_kind": "token",
            "policy_json": {"mode": "brokered"},
            "metadata_json": {"tenant": "owner-1"},
            "active": True,
            "secret_value": "present",
            "created_at": now,
            "updated_at": later,
        }
    )
    assert connector["display_name"] == "GitHub"
    assert connector["has_secret"] is True

    grant = storage._external_access_grant_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "principal_id": "codex-1",
            "grant_key": "grant-1",
            "resource_type": "app",
            "resource_id": "zetherion-ai",
            "capabilities_json": ["read"],
            "metadata_json": {"scope": "repo"},
            "active": True,
            "created_at": now,
            "updated_at": later,
        }
    )
    assert grant["capabilities"] == ["read"]

    app_profile = storage._agent_app_profile_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "app_id": "zetherion-ai",
            "display_name_value": "enc::Zetherion AI",
            "profile_json": {"repo_ids": ["zetherion-ai"]},
            "active": True,
            "created_at": now,
            "updated_at": later,
        }
    )
    assert app_profile["profile"]["repo_ids"] == ["zetherion-ai"]

    knowledge_pack = storage._agent_knowledge_pack_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "app_id": "zetherion-ai",
            "version_value": "v1",
            "pack_json": {"docs": ["quickstart"]},
            "current_version": True,
            "created_at": now,
            "updated_at": later,
        }
    )
    assert knowledge_pack["pack"]["docs"] == ["quickstart"]

    workspace_bundle = storage._workspace_bundle_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "bundle_id": "bundle-1",
            "principal_id": "codex-1",
            "app_id": "zetherion-ai",
            "repo_id": "zetherion-ai",
            "git_ref": "main",
            "resolved_ref": "abc123",
            "bundle_json": {"archive": "inline"},
            "expires_at": later,
            "created_at": now,
            "updated_at": later,
            "downloaded_at": None,
        }
    )
    assert workspace_bundle["resolved_ref"] == "abc123"
    assert workspace_bundle["bundle"]["archive"] == "inline"

    publish_candidate = storage._publish_candidate_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "candidate_id": "cand-1",
            "principal_id": "codex-1",
            "app_id": "zetherion-ai",
            "repo_id": "zetherion-ai",
            "base_sha": "abc123",
            "status": "submitted",
            "candidate_json": {"diff": "patch"},
            "review_json": {"approved": False},
            "created_at": now,
            "updated_at": later,
        }
    )
    assert publish_candidate["candidate"]["diff"] == "patch"

    secret_ref = storage._secret_ref_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "secret_ref_id": "secret-1",
            "connector_id": None,
            "purpose_value": "enc::api-key",
            "metadata_json": {"scope": "local"},
            "active": True,
            "secret_value": "present",
            "created_at": now,
            "updated_at": later,
        }
    )
    assert secret_ref["purpose"] == "api-key"
    assert secret_ref["has_secret"] is True

    audit_event = storage._agent_audit_event_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "audit_id": "audit-1",
            "principal_id": "codex-1",
            "app_id": "zetherion-ai",
            "service_kind": "github",
            "resource_value": "enc::repo",
            "action_value": "enc::read",
            "decision_value": "allowed",
            "audit_json": {"reason": "brokered"},
            "created_at": now,
        }
    )
    assert audit_event["resource"] == "repo"
    assert audit_event["action"] == "read"

    session = storage._agent_session_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "session_id": "sess-1",
            "principal_id": "codex-1",
            "app_id": "zetherion-ai",
            "session_status": "active",
            "metadata_json": {"source": "codex"},
            "created_at": now,
            "updated_at": later,
            "last_activity_at": later,
        }
    )
    assert session["status"] == "active"

    interaction = storage._agent_interaction_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "interaction_id": "int-1",
            "session_id": "sess-1",
            "principal_id": "codex-1",
            "app_id": "zetherion-ai",
            "repo_id": "zetherion-ai",
            "route_path_value": "enc::/admin/ai",
            "intent_value": "enc::ci_reporting_readiness",
            "request_text_value": "enc::show blockers",
            "request_json": {"intent": "ci_reporting_readiness"},
            "normalized_intent_json": {"owner_id": "owner-1"},
            "related_run_id": "run-1",
            "related_candidate_id": None,
            "related_service_request_id": None,
            "audit_id": "audit-1",
            "created_at": now,
        }
    )
    assert interaction["route_path"] == "/admin/ai"
    assert interaction["request_text"] == "show blockers"

    action = storage._agent_action_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "action_record_id": "action-1",
            "interaction_id": "int-1",
            "principal_id": "codex-1",
            "app_id": "zetherion-ai",
            "action_value": "enc::publish",
            "status": "requested",
            "payload_json": {"candidate_id": "cand-1"},
            "created_at": now,
            "updated_at": later,
        }
    )
    assert action["action"] == "publish"

    outcome = storage._agent_outcome_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "outcome_id": "outcome-1",
            "interaction_id": "int-1",
            "action_record_id": "action-1",
            "status": "succeeded",
            "summary_value": "enc::Ready",
            "payload_json": {"merge_ready": True},
            "created_at": now,
        }
    )
    assert outcome["summary"] == "Ready"


def test_owner_ci_storage_gap_service_request_and_operation_row_mappers() -> None:
    storage = _storage(encrypted=True)
    now = datetime(2026, 3, 13, 6, 0, tzinfo=UTC)
    later = now + timedelta(minutes=15)

    gap_event = storage._agent_gap_event_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "gap_id": "gap-1",
            "dedupe_key": "gap:1",
            "session_id": "sess-1",
            "principal_id": "codex-1",
            "app_id": "zetherion-ai",
            "repo_id": "zetherion-ai",
            "run_id": "run-1",
            "gap_type": "service_evidence_incomplete",
            "severity": "high",
            "blocker": True,
            "detected_from": "release_verification",
            "required_capability": "container_logs",
            "observed_request_json": {"service_kind": "docker"},
            "suggested_fix_value": "enc::add log capture",
            "status": "open",
            "metadata_json": {"evidence": "missing"},
            "first_seen_at": now,
            "last_seen_at": later,
            "occurrence_count": 3,
            "updated_at": later,
        }
    )
    assert gap_event["suggested_fix"] == "add log capture"
    assert gap_event["occurrence_count"] == 3

    service_request = storage._agent_service_request_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "request_id": "req-1",
            "principal_id": "codex-1",
            "app_id": "zetherion-ai",
            "service_kind": "stripe",
            "action_id": "product.ensure",
            "target_ref": None,
            "tenant_id": None,
            "change_reason_value": "enc::provision billing",
            "request_json": {"name": "Gold"},
            "status": "executed",
            "approved": True,
            "result_json": {"product_id": "prod_1"},
            "audit_id": "audit-1",
            "created_at": now,
            "updated_at": later,
            "executed_at": later,
        }
    )
    assert service_request["change_reason"] == "provision billing"
    assert service_request["approved"] is True

    capability = storage._service_adapter_capability_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "service_kind": "docker",
            "manifest_json": {"supports_logs": True},
            "created_at": now,
            "updated_at": later,
        }
    )
    assert capability["manifest"]["supports_logs"] is True

    operation = storage._managed_operation_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "operation_id": "op-1",
            "app_id": "zetherion-ai",
            "repo_id": "zetherion-ai",
            "operation_kind": "deploy",
            "lifecycle_stage": "verification",
            "status": "deployed_but_unhealthy",
            "correlation_key": "deploy:1",
            "summary_json": {"status": "red"},
            "metadata_json": {"source": "windows"},
            "created_at": now,
            "updated_at": later,
            "last_observed_at": later,
        }
    )
    assert operation["correlation_key"] == "deploy:1"
    assert operation["summary"]["status"] == "red"

    operation_ref = storage._operation_ref_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "ref_id": "ref-1",
            "operation_id": "op-1",
            "service_kind": "github",
            "ref_kind": "git_sha",
            "ref_value": "abc123",
            "dedupe_key": "github:sha",
            "metadata_json": {"repo": "zetherion-ai"},
            "created_at": now,
            "updated_at": later,
        }
    )
    assert operation_ref["ref_kind"] == "git_sha"

    evidence = storage._operation_evidence_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "evidence_id": "evidence-1",
            "operation_id": "op-1",
            "service_kind": "docker",
            "evidence_type": "container_logs",
            "title_value": "enc::Compose logs",
            "state": "captured",
            "dedupe_key": "logs:1",
            "payload_json": {"container": "bot"},
            "log_text_value": "enc::worker_error",
            "metadata_json": {"stdout": True},
            "created_at": now,
            "updated_at": later,
        }
    )
    assert evidence["title"] == "Compose logs"
    assert evidence["log_text"] == "worker_error"

    incident = storage._operation_incident_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "incident_id": "incident-1",
            "operation_id": "op-1",
            "service_kind": "discord",
            "incident_type": "discord_delivery_failed",
            "severity": "high",
            "blocking": True,
            "dedupe_key": "discord:1",
            "status": "open",
            "root_cause_summary_value": "enc::queue worker stalled",
            "recommended_fix_value": "enc::restart queue worker",
            "evidence_refs_json": ["evidence-1"],
            "metadata_json": {"channel_id": "123"},
            "created_at": now,
            "updated_at": later,
            "last_seen_at": later,
            "occurrence_count": 2,
        }
    )
    assert incident["root_cause_summary"] == "queue worker stalled"
    assert incident["recommended_fix"] == "restart queue worker"


def test_owner_ci_storage_row_mappers_accept_json_strings_from_asyncpg() -> None:
    storage = _storage(encrypted=True)
    now = datetime(2026, 3, 13, 6, 0, tzinfo=UTC)

    run = storage._run_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "run_id": "run-1",
            "repo_id": "zetherion-ai",
            "git_ref": "abc123",
            "trigger_value": "push",
            "status": "running",
            "plan_json": json.dumps({"lanes": ["z-unit-core"]}),
            "review_receipts": json.dumps({"merge_blocked": False}),
            "github_receipts": json.dumps({"status": "pending"}),
            "metadata_json": json.dumps({"git_sha": "abc123"}),
            "created_at": now,
            "updated_at": now,
        }
    )
    assert run["plan"]["lanes"] == ["z-unit-core"]
    assert run["review_receipts"]["merge_blocked"] is False

    shard = storage._shard_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "run_id": "run-1",
            "shard_id": "shard-1",
            "repo_id": "zetherion-ai",
            "lane_id": "z-unit-core",
            "lane_label_value": "enc::Unit core",
            "execution_target": "windows_local",
            "command_json": json.dumps(["pytest", "-q"]),
            "env_refs_json": json.dumps(["env://discord"]),
            "artifact_contract": json.dumps({"expects": ["stdout.log"]}),
            "required_capabilities": json.dumps(["docker"]),
            "relay_mode": "direct",
            "metadata_json": json.dumps({"resource_class": "cpu"}),
            "status": "queued",
            "result_json": json.dumps({}),
            "error_json": json.dumps({}),
            "started_at": now,
            "completed_at": now,
            "created_at": now,
            "updated_at": now,
        }
    )
    assert shard["command"] == ["pytest", "-q"]
    assert shard["artifact_contract"]["expects"] == ["stdout.log"]
    assert shard["required_capabilities"] == ["docker"]

    worker_job = storage._worker_job_from_row(  # noqa: SLF001
        {
            "scope_id": "owner:owner-1",
            "job_id": "job-1",
            "owner_id": "owner-1",
            "run_id": "run-1",
            "shard_id": "shard-1",
            "repo_id": "zetherion-ai",
            "action_name": "worker.noop",
            "runner_name": "docker",
            "payload_json": json.dumps({"lane_id": "z-unit-core", "runner": "docker"}),
            "required_capabilities": json.dumps(["docker"]),
            "artifact_contract": json.dumps({"expects": ["receipt.json"]}),
            "status": "queued",
            "idempotency_key": "idem-1",
            "execution_target": "windows_local",
            "claimed_by_node_id": None,
            "claimed_session_id": None,
            "result_json": json.dumps({}),
            "error_json": json.dumps({}),
            "created_at": now,
            "updated_at": now,
            "submitted_at": now,
        }
    )
    assert worker_job["payload_json"]["lane_id"] == "z-unit-core"
    assert worker_job["artifact_contract"]["expects"] == ["receipt.json"]
    assert worker_job["required_capabilities"] == ["docker"]

    compiled_plan = storage._compiled_plan_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "compiled_plan_id": "compiled-1",
            "repo_id": "catalyst-group-solutions",
            "git_ref": "refs/heads/main",
            "mode_value": "certification",
            "plan_json": json.dumps({"lanes": ["integration-critical", "golive-gate"]}),
            "metadata_json": json.dumps({"source": "owner_ci", "stack": "cgs"}),
            "created_at": now,
            "updated_at": now,
        }
    )
    assert compiled_plan["plan"]["lanes"] == ["integration-critical", "golive-gate"]
    assert compiled_plan["metadata"]["stack"] == "cgs"

    schedule = storage._schedule_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "schedule_id": "sched-1",
            "repo_id": "zetherion-ai",
            "name_value": "enc::Nightly",
            "schedule_kind": "weekly",
            "schedule_spec_json": json.dumps({"weekday": "fri", "hour": 2}),
            "active": True,
            "metadata_json": json.dumps({"canary": True}),
            "created_at": now,
            "updated_at": now,
        }
    )
    assert schedule["schedule_spec"]["hour"] == 2
    assert schedule["metadata"]["canary"] is True

    principal = storage._agent_principal_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "principal_id": "codex-1",
            "display_name_value": "enc::Codex",
            "principal_type": "agent",
            "allowed_scopes_json": json.dumps(["cgs:agent", "zetherion:release"]),
            "metadata_json": json.dumps({"team": "infra"}),
            "active": True,
            "created_at": now,
            "updated_at": now,
        }
    )
    assert principal["allowed_scopes"] == ["cgs:agent", "zetherion:release"]
    assert principal["metadata"]["team"] == "infra"

    incident = storage._operation_incident_from_row(  # noqa: SLF001
        {
            "owner_id": "owner-1",
            "incident_id": "incident-1",
            "operation_id": "op-1",
            "service_kind": "discord",
            "incident_type": "discord_delivery_failed",
            "severity": "high",
            "blocking": True,
            "dedupe_key": "discord:1",
            "status": "open",
            "root_cause_summary_value": "enc::queue worker stalled",
            "recommended_fix_value": "enc::restart queue worker",
            "evidence_refs_json": json.dumps(["bundle-1", "log-1"]),
            "metadata_json": json.dumps({"channel_id": "123", "delivery_mode": "dm"}),
            "created_at": now,
            "updated_at": now,
            "last_seen_at": now,
            "occurrence_count": 2,
        }
    )
    assert incident["evidence_refs"] == ["bundle-1", "log-1"]
    assert incident["metadata"]["delivery_mode"] == "dm"


def test_owner_ci_storage_helper_branches_cover_json_filtering_and_local_receipts() -> None:
    storage = _storage(encrypted=False)

    assert _stable_feedback_key([" Owner-1 ", "Run-1", None]) == _stable_feedback_key(
        ["owner-1", "run-1", ""]
    )
    assert _pending_repo_readiness("repo-1", "pending").missing_evidence == ["owner_ci_run_missing"]
    assert _validate_schema_identifier("owner_personal") == "owner_personal"
    with pytest.raises(ValueError, match="Invalid PostgreSQL schema name"):
        _validate_schema_identifier("owner-personal")

    assert OwnerCiStorage._coerce_json_value(None, "payload") is None  # noqa: SLF001
    assert OwnerCiStorage._coerce_json_value("  ", "payload") is None  # noqa: SLF001
    assert OwnerCiStorage._coerce_json_value('{"ok": true}', "payload") == {  # noqa: SLF001
        "ok": True
    }
    with pytest.raises(ValueError, match="payload must contain valid JSON"):
        OwnerCiStorage._coerce_json_value("{oops", "payload")  # noqa: SLF001
    assert OwnerCiStorage._coerce_json_object(None, "payload") == {}  # noqa: SLF001
    assert OwnerCiStorage._coerce_json_object('{"ok": true}', "payload") == {  # noqa: SLF001
        "ok": True
    }
    with pytest.raises(ValueError, match="payload must be a JSON object"):
        OwnerCiStorage._coerce_json_object('["oops"]', "payload")  # noqa: SLF001
    assert OwnerCiStorage._coerce_json_list(None, "payload") == []  # noqa: SLF001
    assert OwnerCiStorage._coerce_json_list((1, 2), "payload") == [1, 2]  # noqa: SLF001
    with pytest.raises(ValueError, match="payload must be a JSON array"):
        OwnerCiStorage._coerce_json_list('{"oops": true}', "payload")  # noqa: SLF001
    assert storage._decrypt_text("plain") == "plain"  # noqa: SLF001

    report = {
        "artifacts": [
            {"node_id": "run:1", "artifact_id": "a-run"},
            {"node_id": "shard:1", "artifact_id": "a-shard"},
        ],
        "evidence": [
            {"node_id": "run:1", "evidence_ref_id": "e-run"},
            {"node_id": "shard:1", "evidence_ref_id": "e-shard"},
        ],
        "all_evidence_references": [
            {"node_id": "run:1", "evidence_ref_id": "e-run"},
            {"node_id": "shard:1", "evidence_ref_id": "e-shard"},
        ],
        "run_graph": {
            "nodes": [{"node_id": "run:1"}, {"node_id": "shard:1"}],
            "artifacts": [
                {"node_id": "run:1", "artifact_id": "a-run"},
                {"node_id": "shard:1", "artifact_id": "a-shard"},
            ],
            "diagnostics": [
                {"node_id": "run:1", "diagnostic_id": "d-run"},
                {"node_id": "shard:1", "diagnostic_id": "d-shard"},
            ],
            "evidence_references": [
                {"node_id": "run:1", "evidence_ref_id": "e-run"},
                {"node_id": "shard:1", "evidence_ref_id": "e-shard"},
            ],
        },
        "diagnostic_findings": [
            {"finding_id": "f-run", "shard_id": ""},
            {"finding_id": "f-shard", "shard_id": "1"},
        ],
    }
    assert _filter_run_report_for_node(report, node_id="") is report
    filtered = _filter_run_report_for_node(report, node_id="shard:1")
    assert [item["artifact_id"] for item in filtered["artifacts"]] == ["a-shard"]
    assert [item["evidence_ref_id"] for item in filtered["evidence"]] == ["e-shard"]
    assert [item["node_id"] for item in filtered["run_graph"]["nodes"]] == ["shard:1"]
    assert [item["finding_id"] for item in filtered["diagnostic_findings"]] == ["f-shard"]

    assert _normalize_local_repo_readiness_payload([], repo_id_fallback="repo-1") == (None, None)
    assert _normalize_local_repo_readiness_payload({}, repo_id_fallback="") == (None, None)
    receipt, normalized = _normalize_local_repo_readiness_payload(
        {
            "repo_id": "repo-1",
            "merge_ready": True,
            "deploy_ready": False,
            "failed_required_paths": ["coverage"],
            "missing_evidence": ["lcov.info"],
            "release_verification": {"status": "healthy"},
            "shard_receipts": [{"lane_id": "unit", "shard_id": "unit#1", "status": "succeeded"}],
            "summary": "loaded",
        },
        repo_id_fallback="repo-fallback",
    )
    assert receipt is not None
    assert normalized is not None
    assert receipt.repo_id == "repo-1"
    assert receipt.failed_required_paths == ["coverage"]
    assert receipt.shard_receipts[0].lane_id == "unit"

    from_shards = _load_local_repo_readiness_from_shards(
        {"repo_id": "repo-1"},
        [
            "skip-me",
            {"result": {"local_readiness_receipt": {"repo_id": "", "merge_ready": True}}},
            {
                "result": {
                    "local_readiness_receipt": {
                        "repo_id": "repo-1",
                        "merge_ready": True,
                        "deploy_ready": True,
                        "summary": "from shard",
                    }
                }
            },
        ],
    )
    assert from_shards[0] is not None
    assert from_shards[0].summary == "from shard"


def test_storage_helper_merges_and_scheduler_reservations_cover_branch_paths() -> None:
    merged = _merge_json_dict(
        {"metadata": {"lane": "unit"}, "status": "queued"},
        {"metadata": {"repo": "repo-1"}, "status": "running"},
    )
    assert merged == {
        "metadata": {"lane": "unit", "repo": "repo-1"},
        "status": "running",
    }

    explicit_reservation = _job_resource_reservation(
        {
            "repo_id": "repo-1",
            "shard_id": "shard-1",
            "payload_json": {
                "resource_reservation": {
                    "resource_class": "service",
                    "units": 2,
                }
            },
        }
    )
    assert explicit_reservation.repo_id == "repo-1"
    assert explicit_reservation.shard_id == "shard-1"
    assert explicit_reservation.resource_class == "service"
    assert explicit_reservation.units == 2

    implicit_reservation = _job_resource_reservation(
        {
            "repo_id": "repo-2",
            "shard_id": "shard-2",
            "execution_target": "windows_local",
            "payload_json": {
                "resource_class": "serial",
                "resource_units": 0,
                "parallel_group": "deploy",
                "workspace_root": r"C:\\ZetherionCI\\workspaces\\run-1",
            },
        }
    )
    assert implicit_reservation.repo_id == "repo-2"
    assert implicit_reservation.shard_id == "shard-2"
    assert implicit_reservation.resource_class == "serial"
    assert implicit_reservation.units == 1
    assert implicit_reservation.parallel_group == "deploy"
    assert implicit_reservation.metadata["execution_target"] == "windows_local"

    run_rows = {
        "run-1": {
            "plan_json": {"host_capacity_policy": {"host_id": "windows-alpha", "cpu": 4}},
            "metadata_json": {"host_capacity_policy": {"host_id": "windows-beta"}},
        }
    }
    policy = _job_host_capacity_policy({"run_id": "run-1"}, run_rows)
    assert policy["host_id"] == "windows-alpha"
    assert _job_host_id({"run_id": "run-1"}, run_rows) == "windows-alpha"
    assert _job_host_capacity_policy({"run_id": "missing"}, run_rows) == {}
    assert _job_host_id({"run_id": "missing"}, run_rows) == "windows-owner-ci"

    queued_jobs = [
        {"repo_id": "repo-b", "created_at": "2026-03-17T07:00:01Z", "run_id": "run-b"},
        {"repo_id": "repo-a", "created_at": "2026-03-17T07:00:00Z", "run_id": "run-a"},
    ]
    active_jobs = [
        {"repo_id": "repo-a", "run_id": "run-a"},
    ]
    run_rows = {
        "run-a": {"plan_json": {"host_capacity_policy": {"host_id": "windows-owner-ci"}}},
        "run-b": {"plan_json": {"host_capacity_policy": {"host_id": "windows-owner-ci"}}},
    }
    ordered = _fair_claim_order(queued_jobs, active_jobs, run_rows)
    assert [job["repo_id"] for job in ordered] == ["repo-b", "repo-a"]
