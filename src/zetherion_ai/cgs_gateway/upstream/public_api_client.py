"""HTTP client for Zetherion public API upstream calls."""

from __future__ import annotations

from typing import Any

import aiohttp


class PublicAPIClient:
    """Thin async client for Zetherion public API."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 30.0,
    ) -> None:
        self._base_url = base_url.rstrip("/")
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
            raise RuntimeError("PublicAPIClient session is not started")
        return self._session

    def _url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self._base_url}{path}"

    async def request_json(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
        params: dict[str, Any] | None = None,
        data: Any = None,
    ) -> tuple[int, Any, dict[str, str]]:
        """Send request and parse response as JSON when possible."""
        async with self.session.request(
            method.upper(),
            self._url(path),
            headers=headers,
            json=json_body,
            params=params,
            data=data,
        ) as response:
            payload: Any
            try:
                payload = await response.json()
            except Exception:
                payload = await response.text()
            response_headers = dict(response.headers)
            return response.status, payload, response_headers

    async def open_stream(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> aiohttp.ClientResponse:
        """Open streaming response; caller must close the response."""
        return await self.session.request(
            method.upper(),
            self._url(path),
            headers=headers,
            json=json_body,
        )

    async def request_raw(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        params: dict[str, Any] | None = None,
    ) -> tuple[int, bytes, dict[str, str]]:
        """Send request and return raw bytes body."""
        async with self.session.request(
            method.upper(),
            self._url(path),
            headers=headers,
            params=params,
        ) as response:
            payload = await response.read()
            return response.status, payload, dict(response.headers)

    async def create_document_upload(
        self,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> tuple[int, Any, dict[str, str]]:
        return await self.request_json(
            "POST",
            "/api/v1/documents/uploads",
            headers=headers,
            json_body=payload,
        )

    async def complete_document_upload_json(
        self,
        *,
        upload_id: str,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> tuple[int, Any, dict[str, str]]:
        return await self.request_json(
            "POST",
            f"/api/v1/documents/uploads/{upload_id}/complete",
            headers=headers,
            json_body=payload,
        )

    async def complete_document_upload_multipart(
        self,
        *,
        upload_id: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, Any, dict[str, str]]:
        return await self.request_json(
            "POST",
            f"/api/v1/documents/uploads/{upload_id}/complete",
            headers=headers,
            data=body,
        )

    async def list_documents(
        self,
        *,
        headers: dict[str, str],
    ) -> tuple[int, Any, dict[str, str]]:
        return await self.request_json("GET", "/api/v1/documents", headers=headers)

    async def get_document(
        self,
        *,
        document_id: str,
        headers: dict[str, str],
    ) -> tuple[int, Any, dict[str, str]]:
        return await self.request_json(
            "GET",
            f"/api/v1/documents/{document_id}",
            headers=headers,
        )

    async def get_document_binary(
        self,
        *,
        document_id: str,
        suffix: str,
        headers: dict[str, str],
    ) -> tuple[int, bytes, dict[str, str]]:
        return await self.request_raw(
            "GET",
            f"/api/v1/documents/{document_id}/{suffix}",
            headers=headers,
        )

    async def reindex_document(
        self,
        *,
        document_id: str,
        headers: dict[str, str],
    ) -> tuple[int, Any, dict[str, str]]:
        return await self.request_json(
            "POST",
            f"/api/v1/documents/{document_id}/index",
            headers=headers,
        )

    async def rag_query(
        self,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> tuple[int, Any, dict[str, str]]:
        return await self.request_json(
            "POST",
            "/api/v1/rag/query",
            headers=headers,
            json_body=payload,
        )

    async def list_model_providers(
        self,
        *,
        headers: dict[str, str],
    ) -> tuple[int, Any, dict[str, str]]:
        return await self.request_json(
            "GET",
            "/api/v1/models/providers",
            headers=headers,
        )

    async def create_release_marker(
        self,
        *,
        headers: dict[str, str],
        payload: dict[str, Any],
    ) -> tuple[int, Any, dict[str, str]]:
        return await self.request_json(
            "POST",
            "/api/v1/releases/markers",
            headers=headers,
            json_body=payload,
        )
