"""Unit tests for the public API health route."""

from __future__ import annotations

import json
from unittest.mock import MagicMock

import pytest

from zetherion_ai.api.routes.health import handle_health


@pytest.mark.asyncio
async def test_handle_health_returns_expected_payload() -> None:
    response = await handle_health(MagicMock())

    assert response.status == 200
    assert response.content_type == "application/json"
    assert json.loads(response.text) == {"status": "healthy", "version": "0.1.0"}
