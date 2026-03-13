"""Owner-scoped CI controller persistence."""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from zetherion_ai.logging import get_logger
from zetherion_ai.owner_ci.models import (
    RepoReadinessReceipt,
    build_repo_readiness_receipt,
    build_workspace_readiness_receipt,
    normalize_release_verification_receipt,
    normalize_shard_receipt,
    normalize_worker_certification_receipt,
    overlay_local_readiness_shards,
)

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

    from zetherion_ai.security.encryption import FieldEncryptor

log = get_logger("zetherion_ai.owner_ci.storage")

_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_WINDOWS_ABS_PATH_RE = re.compile(r"^(?P<drive>[A-Za-z]):[\\/]*(?P<rest>.*)$")
_RUN_STATUSES = {
    "planned",
    "queued_local",
    "running",
    "running_disconnected",
    "awaiting_sync",
    "review_pending",
    "promotion_blocked",
    "ready_to_merge",
    "merged",
    "failed",
    "cancelled",
}
_SHARD_PENDING_STATUSES = {"planned", "queued_local", "running"}


def _pending_repo_readiness(repo_id: str, summary: str) -> RepoReadinessReceipt:
    return RepoReadinessReceipt(
        repo_id=repo_id,
        merge_ready=False,
        deploy_ready=False,
        failed_required_paths=[],
        missing_evidence=["owner_ci_run_missing"],
        shard_receipts=[],
        release_verification=None,
        summary=summary,
    )


def _expand_local_candidate_roots(raw_path: str) -> list[Path]:
    candidate = str(raw_path or "").strip()
    if not candidate:
        return []

    roots: list[Path] = []
    root = Path(candidate)
    if root.is_absolute():
        roots.append(root)

    windows_match = _WINDOWS_ABS_PATH_RE.match(candidate)
    if windows_match and os.name != "nt":
        drive = windows_match.group("drive").lower()
        rest = windows_match.group("rest").replace("\\", "/").lstrip("/")
        translated = Path(f"/mnt/{drive}/{rest}") if rest else Path(f"/mnt/{drive}")
        if translated not in roots:
            roots.append(translated)

    return roots


def _load_local_repo_readiness(
    repo: dict[str, Any],
) -> tuple[RepoReadinessReceipt | None, dict[str, Any] | None]:
    repo_id_fallback = str(repo.get("repo_id") or "").strip()
    for allowed_path in list(repo.get("allowed_paths") or []):
        for candidate_root in _expand_local_candidate_roots(str(allowed_path or "").strip()):
            for candidate_path in (
                candidate_root / ".artifacts" / "local-readiness-receipt.json",
                candidate_root / ".ci" / "local-readiness-receipt.json",
            ):
                if not candidate_path.is_file():
                    continue
                try:
                    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                receipt, normalized_payload = _normalize_local_repo_readiness_payload(
                    payload,
                    repo_id_fallback=repo_id_fallback,
                )
                if receipt is not None:
                    return receipt, normalized_payload
    return None, None


def _normalize_local_repo_readiness_payload(
    payload: Any,
    *,
    repo_id_fallback: str,
) -> tuple[RepoReadinessReceipt | None, dict[str, Any] | None]:
    if not isinstance(payload, dict):
        return None, None

    repo_id = str(payload.get("repo_id") or repo_id_fallback or "").strip()
    if not repo_id:
        return None, None

    release_payload = dict(payload.get("release_verification") or {})
    shard_payloads = [
        dict(entry)
        for entry in list(payload.get("shard_receipts") or [])
        if isinstance(entry, dict)
    ]
    receipt = RepoReadinessReceipt(
        repo_id=repo_id,
        merge_ready=bool(payload.get("merge_ready", False)),
        deploy_ready=bool(payload.get("deploy_ready", False)),
        failed_required_paths=[
            str(path).strip()
            for path in list(payload.get("failed_required_paths") or [])
            if str(path).strip()
        ],
        missing_evidence=[
            str(path).strip()
            for path in list(payload.get("missing_evidence") or [])
            if str(path).strip()
        ],
        shard_receipts=[
            normalize_shard_receipt(repo_id, shard_payload)
            for shard_payload in shard_payloads
        ],
        release_verification=(
            normalize_release_verification_receipt(release_payload)
            if release_payload
            else None
        ),
        summary=str(payload.get("summary") or "local readiness receipt loaded").strip(),
    )
    return receipt, dict(payload)


def _load_local_repo_readiness_from_shards(
    repo: dict[str, Any],
    shard_payloads: list[dict[str, Any]],
) -> tuple[RepoReadinessReceipt | None, dict[str, Any] | None]:
    repo_id_fallback = str(repo.get("repo_id") or "").strip()
    for shard_payload in reversed(list(shard_payloads or [])):
        if not isinstance(shard_payload, dict):
            continue
        result_payload = dict(shard_payload.get("result") or {})
        candidate = result_payload.get("local_readiness_receipt")
        receipt, normalized_payload = _normalize_local_repo_readiness_payload(
            candidate,
            repo_id_fallback=repo_id_fallback,
        )
        if receipt is not None:
            return receipt, normalized_payload
    return None, None


def _load_local_worker_certification(
    repo: dict[str, Any],
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    for allowed_path in list(repo.get("allowed_paths") or []):
        for candidate_root in _expand_local_candidate_roots(str(allowed_path or "").strip()):
            for candidate_path in (
                candidate_root / ".artifacts" / "worker-certification-receipt.json",
                candidate_root / ".artifacts" / "ci-worker-connectivity.json",
                candidate_root / ".ci" / "worker-certification-receipt.json",
                candidate_root / ".ci" / "ci-worker-connectivity.json",
            ):
                if not candidate_path.is_file():
                    continue
                try:
                    payload = json.loads(candidate_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                receipt = normalize_worker_certification_receipt(payload)
                return receipt.model_dump(mode="json"), payload
    return None, None


def _merge_json_dict(
    current: dict[str, Any] | None,
    patch: dict[str, Any] | None,
) -> dict[str, Any]:
    merged = dict(current or {})
    for key, value in dict(patch or {}).items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = {**dict(merged.get(key) or {}), **value}
        else:
            merged[key] = value
    return merged


def _validate_schema_identifier(schema: str) -> str:
    candidate = schema.strip()
    if not _SCHEMA_NAME_RE.fullmatch(candidate):
        raise ValueError(f"Invalid PostgreSQL schema name: {schema!r}")
    return candidate


def _schema_sql(schema: str) -> str:
    validated = _validate_schema_identifier(schema)
    return f"""\
CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_repo_profiles (
    owner_id            TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    display_name_value  TEXT         NOT NULL,
    github_repo         TEXT         NOT NULL,
    default_branch      TEXT         NOT NULL DEFAULT 'main',
    stack_kind          TEXT         NOT NULL,
    local_fast_lanes    JSONB        NOT NULL DEFAULT '[]'::jsonb,
    local_full_lanes    JSONB        NOT NULL DEFAULT '[]'::jsonb,
    windows_full_lanes  JSONB        NOT NULL DEFAULT '[]'::jsonb,
    review_policy       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    promotion_policy    JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    allowed_paths       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    secrets_profile     TEXT,
    active              BOOLEAN      NOT NULL DEFAULT TRUE,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, repo_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_repo_profiles_owner_active
    ON "{validated}".owner_ci_repo_profiles (owner_id, active, updated_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_plan_snapshots (
    owner_id              TEXT         NOT NULL,
    plan_id               TEXT         NOT NULL,
    repo_id               TEXT         NOT NULL,
    version               INT          NOT NULL,
    title_value           TEXT         NOT NULL,
    content_markdown_value TEXT        NOT NULL,
    tags_json             JSONB        NOT NULL DEFAULT '[]'::jsonb,
    current_version       BOOLEAN      NOT NULL DEFAULT TRUE,
    metadata_json         JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, plan_id, version)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_plan_snapshots_owner_plan
    ON "{validated}".owner_ci_plan_snapshots (owner_id, plan_id, version DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_runs (
    owner_id            TEXT         NOT NULL,
    run_id              TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    git_ref             TEXT         NOT NULL,
    trigger_value       TEXT         NOT NULL,
    status              TEXT         NOT NULL DEFAULT 'planned',
    plan_json           JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    review_receipts     JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    github_receipts     JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, run_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_runs_owner_repo
    ON "{validated}".owner_ci_runs (owner_id, repo_id, created_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_shards (
    owner_id            TEXT         NOT NULL,
    run_id              TEXT         NOT NULL,
    shard_id            TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    lane_id             TEXT         NOT NULL,
    lane_label_value    TEXT         NOT NULL,
    execution_target    TEXT         NOT NULL,
    command_json        JSONB        NOT NULL DEFAULT '[]'::jsonb,
    env_refs_json       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    artifact_contract   JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    required_capabilities JSONB      NOT NULL DEFAULT '[]'::jsonb,
    relay_mode          TEXT         NOT NULL DEFAULT 'direct',
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    status              TEXT         NOT NULL DEFAULT 'planned',
    result_json         JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    error_json          JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    started_at          TIMESTAMPTZ,
    completed_at        TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, shard_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_shards_run
    ON "{validated}".owner_ci_shards (owner_id, run_id, created_at ASC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_worker_nodes (
    scope_id            TEXT         NOT NULL,
    node_id             TEXT         NOT NULL,
    node_name           TEXT,
    capabilities_json   JSONB        NOT NULL DEFAULT '[]'::jsonb,
    status              TEXT         NOT NULL DEFAULT 'active',
    health_status       TEXT         NOT NULL DEFAULT 'healthy',
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_heartbeat_at   TIMESTAMPTZ,
    PRIMARY KEY (scope_id, node_id)
);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_worker_sessions (
    scope_id            TEXT         NOT NULL,
    node_id             TEXT         NOT NULL,
    session_id          TEXT         NOT NULL,
    token_hash          TEXT         NOT NULL,
    signing_secret      TEXT         NOT NULL,
    status              TEXT         NOT NULL DEFAULT 'registered',
    health_status       TEXT         NOT NULL DEFAULT 'healthy',
    node_metadata       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    expires_at          TIMESTAMPTZ,
    revoked_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (scope_id, node_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_worker_sessions_scope_node
    ON "{validated}".owner_ci_worker_sessions (scope_id, node_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_worker_jobs (
    scope_id            TEXT         NOT NULL,
    job_id              TEXT         NOT NULL,
    owner_id            TEXT         NOT NULL,
    run_id              TEXT         NOT NULL,
    shard_id            TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    action_name         TEXT         NOT NULL,
    runner_name         TEXT         NOT NULL,
    payload_json        JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    required_capabilities JSONB      NOT NULL DEFAULT '[]'::jsonb,
    artifact_contract   JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    status              TEXT         NOT NULL DEFAULT 'queued',
    idempotency_key     TEXT         NOT NULL,
    execution_target    TEXT         NOT NULL DEFAULT 'windows_local',
    claimed_by_node_id  TEXT,
    claimed_session_id  TEXT,
    result_json         JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    error_json          JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    submitted_at        TIMESTAMPTZ,
    PRIMARY KEY (scope_id, job_id),
    UNIQUE (scope_id, idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_worker_jobs_scope_status
    ON "{validated}".owner_ci_worker_jobs (scope_id, status, created_at ASC);

ALTER TABLE "{validated}".owner_ci_shards
    ADD COLUMN IF NOT EXISTS metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb;

ALTER TABLE "{validated}".owner_ci_repo_profiles
    ADD COLUMN IF NOT EXISTS local_full_lanes JSONB NOT NULL DEFAULT '[]'::jsonb;

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_compiled_plans (
    owner_id            TEXT         NOT NULL,
    compiled_plan_id    TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    git_ref             TEXT         NOT NULL,
    mode_value          TEXT         NOT NULL,
    plan_json           JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, compiled_plan_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_compiled_plans_owner_repo
    ON "{validated}".owner_ci_compiled_plans (owner_id, repo_id, created_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_schedules (
    owner_id            TEXT         NOT NULL,
    schedule_id         TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    name_value          TEXT         NOT NULL,
    schedule_kind       TEXT         NOT NULL,
    schedule_spec_json  JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    active              BOOLEAN      NOT NULL DEFAULT TRUE,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, schedule_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_schedules_owner_repo
    ON "{validated}".owner_ci_schedules (owner_id, repo_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_events (
    owner_id            TEXT         NOT NULL,
    event_id            TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    run_id              TEXT,
    shard_id            TEXT,
    node_id             TEXT,
    event_type          TEXT         NOT NULL,
    level_value         TEXT         NOT NULL DEFAULT 'info',
    payload_json        JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, event_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_events_owner_run
    ON "{validated}".owner_ci_events (owner_id, run_id, created_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_log_chunks (
    owner_id            TEXT         NOT NULL,
    chunk_id            TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    run_id              TEXT,
    shard_id            TEXT,
    node_id             TEXT,
    stream_name         TEXT         NOT NULL,
    message_value       TEXT         NOT NULL,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, chunk_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_log_chunks_owner_run
    ON "{validated}".owner_ci_log_chunks (owner_id, run_id, created_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_resource_samples (
    owner_id            TEXT         NOT NULL,
    sample_id           TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    run_id              TEXT,
    shard_id            TEXT,
    node_id             TEXT,
    sample_json         JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, sample_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_resource_samples_owner_run
    ON "{validated}".owner_ci_resource_samples (owner_id, run_id, created_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_debug_bundles (
    owner_id            TEXT         NOT NULL,
    bundle_id           TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    run_id              TEXT,
    shard_id            TEXT,
    bundle_json         JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, bundle_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_debug_bundles_owner_run
    ON "{validated}".owner_ci_debug_bundles (owner_id, run_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_project_usage_summaries (
    owner_id            TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    summary_key         TEXT         NOT NULL,
    summary_json        JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, repo_id, summary_key)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_project_usage_summaries_owner_repo
    ON "{validated}".owner_ci_project_usage_summaries (owner_id, repo_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_bootstrap_manifests (
    owner_id            TEXT         NOT NULL,
    client_id           TEXT         NOT NULL,
    manifest_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, client_id)
);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_docs_manifests (
    owner_id            TEXT         NOT NULL,
    slug                TEXT         NOT NULL,
    title_value         TEXT         NOT NULL,
    manifest_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, slug)
);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_setup_receipts (
    owner_id            TEXT         NOT NULL,
    receipt_id          TEXT         NOT NULL,
    client_id           TEXT         NOT NULL,
    receipt_json        JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, receipt_id)
);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_principals (
    owner_id            TEXT         NOT NULL,
    principal_id        TEXT         NOT NULL,
    display_name_value  TEXT         NOT NULL,
    principal_type      TEXT         NOT NULL DEFAULT 'codex',
    allowed_scopes_json JSONB        NOT NULL DEFAULT '[]'::jsonb,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    active              BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, principal_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_principals_owner_active
    ON "{validated}".owner_ci_agent_principals (owner_id, active, updated_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_external_service_connectors (
    owner_id            TEXT         NOT NULL,
    connector_id        TEXT         NOT NULL,
    service_kind        TEXT         NOT NULL,
    display_name_value  TEXT         NOT NULL,
    auth_kind           TEXT         NOT NULL,
    secret_value        TEXT,
    policy_json         JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    active              BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, connector_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_external_service_connectors_owner_service
    ON "{validated}".owner_ci_external_service_connectors (
        owner_id,
        service_kind,
        active,
        updated_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_external_access_grants (
    owner_id            TEXT         NOT NULL,
    principal_id        TEXT         NOT NULL,
    grant_key           TEXT         NOT NULL,
    resource_type       TEXT         NOT NULL,
    resource_id         TEXT         NOT NULL,
    capabilities_json   JSONB        NOT NULL DEFAULT '[]'::jsonb,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    active              BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, principal_id, grant_key)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_external_access_grants_owner_principal
    ON "{validated}".owner_ci_external_access_grants (
        owner_id,
        principal_id,
        active,
        updated_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_app_profiles (
    owner_id            TEXT         NOT NULL,
    app_id              TEXT         NOT NULL,
    display_name_value  TEXT         NOT NULL,
    profile_json        JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    active              BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, app_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_app_profiles_owner_active
    ON "{validated}".owner_ci_agent_app_profiles (owner_id, active, updated_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_knowledge_packs (
    owner_id            TEXT         NOT NULL,
    app_id              TEXT         NOT NULL,
    version_value       TEXT         NOT NULL,
    pack_json           JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    current_version     BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, app_id, version_value)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_knowledge_packs_owner_app
    ON "{validated}".owner_ci_agent_knowledge_packs (
        owner_id,
        app_id,
        current_version,
        updated_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_workspace_bundles (
    owner_id            TEXT         NOT NULL,
    bundle_id           TEXT         NOT NULL,
    principal_id        TEXT         NOT NULL,
    app_id              TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    git_ref             TEXT         NOT NULL,
    resolved_ref        TEXT,
    bundle_json         JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    expires_at          TIMESTAMPTZ,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    downloaded_at       TIMESTAMPTZ,
    PRIMARY KEY (owner_id, bundle_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_workspace_bundles_owner_app
    ON "{validated}".owner_ci_workspace_bundles (owner_id, app_id, created_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_publish_candidates (
    owner_id            TEXT         NOT NULL,
    candidate_id        TEXT         NOT NULL,
    principal_id        TEXT         NOT NULL,
    app_id              TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    base_sha            TEXT         NOT NULL,
    status              TEXT         NOT NULL DEFAULT 'submitted',
    candidate_json      JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    review_json         JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, candidate_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_publish_candidates_owner_app
    ON "{validated}".owner_ci_publish_candidates (owner_id, app_id, created_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_secret_refs (
    owner_id            TEXT         NOT NULL,
    secret_ref_id       TEXT         NOT NULL,
    connector_id        TEXT,
    purpose_value       TEXT         NOT NULL,
    secret_value        TEXT,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    active              BOOLEAN      NOT NULL DEFAULT TRUE,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, secret_ref_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_secret_refs_owner_active
    ON "{validated}".owner_ci_secret_refs (owner_id, active, updated_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_audit_events (
    owner_id            TEXT         NOT NULL,
    audit_id            TEXT         NOT NULL,
    principal_id        TEXT,
    app_id              TEXT,
    service_kind        TEXT,
    resource_value      TEXT,
    action_value        TEXT         NOT NULL,
    decision_value      TEXT         NOT NULL,
    audit_json          JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, audit_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_audit_events_owner_created
    ON "{validated}".owner_ci_agent_audit_events (owner_id, created_at DESC);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_sessions (
    owner_id            TEXT         NOT NULL,
    session_id          TEXT         NOT NULL,
    principal_id        TEXT         NOT NULL,
    app_id              TEXT,
    session_status      TEXT         NOT NULL DEFAULT 'active',
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_activity_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, session_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_sessions_owner_principal
    ON "{validated}".owner_ci_agent_sessions (
        owner_id,
        principal_id,
        updated_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_interactions (
    owner_id               TEXT         NOT NULL,
    interaction_id         TEXT         NOT NULL,
    session_id             TEXT,
    principal_id           TEXT,
    app_id                 TEXT,
    repo_id                TEXT,
    route_path_value       TEXT,
    intent_value           TEXT         NOT NULL,
    request_text_value     TEXT,
    request_json           JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    normalized_intent_json JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    related_run_id         TEXT,
    related_candidate_id   TEXT,
    related_service_request_id TEXT,
    audit_id               TEXT,
    created_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, interaction_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_interactions_owner_session
    ON "{validated}".owner_ci_agent_interactions (
        owner_id,
        session_id,
        created_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_interactions_owner_app
    ON "{validated}".owner_ci_agent_interactions (
        owner_id,
        app_id,
        created_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_actions (
    owner_id            TEXT         NOT NULL,
    action_record_id    TEXT         NOT NULL,
    interaction_id      TEXT         NOT NULL,
    principal_id        TEXT,
    app_id              TEXT,
    action_value        TEXT         NOT NULL,
    status              TEXT         NOT NULL DEFAULT 'pending',
    payload_json        JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, action_record_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_actions_owner_interaction
    ON "{validated}".owner_ci_agent_actions (
        owner_id,
        interaction_id,
        created_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_outcomes (
    owner_id            TEXT         NOT NULL,
    outcome_id          TEXT         NOT NULL,
    interaction_id      TEXT         NOT NULL,
    action_record_id    TEXT,
    status              TEXT         NOT NULL,
    summary_value       TEXT         NOT NULL,
    payload_json        JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, outcome_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_outcomes_owner_interaction
    ON "{validated}".owner_ci_agent_outcomes (
        owner_id,
        interaction_id,
        created_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_gap_events (
    owner_id            TEXT         NOT NULL,
    gap_id              TEXT         NOT NULL,
    dedupe_key          TEXT         NOT NULL,
    session_id          TEXT,
    principal_id        TEXT,
    app_id              TEXT,
    repo_id             TEXT,
    run_id              TEXT,
    gap_type            TEXT         NOT NULL,
    severity            TEXT         NOT NULL DEFAULT 'medium',
    blocker             BOOLEAN      NOT NULL DEFAULT FALSE,
    detected_from       TEXT         NOT NULL,
    required_capability TEXT,
    observed_request_json JSONB      NOT NULL DEFAULT '{{}}'::jsonb,
    suggested_fix_value TEXT,
    status              TEXT         NOT NULL DEFAULT 'open',
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    first_seen_at       TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at        TIMESTAMPTZ  NOT NULL DEFAULT now(),
    occurrence_count    INT          NOT NULL DEFAULT 1,
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, gap_id),
    UNIQUE (owner_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_gap_events_owner_open
    ON "{validated}".owner_ci_agent_gap_events (
        owner_id,
        status,
        blocker,
        last_seen_at DESC
    );

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_gap_events_owner_app
    ON "{validated}".owner_ci_agent_gap_events (
        owner_id,
        app_id,
        last_seen_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_agent_service_requests (
    owner_id            TEXT         NOT NULL,
    request_id          TEXT         NOT NULL,
    principal_id        TEXT,
    app_id              TEXT         NOT NULL,
    service_kind        TEXT         NOT NULL,
    action_id           TEXT         NOT NULL,
    target_ref          TEXT,
    tenant_id           TEXT,
    change_reason_value TEXT,
    request_json        JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    status              TEXT         NOT NULL DEFAULT 'submitted',
    approved            BOOLEAN      NOT NULL DEFAULT FALSE,
    result_json         JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    audit_id            TEXT,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    executed_at         TIMESTAMPTZ,
    PRIMARY KEY (owner_id, request_id)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_agent_service_requests_owner_app
    ON "{validated}".owner_ci_agent_service_requests (
        owner_id,
        app_id,
        created_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_service_adapter_capabilities (
    owner_id            TEXT         NOT NULL,
    service_kind        TEXT         NOT NULL,
    manifest_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, service_kind)
);

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_managed_operations (
    owner_id            TEXT         NOT NULL,
    operation_id        TEXT         NOT NULL,
    app_id              TEXT         NOT NULL,
    repo_id             TEXT         NOT NULL,
    operation_kind      TEXT         NOT NULL DEFAULT 'managed_operation',
    lifecycle_stage     TEXT         NOT NULL DEFAULT 'pending',
    status              TEXT         NOT NULL DEFAULT 'active',
    correlation_key     TEXT,
    summary_json        JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_observed_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, operation_id),
    UNIQUE (owner_id, correlation_key)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_managed_operations_owner_repo
    ON "{validated}".owner_ci_managed_operations (
        owner_id,
        repo_id,
        updated_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_operation_refs (
    owner_id            TEXT         NOT NULL,
    ref_id              TEXT         NOT NULL,
    operation_id        TEXT         NOT NULL,
    service_kind        TEXT,
    ref_kind            TEXT         NOT NULL,
    ref_value           TEXT         NOT NULL,
    dedupe_key          TEXT         NOT NULL,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, ref_id),
    UNIQUE (owner_id, operation_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_operation_refs_owner_ref
    ON "{validated}".owner_ci_operation_refs (
        owner_id,
        ref_kind,
        ref_value,
        updated_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_operation_evidence (
    owner_id            TEXT         NOT NULL,
    evidence_id         TEXT         NOT NULL,
    operation_id        TEXT         NOT NULL,
    service_kind        TEXT         NOT NULL,
    evidence_type       TEXT         NOT NULL,
    title_value         TEXT         NOT NULL,
    state               TEXT         NOT NULL DEFAULT 'ready',
    dedupe_key          TEXT         NOT NULL,
    payload_json        JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    log_text_value      TEXT,
    metadata_json       JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at          TIMESTAMPTZ  NOT NULL DEFAULT now(),
    PRIMARY KEY (owner_id, evidence_id),
    UNIQUE (owner_id, operation_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_operation_evidence_owner_operation
    ON "{validated}".owner_ci_operation_evidence (
        owner_id,
        operation_id,
        updated_at DESC
    );

CREATE TABLE IF NOT EXISTS "{validated}".owner_ci_operation_incidents (
    owner_id                 TEXT         NOT NULL,
    incident_id              TEXT         NOT NULL,
    operation_id             TEXT         NOT NULL,
    service_kind             TEXT         NOT NULL,
    incident_type            TEXT         NOT NULL,
    severity                 TEXT         NOT NULL DEFAULT 'medium',
    blocking                 BOOLEAN      NOT NULL DEFAULT FALSE,
    dedupe_key               TEXT         NOT NULL,
    status                   TEXT         NOT NULL DEFAULT 'open',
    root_cause_summary_value TEXT         NOT NULL,
    recommended_fix_value    TEXT,
    evidence_refs_json       JSONB        NOT NULL DEFAULT '[]'::jsonb,
    metadata_json            JSONB        NOT NULL DEFAULT '{{}}'::jsonb,
    created_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    updated_at               TIMESTAMPTZ  NOT NULL DEFAULT now(),
    last_seen_at             TIMESTAMPTZ  NOT NULL DEFAULT now(),
    occurrence_count         INT          NOT NULL DEFAULT 1,
    PRIMARY KEY (owner_id, incident_id),
    UNIQUE (owner_id, operation_id, dedupe_key)
);

CREATE INDEX IF NOT EXISTS idx_owner_ci_operation_incidents_owner_operation
    ON "{validated}".owner_ci_operation_incidents (
        owner_id,
        operation_id,
        status,
        blocking,
        last_seen_at DESC
    );
"""


class OwnerCiStorage:
    """Persist owner-scoped CI controller state."""

    def __init__(
        self,
        pool: asyncpg.Pool,  # type: ignore[type-arg]
        *,
        schema: str = "owner_personal",
        encryptor: FieldEncryptor | None = None,
    ) -> None:
        self._pool: asyncpg.Pool = pool  # type: ignore[type-arg]
        self._schema = _validate_schema_identifier(schema)
        self._encryptor = encryptor

    async def ensure_schema(self) -> None:
        async with self._pool.acquire() as conn:
            await conn.execute(_schema_sql(self._schema))
        log.info("owner_ci_schema_ensured", schema=self._schema)

    def _encrypt_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        if self._encryptor is None:
            return value
        return self._encryptor.encrypt_value(value)

    def _decrypt_text(self, value: str | None) -> str | None:
        if value is None:
            return None
        if self._encryptor is None:
            return value
        try:
            return self._encryptor.decrypt_value(value)
        except ValueError:
            return value

    @staticmethod
    def _coerce_json_value(raw: Any, field_name: str) -> Any:
        if raw is None:
            return None
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return None
            try:
                return json.loads(text)
            except json.JSONDecodeError as exc:
                raise ValueError(f"{field_name} must contain valid JSON") from exc
        return raw

    @classmethod
    def _coerce_json_object(cls, raw: Any, field_name: str) -> dict[str, Any]:
        value = cls._coerce_json_value(raw, field_name)
        if value is None:
            return {}
        if isinstance(value, dict):
            return dict(value)
        raise ValueError(f"{field_name} must be a JSON object")

    @classmethod
    def _coerce_json_list(cls, raw: Any, field_name: str) -> list[Any]:
        value = cls._coerce_json_value(raw, field_name)
        if value is None:
            return []
        if isinstance(value, list):
            return list(value)
        if isinstance(value, tuple):
            return list(value)
        raise ValueError(f"{field_name} must be a JSON array")

    @staticmethod
    def _repo_profile_extensions(metadata: dict[str, Any]) -> dict[str, Any]:
        return {
            "mandatory_static_gates": list(metadata.get("mandatory_static_gates") or []),
            "shard_templates": list(metadata.get("shard_templates") or []),
            "scheduling_policy": dict(metadata.get("scheduling_policy") or {}),
            "resource_classes": dict(metadata.get("resource_classes") or {}),
            "windows_execution_mode": str(
                metadata.get("windows_execution_mode") or "command"
            ).strip()
            or "command",
            "certification_requirements": list(metadata.get("certification_requirements") or []),
            "scheduled_canaries": list(metadata.get("scheduled_canaries") or []),
            "debug_policy": dict(metadata.get("debug_policy") or {}),
            "agent_bootstrap_profile": dict(metadata.get("agent_bootstrap_profile") or {}),
        }

    def _repo_profile_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        metadata = self._coerce_json_object(row["metadata_json"], "metadata_json")
        return {
            "owner_id": str(row["owner_id"]),
            "repo_id": str(row["repo_id"]),
            "display_name": self._decrypt_text(str(row["display_name_value"])) or "",
            "github_repo": str(row["github_repo"]),
            "default_branch": str(row["default_branch"]),
            "stack_kind": str(row["stack_kind"]),
            **self._repo_profile_extensions(metadata),
            "local_fast_lanes": self._coerce_json_list(row["local_fast_lanes"], "local_fast_lanes"),
            "local_full_lanes": self._coerce_json_list(
                row.get("local_full_lanes"),
                "local_full_lanes",
            ),
            "windows_full_lanes": self._coerce_json_list(
                row["windows_full_lanes"],
                "windows_full_lanes",
            ),
            "review_policy": self._coerce_json_object(row["review_policy"], "review_policy"),
            "promotion_policy": self._coerce_json_object(
                row["promotion_policy"],
                "promotion_policy",
            ),
            "allowed_paths": self._coerce_json_list(row["allowed_paths"], "allowed_paths"),
            "secrets_profile": str(row["secrets_profile"]) if row["secrets_profile"] else None,
            "active": bool(row["active"]),
            "metadata": metadata,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _plan_snapshot_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "plan_id": str(row["plan_id"]),
            "repo_id": str(row["repo_id"]),
            "version": int(row["version"]),
            "title": self._decrypt_text(str(row["title_value"])) or "",
            "content_markdown": self._decrypt_text(str(row["content_markdown_value"])) or "",
            "tags": self._coerce_json_list(row["tags_json"], "tags_json"),
            "current": bool(row["current_version"]),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }

    def _run_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "run_id": str(row["run_id"]),
            "repo_id": str(row["repo_id"]),
            "git_ref": str(row["git_ref"]),
            "trigger": str(row["trigger_value"]),
            "status": str(row["status"]),
            "plan": self._coerce_json_object(row["plan_json"], "plan_json"),
            "review_receipts": self._coerce_json_object(
                row["review_receipts"],
                "review_receipts",
            ),
            "github_receipts": self._coerce_json_object(
                row["github_receipts"],
                "github_receipts",
            ),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _shard_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "run_id": str(row["run_id"]),
            "shard_id": str(row["shard_id"]),
            "repo_id": str(row["repo_id"]),
            "lane_id": str(row["lane_id"]),
            "lane_label": self._decrypt_text(str(row["lane_label_value"])) or "",
            "execution_target": str(row["execution_target"]),
            "command": self._coerce_json_list(row["command_json"], "command_json"),
            "env_refs": self._coerce_json_list(row["env_refs_json"], "env_refs_json"),
            "artifact_contract": self._coerce_json_object(
                row["artifact_contract"],
                "artifact_contract",
            ),
            "required_capabilities": self._coerce_json_list(
                row["required_capabilities"],
                "required_capabilities",
            ),
            "relay_mode": str(row["relay_mode"]),
            "metadata": self._coerce_json_object(row.get("metadata_json"), "metadata_json"),
            "status": str(row["status"]),
            "result": self._coerce_json_object(row["result_json"], "result_json"),
            "error": self._coerce_json_object(row["error_json"], "error_json"),
            "started_at": row["started_at"].isoformat() if row.get("started_at") else None,
            "completed_at": row["completed_at"].isoformat() if row.get("completed_at") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _worker_job_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "scope_id": str(row["scope_id"]),
            "job_id": str(row["job_id"]),
            "owner_id": str(row["owner_id"]),
            "run_id": str(row["run_id"]),
            "shard_id": str(row["shard_id"]),
            "repo_id": str(row["repo_id"]),
            "action": str(row["action_name"]),
            "runner": str(row["runner_name"]),
            "payload_json": self._coerce_json_object(row["payload_json"], "payload_json"),
            "required_capabilities": self._coerce_json_list(
                row["required_capabilities"],
                "required_capabilities",
            ),
            "artifact_contract": self._coerce_json_object(
                row["artifact_contract"],
                "artifact_contract",
            ),
            "status": str(row["status"]),
            "idempotency_key": str(row["idempotency_key"]),
            "execution_target": str(row["execution_target"]),
            "claimed_by_node_id": (
                str(row["claimed_by_node_id"]) if row["claimed_by_node_id"] else None
            ),
            "claimed_session_id": (
                str(row["claimed_session_id"]) if row["claimed_session_id"] else None
            ),
            "result_json": self._coerce_json_object(row["result_json"], "result_json"),
            "error_json": self._coerce_json_object(row["error_json"], "error_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "submitted_at": row["submitted_at"].isoformat() if row.get("submitted_at") else None,
        }

    def _compiled_plan_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "compiled_plan_id": str(row["compiled_plan_id"]),
            "repo_id": str(row["repo_id"]),
            "git_ref": str(row["git_ref"]),
            "mode": str(row["mode_value"]),
            "plan": self._coerce_json_object(row["plan_json"], "plan_json"),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _schedule_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "schedule_id": str(row["schedule_id"]),
            "repo_id": str(row["repo_id"]),
            "name": self._decrypt_text(str(row["name_value"])) or "",
            "schedule_kind": str(row["schedule_kind"]),
            "schedule_spec": self._coerce_json_object(
                row["schedule_spec_json"],
                "schedule_spec_json",
            ),
            "active": bool(row["active"]),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _event_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "event_id": str(row["event_id"]),
            "repo_id": str(row["repo_id"]),
            "run_id": str(row["run_id"]) if row.get("run_id") else None,
            "shard_id": str(row["shard_id"]) if row.get("shard_id") else None,
            "node_id": str(row["node_id"]) if row.get("node_id") else None,
            "event_type": str(row["event_type"]),
            "level": str(row["level_value"]),
            "payload": self._coerce_json_object(row["payload_json"], "payload_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }

    def _log_chunk_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "chunk_id": str(row["chunk_id"]),
            "repo_id": str(row["repo_id"]),
            "run_id": str(row["run_id"]) if row.get("run_id") else None,
            "shard_id": str(row["shard_id"]) if row.get("shard_id") else None,
            "node_id": str(row["node_id"]) if row.get("node_id") else None,
            "stream": str(row["stream_name"]),
            "message": self._decrypt_text(str(row["message_value"])) or "",
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }

    def _resource_sample_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "sample_id": str(row["sample_id"]),
            "repo_id": str(row["repo_id"]),
            "run_id": str(row["run_id"]) if row.get("run_id") else None,
            "shard_id": str(row["shard_id"]) if row.get("shard_id") else None,
            "node_id": str(row["node_id"]) if row.get("node_id") else None,
            "sample": self._coerce_json_object(row["sample_json"], "sample_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }

    def _debug_bundle_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "bundle_id": str(row["bundle_id"]),
            "repo_id": str(row["repo_id"]),
            "run_id": str(row["run_id"]) if row.get("run_id") else None,
            "shard_id": str(row["shard_id"]) if row.get("shard_id") else None,
            "bundle": self._coerce_json_object(row["bundle_json"], "bundle_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _agent_principal_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "principal_id": str(row["principal_id"]),
            "display_name": self._decrypt_text(str(row["display_name_value"])) or "",
            "principal_type": str(row["principal_type"]),
            "allowed_scopes": self._coerce_json_list(
                row["allowed_scopes_json"],
                "allowed_scopes_json",
            ),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "active": bool(row["active"]),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _external_connector_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        metadata = self._coerce_json_object(row["metadata_json"], "metadata_json")
        return {
            "owner_id": str(row["owner_id"]),
            "connector_id": str(row["connector_id"]),
            "service_kind": str(row["service_kind"]),
            "display_name": self._decrypt_text(str(row["display_name_value"])) or "",
            "auth_kind": str(row["auth_kind"]),
            "policy": self._coerce_json_object(row["policy_json"], "policy_json"),
            "metadata": metadata,
            "active": bool(row["active"]),
            "has_secret": bool(row.get("secret_value")),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _external_access_grant_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "principal_id": str(row["principal_id"]),
            "grant_key": str(row["grant_key"]),
            "resource_type": str(row["resource_type"]),
            "resource_id": str(row["resource_id"]),
            "capabilities": self._coerce_json_list(
                row["capabilities_json"],
                "capabilities_json",
            ),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "active": bool(row["active"]),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _agent_app_profile_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        profile = self._coerce_json_object(row["profile_json"], "profile_json")
        return {
            "owner_id": str(row["owner_id"]),
            "app_id": str(row["app_id"]),
            "display_name": self._decrypt_text(str(row["display_name_value"])) or "",
            "profile": profile,
            "active": bool(row["active"]),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _agent_knowledge_pack_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "app_id": str(row["app_id"]),
            "version": str(row["version_value"]),
            "pack": self._coerce_json_object(row["pack_json"], "pack_json"),
            "current": bool(row["current_version"]),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _workspace_bundle_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        bundle = self._coerce_json_object(row["bundle_json"], "bundle_json")
        return {
            "owner_id": str(row["owner_id"]),
            "bundle_id": str(row["bundle_id"]),
            "principal_id": str(row["principal_id"]),
            "app_id": str(row["app_id"]),
            "repo_id": str(row["repo_id"]),
            "git_ref": str(row["git_ref"]),
            "resolved_ref": str(row["resolved_ref"]) if row.get("resolved_ref") else None,
            "bundle": bundle,
            "expires_at": row["expires_at"].isoformat() if row.get("expires_at") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "downloaded_at": row["downloaded_at"].isoformat() if row.get("downloaded_at") else None,
        }

    def _publish_candidate_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "candidate_id": str(row["candidate_id"]),
            "principal_id": str(row["principal_id"]),
            "app_id": str(row["app_id"]),
            "repo_id": str(row["repo_id"]),
            "base_sha": str(row["base_sha"]),
            "status": str(row["status"]),
            "candidate": self._coerce_json_object(row["candidate_json"], "candidate_json"),
            "review": self._coerce_json_object(row["review_json"], "review_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _secret_ref_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "secret_ref_id": str(row["secret_ref_id"]),
            "connector_id": str(row["connector_id"]) if row.get("connector_id") else None,
            "purpose": self._decrypt_text(str(row["purpose_value"])) or "",
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "active": bool(row["active"]),
            "has_secret": bool(row.get("secret_value")),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _agent_audit_event_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "audit_id": str(row["audit_id"]),
            "principal_id": str(row["principal_id"]) if row.get("principal_id") else None,
            "app_id": str(row["app_id"]) if row.get("app_id") else None,
            "service_kind": str(row["service_kind"]) if row.get("service_kind") else None,
            "resource": self._decrypt_text(str(row["resource_value"]))
            if row.get("resource_value")
            else None,
            "action": self._decrypt_text(str(row["action_value"])) or "",
            "decision": str(row["decision_value"]),
            "audit": self._coerce_json_object(row["audit_json"], "audit_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }

    def _agent_session_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "session_id": str(row["session_id"]),
            "principal_id": str(row["principal_id"]),
            "app_id": str(row["app_id"]) if row.get("app_id") else None,
            "status": str(row["session_status"]),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "last_activity_at": (
                row["last_activity_at"].isoformat() if row.get("last_activity_at") else None
            ),
        }

    def _agent_interaction_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "interaction_id": str(row["interaction_id"]),
            "session_id": str(row["session_id"]) if row.get("session_id") else None,
            "principal_id": str(row["principal_id"]) if row.get("principal_id") else None,
            "app_id": str(row["app_id"]) if row.get("app_id") else None,
            "repo_id": str(row["repo_id"]) if row.get("repo_id") else None,
            "route_path": (
                self._decrypt_text(str(row["route_path_value"]))
                if row.get("route_path_value")
                else None
            ),
            "intent": self._decrypt_text(str(row["intent_value"])) or "",
            "request_text": (
                self._decrypt_text(str(row["request_text_value"]))
                if row.get("request_text_value")
                else None
            ),
            "request": self._coerce_json_object(row["request_json"], "request_json"),
            "normalized_intent": self._coerce_json_object(
                row["normalized_intent_json"],
                "normalized_intent_json",
            ),
            "related_run_id": str(row["related_run_id"]) if row.get("related_run_id") else None,
            "related_candidate_id": (
                str(row["related_candidate_id"]) if row.get("related_candidate_id") else None
            ),
            "related_service_request_id": (
                str(row["related_service_request_id"])
                if row.get("related_service_request_id")
                else None
            ),
            "audit_id": str(row["audit_id"]) if row.get("audit_id") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }

    def _agent_action_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "action_record_id": str(row["action_record_id"]),
            "interaction_id": str(row["interaction_id"]),
            "principal_id": str(row["principal_id"]) if row.get("principal_id") else None,
            "app_id": str(row["app_id"]) if row.get("app_id") else None,
            "action": self._decrypt_text(str(row["action_value"])) or "",
            "status": str(row["status"]),
            "payload": self._coerce_json_object(row["payload_json"], "payload_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _agent_outcome_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "outcome_id": str(row["outcome_id"]),
            "interaction_id": str(row["interaction_id"]),
            "action_record_id": (
                str(row["action_record_id"]) if row.get("action_record_id") else None
            ),
            "status": str(row["status"]),
            "summary": self._decrypt_text(str(row["summary_value"])) or "",
            "payload": self._coerce_json_object(row["payload_json"], "payload_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }

    def _agent_gap_event_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "gap_id": str(row["gap_id"]),
            "dedupe_key": str(row["dedupe_key"]),
            "session_id": str(row["session_id"]) if row.get("session_id") else None,
            "principal_id": str(row["principal_id"]) if row.get("principal_id") else None,
            "app_id": str(row["app_id"]) if row.get("app_id") else None,
            "repo_id": str(row["repo_id"]) if row.get("repo_id") else None,
            "run_id": str(row["run_id"]) if row.get("run_id") else None,
            "gap_type": str(row["gap_type"]),
            "severity": str(row["severity"]),
            "blocker": bool(row["blocker"]),
            "detected_from": str(row["detected_from"]),
            "required_capability": (
                str(row["required_capability"]) if row.get("required_capability") else None
            ),
            "observed_request": self._coerce_json_object(
                row["observed_request_json"],
                "observed_request_json",
            ),
            "suggested_fix": (
                self._decrypt_text(str(row["suggested_fix_value"]))
                if row.get("suggested_fix_value")
                else None
            ),
            "status": str(row["status"]),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "first_seen_at": row["first_seen_at"].isoformat() if row.get("first_seen_at") else None,
            "last_seen_at": row["last_seen_at"].isoformat() if row.get("last_seen_at") else None,
            "occurrence_count": int(row["occurrence_count"]),
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _agent_service_request_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "request_id": str(row["request_id"]),
            "principal_id": str(row["principal_id"]) if row.get("principal_id") else None,
            "app_id": str(row["app_id"]),
            "service_kind": str(row["service_kind"]),
            "action_id": str(row["action_id"]),
            "target_ref": str(row["target_ref"]) if row.get("target_ref") else None,
            "tenant_id": str(row["tenant_id"]) if row.get("tenant_id") else None,
            "change_reason": (
                self._decrypt_text(str(row["change_reason_value"]))
                if row.get("change_reason_value")
                else None
            ),
            "request": self._coerce_json_object(row["request_json"], "request_json"),
            "status": str(row["status"]),
            "approved": bool(row["approved"]),
            "result": self._coerce_json_object(row["result_json"], "result_json"),
            "audit_id": str(row["audit_id"]) if row.get("audit_id") else None,
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "executed_at": row["executed_at"].isoformat() if row.get("executed_at") else None,
        }

    def _service_adapter_capability_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "service_kind": str(row["service_kind"]),
            "manifest": self._coerce_json_object(row["manifest_json"], "manifest_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _managed_operation_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "operation_id": str(row["operation_id"]),
            "app_id": str(row["app_id"]),
            "repo_id": str(row["repo_id"]),
            "operation_kind": str(row["operation_kind"]),
            "lifecycle_stage": str(row["lifecycle_stage"]),
            "status": str(row["status"]),
            "correlation_key": (
                str(row["correlation_key"]) if row.get("correlation_key") else None
            ),
            "summary": self._coerce_json_object(row["summary_json"], "summary_json"),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "last_observed_at": (
                row["last_observed_at"].isoformat() if row.get("last_observed_at") else None
            ),
        }

    def _operation_ref_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "ref_id": str(row["ref_id"]),
            "operation_id": str(row["operation_id"]),
            "service_kind": str(row["service_kind"]) if row.get("service_kind") else None,
            "ref_kind": str(row["ref_kind"]),
            "ref_value": str(row["ref_value"]),
            "dedupe_key": str(row["dedupe_key"]),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _operation_evidence_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "evidence_id": str(row["evidence_id"]),
            "operation_id": str(row["operation_id"]),
            "service_kind": str(row["service_kind"]),
            "evidence_type": str(row["evidence_type"]),
            "title": self._decrypt_text(str(row["title_value"])) or "",
            "state": str(row["state"]),
            "dedupe_key": str(row["dedupe_key"]),
            "payload": self._coerce_json_object(row["payload_json"], "payload_json"),
            "log_text": (
                self._decrypt_text(str(row["log_text_value"]))
                if row.get("log_text_value")
                else None
            ),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    def _operation_incident_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "incident_id": str(row["incident_id"]),
            "operation_id": str(row["operation_id"]),
            "service_kind": str(row["service_kind"]),
            "incident_type": str(row["incident_type"]),
            "severity": str(row["severity"]),
            "blocking": bool(row["blocking"]),
            "dedupe_key": str(row["dedupe_key"]),
            "status": str(row["status"]),
            "root_cause_summary": self._decrypt_text(str(row["root_cause_summary_value"])) or "",
            "recommended_fix": (
                self._decrypt_text(str(row["recommended_fix_value"]))
                if row.get("recommended_fix_value")
                else None
            ),
            "evidence_refs": self._coerce_json_list(
                row["evidence_refs_json"],
                "evidence_refs_json",
            ),
            "metadata": self._coerce_json_object(row["metadata_json"], "metadata_json"),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            "last_seen_at": row["last_seen_at"].isoformat() if row.get("last_seen_at") else None,
            "occurrence_count": int(row["occurrence_count"]),
        }

    async def list_repo_profiles(self, owner_id: str) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_repo_profiles'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                 ORDER BY updated_at DESC, repo_id ASC
                """,
                owner_id,
            )
        return [self._repo_profile_from_row(dict(row)) for row in rows]

    async def get_repo_profile(self, owner_id: str, repo_id: str) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_repo_profiles'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND repo_id = $2
                 LIMIT 1
                """,
                owner_id,
                repo_id,
            )
        return self._repo_profile_from_row(dict(row)) if row is not None else None

    async def upsert_repo_profile(self, owner_id: str, profile: dict[str, Any]) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_repo_profiles'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    repo_id,
                    display_name_value,
                    github_repo,
                    default_branch,
                    stack_kind,
                    local_fast_lanes,
                    local_full_lanes,
                    windows_full_lanes,
                    review_policy,
                    promotion_policy,
                    allowed_paths,
                    secrets_profile,
                    active,
                    metadata_json,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9::jsonb, $10::jsonb,
                    $11::jsonb, $12::jsonb, $13, $14, $15::jsonb, now(), now()
                )
                ON CONFLICT (owner_id, repo_id) DO UPDATE SET
                    display_name_value = EXCLUDED.display_name_value,
                    github_repo = EXCLUDED.github_repo,
                    default_branch = EXCLUDED.default_branch,
                    stack_kind = EXCLUDED.stack_kind,
                    local_fast_lanes = EXCLUDED.local_fast_lanes,
                    local_full_lanes = EXCLUDED.local_full_lanes,
                    windows_full_lanes = EXCLUDED.windows_full_lanes,
                    review_policy = EXCLUDED.review_policy,
                    promotion_policy = EXCLUDED.promotion_policy,
                    allowed_paths = EXCLUDED.allowed_paths,
                    secrets_profile = EXCLUDED.secrets_profile,
                    active = EXCLUDED.active,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now()
                RETURNING *
                """,
                owner_id,
                str(profile["repo_id"]).strip(),
                self._encrypt_text(str(profile["display_name"]).strip()),
                str(profile["github_repo"]).strip(),
                str(profile.get("default_branch") or "main").strip(),
                str(profile["stack_kind"]).strip(),
                json.dumps(list(profile.get("local_fast_lanes") or [])),
                json.dumps(list(profile.get("local_full_lanes") or [])),
                json.dumps(list(profile.get("windows_full_lanes") or [])),
                json.dumps(dict(profile.get("review_policy") or {})),
                json.dumps(dict(profile.get("promotion_policy") or {})),
                json.dumps(list(profile.get("allowed_paths") or [])),
                (
                    str(profile["secrets_profile"]).strip()
                    if str(profile.get("secrets_profile") or "").strip()
                    else None
                ),
                bool(profile.get("active", True)),
                json.dumps(dict(profile.get("metadata") or {})),
            )
        if row is None:
            raise RuntimeError("Upsert repo profile returned no row")
        return self._repo_profile_from_row(dict(row))

    async def create_plan_snapshot(
        self,
        *,
        owner_id: str,
        repo_id: str,
        title: str,
        content_markdown: str,
        tags: list[str],
        plan_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        snapshot_plan_id = plan_id or uuid4().hex
        table = f'"{self._schema}".owner_ci_plan_snapshots'
        async with self._pool.acquire() as conn, conn.transaction():
            current_row = await conn.fetchrow(
                f"""
                    SELECT COALESCE(MAX(version), 0) AS current_version
                      FROM {table}
                     WHERE owner_id = $1
                       AND plan_id = $2
                    """,
                owner_id,
                snapshot_plan_id,
            )
            current_version = int(current_row["current_version"] or 0) if current_row else 0
            await conn.execute(
                f"""
                    UPDATE {table}
                       SET current_version = FALSE
                     WHERE owner_id = $1
                       AND plan_id = $2
                    """,
                owner_id,
                snapshot_plan_id,
            )
            row = await conn.fetchrow(
                f"""
                    INSERT INTO {table} (
                        owner_id,
                        plan_id,
                        repo_id,
                        version,
                        title_value,
                        content_markdown_value,
                        tags_json,
                        current_version,
                        metadata_json,
                        created_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7::jsonb, TRUE, $8::jsonb, now()
                    )
                    RETURNING *
                    """,
                owner_id,
                snapshot_plan_id,
                repo_id,
                current_version + 1,
                self._encrypt_text(title.strip() or "Plan"),
                self._encrypt_text(content_markdown),
                json.dumps(list(tags or [])),
                json.dumps(dict(metadata or {})),
            )
        if row is None:
            raise RuntimeError("Create plan snapshot returned no row")
        return self._plan_snapshot_from_row(dict(row))

    async def list_plan_versions(self, owner_id: str, plan_id: str) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_plan_snapshots'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND plan_id = $2
                 ORDER BY version DESC
                """,
                owner_id,
                plan_id,
            )
        return [self._plan_snapshot_from_row(dict(row)) for row in rows]

    async def get_plan_snapshot(
        self,
        owner_id: str,
        plan_id: str,
        *,
        version: int | None = None,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_plan_snapshots'
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
               AND plan_id = $2
        """
        params: list[Any] = [owner_id, plan_id]
        if version is None:
            query += " AND current_version = TRUE ORDER BY version DESC LIMIT 1"
        else:
            query += " AND version = $3 LIMIT 1"
            params.append(int(version))
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)
        return self._plan_snapshot_from_row(dict(row)) if row is not None else None

    async def create_run(
        self,
        *,
        owner_id: str,
        scope_id: str,
        repo_id: str,
        git_ref: str,
        trigger: str,
        plan: dict[str, Any],
        metadata: dict[str, Any] | None,
        shards: list[dict[str, Any]],
    ) -> dict[str, Any]:
        runs_table = f'"{self._schema}".owner_ci_runs'
        shards_table = f'"{self._schema}".owner_ci_shards'
        jobs_table = f'"{self._schema}".owner_ci_worker_jobs'
        run_id = uuid4().hex
        created_at = datetime.now(UTC)
        initial_status = (
            "queued_local"
            if any(
                str(item.get("execution_target") or "").strip().lower()
                in {"windows_local", "any_worker"}
                for item in shards
            )
            else "planned"
        )
        async with self._pool.acquire() as conn, conn.transaction():
            run_row = await conn.fetchrow(
                f"""
                    INSERT INTO {runs_table} (
                        owner_id,
                        run_id,
                        repo_id,
                        git_ref,
                        trigger_value,
                        status,
                        plan_json,
                        review_receipts,
                        github_receipts,
                        metadata_json,
                        created_at,
                        updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7::jsonb, '{{}}'::jsonb, '{{}}'::jsonb,
                        $8::jsonb, $9, $9
                    )
                    RETURNING *
                    """,
                owner_id,
                run_id,
                repo_id,
                git_ref,
                trigger,
                initial_status,
                json.dumps(plan),
                json.dumps(dict(metadata or {})),
                created_at,
            )
            if run_row is None:
                raise RuntimeError("Create run returned no row")
            for shard in shards:
                shard_id = str(shard.get("shard_id") or uuid4().hex)
                execution_target = str(shard.get("execution_target") or "planned").strip()
                shard_status = (
                    "queued_local"
                    if execution_target in {"windows_local", "any_worker"}
                    else str(shard.get("status") or "planned").strip().lower()
                )
                row = await conn.fetchrow(
                    f"""
                        INSERT INTO {shards_table} (
                            owner_id,
                            run_id,
                            shard_id,
                            repo_id,
                            lane_id,
                            lane_label_value,
                            execution_target,
                            command_json,
                            env_refs_json,
                            artifact_contract,
                            required_capabilities,
                            relay_mode,
                            metadata_json,
                            status,
                            result_json,
                            error_json,
                            created_at,
                            updated_at
                        ) VALUES (
                            $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, $10::jsonb,
                            $11::jsonb, $12, $13::jsonb, $14, '{{}}'::jsonb, '{{}}'::jsonb, $15, $15
                        )
                        RETURNING *
                        """,
                    owner_id,
                    run_id,
                    shard_id,
                    repo_id,
                    str(shard.get("lane_id") or shard_id),
                    self._encrypt_text(
                        str(shard.get("lane_label") or shard.get("lane_id") or shard_id)
                    ),
                    execution_target,
                    json.dumps(list(shard.get("command") or [])),
                    json.dumps(list(shard.get("env_refs") or [])),
                    json.dumps(dict(shard.get("artifact_contract") or {})),
                    json.dumps(list(shard.get("required_capabilities") or [])),
                    str(shard.get("relay_mode") or "direct"),
                    json.dumps(dict(shard.get("metadata") or {})),
                    shard_status if shard_status in _RUN_STATUSES else "planned",
                    created_at,
                )
                if row is None:
                    raise RuntimeError("Create shard returned no row")
                if execution_target in {"windows_local", "any_worker"}:
                    payload = dict(shard.get("payload") or {})
                    payload.setdefault(
                        "command",
                        list(shard.get("command") or []),
                    )
                    payload.setdefault(
                        "repo_root",
                        str(shard.get("workspace_root") or ""),
                    )
                    payload.setdefault(
                        "workspace_root",
                        str(shard.get("workspace_root") or ""),
                    )
                    payload.setdefault("execution_target", execution_target)
                    payload.setdefault(
                        "artifact_contract",
                        dict(shard.get("artifact_contract") or {}),
                    )
                    payload.setdefault(
                        "resource_class",
                        str((shard.get("metadata") or {}).get("resource_class") or ""),
                    )
                    payload.setdefault(
                        "parallel_group",
                        str((shard.get("metadata") or {}).get("parallel_group") or ""),
                    )
                    if "container_spec" not in payload and shard.get("container_spec") is not None:
                        payload["container_spec"] = dict(shard.get("container_spec") or {})
                    shard_payload = dict(shard.get("payload") or {})
                    if "container_spec" not in payload and isinstance(
                        shard_payload.get("container_spec"),
                        dict,
                    ):
                        payload["container_spec"] = dict(shard_payload.get("container_spec") or {})
                    if (
                        "compose_project" not in payload
                        and shard_payload.get("compose_project") is not None
                    ):
                        payload["compose_project"] = shard_payload.get("compose_project")
                    if "cleanup_labels" not in payload and isinstance(
                        shard_payload.get("cleanup_labels"),
                        dict,
                    ):
                        payload["cleanup_labels"] = dict(shard_payload.get("cleanup_labels") or {})
                    if "network_contract" not in payload and isinstance(
                        shard_payload.get("network_contract"),
                        dict,
                    ):
                        payload["network_contract"] = dict(
                            shard_payload.get("network_contract") or {}
                        )
                    payload.setdefault(
                        "idempotency_key",
                        str(shard.get("idempotency_key") or f"{run_id}:{shard_id}"),
                    )
                    await conn.execute(
                        f"""
                            INSERT INTO {jobs_table} (
                                scope_id,
                                job_id,
                                owner_id,
                                run_id,
                                shard_id,
                                repo_id,
                                action_name,
                                runner_name,
                                payload_json,
                                required_capabilities,
                                artifact_contract,
                                status,
                                idempotency_key,
                                execution_target,
                                created_at,
                                updated_at
                            ) VALUES (
                                $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb,
                                $11::jsonb, 'queued', $12, $13, $14, $14
                            )
                            """,
                        scope_id,
                        str(shard.get("job_id") or uuid4().hex),
                        owner_id,
                        run_id,
                        shard_id,
                        repo_id,
                        str(shard.get("action") or "ci.test.run"),
                        str(shard.get("runner") or "command"),
                        json.dumps(payload),
                        json.dumps(list(shard.get("required_capabilities") or ["ci.test.run"])),
                        json.dumps(dict(shard.get("artifact_contract") or {})),
                        str(payload["idempotency_key"]),
                        execution_target,
                        created_at,
                    )
        return await self.get_run(owner_id, run_id) or self._run_from_row(dict(run_row))

    async def list_runs(
        self,
        owner_id: str,
        *,
        repo_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_runs'
        params: list[Any] = [owner_id]
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
        """
        if repo_id:
            params.append(repo_id)
            query += f" AND repo_id = ${len(params)}"
        params.append(max(1, limit))
        query += f" ORDER BY created_at DESC LIMIT ${len(params)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._run_from_row(dict(row)) for row in rows]

    async def get_run(self, owner_id: str, run_id: str) -> dict[str, Any] | None:
        runs_table = f'"{self._schema}".owner_ci_runs'
        shards_table = f'"{self._schema}".owner_ci_shards'
        jobs_table = f'"{self._schema}".owner_ci_worker_jobs'
        async with self._pool.acquire() as conn:
            run_row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {runs_table}
                 WHERE owner_id = $1
                   AND run_id = $2
                 LIMIT 1
                """,
                owner_id,
                run_id,
            )
            if run_row is None:
                return None
            shard_rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {shards_table}
                 WHERE owner_id = $1
                   AND run_id = $2
                 ORDER BY created_at ASC
                """,
                owner_id,
                run_id,
            )
            job_rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {jobs_table}
                 WHERE owner_id = $1
                   AND run_id = $2
                 ORDER BY created_at ASC
                """,
                owner_id,
                run_id,
            )
        output = self._run_from_row(dict(run_row))
        output["shards"] = [self._shard_from_row(dict(row)) for row in shard_rows]
        output["worker_jobs"] = [self._worker_job_from_row(dict(row)) for row in job_rows]
        return output

    async def store_run_review(
        self,
        owner_id: str,
        run_id: str,
        review: dict[str, Any],
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_runs'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {table}
                   SET review_receipts = $3::jsonb,
                       updated_at = now()
                 WHERE owner_id = $1
                   AND run_id = $2
                RETURNING *
                """,
                owner_id,
                run_id,
                json.dumps(review),
            )
        if row is None:
            return None
        await self._recompute_run_status(owner_id, run_id)
        return await self.get_run(owner_id, run_id)

    async def store_run_github_receipt(
        self,
        owner_id: str,
        run_id: str,
        receipt: dict[str, Any],
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_runs'
        async with self._pool.acquire() as conn:
            current = await conn.fetchrow(
                f"""
                SELECT github_receipts
                  FROM {table}
                 WHERE owner_id = $1
                   AND run_id = $2
                 LIMIT 1
                """,
                owner_id,
                run_id,
            )
            if current is None:
                return None
            merged = _merge_json_dict(
                self._coerce_json_object(current["github_receipts"], "github_receipts"),
                receipt,
            )
            row = await conn.fetchrow(
                f"""
                UPDATE {table}
                   SET github_receipts = $3::jsonb,
                       updated_at = now()
                 WHERE owner_id = $1
                   AND run_id = $2
                RETURNING *
                """,
                owner_id,
                run_id,
                json.dumps(merged),
            )
        if row is None:
            return None
        await self._recompute_run_status(owner_id, run_id)
        return await self.get_run(owner_id, run_id)

    async def merge_run_metadata(
        self,
        owner_id: str,
        run_id: str,
        metadata_patch: dict[str, Any],
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_runs'
        async with self._pool.acquire() as conn:
            current = await conn.fetchrow(
                f"""
                SELECT metadata_json
                  FROM {table}
                 WHERE owner_id = $1
                   AND run_id = $2
                 LIMIT 1
                """,
                owner_id,
                run_id,
            )
            if current is None:
                return None
            merged = _merge_json_dict(
                self._coerce_json_object(current["metadata_json"], "metadata_json"),
                metadata_patch,
            )
            row = await conn.fetchrow(
                f"""
                UPDATE {table}
                   SET metadata_json = $3::jsonb,
                       updated_at = now()
                 WHERE owner_id = $1
                   AND run_id = $2
                RETURNING *
                """,
                owner_id,
                run_id,
                json.dumps(merged),
            )
        return self._run_from_row(dict(row)) if row is not None else None

    async def set_run_status(
        self,
        owner_id: str,
        run_id: str,
        status: str,
    ) -> dict[str, Any] | None:
        normalized = status.strip().lower()
        if normalized not in _RUN_STATUSES:
            raise ValueError(f"Invalid run status: {status}")
        table = f'"{self._schema}".owner_ci_runs'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {table}
                   SET status = $3,
                       updated_at = now()
                 WHERE owner_id = $1
                   AND run_id = $2
                RETURNING *
                """,
                owner_id,
                run_id,
                normalized,
            )
        return self._run_from_row(dict(row)) if row is not None else None

    async def bootstrap_worker_node_session(
        self,
        *,
        scope_id: str,
        node_id: str,
        node_name: str | None,
        capabilities: list[str],
        metadata: dict[str, Any] | None,
        session_ttl_seconds: int,
        session_id: str | None = None,
        session_token: str | None = None,
        signing_secret: str | None = None,
    ) -> dict[str, Any]:
        sessions_table = f'"{self._schema}".owner_ci_worker_sessions'
        nodes_table = f'"{self._schema}".owner_ci_worker_nodes'
        next_session_id = session_id or uuid4().hex
        next_session_token = session_token or secrets.token_urlsafe(32)
        next_signing_secret = signing_secret or secrets.token_hex(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=max(300, session_ttl_seconds))
        token_hash = self.hash_worker_token(next_session_token)
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                f"""
                    INSERT INTO {nodes_table} (
                        scope_id,
                        node_id,
                        node_name,
                        capabilities_json,
                        status,
                        health_status,
                        metadata_json,
                        created_at,
                        updated_at,
                        last_heartbeat_at
                    ) VALUES (
                        $1, $2, $3, $4::jsonb, 'registered', 'healthy', $5::jsonb,
                        now(), now(), now()
                    )
                    ON CONFLICT (scope_id, node_id) DO UPDATE SET
                        node_name = EXCLUDED.node_name,
                        capabilities_json = EXCLUDED.capabilities_json,
                        status = 'registered',
                        health_status = 'healthy',
                        metadata_json = EXCLUDED.metadata_json,
                        updated_at = now(),
                        last_heartbeat_at = now()
                    """,
                scope_id,
                node_id,
                node_name,
                json.dumps(capabilities),
                json.dumps(dict(metadata or {})),
            )
            await conn.execute(
                f"""
                    INSERT INTO {sessions_table} (
                        scope_id,
                        node_id,
                        session_id,
                        token_hash,
                        signing_secret,
                        status,
                        health_status,
                        node_metadata,
                        expires_at,
                        revoked_at,
                        created_at,
                        updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, 'registered', 'healthy', $6::jsonb,
                        $7, NULL, now(), now()
                    )
                    """,
                scope_id,
                node_id,
                next_session_id,
                token_hash,
                next_signing_secret,
                json.dumps(dict(metadata or {})),
                expires_at,
            )
        return {
            "session_id": next_session_id,
            "token": next_session_token,
            "signing_secret": next_signing_secret,
            "expires_at": expires_at.isoformat(),
        }

    async def rotate_worker_session_credentials(
        self,
        *,
        scope_id: str,
        node_id: str,
        session_id: str,
        session_ttl_seconds: int,
    ) -> dict[str, Any]:
        sessions_table = f'"{self._schema}".owner_ci_worker_sessions'
        next_token = secrets.token_urlsafe(32)
        next_signing_secret = secrets.token_hex(32)
        expires_at = datetime.now(UTC) + timedelta(seconds=max(300, session_ttl_seconds))
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {sessions_table}
                   SET token_hash = $4,
                       signing_secret = $5,
                       expires_at = $6,
                       updated_at = now()
                 WHERE scope_id = $1
                   AND node_id = $2
                   AND session_id = $3
                   AND revoked_at IS NULL
                RETURNING *
                """,
                scope_id,
                node_id,
                session_id,
                self.hash_worker_token(next_token),
                next_signing_secret,
                expires_at,
            )
        if row is None:
            raise ValueError("Worker session not found")
        return {
            "session_id": session_id,
            "token": next_token,
            "signing_secret": next_signing_secret,
            "expires_at": expires_at.isoformat(),
        }

    async def get_worker_session_auth(
        self,
        *,
        scope_id: str,
        node_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        sessions_table = f'"{self._schema}".owner_ci_worker_sessions'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {sessions_table}
                 WHERE scope_id = $1
                   AND node_id = $2
                   AND session_id = $3
                 LIMIT 1
                """,
                scope_id,
                node_id,
                session_id,
            )
        return dict(row) if row is not None else None

    async def touch_worker_session(
        self,
        *,
        scope_id: str,
        node_id: str,
        session_id: str,
    ) -> None:
        sessions_table = f'"{self._schema}".owner_ci_worker_sessions'
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE {sessions_table}
                   SET updated_at = now()
                 WHERE scope_id = $1
                   AND node_id = $2
                   AND session_id = $3
                """,
                scope_id,
                node_id,
                session_id,
            )

    async def register_worker_node(
        self,
        *,
        scope_id: str,
        node_id: str,
        node_name: str | None,
        capabilities: list[str],
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_worker_nodes'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    scope_id,
                    node_id,
                    node_name,
                    capabilities_json,
                    status,
                    health_status,
                    metadata_json,
                    created_at,
                    updated_at,
                    last_heartbeat_at
                ) VALUES (
                    $1, $2, $3, $4::jsonb, 'active', 'healthy', $5::jsonb, now(), now(), now()
                )
                ON CONFLICT (scope_id, node_id) DO UPDATE SET
                    node_name = EXCLUDED.node_name,
                    capabilities_json = EXCLUDED.capabilities_json,
                    status = 'active',
                    health_status = 'healthy',
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now(),
                    last_heartbeat_at = now()
                RETURNING *
                """,
                scope_id,
                node_id,
                node_name,
                json.dumps(capabilities),
                json.dumps(dict(metadata or {})),
            )
        return dict(row) if row is not None else {}

    async def heartbeat_worker_node(
        self,
        *,
        scope_id: str,
        node_id: str,
        health_status: str,
        metadata: dict[str, Any] | None,
    ) -> dict[str, Any]:
        nodes_table = f'"{self._schema}".owner_ci_worker_nodes'
        sessions_table = f'"{self._schema}".owner_ci_worker_sessions'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {nodes_table}
                   SET health_status = $3,
                       metadata_json = $4::jsonb,
                       updated_at = now(),
                       last_heartbeat_at = now()
                 WHERE scope_id = $1
                   AND node_id = $2
                RETURNING *
                """,
                scope_id,
                node_id,
                health_status,
                json.dumps(dict(metadata or {})),
            )
            await conn.execute(
                f"""
                UPDATE {sessions_table}
                   SET health_status = $3,
                       node_metadata = $4::jsonb,
                       updated_at = now()
                 WHERE scope_id = $1
                   AND node_id = $2
                   AND revoked_at IS NULL
                """,
                scope_id,
                node_id,
                health_status,
                json.dumps(dict(metadata or {})),
            )
        return dict(row) if row is not None else {}

    async def claim_worker_job(
        self,
        *,
        scope_id: str,
        node_id: str,
        required_capabilities: list[str],
        session_id: str | None = None,
    ) -> dict[str, Any] | None:
        jobs_table = f'"{self._schema}".owner_ci_worker_jobs'
        shards_table = f'"{self._schema}".owner_ci_shards'
        async with self._pool.acquire() as conn, conn.transaction():
            rows = await conn.fetch(
                f"""
                    SELECT *
                      FROM {jobs_table}
                     WHERE scope_id = $1
                       AND status = 'queued'
                     ORDER BY created_at ASC
                     LIMIT 20
                    """,
                scope_id,
            )
            claimed: dict[str, Any] | None = None
            for row in rows:
                job = self._worker_job_from_row(dict(row))
                job_required = set(job["required_capabilities"])
                if not job_required.issubset(set(required_capabilities or [])):
                    continue
                updated = await conn.fetchrow(
                    f"""
                        UPDATE {jobs_table}
                           SET status = 'claimed',
                               claimed_by_node_id = $3,
                               claimed_session_id = $4,
                               updated_at = now()
                         WHERE scope_id = $1
                           AND job_id = $2
                           AND status = 'queued'
                        RETURNING *
                        """,
                    scope_id,
                    job["job_id"],
                    node_id,
                    session_id,
                )
                if updated is None:
                    continue
                await conn.execute(
                    f"""
                        UPDATE {shards_table}
                           SET status = 'running',
                               started_at = COALESCE(started_at, now()),
                               updated_at = now()
                         WHERE owner_id = $1
                           AND shard_id = $2
                        """,
                    job["owner_id"],
                    job["shard_id"],
                )
                claimed = self._worker_job_from_row(dict(updated))
                break
        if claimed is None:
            return None
        await self._recompute_run_status(claimed["owner_id"], claimed["run_id"])
        return claimed

    async def submit_worker_job_result(
        self,
        *,
        scope_id: str,
        node_id: str,
        job_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        jobs_table = f'"{self._schema}".owner_ci_worker_jobs'
        shards_table = f'"{self._schema}".owner_ci_shards'
        completion_status = str(payload.get("status") or "failed").strip().lower()
        result_json = dict(payload.get("output") or {})
        error_json = dict(payload.get("error") or {})
        idempotency_key = str(payload.get("idempotency_key") or "").strip() or None
        async with self._pool.acquire() as conn, conn.transaction():
            row = await conn.fetchrow(
                f"""
                    SELECT *
                      FROM {jobs_table}
                     WHERE scope_id = $1
                       AND job_id = $2
                     LIMIT 1
                    """,
                scope_id,
                job_id,
            )
            if row is None:
                raise ValueError("Worker job not found")
            current = self._worker_job_from_row(dict(row))
            if current["status"] == "completed":
                if idempotency_key and idempotency_key == current["idempotency_key"]:
                    current["idempotent"] = True
                    return current
                raise ValueError("Worker job already completed")
            if current["claimed_by_node_id"] not in {None, node_id}:
                raise ValueError("Worker job was claimed by a different node")
            final_shard_status = "succeeded" if completion_status == "succeeded" else "failed"
            updated = await conn.fetchrow(
                f"""
                    UPDATE {jobs_table}
                       SET status = 'completed',
                           result_json = $3::jsonb,
                           error_json = $4::jsonb,
                           submitted_at = now(),
                           updated_at = now()
                     WHERE scope_id = $1
                       AND job_id = $2
                    RETURNING *
                    """,
                scope_id,
                job_id,
                json.dumps(result_json),
                json.dumps(error_json),
            )
            await conn.execute(
                f"""
                    UPDATE {shards_table}
                       SET status = $3,
                           result_json = $4::jsonb,
                           error_json = $5::jsonb,
                           completed_at = now(),
                           updated_at = now()
                     WHERE owner_id = $1
                       AND shard_id = $2
                    """,
                current["owner_id"],
                current["shard_id"],
                final_shard_status,
                json.dumps(result_json),
                json.dumps(error_json),
            )
            await self._store_worker_observability(
                conn=conn,
                owner_id=current["owner_id"],
                repo_id=current["repo_id"],
                run_id=current["run_id"],
                shard_id=current["shard_id"],
                node_id=node_id,
                final_status=final_shard_status,
                result_json=result_json,
                error_json=error_json,
            )
        await self._recompute_run_status(current["owner_id"], current["run_id"])
        if updated is None:
            raise RuntimeError("Worker job update returned no row")
        completed = self._worker_job_from_row(dict(updated))
        completed["idempotent"] = False
        return completed

    async def list_worker_nodes(self, scope_id: str) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_worker_nodes'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE scope_id = $1
                 ORDER BY updated_at DESC, node_id ASC
                """,
                scope_id,
            )
        return [dict(row) for row in rows]

    async def get_worker_node(self, scope_id: str, node_id: str) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_worker_nodes'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE scope_id = $1
                   AND node_id = $2
                 LIMIT 1
                """,
                scope_id,
                node_id,
            )
        return dict(row) if row is not None else None

    async def list_worker_jobs(
        self,
        scope_id: str,
        *,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_worker_jobs'
        params: list[Any] = [scope_id]
        query = f"""
            SELECT *
              FROM {table}
             WHERE scope_id = $1
        """
        if status:
            params.append(str(status).strip().lower())
            query += f" AND status = ${len(params)}"
        params.append(max(1, limit))
        query += f" ORDER BY created_at DESC LIMIT ${len(params)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._worker_job_from_row(dict(row)) for row in rows]

    async def create_compiled_plan(
        self,
        *,
        owner_id: str,
        repo_id: str,
        git_ref: str,
        mode: str,
        plan: dict[str, Any],
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_compiled_plans'
        compiled_plan_id = uuid4().hex
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    compiled_plan_id,
                    repo_id,
                    git_ref,
                    mode_value,
                    plan_json,
                    metadata_json,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, now(), now()
                )
                RETURNING *
                """,
                owner_id,
                compiled_plan_id,
                repo_id,
                git_ref,
                mode,
                json.dumps(plan),
                json.dumps(dict(metadata or {})),
            )
        if row is None:
            raise RuntimeError("Create compiled plan returned no row")
        return self._compiled_plan_from_row(dict(row))

    async def get_compiled_plan(
        self,
        owner_id: str,
        compiled_plan_id: str,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_compiled_plans'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND compiled_plan_id = $2
                 LIMIT 1
                """,
                owner_id,
                compiled_plan_id,
            )
        return self._compiled_plan_from_row(dict(row)) if row is not None else None

    async def list_compiled_plans(
        self,
        owner_id: str,
        *,
        repo_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_compiled_plans'
        params: list[Any] = [owner_id]
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
        """
        if repo_id:
            params.append(repo_id)
            query += f" AND repo_id = ${len(params)}"
        params.append(max(1, limit))
        query += f" ORDER BY created_at DESC LIMIT ${len(params)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._compiled_plan_from_row(dict(row)) for row in rows]

    async def upsert_schedule(
        self,
        *,
        owner_id: str,
        repo_id: str,
        name: str,
        schedule_kind: str,
        schedule_spec: dict[str, Any],
        active: bool = True,
        schedule_id: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_schedules'
        next_schedule_id = schedule_id or uuid4().hex
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    schedule_id,
                    repo_id,
                    name_value,
                    schedule_kind,
                    schedule_spec_json,
                    active,
                    metadata_json,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6::jsonb, $7, $8::jsonb, now(), now()
                )
                ON CONFLICT (owner_id, schedule_id) DO UPDATE SET
                    repo_id = EXCLUDED.repo_id,
                    name_value = EXCLUDED.name_value,
                    schedule_kind = EXCLUDED.schedule_kind,
                    schedule_spec_json = EXCLUDED.schedule_spec_json,
                    active = EXCLUDED.active,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now()
                RETURNING *
                """,
                owner_id,
                next_schedule_id,
                repo_id,
                self._encrypt_text(name.strip() or next_schedule_id),
                schedule_kind,
                json.dumps(schedule_spec),
                active,
                json.dumps(dict(metadata or {})),
            )
        if row is None:
            raise RuntimeError("Upsert schedule returned no row")
        return self._schedule_from_row(dict(row))

    async def list_schedules(
        self,
        owner_id: str,
        *,
        repo_id: str | None = None,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_schedules'
        params: list[Any] = [owner_id]
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
        """
        if repo_id:
            params.append(repo_id)
            query += f" AND repo_id = ${len(params)}"
        query += " ORDER BY updated_at DESC, schedule_id ASC"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._schedule_from_row(dict(row)) for row in rows]

    async def get_run_events(
        self,
        owner_id: str,
        run_id: str,
        *,
        shard_id: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_events'
        params: list[Any] = [owner_id, run_id]
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
               AND run_id = $2
        """
        if shard_id:
            params.append(shard_id)
            query += f" AND shard_id = ${len(params)}"
        params.append(max(1, limit))
        query += f" ORDER BY created_at DESC LIMIT ${len(params)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._event_from_row(dict(row)) for row in rows]

    async def get_run_log_chunks(
        self,
        owner_id: str,
        run_id: str,
        *,
        shard_id: str | None = None,
        query_text: str | None = None,
        limit: int = 500,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_log_chunks'
        params: list[Any] = [owner_id, run_id]
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
               AND run_id = $2
        """
        if shard_id:
            params.append(shard_id)
            query += f" AND shard_id = ${len(params)}"
        if query_text:
            params.append(f"%{query_text}%")
            query += f" AND message_value ILIKE ${len(params)}"
        params.append(max(1, limit))
        query += f" ORDER BY created_at DESC LIMIT ${len(params)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._log_chunk_from_row(dict(row)) for row in rows]

    async def get_run_debug_bundle(
        self,
        owner_id: str,
        run_id: str,
        *,
        shard_id: str | None = None,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_debug_bundles'
        params: list[Any] = [owner_id, run_id]
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
               AND run_id = $2
        """
        if shard_id:
            params.append(shard_id)
            query += f" AND shard_id = ${len(params)}"
        query += " ORDER BY updated_at DESC LIMIT 1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)
        return self._debug_bundle_from_row(dict(row)) if row is not None else None

    async def get_local_repo_readiness(
        self,
        repo: dict[str, Any],
    ) -> tuple[RepoReadinessReceipt | None, dict[str, Any] | None]:
        return _load_local_repo_readiness(repo)

    async def get_project_resource_report(
        self,
        owner_id: str,
        repo_id: str,
        *,
        limit: int = 30,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_project_usage_summaries'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND repo_id = $2
                 ORDER BY updated_at DESC
                 LIMIT $3
                """,
                owner_id,
                repo_id,
                max(1, limit),
            )
        items = [
            self._coerce_json_object(row["summary_json"], "summary_json")
            for row in rows
        ]
        return {
            "repo_id": repo_id,
            "items": items,
            "totals": {
                "run_count": len(items),
                "compute_minutes": round(
                    sum(float(item.get("compute_minutes") or 0.0) for item in items),
                    2,
                ),
                "peak_memory_mb": max(
                    [float(item.get("peak_memory_mb") or 0.0) for item in items] or [0.0]
                ),
                "peak_disk_used_bytes": max(
                    [int(item.get("disk_used_bytes") or 0) for item in items] or [0]
                ),
                "peak_container_count": max(
                    [int(item.get("container_count") or 0) for item in items] or [0]
                ),
            },
        }

    async def get_project_failure_report(
        self,
        owner_id: str,
        repo_id: str,
        *,
        limit: int = 20,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_runs'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND repo_id = $2
                   AND status IN ('failed', 'promotion_blocked')
                 ORDER BY updated_at DESC
                 LIMIT $3
                """,
                owner_id,
                repo_id,
                max(1, limit),
            )
        failures = [self._run_from_row(dict(row)) for row in rows]
        return {"repo_id": repo_id, "failures": failures}

    async def get_worker_resource_report(
        self,
        owner_id: str,
        node_id: str,
        *,
        limit: int = 100,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_resource_samples'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND node_id = $2
                 ORDER BY created_at DESC
                 LIMIT $3
                """,
                owner_id,
                node_id,
                max(1, limit),
            )
        samples = [self._resource_sample_from_row(dict(row)) for row in rows]
        return {
            "node_id": node_id,
            "samples": samples,
            "totals": {
                "sample_count": len(samples),
                "peak_memory_mb": max(
                    [
                        float((sample.get("sample") or {}).get("memory_mb") or 0.0)
                        for sample in samples
                    ]
                    or [0.0]
                ),
                "peak_disk_used_bytes": max(
                    [
                        int((sample.get("sample") or {}).get("disk_used_bytes") or 0)
                        for sample in samples
                    ]
                    or [0]
                ),
            },
        }

    async def get_reporting_readiness(self, owner_id: str) -> dict[str, Any]:
        repos = await self.list_repo_profiles(owner_id)
        recent_runs = await self.list_runs(owner_id, limit=max(200, len(repos) * 25, 50))

        repo_order: list[str] = []
        repo_index: dict[str, dict[str, Any]] = {}
        for repo in repos:
            repo_id = str(repo.get("repo_id") or "").strip()
            if not repo_id:
                continue
            repo_order.append(repo_id)
            repo_index[repo_id] = repo

        latest_run_ids: dict[str, str] = {}
        for run in recent_runs:
            repo_id = str(run.get("repo_id") or "").strip()
            run_id = str(run.get("run_id") or "").strip()
            if not repo_id or not run_id or repo_id in latest_run_ids:
                continue
            latest_run_ids[repo_id] = run_id
            if repo_id not in repo_index:
                repo_order.append(repo_id)
                repo_index[repo_id] = {
                    "repo_id": repo_id,
                    "display_name": repo_id,
                    "active": True,
                    "stack_kind": "unknown",
                    "metadata": {},
                }

        repo_receipts: list[RepoReadinessReceipt] = []
        repo_readiness: list[dict[str, Any]] = []
        worker_certification: dict[str, Any] | None = None
        worker_certification_source = "missing"
        for repo_id in repo_order:
            repo = dict(repo_index.get(repo_id) or {})
            latest_run = None
            latest_run_id = latest_run_ids.get(repo_id)
            if latest_run_id:
                latest_run = await self.get_run(owner_id, latest_run_id)

            embedded_local_receipt = None
            if latest_run is not None:
                embedded_local_receipt, _ = _load_local_repo_readiness_from_shards(
                    repo,
                    list(latest_run.get("shards") or []),
                )
            local_receipt, local_receipt_payload = _load_local_repo_readiness(repo)
            if embedded_local_receipt is not None:
                local_receipt = embedded_local_receipt

            if worker_certification is None and repo_id == "zetherion-ai":
                (
                    worker_certification,
                    worker_certification_payload,
                ) = _load_local_worker_certification(repo)
                if worker_certification_payload is not None:
                    worker_certification_source = "local_file"

            if latest_run is not None:
                receipt = build_repo_readiness_receipt(
                    repo=repo,
                    run=latest_run,
                    review=dict(latest_run.get("review_receipts") or {}),
                    release_receipt=dict(
                        (latest_run.get("metadata") or {}).get("release_verification") or {}
                    ),
                    local_receipt=local_receipt,
                )
            elif local_receipt is not None:
                receipt = local_receipt
            else:
                receipt = _pending_repo_readiness(
                    repo_id,
                    "No owner-CI run has produced readiness receipts yet.",
                )

            repo_receipts.append(receipt)
            repo_readiness.append(
                {
                    "repo_id": repo_id,
                    "display_name": str(repo.get("display_name") or repo_id),
                    "active": bool(repo.get("active", True)),
                    "stack_kind": str(repo.get("stack_kind") or "unknown"),
                    "platform_canary": bool((repo.get("metadata") or {}).get("platform_canary")),
                    "latest_run": (
                        {
                            "run_id": str(latest_run.get("run_id") or ""),
                            "status": str(latest_run.get("status") or ""),
                            "git_ref": str(latest_run.get("git_ref") or ""),
                            "created_at": latest_run.get("created_at"),
                            "updated_at": latest_run.get("updated_at"),
                        }
                        if latest_run is not None
                        else (
                            {
                                "run_id": None,
                                "status": str(local_receipt_payload.get("status") or ""),
                                "git_ref": str(
                                    (local_receipt_payload.get("metadata") or {}).get("git_sha")
                                    or ""
                                ),
                                "created_at": local_receipt_payload.get("recorded_at"),
                                "updated_at": local_receipt_payload.get("recorded_at"),
                            }
                            if local_receipt_payload is not None
                            else None
                        )
                    ),
                    "receipt_source": (
                        "owner_ci_run"
                        if latest_run is not None
                        else ("local_file" if local_receipt_payload is not None else "missing")
                    ),
                    "readiness": receipt.model_dump(mode="json"),
                }
            )

        workspace = build_workspace_readiness_receipt(repo_receipts)
        return {
            "generated_at": datetime.now(UTC).isoformat(),
            "workspace_readiness": workspace.model_dump(mode="json"),
            "repo_readiness": repo_readiness,
            "worker_certification": worker_certification,
            "worker_certification_source": worker_certification_source,
        }

    async def get_reporting_summary(self, owner_id: str) -> dict[str, Any]:
        repos = await self.list_repo_profiles(owner_id)
        runs = await self.list_runs(owner_id, limit=200)
        gaps = await self.list_agent_gap_events(owner_id, unresolved_only=True, limit=500)
        operations = await self.list_managed_operations(owner_id, limit=500)
        incidents = await self.list_operation_incidents_for_owner(
            owner_id,
            unresolved_only=True,
            limit=500,
        )
        by_repo: dict[str, dict[str, Any]] = {}
        for repo in repos:
            repo_id = str(repo["repo_id"])
            by_repo[repo_id] = {
                "repo_id": repo_id,
                "display_name": repo["display_name"],
                "active": repo["active"],
                "stack_kind": repo["stack_kind"],
                "platform_canary": bool((repo.get("metadata") or {}).get("platform_canary")),
                "run_count": 0,
                "failed_runs": 0,
                "ready_to_merge_runs": 0,
                "open_gaps": 0,
                "blocker_gaps": 0,
                "operation_count": 0,
                "failed_operations": 0,
                "open_incidents": 0,
            }
        for run in runs:
            repo_id = str(run.get("repo_id") or "")
            summary = by_repo.setdefault(
                repo_id,
                {
                    "repo_id": repo_id,
                    "display_name": repo_id,
                    "active": True,
                    "stack_kind": "unknown",
                    "platform_canary": False,
                    "run_count": 0,
                    "failed_runs": 0,
                    "ready_to_merge_runs": 0,
                    "open_gaps": 0,
                    "blocker_gaps": 0,
                    "operation_count": 0,
                    "failed_operations": 0,
                    "open_incidents": 0,
                },
            )
            summary["run_count"] += 1
            if run.get("status") == "failed":
                summary["failed_runs"] += 1
            if run.get("status") == "ready_to_merge":
                summary["ready_to_merge_runs"] += 1
        for gap in gaps:
            repo_id = str(gap.get("repo_id") or "")
            if not repo_id:
                continue
            summary = by_repo.setdefault(
                repo_id,
                {
                    "repo_id": repo_id,
                    "display_name": repo_id,
                    "active": True,
                    "stack_kind": "unknown",
                    "platform_canary": False,
                    "run_count": 0,
                    "failed_runs": 0,
                    "ready_to_merge_runs": 0,
                    "open_gaps": 0,
                    "blocker_gaps": 0,
                    "operation_count": 0,
                    "failed_operations": 0,
                    "open_incidents": 0,
                },
            )
            summary["open_gaps"] += 1
            if bool(gap.get("blocker")):
                summary["blocker_gaps"] += 1
        for operation in operations:
            repo_id = str(operation.get("repo_id") or "")
            summary = by_repo.setdefault(
                repo_id,
                {
                    "repo_id": repo_id,
                    "display_name": repo_id,
                    "active": True,
                    "stack_kind": "unknown",
                    "platform_canary": False,
                    "run_count": 0,
                    "failed_runs": 0,
                    "ready_to_merge_runs": 0,
                    "open_gaps": 0,
                    "blocker_gaps": 0,
                    "operation_count": 0,
                    "failed_operations": 0,
                    "open_incidents": 0,
                },
            )
            summary["operation_count"] += 1
            if str(operation.get("status") or "") in {"failed", "error"}:
                summary["failed_operations"] += 1
        for incident in incidents:
            operation = next(
                (
                    item
                    for item in operations
                    if str(item.get("operation_id") or "")
                    == str(incident.get("operation_id") or "")
                ),
                {},
            )
            repo_id = str(operation.get("repo_id") or "")
            if not repo_id:
                continue
            summary = by_repo.setdefault(
                repo_id,
                {
                    "repo_id": repo_id,
                    "display_name": repo_id,
                    "active": True,
                    "stack_kind": "unknown",
                    "platform_canary": False,
                    "run_count": 0,
                    "failed_runs": 0,
                    "ready_to_merge_runs": 0,
                    "open_gaps": 0,
                    "blocker_gaps": 0,
                    "operation_count": 0,
                    "failed_operations": 0,
                    "open_incidents": 0,
                },
            )
            summary["open_incidents"] += 1
        return {
            "owner_id": owner_id,
            "repos": list(by_repo.values()),
            "run_count": len(runs),
            "gaps": {
                "open_total": len(gaps),
                "blocker_total": sum(1 for gap in gaps if bool(gap.get("blocker"))),
                "recurring_total": sum(
                    1 for gap in gaps if int(gap.get("occurrence_count") or 0) > 1
                ),
            },
            "operations": {
                "total": len(operations),
                "failed_total": sum(
                    1
                    for operation in operations
                    if str(operation.get("status") or "") in {"failed", "error"}
                ),
                "active_total": sum(
                    1
                    for operation in operations
                    if str(operation.get("status") or "") not in {"resolved", "succeeded"}
                ),
                "incident_total": len(incidents),
                "blocking_incident_total": sum(
                    1 for incident in incidents if bool(incident.get("blocking"))
                ),
            },
        }

    async def store_agent_bootstrap_manifest(
        self,
        owner_id: str,
        client_id: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_bootstrap_manifests'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    client_id,
                    manifest_json,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3::jsonb, now(), now()
                )
                ON CONFLICT (owner_id, client_id) DO UPDATE SET
                    manifest_json = EXCLUDED.manifest_json,
                    updated_at = now()
                RETURNING *
                """,
                owner_id,
                client_id,
                json.dumps(manifest),
            )
        if row is None:
            raise RuntimeError("Store agent bootstrap manifest returned no row")
        payload = self._coerce_json_object(row["manifest_json"], "manifest_json")
        payload["client_id"] = client_id
        return payload

    async def get_agent_bootstrap_manifest(
        self,
        owner_id: str,
        client_id: str,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_agent_bootstrap_manifests'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND client_id = $2
                 LIMIT 1
                """,
                owner_id,
                client_id,
            )
        if row is None:
            return None
        payload = self._coerce_json_object(row["manifest_json"], "manifest_json")
        payload["client_id"] = client_id
        return payload

    async def store_agent_setup_receipt(
        self,
        owner_id: str,
        *,
        client_id: str,
        receipt: dict[str, Any],
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_setup_receipts'
        receipt_id = str(receipt.get("receipt_id") or uuid4().hex)
        payload = {**receipt, "client_id": client_id, "receipt_id": receipt_id}
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    receipt_id,
                    client_id,
                    receipt_json,
                    created_at
                ) VALUES (
                    $1, $2, $3, $4::jsonb, now()
                )
                RETURNING *
                """,
                owner_id,
                receipt_id,
                client_id,
                json.dumps(payload),
            )
        if row is None:
            raise RuntimeError("Store agent setup receipt returned no row")
        return self._coerce_json_object(row["receipt_json"], "receipt_json")

    async def upsert_agent_docs_manifest(
        self,
        owner_id: str,
        *,
        slug: str,
        title: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_docs_manifests'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    slug,
                    title_value,
                    manifest_json,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4::jsonb, now(), now()
                )
                ON CONFLICT (owner_id, slug) DO UPDATE SET
                    title_value = EXCLUDED.title_value,
                    manifest_json = EXCLUDED.manifest_json,
                    updated_at = now()
                RETURNING *
                """,
                owner_id,
                slug,
                self._encrypt_text(title.strip() or slug),
                json.dumps(manifest),
            )
        if row is None:
            raise RuntimeError("Upsert agent docs manifest returned no row")
        return {
            "slug": slug,
            "title": self._decrypt_text(str(row["title_value"])) or slug,
            "manifest": self._coerce_json_object(row["manifest_json"], "manifest_json"),
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    async def list_agent_docs_manifests(self, owner_id: str) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_agent_docs_manifests'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                 ORDER BY slug ASC
                """,
                owner_id,
            )
        return [
            {
                "slug": str(row["slug"]),
                "title": self._decrypt_text(str(row["title_value"])) or str(row["slug"]),
                "manifest": self._coerce_json_object(row["manifest_json"], "manifest_json"),
                "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
            }
            for row in rows
        ]

    async def get_agent_docs_manifest(self, owner_id: str, slug: str) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_agent_docs_manifests'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND slug = $2
                 LIMIT 1
                """,
                owner_id,
                slug,
            )
        if row is None:
            return None
        return {
            "slug": str(row["slug"]),
            "title": self._decrypt_text(str(row["title_value"])) or str(row["slug"]),
            "manifest": self._coerce_json_object(row["manifest_json"], "manifest_json"),
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
        }

    async def upsert_agent_principal(
        self,
        owner_id: str,
        *,
        principal_id: str,
        display_name: str,
        principal_type: str = "codex",
        allowed_scopes: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_principals'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    principal_id,
                    display_name_value,
                    principal_type,
                    allowed_scopes_json,
                    metadata_json,
                    active,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, now(), now()
                )
                ON CONFLICT (owner_id, principal_id) DO UPDATE SET
                    display_name_value = EXCLUDED.display_name_value,
                    principal_type = EXCLUDED.principal_type,
                    allowed_scopes_json = EXCLUDED.allowed_scopes_json,
                    metadata_json = EXCLUDED.metadata_json,
                    active = EXCLUDED.active,
                    updated_at = now()
                RETURNING *
                """,
                owner_id,
                principal_id,
                self._encrypt_text(display_name.strip() or principal_id),
                principal_type.strip() or "codex",
                json.dumps(list(allowed_scopes or [])),
                json.dumps(dict(metadata or {})),
                active,
            )
        if row is None:
            raise RuntimeError("Upsert agent principal returned no row")
        return self._agent_principal_from_row(dict(row))

    async def get_agent_principal(self, owner_id: str, principal_id: str) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_agent_principals'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND principal_id = $2
                 LIMIT 1
                """,
                owner_id,
                principal_id,
            )
        return self._agent_principal_from_row(dict(row)) if row is not None else None

    async def list_agent_principals(self, owner_id: str) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_agent_principals'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                 ORDER BY updated_at DESC, principal_id ASC
                """,
                owner_id,
            )
        return [self._agent_principal_from_row(dict(row)) for row in rows]

    async def upsert_external_service_connector(
        self,
        owner_id: str,
        *,
        connector_id: str,
        service_kind: str,
        display_name: str,
        auth_kind: str,
        secret_value: str | None = None,
        policy: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_external_service_connectors'
        metadata_payload = dict(metadata or {})
        if secret_value is not None:
            metadata_payload["rotated_at"] = datetime.now(UTC).isoformat()
        async with self._pool.acquire() as conn:
            if secret_value is None:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {table} (
                        owner_id,
                        connector_id,
                        service_kind,
                        display_name_value,
                        auth_kind,
                        policy_json,
                        metadata_json,
                        active,
                        created_at,
                        updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, now(), now()
                    )
                    ON CONFLICT (owner_id, connector_id) DO UPDATE SET
                        service_kind = EXCLUDED.service_kind,
                        display_name_value = EXCLUDED.display_name_value,
                        auth_kind = EXCLUDED.auth_kind,
                        policy_json = EXCLUDED.policy_json,
                        metadata_json = EXCLUDED.metadata_json,
                        active = EXCLUDED.active,
                        updated_at = now()
                    RETURNING *
                    """,
                    owner_id,
                    connector_id,
                    service_kind,
                    self._encrypt_text(display_name.strip() or connector_id),
                    auth_kind,
                    json.dumps(dict(policy or {})),
                    json.dumps(metadata_payload),
                    active,
                )
            else:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {table} (
                        owner_id,
                        connector_id,
                        service_kind,
                        display_name_value,
                        auth_kind,
                        secret_value,
                        policy_json,
                        metadata_json,
                        active,
                        created_at,
                        updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7::jsonb, $8::jsonb, $9, now(), now()
                    )
                    ON CONFLICT (owner_id, connector_id) DO UPDATE SET
                        service_kind = EXCLUDED.service_kind,
                        display_name_value = EXCLUDED.display_name_value,
                        auth_kind = EXCLUDED.auth_kind,
                        secret_value = EXCLUDED.secret_value,
                        policy_json = EXCLUDED.policy_json,
                        metadata_json = EXCLUDED.metadata_json,
                        active = EXCLUDED.active,
                        updated_at = now()
                    RETURNING *
                    """,
                    owner_id,
                    connector_id,
                    service_kind,
                    self._encrypt_text(display_name.strip() or connector_id),
                    auth_kind,
                    self._encrypt_text(secret_value),
                    json.dumps(dict(policy or {})),
                    json.dumps(metadata_payload),
                    active,
                )
        if row is None:
            raise RuntimeError("Upsert external service connector returned no row")
        return self._external_connector_from_row(dict(row))

    async def get_external_service_connector(
        self,
        owner_id: str,
        connector_id: str,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_external_service_connectors'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND connector_id = $2
                 LIMIT 1
                """,
                owner_id,
                connector_id,
            )
        return self._external_connector_from_row(dict(row)) if row is not None else None

    async def get_external_service_connector_with_secret(
        self,
        owner_id: str,
        connector_id: str,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_external_service_connectors'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND connector_id = $2
                 LIMIT 1
                """,
                owner_id,
                connector_id,
            )
        if row is None:
            return None
        connector = self._external_connector_from_row(dict(row))
        connector["secret_value"] = (
            self._decrypt_text(str(row["secret_value"])) if row.get("secret_value") else None
        )
        return connector

    async def list_external_service_connectors(
        self,
        owner_id: str,
        *,
        service_kind: str | None = None,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_external_service_connectors'
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
        """
        params: list[Any] = [owner_id]
        if service_kind:
            query += " AND service_kind = $2"
            params.append(service_kind)
        query += " ORDER BY updated_at DESC, connector_id ASC"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._external_connector_from_row(dict(row)) for row in rows]

    async def replace_external_access_grants(
        self,
        owner_id: str,
        *,
        principal_id: str,
        grants: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_external_access_grants'
        async with self._pool.acquire() as conn, conn.transaction():
            await conn.execute(
                f"""
                DELETE FROM {table}
                 WHERE owner_id = $1
                   AND principal_id = $2
                """,
                owner_id,
                principal_id,
            )
            stored_rows = []
            for raw_grant in grants:
                resource_type = str(raw_grant.get("resource_type") or "").strip()
                resource_id = str(raw_grant.get("resource_id") or "").strip()
                if not resource_type or not resource_id:
                    continue
                grant_key = str(raw_grant.get("grant_key") or uuid4().hex)
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {table} (
                        owner_id,
                        principal_id,
                        grant_key,
                        resource_type,
                        resource_id,
                        capabilities_json,
                        metadata_json,
                        active,
                        created_at,
                        updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6::jsonb, $7::jsonb, $8, now(), now()
                    )
                    RETURNING *
                    """,
                    owner_id,
                    principal_id,
                    grant_key,
                    resource_type,
                    resource_id,
                    json.dumps(list(raw_grant.get("capabilities") or [])),
                    json.dumps(dict(raw_grant.get("metadata") or {})),
                    bool(raw_grant.get("active", True)),
                )
                if row is not None:
                    stored_rows.append(self._external_access_grant_from_row(dict(row)))
        return stored_rows

    async def list_external_access_grants(
        self,
        owner_id: str,
        *,
        principal_id: str | None = None,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_external_access_grants'
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
        """
        params: list[Any] = [owner_id]
        if principal_id:
            query += " AND principal_id = $2"
            params.append(principal_id)
        query += " ORDER BY updated_at DESC, grant_key ASC"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._external_access_grant_from_row(dict(row)) for row in rows]

    async def upsert_agent_app_profile(
        self,
        owner_id: str,
        *,
        app_id: str,
        display_name: str,
        profile: dict[str, Any],
        active: bool = True,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_app_profiles'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    app_id,
                    display_name_value,
                    profile_json,
                    active,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4::jsonb, $5, now(), now()
                )
                ON CONFLICT (owner_id, app_id) DO UPDATE SET
                    display_name_value = EXCLUDED.display_name_value,
                    profile_json = EXCLUDED.profile_json,
                    active = EXCLUDED.active,
                    updated_at = now()
                RETURNING *
                """,
                owner_id,
                app_id,
                self._encrypt_text(display_name.strip() or app_id),
                json.dumps(profile),
                active,
            )
        if row is None:
            raise RuntimeError("Upsert agent app profile returned no row")
        return self._agent_app_profile_from_row(dict(row))

    async def get_agent_app_profile(self, owner_id: str, app_id: str) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_agent_app_profiles'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND app_id = $2
                 LIMIT 1
                """,
                owner_id,
                app_id,
            )
        return self._agent_app_profile_from_row(dict(row)) if row is not None else None

    async def find_agent_app_profile(self, app_id: str) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_agent_app_profiles'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE app_id = $1
                   AND active = TRUE
                 ORDER BY updated_at DESC
                 LIMIT 1
                """,
                app_id,
            )
        return self._agent_app_profile_from_row(dict(row)) if row is not None else None

    async def list_agent_app_profiles(self, owner_id: str) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_agent_app_profiles'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                 ORDER BY updated_at DESC, app_id ASC
                """,
                owner_id,
            )
        return [self._agent_app_profile_from_row(dict(row)) for row in rows]

    async def upsert_agent_knowledge_pack(
        self,
        owner_id: str,
        *,
        app_id: str,
        version: str,
        pack: dict[str, Any],
        current: bool = True,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_knowledge_packs'
        async with self._pool.acquire() as conn, conn.transaction():
            if current:
                await conn.execute(
                    f"""
                    UPDATE {table}
                       SET current_version = FALSE,
                           updated_at = now()
                     WHERE owner_id = $1
                       AND app_id = $2
                    """,
                    owner_id,
                    app_id,
                )
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    app_id,
                    version_value,
                    pack_json,
                    current_version,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4::jsonb, $5, now(), now()
                )
                ON CONFLICT (owner_id, app_id, version_value) DO UPDATE SET
                    pack_json = EXCLUDED.pack_json,
                    current_version = EXCLUDED.current_version,
                    updated_at = now()
                RETURNING *
                """,
                owner_id,
                app_id,
                version,
                json.dumps(pack),
                current,
            )
        if row is None:
            raise RuntimeError("Upsert agent knowledge pack returned no row")
        return self._agent_knowledge_pack_from_row(dict(row))

    async def get_agent_knowledge_pack(
        self,
        owner_id: str,
        app_id: str,
        *,
        version: str | None = None,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_agent_knowledge_packs'
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
               AND app_id = $2
        """
        params: list[Any] = [owner_id, app_id]
        if version:
            query += " AND version_value = $3"
            params.append(version)
        else:
            query += " AND current_version = TRUE"
        query += " ORDER BY updated_at DESC LIMIT 1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)
        return self._agent_knowledge_pack_from_row(dict(row)) if row is not None else None

    async def create_workspace_bundle(
        self,
        owner_id: str,
        *,
        principal_id: str,
        app_id: str,
        repo_id: str,
        git_ref: str,
        bundle: dict[str, Any],
        resolved_ref: str | None = None,
        expires_at: datetime | None = None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_workspace_bundles'
        bundle_id = str(bundle.get("bundle_id") or uuid4().hex)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    bundle_id,
                    principal_id,
                    app_id,
                    repo_id,
                    git_ref,
                    resolved_ref,
                    bundle_json,
                    expires_at,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9, now(), now()
                )
                RETURNING *
                """,
                owner_id,
                bundle_id,
                principal_id,
                app_id,
                repo_id,
                git_ref,
                resolved_ref,
                json.dumps({**bundle, "bundle_id": bundle_id}),
                expires_at,
            )
        if row is None:
            raise RuntimeError("Create workspace bundle returned no row")
        return self._workspace_bundle_from_row(dict(row))

    async def get_workspace_bundle(
        self,
        owner_id: str,
        bundle_id: str,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_workspace_bundles'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND bundle_id = $2
                 LIMIT 1
                """,
                owner_id,
                bundle_id,
            )
        return self._workspace_bundle_from_row(dict(row)) if row is not None else None

    async def mark_workspace_bundle_downloaded(self, owner_id: str, bundle_id: str) -> None:
        table = f'"{self._schema}".owner_ci_workspace_bundles'
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE {table}
                   SET downloaded_at = now(),
                       updated_at = now()
                 WHERE owner_id = $1
                   AND bundle_id = $2
                """,
                owner_id,
                bundle_id,
            )

    async def create_publish_candidate(
        self,
        owner_id: str,
        *,
        principal_id: str,
        app_id: str,
        repo_id: str,
        base_sha: str,
        candidate: dict[str, Any],
        status: str = "submitted",
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_publish_candidates'
        candidate_id = str(candidate.get("candidate_id") or uuid4().hex)
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    candidate_id,
                    principal_id,
                    app_id,
                    repo_id,
                    base_sha,
                    status,
                    candidate_json,
                    review_json,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8::jsonb, $9::jsonb, now(), now()
                )
                RETURNING *
                """,
                owner_id,
                candidate_id,
                principal_id,
                app_id,
                repo_id,
                base_sha,
                status,
                json.dumps({**candidate, "candidate_id": candidate_id}),
                json.dumps(dict(candidate.get("review") or {})),
            )
        if row is None:
            raise RuntimeError("Create publish candidate returned no row")
        return self._publish_candidate_from_row(dict(row))

    async def get_publish_candidate(
        self,
        owner_id: str,
        candidate_id: str,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_publish_candidates'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND candidate_id = $2
                 LIMIT 1
                """,
                owner_id,
                candidate_id,
            )
        return self._publish_candidate_from_row(dict(row)) if row is not None else None

    async def list_publish_candidates(
        self,
        owner_id: str,
        *,
        app_id: str | None = None,
        limit: int = 50,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_publish_candidates'
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
        """
        params: list[Any] = [owner_id]
        if app_id:
            query += " AND app_id = $2"
            params.append(app_id)
            query += " ORDER BY created_at DESC LIMIT $3"
            params.append(limit)
        else:
            query += " ORDER BY created_at DESC LIMIT $2"
            params.append(limit)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._publish_candidate_from_row(dict(row)) for row in rows]

    async def update_publish_candidate_review(
        self,
        owner_id: str,
        *,
        candidate_id: str,
        status: str,
        review: dict[str, Any],
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_publish_candidates'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {table}
                   SET status = $3,
                       review_json = $4::jsonb,
                       updated_at = now()
                 WHERE owner_id = $1
                   AND candidate_id = $2
                RETURNING *
                """,
                owner_id,
                candidate_id,
                status,
                json.dumps(review),
            )
        return self._publish_candidate_from_row(dict(row)) if row is not None else None

    async def upsert_secret_ref(
        self,
        owner_id: str,
        *,
        secret_ref_id: str,
        purpose: str,
        secret_value: str | None = None,
        connector_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        active: bool = True,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_secret_refs'
        metadata_payload = dict(metadata or {})
        async with self._pool.acquire() as conn:
            if secret_value is None:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {table} (
                        owner_id,
                        secret_ref_id,
                        connector_id,
                        purpose_value,
                        metadata_json,
                        active,
                        created_at,
                        updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5::jsonb, $6, now(), now()
                    )
                    ON CONFLICT (owner_id, secret_ref_id) DO UPDATE SET
                        connector_id = EXCLUDED.connector_id,
                        purpose_value = EXCLUDED.purpose_value,
                        metadata_json = EXCLUDED.metadata_json,
                        active = EXCLUDED.active,
                        updated_at = now()
                    RETURNING *
                    """,
                    owner_id,
                    secret_ref_id,
                    connector_id,
                    self._encrypt_text(purpose),
                    json.dumps(metadata_payload),
                    active,
                )
            else:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {table} (
                        owner_id,
                        secret_ref_id,
                        connector_id,
                        purpose_value,
                        secret_value,
                        metadata_json,
                        active,
                        created_at,
                        updated_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6::jsonb, $7, now(), now()
                    )
                    ON CONFLICT (owner_id, secret_ref_id) DO UPDATE SET
                        connector_id = EXCLUDED.connector_id,
                        purpose_value = EXCLUDED.purpose_value,
                        secret_value = EXCLUDED.secret_value,
                        metadata_json = EXCLUDED.metadata_json,
                        active = EXCLUDED.active,
                        updated_at = now()
                    RETURNING *
                    """,
                    owner_id,
                    secret_ref_id,
                    connector_id,
                    self._encrypt_text(purpose),
                    self._encrypt_text(secret_value),
                    json.dumps(metadata_payload),
                    active,
                )
        if row is None:
            raise RuntimeError("Upsert secret ref returned no row")
        return self._secret_ref_from_row(dict(row))

    async def record_agent_audit_event(
        self,
        owner_id: str,
        *,
        principal_id: str | None,
        app_id: str | None,
        service_kind: str | None,
        resource: str | None,
        action: str,
        decision: str,
        audit: dict[str, Any],
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_audit_events'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    audit_id,
                    principal_id,
                    app_id,
                    service_kind,
                    resource_value,
                    action_value,
                    decision_value,
                    audit_json,
                    created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, now()
                )
                RETURNING *
                """,
                owner_id,
                uuid4().hex,
                principal_id,
                app_id,
                service_kind,
                self._encrypt_text(resource) if resource else None,
                self._encrypt_text(action),
                decision,
                json.dumps(audit),
            )
        if row is None:
            raise RuntimeError("Record agent audit event returned no row")
        return self._agent_audit_event_from_row(dict(row))

    async def list_agent_audit_events(
        self,
        owner_id: str,
        *,
        principal_id: str | None = None,
        app_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_agent_audit_events'
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
        """
        params: list[Any] = [owner_id]
        placeholder = 2
        if principal_id:
            query += f" AND principal_id = ${placeholder}"
            params.append(principal_id)
            placeholder += 1
        if app_id:
            query += f" AND app_id = ${placeholder}"
            params.append(app_id)
            placeholder += 1
        query += f" ORDER BY created_at DESC LIMIT ${placeholder}"
        params.append(limit)
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._agent_audit_event_from_row(dict(row)) for row in rows]

    async def create_agent_session(
        self,
        owner_id: str,
        *,
        principal_id: str,
        app_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "active",
        session_id: str | None = None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_sessions'
        next_session_id = session_id or uuid4().hex
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    session_id,
                    principal_id,
                    app_id,
                    session_status,
                    metadata_json,
                    created_at,
                    updated_at,
                    last_activity_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6::jsonb, now(), now(), now()
                )
                ON CONFLICT (owner_id, session_id) DO UPDATE SET
                    principal_id = EXCLUDED.principal_id,
                    app_id = EXCLUDED.app_id,
                    session_status = EXCLUDED.session_status,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now(),
                    last_activity_at = now()
                RETURNING *
                """,
                owner_id,
                next_session_id,
                principal_id,
                app_id,
                status,
                json.dumps(dict(metadata or {})),
            )
        if row is None:
            raise RuntimeError("Create agent session returned no row")
        return self._agent_session_from_row(dict(row))

    async def get_agent_session(
        self,
        owner_id: str,
        session_id: str,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_agent_sessions'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND session_id = $2
                 LIMIT 1
                """,
                owner_id,
                session_id,
            )
        return self._agent_session_from_row(dict(row)) if row is not None else None

    async def touch_agent_session(self, owner_id: str, session_id: str) -> None:
        table = f'"{self._schema}".owner_ci_agent_sessions'
        async with self._pool.acquire() as conn:
            await conn.execute(
                f"""
                UPDATE {table}
                   SET updated_at = now(),
                       last_activity_at = now()
                 WHERE owner_id = $1
                   AND session_id = $2
                """,
                owner_id,
                session_id,
            )

    async def create_agent_interaction(
        self,
        owner_id: str,
        *,
        session_id: str | None,
        principal_id: str | None,
        app_id: str | None,
        repo_id: str | None,
        route_path: str | None,
        intent: str,
        request_text: str | None,
        request_payload: dict[str, Any] | None,
        normalized_intent: dict[str, Any] | None = None,
        related_run_id: str | None = None,
        related_candidate_id: str | None = None,
        related_service_request_id: str | None = None,
        audit_id: str | None = None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_interactions'
        interaction_id = uuid4().hex
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    interaction_id,
                    session_id,
                    principal_id,
                    app_id,
                    repo_id,
                    route_path_value,
                    intent_value,
                    request_text_value,
                    request_json,
                    normalized_intent_json,
                    related_run_id,
                    related_candidate_id,
                    related_service_request_id,
                    audit_id,
                    created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9,
                    $10::jsonb, $11::jsonb, $12, $13, $14, $15, now()
                )
                RETURNING *
                """,
                owner_id,
                interaction_id,
                session_id,
                principal_id,
                app_id,
                repo_id,
                self._encrypt_text(route_path) if route_path else None,
                self._encrypt_text(intent),
                self._encrypt_text(request_text) if request_text else None,
                json.dumps(dict(request_payload or {})),
                json.dumps(dict(normalized_intent or {})),
                related_run_id,
                related_candidate_id,
                related_service_request_id,
                audit_id,
            )
        if row is None:
            raise RuntimeError("Create agent interaction returned no row")
        if session_id:
            await self.touch_agent_session(owner_id, session_id)
        return self._agent_interaction_from_row(dict(row))

    async def list_agent_session_interactions(
        self,
        owner_id: str,
        session_id: str,
        *,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_agent_interactions'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND session_id = $2
                 ORDER BY created_at DESC
                 LIMIT $3
                """,
                owner_id,
                session_id,
                max(1, limit),
            )
        return [self._agent_interaction_from_row(dict(row)) for row in rows]

    async def create_agent_action(
        self,
        owner_id: str,
        *,
        interaction_id: str,
        principal_id: str | None,
        app_id: str | None,
        action: str,
        status: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_actions'
        action_record_id = uuid4().hex
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    action_record_id,
                    interaction_id,
                    principal_id,
                    app_id,
                    action_value,
                    status,
                    payload_json,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8::jsonb, now(), now()
                )
                RETURNING *
                """,
                owner_id,
                action_record_id,
                interaction_id,
                principal_id,
                app_id,
                self._encrypt_text(action),
                status,
                json.dumps(dict(payload or {})),
            )
        if row is None:
            raise RuntimeError("Create agent action returned no row")
        return self._agent_action_from_row(dict(row))

    async def create_agent_outcome(
        self,
        owner_id: str,
        *,
        interaction_id: str,
        action_record_id: str | None,
        status: str,
        summary: str,
        payload: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_outcomes'
        outcome_id = uuid4().hex
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    outcome_id,
                    interaction_id,
                    action_record_id,
                    status,
                    summary_value,
                    payload_json,
                    created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7::jsonb, now()
                )
                RETURNING *
                """,
                owner_id,
                outcome_id,
                interaction_id,
                action_record_id,
                status,
                self._encrypt_text(summary),
                json.dumps(dict(payload or {})),
            )
        if row is None:
            raise RuntimeError("Create agent outcome returned no row")
        return self._agent_outcome_from_row(dict(row))

    async def record_agent_gap_event(
        self,
        owner_id: str,
        *,
        dedupe_key: str,
        session_id: str | None,
        principal_id: str | None,
        app_id: str | None,
        repo_id: str | None,
        run_id: str | None,
        gap_type: str,
        severity: str,
        blocker: bool,
        detected_from: str,
        required_capability: str | None,
        observed_request: dict[str, Any] | None,
        suggested_fix: str | None,
        metadata: dict[str, Any] | None = None,
        status: str = "open",
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_gap_events'
        gap_id = uuid4().hex
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    gap_id,
                    dedupe_key,
                    session_id,
                    principal_id,
                    app_id,
                    repo_id,
                    run_id,
                    gap_type,
                    severity,
                    blocker,
                    detected_from,
                    required_capability,
                    observed_request_json,
                    suggested_fix_value,
                    status,
                    metadata_json,
                    first_seen_at,
                    last_seen_at,
                    occurrence_count,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13,
                    $14::jsonb, $15, $16, $17::jsonb, now(), now(), 1, now()
                )
                ON CONFLICT (owner_id, dedupe_key) DO UPDATE SET
                    session_id = COALESCE(EXCLUDED.session_id, {table}.session_id),
                    principal_id = COALESCE(EXCLUDED.principal_id, {table}.principal_id),
                    app_id = COALESCE(EXCLUDED.app_id, {table}.app_id),
                    repo_id = COALESCE(EXCLUDED.repo_id, {table}.repo_id),
                    run_id = COALESCE(EXCLUDED.run_id, {table}.run_id),
                    severity = EXCLUDED.severity,
                    blocker = EXCLUDED.blocker,
                    detected_from = EXCLUDED.detected_from,
                    required_capability = EXCLUDED.required_capability,
                    observed_request_json = EXCLUDED.observed_request_json,
                    suggested_fix_value = EXCLUDED.suggested_fix_value,
                    metadata_json = EXCLUDED.metadata_json,
                    last_seen_at = now(),
                    occurrence_count = {table}.occurrence_count + 1,
                    updated_at = now()
                RETURNING *
                """,
                owner_id,
                gap_id,
                dedupe_key,
                session_id,
                principal_id,
                app_id,
                repo_id,
                run_id,
                gap_type,
                severity,
                blocker,
                detected_from,
                required_capability,
                json.dumps(dict(observed_request or {})),
                self._encrypt_text(suggested_fix) if suggested_fix else None,
                status,
                json.dumps(dict(metadata or {})),
            )
        if row is None:
            raise RuntimeError("Record agent gap event returned no row")
        return self._agent_gap_event_from_row(dict(row))

    async def list_agent_gap_events(
        self,
        owner_id: str,
        *,
        session_id: str | None = None,
        principal_id: str | None = None,
        app_id: str | None = None,
        repo_id: str | None = None,
        status: str | None = None,
        blocker_only: bool = False,
        unresolved_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_agent_gap_events'
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
        """
        params: list[Any] = [owner_id]
        if session_id:
            params.append(session_id)
            query += f" AND session_id = ${len(params)}"
        if principal_id:
            params.append(principal_id)
            query += f" AND principal_id = ${len(params)}"
        if app_id:
            params.append(app_id)
            query += f" AND app_id = ${len(params)}"
        if repo_id:
            params.append(repo_id)
            query += f" AND repo_id = ${len(params)}"
        if status:
            params.append(status)
            query += f" AND status = ${len(params)}"
        elif unresolved_only:
            query += " AND status <> 'resolved'"
        if blocker_only:
            query += " AND blocker = TRUE"
        params.append(max(1, limit))
        query += f" ORDER BY blocker DESC, last_seen_at DESC LIMIT ${len(params)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._agent_gap_event_from_row(dict(row)) for row in rows]

    async def get_agent_gap_event(
        self,
        owner_id: str,
        gap_id: str,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_agent_gap_events'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND gap_id = $2
                 LIMIT 1
                """,
                owner_id,
                gap_id,
            )
        return self._agent_gap_event_from_row(dict(row)) if row is not None else None

    async def update_agent_gap_event(
        self,
        owner_id: str,
        *,
        gap_id: str,
        status: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_agent_gap_events'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {table}
                   SET status = $3,
                       metadata_json = $4::jsonb,
                       updated_at = now()
                 WHERE owner_id = $1
                   AND gap_id = $2
                RETURNING *
                """,
                owner_id,
                gap_id,
                status,
                json.dumps(dict(metadata or {})),
            )
        return self._agent_gap_event_from_row(dict(row)) if row is not None else None

    async def create_agent_service_request(
        self,
        owner_id: str,
        *,
        principal_id: str | None,
        app_id: str,
        service_kind: str,
        action_id: str,
        target_ref: str | None,
        tenant_id: str | None,
        change_reason: str | None,
        request_payload: dict[str, Any],
        status: str,
        approved: bool,
        result: dict[str, Any] | None = None,
        audit_id: str | None = None,
        executed: bool = False,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_agent_service_requests'
        request_id = uuid4().hex
        executed_at = datetime.now(UTC) if executed else None
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    request_id,
                    principal_id,
                    app_id,
                    service_kind,
                    action_id,
                    target_ref,
                    tenant_id,
                    change_reason_value,
                    request_json,
                    status,
                    approved,
                    result_json,
                    audit_id,
                    created_at,
                    updated_at,
                    executed_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb,
                    $11, $12, $13::jsonb, $14, now(), now(), $15
                )
                RETURNING *
                """,
                owner_id,
                request_id,
                principal_id,
                app_id,
                service_kind,
                action_id,
                target_ref,
                tenant_id,
                self._encrypt_text(change_reason) if change_reason else None,
                json.dumps(dict(request_payload)),
                status,
                approved,
                json.dumps(dict(result or {})),
                audit_id,
                executed_at,
            )
        if row is None:
            raise RuntimeError("Create agent service request returned no row")
        return self._agent_service_request_from_row(dict(row))

    async def list_secret_refs(
        self,
        owner_id: str,
        *,
        active_only: bool = False,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_secret_refs'
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
        """
        params: list[Any] = [owner_id]
        if active_only:
            query += " AND active = TRUE"
        query += " ORDER BY updated_at DESC, secret_ref_id ASC"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._secret_ref_from_row(dict(row)) for row in rows]

    async def upsert_service_adapter_capability(
        self,
        owner_id: str,
        *,
        service_kind: str,
        manifest: dict[str, Any],
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_service_adapter_capabilities'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    service_kind,
                    manifest_json,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3::jsonb, now(), now()
                )
                ON CONFLICT (owner_id, service_kind) DO UPDATE SET
                    manifest_json = EXCLUDED.manifest_json,
                    updated_at = now()
                RETURNING *
                """,
                owner_id,
                service_kind,
                json.dumps(dict(manifest or {})),
            )
        if row is None:
            raise RuntimeError("Upsert service adapter capability returned no row")
        return self._service_adapter_capability_from_row(dict(row))

    async def get_service_adapter_capability(
        self,
        owner_id: str,
        service_kind: str,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_service_adapter_capabilities'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND service_kind = $2
                 LIMIT 1
                """,
                owner_id,
                service_kind,
            )
        return self._service_adapter_capability_from_row(dict(row)) if row is not None else None

    async def list_service_adapter_capabilities(self, owner_id: str) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_service_adapter_capabilities'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                 ORDER BY service_kind ASC
                """,
                owner_id,
            )
        return [self._service_adapter_capability_from_row(dict(row)) for row in rows]

    async def create_managed_operation(
        self,
        owner_id: str,
        *,
        app_id: str,
        repo_id: str,
        operation_kind: str,
        lifecycle_stage: str,
        status: str,
        summary: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
        correlation_key: str | None = None,
        operation_id: str | None = None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_managed_operations'
        next_operation_id = operation_id or uuid4().hex
        async with self._pool.acquire() as conn:
            if correlation_key:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {table} (
                        owner_id,
                        operation_id,
                        app_id,
                        repo_id,
                        operation_kind,
                        lifecycle_stage,
                        status,
                        correlation_key,
                        summary_json,
                        metadata_json,
                        created_at,
                        updated_at,
                        last_observed_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10::jsonb, now(), now(), now()
                    )
                    ON CONFLICT (owner_id, correlation_key) DO UPDATE SET
                        app_id = EXCLUDED.app_id,
                        repo_id = EXCLUDED.repo_id,
                        operation_kind = EXCLUDED.operation_kind,
                        lifecycle_stage = EXCLUDED.lifecycle_stage,
                        status = EXCLUDED.status,
                        summary_json = EXCLUDED.summary_json,
                        metadata_json = EXCLUDED.metadata_json,
                        updated_at = now(),
                        last_observed_at = now()
                    RETURNING *
                    """,
                    owner_id,
                    next_operation_id,
                    app_id,
                    repo_id,
                    operation_kind,
                    lifecycle_stage,
                    status,
                    correlation_key,
                    json.dumps(dict(summary or {})),
                    json.dumps(dict(metadata or {})),
                )
            else:
                row = await conn.fetchrow(
                    f"""
                    INSERT INTO {table} (
                        owner_id,
                        operation_id,
                        app_id,
                        repo_id,
                        operation_kind,
                        lifecycle_stage,
                        status,
                        correlation_key,
                        summary_json,
                        metadata_json,
                        created_at,
                        updated_at,
                        last_observed_at
                    ) VALUES (
                        $1, $2, $3, $4, $5, $6, $7, NULL, $8::jsonb, $9::jsonb, now(), now(), now()
                    )
                    RETURNING *
                    """,
                    owner_id,
                    next_operation_id,
                    app_id,
                    repo_id,
                    operation_kind,
                    lifecycle_stage,
                    status,
                    json.dumps(dict(summary or {})),
                    json.dumps(dict(metadata or {})),
                )
        if row is None:
            raise RuntimeError("Create managed operation returned no row")
        return self._managed_operation_from_row(dict(row))

    async def update_managed_operation(
        self,
        owner_id: str,
        *,
        operation_id: str,
        lifecycle_stage: str | None = None,
        status: str | None = None,
        summary: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_managed_operations'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE {table}
                   SET lifecycle_stage = COALESCE($3, lifecycle_stage),
                       status = COALESCE($4, status),
                       summary_json = CASE
                           WHEN $5::jsonb IS NULL THEN summary_json
                           ELSE $5::jsonb
                       END,
                       metadata_json = CASE
                           WHEN $6::jsonb IS NULL THEN metadata_json
                           ELSE $6::jsonb
                       END,
                       updated_at = now(),
                       last_observed_at = now()
                 WHERE owner_id = $1
                   AND operation_id = $2
                RETURNING *
                """,
                owner_id,
                operation_id,
                lifecycle_stage,
                status,
                json.dumps(dict(summary or {})) if summary is not None else None,
                json.dumps(dict(metadata or {})) if metadata is not None else None,
            )
        return self._managed_operation_from_row(dict(row)) if row is not None else None

    async def get_managed_operation(
        self,
        owner_id: str,
        operation_id: str,
    ) -> dict[str, Any] | None:
        table = f'"{self._schema}".owner_ci_managed_operations'
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND operation_id = $2
                 LIMIT 1
                """,
                owner_id,
                operation_id,
            )
        return self._managed_operation_from_row(dict(row)) if row is not None else None

    async def list_managed_operations(
        self,
        owner_id: str,
        *,
        app_id: str | None = None,
        repo_id: str | None = None,
        service_kind: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_managed_operations'
        query = f"""
            SELECT DISTINCT op.*
              FROM {table} op
         LEFT JOIN "{self._schema}".owner_ci_operation_refs ref
                ON ref.owner_id = op.owner_id
               AND ref.operation_id = op.operation_id
             WHERE op.owner_id = $1
        """
        params: list[Any] = [owner_id]
        if app_id:
            params.append(app_id)
            query += f" AND op.app_id = ${len(params)}"
        if repo_id:
            params.append(repo_id)
            query += f" AND op.repo_id = ${len(params)}"
        if service_kind:
            params.append(service_kind)
            query += f" AND ref.service_kind = ${len(params)}"
        if status:
            params.append(status)
            query += f" AND op.status = ${len(params)}"
        params.append(max(1, limit))
        query += f" ORDER BY op.updated_at DESC LIMIT ${len(params)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._managed_operation_from_row(dict(row)) for row in rows]

    async def find_managed_operation_by_ref(
        self,
        owner_id: str,
        *,
        ref_kind: str,
        ref_value: str,
        app_id: str | None = None,
    ) -> dict[str, Any] | None:
        refs_table = f'"{self._schema}".owner_ci_operation_refs'
        ops_table = f'"{self._schema}".owner_ci_managed_operations'
        query = f"""
            SELECT op.*
              FROM {refs_table} ref
              JOIN {ops_table} op
                ON op.owner_id = ref.owner_id
               AND op.operation_id = ref.operation_id
             WHERE ref.owner_id = $1
               AND ref.ref_kind = $2
               AND ref.ref_value = $3
        """
        params: list[Any] = [owner_id, ref_kind, ref_value]
        if app_id:
            params.append(app_id)
            query += f" AND op.app_id = ${len(params)}"
        query += " ORDER BY ref.updated_at DESC LIMIT 1"
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(query, *params)
        return self._managed_operation_from_row(dict(row)) if row is not None else None

    async def upsert_operation_ref(
        self,
        owner_id: str,
        *,
        operation_id: str,
        service_kind: str | None,
        ref_kind: str,
        ref_value: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_operation_refs'
        dedupe_key = secrets.token_hex(8)
        stable = f"{service_kind or ''}:{ref_kind}:{ref_value}"
        dedupe_key = re.sub(r"\s+", "", stable.lower())
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    ref_id,
                    operation_id,
                    service_kind,
                    ref_kind,
                    ref_value,
                    dedupe_key,
                    metadata_json,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8::jsonb, now(), now()
                )
                ON CONFLICT (owner_id, operation_id, dedupe_key) DO UPDATE SET
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now()
                RETURNING *
                """,
                owner_id,
                uuid4().hex,
                operation_id,
                service_kind,
                ref_kind,
                ref_value,
                dedupe_key,
                json.dumps(dict(metadata or {})),
            )
        if row is None:
            raise RuntimeError("Upsert operation ref returned no row")
        return self._operation_ref_from_row(dict(row))

    async def list_operation_refs(
        self,
        owner_id: str,
        operation_id: str,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_operation_refs'
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {table}
                 WHERE owner_id = $1
                   AND operation_id = $2
                 ORDER BY updated_at DESC
                """,
                owner_id,
                operation_id,
            )
        return [self._operation_ref_from_row(dict(row)) for row in rows]

    async def record_operation_evidence(
        self,
        owner_id: str,
        *,
        operation_id: str,
        service_kind: str,
        evidence_type: str,
        title: str,
        payload: dict[str, Any] | None = None,
        log_text: str | None = None,
        metadata: dict[str, Any] | None = None,
        state: str = "ready",
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_operation_evidence'
        stable_key = dedupe_key or re.sub(
            r"\s+",
            "",
            f"{service_kind}:{evidence_type}:{title}".lower(),
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    evidence_id,
                    operation_id,
                    service_kind,
                    evidence_type,
                    title_value,
                    state,
                    dedupe_key,
                    payload_json,
                    log_text_value,
                    metadata_json,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, $10, $11::jsonb, now(), now()
                )
                ON CONFLICT (owner_id, operation_id, dedupe_key) DO UPDATE SET
                    state = EXCLUDED.state,
                    payload_json = EXCLUDED.payload_json,
                    log_text_value = EXCLUDED.log_text_value,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now()
                RETURNING *
                """,
                owner_id,
                uuid4().hex,
                operation_id,
                service_kind,
                evidence_type,
                self._encrypt_text(title.strip() or evidence_type),
                state,
                stable_key,
                json.dumps(dict(payload or {})),
                self._encrypt_text(log_text) if log_text else None,
                json.dumps(dict(metadata or {})),
            )
        if row is None:
            raise RuntimeError("Record operation evidence returned no row")
        return self._operation_evidence_from_row(dict(row))

    async def list_operation_evidence(
        self,
        owner_id: str,
        operation_id: str,
        *,
        service_kind: str | None = None,
        evidence_type: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_operation_evidence'
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
               AND operation_id = $2
        """
        params: list[Any] = [owner_id, operation_id]
        if service_kind:
            params.append(service_kind)
            query += f" AND service_kind = ${len(params)}"
        if evidence_type:
            params.append(evidence_type)
            query += f" AND evidence_type = ${len(params)}"
        params.append(max(1, limit))
        query += f" ORDER BY updated_at DESC LIMIT ${len(params)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._operation_evidence_from_row(dict(row)) for row in rows]

    async def get_operation_log_chunks(
        self,
        owner_id: str,
        operation_id: str,
        *,
        query_text: str | None = None,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        evidence = await self.list_operation_evidence(
            owner_id,
            operation_id,
            evidence_type="logs",
            limit=limit,
        )
        logs: list[dict[str, Any]] = []
        needle = str(query_text or "").strip().lower()
        for entry in evidence:
            message = str(entry.get("log_text") or "").strip()
            if not message:
                continue
            if needle and needle not in message.lower():
                continue
            logs.append(
                {
                    "chunk_id": str(entry.get("evidence_id") or uuid4().hex),
                    "stream": str(
                        (entry.get("payload") or {}).get("stream")
                        or entry.get("service_kind")
                        or "system"
                    ),
                    "message": message,
                    "created_at": entry.get("updated_at"),
                    "metadata": dict(entry.get("metadata") or {}),
                }
            )
            if len(logs) >= limit:
                break
        return logs

    async def record_operation_incident(
        self,
        owner_id: str,
        *,
        operation_id: str,
        service_kind: str,
        incident_type: str,
        severity: str,
        blocking: bool,
        root_cause_summary: str,
        recommended_fix: str | None = None,
        evidence_refs: list[str] | None = None,
        metadata: dict[str, Any] | None = None,
        status: str = "open",
        dedupe_key: str | None = None,
    ) -> dict[str, Any]:
        table = f'"{self._schema}".owner_ci_operation_incidents'
        stable_key = dedupe_key or re.sub(
            r"\s+",
            "",
            f"{service_kind}:{incident_type}:{root_cause_summary}".lower(),
        )
        async with self._pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO {table} (
                    owner_id,
                    incident_id,
                    operation_id,
                    service_kind,
                    incident_type,
                    severity,
                    blocking,
                    dedupe_key,
                    status,
                    root_cause_summary_value,
                    recommended_fix_value,
                    evidence_refs_json,
                    metadata_json,
                    created_at,
                    updated_at,
                    last_seen_at,
                    occurrence_count
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11,
                    $12::jsonb, $13::jsonb, now(), now(), now(), 1
                )
                ON CONFLICT (owner_id, operation_id, dedupe_key) DO UPDATE SET
                    severity = EXCLUDED.severity,
                    blocking = EXCLUDED.blocking,
                    status = EXCLUDED.status,
                    root_cause_summary_value = EXCLUDED.root_cause_summary_value,
                    recommended_fix_value = EXCLUDED.recommended_fix_value,
                    evidence_refs_json = EXCLUDED.evidence_refs_json,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = now(),
                    last_seen_at = now(),
                    occurrence_count = {table}.occurrence_count + 1
                RETURNING *
                """,
                owner_id,
                uuid4().hex,
                operation_id,
                service_kind,
                incident_type,
                severity,
                blocking,
                stable_key,
                status,
                self._encrypt_text(root_cause_summary),
                self._encrypt_text(recommended_fix) if recommended_fix else None,
                json.dumps(list(evidence_refs or [])),
                json.dumps(dict(metadata or {})),
            )
        if row is None:
            raise RuntimeError("Record operation incident returned no row")
        return self._operation_incident_from_row(dict(row))

    async def list_operation_incidents(
        self,
        owner_id: str,
        operation_id: str,
        *,
        unresolved_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_operation_incidents'
        query = f"""
            SELECT *
              FROM {table}
             WHERE owner_id = $1
               AND operation_id = $2
        """
        params: list[Any] = [owner_id, operation_id]
        if unresolved_only:
            query += " AND status <> 'resolved'"
        params.append(max(1, limit))
        query += f" ORDER BY blocking DESC, last_seen_at DESC LIMIT ${len(params)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._operation_incident_from_row(dict(row)) for row in rows]

    async def list_operation_incidents_for_owner(
        self,
        owner_id: str,
        *,
        repo_id: str | None = None,
        unresolved_only: bool = False,
        limit: int = 200,
    ) -> list[dict[str, Any]]:
        table = f'"{self._schema}".owner_ci_operation_incidents'
        ops_table = f'"{self._schema}".owner_ci_managed_operations'
        query = f"""
            SELECT inc.*
              FROM {table} inc
              JOIN {ops_table} op
                ON op.owner_id = inc.owner_id
               AND op.operation_id = inc.operation_id
             WHERE inc.owner_id = $1
        """
        params: list[Any] = [owner_id]
        if repo_id:
            params.append(repo_id)
            query += f" AND op.repo_id = ${len(params)}"
        if unresolved_only:
            query += " AND inc.status <> 'resolved'"
        params.append(max(1, limit))
        query += f" ORDER BY inc.blocking DESC, inc.last_seen_at DESC LIMIT ${len(params)}"
        async with self._pool.acquire() as conn:
            rows = await conn.fetch(query, *params)
        return [self._operation_incident_from_row(dict(row)) for row in rows]

    async def get_operation_hydrated(
        self,
        owner_id: str,
        operation_id: str,
    ) -> dict[str, Any] | None:
        operation = await self.get_managed_operation(owner_id, operation_id)
        if operation is None:
            return None
        refs = await self.list_operation_refs(owner_id, operation_id)
        evidence = await self.list_operation_evidence(owner_id, operation_id, limit=200)
        incidents = await self.list_operation_incidents(
            owner_id,
            operation_id,
            unresolved_only=False,
            limit=200,
        )
        return {
            **operation,
            "refs": refs,
            "evidence": evidence,
            "incidents": incidents,
            "top_incident": incidents[0] if incidents else None,
        }

    async def _store_worker_observability(
        self,
        *,
        conn: Any,
        owner_id: str,
        repo_id: str,
        run_id: str,
        shard_id: str,
        node_id: str,
        final_status: str,
        result_json: dict[str, Any],
        error_json: dict[str, Any],
    ) -> None:
        events_table = f'"{self._schema}".owner_ci_events'
        logs_table = f'"{self._schema}".owner_ci_log_chunks'
        samples_table = f'"{self._schema}".owner_ci_resource_samples'
        bundles_table = f'"{self._schema}".owner_ci_debug_bundles'
        summaries_table = f'"{self._schema}".owner_ci_project_usage_summaries'

        await conn.execute(
            f"""
            INSERT INTO {events_table} (
                owner_id,
                event_id,
                repo_id,
                run_id,
                shard_id,
                node_id,
                event_type,
                level_value,
                payload_json,
                created_at
            ) VALUES (
                $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, now()
            )
            """,
            owner_id,
            uuid4().hex,
            repo_id,
            run_id,
            shard_id,
            node_id,
            "worker.result.accepted",
            "error" if final_status == "failed" else "info",
            json.dumps(
                {
                    "status": final_status,
                    "error_code": error_json.get("code"),
                    "cleanup_receipt": result_json.get("cleanup_receipt"),
                }
            ),
        )

        for raw_event in list(result_json.get("events") or []):
            if not isinstance(raw_event, dict):
                continue
            await conn.execute(
                f"""
                INSERT INTO {events_table} (
                    owner_id,
                    event_id,
                    repo_id,
                    run_id,
                    shard_id,
                    node_id,
                    event_type,
                    level_value,
                    payload_json,
                    created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, now()
                )
                """,
                owner_id,
                uuid4().hex,
                repo_id,
                run_id,
                shard_id,
                node_id,
                str(raw_event.get("event_type") or raw_event.get("type") or "worker.event"),
                str(raw_event.get("level") or "info"),
                json.dumps(dict(raw_event.get("payload") or raw_event)),
            )

        log_chunks = list(result_json.get("log_chunks") or [])
        if not log_chunks:
            for stream in ("stdout", "stderr"):
                message = str(result_json.get(stream) or "").strip()
                if message:
                    log_chunks.append({"stream": stream, "message": message})
        for raw_chunk in log_chunks:
            if not isinstance(raw_chunk, dict):
                continue
            message = str(raw_chunk.get("message") or "").strip()
            if not message:
                continue
            await conn.execute(
                f"""
                INSERT INTO {logs_table} (
                    owner_id,
                    chunk_id,
                    repo_id,
                    run_id,
                    shard_id,
                    node_id,
                    stream_name,
                    message_value,
                    metadata_json,
                    created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7, $8, $9::jsonb, now()
                )
                """,
                owner_id,
                uuid4().hex,
                repo_id,
                run_id,
                shard_id,
                node_id,
                str(raw_chunk.get("stream") or "system"),
                self._encrypt_text(message) or "",
                json.dumps(dict(raw_chunk.get("metadata") or {})),
            )

        resource_samples = list(result_json.get("resource_samples") or [])
        for raw_sample in resource_samples:
            if not isinstance(raw_sample, dict):
                continue
            await conn.execute(
                f"""
                INSERT INTO {samples_table} (
                    owner_id,
                    sample_id,
                    repo_id,
                    run_id,
                    shard_id,
                    node_id,
                    sample_json,
                    created_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6, $7::jsonb, now()
                )
                """,
                owner_id,
                uuid4().hex,
                repo_id,
                run_id,
                shard_id,
                node_id,
                json.dumps(raw_sample),
            )

        debug_bundle = dict(result_json.get("debug_bundle") or {})
        if result_json.get("cleanup_receipt") is not None:
            debug_bundle.setdefault("cleanup_receipt", result_json.get("cleanup_receipt"))
        if result_json.get("container_receipts") is not None:
            debug_bundle.setdefault("container_receipts", result_json.get("container_receipts"))
        if debug_bundle:
            await conn.execute(
                f"""
                INSERT INTO {bundles_table} (
                    owner_id,
                    bundle_id,
                    repo_id,
                    run_id,
                    shard_id,
                    bundle_json,
                    created_at,
                    updated_at
                ) VALUES (
                    $1, $2, $3, $4, $5, $6::jsonb, now(), now()
                )
                ON CONFLICT (owner_id, bundle_id) DO UPDATE SET
                    bundle_json = EXCLUDED.bundle_json,
                    updated_at = now()
                """,
                owner_id,
                f"{run_id}:{shard_id}",
                repo_id,
                run_id,
                shard_id,
                json.dumps(debug_bundle),
            )

        peak_memory_mb = 0.0
        disk_used_bytes = 0
        disk_free_bytes = 0
        container_count = 0
        for sample in resource_samples:
            peak_memory_mb = max(peak_memory_mb, float(sample.get("memory_mb") or 0.0))
            disk_used_bytes = max(disk_used_bytes, int(sample.get("disk_used_bytes") or 0))
            disk_free_bytes = max(disk_free_bytes, int(sample.get("disk_free_bytes") or 0))
            container_count = max(container_count, int(sample.get("container_count") or 0))
        elapsed_ms = int(result_json.get("elapsed_ms") or 0)
        summary_payload = {
            "run_id": run_id,
            "shard_id": shard_id,
            "status": final_status,
            "updated_at": datetime.now(UTC).isoformat(),
            "compute_minutes": round(max(0, elapsed_ms) / 60000.0, 4),
            "peak_memory_mb": peak_memory_mb,
            "disk_used_bytes": disk_used_bytes,
            "disk_free_bytes": disk_free_bytes,
            "container_count": container_count,
            "cleanup_status": (
                dict(result_json.get("cleanup_receipt") or {}).get("status")
                if isinstance(result_json.get("cleanup_receipt"), dict)
                else None
            ),
        }
        await conn.execute(
            f"""
            INSERT INTO {summaries_table} (
                owner_id,
                repo_id,
                summary_key,
                summary_json,
                created_at,
                updated_at
            ) VALUES (
                $1, $2, $3, $4::jsonb, now(), now()
            )
            ON CONFLICT (owner_id, repo_id, summary_key) DO UPDATE SET
                summary_json = EXCLUDED.summary_json,
                updated_at = now()
            """,
            owner_id,
            repo_id,
            run_id,
            json.dumps(summary_payload),
        )

    async def _recompute_run_status(self, owner_id: str, run_id: str) -> None:
        runs_table = f'"{self._schema}".owner_ci_runs'
        shards_table = f'"{self._schema}".owner_ci_shards'
        async with self._pool.acquire() as conn:
            run_row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM {runs_table}
                 WHERE owner_id = $1
                   AND run_id = $2
                 LIMIT 1
                """,
                owner_id,
                run_id,
            )
            if run_row is None:
                return
            shard_rows = await conn.fetch(
                f"""
                SELECT *
                  FROM {shards_table}
                 WHERE owner_id = $1
                   AND run_id = $2
                """,
                owner_id,
                run_id,
            )
            repo_row = await conn.fetchrow(
                f"""
                SELECT *
                  FROM "{self._schema}".owner_ci_repo_profiles
                 WHERE owner_id = $1
                   AND repo_id = $2
                 LIMIT 1
                """,
                owner_id,
                str(run_row["repo_id"]),
            )
            repo_profile = (
                self._repo_profile_from_row(dict(repo_row)) if repo_row is not None else None
            )
            shard_payloads = [
                self._shard_from_row(dict(row))
                for row in shard_rows
            ]
            local_receipt = None
            if repo_profile is not None:
                local_receipt, _ = _load_local_repo_readiness_from_shards(
                    repo_profile,
                    shard_payloads,
                )
            if repo_profile is not None:
                filesystem_receipt, _ = _load_local_repo_readiness(repo_profile)
                if local_receipt is None:
                    local_receipt = filesystem_receipt
            effective_shards = [
                normalize_shard_receipt(str(run_row["repo_id"]), shard_payload)
                for shard_payload in shard_payloads
            ]
            effective_shards = overlay_local_readiness_shards(
                effective_shards,
                local_receipt,
            )
            shard_statuses = [shard.status for shard in effective_shards]
            review_receipts = self._coerce_json_object(
                run_row["review_receipts"],
                "review_receipts",
            )
            next_status = str(run_row["status"]).strip().lower()
            if any(status == "failed" for status in shard_statuses):
                next_status = "failed"
            elif any(status == "running_disconnected" for status in shard_statuses):
                next_status = "awaiting_sync"
            elif any(status in _SHARD_PENDING_STATUSES for status in shard_statuses):
                next_status = (
                    "running"
                    if any(status == "running" for status in shard_statuses)
                    else "queued_local"
                )
            elif shard_statuses and all(
                status in {"succeeded", "skipped"} for status in shard_statuses
            ):
                merge_blocked = bool(review_receipts.get("merge_blocked", True))
                next_status = "ready_to_merge" if not merge_blocked else "review_pending"
            elif shard_statuses and all(status == "cancelled" for status in shard_statuses):
                next_status = "cancelled"
            if next_status not in _RUN_STATUSES:
                next_status = "planned"
            await conn.execute(
                f"""
                UPDATE {runs_table}
                   SET status = $3,
                       updated_at = now()
                 WHERE owner_id = $1
                   AND run_id = $2
                """,
                owner_id,
                run_id,
                next_status,
            )

    @staticmethod
    def hash_worker_token(token: str) -> str:
        import hashlib

        return hashlib.sha256(token.encode("utf-8")).hexdigest()


async def ensure_owner_ci_schema(
    pool: Any,
    *,
    schema: str = "owner_personal",
    encryptor: FieldEncryptor | None = None,
) -> OwnerCiStorage | None:
    """Ensure owner CI storage tables exist when a runtime DB pool is available."""

    if pool is None:
        log.info("owner_ci_schema_bootstrap_skipped", reason="missing_pool")
        return None
    storage = OwnerCiStorage(pool, schema=schema, encryptor=encryptor)
    await storage.ensure_schema()
    return storage
