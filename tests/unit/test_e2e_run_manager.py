"""Regression tests for isolated Docker-backed E2E run management."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "e2e_run_manager.py"
    spec = importlib.util.spec_from_file_location("e2e_run_manager_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


PORTS = {
    "E2E_API_HOST_PORT": 28001,
    "E2E_CGS_GATEWAY_HOST_PORT": 28002,
    "E2E_SKILLS_HOST_PORT": 28003,
    "E2E_WHATSAPP_BRIDGE_HOST_PORT": 28004,
    "E2E_OLLAMA_ROUTER_HOST_PORT": 28005,
    "E2E_OLLAMA_HOST_PORT": 28006,
    "E2E_POSTGRES_HOST_PORT": 28007,
    "E2E_QDRANT_HOST_PORT": 28008,
}


def test_create_run_writes_manifest_and_exports(tmp_path, monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "make_run_id", lambda prefix="run": "run-fixed")
    monkeypatch.setattr(module, "allocate_port_map", lambda service_slot=None: dict(PORTS))
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("DISCORD_TOKEN_TEST", raising=False)
    monkeypatch.delenv("DISCORD_TOKEN", raising=False)
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("ENCRYPTION_PASSPHRASE", raising=False)
    monkeypatch.delenv("E2E_STACK_STORAGE_ROOT", raising=False)

    compose_file = tmp_path / "docker-compose.test.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    manifest, exports = module.create_run(
        runs_root=tmp_path / "runs",
        compose_file=compose_file,
        project_prefix="zetherion-ai-test",
        ttl_minutes=90,
        service_slot="slot_a",
    )

    assert manifest["run_id"] == "run-fixed"
    assert manifest["version"] == 2
    assert manifest["compose_project"] == "zetherion-ai-test-run-fixed"
    assert manifest["ports"] == PORTS
    assert manifest["service_slot"] == "slot_a"
    assert Path(manifest["stack_root"]).is_dir()
    assert Path(manifest["env_file"]).is_file()
    assert manifest["cleanup"]["receipt_path"] == str(Path(manifest["resources"]["manifest_path"]))
    assert manifest["resources"]["containers"]["classification"] == "ephemeral"
    assert manifest["resources"]["networks"]["classification"] == "ephemeral"
    assert (
        manifest["resources"]["docker_label_filters"]["compose_project"]
        == "com.docker.compose.project=zetherion-ai-test-run-fixed"
    )
    assert (
        manifest["resources"]["volumes"]["ephemeral_names"]
        == [
            "zetherion-ai-test-run-fixed_postgres_data_test",
            "zetherion-ai-test-run-fixed_qdrant_storage_test",
            "zetherion-ai-test-run-fixed_ollama_models_test",
            "zetherion-ai-test-run-fixed_ollama_router_models_test",
        ]
    )
    assert manifest["resources"]["volumes"]["persistent_runtime"] == [
        "zetherionai_postgres_data",
        "zetherionai_qdrant_storage",
    ]
    assert manifest["resources"]["volumes"]["forbidden_in_prod"] == [
        "zetherionai_ollama_models",
        "zetherionai_ollama_router_models",
    ]
    assert (
        manifest["resources"]["images"]["reference_patterns"]
        == ["zetherion-ai-test-run-fixed-*"]
    )
    assert manifest["resources"]["images"]["stale_test_retention_hours"] == 6
    assert manifest["resources"]["artifacts"]["stale_manifest_threshold_hours"] == 2
    assert exports["E2E_PROJECT_NAME"] == "zetherion-ai-test-run-fixed"
    assert exports["E2E_QDRANT_HOST_PORT"] == str(PORTS["E2E_QDRANT_HOST_PORT"])
    assert exports["E2E_SERVICE_SLOT"] == "slot_a"
    assert exports["ZETHERION_ENV_FILE"] == str(Path(manifest["env_file"]))
    assert exports["OPENAI_API_KEY"] == ""
    assert exports["DISCORD_TOKEN_TEST"] == "test-discord-token"
    assert exports["DISCORD_TOKEN"] == "test-discord-token"
    assert exports["GEMINI_API_KEY"] == "test-gemini-api-key"
    assert exports["ENCRYPTION_PASSPHRASE"] == "test-encryption-passphrase"

    env_text = Path(manifest["env_file"]).read_text(encoding="utf-8")
    assert "OPENAI_API_KEY=" in env_text
    assert "DISCORD_TOKEN_TEST=test-discord-token" in env_text
    assert "DISCORD_TOKEN=test-discord-token" in env_text
    assert "EMBEDDINGS_BACKEND=openai" in env_text
    assert "GEMINI_API_KEY=test-gemini-api-key" in env_text
    assert "ENCRYPTION_PASSPHRASE=test-encryption-passphrase" in env_text
    assert "E2E_RUN_ID=" not in env_text


def test_create_run_uses_repo_env_fallbacks_when_process_env_is_missing(tmp_path, monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "make_run_id", lambda prefix="run": "run-fixed")
    monkeypatch.setattr(module, "allocate_port_map", lambda service_slot=None: dict(PORTS))
    for key in (
        "OPENAI_API_KEY",
        "GROQ_API_KEY",
        "DISCORD_TOKEN_TEST",
        "DISCORD_TOKEN",
        "DISCORD_E2E_ENABLED",
        "DISCORD_E2E_ALLOWED_AUTHOR_IDS",
        "DISCORD_E2E_GUILD_ID",
        "DISCORD_E2E_CATEGORY_ID",
        "DISCORD_E2E_CHANNEL_PREFIX",
        "EMBEDDINGS_BACKEND",
        "ENCRYPTION_PASSPHRASE",
        "GEMINI_API_KEY",
        "ZETHERION_SOURCE_ENV_FILE",
    ):
        monkeypatch.delenv(key, raising=False)

    compose_dir = tmp_path / "compose-root"
    compose_dir.mkdir()
    compose_file = compose_dir / "docker-compose.test.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")
    (compose_dir / ".env").write_text(
        "\n".join(
            (
                "OPENAI_API_KEY=file-openai",
                "GROQ_API_KEY=file-groq",
                "DISCORD_TOKEN_TEST=file-test-token",
                "DISCORD_E2E_ENABLED=true",
                "DISCORD_E2E_ALLOWED_AUTHOR_IDS=123,456",
                "DISCORD_E2E_GUILD_ID=file-guild",
                "DISCORD_E2E_CATEGORY_ID=file-category",
                "DISCORD_E2E_CHANNEL_PREFIX=file-prefix",
                "TEST_DISCORD_BOT_TOKEN=file-bot-token",
                "TEST_DISCORD_GUILD_ID=file-test-guild",
                "TEST_DISCORD_E2E_CATEGORY_ID=file-test-category-id",
                "TEST_DISCORD_E2E_CATEGORY_NAME=file-test-category-name",
                "TEST_DISCORD_TARGET_BOT_ID=file-target-bot",
                "EMBEDDINGS_BACKEND=ollama",
                "ENCRYPTION_PASSPHRASE=file-passphrase",
                "GEMINI_API_KEY=file-gemini",
            )
        )
        + "\n",
        encoding="utf-8",
    )

    manifest, exports = module.create_run(
        runs_root=tmp_path / "runs",
        compose_file=compose_file,
        project_prefix="zetherion-ai-test",
        ttl_minutes=90,
        service_slot="slot_a",
    )

    assert exports["OPENAI_API_KEY"] == "file-openai"
    assert exports["GROQ_API_KEY"] == "file-groq"
    assert exports["DISCORD_TOKEN_TEST"] == "file-test-token"
    assert exports["DISCORD_TOKEN"] == "file-test-token"
    assert exports["DISCORD_E2E_ENABLED"] == "true"
    assert exports["DISCORD_E2E_ALLOWED_AUTHOR_IDS"] == "123,456"
    assert exports["DISCORD_E2E_GUILD_ID"] == "file-guild"
    assert exports["DISCORD_E2E_CATEGORY_ID"] == "file-category"
    assert exports["DISCORD_E2E_CHANNEL_PREFIX"] == "file-prefix"
    assert exports["TEST_DISCORD_BOT_TOKEN"] == "file-bot-token"
    assert exports["TEST_DISCORD_GUILD_ID"] == "file-test-guild"
    assert exports["TEST_DISCORD_E2E_CATEGORY_ID"] == "file-test-category-id"
    assert exports["TEST_DISCORD_E2E_CATEGORY_NAME"] == "file-test-category-name"
    assert exports["TEST_DISCORD_TARGET_BOT_ID"] == "file-target-bot"
    assert exports["EMBEDDINGS_BACKEND"] == "ollama"
    assert exports["ENCRYPTION_PASSPHRASE"] == "file-passphrase"
    assert exports["GEMINI_API_KEY"] == "file-gemini"

    env_text = Path(manifest["env_file"]).read_text(encoding="utf-8")
    assert "GROQ_API_KEY=file-groq" in env_text
    assert "DISCORD_E2E_ALLOWED_AUTHOR_IDS=123,456" in env_text
    assert "TEST_DISCORD_BOT_TOKEN=file-bot-token" in env_text


def test_render_shell_exports_normalizes_windows_paths_for_shell_use() -> None:
    module = _load_module()

    rendered = module.render_shell_exports(
        {
            "COMPOSE_FILE": r"C:\ZetherionAI-cutover\docker-compose.test.yml",
            "E2E_STACK_ROOT": r"\mnt\wsl\docker-desktop-bind-mounts\Ubuntu\stack-root",
            "PROJECT": "zetherion-ai-test-run-fixed",
        }
    )

    assert "export COMPOSE_FILE=C:/ZetherionAI-cutover/docker-compose.test.yml" in rendered
    assert (
        "export E2E_STACK_ROOT=/mnt/wsl/docker-desktop-bind-mounts/Ubuntu/stack-root"
        in rendered
    )
    assert "export PROJECT=zetherion-ai-test-run-fixed" in rendered


def test_allocate_port_map_uses_slot_offsets() -> None:
    module = _load_module()

    slot_b_ports = module.allocate_port_map(service_slot="slot_b")

    assert slot_b_ports["E2E_API_HOST_PORT"] == 29443
    assert slot_b_ports["E2E_POSTGRES_HOST_PORT"] == 16432


def test_create_run_uses_linux_stack_root_for_wsl_windows_mounts(tmp_path, monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "make_run_id", lambda prefix="run": "run-fixed")
    monkeypatch.setattr(module, "allocate_port_map", lambda service_slot=None: dict(PORTS))
    monkeypatch.setattr(module, "_path_uses_windows_mount", lambda path: True)
    monkeypatch.delenv("E2E_STACK_STORAGE_ROOT", raising=False)
    monkeypatch.setenv("WSL_DISTRO_NAME", "Ubuntu")
    linux_tmp = tmp_path / "linux-tmp"
    monkeypatch.setenv("TMPDIR", str(linux_tmp))

    compose_file = tmp_path / "docker-compose.test.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    manifest, _exports = module.create_run(
        runs_root=tmp_path / "runs",
        compose_file=compose_file,
        project_prefix="zetherion-ai-test",
        ttl_minutes=90,
        service_slot="slot_a",
    )

    stack_root = Path(manifest["stack_root"])
    assert stack_root == linux_tmp / "zetherion-e2e-runs" / "stacks" / "run-fixed"
    assert stack_root.is_dir()
    assert (stack_root / "data").is_dir()
    assert (stack_root / "logs").is_dir()
    assert Path(manifest["env_file"]).is_file()
    assert Path(manifest["env_file"]).parent == stack_root
    assert Path(manifest["stack_root"]).stat().st_mode & 0o002
    assert Path(manifest["artifacts"]["data_root"]).stat().st_mode & 0o002
    assert Path(manifest["artifacts"]["logs_root"]).stat().st_mode & 0o002


def test_create_run_uses_existing_override_stack_storage_root(tmp_path, monkeypatch) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "make_run_id", lambda prefix="run": "run-fixed")
    monkeypatch.setattr(module, "allocate_port_map", lambda service_slot=None: dict(PORTS))
    override_root = tmp_path / "existing-root" / "stacks"
    override_root.mkdir(parents=True)
    monkeypatch.setenv("E2E_STACK_STORAGE_ROOT", str(override_root))

    compose_file = tmp_path / "docker-compose.test.yml"
    compose_file.write_text("services: {}\n", encoding="utf-8")

    manifest, _exports = module.create_run(
        runs_root=tmp_path / "runs",
        compose_file=compose_file,
        project_prefix="zetherion-ai-test",
        ttl_minutes=90,
        service_slot="slot_a",
    )

    stack_root = Path(manifest["stack_root"])
    assert stack_root == override_root / "run-fixed"
    assert stack_root.is_dir()
    assert Path(manifest["env_file"]).parent == stack_root


def test_cleanup_run_updates_manifest_and_removes_stack_root(tmp_path, monkeypatch) -> None:
    module = _load_module()
    stack_root = tmp_path / "stack-root"
    stack_root.mkdir(parents=True)
    (stack_root / "data.txt").write_text("payload", encoding="utf-8")
    manifest_path = tmp_path / "manifests" / "run-fixed.json"
    manifest_path.parent.mkdir(parents=True)
    module.write_manifest(
        manifest_path,
        {
            "run_id": "run-fixed",
            "compose_project": "zetherion-ai-test-run-fixed",
            "compose_file": str(tmp_path / "docker-compose.test.yml"),
            "stack_root": str(stack_root),
            "lease": {"status": "active"},
            "cleanup": {"status": "pending"},
        },
    )

    monkeypatch.setattr(
        module,
        "cleanup_resources",
        lambda **_: {
            "compose_down": {"returncode": 0, "stdout": "", "stderr": ""},
            "containers_removed": [],
            "volumes_removed": [],
            "networks_removed": [],
            "images_removed": [],
            "errors": [],
        },
    )

    payload = module.cleanup_run(manifest_path=manifest_path, reason="unit_test")

    assert payload["cleanup"]["status"] == "cleaned"
    assert payload["cleanup"]["reason"] == "unit_test"
    assert not stack_root.exists()

    stored = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert stored["lease"]["status"] == "cleaned"


def test_cleanup_resources_removes_project_images(monkeypatch) -> None:
    module = _load_module()

    def fake_list_resource_ids(kind: str, label: str) -> list[str]:
        assert label == "com.docker.compose.project=proj-1"
        mapping = {
            "volume": ["volume-1"],
            "network": ["network-1"],
            "image": ["image-1", "image-2"],
        }
        return mapping.get(kind, [])

    commands: list[list[str]] = []

    def fake_run_command(command: list[str]):
        commands.append(command)

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        return Result()

    monkeypatch.setattr(module, "_list_resource_ids", fake_list_resource_ids)
    monkeypatch.setattr(module, "_list_container_ids", lambda project: ["container-1"])
    monkeypatch.setattr(module, "_run_command", fake_run_command)

    cleanup = module.cleanup_resources(compose_file="docker-compose.test.yml", project="proj-1")

    assert cleanup["containers_removed"] == ["container-1"]
    assert cleanup["volumes_removed"] == ["volume-1"]
    assert cleanup["networks_removed"] == ["network-1"]
    assert cleanup["images_removed"] == ["image-1", "image-2"]
    assert ["docker", "image", "rm", "-f", "image-1", "image-2"] in commands


def test_janitor_cleans_only_expired_runs(tmp_path, monkeypatch) -> None:
    module = _load_module()
    layout = module.build_layout(tmp_path)
    module.ensure_layout(layout)

    expired_manifest = layout.manifests_dir / "expired.json"
    active_manifest = layout.manifests_dir / "active.json"
    now = datetime.now(tz=timezone.utc)

    module.write_manifest(
        expired_manifest,
        {
            "run_id": "expired",
            "compose_project": "proj-expired",
            "compose_file": "docker-compose.test.yml",
            "stack_root": str(tmp_path / "expired-stack"),
            "lease": {
                "status": "active",
                "expires_at": (now - timedelta(minutes=5)).isoformat(),
            },
            "cleanup": {"status": "pending"},
        },
    )
    module.write_manifest(
        active_manifest,
        {
            "run_id": "active",
            "compose_project": "proj-active",
            "compose_file": "docker-compose.test.yml",
            "stack_root": str(tmp_path / "active-stack"),
            "lease": {
                "status": "active",
                "expires_at": (now + timedelta(minutes=30)).isoformat(),
            },
            "cleanup": {"status": "pending"},
        },
    )

    cleaned_manifests: list[str] = []

    def fake_cleanup_run(*, manifest_path: Path, reason: str, delete_stack_root: bool = True):
        cleaned_manifests.append(manifest_path.name)
        return {
            "run_id": manifest_path.stem,
            "cleanup": {"status": "cleaned", "reason": reason},
        }

    monkeypatch.setattr(module, "cleanup_run", fake_cleanup_run)

    result = module.janitor(runs_root=tmp_path)

    assert cleaned_manifests == ["expired.json"]
    assert result["cleaned"][0]["run_id"] == "expired"
    assert str(active_manifest) in result["skipped"]
