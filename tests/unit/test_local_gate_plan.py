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


def _required_path_ids(plan: dict[str, object]) -> set[str]:
    return {item["id"] for item in plan["required_paths"]}  # type: ignore[index]


def _lane_ids(plan: dict[str, object]) -> set[str]:
    return {item["lane_id"] for item in plan["lanes"]}  # type: ignore[index]


def test_api_route_change_requires_docs_bundle_and_mypy() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["src/zetherion_ai/api/server.py"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {"bandit-src", "endpoint-doc-bundle", "mypy-src"}
    assert plan["unmapped_protected_paths"] == []


def test_docs_configuration_change_requires_docs_contract() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["docs/technical/configuration.md", ".env.example"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {"docs-contract"}
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


def test_trust_runtime_change_requires_unit_full() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["src/zetherion_ai/trust/runtime.py"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {"bandit-src", "mypy-src", "unit-full"}
    assert plan["unmapped_protected_paths"] == []


def test_profile_builder_change_requires_unit_full() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["src/zetherion_ai/profile/builder.py"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {"bandit-src", "mypy-src", "unit-full"}
    assert plan["unmapped_protected_paths"] == []


def test_routing_policy_change_requires_unit_full() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["src/zetherion_ai/routing/policies.py"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {"bandit-src", "mypy-src", "unit-full"}
    assert plan["unmapped_protected_paths"] == []


def test_queue_manager_change_requires_unit_full() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["src/zetherion_ai/queue/manager.py"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {"bandit-src", "mypy-src", "unit-full"}
    assert _required_path_ids(plan) == {
        "queue_reliability",
        "runtime_status_persistence",
        "startup_readiness",
    }
    assert _lane_ids(plan) >= {
        "z-unit-core",
        "z-unit-runtime",
        "z-int-runtime-queue",
        "z-e2e-faults",
    }
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


def test_ci_support_script_change_requires_regression_packs() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["scripts/check-cicd-success.sh"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {
        "ci-failure-attribution-regression-suite",
        "ci-receipt-regression-suite",
    }
    assert _required_path_ids(plan) == {
        "owner_ci_cutover",
        "owner_ci_receipts",
        "release_contracts",
    }
    assert _lane_ids(plan) >= {"z-unit-owner-ci", "z-release"}
    assert plan["unmapped_protected_paths"] == []


def test_deploy_preflight_helper_change_requires_regression_packs() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["scripts/windows/deploy-runner.ps1"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {
        "ci-receipt-regression-suite",
        "deploy-preflight-regression-suite",
    }
    assert plan["unmapped_protected_paths"] == []


def test_failure_attribution_script_change_requires_targeted_suite() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    plan = module.build_plan(
        changed_paths=["scripts/ci_failure_attribution.py"],
        manifest=manifest,
    )

    assert _requirement_ids(plan) == {
        "ci-failure-attribution-regression-suite",
        "ci-receipt-regression-suite",
    }
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
# The real manifest should never leave a protected path uncovered, including the
# shared-runtime directory globs added for the CI hardening rollout.
def test_manifest_protected_globs_are_all_covered_by_a_rule() -> None:
    module = _load_module()
    manifest = module.load_manifest()

    protected_globs = manifest["protected_globs"]
    rules = manifest["rules"]

    for protected_glob in protected_globs:
        assert any(protected_glob in rule["patterns"] for rule in rules), protected_glob
