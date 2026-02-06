"""Tests for Gemini embeddings client."""

from unittest.mock import patch

import pytest


class TestGeminiEmbeddings:
    """Tests for GeminiEmbeddings class."""

    @pytest.fixture
    def embeddings_client(self, mock_embeddings_client, monkeypatch):
        """Create embeddings client with mocked Gemini."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        with patch(
            "zetherion_ai.memory.embeddings.genai.Client", return_value=mock_embeddings_client
        ):
            from zetherion_ai.memory.embeddings import GeminiEmbeddings

            return GeminiEmbeddings()

    @pytest.mark.asyncio
    async def test_embed_text(self, embeddings_client, mock_embeddings_client):
        """Test single text embedding."""
        result = await embeddings_client.embed_text("test text")

        assert len(result) == 768
        assert all(isinstance(v, float) for v in result)
        mock_embeddings_client.models.embed_content.assert_called_once()

    @pytest.mark.asyncio
    async def test_embed_query(self, embeddings_client):
        """Test query embedding (should use same as embed_text)."""
        result = await embeddings_client.embed_query("test query")

        assert len(result) == 768

    @pytest.mark.asyncio
    async def test_embed_batch_parallel(self, embeddings_client, mock_embeddings_client):
        """Test batch embedding generates in parallel."""
        texts = ["text1", "text2", "text3"]

        results = await embeddings_client.embed_batch(texts)

        assert len(results) == 3
        assert all(len(vec) == 768 for vec in results)
        # Should be called once per text
        assert mock_embeddings_client.models.embed_content.call_count == 3

    @pytest.mark.asyncio
    async def test_embed_empty_list(self, embeddings_client):
        """Test embedding empty list returns empty list."""
        results = await embeddings_client.embed_batch([])

        assert results == []

    def test_embedding_dimension_constant(self):
        """Test that EMBEDDING_DIMENSION constant is correct."""
        from zetherion_ai.memory.embeddings import EMBEDDING_DIMENSION

        assert EMBEDDING_DIMENSION == 768
