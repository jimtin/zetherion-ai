"""Docker-backed dependency and runtime health integration tests."""

from __future__ import annotations

import json
import os
import subprocess
from urllib.request import Request, urlopen

import pytest

from tests.integration.e2e_runtime import get_runtime

pytestmark = [
    pytest.mark.integration,
    pytest.mark.service_integration,
]

RUNTIME = get_runtime()


def _fetch_json(url: str, *, headers: dict[str, str] | None = None) -> dict[str, object]:
    request_headers = {"Accept": "application/json"}
    if headers:
        request_headers.update(headers)
    request = Request(url, headers=request_headers)
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _put_json(url: str, payload: dict[str, object]) -> dict[str, object]:
    body = json.dumps(payload).encode("utf-8")
    request = Request(
        url,
        data=body,
        method="PUT",
        headers={"Accept": "application/json", "Content-Type": "application/json"},
    )
    with urlopen(request, timeout=20) as response:
        return json.loads(response.read().decode("utf-8"))


def _ensure_qdrant_collections(names: set[str]) -> None:
    for name in names:
        _put_json(
            f"{RUNTIME.qdrant_url}/collections/{name}",
            {"vectors": {"size": 3072, "distance": "Cosine"}},
        )


def _skills_headers() -> dict[str, str]:
    headers: dict[str, str] = {}
    api_secret = _get_api_secret()
    if api_secret:
        headers["X-API-Secret"] = api_secret
    return headers


def _get_api_secret() -> str | None:
    container_id = RUNTIME.service_container_id("zetherion-ai-skills")
    if container_id:
        secret_keys = ("SKILLS_API_SECRET", "ZETHERION_SKILLS_API_SECRET")
        try:
            result = subprocess.run(
                [
                    "docker",
                    "inspect",
                    "--format",
                    "{{json .Config.Env}}",
                    container_id,
                ],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0 and result.stdout.strip():
                env_entries = json.loads(result.stdout.strip())
                if isinstance(env_entries, list):
                    for key in secret_keys:
                        prefix = f"{key}="
                        for entry in env_entries:
                            if isinstance(entry, str) and entry.startswith(prefix):
                                value = entry.split("=", 1)[1].strip()
                                if value:
                                    return value
        except Exception:
            pass

    return RUNTIME.resolve_secret(
        "SKILLS_API_SECRET",
        "ZETHERION_SKILLS_API_SECRET",
        default=(
            "test-skills-secret"
            if os.path.basename(RUNTIME.compose_file) == "docker-compose.test.yml"
            else None
        ),
    )


def test_public_api_health_endpoint_is_reachable() -> None:
    payload = _fetch_json(f"{RUNTIME.api_url}/api/v1/health")
    assert str(payload.get("status", "")).lower() in {"ok", "healthy"}


def test_cgs_gateway_health_endpoint_is_reachable() -> None:
    payload = _fetch_json(f"{RUNTIME.cgs_gateway_url}/service/ai/v1/health")
    assert str(payload.get("status", "")).lower() in {"ok", "healthy"}


def test_skills_runtime_health_reports_domains() -> None:
    payload = _fetch_json(
        f"{RUNTIME.skills_url}/internal/runtime/health",
        headers=_skills_headers(),
    )
    domains = payload.get("domains")
    assert isinstance(domains, list)
    keys = {
        str(domain.get("key"))
        for domain in domains
        if isinstance(domain, dict) and domain.get("key")
    }
    assert {"skills", "message_queue"} & keys


def test_qdrant_required_collections_exist() -> None:
    required = {"conversations", "long_term_memory", "user_profiles"}
    _ensure_qdrant_collections(required)
    payload = _fetch_json(f"{RUNTIME.qdrant_url}/collections")
    collections = payload.get("result", {}).get("collections", [])  # type: ignore[union-attr]
    names = {
        str(entry.get("name"))
        for entry in collections
        if isinstance(entry, dict) and entry.get("name")
    }
    assert required <= names
