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
async def test_execute_requires_tenant_and_plan_ids() -> None:
    executor = PlanContinuationExecutor(tenant_admin_manager=AsyncMock(), agent=None)

    with pytest.raises(ValueError, match="tenant_id and plan_id"):
        await executor.execute({"tenant_id": "tenant-only"})


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
async def test_execute_remote_step_dispatches_worker_job() -> None:
    manager = AsyncMock()
    manager.claim_execution_plan_lease = AsyncMock(return_value={"created_by": "42"})
    manager.claim_next_execution_step = AsyncMock(
        return_value={
            "step": {
                "step_id": "11111111-1111-1111-1111-111111111111",
                "step_index": 0,
                "title": "Step 1",
                "prompt_text": "Build the API routes",
                "execution_target": "any_worker",
                "required_capabilities": ["repo.patch"],
                "max_runtime_seconds": 600,
                "artifact_contract": {"expect": "summary"},
                "metadata": {"runner": "noop"},
            },
            "retry": {
                "retry_id": "22222222-2222-2222-2222-222222222222",
                "attempt_number": 1,
            },
        }
    )
    manager.dispatch_execution_step_to_worker = AsyncMock(
        return_value={"job_id": "job-1", "status": "queued"}
    )
    manager.record_execution_artifact = AsyncMock(return_value={"artifact_id": "a1"})
    manager.schedule_execution_continuation = AsyncMock(return_value="q1")
    manager.release_execution_plan_lease = AsyncMock(return_value=None)
    manager.complete_execution_step = AsyncMock(return_value={})

    agent = AsyncMock()
    agent.generate_response = AsyncMock(return_value="Done")

    executor = PlanContinuationExecutor(tenant_admin_manager=manager, agent=agent)
    result = await executor.execute({"tenant_id": "tenant-1", "plan_id": "plan-1"})

    assert result["accepted"] is True
    assert result["dispatched"] is True
    assert result["job_id"] == "job-1"
    manager.dispatch_execution_step_to_worker.assert_awaited_once()
    manager.record_execution_artifact.assert_awaited_once()
    manager.complete_execution_step.assert_not_awaited()
    agent.generate_response.assert_not_awaited()
    manager.schedule_execution_continuation.assert_awaited_once()
    schedule_kwargs = manager.schedule_execution_continuation.await_args.kwargs
    assert schedule_kwargs["reason"] == "worker_dispatch_monitor"
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


@pytest.mark.asyncio
async def test_execute_no_claimable_step_reconciles_and_releases() -> None:
    manager = AsyncMock()
    manager.claim_execution_plan_lease = AsyncMock(return_value={"created_by": "7"})
    manager.claim_next_execution_step = AsyncMock(return_value=None)
    manager.reconcile_execution_plan_status = AsyncMock(return_value={"status": "completed"})
    manager.release_execution_plan_lease = AsyncMock(return_value=None)

    executor = PlanContinuationExecutor(tenant_admin_manager=manager, agent=None)
    result = await executor.execute({"tenant_id": "tenant-1", "plan_id": "plan-1"})

    assert result["noop"] == "no_claimable_step"
    assert result["plan"]["status"] == "completed"
    manager.reconcile_execution_plan_status.assert_awaited_once()
    manager.release_execution_plan_lease.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_malformed_claim_returns_failure_without_step_mutation() -> None:
    manager = AsyncMock()
    manager.claim_execution_plan_lease = AsyncMock(return_value={"created_by": "7"})
    manager.claim_next_execution_step = AsyncMock(return_value={"step": "bad", "retry": "bad"})
    manager.release_execution_plan_lease = AsyncMock(return_value=None)

    executor = PlanContinuationExecutor(tenant_admin_manager=manager, agent=None)
    result = await executor.execute({"tenant_id": "tenant-1", "plan_id": "plan-1"})

    assert result["failure_category"] == "transient"
    manager.fail_execution_step.assert_not_awaited()
    manager.release_execution_plan_lease.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_empty_prompt_fails_step_and_records_failure_artifact() -> None:
    manager = AsyncMock()
    manager.claim_execution_plan_lease = AsyncMock(return_value={"created_by": "7"})
    manager.claim_next_execution_step = AsyncMock(
        return_value={
            "step": {
                "step_id": "11111111-1111-1111-1111-111111111111",
                "step_index": 0,
                "title": "Step 1",
                "prompt_text": "   ",
            },
            "retry": {
                "retry_id": "22222222-2222-2222-2222-222222222222",
                "attempt_number": 1,
            },
        }
    )
    manager.fail_execution_step = AsyncMock(
        return_value={
            "retry_scheduled": False,
            "next_run_at": None,
            "failure_category": "transient",
        }
    )
    manager.record_execution_artifact = AsyncMock(return_value={"artifact_id": "a1"})
    manager.release_execution_plan_lease = AsyncMock(return_value=None)

    executor = PlanContinuationExecutor(tenant_admin_manager=manager, agent=None)
    result = await executor.execute({"tenant_id": "tenant-1", "plan_id": "plan-1"})

    assert result["failure_category"] == "transient"
    manager.fail_execution_step.assert_awaited_once()
    manager.record_execution_artifact.assert_awaited_once()


@pytest.mark.asyncio
async def test_prompt_agent_fallback_and_empty_response_paths() -> None:
    manager = AsyncMock()
    fallback_executor = PlanContinuationExecutor(tenant_admin_manager=manager, agent=None)
    fallback = await fallback_executor._prompt_agent(  # noqa: SLF001 - direct branch coverage
        prompt_text="build",
        plan={"created_by": "abc"},
        step={"title": "No Agent"},
    )
    assert "Recorded step without agent runtime" in fallback

    agent = AsyncMock()
    agent.generate_response = AsyncMock(return_value="")
    active_executor = PlanContinuationExecutor(tenant_admin_manager=manager, agent=agent)
    empty = await active_executor._prompt_agent(  # noqa: SLF001 - direct branch coverage
        prompt_text="build",
        plan={"created_by": "not-numeric"},
        step={"title": "Step 1"},
    )
    assert empty == "Agent returned empty output."
    call_kwargs = agent.generate_response.await_args.kwargs
    assert call_kwargs["user_id"] == 0


def test_categorize_failure_variants() -> None:
    assert (
        PlanContinuationExecutor._categorize_failure(RuntimeError("request timeout")) == "timeout"
    )
    assert (
        PlanContinuationExecutor._categorize_failure(RuntimeError("rate limit exceeded"))
        == "rate_limit"
    )
    assert (
        PlanContinuationExecutor._categorize_failure(RuntimeError("dependency unavailable"))
        == "dependency"
    )
    assert (
        PlanContinuationExecutor._categorize_failure(RuntimeError("operation interrupted"))
        == "interrupted"
    )


def test_execution_target_for_step_variants() -> None:
    assert (
        PlanContinuationExecutor._execution_target_for_step(  # noqa: SLF001 - direct branch coverage
            {"execution_target": "worker:node-1"}
        )
        == "worker:node-1"
    )
    assert (
        PlanContinuationExecutor._execution_target_for_step(  # noqa: SLF001 - direct branch coverage
            {"execution_target": "worker:"}
        )
        == "windows_local"
    )


@pytest.mark.asyncio
async def test_execute_completed_plan_enqueues_overnight_summary() -> None:
    manager = AsyncMock()
    manager.claim_execution_plan_lease = AsyncMock(
        return_value={"created_by": "42", "title": "Ship worker", "goal": "Open a PR"}
    )
    manager.claim_next_execution_step = AsyncMock(
        return_value={
            "step": {
                "step_id": "11111111-1111-1111-1111-111111111111",
                "step_index": 2,
                "title": "Open PR",
                "prompt_text": "Open the PR",
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
            "plan": {
                "status": "completed",
                "title": "Ship worker",
                "goal": "Open a PR",
                "total_steps": 3,
            },
            "step": {"step_id": "11111111-1111-1111-1111-111111111111", "step_index": 2},
            "has_more": False,
            "next_run_at": None,
        }
    )
    manager.release_execution_plan_lease = AsyncMock(return_value=None)

    agent = AsyncMock()
    agent.generate_response = AsyncMock(return_value="Opened PR #123")
    review_inbox = AsyncMock()

    executor = PlanContinuationExecutor(
        tenant_admin_manager=manager,
        agent=agent,
        review_inbox=review_inbox,
    )
    result = await executor.execute({"tenant_id": "tenant-1", "plan_id": "plan-1"})

    assert result["accepted"] is True
    review_inbox.enqueue_overnight_summary.assert_awaited_once()
    kwargs = review_inbox.enqueue_overnight_summary.await_args.kwargs
    assert kwargs["user_id"] == 42
    assert kwargs["plan_id"] == "plan-1"
    assert kwargs["summary"] == "Opened PR #123"


@pytest.mark.asyncio
async def test_execute_terminal_failure_enqueues_blocked_review_item() -> None:
    manager = AsyncMock()
    manager.claim_execution_plan_lease = AsyncMock(
        return_value={"created_by": "42", "title": "Ship worker", "goal": "Open a PR"}
    )
    manager.claim_next_execution_step = AsyncMock(
        return_value={
            "step": {
                "step_id": "11111111-1111-1111-1111-111111111111",
                "step_index": 0,
                "title": "Open PR",
                "prompt_text": "Open the PR",
            },
            "retry": {
                "retry_id": "22222222-2222-2222-2222-222222222222",
                "attempt_number": 1,
            },
        }
    )
    manager.fail_execution_step = AsyncMock(
        return_value={
            "retry_scheduled": False,
            "next_run_at": None,
            "failure_category": "dependency",
            "plan": {"status": "failed"},
            "step": {"status": "blocked"},
        }
    )
    manager.record_execution_artifact = AsyncMock(return_value={"artifact_id": "a1"})
    manager.release_execution_plan_lease = AsyncMock(return_value=None)

    agent = AsyncMock()
    agent.generate_response = AsyncMock(side_effect=RuntimeError("dependency unavailable"))
    review_inbox = AsyncMock()

    executor = PlanContinuationExecutor(
        tenant_admin_manager=manager,
        agent=agent,
        review_inbox=review_inbox,
    )
    result = await executor.execute({"tenant_id": "tenant-1", "plan_id": "plan-1"})

    assert result["failure_category"] == "dependency"
    review_inbox.enqueue_execution_failure.assert_awaited_once()
    kwargs = review_inbox.enqueue_execution_failure.await_args.kwargs
    assert kwargs["user_id"] == 42
    assert kwargs["failure_category"] == "dependency"
    assert kwargs["step_title"] == "Open PR"


def test_attach_review_inbox_replaces_instance() -> None:
    executor = PlanContinuationExecutor(tenant_admin_manager=AsyncMock(), agent=None)
    review_inbox = AsyncMock()

    executor.attach_review_inbox(review_inbox)

    assert executor._review_inbox is review_inbox  # noqa: SLF001 - direct branch coverage


@pytest.mark.asyncio
async def test_maybe_enqueue_plan_completion_summary_skips_non_numeric_owner() -> None:
    review_inbox = AsyncMock()
    executor = PlanContinuationExecutor(
        tenant_admin_manager=AsyncMock(),
        agent=None,
        review_inbox=review_inbox,
    )

    await executor._maybe_enqueue_plan_completion_summary(  # noqa: SLF001 - direct branch coverage
        tenant_id="tenant-1",
        plan_id="plan-1",
        plan={"created_by": "owner"},
        completion={"plan": {"status": "completed"}},
        response_text="done",
    )

    review_inbox.enqueue_overnight_summary.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_enqueue_execution_failure_skips_non_numeric_owner() -> None:
    review_inbox = AsyncMock()
    executor = PlanContinuationExecutor(
        tenant_admin_manager=AsyncMock(),
        agent=None,
        review_inbox=review_inbox,
    )

    await executor._maybe_enqueue_execution_failure(  # noqa: SLF001 - direct branch coverage
        tenant_id="tenant-1",
        plan_id="plan-1",
        plan={"created_by": "owner"},
        step={"step_id": "step-1", "title": "Open PR"},
        failure={"failure_category": "dependency"},
        failure_detail="broken",
    )

    review_inbox.enqueue_execution_failure.assert_not_awaited()


@pytest.mark.asyncio
async def test_maybe_enqueue_reconciled_review_item_completed_enqueues_summary() -> None:
    review_inbox = AsyncMock()
    executor = PlanContinuationExecutor(
        tenant_admin_manager=AsyncMock(),
        agent=None,
        review_inbox=review_inbox,
    )

    await executor._maybe_enqueue_reconciled_review_item(  # noqa: SLF001 - direct branch coverage
        tenant_id="tenant-1",
        plan_id="plan-1",
        plan={
            "created_by": "42",
            "status": "completed",
            "title": "Ship worker",
            "goal": "Open a PR",
            "total_steps": 3,
        },
    )

    review_inbox.enqueue_overnight_summary.assert_awaited_once()
    kwargs = review_inbox.enqueue_overnight_summary.await_args.kwargs
    assert kwargs["user_id"] == 42
    assert kwargs["status"] == "completed"


@pytest.mark.asyncio
async def test_maybe_enqueue_reconciled_review_item_failed_enqueues_failure() -> None:
    review_inbox = AsyncMock()
    executor = PlanContinuationExecutor(
        tenant_admin_manager=AsyncMock(),
        agent=None,
        review_inbox=review_inbox,
    )

    await executor._maybe_enqueue_reconciled_review_item(  # noqa: SLF001 - direct branch coverage
        tenant_id="tenant-1",
        plan_id="plan-1",
        plan={
            "created_by": "42",
            "status": "failed",
            "title": "Ship worker",
            "last_error_category": "dependency",
            "last_error_detail": "Provider unavailable",
        },
    )

    review_inbox.enqueue_execution_failure.assert_awaited_once()
    kwargs = review_inbox.enqueue_execution_failure.await_args.kwargs
    assert kwargs["user_id"] == 42
    assert kwargs["item_type"] == executor._failed_review_item_type("failed")  # noqa: SLF001


def test_optional_int_and_failed_review_item_type_helpers() -> None:
    assert PlanContinuationExecutor._optional_int("7") == 7  # noqa: SLF001 - direct branch coverage
    assert PlanContinuationExecutor._optional_int("bad") is None  # noqa: SLF001
    assert (
        PlanContinuationExecutor._failed_review_item_type("failed").value  # noqa: SLF001
        == "failed"
    )
    assert (
        PlanContinuationExecutor._failed_review_item_type("blocked").value  # noqa: SLF001
        == "blocked"
    )
