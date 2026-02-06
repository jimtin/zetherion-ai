"""Embeddings client supporting both Ollama (local) and Gemini (cloud).

Default is Ollama for local-first, privacy-preserving operation.
Falls back to Gemini if Ollama is unavailable.
"""

import asyncio
from abc import ABC, abstractmethod

import httpx
from google import genai  # type: ignore[attr-defined]

from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.memory.embeddings")

# Embedding dimension for both nomic-embed-text and text-embedding-004
EMBEDDING_DIMENSION = 768


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


class OllamaEmbeddings(EmbeddingsClient):
    """Client for generating embeddings using Ollama (local)."""

    def __init__(self) -> None:
        """Initialize the Ollama embeddings client."""
        settings = get_settings()
        self._base_url = f"http://{settings.ollama_host}:{settings.ollama_port}"
        self._model = settings.ollama_embedding_model
        self._timeout = settings.ollama_timeout
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
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            response = await client.post(
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
        result = self._client.models.embed_content(
            model=self._model,
            contents=text,
        )
        return list(result.embeddings[0].values)  # type: ignore[index, arg-type]


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
