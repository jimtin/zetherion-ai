"""Unit tests for requirements.txt / pyproject.toml dependency synchronization.

Ensures that every package listed in requirements.txt has a corresponding
entry in pyproject.toml [project].dependencies.
"""

import re
from pathlib import Path

# Resolve paths relative to the repository root
_REPO_ROOT = Path(__file__).resolve().parents[2]
_REQUIREMENTS_TXT = _REPO_ROOT / "requirements.txt"
_PYPROJECT_TOML = _REPO_ROOT / "pyproject.toml"


def _parse_requirements(path: Path, *, skip_platform_markers: bool = False) -> set[str]:
    """Parse package names from a requirements.txt file.

    Ignores comments, blank lines, version specifiers, and platform markers.
    Normalizes names to lowercase with hyphens replaced by dashes for comparison.

    Args:
        path: Path to the requirements.txt file.
        skip_platform_markers: If True, skip entries that have platform
            markers (e.g. ;python_version>='3.13') since these are often
            polyfills not required in pyproject.toml.
    """
    names: set[str] = set()
    for line in path.read_text().splitlines():
        line = line.strip()
        # Skip empty lines and comments
        if not line or line.startswith("#"):
            continue
        # Remove inline comments
        if " #" in line:
            line = line[: line.index(" #")]
        # Skip platform-specific entries if requested
        has_marker = ";" in line
        if skip_platform_markers and has_marker:
            continue
        # Remove platform markers (e.g. ;python_version>='3.13')
        if has_marker:
            line = line[: line.index(";")]
        # Remove version specifiers
        name = re.split(r"[><=!~]", line.strip())[0].strip()
        if name:
            names.add(_normalize(name))
    return names


def _parse_pyproject_dependencies(path: Path) -> set[str]:
    """Parse dependency names from pyproject.toml [project].dependencies.

    Uses simple text parsing to extract package names from the dependencies array.
    """
    names: set[str] = set()
    in_deps = False
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if stripped == "dependencies = [":
            in_deps = True
            continue
        if in_deps:
            if stripped == "]":
                break
            # Extract the quoted string value
            match = re.match(r'"([^"]+)"', stripped)
            if match:
                dep = match.group(1)
                # Remove version specifiers
                name = re.split(r"[><=!~\[]", dep)[0].strip()
                if name:
                    names.add(_normalize(name))
    return names


def _normalize(name: str) -> str:
    """Normalize a Python package name for comparison.

    PEP 503: all comparisons should be case-insensitive with hyphens,
    underscores, and periods treated as equivalent.
    """
    return re.sub(r"[-_.]+", "-", name).lower()


class TestPyprojectSync:
    """Tests for dependency synchronization between requirements.txt and pyproject.toml."""

    def test_requirements_txt_exists(self) -> None:
        """requirements.txt should exist at the repository root."""
        assert _REQUIREMENTS_TXT.exists(), f"Missing {_REQUIREMENTS_TXT}"

    def test_pyproject_toml_exists(self) -> None:
        """pyproject.toml should exist at the repository root."""
        assert _PYPROJECT_TOML.exists(), f"Missing {_PYPROJECT_TOML}"

    def test_all_requirements_in_pyproject(self) -> None:
        """Every non-platform-specific package in requirements.txt should appear in pyproject.toml.

        Platform-marker-only dependencies (e.g. audioop-lts;python_version>='3.13')
        are excluded since they are polyfills that pyproject.toml may omit.
        """
        req_names = _parse_requirements(_REQUIREMENTS_TXT, skip_platform_markers=True)
        pyproject_names = _parse_pyproject_dependencies(_PYPROJECT_TOML)

        missing = req_names - pyproject_names
        assert (
            not missing
        ), f"Packages in requirements.txt but missing from pyproject.toml: {sorted(missing)}"

    def test_requirements_is_non_empty(self) -> None:
        """requirements.txt should have at least one dependency."""
        req_names = _parse_requirements(_REQUIREMENTS_TXT)
        assert len(req_names) > 0, "requirements.txt appears to have no dependencies"

    def test_pyproject_dependencies_non_empty(self) -> None:
        """pyproject.toml should have at least one dependency."""
        pyproject_names = _parse_pyproject_dependencies(_PYPROJECT_TOML)
        assert len(pyproject_names) > 0, "pyproject.toml appears to have no dependencies"
