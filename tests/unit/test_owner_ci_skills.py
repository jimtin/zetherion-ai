"""Unit tests for owner-scoped CI controller skills."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.owner_ci import default_repo_profile
from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.ci_controller import CiControllerSkill
from zetherion_ai.skills.ci_observer import CiObserverSkill
from zetherion_ai.skills.pr_reviewer import PrReviewerSkill, _normalize_owner_id


def _storage() -> MagicMock:
    storage = MagicMock()
    storage.upsert_repo_profile = AsyncMock()
    storage.list_repo_profiles = AsyncMock(return_value=[])
    storage.get_repo_profile = AsyncMock(return_value=None)
    storage.create_plan_snapshot = AsyncMock()
    storage.get_plan_snapshot = AsyncMock()
    storage.list_plan_versions = AsyncMock(return_value=[])
    storage.create_compiled_plan = AsyncMock(return_value={"compiled_plan_id": "compiled-1"})
    storage.upsert_schedule = AsyncMock()
    storage.list_schedules = AsyncMock(return_value=[])
    storage.create_run = AsyncMock()
    storage.get_run = AsyncMock()
    storage.list_runs = AsyncMock(return_value=[])
    storage.list_worker_nodes = AsyncMock(return_value=[])
    storage.get_reporting_readiness = AsyncMock(
        return_value={"workspace_readiness": {"merge_ready": False, "deploy_ready": False}}
    )
    storage.get_reporting_summary = AsyncMock(return_value={})
    storage.get_project_resource_report = AsyncMock(return_value={"items": [], "totals": {}})
    storage.get_worker_resource_report = AsyncMock(return_value={"samples": [], "totals": {}})
    storage.get_local_repo_readiness = AsyncMock(return_value=(None, None))
    storage.record_agent_gap_event = AsyncMock(
        side_effect=lambda *args, **kwargs: {
            "gap_id": "gap-1",
            "principal_id": kwargs.get("principal_id"),
            "repo_id": kwargs.get("repo_id"),
            "run_id": kwargs.get("run_id"),
            "gap_type": kwargs.get("gap_type"),
            "suggested_fix": kwargs.get("suggested_fix"),
            "blocker": kwargs.get("blocker", False),
            "status": "open",
            "occurrence_count": 1,
            "metadata": kwargs.get("metadata") or {},
            "first_seen_at": "2026-03-16T09:00:00Z",
            "updated_at": "2026-03-16T09:00:00Z",
        }
    )
    storage.get_run_report = AsyncMock(return_value={})
    storage.get_run_graph = AsyncMock(return_value={})
    storage.get_run_correlation_context = AsyncMock(return_value={})
    storage.get_run_diagnostics = AsyncMock(return_value={})
    storage.get_run_artifacts = AsyncMock(return_value=[])
    storage.get_run_evidence = AsyncMock(return_value=[])
    storage.get_run_debug_bundle = AsyncMock(return_value=None)
    storage.list_agent_coaching_feedback = AsyncMock(return_value=[])
    storage.list_managed_operations = AsyncMock(return_value=[])
    storage.get_operation_hydrated = AsyncMock(return_value=None)
    storage.store_run_github_receipt = AsyncMock()
    storage.merge_run_metadata = AsyncMock()
    storage.set_run_status = AsyncMock()
    storage.store_run_review = AsyncMock()
    return storage


def test_normalize_owner_id_prefers_context_then_user_then_default() -> None:
    assert (
        _normalize_owner_id(
            SkillRequest(
                user_id="user-1",
                context={"owner_id": "owner-1", "operator_id": "op-1", "actor_sub": "actor-1"},
            )
        )
        == "owner-1"
    )
    assert (
        _normalize_owner_id(
            SkillRequest(
                user_id="user-1",
                context={"operator_id": "op-1", "actor_sub": "actor-1"},
            )
        )
        == "op-1"
    )
    assert (
        _normalize_owner_id(SkillRequest(user_id="user-1", context={"actor_sub": "actor-1"}))
        == "actor-1"
    )
    assert _normalize_owner_id(SkillRequest(user_id="user-1")) == "user-1"
    assert _normalize_owner_id(SkillRequest(user_id="")) == "owner"


@pytest.mark.asyncio
async def test_ci_controller_seed_defaults_uses_built_in_profiles() -> None:
    storage = _storage()
    storage.upsert_repo_profile.side_effect = lambda owner_id, profile: {
        "owner_id": owner_id,
        **profile,
    }
    skill = CiControllerSkill(storage=storage)

    response = await skill.handle(SkillRequest(intent="ci_repo_seed_defaults", user_id="owner-1"))

    assert response.success is True
    assert len(response.data["repos"]) == 2
    seeded_repo_ids = [
        call.args[1]["repo_id"] for call in storage.upsert_repo_profile.await_args_list
    ]
    assert seeded_repo_ids == ["catalyst-group-solutions", "zetherion-ai"]


@pytest.mark.asyncio
async def test_ci_controller_certification_run_seeds_platform_canary_and_windows_lanes() -> None:
    storage = _storage()
    profile = default_repo_profile("zetherion-ai")
    assert profile is not None
    storage.get_repo_profile.return_value = profile
    storage.create_run.return_value = {"run_id": "run-123", "repo_id": "zetherion-ai"}
    skill = CiControllerSkill(storage=storage)

    response = await skill.handle(
        SkillRequest(
            intent="ci_run_start",
            user_id="owner-1",
            context={
                "repo_id": "zetherion-ai",
                "mode": "certification",
                "preflight_checks": {
                    "categories_completed": ["static", "security"],
                    "checks": [
                        {"id": "ruff-check", "status": "passed", "tool": "ruff"},
                        {"id": "ruff-format-check", "status": "passed", "tool": "ruff"},
                        {"id": "public-core-export", "status": "passed"},
                        {"id": "bandit", "status": "passed"},
                        {"id": "gitleaks", "status": "passed"},
                        {"id": "pip-audit", "status": "passed"},
                    ],
                },
            },
        )
    )

    assert response.success is True
    create_kwargs = storage.create_run.await_args.kwargs
    assert create_kwargs["scope_id"] == "owner:owner-1:repo:zetherion-ai"
    assert create_kwargs["metadata"]["certification_required"] is True
    assert create_kwargs["metadata"]["platform_canary"] is True
    assert create_kwargs["metadata"]["windows_execution_mode"] == "docker_only"
    assert create_kwargs["metadata"]["required_security_gates"] == [
        "bandit",
        "gitleaks",
        "pip-audit",
    ]
    assert create_kwargs["metadata"]["required_gate_categories"] == [
        "static",
        "security",
        "unit",
        "integration",
        "e2e",
    ]
    assert "discord_roundtrip" in create_kwargs["metadata"]["certification_requirements"]
    shards = create_kwargs["shards"]
    assert any(shard["execution_target"] == "windows_local" for shard in shards)
    assert any(shard["lane_id"] == "discord-required-e2e" for shard in shards)
    assert any(shard["execution_target"] == "local_mac" for shard in shards)
    assert any(shard["lane_id"] == "ruff-check" for shard in shards)
    assert any(shard["lane_id"] == "public-core-export" for shard in shards)
    assert any(shard["lane_id"] == "bandit" for shard in shards)
    assert all(
        shard.get("runner") == "docker"
        for shard in shards
        if shard.get("execution_target") == "windows_local"
    )
    for shard in shards:
        if shard.get("execution_target") == "windows_local":
            assert shard["metadata"]["depends_on"] == [
                "ruff-check",
                "ruff-format-check",
                "public-core-export",
                "bandit",
                "gitleaks",
                "pip-audit",
            ]
    assert all(
        shard.get("payload", {}).get("certification_matrix")
        == profile["metadata"]["certification_matrix"]
        for shard in shards
    )


@pytest.mark.asyncio
async def test_ci_controller_rejects_certification_without_preflight_attestation() -> None:
    storage = _storage()
    profile = default_repo_profile("zetherion-ai")
    assert profile is not None
    storage.get_repo_profile.return_value = profile
    skill = CiControllerSkill(storage=storage)

    response = await skill.handle(
        SkillRequest(
            intent="ci_run_start",
            user_id="owner-1",
            context={
                "repo_id": "zetherion-ai",
                "mode": "certification",
                "principal_id": "codex-agent-1",
            },
        )
    )

    assert response.success is True
    assert response.data["preflight"]["accepted"] is False
    assert response.data["coaching"][0]["principal_id"] == "codex-agent-1"
    storage.create_run.assert_not_awaited()
    storage.record_agent_gap_event.assert_awaited()


@pytest.mark.asyncio
async def test_ci_controller_rejects_certification_when_gitleaks_is_missing() -> None:
    storage = _storage()
    profile = default_repo_profile("zetherion-ai")
    assert profile is not None
    storage.get_repo_profile.return_value = profile
    skill = CiControllerSkill(storage=storage)

    response = await skill.handle(
        SkillRequest(
            intent="ci_run_start",
            user_id="owner-1",
            context={
                "repo_id": "zetherion-ai",
                "mode": "certification",
                "principal_id": "codex-agent-1",
                "preflight_checks": {
                    "categories_completed": ["static", "security"],
                    "checks": [
                        {"id": "ruff-check", "status": "passed", "tool": "ruff"},
                        {"id": "ruff-format-check", "status": "passed", "tool": "ruff"},
                        {"id": "public-core-export", "status": "passed"},
                        {"id": "bandit", "status": "passed"},
                        {"id": "pip-audit", "status": "passed"},
                    ],
                },
            },
        )
    )

    assert response.success is True
    assert response.data["preflight"]["accepted"] is False
    assert {violation["rule_code"] for violation in response.data["preflight"]["violations"]} == {
        "missing_preflight_check"
    }
    assert "gitleaks" in response.data["preflight"]["violations"][0]["summary"]
    assert response.data["coaching"][0]["recommendations"][0]["agents_md_update"]
    storage.create_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_ci_controller_rejects_certification_when_ruff_version_is_wrong() -> None:
    storage = _storage()
    profile = default_repo_profile("zetherion-ai")
    assert profile is not None
    storage.get_repo_profile.return_value = profile
    skill = CiControllerSkill(storage=storage)

    response = await skill.handle(
        SkillRequest(
            intent="ci_run_start",
            user_id="owner-1",
            context={
                "repo_id": "zetherion-ai",
                "mode": "certification",
                "principal_id": "codex-agent-1",
                "preflight_checks": {
                    "categories_completed": ["static", "security"],
                    "checks": [
                        {"id": "ruff-check", "status": "passed", "tool": "ruff"},
                        {"id": "ruff-format-check", "status": "passed", "tool": "ruff"},
                        {"id": "public-core-export", "status": "passed"},
                        {"id": "bandit", "status": "passed"},
                        {"id": "gitleaks", "status": "passed"},
                        {"id": "pip-audit", "status": "passed"},
                    ],
                    "tool_versions": {"ruff": "0.9.9"},
                },
            },
        )
    )

    assert response.success is True
    assert response.data["preflight"]["accepted"] is False
    assert {violation["rule_code"] for violation in response.data["preflight"]["violations"]} == {
        "tool_version_mismatch"
    }
    assert "0.8.4" in response.data["preflight"]["violations"][0]["summary"]
    assert response.data["coaching"][0]["recommendations"][0]["agents_md_update"]
    storage.create_run.assert_not_awaited()


@pytest.mark.asyncio
async def test_ci_controller_promotion_blocks_when_shards_are_awaiting_sync() -> None:
    storage = _storage()
    profile = default_repo_profile("zetherion-ai")
    assert profile is not None
    storage.get_repo_profile.return_value = profile
    storage.get_run.return_value = {
        "run_id": "run-1",
        "repo_id": "zetherion-ai",
        "review_receipts": {"merge_blocked": False},
        "shards": [{"lane_id": "windows", "status": "awaiting_sync"}],
    }
    storage.set_run_status.return_value = {"run_id": "run-1", "status": "promotion_blocked"}
    skill = CiControllerSkill(storage=storage)

    response = await skill.handle(
        SkillRequest(
            intent="ci_run_promote",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )

    assert response.success is True
    assert response.data["promoted"] is False
    storage.set_run_status.assert_awaited_once_with("owner-1", "run-1", "promotion_blocked")


@pytest.mark.asyncio
async def test_ci_observer_loads_run_report_graph_and_coaching() -> None:
    storage = _storage()
    storage.get_run_report.return_value = {"run_id": "run-1"}
    storage.get_run_graph.return_value = {"run_id": "run-1", "nodes": []}
    storage.get_run_correlation_context.return_value = {"run_id": "run-1", "trace_ids": ["t-1"]}
    storage.get_run_diagnostics.return_value = {
        "diagnostic_findings": [{"code": "coverage_gate_failed"}]
    }
    storage.get_run_artifacts.return_value = [{"artifact_id": "a-1"}]
    storage.get_run_evidence.return_value = [{"evidence_ref_id": "e-1"}]
    storage.list_agent_coaching_feedback.return_value = [{"feedback_id": "coach-1"}]
    skill = CiObserverSkill(storage=storage)

    report = await skill.handle(
        SkillRequest(intent="ci_run_report", user_id="owner-1", context={"run_id": "run-1"})
    )
    graph = await skill.handle(
        SkillRequest(intent="ci_run_graph", user_id="owner-1", context={"run_id": "run-1"})
    )
    correlation = await skill.handle(
        SkillRequest(
            intent="ci_run_correlation_context",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )
    diagnostics = await skill.handle(
        SkillRequest(
            intent="ci_run_diagnostics",
            user_id="owner-1",
            context={"run_id": "run-1", "node_id": "shard:run-1:shard-1"},
        )
    )
    artifacts = await skill.handle(
        SkillRequest(
            intent="ci_run_artifacts",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )
    evidence = await skill.handle(
        SkillRequest(
            intent="ci_run_evidence",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )
    coaching = await skill.handle(
        SkillRequest(
            intent="ci_run_coaching",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )

    assert report.data["report"]["run_id"] == "run-1"
    assert graph.data["run_graph"]["run_id"] == "run-1"
    assert correlation.data["correlation_context"]["trace_ids"] == ["t-1"]
    assert diagnostics.data["diagnostics"]["diagnostic_findings"][0]["code"] == (
        "coverage_gate_failed"
    )
    assert artifacts.data["artifacts"][0]["artifact_id"] == "a-1"
    assert evidence.data["evidence"][0]["evidence_ref_id"] == "e-1"
    assert coaching.data["coaching"][0]["feedback_id"] == "coach-1"
    storage.get_run_diagnostics.assert_awaited_once_with(
        "owner-1",
        "run-1",
        node_id="shard:run-1:shard-1",
    )


@pytest.mark.asyncio
async def test_ci_observer_attaches_synthesized_guidance_when_available() -> None:
    storage = _storage()
    storage.list_agent_coaching_feedback.return_value = [{"feedback_id": "coach-1"}]
    synthesizer = MagicMock()
    synthesizer.synthesize_many = AsyncMock(
        return_value=[
            {
                "feedback_id": "coach-1",
                "synthesized_guidance": {
                    "status": "synthesized",
                    "summary": "Tighten preflight checks first.",
                },
            }
        ]
    )
    skill = CiObserverSkill(storage=storage, coaching_synthesizer=synthesizer)

    coaching = await skill.handle(
        SkillRequest(
            intent="ci_run_coaching",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )

    assert coaching.success is True
    assert coaching.data["coaching"][0]["synthesized_guidance"]["status"] == "synthesized"
    synthesizer.synthesize_many.assert_awaited_once()


@pytest.mark.asyncio
async def test_ci_observer_loads_storage_and_vercel_reporting() -> None:
    storage = _storage()
    storage.list_repo_profiles.return_value = [
        {"repo_id": "zetherion-ai", "display_name": "Zetherion AI"}
    ]
    storage.list_runs.return_value = [
        {"run_id": "run-1", "repo_id": "zetherion-ai", "metadata": {}, "plan": {}}
    ]
    storage.list_worker_nodes.return_value = [
        {
            "node_id": "windows-main",
            "node_name": "Windows Main",
            "status": "active",
            "health_status": "healthy",
            "metadata": {
                "workspace_root": "C:/ZetherionCI/workspaces",
                "runtime_root": "C:/ZetherionCI/runtime",
            },
        }
    ]
    storage.get_worker_resource_report.return_value = {
        "samples": [
            {
                "sample": {
                    "disk_used_bytes": 1200,
                    "disk_free_bytes": 10_000_000_000,
                }
            }
        ],
        "totals": {"peak_disk_used_bytes": 1200},
    }
    storage.get_project_resource_report.return_value = {
        "repo_id": "zetherion-ai",
        "items": [{"cleanup_status": "cleanup_degraded"}],
        "totals": {
            "run_count": 1,
            "compute_minutes": 3.2,
            "peak_memory_mb": 256,
            "peak_disk_used_bytes": 1200,
            "peak_container_count": 2,
        },
    }
    storage.get_run_debug_bundle.return_value = {
        "bundle": {
            "cleanup_receipt": {
                "status": "cleanup_degraded",
                "path": "C:/cleanup/receipt.json",
                "deleted_paths": ["C:/ZetherionCI/workspaces/run-1/.artifacts/old.json"],
                "pruned_logs": ["C:/ZetherionCI/logs/old.log"],
                "docker_actions": [{"resource_kind": "image"}],
                "warnings": ["disk_headroom_below_low_watermark_after_cleanup"],
                "low_disk_free_bytes": 21_474_836_480,
                "target_free_bytes": 42_949_672_960,
            }
        }
    }
    storage.list_managed_operations.return_value = [
        {
            "operation_id": "op-1",
            "app_id": "cgs",
            "repo_id": "catalyst-group-solutions",
            "status": "failed",
        }
    ]
    storage.get_operation_hydrated.return_value = {
        "operation_id": "op-1",
        "app_id": "cgs",
        "repo_id": "catalyst-group-solutions",
        "status": "failed",
        "summary": {"route_path": "/admin/ai"},
        "metadata": {},
        "refs": [
            {"ref_kind": "vercel_deployment_id", "ref_value": "dep_123"},
            {"ref_kind": "branch", "ref_value": "main"},
        ],
        "incidents": [
            {
                "incident_type": "runtime_dependency_missing",
                "blocking": True,
            }
        ],
    }
    skill = CiObserverSkill(storage=storage)

    storage_response = await skill.handle(
        SkillRequest(intent="ci_reporting_storage", user_id="owner-1")
    )
    vercel_response = await skill.handle(
        SkillRequest(intent="ci_reporting_vercel", user_id="owner-1")
    )

    assert storage_response.success is True
    assert storage_response.data["report"]["status"] == "blocked"
    assert storage_response.data["report"]["alerts"]
    assert storage_response.data["report"]["announcement_events"][0]["category"] == (
        "ops.storage_pressure"
    )
    assert storage_response.data["report"]["top_consumers"][0]["repo_id"] == "zetherion-ai"
    assert storage_response.data["report"]["workers"][0]["node_id"] == "windows-main"
    assert vercel_response.success is True
    assert vercel_response.data["report"]["summary"]["failed_operations"] == 1
    assert vercel_response.data["report"]["announcement_events"][0]["category"] == (
        "ops.vercel_reporting"
    )
    assert vercel_response.data["report"]["routes"][0]["route_path"] == "/admin/ai"


@pytest.mark.asyncio
async def test_ci_controller_promotion_stores_merge_and_deploy_readiness_receipts() -> None:
    storage = _storage()
    profile = default_repo_profile("zetherion-ai")
    assert profile is not None
    storage.get_repo_profile.return_value = profile
    storage.get_run.return_value = {
        "run_id": "run-1",
        "repo_id": "zetherion-ai",
        "git_ref": "0123456789abcdef0123456789abcdef01234567",
        "review_receipts": {"merge_blocked": False, "verdict": "approved"},
        "metadata": {
            "git_sha": "0123456789abcdef0123456789abcdef01234567",
            "release_verification": {
                "status": "healthy",
                "summary": "Required runtime checks are green.",
                "blocker_count": 0,
            },
        },
        "shards": [{"lane_id": "windows", "status": "succeeded"}],
        "github_receipts": {},
    }
    storage.store_run_github_receipt.side_effect = [
        {"run_id": "run-1", "github_receipts": {}},
        {"run_id": "run-1", "github_receipts": {}},
    ]
    storage.set_run_status.return_value = {"run_id": "run-1", "status": "ready_to_merge"}
    skill = CiControllerSkill(storage=storage)

    response = await skill.handle(
        SkillRequest(
            intent="ci_run_promote",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )

    assert response.success is True
    assert response.data["promoted"] is True
    first_receipt = storage.store_run_github_receipt.await_args_list[0].args[2]
    assert first_receipt["merge_readiness"]["state"] == "success"
    assert first_receipt["deploy_readiness"]["state"] == "success"
    assert first_receipt["repo_readiness"]["merge_ready"] is True


@pytest.mark.asyncio
async def test_ci_controller_promotion_blocks_when_local_required_paths_failed() -> None:
    storage = _storage()
    profile = default_repo_profile("zetherion-ai")
    assert profile is not None
    storage.get_repo_profile.return_value = profile
    storage.get_run.return_value = {
        "run_id": "run-2",
        "repo_id": "zetherion-ai",
        "git_ref": "0123456789abcdef0123456789abcdef01234567",
        "review_receipts": {"merge_blocked": False, "verdict": "approved"},
        "metadata": {
            "git_sha": "0123456789abcdef0123456789abcdef01234567",
            "release_verification": {
                "status": "healthy",
                "blocker_count": 0,
            },
        },
        "shards": [
            {
                "shard_id": "shard-1",
                "lane_id": "z-int-faults",
                "status": "failed",
                "metadata": {"covered_required_paths": ["queue_reliability"]},
                "result": {},
                "error": {},
                "artifact_contract": {"expects": ["stdout"]},
            }
        ],
        "github_receipts": {},
    }
    storage.store_run_github_receipt.return_value = {"run_id": "run-2", "github_receipts": {}}
    storage.set_run_status.return_value = {"run_id": "run-2", "status": "promotion_blocked"}
    skill = CiControllerSkill(storage=storage)

    response = await skill.handle(
        SkillRequest(
            intent="ci_run_promote",
            user_id="owner-1",
            context={"run_id": "run-2"},
        )
    )

    assert response.success is True
    assert response.data["promoted"] is False
    assert response.data["merge_readiness"]["state"] == "failure"
    storage.set_run_status.assert_awaited_with("owner-1", "run-2", "promotion_blocked")


@pytest.mark.asyncio
async def test_ci_controller_store_release_receipt_patches_run_metadata() -> None:
    storage = _storage()
    storage.merge_run_metadata.return_value = {"run_id": "run-1"}
    skill = CiControllerSkill(storage=storage)

    response = await skill.handle(
        SkillRequest(
            intent="ci_run_store_release_receipt",
            user_id="owner-1",
            context={
                "run_id": "run-1",
                "receipt": {
                    "status": "deployed_but_unhealthy",
                    "summary": "Discord DM roundtrip failed.",
                    "blocker_count": 1,
                },
            },
        )
    )

    assert response.success is True
    storage.merge_run_metadata.assert_awaited_once()
    patch = storage.merge_run_metadata.await_args.args[2]
    assert patch["release_verification"]["status"] == "deployed_but_unhealthy"
    assert patch["release_verification"]["blocker_count"] == 1


@pytest.mark.asyncio
async def test_ci_controller_repo_plan_schedule_and_run_read_handlers() -> None:
    storage = _storage()
    profile = default_repo_profile("zetherion-ai")
    assert profile is not None
    storage.upsert_repo_profile.return_value = profile
    storage.list_repo_profiles.return_value = [profile]
    storage.get_repo_profile.return_value = profile
    storage.create_plan_snapshot.return_value = {
        "plan_id": "plan-1",
        "version": 2,
        "repo_id": "zetherion-ai",
    }
    storage.get_plan_snapshot.return_value = {
        "plan_id": "plan-1",
        "version": 2,
        "repo_id": "zetherion-ai",
    }
    storage.list_plan_versions.return_value = [{"version": 2}, {"version": 1}]
    storage.upsert_schedule.return_value = {"schedule_id": "sched-1", "repo_id": "zetherion-ai"}
    storage.list_schedules.return_value = [{"schedule_id": "sched-1"}]
    storage.get_run.return_value = {
        "run_id": "run-1",
        "repo_id": "zetherion-ai",
        "plan": {
            "host_capacity_policy": {
                "host_id": "windows-main",
                "resource_budget": {"cpu": 1, "service": 2, "serial": 1},
                "reserve_runtime_headroom": True,
            }
        },
        "shards": [
            {
                "shard_id": "shard-1",
                "lane_id": "z-unit-core",
                "status": "queued_local",
                "metadata": {"resource_class": "cpu", "parallel_group": "unit"},
            },
            {
                "shard_id": "shard-2",
                "lane_id": "z-int-runtime",
                "status": "running",
                "metadata": {"resource_class": "service", "parallel_group": "db"},
            },
            {
                "shard_id": "shard-3",
                "lane_id": "discord-required-e2e",
                "status": "planned",
                "metadata": {"resource_class": "service", "parallel_group": "db"},
            },
        ],
    }
    storage.list_runs.return_value = [{"run_id": "run-1"}]
    skill = CiControllerSkill(storage=storage)

    repo_upsert = await skill.handle(
        SkillRequest(
            intent="ci_repo_upsert",
            user_id="owner-1",
            context={
                "repo_id": "zetherion-ai",
                "github_repo": "jimtin/zetherion-ai",
                "stack_kind": "python",
            },
        )
    )
    repo_list = await skill.handle(SkillRequest(intent="ci_repo_list", user_id="owner-1"))
    repo_get = await skill.handle(
        SkillRequest(
            intent="ci_repo_get",
            user_id="owner-1",
            context={"repo_id": "zetherion-ai"},
        )
    )
    plan_save = await skill.handle(
        SkillRequest(
            intent="ci_plan_save",
            user_id="owner-1",
            context={
                "repo_id": "zetherion-ai",
                "title": "Repair plan",
                "content_markdown": "# Repair",
            },
        )
    )
    plan_get = await skill.handle(
        SkillRequest(
            intent="ci_plan_get",
            user_id="owner-1",
            context={"plan_id": "plan-1"},
        )
    )
    plan_versions = await skill.handle(
        SkillRequest(
            intent="ci_plan_versions",
            user_id="owner-1",
            context={"plan_id": "plan-1"},
        )
    )
    schedule = await skill.handle(
        SkillRequest(
            intent="ci_schedule_upsert",
            user_id="owner-1",
            context={"repo_id": "zetherion-ai", "name": "Daily"},
        )
    )
    schedules = await skill.handle(
        SkillRequest(
            intent="ci_schedule_list",
            user_id="owner-1",
            context={"repo_id": "zetherion-ai"},
        )
    )
    run_get = await skill.handle(
        SkillRequest(
            intent="ci_run_get",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )
    run_list = await skill.handle(
        SkillRequest(
            intent="ci_run_list",
            user_id="owner-1",
            context={"repo_id": "zetherion-ai"},
        )
    )
    rebalance = await skill.handle(
        SkillRequest(
            intent="ci_run_rebalance",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )

    assert repo_upsert.success is True
    assert repo_list.data["repos"][0]["repo_id"] == "zetherion-ai"
    assert repo_get.data["repo"]["repo_id"] == "zetherion-ai"
    assert plan_save.data["plan"]["plan_id"] == "plan-1"
    assert plan_get.data["plan"]["version"] == 2
    assert len(plan_versions.data["versions"]) == 2
    assert schedule.data["schedule"]["schedule_id"] == "sched-1"
    assert schedules.data["schedules"] == [{"schedule_id": "sched-1"}]
    assert run_get.data["run"]["run_id"] == "run-1"
    assert run_list.data["runs"] == [{"run_id": "run-1"}]
    assert rebalance.data["rebalance"]["busy_parallel_groups"] == ["db"]
    assert rebalance.data["rebalance"]["capacity_snapshot"]["host_id"] == "windows-main"
    assert rebalance.data["rebalance"]["capacity_snapshot"]["service_slots_used"] == 1
    assert rebalance.data["rebalance"]["admitted_shard_ids"] == ["shard-1"]
    assert rebalance.data["rebalance"]["blocked_shard_ids"] == ["shard-3"]
    assert rebalance.data["rebalance"]["admission_decisions"][1]["blocking_reasons"] == [
        "parallel group `db` already active"
    ]


@pytest.mark.asyncio
async def test_ci_controller_store_github_receipt_and_publish_status_handlers() -> None:
    storage = _storage()
    profile = default_repo_profile("zetherion-ai")
    assert profile is not None
    storage.get_repo_profile.return_value = profile
    storage.store_run_github_receipt.return_value = {
        "run_id": "run-1",
        "github_receipts": {"published_statuses": {"published": True}},
    }
    storage.get_run.return_value = {
        "run_id": "run-1",
        "repo_id": "zetherion-ai",
        "metadata": {"git_sha": "0" * 40},
        "github_receipts": {
            "merge_readiness": {
                "context": "zetherion/merge-readiness",
                "state": "success",
                "description": "Merge ready.",
            }
        },
    }
    skill = CiControllerSkill(storage=storage)
    skill._publish_github_statuses = AsyncMock(  # type: ignore[method-assign]
        return_value={"published": True, "contexts": ["zetherion/merge-readiness"]}
    )

    stored = await skill.handle(
        SkillRequest(
            intent="ci_run_store_github_receipt",
            user_id="owner-1",
            context={"run_id": "run-1", "receipt": {"merge_readiness": {"state": "success"}}},
        )
    )
    published = await skill.handle(
        SkillRequest(
            intent="ci_run_publish_statuses",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )

    assert stored.success is True
    assert published.success is True
    assert published.data["published_statuses"]["published"] is True
    storage.store_run_github_receipt.assert_awaited()


@pytest.mark.asyncio
async def test_ci_controller_supports_retry_and_cancel_run_handlers() -> None:
    storage = _storage()
    profile = default_repo_profile("zetherion-ai")
    assert profile is not None
    storage.get_repo_profile.return_value = profile
    storage.get_run.return_value = {
        "run_id": "run-1",
        "repo_id": "zetherion-ai",
        "git_ref": "feature/test",
        "metadata": {
            "mode": "certification",
            "git_sha": "a" * 40,
            "preflight_checks": {
                "categories_completed": ["static", "security"],
                "checks": [
                    {"id": "ruff-check", "status": "passed", "tool": "ruff"},
                    {"id": "ruff-format-check", "status": "passed", "tool": "ruff"},
                    {"id": "public-core-export", "status": "passed"},
                    {"id": "bandit", "status": "passed"},
                    {"id": "gitleaks", "status": "passed"},
                    {"id": "pip-audit", "status": "passed"},
                ],
            },
        },
        "plan": {"mode": "certification"},
        "shards": [],
    }
    storage.create_run.return_value = {"run_id": "run-2", "repo_id": "zetherion-ai"}
    storage.set_run_status.return_value = {"run_id": "run-1", "status": "cancelled"}
    skill = CiControllerSkill(storage=storage)

    retry = await skill.handle(
        SkillRequest(intent="ci_run_retry", user_id="owner-1", context={"run_id": "run-1"})
    )
    cancel = await skill.handle(
        SkillRequest(
            intent="ci_run_cancel",
            user_id="owner-1",
            context={"run_id": "run-1", "reason": "superseded by newer commit"},
        )
    )

    assert retry.success is True
    assert retry.data["run"]["run_id"] == "run-2"
    assert retry.data["retried_run_id"] == "run-1"
    create_kwargs = storage.create_run.await_args.kwargs
    assert create_kwargs["trigger"] == "retry"
    assert create_kwargs["metadata"]["git_sha"] == "a" * 40
    assert create_kwargs["metadata"]["retry_of_run_id"] == "run-1"

    assert cancel.success is True
    assert cancel.data["cancelled"] is True
    merge_args = storage.merge_run_metadata.await_args_list[-1].args
    assert merge_args[0] == "owner-1"
    assert merge_args[1] == "run-1"
    assert merge_args[2]["cancel_requested_by"] == "owner-1"
    assert merge_args[2]["cancel_reason"] == "superseded by newer commit"
    assert merge_args[2]["cancel_mode"] == "best_effort_control_plane"
    storage.set_run_status.assert_awaited_with("owner-1", "run-1", "cancelled")


@pytest.mark.asyncio
async def test_pr_reviewer_blocks_certification_repo_without_windows_success() -> None:
    storage = _storage()
    storage.get_run.return_value = {
        "run_id": "run-1",
        "repo_id": "zetherion-ai",
        "metadata": {
            "certification_required": True,
            "platform_canary": True,
        },
        "shards": [
            {
                "lane_id": "unit-full",
                "execution_target": "windows_local",
                "status": "failed",
            }
        ],
    }
    storage.store_run_review.return_value = {"run_id": "run-1"}
    skill = PrReviewerSkill(storage=storage)

    response = await skill.handle(
        SkillRequest(intent="ci_run_review", user_id="owner-1", context={"run_id": "run-1"})
    )

    assert response.success is True
    review = response.data["review"]
    assert review["verdict"] == "blocked"
    assert review["merge_blocked"] is True
    assert {finding["code"] for finding in review["findings"]} >= {
        "shard_failed",
        "certification_incomplete",
    }


def test_pr_reviewer_metadata_and_initialize() -> None:
    skill = PrReviewerSkill(storage=MagicMock())

    assert skill.metadata.name == "pr_reviewer"
    assert "ci_run_review" in skill.metadata.intents
    assert skill.metadata.version == "0.2.0"


@pytest.mark.asyncio
async def test_pr_reviewer_initialize_and_run_id_validation() -> None:
    skill = PrReviewerSkill(storage=MagicMock())

    assert await skill.initialize() is True

    response = await skill.handle(
        SkillRequest(intent="ci_run_review", user_id="owner-1", context={})
    )

    assert response.success is False
    assert "run_id is required" in str(response.error)


@pytest.mark.asyncio
async def test_pr_reviewer_supports_approved_and_needs_sync_verdicts() -> None:
    storage = _storage()
    skill = PrReviewerSkill(storage=storage)

    approved_run = {
        "run_id": "run-approved",
        "repo_id": "zetherion-ai",
        "metadata": {
            "required_static_gates": ["ruff-check"],
            "certification_required": False,
            "platform_canary": True,
        },
        "shards": [{"lane_id": "ruff-check", "status": "succeeded"}],
    }
    needs_sync_run = {
        "run_id": "run-sync",
        "repo_id": "catalyst-group-solutions",
        "metadata": {
            "required_static_gates": ["lint"],
            "required_gate_categories": ["static", "e2e"],
            "certification_required": True,
            "certification_requirements": ["discord_roundtrip"],
            "platform_canary": False,
        },
        "shards": [
            {
                "lane_id": "lint",
                "status": "succeeded",
                "execution_target": "windows_local",
                "metadata": {"gate_family": "static"},
                "result": {},
            },
            {
                "lane_id": "discord-required-e2e",
                "status": "succeeded",
                "execution_target": "windows_local",
                "metadata": {"gate_family": "e2e"},
                "result": {},
            },
        ],
    }

    approved = skill._review_run(approved_run)  # noqa: SLF001
    needs_sync = skill._review_run(needs_sync_run)  # noqa: SLF001

    assert approved["verdict"] == "approved"
    assert approved["merge_blocked"] is False
    assert needs_sync["verdict"] == "needs_sync"
    assert needs_sync["merge_blocked"] is True
    assert {finding["code"] for finding in needs_sync["findings"]} >= {
        "observability_logs_missing",
        "resource_samples_missing",
        "platform_canary_missing",
    }


def test_pr_reviewer_flags_missing_static_gate_pending_and_missing_discord_receipt() -> None:
    skill = PrReviewerSkill(storage=MagicMock())

    review = skill._review_run(  # noqa: SLF001
        {
            "run_id": "run-2",
            "repo_id": "zetherion-ai",
            "metadata": {
                "required_static_gates": ["ruff-check"],
                "required_security_gates": ["gitleaks"],
                "required_gate_categories": ["static", "security", "unit", "integration", "e2e"],
                "certification_required": True,
                "certification_requirements": ["discord_roundtrip"],
                "platform_canary": True,
            },
            "shards": [
                {
                    "lane_id": "unit-full",
                    "status": "running",
                    "execution_target": "windows_local",
                    "result": {"log_chunks": ["ok"], "resource_samples": [1]},
                }
            ],
        }
    )

    assert review["verdict"] == "blocked"
    assert {finding["code"] for finding in review["findings"]} >= {
        "mandatory_static_gates_missing",
        "mandatory_security_gates_missing",
        "required_gate_categories_missing",
        "shard_pending",
        "certification_incomplete",
        "discord_roundtrip_missing",
    }


@pytest.mark.asyncio
async def test_pr_reviewer_handle_reports_missing_run_and_unknown_intent() -> None:
    storage = _storage()
    storage.get_run.return_value = None
    skill = PrReviewerSkill(storage=storage)

    missing = await skill.handle(
        SkillRequest(intent="ci_run_review", user_id="owner-1", context={"run_id": "missing"})
    )
    unknown = await skill.handle(SkillRequest(intent="ci_unknown", user_id="owner-1"))

    assert missing.success is False
    assert "not found" in str(missing.error)
    assert unknown.success is False
    assert "Unknown reviewer intent" in str(unknown.error)


@pytest.mark.asyncio
async def test_ci_controller_compile_plan_includes_static_gates_and_resource_budget() -> None:
    storage = _storage()
    profile = default_repo_profile("catalyst-group-solutions")
    assert profile is not None
    storage.get_repo_profile.return_value = profile
    storage.create_compiled_plan.return_value = {
        "compiled_plan_id": "compiled-123",
        "repo_id": "catalyst-group-solutions",
        "plan": {"shards": []},
    }
    skill = CiControllerSkill(storage=storage)

    response = await skill.handle(
        SkillRequest(
            intent="ci_plan_compile",
            user_id="owner-1",
            context={"repo_id": "catalyst-group-solutions", "mode": "certification"},
        )
    )

    assert response.success is True
    create_kwargs = storage.create_compiled_plan.await_args.kwargs
    assert create_kwargs["mode"] == "certification"
    plan = create_kwargs["plan"]
    assert plan["windows_execution_mode"] == "docker_only"
    assert "mandatory_static_gates" in plan["certification_requirements"]
    assert plan["required_security_gate_ids"] == ["gitleaks", "yarn-audit"]
    assert plan["required_gate_categories"] == [
        "static",
        "security",
        "unit",
        "integration",
        "e2e",
    ]
    assert any(shard["lane_id"] == "golive-gate" for shard in plan["shards"])
    assert any(shard["execution_target"] == "windows_local" for shard in plan["shards"])
    assert any(shard["execution_target"] == "local_mac" for shard in plan["shards"])
    assert any(shard["lane_id"] == "lint" for shard in plan["shards"])
    assert any(shard["lane_id"] == "c-unit-core" for shard in plan["shards"])
    golive = next(shard for shard in plan["shards"] if shard["lane_id"] == "golive-gate")
    assert golive["metadata"]["depends_on"] == [
        "lint",
        "format-check",
        "typecheck",
        "gitleaks",
        "yarn-audit",
    ]


@pytest.mark.asyncio
async def test_ci_observer_returns_workspace_readiness() -> None:
    storage = _storage()
    storage.get_reporting_readiness.return_value = {
        "workspace_readiness": {
            "merge_ready": True,
            "deploy_ready": False,
            "failed_required_paths": ["cgs_release_verification"],
        },
        "worker_certification": {
            "status": "pending",
            "execution_backend": "wsl_docker",
            "wsl_distribution": "Ubuntu",
        },
        "repo_readiness": [
            {
                "repo_id": "catalyst-group-solutions",
                "readiness": {"merge_ready": True, "deploy_ready": False},
            }
        ],
    }
    skill = CiObserverSkill(storage=storage)

    response = await skill.handle(SkillRequest(intent="ci_reporting_readiness", user_id="owner-1"))

    assert response.success is True
    assert response.data["readiness"]["workspace_readiness"]["merge_ready"] is True
    assert response.data["readiness"]["worker_certification"]["status"] == "pending"
    assert response.data["readiness"]["worker_certification"]["execution_backend"] == "wsl_docker"
    storage.get_reporting_readiness.assert_awaited_once_with("owner-1")


@pytest.mark.asyncio
async def test_ci_observer_supports_events_logs_and_debug_bundle_intents() -> None:
    storage = _storage()
    storage.get_run_events = AsyncMock(return_value=[{"event_id": "evt-1"}])
    storage.get_run_log_chunks = AsyncMock(return_value=[{"chunk_id": "log-1"}])
    storage.get_run_debug_bundle = AsyncMock(return_value={"bundle_id": "bundle-1"})
    skill = CiObserverSkill(storage=storage)

    events = await skill.handle(
        SkillRequest(
            intent="ci_run_events",
            user_id="owner-1",
            context={"run_id": "run-1", "shard_id": "shard-1", "limit": 25},
        )
    )
    logs = await skill.handle(
        SkillRequest(
            intent="ci_run_logs",
            user_id="owner-1",
            context={"run_id": "run-1", "query": "error", "limit": 50},
        )
    )
    bundle = await skill.handle(
        SkillRequest(
            intent="ci_run_debug_bundle",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )

    assert events.success is True
    assert events.data["events"] == [{"event_id": "evt-1"}]
    assert logs.success is True
    assert logs.data["logs"] == [{"chunk_id": "log-1"}]
    assert bundle.success is True
    assert bundle.data["debug_bundle"] == {"bundle_id": "bundle-1"}


@pytest.mark.asyncio
async def test_ci_observer_supports_project_and_worker_reports() -> None:
    storage = _storage()
    storage.get_project_resource_report = AsyncMock(return_value={"cpu": 12})
    storage.get_project_failure_report = AsyncMock(return_value={"failures": 1})
    storage.get_worker_resource_report = AsyncMock(return_value={"memory_gb": 48})
    skill = CiObserverSkill(storage=storage)

    project_resources = await skill.handle(
        SkillRequest(
            intent="ci_reporting_project_resources",
            context={"operator_id": "owner-2", "repo_id": "zetherion-ai", "limit": 10},
        )
    )
    project_failures = await skill.handle(
        SkillRequest(
            intent="ci_reporting_project_failures",
            context={"operator_id": "owner-2", "repo_id": "zetherion-ai", "limit": 10},
        )
    )
    worker_resources = await skill.handle(
        SkillRequest(
            intent="ci_reporting_worker_resources",
            context={"actor_sub": "owner-3", "node_id": "windows-main", "limit": 10},
        )
    )

    assert project_resources.success is True
    assert project_resources.data["report"] == {"cpu": 12}
    assert project_failures.success is True
    assert project_failures.data["report"] == {"failures": 1}
    assert worker_resources.success is True
    assert worker_resources.data["report"] == {"memory_gb": 48}
    storage.get_project_resource_report.assert_awaited_once_with(
        "owner-2",
        "zetherion-ai",
        limit=10,
    )
    storage.get_project_failure_report.assert_awaited_once_with(
        "owner-2",
        "zetherion-ai",
        limit=10,
    )
    storage.get_worker_resource_report.assert_awaited_once_with(
        "owner-3",
        "windows-main",
        limit=10,
    )


@pytest.mark.asyncio
async def test_ci_observer_builds_owner_capacity_report() -> None:
    storage = _storage()
    storage.list_repo_profiles.return_value = [
        {"repo_id": "zetherion-ai"},
        {"repo_id": "catalyst-group-solutions"},
    ]
    storage.list_runs.return_value = [
        {
            "run_id": "run-1",
            "repo_id": "zetherion-ai",
            "plan": {
                "host_capacity_policy": {
                    "host_id": "windows-main",
                    "resource_budget": {"cpu": 8, "service": 2, "serial": 1},
                    "reserve_runtime_headroom": True,
                    "admission_mode": "dynamic_resource_based",
                }
            },
            "shards": [
                {
                    "status": "running",
                    "metadata": {"resource_class": "service", "parallel_group": "db"},
                },
                {
                    "status": "awaiting_sync",
                    "metadata": {"resource_class": "cpu", "parallel_group": "unit"},
                },
            ],
        }
    ]
    storage.list_worker_nodes.side_effect = [
        [
            {
                "node_id": "windows-main",
                "node_name": "Main Windows Worker",
                "status": "active",
                "health_status": "healthy",
                "capabilities": ["docker", "playwright"],
                "metadata": {"os": "windows"},
            }
        ],
        [
            {
                "node_id": "windows-main",
                "node_name": "Main Windows Worker",
                "status": "active",
                "health_status": "healthy",
                "capabilities": ["docker", "playwright", "wsl_docker"],
                "metadata": {"os": "windows"},
            }
        ],
    ]
    storage.get_worker_resource_report.return_value = {
        "samples": [{"sample": {"memory_mb": 2048, "disk_used_bytes": 4096}}],
        "totals": {"sample_count": 1, "peak_memory_mb": 2048.0},
    }
    skill = CiObserverSkill(storage=storage)

    response = await skill.handle(SkillRequest(intent="ci_reporting_capacity", user_id="owner-1"))

    assert response.success is True
    capacity = response.data["capacity"]
    assert capacity["totals"]["host_count"] == 1
    assert capacity["totals"]["worker_count"] == 1
    assert capacity["hosts"][0]["host_id"] == "windows-main"
    assert capacity["hosts"][0]["resource_usage"] == {"cpu": 1, "service": 1, "serial": 0}
    assert capacity["hosts"][0]["busy_parallel_groups"] == ["db", "unit"]
    assert capacity["workers"][0]["repos"] == [
        "catalyst-group-solutions",
        "zetherion-ai",
    ]
    assert capacity["workers"][0]["latest_sample"] == {
        "memory_mb": 2048,
        "disk_used_bytes": 4096,
    }


@pytest.mark.asyncio
async def test_ci_observer_builds_scheduler_overview() -> None:
    storage = _storage()
    storage.list_runs.return_value = [
        {
            "run_id": "run-active",
            "repo_id": "zetherion-ai",
            "plan": {
                "host_capacity_policy": {
                    "host_id": "windows-main",
                    "resource_budget": {"cpu": 2, "service": 1, "serial": 1},
                    "reserve_runtime_headroom": True,
                    "admission_mode": "dynamic_resource_based",
                    "windows_execution_mode": "docker_only",
                }
            },
            "shards": [
                {
                    "shard_id": "active-db",
                    "lane_id": "integration-db",
                    "status": "running",
                    "metadata": {"resource_class": "service", "parallel_group": "db"},
                },
                {
                    "shard_id": "queued-unit",
                    "lane_id": "unit",
                    "status": "queued_local",
                    "metadata": {"resource_class": "cpu", "parallel_group": "unit"},
                },
            ],
        },
        {
            "run_id": "run-second",
            "repo_id": "catalyst-group-solutions",
            "plan": {
                "host_capacity_policy": {
                    "host_id": "windows-main",
                    "resource_budget": {"cpu": 2, "service": 1, "serial": 1},
                    "reserve_runtime_headroom": True,
                    "admission_mode": "dynamic_resource_based",
                }
            },
            "shards": [
                {
                    "shard_id": "queued-public",
                    "lane_id": "public-e2e",
                    "status": "planned",
                    "metadata": {"resource_class": "service", "parallel_group": "db"},
                },
                {
                    "shard_id": "queued-lint",
                    "lane_id": "lint",
                    "status": "planned",
                    "metadata": {"resource_class": "cpu", "parallel_group": "lint"},
                },
            ],
        },
    ]
    skill = CiObserverSkill(storage=storage)

    response = await skill.handle(SkillRequest(intent="ci_reporting_scheduler", user_id="owner-1"))

    assert response.success is True
    scheduler = response.data["scheduler"]
    assert scheduler["totals"]["host_count"] == 1
    assert scheduler["totals"]["pending_shard_count"] == 3
    host = scheduler["hosts"][0]
    assert host["host_id"] == "windows-main"
    assert host["resource_available"] == {"cpu": 2, "service": 0, "serial": 1}
    assert host["pending_run_count"] == 2
    assert host["pending_shard_count"] == 3
    assert host["repo_ids"] == ["catalyst-group-solutions", "zetherion-ai"]
    assert [candidate["shard_id"] for candidate in host["admitted_candidates"]] == [
        "queued-unit",
        "queued-lint",
    ]
    assert [candidate["shard_id"] for candidate in host["blocked_candidates"]] == ["queued-public"]
    assert host["blocked_candidates"][0]["blocking_reasons"] == [
        "service budget exhausted (1/1)",
        "parallel group `db` already active",
    ]
    assert host["metadata"]["can_admit_more"] is True
    storage.list_runs.assert_awaited_once_with("owner-1", limit=200)


@pytest.mark.asyncio
async def test_ci_observer_requires_run_or_repo_identifiers_for_scoped_intents() -> None:
    storage = _storage()
    skill = CiObserverSkill(storage=storage)

    missing_run = await skill.handle(SkillRequest(intent="ci_run_events", user_id="owner-1"))
    missing_repo = await skill.handle(
        SkillRequest(intent="ci_reporting_project_resources", user_id="owner-1")
    )
    missing_node = await skill.handle(
        SkillRequest(intent="ci_reporting_worker_resources", user_id="owner-1")
    )
    unknown = await skill.handle(SkillRequest(intent="ci_unknown", user_id="owner-1"))

    assert missing_run.success is False
    assert missing_run.error == "run_id is required"
    assert missing_repo.success is False
    assert missing_repo.error == "repo_id is required"
    assert missing_node.success is False
    assert missing_node.error == "node_id is required"
    assert unknown.success is False
    assert unknown.error is not None
    assert "Unknown CI observer intent" in unknown.error
