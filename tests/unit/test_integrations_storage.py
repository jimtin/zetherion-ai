"""Unit tests for provider-agnostic IntegrationStorage JSON handling."""

from __future__ import annotations

import json
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.integrations.storage import IntegrationStorage
from zetherion_ai.routing.models import DestinationType


def _make_mock_pool():
    """Build a mock asyncpg pool with an acquirable connection."""
    pool = AsyncMock()
    conn = AsyncMock()

    acq_cm = AsyncMock()
    acq_cm.__aenter__.return_value = conn
    pool.acquire = MagicMock(return_value=acq_cm)

    tx_cm = AsyncMock()
    tx_cm.__aenter__.return_value = conn
    conn.transaction = MagicMock(return_value=tx_cm)

    return pool, conn


class TestJsonEncoding:
    """Ensure JSONB payloads are encoded for asyncpg."""

    @pytest.mark.asyncio
    async def test_upsert_account_serializes_metadata(self):
        """upsert_account passes JSON string metadata to conn.execute."""
        pool, conn = _make_mock_pool()
        storage = IntegrationStorage(pool)
        conn.execute.return_value = "INSERT 0 1"

        metadata = {"account_email": "jane@example.com"}
        await storage.upsert_account(
            user_id=123,
            provider="google",
            account_ref="acc-1",
            email="jane@example.com",
            scopes=["scope-a"],
            metadata=metadata,
        )

        args = conn.execute.await_args.args
        assert "integration_accounts" in args[0]
        assert isinstance(args[7], str)
        assert json.loads(args[7]) == metadata

    @pytest.mark.asyncio
    async def test_store_email_message_serializes_metadata(self):
        """store_email_message passes JSON string metadata to conn.execute."""
        pool, conn = _make_mock_pool()
        storage = IntegrationStorage(pool)
        conn.execute.return_value = "INSERT 0 1"

        metadata = {"foo": "bar"}
        await storage.store_email_message(
            user_id=123,
            provider="google",
            account_ref="acc-1",
            external_id="msg-1",
            thread_id="thr-1",
            subject="subject",
            from_email="sender@example.com",
            to_emails=["receiver@example.com"],
            body_preview="preview",
            received_at=datetime(2026, 2, 14, 1, 5, 0),
            metadata=metadata,
        )

        args = conn.execute.await_args.args
        assert "integration_email_messages" in args[0]
        assert isinstance(args[14], str)
        assert json.loads(args[14]) == metadata


class TestJsonDecoding:
    """Ensure JSON/JSONB rows are decoded into dict objects."""

    @pytest.mark.asyncio
    async def test_get_primary_destination_decodes_metadata_string(self):
        """get_primary_destination decodes metadata when asyncpg returns text JSON."""
        pool, conn = _make_mock_pool()
        storage = IntegrationStorage(pool)
        conn.fetchrow.return_value = {
            "provider": "google",
            "account_ref": "acc-1",
            "destination_id": "calendar-1",
            "destination_type": DestinationType.CALENDAR.value,
            "display_name": "Work",
            "is_primary": True,
            "writable": True,
            "metadata": '{"timezone":"UTC"}',
        }

        result = await storage.get_primary_destination(123, "google", DestinationType.CALENDAR)

        assert result is not None
        assert result.metadata == {"timezone": "UTC"}

    @pytest.mark.asyncio
    async def test_get_routing_preference_decodes_value_string(self):
        """get_routing_preference decodes stored JSON string value."""
        pool, conn = _make_mock_pool()
        storage = IntegrationStorage(pool)
        conn.fetchrow.return_value = {"value": '{"mode":"auto"}'}

        result = await storage.get_routing_preference(
            user_id=123,
            provider="google",
            key="email_policy",
        )

        assert result == {"mode": "auto"}
