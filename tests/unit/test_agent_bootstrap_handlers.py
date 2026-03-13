"""Focused coverage for agent bootstrap handler surfaces."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.skills.agent_bootstrap import AgentBootstrapSkill
from zetherion_ai.skills.base import SkillRequest


def _storage() -> MagicMock:
    storage = MagicMock()
    storage.store_agent_bootstrap_manifest = AsyncMock(
        return_value={"client_id": "codex-desktop", "version": "v2"}
    )
    storage.store_agent_setup_receipt = AsyncMock(return_value={"status": "stored"})
    storage.get_agent_bootstrap_manifest = AsyncMock(
        return_value={"client_id": "codex-desktop", "version": "v2"}
    )
    storage.list_agent_docs_manifests = AsyncMock(return_value=[])
    storage.get_agent_docs_manifest = AsyncMock(return_value=None)
    storage.get_agent_principal = AsyncMock(return_value=None)
    storage.upsert_agent_principal = AsyncMock(
        return_value={"principal_id": "codex-1", "display_name": "Codex 1"}
    )
    storage.create_agent_session = AsyncMock(
        return_value={"session_id": "sess-1", "principal_id": "codex-1"}
    )
    storage.list_agent_app_profiles = AsyncMock(return_value=[])
    storage.get_agent_app_profile = AsyncMock(return_value=None)
    storage.get_agent_knowledge_pack = AsyncMock(return_value=None)
    storage.list_agent_gap_events = AsyncMock(return_value=[])
    storage.list_agent_session_interactions = AsyncMock(return_value=[])
    storage.record_agent_audit_event = AsyncMock(return_value={"audit_id": "audit-1"})
    storage.create_agent_interaction = AsyncMock(return_value={"interaction_id": "int-1"})
    storage.create_agent_action = AsyncMock(return_value={"action_record_id": "act-1"})
    storage.create_agent_outcome = AsyncMock(return_value={"outcome_id": "out-1"})
    storage.create_agent_service_request = AsyncMock(
        return_value={"request_id": "svc-1", "status": "executed"}
    )
    storage.find_managed_operation_by_ref = AsyncMock(return_value=None)
    storage.create_managed_operation = AsyncMock(
        return_value={"operation_id": "op-1", "app_id": "catalyst-group-solutions"}
    )
    storage.get_operation_hydrated = AsyncMock(
        return_value={"operation_id": "op-1", "app_id": "catalyst-group-solutions"}
    )
    storage.get_managed_operation = AsyncMock(
        return_value={"operation_id": "op-1", "app_id": "catalyst-group-solutions"}
    )
    storage.list_managed_operations = AsyncMock(return_value=[])
    storage.list_operation_evidence = AsyncMock(return_value=[])
    storage.get_operation_log_chunks = AsyncMock(return_value=[])
    storage.list_operation_incidents = AsyncMock(return_value=[])
    storage.list_operation_refs = AsyncMock(return_value=[])
    storage.record_operation_evidence = AsyncMock(return_value={"evidence_id": "evidence-1"})
    storage.record_operation_incident = AsyncMock(return_value={"incident_id": "incident-1"})
    storage.upsert_operation_ref = AsyncMock(return_value={"ref_id": "ref-1"})
    storage.update_managed_operation = AsyncMock()
    storage.get_repo_profile = AsyncMock(return_value=None)
    storage.create_workspace_bundle = AsyncMock(
        return_value={
            "bundle_id": "bundle-1",
            "principal_id": "codex-1",
            "app_id": "catalyst-group-solutions",
            "repo_id": "catalyst-group-solutions",
        }
    )
    storage.get_workspace_bundle = AsyncMock(
        return_value={
            "bundle_id": "bundle-1",
            "principal_id": "codex-1",
            "app_id": "catalyst-group-solutions",
            "repo_id": "catalyst-group-solutions",
        }
    )
    storage.mark_workspace_bundle_downloaded = AsyncMock()
    storage.create_compiled_plan = AsyncMock(
        return_value={"compiled_plan_id": "compiled-1", "plan": {"shards": []}}
    )
    storage.create_publish_candidate = AsyncMock(
        return_value={"candidate_id": "candidate-1", "status": "submitted"}
    )
    storage.list_agent_principals = AsyncMock(return_value=[])
    storage.upsert_external_service_connector = AsyncMock(
        return_value={"connector_id": "github-primary", "service_kind": "github"}
    )
    storage.list_external_service_connectors = AsyncMock(return_value=[])
    storage.get_external_service_connector = AsyncMock(return_value=None)
    storage.replace_external_access_grants = AsyncMock(return_value=[])
    storage.upsert_agent_app_profile = AsyncMock(
        return_value={"app_id": "catalyst-group-solutions", "profile": {}}
    )
    storage.upsert_agent_knowledge_pack = AsyncMock(
        return_value={"app_id": "catalyst-group-solutions", "version": "current"}
    )
    storage.list_agent_audit_events = AsyncMock(return_value=[])
    storage.upsert_secret_ref = AsyncMock(return_value={"secret_ref_id": "secret-1"})
    storage.list_secret_refs = AsyncMock(return_value=[])
    storage.get_agent_gap_event = AsyncMock(return_value=None)
    storage.update_agent_gap_event = AsyncMock(return_value=None)
    storage.record_agent_gap_event = AsyncMock(return_value={"gap_id": "gap-1"})
    return storage


def _app_profile(app_id: str = "catalyst-group-solutions") -> dict[str, object]:
    return {
        "app_id": app_id,
        "display_name": "Catalyst Group Solutions",
        "profile": {
            "repo_ids": [app_id],
            "github_repos": [f"jimtin/{app_id}"],
            "docs_slugs": ["cgs-ai-api-quickstart"],
            "service_connector_map": {
                "github": {
                    "connector_id": "github-primary",
                    "service_kind": "github",
                    "read_access": ["branch_metadata", "pr_metadata"],
                    "write_access": [],
                    "broker_only": True,
                },
                "stripe": {
                    "connector_id": "stripe-primary",
                    "service_kind": "stripe",
                    "read_access": ["account_metadata"],
                    "write_access": ["product_ensure"],
                    "broker_only": True,
                },
            },
            "github_governance": {
                "write_principal": "zetherion",
                "agent_push_enabled": False,
            },
        },
    }


def _skill(storage: MagicMock) -> AgentBootstrapSkill:
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_service_capabilities = AsyncMock()  # type: ignore[method-assign]
    skill._record_interaction_outcome = AsyncMock()  # type: ignore[method-assign]
    return skill


@pytest.mark.asyncio
async def test_client_docs_session_and_apps_handlers_cover_success_paths() -> None:
    storage = _storage()
    storage.list_agent_docs_manifests.return_value = [
        {
            "slug": "cgs-ai-api-quickstart",
            "title": "Quickstart",
            "manifest": {"content_markdown": "# Quickstart\nInstall"},
        },
        {
            "slug": "internal-runbook",
            "title": "Runbook",
            "manifest": {"content_markdown": "# Runbook"},
        },
    ]
    storage.get_agent_docs_manifest.return_value = {
        "slug": "cgs-ai-api-quickstart",
        "manifest": {"content_markdown": "# Quickstart"},
    }
    storage.list_agent_session_interactions.return_value = [{"interaction_id": "int-1"}]
    storage.list_agent_gap_events.return_value = [{"gap_id": "gap-1"}]
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.list_external_access_grants.return_value = [
        {"resource_type": "app", "resource_id": "catalyst-group-solutions", "active": True}
    ]

    skill = _skill(storage)
    skill._require_app_access = AsyncMock(return_value=_app_profile())  # type: ignore[method-assign]
    skill._list_accessible_apps = AsyncMock(return_value=[_app_profile()])  # type: ignore[method-assign]

    bootstrap = await skill.handle(
        SkillRequest(
            intent="agent_client_bootstrap",
            user_id="owner-1",
            context={"client_id": "codex-desktop", "manifest": {"version": "v2"}},
        )
    )
    manifest = await skill.handle(
        SkillRequest(
            intent="agent_client_manifest_get",
            user_id="owner-1",
            context={"client_id": "codex-desktop"},
        )
    )
    docs_list = await skill.handle(
        SkillRequest(
            intent="agent_docs_list",
            user_id="owner-1",
            context={
                "app_id": "catalyst-group-solutions",
                "principal_id": "codex-1",
                "query": "quickstart",
            },
        )
    )
    docs_get = await skill.handle(
        SkillRequest(
            intent="agent_docs_get",
            user_id="owner-1",
            context={
                "slug": "cgs-ai-api-quickstart",
                "app_id": "catalyst-group-solutions",
                "principal_id": "codex-1",
            },
        )
    )
    session = await skill.handle(
        SkillRequest(
            intent="agent_session_create",
            user_id="owner-1",
            context={"owner_id": "owner-1", "principal_id": "codex-1"},
        )
    )
    interactions = await skill.handle(
        SkillRequest(
            intent="agent_session_interactions_list",
            user_id="owner-1",
            context={"session_id": "sess-1"},
        )
    )
    gaps = await skill.handle(
        SkillRequest(
            intent="agent_session_gaps_list",
            user_id="owner-1",
            context={"session_id": "sess-1"},
        )
    )
    apps = await skill.handle(
        SkillRequest(
            intent="agent_apps_list",
            user_id="owner-1",
            context={"principal_id": "codex-1"},
        )
    )

    assert bootstrap.success is True
    assert manifest.data["manifest"]["client_id"] == "codex-desktop"
    assert [doc["slug"] for doc in docs_list.data["docs"]] == ["cgs-ai-api-quickstart"]
    assert docs_get.data["doc"]["slug"] == "cgs-ai-api-quickstart"
    assert session.data["session"]["session_id"] == "sess-1"
    assert interactions.data["interactions"] == [{"interaction_id": "int-1"}]
    assert gaps.data["gaps"] == [{"gap_id": "gap-1"}]
    assert apps.data["apps"][0]["app_id"] == "catalyst-group-solutions"


@pytest.mark.asyncio
async def test_docs_and_session_handlers_cover_edge_paths() -> None:
    storage = _storage()
    storage.get_agent_bootstrap_manifest.return_value = None
    storage.get_agent_docs_manifest.side_effect = [
        None,
        {
            "slug": "internal-runbook",
            "manifest": {"content_markdown": "# Internal"},
        },
    ]
    storage.get_agent_principal.return_value = {
        "principal_id": "codex-1",
        "display_name": "Codex 1",
        "allowed_scopes": ["cgs:agent"],
    }

    skill = _skill(storage)
    skill._require_app_access = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            _app_profile(),
            {
                "app_id": "catalyst-group-solutions",
                "profile": {"docs_slugs": ["cgs-ai-api-quickstart"]},
            },
        ]
    )
    skill._list_accessible_apps = AsyncMock(return_value=[])  # type: ignore[method-assign]

    missing_manifest = await skill.handle(
        SkillRequest(
            intent="agent_client_manifest_get",
            user_id="owner-1",
            context={"client_id": "missing-client"},
        )
    )
    missing_docs = await skill.handle(
        SkillRequest(
            intent="agent_docs_get",
            user_id="owner-1",
            context={
                "slug": "missing-doc",
                "app_id": "catalyst-group-solutions",
                "principal_id": "codex-1",
                "session_id": "sess-1",
            },
        )
    )
    disallowed_docs = await skill.handle(
        SkillRequest(
            intent="agent_docs_get",
            user_id="owner-1",
            context={
                "slug": "internal-runbook",
                "app_id": "catalyst-group-solutions",
                "principal_id": "codex-1",
            },
        )
    )
    existing_session = await skill.handle(
        SkillRequest(
            intent="agent_session_create",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "session_id": "sess-existing",
                "app_ids": "not-a-list",
            },
        )
    )

    assert missing_manifest.success is False
    assert "not found" in (missing_manifest.error or "").lower()
    assert missing_docs.success is False
    assert "missing-doc" in (missing_docs.error or "")
    storage.record_agent_gap_event.assert_awaited_once()
    assert disallowed_docs.success is False
    assert "not allowed" in (disallowed_docs.error or "").lower()
    assert existing_session.success is True
    storage.upsert_agent_principal.assert_not_awaited()
    storage.create_agent_session.assert_awaited_once()


@pytest.mark.asyncio
async def test_service_and_operation_handlers_cover_success_and_gap_paths() -> None:
    storage = _storage()
    storage.list_managed_operations.return_value = [
        {"operation_id": "op-1", "app_id": "catalyst-group-solutions"}
    ]
    storage.list_operation_incidents.return_value = [{"incident_id": "inc-1"}]
    storage.list_operation_refs.return_value = [{"ref_kind": "git_sha", "ref_value": "0" * 40}]
    storage.list_operation_evidence.return_value = [{"evidence_id": "ev-1"}]
    storage.get_operation_log_chunks.return_value = [{"chunk_id": "log-1"}]

    skill = _skill(storage)
    skill._require_app_access = AsyncMock(return_value=_app_profile())  # type: ignore[method-assign]
    skill._read_service_view = AsyncMock(return_value={"service_kind": "github"})  # type: ignore[method-assign]
    skill._execute_service_action = AsyncMock(  # type: ignore[method-assign]
        return_value={"request": {"request_id": "svc-1"}}
    )
    skill._list_accessible_apps = AsyncMock(return_value=[_app_profile()])  # type: ignore[method-assign]
    skill._find_or_create_operation = AsyncMock(  # type: ignore[method-assign]
        return_value={"operation_id": "op-1", "app_id": "catalyst-group-solutions"}
    )
    skill._refresh_operation = AsyncMock()  # type: ignore[method-assign]
    skill._record_gap = AsyncMock(return_value={"gap_id": "gap-1"})  # type: ignore[method-assign]

    service_read = await skill.handle(
        SkillRequest(
            intent="agent_service_read",
            user_id="owner-1",
            context={
                "app_id": "catalyst-group-solutions",
                "service_kind": "github",
                "principal_id": "codex-1",
            },
        )
    )
    service_request = await skill.handle(
        SkillRequest(
            intent="agent_service_request_submit",
            user_id="owner-1",
            context={
                "app_id": "catalyst-group-solutions",
                "service_kind": "stripe",
                "action_id": "product.ensure",
                "principal_id": "codex-1",
            },
        )
    )
    operation_resolve = await skill.handle(
        SkillRequest(
            intent="agent_operation_resolve",
            user_id="owner-1",
            context={
                "app_id": "catalyst-group-solutions",
                "principal_id": "codex-1",
                "git_sha": "0" * 40,
            },
        )
    )
    event_gap = await skill.handle(
        SkillRequest(
            intent="agent_operation_event_ingest",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "app_id": "catalyst-group-solutions",
                "service_kind": "github",
                "principal_id": "system:watchdog",
                "event_payload": {"action": "completed"},
            },
        )
    )
    event_success = await skill.handle(
        SkillRequest(
            intent="agent_operation_event_ingest",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "app_id": "catalyst-group-solutions",
                "service_kind": "github",
                "principal_id": "system:watchdog",
                "event_payload": {"workflow_run": {"id": 123, "head_sha": "1" * 40}},
            },
        )
    )
    poll = await skill.handle(
        SkillRequest(
            intent="agent_operation_poll",
            user_id="owner-1",
            context={
                "owner_id": "owner-1",
                "principal_id": "codex-1",
                "app_id": "catalyst-group-solutions",
                "operation_id": "op-1",
            },
        )
    )
    listed = await skill.handle(
        SkillRequest(
            intent="agent_operation_list",
            user_id="owner-1",
            context={"principal_id": "codex-1", "app_id": "catalyst-group-solutions"},
        )
    )
    loaded = await skill.handle(
        SkillRequest(
            intent="agent_operation_get",
            user_id="owner-1",
            context={"operation_id": "op-1", "principal_id": "codex-1"},
        )
    )
    evidence = await skill.handle(
        SkillRequest(
            intent="agent_operation_evidence_list",
            user_id="owner-1",
            context={"operation_id": "op-1", "principal_id": "codex-1"},
        )
    )
    logs = await skill.handle(
        SkillRequest(
            intent="agent_operation_logs",
            user_id="owner-1",
            context={"operation_id": "op-1", "principal_id": "codex-1"},
        )
    )
    incidents = await skill.handle(
        SkillRequest(
            intent="agent_operation_incidents_list",
            user_id="owner-1",
            context={"operation_id": "op-1", "principal_id": "codex-1"},
        )
    )

    assert service_read.data["service_kind"] == "github"
    assert service_request.data["request"]["request_id"] == "svc-1"
    assert operation_resolve.data["operation"]["operation_id"] == "op-1"
    assert event_gap.data["gap"]["gap_id"] == "gap-1"
    assert event_success.data["operation"]["operation_id"] == "op-1"
    assert poll.data["operations"][0]["operation_id"] == "op-1"
    assert listed.data["operations"][0]["incident_count"] == 1
    assert loaded.data["operation"]["operation_id"] == "op-1"
    assert evidence.data["evidence"] == [{"evidence_id": "ev-1"}]
    assert logs.data["logs"] == [{"chunk_id": "log-1"}]
    assert incidents.data["incidents"] == [{"incident_id": "inc-1"}]


@pytest.mark.asyncio
async def test_repo_workspace_plan_publish_and_management_handlers_cover_success_paths() -> None:
    storage = _storage()
    storage.get_agent_principal.return_value = {"principal_id": "codex-1"}
    storage.list_agent_app_profiles.return_value = [_app_profile()]
    storage.get_agent_app_profile.return_value = _app_profile()
    storage.get_agent_knowledge_pack.return_value = {"pack": {"capability_registry": {}}}

    skill = _skill(storage)
    skill._require_app_access = AsyncMock(return_value=_app_profile())  # type: ignore[method-assign]
    skill._discover_github_repositories = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"full_name": "jimtin/zetherion-ai"}]
    )
    skill._enroll_github_repository = AsyncMock(  # type: ignore[method-assign]
        return_value={"app": {"app_id": "zetherion-ai"}}
    )
    skill._resolve_repo_profile = AsyncMock(  # type: ignore[method-assign]
        return_value={
            "repo_id": "catalyst-group-solutions",
            "default_branch": "main",
            "stack_kind": "nextjs",
            "mandatory_static_gates": [],
            "local_fast_lanes": [],
            "local_full_lanes": [],
            "windows_full_lanes": [],
        }
    )
    skill._create_workspace_bundle_payload = AsyncMock(  # type: ignore[method-assign]
        return_value=({"download_mode": "inline_base64", "archive_base64": "ZmFrZQ=="}, "main")
    )
    skill._detect_test_plan_gaps = AsyncMock(return_value=[])  # type: ignore[method-assign]
    skill._find_or_create_operation = AsyncMock(  # type: ignore[method-assign]
        return_value={"operation_id": "op-1", "app_id": "catalyst-group-solutions", "metadata": {}}
    )
    skill._apply_publish_candidate = AsyncMock(  # type: ignore[method-assign]
        return_value={"candidate_id": "candidate-1", "status": "applied"}
    )
    skill._enforce_managed_repo = AsyncMock(  # type: ignore[method-assign]
        return_value={"github_repo": "jimtin/catalyst-group-solutions", "enforced": True}
    )

    discover = await skill.handle(
        SkillRequest(
            intent="agent_repo_discover",
            user_id="owner-1",
            context={"principal_id": "codex-1", "allowed_scopes": ["cgs:agent:discover"]},
        )
    )
    enroll = await skill.handle(
        SkillRequest(
            intent="agent_repo_enroll",
            user_id="owner-1",
            context={"github_repo": "jimtin/zetherion-ai", "stack_kind": "python"},
        )
    )
    bundle_create = await skill.handle(
        SkillRequest(
            intent="agent_workspace_bundle_create",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions", "principal_id": "codex-1"},
        )
    )
    bundle_get = await skill.handle(
        SkillRequest(
            intent="agent_workspace_bundle_get",
            user_id="owner-1",
            context={"bundle_id": "bundle-1", "principal_id": "codex-1"},
        )
    )
    compiled = await skill.handle(
        SkillRequest(
            intent="agent_test_plan_compile",
            user_id="owner-1",
            context={
                "app_id": "catalyst-group-solutions",
                "principal_id": "codex-1",
                "mode": "full",
            },
        )
    )
    submit = await skill.handle(
        SkillRequest(
            intent="agent_publish_candidate_submit",
            user_id="owner-1",
            context={
                "app_id": "catalyst-group-solutions",
                "principal_id": "codex-1",
                "base_sha": "0" * 40,
                "diff_text": "diff --git a b",
            },
        )
    )
    apply = await skill.handle(
        SkillRequest(
            intent="agent_publish_candidate_apply",
            user_id="owner-1",
            context={"candidate_id": "candidate-1", "principal_id": "codex-1"},
        )
    )
    enforce = await skill.handle(
        SkillRequest(
            intent="agent_managed_repo_enforce",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions", "principal_id": "codex-1"},
        )
    )

    assert discover.data["repositories"][0]["managed"] is False
    assert enroll.data["app"]["app_id"] == "zetherion-ai"
    assert bundle_create.data["bundle"]["bundle_id"] == "bundle-1"
    assert bundle_get.data["bundle"]["bundle_id"] == "bundle-1"
    assert compiled.data["compiled_plan"]["compiled_plan_id"] == "compiled-1"
    assert submit.data["candidate"]["candidate_id"] == "candidate-1"
    assert apply.data["candidate_id"] == "candidate-1"
    assert enforce.data["enforced"] is True


@pytest.mark.asyncio
async def test_principal_connector_app_secret_and_gap_handlers_cover_success_paths() -> None:
    storage = _storage()
    storage.list_agent_principals.return_value = [{"principal_id": "codex-1"}]
    storage.list_external_service_connectors.return_value = [
        {
            "connector_id": "github-primary",
            "service_kind": "github",
            "display_name": "GitHub",
            "auth_kind": "token",
            "policy": {},
            "metadata": {},
            "active": True,
        }
    ]
    storage.get_external_service_connector.return_value = {
        "connector_id": "github-primary",
        "service_kind": "github",
        "display_name": "GitHub",
        "auth_kind": "token",
        "policy": {},
        "metadata": {},
        "active": True,
    }
    storage.replace_external_access_grants.return_value = [
        {"resource_id": "catalyst-group-solutions"}
    ]
    storage.list_agent_audit_events.return_value = [{"audit_id": "audit-1"}]
    storage.list_secret_refs.return_value = [{"secret_ref_id": "secret-1"}]
    storage.list_agent_gap_events.return_value = [{"gap_id": "gap-1"}]
    storage.get_agent_gap_event.return_value = {"gap_id": "gap-1"}
    storage.update_agent_gap_event.return_value = {"gap_id": "gap-1", "status": "resolved"}

    skill = _skill(storage)

    principal = await skill.handle(
        SkillRequest(
            intent="agent_principal_upsert",
            user_id="owner-1",
            context={"principal_id": "codex-1", "display_name": "Codex 1"},
        )
    )
    principals = await skill.handle(SkillRequest(intent="agent_principal_list", user_id="owner-1"))
    connector = await skill.handle(
        SkillRequest(
            intent="agent_connector_upsert",
            user_id="owner-1",
            context={"connector_id": "github-primary", "service_kind": "github"},
        )
    )
    connectors = await skill.handle(SkillRequest(intent="agent_connector_list", user_id="owner-1"))
    rotate = await skill.handle(
        SkillRequest(
            intent="agent_connector_rotate",
            user_id="owner-1",
            context={"connector_id": "github-primary", "secret_value": "secret-2"},
        )
    )
    grants = await skill.handle(
        SkillRequest(
            intent="agent_principal_grants_put",
            user_id="owner-1",
            context={"principal_id": "codex-1", "grants": [{"resource_id": "cgs"}]},
        )
    )
    app = await skill.handle(
        SkillRequest(
            intent="agent_app_upsert",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions", "profile": {"repo_ids": ["cgs"]}},
        )
    )
    apps = await skill.handle(SkillRequest(intent="agent_app_list", user_id="owner-1"))
    knowledge = await skill.handle(
        SkillRequest(
            intent="agent_knowledge_pack_upsert",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions", "pack": {"workspace_manifest": {}}},
        )
    )
    audits = await skill.handle(SkillRequest(intent="agent_audit_list", user_id="owner-1"))
    secret = await skill.handle(
        SkillRequest(
            intent="agent_secret_ref_upsert",
            user_id="owner-1",
            context={"secret_ref_id": "secret-1", "purpose": "deploy"},
        )
    )
    secrets = await skill.handle(SkillRequest(intent="agent_secret_ref_list", user_id="owner-1"))
    gap_list = await skill.handle(SkillRequest(intent="agent_gap_list", user_id="owner-1"))
    gap_get = await skill.handle(
        SkillRequest(intent="agent_gap_get", user_id="owner-1", context={"gap_id": "gap-1"})
    )
    gap_update = await skill.handle(
        SkillRequest(
            intent="agent_gap_update",
            user_id="owner-1",
            context={"gap_id": "gap-1", "status": "resolved"},
        )
    )

    assert principal.data["principal"]["principal_id"] == "codex-1"
    assert principals.data["principals"] == [{"principal_id": "codex-1"}]
    assert connector.data["connector"]["connector_id"] == "github-primary"
    assert connectors.data["connectors"][0]["connector_id"] == "github-primary"
    assert rotate.data["connector"]["service_kind"] == "github"
    assert grants.data["grants"][0]["resource_id"] == "catalyst-group-solutions"
    assert app.data["app"]["app_id"] == "catalyst-group-solutions"
    assert apps.data["apps"] == []
    assert knowledge.data["knowledge_pack"]["app_id"] == "catalyst-group-solutions"
    assert audits.data["events"] == [{"audit_id": "audit-1"}]
    assert secret.data["secret_ref"]["secret_ref_id"] == "secret-1"
    assert secrets.data["secret_refs"] == [{"secret_ref_id": "secret-1"}]
    assert gap_list.data["gaps"] == [{"gap_id": "gap-1"}]
    assert gap_get.data["gap"]["gap_id"] == "gap-1"
    assert gap_update.data["gap"]["status"] == "resolved"
