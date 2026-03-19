"""HTTP client for Zetherion skills API (internal control-plane)."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import ssl
import uuid
from typing import Any

import aiohttp


class SkillsClient:
    """Client for calling Skills API /handle intents."""

    def __init__(
        self,
        *,
        base_url: str,
        api_secret: str,
        actor_signing_secret: str | None = None,
        timeout_seconds: float = 20.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._base_urls = self._parse_base_urls(base_url)
        self._base_url = self._base_urls[0]
        self._api_secret = api_secret
        self._actor_signing_secret = (actor_signing_secret or api_secret).strip()
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._ssl_context = ssl_context
        self._session: aiohttp.ClientSession | None = None

    @staticmethod
    def _parse_base_urls(base_url: str) -> list[str]:
        urls = [piece.strip().rstrip("/") for piece in base_url.split(",") if piece.strip()]
        return urls or [""]

    async def start(self) -> None:
        if self._session is None:
            connector = aiohttp.TCPConnector(ssl=self._ssl_context) if self._ssl_context else None
            self._session = aiohttp.ClientSession(timeout=self._timeout, connector=connector)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("SkillsClient session is not started")
        return self._session

    def _iter_base_urls(self) -> list[str]:
        preferred = self._base_url
        return [preferred, *[url for url in self._base_urls if url != preferred]]

    def _url(self, base_url: str, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{base_url}{path}"

    async def _request_with_failover(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        merged_headers = {
            "Content-Type": "application/json",
            "X-API-Secret": self._api_secret,
        }
        if headers:
            merged_headers.update(headers)

        last_error: aiohttp.ClientError | None = None
        for base_url in self._iter_base_urls():
            try:
                async with self.session.request(
                    method.upper(),
                    self._url(base_url, path),
                    headers=merged_headers,
                    json=json_body,
                    params=query,
                ) as response:
                    try:
                        payload: Any = await response.json()
                    except Exception:
                        payload = await response.text()
                    self._base_url = base_url
                    return response.status, payload
            except aiohttp.ClientError as exc:
                last_error = exc

        if last_error is not None:
            raise last_error
        raise RuntimeError("SkillsClient has no configured upstream base URL")

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        """Call any Skills API route and return status + payload."""
        return await self._request_with_failover(
            method,
            path,
            headers=headers,
            json_body=json_body,
            query=query,
        )

    def _build_actor_headers(self, actor: dict[str, Any]) -> dict[str, str]:
        if not self._actor_signing_secret:
            raise RuntimeError("Skills actor signing secret is not configured")
        canonical = json.dumps(actor, sort_keys=True, separators=(",", ":"))
        encoded_actor = (
            base64.urlsafe_b64encode(canonical.encode("utf-8")).decode("ascii").rstrip("=")
        )
        signature = hmac.new(
            self._actor_signing_secret.encode("utf-8"),
            encoded_actor.encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()
        return {
            "X-Admin-Actor": encoded_actor,
            "X-Admin-Signature": signature,
        }

    async def request_admin_json(
        self,
        method: str,
        path: str,
        *,
        actor: dict[str, Any],
        json_body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        """Call tenant-admin Skills routes with signed actor context."""
        headers = self._build_actor_headers(actor)
        return await self.request_json(
            method,
            path,
            headers=headers,
            json_body=json_body,
            query=query,
        )

    async def request_tenant_admin_json(
        self,
        method: str,
        *,
        tenant_id: str,
        subpath: str,
        actor: dict[str, Any],
        json_body: dict[str, Any] | None = None,
        query: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        """Call typed tenant-admin Skills routes with signed actor context."""
        path_suffix = subpath if subpath.startswith("/") else f"/{subpath}"
        return await self.request_admin_json(
            method,
            f"/admin/tenants/{tenant_id}{path_suffix}",
            actor=actor,
            json_body=json_body,
            query=query,
        )

    async def handle_intent(
        self,
        *,
        intent: str,
        context: dict[str, Any],
        user_id: str,
        message: str = "",
        request_id: str | None = None,
    ) -> tuple[int, Any]:
        """Call Skills API /handle for one intent."""
        body = {
            "id": request_id or str(uuid.uuid4()),
            "user_id": user_id,
            "intent": intent,
            "message": message,
            "context": context,
        }
        return await self.request_json("POST", "/handle", json_body=body)
