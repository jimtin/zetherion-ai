"""Minimal async Vercel API client for brokered read-only metadata."""

from __future__ import annotations

from typing import Any

import httpx

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.skills.vercel.client")

VERCEL_API_BASE = "https://api.vercel.com"
DEFAULT_TIMEOUT = 30.0


class VercelAPIError(Exception):
    """Base exception for Vercel API failures."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        response: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.response = response or {}


class VercelAuthError(VercelAPIError):
    """Authentication failed."""


class VercelClient:
    """Async client for a small subset of the Vercel REST API."""

    def __init__(
        self,
        token: str,
        *,
        base_url: str = VERCEL_API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/json",
                },
            )
        return self._client

    async def close(self) -> None:
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    async def _request(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        client = await self._get_client()
        try:
            response = await client.get(path, params=params)
        except httpx.RequestError as exc:
            log.error("vercel_request_failed", path=path, error=str(exc))
            raise VercelAPIError(f"Request failed: {exc}") from exc
        if response.status_code == 401:
            raise VercelAuthError("Authentication failed", status_code=401)
        if response.status_code >= 400:
            try:
                payload = response.json()
            except Exception:
                payload = {"message": response.text}
            message = (
                str(payload.get("error", {}).get("message") or "")
                or str(payload.get("message") or "")
                or response.text
            )
            raise VercelAPIError(
                message,
                status_code=response.status_code,
                response=payload if isinstance(payload, dict) else {"message": response.text},
            )
        try:
            payload = response.json()
        except Exception as exc:  # pragma: no cover - defensive
            raise VercelAPIError("Failed to decode Vercel response") from exc
        if not isinstance(payload, dict):
            raise VercelAPIError("Unexpected Vercel response format")
        return payload

    @staticmethod
    def _params(team_id: str | None = None) -> dict[str, Any]:
        params: dict[str, Any] = {}
        if team_id and team_id.strip():
            params["teamId"] = team_id.strip()
        return params

    async def get_project(self, project_ref: str, *, team_id: str | None = None) -> dict[str, Any]:
        project = str(project_ref or "").strip()
        if not project:
            raise ValueError("project_ref is required")
        return await self._request(f"/v9/projects/{project}", params=self._params(team_id))

    async def list_deployments(
        self,
        *,
        project_id: str | None = None,
        project_name: str | None = None,
        team_id: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        params = self._params(team_id)
        params["limit"] = max(1, min(limit, 20))
        if project_id and project_id.strip():
            params["projectId"] = project_id.strip()
        elif project_name and project_name.strip():
            params["projectName"] = project_name.strip()
        payload = await self._request("/v6/deployments", params=params)
        deployments = payload.get("deployments", [])
        if not isinstance(deployments, list):
            return []
        return [dict(item) for item in deployments if isinstance(item, dict)]

    async def list_domains(
        self,
        project_ref: str,
        *,
        team_id: str | None = None,
    ) -> list[dict[str, Any]]:
        payload = await self._request(
            f"/v9/projects/{project_ref.strip()}/domains",
            params=self._params(team_id),
        )
        domains = payload.get("domains", [])
        if not isinstance(domains, list):
            return []
        return [dict(item) for item in domains if isinstance(item, dict)]

    async def list_env_vars(
        self,
        project_ref: str,
        *,
        team_id: str | None = None,
    ) -> list[dict[str, Any]]:
        params = self._params(team_id)
        params["decrypt"] = "false"
        payload = await self._request(
            f"/v9/projects/{project_ref.strip()}/env",
            params=params,
        )
        envs = payload.get("envs", [])
        if not isinstance(envs, list):
            return []
        return [dict(item) for item in envs if isinstance(item, dict)]
