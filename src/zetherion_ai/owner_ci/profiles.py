"""Default repo profiles and certification matrix for owner CI."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

LOCAL_WORKSPACE_ROOT = Path("/Users/jameshinton/Development")
WINDOWS_CI_ROOT = r"C:\ZetherionCI\workspaces"
WINDOWS_LIVE_DENYLIST = [r"C:\ZetherionAI", r"C:\ZetherionAI\*"]

_CGS_ROOT = str((LOCAL_WORKSPACE_ROOT / "catalyst-group-solutions").resolve())
_ZETHERION_ROOT = str((LOCAL_WORKSPACE_ROOT / "zetherion-ai").resolve())


def _local_gate(
    lane_id: str,
    lane_label: str,
    command: list[str],
    *,
    workspace_root: str,
) -> dict[str, Any]:
    return {
        "lane_id": lane_id,
        "lane_label": lane_label,
        "execution_target": "local_mac",
        "runner": "command",
        "action": "ci.test.run",
        "command": command,
        "artifact_contract": {
            "kind": "ci_shard",
            "expects": ["stdout", "stderr"],
        },
        "metadata": {
            "gate_kind": "static",
            "resource_class": "tiny",
            "parallel_group": "local-static",
            "workspace_root": workspace_root,
        },
    }


def _docker_gate(
    lane_id: str,
    lane_label: str,
    command: list[str],
    *,
    workspace_root: str,
    image: str,
    resource_class: str,
    parallel_group: str,
    certification_receipt: str | None = None,
) -> dict[str, Any]:
    repo_label = (
        Path(workspace_root).name if ":" not in workspace_root else workspace_root.split("\\")[-1]
    )
    cleanup_labels = {
        "zetherion.owner_ci": "true",
        "zetherion.repo": repo_label,
        "zetherion.lane_id": lane_id,
    }
    return {
        "lane_id": lane_id,
        "lane_label": lane_label,
        "execution_target": "windows_local",
        "runner": "docker",
        "action": "ci.test.run",
        "command": command,
        "required_capabilities": ["ci.test.run"],
        "artifact_contract": {
            "kind": "ci_shard",
            "expects": [
                "stdout",
                "stderr",
                "events",
                "log_chunks",
                "resource_samples",
                "debug_bundle",
                "cleanup_receipt",
            ],
        },
        "workspace_root": workspace_root,
        "payload": {
            "container_spec": {
                "image": image,
                "workdir": "/workspace",
                "mounts": [
                    {
                        "source": workspace_root,
                        "target": "/workspace",
                        "read_only": False,
                    }
                ],
                "env": {},
                "command": command,
            },
            "compose_project": f"owner-ci-{lane_id}",
            "cleanup_labels": cleanup_labels,
            "resource_class": resource_class,
            "parallel_group": parallel_group,
            "network_contract": {
                "mode": "isolated",
                "requires_dns": True,
            },
            "cleanup_policy": {
                "docker_only": True,
                "allow_host_mutation": False,
            },
            "execution_contract": {
                "docker_only": True,
                "allow_host_commands": False,
            },
            **({"certification_receipt": certification_receipt} if certification_receipt else {}),
        },
        "metadata": {
            "resource_class": resource_class,
            "parallel_group": parallel_group,
            "docker_only": True,
            "workspace_root": workspace_root,
            **({"certification_receipt": certification_receipt} if certification_receipt else {}),
        },
    }


CERTIFICATION_MATRIX: dict[str, list[dict[str, Any]]] = {
    "catalyst-group-solutions": [
        {"capability": "auth_exchange", "path": "/service/ai/v1/auth/exchange/service"},
        {"capability": "tenant_scope", "path": "/service/ai/v1/tenants/:tenant_id/*"},
        {"capability": "documents", "path": "/service/ai/v1/documents/*"},
        {"capability": "rag", "path": "/service/ai/v1/rag/query"},
        {"capability": "providers", "path": "/service/ai/v1/models/providers"},
        {"capability": "internal_integrations", "path": "/service/ai/v1/internal/integrations/*"},
    ],
    "zetherion-ai": [
        {"capability": "skills_api", "path": "/handle"},
        {"capability": "tenant_admin", "path": "/admin/tenants/:tenant_id/*"},
        {"capability": "worker_bridge", "path": "/worker/v1/*"},
        {"capability": "owner_ci_bridge", "path": "/owner/ci/worker/v1/*"},
        {"capability": "announcements", "path": "/announcements/events"},
        {"capability": "integrations", "path": "/oauth/*"},
    ],
}


DEFAULT_REPO_PROFILES: dict[str, dict[str, Any]] = {
    "catalyst-group-solutions": {
        "repo_id": "catalyst-group-solutions",
        "display_name": "Catalyst Group Solutions",
        "github_repo": "jimtin/catalyst-group-solutions",
        "default_branch": "main",
        "stack_kind": "nextjs",
        "mandatory_static_gates": [
            _local_gate(
                "lint",
                "Next lint",
                ["yarn", "lint"],
                workspace_root=_CGS_ROOT,
            ),
            _local_gate(
                "format-check",
                "Prettier check",
                ["yarn", "prettier:check"],
                workspace_root=_CGS_ROOT,
            ),
            _local_gate(
                "typecheck",
                "TypeScript noEmit",
                ["yarn", "typecheck"],
                workspace_root=_CGS_ROOT,
            ),
            _local_gate(
                "gitleaks",
                "Gitleaks secrets scan",
                ["gitleaks", "detect", "--source", ".", "--no-git"],
                workspace_root=_CGS_ROOT,
            ),
        ],
        "local_fast_lanes": [
            {
                "lane_id": "repo-check",
                "lane_label": "Repository integrity",
                "execution_target": "local_mac",
                "runner": "command",
                "action": "ci.test.run",
                "command": ["yarn", "cgs-ai:repo:check"],
                "artifact_contract": {"kind": "ci_shard", "expects": ["stdout", "stderr"]},
                "metadata": {
                    "resource_class": "small",
                    "parallel_group": "local-fast",
                    "workspace_root": _CGS_ROOT,
                },
            },
            {
                "lane_id": "tenant-scope-fast",
                "lane_label": "Tenant scope regression",
                "execution_target": "local_mac",
                "runner": "command",
                "action": "ci.test.run",
                "command": [
                    "yarn",
                    "test",
                    "src/__tests__/lib/cgs-ai-tenant-scope.test.ts",
                    "src/__tests__/api/service-ai-v1/internal-tenants-auth.test.ts",
                    "--runInBand",
                ],
                "artifact_contract": {"kind": "ci_shard", "expects": ["stdout", "stderr"]},
                "metadata": {
                    "resource_class": "small",
                    "parallel_group": "local-fast",
                    "workspace_root": _CGS_ROOT,
                },
            },
        ],
        "windows_full_lanes": [
            _docker_gate(
                "integration-critical",
                "Critical integration gate",
                ["yarn", "cgs-ai:test:integration"],
                workspace_root=rf"{WINDOWS_CI_ROOT}\catalyst-group-solutions",
                image="cgs-ci:latest",
                resource_class="docker_stack",
                parallel_group="windows-certification",
            ),
            _docker_gate(
                "golive-gate",
                "Go-live certification",
                ["yarn", "cgs-ai:test:golive"],
                workspace_root=rf"{WINDOWS_CI_ROOT}\catalyst-group-solutions",
                image="cgs-ci:latest",
                resource_class="docker_stack",
                parallel_group="windows-certification",
                certification_receipt="golive",
            ),
        ],
        "shard_templates": [
            {"family": "static", "source": "mandatory_static_gates"},
            {"family": "fast", "source": "local_fast_lanes"},
            {"family": "windows_full", "source": "windows_full_lanes"},
        ],
        "scheduling_policy": {
            "default_mode": "fast",
            "max_parallel_local": 1,
            "max_parallel_windows": 2,
            "resource_budgets": {"tiny": 1, "small": 1, "docker_stack": 2},
            "rebalance_enabled": True,
        },
        "resource_classes": {
            "tiny": {"max_parallel": 1},
            "small": {"max_parallel": 1},
            "docker_stack": {"max_parallel": 2},
        },
        "windows_execution_mode": "docker_only",
        "certification_requirements": [
            "mandatory_static_gates",
            "windows_full",
            "tenant_scope",
            "golive",
        ],
        "scheduled_canaries": [
            {
                "schedule_id": "cgs-nightly-certification",
                "name": "Nightly certification",
                "mode": "certification",
                "frequency": "nightly",
            }
        ],
        "debug_policy": {
            "redact_display_logs": True,
            "retain_debug_bundle_days": 14,
            "retain_raw_artifact_days": 14,
        },
        "agent_bootstrap_profile": {
            "client_kind": "cgs-app",
            "docs_slugs": [
                "cgs-ai-api-quickstart",
                "cgs-ai-api-reference",
                "cgs-ai-integration-credentials-runbook",
            ],
            "required_scopes": ["ai:runtime"],
        },
        "review_policy": {
            "require_reviewer": True,
            "block_on_cross_tenant_keywords": ["cross-tenant", "tenant", "scope", "authorization"],
            "required_statuses": ["ready_to_merge"],
        },
        "promotion_policy": {
            "deployment_mode": "github_only",
            "require_windows_full": True,
            "require_certification": True,
        },
        "allowed_paths": [
            _CGS_ROOT,
            rf"{WINDOWS_CI_ROOT}\catalyst-group-solutions",
        ],
        "secrets_profile": "cgs-default",
        "active": True,
        "metadata": {
            "certification_matrix": CERTIFICATION_MATRIX["catalyst-group-solutions"],
            "platform_canary": True,
            "windows_live_denylist": WINDOWS_LIVE_DENYLIST,
            "project_dashboard_tags": ["client-facing", "nextjs"],
        },
    },
    "zetherion-ai": {
        "repo_id": "zetherion-ai",
        "display_name": "Zetherion AI",
        "github_repo": "jimtin/zetherion-ai",
        "default_branch": "main",
        "stack_kind": "python",
        "mandatory_static_gates": [
            _local_gate(
                "ruff-check",
                "Ruff lint",
                ["ruff", "check", "."],
                workspace_root=_ZETHERION_ROOT,
            ),
            _local_gate(
                "ruff-format-check",
                "Ruff format check",
                ["ruff", "format", "--check", "."],
                workspace_root=_ZETHERION_ROOT,
            ),
            _local_gate(
                "gitleaks",
                "Gitleaks secrets scan",
                ["gitleaks", "detect", "--source", ".", "--no-git"],
                workspace_root=_ZETHERION_ROOT,
            ),
        ],
        "local_fast_lanes": [
            {
                "lane_id": "check",
                "lane_label": "Contract checks",
                "execution_target": "local_mac",
                "runner": "command",
                "action": "ci.test.run",
                "command": ["node", "scripts/testing/run-bounded.mjs", "--lane", "check"],
                "artifact_contract": {"kind": "ci_shard", "expects": ["stdout", "stderr"]},
                "metadata": {
                    "resource_class": "small",
                    "parallel_group": "local-fast",
                    "workspace_root": _ZETHERION_ROOT,
                },
            },
            {
                "lane_id": "targeted-unit",
                "lane_label": "Targeted unit lane",
                "execution_target": "local_mac",
                "runner": "command",
                "action": "ci.test.run",
                "command": ["node", "scripts/testing/run-bounded.mjs", "--lane", "targeted-unit"],
                "artifact_contract": {"kind": "ci_shard", "expects": ["stdout", "stderr"]},
                "metadata": {
                    "resource_class": "small",
                    "parallel_group": "local-fast",
                    "workspace_root": _ZETHERION_ROOT,
                },
            },
        ],
        "windows_full_lanes": [
            _docker_gate(
                "unit-full",
                "Full unit coverage",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "unit-full"],
                workspace_root=rf"{WINDOWS_CI_ROOT}\zetherion-ai",
                image="zetherion-ci:latest",
                resource_class="docker_stack",
                parallel_group="windows-certification",
            ),
            _docker_gate(
                "api-integration-coverage",
                "API integration coverage",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "api-integration-coverage"],
                workspace_root=rf"{WINDOWS_CI_ROOT}\zetherion-ai",
                image="zetherion-ci:latest",
                resource_class="docker_stack",
                parallel_group="windows-certification",
            ),
            _docker_gate(
                "e2e-fullstack-critical",
                "Full-stack critical gate",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "e2e-fullstack-critical"],
                workspace_root=rf"{WINDOWS_CI_ROOT}\zetherion-ai",
                image="zetherion-ci:latest",
                resource_class="docker_stack",
                parallel_group="windows-certification",
            ),
            _docker_gate(
                "discord-required-e2e",
                "Discord required roundtrip",
                ["bash", "scripts/local-required-e2e-receipt.sh"],
                workspace_root=rf"{WINDOWS_CI_ROOT}\zetherion-ai",
                image="zetherion-ci:latest",
                resource_class="discord_e2e",
                parallel_group="windows-discord",
                certification_receipt="discord_roundtrip",
            ),
        ],
        "shard_templates": [
            {"family": "static", "source": "mandatory_static_gates"},
            {"family": "fast", "source": "local_fast_lanes"},
            {"family": "windows_full", "source": "windows_full_lanes"},
        ],
        "scheduling_policy": {
            "default_mode": "fast",
            "max_parallel_local": 1,
            "max_parallel_windows": 2,
            "resource_budgets": {
                "tiny": 1,
                "small": 1,
                "docker_stack": 2,
                "discord_e2e": 1,
            },
            "rebalance_enabled": True,
        },
        "resource_classes": {
            "tiny": {"max_parallel": 1},
            "small": {"max_parallel": 1},
            "docker_stack": {"max_parallel": 2},
            "discord_e2e": {"max_parallel": 1},
        },
        "windows_execution_mode": "docker_only",
        "certification_requirements": [
            "mandatory_static_gates",
            "windows_full",
            "discord_roundtrip",
        ],
        "scheduled_canaries": [
            {
                "schedule_id": "zetherion-nightly-certification",
                "name": "Nightly certification",
                "mode": "certification",
                "frequency": "nightly",
            },
            {
                "schedule_id": "zetherion-discord-canary",
                "name": "Discord roundtrip canary",
                "mode": "certification",
                "frequency": "daily",
                "required_lane_id": "discord-required-e2e",
            },
        ],
        "debug_policy": {
            "redact_display_logs": True,
            "retain_debug_bundle_days": 14,
            "retain_raw_artifact_days": 14,
        },
        "agent_bootstrap_profile": {
            "client_kind": "zetherion-service",
            "docs_slugs": [
                "cgs-ai-api-quickstart",
                "cgs-ai-api-reference",
                "zetherion-docs-index",
            ],
            "required_scopes": ["ai:runtime"],
        },
        "review_policy": {
            "require_reviewer": True,
            "block_on_cross_tenant_keywords": [
                "owner_personal",
                "tenant",
                "scope",
                "authorization",
                "worker",
            ],
            "required_statuses": ["ready_to_merge"],
        },
        "promotion_policy": {
            "deployment_mode": "github_only",
            "require_windows_full": True,
            "require_certification": True,
        },
        "allowed_paths": [
            _ZETHERION_ROOT,
            rf"{WINDOWS_CI_ROOT}\zetherion-ai",
        ],
        "secrets_profile": "zetherion-default",
        "active": True,
        "metadata": {
            "certification_matrix": CERTIFICATION_MATRIX["zetherion-ai"],
            "platform_canary": True,
            "windows_live_denylist": WINDOWS_LIVE_DENYLIST,
            "project_dashboard_tags": ["backend", "python", "discord"],
        },
    },
}


def default_repo_profiles() -> list[dict[str, Any]]:
    """Return a deep-copied list of built-in repo profiles."""

    return [deepcopy(profile) for profile in DEFAULT_REPO_PROFILES.values()]


def default_repo_profile(repo_id: str) -> dict[str, Any] | None:
    """Return one deep-copied built-in repo profile."""

    profile = DEFAULT_REPO_PROFILES.get(str(repo_id).strip())
    if profile is None:
        return None
    return deepcopy(profile)
