"""Factory for creating router instances with appropriate backends."""

from zetherion_ai.agent.router import (
    GeminiRouterBackend,
    GroqRouterBackend,
    MessageRouter,
)
from zetherion_ai.agent.router_ollama import OllamaRouterBackend
from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.agent.router_factory")


async def create_router(warmup: bool = True) -> MessageRouter:
    """Create a router with the appropriate backend.

    Args:
        warmup: If True, warm up the Ollama model after creation.

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

                # Warm up the model to avoid cold start delays
                if warmup:
                    await ollama_backend.warmup()

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

    elif settings.router_backend == "groq":
        # Use Groq backend via InferenceBroker and fall back to Gemini/Ollama if unavailable.
        groq_backend = GroqRouterBackend()
        if await groq_backend.health_check():
            log.info("router_backend_selected", backend="groq", model=settings.groq_model)
            return MessageRouter(groq_backend)

        log.warning("groq_unhealthy_falling_back", reason="Groq health check failed")
        if settings.gemini_api_key:
            log.warning("falling_back_to_gemini", reason="Groq unavailable")
            return MessageRouter(GeminiRouterBackend())

        try:
            ollama_backend = OllamaRouterBackend()
            if await ollama_backend.health_check():
                log.warning("falling_back_to_ollama", reason="Groq unavailable and no Gemini key")
                if warmup:
                    await ollama_backend.warmup()
                return MessageRouter(ollama_backend)
        except Exception as e:
            log.error(
                "ollama_fallback_initialization_failed",
                error=str(e),
                error_type=type(e).__name__,
            )

        raise RuntimeError(
            "Groq backend requested but unavailable, "
            "and no healthy Gemini/Ollama fallback configured"
        )

    else:
        raise ValueError(
            "Invalid router backend: "
            f"{settings.router_backend}. Must be 'gemini', 'ollama', or 'groq'"
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

    elif settings.router_backend == "groq":
        log.info("router_backend_selected_sync", backend="groq", model=settings.groq_model)
        groq_backend = GroqRouterBackend()
        return MessageRouter(groq_backend)

    else:
        raise ValueError(
            "Invalid router backend: "
            f"{settings.router_backend}. Must be 'gemini', 'ollama', or 'groq'"
        )
