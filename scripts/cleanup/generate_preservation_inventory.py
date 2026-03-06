#!/usr/bin/env python3
"""Generate a preservation inventory and remote ref snapshot for cleanup work."""

from __future__ import annotations

import argparse
import json
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_SEARCH_ROOTS = (
    Path.home() / "Developer",
    Path.home() / "Development",
    Path.home() / "Documents",
)


@dataclass(frozen=True)
class CandidateClassification:
    category: str
    detail: str


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
    except ValueError:
        return False
    return True


def _run_command(
    command: list[str],
    *,
    cwd: Path | None = None,
    check: bool = True,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        command,
        cwd=cwd,
        check=check,
        capture_output=True,
        text=True,
    )


def discover_candidate_paths(
    *,
    search_roots: list[Path],
    repo_root: Path,
    extra_paths: list[Path] | None = None,
) -> list[Path]:
    candidates: set[Path] = {repo_root.resolve()}
    for extra_path in extra_paths or []:
        candidates.add(extra_path.resolve())

    for search_root in search_roots:
        if not search_root.exists():
            continue
        if search_root.name == "Documents":
            for developer_root in sorted(search_root.glob("Developer*")):
                if not developer_root.is_dir():
                    continue
                for candidate in sorted(developer_root.glob("PersonalBot*")):
                    if candidate.is_dir():
                        candidates.add(candidate.resolve())
            continue

        for candidate in sorted(search_root.glob("PersonalBot*")):
            if candidate.is_dir():
                candidates.add(candidate.resolve())

    return sorted(candidates)


def parse_worktree_paths(output: str) -> set[Path]:
    worktree_paths: set[Path] = set()
    for line in output.splitlines():
        if not line.startswith("worktree "):
            continue
        worktree_paths.add(Path(line.split(" ", 1)[1]).resolve())
    return worktree_paths


def collect_worktree_paths(repo_root: Path) -> set[Path]:
    completed = _run_command(
        ["git", "worktree", "list", "--porcelain"],
        cwd=repo_root,
    )
    return parse_worktree_paths(completed.stdout)


def parse_remote_heads(output: str) -> list[dict[str, str]]:
    branches: list[dict[str, str]] = []
    for line in output.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        sha, ref = stripped.split("\t", 1)
        if not ref.startswith("refs/heads/"):
            continue
        branches.append({"name": ref.removeprefix("refs/heads/"), "sha": sha})
    return branches


def classify_candidate(
    *,
    path: Path,
    repo_root: Path,
    archive_root: Path,
    git_readable: bool,
    is_bare: bool,
    is_known_worktree: bool,
) -> CandidateClassification:
    if _is_relative_to(path, archive_root):
        return CandidateClassification(category="archive", detail="under_archive_root")
    if path.resolve() == repo_root.resolve():
        return CandidateClassification(category="active_clone", detail="canonical_repo_root")
    if not git_readable:
        return CandidateClassification(category="corrupt_backup", detail="git_metadata_unreadable")
    if is_bare:
        return CandidateClassification(category="bare_mirror", detail="bare_repository")
    if is_known_worktree:
        return CandidateClassification(category="backup_clone", detail="linked_git_worktree")
    return CandidateClassification(category="backup_clone", detail="standalone_git_clone")


def collect_git_metadata(path: Path) -> dict[str, Any]:
    metadata: dict[str, Any] = {
        "git_readable": False,
        "is_bare": False,
        "branch": "",
        "head": "",
        "origin_url": "",
        "errors": [],
    }
    try:
        metadata["is_bare"] = (
            _run_command(
                ["git", "-C", str(path), "rev-parse", "--is-bare-repository"]
            ).stdout.strip()
            == "true"
        )
        metadata["git_readable"] = True
    except subprocess.CalledProcessError as exc:
        metadata["errors"].append(exc.stderr.strip() or exc.stdout.strip() or str(exc))
        return metadata

    for key, command in (
        ("branch", ["git", "-C", str(path), "branch", "--show-current"]),
        ("head", ["git", "-C", str(path), "rev-parse", "HEAD"]),
        ("origin_url", ["git", "-C", str(path), "remote", "get-url", "origin"]),
    ):
        completed = _run_command(command, check=False)
        if completed.returncode == 0:
            metadata[key] = completed.stdout.strip()
            continue
        command_label = " ".join(command)
        metadata["errors"].append(
            completed.stderr.strip()
            or completed.stdout.strip()
            or f"command failed: {command_label}"
        )

    return metadata


def collect_remote_inventory(repo_root: Path) -> dict[str, Any]:
    origin_url = _run_command(["git", "remote", "get-url", "origin"], cwd=repo_root).stdout.strip()
    origin_main_sha = _run_command(
        ["git", "rev-parse", "origin/main"],
        cwd=repo_root,
    ).stdout.strip()
    open_prs = json.loads(
        _run_command(
            [
                "gh",
                "pr",
                "list",
                "--state",
                "open",
                "--json",
                "number,title,headRefName,baseRefName,isDraft,url",
            ],
            cwd=repo_root,
        ).stdout
    )
    branches = parse_remote_heads(
        _run_command(["git", "ls-remote", "--heads", "origin"], cwd=repo_root).stdout
    )

    return {
        "origin_url": origin_url,
        "origin_main_sha": origin_main_sha,
        "open_prs": open_prs,
        "remote_branches": branches,
    }


def build_inventory(
    *,
    repo_root: Path,
    archive_root: Path,
    search_roots: list[Path],
    mirror_path: Path,
) -> dict[str, Any]:
    worktree_paths = collect_worktree_paths(repo_root)
    candidates = discover_candidate_paths(
        search_roots=search_roots,
        repo_root=repo_root,
        extra_paths=sorted(worktree_paths),
    )

    local_directories: list[dict[str, Any]] = []
    for candidate in candidates:
        git_metadata = collect_git_metadata(candidate)
        classification = classify_candidate(
            path=candidate,
            repo_root=repo_root,
            archive_root=archive_root,
            git_readable=bool(git_metadata["git_readable"]),
            is_bare=bool(git_metadata["is_bare"]),
            is_known_worktree=candidate in worktree_paths,
        )
        local_directories.append(
            {
                "path": str(candidate),
                "classification": classification.category,
                "classification_detail": classification.detail,
                "git": git_metadata,
            }
        )

    remote_inventory = collect_remote_inventory(repo_root)
    return {
        "generated_at": datetime.now(tz=UTC).isoformat(),
        "repo_root": str(repo_root),
        "archive_root": str(archive_root),
        "mirror_path": str(mirror_path),
        "search_roots": [str(path) for path in search_roots],
        "local_directories": local_directories,
        "remote": remote_inventory,
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive-root", required=True)
    parser.add_argument("--mirror-path", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--repo-root", default=str(REPO_ROOT))
    parser.add_argument("--search-root", action="append", default=[])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    repo_root = Path(args.repo_root).resolve()
    archive_root = Path(args.archive_root).resolve()
    mirror_path = Path(args.mirror_path).resolve()
    output_path = Path(args.output).resolve()
    search_roots = [Path(value).resolve() for value in args.search_root] or list(
        DEFAULT_SEARCH_ROOTS
    )

    inventory = build_inventory(
        repo_root=repo_root,
        archive_root=archive_root,
        search_roots=search_roots,
        mirror_path=mirror_path,
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(inventory, indent=2), encoding="utf-8")
    print(json.dumps(inventory, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
