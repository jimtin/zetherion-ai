"""Additional coverage for CI controller and observer error branches."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import zetherion_ai.skills.ci_controller as ci_controller
from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.ci_controller import CiControllerSkill
from zetherion_ai.skills.ci_observer import CiObserverSkill


def _storage() -> MagicMock:
    storage = MagicMock()
    storage.upsert_repo_profile = AsyncMock(return_value={"repo_id": "zetherion-ai"})
    storage.list_repo_profiles = AsyncMock(return_value=[])
    storage.get_repo_profile = AsyncMock(return_value=None)
    storage.create_plan_snapshot = AsyncMock(return_value={"plan_id": "plan-1", "version": 1})
    storage.get_plan_snapshot = AsyncMock(return_value=None)
    storage.list_plan_versions = AsyncMock(return_value=[])
    storage.create_compiled_plan = AsyncMock(return_value={"compiled_plan_id": "compiled-1"})
    storage.upsert_schedule = AsyncMock(return_value={"schedule_id": "schedule-1"})
    storage.list_schedules = AsyncMock(return_value=[])
    storage.create_run = AsyncMock(return_value={"run_id": "run-1"})
    storage.get_run = AsyncMock(return_value=None)
    storage.list_runs = AsyncMock(return_value=[])
    storage.get_local_repo_readiness = AsyncMock(return_value=(None, None))
    storage.store_run_github_receipt = AsyncMock(return_value=None)
    storage.merge_run_metadata = AsyncMock(return_value=None)
    storage.set_run_status = AsyncMock(
        return_value={"run_id": "run-1", "status": "promotion_blocked"}
    )
    storage.get_run_events = AsyncMock(return_value=[])
    storage.get_run_log_chunks = AsyncMock(return_value=[])
    storage.get_run_debug_bundle = AsyncMock(return_value=None)
    storage.get_reporting_summary = AsyncMock(return_value={"repos": []})
    storage.get_reporting_readiness = AsyncMock(return_value={"repo_readiness": []})
    storage.get_project_resource_report = AsyncMock(return_value={"items": []})
    storage.get_project_failure_report = AsyncMock(return_value={"failures": []})
    storage.get_worker_resource_report = AsyncMock(return_value={"samples": []})
    return storage


@pytest.mark.asyncio
async def test_ci_controller_metadata_initialize_and_unknown_intent() -> None:
    storage = _storage()
    skill = CiControllerSkill(storage=storage)

    assert skill.metadata.name == "ci_controller"
    assert "ci_run_promote" in skill.metadata.intents
    assert await skill.initialize() is True

    response = await skill.handle(SkillRequest(intent="bogus"))

    assert response.success is False
    assert response.error == "Unknown CI controller intent: bogus"


@pytest.mark.asyncio
async def test_ci_controller_handle_reports_validation_and_not_found_errors(
    monkeypatch,
) -> None:
    storage = _storage()
    skill = CiControllerSkill(storage=storage)
    monkeypatch.setattr(ci_controller, "default_repo_profile", lambda repo_id: None)

    requests = [
        SkillRequest(intent="ci_repo_get", context={}),
        SkillRequest(intent="ci_repo_get", context={"repo_id": "missing"}),
        SkillRequest(intent="ci_plan_save", context={"repo_id": "zetherion-ai"}),
        SkillRequest(intent="ci_plan_get", context={}),
        SkillRequest(intent="ci_plan_get", context={"plan_id": "missing"}),
        SkillRequest(intent="ci_plan_versions", context={}),
        SkillRequest(intent="ci_run_get", context={}),
        SkillRequest(intent="ci_run_get", context={"run_id": "run-1"}),
        SkillRequest(intent="ci_run_rebalance", context={}),
        SkillRequest(intent="ci_run_rebalance", context={"run_id": "run-1"}),
        SkillRequest(intent="ci_run_store_github_receipt", context={}),
        SkillRequest(intent="ci_run_store_github_receipt", context={"run_id": "run-1"}),
        SkillRequest(intent="ci_run_store_release_receipt", context={}),
        SkillRequest(intent="ci_run_store_release_receipt", context={"run_id": "run-1"}),
        SkillRequest(intent="ci_run_publish_statuses", context={}),
        SkillRequest(intent="ci_run_publish_statuses", context={"run_id": "run-1"}),
        SkillRequest(intent="ci_run_promote", context={}),
        SkillRequest(intent="ci_run_promote", context={"run_id": "run-1"}),
    ]

    errors = [str((await skill.handle(request)).error or "") for request in requests]

    assert errors == [
        "repo_id is required",
        "Repo profile `missing` not found",
        "content_markdown is required",
        "plan_id is required",
        "Plan `missing` not found",
        "plan_id is required",
        "run_id is required",
        "Run `run-1` not found",
        "run_id is required",
        "Run `run-1` not found",
        "run_id is required",
        "Run `run-1` not found",
        "run_id is required",
        "Run `run-1` not found",
        "run_id is required",
        "Run `run-1` not found",
        "run_id is required",
        "Run `run-1` not found",
    ]


@pytest.mark.asyncio
async def test_ci_controller_plan_save_requires_repo_id() -> None:
    storage = _storage()
    skill = CiControllerSkill(storage=storage)

    response = await skill.handle(
        SkillRequest(intent="ci_plan_save", context={"content_markdown": "# plan"})
    )

    assert response.success is False
    assert response.error == "repo_id is required"


@pytest.mark.asyncio
async def test_ci_controller_run_start_uses_plan_snapshot_and_rejects_empty_full_plan() -> None:
    storage = _storage()
    storage.get_plan_snapshot.return_value = {"plan_id": "plan-1", "content_markdown": "# plan"}
    skill = CiControllerSkill(storage=storage)
    skill._resolve_repo_profile = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "repo_id": "zetherion-ai",
            "default_branch": "main",
            "mandatory_static_gates": [],
            "certification_requirements": [],
            "metadata": {},
        }
    )
    skill._compile_run_plan = MagicMock(return_value={"shards": []})  # type: ignore[method-assign]

    response = await skill.handle(
        SkillRequest(
            intent="ci_run_start",
            context={
                "repo_id": "zetherion-ai",
                "mode": "full",
                "plan_id": "plan-1",
            },
        )
    )

    assert response.success is False
    assert response.error == "Repo `zetherion-ai` has no configured full shards"
    storage.get_plan_snapshot.assert_awaited_once()
    storage.create_compiled_plan.assert_awaited_once()


@pytest.mark.asyncio
async def test_ci_controller_build_readiness_receipts_cover_failure_and_pending_states() -> None:
    skill = CiControllerSkill(storage=MagicMock())
    repo = {
        "repo_id": "zetherion-ai",
        "promotion_policy": {"status_contexts": {}},
    }

    merge_failure, deploy_failure, _, _ = await skill._build_readiness_receipts(
        repo=repo,
        run={"repo_id": "zetherion-ai", "git_ref": "a" * 40, "shards": []},
        review={"merge_blocked": True, "verdict": "blocked"},
        release_receipt={"status": "blocked", "summary": "schema failed", "blocker_count": 1},
        requested_by="owner-1",
    )
    merge_pending, deploy_pending, repo_pending, _ = await skill._build_readiness_receipts(
        repo=repo,
        run={
            "repo_id": "zetherion-ai",
            "git_ref": "b" * 40,
            "shards": [
                {
                    "lane_id": "z-release",
                    "status": "succeeded",
                    "artifact_contract": {"expects": ["stdout.log"]},
                    "metadata": {"covered_required_paths": ["release_verification"]},
                    "result": {},
                    "error": {},
                }
            ],
        },
        review={"merge_blocked": False},
        release_receipt={"status": "degraded", "summary": "manual verification pending"},
        requested_by="owner-1",
    )

    assert merge_failure["state"] == "failure"
    assert deploy_failure["state"] == "failure"
    assert deploy_failure["description"] == "schema failed"
    assert merge_pending["state"] == "pending"
    assert "incomplete" in merge_pending["description"]
    assert deploy_pending["state"] == "pending"
    assert deploy_pending["description"] == "manual verification pending"
    assert repo_pending["missing_evidence"] == ["stdout.log"]


@pytest.mark.asyncio
async def test_ci_controller_build_readiness_receipts_pending_when_worker_receipts_sync() -> None:
    skill = CiControllerSkill(storage=MagicMock())
    repo = {
        "repo_id": "zetherion-ai",
        "promotion_policy": {"status_contexts": {}},
    }

    merge_receipt, _, _, _ = await skill._build_readiness_receipts(
        repo=repo,
        run={
            "repo_id": "zetherion-ai",
            "git_ref": "c" * 40,
            "shards": [{"lane_id": "windows", "status": "awaiting_sync"}],
        },
        review={"merge_blocked": False},
        release_receipt={},
        requested_by="owner-1",
    )

    assert merge_receipt["state"] == "pending"
    assert merge_receipt["description"] == "Worker receipts are still syncing to Zetherion."


@pytest.mark.asyncio
async def test_ci_controller_publish_statuses_and_repo_resolution_cover_remaining_branches(
    monkeypatch,
) -> None:
    storage = _storage()
    skill = CiControllerSkill(storage=storage)

    assert await skill._publish_github_statuses(repo={}, run={}) == {
        "published": False,
        "reason": "github_repo_missing",
    }
    assert await skill._publish_github_statuses(
        repo={"github_repo": "jimtin/zetherion-ai"},
        run={},
    ) == {
        "published": False,
        "reason": "git_sha_missing",
    }

    class _FakeSecret:
        def get_secret_value(self) -> str:
            return "token-123"

    class _FakeGitHubClient:
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
            return None

        async def close(self) -> None:
            return None

    monkeypatch.setattr(
        ci_controller,
        "get_settings",
        lambda: SimpleNamespace(github_token=_FakeSecret()),
    )
    monkeypatch.setattr(ci_controller, "GitHubClient", lambda _token: _FakeGitHubClient())

    assert await skill._publish_github_statuses(
        repo={"github_repo": "jimtin/zetherion-ai"},
        run={"metadata": {"git_sha": "a" * 40}, "github_receipts": {}},
    ) == {
        "published": False,
        "reason": "receipts_missing",
    }

    skipped = await skill._publish_github_statuses(
        repo={"github_repo": "jimtin/zetherion-ai"},
        run={
            "metadata": {"git_sha": "a" * 40},
            "github_receipts": {
                "merge_readiness": {"context": "", "state": "success", "description": ""},
            },
        },
    )
    assert skipped == {"published": False, "sha": "a" * 40, "contexts": []}

    with pytest.raises(ValueError, match="repo_id is required"):
        await skill._resolve_repo_profile("owner-1", "")

    storage.get_repo_profile.return_value = None
    storage.upsert_repo_profile.return_value = {"repo_id": "zetherion-ai"}
    monkeypatch.setattr(
        ci_controller,
        "default_repo_profile",
        lambda repo_id: {"repo_id": repo_id, "github_repo": "jimtin/zetherion-ai"},
    )
    resolved = await skill._resolve_repo_profile("owner-1", "zetherion-ai")
    assert resolved["repo_id"] == "zetherion-ai"

    monkeypatch.setattr(ci_controller, "default_repo_profile", lambda repo_id: None)
    with pytest.raises(ValueError, match="Repo profile `missing-repo` not found"):
        await skill._resolve_repo_profile("owner-1", "missing-repo")


@pytest.mark.asyncio
async def test_ci_controller_promotion_blocks_when_review_receipt_is_blocked() -> None:
    storage = _storage()
    storage.get_run.return_value = {
        "run_id": "run-1",
        "repo_id": "zetherion-ai",
        "review_receipts": {"merge_blocked": True},
        "shards": [],
    }
    skill = CiControllerSkill(storage=storage)
    skill._resolve_repo_profile = AsyncMock(return_value={"repo_id": "zetherion-ai"})  # type: ignore[method-assign]

    response = await skill.handle(
        SkillRequest(
            intent="ci_run_promote",
            user_id="owner-1",
            context={"run_id": "run-1"},
        )
    )

    assert response.success is True
    assert response.data == {
        "run": {"run_id": "run-1", "status": "promotion_blocked"},
        "promoted": False,
    }
    assert response.message == "Run `run-1` is blocked from promotion."
    storage.set_run_status.assert_awaited_once_with("owner-1", "run-1", "promotion_blocked")


@pytest.mark.asyncio
async def test_ci_observer_metadata_initialize_and_error_paths() -> None:
    storage = _storage()
    skill = CiObserverSkill(storage=storage)

    assert skill.metadata.name == "ci_observer"
    assert await skill.initialize() is True

    missing_run = await skill.handle(SkillRequest(intent="ci_run_events", context={}))
    missing_repo = await skill.handle(
        SkillRequest(intent="ci_reporting_project_resources", context={})
    )
    missing_node = await skill.handle(
        SkillRequest(intent="ci_reporting_worker_resources", context={})
    )
    unknown = await skill.handle(SkillRequest(intent="unknown"))

    assert missing_run.success is False and missing_run.error == "run_id is required"
    assert missing_repo.success is False and missing_repo.error == "repo_id is required"
    assert missing_node.success is False and missing_node.error == "node_id is required"
    assert unknown.success is False and unknown.error == "Unknown CI observer intent: unknown"


@pytest.mark.asyncio
async def test_ci_observer_missing_identifiers_cover_remaining_error_branches() -> None:
    storage = _storage()
    skill = CiObserverSkill(storage=storage)

    missing_logs = await skill.handle(SkillRequest(intent="ci_run_logs", context={}))
    missing_debug_bundle = await skill.handle(
        SkillRequest(intent="ci_run_debug_bundle", context={})
    )
    missing_failures = await skill.handle(
        SkillRequest(intent="ci_reporting_project_failures", context={})
    )

    assert missing_logs.success is False
    assert missing_logs.error == "run_id is required"
    assert missing_debug_bundle.success is False
    assert missing_debug_bundle.error == "run_id is required"
    assert missing_failures.success is False
    assert missing_failures.error == "repo_id is required"


@pytest.mark.asyncio
async def test_ci_observer_success_paths_cover_reporting_branches() -> None:
    storage = _storage()
    storage.get_run_debug_bundle.return_value = {"bundle_id": "bundle-1"}
    storage.get_reporting_summary.return_value = {"repos": [{"repo_id": "zetherion-ai"}]}
    storage.get_reporting_readiness.return_value = {
        "repo_readiness": [{"repo_id": "zetherion-ai", "merge_ready": True}]
    }
    storage.get_project_failure_report.return_value = {
        "failures": [{"repo_id": "zetherion-ai", "count": 1}]
    }
    skill = CiObserverSkill(storage=storage)

    debug_bundle = await skill.handle(
        SkillRequest(intent="ci_run_debug_bundle", context={"run_id": "run-1"})
    )
    summary = await skill.handle(SkillRequest(intent="ci_reporting_summary"))
    readiness = await skill.handle(SkillRequest(intent="ci_reporting_readiness"))
    failures = await skill.handle(
        SkillRequest(
            intent="ci_reporting_project_failures",
            context={"repo_id": "zetherion-ai", "limit": 10},
        )
    )

    assert debug_bundle.success is True
    assert debug_bundle.message == "Loaded debug bundle."
    assert debug_bundle.data == {"debug_bundle": {"bundle_id": "bundle-1"}}

    assert summary.success is True
    assert summary.message == "Loaded CI reporting summary."
    assert summary.data == {"summary": {"repos": [{"repo_id": "zetherion-ai"}]}}

    assert readiness.success is True
    assert readiness.message == "Loaded CI readiness summary."
    assert readiness.data["readiness"]["repo_readiness"] == [
        {"repo_id": "zetherion-ai", "merge_ready": True, "github_security": None}
    ]
    github_security = readiness.data["readiness"]["github_security"]
    assert github_security["available"] is False
    assert github_security["blocking"] is False
    assert github_security["reason"] == "github_token_missing"
    assert github_security["status"] == "unavailable"
    assert github_security["repos"] == []
    assert github_security["summary"] == (
        "GitHub security alerts could not be checked because GITHUB_TOKEN is missing."
    )

    assert failures.success is True
    assert failures.message == "Loaded project failure report."
    assert failures.data == {"report": {"failures": [{"repo_id": "zetherion-ai", "count": 1}]}}
