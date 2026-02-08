"""Embeddings client supporting both Ollama (local) and Gemini (cloud).

Default is Ollama for local-first, privacy-preserving operation.
Falls back to Gemini if Ollama is unavailable.
"""

import asyncio
from abc import ABC, abstractmethod

import httpx
import openai
from google import genai  # type: ignore[attr-defined]

from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.memory.embeddings")

# Embedding dimensions per backend
EMBEDDING_DIMENSIONS: dict[str, int] = {
    "ollama": 768,  # nomic-embed-text
    "gemini": 768,  # text-embedding-004
    "openai": 3072,  # text-embedding-3-large
}


def get_embedding_dimension() -> int:
    """Get the embedding dimension for the configured backend."""
    settings = get_settings()
    return EMBEDDING_DIMENSIONS.get(settings.embeddings_backend, 768)


# Keep backward-compatible name
EMBEDDING_DIMENSION = 768  # Default; use get_embedding_dimension() for runtime value


class EmbeddingsClient(ABC):
    """Abstract base class for embeddings clients."""

    @abstractmethod
    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text."""
        raise NotImplementedError

    async def embed_query(self, query: str) -> list[float]:
        """Generate embedding for a search query."""
        return await self.embed_text(query)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts in parallel."""
        results = await asyncio.gather(*[self.embed_text(text) for text in texts])
        return list(results)

    async def close(self) -> None:
        """Close the client (no-op by default)."""
        return


class OllamaEmbeddings(EmbeddingsClient):
    """Client for generating embeddings using Ollama (local)."""

    def __init__(self) -> None:
        """Initialize the Ollama embeddings client."""
        settings = get_settings()
        self._base_url = f"http://{settings.ollama_host}:{settings.ollama_port}"
        self._model = settings.ollama_embedding_model
        self._timeout = settings.ollama_timeout
        self._client = httpx.AsyncClient(timeout=self._timeout)
        log.info(
            "ollama_embeddings_initialized",
            url=self._base_url,
            model=self._model,
        )

    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding using Ollama.

        Args:
            text: The text to embed.

        Returns:
            A list of floats representing the embedding vector.
        """
        response = await self._client.post(
            f"{self._base_url}/api/embed",
            json={
                "model": self._model,
                "input": text,
            },
        )
        response.raise_for_status()
        data = response.json()
        # Ollama returns {"embeddings": [[...], ...]} for multiple inputs
        # or {"embeddings": [[...]]} for single input
        return list(data["embeddings"][0])

    async def close(self) -> None:
        """Close the HTTP client."""
        await self._client.aclose()


class GeminiEmbeddings(EmbeddingsClient):
    """Client for generating embeddings using Gemini (cloud)."""

    def __init__(self) -> None:
        """Initialize the Gemini embeddings client."""
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        self._model = settings.embedding_model
        log.info("gemini_embeddings_initialized", model=self._model)

    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding using Gemini.

        Args:
            text: The text to embed.

        Returns:
            A list of floats representing the embedding vector.
        """
        # Wrap synchronous Gemini call in thread to avoid blocking event loop
        # NOTE: Uses default ThreadPoolExecutor; may queue under high concurrency.
        result = await asyncio.to_thread(
            self._client.models.embed_content,
            model=self._model,
            contents=text,
        )
        return list(result.embeddings[0].values)  # type: ignore[index, arg-type]

    async def close(self) -> None:
        """Close the Gemini client (no-op)."""


class OpenAIEmbeddings(EmbeddingsClient):
    """Client for generating embeddings using OpenAI (cloud, highest quality)."""

    def __init__(self) -> None:
        """Initialize the OpenAI embeddings client."""
        settings = get_settings()
        if not settings.openai_api_key:
            raise ValueError("OpenAI API key required for OpenAI embeddings")
        self._client = openai.AsyncOpenAI(api_key=settings.openai_api_key.get_secret_value())
        self._model = settings.openai_embedding_model
        self._dimensions = settings.openai_embedding_dimensions
        log.info(
            "openai_embeddings_initialized",
            model=self._model,
            dimensions=self._dimensions,
        )

    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding using OpenAI.

        Args:
            text: The text to embed.

        Returns:
            A list of floats representing the embedding vector.
        """
        response = await self._client.embeddings.create(
            model=self._model,
            input=text,
            dimensions=self._dimensions,
        )
        return list(response.data[0].embedding)

    async def close(self) -> None:
        """Close the OpenAI client."""
        await self._client.close()


def get_embeddings_client() -> EmbeddingsClient:
    """Factory function to get the configured embeddings client.

    Returns Ollama by default (local-first), falls back to Gemini if configured.
    """
    settings = get_settings()

    if settings.embeddings_backend == "ollama":
        try:
            return OllamaEmbeddings()
        except Exception as e:
            log.warning(
                "ollama_embeddings_failed",
                error=str(e),
                fallback="gemini",
            )
            if settings.gemini_api_key:
                return GeminiEmbeddings()
            raise

    elif settings.embeddings_backend == "gemini":
        return GeminiEmbeddings()

    elif settings.embeddings_backend == "openai":
        return OpenAIEmbeddings()

    else:
        # Default: try Ollama first, fall back to Gemini
        try:
            return OllamaEmbeddings()
        except Exception as e:
            log.warning(
                "ollama_embeddings_unavailable",
                error=str(e),
                fallback="gemini",
            )
            return GeminiEmbeddings()
