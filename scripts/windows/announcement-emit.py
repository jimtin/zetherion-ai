#!/usr/bin/env python3
"""Non-blocking announcement emitter for Windows deploy/promotions events."""

from __future__ import annotations

import argparse
import base64
import ctypes
import ctypes.wintypes
import datetime as dt
import json
import os
import re
import ssl
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

DEFAULT_STATE_PATH = Path(r"C:\ZetherionAI\data\announcements\notifications-state.json")
DEFAULT_SECRETS_PATH = Path(r"C:\ZetherionAI\data\secrets\promotions.bin")
DEFAULT_OUTBOX_DIR = Path(r"C:\ZetherionAI\data\announcements\outbox")
DEFAULT_API_URL = "https://127.0.0.1:8080/announcements/events"

SUCCESSFUL_RECEIPT_STATUSES = {"accepted", "deduped", "scheduled", "deferred"}
_OUTBOX_FORMAT = "zetherion_announcement_outbox_v1"
_OUTBOX_PBKDF2_ITERATIONS = 600_000
_OUTBOX_SALT_SIZE = 32
_OUTBOX_NONCE_SIZE = 12
_OUTBOX_KEY_SIZE = 32


def _now_iso() -> str:
    return dt.datetime.now(dt.UTC).isoformat()


def _normalize_id(raw: str | None) -> str:
    return (raw or "").strip()


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


def _dpapi_protect(plain: bytes) -> bytes:
    if os.name != "nt":
        raise RuntimeError("DPAPI encode is only available on Windows.")

    crypt32 = ctypes.windll.crypt32
    kernel32 = ctypes.windll.kernel32
    in_buffer = ctypes.create_string_buffer(plain, len(plain))
    in_blob = _DataBlob(
        cbData=len(plain),
        pbData=ctypes.cast(in_buffer, ctypes.POINTER(ctypes.c_byte)),
    )
    out_blob = _DataBlob()

    ok = crypt32.CryptProtectData(
        ctypes.byref(in_blob),
        None,
        None,
        None,
        None,
        0,
        ctypes.byref(out_blob),
    )
    if not ok:
        raise OSError(ctypes.get_last_error(), "CryptProtectData failed")

    try:
        return ctypes.string_at(out_blob.pbData, out_blob.cbData)
    finally:
        kernel32.LocalFree(out_blob.pbData)


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


def _derive_outbox_key(*, passphrase: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(
        algorithm=hashes.SHA256(),
        length=_OUTBOX_KEY_SIZE,
        salt=salt,
        iterations=_OUTBOX_PBKDF2_ITERATIONS,
    )
    return kdf.derive(passphrase.encode("utf-8"))


def _encode_outbox_payload(payload: dict[str, Any]) -> str:
    plain = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
    if os.name == "nt":
        envelope: dict[str, Any] = {
            "format": _OUTBOX_FORMAT,
            "encryption": "dpapi",
            "ciphertext": base64.b64encode(_dpapi_protect(plain)).decode("ascii"),
        }
    else:
        passphrase = os.environ.get("ENCRYPTION_PASSPHRASE", "").strip()
        if not passphrase:
            raise RuntimeError(
                "ENCRYPTION_PASSPHRASE is required to encrypt announcement outbox entries."
            )
        salt = os.urandom(_OUTBOX_SALT_SIZE)
        nonce = os.urandom(_OUTBOX_NONCE_SIZE)
        ciphertext = AESGCM(_derive_outbox_key(passphrase=passphrase, salt=salt)).encrypt(
            nonce,
            plain,
            None,
        )
        envelope = {
            "format": _OUTBOX_FORMAT,
            "encryption": "aesgcm",
            "iterations": _OUTBOX_PBKDF2_ITERATIONS,
            "salt": base64.b64encode(salt).decode("ascii"),
            "ciphertext": base64.b64encode(nonce + ciphertext).decode("ascii"),
        }
    return json.dumps(envelope, indent=2, ensure_ascii=True) + "\n"


def _decode_outbox_payload(text: str) -> dict[str, Any]:
    payload = json.loads(text)
    if not isinstance(payload, dict):
        raise ValueError("Outbox payload must decode to an object.")
    if payload.get("format") != _OUTBOX_FORMAT:
        return payload

    encryption = str(payload.get("encryption", "")).strip().lower()
    ciphertext_encoded = payload.get("ciphertext")
    if not isinstance(ciphertext_encoded, str):
        raise ValueError("Encrypted outbox payload is missing ciphertext.")
    cipher = base64.b64decode(ciphertext_encoded.encode("ascii"))
    if encryption == "dpapi":
        plain = _dpapi_unprotect(cipher)
    elif encryption == "aesgcm":
        passphrase = os.environ.get("ENCRYPTION_PASSPHRASE", "").strip()
        if not passphrase:
            raise RuntimeError(
                "ENCRYPTION_PASSPHRASE is required to read encrypted announcement outbox entries."
            )
        salt_encoded = payload.get("salt")
        if not isinstance(salt_encoded, str):
            raise ValueError("Encrypted outbox payload is missing salt.")
        salt = base64.b64decode(salt_encoded.encode("ascii"))
        nonce = cipher[:_OUTBOX_NONCE_SIZE]
        ciphertext = cipher[_OUTBOX_NONCE_SIZE:]
        plain = AESGCM(_derive_outbox_key(passphrase=passphrase, salt=salt)).decrypt(
            nonce,
            ciphertext,
            None,
        )
    else:
        raise ValueError(f"Unsupported outbox encryption mode: {encryption or 'missing'}")

    decoded = json.loads(plain.decode("utf-8"))
    if not isinstance(decoded, dict):
        raise ValueError("Decrypted outbox payload must decode to an object.")
    return decoded


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

    ssl_context = None
    if url.lower().startswith("https://"):
        ca_path = _normalize_id(os.environ.get("INTERNAL_TLS_CA_PATH"))
        cert_path = _normalize_id(os.environ.get("INTERNAL_TLS_CLIENT_CERT_PATH"))
        key_path = _normalize_id(os.environ.get("INTERNAL_TLS_CLIENT_KEY_PATH"))
        ssl_context = ssl.create_default_context(cafile=ca_path or None)
        if cert_path and key_path:
            ssl_context.load_cert_chain(certfile=cert_path, keyfile=key_path)

    try:
        with urllib.request.urlopen(request, timeout=timeout, context=ssl_context) as response:
            return response.status, response.read().decode("utf-8", errors="replace")
    except urllib.error.HTTPError as exc:
        return exc.code, exc.read().decode("utf-8", errors="replace")
    except urllib.error.URLError as exc:
        return 0, str(exc)


def _resolve_enabled(*, override_enabled: str | None, secrets: dict[str, Any]) -> bool:
    if override_enabled is not None:
        return _is_enabled(override_enabled)
    for key in ("ANNOUNCEMENT_EMIT_ENABLED", "DISCORD_DM_NOTIFY_ENABLED"):
        value = secrets.get(key)
        if value is not None:
            return _is_enabled(str(value))
    for key in ("ANNOUNCEMENT_EMIT_ENABLED", "DISCORD_DM_NOTIFY_ENABLED"):
        value = os.environ.get(key)
        if value is not None:
            return _is_enabled(value)
    return False


def _resolve_api_secret(*, override_secret: str, secrets: dict[str, Any]) -> str:
    if override_secret:
        return override_secret
    for key in ("ANNOUNCEMENT_API_SECRET", "SKILLS_API_SECRET"):
        value = _normalize_id(str(secrets.get(key, "")))
        if value:
            return value
    for key in ("ANNOUNCEMENT_API_SECRET", "SKILLS_API_SECRET"):
        value = _normalize_id(os.environ.get(key))
        if value:
            return value
    return ""


def _resolve_target_user_id(*, override_target: str, secrets: dict[str, Any]) -> str:
    if override_target:
        return override_target
    for key in ("ANNOUNCEMENT_TARGET_USER_ID", "DISCORD_NOTIFY_USER_ID", "OWNER_USER_ID"):
        value = _normalize_id(str(secrets.get(key, "")))
        if value:
            return value
    for key in ("ANNOUNCEMENT_TARGET_USER_ID", "DISCORD_NOTIFY_USER_ID", "OWNER_USER_ID"):
        value = _normalize_id(os.environ.get(key))
        if value:
            return value
    return ""


def _resolve_api_url(*, override_url: str, secrets: dict[str, Any]) -> str:
    for candidate in (
        _normalize_id(override_url),
        _normalize_id(str(secrets.get("ANNOUNCEMENT_API_URL", ""))),
        _normalize_id(os.environ.get("ANNOUNCEMENT_API_URL")),
        _normalize_id(os.environ.get("ZETHERION_SKILLS_API_BASE_URL")),
        DEFAULT_API_URL,
    ):
        if not candidate:
            continue
        base = candidate.rstrip("/")
        if base.endswith("/announcements/events"):
            return base
        return f"{base}/announcements/events"
    return DEFAULT_API_URL


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
        return explicit.strip().lower()
    return f"{event}:{sha}:{status}".lower()


def _emit_status(
    status: str,
    *,
    idempotency_key: str = "",
    detail: str = "",
    queued_path: str = "",
    flushed: int | None = None,
    pending: int | None = None,
) -> None:
    payload: dict[str, Any] = {
        "generated_at": _now_iso(),
        "status": status,
    }
    if idempotency_key:
        payload["idempotency_key"] = idempotency_key
    if detail:
        payload["detail"] = detail
    if queued_path:
        payload["queued_path"] = queued_path
    if flushed is not None:
        payload["flushed"] = flushed
    if pending is not None:
        payload["pending"] = pending
    print(json.dumps(payload, ensure_ascii=True))


def _coerce_target_user_id(raw: str) -> int | None:
    try:
        value = int(raw.strip())
    except ValueError:
        return None
    if value <= 0:
        return None
    return value


def _category_and_severity(*, event: str, status: str) -> tuple[str, str]:
    normalized = status.strip().lower()
    success = normalized in {"success", "ok", "completed", "done", "skipped_existing_success"}
    if event == "deploy":
        return ("deploy.completed", "normal") if success else ("deploy.failed", "critical")
    if event == "promotions":
        return ("promotions.completed", "normal") if success else ("promotions.failed", "critical")
    if success:
        return ("health.discord_canary", "normal")
    if normalized == "cleanup_degraded":
        return ("health.discord_canary", "high")
    return ("health.discord_canary", "critical")


def _title(*, event: str, status: str) -> str:
    if event == "deploy":
        event_title = "Deploy"
    elif event == "promotions":
        event_title = "Promotions"
    else:
        event_title = "Discord canary"
    return f"{event_title} status: {status.strip() or 'unknown'}"


def _sanitize_filename(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value).strip("-")
    if cleaned:
        return cleaned[:96]
    return "announcement"


def _queue_outbox_event(
    *,
    outbox_dir: Path,
    queue_payload: dict[str, Any],
    idempotency_key: str,
) -> Path:
    outbox_dir.mkdir(parents=True, exist_ok=True)
    stamp = dt.datetime.now(dt.UTC).strftime("%Y%m%dT%H%M%S%fZ")
    base_name = f"{stamp}-{_sanitize_filename(idempotency_key)}"
    candidate = outbox_dir / f"{base_name}.json"
    suffix = 1
    while candidate.exists():
        candidate = outbox_dir / f"{base_name}-{suffix}.json"
        suffix += 1
    candidate.write_text(_encode_outbox_payload(queue_payload), encoding="utf-8")
    return candidate


def _attempt_emit(
    *,
    api_url: str,
    api_secret: str,
    request_payload: dict[str, Any],
) -> tuple[bool, str]:
    status_code, body = _post_json(
        api_url,
        payload=request_payload,
        headers={"X-API-Secret": api_secret},
    )
    if status_code < 200 or status_code >= 300:
        return False, f"http_{status_code}:{body}"
    try:
        parsed = json.loads(body) if body else {}
    except json.JSONDecodeError:
        return False, "invalid_json_response"
    if not isinstance(parsed, dict):
        return False, "invalid_response_payload"
    receipt = parsed.get("receipt")
    if isinstance(receipt, dict):
        receipt_status = str(receipt.get("status", "")).strip().lower()
        if receipt_status in SUCCESSFUL_RECEIPT_STATUSES:
            return True, receipt_status
        if receipt_status:
            return False, f"unexpected_receipt_status:{receipt_status}"
    ok = parsed.get("ok")
    if isinstance(ok, bool):
        return ok, "ok_flag"
    return False, "missing_receipt_and_ok_flag"


def _mark_sent(
    *, state_path: Path, idempotency_key: str, event: str, sha: str, status: str
) -> None:
    state = _read_json(state_path, default={"sent": {}})
    sent = state.get("sent")
    if not isinstance(sent, dict):
        sent = {}
    sent[idempotency_key] = {
        "sent_at": _now_iso(),
        "event": event,
        "sha": sha,
        "status": status,
    }
    state["sent"] = sent
    state["updated_at"] = _now_iso()
    _write_json(state_path, state)


def _flush_outbox(
    *,
    outbox_dir: Path,
    api_secret: str,
    state_path: Path,
) -> tuple[int, int]:
    if not outbox_dir.exists():
        return 0, 0
    flushed = 0
    pending = 0
    for path in sorted(outbox_dir.glob("*.json")):
        try:
            payload = _decode_outbox_payload(path.read_text(encoding="utf-8"))
        except (OSError, ValueError, json.JSONDecodeError, RuntimeError):
            # Invalid queue entries are dropped to avoid infinite replay loops.
            path.unlink(missing_ok=True)
            continue
        if not isinstance(payload, dict):
            path.unlink(missing_ok=True)
            continue

        request_payload = payload.get("request_payload")
        api_url = _normalize_id(str(payload.get("api_url", "")))
        idempotency_key = _normalize_id(str(payload.get("idempotency_key", "")))
        event_name = _normalize_id(str(payload.get("event", "")))
        sha = _normalize_id(str(payload.get("sha", "")))
        status = _normalize_id(str(payload.get("status", "")))
        if not isinstance(request_payload, dict) or not api_url or not idempotency_key:
            path.unlink(missing_ok=True)
            continue

        ok, _detail = _attempt_emit(
            api_url=api_url,
            api_secret=api_secret,
            request_payload=request_payload,
        )
        if not ok:
            pending += 1
            continue

        _mark_sent(
            state_path=state_path,
            idempotency_key=idempotency_key,
            event=event_name,
            sha=sha,
            status=status,
        )
        path.unlink(missing_ok=True)
        flushed += 1
    return flushed, pending


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--event", choices=["deploy", "promotions", "discord_canary"], default="")
    parser.add_argument("--sha", default="")
    parser.add_argument("--status", default="")
    parser.add_argument("--message", default="")
    parser.add_argument("--run-url", default="")
    parser.add_argument("--stage-results", default="")
    parser.add_argument("--idempotency-key", default="")
    parser.add_argument("--state-path", default=str(DEFAULT_STATE_PATH))
    parser.add_argument("--secrets-path", default=str(DEFAULT_SECRETS_PATH))
    parser.add_argument("--outbox-dir", default=str(DEFAULT_OUTBOX_DIR))
    parser.add_argument("--target-user-id", default="")
    parser.add_argument("--api-secret", default="")
    parser.add_argument("--api-url", default="")
    parser.add_argument("--enabled", default=None)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--flush-outbox", action="store_true")
    args = parser.parse_args()

    state_path = Path(args.state_path)
    secrets_path = Path(args.secrets_path)
    outbox_dir = Path(args.outbox_dir)
    secrets, _secrets_warning = _load_promotions_secrets(secrets_path)

    enabled = _resolve_enabled(override_enabled=args.enabled, secrets=secrets)
    if not enabled:
        _emit_status("disabled")
        return 0

    api_secret = _resolve_api_secret(
        override_secret=_normalize_id(args.api_secret), secrets=secrets
    )
    if not api_secret:
        _emit_status("skipped_missing_api_secret")
        return 0

    if args.flush_outbox:
        flushed, pending = _flush_outbox(
            outbox_dir=outbox_dir,
            api_secret=api_secret,
            state_path=state_path,
        )
        _emit_status("flush_completed", flushed=flushed, pending=pending)
        return 0

    event = _normalize_id(args.event).lower()
    sha = _normalize_id(args.sha).lower()
    status = _normalize_id(args.status).lower()
    if not event or not sha or not status:
        _emit_status("invalid_arguments")
        return 2

    idempotency_key = _build_idempotency_key(
        event=event,
        sha=sha,
        status=status,
        explicit=_normalize_id(args.idempotency_key) or None,
    )

    state = _read_json(state_path, default={"sent": {}})
    sent = state.get("sent")
    if not isinstance(sent, dict):
        sent = {}
    if idempotency_key in sent:
        _emit_status("deduped", idempotency_key=idempotency_key)
        return 0

    target_user_raw = _resolve_target_user_id(
        override_target=_normalize_id(args.target_user_id),
        secrets=secrets,
    )
    target_user_id = _coerce_target_user_id(target_user_raw)
    if target_user_id is None:
        _emit_status("skipped_missing_recipient", idempotency_key=idempotency_key)
        return 0

    category, severity = _category_and_severity(event=event, status=status)
    message = _normalize_id(args.message) or _default_message(
        event=event,
        sha=sha,
        status=status,
        run_url=_normalize_id(args.run_url),
        stage_results=_normalize_id(args.stage_results),
    )
    request_payload = {
        "source": f"windows.{event}",
        "category": category,
        "severity": severity,
        "target_user_id": target_user_id,
        "title": _title(event=event, status=status),
        "body": message,
        "idempotency_key": idempotency_key,
        "payload": {
            "event": event,
            "sha": sha,
            "status": status,
            "run_url": _normalize_id(args.run_url),
            "stage_results": _normalize_id(args.stage_results),
        },
    }

    api_url = _resolve_api_url(override_url=_normalize_id(args.api_url), secrets=secrets)

    if args.dry_run:
        _emit_status("dry_run", idempotency_key=idempotency_key)
        return 0

    ok, detail = _attempt_emit(
        api_url=api_url,
        api_secret=api_secret,
        request_payload=request_payload,
    )
    if ok:
        _mark_sent(
            state_path=state_path,
            idempotency_key=idempotency_key,
            event=event,
            sha=sha,
            status=status,
        )
        _emit_status("sent", idempotency_key=idempotency_key, detail=detail)
        return 0

    queued_payload = {
        "queued_at": _now_iso(),
        "api_url": api_url,
        "idempotency_key": idempotency_key,
        "event": event,
        "sha": sha,
        "status": status,
        "request_payload": request_payload,
        "last_error": detail,
    }
    queued_path = _queue_outbox_event(
        outbox_dir=outbox_dir,
        queue_payload=queued_payload,
        idempotency_key=idempotency_key,
    )
    _emit_status(
        "queued_non_blocking",
        idempotency_key=idempotency_key,
        detail=detail,
        queued_path=str(queued_path),
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
