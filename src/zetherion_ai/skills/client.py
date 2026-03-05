"""Async client for communicating with the skills service.

The client is used by the bot container to communicate with the skills
service over the internal Docker network. It handles authentication,
request serialization, and error handling.
"""

import base64
import hashlib
import hmac
import json
from typing import Any

import httpx

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    HeartbeatAction,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
)

log = get_logger("zetherion_ai.skills.client")


class SkillsClientError(Exception):
    """Base exception for skills client errors."""

    pass


class SkillsConnectionError(SkillsClientError):
    """Raised when unable to connect to skills service."""

    pass


class SkillsAuthError(SkillsClientError):
    """Raised when authentication fails."""

    pass


class SkillsClient:
    """Async client for the skills service.

    Provides methods to:
    - Handle skill requests
    - Trigger heartbeat cycles
    - List available skills
    - Check service health
    """

    def __init__(
        self,
        base_url: str = "http://zetherion_ai-skills:8080",
        api_secret: str | None = None,
        actor_signing_secret: str | None = None,
        timeout: float = 30.0,
    ):
        """Initialize the skills client.

        Args:
            base_url: Base URL of the skills service.
            api_secret: Shared secret for authentication.
            timeout: Request timeout in seconds.
        """
        self._base_url = base_url.rstrip("/")
        self._api_secret = api_secret
        self._actor_signing_secret = (actor_signing_secret or api_secret or "").strip()
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

        log.info("skills_client_initialized", base_url=self._base_url)

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client.

        Returns:
            The async HTTP client.
        """
        if self._client is None or self._client.is_closed:
            headers = {}
            if self._api_secret:
                headers["X-API-Secret"] = self._api_secret
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                headers=headers,
                timeout=self._timeout,
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None
            log.debug("skills_client_closed")

    def _build_admin_actor_headers(self, actor: dict[str, Any]) -> dict[str, str]:
        """Build signed admin actor envelope headers for tenant-admin endpoints."""
        if not self._actor_signing_secret:
            raise SkillsClientError("Admin actor signing secret is not configured")
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
        """Call a Skills tenant-admin route with signed actor context."""
        try:
            client = await self._get_client()
            headers = self._build_admin_actor_headers(actor)
            response = await client.request(
                method.upper(),
                path,
                headers=headers,
                json=json_body,
                params=query,
            )
            try:
                payload: Any = response.json()
            except Exception:
                payload = response.text
            return response.status_code, payload
        except httpx.ConnectError as e:
            log.error("skills_admin_connection_failed", error=str(e), method=method, path=path)
            raise SkillsConnectionError(f"Unable to connect to skills service: {e}") from e
        except httpx.RequestError as e:
            log.error("skills_admin_request_failed", error=str(e), method=method, path=path)
            raise SkillsClientError(f"Admin request failed: {e}") from e

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
        """Call a typed tenant-admin Skills route with signed actor context."""
        path_suffix = subpath if subpath.startswith("/") else f"/{subpath}"
        return await self.request_admin_json(
            method=method,
            path=f"/admin/tenants/{tenant_id}{path_suffix}",
            actor=actor,
            json_body=json_body,
            query=query,
        )

    async def health_check(self) -> bool:
        """Check if the skills service is healthy.

        Returns:
            True if the service is healthy.
        """
        try:
            client = await self._get_client()
            response = await client.get("/health")
            return response.status_code == 200
        except httpx.RequestError as e:
            log.warning("skills_health_check_failed", error=str(e))
            return False

    async def handle_request(self, request: SkillRequest) -> SkillResponse:
        """Send a request to the skills service.

        Args:
            request: The skill request.

        Returns:
            Response from the skill.

        Raises:
            SkillsConnectionError: If unable to connect.
            SkillsAuthError: If authentication fails.
            SkillsClientError: For other errors.
        """
        try:
            client = await self._get_client()
            response = await client.post(
                "/handle",
                json=request.to_dict(),
            )

            if response.status_code == 401:
                raise SkillsAuthError("Authentication failed")
            if response.status_code == 403:
                raise SkillsAuthError("Authorization failed")

            response.raise_for_status()

            data = response.json()
            return SkillResponse.from_dict(data)

        except httpx.ConnectError as e:
            log.error("skills_connection_failed", error=str(e))
            raise SkillsConnectionError(f"Unable to connect to skills service: {e}") from e
        except httpx.RequestError as e:
            log.error("skills_request_failed", error=str(e))
            raise SkillsClientError(f"Request failed: {e}") from e

    async def trigger_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Trigger a heartbeat cycle on the skills service.

        Args:
            user_ids: List of user IDs to check.

        Returns:
            List of actions from all skills.

        Raises:
            SkillsConnectionError: If unable to connect.
            SkillsClientError: For other errors.
        """
        try:
            client = await self._get_client()
            response = await client.post(
                "/heartbeat",
                json={"user_ids": user_ids},
            )

            if response.status_code == 401:
                raise SkillsAuthError("Authentication failed")

            response.raise_for_status()

            data = response.json()
            return [HeartbeatAction.from_dict(a) for a in data.get("actions", [])]

        except httpx.ConnectError as e:
            log.error("skills_heartbeat_connection_failed", error=str(e))
            raise SkillsConnectionError(f"Unable to connect to skills service: {e}") from e
        except httpx.RequestError as e:
            log.error("skills_heartbeat_failed", error=str(e))
            raise SkillsClientError(f"Heartbeat failed: {e}") from e

    async def list_skills(self) -> list[SkillMetadata]:
        """List all available skills.

        Returns:
            List of skill metadata.

        Raises:
            SkillsConnectionError: If unable to connect.
            SkillsClientError: For other errors.
        """
        try:
            client = await self._get_client()
            response = await client.get("/skills")

            if response.status_code == 401:
                raise SkillsAuthError("Authentication failed")

            response.raise_for_status()

            data = response.json()
            return [SkillMetadata.from_dict(s) for s in data.get("skills", [])]

        except httpx.ConnectError as e:
            log.error("skills_list_connection_failed", error=str(e))
            raise SkillsConnectionError(f"Unable to connect to skills service: {e}") from e
        except httpx.RequestError as e:
            log.error("skills_list_failed", error=str(e))
            raise SkillsClientError(f"List skills failed: {e}") from e

    async def get_skill(self, name: str) -> SkillMetadata | None:
        """Get metadata for a specific skill.

        Args:
            name: The skill name.

        Returns:
            Skill metadata, or None if not found.

        Raises:
            SkillsConnectionError: If unable to connect.
            SkillsClientError: For other errors.
        """
        try:
            client = await self._get_client()
            response = await client.get(f"/skills/{name}")

            if response.status_code == 404:
                return None
            if response.status_code == 401:
                raise SkillsAuthError("Authentication failed")

            response.raise_for_status()

            data = response.json()
            return SkillMetadata.from_dict(data)

        except httpx.ConnectError as e:
            log.error("skills_get_connection_failed", error=str(e))
            raise SkillsConnectionError(f"Unable to connect to skills service: {e}") from e
        except httpx.RequestError as e:
            log.error("skills_get_failed", error=str(e))
            raise SkillsClientError(f"Get skill failed: {e}") from e

    async def get_status(self) -> dict[str, Any]:
        """Get the status of the skills service.

        Returns:
            Dictionary with service status.

        Raises:
            SkillsConnectionError: If unable to connect.
            SkillsClientError: For other errors.
        """
        try:
            client = await self._get_client()
            response = await client.get("/status")

            if response.status_code == 401:
                raise SkillsAuthError("Authentication failed")

            response.raise_for_status()

            result: dict[str, Any] = response.json()
            return result

        except httpx.ConnectError as e:
            log.error("skills_status_connection_failed", error=str(e))
            raise SkillsConnectionError(f"Unable to connect to skills service: {e}") from e
        except httpx.RequestError as e:
            log.error("skills_status_failed", error=str(e))
            raise SkillsClientError(f"Get status failed: {e}") from e

    async def get_prompt_fragments(self, user_id: str) -> list[str]:
        """Get system prompt fragments from all skills.

        Args:
            user_id: The user ID for personalization.

        Returns:
            List of prompt fragments.

        Raises:
            SkillsConnectionError: If unable to connect.
            SkillsClientError: For other errors.
        """
        try:
            client = await self._get_client()
            response = await client.get(
                "/prompt-fragments",
                params={"user_id": user_id},
            )

            if response.status_code == 401:
                raise SkillsAuthError("Authentication failed")

            response.raise_for_status()

            data = response.json()
            fragments: list[str] = data.get("fragments", [])
            return fragments

        except httpx.ConnectError as e:
            log.error("skills_prompt_connection_failed", error=str(e))
            raise SkillsConnectionError(f"Unable to connect to skills service: {e}") from e
        except httpx.RequestError as e:
            log.error("skills_prompt_failed", error=str(e))
            raise SkillsClientError(f"Get prompt fragments failed: {e}") from e

    async def put_setting(
        self,
        *,
        namespace: str,
        key: str,
        value: Any,
        changed_by: int,
        data_type: str = "string",
    ) -> None:
        """Create/update a runtime setting via the skills service API."""
        try:
            client = await self._get_client()
            response = await client.put(
                f"/settings/{namespace}/{key}",
                json={
                    "value": value,
                    "changed_by": changed_by,
                    "data_type": data_type,
                },
            )

            if response.status_code == 401:
                raise SkillsAuthError("Authentication failed")
            if response.status_code == 403:
                raise SkillsAuthError("Authorization failed")

            response.raise_for_status()
        except httpx.ConnectError as e:
            log.error("skills_setting_connection_failed", error=str(e))
            raise SkillsConnectionError(f"Unable to connect to skills service: {e}") from e
        except httpx.RequestError as e:
            log.error("skills_setting_failed", error=str(e))
            raise SkillsClientError(f"Update setting failed: {e}") from e

    async def put_secret(
        self,
        *,
        name: str,
        value: str,
        changed_by: int,
        description: str | None = None,
    ) -> None:
        """Create/update an encrypted secret via the skills service API."""
        try:
            client = await self._get_client()
            payload: dict[str, Any] = {
                "value": value,
                "changed_by": changed_by,
            }
            if description:
                payload["description"] = description
            response = await client.put(f"/secrets/{name}", json=payload)

            if response.status_code == 401:
                raise SkillsAuthError("Authentication failed")
            if response.status_code == 403:
                raise SkillsAuthError("Authorization failed")

            response.raise_for_status()
        except httpx.ConnectError as e:
            log.error("skills_secret_connection_failed", error=str(e))
            raise SkillsConnectionError(f"Unable to connect to skills service: {e}") from e
        except httpx.RequestError as e:
            log.error("skills_secret_failed", error=str(e))
            raise SkillsClientError(f"Update secret failed: {e}") from e

    async def emit_announcement_event(
        self,
        *,
        source: str,
        category: str,
        target_user_id: int | str,
        title: str,
        body: str,
        severity: str = "normal",
        tenant_id: str | None = None,
        payload: dict[str, Any] | None = None,
        fingerprint: str | None = None,
        idempotency_key: str | None = None,
        occurred_at: str | None = None,
        channel: str = "discord_dm",
        dedupe_window_minutes: int = 10,
        state: str = "accepted",
    ) -> dict[str, Any]:
        """Emit an announcement event through the internal skills API."""
        request_payload: dict[str, Any] = {
            "source": source,
            "category": category,
            "severity": severity,
            "target_user_id": target_user_id,
            "title": title,
            "body": body,
            "payload": payload or {},
            "channel": channel,
            "dedupe_window_minutes": dedupe_window_minutes,
            "state": state,
        }
        if tenant_id:
            request_payload["tenant_id"] = tenant_id
        if fingerprint:
            request_payload["fingerprint"] = fingerprint
        if idempotency_key:
            request_payload["idempotency_key"] = idempotency_key
        if occurred_at:
            request_payload["occurred_at"] = occurred_at

        try:
            client = await self._get_client()
            response = await client.post("/announcements/events", json=request_payload)

            if response.status_code == 401:
                raise SkillsAuthError("Authentication failed")
            if response.status_code == 403:
                raise SkillsAuthError("Authorization failed")

            response.raise_for_status()
            result: dict[str, Any] = response.json()
            return result
        except httpx.ConnectError as e:
            log.error("skills_announcement_connection_failed", error=str(e))
            raise SkillsConnectionError(f"Unable to connect to skills service: {e}") from e
        except httpx.RequestError as e:
            log.error("skills_announcement_emit_failed", error=str(e))
            raise SkillsClientError(f"Emit announcement failed: {e}") from e


async def create_skills_client(
    base_url: str | None = None,
    api_secret: str | None = None,
) -> SkillsClient:
    """Create and configure a skills client.

    Args:
        base_url: Optional custom base URL.
        api_secret: Optional API secret.

    Returns:
        Configured skills client.
    """
    from zetherion_ai.config import get_settings

    settings = get_settings()

    url = base_url or str(
        getattr(settings, "skills_service_url", "http://zetherion_ai-skills:8080")
    )
    secret = api_secret or getattr(settings, "skills_api_secret", None)
    if secret and hasattr(secret, "get_secret_value"):
        secret = secret.get_secret_value()

    return SkillsClient(base_url=url, api_secret=secret)
