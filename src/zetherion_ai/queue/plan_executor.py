"""Execution-plan continuation worker for queue-driven overnight runs."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from zetherion_ai.logging import get_logger

log = get_logger("zetherion_ai.queue.plan_executor")


class PlanContinuationExecutor:
    """Runs one continuation tick for a persisted tenant execution plan."""

    def __init__(
        self,
        *,
        tenant_admin_manager: Any,
        agent: Any = None,
        worker_id_prefix: str = "plan-worker",
        lease_seconds: int = 90,
        stale_step_seconds: int = 300,
    ) -> None:
        self._tenant_admin_manager = tenant_admin_manager
        self._agent = agent
        self._worker_id_prefix = worker_id_prefix
        self._lease_seconds = max(15, min(int(lease_seconds), 3600))
        self._stale_step_seconds = max(30, min(int(stale_step_seconds), 7200))

    async def execute(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Process one queue payload for `plan_continuation`."""
        tenant_id = str(payload.get("tenant_id") or "").strip()
        plan_id = str(payload.get("plan_id") or "").strip()
        if not tenant_id or not plan_id:
            raise ValueError("plan_continuation payload requires tenant_id and plan_id")

        worker_id = f"{self._worker_id_prefix}-{uuid4().hex[:10]}"
        lease_token = uuid4().hex
        plan_claimed = False
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
