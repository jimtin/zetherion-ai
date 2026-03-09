"""Tenant-facing notification service built on the announcement core."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from zetherion_ai.announcements import (
    AnnouncementChannelRegistry,
    AnnouncementEventInput,
    AnnouncementPolicyDecision,
    AnnouncementPolicyEngine,
    AnnouncementRecipient,
    AnnouncementRepository,
    AnnouncementSeverity,
)

if TYPE_CHECKING:
    from zetherion_ai.admin import TenantAdminManager
    from zetherion_ai.api.tenant import TenantManager

_TEMPLATE_PATTERN = re.compile(r"{(?P<key>[a-zA-Z0-9_.]+)}")


class TenantNotificationService:
    """Coordinate tenant subscriptions and announcement event emission."""

    def __init__(
        self,
        *,
        tenant_manager: TenantManager,
        announcement_repository: AnnouncementRepository,
        announcement_policy_engine: AnnouncementPolicyEngine,
        channel_registry: AnnouncementChannelRegistry,
        tenant_admin_manager: TenantAdminManager | None = None,
    ) -> None:
        self._tenant_manager = tenant_manager
        self._announcement_repository = announcement_repository
        self._announcement_policy_engine = announcement_policy_engine
        self._channel_registry = channel_registry
        self._tenant_admin_manager = tenant_admin_manager

    async def list_channels(self, tenant_id: str) -> list[dict[str, Any]]:
        """List public tenant notification channels from the shared registry."""
        email_accounts: list[dict[str, Any]] = []
        if self._tenant_admin_manager is not None:
            email_accounts = await self._tenant_admin_manager.list_email_accounts(
                tenant_id=tenant_id,
                provider="google",
            )

        channels: list[dict[str, Any]] = []
        for definition in self._channel_registry.definitions(public_only=True):
            status = "available"
            metadata: dict[str, Any] = {}
            if definition.channel == "email":
                connected = [
                    account
                    for account in email_accounts
                    if str(account.get("status") or "").strip().lower() in {"connected", "degraded"}
                ]
                if not connected:
                    status = "unconfigured"
                    metadata["reason"] = "no_connected_tenant_mailbox"
                else:
                    metadata["account_ids"] = [
                        str(account.get("account_id"))
                        for account in connected
                        if account.get("account_id")
                    ]
                    metadata["accounts"] = [
                        {
                            "account_id": str(account.get("account_id")),
                            "email_address": str(account.get("email_address") or ""),
                            "status": str(account.get("status") or ""),
                        }
                        for account in connected
                    ]
            channels.append(
                {
                    "channel_id": definition.channel,
                    "display_name": definition.display_name,
                    "description": definition.description,
                    "config_fields": list(definition.config_fields),
                    "status": status,
                    "metadata": metadata,
                }
            )
        return channels

    async def publish_event(
        self,
        *,
        tenant_id: str,
        source_app: str,
        event_type: str,
        severity: str,
        title: str,
        body: str,
        payload: dict[str, Any],
        occurred_at: datetime | None,
        dedupe_key: str | None,
    ) -> dict[str, Any]:
        """Publish one tenant event to all matching subscriptions."""
        subscriptions = await self._tenant_manager.match_notification_subscriptions(
            tenant_id,
            source_app=source_app,
            event_type=event_type,
        )
        deliveries: list[dict[str, Any]] = []
        for subscription in subscriptions:
            deliveries.append(
                await self._emit_subscription_event(
                    tenant_id=tenant_id,
                    subscription=subscription,
                    source_app=source_app,
                    event_type=event_type,
                    severity=severity,
                    title=title,
                    body=body,
                    payload=payload,
                    occurred_at=occurred_at,
                    dedupe_key=dedupe_key,
                )
            )
        return {
            "matched_subscriptions": len(subscriptions),
            "deliveries": deliveries,
        }

    def validate_subscription(
        self,
        *,
        channel_id: str,
        event_types: list[str],
        channel_config: dict[str, Any],
    ) -> dict[str, Any]:
        """Validate and normalize one subscription payload."""
        normalized_channel = str(channel_id or "").strip().lower()
        if not normalized_channel:
            raise ValueError("channel_id is required")
        if self._channel_registry.get_definition(normalized_channel) is None:
            raise ValueError("Unsupported channel_id")

        cleaned_event_types = [str(item).strip() for item in event_types if str(item).strip()]
        cleaned_event_types = list(dict.fromkeys(cleaned_event_types))
        if not cleaned_event_types:
            raise ValueError("event_types must contain at least one event type")

        normalized_config = channel_config if isinstance(channel_config, dict) else {}
        if normalized_channel == "webhook":
            webhook_url = str(normalized_config.get("webhook_url") or "").strip()
            if not webhook_url:
                raise ValueError("channel_config.webhook_url is required for webhook")
            return {
                "channel_id": normalized_channel,
                "event_types": cleaned_event_types,
                "channel_config": {
                    "webhook_url": webhook_url,
                },
            }
        if normalized_channel == "email":
            email = str(normalized_config.get("email") or "").strip().lower()
            if not email:
                raise ValueError("channel_config.email is required for email")
            account_id = str(normalized_config.get("account_id") or "").strip() or None
            normalized = {"email": email}
            if account_id:
                normalized["account_id"] = account_id
            return {
                "channel_id": normalized_channel,
                "event_types": cleaned_event_types,
                "channel_config": normalized,
            }

        raise ValueError("Unsupported channel_id")

    async def _emit_subscription_event(
        self,
        *,
        tenant_id: str,
        subscription: dict[str, Any],
        source_app: str,
        event_type: str,
        severity: str,
        title: str,
        body: str,
        payload: dict[str, Any],
        occurred_at: datetime | None,
        dedupe_key: str | None,
    ) -> dict[str, Any]:
        recipient = self._subscription_recipient(tenant_id=tenant_id, subscription=subscription)
        render_context = {
            "source_app": source_app,
            "event_type": event_type,
            "severity": severity,
            "title": title,
            "body": body,
            "payload": payload,
            "payload_json": json.dumps(payload, sort_keys=True),
        }
        template = (
            subscription.get("template") if isinstance(subscription.get("template"), dict) else {}
        )
        rendered_title = self._render_template(
            str(template.get("title") or ""),
            default=title,
            context=render_context,
        )
        rendered_body = self._render_template(
            str(template.get("body") or ""),
            default=body,
            context=render_context,
        )

        event = AnnouncementEventInput(
            source=f"tenant_notification:{source_app}",
            category=event_type,
            severity=AnnouncementSeverity.coerce(severity),
            title=rendered_title,
            body=rendered_body,
            target_user_id=0,
            tenant_id=tenant_id,
            recipient=recipient,
            payload={
                "source_app": source_app,
                "event_type": event_type,
                "subscription_id": subscription["subscription_id"],
                "data": payload,
            },
            fingerprint=(str(dedupe_key).strip() or None) if dedupe_key else None,
            idempotency_key=(
                f"{str(dedupe_key).strip()}:{subscription['subscription_id']}"
                if dedupe_key and str(dedupe_key).strip()
                else None
            ),
            occurred_at=occurred_at,
            state="accepted",
        )
        decision: AnnouncementPolicyDecision = (
            await self._announcement_policy_engine.evaluate_event(event)
        )
        event.state = decision.delivery_mode
        persisted = await self._announcement_repository.create_event(
            event,
            dedupe_window_minutes=10,
        )

        receipt_status = persisted.status
        scheduled_for = decision.scheduled_for
        reason_code = persisted.reason_code or decision.reason_code
        if persisted.status != "deduped":
            if decision.status == "scheduled" and decision.delivery_mode in {"immediate", "digest"}:
                when = decision.scheduled_for or datetime.now(UTC)
                await self._announcement_repository.create_delivery(
                    event_id=persisted.event_id,
                    channel=recipient.channel,
                    scheduled_for=when,
                )
                receipt_status = "scheduled"
                scheduled_for = when
            elif decision.status == "deferred":
                receipt_status = "deferred"
        return {
            "subscription_id": subscription["subscription_id"],
            "channel_id": recipient.channel,
            "receipt": {
                "status": receipt_status,
                "event_id": persisted.event_id,
                "scheduled_for": scheduled_for.isoformat() if scheduled_for else None,
                "reason_code": reason_code,
            },
        }

    @staticmethod
    def _subscription_recipient(
        *,
        tenant_id: str,
        subscription: dict[str, Any],
    ) -> AnnouncementRecipient:
        channel_id = str(subscription.get("channel_id") or "").strip().lower()
        config = (
            subscription.get("channel_config")
            if isinstance(subscription.get("channel_config"), dict)
            else {}
        )
        metadata = {
            "tenant_id": tenant_id,
            "subscription_id": subscription["subscription_id"],
        }
        if channel_id == "webhook":
            webhook_url = str(config.get("webhook_url") or "").strip()
            return AnnouncementRecipient(
                channel="webhook",
                routing_key=f"webhook:url:{webhook_url}",
                webhook_url=webhook_url,
                metadata=metadata,
            )
        if channel_id == "email":
            email = str(config.get("email") or "").strip().lower()
            account_id = str(config.get("account_id") or "").strip()
            if account_id:
                metadata["account_id"] = account_id
            return AnnouncementRecipient(
                channel="email",
                routing_key=f"email:{email}",
                email=email,
                metadata=metadata,
            )
        raise ValueError("Unsupported subscription channel")

    @staticmethod
    def _render_template(template: str, *, default: str, context: dict[str, Any]) -> str:
        raw_template = str(template or "").strip()
        if not raw_template:
            return default

        def _replace(match: re.Match[str]) -> str:
            key = match.group("key")
            value: Any = context
            for part in key.split("."):
                if isinstance(value, dict) and part in value:
                    value = value[part]
                else:
                    return ""
            if isinstance(value, dict | list):
                return json.dumps(value, sort_keys=True)
            return str(value)

        rendered = _TEMPLATE_PATTERN.sub(_replace, raw_template).strip()
        return rendered or default
