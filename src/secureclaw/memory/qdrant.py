"""Qdrant vector database client for memory storage."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models

from secureclaw.config import get_settings
from secureclaw.logging import get_logger
from secureclaw.memory.embeddings import EMBEDDING_DIMENSION, GeminiEmbeddings

log = get_logger("secureclaw.memory.qdrant")

# Collection names
CONVERSATIONS_COLLECTION = "conversations"
LONG_TERM_MEMORY_COLLECTION = "long_term_memory"


class QdrantMemory:
    """Vector memory storage using Qdrant."""

    def __init__(self) -> None:
        """Initialize the Qdrant memory client."""
        settings = get_settings()
        self._client = AsyncQdrantClient(
            host=settings.qdrant_host,
            port=settings.qdrant_port,
        )
        self._embeddings = GeminiEmbeddings()
        log.info("qdrant_client_initialized", url=settings.qdrant_url)

    async def initialize(self) -> None:
        """Initialize collections if they don't exist."""
        await self._ensure_collection(CONVERSATIONS_COLLECTION)
        await self._ensure_collection(LONG_TERM_MEMORY_COLLECTION)
        log.info("qdrant_collections_ready")

    async def _ensure_collection(self, name: str) -> None:
        """Ensure a collection exists, creating it if necessary."""
        collections = await self._client.get_collections()
        collection_names = [c.name for c in collections.collections]

        if name not in collection_names:
            await self._client.create_collection(
                collection_name=name,
                vectors_config=qdrant_models.VectorParams(
                    size=EMBEDDING_DIMENSION,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
            log.info("collection_created", name=name)

    async def store_message(
        self,
        user_id: int,
        channel_id: int,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a conversation message.

        Args:
            user_id: Discord user ID.
            channel_id: Discord channel ID.
            role: Message role (user/assistant).
            content: Message content.
            metadata: Optional additional metadata.

        Returns:
            The ID of the stored message.
        """
        message_id = str(uuid4())
        embedding = await self._embeddings.embed_text(content)

        payload = {
            "user_id": user_id,
            "channel_id": channel_id,
            "role": role,
            "content": content,
            "timestamp": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }

        await self._client.upsert(
            collection_name=CONVERSATIONS_COLLECTION,
            points=[
                qdrant_models.PointStruct(
                    id=message_id,
                    vector=embedding,
                    payload=payload,
                )
            ],
        )

        log.debug(
            "message_stored",
            message_id=message_id,
            user_id=user_id,
            role=role,
        )
        return message_id

    async def store_memory(
        self,
        content: str,
        memory_type: str = "general",
        metadata: dict[str, Any] | None = None,
    ) -> str:
        """Store a long-term memory.

        Args:
            content: Memory content.
            memory_type: Type of memory (preference, fact, decision, etc.).
            metadata: Optional additional metadata.

        Returns:
            The ID of the stored memory.
        """
        memory_id = str(uuid4())
        embedding = await self._embeddings.embed_text(content)

        payload = {
            "content": content,
            "type": memory_type,
            "timestamp": datetime.utcnow().isoformat(),
            **(metadata or {}),
        }

        await self._client.upsert(
            collection_name=LONG_TERM_MEMORY_COLLECTION,
            points=[
                qdrant_models.PointStruct(
                    id=memory_id,
                    vector=embedding,
                    payload=payload,
                )
            ],
        )

        log.info("memory_stored", memory_id=memory_id, type=memory_type)
        return memory_id

    async def search_conversations(
        self,
        query: str,
        user_id: int | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search conversation history.

        Args:
            query: Search query.
            user_id: Optional filter by user ID.
            limit: Maximum number of results.

        Returns:
            List of matching messages with scores.
        """
        query_vector = await self._embeddings.embed_query(query)

        filter_conditions = None
        if user_id is not None:
            filter_conditions = qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="user_id",
                        match=qdrant_models.MatchValue(value=user_id),
                    )
                ]
            )

        results = await self._client.search(  # type: ignore[attr-defined]
            collection_name=CONVERSATIONS_COLLECTION,
            query_vector=query_vector,
            query_filter=filter_conditions,
            limit=limit,
        )

        return [
            {
                "id": str(hit.id),
                "score": hit.score,
                **(hit.payload or {}),
            }
            for hit in results
        ]

    async def search_memories(
        self,
        query: str,
        memory_type: str | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """Search long-term memories.

        Args:
            query: Search query.
            memory_type: Optional filter by memory type.
            limit: Maximum number of results.

        Returns:
            List of matching memories with scores.
        """
        query_vector = await self._embeddings.embed_query(query)

        filter_conditions = None
        if memory_type is not None:
            filter_conditions = qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="type",
                        match=qdrant_models.MatchValue(value=memory_type),
                    )
                ]
            )

        results = await self._client.search(  # type: ignore[attr-defined]
            collection_name=LONG_TERM_MEMORY_COLLECTION,
            query_vector=query_vector,
            query_filter=filter_conditions,
            limit=limit,
        )

        return [
            {
                "id": str(hit.id),
                "score": hit.score,
                **(hit.payload or {}),
            }
            for hit in results
        ]

    async def get_recent_context(
        self,
        user_id: int,
        channel_id: int,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        """Get recent conversation context for a user in a channel.

        Args:
            user_id: Discord user ID.
            channel_id: Discord channel ID.
            limit: Maximum number of messages.

        Returns:
            List of recent messages, oldest first.
        """
        results = await self._client.scroll(
            collection_name=CONVERSATIONS_COLLECTION,
            scroll_filter=qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key="user_id",
                        match=qdrant_models.MatchValue(value=user_id),
                    ),
                    qdrant_models.FieldCondition(
                        key="channel_id",
                        match=qdrant_models.MatchValue(value=channel_id),
                    ),
                ]
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        messages = [
            {
                "id": str(point.id),
                **(point.payload or {}),
            }
            for point in results[0]
        ]

        # Sort by timestamp
        messages.sort(key=lambda m: m.get("timestamp", ""))
        return messages
