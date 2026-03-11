"""Regression tests for scripts/check-cicd-success.sh."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "check-cicd-success.sh"


def _write_fake_gh(tmp_path: Path, fixtures: dict[str, object]) -> dict[str, str]:
    fixtures_path = tmp_path / "fixtures.json"
    fixtures_path.write_text(json.dumps(fixtures), encoding="utf-8")

    gh_path = tmp_path / "gh"
    gh_path.write_text(
        textwrap.dedent(
            """\
            #!/usr/bin/env python3
            import json
            import os
            import sys
            from pathlib import Path

            fixtures = json.loads(Path(os.environ["FAKE_GH_FIXTURES"]).read_text(encoding="utf-8"))
            args = sys.argv[1:]

            if args[:2] == ["run", "list"]:
                print(json.dumps(fixtures.get("run_list", [])))
                raise SystemExit(0)

            if args[:1] == ["api"]:
                endpoint = args[1]
                if endpoint.endswith("/check-runs"):
                    print(json.dumps(fixtures.get("check_runs", {"check_runs": []})))
                    raise SystemExit(0)
                if endpoint.endswith("/pulls"):
                    print(json.dumps(fixtures.get("pulls", [])))
                    raise SystemExit(0)
                raise SystemExit(f"unexpected gh api endpoint: {endpoint}")

            if args[:2] == ["run", "download"]:
                run_id = args[2]
                name = args[args.index("--name") + 1]
                target_dir = Path(args[args.index("--dir") + 1])
                artifact_payload = fixtures["download_artifacts"][run_id][name]
                artifact_dir = target_dir / name
                artifact_dir.mkdir(parents=True, exist_ok=True)
                for filename, payload in artifact_payload.items():
                    (artifact_dir / filename).write_text(json.dumps(payload), encoding="utf-8")
                raise SystemExit(0)

            raise SystemExit(f"unexpected gh invocation: {args}")
            """
        ),
        encoding="utf-8",
    )
    gh_path.chmod(gh_path.stat().st_mode | stat.S_IEXEC)

    env = os.environ.copy()
    env["FAKE_GH_FIXTURES"] = str(fixtures_path)
    env["PATH"] = f"{tmp_path}:{env['PATH']}"
    return env


def _run_script(
    tmp_path: Path, fixtures: dict[str, object], *args: str
) -> subprocess.CompletedProcess[str]:
    env = _write_fake_gh(tmp_path, fixtures)
    return subprocess.run(
        ["bash", str(SCRIPT_PATH), *args],
        cwd=REPO_ROOT,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_main_check_run_fallback_validates_deploy_receipt(tmp_path: Path) -> None:
    target_sha = "a" * 40
    fixtures = {
        "run_list": [
            {
                "databaseId": 9001,
                "workflowName": "Deploy Windows",
                "headSha": target_sha,
                "headBranch": "main",
                "status": "completed",
                "conclusion": "success",
                "createdAt": "2026-03-08T00:00:00Z",
                "url": "https://example.invalid/deploy/9001",
            }
        ],
        "check_runs": {
            "check_runs": [
                {
                    "name": "CI Gate / CI Summary",
                    "status": "completed",
                    "conclusion": "success",
                },
                {
                    "name": "CI Gate / Required E2E Gate",
                    "status": "completed",
                    "conclusion": "success",
                },
            ]
        },
        "download_artifacts": {
            "9001": {
                "deployment-receipt": {
                    "deployment-receipt.json": {
                        "status": "success",
                        "target_sha": target_sha,
                        "deployed_sha": target_sha,
                        "core_status": "healthy",
                        "aux_status": "degraded",
                        "checks": {
                            "containers_healthy": True,
                            "auxiliary_services_healthy": False,
                            "bot_startup_markers": True,
                            "postgres_model_keys": True,
                            "fallback_probe": True,
                            "recovery_tasks_registered": True,
                            "runner_service_persistent": True,
                            "docker_service_persistent": True,
                        },
                    }
                }
            }
        },
    }

    result = _run_script(tmp_path, fixtures, "--sha", target_sha, "--ref", "main")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "CI success verified: source=check-runs" in result.stdout
    assert "Deployment success verified:" in result.stdout


def test_main_associated_pr_ci_fallback_validates_deploy_receipt(tmp_path: Path) -> None:
    target_sha = "c" * 40
    pr_head_sha = "d" * 40
    fixtures = {
        "run_list": [
            {
                "databaseId": 7001,
                "workflowName": "CI/CD Pipeline",
                "headSha": pr_head_sha,
                "headBranch": "codex/fix-main-ci-proof-fallback",
                "status": "completed",
                "conclusion": "success",
                "createdAt": "2026-03-10T00:00:00Z",
                "url": "https://example.invalid/ci/7001",
            },
            {
                "databaseId": 9002,
                "workflowName": "Deploy Windows",
                "headSha": target_sha,
                "headBranch": "main",
                "status": "completed",
                "conclusion": "success",
                "createdAt": "2026-03-10T00:05:00Z",
                "url": "https://example.invalid/deploy/9002",
            },
        ],
        "check_runs": {"check_runs": []},
        "pulls": [
            {
                "number": 150,
                "merged_at": "2026-03-10T00:04:00Z",
                "merge_commit_sha": target_sha,
                "head": {
                    "sha": pr_head_sha,
                    "ref": "codex/fix-main-ci-proof-fallback",
                },
            }
        ],
        "download_artifacts": {
            "9002": {
                "deployment-receipt": {
                    "deployment-receipt.json": {
                        "status": "success",
                        "target_sha": target_sha,
                        "deployed_sha": target_sha,
                        "core_status": "healthy",
                        "aux_status": "healthy",
                        "checks": {
                            "containers_healthy": True,
                            "auxiliary_services_healthy": True,
                            "bot_startup_markers": True,
                            "postgres_model_keys": True,
                            "fallback_probe": True,
                            "recovery_tasks_registered": True,
                            "runner_service_persistent": True,
                            "docker_service_persistent": True,
                        },
                    }
                }
            }
        },
    }

    result = _run_script(tmp_path, fixtures, "--sha", target_sha, "--ref", "main")

    assert result.returncode == 0, result.stderr + result.stdout
    assert "CI success verified: source=associated-pr-ci" in result.stdout
    assert "run_id=7001" in result.stdout
    assert "Deployment success verified:" in result.stdout


def test_pending_main_check_runs_fail_with_pending_diagnostic(tmp_path: Path) -> None:
    target_sha = "b" * 40
    fixtures = {
        "run_list": [],
        "check_runs": {
            "check_runs": [
                {
                    "name": "CI Gate / CI Summary",
                    "status": "in_progress",
                    "conclusion": None,
                },
                {
                    "name": "CI Gate / Required E2E Gate",
                    "status": "completed",
                    "conclusion": "success",
                },
            ]
        },
        "download_artifacts": {},
    }

    result = _run_script(
        tmp_path,
        fixtures,
        "--sha",
        target_sha,
        "--ref",
        "main",
        "--wait-seconds",
        "0",
    )

    assert result.returncode == 1
    assert "ERROR: CI verification is still pending" in result.stdout
    assert "Required main check-runs:" in result.stdout
