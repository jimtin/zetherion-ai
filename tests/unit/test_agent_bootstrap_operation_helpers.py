"""Focused coverage for agent bootstrap operation helper paths."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.skills.agent_bootstrap import AgentBootstrapSkill


def _app_profile() -> dict[str, object]:
    return {
        "app_id": "catalyst-group-solutions",
        "profile": {
            "repo_ids": ["catalyst-group-solutions"],
            "service_connector_map": {
                "github": {"connector_id": "github-primary"},
                "stripe": {"connector_id": "stripe-primary"},
            },
        },
    }


def _operation() -> dict[str, object]:
    return {
        "operation_id": "op-1",
        "repo_id": "catalyst-group-solutions",
        "summary": {"status": "pending"},
        "metadata": {"source": "windows"},
    }


def _skill(storage: MagicMock) -> AgentBootstrapSkill:
    skill = AgentBootstrapSkill(storage=storage)
    skill._record_gap = AsyncMock(return_value={"gap_id": "gap-1"})  # type: ignore[method-assign]
    return skill


def test_provider_event_helpers_cover_ref_extraction_rendering_and_incident_mapping() -> None:
    skill = _skill(MagicMock())

    github_refs = skill._extract_operation_refs_from_event(  # noqa: SLF001
        service_kind="github",
        event_payload={
            "repository": {"full_name": "jimtin/zetherion-ai"},
            "workflow_run": {
                "id": 41,
                "head_sha": "a" * 40,
                "head_branch": "main",
                "pull_requests": [{"number": 164}],
                "conclusion": "failure",
            },
        },
    )
    vercel_refs = skill._extract_operation_refs_from_event(  # noqa: SLF001
        service_kind="vercel",
        event_payload={
            "id": "evt_vercel_1",
            "payload": {
                "id": "dep_1",
                "target": "production",
                "readyState": "ERROR",
                "meta": {
                    "githubCommitSha": "b" * 40,
                    "githubCommitRef": "main",
                },
            },
        },
    )
    clerk_refs = skill._extract_operation_refs_from_event(  # noqa: SLF001
        service_kind="clerk",
        event_payload={"id": "evt_clerk_1", "data": {"id": "instance_1"}},
    )
    stripe_refs = skill._extract_operation_refs_from_event(  # noqa: SLF001
        service_kind="stripe",
        event_payload={
            "id": "evt_stripe_1",
            "type": "customer.subscription.updated",
            "data": {"object": {"id": "sub_123", "customer": "cus_123"}},
        },
    )

    assert github_refs["repo_full_name"] == "jimtin/zetherion-ai"
    assert github_refs["pr_number"] == "164"
    assert vercel_refs["vercel_deployment_id"] == "dep_1"
    assert vercel_refs["git_sha"] == "b" * 40
    assert clerk_refs == {
        "clerk_event_id": "evt_clerk_1",
        "clerk_instance_ref": "instance_1",
    }
    assert stripe_refs["customer_id"] == "cus_123"
    assert stripe_refs["subscription_id"] == "sub_123"

    rendered_vercel = skill._render_event_log_lines(  # noqa: SLF001
        service_kind="vercel",
        event_type="deployment.error",
        event_payload={
            "payload": {
                "name": "cgs-web",
                "target": "production",
                "readyState": "ERROR",
            },
        },
    )
    rendered_clerk = skill._render_event_log_lines(  # noqa: SLF001
        service_kind="clerk",
        event_type="user.created",
        event_payload={
            "data": {
                "id": "user_123",
                "email_addresses": [{"email_address": "owner@example.com"}],
            },
        },
    )
    rendered_stripe = skill._render_event_log_lines(  # noqa: SLF001
        service_kind="stripe",
        event_type="customer.subscription.updated",
        event_payload={
            "data": {"object": {"id": "sub_123", "customer": "cus_123", "status": "past_due"}},
        },
    )

    assert "cgs-web" in rendered_vercel
    assert "production" in rendered_vercel
    assert "owner@example.com" in rendered_clerk
    assert "sub_123" in rendered_stripe
    assert "past_due" in rendered_stripe

    github_incident = skill._incident_from_provider_event(  # noqa: SLF001
        service_kind="github",
        event_type="workflow_run.completed",
        event_payload={"workflow_run": {"conclusion": "failure"}},
    )
    vercel_incident = skill._incident_from_provider_event(  # noqa: SLF001
        service_kind="vercel",
        event_type="deployment.error",
        event_payload={"payload": {"readyState": "ERROR"}},
    )
    stripe_incident = skill._incident_from_provider_event(  # noqa: SLF001
        service_kind="stripe",
        event_type="customer.subscription.updated",
        event_payload={"pending_webhooks": 2},
    )
    clerk_incident = skill._incident_from_provider_event(  # noqa: SLF001
        service_kind="clerk",
        event_type="session.failed",
        event_payload={},
    )

    assert github_incident["incident_type"] == "workflow_failed"
    assert vercel_incident["incident_type"] == "deployment_failed"
    assert stripe_incident["incident_type"] == "webhook_pending"
    assert clerk_incident["incident_type"] == "auth_failed"
    assert skill._service_kind_for_operation_ref("subscription_id") == "stripe"  # noqa: SLF001
    assert skill._service_kind_for_operation_ref("github_run_id") == "github"  # noqa: SLF001
    assert skill._service_kind_for_operation_ref("unknown_ref") is None  # noqa: SLF001


def test_provider_event_helpers_cover_non_incident_paths_and_additional_ref_mappings() -> None:
    skill = _skill(MagicMock())

    top_level_vercel_incident = skill._incident_from_provider_event(  # noqa: SLF001
        service_kind="vercel",
        event_type="deployment.error",
        event_payload={"state": "FAILED"},
    )
    no_vercel_incident = skill._incident_from_provider_event(  # noqa: SLF001
        service_kind="vercel",
        event_type="deployment.ready",
        event_payload={"payload": {"readyState": "READY"}},
    )
    no_stripe_incident = skill._incident_from_provider_event(  # noqa: SLF001
        service_kind="stripe",
        event_type="customer.subscription.updated",
        event_payload={"pending_webhooks": 0},
    )

    assert top_level_vercel_incident["incident_type"] == "deployment_failed"
    assert no_vercel_incident is None
    assert no_stripe_incident is None
    assert skill._service_kind_for_operation_ref("github_delivery_id") == "github"  # noqa: SLF001
    assert skill._service_kind_for_operation_ref("vercel_deployment_id") == "vercel"  # noqa: SLF001
    assert skill._service_kind_for_operation_ref("vercel_event_id") == "vercel"  # noqa: SLF001
    assert skill._service_kind_for_operation_ref("issuer") == "clerk"  # noqa: SLF001
    assert skill._service_kind_for_operation_ref("clerk_event_id") == "clerk"  # noqa: SLF001
    assert skill._service_kind_for_operation_ref("stripe_event_id") == "stripe"  # noqa: SLF001


def test_provider_event_helpers_cover_payload_normalization_and_sparse_refs() -> None:
    skill = _skill(MagicMock())

    assert skill._normalize_event_payload({"ok": True}) == {"ok": True}  # noqa: SLF001
    assert skill._normalize_event_payload('{"ok": true}') == {"ok": True}  # noqa: SLF001
    assert skill._normalize_event_payload('["not", "a", "dict"]') == {}  # noqa: SLF001
    assert skill._normalize_event_payload(object()) == {}  # noqa: SLF001

    vercel_refs = skill._extract_operation_refs_from_event(  # noqa: SLF001
        service_kind="vercel",
        event_payload={
            "payload": {
                "id": "dep_2",
                "meta": {
                    "githubCommitSha": "c" * 40,
                },
            },
        },
    )
    clerk_refs = skill._extract_operation_refs_from_event(  # noqa: SLF001
        service_kind="clerk",
        event_payload={"id": "evt_clerk_2", "data": {}},
    )
    stripe_refs = skill._extract_operation_refs_from_event(  # noqa: SLF001
        service_kind="stripe",
        event_payload={
            "id": "evt_stripe_2",
            "type": "invoice.paid",
            "data": {"object": {"id": "in_456"}},
        },
    )

    assert vercel_refs == {
        "vercel_deployment_id": "dep_2",
        "git_sha": "c" * 40,
    }
    assert clerk_refs == {"clerk_event_id": "evt_clerk_2"}
    assert stripe_refs == {"stripe_event_id": "evt_stripe_2"}


@pytest.mark.asyncio
async def test_detect_test_plan_gaps_adds_playwright_and_skips_supported_tooling() -> None:
    storage = MagicMock()
    storage.list_secret_refs = AsyncMock(return_value=[])
    skill = _skill(storage)
    skill._record_gap = AsyncMock(return_value={"gap_id": "gap-playwright"})  # type: ignore[method-assign]

    gaps = await skill._detect_test_plan_gaps(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        session_id="sess-1",
        app_id="catalyst-group-solutions",
        repo_id="catalyst-group-solutions",
        knowledge_pack={"capability_registry": {"supported_tooling": ["pytest"]}},
        request_context={
            "focus": "Authentication hardening",
            "changed_files": ["tests/playwright/auth/login.spec.ts"],
            "required_tooling": ["pytest"],
        },
    )

    assert gaps == [{"gap_id": "gap-playwright"}, {"gap_id": "gap-playwright"}]
    assert skill._record_gap.await_count == 2  # type: ignore[attr-defined]
    first_gap_kwargs = skill._record_gap.await_args_list[0].kwargs  # type: ignore[attr-defined]
    second_gap_kwargs = skill._record_gap.await_args_list[1].kwargs  # type: ignore[attr-defined]
    assert first_gap_kwargs["required_capability"] == "playwright"
    assert first_gap_kwargs["observed_request"]["required_tooling"] == ["playwright", "pytest"]
    assert second_gap_kwargs["required_capability"] == "playwright"
    assert second_gap_kwargs["gap_type"] == "missing_test_harness"


@pytest.mark.asyncio
async def test_collect_ci_runtime_operation_evidence_records_summary_logs_bundle_and_incident() -> (
    None
):
    storage = MagicMock()
    storage.get_run = AsyncMock(
        return_value={
            "run_id": "run-1",
            "repo_id": "catalyst-group-solutions",
            "status": "failed",
            "shards": [
                {
                    "shard_id": "shard-1",
                    "lane_id": "unit-full",
                    "status": "failed",
                    "result": {
                        "coverage_summary": {
                            "passed": False,
                            "metrics": {"branches": {"passed": False, "actual": 88.4}},
                            "artifacts": {
                                "coverage_json": ".artifacts/coverage/coverage.json",
                                "coverage_report": ".artifacts/coverage/coverage-report.txt",
                            },
                        },
                        "coverage_gaps": {"gaps": [{"identifier": "foo"}]},
                    },
                    "error": {},
                }
            ],
        }
    )
    storage.get_run_events = AsyncMock(return_value=[{"event_type": "worker.result.accepted"}])
    storage.get_run_log_chunks = AsyncMock(
        return_value=[
            {"message": "worker_error: queue stalled"},
            {"message": "compose stderr"},
        ]
    )
    storage.get_run_debug_bundle = AsyncMock(
        return_value={
            "bundle_id": "bundle-1",
            "shard_id": "shard-1",
            "bundle": {
                "cleanup_receipt": {"status": "clean"},
                "container_receipts": [{"container": "bot"}],
                "compose_state": {"bot": "running"},
            },
        }
    )
    storage.record_operation_evidence = AsyncMock(
        side_effect=[
            {"evidence_id": "summary-1"},
            {"evidence_id": "events-1"},
            {"evidence_id": "logs-1"},
            {"evidence_id": "bundle-1"},
            {"evidence_id": "coverage-summary-1"},
            {"evidence_id": "coverage-gaps-1"},
            {"evidence_id": "diagnostic-summary-1"},
            {"evidence_id": "diagnostic-findings-1"},
            {"evidence_id": "diagnostic-artifacts-1"},
        ]
    )
    storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})

    skill = _skill(storage)

    result = await skill._collect_ci_runtime_operation_evidence(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation_id="op-1",
        run_id="run-1",
        request_context={"session_id": "sess-1"},
    )

    assert result["status"] == "failed"
    assert result["lifecycle_stage"] == "ci_runtime"
    assert result["summary"]["event_count"] == 1
    assert result["summary"]["debug_bundle"]["bundle_id"] == "bundle-1"
    assert result["diagnostic_summary"]["blocking"] is True
    assert result["diagnostic_summary"]["diagnostic_artifacts"]
    assert storage.record_operation_evidence.await_count == 9
    assert storage.record_operation_incident.await_count >= 1
    skill._record_gap.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_collect_ci_runtime_operation_evidence_for_missing_run_and_logs() -> None:
    storage = MagicMock()
    storage.get_run = AsyncMock(
        side_effect=[
            None,
            {"run_id": "run-1", "repo_id": "repo", "status": "succeeded"},
        ]
    )
    storage.get_run_events = AsyncMock(return_value=[])
    storage.get_run_log_chunks = AsyncMock(return_value=[])
    storage.get_run_debug_bundle = AsyncMock(return_value=None)
    storage.record_operation_evidence = AsyncMock(return_value={"evidence_id": "summary-1"})
    storage.record_operation_incident = AsyncMock()

    skill = _skill(storage)

    missing = await skill._collect_ci_runtime_operation_evidence(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation_id="op-1",
        run_id="missing-run",
        request_context={"session_id": "sess-1"},
    )
    present = await skill._collect_ci_runtime_operation_evidence(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation_id="op-1",
        run_id="run-1",
        request_context={"session_id": "sess-1"},
    )

    assert missing == {}
    assert present["status"] == "succeeded"
    assert storage.record_operation_evidence.await_count == 1
    assert skill._record_gap.await_count == 2  # type: ignore[attr-defined]
    storage.record_operation_incident.assert_not_awaited()


@pytest.mark.asyncio
async def test_collect_ci_runtime_operation_evidence_fallback_incident_without_diagnostics() -> (
    None
):
    storage = MagicMock()
    storage.get_run = AsyncMock(
        return_value={
            "run_id": "run-1",
            "repo_id": "catalyst-group-solutions",
            "status": "failed",
            "shards": [],
        }
    )
    storage.get_run_events = AsyncMock(return_value=[])
    storage.get_run_log_chunks = AsyncMock(
        return_value=[
            {"message": "worker_error: release verification failed"},
            {"message": "secondary detail"},
        ]
    )
    storage.get_run_debug_bundle = AsyncMock(return_value=None)
    storage.record_operation_evidence = AsyncMock(return_value={"evidence_id": "summary-1"})
    storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})

    skill = _skill(storage)

    result = await skill._collect_ci_runtime_operation_evidence(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation_id="op-1",
        run_id="run-1",
        request_context={"session_id": "sess-1"},
    )

    assert result["status"] == "failed"
    assert result["diagnostic_findings"] == []
    storage.record_operation_incident.assert_awaited_once()
    kwargs = storage.record_operation_incident.await_args.kwargs
    assert kwargs["incident_type"] == "ci_runtime_failed"
    assert kwargs["root_cause_summary"] == "worker_error: release verification failed"
    assert kwargs["evidence_refs"] == ["summary-1"]


@pytest.mark.asyncio
async def test_collect_ci_runtime_operation_evidence_leaves_in_progress_runs_active() -> None:
    storage = MagicMock()
    storage.get_run = AsyncMock(
        return_value={
            "run_id": "run-1",
            "repo_id": "catalyst-group-solutions",
            "status": "running",
            "shards": [],
        }
    )
    storage.get_run_events = AsyncMock(return_value=[])
    storage.get_run_log_chunks = AsyncMock(return_value=[])
    storage.get_run_debug_bundle = AsyncMock(return_value=None)
    storage.record_operation_evidence = AsyncMock(return_value={"evidence_id": "summary-1"})
    storage.record_operation_incident = AsyncMock()

    skill = _skill(storage)

    result = await skill._collect_ci_runtime_operation_evidence(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation_id="op-1",
        run_id="run-1",
        request_context={"session_id": "sess-1"},
    )

    assert result["status"] == "active"
    assert result["diagnostic_findings"] == []
    storage.record_operation_incident.assert_not_awaited()
    skill._record_gap.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_collect_ci_runtime_operation_evidence_skips_diagnostic_artifacts_when_empty() -> (
    None
):
    storage = MagicMock()
    storage.get_run = AsyncMock(
        return_value={
            "run_id": "run-2",
            "repo_id": "catalyst-group-solutions",
            "status": "failed",
            "shards": [],
        }
    )
    storage.get_run_events = AsyncMock(return_value=[])
    storage.get_run_log_chunks = AsyncMock(return_value=[])
    storage.get_run_debug_bundle = AsyncMock(return_value=None)
    storage.record_operation_evidence = AsyncMock(
        side_effect=[
            {"evidence_id": "summary-1"},
            {"evidence_id": "diagnostic-summary-1"},
            {"evidence_id": "diagnostic-findings-1"},
        ]
    )
    storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})

    skill = _skill(storage)

    with patch(
        "zetherion_ai.skills.agent_bootstrap.build_run_diagnostics",
        return_value=(
            {
                "blocking": True,
                "diagnostic_artifacts": [],
            },
            [
                {
                    "code": "ci_runtime_failed",
                    "severity": "high",
                    "blocking": True,
                    "summary": "runtime failed",
                }
            ],
        ),
    ):
        result = await skill._collect_ci_runtime_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            run_id="run-2",
            request_context={"session_id": "sess-1"},
        )

    assert result["status"] == "failed"
    assert result["diagnostic_summary"]["diagnostic_artifacts"] == []
    assert storage.record_operation_evidence.await_count == 3
    storage.record_operation_incident.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_service_operation_evidence_dispatches_known_services_and_unknown_gap() -> (
    None
):
    storage = MagicMock()
    skill = _skill(storage)
    skill._collect_github_operation_evidence = AsyncMock(return_value={"status": "succeeded"})  # type: ignore[method-assign]
    skill._collect_vercel_operation_evidence = AsyncMock(return_value={"status": "succeeded"})  # type: ignore[method-assign]
    skill._collect_clerk_operation_evidence = AsyncMock(return_value={"status": "succeeded"})  # type: ignore[method-assign]
    skill._collect_stripe_operation_evidence = AsyncMock(return_value={"status": "succeeded"})  # type: ignore[method-assign]

    github = await skill._collect_service_operation_evidence(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation_id="op-1",
        service_kind="github",
        refs={"git_sha": "a" * 40},
        request_context={"session_id": "sess-1"},
        public_base_url="https://cgs.example.com",
    )
    vercel = await skill._collect_service_operation_evidence(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation_id="op-1",
        service_kind="vercel",
        refs={"vercel_deployment_id": "dep_1"},
        request_context={"session_id": "sess-1"},
        public_base_url="https://cgs.example.com",
    )
    clerk = await skill._collect_service_operation_evidence(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation_id="op-1",
        service_kind="clerk",
        refs={"clerk_event_id": "evt_1"},
        request_context={"session_id": "sess-1"},
        public_base_url="https://cgs.example.com",
    )
    stripe = await skill._collect_service_operation_evidence(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation_id="op-1",
        service_kind="stripe",
        refs={"stripe_event_id": "evt_1"},
        request_context={"session_id": "sess-1"},
        public_base_url="https://cgs.example.com",
    )
    unknown = await skill._collect_service_operation_evidence(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation_id="op-1",
        service_kind="docker",
        refs={},
        request_context={"session_id": "sess-1"},
        public_base_url="https://cgs.example.com",
    )

    assert github["status"] == "succeeded"
    assert vercel["status"] == "succeeded"
    assert clerk["status"] == "succeeded"
    assert stripe["status"] == "succeeded"
    assert unknown == {}
    skill._collect_github_operation_evidence.assert_awaited_once()  # type: ignore[attr-defined]
    skill._collect_vercel_operation_evidence.assert_awaited_once()  # type: ignore[attr-defined]
    skill._collect_clerk_operation_evidence.assert_awaited_once()  # type: ignore[attr-defined]
    skill._collect_stripe_operation_evidence.assert_awaited_once()  # type: ignore[attr-defined]
    skill._record_gap.assert_awaited_once()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refresh_operation_handles_runtime_adapter_success_and_missing_connector() -> None:
    storage = MagicMock()
    storage.list_operation_refs = AsyncMock(
        return_value=[
            {"ref_kind": "run_id", "ref_value": "run-1"},
            {"ref_kind": "git_sha", "ref_value": "a" * 40},
        ]
    )
    storage.get_service_adapter_capability = AsyncMock(
        side_effect=[None, {"manifest": {"ok": True}}]
    )
    storage.update_managed_operation = AsyncMock(return_value=_operation())

    skill = _skill(storage)
    skill._collect_ci_runtime_operation_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "summary": {"status": "green"},
            "status": "succeeded",
            "lifecycle_stage": "ci_runtime",
        }
    )
    skill._collect_service_operation_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "summary": {"status": "green"},
            "status": "succeeded",
            "lifecycle_stage": "stripe",
        }
    )

    await skill._refresh_operation(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation=_operation(),
        request_context={"session_id": "sess-1", "service_kind": "stripe"},
        public_base_url="https://cgs.example.com",
    )

    skill._record_gap.assert_awaited_once()  # type: ignore[attr-defined]
    storage.update_managed_operation.assert_awaited_once()
    update_kwargs = storage.update_managed_operation.await_args.kwargs
    assert update_kwargs["status"] == "succeeded"
    assert update_kwargs["lifecycle_stage"] == "stripe"
    assert update_kwargs["summary"]["resolved_refs"]["run_id"] == "run-1"
    assert set(update_kwargs["summary"]["services"]) == {"ci_runtime", "stripe"}


@pytest.mark.asyncio
async def test_refresh_operation_handles_service_only_success_without_runtime() -> None:
    storage = MagicMock()
    storage.list_operation_refs = AsyncMock(
        return_value=[{"ref_kind": "vercel_deployment_id", "ref_value": "dep-1"}]
    )
    storage.get_service_adapter_capability = AsyncMock(return_value={"manifest": {"ok": True}})
    storage.update_managed_operation = AsyncMock(return_value=_operation())

    skill = _skill(storage)
    skill._collect_ci_runtime_operation_evidence = AsyncMock(return_value=None)  # type: ignore[method-assign]
    skill._collect_service_operation_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "summary": {"status": "green"},
            "status": "succeeded",
            "lifecycle_stage": "vercel",
        }
    )

    await skill._refresh_operation(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation=_operation(),
        request_context={"session_id": "sess-1", "service_kind": "vercel"},
        public_base_url="https://cgs.example.com",
    )

    update_kwargs = storage.update_managed_operation.await_args.kwargs
    assert update_kwargs["status"] == "succeeded"
    assert update_kwargs["lifecycle_stage"] == "vercel"
    assert update_kwargs["summary"]["services"] == {"vercel": {"status": "green"}}
    skill._record_gap.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refresh_operation_keeps_active_status_when_adapter_returns_no_payload() -> None:
    storage = MagicMock()
    storage.list_operation_refs = AsyncMock(
        return_value=[
            {"ref_kind": "run_id", "ref_value": "run-1"},
            {"ref_kind": "github_delivery_id", "ref_value": "delivery-1"},
        ]
    )
    storage.get_service_adapter_capability = AsyncMock(return_value={"manifest": {"ok": True}})
    storage.update_managed_operation = AsyncMock(return_value=_operation())

    skill = _skill(storage)
    skill._collect_ci_runtime_operation_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "summary": {"status": "running"},
            "status": "active",
            "lifecycle_stage": "ci_runtime",
        }
    )
    skill._collect_service_operation_evidence = AsyncMock(return_value={})  # type: ignore[method-assign]

    await skill._refresh_operation(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation=_operation(),
        request_context={"session_id": "sess-1", "service_kind": "github"},
        public_base_url="https://cgs.example.com",
    )

    update_kwargs = storage.update_managed_operation.await_args.kwargs
    assert update_kwargs["status"] == "active"
    assert update_kwargs["lifecycle_stage"] == "ci_runtime"
    assert update_kwargs["summary"]["services"] == {"ci_runtime": {"status": "running"}}
    skill._record_gap.assert_not_awaited()  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_refresh_operation_marks_failed_when_any_adapter_fails() -> None:
    storage = MagicMock()
    storage.list_operation_refs = AsyncMock(
        return_value=[
            {"ref_kind": "run_id", "ref_value": "run-1"},
            {"ref_kind": "vercel_deployment_id", "ref_value": "dep-1"},
        ]
    )
    storage.get_service_adapter_capability = AsyncMock(return_value={"manifest": {"ok": True}})
    storage.update_managed_operation = AsyncMock(return_value=_operation())

    skill = _skill(storage)
    skill._collect_ci_runtime_operation_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "summary": {"status": "green"},
            "status": "succeeded",
            "lifecycle_stage": "ci_runtime",
        }
    )
    skill._collect_service_operation_evidence = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "summary": {"status": "red"},
            "status": "failed",
            "lifecycle_stage": "vercel",
        }
    )

    await skill._refresh_operation(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=_app_profile(),
        operation=_operation(),
        request_context={"session_id": "sess-1", "service_kind": "vercel"},
        public_base_url="https://cgs.example.com",
    )

    update_kwargs = storage.update_managed_operation.await_args.kwargs
    assert update_kwargs["status"] == "failed"
    assert update_kwargs["lifecycle_stage"] == "vercel"
    assert update_kwargs["summary"]["services"] == {
        "ci_runtime": {"status": "green"},
        "vercel": {"status": "red"},
    }


@pytest.mark.asyncio
async def test_refresh_operation_records_workspace_gap_without_runtime_or_service_refs() -> None:
    storage = MagicMock()
    storage.list_operation_refs = AsyncMock(return_value=[])
    storage.update_managed_operation = AsyncMock()

    skill = _skill(storage)
    skill._collect_ci_runtime_operation_evidence = AsyncMock(return_value=None)  # type: ignore[method-assign]
    skill._collect_service_operation_evidence = AsyncMock(return_value=None)  # type: ignore[method-assign]

    await skill._refresh_operation(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile={"profile": {"repo_ids": ["catalyst-group-solutions"]}},
        operation=_operation(),
        request_context={"session_id": "sess-1"},
        public_base_url="https://cgs.example.com",
    )

    skill._record_gap.assert_awaited_once()  # type: ignore[attr-defined]
    storage.update_managed_operation.assert_not_awaited()
