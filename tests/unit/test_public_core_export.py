"""Tests for curated public-core export validation."""

from __future__ import annotations

import json
from pathlib import Path

from zetherion_ai.owner_ci.models import PublicCoreExportManifest
from zetherion_ai.owner_ci.public_core import (
    build_public_core_export_tree,
    load_public_core_export_manifest,
    validate_public_core_export_tree,
)


def _manifest() -> PublicCoreExportManifest:
    return PublicCoreExportManifest.model_validate(
        {
            "source_repo": "private-primary",
            "target_repo": "public-core",
            "default_action": "deny",
            "public_include_globs": ["src/public/**", "README.md"],
            "private_block_globs": ["src/private/**"],
            "allowed_doc_globs": ["docs/public/**"],
            "forbidden_terms": ["enterprise-only"],
            "boundaries": [
                {
                    "boundary_id": "public",
                    "label": "Public",
                    "include_globs": ["src/public/**", "docs/public/**", "README.md"],
                }
            ],
        }
    )


def test_load_public_core_export_manifest_validates_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(
        json.dumps(
            {
                "source_repo": "private-primary",
                "target_repo": "public-core",
            }
        ),
        encoding="utf-8",
    )

    manifest = load_public_core_export_manifest(manifest_path)

    assert manifest.source_repo == "private-primary"
    assert manifest.target_repo == "public-core"
    assert manifest.default_action == "deny"


def test_load_public_core_export_manifest_rejects_non_object_json(tmp_path: Path) -> None:
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(["not", "an", "object"]), encoding="utf-8")

    try:
        load_public_core_export_manifest(manifest_path)
    except ValueError as exc:
        assert "JSON object" in str(exc)
    else:
        raise AssertionError("Expected load_public_core_export_manifest() to reject lists")


def test_validate_public_core_export_tree_flags_private_and_forbidden_content(
    tmp_path: Path,
) -> None:
    (tmp_path / "README.md").write_text("safe\n", encoding="utf-8")
    (tmp_path / "src" / "public").mkdir(parents=True)
    (tmp_path / "src" / "private").mkdir(parents=True)
    (tmp_path / "docs" / "public").mkdir(parents=True)
    (tmp_path / "src" / "public" / "module.py").write_text(
        "print('enterprise-only')\n",
        encoding="utf-8",
    )
    (tmp_path / "src" / "private" / "secret.py").write_text("secret\n", encoding="utf-8")
    (tmp_path / "docs" / "public" / "guide.md").write_text("guide\n", encoding="utf-8")

    result = validate_public_core_export_tree(root=tmp_path, manifest=_manifest())

    assert result["summary"]["export_candidate_count"] == 3
    assert result["summary"]["violation_count"] == 1
    assert result["violations"][0]["path"] == "src/public/module.py"
    assert result["violations"][0]["reason"] == "forbidden_term_detected"
    assert any(item["path"] == "src/private/secret.py" for item in result["blocked_candidates"])
    assert result["summary"]["valid"] is False


def test_validate_public_core_export_tree_allows_curated_public_files(tmp_path: Path) -> None:
    (tmp_path / "README.md").write_text("safe\n", encoding="utf-8")
    (tmp_path / "src" / "public").mkdir(parents=True)
    (tmp_path / "src" / "public" / "module.py").write_text("print('ok')\n", encoding="utf-8")

    result = validate_public_core_export_tree(root=tmp_path, manifest=_manifest())

    assert result["summary"]["valid"] is True
    assert result["summary"]["violation_count"] == 0
    assert sorted(item["path"] for item in result["export_candidates"]) == [
        "README.md",
        "src/public/module.py",
    ]


def test_build_public_core_export_tree_stages_only_allowed_files(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    output_root = tmp_path / "out"
    (source_root / "README.md").parent.mkdir(parents=True, exist_ok=True)
    (source_root / "README.md").write_text("safe\n", encoding="utf-8")
    (source_root / "src" / "public").mkdir(parents=True)
    (source_root / "src" / "public" / "module.py").write_text("print('ok')\n", encoding="utf-8")
    (source_root / "src" / "private").mkdir(parents=True)
    (source_root / "src" / "private" / "secret.py").write_text("secret\n", encoding="utf-8")

    result = build_public_core_export_tree(
        source_root=source_root,
        output_root=output_root,
        manifest=_manifest(),
    )

    assert result["summary"]["exported_file_count"] == 2
    assert result["summary"]["blocked_file_count"] == 0
    assert sorted(item["path"] for item in result["exported_files"]) == [
        "README.md",
        "src/public/module.py",
    ]
    assert (output_root / "README.md").exists()
    assert (output_root / "src" / "public" / "module.py").exists()
    assert not (output_root / "src" / "private" / "secret.py").exists()


def test_build_and_validate_public_core_export_raise_for_missing_roots(tmp_path: Path) -> None:
    missing_root = tmp_path / "missing"

    try:
        validate_public_core_export_tree(root=missing_root, manifest=_manifest())
    except ValueError as exc:
        assert "Source root does not exist" in str(exc)
    else:
        raise AssertionError("Expected validate_public_core_export_tree() to reject missing roots")

    try:
        build_public_core_export_tree(
            source_root=missing_root,
            output_root=tmp_path / "out",
            manifest=_manifest(),
        )
    except ValueError as exc:
        assert "Source root does not exist" in str(exc)
    else:
        raise AssertionError("Expected build_public_core_export_tree() to reject missing roots")


def test_build_public_core_export_tree_blocks_forbidden_public_content(tmp_path: Path) -> None:
    source_root = tmp_path / "source"
    output_root = tmp_path / "out"
    (source_root / "src" / "public").mkdir(parents=True)
    (source_root / "src" / "public" / "module.py").write_text(
        "print('enterprise-only')\n",
        encoding="utf-8",
    )

    result = build_public_core_export_tree(
        source_root=source_root,
        output_root=output_root,
        manifest=_manifest(),
    )

    assert result["summary"]["blocked_file_count"] == 1
    assert result["blocked_files"][0]["path"] == "src/public/module.py"
    assert result["blocked_files"][0]["reason"] == "forbidden_term_detected"
    assert not (output_root / "src" / "public" / "module.py").exists()


def test_build_public_core_export_tree_blocks_private_globs_even_when_publicly_included(
    tmp_path: Path,
) -> None:
    manifest = PublicCoreExportManifest.model_validate(
        {
            **_manifest().model_dump(mode="json"),
            "public_include_globs": ["src/**"],
        }
    )
    source_root = tmp_path / "source"
    output_root = tmp_path / "out"
    (source_root / "src" / "private").mkdir(parents=True)
    (source_root / "src" / "private" / "secret.py").write_text("safe\n", encoding="utf-8")

    result = build_public_core_export_tree(
        source_root=source_root,
        output_root=output_root,
        manifest=manifest,
    )

    assert result["summary"]["blocked_file_count"] == 1
    assert result["blocked_files"][0]["path"] == "src/private/secret.py"
    assert result["blocked_files"][0]["reason"] == "path_matches_private_block_glob"


def test_public_core_boundary_exclusions_and_existing_output_root_are_handled(
    tmp_path: Path,
) -> None:
    manifest = PublicCoreExportManifest.model_validate(
        {
            **_manifest().model_dump(mode="json"),
            "public_include_globs": ["src/**"],
            "boundaries": [
                {
                    "boundary_id": "public",
                    "label": "Public",
                    "include_globs": ["src/**"],
                    "exclude_globs": ["src/private/**"],
                }
            ],
        }
    )
    source_root = tmp_path / "source"
    output_root = tmp_path / "out"
    output_root.mkdir(parents=True)
    (output_root / "stale.txt").write_text("stale\n", encoding="utf-8")
    (source_root / "src" / "private").mkdir(parents=True)
    (source_root / "src" / "private" / "secret.py").write_text("safe\n", encoding="utf-8")

    validation = validate_public_core_export_tree(root=source_root, manifest=manifest)
    result = build_public_core_export_tree(
        source_root=source_root,
        output_root=output_root,
        manifest=manifest,
    )

    assert validation["violations"][0]["boundaries"] == []
    assert not (output_root / "stale.txt").exists()
    assert result["blocked_files"][0]["boundaries"] == []
