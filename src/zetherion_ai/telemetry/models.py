"""Data models for telemetry collection and fleet reporting.

All models are plain dataclasses with to_dict/from_dict for serialisation.
No message content, user IDs, or PII is ever included — only aggregate stats.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

# ------------------------------------------------------------------
# Consent
# ------------------------------------------------------------------

# Categories that an owner can individually opt into
VALID_CATEGORIES = frozenset({"performance", "usage", "cost", "health", "quality"})


@dataclass
class TelemetryConsent:
    """Per-category opt-in flags for telemetry sharing."""

    categories: set[str] = field(default_factory=set)

    def allows(self, category: str) -> bool:
        """Check whether a specific category is opted-in."""
        return category in self.categories

    def to_dict(self) -> dict[str, Any]:
        return {"categories": sorted(self.categories)}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryConsent:
        raw = set(data.get("categories", []))
        return cls(categories=raw & VALID_CATEGORIES)


# ------------------------------------------------------------------
# Telemetry Report (sent by deployed agents)
# ------------------------------------------------------------------


@dataclass
class TelemetryReport:
    """An anonymized telemetry report from a deployed instance."""

    instance_id: str
    timestamp: str  # ISO-8601
    version: str
    consent: TelemetryConsent
    metrics: dict[str, dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "timestamp": self.timestamp,
            "version": self.version,
            "consent": self.consent.to_dict(),
            "metrics": self.metrics,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TelemetryReport:
        return cls(
            instance_id=data["instance_id"],
            timestamp=data["timestamp"],
            version=data["version"],
            consent=TelemetryConsent.from_dict(data.get("consent", {})),
            metrics=data.get("metrics", {}),
        )


# ------------------------------------------------------------------
# Instance Registration (stored on central instance)
# ------------------------------------------------------------------


@dataclass
class InstanceRegistration:
    """Registration record for a reporting instance on the central server."""

    instance_id: str
    api_key_hash: str  # bcrypt hash of the issued key
    first_seen: datetime = field(default_factory=datetime.now)
    last_seen: datetime = field(default_factory=datetime.now)
    current_version: str = ""
    consent: TelemetryConsent = field(default_factory=TelemetryConsent)

    def to_dict(self) -> dict[str, Any]:
        return {
            "instance_id": self.instance_id,
            "api_key_hash": self.api_key_hash,
            "first_seen": self.first_seen.isoformat(),
            "last_seen": self.last_seen.isoformat(),
            "current_version": self.current_version,
            "consent": self.consent.to_dict(),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> InstanceRegistration:
        return cls(
            instance_id=data["instance_id"],
            api_key_hash=data.get("api_key_hash", ""),
            first_seen=datetime.fromisoformat(data["first_seen"])
            if "first_seen" in data
            else datetime.now(),
            last_seen=datetime.fromisoformat(data["last_seen"])
            if "last_seen" in data
            else datetime.now(),
            current_version=data.get("current_version", ""),
            consent=TelemetryConsent.from_dict(data.get("consent", {})),
        )


def generate_instance_id() -> str:
    """Generate an opaque instance identifier (UUID4)."""
    return str(uuid.uuid4())
