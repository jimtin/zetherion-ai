"""Trust-policy evaluation for tenant-gated autonomous actions.

This module centralizes trust-tier and policy checks for high-risk automation
surfaces (messaging and autonomous coding workflows). It intentionally reads
from existing dynamic tenant/global settings and does not introduce a separate
policy storage stack.
"""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from zetherion_ai.config import get_dynamic, get_dynamic_for_tenant


class TrustTier(StrEnum):
    """Ordered trust tiers used for policy decisions."""

    TIER0 = "tier0"
    TIER1 = "tier1"
    TIER2 = "tier2"
    TIER3 = "tier3"
    TIER4 = "tier4"

    @property
    def level(self) -> int:
        return {
            TrustTier.TIER0: 0,
            TrustTier.TIER1: 1,
            TrustTier.TIER2: 2,
            TrustTier.TIER3: 3,
            TrustTier.TIER4: 4,
        }[self]

    @classmethod
    def coerce(cls, raw: Any, *, default: TrustTier) -> TrustTier:
        text = str(raw or "").strip().lower()
        aliases = {
            "0": cls.TIER0,
            "1": cls.TIER1,
            "2": cls.TIER2,
            "3": cls.TIER3,
            "4": cls.TIER4,
            "untrusted": cls.TIER0,
            "low": cls.TIER1,
            "standard": cls.TIER2,
            "elevated": cls.TIER3,
            "high": cls.TIER4,
        }
        if text in aliases:
            return aliases[text]
        with_exception = {
            cls.TIER0.value: cls.TIER0,
            cls.TIER1.value: cls.TIER1,
            cls.TIER2.value: cls.TIER2,
            cls.TIER3.value: cls.TIER3,
            cls.TIER4.value: cls.TIER4,
        }
        return with_exception.get(text, default)


class TrustActionClass(StrEnum):
    """Risk classes for actions evaluated by policy."""

    READ = "read"
    MUTATE = "mutate"
    SENSITIVE = "sensitive"
    CRITICAL = "critical"


class TrustDecisionOutcome(StrEnum):
    """Policy evaluation outcome."""

    ALLOW = "allow"
    DENY = "deny"
    APPROVAL_REQUIRED = "approval_required"


@dataclass(frozen=True)
class TrustPolicyRule:
    """Rule describing how one action should be gated."""

    action: str
    action_class: TrustActionClass
    min_tier: TrustTier = TrustTier.TIER3
    kill_switch_key: str | None = None
    requires_approval: bool = False
    requires_two_person: bool = False
    allowlist_key: str | None = None
    policy_enabled_key: str | None = None
    required_context_flags: tuple[str, ...] = ()
    elevation_key: str | None = None


@dataclass(frozen=True)
class TrustPolicyDecision:
    """Structured decision returned by the evaluator."""

    action: str
    action_class: TrustActionClass
    outcome: TrustDecisionOutcome
    status: int
    code: str
    message: str
    details: dict[str, Any] = field(default_factory=dict)
    requires_two_person: bool = False

    @property
    def allowed(self) -> bool:
        return self.outcome == TrustDecisionOutcome.ALLOW

    @property
    def approval_required(self) -> bool:
        return self.outcome == TrustDecisionOutcome.APPROVAL_REQUIRED


SettingResolver = Callable[[str | None, str, str, Any], Any]


class TrustPolicyEvaluator:
    """Tenant-aware trust policy evaluator."""

    _DEFAULT_RULES: dict[str, TrustPolicyRule] = {
        "messaging.read": TrustPolicyRule(
            action="messaging.read",
            action_class=TrustActionClass.SENSITIVE,
            min_tier=TrustTier.TIER2,
            allowlist_key="messaging_allowlisted_chats",
        ),
        "messaging.ingest": TrustPolicyRule(
            action="messaging.ingest",
            action_class=TrustActionClass.SENSITIVE,
            min_tier=TrustTier.TIER2,
            kill_switch_key="messaging_ingestion_kill_switch",
        ),
        "messaging.send": TrustPolicyRule(
            action="messaging.send",
            action_class=TrustActionClass.CRITICAL,
            min_tier=TrustTier.TIER3,
            kill_switch_key="messaging_send_kill_switch",
            requires_approval=True,
            requires_two_person=True,
            allowlist_key="messaging_allowlisted_chats",
            elevation_key="messaging_send_explicitly_elevated",
        ),
        "messaging.delete": TrustPolicyRule(
            action="messaging.delete",
            action_class=TrustActionClass.CRITICAL,
            min_tier=TrustTier.TIER3,
            kill_switch_key="messaging_send_kill_switch",
            requires_approval=True,
            requires_two_person=True,
            elevation_key="messaging_delete_explicitly_elevated",
        ),
        "automerge.execute": TrustPolicyRule(
            action="automerge.execute",
            action_class=TrustActionClass.CRITICAL,
            min_tier=TrustTier.TIER4,
            kill_switch_key="auto_merge_execution_kill_switch",
            policy_enabled_key="auto_merge_policy_enabled",
            required_context_flags=("branch_guard_passed", "risk_guard_passed"),
        ),
        "worker.register": TrustPolicyRule(
            action="worker.register",
            action_class=TrustActionClass.SENSITIVE,
            min_tier=TrustTier.TIER2,
            required_context_flags=("bootstrap_secret_valid",),
        ),
        "worker.job.claim": TrustPolicyRule(
            action="worker.job.claim",
            action_class=TrustActionClass.CRITICAL,
            min_tier=TrustTier.TIER3,
            kill_switch_key="worker_dispatch_kill_switch",
            required_context_flags=("node_registered", "node_healthy", "capability_allowlisted"),
        ),
        "worker.job.complete": TrustPolicyRule(
            action="worker.job.complete",
            action_class=TrustActionClass.CRITICAL,
            min_tier=TrustTier.TIER3,
            kill_switch_key="worker_result_accept_kill_switch",
            required_context_flags=("node_registered", "node_healthy", "capability_allowlisted"),
        ),
        "worker.capability.update": TrustPolicyRule(
            action="worker.capability.update",
            action_class=TrustActionClass.CRITICAL,
            min_tier=TrustTier.TIER3,
            requires_approval=True,
            requires_two_person=True,
            required_context_flags=("node_registered", "node_healthy"),
            elevation_key="worker_capability_update_explicitly_elevated",
        ),
        # Existing high-risk admin actions continue to route through the
        # established change-ticket approval flow.
        "secret.put": TrustPolicyRule(
            action="secret.put",
            action_class=TrustActionClass.CRITICAL,
            min_tier=TrustTier.TIER3,
            requires_approval=True,
            requires_two_person=True,
        ),
        "secret.delete": TrustPolicyRule(
            action="secret.delete",
            action_class=TrustActionClass.CRITICAL,
            min_tier=TrustTier.TIER3,
            requires_approval=True,
            requires_two_person=True,
        ),
        "email.oauth_app.put": TrustPolicyRule(
            action="email.oauth_app.put",
            action_class=TrustActionClass.CRITICAL,
            min_tier=TrustTier.TIER3,
            requires_approval=True,
            requires_two_person=True,
        ),
        "email.mailbox.delete": TrustPolicyRule(
            action="email.mailbox.delete",
            action_class=TrustActionClass.CRITICAL,
            min_tier=TrustTier.TIER3,
            requires_approval=True,
            requires_two_person=True,
        ),
        "discord.role.owner": TrustPolicyRule(
            action="discord.role.owner",
            action_class=TrustActionClass.CRITICAL,
            min_tier=TrustTier.TIER3,
            requires_approval=True,
            requires_two_person=True,
        ),
    }

    def __init__(self, *, setting_resolver: SettingResolver | None = None) -> None:
        self._setting_resolver = setting_resolver or self._resolve_setting

    @staticmethod
    def _resolve_setting(tenant_id: str | None, namespace: str, key: str, default: Any) -> Any:
        if tenant_id:
            return get_dynamic_for_tenant(tenant_id, namespace, key, default)
        return get_dynamic(namespace, key, default)

    def evaluate(
        self,
        *,
        tenant_id: str | None,
        action: str,
        context: Mapping[str, Any] | None = None,
    ) -> TrustPolicyDecision:
        """Evaluate one action for a tenant."""
        ctx = context or {}
        normalized_action = str(action).strip().lower()

        default_tier_raw = self._setting_resolver(
            tenant_id,
            "security",
            "default_trust_tier",
            "tier3",
        )
        default_tier = TrustTier.coerce(default_tier_raw, default=TrustTier.TIER3)
        current_tier = TrustTier.coerce(
            self._setting_resolver(tenant_id, "security", "trust_tier", default_tier.value),
            default=default_tier,
        )

        rule = self._DEFAULT_RULES.get(normalized_action)
        if rule is None:
            # Deny-by-default only for explicitly sensitive namespaces.
            if normalized_action.startswith(("messaging.", "automerge.", "worker.")):
                return TrustPolicyDecision(
                    action=normalized_action,
                    action_class=TrustActionClass.SENSITIVE,
                    outcome=TrustDecisionOutcome.DENY,
                    status=403,
                    code="AI_TRUST_POLICY_DENIED",
                    message="Action is blocked by deny-by-default trust policy",
                    details={"action": normalized_action},
                )
            action_class = (
                TrustActionClass.READ
                if str(ctx.get("method", "")).upper() == "GET"
                else TrustActionClass.MUTATE
            )
            return TrustPolicyDecision(
                action=normalized_action,
                action_class=action_class,
                outcome=TrustDecisionOutcome.ALLOW,
                status=200,
                code="AI_OK",
                message="Allowed",
                details={"resolved_tier": current_tier.value},
            )

        if rule.kill_switch_key and self._as_bool(
            self._setting_resolver(tenant_id, "security", rule.kill_switch_key, False)
        ):
            return TrustPolicyDecision(
                action=normalized_action,
                action_class=rule.action_class,
                outcome=TrustDecisionOutcome.DENY,
                status=423,
                code="AI_KILL_SWITCH_ACTIVE",
                message="Action is disabled by kill switch",
                details={"kill_switch": rule.kill_switch_key},
                requires_two_person=rule.requires_two_person,
            )

        if rule.policy_enabled_key and not self._as_bool(
            self._setting_resolver(tenant_id, "security", rule.policy_enabled_key, False)
        ):
            return TrustPolicyDecision(
                action=normalized_action,
                action_class=rule.action_class,
                outcome=TrustDecisionOutcome.DENY,
                status=403,
                code="AI_TRUST_POLICY_DENIED",
                message="Action policy is not enabled",
                details={"required_setting": rule.policy_enabled_key},
                requires_two_person=rule.requires_two_person,
            )

        if current_tier.level < rule.min_tier.level:
            return TrustPolicyDecision(
                action=normalized_action,
                action_class=rule.action_class,
                outcome=TrustDecisionOutcome.DENY,
                status=403,
                code="AI_TRUST_TIER_TOO_LOW",
                message="Action requires a higher trust tier",
                details={
                    "required_tier": rule.min_tier.value,
                    "resolved_tier": current_tier.value,
                },
                requires_two_person=rule.requires_two_person,
            )

        rollout_denial = self._evaluate_rollout_stage(
            tenant_id=tenant_id,
            action=normalized_action,
            action_class=rule.action_class,
            requires_two_person=rule.requires_two_person,
        )
        if rollout_denial is not None:
            return rollout_denial

        if rule.allowlist_key:
            chat_id = str(ctx.get("chat_id") or "").strip()
            allowlisted = self._coerce_allowlist(
                self._setting_resolver(tenant_id, "security", rule.allowlist_key, [])
            )
            if not chat_id or chat_id not in allowlisted:
                return TrustPolicyDecision(
                    action=normalized_action,
                    action_class=rule.action_class,
                    outcome=TrustDecisionOutcome.DENY,
                    status=403,
                    code="AI_MESSAGING_CHAT_NOT_ALLOWLISTED",
                    message="Chat is not allowlisted for this action",
                    details={"chat_id": chat_id, "allowlist_key": rule.allowlist_key},
                    requires_two_person=rule.requires_two_person,
                )

        for flag in rule.required_context_flags:
            if not self._as_bool(ctx.get(flag)):
                return TrustPolicyDecision(
                    action=normalized_action,
                    action_class=rule.action_class,
                    outcome=TrustDecisionOutcome.DENY,
                    status=409,
                    code="AI_TRUST_POLICY_GUARD_FAILED",
                    message="Required guardrail check failed",
                    details={"failed_guard": flag},
                    requires_two_person=rule.requires_two_person,
                )

        if rule.requires_approval:
            is_elevated = self._as_bool(ctx.get("explicitly_elevated"))
            if not is_elevated and rule.elevation_key:
                is_elevated = self._as_bool(
                    self._setting_resolver(tenant_id, "security", rule.elevation_key, False)
                )
            if not is_elevated:
                return TrustPolicyDecision(
                    action=normalized_action,
                    action_class=rule.action_class,
                    outcome=TrustDecisionOutcome.APPROVAL_REQUIRED,
                    status=409,
                    code="AI_APPROVAL_REQUIRED",
                    message="This action requires approval before apply",
                    details={"action": normalized_action},
                    requires_two_person=rule.requires_two_person,
                )

        return TrustPolicyDecision(
            action=normalized_action,
            action_class=rule.action_class,
            outcome=TrustDecisionOutcome.ALLOW,
            status=200,
            code="AI_OK",
            message="Allowed",
            details={"resolved_tier": current_tier.value},
            requires_two_person=rule.requires_two_person,
        )

    def _evaluate_rollout_stage(
        self,
        *,
        tenant_id: str | None,
        action: str,
        action_class: TrustActionClass,
        requires_two_person: bool,
    ) -> TrustPolicyDecision | None:
        stage_key: str | None = None
        canary_key: str | None = None
        if action.startswith("messaging."):
            stage_key = "messaging_rollout_stage"
            canary_key = "messaging_canary_enabled"
        elif action.startswith("automerge."):
            stage_key = "automerge_rollout_stage"
            canary_key = "automerge_canary_enabled"

        if stage_key is None or canary_key is None:
            return None

        stage_raw = (
            str(self._setting_resolver(tenant_id, "security", stage_key, "general") or "general")
            .strip()
            .lower()
        )
        stage = stage_raw if stage_raw in {"disabled", "canary", "general"} else "general"

        if stage == "disabled":
            return TrustPolicyDecision(
                action=action,
                action_class=action_class,
                outcome=TrustDecisionOutcome.DENY,
                status=403,
                code="AI_ROLLOUT_STAGE_BLOCKED",
                message="Action is disabled for this tenant rollout stage",
                details={"rollout_stage": stage, "rollout_key": stage_key},
                requires_two_person=requires_two_person,
            )
        if stage == "canary":
            canary_enabled = self._as_bool(
                self._setting_resolver(tenant_id, "security", canary_key, False)
            )
            if not canary_enabled:
                return TrustPolicyDecision(
                    action=action,
                    action_class=action_class,
                    outcome=TrustDecisionOutcome.DENY,
                    status=403,
                    code="AI_ROLLOUT_STAGE_BLOCKED",
                    message="Action is not enabled for tenant canary rollout",
                    details={"rollout_stage": stage, "required_setting": canary_key},
                    requires_two_person=requires_two_person,
                )
        return None

    @staticmethod
    def _as_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, int | float):
            return bool(value)
        if isinstance(value, str):
            normalized = value.strip().lower()
            return normalized in {"1", "true", "yes", "on"}
        return False

    @staticmethod
    def _coerce_allowlist(raw: Any) -> set[str]:
        if isinstance(raw, list):
            return {str(item).strip() for item in raw if str(item).strip()}
        if isinstance(raw, str):
            text = raw.strip()
            if not text:
                return set()
            if text.startswith("[") and text.endswith("]"):
                try:
                    parsed = json.loads(text)
                except Exception:
                    parsed = None
                if isinstance(parsed, list):
                    return {str(item).strip() for item in parsed if str(item).strip()}
            return {piece.strip() for piece in text.split(",") if piece.strip()}
        return set()
