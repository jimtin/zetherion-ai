"""Tests for cross-repo validation matrix compilation."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from subprocess import CompletedProcess

from zetherion_ai.owner_ci.system_validation import (
    _candidate_set_from_input,
    _duration_seconds,
    _normalized_state,
    build_system_coaching,
    build_system_rollout_readiness,
    build_system_run_plan,
    build_system_run_report,
    build_system_run_usage_summary,
    build_validation_matrix,
    resolve_system_run_batches,
)

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
    public_core_gate = next(
        shard for shard in zetherion_mode["shards"] if shard["shard_id"] == "public-core-export"
    )
    assert public_core_gate["expected_artifacts"] == [
        "stdout",
        "stderr",
        "public-core-export-stage.json",
        "public-core-export-report.json",
    ]

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


def test_system_run_plan_and_readiness_include_repo_and_combined_profiles(
    tmp_path: Path,
) -> None:
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
                        "required_paths": ["cgs_release_verification"],
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

    candidate_set = {
        "system_id": "cgs-zetherion",
        "repos": [
            {"repo_id": "zetherion-ai", "git_ref": "feature/z"},
            {"repo_id": "catalyst-group-solutions", "git_ref": "feature/cgs"},
        ],
    }

    plan = build_system_run_plan(
        candidate_set=candidate_set,
        cgs_manifest_path=cgs_manifest_path,
        combined_manifest_path=combined_manifest_path,
    )
    readiness = build_system_rollout_readiness(
        candidate_set=candidate_set,
        cgs_manifest_path=cgs_manifest_path,
        combined_manifest_path=combined_manifest_path,
    )

    assert {profile["mode_id"] for profile in plan["profiles"]} == {
        "zetherion_alone",
        "cgs_alone",
        "combined_system",
    }
    assert any(
        shard["validation_mode"] == "combined_system" for shard in plan["shards"]
    )
    assert readiness["status"] == "ready"
    assert "Combined-system validation is ready" in readiness["summary"]


def test_system_coaching_blocks_when_repo_candidates_are_missing(tmp_path: Path) -> None:
    cgs_manifest_path = tmp_path / "cgs-shard-manifest.json"
    cgs_manifest_path.write_text(
        json.dumps(
            {
                "repo_id": "catalyst-group-solutions",
                "validation_mode": "cgs_alone",
                "resource_limits": {"cpu": 4},
                "shards": [],
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
                "repo_ids": ["zetherion-ai", "catalyst-group-solutions"],
                "shards": [],
            }
        ),
        encoding="utf-8",
    )

    coaching = build_system_coaching(
        candidate_set={
            "system_id": "cgs-zetherion",
            "repos": [{"repo_id": "zetherion-ai", "git_ref": "feature/z"}],
        },
        principal_id="codex-agent-1",
        cgs_manifest_path=cgs_manifest_path,
        combined_manifest_path=combined_manifest_path,
    )

    assert coaching[0]["scope"] == "system_run"
    assert coaching[0]["blocking"] is True
    assert coaching[0]["findings"][0]["rule_code"] == "missing_system_repo_candidates"
    assert "candidate refs" in coaching[0]["recommendations"][0]["agents_md_update"]


def test_system_run_report_and_usage_summarize_executed_system_validation() -> None:
    candidate_set = {
        "system_id": "cgs-zetherion",
        "mode_id": "combined_system",
        "repos": [
            {"repo_id": "zetherion-ai", "git_ref": "HEAD"},
            {"repo_id": "catalyst-group-solutions", "git_ref": "HEAD"},
        ],
    }
    plan = build_system_run_plan(candidate_set=candidate_set)
    execution = {
        "all_passed": False,
        "batches": [
            {
                "batch_index": 0,
                "status": "failed",
                "shard_ids": ["combined-contract"],
                "shards": [
                    {
                        "shard_id": "combined-contract",
                        "lane_id": "combined-contract",
                        "lane_label": "Combined contract",
                        "lane_family": "combined_system",
                        "validation_mode": "combined_system",
                        "purpose": "Validate contract flow.",
                        "blocking": True,
                        "repo_ids": [
                            "zetherion-ai",
                            "catalyst-group-solutions",
                        ],
                        "depends_on": [],
                        "expected_artifacts": ["stdout", "stderr"],
                        "required_paths": ["combined_contract"],
                        "status": "failed",
                        "started_at": "2026-03-17T10:00:00Z",
                        "completed_at": "2026-03-17T10:02:00Z",
                        "steps": [
                            {
                                "step_id": "zetherion-step-1",
                                "label": "zetherion",
                                "repo_id": "zetherion-ai",
                                "cwd": "/tmp/zetherion-ai",
                                "command": ["bash", "-lc", "echo zetherion"],
                                "status": "passed",
                                "return_code": 0,
                                "started_at": "2026-03-17T10:00:00Z",
                                "completed_at": "2026-03-17T10:01:00Z",
                            },
                            {
                                "step_id": "cgs-step-2",
                                "label": "cgs",
                                "repo_id": "catalyst-group-solutions",
                                "cwd": "/tmp/catalyst-group-solutions",
                                "command": ["bash", "-lc", "exit 1"],
                                "status": "failed",
                                "return_code": 1,
                                "started_at": "2026-03-17T10:01:00Z",
                                "completed_at": "2026-03-17T10:02:00Z",
                            },
                        ],
                    }
                ],
            }
        ],
    }
    execution["shards"] = execution["batches"][0]["shards"]

    usage = build_system_run_usage_summary(
        system_run_id="system-run-1",
        system_id="cgs-zetherion",
        mode_id="combined_system",
        candidate_set=candidate_set,
        execution=execution,
    )
    assert usage["failed_shard_count"] == 1
    assert usage["step_count"] == 2
    assert usage["billable_minutes"] == 2.0

    extra_usage_summary = build_system_run_usage_summary(
        system_run_id="system-run-extra",
        system_id="cgs-zetherion",
        mode_id="combined_system",
        candidate_set={
            "system_id": "cgs-zetherion",
            "mode_id": "combined_system",
            "repos": [
                {"repo_id": "zetherion-ai", "git_ref": "HEAD"},
                {"repo_id": "catalyst-group-solutions", "git_ref": "HEAD"},
            ],
        },
        execution={
            "all_passed": False,
            "batches": [],
            "shards": [
                {
                    "shard_id": "ad-hoc",
                    "expected_artifacts": ["stdout"],
                    "status": "failed",
                    "started_at": "2026-03-17T10:00:00Z",
                    "completed_at": "2026-03-17T10:01:00Z",
                    "steps": [
                        {
                            "step_id": "step-1",
                            "status": "failed",
                            "started_at": "2026-03-17T10:00:00Z",
                            "completed_at": "2026-03-17T10:01:00Z",
                        }
                    ],
                }
            ],
        },
    )

    report = build_system_run_report(
        {
            "system_run_id": "system-run-1",
            "system_id": "cgs-zetherion",
            "mode_id": "combined_system",
            "status": "failed",
            "candidate_set": candidate_set,
            "plan": plan,
            "readiness": {
                "system_id": "cgs-zetherion",
                "mode_id": "combined_system",
                "status": "ready",
                "blocking": False,
                "summary": "Ready to execute.",
                "blocker_count": 0,
                "blocking_shards": ["combined-contract"],
                "missing_repo_ids": [],
                "recommended_next_steps": [],
                "metadata": {},
                "checked_at": "2026-03-17T09:59:00Z",
            },
            "coaching": [
                {
                    "feedback_id": "system-coach-1",
                    "scope": "system_run",
                    "summary": "Run the blocking combined-system shards.",
                    "blocking": False,
                    "findings": [],
                    "recommendations": [],
                    "rule_violations": [],
                    "evidence_references": [],
                    "metadata": {},
                }
            ],
            "execution": execution,
            "usage_summary": usage,
            "metadata": {"environment": "local"},
            "error": {},
            "created_at": "2026-03-17T09:58:00Z",
            "updated_at": "2026-03-17T10:02:00Z",
            "started_at": "2026-03-17T10:00:00Z",
            "completed_at": "2026-03-17T10:02:00Z",
        }
    )

    assert resolve_system_run_batches(plan["shards"])
    assert report["usage_summary"]["billable_minutes"] == 2.0
    assert report["run_graph"]["nodes"][0]["node_id"] == "system-run:system-run-1"
    assert report["diagnostic_summary"]["blocking"] is True
    assert report["diagnostic_findings"][0]["code"] == "system_shard_failed"
    assert report["artifacts"][0]["kind"] == "expected_artifact"


def test_system_coaching_blocks_when_validation_profiles_are_unavailable(tmp_path: Path) -> None:
    missing_cgs_manifest = tmp_path / "missing-cgs.json"
    missing_combined_manifest = tmp_path / "missing-combined.json"

    coaching = build_system_coaching(
        candidate_set={
            "system_id": "cgs-zetherion",
            "repos": [
                {"repo_id": "zetherion-ai", "git_ref": "feature/z"},
                {"repo_id": "catalyst-group-solutions", "git_ref": "feature/cgs"},
            ],
        },
        principal_id="codex-agent-1",
        cgs_manifest_path=missing_cgs_manifest,
        combined_manifest_path=missing_combined_manifest,
    )

    assert coaching[0]["blocking"] is True
    assert coaching[0]["findings"][0]["rule_code"] == "missing_system_validation_profile"


def test_validation_matrix_marks_missing_manifests_unavailable(tmp_path: Path) -> None:
    matrix = build_validation_matrix(
        cgs_manifest_path=tmp_path / "missing-cgs.json",
        combined_manifest_path=tmp_path / "missing-combined.json",
    )

    modes = {mode["mode_id"]: mode for mode in matrix["modes"]}
    assert modes["zetherion_alone"]["available"] is True
    assert modes["cgs_alone"]["available"] is False
    assert "missing manifest" in modes["cgs_alone"]["metadata"]["unavailable_reason"]
    assert modes["combined_system"]["available"] is False
    assert "missing manifest" in modes["combined_system"]["metadata"]["unavailable_reason"]


def test_system_coaching_returns_ready_guidance_when_profiles_are_available(tmp_path: Path) -> None:
    cgs_manifest_path = tmp_path / "cgs-shard-manifest.json"
    cgs_manifest_path.write_text(
        json.dumps(
            {
                "repo_id": "catalyst-group-solutions",
                "validation_mode": "cgs_alone",
                "resource_limits": {"cpu": 4},
                "shards": [],
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
                "repo_ids": ["zetherion-ai", "catalyst-group-solutions"],
                "shards": [],
            }
        ),
        encoding="utf-8",
    )

    coaching = build_system_coaching(
        candidate_set={
            "system_id": "cgs-zetherion",
            "repos": [
                {"repo_id": "zetherion-ai", "git_ref": "feature/z"},
                {"repo_id": "catalyst-group-solutions", "git_ref": "feature/cgs"},
            ],
        },
        principal_id="codex-agent-1",
        cgs_manifest_path=cgs_manifest_path,
        combined_manifest_path=combined_manifest_path,
    )

    assert coaching[0]["blocking"] is False
    assert coaching[0]["findings"][0]["rule_code"] == "system_validation_ready"
    assert "combined-system validation" in coaching[0]["recommendations"][0]["agents_md_update"]


def test_system_validation_helper_paths_cover_state_normalization_and_defaults() -> None:
    assert _normalized_state("running") == "running"
    assert _normalized_state("queued") == "queued"
    assert _normalized_state("skipped") == "skipped"
    assert _normalized_state(None) == "unknown"

    candidate_set = _candidate_set_from_input("invalid")
    assert candidate_set.repos == []

    assert _duration_seconds("", "2026-03-17T10:00:00Z") == 0.0
    assert _duration_seconds("bad-date", "2026-03-17T10:00:00Z") == 0.0
    assert _duration_seconds("2026-03-17T10:02:00Z", "2026-03-17T10:00:00Z") == 0.0


def test_system_run_plan_handles_sparse_combined_manifest_defaults(tmp_path: Path) -> None:
    cgs_manifest_path = tmp_path / "cgs-shard-manifest.json"
    cgs_manifest_path.write_text(
        json.dumps(
            {
                "repo_id": "catalyst-group-solutions",
                "validation_mode": "cgs_alone",
                "resource_limits": {"cpu": 4},
                "shards": [],
            }
        ),
        encoding="utf-8",
    )
    combined_manifest_path = tmp_path / "system-validation.json"
    combined_manifest_path.write_text(
        json.dumps(
            {
                "repo_ids": ["zetherion-ai", "catalyst-group-solutions"],
                "shards": [
                    "skip-me",
                    {
                        "repo_id": "zetherion-ai",
                        "purpose": "Fallback shard",
                        "depends_on": ["unknown-shard"],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    matrix = build_validation_matrix(
        cgs_manifest_path=cgs_manifest_path,
        combined_manifest_path=combined_manifest_path,
    )
    combined_mode = next(mode for mode in matrix["modes"] if mode["mode_id"] == "combined_system")
    fallback_shard = combined_mode["shards"][0]
    assert combined_mode["mode_label"] == "Validation mode"
    assert combined_mode["description"] == (
        "Controlled local contract validation across the CGS and Zetherion "
        "control-plane boundary."
    )
    assert combined_mode["blocking_categories"] == ["combined_system"]
    assert fallback_shard["repo_ids"] == ["zetherion-ai"]
    assert fallback_shard["lane_family"] == "integration"
    assert fallback_shard["validation_mode"] == "combined_system"
    assert fallback_shard["shard_id"] == "Fallback shard"


def test_system_run_helpers_cover_cycles_list_candidates_and_unplanned_shards(
    tmp_path: Path,
) -> None:
    cgs_manifest_path = tmp_path / "cgs-shard-manifest.json"
    cgs_manifest_path.write_text(
        json.dumps(
            {
                "repo_id": "catalyst-group-solutions",
                "validation_mode": "cgs_alone",
                "resource_limits": {"cpu": 4},
                "shards": [
                    {
                        "lane_id": "c-unit",
                        "label": "CGS unit",
                        "lane_family": "unit",
                        "validation_mode": "cgs_alone",
                        "shard_purpose": "Run CGS unit tests",
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
                "repo_ids": ["zetherion-ai", "catalyst-group-solutions"],
                "shards": [
                    {
                        "shard_id": "combined-a",
                        "lane_family": "combined_system",
                        "purpose": "Validate A",
                        "blocking": True,
                        "depends_on": ["combined-b"],
                        "commands": [
                            {
                                "repo_id": "zetherion-ai",
                                "cwd": ".",
                                "command": ["bash", "-lc", "echo a"],
                            }
                        ],
                    },
                    {
                        "shard_id": "combined-b",
                        "lane_family": "combined_system",
                        "purpose": "Validate B",
                        "blocking": True,
                        "depends_on": ["combined-a"],
                        "commands": [
                            {
                                "repo_id": "catalyst-group-solutions",
                                "cwd": ".",
                                "command": ["bash", "-lc", "echo b"],
                            }
                        ],
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    try:
        resolve_system_run_batches(
            [
                {"shard_id": "a", "depends_on": ["b"]},
                {"shard_id": "b", "depends_on": ["a"]},
            ]
        )
    except ValueError as exc:
        assert "dependency cycles" in str(exc)
    else:
        raise AssertionError("Expected dependency cycle detection")

    plan = build_system_run_plan(
        candidate_set=[
            {"repo_id": "zetherion-ai", "git_ref": "feature/z"},
            {"repo_id": "catalyst-group-solutions", "git_ref": "feature/cgs"},
            {"repo_id": "ignored-repo", "git_ref": "feature/ignored"},
        ],
        cgs_manifest_path=cgs_manifest_path,
        combined_manifest_path=combined_manifest_path,
    )
    assert {profile["mode_id"] for profile in plan["profiles"]} == {
        "zetherion_alone",
        "cgs_alone",
        "combined_system",
    }

    usage_summary = build_system_run_usage_summary(
        system_run_id="system-run-extra",
        system_id="cgs-zetherion",
        mode_id="combined_system",
        candidate_set={
            "system_id": "cgs-zetherion",
            "mode_id": "combined_system",
            "repos": [
                {"repo_id": "zetherion-ai", "git_ref": "HEAD"},
                {"repo_id": "catalyst-group-solutions", "git_ref": "HEAD"},
            ],
        },
        execution={
            "all_passed": False,
            "batches": [],
            "shards": [
                {
                    "shard_id": "ad-hoc",
                    "expected_artifacts": ["stdout"],
                    "status": "failed",
                    "started_at": "2026-03-17T10:00:00Z",
                    "completed_at": "2026-03-17T10:01:00Z",
                    "steps": [
                        {
                            "step_id": "step-1",
                            "status": "failed",
                            "started_at": "2026-03-17T10:00:00Z",
                            "completed_at": "2026-03-17T10:01:00Z",
                        }
                    ],
                }
            ],
        },
    )

    report = build_system_run_report(
        {
            "system_run_id": "system-run-extra",
            "system_id": "cgs-zetherion",
            "mode_id": "combined_system",
            "status": "blocked",
            "candidate_set": {
                "system_id": "cgs-zetherion",
                "mode_id": "combined_system",
                "repos": [
                    {"repo_id": "zetherion-ai", "git_ref": "HEAD"},
                    {"repo_id": "catalyst-group-solutions", "git_ref": "HEAD"},
                ],
            },
            "plan": {
                "system_id": "cgs-zetherion",
                "mode_id": "combined_system",
                "candidate_set": {
                    "repos": [
                        {"repo_id": "zetherion-ai", "git_ref": "HEAD"},
                        {"repo_id": "catalyst-group-solutions", "git_ref": "HEAD"},
                    ]
                },
                "profiles": [],
                "shards": [],
                "blocking_categories": ["combined_system"],
                "summary": "Plan",
                "metadata": {},
            },
            "readiness": {
                "system_id": "cgs-zetherion",
                "mode_id": "combined_system",
                "status": "blocked",
                "blocking": True,
                "summary": "Missing system manifests.",
                "blocker_count": 1,
                "blocking_shards": [],
                "missing_repo_ids": [],
                "recommended_next_steps": [],
                "metadata": {},
                "checked_at": "2026-03-17T10:00:00Z",
            },
            "coaching": [],
            "execution": {
                "all_passed": False,
                "batches": [],
                "shards": [
                    {
                        "shard_id": "ad-hoc",
                        "lane_family": "combined_system",
                        "validation_mode": "combined_system",
                        "purpose": "Ad hoc failure",
                        "blocking": True,
                        "repo_ids": ["zetherion-ai"],
                        "depends_on": [],
                        "required_paths": ["combined_contract"],
                        "expected_artifacts": ["stdout"],
                        "status": "failed",
                        "started_at": "2026-03-17T10:00:00Z",
                        "completed_at": "2026-03-17T10:01:00Z",
                        "steps": [
                            {
                                "step_id": "step-1",
                                "label": "Run",
                                "repo_id": "zetherion-ai",
                                "cwd": "/tmp/zetherion-ai",
                                "command": "echo bad",
                                "status": "failed",
                                "return_code": 1,
                                "started_at": "2026-03-17T10:00:00Z",
                                "completed_at": "2026-03-17T10:01:00Z",
                            },
                            "ignore-me",
                        ],
                    }
                ],
            },
            "usage_summary": usage_summary,
            "metadata": {},
            "error": {"code": "blocked"},
            "created_at": "2026-03-17T09:58:00Z",
            "updated_at": "2026-03-17T10:01:00Z",
            "started_at": "2026-03-17T10:00:00Z",
            "completed_at": "2026-03-17T10:01:00Z",
        }
    )
    assert extra_usage_summary["failed_shard_count"] == 1

    codes = {finding["code"] for finding in report["diagnostic_findings"]}
    assert "system_shard_failed" in codes
    assert "system_run_readiness_blocked" in codes
    assert any(
        node["node_id"] == "system-shard:system-run-extra:ad-hoc"
        for node in report["run_graph"]["nodes"]
    )
