"""HTTP integration tests for the YouTube API routes.

Exercises real HTTP communication using ``aiohttp.test_utils.TestClient``
pointed at an in-process TestServer.  The YouTubeStorage, YouTube skills,
and AssumptionTracker are replaced with mocks so no Postgres is needed.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.api.auth import generate_api_key
from zetherion_ai.api.middleware import (
    RateLimiter,
    create_auth_middleware,
    create_rate_limit_middleware,
)
from zetherion_ai.api.routes.youtube import register_youtube_routes
from zetherion_ai.skills.base import SkillResponse

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

JWT_SECRET = "youtube-integration-test-secret"
TENANT_ID = str(uuid.uuid4())
OTHER_TENANT_ID = str(uuid.uuid4())
CHANNEL_ID = str(uuid.uuid4())
CHANNEL_UUID = UUID(CHANNEL_ID)
REPLY_ID = str(uuid.uuid4())
ASSUMPTION_ID = str(uuid.uuid4())


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_tenant(tenant_id: str = TENANT_ID, *, active: bool = True, rpm: int = 60) -> dict:
    """Build a fake tenant dict."""
    return {
        "tenant_id": tenant_id,
        "name": "Test Tenant",
        "domain": "example.com",
        "is_active": active,
        "rate_limit_rpm": rpm,
        "config": {},
    }


def _make_channel(
    channel_id: str = CHANNEL_ID,
    tenant_id: str = TENANT_ID,
) -> dict:
    """Build a fake channel dict."""
    now = datetime.now(UTC)
    return {
        "id": channel_id,
        "tenant_id": tenant_id,
        "channel_youtube_id": "UC_test123",
        "channel_name": "Test Channel",
        "config": {},
        "created_at": now,
        "updated_at": now,
    }


def _make_report(channel_id: str = CHANNEL_ID) -> dict:
    """Build a fake intelligence report dict."""
    now = datetime.now(UTC)
    return {
        "report_id": str(uuid.uuid4()),
        "channel_id": channel_id,
        "report_type": "weekly",
        "report": {"summary": "Test report"},
        "model_used": "test-model",
        "generated_at": now,
    }


def _make_strategy(channel_id: str = CHANNEL_ID) -> dict:
    """Build a fake strategy dict."""
    now = datetime.now(UTC)
    return {
        "strategy_id": str(uuid.uuid4()),
        "channel_id": channel_id,
        "strategy_type": "full",
        "strategy": {"goals": ["grow subscribers"]},
        "model_used": "test-model",
        "generated_at": now,
    }


def _make_assumption(
    channel_id: str = CHANNEL_ID,
    assumption_id: str | None = None,
) -> dict:
    """Build a fake assumption dict."""
    now = datetime.now(UTC)
    return {
        "id": assumption_id or str(uuid.uuid4()),
        "channel_id": channel_id,
        "category": "content",
        "statement": "Channel focuses on tech reviews",
        "evidence": [],
        "confidence": 0.8,
        "source": "inferred",
        "confirmed_at": None,
        "last_validated": now,
        "next_validation": now,
    }


def _make_reply(channel_id: str = CHANNEL_ID) -> dict:
    """Build a fake reply draft dict."""
    now = datetime.now(UTC)
    return {
        "reply_id": str(uuid.uuid4()),
        "channel_id": channel_id,
        "comment_id": "yt_comment_123",
        "video_id": "yt_video_456",
        "original_comment": "Great video!",
        "draft_reply": "Thanks for watching!",
        "confidence": 0.9,
        "category": "positive",
        "status": "pending",
        "auto_approved": False,
        "model_used": "test-model",
        "created_at": now,
        "reviewed_at": None,
        "posted_at": None,
    }


def _make_tag_recommendation(channel_id: str = CHANNEL_ID) -> dict:
    """Build a fake tag recommendation dict."""
    now = datetime.now(UTC)
    return {
        "id": str(uuid.uuid4()),
        "channel_id": channel_id,
        "video_id": "yt_video_456",
        "current_tags": ["tech"],
        "suggested_tags": ["tech", "review", "gadgets"],
        "reason": "Broader reach",
        "status": "pending",
        "created_at": now,
    }


@dataclass
class _FakeManagementState:
    """Minimal stand-in for ManagementState with to_dict()."""

    channel_id: UUID = field(default_factory=uuid.uuid4)

    def to_dict(self) -> dict[str, Any]:
        return {
            "channel_id": str(self.channel_id),
            "updated_at": datetime.now(UTC).isoformat(),
            "onboarding_complete": False,
            "trust": {
                "level": 1,
                "label": "SUPERVISED",
                "stats": {"total": 0, "approved": 0, "rejected": 0, "rate": 0.0},
                "next_level_at": 50,
            },
            "auto_reply": {
                "enabled": False,
                "auto_categories": [],
                "review_categories": [],
                "pending_count": 0,
                "posted_today": 0,
            },
            "health_issues": [],
        }


# ---------------------------------------------------------------------------
# App builder
# ---------------------------------------------------------------------------


def _build_youtube_app(
    tenant_manager: AsyncMock,
    youtube_storage: AsyncMock,
    youtube_skills: dict[str, Any] | None = None,
) -> web.Application:
    """Build an aiohttp app with YouTube routes and mocked dependencies."""
    rate_limiter = RateLimiter()

    app = web.Application(
        middlewares=[
            create_auth_middleware(JWT_SECRET),
            create_rate_limit_middleware(rate_limiter),
        ]
    )

    app["tenant_manager"] = tenant_manager
    app["jwt_secret"] = JWT_SECRET
    app["youtube_storage"] = youtube_storage

    # Register YouTube skills on the app
    for key, skill in (youtube_skills or {}).items():
        app[f"youtube_{key}"] = skill

    register_youtube_routes(app)
    return app


def _make_storage_mock() -> AsyncMock:
    """Create a fully-stubbed YouTubeStorage mock with sensible defaults."""
    storage = AsyncMock()

    channel = _make_channel()
    storage.get_channel = AsyncMock(return_value=channel)
    storage.create_channel = AsyncMock(return_value=channel)
    storage.list_channels = AsyncMock(return_value=[channel])

    # Ingestion
    storage.upsert_videos = AsyncMock(return_value=3)
    storage.upsert_comments = AsyncMock(return_value=5)
    stats_row = {
        "id": str(uuid.uuid4()),
        "channel_id": CHANNEL_ID,
        "snapshot": {},
        "recorded_at": datetime.now(UTC),
    }
    storage.insert_stats = AsyncMock(return_value=stats_row)
    doc_row = {
        "id": str(uuid.uuid4()),
        "channel_id": CHANNEL_ID,
        "title": "doc",
        "content": "body",
        "doc_type": "brand_guide",
        "created_at": datetime.now(UTC),
    }
    storage.save_document = AsyncMock(return_value=doc_row)

    # Intelligence
    storage.get_latest_report = AsyncMock(return_value=_make_report())
    storage.get_report_history = AsyncMock(return_value=[_make_report()])

    # Management
    storage.get_reply_drafts = AsyncMock(return_value=[_make_reply()])
    storage.get_tag_recommendations = AsyncMock(return_value=[_make_tag_recommendation()])

    # Strategy
    storage.get_latest_strategy = AsyncMock(return_value=_make_strategy())
    storage.get_strategy_history = AsyncMock(return_value=[_make_strategy()])

    # Assumptions
    storage.get_assumptions = AsyncMock(return_value=[_make_assumption()])
    updated_assumption = _make_assumption(assumption_id=ASSUMPTION_ID)
    storage.update_assumption = AsyncMock(return_value=updated_assumption)
    storage.get_assumption = AsyncMock(return_value=_make_assumption(assumption_id=ASSUMPTION_ID))
    storage.get_stale_assumptions = AsyncMock(return_value=[_make_assumption()])

    return storage


def _make_skills_mock() -> dict[str, AsyncMock]:
    """Create mocked YouTube skills."""
    intelligence = AsyncMock()
    intelligence.run_analysis = AsyncMock(return_value={"report": "analysis result"})

    management = AsyncMock()
    management.get_management_state = AsyncMock(
        return_value=_FakeManagementState(channel_id=CHANNEL_UUID)
    )
    management.handle = AsyncMock(
        return_value=SkillResponse(
            request_id=uuid.uuid4(),
            success=True,
            data={"status": "ok"},
        )
    )

    strategy = AsyncMock()
    strategy.generate_strategy = AsyncMock(return_value={"strategy": "content plan"})

    return {
        "intelligence": intelligence,
        "management": management,
        "strategy": strategy,
    }


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def yt_client():
    """Provide a TestClient with mocked storage, skills, and TenantManager."""
    tm = AsyncMock()
    _, _, _ = generate_api_key()
    full_key, _, _ = generate_api_key()
    tenant = _make_tenant()

    tm.authenticate_api_key = AsyncMock(return_value=tenant)
    tm.get_tenant = AsyncMock(return_value=tenant)

    storage = _make_storage_mock()
    skills = _make_skills_mock()

    app = _build_youtube_app(tm, storage, skills)
    async with TestClient(TestServer(app)) as client:
        yield client, tm, storage, skills, full_key


# ---------------------------------------------------------------------------
# 1. Channel CRUD
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_register_channel(yt_client):
    """POST /api/v1/youtube/channels creates a channel and returns 201."""
    client, _, storage, _, api_key = yt_client
    resp = await client.post(
        "/api/v1/youtube/channels",
        json={"channel_youtube_id": "UC_new123", "channel_name": "New Channel"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["channel_youtube_id"] == "UC_test123"
    storage.create_channel.assert_called_once()


@pytest.mark.integration
async def test_register_channel_missing_id(yt_client):
    """POST /api/v1/youtube/channels without channel_youtube_id returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.post(
        "/api/v1/youtube/channels",
        json={"channel_name": "No ID"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400
    data = await resp.json()
    assert "channel_youtube_id" in data["error"]


@pytest.mark.integration
async def test_register_channel_invalid_json_returns_400(yt_client):
    """POST /api/v1/youtube/channels with malformed JSON returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.post(
        "/api/v1/youtube/channels",
        data="{bad-json",
        headers={"X-API-Key": api_key, "Content-Type": "application/json"},
    )
    assert resp.status == 400


@pytest.mark.integration
async def test_list_channels(yt_client):
    """GET /api/v1/youtube/channels returns tenant's channels."""
    client, _, storage, _, api_key = yt_client
    resp = await client.get(
        "/api/v1/youtube/channels",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    storage.list_channels.assert_called_once()


# ---------------------------------------------------------------------------
# 2. Data ingestion
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_push_videos(yt_client):
    """POST .../videos upserts a batch and returns count."""
    client, _, storage, _, api_key = yt_client
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/videos",
        json={"videos": [{"video_youtube_id": "v1"}, {"video_youtube_id": "v2"}]},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["upserted"] == 3
    storage.upsert_videos.assert_called_once()


@pytest.mark.integration
async def test_push_videos_empty(yt_client):
    """POST .../videos with empty array returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/videos",
        json={"videos": []},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400


@pytest.mark.integration
async def test_push_comments(yt_client):
    """POST .../comments upserts comments."""
    client, _, storage, _, api_key = yt_client
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/comments",
        json={"comments": [{"text": "nice"}]},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["upserted"] == 5
    storage.upsert_comments.assert_called_once()


@pytest.mark.integration
async def test_push_comments_empty(yt_client):
    """POST .../comments with empty array returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/comments",
        json={"comments": []},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400


@pytest.mark.integration
async def test_push_stats(yt_client):
    """POST .../stats inserts a stats snapshot."""
    client, _, storage, _, api_key = yt_client
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/stats",
        json={"snapshot": {"subscribers": 1000}},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 201
    storage.insert_stats.assert_called_once()


@pytest.mark.integration
async def test_push_document(yt_client):
    """POST .../documents uploads a document."""
    client, _, storage, _, api_key = yt_client
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/documents",
        json={"title": "Brand Guide", "content": "Our brand...", "doc_type": "brand_guide"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 201
    storage.save_document.assert_called_once()


@pytest.mark.integration
async def test_push_document_no_content(yt_client):
    """POST .../documents without content returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/documents",
        json={"title": "Empty Doc"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# 3. Intelligence
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_trigger_analysis(yt_client):
    """POST .../intelligence/analyze triggers analysis and returns 201."""
    client, _, _, skills, api_key = yt_client
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence/analyze",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["report"] == "analysis result"
    skills["intelligence"].run_analysis.assert_called_once()


@pytest.mark.integration
async def test_trigger_analysis_no_new_data(yt_client):
    """POST .../intelligence/analyze when nothing to analyze returns 200."""
    client, _, _, skills, api_key = yt_client
    skills["intelligence"].run_analysis = AsyncMock(return_value=None)
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence/analyze",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert "No new data" in data["message"]


@pytest.mark.integration
async def test_get_intelligence_latest(yt_client):
    """GET .../intelligence returns the latest report."""
    client, _, storage, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert "report_id" in data
    storage.get_latest_report.assert_called_once()


@pytest.mark.integration
async def test_get_intelligence_not_found(yt_client):
    """GET .../intelligence when no report returns 404."""
    client, _, storage, _, api_key = yt_client
    storage.get_latest_report = AsyncMock(return_value=None)
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_intelligence_history(yt_client):
    """GET .../intelligence/history returns report history."""
    client, _, storage, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence/history",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    storage.get_report_history.assert_called_once()


@pytest.mark.integration
async def test_intelligence_history_with_limit(yt_client):
    """GET .../intelligence/history?limit=5 passes limit to storage."""
    client, _, storage, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence/history?limit=5",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    storage.get_report_history.assert_called_once_with(CHANNEL_UUID, limit=5)


@pytest.mark.integration
async def test_intelligence_history_invalid_limit_returns_400(yt_client):
    """GET .../intelligence/history with non-integer limit returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence/history?limit=bad",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# 4. Management
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_get_management_state(yt_client):
    """GET .../management returns the management state."""
    client, _, _, skills, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert "trust" in data
    assert "auto_reply" in data
    skills["management"].get_management_state.assert_called_once()


@pytest.mark.integration
async def test_get_management_state_not_found(yt_client):
    """GET .../management when channel not found returns 404."""
    client, _, _, skills, api_key = yt_client
    skills["management"].get_management_state = AsyncMock(return_value=None)
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_configure_management(yt_client):
    """POST .../management/configure updates management config."""
    client, _, _, skills, api_key = yt_client
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management/configure",
        json={"answers": {"niche": "tech"}, "config": {"auto_reply": True}},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    skills["management"].handle.assert_called_once()


@pytest.mark.integration
async def test_configure_management_failure(yt_client):
    """POST .../management/configure when skill fails returns 400."""
    client, _, _, skills, api_key = yt_client
    skills["management"].handle = AsyncMock(
        return_value=SkillResponse(
            request_id=uuid.uuid4(),
            success=False,
            data={"error": "invalid config"},
        )
    )
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management/configure",
        json={"answers": {}},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400


@pytest.mark.integration
async def test_list_replies(yt_client):
    """GET .../management/replies returns reply drafts."""
    client, _, storage, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management/replies",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    storage.get_reply_drafts.assert_called_once()


@pytest.mark.integration
async def test_list_replies_with_status_filter(yt_client):
    """GET .../management/replies?status=pending passes filter."""
    client, _, storage, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management/replies?status=pending&limit=10",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    storage.get_reply_drafts.assert_called_once_with(CHANNEL_UUID, status="pending", limit=10)


@pytest.mark.integration
async def test_list_replies_invalid_limit_returns_400(yt_client):
    """GET .../management/replies with non-integer limit returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management/replies?limit=nope",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400


@pytest.mark.integration
async def test_update_reply(yt_client):
    """PATCH .../management/replies/{reply_id} updates a reply."""
    client, _, _, skills, api_key = yt_client
    resp = await client.patch(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management/replies/{REPLY_ID}",
        json={"action": "approve"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    skills["management"].handle.assert_called_once()


@pytest.mark.integration
async def test_update_reply_no_action(yt_client):
    """PATCH .../management/replies/{reply_id} without action returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.patch(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management/replies/{REPLY_ID}",
        json={},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400
    data = await resp.json()
    assert "action required" in data["error"]


@pytest.mark.integration
async def test_get_tags(yt_client):
    """GET .../management/tags returns tag recommendations."""
    client, _, storage, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management/tags",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    storage.get_tag_recommendations.assert_called_once()


@pytest.mark.integration
async def test_channel_health(yt_client):
    """GET .../management/health returns channel health data."""
    client, _, _, skills, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management/health",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert data["status"] == "ok"
    skills["management"].handle.assert_called_once()


@pytest.mark.integration
async def test_channel_health_failure(yt_client):
    """GET .../management/health when skill fails returns 400."""
    client, _, _, skills, api_key = yt_client
    skills["management"].handle = AsyncMock(
        return_value=SkillResponse(
            request_id=uuid.uuid4(),
            success=False,
            data={"error": "health check failed"},
        )
    )
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management/health",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# 5. Strategy
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_generate_strategy(yt_client):
    """POST .../strategy/generate creates a strategy and returns 201."""
    client, _, _, skills, api_key = yt_client
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/strategy/generate",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 201
    data = await resp.json()
    assert data["strategy"] == "content plan"
    skills["strategy"].generate_strategy.assert_called_once()


@pytest.mark.integration
async def test_get_latest_strategy(yt_client):
    """GET .../strategy returns the latest strategy."""
    client, _, storage, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/strategy",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert "strategy_id" in data
    storage.get_latest_strategy.assert_called_once()


@pytest.mark.integration
async def test_get_latest_strategy_not_found(yt_client):
    """GET .../strategy when no strategy exists returns 404."""
    client, _, storage, _, api_key = yt_client
    storage.get_latest_strategy = AsyncMock(return_value=None)
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/strategy",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_strategy_history(yt_client):
    """GET .../strategy/history returns strategy history."""
    client, _, storage, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/strategy/history",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    storage.get_strategy_history.assert_called_once()


@pytest.mark.integration
async def test_strategy_history_with_limit(yt_client):
    """GET .../strategy/history?limit=3 passes limit to storage."""
    client, _, storage, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/strategy/history?limit=3",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    storage.get_strategy_history.assert_called_once_with(CHANNEL_UUID, limit=3)


@pytest.mark.integration
async def test_strategy_history_invalid_limit_returns_400(yt_client):
    """GET .../strategy/history with non-integer limit returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/strategy/history?limit=oops",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400


# ---------------------------------------------------------------------------
# 6. Assumptions
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_list_assumptions(yt_client):
    """GET .../assumptions returns channel assumptions."""
    client, _, storage, _, api_key = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/assumptions",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 200
    data = await resp.json()
    assert isinstance(data, list)
    assert len(data) == 1
    storage.get_assumptions.assert_called_once()


@pytest.mark.integration
async def test_update_assumption_confirm(yt_client):
    """PATCH .../assumptions/{id} with action=confirm confirms it."""
    client, _, storage, _, api_key = yt_client
    with patch("zetherion_ai.skills.youtube.assumptions.AssumptionTracker") as mock_tracker:
        tracker_instance = AsyncMock()
        tracker_instance.confirm = AsyncMock(
            return_value=_make_assumption(assumption_id=ASSUMPTION_ID)
        )
        mock_tracker.return_value = tracker_instance

        resp = await client.patch(
            f"/api/v1/youtube/channels/{CHANNEL_ID}/assumptions/{ASSUMPTION_ID}",
            json={"action": "confirm"},
            headers={"X-API-Key": api_key},
        )
        assert resp.status == 200
        tracker_instance.confirm.assert_called_once()


@pytest.mark.integration
async def test_update_assumption_invalidate(yt_client):
    """PATCH .../assumptions/{id} with action=invalidate invalidates it."""
    client, _, storage, _, api_key = yt_client
    with patch("zetherion_ai.skills.youtube.assumptions.AssumptionTracker") as mock_tracker:
        tracker_instance = AsyncMock()
        tracker_instance.invalidate = AsyncMock(
            return_value=_make_assumption(assumption_id=ASSUMPTION_ID)
        )
        mock_tracker.return_value = tracker_instance

        resp = await client.patch(
            f"/api/v1/youtube/channels/{CHANNEL_ID}/assumptions/{ASSUMPTION_ID}",
            json={"action": "invalidate", "reason": "outdated"},
            headers={"X-API-Key": api_key},
        )
        assert resp.status == 200
        tracker_instance.invalidate.assert_called_once()


@pytest.mark.integration
async def test_update_assumption_invalid_action(yt_client):
    """PATCH .../assumptions/{id} with bad action returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.patch(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/assumptions/{ASSUMPTION_ID}",
        json={"action": "delete"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400
    data = await resp.json()
    assert "confirm" in data["error"] or "invalidate" in data["error"]


@pytest.mark.integration
async def test_update_assumption_not_found(yt_client):
    """PATCH .../assumptions/{id} when assumption missing returns 404."""
    client, _, storage, _, api_key = yt_client
    with patch("zetherion_ai.skills.youtube.assumptions.AssumptionTracker") as mock_tracker:
        tracker_instance = AsyncMock()
        tracker_instance.confirm = AsyncMock(return_value=None)
        mock_tracker.return_value = tracker_instance

        resp = await client.patch(
            f"/api/v1/youtube/channels/{CHANNEL_ID}/assumptions/{ASSUMPTION_ID}",
            json={"action": "confirm"},
            headers={"X-API-Key": api_key},
        )
        assert resp.status == 404


@pytest.mark.integration
async def test_update_assumption_invalid_uuid(yt_client):
    """PATCH .../assumptions/{id} with invalid UUID returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.patch(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/assumptions/not-a-uuid",
        json={"action": "confirm"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400


@pytest.mark.integration
async def test_validate_assumptions(yt_client):
    """POST .../assumptions/validate returns stale assumptions."""
    client, _, storage, _, api_key = yt_client
    stale = _make_assumption()
    stale["channel_id"] = CHANNEL_ID
    storage.get_stale_assumptions = AsyncMock(return_value=[stale])

    with patch("zetherion_ai.skills.youtube.assumptions.AssumptionTracker") as mock_tracker:
        tracker_instance = AsyncMock()
        tracker_instance.get_stale = AsyncMock(return_value=[stale])
        mock_tracker.return_value = tracker_instance

        resp = await client.post(
            f"/api/v1/youtube/channels/{CHANNEL_ID}/assumptions/validate",
            headers={"X-API-Key": api_key},
        )
        assert resp.status == 200
        data = await resp.json()
        assert "stale_count" in data
        assert "assumptions" in data


# ---------------------------------------------------------------------------
# 7. Auth enforcement
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_no_api_key_returns_401(yt_client):
    """Request without X-API-Key header returns 401."""
    client, _, _, _, _ = yt_client
    resp = await client.get("/api/v1/youtube/channels")
    assert resp.status == 401
    data = await resp.json()
    assert "Missing" in data["error"]


@pytest.mark.integration
async def test_invalid_api_key_returns_401(yt_client):
    """Request with an invalid API key returns 401."""
    client, tm, _, _, _ = yt_client
    tm.authenticate_api_key = AsyncMock(return_value=None)
    resp = await client.get(
        "/api/v1/youtube/channels",
        headers={"X-API-Key": "sk_live_invalid_key"},
    )
    assert resp.status == 401
    data = await resp.json()
    assert "Invalid" in data["error"]


@pytest.mark.integration
async def test_post_channel_no_auth(yt_client):
    """POST /api/v1/youtube/channels without auth returns 401."""
    client, _, _, _, _ = yt_client
    resp = await client.post(
        "/api/v1/youtube/channels",
        json={"channel_youtube_id": "UC_test"},
    )
    assert resp.status == 401


@pytest.mark.integration
async def test_push_videos_no_auth(yt_client):
    """POST .../videos without auth returns 401."""
    client, _, _, _, _ = yt_client
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/videos",
        json={"videos": [{"id": "v1"}]},
    )
    assert resp.status == 401


@pytest.mark.integration
async def test_intelligence_no_auth(yt_client):
    """GET .../intelligence without auth returns 401."""
    client, _, _, _, _ = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence",
    )
    assert resp.status == 401


@pytest.mark.integration
async def test_management_no_auth(yt_client):
    """GET .../management without auth returns 401."""
    client, _, _, _, _ = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management",
    )
    assert resp.status == 401


@pytest.mark.integration
async def test_strategy_no_auth(yt_client):
    """GET .../strategy without auth returns 401."""
    client, _, _, _, _ = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/strategy",
    )
    assert resp.status == 401


@pytest.mark.integration
async def test_assumptions_no_auth(yt_client):
    """GET .../assumptions without auth returns 401."""
    client, _, _, _, _ = yt_client
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/assumptions",
    )
    assert resp.status == 401


# ---------------------------------------------------------------------------
# 8. Tenant isolation (channel not owned by tenant)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_channel_not_owned_returns_not_found(yt_client):
    """Accessing a channel owned by another tenant returns 404."""
    client, _, storage, _, api_key = yt_client
    # Channel belongs to a different tenant
    storage.get_channel = AsyncMock(return_value=_make_channel(tenant_id=OTHER_TENANT_ID))
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404
    data = await resp.json()
    assert "not found" in data["error"].lower()


@pytest.mark.integration
async def test_push_videos_wrong_tenant(yt_client):
    """POST .../videos for a channel not owned by tenant returns 404."""
    client, _, storage, _, api_key = yt_client
    storage.get_channel = AsyncMock(return_value=_make_channel(tenant_id=OTHER_TENANT_ID))
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/videos",
        json={"videos": [{"id": "v1"}]},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_push_comments_wrong_tenant(yt_client):
    """POST .../comments for a channel not owned by tenant returns 404."""
    client, _, storage, _, api_key = yt_client
    storage.get_channel = AsyncMock(return_value=_make_channel(tenant_id=OTHER_TENANT_ID))
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/comments",
        json={"comments": [{"text": "hi"}]},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_push_stats_wrong_tenant(yt_client):
    """POST .../stats for a channel not owned by tenant returns 404."""
    client, _, storage, _, api_key = yt_client
    storage.get_channel = AsyncMock(return_value=_make_channel(tenant_id=OTHER_TENANT_ID))
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/stats",
        json={"snapshot": {}},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_push_document_wrong_tenant(yt_client):
    """POST .../documents for a channel not owned by tenant returns 404."""
    client, _, storage, _, api_key = yt_client
    storage.get_channel = AsyncMock(return_value=_make_channel(tenant_id=OTHER_TENANT_ID))
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/documents",
        json={"title": "x", "content": "body"},
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_trigger_analysis_wrong_tenant(yt_client):
    """POST .../intelligence/analyze for wrong tenant returns 404."""
    client, _, storage, _, api_key = yt_client
    storage.get_channel = AsyncMock(return_value=_make_channel(tenant_id=OTHER_TENANT_ID))
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence/analyze",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_management_wrong_tenant(yt_client):
    """GET .../management for wrong tenant returns 404."""
    client, _, storage, _, api_key = yt_client
    storage.get_channel = AsyncMock(return_value=_make_channel(tenant_id=OTHER_TENANT_ID))
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_strategy_wrong_tenant(yt_client):
    """GET .../strategy for wrong tenant returns 404."""
    client, _, storage, _, api_key = yt_client
    storage.get_channel = AsyncMock(return_value=_make_channel(tenant_id=OTHER_TENANT_ID))
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/strategy",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_assumptions_wrong_tenant(yt_client):
    """GET .../assumptions for wrong tenant returns 404."""
    client, _, storage, _, api_key = yt_client
    storage.get_channel = AsyncMock(return_value=_make_channel(tenant_id=OTHER_TENANT_ID))
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/assumptions",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_channel_not_found_returns_404(yt_client):
    """Accessing a non-existent channel returns 404."""
    client, _, storage, _, api_key = yt_client
    storage.get_channel = AsyncMock(return_value=None)
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 404


@pytest.mark.integration
async def test_invalid_channel_id_returns_400(yt_client):
    """Accessing a route with an invalid UUID channel_id returns 400."""
    client, _, _, _, api_key = yt_client
    resp = await client.get(
        "/api/v1/youtube/channels/not-a-valid-uuid/intelligence",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 400
    data = await resp.json()
    assert "Invalid channel_id" in data["error"]


# ---------------------------------------------------------------------------
# 9. Service unavailable (no storage / no skill)
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_no_storage_returns_503(yt_client):
    """When youtube_storage is missing, routes return 503."""
    client, _, _, _, api_key = yt_client
    # Remove storage from the app
    del client.app["youtube_storage"]
    resp = await client.get(
        "/api/v1/youtube/channels",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 503


@pytest.mark.integration
async def test_no_intelligence_skill_returns_503(yt_client):
    """When youtube_intelligence skill is missing, analyze returns 503."""
    client, _, _, _, api_key = yt_client
    client.app.pop("youtube_intelligence", None)
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/intelligence/analyze",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 503


@pytest.mark.integration
async def test_no_management_skill_returns_503(yt_client):
    """When youtube_management skill is missing, management returns 503."""
    client, _, _, _, api_key = yt_client
    client.app.pop("youtube_management", None)
    resp = await client.get(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/management",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 503


@pytest.mark.integration
async def test_no_strategy_skill_returns_503(yt_client):
    """When youtube_strategy skill is missing, strategy generate returns 503."""
    client, _, _, _, api_key = yt_client
    client.app.pop("youtube_strategy", None)
    resp = await client.post(
        f"/api/v1/youtube/channels/{CHANNEL_ID}/strategy/generate",
        headers={"X-API-Key": api_key},
    )
    assert resp.status == 503
