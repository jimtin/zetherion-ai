"""Unit tests for announcement DM guard check script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "check-announcement-dm-guard.py"
    )
    spec = importlib.util.spec_from_file_location("check_announcement_dm_guard_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_collect_violations_allows_discord_adapter(tmp_path):
    module = _load_module()

    allowed_file = tmp_path / "src" / "zetherion_ai" / "announcements" / "discord_adapter.py"
    allowed_file.parent.mkdir(parents=True, exist_ok=True)
    allowed_file.write_text("async def send(user):\n    await user.send('ok')\n", encoding="utf-8")

    module.TARGET_PATHS = (Path("src/zetherion_ai/announcements"),)
    module.ALLOWED_USER_SEND_FILES = {Path("src/zetherion_ai/announcements/discord_adapter.py")}

    violations = module.collect_violations(tmp_path)
    assert violations == []


def test_collect_violations_flags_forbidden_user_send(tmp_path):
    module = _load_module()

    blocked_file = tmp_path / "src" / "zetherion_ai" / "discord" / "bot.py"
    blocked_file.parent.mkdir(parents=True, exist_ok=True)
    blocked_file.write_text(
        "async def notify(user):\n    await user.send('blocked')\n",
        encoding="utf-8",
    )

    module.TARGET_PATHS = (Path("src/zetherion_ai/discord/bot.py"),)
    module.ALLOWED_USER_SEND_FILES = {Path("src/zetherion_ai/announcements/discord_adapter.py")}

    violations = module.collect_violations(tmp_path)
    assert len(violations) == 1
    assert "src/zetherion_ai/discord/bot.py:2" in violations[0]
