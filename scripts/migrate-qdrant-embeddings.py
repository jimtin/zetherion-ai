#!/usr/bin/env python3
# ruff: noqa: E402
"""Preflight and migration helper for Qdrant embedding dimension upgrades.

Default usage runs a non-destructive preflight:
  python scripts/migrate-qdrant-embeddings.py

To migrate mismatched collections:
  python scripts/migrate-qdrant-embeddings.py --mode migrate --yes
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qdrant_client import AsyncQdrantClient
from qdrant_client.http import models as qdrant_models

REPO_ROOT = Path(__file__).resolve().parents[1]
SRC_ROOT = REPO_ROOT / "src"
if str(SRC_ROOT) not in sys.path:
    sys.path.insert(0, str(SRC_ROOT))

from zetherion_ai.config import get_settings
from zetherion_ai.memory.embeddings import get_embedding_dimension, get_embeddings_client
from zetherion_ai.memory.qdrant import CONVERSATIONS_COLLECTION, LONG_TERM_MEMORY_COLLECTION
from zetherion_ai.security.encryption import FieldEncryptor
from zetherion_ai.security.keys import KeyManager

DEFAULT_COLLECTIONS = (CONVERSATIONS_COLLECTION, LONG_TERM_MEMORY_COLLECTION)


@dataclass
class CollectionStatus:
    """Dimension metadata for a collection."""

    name: str
    exists: bool
    actual_size: int | None
    expected_size: int
    points_count: int | None

    @property
    def is_match(self) -> bool:
        if not self.exists:
            return True
        return self.actual_size == self.expected_size


def _extract_vector_size(vectors: Any) -> int | None:
    """Extract vector size from Qdrant's vector config shapes."""
    if vectors is None:
        return None

    direct_size = getattr(vectors, "size", None)
    if isinstance(direct_size, int):
        return direct_size

    if isinstance(vectors, dict):
        raw_size = vectors.get("size")
        if isinstance(raw_size, int):
            return raw_size
        for nested in vectors.values():
            nested_size = _extract_vector_size(nested)
            if nested_size is not None:
                return nested_size

    return None


def _extract_points_count(info: Any) -> int | None:
    """Read points_count from model or dict response."""
    count = getattr(info, "points_count", None)
    if isinstance(count, int):
        return count
    if isinstance(info, dict):
        raw = info.get("points_count")
        if isinstance(raw, int):
            return raw
    return None


def _extract_vectors_config(info: Any) -> Any:
    """Read vectors config from model or dict response."""
    config = getattr(info, "config", None)
    if config is not None:
        params = getattr(config, "params", None)
        if params is not None:
            return getattr(params, "vectors", None)
    if isinstance(info, dict):
        return info.get("config", {}).get("params", {}).get("vectors")
    return None


def _build_encryptor() -> FieldEncryptor:
    """Build runtime payload encryptor using configured passphrase and salt."""
    settings = get_settings()
    key_manager = KeyManager(
        passphrase=settings.encryption_passphrase.get_secret_value(),
        salt_path=settings.encryption_salt_path,
    )
    # Use non-strict decrypt mode so legacy unencrypted payloads can still migrate.
    return FieldEncryptor(key=key_manager.key, strict=False)


def _build_qdrant_client() -> AsyncQdrantClient:
    settings = get_settings()
    if settings.qdrant_use_tls:
        kwargs: dict[str, Any] = {
            "url": settings.qdrant_url,
            "https": True,
        }
        if settings.qdrant_cert_path:
            kwargs["verify"] = settings.qdrant_cert_path
        return AsyncQdrantClient(**kwargs)
    return AsyncQdrantClient(
        host=settings.qdrant_host,
        port=settings.qdrant_port,
    )


def _content_for_embedding(payload: dict[str, Any], encryptor: FieldEncryptor) -> str | None:
    """Extract plaintext content for re-embedding from payload."""
    decrypted = encryptor.decrypt_payload(payload)
    raw = decrypted.get("content")
    if isinstance(raw, str):
        text = raw.strip()
        return text or None
    return None


async def _collection_status(
    client: AsyncQdrantClient,
    collection_name: str,
    expected_size: int,
) -> CollectionStatus:
    collections = await client.get_collections()
    names = {entry.name for entry in collections.collections}
    if collection_name not in names:
        return CollectionStatus(
            name=collection_name,
            exists=False,
            actual_size=None,
            expected_size=expected_size,
            points_count=None,
        )

    info = await client.get_collection(collection_name=collection_name)
    vectors = _extract_vectors_config(info)
    actual_size = _extract_vector_size(vectors)
    points_count = _extract_points_count(info)
    return CollectionStatus(
        name=collection_name,
        exists=True,
        actual_size=actual_size,
        expected_size=expected_size,
        points_count=points_count,
    )


async def _iter_points(
    client: AsyncQdrantClient,
    *,
    collection_name: str,
    with_vectors: bool,
    page_size: int,
):
    offset: Any | None = None
    while True:
        points, next_offset = await client.scroll(
            collection_name=collection_name,
            scroll_filter=None,
            limit=page_size,
            offset=offset,
            with_payload=True,
            with_vectors=with_vectors,
        )
        if points:
            yield points
        if next_offset is None:
            break
        offset = next_offset


async def _migrate_collection(
    *,
    client: AsyncQdrantClient,
    encryptor: FieldEncryptor,
    embeddings: Any,
    collection_name: str,
    expected_size: int,
    batch_size: int,
    allow_skip: bool,
) -> None:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S")
    temp_collection = f"{collection_name}_reembed_{expected_size}_{stamp}"

    await client.create_collection(
        collection_name=temp_collection,
        vectors_config=qdrant_models.VectorParams(
            size=expected_size,
            distance=qdrant_models.Distance.COSINE,
        ),
    )

    total_points = 0
    migrated_points = 0
    skipped_ids: list[str] = []
    batch: list[qdrant_models.PointStruct] = []

    async for points in _iter_points(
        client,
        collection_name=collection_name,
        with_vectors=False,
        page_size=max(1, batch_size),
    ):
        for point in points:
            total_points += 1
            payload = point.payload or {}
            text = _content_for_embedding(payload, encryptor)
            if text is None:
                skipped_ids.append(str(point.id))
                continue

            embedding = await embeddings.embed_text(text)
            batch.append(
                qdrant_models.PointStruct(
                    id=point.id,
                    vector=embedding,
                    payload=payload,
                )
            )
            migrated_points += 1

            if len(batch) >= batch_size:
                await client.upsert(collection_name=temp_collection, points=batch)
                batch = []

    if batch:
        await client.upsert(collection_name=temp_collection, points=batch)

    if skipped_ids and not allow_skip:
        preview = ", ".join(skipped_ids[:10])
        raise RuntimeError(
            f"{collection_name}: {len(skipped_ids)} point(s) missing decryptable content. "
            f"First skipped IDs: {preview}. Re-run with --allow-skip if acceptable."
        )

    if migrated_points == 0 and total_points > 0:
        raise RuntimeError(
            f"{collection_name}: no points were migrated. Check encryption settings and payloads."
        )

    await client.delete_collection(collection_name=collection_name)
    await client.create_collection(
        collection_name=collection_name,
        vectors_config=qdrant_models.VectorParams(
            size=expected_size,
            distance=qdrant_models.Distance.COSINE,
        ),
    )

    restore_batch: list[qdrant_models.PointStruct] = []
    async for points in _iter_points(
        client,
        collection_name=temp_collection,
        with_vectors=True,
        page_size=max(1, batch_size),
    ):
        for point in points:
            restore_batch.append(
                qdrant_models.PointStruct(
                    id=point.id,
                    vector=point.vector,
                    payload=point.payload or {},
                )
            )
            if len(restore_batch) >= batch_size:
                await client.upsert(collection_name=collection_name, points=restore_batch)
                restore_batch = []

    if restore_batch:
        await client.upsert(collection_name=collection_name, points=restore_batch)

    await client.delete_collection(collection_name=temp_collection)

    print(
        f"[migrated] {collection_name}: total={total_points}, "
        f"migrated={migrated_points}, skipped={len(skipped_ids)}"
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Preflight/migrate Qdrant collections to the configured embedding dimension.",
    )
    parser.add_argument(
        "--mode",
        choices=("preflight", "migrate"),
        default="preflight",
        help="Run non-destructive checks or perform migration.",
    )
    parser.add_argument(
        "--collection",
        action="append",
        dest="collections",
        default=[],
        help="Collection to check/migrate (repeatable). Defaults to core memory collections.",
    )
    parser.add_argument(
        "--batch-size",
        type=int,
        default=64,
        help="Upsert batch size for migration.",
    )
    parser.add_argument(
        "--allow-skip",
        action="store_true",
        help="Allow points with missing/non-decryptable content to be skipped.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Required acknowledgement for --mode migrate.",
    )
    return parser.parse_args()


async def _run() -> int:
    args = _parse_args()
    settings = get_settings()
    expected_size = get_embedding_dimension()
    collections = tuple(args.collections) if args.collections else DEFAULT_COLLECTIONS

    print(
        f"Configured embeddings backend={settings.embeddings_backend} "
        f"expected_dimension={expected_size}"
    )

    client = _build_qdrant_client()
    try:
        statuses = [
            await _collection_status(client, collection_name=name, expected_size=expected_size)
            for name in collections
        ]

        mismatches: list[CollectionStatus] = []
        for status in statuses:
            if not status.exists:
                print(f"[missing] {status.name}")
                continue
            print(
                f"[found] {status.name}: actual={status.actual_size}, "
                f"expected={status.expected_size}, points={status.points_count}"
            )
            if not status.is_match:
                mismatches.append(status)

        if not mismatches:
            print("No collection dimension mismatches detected.")
            return 0

        print(
            f"Detected {len(mismatches)} mismatch(es): "
            + ", ".join(status.name for status in mismatches)
        )
        if args.mode == "preflight":
            return 2

        if not args.yes:
            print("Refusing migration without --yes.")
            return 1

        encryptor = _build_encryptor()
        embeddings = get_embeddings_client()
        try:
            for status in mismatches:
                print(f"[migrate] {status.name} -> {expected_size}")
                await _migrate_collection(
                    client=client,
                    encryptor=encryptor,
                    embeddings=embeddings,
                    collection_name=status.name,
                    expected_size=expected_size,
                    batch_size=max(1, args.batch_size),
                    allow_skip=args.allow_skip,
                )
        finally:
            close_embeddings = getattr(embeddings, "close", None)
            if callable(close_embeddings):
                maybe = close_embeddings()
                if asyncio.iscoroutine(maybe):
                    await maybe

        print("Migration complete.")
        return 0
    finally:
        with contextlib.suppress(Exception):
            await client.close()


def main() -> int:
    return asyncio.run(_run())


if __name__ == "__main__":
    raise SystemExit(main())
