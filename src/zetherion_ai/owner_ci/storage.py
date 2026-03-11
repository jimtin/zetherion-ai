"""Owner-scoped CI controller persistence."""

from __future__ import annotations

import json
import re
import secrets
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from zetherion_ai.logging import get_logger

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-not-found,import-untyped]

    from zetherion_ai.security.encryption import FieldEncryptor

log = get_logger("zetherion_ai.owner_ci.storage")

_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
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
        metadata = dict(row["metadata_json"] or {})
        return {
            "owner_id": str(row["owner_id"]),
            "repo_id": str(row["repo_id"]),
            "display_name": self._decrypt_text(str(row["display_name_value"])) or "",
            "github_repo": str(row["github_repo"]),
            "default_branch": str(row["default_branch"]),
            "stack_kind": str(row["stack_kind"]),
            **self._repo_profile_extensions(metadata),
            "local_fast_lanes": list(row["local_fast_lanes"] or []),
            "windows_full_lanes": list(row["windows_full_lanes"] or []),
            "review_policy": dict(row["review_policy"] or {}),
            "promotion_policy": dict(row["promotion_policy"] or {}),
            "allowed_paths": list(row["allowed_paths"] or []),
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
            "tags": list(row["tags_json"] or []),
            "current": bool(row["current_version"]),
            "metadata": dict(row["metadata_json"] or {}),
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
            "plan": dict(row["plan_json"] or {}),
            "review_receipts": dict(row["review_receipts"] or {}),
            "github_receipts": dict(row["github_receipts"] or {}),
            "metadata": dict(row["metadata_json"] or {}),
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
            "command": list(row["command_json"] or []),
            "env_refs": list(row["env_refs_json"] or []),
            "artifact_contract": dict(row["artifact_contract"] or {}),
            "required_capabilities": list(row["required_capabilities"] or []),
            "relay_mode": str(row["relay_mode"]),
            "metadata": dict(row.get("metadata_json") or {}),
            "status": str(row["status"]),
            "result": dict(row["result_json"] or {}),
            "error": dict(row["error_json"] or {}),
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
            "payload_json": dict(row["payload_json"] or {}),
            "required_capabilities": list(row["required_capabilities"] or []),
            "artifact_contract": dict(row["artifact_contract"] or {}),
            "status": str(row["status"]),
            "idempotency_key": str(row["idempotency_key"]),
            "execution_target": str(row["execution_target"]),
            "claimed_by_node_id": (
                str(row["claimed_by_node_id"]) if row["claimed_by_node_id"] else None
            ),
            "claimed_session_id": (
                str(row["claimed_session_id"]) if row["claimed_session_id"] else None
            ),
            "result_json": dict(row["result_json"] or {}),
            "error_json": dict(row["error_json"] or {}),
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
            "plan": dict(row["plan_json"] or {}),
            "metadata": dict(row["metadata_json"] or {}),
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
            "schedule_spec": dict(row["schedule_spec_json"] or {}),
            "active": bool(row["active"]),
            "metadata": dict(row["metadata_json"] or {}),
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
            "payload": dict(row["payload_json"] or {}),
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
            "metadata": dict(row["metadata_json"] or {}),
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
            "sample": dict(row["sample_json"] or {}),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
        }

    def _debug_bundle_from_row(self, row: dict[str, Any]) -> dict[str, Any]:
        return {
            "owner_id": str(row["owner_id"]),
            "bundle_id": str(row["bundle_id"]),
            "repo_id": str(row["repo_id"]),
            "run_id": str(row["run_id"]) if row.get("run_id") else None,
            "shard_id": str(row["shard_id"]) if row.get("shard_id") else None,
            "bundle": dict(row["bundle_json"] or {}),
            "created_at": row["created_at"].isoformat() if row.get("created_at") else None,
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
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
                    $11::jsonb, $12, $13, $14::jsonb, now(), now()
                )
                ON CONFLICT (owner_id, repo_id) DO UPDATE SET
                    display_name_value = EXCLUDED.display_name_value,
                    github_repo = EXCLUDED.github_repo,
                    default_branch = EXCLUDED.default_branch,
                    stack_kind = EXCLUDED.stack_kind,
                    local_fast_lanes = EXCLUDED.local_fast_lanes,
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
                json.dumps(receipt),
            )
        if row is None:
            return None
        await self._recompute_run_status(owner_id, run_id)
        return await self.get_run(owner_id, run_id)

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
        items = [dict(row["summary_json"] or {}) for row in rows]
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

    async def get_reporting_summary(self, owner_id: str) -> dict[str, Any]:
        repos = await self.list_repo_profiles(owner_id)
        runs = await self.list_runs(owner_id, limit=200)
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
                },
            )
            summary["run_count"] += 1
            if run.get("status") == "failed":
                summary["failed_runs"] += 1
            if run.get("status") == "ready_to_merge":
                summary["ready_to_merge_runs"] += 1
        return {
            "owner_id": owner_id,
            "repos": list(by_repo.values()),
            "run_count": len(runs),
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
        payload = dict(row["manifest_json"] or {})
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
        payload = dict(row["manifest_json"] or {})
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
        return dict(row["receipt_json"] or {})

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
            "manifest": dict(row["manifest_json"] or {}),
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
                "manifest": dict(row["manifest_json"] or {}),
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
            "manifest": dict(row["manifest_json"] or {}),
            "updated_at": row["updated_at"].isoformat() if row.get("updated_at") else None,
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
                SELECT status
                  FROM {shards_table}
                 WHERE owner_id = $1
                   AND run_id = $2
                """,
                owner_id,
                run_id,
            )
            shard_statuses = [str(row["status"]).strip().lower() for row in shard_rows]
            review_receipts = dict(run_row["review_receipts"] or {})
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
