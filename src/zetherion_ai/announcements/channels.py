"""Shared announcement channel registration and tenant email sender helpers."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from zetherion_ai.announcements.discord_adapter import DiscordDMChannelAdapter
from zetherion_ai.announcements.dispatcher import (
    AnnouncementChannelDefinition,
    AnnouncementChannelRegistry,
    AnnouncementDispatchError,
)
from zetherion_ai.announcements.email_adapter import EmailChannelAdapter
from zetherion_ai.announcements.webhook_adapter import WebhookChannelAdapter
from zetherion_ai.logging import get_logger
from zetherion_ai.skills.gmail.client import GmailClient

if TYPE_CHECKING:
    import discord

    from zetherion_ai.admin import TenantAdminManager

log = get_logger("zetherion_ai.announcements.channels")

_GMAIL_SEND_SCOPES = frozenset(
    {
        "https://www.googleapis.com/auth/gmail.send",
        "https://mail.google.com/",
    }
)


class TenantGoogleAnnouncementEmailSender:
    """Send tenant notification email through one connected Gmail mailbox."""

    def __init__(self, tenant_admin_manager: TenantAdminManager) -> None:
        self._tenant_admin_manager = tenant_admin_manager

    async def send(
        self,
        *,
        to_address: str,
        subject: str,
        body: str,
        metadata: dict[str, Any],
    ) -> None:
        tenant_id = str(metadata.get("tenant_id") or "").strip()
        if not tenant_id:
            raise AnnouncementDispatchError(
                code="missing_tenant_id",
                detail="Notification email delivery requires tenant_id metadata",
                retryable=False,
            )

        account = await self._resolve_account(tenant_id=tenant_id, metadata=metadata)
        scopes = {str(scope).strip() for scope in account.get("scopes") or [] if str(scope).strip()}
        if scopes and scopes.isdisjoint(_GMAIL_SEND_SCOPES):
            raise AnnouncementDispatchError(
                code="email_sender_scope_missing",
                detail="Connected tenant email account is missing gmail.send scope",
                retryable=False,
            )

        access_token = str(account.get("access_token") or "").strip()
        if not access_token:
            raise AnnouncementDispatchError(
                code="email_sender_token_missing",
                detail="Resolved tenant email account has no access token",
                retryable=True,
            )

        client = GmailClient(access_token)
        try:
            await client.send_message(
                to=to_address,
                subject=subject,
                body=body,
            )
        except AnnouncementDispatchError:
            raise
        except Exception as exc:
            raise AnnouncementDispatchError(
                code="gmail_send_failed",
                detail=str(exc),
                retryable=True,
            ) from exc

        log.info(
            "announcement_email_sent_via_tenant_account",
            tenant_id=tenant_id,
            account_id=account.get("account_id"),
            to_address=to_address,
        )

    async def _resolve_account(self, *, tenant_id: str, metadata: dict[str, Any]) -> dict[str, Any]:
        requested_account_id = str(metadata.get("account_id") or "").strip()
        if requested_account_id:
            return await self._tenant_admin_manager._refresh_google_access_token_if_needed(
                tenant_id=tenant_id,
                account_id=requested_account_id,
            )

        accounts = await self._tenant_admin_manager.list_email_accounts(
            tenant_id=tenant_id,
            provider="google",
        )
        connected = [
            account
            for account in accounts
            if str(account.get("status") or "").strip().lower() in {"connected", "degraded"}
        ]
        if not connected:
            raise AnnouncementDispatchError(
                code="email_sender_account_missing",
                detail="Tenant has no connected Google mailbox for notification delivery",
                retryable=False,
            )

        account_id = str(connected[0].get("account_id") or "").strip()
        if not account_id:
            raise AnnouncementDispatchError(
                code="email_sender_account_invalid",
                detail="Tenant email account record is missing account_id",
                retryable=False,
            )

        return await self._tenant_admin_manager._refresh_google_access_token_if_needed(
            tenant_id=tenant_id,
            account_id=account_id,
        )


def build_announcement_channel_registry(
    *,
    discord_bot: discord.Client | None = None,
    tenant_admin_manager: TenantAdminManager | None = None,
) -> AnnouncementChannelRegistry:
    """Build the canonical channel registry used by runtime surfaces."""

    registry = AnnouncementChannelRegistry()
    if discord_bot is not None:
        registry.register(
            "discord_dm",
            DiscordDMChannelAdapter(discord_bot),
            definition=AnnouncementChannelDefinition(
                channel="discord_dm",
                display_name="Discord DM",
                description="Direct message to an allowed owner or Discord-bound recipient.",
                public_enabled=False,
                config_fields=("target_user_id",),
            ),
        )

    registry.register(
        "webhook",
        WebhookChannelAdapter(),
        definition=AnnouncementChannelDefinition(
            channel="webhook",
            display_name="Webhook",
            description="POST a structured notification payload to a tenant webhook endpoint.",
            public_enabled=True,
            config_fields=("webhook_url",),
        ),
    )

    if tenant_admin_manager is not None:
        registry.register(
            "email",
            EmailChannelAdapter(TenantGoogleAnnouncementEmailSender(tenant_admin_manager)),
            definition=AnnouncementChannelDefinition(
                channel="email",
                display_name="Email",
                description="Send a notification email via a tenant-connected Google mailbox.",
                public_enabled=True,
                config_fields=("email", "account_id"),
            ),
        )

    return registry
