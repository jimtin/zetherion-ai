"""Unit tests for the brokered agent bootstrap skill."""

from __future__ import annotations

import base64
import io
import json
import tarfile
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

import zetherion_ai.owner_ci.system_validation as system_validation_module
import zetherion_ai.skills.agent_bootstrap as agent_bootstrap_module
from zetherion_ai.skills.agent_bootstrap import AgentBootstrapSkill
from zetherion_ai.skills.base import SkillRequest


def _storage() -> MagicMock:
    storage = MagicMock()
    storage.upsert_agent_docs_manifest = AsyncMock()
    storage.list_agent_docs_manifests = AsyncMock(return_value=[])
    storage.get_agent_docs_manifest = AsyncMock()
    storage.store_agent_bootstrap_manifest = AsyncMock()
    storage.store_agent_setup_receipt = AsyncMock()
    storage.get_agent_bootstrap_manifest = AsyncMock()
    storage.get_agent_principal = AsyncMock(return_value=None)
    storage.upsert_agent_principal = AsyncMock()
    storage.create_agent_session = AsyncMock(
        return_value={"session_id": "sess-1", "principal_id": "codex-1"}
    )
    storage.list_agent_app_profiles = AsyncMock(return_value=[])
    storage.get_agent_app_profile = AsyncMock(return_value=None)
    storage.find_agent_app_profile = AsyncMock(return_value=None)
    storage.upsert_agent_app_profile = AsyncMock()
    storage.get_agent_knowledge_pack = AsyncMock(return_value=None)
    storage.upsert_agent_knowledge_pack = AsyncMock()
    storage.upsert_service_adapter_capability = AsyncMock()
    storage.list_external_access_grants = AsyncMock(return_value=[])
    storage.record_agent_audit_event = AsyncMock(return_value={"audit_id": "audit-1"})
    storage.get_repo_profile = AsyncMock()
    storage.create_workspace_bundle = AsyncMock()
    storage.mark_workspace_bundle_downloaded = AsyncMock()
    storage.get_workspace_bundle = AsyncMock()
    storage.create_workspace_upload = AsyncMock()
    storage.get_workspace_upload = AsyncMock(return_value=None)
    storage.create_execution_candidate = AsyncMock()
    storage.get_execution_candidate = AsyncMock(return_value=None)
    storage.create_compiled_plan = AsyncMock()
    storage.create_publish_candidate = AsyncMock()
    storage.list_agent_principals = AsyncMock(return_value=[])
    storage.upsert_external_service_connector = AsyncMock()
    storage.list_external_service_connectors = AsyncMock(return_value=[])
    storage.get_external_service_connector = AsyncMock(return_value=None)
    storage.get_external_service_connector_with_secret = AsyncMock(return_value=None)
    storage.replace_external_access_grants = AsyncMock(return_value=[])
    storage.list_agent_audit_events = AsyncMock(return_value=[])
    storage.create_agent_interaction = AsyncMock(return_value={"interaction_id": "interaction-1"})
    storage.create_agent_action = AsyncMock(return_value={"action_record_id": "action-1"})
    storage.create_agent_outcome = AsyncMock(return_value={"outcome_id": "outcome-1"})
    storage.record_agent_gap_event = AsyncMock(
        return_value={"gap_id": "gap-1", "occurrence_count": 1}
    )
    storage.list_agent_gap_events = AsyncMock(return_value=[])
    storage.get_agent_gap_event = AsyncMock(return_value=None)
    storage.update_agent_gap_event = AsyncMock(return_value=None)
    storage.create_agent_service_request = AsyncMock(
        return_value={"request_id": "service-1", "status": "executed"}
    )
    storage.list_secret_refs = AsyncMock(return_value=[])
    storage.get_secret_ref = AsyncMock(return_value=None)
    storage.get_secret_ref_value = AsyncMock(return_value=None)
    storage.upsert_secret_ref = AsyncMock()
    storage.list_agent_session_interactions = AsyncMock(return_value=[])
    storage.find_managed_operation_by_ref = AsyncMock(return_value=None)
    storage.create_managed_operation = AsyncMock(
        return_value={
            "operation_id": "op-1",
            "app_id": "catalyst-group-solutions",
            "repo_id": "catalyst-group-solutions",
            "summary": {},
            "metadata": {},
        }
    )
    storage.update_managed_operation = AsyncMock()
    storage.upsert_operation_ref = AsyncMock()
    storage.get_operation_hydrated = AsyncMock(
        return_value={"operation_id": "op-1", "refs": [], "incidents": [], "evidence": []}
    )
    storage.get_managed_operation = AsyncMock(return_value=None)
    storage.list_managed_operations = AsyncMock(return_value=[])
    storage.list_operation_evidence = AsyncMock(return_value=[])
    storage.get_operation_log_chunks = AsyncMock(return_value=[])
    storage.list_operation_incidents = AsyncMock(return_value=[])
    storage.list_operation_refs = AsyncMock(return_value=[])
    storage.record_operation_evidence = AsyncMock(return_value={"evidence_id": "evidence-1"})
    storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})
    storage.get_run = AsyncMock(return_value=None)
    storage.get_run_events = AsyncMock(return_value=[])
    storage.get_run_log_chunks = AsyncMock(return_value=[])
    storage.get_run_debug_bundle = AsyncMock(return_value=None)
    storage.create_system_run = AsyncMock()
    storage.list_system_runs = AsyncMock(return_value=[])
    storage.get_system_run = AsyncMock(return_value=None)
    storage.update_system_run = AsyncMock()
    storage.refresh_system_run_report = AsyncMock(return_value=None)
    storage.get_system_run_report = AsyncMock(return_value=None)
    storage.get_system_run_graph = AsyncMock(return_value=None)
    storage.get_system_run_correlation_context = AsyncMock(return_value=None)
    storage.get_system_run_diagnostics = AsyncMock(return_value=None)
    storage.get_system_run_artifacts = AsyncMock(return_value=[])
    storage.get_system_run_evidence = AsyncMock(return_value=[])
    storage.get_system_run_coaching = AsyncMock(return_value=[])
    storage.get_system_run_readiness = AsyncMock(return_value=None)
    storage.get_system_run_usage = AsyncMock(return_value=None)
    return storage


def _app_profile(app_id: str = "catalyst-group-solutions") -> dict[str, Any]:
    return {
        "app_id": app_id,
        "profile": {
            "repo_ids": [app_id],
            "docs_slugs": ["cgs-ai-api-quickstart"],
            "service_connector_map": {
                "github": {
                    "connector_id": "github-primary",
                    "read_access": ["branch_metadata", "diff_compare", "pr_metadata"],
                    "write_access": [],
                    "broker_only": True,
                }
            },
            "github_governance": {
                "write_principal": "zetherion",
                "agent_push_enabled": False,
            },
        },
    }


@pytest.mark.asyncio
async def test_agent_session_create_bootstraps_principal_and_filters_apps() -> None:
    storage = _storage()
    storage.upsert_agent_principal.return_value = {
        "principal_id": "codex-1",
        "display_name": "Codex 1",
        "allowed_scopes": ["cgs:agent"],
    }
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.list_external_access_grants.return_value = [
        {
            "resource_type": "app",
            "resource_id": "catalyst-group-solutions",
            "active": True,
        }
    ]
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    response = await skill.handle(
        SkillRequest(
            intent="agent_session_create",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "display_name": "Codex 1",
                "public_base_url": "https://cgs.example.com",
            },
        )
    )

    assert response.success is True
    session = response.data["session"]
    assert session["principal"]["principal_id"] == "codex-1"
    assert len(session["accessible_apps"]) == 1
    storage.record_agent_audit_event.assert_awaited_once()


@pytest.mark.asyncio
async def test_agent_app_manifest_get_includes_pack_and_docs() -> None:
    storage = _storage()
    storage.get_agent_app_profile.return_value = _app_profile()
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.list_external_access_grants.return_value = [
        {
            "resource_type": "app",
            "resource_id": "catalyst-group-solutions",
            "active": True,
        }
    ]
    storage.get_agent_knowledge_pack.return_value = {
        "app_id": "catalyst-group-solutions",
        "pack": {"workspace_manifest": {"repo_id": "catalyst-group-solutions"}},
    }
    storage.get_agent_docs_manifest.return_value = {
        "slug": "cgs-ai-api-quickstart",
        "manifest": {"content_markdown": "# Quickstart"},
    }
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    response = await skill.handle(
        SkillRequest(
            intent="agent_app_manifest_get",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
            },
        )
    )

    assert response.success is True
    assert response.data["app"]["app_id"] == "catalyst-group-solutions"
    assert response.data["knowledge_pack"]["pack"]["workspace_manifest"]["repo_id"] == (
        "catalyst-group-solutions"
    )
    assert response.data["docs"][0]["slug"] == "cgs-ai-api-quickstart"
    assert response.data["services"][0]["service_kind"] == "github"


@pytest.mark.asyncio
async def test_agent_app_services_list_returns_catalog() -> None:
    storage = _storage()
    storage.get_agent_app_profile.return_value = _app_profile()
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.list_external_access_grants.return_value = [
        {
            "resource_type": "app",
            "resource_id": "catalyst-group-solutions",
            "active": True,
        }
    ]
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    response = await skill.handle(
        SkillRequest(
            intent="agent_app_services_list",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
                "public_base_url": "https://cgs.example.com",
            },
        )
    )

    assert response.success is True
    assert response.data["services"][0]["service_kind"] == "github"
    assert response.data["services"][0]["routes"]["read"].endswith("/services/github")


@pytest.mark.asyncio
async def test_agent_app_adoption_handlers_return_gaps_readiness_and_coaching() -> None:
    storage = _storage()
    storage.get_agent_app_profile.return_value = _app_profile()
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.list_external_access_grants.return_value = [
        {
            "resource_type": "app",
            "resource_id": "catalyst-group-solutions",
            "active": True,
        }
    ]
    storage.list_external_service_connectors.return_value = []
    storage.list_agent_gap_events.return_value = [
        {"gap_id": "gap-auth-1", "blocker": True, "summary": "Missing auth verification"}
    ]
    storage.list_agent_coaching_feedback = AsyncMock(return_value=[])
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    coaching = await skill.handle(
        SkillRequest(
            intent="agent_app_coaching_get",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
            },
        )
    )
    gaps = await skill.handle(
        SkillRequest(
            intent="agent_app_integration_gaps_get",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
            },
        )
    )
    readiness = await skill.handle(
        SkillRequest(
            intent="agent_app_rollout_readiness_get",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
            },
        )
    )

    assert coaching.success is True
    assert coaching.data["coaching"][0]["scope"] == "app"
    assert "AGENTS.md" in coaching.data["coaching"][0]["recommendations"][0]["title"]
    assert gaps.success is True
    gap_types = {gap["gap_type"] for gap in gaps.data["integration_gaps"]}
    assert "missing_connector_record" in gap_types
    assert "missing_runtime_policy" in gap_types
    assert "missing_agent_profiles" in gap_types
    assert readiness.success is True
    assert readiness.data["rollout_readiness"]["status"] == "blocked"
    assert readiness.data["rollout_readiness"]["blocker_count"] >= 1
    assert readiness.data["rollout_readiness"]["metadata"]["open_recorded_gap_total"] == 1


@pytest.mark.asyncio
async def test_agent_app_coaching_attaches_synthesized_guidance_when_available() -> None:
    storage = _storage()
    storage.get_agent_app_profile.return_value = _app_profile()
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.list_external_access_grants.return_value = [
        {
            "resource_type": "app",
            "resource_id": "catalyst-group-solutions",
            "active": True,
        }
    ]
    storage.list_external_service_connectors.return_value = []
    storage.list_agent_gap_events.return_value = []
    storage.list_agent_coaching_feedback = AsyncMock(
        return_value=[{"feedback_id": "coach-1", "scope": "app"}]
    )
    synthesizer = MagicMock()
    synthesizer.synthesize_many = AsyncMock(
        return_value=[
            {
                "feedback_id": "coach-1",
                "scope": "app",
                "synthesized_guidance": {
                    "status": "synthesized",
                    "summary": "Stabilize onboarding docs first.",
                },
            }
        ]
    )
    skill = AgentBootstrapSkill(storage=storage, coaching_synthesizer=synthesizer)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    coaching = await skill.handle(
        SkillRequest(
            intent="agent_app_coaching_get",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
            },
        )
    )

    assert coaching.success is True
    assert coaching.data["coaching"][0]["synthesized_guidance"]["status"] == "synthesized"
    synthesizer.synthesize_many.assert_awaited_once()


def test_app_profile_helpers_normalize_repo_policy_and_agent_profiles() -> None:
    skill = AgentBootstrapSkill(storage=_storage())

    profile_with_repo = {
        "app_id": "sample-app",
        "profile": {
            "repo_ids": ["sample-repo", "fallback-repo"],
            "ai_runtime_policy": {
                "allowed_providers": ["Groq", "", "groq", "openai"],
                "allowed_models": ["gpt-oss-120b", "", "gpt-oss-120b"],
            },
            "ai_agent_profiles": [
                {"agent_profile_id": "planner"},
                "ignore-me",
            ],
        },
    }

    assert skill._app_repo_id(profile_with_repo) == "sample-repo"  # noqa: SLF001
    assert skill._app_repo_id({"app_id": "sample-app", "profile": {}}) == "sample-app"  # noqa: SLF001
    assert skill._app_runtime_policy({"app_id": "sample-app", "profile": {}}) == {}  # noqa: SLF001
    assert skill._app_runtime_policy(profile_with_repo) == {  # noqa: SLF001
        "allowed_providers": ["groq", "openai"],
        "allowed_models": ["gpt-oss-120b"],
    }
    assert skill._app_agent_profiles(profile_with_repo) == [  # noqa: SLF001
        {"agent_profile_id": "planner"}
    ]


@pytest.mark.asyncio
async def test_build_app_integration_gaps_covers_connector_health_variants() -> None:
    storage = _storage()
    storage.list_external_service_connectors.return_value = [
        {
            "connector_id": "stripe-primary",
            "service_kind": "stripe",
            "auth_kind": "token",
            "has_secret": False,
            "active": False,
            "metadata": {},
        },
        {
            "connector_id": "vercel-primary",
            "service_kind": "vercel",
            "auth_kind": "token",
            "has_secret": True,
            "active": True,
            "metadata": {},
        },
    ]
    skill = AgentBootstrapSkill(storage=storage)

    gaps = await skill._build_app_integration_gaps(  # noqa: SLF001
        owner_id="owner-1",
        app_profile={
            "app_id": "sample-app",
            "profile": {
                "repo_ids": ["sample-app"],
                "capability_registry": {
                    "required_connectors": ["github", "clerk", "stripe", "vercel"]
                },
                "service_connector_map": {
                    "github": {},
                    "clerk": {"read_access": ["instance_metadata"]},
                    "stripe": {"connector_id": "stripe-primary"},
                    "vercel": {"connector_id": "vercel-primary"},
                },
                "ai_runtime_policy": {
                    "allowed_providers": [],
                    "allowed_models": [],
                },
                "ai_agent_profiles": [],
            },
        },
    )

    gap_types = {gap["gap_type"] for gap in gaps}
    assert "missing_connector_binding" in gap_types
    assert "missing_connector_id" in gap_types
    assert "connector_blocked" in gap_types
    assert "connector_degraded" in gap_types
    assert "missing_allowed_providers" in gap_types
    assert "missing_allowed_models" in gap_types
    assert "missing_agent_profiles" in gap_types


@pytest.mark.asyncio
async def test_build_app_integration_gaps_uses_default_repo_connectors() -> None:
    storage = _storage()
    storage.list_external_service_connectors.return_value = []
    skill = AgentBootstrapSkill(storage=storage)

    gaps = await skill._build_app_integration_gaps(  # noqa: SLF001
        owner_id="owner-1",
        app_profile={
            "app_id": "catalyst-group-solutions",
            "profile": {
                "repo_ids": ["catalyst-group-solutions"],
                "service_connector_map": {
                    "github": {"connector_id": "github-primary"},
                },
                "ai_runtime_policy": {
                    "allowed_providers": ["groq"],
                    "allowed_models": ["gpt-oss-120b"],
                },
                "ai_agent_profiles": [{"agent_profile_id": "planner"}],
            },
        },
    )

    gap_types = {gap["gap_type"] for gap in gaps}
    assert "missing_connector_record" in gap_types
    assert "missing_connector_binding" in gap_types


def test_build_rollout_steps_returns_ready_step_when_no_gaps() -> None:
    skill = AgentBootstrapSkill(storage=_storage())

    steps = skill._build_rollout_steps(app_id="sample-app", gaps=[])  # noqa: SLF001

    assert steps == [
        {
            "step_id": "sample-app:ready",
            "title": "Ready for the next rollout checkpoint",
            "instructions": [
                "Use the current runtime policy and connector set to onboard "
                "the next agent or integration client."
            ],
            "blocking": False,
            "metadata": {},
        }
    ]


@pytest.mark.asyncio
async def test_app_handlers_require_app_id() -> None:
    skill = AgentBootstrapSkill(storage=_storage())

    coaching = await skill.handle(
        SkillRequest(
            intent="agent_app_coaching_get",
            user_id="owner-1",
            context={"owner_id": "owner-1"},
        )
    )
    gaps = await skill.handle(
        SkillRequest(
            intent="agent_app_integration_gaps_get",
            user_id="owner-1",
            context={"owner_id": "owner-1"},
        )
    )
    readiness = await skill.handle(
        SkillRequest(
            intent="agent_app_rollout_readiness_get",
            user_id="owner-1",
            context={"owner_id": "owner-1"},
        )
    )

    assert coaching.success is False
    assert coaching.error == "app_id is required"
    assert gaps.success is False
    assert gaps.error == "app_id is required"
    assert readiness.success is False
    assert readiness.error == "app_id is required"


def test_system_candidate_set_from_request_supports_candidate_set_and_fallbacks() -> None:
    skill = AgentBootstrapSkill(storage=_storage())

    explicit = skill._system_candidate_set_from_request(  # noqa: SLF001
        SkillRequest(
            intent="agent_system_run_plan_get",
            user_id="owner-1",
            context={
                "candidate_set": {
                    "system_id": "custom-system",
                    "mode_id": "combined_system",
                    "repos": [{"repo_id": "zetherion-ai", "git_ref": "feature/z"}],
                }
            },
        )
    )
    listed = skill._system_candidate_set_from_request(  # noqa: SLF001
        SkillRequest(
            intent="agent_system_run_plan_get",
            user_id="owner-1",
            context={
                "repos": [
                    "ignore-me",
                    {"repo_id": "zetherion-ai", "git_ref": "feature/z"},
                ]
            },
        )
    )
    indexed = skill._system_candidate_set_from_request(  # noqa: SLF001
        SkillRequest(
            intent="agent_system_run_plan_get",
            user_id="owner-1",
            context={
                "repo_1_git_ref": "feature/z",
                "repo_2_commit_sha": "abc1234",
                "metadata": "not-a-dict",
            },
        )
    )

    assert explicit["system_id"] == "custom-system"
    assert listed["repos"] == [{"repo_id": "zetherion-ai", "git_ref": "feature/z"}]
    assert indexed["repos"] == [
        {"repo_id": "zetherion-ai", "git_ref": "feature/z"},
        {
            "repo_id": "catalyst-group-solutions",
            "git_ref": "HEAD",
            "commit_sha": "abc1234",
        },
    ]
    assert indexed["metadata"] == {}


def test_build_workspace_bundle_embeds_inline_archive(tmp_path: Path) -> None:
    repo_root = tmp_path / "sample-repo"
    repo_root.mkdir()
    (repo_root / "README.md").write_text("# Sample\n", encoding="utf-8")
    (repo_root / "src").mkdir()
    (repo_root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")

    skill = AgentBootstrapSkill(storage=_storage())
    repo = {
        "repo_id": "sample-repo",
        "allowed_paths": [str(repo_root)],
    }

    bundle, resolved_ref = skill._build_workspace_bundle(  # noqa: SLF001
        repo=repo,
        knowledge_pack={"workspace_manifest": {"repo_id": "sample-repo"}},
        git_ref="main",
    )

    assert resolved_ref in {None, "main"}
    assert bundle["download_mode"] == "inline_base64"
    archive_bytes = base64.b64decode(bundle["archive_base64"])
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        names = archive.getnames()
    assert "sample-repo/README.md" in names
    assert "sample-repo/src/main.py" in names


@pytest.mark.asyncio
async def test_publish_candidate_submit_stores_diff_without_github_write_access() -> None:
    storage = _storage()
    storage.get_agent_app_profile.return_value = _app_profile()
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.list_external_access_grants.return_value = [
        {
            "resource_type": "app",
            "resource_id": "catalyst-group-solutions",
            "active": True,
        }
    ]
    storage.create_publish_candidate.return_value = {
        "candidate_id": "cand-1",
        "status": "submitted",
    }
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    response = await skill.handle(
        SkillRequest(
            intent="agent_publish_candidate_submit",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
                "base_sha": "abc1234",
                "diff_text": "diff --git a/file b/file",
                "summary": "Update feature gate",
            },
        )
    )

    assert response.success is True
    storage.create_publish_candidate.assert_awaited_once()
    candidate_payload = storage.create_publish_candidate.await_args.kwargs["candidate"]
    assert candidate_payload["candidate_type"] == "text/x-diff"
    assert candidate_payload["github_governance"]["agent_push_enabled"] is False
    assert response.data["operation"]["operation_id"] == "op-1"


@pytest.mark.asyncio
async def test_operation_resolve_creates_and_refreshes_operation() -> None:
    storage = _storage()
    storage.get_agent_app_profile.return_value = _app_profile()
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.list_external_access_grants.return_value = [
        {
            "resource_type": "app",
            "resource_id": "catalyst-group-solutions",
            "active": True,
        }
    ]
    storage.get_operation_hydrated.return_value = {
        "operation_id": "op-1",
        "app_id": "catalyst-group-solutions",
        "repo_id": "catalyst-group-solutions",
        "refs": [{"ref_kind": "git_sha", "ref_value": "abc1234"}],
    }
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]
    skill._refresh_operation = AsyncMock()  # type: ignore[method-assign]

    response = await skill.handle(
        SkillRequest(
            intent="agent_operation_resolve",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
                "git_sha": "abc1234",
            },
        )
    )

    assert response.success is True
    assert response.data["operation"]["operation_id"] == "op-1"
    skill._refresh_operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_operation_event_ingest_records_provider_event_and_refreshes() -> None:
    storage = _storage()
    storage.get_agent_app_profile.return_value = _app_profile()
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.get_operation_hydrated.return_value = {
        "operation_id": "op-1",
        "app_id": "catalyst-group-solutions",
        "repo_id": "catalyst-group-solutions",
        "refs": [{"ref_kind": "github_run_id", "ref_value": "12345"}],
        "evidence": [{"evidence_id": "evidence-1"}],
        "incidents": [],
    }
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]
    skill._refresh_operation = AsyncMock()  # type: ignore[method-assign]

    response = await skill.handle(
        SkillRequest(
            intent="agent_operation_event_ingest",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "system:webhook:github",
                "app_id": "catalyst-group-solutions",
                "service_kind": "github",
                "event_type": "workflow_run",
                "delivery_id": "delivery-1",
                "event_payload": {
                    "workflow_run": {
                        "id": 12345,
                        "head_sha": "abc1234def5678",
                        "head_branch": "main",
                        "conclusion": "failure",
                    }
                },
            },
        )
    )

    assert response.success is True
    assert response.data["operation"]["operation_id"] == "op-1"
    storage.record_operation_evidence.assert_awaited()
    storage.record_operation_incident.assert_awaited()
    skill._refresh_operation.assert_awaited_once()


@pytest.mark.asyncio
async def test_test_plan_compile_records_gap_for_missing_playwright() -> None:
    storage = _storage()
    storage.get_agent_app_profile.return_value = _app_profile()
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.list_external_access_grants.return_value = [
        {
            "resource_type": "app",
            "resource_id": "catalyst-group-solutions",
            "active": True,
        }
    ]
    storage.get_repo_profile.return_value = {
        "repo_id": "catalyst-group-solutions",
        "default_branch": "main",
        "local_fast_lanes": [],
        "windows_full_lanes": [],
        "mandatory_static_gates": [],
        "shard_templates": [],
    }
    storage.get_agent_knowledge_pack.return_value = {
        "app_id": "catalyst-group-solutions",
        "pack": {
            "capability_registry": {
                "supported_tooling": ["jest", "eslint"],
            },
            "env_contract": {"required_secret_refs": []},
        },
    }
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    response = await skill.handle(
        SkillRequest(
            intent="agent_test_plan_compile",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
                "required_tooling": ["playwright"],
            },
        )
    )

    assert response.success is False
    storage.record_agent_gap_event.assert_awaited()


@pytest.mark.asyncio
async def test_service_request_submit_executes_brokered_stripe_action() -> None:
    storage = _storage()
    app_profile = _app_profile()
    app_profile["profile"]["service_connector_map"]["stripe"] = {
        "connector_id": "stripe-primary",
        "read_access": ["account_metadata"],
        "write_access": ["product_ensure"],
        "broker_only": True,
    }
    storage.get_agent_app_profile.return_value = app_profile
    storage.list_agent_app_profiles.return_value = [app_profile]
    storage.list_external_access_grants.return_value = [
        {
            "resource_type": "app",
            "resource_id": "catalyst-group-solutions",
            "active": True,
        }
    ]
    storage.get_external_service_connector_with_secret.return_value = {
        "connector_id": "stripe-primary",
        "service_kind": "stripe",
        "active": True,
        "secret_value": "sk_test_123",
    }
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]
    skill._execute_stripe_service_action = AsyncMock(  # type: ignore[method-assign]
        return_value={"status": "executed", "product": {"id": "prod_123"}}
    )

    response = await skill.handle(
        SkillRequest(
            intent="agent_service_request_submit",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
                "service_kind": "stripe",
                "action_id": "product.ensure",
                "input": {"name": "Gold"},
            },
        )
    )

    assert response.success is True
    assert response.data["request"]["request_id"] == "service-1"
    storage.create_agent_service_request.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_read_delegates_to_service_broker() -> None:
    storage = _storage()
    storage.get_agent_app_profile.return_value = _app_profile()
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.list_external_access_grants.return_value = [
        {
            "resource_type": "app",
            "resource_id": "catalyst-group-solutions",
            "active": True,
        }
    ]
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]
    skill._read_service_view = AsyncMock(  # type: ignore[method-assign]
        return_value={"service_kind": "github", "view": "overview"}
    )

    response = await skill.handle(
        SkillRequest(
            intent="agent_service_read",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
                "service_kind": "github",
                "view": "overview",
                "public_base_url": "https://cgs.example.com",
            },
        )
    )

    assert response.success is True
    assert response.data["service_kind"] == "github"
    skill._read_service_view.assert_awaited_once()


@pytest.mark.asyncio
async def test_repo_discover_delegates_to_github_broker() -> None:
    storage = _storage()
    storage.get_agent_principal.return_value = {
        "principal_id": "codex-1",
        "allowed_scopes": ["cgs:agent"],
    }
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]
    skill._discover_github_repositories = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"full_name": "jimtin/private-app", "private": True}]
    )

    response = await skill.handle(
        SkillRequest(
            intent="agent_repo_discover",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "allowed_scopes": ["cgs:agent"],
            },
        )
    )

    assert response.success is True
    assert response.data["repositories"][0]["full_name"] == "jimtin/private-app"


@pytest.mark.asyncio
async def test_repo_enroll_returns_managed_records() -> None:
    storage = _storage()
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]
    skill._enroll_github_repository = AsyncMock(  # type: ignore[method-assign]
        return_value={"repo_profile": {"repo_id": "private-app"}}
    )

    response = await skill.handle(
        SkillRequest(
            intent="agent_repo_enroll",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "github_repo": "jimtin/private-app",
                "stack_kind": "generic",
            },
        )
    )

    assert response.success is True
    assert response.data["repo_profile"]["repo_id"] == "private-app"


@pytest.mark.asyncio
async def test_publish_candidate_apply_delegates_to_controlled_apply_flow() -> None:
    storage = _storage()
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]
    skill._apply_publish_candidate = AsyncMock(  # type: ignore[method-assign]
        return_value={"candidate": {"candidate_id": "cand-1", "status": "github_pr_open"}}
    )

    response = await skill.handle(
        SkillRequest(
            intent="agent_publish_candidate_apply",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "candidate_id": "cand-1",
            },
        )
    )

    assert response.success is True
    assert response.data["candidate"]["status"] == "github_pr_open"


@pytest.mark.asyncio
async def test_system_validation_handlers_return_matrix_plan_readiness_and_coaching(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _storage()
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]
    cgs_manifest_path = tmp_path / "cgs-shard-manifest.json"
    cgs_manifest_path.write_text(
        json.dumps(
            {
                "repo_id": "catalyst-group-solutions",
                "validation_mode": "cgs_alone",
                "resource_limits": {"cpu": 4},
                "shards": [],
            }
        ),
        encoding="utf-8",
    )
    combined_manifest_path = tmp_path / "system-validation.json"
    combined_manifest_path.write_text(
        json.dumps(
            {
                "mode_id": "combined_system",
                "mode_label": "CGS + Zetherion together",
                "repo_ids": ["zetherion-ai", "catalyst-group-solutions"],
                "shards": [],
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        agent_bootstrap_module,
        "build_validation_matrix",
        lambda: system_validation_module.build_validation_matrix(
            cgs_manifest_path=cgs_manifest_path,
            combined_manifest_path=combined_manifest_path,
        ),
    )
    monkeypatch.setattr(
        agent_bootstrap_module,
        "build_system_run_plan",
        lambda *, candidate_set: system_validation_module.build_system_run_plan(
            candidate_set=candidate_set,
            cgs_manifest_path=cgs_manifest_path,
            combined_manifest_path=combined_manifest_path,
        ),
    )
    monkeypatch.setattr(
        agent_bootstrap_module,
        "build_system_rollout_readiness",
        lambda *, candidate_set: system_validation_module.build_system_rollout_readiness(
            candidate_set=candidate_set,
            cgs_manifest_path=cgs_manifest_path,
            combined_manifest_path=combined_manifest_path,
        ),
    )
    monkeypatch.setattr(
        agent_bootstrap_module,
        "build_system_coaching",
        lambda *, candidate_set, principal_id=None: system_validation_module.build_system_coaching(
            candidate_set=candidate_set,
            principal_id=principal_id,
            cgs_manifest_path=cgs_manifest_path,
            combined_manifest_path=combined_manifest_path,
        ),
    )

    candidate_repos = [
        {"repo_id": "zetherion-ai", "git_ref": "codex/owner-ci-platform-hardening"},
        {
            "repo_id": "catalyst-group-solutions",
            "git_ref": "codex/cgs-refinements-platform",
        },
    ]

    matrix = await skill.handle(
        SkillRequest(
            intent="agent_system_validation_matrix_get",
            user_id="owner-1",
            context={"owner_id": "owner-1"},
        )
    )
    plan = await skill.handle(
        SkillRequest(
            intent="agent_system_run_plan_get",
            user_id="owner-1",
            context={"owner_id": "owner-1", "repos": candidate_repos},
        )
    )
    readiness = await skill.handle(
        SkillRequest(
            intent="agent_system_rollout_readiness_get",
            user_id="owner-1",
            context={"owner_id": "owner-1", "repos": candidate_repos},
        )
    )
    coaching = await skill.handle(
        SkillRequest(
            intent="agent_system_coaching_get",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-agent-1",
                "repos": candidate_repos,
            },
        )
    )

    assert matrix.success is True
    assert {mode["mode_id"] for mode in matrix.data["validation_matrix"]["modes"]} >= {
        "zetherion_alone",
        "cgs_alone",
        "combined_system",
    }
    assert plan.success is True
    assert {profile["mode_id"] for profile in plan.data["system_run_plan"]["profiles"]} >= {
        "zetherion_alone",
        "cgs_alone",
        "combined_system",
    }
    assert readiness.success is True
    assert readiness.data["rollout_readiness"]["status"] == "ready"
    assert coaching.success is True
    assert coaching.data["coaching"][0]["scope"] == "system_run"
    assert "Combined-system validation" in coaching.data["coaching"][0]["summary"]


@pytest.mark.asyncio
async def test_system_coaching_blocks_when_candidate_set_is_incomplete() -> None:
    storage = _storage()
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    response = await skill.handle(
        SkillRequest(
            intent="agent_system_coaching_get",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-agent-1",
                "repos": [
                    {
                        "repo_id": "zetherion-ai",
                        "git_ref": "codex/owner-ci-platform-hardening",
                    }
                ],
            },
        )
    )

    assert response.success is True
    assert response.data["coaching"][0]["blocking"] is True
    assert response.data["coaching"][0]["findings"][0]["rule_code"] == (
        "missing_system_repo_candidates"
    )


@pytest.mark.asyncio
async def test_system_run_handlers_create_execute_and_retrieve_reports() -> None:
    storage = _storage()
    storage.create_system_run.return_value = {
        "system_run_id": "system-run-1",
        "system_id": "cgs-zetherion",
        "status": "planned",
        "candidate_set": {
            "repos": [
                {"repo_id": "zetherion-ai", "git_ref": "HEAD"},
                {"repo_id": "catalyst-group-solutions", "git_ref": "HEAD"},
            ]
        },
        "plan": {"shards": []},
        "readiness": {"blocking": False},
        "coaching": [],
        "metadata": {},
    }
    storage.get_system_run.side_effect = [
        {
            "system_run_id": "system-run-1",
            "system_id": "cgs-zetherion",
            "mode_id": "combined_system",
            "status": "planned",
            "candidate_set": {
                "system_id": "cgs-zetherion",
                "mode_id": "combined_system",
                "repos": [
                    {"repo_id": "zetherion-ai", "git_ref": "HEAD"},
                    {"repo_id": "catalyst-group-solutions", "git_ref": "HEAD"},
                ],
            },
            "plan": {"shards": []},
            "readiness": {
                "system_id": "cgs-zetherion",
                "mode_id": "combined_system",
                "blocking": False,
                "status": "ready",
                "summary": "Ready",
                "blocking_shards": [],
                "missing_repo_ids": [],
                "recommended_next_steps": [],
                "metadata": {},
            },
            "coaching": [],
            "execution": {},
            "metadata": {},
        },
        {
            "system_run_id": "system-run-1",
            "system_id": "cgs-zetherion",
            "mode_id": "combined_system",
            "status": "planned",
            "candidate_set": {
                "system_id": "cgs-zetherion",
                "mode_id": "combined_system",
                "repos": [
                    {"repo_id": "zetherion-ai", "git_ref": "HEAD"},
                    {"repo_id": "catalyst-group-solutions", "git_ref": "HEAD"},
                ],
            },
            "plan": {"shards": []},
            "readiness": {
                "system_id": "cgs-zetherion",
                "mode_id": "combined_system",
                "blocking": False,
                "status": "ready",
                "summary": "Ready",
                "blocking_shards": [],
                "missing_repo_ids": [],
                "recommended_next_steps": [],
                "metadata": {},
            },
            "coaching": [],
            "execution": {},
            "metadata": {},
        },
        {
            "system_run_id": "system-run-1",
            "system_id": "cgs-zetherion",
            "mode_id": "combined_system",
            "status": "succeeded",
            "candidate_set": {"repos": []},
            "plan": {"shards": []},
            "readiness": {"blocking": False},
            "coaching": [],
            "execution": {"all_passed": True, "batches": [], "shards": []},
            "metadata": {},
        },
    ]
    storage.update_system_run.return_value = {
        "system_run_id": "system-run-1",
        "status": "succeeded",
    }
    storage.refresh_system_run_report.return_value = {
        "system_run_id": "system-run-1",
        "run_graph": {"nodes": []},
    }
    storage.get_system_run_report.return_value = {
        "system_run_id": "system-run-1",
        "run_graph": {"nodes": [{"node_id": "system-run:system-run-1"}]},
    }
    storage.get_system_run_usage.return_value = {"billable_minutes": 0.0}

    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    create_response = await skill.handle(
        SkillRequest(
            intent="agent_system_run_create",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-agent-1",
                "repos": [
                    {"repo_id": "zetherion-ai", "git_ref": "HEAD"},
                    {"repo_id": "catalyst-group-solutions", "git_ref": "HEAD"},
                ],
            },
        )
    )
    execute_response = await skill.handle(
        SkillRequest(
            intent="agent_system_run_execute",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "system_run_id": "system-run-1",
            },
        )
    )
    report_response = await skill.handle(
        SkillRequest(
            intent="agent_system_run_report_get",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "system_run_id": "system-run-1",
            },
        )
    )
    usage_response = await skill.handle(
        SkillRequest(
            intent="agent_system_run_usage_get",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "system_run_id": "system-run-1",
            },
        )
    )

    assert create_response.success is True
    assert create_response.data["system_run"]["system_run_id"] == "system-run-1"
    assert execute_response.success is True
    assert execute_response.data["system_run"]["status"] == "succeeded"
    assert report_response.success is True
    assert report_response.data["report"]["system_run_id"] == "system-run-1"
    assert usage_response.success is True
    assert usage_response.data["usage_summary"]["billable_minutes"] == 0.0


def test_system_run_request_helpers_cover_candidate_shapes_and_errors() -> None:
    skill = AgentBootstrapSkill(storage=_storage())

    candidate_set = skill._system_candidate_set_from_request(  # noqa: SLF001
        SkillRequest(
            intent="agent_system_run_create",
            user_id="owner-1",
            context={
                "candidate_set": {
                    "system_id": "cgs-zetherion",
                    "mode_id": "combined_system",
                    "repos": [{"repo_id": "zetherion-ai", "git_ref": "HEAD"}],
                }
            },
        )
    )
    indexed = skill._system_candidate_set_from_request(  # noqa: SLF001
        SkillRequest(
            intent="agent_system_run_create",
            user_id="owner-1",
            context={
                "repo_1_git_ref": "feature/z",
                "repo_2_commit_sha": "abc123",
            },
        )
    )

    assert candidate_set["repos"][0]["repo_id"] == "zetherion-ai"
    assert indexed["repos"][0]["git_ref"] == "feature/z"
    assert indexed["repos"][1]["commit_sha"] == "abc123"
    assert skill._system_repo_root_for("zetherion-ai").exists()  # noqa: SLF001
    assert skill._system_command_parts(["bash", "-lc", "echo ok"]) == [  # noqa: SLF001
        "bash",
        "-lc",
        "echo ok",
    ]
    assert skill._system_command_parts("echo ok") == ["bash", "-lc", "echo ok"]  # noqa: SLF001

    with pytest.raises(ValueError, match="Unsupported system validation repo_id"):
        skill._system_repo_root_for("unknown-repo")  # noqa: SLF001
    with pytest.raises(ValueError, match="missing a command"):
        skill._system_command_parts([])  # noqa: SLF001


@pytest.mark.asyncio
async def test_system_run_execution_helpers_cover_failures_and_blocked_states(
    tmp_path: Path,
) -> None:
    storage = _storage()
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    async def _fake_run(
        command: list[str], *, cwd: Path, env: dict[str, str] | None = None, check: bool
    ) -> dict[str, Any]:
        if command[-1] == "exit 1":
            return {"returncode": 1, "stdout": "", "stderr": "boom"}
        return {"returncode": 0, "stdout": str(cwd), "stderr": ""}

    skill._run_command = AsyncMock(side_effect=_fake_run)  # type: ignore[method-assign]

    combined_shard = await skill._execute_system_run_shard(  # noqa: SLF001
        {
            "shard_id": "combined-contract",
            "validation_mode": "combined_system",
            "lane_family": "combined_system",
            "metadata": {
                "commands": [
                    {
                        "repo_id": "zetherion-ai",
                        "cwd": ".",
                        "command": ["bash", "-lc", "echo ok"],
                    },
                    {
                        "repo_id": "catalyst-group-solutions",
                        "cwd": ".",
                        "command": ["bash", "-lc", "exit 1"],
                    },
                ]
            },
        }
    )
    repo_shard = await skill._execute_system_run_shard(  # noqa: SLF001
        {
            "shard_id": "repo-unit",
            "lane_id": "repo-unit",
            "lane_label": "Repo unit",
            "validation_mode": "zetherion_alone",
            "lane_family": "unit",
            "repo_ids": ["zetherion-ai"],
            "metadata": {"command": ["bash", "-lc", "echo ok"]},
        }
    )

    assert combined_shard["status"] == "failed"
    assert len(combined_shard["steps"]) == 2
    assert repo_shard["status"] == "passed"

    with pytest.raises(ValueError, match="has no commands"):
        await skill._execute_system_run_shard(  # noqa: SLF001
            {
                "shard_id": "combined-empty",
                "validation_mode": "combined_system",
                "metadata": {"commands": []},
            }
        )
    with pytest.raises(ValueError, match="missing repo_id"):
        await skill._execute_system_run_shard(  # noqa: SLF001
            {
                "shard_id": "combined-missing-repo",
                "validation_mode": "combined_system",
                "metadata": {"commands": [{"command": ["bash", "-lc", "echo ok"]}]},
            }
        )
    with pytest.raises(ValueError, match="missing repo_id"):
        await skill._execute_system_run_shard(  # noqa: SLF001
            {
                "shard_id": "repo-missing",
                "validation_mode": "zetherion_alone",
                "metadata": {"command": ["bash", "-lc", "echo ok"]},
            }
        )

    storage.get_system_run.side_effect = [
        {
            "system_run_id": "system-run-blocked",
            "system_id": "cgs-zetherion",
            "mode_id": "combined_system",
            "status": "planned",
            "candidate_set": {"repos": []},
            "plan": {"shards": []},
            "readiness": {"blocking": True, "summary": "Blocked"},
            "coaching": [],
            "execution": {},
            "metadata": {},
        },
    ]
    storage.update_system_run.return_value = {
        "system_run_id": "system-run-blocked",
        "status": "blocked",
    }
    storage.refresh_system_run_report.return_value = {"system_run_id": "system-run-blocked"}

    missing_response = await skill.handle(
        SkillRequest(
            intent="agent_system_run_execute",
            user_id="owner-1",
            context={"owner_id": "owner-1"},
        )
    )
    blocked_response = await skill.handle(
        SkillRequest(
            intent="agent_system_run_execute",
            user_id="owner-1",
            context={"owner_id": "owner-1", "system_run_id": "system-run-blocked"},
        )
    )

    assert missing_response.success is False
    assert "system_run_id is required" in (missing_response.error or "")
    assert blocked_response.success is True
    assert blocked_response.data["system_run"]["status"] == "blocked"


@pytest.mark.asyncio
async def test_uploaded_ci_workspace_candidate_and_secret_binding_flow() -> None:
    storage = _storage()
    app_profile = {
        "app_id": "uploaded-app",
        "display_name": "Uploaded App",
        "active": True,
        "profile": {
            "candidate_retention_hours": 12,
            "approved_secret_bindings": {},
        },
    }
    storage.get_agent_app_profile.return_value = app_profile
    storage.list_agent_app_profiles.return_value = [app_profile]
    storage.list_external_access_grants.return_value = [
        {
            "active": True,
            "resource_type": "app",
            "resource_id": "uploaded-app",
        }
    ]

    archive_buffer = io.BytesIO()
    with tarfile.open(fileobj=archive_buffer, mode="w:gz") as archive:
        payload = b'{"name":"uploaded-app","scripts":{"test":"echo ok"}}'
        info = tarfile.TarInfo(name="package.json")
        info.size = len(payload)
        archive.addfile(info, io.BytesIO(payload))
    bundle_base64 = base64.b64encode(archive_buffer.getvalue()).decode("ascii")
    manifest = {
        "layout": "single_repo",
        "repos": [{"repo_key": "app", "path": ".", "role": "app"}],
        "primary_repo_key": "app",
        "required_secret_capabilities": ["stripe_test"],
    }
    upload_record = {
        "upload_id": "upload-1",
        "principal_id": "principal-1",
        "app_id": "uploaded-app",
        "status": "validated",
        "layout": "single_repo",
        "digest_sha256": "digest-1",
        "size_bytes": len(archive_buffer.getvalue()),
        "manifest": manifest,
        "validation": {
            "status": "validated",
            "file_count": 1,
            "top_level_entries": ["package.json"],
            "missing_repo_paths": [],
            "blocking_errors": [],
        },
        "bundle_base64": bundle_base64,
        "expires_at": "2026-03-22T00:00:00+00:00",
    }
    storage.create_workspace_upload.return_value = upload_record
    storage.get_workspace_upload.return_value = upload_record
    storage.create_execution_candidate.return_value = {
        "candidate_id": "cand-1",
        "principal_id": "principal-1",
        "app_id": "uploaded-app",
        "upload_id": "upload-1",
        "status": "ready",
        "candidate": {
            "upload_id": "upload-1",
            "layout": "single_repo",
            "manifest": manifest,
            "repo_summary": [{"repo_key": "app", "path": ".", "role": "app"}],
            "required_secret_capabilities": ["stripe_test"],
            "secret_capability_status": {
                "stripe_test": {"status": "missing_binding"}
            },
        },
        "validation": upload_record["validation"],
        "expires_at": "2026-03-22T00:00:00+00:00",
    }
    storage.get_secret_ref.return_value = {
        "secret_ref_id": "uploaded-app-stripe-test-stripe-secret-key",
        "has_secret": True,
        "active": True,
    }
    updated_app_profile = {
        **app_profile,
        "profile": {
            **app_profile["profile"],
            "approved_secret_bindings": {
                "stripe_test": {
                    "capability_id": "stripe_test",
                    "service_kind": "stripe",
                    "secrets": {
                        "STRIPE_SECRET_KEY": {
                            "secret_ref_id": "uploaded-app-stripe-test-stripe-secret-key"
                        },
                        "STRIPE_PUBLISHABLE_KEY": {
                            "secret_ref_id": "uploaded-app-stripe-test-stripe-publishable-key"
                        },
                    },
                    "updated_at": "2026-03-21T00:00:00+00:00",
                }
            },
        },
    }
    storage.upsert_agent_app_profile.return_value = updated_app_profile
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    upload_response = await skill.handle(
        SkillRequest(
            intent="agent_workspace_upload_create",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "principal-1",
                "app_id": "uploaded-app",
                "bundle_base64": bundle_base64,
                "candidate_manifest": manifest,
            },
        )
    )
    candidate_response = await skill.handle(
        SkillRequest(
            intent="agent_execution_candidate_create",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "principal-1",
                "app_id": "uploaded-app",
                "upload_id": "upload-1",
            },
        )
    )
    binding_response = await skill.handle(
        SkillRequest(
            intent="agent_app_secret_bindings_put",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "principal-1",
                "app_id": "uploaded-app",
                "capability_id": "stripe_test",
                "secrets": {
                    "STRIPE_SECRET_KEY": {"secret_value": "sk_test_123"},
                    "STRIPE_PUBLISHABLE_KEY": {"secret_value": "pk_test_123"},
                },
            },
        )
    )

    assert upload_response.success is True
    assert upload_response.data["upload"]["upload_id"] == "upload-1"
    assert "bundle_base64" not in upload_response.data["upload"]
    assert candidate_response.success is True
    assert candidate_response.data["candidate"]["candidate_id"] == "cand-1"
    assert (
        candidate_response.data["candidate"]["candidate"]["secret_capability_status"][
            "stripe_test"
        ]["status"]
        == "missing_binding"
    )
    assert binding_response.success is True
    assert binding_response.data["binding"]["capability_id"] == "stripe_test"
    assert storage.upsert_secret_ref.await_count == 2
    storage.create_workspace_upload.assert_awaited()
    storage.create_execution_candidate.assert_awaited()


@pytest.mark.asyncio
async def test_app_coaching_and_rollout_helpers_cover_degraded_and_recorded_only_paths() -> None:
    storage = _storage()
    storage.list_external_service_connectors.return_value = [
        {
            "connector_id": "github-main",
            "service_kind": "github",
            "has_secret": True,
            "health_status": "healthy",
            "active": True,
        }
    ]
    storage.list_agent_gap_events.return_value = [
        {
            "gap_id": "gap-degraded-1",
            "blocker": False,
            "summary": "Connector notes are incomplete",
        }
    ]
    storage.list_agent_coaching_feedback = AsyncMock(
        return_value=[
            {
                "feedback_id": "feedback-1",
                "scope": "app",
                "summary": "Existing app coaching",
                "blocking": False,
            }
        ]
    )
    skill = AgentBootstrapSkill(storage=storage)

    app_profile = {
        "app_id": "app-1",
        "profile": {
            "repo_ids": ["repo-a"],
            "service_connector_map": {
                "github": {
                    "connector_id": "github-main",
                    "read_access": ["repo:read"],
                    "write_access": ["repo:write"],
                }
            },
            "ai_runtime_policy": {
                "allowed_providers": ["groq"],
                "allowed_models": ["gpt-oss-120b"],
            },
            "ai_agent_profiles": [{"agent_profile_id": "planner"}],
        },
    }

    readiness = await skill._build_rollout_readiness(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="principal-1",
        app_profile=app_profile,
        limit=5,
    )
    coaching = await skill._build_app_coaching(  # noqa: SLF001
        owner_id="owner-1",
        principal_id="principal-1",
        app_profile=app_profile,
        limit=5,
    )

    assert readiness["status"] == "degraded"
    assert readiness["blocker_count"] == 0
    assert readiness["degraded_count"] == 1
    assert coaching == [
        {
            "feedback_id": "feedback-1",
            "scope": "app",
            "summary": "Existing app coaching",
            "blocking": False,
        }
    ]


@pytest.mark.asyncio
async def test_system_run_handlers_require_system_run_id_for_retrieval_intents() -> None:
    skill = AgentBootstrapSkill(storage=_storage())

    intents = [
        "agent_system_run_get",
        "agent_system_run_execute",
        "agent_system_run_report_get",
        "agent_system_run_graph_get",
        "agent_system_run_correlation_context_get",
        "agent_system_run_diagnostics_get",
        "agent_system_run_artifacts_get",
        "agent_system_run_evidence_get",
        "agent_system_run_coaching_get",
        "agent_system_run_readiness_get",
        "agent_system_run_usage_get",
    ]

    for intent in intents:
        response = await skill.handle(
            SkillRequest(
                intent=intent,
                user_id="owner-1",
                context={"owner_id": "owner-1"},
            )
        )
        assert response.success is False
        assert response.error == "system_run_id is required"


@pytest.mark.asyncio
async def test_system_run_handlers_cover_listing_not_found_and_skipped_batches() -> None:
    storage = _storage()
    storage.list_system_runs.return_value = [{"system_run_id": "system-run-1"}]
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]

    listed = await skill.handle(
        SkillRequest(
            intent="agent_system_run_list",
            user_id="owner-1",
            context={"owner_id": "owner-1"},
        )
    )
    assert listed.success is True
    storage.list_system_runs.assert_awaited_with(
        "owner-1",
        system_id=None,
        limit=25,
    )

    storage.get_system_run.side_effect = [None, None]
    missing_get = await skill.handle(
        SkillRequest(
            intent="agent_system_run_get",
            user_id="owner-1",
            context={"owner_id": "owner-1", "system_run_id": "missing-run"},
        )
    )
    missing_execute = await skill.handle(
        SkillRequest(
            intent="agent_system_run_execute",
            user_id="owner-1",
            context={"owner_id": "owner-1", "system_run_id": "missing-run"},
        )
    )

    assert missing_get.success is False
    assert "not found" in str(missing_get.error)
    assert missing_execute.success is False
    assert "not found" in str(missing_execute.error)

    async def _fake_run(
        command: list[str], *, cwd: Path, env: dict[str, str] | None = None, check: bool
    ) -> dict[str, Any]:
        if command[-1] == "exit 1":
            return {"returncode": 1, "stdout": "", "stderr": "boom"}
        return {"returncode": 0, "stdout": str(cwd), "stderr": ""}

    skill._run_command = AsyncMock(side_effect=_fake_run)  # type: ignore[method-assign]
    storage.get_system_run.side_effect = [
        {
            "system_run_id": "system-run-2",
            "system_id": "cgs-zetherion",
            "mode_id": "combined_system",
            "status": "planned",
            "candidate_set": {
                "repos": [
                    {"repo_id": "zetherion-ai", "git_ref": "HEAD"},
                    {"repo_id": "catalyst-group-solutions", "git_ref": "HEAD"},
                ]
            },
            "plan": {
                "shards": [
                    {
                        "shard_id": "combined-a",
                        "lane_family": "combined_system",
                        "validation_mode": "combined_system",
                        "blocking": True,
                        "depends_on": [],
                        "repo_ids": ["zetherion-ai"],
                        "metadata": {
                            "commands": [
                                {
                                    "repo_id": "zetherion-ai",
                                    "cwd": ".",
                                    "command": ["bash", "-lc", "exit 1"],
                                }
                            ]
                        },
                    },
                    {
                        "shard_id": "combined-b",
                        "lane_family": "combined_system",
                        "validation_mode": "combined_system",
                        "blocking": True,
                        "depends_on": ["combined-a"],
                        "repo_ids": ["catalyst-group-solutions"],
                        "required_paths": ["combined_contract"],
                        "expected_artifacts": ["stdout"],
                        "metadata": {
                            "commands": [
                                {
                                    "repo_id": "catalyst-group-solutions",
                                    "cwd": ".",
                                    "command": ["bash", "-lc", "echo ok"],
                                }
                            ]
                        },
                    },
                ]
            },
            "readiness": {"blocking": False},
            "coaching": [],
            "execution": {},
            "metadata": {},
        }
    ]
    storage.update_system_run.return_value = {
        "system_run_id": "system-run-2",
        "status": "failed",
    }
    storage.refresh_system_run_report.return_value = {"system_run_id": "system-run-2"}

    executed = await skill.handle(
        SkillRequest(
            intent="agent_system_run_execute",
            user_id="owner-1",
            context={"owner_id": "owner-1", "system_run_id": "system-run-2"},
        )
    )

    assert executed.success is True
    assert executed.data["system_run"]["status"] == "failed"
    assert executed.data["report"]["system_run_id"] == "system-run-2"
    execution_update = storage.update_system_run.await_args_list[-1].kwargs["execution"]
    assert execution_update["batches"][0]["status"] == "failed"
    assert execution_update["batches"][1]["status"] == "skipped"
    assert execution_update["batches"][1]["shards"][0]["skip_reason"] == "previous batch failed"


def test_extract_operation_refs_from_event_covers_stripe_payloads() -> None:
    skill = AgentBootstrapSkill(storage=_storage())

    refs = skill._extract_operation_refs_from_event(  # noqa: SLF001
        "stripe",
        {
            "id": "evt_123",
            "type": "customer.subscription.updated",
            "data": {
                "object": {
                    "customer": "cus_123",
                    "subscription": "sub_123",
                }
            },
        },
    )

    assert refs == {
        "stripe_event_id": "evt_123",
        "customer_id": "cus_123",
        "subscription_id": "sub_123",
    }
