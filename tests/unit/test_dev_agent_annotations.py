"""Tests for dev-agent annotation keying and diff behavior."""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
DEV_AGENT_SRC = PROJECT_ROOT / "zetherion-dev-agent" / "src"
if str(DEV_AGENT_SRC) not in sys.path:
    sys.path.insert(0, str(DEV_AGENT_SRC))

from zetherion_dev_agent.watchers.annotations import (  # noqa: E402
    Annotation,
    annotation_state_key,
    diff_annotations,
    parse_state_annotation,
)


def test_annotation_state_key_includes_line_and_content_hash() -> None:
    ann1 = Annotation(annotation_type="TODO", content="Refactor auth", file="src/auth.py", line=10)
    ann2 = Annotation(annotation_type="TODO", content="Refactor auth", file="src/auth.py", line=11)

    key1 = annotation_state_key(ann1)
    key2 = annotation_state_key(ann2)

    assert key1 != key2
    assert key1.startswith("TODO:src/auth.py:10:")
    assert key2.startswith("TODO:src/auth.py:11:")


def test_diff_annotations_tracks_same_type_same_file_different_lines() -> None:
    old = [
        Annotation(annotation_type="TODO", content="A", file="src/a.py", line=10),
    ]
    new = [
        Annotation(annotation_type="TODO", content="A", file="src/a.py", line=10),
        Annotation(annotation_type="TODO", content="A", file="src/a.py", line=20),
    ]

    added, removed = diff_annotations(old, new)
    assert len(added) == 1
    assert added[0].line == 20
    assert removed == []


def test_parse_state_annotation_supports_legacy_and_new_key_formats() -> None:
    parsed_new, is_legacy_new = parse_state_annotation(
        "TODO:src/auth.py:42:deadbeefdeadbeef",
        "Refactor auth",
    )
    assert parsed_new is not None
    assert parsed_new.annotation_type == "TODO"
    assert parsed_new.file == "src/auth.py"
    assert parsed_new.line == 42
    assert is_legacy_new is False

    parsed_legacy, is_legacy_old = parse_state_annotation("TODO:src/auth.py", "Refactor auth")
    assert parsed_legacy is not None
    assert parsed_legacy.annotation_type == "TODO"
    assert parsed_legacy.file == "src/auth.py"
    assert parsed_legacy.line == 0
    assert is_legacy_old is True
