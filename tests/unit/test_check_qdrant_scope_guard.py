"""Unit tests for Qdrant scope guard script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    module_path = Path(__file__).resolve().parents[2] / "scripts" / "check-qdrant-scope-guard.py"
    spec = importlib.util.spec_from_file_location("check_qdrant_scope_guard_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_collect_violations_allows_qdrant_module(tmp_path):
    module = _load_module()

    allowed_file = tmp_path / "src" / "zetherion_ai" / "memory" / "qdrant.py"
    allowed_file.parent.mkdir(parents=True, exist_ok=True)
    allowed_file.write_text(
        (
            "async def keep_legacy(memory):\n"
            "    return await memory.search_collection('x', query='y')\n"
        ),
        encoding="utf-8",
    )

    module.TARGET_PATHS = (Path("src/zetherion_ai"),)
    module.ALLOWED_FILES = {Path("src/zetherion_ai/memory/qdrant.py")}

    violations = module.collect_violations(tmp_path)
    assert violations == []


def test_collect_violations_flags_unscoped_helper_usage(tmp_path):
    module = _load_module()

    blocked_file = tmp_path / "src" / "zetherion_ai" / "skills" / "calendar.py"
    blocked_file.parent.mkdir(parents=True, exist_ok=True)
    blocked_file.write_text(
        (
            "async def bad(memory):\n"
            "    return await memory.filter_by_field("
            "collection_name='x', field='y', value='z')\n"
        ),
        encoding="utf-8",
    )

    module.TARGET_PATHS = (Path("src/zetherion_ai"),)
    module.ALLOWED_FILES = {Path("src/zetherion_ai/memory/qdrant.py")}

    violations = module.collect_violations(tmp_path)
    assert len(violations) == 1
    assert "src/zetherion_ai/skills/calendar.py:2" in violations[0]
    assert "filter_by_field" in violations[0]
