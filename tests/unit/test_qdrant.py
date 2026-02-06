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
        mock_client.search = AsyncMock(return_value=[mock_hit])

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
        mock_client.search = AsyncMock(return_value=[])

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.search_conversations("test", user_id=12345)

        # Verify filter was passed
        call_args = mock_client.search.call_args
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
        mock_client.search = AsyncMock(return_value=[mock_hit])

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
        mock_client.search = AsyncMock(return_value=[mock_hit])

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
        mock_client.search = AsyncMock(return_value=[])

        with patch("zetherion_ai.memory.qdrant.get_settings", return_value=mock_settings):
            with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client):
                with patch(
                    "zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings
                ):
                    memory = QdrantMemory()
                    await memory.search_memories("test", memory_type="preference")

        # Verify filter was passed
        call_args = mock_client.search.call_args
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
