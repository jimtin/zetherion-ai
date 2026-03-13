"""Coverage for external service evidence collection in agent bootstrap."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.skills.agent_bootstrap import AgentBootstrapSkill
from zetherion_ai.skills.github.client import GitHubAPIError
from zetherion_ai.skills.vercel.client import VercelAPIError


def _storage() -> MagicMock:
    storage = MagicMock()
    storage.upsert_operation_ref = AsyncMock(return_value={"ref_id": "ref-1"})
    storage.record_operation_evidence = AsyncMock(
        side_effect=[
            {"evidence_id": "summary-1"},
            {"evidence_id": "events-1"},
            {"evidence_id": "artifacts-1"},
            {"evidence_id": "logs-1"},
            {"evidence_id": "summary-2"},
            {"evidence_id": "summary-3"},
            {"evidence_id": "logs-3"},
            {"evidence_id": "summary-4"},
            {"evidence_id": "events-4"},
            {"evidence_id": "summary-5"},
        ]
    )
    storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})
    storage.get_run_log_chunks = AsyncMock(
        side_effect=[
            [{"message": "clerk error: auth failed"}],
            [{"message": "auth callback failed"}],
        ]
    )
    return storage


def _skill(storage: MagicMock) -> AgentBootstrapSkill:
    skill = AgentBootstrapSkill(storage=storage)
    skill._record_gap = AsyncMock(return_value={"gap_id": "gap-1"})  # type: ignore[method-assign]
    skill._require_github_connector = AsyncMock(  # type: ignore[method-assign]
        return_value={"secret_value": "gh-secret"}
    )
    skill._require_connector = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {
                "secret_value": "vercel-secret",
                "metadata": {"team_id": "team-1", "project_name": "cgs"},
            },
            {
                "metadata": {
                    "issuer": "https://clerk.example.com",
                    "jwks_url": "https://clerk.example.com/jwks",
                }
            },
            {"secret_value": "stripe-secret"},
            {"secret_value": "stripe-secret"},
        ]
    )
    return skill


def _app_profile() -> dict[str, object]:
    return {
        "app_id": "catalyst-group-solutions",
        "profile": {
            "repo_ids": ["catalyst-group-solutions"],
            "github_repos": ["jimtin/catalyst-group-solutions"],
            "service_connector_map": {
                "github": {"connector_id": "github-primary"},
                "vercel": {"connector_id": "vercel-primary"},
                "clerk": {"connector_id": "clerk-primary"},
                "stripe": {"connector_id": "stripe-primary"},
            },
        },
    }


@pytest.mark.asyncio
async def test_collect_service_evidence_covers_github_vercel_clerk_and_stripe_paths() -> None:
    storage = _storage()
    skill = _skill(storage)

    repository = SimpleNamespace(
        default_branch="main",
        to_dict=lambda: {"name": "catalyst-group-solutions"},
    )
    pull_request = SimpleNamespace(
        to_dict=lambda: {"number": 7, "head": {"ref": "feature/test"}},
    )
    github_client = MagicMock()
    github_client.get_repository = AsyncMock(return_value=repository)
    github_client.get_pull_request = AsyncMock(return_value=pull_request)
    github_client.list_workflow_runs = AsyncMock(
        return_value=[SimpleNamespace(id=101, head_sha="abc12345")]
    )
    github_client.get_workflow_run = AsyncMock(
        return_value={"id": 101, "name": "CI", "status": "completed", "conclusion": "failure"}
    )
    github_client.list_workflow_jobs = AsyncMock(
        return_value=[{"name": "unit", "conclusion": "failure"}]
    )
    github_client.list_workflow_run_artifacts = AsyncMock(return_value=[{"id": 1}])
    github_client.download_workflow_run_logs = AsyncMock(side_effect=GitHubAPIError("no logs"))
    github_client.close = AsyncMock()

    vercel_client = MagicMock()
    vercel_client.get_project = AsyncMock(return_value={"id": "proj_1", "name": "cgs"})
    vercel_client.list_deployments = AsyncMock(
        return_value=[
            {
                "uid": "dep_1",
                "readyState": "ERROR",
                "meta": {"githubCommitSha": "abc123", "githubCommitRef": "main"},
            }
        ]
    )
    vercel_client.list_domains = AsyncMock(return_value=[{"name": "cgs.example.com"}])
    vercel_client.get_deployment_events = AsyncMock(side_effect=VercelAPIError("no events"))
    vercel_client.close = AsyncMock()

    clerk_client = MagicMock()
    clerk_client.get_openid_configuration = AsyncMock(
        return_value={"issuer": "https://clerk.example.com"}
    )
    clerk_client.get_jwks = AsyncMock(return_value={"keys": [{"kid": "kid-1"}]})
    clerk_client.close = AsyncMock()

    stripe_client = MagicMock()
    stripe_client.get_account = AsyncMock(return_value={"id": "acct_1"})
    stripe_client.get_event = AsyncMock(
        side_effect=[
            {
                "id": "evt_1",
                "type": "invoice.paid",
                "pending_webhooks": 2,
                "data": {"object": {"id": "in_1"}},
            },
            None,
        ]
    )
    stripe_client.get_customer = AsyncMock(return_value=None)
    stripe_client.get_subscription = AsyncMock(return_value=None)
    stripe_client.close = AsyncMock()

    with (
        patch("zetherion_ai.skills.agent_bootstrap.GitHubClient", return_value=github_client),
        patch("zetherion_ai.skills.agent_bootstrap.VercelClient", return_value=vercel_client),
        patch(
            "zetherion_ai.skills.agent_bootstrap.ClerkMetadataClient",
            return_value=clerk_client,
        ),
        patch("zetherion_ai.skills.agent_bootstrap.StripeClient", return_value=stripe_client),
    ):
        github = await skill._collect_github_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            refs={"git_sha": "abc12345", "pr_number": "7"},
            request_context={"session_id": "sess-1", "run_id": "run-1"},
        )
        vercel = await skill._collect_vercel_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            refs={"git_sha": "abc123"},
            request_context={"session_id": "sess-1", "run_id": "run-1"},
        )
        clerk = await skill._collect_clerk_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            refs={"issuer": "https://clerk.example.com"},
            request_context={"session_id": "sess-1", "run_id": "run-1"},
        )
        stripe_failed = await skill._collect_stripe_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            refs={"stripe_event_id": "evt_1"},
            request_context={"session_id": "sess-1", "run_id": "run-1"},
        )
        stripe_missing = await skill._collect_stripe_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            refs={},
            request_context={"session_id": "sess-1", "run_id": "run-1"},
        )

    assert github["status"] == "failed"
    assert github["summary"]["artifact_count"] == 1
    assert vercel["status"] == "failed"
    assert vercel["summary"]["domains"][0]["name"] == "cgs.example.com"
    assert clerk["status"] == "failed"
    assert clerk["summary"]["app_diagnostics"][0]["message"].startswith("clerk error")
    assert stripe_failed["status"] == "failed"
    assert stripe_missing["status"] == "active"
    assert skill._record_gap.await_count == 3  # type: ignore[attr-defined]
    assert storage.record_operation_incident.await_count == 4
    assert storage.upsert_operation_ref.await_count >= 4
    github_client.close.assert_awaited_once()
    vercel_client.close.assert_awaited_once()
    clerk_client.close.assert_awaited_once()
    assert stripe_client.close.await_count == 2
