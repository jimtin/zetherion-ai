"""Focused unit coverage for CI controller helper branches."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import zetherion_ai.skills.ci_controller as ci_controller
from zetherion_ai.owner_ci import default_repo_profile
from zetherion_ai.skills.base import SkillRequest


def test_repo_profile_input_normalizes_extensions_and_validates_required_fields() -> None:
    payload = {
        "repo_id": "zetherion-ai",
        "display_name": "Zetherion AI",
        "github_repo": "jimtin/zetherion-ai",
        "stack_kind": "python",
        "default_branch": "main",
        "mandatory_static_gates": [{"lane_id": "ruff"}],
        "mandatory_security_gates": [{"lane_id": "gitleaks"}],
        "resource_classes": {"cpu": {"max_parallel": 8}},
        "windows_execution_mode": "docker_only",
        "allowed_paths": ["/tmp/zetherion-ai"],
        "active": False,
    }

    normalized = ci_controller._normalize_repo_profile_input(payload)

    assert normalized["repo_id"] == "zetherion-ai"
    assert normalized["github_repo"] == "jimtin/zetherion-ai"
    assert normalized["windows_execution_mode"] == "docker_only"
    assert normalized["metadata"]["mandatory_static_gates"] == [{"lane_id": "ruff"}]
    assert normalized["metadata"]["mandatory_security_gates"] == [{"lane_id": "gitleaks"}]
    assert normalized["resource_classes"] == {"cpu": {"max_parallel": 8}}
    assert normalized["active"] is False

    with pytest.raises(ValueError, match="repo_id"):
        ci_controller._normalize_repo_profile_input({"github_repo": "jimtin/zetherion-ai"})
    with pytest.raises(ValueError, match="github_repo"):
        ci_controller._normalize_repo_profile_input({"repo_id": "repo", "stack_kind": "python"})
    with pytest.raises(ValueError, match="stack_kind"):
        ci_controller._normalize_repo_profile_input(
            {"repo_id": "repo", "github_repo": "jimtin/repo"}
        )


def test_lane_and_repo_helpers_cover_coercion_contexts_and_sha_inference() -> None:
    lanes = ci_controller._coerce_lane_objects(
        ["lint", {"id": "unit", "lane_label": "Unit"}, {"lane_id": ""}]
    )
    assert lanes == [
        {"lane_id": "lint", "lane_label": "lint"},
        {"id": "unit", "lane_label": "Unit", "lane_id": "unit"},
        {"lane_id": "lane-3", "lane_label": "lane-3"},
    ]

    assert ci_controller._parse_github_repo("jimtin/zetherion-ai") == ("jimtin", "zetherion-ai")
    with pytest.raises(ValueError, match="owner/repo"):
        ci_controller._parse_github_repo("zetherion-ai")
    with pytest.raises(ValueError, match="owner/repo"):
        ci_controller._parse_github_repo("jimtin/")

    skipped_lanes = ci_controller._coerce_lane_objects(
        ["", {"lane_id": " ", "id": " ", "lane_label": "ignored"}]
    )
    assert skipped_lanes == []

    merge_context, deploy_context = ci_controller._status_contexts_for(
        {"promotion_policy": {"status_contexts": {"merge": "custom/merge"}}}
    )
    assert merge_context == "custom/merge"
    assert deploy_context == "zetherion/deploy-readiness"

    assert ci_controller._infer_git_sha({"metadata": {"head_sha": "A" * 40}}) == ("a" * 40)
    assert ci_controller._infer_git_sha({"git_ref": "123"}) is None
    assert ci_controller.CiControllerSkill._scope_id("owner-1", "zetherion-ai") == (
        "owner:owner-1:repo:zetherion-ai"
    )


def test_compile_run_plan_sets_windows_dependencies_required_paths_and_certification_payload(
) -> None:
    skill = ci_controller.CiControllerSkill(storage=MagicMock())
    repo = {
        "repo_id": "zetherion-ai",
        "stack_kind": "python",
        "allowed_paths": ["/tmp/zetherion-ai"],
        "windows_execution_mode": "docker_only",
        "mandatory_static_gates": [
            {
                "lane_id": "ruff-check",
                "lane_label": "Ruff",
                "command": ["ruff", "check", "src"],
                "metadata": {"covered_required_paths": ["static_quality"]},
            }
        ],
        "mandatory_security_gates": [
            {
                "lane_id": "gitleaks",
                "lane_label": "Secrets",
                "command": ["gitleaks", "detect"],
                "metadata": {"covered_required_paths": ["security_quality"]},
            }
        ],
        "local_fast_lanes": [
            {
                "lane_id": "z-unit-core",
                "command": ["pytest", "tests/unit"],
                "metadata": {"covered_required_paths": ["dm_reply_path"]},
            }
        ],
        "local_full_lanes": [
            {
                "lane_id": "z-int-runtime",
                "command": ["pytest", "tests/integration"],
                "metadata": {
                    "covered_required_paths": ["queue_reliability"],
                    "timeout_seconds": 600,
                },
            }
        ],
        "windows_full_lanes": [
            {
                "lane_id": "discord-required-e2e",
                "execution_target": "windows_local",
                "command": ["bash", "scripts/run-required-discord-e2e.sh"],
                "payload": {"container_spec": {"image": "python:3.12"}},
                "metadata": {
                    "covered_required_paths": ["discord_roundtrip"],
                    "parallel_group": "discord",
                    "resource_class": "serial",
                },
            }
        ],
        "metadata": {"certification_matrix": ["discord", "queue"]},
        "certification_requirements": ["discord_roundtrip"],
        "scheduling_policy": {"resource_budgets": {"cpu": 8, "service": 2, "serial": 1}},
        "debug_policy": {"retain_debug_bundle_days": 7, "redact_display_logs": False},
        "scheduled_canaries": [{"lane_id": "z-e2e-discord-real"}],
    }

    plan = skill._compile_run_plan(repo=repo, mode="certification", git_ref="main")

    assert plan["required_static_gate_ids"] == ["ruff-check"]
    assert plan["required_security_gate_ids"] == ["gitleaks"]
    assert plan["required_gate_categories"] == [
        "static",
        "security",
        "unit",
        "integration",
        "e2e",
    ]
    assert plan["required_paths"] == [
        "discord_roundtrip",
        "dm_reply_path",
        "queue_reliability",
        "security_quality",
        "static_quality",
    ]
    assert plan["resource_budget"] == {"cpu": 8, "service": 2, "serial": 1}
    assert plan["host_capacity_policy"]["admission_mode"] == "dynamic_resource_based"
    assert plan["debug_bundle_contract"]["retain_debug_bundle_days"] == 7
    assert [shard["lane_id"] for shard in plan["shards"]] == [
        "ruff-check",
        "gitleaks",
        "z-unit-core",
        "z-int-runtime",
        "discord-required-e2e",
    ]
    windows_shard = plan["shards"][-1]
    assert windows_shard["runner"] == "docker"
    assert windows_shard["required_capabilities"] == ["ci.test.run"]
    assert windows_shard["metadata"]["depends_on"] == ["ruff-check", "gitleaks"]
    assert windows_shard["payload"]["certification_matrix"] == ["discord", "queue"]
    assert windows_shard["payload"]["certification_requirements"] == ["discord_roundtrip"]
    assert windows_shard["metadata"]["certification_mode"] is True
    assert plan["shards"][0]["metadata"]["gate_family"] == "static"
    assert plan["shards"][1]["metadata"]["gate_family"] == "security"
    assert plan["shards"][2]["metadata"]["gate_family"] == "unit"
    assert plan["shards"][3]["metadata"]["gate_family"] == "integration"
    assert plan["shards"][4]["metadata"]["gate_family"] == "e2e"
    assert plan["shards"][4]["metadata"]["resource_reservation"]["resource_class"] == "serial"
    assert plan["shards"][3]["timeout_seconds"] == 600


def test_compile_run_plan_rejects_docker_only_windows_shard_without_container_spec() -> None:
    skill = ci_controller.CiControllerSkill(storage=MagicMock())
    repo = {
        "repo_id": "zetherion-ai",
        "stack_kind": "python",
        "allowed_paths": ["/tmp/zetherion-ai"],
        "windows_execution_mode": "docker_only",
        "mandatory_static_gates": [],
        "local_fast_lanes": [],
        "local_full_lanes": [],
        "windows_full_lanes": [
            {
                "lane_id": "discord-required-e2e",
                "execution_target": "windows_local",
            }
        ],
    }

    with pytest.raises(ValueError, match="container_spec"):
        skill._compile_run_plan(repo=repo, mode="certification", git_ref="main")


def test_default_cgs_windows_lanes_use_real_images_and_persistent_volume_mounts() -> None:
    profile = default_repo_profile("catalyst-group-solutions")

    assert profile is not None
    lanes = list(profile["windows_full_lanes"])
    assert [lane["lane_id"] for lane in lanes] == ["integration-critical", "golive-gate"]

    integration_lane = lanes[0]
    integration_spec = integration_lane["payload"]["container_spec"]
    assert integration_spec["image"] == "node:22-bookworm"
    assert any(
        mount["source"] == "cgs-node-tool-node_modules"
        for mount in integration_spec["mounts"]
    )
    assert any(
        mount["source"] == "cgs-node-tool-yarn_cache" for mount in integration_spec["mounts"]
    )
    assert integration_spec["env"]["CI"] == "true"
    assert "yarn cgs-ai:test:integration" in integration_spec["command"][-1]

    golive_lane = lanes[1]
    golive_spec = golive_lane["payload"]["container_spec"]
    assert golive_spec["image"] == "mcr.microsoft.com/playwright:v1.58.2-noble"
    assert golive_spec["env"]["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] == "1"
    assert "yarn cgs-ai:test:golive" in golive_spec["command"][-1]


@pytest.mark.asyncio
async def test_publish_github_statuses_handles_missing_inputs_and_success(monkeypatch) -> None:
    skill = ci_controller.CiControllerSkill(storage=MagicMock())
    repo = {"github_repo": "jimtin/zetherion-ai"}
    run = {
        "metadata": {"git_sha": "0" * 40, "status_target_url": "https://example.com/status"},
        "github_receipts": {
            "merge_readiness": {
                "context": "zetherion/merge-readiness",
                "state": "success",
                "description": "Merge ready.",
            },
            "deploy_readiness": {
                "context": "zetherion/deploy-readiness",
                "state": "pending",
                "description": "Waiting on receipt.",
            },
        },
    }

    monkeypatch.setattr(
        ci_controller,
        "get_settings",
        lambda: SimpleNamespace(github_token=None),
    )
    assert await skill._publish_github_statuses(repo=repo, run=run) == {
        "published": False,
        "reason": "github_token_missing",
    }

    published_calls: list[dict[str, str | None]] = []

    class FakeSecret:
        def get_secret_value(self) -> str:
            return "token-123"

    class FakeGitHubClient:
        async def create_commit_status(
            self,
            owner: str,
            repo_name: str,
            sha: str,
            *,
            state: str,
            context: str,
            description: str,
            target_url: str | None,
        ) -> None:
            published_calls.append(
                {
                    "owner": owner,
                    "repo_name": repo_name,
                    "sha": sha,
                    "state": state,
                    "context": context,
                    "description": description,
                    "target_url": target_url,
                }
            )

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        ci_controller,
        "get_settings",
        lambda: SimpleNamespace(github_token=FakeSecret()),
    )
    monkeypatch.setattr(ci_controller, "GitHubClient", lambda _token: FakeGitHubClient())

    result = await skill._publish_github_statuses(repo=repo, run=run)

    assert result == {
        "published": True,
        "sha": "0" * 40,
        "contexts": ["zetherion/merge-readiness", "zetherion/deploy-readiness"],
    }
    assert published_calls[0]["owner"] == "jimtin"
    assert published_calls[0]["target_url"] == "https://example.com/status"
    assert published_calls[1]["state"] == "pending"


def test_normalize_mode_rejects_unknown_values() -> None:
    request = SkillRequest(context={"mode": "full"})
    assert ci_controller.CiControllerSkill._normalize_mode(request) == "full"
    with pytest.raises(ValueError, match="Unsupported run mode"):
        ci_controller.CiControllerSkill._normalize_mode(SkillRequest(context={"mode": "bogus"}))
