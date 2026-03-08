from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]


def test_discord_e2e_shell_wrappers_support_windows_repo_venvs() -> None:
    for relative_path in (
        "scripts/run-required-discord-e2e.sh",
        "scripts/local-required-e2e-receipt.sh",
    ):
        rendered = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "$REPO_DIR/.venv/Scripts/activate" in rendered
        assert "$REPO_DIR/venv/Scripts/activate" in rendered
        assert "$REPO_DIR/.venv/Scripts/python.exe" in rendered
        assert "$REPO_DIR/venv/Scripts/python.exe" in rendered
        assert 'cygpath -u "$provided_bundle"' in rendered
        assert 'cygpath -u "$ca_bundle"' in rendered
        assert "tr -d '\\r'" in rendered


def test_required_discord_wrapper_uses_thread_timeout_on_windows() -> None:
    rendered = (REPO_ROOT / "scripts/run-required-discord-e2e.sh").read_text(encoding="utf-8")
    assert "--timeout-method=thread" in rendered
    assert "${PYTEST_TIMEOUT_ARGS[@]}" in rendered
    assert 'case "$(uname -s)" in' in rendered


def test_discord_e2e_shell_wrappers_avoid_bash4_only_assoc_arrays() -> None:
    for relative_path in (
        "scripts/run-required-discord-e2e.sh",
        "scripts/local-required-e2e-receipt.sh",
        "scripts/discord_e2e_run_manager.sh",
    ):
        rendered = (REPO_ROOT / relative_path).read_text(encoding="utf-8")
        assert "local -A" not in rendered
        assert "declare -A" not in rendered
        assert "typeset -A" not in rendered


def test_windows_canary_runner_bootstraps_repo_venv_dependencies() -> None:
    rendered = (REPO_ROOT / "scripts/windows/discord-canary-runner.ps1").read_text(encoding="utf-8")
    assert "function Ensure-RepoPythonExecutable" in rendered
    assert "function Install-RepoPythonDependencies" in rendered
    assert "function Resolve-RepoCaBundle" in rendered
    assert "requirements-dev.txt" in rendered
    assert "$pythonExe = Ensure-RepoPythonExecutable -RepoPath $DeployPath" in rendered
    assert "$repoCaBundle = Resolve-RepoCaBundle -PythonExecutable $pythonExe" in rendered
    assert "$env:SSL_CERT_FILE = $repoCaBundle" in rendered
