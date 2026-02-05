"""Factory for creating router instances with appropriate backends."""

from secureclaw.agent.router import GeminiRouterBackend, MessageRouter
from secureclaw.agent.router_ollama import OllamaRouterBackend
from secureclaw.config import get_settings
from secureclaw.logging import get_logger

log = get_logger("secureclaw.agent.router_factory")


async def create_router() -> MessageRouter:
    """Create a router with the appropriate backend.

    Returns:
        MessageRouter instance with configured backend.

    Raises:
        RuntimeError: If no valid backend can be configured.
    """
    settings = get_settings()

    if settings.router_backend == "ollama":
        # Try to use Ollama backend
        try:
            ollama_backend = OllamaRouterBackend()

            # Check if Ollama is healthy
            if await ollama_backend.health_check():
                log.info(
                    "router_backend_selected", backend="ollama", model=settings.ollama_router_model
                )
                return MessageRouter(ollama_backend)
            else:
                log.warning(
                    "ollama_unhealthy_falling_back",
                    reason="Health check failed - model may not be loaded",
                )
                await ollama_backend.close()

        except Exception as e:
            log.error(
                "ollama_initialization_failed",
                error=str(e),
                error_type=type(e).__name__,
            )

        # Fall back to Gemini if available
        if settings.gemini_api_key:
            log.warning("falling_back_to_gemini", reason="Ollama initialization failed")
            gemini_backend = GeminiRouterBackend()
            return MessageRouter(gemini_backend)
        else:
            raise RuntimeError(
                "Ollama backend requested but unavailable, "
                "and no Gemini API key configured for fallback"
            )

    elif settings.router_backend == "gemini":
        # Use Gemini backend (default)
        log.info("router_backend_selected", backend="gemini", model=settings.router_model)
        gemini_backend = GeminiRouterBackend()
        return MessageRouter(gemini_backend)

    else:
        raise ValueError(
            f"Invalid router backend: {settings.router_backend}. Must be 'gemini' or 'ollama'"
        )


def create_router_sync() -> MessageRouter:
    """Create a router synchronously (for backward compatibility).

    Note: This creates a router without performing async health checks.
    Use create_router() for full initialization with health checking.

    Returns:
        MessageRouter instance with configured backend.
    """
    settings = get_settings()

    if settings.router_backend == "ollama":
        log.info(
            "router_backend_selected_sync",
            backend="ollama",
            model=settings.ollama_router_model,
            note="Health check skipped in sync mode",
        )
        ollama_backend = OllamaRouterBackend()
        return MessageRouter(ollama_backend)

    elif settings.router_backend == "gemini":
        log.info("router_backend_selected_sync", backend="gemini", model=settings.router_model)
        gemini_backend = GeminiRouterBackend()
        return MessageRouter(gemini_backend)

    else:
        raise ValueError(
            f"Invalid router backend: {settings.router_backend}. Must be 'gemini' or 'ollama'"
        )
