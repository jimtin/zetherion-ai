"""Helpers for isolated Docker-backed E2E runs."""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

DEFAULT_COMPOSE_FILE = "docker-compose.test.yml"
DEFAULT_PROJECT = "zetherion-ai-test"
SERVICE_SLOT_OFFSETS = {
    "slot_a": 0,
    "slot_b": 1000,
}


@dataclass(frozen=True)
class E2ERuntime:
    compose_file: str
    project_name: str
    run_id: str
    stack_root: str
    host: str
    service_slot: str
    port_offset: int
    skills_port: int
    api_port: int
    cgs_gateway_port: int
    whatsapp_bridge_port: int
    postgres_port: int
    qdrant_port: int
    ollama_port: int
    ollama_router_port: int

    @property
    def skills_url(self) -> str:
        return f"http://{self.host}:{self.skills_port}"

    @property
    def api_url(self) -> str:
        return f"http://{self.host}:{self.api_port}"

    @property
    def cgs_gateway_url(self) -> str:
        return f"http://{self.host}:{self.cgs_gateway_port}"

    @property
    def qdrant_url(self) -> str:
        return f"http://{self.host}:{self.qdrant_port}"

    @property
    def postgres_dsn(self) -> str:
        return f"postgresql://zetherion:password@{self.host}:{self.postgres_port}/zetherion"

    def compose_base_command(self) -> list[str]:
        return ["docker", "compose", "-f", self.compose_file, "-p", self.project_name]

    def env_file_path(self) -> Path | None:
        candidate = os.getenv("E2E_RUN_ENV_PATH", "").strip()
        if candidate:
            path = Path(candidate)
            return path if path.is_file() else None
        if self.stack_root:
            path = Path(self.stack_root) / "run.env"
            return path if path.is_file() else None
        return None

    def env_file_values(self) -> dict[str, str]:
        path = self.env_file_path()
        if path is None:
            return {}
        values: dict[str, str] = {}
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw or raw.startswith("#") or "=" not in raw:
                continue
            key, value = raw.split("=", 1)
            values[key.strip()] = value.strip()
        return values

    def resolve_secret(
        self,
        *keys: str,
        default: str | None = None,
    ) -> str | None:
        env_values = self.env_file_values()
        for key in keys:
            value = os.getenv(key)
            if value:
                return value
            value = env_values.get(key)
            if value:
                return value
        return default

    def service_container_id(self, service: str) -> str | None:
        result = subprocess.run(
            [*self.compose_base_command(), "ps", "-q", service],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode == 0:
            container_id = result.stdout.strip().splitlines()
            if container_id:
                return container_id[0].strip()

        fallback = subprocess.run(
            [
                "docker",
                "ps",
                "-q",
                "--filter",
                f"label=com.docker.compose.project={self.project_name}",
                "--filter",
                f"label=com.docker.compose.service={service}",
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if fallback.returncode != 0:
            return None
        container_id = fallback.stdout.strip().splitlines()
        return container_id[0].strip() if container_id else None

    def service_running(self, service: str) -> bool:
        container_id = self.service_container_id(service)
        if not container_id:
            return False
        result = subprocess.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", container_id],
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.returncode == 0 and result.stdout.strip() == "running"

    def service_health(self, service: str) -> str:
        container_id = self.service_container_id(service)
        if not container_id:
            return "missing"
        result = subprocess.run(
            [
                "docker",
                "inspect",
                "--format",
                "{{if .State.Health}}{{.State.Health.Status}}{{else}}{{.State.Status}}{{end}}",
                container_id,
            ],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0:
            return "missing"
        return result.stdout.strip() or "missing"


_runtime: E2ERuntime | None = None


def get_runtime() -> E2ERuntime:
    global _runtime
    if _runtime is not None:
        return _runtime

    def _port(name: str, default: int) -> int:
        return int(os.getenv(name, str(default + slot_offset)))

    requested_slot = os.getenv("E2E_SERVICE_SLOT", "slot_a").strip().lower() or "slot_a"
    service_slot = requested_slot if requested_slot in SERVICE_SLOT_OFFSETS else "slot_a"
    slot_offset = SERVICE_SLOT_OFFSETS[service_slot]

    _runtime = E2ERuntime(
        compose_file=os.getenv("COMPOSE_FILE", DEFAULT_COMPOSE_FILE),
        project_name=os.getenv("E2E_PROJECT_NAME", os.getenv("PROJECT", DEFAULT_PROJECT)),
        run_id=os.getenv("E2E_RUN_ID", "static"),
        stack_root=os.getenv("E2E_STACK_ROOT", ""),
        host=os.getenv("E2E_RUNTIME_HOST", "localhost"),
        service_slot=service_slot,
        port_offset=slot_offset,
        skills_port=_port("E2E_SKILLS_HOST_PORT", 18080),
        api_port=_port("E2E_API_HOST_PORT", 28443),
        cgs_gateway_port=_port("E2E_CGS_GATEWAY_HOST_PORT", 28444),
        whatsapp_bridge_port=_port("E2E_WHATSAPP_BRIDGE_HOST_PORT", 18877),
        postgres_port=_port("E2E_POSTGRES_HOST_PORT", 15432),
        qdrant_port=_port("E2E_QDRANT_HOST_PORT", 16333),
        ollama_port=_port("E2E_OLLAMA_HOST_PORT", 21434),
        ollama_router_port=_port("E2E_OLLAMA_ROUTER_HOST_PORT", 31434),
    )
    return _runtime
