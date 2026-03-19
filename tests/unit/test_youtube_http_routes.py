"""Run the mocked YouTube HTTP route suite in the unit lane.

These tests use an in-process aiohttp TestServer plus mocked storage/skills.
They do not require external services, so we mirror them into the unit lane to
count route coverage in the canonical unit-full gate.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Any

from tests.integration import test_youtube_http as integration_youtube_http

yt_client = integration_youtube_http.yt_client


def _copy_non_integration_marks(target: Callable[..., Any]) -> list[object]:
    return [
        mark
        for mark in getattr(target, "pytestmark", [])
        if getattr(mark, "name", None) != "integration"
    ]


def _make_wrapper(target: Callable[..., Any]) -> Callable[..., Any]:
    if inspect.iscoroutinefunction(target):

        async def wrapped(*args: Any, __target: Callable[..., Any] = target, **kwargs: Any) -> Any:
            return await __target(*args, **kwargs)

    else:

        def wrapped(*args: Any, __target: Callable[..., Any] = target, **kwargs: Any) -> Any:
            return __target(*args, **kwargs)

    wrapped.__name__ = target.__name__
    wrapped.__qualname__ = target.__name__
    wrapped.__doc__ = target.__doc__
    wrapped.__module__ = __name__
    wrapped.__signature__ = inspect.signature(target)
    marks = _copy_non_integration_marks(target)
    if marks:
        wrapped.pytestmark = marks
    return wrapped


for _name in dir(integration_youtube_http):
    if not _name.startswith("test_"):
        continue
    _target = getattr(integration_youtube_http, _name)
    if callable(_target):
        globals()[_name] = _make_wrapper(_target)
