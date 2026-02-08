"""Qdrant vector database client for memory storage."""

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models

from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger
from zetherion_ai.memory.embeddings import get_embedding_dimension, get_embeddings_client
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
            kwargs: dict[str, Any] = {
                "url": settings.qdrant_url,
                "https": True,
            }
            if settings.qdrant_cert_path:
                kwargs["verify"] = settings.qdrant_cert_path
            self._client = AsyncQdrantClient(**kwargs)
        else:
            self._client = AsyncQdrantClient(
                host=settings.qdrant_host,
                port=settings.qdrant_port,
            )

        self._embeddings = get_embeddings_client()
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
                    size=get_embedding_dimension(),
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
            "timestamp": datetime.now(UTC).isoformat(),
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
        user_id: int | None = None,
    ) -> str:
        """Store a long-term memory.

        Args:
            content: Memory content.
            memory_type: Type of memory (preference, fact, decision, etc.).
            metadata: Optional additional metadata.
            user_id: Optional user ID for user-scoped memories.

        Returns:
            The ID of the stored memory.
        """
        memory_id = str(uuid4())
        embedding = await self._embeddings.embed_text(content)

        payload = {
            "content": content,
            "type": memory_type,
            "timestamp": datetime.now(UTC).isoformat(),
            **(metadata or {}),
        }
        if user_id is not None:
            payload["user_id"] = user_id

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
        user_id: int | None = None,
    ) -> list[dict[str, Any]]:
        """Search long-term memories.

        Args:
            query: Search query.
            memory_type: Optional filter by memory type.
            limit: Maximum number of results.
            user_id: Optional filter by user ID.

        Returns:
            List of matching memories with scores.
        """
        query_vector = await self._embeddings.embed_query(query)

        filter_conditions_list: list[
            qdrant_models.FieldCondition
            | qdrant_models.IsEmptyCondition
            | qdrant_models.IsNullCondition
            | qdrant_models.HasIdCondition
            | qdrant_models.HasVectorCondition
            | qdrant_models.NestedCondition
            | qdrant_models.Filter
        ] = []
        if memory_type is not None:
            filter_conditions_list.append(
                qdrant_models.FieldCondition(
                    key="type",
                    match=qdrant_models.MatchValue(value=memory_type),
                )
            )
        if user_id is not None:
            filter_conditions_list.append(
                qdrant_models.FieldCondition(
                    key="user_id",
                    match=qdrant_models.MatchValue(value=user_id),
                )
            )
        filter_conditions = None
        if filter_conditions_list:
            filter_conditions = qdrant_models.Filter(must=filter_conditions_list)

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

    async def ensure_collection(
        self,
        name: str,
        vector_size: int | None = None,
    ) -> None:
        """Ensure a collection exists, creating it if necessary.

        Public wrapper for skills to create their own collections.

        Args:
            name: Collection name.
            vector_size: Size of vectors (default: auto-detected from backend).
        """
        collections = await self._client.get_collections()
        collection_names = [c.name for c in collections.collections]

        if name not in collection_names:
            await self._client.create_collection(
                collection_name=name,
                vectors_config=qdrant_models.VectorParams(
                    size=vector_size or get_embedding_dimension(),
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
            log.info("collection_created", name=name)

    async def store_with_payload(
        self,
        collection_name: str,
        point_id: str,
        payload: dict[str, Any],
        content_for_embedding: str | None = None,
        text: str | None = None,
    ) -> str:
        """Store a point with payload in a collection.

        Args:
            collection_name: Target collection.
            point_id: Unique ID for the point.
            payload: Payload data to store.
            content_for_embedding: Text to embed. If None, uses payload["content"].
            text: Alias for content_for_embedding.

        Returns:
            The point ID.
        """
        embed_text = text or content_for_embedding or payload.get("content", "")
        embedding = await self._embeddings.embed_text(str(embed_text))

        # Encrypt sensitive fields if encryptor is configured
        if self._encryptor is not None:
            payload = self._encryptor.encrypt_payload(payload)

        await self._client.upsert(
            collection_name=collection_name,
            points=[
                qdrant_models.PointStruct(
                    id=point_id,
                    vector=embedding,
                    payload=payload,
                )
            ],
        )

        log.debug(
            "point_stored",
            collection=collection_name,
            point_id=point_id,
            encrypted=self._encryptor is not None,
        )
        return point_id

    async def get_by_id(
        self,
        collection_name: str,
        point_id: str,
    ) -> dict[str, Any] | None:
        """Get a point by ID.

        Args:
            collection_name: Collection to search.
            point_id: Point ID to retrieve.

        Returns:
            Point payload or None if not found.
        """
        try:
            results = await self._client.retrieve(
                collection_name=collection_name,
                ids=[point_id],
                with_payload=True,
            )
            if results:
                payload = results[0].payload or {}
                if self._encryptor is not None:
                    payload = self._encryptor.decrypt_payload(payload)
                return {"id": str(results[0].id), **payload}
        except Exception as e:
            log.debug("get_by_id_failed", collection=collection_name, id=point_id, error=str(e))
        return None

    async def filter_by_field(
        self,
        collection_name: str,
        field: str,
        value: Any,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Filter points by a field value.

        Args:
            collection_name: Collection to search.
            field: Field name to filter on.
            value: Value to match.
            limit: Maximum results.

        Returns:
            List of matching points.
        """
        results = await self._client.scroll(
            collection_name=collection_name,
            scroll_filter=qdrant_models.Filter(
                must=[
                    qdrant_models.FieldCondition(
                        key=field,
                        match=qdrant_models.MatchValue(value=value),
                    )
                ]
            ),
            limit=limit,
            with_payload=True,
            with_vectors=False,
        )

        output = []
        for point in results[0]:
            payload = point.payload or {}
            if self._encryptor is not None:
                payload = self._encryptor.decrypt_payload(payload)
            output.append({"id": str(point.id), **payload})
        return output

    async def delete_by_id(
        self,
        collection_name: str,
        point_id: str,
    ) -> bool:
        """Delete a point by ID.

        Args:
            collection_name: Collection containing the point.
            point_id: Point ID to delete.

        Returns:
            True if deleted successfully.
        """
        try:
            await self._client.delete(
                collection_name=collection_name,
                points_selector=qdrant_models.PointIdsList(points=[point_id]),
            )
            log.debug("point_deleted", collection=collection_name, point_id=point_id)
            return True
        except Exception as e:
            log.error("delete_failed", collection=collection_name, point_id=point_id, error=str(e))
            return False

    async def close(self) -> None:
        """Close the Qdrant client connection."""
        if hasattr(self._client, "close"):
            await self._client.close()
