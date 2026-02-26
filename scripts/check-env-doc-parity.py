#!/usr/bin/env python3
"""Validate configuration environment variable coverage in docs."""

from __future__ import annotations

import ast
import re
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
CONFIG_PATH = REPO_ROOT / "src/zetherion_ai/config.py"
ENV_EXAMPLE_PATH = REPO_ROOT / ".env.example"
DOC_CONFIG_PATH = REPO_ROOT / "docs/technical/configuration.md"

DOC_VAR_RE = re.compile(r"\|\s*`([A-Z0-9_]+)`\s*\|")
ENV_VAR_RE = re.compile(r"^([A-Z][A-Z0-9_]+)=")
EXCLUSION_SECTION_HEADING = "## Intentionally Undocumented / Internal Exclusions"
EXCLUSION_VAR_RE = re.compile(r"-\s+`([A-Z0-9_]+)`")

# High-signal runtime surfaces that must remain explicitly documented.
REQUIRED_DOC_VARS = {
    "GROQ_API_KEY",
    "GROQ_MODEL",
    "GROQ_BASE_URL",
    "WORK_ROUTER_ENABLED",
    "PROVIDER_OUTLOOK_ENABLED",
    "EMAIL_SECURITY_GATE_ENABLED",
    "LOCAL_EXTRACTION_REQUIRED",
    "DOCS_KNOWLEDGE_ENABLED",
    "DOCS_KNOWLEDGE_ROOT",
    "DOCS_KNOWLEDGE_STATE_PATH",
    "DOCS_KNOWLEDGE_GAP_LOG_PATH",
    "API_HOST",
    "API_PORT",
    "API_JWT_SECRET",
    "AUTO_UPDATE_ENABLED",
    "AUTO_UPDATE_REPO",
    "AUTO_UPDATE_CHECK_INTERVAL_MINUTES",
    "UPDATE_REQUIRE_APPROVAL",
    "AUTO_UPDATE_PAUSE_ON_FAILURE",
    "UPDATER_SERVICE_URL",
    "UPDATER_SECRET",
    "GITHUB_TOKEN",
    "GITHUB_DEFAULT_REPO",
    "GITHUB_API_TIMEOUT",
}


def _is_field_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    if isinstance(node.func, ast.Name) and node.func.id == "Field":
        return True
    if isinstance(node.func, ast.Attribute) and node.func.attr == "Field":
        return True
    return False


def _field_alias(node: ast.AST) -> str | None:
    if not _is_field_call(node):
        return None
    call = node  # type: ignore[assignment]
    for kw in call.keywords:  # type: ignore[attr-defined]
        if kw.arg == "alias" and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str):
            return kw.value.value
    return None


def extract_config_env_vars(path: Path) -> set[str]:
    module = ast.parse(path.read_text(encoding="utf-8"))

    settings_cls: ast.ClassDef | None = None
    for node in module.body:
        if isinstance(node, ast.ClassDef) and node.name == "Settings":
            settings_cls = node
            break

    if settings_cls is None:
        raise RuntimeError("Settings class not found in config.py")

    env_vars: set[str] = set()
    for node in settings_cls.body:
        if isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            env_name = _field_alias(node.value) or node.target.id.upper()
            env_vars.add(env_name)

    return env_vars


def extract_env_example_vars(path: Path) -> set[str]:
    values: set[str] = set()
    for line in path.read_text(encoding="utf-8").splitlines():
        match = ENV_VAR_RE.match(line)
        if match:
            values.add(match.group(1))
    return values


def extract_doc_vars(path: Path) -> tuple[set[str], set[str]]:
    text = path.read_text(encoding="utf-8")
    doc_vars = set(DOC_VAR_RE.findall(text))

    exclusions: set[str] = set()
    if EXCLUSION_SECTION_HEADING in text:
        _, section = text.split(EXCLUSION_SECTION_HEADING, 1)
        exclusions = set(EXCLUSION_VAR_RE.findall(section))

    return doc_vars, exclusions


def main() -> int:
    config_vars = extract_config_env_vars(CONFIG_PATH)
    env_vars = extract_env_example_vars(ENV_EXAMPLE_PATH)
    doc_vars, exclusions = extract_doc_vars(DOC_CONFIG_PATH)

    documented_unknown = sorted(doc_vars - (config_vars | env_vars))
    undocumented_config = sorted(config_vars - doc_vars - exclusions)
    missing_required = sorted(REQUIRED_DOC_VARS - doc_vars)

    if documented_unknown or undocumented_config or missing_required:
        print("Environment documentation parity check failed.")

        if documented_unknown:
            print("\nDocumented variables missing from config.py and .env.example:")
            for var in documented_unknown:
                print(f"  - {var}")

        if undocumented_config:
            print("\nConfig variables missing from docs (and not listed in exclusions):")
            for var in undocumented_config:
                print(f"  - {var}")

        if missing_required:
            print("\nRequired runtime variables missing from configuration docs:")
            for var in missing_required:
                print(f"  - {var}")

        return 1

    print("Environment documentation parity check passed.")
    print(
        ""
        f"Documented vars: {len(doc_vars)} | "
        f"Config vars: {len(config_vars)} | "
        f"Env example vars: {len(env_vars)}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
