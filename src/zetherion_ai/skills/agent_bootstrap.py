"""Owner-scoped agent bootstrap and broker skill."""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import io
import json
import os
import re
import shutil
import tarfile
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from zetherion_ai.logging import get_logger
from zetherion_ai.owner_ci import OwnerCiStorage, default_repo_profile, default_repo_profiles
from zetherion_ai.owner_ci.diagnostics import build_run_diagnostics
from zetherion_ai.skills.base import Skill, SkillMetadata, SkillRequest, SkillResponse
from zetherion_ai.skills.ci_controller import CiControllerSkill
from zetherion_ai.skills.clerk.client import ClerkMetadataClient, ClerkMetadataError
from zetherion_ai.skills.github.client import GitHubAPIError, GitHubClient, GitHubValidationError
from zetherion_ai.skills.permissions import Permission, PermissionSet
from zetherion_ai.skills.stripe.client import StripeAPIError, StripeClient
from zetherion_ai.skills.vercel.client import VercelAPIError, VercelClient

log = get_logger("zetherion_ai.skills.agent_bootstrap")

_REPO_ROOT = Path(__file__).resolve().parents[3]
_CGS_REPO_ROOT = _REPO_ROOT.parent / "catalyst-group-solutions"
_INLINE_ARCHIVE_MAX_BYTES = 16 * 1024 * 1024
_EXCLUDED_DIR_NAMES = {
    ".git",
    ".next",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "venv",
}
_HEX_SHA_RE = re.compile(r"^[0-9a-f]{7,40}$", re.IGNORECASE)
_REPO_ID_SANITIZE_RE = re.compile(r"[^a-z0-9]+")
_GITHUB_CONNECTOR_ID = "github-primary"
_VERCEL_CONNECTOR_ID = "vercel-primary"
_CLERK_CONNECTOR_ID = "clerk-primary"
_STRIPE_CONNECTOR_ID = "stripe-primary"

_SERVICE_VIEW_CAPABILITIES: dict[str, dict[str, str]] = {
    "github": {
        "overview": "branch_metadata",
        "compare": "diff_compare",
        "pulls": "pr_metadata",
        "workflows": "workflow_status",
    },
    "vercel": {
        "overview": "project_metadata",
        "deployments": "deployment_status",
        "domains": "domain_metadata",
        "envs": "env_names",
    },
    "clerk": {
        "overview": "instance_metadata",
        "jwks": "jwks_metadata",
        "openid": "issuer_metadata",
    },
    "stripe": {
        "overview": "account_metadata",
        "products": "product_metadata",
        "prices": "price_metadata",
        "customers": "customer_metadata",
        "subscriptions": "subscription_metadata",
        "invoices": "invoice_metadata",
        "webhook_health": "webhook_metadata",
    },
    "discord": {
        "overview": "channel_metadata",
    },
}

_SERVICE_ACTION_CAPABILITIES: dict[str, dict[str, str]] = {
    "stripe": {
        "product.ensure": "product_ensure",
        "price.ensure": "price_ensure",
        "customer.link": "customer_link",
        "subscription.link": "subscription_link",
        "subscription.update_price": "subscription_update_price",
        "meter.config.ensure": "meter_config_ensure",
    }
}

_SENSITIVE_KEY_PARTS = {
    "authorization",
    "password",
    "secret",
    "token",
    "webhook_secret",
}
_SENSITIVE_KEY_EXACT = {
    "api_key",
    "secret_value",
    "patch_bundle_base64",
    "diff_text",
}
_SAFE_KEY_EXACT = {
    "grant_key",
    "connector_id",
    "principal_id",
    "public_key",
    "publishable_key_hint",
    "key_prefix",
}

_TERMINAL_OPERATION_STATUSES = {"succeeded", "resolved", "failed", "error"}


def _slugify_repo_id(value: str) -> str:
    normalized = _REPO_ID_SANITIZE_RE.sub("-", value.strip().lower()).strip("-")
    return normalized or "managed-repo"


def _split_github_repo(value: str) -> tuple[str, str]:
    full_name = str(value or "").strip().strip("/")
    parts = full_name.split("/", 1)
    if len(parts) != 2 or not parts[0] or not parts[1]:
        raise ValueError("github_repo must be in `owner/repo` format")
    return parts[0], parts[1]


def _safe_branch_suffix(value: str) -> str:
    normalized = _REPO_ID_SANITIZE_RE.sub("-", value.strip().lower()).strip("-")
    return normalized or "managed-change"


def _system_safe_principal_id(value: str | None) -> str | None:
    principal_id = str(value or "").strip() or None
    if principal_id and principal_id.startswith("system:"):
        return None
    return principal_id


_DEFAULT_DOCS = [
    {
        "slug": "cgs-ai-api-quickstart",
        "title": "CGS AI API Quickstart",
        "path": "/docs/technical/cgs-ai-api-quickstart",
        "category": "quickstart",
        "source_path": _CGS_REPO_ROOT / "docs/technical/cgs-ai-api-quickstart.md",
    },
    {
        "slug": "cgs-ai-api-reference",
        "title": "CGS AI API Reference",
        "path": "/docs/technical/cgs-ai-api-reference",
        "category": "reference",
        "source_path": _CGS_REPO_ROOT / "docs/technical/cgs-ai-api-reference.md",
    },
    {
        "slug": "cgs-ai-integration-credentials-runbook",
        "title": "Integration Credentials Runbook",
        "path": "/docs/technical/cgs-ai-integration-credentials-runbook",
        "category": "runbook",
        "source_path": _CGS_REPO_ROOT / "docs/technical/cgs-ai-integration-credentials-runbook.md",
    },
    {
        "slug": "zetherion-docs-index",
        "title": "Zetherion Product Docs",
        "path": "/products/zetherion-ai/docs",
        "category": "product",
        "source_path": _REPO_ROOT / "docs/index.md",
    },
]


def _normalize_owner_id(request: SkillRequest) -> str:
    for candidate in (
        request.context.get("owner_id"),
        request.context.get("operator_id"),
        request.context.get("actor_sub"),
        request.user_id,
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return "owner"


def _normalize_principal_id(request: SkillRequest) -> str:
    for candidate in (
        request.context.get("principal_id"),
        request.context.get("agent_principal_id"),
        request.context.get("subject"),
        request.user_id,
    ):
        value = str(candidate or "").strip()
        if value:
            return value
    return "agent"


def _read_text(path: Path | None) -> str | None:
    if path is None or not path.exists() or not path.is_file():
        return None
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return None


def _markdown_headings(content: str | None) -> list[str]:
    if not content:
        return []
    headings: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            headings.append(stripped.lstrip("#").strip())
    return headings


def _doc_manifest(definition: dict[str, Any], public_base_url: str) -> dict[str, Any]:
    source_path = definition.get("source_path")
    source = Path(source_path) if isinstance(source_path, Path | str) else None
    content_markdown = _read_text(source)
    path = str(definition["path"])
    base = public_base_url.rstrip("/")
    return {
        "slug": str(definition["slug"]),
        "title": str(definition["title"]),
        "path": path,
        "category": str(definition["category"]),
        "url": f"{base}{path}" if base else path,
        "version": "current",
        "source_path": str(source) if source and source.exists() else None,
        "content_markdown": content_markdown,
        "headings": _markdown_headings(content_markdown),
    }


def _resolve_git_dir(repo_root: Path) -> Path | None:
    dot_git = repo_root / ".git"
    if dot_git.is_dir():
        return dot_git
    if dot_git.is_file():
        try:
            raw = dot_git.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        prefix = "gitdir:"
        if raw.lower().startswith(prefix):
            git_dir = raw[len(prefix) :].strip()
            resolved = (
                (repo_root / git_dir).resolve()
                if not Path(git_dir).is_absolute()
                else Path(git_dir)
            )
            return resolved if resolved.exists() else None
    return None


def _resolve_packed_ref(git_dir: Path, ref_name: str) -> str | None:
    packed_refs = git_dir / "packed-refs"
    if not packed_refs.exists():
        return None
    try:
        content = packed_refs.read_text(encoding="utf-8")
    except OSError:
        return None
    for line in content.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or stripped.startswith("^"):
            continue
        try:
            sha, name = stripped.split(" ", 1)
        except ValueError:
            continue
        if name.strip() == ref_name:
            return sha.strip()
    return None


def _resolve_git_ref(repo_root: Path, git_ref: str) -> str | None:
    candidate = git_ref.strip() or "HEAD"
    if _HEX_SHA_RE.fullmatch(candidate):
        return candidate
    git_dir = _resolve_git_dir(repo_root)
    if git_dir is None:
        return None
    if candidate == "HEAD":
        head_file = git_dir / "HEAD"
        try:
            head_value = head_file.read_text(encoding="utf-8").strip()
        except OSError:
            return None
        if head_value.startswith("ref:"):
            return _resolve_git_ref(repo_root, head_value.split(":", 1)[1].strip())
        return head_value or None
    ref_names = (
        [candidate]
        if candidate.startswith("refs/")
        else [
            f"refs/heads/{candidate}",
            f"refs/tags/{candidate}",
            candidate,
        ]
    )
    for ref_name in ref_names:
        ref_path = git_dir / ref_name
        if ref_path.exists() and ref_path.is_file():
            try:
                return ref_path.read_text(encoding="utf-8").strip()
            except OSError:
                continue
        packed = _resolve_packed_ref(git_dir, ref_name)
        if packed:
            return packed
    return None


def _tar_workspace(repo_root: Path) -> tuple[bytes, int]:
    file_count = 0
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        for path in sorted(repo_root.rglob("*")):
            if path.is_dir() or path.is_symlink():
                continue
            rel = path.relative_to(repo_root)
            if any(part in _EXCLUDED_DIR_NAMES for part in rel.parts):
                continue
            archive.add(path, arcname=str(Path(repo_root.name) / rel), recursive=False)
            file_count += 1
    return buffer.getvalue(), file_count


def _collect_commands(repo: dict[str, Any]) -> dict[str, list[list[str]]]:
    def extract(raw_lanes: list[Any]) -> list[list[str]]:
        commands: list[list[str]] = []
        for lane in raw_lanes:
            if not isinstance(lane, dict):
                continue
            command = list(lane.get("command") or [])
            if command:
                commands.append([str(part) for part in command])
        return commands

    return {
        "mandatory_static_gates": extract(list(repo.get("mandatory_static_gates") or [])),
        "local_fast": extract(list(repo.get("local_fast_lanes") or [])),
        "local_full": extract(list(repo.get("local_full_lanes") or [])),
        "windows_full": extract(list(repo.get("windows_full_lanes") or [])),
    }


def _default_service_connector_map(repo_id: str) -> dict[str, Any]:
    mapping: dict[str, Any] = {
        "github": {
            "connector_id": "github-primary",
            "service_kind": "github",
            "read_access": [
                "repo_list",
                "ref_resolve",
                "branch_metadata",
                "diff_compare",
                "pr_metadata",
                "workflow_status",
                "archive_bundle",
            ],
            "write_access": [],
            "broker_only": True,
            "push_mode": "zetherion_only",
        },
    }
    if repo_id in {"catalyst-group-solutions", "zetherion-ai"}:
        mapping["vercel"] = {
            "connector_id": "vercel-primary",
            "service_kind": "vercel",
            "read_access": [
                "project_metadata",
                "deployment_status",
                "domain_metadata",
                "env_names",
            ],
            "write_access": [],
            "broker_only": True,
        }
        mapping["clerk"] = {
            "connector_id": "clerk-primary",
            "service_kind": "clerk",
            "read_access": ["jwks_metadata", "issuer_metadata", "instance_metadata"],
            "write_access": [],
            "broker_only": True,
        }
    if repo_id == "catalyst-group-solutions":
        mapping["stripe"] = {
            "connector_id": "stripe-primary",
            "service_kind": "stripe",
            "read_access": [
                "account_metadata",
                "product_metadata",
                "price_metadata",
                "customer_metadata",
                "subscription_metadata",
                "invoice_metadata",
                "webhook_metadata",
            ],
            "write_access": list(_SERVICE_ACTION_CAPABILITIES.get("stripe", {}).values()),
            "broker_only": True,
        }
    if repo_id == "zetherion-ai":
        mapping["discord"] = {
            "connector_id": "discord-primary",
            "service_kind": "discord",
            "read_access": ["guild_metadata", "channel_metadata"],
            "write_access": [],
            "broker_only": True,
        }
    return mapping


def _default_mock_profiles(repo_id: str) -> list[dict[str, Any]]:
    if repo_id == "catalyst-group-solutions":
        return [
            {
                "profile_id": "cgs-zetherion-boundary",
                "summary": (
                    "Mock the upstream Zetherion boundary by stubbing "
                    "@/lib/cgs-ai/zetherion in route and library tests."
                ),
                "references": [
                    "src/lib/cgs-ai/zetherion.ts",
                    "src/__tests__/api/service-ai-v1/*",
                ],
            }
        ]
    if repo_id == "zetherion-ai":
        return [
            {
                "profile_id": "zetherion-isolated-docker-e2e",
                "summary": (
                    "Use docker-compose.test.yml and scripts/e2e_run_manager.py "
                    "for isolated end-to-end runs."
                ),
                "references": [
                    "scripts/e2e_run_manager.py",
                    "docker-compose.test.yml",
                ],
            },
            {
                "profile_id": "zetherion-discord-required-receipt",
                "summary": (
                    "Use the required Discord roundtrip receipt wrappers "
                    "for certification evidence."
                ),
                "references": [
                    "scripts/local-required-e2e-receipt.sh",
                    "scripts/run-required-discord-e2e.sh",
                ],
            },
        ]
    return [
        {
            "profile_id": "generic-fast-feedback",
            "summary": (
                "Start with local static analysis, lightweight smoke tests, and repo-specific "
                "commands added during enrollment."
            ),
            "references": [],
        }
    ]


def _default_workspace_manifest(repo: dict[str, Any]) -> dict[str, Any]:
    repo_id = str(repo["repo_id"])
    stack_kind = str(repo.get("stack_kind") or "generic").strip() or "generic"
    if stack_kind == "nextjs":
        install_commands = [["yarn", "install", "--frozen-lockfile"]]
        start_commands = [["yarn", "dev"]]
        package_manager = "yarn"
    elif stack_kind == "python":
        install_commands = [["python", "-m", "venv", "venv"], ["pip", "install", "-e", ".[dev]"]]
        start_commands = [["python", "-m", "zetherion_ai.main"]]
        package_manager = "pip"
    else:
        install_commands = []
        start_commands = []
        package_manager = "custom"
    return {
        "repo_id": repo_id,
        "github_repo": str(repo["github_repo"]),
        "clone_urls": {
            "https": f"https://github.com/{repo['github_repo']}.git",
            "ssh": f"git@github.com:{repo['github_repo']}.git",
        },
        "default_branch": str(repo["default_branch"]),
        "workspace_roots": list(repo.get("allowed_paths") or []),
        "package_manager": package_manager,
        "install_commands": install_commands,
        "start_commands": start_commands,
        "local_fast_commands": _collect_commands(repo)["local_fast"],
        "local_full_commands": _collect_commands(repo)["local_full"],
        "windows_full_commands": _collect_commands(repo)["windows_full"],
        "docker_only_windows": str(repo.get("windows_execution_mode") or "") == "docker_only",
        "github_governance": {
            "managed_repo": True,
            "broker_only": True,
            "write_principal": "zetherion",
            "agent_push_enabled": False,
            "publish_flow": "publish_candidate_only",
            "branch_protection_required": True,
        },
    }


def _default_test_harness_manifest(repo: dict[str, Any]) -> dict[str, Any]:
    return {
        "repo_id": str(repo["repo_id"]),
        "mandatory_static_gates": list(repo.get("mandatory_static_gates") or []),
        "local_fast_lanes": list(repo.get("local_fast_lanes") or []),
        "local_full_lanes": list(repo.get("local_full_lanes") or []),
        "windows_full_lanes": list(repo.get("windows_full_lanes") or []),
        "shard_templates": list(repo.get("shard_templates") or []),
        "resource_classes": dict(repo.get("resource_classes") or {}),
        "certification_requirements": list(repo.get("certification_requirements") or []),
        "scheduled_canaries": list(repo.get("scheduled_canaries") or []),
        "windows_execution_mode": str(repo.get("windows_execution_mode") or "command"),
    }


def _default_command_catalog(repo: dict[str, Any]) -> dict[str, Any]:
    commands = _collect_commands(repo)
    return {
        "mandatory_static_gates": commands["mandatory_static_gates"],
        "local_fast": commands["local_fast"],
        "local_full": commands["local_full"],
        "windows_full": commands["windows_full"],
    }


def _default_service_operations(repo_id: str) -> dict[str, Any]:
    operations: dict[str, Any] = {}
    for service_kind, views in _SERVICE_VIEW_CAPABILITIES.items():
        if service_kind == "discord" and repo_id != "zetherion-ai":
            continue
        if service_kind == "stripe" and repo_id != "catalyst-group-solutions":
            continue
        if service_kind in {"vercel", "clerk"} and repo_id not in {
            "catalyst-group-solutions",
            "zetherion-ai",
        }:
            continue
        operations[service_kind] = {
            "views": sorted(views.keys()),
            "actions": sorted(_SERVICE_ACTION_CAPABILITIES.get(service_kind, {}).keys()),
        }
    return operations


def _default_capability_registry(repo: dict[str, Any]) -> dict[str, Any]:
    repo_id = str(repo["repo_id"])
    supported_tooling = sorted(
        {
            "docker" if str(repo.get("windows_execution_mode") or "") == "docker_only" else "",
            "ruff" if repo_id == "zetherion-ai" else "",
            "pytest" if repo_id == "zetherion-ai" else "",
            "jest" if repo_id == "catalyst-group-solutions" else "",
            "eslint" if repo_id == "catalyst-group-solutions" else "",
            "discord_e2e" if repo_id == "zetherion-ai" else "",
        }
        - {""}
    )
    return {
        "supported_tooling": supported_tooling,
        "mock_profiles": [entry["profile_id"] for entry in _default_mock_profiles(repo_id)],
        "required_connectors": sorted(_default_service_connector_map(repo_id).keys()),
        "required_secret_refs": [],
        "service_actions": _default_service_operations(repo_id),
        "required_docs": list((repo.get("agent_bootstrap_profile") or {}).get("docs_slugs") or []),
    }


def _normalize_limit(value: Any, *, default: int = 10, maximum: int = 20) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(1, min(parsed, maximum))


def _service_routes_for(repo_id: str, *, base_url: str) -> dict[str, str]:
    prefix = f"{base_url}/service/ai/v1/agent/apps/{repo_id}/services" if base_url else ""
    return {
        "catalog": f"{prefix}" if prefix else f"/service/ai/v1/agent/apps/{repo_id}/services",
        "service": (
            f"{prefix}/:serviceKind"
            if prefix
            else f"/service/ai/v1/agent/apps/{repo_id}/services/:serviceKind"
        ),
    }


def _operation_routes(*, base_url: str, app_id: str | None = None) -> dict[str, str]:
    prefix = f"{base_url}/service/ai/v1/agent" if base_url else "/service/ai/v1/agent"
    routes = {
        "operation": f"{prefix}/operations/:operationId",
        "evidence": f"{prefix}/operations/:operationId/evidence",
        "logs": f"{prefix}/operations/:operationId/logs",
        "incidents": f"{prefix}/operations/:operationId/incidents",
    }
    if app_id:
        routes["resolve"] = f"{prefix}/apps/{app_id}/operations/resolve"
    return routes


def _default_service_adapter_capabilities() -> dict[str, dict[str, Any]]:
    return {
        "github": {
            "readable_refs": [
                "publish_candidate_id",
                "git_sha",
                "branch",
                "pr_number",
                "github_run_id",
                "github_delivery_id",
            ],
            "available_evidence_types": ["summary", "events", "logs", "artifacts"],
            "redaction_rules": ["truncate_long_logs", "redact_tokens"],
            "correlation_strategy": "publish_candidate_or_git_refs",
            "ingestion_modes": ["webhook", "poll"],
            "retention": {"raw_logs_days": 7, "summary_days": 30},
            "known_unsupported": [],
        },
        "vercel": {
            "readable_refs": ["git_sha", "branch", "vercel_deployment_id", "vercel_event_id"],
            "available_evidence_types": ["summary", "events", "logs"],
            "redaction_rules": ["redact_env_values"],
            "correlation_strategy": "deployment_meta_and_git_refs",
            "ingestion_modes": ["webhook", "poll"],
            "retention": {"raw_logs_days": 7, "summary_days": 30},
            "known_unsupported": ["deployment_artifacts"],
        },
        "clerk": {
            "readable_refs": ["clerk_instance_ref", "issuer", "jwks_url", "clerk_event_id"],
            "available_evidence_types": ["summary", "events", "logs"],
            "redaction_rules": ["redact_keys"],
            "correlation_strategy": "app_connector_metadata",
            "ingestion_modes": ["webhook", "poll"],
            "retention": {"raw_logs_days": 7, "summary_days": 30},
            "known_unsupported": ["provider_side_request_logs"],
        },
        "stripe": {
            "readable_refs": ["stripe_event_id", "customer_id", "subscription_id"],
            "available_evidence_types": ["summary", "events"],
            "redaction_rules": ["redact_pii"],
            "correlation_strategy": "billing_object_refs",
            "ingestion_modes": ["webhook", "poll"],
            "retention": {"raw_logs_days": 7, "summary_days": 30},
            "known_unsupported": ["provider_side_execution_logs"],
        },
    }


def _connector_health_report(
    connector: dict[str, Any],
    capability: dict[str, Any] | None,
) -> dict[str, Any]:
    service_kind = str(connector.get("service_kind") or "").strip().lower()
    metadata = dict(connector.get("metadata") or {})
    blocking_reasons: list[str] = []
    warnings: list[str] = []
    auth_kind = str(connector.get("auth_kind") or "token").strip().lower() or "token"
    has_secret = bool(connector.get("has_secret"))
    active = bool(connector.get("active", True))
    if not active:
        blocking_reasons.append("connector_inactive")
    if auth_kind not in {"none", "anonymous"} and not has_secret:
        blocking_reasons.append("missing_secret")
    if capability is None:
        warnings.append("missing_service_capability_manifest")
    if service_kind == "clerk" and not (
        _derive_clerk_jwks_url(metadata)
        or str(metadata.get("issuer") or "").strip()
        or str(metadata.get("frontend_api_url") or "").strip()
    ):
        warnings.append("missing_clerk_metadata")
    if service_kind == "vercel" and not (
        str(metadata.get("team_id") or "").strip()
        or str(metadata.get("project_ref") or "").strip()
    ):
        warnings.append("missing_vercel_project_metadata")
    status = "healthy"
    if blocking_reasons:
        status = "blocked"
    elif warnings:
        status = "degraded"
    return {
        "connector_id": str(connector.get("connector_id") or "").strip(),
        "service_kind": service_kind,
        "status": status,
        "auth_kind": auth_kind,
        "active": active,
        "auth_configured": auth_kind in {"none", "anonymous"} or has_secret,
        "capability_manifest_present": capability is not None,
        "blocking_reasons": blocking_reasons,
        "warnings": warnings,
        "checked_at": datetime.now(UTC).isoformat(),
    }


def _pick_fields(payload: dict[str, Any], fields: list[str]) -> dict[str, Any]:
    return {field: payload.get(field) for field in fields if field in payload}


def _derive_clerk_jwks_url(metadata: dict[str, Any]) -> str | None:
    explicit = str(metadata.get("jwks_url") or "").strip()
    if explicit:
        return explicit
    issuer = str(metadata.get("issuer") or "").strip().rstrip("/")
    if issuer:
        return f"{issuer}/.well-known/jwks.json"
    frontend_api_url = str(metadata.get("frontend_api_url") or "").strip().rstrip("/")
    if frontend_api_url:
        return f"{frontend_api_url}/.well-known/jwks.json"
    return None


def _normalize_route_path(value: Any) -> str | None:
    path = str(value or "").strip()
    return path or None


def _normalize_session_id(value: Any) -> str | None:
    session_id = str(value or "").strip()
    return session_id or None


def _is_sensitive_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in _SAFE_KEY_EXACT:
        return False
    if lowered in _SENSITIVE_KEY_EXACT:
        return True
    return any(part in lowered for part in _SENSITIVE_KEY_PARTS)


def _redact_payload(value: Any) -> Any:
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_str = str(key)
            if _is_sensitive_key(key_str):
                redacted[key_str] = "***redacted***"
            else:
                redacted[key_str] = _redact_payload(item)
        return redacted
    if isinstance(value, list):
        return [_redact_payload(item) for item in value]
    if isinstance(value, tuple):
        return [_redact_payload(item) for item in value]
    if isinstance(value, str) and len(value) > 4000:
        return f"{value[:4000]}…"
    return value


def _compact_text_payload(payload: dict[str, Any]) -> str | None:
    for key in ("question", "prompt", "summary", "intent_summary", "query", "focus"):
        value = str(payload.get(key) or "").strip()
        if value:
            return value[:1000]
    return None


def _stable_gap_key(parts: list[str | None]) -> str:
    normalized = "||".join(str(part or "").strip().lower() for part in parts)
    return hashlib.sha256(normalized.encode("utf-8")).hexdigest()


class AgentBootstrapSkill(Skill):
    """Store broker state for downstream Codex agents and expose machine-readable app knowledge."""

    def __init__(self, *, storage: OwnerCiStorage) -> None:
        super().__init__(memory=None)
        self._storage = storage

    @property
    def metadata(self) -> SkillMetadata:
        return SkillMetadata(
            name="agent_bootstrap",
            description=(
                "Owner-scoped agent bootstrap, app manifests, broker state, "
                "and publish candidates"
            ),
            version="0.3.0",
            permissions=PermissionSet({Permission.ADMIN, Permission.READ_CONFIG}),
            intents=[
                "agent_client_bootstrap",
                "agent_client_manifest_get",
                "agent_docs_list",
                "agent_docs_get",
                "agent_session_create",
                "agent_session_interactions_list",
                "agent_session_gaps_list",
                "agent_apps_list",
                "agent_app_manifest_get",
                "agent_app_services_list",
                "agent_service_read",
                "agent_service_request_submit",
                "agent_operation_resolve",
                "agent_operation_event_ingest",
                "agent_operation_poll",
                "agent_operation_list",
                "agent_operation_get",
                "agent_operation_evidence_list",
                "agent_operation_logs",
                "agent_operation_incidents_list",
                "agent_repo_discover",
                "agent_repo_enroll",
                "agent_workspace_bundle_create",
                "agent_workspace_bundle_get",
                "agent_test_plan_compile",
                "agent_publish_candidate_submit",
                "agent_publish_candidate_apply",
                "agent_managed_repo_enforce",
                "agent_principal_upsert",
                "agent_principal_list",
                "agent_connector_get",
                "agent_connector_health",
                "agent_connector_upsert",
                "agent_connector_list",
                "agent_connector_rotate",
                "agent_principal_grants_put",
                "agent_app_upsert",
                "agent_app_list",
                "agent_knowledge_pack_upsert",
                "agent_audit_list",
                "agent_secret_ref_upsert",
                "agent_secret_ref_list",
                "agent_gap_list",
                "agent_gap_get",
                "agent_gap_update",
            ],
        )

    async def initialize(self) -> bool:
        log.info("agent_bootstrap_initialized")
        return True

    async def handle(self, request: SkillRequest) -> SkillResponse:
        owner_id = _normalize_owner_id(request)
        public_base_url = str(request.context.get("public_base_url") or "").strip()
        explicit_owner_id = str(request.context.get("owner_id") or "").strip()
        if not explicit_owner_id and request.intent in {
            "agent_operation_event_ingest",
            "agent_operation_poll",
        }:
            inferred_owner_id = await self._infer_owner_id(request.context)
            if inferred_owner_id:
                owner_id = inferred_owner_id
            elif request.intent == "agent_operation_event_ingest":
                return SkillResponse.error_response(
                    request.id,
                    "owner_id or an enrolled app_id is required for system operation ingestion",
                )
        await self._ensure_default_docs(owner_id, public_base_url)
        await self._ensure_default_apps(owner_id, public_base_url)
        await self._ensure_default_service_capabilities(owner_id)

        handlers = {
            "agent_client_bootstrap": self._handle_client_bootstrap,
            "agent_client_manifest_get": self._handle_client_manifest_get,
            "agent_docs_list": self._handle_docs_list,
            "agent_docs_get": self._handle_docs_get,
            "agent_session_create": self._handle_session_create,
            "agent_session_interactions_list": self._handle_session_interactions_list,
            "agent_session_gaps_list": self._handle_session_gaps_list,
            "agent_apps_list": self._handle_apps_list,
            "agent_app_manifest_get": self._handle_app_manifest_get,
            "agent_app_services_list": self._handle_app_services_list,
            "agent_service_read": self._handle_service_read,
            "agent_service_request_submit": self._handle_service_request_submit,
            "agent_operation_resolve": self._handle_operation_resolve,
            "agent_operation_event_ingest": self._handle_operation_event_ingest,
            "agent_operation_poll": self._handle_operation_poll,
            "agent_operation_list": self._handle_operation_list,
            "agent_operation_get": self._handle_operation_get,
            "agent_operation_evidence_list": self._handle_operation_evidence_list,
            "agent_operation_logs": self._handle_operation_logs,
            "agent_operation_incidents_list": self._handle_operation_incidents_list,
            "agent_repo_discover": self._handle_repo_discover,
            "agent_repo_enroll": self._handle_repo_enroll,
            "agent_workspace_bundle_create": self._handle_workspace_bundle_create,
            "agent_workspace_bundle_get": self._handle_workspace_bundle_get,
            "agent_test_plan_compile": self._handle_test_plan_compile,
            "agent_publish_candidate_submit": self._handle_publish_candidate_submit,
            "agent_publish_candidate_apply": self._handle_publish_candidate_apply,
            "agent_managed_repo_enforce": self._handle_managed_repo_enforce,
            "agent_principal_upsert": self._handle_principal_upsert,
            "agent_principal_list": self._handle_principal_list,
            "agent_connector_get": self._handle_connector_get,
            "agent_connector_health": self._handle_connector_health,
            "agent_connector_upsert": self._handle_connector_upsert,
            "agent_connector_list": self._handle_connector_list,
            "agent_connector_rotate": self._handle_connector_rotate,
            "agent_principal_grants_put": self._handle_principal_grants_put,
            "agent_app_upsert": self._handle_app_upsert,
            "agent_app_list": self._handle_app_list,
            "agent_knowledge_pack_upsert": self._handle_knowledge_pack_upsert,
            "agent_audit_list": self._handle_audit_list,
            "agent_secret_ref_upsert": self._handle_secret_ref_upsert,
            "agent_secret_ref_list": self._handle_secret_ref_list,
            "agent_gap_list": self._handle_gap_list,
            "agent_gap_get": self._handle_gap_get,
            "agent_gap_update": self._handle_gap_update,
        }
        handler = handlers.get(request.intent)
        if handler is None:
            return SkillResponse.error_response(
                request.id,
                f"Unknown agent bootstrap intent: {request.intent}",
            )
        try:
            response = await handler(request, owner_id, public_base_url)
        except ValueError as exc:
            await self._record_interaction_outcome(
                request,
                owner_id=owner_id,
                public_base_url=public_base_url,
                success=False,
                error_message=str(exc),
            )
            return SkillResponse.error_response(request.id, str(exc))
        await self._record_interaction_outcome(
            request,
            owner_id=owner_id,
            public_base_url=public_base_url,
            success=True,
            response=response,
        )
        return response

    async def _handle_client_bootstrap(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        client_id = str(request.context.get("client_id") or "").strip()
        if not client_id:
            return SkillResponse.error_response(request.id, "client_id is required")
        manifest_payload = dict(request.context.get("manifest") or {})
        if not manifest_payload:
            return SkillResponse.error_response(request.id, "manifest is required")
        stored_manifest = await self._storage.store_agent_bootstrap_manifest(
            owner_id,
            client_id,
            manifest_payload,
        )
        receipt = await self._storage.store_agent_setup_receipt(
            owner_id,
            client_id=client_id,
            receipt={
                "status": "stored",
                "steps": list(request.context.get("steps") or []),
                "stored_manifest_version": manifest_payload.get("version") or "v1",
            },
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Stored bootstrap manifest for `{client_id}`.",
            data={"manifest": stored_manifest, "receipt": receipt},
        )

    async def _handle_client_manifest_get(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        client_id = str(request.context.get("client_id") or "").strip()
        if not client_id:
            return SkillResponse.error_response(request.id, "client_id is required")
        stored_manifest = await self._storage.get_agent_bootstrap_manifest(owner_id, client_id)
        if stored_manifest is None:
            return SkillResponse.error_response(request.id, f"Manifest `{client_id}` not found")
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded bootstrap manifest for `{client_id}`.",
            data={"manifest": stored_manifest},
        )

    async def _handle_docs_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip() or None
        query = str(request.context.get("query") or "").strip().lower()
        docs = await self._storage.list_agent_docs_manifests(owner_id)
        allowed_slugs: set[str] | None = None
        if app_id:
            app_profile = await self._require_app_access(
                owner_id,
                principal_id=str(request.context.get("principal_id") or "").strip() or None,
                app_id=app_id,
            )
            allowed_slugs = {
                str(slug)
                for slug in list((app_profile.get("profile") or {}).get("docs_slugs") or [])
                if str(slug).strip()
            }
        filtered: list[dict[str, Any]] = []
        for doc in docs:
            if allowed_slugs is not None and str(doc["slug"]) not in allowed_slugs:
                continue
            if query:
                haystack = " ".join(
                    [
                        str(doc.get("slug") or ""),
                        str(doc.get("title") or ""),
                        str((doc.get("manifest") or {}).get("content_markdown") or ""),
                    ]
                ).lower()
                if query not in haystack:
                    continue
            filtered.append(doc)
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(filtered)} docs manifests.",
            data={"docs": filtered},
        )

    async def _handle_docs_get(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        slug = str(request.context.get("slug") or "").strip()
        if not slug:
            return SkillResponse.error_response(request.id, "slug is required")
        app_id = str(request.context.get("app_id") or "").strip() or None
        docs_manifest = await self._storage.get_agent_docs_manifest(owner_id, slug)
        if docs_manifest is None:
            if app_id:
                await self._record_gap(
                    owner_id=owner_id,
                    principal_id=str(request.context.get("principal_id") or "").strip() or None,
                    session_id=_normalize_session_id(request.context.get("session_id")),
                    app_id=app_id,
                    repo_id=None,
                    gap_type="missing_docs",
                    severity="medium",
                    blocker=False,
                    detected_from="docs_get",
                    required_capability=slug,
                    observed_request={"slug": slug},
                    suggested_fix=f"Add docs manifest `{slug}` to the app docs bundle.",
                    metadata={},
                    run_id=None,
                )
            return SkillResponse.error_response(request.id, f"Docs manifest `{slug}` not found")
        if app_id:
            app_profile = await self._require_app_access(
                owner_id,
                principal_id=str(request.context.get("principal_id") or "").strip() or None,
                app_id=app_id,
            )
            allowed_slugs = {
                str(item)
                for item in list((app_profile.get("profile") or {}).get("docs_slugs") or [])
            }
            if slug not in allowed_slugs:
                return SkillResponse.error_response(
                    request.id, f"Docs manifest `{slug}` is not allowed"
                )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded docs manifest `{slug}`.",
            data={"doc": docs_manifest},
        )

    async def _handle_session_create(
        self,
        request: SkillRequest,
        owner_id: str,
        public_base_url: str,
    ) -> SkillResponse:
        principal_id = _normalize_principal_id(request)
        existing = await self._storage.get_agent_principal(owner_id, principal_id)
        if existing is None:
            display_name = (
                str(request.context.get("display_name") or principal_id).strip() or principal_id
            )
            principal = await self._storage.upsert_agent_principal(
                owner_id,
                principal_id=principal_id,
                display_name=display_name,
                principal_type=str(request.context.get("principal_type") or "codex").strip()
                or "codex",
                allowed_scopes=[
                    str(scope).strip()
                    for scope in list(request.context.get("allowed_scopes") or ["cgs:agent"])
                    if str(scope).strip()
                ],
                metadata=dict(request.context.get("metadata") or {}),
                active=True,
            )
        else:
            principal = existing
        apps = await self._list_accessible_apps(owner_id, principal_id)
        session = await self._storage.create_agent_session(
            owner_id,
            principal_id=principal_id,
            metadata={
                "public_base_url": public_base_url or None,
                "allowed_scopes": list(request.context.get("allowed_scopes") or []),
                "subject": str(request.context.get("subject") or request.user_id or ""),
            },
            app_id=(
                str((request.context.get("app_ids") or [None])[0] or "").strip() or None
                if isinstance(request.context.get("app_ids"), list)
                else None
            ),
            session_id=_normalize_session_id(request.context.get("session_id")),
        )
        bundle = {
            "owner_id": owner_id,
            "session_id": session["session_id"],
            "principal": principal,
            "accessible_apps": apps,
            "routes": {
                "apps": "/service/ai/v1/agent/apps",
                "workspace_bundle_create": "/service/ai/v1/agent/apps/:appId/workspace-bundles",
                "workspace_bundle_get": "/service/ai/v1/agent/workspace-bundles/:bundleId",
                "test_plan_compile": "/service/ai/v1/agent/apps/:appId/test-plans/compile",
                "publish_candidates": "/service/ai/v1/agent/apps/:appId/publish-candidates",
                "operations": "/service/ai/v1/agent/operations",
                "operation": "/service/ai/v1/agent/operations/:operationId",
                "operation_evidence": "/service/ai/v1/agent/operations/:operationId/evidence",
                "operation_logs": "/service/ai/v1/agent/operations/:operationId/logs",
                "operation_incidents": "/service/ai/v1/agent/operations/:operationId/incidents",
                "operation_resolve": "/service/ai/v1/agent/apps/:appId/operations/resolve",
                "services": "/service/ai/v1/agent/apps/:appId/services",
                "service_read": "/service/ai/v1/agent/apps/:appId/services/:serviceKind",
                "service_requests": "/service/ai/v1/agent/apps/:appId/service-requests",
                "logs": "/service/ai/v1/agent/runs/:runId/logs",
                "resources": "/service/ai/v1/agent/runs/:runId/resources",
                "session_interactions": "/service/ai/v1/agent/sessions/:sessionId/interactions",
                "session_gaps": "/service/ai/v1/agent/sessions/:sessionId/gaps",
            },
            "public_base_url": public_base_url or None,
            "bootstrap_model": "broker_only",
            "github_governance": {
                "write_principal": "zetherion",
                "agent_push_enabled": False,
                "publish_flow": "publish_candidate_only",
            },
        }
        await self._storage.record_agent_audit_event(
            owner_id,
            principal_id=principal_id,
            app_id=None,
            service_kind=None,
            resource="session",
            action="agent.session.create",
            decision="allowed",
            audit={"app_count": len(apps)},
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Prepared session bundle for `{principal_id}`.",
            data={"session": bundle},
        )

    async def _handle_session_interactions_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        session_id = str(request.context.get("session_id") or "").strip()
        if not session_id:
            return SkillResponse.error_response(request.id, "session_id is required")
        interactions = await self._storage.list_agent_session_interactions(
            owner_id,
            session_id,
            limit=int(request.context.get("limit") or 100),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(interactions)} interactions.",
            data={"interactions": interactions},
        )

    async def _handle_session_gaps_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        session_id = str(request.context.get("session_id") or "").strip()
        if not session_id:
            return SkillResponse.error_response(request.id, "session_id is required")
        gaps = await self._storage.list_agent_gap_events(
            owner_id,
            session_id=session_id,
            unresolved_only=bool(request.context.get("unresolved_only", False)),
            limit=int(request.context.get("limit") or 100),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(gaps)} gaps.",
            data={"gaps": gaps},
        )

    async def _handle_apps_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        apps = (
            await self._list_accessible_apps(owner_id, principal_id)
            if principal_id
            else await self._storage.list_agent_app_profiles(owner_id)
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(apps)} apps.",
            data={"apps": apps},
        )

    async def _handle_app_manifest_get(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        if not app_id:
            return SkillResponse.error_response(request.id, "app_id is required")
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        app_profile = await self._require_app_access(
            owner_id, principal_id=principal_id, app_id=app_id
        )
        knowledge_pack = await self._storage.get_agent_knowledge_pack(owner_id, app_id)
        if knowledge_pack is None:
            return SkillResponse.error_response(request.id, f"Knowledge pack `{app_id}` not found")
        repo_id = (
            str(((app_profile.get("profile") or {}).get("repo_ids") or [None])[0] or "").strip()
            or None
        )
        gaps = await self._storage.list_agent_gap_events(
            owner_id,
            app_id=app_id,
            repo_id=repo_id,
            unresolved_only=True,
            limit=200,
        )
        docs = []
        for slug in list((app_profile.get("profile") or {}).get("docs_slugs") or []):
            manifest = await self._storage.get_agent_docs_manifest(owner_id, str(slug))
            if manifest is not None:
                docs.append(manifest)
        pack = dict(knowledge_pack.get("pack") or {})
        pack["known_gaps_summary"] = {
            "open_total": len(gaps),
            "blocker_total": sum(1 for gap in gaps if bool(gap.get("blocker"))),
            "recent_gap_ids": [str(gap.get("gap_id") or "") for gap in gaps[:5]],
        }
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded app manifest for `{app_id}`.",
            data={
                "app": app_profile,
                "knowledge_pack": {**knowledge_pack, "pack": pack},
                "docs": docs,
                "services": self._list_app_services(app_profile),
            },
        )

    async def _handle_app_services_list(
        self,
        request: SkillRequest,
        owner_id: str,
        public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        if not app_id:
            return SkillResponse.error_response(request.id, "app_id is required")
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        app_profile = await self._require_app_access(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
        )
        services = self._list_app_services(app_profile, public_base_url=public_base_url)
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(services)} brokered services for `{app_id}`.",
            data={"services": services},
        )

    async def _handle_service_read(
        self,
        request: SkillRequest,
        owner_id: str,
        public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        service_kind = str(request.context.get("service_kind") or "").strip().lower()
        if not app_id or not service_kind:
            return SkillResponse.error_response(
                request.id,
                "app_id and service_kind are required",
            )
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        app_profile = await self._require_app_access(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
        )
        view = str(request.context.get("view") or "overview").strip().lower() or "overview"
        data = await self._read_service_view(
            owner_id=owner_id,
            principal_id=principal_id,
            app_id=app_id,
            app_profile=app_profile,
            service_kind=service_kind,
            view=view,
            public_base_url=public_base_url,
            request_context=request.context,
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {service_kind} broker view `{view}` for `{app_id}`.",
            data=data,
        )

    async def _handle_service_request_submit(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        service_kind = str(request.context.get("service_kind") or "").strip().lower()
        action_id = str(request.context.get("action_id") or "").strip()
        if not app_id or not service_kind or not action_id:
            return SkillResponse.error_response(
                request.id,
                "app_id, service_kind, and action_id are required",
            )
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        app_profile = await self._require_app_access(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
        )
        result = await self._execute_service_action(
            owner_id=owner_id,
            principal_id=principal_id,
            app_id=app_id,
            app_profile=app_profile,
            service_kind=service_kind,
            action_id=action_id,
            request_context=request.context,
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Processed {service_kind} service action `{action_id}` for `{app_id}`.",
            data=result,
        )

    async def _handle_operation_resolve(
        self,
        request: SkillRequest,
        owner_id: str,
        public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        if not app_id:
            return SkillResponse.error_response(request.id, "app_id is required")
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        app_profile = await self._require_app_access(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
        )
        operation_refs = self._extract_operation_refs(request.context)
        if not operation_refs:
            return SkillResponse.error_response(
                request.id,
                "At least one operation reference is required",
            )
        repo_id = str(
            request.context.get("repo_id")
            or ((app_profile.get("profile") or {}).get("repo_ids") or [None])[0]
            or ""
        ).strip()
        operation = await self._find_or_create_operation(
            owner_id=owner_id,
            app_id=app_id,
            repo_id=repo_id,
            refs=operation_refs,
            request_context=request.context,
        )
        await self._refresh_operation(
            owner_id=owner_id,
            principal_id=principal_id,
            app_id=app_id,
            app_profile=app_profile,
            operation=operation,
            request_context=request.context,
            public_base_url=public_base_url,
        )
        hydrated = await self._storage.get_operation_hydrated(owner_id, operation["operation_id"])
        return SkillResponse(
            request_id=request.id,
            message=f"Resolved operation `{operation['operation_id']}` for `{app_id}`.",
            data={"operation": hydrated},
        )

    async def _handle_operation_event_ingest(
        self,
        request: SkillRequest,
        owner_id: str,
        public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        service_kind = str(request.context.get("service_kind") or "").strip().lower()
        if not app_id or not service_kind:
            return SkillResponse.error_response(
                request.id,
                "app_id and service_kind are required",
            )
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        app_profile = await self._require_app_access(
            owner_id,
            principal_id=_system_safe_principal_id(principal_id),
            app_id=app_id,
        )
        repo_id = str(
            request.context.get("repo_id")
            or ((app_profile.get("profile") or {}).get("repo_ids") or [None])[0]
            or ""
        ).strip()
        event_payload = self._normalize_event_payload(request.context.get("event_payload"))
        operation_refs = {
            **self._extract_operation_refs(request.context),
            **self._extract_operation_refs_from_event(service_kind, event_payload),
        }
        if not operation_refs:
            gap = await self._record_gap(
                owner_id=owner_id,
                principal_id=principal_id,
                session_id=_normalize_session_id(request.context.get("session_id")),
                app_id=app_id,
                repo_id=repo_id,
                gap_type="missing_provider_correlation",
                severity="high",
                blocker=False,
                detected_from="operation_event_ingest",
                required_capability=f"{service_kind}:correlation_refs",
                observed_request={
                    "service_kind": service_kind,
                    "event_payload": _redact_payload(event_payload),
                },
                suggested_fix=(
                    "Provide a correlation ref such as git_sha, github_run_id, "
                    "vercel_deployment_id, stripe_event_id, or run_id."
                ),
                metadata={"source": request.context.get("source") or "provider_event"},
                run_id=str(request.context.get("run_id") or "").strip() or None,
            )
            return SkillResponse(
                request_id=request.id,
                message=f"Recorded orphaned {service_kind} event gap.",
                data={"gap": gap},
            )
        operation = await self._find_or_create_operation(
            owner_id=owner_id,
            app_id=app_id,
            repo_id=repo_id,
            refs=operation_refs,
            request_context={
                **dict(request.context),
                **operation_refs,
                "operation_kind": str(request.context.get("operation_kind") or "provider_event"),
            },
        )
        await self._record_operation_event_ingest(
            owner_id=owner_id,
            principal_id=principal_id,
            app_id=app_id,
            repo_id=repo_id,
            operation_id=str(operation["operation_id"]),
            service_kind=service_kind,
            refs=operation_refs,
            event_payload=event_payload,
            request_context=request.context,
        )
        await self._refresh_operation(
            owner_id=owner_id,
            principal_id=principal_id,
            app_id=app_id,
            app_profile=app_profile,
            operation=operation,
            request_context={
                **dict(request.context),
                **operation_refs,
                "service_kind": service_kind,
            },
            public_base_url=public_base_url,
        )
        hydrated = await self._storage.get_operation_hydrated(owner_id, operation["operation_id"])
        return SkillResponse(
            request_id=request.id,
            message=f"Ingested {service_kind} event for `{app_id}`.",
            data={"operation": hydrated},
        )

    async def _handle_operation_poll(
        self,
        request: SkillRequest,
        owner_id: str,
        public_base_url: str,
    ) -> SkillResponse:
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        app_id = str(request.context.get("app_id") or "").strip() or None
        service_kind = str(request.context.get("service_kind") or "").strip().lower() or None
        operation_id = str(request.context.get("operation_id") or "").strip() or None
        limit = _normalize_limit(request.context.get("limit"), default=10, maximum=50)
        operations: list[dict[str, Any]] = []
        if operation_id:
            operation = await self._storage.get_managed_operation(owner_id, operation_id)
            if operation is None:
                return SkillResponse.error_response(
                    request.id,
                    f"Operation `{operation_id}` not found",
                )
            operations = [operation]
        else:
            operations = await self._storage.list_managed_operations(
                owner_id,
                app_id=app_id,
                service_kind=service_kind,
                status=str(request.context.get("status") or "").strip() or "active",
                limit=limit,
            )
        refreshed: list[dict[str, Any]] = []
        for operation in operations:
            target_app_id = str(operation.get("app_id") or "")
            app_profile = await self._require_app_access(
                owner_id,
                principal_id=_system_safe_principal_id(principal_id),
                app_id=target_app_id,
            )
            await self._refresh_operation(
                owner_id=owner_id,
                principal_id=principal_id,
                app_id=target_app_id,
                app_profile=app_profile,
                operation=operation,
                request_context={
                    **dict(request.context),
                    "app_id": target_app_id,
                    "operation_id": str(operation.get("operation_id") or ""),
                    "service_kind": service_kind,
                    "source": request.context.get("source") or "poll",
                },
                public_base_url=public_base_url,
            )
            hydrated = await self._storage.get_operation_hydrated(
                owner_id,
                str(operation["operation_id"]),
            )
            if hydrated is not None:
                refreshed.append(hydrated)
        return SkillResponse(
            request_id=request.id,
            message=f"Refreshed {len(refreshed)} operations.",
            data={"operations": refreshed},
        )

    async def _handle_operation_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        requested_app_id = str(request.context.get("app_id") or "").strip() or None
        requested_repo_id = str(request.context.get("repo_id") or "").strip() or None
        operations = await self._storage.list_managed_operations(
            owner_id,
            app_id=requested_app_id,
            repo_id=requested_repo_id,
            service_kind=str(request.context.get("service_kind") or "").strip() or None,
            status=str(request.context.get("status") or "").strip() or None,
            limit=int(request.context.get("limit") or 50),
        )
        if principal_id:
            accessible = {
                str(app["app_id"])
                for app in await self._list_accessible_apps(owner_id, principal_id)
            }
            operations = [
                operation
                for operation in operations
                if str(operation.get("app_id") or "") in accessible
            ]
        payload: list[dict[str, Any]] = []
        for operation in operations:
            incidents = await self._storage.list_operation_incidents(
                owner_id,
                str(operation["operation_id"]),
                unresolved_only=False,
                limit=5,
            )
            refs = await self._storage.list_operation_refs(
                owner_id,
                str(operation["operation_id"]),
            )
            payload.append(
                {
                    **operation,
                    "refs": refs,
                    "top_incident": incidents[0] if incidents else None,
                    "incident_count": len(incidents),
                }
            )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(payload)} operations.",
            data={"operations": payload},
        )

    async def _handle_operation_get(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        operation_id = str(request.context.get("operation_id") or "").strip()
        if not operation_id:
            return SkillResponse.error_response(request.id, "operation_id is required")
        operation = await self._storage.get_operation_hydrated(owner_id, operation_id)
        if operation is None:
            return SkillResponse.error_response(request.id, f"Operation `{operation_id}` not found")
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        if principal_id:
            await self._require_app_access(
                owner_id,
                principal_id=principal_id,
                app_id=str(operation.get("app_id") or ""),
            )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded operation `{operation_id}`.",
            data={"operation": operation},
        )

    async def _handle_operation_evidence_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        operation_id = str(request.context.get("operation_id") or "").strip()
        if not operation_id:
            return SkillResponse.error_response(request.id, "operation_id is required")
        operation = await self._storage.get_managed_operation(owner_id, operation_id)
        if operation is None:
            return SkillResponse.error_response(request.id, f"Operation `{operation_id}` not found")
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        if principal_id:
            await self._require_app_access(
                owner_id,
                principal_id=principal_id,
                app_id=str(operation.get("app_id") or ""),
            )
        evidence = await self._storage.list_operation_evidence(
            owner_id,
            operation_id,
            service_kind=str(request.context.get("service_kind") or "").strip() or None,
            evidence_type=str(request.context.get("evidence_type") or "").strip() or None,
            limit=int(request.context.get("limit") or 200),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(evidence)} evidence items.",
            data={"evidence": evidence},
        )

    async def _handle_operation_logs(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        operation_id = str(request.context.get("operation_id") or "").strip()
        if not operation_id:
            return SkillResponse.error_response(request.id, "operation_id is required")
        operation = await self._storage.get_managed_operation(owner_id, operation_id)
        if operation is None:
            return SkillResponse.error_response(request.id, f"Operation `{operation_id}` not found")
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        if principal_id:
            await self._require_app_access(
                owner_id,
                principal_id=principal_id,
                app_id=str(operation.get("app_id") or ""),
            )
        logs = await self._storage.get_operation_log_chunks(
            owner_id,
            operation_id,
            query_text=str(request.context.get("query") or "").strip() or None,
            limit=int(request.context.get("limit") or 200),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(logs)} operation log chunks.",
            data={"logs": logs},
        )

    async def _handle_operation_incidents_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        operation_id = str(request.context.get("operation_id") or "").strip()
        if not operation_id:
            return SkillResponse.error_response(request.id, "operation_id is required")
        operation = await self._storage.get_managed_operation(owner_id, operation_id)
        if operation is None:
            return SkillResponse.error_response(request.id, f"Operation `{operation_id}` not found")
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        if principal_id:
            await self._require_app_access(
                owner_id,
                principal_id=principal_id,
                app_id=str(operation.get("app_id") or ""),
            )
        incidents = await self._storage.list_operation_incidents(
            owner_id,
            operation_id,
            unresolved_only=bool(request.context.get("unresolved_only", False)),
            limit=int(request.context.get("limit") or 200),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(incidents)} incidents.",
            data={"incidents": incidents},
        )

    async def _handle_repo_discover(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        principal = (
            await self._storage.get_agent_principal(owner_id, principal_id)
            if principal_id
            else None
        )
        scopes = {
            str(scope).strip()
            for scope in list(request.context.get("allowed_scopes") or [])
            if str(scope).strip()
        }
        if principal_id and principal is None:
            return SkillResponse.error_response(
                request.id,
                f"Principal `{principal_id}` is not registered",
            )
        if principal_id and not ({"cgs:agent", "cgs:agent:discover"} & scopes):
            return SkillResponse.error_response(
                request.id,
                "Principal is not allowed to discover brokered repositories",
            )
        repositories = await self._discover_github_repositories(
            owner_id,
            connector_id=str(request.context.get("connector_id") or "").strip() or None,
            query=str(request.context.get("query") or "").strip() or None,
            limit=max(1, min(int(request.context.get("limit") or 25), 100)),
            private_only=bool(request.context.get("private_only", True)),
        )
        apps = await self._storage.list_agent_app_profiles(owner_id)
        managed_by_repo = {
            github_repo
            for app in apps
            for github_repo in list((app.get("profile") or {}).get("github_repos") or [])
        }
        payload = []
        for repository in repositories:
            repo_dict = dict(repository)
            repo_dict["managed"] = repo_dict["full_name"] in managed_by_repo
            payload.append(repo_dict)
        await self._storage.record_agent_audit_event(
            owner_id,
            principal_id=principal_id,
            app_id=None,
            service_kind="github",
            resource="discoverable_repositories",
            action="repo.discover",
            decision="allowed",
            audit={"count": len(payload)},
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(payload)} discoverable repositories.",
            data={"repositories": payload},
        )

    async def _handle_repo_enroll(
        self,
        request: SkillRequest,
        owner_id: str,
        public_base_url: str,
    ) -> SkillResponse:
        github_repo = str(
            request.context.get("github_repo") or request.context.get("repo_full_name") or ""
        ).strip()
        if not github_repo:
            return SkillResponse.error_response(request.id, "github_repo is required")
        app_id = str(request.context.get("app_id") or "").strip() or None
        display_name = str(request.context.get("display_name") or "").strip() or None
        stack_kind = str(request.context.get("stack_kind") or "generic").strip() or "generic"
        enrolled = await self._enroll_github_repository(
            owner_id=owner_id,
            github_repo=github_repo,
            app_id=app_id,
            display_name=display_name,
            stack_kind=stack_kind,
            public_base_url=public_base_url,
            overrides=dict(request.context.get("profile_overrides") or {}),
            enforce_managed_repo=bool(request.context.get("enforce_managed_repo", True)),
            principal_id=str(request.context.get("principal_id") or "").strip() or None,
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Enrolled `{github_repo}` into managed broker control.",
            data=enrolled,
        )

    async def _handle_workspace_bundle_create(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        if not app_id:
            return SkillResponse.error_response(request.id, "app_id is required")
        principal_id = _normalize_principal_id(request)
        app_profile = await self._require_app_access(
            owner_id, principal_id=principal_id, app_id=app_id
        )
        repo_id = str(
            request.context.get("repo_id")
            or ((app_profile.get("profile") or {}).get("repo_ids") or [None])[0]
            or ""
        ).strip()
        if not repo_id:
            return SkillResponse.error_response(request.id, "repo_id is required")
        repo = await self._resolve_repo_profile(owner_id, repo_id)
        knowledge_pack = await self._storage.get_agent_knowledge_pack(owner_id, app_id)
        git_ref = str(
            request.context.get("git_ref") or repo.get("default_branch") or "main"
        ).strip()
        bundle_payload, resolved_ref = await self._create_workspace_bundle_payload(
            owner_id=owner_id,
            repo=repo,
            knowledge_pack=knowledge_pack["pack"] if knowledge_pack else {},
            git_ref=git_ref,
        )
        bundle = await self._storage.create_workspace_bundle(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
            repo_id=repo_id,
            git_ref=git_ref,
            resolved_ref=resolved_ref,
            bundle=bundle_payload,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )
        await self._storage.record_agent_audit_event(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
            service_kind="github",
            resource=repo_id,
            action="workspace_bundle.create",
            decision="allowed",
            audit={
                "bundle_id": bundle["bundle_id"],
                "git_ref": git_ref,
                "resolved_ref": resolved_ref,
            },
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Created workspace bundle `{bundle['bundle_id']}`.",
            data={"bundle": bundle},
        )

    async def _handle_workspace_bundle_get(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        bundle_id = str(request.context.get("bundle_id") or "").strip()
        if not bundle_id:
            return SkillResponse.error_response(request.id, "bundle_id is required")
        principal_id = _normalize_principal_id(request)
        bundle = await self._storage.get_workspace_bundle(owner_id, bundle_id)
        if bundle is None:
            return SkillResponse.error_response(
                request.id, f"Workspace bundle `{bundle_id}` not found"
            )
        if str(bundle.get("principal_id") or "") != principal_id:
            return SkillResponse.error_response(
                request.id, "Workspace bundle is not available for this principal"
            )
        await self._storage.mark_workspace_bundle_downloaded(owner_id, bundle_id)
        await self._storage.record_agent_audit_event(
            owner_id,
            principal_id=principal_id,
            app_id=str(bundle.get("app_id") or ""),
            service_kind="github",
            resource=str(bundle.get("repo_id") or ""),
            action="workspace_bundle.get",
            decision="allowed",
            audit={"bundle_id": bundle_id},
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded workspace bundle `{bundle_id}`.",
            data={"bundle": bundle},
        )

    async def _handle_test_plan_compile(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        if not app_id:
            return SkillResponse.error_response(request.id, "app_id is required")
        principal_id = _normalize_principal_id(request)
        app_profile = await self._require_app_access(
            owner_id, principal_id=principal_id, app_id=app_id
        )
        repo_id = str(
            request.context.get("repo_id")
            or ((app_profile.get("profile") or {}).get("repo_ids") or [None])[0]
            or ""
        ).strip()
        repo = await self._resolve_repo_profile(owner_id, repo_id)
        git_ref = str(
            request.context.get("git_ref") or repo.get("default_branch") or "main"
        ).strip()
        mode = str(request.context.get("mode") or "fast").strip().lower()
        knowledge_pack = await self._storage.get_agent_knowledge_pack(owner_id, app_id)
        gaps = await self._detect_test_plan_gaps(
            owner_id=owner_id,
            principal_id=principal_id,
            session_id=_normalize_session_id(request.context.get("session_id")),
            app_id=app_id,
            repo_id=repo_id,
            knowledge_pack=dict((knowledge_pack or {}).get("pack") or {}),
            request_context=request.context,
        )
        if gaps:
            missing = ", ".join(
                sorted({str(gap.get("required_capability") or gap.get("gap_type")) for gap in gaps})
            )
            raise ValueError("Test plan compile blocked by unresolved capability gaps: " + missing)
        controller = CiControllerSkill(storage=self._storage)
        compiled = controller._compile_run_plan(repo=repo, mode=mode, git_ref=git_ref)
        plan = {
            **compiled,
            "app_id": app_id,
            "prefer_mock_mode": bool(request.context.get("prefer_mock_mode", False)),
            "changed_files": list(request.context.get("changed_files") or []),
            "focus": str(request.context.get("focus") or "").strip() or None,
        }
        stored = await self._storage.create_compiled_plan(
            owner_id=owner_id,
            repo_id=repo_id,
            git_ref=git_ref,
            mode=mode,
            plan=plan,
            metadata={
                "app_id": app_id,
                "principal_id": principal_id,
                "prefer_mock_mode": bool(request.context.get("prefer_mock_mode", False)),
                "capability_registry": dict(
                    (knowledge_pack or {}).get("pack", {}).get("capability_registry") or {}
                ),
            },
        )
        await self._storage.record_agent_audit_event(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
            service_kind=None,
            resource=repo_id,
            action="test_plan.compile",
            decision="allowed",
            audit={"compiled_plan_id": stored["compiled_plan_id"], "mode": mode},
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Compiled test plan `{stored['compiled_plan_id']}` for `{app_id}`.",
            data={"compiled_plan": stored},
        )

    async def _handle_publish_candidate_submit(
        self,
        request: SkillRequest,
        owner_id: str,
        public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        if not app_id:
            return SkillResponse.error_response(request.id, "app_id is required")
        principal_id = _normalize_principal_id(request)
        app_profile = await self._require_app_access(
            owner_id, principal_id=principal_id, app_id=app_id
        )
        repo_id = str(
            request.context.get("repo_id")
            or ((app_profile.get("profile") or {}).get("repo_ids") or [None])[0]
            or ""
        ).strip()
        base_sha = str(request.context.get("base_sha") or "").strip()
        if not base_sha:
            return SkillResponse.error_response(request.id, "base_sha is required")
        diff_text = str(request.context.get("diff_text") or "").strip()
        patch_bundle_base64 = str(request.context.get("patch_bundle_base64") or "").strip()
        if not diff_text and not patch_bundle_base64:
            return SkillResponse.error_response(
                request.id,
                "diff_text or patch_bundle_base64 is required",
            )
        candidate = await self._storage.create_publish_candidate(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
            repo_id=repo_id,
            base_sha=base_sha,
            candidate={
                "base_sha": base_sha,
                "candidate_type": "text/x-diff" if diff_text else "application/gzip",
                "diff_text": diff_text or None,
                "patch_bundle_base64": patch_bundle_base64 or None,
                "changed_files": list(request.context.get("changed_files") or []),
                "summary": str(request.context.get("summary") or "").strip() or None,
                "intent": str(request.context.get("intent_summary") or "").strip() or None,
                "target_branch": str(request.context.get("target_branch") or "").strip() or None,
                "local_test_receipts": list(request.context.get("local_test_receipts") or []),
                "github_governance": (
                    (app_profile.get("profile") or {}).get("github_governance")
                    or {"write_principal": "zetherion", "agent_push_enabled": False}
                ),
                "status": "submitted",
            },
        )
        operation = await self._find_or_create_operation(
            owner_id=owner_id,
            app_id=app_id,
            repo_id=repo_id,
            refs={
                "publish_candidate_id": str(candidate["candidate_id"]),
                "git_sha": base_sha,
                **(
                    {
                        "branch": str(request.context.get("target_branch") or "").strip(),
                    }
                    if str(request.context.get("target_branch") or "").strip()
                    else {}
                ),
            },
            request_context={
                **dict(request.context or {}),
                "operation_kind": "publish_candidate",
            },
        )
        await self._storage.update_managed_operation(
            owner_id,
            operation_id=str(operation["operation_id"]),
            lifecycle_stage="publish_candidate_submitted",
            status="active",
            summary={
                "publish_candidate_id": str(candidate["candidate_id"]),
                "base_sha": base_sha,
                "target_branch": str(request.context.get("target_branch") or "").strip() or None,
                "operation_routes": _operation_routes(base_url=public_base_url, app_id=app_id),
            },
            metadata={
                **dict(operation.get("metadata") or {}),
                "source": "publish_candidate_submit",
            },
        )
        await self._storage.record_agent_audit_event(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
            service_kind="github",
            resource=repo_id,
            action="publish_candidate.submit",
            decision="allowed",
            audit={"candidate_id": candidate["candidate_id"], "base_sha": base_sha},
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Stored publish candidate `{candidate['candidate_id']}`.",
            data={
                "candidate": candidate,
                "operation": await self._storage.get_operation_hydrated(
                    owner_id,
                    str(operation["operation_id"]),
                ),
            },
        )

    async def _handle_publish_candidate_apply(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        candidate_id = str(request.context.get("candidate_id") or "").strip()
        if not candidate_id:
            return SkillResponse.error_response(request.id, "candidate_id is required")
        applied = await self._apply_publish_candidate(
            owner_id=owner_id,
            candidate_id=candidate_id,
            target_branch=str(request.context.get("target_branch") or "").strip() or None,
            principal_id=str(request.context.get("principal_id") or "").strip() or None,
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Applied publish candidate `{candidate_id}`.",
            data=applied,
        )

    async def _handle_managed_repo_enforce(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        github_repo = str(request.context.get("github_repo") or "").strip() or None
        if not app_id and not github_repo:
            return SkillResponse.error_response(request.id, "app_id or github_repo is required")
        if app_id:
            app = await self._storage.get_agent_app_profile(owner_id, app_id)
            if app is None:
                return SkillResponse.error_response(request.id, f"App `{app_id}` not found")
            profile = dict(app.get("profile") or {})
            github_repo = github_repo or (
                str((profile.get("github_repos") or [None])[0] or "").strip() or None
            )
            if github_repo is None:
                return SkillResponse.error_response(
                    request.id, f"App `{app_id}` does not declare a GitHub repository"
                )
        result = await self._enforce_managed_repo(
            owner_id=owner_id,
            app_id=app_id or _slugify_repo_id(github_repo or ""),
            github_repo=github_repo or "",
            default_branch=str(request.context.get("default_branch") or "").strip() or None,
            principal_id=str(request.context.get("principal_id") or "").strip() or None,
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Applied managed repo governance for `{github_repo}`.",
            data=result,
        )

    async def _handle_principal_upsert(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        principal_id = str(request.context.get("principal_id") or "").strip()
        if not principal_id:
            return SkillResponse.error_response(request.id, "principal_id is required")
        display_name = (
            str(request.context.get("display_name") or principal_id).strip() or principal_id
        )
        principal = await self._storage.upsert_agent_principal(
            owner_id,
            principal_id=principal_id,
            display_name=display_name,
            principal_type=str(request.context.get("principal_type") or "codex").strip() or "codex",
            allowed_scopes=[
                str(scope).strip()
                for scope in list(request.context.get("allowed_scopes") or ["cgs:agent"])
                if str(scope).strip()
            ],
            metadata=dict(request.context.get("metadata") or {}),
            active=bool(request.context.get("active", True)),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Upserted principal `{principal_id}`.",
            data={"principal": principal},
        )

    async def _handle_principal_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        principals = await self._storage.list_agent_principals(owner_id)
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(principals)} principals.",
            data={"principals": principals},
        )

    async def _handle_connector_upsert(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        connector_id = str(request.context.get("connector_id") or "").strip()
        service_kind = str(request.context.get("service_kind") or "").strip()
        auth_kind = str(request.context.get("auth_kind") or "").strip() or "token"
        if not connector_id or not service_kind:
            return SkillResponse.error_response(
                request.id, "connector_id and service_kind are required"
            )
        connector = await self._storage.upsert_external_service_connector(
            owner_id,
            connector_id=connector_id,
            service_kind=service_kind,
            display_name=str(request.context.get("display_name") or connector_id).strip()
            or connector_id,
            auth_kind=auth_kind,
            secret_value=(str(request.context.get("secret_value") or "").strip() or None),
            policy=dict(request.context.get("policy") or {}),
            metadata=dict(request.context.get("metadata") or {}),
            active=bool(request.context.get("active", True)),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Upserted connector `{connector_id}`.",
            data={"connector": connector},
        )

    async def _handle_connector_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        connectors = await self._storage.list_external_service_connectors(
            owner_id,
            service_kind=str(request.context.get("service_kind") or "").strip() or None,
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(connectors)} connectors.",
            data={"connectors": connectors},
        )

    async def _handle_connector_get(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        connector_id = str(request.context.get("connector_id") or "").strip()
        if not connector_id:
            return SkillResponse.error_response(request.id, "connector_id is required")
        connector = await self._storage.get_external_service_connector(owner_id, connector_id)
        if connector is None:
            return SkillResponse.error_response(request.id, f"Connector `{connector_id}` not found")
        capability = await self._storage.get_service_adapter_capability(
            owner_id,
            str(connector.get("service_kind") or "").strip(),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded connector `{connector_id}`.",
            data={
                "connector": connector,
                "capability": capability,
                "health": _connector_health_report(connector, capability),
            },
        )

    async def _handle_connector_health(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        connector_id = str(request.context.get("connector_id") or "").strip()
        if not connector_id:
            return SkillResponse.error_response(request.id, "connector_id is required")
        connector = await self._storage.get_external_service_connector(owner_id, connector_id)
        if connector is None:
            return SkillResponse.error_response(request.id, f"Connector `{connector_id}` not found")
        capability = await self._storage.get_service_adapter_capability(
            owner_id,
            str(connector.get("service_kind") or "").strip(),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded connector health for `{connector_id}`.",
            data={"health": _connector_health_report(connector, capability)},
        )

    async def _handle_connector_rotate(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        connector_id = str(request.context.get("connector_id") or "").strip()
        secret_value = str(request.context.get("secret_value") or "").strip()
        if not connector_id or not secret_value:
            return SkillResponse.error_response(
                request.id, "connector_id and secret_value are required"
            )
        existing = await self._storage.get_external_service_connector(owner_id, connector_id)
        if existing is None:
            return SkillResponse.error_response(request.id, f"Connector `{connector_id}` not found")
        connector = await self._storage.upsert_external_service_connector(
            owner_id,
            connector_id=connector_id,
            service_kind=str(existing["service_kind"]),
            display_name=str(existing["display_name"]),
            auth_kind=str(existing["auth_kind"]),
            secret_value=secret_value,
            policy=dict(existing.get("policy") or {}),
            metadata={
                **dict(existing.get("metadata") or {}),
                "rotated_by": _normalize_principal_id(request),
            },
            active=bool(existing.get("active", True)),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Rotated connector `{connector_id}`.",
            data={"connector": connector},
        )

    async def _handle_principal_grants_put(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        principal_id = str(request.context.get("principal_id") or "").strip()
        if not principal_id:
            return SkillResponse.error_response(request.id, "principal_id is required")
        grants = await self._storage.replace_external_access_grants(
            owner_id,
            principal_id=principal_id,
            grants=list(request.context.get("grants") or []),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Updated grants for `{principal_id}`.",
            data={"grants": grants},
        )

    async def _handle_app_upsert(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        if not app_id:
            return SkillResponse.error_response(request.id, "app_id is required")
        profile = dict(request.context.get("profile") or {})
        app = await self._storage.upsert_agent_app_profile(
            owner_id,
            app_id=app_id,
            display_name=str(request.context.get("display_name") or app_id).strip() or app_id,
            profile=profile,
            active=bool(request.context.get("active", True)),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Upserted app `{app_id}`.",
            data={"app": app},
        )

    async def _handle_app_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        apps = await self._storage.list_agent_app_profiles(owner_id)
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(apps)} app profiles.",
            data={"apps": apps},
        )

    async def _handle_knowledge_pack_upsert(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        app_id = str(request.context.get("app_id") or "").strip()
        version = str(request.context.get("version") or "").strip() or "current"
        pack = dict(request.context.get("pack") or {})
        if not app_id or not pack:
            return SkillResponse.error_response(request.id, "app_id and pack are required")
        stored = await self._storage.upsert_agent_knowledge_pack(
            owner_id,
            app_id=app_id,
            version=version,
            pack=pack,
            current=bool(request.context.get("current", True)),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Upserted knowledge pack `{app_id}@{version}`.",
            data={"knowledge_pack": stored},
        )

    async def _handle_audit_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        events = await self._storage.list_agent_audit_events(
            owner_id,
            principal_id=str(request.context.get("principal_id") or "").strip() or None,
            app_id=str(request.context.get("app_id") or "").strip() or None,
            limit=int(request.context.get("limit") or 100),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(events)} audit events.",
            data={"events": events},
        )

    async def _handle_secret_ref_upsert(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        secret_ref_id = str(request.context.get("secret_ref_id") or "").strip()
        purpose = str(request.context.get("purpose") or "").strip()
        if not secret_ref_id or not purpose:
            return SkillResponse.error_response(
                request.id, "secret_ref_id and purpose are required"
            )
        secret_ref = await self._storage.upsert_secret_ref(
            owner_id,
            secret_ref_id=secret_ref_id,
            purpose=purpose,
            secret_value=str(request.context.get("secret_value") or "").strip() or None,
            connector_id=str(request.context.get("connector_id") or "").strip() or None,
            metadata=dict(request.context.get("metadata") or {}),
            active=bool(request.context.get("active", True)),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Upserted secret ref `{secret_ref_id}`.",
            data={"secret_ref": secret_ref},
        )

    async def _handle_secret_ref_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        secret_refs = await self._storage.list_secret_refs(
            owner_id,
            active_only=bool(request.context.get("active_only", False)),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(secret_refs)} secret refs.",
            data={"secret_refs": secret_refs},
        )

    async def _handle_gap_list(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        gaps = await self._storage.list_agent_gap_events(
            owner_id,
            session_id=str(request.context.get("session_id") or "").strip() or None,
            principal_id=str(request.context.get("principal_id") or "").strip() or None,
            app_id=str(request.context.get("app_id") or "").strip() or None,
            repo_id=str(request.context.get("repo_id") or "").strip() or None,
            status=str(request.context.get("status") or "").strip() or None,
            blocker_only=bool(request.context.get("blocker_only", False)),
            unresolved_only=bool(request.context.get("unresolved_only", False)),
            limit=int(request.context.get("limit") or 100),
        )
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded {len(gaps)} gaps.",
            data={"gaps": gaps},
        )

    async def _handle_gap_get(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        gap_id = str(request.context.get("gap_id") or "").strip()
        if not gap_id:
            return SkillResponse.error_response(request.id, "gap_id is required")
        gap = await self._storage.get_agent_gap_event(owner_id, gap_id)
        if gap is None:
            return SkillResponse.error_response(request.id, f"Gap `{gap_id}` not found")
        return SkillResponse(
            request_id=request.id,
            message=f"Loaded gap `{gap_id}`.",
            data={"gap": gap},
        )

    async def _handle_gap_update(
        self,
        request: SkillRequest,
        owner_id: str,
        _public_base_url: str,
    ) -> SkillResponse:
        gap_id = str(request.context.get("gap_id") or "").strip()
        status = str(request.context.get("status") or "").strip()
        if not gap_id or not status:
            return SkillResponse.error_response(request.id, "gap_id and status are required")
        gap = await self._storage.update_agent_gap_event(
            owner_id,
            gap_id=gap_id,
            status=status,
            metadata=dict(request.context.get("metadata") or {}),
        )
        if gap is None:
            return SkillResponse.error_response(request.id, f"Gap `{gap_id}` not found")
        return SkillResponse(
            request_id=request.id,
            message=f"Updated gap `{gap_id}`.",
            data={"gap": gap},
        )

    async def _ensure_default_docs(self, owner_id: str, public_base_url: str) -> None:
        for doc in _DEFAULT_DOCS:
            manifest = _doc_manifest(doc, public_base_url)
            await self._storage.upsert_agent_docs_manifest(
                owner_id,
                slug=str(doc["slug"]),
                title=str(doc["title"]),
                manifest=manifest,
            )

    async def _ensure_default_apps(self, owner_id: str, public_base_url: str) -> None:
        docs_by_slug = {
            doc["slug"]: doc["manifest"]
            for doc in await self._storage.list_agent_docs_manifests(owner_id)
        }
        for repo in default_repo_profiles():
            app_id = str(repo["repo_id"])
            if await self._storage.get_agent_app_profile(owner_id, app_id) is None:
                await self._storage.upsert_agent_app_profile(
                    owner_id,
                    app_id=app_id,
                    display_name=str(repo["display_name"]),
                    profile=self._default_app_profile(repo, public_base_url),
                    active=True,
                )
            if await self._storage.get_agent_knowledge_pack(owner_id, app_id) is None:
                await self._storage.upsert_agent_knowledge_pack(
                    owner_id,
                    app_id=app_id,
                    version="current",
                    pack=self._default_knowledge_pack(repo, docs_by_slug),
                    current=True,
                )

    async def _ensure_default_service_capabilities(self, owner_id: str) -> None:
        for service_kind, manifest in _default_service_adapter_capabilities().items():
            await self._storage.upsert_service_adapter_capability(
                owner_id,
                service_kind=service_kind,
                manifest=manifest,
            )

    async def _record_interaction_outcome(
        self,
        request: SkillRequest,
        *,
        owner_id: str,
        public_base_url: str,
        success: bool,
        response: SkillResponse | None = None,
        error_message: str | None = None,
    ) -> None:
        principal_id = str(request.context.get("principal_id") or "").strip() or None
        session_id = _normalize_session_id(request.context.get("session_id"))
        app_id = str(request.context.get("app_id") or "").strip() or None
        repo_id = str(request.context.get("repo_id") or "").strip() or None
        if app_id and not repo_id:
            app_profile = await self._storage.get_agent_app_profile(owner_id, app_id)
            repo_id = (
                str(
                    ((app_profile or {}).get("profile") or {}).get("repo_ids", [None])[0] or ""
                ).strip()
                or None
            )
        request_payload = _redact_payload(dict(request.context or {}))
        route_path = _normalize_route_path(request.context.get("route_path"))
        interaction = await self._storage.create_agent_interaction(
            owner_id,
            session_id=session_id,
            principal_id=principal_id,
            app_id=app_id,
            repo_id=repo_id,
            route_path=route_path,
            intent=request.intent,
            request_text=_compact_text_payload(request_payload)
            or (request.intent if request.intent else None),
            request_payload=request_payload,
            normalized_intent={
                "intent": request.intent,
                "public_base_url": public_base_url or None,
            },
            related_run_id=(
                str(
                    (response.data or {}).get("run", {}).get("run_id")
                    or request.context.get("run_id")
                    or ""
                ).strip()
                or None
                if response is not None
                else (str(request.context.get("run_id") or "").strip() or None)
            ),
            related_candidate_id=(
                str((response.data or {}).get("candidate", {}).get("candidate_id") or "").strip()
                or None
                if response is not None
                else None
            ),
            related_service_request_id=(
                str((response.data or {}).get("request", {}).get("request_id") or "").strip()
                or None
                if response is not None
                else None
            ),
            audit_id=None,
        )
        action = await self._storage.create_agent_action(
            owner_id,
            interaction_id=interaction["interaction_id"],
            principal_id=principal_id,
            app_id=app_id,
            action=request.intent,
            status="succeeded" if success else "failed",
            payload={
                "route_path": route_path,
                "request_id": request.id,
            },
        )
        summary = (
            str(response.message or "").strip()
            if success and response is not None
            else (error_message or "Request failed")
        )
        await self._storage.create_agent_outcome(
            owner_id,
            interaction_id=interaction["interaction_id"],
            action_record_id=action["action_record_id"],
            status="success" if success else "error",
            summary=summary,
            payload=_redact_payload(
                dict((response.data or {}) if response is not None else {"error": error_message})
            ),
        )

    async def _record_gap(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        session_id: str | None,
        app_id: str | None,
        repo_id: str | None,
        gap_type: str,
        severity: str,
        blocker: bool,
        detected_from: str,
        required_capability: str | None,
        observed_request: dict[str, Any],
        suggested_fix: str | None,
        metadata: dict[str, Any],
        run_id: str | None,
    ) -> dict[str, Any]:
        return await self._storage.record_agent_gap_event(
            owner_id,
            dedupe_key=_stable_gap_key(
                [
                    gap_type,
                    app_id,
                    repo_id,
                    detected_from,
                    required_capability,
                    json.dumps(_redact_payload(observed_request), sort_keys=True),
                ]
            ),
            session_id=session_id,
            principal_id=principal_id,
            app_id=app_id,
            repo_id=repo_id,
            run_id=run_id,
            gap_type=gap_type,
            severity=severity,
            blocker=blocker,
            detected_from=detected_from,
            required_capability=required_capability,
            observed_request=_redact_payload(observed_request),
            suggested_fix=suggested_fix,
            metadata=metadata,
        )

    async def _record_missing_secret_ref_gaps(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        session_id: str | None,
        app_id: str,
        repo_id: str,
        required_secret_refs: list[str],
        reason: str,
    ) -> list[dict[str, Any]]:
        known_refs = {
            str(entry.get("secret_ref_id") or "")
            for entry in await self._storage.list_secret_refs(owner_id, active_only=True)
        }
        missing = [
            secret_ref for secret_ref in required_secret_refs if secret_ref not in known_refs
        ]
        gaps: list[dict[str, Any]] = []
        for secret_ref in missing:
            gaps.append(
                await self._record_gap(
                    owner_id=owner_id,
                    principal_id=principal_id,
                    session_id=session_id,
                    app_id=app_id,
                    repo_id=repo_id,
                    gap_type="missing_secret_ref",
                    severity="high",
                    blocker=True,
                    detected_from=reason,
                    required_capability=secret_ref,
                    observed_request={"secret_ref_id": secret_ref},
                    suggested_fix=f"Provision secret ref `{secret_ref}` before running this flow.",
                    metadata={},
                    run_id=None,
                )
            )
        return gaps

    async def _infer_owner_id(self, request_context: dict[str, Any]) -> str | None:
        app_id = str(request_context.get("app_id") or "").strip()
        if app_id:
            app_profile = await self._storage.find_agent_app_profile(app_id)
            owner_id = str((app_profile or {}).get("owner_id") or "").strip()
            if owner_id:
                return owner_id
        return None

    def _extract_operation_refs(self, request_context: dict[str, Any]) -> dict[str, str]:
        refs: dict[str, str] = {}
        candidates = {
            "publish_candidate_id": request_context.get("publish_candidate_id"),
            "git_sha": request_context.get("git_sha") or request_context.get("sha"),
            "branch": request_context.get("branch"),
            "pr_number": request_context.get("pr_number"),
            "github_run_id": request_context.get("github_run_id"),
            "vercel_deployment_id": request_context.get("vercel_deployment_id"),
            "clerk_instance_ref": request_context.get("clerk_instance_ref"),
            "issuer": request_context.get("issuer"),
            "jwks_url": request_context.get("jwks_url"),
            "stripe_event_id": request_context.get("stripe_event_id"),
            "clerk_event_id": request_context.get("clerk_event_id"),
            "github_delivery_id": request_context.get("github_delivery_id"),
            "vercel_event_id": request_context.get("vercel_event_id"),
            "customer_id": request_context.get("customer_id"),
            "subscription_id": request_context.get("subscription_id"),
            "run_id": request_context.get("run_id"),
        }
        for key, raw_value in candidates.items():
            value = str(raw_value or "").strip()
            if value:
                refs[key] = value
        base_sha = str(request_context.get("base_sha") or "").strip()
        if base_sha and "git_sha" not in refs:
            refs["git_sha"] = base_sha
        return refs

    def _normalize_event_payload(self, value: Any) -> dict[str, Any]:
        if isinstance(value, dict):
            return dict(value)
        if isinstance(value, str):
            with contextlib.suppress(json.JSONDecodeError):
                parsed = json.loads(value)
                if isinstance(parsed, dict):
                    return dict(parsed)
        return {}

    def _extract_operation_refs_from_event(
        self,
        service_kind: str,
        event_payload: dict[str, Any],
    ) -> dict[str, str]:
        refs: dict[str, str] = {}
        if service_kind == "github":
            workflow_run = dict(event_payload.get("workflow_run") or {})
            pull_request = dict(event_payload.get("pull_request") or {})
            check_run = dict(event_payload.get("check_run") or {})
            check_suite = dict(event_payload.get("check_suite") or {})
            repository = dict(event_payload.get("repository") or {})
            head = dict(pull_request.get("head") or {})
            if workflow_run.get("id"):
                refs["github_run_id"] = str(workflow_run.get("id"))
            if event_payload.get("delivery_id"):
                refs["github_delivery_id"] = str(event_payload.get("delivery_id"))
            if workflow_run.get("head_sha"):
                refs["git_sha"] = str(workflow_run.get("head_sha"))
            elif head.get("sha"):
                refs["git_sha"] = str(head.get("sha"))
            elif check_run.get("head_sha"):
                refs["git_sha"] = str(check_run.get("head_sha"))
            elif check_suite.get("head_sha"):
                refs["git_sha"] = str(check_suite.get("head_sha"))
            elif event_payload.get("after"):
                refs["git_sha"] = str(event_payload.get("after"))
            branch = (
                str(workflow_run.get("head_branch") or "").strip()
                or str(head.get("ref") or "").strip()
                or str(check_suite.get("head_branch") or "").strip()
                or str(event_payload.get("ref") or "").removeprefix("refs/heads/").strip()
            )
            if branch:
                refs["branch"] = branch
            pr_number = pull_request.get("number") or (
                (workflow_run.get("pull_requests") or [{}])[0] or {}
            ).get("number")
            if pr_number:
                refs["pr_number"] = str(pr_number)
            if repository.get("full_name"):
                refs.setdefault("repo_full_name", str(repository.get("full_name")))
        elif service_kind == "vercel":
            payload = dict(event_payload.get("payload") or {})
            deployment = dict(event_payload.get("deployment") or {})
            meta = dict(payload.get("meta") or deployment.get("meta") or {})
            deployment_id = (
                str(event_payload.get("deployment_id") or "").strip()
                or str(payload.get("id") or "").strip()
                or str(deployment.get("id") or "").strip()
            )
            if deployment_id:
                refs["vercel_deployment_id"] = deployment_id
            event_id = str(event_payload.get("id") or "").strip()
            if event_id:
                refs["vercel_event_id"] = event_id
            git_sha = (
                str(meta.get("githubCommitSha") or "").strip()
                or str(meta.get("githubCommitRefSha") or "").strip()
                or str(meta.get("gitCommitSha") or "").strip()
            )
            if git_sha:
                refs["git_sha"] = git_sha
            branch = (
                str(meta.get("githubCommitRef") or "").strip()
                or str(meta.get("gitCommitRef") or "").strip()
                or str(payload.get("target") or "").strip()
            )
            if branch:
                refs["branch"] = branch
        elif service_kind == "clerk":
            data = dict(event_payload.get("data") or {})
            if event_payload.get("id"):
                refs["clerk_event_id"] = str(event_payload.get("id"))
            if data.get("id"):
                refs["clerk_instance_ref"] = str(data.get("id"))
        elif service_kind == "stripe":
            data = dict(event_payload.get("data") or {})
            obj = dict(data.get("object") or {})
            if event_payload.get("id"):
                refs["stripe_event_id"] = str(event_payload.get("id"))
            if obj.get("customer"):
                refs["customer_id"] = str(obj.get("customer"))
            subscription_id = obj.get("subscription") or obj.get("id")
            if subscription_id and str(event_payload.get("type") or "").startswith(
                "customer.subscription."
            ):
                refs["subscription_id"] = str(subscription_id)
        return {key: value for key, value in refs.items() if str(value).strip()}

    async def _record_operation_event_ingest(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        app_id: str,
        repo_id: str,
        operation_id: str,
        service_kind: str,
        refs: dict[str, str],
        event_payload: dict[str, Any],
        request_context: dict[str, Any],
    ) -> None:
        delivery_id = (
            str(request_context.get("delivery_id") or "").strip()
            or refs.get("github_delivery_id")
            or refs.get("vercel_event_id")
            or refs.get("clerk_event_id")
            or refs.get("stripe_event_id")
            or ""
        )
        event_type = (
            str(request_context.get("event_type") or "").strip()
            or str(event_payload.get("type") or "").strip()
            or str(request_context.get("action") or "").strip()
            or f"{service_kind}.event"
        )
        for ref_kind, ref_value in refs.items():
            await self._storage.upsert_operation_ref(
                owner_id,
                operation_id=operation_id,
                service_kind=self._service_kind_for_operation_ref(ref_kind) or service_kind,
                ref_kind=ref_kind,
                ref_value=ref_value,
                metadata={
                    "source": request_context.get("source") or "provider_event",
                    "event_type": event_type,
                },
            )
        summary_payload = {
            "event_type": event_type,
            "delivery_id": delivery_id or None,
            "source": request_context.get("source") or "provider_event",
            "refs": refs,
        }
        summary_evidence = await self._storage.record_operation_evidence(
            owner_id,
            operation_id=operation_id,
            service_kind=service_kind,
            evidence_type="events",
            title=f"{service_kind} event {event_type}",
            payload={
                "summary": summary_payload,
                "event": _redact_payload(event_payload),
            },
            log_text=self._render_event_log_lines(service_kind, event_type, event_payload),
            metadata={
                "delivery_id": delivery_id or None,
                "event_type": event_type,
            },
            dedupe_key=":".join(
                part
                for part in [
                    service_kind,
                    "event",
                    delivery_id or None,
                    event_type or None,
                    refs.get("github_run_id"),
                    refs.get("vercel_deployment_id"),
                    refs.get("stripe_event_id"),
                ]
                if part
            ),
        )
        incident = self._incident_from_provider_event(
            service_kind=service_kind,
            event_type=event_type,
            event_payload=event_payload,
        )
        if incident is not None:
            await self._storage.record_operation_incident(
                owner_id,
                operation_id=operation_id,
                service_kind=service_kind,
                incident_type=str(incident["incident_type"]),
                severity=str(incident["severity"]),
                blocking=bool(incident["blocking"]),
                root_cause_summary=str(incident["root_cause_summary"]),
                recommended_fix=str(incident["recommended_fix"]),
                evidence_refs=[str(summary_evidence["evidence_id"])],
                metadata=dict(incident.get("metadata") or {}),
            )
        await self._storage.record_agent_audit_event(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
            service_kind=service_kind,
            resource=str(operation_id),
            action="operation_event_ingest",
            decision="accepted",
            audit={
                "event_type": event_type,
                "delivery_id": delivery_id or None,
                "repo_id": repo_id,
                "refs": refs,
            },
        )

    def _render_event_log_lines(
        self,
        service_kind: str,
        event_type: str,
        event_payload: dict[str, Any],
    ) -> str:
        lines = [f"{service_kind}:{event_type}"]
        if service_kind == "github":
            workflow_run = dict(event_payload.get("workflow_run") or {})
            check_run = dict(event_payload.get("check_run") or {})
            pull_request = dict(event_payload.get("pull_request") or {})
            lines.extend(
                [
                    str(workflow_run.get("name") or "").strip(),
                    str(workflow_run.get("conclusion") or workflow_run.get("status") or "").strip(),
                    str(check_run.get("name") or "").strip(),
                    str(check_run.get("conclusion") or check_run.get("status") or "").strip(),
                    str(pull_request.get("title") or "").strip(),
                ]
            )
        elif service_kind == "vercel":
            payload = dict(event_payload.get("payload") or {})
            lines.extend(
                [
                    str(payload.get("name") or event_payload.get("name") or "").strip(),
                    str(payload.get("target") or "").strip(),
                    str(payload.get("readyState") or payload.get("state") or "").strip(),
                ]
            )
        elif service_kind == "clerk":
            data = dict(event_payload.get("data") or {})
            lines.extend(
                [
                    str(data.get("id") or "").strip(),
                    str(
                        (data.get("email_addresses") or [{}])[0].get("email_address") or ""
                    ).strip(),
                ]
            )
        elif service_kind == "stripe":
            data = dict(event_payload.get("data") or {})
            obj = dict(data.get("object") or {})
            lines.extend(
                [
                    str(obj.get("id") or "").strip(),
                    str(obj.get("customer") or "").strip(),
                    str(obj.get("status") or "").strip(),
                ]
            )
        return "\n".join(line for line in lines if line)

    def _incident_from_provider_event(
        self,
        *,
        service_kind: str,
        event_type: str,
        event_payload: dict[str, Any],
    ) -> dict[str, Any] | None:
        if service_kind == "github":
            workflow_run = dict(event_payload.get("workflow_run") or {})
            conclusion = str(workflow_run.get("conclusion") or "").strip().lower()
            if conclusion in {"failure", "cancelled", "timed_out", "action_required"}:
                return {
                    "incident_type": "workflow_failed",
                    "severity": "high",
                    "blocking": True,
                    "root_cause_summary": (
                        f"GitHub workflow run failed with conclusion `{conclusion}`."
                    ),
                    "recommended_fix": (
                        "Inspect the failing GitHub run, review the attached logs, "
                        "and rerun after applying the fix."
                    ),
                    "metadata": {"workflow_run": workflow_run},
                }
        if service_kind == "vercel":
            payload = dict(event_payload.get("payload") or {})
            state = (
                str(
                    payload.get("readyState")
                    or payload.get("state")
                    or event_payload.get("state")
                    or ""
                )
                .strip()
                .lower()
            )
            if state in {"error", "failed", "canceled"}:
                return {
                    "incident_type": "deployment_failed",
                    "severity": "high",
                    "blocking": True,
                    "root_cause_summary": (f"Vercel deployment failed with state `{state}`."),
                    "recommended_fix": (
                        "Inspect the deployment logs and retry the build after "
                        "fixing the failing step."
                    ),
                    "metadata": {"payload": payload},
                }
        if service_kind == "stripe":
            pending = int(event_payload.get("pending_webhooks") or 0)
            if pending > 0:
                return {
                    "incident_type": "webhook_pending",
                    "severity": "medium",
                    "blocking": False,
                    "root_cause_summary": (
                        "Stripe reported pending webhook deliveries for the event."
                    ),
                    "recommended_fix": (
                        "Inspect the Stripe webhook destination and replay the "
                        "event after fixing the receiver."
                    ),
                    "metadata": {"pending_webhooks": pending},
                }
        if service_kind == "clerk" and event_type.endswith(".failed"):
            return {
                "incident_type": "auth_failed",
                "severity": "high",
                "blocking": True,
                "root_cause_summary": "Clerk emitted a failed auth or user management event.",
                "recommended_fix": (
                    "Inspect the linked application auth diagnostics and Clerk "
                    "configuration for the failing event."
                ),
                "metadata": {"event_type": event_type},
            }
        return None

    def _service_kind_for_operation_ref(self, ref_kind: str) -> str | None:
        if ref_kind in {"publish_candidate_id", "git_sha", "branch", "pr_number", "github_run_id"}:
            return "github"
        if ref_kind in {"github_delivery_id"}:
            return "github"
        if ref_kind in {"vercel_deployment_id"}:
            return "vercel"
        if ref_kind in {"vercel_event_id"}:
            return "vercel"
        if ref_kind in {"clerk_instance_ref", "issuer", "jwks_url", "clerk_event_id"}:
            return "clerk"
        if ref_kind in {"stripe_event_id", "customer_id", "subscription_id"}:
            return "stripe"
        return None

    async def _find_or_create_operation(
        self,
        *,
        owner_id: str,
        app_id: str,
        repo_id: str,
        refs: dict[str, str],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        for ref_kind in (
            "publish_candidate_id",
            "github_run_id",
            "github_delivery_id",
            "vercel_deployment_id",
            "vercel_event_id",
            "git_sha",
            "pr_number",
            "branch",
            "clerk_event_id",
            "stripe_event_id",
            "customer_id",
            "subscription_id",
        ):
            ref_value = refs.get(ref_kind)
            if not ref_value:
                continue
            existing = await self._storage.find_managed_operation_by_ref(
                owner_id,
                ref_kind=ref_kind,
                ref_value=ref_value,
                app_id=app_id,
            )
            if existing is not None:
                return existing
        correlation_key = _stable_gap_key(
            [app_id, *[f"{key}:{value}" for key, value in sorted(refs.items())]]
        )
        operation_kind = str(request_context.get("operation_kind") or "").strip() or (
            "publish_candidate" if refs.get("publish_candidate_id") else "service_evidence"
        )
        operation = await self._storage.create_managed_operation(
            owner_id,
            app_id=app_id,
            repo_id=repo_id,
            operation_kind=operation_kind,
            lifecycle_stage="resolving",
            status="active",
            correlation_key=correlation_key,
            summary={"requested_refs": refs},
            metadata={"source": "agent_operation_resolve"},
        )
        for ref_kind, ref_value in refs.items():
            await self._storage.upsert_operation_ref(
                owner_id,
                operation_id=str(operation["operation_id"]),
                service_kind=self._service_kind_for_operation_ref(ref_kind),
                ref_kind=ref_kind,
                ref_value=ref_value,
                metadata={"source": "request"},
            )
        return operation

    async def _refresh_operation(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        app_id: str,
        app_profile: dict[str, Any],
        operation: dict[str, Any],
        request_context: dict[str, Any],
        public_base_url: str,
    ) -> None:
        operation_refs = await self._storage.list_operation_refs(
            owner_id,
            str(operation["operation_id"]),
        )
        refs = {
            str(entry.get("ref_kind") or ""): str(entry.get("ref_value") or "")
            for entry in operation_refs
            if str(entry.get("ref_kind") or "").strip()
            and str(entry.get("ref_value") or "").strip()
        }
        requested_services = {
            str(value).strip().lower()
            for value in [
                request_context.get("service_kind"),
                *(
                    (request_context.get("service_kinds") or [])
                    if isinstance(request_context.get("service_kinds"), list)
                    else []
                ),
            ]
            if str(value or "").strip()
        }
        services_to_refresh: set[str] = {
            inferred_service_kind
            for inferred_service_kind in (
                self._service_kind_for_operation_ref(ref_kind) for ref_kind in refs
            )
            if inferred_service_kind is not None
        }
        services_to_refresh.update(requested_services)
        declared_services = {
            str(key).strip().lower()
            for key in dict((app_profile.get("profile") or {}).get("service_connector_map") or {})
            if str(key).strip()
        }
        services_to_refresh = {
            service_kind
            for service_kind in services_to_refresh
            if service_kind
            and (
                service_kind in declared_services
                or service_kind in {"github", "vercel", "clerk", "stripe"}
            )
        }
        runtime_result: dict[str, Any] | None = None
        if refs.get("run_id"):
            runtime_result = await self._collect_ci_runtime_operation_evidence(
                owner_id=owner_id,
                principal_id=principal_id,
                app_id=app_id,
                app_profile=app_profile,
                operation_id=str(operation["operation_id"]),
                run_id=str(refs["run_id"]),
                request_context=request_context,
            )
        if not services_to_refresh and not runtime_result:
            await self._record_gap(
                owner_id=owner_id,
                principal_id=principal_id,
                session_id=_normalize_session_id(request_context.get("session_id")),
                app_id=app_id,
                repo_id=str(operation.get("repo_id") or ""),
                gap_type="workspace_contract_gap",
                severity="high",
                blocker=True,
                detected_from="operation_resolve",
                required_capability="operation_correlation",
                observed_request={"refs": refs},
                suggested_fix=(
                    "Provide a supported operation reference such as git_sha, "
                    "github_run_id, or vercel_deployment_id."
                ),
                metadata={},
                run_id=str(request_context.get("run_id") or "").strip() or None,
            )
            return
        summaries: dict[str, Any] = {}
        statuses: list[str] = []
        stages: list[str] = []
        if runtime_result:
            summaries["ci_runtime"] = runtime_result.get("summary")
            statuses.append(str(runtime_result.get("status") or "active"))
            stages.append(str(runtime_result.get("lifecycle_stage") or "ci_runtime"))
        for service_kind in sorted(services_to_refresh):
            capability = await self._storage.get_service_adapter_capability(owner_id, service_kind)
            if capability is None:
                await self._record_gap(
                    owner_id=owner_id,
                    principal_id=principal_id,
                    session_id=_normalize_session_id(request_context.get("session_id")),
                    app_id=app_id,
                    repo_id=str(operation.get("repo_id") or ""),
                    gap_type="missing_connector",
                    severity="high",
                    blocker=True,
                    detected_from="operation_resolve",
                    required_capability=f"{service_kind}:adapter",
                    observed_request={"service_kind": service_kind},
                    suggested_fix=(
                        f"Register a `{service_kind}` service adapter " "capability manifest."
                    ),
                    metadata={},
                    run_id=str(request_context.get("run_id") or "").strip() or None,
                )
                continue
            adapter_result = await self._collect_service_operation_evidence(
                owner_id=owner_id,
                principal_id=principal_id,
                app_id=app_id,
                app_profile=app_profile,
                operation_id=str(operation["operation_id"]),
                service_kind=service_kind,
                refs=refs,
                request_context=request_context,
                public_base_url=public_base_url,
            )
            if not adapter_result:
                continue
            summaries[service_kind] = adapter_result.get("summary")
            statuses.append(str(adapter_result.get("status") or "active"))
            stages.append(str(adapter_result.get("lifecycle_stage") or service_kind))
        final_status = "active"
        if any(status in {"failed", "error"} for status in statuses):
            final_status = "failed"
        elif statuses and all(status in {"succeeded", "resolved"} for status in statuses):
            final_status = "succeeded"
        lifecycle_stage = stages[-1] if stages else "resolved"
        await self._storage.update_managed_operation(
            owner_id,
            operation_id=str(operation["operation_id"]),
            lifecycle_stage=lifecycle_stage,
            status=final_status,
            summary={
                **dict(operation.get("summary") or {}),
                "resolved_refs": refs,
                "services": summaries,
            },
            metadata={
                **dict(operation.get("metadata") or {}),
                "public_base_url": public_base_url or None,
            },
        )

    async def _collect_ci_runtime_operation_evidence(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        app_id: str,
        app_profile: dict[str, Any],
        operation_id: str,
        run_id: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        run = await self._storage.get_run(owner_id, run_id)
        if run is None:
            await self._record_gap(
                owner_id=owner_id,
                principal_id=principal_id,
                session_id=_normalize_session_id(request_context.get("session_id")),
                app_id=app_id,
                repo_id=str(
                    ((app_profile.get("profile") or {}).get("repo_ids") or [None])[0] or ""
                ),
                gap_type="missing_provider_correlation",
                severity="medium",
                blocker=False,
                detected_from="operation_resolve",
                required_capability="ci_runtime:run",
                observed_request={"run_id": run_id},
                suggested_fix=(
                    "Attach a valid CI run id so runtime and container evidence "
                    "can be correlated."
                ),
                metadata={},
                run_id=run_id,
            )
            return {}
        events = await self._storage.get_run_events(owner_id, run_id, limit=100)
        logs = await self._storage.get_run_log_chunks(owner_id, run_id, limit=200)
        debug_bundle = await self._storage.get_run_debug_bundle(owner_id, run_id)
        coverage_summary = next(
            (
                dict((dict(shard.get("result") or {})).get("coverage_summary") or {})
                for shard in list(run.get("shards") or [])
                if isinstance(dict(shard.get("result") or {}).get("coverage_summary"), dict)
            ),
            {},
        )
        coverage_gaps = next(
            (
                dict((dict(shard.get("result") or {})).get("coverage_gaps") or {})
                for shard in list(run.get("shards") or [])
                if isinstance(dict(shard.get("result") or {}).get("coverage_gaps"), dict)
            ),
            {},
        )
        summary = {
            "run": run,
            "event_count": len(events),
            "log_count": len(logs),
            "coverage_summary": coverage_summary,
            "debug_bundle": {
                "bundle_id": str(debug_bundle.get("bundle_id") or "") if debug_bundle else None,
                "shard_id": str(debug_bundle.get("shard_id") or "") if debug_bundle else None,
                "cleanup_receipt": (
                    dict((debug_bundle.get("bundle") or {}).get("cleanup_receipt") or {})
                    if debug_bundle
                    else {}
                ),
                "container_receipts": (
                    list((debug_bundle.get("bundle") or {}).get("container_receipts") or [])
                    if debug_bundle
                    else []
                ),
                "compose_state": (
                    dict((debug_bundle.get("bundle") or {}).get("compose_state") or {})
                    if debug_bundle
                    else {}
                ),
            },
        }
        summary_evidence = await self._storage.record_operation_evidence(
            owner_id,
            operation_id=operation_id,
            service_kind="ci_runtime",
            evidence_type="summary",
            title="CI runtime summary",
            payload=summary,
            metadata={"run_id": run_id},
        )
        if events:
            await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="ci_runtime",
                evidence_type="events",
                title="CI lifecycle events",
                payload={"events": events},
                metadata={"run_id": run_id},
            )
        if logs:
            await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="ci_runtime",
                evidence_type="logs",
                title="CI and container logs",
                payload={"entries": logs[:50], "stream": "ci_runtime"},
                log_text="\n".join(
                    str(entry.get("message") or "")
                    for entry in logs[:200]
                    if str(entry.get("message") or "").strip()
                ),
                metadata={"run_id": run_id},
            )
        if debug_bundle:
            await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="ci_runtime",
                evidence_type="artifacts",
                title="CI runtime debug bundle",
                payload=dict(debug_bundle.get("bundle") or {}),
                metadata={"run_id": run_id, "bundle_id": str(debug_bundle.get("bundle_id") or "")},
            )
        if coverage_summary:
            await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="ci_runtime",
                evidence_type="coverage_summary",
                title="CI coverage summary",
                payload=coverage_summary,
                metadata={"run_id": run_id},
                state="failed" if not bool(coverage_summary.get("passed", True)) else "ready",
            )
        if coverage_gaps:
            await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="ci_runtime",
                evidence_type="coverage_gaps",
                title="CI coverage gaps",
                payload=coverage_gaps,
                metadata={"run_id": run_id},
                state=(
                    "failed"
                    if coverage_summary and not bool(coverage_summary.get("passed", True))
                    else "ready"
                ),
            )
        if not logs and not debug_bundle:
            await self._record_gap(
                owner_id=owner_id,
                principal_id=principal_id,
                session_id=_normalize_session_id(request_context.get("session_id")),
                app_id=app_id,
                repo_id=str(run.get("repo_id") or ""),
                gap_type="missing_tooling",
                severity="medium",
                blocker=False,
                detected_from="operation_logs",
                required_capability="ci_runtime:container_logs",
                observed_request={"run_id": run_id},
                suggested_fix=(
                    "Ensure CI shards persist stdout/stderr, Docker container "
                    "logs, and debug bundles for failed runs."
                ),
                metadata={},
                run_id=run_id,
            )
        diagnostic_summary, diagnostic_findings = build_run_diagnostics(
            run=run,
            logs=logs,
            debug_bundle=debug_bundle,
        )
        if diagnostic_findings:
            diagnostic_summary_evidence = await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="ci_runtime",
                evidence_type="diagnostic_summary",
                title="CI runtime diagnosis",
                payload=diagnostic_summary,
                metadata={"run_id": run_id},
                state="failed" if bool(diagnostic_summary.get("blocking")) else "ready",
            )
            await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="ci_runtime",
                evidence_type="diagnostic_findings",
                title="CI runtime diagnostic findings",
                payload={"findings": diagnostic_findings},
                metadata={"run_id": run_id},
                state="failed" if bool(diagnostic_summary.get("blocking")) else "ready",
            )
            diagnostic_artifacts = list(diagnostic_summary.get("diagnostic_artifacts") or [])
            if diagnostic_artifacts:
                await self._storage.record_operation_evidence(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="ci_runtime",
                    evidence_type="diagnostic_artifacts",
                    title="CI runtime diagnostic artifacts",
                    payload={"artifacts": diagnostic_artifacts},
                    metadata={"run_id": run_id},
                    state="failed" if bool(diagnostic_summary.get("blocking")) else "ready",
                )
            for finding in diagnostic_findings:
                await self._storage.record_operation_incident(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="ci_runtime",
                    incident_type=str(
                        finding.get("code") or finding.get("type") or "ci_runtime_failed"
                    ),
                    severity=str(finding.get("severity") or "high"),
                    blocking=bool(finding.get("blocking", False)),
                    root_cause_summary=str(
                        finding.get("root_cause_summary")
                        or finding.get("summary")
                        or "CI runtime failure"
                    ),
                    recommended_fix=str(finding.get("recommended_fix") or "").strip() or None,
                    evidence_refs=[str(diagnostic_summary_evidence["evidence_id"])],
                    metadata={
                        "run_id": run_id,
                        "shard_id": finding.get("shard_id"),
                        "lane_id": finding.get("lane_id"),
                        "diagnostic": True,
                    },
                )
        run_status = str(run.get("status") or "").strip().lower()
        status = "active"
        if run_status in {"failed", "promotion_blocked", "cancelled"}:
            status = "failed"
            if not diagnostic_findings:
                first_message = next(
                    (
                        str(entry.get("message") or "").strip()
                        for entry in logs
                        if str(entry.get("message") or "").strip()
                    ),
                    "",
                )
                await self._storage.record_operation_incident(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="ci_runtime",
                    incident_type="ci_runtime_failed",
                    severity="high",
                    blocking=True,
                    root_cause_summary=first_message
                    or f"CI runtime failed with status `{run_status}`.",
                    recommended_fix=(
                        "Inspect the CI/container logs and debug bundle, then rerun "
                        "the failing shard after applying the fix."
                    ),
                    evidence_refs=[str(summary_evidence["evidence_id"])],
                    metadata={"run_id": run_id, "status": run_status},
                )
        elif run_status in {"ready_to_merge", "merged", "succeeded", "completed"}:
            status = "succeeded"
        return {
            "summary": summary,
            "status": status,
            "lifecycle_stage": "ci_runtime",
            "diagnostic_summary": diagnostic_summary,
            "diagnostic_findings": diagnostic_findings,
        }

    async def _collect_service_operation_evidence(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        app_id: str,
        app_profile: dict[str, Any],
        operation_id: str,
        service_kind: str,
        refs: dict[str, str],
        request_context: dict[str, Any],
        public_base_url: str,
    ) -> dict[str, Any]:
        if service_kind == "github":
            return await self._collect_github_operation_evidence(
                owner_id=owner_id,
                principal_id=principal_id,
                app_id=app_id,
                app_profile=app_profile,
                operation_id=operation_id,
                refs=refs,
                request_context=request_context,
            )
        if service_kind == "vercel":
            return await self._collect_vercel_operation_evidence(
                owner_id=owner_id,
                principal_id=principal_id,
                app_id=app_id,
                app_profile=app_profile,
                operation_id=operation_id,
                refs=refs,
                request_context=request_context,
            )
        if service_kind == "clerk":
            return await self._collect_clerk_operation_evidence(
                owner_id=owner_id,
                principal_id=principal_id,
                app_id=app_id,
                app_profile=app_profile,
                operation_id=operation_id,
                refs=refs,
                request_context=request_context,
            )
        if service_kind == "stripe":
            return await self._collect_stripe_operation_evidence(
                owner_id=owner_id,
                principal_id=principal_id,
                app_id=app_id,
                app_profile=app_profile,
                operation_id=operation_id,
                refs=refs,
                request_context=request_context,
            )
        await self._record_gap(
            owner_id=owner_id,
            principal_id=principal_id,
            session_id=_normalize_session_id(request_context.get("session_id")),
            app_id=app_id,
            repo_id=str(((app_profile.get("profile") or {}).get("repo_ids") or [None])[0] or ""),
            gap_type="unsupported_service_action",
            severity="medium",
            blocker=False,
            detected_from="operation_resolve",
            required_capability=f"{service_kind}:adapter",
            observed_request={"service_kind": service_kind},
            suggested_fix=f"Implement the `{service_kind}` operation adapter.",
            metadata={},
            run_id=str(request_context.get("run_id") or "").strip() or None,
        )
        return {}

    async def _collect_github_operation_evidence(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        app_id: str,
        app_profile: dict[str, Any],
        operation_id: str,
        refs: dict[str, str],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        connector = self._service_connector_for(app_profile, service_kind="github")
        github_repo = str(
            ((app_profile.get("profile") or {}).get("github_repos") or [None])[0] or ""
        ).strip()
        if not github_repo:
            return {}
        repo_owner, repo_name = _split_github_repo(github_repo)
        client = GitHubClient(
            token=str(
                (
                    await self._require_github_connector(
                        owner_id,
                        str(connector.get("connector_id") or _GITHUB_CONNECTOR_ID),
                    )
                )["secret_value"]
            )
        )
        try:
            repository = await client.get_repository(repo_owner, repo_name)
            branch = refs.get("branch") or str(repository.default_branch or "").strip() or None
            git_sha = refs.get("git_sha")
            pr_number = int(refs["pr_number"]) if refs.get("pr_number", "").isdigit() else None
            run_id = int(refs["github_run_id"]) if refs.get("github_run_id", "").isdigit() else None
            pr: dict[str, Any] | None = None
            if pr_number is not None:
                pr = (await client.get_pull_request(repo_owner, repo_name, pr_number)).to_dict()
                head_ref = ((pr.get("head") or {}) if isinstance(pr, dict) else {}).get("ref")
                if head_ref and not branch:
                    branch = str(head_ref)
            workflow_run_payload: dict[str, Any] | None = None
            workflow_runs = await client.list_workflow_runs(
                repo_owner,
                repo_name,
                branch=branch,
                per_page=20,
                page=1,
            )
            if run_id is not None:
                workflow_run_payload = await client.get_workflow_run(repo_owner, repo_name, run_id)
            elif git_sha:
                matched = next(
                    (
                        run
                        for run in workflow_runs
                        if str(run.head_sha or "").startswith(git_sha[:7])
                    ),
                    None,
                )
                if matched is not None:
                    workflow_run_payload = await client.get_workflow_run(
                        repo_owner,
                        repo_name,
                        int(matched.id),
                    )
            elif workflow_runs:
                workflow_run_payload = await client.get_workflow_run(
                    repo_owner,
                    repo_name,
                    int(workflow_runs[0].id),
                )
            jobs: list[dict[str, Any]] = []
            logs_payload: dict[str, Any] = {}
            artifacts: list[dict[str, Any]] = []
            if workflow_run_payload is not None:
                run_id = int(workflow_run_payload.get("id") or 0)
                jobs = await client.list_workflow_jobs(repo_owner, repo_name, run_id, per_page=100)
                artifacts = await client.list_workflow_run_artifacts(
                    repo_owner,
                    repo_name,
                    run_id,
                    per_page=100,
                )
                try:
                    logs_payload = await client.download_workflow_run_logs(
                        repo_owner,
                        repo_name,
                        run_id,
                    )
                except GitHubAPIError as exc:
                    await self._record_gap(
                        owner_id=owner_id,
                        principal_id=principal_id,
                        session_id=_normalize_session_id(request_context.get("session_id")),
                        app_id=app_id,
                        repo_id=str(
                            ((app_profile.get("profile") or {}).get("repo_ids") or [None])[0] or ""
                        ),
                        gap_type="missing_tooling",
                        severity="medium",
                        blocker=False,
                        detected_from="operation_logs",
                        required_capability="github:logs",
                        observed_request={"github_run_id": run_id},
                        suggested_fix=(
                            "Grant GitHub log download access or extend the "
                            "adapter to decode workflow logs."
                        ),
                        metadata={"error": str(exc)},
                        run_id=str(request_context.get("run_id") or "").strip() or None,
                    )
            if workflow_run_payload is not None:
                await self._storage.upsert_operation_ref(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="github",
                    ref_kind="github_run_id",
                    ref_value=str(workflow_run_payload.get("id") or ""),
                    metadata={"source": "github"},
                )
            if branch:
                await self._storage.upsert_operation_ref(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="github",
                    ref_kind="branch",
                    ref_value=branch,
                    metadata={"source": "github"},
                )
            if git_sha:
                await self._storage.upsert_operation_ref(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="github",
                    ref_kind="git_sha",
                    ref_value=git_sha,
                    metadata={"source": "github"},
                )
            if pr_number is not None:
                await self._storage.upsert_operation_ref(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="github",
                    ref_kind="pr_number",
                    ref_value=str(pr_number),
                    metadata={"source": "github"},
                )
            summary = {
                "repository": repository.to_dict(),
                "pull_request": pr,
                "workflow_run": workflow_run_payload,
                "job_count": len(jobs),
                "artifact_count": len(artifacts),
            }
            summary_evidence = await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="github",
                evidence_type="summary",
                title="GitHub workflow summary",
                payload=summary,
                metadata={"service_kind": "github"},
            )
            await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="github",
                evidence_type="events",
                title="GitHub workflow jobs",
                payload={"jobs": jobs},
                metadata={"service_kind": "github"},
            )
            if artifacts:
                await self._storage.record_operation_evidence(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="github",
                    evidence_type="artifacts",
                    title="GitHub workflow artifacts",
                    payload={"artifacts": artifacts},
                    metadata={"service_kind": "github"},
                )
            if logs_payload.get("combined_text"):
                await self._storage.record_operation_evidence(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="github",
                    evidence_type="logs",
                    title="GitHub workflow logs",
                    payload={"entries": logs_payload.get("entries", []), "stream": "github"},
                    log_text=str(logs_payload.get("combined_text") or ""),
                    metadata={
                        "service_kind": "github",
                        "truncated": bool(logs_payload.get("truncated")),
                    },
                )
            status = "active"
            lifecycle_stage = "github"
            if workflow_run_payload is not None:
                conclusion = str(workflow_run_payload.get("conclusion") or "").strip().lower()
                run_status = str(workflow_run_payload.get("status") or "").strip().lower()
                if conclusion in {"failure", "cancelled", "timed_out", "action_required"}:
                    status = "failed"
                    failed_jobs = [
                        job
                        for job in jobs
                        if str(job.get("conclusion") or "").strip().lower()
                        in {"failure", "cancelled", "timed_out"}
                    ]
                    root_cause = (
                        "GitHub Actions run "
                        f"{workflow_run_payload.get('name') or workflow_run_payload.get('id')} "
                        "failed."
                    )
                    if failed_jobs:
                        first_job = failed_jobs[0]
                        root_cause += (
                            f" First failing job: {first_job.get('name') or first_job.get('id')}."
                        )
                    await self._storage.record_operation_incident(
                        owner_id,
                        operation_id=operation_id,
                        service_kind="github",
                        incident_type="workflow_failed",
                        severity="high",
                        blocking=True,
                        root_cause_summary=root_cause,
                        recommended_fix=(
                            "Inspect the failing GitHub job and rerun after " "applying the fix."
                        ),
                        evidence_refs=[str(summary_evidence["evidence_id"])],
                        metadata={"workflow_run": workflow_run_payload, "jobs": failed_jobs[:5]},
                    )
                elif run_status == "completed":
                    status = "succeeded"
            return {
                "summary": summary,
                "status": status,
                "lifecycle_stage": lifecycle_stage,
            }
        finally:
            await client.close()

    async def _collect_vercel_operation_evidence(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        app_id: str,
        app_profile: dict[str, Any],
        operation_id: str,
        refs: dict[str, str],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        connector_ref = self._service_connector_for(app_profile, service_kind="vercel")
        connector = await self._require_connector(
            owner_id,
            connector_id=str(connector_ref.get("connector_id") or _VERCEL_CONNECTOR_ID),
            service_kind="vercel",
        )
        metadata = dict(connector.get("metadata") or {})
        team_id = (
            str(request_context.get("team_id") or metadata.get("team_id") or "").strip() or None
        )
        project_ref = (
            str(request_context.get("project_ref") or "").strip()
            or str(metadata.get("project_name") or "").strip()
            or str(metadata.get("project_id") or "").strip()
            or str(app_id)
        )
        client = VercelClient(token=str(connector["secret_value"]))
        try:
            project = await client.get_project(project_ref, team_id=team_id)
            deployment_id = refs.get("vercel_deployment_id")
            deployment: dict[str, Any] | None = None
            deployments = await client.list_deployments(
                project_id=str(project.get("id") or "").strip() or None,
                project_name=str(project.get("name") or project_ref),
                team_id=team_id,
                limit=20,
            )
            if deployment_id:
                deployment = await client.get_deployment(deployment_id, team_id=team_id)
            else:
                git_sha = refs.get("git_sha")
                branch = refs.get("branch")
                for candidate in deployments:
                    meta = dict(candidate.get("meta") or {})
                    candidate_sha = str(
                        meta.get("githubCommitSha")
                        or meta.get("githubCommitRefSha")
                        or meta.get("gitCommitSha")
                        or ""
                    ).strip()
                    candidate_branch = str(
                        meta.get("githubCommitRef")
                        or meta.get("gitCommitRef")
                        or candidate.get("target")
                        or ""
                    ).strip()
                    if git_sha and candidate_sha and candidate_sha.startswith(git_sha[:7]):
                        deployment = candidate
                        break
                    if branch and candidate_branch == branch:
                        deployment = candidate
                        break
                if deployment is None and deployments:
                    deployment = deployments[0]
            domains = await client.list_domains(
                str(project.get("name") or project_ref), team_id=team_id
            )
            events: list[dict[str, Any]] = []
            log_text = ""
            if deployment and deployment.get("uid"):
                try:
                    events = await client.get_deployment_events(
                        str(deployment.get("uid") or ""),
                        team_id=team_id,
                        limit=100,
                    )
                    lines: list[str] = []
                    for event in events:
                        rendered = (
                            str(event.get("text") or "").strip()
                            or str((event.get("payload") or {}).get("text") or "").strip()
                            or str(event.get("name") or "").strip()
                        )
                        if rendered:
                            lines.append(rendered)
                    log_text = "\n".join(lines[:200])
                except VercelAPIError as exc:
                    await self._record_gap(
                        owner_id=owner_id,
                        principal_id=principal_id,
                        session_id=_normalize_session_id(request_context.get("session_id")),
                        app_id=app_id,
                        repo_id=str(
                            ((app_profile.get("profile") or {}).get("repo_ids") or [None])[0] or ""
                        ),
                        gap_type="missing_tooling",
                        severity="medium",
                        blocker=False,
                        detected_from="operation_logs",
                        required_capability="vercel:logs",
                        observed_request={"vercel_deployment_id": deployment.get("uid")},
                        suggested_fix=(
                            "Extend the Vercel adapter to load deployment "
                            "event logs for this project."
                        ),
                        metadata={"error": str(exc)},
                        run_id=str(request_context.get("run_id") or "").strip() or None,
                    )
            if deployment and deployment.get("uid"):
                await self._storage.upsert_operation_ref(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="vercel",
                    ref_kind="vercel_deployment_id",
                    ref_value=str(deployment.get("uid") or ""),
                    metadata={"source": "vercel"},
                )
            summary = {
                "project": _pick_fields(
                    project,
                    ["id", "name", "framework", "updatedAt", "latestDeployments", "link"],
                ),
                "deployment": deployment,
                "domains": domains,
            }
            summary_evidence = await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="vercel",
                evidence_type="summary",
                title="Vercel deployment summary",
                payload=summary,
                metadata={"service_kind": "vercel"},
            )
            if events:
                await self._storage.record_operation_evidence(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="vercel",
                    evidence_type="events",
                    title="Vercel deployment events",
                    payload={"events": events},
                    metadata={"service_kind": "vercel"},
                )
            if log_text:
                await self._storage.record_operation_evidence(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="vercel",
                    evidence_type="logs",
                    title="Vercel deployment logs",
                    payload={"stream": "vercel", "events": events[:20]},
                    log_text=log_text,
                    metadata={"service_kind": "vercel"},
                )
            status = "active"
            state_value = (
                str((deployment or {}).get("readyState") or (deployment or {}).get("state") or "")
                .strip()
                .lower()
            )
            if state_value in {"error", "failed", "canceled"}:
                status = "failed"
                await self._storage.record_operation_incident(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="vercel",
                    incident_type="deployment_failed",
                    severity="high",
                    blocking=True,
                    root_cause_summary=f"Vercel deployment failed with state `{state_value}`.",
                    recommended_fix="Inspect the deployment logs and failing build step in Vercel.",
                    evidence_refs=[str(summary_evidence["evidence_id"])],
                    metadata={"deployment": deployment},
                )
            elif state_value in {"ready", "succeeded"}:
                status = "succeeded"
            return {
                "summary": summary,
                "status": status,
                "lifecycle_stage": "vercel",
            }
        finally:
            await client.close()

    async def _collect_clerk_operation_evidence(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        app_id: str,
        app_profile: dict[str, Any],
        operation_id: str,
        refs: dict[str, str],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        connector_ref = self._service_connector_for(app_profile, service_kind="clerk")
        connector = await self._require_connector(
            owner_id,
            connector_id=str(connector_ref.get("connector_id") or _CLERK_CONNECTOR_ID),
            service_kind="clerk",
            require_secret=False,
        )
        metadata = dict(connector.get("metadata") or {})
        issuer = refs.get("issuer") or str(metadata.get("issuer") or "").strip() or None
        jwks_url = refs.get("jwks_url") or _derive_clerk_jwks_url(metadata)
        instance_summary = {
            "issuer": issuer,
            "jwks_url": jwks_url,
            "frontend_api_url": str(metadata.get("frontend_api_url") or "").strip() or None,
            "instance_name": str(metadata.get("instance_name") or "").strip() or None,
        }
        client = ClerkMetadataClient()
        try:
            payload: dict[str, Any] = {"instance": instance_summary}
            if issuer:
                with contextlib.suppress(ClerkMetadataError):
                    payload["openid_configuration"] = await client.get_openid_configuration(issuer)
            if jwks_url:
                with contextlib.suppress(ClerkMetadataError):
                    payload["jwks"] = await client.get_jwks(jwks_url)
            app_diagnostics: list[dict[str, Any]] = []
            run_id = str(request_context.get("run_id") or refs.get("run_id") or "").strip()
            if run_id:
                app_diagnostics.extend(
                    await self._storage.get_run_log_chunks(
                        owner_id,
                        run_id,
                        query_text="clerk",
                        limit=50,
                    )
                )
                app_diagnostics.extend(
                    await self._storage.get_run_log_chunks(
                        owner_id,
                        run_id,
                        query_text="auth",
                        limit=50,
                    )
                )
            if not app_diagnostics:
                await self._record_gap(
                    owner_id=owner_id,
                    principal_id=principal_id,
                    session_id=_normalize_session_id(request_context.get("session_id")),
                    app_id=app_id,
                    repo_id=str(
                        ((app_profile.get("profile") or {}).get("repo_ids") or [None])[0] or ""
                    ),
                    gap_type="missing_docs",
                    severity="medium",
                    blocker=False,
                    detected_from="operation_resolve",
                    required_capability="clerk:app_diagnostics",
                    observed_request={"run_id": run_id or None},
                    suggested_fix=(
                        "Attach a CI run id or add dedicated Clerk auth "
                        "diagnostics to the app knowledge pack."
                    ),
                    metadata={},
                    run_id=run_id or None,
                )
            summary = {**payload, "app_diagnostics": app_diagnostics[:20]}
            summary_evidence = await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="clerk",
                evidence_type="summary",
                title="Clerk auth summary",
                payload=summary,
                metadata={"service_kind": "clerk"},
            )
            if app_diagnostics:
                diagnostic_text = "\n".join(
                    str(entry.get("message") or "") for entry in app_diagnostics[:50]
                )
                await self._storage.record_operation_evidence(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="clerk",
                    evidence_type="logs",
                    title="Application auth diagnostics",
                    payload={"stream": "app", "entries": app_diagnostics[:20]},
                    log_text=diagnostic_text,
                    metadata={"service_kind": "clerk"},
                )
            status = (
                "succeeded"
                if payload.get("openid_configuration") or payload.get("jwks")
                else "active"
            )
            if run_id and any(
                "error" in str(entry.get("message") or "").lower() for entry in app_diagnostics
            ):
                status = "failed"
                await self._storage.record_operation_incident(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="clerk",
                    incident_type="auth_failed",
                    severity="high",
                    blocking=True,
                    root_cause_summary=(
                        "Application auth diagnostics indicate a Clerk-related " "failure."
                    ),
                    recommended_fix=(
                        "Inspect the auth logs and Clerk issuer/JWKS "
                        "configuration for the failing app."
                    ),
                    evidence_refs=[str(summary_evidence["evidence_id"])],
                    metadata={"run_id": run_id},
                )
            return {
                "summary": summary,
                "status": status,
                "lifecycle_stage": "clerk",
            }
        finally:
            await client.close()

    async def _collect_stripe_operation_evidence(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        app_id: str,
        app_profile: dict[str, Any],
        operation_id: str,
        refs: dict[str, str],
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        connector_ref = self._service_connector_for(app_profile, service_kind="stripe")
        connector = await self._require_connector(
            owner_id,
            connector_id=str(connector_ref.get("connector_id") or _STRIPE_CONNECTOR_ID),
            service_kind="stripe",
        )
        client = StripeClient(token=str(connector["secret_value"]))
        try:
            account = await client.get_account()
            event_id = refs.get("stripe_event_id")
            customer_id = refs.get("customer_id")
            subscription_id = refs.get("subscription_id")
            event = await client.get_event(event_id) if event_id else None
            customer = await client.get_customer(customer_id) if customer_id else None
            subscription = (
                await client.get_subscription(subscription_id) if subscription_id else None
            )
            if event_id:
                await self._storage.upsert_operation_ref(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="stripe",
                    ref_kind="stripe_event_id",
                    ref_value=event_id,
                    metadata={"source": "stripe"},
                )
            summary = {
                "account": account,
                "event": event,
                "customer": customer,
                "subscription": subscription,
            }
            summary_evidence = await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="stripe",
                evidence_type="summary",
                title="Stripe billing summary",
                payload=summary,
                metadata={"service_kind": "stripe"},
            )
            event_lines = []
            if isinstance(event, dict):
                event_lines.append(str(event.get("type") or "stripe.event"))
                event_lines.append(
                    str(((event.get("data") or {}).get("object") or {}).get("id") or "")
                )
            if event_lines:
                await self._storage.record_operation_evidence(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="stripe",
                    evidence_type="events",
                    title="Stripe event details",
                    payload={"event": event},
                    log_text="\n".join(line for line in event_lines if line),
                    metadata={"service_kind": "stripe"},
                )
            status = "active"
            if isinstance(event, dict) and int(event.get("pending_webhooks") or 0) > 0:
                status = "failed"
                await self._storage.record_operation_incident(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="stripe",
                    incident_type="webhook_pending",
                    severity="medium",
                    blocking=False,
                    root_cause_summary="Stripe event still has pending webhook deliveries.",
                    recommended_fix=(
                        "Inspect the webhook destination and replay the event "
                        "after fixing the receiver."
                    ),
                    evidence_refs=[str(summary_evidence["evidence_id"])],
                    metadata={"event_id": event.get("id")},
                )
            elif event or customer or subscription:
                status = "succeeded"
            else:
                await self._record_gap(
                    owner_id=owner_id,
                    principal_id=principal_id,
                    session_id=_normalize_session_id(request_context.get("session_id")),
                    app_id=app_id,
                    repo_id=str(
                        ((app_profile.get("profile") or {}).get("repo_ids") or [None])[0] or ""
                    ),
                    gap_type="missing_connector",
                    severity="medium",
                    blocker=False,
                    detected_from="operation_resolve",
                    required_capability="stripe:event_or_object_ref",
                    observed_request={"refs": refs},
                    suggested_fix=(
                        "Provide a Stripe event, customer, or subscription "
                        "reference to collect billing evidence."
                    ),
                    metadata={},
                    run_id=str(request_context.get("run_id") or "").strip() or None,
                )
            return {
                "summary": summary,
                "status": status,
                "lifecycle_stage": "stripe",
            }
        finally:
            await client.close()

    async def _detect_test_plan_gaps(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        session_id: str | None,
        app_id: str,
        repo_id: str,
        knowledge_pack: dict[str, Any],
        request_context: dict[str, Any],
    ) -> list[dict[str, Any]]:
        gaps: list[dict[str, Any]] = []
        capability_registry = dict(knowledge_pack.get("capability_registry") or {})
        supported_tooling = {
            str(item).strip().lower()
            for item in list(capability_registry.get("supported_tooling") or [])
            if str(item).strip()
        }
        required_tooling = {
            str(item).strip().lower()
            for item in list(request_context.get("required_tooling") or [])
            if str(item).strip()
        }
        focus = str(request_context.get("focus") or "").strip().lower()
        changed_files = {
            str(item).strip().lower()
            for item in list(request_context.get("changed_files") or [])
            if str(item).strip()
        }
        if "playwright" in focus or any("playwright" in item for item in changed_files):
            required_tooling.add("playwright")
        for tool in sorted(required_tooling):
            if tool in supported_tooling:
                continue
            gaps.append(
                await self._record_gap(
                    owner_id=owner_id,
                    principal_id=principal_id,
                    session_id=session_id,
                    app_id=app_id,
                    repo_id=repo_id,
                    gap_type="missing_tooling",
                    severity="high",
                    blocker=True,
                    detected_from="test_plan_compile",
                    required_capability=tool,
                    observed_request={
                        "focus": focus or None,
                        "required_tooling": sorted(required_tooling),
                        "changed_files": sorted(changed_files),
                    },
                    suggested_fix=(
                        f"Add `{tool}` to the capability registry and test harness manifest."
                    ),
                    metadata={},
                    run_id=None,
                )
            )
            gaps.append(
                await self._record_gap(
                    owner_id=owner_id,
                    principal_id=principal_id,
                    session_id=session_id,
                    app_id=app_id,
                    repo_id=repo_id,
                    gap_type="missing_test_harness",
                    severity="high",
                    blocker=True,
                    detected_from="test_plan_compile",
                    required_capability=tool,
                    observed_request={"focus": focus or None},
                    suggested_fix=(
                        f"Create a `{tool}` harness and register it in the app knowledge pack."
                    ),
                    metadata={},
                    run_id=None,
                )
            )
        required_secret_refs = [
            str(item).strip()
            for item in list(
                (knowledge_pack.get("env_contract") or {}).get("required_secret_refs") or []
            )
            if str(item).strip()
        ]
        gaps.extend(
            await self._record_missing_secret_ref_gaps(
                owner_id=owner_id,
                principal_id=principal_id,
                session_id=session_id,
                app_id=app_id,
                repo_id=repo_id,
                required_secret_refs=required_secret_refs,
                reason="test_plan_compile",
            )
        )
        return gaps

    def _default_app_profile(self, repo: dict[str, Any], public_base_url: str) -> dict[str, Any]:
        repo_id = str(repo["repo_id"])
        bootstrap_profile = dict(repo.get("agent_bootstrap_profile") or {})
        workspace_manifest = _default_workspace_manifest(repo)
        base_url = public_base_url.rstrip("/") if public_base_url else ""
        return {
            "app_id": repo_id,
            "display_name": str(repo["display_name"]),
            "repo_ids": [repo_id],
            "github_repos": [str(repo["github_repo"])],
            "default_branches": [str(repo["default_branch"])],
            "stack_kind": str(repo["stack_kind"]),
            "workspace_roots": list(repo.get("allowed_paths") or []),
            "docs_bundle_id": f"{repo_id}:current",
            "docs_slugs": list(bootstrap_profile.get("docs_slugs") or []),
            "ci_profile_id": repo_id,
            "test_profile_id": f"{repo_id}:default",
            "mock_profile_ids": [entry["profile_id"] for entry in _default_mock_profiles(repo_id)],
            "env_contract_id": str(repo.get("secrets_profile") or "") or None,
            "secrets_profile_id": str(repo.get("secrets_profile") or "") or None,
            "required_secret_refs": [],
            "mock_default": True,
            "live_runtime_requirements": [],
            "required_scopes": list(bootstrap_profile.get("required_scopes") or ["cgs:agent"]),
            "service_connector_map": _default_service_connector_map(repo_id),
            "service_operations": _default_service_operations(repo_id),
            "capability_registry": _default_capability_registry(repo),
            "known_gaps_summary": {
                "open_total": 0,
                "blocker_total": 0,
                "recurring_total": 0,
            },
            "workspace_manifest": workspace_manifest,
            "github_governance": dict(workspace_manifest.get("github_governance") or {}),
            "runtime_routes": {
                "apps": f"{base_url}/service/ai/v1/agent/apps"
                if base_url
                else "/service/ai/v1/agent/apps",
                "docs": f"{base_url}/service/ai/v1/agent/apps/{repo_id}/docs"
                if base_url
                else f"/service/ai/v1/agent/apps/{repo_id}/docs",
                "workspace_bundles": (
                    f"{base_url}/service/ai/v1/agent/apps/{repo_id}/workspace-bundles"
                )
                if base_url
                else f"/service/ai/v1/agent/apps/{repo_id}/workspace-bundles",
                "publish_candidates": (
                    f"{base_url}/service/ai/v1/agent/apps/{repo_id}/publish-candidates"
                )
                if base_url
                else f"/service/ai/v1/agent/apps/{repo_id}/publish-candidates",
                "operations": _operation_routes(base_url=base_url, app_id=repo_id),
                "services": _service_routes_for(repo_id, base_url=base_url)["catalog"],
                "service_read": _service_routes_for(repo_id, base_url=base_url)["service"],
            },
        }

    def _default_knowledge_pack(
        self,
        repo: dict[str, Any],
        docs_by_slug: dict[str, dict[str, Any]],
    ) -> dict[str, Any]:
        repo_id = str(repo["repo_id"])
        docs_slugs = list((repo.get("agent_bootstrap_profile") or {}).get("docs_slugs") or [])
        docs = [docs_by_slug[slug] for slug in docs_slugs if slug in docs_by_slug]
        workspace_manifest = _default_workspace_manifest(repo)
        openapi_hints = []
        if repo_id in {"catalyst-group-solutions", "zetherion-ai"}:
            openapi_hints = [
                "docs/technical/openapi-public-api.yaml",
                "docs/technical/openapi-cgs-gateway.yaml",
            ]
        return {
            "workspace_manifest": workspace_manifest,
            "api_contract_bundle": {
                "docs": docs,
                "capabilities": list(
                    (repo.get("metadata") or {}).get("certification_matrix") or []
                ),
                "openapi_hints": openapi_hints,
            },
            "test_harness_manifest": _default_test_harness_manifest(repo),
            "mock_harness_manifest": {
                "profiles": _default_mock_profiles(repo_id),
            },
            "env_contract": {
                "secrets_profile": str(repo.get("secrets_profile") or "") or None,
                "windows_execution_mode": str(repo.get("windows_execution_mode") or "command"),
                "service_connectors": _default_service_connector_map(repo_id),
                "required_secret_refs": [],
            },
            "command_catalog": _default_command_catalog(repo),
            "observability_contract": {
                "preferred_debugging_path": "resolve -> operation -> evidence",
                "operation_routes": _operation_routes(base_url="", app_id=repo_id),
                "run_logs": "/service/ai/v1/agent/runs/:runId/logs",
                "run_resources": "/service/ai/v1/agent/runs/:runId/resources",
                "admin_dashboard": "/admin/ai",
                "owner_dashboard": "/dashboard/ai",
            },
            "troubleshooting_playbook": {
                "docs": docs,
                "runbooks": [
                    "docs/development/testing.md",
                    "docs/development/ci-cd.md",
                    "docs/development/owner-ci-controller.md",
                ],
                "required_receipts": list(repo.get("certification_requirements") or []),
            },
            "service_connector_map": _default_service_connector_map(repo_id),
            "service_operations": _default_service_operations(repo_id),
            "capability_registry": _default_capability_registry(repo),
            "known_gaps_summary": {
                "open_total": 0,
                "blocker_total": 0,
                "recurring_total": 0,
            },
            "github_governance": dict(workspace_manifest.get("github_governance") or {}),
        }

    def _list_app_services(
        self,
        app_profile: dict[str, Any],
        *,
        public_base_url: str = "",
    ) -> list[dict[str, Any]]:
        profile = dict(app_profile.get("profile") or {})
        service_map = dict(profile.get("service_connector_map") or {})
        repo_id = str(app_profile.get("app_id") or "")
        base_url = public_base_url.rstrip("/")
        routes = _service_routes_for(repo_id, base_url=base_url)
        services: list[dict[str, Any]] = []
        for service_kind, raw_connector in sorted(service_map.items()):
            connector = dict(raw_connector or {})
            services.append(
                {
                    "service_kind": service_kind,
                    "connector_id": str(connector.get("connector_id") or "").strip() or None,
                    "broker_only": bool(connector.get("broker_only", True)),
                    "read_access": list(connector.get("read_access") or []),
                    "write_access": list(connector.get("write_access") or []),
                    "available_views": sorted(
                        _SERVICE_VIEW_CAPABILITIES.get(
                            service_kind,
                            {"overview": "metadata"},
                        ).keys()
                    ),
                    "available_actions": sorted(
                        _SERVICE_ACTION_CAPABILITIES.get(service_kind, {}).keys()
                    ),
                    "routes": {
                        "catalog": routes["catalog"],
                        "read": routes["service"].replace(":serviceKind", service_kind),
                        "actions": (
                            f"/service/ai/v1/agent/apps/{repo_id}/service-requests"
                            if not base_url
                            else f"{base_url}/service/ai/v1/agent/apps/{repo_id}/service-requests"
                        ),
                    },
                }
            )
        return services

    def _service_connector_for(
        self,
        app_profile: dict[str, Any],
        *,
        service_kind: str,
    ) -> dict[str, Any]:
        profile = dict(app_profile.get("profile") or {})
        service_map = dict(profile.get("service_connector_map") or {})
        connector = dict(service_map.get(service_kind) or {})
        if not connector:
            raise ValueError(
                f"App `{app_profile.get('app_id')}` does not declare a `{service_kind}` connector"
            )
        allowed_views = _SERVICE_VIEW_CAPABILITIES.get(service_kind, {"overview": "metadata"})
        return {
            **connector,
            "service_kind": service_kind,
            "available_views": sorted(allowed_views.keys()),
            "available_actions": sorted(_SERVICE_ACTION_CAPABILITIES.get(service_kind, {}).keys()),
        }

    async def _require_connector(
        self,
        owner_id: str,
        *,
        connector_id: str,
        service_kind: str,
        require_secret: bool = True,
    ) -> dict[str, Any]:
        connector = await self._storage.get_external_service_connector_with_secret(
            owner_id,
            connector_id,
        )
        if connector is None:
            raise ValueError(f"Connector `{connector_id}` not found")
        if str(connector.get("service_kind") or "").strip() != service_kind:
            raise ValueError(f"Connector `{connector_id}` is not a `{service_kind}` connector")
        if not bool(connector.get("active", True)):
            raise ValueError(f"Connector `{connector_id}` is inactive")
        if require_secret and not str(connector.get("secret_value") or "").strip():
            raise ValueError(f"Connector `{connector_id}` has no secret configured")
        return connector

    async def _read_service_view(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        app_id: str,
        app_profile: dict[str, Any],
        service_kind: str,
        view: str,
        public_base_url: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        try:
            connector = self._service_connector_for(app_profile, service_kind=service_kind)
            capability_map = _SERVICE_VIEW_CAPABILITIES.get(service_kind, {"overview": "metadata"})
            if view not in capability_map:
                raise ValueError(f"Unsupported `{service_kind}` broker view `{view}`")
            allowed_ops = {
                str(entry).strip()
                for entry in list(connector.get("read_access") or [])
                if str(entry).strip()
            }
            required_capability = capability_map[view]
            if allowed_ops and required_capability not in allowed_ops:
                raise ValueError(
                    f"Connector `{connector.get('connector_id')}` does not allow "
                    f"`{service_kind}` view `{view}`"
                )
            if service_kind == "github":
                payload = await self._read_github_service_view(
                    owner_id=owner_id,
                    app_profile=app_profile,
                    connector_id=str(connector.get("connector_id") or _GITHUB_CONNECTOR_ID),
                    view=view,
                    request_context=request_context,
                )
            elif service_kind == "vercel":
                payload = await self._read_vercel_service_view(
                    owner_id=owner_id,
                    app_profile=app_profile,
                    connector_id=str(connector.get("connector_id") or _VERCEL_CONNECTOR_ID),
                    view=view,
                    request_context=request_context,
                )
            elif service_kind == "clerk":
                payload = await self._read_clerk_service_view(
                    owner_id=owner_id,
                    app_profile=app_profile,
                    connector_id=str(connector.get("connector_id") or _CLERK_CONNECTOR_ID),
                    view=view,
                    request_context=request_context,
                )
            elif service_kind == "stripe":
                payload = await self._read_stripe_service_view(
                    owner_id=owner_id,
                    app_profile=app_profile,
                    connector_id=str(connector.get("connector_id") or _STRIPE_CONNECTOR_ID),
                    view=view,
                    request_context=request_context,
                )
            else:
                payload = await self._read_generic_service_view(
                    owner_id=owner_id,
                    app_profile=app_profile,
                    connector_id=str(connector.get("connector_id") or "").strip(),
                    service_kind=service_kind,
                    view=view,
                )
        except ValueError as exc:
            await self._record_gap(
                owner_id=owner_id,
                principal_id=principal_id,
                session_id=_normalize_session_id(request_context.get("session_id")),
                app_id=app_id,
                repo_id=(
                    str(
                        ((app_profile.get("profile") or {}).get("repo_ids") or [None])[0] or ""
                    ).strip()
                    or None
                ),
                gap_type="missing_connector"
                if "connector" in str(exc).lower()
                else "unsupported_service_action",
                severity="high",
                blocker=True,
                detected_from="service_read",
                required_capability=f"{service_kind}:{view}",
                observed_request={
                    "service_kind": service_kind,
                    "view": view,
                },
                suggested_fix=(
                    f"Configure the `{service_kind}` connector and grant `{view}` access."
                    if "connector" in str(exc).lower()
                    else f"Extend the `{service_kind}` broker to support `{view}`."
                ),
                metadata={"error": str(exc)},
                run_id=None,
            )
            raise
        await self._storage.record_agent_audit_event(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
            service_kind=service_kind,
            resource=str(connector.get("connector_id") or service_kind),
            action=f"service.read.{view}",
            decision="allowed",
            audit={
                "service_kind": service_kind,
                "view": view,
                "public_base_url": public_base_url or None,
            },
        )
        return payload

    async def _read_github_service_view(
        self,
        *,
        owner_id: str,
        app_profile: dict[str, Any],
        connector_id: str,
        view: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        connector = await self._require_github_connector(owner_id, connector_id)
        profile = dict(app_profile.get("profile") or {})
        github_repo = str((profile.get("github_repos") or [None])[0] or "").strip()
        if not github_repo:
            raise ValueError(
                f"App `{app_profile.get('app_id')}` does not declare a GitHub repository"
            )
        repo_owner, repo_name = _split_github_repo(github_repo)
        limit = _normalize_limit(request_context.get("limit"), default=10, maximum=20)
        client = GitHubClient(token=str(connector["secret_value"]))
        try:
            repository = await client.get_repository(repo_owner, repo_name)
            if view == "overview":
                branch = str(
                    request_context.get("branch")
                    or repository.default_branch
                    or ((profile.get("default_branches") or [None])[0] or "")
                ).strip()
                protection = (
                    await client.get_branch_protection(repo_owner, repo_name, branch=branch)
                    if branch
                    else None
                )
                pull_requests = await client.list_pull_requests(
                    repo_owner,
                    repo_name,
                    state=str(request_context.get("state") or "open").strip() or "open",
                    base=branch or None,
                    per_page=min(limit, 10),
                    page=1,
                )
                workflow_runs = await client.list_workflow_runs(
                    repo_owner,
                    repo_name,
                    branch=branch or None,
                    per_page=min(limit, 10),
                    page=1,
                )
                return {
                    "service_kind": "github",
                    "view": view,
                    "repository": repository.to_dict(),
                    "default_branch": repository.default_branch,
                    "branch_protection": protection,
                    "pull_requests": [pull_request.to_dict() for pull_request in pull_requests],
                    "workflow_runs": [run.to_dict() for run in workflow_runs],
                    "github_governance": dict(profile.get("github_governance") or {}),
                    "connector": {
                        "connector_id": str(connector.get("connector_id") or connector_id),
                        "auth_kind": str(connector.get("auth_kind") or ""),
                    },
                }
            if view == "compare":
                base_ref = str(request_context.get("base") or "").strip()
                head_ref = str(request_context.get("head") or "").strip()
                if not base_ref or not head_ref:
                    raise ValueError("base and head are required for GitHub compare")
                comparison = await client.compare_commits(
                    repo_owner,
                    repo_name,
                    base=base_ref,
                    head=head_ref,
                )
                return {
                    "service_kind": "github",
                    "view": view,
                    "repository": repository.to_dict(),
                    "comparison": comparison,
                }
            if view == "pulls":
                pull_requests = await client.list_pull_requests(
                    repo_owner,
                    repo_name,
                    state=str(request_context.get("state") or "open").strip() or "open",
                    base=str(request_context.get("base") or "").strip() or None,
                    head=str(request_context.get("head") or "").strip() or None,
                    per_page=limit,
                    page=1,
                )
                return {
                    "service_kind": "github",
                    "view": view,
                    "repository": repository.to_dict(),
                    "pull_requests": [pull_request.to_dict() for pull_request in pull_requests],
                }
            if view == "workflows":
                workflow_runs = await client.list_workflow_runs(
                    repo_owner,
                    repo_name,
                    branch=str(request_context.get("branch") or "").strip() or None,
                    event=str(request_context.get("event") or "").strip() or None,
                    status=str(request_context.get("status") or "").strip() or None,
                    per_page=limit,
                    page=1,
                )
                return {
                    "service_kind": "github",
                    "view": view,
                    "repository": repository.to_dict(),
                    "workflow_runs": [run.to_dict() for run in workflow_runs],
                }
            raise ValueError(f"Unsupported GitHub broker view `{view}`")
        finally:
            await client.close()

    async def _read_vercel_service_view(
        self,
        *,
        owner_id: str,
        app_profile: dict[str, Any],
        connector_id: str,
        view: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        connector = await self._require_connector(
            owner_id,
            connector_id=connector_id,
            service_kind="vercel",
        )
        metadata = dict(connector.get("metadata") or {})
        project_ref = (
            str(request_context.get("project_ref") or "").strip()
            or str(metadata.get("project_name") or "").strip()
            or str(metadata.get("project_id") or "").strip()
            or str(app_profile.get("app_id") or "").strip()
        )
        if not project_ref:
            raise ValueError("project_ref is required for Vercel broker access")
        team_id = (
            str(request_context.get("team_id") or metadata.get("team_id") or "").strip() or None
        )
        limit = _normalize_limit(request_context.get("limit"), default=10, maximum=20)
        client = VercelClient(token=str(connector["secret_value"]))
        try:
            project = await client.get_project(project_ref, team_id=team_id)
            project_summary = _pick_fields(
                project,
                [
                    "id",
                    "name",
                    "framework",
                    "accountId",
                    "createdAt",
                    "updatedAt",
                    "latestDeployments",
                    "link",
                    "targets",
                ],
            )
            if view == "overview":
                deployments = await client.list_deployments(
                    project_id=str(project.get("id") or "").strip() or None,
                    project_name=str(project.get("name") or project_ref),
                    team_id=team_id,
                    limit=min(limit, 10),
                )
                domains = await client.list_domains(project_ref, team_id=team_id)
                return {
                    "service_kind": "vercel",
                    "view": view,
                    "project": project_summary,
                    "deployments": [
                        _pick_fields(
                            item,
                            [
                                "uid",
                                "name",
                                "url",
                                "createdAt",
                                "readyState",
                                "state",
                                "target",
                                "creator",
                                "meta",
                            ],
                        )
                        for item in deployments
                    ],
                    "domains": [
                        _pick_fields(
                            item,
                            [
                                "name",
                                "apexName",
                                "projectId",
                                "redirect",
                                "redirectStatusCode",
                                "gitBranch",
                                "updatedAt",
                                "verified",
                            ],
                        )
                        for item in domains
                    ],
                }
            if view == "deployments":
                deployments = await client.list_deployments(
                    project_id=str(project.get("id") or "").strip() or None,
                    project_name=str(project.get("name") or project_ref),
                    team_id=team_id,
                    limit=limit,
                )
                return {
                    "service_kind": "vercel",
                    "view": view,
                    "project": project_summary,
                    "deployments": [
                        _pick_fields(
                            item,
                            [
                                "uid",
                                "name",
                                "url",
                                "createdAt",
                                "readyState",
                                "state",
                                "target",
                                "creator",
                                "meta",
                            ],
                        )
                        for item in deployments
                    ],
                }
            if view == "domains":
                domains = await client.list_domains(project_ref, team_id=team_id)
                return {
                    "service_kind": "vercel",
                    "view": view,
                    "project": project_summary,
                    "domains": [
                        _pick_fields(
                            item,
                            [
                                "name",
                                "apexName",
                                "projectId",
                                "redirect",
                                "redirectStatusCode",
                                "gitBranch",
                                "updatedAt",
                                "verified",
                            ],
                        )
                        for item in domains
                    ],
                }
            if view == "envs":
                envs = await client.list_env_vars(project_ref, team_id=team_id)
                return {
                    "service_kind": "vercel",
                    "view": view,
                    "project": project_summary,
                    "envs": [
                        _pick_fields(
                            item,
                            [
                                "id",
                                "key",
                                "type",
                                "target",
                                "configurationId",
                                "createdAt",
                                "updatedAt",
                                "gitBranch",
                                "customEnvironmentIds",
                            ],
                        )
                        for item in envs
                    ],
                }
            raise ValueError(f"Unsupported Vercel broker view `{view}`")
        finally:
            await client.close()

    async def _read_clerk_service_view(
        self,
        *,
        owner_id: str,
        app_profile: dict[str, Any],
        connector_id: str,
        view: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        connector = await self._require_connector(
            owner_id,
            connector_id=connector_id,
            service_kind="clerk",
            require_secret=False,
        )
        metadata = dict(connector.get("metadata") or {})
        issuer = (
            str(request_context.get("issuer") or "").strip()
            or str(metadata.get("issuer") or "").strip()
        )
        frontend_api_url = (
            str(request_context.get("frontend_api_url") or "").strip()
            or str(metadata.get("frontend_api_url") or "").strip()
        )
        jwks_url = (
            str(request_context.get("jwks_url") or "").strip()
            or _derive_clerk_jwks_url(metadata)
            or ""
        )
        summary = {
            "issuer": issuer or None,
            "frontend_api_url": frontend_api_url or None,
            "jwks_url": jwks_url or None,
            "instance_name": str(metadata.get("instance_name") or "").strip() or None,
            "publishable_key_hint": str(metadata.get("publishable_key_hint") or "").strip() or None,
        }
        if view == "overview":
            return {
                "service_kind": "clerk",
                "view": view,
                "instance": summary,
                "app_id": str(app_profile.get("app_id") or ""),
            }
        client = ClerkMetadataClient()
        try:
            if view == "jwks":
                if not jwks_url:
                    raise ValueError("jwks_url is required for Clerk JWKS metadata")
                return {
                    "service_kind": "clerk",
                    "view": view,
                    "instance": summary,
                    "jwks": await client.get_jwks(jwks_url),
                }
            if view == "openid":
                if not issuer:
                    raise ValueError("issuer is required for Clerk OpenID metadata")
                return {
                    "service_kind": "clerk",
                    "view": view,
                    "instance": summary,
                    "openid_configuration": await client.get_openid_configuration(issuer),
                }
            raise ValueError(f"Unsupported Clerk broker view `{view}`")
        except ClerkMetadataError as exc:
            raise ValueError(str(exc)) from exc
        finally:
            await client.close()

    async def _read_stripe_service_view(
        self,
        *,
        owner_id: str,
        app_profile: dict[str, Any],
        connector_id: str,
        view: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        connector = await self._require_connector(
            owner_id,
            connector_id=connector_id,
            service_kind="stripe",
        )
        metadata = dict(connector.get("metadata") or {})
        limit = _normalize_limit(request_context.get("limit"), default=10, maximum=20)
        client = StripeClient(token=str(connector["secret_value"]))
        try:
            if view == "overview":
                account = await client.get_account()
                products = await client.list_products(limit=min(limit, 5))
                subscriptions = await client.list_subscriptions(limit=min(limit, 5))
                invoices = await client.list_invoices(limit=min(limit, 5))
                return {
                    "service_kind": "stripe",
                    "view": view,
                    "account": account,
                    "products": products,
                    "subscriptions": subscriptions,
                    "invoices": invoices,
                    "metadata": metadata,
                }
            if view == "products":
                return {
                    "service_kind": "stripe",
                    "view": view,
                    "products": await client.list_products(limit=limit),
                }
            if view == "prices":
                return {
                    "service_kind": "stripe",
                    "view": view,
                    "prices": await client.list_prices(
                        limit=limit,
                        product_id=str(request_context.get("product_id") or "").strip() or None,
                    ),
                }
            if view == "customers":
                return {
                    "service_kind": "stripe",
                    "view": view,
                    "customers": await client.list_customers(
                        limit=limit,
                        email=str(request_context.get("email") or "").strip() or None,
                    ),
                }
            if view == "subscriptions":
                return {
                    "service_kind": "stripe",
                    "view": view,
                    "subscriptions": await client.list_subscriptions(
                        limit=limit,
                        customer_id=(str(request_context.get("customer_id") or "").strip() or None),
                    ),
                }
            if view == "invoices":
                return {
                    "service_kind": "stripe",
                    "view": view,
                    "invoices": await client.list_invoices(
                        limit=limit,
                        customer_id=(str(request_context.get("customer_id") or "").strip() or None),
                    ),
                }
            if view == "webhook_health":
                return {
                    "service_kind": "stripe",
                    "view": view,
                    "webhook_endpoints": await client.list_webhook_endpoints(limit=limit),
                    "metadata": metadata,
                }
            raise ValueError(f"Unsupported Stripe broker view `{view}`")
        except StripeAPIError as exc:
            raise ValueError(str(exc)) from exc
        finally:
            await client.close()

    async def _execute_service_action(
        self,
        *,
        owner_id: str,
        principal_id: str | None,
        app_id: str,
        app_profile: dict[str, Any],
        service_kind: str,
        action_id: str,
        request_context: dict[str, Any],
    ) -> dict[str, Any]:
        repo_id = (
            str(((app_profile.get("profile") or {}).get("repo_ids") or [None])[0] or "").strip()
            or None
        )
        try:
            connector = self._service_connector_for(app_profile, service_kind=service_kind)
            allowed_actions = {
                str(entry).strip()
                for entry in list(connector.get("write_access") or [])
                if str(entry).strip()
            }
            capability = _SERVICE_ACTION_CAPABILITIES.get(service_kind, {}).get(action_id)
            if capability is None:
                raise ValueError(f"Unsupported `{service_kind}` service action `{action_id}`")
            if allowed_actions and capability not in allowed_actions:
                raise ValueError(
                    f"Connector `{connector.get('connector_id')}` does not allow "
                    f"`{service_kind}` action `{action_id}`"
                )
            input_payload = (
                dict(request_context.get("input") or {})
                if isinstance(request_context.get("input"), dict)
                else {}
            )
            if service_kind != "stripe":
                raise ValueError(f"Service action `{service_kind}:{action_id}` is not implemented")
            execution = await self._execute_stripe_service_action(
                owner_id=owner_id,
                connector_id=str(connector.get("connector_id") or _STRIPE_CONNECTOR_ID),
                action_id=action_id,
                input_payload=input_payload,
            )
        except ValueError as exc:
            await self._record_gap(
                owner_id=owner_id,
                principal_id=principal_id,
                session_id=_normalize_session_id(request_context.get("session_id")),
                app_id=app_id,
                repo_id=repo_id,
                gap_type="unsupported_service_action"
                if "action" in str(exc).lower()
                else "missing_connector",
                severity="high",
                blocker=True,
                detected_from="service_request_validation",
                required_capability=f"{service_kind}:{action_id}",
                observed_request={"service_kind": service_kind, "action_id": action_id},
                suggested_fix=(
                    "Check the "
                    f"`{service_kind}` connector and service action registry for "
                    f"`{action_id}`."
                ),
                metadata={"error": str(exc)},
                run_id=None,
            )
            raise
        audit = await self._storage.record_agent_audit_event(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
            service_kind=service_kind,
            resource=str(connector.get("connector_id") or service_kind),
            action=f"service.request.{action_id}",
            decision="allowed",
            audit={
                "service_kind": service_kind,
                "action_id": action_id,
                "status": execution.get("status"),
            },
        )
        service_request = await self._storage.create_agent_service_request(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
            service_kind=service_kind,
            action_id=action_id,
            target_ref=str(request_context.get("target_ref") or "").strip() or None,
            tenant_id=str(request_context.get("tenant_id") or "").strip() or None,
            change_reason=str(request_context.get("change_reason") or "").strip() or None,
            request_payload=input_payload,
            status=str(execution.get("status") or "executed"),
            approved=True,
            result=execution,
            audit_id=str(audit.get("audit_id") or ""),
            executed=True,
        )
        return {
            "request": service_request,
            "result": execution,
            "audit": audit,
        }

    async def _execute_stripe_service_action(
        self,
        *,
        owner_id: str,
        connector_id: str,
        action_id: str,
        input_payload: dict[str, Any],
    ) -> dict[str, Any]:
        connector = await self._require_connector(
            owner_id,
            connector_id=connector_id,
            service_kind="stripe",
        )
        client = StripeClient(token=str(connector["secret_value"]))
        try:
            if action_id == "product.ensure":
                product = await client.ensure_product(
                    name=str(input_payload.get("name") or "").strip(),
                    product_id=str(input_payload.get("product_id") or "").strip() or None,
                    lookup_key=str(input_payload.get("lookup_key") or "").strip() or None,
                    metadata=(
                        dict(input_payload.get("metadata") or {})
                        if isinstance(input_payload.get("metadata"), dict)
                        else {}
                    ),
                    description=str(input_payload.get("description") or "").strip() or None,
                )
                return {"status": "executed", "product": product}
            if action_id == "price.ensure":
                price = await client.ensure_price(
                    product_id=str(input_payload.get("product_id") or "").strip(),
                    currency=str(input_payload.get("currency") or "usd").strip() or "usd",
                    unit_amount=int(input_payload.get("unit_amount") or 0),
                    recurring_interval=(
                        str(input_payload.get("recurring_interval") or "").strip() or None
                    ),
                    lookup_key=str(input_payload.get("lookup_key") or "").strip() or None,
                )
                return {"status": "executed", "price": price}
            if action_id == "customer.link":
                customer = await client.link_customer(
                    customer_id=str(input_payload.get("customer_id") or "").strip() or None,
                    email=str(input_payload.get("email") or "").strip() or None,
                    name=str(input_payload.get("name") or "").strip() or None,
                    metadata=(
                        dict(input_payload.get("metadata") or {})
                        if isinstance(input_payload.get("metadata"), dict)
                        else {}
                    ),
                )
                return {"status": "executed", "customer": customer}
            if action_id == "subscription.link":
                subscription = await client.link_subscription(
                    subscription_id=(
                        str(input_payload.get("subscription_id") or "").strip() or None
                    ),
                    customer_id=str(input_payload.get("customer_id") or "").strip() or None,
                    price_id=str(input_payload.get("price_id") or "").strip() or None,
                )
                return {"status": "executed", "subscription": subscription}
            if action_id == "subscription.update_price":
                subscription = await client.update_subscription_price(
                    subscription_id=str(input_payload.get("subscription_id") or "").strip(),
                    price_id=str(input_payload.get("price_id") or "").strip(),
                )
                return {"status": "executed", "subscription": subscription}
            if action_id == "meter.config.ensure":
                meter = await client.ensure_meter(
                    event_name=str(input_payload.get("event_name") or "").strip(),
                    display_name=str(input_payload.get("display_name") or "").strip() or None,
                    customer_mapping_key=(
                        str(input_payload.get("customer_mapping_key") or "").strip() or None
                    ),
                    metadata=(
                        dict(input_payload.get("metadata") or {})
                        if isinstance(input_payload.get("metadata"), dict)
                        else {}
                    ),
                )
                return {"status": "executed", "meter": meter}
            raise ValueError(f"Unsupported Stripe action `{action_id}`")
        except StripeAPIError as exc:
            raise ValueError(str(exc)) from exc
        finally:
            await client.close()

    async def _read_generic_service_view(
        self,
        *,
        owner_id: str,
        app_profile: dict[str, Any],
        connector_id: str,
        service_kind: str,
        view: str,
    ) -> dict[str, Any]:
        connector = await self._require_connector(
            owner_id,
            connector_id=connector_id,
            service_kind=service_kind,
            require_secret=False,
        )
        return {
            "service_kind": service_kind,
            "view": view,
            "connector": {
                "connector_id": str(connector.get("connector_id") or connector_id),
                "service_kind": service_kind,
                "metadata": dict(connector.get("metadata") or {}),
                "policy": dict(connector.get("policy") or {}),
            },
            "app_id": str(app_profile.get("app_id") or ""),
        }

    async def _resolve_repo_profile(self, owner_id: str, repo_id: str) -> dict[str, Any]:
        profile = await self._storage.get_repo_profile(owner_id, repo_id)
        if profile is None:
            built_in = default_repo_profile(repo_id)
            if built_in is None:
                raise ValueError(f"Repo profile `{repo_id}` not found")
            profile = built_in
        return profile

    async def _list_accessible_apps(
        self,
        owner_id: str,
        principal_id: str,
    ) -> list[dict[str, Any]]:
        apps = await self._storage.list_agent_app_profiles(owner_id)
        grants = await self._storage.list_external_access_grants(
            owner_id, principal_id=principal_id
        )
        if not grants:
            return []
        allowed_app_ids: set[str] = set()
        repo_to_apps: dict[str, set[str]] = {}
        for app in apps:
            profile = dict(app.get("profile") or {})
            for repo_id in list(profile.get("repo_ids") or []):
                repo_to_apps.setdefault(str(repo_id), set()).add(str(app["app_id"]))
        for grant in grants:
            if not bool(grant.get("active", True)):
                continue
            resource_type = str(grant.get("resource_type") or "").strip()
            resource_id = str(grant.get("resource_id") or "").strip()
            if resource_type == "app":
                allowed_app_ids.add(resource_id)
            elif resource_type == "repo":
                allowed_app_ids.update(repo_to_apps.get(resource_id, set()))
        return [app for app in apps if str(app["app_id"]) in allowed_app_ids]

    async def _require_app_access(
        self,
        owner_id: str,
        *,
        principal_id: str | None,
        app_id: str,
    ) -> dict[str, Any]:
        app = await self._storage.get_agent_app_profile(owner_id, app_id)
        if app is None:
            raise ValueError(f"App `{app_id}` not found")
        if not principal_id:
            return app
        accessible = await self._list_accessible_apps(owner_id, principal_id)
        allowed = {str(entry["app_id"]) for entry in accessible}
        if app_id not in allowed:
            raise ValueError(f"Principal `{principal_id}` is not allowed to access app `{app_id}`")
        return app

    def _build_workspace_bundle(
        self,
        *,
        repo: dict[str, Any],
        knowledge_pack: dict[str, Any],
        git_ref: str,
    ) -> tuple[dict[str, Any], str | None]:
        repo_id = str(repo["repo_id"])
        repo_root = None
        for raw_path in list(repo.get("allowed_paths") or []):
            path_str = str(raw_path).strip()
            candidate = Path(path_str)
            if candidate.exists() and candidate.is_dir():
                repo_root = candidate
                break
        resolved_ref = _resolve_git_ref(repo_root, git_ref) if repo_root is not None else None
        bundle: dict[str, Any] = {
            "repo_id": repo_id,
            "git_ref": git_ref,
            "resolved_ref": resolved_ref or git_ref,
            "workspace_manifest": dict(knowledge_pack.get("workspace_manifest") or {}),
            "knowledge_pack_version": "current",
            "source_kind": "local_checkout" if repo_root is not None else "metadata_only",
            "download_mode": "metadata_only",
        }
        if repo_root is not None:
            archive_bytes, file_count = _tar_workspace(repo_root)
            bundle["file_count"] = file_count
            bundle["archive_size_bytes"] = len(archive_bytes)
            bundle["archive_sha256"] = hashlib.sha256(archive_bytes).hexdigest()
            if len(archive_bytes) <= _INLINE_ARCHIVE_MAX_BYTES:
                bundle["archive_format"] = "tar.gz"
                bundle["archive_base64"] = base64.b64encode(archive_bytes).decode("ascii")
                bundle["download_mode"] = "inline_base64"
            else:
                bundle["archive_omitted"] = {
                    "reason": "inline_archive_too_large",
                    "max_inline_bytes": _INLINE_ARCHIVE_MAX_BYTES,
                }
        return bundle, resolved_ref

    async def _create_workspace_bundle_payload(
        self,
        *,
        owner_id: str,
        repo: dict[str, Any],
        knowledge_pack: dict[str, Any],
        git_ref: str,
    ) -> tuple[dict[str, Any], str | None]:
        bundle, resolved_ref = self._build_workspace_bundle(
            repo=repo,
            knowledge_pack=knowledge_pack,
            git_ref=git_ref,
        )
        if bundle.get("source_kind") != "metadata_only":
            return bundle, resolved_ref
        github_repo = str(repo.get("github_repo") or "").strip()
        if not github_repo:
            return bundle, resolved_ref
        github_connector = (knowledge_pack.get("service_connector_map") or {}).get("github") or {}
        connector_id = (
            str(github_connector.get("connector_id") or _GITHUB_CONNECTOR_ID).strip()
            or _GITHUB_CONNECTOR_ID
        )
        try:
            return await self._build_github_workspace_bundle(
                owner_id=owner_id,
                repo_id=str(repo["repo_id"]),
                github_repo=github_repo,
                knowledge_pack=knowledge_pack,
                git_ref=git_ref,
                connector_id=connector_id,
            )
        except (GitHubAPIError, ValueError) as exc:
            log.warning(
                "github_workspace_bundle_fallback_failed",
                repo_id=str(repo["repo_id"]),
                github_repo=github_repo,
                error=str(exc),
            )
            return bundle, resolved_ref

    async def _build_github_workspace_bundle(
        self,
        *,
        owner_id: str,
        repo_id: str,
        github_repo: str,
        knowledge_pack: dict[str, Any],
        git_ref: str,
        connector_id: str,
    ) -> tuple[dict[str, Any], str | None]:
        connector = await self._require_github_connector(owner_id, connector_id)
        owner, repo_name = _split_github_repo(github_repo)
        client = GitHubClient(token=str(connector["secret_value"]))
        try:
            repository = await client.get_repository(owner, repo_name)
            resolved_ref = (
                repository.default_branch if git_ref.strip() in {"", "HEAD"} else git_ref.strip()
            )
            archive_bytes = await client.get_repository_archive(
                owner,
                repo_name,
                ref=resolved_ref,
            )
        finally:
            await client.close()
        bundle: dict[str, Any] = {
            "repo_id": repo_id,
            "git_ref": git_ref,
            "resolved_ref": resolved_ref,
            "workspace_manifest": dict(knowledge_pack.get("workspace_manifest") or {}),
            "knowledge_pack_version": "current",
            "source_kind": "github_archive",
            "download_mode": "metadata_only",
            "connector_id": connector_id,
            "archive_format": "tar.gz",
            "archive_size_bytes": len(archive_bytes),
            "archive_sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "repository": repository.to_dict(),
        }
        if len(archive_bytes) <= _INLINE_ARCHIVE_MAX_BYTES:
            bundle["archive_base64"] = base64.b64encode(archive_bytes).decode("ascii")
            bundle["download_mode"] = "inline_base64"
        else:
            bundle["archive_omitted"] = {
                "reason": "inline_archive_too_large",
                "max_inline_bytes": _INLINE_ARCHIVE_MAX_BYTES,
            }
        return bundle, resolved_ref

    async def _require_github_connector(
        self,
        owner_id: str,
        connector_id: str | None = None,
    ) -> dict[str, Any]:
        return await self._require_connector(
            owner_id,
            connector_id=connector_id or _GITHUB_CONNECTOR_ID,
            service_kind="github",
        )

    async def _discover_github_repositories(
        self,
        owner_id: str,
        *,
        connector_id: str | None,
        query: str | None,
        limit: int,
        private_only: bool,
    ) -> list[dict[str, Any]]:
        connector = await self._require_github_connector(owner_id, connector_id)
        policy = dict(connector.get("policy") or {})
        allowlist = {
            str(item).strip()
            for item in list(policy.get("allowed_repositories") or [])
            if str(item).strip()
        }
        allowed_owners = {
            str(item).strip()
            for item in list(policy.get("allowed_owners") or [])
            if str(item).strip()
        }
        search_term = str(query or "").strip().lower()
        client = GitHubClient(token=str(connector["secret_value"]))
        try:
            page = 1
            results: list[dict[str, Any]] = []
            use_installation_endpoint = "app" in str(connector.get("auth_kind") or "").lower()
            while len(results) < limit:
                if use_installation_endpoint:
                    repositories = await client.list_installation_repositories(
                        per_page=min(limit, 100),
                        page=page,
                    )
                else:
                    repositories = await client.list_repositories(
                        per_page=min(limit, 100),
                        page=page,
                    )
                if not repositories:
                    break
                for repository in repositories:
                    repo_data = repository.to_dict()
                    if private_only and not bool(repo_data.get("private")):
                        continue
                    if bool(repo_data.get("archived")):
                        continue
                    if allowlist and str(repo_data["full_name"]) not in allowlist:
                        continue
                    if allowed_owners and str(repo_data["owner"]) not in allowed_owners:
                        continue
                    if (
                        search_term
                        and search_term
                        not in " ".join(
                            [
                                str(repo_data.get("name") or ""),
                                str(repo_data.get("full_name") or ""),
                                str(repo_data.get("description") or ""),
                            ]
                        ).lower()
                    ):
                        continue
                    results.append(
                        {
                            **repo_data,
                            "connector_id": str(connector["connector_id"]),
                        }
                    )
                    if len(results) >= limit:
                        break
                if len(repositories) < min(limit, 100):
                    break
                page += 1
            return results
        finally:
            await client.close()

    async def _enroll_github_repository(
        self,
        *,
        owner_id: str,
        github_repo: str,
        app_id: str | None,
        display_name: str | None,
        stack_kind: str,
        public_base_url: str,
        overrides: dict[str, Any],
        enforce_managed_repo: bool,
        principal_id: str | None,
    ) -> dict[str, Any]:
        connector = await self._require_github_connector(owner_id, _GITHUB_CONNECTOR_ID)
        repo_owner, repo_name = _split_github_repo(github_repo)
        client = GitHubClient(token=str(connector["secret_value"]))
        try:
            repository = await client.get_repository(repo_owner, repo_name)
        finally:
            await client.close()

        repo_id = app_id or _slugify_repo_id(repository.name)
        repo_profile = self._build_enrolled_repo_profile(
            repository=repository.to_dict(),
            repo_id=repo_id,
            display_name=display_name or repository.name,
            stack_kind=stack_kind,
            overrides=overrides,
        )
        stored_repo = await self._storage.upsert_repo_profile(owner_id, repo_profile)

        docs_by_slug = {
            doc["slug"]: doc["manifest"]
            for doc in await self._storage.list_agent_docs_manifests(owner_id)
        }
        app_profile = self._default_app_profile(stored_repo, public_base_url)
        app_profile = self._merge_mapping(
            app_profile,
            dict(overrides.get("app_profile") or {}),
        )
        stored_app = await self._storage.upsert_agent_app_profile(
            owner_id,
            app_id=repo_id,
            display_name=str(app_profile.get("display_name") or repo_profile["display_name"]),
            profile=app_profile,
            active=True,
        )

        knowledge_pack = self._default_knowledge_pack(stored_repo, docs_by_slug)
        knowledge_pack = self._merge_mapping(
            knowledge_pack,
            dict(overrides.get("knowledge_pack") or {}),
        )
        stored_pack = await self._storage.upsert_agent_knowledge_pack(
            owner_id,
            app_id=repo_id,
            version="current",
            pack=knowledge_pack,
            current=True,
        )

        governance: dict[str, Any] | None = None
        if enforce_managed_repo:
            governance = await self._enforce_managed_repo(
                owner_id=owner_id,
                app_id=repo_id,
                github_repo=github_repo,
                default_branch=repository.default_branch,
                principal_id=principal_id,
            )

        await self._storage.record_agent_audit_event(
            owner_id,
            principal_id=principal_id,
            app_id=repo_id,
            service_kind="github",
            resource=github_repo,
            action="repo.enroll",
            decision="allowed",
            audit={
                "repo_id": repo_id,
                "default_branch": repository.default_branch,
                "managed_repo_enforced": bool(
                    governance and governance.get("governance", {}).get("applied")
                ),
            },
        )
        return {
            "repository": repository.to_dict(),
            "repo_profile": stored_repo,
            "app": stored_app,
            "knowledge_pack": stored_pack,
            **({"governance": governance} if governance else {}),
        }

    def _build_enrolled_repo_profile(
        self,
        *,
        repository: dict[str, Any],
        repo_id: str,
        display_name: str,
        stack_kind: str,
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        repo_name = str(repository.get("name") or repo_id)
        local_root = str((_REPO_ROOT.parent / repo_name).resolve())
        windows_root = rf"C:\ZetherionCI\workspaces\{repo_name}"
        base: dict[str, Any] = {
            "repo_id": repo_id,
            "display_name": display_name,
            "github_repo": str(repository["full_name"]),
            "default_branch": str(repository.get("default_branch") or "main"),
            "stack_kind": stack_kind,
            "mandatory_static_gates": [],
            "local_fast_lanes": [],
            "local_full_lanes": [],
            "windows_full_lanes": [],
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
                "rebalance_enabled": True,
            },
            "resource_classes": {
                "cpu": {"max_parallel": 8},
                "service": {"max_parallel": 2},
                "serial": {"max_parallel": 1},
            },
            "windows_execution_mode": "docker_only",
            "certification_requirements": ["mandatory_static_gates"],
            "scheduled_canaries": [],
            "debug_policy": {
                "redact_display_logs": True,
                "retain_debug_bundle_days": 14,
                "retain_raw_artifact_days": 14,
            },
            "agent_bootstrap_profile": {
                "client_kind": "managed-github-repo",
                "docs_slugs": [],
                "required_scopes": ["cgs:agent"],
            },
            "review_policy": {
                "require_reviewer": True,
                "required_statuses": ["zetherion/merge-readiness"],
            },
            "promotion_policy": {
                "deployment_mode": "zetherion_control_plane",
                "github_decision_mode": "external_status_only",
                "status_contexts": {
                    "merge": "zetherion/merge-readiness",
                    "deploy": "zetherion/deploy-readiness",
                },
                "require_certification": False,
                "require_release_receipt": True,
            },
            "allowed_paths": [local_root, windows_root],
            "secrets_profile": None,
            "active": True,
            "metadata": {
                "managed_by_zetherion": True,
                "source": "github_enrollment",
                "private": bool(repository.get("private", False)),
                "archived": bool(repository.get("archived", False)),
                "html_url": str(repository.get("html_url") or ""),
                "project_dashboard_tags": ["managed-repo", stack_kind],
            },
        }
        return self._merge_mapping(base, overrides)

    async def _enforce_managed_repo(
        self,
        *,
        owner_id: str,
        app_id: str,
        github_repo: str,
        default_branch: str | None,
        principal_id: str | None,
    ) -> dict[str, Any]:
        connector = await self._require_github_connector(owner_id, _GITHUB_CONNECTOR_ID)
        repo_owner, repo_name = _split_github_repo(github_repo)
        client = GitHubClient(token=str(connector["secret_value"]))
        policy = dict(connector.get("policy") or {})
        branch = str(default_branch or "").strip()
        try:
            if not branch:
                repository = await client.get_repository(repo_owner, repo_name)
                branch = repository.default_branch
            restrictions_payload = {
                "users": [
                    str(value).strip()
                    for value in list(policy.get("allowed_push_users") or [])
                    if str(value).strip()
                ],
                "teams": [
                    str(value).strip()
                    for value in list(policy.get("allowed_push_teams") or [])
                    if str(value).strip()
                ],
                "apps": [
                    str(value).strip()
                    for value in list(policy.get("allowed_push_apps") or [])
                    if str(value).strip()
                ],
            }
            restrictions = restrictions_payload if any(restrictions_payload.values()) else None
            protection_payload = {
                "required_status_checks": None,
                "enforce_admins": bool(policy.get("enforce_admins", True)),
                "required_pull_request_reviews": {
                    "dismiss_stale_reviews": bool(policy.get("dismiss_stale_reviews", True)),
                    "require_code_owner_reviews": bool(
                        policy.get("require_code_owner_reviews", False)
                    ),
                    "required_approving_review_count": max(
                        1,
                        int(policy.get("required_approving_review_count") or 1),
                    ),
                },
                "restrictions": restrictions,
                "required_conversation_resolution": True,
                "allow_force_pushes": False,
                "allow_deletions": False,
                "block_creations": False,
            }
            protection = await client.update_branch_protection(
                repo_owner,
                repo_name,
                branch=branch,
                payload=protection_payload,
            )
            governance = {
                "managed_repo": True,
                "broker_only": True,
                "write_principal": "zetherion",
                "agent_push_enabled": False,
                "publish_flow": "publish_candidate_only",
                "branch_protection_required": True,
                "default_branch": branch,
                "applied": True,
                "applied_at": datetime.now(UTC).isoformat(),
                "restrictions": restrictions or {},
            }
            review = {
                "status": "applied",
                "protection": protection,
                "governance": governance,
            }
        except (GitHubAPIError, GitHubValidationError) as exc:
            governance = {
                "managed_repo": True,
                "broker_only": True,
                "write_principal": "zetherion",
                "agent_push_enabled": False,
                "publish_flow": "publish_candidate_only",
                "branch_protection_required": True,
                "default_branch": branch or default_branch,
                "applied": False,
                "error": str(exc),
                "attempted_at": datetime.now(UTC).isoformat(),
            }
            review = {
                "status": "failed",
                "error": str(exc),
                "governance": governance,
            }
        finally:
            await client.close()

        app = await self._storage.get_agent_app_profile(owner_id, app_id)
        if app is not None:
            profile = dict(app.get("profile") or {})
            profile["github_governance"] = governance
            github_repos = list(profile.get("github_repos") or [])
            if github_repo not in github_repos:
                github_repos.append(github_repo)
            profile["github_repos"] = github_repos
            await self._storage.upsert_agent_app_profile(
                owner_id,
                app_id=app_id,
                display_name=str(app.get("display_name") or app_id),
                profile=profile,
                active=bool(app.get("active", True)),
            )

        repo_profile = await self._storage.get_repo_profile(owner_id, app_id)
        if repo_profile is not None:
            metadata = dict(repo_profile.get("metadata") or {})
            metadata["github_governance"] = governance
            await self._storage.upsert_repo_profile(
                owner_id,
                {
                    **repo_profile,
                    "metadata": metadata,
                },
            )

        await self._storage.record_agent_audit_event(
            owner_id,
            principal_id=principal_id,
            app_id=app_id,
            service_kind="github",
            resource=github_repo,
            action="repo.governance.enforce",
            decision="allowed" if governance.get("applied") else "warning",
            audit=review,
        )
        return {
            "app_id": app_id,
            "github_repo": github_repo,
            "governance": governance,
            "review": review,
        }

    async def _apply_publish_candidate(
        self,
        *,
        owner_id: str,
        candidate_id: str,
        target_branch: str | None,
        principal_id: str | None,
    ) -> dict[str, Any]:
        candidate = await self._storage.get_publish_candidate(owner_id, candidate_id)
        if candidate is None:
            raise ValueError(f"Publish candidate `{candidate_id}` not found")
        repo = await self._resolve_repo_profile(owner_id, str(candidate["repo_id"]))
        connector = await self._require_github_connector(owner_id, _GITHUB_CONNECTOR_ID)
        repo_owner, repo_name = _split_github_repo(str(repo["github_repo"]))
        base_branch = str(repo.get("default_branch") or "main")
        branch_name = (
            target_branch
            or str((candidate.get("candidate") or {}).get("target_branch") or "").strip()
        )
        if not branch_name:
            branch_name = (
                f"zetherion/{_safe_branch_suffix(str(candidate['app_id']))}/"
                f"{_safe_branch_suffix(candidate_id[:12])}"
            )
        operation = await self._storage.find_managed_operation_by_ref(
            owner_id,
            ref_kind="publish_candidate_id",
            ref_value=candidate_id,
            app_id=str(candidate["app_id"]),
        )
        if operation is None:
            operation = await self._find_or_create_operation(
                owner_id=owner_id,
                app_id=str(candidate["app_id"]),
                repo_id=str(candidate["repo_id"]),
                refs={
                    "publish_candidate_id": candidate_id,
                    "git_sha": str(candidate["base_sha"]),
                    "branch": branch_name,
                },
                request_context={"operation_kind": "publish_candidate"},
            )
        operation_id = str(operation["operation_id"])
        review: dict[str, Any] = {
            "status": "applying",
            "started_at": datetime.now(UTC).isoformat(),
            "branch": branch_name,
            "base_branch": base_branch,
        }
        await self._storage.update_publish_candidate_review(
            owner_id,
            candidate_id=candidate_id,
            status="applying",
            review=review,
        )
        await self._storage.update_managed_operation(
            owner_id,
            operation_id=operation_id,
            lifecycle_stage="github_apply",
            status="active",
            summary={
                **dict(operation.get("summary") or {}),
                "publish_candidate_id": candidate_id,
                "branch": branch_name,
                "base_branch": base_branch,
            },
            metadata={
                **dict(operation.get("metadata") or {}),
                "candidate_id": candidate_id,
            },
        )
        await self._storage.upsert_operation_ref(
            owner_id,
            operation_id=operation_id,
            service_kind="github",
            ref_kind="branch",
            ref_value=branch_name,
            metadata={"source": "publish_candidate_apply"},
        )

        temp_dir = tempfile.TemporaryDirectory(prefix="zetherion-publish-")
        workspace = Path(temp_dir.name) / repo_name
        token = str(connector["secret_value"])
        client = GitHubClient(token=token)
        try:
            auth_args = ["-c", f"http.extraheader=Authorization: Bearer {token}"]
            remote_url = f"https://github.com/{repo_owner}/{repo_name}.git"
            git_env = {
                **os.environ,
                "GIT_TERMINAL_PROMPT": "0",
            }
            await self._run_command(["git", "init", str(workspace)], env=git_env)
            await self._run_command(
                ["git", "-C", str(workspace), "remote", "add", "origin", remote_url],
                env=git_env,
            )
            await self._run_command(
                [
                    "git",
                    *auth_args,
                    "-C",
                    str(workspace),
                    "fetch",
                    "--depth",
                    "200",
                    "origin",
                    base_branch,
                ],
                env=git_env,
            )
            await self._run_command(
                ["git", "-C", str(workspace), "checkout", "-B", branch_name, "FETCH_HEAD"],
                env=git_env,
            )
            base_sha = str(candidate["base_sha"])
            verify = await self._run_command(
                ["git", "-C", str(workspace), "rev-parse", "--verify", f"{base_sha}^{{commit}}"],
                env=git_env,
                check=False,
            )
            if verify["returncode"] != 0:
                await self._run_command(
                    ["git", *auth_args, "-C", str(workspace), "fetch", "origin", base_branch],
                    env=git_env,
                )
            await self._run_command(
                ["git", "-C", str(workspace), "checkout", base_sha],
                env=git_env,
            )
            await self._run_command(
                ["git", "-C", str(workspace), "switch", "-C", branch_name],
                env=git_env,
            )
            await self._apply_candidate_payload(
                workspace=workspace,
                candidate_payload=dict(candidate.get("candidate") or {}),
                env=git_env,
            )
            changed = await self._run_command(
                ["git", "-C", str(workspace), "status", "--porcelain"],
                env=git_env,
            )
            if not str(changed["stdout"]).strip():
                raise ValueError(
                    "Publish candidate did not produce any staged " "or working-tree changes"
                )

            validation = await self._run_fast_validation_lanes(repo=repo, workspace=workspace)
            review["validation"] = validation
            if validation.get("status") == "failed":
                validation_evidence = await self._storage.record_operation_evidence(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="github",
                    evidence_type="summary",
                    title="Publish candidate validation failure",
                    payload={"validation": validation, "candidate_id": candidate_id},
                    metadata={"stage": "fast_validation"},
                )
                await self._storage.record_operation_incident(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="github",
                    incident_type="publish_candidate_validation_failed",
                    severity="high",
                    blocking=True,
                    root_cause_summary=(
                        "Fast validation failed before the candidate could be " "pushed."
                    ),
                    recommended_fix=(
                        "Review the validation receipts and fix the failing "
                        "fast gates before retrying."
                    ),
                    evidence_refs=[str(validation_evidence["evidence_id"])],
                    metadata={"validation": validation},
                )
                await self._storage.update_managed_operation(
                    owner_id,
                    operation_id=operation_id,
                    lifecycle_stage="validation_failed",
                    status="failed",
                    summary={
                        **dict(operation.get("summary") or {}),
                        "validation": validation,
                    },
                    metadata={
                        **dict(operation.get("metadata") or {}),
                        "candidate_id": candidate_id,
                    },
                )
                review["status"] = "failed_validation"
                review["completed_at"] = datetime.now(UTC).isoformat()
                await self._storage.update_publish_candidate_review(
                    owner_id,
                    candidate_id=candidate_id,
                    status="failed_validation",
                    review=review,
                )
                return {
                    "candidate": await self._storage.get_publish_candidate(
                        owner_id,
                        candidate_id,
                    )
                }

            await self._run_command(
                ["git", "-C", str(workspace), "config", "user.name", "Zetherion Broker"],
                env=git_env,
            )
            await self._run_command(
                ["git", "-C", str(workspace), "config", "user.email", "zetherion-broker@local"],
                env=git_env,
            )
            commit_message = str(
                (candidate.get("candidate") or {}).get("summary")
                or f"Apply publish candidate {candidate_id[:12]}"
            ).strip()
            await self._run_command(
                ["git", "-C", str(workspace), "add", "-A"],
                env=git_env,
            )
            await self._run_command(
                ["git", "-C", str(workspace), "commit", "-m", commit_message],
                env=git_env,
            )
            head_sha_result = await self._run_command(
                ["git", "-C", str(workspace), "rev-parse", "HEAD"],
                env=git_env,
            )
            head_sha = str(head_sha_result.get("stdout") or "").strip() or None
            await self._run_command(
                [
                    "git",
                    *auth_args,
                    "-C",
                    str(workspace),
                    "push",
                    "--force-with-lease",
                    "origin",
                    f"HEAD:refs/heads/{branch_name}",
                ],
                env=git_env,
            )
            existing_pr = await client.find_open_pull_request(
                repo_owner,
                repo_name,
                head=branch_name,
                base=base_branch,
            )
            pr = existing_pr
            if pr is None:
                pr = await client.create_pull_request(
                    repo_owner,
                    repo_name,
                    title=commit_message,
                    head=branch_name,
                    base=base_branch,
                    body=str((candidate.get("candidate") or {}).get("intent") or ""),
                    draft=False,
                )
            review.update(
                {
                    "status": "github_pr_open",
                    "completed_at": datetime.now(UTC).isoformat(),
                    "pr": pr.to_dict(),
                }
            )
            pr_number = str(pr.to_dict().get("number") or "").strip() or None
            if pr_number:
                await self._storage.upsert_operation_ref(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="github",
                    ref_kind="pr_number",
                    ref_value=pr_number,
                    metadata={"source": "publish_candidate_apply"},
                )
            if head_sha:
                await self._storage.upsert_operation_ref(
                    owner_id,
                    operation_id=operation_id,
                    service_kind="github",
                    ref_kind="git_sha",
                    ref_value=head_sha,
                    metadata={"source": "publish_candidate_apply"},
                )
            apply_evidence = await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="github",
                evidence_type="summary",
                title="Publish candidate applied to GitHub",
                payload={
                    "candidate_id": candidate_id,
                    "branch": branch_name,
                    "base_branch": base_branch,
                    "validation": validation,
                    "pull_request": pr.to_dict(),
                    "head_sha": head_sha,
                },
                metadata={"stage": "github_pr_open"},
            )
            await self._storage.update_managed_operation(
                owner_id,
                operation_id=operation_id,
                lifecycle_stage="github_pr_open",
                status="succeeded",
                summary={
                    **dict(operation.get("summary") or {}),
                    "validation": validation,
                    "branch": branch_name,
                    "pull_request": pr.to_dict(),
                    "head_sha": head_sha,
                },
                metadata={
                    **dict(operation.get("metadata") or {}),
                    "latest_evidence_id": str(apply_evidence["evidence_id"]),
                    "candidate_id": candidate_id,
                },
            )
            updated_candidate = await self._storage.update_publish_candidate_review(
                owner_id,
                candidate_id=candidate_id,
                status="github_pr_open",
                review=review,
            )
            await self._storage.record_agent_audit_event(
                owner_id,
                principal_id=principal_id or str(candidate.get("principal_id") or ""),
                app_id=str(candidate["app_id"]),
                service_kind="github",
                resource=str(repo["github_repo"]),
                action="publish_candidate.apply",
                decision="allowed",
                audit={
                    "candidate_id": candidate_id,
                    "branch": branch_name,
                    "pull_request": pr.to_dict(),
                },
            )
            return {
                "candidate": updated_candidate,
                "pull_request": pr.to_dict(),
            }
        except Exception as exc:
            failure_evidence = await self._storage.record_operation_evidence(
                owner_id,
                operation_id=operation_id,
                service_kind="github",
                evidence_type="summary",
                title="Publish candidate apply failure",
                payload={"candidate_id": candidate_id, "error": str(exc)},
                metadata={"stage": "apply_failed"},
            )
            await self._storage.record_operation_incident(
                owner_id,
                operation_id=operation_id,
                service_kind="github",
                incident_type="publish_candidate_apply_failed",
                severity="high",
                blocking=True,
                root_cause_summary=f"Applying the publish candidate to GitHub failed: {exc}",
                recommended_fix=(
                    "Inspect the apply error and retry after fixing the "
                    "controlled workspace or GitHub write step."
                ),
                evidence_refs=[str(failure_evidence["evidence_id"])],
                metadata={"candidate_id": candidate_id},
            )
            await self._storage.update_managed_operation(
                owner_id,
                operation_id=operation_id,
                lifecycle_stage="apply_failed",
                status="failed",
                summary={
                    **dict(operation.get("summary") or {}),
                    "error": str(exc),
                    "branch": branch_name,
                },
                metadata={
                    **dict(operation.get("metadata") or {}),
                    "candidate_id": candidate_id,
                },
            )
            review.update(
                {
                    "status": "failed",
                    "completed_at": datetime.now(UTC).isoformat(),
                    "error": str(exc),
                }
            )
            updated_candidate = await self._storage.update_publish_candidate_review(
                owner_id,
                candidate_id=candidate_id,
                status="failed",
                review=review,
            )
            await self._storage.record_agent_audit_event(
                owner_id,
                principal_id=principal_id or str(candidate.get("principal_id") or ""),
                app_id=str(candidate["app_id"]),
                service_kind="github",
                resource=str(repo["github_repo"]),
                action="publish_candidate.apply",
                decision="blocked",
                audit={
                    "candidate_id": candidate_id,
                    "error": str(exc),
                },
            )
            return {"candidate": updated_candidate, "error": str(exc)}
        finally:
            await client.close()
            temp_dir.cleanup()

    async def _apply_candidate_payload(
        self,
        *,
        workspace: Path,
        candidate_payload: dict[str, Any],
        env: dict[str, str],
    ) -> None:
        diff_text = str(candidate_payload.get("diff_text") or "").strip()
        patch_bundle = str(candidate_payload.get("patch_bundle_base64") or "").strip()
        if diff_text:
            patch_file = workspace / ".zetherion-publish.patch"
            patch_file.write_text(diff_text, encoding="utf-8")
            result = await self._run_command(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "apply",
                    "--index",
                    "--3way",
                    str(patch_file),
                ],
                env=env,
                check=False,
            )
            if result["returncode"] != 0:
                await self._run_command(
                    ["git", "-C", str(workspace), "apply", "--index", str(patch_file)],
                    env=env,
                )
            return
        if not patch_bundle:
            raise ValueError("Publish candidate is missing diff_text and patch_bundle_base64")
        archive_bytes = base64.b64decode(patch_bundle)
        overlay_dir = workspace / ".zetherion-overlay"
        overlay_dir.mkdir(parents=True, exist_ok=True)
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:*") as archive:
            self._extract_tar_safely(archive, overlay_dir)
        patch_candidates = [
            candidate
            for candidate in [
                overlay_dir / "patch.diff",
                overlay_dir / "diff.patch",
            ]
            if candidate.exists()
        ]
        if patch_candidates:
            await self._run_command(
                [
                    "git",
                    "-C",
                    str(workspace),
                    "apply",
                    "--index",
                    "--3way",
                    str(patch_candidates[0]),
                ],
                env=env,
            )
            return
        extracted_roots = sorted(child for child in overlay_dir.iterdir())
        for root in extracted_roots:
            if root.name.startswith(".zetherion-"):
                continue
            if root.is_dir():
                for path in sorted(root.rglob("*")):
                    if path.is_dir():
                        continue
                    relative = path.relative_to(root)
                    destination = workspace / relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(path, destination)
            else:
                destination = workspace / root.name
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(root, destination)

    async def _run_fast_validation_lanes(
        self,
        *,
        repo: dict[str, Any],
        workspace: Path,
    ) -> dict[str, Any]:
        lanes = [
            lane
            for lane in [
                *list(repo.get("mandatory_static_gates") or []),
                *list(repo.get("local_fast_lanes") or []),
            ]
            if isinstance(lane, dict)
        ]
        receipts: list[dict[str, Any]] = []
        failed = False
        for lane in lanes:
            command = [str(part) for part in list(lane.get("command") or []) if str(part).strip()]
            lane_id = str(lane.get("lane_id") or "lane")
            if not command:
                receipts.append(
                    {
                        "lane_id": lane_id,
                        "status": "skipped",
                        "reason": "empty_command",
                    }
                )
                continue
            if shutil.which(command[0]) is None:
                receipts.append(
                    {
                        "lane_id": lane_id,
                        "status": "skipped",
                        "reason": f"command_not_available:{command[0]}",
                        "command": command,
                    }
                )
                continue
            result = await self._run_command(command, cwd=workspace)
            receipt = {
                "lane_id": lane_id,
                "status": "passed" if result["returncode"] == 0 else "failed",
                "command": command,
                "stdout": result["stdout"],
                "stderr": result["stderr"],
                "returncode": result["returncode"],
            }
            receipts.append(receipt)
            if result["returncode"] != 0:
                failed = True
                break
        return {
            "status": "failed" if failed else "passed",
            "receipts": receipts,
        }

    async def _run_command(
        self,
        command: list[str],
        *,
        cwd: Path | None = None,
        env: dict[str, str] | None = None,
        check: bool = True,
    ) -> dict[str, Any]:
        process = await asyncio.create_subprocess_exec(
            *command,
            cwd=str(cwd) if cwd else None,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout_bytes, stderr_bytes = await process.communicate()
        stdout = stdout_bytes.decode("utf-8", errors="replace")
        stderr = stderr_bytes.decode("utf-8", errors="replace")
        result = {
            "command": command,
            "returncode": int(process.returncode or 0),
            "stdout": stdout,
            "stderr": stderr,
        }
        if check and process.returncode != 0:
            raise ValueError(
                "Command failed "
                f"({process.returncode}): {' '.join(command[:4])}\n"
                f"{stderr.strip() or stdout.strip()}"
            )
        return result

    def _merge_mapping(
        self,
        base: dict[str, Any],
        overrides: dict[str, Any],
    ) -> dict[str, Any]:
        merged = dict(base)
        for key, value in overrides.items():
            if isinstance(value, dict) and isinstance(merged.get(key), dict):
                merged[key] = self._merge_mapping(dict(merged[key]), value)
            else:
                merged[key] = value
        return merged

    def _extract_tar_safely(self, archive: tarfile.TarFile, destination: Path) -> None:
        root = destination.resolve()
        for member in archive.getmembers():
            if member.issym() or member.islnk():
                raise ValueError(f"Archive member uses an unsupported link: {member.name}")
            member_path = (root / member.name).resolve()
            if member_path != root and root not in member_path.parents:
                raise ValueError(f"Archive member escapes destination: {member.name}")
            if member.isdir():
                member_path.mkdir(parents=True, exist_ok=True)
                continue
            if not member.isfile():
                raise ValueError(f"Archive member type is not supported: {member.name}")
            member_path.parent.mkdir(parents=True, exist_ok=True)
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError(f"Archive member could not be read: {member.name}")
            with extracted, member_path.open("wb") as destination_file:
                shutil.copyfileobj(extracted, destination_file)
            if member.mode:
                member_path.chmod(member.mode)
