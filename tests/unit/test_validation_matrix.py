"""Tests for cross-repo validation matrix compilation."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from subprocess import CompletedProcess

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module(relative_path: str, module_name: str):
    module_path = REPO_ROOT / relative_path
    spec = importlib.util.spec_from_file_location(module_name, module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_compile_validation_matrix_includes_repo_and_combined_modes(tmp_path: Path) -> None:
    compile_module = _load_module(
        "scripts/testing/compile_validation_matrix.py",
        "compile_validation_matrix_module",
    )

    cgs_manifest_path = tmp_path / "cgs-shard-manifest.json"
    cgs_manifest_path.write_text(
        json.dumps(
            {
                "repo_id": "catalyst-group-solutions",
                "validation_mode": "cgs_alone",
                "resource_limits": {"cpu": 4},
                "shards": [
                    {
                        "lane_id": "c-unit-coverage",
                        "label": "CGS unit coverage",
                        "lane_family": "unit",
                        "validation_mode": "cgs_alone",
                        "shard_purpose": "Cover CGS unit surface.",
                        "resource_class": "cpu",
                        "release_blocking": True,
                        "expected_artifacts": ["coverage-summary.json"],
                        "command": "yarn test:ci",
                    }
                ],
            }
        ),
        encoding="utf-8",
    )
    combined_manifest_path = tmp_path / "system-validation.json"
    combined_manifest_path.write_text(
        json.dumps(
            {
                "mode_id": "combined_system",
                "mode_label": "CGS + Zetherion together",
                "description": "Combined contract checks.",
                "repo_ids": ["zetherion-ai", "catalyst-group-solutions"],
                "shards": [
                    {
                        "shard_id": "combined-contract",
                        "lane_family": "combined_system",
                        "purpose": "Validate contract flow.",
                        "blocking": True,
                        "resource_class": "cpu",
                        "depends_on": ["c-unit-coverage"],
                        "expected_artifacts": ["stdout", "stderr"],
                        "commands": [
                            {
                                "repo_id": "zetherion-ai",
                                "cwd": ".",
                                "command": ["bash", "-lc", "echo zetherion"],
                            },
                            {
                                "repo_id": "catalyst-group-solutions",
                                "cwd": "../catalyst-group-solutions",
                                "command": ["bash", "-lc", "echo cgs"],
                            },
                        ],
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    payload = compile_module.build_validation_matrix(
        cgs_manifest_path=cgs_manifest_path,
        combined_manifest_path=combined_manifest_path,
    )

    mode_ids = {mode["mode_id"] for mode in payload["modes"]}
    assert mode_ids == {"zetherion_alone", "cgs_alone", "combined_system"}

    zetherion_mode = next(mode for mode in payload["modes"] if mode["mode_id"] == "zetherion_alone")
    assert zetherion_mode["available"] is True
    assert {"static", "security", "unit", "integration"} <= set(
        zetherion_mode["lane_families"]
    )

    cgs_mode = next(mode for mode in payload["modes"] if mode["mode_id"] == "cgs_alone")
    assert cgs_mode["available"] is True
    assert cgs_mode["shards"][0]["expected_artifacts"] == ["coverage-summary.json"]

    combined_mode = next(mode for mode in payload["modes"] if mode["mode_id"] == "combined_system")
    assert combined_mode["available"] is True
    assert combined_mode["shards"][0]["repo_ids"] == [
        "zetherion-ai",
        "catalyst-group-solutions",
    ]


def test_combined_system_runner_builds_cross_repo_steps() -> None:
    runner_module = _load_module(
        "scripts/testing/run_combined_system_lane.py",
        "run_combined_system_lane_module",
    )
    shard = {
        "shard_id": "combined-contract",
        "commands": [
            {
                "repo_id": "zetherion-ai",
                "cwd": ".",
                "command": ["bash", "-lc", "echo zetherion"],
            },
            {
                "repo_id": "catalyst-group-solutions",
                "cwd": "../catalyst-group-solutions",
                "command": ["bash", "-lc", "echo cgs"],
            },
        ],
    }

    steps = runner_module.build_execution_steps(shard)

    assert len(steps) == 2
    assert steps[0]["repo_id"] == "zetherion-ai"
    assert steps[0]["cwd"] in {"/workspace", str(REPO_ROOT)}
    assert steps[1]["repo_id"] == "catalyst-group-solutions"
    assert steps[1]["cwd"].endswith("/catalyst-group-solutions")


def test_combined_system_runner_resolves_dependency_batches() -> None:
    runner_module = _load_module(
        "scripts/testing/run_combined_system_lane.py",
        "run_combined_system_lane_batches_module",
    )
    shards = [
        {
            "shard_id": "combined-a",
            "depends_on": [],
            "commands": [{"repo_id": "zetherion-ai", "command": ["bash", "-lc", "echo a"]}],
        },
        {
            "shard_id": "combined-b",
            "depends_on": [],
            "commands": [{"repo_id": "zetherion-ai", "command": ["bash", "-lc", "echo b"]}],
        },
        {
            "shard_id": "combined-c",
            "depends_on": ["combined-a"],
            "commands": [{"repo_id": "zetherion-ai", "command": ["bash", "-lc", "echo c"]}],
        },
    ]

    batches = runner_module.resolve_batches(shards)

    assert [[shard["shard_id"] for shard in batch] for batch in batches] == [
        ["combined-a", "combined-b"],
        ["combined-c"],
    ]


def test_combined_system_runner_treats_external_dependencies_as_preconditions() -> None:
    runner_module = _load_module(
        "scripts/testing/run_combined_system_lane.py",
        "run_combined_system_lane_validation_module",
    )
    shards = [
        {
            "shard_id": "combined-a",
            "depends_on": ["c-int-owner-ci"],
            "commands": [{"repo_id": "zetherion-ai", "command": ["bash", "-lc", "echo a"]}],
        },
        {
            "shard_id": "combined-b",
            "depends_on": ["combined-a", "z-unit-owner-ci"],
            "commands": [{"repo_id": "zetherion-ai", "command": ["bash", "-lc", "echo b"]}],
        },
    ]

    batches = runner_module.resolve_batches(shards)

    assert [[shard["shard_id"] for shard in batch] for batch in batches] == [
        ["combined-a"],
        ["combined-b"],
    ]


def test_combined_system_runner_rejects_duplicate_shard_ids() -> None:
    runner_module = _load_module(
        "scripts/testing/run_combined_system_lane.py",
        "run_combined_system_lane_duplicate_validation_module",
    )
    manifest = {
        "shards": [
            {
                "shard_id": "combined-a",
                "commands": [{"repo_id": "zetherion-ai", "command": ["bash", "-lc", "echo a"]}],
            },
            {
                "shard_id": "combined-a",
                "commands": [{"repo_id": "zetherion-ai", "command": ["bash", "-lc", "echo b"]}],
            },
        ]
    }

    try:
        runner_module.validate_manifest(manifest)
    except ValueError as exc:
        assert "duplicate" in str(exc)
    else:
        raise AssertionError("Expected validate_manifest() to reject duplicate shard ids")


def test_combined_system_runner_executes_batches_and_writes_summary(
    monkeypatch, tmp_path: Path
) -> None:
    runner_module = _load_module(
        "scripts/testing/run_combined_system_lane.py",
        "run_combined_system_lane_execute_module",
    )
    manifest = {
        "mode_id": "combined_system",
        "mode_label": "CGS + Zetherion together",
        "shards": [
            {
                "shard_id": "combined-a",
                "lane_family": "combined_system",
                "purpose": "A",
                "depends_on": [],
                "commands": [{"repo_id": "zetherion-ai", "command": ["bash", "-lc", "echo a"]}],
            },
            {
                "shard_id": "combined-b",
                "lane_family": "combined_system",
                "purpose": "B",
                "depends_on": ["combined-a"],
                "commands": [
                    {"repo_id": "catalyst-group-solutions", "command": ["bash", "-lc", "echo b"]}
                ],
            },
        ],
    }
    calls: list[tuple[list[str], str]] = []

    def _fake_run(command, cwd, check):  # type: ignore[no-untyped-def]
        calls.append((list(command), str(cwd)))
        return CompletedProcess(command, 0)

    monkeypatch.setattr(runner_module.subprocess, "run", _fake_run)

    summary = runner_module.run_manifest(
        manifest,
        workspace_root=tmp_path,
        max_parallel=2,
    )

    assert summary["all_passed"] is True
    assert [shard["shard_id"] for shard in summary["shards"]] == ["combined-a", "combined-b"]
    assert all(shard["status"] == "passed" for shard in summary["shards"])
    assert len(calls) == 2
