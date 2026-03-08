"""Validation for the Segment 0 isolation compatibility manifest."""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = REPO_ROOT / ".ci" / "isolation_compatibility_manifest.json"

EXPECTED_TRUST_DOMAINS = {
    "owner_personal",
    "owner_portfolio",
    "tenant_raw",
    "tenant_derived",
    "control_plane",
    "worker_artifact",
}

EXPECTED_MECHANISM_CLASSES = {
    "access_control",
    "behavioral_trust",
    "grant",
    "descriptive_relationship_state",
    "derived_intelligence",
}

EXPECTED_COMPATIBILITY_PATHS = {
    "src/zetherion_ai/memory/qdrant.py",
    "src/zetherion_ai/discord/user_manager.py",
    "src/zetherion_ai/integrations/storage.py",
    "src/zetherion_ai/agent/core.py",
    "src/zetherion_ai/agent/prompts.py",
    "src/zetherion_ai/routing/email_router.py",
}


def _load_manifest() -> dict[str, object]:
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def _collect_paths(node: object) -> set[str]:
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key in {"path", "paths"}:
                if isinstance(value, str):
                    found.add(value)
                elif isinstance(value, list):
                    found.update(item for item in value if isinstance(item, str))
            else:
                found.update(_collect_paths(value))
    elif isinstance(node, list):
        for item in node:
            found.update(_collect_paths(item))
    return found


def test_manifest_exists():
    assert MANIFEST_PATH.exists(), f"missing manifest: {MANIFEST_PATH}"


def test_manifest_declares_all_target_trust_domains():
    manifest = _load_manifest()
    domains = {entry["id"] for entry in manifest["target_trust_domains"]}
    assert domains == EXPECTED_TRUST_DOMAINS


def test_manifest_referenced_paths_exist():
    manifest = _load_manifest()
    missing = sorted(path for path in _collect_paths(manifest) if not (REPO_ROOT / path).exists())
    assert missing == []


def test_manifest_covers_required_mechanism_classes():
    manifest = _load_manifest()
    classes = {entry["classification"] for entry in manifest["mechanism_inventory"]}
    assert classes == EXPECTED_MECHANISM_CLASSES


def test_manifest_tracks_known_legacy_compatibility_surfaces():
    manifest = _load_manifest()
    compatibility_paths = {entry["path"] for entry in manifest["compatibility_allowlist"]}
    assert EXPECTED_COMPATIBILITY_PATHS.issubset(compatibility_paths)


def test_manifest_tracks_segment_2_tenant_conversation_surfaces() -> None:
    manifest = _load_manifest()

    tenant_api = next(
        entry for entry in manifest["storage_families"] if entry["id"] == "tenant-api-relational"
    )
    assert "tenant_subject_memories" in tenant_api["tables"]

    public_api_runtime = next(
        entry for entry in manifest["route_families"] if entry["id"] == "public-api-runtime"
    )
    assert "src/zetherion_ai/api/routes/sessions.py" in public_api_runtime["paths"]

    domain_prompts = next(
        entry for entry in manifest["prompt_sources"] if entry["id"] == "domain-prompts"
    )
    assert "src/zetherion_ai/api/conversation_runtime.py" in domain_prompts["paths"]
    assert "src/zetherion_ai/skills/client_chat.py" in domain_prompts["paths"]
