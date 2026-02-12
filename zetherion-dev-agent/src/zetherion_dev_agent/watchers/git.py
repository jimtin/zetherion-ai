"""Git watcher — detects new commits, branches, and tags."""

from __future__ import annotations

import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path


@dataclass
class CommitInfo:
    """A single git commit."""

    sha: str
    message: str
    author: str
    files_changed: int
    insertions: int
    deletions: int
    branch: str


@dataclass
class TagInfo:
    """A git tag."""

    name: str
    sha: str


def get_current_branch(repo_path: str) -> str:
    """Get the current branch name."""
    result = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "--abbrev-ref", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else "unknown"


def get_repo_name(repo_path: str) -> str:
    """Get the repository name from the remote or directory."""
    result = subprocess.run(  # nosec B603 B607
        ["git", "remote", "get-url", "origin"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode == 0 and result.stdout.strip():
        url = result.stdout.strip()
        # Extract name from URL like git@github.com:user/repo.git
        name = url.rstrip("/").split("/")[-1]
        if name.endswith(".git"):
            name = name[:-4]
        return name
    return Path(repo_path).name


def get_new_commits(repo_path: str, since_sha: str | None) -> list[CommitInfo]:
    """Get commits since a given SHA (or last 5 if no SHA)."""
    branch = get_current_branch(repo_path)

    range_arg = f"{since_sha}..HEAD" if since_sha else "-5"

    # Get commit list
    result = subprocess.run(  # nosec B603 B607
        ["git", "log", range_arg, "--format=%H|%s|%an", "--no-merges"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    commits = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        parts = line.split("|", 2)
        if len(parts) < 3:
            continue
        sha, message, author = parts

        # Get diffstat for this commit
        stat_result = subprocess.run(  # nosec B603 B607
            ["git", "diff", "--shortstat", f"{sha}~1", sha],
            cwd=repo_path,
            capture_output=True,
            text=True,
            timeout=10,
        )
        files_changed, insertions, deletions = _parse_shortstat(stat_result.stdout)

        commits.append(
            CommitInfo(
                sha=sha,
                message=message,
                author=author,
                files_changed=files_changed,
                insertions=insertions,
                deletions=deletions,
                branch=branch,
            )
        )

    return commits


def get_latest_sha(repo_path: str) -> str | None:
    """Get the latest commit SHA."""
    result = subprocess.run(  # nosec B603 B607
        ["git", "rev-parse", "HEAD"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    return result.stdout.strip() if result.returncode == 0 else None


def get_tags(repo_path: str) -> list[TagInfo]:
    """Get all tags with their SHAs."""
    result = subprocess.run(  # nosec B603 B607
        ["git", "tag", "--sort=-creatordate", "--format=%(refname:short)|%(objectname:short)"],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=10,
    )
    if result.returncode != 0 or not result.stdout.strip():
        return []

    tags = []
    for line in result.stdout.strip().split("\n"):
        if "|" in line:
            name, sha = line.split("|", 1)
            tags.append(TagInfo(name=name, sha=sha))
    return tags


def _parse_shortstat(stat_output: str) -> tuple[int, int, int]:
    """Parse `git diff --shortstat` output."""
    # Example: " 3 files changed, 45 insertions(+), 12 deletions(-)"
    files = insertions = deletions = 0
    text = stat_output.strip()
    if not text:
        return files, insertions, deletions

    import re

    m = re.search(r"(\d+) file", text)
    if m:
        files = int(m.group(1))
    m = re.search(r"(\d+) insertion", text)
    if m:
        insertions = int(m.group(1))
    m = re.search(r"(\d+) deletion", text)
    if m:
        deletions = int(m.group(1))
    return files, insertions, deletions
