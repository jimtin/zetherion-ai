"""Focused unit coverage for Segment 2 data-plane isolation foundations."""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from zetherion_ai.analytics.replay_store import (
    LocalReplayStore,
    ScopedReplayStore,
    create_replay_store_from_settings,
)
from zetherion_ai.config import Settings
from zetherion_ai.security.domain_keys import (
    DomainKeyProvider,
    EncryptionDomain,
    TenantKeyEnvelopeService,
)
from zetherion_ai.trust.data_plane import ensure_postgres_isolation_schemas
from zetherion_ai.trust.scope import TrustDomain


def _make_settings(**overrides):
    base = {
        "discord_token": "discord-token",
        "gemini_api_key": "gemini-key",
        "encryption_passphrase": "master-passphrase-123",
    }
    base.update(overrides)
    return Settings(_env_file=None, **base)


class _DummyAcquire:
    def __init__(self, conn) -> None:
        self._conn = conn

    async def __aenter__(self):
        return self._conn

    async def __aexit__(self, exc_type, exc, tb) -> bool:
        return False


class _DummyPool:
    def __init__(self) -> None:
        self.conn = SimpleNamespace(execute=AsyncMock(return_value="OK"))

    def acquire(self):
        return _DummyAcquire(self.conn)


def test_settings_expose_domain_specific_qdrant_urls() -> None:
    settings = _make_settings(
        qdrant_host="legacy-qdrant",
        qdrant_port=6333,
        qdrant_use_tls=False,
        qdrant_owner_host="owner-qdrant",
        qdrant_owner_port=7333,
        qdrant_owner_use_tls=True,
        qdrant_tenant_host="tenant-qdrant",
        qdrant_tenant_port=8333,
    )

    assert settings.qdrant_owner_url == "https://owner-qdrant:7333"
    assert settings.qdrant_tenant_url == "http://tenant-qdrant:8333"


@pytest.mark.asyncio
async def test_qdrant_memory_owner_domain_uses_owner_endpoint() -> None:
    settings = SimpleNamespace(
        qdrant_host="legacy-qdrant",
        qdrant_port=6333,
        qdrant_use_tls=False,
        qdrant_cert_path=None,
        qdrant_owner_host="owner-qdrant",
        qdrant_owner_port=7333,
        qdrant_owner_use_tls=None,
        qdrant_owner_cert_path=None,
        qdrant_owner_url="http://owner-qdrant:7333",
        qdrant_tenant_host="tenant-qdrant",
        qdrant_tenant_port=8333,
        qdrant_tenant_use_tls=None,
        qdrant_tenant_cert_path=None,
        qdrant_tenant_url="http://tenant-qdrant:8333",
    )
    mock_embeddings = AsyncMock()
    mock_client = AsyncMock()

    with (
        patch("zetherion_ai.memory.qdrant.get_settings", return_value=settings),
        patch("zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings),
        patch(
            "zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client
        ) as client_cls,
    ):
        from zetherion_ai.memory.qdrant import QdrantMemory

        memory = QdrantMemory(trust_domain=TrustDomain.OWNER_PERSONAL)

    client_cls.assert_called_once_with(host="owner-qdrant", port=7333)
    assert memory._storage_plane.value == "owner"


@pytest.mark.asyncio
async def test_qdrant_memory_tenant_domain_uses_tenant_endpoint() -> None:
    settings = SimpleNamespace(
        qdrant_host="legacy-qdrant",
        qdrant_port=6333,
        qdrant_use_tls=False,
        qdrant_cert_path=None,
        qdrant_owner_host="owner-qdrant",
        qdrant_owner_port=7333,
        qdrant_owner_use_tls=None,
        qdrant_owner_cert_path=None,
        qdrant_owner_url="http://owner-qdrant:7333",
        qdrant_tenant_host="tenant-qdrant",
        qdrant_tenant_port=8333,
        qdrant_tenant_use_tls=None,
        qdrant_tenant_cert_path=None,
        qdrant_tenant_url="http://tenant-qdrant:8333",
    )
    mock_embeddings = AsyncMock()
    mock_client = AsyncMock()

    with (
        patch("zetherion_ai.memory.qdrant.get_settings", return_value=settings),
        patch("zetherion_ai.memory.qdrant.get_embeddings_client", return_value=mock_embeddings),
        patch(
            "zetherion_ai.memory.qdrant.AsyncQdrantClient", return_value=mock_client
        ) as client_cls,
    ):
        from zetherion_ai.memory.qdrant import QdrantMemory

        memory = QdrantMemory(trust_domain=TrustDomain.TENANT_RAW)

    client_cls.assert_called_once_with(host="tenant-qdrant", port=8333)
    assert memory._storage_plane.value == "tenant"


@pytest.mark.asyncio
async def test_scoped_replay_store_prefixes_new_writes_and_reads_legacy(tmp_path: Path) -> None:
    base_store = LocalReplayStore(root_path=str(tmp_path))
    scoped_store = ScopedReplayStore(base_store, trust_domain=TrustDomain.TENANT_RAW)

    await base_store.put_chunk("documents/tenant-a/legacy.bin", b"legacy")
    assert await scoped_store.get_chunk("documents/tenant-a/legacy.bin") == b"legacy"

    await scoped_store.put_chunk("documents/tenant-a/current.bin", b"current")
    assert (tmp_path / "tenant_raw" / "documents" / "tenant-a" / "current.bin").exists()


@pytest.mark.asyncio
async def test_scoped_replay_store_blocks_cross_domain_keys(tmp_path: Path) -> None:
    base_store = LocalReplayStore(root_path=str(tmp_path))
    scoped_store = ScopedReplayStore(base_store, trust_domain=TrustDomain.TENANT_RAW)

    with pytest.raises(ValueError, match="Cross-domain object key blocked"):
        await scoped_store.get_chunk("owner_personal/secret.bin")


def test_create_replay_store_from_settings_wraps_domain_scoped_backend() -> None:
    settings = MagicMock(
        object_storage_backend="local",
        object_storage_local_path="data/replay_chunks",
    )

    store = create_replay_store_from_settings(settings, trust_domain=TrustDomain.TENANT_RAW)
    assert isinstance(store, ScopedReplayStore)


@pytest.mark.asyncio
async def test_ensure_postgres_isolation_schemas_creates_expected_names() -> None:
    pool = _DummyPool()
    settings = {
        "postgres_tenant_app_schema": "tenant_app",
        "postgres_owner_personal_schema": "owner_personal",
        "postgres_owner_portfolio_schema": "owner_portfolio",
        "postgres_control_plane_schema": "control_plane",
        "postgres_cgs_gateway_schema": "cgs_gateway",
    }

    schemas = await ensure_postgres_isolation_schemas(pool, settings)

    assert schemas == (
        "tenant_app",
        "owner_personal",
        "owner_portfolio",
        "control_plane",
        "cgs_gateway",
    )
    executed = [call.args[0] for call in pool.conn.execute.await_args_list]
    assert 'CREATE SCHEMA IF NOT EXISTS "tenant_app"' in executed
    assert 'CREATE SCHEMA IF NOT EXISTS "cgs_gateway"' in executed


def test_domain_key_provider_supports_owner_and_tenant_overrides(tmp_path: Path) -> None:
    settings = _make_settings(
        encryption_owner_passphrase="owner-passphrase-123",
        encryption_owner_salt_path=str(tmp_path / "owner-salt.bin"),
        encryption_tenant_passphrase="tenant-passphrase-123",
        encryption_tenant_salt_path=str(tmp_path / "tenant-salt.bin"),
    )
    provider = DomainKeyProvider(settings)

    owner_material = provider.build_material(EncryptionDomain.OWNER_PERSONAL, strict=True)
    tenant_material = provider.build_material(EncryptionDomain.TENANT_DATA, strict=True)

    assert owner_material.salt_path.endswith("owner-salt.bin")
    assert tenant_material.salt_path.endswith("tenant-salt.bin")
    assert owner_material.key_manager.key != tenant_material.key_manager.key

    envelope_service = TenantKeyEnvelopeService(tenant_material.key_manager.key)
    tenant_key = envelope_service.generate_tenant_key()
    wrapped = envelope_service.wrap_key(tenant_key)
    assert envelope_service.unwrap_key(wrapped) == tenant_key
