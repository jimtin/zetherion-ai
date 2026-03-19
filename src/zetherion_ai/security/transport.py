"""Shared transport-security helpers for internal TLS and production policy."""

from __future__ import annotations

import ssl
from urllib.parse import urlparse

_PRODUCTION_ENVIRONMENTS = {"prod", "production", "live"}


def is_production_environment(environment: str | None) -> bool:
    """Return True when the environment should enforce live-security policies."""
    normalized = str(environment or "").strip().lower()
    return normalized in _PRODUCTION_ENVIRONMENTS


def split_csv_urls(value: str) -> list[str]:
    """Split a comma-separated URL list into normalized entries."""
    return [entry.strip().rstrip("/") for entry in value.split(",") if entry.strip()]


def validate_https_url(value: str, *, field_name: str) -> str:
    """Validate a single HTTPS URL."""
    parsed = urlparse(value)
    if parsed.scheme.lower() != "https":
        raise ValueError(f"{field_name} must use https:// in strict transport mode")
    if not parsed.netloc:
        raise ValueError(f"{field_name} must be a valid absolute HTTPS URL")
    return value


def validate_https_csv_urls(value: str, *, field_name: str) -> str:
    """Validate a comma-separated list of HTTPS URLs."""
    urls = split_csv_urls(value)
    if not urls:
        raise ValueError(f"{field_name} must include at least one HTTPS URL")
    for url in urls:
        validate_https_url(url, field_name=field_name)
    return ",".join(urls)


def require_non_ollama_backend(value: str, *, field_name: str) -> str:
    """Reject Ollama-backed production routing."""
    normalized = str(value).strip().lower()
    if normalized == "ollama":
        raise ValueError(f"{field_name} cannot be 'ollama' in strict transport mode")
    return normalized


def build_server_ssl_context(
    *,
    cert_path: str | None,
    key_path: str | None,
    ca_path: str | None = None,
    require_client_cert: bool = False,
) -> ssl.SSLContext | None:
    """Build a TLS server context when certificate material is configured."""
    if not cert_path or not key_path:
        return None
    context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    if ca_path:
        context.load_verify_locations(cafile=ca_path)
    context.verify_mode = ssl.CERT_REQUIRED if require_client_cert else ssl.CERT_NONE
    context.check_hostname = False
    return context


def build_client_ssl_context(
    *,
    ca_path: str | None = None,
    cert_path: str | None = None,
    key_path: str | None = None,
    require_tls: bool = False,
) -> ssl.SSLContext | None:
    """Build a TLS client context for aiohttp/httpx clients."""
    if not require_tls and not any((ca_path, cert_path, key_path)):
        return None
    context = ssl.create_default_context(ssl.Purpose.SERVER_AUTH)
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    if ca_path:
        context.load_verify_locations(cafile=ca_path)
    if cert_path and key_path:
        context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    return context


def build_asyncpg_ssl_context(
    *,
    ca_path: str | None = None,
    cert_path: str | None = None,
    key_path: str | None = None,
    require_tls: bool = False,
) -> ssl.SSLContext | None:
    """Build an SSL context for asyncpg pools."""
    context = build_client_ssl_context(
        ca_path=ca_path,
        cert_path=cert_path,
        key_path=key_path,
        require_tls=require_tls,
    )
    if context is not None:
        context.check_hostname = False
    return context
