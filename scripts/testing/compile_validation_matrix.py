#!/usr/bin/env python3
"""Compile a machine-readable local validation matrix across Zetherion and CGS."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from zetherion_ai.owner_ci.system_validation import (
    DEFAULT_CGS_MANIFEST_PATH,
    DEFAULT_COMBINED_MANIFEST_PATH,
    build_validation_matrix,
)


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cgs-manifest", default=str(DEFAULT_CGS_MANIFEST_PATH))
    parser.add_argument("--combined-manifest", default=str(DEFAULT_COMBINED_MANIFEST_PATH))
    parser.add_argument("--output", default="")
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    payload = build_validation_matrix(
        cgs_manifest_path=Path(args.cgs_manifest),
        combined_manifest_path=Path(args.combined_manifest),
    )
    rendered = f"{json.dumps(payload, indent=2)}\n"
    if args.output:
        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
