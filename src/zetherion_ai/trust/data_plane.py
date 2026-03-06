"""Data-plane isolation helpers for trust-domain storage separation."""

from __future__ import annotations

import re
from collections.abc import Mapping
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from zetherion_ai.logging import get_logger
from zetherion_ai.trust.scope import TrustDomain

if TYPE_CHECKING:
    from zetherion_ai.config import Settings

log = get_logger("zetherion_ai.trust.data_plane")


class QdrantStoragePlane(StrEnum):
    """Physical Qdrant plane used by one trust domain."""

    OWNER = "owner"
    TENANT = "tenant"


_OWNER_QDRANT_DOMAINS = {
    TrustDomain.OWNER_PERSONAL,
    TrustDomain.OWNER_PORTFOLIO,
}
_SCHEMA_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def qdrant_storage_plane_for_domain(trust_domain: TrustDomain) -> QdrantStoragePlane:
    """Map a trust domain to the owner or tenant Qdrant plane."""

    if trust_domain in _OWNER_QDRANT_DOMAINS:
        return QdrantStoragePlane.OWNER
    return QdrantStoragePlane.TENANT


def object_storage_prefix_for_domain(trust_domain: TrustDomain) -> str:
    """Return the canonical object-storage prefix for a trust domain."""

    return trust_domain.value


def known_object_storage_prefixes() -> tuple[str, ...]:
    """Return all canonical object-storage domain prefixes."""

    return tuple(domain.value for domain in TrustDomain)


def postgres_isolation_schema_map(settings: Settings | Mapping[str, Any]) -> dict[str, str]:
    """Return logical PostgreSQL isolation schemas from settings."""

    def _value(key: str, default: str) -> str:
        if isinstance(settings, Mapping):
            raw = settings.get(key, default)
        else:
            raw = getattr(settings, key, default)
        if not isinstance(raw, str):
            return default
        candidate = raw.strip()
        return candidate or default

    return {
        "tenant_app": _value("postgres_tenant_app_schema", "tenant_app"),
        "owner_personal": _value("postgres_owner_personal_schema", "owner_personal"),
        "owner_portfolio": _value("postgres_owner_portfolio_schema", "owner_portfolio"),
        "control_plane": _value("postgres_control_plane_schema", "control_plane"),
        "cgs_gateway": _value("postgres_cgs_gateway_schema", "cgs_gateway"),
    }


def _validate_schema_name(schema_name: str) -> str:
    candidate = schema_name.strip()
    if not _SCHEMA_NAME_RE.fullmatch(candidate):
        raise ValueError(f"Invalid PostgreSQL schema name: {schema_name!r}")
    return candidate


async def ensure_postgres_isolation_schemas(
    pool: Any,
    settings: Settings | Mapping[str, Any],
) -> tuple[str, ...]:
    """Create additive isolation schemas when a compatible pool is available."""

    if pool is None:
        return ()

    schema_map = postgres_isolation_schema_map(settings)
    schema_names: list[str] = []
    seen: set[str] = set()
    for schema_name in schema_map.values():
        validated = _validate_schema_name(schema_name)
        if validated not in seen:
            seen.add(validated)
            schema_names.append(validated)

    try:
        async with pool.acquire() as conn:
            for schema_name in schema_names:
                await conn.execute(f'CREATE SCHEMA IF NOT EXISTS "{schema_name}"')
    except AttributeError:
        log.warning(
            "postgres_isolation_schema_bootstrap_skipped",
            reason="pool_missing_acquire",
        )
        return ()
    except TypeError:
        log.warning(
            "postgres_isolation_schema_bootstrap_skipped",
            reason="pool_not_async_context_manager",
        )
        return ()

    log.info("postgres_isolation_schemas_ensured", schemas=schema_names)
    return tuple(schema_names)
