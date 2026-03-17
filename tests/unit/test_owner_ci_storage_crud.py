"""Focused coverage for owner-CI storage CRUD and reporting methods."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

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
async def test_repo_profile_and_plan_snapshot_methods_cover_no_row_and_version_paths() -> None:
    no_repo_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Upsert repo profile returned no row"):
        await no_repo_storage.upsert_repo_profile(
            "owner-1",
            {
                "repo_id": "zetherion-ai",
                "display_name": "Zetherion AI",
                "github_repo": "jimtin/zetherion-ai",
                "stack_kind": "python",
            },
        )

    no_plan_storage = _storage(_FakeConn(fetchrow_results=[{"current_version": 0}, None]))
    with pytest.raises(RuntimeError, match="Create plan snapshot returned no row"):
        await no_plan_storage.create_plan_snapshot(
            owner_id="owner-1",
            repo_id="zetherion-ai",
            title="Plan",
            content_markdown="# Plan",
            tags=["repair"],
            plan_id="plan-1",
        )

    version_conn = _FakeConn(fetchrow_results=[_plan_row(1)])
    version_storage = _storage(version_conn)
    versioned = await version_storage.get_plan_snapshot("owner-1", "plan-1", version=1)

    assert versioned is not None
    assert versioned["version"] == 1
    assert version_conn.fetchrow_calls[0][1] == ("owner-1", "plan-1", 1)


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
async def test_create_run_covers_missing_rows_and_worker_payload_fallback_metadata() -> None:
    missing_run_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Create run returned no row"):
        await missing_run_storage.create_run(
            owner_id="owner-1",
            scope_id="owner:owner-1:repo:zetherion-ai",
            repo_id="zetherion-ai",
            git_ref="main",
            trigger="manual",
            plan={"compiled_plan": {"compiled_plan_id": "compiled-1"}},
            metadata={},
            shards=[],
        )

    missing_shard_storage = _storage(_FakeConn(fetchrow_results=[_run_row(), None]))
    with pytest.raises(RuntimeError, match="Create shard returned no row"):
        await missing_shard_storage.create_run(
            owner_id="owner-1",
            scope_id="owner:owner-1:repo:zetherion-ai",
            repo_id="zetherion-ai",
            git_ref="main",
            trigger="manual",
            plan={"compiled_plan": {"compiled_plan_id": "compiled-1"}},
            metadata={},
            shards=[
                {
                    "shard_id": "shard-win",
                    "lane_id": "windows-dispatch",
                    "execution_target": "windows_local",
                }
            ],
        )

    conn = _FakeConn(
        fetchrow_results=[
            _run_row(status="queued_local"),
            _shard_row(
                "shard-win",
                "windows-dispatch",
                status="queued_local",
                execution_target="windows_local",
            ),
        ]
    )
    storage = _storage(conn)
    storage.get_run = AsyncMock(return_value={**_run_row(status="queued_local"), "shards": []})  # type: ignore[method-assign]

    created = await storage.create_run(
        owner_id="owner-1",
        scope_id="owner:owner-1:repo:zetherion-ai",
        repo_id="zetherion-ai",
        git_ref="main",
        trigger="manual",
        plan={"compiled_plan": {"compiled_plan_id": "compiled-1"}},
        metadata={},
        shards=[
            {
                "shard_id": "shard-win",
                "lane_id": "windows-dispatch",
                "execution_target": "windows_local",
                "runner": "docker",
                "command": ["pytest", "-q"],
                "payload": {
                    "container_spec": {"image": "python:3.12"},
                    "compose_project": "zetherion-ci",
                    "cleanup_labels": {"app": "zetherion"},
                    "network_contract": {"service": "discord"},
                },
                "metadata": {"resource_class": "service", "parallel_group": "discord"},
            }
        ],
    )

    assert created["status"] == "queued_local"
    payload = json.loads(str(conn.execute_calls[0][1][8]))
    assert payload["container_spec"] == {"image": "python:3.12"}
    assert payload["compose_project"] == "zetherion-ci"
    assert payload["cleanup_labels"] == {"app": "zetherion"}
    assert payload["network_contract"] == {"service": "discord"}
    assert payload["resource_class"] == "service"
    assert payload["parallel_group"] == "discord"


@pytest.mark.asyncio
async def test_create_run_promotes_top_level_container_spec_into_worker_payload() -> None:
    conn = _FakeConn(
        fetchrow_results=[
            _run_row(status="queued_local"),
            _shard_row(
                "shard-win",
                "windows-dispatch",
                status="queued_local",
                execution_target="windows_local",
            ),
        ]
    )
    storage = _storage(conn)
    storage.get_run = AsyncMock(return_value={**_run_row(status="queued_local"), "shards": []})  # type: ignore[method-assign]

    created = await storage.create_run(
        owner_id="owner-1",
        scope_id="owner:owner-1:repo:zetherion-ai",
        repo_id="zetherion-ai",
        git_ref="main",
        trigger="manual",
        plan={"compiled_plan": {"compiled_plan_id": "compiled-1"}},
        metadata={},
        shards=[
            {
                "shard_id": "shard-win",
                "lane_id": "windows-dispatch",
                "execution_target": "windows_local",
                "runner": "docker",
                "command": ["pytest", "-q"],
                "container_spec": {"image": "python:3.12"},
                "payload": {
                    "compose_project": "zetherion-ci",
                    "cleanup_labels": {"app": "zetherion"},
                    "network_contract": {"service": "discord"},
                },
                "metadata": {"resource_class": "service", "parallel_group": "discord"},
            }
        ],
    )

    assert created["status"] == "queued_local"
    payload = json.loads(str(conn.execute_calls[0][1][8]))
    assert payload["container_spec"] == {"image": "python:3.12"}
    assert payload["compose_project"] == "zetherion-ci"
    assert payload["cleanup_labels"] == {"app": "zetherion"}
    assert payload["network_contract"] == {"service": "discord"}


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
async def test_run_accessors_cover_optional_filters_and_missing_update_rows() -> None:
    list_conn = _FakeConn(fetch_results=[[_run_row(status="queued_local")]])
    list_storage = _storage(list_conn)

    listed = await list_storage.list_runs("owner-1", repo_id="zetherion-ai", limit=5)

    assert listed[0]["repo_id"] == "zetherion-ai"
    assert list_conn.fetch_calls[0][1] == ("owner-1", "zetherion-ai", 5)

    missing_run_storage = _storage(_FakeConn(fetchrow_results=[None]))
    assert await missing_run_storage.get_run("owner-1", "missing-run") is None

    review_storage = _storage(_FakeConn(fetchrow_results=[None]))
    review_storage._recompute_run_status = AsyncMock()  # type: ignore[attr-defined]
    assert (
        await review_storage.store_run_review(
            "owner-1",
            "missing-run",
            {"verdict": "approved"},
        )
        is None
    )
    review_storage._recompute_run_status.assert_not_awaited()  # type: ignore[attr-defined]

    github_missing_current = _storage(_FakeConn(fetchrow_results=[None]))
    github_missing_current._recompute_run_status = AsyncMock()  # type: ignore[attr-defined]
    assert (
        await github_missing_current.store_run_github_receipt(
            "owner-1",
            "missing-run",
            {"merge_readiness": {"state": "success"}},
        )
        is None
    )
    github_missing_current._recompute_run_status.assert_not_awaited()  # type: ignore[attr-defined]

    github_missing_update = _storage(_FakeConn(fetchrow_results=[{"github_receipts": {}}, None]))
    github_missing_update._recompute_run_status = AsyncMock()  # type: ignore[attr-defined]
    assert (
        await github_missing_update.store_run_github_receipt(
            "owner-1",
            "run-1",
            {"merge_readiness": {"state": "success"}},
        )
        is None
    )
    github_missing_update._recompute_run_status.assert_not_awaited()  # type: ignore[attr-defined]

    metadata_storage = _storage(_FakeConn(fetchrow_results=[None]))
    assert (
        await metadata_storage.merge_run_metadata(
            "owner-1",
            "missing-run",
            {"release_verification": {"status": "healthy"}},
        )
        is None
    )


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
async def test_plan_schedule_and_run_query_helpers_cover_filter_and_error_branches() -> None:
    jobs_conn = _FakeConn(fetch_results=[[_worker_job_row()]])
    jobs_storage = _storage(jobs_conn)

    jobs = await jobs_storage.list_worker_jobs(
        "owner:owner-1:repo:zetherion-ai",
        status="queued",
        limit=1,
    )

    assert jobs[0]["job_id"] == "job-1"
    assert jobs_conn.fetch_calls[0][1] == (
        "owner:owner-1:repo:zetherion-ai",
        "queued",
        1,
    )

    missing_plan_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Create compiled plan returned no row"):
        await missing_plan_storage.create_compiled_plan(
            owner_id="owner-1",
            repo_id="zetherion-ai",
            git_ref="main",
            mode="certification",
            plan={"shards": []},
            metadata={},
        )

    missing_compiled_storage = _storage(_FakeConn(fetchrow_results=[None]))
    assert await missing_compiled_storage.get_compiled_plan("owner-1", "missing") is None

    compiled_list_conn = _FakeConn(fetch_results=[[_compiled_plan_row()]])
    compiled_list_storage = _storage(compiled_list_conn)

    compiled_plans = await compiled_list_storage.list_compiled_plans(
        "owner-1",
        repo_id="zetherion-ai",
        limit=2,
    )

    assert compiled_plans[0]["compiled_plan_id"] == "compiled-1"
    assert compiled_list_conn.fetch_calls[0][1] == ("owner-1", "zetherion-ai", 2)

    missing_schedule_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Upsert schedule returned no row"):
        await missing_schedule_storage.upsert_schedule(
            owner_id="owner-1",
            repo_id="zetherion-ai",
            name="Daily",
            schedule_kind="weekly",
            schedule_spec={"hour": 9},
        )

    schedule_list_conn = _FakeConn(fetch_results=[[_schedule_row()]])
    schedule_list_storage = _storage(schedule_list_conn)

    schedules = await schedule_list_storage.list_schedules(
        "owner-1",
        repo_id="zetherion-ai",
    )

    assert schedules[0]["schedule_id"] == "schedule-1"
    assert schedule_list_conn.fetch_calls[0][1] == ("owner-1", "zetherion-ai")

    event_conn = _FakeConn(
        fetch_results=[
            [
                {
                    "event_type": "shard.started",
                    "payload_json": {"status": "running"},
                }
            ]
        ]
    )
    event_storage = _storage(event_conn)
    event_storage._event_from_row = lambda row: {  # type: ignore[method-assign]
        "event_type": row["event_type"],
        "payload": row["payload_json"],
    }

    events = await event_storage.get_run_events(
        "owner-1",
        "run-1",
        shard_id="shard-1",
        limit=3,
    )

    assert events == [{"event_type": "shard.started", "payload": {"status": "running"}}]
    assert event_conn.fetch_calls[0][1] == ("owner-1", "run-1", "shard-1", 3)

    log_conn = _FakeConn(
        fetch_results=[
            [
                {
                    "message_value": "needle found",
                }
            ]
        ]
    )
    log_storage = _storage(log_conn)
    log_storage._log_chunk_from_row = lambda row: {  # type: ignore[method-assign]
        "message": row["message_value"]
    }

    logs = await log_storage.get_run_log_chunks(
        "owner-1",
        "run-1",
        shard_id="shard-1",
        query_text="needle",
        limit=4,
    )

    assert logs == [{"message": "needle found"}]
    assert log_conn.fetch_calls[0][1] == (
        "owner-1",
        "run-1",
        "shard-1",
        "%needle%",
        4,
    )

    debug_missing_storage = _storage(_FakeConn(fetchrow_results=[None]))
    debug_missing_storage._debug_bundle_from_row = lambda row: row  # type: ignore[method-assign]
    assert (
        await debug_missing_storage.get_run_debug_bundle(
            "owner-1",
            "run-1",
            shard_id="shard-1",
        )
        is None
    )

    debug_conn = _FakeConn(fetchrow_results=[{"bundle_path": "/tmp/bundle.json"}])
    debug_storage = _storage(debug_conn)
    debug_storage._debug_bundle_from_row = lambda row: {  # type: ignore[method-assign]
        "bundle_path": row["bundle_path"]
    }

    debug_bundle = await debug_storage.get_run_debug_bundle(
        "owner-1",
        "run-1",
        shard_id="shard-1",
    )

    assert debug_bundle == {"bundle_path": "/tmp/bundle.json"}
    assert debug_conn.fetchrow_calls[0][1] == ("owner-1", "run-1", "shard-1")


@pytest.mark.asyncio
async def test_run_query_helpers_cover_unfiltered_paths() -> None:
    jobs_conn = _FakeConn(fetch_results=[[_worker_job_row()]])
    jobs_storage = _storage(jobs_conn)
    jobs = await jobs_storage.list_worker_jobs("owner:owner-1:repo:zetherion-ai")

    assert jobs[0]["job_id"] == "job-1"
    assert jobs_conn.fetch_calls[0][1] == ("owner:owner-1:repo:zetherion-ai", 100)

    event_conn = _FakeConn(fetch_results=[[{"event_type": "run.started", "payload_json": {}}]])
    event_storage = _storage(event_conn)
    event_storage._event_from_row = lambda row: row  # type: ignore[method-assign]
    events = await event_storage.get_run_events("owner-1", "run-1")

    assert events == [{"event_type": "run.started", "payload_json": {}}]
    assert event_conn.fetch_calls[0][1] == ("owner-1", "run-1", 200)

    log_conn = _FakeConn(fetch_results=[[{"message_value": "line-1"}]])
    log_storage = _storage(log_conn)
    log_storage._log_chunk_from_row = lambda row: row  # type: ignore[method-assign]
    logs = await log_storage.get_run_log_chunks("owner-1", "run-1")

    assert logs == [{"message_value": "line-1"}]
    assert log_conn.fetch_calls[0][1] == ("owner-1", "run-1", 500)

    debug_conn = _FakeConn(fetchrow_results=[{"bundle_path": "/tmp/run-bundle.json"}])
    debug_storage = _storage(debug_conn)
    debug_storage._debug_bundle_from_row = lambda row: row  # type: ignore[method-assign]
    debug_bundle = await debug_storage.get_run_debug_bundle("owner-1", "run-1")

    assert debug_bundle == {"bundle_path": "/tmp/run-bundle.json"}
    assert debug_conn.fetchrow_calls[0][1] == ("owner-1", "run-1")


@pytest.mark.asyncio
async def test_run_report_and_projection_accessors_cover_missing_and_node_filtered_paths() -> None:
    missing_storage = _storage(_FakeConn())
    missing_storage.get_run = AsyncMock(return_value=None)  # type: ignore[method-assign]

    assert await missing_storage.get_run_report("owner-1", "missing-run") is None

    report_storage = _storage(_FakeConn())
    report_storage.get_run = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "run_id": "run-1",
            "repo_id": "zetherion-ai",
            "metadata": {"principal_id": "codex-1"},
        }
    )
    report_storage.get_run_log_chunks = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"message": "hello"}]
    )
    report_storage.get_run_debug_bundle = AsyncMock(  # type: ignore[method-assign]
        return_value={"bundle_path": "/tmp/bundle.json"}
    )
    report_storage.list_agent_coaching_feedback = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"feedback_id": "feedback-1"}]
    )

    from zetherion_ai.owner_ci import storage as storage_module

    base_report = {
        "run_id": "run-1",
        "run_graph": {"nodes": [{"node_id": "run:run-1"}]},
        "correlation_context": {"trace_ids": ["trace-1"]},
        "diagnostic_summary": {"status": "failed"},
        "diagnostic_findings": [{"finding_id": "finding-1"}],
        "diagnostic_artifacts": [{"artifact_id": "diag-1"}],
        "coverage_summary": {"branches": 84.22},
        "coverage_gaps": {"gaps": [{"file": "storage.py"}]},
        "correlated_incidents": [{"incident_id": "incident-1"}],
        "artifacts": [{"artifact_id": "artifact-1"}],
        "evidence": [{"evidence_ref_id": "evidence-1"}],
    }
    filtered_report = {
        **base_report,
        "run_graph": {"nodes": [{"node_id": "shard:shard-1"}]},
        "artifacts": [{"artifact_id": "artifact-node"}],
        "evidence": [{"evidence_ref_id": "evidence-node"}],
    }

    with patch.object(
        storage_module,
        "build_run_report",
        return_value=base_report,
    ) as build_report, patch.object(
        storage_module,
        "_filter_run_report_for_node",
        return_value=filtered_report,
    ) as filter_report:
        report = await report_storage.get_run_report(
            "owner-1",
            "run-1",
            shard_id="shard-1",
            log_limit=12,
        )
        filtered = await report_storage.get_run_report(
            "owner-1",
            "run-1",
            shard_id="shard-1",
            node_id="node-1",
            log_limit=12,
        )

    assert report == base_report
    assert filtered == filtered_report
    build_report.assert_called()
    filter_report.assert_called_once_with(base_report, node_id="node-1")
    report_storage.get_run_log_chunks.assert_awaited_with(  # type: ignore[attr-defined]
        "owner-1",
        "run-1",
        shard_id="shard-1",
        limit=12,
    )
    report_storage.get_run_debug_bundle.assert_awaited_with(  # type: ignore[attr-defined]
        "owner-1",
        "run-1",
        shard_id="shard-1",
    )
    report_storage.list_agent_coaching_feedback.assert_awaited_with(  # type: ignore[attr-defined]
        "owner-1",
        principal_id="codex-1",
        repo_id="zetherion-ai",
        run_id="run-1",
        limit=50,
    )

    projection_storage = _storage(_FakeConn())
    projection_storage.get_run_report = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"run_graph": {"nodes": ["run-node"]}},
            None,
            {"correlation_context": {"trace_ids": ["trace-1"]}},
            None,
            {
                "diagnostic_summary": {"status": "failed"},
                "diagnostic_findings": [{"finding_id": "finding-1"}],
                "diagnostic_artifacts": [{"artifact_id": "diag-1"}],
                "coverage_summary": {"branches": 84.22},
                "coverage_gaps": {"gaps": [{"file": "storage.py"}]},
                "correlated_incidents": [{"incident_id": "incident-1"}],
            },
            None,
            {"artifacts": [{"artifact_id": "artifact-1"}]},
            None,
            {"evidence": [{"evidence_ref_id": "evidence-1"}]},
            None,
        ]
    )

    assert await projection_storage.get_run_graph("owner-1", "run-1") == {
        "nodes": ["run-node"]
    }
    assert await projection_storage.get_run_graph("owner-1", "missing-run") is None
    assert await projection_storage.get_run_correlation_context("owner-1", "run-1") == {
        "trace_ids": ["trace-1"]
    }
    assert (
        await projection_storage.get_run_correlation_context("owner-1", "missing-run")
        is None
    )
    assert await projection_storage.get_run_diagnostics(
        "owner-1",
        "run-1",
        node_id="node-1",
    ) == {
        "diagnostic_summary": {"status": "failed"},
        "diagnostic_findings": [{"finding_id": "finding-1"}],
        "diagnostic_artifacts": [{"artifact_id": "diag-1"}],
        "coverage_summary": {"branches": 84.22},
        "coverage_gaps": {"gaps": [{"file": "storage.py"}]},
        "correlated_incidents": [{"incident_id": "incident-1"}],
    }
    assert await projection_storage.get_run_diagnostics("owner-1", "missing-run") is None
    assert await projection_storage.get_run_artifacts("owner-1", "run-1") == [
        {"artifact_id": "artifact-1"}
    ]
    assert await projection_storage.get_run_artifacts("owner-1", "missing-run") == []
    assert await projection_storage.get_run_evidence("owner-1", "run-1") == [
        {"evidence_ref_id": "evidence-1"}
    ]
    assert await projection_storage.get_run_evidence("owner-1", "missing-run") == []
    assert projection_storage.get_run_report.await_args_list[4].kwargs == {  # type: ignore[attr-defined]
        "node_id": "node-1"
    }


@pytest.mark.asyncio
async def test_coaching_feedback_and_local_readiness_delegate_cover_storage_wrappers() -> None:
    coaching_storage = _storage(_FakeConn())
    coaching_storage.list_agent_gap_events = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "gap_id": "gap-coaching",
                "principal_id": "codex-1",
                "repo_id": "catalyst-group-solutions",
                "run_id": "run-1",
                "status": "open",
                "blocker": True,
                "occurrence_count": 2,
                "gap_type": "agent_instruction_update",
                "suggested_fix": "Update AGENTS.md to require gitleaks.",
                "metadata": {
                    "record_kind": "agent_coaching",
                    "coaching_kind": "preflight",
                    "rule_code": "missing_gitleaks",
                    "summary": "Missing gitleaks preflight attestation.",
                    "findings": [
                        {
                            "finding_id": "finding-1",
                            "rule_code": "missing_gitleaks",
                            "summary": "Missing gitleaks preflight attestation.",
                            "remediation": "Run gitleaks before certification.",
                            "blocking": True,
                        }
                    ],
                    "recommendations": [
                        {
                            "title": "Update AGENTS.md",
                            "instructions": ["Require gitleaks in the common checks block."],
                            "agents_md_update": "- Run gitleaks before certification.",
                        }
                    ],
                },
            }
        ]
    )

    feedback = await coaching_storage.list_agent_coaching_feedback(
        "owner-1",
        principal_id="codex-1",
        repo_id="catalyst-group-solutions",
        run_id="run-1",
        limit=25,
    )

    assert feedback[0]["principal_id"] == "codex-1"
    assert feedback[0]["recommendations"][0]["title"] == "Update AGENTS.md"
    coaching_storage.list_agent_gap_events.assert_awaited_once_with(  # type: ignore[attr-defined]
        "owner-1",
        principal_id="codex-1",
        repo_id="catalyst-group-solutions",
        run_id="run-1",
        unresolved_only=False,
        limit=25,
    )

    readiness_storage = _storage(_FakeConn())
    readiness, payload = await readiness_storage.get_local_repo_readiness(
        {"repo_id": "zetherion-ai", "allowed_paths": []}
    )

    assert readiness is None
    assert payload is None


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
async def test_agent_bootstrap_connector_and_knowledge_pack_error_branches() -> None:
    manifest_missing_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Store agent bootstrap manifest returned no row"):
        await manifest_missing_storage.store_agent_bootstrap_manifest(
            "owner-1",
            "codex-desktop",
            {"version": "v2"},
        )

    manifest_lookup_storage = _storage(_FakeConn(fetchrow_results=[None]))
    assert (
        await manifest_lookup_storage.get_agent_bootstrap_manifest("owner-1", "codex-desktop")
        is None
    )

    receipt_missing_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Store agent setup receipt returned no row"):
        await receipt_missing_storage.store_agent_setup_receipt(
            "owner-1",
            client_id="codex-desktop",
            receipt={"status": "stored"},
        )

    docs_missing_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Upsert agent docs manifest returned no row"):
        await docs_missing_storage.upsert_agent_docs_manifest(
            "owner-1",
            slug="doc-1",
            title="Quickstart",
            manifest={"content_markdown": "# Quickstart"},
        )

    docs_lookup_storage = _storage(_FakeConn(fetchrow_results=[None]))
    assert await docs_lookup_storage.get_agent_docs_manifest("owner-1", "doc-1") is None

    principal_missing_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Upsert agent principal returned no row"):
        await principal_missing_storage.upsert_agent_principal(
            "owner-1",
            principal_id="codex-1",
            display_name="Codex 1",
        )

    connector_without_secret_conn = _FakeConn(fetchrow_results=[_connector_row(has_secret=False)])
    connector_without_secret_storage = _storage(connector_without_secret_conn)
    connector_without_secret = (
        await connector_without_secret_storage.upsert_external_service_connector(
            "owner-1",
            connector_id="github-primary",
            service_kind="github",
            display_name="GitHub",
            auth_kind="token",
            policy={"read_access": ["branch_metadata"]},
            metadata={"scope": "repo"},
        )
    )

    assert connector_without_secret["has_secret"] is False
    assert connector_without_secret_conn.fetchrow_calls[0][1] == (
        "owner-1",
        "github-primary",
        "github",
        "GitHub",
        "token",
        json.dumps({"read_access": ["branch_metadata"]}),
        json.dumps({"scope": "repo"}),
        True,
    )

    connector_missing_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Upsert external service connector returned no row"):
        await connector_missing_storage.upsert_external_service_connector(
            "owner-1",
            connector_id="github-primary",
            service_kind="github",
            display_name="GitHub",
            auth_kind="token",
        )

    connector_secret_lookup_storage = _storage(_FakeConn(fetchrow_results=[None]))
    assert (
        await connector_secret_lookup_storage.get_external_service_connector_with_secret(
            "owner-1",
            "github-primary",
        )
        is None
    )

    connector_list_conn = _FakeConn(
        fetch_results=[[_connector_row(), _connector_row("vercel-primary", service_kind="vercel")]]
    )
    connector_list_storage = _storage(connector_list_conn)
    listed_connectors = await connector_list_storage.list_external_service_connectors("owner-1")

    assert [connector["connector_id"] for connector in listed_connectors] == [
        "github-primary",
        "vercel-primary",
    ]
    assert connector_list_conn.fetch_calls[0][1] == ("owner-1",)

    grant_list_conn = _FakeConn(fetch_results=[[_grant_row("grant-1"), _grant_row("grant-2")]])
    grant_list_storage = _storage(grant_list_conn)
    listed_grants = await grant_list_storage.list_external_access_grants("owner-1")

    assert [grant["grant_key"] for grant in listed_grants] == ["grant-1", "grant-2"]
    assert grant_list_conn.fetch_calls[0][1] == ("owner-1",)

    app_profile_missing_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Upsert agent app profile returned no row"):
        await app_profile_missing_storage.upsert_agent_app_profile(
            "owner-1",
            app_id="catalyst-group-solutions",
            display_name="Catalyst Group Solutions",
            profile={"repo_ids": ["catalyst-group-solutions"]},
        )

    non_current_pack_conn = _FakeConn(fetchrow_results=[_pack_row(version="v2", current=False)])
    non_current_pack_storage = _storage(non_current_pack_conn)
    non_current_pack = await non_current_pack_storage.upsert_agent_knowledge_pack(
        "owner-1",
        app_id="catalyst-group-solutions",
        version="v2",
        pack={"workspace_manifest": {"repo_id": "catalyst-group-solutions"}},
        current=False,
    )

    assert non_current_pack["version"] == "v2"
    assert non_current_pack["current"] is False
    assert non_current_pack_conn.execute_calls == []

    missing_pack_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Upsert agent knowledge pack returned no row"):
        await missing_pack_storage.upsert_agent_knowledge_pack(
            "owner-1",
            app_id="catalyst-group-solutions",
            version="v3",
            pack={"workspace_manifest": {"repo_id": "catalyst-group-solutions"}},
            current=False,
        )


@pytest.mark.asyncio
async def test_agent_session_and_interaction_error_and_optional_touch_branches() -> None:
    missing_session_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Create agent session returned no row"):
        await missing_session_storage.create_agent_session(
            "owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
        )

    interaction_storage = _storage(_FakeConn(fetchrow_results=[_interaction_row("int-optional")]))
    interaction_storage.touch_agent_session = AsyncMock()  # type: ignore[method-assign]

    interaction = await interaction_storage.create_agent_interaction(
        "owner-1",
        session_id=None,
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        repo_id="catalyst-group-solutions",
        route_path=None,
        intent="diagnostics.read",
        request_text=None,
        request_payload=None,
        normalized_intent=None,
    )

    assert interaction["interaction_id"] == "int-optional"
    interaction_storage.touch_agent_session.assert_not_awaited()  # type: ignore[attr-defined]

    missing_interaction_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Create agent interaction returned no row"):
        await missing_interaction_storage.create_agent_interaction(
            "owner-1",
            session_id="sess-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            repo_id="catalyst-group-solutions",
            route_path="/agent",
            intent="diagnostics.read",
            request_text="show me",
            request_payload={"view": "run"},
        )

    missing_action_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Create agent action returned no row"):
        await missing_action_storage.create_agent_action(
            "owner-1",
            interaction_id="int-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            action="publish",
            status="requested",
        )

    missing_outcome_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Create agent outcome returned no row"):
        await missing_outcome_storage.create_agent_outcome(
            "owner-1",
            interaction_id="int-1",
            action_record_id=None,
            status="failed",
            summary="no row",
        )


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
async def test_workspace_publish_session_and_secret_error_branches() -> None:
    missing_bundle_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Create workspace bundle returned no row"):
        await missing_bundle_storage.create_workspace_bundle(
            "owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            repo_id="catalyst-group-solutions",
            git_ref="main",
            bundle={"download_mode": "inline_base64"},
        )

    missing_candidate_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Create publish candidate returned no row"):
        await missing_candidate_storage.create_publish_candidate(
            "owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            repo_id="catalyst-group-solutions",
            base_sha="a" * 40,
            candidate={"diff_text": "diff --git"},
        )

    missing_secret_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Upsert secret ref returned no row"):
        await missing_secret_storage.upsert_secret_ref(
            "owner-1",
            secret_ref_id="secret-1",
            purpose="api-token",
            secret_value="secret",
        )

    missing_audit_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Record agent audit event returned no row"):
        await missing_audit_storage.record_agent_audit_event(
            "owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            service_kind="github",
            resource="repo",
            action="read",
            decision="allowed",
            audit={"reason": "brokered"},
        )

    audit_list_conn = _FakeConn(fetch_results=[[_audit_row()]])
    audit_list_storage = _storage(audit_list_conn)
    audit_events = await audit_list_storage.list_agent_audit_events("owner-1")

    assert audit_events[0]["audit_id"] == "audit-1"
    assert audit_list_conn.fetch_calls[0][1] == ("owner-1", 100)


@pytest.mark.asyncio
async def test_external_access_grant_and_gap_query_branch_paths() -> None:
    replace_conn = _FakeConn(
        fetchrow_results=[None, {**_grant_row("grant-2"), "resource_type": "repo"}]
    )
    replace_storage = _storage(replace_conn)

    grants = await replace_storage.replace_external_access_grants(
        "owner-1",
        principal_id="codex-1",
        grants=[
            {"resource_type": "app", "resource_id": "skip-missing-row"},
            {"resource_type": "repo", "resource_id": "jimtin/zetherion-ai"},
        ],
    )

    assert [grant["grant_key"] for grant in grants] == ["grant-2"]

    grant_list_conn = _FakeConn(fetch_results=[[_grant_row("grant-1")]])
    grant_list_storage = _storage(grant_list_conn)
    listed_grants = await grant_list_storage.list_external_access_grants(
        "owner-1",
        principal_id="codex-1",
    )

    assert listed_grants[0]["grant_key"] == "grant-1"
    assert grant_list_conn.fetch_calls[0][1] == ("owner-1", "codex-1")
    assert "principal_id = $2" in grant_list_conn.fetch_calls[0][0]

    gap_list_conn = _FakeConn(fetch_results=[[_gap_row(status="open")]])
    gap_list_storage = _storage(gap_list_conn)
    gaps = await gap_list_storage.list_agent_gap_events(
        "owner-1",
        session_id="sess-1",
        principal_id="codex-1",
        app_id="app-1",
        repo_id="repo-1",
        run_id="run-1",
        status="open",
        unresolved_only=True,
        blocker_only=True,
        limit=0,
    )

    assert gaps[0]["status"] == "open"
    query, args = gap_list_conn.fetch_calls[0]
    assert "run_id = $6" in query
    assert "status = $7" in query
    assert "status <> 'resolved'" not in query
    assert args == ("owner-1", "sess-1", "codex-1", "app-1", "repo-1", "run-1", "open", 1)

    missing_gap_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Record agent gap event returned no row"):
        await missing_gap_storage.record_agent_gap_event(
            "owner-1",
            dedupe_key="gap:missing-row",
            session_id=None,
            principal_id="codex-1",
            app_id="app-1",
            repo_id="repo-1",
            run_id="run-1",
            gap_type="agent_instruction_update",
            severity="medium",
            blocker=False,
            detected_from="ci_run_diagnostics",
            required_capability=None,
            observed_request=None,
            suggested_fix=None,
        )


@pytest.mark.asyncio
async def test_storage_filter_and_missing_record_paths_cover_blank_optional_inputs() -> None:
    conn = _FakeConn(
        fetchrow_results=[None, None, _operation_row(status="resolved")],
        fetch_results=[
            [_gap_row(status="open")],
            [_secret_ref_row()],
            [_operation_row(status="resolved")],
        ],
    )
    storage = _storage(conn)

    gaps = await storage.list_agent_gap_events(
        "owner-1",
        session_id="",
        principal_id="",
        app_id="",
        repo_id="",
        run_id="",
        status="",
        unresolved_only=True,
        blocker_only=False,
        limit=0,
    )
    missing_gap = await storage.get_agent_gap_event("owner-1", "missing-gap")
    updated_gap = await storage.update_agent_gap_event(
        "owner-1",
        gap_id="missing-gap",
        status="resolved",
    )
    secret_refs = await storage.list_secret_refs("owner-1", active_only=False)
    operations = await storage.list_managed_operations(
        "owner-1",
        app_id="",
        repo_id="",
        service_kind="",
        status="",
        limit=0,
    )
    found_operation = await storage.find_managed_operation_by_ref(
        "owner-1",
        ref_kind="git_sha",
        ref_value="a" * 40,
        app_id="",
    )

    assert gaps[0]["status"] == "open"
    gap_query, gap_args = conn.fetch_calls[0]
    assert "status <> 'resolved'" in gap_query
    assert "session_id =" not in gap_query
    assert "principal_id =" not in gap_query
    assert "app_id =" not in gap_query
    assert "repo_id =" not in gap_query
    assert "run_id =" not in gap_query
    assert "blocker = TRUE" not in gap_query
    assert gap_args == ("owner-1", 1)

    assert missing_gap is None
    assert updated_gap is None

    assert secret_refs[0]["secret_ref_id"] == "secret-1"
    secret_query, secret_args = conn.fetch_calls[1]
    assert "active = TRUE" not in secret_query
    assert secret_args == ("owner-1",)

    assert operations[0]["status"] == "resolved"
    operations_query, operations_args = conn.fetch_calls[2]
    assert "op.app_id =" not in operations_query
    assert "op.repo_id =" not in operations_query
    assert "ref.service_kind =" not in operations_query
    assert "op.status =" not in operations_query
    assert operations_args == ("owner-1", 1)

    assert found_operation is not None
    find_query, find_args = conn.fetchrow_calls[2]
    assert "op.app_id =" not in find_query
    assert find_args == ("owner-1", "git_sha", "a" * 40)


@pytest.mark.asyncio
async def test_storage_missing_row_errors_cover_service_request_capability_and_operation_creation(
) -> None:
    missing_request_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Create agent service request returned no row"):
        await missing_request_storage.create_agent_service_request(
            "owner-1",
            principal_id=None,
            app_id="catalyst-group-solutions",
            service_kind="stripe",
            action_id="product.ensure",
            target_ref="product:gold",
            tenant_id="tenant-1",
            change_reason=None,
            request_payload={"name": "Gold"},
            status="pending",
            approved=False,
            result=None,
            audit_id=None,
            executed=False,
        )

    missing_capability_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Upsert service adapter capability returned no row"):
        await missing_capability_storage.upsert_service_adapter_capability(
            "owner-1",
            service_kind="github",
            manifest={},
        )

    missing_operation_storage = _storage(_FakeConn(fetchrow_results=[None, None]))
    with pytest.raises(RuntimeError, match="Create managed operation returned no row"):
        await missing_operation_storage.create_managed_operation(
            "owner-1",
            app_id="catalyst-group-solutions",
            repo_id="catalyst-group-solutions",
            operation_kind="deploy",
            lifecycle_stage="verification",
            status="active",
            summary=None,
            metadata=None,
            correlation_key="deploy:missing",
        )

    with pytest.raises(RuntimeError, match="Create managed operation returned no row"):
        await missing_operation_storage.create_managed_operation(
            "owner-1",
            app_id="catalyst-group-solutions",
            repo_id="catalyst-group-solutions",
            operation_kind="deploy",
            lifecycle_stage="verification",
            status="active",
            summary=None,
            metadata=None,
            correlation_key=None,
            operation_id="op-missing",
        )


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
async def test_operation_accessors_cover_missing_rows_blank_filters_and_hydration_none() -> None:
    missing_ref_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Upsert operation ref returned no row"):
        await missing_ref_storage.upsert_operation_ref(
            "owner-1",
            operation_id="op-1",
            service_kind=None,
            ref_kind="git_sha",
            ref_value="A" * 40,
            metadata=None,
        )

    missing_evidence_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Record operation evidence returned no row"):
        await missing_evidence_storage.record_operation_evidence(
            "owner-1",
            operation_id="op-1",
            service_kind="docker",
            evidence_type="logs",
            title="Compose logs",
            payload=None,
            log_text=None,
            metadata=None,
        )

    missing_incident_storage = _storage(_FakeConn(fetchrow_results=[None]))
    with pytest.raises(RuntimeError, match="Record operation incident returned no row"):
        await missing_incident_storage.record_operation_incident(
            "owner-1",
            operation_id="op-1",
            service_kind="discord",
            incident_type="discord_delivery_failed",
            severity="high",
            blocking=True,
            root_cause_summary="queue worker stalled",
            recommended_fix=None,
            evidence_refs=None,
            metadata=None,
        )

    conn = _FakeConn(
        fetch_results=[
            [_evidence_row(), _evidence_row("evidence-2", log_text=None)],
            [_incident_row()],
            [_incident_row()],
        ]
    )
    storage = _storage(conn)
    evidence = await storage.list_operation_evidence(
        "owner-1",
        "op-1",
        service_kind="",
        evidence_type="",
        limit=0,
    )
    incidents = await storage.list_operation_incidents(
        "owner-1",
        "op-1",
        unresolved_only=False,
        limit=0,
    )
    owner_incidents = await storage.list_operation_incidents_for_owner(
        "owner-1",
        repo_id="",
        unresolved_only=False,
        limit=0,
    )

    assert evidence[0]["evidence_id"] == "evidence-1"
    evidence_query, evidence_args = conn.fetch_calls[0]
    assert "service_kind =" not in evidence_query
    assert "evidence_type =" not in evidence_query
    assert evidence_args == ("owner-1", "op-1", 1)

    assert incidents[0]["incident_id"] == "incident-1"
    incidents_query, incidents_args = conn.fetch_calls[1]
    assert "status <> 'resolved'" not in incidents_query
    assert incidents_args == ("owner-1", "op-1", 1)

    assert owner_incidents[0]["incident_id"] == "incident-1"
    owner_query, owner_args = conn.fetch_calls[2]
    assert "op.repo_id =" not in owner_query
    assert "inc.status <> 'resolved'" not in owner_query
    assert owner_args == ("owner-1", 1)

    missing_hydrated_storage = _storage(_FakeConn())
    missing_hydrated_storage.get_managed_operation = AsyncMock(return_value=None)  # type: ignore[method-assign]
    assert await missing_hydrated_storage.get_operation_hydrated("owner-1", "missing-op") is None


@pytest.mark.asyncio
async def test_get_operation_log_chunks_filters_and_limits_entries() -> None:
    storage = _storage(_FakeConn())
    storage.list_operation_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value=[
            {
                "evidence_id": "evidence-1",
                "service_kind": "docker",
                "payload": {"stream": "stdout"},
                "log_text": "match this log",
                "metadata": {"order": 1},
                "updated_at": _dt().isoformat(),
            },
            {
                "evidence_id": "evidence-2",
                "service_kind": "docker",
                "payload": {"stream": "stderr"},
                "log_text": "skip this log",
                "metadata": {"order": 2},
                "updated_at": _dt().isoformat(),
            },
            {
                "evidence_id": "evidence-3",
                "service_kind": "docker",
                "payload": {"stream": "stderr"},
                "log_text": "match this too",
                "metadata": {"order": 3},
                "updated_at": _dt().isoformat(),
            },
        ]
    )

    filtered_logs = await storage.get_operation_log_chunks(
        "owner-1",
        "op-1",
        query_text="match",
        limit=5,
    )
    limited_logs = await storage.get_operation_log_chunks(
        "owner-1",
        "op-1",
        query_text="",
        limit=1,
    )

    assert [chunk["chunk_id"] for chunk in filtered_logs] == ["evidence-1", "evidence-3"]
    assert [chunk["message"] for chunk in filtered_logs] == [
        "match this log",
        "match this too",
    ]
    assert len(limited_logs) == 1
    assert limited_logs[0]["chunk_id"] == "evidence-1"


@pytest.mark.asyncio
async def test_claim_worker_job_skips_claim_race_when_update_returns_none() -> None:
    queued_job = _worker_job_row()
    conn = _FakeConn(
        fetchrow_results=[None],
        fetch_results=[
            [queued_job],
            [],
            [
                {
                    "run_id": "run-1",
                    "plan_json": {
                        "host_capacity_policy": {
                            "host_id": "windows-owner-ci",
                            "resource_budget": {"cpu": 4},
                        }
                    },
                    "metadata_json": {},
                }
            ],
        ],
    )
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
async def test_store_worker_observability_covers_existing_log_chunks_and_non_dict_samples(
) -> None:
    conn = _FakeConn()
    storage = _storage(conn)

    await storage._store_worker_observability(  # noqa: SLF001
        conn=conn,
        owner_id="owner-1",
        repo_id="zetherion-ai",
        run_id="run-2",
        shard_id="shard-2",
        node_id="windows-secondary",
        final_status="succeeded",
        result_json={
            "log_chunks": [
                "skip-me",
                {"stream": "stdout", "message": ""},
                {"stream": "stderr", "message": "chunk log", "metadata": {"stdout": False}},
            ],
            "resource_samples": [
                "skip-me",
                {
                    "memory_mb": 128,
                    "disk_used_bytes": 512,
                    "disk_free_bytes": 2048,
                    "container_count": 1,
                },
            ],
            "debug_bundle": {"bundle_path": "/tmp/bundle.json"},
            "cleanup_receipt": None,
            "container_receipts": None,
            "elapsed_ms": -10,
        },
        error_json={},
    )

    log_inserts = [
        call for call in conn.execute_calls if "owner_ci_log_chunks" in call[0]
    ]
    sample_inserts = [
        call for call in conn.execute_calls if "owner_ci_resource_samples" in call[0]
    ]
    bundle_insert = next(
        call for call in conn.execute_calls if "owner_ci_debug_bundles" in call[0]
    )
    summary_insert = next(
        call
        for call in conn.execute_calls
        if "owner_ci_project_usage_summaries" in call[0]
    )

    assert len(log_inserts) == 1
    assert len(sample_inserts) == 1
    assert json.loads(str(bundle_insert[1][5])) == {"bundle_path": "/tmp/bundle.json"}
    summary_payload = json.loads(str(summary_insert[1][3]))
    assert summary_payload["compute_minutes"] == 0.0
    assert summary_payload["container_count"] == 1
    assert summary_payload["cleanup_status"] is None


@pytest.mark.asyncio
async def test_recompute_run_status_covers_missing_run_repo_profile_and_local_receipt_fallbacks(
) -> None:
    missing_run_conn = _FakeConn(fetchrow_results=[None])
    missing_run_storage = _storage(missing_run_conn)

    await missing_run_storage._recompute_run_status("owner-1", "missing-run")  # noqa: SLF001

    assert missing_run_conn.execute_calls == []

    invalid_conn = _FakeConn(
        fetchrow_results=[{**_run_row(status="planned"), "status": "mystery"}, None],
        fetch_results=[[]],
    )
    invalid_storage = _storage(invalid_conn)

    await invalid_storage._recompute_run_status("owner-1", "run-1")  # noqa: SLF001

    invalid_update = next(
        call for call in invalid_conn.execute_calls if call[0].lstrip().startswith("UPDATE")
    )
    assert invalid_update[1][2] == "planned"

    from zetherion_ai.owner_ci import storage as storage_module

    shard_receipt_conn = _FakeConn(
        fetchrow_results=[_run_row(status="planned"), _repo_row()],
        fetch_results=[
            [
                _shard_row(
                    "shard-1",
                    "lane-1",
                    status="planned",
                    execution_target="windows_local",
                )
            ]
        ],
    )
    shard_receipt_storage = _storage(shard_receipt_conn)
    with patch.object(
        storage_module,
        "_load_local_repo_readiness_from_shards",
        return_value=({"source": "shards"}, {"source": "shards"}),
    ) as from_shards, patch.object(
        storage_module,
        "_load_local_repo_readiness",
        return_value=({"source": "filesystem"}, {"source": "filesystem"}),
    ) as from_fs, patch.object(
        storage_module,
        "overlay_local_readiness_shards",
        side_effect=lambda shards, receipt: shards,
    ) as overlay:
        await shard_receipt_storage._recompute_run_status("owner-1", "run-1")  # noqa: SLF001

    from_shards.assert_called_once()
    from_fs.assert_called_once()
    assert overlay.call_args.args[1] == {"source": "shards"}

    filesystem_receipt_conn = _FakeConn(
        fetchrow_results=[_run_row(status="planned"), _repo_row()],
        fetch_results=[
            [
                _shard_row(
                    "shard-1",
                    "lane-1",
                    status="planned",
                    execution_target="windows_local",
                )
            ]
        ],
    )
    filesystem_receipt_storage = _storage(filesystem_receipt_conn)
    with patch.object(
        storage_module,
        "_load_local_repo_readiness_from_shards",
        return_value=(None, None),
    ) as from_shards_none, patch.object(
        storage_module,
        "_load_local_repo_readiness",
        return_value=({"source": "filesystem"}, {"source": "filesystem"}),
    ) as from_fs_only, patch.object(
        storage_module,
        "overlay_local_readiness_shards",
        side_effect=lambda shards, receipt: shards,
    ) as overlay_filesystem:
        await filesystem_receipt_storage._recompute_run_status("owner-1", "run-1")  # noqa: SLF001

    from_shards_none.assert_called_once()
    from_fs_only.assert_called_once()
    assert overlay_filesystem.call_args.args[1] == {"source": "filesystem"}


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
        fetch_results=[[queued_job], [], []],
    )
    storage = _storage(conn)
    storage._store_worker_observability = AsyncMock()  # type: ignore[method-assign]
    storage._store_run_coaching_feedback = AsyncMock()  # type: ignore[method-assign]
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
    storage._store_run_coaching_feedback.assert_awaited_once()  # type: ignore[attr-defined]
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
    conn = _FakeConn(
        fetch_results=[[{**_worker_job_row(), "required_capabilities": ["docker"]}], [], []]
    )
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
async def test_claim_worker_job_skips_blocked_parallel_group_and_claims_next_admissible_job(
) -> None:
    blocked_job = {
        **_worker_job_row(),
        "job_id": "job-blocked",
        "shard_id": "shard-blocked",
        "payload_json": {
            "execution_target": "windows_local",
            "resource_class": "service",
            "parallel_group": "db",
        },
    }
    admissible_job = {
        **_worker_job_row(),
        "job_id": "job-admitted",
        "shard_id": "shard-admitted",
        "payload_json": {
            "execution_target": "windows_local",
            "resource_class": "service",
            "parallel_group": "queue",
        },
    }
    active_job = {
        **_worker_job_row(),
        "job_id": "job-active",
        "status": "claimed",
        "payload_json": {
            "execution_target": "windows_local",
            "resource_class": "service",
            "parallel_group": "db",
        },
    }
    claimed_row = {
        **admissible_job,
        "status": "claimed",
        "claimed_by_node_id": "node-1",
        "claimed_session_id": "sess-1",
    }
    conn = _FakeConn(
        fetchrow_results=[claimed_row],
        fetch_results=[
            [blocked_job, admissible_job],
            [active_job],
            [
                {
                    "run_id": "run-1",
                    "plan_json": {
                        "host_capacity_policy": {
                            "host_id": "windows-owner-ci",
                            "resource_budget": {"service": 2},
                        }
                    },
                    "metadata_json": {},
                }
            ],
        ],
    )
    storage = _storage(conn)
    storage._recompute_run_status = AsyncMock()  # type: ignore[method-assign]

    claimed = await storage.claim_worker_job(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        required_capabilities=["ci.test.run"],
        session_id="sess-1",
    )

    assert claimed is not None
    assert claimed["job_id"] == "job-admitted"


@pytest.mark.asyncio
async def test_claim_worker_job_returns_none_when_host_budget_is_exhausted() -> None:
    queued_job = {
        **_worker_job_row(),
        "payload_json": {
            "execution_target": "windows_local",
            "resource_class": "service",
        },
    }
    active_job = {
        **_worker_job_row(),
        "job_id": "job-active",
        "status": "claimed",
        "payload_json": {
            "execution_target": "windows_local",
            "resource_class": "service",
        },
    }
    conn = _FakeConn(
        fetch_results=[
            [queued_job],
            [active_job],
            [
                {
                    "run_id": "run-1",
                    "plan_json": {
                        "host_capacity_policy": {
                            "host_id": "windows-owner-ci",
                            "resource_budget": {"service": 1},
                        }
                    },
                    "metadata_json": {},
                }
            ],
        ]
    )
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
async def test_claim_worker_job_prefers_repo_with_lower_active_pressure() -> None:
    queued_repo_a = {
        **_worker_job_row(),
        "job_id": "job-repo-a",
        "repo_id": "repo-a",
        "run_id": "run-a",
    }
    queued_repo_b = {
        **_worker_job_row(),
        "job_id": "job-repo-b",
        "repo_id": "repo-b",
        "run_id": "run-b",
    }
    active_repo_a = {
        **_worker_job_row(),
        "job_id": "job-active-a",
        "repo_id": "repo-a",
        "run_id": "run-active-a",
        "status": "claimed",
        "claimed_by_node_id": "node-1",
        "claimed_session_id": "sess-1",
    }
    claimed_row = {
        **queued_repo_b,
        "status": "claimed",
        "claimed_by_node_id": "node-1",
        "claimed_session_id": "sess-1",
    }
    conn = _FakeConn(
        fetchrow_results=[claimed_row],
        fetch_results=[
            [queued_repo_a, queued_repo_b],
            [active_repo_a],
            [
                {
                    "run_id": "run-a",
                    "plan_json": {
                        "host_capacity_policy": {
                            "host_id": "windows-owner-ci",
                            "resource_budget": {"cpu": 4},
                        }
                    },
                    "metadata_json": {},
                },
                {
                    "run_id": "run-b",
                    "plan_json": {
                        "host_capacity_policy": {
                            "host_id": "windows-owner-ci",
                            "resource_budget": {"cpu": 4},
                        }
                    },
                    "metadata_json": {},
                },
                {
                    "run_id": "run-active-a",
                    "plan_json": {
                        "host_capacity_policy": {
                            "host_id": "windows-owner-ci",
                            "resource_budget": {"cpu": 4},
                        }
                    },
                    "metadata_json": {},
                },
            ],
        ],
    )
    storage = _storage(conn)
    storage._recompute_run_status = AsyncMock()  # type: ignore[method-assign]

    claimed = await storage.claim_worker_job(
        scope_id="owner:owner-1:repo:zetherion-ai",
        node_id="node-1",
        required_capabilities=["ci.test.run"],
        session_id="sess-1",
    )

    assert claimed is not None
    assert claimed["job_id"] == "job-repo-b"


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


@pytest.mark.asyncio
async def test_submit_worker_job_result_raises_when_update_row_is_missing() -> None:
    claimed_job = {
        **_worker_job_row(),
        "status": "claimed",
        "claimed_by_node_id": "node-1",
        "claimed_session_id": "sess-1",
    }
    storage = _storage(_FakeConn(fetchrow_results=[claimed_job, None]))
    storage._store_worker_observability = AsyncMock()  # type: ignore[method-assign]
    storage._store_run_coaching_feedback = AsyncMock()  # type: ignore[method-assign]
    storage._recompute_run_status = AsyncMock()  # type: ignore[method-assign]

    with pytest.raises(RuntimeError, match="Worker job update returned no row"):
        await storage.submit_worker_job_result(
            scope_id="owner:owner-1:repo:zetherion-ai",
            node_id="node-1",
            job_id="job-1",
            payload={"status": "failed", "output": {}, "error": {}},
        )

    storage._store_worker_observability.assert_awaited_once()  # type: ignore[attr-defined]
    storage._recompute_run_status.assert_awaited_once()  # type: ignore[attr-defined]
    storage._store_run_coaching_feedback.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_store_run_coaching_feedback_covers_early_returns_and_recording_paths() -> None:
    storage = _storage(_FakeConn())
    storage.get_run = AsyncMock(
        side_effect=[
            None,
            {"status": "succeeded"},
            {"status": "failed", "metadata": {}},
            {
                "status": "failed",
                "repo_id": "repo-1",
                "metadata": {"principal_id": "codex-1"},
            },
            {
                "status": "failed",
                "repo_id": "repo-1",
                "metadata": {
                    "principal_id": "codex-1",
                    "session_id": "sess-1",
                    "app_id": "app-1",
                },
            },
        ]
    )  # type: ignore[method-assign]
    storage.list_agent_coaching_feedback = AsyncMock(
        return_value=[
            {
                "recurrence_count": 3,
                "rule_violations": [
                    {"rule_code": "missing_gitleaks"},
                    {"rule_code": ""},
                ],
            }
        ]
    )  # type: ignore[method-assign]
    storage.get_run_report = AsyncMock(side_effect=[None, {"run_id": "run-1"}])  # type: ignore[method-assign]
    storage.record_agent_gap_event = AsyncMock(return_value=_gap_row())  # type: ignore[method-assign]

    from zetherion_ai.owner_ci import storage as storage_module

    with patch.object(
        storage_module,
        "build_recurring_diagnostic_coaching_payloads",
        return_value=[
            {
                "gap_type": "agent_instruction_update",
                "repo_id": "repo-1",
                "run_id": "run-1",
                "severity": "medium",
                "blocker": False,
                "detected_from": "ci_run_diagnostics",
                "required_capability": "gitleaks",
                "observed_request": {"tool": "gitleaks"},
                "suggested_fix": "Update AGENTS.md to require gitleaks.",
                "metadata": {"rule_code": "missing_gitleaks"},
            }
        ],
    ):
        await storage._store_run_coaching_feedback("owner-1", "run-1")  # noqa: SLF001
        await storage._store_run_coaching_feedback("owner-1", "run-1")  # noqa: SLF001
        await storage._store_run_coaching_feedback("owner-1", "run-1")  # noqa: SLF001
        await storage._store_run_coaching_feedback("owner-1", "run-1")  # noqa: SLF001
        await storage._store_run_coaching_feedback("owner-1", "run-1")  # noqa: SLF001

    storage.record_agent_gap_event.assert_awaited_once()  # type: ignore[attr-defined]
    kwargs = storage.record_agent_gap_event.await_args.kwargs  # type: ignore[attr-defined]
    assert kwargs["principal_id"] == "codex-1"
    assert kwargs["session_id"] == "sess-1"
    assert kwargs["app_id"] == "app-1"
    assert kwargs["required_capability"] == "gitleaks"
