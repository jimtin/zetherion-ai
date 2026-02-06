"""Unit tests for the Gemini embeddings module."""

from unittest.mock import MagicMock, patch

import pytest

from zetherion_ai.memory.embeddings import EMBEDDING_DIMENSION, GeminiEmbeddings


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock()
    settings.gemini_api_key.get_secret_value.return_value = "test-api-key"
    settings.embedding_model = "models/text-embedding-004"
    return settings


@pytest.fixture
def mock_genai_client():
    """Create a mock genai client."""
    client = MagicMock()
    return client


class TestGeminiEmbeddingsInit:
    """Tests for GeminiEmbeddings initialization."""

    def test_init(self, mock_settings):
        """Test initialization."""
        mock_client = MagicMock()
        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch(
                "zetherion_ai.memory.embeddings.genai.Client", return_value=mock_client
            ) as mock_genai:
                embeddings = GeminiEmbeddings()
                mock_genai.assert_called_once_with(api_key="test-api-key")
                assert embeddings._model == "models/text-embedding-004"


class TestGeminiEmbeddingsEmbedText:
    """Tests for embed_text method."""

    @pytest.mark.asyncio
    async def test_embed_text(self, mock_settings):
        """Test embedding a single text."""
        mock_embedding = MagicMock()
        mock_embedding.values = [0.1] * 768
        mock_result = MagicMock()
        mock_result.embeddings = [mock_embedding]

        mock_client = MagicMock()
        mock_client.models.embed_content.return_value = mock_result

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.genai.Client", return_value=mock_client):
                embeddings = GeminiEmbeddings()
                result = await embeddings.embed_text("Hello, world!")

        assert len(result) == EMBEDDING_DIMENSION
        mock_client.models.embed_content.assert_called_once_with(
            model="models/text-embedding-004",
            contents="Hello, world!",
        )

    @pytest.mark.asyncio
    async def test_embed_text_empty_string(self, mock_settings):
        """Test embedding an empty string."""
        mock_embedding = MagicMock()
        mock_embedding.values = [0.0] * 768
        mock_result = MagicMock()
        mock_result.embeddings = [mock_embedding]

        mock_client = MagicMock()
        mock_client.models.embed_content.return_value = mock_result

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.genai.Client", return_value=mock_client):
                embeddings = GeminiEmbeddings()
                result = await embeddings.embed_text("")

        assert len(result) == EMBEDDING_DIMENSION

    @pytest.mark.asyncio
    async def test_embed_text_long_text(self, mock_settings):
        """Test embedding a long text."""
        long_text = "This is a test. " * 1000
        mock_embedding = MagicMock()
        mock_embedding.values = [0.5] * 768
        mock_result = MagicMock()
        mock_result.embeddings = [mock_embedding]

        mock_client = MagicMock()
        mock_client.models.embed_content.return_value = mock_result

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.genai.Client", return_value=mock_client):
                embeddings = GeminiEmbeddings()
                result = await embeddings.embed_text(long_text)

        assert len(result) == EMBEDDING_DIMENSION


class TestGeminiEmbeddingsEmbedQuery:
    """Tests for embed_query method."""

    @pytest.mark.asyncio
    async def test_embed_query(self, mock_settings):
        """Test embedding a query."""
        mock_embedding = MagicMock()
        mock_embedding.values = [0.2] * 768
        mock_result = MagicMock()
        mock_result.embeddings = [mock_embedding]

        mock_client = MagicMock()
        mock_client.models.embed_content.return_value = mock_result

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.genai.Client", return_value=mock_client):
                embeddings = GeminiEmbeddings()
                result = await embeddings.embed_query("What is Python?")

        assert len(result) == EMBEDDING_DIMENSION

    @pytest.mark.asyncio
    async def test_embed_query_uses_embed_text(self, mock_settings):
        """Test that embed_query uses embed_text internally."""
        mock_embedding = MagicMock()
        mock_embedding.values = [0.3] * 768
        mock_result = MagicMock()
        mock_result.embeddings = [mock_embedding]

        mock_client = MagicMock()
        mock_client.models.embed_content.return_value = mock_result

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.genai.Client", return_value=mock_client):
                embeddings = GeminiEmbeddings()

                # Embed the same text both ways
                result_text = await embeddings.embed_text("test query")
                result_query = await embeddings.embed_query("test query")

        # Should produce identical results
        assert result_text == result_query


class TestGeminiEmbeddingsEmbedBatch:
    """Tests for embed_batch method."""

    @pytest.mark.asyncio
    async def test_embed_batch_single(self, mock_settings):
        """Test embedding a single text in batch."""
        mock_embedding = MagicMock()
        mock_embedding.values = [0.4] * 768
        mock_result = MagicMock()
        mock_result.embeddings = [mock_embedding]

        mock_client = MagicMock()
        mock_client.models.embed_content.return_value = mock_result

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.genai.Client", return_value=mock_client):
                embeddings = GeminiEmbeddings()
                results = await embeddings.embed_batch(["Hello"])

        assert len(results) == 1
        assert len(results[0]) == EMBEDDING_DIMENSION

    @pytest.mark.asyncio
    async def test_embed_batch_multiple(self, mock_settings):
        """Test embedding multiple texts in batch."""
        mock_embedding = MagicMock()
        mock_embedding.values = [0.5] * 768
        mock_result = MagicMock()
        mock_result.embeddings = [mock_embedding]

        mock_client = MagicMock()
        mock_client.models.embed_content.return_value = mock_result

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.genai.Client", return_value=mock_client):
                embeddings = GeminiEmbeddings()
                texts = ["Text 1", "Text 2", "Text 3"]
                results = await embeddings.embed_batch(texts)

        assert len(results) == 3
        for result in results:
            assert len(result) == EMBEDDING_DIMENSION

    @pytest.mark.asyncio
    async def test_embed_batch_empty(self, mock_settings):
        """Test embedding an empty batch."""
        mock_client = MagicMock()

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.genai.Client", return_value=mock_client):
                embeddings = GeminiEmbeddings()
                results = await embeddings.embed_batch([])

        assert results == []

    @pytest.mark.asyncio
    async def test_embed_batch_parallel_execution(self, mock_settings):
        """Test that batch embedding runs in parallel."""
        call_count = 0

        def mock_embed_content(**kwargs):
            nonlocal call_count
            call_count += 1
            mock_embedding = MagicMock()
            mock_embedding.values = [float(call_count)] * 768
            mock_result = MagicMock()
            mock_result.embeddings = [mock_embedding]
            return mock_result

        mock_client = MagicMock()
        mock_client.models.embed_content.side_effect = mock_embed_content

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.genai.Client", return_value=mock_client):
                embeddings = GeminiEmbeddings()
                texts = ["A", "B", "C", "D", "E"]
                results = await embeddings.embed_batch(texts)

        # All texts should have been processed
        assert len(results) == 5
        assert call_count == 5


class TestEmbeddingDimension:
    """Tests for embedding dimension constant."""

    def test_embedding_dimension_value(self):
        """Test that EMBEDDING_DIMENSION is correct."""
        assert EMBEDDING_DIMENSION == 768

    @pytest.mark.asyncio
    async def test_returned_embedding_matches_dimension(self, mock_settings):
        """Test that returned embeddings match the expected dimension."""
        mock_embedding = MagicMock()
        mock_embedding.values = [0.1] * EMBEDDING_DIMENSION
        mock_result = MagicMock()
        mock_result.embeddings = [mock_embedding]

        mock_client = MagicMock()
        mock_client.models.embed_content.return_value = mock_result

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.genai.Client", return_value=mock_client):
                embeddings = GeminiEmbeddings()
                result = await embeddings.embed_text("test")

        assert len(result) == EMBEDDING_DIMENSION
