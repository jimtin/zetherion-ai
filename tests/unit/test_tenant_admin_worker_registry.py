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
        await manager.bootstrap_worker_node_session(**{**base_kwargs, "session_token_hash": ""})
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


@pytest.mark.asyncio
async def test_worker_job_listing_and_detail_paths() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    tenant_id = "11111111-1111-1111-1111-111111111111"
    plan_id = "22222222-2222-2222-2222-222222222222"
    job_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"

    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"job_id": job_id, "status": "running"}]
    )
    jobs = await manager.list_worker_jobs(
        tenant_id=tenant_id,
        node_id="node-1",
        status="RUNNING",
        plan_id=plan_id,
        limit=5000,
    )
    assert jobs == [{"job_id": job_id, "status": "running"}]
    list_call = manager._fetch.await_args
    assert "claimed_by_node_id" in list_call.args[0]
    assert "plan_id" in list_call.args[0]
    assert list_call.args[-1] == 1000
    assert list_call.args[1] == tenant_id
    assert list_call.args[2] == "node-1"
    assert list_call.args[3] == "running"
    assert list_call.args[4] == plan_id

    manager._fetchrow = AsyncMock(  # type: ignore[method-assign]
        return_value={"job_id": job_id, "status": "queued"}
    )
    fetched_job = await manager.get_worker_job(tenant_id=tenant_id, job_id=job_id)
    assert fetched_job == {"job_id": job_id, "status": "queued"}

    manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    missing_job = await manager.get_worker_job(tenant_id=tenant_id, job_id=job_id)
    assert missing_job is None

    manager._fetch = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"event_id": 3, "job_id": "job-1"}]
    )
    events = await manager.list_worker_job_events(
        tenant_id=tenant_id,
        node_id="node-1",
        job_id="job-1",
        limit=0,
    )
    assert events == [{"event_id": 3, "job_id": "job-1"}]
    event_call = manager._fetch.await_args
    assert "node_id" in event_call.args[0]
    assert "job_id" in event_call.args[0]
    assert event_call.args[-1] == 1
    assert event_call.args[1] == tenant_id
    assert event_call.args[2] == "node-1"
    assert event_call.args[3] == "job-1"


@pytest.mark.asyncio
async def test_set_worker_node_status_updates_and_audits() -> None:
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    tenant_id = "11111111-1111-1111-1111-111111111111"
    before = {
        "tenant_id": tenant_id,
        "node_id": "node-1",
        "status": "active",
        "health_status": "healthy",
        "capabilities": ["repo.patch"],
    }
    after = {
        "tenant_id": tenant_id,
        "node_id": "node-1",
        "status": "quarantined",
        "health_status": "degraded",
        "capabilities": ["repo.patch"],
    }
    manager.get_worker_node = AsyncMock(side_effect=[before, after])  # type: ignore[method-assign]
    manager._fetchrow = AsyncMock(return_value={"node_id": "node-1"})  # type: ignore[method-assign]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    updated = await manager.set_worker_node_status(
        tenant_id=tenant_id,
        node_id="node-1",
        status="quarantined",
        actor=_actor(),
        metadata={"reason": "manual-review"},
    )
    assert updated["status"] == "quarantined"
    assert updated["health_status"] == "degraded"

    update_call = manager._fetchrow.await_args
    assert update_call.args[3] == "quarantined"
    assert update_call.args[4] == "degraded"
    assert update_call.args[5] == '{"reason": "manual-review"}'
    manager._write_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_retry_worker_job_success_path() -> None:
    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    tenant_id = "11111111-1111-1111-1111-111111111111"
    job_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    plan_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    step_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    retry_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    conn.fetchrow.side_effect = [
        {
            "job_id": job_id,
            "tenant_id": tenant_id,
            "plan_id": plan_id,
            "step_id": step_id,
            "retry_id": retry_id,
            "status": "running",
            "claimed_by_node_id": "node-1",
            "error_json": None,
        },
        {"status": "running"},
        {"status": "running"},
        {"job_id": job_id, "status": "expired"},
        {"step_id": step_id, "status": "pending"},
        {"plan_id": plan_id, "status": "queued"},
        {"job_id": job_id, "status": "expired"},
    ]
    manager.schedule_execution_continuation = AsyncMock(  # type: ignore[method-assign]
        return_value=None
    )
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    payload = await manager.retry_worker_job(
        tenant_id=tenant_id,
        job_id=job_id,
        actor=_actor(),
    )
    assert payload["job"]["status"] == "expired"
    assert payload["step"]["status"] == "pending"
    assert payload["plan"]["status"] == "queued"
    assert isinstance(payload["scheduled_for"], datetime)
    assert conn.execute.await_count == 3
    manager.schedule_execution_continuation.assert_awaited_once()
    manager._write_audit.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_worker_job_success_and_idempotent_paths() -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    job_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    plan_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    step_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    retry_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    conn = _FakeConn()
    manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
    conn.fetchrow.side_effect = [
        {
            "job_id": job_id,
            "tenant_id": tenant_id,
            "plan_id": plan_id,
            "step_id": step_id,
            "retry_id": retry_id,
            "status": "running",
        },
        {"status": "running"},
        {"status": "running"},
        {"job_id": job_id, "status": "cancelled"},
        {"step_id": step_id, "status": "blocked"},
        {"plan_id": plan_id, "status": "failed"},
    ]
    manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]

    cancelled = await manager.cancel_worker_job(
        tenant_id=tenant_id,
        job_id=job_id,
        actor=_actor(),
    )
    assert cancelled["idempotent"] is False
    assert cancelled["job"]["status"] == "cancelled"
    assert cancelled["step"]["status"] == "blocked"
    assert cancelled["plan"]["status"] == "failed"
    assert conn.execute.await_count == 3
    manager._write_audit.assert_awaited_once()

    idem_conn = _FakeConn()
    idem_manager = TenantAdminManager(pool=_FakePool(idem_conn))  # type: ignore[arg-type]
    idem_manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    idem_conn.fetchrow.side_effect = [
        {
            "job_id": job_id,
            "tenant_id": tenant_id,
            "plan_id": plan_id,
            "step_id": step_id,
            "retry_id": retry_id,
            "status": "cancelled",
        },
        {"job_id": job_id, "status": "cancelled"},
    ]

    idempotent = await idem_manager.cancel_worker_job(
        tenant_id=tenant_id,
        job_id=job_id,
        actor=_actor(),
    )
    assert idempotent["idempotent"] is True
    assert idempotent["job"]["status"] == "cancelled"
    idem_manager._write_audit.assert_not_awaited()


@pytest.mark.asyncio
async def test_worker_status_update_validation_and_reload_paths() -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="job status must be one of"):
        await manager.list_worker_jobs(tenant_id=tenant_id, status="bad-status")

    with pytest.raises(ValueError, match="Missing node_id"):
        await manager.set_worker_node_status(
            tenant_id=tenant_id,
            node_id="",
            status="active",
            actor=_actor(),
        )

    manager.get_worker_node = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(ValueError, match="Worker node not found"):
        await manager.set_worker_node_status(
            tenant_id=tenant_id,
            node_id="node-1",
            status="active",
            actor=_actor(),
        )

    active_conn = _FakeConn()
    active_manager = TenantAdminManager(pool=_FakePool(active_conn))  # type: ignore[arg-type]
    active_manager.get_worker_node = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "tenant_id": tenant_id,
                "node_id": "node-1",
                "status": "registered",
                "health_status": "unknown",
            },
            {
                "tenant_id": tenant_id,
                "node_id": "node-1",
                "status": "active",
                "health_status": "healthy",
            },
        ]
    )
    active_manager._fetchrow = AsyncMock(return_value={"node_id": "node-1"})  # type: ignore[method-assign]
    active_manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    await active_manager.set_worker_node_status(
        tenant_id=tenant_id,
        node_id="node-1",
        status="active",
        actor=_actor(),
        health_status=None,
    )
    active_call = active_manager._fetchrow.await_args
    assert active_call.args[4] == "healthy"

    explicit_conn = _FakeConn()
    explicit_manager = TenantAdminManager(pool=_FakePool(explicit_conn))  # type: ignore[arg-type]
    explicit_manager.get_worker_node = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "tenant_id": tenant_id,
                "node_id": "node-1",
                "status": "active",
                "health_status": "healthy",
            },
            {
                "tenant_id": tenant_id,
                "node_id": "node-1",
                "status": "active",
                "health_status": "degraded",
            },
        ]
    )
    explicit_manager._fetchrow = AsyncMock(return_value={"node_id": "node-1"})  # type: ignore[method-assign]
    explicit_manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
    await explicit_manager.set_worker_node_status(
        tenant_id=tenant_id,
        node_id="node-1",
        status="active",
        actor=_actor(),
        health_status="degraded",
    )
    explicit_call = explicit_manager._fetchrow.await_args
    assert explicit_call.args[4] == "degraded"

    update_fail_manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    update_fail_manager.get_worker_node = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "tenant_id": tenant_id,
            "node_id": "node-1",
            "status": "active",
            "health_status": "healthy",
        }
    )
    update_fail_manager._fetchrow = AsyncMock(return_value=None)  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to update worker node status"):
        await update_fail_manager.set_worker_node_status(
            tenant_id=tenant_id,
            node_id="node-1",
            status="quarantined",
            actor=_actor(),
        )

    reload_fail_manager = TenantAdminManager(pool=_FakePool(_FakeConn()))  # type: ignore[arg-type]
    reload_fail_manager.get_worker_node = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "tenant_id": tenant_id,
                "node_id": "node-1",
                "status": "active",
                "health_status": "healthy",
            },
            None,
        ]
    )
    reload_fail_manager._fetchrow = AsyncMock(return_value={"node_id": "node-1"})  # type: ignore[method-assign]
    with pytest.raises(RuntimeError, match="Failed to reload worker node"):
        await reload_fail_manager.set_worker_node_status(
            tenant_id=tenant_id,
            node_id="node-1",
            status="quarantined",
            actor=_actor(),
        )


def _worker_job_record(
    *,
    tenant_id: str,
    job_id: str,
    plan_id: str,
    step_id: str,
    retry_id: str,
    status: str,
) -> dict[str, str]:
    return {
        "job_id": job_id,
        "tenant_id": tenant_id,
        "plan_id": plan_id,
        "step_id": step_id,
        "retry_id": retry_id,
        "status": status,
    }


@pytest.mark.asyncio
async def test_retry_worker_job_error_paths() -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    job_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    plan_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    step_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    retry_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    def _manager_with_rows(rows: list[Any]) -> TenantAdminManager:
        conn = _FakeConn()
        conn.fetchrow.side_effect = rows
        manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
        manager.schedule_execution_continuation = AsyncMock(return_value=None)  # type: ignore[method-assign]
        manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
        return manager

    with pytest.raises(ValueError, match="Worker job not found"):
        await _manager_with_rows([None]).retry_worker_job(
            tenant_id=tenant_id,
            job_id=job_id,
            actor=_actor(),
        )

    with pytest.raises(ValueError, match="already succeeded"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="succeeded",
                )
            ]
        ).retry_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())

    with pytest.raises(ValueError, match="Execution step not found"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="running",
                ),
                None,
            ]
        ).retry_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())

    with pytest.raises(ValueError, match="Execution plan not found"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="running",
                ),
                {"status": "running"},
                None,
            ]
        ).retry_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())

    with pytest.raises(RuntimeError, match="Failed to expire worker job"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="running",
                ),
                {"status": "running"},
                {"status": "running"},
                None,
            ]
        ).retry_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())

    with pytest.raises(RuntimeError, match="Failed to reset execution step"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="running",
                ),
                {"status": "running"},
                {"status": "running"},
                {"job_id": job_id, "status": "expired"},
                None,
            ]
        ).retry_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())

    with pytest.raises(RuntimeError, match="Failed to queue execution plan"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="running",
                ),
                {"status": "running"},
                {"status": "running"},
                {"job_id": job_id, "status": "expired"},
                {"step_id": step_id, "status": "pending"},
                None,
            ]
        ).retry_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())

    with pytest.raises(RuntimeError, match="Failed to reload worker job"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="running",
                ),
                {"status": "running"},
                {"status": "running"},
                {"job_id": job_id, "status": "expired"},
                {"step_id": step_id, "status": "pending"},
                {"plan_id": plan_id, "status": "queued"},
                None,
            ]
        ).retry_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())


@pytest.mark.asyncio
async def test_cancel_worker_job_error_paths() -> None:
    tenant_id = "11111111-1111-1111-1111-111111111111"
    job_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
    plan_id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
    step_id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
    retry_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"

    def _manager_with_rows(rows: list[Any]) -> TenantAdminManager:
        conn = _FakeConn()
        conn.fetchrow.side_effect = rows
        manager = TenantAdminManager(pool=_FakePool(conn))  # type: ignore[arg-type]
        manager._write_audit = AsyncMock(return_value=None)  # type: ignore[method-assign]
        return manager

    with pytest.raises(ValueError, match="Worker job not found"):
        await _manager_with_rows([None]).cancel_worker_job(
            tenant_id=tenant_id,
            job_id=job_id,
            actor=_actor(),
        )

    with pytest.raises(ValueError, match="already terminal"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="succeeded",
                )
            ]
        ).cancel_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())

    with pytest.raises(ValueError, match="Execution step not found"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="running",
                ),
                None,
            ]
        ).cancel_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())

    with pytest.raises(ValueError, match="Execution plan not found"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="running",
                ),
                {"status": "running"},
                None,
            ]
        ).cancel_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())

    with pytest.raises(RuntimeError, match="Failed to cancel worker job"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="running",
                ),
                {"status": "running"},
                {"status": "running"},
                None,
            ]
        ).cancel_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())

    with pytest.raises(RuntimeError, match="Failed to block execution step"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="running",
                ),
                {"status": "running"},
                {"status": "running"},
                {"job_id": job_id, "status": "cancelled"},
                None,
            ]
        ).cancel_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())

    with pytest.raises(RuntimeError, match="Failed to fail execution plan"):
        await _manager_with_rows(
            [
                _worker_job_record(
                    tenant_id=tenant_id,
                    job_id=job_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    status="running",
                ),
                {"status": "running"},
                {"status": "running"},
                {"job_id": job_id, "status": "cancelled"},
                {"step_id": step_id, "status": "blocked"},
                None,
            ]
        ).cancel_worker_job(tenant_id=tenant_id, job_id=job_id, actor=_actor())
