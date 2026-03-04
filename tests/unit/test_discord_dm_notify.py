"""Unit tests for Windows Discord DM notification utility."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "windows" / "discord-dm-notify.py"
    )
    spec = importlib.util.spec_from_file_location("discord_dm_notify_test_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def dm_module():
    return _load_module()


def _run_main(dm_module, monkeypatch, capsys, args):
    monkeypatch.setattr(sys, "argv", ["discord-dm-notify.py", *args])
    code = dm_module.main()
    output = capsys.readouterr().out.strip()
    payload = json.loads(output)
    return code, payload


def test_disabled_by_default_is_non_blocking(dm_module, monkeypatch, capsys, tmp_path):
    code, payload = _run_main(
        dm_module,
        monkeypatch,
        capsys,
        [
            "--event",
            "deploy",
            "--sha",
            "abc1234",
            "--status",
            "success",
            "--secrets-path",
            str(tmp_path / "missing.bin"),
            "--state-path",
            str(tmp_path / "state.json"),
        ],
    )
    assert code == 0
    assert payload["status"] == "disabled"


def test_enabled_missing_token_is_non_blocking(dm_module, monkeypatch, capsys, tmp_path):
    code, payload = _run_main(
        dm_module,
        monkeypatch,
        capsys,
        [
            "--event",
            "deploy",
            "--sha",
            "abc1234",
            "--status",
            "success",
            "--enabled",
            "true",
            "--secrets-path",
            str(tmp_path / "missing.bin"),
            "--state-path",
            str(tmp_path / "state.json"),
        ],
    )
    assert code == 0
    assert payload["status"] == "skipped_missing_token"


def test_recipient_override_beats_owner_secret(dm_module, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        dm_module,
        "_load_promotions_secrets",
        lambda _path: ({"OWNER_USER_ID": "owner-1", "DISCORD_BOT_TOKEN": "token-1"}, None),
    )

    captured = {}

    def fake_send_discord_dm(*, bot_token, recipient_id, message):
        captured["bot_token"] = bot_token
        captured["recipient_id"] = recipient_id
        captured["message"] = message
        return True, "{}"

    monkeypatch.setattr(dm_module, "_send_discord_dm", fake_send_discord_dm)

    code, payload = _run_main(
        dm_module,
        monkeypatch,
        capsys,
        [
            "--event",
            "promotions",
            "--sha",
            "abc1234",
            "--status",
            "success",
            "--enabled",
            "true",
            "--recipient-id",
            "override-9",
            "--secrets-path",
            str(tmp_path / "unused.bin"),
            "--state-path",
            str(tmp_path / "state.json"),
        ],
    )

    assert code == 0
    assert payload["status"] == "sent"
    assert captured["recipient_id"] == "override-9"


def test_dedupe_key_prevents_repeat_send(dm_module, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        dm_module,
        "_load_promotions_secrets",
        lambda _path: (
            {
                "OWNER_USER_ID": "owner-1",
                "DISCORD_BOT_TOKEN": "token-1",
                "DISCORD_DM_NOTIFY_ENABLED": "true",
            },
            None,
        ),
    )

    calls = {"count": 0}

    def fake_send_discord_dm(*, bot_token, recipient_id, message):
        calls["count"] += 1
        return True, "{}"

    monkeypatch.setattr(dm_module, "_send_discord_dm", fake_send_discord_dm)
    state_path = tmp_path / "state.json"
    secrets_path = tmp_path / "unused.bin"

    first_code, first_payload = _run_main(
        dm_module,
        monkeypatch,
        capsys,
        [
            "--event",
            "deploy",
            "--sha",
            "abc1234",
            "--status",
            "success",
            "--secrets-path",
            str(secrets_path),
            "--state-path",
            str(state_path),
        ],
    )
    second_code, second_payload = _run_main(
        dm_module,
        monkeypatch,
        capsys,
        [
            "--event",
            "deploy",
            "--sha",
            "abc1234",
            "--status",
            "success",
            "--secrets-path",
            str(secrets_path),
            "--state-path",
            str(state_path),
        ],
    )

    assert first_code == 0
    assert second_code == 0
    assert first_payload["status"] == "sent"
    assert second_payload["status"] == "deduped"
    assert calls["count"] == 1
