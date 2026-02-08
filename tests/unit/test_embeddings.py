"""Unit tests for the embeddings module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.memory.embeddings import (
    EMBEDDING_DIMENSION,
    EMBEDDING_DIMENSIONS,
    GeminiEmbeddings,
    OllamaEmbeddings,
    OpenAIEmbeddings,
    get_embedding_dimension,
    get_embeddings_client,
)


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


class TestOpenAIEmbeddingsInit:
    """Tests for OpenAIEmbeddings initialization."""

    def test_init_sets_correct_model(self):
        """Test initialization sets the configured model."""
        mock_settings = MagicMock()
        mock_settings.openai_api_key = MagicMock()
        mock_settings.openai_api_key.get_secret_value.return_value = "test-openai-key"
        mock_settings.openai_embedding_model = "text-embedding-3-large"
        mock_settings.openai_embedding_dimensions = 3072

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.openai.AsyncOpenAI"):
                embeddings = OpenAIEmbeddings()
                assert embeddings._model == "text-embedding-3-large"

    def test_init_sets_correct_dimensions(self):
        """Test initialization sets the configured dimensions (3072)."""
        mock_settings = MagicMock()
        mock_settings.openai_api_key = MagicMock()
        mock_settings.openai_api_key.get_secret_value.return_value = "test-openai-key"
        mock_settings.openai_embedding_model = "text-embedding-3-large"
        mock_settings.openai_embedding_dimensions = 3072

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.openai.AsyncOpenAI"):
                embeddings = OpenAIEmbeddings()
                assert embeddings._dimensions == 3072

    def test_init_raises_without_api_key(self):
        """Test initialization raises ValueError when OpenAI API key is missing."""
        mock_settings = MagicMock()
        mock_settings.openai_api_key = None

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with pytest.raises(ValueError, match="OpenAI API key required"):
                OpenAIEmbeddings()


class TestOpenAIEmbeddingsEmbedText:
    """Tests for OpenAIEmbeddings.embed_text method."""

    @pytest.mark.asyncio
    async def test_embed_text_calls_api_correctly(self):
        """Test that embed_text calls the OpenAI API with correct parameters."""
        mock_settings = MagicMock()
        mock_settings.openai_api_key = MagicMock()
        mock_settings.openai_api_key.get_secret_value.return_value = "test-openai-key"
        mock_settings.openai_embedding_model = "text-embedding-3-large"
        mock_settings.openai_embedding_dimensions = 3072

        mock_embedding = MagicMock()
        mock_embedding.embedding = [0.1] * 3072
        mock_response = MagicMock()
        mock_response.data = [mock_embedding]

        mock_async_client = AsyncMock()
        mock_async_client.embeddings.create = AsyncMock(return_value=mock_response)

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch(
                "zetherion_ai.memory.embeddings.openai.AsyncOpenAI",
                return_value=mock_async_client,
            ):
                embeddings = OpenAIEmbeddings()
                result = await embeddings.embed_text("Hello, world!")

        assert len(result) == 3072
        mock_async_client.embeddings.create.assert_called_once_with(
            model="text-embedding-3-large",
            input="Hello, world!",
            dimensions=3072,
        )

    @pytest.mark.asyncio
    async def test_embed_text_returns_list_of_floats(self):
        """Test that embed_text returns a list of floats."""
        mock_settings = MagicMock()
        mock_settings.openai_api_key = MagicMock()
        mock_settings.openai_api_key.get_secret_value.return_value = "test-openai-key"
        mock_settings.openai_embedding_model = "text-embedding-3-large"
        mock_settings.openai_embedding_dimensions = 3072

        expected_values = [0.1 * i for i in range(3072)]
        mock_embedding = MagicMock()
        mock_embedding.embedding = expected_values
        mock_response = MagicMock()
        mock_response.data = [mock_embedding]

        mock_async_client = AsyncMock()
        mock_async_client.embeddings.create = AsyncMock(return_value=mock_response)

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch(
                "zetherion_ai.memory.embeddings.openai.AsyncOpenAI",
                return_value=mock_async_client,
            ):
                embeddings = OpenAIEmbeddings()
                result = await embeddings.embed_text("test")

        assert result == expected_values


class TestOpenAIEmbeddingsClose:
    """Tests for OpenAIEmbeddings.close method."""

    @pytest.mark.asyncio
    async def test_close_calls_client_close(self):
        """Test that close() calls the underlying client's close method."""
        mock_settings = MagicMock()
        mock_settings.openai_api_key = MagicMock()
        mock_settings.openai_api_key.get_secret_value.return_value = "test-openai-key"
        mock_settings.openai_embedding_model = "text-embedding-3-large"
        mock_settings.openai_embedding_dimensions = 3072

        mock_async_client = AsyncMock()

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch(
                "zetherion_ai.memory.embeddings.openai.AsyncOpenAI",
                return_value=mock_async_client,
            ):
                embeddings = OpenAIEmbeddings()
                await embeddings.close()

        mock_async_client.close.assert_called_once()


class TestOllamaEmbeddingsClose:
    """Tests for OllamaEmbeddings.close method."""

    @pytest.mark.asyncio
    async def test_close_calls_client_aclose(self):
        """Test that close() calls self._client.aclose()."""
        mock_settings = MagicMock()
        mock_settings.ollama_host = "localhost"
        mock_settings.ollama_port = 11434
        mock_settings.ollama_embedding_model = "nomic-embed-text"
        mock_settings.ollama_timeout = 30

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.httpx.AsyncClient") as mock_httpx:
                mock_http_client = AsyncMock()
                mock_httpx.return_value = mock_http_client

                embeddings = OllamaEmbeddings()
                await embeddings.close()

        mock_http_client.aclose.assert_called_once()


class TestGetEmbeddingDimension:
    """Tests for the get_embedding_dimension() function."""

    def test_returns_768_for_ollama(self):
        """Test that ollama backend returns 768 dimensions."""
        mock_settings = MagicMock()
        mock_settings.embeddings_backend = "ollama"
        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            assert get_embedding_dimension() == 768

    def test_returns_768_for_gemini(self):
        """Test that gemini backend returns 768 dimensions."""
        mock_settings = MagicMock()
        mock_settings.embeddings_backend = "gemini"
        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            assert get_embedding_dimension() == 768

    def test_returns_3072_for_openai(self):
        """Test that openai backend returns 3072 dimensions."""
        mock_settings = MagicMock()
        mock_settings.embeddings_backend = "openai"
        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            assert get_embedding_dimension() == 3072

    def test_returns_default_768_for_unknown_backend(self):
        """Test that unknown backend falls back to 768 dimensions."""
        mock_settings = MagicMock()
        mock_settings.embeddings_backend = "unknown"
        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            assert get_embedding_dimension() == 768

    def test_embedding_dimensions_dict_has_all_backends(self):
        """Test that EMBEDDING_DIMENSIONS dict contains all supported backends."""
        assert "ollama" in EMBEDDING_DIMENSIONS
        assert "gemini" in EMBEDDING_DIMENSIONS
        assert "openai" in EMBEDDING_DIMENSIONS
        assert EMBEDDING_DIMENSIONS["ollama"] == 768
        assert EMBEDDING_DIMENSIONS["gemini"] == 768
        assert EMBEDDING_DIMENSIONS["openai"] == 3072


class TestGetEmbeddingsClientFactory:
    """Tests for the get_embeddings_client() factory function."""

    def test_returns_openai_for_openai_backend(self):
        """Test that factory returns OpenAIEmbeddings for 'openai' backend."""
        mock_settings = MagicMock()
        mock_settings.embeddings_backend = "openai"
        mock_settings.openai_api_key = MagicMock()
        mock_settings.openai_api_key.get_secret_value.return_value = "test-key"
        mock_settings.openai_embedding_model = "text-embedding-3-large"
        mock_settings.openai_embedding_dimensions = 3072

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.openai.AsyncOpenAI"):
                client = get_embeddings_client()
                assert isinstance(client, OpenAIEmbeddings)

    def test_returns_gemini_for_gemini_backend(self):
        """Test that factory returns GeminiEmbeddings for 'gemini' backend."""
        mock_settings = MagicMock()
        mock_settings.embeddings_backend = "gemini"
        mock_settings.gemini_api_key = MagicMock()
        mock_settings.gemini_api_key.get_secret_value.return_value = "test-key"
        mock_settings.embedding_model = "text-embedding-004"

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.genai.Client"):
                client = get_embeddings_client()
                assert isinstance(client, GeminiEmbeddings)

    def test_returns_ollama_for_ollama_backend(self):
        """Test that factory returns OllamaEmbeddings for 'ollama' backend."""
        mock_settings = MagicMock()
        mock_settings.embeddings_backend = "ollama"
        mock_settings.ollama_host = "localhost"
        mock_settings.ollama_port = 11434
        mock_settings.ollama_embedding_model = "nomic-embed-text"
        mock_settings.ollama_timeout = 30

        with patch("zetherion_ai.memory.embeddings.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.embeddings.httpx.AsyncClient"):
                client = get_embeddings_client()
                assert isinstance(client, OllamaEmbeddings)
