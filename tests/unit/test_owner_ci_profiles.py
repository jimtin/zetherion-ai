"""Tests for owner-CI profile path resolution helpers."""

from __future__ import annotations

from pathlib import Path

import zetherion_ai.owner_ci.profiles as profiles


def test_resolve_local_workspace_root_prefers_first_existing_candidate(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "primary"
    secondary = tmp_path / "secondary"
    primary.mkdir()
    secondary.mkdir()

    original_candidates = profiles._LOCAL_WORKSPACE_ROOT_CANDIDATES
    profiles._LOCAL_WORKSPACE_ROOT_CANDIDATES = (primary, secondary)
    try:
        assert profiles._resolve_local_workspace_root() == primary
    finally:
        profiles._LOCAL_WORKSPACE_ROOT_CANDIDATES = original_candidates


def test_resolve_local_workspace_root_falls_back_when_first_candidate_missing(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "missing-primary"
    secondary = tmp_path / "secondary"
    secondary.mkdir()

    original_candidates = profiles._LOCAL_WORKSPACE_ROOT_CANDIDATES
    profiles._LOCAL_WORKSPACE_ROOT_CANDIDATES = (primary, secondary)
    try:
        assert profiles._resolve_local_workspace_root() == secondary
    finally:
        profiles._LOCAL_WORKSPACE_ROOT_CANDIDATES = original_candidates


def test_resolve_local_workspace_root_uses_default_candidate_when_none_exist(
    tmp_path: Path,
) -> None:
    primary = tmp_path / "missing-primary"
    secondary = tmp_path / "missing-secondary"

    original_candidates = profiles._LOCAL_WORKSPACE_ROOT_CANDIDATES
    profiles._LOCAL_WORKSPACE_ROOT_CANDIDATES = (primary, secondary)
    try:
        assert profiles._resolve_local_workspace_root() == primary
    finally:
        profiles._LOCAL_WORKSPACE_ROOT_CANDIDATES = original_candidates
