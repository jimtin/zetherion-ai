"""Provider adapter registry and capability tracking."""

from __future__ import annotations

from dataclasses import dataclass

from zetherion_ai.integrations.providers.base import (
    CalendarProviderAdapter,
    EmailProviderAdapter,
    TaskProviderAdapter,
)


@dataclass
class ProviderCapabilities:
    """Capabilities available for a provider."""

    email_read: bool = False
    email_write: bool = False
    task_read: bool = False
    task_write: bool = False
    calendar_read: bool = False
    calendar_write: bool = False
    two_way_sync: bool = False
    cross_calendar_conflicts: bool = False


@dataclass
class ProviderAdapters:
    """Adapter set for a provider."""

    email: EmailProviderAdapter | None = None
    task: TaskProviderAdapter | None = None
    calendar: CalendarProviderAdapter | None = None


class ProviderRegistry:
    """Runtime registry for provider adapters and capabilities."""

    def __init__(self) -> None:
        self._adapters: dict[str, ProviderAdapters] = {}
        self._capabilities: dict[str, ProviderCapabilities] = {}

    def register(
        self,
        provider: str,
        *,
        adapters: ProviderAdapters,
        capabilities: ProviderCapabilities,
    ) -> None:
        """Register adapters/capabilities for a provider."""
        self._adapters[provider] = adapters
        self._capabilities[provider] = capabilities

    def adapters(self, provider: str) -> ProviderAdapters | None:
        """Get adapter set for a provider."""
        return self._adapters.get(provider)

    def capabilities(self, provider: str) -> ProviderCapabilities | None:
        """Get capabilities for a provider."""
        return self._capabilities.get(provider)

    def list_providers(self) -> list[str]:
        """List registered providers."""
        return sorted(self._adapters)
