"""Regression tests for Discord E2E channel isolation management."""

from __future__ import annotations

import importlib.util
import json
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from zetherion_ai.discord.e2e_lease import DiscordE2ELease


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "discord_e2e_run_manager.py"
    spec = importlib.util.spec_from_file_location("discord_e2e_run_manager_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


class FakeDiscordAPI:
    def __init__(self, *, user_id: int, channels: list[dict] | None = None) -> None:
        self.user_id = user_id
        self.channels = list(channels or [])
        self.created_channels: list[dict] = []
        self.deleted_channels: list[int] = []
        self.sent_messages: list[dict] = []
        self.deleted_messages: list[int] = []

    def get_current_user(self) -> dict[str, str]:
        return {"id": str(self.user_id)}

    def list_guild_channels(self, guild_id: int) -> list[dict]:
        return list(self.channels)

    def create_guild_channel(self, guild_id: int, payload: dict) -> dict:
        created = {
            "id": str(9000 + len(self.channels)),
            "guild_id": str(guild_id),
            **payload,
        }
        self.channels.append(created)
        self.created_channels.append(created)
        return created

    def create_thread(self, parent_channel_id: int, payload: dict) -> dict:
        created = {
            "id": str(9500 + len(self.channels)),
            "parent_id": str(parent_channel_id),
            **payload,
        }
        self.channels.append(created)
        self.created_channels.append(created)
        return created

    def delete_channel(self, channel_id: int) -> None:
        self.deleted_channels.append(channel_id)
        self.channels = [channel for channel in self.channels if int(channel["id"]) != channel_id]

    def send_message(self, channel_id: int, content: str) -> dict:
        message = {
            "id": str(7000 + len(self.sent_messages)),
            "channel_id": str(channel_id),
            "content": content,
        }
        self.sent_messages.append(message)
        return message

    def list_messages(self, channel_id: int, *, limit: int = 50) -> list[dict]:
        if not self.sent_messages:
            return []
        request = self.sent_messages[-1]
        return [
            {
                "id": str(8000 + len(self.sent_messages)),
                "author": {"id": "2222"},
                "message_reference": {"message_id": request["id"]},
            }
        ]

    def delete_message(self, channel_id: int, message_id: int) -> None:
        self.deleted_messages.append(message_id)


@pytest.fixture(autouse=True)
def _clear_e2e_author_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DISCORD_E2E_ALLOWED_AUTHOR_IDS", "1111")


def test_create_run_creates_channel_and_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "make_run_id", lambda prefix="discord": "discord-fixed")

    category = {"id": "456", "type": 4, "name": "zeth-e2e"}
    test_api = FakeDiscordAPI(user_id=1111, channels=[category])
    target_api = FakeDiscordAPI(user_id=2222, channels=[dict(category)])

    manifest, exports = module.create_run(
        runs_root=tmp_path,
        guild_id=123,
        category_id=456,
        category_name=None,
        parent_channel_id=None,
        channel_prefix="zeth-e2e",
        ttl_minutes=90,
        mode="local_required",
        test_api=test_api,
        target_api=target_api,
        parent_run_id="outer-run",
    )

    assert manifest["run_id"] == "discord-fixed"
    assert target_api.created_channels, "target bot should provision isolated channels"
    assert not test_api.created_channels
    assert manifest["channel"]["name"].startswith("zeth-e2e-")
    assert Path(manifest["cleanup_ledger_path"]).parent.is_dir()
    assert exports["TEST_DISCORD_CHANNEL_ID"] == str(manifest["channel"]["id"])
    assert exports["TEST_DISCORD_TARGET_BOT_ID"] == "2222"
    assert exports["DISCORD_E2E_TARGET_LEASE_STATUS"] == "acquired"
    lease = DiscordE2ELease.from_topic(manifest["channel"]["topic"])
    assert lease is not None
    assert lease.target_bot_id == 2222
    assert lease.author_id == 1111
    assert lease.parent_run_id == "outer-run"


def test_create_thread_run_creates_thread_and_exports(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = _load_module()
    monkeypatch.setattr(module, "make_run_id", lambda prefix="discord": "discord-thread")

    parent_channel = {"id": "321", "type": 0, "name": "fam"}
    test_api = FakeDiscordAPI(user_id=1111, channels=[parent_channel])
    target_api = FakeDiscordAPI(user_id=2222, channels=[dict(parent_channel)])

    manifest, exports = module.create_run(
        runs_root=tmp_path,
        guild_id=123,
        category_id=None,
        category_name=None,
        parent_channel_id=321,
        channel_prefix="zeth-e2e",
        ttl_minutes=90,
        mode="local_required",
        test_api=test_api,
        target_api=target_api,
    )

    assert manifest["resource_type"] == "thread"
    assert manifest["parent_channel_id"] == 321
    assert target_api.created_channels[0]["type"] == 11
    assert manifest["channel"]["name"].startswith("zeth-e2e-m-")
    assert exports["TEST_DISCORD_CHANNEL_ID"] == str(manifest["channel"]["id"])


def test_create_run_fails_when_target_bot_lease_is_active(tmp_path: Path) -> None:
    module = _load_module()
    future_lease = DiscordE2ELease(
        run_id="discord-existing",
        mode="local_required",
        target_bot_id=2222,
        author_id=1111,
        created_at=datetime.now(tz=UTC),
        expires_at=datetime.now(tz=UTC) + timedelta(minutes=15),
        guild_id=123,
        category_id=456,
        channel_prefix="zeth-e2e",
    )
    channels = [
        {"id": "456", "type": 4, "name": "zeth-e2e"},
        {
            "id": "789",
            "type": 0,
            "name": "zeth-e2e-discord-existing",
            "parent_id": "456",
            "topic": future_lease.to_topic(),
        },
    ]
    test_api = FakeDiscordAPI(user_id=1111, channels=[channels[0]])
    target_api = FakeDiscordAPI(user_id=2222, channels=channels)

    with pytest.raises(module.DiscordE2ERunManagerError, match="target_lease_unavailable"):
        module.create_run(
            runs_root=tmp_path,
            guild_id=123,
            category_id=456,
            category_name=None,
            parent_channel_id=None,
            channel_prefix="zeth-e2e",
            ttl_minutes=90,
            mode="local_required",
            test_api=test_api,
            target_api=target_api,
        )


def test_cleanup_run_replays_cleanup_prompts_and_deletes_channel(tmp_path: Path) -> None:
    module = _load_module()
    manifest_path = tmp_path / "manifests" / "discord-fixed.json"
    manifest_path.parent.mkdir(parents=True)
    cleanup_ledger_path = tmp_path / "cleanup-ledgers" / "discord-fixed.jsonl"
    cleanup_ledger_path.parent.mkdir(parents=True)
    cleanup_ledger_path.write_text(
        json.dumps({"label": "task:demo", "prompt": "Delete the task demo"}) + "\n",
        encoding="utf-8",
    )
    module.write_manifest(
        manifest_path,
        {
            "run_id": "discord-fixed",
            "target_bot_id": 2222,
            "channel": {"id": "9001", "name": "zeth-e2e-discord-fixed"},
            "cleanup_ledger_path": str(cleanup_ledger_path),
            "lease": {"status": "active"},
            "cleanup": {"status": "pending"},
        },
    )

    test_api = FakeDiscordAPI(user_id=1111)
    admin_api = FakeDiscordAPI(user_id=2222)
    payload = module.cleanup_run(
        manifest_path=manifest_path,
        reason="unit_test_cleanup",
        test_api=test_api,
        admin_api=admin_api,
    )

    assert payload["cleanup"]["status"] == "cleaned"
    assert payload["cleanup"]["synthetic_cleanup"]["attempted"] == 1
    assert payload["cleanup"]["synthetic_cleanup"]["acknowledged"] == 1
    assert admin_api.deleted_channels == [9001]
    assert test_api.sent_messages[0]["content"].startswith("<@2222> Delete the task demo")
