"""HTTP client for Zetherion skills API (internal control-plane)."""

from __future__ import annotations

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
        timeout_seconds: float = 20.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._api_secret = api_secret
        self._timeout = aiohttp.ClientTimeout(total=timeout_seconds)
        self._session: aiohttp.ClientSession | None = None

    async def start(self) -> None:
        if self._session is None:
            self._session = aiohttp.ClientSession(timeout=self._timeout)

    async def close(self) -> None:
        if self._session is not None:
            await self._session.close()
            self._session = None

    @property
    def session(self) -> aiohttp.ClientSession:
        if self._session is None:
            raise RuntimeError("SkillsClient session is not started")
        return self._session

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self._base_url}{path}"

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
        headers = {
            "Content-Type": "application/json",
            "X-API-Secret": self._api_secret,
        }
        async with self.session.post(self._url("/handle"), headers=headers, json=body) as response:
            try:
                payload: Any = await response.json()
            except Exception:
                payload = await response.text()
            return response.status, payload
