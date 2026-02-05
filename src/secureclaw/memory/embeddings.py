"""Gemini embeddings client using the new google-genai package."""

import asyncio

from google import genai

from secureclaw.config import get_settings
from secureclaw.logging import get_logger

log = get_logger("secureclaw.memory.embeddings")

# Embedding dimension for text-embedding-004
EMBEDDING_DIMENSION = 768


class GeminiEmbeddings:
    """Client for generating embeddings using Gemini."""

    def __init__(self) -> None:
        """Initialize the Gemini embeddings client."""
        settings = get_settings()
        self._client = genai.Client(api_key=settings.gemini_api_key.get_secret_value())
        self._model = settings.embedding_model
        log.info("gemini_embeddings_initialized", model=self._model)

    async def embed_text(self, text: str) -> list[float]:
        """Generate embedding for a single text.

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

    async def embed_query(self, query: str) -> list[float]:
        """Generate embedding for a search query.

        Args:
            query: The search query to embed.

        Returns:
            A list of floats representing the embedding vector.
        """
        # For queries, we use the same embedding but it's semantically a query
        return await self.embed_text(query)

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts in parallel.

        Args:
            texts: List of texts to embed.

        Returns:
            A list of embedding vectors.
        """
        # Generate all embeddings concurrently for better performance
        results = await asyncio.gather(*[self.embed_text(text) for text in texts])
        return list(results)
