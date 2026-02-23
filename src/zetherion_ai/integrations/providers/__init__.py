"""Provider adapter interfaces and implementations."""

from zetherion_ai.integrations.providers.base import (
    CalendarProviderAdapter,
    EmailProviderAdapter,
    ProviderDestination,
    ProviderEvent,
    ProviderTask,
    TaskProviderAdapter,
)
from zetherion_ai.integrations.providers.google import GoogleProviderAdapter
from zetherion_ai.integrations.providers.outlook import OutlookProviderAdapter

__all__ = [
    "CalendarProviderAdapter",
    "EmailProviderAdapter",
    "ProviderDestination",
    "ProviderEvent",
    "ProviderTask",
    "TaskProviderAdapter",
    "GoogleProviderAdapter",
    "OutlookProviderAdapter",
]
