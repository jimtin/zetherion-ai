#!/usr/bin/env python3
"""Run an isolated Discord E2E canary against the Windows production bot."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

DEFAULT_DEPLOY_PATH = Path(r"C:\ZetherionAI")
DEFAULT_DATA_ROOT = DEFAULT_DEPLOY_PATH / "data" / "discord-canary"
DEFAULT_STATE_PATH = DEFAULT_DATA_ROOT / "state.json"
DEFAULT_OUTPUT_PATH = DEFAULT_DATA_ROOT / "last-run.json"
DEFAULT_LOG_PATH = DEFAULT_DATA_ROOT / "last-run.log"
DEFAULT_RESULT_PATH = DEFAULT_DATA_ROOT / "discord-e2e-result.json"
DEFAULT_ANNOUNCEMENT_SCRIPT = DEFAULT_DEPLOY_PATH / "scripts" / "windows" / "announcement-emit.py"
DEFAULT_ANNOUNCEMENT_STATE_PATH = (
    DEFAULT_DEPLOY_PATH / "data" / "announcements" / "notifications-state.json"
)
DEFAULT_ANNOUNCEMENT_OUTBOX_DIR = DEFAULT_DEPLOY_PATH / "data" / "announcements" / "outbox"
DEFAULT_TIMEOUT_SECONDS = 20 * 60
DEFAULT_TTL_MINUTES = 60
DEFAULT_CHANNEL_PREFIX = "zeth-canary"
DEFAULT_INTERVAL_MINUTES = 6 * 60
LEASE_CONTENDED_TOKEN = "target_lease_unavailable"


SUCCESS_EXIT_STATUSES = {"success", "cleanup_degraded", "lease_contended"}
ALERTABLE_FAILURE_STATUSES = {"failed", "timeout", "runner_error"}


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _now_iso() -> str:
    return _now().isoformat()


def _read_json(path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default
    if isinstance(payload, dict):
        return payload
    return default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


def _read_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if not key:
            continue
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key] = value
    return values


def _env_lookup(name: str, *, file_env: dict[str, str], base_env: dict[str, str]) -> str:
    value = base_env.get(name)
    if value is not None and str(value).strip():
        return str(value).strip()
    file_value = file_env.get(name)
    if file_value is not None and str(file_value).strip():
        return str(file_value).strip()
    return ""


def _resolve_first(
    *names: str, file_env: dict[str, str], base_env: dict[str, str], default: str = ""
) -> str:
    for name in names:
        value = _env_lookup(name, file_env=file_env, base_env=base_env)
        if value:
            return value
    return default


def resolve_bash_executable(*, base_env: dict[str, str] | None = None) -> str:
    env = base_env or os.environ
    for candidate in (
        env.get("BASH_EXE", ""),
        shutil.which("bash"),
        r"C:\Program Files\Git\bin\bash.exe",
        r"C:\Program Files\Git\usr\bin\bash.exe",
    ):
        if candidate and Path(candidate).exists():
            return str(candidate)
    raise RuntimeError("bash executable not found; install Git for Windows or set BASH_EXE")


def build_child_env(
    deploy_path: Path,
    result_path: Path,
    *,
    base_env: dict[str, str] | None = None,
) -> dict[str, str]:
    effective_env = dict(base_env or os.environ)
    file_env = _read_env_file(deploy_path / ".env")

    child = dict(effective_env)
    child["DISCORD_E2E_MODE"] = "windows_prod_canary"
    child["DISCORD_E2E_RESULT_PATH"] = str(result_path)
    child["TEST_DISCORD_BOT_TOKEN"] = _resolve_first(
        "WINDOWS_DISCORD_CANARY_TEST_BOT_TOKEN",
        "TEST_DISCORD_BOT_TOKEN",
        file_env=file_env,
        base_env=effective_env,
    )
    child["TEST_DISCORD_GUILD_ID"] = _resolve_first(
        "WINDOWS_DISCORD_CANARY_GUILD_ID",
        "TEST_DISCORD_GUILD_ID",
        file_env=file_env,
        base_env=effective_env,
    )
    child["TEST_DISCORD_E2E_PARENT_CHANNEL_ID"] = _resolve_first(
        "WINDOWS_DISCORD_CANARY_PARENT_CHANNEL_ID",
        "TEST_DISCORD_E2E_PARENT_CHANNEL_ID",
        file_env=file_env,
        base_env=effective_env,
    )
    child["TEST_DISCORD_E2E_CATEGORY_ID"] = _resolve_first(
        "WINDOWS_DISCORD_CANARY_CATEGORY_ID",
        "TEST_DISCORD_E2E_CATEGORY_ID",
        file_env=file_env,
        base_env=effective_env,
    )
    child["TEST_DISCORD_E2E_CATEGORY_NAME"] = _resolve_first(
        "WINDOWS_DISCORD_CANARY_CATEGORY_NAME",
        "TEST_DISCORD_E2E_CATEGORY_NAME",
        file_env=file_env,
        base_env=effective_env,
    )
    child["TEST_DISCORD_E2E_CHANNEL_PREFIX"] = _resolve_first(
        "WINDOWS_DISCORD_CANARY_CHANNEL_PREFIX",
        "TEST_DISCORD_E2E_CHANNEL_PREFIX",
        file_env=file_env,
        base_env=effective_env,
        default=DEFAULT_CHANNEL_PREFIX,
    )
    child["TEST_DISCORD_E2E_TTL_MINUTES"] = _resolve_first(
        "WINDOWS_DISCORD_CANARY_TTL_MINUTES",
        "TEST_DISCORD_E2E_TTL_MINUTES",
        file_env=file_env,
        base_env=effective_env,
        default=str(DEFAULT_TTL_MINUTES),
    )
    child["DISCORD_E2E_ALLOWED_AUTHOR_IDS"] = _resolve_first(
        "WINDOWS_DISCORD_CANARY_ALLOWED_AUTHOR_IDS",
        "DISCORD_E2E_ALLOWED_AUTHOR_IDS",
        file_env=file_env,
        base_env=effective_env,
    )
    child["DISCORD_E2E_ENABLED"] = _resolve_first(
        "WINDOWS_DISCORD_CANARY_ENABLED",
        "DISCORD_E2E_ENABLED",
        file_env=file_env,
        base_env=effective_env,
        default="true",
    )
    child["DISCORD_E2E_PROVIDER"] = _resolve_first(
        "WINDOWS_DISCORD_CANARY_PROVIDER",
        "DISCORD_E2E_PROVIDER",
        file_env=file_env,
        base_env=effective_env,
        default="groq",
    )
    target_token = _resolve_first(
        "WINDOWS_DISCORD_CANARY_TARGET_TOKEN",
        "DISCORD_TOKEN",
        file_env=file_env,
        base_env=effective_env,
    )
    if target_token:
        child["DISCORD_TOKEN"] = target_token
    target_bot_id = _resolve_first(
        "WINDOWS_DISCORD_CANARY_TARGET_BOT_ID",
        "TEST_DISCORD_TARGET_BOT_ID",
        file_env=file_env,
        base_env=effective_env,
    )
    if target_bot_id:
        child["TEST_DISCORD_TARGET_BOT_ID"] = target_bot_id
    else:
        child.pop("TEST_DISCORD_TARGET_BOT_ID", None)

    # Force the canary to target the live production bot identity,
    # never the local test bot token.
    child.pop("DISCORD_TOKEN_TEST", None)
    return child


def _terminate_process_tree(process: subprocess.Popen[str]) -> None:
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(
            ["taskkill", "/PID", str(process.pid), "/T", "/F"],
            check=False,
            capture_output=True,
            text=True,
        )
        return
    process.kill()


def _read_log_text(path: Path) -> str:
    if not path.exists():
        return ""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def classify_canary_result(
    *,
    exit_code: int,
    timed_out: bool,
    discord_result: dict[str, Any],
    log_text: str,
) -> tuple[str, str, str]:
    cleanup_status = str(discord_result.get("cleanup_status", "")).strip().lower()
    lease_status = str(discord_result.get("target_lease_status", "")).strip().lower()
    log_lower = log_text.lower()

    if timed_out:
        return (
            "timeout",
            "discord_canary_timeout",
            "Windows Discord canary exceeded the configured timeout.",
        )

    if exit_code == 0:
        if cleanup_status and cleanup_status != "cleaned":
            return (
                "cleanup_degraded",
                "discord_canary_cleanup_degraded",
                "Windows Discord canary passed but cleanup "
                f"finished with status '{cleanup_status}'.",
            )
        return (
            "success",
            "discord_canary_passed",
            "Windows Discord canary passed.",
        )

    if lease_status == LEASE_CONTENDED_TOKEN or LEASE_CONTENDED_TOKEN in log_lower:
        return (
            "lease_contended",
            LEASE_CONTENDED_TOKEN,
            "Windows Discord canary skipped because another active run "
            "already holds the target bot lease.",
        )

    return (
        "failed",
        "discord_canary_failed",
        "Windows Discord canary failed.",
    )


def _read_repo_sha(deploy_path: Path) -> str:
    result = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=deploy_path,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        return ""
    return result.stdout.strip()


def _should_emit_announcement(previous_state: dict[str, Any], receipt: dict[str, Any]) -> bool:
    current_status = str(receipt.get("status", "")).strip().lower()
    previous_status = str(previous_state.get("last_status", "")).strip().lower()

    if current_status in ALERTABLE_FAILURE_STATUSES:
        return current_status != previous_status
    if current_status == "cleanup_degraded":
        return current_status != previous_status
    if current_status == "success":
        return previous_status in ALERTABLE_FAILURE_STATUSES | {"cleanup_degraded"}
    return False


def _invoke_announcement(
    *,
    deploy_path: Path,
    announcement_script: Path,
    previous_state: dict[str, Any],
    receipt: dict[str, Any],
) -> dict[str, Any]:
    if not announcement_script.exists():
        return {"status": "not_run", "reason": "announcement_script_missing"}

    if not _should_emit_announcement(previous_state, receipt):
        return {"status": "not_run", "reason": "status_not_alertable"}

    sha = str(receipt.get("repo_sha", "")).strip() or "unknown"
    state_path = DEFAULT_ANNOUNCEMENT_STATE_PATH
    outbox_dir = DEFAULT_ANNOUNCEMENT_OUTBOX_DIR
    command = [
        sys.executable,
        str(announcement_script),
        "--event",
        "discord_canary",
        "--sha",
        sha,
        "--status",
        str(receipt.get("status", "unknown")),
        "--message",
        str(receipt.get("reason", "")),
        "--state-path",
        str(state_path),
        "--outbox-dir",
        str(outbox_dir),
    ]
    result = subprocess.run(command, cwd=deploy_path, capture_output=True, text=True, check=False)
    payload: dict[str, Any] = {
        "status": "error",
        "exit_code": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }
    if result.stdout.strip():
        try:
            parsed = json.loads(result.stdout)
        except json.JSONDecodeError:
            pass
        else:
            if isinstance(parsed, dict):
                payload = parsed
                payload["exit_code"] = result.returncode
    return payload


def _update_state(state: dict[str, Any], receipt: dict[str, Any]) -> dict[str, Any]:
    updated = dict(state)
    generated_at = str(receipt.get("generated_at", _now_iso()))
    status = str(receipt.get("status", "unknown"))
    updated["last_run_at"] = generated_at
    updated["last_status"] = status
    updated["last_reason_code"] = str(receipt.get("reason_code", ""))
    updated["last_cleanup_status"] = str(
        receipt.get("discord_result", {}).get("cleanup_status", "")
    )
    updated["last_target_lease_status"] = str(
        receipt.get("discord_result", {}).get("target_lease_status", "")
    )
    updated["last_repo_sha"] = str(receipt.get("repo_sha", ""))
    updated["updated_at"] = _now_iso()
    if status == "success":
        updated["last_success_at"] = generated_at
    elif status == "lease_contended":
        updated["last_lease_contended_at"] = generated_at
    else:
        updated["last_failure_at"] = generated_at
    return updated


def run_canary(
    *,
    deploy_path: Path,
    output_path: Path,
    state_path: Path,
    log_path: Path,
    result_path: Path,
    announcement_script: Path,
    timeout_seconds: int,
    bash_executable: str | None = None,
) -> tuple[int, dict[str, Any]]:
    if timeout_seconds < 60:
        raise RuntimeError("timeout_seconds must be >= 60")
    if not deploy_path.exists():
        raise RuntimeError(f"deploy path not found: {deploy_path}")

    bash_path = bash_executable or resolve_bash_executable()
    child_env = build_child_env(deploy_path, result_path)
    previous_state = _read_json(state_path, default={})
    wrapper_path = deploy_path / "scripts" / "run-required-discord-e2e.sh"
    if not wrapper_path.exists():
        raise RuntimeError(f"Discord E2E wrapper not found: {wrapper_path}")
    wrapper_command = wrapper_path.relative_to(deploy_path).as_posix()

    result_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.parent.mkdir(parents=True, exist_ok=True)

    command = [bash_path, wrapper_command]
    started_at = _now_iso()
    timed_out = False
    exit_code = 1

    with log_path.open("w", encoding="utf-8") as log_handle:
        process = subprocess.Popen(
            command,
            cwd=deploy_path,
            env=child_env,
            stdout=log_handle,
            stderr=subprocess.STDOUT,
            text=True,
        )
        try:
            exit_code = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            timed_out = True
            _terminate_process_tree(process)
            process.wait(timeout=30)
            exit_code = 124

    log_text = _read_log_text(log_path)
    discord_result = _read_json(result_path, default={})
    status, reason_code, reason = classify_canary_result(
        exit_code=exit_code,
        timed_out=timed_out,
        discord_result=discord_result,
        log_text=log_text,
    )

    receipt: dict[str, Any] = {
        "generated_at": _now_iso(),
        "started_at": started_at,
        "deploy_path": str(deploy_path),
        "status": status,
        "reason_code": reason_code,
        "reason": reason,
        "timeout_seconds": timeout_seconds,
        "timed_out": timed_out,
        "wrapper_exit_code": exit_code,
        "repo_sha": _read_repo_sha(deploy_path),
        "log_path": str(log_path),
        "result_path": str(result_path),
        "state_path": str(state_path),
        "discord_result": discord_result,
        "synthetic_test_run": bool(discord_result.get("synthetic_test_run", True)),
    }

    updated_state = _update_state(previous_state, receipt)
    _write_json(state_path, updated_state)
    receipt["announcement"] = _invoke_announcement(
        deploy_path=deploy_path,
        announcement_script=announcement_script,
        previous_state=previous_state,
        receipt=receipt,
    )
    _write_json(output_path, receipt)

    return (0 if status in SUCCESS_EXIT_STATUSES else 1), receipt


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--deploy-path", default=str(DEFAULT_DEPLOY_PATH))
    parser.add_argument("--output-path", default=str(DEFAULT_OUTPUT_PATH))
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--log-path", default=str(DEFAULT_LOG_PATH))
    parser.add_argument("--result-path", default=str(DEFAULT_RESULT_PATH))
    parser.add_argument("--announcement-script", default=str(DEFAULT_ANNOUNCEMENT_SCRIPT))
    parser.add_argument("--timeout-seconds", type=int, default=DEFAULT_TIMEOUT_SECONDS)
    parser.add_argument("--bash-executable", default="")
    args = parser.parse_args()

    try:
        exit_code, receipt = run_canary(
            deploy_path=Path(args.deploy_path),
            output_path=Path(args.output_path),
            state_path=Path(args.state_path),
            log_path=Path(args.log_path),
            result_path=Path(args.result_path),
            announcement_script=Path(args.announcement_script),
            timeout_seconds=args.timeout_seconds,
            bash_executable=args.bash_executable or None,
        )
    except Exception as exc:  # noqa: BLE001
        receipt = {
            "generated_at": _now_iso(),
            "deploy_path": args.deploy_path,
            "status": "runner_error",
            "reason_code": "discord_canary_runner_error",
            "reason": str(exc),
            "log_path": args.log_path,
            "result_path": args.result_path,
            "state_path": args.state_path,
            "repo_sha": "",
            "discord_result": {},
            "synthetic_test_run": True,
        }
        state = _read_json(Path(args.state_path), default={})
        _write_json(Path(args.state_path), _update_state(state, receipt))
        _write_json(Path(args.output_path), receipt)
        print(json.dumps(receipt, ensure_ascii=True))
        return 1

    print(json.dumps(receipt, ensure_ascii=True))
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
