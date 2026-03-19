"""HTTP client for Zetherion public API upstream calls."""

from __future__ import annotations

import inspect
import ssl
from typing import Any, cast

import aiohttp


class PublicAPIClient:
    """Thin async client for Zetherion public API."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: float = 30.0,
        ssl_context: ssl.SSLContext | None = None,
    ) -> None:
        self._base_urls = self._parse_base_urls(base_url)
        self._base_url = self._base_urls[0]
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
            raise RuntimeError("PublicAPIClient session is not started")
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
        params: dict[str, Any] | None = None,
        data: Any = None,
    ) -> aiohttp.ClientResponse:
        last_error: aiohttp.ClientError | None = None
        for base_url in self._iter_base_urls():
            try:
                request_result = self.session.request(
                    method.upper(),
                    self._url(base_url, path),
                    headers=headers,
                    json=json_body,
                    params=params,
                    data=data,
                )
                response = (
                    await request_result if inspect.isawaitable(request_result) else request_result
                )
                self._base_url = base_url
                return cast(aiohttp.ClientResponse, response)
            except aiohttp.ClientError as exc:
                last_error = exc
        if last_error is not None:
            raise last_error
        raise RuntimeError("PublicAPIClient has no configured upstream base URL")

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
        response = await self._request_with_failover(
            method,
            path,
            headers=headers,
            json_body=json_body,
            params=params,
            data=data,
        )
        try:
            payload: Any
            try:
                payload = await response.json()
            except Exception:
                payload = await response.text()
            response_headers = dict(response.headers)
            return response.status, payload, response_headers
        finally:
            release = getattr(response, "release", None)
            if release is None:
                continue_response = getattr(response, "__aexit__", None)
                if continue_response is not None:
                    await continue_response(None, None, None)
            else:
                result = release()
                if inspect.isawaitable(result):
                    await result

    async def open_stream(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> aiohttp.ClientResponse:
        """Open streaming response; caller must close the response."""
        return await self._request_with_failover(
            method,
            path,
            headers=headers,
            json_body=json_body,
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
        response = await self._request_with_failover(
            method,
            path,
            headers=headers,
            params=params,
        )
        try:
            payload = await response.read()
            return response.status, payload, dict(response.headers)
        finally:
            release = getattr(response, "release", None)
            if release is None:
                continue_response = getattr(response, "__aexit__", None)
                if continue_response is not None:
                    await continue_response(None, None, None)
            else:
                result = release()
                if inspect.isawaitable(result):
                    await result

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
        limit: int | None = None,
        include_archived: bool = False,
    ) -> tuple[int, Any, dict[str, str]]:
        params: dict[str, Any] = {"include_archived": "true" if include_archived else "false"}
        if limit is not None:
            params["limit"] = int(limit)
        return await self.request_json(
            "GET",
            "/api/v1/documents",
            headers=headers,
            params=params,
        )

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
