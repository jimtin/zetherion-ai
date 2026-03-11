"""Minimal Clerk discovery client for public auth metadata."""

from __future__ import annotations

from typing import Any

import httpx

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.skills.clerk.client")

DEFAULT_TIMEOUT = 20.0


class ClerkMetadataError(Exception):
    """Raised when Clerk metadata cannot be loaded."""


class ClerkMetadataClient:
    """Read-only client for Clerk public discovery endpoints."""

    def __init__(self, *, timeout: float = DEFAULT_TIMEOUT) -> None:
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(timeout=self._timeout)
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _get_json(self, url: str) -> dict[str, Any]:
        client = await self._get_client()
        try:
            response = await client.get(url)
        except httpx.RequestError as exc:
            log.error("clerk_metadata_request_failed", url=url, error=str(exc))
            raise ClerkMetadataError(f"Request failed: {exc}") from exc
        if response.status_code >= 400:
            raise ClerkMetadataError(f"Metadata request failed with HTTP {response.status_code}")
        try:
            payload = response.json()
        except Exception as exc:  # pragma: no cover - defensive
            raise ClerkMetadataError("Failed to decode Clerk metadata response") from exc
        if not isinstance(payload, dict):
            raise ClerkMetadataError("Unexpected Clerk metadata response format")
        return payload

    async def get_jwks(self, jwks_url: str) -> dict[str, Any]:
        return await self._get_json(jwks_url)

    async def get_openid_configuration(self, issuer: str) -> dict[str, Any]:
        issuer_base = issuer.rstrip("/")
        return await self._get_json(f"{issuer_base}/.well-known/openid-configuration")
