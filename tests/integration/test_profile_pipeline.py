"""Integration tests for the profile extraction to Qdrant storage pipeline.

Validates the full path from ProfileBuilder.process_message through
tier-1 inference to Qdrant persistence via a mocked QdrantMemory
(same pattern as test_user_isolation.py).
"""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from zetherion_ai.memory.qdrant import QdrantMemory
from zetherion_ai.profile.builder import ProfileBuilder
from zetherion_ai.profile.cache import ProfileCache
from zetherion_ai.profile.employment import EmploymentProfile
from zetherion_ai.profile.models import (
    CONFIDENCE_QUEUE_CONFIRM,
    ProfileCategory,
    ProfileEntry,
    ProfileUpdate,
)
from zetherion_ai.profile.storage import ProfileStorage

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _build_memory() -> QdrantMemory:
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
            _empty_response = MagicMock()
            _empty_response.points = []
            client.query_points = AsyncMock(return_value=_empty_response)
            client.scroll = AsyncMock(return_value=([], None))
            client.get_collections = AsyncMock(return_value=MagicMock(collections=[]))
            mock_client.return_value = client

            mem = QdrantMemory()

    # Expose internals for assertions
    mem._test_client = client  # type: ignore[attr-defined]
    mem._test_embeddings = embeddings  # type: ignore[attr-defined]
    return mem


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture()
def mock_memory():
    """Build a QdrantMemory with mocked internals."""
    return _build_memory()


@pytest.fixture()
def builder(mock_memory, tmp_path):
    """Create a ProfileBuilder backed by the mocked QdrantMemory."""
    return ProfileBuilder(
        memory=mock_memory,
        inference_broker=None,
        storage=ProfileStorage(db_path=str(tmp_path / "profiles.db")),
        cache=ProfileCache(),
        tier1_only=True,
    )


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_process_message_persists_name(builder, mock_memory):
    """Processing 'My name is Alice' should persist a profile entry to Qdrant."""
    await builder.process_message("user-1", "My name is Alice")

    # store_memory is called on the real QdrantMemory which delegates to the
    # mocked internal client.  The client.upsert call is what we can inspect.
    assert mock_memory._test_client.upsert.call_count >= 1

    # At least one upsert should carry profile-related content with "name"
    found_profile = False
    for call in mock_memory._test_client.upsert.call_args_list:
        points = call.kwargs.get("points") or call[1].get("points", [])
        for pt in points:
            payload = pt.payload
            if payload.get("type") == "profile" and "name" in payload.get("content", ""):
                found_profile = True
    assert found_profile, "Expected a profile upsert containing 'name'"


@pytest.mark.integration
async def test_persist_includes_user_id(builder, mock_memory):
    """The persisted profile entry metadata should include the correct user_id."""
    await builder.process_message("user-1", "I'm Bob")

    assert mock_memory._test_client.upsert.call_count >= 1

    # The user_id ends up inside the metadata dict which is spread into the
    # payload by store_memory.
    found_user_id = False
    for call in mock_memory._test_client.upsert.call_args_list:
        points = call.kwargs.get("points") or call[1].get("points", [])
        for pt in points:
            payload = pt.payload
            if payload.get("type") == "profile" and payload.get("user_id") == "user-1":
                found_user_id = True
    assert found_user_id, "Expected user_id='user-1' in the upserted payload"


@pytest.mark.integration
async def test_persist_content_format(builder, mock_memory):
    """The stored content string should contain the key and value."""
    await builder.process_message("user-1", "My name is Charlie")

    found = False
    for call in mock_memory._test_client.upsert.call_args_list:
        points = call.kwargs.get("points") or call[1].get("points", [])
        for pt in points:
            content = pt.payload.get("content", "")
            if "name" in content and "Charlie" in content:
                found = True
    assert found, "Stored content should contain the key ('name') and value ('Charlie')"


@pytest.mark.integration
async def test_multiple_updates_from_message(builder, mock_memory):
    """A message with multiple signals should trigger multiple upserts."""
    await builder.process_message(
        "user-1",
        "I'm John, I work as a software developer, and I'm from London",
    )

    # The NAME_PATTERN and ROLE_PATTERN should each produce a user-profile
    # update that gets persisted (both have confidence >= 0.9).
    profile_upserts = 0
    for call in mock_memory._test_client.upsert.call_args_list:
        points = call.kwargs.get("points") or call[1].get("points", [])
        for pt in points:
            if pt.payload.get("type") == "profile":
                profile_upserts += 1

    assert profile_upserts >= 2, f"Expected at least 2 profile upserts, got {profile_upserts}"


@pytest.mark.integration
async def test_high_confidence_auto_applied(builder, mock_memory):
    """A high-confidence name extraction (>= 0.9) should be stored to memory."""
    await builder.process_message("user-1", "My name is Alice")

    # The name update has confidence=0.9 (>= CONFIDENCE_AUTO_APPLY) so it
    # should be auto-applied and persisted.
    profile_upserts = 0
    for call in mock_memory._test_client.upsert.call_args_list:
        points = call.kwargs.get("points") or call[1].get("points", [])
        for pt in points:
            if pt.payload.get("type") == "profile":
                profile_upserts += 1

    assert profile_upserts >= 1, "High-confidence update should be persisted"


@pytest.mark.integration
async def test_low_confidence_discarded(builder, mock_memory):
    """Updates below CONFIDENCE_QUEUE_CONFIRM (0.3) should NOT be persisted."""
    low_conf_update = ProfileUpdate(
        profile="user",
        field_name="hobby",
        action="set",
        value="chess",
        confidence=CONFIDENCE_QUEUE_CONFIRM - 0.1,  # 0.2 -- below threshold
        source_tier=1,
        category=ProfileCategory.PREFERENCES,
    )

    applied = await builder._process_updates("user-1", [low_conf_update])

    # The update should be discarded entirely
    assert len(applied) == 0

    # No profile upserts should have been made
    profile_upserts = 0
    for call in mock_memory._test_client.upsert.call_args_list:
        points = call.kwargs.get("points") or call[1].get("points", [])
        for pt in points:
            if pt.payload.get("type") == "profile":
                profile_upserts += 1

    assert profile_upserts == 0, "Low-confidence update must not be persisted"


@pytest.mark.integration
async def test_load_profile_round_trip(builder, mock_memory):
    """_load_profile should reconstruct ProfileEntry objects from search results."""
    entry_id = str(uuid4())
    now = datetime.now().isoformat()

    # Simulate Qdrant returning a stored profile entry via search
    hit = MagicMock()
    hit.id = "point-1"
    hit.score = 0.95
    hit.payload = {
        "content": "timezone: UTC",
        "type": "profile",
        "metadata": {
            "id": entry_id,
            "user_id": "user-1",
            "category": "preferences",
            "key": "timezone",
            "value": "UTC",
            "confidence": 0.9,
            "source": "conversation",
            "created_at": now,
            "last_confirmed": now,
            "decay_rate": 0.01,
        },
    }
    mock_resp = MagicMock()
    mock_resp.points = [hit]
    mock_memory._test_client.query_points = AsyncMock(return_value=mock_resp)

    entries = await builder._load_profile("user-1")

    assert len(entries) == 1
    assert isinstance(entries[0], ProfileEntry)
    assert entries[0].key == "timezone"
    assert entries[0].value == "UTC"
    assert entries[0].user_id == "user-1"


@pytest.mark.integration
async def test_employment_profile_persistence(builder, mock_memory):
    """save_employment_profile should store to Qdrant and _load should retrieve."""
    profile = EmploymentProfile(user_id="user-1")

    await builder.save_employment_profile(profile)

    # Verify at least one upsert occurred with memory_type=employment_profile
    emp_upserts = 0
    for call in mock_memory._test_client.upsert.call_args_list:
        points = call.kwargs.get("points") or call[1].get("points", [])
        for pt in points:
            if pt.payload.get("type") == "employment_profile":
                emp_upserts += 1
    assert emp_upserts >= 1, "Expected employment_profile upsert"

    # Now set up search to return the stored profile for loading.
    # _load_employment_profile expects results[0].get("metadata", {})
    # to contain the profile dict.  The real search_memories flattens
    # the hit payload into the result dict, so we place a "metadata"
    # key inside the payload so it survives the flattening.
    profile_dict = profile.to_dict()
    hit = MagicMock()
    hit.id = "point-emp"
    hit.score = 0.99
    hit.payload = {
        "content": f"employment profile for {profile.user_id}",
        "type": "employment_profile",
        "metadata": profile_dict,
    }
    mock_resp = MagicMock()
    mock_resp.points = [hit]
    mock_memory._test_client.query_points = AsyncMock(return_value=mock_resp)

    # Clear the cache so it actually loads from "Qdrant"
    builder._cache.invalidate("user-1")

    loaded = await builder._load_employment_profile("user-1")
    assert loaded is not None
    assert isinstance(loaded, EmploymentProfile)
    assert loaded.user_id == "user-1"


@pytest.mark.integration
async def test_two_users_isolated(builder, mock_memory):
    """Profile updates for different users should carry distinct user_ids."""
    await builder.process_message("user-1", "My name is Alice")
    await builder.process_message("user-2", "My name is Bob")

    user_ids_seen: set[str] = set()
    for call in mock_memory._test_client.upsert.call_args_list:
        points = call.kwargs.get("points") or call[1].get("points", [])
        for pt in points:
            uid = pt.payload.get("user_id")
            if uid and pt.payload.get("type") == "profile":
                user_ids_seen.add(uid)

    assert "user-1" in user_ids_seen, "user-1 profile upsert not found"
    assert "user-2" in user_ids_seen, "user-2 profile upsert not found"
