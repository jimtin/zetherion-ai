"""Export sanitized Windows live environment manifests for CGS + Zetherion cutover.

This script reads env files, records names/presence/classification only, and
never writes raw secret values to disk. It is intended for Windows live-cutover
preflight and can also be run locally against copied env files.
"""

from __future__ import annotations

import argparse
import json
import re
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

SystemId = Literal["cgs", "zetherion", "shared"]
RequirementLevel = Literal["blocking", "warning"]
LocalExpectation = Literal["required", "optional", "windows_only"]
EnvStatus = Literal["present", "missing", "placeholder"]


@dataclass(frozen=True)
class EnvRequirement:
    name: str
    system: SystemId
    group: str
    windows_requirement: RequirementLevel
    local_debug: LocalExpectation
    source: Literal["cgs", "zetherion", "either"]
    aliases: tuple[str, ...] = ()
    note: str = ""


@dataclass(frozen=True)
class EnvPatternRequirement:
    pattern: str
    system: SystemId
    group: str
    windows_requirement: RequirementLevel
    local_debug: LocalExpectation
    source: Literal["cgs", "zetherion", "either"]
    note: str = ""


@dataclass(frozen=True)
class SharedKeyExpectation:
    name: str
    required_in: tuple[Literal["cgs", "zetherion"], ...]
    windows_requirement: RequirementLevel
    local_debug: LocalExpectation
    aliases: tuple[str, ...] = ()


@dataclass
class ManifestEntry:
    name: str
    matched_key: str | None
    status: EnvStatus
    present: bool
    system: SystemId
    group: str
    windows_requirement: RequirementLevel
    local_debug: LocalExpectation
    source: str
    aliases: list[str] = field(default_factory=list)
    note: str = ""


PLACEHOLDER_PATTERNS = (
    re.compile(r"^\s*$"),
    re.compile(r"^<[^>]+>$"),
    re.compile(r"replace[-_ ]?(me|with)", re.IGNORECASE),
    re.compile(r"placeholder", re.IGNORECASE),
    re.compile(r"change[-_ ]?me", re.IGNORECASE),
    re.compile(r"^your[-_ ]", re.IGNORECASE),
    re.compile(r"_here$", re.IGNORECASE),
    re.compile(r"example\.com", re.IGNORECASE),
    re.compile(r"^\s*(example|dummy|todo)\s*$", re.IGNORECASE),
)


CGS_REQUIREMENTS: tuple[EnvRequirement, ...] = (
    EnvRequirement("DATABASE_URL", "cgs", "core", "blocking", "required", "cgs"),
    EnvRequirement("CGS_AI_TOKEN_SIGNING_SECRET", "cgs", "core", "blocking", "required", "cgs"),
    EnvRequirement("ENCRYPTION_PASSPHRASE", "shared", "core", "blocking", "required", "either"),
    EnvRequirement("CRON_SECRET", "cgs", "core", "blocking", "optional", "cgs"),
    EnvRequirement(
        "CGS_PUBLIC_BASE_URL",
        "cgs",
        "core",
        "blocking",
        "required",
        "cgs",
        aliases=("NEXT_PUBLIC_BASE_URL",),
        note="Either CGS_PUBLIC_BASE_URL or NEXT_PUBLIC_BASE_URL must be present.",
    ),
    EnvRequirement(
        "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY",
        "cgs",
        "auth",
        "blocking",
        "required",
        "cgs",
    ),
    EnvRequirement("CLERK_SECRET_KEY", "cgs", "auth", "blocking", "required", "cgs"),
    EnvRequirement(
        "CLERK_WEBHOOK_SIGNING_SECRET",
        "cgs",
        "auth",
        "blocking",
        "optional",
        "cgs",
        aliases=("CLERK_WEBHOOK_SECRET",),
    ),
    EnvRequirement("CGS_AUTH_ISSUER", "shared", "auth", "blocking", "required", "either"),
    EnvRequirement("CGS_AUTH_JWKS_URL", "shared", "auth", "blocking", "required", "either"),
    EnvRequirement("CGS_AUTH_AUDIENCE", "shared", "auth", "warning", "optional", "either"),
    EnvRequirement("STRIPE_SECRET_KEY", "cgs", "billing", "blocking", "optional", "cgs"),
    EnvRequirement("STRIPE_WEBHOOK_SECRET", "cgs", "billing", "blocking", "optional", "cgs"),
    EnvRequirement("CGS_STRIPE_DEFAULT_PRICE_ID", "cgs", "billing", "blocking", "optional", "cgs"),
    EnvRequirement(
        "VERCEL_WEBHOOK_SECRET", "cgs", "vercel_integration", "blocking", "optional", "cgs"
    ),
    EnvRequirement("VERCEL_API_TOKEN", "cgs", "vercel_integration", "blocking", "optional", "cgs"),
    EnvRequirement("EDGE_CONFIG_ID", "cgs", "vercel_integration", "warning", "optional", "cgs"),
    EnvRequirement(
        "GITHUB_WEBHOOK_SECRET", "cgs", "vercel_integration", "blocking", "optional", "cgs"
    ),
    EnvRequirement(
        "ZETHERION_PUBLIC_API_BASE_URL",
        "shared",
        "vercel_integration",
        "blocking",
        "required",
        "either",
    ),
    EnvRequirement(
        "ZETHERION_SKILLS_API_BASE_URL",
        "shared",
        "vercel_integration",
        "blocking",
        "required",
        "either",
    ),
    EnvRequirement(
        "ZETHERION_SKILLS_API_SECRET",
        "shared",
        "vercel_integration",
        "blocking",
        "required",
        "either",
        aliases=("SKILLS_API_SECRET",),
    ),
    EnvRequirement(
        "ZETHERION_OWNER_CI_WORKER_BASE_URL",
        "shared",
        "vercel_integration",
        "blocking",
        "required",
        "cgs",
    ),
    EnvRequirement(
        "CGS_CI_RELAY_SECRET", "shared", "vercel_integration", "blocking", "required", "cgs"
    ),
    EnvRequirement("TRADEOXY_SYNC_BASE_URL", "cgs", "tradeoxy", "warning", "optional", "cgs"),
    EnvRequirement("TRADEOXY_SYNC_SECRET", "cgs", "tradeoxy", "warning", "optional", "cgs"),
)


CGS_PATTERN_REQUIREMENTS: tuple[EnvPatternRequirement, ...] = (
    EnvPatternRequirement(
        r"^CGS_AI_CI_[A-Z0-9_]+$", "cgs", "billing", "warning", "optional", "cgs"
    ),
    EnvPatternRequirement(
        r"^CGS_STRIPE_PRICE_[A-Z0-9_]+$", "cgs", "billing", "warning", "optional", "cgs"
    ),
)


ZETHERION_REQUIREMENTS: tuple[EnvRequirement, ...] = (
    EnvRequirement(
        "OPENAI_API_KEY", "zetherion", "core_provider", "blocking", "required", "zetherion"
    ),
    EnvRequirement(
        "GROQ_API_KEY", "zetherion", "core_provider", "blocking", "required", "zetherion"
    ),
    EnvRequirement(
        "API_JWT_SECRET", "zetherion", "core_provider", "blocking", "required", "zetherion"
    ),
    EnvRequirement(
        "CGS_AUTH_JWKS_URL", "shared", "core_provider", "blocking", "required", "either"
    ),
    EnvRequirement(
        "DOCKER_SOCKET_PATH",
        "zetherion",
        "runtime_windows",
        "warning",
        "windows_only",
        "zetherion",
    ),
    EnvRequirement(
        "ZETHERION_HOST_WORKSPACE_ROOT",
        "zetherion",
        "runtime_windows",
        "warning",
        "windows_only",
        "zetherion",
    ),
    EnvRequirement(
        "ZETHERION_WORKSPACE_MOUNT_TARGET",
        "zetherion",
        "runtime_windows",
        "warning",
        "windows_only",
        "zetherion",
    ),
    EnvRequirement(
        "DISCORD_TOKEN", "zetherion", "announcements_alerts", "blocking", "required", "zetherion"
    ),
    EnvRequirement(
        "ANNOUNCEMENT_EMIT_ENABLED",
        "zetherion",
        "announcements_alerts",
        "warning",
        "optional",
        "zetherion",
    ),
    EnvRequirement(
        "ANNOUNCEMENT_API_URL",
        "shared",
        "announcements_alerts",
        "warning",
        "optional",
        "zetherion",
    ),
    EnvRequirement(
        "ANNOUNCEMENT_API_SECRET",
        "shared",
        "announcements_alerts",
        "blocking",
        "required",
        "either",
        aliases=("SKILLS_API_SECRET",),
        note=(
            "ANNOUNCEMENT_API_SECRET is preferred; SKILLS_API_SECRET remains a "
            "compatibility fallback."
        ),
    ),
    EnvRequirement(
        "ANNOUNCEMENT_TARGET_USER_ID",
        "zetherion",
        "announcements_alerts",
        "warning",
        "optional",
        "zetherion",
    ),
    EnvRequirement(
        "OBJECT_STORAGE_BACKEND",
        "zetherion",
        "runtime_windows",
        "warning",
        "optional",
        "zetherion",
    ),
    EnvRequirement(
        "OBJECT_STORAGE_BUCKET",
        "zetherion",
        "runtime_windows",
        "warning",
        "optional",
        "zetherion",
    ),
    EnvRequirement(
        "OBJECT_STORAGE_ENDPOINT",
        "zetherion",
        "runtime_windows",
        "warning",
        "optional",
        "zetherion",
    ),
    EnvRequirement(
        "OBJECT_STORAGE_ACCESS_KEY_ID",
        "zetherion",
        "runtime_windows",
        "warning",
        "optional",
        "zetherion",
    ),
    EnvRequirement(
        "OBJECT_STORAGE_SECRET_ACCESS_KEY",
        "zetherion",
        "runtime_windows",
        "warning",
        "optional",
        "zetherion",
    ),
)


ZETHERION_PATTERN_REQUIREMENTS: tuple[EnvPatternRequirement, ...] = (
    EnvPatternRequirement(
        r"^WINDOWS_DISCORD_CANARY_[A-Z0-9_]+$",
        "zetherion",
        "announcements_alerts",
        "warning",
        "optional",
        "zetherion",
    ),
    EnvPatternRequirement(
        r"^(ROUTER_|OPENAI_|GROQ_|CLAUDE_|EMBEDDING_|EMBEDDINGS_|OLLAMA_)[A-Z0-9_]+$",
        "zetherion",
        "core_provider",
        "warning",
        "optional",
        "zetherion",
        note="Discovered provider/model selection configuration present in the live environment.",
    ),
    EnvPatternRequirement(
        r"^(WORKER_|OWNER_CI_|REPLAY_|OBJECT_STORAGE_)[A-Z0-9_]+$",
        "zetherion",
        "runtime_windows",
        "warning",
        "optional",
        "zetherion",
    ),
)


SHARED_KEY_EXPECTATIONS: tuple[SharedKeyExpectation, ...] = (
    SharedKeyExpectation(
        "CGS_AUTH_ISSUER",
        required_in=("cgs",),
        windows_requirement="blocking",
        local_debug="required",
    ),
    SharedKeyExpectation(
        "CGS_AUTH_JWKS_URL",
        required_in=("cgs", "zetherion"),
        windows_requirement="blocking",
        local_debug="required",
    ),
    SharedKeyExpectation(
        "CGS_AUTH_AUDIENCE",
        required_in=("cgs",),
        windows_requirement="warning",
        local_debug="optional",
    ),
    SharedKeyExpectation(
        "ZETHERION_PUBLIC_API_BASE_URL",
        required_in=("cgs",),
        windows_requirement="blocking",
        local_debug="required",
    ),
    SharedKeyExpectation(
        "ZETHERION_SKILLS_API_BASE_URL",
        required_in=("cgs",),
        windows_requirement="blocking",
        local_debug="required",
    ),
    SharedKeyExpectation(
        "ZETHERION_SKILLS_API_SECRET",
        required_in=("cgs", "zetherion"),
        windows_requirement="blocking",
        local_debug="required",
        aliases=("SKILLS_API_SECRET",),
    ),
    SharedKeyExpectation(
        "ANNOUNCEMENT_API_URL",
        required_in=("zetherion",),
        windows_requirement="warning",
        local_debug="optional",
    ),
    SharedKeyExpectation(
        "ANNOUNCEMENT_API_SECRET",
        required_in=("zetherion",),
        windows_requirement="blocking",
        local_debug="required",
        aliases=("SKILLS_API_SECRET",),
    ),
)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cgs-env-file", default="", help="Path to the CGS live env file.")
    parser.add_argument(
        "--zetherion-env-file",
        default="",
        help="Path to the Zetherion live env file.",
    )
    parser.add_argument(
        "--out-dir",
        default=".artifacts/windows-live-env",
        help="Directory for sanitized manifest outputs.",
    )
    parser.add_argument("--host-label", default="windows-live", help="Host label for the report.")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="Exit non-zero when blocking Windows requirements are missing or placeholders.",
    )
    return parser.parse_args(argv)


def _strip_wrapping_quotes(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1].strip()
    return value


def parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    env_map: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if not re.match(r"^[A-Za-z_][A-Za-z0-9_]*$", key):
            continue
        env_map[key] = _strip_wrapping_quotes(value)
    return env_map


def classify_env_value(value: str | None) -> EnvStatus:
    if value is None:
        return "missing"
    stripped = _strip_wrapping_quotes(value)
    for pattern in PLACEHOLDER_PATTERNS:
        if pattern.search(stripped):
            return "placeholder"
    return "present"


def _resolve_entry(requirement: EnvRequirement, env_map: dict[str, str]) -> ManifestEntry:
    keys = (requirement.name, *requirement.aliases)
    matched_key: str | None = None
    status: EnvStatus = "missing"

    for key in keys:
        current_status = classify_env_value(env_map.get(key))
        if current_status == "present":
            matched_key = key
            status = current_status
            break
        if current_status == "placeholder" and status == "missing":
            matched_key = key
            status = current_status

    return ManifestEntry(
        name=requirement.name,
        matched_key=matched_key,
        status=status,
        present=status == "present",
        system=requirement.system,
        group=requirement.group,
        windows_requirement=requirement.windows_requirement,
        local_debug=requirement.local_debug,
        source=requirement.source,
        aliases=list(requirement.aliases),
        note=requirement.note,
    )


def _resolve_patterns(
    requirements: Iterable[EnvPatternRequirement], env_map: dict[str, str]
) -> list[dict[str, object]]:
    discovered: list[dict[str, object]] = []
    for requirement in requirements:
        regex = re.compile(requirement.pattern)
        matches = sorted(key for key in env_map if regex.match(key))
        if not matches:
            continue
        discovered.append(
            {
                "pattern": requirement.pattern,
                "system": requirement.system,
                "group": requirement.group,
                "windows_requirement": requirement.windows_requirement,
                "local_debug": requirement.local_debug,
                "source": requirement.source,
                "note": requirement.note,
                "matches": matches,
            }
        )
    return discovered


def _status_from_keys(keys: Iterable[str], env_map: dict[str, str]) -> tuple[EnvStatus, str | None]:
    matched_key: str | None = None
    status: EnvStatus = "missing"
    for key in keys:
        current_status = classify_env_value(env_map.get(key))
        if current_status == "present":
            return "present", key
        if current_status == "placeholder" and status == "missing":
            matched_key = key
            status = "placeholder"
    return status, matched_key


def build_manifest(
    *,
    system: Literal["cgs", "zetherion"],
    env_path: Path,
    env_map: dict[str, str],
    host_label: str,
) -> dict[str, object]:
    if system == "cgs":
        requirements = CGS_REQUIREMENTS
        pattern_requirements = CGS_PATTERN_REQUIREMENTS
    else:
        requirements = ZETHERION_REQUIREMENTS
        pattern_requirements = ZETHERION_PATTERN_REQUIREMENTS

    entries = [_resolve_entry(requirement, env_map) for requirement in requirements]
    grouped: dict[str, list[dict[str, object]]] = {}
    for entry in entries:
        grouped.setdefault(entry.group, []).append(asdict(entry))

    blocking_missing = [
        entry.name
        for entry in entries
        if entry.windows_requirement == "blocking" and entry.status != "present"
    ]
    warning_missing = [
        entry.name
        for entry in entries
        if entry.windows_requirement == "warning" and entry.status != "present"
    ]
    local_debug_missing = [
        entry.name
        for entry in entries
        if entry.local_debug == "required" and entry.status != "present"
    ]

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "host_label": host_label,
        "system": system,
        "source_env_file": str(env_path),
        "source_exists": env_path.exists(),
        "summary": {
            "blocking_missing": blocking_missing,
            "warning_missing": warning_missing,
            "local_debug_missing": local_debug_missing,
            "present_count": sum(1 for entry in entries if entry.status == "present"),
            "placeholder_count": sum(1 for entry in entries if entry.status == "placeholder"),
            "missing_count": sum(1 for entry in entries if entry.status == "missing"),
        },
        "groups": [
            {"group": group, "entries": payload} for group, payload in sorted(grouped.items())
        ],
        "pattern_matches": _resolve_patterns(pattern_requirements, env_map),
    }


def build_shared_cross_system_map(
    cgs_env_map: dict[str, str], zetherion_env_map: dict[str, str], host_label: str
) -> dict[str, object]:
    entries: list[dict[str, object]] = []
    for requirement in SHARED_KEY_EXPECTATIONS:
        keys = (requirement.name, *requirement.aliases)
        cgs_status, cgs_matched_key = _status_from_keys(keys, cgs_env_map)
        zetherion_status, zetherion_matched_key = _status_from_keys(keys, zetherion_env_map)
        missing_bindings: list[str] = []
        if "cgs" in requirement.required_in and cgs_status != "present":
            missing_bindings.append("cgs")
        if "zetherion" in requirement.required_in and zetherion_status != "present":
            missing_bindings.append("zetherion")
        entries.append(
            {
                "name": requirement.name,
                "cgs_status": cgs_status,
                "zetherion_status": zetherion_status,
                "cgs_matched_key": cgs_matched_key,
                "zetherion_matched_key": zetherion_matched_key,
                "required_in": list(requirement.required_in),
                "windows_requirement": requirement.windows_requirement,
                "local_debug": requirement.local_debug,
                "missing_bindings": missing_bindings,
                "status": "aligned" if not missing_bindings else "missing_required_binding",
            }
        )

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),  # noqa: UP017
        "host_label": host_label,
        "entries": entries,
        "summary": {
            "blocking_missing_bindings": [
                entry["name"]
                for entry in entries
                if entry["windows_requirement"] == "blocking" and entry["missing_bindings"]
            ],
            "warning_missing_bindings": [
                entry["name"]
                for entry in entries
                if entry["windows_requirement"] == "warning" and entry["missing_bindings"]
            ],
        },
    }


def render_summary_markdown(
    cgs_manifest: dict[str, object],
    zetherion_manifest: dict[str, object],
    shared_map: dict[str, object],
) -> str:
    cgs_summary = cgs_manifest["summary"]
    zetherion_summary = zetherion_manifest["summary"]
    shared_summary = shared_map["summary"]
    lines = [
        "# Windows Live Env Harvest Summary",
        "",
        f"- Generated at: {cgs_manifest['generated_at']}",
        f"- Host label: {cgs_manifest['host_label']}",
        (
            f"- CGS env source: {cgs_manifest['source_env_file']} "
            f"(exists={cgs_manifest['source_exists']})"
        ),
        (
            f"- Zetherion env source: {zetherion_manifest['source_env_file']} "
            f"(exists={zetherion_manifest['source_exists']})"
        ),
        "",
        "## Blocking Before First Windows Certification",
        "",
    ]
    blocking_items = [
        ("CGS", cgs_summary["blocking_missing"]),
        ("Zetherion", zetherion_summary["blocking_missing"]),
        ("Shared cross-system", shared_summary["blocking_missing_bindings"]),
    ]
    for label, items in blocking_items:
        if items:
            lines.append(f"- {label}: {', '.join(items)}")
        else:
            lines.append(f"- {label}: none")

    lines.extend(
        [
            "",
            "## Also Required Locally For Fallback Debugging",
            "",
            _render_summary_list(
                "CGS",
                cgs_summary["local_debug_missing"],
                "none missing",
            ),
            _render_summary_list(
                "Zetherion",
                zetherion_summary["local_debug_missing"],
                "none missing",
            ),
            "",
            "## Warning-Only / Optional Gaps",
            "",
            _render_summary_list("CGS", cgs_summary["warning_missing"]),
            _render_summary_list("Zetherion", zetherion_summary["warning_missing"]),
            _render_summary_list(
                "Shared cross-system",
                shared_summary["warning_missing_bindings"],
            ),
            "",
            "## Notes",
            "",
            "- These manifests intentionally store names, presence state, and classification only.",
            "- Placeholder values count as non-ready for blocking Windows certification.",
            "- TradeOxy keys remain warning-only until the deferred final release wave.",
        ]
    )
    return "\n".join(lines) + "\n"


def write_json(path: Path, payload: dict[str, object]) -> None:
    path.write_text(json.dumps(payload, indent=2, sort_keys=False) + "\n", encoding="utf-8")


def _render_summary_list(label: str, items: list[str], empty: str = "none") -> str:
    return f"- {label}: {', '.join(items) if items else empty}"


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    out_dir = Path(args.out_dir).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)

    cgs_env_path = Path(args.cgs_env_file).expanduser().resolve() if args.cgs_env_file else Path("")
    zetherion_env_path = (
        Path(args.zetherion_env_file).expanduser().resolve()
        if args.zetherion_env_file
        else Path("")
    )

    cgs_env_map = parse_env_file(cgs_env_path) if args.cgs_env_file else {}
    zetherion_env_map = parse_env_file(zetherion_env_path) if args.zetherion_env_file else {}

    cgs_manifest = build_manifest(
        system="cgs",
        env_path=cgs_env_path if args.cgs_env_file else Path("<not-provided>"),
        env_map=cgs_env_map,
        host_label=args.host_label,
    )
    zetherion_manifest = build_manifest(
        system="zetherion",
        env_path=zetherion_env_path if args.zetherion_env_file else Path("<not-provided>"),
        env_map=zetherion_env_map,
        host_label=args.host_label,
    )
    shared_map = build_shared_cross_system_map(cgs_env_map, zetherion_env_map, args.host_label)

    write_json(out_dir / "cgs-live-env-manifest.json", cgs_manifest)
    write_json(out_dir / "zetherion-live-env-manifest.json", zetherion_manifest)
    write_json(out_dir / "shared-cross-system-env-map.json", shared_map)
    (out_dir / "windows-live-env-summary.md").write_text(
        render_summary_markdown(cgs_manifest, zetherion_manifest, shared_map),
        encoding="utf-8",
    )

    blocking_missing = (
        cgs_manifest["summary"]["blocking_missing"]
        + zetherion_manifest["summary"]["blocking_missing"]
        + shared_map["summary"]["blocking_missing_bindings"]
    )
    print(
        json.dumps(
            {
                "status": "ok" if not blocking_missing else "blocking_missing",
                "output_dir": str(out_dir),
                "blocking_missing": blocking_missing,
            }
        )
    )
    return 1 if args.strict and blocking_missing else 0


if __name__ == "__main__":
    raise SystemExit(main())
