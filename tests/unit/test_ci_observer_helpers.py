from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.ci_observer import (
    CiObserverSkill,
    _active_capacity_by_host,
    _build_github_security_summary,
    _build_storage_report,
    _build_vercel_report,
    _host_blocking_reasons,
    _increment_severity_bucket,
    _normalize_owner_id,
    _normalize_security_severity,
    _parse_github_repo,
    _resource_reservation_for_shard,
    _round_robin_candidates_by_repo,
    _scheduler_overview,
    _storage_budget_policy_from_receipts,
    _storage_categories_for_worker,
    _storage_cleanup_receipt_from_bundle,
    _storage_incidents_for_worker,
    _summarize_code_scanning_alerts,
    _summarize_dependabot_alerts,
)
from zetherion_ai.skills.github.client import GitHubAPIError


def _storage() -> MagicMock:
    storage = MagicMock()
    storage.get_run_events = AsyncMock(return_value=[])
    storage.get_run_log_chunks = AsyncMock(return_value=[])
    storage.get_run_debug_bundle = AsyncMock(return_value={})
    storage.get_run_report = AsyncMock(return_value={})
    storage.get_run_graph = AsyncMock(return_value={})
    storage.get_run_correlation_context = AsyncMock(return_value={})
    storage.get_run_diagnostics = AsyncMock(return_value={})
    storage.get_run_artifacts = AsyncMock(return_value=[])
    storage.get_run_evidence = AsyncMock(return_value=[])
    storage.list_agent_coaching_feedback = AsyncMock(return_value=[])
    storage.get_reporting_summary = AsyncMock(return_value={})
    storage.get_reporting_readiness = AsyncMock(return_value={})
    storage.list_repo_profiles = AsyncMock(return_value=[])
    storage.list_runs = AsyncMock(return_value=[])
    storage.list_worker_nodes = AsyncMock(return_value=[])
    storage.get_worker_resource_report = AsyncMock(return_value={"samples": []})
    storage.get_project_resource_report = AsyncMock(return_value={})
    storage.get_project_failure_report = AsyncMock(return_value={})
    return storage


def test_ci_observer_helper_paths_cover_owner_and_resource_defaults() -> None:
    assert (
        _normalize_owner_id(SkillRequest(user_id="user-1", context={"owner_id": "owner-1"}))
        == "owner-1"
    )
    assert (
        _normalize_owner_id(SkillRequest(user_id="user-1", context={"operator_id": "operator-1"}))
        == "operator-1"
    )
    assert _normalize_owner_id(SkillRequest(user_id="")) == "owner"

    explicit = _resource_reservation_for_shard(
        "zetherion-ai",
        {
            "shard_id": "shard-1",
            "metadata": {
                "resource_reservation": {
                    "resource_class": "service",
                    "units": 2,
                    "parallel_group": "db",
                }
            },
        },
    )
    assert explicit.repo_id == "zetherion-ai"
    assert explicit.shard_id == "shard-1"
    assert explicit.resource_class == "service"
    assert explicit.parallel_group == "db"

    fallback = _resource_reservation_for_shard(
        "zetherion-ai",
        {
            "shard_id": "shard-2",
            "lane_id": "lint",
            "execution_target": "local_mac",
            "metadata": {
                "resource_class": "cpu",
                "resource_units": 0,
                "workspace_root": "/tmp/workspace",
            },
        },
    )
    assert fallback.units == 1
    assert fallback.workspace_root == "/tmp/workspace"
    assert fallback.metadata["lane_id"] == "lint"

    assert _host_blocking_reasons(
        {"cpu": 2, "service": 1, "serial": 0},
        {"cpu": 2, "service": 1, "serial": 0},
    ) == [
        "cpu budget exhausted (2/2)",
        "service budget exhausted (1/1)",
    ]


def test_ci_observer_capacity_and_scheduler_overview_cover_blocked_candidates() -> None:
    runs = [
        {
            "run_id": "run-active",
            "repo_id": "repo-a",
            "plan": {
                "host_capacity_policy": {
                    "host_id": "windows-main",
                    "resource_budget": {"cpu": 1, "service": 1, "serial": 1},
                    "reserve_runtime_headroom": True,
                    "workspace_root": "C:/ZetherionCI",
                    "runtime_root": "C:/ZetherionAI",
                }
            },
            "shards": [
                {
                    "shard_id": "active-1",
                    "status": "running",
                    "metadata": {"resource_class": "cpu", "parallel_group": "lint"},
                },
                {
                    "shard_id": "ignored-1",
                    "status": "succeeded",
                    "metadata": {"resource_class": "service"},
                },
            ],
        },
        {
            "run_id": "run-pending",
            "repo_id": "repo-b",
            "metadata": {
                "host_capacity_policy": {
                    "host_id": "windows-main",
                    "resource_budget": {"cpu": 1, "service": 1, "serial": 1},
                }
            },
            "shards": [
                {
                    "shard_id": "pending-1",
                    "lane_id": "unit",
                    "status": "queued_local",
                    "metadata": {"resource_class": "cpu", "parallel_group": "lint"},
                },
                {
                    "shard_id": "pending-2",
                    "lane_id": "service",
                    "status": "planned",
                    "metadata": {"resource_class": "service", "parallel_group": "db"},
                },
            ],
        },
    ]

    capacity = _active_capacity_by_host(runs)
    assert capacity["windows-main"]["resource_usage"] == {"cpu": 1, "service": 0, "serial": 0}
    assert capacity["windows-main"]["active_runs"] == {"run-active"}
    assert capacity["windows-main"]["busy_parallel_groups"] == {"lint"}

    overview = _scheduler_overview(runs)
    host = overview.hosts[0]
    assert host.host_id == "windows-main"
    assert host.pending_shard_count == 2
    assert host.blocked_candidates[0].parallel_group == "lint"
    assert "parallel group `lint` already active" in host.blocked_candidates[0].blocking_reasons
    assert host.admitted_candidates[0].lane_id == "service"
    assert host.metadata["workspace_root"] == "C:/ZetherionCI"
    assert host.metadata["runtime_root"] == "C:/ZetherionAI"


def test_ci_observer_round_robin_and_scheduler_cover_all_blocked_candidates() -> None:
    assert _round_robin_candidates_by_repo(
        {
            "repo-a": [{"lane_id": "a-1"}, {"lane_id": "a-2"}],
            "repo-b": [{"lane_id": "b-1"}],
            "repo-c": [],
        }
    ) == [
        {"lane_id": "a-1"},
        {"lane_id": "b-1"},
        {"lane_id": "a-2"},
    ]

    overview = _scheduler_overview(
        [
            {
                "run_id": "run-blocked",
                "repo_id": "repo-a",
                "plan": {
                    "host_capacity_policy": {
                        "host_id": "windows-main",
                        "resource_budget": {"cpu": 1, "service": 1, "serial": 0},
                        "reserve_runtime_headroom": True,
                    }
                },
                "shards": [
                    {
                        "shard_id": "active-unit",
                        "lane_id": "unit",
                        "status": "running",
                        "metadata": {"resource_class": "cpu", "parallel_group": "unit"},
                    },
                    {
                        "shard_id": "queued-unit",
                        "lane_id": "unit-queued",
                        "status": "queued_local",
                        "metadata": {"resource_class": "cpu", "parallel_group": "unit"},
                    },
                ],
            }
        ]
    )

    host = overview.hosts[0]
    assert host.admitted_candidates == []
    assert host.blocked_candidates[0].blocking_reasons == [
        "cpu budget exhausted (1/1)",
        "parallel group `unit` already active",
    ]
    assert "parallel group `unit` already active" in host.blocking_reasons


@pytest.mark.asyncio
async def test_ci_observer_handle_validates_required_ids_and_unknown_intent() -> None:
    skill = CiObserverSkill(storage=_storage())

    missing_run = await skill.handle(
        SkillRequest(intent="ci_run_report", user_id="owner-1", context={})
    )
    missing_repo = await skill.handle(
        SkillRequest(intent="ci_reporting_project_resources", user_id="owner-1", context={})
    )
    missing_node = await skill.handle(
        SkillRequest(intent="ci_reporting_worker_resources", user_id="owner-1", context={})
    )
    missing_graph = await skill.handle(
        SkillRequest(intent="ci_run_graph", user_id="owner-1", context={})
    )
    missing_correlation = await skill.handle(
        SkillRequest(intent="ci_run_correlation_context", user_id="owner-1", context={})
    )
    missing_diagnostics = await skill.handle(
        SkillRequest(intent="ci_run_diagnostics", user_id="owner-1", context={})
    )
    missing_artifacts = await skill.handle(
        SkillRequest(intent="ci_run_artifacts", user_id="owner-1", context={})
    )
    missing_evidence = await skill.handle(
        SkillRequest(intent="ci_run_evidence", user_id="owner-1", context={})
    )
    missing_coaching = await skill.handle(
        SkillRequest(intent="ci_run_coaching", user_id="owner-1", context={})
    )
    unknown = await skill.handle(
        SkillRequest(intent="ci_reporting_unknown", user_id="owner-1", context={})
    )

    assert missing_run.success is False
    assert "run_id is required" in str(missing_run.error)
    assert missing_repo.success is False
    assert "repo_id is required" in str(missing_repo.error)
    assert missing_node.success is False
    assert "node_id is required" in str(missing_node.error)
    assert missing_graph.success is False
    assert "run_id is required" in str(missing_graph.error)
    assert missing_correlation.success is False
    assert "run_id is required" in str(missing_correlation.error)
    assert missing_diagnostics.success is False
    assert "run_id is required" in str(missing_diagnostics.error)
    assert missing_artifacts.success is False
    assert "run_id is required" in str(missing_artifacts.error)
    assert missing_evidence.success is False
    assert "run_id is required" in str(missing_evidence.error)
    assert missing_coaching.success is False
    assert "run_id is required" in str(missing_coaching.error)
    assert unknown.success is False
    assert "Unknown CI observer intent" in str(unknown.error)


@pytest.mark.asyncio
async def test_ci_observer_readiness_reports_github_security_as_unavailable_without_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _storage()
    storage.get_reporting_readiness.return_value = {
        "workspace_readiness": {"merge_ready": True, "deploy_ready": True},
        "repo_readiness": [{"repo_id": "zetherion-ai"}],
    }
    storage.list_repo_profiles.return_value = [
        {"repo_id": "zetherion-ai", "github_repo": "jimtin/zetherion-ai"}
    ]
    skill = CiObserverSkill(storage=storage)

    from zetherion_ai.skills import ci_observer

    monkeypatch.setattr(
        ci_observer,
        "get_settings",
        lambda: SimpleNamespace(github_token=None),
    )
    response = await skill.handle(SkillRequest(intent="ci_reporting_readiness", user_id="owner-1"))

    assert response.success is True
    github_security = response.data["readiness"]["github_security"]
    assert github_security["status"] == "unavailable"
    assert github_security["reason"] == "github_token_missing"
    assert response.data["readiness"]["repo_readiness"][0]["github_security"] is None


@pytest.mark.asyncio
async def test_ci_observer_readiness_includes_github_security_alert_summary(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    storage = _storage()
    storage.get_reporting_readiness.return_value = {
        "workspace_readiness": {"merge_ready": True, "deploy_ready": True},
        "repo_readiness": [{"repo_id": "zetherion-ai"}],
    }
    storage.list_repo_profiles.return_value = [
        {"repo_id": "zetherion-ai", "github_repo": "jimtin/zetherion-ai"}
    ]
    skill = CiObserverSkill(storage=storage)

    class _FakeSecret:
        def get_secret_value(self) -> str:
            return "ghp_test_token"

    class _FakeGitHubClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def list_dependabot_alerts(self, owner: str, repo: str) -> list[dict[str, object]]:
            assert (owner, repo) == ("jimtin", "zetherion-ai")
            return [
                {"security_vulnerability": {"severity": "high", "package": {"ecosystem": "pip"}}}
            ]

        async def list_code_scanning_alerts(self, owner: str, repo: str) -> list[dict[str, object]]:
            assert (owner, repo) == ("jimtin", "zetherion-ai")
            return [{"rule": {"security_severity_level": "medium"}, "tool": {"name": "CodeQL"}}]

        async def close(self) -> None:
            return None

    from zetherion_ai.skills import ci_observer

    monkeypatch.setattr(
        ci_observer,
        "get_settings",
        lambda: SimpleNamespace(github_token=_FakeSecret()),
    )
    monkeypatch.setattr(ci_observer, "GitHubClient", _FakeGitHubClient)
    response = await skill.handle(SkillRequest(intent="ci_reporting_readiness", user_id="owner-1"))

    assert response.success is True
    github_security = response.data["readiness"]["github_security"]
    assert github_security["status"] == "blocked"
    assert github_security["totals"]["critical_or_high"] == 1
    repo_summary = response.data["readiness"]["repo_readiness"][0]["github_security"]
    assert repo_summary["dependabot"]["open_count"] == 1
    assert repo_summary["code_scanning"]["open_count"] == 1
    assert repo_summary["status"] == "blocked"


def test_ci_observer_security_helpers_normalize_repo_and_severity_shapes() -> None:
    assert _parse_github_repo("jimtin/zetherion-ai") == ("jimtin", "zetherion-ai")
    assert _parse_github_repo("jimtin/") is None
    assert _parse_github_repo("not-a-repo") is None

    assert _normalize_security_severity("critical") == "critical"
    assert _normalize_security_severity("error") == "high"
    assert _normalize_security_severity("moderate") == "medium"
    assert _normalize_security_severity("warning") == "medium"
    assert _normalize_security_severity("note") == "low"
    assert _normalize_security_severity("something-else") == "unknown"

    custom_bucket = {"critical": 1}
    _increment_severity_bucket(custom_bucket, "informational")
    assert custom_bucket["informational"] == 1

    dependabot = _summarize_dependabot_alerts(
        [
            {
                "security_vulnerability": {
                    "severity": "moderate",
                    "package": {"ecosystem": "pip"},
                }
            },
            {
                "security_vulnerability": {
                    "severity": "critical",
                    "package": {"ecosystem": "npm"},
                }
            },
            {"security_vulnerability": {"package": {"ecosystem": "npm"}}},
        ]
    )
    assert dependabot["open_count"] == 3
    assert dependabot["severity_counts"]["medium"] == 1
    assert dependabot["severity_counts"]["critical"] == 1
    assert dependabot["severity_counts"]["unknown"] == 1
    assert dependabot["ecosystems"] == {"pip": 1, "npm": 2}

    code_scanning = _summarize_code_scanning_alerts(
        [
            {
                "rule": {"security_severity_level": "high"},
                "tool": {"name": "CodeQL"},
            },
            {
                "rule": {"severity": "warning"},
                "tool": {"name": "Semgrep"},
            },
            {"rule": {}, "tool": {}},
        ]
    )
    assert code_scanning["open_count"] == 3
    assert code_scanning["severity_counts"]["high"] == 1
    assert code_scanning["severity_counts"]["medium"] == 1
    assert code_scanning["severity_counts"]["unknown"] == 1
    assert code_scanning["tools"] == {"codeql": 1, "semgrep": 1}


@pytest.mark.asyncio
async def test_build_github_security_summary_handles_api_errors_and_invalid_repo_profiles(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _FakeSecret:
        def get_secret_value(self) -> str:
            return "ghp_test_token"

    class _FakeGitHubClient:
        def __init__(self, token: str) -> None:
            self.token = token

        async def list_dependabot_alerts(self, owner: str, repo: str) -> list[dict[str, object]]:
            raise GitHubAPIError("dependabot unavailable", status_code=503)

        async def list_code_scanning_alerts(self, owner: str, repo: str) -> list[dict[str, object]]:
            assert (owner, repo) == ("jimtin", "zetherion-ai")
            return [
                {
                    "rule": {"security_severity_level": "low"},
                    "tool": {"name": "CodeQL"},
                }
            ]

        async def close(self) -> None:
            return None

    from zetherion_ai.skills import ci_observer

    monkeypatch.setattr(
        ci_observer,
        "get_settings",
        lambda: SimpleNamespace(github_token=_FakeSecret()),
    )
    monkeypatch.setattr(ci_observer, "GitHubClient", _FakeGitHubClient)

    summary = await _build_github_security_summary(
        [
            {"repo_id": "bad", "github_repo": "not-a-repo"},
            {"repo_id": "zetherion-ai", "github_repo": "jimtin/zetherion-ai"},
        ]
    )

    assert summary["status"] == "degraded"
    assert summary["available"] is True
    assert summary["blocking"] is False
    assert summary["repos"][0]["repo_id"] == "zetherion-ai"
    assert summary["repos"][0]["errors"] == ["dependabot:503"]
    assert summary["repos"][0]["code_scanning"]["open_count"] == 1
    assert summary["totals"]["open_dependabot"] == 0
    assert summary["totals"]["open_code_scanning"] == 1


def test_ci_observer_storage_helpers_cover_default_and_pressure_paths() -> None:
    cleanup_receipt = _storage_cleanup_receipt_from_bundle(
        {
            "bundle": {
                "cleanup_receipt": {
                    "status": "cleanup_degraded",
                    "path": "C:/cleanup/receipt.json",
                    "deleted_paths": ["C:/workspaces/run-1/.artifacts/a.json"],
                    "pruned_logs": ["C:/logs/old.log"],
                    "docker_actions": [{"resource_kind": "image"}],
                    "warnings": ["disk pressure remains high"],
                    "low_disk_free_bytes": 1024,
                    "target_free_bytes": 2048,
                    "artifact_retention_hours": 12,
                    "log_retention_days": 3,
                    "cleanup_enabled": False,
                }
            }
        }
    )
    assert cleanup_receipt is not None
    assert cleanup_receipt.status == "cleanup_degraded"
    assert cleanup_receipt.metadata["cleanup_enabled"] is False

    default_policy = _storage_budget_policy_from_receipts([])
    assert default_policy.cleanup_enabled is True

    policy = _storage_budget_policy_from_receipts([cleanup_receipt])
    assert policy.low_disk_free_bytes == 1024
    assert policy.target_free_bytes == 2048
    assert policy.cleanup_enabled is False

    worker = {
        "node_id": "windows-main",
        "metadata": {
            "workspace_root": "C:/ZetherionCI/workspaces",
            "runtime_root": "C:/ZetherionCI/runtime",
        },
    }
    latest_sample = {"disk_used_bytes": 4096, "disk_free_bytes": 512}
    categories = _storage_categories_for_worker(
        worker=worker,
        latest_sample=latest_sample,
        cleanup_receipt=cleanup_receipt,
    )
    assert [category.category for category in categories] == [
        "workspace_roots",
        "runtime_roots",
        "retained_artifacts",
        "worker_logs",
        "docker_cache",
    ]

    incidents = _storage_incidents_for_worker(
        worker=worker,
        latest_sample=latest_sample,
        policy=policy,
        cleanup_receipt=cleanup_receipt,
    )
    assert {incident.severity for incident in incidents} == {"high", "medium"}
    assert any(incident.blocking for incident in incidents)
    assert any(incident.evidence_refs == ["C:/cleanup/receipt.json"] for incident in incidents)

    target_only_incidents = _storage_incidents_for_worker(
        worker=worker,
        latest_sample={"disk_free_bytes": 1536},
        policy=policy,
        cleanup_receipt=None,
    )
    assert len(target_only_incidents) == 1
    assert target_only_incidents[0].severity == "medium"
    assert target_only_incidents[0].blocking is False


@pytest.mark.asyncio
async def test_ci_observer_storage_and_vercel_report_helpers_cover_alerting_paths() -> None:
    storage = _storage()
    storage.list_repo_profiles.return_value = [
        {"repo_id": "", "display_name": "Skip Me"},
        {"repo_id": "repo-a", "display_name": "Repo A"},
        {"repo_id": "repo-b", "display_name": "Repo B"},
    ]
    storage.list_runs.return_value = [
        {"run_id": "", "repo_id": "repo-a", "metadata": {}, "plan": {}},
        {"run_id": "run-1", "repo_id": "repo-a", "metadata": {}, "plan": {}},
    ]
    storage.get_run_debug_bundle.return_value = {
        "bundle": {
            "cleanup_receipt": {
                "status": "cleanup_degraded",
                "path": "C:/cleanup/receipt.json",
                "deleted_paths": ["C:/repo-a/.artifacts/a.json"],
                "pruned_logs": ["C:/logs/a.log"],
                "docker_actions": [{"resource_kind": "image"}],
                "warnings": ["cleanup warning"],
                "low_disk_free_bytes": 1024,
                "target_free_bytes": 2048,
            }
        }
    }
    storage.get_project_resource_report.side_effect = [
        {
            "items": [{"cleanup_status": "cleanup_degraded"}],
            "totals": {"peak_disk_used_bytes": 9000, "compute_minutes": 3.5},
        },
        {
            "items": [],
            "totals": {"peak_disk_used_bytes": 2000, "compute_minutes": 1.5},
        },
    ]
    storage.list_worker_nodes.side_effect = [
        [
            {"node_id": "", "metadata": {}},
            {
                "node_id": "node-1",
                "node_name": "Windows Main",
                "status": "active",
                "health_status": "healthy",
                "metadata": {
                    "workspace_root": "C:/ZetherionCI/workspaces",
                    "runtime_root": "C:/ZetherionCI/runtime",
                },
            },
        ],
        [
            {
                "node_id": "node-1",
                "node_name": "",
                "status": "active",
                "health_status": "degraded",
                "metadata": {},
            }
        ],
    ]
    storage.get_worker_resource_report.return_value = {
        "samples": [{"sample": {"disk_used_bytes": 4096, "disk_free_bytes": 512}}]
    }
    storage.list_managed_operations = AsyncMock(
        return_value=[
            {
                "operation_id": "op-1",
                "app_id": "cgs",
                "repo_id": "catalyst-group-solutions",
                "status": "failed",
            },
            {
                "operation_id": "op-2",
                "app_id": "cgs",
                "repo_id": "catalyst-group-solutions",
                "status": "running",
            },
        ]
    )
    storage.get_operation_hydrated = AsyncMock(
        side_effect=[
            {
                "operation_id": "op-1",
                "app_id": "cgs",
                "repo_id": "catalyst-group-solutions",
                "status": "failed",
                "summary": {"route_path": "/admin/ai"},
                "metadata": {},
                "refs": [
                    {"ref_kind": "vercel_deployment_id", "ref_value": "dep-1"},
                    {"ref_kind": "branch", "ref_value": "main"},
                ],
                "incidents": [
                    {"incident_type": "deployment_failed", "blocking": True},
                    {"incident_type": "runtime_error", "blocking": False},
                ],
            },
            {
                "operation_id": "op-2",
                "app_id": "cgs",
                "repo_id": "catalyst-group-solutions",
                "status": "running",
                "summary": {},
                "metadata": {"route_path": "/status"},
                "refs": [],
                "incidents": [],
            },
        ]
    )

    storage_report = await _build_storage_report(storage=storage, owner_id="owner-1", limit=5)

    assert storage_report.status == "blocked"
    assert storage_report.top_consumers[0]["repo_id"] == "repo-a"
    assert storage_report.alerts
    assert storage_report.announcement_events[0]["category"] == "ops.storage_pressure"
    assert storage_report.workers[0]["storage_status"] == "blocked"
    assert any("Top disk consumer" in coaching for coaching in storage_report.coaching)

    vercel_report = await _build_vercel_report(storage=storage, owner_id="owner-1", limit=5)

    assert vercel_report["summary"]["failed_operations"] == 1
    assert vercel_report["summary"]["active_operations"] == 1
    assert vercel_report["summary"]["blocking_incident_total"] == 1
    assert vercel_report["routes"][0]["route_path"] == "/admin/ai"
    assert vercel_report["routes"][0]["blocking_count"] == 1
    assert vercel_report["incident_types"][0]["incident_type"] == "deployment_failed"
    assert vercel_report["announcement_events"][0]["category"] == "ops.vercel_reporting"


@pytest.mark.asyncio
async def test_ci_observer_storage_report_covers_healthy_inventory_without_cleanup_receipts() -> (
    None
):
    storage = _storage()
    storage.list_repo_profiles.return_value = [{"repo_id": "repo-a", "display_name": "Repo A"}]
    storage.list_runs.return_value = [
        {"run_id": "", "repo_id": "repo-a", "metadata": {}, "plan": {}},
        {"run_id": "run-1", "repo_id": "repo-a", "metadata": {}, "plan": {}},
    ]
    storage.get_run_debug_bundle.return_value = {"bundle": {}}
    storage.get_project_resource_report.return_value = {
        "items": [],
        "totals": {"peak_disk_used_bytes": 1024, "compute_minutes": 1.0},
    }
    storage.list_worker_nodes.return_value = [
        {
            "node_id": "node-healthy",
            "node_name": "Windows Main",
            "status": "active",
            "health_status": "healthy",
            "metadata": {
                "workspace_root": "C:/ZetherionCI/workspaces",
                "runtime_root": "C:/ZetherionCI/runtime",
            },
        }
    ]
    storage.get_worker_resource_report.return_value = {
        "samples": [
            {
                "sample": {
                    "disk_used_bytes": 1024,
                    "disk_free_bytes": 100 * 1024 * 1024 * 1024,
                }
            }
        ]
    }

    storage_report = await _build_storage_report(storage=storage, owner_id="owner-1", limit=5)

    assert storage_report.status == "healthy"
    assert storage_report.top_consumers[0]["repo_id"] == "repo-a"
    assert storage_report.alerts == [
        "Highest current disk consumer is repo-a at 1024 bytes peak usage."
    ]
    assert storage_report.coaching == [
        "Top disk consumer right now is `repo-a`. "
        "Start retention tuning there before broad policy changes."
    ]
    assert storage_report.announcement_events[0]["category"] == "ops.storage_pressure"
    assert storage_report.announcement_events[0]["severity"] == "high"
    assert storage_report.workers[0]["storage_status"] == "healthy"
    assert storage_report.workers[0]["categories"][0]["category"] == "workspace_roots"
