"""Unit tests for the preservation inventory generator."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2]
        / "scripts"
        / "cleanup"
        / "generate_preservation_inventory.py"
    )
    spec = importlib.util.spec_from_file_location("preservation_inventory_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_discover_candidate_paths_finds_known_personalbot_roots(tmp_path: Path) -> None:
    module = _load_module()
    repo_root = tmp_path / "Developer" / "PersonalBot"
    repo_root.mkdir(parents=True)
    (tmp_path / "Developer" / "PersonalBot-discord-fix").mkdir(parents=True)
    (tmp_path / "Documents" / "Developer.nosync" / "PersonalBot-archive").mkdir(parents=True)
    (tmp_path / "Documents" / "Random" / "PersonalBot-ignored").mkdir(parents=True)

    discovered = module.discover_candidate_paths(
        search_roots=[
            tmp_path / "Developer",
            tmp_path / "Development",
            tmp_path / "Documents",
        ],
        repo_root=repo_root,
    )

    assert discovered == sorted(
        [
            (tmp_path / "Developer" / "PersonalBot").resolve(),
            (tmp_path / "Developer" / "PersonalBot-discord-fix").resolve(),
            (tmp_path / "Documents" / "Developer.nosync" / "PersonalBot-archive").resolve(),
        ]
    )


def test_parse_worktree_paths_reads_porcelain_listing() -> None:
    module = _load_module()
    output = "\n".join(
        [
            "worktree /tmp/PersonalBot",
            "HEAD abcdef",
            "branch refs/heads/main",
            "",
            "worktree /tmp/PersonalBot-discord-fix",
            "HEAD 123456",
            "branch refs/heads/codex/seg-test",
            "",
        ]
    )

    parsed = module.parse_worktree_paths(output)

    assert parsed == {
        Path("/tmp/PersonalBot").resolve(),
        Path("/tmp/PersonalBot-discord-fix").resolve(),
    }


def test_classify_candidate_covers_expected_cleanup_categories(tmp_path: Path) -> None:
    module = _load_module()
    repo_root = (tmp_path / "Developer" / "PersonalBot").resolve()
    archive_root = (tmp_path / "Developer" / "_archive").resolve()

    active = module.classify_candidate(
        path=repo_root,
        repo_root=repo_root,
        archive_root=archive_root,
        git_readable=True,
        is_bare=False,
        is_known_worktree=True,
    )
    bare = module.classify_candidate(
        path=(tmp_path / "PersonalBot.git").resolve(),
        repo_root=repo_root,
        archive_root=archive_root,
        git_readable=True,
        is_bare=True,
        is_known_worktree=False,
    )
    corrupt = module.classify_candidate(
        path=(tmp_path / "PersonalBot-corrupt").resolve(),
        repo_root=repo_root,
        archive_root=archive_root,
        git_readable=False,
        is_bare=False,
        is_known_worktree=False,
    )
    archived = module.classify_candidate(
        path=(archive_root / "PersonalBot-old").resolve(),
        repo_root=repo_root,
        archive_root=archive_root,
        git_readable=True,
        is_bare=False,
        is_known_worktree=False,
    )
    backup = module.classify_candidate(
        path=(tmp_path / "Developer" / "PersonalBot-discord-fix").resolve(),
        repo_root=repo_root,
        archive_root=archive_root,
        git_readable=True,
        is_bare=False,
        is_known_worktree=True,
    )

    assert (active.category, active.detail) == ("active_clone", "canonical_repo_root")
    assert (bare.category, bare.detail) == ("bare_mirror", "bare_repository")
    assert (corrupt.category, corrupt.detail) == ("corrupt_backup", "git_metadata_unreadable")
    assert (archived.category, archived.detail) == ("archive", "under_archive_root")
    assert (backup.category, backup.detail) == ("backup_clone", "linked_git_worktree")
