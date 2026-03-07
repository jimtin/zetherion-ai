"""Execution-plan continuation worker for queue-driven overnight runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from zetherion_ai.logging import get_logger
from zetherion_ai.personal.models import PersonalReviewItemType

log = get_logger("zetherion_ai.queue.plan_executor")


class PlanContinuationExecutor:
    """Runs one continuation tick for a persisted tenant execution plan."""

    def __init__(
        self,
        *,
        tenant_admin_manager: Any,
        agent: Any = None,
        review_inbox: Any = None,
        worker_id_prefix: str = "plan-worker",
        lease_seconds: int = 90,
        stale_step_seconds: int = 300,
    ) -> None:
        self._tenant_admin_manager = tenant_admin_manager
        self._agent = agent
        self._review_inbox = review_inbox
        self._worker_id_prefix = worker_id_prefix
        self._lease_seconds = max(15, min(int(lease_seconds), 3600))
        self._stale_step_seconds = max(30, min(int(stale_step_seconds), 7200))

    def attach_review_inbox(self, review_inbox: Any) -> None:
        """Attach a canonical owner review inbox after executor construction."""

        self._review_inbox = review_inbox

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process one queue payload for `plan_continuation`."""
        tenant_id = str(payload.get("tenant_id") or "").strip()
        plan_id = str(payload.get("plan_id") or "").strip()
        if not tenant_id or not plan_id:
            raise ValueError("plan_continuation payload requires tenant_id and plan_id")

        worker_id = f"{self._worker_id_prefix}-{uuid4().hex[:10]}"
        lease_token = uuid4().hex
        plan_claimed = False
        plan: dict[str, Any] | None = None
        claimed_step: dict[str, Any] | None = None
        claimed_retry: dict[str, Any] | None = None

        try:
            plan = await self._tenant_admin_manager.claim_execution_plan_lease(
                tenant_id=tenant_id,
                plan_id=plan_id,
                worker_id=worker_id,
                lease_seconds=self._lease_seconds,
            )
            if plan is None:
                return {
                    "accepted": True,
                    "tenant_id": tenant_id,
                    "plan_id": plan_id,
                    "noop": "lease_not_acquired",
                }
            plan_claimed = True

            claim = await self._tenant_admin_manager.claim_next_execution_step(
                tenant_id=tenant_id,
                plan_id=plan_id,
                worker_id=worker_id,
                lease_token=lease_token,
                stale_running_seconds=self._stale_step_seconds,
            )
            if claim is None:
                reconciled = await self._tenant_admin_manager.reconcile_execution_plan_status(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                )
                await self._maybe_enqueue_reconciled_review_item(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    plan=reconciled or plan,
                )
                return {
                    "accepted": True,
                    "tenant_id": tenant_id,
                    "plan_id": plan_id,
                    "noop": "no_claimable_step",
                    "plan": reconciled,
                }

            step_data = claim.get("step") if isinstance(claim, dict) else None
            retry_data = claim.get("retry") if isinstance(claim, dict) else None
            if not isinstance(step_data, dict) or not isinstance(retry_data, dict):
                raise ValueError("Execution step claim payload is malformed")

            claimed_step = step_data
            claimed_retry = retry_data
            step_id = str(claimed_step["step_id"])
            retry_id = str(claimed_retry["retry_id"])
            execution_target = self._execution_target_for_step(claimed_step)

            if execution_target != "windows_local":
                dispatch = await self._tenant_admin_manager.dispatch_execution_step_to_worker(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    step=claimed_step,
                    retry=claimed_retry,
                    dispatcher_id=worker_id,
                    from_status=str(claim.get("from_status") or "pending"),
                )
                await self._tenant_admin_manager.record_execution_artifact(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    artifact_type="step_dispatched",
                    artifact_json={
                        "execution_target": execution_target,
                        "job_id": dispatch.get("job_id"),
                        "step_index": claimed_step.get("step_index"),
                        "attempt_number": claimed_retry.get("attempt_number"),
                    },
                )
                monitor_at = datetime.now(UTC) + timedelta(seconds=self._stale_step_seconds)
                await self._tenant_admin_manager.schedule_execution_continuation(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    scheduled_for=monitor_at,
                    reason="worker_dispatch_monitor",
                    requested_by=worker_id,
                )
                return {
                    "accepted": True,
                    "tenant_id": tenant_id,
                    "plan_id": plan_id,
                    "step_id": step_id,
                    "execution_target": execution_target,
                    "dispatched": True,
                    "job_id": dispatch.get("job_id"),
                    "has_more": True,
                }

            prompt_text = str(claimed_step.get("prompt_text") or "").strip()
            if not prompt_text:
                raise ValueError("Execution step prompt is empty")

            await self._tenant_admin_manager.record_execution_artifact(
                tenant_id=tenant_id,
                plan_id=plan_id,
                step_id=step_id,
                retry_id=retry_id,
                artifact_type="step_prompt",
                artifact_json={
                    "prompt_text": prompt_text,
                    "step_index": claimed_step.get("step_index"),
                    "attempt_number": claimed_retry.get("attempt_number"),
                },
            )

            response_text = await self._prompt_agent(
                prompt_text=prompt_text,
                plan=plan,
                step=claimed_step,
            )

            await self._tenant_admin_manager.record_execution_artifact(
                tenant_id=tenant_id,
                plan_id=plan_id,
                step_id=step_id,
                retry_id=retry_id,
                artifact_type="agent_response",
                artifact_json={
                    "response_text": response_text,
                    "generated_at": datetime.now().isoformat(),
                },
            )

            completion = await self._tenant_admin_manager.complete_execution_step(
                tenant_id=tenant_id,
                plan_id=plan_id,
                step_id=step_id,
                retry_id=retry_id,
                worker_id=worker_id,
                output_json={"response_text": response_text},
            )
            if completion.get("has_more") and completion.get("next_run_at") is not None:
                await self._tenant_admin_manager.schedule_execution_continuation(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    scheduled_for=completion["next_run_at"],
                    reason="step_completed",
                    requested_by=worker_id,
                )
            if (
                not completion.get("has_more")
                and str(completion["plan"].get("status") or "") == "completed"
            ):
                await self._maybe_enqueue_plan_completion_summary(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    plan=plan,
                    completion=completion,
                    response_text=response_text,
                )
            return {
                "accepted": True,
                "tenant_id": tenant_id,
                "plan_id": plan_id,
                "step_id": step_id,
                "status": str(completion["plan"]["status"]),
                "has_more": bool(completion.get("has_more")),
            }
        except Exception as exc:
            category = self._categorize_failure(exc)
            log.warning(
                "plan_continuation_failed",
                tenant_id=tenant_id,
                plan_id=plan_id,
                worker_id=worker_id,
                failure_category=category,
                error=str(exc),
            )
            if claimed_step is not None and claimed_retry is not None:
                step_id = str(claimed_step["step_id"])
                retry_id = str(claimed_retry["retry_id"])
                failure = await self._tenant_admin_manager.fail_execution_step(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    worker_id=worker_id,
                    failure_category=category,
                    failure_detail=str(exc),
                )
                await self._tenant_admin_manager.record_execution_artifact(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    step_id=step_id,
                    retry_id=retry_id,
                    artifact_type="step_failure",
                    artifact_json={
                        "failure_category": category,
                        "failure_detail": str(exc),
                        "retry_scheduled": bool(failure.get("retry_scheduled")),
                    },
                )
                if failure.get("retry_scheduled") and failure.get("next_run_at") is not None:
                    await self._tenant_admin_manager.schedule_execution_continuation(
                        tenant_id=tenant_id,
                        plan_id=plan_id,
                        scheduled_for=failure["next_run_at"],
                        reason="step_retry_scheduled",
                        requested_by=worker_id,
                    )
                else:
                    await self._maybe_enqueue_execution_failure(
                        tenant_id=tenant_id,
                        plan_id=plan_id,
                        plan=plan,
                        step=claimed_step,
                        failure=failure,
                        failure_detail=str(exc),
                    )
            return {
                "accepted": True,
                "tenant_id": tenant_id,
                "plan_id": plan_id,
                "error": str(exc),
                "failure_category": category,
            }
        finally:
            if plan_claimed:
                await self._tenant_admin_manager.release_execution_plan_lease(
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    worker_id=worker_id,
                )

    async def _maybe_enqueue_plan_completion_summary(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        plan: dict[str, Any],
        completion: dict[str, Any],
        response_text: str,
    ) -> None:
        if self._review_inbox is None:
            return
        user_id = self._owner_user_id(plan)
        if user_id is None:
            return
        try:
            step = completion.get("step") if isinstance(completion.get("step"), dict) else {}
            completed_plan = (
                completion.get("plan") if isinstance(completion.get("plan"), dict) else plan
            )
            await self._review_inbox.enqueue_overnight_summary(
                user_id=user_id,
                tenant_id=tenant_id,
                plan_id=plan_id,
                plan_title=str(completed_plan.get("title") or plan.get("title") or plan_id),
                goal=str(completed_plan.get("goal") or plan.get("goal") or "").strip() or None,
                summary=response_text,
                step_id=str(step.get("step_id") or "").strip() or None,
                step_index=self._optional_int(step.get("step_index")),
                total_steps=self._optional_int(completed_plan.get("total_steps")),
                status=str(completed_plan.get("status") or "completed"),
            )
        except Exception:
            log.exception(
                "plan_completion_review_enqueue_failed",
                tenant_id=tenant_id,
                plan_id=plan_id,
            )

    async def _maybe_enqueue_execution_failure(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        plan: dict[str, Any] | None,
        step: dict[str, Any] | None,
        failure: dict[str, Any],
        failure_detail: str,
    ) -> None:
        if self._review_inbox is None or plan is None or step is None:
            return
        user_id = self._owner_user_id(plan)
        if user_id is None:
            return
        try:
            await self._review_inbox.enqueue_execution_failure(
                user_id=user_id,
                tenant_id=tenant_id,
                plan_id=plan_id,
                plan_title=str(plan.get("title") or plan_id),
                step_id=str(step.get("step_id") or "").strip() or None,
                step_title=str(step.get("title") or step.get("step_id") or "blocked step"),
                failure_category=str(failure.get("failure_category") or "transient"),
                failure_detail=failure_detail,
            )
        except Exception:
            log.exception(
                "plan_failure_review_enqueue_failed",
                tenant_id=tenant_id,
                plan_id=plan_id,
            )

    async def _maybe_enqueue_reconciled_review_item(
        self,
        *,
        tenant_id: str,
        plan_id: str,
        plan: dict[str, Any],
    ) -> None:
        if self._review_inbox is None:
            return
        user_id = self._owner_user_id(plan)
        if user_id is None:
            return

        status = str(plan.get("status") or "").strip().lower()
        try:
            if status == "completed":
                await self._review_inbox.enqueue_overnight_summary(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    plan_title=str(plan.get("title") or plan_id),
                    goal=str(plan.get("goal") or "").strip() or None,
                    summary="All queued execution steps completed.",
                    total_steps=self._optional_int(plan.get("total_steps")),
                    status=status,
                )
            elif status == "failed":
                await self._review_inbox.enqueue_execution_failure(
                    user_id=user_id,
                    tenant_id=tenant_id,
                    plan_id=plan_id,
                    plan_title=str(plan.get("title") or plan_id),
                    step_id=None,
                    step_title=str(plan.get("title") or plan_id),
                    failure_category=str(plan.get("last_error_category") or "failed"),
                    failure_detail=str(plan.get("last_error_detail") or "Execution plan failed."),
                    item_type=self._failed_review_item_type(status),
                )
        except Exception:
            log.exception(
                "plan_reconcile_review_enqueue_failed",
                tenant_id=tenant_id,
                plan_id=plan_id,
                status=status,
            )

    @staticmethod
    def _owner_user_id(plan: dict[str, Any] | None) -> int | None:
        if not isinstance(plan, dict):
            return None
        created_by = str(plan.get("created_by") or "").strip()
        if created_by.isdigit():
            return int(created_by)
        return None

    @staticmethod
    def _optional_int(value: Any) -> int | None:
        try:
            return int(value) if value is not None else None
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _failed_review_item_type(status: str) -> PersonalReviewItemType:
        return (
            PersonalReviewItemType.FAILED if status == "failed" else PersonalReviewItemType.BLOCKED
        )

    async def _prompt_agent(
        self,
        *,
        prompt_text: str,
        plan: dict[str, Any],
        step: dict[str, Any],
    ) -> str:
        if self._agent is None or not hasattr(self._agent, "generate_response"):
            return f"Recorded step without agent runtime: {step.get('title') or 'step'}"

        user_id = 0
        created_by = str(plan.get("created_by") or "").strip()
        if created_by.isdigit():
            user_id = int(created_by)

        response = await self._agent.generate_response(
            user_id=user_id,
            channel_id=0,
            message=prompt_text,
        )
        return str(response or "").strip() or "Agent returned empty output."

    @staticmethod
    def _categorize_failure(exc: Exception) -> str:
        text = str(exc).lower()
        if "timeout" in text:
            return "timeout"
        if "rate" in text and "limit" in text:
            return "rate_limit"
        if "dependency" in text or "unavailable" in text:
            return "dependency"
        if "cancel" in text or "interrupted" in text:
            return "interrupted"
        return "transient"

    @staticmethod
    def _execution_target_for_step(step: dict[str, Any]) -> str:
        raw = str(step.get("execution_target") or "windows_local").strip().lower()
        if raw in {"windows_local", "any_worker"}:
            return raw
        if raw.startswith("worker:") and raw[7:].strip():
            return raw
        return "windows_local"
