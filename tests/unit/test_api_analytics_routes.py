"""Unit tests for public API analytics route handlers."""

from __future__ import annotations

import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestClient, TestServer

from zetherion_ai.api.routes.analytics import (
    _as_datetime,
    _is_sampled,
    _replay_policy,
    _serialise,
    _verify_release_signature,
    handle_analytics_events,
    handle_get_funnel,
    handle_get_recommendations,
    handle_get_replay_chunk,
    handle_recommendation_feedback,
    handle_release_marker,
    handle_replay_chunks,
    handle_session_end,
)


@pytest_asyncio.fixture()
async def analytics_routes_client():
    """aiohttp TestClient with tenant/session context injected."""
    tenant = {
        "tenant_id": "tenant-1",
        "name": "Test Tenant",
        "config": {"analytics": {"replay_enabled": True, "replay_sample_rate": 1.0}},
    }
    session = {"session_id": "session-1", "tenant_id": "tenant-1"}

    tenant_manager = AsyncMock()
    tenant_manager.get_web_session = AsyncMock(return_value=None)
    tenant_manager.ensure_web_session = AsyncMock(
        return_value={
            "web_session_id": "web-session-1",
            "tenant_id": "tenant-1",
            "session_id": "session-1",
            "consent_replay": True,
            "replay_sampled": True,
            "metadata": {},
        }
    )
    tenant_manager.add_web_event = AsyncMock(return_value={"event_id": "evt-1"})
    tenant_manager.add_replay_chunk = AsyncMock(
        return_value={"chunk_id": "chunk-1", "sequence_no": 0}
    )
    tenant_manager.get_latest_replay_chunk = AsyncMock(return_value=None)
    tenant_manager.get_replay_chunk = AsyncMock(
        return_value={
            "chunk_id": "chunk-1",
            "web_session_id": "web-session-1",
            "sequence_no": 0,
            "object_key": "replay/chunk-0.bin",
            "checksum_sha256": "a" * 64,
            "chunk_size_bytes": 3,
            "metadata": {},
        }
    )
    tenant_manager.end_web_session = AsyncMock(
        return_value={"web_session_id": "web-session-1", "ended_at": datetime.now(UTC)}
    )
    tenant_manager.list_recommendations = AsyncMock(
        return_value=[{"recommendation_id": "rec-1", "title": "Fix form drop-off"}]
    )
    tenant_manager.get_funnel_daily = AsyncMock(
        return_value=[
            {
                "tenant_id": "tenant-1",
                "metric_date": datetime(2026, 2, 27, tzinfo=UTC).date(),
                "funnel_name": "primary",
                "stage_name": "consideration",
                "stage_order": 2,
                "users_count": 12,
                "drop_off_rate": 0.1,
                "conversion_rate": 0.5,
                "metadata": {},
                "updated_at": datetime.now(UTC),
            }
        ]
    )
    tenant_manager.add_recommendation_feedback = AsyncMock(
        return_value={
            "feedback_id": "feedback-1",
            "recommendation_id": "rec-1",
            "feedback_type": "accepted",
        }
    )
    tenant_manager.add_release_marker = AsyncMock(return_value={"marker_id": "m-1"})
    tenant_manager.register_release_nonce = AsyncMock(return_value=True)
    tenant_manager.add_interaction = AsyncMock(return_value={"interaction_id": "int-1"})
    tenant_manager.update_contact_custom_fields = AsyncMock(
        return_value={"contact_id": "contact-1"}
    )

    replay_store = AsyncMock()
    replay_store.get_chunk = AsyncMock(return_value=b"abc")
    replay_store.put_chunk = AsyncMock(return_value=None)
    replay_store.delete_chunk = AsyncMock(return_value=True)

    @web.middleware
    async def inject_context(request: web.Request, handler):
        request["tenant"] = tenant
        request["session"] = session
        return await handler(request)

    app = web.Application(middlewares=[inject_context])
    app["tenant_manager"] = tenant_manager
    app["replay_store"] = replay_store
    app.router.add_post("/api/v1/analytics/events", handle_analytics_events)
    app.router.add_post("/api/v1/analytics/replay/chunks", handle_replay_chunks)
    app.router.add_get(
        "/api/v1/analytics/replay/chunks/{web_session_id}/{sequence_no}",
        handle_get_replay_chunk,
    )
    app.router.add_post("/api/v1/analytics/sessions/end", handle_session_end)
    app.router.add_get("/api/v1/analytics/recommendations", handle_get_recommendations)
    app.router.add_get("/api/v1/analytics/funnel", handle_get_funnel)
    app.router.add_post(
        "/api/v1/analytics/recommendations/{recommendation_id}/feedback",
        handle_recommendation_feedback,
    )
    app.router.add_post("/api/v1/releases/markers", handle_release_marker)

    async with TestClient(TestServer(app)) as client:
        yield client, tenant_manager, replay_store


def test_helper_serialise_and_datetime_parsers() -> None:
    now = datetime.now(UTC)
    serialised = _serialise({"ts": now, "id": uuid4()})
    assert serialised["ts"] == now.isoformat()
    assert isinstance(serialised["id"], str)
    assert _as_datetime(None) is None
    assert _as_datetime(now) == now
    assert _as_datetime("2026-02-25T12:00:00Z") == datetime(2026, 2, 25, 12, 0, 0, tzinfo=UTC)


def test_helper_sampling_logic() -> None:
    assert _is_sampled("session-1", 0.0) is False
    assert _is_sampled("session-1", 1.0) is True
    assert _is_sampled("session-1", 0.5) == _is_sampled("session-1", 0.5)


def test_helper_replay_policy_parsing() -> None:
    mock_settings = MagicMock(
        analytics_replay_enabled_default=False,
        analytics_replay_sample_rate_default=0.25,
    )
    tenant = {"config": {"analytics": {"replay_enabled": True, "replay_sample_rate": "bad"}}}
    with patch("zetherion_ai.api.routes.analytics.get_settings", return_value=mock_settings):
        enabled, sample_rate = _replay_policy(tenant)
    assert enabled is True
    assert sample_rate == 0.25

    tenant_invalid = {"config": {"analytics": ["not-a-dict"]}}
    with patch("zetherion_ai.api.routes.analytics.get_settings", return_value=mock_settings):
        enabled2, sample_rate2 = _replay_policy(tenant_invalid)
    assert enabled2 is False
    assert sample_rate2 == 0.25


def test_helper_release_signature_verification() -> None:
    secret = "secret-key"
    tenant_id = "tenant-1"
    timestamp = "1700000000"
    nonce = "nonce-1"
    raw_body = '{"source":"ci"}'
    canonical = f"{tenant_id}.{timestamp}.{nonce}.{raw_body}"
    signature = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()

    assert (
        _verify_release_signature(
            tenant_id=tenant_id,
            raw_body=raw_body,
            timestamp=timestamp,
            nonce=nonce,
            signature=signature,
            secret=secret,
        )
        is True
    )
    assert (
        _verify_release_signature(
            tenant_id=tenant_id,
            raw_body=raw_body,
            timestamp=timestamp,
            nonce=nonce,
            signature="0" * 64,
            secret=secret,
        )
        is False
    )


@pytest.mark.asyncio
async def test_handle_analytics_events_ingests_batch(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    response = await client.post(
        "/api/v1/analytics/events",
        json={
            "external_user_id": "user-1",
            "consent_replay": True,
            "events": [
                {
                    "event_type": "page_view",
                    "event_name": "page_view",
                    "page_url": "https://example.com/",
                },
                {
                    "event_type": "click",
                    "event_name": "cta_click",
                    "element_selector": "#cta",
                },
            ],
        },
    )
    assert response.status == 201
    payload = await response.json()
    assert payload["ok"] is True
    assert payload["ingested"] == 2
    tenant_manager.ensure_web_session.assert_awaited_once()
    assert tenant_manager.add_web_event.await_count == 2


@pytest.mark.asyncio
async def test_handle_replay_chunks_requires_explicit_consent(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    response = await client.post(
        "/api/v1/analytics/replay/chunks",
        json={
            "web_session_id": "web-session-1",
            "sequence_no": 0,
            "object_key": "replay/chunk-0.bin",
            "chunk_size_bytes": 128,
            "consent": False,
            "sampled": True,
        },
    )
    assert response.status == 403
    tenant_manager.add_replay_chunk.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_replay_chunks_accepts_when_consented(analytics_routes_client) -> None:
    client, tenant_manager, replay_store = analytics_routes_client
    response = await client.post(
        "/api/v1/analytics/replay/chunks",
        json={
            "web_session_id": "web-session-1",
            "sequence_no": 0,
            "object_key": "replay/chunk-0.bin",
            "chunk_size_bytes": 256,
            "checksum_sha256": "a" * 64,
            "consent": True,
            "sampled": True,
        },
    )
    assert response.status == 201
    body = await response.json()
    assert body["ok"] is True
    assert body["accepted"] is True
    tenant_manager.add_replay_chunk.assert_awaited_once()
    replay_store.put_chunk.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_replay_chunks_stores_chunk_bytes_when_provided(
    analytics_routes_client,
) -> None:
    client, tenant_manager, replay_store = analytics_routes_client
    payload_bytes = b"chunk-data"
    response = await client.post(
        "/api/v1/analytics/replay/chunks",
        json={
            "web_session_id": "web-session-1",
            "sequence_no": 0,
            "object_key": "replay/chunk-0.bin",
            "chunk_size_bytes": len(payload_bytes),
            "checksum_sha256": hashlib.sha256(payload_bytes).hexdigest(),
            "chunk_base64": "Y2h1bmstZGF0YQ==",
            "consent": True,
            "sampled": True,
        },
    )
    assert response.status == 201
    replay_store.put_chunk.assert_awaited_once_with("replay/chunk-0.bin", payload_bytes)
    tenant_manager.add_replay_chunk.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_replay_chunks_rejects_out_of_order_first_chunk(
    analytics_routes_client,
) -> None:
    client, tenant_manager, _ = analytics_routes_client
    tenant_manager.get_latest_replay_chunk = AsyncMock(return_value=None)

    response = await client.post(
        "/api/v1/analytics/replay/chunks",
        json={
            "web_session_id": "web-session-1",
            "sequence_no": 3,
            "object_key": "replay/chunk-3.bin",
            "chunk_size_bytes": 256,
            "checksum_sha256": "b" * 64,
            "consent": True,
            "sampled": True,
        },
    )
    assert response.status == 409
    assert tenant_manager.add_replay_chunk.await_count == 0


@pytest.mark.asyncio
async def test_handle_replay_chunks_rejects_checksum_mismatch(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    tenant_manager.get_latest_replay_chunk = AsyncMock(
        return_value={"sequence_no": 5, "checksum_sha256": "1" * 64}
    )

    response = await client.post(
        "/api/v1/analytics/replay/chunks",
        json={
            "web_session_id": "web-session-1",
            "sequence_no": 5,
            "object_key": "replay/chunk-5.bin",
            "chunk_size_bytes": 256,
            "checksum_sha256": "2" * 64,
            "consent": True,
            "sampled": True,
        },
    )
    assert response.status == 409
    assert tenant_manager.add_replay_chunk.await_count == 0


@pytest.mark.asyncio
async def test_handle_session_end_generates_summary_and_enrichment(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client

    aggregator = MagicMock()
    aggregator.summarize_session = AsyncMock(
        return_value={
            "event_count": 10,
            "funnel_stage": "considering",
            "converted": False,
            "events_by_type": {"form_start": 1},
            "friction": {"rage_clicks": 1, "dead_clicks": 0, "js_errors": 0, "api_errors": 0},
        }
    )
    aggregator.compute_daily_funnel = AsyncMock(return_value=[])
    aggregator.detect_release_regression = AsyncMock(
        return_value={"has_release": False, "regression": False}
    )

    engine = MagicMock()
    engine.generate_candidates = MagicMock(return_value=[])
    engine.persist_candidates = AsyncMock(return_value=[])

    with (
        patch("zetherion_ai.api.routes.analytics.AnalyticsAggregator", return_value=aggregator),
        patch("zetherion_ai.api.routes.analytics.RecommendationEngine", return_value=engine),
    ):
        response = await client.post(
            "/api/v1/analytics/sessions/end",
            json={"web_session_id": "web-session-1", "contact_id": "contact-1"},
        )

    assert response.status == 200
    body = await response.json()
    assert body["ok"] is True
    assert body["recommendations_generated"] == 0
    tenant_manager.add_interaction.assert_awaited_once()
    tenant_manager.update_contact_custom_fields.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_get_recommendations_returns_rows(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    response = await client.get("/api/v1/analytics/recommendations?status=open&limit=20")
    assert response.status == 200
    body = await response.json()
    assert body["count"] == 1
    assert body["recommendations"][0]["recommendation_id"] == "rec-1"
    tenant_manager.list_recommendations.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_get_funnel_returns_rows(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    response = await client.get("/api/v1/analytics/funnel?metric_date=2026-02-27&limit=300")
    assert response.status == 200
    body = await response.json()
    assert body["count"] == 1
    assert body["funnel"][0]["stage_name"] == "consideration"
    tenant_manager.get_funnel_daily.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_get_funnel_rejects_invalid_metric_date(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    response = await client.get("/api/v1/analytics/funnel?metric_date=bad-date")
    assert response.status == 400
    tenant_manager.get_funnel_daily.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_recommendation_feedback_records_outcome(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    response = await client.post(
        "/api/v1/analytics/recommendations/rec-1/feedback",
        json={"feedback_type": "accepted", "actor": "operator:test"},
    )
    assert response.status == 201
    body = await response.json()
    assert body["ok"] is True
    assert body["feedback"]["feedback_type"] == "accepted"
    tenant_manager.add_recommendation_feedback.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_get_replay_chunk_returns_metadata_and_bytes(analytics_routes_client) -> None:
    client, tenant_manager, replay_store = analytics_routes_client
    response = await client.get("/api/v1/analytics/replay/chunks/web-session-1/0?include_data=true")
    assert response.status == 200
    body = await response.json()
    assert body["chunk"]["sequence_no"] == 0
    assert body["data_base64"] == "YWJj"
    replay_store.get_chunk.assert_awaited_once_with("replay/chunk-0.bin")
    tenant_manager.get_replay_chunk.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_release_marker_requires_signature_when_configured(
    analytics_routes_client,
) -> None:
    client, tenant_manager, _ = analytics_routes_client
    secret = MagicMock()
    secret.get_secret_value.return_value = "signing-secret"
    mock_settings = MagicMock(
        release_marker_signing_secret=secret,
        release_marker_signature_ttl_seconds=300,
    )
    with patch("zetherion_ai.api.routes.analytics.get_settings", return_value=mock_settings):
        response = await client.post("/api/v1/releases/markers", json={"source": "ci"})

    assert response.status == 401
    tenant_manager.add_release_marker.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_release_marker_accepts_valid_signature(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    secret_value = "signing-secret"
    secret = MagicMock()
    secret.get_secret_value.return_value = secret_value
    mock_settings = MagicMock(
        release_marker_signing_secret=secret,
        release_marker_signature_ttl_seconds=300,
    )

    body = {"source": "ci", "environment": "production", "commit_sha": "abc123"}
    raw = json.dumps(body, separators=(",", ":"))
    timestamp = str(int(time.time()))
    nonce = "nonce-1"
    signature = hmac.new(
        secret_value.encode("utf-8"),
        f"tenant-1.{timestamp}.{nonce}.{raw}".encode(),
        hashlib.sha256,
    ).hexdigest()

    with patch("zetherion_ai.api.routes.analytics.get_settings", return_value=mock_settings):
        response = await client.post(
            "/api/v1/releases/markers",
            data=raw,
            headers={
                "Content-Type": "application/json",
                "X-Release-Timestamp": timestamp,
                "X-Release-Nonce": nonce,
                "X-Release-Signature": signature,
            },
        )

    assert response.status == 201
    tenant_manager.register_release_nonce.assert_awaited_once()
    tenant_manager.add_release_marker.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_release_marker_rejects_replayed_nonce(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    tenant_manager.register_release_nonce = AsyncMock(return_value=False)
    secret_value = "signing-secret"
    secret = MagicMock()
    secret.get_secret_value.return_value = secret_value
    mock_settings = MagicMock(
        release_marker_signing_secret=secret,
        release_marker_signature_ttl_seconds=300,
    )

    body = {"source": "ci"}
    raw = json.dumps(body, separators=(",", ":"))
    timestamp = str(int(time.time()))
    nonce = "nonce-replay"
    signature = hmac.new(
        secret_value.encode("utf-8"),
        f"tenant-1.{timestamp}.{nonce}.{raw}".encode(),
        hashlib.sha256,
    ).hexdigest()

    with patch("zetherion_ai.api.routes.analytics.get_settings", return_value=mock_settings):
        response = await client.post(
            "/api/v1/releases/markers",
            data=raw,
            headers={
                "Content-Type": "application/json",
                "X-Release-Timestamp": timestamp,
                "X-Release-Nonce": nonce,
                "X-Release-Signature": signature,
            },
        )

    assert response.status == 409
    tenant_manager.add_release_marker.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_analytics_events_rejects_invalid_json(analytics_routes_client) -> None:
    client, _, _ = analytics_routes_client
    response = await client.post(
        "/api/v1/analytics/events",
        data="{invalid",
        headers={"Content-Type": "application/json"},
    )
    assert response.status == 400


@pytest.mark.asyncio
async def test_handle_analytics_events_rejects_invalid_payload(analytics_routes_client) -> None:
    client, _, _ = analytics_routes_client
    response = await client.post("/api/v1/analytics/events", json={"events": []})
    assert response.status == 400


@pytest.mark.asyncio
async def test_handle_analytics_events_uses_existing_web_session(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    tenant_manager.get_web_session = AsyncMock(
        return_value={"web_session_id": "web-session-1", "tenant_id": "tenant-1"}
    )
    tenant_manager.ensure_web_session = AsyncMock(
        return_value={"web_session_id": "should-not-be-used"}
    )

    response = await client.post(
        "/api/v1/analytics/events",
        json={
            "web_session_id": "web-session-1",
            "events": [{"event_type": "page_view", "event_name": "page_view"}],
        },
    )
    assert response.status == 201
    tenant_manager.ensure_web_session.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_replay_chunks_returns_not_sampled(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    with patch("zetherion_ai.api.routes.analytics._is_sampled", return_value=False):
        response = await client.post(
            "/api/v1/analytics/replay/chunks",
            json={
                "web_session_id": "web-session-1",
                "sequence_no": 0,
                "object_key": "replay/chunk-0.bin",
                "chunk_size_bytes": 128,
                "consent": True,
                "sampled": False,
            },
        )
    assert response.status == 202
    tenant_manager.add_replay_chunk.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_replay_chunks_rejects_gap_sequence(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    tenant_manager.get_latest_replay_chunk = AsyncMock(
        return_value={"sequence_no": 1, "checksum_sha256": "a" * 64}
    )

    response = await client.post(
        "/api/v1/analytics/replay/chunks",
        json={
            "web_session_id": "web-session-1",
            "sequence_no": 5,
            "object_key": "replay/chunk-5.bin",
            "chunk_size_bytes": 256,
            "checksum_sha256": "a" * 64,
            "consent": True,
            "sampled": True,
        },
    )
    assert response.status == 409
    tenant_manager.add_replay_chunk.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_replay_chunks_rejects_invalid_base64(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    response = await client.post(
        "/api/v1/analytics/replay/chunks",
        json={
            "web_session_id": "web-session-1",
            "sequence_no": 0,
            "object_key": "replay/chunk-0.bin",
            "chunk_size_bytes": 5,
            "checksum_sha256": "a" * 64,
            "chunk_base64": "%%%not-base64%%%",
            "consent": True,
            "sampled": True,
        },
    )
    assert response.status == 400
    tenant_manager.add_replay_chunk.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_replay_chunks_rejects_invalid_json(analytics_routes_client) -> None:
    client, _, _ = analytics_routes_client
    response = await client.post(
        "/api/v1/analytics/replay/chunks",
        data="{not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status == 400


@pytest.mark.asyncio
async def test_handle_replay_chunks_rejects_invalid_schema(analytics_routes_client) -> None:
    client, _, _ = analytics_routes_client
    response = await client.post("/api/v1/analytics/replay/chunks", json={})
    assert response.status == 400


@pytest.mark.asyncio
async def test_handle_replay_chunks_rejects_size_mismatch(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    response = await client.post(
        "/api/v1/analytics/replay/chunks",
        json={
            "web_session_id": "web-session-1",
            "sequence_no": 0,
            "object_key": "replay/chunk-0.bin",
            "chunk_size_bytes": 99,
            "checksum_sha256": hashlib.sha256(b"abc").hexdigest(),
            "chunk_base64": "YWJj",
            "consent": True,
            "sampled": True,
        },
    )
    assert response.status == 400
    tenant_manager.add_replay_chunk.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_replay_chunks_rejects_checksum_mismatch_with_payload(
    analytics_routes_client,
) -> None:
    client, tenant_manager, _ = analytics_routes_client
    response = await client.post(
        "/api/v1/analytics/replay/chunks",
        json={
            "web_session_id": "web-session-1",
            "sequence_no": 0,
            "object_key": "replay/chunk-0.bin",
            "chunk_size_bytes": 3,
            "checksum_sha256": "b" * 64,
            "chunk_base64": "YWJj",
            "consent": True,
            "sampled": True,
        },
    )
    assert response.status == 400
    tenant_manager.add_replay_chunk.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_replay_chunks_returns_503_when_store_write_fails(
    analytics_routes_client,
) -> None:
    client, _, replay_store = analytics_routes_client
    replay_store.put_chunk = AsyncMock(side_effect=RuntimeError("disk-full"))
    response = await client.post(
        "/api/v1/analytics/replay/chunks",
        json={
            "web_session_id": "web-session-1",
            "sequence_no": 0,
            "object_key": "replay/chunk-0.bin",
            "chunk_size_bytes": 3,
            "checksum_sha256": hashlib.sha256(b"abc").hexdigest(),
            "chunk_base64": "YWJj",
            "consent": True,
            "sampled": True,
        },
    )
    assert response.status == 503


@pytest.mark.asyncio
async def test_handle_get_replay_chunk_rejects_invalid_sequence(analytics_routes_client) -> None:
    client, _, _ = analytics_routes_client
    response = await client.get("/api/v1/analytics/replay/chunks/web-session-1/not-an-int")
    assert response.status == 400


@pytest.mark.asyncio
async def test_handle_get_replay_chunk_returns_not_found(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    tenant_manager.get_replay_chunk = AsyncMock(return_value=None)
    response = await client.get("/api/v1/analytics/replay/chunks/web-session-1/0")
    assert response.status == 404


@pytest.mark.asyncio
async def test_handle_get_replay_chunk_returns_503_without_store(analytics_routes_client) -> None:
    client, _, _ = analytics_routes_client
    client.app["replay_store"] = None
    response = await client.get("/api/v1/analytics/replay/chunks/web-session-1/0?include_data=true")
    assert response.status == 503


@pytest.mark.asyncio
async def test_handle_get_replay_chunk_returns_404_when_bytes_missing(
    analytics_routes_client,
) -> None:
    client, _, replay_store = analytics_routes_client
    replay_store.get_chunk = AsyncMock(return_value=None)
    response = await client.get("/api/v1/analytics/replay/chunks/web-session-1/0?include_data=true")
    assert response.status == 404


@pytest.mark.asyncio
async def test_handle_session_end_rejects_invalid_payload(analytics_routes_client) -> None:
    client, _, _ = analytics_routes_client
    response = await client.post("/api/v1/analytics/sessions/end", json={"ended_at": "not-a-date"})
    assert response.status == 400


@pytest.mark.asyncio
async def test_handle_session_end_without_contact_skips_contact_update(
    analytics_routes_client,
) -> None:
    client, tenant_manager, _ = analytics_routes_client
    tenant_manager.update_contact_custom_fields = AsyncMock(
        return_value={"contact_id": "contact-1"}
    )

    aggregator = MagicMock()
    aggregator.summarize_session = AsyncMock(
        return_value={
            "event_count": 1,
            "funnel_stage": "awareness",
            "converted": False,
            "events_by_type": {},
            "friction": {"rage_clicks": 0, "dead_clicks": 0, "js_errors": 0, "api_errors": 0},
        }
    )
    aggregator.compute_daily_funnel = AsyncMock(return_value=[])
    aggregator.detect_release_regression = AsyncMock(return_value={"has_release": False})
    engine = MagicMock()
    engine.generate_candidates = MagicMock(return_value=[])
    engine.persist_candidates = AsyncMock(return_value=[])

    with (
        patch("zetherion_ai.api.routes.analytics.AnalyticsAggregator", return_value=aggregator),
        patch("zetherion_ai.api.routes.analytics.RecommendationEngine", return_value=engine),
    ):
        response = await client.post(
            "/api/v1/analytics/sessions/end",
            json={"web_session_id": "ws"},
        )

    assert response.status == 200
    tenant_manager.update_contact_custom_fields.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_recommendation_feedback_rejects_invalid_payload(
    analytics_routes_client,
) -> None:
    client, _, _ = analytics_routes_client
    response = await client.post(
        "/api/v1/analytics/recommendations/rec-1/feedback",
        json={"feedback_type": ""},
    )
    assert response.status == 400


@pytest.mark.asyncio
async def test_handle_release_marker_rejects_invalid_json_payload(analytics_routes_client) -> None:
    client, _, _ = analytics_routes_client
    response = await client.post(
        "/api/v1/releases/markers",
        data="{not-json",
        headers={"Content-Type": "application/json"},
    )
    assert response.status == 400


@pytest.mark.asyncio
async def test_handle_release_marker_rejects_invalid_timestamp_header(
    analytics_routes_client,
) -> None:
    client, tenant_manager, _ = analytics_routes_client
    secret_value = "signing-secret"
    secret = MagicMock()
    secret.get_secret_value.return_value = secret_value
    mock_settings = MagicMock(
        release_marker_signing_secret=secret,
        release_marker_signature_ttl_seconds=300,
    )

    with patch("zetherion_ai.api.routes.analytics.get_settings", return_value=mock_settings):
        response = await client.post(
            "/api/v1/releases/markers",
            json={"source": "ci"},
            headers={
                "X-Release-Timestamp": "bad-timestamp",
                "X-Release-Nonce": "nonce-1",
                "X-Release-Signature": "0" * 64,
            },
        )

    assert response.status == 401
    tenant_manager.add_release_marker.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_release_marker_rejects_expired_signature(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    secret_value = "signing-secret"
    secret = MagicMock()
    secret.get_secret_value.return_value = secret_value
    mock_settings = MagicMock(
        release_marker_signing_secret=secret,
        release_marker_signature_ttl_seconds=1,
    )

    body = {"source": "ci"}
    raw = json.dumps(body, separators=(",", ":"))
    old_timestamp = str(int(time.time()) - 500)
    nonce = "nonce-old"
    signature = hmac.new(
        secret_value.encode("utf-8"),
        f"tenant-1.{old_timestamp}.{nonce}.{raw}".encode(),
        hashlib.sha256,
    ).hexdigest()

    with patch("zetherion_ai.api.routes.analytics.get_settings", return_value=mock_settings):
        response = await client.post(
            "/api/v1/releases/markers",
            data=raw,
            headers={
                "Content-Type": "application/json",
                "X-Release-Timestamp": old_timestamp,
                "X-Release-Nonce": nonce,
                "X-Release-Signature": signature,
            },
        )

    assert response.status == 401
    tenant_manager.add_release_marker.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_release_marker_rejects_invalid_signature(analytics_routes_client) -> None:
    client, tenant_manager, _ = analytics_routes_client
    secret = MagicMock()
    secret.get_secret_value.return_value = "signing-secret"
    mock_settings = MagicMock(
        release_marker_signing_secret=secret,
        release_marker_signature_ttl_seconds=300,
    )

    with patch("zetherion_ai.api.routes.analytics.get_settings", return_value=mock_settings):
        response = await client.post(
            "/api/v1/releases/markers",
            json={"source": "ci"},
            headers={
                "X-Release-Timestamp": str(int(time.time())),
                "X-Release-Nonce": "nonce-x",
                "X-Release-Signature": "f" * 64,
            },
        )

    assert response.status == 401
    tenant_manager.add_release_marker.assert_not_awaited()
