"""Unit tests for the brokered agent bootstrap skill."""

from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

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
    storage.upsert_agent_app_profile = AsyncMock()
    storage.get_agent_knowledge_pack = AsyncMock(return_value=None)
    storage.upsert_agent_knowledge_pack = AsyncMock()
    storage.list_external_access_grants = AsyncMock(return_value=[])
    storage.record_agent_audit_event = AsyncMock(return_value={"audit_id": "audit-1"})
    storage.get_repo_profile = AsyncMock()
    storage.create_workspace_bundle = AsyncMock()
    storage.mark_workspace_bundle_downloaded = AsyncMock()
    storage.get_workspace_bundle = AsyncMock()
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
    storage.list_agent_session_interactions = AsyncMock(return_value=[])
    return storage


def _app_profile(app_id: str = "catalyst-group-solutions") -> dict[str, object]:
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
