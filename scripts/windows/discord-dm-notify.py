#!/usr/bin/env python3
"""Non-blocking Discord DM notifier for deploy/promotions completion events."""

from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import datetime as dt
import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

DEFAULT_STATE_PATH = Path(r"C:\ZetherionAI\data\promotions\notifications-state.json")
DEFAULT_SECRETS_PATH = Path(r"C:\ZetherionAI\data\secrets\promotions.bin")


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _is_enabled(raw: str | None) -> bool:
    if raw is None:
        return False
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _read_json(path: Path, *, default: dict[str, Any]) -> dict[str, Any]:
    if not path.exists():
        return default
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default
    if isinstance(payload, dict):
        return payload
    return default


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=True) + "\n", encoding="utf-8")


class _DataBlob(ctypes.Structure):
    _fields_ = [
        ("cbData", ctypes.wintypes.DWORD),
        ("pbData", ctypes.POINTER(ctypes.c_byte)),
    ]


def _dpapi_unprotect(cipher: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI decode is only available on Windows.")

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(cipher, len(cipher))
    in_blob = _DataBlob(
        cbData=len(cipher),
        pbData=ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    out_blob = _DataBlob()

    ok = crypt32.CryptUnprotectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptUnprotectData failed")

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


def _load_promotions_secrets(path: Path) -> tuple[dict[str, Any], str | None]:
    if not path.exists():
        return {}, f"secrets file not found: {path}"
    try:
        cipher = path.read_bytes()
    except OSError as exc:
        return {}, f"unable to read secrets file: {exc}"
    if not cipher:
        return {}, "secrets file is empty"

    try:
        plain = _dpapi_unprotect(cipher).decode("utf-8")
        payload = json.loads(plain)
    except Exception as exc:  # noqa: BLE001
        return {}, f"unable to decode secrets blob: {exc}"

    secrets = payload.get("secrets")
    if not isinstance(secrets, dict):
        return {}, "secrets payload missing 'secrets' object"
    return secrets, None


def _post_json(
    url: str,
    *,
    payload: dict[str, Any],
    headers: dict[str, str],
    timeout: int = 20,
) -> tuple[int, str]:
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(url, data=body, method="POST")
    request.add_header("Content-Type", "application/json")
    for key, value in headers.items():
        request.add_header(key, value)

    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return 0, str(exc)


def _normalize_id(raw: str | None) -> str:
    return (raw or "").strip()


def _resolve_recipient_id(
    *,
    override_id: str,
    secrets: dict[str, Any],
) -> str:
    if override_id:
        return override_id

    notify_id = _normalize_id(str(secrets.get("DISCORD_NOTIFY_USER_ID", "")))
    if notify_id:
        return notify_id

    env_notify_id = _normalize_id(os.environ.get("DISCORD_NOTIFY_USER_ID"))
    if env_notify_id:
        return env_notify_id

    owner_secret = _normalize_id(str(secrets.get("OWNER_USER_ID", "")))
    if owner_secret:
        return owner_secret

    return _normalize_id(os.environ.get("OWNER_USER_ID"))


def _resolve_bot_token(
    *,
    override_token: str,
    secrets: dict[str, Any],
) -> str:
    if override_token:
        return override_token

    secret_token = _normalize_id(str(secrets.get("DISCORD_BOT_TOKEN", "")))
    if secret_token:
        return secret_token

    return _normalize_id(os.environ.get("DISCORD_BOT_TOKEN"))


def _resolve_enabled(
    *,
    override_enabled: str | None,
    secrets: dict[str, Any],
) -> bool:
    if override_enabled is not None:
        return _is_enabled(override_enabled)
    secret_enabled = secrets.get("DISCORD_DM_NOTIFY_ENABLED")
    if isinstance(secret_enabled, str):
        return _is_enabled(secret_enabled)
    if secret_enabled is not None:
        return _is_enabled(str(secret_enabled))
    return _is_enabled(os.environ.get("DISCORD_DM_NOTIFY_ENABLED"))


def _default_message(
    *,
    event: str,
    sha: str,
    status: str,
    run_url: str,
    stage_results: str,
) -> str:
    parts = [f"Zetherion {event} status: {status}", f"SHA: {sha}"]
    if run_url:
        parts.append(f"Run: {run_url}")
    if stage_results:
        parts.append(f"Details: {stage_results}")
    return " | ".join(parts)


def _build_idempotency_key(*, event: str, sha: str, status: str, explicit: str | None) -> str:
    if explicit:
        return explicit.strip()
    return f"{event}:{sha}:{status}".lower()


def _emit_status(status: str, *, idempotency_key: str = "") -> None:
    payload = {
        "generated_at": _now_iso(),
        "status": status,
    }
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    print(json.dumps(payload, ensure_ascii=True))


def _send_discord_dm(*, bot_token: str, recipient_id: str, message: str) -> tuple[bool, str]:
    headers = {"Authorization": f"Bot {bot_token}"}

    channel_status, channel_body = _post_json(
        "https://discord.com/api/v10/users/@me/channels",
        payload={"recipient_id": recipient_id},
        headers=headers,
    )
    if channel_status < 200 or channel_status >= 300:
        return False, f"create_dm_failed:{channel_status}:{channel_body}"

    try:
        channel_payload = json.loads(channel_body) if channel_body else {}
    except json.JSONDecodeError:
        return False, f"create_dm_invalid_json:{channel_body}"

    if not isinstance(channel_payload, dict):
        return False, "create_dm_invalid_payload"
    channel_id = _normalize_id(str(channel_payload.get("id", "")))
    if not channel_id:
        return False, "create_dm_missing_channel_id"

    message_status, message_body = _post_json(
        f"https://discord.com/api/v10/channels/{channel_id}/messages",
        payload={"content": message},
        headers=headers,
    )
    if message_status < 200 or message_status >= 300:
        return False, f"send_message_failed:{message_status}:{message_body}"

    return True, message_body


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", required=True, choices=["deploy", "promotions"])
    parser.add_argument("--sha", required=True)
    parser.add_argument("--status", required=True)
    parser.add_argument("--message", default="")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--stage-results", default="")
    parser.add_argument("--idempotency-key", default="")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--secrets-path", default=str(DEFAULT_SECRETS_PATH))
    parser.add_argument("--recipient-id", default="")
    parser.add_argument("--bot-token", default="")
    parser.add_argument("--enabled", default=None)
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    state_path = Path(args.state_path)
    secrets_path = Path(args.secrets_path)
    secrets, _secrets_warning = _load_promotions_secrets(secrets_path)

    enabled = _resolve_enabled(override_enabled=args.enabled, secrets=secrets)
    if not enabled:
        _emit_status("disabled")
        return 0

    bot_token = _resolve_bot_token(override_token=args.bot_token, secrets=secrets)
    if not bot_token:
        _emit_status("skipped_missing_token")
        return 0

    recipient_id = _resolve_recipient_id(
        override_id=_normalize_id(args.recipient_id),
        secrets=secrets,
    )
    if not recipient_id:
        _emit_status("skipped_missing_recipient")
        return 0

    key = _build_idempotency_key(
        event=args.event,
        sha=args.sha.lower(),
        status=args.status.strip().lower(),
        explicit=_normalize_id(args.idempotency_key) or None,
    )

    state = _read_json(state_path, default={"sent": {}})
    sent = state.get("sent")
    if not isinstance(sent, dict):
        sent = {}
    if key in sent:
        _emit_status("deduped", idempotency_key=key)
        return 0

    message = args.message.strip() or _default_message(
        event=args.event,
        sha=args.sha.lower(),
        status=args.status,
        run_url=args.run_url.strip(),
        stage_results=args.stage_results.strip(),
    )

    if args.dry_run:
        _emit_status("dry_run", idempotency_key=key)
        return 0

    ok, detail = _send_discord_dm(
        bot_token=bot_token,
        recipient_id=recipient_id,
        message=message,
    )
    if not ok:
        _emit_status("failed_non_blocking", idempotency_key=key)
        return 0

    sent[key] = {
        "sent_at": _now_iso(),
        "event": args.event,
        "sha": args.sha.lower(),
        "status": args.status,
    }
    state["sent"] = sent
    state["updated_at"] = _now_iso()
    _write_json(state_path, state)

    _emit_status("sent", idempotency_key=key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
