"""Unit tests for local changed-file gate planning."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "local_gate_plan.py"
    spec = importlib.util.spec_from_file_location("local_gate_plan_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _requirement_ids(plan: dict[str, object]) -> set[str]:
    return {item["id"] for item in plan["requirements"]}  # type: ignore[index]


def test_api_route_change_requires_docs_bundle_and_mypy() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["src/zetherion_ai/api/server.py"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {"bandit-src", "endpoint-doc-bundle", "mypy-src"}
    assert plan["unmapped_protected_paths"] == []


def test_shared_operational_storage_change_requires_unit_full() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["src/zetherion_ai/personal/operational_storage.py"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {"bandit-src", "mypy-src", "unit-full"}
    assert plan["unmapped_protected_paths"] == []


def test_startup_bootstrap_change_requires_unit_full() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["src/zetherion_ai/main.py"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {"bandit-src", "mypy-src", "unit-full"}
    assert plan["unmapped_protected_paths"] == []


def test_qdrant_change_requires_targeted_regression_suite() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["src/zetherion_ai/memory/qdrant.py"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {"bandit-src", "mypy-src", "qdrant-regression-suite"}
    qdrant_requirement = next(
        item
        for item in plan["requirements"]
        if item["id"] == "qdrant-regression-suite"  # type: ignore[index]
    )
    assert qdrant_requirement["pytest_targets"] == [
        "tests/unit/test_qdrant.py",
        "tests/unit/test_data_plane_isolation.py",
        "tests/test_qdrant.py",
    ]


def test_replay_store_change_requires_targeted_regression_suite() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["src/zetherion_ai/analytics/replay_store.py"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {"bandit-src", "mypy-src", "replay-store-regression-suite"}
    assert plan["unmapped_protected_paths"] == []


def test_unmapped_protected_paths_are_reported() -> None:
    module = _load_module()
    manifest = {
        "protected_globs": ["src/zetherion_ai/memory/qdrant.py"],
        "requirements": {
            "mypy-src": {
                "kind": "check",
                "description": "Run strict mypy over src/zetherion_ai.",
            }
        },
        "rules": [
            {
                "id": "python-typecheck",
                "patterns": ["src/zetherion_ai/**"],
                "requirements": ["mypy-src"],
            }
        ],
    }

    plan = module.build_plan(
        changed_paths=["src/zetherion_ai/memory/qdrant.py"],
        manifest=manifest,
    )

    assert plan["unmapped_protected_paths"] == ["src/zetherion_ai/memory/qdrant.py"]


# The real manifest should never leave a protected path uncovered.
def test_manifest_protected_globs_are_all_covered_by_a_rule() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    protected_globs = manifest["protected_globs"]
    rules = manifest["rules"]

    for protected_glob in protected_globs:
        assert any(protected_glob in rule["patterns"] for rule in rules), protected_glob
