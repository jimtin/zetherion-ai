from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.ci_observer import (
    CiObserverSkill,
    _active_capacity_by_host,
    _host_blocking_reasons,
    _normalize_owner_id,
    _resource_reservation_for_shard,
    _round_robin_candidates_by_repo,
    _scheduler_overview,
)


def _storage() -> MagicMock:
    storage = MagicMock()
    storage.get_run_events = AsyncMock(return_value=[])
    storage.get_run_log_chunks = AsyncMock(return_value=[])
    storage.get_run_debug_bundle = AsyncMock(return_value={})
    storage.get_run_report = AsyncMock(return_value={})
    storage.get_run_graph = AsyncMock(return_value={})
    storage.get_run_correlation_context = AsyncMock(return_value={})
    storage.get_run_diagnostics = AsyncMock(return_value={})
    storage.get_run_artifacts = AsyncMock(return_value=[])
    storage.get_run_evidence = AsyncMock(return_value=[])
    storage.list_agent_coaching_feedback = AsyncMock(return_value=[])
    storage.get_reporting_summary = AsyncMock(return_value={})
    storage.get_reporting_readiness = AsyncMock(return_value={})
    storage.list_repo_profiles = AsyncMock(return_value=[])
    storage.list_runs = AsyncMock(return_value=[])
    storage.list_worker_nodes = AsyncMock(return_value=[])
    storage.get_worker_resource_report = AsyncMock(return_value={"samples": []})
    storage.get_project_resource_report = AsyncMock(return_value={})
    storage.get_project_failure_report = AsyncMock(return_value={})
    return storage


def test_ci_observer_helper_paths_cover_owner_and_resource_defaults() -> None:
    assert _normalize_owner_id(
        SkillRequest(user_id="user-1", context={"owner_id": "owner-1"})
    ) == "owner-1"
    assert _normalize_owner_id(
        SkillRequest(user_id="user-1", context={"operator_id": "operator-1"})
    ) == "operator-1"
    assert _normalize_owner_id(SkillRequest(user_id="")) == "owner"

    explicit = _resource_reservation_for_shard(
        "zetherion-ai",
        {
            "shard_id": "shard-1",
            "metadata": {
                "resource_reservation": {
                    "resource_class": "service",
                    "units": 2,
                    "parallel_group": "db",
                }
            },
        },
    )
    assert explicit.repo_id == "zetherion-ai"
    assert explicit.shard_id == "shard-1"
    assert explicit.resource_class == "service"
    assert explicit.parallel_group == "db"

    fallback = _resource_reservation_for_shard(
        "zetherion-ai",
        {
            "shard_id": "shard-2",
            "lane_id": "lint",
            "execution_target": "local_mac",
            "metadata": {
                "resource_class": "cpu",
                "resource_units": 0,
                "workspace_root": "/tmp/workspace",
            },
        },
    )
    assert fallback.units == 1
    assert fallback.workspace_root == "/tmp/workspace"
    assert fallback.metadata["lane_id"] == "lint"

    assert _host_blocking_reasons(
        {"cpu": 2, "service": 1, "serial": 0},
        {"cpu": 2, "service": 1, "serial": 0},
    ) == [
        "cpu budget exhausted (2/2)",
        "service budget exhausted (1/1)",
    ]


def test_ci_observer_capacity_and_scheduler_overview_cover_blocked_candidates() -> None:
    runs = [
        {
            "run_id": "run-active",
            "repo_id": "repo-a",
            "plan": {
                "host_capacity_policy": {
                    "host_id": "windows-main",
                    "resource_budget": {"cpu": 1, "service": 1, "serial": 1},
                    "reserve_runtime_headroom": True,
                    "workspace_root": "C:/ZetherionCI",
                    "runtime_root": "C:/ZetherionAI",
                }
            },
            "shards": [
                {
                    "shard_id": "active-1",
                    "status": "running",
                    "metadata": {"resource_class": "cpu", "parallel_group": "lint"},
                },
                {
                    "shard_id": "ignored-1",
                    "status": "succeeded",
                    "metadata": {"resource_class": "service"},
                },
            ],
        },
        {
            "run_id": "run-pending",
            "repo_id": "repo-b",
            "metadata": {
                "host_capacity_policy": {
                    "host_id": "windows-main",
                    "resource_budget": {"cpu": 1, "service": 1, "serial": 1},
                }
            },
            "shards": [
                {
                    "shard_id": "pending-1",
                    "lane_id": "unit",
                    "status": "queued_local",
                    "metadata": {"resource_class": "cpu", "parallel_group": "lint"},
                },
                {
                    "shard_id": "pending-2",
                    "lane_id": "service",
                    "status": "planned",
                    "metadata": {"resource_class": "service", "parallel_group": "db"},
                },
            ],
        },
    ]

    capacity = _active_capacity_by_host(runs)
    assert capacity["windows-main"]["resource_usage"] == {"cpu": 1, "service": 0, "serial": 0}
    assert capacity["windows-main"]["active_runs"] == {"run-active"}
    assert capacity["windows-main"]["busy_parallel_groups"] == {"lint"}

    overview = _scheduler_overview(runs)
    host = overview.hosts[0]
    assert host.host_id == "windows-main"
    assert host.pending_shard_count == 2
    assert host.blocked_candidates[0].parallel_group == "lint"
    assert "parallel group `lint` already active" in host.blocked_candidates[0].blocking_reasons
    assert host.admitted_candidates[0].lane_id == "service"
    assert host.metadata["workspace_root"] == "C:/ZetherionCI"
    assert host.metadata["runtime_root"] == "C:/ZetherionAI"


def test_ci_observer_round_robin_and_scheduler_cover_all_blocked_candidates() -> None:
    assert _round_robin_candidates_by_repo(
        {
            "repo-a": [{"lane_id": "a-1"}, {"lane_id": "a-2"}],
            "repo-b": [{"lane_id": "b-1"}],
            "repo-c": [],
        }
    ) == [
        {"lane_id": "a-1"},
        {"lane_id": "b-1"},
        {"lane_id": "a-2"},
    ]

    overview = _scheduler_overview(
        [
            {
                "run_id": "run-blocked",
                "repo_id": "repo-a",
                "plan": {
                    "host_capacity_policy": {
                        "host_id": "windows-main",
                        "resource_budget": {"cpu": 1, "service": 1, "serial": 0},
                        "reserve_runtime_headroom": True,
                    }
                },
                "shards": [
                    {
                        "shard_id": "active-unit",
                        "lane_id": "unit",
                        "status": "running",
                        "metadata": {"resource_class": "cpu", "parallel_group": "unit"},
                    },
                    {
                        "shard_id": "queued-unit",
                        "lane_id": "unit-queued",
                        "status": "queued_local",
                        "metadata": {"resource_class": "cpu", "parallel_group": "unit"},
                    },
                ],
            }
        ]
    )

    host = overview.hosts[0]
    assert host.admitted_candidates == []
    assert host.blocked_candidates[0].blocking_reasons == [
        "cpu budget exhausted (1/1)",
        "parallel group `unit` already active",
    ]
    assert "parallel group `unit` already active" in host.blocking_reasons


@pytest.mark.asyncio
async def test_ci_observer_handle_validates_required_ids_and_unknown_intent() -> None:
    skill = CiObserverSkill(storage=_storage())

    missing_run = await skill.handle(
        SkillRequest(intent="ci_run_report", user_id="owner-1", context={})
    )
    missing_repo = await skill.handle(
        SkillRequest(intent="ci_reporting_project_resources", user_id="owner-1", context={})
    )
    missing_node = await skill.handle(
        SkillRequest(intent="ci_reporting_worker_resources", user_id="owner-1", context={})
    )
    missing_graph = await skill.handle(
        SkillRequest(intent="ci_run_graph", user_id="owner-1", context={})
    )
    missing_correlation = await skill.handle(
        SkillRequest(intent="ci_run_correlation_context", user_id="owner-1", context={})
    )
    missing_diagnostics = await skill.handle(
        SkillRequest(intent="ci_run_diagnostics", user_id="owner-1", context={})
    )
    missing_artifacts = await skill.handle(
        SkillRequest(intent="ci_run_artifacts", user_id="owner-1", context={})
    )
    missing_evidence = await skill.handle(
        SkillRequest(intent="ci_run_evidence", user_id="owner-1", context={})
    )
    missing_coaching = await skill.handle(
        SkillRequest(intent="ci_run_coaching", user_id="owner-1", context={})
    )
    unknown = await skill.handle(
        SkillRequest(intent="ci_reporting_unknown", user_id="owner-1", context={})
    )

    assert missing_run.success is False
    assert "run_id is required" in str(missing_run.error)
    assert missing_repo.success is False
    assert "repo_id is required" in str(missing_repo.error)
    assert missing_node.success is False
    assert "node_id is required" in str(missing_node.error)
    assert missing_graph.success is False
    assert "run_id is required" in str(missing_graph.error)
    assert missing_correlation.success is False
    assert "run_id is required" in str(missing_correlation.error)
    assert missing_diagnostics.success is False
    assert "run_id is required" in str(missing_diagnostics.error)
    assert missing_artifacts.success is False
    assert "run_id is required" in str(missing_artifacts.error)
    assert missing_evidence.success is False
    assert "run_id is required" in str(missing_evidence.error)
    assert missing_coaching.success is False
    assert "run_id is required" in str(missing_coaching.error)
    assert unknown.success is False
    assert "Unknown CI observer intent" in str(unknown.error)
