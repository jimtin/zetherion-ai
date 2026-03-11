"""Unit tests for owner-scoped CI controller skills."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.owner_ci import default_repo_profile
from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.ci_controller import CiControllerSkill
from zetherion_ai.skills.pr_reviewer import PrReviewerSkill


def _storage() -> MagicMock:
    storage = MagicMock()
    storage.upsert_repo_profile = AsyncMock()
    storage.list_repo_profiles = AsyncMock(return_value=[])
    storage.get_repo_profile = AsyncMock()
    storage.create_plan_snapshot = AsyncMock()
    storage.get_plan_snapshot = AsyncMock()
    storage.list_plan_versions = AsyncMock(return_value=[])
    storage.create_compiled_plan = AsyncMock(return_value={"compiled_plan_id": "compiled-1"})
    storage.upsert_schedule = AsyncMock()
    storage.list_schedules = AsyncMock(return_value=[])
    storage.create_run = AsyncMock()
    storage.get_run = AsyncMock()
    storage.list_runs = AsyncMock(return_value=[])
    storage.store_run_github_receipt = AsyncMock()
    storage.set_run_status = AsyncMock()
    storage.store_run_review = AsyncMock()
    return storage


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
            context={"repo_id": "zetherion-ai", "mode": "certification"},
        )
    )

    assert response.success is True
    create_kwargs = storage.create_run.await_args.kwargs
    assert create_kwargs["scope_id"] == "owner:owner-1:repo:zetherion-ai"
    assert create_kwargs["metadata"]["certification_required"] is True
    assert create_kwargs["metadata"]["platform_canary"] is True
    assert create_kwargs["metadata"]["windows_execution_mode"] == "docker_only"
    assert "discord_roundtrip" in create_kwargs["metadata"]["certification_requirements"]
    shards = create_kwargs["shards"]
    assert any(shard["execution_target"] == "windows_local" for shard in shards)
    assert any(shard["lane_id"] == "ruff-check" for shard in shards)
    assert any(shard["lane_id"] == "discord-required-e2e" for shard in shards)
    assert all(
        shard.get("runner") == "docker"
        for shard in shards
        if shard.get("execution_target") == "windows_local"
    )
    assert all(
        shard.get("payload", {}).get("certification_matrix")
        == profile["metadata"]["certification_matrix"]
        for shard in shards
    )


@pytest.mark.asyncio
async def test_ci_controller_promotion_blocks_when_shards_are_awaiting_sync() -> None:
    storage = _storage()
    storage.get_run.return_value = {
        "run_id": "run-1",
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
    assert any(shard["lane_id"] == "lint" for shard in plan["shards"])
    assert any(shard["lane_id"] == "golive-gate" for shard in plan["shards"])
