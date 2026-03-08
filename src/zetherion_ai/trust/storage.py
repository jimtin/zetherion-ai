"""Canonical trust persistence for policies, grants, scorecards, feedback, and audits."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import UTC, datetime
from hashlib import blake2b
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from zetherion_ai.logging import get_logger
from zetherion_ai.trust.engine import TrustDecision

if TYPE_CHECKING:
    import asyncpg  # type: ignore[import-untyped]

log = get_logger("zetherion_ai.trust.storage")

_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_ALLOWED_RESOURCE_SCOPE_PREFIXES = (
    "tenant:",
    "owner_personal:",
    "owner_portfolio:",
    "repo:",
    "codex.session:",
    "desktop.app:",
    "messaging.chat:",
    "worker_artifact:",
)


def _schema_lock_key(schema: str) -> int:
    digest = blake2b(f"trust_storage:{schema}".encode(), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)


@dataclass(frozen=True)
class TrustPolicyRecord:
    """Canonical persisted trust policy."""

    policy_id: str
    principal_id: str
    principal_type: str
    resource_scope: str
    action: str
    mode: str
    risk_class: str
    tenant_id: str | None = None
    source_system: str = ""
    source_record_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None
    updated_at: datetime | None = None


@dataclass(frozen=True)
class TrustGrantRecord:
    """Canonical persisted trust grant."""

    grant_id: str
    grantee_id: str
    grantee_type: str
    resource_scope: str
    permissions: list[str] = field(default_factory=list)
    tenant_id: str | None = None
    granted_by_id: str | None = None
    granted_by_type: str | None = None
    source_system: str = ""
    source_record_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    issued_at: datetime | None = None
    expires_at: datetime | None = None
    revoked_at: datetime | None = None
    revoke_reason: str | None = None

    @property
    def active(self) -> bool:
        now = datetime.now(UTC)
        return self.revoked_at is None and (self.expires_at is None or self.expires_at > now)


@dataclass(frozen=True)
class TrustScorecardRecord:
    """Canonical persisted trust scorecard."""

    scorecard_id: str
    subject_id: str
    subject_type: str
    resource_scope: str
    action: str
    tenant_id: str | None = None
    score: float = 0.0
    approvals: int = 0
    rejections: int = 0
    edits: int = 0
    total_interactions: int = 0
    level: str | None = None
    ceiling: float | None = None
    source_system: str = ""
    source_record_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)
    updated_at: datetime | None = None


@dataclass(frozen=True)
class TrustFeedbackEventRecord:
    """Canonical persisted trust feedback event."""

    event_id: str
    subject_id: str
    subject_type: str
    resource_scope: str
    action: str
    outcome: str
    tenant_id: str | None = None
    delta: float | None = None
    source_system: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True)
class TrustDecisionAuditRecord:
    """Canonical persisted trust decision audit record."""

    decision_id: str
    adapter_name: str
    action: str
    outcome: str
    mode: str
    risk_class: str
    reason_code: str
    tenant_id: str | None = None
    principal_id: str | None = None
    principal_type: str | None = None
    resource_scope: str | None = None
    requires_two_person: bool = False
    source_system: str = "shadow_engine"
    trace: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)
    created_at: datetime | None = None


@dataclass(frozen=True)
class TrustPolicyInput:
    """Upsert payload for canonical trust policies."""

    principal_id: str
    principal_type: str
    resource_scope: str
    action: str
    mode: str
    risk_class: str
    tenant_id: str | None = None
    source_system: str = "manual"
    source_record_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrustGrantInput:
    """Upsert payload for canonical trust grants."""

    grantee_id: str
    grantee_type: str
    resource_scope: str
    permissions: list[str] = field(default_factory=list)
    tenant_id: str | None = None
    granted_by_id: str | None = None
    granted_by_type: str | None = None
    expires_at: datetime | None = None
    source_system: str = "manual"
    source_record_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrustScorecardInput:
    """Upsert payload for canonical trust scorecards."""

    subject_id: str
    subject_type: str
    resource_scope: str
    action: str
    tenant_id: str | None = None
    score: float = 0.0
    approvals: int = 0
    rejections: int = 0
    edits: int = 0
    total_interactions: int = 0
    level: str | None = None
    ceiling: float | None = None
    source_system: str = "manual"
    source_record_id: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrustFeedbackEventInput:
    """Insert payload for canonical trust feedback events."""

    subject_id: str
    subject_type: str
    resource_scope: str
    action: str
    outcome: str
    tenant_id: str | None = None
    delta: float | None = None
    source_system: str = "manual"
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class TrustDecisionAuditInput:
    """Insert payload for canonical trust decision audit rows."""

    adapter_name: str
    action: str
    outcome: str
    mode: str
    risk_class: str
    reason_code: str
    tenant_id: str | None = None
    principal_id: str | None = None
    principal_type: str | None = None
    resource_scope: str | None = None
    requires_two_person: bool = False
    source_system: str = "shadow_engine"
    trace: tuple[str, ...] = ()
    metadata: dict[str, Any] = field(default_factory=dict)


class TrustStorage:
    """PostgreSQL-backed canonical trust storage."""

    def __init__(self, *, schema: str = "control_plane") -> None:
        self._pool: asyncpg.Pool | None = None
        self._schema = _validate_schema_name(schema)

    async def initialize(self, pool: asyncpg.Pool) -> None:
        self._pool = pool
        async with pool.acquire() as conn, conn.transaction():
            await conn.execute(
                "SELECT pg_advisory_xact_lock($1::bigint)",
                _schema_lock_key(self._schema),
            )
            await conn.execute(_schema_sql(self._schema))
        log.info("trust_storage_initialized", schema=self._schema)

    async def upsert_policy(self, policy: TrustPolicyInput) -> TrustPolicyRecord:
        pool = self._require_pool()
        resource_scope = normalize_resource_scope(policy.resource_scope)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO "{self._schema}".trust_policies (
                    policy_id,
                    tenant_id,
                    principal_id,
                    principal_type,
                    resource_scope,
                    action,
                    mode,
                    risk_class,
                    source_system,
                    source_record_id,
                    metadata_json
                )
                VALUES (
                    $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11::jsonb
                )
                ON CONFLICT (source_system, source_record_id)
                WHERE source_record_id IS NOT NULL
                DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    principal_id = EXCLUDED.principal_id,
                    principal_type = EXCLUDED.principal_type,
                    resource_scope = EXCLUDED.resource_scope,
                    action = EXCLUDED.action,
                    mode = EXCLUDED.mode,
                    risk_class = EXCLUDED.risk_class,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = NOW()
                RETURNING *
                """,  # nosec B608 - self._schema is regex-validated before interpolation
                str(uuid4()),
                policy.tenant_id,
                policy.principal_id,
                policy.principal_type,
                resource_scope,
                policy.action,
                _normalize_text(policy.mode),
                _normalize_text(policy.risk_class),
                policy.source_system,
                policy.source_record_id,
                _as_json_text(policy.metadata),
            )
        if row is None:
            raise RuntimeError("Failed to upsert trust policy")
        return _policy_from_row(row)

    async def upsert_grant(self, grant: TrustGrantInput) -> TrustGrantRecord:
        pool = self._require_pool()
        resource_scope = normalize_resource_scope(grant.resource_scope)
        permissions = normalize_grant_permissions(grant.permissions)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO "{self._schema}".trust_grants (
                    grant_id,
                    tenant_id,
                    grantee_id,
                    grantee_type,
                    resource_scope,
                    permissions_json,
                    granted_by_id,
                    granted_by_type,
                    source_system,
                    source_record_id,
                    metadata_json,
                    expires_at
                )
                VALUES (
                    $1::uuid, $2, $3, $4, $5, $6::jsonb, $7, $8, $9, $10, $11::jsonb, $12
                )
                ON CONFLICT (source_system, source_record_id)
                WHERE source_record_id IS NOT NULL
                DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    grantee_id = EXCLUDED.grantee_id,
                    grantee_type = EXCLUDED.grantee_type,
                    resource_scope = EXCLUDED.resource_scope,
                    permissions_json = EXCLUDED.permissions_json,
                    granted_by_id = EXCLUDED.granted_by_id,
                    granted_by_type = EXCLUDED.granted_by_type,
                    metadata_json = EXCLUDED.metadata_json,
                    expires_at = EXCLUDED.expires_at,
                    revoked_at = NULL,
                    revoke_reason = NULL
                RETURNING *
                """,  # nosec B608 - self._schema is regex-validated before interpolation
                str(uuid4()),
                grant.tenant_id,
                grant.grantee_id,
                grant.grantee_type,
                resource_scope,
                _as_json_text(permissions),
                grant.granted_by_id,
                grant.granted_by_type,
                grant.source_system,
                grant.source_record_id,
                _as_json_text(grant.metadata),
                grant.expires_at,
            )
        if row is None:
            raise RuntimeError("Failed to upsert trust grant")
        return _grant_from_row(row)

    async def revoke_grant(
        self, grant_id: str, *, revoke_reason: str | None = None
    ) -> TrustGrantRecord:
        pool = self._require_pool()
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                UPDATE "{self._schema}".trust_grants
                SET revoked_at = NOW(),
                    revoke_reason = $2
                WHERE grant_id = $1::uuid
                RETURNING *
                """,  # nosec B608 - self._schema is regex-validated before interpolation
                str(grant_id),
                revoke_reason,
            )
        if row is None:
            raise ValueError("Trust grant not found")
        return _grant_from_row(row)

    async def list_active_grants(
        self,
        *,
        grantee_id: str,
        grantee_type: str,
        tenant_id: str | None = None,
        resource_scope_prefix: str | None = None,
    ) -> list[TrustGrantRecord]:
        pool = self._require_pool()
        params: list[Any] = [grantee_id, grantee_type]
        where = [
            "grantee_id = $1",
            "grantee_type = $2",
            "revoked_at IS NULL",
            "(expires_at IS NULL OR expires_at > NOW())",
        ]
        if tenant_id is not None:
            params.append(tenant_id)
            where.append(f"tenant_id = ${len(params)}")
        if resource_scope_prefix is not None:
            params.append(resource_scope_prefix.rstrip("*") + "%")
            where.append(f"resource_scope LIKE ${len(params)}")
        async with pool.acquire() as conn:
            rows = await conn.fetch(
                f"""
                SELECT *
                FROM "{self._schema}".trust_grants
                WHERE {' AND '.join(where)}
                ORDER BY expires_at ASC NULLS LAST, issued_at DESC
                """,  # nosec B608 - self._schema is validated and WHERE clauses use placeholders only
                *params,
            )
        return [_grant_from_row(row) for row in rows]

    async def get_scorecard(
        self,
        *,
        subject_id: str,
        subject_type: str,
        resource_scope: str,
        action: str,
        tenant_id: str | None = None,
    ) -> TrustScorecardRecord | None:
        """Fetch the latest canonical scorecard for one subject/resource/action tuple."""

        pool = self._require_pool()
        normalized_scope = normalize_resource_scope(resource_scope)
        params: list[Any] = [subject_id, subject_type, normalized_scope, action]
        where = [
            "subject_id = $1",
            "subject_type = $2",
            "resource_scope = $3",
            "action = $4",
        ]
        if tenant_id is None:
            where.append("tenant_id IS NULL")
        else:
            params.append(tenant_id)
            where.append(f"tenant_id = ${len(params)}")

        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                SELECT *
                FROM "{self._schema}".trust_scorecards
                WHERE {' AND '.join(where)}
                ORDER BY updated_at DESC
                LIMIT 1
                """,  # nosec B608 - self._schema is regex-validated and values use placeholders only
                *params,
            )
        return _scorecard_from_row(row) if row is not None else None

    async def record_feedback_outcome(
        self,
        *,
        subject_id: str,
        subject_type: str,
        resource_scope: str,
        action: str,
        outcome: str,
        tenant_id: str | None = None,
        source_system: str = "review_inbox",
        metadata: dict[str, Any] | None = None,
        level: str | None = None,
        ceiling: float | None = None,
    ) -> tuple[TrustFeedbackEventRecord, TrustScorecardRecord]:
        """Record one feedback event and fold it into the canonical scorecard."""

        normalized_scope = normalize_resource_scope(resource_scope)
        normalized_outcome = _normalize_text(outcome)
        merged_metadata = dict(metadata or {})
        pool = self._require_pool()
        current = await self.get_scorecard(
            subject_id=subject_id,
            subject_type=subject_type,
            resource_scope=normalized_scope,
            action=action,
            tenant_id=tenant_id,
        )

        delta = _feedback_delta(normalized_outcome)
        approvals, rejections, edits = _feedback_counters(normalized_outcome)
        effective_ceiling = (
            current.ceiling if current is not None and current.ceiling is not None else ceiling
        )
        next_score = _clamp_score(
            (current.score if current is not None else 0.0) + delta,
            ceiling=effective_ceiling,
        )
        next_level = level or _feedback_level(next_score)

        if current is None:
            scorecard = await self.upsert_scorecard(
                TrustScorecardInput(
                    tenant_id=tenant_id,
                    subject_id=subject_id,
                    subject_type=subject_type,
                    resource_scope=normalized_scope,
                    action=action,
                    score=next_score,
                    approvals=approvals,
                    rejections=rejections,
                    edits=edits,
                    total_interactions=1,
                    level=next_level,
                    ceiling=effective_ceiling,
                    source_system=source_system,
                    source_record_id=_feedback_source_record_id(
                        tenant_id=tenant_id,
                        subject_id=subject_id,
                        subject_type=subject_type,
                        resource_scope=normalized_scope,
                        action=action,
                    ),
                    metadata=merged_metadata,
                )
            )
        else:
            merged_row_metadata = dict(current.metadata)
            merged_row_metadata.update(merged_metadata)
            async with pool.acquire() as conn:
                row = await conn.fetchrow(
                    f"""
                    UPDATE "{self._schema}".trust_scorecards
                    SET score = $2,
                        approvals = $3,
                        rejections = $4,
                        edits = $5,
                        total_interactions = $6,
                        level = $7,
                        ceiling = $8,
                        metadata_json = $9::jsonb,
                        updated_at = NOW()
                    WHERE scorecard_id = $1::uuid
                    RETURNING *
                    """,  # nosec B608 - self._schema is regex-validated before interpolation
                    current.scorecard_id,
                    next_score,
                    current.approvals + approvals,
                    current.rejections + rejections,
                    current.edits + edits,
                    current.total_interactions + 1,
                    next_level,
                    effective_ceiling,
                    _as_json_text(merged_row_metadata),
                )
            if row is None:
                raise RuntimeError("Failed to update trust scorecard from feedback")
            scorecard = _scorecard_from_row(row)

        event_metadata = dict(merged_metadata)
        event_metadata.update(
            {
                "score_after": scorecard.score,
                "scorecard_id": scorecard.scorecard_id,
                "level_after": scorecard.level,
            }
        )
        event = await self.record_feedback_event(
            TrustFeedbackEventInput(
                tenant_id=tenant_id,
                subject_id=subject_id,
                subject_type=subject_type,
                resource_scope=normalized_scope,
                action=action,
                outcome=normalized_outcome,
                delta=delta,
                source_system=source_system,
                metadata=event_metadata,
            )
        )
        return event, scorecard

    async def upsert_scorecard(self, scorecard: TrustScorecardInput) -> TrustScorecardRecord:
        pool = self._require_pool()
        resource_scope = normalize_resource_scope(scorecard.resource_scope)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO "{self._schema}".trust_scorecards (
                    scorecard_id,
                    tenant_id,
                    subject_id,
                    subject_type,
                    resource_scope,
                    action,
                    score,
                    approvals,
                    rejections,
                    edits,
                    total_interactions,
                    level,
                    ceiling,
                    source_system,
                    source_record_id,
                    metadata_json
                )
                VALUES (
                    $1::uuid, $2, $3, $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13, $14, $15, $16::jsonb
                )
                ON CONFLICT (source_system, source_record_id)
                WHERE source_record_id IS NOT NULL
                DO UPDATE SET
                    tenant_id = EXCLUDED.tenant_id,
                    subject_id = EXCLUDED.subject_id,
                    subject_type = EXCLUDED.subject_type,
                    resource_scope = EXCLUDED.resource_scope,
                    action = EXCLUDED.action,
                    score = EXCLUDED.score,
                    approvals = EXCLUDED.approvals,
                    rejections = EXCLUDED.rejections,
                    edits = EXCLUDED.edits,
                    total_interactions = EXCLUDED.total_interactions,
                    level = EXCLUDED.level,
                    ceiling = EXCLUDED.ceiling,
                    metadata_json = EXCLUDED.metadata_json,
                    updated_at = NOW()
                RETURNING *
                """,  # nosec B608 - self._schema is regex-validated before interpolation
                str(uuid4()),
                scorecard.tenant_id,
                scorecard.subject_id,
                scorecard.subject_type,
                resource_scope,
                scorecard.action,
                scorecard.score,
                scorecard.approvals,
                scorecard.rejections,
                scorecard.edits,
                scorecard.total_interactions,
                scorecard.level,
                scorecard.ceiling,
                scorecard.source_system,
                scorecard.source_record_id,
                _as_json_text(scorecard.metadata),
            )
        if row is None:
            raise RuntimeError("Failed to upsert trust scorecard")
        return _scorecard_from_row(row)

    async def record_feedback_event(
        self, event: TrustFeedbackEventInput
    ) -> TrustFeedbackEventRecord:
        pool = self._require_pool()
        resource_scope = normalize_resource_scope(event.resource_scope)
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO "{self._schema}".trust_feedback_events (
                    event_id,
                    tenant_id,
                    subject_id,
                    subject_type,
                    resource_scope,
                    action,
                    outcome,
                    delta,
                    source_system,
                    metadata_json
                )
                VALUES (
                    $1::uuid, $2, $3, $4, $5, $6, $7, $8, $9, $10::jsonb
                )
                RETURNING *
                """,  # nosec B608 - self._schema is regex-validated before interpolation
                str(uuid4()),
                event.tenant_id,
                event.subject_id,
                event.subject_type,
                resource_scope,
                event.action,
                _normalize_text(event.outcome),
                event.delta,
                event.source_system,
                _as_json_text(event.metadata),
            )
        if row is None:
            raise RuntimeError("Failed to record trust feedback event")
        return _feedback_from_row(row)

    async def record_decision_audit(
        self, audit: TrustDecisionAuditInput
    ) -> TrustDecisionAuditRecord:
        pool = self._require_pool()
        resource_scope = (
            normalize_resource_scope(audit.resource_scope)
            if audit.resource_scope is not None
            else None
        )
        async with pool.acquire() as conn:
            row = await conn.fetchrow(
                f"""
                INSERT INTO "{self._schema}".trust_decision_audit (
                    decision_id,
                    tenant_id,
                    adapter_name,
                    principal_id,
                    principal_type,
                    resource_scope,
                    action,
                    outcome,
                    mode,
                    risk_class,
                    reason_code,
                    requires_two_person,
                    source_system,
                    trace_json,
                    metadata_json
                )
                VALUES (
                    $1::uuid, $2, $3, $4, $5, $6, $7, $8,
                    $9, $10, $11, $12, $13, $14::jsonb, $15::jsonb
                )
                RETURNING *
                """,  # nosec B608 - self._schema is regex-validated before interpolation
                str(uuid4()),
                audit.tenant_id,
                audit.adapter_name,
                audit.principal_id,
                audit.principal_type,
                resource_scope,
                audit.action,
                _normalize_text(audit.outcome),
                _normalize_text(audit.mode),
                _normalize_text(audit.risk_class),
                audit.reason_code,
                audit.requires_two_person,
                audit.source_system,
                _as_json_text(list(audit.trace)),
                _as_json_text(audit.metadata),
            )
        if row is None:
            raise RuntimeError("Failed to record trust decision audit")
        return _audit_from_row(row)

    async def record_decision(
        self,
        decision: TrustDecision,
        *,
        source_system: str = "trust_engine",
    ) -> TrustDecisionAuditRecord:
        """Persist one canonical trust decision."""

        resource_scope = None
        if decision.resource is not None:
            candidate_scope = str(decision.resource.resource_id or "").strip()
            if candidate_scope and any(
                candidate_scope.startswith(prefix) for prefix in _ALLOWED_RESOURCE_SCOPE_PREFIXES
            ):
                resource_scope = candidate_scope

        return await self.record_decision_audit(
            TrustDecisionAuditInput(
                adapter_name=decision.adapter_name,
                action=decision.action,
                outcome=decision.outcome.value,
                mode=decision.mode.value,
                risk_class=decision.risk_class.value,
                reason_code=decision.reason_code,
                tenant_id=decision.principal.tenant_id if decision.principal else None,
                principal_id=decision.principal.principal_id if decision.principal else None,
                principal_type=decision.principal.principal_type if decision.principal else None,
                resource_scope=resource_scope,
                requires_two_person=decision.requires_two_person,
                source_system=source_system,
                trace=decision.trace,
                metadata=decision.metadata,
            )
        )

    async def record_shadow_decision(
        self,
        decision: TrustDecision,
        *,
        source_system: str = "shadow_engine",
    ) -> TrustDecisionAuditRecord:
        """Compatibility wrapper for legacy shadow-mode call sites."""

        effective_source = "trust_engine" if source_system == "shadow_engine" else source_system
        return await self.record_decision(decision, source_system=effective_source)

    def _require_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            raise RuntimeError("TrustStorage is not initialized")
        return self._pool


async def ensure_trust_storage_schema(
    pool: Any, *, schema: str = "control_plane"
) -> tuple[str, ...]:
    """Best-effort bootstrap for canonical trust tables."""

    if pool is None:
        return ()

    storage = TrustStorage(schema=schema)
    try:
        await storage.initialize(pool)
    except AttributeError:
        log.warning("trust_storage_bootstrap_skipped", reason="pool_missing_acquire")
        return ()
    except TypeError:
        log.warning("trust_storage_bootstrap_skipped", reason="pool_not_async_context_manager")
        return ()
    return (
        f"{storage._schema}.trust_policies",
        f"{storage._schema}.trust_grants",
        f"{storage._schema}.trust_scorecards",
        f"{storage._schema}.trust_feedback_events",
        f"{storage._schema}.trust_decision_audit",
    )


def normalize_resource_scope(resource_scope: str) -> str:
    """Validate a canonical trust resource scope string."""

    candidate = str(resource_scope or "").strip()
    if not candidate:
        raise ValueError("Resource scope is required")
    if not candidate.endswith("*") and candidate.endswith(":"):
        raise ValueError("Resource scope suffix is required")
    if not any(candidate.startswith(prefix) for prefix in _ALLOWED_RESOURCE_SCOPE_PREFIXES):
        raise ValueError(f"Unsupported trust resource scope: {resource_scope!r}")
    return candidate


def normalize_grant_permissions(permissions: list[str] | tuple[str, ...]) -> list[str]:
    """Normalize grant permission values."""

    normalized = sorted({str(item).strip().lower() for item in permissions if str(item).strip()})
    if not normalized:
        raise ValueError("At least one grant permission is required")
    return normalized


def _schema_sql(schema: str) -> str:
    return f"""
CREATE SCHEMA IF NOT EXISTS "{schema}";

CREATE TABLE IF NOT EXISTS "{schema}".trust_policies (
    policy_id UUID PRIMARY KEY,
    tenant_id TEXT,
    principal_id TEXT NOT NULL,
    principal_type TEXT NOT NULL,
    resource_scope TEXT NOT NULL,
    action TEXT NOT NULL,
    mode TEXT NOT NULL,
    risk_class TEXT NOT NULL,
    source_system TEXT NOT NULL,
    source_record_id TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_trust_policies_source
    ON "{schema}".trust_policies (source_system, source_record_id)
    WHERE source_record_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trust_policies_lookup
    ON "{schema}".trust_policies (tenant_id, principal_type, principal_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS "{schema}".trust_grants (
    grant_id UUID PRIMARY KEY,
    tenant_id TEXT,
    grantee_id TEXT NOT NULL,
    grantee_type TEXT NOT NULL,
    resource_scope TEXT NOT NULL,
    permissions_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    granted_by_id TEXT,
    granted_by_type TEXT,
    source_system TEXT NOT NULL,
    source_record_id TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    issued_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    expires_at TIMESTAMPTZ,
    revoked_at TIMESTAMPTZ,
    revoke_reason TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_trust_grants_source
    ON "{schema}".trust_grants (source_system, source_record_id)
    WHERE source_record_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trust_grants_active
    ON "{schema}".trust_grants (
        tenant_id,
        grantee_type,
        grantee_id,
        revoked_at,
        expires_at DESC
    );

CREATE TABLE IF NOT EXISTS "{schema}".trust_scorecards (
    scorecard_id UUID PRIMARY KEY,
    tenant_id TEXT,
    subject_id TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    resource_scope TEXT NOT NULL,
    action TEXT NOT NULL,
    score DOUBLE PRECISION NOT NULL DEFAULT 0.0,
    approvals INTEGER NOT NULL DEFAULT 0,
    rejections INTEGER NOT NULL DEFAULT 0,
    edits INTEGER NOT NULL DEFAULT 0,
    total_interactions INTEGER NOT NULL DEFAULT 0,
    level TEXT,
    ceiling DOUBLE PRECISION,
    source_system TEXT NOT NULL,
    source_record_id TEXT,
    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE UNIQUE INDEX IF NOT EXISTS uq_trust_scorecards_source
    ON "{schema}".trust_scorecards (source_system, source_record_id)
    WHERE source_record_id IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_trust_scorecards_lookup
    ON "{schema}".trust_scorecards (tenant_id, subject_type, subject_id, updated_at DESC);

CREATE TABLE IF NOT EXISTS "{schema}".trust_feedback_events (
    event_id UUID PRIMARY KEY,
    tenant_id TEXT,
    subject_id TEXT NOT NULL,
    subject_type TEXT NOT NULL,
    resource_scope TEXT NOT NULL,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    delta DOUBLE PRECISION,
    source_system TEXT NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trust_feedback_events_lookup
    ON "{schema}".trust_feedback_events (tenant_id, subject_type, subject_id, created_at DESC);

CREATE TABLE IF NOT EXISTS "{schema}".trust_decision_audit (
    decision_id UUID PRIMARY KEY,
    tenant_id TEXT,
    adapter_name TEXT NOT NULL,
    principal_id TEXT,
    principal_type TEXT,
    resource_scope TEXT,
    action TEXT NOT NULL,
    outcome TEXT NOT NULL,
    mode TEXT NOT NULL,
    risk_class TEXT NOT NULL,
    reason_code TEXT NOT NULL,
    requires_two_person BOOLEAN NOT NULL DEFAULT FALSE,
    source_system TEXT NOT NULL,
    trace_json JSONB NOT NULL DEFAULT '[]'::jsonb,
    metadata_json JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);
CREATE INDEX IF NOT EXISTS idx_trust_decision_audit_lookup
    ON "{schema}".trust_decision_audit (tenant_id, adapter_name, created_at DESC);
"""


def _validate_schema_name(schema_name: str) -> str:
    candidate = str(schema_name or "").strip()
    if not _SCHEMA_NAME_RE.fullmatch(candidate):
        raise ValueError(f"Invalid PostgreSQL schema name: {schema_name!r}")
    return candidate


def _normalize_text(value: str) -> str:
    return str(value or "").strip().lower()


def _as_json_text(value: Any) -> str:
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _coerce_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value
    return None


def _json_dict(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


def _json_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return [str(item) for item in value]
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError:
            return []
        return [str(item) for item in parsed] if isinstance(parsed, list) else []
    return []


def _feedback_delta(outcome: str) -> float:
    deltas = {
        "approved": 0.05,
        "minor_edit": -0.02,
        "major_edit": -0.10,
        "rejected": -0.20,
        "escalated": 0.0,
    }
    return float(deltas.get(_normalize_text(outcome), 0.0))


def _feedback_counters(outcome: str) -> tuple[int, int, int]:
    normalized = _normalize_text(outcome)
    if normalized == "approved":
        return (1, 0, 0)
    if normalized == "rejected":
        return (0, 1, 0)
    if normalized in {"minor_edit", "major_edit"}:
        return (0, 0, 1)
    return (0, 0, 0)


def _clamp_score(score: float, *, ceiling: float | None = None) -> float:
    upper_bound = min(1.0, ceiling) if ceiling is not None else 1.0
    return max(0.0, min(float(score), upper_bound))


def _feedback_level(score: float) -> str:
    if score >= 0.85:
        return "auto"
    if score >= 0.60:
        return "draft"
    if score >= 0.25:
        return "ask"
    return "review"


def _feedback_source_record_id(
    *,
    tenant_id: str | None,
    subject_id: str,
    subject_type: str,
    resource_scope: str,
    action: str,
) -> str:
    tenant_part = str(tenant_id).strip() if tenant_id is not None else "owner"
    return f"{tenant_part}:{subject_type}:{subject_id}:{resource_scope}:{action}"


def _policy_from_row(row: Any) -> TrustPolicyRecord:
    return TrustPolicyRecord(
        policy_id=str(row["policy_id"]),
        tenant_id=str(row["tenant_id"]).strip() if row.get("tenant_id") is not None else None,
        principal_id=str(row["principal_id"]),
        principal_type=str(row["principal_type"]),
        resource_scope=str(row["resource_scope"]),
        action=str(row["action"]),
        mode=str(row["mode"]),
        risk_class=str(row["risk_class"]),
        source_system=str(row["source_system"]),
        source_record_id=(str(row["source_record_id"]) if row.get("source_record_id") else None),
        metadata=_json_dict(row.get("metadata_json")),
        created_at=_coerce_datetime(row.get("created_at")),
        updated_at=_coerce_datetime(row.get("updated_at")),
    )


def _grant_from_row(row: Any) -> TrustGrantRecord:
    return TrustGrantRecord(
        grant_id=str(row["grant_id"]),
        tenant_id=str(row["tenant_id"]).strip() if row.get("tenant_id") is not None else None,
        grantee_id=str(row["grantee_id"]),
        grantee_type=str(row["grantee_type"]),
        resource_scope=str(row["resource_scope"]),
        permissions=_json_list(row.get("permissions_json")),
        granted_by_id=(str(row["granted_by_id"]) if row.get("granted_by_id") else None),
        granted_by_type=(str(row["granted_by_type"]) if row.get("granted_by_type") else None),
        source_system=str(row["source_system"]),
        source_record_id=(str(row["source_record_id"]) if row.get("source_record_id") else None),
        metadata=_json_dict(row.get("metadata_json")),
        issued_at=_coerce_datetime(row.get("issued_at")),
        expires_at=_coerce_datetime(row.get("expires_at")),
        revoked_at=_coerce_datetime(row.get("revoked_at")),
        revoke_reason=(str(row["revoke_reason"]) if row.get("revoke_reason") else None),
    )


def _scorecard_from_row(row: Any) -> TrustScorecardRecord:
    return TrustScorecardRecord(
        scorecard_id=str(row["scorecard_id"]),
        tenant_id=str(row["tenant_id"]).strip() if row.get("tenant_id") is not None else None,
        subject_id=str(row["subject_id"]),
        subject_type=str(row["subject_type"]),
        resource_scope=str(row["resource_scope"]),
        action=str(row["action"]),
        score=float(row.get("score") or 0.0),
        approvals=int(row.get("approvals") or 0),
        rejections=int(row.get("rejections") or 0),
        edits=int(row.get("edits") or 0),
        total_interactions=int(row.get("total_interactions") or 0),
        level=str(row["level"]) if row.get("level") is not None else None,
        ceiling=float(row["ceiling"]) if row.get("ceiling") is not None else None,
        source_system=str(row["source_system"]),
        source_record_id=(str(row["source_record_id"]) if row.get("source_record_id") else None),
        metadata=_json_dict(row.get("metadata_json")),
        updated_at=_coerce_datetime(row.get("updated_at")),
    )


def _feedback_from_row(row: Any) -> TrustFeedbackEventRecord:
    return TrustFeedbackEventRecord(
        event_id=str(row["event_id"]),
        tenant_id=str(row["tenant_id"]).strip() if row.get("tenant_id") is not None else None,
        subject_id=str(row["subject_id"]),
        subject_type=str(row["subject_type"]),
        resource_scope=str(row["resource_scope"]),
        action=str(row["action"]),
        outcome=str(row["outcome"]),
        delta=float(row["delta"]) if row.get("delta") is not None else None,
        source_system=str(row["source_system"]),
        metadata=_json_dict(row.get("metadata_json")),
        created_at=_coerce_datetime(row.get("created_at")),
    )


def _audit_from_row(row: Any) -> TrustDecisionAuditRecord:
    trace_value = row.get("trace_json", [])
    if isinstance(trace_value, str):
        try:
            trace_value = json.loads(trace_value)
        except json.JSONDecodeError:
            trace_value = []
    trace = tuple(str(item) for item in trace_value) if isinstance(trace_value, list) else ()
    return TrustDecisionAuditRecord(
        decision_id=str(row["decision_id"]),
        tenant_id=str(row["tenant_id"]).strip() if row.get("tenant_id") is not None else None,
        adapter_name=str(row["adapter_name"]),
        principal_id=(str(row["principal_id"]) if row.get("principal_id") else None),
        principal_type=(str(row["principal_type"]) if row.get("principal_type") else None),
        resource_scope=(str(row["resource_scope"]) if row.get("resource_scope") else None),
        action=str(row["action"]),
        outcome=str(row["outcome"]),
        mode=str(row["mode"]),
        risk_class=str(row["risk_class"]),
        reason_code=str(row["reason_code"]),
        requires_two_person=bool(row.get("requires_two_person", False)),
        source_system=str(row["source_system"]),
        trace=trace,
        metadata=_json_dict(row.get("metadata_json")),
        created_at=_coerce_datetime(row.get("created_at")),
    )
