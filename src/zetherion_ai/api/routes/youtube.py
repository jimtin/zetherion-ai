"""YouTube skill endpoints for the public API.

All endpoints require X-API-Key authentication (handled by middleware).
The tenant is attached to the request by the auth middleware.

Routes are grouped by function:
  - Channel management (register, list)
  - Data ingestion (videos, comments, stats, documents)
  - Intelligence (analyze, reports)
  - Management (config, replies, tags, health)
  - Strategy (generate, get, history)
  - Assumptions (list, update, validate)
"""

from __future__ import annotations

import json
from typing import Any
from uuid import UUID

from aiohttp import web

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.api.routes.youtube")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_storage(request: web.Request) -> Any:
    """Retrieve the YouTubeStorage from the app."""
    storage = request.app.get("youtube_storage")
    if storage is None:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": "YouTube storage not available"}),
            content_type="application/json",
        )
    return storage


def _get_skill(request: web.Request, name: str) -> Any:
    """Retrieve a YouTube skill from the app."""
    skill = request.app.get(f"youtube_{name}")
    if skill is None:
        raise web.HTTPServiceUnavailable(
            text=json.dumps({"error": f"YouTube {name} skill not available"}),
            content_type="application/json",
        )
    return skill


def _tenant_id(request: web.Request) -> UUID:
    return UUID(str(request["tenant"]["tenant_id"]))


def _channel_id(request: web.Request) -> UUID:
    raw = request.match_info.get("channel_id", "")
    try:
        return UUID(raw)
    except ValueError as err:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "Invalid channel_id"}),
            content_type="application/json",
        ) from err


async def _verify_channel_ownership(request: web.Request, channel_id: UUID) -> dict[str, Any]:
    """Load a channel and verify it belongs to the authenticated tenant."""
    storage = _get_storage(request)
    channel = await storage.get_channel(channel_id)
    if channel is None:
        raise web.HTTPNotFound(
            text=json.dumps({"error": "Channel not found"}),
            content_type="application/json",
        )
    if str(channel["tenant_id"]) != str(_tenant_id(request)):
        raise web.HTTPNotFound(
            text=json.dumps({"error": "Channel not found"}),
            content_type="application/json",
        )
    return channel  # type: ignore[no-any-return]


def _serialise(row: dict[str, Any]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for k, v in row.items():
        if hasattr(v, "isoformat"):
            out[k] = v.isoformat()
        elif hasattr(v, "hex"):
            out[k] = str(v)
        else:
            out[k] = v
    return out


async def _json_body(request: web.Request) -> dict[str, Any]:
    """Parse request JSON body or raise 400 with a stable payload."""
    try:
        data = await request.json()
    except Exception as err:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "Invalid JSON body"}),
            content_type="application/json",
        ) from err
    if not isinstance(data, dict):
        raise web.HTTPBadRequest(
            text=json.dumps({"error": "JSON body must be an object"}),
            content_type="application/json",
        )
    return data


def _query_int(request: web.Request, name: str, default: int, *, minimum: int = 1) -> int:
    """Parse an integer query parameter and raise 400 on invalid input."""
    raw = request.query.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except (TypeError, ValueError) as err:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": f"Invalid integer for '{name}'"}),
            content_type="application/json",
        ) from err
    if value < minimum:
        raise web.HTTPBadRequest(
            text=json.dumps({"error": f"'{name}' must be >= {minimum}"}),
            content_type="application/json",
        )
    return value


# ---------------------------------------------------------------------------
# Channels
# ---------------------------------------------------------------------------


async def handle_register_channel(request: web.Request) -> web.Response:
    """POST /api/v1/youtube/channels — register a YouTube channel."""
    storage = _get_storage(request)
    data = await _json_body(request)

    channel_youtube_id = data.get("channel_youtube_id")
    if not channel_youtube_id:
        return web.json_response({"error": "channel_youtube_id required"}, status=400)

    row = await storage.create_channel(
        tenant_id=_tenant_id(request),
        channel_youtube_id=channel_youtube_id,
        channel_name=data.get("channel_name", ""),
        config=data.get("config"),
    )
    return web.json_response(_serialise(row), status=201)


async def handle_list_channels(request: web.Request) -> web.Response:
    """GET /api/v1/youtube/channels — list tenant's channels."""
    storage = _get_storage(request)
    rows = await storage.list_channels(_tenant_id(request))
    return web.json_response([_serialise(r) for r in rows])


# ---------------------------------------------------------------------------
# Data ingestion
# ---------------------------------------------------------------------------


async def handle_push_videos(request: web.Request) -> web.Response:
    """POST /api/v1/youtube/channels/{channel_id}/videos — push video batch."""
    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    data = await _json_body(request)
    videos = data.get("videos", [])
    if not videos:
        return web.json_response({"error": "videos array required"}, status=400)

    count = await storage.upsert_videos(ch_id, videos)
    return web.json_response({"upserted": count})


async def handle_push_comments(request: web.Request) -> web.Response:
    """POST /api/v1/youtube/channels/{channel_id}/comments — push comment batch."""
    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    data = await _json_body(request)
    comments = data.get("comments", [])
    if not comments:
        return web.json_response({"error": "comments array required"}, status=400)

    count = await storage.upsert_comments(ch_id, comments)
    return web.json_response({"upserted": count})


async def handle_push_stats(request: web.Request) -> web.Response:
    """POST /api/v1/youtube/channels/{channel_id}/stats — push stats snapshot."""
    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    data = await _json_body(request)
    snapshot = data.get("snapshot", data)
    row = await storage.insert_stats(ch_id, snapshot)
    return web.json_response(_serialise(row), status=201)


async def handle_push_document(request: web.Request) -> web.Response:
    """POST /api/v1/youtube/channels/{channel_id}/documents — upload a document."""
    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    data = await _json_body(request)
    title = data.get("title", "")
    content = data.get("content", "")
    doc_type = data.get("doc_type", "")

    if not content:
        return web.json_response({"error": "content required"}, status=400)

    row = await storage.save_document(ch_id, title, content, doc_type)
    return web.json_response(_serialise(row), status=201)


# ---------------------------------------------------------------------------
# Intelligence
# ---------------------------------------------------------------------------


async def handle_trigger_analysis(request: web.Request) -> web.Response:
    """POST /api/v1/youtube/channels/{channel_id}/intelligence/analyze"""
    skill = _get_skill(request, "intelligence")
    ch_id = _channel_id(request)
    channel = await _verify_channel_ownership(request, ch_id)

    report = await skill.run_analysis(ch_id, channel)
    if report is None:
        return web.json_response({"message": "No new data to analyze"}, status=200)
    return web.json_response(report, status=201)


async def handle_get_intelligence(request: web.Request) -> web.Response:
    """GET /api/v1/youtube/channels/{channel_id}/intelligence"""
    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    row = await storage.get_latest_report(ch_id)
    if row is None:
        return web.json_response({"message": "No report available"}, status=404)
    return web.json_response(_serialise(row))


async def handle_intelligence_history(request: web.Request) -> web.Response:
    """GET /api/v1/youtube/channels/{channel_id}/intelligence/history"""
    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    limit = _query_int(request, "limit", 10)
    rows = await storage.get_report_history(ch_id, limit=limit)
    return web.json_response([_serialise(r) for r in rows])


# ---------------------------------------------------------------------------
# Management
# ---------------------------------------------------------------------------


async def handle_get_management_state(request: web.Request) -> web.Response:
    """GET /api/v1/youtube/channels/{channel_id}/management"""
    skill = _get_skill(request, "management")
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    state = await skill.get_management_state(ch_id)
    if state is None:
        return web.json_response({"error": "Channel not found"}, status=404)
    return web.json_response(state.to_dict())


async def handle_configure_management(request: web.Request) -> web.Response:
    """POST /api/v1/youtube/channels/{channel_id}/management/configure"""
    from zetherion_ai.skills.base import SkillRequest

    skill = _get_skill(request, "management")
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    data = await _json_body(request)
    req = SkillRequest(
        intent="yt_configure_management",
        context={
            "channel_id": str(ch_id),
            "answers": data.get("answers", {}),
            "config": data.get("config", {}),
        },
    )
    resp = await skill.handle(req)
    return web.json_response(resp.data, status=200 if resp.success else 400)


async def handle_list_replies(request: web.Request) -> web.Response:
    """GET /api/v1/youtube/channels/{channel_id}/management/replies"""
    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    status_filter = request.query.get("status")
    limit = _query_int(request, "limit", 50)
    rows = await storage.get_reply_drafts(ch_id, status=status_filter, limit=limit)
    return web.json_response([_serialise(r) for r in rows])


async def handle_update_reply(request: web.Request) -> web.Response:
    """PATCH /api/v1/youtube/channels/{channel_id}/management/replies/{reply_id}"""
    from zetherion_ai.skills.base import SkillRequest

    skill = _get_skill(request, "management")
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    reply_id = request.match_info.get("reply_id", "")
    data = await _json_body(request)
    action = data.get("action")  # approve / reject / posted

    if not action:
        return web.json_response({"error": "action required"}, status=400)

    req = SkillRequest(
        intent="yt_review_replies",
        context={
            "channel_id": str(ch_id),
            "reply_id": reply_id,
            "action": action,
        },
    )
    resp = await skill.handle(req)
    return web.json_response(resp.data, status=200 if resp.success else 400)


async def handle_get_tags(request: web.Request) -> web.Response:
    """GET /api/v1/youtube/channels/{channel_id}/management/tags"""
    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    rows = await storage.get_tag_recommendations(ch_id)
    return web.json_response([_serialise(r) for r in rows])


async def handle_channel_health(request: web.Request) -> web.Response:
    """GET /api/v1/youtube/channels/{channel_id}/management/health"""
    from zetherion_ai.skills.base import SkillRequest

    skill = _get_skill(request, "management")
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    req = SkillRequest(
        intent="yt_channel_health",
        context={"channel_id": str(ch_id)},
    )
    resp = await skill.handle(req)
    return web.json_response(resp.data, status=200 if resp.success else 400)


# ---------------------------------------------------------------------------
# Strategy
# ---------------------------------------------------------------------------


async def handle_generate_strategy(request: web.Request) -> web.Response:
    """POST /api/v1/youtube/channels/{channel_id}/strategy/generate"""
    skill = _get_skill(request, "strategy")
    ch_id = _channel_id(request)
    channel = await _verify_channel_ownership(request, ch_id)

    strategy = await skill.generate_strategy(ch_id, channel)
    return web.json_response(strategy, status=201)


async def handle_get_strategy(request: web.Request) -> web.Response:
    """GET /api/v1/youtube/channels/{channel_id}/strategy"""
    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    row = await storage.get_latest_strategy(ch_id)
    if row is None:
        return web.json_response({"message": "No strategy available"}, status=404)
    return web.json_response(_serialise(row))


async def handle_strategy_history(request: web.Request) -> web.Response:
    """GET /api/v1/youtube/channels/{channel_id}/strategy/history"""
    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    limit = _query_int(request, "limit", 10)
    rows = await storage.get_strategy_history(ch_id, limit=limit)
    return web.json_response([_serialise(r) for r in rows])


# ---------------------------------------------------------------------------
# Assumptions
# ---------------------------------------------------------------------------


async def handle_list_assumptions(request: web.Request) -> web.Response:
    """GET /api/v1/youtube/channels/{channel_id}/assumptions"""
    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    rows = await storage.get_assumptions(ch_id)
    return web.json_response([_serialise(r) for r in rows])


async def handle_update_assumption(request: web.Request) -> web.Response:
    """PATCH /api/v1/youtube/channels/{channel_id}/assumptions/{assumption_id}"""
    from zetherion_ai.skills.youtube.assumptions import AssumptionTracker

    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    assumption_id_raw = request.match_info.get("assumption_id", "")
    try:
        assumption_id = UUID(assumption_id_raw)
    except ValueError:
        return web.json_response({"error": "Invalid assumption_id"}, status=400)

    data = await _json_body(request)
    action = data.get("action")  # confirm / invalidate

    tracker = AssumptionTracker(storage)

    if action == "confirm":
        result = await tracker.confirm(assumption_id)
    elif action == "invalidate":
        result = await tracker.invalidate(assumption_id, reason=data.get("reason", ""))
    else:
        return web.json_response({"error": "action must be 'confirm' or 'invalidate'"}, status=400)

    if result is None:
        return web.json_response({"error": "Assumption not found"}, status=404)
    return web.json_response(_serialise(result))


async def handle_validate_assumptions(request: web.Request) -> web.Response:
    """POST /api/v1/youtube/channels/{channel_id}/assumptions/validate"""
    from zetherion_ai.skills.youtube.assumptions import AssumptionTracker

    storage = _get_storage(request)
    ch_id = _channel_id(request)
    await _verify_channel_ownership(request, ch_id)

    tracker = AssumptionTracker(storage)
    stale = await tracker.get_stale()
    # Filter to this channel only
    stale = [a for a in stale if str(a.get("channel_id")) == str(ch_id)]

    return web.json_response(
        {
            "stale_count": len(stale),
            "assumptions": [_serialise(a) for a in stale],
        }
    )


# ---------------------------------------------------------------------------
# Route registration helper
# ---------------------------------------------------------------------------


def register_youtube_routes(app: web.Application) -> None:
    """Register all YouTube API routes on the aiohttp application."""
    prefix = "/api/v1/youtube"
    ch = prefix + "/channels"
    chid = ch + "/{channel_id}"

    # Channels
    app.router.add_post(ch, handle_register_channel)
    app.router.add_get(ch, handle_list_channels)

    # Ingestion
    app.router.add_post(chid + "/videos", handle_push_videos)
    app.router.add_post(chid + "/comments", handle_push_comments)
    app.router.add_post(chid + "/stats", handle_push_stats)
    app.router.add_post(chid + "/documents", handle_push_document)

    # Intelligence
    app.router.add_post(chid + "/intelligence/analyze", handle_trigger_analysis)
    app.router.add_get(chid + "/intelligence", handle_get_intelligence)
    app.router.add_get(chid + "/intelligence/history", handle_intelligence_history)

    # Management
    app.router.add_get(chid + "/management", handle_get_management_state)
    app.router.add_post(chid + "/management/configure", handle_configure_management)
    app.router.add_get(chid + "/management/replies", handle_list_replies)
    app.router.add_patch(chid + "/management/replies/{reply_id}", handle_update_reply)
    app.router.add_get(chid + "/management/tags", handle_get_tags)
    app.router.add_get(chid + "/management/health", handle_channel_health)

    # Strategy
    app.router.add_post(chid + "/strategy/generate", handle_generate_strategy)
    app.router.add_get(chid + "/strategy", handle_get_strategy)
    app.router.add_get(chid + "/strategy/history", handle_strategy_history)

    # Assumptions
    app.router.add_get(chid + "/assumptions", handle_list_assumptions)
    app.router.add_patch(chid + "/assumptions/{assumption_id}", handle_update_assumption)
    app.router.add_post(chid + "/assumptions/validate", handle_validate_assumptions)

    log.info("youtube_routes_registered", prefix=prefix)
