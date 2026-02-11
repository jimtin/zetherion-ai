"""Unit tests for the Qdrant memory module."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.memory.qdrant import (
    CONVERSATIONS_COLLECTION,
    LONG_TERM_MEMORY_COLLECTION,
    QdrantMemory,
)


@pytest.fixture
def mock_settings():
    """Create mock settings."""
    settings = MagicMock()
    settings.qdrant_host = "localhost"
    settings.qdrant_port = 6333
    settings.qdrant_url = "http://localhost:6333"
    settings.qdrant_use_tls = False
    settings.qdrant_cert_path = None
    return settings


@pytest.fixture
def mock_embeddings():
    """Create mock embeddings client."""
    embeddings = AsyncMock()
    embeddings.embed_text = AsyncMock(return_value=[0.1] * 768)
    embeddings.embed_query = AsyncMock(return_value=[0.1] * 768)
    return embeddings


@pytest.fixture
def mock_encryptor():
    """Create mock field encryptor."""
    encryptor = MagicMock()
    encryptor.encrypt_payload = MagicMock(side_effect=lambda p: {**p, "_encrypted": True})
    encryptor.decrypt_payload = MagicMock(
        side_effect=lambda p: {k: v for k, v in p.items() if k != "_encrypted"}
    )
    return encryptor


class TestQdrantMemoryInit:
    """Tests for QdrantMemory initialization."""

    def test_init_without_tls(self, mock_settings):
        """Test initialization without TLS."""
        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient") as mock_client:
                with patch("zetherion_ai.memory.qdrant.get_embeddings_client"):
                    memory = QdrantMemory()
                    mock_client.assert_called_once_with(
                        host="localhost",
                        port=6333,
                    )
                    assert memory._encryptor is None

    def test_init_with_tls(self, mock_settings):
        """Test initialization with TLS."""
        mock_settings.qdrant_use_tls = True
        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient") as mock_client:
                with patch("zetherion_ai.memory.qdrant.get_embeddings_client"):
                    QdrantMemory()
                    mock_client.assert_called_once_with(
                        url="http://localhost:6333",
                        https=True,
                    )

    def test_init_with_encryptor(self, mock_settings, mock_encryptor):
        """Test initialization with encryptor."""
        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient"):
                with patch("zetherion_ai.memory.qdrant.get_embeddings_client"):
                    memory = QdrantMemory(encryptor=mock_encryptor)
                    assert memory._encryptor == mock_encryptor


class TestQdrantMemoryInitialize:
    """Tests for QdrantMemory initialize method."""

    @pytest.mark.asyncio
    async def test_initialize_creates_collections(self, mock_settings):
        """Test that initialize creates missing collections."""
        mock_client = AsyncMock()
        mock_collections = MagicMock()
        mock_collections.collections = []  # No existing collections
        mock_client.get_collections = AsyncMock(return_value=mock_collections)
        mock_client.create_collection = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch("zetherion_ai.memory.qdrant.get_embeddings_client"):
                    memory = QdrantMemory()
                    await memory.initialize()

        # Should create both collections
        assert mock_client.create_collection.call_count == 2

    @pytest.mark.asyncio
    async def test_initialize_skips_existing_collections(self, mock_settings):
        """Test that initialize skips existing collections."""
        mock_client = AsyncMock()
        mock_conv = MagicMock()
        mock_conv.name = CONVERSATIONS_COLLECTION
        mock_ltm = MagicMock()
        mock_ltm.name = LONG_TERM_MEMORY_COLLECTION
        mock_collections = MagicMock()
        mock_collections.collections = [mock_conv, mock_ltm]
        mock_client.get_collections = AsyncMock(return_value=mock_collections)
        mock_client.create_collection = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch("zetherion_ai.memory.qdrant.get_embeddings_client"):
                    memory = QdrantMemory()
                    await memory.initialize()

        # Should not create any collections
        mock_client.create_collection.assert_not_called()


class TestQdrantMemoryStoreMessage:
    """Tests for store_message method."""

    @pytest.mark.asyncio
    async def test_store_message_basic(self, mock_settings, mock_embeddings):
        """Test basic message storage."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    message_id = await memory.store_message(
                        user_id=12345,
                        channel_id=67890,
                        role="user",
                        content="Hello, world!",
                    )

        assert message_id is not None
        mock_embeddings.embed_text.assert_called_once_with("Hello, world!")
        mock_client.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_message_with_encryption(
        self, mock_settings, mock_embeddings, mock_encryptor
    ):
        """Test message storage with encryption."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory(encryptor=mock_encryptor)
                    await memory.store_message(
                        user_id=12345,
                        channel_id=67890,
                        role="user",
                        content="Secret message",
                    )

        mock_encryptor.encrypt_payload.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_message_with_metadata(self, mock_settings, mock_embeddings):
        """Test message storage with metadata."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.store_message(
                        user_id=12345,
                        channel_id=67890,
                        role="user",
                        content="Hello!",
                        metadata={"custom_field": "custom_value"},
                    )

        # Check that upsert was called with the metadata
        call_args = mock_client.upsert.call_args
        point = call_args[1]["points"][0]
        assert "custom_field" in point.payload


class TestQdrantMemoryStoreMemory:
    """Tests for store_memory method."""

    @pytest.mark.asyncio
    async def test_store_memory_basic(self, mock_settings, mock_embeddings):
        """Test basic memory storage."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    memory_id = await memory.store_memory(
                        content="User prefers dark mode",
                        memory_type="preference",
                    )

        assert memory_id is not None
        mock_embeddings.embed_text.assert_called_once_with("User prefers dark mode")
        mock_client.upsert.assert_called_once()

    @pytest.mark.asyncio
    async def test_store_memory_with_encryption(
        self, mock_settings, mock_embeddings, mock_encryptor
    ):
        """Test memory storage with encryption."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory(encryptor=mock_encryptor)
                    await memory.store_memory(
                        content="Secret preference",
                        memory_type="preference",
                    )

        mock_encryptor.encrypt_payload.assert_called_once()


class TestQdrantMemorySearchConversations:
    """Tests for search_conversations method."""

    @pytest.mark.asyncio
    async def test_search_conversations_basic(self, mock_settings, mock_embeddings):
        """Test basic conversation search."""
        mock_client = AsyncMock()
        mock_hit = MagicMock()
        mock_hit.id = "test-id"
        mock_hit.score = 0.95
        mock_hit.payload = {
            "content": "Hello!",
            "user_id": 12345,
            "role": "user",
        }
        mock_response = MagicMock()
        mock_response.points = [mock_hit]
        mock_client.query_points = AsyncMock(return_value=mock_response)

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    results = await memory.search_conversations("Hello")

        assert len(results) == 1
        assert results[0]["id"] == "test-id"
        assert results[0]["score"] == 0.95
        assert results[0]["content"] == "Hello!"

    @pytest.mark.asyncio
    async def test_search_conversations_with_user_filter(self, mock_settings, mock_embeddings):
        """Test conversation search with user filter."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.points = []
        mock_client.query_points = AsyncMock(return_value=mock_response)

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.search_conversations("test", user_id=12345)

        # Verify filter was passed
        call_args = mock_client.query_points.call_args
        assert call_args[1]["query_filter"] is not None

    @pytest.mark.asyncio
    async def test_search_conversations_with_decryption(
        self, mock_settings, mock_embeddings, mock_encryptor
    ):
        """Test conversation search with decryption."""
        mock_client = AsyncMock()
        mock_hit = MagicMock()
        mock_hit.id = "test-id"
        mock_hit.score = 0.9
        mock_hit.payload = {"content": "encrypted", "_encrypted": True}
        mock_response = MagicMock()
        mock_response.points = [mock_hit]
        mock_client.query_points = AsyncMock(return_value=mock_response)

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory(encryptor=mock_encryptor)
                    await memory.search_conversations("test")

        mock_encryptor.decrypt_payload.assert_called_once()


class TestQdrantMemorySearchMemories:
    """Tests for search_memories method."""

    @pytest.mark.asyncio
    async def test_search_memories_basic(self, mock_settings, mock_embeddings):
        """Test basic memory search."""
        mock_client = AsyncMock()
        mock_hit = MagicMock()
        mock_hit.id = "memory-id"
        mock_hit.score = 0.88
        mock_hit.payload = {
            "content": "User likes Python",
            "type": "preference",
        }
        mock_response = MagicMock()
        mock_response.points = [mock_hit]
        mock_client.query_points = AsyncMock(return_value=mock_response)

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    results = await memory.search_memories("Python")

        assert len(results) == 1
        assert results[0]["id"] == "memory-id"
        assert results[0]["content"] == "User likes Python"

    @pytest.mark.asyncio
    async def test_search_memories_with_type_filter(self, mock_settings, mock_embeddings):
        """Test memory search with type filter."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.points = []
        mock_client.query_points = AsyncMock(return_value=mock_response)

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.search_memories("test", memory_type="preference")

        # Verify filter was passed
        call_args = mock_client.query_points.call_args
        assert call_args[1]["query_filter"] is not None


class TestQdrantMemoryGetRecentContext:
    """Tests for get_recent_context method."""

    @pytest.mark.asyncio
    async def test_get_recent_context(self, mock_settings, mock_embeddings):
        """Test getting recent conversation context."""
        mock_client = AsyncMock()
        mock_point1 = MagicMock()
        mock_point1.id = "msg1"
        mock_point1.payload = {"content": "First", "timestamp": "2024-01-01T10:00:00"}
        mock_point2 = MagicMock()
        mock_point2.id = "msg2"
        mock_point2.payload = {"content": "Second", "timestamp": "2024-01-01T10:01:00"}
        mock_client.scroll = AsyncMock(return_value=([mock_point2, mock_point1], None))

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    results = await memory.get_recent_context(
                        user_id=12345,
                        channel_id=67890,
                        limit=20,
                    )

        assert len(results) == 2
        # Should be sorted by timestamp (oldest first)
        assert results[0]["content"] == "First"
        assert results[1]["content"] == "Second"

    @pytest.mark.asyncio
    async def test_get_recent_context_with_decryption(
        self, mock_settings, mock_embeddings, mock_encryptor
    ):
        """Test getting context with decryption."""
        mock_client = AsyncMock()
        mock_point = MagicMock()
        mock_point.id = "msg1"
        mock_point.payload = {
            "content": "encrypted",
            "_encrypted": True,
            "timestamp": "2024-01-01T10:00:00",
        }
        mock_client.scroll = AsyncMock(return_value=([mock_point], None))

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory(encryptor=mock_encryptor)
                    await memory.get_recent_context(user_id=123, channel_id=456)

        mock_encryptor.decrypt_payload.assert_called_once()


class TestQdrantMemoryClose:
    """Tests for close method."""

    @pytest.mark.asyncio
    async def test_close(self, mock_settings):
        """Test closing the client."""
        mock_client = AsyncMock()
        mock_client.close = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch("zetherion_ai.memory.qdrant.get_embeddings_client"):
                    memory = QdrantMemory()
                    await memory.close()

        mock_client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_without_close_method(self, mock_settings):
        """Test closing when client doesn't have close method."""
        mock_client = MagicMock()
        del mock_client.close  # Remove close method

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch("zetherion_ai.memory.qdrant.get_embeddings_client"):
                    memory = QdrantMemory()
                    # Should not raise
                    await memory.close()


class TestStoreMemoryUserId:
    """Tests for store_memory user_id parameter handling."""

    @pytest.mark.asyncio
    async def test_store_memory_includes_user_id_when_provided(
        self, mock_settings, mock_embeddings
    ):
        """Test that store_memory includes user_id in payload when provided."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.store_memory(
                        content="User likes Python",
                        memory_type="preference",
                        user_id=12345,
                    )

        call_args = mock_client.upsert.call_args
        point = call_args[1]["points"][0]
        assert "user_id" in point.payload
        assert point.payload["user_id"] == 12345

    @pytest.mark.asyncio
    async def test_store_memory_excludes_user_id_when_not_provided(
        self, mock_settings, mock_embeddings
    ):
        """Test that store_memory does NOT include user_id when not provided (None)."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.store_memory(
                        content="General fact",
                        memory_type="fact",
                    )

        call_args = mock_client.upsert.call_args
        point = call_args[1]["points"][0]
        assert "user_id" not in point.payload


class TestSearchMemoriesUserId:
    """Tests for search_memories user_id parameter handling."""

    @pytest.mark.asyncio
    async def test_search_memories_adds_user_id_filter_when_provided(
        self, mock_settings, mock_embeddings
    ):
        """Test that search_memories adds FieldCondition filter for user_id when provided."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.points = []
        mock_client.query_points = AsyncMock(return_value=mock_response)

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.search_memories("test query", user_id=12345)

        call_args = mock_client.query_points.call_args
        query_filter = call_args[1]["query_filter"]
        assert query_filter is not None
        # Should have a user_id FieldCondition in the must list
        field_keys = [cond.key for cond in query_filter.must]
        assert "user_id" in field_keys

    @pytest.mark.asyncio
    async def test_search_memories_no_user_id_filter_when_not_provided(
        self, mock_settings, mock_embeddings
    ):
        """Test that search_memories does NOT filter by user_id when not provided."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.points = []
        mock_client.query_points = AsyncMock(return_value=mock_response)

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.search_memories("test query")

        call_args = mock_client.query_points.call_args
        query_filter = call_args[1]["query_filter"]
        # No filter at all when neither memory_type nor user_id provided
        assert query_filter is None

    @pytest.mark.asyncio
    async def test_search_memories_user_id_with_type_filter(self, mock_settings, mock_embeddings):
        """Test that search_memories combines user_id and memory_type filters."""
        mock_client = AsyncMock()
        mock_response = MagicMock()
        mock_response.points = []
        mock_client.query_points = AsyncMock(return_value=mock_response)

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.search_memories(
                        "test query", memory_type="preference", user_id=99999
                    )

        call_args = mock_client.query_points.call_args
        query_filter = call_args[1]["query_filter"]
        assert query_filter is not None
        field_keys = [cond.key for cond in query_filter.must]
        assert "type" in field_keys
        assert "user_id" in field_keys


class TestTimestampTimezone:
    """Tests for timezone-aware timestamp generation."""

    @pytest.mark.asyncio
    async def test_store_message_timestamp_uses_utc(self, mock_settings, mock_embeddings):
        """Test that store_message timestamps use timezone.utc (contain +00:00 or Z)."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.store_message(
                        user_id=123, channel_id=456, role="user", content="Hello"
                    )

        call_args = mock_client.upsert.call_args
        point = call_args[1]["points"][0]
        timestamp = point.payload["timestamp"]
        assert "+00:00" in timestamp or timestamp.endswith("Z")

    @pytest.mark.asyncio
    async def test_store_memory_timestamp_uses_utc(self, mock_settings, mock_embeddings):
        """Test that store_memory timestamps use timezone.utc."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.store_memory(content="A fact", memory_type="fact")

        call_args = mock_client.upsert.call_args
        point = call_args[1]["points"][0]
        timestamp = point.payload["timestamp"]
        assert "+00:00" in timestamp or timestamp.endswith("Z")


class TestTlsCertPath:
    """Tests for TLS certificate path configuration."""

    def test_tls_with_cert_path_passes_verify(self, mock_settings):
        """Test that TLS cert path is wired when qdrant_use_tls=True and qdrant_cert_path is set."""
        mock_settings.qdrant_use_tls = True
        mock_settings.qdrant_cert_path = "/path/to/cert.pem"

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient") as mock_client:
                with patch("zetherion_ai.memory.qdrant.get_embeddings_client"):
                    QdrantMemory()
                    mock_client.assert_called_once_with(
                        url=mock_settings.qdrant_url,
                        https=True,
                        verify="/path/to/cert.pem",
                    )


class TestDynamicEmbeddingDimension:
    """Tests for dynamic embedding dimension via get_embedding_dimension()."""

    @pytest.mark.asyncio
    async def test_ensure_collection_uses_dynamic_dimension(self, mock_settings):
        """Test that _ensure_collection uses get_embedding_dimension() for vector size."""
        mock_client = AsyncMock()
        mock_collections = MagicMock()
        mock_collections.collections = []
        mock_client.get_collections = AsyncMock(return_value=mock_collections)
        mock_client.create_collection = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch("zetherion_ai.memory.qdrant.get_embeddings_client"):
                    with patch(
                        "zetherion_ai.memory.qdrant.get_embedding_dimension", return_value=3072
                    ) as mock_dim:
                        memory = QdrantMemory()
                        await memory._ensure_collection("test_collection")

        mock_dim.assert_called()
        call_args = mock_client.create_collection.call_args
        vectors_config = call_args[1]["vectors_config"]
        assert vectors_config.size == 3072


class TestSearchMemoriesDecryption:
    """Tests for search_memories decryption path."""

    @pytest.mark.asyncio
    async def test_search_memories_with_decryption(
        self, mock_settings, mock_embeddings, mock_encryptor
    ):
        """Test that search_memories decrypts payloads when encryptor is configured."""
        mock_client = AsyncMock()
        mock_hit = MagicMock()
        mock_hit.id = "memory-id"
        mock_hit.score = 0.9
        mock_hit.payload = {"content": "encrypted data", "_encrypted": True}
        mock_response = MagicMock()
        mock_response.points = [mock_hit]
        mock_client.query_points = AsyncMock(return_value=mock_response)

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory(encryptor=mock_encryptor)
                    results = await memory.search_memories("test query")

        mock_encryptor.decrypt_payload.assert_called_once()
        assert len(results) == 1
        assert "_encrypted" not in results[0]


class TestEnsureCollectionPublic:
    """Tests for the public ensure_collection method."""

    @pytest.mark.asyncio
    async def test_ensure_collection_creates_when_missing(self, mock_settings, mock_embeddings):
        """Test that ensure_collection creates a collection when it does not exist."""
        mock_client = AsyncMock()
        mock_collections = MagicMock()
        mock_collections.collections = []
        mock_client.get_collections = AsyncMock(return_value=mock_collections)
        mock_client.create_collection = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    with patch(
                        "zetherion_ai.memory.qdrant.get_embedding_dimension", return_value=768
                    ):
                        memory = QdrantMemory()
                        await memory.ensure_collection("my_collection")

        mock_client.create_collection.assert_called_once()
        call_args = mock_client.create_collection.call_args
        assert call_args[1]["collection_name"] == "my_collection"
        assert call_args[1]["vectors_config"].size == 768

    @pytest.mark.asyncio
    async def test_ensure_collection_skips_when_exists(self, mock_settings, mock_embeddings):
        """Test that ensure_collection does nothing when the collection already exists."""
        mock_client = AsyncMock()
        existing = MagicMock()
        existing.name = "my_collection"
        mock_collections = MagicMock()
        mock_collections.collections = [existing]
        mock_client.get_collections = AsyncMock(return_value=mock_collections)
        mock_client.create_collection = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.ensure_collection("my_collection")

        mock_client.create_collection.assert_not_called()

    @pytest.mark.asyncio
    async def test_ensure_collection_uses_custom_vector_size(self, mock_settings, mock_embeddings):
        """Test that ensure_collection uses explicit vector_size when provided."""
        mock_client = AsyncMock()
        mock_collections = MagicMock()
        mock_collections.collections = []
        mock_client.get_collections = AsyncMock(return_value=mock_collections)
        mock_client.create_collection = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.ensure_collection("custom_col", vector_size=1536)

        call_args = mock_client.create_collection.call_args
        assert call_args[1]["vectors_config"].size == 1536


class TestStoreWithPayload:
    """Tests for store_with_payload method."""

    @pytest.mark.asyncio
    async def test_store_with_payload_basic(self, mock_settings, mock_embeddings):
        """Test basic store_with_payload stores a point and returns the ID."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    result = await memory.store_with_payload(
                        collection_name="test_col",
                        point_id="point-123",
                        payload={"content": "test data", "key": "value"},
                    )

        assert result == "point-123"
        mock_embeddings.embed_text.assert_called_once_with("test data")
        mock_client.upsert.assert_called_once()
        call_args = mock_client.upsert.call_args
        assert call_args[1]["collection_name"] == "test_col"

    @pytest.mark.asyncio
    async def test_store_with_payload_uses_content_for_embedding(
        self, mock_settings, mock_embeddings
    ):
        """Test that content_for_embedding param takes priority over payload content."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.store_with_payload(
                        collection_name="test_col",
                        point_id="point-456",
                        payload={"content": "payload content"},
                        content_for_embedding="custom embedding text",
                    )

        mock_embeddings.embed_text.assert_called_once_with("custom embedding text")

    @pytest.mark.asyncio
    async def test_store_with_payload_uses_text_alias(self, mock_settings, mock_embeddings):
        """Test that text param (alias) takes priority over content_for_embedding."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.store_with_payload(
                        collection_name="test_col",
                        point_id="point-789",
                        payload={"content": "payload content"},
                        text="text alias content",
                        content_for_embedding="should be ignored",
                    )

        mock_embeddings.embed_text.assert_called_once_with("text alias content")

    @pytest.mark.asyncio
    async def test_store_with_payload_with_encryption(
        self, mock_settings, mock_embeddings, mock_encryptor
    ):
        """Test that store_with_payload encrypts payload when encryptor is configured."""
        mock_client = AsyncMock()
        mock_client.upsert = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory(encryptor=mock_encryptor)
                    await memory.store_with_payload(
                        collection_name="test_col",
                        point_id="enc-point",
                        payload={"content": "sensitive data"},
                    )

        mock_encryptor.encrypt_payload.assert_called_once()
        call_args = mock_client.upsert.call_args
        point = call_args[1]["points"][0]
        assert point.payload.get("_encrypted") is True


class TestGetById:
    """Tests for get_by_id method."""

    @pytest.mark.asyncio
    async def test_get_by_id_found(self, mock_settings, mock_embeddings):
        """Test get_by_id returns payload when point is found."""
        mock_client = AsyncMock()
        mock_point = MagicMock()
        mock_point.id = "point-123"
        mock_point.payload = {"content": "found data", "key": "value"}
        mock_client.retrieve = AsyncMock(return_value=[mock_point])

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    result = await memory.get_by_id("test_col", "point-123")

        assert result is not None
        assert result["id"] == "point-123"
        assert result["content"] == "found data"
        assert result["key"] == "value"

    @pytest.mark.asyncio
    async def test_get_by_id_not_found(self, mock_settings, mock_embeddings):
        """Test get_by_id returns None when point is not found."""
        mock_client = AsyncMock()
        mock_client.retrieve = AsyncMock(return_value=[])

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    result = await memory.get_by_id("test_col", "nonexistent")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_id_with_decryption(self, mock_settings, mock_embeddings, mock_encryptor):
        """Test get_by_id decrypts payload when encryptor is configured."""
        mock_client = AsyncMock()
        mock_point = MagicMock()
        mock_point.id = "enc-point"
        mock_point.payload = {"content": "encrypted", "_encrypted": True}
        mock_client.retrieve = AsyncMock(return_value=[mock_point])

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory(encryptor=mock_encryptor)
                    result = await memory.get_by_id("test_col", "enc-point")

        mock_encryptor.decrypt_payload.assert_called_once()
        assert result is not None
        assert "_encrypted" not in result

    @pytest.mark.asyncio
    async def test_get_by_id_exception_returns_none(self, mock_settings, mock_embeddings):
        """Test get_by_id returns None when an exception occurs."""
        mock_client = AsyncMock()
        mock_client.retrieve = AsyncMock(side_effect=Exception("connection error"))

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    result = await memory.get_by_id("test_col", "error-point")

        assert result is None

    @pytest.mark.asyncio
    async def test_get_by_id_none_payload(self, mock_settings, mock_embeddings):
        """Test get_by_id handles None payload gracefully."""
        mock_client = AsyncMock()
        mock_point = MagicMock()
        mock_point.id = "point-null"
        mock_point.payload = None
        mock_client.retrieve = AsyncMock(return_value=[mock_point])

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    result = await memory.get_by_id("test_col", "point-null")

        assert result is not None
        assert result["id"] == "point-null"


class TestFilterByField:
    """Tests for filter_by_field method."""

    @pytest.mark.asyncio
    async def test_filter_by_field_with_results(self, mock_settings, mock_embeddings):
        """Test filter_by_field returns matching points."""
        mock_client = AsyncMock()
        mock_point1 = MagicMock()
        mock_point1.id = "p1"
        mock_point1.payload = {"status": "active", "name": "Item 1"}
        mock_point2 = MagicMock()
        mock_point2.id = "p2"
        mock_point2.payload = {"status": "active", "name": "Item 2"}
        mock_client.scroll = AsyncMock(return_value=([mock_point1, mock_point2], None))

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    results = await memory.filter_by_field("test_col", "status", "active")

        assert len(results) == 2
        assert results[0]["id"] == "p1"
        assert results[0]["name"] == "Item 1"
        assert results[1]["id"] == "p2"

    @pytest.mark.asyncio
    async def test_filter_by_field_empty_results(self, mock_settings, mock_embeddings):
        """Test filter_by_field returns empty list when no matches."""
        mock_client = AsyncMock()
        mock_client.scroll = AsyncMock(return_value=([], None))

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    results = await memory.filter_by_field("test_col", "status", "nonexistent")

        assert results == []

    @pytest.mark.asyncio
    async def test_filter_by_field_with_decryption(
        self, mock_settings, mock_embeddings, mock_encryptor
    ):
        """Test filter_by_field decrypts payloads when encryptor is configured."""
        mock_client = AsyncMock()
        mock_point = MagicMock()
        mock_point.id = "enc-p"
        mock_point.payload = {"status": "active", "_encrypted": True}
        mock_client.scroll = AsyncMock(return_value=([mock_point], None))

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory(encryptor=mock_encryptor)
                    results = await memory.filter_by_field("test_col", "status", "active")

        mock_encryptor.decrypt_payload.assert_called_once()
        assert len(results) == 1
        assert "_encrypted" not in results[0]

    @pytest.mark.asyncio
    async def test_filter_by_field_custom_limit(self, mock_settings, mock_embeddings):
        """Test filter_by_field passes custom limit to scroll."""
        mock_client = AsyncMock()
        mock_client.scroll = AsyncMock(return_value=([], None))

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.filter_by_field("test_col", "field", "val", limit=50)

        call_args = mock_client.scroll.call_args
        assert call_args[1]["limit"] == 50


class TestDeleteById:
    """Tests for delete_by_id method."""

    @pytest.mark.asyncio
    async def test_delete_by_id_success(self, mock_settings, mock_embeddings):
        """Test delete_by_id returns True on successful deletion."""
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock()

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    result = await memory.delete_by_id("test_col", "point-123")

        assert result is True
        mock_client.delete.assert_called_once()

    @pytest.mark.asyncio
    async def test_delete_by_id_failure(self, mock_settings, mock_embeddings):
        """Test delete_by_id returns False when an exception occurs."""
        mock_client = AsyncMock()
        mock_client.delete = AsyncMock(side_effect=Exception("deletion failed"))

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    result = await memory.delete_by_id("test_col", "error-point")

        assert result is False
