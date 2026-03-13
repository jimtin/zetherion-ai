"""Focused unit coverage for agent bootstrap helper functions."""

from __future__ import annotations

import base64
import io
import tarfile
from pathlib import Path

import pytest

import zetherion_ai.skills.agent_bootstrap as agent_bootstrap
from zetherion_ai.skills.base import SkillRequest


def test_identifier_and_request_normalizers_cover_fallbacks() -> None:
    assert agent_bootstrap._slugify_repo_id("  Catalyst Group Solutions  ") == (
        "catalyst-group-solutions"
    )
    assert agent_bootstrap._slugify_repo_id("!!!") == "managed-repo"
    assert agent_bootstrap._safe_branch_suffix(" Feature Branch ") == "feature-branch"
    assert agent_bootstrap._safe_branch_suffix("   ") == "managed-change"
    assert agent_bootstrap._system_safe_principal_id("system:watchdog") is None
    assert agent_bootstrap._system_safe_principal_id(" codex-1 ") == "codex-1"

    request = SkillRequest(
        user_id="user-1",
        context={
            "operator_id": "owner-2",
            "agent_principal_id": "codex-9",
        },
    )
    assert agent_bootstrap._normalize_owner_id(request) == "owner-2"
    assert agent_bootstrap._normalize_principal_id(request) == "codex-9"
    assert agent_bootstrap._normalize_owner_id(SkillRequest()) == "owner"
    assert agent_bootstrap._normalize_principal_id(SkillRequest()) == "agent"


def test_split_github_repo_rejects_invalid_shapes() -> None:
    assert agent_bootstrap._split_github_repo("jimtin/zetherion-ai") == (
        "jimtin",
        "zetherion-ai",
    )
    with pytest.raises(ValueError, match="github_repo"):
        agent_bootstrap._split_github_repo("zetherion-ai")
    with pytest.raises(ValueError, match="github_repo"):
        agent_bootstrap._split_github_repo("/jimtin/")


def test_doc_manifest_helpers_read_markdown_and_missing_files(tmp_path: Path) -> None:
    doc_path = tmp_path / "quickstart.md"
    doc_path.write_text("# Quickstart\n\n## Install\n\nRun it.\n", encoding="utf-8")

    manifest = agent_bootstrap._doc_manifest(
        {
            "slug": "quickstart",
            "title": "Quickstart",
            "path": "/docs/quickstart",
            "category": "guide",
            "source_path": doc_path,
        },
        "https://example.com/",
    )

    assert manifest["url"] == "https://example.com/docs/quickstart"
    assert manifest["content_markdown"].startswith("# Quickstart")
    assert manifest["headings"] == ["Quickstart", "Install"]
    assert manifest["source_path"] == str(doc_path)
    assert agent_bootstrap._read_text(tmp_path / "missing.md") is None
    assert agent_bootstrap._markdown_headings(None) == []


def test_git_helpers_resolve_head_refs_worktrees_and_packed_refs(tmp_path: Path) -> None:
    repo_root = tmp_path / "repo"
    git_dir = repo_root / ".git"
    (git_dir / "refs" / "heads").mkdir(parents=True)
    repo_root.mkdir(exist_ok=True)
    branch_sha = "0123456789abcdef0123456789abcdef01234567"
    tag_sha = "fedcba9876543210fedcba9876543210fedcba98"
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text(f"{branch_sha}\n", encoding="utf-8")
    (git_dir / "packed-refs").write_text(
        f"# pack-refs\n{tag_sha} refs/tags/v1.0.0\n",
        encoding="utf-8",
    )

    assert agent_bootstrap._resolve_git_dir(repo_root) == git_dir
    assert agent_bootstrap._resolve_git_ref(repo_root, "HEAD") == branch_sha
    assert agent_bootstrap._resolve_git_ref(repo_root, "main") == branch_sha
    assert agent_bootstrap._resolve_git_ref(repo_root, "v1.0.0") == tag_sha
    assert agent_bootstrap._resolve_git_ref(repo_root, branch_sha) == branch_sha
    assert agent_bootstrap._resolve_git_ref(repo_root, "missing-ref") is None

    worktree_root = tmp_path / "worktree"
    actual_git_dir = tmp_path / "actual-git"
    actual_git_dir.mkdir()
    worktree_root.mkdir()
    (worktree_root / ".git").write_text("gitdir: ../actual-git\n", encoding="utf-8")
    assert agent_bootstrap._resolve_git_dir(worktree_root) == actual_git_dir


def test_tar_workspace_and_command_catalog_skip_generated_content(tmp_path: Path) -> None:
    repo_root = tmp_path / "sample-repo"
    (repo_root / "src").mkdir(parents=True)
    (repo_root / ".git").mkdir()
    (repo_root / "node_modules").mkdir()
    (repo_root / "build").mkdir()
    (repo_root / "src" / "main.py").write_text("print('ok')\n", encoding="utf-8")
    (repo_root / "README.md").write_text("# Sample\n", encoding="utf-8")
    (repo_root / ".git" / "config").write_text("[core]\n", encoding="utf-8")
    (repo_root / "node_modules" / "dep.js").write_text("ignored\n", encoding="utf-8")
    (repo_root / "build" / "out.txt").write_text("ignored\n", encoding="utf-8")
    (repo_root / "symlink-target.txt").write_text("ignored\n", encoding="utf-8")
    (repo_root / "src" / "link").symlink_to(repo_root / "symlink-target.txt")

    archive_bytes, file_count = agent_bootstrap._tar_workspace(repo_root)

    assert file_count == 3
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        names = sorted(member.name for member in archive.getmembers())
    assert names == [
        "sample-repo/README.md",
        "sample-repo/src/main.py",
        "sample-repo/symlink-target.txt",
    ]

    repo = {
        "mandatory_static_gates": [{"command": ["ruff", "check"]}],
        "local_fast_lanes": [{"command": ["pytest", "-q"]}, "skip-me"],
        "local_full_lanes": [{"command": ["pytest", "tests/integration"]}],
        "windows_full_lanes": [{"command": ["docker", "compose", "up"]}],
    }
    commands = agent_bootstrap._collect_commands(repo)
    assert commands == {
        "mandatory_static_gates": [["ruff", "check"]],
        "local_fast": [["pytest", "-q"]],
        "local_full": [["pytest", "tests/integration"]],
        "windows_full": [["docker", "compose", "up"]],
    }


def test_default_connector_maps_manifests_and_capabilities_cover_repo_variants() -> None:
    cgs_repo = {
        "repo_id": "catalyst-group-solutions",
        "github_repo": "jimtin/catalyst-group-solutions",
        "default_branch": "main",
        "stack_kind": "nextjs",
        "allowed_paths": ["/tmp/cgs"],
        "mandatory_static_gates": [{"command": ["eslint"]}],
        "local_fast_lanes": [{"command": ["vitest"]}],
        "local_full_lanes": [{"command": ["playwright"]}],
        "windows_full_lanes": [],
        "windows_execution_mode": "command",
        "agent_bootstrap_profile": {"docs_slugs": ["cgs-ai-api-quickstart"]},
        "resource_classes": {"cpu": {"max_parallel": 8}},
        "certification_requirements": ["cgs_auth_flow_passed"],
        "scheduled_canaries": [{"lane_id": "c-e2e-browser"}],
    }
    zetherion_repo = {
        "repo_id": "zetherion-ai",
        "github_repo": "jimtin/zetherion-ai",
        "default_branch": "main",
        "stack_kind": "python",
        "allowed_paths": ["/tmp/zetherion"],
        "mandatory_static_gates": [{"command": ["ruff"]}],
        "local_fast_lanes": [{"command": ["pytest", "tests/unit"]}],
        "local_full_lanes": [{"command": ["pytest", "tests/integration"]}],
        "windows_full_lanes": [{"command": ["docker", "compose", "run"]}],
        "windows_execution_mode": "docker_only",
        "agent_bootstrap_profile": {"docs_slugs": ["zetherion-docs-index"]},
        "resource_classes": {"service": {"max_parallel": 2}},
        "certification_requirements": ["discord_roundtrip"],
        "scheduled_canaries": [{"lane_id": "z-e2e-discord-real"}],
    }
    generic_repo = {
        "repo_id": "generic-repo",
        "github_repo": "jimtin/generic-repo",
        "default_branch": "main",
        "stack_kind": "generic",
        "allowed_paths": ["/tmp/generic"],
    }

    cgs_connectors = agent_bootstrap._default_service_connector_map("catalyst-group-solutions")
    z_connectors = agent_bootstrap._default_service_connector_map("zetherion-ai")
    generic_connectors = agent_bootstrap._default_service_connector_map("generic-repo")
    assert sorted(cgs_connectors) == ["clerk", "github", "stripe", "vercel"]
    assert sorted(z_connectors) == ["clerk", "discord", "github", "vercel"]
    assert sorted(generic_connectors) == ["github"]

    assert agent_bootstrap._default_mock_profiles("catalyst-group-solutions")[0]["profile_id"] == (
        "cgs-zetherion-boundary"
    )
    assert agent_bootstrap._default_mock_profiles("zetherion-ai")[1]["profile_id"] == (
        "zetherion-discord-required-receipt"
    )
    assert agent_bootstrap._default_mock_profiles("other")[0]["profile_id"] == (
        "generic-fast-feedback"
    )

    cgs_workspace = agent_bootstrap._default_workspace_manifest(cgs_repo)
    z_workspace = agent_bootstrap._default_workspace_manifest(zetherion_repo)
    generic_workspace = agent_bootstrap._default_workspace_manifest(generic_repo)
    assert cgs_workspace["package_manager"] == "yarn"
    assert z_workspace["package_manager"] == "pip"
    assert generic_workspace["package_manager"] == "custom"
    assert z_workspace["docker_only_windows"] is True
    assert cgs_workspace["local_fast_commands"] == [["vitest"]]
    assert z_workspace["windows_full_commands"] == [["docker", "compose", "run"]]

    harness_manifest = agent_bootstrap._default_test_harness_manifest(zetherion_repo)
    command_catalog = agent_bootstrap._default_command_catalog(cgs_repo)
    service_ops = agent_bootstrap._default_service_operations("zetherion-ai")
    capability_registry = agent_bootstrap._default_capability_registry(zetherion_repo)
    adapter_capabilities = agent_bootstrap._default_service_adapter_capabilities()

    assert harness_manifest["resource_classes"] == {"service": {"max_parallel": 2}}
    assert command_catalog["mandatory_static_gates"] == [["eslint"]]
    assert "discord" in service_ops
    assert capability_registry["supported_tooling"] == [
        "discord_e2e",
        "docker",
        "pytest",
        "ruff",
    ]
    assert capability_registry["required_docs"] == ["zetherion-docs-index"]
    assert "deployment_artifacts" in adapter_capabilities["vercel"]["known_unsupported"]


def test_misc_bootstrap_helpers_cover_routing_redaction_and_stable_keys() -> None:
    assert agent_bootstrap._normalize_limit("9", default=2, maximum=5) == 5
    assert agent_bootstrap._normalize_limit("0", default=2, maximum=5) == 1
    assert agent_bootstrap._normalize_limit("nope", default=3, maximum=5) == 3

    assert agent_bootstrap._service_routes_for(
        "zetherion-ai",
        base_url="https://cgs.example.com",
    )["service"].endswith("/services/:serviceKind")
    assert agent_bootstrap._service_routes_for("zetherion-ai", base_url="")["catalog"].startswith(
        "/service/ai/v1/agent/apps/zetherion-ai/services"
    )
    assert agent_bootstrap._operation_routes(
        base_url="https://cgs.example.com",
        app_id="catalyst-group-solutions",
    )["resolve"].endswith("/apps/catalyst-group-solutions/operations/resolve")
    assert "resolve" not in agent_bootstrap._operation_routes(base_url="", app_id=None)

    payload = {
        "secret_value": "super-secret",
        "grant_key": "safe",
        "nested": {
            "Authorization": "Bearer abc",
            "public_key": "visible",
        },
        "list": [{"token": "hidden"}],
        "tuple": ("ok", {"password": "hidden"}),
        "long": "x" * 5001,
    }
    redacted = agent_bootstrap._redact_payload(payload)
    assert redacted["secret_value"] == "***redacted***"
    assert redacted["grant_key"] == "safe"
    assert redacted["nested"]["Authorization"] == "***redacted***"
    assert redacted["nested"]["public_key"] == "visible"
    assert redacted["list"][0]["token"] == "***redacted***"
    assert redacted["tuple"][1]["password"] == "***redacted***"
    assert str(redacted["long"]).endswith("…")

    assert agent_bootstrap._pick_fields({"a": 1, "b": 2}, ["b", "c"]) == {"b": 2}
    assert agent_bootstrap._derive_clerk_jwks_url({"jwks_url": "https://jwks.example.com"}) == (
        "https://jwks.example.com"
    )
    assert agent_bootstrap._derive_clerk_jwks_url({"issuer": "https://issuer.example.com/"}) == (
        "https://issuer.example.com/.well-known/jwks.json"
    )
    assert agent_bootstrap._derive_clerk_jwks_url(
        {"frontend_api_url": "https://frontend.example.com/"}
    ) == "https://frontend.example.com/.well-known/jwks.json"
    assert agent_bootstrap._derive_clerk_jwks_url({}) is None
    assert agent_bootstrap._normalize_route_path(" /admin/ai ") == "/admin/ai"
    assert agent_bootstrap._normalize_route_path("") is None
    assert agent_bootstrap._normalize_session_id(" sess-1 ") == "sess-1"
    assert agent_bootstrap._normalize_session_id(None) is None
    assert agent_bootstrap._compact_text_payload({"prompt": "hello", "summary": "ignored"}) == (
        "hello"
    )
    assert agent_bootstrap._compact_text_payload({}) is None
    assert agent_bootstrap._stable_gap_key(["Repo", "DM", None]) == agent_bootstrap._stable_gap_key(
        ["repo", "dm", ""]
    )


def test_workspace_bundle_builder_keeps_inline_archives_small(tmp_path: Path) -> None:
    repo_root = tmp_path / "workspace"
    repo_root.mkdir()
    (repo_root / "README.md").write_text("# Workspace\n", encoding="utf-8")

    skill = agent_bootstrap.AgentBootstrapSkill(storage=object())  # type: ignore[arg-type]
    bundle, resolved_ref = skill._build_workspace_bundle(  # noqa: SLF001
        repo={"repo_id": "workspace", "allowed_paths": [str(repo_root)]},
        knowledge_pack={"workspace_manifest": {"repo_id": "workspace"}},
        git_ref="HEAD",
    )

    assert resolved_ref in {None, "HEAD"}
    assert bundle["download_mode"] == "inline_base64"
    archive_bytes = base64.b64decode(bundle["archive_base64"])
    with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
        assert archive.getnames() == ["workspace/README.md"]
