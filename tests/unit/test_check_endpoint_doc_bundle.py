"""Unit tests for the endpoint docs bundle check script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "check-endpoint-doc-bundle.py"
    spec = importlib.util.spec_from_file_location("check_endpoint_doc_bundle_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_internal_public_api_server_bootstrap_change_is_ignored(monkeypatch) -> None:
    module = _load_module()
    rule = module.DOC_RULES["zetherion_public_api"]

    monkeypatch.setattr(
        module,
        "_changed_content_lines",
        lambda base_ref, path: (
            "+    from zetherion_ai.trust.storage import ensure_trust_storage_schema",
            "+            await ensure_trust_storage_schema(",
        ),
    )

    matched = module._matched_rule_changes(
        "zetherion_public_api",
        rule,
        {"src/zetherion_ai/api/server.py"},
        "origin/main",
    )

    assert matched == []


def test_public_api_server_route_registration_change_requires_docs(monkeypatch) -> None:
    module = _load_module()
    rule = module.DOC_RULES["zetherion_public_api"]

    monkeypatch.setattr(
        module,
        "_changed_content_lines",
        lambda base_ref, path: ('+        app.router.add_post("/api/v1/example", handle_example)',),
    )

    matched = module._matched_rule_changes(
        "zetherion_public_api",
        rule,
        {"src/zetherion_ai/api/server.py"},
        "origin/main",
    )

    assert matched == ["src/zetherion_ai/api/server.py"]


def test_public_api_route_module_change_requires_docs() -> None:
    module = _load_module()
    rule = module.DOC_RULES["zetherion_public_api"]

    matched = module._matched_rule_changes(
        "zetherion_public_api",
        rule,
        {"src/zetherion_ai/api/routes/example.py"},
        "origin/main",
    )

    assert matched == ["src/zetherion_ai/api/routes/example.py"]


def test_internal_cgs_server_bootstrap_change_is_ignored(monkeypatch) -> None:
    module = _load_module()
    rule = module.DOC_RULES["cgs_gateway_routes"]

    monkeypatch.setattr(
        module,
        "_changed_content_lines",
        lambda base_ref, path: (
            "+    from zetherion_ai.trust.storage import ensure_trust_storage_schema",
            "+            await ensure_trust_storage_schema(",
        ),
    )

    matched = module._matched_rule_changes(
        "cgs_gateway_routes",
        rule,
        {"src/zetherion_ai/cgs_gateway/server.py"},
        "origin/main",
    )

    assert matched == []
