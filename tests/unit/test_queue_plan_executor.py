"""Unit tests for queue plan continuation executor."""

from __future__ import annotations

from datetime import UTC, datetime
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.queue.plan_executor import PlanContinuationExecutor


@pytest.mark.asyncio
async def test_execute_no_lease_returns_noop() -> None:
    manager = AsyncMock()
    manager.claim_execution_plan_lease = AsyncMock(return_value=None)

    executor = PlanContinuationExecutor(tenant_admin_manager=manager, agent=None)
    result = await executor.execute({"tenant_id": "tenant-1", "plan_id": "plan-1"})

    assert result["accepted"] is True
    assert result["noop"] == "lease_not_acquired"
    manager.release_execution_plan_lease.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_success_records_artifacts_and_schedules_next() -> None:
    manager = AsyncMock()
    manager.claim_execution_plan_lease = AsyncMock(return_value={"created_by": "42"})
    manager.claim_next_execution_step = AsyncMock(
        return_value={
            "step": {
                "step_id": "11111111-1111-1111-1111-111111111111",
                "step_index": 0,
                "title": "Step 1",
                "prompt_text": "Build the API routes",
            },
            "retry": {
                "retry_id": "22222222-2222-2222-2222-222222222222",
                "attempt_number": 1,
            },
        }
    )
    manager.record_execution_artifact = AsyncMock(return_value={"artifact_id": "a1"})
    manager.complete_execution_step = AsyncMock(
        return_value={
            "plan": {"status": "running"},
            "step": {"step_id": "11111111-1111-1111-1111-111111111111"},
            "has_more": True,
            "next_run_at": datetime.now(UTC),
        }
    )
    manager.schedule_execution_continuation = AsyncMock(return_value="q1")
    manager.release_execution_plan_lease = AsyncMock(return_value=None)

    agent = AsyncMock()
    agent.generate_response = AsyncMock(return_value="Done")

    executor = PlanContinuationExecutor(tenant_admin_manager=manager, agent=agent)
    result = await executor.execute({"tenant_id": "tenant-1", "plan_id": "plan-1"})

    assert result["accepted"] is True
    assert result["has_more"] is True
    assert manager.record_execution_artifact.await_count == 2
    manager.complete_execution_step.assert_awaited_once()
    manager.schedule_execution_continuation.assert_awaited_once()
    manager.release_execution_plan_lease.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_failure_marks_retry_and_reschedules() -> None:
    manager = AsyncMock()
    manager.claim_execution_plan_lease = AsyncMock(return_value={"created_by": "42"})
    manager.claim_next_execution_step = AsyncMock(
        return_value={
            "step": {
                "step_id": "11111111-1111-1111-1111-111111111111",
                "step_index": 0,
                "title": "Step 1",
                "prompt_text": "Build the API routes",
            },
            "retry": {
                "retry_id": "22222222-2222-2222-2222-222222222222",
                "attempt_number": 1,
            },
        }
    )
    manager.record_execution_artifact = AsyncMock(return_value={"artifact_id": "a1"})
    manager.fail_execution_step = AsyncMock(
        return_value={
            "retry_scheduled": True,
            "next_run_at": datetime.now(UTC),
            "failure_category": "timeout",
        }
    )
    manager.schedule_execution_continuation = AsyncMock(return_value="q1")
    manager.release_execution_plan_lease = AsyncMock(return_value=None)

    agent = AsyncMock()
    agent.generate_response = AsyncMock(side_effect=RuntimeError("timeout from provider"))

    executor = PlanContinuationExecutor(tenant_admin_manager=manager, agent=agent)
    result = await executor.execute({"tenant_id": "tenant-1", "plan_id": "plan-1"})

    assert result["accepted"] is True
    assert result["failure_category"] == "timeout"
    manager.fail_execution_step.assert_awaited_once()
    manager.schedule_execution_continuation.assert_awaited_once()
    manager.release_execution_plan_lease.assert_awaited_once()
