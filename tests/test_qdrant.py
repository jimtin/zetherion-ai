"""Tests for Qdrant memory storage."""

from unittest.mock import Mock, patch

import pytest


class TestQdrantMemory:
    """Tests for QdrantMemory class."""

    @pytest.fixture
    def memory_client(self, mock_qdrant_client, mock_embeddings_client, monkeypatch):
        """Create QdrantMemory with mocked clients."""
        monkeypatch.setenv("DISCORD_TOKEN", "test")
        monkeypatch.setenv("GEMINI_API_KEY", "test-key")

        with (
            patch("secureclaw.memory.qdrant.AsyncQdrantClient", return_value=mock_qdrant_client),
            patch("secureclaw.memory.embeddings.genai.Client", return_value=mock_embeddings_client),
        ):
            from secureclaw.memory.qdrant import QdrantMemory

            return QdrantMemory()

    @pytest.mark.asyncio
    async def test_initialize_creates_collections(self, memory_client, mock_qdrant_client):
        """Test that initialize creates required collections."""
        await memory_client.initialize()

        # Should check for collections
        mock_qdrant_client.get_collections.assert_called()
        # Should create both collections (conversations and long_term_memory)
        assert mock_qdrant_client.create_collection.call_count == 2

    @pytest.mark.asyncio
    async def test_store_message(self, memory_client, mock_qdrant_client):
        """Test storing a conversation message."""
        message_id = await memory_client.store_message(
            user_id=123,
            channel_id=456,
            role="user",
            content="Hello bot!",
        )

        assert isinstance(message_id, str)
        mock_qdrant_client.upsert.assert_called_once()

        # Check upsert was called with correct collection
        call_args = mock_qdrant_client.upsert.call_args
        assert call_args.kwargs["collection_name"] == "conversations"

    @pytest.mark.asyncio
    async def test_store_memory(self, memory_client, mock_qdrant_client):
        """Test storing a long-term memory."""
        memory_id = await memory_client.store_memory(
            content="User prefers dark mode",
            memory_type="preference",
        )

        assert isinstance(memory_id, str)
        mock_qdrant_client.upsert.assert_called_once()

        call_args = mock_qdrant_client.upsert.call_args
        assert call_args.kwargs["collection_name"] == "long_term_memory"

    @pytest.mark.asyncio
    async def test_search_conversations(self, memory_client, mock_qdrant_client, sample_vector):
        """Test searching conversation history."""
        # Mock search results
        mock_hit = Mock()
        mock_hit.id = "msg-1"
        mock_hit.score = 0.95
        mock_hit.payload = {"content": "Hello", "role": "user"}

        mock_qdrant_client.search.return_value = [mock_hit]

        results = await memory_client.search_conversations(
            query="Hello",
            user_id=123,
            limit=5,
        )

        assert len(results) == 1
        assert results[0]["id"] == "msg-1"
        assert results[0]["score"] == 0.95
        assert results[0]["content"] == "Hello"

        mock_qdrant_client.search.assert_called_once()

    @pytest.mark.asyncio
    async def test_search_memories(self, memory_client, mock_qdrant_client):
        """Test searching long-term memories."""
        mock_hit = Mock()
        mock_hit.id = "mem-1"
        mock_hit.score = 0.88
        mock_hit.payload = {"content": "Dark mode preference", "type": "preference"}

        mock_qdrant_client.search.return_value = [mock_hit]

        results = await memory_client.search_memories(
            query="preferences",
            limit=5,
        )

        assert len(results) == 1
        assert results[0]["id"] == "mem-1"

    @pytest.mark.asyncio
    async def test_get_recent_context(self, memory_client, mock_qdrant_client):
        """Test getting recent conversation context."""
        mock_point1 = Mock()
        mock_point1.id = "msg-1"
        mock_point1.payload = {
            "content": "Hello",
            "role": "user",
            "timestamp": "2026-02-05T10:00:00",
        }

        mock_point2 = Mock()
        mock_point2.id = "msg-2"
        mock_point2.payload = {
            "content": "Hi!",
            "role": "assistant",
            "timestamp": "2026-02-05T10:00:01",
        }

        mock_qdrant_client.scroll.return_value = ([mock_point1, mock_point2], None)

        results = await memory_client.get_recent_context(
            user_id=123,
            channel_id=456,
            limit=20,
        )

        assert len(results) == 2
        # Should be sorted by timestamp
        assert results[0]["content"] == "Hello"
        assert results[1]["content"] == "Hi!"

    @pytest.mark.asyncio
    async def test_store_message_with_metadata(self, memory_client, mock_qdrant_client):
        """Test storing message with additional metadata."""
        await memory_client.store_message(
            user_id=123,
            channel_id=456,
            role="user",
            content="Test",
            metadata={"intent": "simple_query"},
        )

        call_args = mock_qdrant_client.upsert.call_args
        points = call_args.kwargs["points"]
        assert "intent" in points[0].payload
