"""Health checking for post-update validation."""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("updater_sidecar.health_checker")


@dataclass
class HealthCheckConfig:
    """Configuration for health checking."""

    retries: int = 6
    delay_seconds: int = 10
    timeout_seconds: int = 10


async def check_service_health(
    url: str,
    config: HealthCheckConfig | None = None,
) -> bool:
    """Check a service health endpoint, retrying on failure.

    Returns True if the service is healthy within the retry window.
    """
    cfg = config or HealthCheckConfig()

    for attempt in range(cfg.retries):
        try:
            async with httpx.AsyncClient(timeout=cfg.timeout_seconds) as client:
                resp = await client.get(url)
            if resp.status_code == 200:
                log.info(
                    "Health check passed: %s (attempt %d)", url, attempt + 1
                )
                return True
            log.warning(
                "Health check returned %d: %s (attempt %d)",
                resp.status_code,
                url,
                attempt + 1,
            )
        except httpx.RequestError as exc:
            log.warning(
                "Health check failed: %s (attempt %d): %s",
                url,
                attempt + 1,
                exc,
            )

        if attempt < cfg.retries - 1:
            await asyncio.sleep(cfg.delay_seconds)

    log.error("Health check exhausted retries: %s", url)
    return False


async def check_all_services(
    urls: list[str],
    config: HealthCheckConfig | None = None,
) -> bool:
    """Check all service health endpoints.

    Returns True only if ALL services are healthy.
    """
    for url in urls:
        if not await check_service_health(url, config):
            return False
    return True
