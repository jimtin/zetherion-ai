from __future__ import annotations

from pathlib import Path

from tests.integration import e2e_runtime

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_test_full_script_supports_docker_python_fallback() -> None:
    rendered = (REPO_ROOT / "scripts/test-full.sh").read_text(encoding="utf-8")
    assert "docker-python-tool.sh" in rendered
    assert "ZETHERION_USE_DOCKER_PYTHON=true" in rendered
    assert "write-local-readiness-receipt.py" in rendered
    assert (
        'LOCAL_READINESS_RECEIPT_PATH="${LOCAL_READINESS_RECEIPT_PATH:-.artifacts/local-readiness-receipt.json}"'
        in rendered
    )


def test_pre_push_script_uses_python_module_wrappers() -> None:
    rendered = (REPO_ROOT / "scripts/pre-push-tests.sh").read_text(encoding="utf-8")
    assert 'run_python_module()' in rendered
    assert 'run_ruff()' in rendered
    assert 'run_pip_audit()' in rendered
    assert 'run_pip_licenses()' in rendered
    assert 'E2E_ENABLE_OLLAMA="${E2E_ENABLE_OLLAMA:-false}"' in rendered
    assert 'ensure_optional_ollama_profile()' in rendered
    assert 'EXPLICIT_ZETHERION_ENV_FILE="${ZETHERION_ENV_FILE:-}"' in rendered
    assert 'DEFAULT_ZETHERION_ENV_FILE="$REPO_DIR/.env"' in rendered
    assert 'is_generated_e2e_env_file()' in rendered
    assert 'Ignoring missing generated E2E env file' in rendered
    assert 'OLLAMA_DOCKER_IMAGE="${OLLAMA_DOCKER_IMAGE:-ollama/ollama:latest@sha256:' in rendered
    assert 'ensure_ollama_base_image()' in rendered


def test_discord_wrappers_support_docker_python_fallback() -> None:
    for relative_path in (
        "scripts/run-required-discord-e2e.sh",
        "scripts/local-required-e2e-receipt.sh",
    ):
        rendered = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "DOCKER_PYTHON_WRAPPER" in rendered
        assert "python_supports_required_version" in rendered
        assert "python_has_required_modules" in rendered
        assert 'if [[ "${ZETHERION_USE_DOCKER_PYTHON:-false}" == "true" ]]; then' in rendered
        assert 'EXPLICIT_ZETHERION_ENV_FILE="${ZETHERION_ENV_FILE:-}"' in rendered
        assert 'DEFAULT_ZETHERION_ENV_FILE="$REPO_DIR/.env"' in rendered
        assert 'is_generated_e2e_env_file()' in rendered
        assert 'Ignoring missing generated E2E env file' in rendered
    local_receipt = (REPO_ROOT / "scripts/local-required-e2e-receipt.sh").read_text(
        encoding="utf-8"
    )
    assert 'E2E_ENABLE_OLLAMA="${E2E_ENABLE_OLLAMA:-false}"' in local_receipt
    assert 'ensure_optional_ollama_profile()' in local_receipt
    assert (
        'OLLAMA_DOCKER_IMAGE="${OLLAMA_DOCKER_IMAGE:-ollama/ollama:latest@sha256:'
        in local_receipt
    )
    assert 'ensure_ollama_base_image()' in local_receipt


def test_docker_python_tool_normalizes_tmpdir() -> None:
    rendered = (REPO_ROOT / "scripts/docker-python-tool.sh").read_text(encoding="utf-8")
    assert 'HOST_WORKSPACE_ROOT="${ZETHERION_HOST_WORKSPACE_ROOT:-$REPO_DIR}"' in rendered
    assert 'WORKSPACE_MOUNT_TARGET="${ZETHERION_WORKSPACE_MOUNT_TARGET:-/workspace}"' in rendered
    assert (
        'SIBLING_CGS_ROOT_DEFAULT="$(cd "$REPO_DIR/.." && pwd)/catalyst-group-solutions"'
        in rendered
    )
    assert (
        'SIBLING_CGS_MOUNT_TARGET="${ZETHERION_CGS_WORKSPACE_MOUNT_TARGET:-/workspace-siblings/catalyst-group-solutions}"'
        in rendered
    )
    assert '"CGS_WORKSPACE_ROOT=$SIBLING_CGS_MOUNT_TARGET"' in rendered
    assert '"CGS_DOCKER_HOST_ROOT=$SIBLING_CGS_ROOT_DEFAULT"' in rendered
    assert 'is_generated_e2e_env_file()' in rendered
    assert 'CONTAINER_ENV_FILE_PATH=""' in rendered
    assert 'HOST_DOCKERFILE_PATH="$(map_repo_path_to_host "$DOCKERFILE_PATH")"' in rendered
    assert '"-e" "TMPDIR=/tmp"' in rendered
    assert '"-e" "TMP=/tmp"' in rendered
    assert '"-e" "TEMP=/tmp"' in rendered
    assert 'Ignoring missing generated E2E env file' in rendered
    assert 'RUN_ARGS+=(-v "$HOST_ENV_FILE_PATH:$CONTAINER_ENV_FILE_PATH:ro")' in rendered
    assert 'RUN_ARGS+=(-v "$HOST_WORKSPACE_ROOT:$HOST_WORKSPACE_ROOT")' in rendered
    assert 'RUN_ARGS+=(-v "$REPO_DIR:$REPO_DIR")' in rendered
    assert 'RUN_ARGS+=(-v "$SIBLING_CGS_ROOT_DEFAULT:$SIBLING_CGS_MOUNT_TARGET")' in rendered
    assert '-v "$HOST_WORKSPACE_ROOT:$WORKSPACE_MOUNT_TARGET"' in rendered
    assert "RECEIPT_*" in rendered
    assert "SUITE_*" in rendered
    assert "WRAPPER_*" in rendered
    assert 'if command -v python3 >/dev/null 2>&1; then' in rendered
    assert 'if command -v sha256sum >/dev/null 2>&1; then' in rendered
    assert 'if command -v shasum >/dev/null 2>&1; then' in rendered
    assert "|TEMP|" not in rendered
    assert "|TMP|" not in rendered
    assert "|TMPDIR|" not in rendered
    assert 'EXPLICIT_ZETHERION_ENV_FILE="${ZETHERION_ENV_FILE:-}"' in rendered
    assert 'DEFAULT_ZETHERION_ENV_FILE="$REPO_DIR/.env"' in rendered


def test_fullstack_critical_lane_uses_heartbeat_wrapper() -> None:
    rendered = (REPO_ROOT / "scripts/testing/lanes.mjs").read_text(encoding="utf-8")
    assert '"e2e-fullstack-critical": {' in rendered
    assert 'heartbeat: true' in rendered


def test_check_lane_uses_repo_python_helper_for_mkdocs() -> None:
    rendered = (REPO_ROOT / "scripts/testing/lanes.mjs").read_text(encoding="utf-8")
    assert 'scripts/repo-python-tool.sh -m mkdocs build --strict' in rendered


def test_repo_python_tool_prefers_repo_virtualenvs_before_python3() -> None:
    rendered = (REPO_ROOT / "scripts/repo-python-tool.sh").read_text(encoding="utf-8")
    assert '"$REPO_DIR/.venv/bin/python"' in rendered
    assert '"$REPO_DIR/venv/bin/python"' in rendered
    assert 'command -v python3' in rendered
    assert 'DOCKER_PYTHON_WRAPPER="$SCRIPT_DIR/docker-python-tool.sh"' in rendered
    assert 'python_supports_module' in rendered
    assert 'exec "$DOCKER_PYTHON_WRAPPER" "$@"' in rendered
    assert '[ "${1:-}" = "-m" ] && [ "${2:-}" = "mkdocs" ]' in rendered
    assert 'exec "$PYTHON_BIN" "$@"' in rendered


def test_e2e_runtime_uses_host_override(monkeypatch) -> None:
    monkeypatch.setenv("E2E_RUNTIME_HOST", "host.docker.internal")
    e2e_runtime._runtime = None
    runtime = e2e_runtime.get_runtime()

    assert runtime.host == "host.docker.internal"
    assert runtime.skills_url.startswith("http://host.docker.internal:")
    assert "host.docker.internal" in runtime.postgres_dsn

    monkeypatch.delenv("E2E_RUNTIME_HOST", raising=False)
    e2e_runtime._runtime = None
