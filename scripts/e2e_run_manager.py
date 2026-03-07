#!/usr/bin/env python3
"""Manage isolated Docker-backed E2E runs for local and canary workflows."""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shlex
import shutil
import socket
import subprocess
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_COMPOSE_FILE = REPO_ROOT / "docker-compose.test.yml"
DEFAULT_RUNS_ROOT = REPO_ROOT / ".artifacts" / "e2e-runs"
DEFAULT_PROJECT_PREFIX = "zetherion-ai-test"
DEFAULT_TTL_MINUTES = 180
RUN_LABEL = "zetherion.e2e"

PORT_DEFAULTS: dict[str, int] = {
    "E2E_API_HOST_PORT": 28443,
    "E2E_CGS_GATEWAY_HOST_PORT": 28444,
    "E2E_SKILLS_HOST_PORT": 18080,
    "E2E_WHATSAPP_BRIDGE_HOST_PORT": 18877,
    "E2E_OLLAMA_ROUTER_HOST_PORT": 31434,
    "E2E_OLLAMA_HOST_PORT": 21434,
    "E2E_POSTGRES_HOST_PORT": 15432,
    "E2E_QDRANT_HOST_PORT": 16333,
}


@dataclass(frozen=True)
class RunLayout:
    runs_root: Path
    manifests_dir: Path
    stacks_dir: Path


@dataclass(frozen=True)
class RunPaths:
    manifest_path: Path
    stack_root: Path
    data_root: Path
    logs_root: Path
    env_file: Path


class RunManagerError(RuntimeError):
    """Raised when an E2E run cannot be created or cleaned."""


def _now() -> datetime:
    return datetime.now(tz=UTC)


def _iso(timestamp: datetime) -> str:
    return timestamp.isoformat()


def build_layout(runs_root: Path) -> RunLayout:
    return RunLayout(
        runs_root=runs_root,
        manifests_dir=runs_root / "manifests",
        stacks_dir=runs_root / "stacks",
    )


def ensure_layout(layout: RunLayout) -> None:
    layout.runs_root.mkdir(parents=True, exist_ok=True)
    layout.manifests_dir.mkdir(parents=True, exist_ok=True)
    layout.stacks_dir.mkdir(parents=True, exist_ok=True)


def make_run_id(prefix: str = "run") -> str:
    stamp = _now().strftime("%Y%m%d-%H%M%S")
    return f"{prefix}-{stamp}-{secrets.token_hex(3)}"


def reserve_host_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        return int(sock.getsockname()[1])


def allocate_port_map() -> dict[str, int]:
    allocated: dict[str, int] = {}
    used: set[int] = set()
    for env_name in PORT_DEFAULTS:
        port = reserve_host_port()
        while port in used:
            port = reserve_host_port()
        used.add(port)
        allocated[env_name] = port
    return allocated


def build_paths(layout: RunLayout, run_id: str) -> RunPaths:
    stack_root = layout.stacks_dir / run_id
    return RunPaths(
        manifest_path=layout.manifests_dir / f"{run_id}.json",
        stack_root=stack_root,
        data_root=stack_root / "data",
        logs_root=stack_root / "logs",
        env_file=stack_root / "run.env",
    )


def write_env_file(path: Path, values: dict[str, str]) -> None:
    lines = [f"{key}={value}" for key, value in sorted(values.items())]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def load_manifest(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise RunManagerError(f"manifest must be a JSON object: {path}")
    return payload


def write_manifest(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def render_shell_exports(values: dict[str, str]) -> str:
    return "\n".join(f"export {key}={shlex.quote(value)}" for key, value in sorted(values.items()))


def _run_command(command: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(command, capture_output=True, text=True)


def _list_resource_ids(kind: str, label: str) -> list[str]:
    command = ["docker", kind, "ls", "-q", "--filter", f"label={label}"]
    result = _run_command(command)
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def _list_container_ids(project: str) -> list[str]:
    result = _run_command(
        ["docker", "ps", "-aq", "--filter", f"label=com.docker.compose.project={project}"]
    )
    if result.returncode != 0:
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def cleanup_resources(*, compose_file: str, project: str) -> dict[str, Any]:
    cleanup: dict[str, Any] = {
        "compose_down": {"returncode": None, "stdout": "", "stderr": ""},
        "containers_removed": [],
        "volumes_removed": [],
        "networks_removed": [],
        "errors": [],
    }

    result = _run_command(
        [
            "docker",
            "compose",
            "-f",
            compose_file,
            "-p",
            project,
            "down",
            "-v",
            "--remove-orphans",
        ]
    )
    cleanup["compose_down"] = {
        "returncode": result.returncode,
        "stdout": result.stdout.strip(),
        "stderr": result.stderr.strip(),
    }

    container_ids = _list_container_ids(project)
    if container_ids:
        rm_result = _run_command(["docker", "rm", "-f", *container_ids])
        if rm_result.returncode == 0:
            cleanup["containers_removed"] = container_ids
        else:
            cleanup["errors"].append(
                f"docker rm failed for project {project}: {rm_result.stderr.strip()}"
            )

    volume_ids = _list_resource_ids("volume", f"com.docker.compose.project={project}")
    if volume_ids:
        rm_result = _run_command(["docker", "volume", "rm", *volume_ids])
        if rm_result.returncode == 0:
            cleanup["volumes_removed"] = volume_ids
        else:
            cleanup["errors"].append(
                f"docker volume rm failed for project {project}: {rm_result.stderr.strip()}"
            )

    network_ids = _list_resource_ids("network", f"com.docker.compose.project={project}")
    if network_ids:
        rm_result = _run_command(["docker", "network", "rm", *network_ids])
        if rm_result.returncode == 0:
            cleanup["networks_removed"] = network_ids
        else:
            cleanup["errors"].append(
                f"docker network rm failed for project {project}: {rm_result.stderr.strip()}"
            )

    return cleanup


def create_run(
    *,
    runs_root: Path,
    compose_file: Path,
    project_prefix: str,
    ttl_minutes: int,
) -> tuple[dict[str, Any], dict[str, str]]:
    layout = build_layout(runs_root)
    ensure_layout(layout)

    run_id = make_run_id()
    paths = build_paths(layout, run_id)
    paths.data_root.mkdir(parents=True, exist_ok=True)
    paths.logs_root.mkdir(parents=True, exist_ok=True)

    host_ports = allocate_port_map()
    project_name = f"{project_prefix}-{run_id}"
    created_at = _now()
    expires_at = created_at + timedelta(minutes=ttl_minutes)

    exports: dict[str, str] = {
        "E2E_RUN_ID": run_id,
        "E2E_PROJECT_NAME": project_name,
        "E2E_STACK_ROOT": str(paths.stack_root),
        "E2E_RUN_MANIFEST_PATH": str(paths.manifest_path),
        "E2E_RUN_ENV_PATH": str(paths.env_file),
        "COMPOSE_FILE": str(compose_file),
        "PROJECT": project_name,
    }
    exports.update({key: str(value) for key, value in host_ports.items()})

    write_env_file(paths.env_file, exports)

    manifest: dict[str, Any] = {
        "version": 1,
        "run_label": RUN_LABEL,
        "run_id": run_id,
        "compose_project": project_name,
        "compose_file": str(compose_file),
        "stack_root": str(paths.stack_root),
        "env_file": str(paths.env_file),
        "artifacts": {
            "data_root": str(paths.data_root),
            "logs_root": str(paths.logs_root),
        },
        "ports": host_ports,
        "lease": {
            "created_at": _iso(created_at),
            "expires_at": _iso(expires_at),
            "ttl_minutes": ttl_minutes,
            "owner_pid": os.getpid(),
            "status": "active",
        },
        "cleanup": {
            "status": "pending",
            "reason": "",
            "completed_at": None,
            "details": None,
        },
    }
    write_manifest(paths.manifest_path, manifest)
    return manifest, exports


def _manifest_expired(payload: dict[str, Any], now: datetime) -> bool:
    lease = payload.get("lease")
    if not isinstance(lease, dict):
        return True
    expires_at = str(lease.get("expires_at", "")).strip()
    if not expires_at:
        return True
    try:
        expiry = datetime.fromisoformat(expires_at)
    except ValueError:
        return True
    if expiry.tzinfo is None:
        expiry = expiry.replace(tzinfo=UTC)
    return expiry <= now


def cleanup_run(
    *,
    manifest_path: Path,
    reason: str,
    delete_stack_root: bool = True,
) -> dict[str, Any]:
    payload = load_manifest(manifest_path)
    compose_file = str(payload.get("compose_file") or DEFAULT_COMPOSE_FILE)
    project_name = str(payload.get("compose_project") or "")
    stack_root = Path(str(payload.get("stack_root") or ""))
    if not project_name:
        raise RunManagerError(f"compose project missing in manifest: {manifest_path}")

    details = cleanup_resources(compose_file=compose_file, project=project_name)
    if delete_stack_root and stack_root.exists():
        shutil.rmtree(stack_root, ignore_errors=True)

    cleanup_errors = details.get("errors") or []
    cleanup_status = "cleaned" if not cleanup_errors else "cleanup_failed"
    payload.setdefault("cleanup", {})
    if not isinstance(payload["cleanup"], dict):
        payload["cleanup"] = {}
    payload["cleanup"].update(
        {
            "status": cleanup_status,
            "reason": reason,
            "completed_at": _iso(_now()),
            "details": details,
        }
    )
    payload.setdefault("lease", {})
    if not isinstance(payload["lease"], dict):
        payload["lease"] = {}
    payload["lease"]["status"] = "cleaned" if cleanup_status == "cleaned" else "cleanup_failed"
    write_manifest(manifest_path, payload)
    return payload


def janitor(*, runs_root: Path, include_active: bool = False) -> dict[str, Any]:
    layout = build_layout(runs_root)
    ensure_layout(layout)
    now = _now()
    cleaned: list[dict[str, Any]] = []
    skipped: list[str] = []

    for manifest_path in sorted(layout.manifests_dir.glob("*.json")):
        payload = load_manifest(manifest_path)
        lease = payload.get("lease")
        if isinstance(lease, dict) and str(lease.get("status", "")).strip() in {
            "cleaned",
            "cleanup_failed",
        }:
            skipped.append(str(manifest_path))
            continue

        expired = _manifest_expired(payload, now)
        if not expired and not include_active:
            skipped.append(str(manifest_path))
            continue

        cleaned_payload = cleanup_run(
            manifest_path=manifest_path,
            reason="janitor_expired" if expired else "janitor_manual",
        )
        cleaned.append(
            {
                "manifest_path": str(manifest_path),
                "run_id": cleaned_payload.get("run_id"),
                "status": cleaned_payload.get("cleanup", {}).get("status"),
            }
        )

    return {
        "cleaned": cleaned,
        "skipped": skipped,
        "generated_at": _iso(now),
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    start_parser = subparsers.add_parser("start", help="Create a new isolated E2E run lease.")
    start_parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    start_parser.add_argument("--compose-file", default=str(DEFAULT_COMPOSE_FILE))
    start_parser.add_argument("--project-prefix", default=DEFAULT_PROJECT_PREFIX)
    start_parser.add_argument("--ttl-minutes", type=int, default=DEFAULT_TTL_MINUTES)
    start_parser.add_argument(
        "--shell",
        action="store_true",
        help="Print shell exports instead of JSON.",
    )

    cleanup_parser = subparsers.add_parser("cleanup", help="Clean a specific E2E run.")
    cleanup_parser.add_argument("--manifest", required=True)
    cleanup_parser.add_argument("--reason", default="explicit_cleanup")

    janitor_parser = subparsers.add_parser("janitor", help="Clean expired E2E runs.")
    janitor_parser.add_argument("--runs-root", default=str(DEFAULT_RUNS_ROOT))
    janitor_parser.add_argument(
        "--include-active",
        action="store_true",
        help="Clean all known runs, not only expired ones.",
    )

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "start":
        manifest, exports = create_run(
            runs_root=Path(args.runs_root),
            compose_file=Path(args.compose_file),
            project_prefix=str(args.project_prefix),
            ttl_minutes=int(args.ttl_minutes),
        )
        if args.shell:
            print(render_shell_exports(exports))
        else:
            print(json.dumps({"manifest": manifest, "exports": exports}, indent=2))
        return 0

    if args.command == "cleanup":
        payload = cleanup_run(manifest_path=Path(args.manifest), reason=str(args.reason))
        print(json.dumps(payload, indent=2))
        return 0

    if args.command == "janitor":
        payload = janitor(runs_root=Path(args.runs_root), include_active=bool(args.include_active))
        print(json.dumps(payload, indent=2))
        return 0

    parser.error(f"unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
