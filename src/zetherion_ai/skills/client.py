"""Async client for communicating with the skills service.

The client is used by the bot container to communicate with the skills
service over the internal Docker network. It handles authentication,
request serialization, and error handling.
"""

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
