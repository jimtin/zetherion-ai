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


@pytest.mark.asyncio
async def test_collect_vercel_and_clerk_evidence_cover_explicit_ids_and_missing_run_logs() -> None:
    storage = _storage()
    skill = _skill(storage)

    vercel_client = MagicMock()
    vercel_client.get_project = AsyncMock(return_value={"id": "proj_2", "name": "cgs"})
    vercel_client.list_deployments = AsyncMock(
        return_value=[
            {
                "uid": "dep_fallback",
                "readyState": "READY",
                "meta": {"githubCommitRef": "main"},
            }
        ]
    )
    vercel_client.get_deployment = AsyncMock(
        return_value={
            "uid": "dep_explicit",
            "readyState": "READY",
            "state": "READY",
            "meta": {"githubCommitRef": "release"},
        }
    )
    vercel_client.list_domains = AsyncMock(return_value=[])
    vercel_client.get_deployment_events = AsyncMock(
        return_value=[
            {"text": "Build started"},
            {"payload": {"text": "Deploy complete"}},
        ]
    )
    vercel_client.close = AsyncMock()

    clerk_client = MagicMock()
    clerk_client.get_openid_configuration = AsyncMock(
        return_value={"issuer": "https://clerk.example.com"}
    )
    clerk_client.get_jwks = AsyncMock(return_value={"keys": [{"kid": "kid-2"}]})
    clerk_client.close = AsyncMock()

    with (
        patch("zetherion_ai.skills.agent_bootstrap.VercelClient", return_value=vercel_client),
        patch(
            "zetherion_ai.skills.agent_bootstrap.ClerkMetadataClient",
            return_value=clerk_client,
        ),
    ):
        vercel = await skill._collect_vercel_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            refs={"vercel_deployment_id": "dep_explicit"},
            request_context={"session_id": "sess-1", "run_id": "run-2"},
        )
        clerk = await skill._collect_clerk_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            refs={},
            request_context={"session_id": "sess-1"},
        )

    assert vercel["status"] == "succeeded"
    assert vercel["summary"]["deployment"]["uid"] == "dep_explicit"
    vercel_client.get_deployment.assert_awaited_once_with("dep_explicit", team_id="team-1")
    vercel_client.get_deployment_events.assert_awaited_once_with(
        "dep_explicit",
        team_id="team-1",
        limit=100,
    )
    assert clerk["status"] == "succeeded"
    assert clerk["summary"]["app_diagnostics"] == []
    skill._record_gap.assert_awaited_once()  # type: ignore[attr-defined]
    storage.record_operation_incident.assert_not_awaited()
    vercel_client.close.assert_awaited_once()
    clerk_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_service_evidence_covers_branch_match_fallback_and_sparse_success_paths() -> (
    None
):
    github_storage = MagicMock()
    github_storage.upsert_operation_ref = AsyncMock(return_value={"ref_id": "ref-1"})
    github_storage.record_operation_evidence = AsyncMock(
        side_effect=[
            {"evidence_id": "summary-gh"},
            {"evidence_id": "events-gh"},
        ]
    )
    github_storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})

    github_skill = _skill(github_storage)
    repository = SimpleNamespace(default_branch="main", to_dict=lambda: {"name": "cgs"})
    github_client = MagicMock()
    github_client.get_repository = AsyncMock(return_value=repository)
    github_client.list_workflow_runs = AsyncMock(return_value=[])
    github_client.get_workflow_run = AsyncMock(
        return_value={"id": 505, "name": "CI", "status": "completed", "conclusion": "failure"}
    )
    github_client.list_workflow_jobs = AsyncMock(
        return_value=[{"name": "unit", "conclusion": "success"}]
    )
    github_client.list_workflow_run_artifacts = AsyncMock(return_value=[])
    github_client.download_workflow_run_logs = AsyncMock(return_value={})
    github_client.close = AsyncMock()

    with patch("zetherion_ai.skills.agent_bootstrap.GitHubClient", return_value=github_client):
        github = await github_skill._collect_github_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-github",
            refs={"github_run_id": "505"},
            request_context={"session_id": "sess-1"},
        )

    assert github["status"] == "failed"
    assert (
        github_storage.record_operation_incident.await_args.kwargs["root_cause_summary"]
        == "GitHub Actions run CI failed."
    )
    github_client.close.assert_awaited_once()

    vercel_storage = MagicMock()
    vercel_storage.upsert_operation_ref = AsyncMock(return_value={"ref_id": "ref-1"})
    vercel_storage.record_operation_evidence = AsyncMock(
        side_effect=[
            {"evidence_id": "summary-vercel-1"},
            {"evidence_id": "events-vercel-1"},
            {"evidence_id": "logs-vercel-1"},
        ]
    )
    vercel_storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})

    vercel_skill = _skill(vercel_storage)
    vercel_skill._require_connector = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "secret_value": "vercel-secret",
            "metadata": {"team_id": "team-1", "project_name": "cgs"},
        }
    )

    branch_match_client = MagicMock()
    branch_match_client.get_project = AsyncMock(return_value={"id": "proj-1", "name": "cgs"})
    branch_match_client.list_deployments = AsyncMock(
        return_value=[
            {
                "uid": "dep-other",
                "readyState": "QUEUED",
                "meta": {"githubCommitRef": "preview"},
            },
            {
                "uid": "dep-release",
                "readyState": "READY",
                "meta": {"githubCommitRef": "release"},
            },
        ]
    )
    branch_match_client.list_domains = AsyncMock(return_value=[])
    branch_match_client.get_deployment_events = AsyncMock(
        return_value=[
            {"payload": {}},
            {"name": "Deployment finished"},
        ]
    )
    branch_match_client.close = AsyncMock()

    with patch(
        "zetherion_ai.skills.agent_bootstrap.VercelClient",
        return_value=branch_match_client,
    ):
        branch_match = await vercel_skill._collect_vercel_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-vercel-1",
            refs={"branch": "release"},
            request_context={"session_id": "sess-1"},
        )

    assert branch_match["status"] == "succeeded"
    assert branch_match["summary"]["deployment"]["uid"] == "dep-release"
    branch_match_client.get_deployment_events.assert_awaited_once_with(
        "dep-release",
        team_id="team-1",
        limit=100,
    )
    branch_match_client.close.assert_awaited_once()

    fallback_storage = MagicMock()
    fallback_storage.upsert_operation_ref = AsyncMock(return_value={"ref_id": "ref-1"})
    fallback_storage.record_operation_evidence = AsyncMock(
        return_value={"evidence_id": "summary-vercel-2"}
    )
    fallback_storage.record_operation_incident = AsyncMock(
        return_value={"incident_id": "incident-1"}
    )

    fallback_skill = _skill(fallback_storage)
    fallback_skill._require_connector = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "secret_value": "vercel-secret",
            "metadata": {"team_id": "team-1", "project_name": "cgs"},
        }
    )

    fallback_client = MagicMock()
    fallback_client.get_project = AsyncMock(return_value={"id": "proj-1", "name": "cgs"})
    fallback_client.list_deployments = AsyncMock(
        return_value=[
            {
                "readyState": "BUILDING",
                "meta": {},
            }
        ]
    )
    fallback_client.list_domains = AsyncMock(return_value=[])
    fallback_client.get_deployment_events = AsyncMock(return_value=[])
    fallback_client.close = AsyncMock()

    with patch("zetherion_ai.skills.agent_bootstrap.VercelClient", return_value=fallback_client):
        fallback = await fallback_skill._collect_vercel_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-vercel-2",
            refs={},
            request_context={"session_id": "sess-1"},
        )

    assert fallback["status"] == "active"
    assert fallback["summary"]["deployment"]["readyState"] == "BUILDING"
    fallback_storage.upsert_operation_ref.assert_not_awaited()
    fallback_client.get_deployment_events.assert_not_awaited()
    fallback_storage.record_operation_incident.assert_not_awaited()
    fallback_client.close.assert_awaited_once()

    clerk_storage = MagicMock()
    clerk_storage.record_operation_evidence = AsyncMock(
        side_effect=[
            {"evidence_id": "summary-clerk"},
            {"evidence_id": "logs-clerk"},
        ]
    )
    clerk_storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})
    clerk_storage.get_run_log_chunks = AsyncMock(
        side_effect=[
            [{"message": "auth warmup completed"}],
            [],
        ]
    )

    clerk_skill = _skill(clerk_storage)
    clerk_skill._require_connector = AsyncMock(  # type: ignore[method-assign]
        return_value={"metadata": {}}
    )

    clerk_client = MagicMock()
    clerk_client.get_openid_configuration = AsyncMock()
    clerk_client.get_jwks = AsyncMock()
    clerk_client.close = AsyncMock()

    with patch(
        "zetherion_ai.skills.agent_bootstrap.ClerkMetadataClient",
        return_value=clerk_client,
    ):
        clerk = await clerk_skill._collect_clerk_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-clerk",
            refs={},
            request_context={"session_id": "sess-1", "run_id": "run-3"},
        )

    assert clerk["status"] == "active"
    assert clerk["summary"]["app_diagnostics"] == [{"message": "auth warmup completed"}]
    clerk_client.get_openid_configuration.assert_not_awaited()
    clerk_client.get_jwks.assert_not_awaited()
    clerk_skill._record_gap.assert_not_awaited()  # type: ignore[attr-defined]
    clerk_storage.record_operation_incident.assert_not_awaited()
    clerk_client.close.assert_awaited_once()

    stripe_storage = MagicMock()
    stripe_storage.upsert_operation_ref = AsyncMock(return_value={"ref_id": "ref-1"})
    stripe_storage.record_operation_evidence = AsyncMock(
        return_value={"evidence_id": "summary-stripe"}
    )
    stripe_storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})

    stripe_skill = _skill(stripe_storage)
    stripe_skill._require_connector = AsyncMock(  # type: ignore[method-assign]
        return_value={"secret_value": "stripe-secret"}
    )

    stripe_client = MagicMock()
    stripe_client.get_account = AsyncMock(return_value={"id": "acct_2"})
    stripe_client.get_event = AsyncMock()
    stripe_client.get_customer = AsyncMock(return_value={"id": "cus_1"})
    stripe_client.get_subscription = AsyncMock(return_value={"id": "sub_1"})
    stripe_client.close = AsyncMock()

    with patch("zetherion_ai.skills.agent_bootstrap.StripeClient", return_value=stripe_client):
        stripe = await stripe_skill._collect_stripe_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-stripe",
            refs={"customer_id": "cus_1", "subscription_id": "sub_1"},
            request_context={"session_id": "sess-1"},
        )

    assert stripe["status"] == "succeeded"
    stripe_client.get_event.assert_not_awaited()
    assert stripe_storage.record_operation_evidence.await_count == 1
    stripe_storage.record_operation_incident.assert_not_awaited()
    stripe_skill._record_gap.assert_not_awaited()  # type: ignore[attr-defined]
    stripe_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_github_operation_evidence_returns_empty_without_configured_repo() -> None:
    storage = _storage()
    skill = _skill(storage)
    app_profile = _app_profile()
    app_profile["profile"]["github_repos"] = []

    result = await skill._collect_github_operation_evidence(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="codex-1",
        app_id="catalyst-group-solutions",
        app_profile=app_profile,
        operation_id="op-1",
        refs={},
        request_context={"session_id": "sess-1"},
    )

    assert result == {}
    skill._require_github_connector.assert_not_awaited()  # type: ignore[attr-defined]
    storage.record_operation_evidence.assert_not_awaited()
    storage.upsert_operation_ref.assert_not_awaited()


@pytest.mark.asyncio
async def test_collect_github_operation_evidence_uses_pr_head_and_latest_run_fallback() -> None:
    storage = MagicMock()
    storage.upsert_operation_ref = AsyncMock(return_value={"ref_id": "ref-1"})
    storage.record_operation_evidence = AsyncMock(
        side_effect=[
            {"evidence_id": "summary-1"},
            {"evidence_id": "events-1"},
        ]
    )
    storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})

    skill = _skill(storage)

    repository = SimpleNamespace(default_branch="", to_dict=lambda: {"name": "cgs"})
    pull_request = SimpleNamespace(
        to_dict=lambda: {"number": 7, "head": {"ref": "feature/offline-coverage"}}
    )
    github_client = MagicMock()
    github_client.get_repository = AsyncMock(return_value=repository)
    github_client.get_pull_request = AsyncMock(return_value=pull_request)
    github_client.list_workflow_runs = AsyncMock(
        return_value=[SimpleNamespace(id=202, head_sha="feedface")]
    )
    github_client.get_workflow_run = AsyncMock(
        return_value={"id": 202, "name": "CI", "status": "in_progress", "conclusion": "success"}
    )
    github_client.list_workflow_jobs = AsyncMock(return_value=[])
    github_client.list_workflow_run_artifacts = AsyncMock(return_value=[])
    github_client.download_workflow_run_logs = AsyncMock(return_value={})
    github_client.close = AsyncMock()

    with patch("zetherion_ai.skills.agent_bootstrap.GitHubClient", return_value=github_client):
        result = await skill._collect_github_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            refs={"pr_number": "7"},
            request_context={"session_id": "sess-1"},
        )

    assert result["status"] == "active"
    assert result["summary"]["workflow_run"]["id"] == 202
    ref_kinds = {call.kwargs["ref_kind"] for call in storage.upsert_operation_ref.await_args_list}
    assert ref_kinds == {"github_run_id", "branch", "pr_number"}
    storage.record_operation_incident.assert_not_awaited()
    github_client.get_pull_request.assert_awaited_once_with(
        "jimtin",
        "catalyst-group-solutions",
        7,
    )
    github_client.get_workflow_run.assert_awaited_once_with(
        "jimtin",
        "catalyst-group-solutions",
        202,
    )
    github_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_github_operation_evidence_uses_explicit_run_id_and_success_logs() -> None:
    storage = MagicMock()
    storage.upsert_operation_ref = AsyncMock(return_value={"ref_id": "ref-1"})
    storage.record_operation_evidence = AsyncMock(
        side_effect=[
            {"evidence_id": "summary-1"},
            {"evidence_id": "events-1"},
            {"evidence_id": "logs-1"},
        ]
    )
    storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})

    skill = _skill(storage)

    repository = SimpleNamespace(default_branch="", to_dict=lambda: {"name": "cgs"})
    github_client = MagicMock()
    github_client.get_repository = AsyncMock(return_value=repository)
    github_client.list_workflow_runs = AsyncMock(return_value=[])
    github_client.get_workflow_run = AsyncMock(
        return_value={"id": 303, "name": "CI", "status": "completed", "conclusion": "success"}
    )
    github_client.list_workflow_jobs = AsyncMock(
        return_value=[{"name": "unit", "conclusion": "success"}]
    )
    github_client.list_workflow_run_artifacts = AsyncMock(return_value=[])
    github_client.download_workflow_run_logs = AsyncMock(
        return_value={
            "combined_text": "line one\nline two",
            "entries": [{"message": "line one"}],
            "truncated": True,
        }
    )
    github_client.close = AsyncMock()

    with patch("zetherion_ai.skills.agent_bootstrap.GitHubClient", return_value=github_client):
        result = await skill._collect_github_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            refs={"github_run_id": "303"},
            request_context={"session_id": "sess-1"},
        )

    assert result["status"] == "succeeded"
    assert storage.record_operation_evidence.await_count == 3
    ref_kinds = [call.kwargs["ref_kind"] for call in storage.upsert_operation_ref.await_args_list]
    assert ref_kinds == ["github_run_id"]
    storage.record_operation_incident.assert_not_awaited()
    github_client.get_workflow_run.assert_awaited_once_with(
        "jimtin",
        "catalyst-group-solutions",
        303,
    )
    github_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_github_operation_evidence_stays_active_when_git_sha_has_no_run() -> None:
    storage = MagicMock()
    storage.upsert_operation_ref = AsyncMock(return_value={"ref_id": "ref-1"})
    storage.record_operation_evidence = AsyncMock(
        side_effect=[
            {"evidence_id": "summary-1"},
            {"evidence_id": "events-1"},
        ]
    )
    storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})

    skill = _skill(storage)

    repository = SimpleNamespace(default_branch="", to_dict=lambda: {"name": "cgs"})
    github_client = MagicMock()
    github_client.get_repository = AsyncMock(return_value=repository)
    github_client.list_workflow_runs = AsyncMock(
        return_value=[SimpleNamespace(id=404, head_sha="deadbeef")]
    )
    github_client.get_workflow_run = AsyncMock()
    github_client.list_workflow_jobs = AsyncMock(return_value=[])
    github_client.list_workflow_run_artifacts = AsyncMock(return_value=[])
    github_client.download_workflow_run_logs = AsyncMock(return_value={})
    github_client.close = AsyncMock()

    with patch("zetherion_ai.skills.agent_bootstrap.GitHubClient", return_value=github_client):
        result = await skill._collect_github_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            refs={"git_sha": "abc12345"},
            request_context={"session_id": "sess-1"},
        )

    assert result["status"] == "active"
    assert result["summary"]["workflow_run"] is None
    github_client.get_workflow_run.assert_not_awaited()
    ref_kinds = [call.kwargs["ref_kind"] for call in storage.upsert_operation_ref.await_args_list]
    assert ref_kinds == ["git_sha"]
    storage.record_operation_incident.assert_not_awaited()
    github_client.close.assert_awaited_once()


@pytest.mark.asyncio
async def test_collect_github_operation_evidence_stays_active_without_refs_or_workflow_runs() -> (
    None
):
    storage = MagicMock()
    storage.upsert_operation_ref = AsyncMock(return_value={"ref_id": "ref-1"})
    storage.record_operation_evidence = AsyncMock(
        side_effect=[
            {"evidence_id": "summary-1"},
            {"evidence_id": "events-1"},
        ]
    )
    storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})

    skill = _skill(storage)

    repository = SimpleNamespace(default_branch="", to_dict=lambda: {"name": "cgs"})
    github_client = MagicMock()
    github_client.get_repository = AsyncMock(return_value=repository)
    github_client.list_workflow_runs = AsyncMock(return_value=[])
    github_client.get_workflow_run = AsyncMock()
    github_client.list_workflow_jobs = AsyncMock(return_value=[])
    github_client.list_workflow_run_artifacts = AsyncMock(return_value=[])
    github_client.download_workflow_run_logs = AsyncMock(return_value={})
    github_client.close = AsyncMock()

    with patch("zetherion_ai.skills.agent_bootstrap.GitHubClient", return_value=github_client):
        result = await skill._collect_github_operation_evidence(  # noqa: SLF001
            owner_id="owner-1",
            principal_id="codex-1",
            app_id="catalyst-group-solutions",
            app_profile=_app_profile(),
            operation_id="op-1",
            refs={},
            request_context={"session_id": "sess-1"},
        )

    assert result["status"] == "active"
    assert result["summary"]["workflow_run"] is None
    storage.upsert_operation_ref.assert_not_awaited()
    storage.record_operation_incident.assert_not_awaited()
    github_client.get_workflow_run.assert_not_awaited()
    github_client.close.assert_awaited_once()
