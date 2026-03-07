"""Unit tests for the Windows Discord canary runner."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


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
        "DISCORD_TOKEN=prod-token\n"
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
        },
    )

    assert env["TEST_DISCORD_BOT_TOKEN"] == "override-test-token"
    assert env["TEST_DISCORD_GUILD_ID"] == "override-guild"
    assert env["TEST_DISCORD_E2E_CHANNEL_PREFIX"] == "canary-prefix"
    assert env["DISCORD_TOKEN"] == "live-target-token"
    assert "DISCORD_TOKEN_TEST" not in env
    assert env["DISCORD_E2E_MODE"] == "windows_prod_canary"


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
