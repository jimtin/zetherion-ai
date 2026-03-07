"""Legacy-to-canonical trust persistence backfill helpers."""

from __future__ import annotations

from typing import Any

from zetherion_ai.personal.models import PersonalPolicy, PolicyMode
from zetherion_ai.skills.github.models import ActionType, AutonomyConfig, AutonomyLevel
from zetherion_ai.skills.gmail.replies import ReplyType
from zetherion_ai.skills.gmail.trust import TrustScore
from zetherion_ai.skills.youtube.models import TrustLevel
from zetherion_ai.trust.engine import TrustMode, TrustRiskClass
from zetherion_ai.trust.storage import (
    TrustGrantInput,
    TrustGrantRecord,
    TrustPolicyInput,
    TrustPolicyRecord,
    TrustScorecardInput,
    TrustScorecardRecord,
    TrustStorage,
)


class TrustBackfillService:
    """Maps legacy trust data sources into canonical trust persistence."""

    def __init__(self, storage: TrustStorage) -> None:
        self._storage = storage

    async def backfill_personal_policy(
        self, policy: PersonalPolicy
    ) -> tuple[TrustPolicyRecord, TrustScorecardRecord]:
        """Backfill one personal policy and its trust score into canonical storage."""

        resource_scope = f"owner_personal:{policy.domain.value}:{policy.action}"
        stored_policy = await self._storage.upsert_policy(
            TrustPolicyInput(
                principal_id=str(policy.user_id),
                principal_type="owner",
                tenant_id=None,
                resource_scope=resource_scope,
                action=policy.action,
                mode=_map_personal_mode(policy.mode).value,
                risk_class=_map_personal_risk(policy.action).value,
                source_system="personal_policy",
                source_record_id=str(policy.id)
                if policy.id is not None
                else f"{policy.user_id}:{policy.domain.value}:{policy.action}",
                metadata={
                    "domain": policy.domain.value,
                    "conditions": policy.conditions or {},
                },
            )
        )
        scorecard = await self._storage.upsert_scorecard(
            TrustScorecardInput(
                subject_id=str(policy.user_id),
                subject_type="owner",
                tenant_id=None,
                resource_scope=resource_scope,
                action=policy.action,
                score=float(policy.trust_score),
                source_system="personal_policy",
                source_record_id=(
                    f"score:{policy.id}"
                    if policy.id is not None
                    else f"score:{policy.user_id}:{policy.domain.value}:{policy.action}"
                ),
                metadata={"domain": policy.domain.value},
            )
        )
        return stored_policy, scorecard

    async def backfill_gmail_type_trust(
        self,
        *,
        user_id: int,
        reply_type: ReplyType,
        trust_score: TrustScore,
    ) -> TrustScorecardRecord:
        """Backfill Gmail per-type trust into canonical scorecards."""

        return await self._storage.upsert_scorecard(
            TrustScorecardInput(
                subject_id=str(user_id),
                subject_type="owner",
                resource_scope=f"owner_personal:gmail:type:{reply_type.value}",
                action="gmail.reply.send",
                score=trust_score.score,
                approvals=trust_score.approvals,
                rejections=trust_score.rejections,
                edits=trust_score.edits,
                total_interactions=trust_score.total_interactions,
                source_system="gmail_type_trust",
                source_record_id=f"{user_id}:{reply_type.value}",
                metadata={"reply_type": reply_type.value},
            )
        )

    async def backfill_gmail_contact_trust(
        self,
        *,
        user_id: int,
        contact_email: str,
        trust_score: TrustScore,
    ) -> TrustScorecardRecord:
        """Backfill Gmail per-contact trust into canonical scorecards."""

        return await self._storage.upsert_scorecard(
            TrustScorecardInput(
                subject_id=str(user_id),
                subject_type="owner",
                resource_scope=f"owner_personal:gmail:contact:{contact_email}",
                action="gmail.reply.send",
                score=trust_score.score,
                approvals=trust_score.approvals,
                rejections=trust_score.rejections,
                edits=trust_score.edits,
                total_interactions=trust_score.total_interactions,
                source_system="gmail_contact_trust",
                source_record_id=f"{user_id}:{contact_email.lower()}",
                metadata={"contact_email": contact_email.lower()},
            )
        )

    async def backfill_github_autonomy(
        self,
        *,
        principal_id: str,
        config: AutonomyConfig,
        tenant_id: str | None = None,
        resource_scope: str = "repo:*",
    ) -> list[TrustPolicyRecord]:
        """Backfill GitHub autonomy config into canonical policies."""

        records: list[TrustPolicyRecord] = []
        for action in ActionType:
            level = config.get_level(action)
            record = await self._storage.upsert_policy(
                TrustPolicyInput(
                    principal_id=principal_id,
                    principal_type="owner",
                    tenant_id=tenant_id,
                    resource_scope=resource_scope,
                    action=f"github.{action.value}",
                    mode=_map_github_mode(level).value,
                    risk_class=_map_github_risk(action).value,
                    source_system="github_autonomy",
                    source_record_id=f"{principal_id}:{tenant_id or 'global'}:{action.value}",
                    metadata={"autonomy_level": level.value},
                )
            )
            records.append(record)
        return records

    async def backfill_youtube_trust(
        self,
        *,
        tenant_id: str,
        channel_id: str,
        trust_level: int,
        trust_stats: dict[str, Any],
    ) -> TrustScorecardRecord:
        """Backfill YouTube channel trust into canonical scorecards."""

        return await self._storage.upsert_scorecard(
            TrustScorecardInput(
                tenant_id=tenant_id,
                subject_id=channel_id,
                subject_type="tenant_channel",
                resource_scope=f"tenant:{tenant_id}:youtube:channel:{channel_id}",
                action="youtube.reply.approve",
                score=float(trust_stats.get("approved", 0) or 0)
                / max(int(trust_stats.get("total", 0) or 0), 1),
                approvals=int(trust_stats.get("approved", 0) or 0),
                rejections=int(trust_stats.get("rejected", 0) or 0),
                edits=0,
                total_interactions=int(trust_stats.get("total", 0) or 0),
                level=TrustLevel(trust_level).name.lower(),
                source_system="youtube_trust",
                source_record_id=f"{tenant_id}:{channel_id}",
                metadata={"trust_level": trust_level, "trust_stats": trust_stats},
            )
        )

    async def backfill_worker_messaging_grant(
        self,
        grant_record: dict[str, Any],
    ) -> TrustGrantRecord:
        """Backfill legacy worker messaging grant rows into canonical grants."""

        permissions: list[str] = []
        if bool(grant_record.get("allow_read")):
            permissions.append("read")
        if bool(grant_record.get("allow_draft")):
            permissions.append("draft")
        if bool(grant_record.get("allow_send")):
            permissions.append("send")
        return await self._storage.upsert_grant(
            TrustGrantInput(
                tenant_id=str(grant_record.get("tenant_id") or "").strip() or None,
                grantee_id=str(grant_record.get("node_id") or ""),
                grantee_type="worker_node",
                resource_scope=(
                    f"messaging.chat:{grant_record.get('provider')}:{grant_record.get('chat_id')}"
                ),
                permissions=permissions,
                granted_by_id=str(
                    grant_record.get("created_by") or grant_record.get("updated_by") or ""
                )
                or None,
                granted_by_type="system",
                expires_at=grant_record.get("expires_at"),
                source_system="worker_messaging_grant",
                source_record_id=str(grant_record.get("grant_id") or ""),
                metadata={
                    "redacted_payload": bool(grant_record.get("redacted_payload")),
                    "provider": str(grant_record.get("provider") or ""),
                    "chat_id": str(grant_record.get("chat_id") or ""),
                },
            )
        )


def _map_personal_mode(mode: PolicyMode) -> TrustMode:
    if mode == PolicyMode.AUTO:
        return TrustMode.AUTO
    if mode == PolicyMode.DRAFT:
        return TrustMode.DRAFT
    if mode == PolicyMode.NEVER:
        return TrustMode.BLOCK
    return TrustMode.ASK


def _map_personal_risk(action: str) -> TrustRiskClass:
    normalized = str(action or "").lower()
    if any(token in normalized for token in ("send", "delete", "reply", "email", "payment")):
        return TrustRiskClass.HIGH
    return TrustRiskClass.MODERATE


def _map_github_mode(level: AutonomyLevel) -> TrustMode:
    if level == AutonomyLevel.AUTONOMOUS:
        return TrustMode.AUTO
    if level == AutonomyLevel.ALWAYS_ASK:
        return TrustMode.REVIEW
    return TrustMode.ASK


def _map_github_risk(action: ActionType) -> TrustRiskClass:
    if action in {
        ActionType.FORCE_PUSH,
        ActionType.DELETE_REPO,
        ActionType.TRANSFER_REPO,
        ActionType.UPDATE_BRANCH_PROTECTION,
    }:
        return TrustRiskClass.CRITICAL
    if action in {
        ActionType.CREATE_ISSUE,
        ActionType.UPDATE_ISSUE,
        ActionType.CLOSE_ISSUE,
        ActionType.REOPEN_ISSUE,
        ActionType.CREATE_PR,
        ActionType.MERGE_PR,
        ActionType.CLOSE_PR,
        ActionType.CREATE_RELEASE,
        ActionType.DELETE_BRANCH,
        ActionType.CREATE_LABEL,
        ActionType.DELETE_LABEL,
    }:
        return TrustRiskClass.HIGH
    if action in {
        ActionType.ADD_LABEL,
        ActionType.REMOVE_LABEL,
        ActionType.ADD_COMMENT,
        ActionType.ASSIGN_ISSUE,
        ActionType.UNASSIGN_ISSUE,
        ActionType.REQUEST_REVIEW,
        ActionType.ADD_REACTION,
    }:
        return TrustRiskClass.MODERATE
    return TrustRiskClass.LOW
