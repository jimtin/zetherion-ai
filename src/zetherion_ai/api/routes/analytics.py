"""Analytics and app-watcher routes for tenant public API."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import time
from datetime import UTC, datetime
from typing import Any

from aiohttp import web
from pydantic import ValidationError

from zetherion_ai.analytics import AnalyticsAggregator, RecommendationEngine
from zetherion_ai.api.models import (
    AnalyticsEventBatchRequest,
    RecommendationFeedbackRequest,
    ReplayChunkRequest,
    SessionEndRequest,
)
from zetherion_ai.config import get_settings
from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.api.routes.analytics")


def _serialise(record: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in record.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "hex"):
            out[k] = str(v)
        else:
            out[k] = v
    return out


def _as_datetime(value: str | datetime | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _is_sampled(session_id: str, sample_rate: float) -> bool:
    if sample_rate <= 0:
        return False
    if sample_rate >= 1:
        return True
    digest = hashlib.sha256(session_id.encode()).hexdigest()
    # Deterministic sampling by session ID
    bucket = int(digest[:8], 16) / 0xFFFFFFFF
    return bucket < sample_rate


def _replay_policy(tenant: dict[str, Any]) -> tuple[bool, float]:
    settings = get_settings()
    config = tenant.get("config") or {}
    analytics_cfg = config.get("analytics") if isinstance(config, dict) else {}
    if not isinstance(analytics_cfg, dict):
        analytics_cfg = {}

    enabled = bool(analytics_cfg.get("replay_enabled", settings.analytics_replay_enabled_default))
    sample_rate_raw = analytics_cfg.get(
        "replay_sample_rate", settings.analytics_replay_sample_rate_default
    )
    try:
        sample_rate = float(sample_rate_raw)
    except (TypeError, ValueError):
        sample_rate = settings.analytics_replay_sample_rate_default
    sample_rate = max(0.0, min(1.0, sample_rate))
    return enabled, sample_rate


def _release_signing_secret() -> str:
    settings = get_settings()
    secret = (
        settings.release_marker_signing_secret.get_secret_value()
        if settings.release_marker_signing_secret is not None
        else ""
    )
    return secret.strip()


def _verify_release_signature(
    *,
    tenant_id: str,
    raw_body: str,
    timestamp: str,
    nonce: str,
    signature: str,
    secret: str,
) -> bool:
    canonical = f"{tenant_id}.{timestamp}.{nonce}.{raw_body}"
    expected = hmac.new(
        secret.encode("utf-8"),
        canonical.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    return hmac.compare_digest(expected, signature.lower().strip())


async def handle_analytics_events(request: web.Request) -> web.Response:
    """POST /api/v1/analytics/events (session-auth)."""
    tenant = request["tenant"]
    session = request["session"]
    tenant_manager = request.app["tenant_manager"]

    tenant_id = str(tenant["tenant_id"])
    session_id = str(session["session_id"])
    execution_mode = str(session.get("execution_mode") or request.get("execution_mode") or "live")

    try:
        raw = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    try:
        payload = AnalyticsEventBatchRequest.model_validate(raw)
    except ValidationError as exc:
        return web.json_response(
            {"error": "Validation failed", "details": exc.errors()}, status=400
        )

    web_session = None
    if payload.web_session_id:
        web_session = await tenant_manager.get_web_session(tenant_id, payload.web_session_id)

    replay_enabled, sample_rate = _replay_policy(tenant)
    sampled = _is_sampled(session_id, sample_rate)

    if web_session is None:
        web_session = await tenant_manager.ensure_web_session(
            tenant_id,
            session_id=session_id,
            external_user_id=payload.external_user_id,
            execution_mode=execution_mode,
            consent_replay=bool(payload.consent_replay and replay_enabled),
            replay_sampled=sampled,
            metadata=payload.metadata,
        )

    for event in payload.events:
        await tenant_manager.add_web_event(
            tenant_id,
            web_session_id=event.web_session_id or str(web_session["web_session_id"]),
            session_id=session_id,
            execution_mode=execution_mode,
            event_type=event.event_type,
            event_name=event.event_name,
            page_url=event.page_url,
            element_selector=event.element_selector,
            properties=event.properties,
            occurred_at=event.occurred_at,
        )

    return web.json_response(
        {
            "ok": True,
            "web_session_id": str(web_session["web_session_id"]),
            "ingested": len(payload.events),
            "replay_enabled": replay_enabled,
            "replay_sampled": sampled,
        },
        status=201,
    )


async def handle_replay_chunks(request: web.Request) -> web.Response:
    """POST /api/v1/analytics/replay/chunks (session-auth, consent-gated)."""
    tenant = request["tenant"]
    session = request["session"]
    tenant_manager = request.app["tenant_manager"]

    tenant_id = str(tenant["tenant_id"])
    session_id = str(session["session_id"])
    execution_mode = str(session.get("execution_mode") or request.get("execution_mode") or "live")

    try:
        raw = await request.json()
    except Exception:
        return web.json_response({"error": "Invalid JSON body"}, status=400)

    try:
        payload = ReplayChunkRequest.model_validate(raw)
    except ValidationError as exc:
        return web.json_response(
            {"error": "Validation failed", "details": exc.errors()}, status=400
        )

    replay_enabled, sample_rate = _replay_policy(tenant)
    if not replay_enabled:
        return web.json_response(
            {"error": "Replay capture is disabled for this tenant"}, status=403
        )
    if not payload.consent:
        return web.json_response(
            {"error": "Replay capture requires explicit consent"},
            status=403,
        )

    sampled = _is_sampled(session_id, sample_rate)
    if not sampled and not payload.sampled:
        return web.json_response(
            {"ok": True, "accepted": False, "reason": "not_sampled"}, status=202
        )

    web_session = await tenant_manager.get_web_session(tenant_id, payload.web_session_id)
    if web_session is None:
        web_session = await tenant_manager.ensure_web_session(
            tenant_id,
            session_id=session_id,
            execution_mode=execution_mode,
            consent_replay=True,
            replay_sampled=True,
            metadata={"created_by": "replay_chunk_ingest"},
        )

    latest_chunk = await tenant_manager.get_latest_replay_chunk(
        tenant_id,
        web_session_id=str(web_session["web_session_id"]),
    )
    if latest_chunk is None and payload.sequence_no != 0:
        return web.json_response(
            {
                "error": "Replay chunk out of order",
                "details": "First replay chunk must use sequence_no=0",
            },
            status=409,
        )
    if latest_chunk is not None:
        latest_sequence = int(latest_chunk.get("sequence_no", 0))
        if payload.sequence_no > latest_sequence + 1:
            return web.json_response(
                {
                    "error": "Replay chunk out of order",
                    "details": f"Expected sequence <= {latest_sequence + 1}",
                },
                status=409,
            )

        latest_checksum = str(latest_chunk.get("checksum_sha256") or "").lower()
        incoming_checksum = str(payload.checksum_sha256 or "").lower()
        if (
            payload.sequence_no == latest_sequence
            and latest_checksum
            and incoming_checksum
            and latest_checksum != incoming_checksum
        ):
            return web.json_response(
                {
                    "error": "Replay chunk checksum mismatch",
                    "details": "Existing sequence has a different checksum",
                },
                status=409,
            )

    replay_store = request.app.get("replay_store")
    chunk_data_bytes: bytes | None = None
    if payload.chunk_base64:
        if not replay_store:
            return web.json_response(
                {"error": "Replay byte storage is not configured"},
                status=503,
            )
        try:
            chunk_data_bytes = base64.b64decode(payload.chunk_base64, validate=True)
        except Exception:
            return web.json_response(
                {
                    "error": "Invalid replay chunk encoding",
                    "details": "chunk_base64 must be valid base64",
                },
                status=400,
            )
        if payload.chunk_size_bytes and len(chunk_data_bytes) != payload.chunk_size_bytes:
            return web.json_response(
                {
                    "error": "Replay chunk size mismatch",
                    "details": (
                        f"Declared {payload.chunk_size_bytes} bytes but decoded "
                        f"{len(chunk_data_bytes)}"
                    ),
                },
                status=400,
            )
        if payload.checksum_sha256:
            calculated = hashlib.sha256(chunk_data_bytes).hexdigest()
            if calculated != payload.checksum_sha256.lower():
                return web.json_response(
                    {
                        "error": "Replay chunk checksum mismatch",
                        "details": "Provided checksum does not match data",
                    },
                    status=400,
                )

    row = await tenant_manager.add_replay_chunk(
        tenant_id,
        web_session_id=str(web_session["web_session_id"]),
        sequence_no=payload.sequence_no,
        object_key=payload.object_key,
        checksum_sha256=payload.checksum_sha256,
        chunk_size_bytes=payload.chunk_size_bytes,
        metadata=payload.metadata,
    )

    if chunk_data_bytes is not None and replay_store is not None:
        try:
            await replay_store.put_chunk(payload.object_key, chunk_data_bytes)
        except Exception:
            log.exception(
                "replay_chunk_store_failed",
                tenant_id=tenant_id,
                object_key=payload.object_key,
            )
            return web.json_response(
                {"error": "Replay storage write failed"},
                status=503,
            )

    return web.json_response({"ok": True, "accepted": True, "chunk": _serialise(row)}, status=201)


async def handle_get_replay_chunk(request: web.Request) -> web.Response:
    """GET /api/v1/analytics/replay/chunks/{web_session_id}/{sequence_no}."""
    tenant = request["tenant"]
    tenant_manager = request.app["tenant_manager"]
    replay_store = request.app.get("replay_store")

    tenant_id = str(tenant["tenant_id"])
    web_session_id = request.match_info["web_session_id"]
    sequence_no_raw = request.match_info["sequence_no"]
    include_data = request.query.get("include_data", "false").lower() in {"1", "true", "yes"}

    try:
        sequence_no = int(sequence_no_raw)
    except ValueError:
        return web.json_response({"error": "sequence_no must be an integer"}, status=400)

    chunk = await tenant_manager.get_replay_chunk(
        tenant_id,
        web_session_id=web_session_id,
        sequence_no=sequence_no,
    )
    if chunk is None:
        return web.json_response({"error": "Replay chunk not found"}, status=404)

    response: dict[str, Any] = {"chunk": _serialise(chunk)}
    if include_data:
        if replay_store is None:
            return web.json_response(
                {"error": "Replay byte storage is not configured"},
                status=503,
            )
        data = await replay_store.get_chunk(str(chunk["object_key"]))
        if data is None:
            return web.json_response({"error": "Replay chunk bytes not found"}, status=404)
        response["data_base64"] = base64.b64encode(data).decode("ascii")
        response["encoding"] = "base64"
    return web.json_response(response, status=200)


async def handle_session_end(request: web.Request) -> web.Response:
    """POST /api/v1/analytics/sessions/end (session-auth)."""
    tenant = request["tenant"]
    session = request["session"]
    tenant_manager = request.app["tenant_manager"]

    tenant_id = str(tenant["tenant_id"])
    session_id = str(session["session_id"])
    execution_mode = str(session.get("execution_mode") or request.get("execution_mode") or "live")

    try:
        raw = await request.json()
    except Exception:
        raw = {}

    try:
        payload = SessionEndRequest.model_validate(raw)
    except ValidationError as exc:
        return web.json_response(
            {"error": "Validation failed", "details": exc.errors()}, status=400
        )

    if payload.web_session_id:
        web_session = await tenant_manager.get_web_session(tenant_id, payload.web_session_id)
    else:
        web_session = None

    if web_session is None:
        web_session = await tenant_manager.ensure_web_session(
            tenant_id,
            session_id=session_id,
            execution_mode=execution_mode,
            metadata={"created_by": "session_end"},
        )

    ended = await tenant_manager.end_web_session(
        tenant_id,
        str(web_session["web_session_id"]),
        ended_at=payload.ended_at,
        metadata_patch=payload.metadata,
    )

    aggregator = AnalyticsAggregator(tenant_manager)
    summary = await aggregator.summarize_session(
        tenant_id,
        web_session_id=str(web_session["web_session_id"]),
        session_id=session_id,
        include_test=execution_mode == "test",
    )
    recommendations: list[dict[str, Any]] = []
    release: dict[str, Any] = {"has_release": False, "regression": False}

    if execution_mode != "test":
        funnel = await aggregator.compute_daily_funnel(tenant_id)
        release = await aggregator.detect_release_regression(tenant_id)

        engine = RecommendationEngine(tenant_manager)
        candidates = engine.generate_candidates(
            session_summary=summary,
            funnel_rows=funnel,
            release_regression=release,
        )
        recommendations = await engine.persist_candidates(
            tenant_id,
            candidates,
            source="session_end",
        )

        interaction_summary = (
            f"Web behavior summary: stage={summary.get('funnel_stage')}, "
            f"events={summary.get('event_count', 0)}, converted={summary.get('converted', False)}"
        )
        await tenant_manager.add_interaction(
            tenant_id=tenant_id,
            contact_id=payload.contact_id,
            session_id=session_id,
            interaction_type="web_behavior_summary",
            summary=interaction_summary,
            entities={
                "web_behavior_summary": summary,
                "release_regression": release,
                "recommendation_count": len(recommendations),
            },
            sentiment=None,
            intent="behavior_summary",
            outcome="resolved" if summary.get("converted") else "unresolved",
        )

        if payload.contact_id:
            await tenant_manager.update_contact_custom_fields(
                tenant_id,
                payload.contact_id,
                {
                    "behavior_segment": {
                        "funnel_stage": summary.get("funnel_stage"),
                        "converted": summary.get("converted"),
                        "updated_at": datetime.now(tz=UTC).isoformat(),
                    }
                },
            )

    return web.json_response(
        {
            "ok": True,
            "web_session": _serialise(ended or web_session),
            "summary": summary,
            "recommendations_generated": len(recommendations),
            "execution_mode": execution_mode,
        }
    )


async def handle_get_recommendations(request: web.Request) -> web.Response:
    """GET /api/v1/analytics/recommendations (session-auth)."""
    tenant = request["tenant"]
    tenant_manager = request.app["tenant_manager"]
    execution_mode = str(request.get("execution_mode") or "live")

    tenant_id = str(tenant["tenant_id"])
    status = request.query.get("status")
    try:
        limit = max(1, min(int(request.query.get("limit", "50")), 200))
    except ValueError:
        limit = 50

    if execution_mode == "test":
        return web.json_response({"recommendations": [], "count": 0})

    rows = await tenant_manager.list_recommendations(tenant_id, status=status, limit=limit)
    return web.json_response(
        {
            "recommendations": [_serialise(r) for r in rows],
            "count": len(rows),
        }
    )


async def handle_get_funnel(request: web.Request) -> web.Response:
    """GET /api/v1/analytics/funnel (API-key auth)."""
    tenant = request["tenant"]
    tenant_manager = request.app["tenant_manager"]
    tenant_id = str(tenant["tenant_id"])

    metric_date_raw = request.query.get("metric_date")
    metric_date = None
    if metric_date_raw:
        try:
            metric_date = datetime.fromisoformat(metric_date_raw).date()
        except ValueError:
            return web.json_response(
                {"error": "Invalid metric_date, expected YYYY-MM-DD"},
                status=400,
            )

    try:
        limit = max(1, min(int(request.query.get("limit", "200")), 500))
    except ValueError:
        limit = 200

    rows = await tenant_manager.get_funnel_daily(tenant_id, metric_date=metric_date, limit=limit)
    return web.json_response({"funnel": [_serialise(r) for r in rows], "count": len(rows)})


async def handle_recommendation_feedback(request: web.Request) -> web.Response:
    """POST /api/v1/analytics/recommendations/{recommendation_id}/feedback."""
    tenant = request["tenant"]
    tenant_manager = request.app["tenant_manager"]
    execution_mode = str(request.get("execution_mode") or "live")

    tenant_id = str(tenant["tenant_id"])
    recommendation_id = request.match_info["recommendation_id"]

    if execution_mode == "test":
        return web.json_response(
            {"error": "Recommendation feedback is unavailable in test mode"},
            status=403,
        )

    try:
        raw = await request.json()
    except Exception:
        raw = {}

    try:
        payload = RecommendationFeedbackRequest.model_validate(raw)
    except ValidationError as exc:
        return web.json_response(
            {"error": "Validation failed", "details": exc.errors()}, status=400
        )

    feedback = await tenant_manager.add_recommendation_feedback(
        tenant_id,
        recommendation_id,
        feedback_type=payload.feedback_type,
        note=payload.note,
        actor=payload.actor,
    )
    return web.json_response({"ok": True, "feedback": _serialise(feedback)}, status=201)


async def handle_release_marker(request: web.Request) -> web.Response:
    """POST /api/v1/releases/markers (API-key auth)."""
    tenant = request["tenant"]
    tenant_manager = request.app["tenant_manager"]
    tenant_id = str(tenant["tenant_id"])

    try:
        raw_body = await request.text()
    except Exception:
        raw_body = ""

    if raw_body.strip():
        try:
            parsed = json.loads(raw_body)
            payload = parsed if isinstance(parsed, dict) else {}
        except Exception:
            return web.json_response({"error": "Invalid JSON body"}, status=400)
    else:
        payload = {}

    signing_secret = _release_signing_secret()
    if signing_secret:
        timestamp = request.headers.get("X-Release-Timestamp", "").strip()
        signature = request.headers.get("X-Release-Signature", "").strip().lower()
        nonce = request.headers.get("X-Release-Nonce", "").strip()
        if not timestamp or not signature or not nonce:
            return web.json_response(
                {
                    "error": "Missing release signature headers",
                    "details": (
                        "Required: X-Release-Timestamp, X-Release-Nonce, " "X-Release-Signature"
                    ),
                },
                status=401,
            )

        try:
            timestamp_int = int(timestamp)
        except ValueError:
            return web.json_response({"error": "Invalid X-Release-Timestamp header"}, status=401)

        ttl_seconds = max(1, int(get_settings().release_marker_signature_ttl_seconds))
        age = abs(int(time.time()) - timestamp_int)
        if age > ttl_seconds:
            return web.json_response(
                {
                    "error": "Release signature expired",
                    "details": f"timestamp age {age}s exceeds ttl",
                },
                status=401,
            )

        if not _verify_release_signature(
            tenant_id=tenant_id,
            raw_body=raw_body,
            timestamp=timestamp,
            nonce=nonce,
            signature=signature,
            secret=signing_secret,
        ):
            return web.json_response({"error": "Invalid release signature"}, status=401)

        nonce_registered = await tenant_manager.register_release_nonce(
            tenant_id,
            nonce=nonce,
            signature=signature,
        )
        if not nonce_registered:
            return web.json_response({"error": "Release nonce replay detected"}, status=409)

    row = await tenant_manager.add_release_marker(
        tenant_id,
        source=str(payload.get("source", "api")),
        environment=str(payload.get("environment", "production")),
        commit_sha=payload.get("commit_sha"),
        branch=payload.get("branch"),
        tag_name=payload.get("tag_name"),
        deployed_at=_as_datetime(payload.get("deployed_at")),
        metadata=payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {},
    )

    return web.json_response({"ok": True, "marker": _serialise(row)}, status=201)
