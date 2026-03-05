"""Unit tests for Windows announcement emitter utility."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "windows" / "announcement-emit.py"
    )
    spec = importlib.util.spec_from_file_location("announcement_emit_test_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def announcement_module():
    return _load_module()


def _run_main(announcement_module, monkeypatch, capsys, args):
    monkeypatch.setattr(sys, "argv", ["announcement-emit.py", *args])
    code = announcement_module.main()
    output = capsys.readouterr().out.strip()
    payload = json.loads(output)
    return code, payload


def test_disabled_by_default_is_non_blocking(announcement_module, monkeypatch, capsys, tmp_path):
    code, payload = _run_main(
        announcement_module,
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
            "--outbox-dir",
            str(tmp_path / "outbox"),
        ],
    )
    assert code == 0
    assert payload["status"] == "disabled"


def test_enabled_missing_api_secret_is_non_blocking(
    announcement_module,
    monkeypatch,
    capsys,
    tmp_path,
):
    monkeypatch.delenv("ANNOUNCEMENT_API_SECRET", raising=False)
    monkeypatch.delenv("SKILLS_API_SECRET", raising=False)
    code, payload = _run_main(
        announcement_module,
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
    assert payload["status"] == "skipped_missing_api_secret"


def test_target_override_beats_owner_secret(announcement_module, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        announcement_module,
        "_load_promotions_secrets",
        lambda _path: (
            {
                "OWNER_USER_ID": "101",
                "ANNOUNCEMENT_API_SECRET": "api-secret",
                "ANNOUNCEMENT_EMIT_ENABLED": "true",
            },
            None,
        ),
    )

    captured = {}

    def fake_attempt_emit(*, api_url, api_secret, request_payload):
        captured["api_url"] = api_url
        captured["api_secret"] = api_secret
        captured["target_user_id"] = request_payload["target_user_id"]
        return True, "accepted"

    monkeypatch.setattr(announcement_module, "_attempt_emit", fake_attempt_emit)

    code, payload = _run_main(
        announcement_module,
        monkeypatch,
        capsys,
        [
            "--event",
            "promotions",
            "--sha",
            "abc1234",
            "--status",
            "success",
            "--target-user-id",
            "202",
            "--secrets-path",
            str(tmp_path / "unused.bin"),
            "--state-path",
            str(tmp_path / "state.json"),
        ],
    )

    assert code == 0
    assert payload["status"] == "sent"
    assert captured["api_secret"] == "api-secret"
    assert captured["target_user_id"] == 202


def test_dedupe_key_prevents_repeat_send(announcement_module, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        announcement_module,
        "_load_promotions_secrets",
        lambda _path: (
            {
                "ANNOUNCEMENT_API_SECRET": "api-secret",
                "ANNOUNCEMENT_EMIT_ENABLED": "true",
                "ANNOUNCEMENT_TARGET_USER_ID": "333",
            },
            None,
        ),
    )

    calls = {"count": 0}

    def fake_attempt_emit(*, api_url, api_secret, request_payload):
        calls["count"] += 1
        return True, "accepted"

    monkeypatch.setattr(announcement_module, "_attempt_emit", fake_attempt_emit)

    state_path = tmp_path / "state.json"
    secrets_path = tmp_path / "unused.bin"
    outbox_dir = tmp_path / "outbox"
    args = [
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
        "--outbox-dir",
        str(outbox_dir),
    ]

    first_code, first_payload = _run_main(announcement_module, monkeypatch, capsys, args)
    second_code, second_payload = _run_main(announcement_module, monkeypatch, capsys, args)

    assert first_code == 0
    assert second_code == 0
    assert first_payload["status"] == "sent"
    assert second_payload["status"] == "deduped"
    assert calls["count"] == 1


def test_failed_emit_is_queued_non_blocking(announcement_module, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        announcement_module,
        "_load_promotions_secrets",
        lambda _path: (
            {
                "ANNOUNCEMENT_API_SECRET": "api-secret",
                "ANNOUNCEMENT_EMIT_ENABLED": "true",
                "ANNOUNCEMENT_TARGET_USER_ID": "404",
            },
            None,
        ),
    )
    monkeypatch.setattr(
        announcement_module,
        "_attempt_emit",
        lambda **kwargs: (False, "http_503:unavailable"),
    )

    outbox_dir = tmp_path / "outbox"
    code, payload = _run_main(
        announcement_module,
        monkeypatch,
        capsys,
        [
            "--event",
            "deploy",
            "--sha",
            "abc1234",
            "--status",
            "failure",
            "--secrets-path",
            str(tmp_path / "unused.bin"),
            "--state-path",
            str(tmp_path / "state.json"),
            "--outbox-dir",
            str(outbox_dir),
        ],
    )

    assert code == 0
    assert payload["status"] == "queued_non_blocking"
    queued_path = Path(payload["queued_path"])
    assert queued_path.exists()
    queued_payload = json.loads(queued_path.read_text(encoding="utf-8"))
    assert queued_payload["last_error"] == "http_503:unavailable"


def test_flush_outbox_replays_queued_events(announcement_module, monkeypatch, capsys, tmp_path):
    monkeypatch.setattr(
        announcement_module,
        "_load_promotions_secrets",
        lambda _path: (
            {
                "ANNOUNCEMENT_API_SECRET": "api-secret",
                "ANNOUNCEMENT_EMIT_ENABLED": "true",
                "ANNOUNCEMENT_TARGET_USER_ID": "505",
            },
            None,
        ),
    )

    outbox_dir = tmp_path / "outbox"
    state_path = tmp_path / "state.json"
    secrets_path = tmp_path / "unused.bin"

    monkeypatch.setattr(
        announcement_module,
        "_attempt_emit",
        lambda **kwargs: (False, "http_503:unavailable"),
    )
    first_code, first_payload = _run_main(
        announcement_module,
        monkeypatch,
        capsys,
        [
            "--event",
            "deploy",
            "--sha",
            "abc1234",
            "--status",
            "failure",
            "--secrets-path",
            str(secrets_path),
            "--state-path",
            str(state_path),
            "--outbox-dir",
            str(outbox_dir),
        ],
    )
    assert first_code == 0
    assert first_payload["status"] == "queued_non_blocking"

    monkeypatch.setattr(
        announcement_module,
        "_attempt_emit",
        lambda **kwargs: (True, "accepted"),
    )
    second_code, second_payload = _run_main(
        announcement_module,
        monkeypatch,
        capsys,
        [
            "--flush-outbox",
            "--secrets-path",
            str(secrets_path),
            "--state-path",
            str(state_path),
            "--outbox-dir",
            str(outbox_dir),
        ],
    )

    assert second_code == 0
    assert second_payload["status"] == "flush_completed"
    assert second_payload["flushed"] == 1
    assert second_payload["pending"] == 0
    assert list(outbox_dir.glob("*.json")) == []
