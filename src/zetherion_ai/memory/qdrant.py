"""Qdrant vector database client for memory storage."""

from datetime import datetime
from typing import Any
from uuid import uuid4

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models

from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger
from zetherion_ai.memory.embeddings import EMBEDDING_DIMENSION, GeminiEmbeddings
from zetherion_ai.security.encryption import FieldEncryptor

log = get_logger("zetherion_ai.memory.qdrant")

# Collection names
CONVERSATIONS_COLLECTION = "conversations"
LONG_TERM_MEMORY_COLLECTION = "long_term_memory"


class QdrantMemory:
    """Vector memory storage using Qdrant."""

    def __init__(self, encryptor: FieldEncryptor | None = None) -> None:
        """Initialize the Qdrant memory client.

        Args:
            encryptor: Optional field encryptor for sensitive data.
                       If provided, content fields will be encrypted at rest.
        """
        settings = get_settings()

        # Configure TLS if enabled
        if settings.qdrant_use_tls:
            # Use URL-based initialization for HTTPS
            self._client = AsyncQdrantClient(
                url=settings.qdrant_url,
                # For self-signed certs, we can optionally provide the cert path
                # If not provided, verification is skipped (internal network only)
                https=True,
            )
        else:
            self._client = AsyncQdrantClient(
                host=settings.qdrant_host,
                port=settings.qdrant_port,
            )

        self._embeddings = GeminiEmbeddings()
        self._encryptor = encryptor
        log.info(
            "qdrant_client_initialized",
            url=settings.qdrant_url,
            encryption_enabled=encryptor is not None,
            tls_enabled=settings.qdrant_use_tls,
        )

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

        # Encrypt sensitive fields if encryptor is configured
        if self._encryptor is not None:
            payload = self._encryptor.encrypt_payload(payload)

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
            encrypted=self._encryptor is not None,
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

        # Encrypt sensitive fields if encryptor is configured
        if self._encryptor is not None:
            payload = self._encryptor.encrypt_payload(payload)

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

        log.info(
            "memory_stored",
            memory_id=memory_id,
            type=memory_type,
            encrypted=self._encryptor is not None,
        )
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

        output = []
        for hit in results:
            payload = hit.payload or {}
            # Decrypt sensitive fields if encryptor is configured
            if self._encryptor is not None:
                payload = self._encryptor.decrypt_payload(payload)
            output.append(
                {
                    "id": str(hit.id),
                    "score": hit.score,
                    **payload,
                }
            )
        return output

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

        output = []
        for hit in results:
            payload = hit.payload or {}
            # Decrypt sensitive fields if encryptor is configured
            if self._encryptor is not None:
                payload = self._encryptor.decrypt_payload(payload)
            output.append(
                {
                    "id": str(hit.id),
                    "score": hit.score,
                    **payload,
                }
            )
        return output

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

        messages = []
        for point in results[0]:
            payload = point.payload or {}
            # Decrypt sensitive fields if encryptor is configured
            if self._encryptor is not None:
                payload = self._encryptor.decrypt_payload(payload)
            messages.append(
                {
                    "id": str(point.id),
                    **payload,
                }
            )

        # Sort by timestamp
        messages.sort(key=lambda m: m.get("timestamp", ""))
        return messages

    async def close(self) -> None:
        """Close the Qdrant client connection."""
        if hasattr(self._client, "close"):
            await self._client.close()
