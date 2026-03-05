"""Unit tests for autonomous PR orchestration guardrails."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from zetherion_ai.automerge.orchestrator import (
    AutomergeExecutionRequest,
    AutomergeGuardrails,
    AutomergeOrchestrator,
)
from zetherion_ai.skills.github.models import PullRequest


def _pull_request() -> PullRequest:
    return PullRequest(
        number=77,
        title="Automerge PR",
        html_url="https://example.com/pr/77",
        head_ref="codex/automerge-1",
        base_ref="main",
        additions=42,
        deletions=8,
        changed_files=2,
    )


@pytest.mark.asyncio
async def test_execute_merges_when_guardrails_and_checks_pass() -> None:
    api = SimpleNamespace(
        ensure_branch=AsyncMock(
            return_value={"created": True, "ref": "refs/heads/codex/automerge-1", "sha": "abc"}
        ),
        find_open_pull_request=AsyncMock(return_value=None),
        create_pull_request=AsyncMock(return_value=_pull_request()),
        get_pull_request=AsyncMock(return_value=_pull_request()),
        list_pull_request_files=AsyncMock(
            return_value=[{"filename": "src/app.py"}, {"filename": "tests/test_app.py"}]
        ),
        list_check_runs=AsyncMock(
            return_value=[
                {
                    "name": "CI/CD Pipeline",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        ),
        merge_pull_request=AsyncMock(
            return_value={"merged": True, "message": "Pull Request successfully merged"}
        ),
    )
    request = AutomergeExecutionRequest(
        tenant_id="tenant-1",
        repository="openclaw/openclaw",
        base_branch="main",
        source_ref="main",
        head_branch="codex/automerge-1",
        guardrails=AutomergeGuardrails(
            allowed_paths=("src/", "tests/"),
            required_checks=("CI/CD Pipeline",),
            max_changed_files=10,
            max_additions=500,
            max_deletions=250,
        ),
    )

    result = await AutomergeOrchestrator().execute(request=request, api=api)
    assert result.status == "merged"
    assert result.pr_number == 77
    assert result.decision == "allow"
    assert result.branch_created is True
    api.merge_pull_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_execute_blocks_forbidden_actions_before_github_calls() -> None:
    api = SimpleNamespace(
        ensure_branch=AsyncMock(),
        find_open_pull_request=AsyncMock(),
        create_pull_request=AsyncMock(),
        get_pull_request=AsyncMock(),
        list_pull_request_files=AsyncMock(),
        list_check_runs=AsyncMock(),
        merge_pull_request=AsyncMock(),
    )
    request = AutomergeExecutionRequest(
        tenant_id="tenant-1",
        repository="openclaw/openclaw",
        requested_actions=("git.force_push",),
        guardrails=AutomergeGuardrails(
            forbidden_actions=("git.force_push",),
        ),
    )

    result = await AutomergeOrchestrator().execute(request=request, api=api)
    assert result.status == "blocked"
    assert result.reason == "forbidden_actions_requested"
    api.ensure_branch.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_blocks_when_required_checks_are_not_green() -> None:
    pr = _pull_request()
    api = SimpleNamespace(
        ensure_branch=AsyncMock(
            return_value={"created": False, "ref": "refs/heads/codex/automerge-1", "sha": "abc"}
        ),
        find_open_pull_request=AsyncMock(return_value=pr),
        create_pull_request=AsyncMock(),
        get_pull_request=AsyncMock(return_value=pr),
        list_pull_request_files=AsyncMock(return_value=[{"filename": "src/app.py"}]),
        list_check_runs=AsyncMock(
            return_value=[
                {
                    "name": "CI/CD Pipeline",
                    "status": "completed",
                    "conclusion": "failure",
                }
            ]
        ),
        merge_pull_request=AsyncMock(),
    )
    request = AutomergeExecutionRequest(
        tenant_id="tenant-1",
        repository="openclaw/openclaw",
        head_branch="codex/automerge-1",
        guardrails=AutomergeGuardrails(
            allowed_paths=("src/",),
            required_checks=("CI/CD Pipeline",),
        ),
    )

    result = await AutomergeOrchestrator().execute(request=request, api=api)
    assert result.status == "blocked"
    assert result.reason == "required_checks_not_green"
    api.merge_pull_request.assert_not_awaited()


@pytest.mark.asyncio
async def test_execute_flags_rollback_required_on_post_merge_validation_failure() -> None:
    pr = _pull_request()
    api = SimpleNamespace(
        ensure_branch=AsyncMock(
            return_value={"created": False, "ref": "refs/heads/codex/automerge-1", "sha": "abc"}
        ),
        find_open_pull_request=AsyncMock(return_value=pr),
        create_pull_request=AsyncMock(),
        get_pull_request=AsyncMock(return_value=pr),
        list_pull_request_files=AsyncMock(return_value=[{"filename": "src/app.py"}]),
        list_check_runs=AsyncMock(
            return_value=[
                {
                    "name": "CI/CD Pipeline",
                    "status": "completed",
                    "conclusion": "success",
                }
            ]
        ),
        merge_pull_request=AsyncMock(return_value={"merged": True, "message": "merged"}),
    )
    request = AutomergeExecutionRequest(
        tenant_id="tenant-1",
        repository="openclaw/openclaw",
        head_branch="codex/automerge-1",
        guardrails=AutomergeGuardrails(
            allowed_paths=("src/",),
            required_checks=("CI/CD Pipeline",),
        ),
        post_merge_validation_passed=False,
    )

    result = await AutomergeOrchestrator().execute(request=request, api=api)
    assert result.status == "rollback_required"
    assert result.reason == "merged_but_post_merge_validation_failed"
