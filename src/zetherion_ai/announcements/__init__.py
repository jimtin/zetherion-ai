"""Announcement domain storage and models."""

from zetherion_ai.announcements.discord_adapter import DiscordDMChannelAdapter
from zetherion_ai.announcements.dispatcher import (
    AnnouncementChannelAdapter,
    AnnouncementDispatcher,
    AnnouncementDispatchError,
)
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
    AnnouncementRepository,
    AnnouncementSeverity,
    AnnouncementSuppressionState,
    AnnouncementUserPreferences,
)

__all__ = [
    "AnnouncementDelivery",
    "AnnouncementEvent",
    "AnnouncementEventInput",
    "AnnouncementChannelAdapter",
    "AnnouncementDispatchError",
    "AnnouncementDispatcher",
    "DiscordDMChannelAdapter",
    "AnnouncementPolicyDecision",
    "AnnouncementPolicyEngine",
    "AnnouncementPreferencePatch",
    "AnnouncementReceipt",
    "AnnouncementRepository",
    "AnnouncementSeverity",
    "AnnouncementSuppressionState",
    "AnnouncementUserPreferences",
    "ResolvedAnnouncementPreferences",
]
