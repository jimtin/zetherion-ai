"""Unit tests for tenant worker registry and secure worker control storage."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.admin.tenant_admin_manager import AdminActorContext, TenantAdminManager


class _AsyncContext:
    def __init__(self, value: Any) -> None:
        self._value = value

    async def __aenter__(self) -> Any:
        return self._value

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _FakeConn:
    def __init__(self) -> None:
        self.execute = AsyncMock(return_value="OK")
        self.fetch = AsyncMock(return_value=[])
        self.fetchrow = AsyncMock(return_value=None)
        self.fetchval = AsyncMock(return_value=None)

    def transaction(self) -> _AsyncContext:
        return _AsyncContext(None)


class _FakePool:
    def __init__(self, conn: _FakeConn) -> None:
        self._conn = conn

    def acquire(self) -> _AsyncContext:
        return _AsyncContext(self._conn)


def _actor() -> AdminActorContext:
    return AdminActorContext(
        actor_sub="operator-1",
        actor_roles=("operator",),
        request_id="req-1",
        timestamp=datetime.now(UTC),
        nonce="nonce-1",
        actor_email="ops@example.com",
    )


@pytest.mark.asyncio
async def test_bootstrap_worker_session_and_auth_lookup_paths() -> None:
    conn = _FakeConn()
    conn.fetchrow.side_effect = [
        {
            "node_id": "node-1",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "node_name": "laptop",
            "status": "bootstrap_pending",
            "health_status": "unknown",
            "metadata": {},
            "last_heartbeat_at": None,
            "created_by": "worker-bootstrap",
            "updated_by": "worker-bootstrap",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        },
        {
            "session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "node_id": "node-1",
            "issued_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "rotated_at": None,
            "revoked_at": None,
            "last_seen_at": None,
            "metadata": {},
        },
    ]
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]

    created = await manager.bootstrap_worker_node_session(
        tenant_id="11111111-1111-1111-1111-111111111111",
        node_id="node-1",
        node_name="laptop",
        capabilities=["repo.patch", "repo.pr.open"],
        metadata={"env": "dev"},
        session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        session_token_hash="a" * 64,
        signing_secret="signing-secret",
        session_ttl_seconds=3600,
        actor_sub="worker-bootstrap",
    )
    assert created["node"]["node_id"] == "node-1"
    assert created["session"]["session_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    assert created["capabilities"] == ["repo.patch", "repo.pr.open"]
    assert conn.execute.await_count >= 4

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "node_id": "node-1",
            "token_hash": "a" * 64,
            "signing_secret_enc": "signing-secret",
            "issued_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "rotated_at": None,
            "revoked_at": None,
            "last_seen_at": None,
            "session_metadata": {},
            "node_name": "laptop",
            "status": "registered",
            "health_status": "healthy",
            "node_metadata": {},
            "last_heartbeat_at": datetime.now(UTC),
        }
    )
    manager._fetch = AsyncMock(return_value=[{"capability": "repo.patch"}])  # type: ignore[method-assign]
    auth = await manager.get_worker_session_auth(
        tenant_id="11111111-1111-1111-1111-111111111111",
        node_id="node-1",
        session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
    assert auth is not None
    assert auth["signing_secret"] == "signing-secret"
    assert auth["capabilities"] == ["repo.patch"]


@pytest.mark.asyncio
async def test_bootstrap_worker_session_validation_errors() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    base_kwargs = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "node_id": "node-1",
        "session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        "session_token_hash": "a" * 64,
        "signing_secret": "signing-secret",
    }

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.bootstrap_worker_node_session(**{**base_kwargs, "node_id": ""})
    with pytest.raises(ValueError, match="Missing session_token_hash"):
        await manager.bootstrap_worker_node_session(
            **{**base_kwargs, "session_token_hash": ""}
        )
    with pytest.raises(ValueError, match="SHA-256 hex digest"):
        await manager.bootstrap_worker_node_session(
            **{**base_kwargs, "session_token_hash": "not-a-sha256"}
        )
    with pytest.raises(ValueError, match="Missing signing_secret"):
        await manager.bootstrap_worker_node_session(**{**base_kwargs, "signing_secret": ""})


@pytest.mark.asyncio
async def test_worker_registry_mutations_and_queries_paths() -> None:
    conn = _FakeConn()
    conn.fetchrow.return_value = {"node_id": "node-1"}
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    manager.get_worker_node = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "node_id": "node-1",
            "status": "registered",
            "health_status": "healthy",
            "capabilities": ["repo.patch"],
        }
    )

    registered = await manager.register_worker_node(
        tenant_id="11111111-1111-1111-1111-111111111111",
        node_id="node-1",
        node_name="laptop",
        capabilities=["repo.patch"],
        metadata={"version": "1"},
        actor_sub="worker-register",
    )
    assert registered["node_id"] == "node-1"

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "node_id": "node-1",
            "issued_at": datetime.now(UTC),
            "expires_at": datetime.now(UTC) + timedelta(hours=1),
            "rotated_at": datetime.now(UTC),
            "revoked_at": None,
            "last_seen_at": datetime.now(UTC),
            "metadata": {},
        }
    )
    rotated = await manager.rotate_worker_session_credentials(
        tenant_id="11111111-1111-1111-1111-111111111111",
        node_id="node-1",
        session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        session_token_hash="b" * 64,
        signing_secret="rotated-secret",
    )
    assert rotated["session_id"] == "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "node_id": "node-1",
            "status": "active",
            "health_status": "healthy",
            "metadata": {},
            "last_heartbeat_at": datetime.now(UTC),
            "created_by": "worker-bootstrap",
            "updated_by": "worker-heartbeat",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    heartbeat = await manager.heartbeat_worker_node(
        tenant_id="11111111-1111-1111-1111-111111111111",
        node_id="node-1",
        health_status="healthy",
        metadata={"ping": 1},
        actor_sub="worker-heartbeat",
    )
    assert heartbeat["status"] == "active"

    manager._fetchval = AsyncMock(return_value=2)  # type: ignore[method-assign]
    assert (
        await manager.has_worker_capabilities(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
            required_capabilities=["repo.patch", "repo.pr.open"],
        )
        is True
    )

    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            [
                {
                    "tenant_id": "11111111-1111-1111-1111-111111111111",
                    "node_id": "node-1",
                    "node_name": "laptop",
                    "status": "active",
                    "health_status": "healthy",
                    "metadata": {},
                    "last_heartbeat_at": datetime.now(UTC),
                    "created_by": "worker-bootstrap",
                    "updated_by": "worker-heartbeat",
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            ],
            [{"node_id": "node-1", "capability": "repo.patch"}],
        ]
    )
    listed = await manager.list_worker_nodes(
        tenant_id="11111111-1111-1111-1111-111111111111",
        include_inactive=False,
        limit=10,
    )
    assert listed[0]["capabilities"] == ["repo.patch"]

    # Use a fresh manager instance for direct get_worker_node coverage.
    detail_conn = _FakeConn()
    detail_manager = TenantAdminManager(pool=_FakePool(detail_conn))  # type: ignore[arg-type]
    detail_manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "node_id": "node-1",
            "node_name": "laptop",
            "status": "active",
            "health_status": "healthy",
            "metadata": {},
            "last_heartbeat_at": datetime.now(UTC),
            "created_by": "worker-bootstrap",
            "updated_by": "worker-heartbeat",
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
        }
    )
    detail_manager._fetch = AsyncMock(return_value=[{"capability": "repo.patch"}])  # type: ignore[method-assign]
    detail_manager._fetchval = AsyncMock(return_value=1)  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value=None
    )
    node = await detail_manager.get_worker_node(
        tenant_id="11111111-1111-1111-1111-111111111111",
        node_id="node-1",
    )
    assert node is not None
    assert node["active_session_count"] == 1


@pytest.mark.asyncio
async def test_worker_capability_update_and_job_event_paths() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    before = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "node_id": "node-1",
        "status": "registered",
        "health_status": "healthy",
        "capabilities": ["repo.patch"],
    }
    after = {
        "tenant_id": "11111111-1111-1111-1111-111111111111",
        "node_id": "node-1",
        "status": "registered",
        "health_status": "healthy",
        "capabilities": ["repo.patch", "repo.pr.open"],
    }
    manager.get_worker_node = AsyncMock(side_effect=[before, after])  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    updated = await manager.set_worker_capabilities(
        tenant_id="11111111-1111-1111-1111-111111111111",
        node_id="node-1",
        capabilities=["repo.patch", "repo.pr.open"],
        actor=_actor(),
    )
    assert updated["capabilities"] == ["repo.patch", "repo.pr.open"]
    manager._write_audit.assert_awaited_once()

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "event_id": 1,
            "tenant_id": "11111111-1111-1111-1111-111111111111",
            "node_id": "node-1",
            "session_id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            "job_id": "job-1",
            "event_type": "worker.job.claim",
            "request_nonce": "nonce-1",
            "payload_json": {},
            "created_at": datetime.now(UTC),
        }
    )
    event = await manager.record_worker_job_event(
        tenant_id="11111111-1111-1111-1111-111111111111",
        node_id="node-1",
        session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
        job_id="job-1",
        event_type="worker.job.claim",
        request_nonce="nonce-1",
        payload={},
    )
    assert event["event_id"] == 1

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="replay"):
        await manager.record_worker_job_event(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
            session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            job_id="job-1",
            event_type="worker.job.claim",
            request_nonce="nonce-1",
            payload={},
        )


@pytest.mark.asyncio
async def test_worker_registry_validation_and_edge_paths() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]

    conn.execute.return_value = "UPDATE 1"
    touched = await manager.touch_worker_session(
        tenant_id="11111111-1111-1111-1111-111111111111",
        node_id="node-1",
        session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
    assert touched is True

    conn.execute.return_value = "UPDATE 0"
    touched = await manager.touch_worker_session(
        tenant_id="11111111-1111-1111-1111-111111111111",
        node_id="node-1",
        session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
    )
    assert touched is False

    with pytest.raises(ValueError, match="session_token_hash"):
        await manager.rotate_worker_session_credentials(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
            session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            session_token_hash="bad",
            signing_secret="secret",
        )

    with pytest.raises(ValueError, match="Missing signing_secret"):
        await manager.rotate_worker_session_credentials(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
            session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            session_token_hash="a" * 64,
            signing_secret="",
        )

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="not found"):
        await manager.rotate_worker_session_credentials(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
            session_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa",
            session_token_hash="a" * 64,
            signing_secret="secret",
        )

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.register_worker_node(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="",
        )

    missing_conn = _FakeConn()
    missing_conn.fetchrow.return_value = None
    missing_manager = TenantAdminManager(pool=_FakePool(missing_conn))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="not found"):
        await missing_manager.register_worker_node(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-missing",
        )

    runtime_conn = _FakeConn()
    runtime_conn.fetchrow.return_value = {"node_id": "node-1"}
    runtime_manager = TenantAdminManager(pool=_FakePool(runtime_conn))  # type: ignore[arg-type]
    runtime_manager.get_worker_node = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to load"):
        await runtime_manager.register_worker_node(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
        )

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.heartbeat_worker_node(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="",
        )

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="Worker node not found"):
        await manager.heartbeat_worker_node(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
        )

    assert (
        await manager.has_worker_capabilities(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
            required_capabilities=[],
        )
        is True
    )

    manager._fetch = AsyncMock(return_value=[])  # type: ignore[method-assign]
    listed = await manager.list_worker_nodes(
        tenant_id="11111111-1111-1111-1111-111111111111",
        include_inactive=False,
        limit=10,
    )
    assert listed == []

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.get_worker_node(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="",
        )

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    missing_node = await manager.get_worker_node(
        tenant_id="11111111-1111-1111-1111-111111111111",
        node_id="node-1",
    )
    assert missing_node is None

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.set_worker_capabilities(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="",
            capabilities=["repo.patch"],
        )

    manager.get_worker_node = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="Worker node not found"):
        await manager.set_worker_capabilities(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
            capabilities=["repo.patch"],
        )

    manager.get_worker_node = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "tenant_id": "11111111-1111-1111-1111-111111111111",
                "node_id": "node-1",
                "status": "active",
                "health_status": "healthy",
                "capabilities": ["repo.patch"],
            },
            None,
        ]
    )
    with pytest.raises(RuntimeError, match="Failed to load"):
        await manager.set_worker_capabilities(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
            capabilities=["repo.patch", "repo.pr.open"],
        )

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.record_worker_job_event(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="",
            event_type="worker.job.claim",
        )

    with pytest.raises(ValueError, match="Missing event_type"):
        await manager.record_worker_job_event(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
            event_type="",
        )

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to record"):
        await manager.record_worker_job_event(
            tenant_id="11111111-1111-1111-1111-111111111111",
            node_id="node-1",
            event_type="worker.job.claim",
            request_nonce=None,
        )
