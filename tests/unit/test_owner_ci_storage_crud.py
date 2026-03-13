"""Focused coverage for owner-CI storage CRUD and reporting methods."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.owner_ci.storage import OwnerCiStorage, ensure_owner_ci_schema


class _AsyncContext:
    def __init__(self, value):
        self._value = value

    async def __aenter__(self):
        return self._value

    async def __aexit__(self, exc_type, exc, tb):
        return False


class _FakeConn:
    def __init__(
        self,
        *,
        fetchrow_results: list[dict[str, object] | None] | None = None,
        fetch_results: list[list[dict[str, object]]] | None = None,
    ) -> None:
        self.fetchrow_results = list(fetchrow_results or [])
        self.fetch_results = list(fetch_results or [])
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetchrow(self, query: str, *args: object):
        self.fetchrow_calls.append((query, args))
        if self.fetchrow_results:
            return self.fetchrow_results.pop(0)
        return None

    async def fetch(self, query: str, *args: object):
        self.fetch_calls.append((query, args))
        if self.fetch_results:
            return self.fetch_results.pop(0)
        return []

    async def execute(self, query: str, *args: object):
        self.execute_calls.append((query, args))
        return "OK"

    def transaction(self):
        return _AsyncContext(self)


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self):
        return _AsyncContext(self._conn)


def _storage(conn: _FakeConn) -> OwnerCiStorage:
    storage = OwnerCiStorage.__new__(OwnerCiStorage)
    storage._pool = _FakePool(conn)  # type: ignore[attr-defined]
    storage._schema = "owner_personal"  # type: ignore[attr-defined]
    storage._encryptor = None  # type: ignore[attr-defined]
    return storage


def _dt() -> datetime:
    return datetime(2026, 3, 13, 8, 0, tzinfo=UTC)


def _repo_row() -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "repo_id": "zetherion-ai",
        "display_name_value": "Zetherion AI",
        "github_repo": "jimtin/zetherion-ai",
        "default_branch": "main",
        "stack_kind": "python",
        "local_fast_lanes": [{"lane_id": "z-unit-core"}],
        "local_full_lanes": [{"lane_id": "z-int-runtime"}],
        "windows_full_lanes": [{"lane_id": "z-release"}],
        "review_policy": {"required": True},
        "promotion_policy": {"require_release_receipt": True},
        "allowed_paths": ["/tmp/zetherion-ai"],
        "secrets_profile": "local",
        "active": True,
        "metadata_json": {
            "mandatory_static_gates": [{"lane_id": "ruff-check"}],
            "resource_classes": {"cpu": {"max_parallel": 8}},
            "windows_execution_mode": "docker_only",
            "certification_requirements": ["discord_roundtrip"],
            "scheduled_canaries": [{"lane_id": "z-e2e-discord-real"}],
            "shard_templates": [],
            "scheduling_policy": {},
            "debug_policy": {},
            "agent_bootstrap_profile": {},
        },
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _plan_row(version: int = 1) -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "plan_id": "plan-1",
        "repo_id": "zetherion-ai",
        "version": version,
        "title_value": "Plan",
        "content_markdown_value": "# Plan",
        "tags_json": ["repair"],
        "current_version": version == 2,
        "metadata_json": {"kind": "repair"},
        "created_at": _dt(),
    }


def _run_row(status: str = "planned") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "run_id": "run-1",
        "repo_id": "zetherion-ai",
        "git_ref": "main",
        "trigger_value": "manual",
        "status": status,
        "plan_json": {"compiled_plan": {"compiled_plan_id": "compiled-1"}},
        "review_receipts": {"merge_blocked": False},
        "github_receipts": {"merge_readiness": {"state": "success"}},
        "metadata_json": {"git_sha": "0" * 40},
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _shard_row(
    shard_id: str,
    lane_id: str,
    *,
    status: str,
    execution_target: str,
) -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "run_id": "run-1",
        "shard_id": shard_id,
        "repo_id": "zetherion-ai",
        "lane_id": lane_id,
        "lane_label_value": lane_id,
        "execution_target": execution_target,
        "command_json": ["pytest", "-q"],
        "env_refs_json": [],
        "artifact_contract": {"expects": ["stdout"]},
        "required_capabilities": ["ci.test.run"],
        "relay_mode": "direct",
        "metadata_json": {"resource_class": "cpu"},
        "status": status,
        "result_json": {},
        "error_json": {},
        "started_at": _dt(),
        "completed_at": _dt(),
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _worker_job_row() -> dict[str, object]:
    return {
        "scope_id": "owner:owner-1:repo:zetherion-ai",
        "job_id": "job-1",
        "owner_id": "owner-1",
        "run_id": "run-1",
        "shard_id": "shard-win",
        "repo_id": "zetherion-ai",
        "action_name": "ci.test.run",
        "runner_name": "docker",
        "payload_json": {"execution_target": "windows_local"},
        "required_capabilities": ["ci.test.run"],
        "artifact_contract": {"kind": "ci_shard"},
        "status": "queued",
        "idempotency_key": "run-1:shard-win",
        "execution_target": "windows_local",
        "claimed_by_node_id": None,
        "claimed_session_id": None,
        "result_json": {},
        "error_json": {},
        "created_at": _dt(),
        "updated_at": _dt(),
        "submitted_at": _dt(),
    }


def _worker_session_row(session_id: str = "sess-1") -> dict[str, object]:
    return {
        "scope_id": "owner:owner-1:repo:zetherion-ai",
        "node_id": "node-1",
        "session_id": session_id,
        "token_hash": "hashed",
        "signing_secret": "signing-secret",
        "status": "registered",
        "health_status": "healthy",
        "node_metadata": {"pool": "local"},
        "expires_at": _dt(),
        "revoked_at": None,
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _worker_node_row(node_id: str = "node-1") -> dict[str, object]:
    return {
        "scope_id": "owner:owner-1:repo:zetherion-ai",
        "node_id": node_id,
        "node_name": "Windows worker",
        "capabilities_json": ["docker"],
        "status": "active",
        "health_status": "healthy",
        "metadata_json": {"pool": "local"},
        "created_at": _dt(),
        "updated_at": _dt(),
        "last_heartbeat_at": _dt(),
    }


def _compiled_plan_row() -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "compiled_plan_id": "compiled-1",
        "repo_id": "zetherion-ai",
        "git_ref": "main",
        "mode_value": "certification",
        "plan_json": {"shards": [{"lane_id": "ruff-check"}]},
        "metadata_json": {"trigger": "manual"},
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _schedule_row() -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "schedule_id": "schedule-1",
        "repo_id": "zetherion-ai",
        "name_value": "Daily",
        "schedule_kind": "weekly",
        "schedule_spec_json": {"hour": 9},
        "active": True,
        "metadata_json": {"kind": "canary"},
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _docs_row(slug: str = "doc-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "slug": slug,
        "title_value": "Quickstart",
        "manifest_json": {"content_markdown": "# Quickstart"},
        "updated_at": _dt(),
    }


def _principal_row(principal_id: str = "codex-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "principal_id": principal_id,
        "display_name_value": "Codex 1",
        "principal_type": "codex",
        "allowed_scopes_json": ["workspace:read"],
        "metadata_json": {"source": "local"},
        "active": True,
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _connector_row(
    connector_id: str = "github-primary",
    *,
    service_kind: str = "github",
    has_secret: bool = True,
) -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "connector_id": connector_id,
        "service_kind": service_kind,
        "display_name_value": "GitHub",
        "auth_kind": "token",
        "secret_value": "secret-token" if has_secret else None,
        "policy_json": {"read_access": ["branch_metadata"]},
        "metadata_json": {"rotated_at": _dt().isoformat()},
        "active": True,
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _grant_row(grant_key: str = "grant-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "principal_id": "codex-1",
        "grant_key": grant_key,
        "resource_type": "app",
        "resource_id": "catalyst-group-solutions",
        "capabilities_json": ["github:read"],
        "metadata_json": {"broker_only": True},
        "active": True,
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _app_profile_row(app_id: str = "catalyst-group-solutions") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "app_id": app_id,
        "display_name_value": "Catalyst Group Solutions",
        "profile_json": {"repo_ids": [app_id], "docs_slugs": ["doc-1"]},
        "active": True,
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _pack_row(version: str = "v1", *, current: bool = True) -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "app_id": "catalyst-group-solutions",
        "version_value": version,
        "pack_json": {"workspace_manifest": {"repo_id": "catalyst-group-solutions"}},
        "current_version": current,
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _bundle_row(bundle_id: str = "bundle-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "bundle_id": bundle_id,
        "principal_id": "codex-1",
        "app_id": "catalyst-group-solutions",
        "repo_id": "catalyst-group-solutions",
        "git_ref": "main",
        "resolved_ref": "abc123",
        "bundle_json": {"bundle_id": bundle_id, "download_mode": "inline_base64"},
        "expires_at": _dt(),
        "created_at": _dt(),
        "updated_at": _dt(),
        "downloaded_at": None,
    }


def _candidate_row(
    candidate_id: str = "cand-1",
    *,
    status: str = "submitted",
) -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "candidate_id": candidate_id,
        "principal_id": "codex-1",
        "app_id": "catalyst-group-solutions",
        "repo_id": "catalyst-group-solutions",
        "base_sha": "a" * 40,
        "status": status,
        "candidate_json": {"candidate_id": candidate_id, "diff_text": "diff --git"},
        "review_json": {"approved": status == "approved"},
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _secret_ref_row(
    secret_ref_id: str = "secret-1",
    *,
    has_secret: bool = True,
) -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "secret_ref_id": secret_ref_id,
        "connector_id": "github-primary",
        "purpose_value": "api-token",
        "secret_value": "secret" if has_secret else None,
        "metadata_json": {"scope": "local"},
        "active": True,
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _audit_row(audit_id: str = "audit-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "audit_id": audit_id,
        "principal_id": "codex-1",
        "app_id": "catalyst-group-solutions",
        "service_kind": "github",
        "resource_value": "repo",
        "action_value": "read",
        "decision_value": "allowed",
        "audit_json": {"reason": "brokered"},
        "created_at": _dt(),
    }


def _session_row(session_id: str = "sess-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "session_id": session_id,
        "principal_id": "codex-1",
        "app_id": "catalyst-group-solutions",
        "session_status": "active",
        "metadata_json": {"source": "codex"},
        "created_at": _dt(),
        "updated_at": _dt(),
        "last_activity_at": _dt(),
    }


def _interaction_row(interaction_id: str = "int-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "interaction_id": interaction_id,
        "session_id": "sess-1",
        "principal_id": "codex-1",
        "app_id": "catalyst-group-solutions",
        "repo_id": "catalyst-group-solutions",
        "route_path_value": "/admin/ai",
        "intent_value": "ci_reporting_readiness",
        "request_text_value": "show readiness",
        "request_json": {"intent": "ci_reporting_readiness"},
        "normalized_intent_json": {"owner_id": "owner-1"},
        "related_run_id": "run-1",
        "related_candidate_id": None,
        "related_service_request_id": None,
        "audit_id": "audit-1",
        "created_at": _dt(),
    }


def _action_row(action_id: str = "action-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "action_record_id": action_id,
        "interaction_id": "int-1",
        "principal_id": "codex-1",
        "app_id": "catalyst-group-solutions",
        "action_value": "publish",
        "status": "requested",
        "payload_json": {"candidate_id": "cand-1"},
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _outcome_row(outcome_id: str = "outcome-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "outcome_id": outcome_id,
        "interaction_id": "int-1",
        "action_record_id": "action-1",
        "status": "succeeded",
        "summary_value": "Ready",
        "payload_json": {"merge_ready": True},
        "created_at": _dt(),
    }


def _gap_row(
    gap_id: str = "gap-1",
    *,
    status: str = "open",
    occurrence_count: int = 1,
) -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "gap_id": gap_id,
        "dedupe_key": "gap:1",
        "session_id": "sess-1",
        "principal_id": "codex-1",
        "app_id": "catalyst-group-solutions",
        "repo_id": "catalyst-group-solutions",
        "run_id": "run-1",
        "gap_type": "service_evidence_incomplete",
        "severity": "high",
        "blocker": True,
        "detected_from": "release_verification",
        "required_capability": "container_logs",
        "observed_request_json": {"service_kind": "docker"},
        "suggested_fix_value": "capture logs",
        "status": status,
        "metadata_json": {"evidence": "missing"},
        "first_seen_at": _dt(),
        "last_seen_at": _dt(),
        "occurrence_count": occurrence_count,
        "updated_at": _dt(),
    }


def _service_request_row(request_id: str = "req-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "request_id": request_id,
        "principal_id": "codex-1",
        "app_id": "catalyst-group-solutions",
        "service_kind": "stripe",
        "action_id": "product.ensure",
        "target_ref": None,
        "tenant_id": None,
        "change_reason_value": "provision product",
        "request_json": {"name": "Gold"},
        "status": "executed",
        "approved": True,
        "result_json": {"product_id": "prod_1"},
        "audit_id": "audit-1",
        "created_at": _dt(),
        "updated_at": _dt(),
        "executed_at": _dt(),
    }


def _capability_row(service_kind: str = "github") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "service_kind": service_kind,
        "manifest_json": {"supports_logs": True},
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _operation_row(
    operation_id: str = "op-1",
    *,
    correlation_key: str | None = "deploy:1",
    status: str = "active",
) -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "operation_id": operation_id,
        "app_id": "catalyst-group-solutions",
        "repo_id": "catalyst-group-solutions",
        "operation_kind": "deploy",
        "lifecycle_stage": "verification",
        "status": status,
        "correlation_key": correlation_key,
        "summary_json": {"status": status},
        "metadata_json": {"source": "windows"},
        "created_at": _dt(),
        "updated_at": _dt(),
        "last_observed_at": _dt(),
    }


def _op_ref_row(ref_id: str = "ref-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "ref_id": ref_id,
        "operation_id": "op-1",
        "service_kind": "github",
        "ref_kind": "git_sha",
        "ref_value": "a" * 40,
        "dedupe_key": f"github:git_sha:{'a' * 40}",
        "metadata_json": {"repo": "catalyst-group-solutions"},
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _evidence_row(
    evidence_id: str = "evidence-1",
    *,
    log_text: str | None = "worker_error",
) -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "evidence_id": evidence_id,
        "operation_id": "op-1",
        "service_kind": "docker",
        "evidence_type": "logs",
        "title_value": "Compose logs",
        "state": "captured",
        "dedupe_key": f"logs:{evidence_id}",
        "payload_json": {"stream": "stderr"},
        "log_text_value": log_text,
        "metadata_json": {"stdout": False},
        "created_at": _dt(),
        "updated_at": _dt(),
    }


def _incident_row(incident_id: str = "incident-1") -> dict[str, object]:
    return {
        "owner_id": "owner-1",
        "incident_id": incident_id,
        "operation_id": "op-1",
        "service_kind": "discord",
        "incident_type": "discord_delivery_failed",
        "severity": "high",
        "blocking": True,
        "dedupe_key": "discord:1",
        "status": "open",
        "root_cause_summary_value": "queue worker stalled",
        "recommended_fix_value": "restart queue worker",
        "evidence_refs_json": ["evidence-1"],
        "metadata_json": {"channel_id": "123"},
        "created_at": _dt(),
        "updated_at": _dt(),
        "last_seen_at": _dt(),
        "occurrence_count": 2,
    }


@pytest.mark.asyncio
async def test_repo_profile_and_plan_snapshot_methods_round_trip_rows() -> None:
    conn = _FakeConn(
        fetchrow_results=[
            _repo_row(),
            _repo_row(),
            {"current_version": 1},
            _plan_row(2),
            _plan_row(2),
        ],
        fetch_results=[[_repo_row()], [_plan_row(2), _plan_row(1)]],
    )
    storage = _storage(conn)

    repo = await storage.upsert_repo_profile(
        "owner-1",
        {
            "repo_id": "zetherion-ai",
            "display_name": "Zetherion AI",
            "github_repo": "jimtin/zetherion-ai",
            "default_branch": "main",
            "stack_kind": "python",
            "local_fast_lanes": [{"lane_id": "z-unit-core"}],
            "local_full_lanes": [{"lane_id": "z-int-runtime"}],
            "windows_full_lanes": [{"lane_id": "z-release"}],
            "review_policy": {"required": True},
            "promotion_policy": {"require_release_receipt": True},
            "allowed_paths": ["/tmp/zetherion-ai"],
            "secrets_profile": "local",
            "metadata": {"windows_execution_mode": "docker_only"},
        },
    )
    listed = await storage.list_repo_profiles("owner-1")
    loaded = await storage.get_repo_profile("owner-1", "zetherion-ai")
    plan = await storage.create_plan_snapshot(
        owner_id="owner-1",
        repo_id="zetherion-ai",
        title="Plan",
        content_markdown="# Plan",
        tags=["repair"],
        plan_id="plan-1",
        metadata={"kind": "repair"},
    )
    versions = await storage.list_plan_versions("owner-1", "plan-1")
    latest = await storage.get_plan_snapshot("owner-1", "plan-1")

    assert repo["display_name"] == "Zetherion AI"
    assert listed[0]["repo_id"] == "zetherion-ai"
    assert loaded is not None and loaded["metadata"]["windows_execution_mode"] == "docker_only"
    assert plan["version"] == 2
    assert [version["version"] for version in versions] == [2, 1]
    assert latest is not None and latest["current"] is True


@pytest.mark.asyncio
async def test_create_run_get_run_and_list_runs_include_worker_jobs() -> None:
    conn = _FakeConn(
        fetchrow_results=[
            _run_row(status="queued_local"),
            _shard_row(
                "shard-local",
                "z-unit-core",
                status="planned",
                execution_target="local_mac",
            ),
            _shard_row(
                "shard-win",
                "discord-required-e2e",
                status="queued_local",
                execution_target="windows_local",
            ),
            _run_row(status="queued_local"),
        ],
        fetch_results=[
            [
                _shard_row(
                    "shard-local",
                    "z-unit-core",
                    status="planned",
                    execution_target="local_mac",
                ),
                _shard_row(
                    "shard-win",
                    "discord-required-e2e",
                    status="queued_local",
                    execution_target="windows_local",
                ),
            ],
            [_worker_job_row()],
            [_run_row(status="queued_local")],
        ],
    )
    storage = _storage(conn)

    run = await storage.create_run(
        owner_id="owner-1",
        scope_id="owner:owner-1:repo:zetherion-ai",
        repo_id="zetherion-ai",
        git_ref="main",
        trigger="manual",
        plan={"compiled_plan": {"compiled_plan_id": "compiled-1"}},
        metadata={"git_sha": "0" * 40},
        shards=[
            {
                "shard_id": "shard-local",
                "lane_id": "z-unit-core",
                "lane_label": "Unit",
                "command": ["pytest", "tests/unit"],
                "metadata": {"resource_class": "cpu"},
            },
            {
                "shard_id": "shard-win",
                "lane_id": "discord-required-e2e",
                "lane_label": "Discord",
                "execution_target": "windows_local",
                "runner": "docker",
                "command": ["bash", "scripts/run-required-discord-e2e.sh"],
                "artifact_contract": {"kind": "ci_shard"},
                "required_capabilities": ["ci.test.run"],
                "workspace_root": "/tmp/zetherion-ai",
                "payload": {"container_spec": {"image": "python:3.12"}},
                "metadata": {"resource_class": "serial"},
            },
        ],
    )
    listed = await storage.list_runs("owner-1")

    assert run["status"] == "queued_local"
    assert len(run["shards"]) == 2
    assert run["worker_jobs"][0]["execution_target"] == "windows_local"
    assert listed[0]["run_id"] == "run-1"
    job_insert = conn.execute_calls[0]
    payload = json.loads(str(job_insert[1][8]))
    assert payload["container_spec"] == {"image": "python:3.12"}
    assert payload["workspace_root"] == "/tmp/zetherion-ai"


@pytest.mark.asyncio
async def test_review_receipt_metadata_status_and_github_receipts_update_methods() -> None:
    conn = _FakeConn(
        fetchrow_results=[
            _run_row(),
            _run_row(),
            {"github_receipts": {"existing": {"state": "success"}}},
            {"metadata_json": {"release_verification": {"status": "pending"}}},
            {
                **_run_row(),
                "metadata_json": {"release_verification": {"status": "healthy"}},
            },
            _run_row(status="ready_to_merge"),
        ]
    )
    storage = _storage(conn)
    storage._recompute_run_status = AsyncMock()  # type: ignore[attr-defined]
    storage.get_run = AsyncMock(return_value={**_run_row(), "shards": []})  # type: ignore[method-assign]

    reviewed = await storage.store_run_review("owner-1", "run-1", {"verdict": "approved"})
    github = await storage.store_run_github_receipt(
        "owner-1",
        "run-1",
        {"merge_readiness": {"state": "success"}},
    )
    metadata = await storage.merge_run_metadata(
        "owner-1",
        "run-1",
        {"release_verification": {"status": "healthy"}},
    )
    status = await storage.set_run_status("owner-1", "run-1", "ready_to_merge")

    assert reviewed is not None
    assert github is not None
    assert metadata is not None
    assert metadata["metadata"]["release_verification"]["status"] == "healthy"
    assert status is not None and status["status"] == "ready_to_merge"
    with pytest.raises(ValueError, match="Invalid run status"):
        await storage.set_run_status("owner-1", "run-1", "bogus")


@pytest.mark.asyncio
async def test_compiled_plan_schedule_and_reporting_helpers_aggregate_rows() -> None:
    conn = _FakeConn(
        fetchrow_results=[_compiled_plan_row(), _compiled_plan_row(), _schedule_row()],
        fetch_results=[
            [_compiled_plan_row()],
            [_schedule_row()],
            [
                {
                    "summary_json": {
                        "compute_minutes": 4.5,
                        "peak_memory_mb": 512,
                        "container_count": 3,
                    }
                }
            ],
            [_run_row(status="promotion_blocked")],
            [
                {
                    "owner_id": "owner-1",
                    "sample_id": "sample-1",
                    "repo_id": "zetherion-ai",
                    "run_id": "run-1",
                    "shard_id": "shard-1",
                    "node_id": "windows-main",
                    "sample_json": {"memory_mb": 1024, "disk_used_bytes": 2048},
                    "created_at": _dt(),
                }
            ],
        ],
    )
    storage = _storage(conn)

    compiled = await storage.create_compiled_plan(
        owner_id="owner-1",
        repo_id="zetherion-ai",
        git_ref="main",
        mode="certification",
        plan={"shards": [{"lane_id": "ruff-check"}]},
        metadata={"trigger": "manual"},
    )
    loaded = await storage.get_compiled_plan("owner-1", "compiled-1")
    listed = await storage.list_compiled_plans("owner-1")
    schedule = await storage.upsert_schedule(
        owner_id="owner-1",
        repo_id="zetherion-ai",
        name="Daily",
        schedule_kind="weekly",
        schedule_spec={"hour": 9},
        metadata={"kind": "canary"},
    )
    schedules = await storage.list_schedules("owner-1")
    project_resources = await storage.get_project_resource_report("owner-1", "zetherion-ai")
    failures = await storage.get_project_failure_report("owner-1", "zetherion-ai")
    worker_resources = await storage.get_worker_resource_report("owner-1", "windows-main")

    assert compiled["compiled_plan_id"] == "compiled-1"
    assert loaded is not None and loaded["mode"] == "certification"
    assert listed[0]["repo_id"] == "zetherion-ai"
    assert schedule["name"] == "Daily"
    assert schedules[0]["schedule_kind"] == "weekly"
    assert project_resources["totals"]["compute_minutes"] == 4.5
    assert failures["failures"][0]["status"] == "promotion_blocked"
    assert worker_resources["totals"]["peak_memory_mb"] == 1024.0


@pytest.mark.asyncio
async def test_ensure_owner_ci_schema_handles_missing_and_present_pool() -> None:
    assert await ensure_owner_ci_schema(None) is None

    conn = _FakeConn()
    storage = await ensure_owner_ci_schema(_FakePool(conn))

    assert storage is not None
    assert conn.execute_calls


@pytest.mark.asyncio
async def test_agent_bootstrap_docs_principal_connector_and_app_methods_round_trip() -> None:
    conn = _FakeConn(
        fetchrow_results=[
            {"manifest_json": {"version": "v2"}},
            {"manifest_json": {"version": "v2"}},
            {"receipt_json": {"receipt_id": "receipt-1", "status": "stored"}},
            _docs_row(),
            _docs_row(),
            _principal_row(),
            _principal_row(),
            _connector_row(),
            _connector_row(),
            _connector_row(),
            _grant_row("grant-1"),
            _grant_row("grant-2"),
            _app_profile_row(),
            _app_profile_row(),
            _app_profile_row(),
            _pack_row(),
            _pack_row(),
            _pack_row(),
        ],
        fetch_results=[
            [_docs_row()],
            [_principal_row()],
            [_connector_row()],
            [_grant_row("grant-2"), _grant_row("grant-1")],
            [_app_profile_row()],
        ],
    )
    storage = _storage(conn)

    manifest = await storage.store_agent_bootstrap_manifest(
        "owner-1",
        "codex-desktop",
        {"version": "v2"},
    )
    loaded_manifest = await storage.get_agent_bootstrap_manifest("owner-1", "codex-desktop")
    receipt = await storage.store_agent_setup_receipt(
        "owner-1",
        client_id="codex-desktop",
        receipt={"receipt_id": "receipt-1", "status": "stored"},
    )
    doc = await storage.upsert_agent_docs_manifest(
        "owner-1",
        slug="doc-1",
        title="Quickstart",
        manifest={"content_markdown": "# Quickstart"},
    )
    docs = await storage.list_agent_docs_manifests("owner-1")
    loaded_doc = await storage.get_agent_docs_manifest("owner-1", "doc-1")
    principal = await storage.upsert_agent_principal(
        "owner-1",
        principal_id="codex-1",
        display_name="Codex 1",
        allowed_scopes=["workspace:read"],
        metadata={"source": "local"},
    )
    loaded_principal = await storage.get_agent_principal("owner-1", "codex-1")
    principals = await storage.list_agent_principals("owner-1")
    connector = await storage.upsert_external_service_connector(
        "owner-1",
        connector_id="github-primary",
        service_kind="github",
        display_name="GitHub",
        auth_kind="token",
        secret_value="secret-token",
        policy={"read_access": ["branch_metadata"]},
        metadata={"scope": "repo"},
    )
    loaded_connector = await storage.get_external_service_connector("owner-1", "github-primary")
    connector_with_secret = await storage.get_external_service_connector_with_secret(
        "owner-1",
        "github-primary",
    )
    connectors = await storage.list_external_service_connectors(
        "owner-1",
        service_kind="github",
    )
    grants = await storage.replace_external_access_grants(
        "owner-1",
        principal_id="codex-1",
        grants=[
            {"resource_type": "app", "resource_id": "catalyst-group-solutions"},
            {"resource_type": "", "resource_id": "skip-me"},
            {"resource_type": "repo", "resource_id": "jimtin/zetherion-ai"},
        ],
    )
    listed_grants = await storage.list_external_access_grants(
        "owner-1",
        principal_id="codex-1",
    )
    app_profile = await storage.upsert_agent_app_profile(
        "owner-1",
        app_id="catalyst-group-solutions",
        display_name="Catalyst Group Solutions",
        profile={"repo_ids": ["catalyst-group-solutions"]},
    )
    loaded_app_profile = await storage.get_agent_app_profile(
        "owner-1",
        "catalyst-group-solutions",
    )
    found_app_profile = await storage.find_agent_app_profile("catalyst-group-solutions")
    listed_app_profiles = await storage.list_agent_app_profiles("owner-1")
    pack = await storage.upsert_agent_knowledge_pack(
        "owner-1",
        app_id="catalyst-group-solutions",
        version="v1",
        pack={"workspace_manifest": {"repo_id": "catalyst-group-solutions"}},
    )
    current_pack = await storage.get_agent_knowledge_pack(
        "owner-1",
        "catalyst-group-solutions",
    )
    versioned_pack = await storage.get_agent_knowledge_pack(
        "owner-1",
        "catalyst-group-solutions",
        version="v1",
    )

    assert manifest["client_id"] == "codex-desktop"
    assert loaded_manifest == manifest
    assert receipt["status"] == "stored"
    assert doc["slug"] == "doc-1"
    assert docs[0]["title"] == "Quickstart"
    assert loaded_doc is not None and loaded_doc["manifest"]["content_markdown"] == "# Quickstart"
    assert principal["principal_id"] == "codex-1"
    assert loaded_principal is not None and loaded_principal["display_name"] == "Codex 1"
    assert principals[0]["principal_type"] == "codex"
    assert connector["has_secret"] is True
    assert loaded_connector is not None and loaded_connector["service_kind"] == "github"
    assert connector_with_secret is not None
    assert connector_with_secret["secret_value"] == "secret-token"
    assert connectors[0]["connector_id"] == "github-primary"
    assert [grant["grant_key"] for grant in grants] == ["grant-1", "grant-2"]
    assert listed_grants[0]["resource_type"] == "app"
    assert app_profile["app_id"] == "catalyst-group-solutions"
    assert loaded_app_profile is not None
    assert loaded_app_profile["display_name"] == "Catalyst Group Solutions"
    assert found_app_profile is not None and found_app_profile["active"] is True
    assert listed_app_profiles[0]["profile"]["repo_ids"] == ["catalyst-group-solutions"]
    assert pack["version"] == "v1"
    assert current_pack is not None and current_pack["current"] is True
    assert versioned_pack is not None
    assert (
        versioned_pack["pack"]["workspace_manifest"]["repo_id"]
        == "catalyst-group-solutions"
    )
    assert any("DELETE FROM" in call[0] for call in conn.execute_calls)


@pytest.mark.asyncio
async def test_workspace_publish_session_and_service_request_methods_round_trip() -> None:
    conn = _FakeConn(
        fetchrow_results=[
            _bundle_row(),
            _bundle_row(),
            _candidate_row(),
            _candidate_row(),
            _candidate_row(status="approved"),
            _secret_ref_row(has_secret=True),
            _secret_ref_row(has_secret=False),
            _audit_row(),
            _session_row(),
            _session_row(),
            _interaction_row(),
            _action_row(),
            _outcome_row(),
            _service_request_row(),
        ],
        fetch_results=[
            [_candidate_row(), _candidate_row("cand-2")],
            [_candidate_row()],
            [_secret_ref_row(has_secret=True)],
            [_audit_row()],
            [_interaction_row()],
        ],
    )
    storage = _storage(conn)

    bundle = await storage.create_workspace_bundle(
        "owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        repo_id="catalyst-group-solutions",
        git_ref="main",
        bundle={"bundle_id": "bundle-1", "download_mode": "inline_base64"},
        resolved_ref="abc123",
        expires_at=_dt(),
    )
    loaded_bundle = await storage.get_workspace_bundle("owner-1", "bundle-1")
    await storage.mark_workspace_bundle_downloaded("owner-1", "bundle-1")
    candidate = await storage.create_publish_candidate(
        "owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        repo_id="catalyst-group-solutions",
        base_sha="a" * 40,
        candidate={"candidate_id": "cand-1", "diff_text": "diff --git"},
    )
    loaded_candidate = await storage.get_publish_candidate("owner-1", "cand-1")
    listed_candidates = await storage.list_publish_candidates("owner-1")
    filtered_candidates = await storage.list_publish_candidates(
        "owner-1",
        app_id="catalyst-group-solutions",
    )
    reviewed_candidate = await storage.update_publish_candidate_review(
        "owner-1",
        candidate_id="cand-1",
        status="approved",
        review={"approved": True},
    )
    secret_ref = await storage.upsert_secret_ref(
        "owner-1",
        secret_ref_id="secret-1",
        purpose="api-token",
        secret_value="secret",
        connector_id="github-primary",
        metadata={"scope": "local"},
    )
    secret_ref_no_secret = await storage.upsert_secret_ref(
        "owner-1",
        secret_ref_id="secret-2",
        purpose="readonly",
        connector_id="github-primary",
    )
    secret_refs = await storage.list_secret_refs("owner-1", active_only=True)
    audit_event = await storage.record_agent_audit_event(
        "owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        service_kind="github",
        resource="repo",
        action="read",
        decision="allowed",
        audit={"reason": "brokered"},
    )
    audit_events = await storage.list_agent_audit_events(
        "owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
    )
    session = await storage.create_agent_session(
        "owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        session_id="sess-1",
        metadata={"source": "codex"},
    )
    loaded_session = await storage.get_agent_session("owner-1", "sess-1")
    await storage.touch_agent_session("owner-1", "sess-1")
    interaction = await storage.create_agent_interaction(
        "owner-1",
        session_id="sess-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        repo_id="catalyst-group-solutions",
        route_path="/admin/ai",
        intent="ci_reporting_readiness",
        request_text="show readiness",
        request_payload={"intent": "ci_reporting_readiness"},
        normalized_intent={"owner_id": "owner-1"},
        related_run_id="run-1",
        audit_id="audit-1",
    )
    interactions = await storage.list_agent_session_interactions("owner-1", "sess-1")
    action = await storage.create_agent_action(
        "owner-1",
        interaction_id="int-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        action="publish",
        status="requested",
        payload={"candidate_id": "cand-1"},
    )
    outcome = await storage.create_agent_outcome(
        "owner-1",
        interaction_id="int-1",
        action_record_id="action-1",
        status="succeeded",
        summary="Ready",
        payload={"merge_ready": True},
    )
    service_request = await storage.create_agent_service_request(
        "owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        service_kind="stripe",
        action_id="product.ensure",
        target_ref=None,
        tenant_id=None,
        change_reason="provision product",
        request_payload={"name": "Gold"},
        status="executed",
        approved=True,
        result={"product_id": "prod_1"},
        audit_id="audit-1",
        executed=True,
    )

    assert bundle["resolved_ref"] == "abc123"
    assert loaded_bundle is not None and loaded_bundle["bundle"]["bundle_id"] == "bundle-1"
    assert candidate["status"] == "submitted"
    assert loaded_candidate is not None
    assert loaded_candidate["candidate"]["candidate_id"] == "cand-1"
    assert len(listed_candidates) == 2
    assert filtered_candidates[0]["app_id"] == "catalyst-group-solutions"
    assert reviewed_candidate is not None and reviewed_candidate["review"]["approved"] is True
    assert secret_ref["has_secret"] is True
    assert secret_ref_no_secret["has_secret"] is False
    assert secret_refs[0]["active"] is True
    assert audit_event["decision"] == "allowed"
    assert audit_events[0]["resource"] == "repo"
    assert session["session_id"] == "sess-1"
    assert loaded_session is not None and loaded_session["status"] == "active"
    assert interaction["route_path"] == "/admin/ai"
    assert interactions[0]["intent"] == "ci_reporting_readiness"
    assert action["action"] == "publish"
    assert outcome["summary"] == "Ready"
    assert service_request["approved"] is True
    assert any("downloaded_at = now()" in call[0] for call in conn.execute_calls)


@pytest.mark.asyncio
async def test_gap_capability_operation_and_evidence_methods_round_trip() -> None:
    conn = _FakeConn(
        fetchrow_results=[
            _gap_row(),
            _gap_row(),
            _gap_row(status="resolved", occurrence_count=2),
            _capability_row(),
            _capability_row(),
            _operation_row(),
            _operation_row("op-2", correlation_key=None),
            _operation_row(status="resolved"),
            _operation_row(status="resolved"),
            _operation_row(status="resolved"),
            _op_ref_row(),
            _evidence_row(),
            _incident_row(),
        ],
        fetch_results=[
            [_gap_row()],
            [_capability_row()],
            [_operation_row(status="resolved")],
            [_op_ref_row()],
            [_evidence_row(), _evidence_row("evidence-2", log_text=None)],
            [_incident_row()],
            [_incident_row()],
        ],
    )
    storage = _storage(conn)

    gap = await storage.record_agent_gap_event(
        "owner-1",
        dedupe_key="gap:1",
        session_id="sess-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        repo_id="catalyst-group-solutions",
        run_id="run-1",
        gap_type="service_evidence_incomplete",
        severity="high",
        blocker=True,
        detected_from="release_verification",
        required_capability="container_logs",
        observed_request={"service_kind": "docker"},
        suggested_fix="capture logs",
    )
    gaps = await storage.list_agent_gap_events(
        "owner-1",
        session_id="sess-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        repo_id="catalyst-group-solutions",
        unresolved_only=True,
        blocker_only=True,
    )
    loaded_gap = await storage.get_agent_gap_event("owner-1", "gap-1")
    updated_gap = await storage.update_agent_gap_event(
        "owner-1",
        gap_id="gap-1",
        status="resolved",
        metadata={"fixed": True},
    )
    capability = await storage.upsert_service_adapter_capability(
        "owner-1",
        service_kind="github",
        manifest={"supports_logs": True},
    )
    loaded_capability = await storage.get_service_adapter_capability("owner-1", "github")
    capabilities = await storage.list_service_adapter_capabilities("owner-1")
    operation = await storage.create_managed_operation(
        "owner-1",
        app_id="catalyst-group-solutions",
        repo_id="catalyst-group-solutions",
        operation_kind="deploy",
        lifecycle_stage="verification",
        status="active",
        summary={"status": "pending"},
        metadata={"source": "windows"},
        correlation_key="deploy:1",
    )
    operation_without_key = await storage.create_managed_operation(
        "owner-1",
        app_id="catalyst-group-solutions",
        repo_id="catalyst-group-solutions",
        operation_kind="deploy",
        lifecycle_stage="verification",
        status="active",
        summary={"status": "pending"},
        metadata={"source": "local"},
        correlation_key=None,
        operation_id="op-2",
    )
    updated_operation = await storage.update_managed_operation(
        "owner-1",
        operation_id="op-1",
        lifecycle_stage="resolved",
        status="resolved",
        summary={"status": "green"},
        metadata={"source": "repaired"},
    )
    loaded_operation = await storage.get_managed_operation("owner-1", "op-1")
    operations = await storage.list_managed_operations(
        "owner-1",
        app_id="catalyst-group-solutions",
        repo_id="catalyst-group-solutions",
        service_kind="github",
        status="resolved",
    )
    found_operation = await storage.find_managed_operation_by_ref(
        "owner-1",
        ref_kind="git_sha",
        ref_value="a" * 40,
        app_id="catalyst-group-solutions",
    )
    operation_ref = await storage.upsert_operation_ref(
        "owner-1",
        operation_id="op-1",
        service_kind="GitHub",
        ref_kind="git_sha",
        ref_value="A" * 40,
        metadata={"repo": "catalyst-group-solutions"},
    )
    refs = await storage.list_operation_refs("owner-1", "op-1")
    evidence = await storage.record_operation_evidence(
        "owner-1",
        operation_id="op-1",
        service_kind="docker",
        evidence_type="logs",
        title="Compose logs",
        payload={"stream": "stderr"},
        log_text="worker_error",
        metadata={"stdout": False},
    )
    listed_evidence = await storage.list_operation_evidence(
        "owner-1",
        "op-1",
        service_kind="docker",
        evidence_type="logs",
    )
    storage.list_operation_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "evidence_id": "evidence-1",
                "service_kind": "docker",
                "metadata": {"stdout": False},
                "payload": {"stream": "stderr"},
                "log_text": "worker_error",
                "updated_at": _dt().isoformat(),
            },
            {
                "evidence_id": "evidence-2",
                "service_kind": "docker",
                "metadata": {},
                "payload": {"stream": "stderr"},
                "log_text": None,
                "updated_at": _dt().isoformat(),
            },
        ]
    )
    log_chunks = await storage.get_operation_log_chunks(
        "owner-1",
        "op-1",
        query_text="worker",
    )
    incident = await storage.record_operation_incident(
        "owner-1",
        operation_id="op-1",
        service_kind="discord",
        incident_type="discord_delivery_failed",
        severity="high",
        blocking=True,
        root_cause_summary="queue worker stalled",
        recommended_fix="restart queue worker",
        evidence_refs=["evidence-1"],
        metadata={"channel_id": "123"},
    )
    incidents = await storage.list_operation_incidents("owner-1", "op-1", unresolved_only=True)
    owner_incidents = await storage.list_operation_incidents_for_owner(
        "owner-1",
        repo_id="catalyst-group-solutions",
        unresolved_only=True,
    )

    storage.get_managed_operation = AsyncMock(return_value=_operation_row(status="resolved"))  # type: ignore[method-assign]
    storage.list_operation_refs = AsyncMock(return_value=[_op_ref_row()])  # type: ignore[method-assign]
    storage.list_operation_evidence = AsyncMock(return_value=[_evidence_row()])  # type: ignore[method-assign]
    storage.list_operation_incidents = AsyncMock(return_value=[_incident_row()])  # type: ignore[method-assign]
    hydrated = await storage.get_operation_hydrated("owner-1", "op-1")

    assert gap["gap_type"] == "service_evidence_incomplete"
    assert gaps[0]["blocker"] is True
    assert loaded_gap is not None and loaded_gap["gap_id"] == "gap-1"
    assert updated_gap is not None and updated_gap["status"] == "resolved"
    assert capability["service_kind"] == "github"
    assert loaded_capability is not None and loaded_capability["manifest"]["supports_logs"] is True
    assert capabilities[0]["service_kind"] == "github"
    assert operation["correlation_key"] == "deploy:1"
    assert operation_without_key["operation_id"] == "op-2"
    assert updated_operation is not None and updated_operation["status"] == "resolved"
    assert loaded_operation is not None and loaded_operation["lifecycle_stage"] == "verification"
    assert operations[0]["repo_id"] == "catalyst-group-solutions"
    assert found_operation is not None and found_operation["operation_kind"] == "deploy"
    assert operation_ref["dedupe_key"] == f"github:git_sha:{'a' * 40}"
    assert refs[0]["ref_kind"] == "git_sha"
    assert evidence["title"] == "Compose logs"
    assert listed_evidence[0]["service_kind"] == "docker"
    assert log_chunks[0]["stream"] == "stderr"
    assert incident["incident_type"] == "discord_delivery_failed"
    assert incidents[0]["blocking"] is True
    assert owner_incidents[0]["service_kind"] == "discord"
    assert hydrated is not None and hydrated["top_incident"]["incident_id"] == "incident-1"


@pytest.mark.asyncio
async def test_store_worker_observability_and_recompute_run_status_cover_state_transitions(
) -> None:
    conn = _FakeConn(
        fetchrow_results=[
            _run_row(status="review_pending"),
            _repo_row(),
            _run_row(status="planned"),
            _repo_row(),
            _run_row(status="planned"),
            _repo_row(),
            _run_row(status="planned"),
            _repo_row(),
            _run_row(status="review_pending"),
            _repo_row(),
            _run_row(status="planned"),
            _repo_row(),
        ],
        fetch_results=[
            [
                _shard_row(
                    "shard-1",
                    "lane-1",
                    status="failed",
                    execution_target="windows_local",
                )
            ],
            [
                _shard_row(
                    "shard-1",
                    "lane-1",
                    status="running_disconnected",
                    execution_target="windows_local",
                )
            ],
            [
                _shard_row(
                    "shard-1",
                    "lane-1",
                    status="planned",
                    execution_target="windows_local",
                )
            ],
            [
                _shard_row(
                    "shard-1",
                    "lane-1",
                    status="running",
                    execution_target="windows_local",
                )
            ],
            [
                _shard_row(
                    "shard-1",
                    "lane-1",
                    status="succeeded",
                    execution_target="windows_local",
                ),
                _shard_row(
                    "shard-2",
                    "lane-2",
                    status="skipped",
                    execution_target="windows_local",
                ),
            ],
            [
                _shard_row(
                    "shard-1",
                    "lane-1",
                    status="cancelled",
                    execution_target="windows_local",
                ),
                _shard_row(
                    "shard-2",
                    "lane-2",
                    status="cancelled",
                    execution_target="windows_local",
                ),
            ],
        ],
    )
    storage = _storage(conn)

    await storage._store_worker_observability(  # noqa: SLF001
        conn=conn,
        owner_id="owner-1",
        repo_id="zetherion-ai",
        run_id="run-1",
        shard_id="shard-1",
        node_id="windows-main",
        final_status="failed",
        result_json={
            "events": [
                {"event_type": "container.started", "level": "info", "payload": {"name": "bot"}},
                "skip-me",
            ],
            "stdout": "line 1",
            "stderr": "line 2",
            "resource_samples": [
                {
                    "memory_mb": 512,
                    "disk_used_bytes": 2048,
                    "disk_free_bytes": 8192,
                    "container_count": 3,
                }
            ],
            "cleanup_receipt": {"status": "clean"},
            "container_receipts": [{"container": "bot"}],
            "elapsed_ms": 123000,
        },
        error_json={"code": "worker_error"},
    )

    for _ in range(6):
        await storage._recompute_run_status("owner-1", "run-1")  # noqa: SLF001

    summary_insert = next(
        call
        for call in conn.execute_calls
        if "owner_ci_project_usage_summaries" in call[0]
    )
    summary_payload = json.loads(str(summary_insert[1][3]))
    updated_statuses = [
        str(call[1][2])
        for call in conn.execute_calls
        if call[0].lstrip().startswith("UPDATE")
    ]

    assert any("worker.result.accepted" in str(call[1]) for call in conn.execute_calls)
    assert summary_payload["container_count"] == 3
    assert summary_payload["cleanup_status"] == "clean"
    assert updated_statuses == [
        "failed",
        "awaiting_sync",
        "queued_local",
        "running",
        "ready_to_merge",
        "cancelled",
    ]


@pytest.mark.asyncio
async def test_recompute_run_status_accepts_string_backed_review_receipts() -> None:
    run_row = _run_row(status="review_pending")
    run_row["review_receipts"] = json.dumps({"merge_blocked": False})
    conn = _FakeConn(
        fetchrow_results=[run_row, _repo_row()],
        fetch_results=[
            [
                _shard_row(
                    "shard-1",
                    "lane-1",
                    status="succeeded",
                    execution_target="windows_local",
                ),
                _shard_row(
                    "shard-2",
                    "lane-2",
                    status="skipped",
                    execution_target="windows_local",
                ),
            ]
        ],
    )
    storage = _storage(conn)

    await storage._recompute_run_status("owner-1", "run-1")  # noqa: SLF001

    status_update = next(
        call
        for call in conn.execute_calls
        if call[0].lstrip().startswith("UPDATE")
    )
    assert status_update[1][2] == "ready_to_merge"


@pytest.mark.asyncio
async def test_worker_node_session_lifecycle_and_listing_methods_round_trip() -> None:
    conn = _FakeConn(
        fetchrow_results=[
            _worker_session_row(),
            _worker_session_row(),
            _worker_node_row(),
            _worker_node_row(),
            _worker_node_row(),
        ],
        fetch_results=[
            [_worker_node_row()],
            [_worker_job_row()],
        ],
    )
    storage = _storage(conn)

    session = await storage.bootstrap_worker_node_session(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        node_name="Windows worker",
        capabilities=["docker"],
        metadata={"pool": "local"},
        session_ttl_seconds=900,
        session_id="sess-1",
        session_token="token-1",
        signing_secret="signing-secret",
    )
    rotated = await storage.rotate_worker_session_credentials(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        session_id="sess-1",
        session_ttl_seconds=900,
    )
    auth = await storage.get_worker_session_auth(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        session_id="sess-1",
    )
    await storage.touch_worker_session(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        session_id="sess-1",
    )
    node = await storage.register_worker_node(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        node_name="Windows worker",
        capabilities=["docker"],
        metadata={"pool": "local"},
    )
    heartbeat = await storage.heartbeat_worker_node(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        health_status="healthy",
        metadata={"pool": "local"},
    )
    nodes = await storage.list_worker_nodes("owner:owner-1:repo:zetherion-ai")
    loaded_node = await storage.get_worker_node("owner:owner-1:repo:zetherion-ai", "node-1")
    jobs = await storage.list_worker_jobs(
        "owner:owner-1:repo:zetherion-ai",
        status="queued",
    )

    assert session["session_id"] == "sess-1"
    assert session["token"] == "token-1"
    assert rotated["session_id"] == "sess-1"
    assert rotated["token"]
    assert auth is not None and auth["signing_secret"] == "signing-secret"
    assert node["node_id"] == "node-1"
    assert heartbeat["health_status"] == "healthy"
    assert nodes[0]["node_name"] == "Windows worker"
    assert loaded_node is not None and loaded_node["status"] == "active"
    assert jobs[0]["job_id"] == "job-1"
    assert any("INSERT INTO" in call[0] for call in conn.execute_calls)


@pytest.mark.asyncio
async def test_claim_worker_job_and_submit_result_cover_success_and_idempotent_replay() -> None:
    queued_job = _worker_job_row()
    claimed_job = {
        **queued_job,
        "status": "claimed",
        "claimed_by_node_id": "node-1",
        "claimed_session_id": "sess-1",
    }
    completed_job = {
        **queued_job,
        "status": "completed",
        "claimed_by_node_id": "node-1",
        "claimed_session_id": "sess-1",
        "result_json": {"summary": "ok"},
        "error_json": {},
    }

    conn = _FakeConn(
        fetchrow_results=[
            claimed_job,
            claimed_job,
            completed_job,
            completed_job,
        ],
        fetch_results=[[queued_job]],
    )
    storage = _storage(conn)
    storage._store_worker_observability = AsyncMock()  # type: ignore[method-assign]
    storage._recompute_run_status = AsyncMock()  # type: ignore[method-assign]

    claimed = await storage.claim_worker_job(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        required_capabilities=["ci.test.run"],
        session_id="sess-1",
    )
    completed = await storage.submit_worker_job_result(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        job_id="job-1",
        payload={
            "status": "succeeded",
            "output": {"summary": "ok"},
            "error": {},
            "idempotency_key": "run-1:shard-win",
        },
    )
    replayed = await storage.submit_worker_job_result(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        job_id="job-1",
        payload={
            "status": "succeeded",
            "output": {"summary": "ok"},
            "error": {},
            "idempotency_key": "run-1:shard-win",
        },
    )

    assert claimed is not None and claimed["status"] == "claimed"
    assert completed["status"] == "completed"
    assert completed["idempotent"] is False
    assert replayed["idempotent"] is True
    storage._store_worker_observability.assert_awaited_once()  # type: ignore[attr-defined]
    assert storage._recompute_run_status.await_count == 2  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_rotate_worker_session_credentials_raises_when_session_is_missing() -> None:
    storage = _storage(_FakeConn())

    with pytest.raises(ValueError, match="Worker session not found"):
        await storage.rotate_worker_session_credentials(
            scope_id="owner:owner-1:repo:zetherion-ai",
            node_id="node-1",
            session_id="missing-session",
            session_ttl_seconds=900,
        )


@pytest.mark.asyncio
async def test_claim_worker_job_returns_none_when_node_lacks_required_capabilities() -> None:
    conn = _FakeConn(fetch_results=[[{**_worker_job_row(), "required_capabilities": ["docker"]}]])
    storage = _storage(conn)
    storage._recompute_run_status = AsyncMock()  # type: ignore[method-assign]

    claimed = await storage.claim_worker_job(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        required_capabilities=["ci.test.run"],
        session_id="sess-1",
    )

    assert claimed is None
    storage._recompute_run_status.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("row", "node_id", "payload", "message"),
    [
        (
            None,
            "node-1",
            {"status": "failed"},
            "Worker job not found",
        ),
        (
            {
                **_worker_job_row(),
                "status": "claimed",
                "claimed_by_node_id": "node-2",
            },
            "node-1",
            {"status": "failed"},
            "Worker job was claimed by a different node",
        ),
        (
            {
                **_worker_job_row(),
                "status": "completed",
                "claimed_by_node_id": "node-1",
                "result_json": {"summary": "done"},
            },
            "node-1",
            {"status": "succeeded", "idempotency_key": "other-key"},
            "Worker job already completed",
        ),
    ],
)
async def test_submit_worker_job_result_rejects_invalid_worker_job_states(
    row: dict[str, object] | None,
    node_id: str,
    payload: dict[str, object],
    message: str,
) -> None:
    storage = _storage(_FakeConn(fetchrow_results=[row] if row is not None else [None]))

    with pytest.raises(ValueError, match=message):
        await storage.submit_worker_job_result(
            scope_id="owner:owner-1:repo:zetherion-ai",
            node_id=node_id,
            job_id="job-1",
            payload=payload,
        )
