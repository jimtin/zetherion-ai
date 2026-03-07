"""Regression tests for scripts/require-local-gate-update.sh."""

from __future__ import annotations

import json
import os
import stat
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCRIPT_PATH = REPO_ROOT / "scripts" / "require-local-gate-update.sh"


def _init_repo(tmp_path: Path) -> str:
    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True, text=True)
    subprocess.run(["git", "config", "user.name", "Test User"], cwd=tmp_path, check=True)
    subprocess.run(["git", "config", "user.email", "test@example.com"], cwd=tmp_path, check=True)

    for rel in [
        "AGENTS.md",
        ".ci/pipeline_contract.json",
        "scripts/local_gate_plan.py",
        "docs/development/canonical-test-gate-and-ci-cost-plan.md",
    ]:
        path = tmp_path / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("baseline\n", encoding="utf-8")

    subprocess.run(["git", "add", "."], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "commit", "-m", "baseline"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    )
    return subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


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
                print(json.dumps(fixtures["run_list"]))
                raise SystemExit(0)
            if args[:2] == ["run", "download"]:
                run_id = args[2]
                name = args[args.index("--name") + 1]
                target_dir = Path(args[args.index("--dir") + 1])
                artifact_dir = target_dir / name
                artifact_dir.mkdir(parents=True, exist_ok=True)
                payload = fixtures["download_artifacts"][run_id][name]
                for filename, data in payload.items():
                    (artifact_dir / filename).write_text(json.dumps(data), encoding="utf-8")
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


def test_require_local_gate_update_accepts_specific_local_gate_breach(tmp_path: Path) -> None:
    head_sha = _init_repo(tmp_path)
    (tmp_path / "scripts" / "local_gate_plan.py").write_text("updated\n", encoding="utf-8")
    (tmp_path / "AGENTS.md").write_text("updated\n", encoding="utf-8")

    fixtures = {
        "run_list": [
            {
                "databaseId": 1001,
                "workflowName": "CI/CD Pipeline",
                "headSha": head_sha,
                "status": "completed",
                "conclusion": "failure",
                "createdAt": "2026-03-08T00:00:00Z",
                "url": "https://example.invalid/runs/1001",
            }
        ],
        "download_artifacts": {
            "1001": {
                "ci-failure-attribution": {
                    "ci-failure-attribution.json": {
                        "generated_at": "2026-03-08T00:00:00Z",
                        "failed_job_count": 1,
                        "failures": [
                            {
                                "job": "unit-test",
                                "result": "failure",
                                "reason_code": "LOCAL_GATE_BREACH_UNIT_AND_MYPY",
                                "explanation": "Unit coverage and mypy are local requirements.",
                            }
                        ],
                    }
                }
            }
        },
    }

    env = _write_fake_gh(tmp_path, fixtures)
    result = subprocess.run(
        ["bash", str(SCRIPT_PATH), "--sha", head_sha],
        cwd=tmp_path,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    assert "Local gate update requirements satisfied" in result.stdout
