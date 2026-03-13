"""Default repo profiles and certification matrix for owner CI."""

from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

_LOCAL_WORKSPACE_ROOT_CANDIDATES = (
    Path("/Users/jameshinton/Developer"),
    Path("/Users/jameshinton/Development"),
)


def _resolve_local_workspace_root() -> Path:
    for candidate in _LOCAL_WORKSPACE_ROOT_CANDIDATES:
        if candidate.exists():
            return candidate
    return _LOCAL_WORKSPACE_ROOT_CANDIDATES[0]


LOCAL_WORKSPACE_ROOT = _resolve_local_workspace_root()
WINDOWS_WORKSPACE_ROOT = r"C:\ZetherionCI\workspaces"
WINDOWS_RUNTIME_ROOT = r"C:\ZetherionCI\agent-runtime"
WINDOWS_LIVE_DENYLIST = [r"C:\ZetherionAI", r"C:\ZetherionAI\*"]

_CGS_ROOT = str((LOCAL_WORKSPACE_ROOT / "catalyst-group-solutions").resolve())
_ZETHERION_ROOT = str((LOCAL_WORKSPACE_ROOT / "zetherion-ai").resolve())
_CGS_WINDOWS_WORKSPACE_ROOT = rf"{WINDOWS_WORKSPACE_ROOT}\catalyst-group-solutions"
_ZETHERION_WINDOWS_WORKSPACE_ROOT = rf"{WINDOWS_WORKSPACE_ROOT}\zetherion-ai"


def _cgs_windows_node_cache_mounts(workspace_root: str) -> list[dict[str, Any]]:
    return [
        {
            "source": workspace_root,
            "target": "/workspace",
            "read_only": False,
        },
        {
            "source": "cgs-node-tool-node_modules",
            "target": "/workspace/node_modules",
            "read_only": False,
        },
        {
            "source": "cgs-node-tool-yarn_cache",
            "target": "/usr/local/share/.cache/yarn",
            "read_only": False,
        },
    ]


def _cgs_windows_bootstrap_command(script_command: str) -> list[str]:
    return [
        "bash",
        "-lc",
        (
            "corepack enable >/dev/null 2>&1 || true; "
            "corepack prepare yarn@1.22.22 --activate >/dev/null 2>&1 || true; "
            "if [ ! -f /workspace/node_modules/.yarn-integrity ] "
            "|| [ /workspace/yarn.lock -nt /workspace/node_modules/.yarn-integrity ]; then "
            "yarn install --frozen-lockfile >/dev/null; "
            "fi; "
            f"{script_command}"
        ),
    ]


def _cgs_windows_env(*, include_playwright: bool = False) -> dict[str, str]:
    env = {
        "CI": "true",
        "HOME": "/root",
        "TMPDIR": "/tmp",
        "TMP": "/tmp",
        "TEMP": "/tmp",
    }
    if include_playwright:
        env["PLAYWRIGHT_SKIP_BROWSER_DOWNLOAD"] = "1"
    return env


def _local_gate(
    lane_id: str,
    lane_label: str,
    command: list[str],
    *,
    workspace_root: str,
    resource_class: str = "cpu",
    parallel_group: str = "local-cpu",
    timeout_seconds: int = 1200,
    required_paths: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "lane_id": lane_id,
        "lane_label": lane_label,
        "execution_target": "local_mac",
        "runner": "command",
        "action": "ci.test.run",
        "command": command,
        "timeout_seconds": timeout_seconds,
        "artifact_contract": {
            "kind": "ci_shard",
            "expects": ["stdout", "stderr"],
        },
        "metadata": {
            "gate_kind": "static",
            "resource_class": resource_class,
            "parallel_group": parallel_group,
            "workspace_root": workspace_root,
            "covered_required_paths": list(required_paths or []),
            "timeout_seconds": timeout_seconds,
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
    timeout_seconds: int = 3600,
    required_paths: list[str] | None = None,
    certification_receipt: str | None = None,
    execution_backend: str = "wsl_docker",
    docker_backend: str = "wsl_docker",
    runtime_root: str = WINDOWS_RUNTIME_ROOT,
    wsl_distribution: str = "Ubuntu",
    mounts: list[dict[str, Any]] | None = None,
    env: dict[str, str] | None = None,
    workdir: str = "/workspace",
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
        "timeout_seconds": timeout_seconds,
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
                "workdir": workdir,
                "mounts": list(mounts or [])
                or [
                    {
                        "source": workspace_root,
                        "target": "/workspace",
                        "read_only": False,
                    }
                ],
                "env": dict(env or {}),
                "command": command,
            },
            "compose_project": f"owner-ci-{lane_id}",
            "cleanup_labels": cleanup_labels,
            "resource_class": resource_class,
            "parallel_group": parallel_group,
            "covered_required_paths": list(required_paths or []),
            "execution_backend": execution_backend,
            "docker_backend": docker_backend,
            "wsl_distribution": wsl_distribution,
            "workspace_root": workspace_root,
            "runtime_root": runtime_root,
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
                "execution_backend": execution_backend,
                "docker_backend": docker_backend,
                "wsl_distribution": wsl_distribution,
            },
            **({"certification_receipt": certification_receipt} if certification_receipt else {}),
        },
        "metadata": {
            "resource_class": resource_class,
            "parallel_group": parallel_group,
            "docker_only": True,
            "execution_backend": execution_backend,
            "docker_backend": docker_backend,
            "wsl_distribution": wsl_distribution,
            "workspace_root": workspace_root,
            "runtime_root": runtime_root,
            "covered_required_paths": list(required_paths or []),
            "timeout_seconds": timeout_seconds,
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
                ["bash", "-lc", "scripts/cgs-ai/docker-node-tool.sh yarn lint"],
                workspace_root=_CGS_ROOT,
                required_paths=["cgs_repo_integrity"],
            ),
            _local_gate(
                "format-check",
                "Prettier check",
                ["bash", "-lc", "scripts/cgs-ai/docker-node-tool.sh yarn prettier:check"],
                workspace_root=_CGS_ROOT,
                required_paths=["cgs_repo_integrity"],
            ),
            _local_gate(
                "typecheck",
                "TypeScript noEmit",
                ["bash", "-lc", "scripts/cgs-ai/docker-node-tool.sh yarn typecheck"],
                workspace_root=_CGS_ROOT,
                required_paths=["cgs_repo_integrity"],
            ),
            _local_gate(
                "gitleaks",
                "Gitleaks secrets scan",
                ["gitleaks", "detect", "--source", ".", "--no-git"],
                workspace_root=_CGS_ROOT,
                required_paths=["cgs_repo_integrity"],
            ),
        ],
        "local_fast_lanes": [
            _local_gate(
                "repo-check",
                "Repository integrity",
                ["bash", "-lc", "scripts/cgs-ai/docker-node-tool.sh yarn cgs-ai:repo:check"],
                workspace_root=_CGS_ROOT,
                parallel_group="local-cpu",
                required_paths=["cgs_repo_integrity"],
            ),
            _local_gate(
                "tenant-scope-fast",
                "Tenant scope regression",
                [
                    "bash",
                    "-lc",
                    (
                        "scripts/cgs-ai/docker-node-tool.sh yarn test "
                        "src/__tests__/lib/cgs-ai-tenant-scope.test.ts "
                        "src/__tests__/api/service-ai-v1/internal-tenants-auth.test.ts "
                        "--runInBand"
                    ),
                ],
                workspace_root=_CGS_ROOT,
                parallel_group="local-cpu",
                required_paths=["cgs_tenant_scope"],
            ),
        ],
        "local_full_lanes": [
            _local_gate(
                "c-unit-core",
                "CGS unit core",
                [
                    "bash",
                    "-lc",
                    (
                        "scripts/cgs-ai/docker-node-tool.sh yarn test "
                        "src/__tests__/lib/cgs-ai-tenant-scope.test.ts "
                        "src/__tests__/lib/cgs-ai-auth-bootstrap.test.ts "
                        "src/__tests__/lib/cgs-ai-startup.test.ts "
                        "--runInBand"
                    ),
                ],
                workspace_root=_CGS_ROOT,
                parallel_group="local-cpu",
                required_paths=["cgs_auth_flow", "cgs_tenant_scope", "owner_ci_receipts"],
            ),
            _local_gate(
                "c-unit-routes",
                "CGS API route unit checks",
                [
                    "bash",
                    "-lc",
                    (
                        "scripts/cgs-ai/docker-node-tool.sh yarn test "
                        "src/__tests__/api/admin-ai-health.test.ts "
                        "src/__tests__/api/service-ai-v1/owner-ci-routes.test.ts "
                        "src/__tests__/auth-redirect-page.test.tsx "
                        "--runInBand"
                    ),
                ],
                workspace_root=_CGS_ROOT,
                parallel_group="local-cpu",
                required_paths=["cgs_auth_flow", "cgs_ai_ops_schema", "owner_ci_receipts"],
            ),
            _local_gate(
                "c-int-auth",
                "CGS auth integration",
                ["bash", "-lc", "scripts/cgs-ai/docker-node-tool.sh yarn cgs-ai:test:integration"],
                workspace_root=_CGS_ROOT,
                resource_class="service",
                parallel_group="local-service",
                timeout_seconds=2400,
                required_paths=["cgs_auth_flow", "cgs_login_redirect"],
            ),
            _local_gate(
                "c-int-ai-ops",
                "CGS AI Ops integration",
                [
                    "bash",
                    "-lc",
                    (
                        "scripts/cgs-ai/docker-node-tool.sh yarn test "
                        "src/__tests__/api/admin-ai-health.test.ts "
                        "src/__tests__/components/admin/OwnerCiDashboard.test.tsx "
                        "--runInBand"
                    ),
                ],
                workspace_root=_CGS_ROOT,
                resource_class="service",
                parallel_group="local-service",
                timeout_seconds=1800,
                required_paths=[
                    "cgs_ai_ops_schema",
                    "owner_ci_receipts",
                    "cross_repo_runtime_health",
                ],
            ),
            _local_gate(
                "c-e2e-browser",
                "CGS Playwright critical flows",
                ["bash", "-lc", "scripts/cgs-ai/docker-node-tool.sh yarn test:e2e"],
                workspace_root=_CGS_ROOT,
                resource_class="serial",
                parallel_group="local-serial",
                timeout_seconds=3600,
                required_paths=["cgs_auth_flow", "cgs_login_redirect", "cgs_admin_ai_page"],
            ),
            _local_gate(
                "c-release",
                "CGS go-live receipt",
                ["bash", "-lc", "scripts/cgs-ai/docker-node-tool.sh yarn cgs-ai:test:golive"],
                workspace_root=_CGS_ROOT,
                resource_class="serial",
                parallel_group="local-serial",
                timeout_seconds=4200,
                required_paths=["cgs_release_verification", "owner_ci_receipts"],
            ),
        ],
        "windows_full_lanes": [
            _docker_gate(
                "integration-critical",
                "Critical integration gate",
                _cgs_windows_bootstrap_command("yarn cgs-ai:test:integration"),
                workspace_root=_CGS_WINDOWS_WORKSPACE_ROOT,
                image="node:22-bookworm",
                resource_class="service",
                parallel_group="windows-certification",
                required_paths=[
                    "cgs_auth_flow",
                    "cgs_login_redirect",
                    "cgs_ai_ops_schema",
                    "cgs_owner_ci_reporting",
                    "cgs_chatbot_runtime_proxy",
                    "cgs_tenant_scope",
                    "cross_repo_runtime_health",
                    "owner_ci_receipts",
                ],
                mounts=_cgs_windows_node_cache_mounts(_CGS_WINDOWS_WORKSPACE_ROOT),
                env=_cgs_windows_env(),
            ),
            _docker_gate(
                "golive-gate",
                "Go-live certification",
                _cgs_windows_bootstrap_command("yarn cgs-ai:test:golive"),
                workspace_root=_CGS_WINDOWS_WORKSPACE_ROOT,
                image="mcr.microsoft.com/playwright:v1.58.2-noble",
                resource_class="serial",
                parallel_group="windows-certification",
                required_paths=[
                    "cgs_repo_integrity",
                    "cgs_auth_flow",
                    "cgs_login_redirect",
                    "cgs_ai_ops_schema",
                    "cgs_admin_ai_page",
                    "cgs_owner_ci_reporting",
                    "cgs_chatbot_runtime_proxy",
                    "cgs_release_verification",
                    "cgs_tenant_scope",
                    "cross_repo_runtime_health",
                    "owner_ci_receipts",
                ],
                certification_receipt="golive",
                mounts=_cgs_windows_node_cache_mounts(_CGS_WINDOWS_WORKSPACE_ROOT),
                env=_cgs_windows_env(include_playwright=True),
            ),
        ],
        "shard_templates": [
            {"family": "static", "source": "mandatory_static_gates"},
            {"family": "fast", "source": "local_fast_lanes"},
            {"family": "local_full", "source": "local_full_lanes"},
            {"family": "windows_full", "source": "windows_full_lanes"},
        ],
        "scheduling_policy": {
            "default_mode": "fast",
            "max_parallel_local": 8,
            "max_parallel_windows": 2,
            "resource_budgets": {"cpu": 8, "service": 2, "serial": 1},
            "rebalance_enabled": True,
        },
        "resource_classes": {
            "cpu": {"max_parallel": 8},
            "service": {"max_parallel": 2},
            "serial": {"max_parallel": 1},
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
            "required_statuses": ["zetherion/merge-readiness"],
        },
        "promotion_policy": {
            "deployment_mode": "zetherion_control_plane",
            "github_decision_mode": "external_status_only",
            "status_contexts": {
                "merge": "zetherion/merge-readiness",
                "deploy": "zetherion/deploy-readiness",
            },
            "require_windows_full": True,
            "require_certification": True,
            "require_release_receipt": True,
        },
        "allowed_paths": [
            _CGS_ROOT,
            _CGS_WINDOWS_WORKSPACE_ROOT,
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
                ["bash", "-lc", "scripts/docker-python-tool.sh -m ruff check ."],
                workspace_root=_ZETHERION_ROOT,
                required_paths=["zetherion_repo_integrity"],
            ),
            _local_gate(
                "ruff-format-check",
                "Ruff format check",
                ["bash", "-lc", "scripts/docker-python-tool.sh -m ruff format --check ."],
                workspace_root=_ZETHERION_ROOT,
                required_paths=["zetherion_repo_integrity"],
            ),
            _local_gate(
                "gitleaks",
                "Gitleaks secrets scan",
                ["gitleaks", "detect", "--source", ".", "--no-git"],
                workspace_root=_ZETHERION_ROOT,
                required_paths=["zetherion_repo_integrity"],
            ),
        ],
        "local_fast_lanes": [
            _local_gate(
                "check",
                "Contract checks",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "check"],
                workspace_root=_ZETHERION_ROOT,
                parallel_group="local-cpu",
                required_paths=[
                    "owner_ci_cutover",
                    "release_contracts",
                    "zetherion_repo_integrity",
                ],
            ),
            _local_gate(
                "targeted-unit",
                "Targeted unit lane",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "targeted-unit"],
                workspace_root=_ZETHERION_ROOT,
                parallel_group="local-cpu",
                required_paths=["discord_dm_reply", "queue_reliability"],
            ),
        ],
        "local_full_lanes": [
            _local_gate(
                "z-unit-core",
                "Zetherion unit core",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-unit-core"],
                workspace_root=_ZETHERION_ROOT,
                parallel_group="local-cpu",
                required_paths=[
                    "queue_reliability",
                    "runtime_status_persistence",
                    "release_contracts",
                ],
            ),
            _local_gate(
                "z-unit-runtime",
                "Zetherion unit runtime",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-unit-runtime"],
                workspace_root=_ZETHERION_ROOT,
                parallel_group="local-cpu",
                required_paths=["discord_dm_reply", "discord_channel_reply", "startup_readiness"],
            ),
            _local_gate(
                "z-unit-owner-ci",
                "Zetherion owner-CI unit",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-unit-owner-ci"],
                workspace_root=_ZETHERION_ROOT,
                parallel_group="local-cpu",
                required_paths=["owner_ci_cutover", "owner_ci_receipts", "release_contracts"],
            ),
            _local_gate(
                "z-int-runtime-api",
                "Zetherion runtime API integration",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-int-runtime-api"],
                workspace_root=_ZETHERION_ROOT,
                resource_class="service",
                parallel_group="local-service",
                timeout_seconds=2400,
                required_paths=[
                    "skills_reachability",
                    "qdrant_readiness",
                    "runtime_status_persistence",
                ],
            ),
            _local_gate(
                "z-int-runtime-queue",
                "Zetherion queue/runtime integration",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-int-runtime-queue"],
                workspace_root=_ZETHERION_ROOT,
                resource_class="service",
                parallel_group="local-service",
                timeout_seconds=2400,
                required_paths=[
                    "queue_reliability",
                    "startup_readiness",
                    "runtime_status_persistence",
                ],
            ),
            _local_gate(
                "z-int-dependencies",
                "Zetherion dependency integration",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-int-dependencies"],
                workspace_root=_ZETHERION_ROOT,
                resource_class="service",
                parallel_group="local-service",
                timeout_seconds=2400,
                required_paths=[
                    "skills_reachability",
                    "qdrant_readiness",
                    "runtime_status_persistence",
                ],
            ),
            _local_gate(
                "z-int-platform",
                "Zetherion platform integration",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-int-platform"],
                workspace_root=_ZETHERION_ROOT,
                resource_class="service",
                parallel_group="local-service",
                timeout_seconds=2400,
                required_paths=["runtime_status_persistence"],
            ),
            _local_gate(
                "z-e2e-dm-sim",
                "Discord DM simulated canaries",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-e2e-dm-sim"],
                workspace_root=_ZETHERION_ROOT,
                resource_class="service",
                parallel_group="local-service",
                timeout_seconds=1800,
                required_paths=["discord_dm_reply", "queue_reliability"],
            ),
            _local_gate(
                "z-e2e-channel-sim",
                "Discord channel simulated canaries",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-e2e-channel-sim"],
                workspace_root=_ZETHERION_ROOT,
                resource_class="service",
                parallel_group="local-service",
                timeout_seconds=1800,
                required_paths=["discord_channel_reply", "queue_reliability"],
            ),
            _local_gate(
                "z-e2e-faults",
                "Discord queue/runtime fault injection",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-e2e-faults"],
                workspace_root=_ZETHERION_ROOT,
                resource_class="service",
                parallel_group="local-service",
                timeout_seconds=1800,
                required_paths=["queue_reliability", "startup_readiness", "qdrant_readiness"],
            ),
            _local_gate(
                "z-e2e-discord-live",
                "Discord live canaries",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-e2e-discord-live"],
                workspace_root=_ZETHERION_ROOT,
                resource_class="serial",
                parallel_group="local-serial",
                timeout_seconds=3600,
                required_paths=["discord_dm_reply", "discord_channel_reply"],
            ),
            _local_gate(
                "z-e2e-discord-security",
                "Discord security canaries",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-e2e-discord-security"],
                workspace_root=_ZETHERION_ROOT,
                resource_class="serial",
                parallel_group="local-serial",
                timeout_seconds=2400,
                required_paths=[],
            ),
            _local_gate(
                "z-release",
                "Local release verification",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "z-release"],
                workspace_root=_ZETHERION_ROOT,
                resource_class="serial",
                parallel_group="local-serial",
                timeout_seconds=5400,
                required_paths=["release_contracts", "runtime_drift_zero", "back_to_back_deploys"],
            ),
        ],
        "windows_full_lanes": [
            _docker_gate(
                "unit-full",
                "Full unit coverage",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "unit-full"],
                workspace_root=_ZETHERION_WINDOWS_WORKSPACE_ROOT,
                image="zetherion-ci:latest",
                resource_class="service",
                parallel_group="windows-certification",
                required_paths=["queue_reliability", "runtime_status_persistence"],
            ),
            _docker_gate(
                "api-integration-coverage",
                "API integration coverage",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "api-integration-coverage"],
                workspace_root=_ZETHERION_WINDOWS_WORKSPACE_ROOT,
                image="zetherion-ci:latest",
                resource_class="service",
                parallel_group="windows-certification",
                required_paths=["skills_reachability", "qdrant_readiness"],
            ),
            _docker_gate(
                "e2e-fullstack-critical",
                "Full-stack critical gate",
                ["node", "scripts/testing/run-bounded.mjs", "--lane", "e2e-fullstack-critical"],
                workspace_root=_ZETHERION_WINDOWS_WORKSPACE_ROOT,
                image="zetherion-ci:latest",
                resource_class="service",
                parallel_group="windows-certification",
                required_paths=["release_contracts", "owner_ci_cutover"],
            ),
            _docker_gate(
                "discord-required-e2e",
                "Discord required roundtrip",
                ["bash", "scripts/local-required-e2e-receipt.sh"],
                workspace_root=_ZETHERION_WINDOWS_WORKSPACE_ROOT,
                image="zetherion-ci:latest",
                resource_class="serial",
                parallel_group="windows-discord",
                required_paths=["discord_dm_reply", "discord_channel_reply"],
                certification_receipt="discord_roundtrip",
            ),
        ],
        "shard_templates": [
            {"family": "static", "source": "mandatory_static_gates"},
            {"family": "fast", "source": "local_fast_lanes"},
            {"family": "local_full", "source": "local_full_lanes"},
            {"family": "windows_full", "source": "windows_full_lanes"},
        ],
        "scheduling_policy": {
            "default_mode": "fast",
            "max_parallel_local": 8,
            "max_parallel_windows": 2,
            "resource_budgets": {
                "cpu": 8,
                "service": 2,
                "serial": 1,
            },
            "rebalance_enabled": True,
        },
        "resource_classes": {
            "cpu": {"max_parallel": 8},
            "service": {"max_parallel": 2},
            "serial": {"max_parallel": 1},
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
            "required_statuses": ["zetherion/merge-readiness"],
        },
        "promotion_policy": {
            "deployment_mode": "zetherion_control_plane",
            "github_decision_mode": "external_status_only",
            "status_contexts": {
                "merge": "zetherion/merge-readiness",
                "deploy": "zetherion/deploy-readiness",
            },
            "require_windows_full": True,
            "require_certification": True,
            "require_release_receipt": True,
        },
        "allowed_paths": [
            _ZETHERION_ROOT,
            _ZETHERION_WINDOWS_WORKSPACE_ROOT,
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
