#!/usr/bin/env python3
"""Manage isolated Discord E2E channels, leases, and synthetic cleanup."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import secrets
import sys
import time
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from zetherion_ai.discord.e2e_lease import DiscordE2ELease  # noqa: E402

DEFAULT_RUNS_ROOT = REPO_ROOT / ".artifacts" / "discord-e2e-runs"
DEFAULT_CHANNEL_PREFIX = "zeth-e2e"
DEFAULT_TTL_MINUTES = 180
DEFAULT_MODE = "local_required"
API_BASE_URL = "https://discord.com/api/v10"


TEST_BOT_CHANNEL_ALLOW = 1024 | 2048 | 8192 | 65536
TARGET_BOT_CHANNEL_ALLOW = 1024 | 2048 | 8192 | 16384 | 32768 | 65536


def _channel_permission_overwrites(*, test_bot_id: int, target_bot_id: int) -> list[dict[str, Any]]:
    return [
        {
            "id": str(test_bot_id),
            "type": 1,
            "allow": str(TEST_BOT_CHANNEL_ALLOW),
            "deny": "0",
        },
        {
            "id": str(target_bot_id),
            "type": 1,
            "allow": str(TARGET_BOT_CHANNEL_ALLOW),
            "deny": "0",
        },
    ]


@dataclass(frozen=True)
class RunPaths:
    manifest_path: Path
    cleanup_ledger_path: Path
    heartbeat_path: Path


class DiscordE2ERunManagerError(RuntimeError):
    """Raised when a Discord E2E run cannot be created or cleaned."""


class DiscordAPI:
    """Minimal Discord REST client for channel lifecycle and cleanup."""

    def __init__(self, token: str) -> None:
        if not token.strip():
            raise DiscordE2ERunManagerError("Discord token is required")
        self._client = httpx.Client(
            base_url=API_BASE_URL,
            headers={
                "Authorization": f"Bot {token.strip()}",
                "User-Agent": "ZetherionDiscordE2ERunManager/1.0",
            },
            timeout=httpx.Timeout(30.0, connect=15.0),
        )

    def close(self) -> None:
        self._client.close()

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        response = self._client.request(method, path, **kwargs)
        if response.status_code >= 400:
            raise DiscordE2ERunManagerError(
                f"Discord API {method} {path} failed: "
                f"{response.status_code} {response.text.strip()}"
            )
        if response.content:
            return response.json()
        return None

    def get_current_user(self) -> dict[str, Any]:
        payload = self._request("GET", "/users/@me")
        if not isinstance(payload, dict):
            raise DiscordE2ERunManagerError("Discord /users/@me response was not an object")
        return payload

    def list_guild_channels(self, guild_id: int) -> list[dict[str, Any]]:
        payload = self._request("GET", f"/guilds/{guild_id}/channels")
        if not isinstance(payload, list):
            raise DiscordE2ERunManagerError("Discord guild channel response was not a list")
        return [channel for channel in payload if isinstance(channel, dict)]

    def create_guild_channel(self, guild_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._request("POST", f"/guilds/{guild_id}/channels", json=payload)
        if not isinstance(response, dict):
            raise DiscordE2ERunManagerError("Discord create channel response was not an object")
        return response

    def create_thread(self, parent_channel_id: int, payload: dict[str, Any]) -> dict[str, Any]:
        response = self._request("POST", f"/channels/{parent_channel_id}/threads", json=payload)
        if not isinstance(response, dict):
            raise DiscordE2ERunManagerError("Discord create thread response was not an object")
        return response

    def delete_channel(self, channel_id: int) -> None:
        self._request("DELETE", f"/channels/{channel_id}")

    def send_message(self, channel_id: int, content: str) -> dict[str, Any]:
        response = self._request(
            "POST", f"/channels/{channel_id}/messages", json={"content": content}
        )
        if not isinstance(response, dict):
            raise DiscordE2ERunManagerError("Discord send message response was not an object")
        return response

    def list_messages(self, channel_id: int, *, limit: int = 50) -> list[dict[str, Any]]:
        payload = self._request("GET", f"/channels/{channel_id}/messages", params={"limit": limit})
        if not isinstance(payload, list):
            raise DiscordE2ERunManagerError("Discord channel history response was not a list")
        return [message for message in payload if isinstance(message, dict)]

    def delete_message(self, channel_id: int, message_id: int) -> None:
        self._request("DELETE", f"/channels/{channel_id}/messages/{message_id}")


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).isoformat()


def make_run_id(prefix: str = "discord") -> str:
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(3)}"


def build_paths(runs_root: Path, run_id: str) -> RunPaths:
    return RunPaths(
        manifest_path=runs_root / "manifests" / f"{run_id}.json",
        cleanup_ledger_path=runs_root / "cleanup-ledgers" / f"{run_id}.jsonl",
        heartbeat_path=runs_root / "heartbeats" / f"{run_id}.touch",
    )


def ensure_layout(runs_root: Path) -> None:
    (runs_root / "manifests").mkdir(parents=True, exist_ok=True)
    (runs_root / "cleanup-ledgers").mkdir(parents=True, exist_ok=True)
    (runs_root / "heartbeats").mkdir(parents=True, exist_ok=True)


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise DiscordE2ERunManagerError(f"manifest must be a JSON object: {path}")
    return payload


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def touch_heartbeat(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.touch()


def resolve_heartbeat_stale_seconds() -> int:
    raw = str(os.environ.get("TEST_DISCORD_E2E_HEARTBEAT_STALE_SECONDS", "300")).strip()
    try:
        value = int(raw)
    except ValueError:
        return 300
    return max(value, 60)


def render_shell_exports(values: dict[str, str]) -> str:
    return "\n".join(f"export {key}={_shell_quote(value)}" for key, value in sorted(values.items()))


def _shell_quote(value: str) -> str:
    return "'" + value.replace("'", "'\\''") + "'"


def resolve_category(
    api: DiscordAPI,
    *,
    guild_id: int,
    category_id: int | None,
    category_name: str | None,
) -> dict[str, Any]:
    channels = api.list_guild_channels(guild_id)
    if category_id is not None:
        for channel in channels:
            if int(channel.get("id", 0)) == category_id and int(channel.get("type", -1)) == 4:
                return channel
        raise DiscordE2ERunManagerError(
            f"Discord category {category_id} was not found in guild {guild_id}"
        )

    normalized_name = (category_name or "").strip()
    if not normalized_name:
        raise DiscordE2ERunManagerError(
            "Either TEST_DISCORD_E2E_CATEGORY_ID or TEST_DISCORD_E2E_CATEGORY_NAME is required"
        )

    for channel in channels:
        if int(channel.get("type", -1)) == 4 and str(channel.get("name", "")) == normalized_name:
            return channel

    return api.create_guild_channel(guild_id, {"name": normalized_name, "type": 4})


def resolve_parent_channel(
    api: DiscordAPI,
    *,
    guild_id: int,
    parent_channel_id: int,
) -> dict[str, Any]:
    for channel in api.list_guild_channels(guild_id):
        if int(channel.get("id", 0)) == parent_channel_id and int(channel.get("type", -1)) == 0:
            return channel
    raise DiscordE2ERunManagerError(
        f"Discord parent channel {parent_channel_id} was not found in guild {guild_id}"
    )


def list_run_channels(
    api: DiscordAPI,
    *,
    guild_id: int,
    channel_prefix: str,
    category_id: int | None = None,
    parent_channel_id: int | None = None,
) -> list[dict[str, Any]]:
    matches: list[dict[str, Any]] = []
    normalized_prefix = channel_prefix.strip().lower()
    for channel in api.list_guild_channels(guild_id):
        channel_type = int(channel.get("type", -1))
        if category_id is not None:
            if channel_type != 0:
                continue
            if int(channel.get("parent_id", 0) or 0) != category_id:
                continue
        elif parent_channel_id is not None:
            if channel_type not in {11, 12}:
                continue
            if int(channel.get("parent_id", 0) or 0) != parent_channel_id:
                continue
        else:
            continue
        name = str(channel.get("name", ""))
        if not name.lower().startswith(normalized_prefix):
            continue
        lease = DiscordE2ELease.from_channel_metadata(
            topic=channel.get("topic"),
            name=name,
            channel_prefix=channel_prefix,
            guild_id=guild_id,
        )
        if lease is None:
            continue
        matches.append({"channel": channel, "lease": lease})
    return matches


def ensure_author_allowlist(test_bot_id: int) -> None:
    raw = str(os.environ.get("DISCORD_E2E_ALLOWED_AUTHOR_IDS", "")).strip()
    if not raw:
        raise DiscordE2ERunManagerError(
            "DISCORD_E2E_ALLOWED_AUTHOR_IDS must include the test bot user ID "
            "for synthetic E2E runs"
        )
    allowed = {int(part.strip()) for part in raw.split(",") if part.strip()}
    if test_bot_id not in allowed:
        raise DiscordE2ERunManagerError(
            f"DISCORD_E2E_ALLOWED_AUTHOR_IDS does not include test bot user ID {test_bot_id}"
        )


def create_run(
    *,
    runs_root: Path,
    guild_id: int,
    category_id: int | None,
    category_name: str | None,
    parent_channel_id: int | None,
    channel_prefix: str,
    ttl_minutes: int,
    mode: str,
    test_api: DiscordAPI,
    target_api: DiscordAPI,
    admin_api: DiscordAPI,
    parent_run_id: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    ensure_layout(runs_root)

    test_bot = test_api.get_current_user()
    target_bot = target_api.get_current_user()
    admin_bot = admin_api.get_current_user()
    test_bot_id = int(test_bot["id"])
    admin_bot_id = int(admin_bot["id"])
    if mode == "windows_prod_canary":
        target_bot_id = int(target_bot["id"])
    else:
        target_bot_id = int(os.environ.get("TEST_DISCORD_TARGET_BOT_ID") or target_bot["id"])
    ensure_author_allowlist(test_bot_id)

    resource_type = "thread" if parent_channel_id is not None else "channel"
    resolved_category_id: int | None = None
    resolved_parent_channel_id: int | None = None
    if resource_type == "thread":
        parent_channel = resolve_parent_channel(
            admin_api,
            guild_id=guild_id,
            parent_channel_id=parent_channel_id,
        )
        resolved_parent_channel_id = int(parent_channel["id"])
    else:
        category = resolve_category(
            admin_api,
            guild_id=guild_id,
            category_id=category_id,
            category_name=category_name,
        )
        resolved_category_id = int(category["id"])

    now = _now()
    conflicts = list_run_channels(
        admin_api,
        guild_id=guild_id,
        category_id=resolved_category_id,
        parent_channel_id=resolved_parent_channel_id,
        channel_prefix=channel_prefix,
    )
    for entry in conflicts:
        lease = entry["lease"]
        if lease.target_bot_id == target_bot_id and lease.is_active(now=now):
            raise DiscordE2ERunManagerError(
                "target_lease_unavailable: active run "
                f"{lease.run_id} already holds target bot {target_bot_id}"
            )

    run_id = make_run_id()
    paths = build_paths(runs_root, run_id)
    paths.cleanup_ledger_path.parent.mkdir(parents=True, exist_ok=True)
    created_at = now
    expires_at = now + timedelta(minutes=ttl_minutes)
    heartbeat_stale_seconds = resolve_heartbeat_stale_seconds()
    lease = DiscordE2ELease(
        run_id=run_id,
        mode=mode,
        target_bot_id=target_bot_id,
        author_id=test_bot_id,
        created_at=created_at,
        expires_at=expires_at,
        guild_id=guild_id,
        category_id=resolved_category_id,
        channel_prefix=channel_prefix,
        parent_run_id=parent_run_id,
    )

    if resource_type == "thread":
        resource_name = lease.to_thread_name()
        created_channel = admin_api.create_thread(
            resolved_parent_channel_id,
            {
                "name": resource_name,
                "auto_archive_duration": 60,
                "type": 11,
            },
        )
    else:
        resource_name = f"{channel_prefix}-{run_id}"[:100]
        created_channel = admin_api.create_guild_channel(
            guild_id,
            {
                "name": resource_name,
                "type": 0,
                "parent_id": str(resolved_category_id),
                "topic": lease.to_topic(),
                "permission_overwrites": _channel_permission_overwrites(
                    test_bot_id=test_bot_id,
                    target_bot_id=target_bot_id,
                ),
            },
        )
    channel_id = int(created_channel["id"])
    touch_heartbeat(paths.heartbeat_path)

    manifest: dict[str, Any] = {
        "version": 1,
        "run_id": run_id,
        "mode": mode,
        "resource_type": resource_type,
        "guild_id": guild_id,
        "category_id": resolved_category_id,
        "parent_channel_id": resolved_parent_channel_id,
        "channel_prefix": channel_prefix,
        "test_bot_id": test_bot_id,
        "target_bot_id": target_bot_id,
        "admin_bot_id": admin_bot_id,
        "channel": {
            "id": channel_id,
            "name": str(created_channel.get("name", resource_name)),
            "topic": str(
                created_channel.get("topic", lease.to_topic() if resource_type == "channel" else "")
            ),
        },
        "cleanup_ledger_path": str(paths.cleanup_ledger_path),
        "runtime": {
            "heartbeat_path": str(paths.heartbeat_path),
            "heartbeat_stale_seconds": heartbeat_stale_seconds,
        },
        "lease": {
            **lease.to_payload(),
            "status": "active",
        },
        "cleanup": {
            "status": "pending",
            "reason": "",
            "executed_at": "",
            "channel_deleted": False,
            "synthetic_cleanup": {
                "status": "pending",
                "attempted": 0,
                "acknowledged": 0,
                "failed": 0,
                "details": [],
            },
        },
    }
    write_manifest(paths.manifest_path, manifest)

    exports = {
        "DISCORD_E2E_RUN_ID": run_id,
        "DISCORD_E2E_RUN_MANIFEST_PATH": str(paths.manifest_path),
        "DISCORD_E2E_CLEANUP_LEDGER_PATH": str(paths.cleanup_ledger_path),
        "DISCORD_E2E_HEARTBEAT_PATH": str(paths.heartbeat_path),
        "DISCORD_E2E_CHANNEL_ID": str(channel_id),
        "DISCORD_E2E_CHANNEL_NAME": str(created_channel.get("name", resource_name)),
        "DISCORD_E2E_TARGET_BOT_ID": str(target_bot_id),
        "DISCORD_E2E_TEST_BOT_ID": str(test_bot_id),
        "DISCORD_E2E_TARGET_LEASE_STATUS": "acquired",
        "DISCORD_E2E_MODE": mode,
        "TEST_DISCORD_CHANNEL_ID": str(channel_id),
        "TEST_DISCORD_TARGET_BOT_ID": str(target_bot_id),
    }
    return manifest, exports


def _parse_lease_created_at(payload: dict[str, Any]) -> datetime | None:
    lease_payload = payload.get("lease", {})
    if not isinstance(lease_payload, dict):
        return None
    raw = lease_payload.get("created_at")
    if raw in (None, ""):
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=UTC)
        return parsed.astimezone(UTC)
    except (TypeError, ValueError):
        return None


def _is_manifest_heartbeat_stale(payload: dict[str, Any], *, now: datetime) -> bool:
    runtime = payload.get("runtime", {})
    if not isinstance(runtime, dict):
        runtime = {}
    stale_seconds = runtime.get("heartbeat_stale_seconds", resolve_heartbeat_stale_seconds())
    try:
        stale_seconds = max(int(stale_seconds), 60)
    except (TypeError, ValueError):
        stale_seconds = 300
    cutoff = now - timedelta(seconds=stale_seconds)

    heartbeat_path_raw = str(runtime.get("heartbeat_path", "")).strip()
    if heartbeat_path_raw:
        heartbeat_path = Path(heartbeat_path_raw)
        if heartbeat_path.is_file():
            heartbeat_at = datetime.fromtimestamp(heartbeat_path.stat().st_mtime, tz=UTC)
            return heartbeat_at < cutoff

    created_at = _parse_lease_created_at(payload)
    return created_at is not None and created_at < cutoff


def _is_orphan_lease_stale(lease: DiscordE2ELease, *, now: datetime) -> bool:
    stale_seconds = resolve_heartbeat_stale_seconds()
    cutoff = now - timedelta(seconds=stale_seconds)
    return lease.created_at.astimezone(UTC) < cutoff


def _normalize_message_id(raw: Any) -> int:
    return int(str(raw))


def wait_for_reply(
    api: DiscordAPI,
    *,
    channel_id: int,
    target_bot_id: int,
    request_message_id: int,
    timeout_seconds: int = 45,
) -> dict[str, Any] | None:
    deadline = time.monotonic() + timeout_seconds
    while time.monotonic() < deadline:
        for message in api.list_messages(channel_id, limit=50):
            author = message.get("author") or {}
            reference = message.get("message_reference") or {}
            if int(author.get("id", 0) or 0) != target_bot_id:
                continue
            if int(reference.get("message_id", 0) or 0) != request_message_id:
                continue
            return message
        time.sleep(2)
    return None


def _iter_cleanup_entries(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    entries: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        payload = json.loads(line)
        if isinstance(payload, dict):
            entries.append(payload)
    return entries


def process_cleanup_ledger(
    api: DiscordAPI,
    *,
    channel_id: int,
    target_bot_id: int,
    ledger_path: Path,
    delete_api: DiscordAPI | None = None,
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    delete_api = delete_api or api
    entries = _iter_cleanup_entries(ledger_path)
    attempted = 0
    acknowledged = 0
    failed = 0

    for entry in entries:
        prompt = str(entry.get("prompt", "")).strip()
        if not prompt:
            continue
        attempted += 1
        detail: dict[str, Any] = {
            "label": str(entry.get("label", "cleanup_prompt")),
            "prompt": prompt,
            "status": "pending",
        }
        try:
            sent = api.send_message(channel_id, f"<@{target_bot_id}> {prompt}")
            sent_id = _normalize_message_id(sent["id"])
            reply = wait_for_reply(
                api,
                channel_id=channel_id,
                target_bot_id=target_bot_id,
                request_message_id=sent_id,
            )
            if reply is None:
                failed += 1
                detail["status"] = "timed_out"
            else:
                acknowledged += 1
                detail["status"] = "acknowledged"
                detail["reply_id"] = str(reply.get("id", ""))
                with contextlib.suppress(Exception):
                    delete_api.delete_message(channel_id, _normalize_message_id(reply["id"]))
            with contextlib.suppress(Exception):
                delete_api.delete_message(channel_id, sent_id)
        except Exception as exc:  # noqa: BLE001
            failed += 1
            detail["status"] = "failed"
            detail["error"] = str(exc)
        details.append(detail)

    status = "cleaned" if failed == 0 else ("partial" if acknowledged > 0 else "failed")
    return {
        "status": status,
        "attempted": attempted,
        "acknowledged": acknowledged,
        "failed": failed,
        "details": details,
    }


def cleanup_run(
    *,
    manifest_path: Path,
    reason: str,
    test_api: DiscordAPI | None,
    admin_api: DiscordAPI | None = None,
) -> dict[str, Any]:
    payload = load_manifest(manifest_path)
    cleanup = payload.setdefault("cleanup", {})
    if cleanup.get("status") == "cleaned":
        return payload

    channel_id = int(payload.get("channel", {}).get("id", 0) or 0)
    target_bot_id = int(payload.get("target_bot_id", 0) or 0)
    ledger_path = Path(payload.get("cleanup_ledger_path", ""))
    runtime = payload.get("runtime", {})
    heartbeat_path_raw = runtime.get("heartbeat_path", "") if isinstance(runtime, dict) else ""
    heartbeat_path = Path(str(heartbeat_path_raw)) if str(heartbeat_path_raw).strip() else None
    synthetic_cleanup = {
        "status": "not_run",
        "attempted": 0,
        "acknowledged": 0,
        "failed": 0,
        "details": [],
    }
    channel_deleted = False
    errors: list[str] = []

    delete_api = admin_api or test_api
    if test_api is not None and channel_id:
        try:
            synthetic_cleanup = process_cleanup_ledger(
                test_api,
                channel_id=channel_id,
                target_bot_id=target_bot_id,
                ledger_path=ledger_path,
                delete_api=delete_api,
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"synthetic_cleanup_failed: {exc}")
    if delete_api is not None and channel_id:
        try:
            delete_api.delete_channel(channel_id)
            channel_deleted = True
        except Exception as exc:  # noqa: BLE001
            errors.append(f"channel_delete_failed: {exc}")

    cleanup_status = (
        "cleaned"
        if channel_deleted and not errors
        else (
            "partial"
            if channel_deleted or synthetic_cleanup.get("acknowledged", 0) > 0
            else "failed"
        )
    )
    cleanup.update(
        {
            "status": cleanup_status,
            "reason": reason,
            "executed_at": _iso(_now()),
            "channel_deleted": channel_deleted,
            "synthetic_cleanup": synthetic_cleanup,
            "errors": errors,
        }
    )
    lease = payload.setdefault("lease", {})
    lease["status"] = cleanup_status
    if heartbeat_path is not None:
        with contextlib.suppress(Exception):
            heartbeat_path.unlink()
    write_manifest(manifest_path, payload)
    return payload


def janitor(
    *,
    runs_root: Path,
    guild_id: int,
    category_id: int | None,
    category_name: str | None,
    parent_channel_id: int | None,
    channel_prefix: str,
    test_api: DiscordAPI | None,
    admin_api: DiscordAPI | None = None,
) -> dict[str, Any]:
    ensure_layout(runs_root)
    cleaned: list[dict[str, Any]] = []
    skipped: list[str] = []
    errors: list[str] = []
    now = _now()

    manifests_dir = runs_root / "manifests"
    for manifest_path in sorted(manifests_dir.glob("*.json")):
        try:
            payload = load_manifest(manifest_path)
            lease = DiscordE2ELease.from_payload(payload.get("lease", {}))
            if (
                lease is None
                or not lease.is_active(now=now)
                or _is_manifest_heartbeat_stale(payload, now=now)
            ):
                reason = (
                    "stale_discord_e2e_heartbeat"
                    if lease is not None
                    and lease.is_active(now=now)
                    and _is_manifest_heartbeat_stale(payload, now=now)
                    else "stale_discord_e2e_run"
                )
                cleaned.append(
                    cleanup_run(
                        manifest_path=manifest_path,
                        reason=reason,
                        test_api=test_api,
                        admin_api=admin_api,
                    )
                )
            else:
                skipped.append(str(manifest_path))
        except Exception as exc:  # noqa: BLE001
            errors.append(f"{manifest_path.name}: {exc}")

    channel_api = admin_api or test_api
    if channel_api is not None:
        try:
            resolved_category_id = None
            resolved_parent_channel_id = None
            if parent_channel_id is not None:
                parent_channel = resolve_parent_channel(
                    channel_api,
                    guild_id=guild_id,
                    parent_channel_id=parent_channel_id,
                )
                resolved_parent_channel_id = int(parent_channel["id"])
            else:
                category = resolve_category(
                    channel_api,
                    guild_id=guild_id,
                    category_id=category_id,
                    category_name=category_name,
                )
                resolved_category_id = int(category["id"])
            for entry in list_run_channels(
                channel_api,
                guild_id=guild_id,
                category_id=resolved_category_id,
                parent_channel_id=resolved_parent_channel_id,
                channel_prefix=channel_prefix,
            ):
                lease = entry["lease"]
                channel = entry["channel"]
                channel_id = int(channel["id"])
                matching_manifest = manifests_dir / f"{lease.run_id}.json"
                manifest_exists = matching_manifest.exists()
                if manifest_exists and lease.is_active(now=now):
                    continue
                if lease.is_active(now=now) and not _is_orphan_lease_stale(lease, now=now):
                    continue
                try:
                    channel_api.delete_channel(channel_id)
                    reason = (
                        "orphaned_stale_discord_channel"
                        if not lease.is_active(now=now)
                        else "orphaned_stale_active_discord_channel"
                    )
                    cleaned.append(
                        {
                            "run_id": lease.run_id,
                            "cleanup": {
                                "status": "cleaned",
                                "reason": reason,
                                "executed_at": _iso(now),
                            },
                        }
                    )
                except Exception as exc:  # noqa: BLE001
                    errors.append(f"orphan channel {channel_id}: {exc}")
        except DiscordE2ERunManagerError:
            # Category may not exist yet. That is not a janitor failure.
            pass

    return {"cleaned": cleaned, "skipped": skipped, "errors": errors}


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start")
    start_parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    start_parser.add_argument("--guild-id", required=True, type=int)
    start_parser.add_argument("--category-id", type=int)
    start_parser.add_argument("--category-name")
    start_parser.add_argument("--parent-channel-id", type=int)
    start_parser.add_argument("--channel-prefix", default=DEFAULT_CHANNEL_PREFIX)
    start_parser.add_argument("--ttl-minutes", type=int, default=DEFAULT_TTL_MINUTES)
    start_parser.add_argument("--mode", default=DEFAULT_MODE)
    start_parser.add_argument("--shell", action="store_true")

    cleanup_parser = subparsers.add_parser("cleanup")
    cleanup_parser.add_argument("--manifest", required=True)
    cleanup_parser.add_argument("--reason", default="explicit_cleanup")

    janitor_parser = subparsers.add_parser("janitor")
    janitor_parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    janitor_parser.add_argument("--guild-id", required=True, type=int)
    janitor_parser.add_argument("--category-id", type=int)
    janitor_parser.add_argument("--category-name")
    janitor_parser.add_argument("--parent-channel-id", type=int)
    janitor_parser.add_argument("--channel-prefix", default=DEFAULT_CHANNEL_PREFIX)
    return parser


def _build_test_api() -> DiscordAPI:
    token = str(os.environ.get("TEST_DISCORD_BOT_TOKEN", "")).strip()
    if not token:
        raise DiscordE2ERunManagerError("TEST_DISCORD_BOT_TOKEN is required")
    return DiscordAPI(token)


def _resolve_target_token(*, mode: str | None = None) -> str:
    run_mode = str(mode or os.environ.get("DISCORD_E2E_MODE", "")).strip().lower()
    if run_mode == "windows_prod_canary":
        token = str(os.environ.get("DISCORD_TOKEN", "")).strip()
        if not token:
            raise DiscordE2ERunManagerError(
                "DISCORD_TOKEN is required for windows_prod_canary target provisioning"
            )
        return token

    token = str(
        os.environ.get("DISCORD_TOKEN_TEST", "") or os.environ.get("DISCORD_TOKEN", "")
    ).strip()
    if not token:
        raise DiscordE2ERunManagerError("DISCORD_TOKEN_TEST or DISCORD_TOKEN is required")
    return token


def _build_target_api() -> DiscordAPI:
    return DiscordAPI(_resolve_target_token())


def _resolve_admin_token() -> str:
    token = str(
        os.environ.get("DISCORD_E2E_ADMIN_TOKEN", "")
        or os.environ.get("DISCORD_TOKEN", "")
        or os.environ.get("TEST_DISCORD_BOT_TOKEN", "")
    ).strip()
    if not token:
        raise DiscordE2ERunManagerError(
            "DISCORD_E2E_ADMIN_TOKEN, DISCORD_TOKEN, or TEST_DISCORD_BOT_TOKEN is required"
        )
    return token


def _build_admin_api() -> DiscordAPI:
    return DiscordAPI(_resolve_admin_token())


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "start":
        test_api = _build_test_api()
        target_api = _build_target_api()
        admin_api = _build_admin_api()
        try:
            manifest, exports = create_run(
                runs_root=Path(args.runs_root),
                guild_id=args.guild_id,
                category_id=args.category_id,
                category_name=args.category_name,
                parent_channel_id=args.parent_channel_id,
                channel_prefix=args.channel_prefix,
                ttl_minutes=args.ttl_minutes,
                mode=args.mode,
                test_api=test_api,
                target_api=target_api,
                admin_api=admin_api,
                parent_run_id=str(os.environ.get("E2E_RUN_ID", "")).strip() or None,
            )
            if args.shell:
                print(render_shell_exports(exports))
            else:
                print(
                    json.dumps({"manifest": manifest, "exports": exports}, indent=2, sort_keys=True)
                )
            return 0
        finally:
            test_api.close()
            target_api.close()
            admin_api.close()

    if args.command == "cleanup":
        test_api = _build_test_api()
        admin_api = _build_admin_api()
        try:
            payload = cleanup_run(
                manifest_path=Path(args.manifest),
                reason=args.reason,
                test_api=test_api,
                admin_api=admin_api,
            )
            print(json.dumps(payload, indent=2, sort_keys=True))
            return 0
        finally:
            test_api.close()
            admin_api.close()

    if args.command == "janitor":
        test_api = _build_test_api()
        admin_api = _build_admin_api()
        try:
            result = janitor(
                runs_root=Path(args.runs_root),
                guild_id=args.guild_id,
                category_id=args.category_id,
                category_name=args.category_name,
                parent_channel_id=args.parent_channel_id,
                channel_prefix=args.channel_prefix,
                test_api=test_api,
                admin_api=admin_api,
            )
            print(json.dumps(result, indent=2, sort_keys=True))
            return 0
        finally:
            test_api.close()
            admin_api.close()

    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except DiscordE2ERunManagerError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc
