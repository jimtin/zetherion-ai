"""Tests for replay storage backends."""

from __future__ import annotations

import sys
import types
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from zetherion_ai.analytics.replay_store import (
    LocalReplayStore,
    S3ReplayStore,
    _bool_value,
    _secret_value,
    _settings_value,
    create_replay_store_from_settings,
)


@pytest.mark.asyncio
async def test_local_replay_store_roundtrip(tmp_path: Path) -> None:
    store = LocalReplayStore(root_path=str(tmp_path))
    await store.put_chunk("tenant-a/session-1/chunk-0.bin", b"abc")

    data = await store.get_chunk("tenant-a/session-1/chunk-0.bin")
    assert data == b"abc"

    deleted = await store.delete_chunk("tenant-a/session-1/chunk-0.bin")
    assert deleted is True
    assert await store.get_chunk("tenant-a/session-1/chunk-0.bin") is None


@pytest.mark.asyncio
async def test_local_replay_store_blocks_traversal(tmp_path: Path) -> None:
    store = LocalReplayStore(root_path=str(tmp_path))
    with pytest.raises(ValueError):
        await store.put_chunk("../outside.bin", b"x")


@pytest.mark.asyncio
async def test_local_replay_store_delete_missing_returns_false(tmp_path: Path) -> None:
    store = LocalReplayStore(root_path=str(tmp_path))
    assert await store.delete_chunk("tenant-a/session-1/chunk-404.bin") is False
    await store.close()


def test_create_replay_store_from_settings_local() -> None:
    settings = MagicMock(
        object_storage_backend="local",
        object_storage_local_path="data/replay_chunks",
    )
    store = create_replay_store_from_settings(settings)
    assert isinstance(store, LocalReplayStore)


def test_create_replay_store_from_settings_none() -> None:
    settings = MagicMock(object_storage_backend="none")
    store = create_replay_store_from_settings(settings)
    assert store is None


def test_create_replay_store_from_settings_legacy_fallback() -> None:
    settings = MagicMock(
        replay_storage_backend="local",
        replay_storage_local_path="data/replay_chunks",
    )
    store = create_replay_store_from_settings(settings)
    assert isinstance(store, LocalReplayStore)


def test_create_replay_store_from_settings_unknown_backend() -> None:
    settings = MagicMock(object_storage_backend="mystery-backend")
    store = create_replay_store_from_settings(settings)
    assert store is None


def test_create_replay_store_from_settings_s3_requires_bucket() -> None:
    settings = MagicMock(
        object_storage_backend="s3",
        object_storage_bucket="",
        object_storage_region="",
        object_storage_endpoint="",
        object_storage_access_key_id="",
        object_storage_secret_access_key=None,
        object_storage_force_path_style=True,
    )
    with pytest.raises(ValueError, match="object_storage_bucket"):
        create_replay_store_from_settings(settings)


def test_create_replay_store_from_settings_s3_constructs_backend(monkeypatch) -> None:
    captured: dict[str, object] = {}

    class _FakeS3Store:
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
            captured.update(
                {
                    "bucket": bucket,
                    "region": region,
                    "endpoint": endpoint,
                    "access_key_id": access_key_id,
                    "secret_access_key": secret_access_key,
                    "force_path_style": force_path_style,
                }
            )

    monkeypatch.setattr("zetherion_ai.analytics.replay_store.S3ReplayStore", _FakeS3Store)

    secret = MagicMock()
    secret.get_secret_value.return_value = "secret-123"
    settings = MagicMock(
        object_storage_backend="s3",
        object_storage_bucket="bucket-1",
        object_storage_region="eu-west-1",
        object_storage_endpoint="http://minio.local:9000",
        object_storage_access_key_id="access-1",
        object_storage_secret_access_key=secret,
        object_storage_force_path_style="false",
    )

    store = create_replay_store_from_settings(settings)
    assert isinstance(store, _FakeS3Store)
    assert captured["bucket"] == "bucket-1"
    assert captured["region"] == "eu-west-1"
    assert captured["endpoint"] == "http://minio.local:9000"
    assert captured["access_key_id"] == "access-1"
    assert captured["secret_access_key"] == "secret-123"
    assert captured["force_path_style"] is False


@pytest.mark.asyncio
async def test_s3_replay_store_roundtrip_with_fake_boto(monkeypatch) -> None:
    class FakeBody:
        def __init__(self, data: bytes) -> None:
            self._data = data

        def read(self) -> bytes:
            return self._data

    class FakeS3Client:
        def __init__(self) -> None:
            self._objects: dict[str, bytes] = {}

        def put_object(self, **kwargs):  # type: ignore[no-untyped-def]
            key = str(kwargs["Key"])
            body = bytes(kwargs["Body"])
            self._objects[key] = body

        def get_object(self, **kwargs):  # type: ignore[no-untyped-def]
            key = str(kwargs["Key"])
            if key not in self._objects:
                raise RuntimeError("not found")
            return {"Body": FakeBody(self._objects[key])}

        def delete_object(self, **kwargs):  # type: ignore[no-untyped-def]
            self._objects.pop(str(kwargs["Key"]), None)

    class FakeConfig:
        def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            self.kwargs = kwargs

    fake_client = FakeS3Client()
    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda *args, **kwargs: fake_client  # type: ignore[attr-defined]
    fake_botocore = types.ModuleType("botocore")
    fake_botocore_config = types.ModuleType("botocore.config")
    fake_botocore_config.Config = FakeConfig  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore", fake_botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", fake_botocore_config)

    store = S3ReplayStore(bucket="bucket-1", region="eu-west-1")
    await store.put_chunk("tenant-a/chunk-1.bin", b"hello")
    assert await store.get_chunk("tenant-a/chunk-1.bin") == b"hello"
    assert await store.get_chunk("tenant-a/missing.bin") is None
    assert await store.delete_chunk("tenant-a/chunk-1.bin") is True
    assert await store.delete_chunk("tenant-a/chunk-1.bin") is True
    await store.close()


@pytest.mark.asyncio
async def test_s3_replay_store_handles_none_body_and_delete_error(monkeypatch) -> None:
    class FakeS3Client:
        def get_object(self, **kwargs):  # type: ignore[no-untyped-def]
            return {"Body": None}

        def delete_object(self, **kwargs):  # type: ignore[no-untyped-def]
            raise RuntimeError("delete-failed")

    class FakeConfig:
        def __init__(self, **kwargs) -> None:  # type: ignore[no-untyped-def]
            self.kwargs = kwargs

    fake_boto3 = types.ModuleType("boto3")
    fake_boto3.client = lambda *args, **kwargs: FakeS3Client()  # type: ignore[attr-defined]
    fake_botocore = types.ModuleType("botocore")
    fake_botocore_config = types.ModuleType("botocore.config")
    fake_botocore_config.Config = FakeConfig  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "boto3", fake_boto3)
    monkeypatch.setitem(sys.modules, "botocore", fake_botocore)
    monkeypatch.setitem(sys.modules, "botocore.config", fake_botocore_config)

    store = S3ReplayStore(bucket="bucket-1", region="")
    assert await store.get_chunk("tenant-a/chunk-1.bin") is None
    assert await store.delete_chunk("tenant-a/chunk-1.bin") is False


def test_helper_functions_cover_edge_paths() -> None:
    class LegacyOnly:
        replay_storage_backend = "local"

    class DictBacked:
        def __init__(self) -> None:
            self.__dict__["object_storage_backend"] = "none"

    class HasAttr:
        object_storage_backend = "direct"
        replay_storage_backend = "legacy"

    assert (
        _settings_value(
            DictBacked(),
            "object_storage_backend",
            "replay_storage_backend",
        )
        == "none"
    )
    assert (
        _settings_value(
            LegacyOnly(),
            "object_storage_backend",
            "replay_storage_backend",
        )
        == "local"
    )
    assert (
        _settings_value(
            HasAttr(),
            "object_storage_backend",
            "replay_storage_backend",
        )
        == "direct"
    )
    assert _secret_value(None) == ""
    assert _secret_value("abc") == "abc"
    assert _bool_value(True) is True
    assert _bool_value("YES") is True
    assert _bool_value("0") is False
    assert _bool_value(0) is False
