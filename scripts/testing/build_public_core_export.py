#!/usr/bin/env python3
"""Stage a curated public-core export tree from the current workspace."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zetherion_ai.owner_ci.public_core import (
    build_public_core_export_tree,
    load_public_core_export_manifest,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-root",
        default=".",
        help="Source workspace root to stage from",
    )
    parser.add_argument(
        "--manifest",
        default=".ci/public-core-export-manifest.json",
        help="Path to the public-core export manifest",
    )
    parser.add_argument(
        "--output-root",
        default=".artifacts/public-core-export/staged-tree",
        help="Where to write the staged public-core export tree",
    )
    parser.add_argument(
        "--report",
        default="",
        help="Optional JSON report output path",
    )
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    source_root = Path(args.source_root)
    manifest = load_public_core_export_manifest(Path(args.manifest))
    result = build_public_core_export_tree(
        source_root=source_root,
        output_root=Path(args.output_root),
        manifest=manifest,
    )
    rendered = f"{json.dumps(result, indent=2)}\n"
    if args.report:
        report_path = Path(args.report)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        report_path.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
