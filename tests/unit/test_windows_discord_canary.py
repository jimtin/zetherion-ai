"""Unit tests for the Windows Discord canary runner."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "windows" / "discord-canary.py"
    spec = importlib.util.spec_from_file_location("windows_discord_canary_test_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_build_child_env_prefers_canary_overrides_and_unsets_test_target(tmp_path: Path) -> None:
    module = _load_module()
    deploy_path = tmp_path / "deploy"
    deploy_path.mkdir()
    (deploy_path / ".env").write_text(
        "TEST_DISCORD_BOT_TOKEN=file-test-token\n"
        "TEST_DISCORD_GUILD_ID=file-guild\n"
        "DISCORD_E2E_ENABLED=true\n"
        "DISCORD_E2E_GUILD_ID=override-guild\n"
        "DISCORD_E2E_CHANNEL_PREFIX=canary-prefix\n"
        "DISCORD_E2E_ALLOWED_AUTHOR_IDS=1111\n"
        "DISCORD_TOKEN=prod-token\n"
        "OPENAI_API_KEY=file-openai\n"
        "GEMINI_API_KEY=file-gemini\n"
        "GROQ_API_KEY=file-groq\n"
        "DISCORD_TOKEN_TEST=should-not-survive\n",
        encoding="utf-8",
    )

    env = module.build_child_env(
        deploy_path,
        tmp_path / "result.json",
        base_env={
            "WINDOWS_DISCORD_CANARY_TEST_BOT_TOKEN": "override-test-token",
            "WINDOWS_DISCORD_CANARY_GUILD_ID": "override-guild",
            "WINDOWS_DISCORD_CANARY_CHANNEL_PREFIX": "canary-prefix",
            "WINDOWS_DISCORD_CANARY_TARGET_TOKEN": "live-target-token",
            "DISCORD_TOKEN_TEST": "stale-test-target",
            "TEST_DISCORD_TARGET_BOT_ID": "stale-explicit-target",
        },
    )

    assert env["TEST_DISCORD_BOT_TOKEN"] == "override-test-token"
    assert env["TEST_DISCORD_GUILD_ID"] == "override-guild"
    assert env["TEST_DISCORD_E2E_CHANNEL_PREFIX"] == "canary-prefix"
    assert env["DISCORD_TOKEN"] == "live-target-token"
    assert env["OPENAI_API_KEY"] == "file-openai"
    assert env["GEMINI_API_KEY"] == "file-gemini"
    assert env["GROQ_API_KEY"] == "file-groq"
    assert "DISCORD_TOKEN_TEST" not in env
    assert "TEST_DISCORD_TARGET_BOT_ID" not in env
    assert "TEST_DISCORD_E2E_PARENT_CHANNEL_ID" not in env
    assert env["DISCORD_E2E_MODE"] == "windows_prod_canary"


def test_build_child_env_rejects_runtime_channel_prefix_mismatch(tmp_path: Path) -> None:
    module = _load_module()
    deploy_path = tmp_path / "deploy"
    deploy_path.mkdir()
    (deploy_path / ".env").write_text(
        "DISCORD_E2E_ENABLED=true\n"
        "DISCORD_E2E_CHANNEL_PREFIX=zeth-e2e\n"
        "DISCORD_E2E_ALLOWED_AUTHOR_IDS=1111\n",
        encoding="utf-8",
    )

    with pytest.raises(RuntimeError, match="discord_canary_runtime_scope_mismatch"):
        module.build_child_env(
            deploy_path,
            tmp_path / "result.json",
            base_env={
                "WINDOWS_DISCORD_CANARY_CHANNEL_PREFIX": "zeth-canary",
                "DISCORD_E2E_ALLOWED_AUTHOR_IDS": "1111",
            },
        )


def test_build_child_env_exports_ssl_cert_file(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    deploy_path = tmp_path / "deploy"
    deploy_path.mkdir()
    (deploy_path / ".env").write_text(
        "DISCORD_E2E_ENABLED=true\n"
        "DISCORD_E2E_CHANNEL_PREFIX=zeth-e2e\n"
        "DISCORD_E2E_ALLOWED_AUTHOR_IDS=1111\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_resolve_python_ca_bundle", lambda: "/tmp/cacert.pem")
    monkeypatch.setattr(module, "_readable_file", lambda value: value == "/tmp/cacert.pem")

    env = module.build_child_env(deploy_path, tmp_path / "result.json")

    assert env["SSL_CERT_FILE"] == "/tmp/cacert.pem"


def test_resolve_bash_executable_prefers_git_bash_over_system_shim(monkeypatch) -> None:
    module = _load_module()
    system_bash = r"C:\Windows\System32\bash.exe"
    git_bash = r"C:\Program Files\Git\bin\bash.exe"

    monkeypatch.setattr(
        module.shutil, "which", lambda name: system_bash if name == "bash" else None
    )
    monkeypatch.setattr(module.Path, "exists", lambda self: str(self) in {system_bash, git_bash})

    resolved = module.resolve_bash_executable(base_env={})

    assert resolved == git_bash


def test_prepare_bash_wrapper_keeps_relative_repo_path_for_lf_script(tmp_path: Path) -> None:
    module = _load_module()
    deploy_path = tmp_path / "deploy"
    wrapper_path = deploy_path / "scripts" / "run-required-discord-e2e.sh"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_text("#!/usr/bin/env bash\nset -euo pipefail\n", encoding="utf-8")

    wrapper_command = module.prepare_bash_wrapper(
        deploy_path=deploy_path,
        wrapper_path=wrapper_path,
    )

    assert wrapper_command == "scripts/run-required-discord-e2e.sh"


def test_prepare_bash_wrapper_normalizes_crlf_copy(tmp_path: Path) -> None:
    module = _load_module()
    deploy_path = tmp_path / "deploy"
    wrapper_path = deploy_path / "scripts" / "run-required-discord-e2e.sh"
    wrapper_path.parent.mkdir(parents=True)
    wrapper_path.write_bytes(
        b'#!/usr/bin/env bash\r\nsource "$REPO_DIR/.env"\r\nset -euo pipefail\r\n'
    )
    manager_path = deploy_path / "scripts" / "discord_e2e_run_manager.sh"
    manager_path.write_bytes(b"#!/usr/bin/env bash\r\nset -euo pipefail\r\n")
    (deploy_path / ".env").write_bytes(b"OPENAI_API_KEY=test\r\n")

    wrapper_command = module.prepare_bash_wrapper(
        deploy_path=deploy_path,
        wrapper_path=wrapper_path,
    )

    normalized_path = deploy_path / wrapper_command
    normalized_manager = normalized_path.with_name("discord_e2e_run_manager.normalized.sh")
    normalized_env = deploy_path / "data" / "discord-canary" / "bash-env" / "repo.env.normalized"
    assert wrapper_command == "scripts/run-required-discord-e2e.normalized.sh"
    assert (
        b'source "$REPO_DIR/data/discord-canary/bash-env/repo.env.normalized"'
        in normalized_path.read_bytes()
    )
    assert normalized_manager.read_bytes() == b"#!/usr/bin/env bash\nset -euo pipefail\n"
    assert normalized_env.read_bytes() == b"OPENAI_API_KEY=test\n"


def test_prepare_bash_env_bridge_writes_shell_exports(tmp_path: Path) -> None:
    module = _load_module()
    deploy_path = tmp_path / "deploy"
    deploy_path.mkdir()

    bridge_path = module.prepare_bash_env_bridge(
        deploy_path=deploy_path,
        child_env={
            "TEST_DISCORD_BOT_TOKEN": "test-token",
            "DISCORD_E2E_ENABLED": "true",
            "IRRELEVANT": "ignore-me",
        },
    )

    rendered = (deploy_path / bridge_path).read_text(encoding="utf-8")
    assert bridge_path == "data/discord-canary/bash-env/required-discord-e2e.env.sh"
    assert "export TEST_DISCORD_BOT_TOKEN=test-token" in rendered
    assert "export DISCORD_E2E_ENABLED=true" in rendered
    assert "IRRELEVANT" not in rendered


def test_build_bash_wrapper_command_sources_bridge_file() -> None:
    module = _load_module()

    command = module.build_bash_wrapper_command(
        env_bridge_path="data/discord-canary/bash-env/required-discord-e2e.env.sh",
        wrapper_command="scripts/run-required-discord-e2e.sh",
    )

    assert command == [
        "-lc",
        (
            ". data/discord-canary/bash-env/required-discord-e2e.env.sh "
            "&& exec ./scripts/run-required-discord-e2e.sh"
        ),
    ]


def test_classify_canary_result_maps_lease_contention() -> None:
    module = _load_module()

    status, reason_code, reason = module.classify_canary_result(
        exit_code=1,
        timed_out=False,
        discord_result={"target_lease_status": "target_lease_unavailable"},
        log_text="",
    )

    assert status == "lease_contended"
    assert reason_code == "target_lease_unavailable"
    assert "lease" in reason.lower()


def test_classify_canary_result_detects_cleanup_degradation() -> None:
    module = _load_module()

    status, reason_code, reason = module.classify_canary_result(
        exit_code=0,
        timed_out=False,
        discord_result={"cleanup_status": "cleanup_failed"},
        log_text="",
    )

    assert status == "cleanup_degraded"
    assert reason_code == "discord_canary_cleanup_degraded"
    assert "cleanup" in reason.lower()


def test_should_emit_announcement_on_recovery() -> None:
    module = _load_module()
    previous_state = {"last_status": "failed"}
    receipt = {"status": "success"}

    assert module._should_emit_announcement(previous_state, receipt) is True


def test_run_canary_clears_stale_result_file(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    deploy_path = tmp_path / "deploy"
    scripts_dir = deploy_path / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "run-required-discord-e2e.sh").write_text(
        "#!/usr/bin/env bash\n",
        encoding="utf-8",
    )
    (deploy_path / ".env").write_text(
        "DISCORD_E2E_ENABLED=true\n",
        encoding="utf-8",
    )

    output_path = tmp_path / "out.json"
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "run.log"
    result_path = tmp_path / "result.json"
    result_path.write_text('{"stale": true}\n', encoding="utf-8")
    announcement_path = deploy_path / "scripts" / "windows" / "announcement-emit.py"
    announcement_path.parent.mkdir(parents=True, exist_ok=True)
    announcement_path.write_text("print('noop')\n", encoding="utf-8")

    class FakeProcess:
        def __init__(self, command, cwd=None, env=None, stdout=None, stderr=None, text=None):
            assert not result_path.exists()
            if stdout is not None:
                stdout.write("wrapped ok\n")
                stdout.flush()

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(module, "resolve_bash_executable", lambda base_env=None: "/bin/bash")
    monkeypatch.setattr(module, "_read_repo_sha", lambda deploy_path: "test-sha")
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)

    exit_code, receipt = module.run_canary(
        deploy_path=deploy_path,
        output_path=output_path,
        state_path=state_path,
        log_path=log_path,
        result_path=result_path,
        announcement_script=announcement_path,
        timeout_seconds=1200,
        bash_executable=None,
    )

    assert exit_code == 0
    assert receipt["discord_result"] == {}


def test_run_canary_uses_repo_relative_wrapper_path(tmp_path: Path, monkeypatch) -> None:
    module = _load_module()
    deploy_path = tmp_path / "deploy"
    scripts_dir = deploy_path / "scripts"
    scripts_dir.mkdir(parents=True)
    (scripts_dir / "run-required-discord-e2e.sh").write_text(
        "#!/usr/bin/env bash\n",
        encoding="utf-8",
    )
    (deploy_path / ".env").write_text(
        "DISCORD_E2E_ENABLED=true\n",
        encoding="utf-8",
    )

    output_path = tmp_path / "out.json"
    state_path = tmp_path / "state.json"
    log_path = tmp_path / "run.log"
    result_path = tmp_path / "result.json"
    announcement_path = deploy_path / "scripts" / "windows" / "announcement-emit.py"
    announcement_path.parent.mkdir(parents=True, exist_ok=True)
    announcement_path.write_text("print('noop')\n", encoding="utf-8")

    captured: dict[str, object] = {}

    class FakeProcess:
        def __init__(self, command, cwd=None, env=None, stdout=None, stderr=None, text=None):
            captured["command"] = command
            captured["cwd"] = cwd
            captured["env"] = env
            if stdout is not None:
                stdout.write("wrapped ok\n")
                stdout.flush()

        def wait(self, timeout=None):
            return 0

    monkeypatch.setattr(module, "resolve_bash_executable", lambda base_env=None: "/bin/bash")
    monkeypatch.setattr(module, "_read_repo_sha", lambda deploy_path: "test-sha")
    monkeypatch.setattr(module.subprocess, "Popen", FakeProcess)

    exit_code, receipt = module.run_canary(
        deploy_path=deploy_path,
        output_path=output_path,
        state_path=state_path,
        log_path=log_path,
        result_path=result_path,
        announcement_script=announcement_path,
        timeout_seconds=1200,
        bash_executable=None,
    )

    assert captured["command"] == [
        "/bin/bash",
        "-lc",
        (
            ". data/discord-canary/bash-env/required-discord-e2e.env.sh "
            "&& exec ./scripts/run-required-discord-e2e.sh"
        ),
    ]
    assert captured["cwd"] == deploy_path
    assert exit_code == 0
    assert receipt["status"] == "success"
