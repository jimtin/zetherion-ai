"""Annotation watcher — scans for TODO/FIXME/IDEA/HACK comments."""

from __future__ import annotations

import hashlib
import re
import subprocess  # nosec B404
from dataclasses import dataclass
from pathlib import Path

# Patterns to search for
ANNOTATION_PATTERNS = re.compile(
    r"#\s*(TODO|FIXME|IDEA|HACK)\s*:?\s*(.+)",
    re.IGNORECASE,
)

# File extensions to scan
SCANNABLE_EXTENSIONS = {
    ".py",
    ".js",
    ".ts",
    ".tsx",
    ".jsx",
    ".rs",
    ".go",
    ".java",
    ".kt",
    ".rb",
    ".sh",
    ".yaml",
    ".yml",
    ".toml",
}

# Directories to skip
SKIP_DIRS = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    ".mypy_cache",
    ".pytest_cache",
    "dist",
    "build",
    ".eggs",
}


@dataclass
class Annotation:
    """A code annotation (TODO, FIXME, IDEA, HACK)."""

    annotation_type: str  # "TODO", "FIXME", "IDEA", "HACK"
    content: str
    file: str  # Relative path
    line: int


def annotation_content_hash(content: str) -> str:
    """Return a compact stable hash for annotation content."""
    return hashlib.sha1(content.strip().encode("utf-8")).hexdigest()[:16]


def annotation_state_key(annotation: Annotation) -> str:
    """Build the persisted state key for an annotation.

    Format: ``type:file:line:content_hash``.
    """
    return (
        f"{annotation.annotation_type}:{annotation.file}:{annotation.line}:"
        f"{annotation_content_hash(annotation.content)}"
    )


def parse_state_annotation(
    key: str,
    content: str,
) -> tuple[Annotation | None, bool]:
    """Parse an annotation from persisted state.

    Returns:
        Tuple of ``(annotation, is_legacy_key)`` where legacy keys are the
        historical ``type:file`` format without line/hash.
    """
    parts = key.split(":", 3)
    if len(parts) == 4:
        annotation_type, file_path, line_raw, _ = parts
        try:
            line_no = int(line_raw)
        except ValueError:
            line_no = 0
        return (
            Annotation(
                annotation_type=annotation_type,
                content=content,
                file=file_path,
                line=line_no,
            ),
            False,
        )

    legacy = key.split(":", 1)
    if len(legacy) == 2:
        annotation_type, file_path = legacy
        return (
            Annotation(
                annotation_type=annotation_type,
                content=content,
                file=file_path,
                line=0,
            ),
            True,
        )

    return None, True


def scan_annotations(repo_path: str) -> list[Annotation]:
    """Scan a repository for code annotations.

    Uses `git grep` for speed when available, falls back to file scanning.
    """
    annotations = _git_grep_scan(repo_path)
    if annotations is not None:
        return annotations
    return _file_scan(repo_path)


def _git_grep_scan(repo_path: str) -> list[Annotation] | None:
    """Use git grep to find annotations (faster than file scanning)."""
    result = subprocess.run(  # nosec B603 B607
        [
            "git",
            "grep",
            "-n",
            "-E",
            r"(TODO|FIXME|IDEA|HACK)\s*:?\s*\S",
            "--",
            "*.py",
            "*.js",
            "*.ts",
            "*.tsx",
            "*.rs",
            "*.go",
            "*.java",
            "*.rb",
            "*.sh",
            "*.yaml",
            "*.yml",
            "*.toml",
        ],
        cwd=repo_path,
        capture_output=True,
        text=True,
        timeout=30,
    )
    if result.returncode not in (0, 1):  # 1 = no matches
        return None  # git grep not available, fall back

    annotations = []
    for line in result.stdout.strip().split("\n"):
        if not line:
            continue
        # Format: file:line:content
        parts = line.split(":", 2)
        if len(parts) < 3:
            continue
        file_path, line_no, content = parts[0], parts[1], parts[2]
        match = ANNOTATION_PATTERNS.search(content)
        if match:
            annotations.append(
                Annotation(
                    annotation_type=match.group(1).upper(),
                    content=match.group(2).strip(),
                    file=file_path,
                    line=int(line_no),
                )
            )
    return annotations


def _file_scan(repo_path: str) -> list[Annotation]:
    """Fallback: scan files directly."""
    annotations = []
    root = Path(repo_path)

    for path in root.rglob("*"):
        if path.is_dir():
            continue
        if any(skip in path.parts for skip in SKIP_DIRS):
            continue
        if path.suffix not in SCANNABLE_EXTENSIONS:
            continue

        try:
            text = path.read_text(errors="ignore")
        except OSError:
            continue

        for line_no, line in enumerate(text.split("\n"), 1):
            match = ANNOTATION_PATTERNS.search(line)
            if match:
                rel_path = str(path.relative_to(root))
                annotations.append(
                    Annotation(
                        annotation_type=match.group(1).upper(),
                        content=match.group(2).strip(),
                        file=rel_path,
                        line=line_no,
                    )
                )
    return annotations


def diff_annotations(
    old: list[Annotation], new: list[Annotation]
) -> tuple[list[Annotation], list[Annotation]]:
    """Find added and removed annotations between two scans.

    Returns (added, removed).
    """

    def _key(a: Annotation) -> str:
        return annotation_state_key(a)

    old_set = {_key(a) for a in old}
    new_set = {_key(a) for a in new}

    added = [a for a in new if _key(a) not in old_set]
    removed = [a for a in old if _key(a) not in new_set]

    return added, removed
