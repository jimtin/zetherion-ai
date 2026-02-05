"""Abstract base class for router backends."""

from typing import Protocol

from secureclaw.agent.router import RoutingDecision


class RouterBackend(Protocol):
    """Protocol defining the interface for router backends."""

    async def classify(self, message: str) -> RoutingDecision:
        """Classify a message and determine routing.

        Args:
            message: The user's message to classify.

        Returns:
            RoutingDecision with intent and routing info.
        """
        ...

    async def generate_simple_response(self, message: str) -> str:
        """Generate a response for simple queries.

        Args:
            message: The user's simple query.

        Returns:
            Generated response.
        """
        ...

    async def health_check(self) -> bool:
        """Check if the backend is healthy and available.

        Returns:
            True if backend is healthy, False otherwise.
        """
        ...
