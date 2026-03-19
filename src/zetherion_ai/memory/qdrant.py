"""Qdrant vector database client for memory storage."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models

from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger
from zetherion_ai.memory.embeddings import get_embedding_dimension, get_embeddings_client
from zetherion_ai.security.encryption import FieldEncryptor
from zetherion_ai.trust.data_plane import QdrantStoragePlane, qdrant_storage_plane_for_domain
from zetherion_ai.trust.scope import TrustDomain

log = get_logger("zetherion_ai.memory.qdrant")

# Collection names
CONVERSATIONS_COLLECTION = "conversations"
LONG_TERM_MEMORY_COLLECTION = "long_term_memory"
USER_PROFILES_COLLECTION = "user_profiles"
DOCS_KNOWLEDGE_COLLECTION = "docs_knowledge"
TENANT_DOCUMENTS_COLLECTION = "tenant_documents"
SKILL_CALENDAR_COLLECTION = "skill_calendar"
SKILL_TASKS_COLLECTION = "skill_tasks"
SKILL_MILESTONES_COLLECTION = "skill_milestones"
SKILL_DEV_JOURNAL_COLLECTION = "skill_dev_journal"


@dataclass(frozen=True)
class ScopedCollectionPolicy:
    """Declarative access rules for one Qdrant collection."""

    allowed_domains: tuple[TrustDomain, ...]
    required_payload_keys: tuple[str, ...] = ()
    required_filter_keys: tuple[str, ...] = ()
    allowed_filter_fields: tuple[str, ...] = ()


_SCOPED_COLLECTION_POLICIES: dict[str, ScopedCollectionPolicy] = {
    CONVERSATIONS_COLLECTION: ScopedCollectionPolicy(
        allowed_domains=(TrustDomain.OWNER_PERSONAL, TrustDomain.TENANT_RAW),
        required_payload_keys=("user_id", "channel_id", "role", "content"),
        allowed_filter_fields=("user_id", "channel_id", "role"),
    ),
    LONG_TERM_MEMORY_COLLECTION: ScopedCollectionPolicy(
        allowed_domains=(TrustDomain.OWNER_PERSONAL, TrustDomain.TENANT_RAW),
        required_payload_keys=("content", "type"),
        allowed_filter_fields=("user_id", "type"),
    ),
    USER_PROFILES_COLLECTION: ScopedCollectionPolicy(
        allowed_domains=(TrustDomain.OWNER_PERSONAL, TrustDomain.TENANT_RAW),
        required_payload_keys=("user_id", "category", "key", "value"),
        allowed_filter_fields=("user_id", "category", "key"),
    ),
    DOCS_KNOWLEDGE_COLLECTION: ScopedCollectionPolicy(
        allowed_domains=(TrustDomain.OWNER_PERSONAL, TrustDomain.CONTROL_PLANE),
        required_payload_keys=("content", "source_path", "source_hash", "chunk_index"),
        allowed_filter_fields=("source_path", "source_hash"),
    ),
    TENANT_DOCUMENTS_COLLECTION: ScopedCollectionPolicy(
        allowed_domains=(TrustDomain.TENANT_RAW,),
        required_payload_keys=("tenant_id", "document_id", "content"),
        required_filter_keys=("tenant_id",),
        allowed_filter_fields=("tenant_id", "document_id", "file_name"),
    ),
    SKILL_CALENDAR_COLLECTION: ScopedCollectionPolicy(
        allowed_domains=(TrustDomain.OWNER_PERSONAL,),
        required_payload_keys=("user_id", "title"),
        allowed_filter_fields=("user_id",),
    ),
    SKILL_TASKS_COLLECTION: ScopedCollectionPolicy(
        allowed_domains=(TrustDomain.OWNER_PERSONAL,),
        required_payload_keys=("user_id", "title", "status"),
        allowed_filter_fields=("user_id", "status", "project"),
    ),
    SKILL_MILESTONES_COLLECTION: ScopedCollectionPolicy(
        allowed_domains=(TrustDomain.OWNER_PERSONAL,),
        required_payload_keys=("user_id", "_type"),
        allowed_filter_fields=("user_id", "_type", "platform", "status"),
    ),
    SKILL_DEV_JOURNAL_COLLECTION: ScopedCollectionPolicy(
        allowed_domains=(TrustDomain.OWNER_PERSONAL, TrustDomain.WORKER_ARTIFACT),
        required_payload_keys=("user_id", "entry_type", "title"),
        allowed_filter_fields=("user_id", "fingerprint", "entry_type", "status", "project"),
    ),
}


def _string_override(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    return candidate or None


def _port_override(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, str):
        candidate = value.strip()
        if candidate.isdigit():
            return int(candidate)
    return None


def _tls_override(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        candidate = value.strip().lower()
        if candidate in {"1", "true", "yes", "on"}:
            return True
        if candidate in {"0", "false", "no", "off"}:
            return False
    return None


def _qdrant_connection_settings(
    settings: Any,
    storage_plane: QdrantStoragePlane,
) -> dict[str, Any]:
    """Resolve effective Qdrant connection settings for one storage plane."""

    if storage_plane == QdrantStoragePlane.OWNER:
        host = (
            _string_override(getattr(settings, "qdrant_owner_host", None)) or settings.qdrant_host
        )
        port = _port_override(getattr(settings, "qdrant_owner_port", None))
        use_tls_override = _tls_override(getattr(settings, "qdrant_owner_use_tls", None))
        cert_path = _string_override(
            getattr(settings, "qdrant_owner_cert_path", None)
        ) or _string_override(getattr(settings, "qdrant_cert_path", None))
        url = _string_override(getattr(settings, "qdrant_owner_url", None)) or _string_override(
            getattr(settings, "qdrant_url", None)
        )
    else:
        host = (
            _string_override(getattr(settings, "qdrant_tenant_host", None)) or settings.qdrant_host
        )
        port = _port_override(getattr(settings, "qdrant_tenant_port", None))
        use_tls_override = _tls_override(getattr(settings, "qdrant_tenant_use_tls", None))
        cert_path = _string_override(
            getattr(settings, "qdrant_tenant_cert_path", None)
        ) or _string_override(getattr(settings, "qdrant_cert_path", None))
        url = _string_override(getattr(settings, "qdrant_tenant_url", None)) or _string_override(
            getattr(settings, "qdrant_url", None)
        )

    effective_port = settings.qdrant_port if port is None else port
    use_tls = settings.qdrant_use_tls if use_tls_override is None else use_tls_override
    scheme = "https" if use_tls else "http"
    effective_url = url or f"{scheme}://{host}:{effective_port}"
    return {
        "host": host,
        "port": effective_port,
        "use_tls": use_tls,
        "cert_path": cert_path,
        "url": effective_url,
    }


class QdrantMemory:
    """Vector memory storage using Qdrant."""

    def __init__(
        self,
        encryptor: FieldEncryptor | None = None,
        *,
        trust_domain: TrustDomain = TrustDomain.TENANT_RAW,
    ) -> None:
        """Initialize the Qdrant memory client.

        Args:
            encryptor: Optional field encryptor for sensitive data.
                       If provided, content fields will be encrypted at rest.
            trust_domain: Canonical trust domain that owns this memory surface.
        """
        settings = get_settings()
        self._trust_domain = trust_domain
        self._storage_plane = qdrant_storage_plane_for_domain(trust_domain)
        connection = _qdrant_connection_settings(settings, self._storage_plane)

        if connection["use_tls"]:
            kwargs: dict[str, Any] = {
                "url": connection["url"],
                "https": True,
            }
            if connection["cert_path"]:
                kwargs["verify"] = connection["cert_path"]
            client_cert_path = _string_override(
                getattr(settings, "internal_tls_client_cert_path", None)
            )
            client_key_path = _string_override(getattr(settings, "internal_tls_client_key_path", None))
            if client_cert_path and client_key_path:
                kwargs["cert"] = (client_cert_path, client_key_path)
            self._client = AsyncQdrantClient(**kwargs)
        else:
            self._client = AsyncQdrantClient(
                host=connection["host"],
                port=connection["port"],
            )

        self._embeddings = get_embeddings_client()
        self._encryptor = encryptor
        log.info(
            "qdrant_client_initialized",
            url=connection["url"],
            encryption_enabled=encryptor is not None,
            tls_enabled=connection["use_tls"],
            trust_domain=trust_domain.value,
            storage_plane=self._storage_plane.value,
        )

    def _policy_for_collection(self, collection_name: str) -> ScopedCollectionPolicy:
        policy = _SCOPED_COLLECTION_POLICIES.get(collection_name)
        if policy is None:
            raise ValueError(
                f"Scoped Qdrant access requires a registered collection policy: {collection_name}"
            )
        return policy

    def _validate_collection_domain(
        self,
        collection_name: str,
        *,
        operation: str,
    ) -> ScopedCollectionPolicy:
        policy = self._policy_for_collection(collection_name)
        if self._trust_domain not in policy.allowed_domains:
            allowed = ", ".join(domain.value for domain in policy.allowed_domains)
            raise ValueError(
                "Scoped Qdrant access blocked for "
                f"{operation} on {collection_name}: trust_domain={self._trust_domain.value} "
                f"allowed_domains={allowed}"
            )
        return policy

    @staticmethod
    def _present_keys(values: dict[str, Any]) -> set[str]:
        present: set[str] = set()
        for key, value in values.items():
            if value is None:
                continue
            if isinstance(value, str) and not value.strip():
                continue
            present.add(key)
        return present

    def _validate_payload_keys(
        self,
        collection_name: str,
        policy: ScopedCollectionPolicy,
        payload: dict[str, Any],
    ) -> None:
        if not policy.required_payload_keys:
            return
        present = self._present_keys(payload)
        missing = [key for key in policy.required_payload_keys if key not in present]
        if missing:
            raise ValueError(
                f"Scoped Qdrant payload for {collection_name} is missing required keys: "
                f"{', '.join(missing)}"
            )

    def _validate_filter_dict(
        self,
        collection_name: str,
        policy: ScopedCollectionPolicy,
        filters: dict[str, Any],
    ) -> None:
        present = self._present_keys(filters)
        if policy.required_filter_keys:
            missing = [key for key in policy.required_filter_keys if key not in present]
            if missing:
                raise ValueError(
                    f"Scoped Qdrant filters for {collection_name} are missing required keys: "
                    f"{', '.join(missing)}"
                )
        if policy.allowed_filter_fields:
            disallowed = [key for key in present if key not in policy.allowed_filter_fields]
            if disallowed:
                raise ValueError(
                    f"Scoped Qdrant filters for {collection_name} include disallowed keys: "
                    f"{', '.join(sorted(disallowed))}"
                )

    def _validate_filter_field(
        self,
        collection_name: str,
        policy: ScopedCollectionPolicy,
        field: str,
    ) -> None:
        if policy.allowed_filter_fields and field not in policy.allowed_filter_fields:
            raise ValueError(
                f"Scoped Qdrant field access for {collection_name} does not allow: {field}"
            )

    async def initialize(self) -> None:
        """Initialize collections if they don't exist."""
        await self._ensure_collection(CONVERSATIONS_COLLECTION)
        await self._ensure_collection(LONG_TERM_MEMORY_COLLECTION)
        log.info("qdrant_collections_ready")

    @staticmethod
    def _extract_vector_size(vectors: Any) -> int | None:
        """Extract vector size from Qdrant vector config in multiple shapes."""
        if vectors is None:
            return None

        direct_size = getattr(vectors, "size", None)
        if isinstance(direct_size, int):
            return direct_size

        if isinstance(vectors, dict):
            raw_size = vectors.get("size")
            if isinstance(raw_size, int):
                return raw_size
            for nested in vectors.values():
                nested_size = QdrantMemory._extract_vector_size(nested)
                if nested_size is not None:
                    return nested_size

        return None

    async def _get_collection_vector_size(self, name: str) -> int | None:
        """Return configured vector size for an existing collection if available."""
        info = await self._client.get_collection(collection_name=name)
        vectors = None
        config = getattr(info, "config", None)
        if config is not None:
            params = getattr(config, "params", None)
            if params is not None:
                vectors = getattr(params, "vectors", None)

        if vectors is None and isinstance(info, dict):
            vectors = info.get("config", {}).get("params", {}).get("vectors")

        return self._extract_vector_size(vectors)

    async def _validate_collection_dimension(self, name: str, expected_size: int) -> None:
        """Validate that existing collection dimension matches the configured embedding size."""
        actual_size = await self._get_collection_vector_size(name)
        if actual_size is None:
            log.warning(
                "collection_dimension_unknown",
                name=name,
                expected_size=expected_size,
            )
            return

        if actual_size != expected_size:
            message = (
                f"Collection '{name}' vector size mismatch: expected {expected_size}, "
                f"found {actual_size}. Run scripts/migrate-qdrant-embeddings.py to rebuild."
            )
            log.error(
                "collection_dimension_mismatch",
                name=name,
                expected_size=expected_size,
                actual_size=actual_size,
            )
            raise ValueError(message)

    async def _ensure_collection(self, name: str) -> None:
        """Ensure a collection exists, creating it if necessary."""
        collections = await self._client.get_collections()
        collection_names = [c.name for c in collections.collections]
        expected_size = get_embedding_dimension()

        if name not in collection_names:
            await self._client.create_collection(
                collection_name=name,
                vectors_config=qdrant_models.VectorParams(
                    size=expected_size,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
            log.info("collection_created", name=name)
            return

        await self._validate_collection_dimension(name, expected_size)

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

        response = await self._client.query_points(
            collection_name=CONVERSATIONS_COLLECTION,
            query=query_vector,
            query_filter=filter_conditions,
            limit=limit,
        )

        output = []
        for hit in response.points:
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

        log.debug(
            "conversation_search_complete",
            result_count=len(output),
            top_score=round(output[0]["score"], 3) if output else None,
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

        filter_conditions_list: list[qdrant_models.Condition] = []
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

        response = await self._client.query_points(
            collection_name=LONG_TERM_MEMORY_COLLECTION,
            query=query_vector,
            query_filter=filter_conditions,
            limit=limit,
        )

        output = []
        for hit in response.points:
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

        log.debug(
            "memory_search_complete",
            result_count=len(output),
            top_score=round(output[0]["score"], 3) if output else None,
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
        # Scroll all matching rows so "recent" ordering is deterministic even
        # when backend pagination order differs from timestamp order.
        scroll_filter = qdrant_models.Filter(
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
        )

        points: list[Any] = []
        offset: qdrant_models.ExtendedPointId | None = None
        max_scan = max(limit * 10, 200)

        while True:
            page, next_offset = await self._client.scroll(
                collection_name=CONVERSATIONS_COLLECTION,
                scroll_filter=scroll_filter,
                limit=min(max_scan, 256),
                offset=offset,
                with_payload=True,
                with_vectors=False,
            )
            points.extend(page)
            if next_offset is None or len(points) >= max_scan:
                break
            offset = next_offset

        messages: list[dict[str, Any]] = []
        for point in points:
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

        # Sort by timestamp and cap to latest "limit" records.
        messages.sort(key=lambda m: m.get("timestamp", ""))
        if limit > 0:
            messages = messages[-limit:]
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
        expected_size = vector_size or get_embedding_dimension()

        if name not in collection_names:
            await self._client.create_collection(
                collection_name=name,
                vectors_config=qdrant_models.VectorParams(
                    size=expected_size,
                    distance=qdrant_models.Distance.COSINE,
                ),
            )
            log.info("collection_created", name=name)
            return

        await self._validate_collection_dimension(name, expected_size)

    async def ensure_scoped_collection(
        self,
        collection_name: str,
        vector_size: int | None = None,
    ) -> None:
        """Ensure a registered collection exists for this trust domain."""

        self._validate_collection_domain(collection_name, operation="ensure_collection")
        await self.ensure_collection(collection_name, vector_size=vector_size)

    async def store_scoped_payload(
        self,
        *,
        collection_name: str,
        point_id: str,
        payload: dict[str, Any],
        content_for_embedding: str | None = None,
        text: str | None = None,
    ) -> str:
        """Store a point after validating the collection policy."""

        policy = self._validate_collection_domain(collection_name, operation="store")
        self._validate_payload_keys(collection_name, policy, payload)
        return await self.store_with_payload(
            collection_name=collection_name,
            point_id=point_id,
            payload=payload,
            content_for_embedding=content_for_embedding,
            text=text,
        )

    async def search_scoped_collection(
        self,
        collection_name: str,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Search a registered collection with required scope filters enforced."""

        policy = self._validate_collection_domain(collection_name, operation="search")
        safe_filters = dict(filters or {})
        self._validate_filter_dict(collection_name, policy, safe_filters)
        return await self.search_collection(
            collection_name=collection_name,
            query=query,
            filters=safe_filters or None,
            limit=limit,
            score_threshold=score_threshold,
        )

    async def delete_scoped_by_filters(
        self,
        collection_name: str,
        *,
        filters: dict[str, Any],
    ) -> None:
        """Delete points from a registered collection with validated filters."""

        policy = self._validate_collection_domain(collection_name, operation="delete_by_filters")
        safe_filters = dict(filters)
        self._validate_filter_dict(collection_name, policy, safe_filters)
        await self.delete_by_filters(collection_name, filters=safe_filters)

    async def get_scoped_by_id(
        self,
        collection_name: str,
        point_id: str,
    ) -> dict[str, Any] | None:
        """Retrieve one point from a registered collection."""

        self._validate_collection_domain(collection_name, operation="get_by_id")
        return await self.get_by_id(collection_name, point_id)

    async def filter_scoped_by_field(
        self,
        collection_name: str,
        field: str,
        value: Any,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Filter a registered collection on an approved field."""

        policy = self._validate_collection_domain(collection_name, operation="filter_by_field")
        self._validate_filter_field(collection_name, policy, field)
        return await self.filter_by_field(collection_name, field, value, limit=limit)

    async def delete_scoped_by_field(
        self,
        collection_name: str,
        field: str,
        value: Any,
    ) -> bool:
        """Delete points from a registered collection on an approved field."""

        policy = self._validate_collection_domain(collection_name, operation="delete_by_field")
        self._validate_filter_field(collection_name, policy, field)
        return await self.delete_by_field(collection_name, field, value)

    async def delete_scoped_by_id(
        self,
        collection_name: str,
        point_id: str,
    ) -> bool:
        """Delete one point from a registered collection."""

        self._validate_collection_domain(collection_name, operation="delete_by_id")
        return await self.delete_by_id(collection_name, point_id)

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

    async def search_collection(
        self,
        collection_name: str,
        query: str,
        *,
        filters: dict[str, Any] | None = None,
        limit: int = 10,
        score_threshold: float | None = None,
    ) -> list[dict[str, Any]]:
        """Search any collection by semantic similarity.

        Args:
            collection_name: Collection to query.
            query: Semantic search query.
            filters: Optional exact-match payload filters.
            limit: Maximum results to return.
            score_threshold: Optional minimum similarity score.

        Returns:
            List of matching payloads with ``id`` and ``score``.
        """
        query_vector = await self._embeddings.embed_query(query)

        filter_obj: qdrant_models.Filter | None = None
        if filters:
            conditions: list[qdrant_models.Condition] = []
            for key, value in filters.items():
                conditions.append(
                    qdrant_models.FieldCondition(
                        key=key,
                        match=qdrant_models.MatchValue(value=value),
                    )
                )
            filter_obj = qdrant_models.Filter(must=conditions)

        response = await self._client.query_points(
            collection_name=collection_name,
            query=query_vector,
            query_filter=filter_obj,
            limit=limit,
        )

        output: list[dict[str, Any]] = []
        for hit in response.points:
            payload = hit.payload or {}
            if self._encryptor is not None:
                payload = self._encryptor.decrypt_payload(payload)
            entry = {
                "id": str(hit.id),
                "score": hit.score,
                **payload,
            }
            if score_threshold is not None and float(hit.score) < score_threshold:
                continue
            output.append(entry)

        log.debug(
            "collection_search_complete",
            collection=collection_name,
            result_count=len(output),
            top_score=round(output[0]["score"], 3) if output else None,
        )
        return output

    async def delete_by_filters(
        self,
        collection_name: str,
        *,
        filters: dict[str, Any],
    ) -> None:
        """Delete points in a collection that match exact payload filters.

        Args:
            collection_name: Collection to prune.
            filters: Exact-match payload filters.
        """
        if not filters:
            return

        conditions: list[qdrant_models.Condition] = []
        for key, value in filters.items():
            conditions.append(
                qdrant_models.FieldCondition(
                    key=key,
                    match=qdrant_models.MatchValue(value=value),
                )
            )

        await self._client.delete(
            collection_name=collection_name,
            points_selector=qdrant_models.FilterSelector(
                filter=qdrant_models.Filter(must=conditions)
            ),
        )

        log.debug(
            "collection_points_deleted",
            collection=collection_name,
            filter_keys=sorted(filters.keys()),
        )

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

    async def delete_by_field(
        self,
        collection_name: str,
        field: str,
        value: Any,
    ) -> bool:
        """Delete all points in ``collection_name`` where ``field`` equals ``value``."""
        try:
            await self._client.delete(
                collection_name=collection_name,
                points_selector=qdrant_models.FilterSelector(
                    filter=qdrant_models.Filter(
                        must=[
                            qdrant_models.FieldCondition(
                                key=field,
                                match=qdrant_models.MatchValue(value=value),
                            )
                        ]
                    )
                ),
            )
            log.debug(
                "points_deleted_by_field",
                collection=collection_name,
                field=field,
            )
            return True
        except Exception as e:
            log.error(
                "delete_by_field_failed",
                collection=collection_name,
                field=field,
                error=str(e),
            )
            return False

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
