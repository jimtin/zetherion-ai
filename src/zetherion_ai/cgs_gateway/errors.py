"""Error models and response helpers for CGS gateway."""

from __future__ import annotations

from typing import Any

from aiohttp import web


class GatewayError(Exception):
    """Typed gateway error with stable code + HTTP status."""

    def __init__(
        self,
        *,
        code: str,
        message: str,
        status: int = 400,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details or {}


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
) -> web.Response:
    """Return standard error envelope."""
    return web.json_response(
        {
            "request_id": request_id,
            "data": None,
            "error": {
                "code": code,
                "message": message,
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
        )
    return error_response(
        request_id,
        code="AI_INTERNAL_ERROR",
        message="Unexpected gateway error",
        status=500,
    )
