"""Unit tests for provider-agnostic IntegrationStorage JSON handling."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.integrations.storage import IntegrationStorage
from zetherion_ai.routing.models import (
    DestinationRef,
    DestinationType,
    RouteDecision,
    RouteMode,
    RouteTag,
)


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


class TestCoverageScenarios:
    """Exercise additional storage paths to keep gate coverage stable."""

    @pytest.mark.asyncio
    async def test_destination_account_queue_and_sync_paths(self) -> None:
        pool, conn = _make_mock_pool()
        storage = IntegrationStorage(pool)

        # ensure_schema
        conn.execute.return_value = "CREATE"
        await storage.ensure_schema()
        conn.execute.assert_awaited()

        # list_destinations + get_primary_destination
        conn.fetch.return_value = [
            {
                "provider": "google",
                "account_ref": "acc-1",
                "destination_id": "calendar-1",
                "destination_type": DestinationType.CALENDAR.value,
                "display_name": "Work",
                "is_primary": True,
                "writable": True,
                "metadata": '{"tz":"UTC"}',
            }
        ]
        listed = await storage.list_destinations(7, "google", DestinationType.CALENDAR)
        assert listed[0].metadata == {"tz": "UTC"}

        conn.fetchrow.return_value = {
            "provider": "google",
            "account_ref": "acc-1",
            "destination_id": "calendar-1",
            "destination_type": DestinationType.CALENDAR.value,
            "display_name": None,
            "is_primary": True,
            "writable": True,
            "metadata": {"a": 1},
        }
        primary = await storage.get_primary_destination(7, "google", DestinationType.CALENDAR)
        assert primary is not None
        assert primary.display_name == "calendar-1"

        # set_primary_destination + delete_account use transaction path.
        conn.execute.reset_mock()
        conn.execute.side_effect = ["UPDATE 2", "UPDATE 1", "DELETE 0", "DELETE 1", "DELETE 1"]
        assert await storage.set_primary_destination(
            7,
            "google",
            DestinationType.CALENDAR,
            "calendar-1",
        )
        assert await storage.delete_account(user_id=7, provider="google", account_ref="acc-1")

        # enqueue + claim + mark done + blocked + counts.
        conn.execute.reset_mock()
        conn.execute.side_effect = ["INSERT 0 1", "INSERT 0 1"]
        batch_id, inserted = await storage.enqueue_ingestion_batch(
            user_id=7,
            provider="google",
            source_type="email",
            items=[{"id": "m1", "account_email": "a@example.com"}, {"id": "m2"}],
        )
        assert batch_id.startswith("batch-")
        assert inserted == 2
        empty_batch_id, empty_inserted = await storage.enqueue_ingestion_batch(
            user_id=7,
            provider="google",
            source_type="email",
            items=[],
            queue_batch_id="batch-empty",
        )
        assert empty_batch_id == "batch-empty"
        assert empty_inserted == 0

        storage._fetch = AsyncMock(  # type: ignore[method-assign]
            return_value=[
                {
                    "id": 1,
                    "queue_batch_id": "batch-1",
                    "user_id": 7,
                    "provider": "google",
                    "source_type": "email",
                    "account_ref": "acc-1",
                    "external_id": "m1",
                    "payload": '{"subject":"hi"}',
                    "status": "processing",
                    "error_code": None,
                    "error_detail": None,
                    "attempt_count": 1,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            ]
        )
        claimed = await storage.claim_ingestion_queue_items(
            user_id=7,
            provider="google",
            source_type="email",
            statuses=["pending"],
            limit=5,
        )
        assert claimed[0].payload == {"subject": "hi"}

        storage._execute = AsyncMock(return_value="UPDATE 1")  # type: ignore[method-assign]
        await storage.mark_ingestion_items_done([1, 2])
        await storage.mark_ingestion_items_done([])
        await storage.mark_ingestion_items_blocked_unhealthy(
            queue_ids=[1],
            error_code="UNHEALTHY",
            error_detail="api-down",
        )
        await storage.mark_ingestion_items_blocked_unhealthy(
            queue_ids=[],
            error_code="UNHEALTHY",
            error_detail=None,
        )

        storage._fetch = AsyncMock(  # type: ignore[method-assign]
            return_value=[{"status": "pending", "cnt": 2}, {"status": "done", "cnt": 5}]
        )
        counts = await storage.get_ingestion_queue_counts(
            user_id=7,
            provider="google",
            source_type="email",
        )
        assert counts == {"pending": 2, "done": 5}

        await storage.set_sync_state(7, "google", "acc-1", cursor="123", state={"k": "v"})
        storage._fetchrow = AsyncMock(  # type: ignore[method-assign]
            return_value={
                "cursor": "123",
                "state": '{"k":"v"}',
                "updated_at": datetime.now(UTC),
            }
        )
        sync_state = await storage.get_sync_state(7, "google", "acc-1")
        assert sync_state is not None
        assert sync_state["state"] == {"k": "v"}

    @pytest.mark.asyncio
    async def test_dead_letter_links_preferences_and_decisions(self) -> None:
        pool, conn = _make_mock_pool()
        storage = IntegrationStorage(pool)

        # Dead-letter no-op when queue row is missing.
        conn.fetchrow.return_value = None
        await storage.move_ingestion_item_to_dead_letter(
            queue_id=99,
            error_code="X",
            error_detail="missing",
        )

        # Dead-letter insert + update when queue row exists.
        conn.fetchrow.return_value = {
            "id": 42,
            "queue_batch_id": "batch-42",
            "user_id": 7,
            "provider": "google",
            "source_type": "email",
            "account_ref": "acc-1",
            "external_id": "m42",
            "payload": {"subject": "hello"},
        }
        conn.execute = AsyncMock(return_value="OK")
        await storage.move_ingestion_item_to_dead_letter(
            queue_id=42,
            error_code="FAIL",
            error_detail="boom",
        )
        assert conn.execute.await_count >= 2

        # Object links + routing preferences + route decisions
        storage._execute = AsyncMock(return_value="OK")  # type: ignore[method-assign]
        await storage.upsert_object_link(
            user_id=7,
            provider="google",
            object_type="task",
            local_id="local-1",
            external_id="ext-1",
            destination_id="list-1",
            metadata={"a": 1},
        )
        storage._fetchrow = AsyncMock(  # type: ignore[method-assign]
            side_effect=[
                {
                    "local_id": "local-1",
                    "external_id": "ext-1",
                    "destination_id": "list-1",
                    "metadata": '{"a":1}',
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                },
                {"value": '{"mode":"auto"}'},
                None,
                None,
            ]
        )
        link = await storage.get_object_link_by_external(
            user_id=7,
            provider="google",
            object_type="task",
            external_id="ext-1",
        )
        assert link is not None
        assert link["metadata"] == {"a": 1}

        await storage.set_routing_preference(
            user_id=7,
            provider="google",
            key="policy",
            value={"mode": "auto"},
        )
        pref = await storage.get_routing_preference(user_id=7, provider="google", key="policy")
        assert pref == {"mode": "auto"}
        assert (
            await storage.get_routing_preference(
                user_id=7,
                provider="google",
                key="missing",
            )
            is None
        )
        assert (
            await storage.get_object_link_by_external(
                user_id=7,
                provider="google",
                object_type="task",
                external_id="missing",
            )
            is None
        )

        decision = RouteDecision(
            mode=RouteMode.AUTO,
            route_tag=RouteTag.TASK_CANDIDATE,
            reason="rule",
            provider="google",
            target=DestinationRef(
                provider="google",
                destination_id="list-1",
                destination_type=DestinationType.TASK_LIST,
                display_name="Tasks",
            ),
        )
        await storage.record_routing_decision(
            user_id=7,
            provider="google",
            source_type="email",
            decision=decision,
        )
