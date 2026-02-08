"""Integration tests for user-scoped memory isolation.

Verifies that QdrantMemory correctly applies user_id filters when storing
and searching memories, ensuring one user's data is never returned for
another user. Uses mocked Qdrant and embeddings clients -- the goal is to
test the filtering logic, not actual Qdrant connectivity.
"""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from qdrant_client.http import models as qdrant_models

from zetherion_ai.memory.qdrant import QdrantMemory

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_hit(point_id: str, payload: dict, score: float = 0.9) -> MagicMock:
    """Create a mock Qdrant ScoredPoint."""
    hit = MagicMock()
    hit.id = point_id
    hit.payload = payload
    hit.score = score
    return hit


def _build_memory(user_id: int | None = None) -> QdrantMemory:
    """Build a QdrantMemory with mocked internals.

    Patches ``get_settings`` and ``get_embeddings_client`` so the
    constructor does not require real configuration or services.
    """
    with (
        patch("zetherion_ai.memory.qdrant.get_settings") as mock_settings,
        patch("zetherion_ai.memory.qdrant.get_embeddings_client") as mock_embed_factory,
    ):
        settings = MagicMock()
        settings.qdrant_use_tls = False
        settings.qdrant_host = "localhost"
        settings.qdrant_port = 6333
        settings.qdrant_url = "http://localhost:6333"
        mock_settings.return_value = settings

        embeddings = AsyncMock()
        embeddings.embed_text = AsyncMock(return_value=[0.1] * 768)
        embeddings.embed_query = AsyncMock(return_value=[0.1] * 768)
        mock_embed_factory.return_value = embeddings

        with patch("zetherion_ai.memory.qdrant.AsyncQdrantClient") as mock_client:
            client = AsyncMock()
            client.upsert = AsyncMock()
            client.search = AsyncMock(return_value=[])
            client.scroll = AsyncMock(return_value=([], None))
            client.get_collections = AsyncMock(return_value=MagicMock(collections=[]))
            mock_client.return_value = client

            mem = QdrantMemory()

    # Expose internals for assertions
    mem._test_client = client  # type: ignore[attr-defined]
    mem._test_embeddings = embeddings  # type: ignore[attr-defined]
    return mem


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

USER_1_ID = 111
USER_2_ID = 222


@pytest.mark.integration
async def test_store_memory_includes_user_id_in_payload():
    """Storing a memory with user_id should include user_id in the payload."""
    mem = _build_memory()

    await mem.store_memory(
        content="User 1 likes jazz",
        memory_type="preference",
        user_id=USER_1_ID,
    )

    mem._test_client.upsert.assert_called_once()
    call_kwargs = mem._test_client.upsert.call_args
    points = call_kwargs.kwargs.get("points") or call_kwargs[1].get("points")
    assert len(points) == 1
    payload = points[0].payload
    assert payload["user_id"] == USER_1_ID
    assert payload["content"] == "User 1 likes jazz"


@pytest.mark.integration
async def test_store_memory_without_user_id_omits_field():
    """Storing a memory without user_id should NOT include user_id in the payload."""
    mem = _build_memory()

    await mem.store_memory(
        content="General fact about the world",
        memory_type="fact",
    )

    mem._test_client.upsert.assert_called_once()
    call_kwargs = mem._test_client.upsert.call_args
    points = call_kwargs.kwargs.get("points") or call_kwargs[1].get("points")
    payload = points[0].payload
    assert "user_id" not in payload


@pytest.mark.integration
async def test_search_memories_user_1_filter():
    """Searching as user_1 should pass a FieldCondition filter for user_1."""
    mem = _build_memory()

    # Seed mock search results for user 1
    mem._test_client.search = AsyncMock(
        return_value=[
            _make_hit("p1", {"content": "jazz", "user_id": USER_1_ID, "type": "preference"}),
        ]
    )

    results = await mem.search_memories("music", user_id=USER_1_ID)

    # Verify search was called with the correct filter
    mem._test_client.search.assert_called_once()
    call_kwargs = mem._test_client.search.call_args
    query_filter = call_kwargs.kwargs.get("query_filter") or call_kwargs[1].get("query_filter")

    assert query_filter is not None
    assert isinstance(query_filter, qdrant_models.Filter)
    assert len(query_filter.must) >= 1

    user_condition = [
        c
        for c in query_filter.must
        if isinstance(c, qdrant_models.FieldCondition) and c.key == "user_id"
    ]
    assert len(user_condition) == 1
    assert user_condition[0].match.value == USER_1_ID

    # Verify results come through
    assert len(results) == 1
    assert results[0]["content"] == "jazz"


@pytest.mark.integration
async def test_search_memories_user_2_filter():
    """Searching as user_2 should pass a FieldCondition filter for user_2."""
    mem = _build_memory()

    mem._test_client.search = AsyncMock(
        return_value=[
            _make_hit("p2", {"content": "rock", "user_id": USER_2_ID, "type": "preference"}),
        ]
    )

    results = await mem.search_memories("music", user_id=USER_2_ID)

    call_kwargs = mem._test_client.search.call_args
    query_filter = call_kwargs.kwargs.get("query_filter") or call_kwargs[1].get("query_filter")

    assert query_filter is not None
    user_condition = [
        c
        for c in query_filter.must
        if isinstance(c, qdrant_models.FieldCondition) and c.key == "user_id"
    ]
    assert len(user_condition) == 1
    assert user_condition[0].match.value == USER_2_ID

    assert len(results) == 1
    assert results[0]["content"] == "rock"


@pytest.mark.integration
async def test_search_memories_without_user_id_no_user_filter():
    """Searching without user_id should not apply any user filter."""
    mem = _build_memory()

    mem._test_client.search = AsyncMock(return_value=[])

    await mem.search_memories("anything")

    call_kwargs = mem._test_client.search.call_args
    query_filter = call_kwargs.kwargs.get("query_filter") or call_kwargs[1].get("query_filter")

    assert query_filter is None


@pytest.mark.integration
async def test_search_conversations_user_filter():
    """search_conversations should apply user_id filter when provided."""
    mem = _build_memory()

    mem._test_client.search = AsyncMock(
        return_value=[
            _make_hit("c1", {"content": "hello", "user_id": USER_1_ID, "role": "user"}),
        ]
    )

    results = await mem.search_conversations("hello", user_id=USER_1_ID)

    call_kwargs = mem._test_client.search.call_args
    query_filter = call_kwargs.kwargs.get("query_filter") or call_kwargs[1].get("query_filter")

    assert query_filter is not None
    assert isinstance(query_filter, qdrant_models.Filter)
    user_condition = [
        c
        for c in query_filter.must
        if isinstance(c, qdrant_models.FieldCondition) and c.key == "user_id"
    ]
    assert len(user_condition) == 1
    assert user_condition[0].match.value == USER_1_ID

    assert len(results) == 1


@pytest.mark.integration
async def test_search_conversations_no_user_filter():
    """search_conversations without user_id should not apply user filter."""
    mem = _build_memory()

    mem._test_client.search = AsyncMock(return_value=[])

    await mem.search_conversations("hello")

    call_kwargs = mem._test_client.search.call_args
    query_filter = call_kwargs.kwargs.get("query_filter") or call_kwargs[1].get("query_filter")

    assert query_filter is None


@pytest.mark.integration
async def test_user_isolation_across_stores_and_searches():
    """End-to-end: store for two users, search for each, verify filters differ."""
    mem = _build_memory()

    # Store memory for user 1
    await mem.store_memory("User 1 fact", memory_type="fact", user_id=USER_1_ID)

    first_upsert = mem._test_client.upsert.call_args
    first_points = first_upsert.kwargs.get("points") or first_upsert[1].get("points")
    assert first_points[0].payload["user_id"] == USER_1_ID

    # Store memory for user 2
    await mem.store_memory("User 2 fact", memory_type="fact", user_id=USER_2_ID)

    second_upsert = mem._test_client.upsert.call_args
    second_points = second_upsert.kwargs.get("points") or second_upsert[1].get("points")
    assert second_points[0].payload["user_id"] == USER_2_ID

    # Search as user 1
    mem._test_client.search = AsyncMock(
        return_value=[
            _make_hit("p1", {"content": "User 1 fact", "user_id": USER_1_ID, "type": "fact"}),
        ]
    )
    results_1 = await mem.search_memories("fact", user_id=USER_1_ID)

    filter_1 = mem._test_client.search.call_args.kwargs.get(
        "query_filter"
    ) or mem._test_client.search.call_args[1].get("query_filter")
    user_cond_1 = [
        c
        for c in filter_1.must
        if isinstance(c, qdrant_models.FieldCondition) and c.key == "user_id"
    ]
    assert user_cond_1[0].match.value == USER_1_ID
    assert results_1[0]["content"] == "User 1 fact"

    # Search as user 2
    mem._test_client.search = AsyncMock(
        return_value=[
            _make_hit("p2", {"content": "User 2 fact", "user_id": USER_2_ID, "type": "fact"}),
        ]
    )
    results_2 = await mem.search_memories("fact", user_id=USER_2_ID)

    filter_2 = mem._test_client.search.call_args.kwargs.get(
        "query_filter"
    ) or mem._test_client.search.call_args[1].get("query_filter")
    user_cond_2 = [
        c
        for c in filter_2.must
        if isinstance(c, qdrant_models.FieldCondition) and c.key == "user_id"
    ]
    assert user_cond_2[0].match.value == USER_2_ID
    assert results_2[0]["content"] == "User 2 fact"
