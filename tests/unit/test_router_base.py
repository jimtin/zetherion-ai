"""Coverage for the router backend protocol module."""

from __future__ import annotations

import pytest

from zetherion_ai.agent.router_base import RouterBackend


def test_router_backend_protocol_exposes_expected_methods() -> None:
    annotations = RouterBackend.__dict__["__annotations__"]
    assert annotations == {}
    assert hasattr(RouterBackend, "classify")
    assert hasattr(RouterBackend, "generate_simple_response")
    assert hasattr(RouterBackend, "health_check")


@pytest.mark.asyncio
async def test_router_backend_protocol_stub_coroutines_are_awaitable() -> None:
    assert await RouterBackend.classify(None, "hello") is None
    assert await RouterBackend.generate_simple_response(None, "hi") is None
    assert await RouterBackend.health_check(None) is None
