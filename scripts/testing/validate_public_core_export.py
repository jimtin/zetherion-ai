#!/usr/bin/env python3
"""Validate the curated public-core export boundary."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from zetherion_ai.owner_ci.public_core import (
    load_public_core_export_manifest,
    validate_public_core_export_tree,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="Source repo root to validate")
    parser.add_argument(
        "--manifest",
        default=".ci/public-core-export-manifest.json",
        help="Path to the public-core export manifest",
    )
    parser.add_argument(
        "--output",
        default="",
        help="Optional JSON output path for the validation receipt",
    )
    args = parser.parse_args()

    root = Path(args.root).resolve()
    manifest_path = Path(args.manifest).resolve()
    manifest = load_public_core_export_manifest(manifest_path)
    result = validate_public_core_export_tree(root=root, manifest=manifest)
    payload = json.dumps(result, indent=2, sort_keys=True)
    if args.output:
        output_path = Path(args.output).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(payload + "\n", encoding="utf-8")
    else:
        sys.stdout.write(payload + "\n")
    return 0 if result["summary"]["valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
