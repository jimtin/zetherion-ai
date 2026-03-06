"""Replay chunk storage backends (local and S3-compatible)."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from zetherion_ai.logging import get_logger
from zetherion_ai.trust.data_plane import (
    known_object_storage_prefixes,
    object_storage_prefix_for_domain,
)
from zetherion_ai.trust.scope import TrustDomain

if TYPE_CHECKING:
    from zetherion_ai.config import Settings

log = get_logger("zetherion_ai.analytics.replay_store")


class ReplayStore(Protocol):
    """Storage interface for replay chunk bytes."""

    async def put_chunk(self, object_key: str, data: bytes) -> None:
        """Persist chunk bytes at object key."""

    async def get_chunk(self, object_key: str) -> bytes | None:
        """Read chunk bytes by object key."""

    async def delete_chunk(self, object_key: str) -> bool:
        """Delete object key if it exists."""

    async def close(self) -> None:
        """Release backend resources."""


@dataclass
class LocalReplayStore:
    """Filesystem-backed replay chunk store."""

    root_path: str

    def __post_init__(self) -> None:
        self._root = Path(self.root_path).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _resolve_key(self, object_key: str) -> Path:
        candidate = (self._root / object_key.lstrip("/")).resolve()
        if not str(candidate).startswith(str(self._root)):
            raise ValueError("Invalid replay object_key (path traversal blocked)")
        return candidate

    async def put_chunk(self, object_key: str, data: bytes) -> None:
        path = self._resolve_key(object_key)
        await asyncio.to_thread(path.parent.mkdir, parents=True, exist_ok=True)
        await asyncio.to_thread(path.write_bytes, data)

    async def get_chunk(self, object_key: str) -> bytes | None:
        path = self._resolve_key(object_key)
        exists = await asyncio.to_thread(path.exists)
        if not exists:
            return None
        return await asyncio.to_thread(path.read_bytes)

    async def delete_chunk(self, object_key: str) -> bool:
        path = self._resolve_key(object_key)
        exists = await asyncio.to_thread(path.exists)
        if not exists:
            return False
        await asyncio.to_thread(path.unlink)
        return True

    async def close(self) -> None:
        return None


class S3ReplayStore:
    """S3-compatible replay chunk store."""

    def __init__(
        self,
        *,
        bucket: str,
        region: str,
        endpoint: str | None = None,
        access_key_id: str | None = None,
        secret_access_key: str | None = None,
        force_path_style: bool = True,
    ) -> None:
        try:
            import boto3  # type: ignore[import-not-found,import-untyped]
            from botocore.config import (  # type: ignore[import-not-found,import-untyped]
                Config,
            )
        except Exception as exc:  # pragma: no cover - depends on optional package
            raise RuntimeError("boto3 is required for object_storage_backend=s3") from exc

        config = Config(s3={"addressing_style": "path" if force_path_style else "auto"})
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            region_name=region or None,
            endpoint_url=endpoint or None,
            aws_access_key_id=access_key_id or None,
            aws_secret_access_key=secret_access_key or None,
            config=config,
        )

    async def put_chunk(self, object_key: str, data: bytes) -> None:
        await asyncio.to_thread(
            self._client.put_object,
            Bucket=self._bucket,
            Key=object_key,
            Body=data,
            ContentType="application/octet-stream",
        )

    async def get_chunk(self, object_key: str) -> bytes | None:
        try:
            response = await asyncio.to_thread(
                self._client.get_object,
                Bucket=self._bucket,
                Key=object_key,
            )
        except Exception:
            return None
        body = response.get("Body")
        if body is None:
            return None
        return await asyncio.to_thread(body.read)

    async def delete_chunk(self, object_key: str) -> bool:
        try:
            await asyncio.to_thread(
                self._client.delete_object,
                Bucket=self._bucket,
                Key=object_key,
            )
            return True
        except Exception:
            return False

    async def close(self) -> None:
        return None


class ScopedReplayStore:
    """Replay-store wrapper that enforces trust-domain key prefixes."""

    def __init__(
        self,
        base_store: ReplayStore,
        *,
        trust_domain: TrustDomain,
        legacy_read_fallback: bool = True,
    ) -> None:
        self._base_store = base_store
        self._trust_domain = trust_domain
        self._prefix = object_storage_prefix_for_domain(trust_domain)
        self._known_prefixes = set(known_object_storage_prefixes())
        self._legacy_read_fallback = legacy_read_fallback

    def _normalized_candidates(self, object_key: str) -> tuple[str, list[str]]:
        normalized = object_key.lstrip("/")
        if not normalized:
            raise ValueError("Object key must not be empty")

        prefix = normalized.split("/", 1)[0]
        if prefix in self._known_prefixes:
            if prefix != self._prefix:
                raise ValueError(
                    f"Cross-domain object key blocked for {self._trust_domain.value}: {object_key}"
                )
            return normalized, [normalized]

        scoped = f"{self._prefix}/{normalized}"
        candidates = [scoped]
        if self._legacy_read_fallback:
            candidates.append(normalized)
        return scoped, candidates

    async def put_chunk(self, object_key: str, data: bytes) -> None:
        scoped_key, _ = self._normalized_candidates(object_key)
        await self._base_store.put_chunk(scoped_key, data)

    async def get_chunk(self, object_key: str) -> bytes | None:
        _, candidates = self._normalized_candidates(object_key)
        for candidate in candidates:
            payload = await self._base_store.get_chunk(candidate)
            if payload is not None:
                if candidate != candidates[0]:
                    log.info(
                        "object_storage_legacy_fallback_read",
                        trust_domain=self._trust_domain.value,
                        object_key=object_key,
                    )
                return payload
        return None

    async def delete_chunk(self, object_key: str) -> bool:
        _, candidates = self._normalized_candidates(object_key)
        deleted = False
        for candidate in dict.fromkeys(candidates):
            deleted = await self._base_store.delete_chunk(candidate) or deleted
        return deleted

    async def close(self) -> None:
        await self._base_store.close()


def _settings_value(settings: Settings, key: str, legacy_key: str) -> object:
    values = getattr(settings, "__dict__", {})
    if isinstance(values, dict):
        if key in values:
            return values[key]
        if legacy_key in values:
            return values[legacy_key]
    if hasattr(settings, key):
        return getattr(settings, key)
    return getattr(settings, legacy_key)


def _secret_value(raw: object) -> str:
    if raw is None:
        return ""
    if hasattr(raw, "get_secret_value"):
        return str(raw.get_secret_value())
    return str(raw)


def _bool_value(raw: object) -> bool:
    if isinstance(raw, bool):
        return raw
    if isinstance(raw, str):
        return raw.strip().lower() in {"1", "true", "yes", "on"}
    return bool(raw)


def create_replay_store_from_settings(
    settings: Settings,
    *,
    trust_domain: TrustDomain | None = None,
) -> ReplayStore | None:
    """Create replay storage backend from settings."""
    backend = str(_settings_value(settings, "object_storage_backend", "replay_storage_backend"))
    backend = backend.strip().lower()
    if backend == "none":
        return None
    if backend == "local":
        local_path = str(
            _settings_value(settings, "object_storage_local_path", "replay_storage_local_path")
        )
        store: ReplayStore | None = LocalReplayStore(root_path=local_path)
        if trust_domain is not None:
            return ScopedReplayStore(store, trust_domain=trust_domain)
        return store
    if backend == "s3":
        secret = _secret_value(
            _settings_value(
                settings,
                "object_storage_secret_access_key",
                "replay_storage_secret_access_key",
            )
        )
        bucket = str(_settings_value(settings, "object_storage_bucket", "replay_storage_bucket"))
        if not bucket:
            raise ValueError("object_storage_bucket is required when object_storage_backend=s3")
        store = S3ReplayStore(
            bucket=bucket,
            region=str(_settings_value(settings, "object_storage_region", "replay_storage_region")),
            endpoint=(
                str(_settings_value(settings, "object_storage_endpoint", "replay_storage_endpoint"))
                or None
            ),
            access_key_id=(
                str(
                    _settings_value(
                        settings,
                        "object_storage_access_key_id",
                        "replay_storage_access_key_id",
                    )
                )
                or None
            ),
            secret_access_key=secret or None,
            force_path_style=_bool_value(
                _settings_value(
                    settings, "object_storage_force_path_style", "replay_storage_force_path_style"
                )
            ),
        )
        if trust_domain is not None:
            return ScopedReplayStore(store, trust_domain=trust_domain)
        return store
    log.warning("unknown_object_storage_backend", backend=backend)
    return None
