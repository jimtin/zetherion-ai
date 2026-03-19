"""Focused coverage for agent bootstrap handler surfaces."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from zetherion_ai.skills.agent_bootstrap import AgentBootstrapSkill
from zetherion_ai.skills.base import SkillRequest, SkillResponse


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
    storage.get_service_adapter_capability = AsyncMock(return_value=None)
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


def _skill_with_real_outcome_recording(storage: MagicMock) -> AgentBootstrapSkill:
    skill = AgentBootstrapSkill(storage=storage)
    skill._ensure_default_docs = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_apps = AsyncMock()  # type: ignore[method-assign]
    skill._ensure_default_service_capabilities = AsyncMock()  # type: ignore[method-assign]
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
async def test_agent_bootstrap_handler_error_paths_cover_missing_inputs_and_denials() -> None:
    storage = _storage()
    storage.get_agent_principal.side_effect = [
        None,
        {"principal_id": "codex-1", "display_name": "Codex 1"},
    ]
    storage.get_managed_operation.return_value = None
    storage.get_operation_hydrated.return_value = None

    skill = _skill(storage)
    skill._require_app_access = AsyncMock(return_value=_app_profile())  # type: ignore[method-assign]

    missing_doc_slug = await skill.handle(
        SkillRequest(intent="agent_docs_get", user_id="owner-1", context={})
    )
    missing_interactions_session = await skill.handle(
        SkillRequest(intent="agent_session_interactions_list", user_id="owner-1", context={})
    )
    missing_gaps_session = await skill.handle(
        SkillRequest(intent="agent_session_gaps_list", user_id="owner-1", context={})
    )
    missing_app_manifest_id = await skill.handle(
        SkillRequest(intent="agent_app_manifest_get", user_id="owner-1", context={})
    )
    missing_knowledge_pack = await skill.handle(
        SkillRequest(
            intent="agent_app_manifest_get",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions", "principal_id": "codex-1"},
        )
    )
    missing_services_app = await skill.handle(
        SkillRequest(intent="agent_app_services_list", user_id="owner-1", context={})
    )
    missing_service_read_args = await skill.handle(
        SkillRequest(
            intent="agent_service_read",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions"},
        )
    )
    missing_service_request_args = await skill.handle(
        SkillRequest(
            intent="agent_service_request_submit",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions", "service_kind": "github"},
        )
    )
    missing_operation_app = await skill.handle(
        SkillRequest(intent="agent_operation_resolve", user_id="owner-1", context={})
    )
    missing_operation_refs = await skill.handle(
        SkillRequest(
            intent="agent_operation_resolve",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions", "principal_id": "codex-1"},
        )
    )
    missing_operation_poll = await skill.handle(
        SkillRequest(
            intent="agent_operation_poll",
            user_id="owner-1",
            context={"owner_id": "owner-1", "operation_id": "missing-op"},
        )
    )
    missing_operation_get_id = await skill.handle(
        SkillRequest(intent="agent_operation_get", user_id="owner-1", context={})
    )
    missing_operation_get = await skill.handle(
        SkillRequest(
            intent="agent_operation_get",
            user_id="owner-1",
            context={"operation_id": "missing-op"},
        )
    )
    missing_evidence_operation_id = await skill.handle(
        SkillRequest(intent="agent_operation_evidence_list", user_id="owner-1", context={})
    )
    missing_diagnosis_operation_id = await skill.handle(
        SkillRequest(intent="agent_operation_diagnosis_get", user_id="owner-1", context={})
    )
    missing_logs_operation_id = await skill.handle(
        SkillRequest(intent="agent_operation_logs", user_id="owner-1", context={})
    )
    missing_incidents_operation_id = await skill.handle(
        SkillRequest(intent="agent_operation_incidents_list", user_id="owner-1", context={})
    )
    missing_principal = await skill.handle(
        SkillRequest(
            intent="agent_repo_discover",
            user_id="owner-1",
            context={"principal_id": "codex-1", "allowed_scopes": ["cgs:agent:discover"]},
        )
    )
    denied_scope = await skill.handle(
        SkillRequest(
            intent="agent_repo_discover",
            user_id="owner-1",
            context={"principal_id": "codex-1", "allowed_scopes": []},
        )
    )

    assert missing_doc_slug.success is False
    assert missing_doc_slug.error == "slug is required"
    assert missing_interactions_session.success is False
    assert missing_interactions_session.error == "session_id is required"
    assert missing_gaps_session.success is False
    assert missing_gaps_session.error == "session_id is required"
    assert missing_app_manifest_id.success is False
    assert missing_app_manifest_id.error == "app_id is required"
    assert missing_knowledge_pack.success is False
    assert "Knowledge pack `catalyst-group-solutions` not found" in (
        missing_knowledge_pack.error or ""
    )
    assert missing_services_app.success is False
    assert missing_services_app.error == "app_id is required"
    assert missing_service_read_args.success is False
    assert missing_service_read_args.error == "app_id and service_kind are required"
    assert missing_service_request_args.success is False
    assert missing_service_request_args.error == "app_id, service_kind, and action_id are required"
    assert missing_operation_app.success is False
    assert missing_operation_app.error == "app_id is required"
    assert missing_operation_refs.success is False
    assert "At least one operation reference is required" in (missing_operation_refs.error or "")
    assert missing_operation_poll.success is False
    assert "Operation `missing-op` not found" in (missing_operation_poll.error or "")
    assert missing_operation_get_id.success is False
    assert missing_operation_get_id.error == "operation_id is required"
    assert missing_operation_get.success is False
    assert "Operation `missing-op` not found" in (missing_operation_get.error or "")
    assert missing_evidence_operation_id.success is False
    assert missing_evidence_operation_id.error == "operation_id is required"
    assert missing_diagnosis_operation_id.success is False
    assert missing_diagnosis_operation_id.error == "operation_id is required"
    assert missing_logs_operation_id.success is False
    assert missing_logs_operation_id.error == "operation_id is required"
    assert missing_incidents_operation_id.success is False
    assert missing_incidents_operation_id.error == "operation_id is required"
    assert missing_principal.success is False
    assert "Principal `codex-1` is not registered" in (missing_principal.error or "")
    assert denied_scope.success is False
    assert "not allowed to discover brokered repositories" in (denied_scope.error or "")


@pytest.mark.asyncio
async def test_agent_bootstrap_handle_owner_inference_unknown_intent_and_missing_inputs() -> None:
    storage = _storage()
    skill = _skill(storage)
    skill._infer_owner_id = AsyncMock(side_effect=[None, "owner-inferred"])  # type: ignore[method-assign]

    missing_owner = await skill.handle(
        SkillRequest(
            intent="agent_operation_event_ingest",
            user_id="owner-1",
            context={"app_id": ""},
        )
    )
    inferred_owner = await skill.handle(
        SkillRequest(
            intent="agent_operation_poll",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions"},
        )
    )
    unknown = await skill.handle(
        SkillRequest(intent="agent_unknown", user_id="owner-1", context={})
    )
    missing_client_id = await skill.handle(
        SkillRequest(
            intent="agent_client_bootstrap",
            user_id="owner-1",
            context={"manifest": {"version": "v1"}},
        )
    )
    missing_manifest = await skill.handle(
        SkillRequest(
            intent="agent_client_bootstrap",
            user_id="owner-1",
            context={"client_id": "codex-desktop"},
        )
    )
    missing_manifest_get_id = await skill.handle(
        SkillRequest(intent="agent_client_manifest_get", user_id="owner-1", context={})
    )

    assert missing_owner.success is False
    assert "owner_id or an enrolled app_id is required" in (missing_owner.error or "")
    assert inferred_owner.success is True
    assert inferred_owner.data["operations"] == []
    assert unknown.success is False
    assert "Unknown agent bootstrap intent" in (unknown.error or "")
    assert missing_client_id.success is False
    assert "client_id is required" in (missing_client_id.error or "")
    assert missing_manifest.success is False
    assert "manifest is required" in (missing_manifest.error or "")
    assert missing_manifest_get_id.success is False
    assert "client_id is required" in (missing_manifest_get_id.error or "")


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
    diagnosis = await skill.handle(
        SkillRequest(
            intent="agent_operation_diagnosis_get",
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
    assert diagnosis.data["diagnosis"]["operation_id"] == "op-1"
    assert diagnosis.data["diagnosis"]["evidence_count"] == 1
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
async def test_repo_workspace_plan_publish_and_management_handlers_cover_error_paths() -> None:
    storage = _storage()
    storage.get_workspace_bundle.side_effect = [
        None,
        {
            "bundle_id": "bundle-1",
            "principal_id": "other-principal",
            "app_id": "catalyst-group-solutions",
            "repo_id": "catalyst-group-solutions",
        },
    ]
    storage.get_agent_app_profile.side_effect = [
        None,
        {"app_id": "catalyst-group-solutions", "profile": {}},
    ]

    skill = _skill(storage)
    skill._require_app_access = AsyncMock(  # type: ignore[method-assign]
        side_effect=[
            {"app_id": "catalyst-group-solutions", "profile": {"repo_ids": []}},
            _app_profile(),
            _app_profile(),
            _app_profile(),
        ]
    )
    skill._resolve_repo_profile = AsyncMock(  # type: ignore[method-assign]
        return_value={"repo_id": "catalyst-group-solutions", "default_branch": "main"}
    )
    skill._detect_test_plan_gaps = AsyncMock(  # type: ignore[method-assign]
        return_value=[{"required_capability": "coverage"}]
    )
    skill._apply_publish_candidate = AsyncMock(  # type: ignore[method-assign]
        return_value={"candidate_id": "candidate-1", "status": "applied"}
    )
    skill._enforce_managed_repo = AsyncMock(  # type: ignore[method-assign]
        return_value={"github_repo": "jimtin/catalyst-group-solutions", "enforced": True}
    )

    missing_enroll_repo = await skill.handle(
        SkillRequest(intent="agent_repo_enroll", user_id="owner-1", context={})
    )
    missing_bundle_app = await skill.handle(
        SkillRequest(intent="agent_workspace_bundle_create", user_id="owner-1", context={})
    )
    missing_bundle_repo = await skill.handle(
        SkillRequest(
            intent="agent_workspace_bundle_create",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions", "principal_id": "codex-1"},
        )
    )
    missing_bundle_get_id = await skill.handle(
        SkillRequest(intent="agent_workspace_bundle_get", user_id="owner-1", context={})
    )
    missing_bundle = await skill.handle(
        SkillRequest(
            intent="agent_workspace_bundle_get",
            user_id="owner-1",
            context={"bundle_id": "missing-bundle", "principal_id": "codex-1"},
        )
    )
    wrong_principal_bundle = await skill.handle(
        SkillRequest(
            intent="agent_workspace_bundle_get",
            user_id="owner-1",
            context={"bundle_id": "bundle-1", "principal_id": "codex-1"},
        )
    )
    missing_compile_app = await skill.handle(
        SkillRequest(intent="agent_test_plan_compile", user_id="owner-1", context={})
    )
    compile_gap_blocked = await skill.handle(
        SkillRequest(
            intent="agent_test_plan_compile",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions", "principal_id": "codex-1"},
        )
    )
    missing_publish_app = await skill.handle(
        SkillRequest(intent="agent_publish_candidate_submit", user_id="owner-1", context={})
    )
    missing_publish_base_sha = await skill.handle(
        SkillRequest(
            intent="agent_publish_candidate_submit",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions", "principal_id": "codex-1"},
        )
    )
    missing_publish_diff = await skill.handle(
        SkillRequest(
            intent="agent_publish_candidate_submit",
            user_id="owner-1",
            context={
                "app_id": "catalyst-group-solutions",
                "principal_id": "codex-1",
                "base_sha": "0" * 40,
            },
        )
    )
    missing_apply_candidate = await skill.handle(
        SkillRequest(intent="agent_publish_candidate_apply", user_id="owner-1", context={})
    )
    missing_enforce_target = await skill.handle(
        SkillRequest(intent="agent_managed_repo_enforce", user_id="owner-1", context={})
    )
    missing_enforce_app = await skill.handle(
        SkillRequest(
            intent="agent_managed_repo_enforce",
            user_id="owner-1",
            context={"app_id": "missing-app"},
        )
    )
    missing_enforce_repo = await skill.handle(
        SkillRequest(
            intent="agent_managed_repo_enforce",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions"},
        )
    )

    assert missing_enroll_repo.success is False
    assert missing_enroll_repo.error == "github_repo is required"
    assert missing_bundle_app.success is False
    assert missing_bundle_app.error == "app_id is required"
    assert missing_bundle_repo.success is False
    assert missing_bundle_repo.error == "repo_id is required"
    assert missing_bundle_get_id.success is False
    assert missing_bundle_get_id.error == "bundle_id is required"
    assert missing_bundle.success is False
    assert "Workspace bundle `missing-bundle` not found" in (missing_bundle.error or "")
    assert wrong_principal_bundle.success is False
    assert "not available for this principal" in (wrong_principal_bundle.error or "")
    assert missing_compile_app.success is False
    assert missing_compile_app.error == "app_id is required"
    assert compile_gap_blocked.success is False
    assert "Test plan compile blocked by unresolved capability gaps" in (
        compile_gap_blocked.error or ""
    )
    assert missing_publish_app.success is False
    assert missing_publish_app.error == "app_id is required"
    assert missing_publish_base_sha.success is False
    assert missing_publish_base_sha.error == "base_sha is required"
    assert missing_publish_diff.success is False
    assert missing_publish_diff.error == "diff_text or patch_bundle_base64 is required"
    assert missing_apply_candidate.success is False
    assert missing_apply_candidate.error == "candidate_id is required"
    assert missing_enforce_target.success is False
    assert missing_enforce_target.error == "app_id or github_repo is required"
    assert missing_enforce_app.success is False
    assert "App `missing-app` not found" in (missing_enforce_app.error or "")
    assert missing_enforce_repo.success is False
    assert "does not declare a GitHub repository" in (missing_enforce_repo.error or "")


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


@pytest.mark.asyncio
async def test_connector_detail_and_health_handlers_report_configuration_state() -> None:
    storage = _storage()
    storage.get_external_service_connector.return_value = {
        "connector_id": "clerk-primary",
        "service_kind": "clerk",
        "display_name": "Clerk Primary",
        "auth_kind": "token",
        "policy": {},
        "metadata": {"issuer": "https://clerk.example.com"},
        "active": True,
        "has_secret": True,
    }
    storage.get_service_adapter_capability.return_value = {
        "service_kind": "clerk",
        "manifest": {"ingestion_modes": ["webhook", "poll"]},
    }
    skill = _skill(storage)

    connector = await skill.handle(
        SkillRequest(
            intent="agent_connector_get",
            user_id="owner-1",
            context={"connector_id": "clerk-primary"},
        )
    )
    health = await skill.handle(
        SkillRequest(
            intent="agent_connector_health",
            user_id="owner-1",
            context={"connector_id": "clerk-primary"},
        )
    )

    assert connector.success is True
    assert connector.data["connector"]["connector_id"] == "clerk-primary"
    assert connector.data["capability"]["service_kind"] == "clerk"
    assert connector.data["health"]["status"] == "healthy"
    assert health.success is True
    assert health.data["health"]["auth_configured"] is True
    assert health.data["health"]["blocking_reasons"] == []


@pytest.mark.asyncio
async def test_connector_and_principal_guard_handlers_cover_missing_and_not_found_paths() -> None:
    storage = _storage()
    storage.get_external_service_connector.side_effect = [
        None,
        None,
        None,
    ]
    skill = _skill(storage)

    missing_principal = await skill.handle(
        SkillRequest(intent="agent_principal_upsert", user_id="owner-1", context={})
    )
    missing_connector_upsert_fields = await skill.handle(
        SkillRequest(
            intent="agent_connector_upsert",
            user_id="owner-1",
            context={"connector_id": "github-primary"},
        )
    )
    missing_connector_get_id = await skill.handle(
        SkillRequest(intent="agent_connector_get", user_id="owner-1", context={})
    )
    missing_connector_get = await skill.handle(
        SkillRequest(
            intent="agent_connector_get",
            user_id="owner-1",
            context={"connector_id": "missing-connector"},
        )
    )
    missing_connector_health_id = await skill.handle(
        SkillRequest(intent="agent_connector_health", user_id="owner-1", context={})
    )
    missing_connector_health = await skill.handle(
        SkillRequest(
            intent="agent_connector_health",
            user_id="owner-1",
            context={"connector_id": "missing-connector"},
        )
    )
    missing_rotate_args = await skill.handle(
        SkillRequest(
            intent="agent_connector_rotate",
            user_id="owner-1",
            context={"connector_id": "github-primary"},
        )
    )
    missing_rotate_connector = await skill.handle(
        SkillRequest(
            intent="agent_connector_rotate",
            user_id="owner-1",
            context={"connector_id": "missing-connector", "secret_value": "secret-2"},
        )
    )
    missing_grants_principal = await skill.handle(
        SkillRequest(intent="agent_principal_grants_put", user_id="owner-1", context={})
    )
    missing_app_id = await skill.handle(
        SkillRequest(intent="agent_app_upsert", user_id="owner-1", context={})
    )

    assert missing_principal.success is False
    assert missing_principal.error == "principal_id is required"
    assert missing_connector_upsert_fields.success is False
    assert missing_connector_upsert_fields.error == "connector_id and service_kind are required"
    assert missing_connector_get_id.success is False
    assert missing_connector_get_id.error == "connector_id is required"
    assert missing_connector_get.success is False
    assert "Connector `missing-connector` not found" in (missing_connector_get.error or "")
    assert missing_connector_health_id.success is False
    assert missing_connector_health_id.error == "connector_id is required"
    assert missing_connector_health.success is False
    assert "Connector `missing-connector` not found" in (missing_connector_health.error or "")
    assert missing_rotate_args.success is False
    assert missing_rotate_args.error == "connector_id and secret_value are required"
    assert missing_rotate_connector.success is False
    assert "Connector `missing-connector` not found" in (missing_rotate_connector.error or "")
    assert missing_grants_principal.success is False
    assert missing_grants_principal.error == "principal_id is required"
    assert missing_app_id.success is False
    assert missing_app_id.error == "app_id is required"


@pytest.mark.asyncio
async def test_handle_records_failed_outcomes_when_handlers_raise_value_error() -> None:
    storage = _storage()
    skill = _skill(storage)
    skill._handle_docs_list = AsyncMock(side_effect=ValueError("docs failed"))  # type: ignore[method-assign]

    response = await skill.handle(
        SkillRequest(intent="agent_docs_list", user_id="owner-1", context={})
    )

    assert response.success is False
    assert response.error == "docs failed"
    skill._record_interaction_outcome.assert_awaited()
    kwargs = skill._record_interaction_outcome.await_args.kwargs
    assert kwargs["success"] is False
    assert kwargs["error_message"] == "docs failed"


@pytest.mark.asyncio
async def test_app_manifest_and_services_handlers_cover_success_and_skip_paths() -> None:
    storage = _storage()
    storage.get_agent_knowledge_pack.return_value = {
        "app_id": "catalyst-group-solutions",
        "pack": {"workspace_manifest": {"entrypoint": "main.py"}},
    }
    storage.list_agent_gap_events.return_value = [
        {"gap_id": "gap-1", "blocker": True},
        {"gap_id": "gap-2", "blocker": False},
    ]
    storage.get_agent_docs_manifest.side_effect = [
        {"slug": "cgs-ai-api-quickstart", "title": "Quickstart"},
        None,
    ]
    app_profile = {
        **_app_profile(),
        "profile": {
            **dict(_app_profile()["profile"]),
            "docs_slugs": ["cgs-ai-api-quickstart", "missing-doc"],
        },
    }

    skill = _skill(storage)
    skill._require_app_access = AsyncMock(return_value=app_profile)  # type: ignore[method-assign]

    manifest = await skill.handle(
        SkillRequest(
            intent="agent_app_manifest_get",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions", "principal_id": "codex-1"},
        )
    )
    services = await skill.handle(
        SkillRequest(
            intent="agent_app_services_list",
            user_id="owner-1",
            context={
                "app_id": "catalyst-group-solutions",
                "principal_id": "codex-1",
                "public_base_url": "https://cgs.example.com",
            },
        )
    )

    assert manifest.success is True
    assert manifest.data["docs"] == [{"slug": "cgs-ai-api-quickstart", "title": "Quickstart"}]
    assert manifest.data["knowledge_pack"]["pack"]["known_gaps_summary"] == {
        "open_total": 2,
        "blocker_total": 1,
        "recent_gap_ids": ["gap-1", "gap-2"],
    }
    assert services.success is True
    assert services.data["services"][0]["routes"]["actions"].startswith("https://cgs.example.com/")


@pytest.mark.asyncio
async def test_docs_and_operation_detail_handlers_cover_filtered_and_not_found_paths() -> None:
    storage = _storage()
    storage.list_agent_docs_manifests.return_value = [
        {
            "slug": "cgs-ai-api-quickstart",
            "title": "Quickstart",
            "manifest": {"content_markdown": "# Quickstart"},
        }
    ]
    storage.get_agent_docs_manifest.return_value = None
    storage.list_managed_operations.return_value = [
        {"operation_id": "op-1", "app_id": "other-app"},
    ]
    storage.get_managed_operation.side_effect = [None, None, None, None]

    skill = _skill(storage)
    skill._require_app_access = AsyncMock(return_value=_app_profile())  # type: ignore[method-assign]
    skill._list_accessible_apps = AsyncMock(return_value=[])  # type: ignore[method-assign]

    docs_list = await skill.handle(
        SkillRequest(
            intent="agent_docs_list",
            user_id="owner-1",
            context={
                "app_id": "catalyst-group-solutions",
                "principal_id": "codex-1",
                "query": "no-match",
            },
        )
    )
    missing_docs = await skill.handle(
        SkillRequest(
            intent="agent_docs_get",
            user_id="owner-1",
            context={"slug": "missing-doc"},
        )
    )
    filtered_operations = await skill.handle(
        SkillRequest(
            intent="agent_operation_list",
            user_id="owner-1",
            context={"principal_id": "codex-1", "app_id": "catalyst-group-solutions"},
        )
    )
    missing_evidence = await skill.handle(
        SkillRequest(
            intent="agent_operation_evidence_list",
            user_id="owner-1",
            context={"operation_id": "missing-op", "principal_id": "codex-1"},
        )
    )
    missing_diagnosis = await skill.handle(
        SkillRequest(
            intent="agent_operation_diagnosis_get",
            user_id="owner-1",
            context={"operation_id": "missing-op", "principal_id": "codex-1"},
        )
    )
    missing_logs = await skill.handle(
        SkillRequest(
            intent="agent_operation_logs",
            user_id="owner-1",
            context={"operation_id": "missing-op", "principal_id": "codex-1"},
        )
    )
    missing_incidents = await skill.handle(
        SkillRequest(
            intent="agent_operation_incidents_list",
            user_id="owner-1",
            context={"operation_id": "missing-op", "principal_id": "codex-1"},
        )
    )

    assert docs_list.success is True
    assert docs_list.data["docs"] == []
    assert missing_docs.success is False
    assert "Docs manifest `missing-doc` not found" in (missing_docs.error or "")
    storage.record_agent_gap_event.assert_not_awaited()
    assert filtered_operations.success is True
    assert filtered_operations.data["operations"] == []
    assert missing_evidence.success is False
    assert "Operation `missing-op` not found" in (missing_evidence.error or "")
    assert missing_diagnosis.success is False
    assert "Operation `missing-op` not found" in (missing_diagnosis.error or "")
    assert missing_logs.success is False
    assert "Operation `missing-op` not found" in (missing_logs.error or "")
    assert missing_incidents.success is False
    assert "Operation `missing-op` not found" in (missing_incidents.error or "")


@pytest.mark.asyncio
async def test_handle_operation_poll_without_inferred_owner_and_docs_get_with_app_access() -> None:
    storage = _storage()
    storage.get_agent_docs_manifest.return_value = {
        "slug": "cgs-ai-api-quickstart",
        "title": "Quickstart",
    }

    skill = _skill(storage)
    skill._infer_owner_id = AsyncMock(return_value=None)  # type: ignore[method-assign]
    skill._handle_operation_poll = AsyncMock(  # type: ignore[method-assign]
        return_value=SkillResponse(
            request_id="req-poll",
            message="poll ok",
            data={"status": "queued"},
        )
    )
    skill._require_app_access = AsyncMock(  # type: ignore[method-assign]
        return_value={
            **_app_profile(),
            "profile": {
                **dict(_app_profile()["profile"]),
                "docs_slugs": ["cgs-ai-api-quickstart"],
            },
        }
    )

    poll = await skill.handle(
        SkillRequest(
            intent="agent_operation_poll",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions"},
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

    assert poll.success is True
    assert poll.data["status"] == "queued"
    skill._infer_owner_id.assert_awaited_once()
    skill._handle_operation_poll.assert_awaited_once()
    assert docs_get.success is True
    assert docs_get.data["doc"]["slug"] == "cgs-ai-api-quickstart"


@pytest.mark.asyncio
async def test_docs_and_operation_handlers_query_without_principal_guard_checks() -> None:
    storage = _storage()
    storage.list_agent_docs_manifests.return_value = [
        {
            "slug": "quickstart",
            "title": "Quickstart",
            "manifest": {"content_markdown": "# Quickstart\nInstall"},
        },
        {
            "slug": "runbook",
            "title": "Internal Runbook",
            "manifest": {"content_markdown": "# Runbook"},
        },
    ]
    storage.list_managed_operations.return_value = [
        {"operation_id": "op-1", "app_id": "catalyst-group-solutions"}
    ]
    storage.get_agent_docs_manifest.return_value = {
        "slug": "quickstart",
        "title": "Quickstart",
        "manifest": {"content_markdown": "# Quickstart\nInstall"},
    }
    storage.get_operation_hydrated.return_value = {
        "operation_id": "op-1",
        "app_id": "catalyst-group-solutions",
    }
    storage.get_managed_operation.return_value = {
        "operation_id": "op-1",
        "app_id": "catalyst-group-solutions",
    }
    storage.list_operation_evidence.return_value = [{"evidence_id": "ev-1"}]
    storage.get_operation_log_chunks.return_value = [{"chunk_id": "log-1"}]
    storage.list_operation_incidents.return_value = [{"incident_id": "inc-1"}]
    storage.list_operation_refs.return_value = [{"ref_kind": "git_sha", "ref_value": "1" * 40}]

    skill = _skill(storage)
    skill._require_app_access = AsyncMock(return_value=_app_profile())  # type: ignore[method-assign]
    skill._list_accessible_apps = AsyncMock(return_value=[_app_profile()])  # type: ignore[method-assign]

    docs_list_unfiltered = await skill.handle(
        SkillRequest(
            intent="agent_docs_list",
            user_id="owner-1",
            context={},
        )
    )
    docs_list = await skill.handle(
        SkillRequest(
            intent="agent_docs_list",
            user_id="owner-1",
            context={"query": "quickstart"},
        )
    )
    docs_get = await skill.handle(
        SkillRequest(
            intent="agent_docs_get",
            user_id="owner-1",
            context={"slug": "quickstart"},
        )
    )
    listed = await skill.handle(
        SkillRequest(
            intent="agent_operation_list",
            user_id="owner-1",
            context={"app_id": "catalyst-group-solutions"},
        )
    )
    loaded = await skill.handle(
        SkillRequest(
            intent="agent_operation_get",
            user_id="owner-1",
            context={"operation_id": "op-1"},
        )
    )
    evidence = await skill.handle(
        SkillRequest(
            intent="agent_operation_evidence_list",
            user_id="owner-1",
            context={"operation_id": "op-1"},
        )
    )
    diagnosis = await skill.handle(
        SkillRequest(
            intent="agent_operation_diagnosis_get",
            user_id="owner-1",
            context={"operation_id": "op-1"},
        )
    )
    logs = await skill.handle(
        SkillRequest(
            intent="agent_operation_logs",
            user_id="owner-1",
            context={"operation_id": "op-1"},
        )
    )
    incidents = await skill.handle(
        SkillRequest(
            intent="agent_operation_incidents_list",
            user_id="owner-1",
            context={"operation_id": "op-1"},
        )
    )

    assert docs_list_unfiltered.success is True
    assert [doc["slug"] for doc in docs_list_unfiltered.data["docs"]] == [
        "quickstart",
        "runbook",
    ]
    assert docs_list.success is True
    assert [doc["slug"] for doc in docs_list.data["docs"]] == ["quickstart"]
    assert docs_get.success is True
    assert docs_get.data["doc"]["slug"] == "quickstart"
    assert listed.data["operations"][0]["incident_count"] == 1
    assert loaded.data["operation"]["operation_id"] == "op-1"
    assert evidence.data["evidence"] == [{"evidence_id": "ev-1"}]
    assert diagnosis.data["diagnosis"]["operation_id"] == "op-1"
    assert logs.data["logs"] == [{"chunk_id": "log-1"}]
    assert incidents.data["incidents"] == [{"incident_id": "inc-1"}]
    skill._list_accessible_apps.assert_not_awaited()
    skill._require_app_access.assert_not_awaited()


@pytest.mark.asyncio
async def test_operation_poll_skips_non_hydrated_results_and_event_ingest_requires_fields() -> None:
    storage = _storage()
    storage.get_managed_operation.return_value = {
        "operation_id": "op-1",
        "app_id": "catalyst-group-solutions",
    }
    storage.get_operation_hydrated.return_value = None

    skill = _skill(storage)
    skill._require_app_access = AsyncMock(return_value=_app_profile())  # type: ignore[method-assign]
    skill._refresh_operation = AsyncMock()  # type: ignore[method-assign]

    poll = await skill.handle(
        SkillRequest(
            intent="agent_operation_poll",
            user_id="owner-1",
            context={"owner_id": "owner-1", "operation_id": "op-1"},
        )
    )
    missing_event_fields = await skill.handle(
        SkillRequest(
            intent="agent_operation_event_ingest",
            user_id="owner-1",
            context={"owner_id": "owner-1", "app_id": "catalyst-group-solutions"},
        )
    )

    assert poll.success is True
    assert poll.data["operations"] == []
    skill._refresh_operation.assert_awaited_once()
    assert missing_event_fields.success is False
    assert missing_event_fields.error == "app_id and service_kind are required"


@pytest.mark.asyncio
async def test_managed_repo_and_payload_guard_handlers_cover_remaining_branches() -> None:
    storage = _storage()
    storage.get_agent_gap_event.return_value = None
    storage.update_agent_gap_event.return_value = None

    skill = _skill(storage)
    skill._enforce_managed_repo = AsyncMock(  # type: ignore[method-assign]
        return_value={"github_repo": "jimtin/zetherion-ai", "enforced": True}
    )

    enforce_repo_only = await skill.handle(
        SkillRequest(
            intent="agent_managed_repo_enforce",
            user_id="owner-1",
            context={"github_repo": "jimtin/zetherion-ai", "default_branch": "main"},
        )
    )
    missing_knowledge_pack = await skill.handle(
        SkillRequest(intent="agent_knowledge_pack_upsert", user_id="owner-1", context={})
    )
    missing_secret_ref = await skill.handle(
        SkillRequest(intent="agent_secret_ref_upsert", user_id="owner-1", context={})
    )
    missing_gap_id = await skill.handle(
        SkillRequest(intent="agent_gap_get", user_id="owner-1", context={})
    )
    missing_gap = await skill.handle(
        SkillRequest(
            intent="agent_gap_get",
            user_id="owner-1",
            context={"gap_id": "missing-gap"},
        )
    )
    missing_gap_update_args = await skill.handle(
        SkillRequest(intent="agent_gap_update", user_id="owner-1", context={})
    )
    missing_gap_update = await skill.handle(
        SkillRequest(
            intent="agent_gap_update",
            user_id="owner-1",
            context={"gap_id": "missing-gap", "status": "resolved"},
        )
    )

    assert enforce_repo_only.success is True
    assert enforce_repo_only.data["github_repo"] == "jimtin/zetherion-ai"
    assert missing_knowledge_pack.success is False
    assert missing_knowledge_pack.error == "app_id and pack are required"
    assert missing_secret_ref.success is False
    assert missing_secret_ref.error == "secret_ref_id and purpose are required"
    assert missing_gap_id.success is False
    assert missing_gap_id.error == "gap_id is required"
    assert missing_gap.success is False
    assert "Gap `missing-gap` not found" in (missing_gap.error or "")
    assert missing_gap_update_args.success is False
    assert missing_gap_update_args.error == "gap_id and status are required"
    assert missing_gap_update.success is False
    assert "Gap `missing-gap` not found" in (missing_gap_update.error or "")


@pytest.mark.asyncio
async def test_principal_upsert_records_json_safe_audit_payloads() -> None:
    storage = _storage()
    principal_uuid = uuid4()
    storage.upsert_agent_principal.return_value = {
        "principal_id": "codex-1",
        "display_name": "Codex 1",
        "metadata": {"principal_uuid": principal_uuid},
    }
    skill = _skill_with_real_outcome_recording(storage)

    response = await skill.handle(
        SkillRequest(
            intent="agent_principal_upsert",
            user_id="owner-1",
            context={"principal_id": "codex-1", "display_name": "Codex 1"},
        )
    )

    assert response.success is True
    create_action_kwargs = storage.create_agent_action.await_args.kwargs
    assert create_action_kwargs["payload"]["request_id"] == str(response.request_id)
    create_outcome_kwargs = storage.create_agent_outcome.await_args.kwargs
    assert create_outcome_kwargs["payload"]["principal"]["metadata"]["principal_uuid"] == str(
        principal_uuid
    )
