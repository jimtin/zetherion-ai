"""In-memory tenant-aware mutation rate limiting for CGS gateway."""

from __future__ import annotations

import time
from collections import defaultdict


class TenantMutationRateLimiter:
    """Simple per-tenant/per-family token bucket limiter."""

    def __init__(
        self,
        *,
        default_limit_per_minute: int = 30,
        family_limits_per_minute: dict[str, int] | None = None,
    ) -> None:
        self._default_limit = max(1, int(default_limit_per_minute))
        self._family_limits = {
            family.strip().lower(): max(1, int(limit))
            for family, limit in (family_limits_per_minute or {}).items()
            if family.strip()
        }
        self._buckets: dict[str, dict[str, float]] = defaultdict(dict)

    def _limit_for_family(self, family: str) -> int:
        return self._family_limits.get(family.strip().lower(), self._default_limit)

    def check(self, *, tenant_id: str, family: str) -> tuple[bool, int]:
        """Return (allowed, retry_after_seconds)."""
        tenant = tenant_id.strip()
        key = f"{tenant}:{family.strip().lower()}"
        limit = self._limit_for_family(family)
        now = time.monotonic()

        bucket = self._buckets.get(key)
        if not bucket:
            self._buckets[key] = {"tokens": float(limit - 1), "last_refill": now}
            return True, 0

        tokens = float(bucket.get("tokens", float(limit)))
        last_refill = float(bucket.get("last_refill", now))
        refill_rate = float(limit) / 60.0
        elapsed = max(0.0, now - last_refill)
        tokens = min(float(limit), tokens + elapsed * refill_rate)
        bucket["last_refill"] = now

        if tokens >= 1.0:
            bucket["tokens"] = tokens - 1.0
            return True, 0

        bucket["tokens"] = tokens
        retry_after = int(max(1.0, (1.0 - tokens) / refill_rate))
        return False, retry_after
