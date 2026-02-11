"""Inbound telemetry receiver for the central instance.

Validates incoming reports via instance API keys (bcrypt-hashed),
stores them in PostgreSQL, and provides fleet-level aggregation.
"""

from __future__ import annotations

import secrets
from datetime import datetime
from typing import TYPE_CHECKING, Any

import bcrypt  # type: ignore[import-not-found]

from zetherion_ai.logging import get_logger
from zetherion_ai.telemetry.models import (
    InstanceRegistration,
    TelemetryConsent,
    TelemetryReport,
)

if TYPE_CHECKING:
    from zetherion_ai.telemetry.storage import TelemetryStorage

log = get_logger("zetherion_ai.telemetry.receiver")

# Key prefix for easy identification
_KEY_PREFIX = "zt_inst_"


class TelemetryReceiver:
    """Central-instance component that ingests telemetry from deployed agents."""

    def __init__(self, storage: TelemetryStorage) -> None:
        self._storage = storage
        # In-memory cache of instance_id → hashed key for fast validation
        self._key_cache: dict[str, str] = {}

    # ------------------------------------------------------------------
    # Instance registration
    # ------------------------------------------------------------------

    async def register_instance(
        self,
        instance_id: str,
        consent: TelemetryConsent | None = None,
    ) -> str:
        """Register a new reporting instance and return its API key.

        The raw key is returned exactly once; only the bcrypt hash is stored.
        """
        raw_key = f"{_KEY_PREFIX}{secrets.token_urlsafe(32)}"
        hashed = bcrypt.hashpw(raw_key.encode(), bcrypt.gensalt()).decode()

        registration = InstanceRegistration(
            instance_id=instance_id,
            api_key_hash=hashed,
            first_seen=datetime.now(),
            last_seen=datetime.now(),
            consent=consent or TelemetryConsent(),
        )
        await self._storage.register_instance(registration)
        self._key_cache[instance_id] = hashed

        log.info("telemetry_instance_registered", instance_id=instance_id)
        return raw_key

    # ------------------------------------------------------------------
    # Authentication
    # ------------------------------------------------------------------

    async def validate_key(self, instance_id: str, api_key: str) -> bool:
        """Validate an API key against the stored bcrypt hash."""
        hashed = self._key_cache.get(instance_id)
        if not hashed:
            reg = await self._storage.get_instance(instance_id)
            if reg is None:
                return False
            hashed = reg.api_key_hash
            self._key_cache[instance_id] = hashed

        return bool(bcrypt.checkpw(api_key.encode(), hashed.encode()))

    # ------------------------------------------------------------------
    # Ingestion
    # ------------------------------------------------------------------

    async def ingest(self, report: TelemetryReport, api_key: str) -> bool:
        """Validate and store an incoming telemetry report.

        Returns True if accepted, False if rejected.
        """
        if not await self.validate_key(report.instance_id, api_key):
            log.warning(
                "telemetry_auth_failed",
                instance_id=report.instance_id,
            )
            return False

        # Filter metrics to only include consented categories
        filtered_metrics: dict[str, dict[str, Any]] = {}
        for category, data in report.metrics.items():
            if report.consent.allows(category):
                filtered_metrics[category] = data
        report.metrics = filtered_metrics

        await self._storage.save_report(report)
        log.info(
            "telemetry_report_ingested",
            instance_id=report.instance_id,
            categories=list(filtered_metrics.keys()),
        )
        return True

    # ------------------------------------------------------------------
    # Deletion (GDPR-style right to erasure)
    # ------------------------------------------------------------------

    async def delete_instance(self, instance_id: str, api_key: str) -> bool:
        """Remove an instance and all its data (requires valid API key)."""
        if not await self.validate_key(instance_id, api_key):
            return False

        deleted = await self._storage.delete_instance(instance_id)
        self._key_cache.pop(instance_id, None)

        if deleted:
            log.info("telemetry_instance_deleted", instance_id=instance_id)
        return deleted

    # ------------------------------------------------------------------
    # Fleet queries
    # ------------------------------------------------------------------

    async def get_fleet_summary(self) -> dict[str, Any]:
        """Aggregate data across all reporting instances."""
        instances = await self._storage.list_instances()

        if not instances:
            return {
                "total_instances": 0,
                "versions": {},
                "last_report": None,
            }

        versions: dict[str, int] = {}
        latest_seen: datetime | None = None

        for inst in instances:
            ver = inst.get("current_version", "unknown")
            versions[ver] = versions.get(ver, 0) + 1
            last = inst.get("last_seen")
            if last and (latest_seen is None or last > latest_seen):
                latest_seen = last

        return {
            "total_instances": len(instances),
            "versions": versions,
            "last_report": latest_seen.isoformat() if latest_seen else None,
        }
