"""Unit tests for PR metadata policy validation."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]


def _load_module():
    module_path = REPO_ROOT / "scripts" / "check_pr_metadata.py"
    spec = importlib.util.spec_from_file_location("check_pr_metadata_module", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _event(
    *,
    body: str,
    head_ref: str = "codex/example",
    sender: str = "codex-agent",
) -> dict[str, Any]:
    return {
        "sender": {"login": sender},
        "pull_request": {
            "body": body,
            "head": {"ref": head_ref},
        },
    }


VALID_BODY = """## Summary
A real summary.

## Capability IDs
- `ci.contract.alignment`

## Workflow Scenario IDs
- `pr.receipt_sha_mismatch_rejected`

## Validation
- `python3 scripts/check_pipeline_contract.py`

## Receipt / Verification
- [x] Included deterministic local evidence for this segment.
- [x] Updated and committed `.ci/e2e-receipt.json`, or this PR is not `e2e_required`.
- [x] Same-PR regression coverage is included, or this PR does not change CI, deploy, or gating\
  logic.
- [x] Windows post-merge verification is included, or this PR does not change Windows\
  deploy behavior.
"""


def test_non_pr_event_is_ignored() -> None:
    module = _load_module()

    assert module.validate_pr_metadata({"ref": "refs/heads/main"}) == []


def test_valid_pr_metadata_passes() -> None:
    module = _load_module()

    assert module.validate_pr_metadata(_event(body=VALID_BODY)) == []


def test_non_codex_branch_is_rejected() -> None:
    module = _load_module()

    errors = module.validate_pr_metadata(_event(body=VALID_BODY, head_ref="feature/test"))

    assert any("must start with 'codex/'" in error for error in errors)


def test_placeholders_are_rejected() -> None:
    module = _load_module()
    body = VALID_BODY.replace("`ci.contract.alignment`", "`...`")

    errors = module.validate_pr_metadata(_event(body=body))

    assert any("capability IDs" in error for error in errors)


def test_unchecked_receipt_boxes_are_rejected() -> None:
    module = _load_module()
    before = "- [x] Included deterministic local evidence for this segment."
    after = "- [ ] Included deterministic local evidence for this segment."
    body = VALID_BODY.replace(before, after)

    errors = module.validate_pr_metadata(_event(body=body))

    assert any("must be explicitly checked" in error for error in errors)


def test_dependabot_is_exempt() -> None:
    module = _load_module()

    errors = module.validate_pr_metadata(
        _event(body="", head_ref="dependabot/pip/ruff-1.0.0", sender="dependabot[bot]")
    )

    assert errors == []
