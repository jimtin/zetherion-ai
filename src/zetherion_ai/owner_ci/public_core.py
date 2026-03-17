"""Public-core export validation helpers."""

from __future__ import annotations

import json
import shutil
from fnmatch import fnmatchcase
from pathlib import Path
from typing import Any

from zetherion_ai.owner_ci.models import PublicCoreExportManifest


def load_public_core_export_manifest(path: Path) -> PublicCoreExportManifest:
    """Load and validate a public-core export manifest."""

    raw = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        raise ValueError("Public core export manifest must be a JSON object")
    return PublicCoreExportManifest.model_validate(raw)


def _matches_any(relative_path: str, patterns: list[str]) -> bool:
    candidate = relative_path.strip().lstrip("./")
    return any(fnmatchcase(candidate, pattern) for pattern in patterns if pattern)


def _boundary_labels(relative_path: str, manifest: PublicCoreExportManifest) -> list[str]:
    labels: list[str] = []
    for boundary in manifest.boundaries:
        if boundary.include_globs and not _matches_any(relative_path, boundary.include_globs):
            continue
        if boundary.exclude_globs and _matches_any(relative_path, boundary.exclude_globs):
            continue
        labels.append(boundary.label)
    return labels


def _export_allowed(relative_path: str, manifest: PublicCoreExportManifest) -> bool:
    return _matches_any(relative_path, manifest.public_include_globs) or _matches_any(
        relative_path,
        manifest.allowed_doc_globs,
    )


def _forbidden_hits(path: Path, manifest: PublicCoreExportManifest) -> list[str]:
    text = path.read_text(encoding="utf-8", errors="ignore")
    return [term for term in manifest.forbidden_terms if term and term in text]


def build_public_core_export_tree(
    *,
    source_root: Path,
    output_root: Path,
    manifest: PublicCoreExportManifest,
) -> dict[str, Any]:
    """Stage a curated public-core export tree from a source workspace."""

    source_root = source_root.resolve()
    output_root = output_root.resolve()
    if not source_root.exists():
        raise ValueError(f"Source root does not exist: {source_root}")

    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    exported_files: list[dict[str, Any]] = []
    skipped_files: list[dict[str, Any]] = []
    blocked_files: list[dict[str, Any]] = []

    for path in sorted(candidate for candidate in source_root.rglob("*") if candidate.is_file()):
        relative_path = path.relative_to(source_root).as_posix()
        boundary_labels = _boundary_labels(relative_path, manifest)
        include_match = _export_allowed(relative_path, manifest)
        blocked_match = _matches_any(relative_path, manifest.private_block_globs)
        forbidden_hits = _forbidden_hits(path, manifest) if include_match else []

        if include_match and not blocked_match and not forbidden_hits:
            target_path = output_root / relative_path
            target_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, target_path)
            exported_files.append(
                {
                    "path": relative_path,
                    "boundaries": boundary_labels,
                }
            )
            continue

        if include_match:
            blocked_files.append(
                {
                    "path": relative_path,
                    "reason": (
                        "path_matches_private_block_glob"
                        if blocked_match
                        else "forbidden_term_detected"
                    ),
                    "boundaries": boundary_labels,
                    "forbidden_terms": forbidden_hits,
                }
            )
            continue

        skipped_files.append(
            {
                "path": relative_path,
                "reason": "not_exported_by_manifest",
                "boundaries": boundary_labels,
            }
        )

    return {
        "manifest": manifest.model_dump(mode="json"),
        "source_root": str(source_root),
        "output_root": str(output_root),
        "summary": {
            "exported_file_count": len(exported_files),
            "skipped_file_count": len(skipped_files),
            "blocked_file_count": len(blocked_files),
        },
        "exported_files": exported_files,
        "skipped_files": skipped_files,
        "blocked_files": blocked_files,
    }


def validate_public_core_export_tree(
    *,
    root: Path,
    manifest: PublicCoreExportManifest,
) -> dict[str, Any]:
    """Validate a source tree against the curated public-core export manifest."""

    root = root.resolve()
    if not root.exists():
        raise ValueError(f"Source root does not exist: {root}")

    export_candidates: list[dict[str, Any]] = []
    blocked_candidates: list[dict[str, Any]] = []
    violations: list[dict[str, Any]] = []

    for path in sorted(candidate for candidate in root.rglob("*") if candidate.is_file()):
        relative_path = path.relative_to(root).as_posix()
        include_match = _matches_any(relative_path, manifest.public_include_globs) or _matches_any(
            relative_path,
            manifest.allowed_doc_globs,
        )
        blocked_match = _matches_any(relative_path, manifest.private_block_globs)
        boundary_labels = _boundary_labels(relative_path, manifest)

        if include_match:
            candidate = {
                "path": relative_path,
                "boundaries": boundary_labels,
            }
            if blocked_match:
                violations.append(
                    {
                        "path": relative_path,
                        "reason": "path_matches_private_block_glob",
                        "boundaries": boundary_labels,
                    }
                )
            else:
                forbidden_hits = _forbidden_hits(path, manifest)
                if forbidden_hits:
                    violations.append(
                        {
                            "path": relative_path,
                            "reason": "forbidden_term_detected",
                            "boundaries": boundary_labels,
                            "forbidden_terms": forbidden_hits,
                        }
                    )
                export_candidates.append(candidate)
            continue

        blocked_candidates.append(
            {
                "path": relative_path,
                "blocked": blocked_match or manifest.default_action == "deny",
                "boundaries": boundary_labels,
            }
        )

    return {
        "manifest": manifest.model_dump(mode="json"),
        "summary": {
            "export_candidate_count": len(export_candidates),
            "blocked_candidate_count": len(blocked_candidates),
            "violation_count": len(violations),
            "valid": len(violations) == 0,
        },
        "export_candidates": export_candidates,
        "blocked_candidates": blocked_candidates,
        "violations": violations,
    }
