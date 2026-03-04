"""Error models and response helpers for CGS gateway."""

from __future__ import annotations

from typing import Any, Literal

from aiohttp import web

_RETRYABLE_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}
UpstreamErrorSource = Literal["upstream", "skills"]


class GatewayError(Exception):
    """Typed gateway error with stable code + HTTP status."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status: int = 400,
        details: dict[str, Any] | None = None,
        retryable: bool | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}
        self.retryable = retryable


def success_response(request_id: str, data: Any, *, status: int = 200) -> web.Response:
    """Return standard success envelope."""
    return web.json_response(
        {
            "request_id": request_id,
            "data": data,
            "error": None,
        },
        status=status,
    )


def error_response(
    request_id: str,
    *,
    code: str,
    message: str,
    status: int,
    details: dict[str, Any] | None = None,
    retryable: bool | None = None,
) -> web.Response:
    """Return standard error envelope."""
    retryable_value = _is_retryable_status(status) if retryable is None else bool(retryable)
    return web.json_response(
        {
            "request_id": request_id,
            "data": None,
            "error": {
                "code": code,
                "message": message,
                "retryable": retryable_value,
                "details": details or {},
            },
        },
        status=status,
    )


def from_exception(request_id: str, exc: Exception) -> web.Response:
    """Map exceptions to envelope error responses."""
    if isinstance(exc, GatewayError):
        return error_response(
            request_id,
            code=exc.code,
            message=exc.message,
            status=exc.status,
            details=exc.details,
            retryable=exc.retryable,
        )
    return error_response(
        request_id,
        code="AI_INTERNAL_ERROR",
        message="Unexpected gateway error",
        status=500,
        retryable=True,
    )


def _is_retryable_status(status: int) -> bool:
    return status in _RETRYABLE_STATUS_CODES


def _sanitize_upstream_payload(payload: Any) -> dict[str, Any]:
    if isinstance(payload, dict):
        safe: dict[str, Any] = {}
        for key in ("code", "error", "message", "detail", "request_id", "trace_id"):
            value = payload.get(key)
            if value is None:
                continue
            if isinstance(value, str):
                safe[key] = value[:300]
            elif isinstance(value, int | float | bool):
                safe[key] = value
            else:
                safe[key] = str(value)[:300]
        return safe

    text = str(payload).strip()
    if not text:
        return {}
    return {"message": text[:300]}


def map_upstream_error(
    *,
    status: int,
    payload: Any,
    source: UpstreamErrorSource = "upstream",
    message: str | None = None,
) -> GatewayError:
    """Map upstream/skills status codes to stable AI_* gateway errors."""
    details = {"upstream_status": int(status)}
    details.update(_sanitize_upstream_payload(payload))
    retryable = _is_retryable_status(status)

    if source == "skills":
        return GatewayError(
            code="AI_SKILLS_UPSTREAM_ERROR",
            message=message or "Skills upstream request failed",
            status=502,
            details=details,
            retryable=retryable,
        )

    if status == 401:
        return GatewayError(
            code="AI_UPSTREAM_401",
            message="Upstream authentication failed",
            status=401,
            details=details,
            retryable=False,
        )
    if status == 403:
        return GatewayError(
            code="AI_UPSTREAM_403",
            message="Upstream request forbidden",
            status=403,
            details=details,
            retryable=False,
        )
    if status == 404:
        return GatewayError(
            code="AI_UPSTREAM_404",
            message="Upstream resource not found",
            status=404,
            details=details,
            retryable=False,
        )
    if status == 409:
        return GatewayError(
            code="AI_UPSTREAM_409",
            message="Upstream conflict",
            status=409,
            details=details,
            retryable=False,
        )
    if status == 429:
        return GatewayError(
            code="AI_UPSTREAM_429",
            message="Upstream rate limited",
            status=429,
            details=details,
            retryable=True,
        )
    if status >= 500:
        return GatewayError(
            code="AI_UPSTREAM_5XX",
            message="Upstream service unavailable",
            status=503,
            details=details,
            retryable=True,
        )
    return GatewayError(
        code="AI_UPSTREAM_ERROR",
        message="Upstream request failed",
        status=502,
        details=details,
        retryable=retryable,
    )
