"""Announcement domain storage and models."""

from zetherion_ai.announcements.discord_adapter import DiscordDMChannelAdapter
from zetherion_ai.announcements.dispatcher import (
    AnnouncementChannelAdapter,
    AnnouncementChannelRegistry,
    AnnouncementDispatcher,
    AnnouncementDispatchError,
)
from zetherion_ai.announcements.email_adapter import AnnouncementEmailSender, EmailChannelAdapter
from zetherion_ai.announcements.policy import (
    AnnouncementPolicyDecision,
    AnnouncementPolicyEngine,
    ResolvedAnnouncementPreferences,
)
from zetherion_ai.announcements.storage import (
    AnnouncementDelivery,
    AnnouncementEvent,
    AnnouncementEventInput,
    AnnouncementPreferencePatch,
    AnnouncementReceipt,
    AnnouncementRecipient,
    AnnouncementRepository,
    AnnouncementSeverity,
    AnnouncementSuppressionState,
    AnnouncementUserPreferences,
    resolve_announcement_recipient,
)
from zetherion_ai.announcements.webhook_adapter import WebhookChannelAdapter

__all__ = [
    "AnnouncementDelivery",
    "AnnouncementEvent",
    "AnnouncementEventInput",
    "AnnouncementChannelAdapter",
    "AnnouncementChannelRegistry",
    "AnnouncementDispatchError",
    "AnnouncementDispatcher",
    "AnnouncementEmailSender",
    "DiscordDMChannelAdapter",
    "EmailChannelAdapter",
    "AnnouncementPolicyDecision",
    "AnnouncementPolicyEngine",
    "AnnouncementRecipient",
    "AnnouncementPreferencePatch",
    "AnnouncementReceipt",
    "AnnouncementRepository",
    "AnnouncementSeverity",
    "AnnouncementSuppressionState",
    "AnnouncementUserPreferences",
    "ResolvedAnnouncementPreferences",
    "WebhookChannelAdapter",
    "resolve_announcement_recipient",
]
