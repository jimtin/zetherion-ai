"""Configuration for the dev agent."""

from __future__ import annotations

import os
import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path(
    os.environ.get(
        "ZETHERION_DEV_AGENT_HOME",
        str(Path.home() / ".zetherion-dev-agent"),
    )
).expanduser()
CONFIG_FILE = CONFIG_DIR / "config.toml"
STATE_FILE = CONFIG_DIR / "state.json"
DATABASE_FILE = CONFIG_DIR / "daemon.db"
WORKER_LOG_DIR = CONFIG_DIR / "worker-jobs"


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    return default


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        return int(raw.strip())
    except ValueError:
        return default


def _env_list(name: str, default: list[str]) -> list[str]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    values = [item.strip() for item in raw.split(",") if item.strip()]
    return values or default


def _toml_basic_string(value: str) -> str:
    escaped = str(value).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def _toml_string_array(values: list[str]) -> str:
    return "[" + ", ".join(_toml_basic_string(value) for value in values) + "]"


@dataclass
class AgentConfig:
    """Dev agent configuration."""

    webhook_url: str = ""
    agent_name: str = "zetherion-dev-agent"
    repos: list[str] = field(default_factory=list)
    scan_interval: int = 60  # seconds
    claude_code_enabled: bool = True
    annotations_enabled: bool = True
    git_enabled: bool = True
    container_monitor_enabled: bool = True
    cleanup_enabled: bool = True
    cleanup_hour: int = 2
    cleanup_minute: int = 30
    approval_reprompt_hours: int = 24
    api_host: str = "127.0.0.1"
    api_port: int = 8787
    api_token: str = ""
    api_tls_cert_path: str = ""
    api_tls_key_path: str = ""
    internal_tls_ca_path: str = ""
    api_require_client_cert: bool = True
    database_path: str = str(DATABASE_FILE)
    bootstrap_secret: str = ""
    bootstrap_require_once: bool = True
    worker_base_url: str = "http://127.0.0.1:8000/worker/v1"
    worker_relay_base_url: str = ""
    worker_relay_secret: str = ""
    worker_control_plane: str = "tenant"
    worker_scope_id: str = ""
    worker_tenant_id: str = ""
    worker_node_id: str = ""
    worker_node_name: str = ""
    worker_bootstrap_secret: str = ""
    worker_capabilities: list[str] = field(
        default_factory=lambda: ["ci.test.run", "repo.patch", "repo.commit", "repo.pr.open"]
    )
    worker_claim_required_capabilities: list[str] = field(default_factory=lambda: ["ci.test.run"])
    worker_poll_after_seconds: int = 15
    worker_heartbeat_interval_seconds: int = 30
    worker_runner: str = "noop"
    worker_execution_backend: str = "wsl_docker"
    worker_workspace_root: str = r"C:\ZetherionCI\workspaces"
    worker_runtime_root: str = r"C:\ZetherionCI\agent-runtime"
    worker_docker_backend: str = "wsl_docker"
    worker_wsl_distribution: str = "Ubuntu"
    worker_cleanup_enabled: bool = True
    worker_cleanup_low_disk_free_bytes: int = 21_474_836_480
    worker_cleanup_target_free_bytes: int = 42_949_672_960
    worker_cleanup_artifact_retention_hours: int = 24
    worker_cleanup_log_retention_days: int = 7
    worker_allowed_repo_roots: list[str] = field(default_factory=list)
    worker_denied_repo_roots: list[str] = field(default_factory=lambda: [r"C:\ZetherionAI"])
    worker_allowed_actions: list[str] = field(
        default_factory=lambda: [
            "worker.noop",
            "ci.test.run",
            "repo.patch",
            "repo.commit",
            "repo.pr.open",
        ]
    )
    worker_allowed_commands: list[str] = field(
        default_factory=lambda: [
            "git",
            "python",
            "python3",
            "pytest",
            "ruff",
            "bash",
            "sh",
            "node",
            "yarn",
            "npm",
            "npx",
            "gitleaks",
            "docker",
            "docker-compose",
            "pwsh",
            "powershell",
        ]
    )
    worker_max_runtime_seconds: int = 600
    worker_max_memory_mb: int = 512
    worker_max_artifact_bytes: int = 1_048_576
    worker_log_dir: str = str(WORKER_LOG_DIR)

    @classmethod
    def load(cls) -> AgentConfig:
        """Load config from ~/.zetherion-dev-agent/config.toml."""
        base = cls()
        if not CONFIG_FILE.exists():
            return cls._apply_env_overrides(base)
        with CONFIG_FILE.open("rb") as f:
            data = tomllib.load(f)
        loaded = cls(
            webhook_url=data.get("webhook_url", ""),
            agent_name=data.get("agent_name", "zetherion-dev-agent"),
            repos=data.get("repos", []),
            scan_interval=data.get("scan_interval", 60),
            claude_code_enabled=data.get("claude_code_enabled", True),
            annotations_enabled=data.get("annotations_enabled", True),
            git_enabled=data.get("git_enabled", True),
            container_monitor_enabled=data.get("container_monitor_enabled", True),
            cleanup_enabled=data.get("cleanup_enabled", True),
            cleanup_hour=data.get("cleanup_hour", 2),
            cleanup_minute=data.get("cleanup_minute", 30),
            approval_reprompt_hours=data.get("approval_reprompt_hours", 24),
            api_host=data.get("api_host", "127.0.0.1"),
            api_port=data.get("api_port", 8787),
            api_token=data.get("api_token", ""),
            api_tls_cert_path=data.get("api_tls_cert_path", ""),
            api_tls_key_path=data.get("api_tls_key_path", ""),
            internal_tls_ca_path=data.get("internal_tls_ca_path", ""),
            api_require_client_cert=data.get("api_require_client_cert", True),
            database_path=data.get("database_path", str(DATABASE_FILE)),
            bootstrap_secret=data.get("bootstrap_secret", ""),
            bootstrap_require_once=data.get("bootstrap_require_once", True),
            worker_base_url=data.get("worker_base_url", "http://127.0.0.1:8000/worker/v1"),
            worker_relay_base_url=data.get("worker_relay_base_url", ""),
            worker_relay_secret=data.get("worker_relay_secret", ""),
            worker_control_plane=data.get("worker_control_plane", "tenant"),
            worker_scope_id=data.get("worker_scope_id", ""),
            worker_tenant_id=data.get("worker_tenant_id", ""),
            worker_node_id=data.get("worker_node_id", ""),
            worker_node_name=data.get("worker_node_name", ""),
            worker_bootstrap_secret=data.get("worker_bootstrap_secret", ""),
            worker_capabilities=data.get(
                "worker_capabilities",
                ["ci.test.run", "repo.patch", "repo.commit", "repo.pr.open"],
            ),
            worker_claim_required_capabilities=data.get(
                "worker_claim_required_capabilities",
                ["ci.test.run"],
            ),
            worker_poll_after_seconds=data.get("worker_poll_after_seconds", 15),
            worker_heartbeat_interval_seconds=data.get("worker_heartbeat_interval_seconds", 30),
            worker_runner=data.get("worker_runner", "noop"),
            worker_execution_backend=data.get(
                "worker_execution_backend",
                "wsl_docker",
            ),
            worker_workspace_root=data.get(
                "worker_workspace_root",
                r"C:\ZetherionCI\workspaces",
            ),
            worker_runtime_root=data.get(
                "worker_runtime_root",
                r"C:\ZetherionCI\agent-runtime",
            ),
            worker_docker_backend=data.get(
                "worker_docker_backend",
                "wsl_docker",
            ),
            worker_wsl_distribution=data.get(
                "worker_wsl_distribution",
                "Ubuntu",
            ),
            worker_cleanup_enabled=data.get("worker_cleanup_enabled", True),
            worker_cleanup_low_disk_free_bytes=data.get(
                "worker_cleanup_low_disk_free_bytes",
                21_474_836_480,
            ),
            worker_cleanup_target_free_bytes=data.get(
                "worker_cleanup_target_free_bytes",
                42_949_672_960,
            ),
            worker_cleanup_artifact_retention_hours=data.get(
                "worker_cleanup_artifact_retention_hours",
                24,
            ),
            worker_cleanup_log_retention_days=data.get(
                "worker_cleanup_log_retention_days",
                7,
            ),
            worker_allowed_repo_roots=data.get("worker_allowed_repo_roots", []),
            worker_denied_repo_roots=data.get("worker_denied_repo_roots", [r"C:\ZetherionAI"]),
            worker_allowed_actions=data.get(
                "worker_allowed_actions",
                ["worker.noop", "ci.test.run", "repo.patch", "repo.commit", "repo.pr.open"],
            ),
            worker_allowed_commands=data.get(
                "worker_allowed_commands",
                [
                    "git",
                    "python",
                    "python3",
                    "pytest",
                    "ruff",
                    "bash",
                    "sh",
                    "node",
                    "yarn",
                    "npm",
                    "npx",
                    "gitleaks",
                    "docker",
                    "docker-compose",
                    "pwsh",
                    "powershell",
                ],
            ),
            worker_max_runtime_seconds=data.get("worker_max_runtime_seconds", 600),
            worker_max_memory_mb=data.get("worker_max_memory_mb", 512),
            worker_max_artifact_bytes=data.get("worker_max_artifact_bytes", 1_048_576),
            worker_log_dir=data.get("worker_log_dir", str(WORKER_LOG_DIR)),
        )
        return cls._apply_env_overrides(loaded)

    @classmethod
    def _apply_env_overrides(cls, cfg: AgentConfig) -> AgentConfig:
        cfg.webhook_url = os.environ.get("DEV_AGENT_WEBHOOK_URL", cfg.webhook_url)
        cfg.agent_name = os.environ.get("DEV_AGENT_NAME", cfg.agent_name)
        cfg.repos = _env_list("DEV_AGENT_REPOS", cfg.repos)
        cfg.scan_interval = _env_int("DEV_AGENT_SCAN_INTERVAL", cfg.scan_interval)
        cfg.claude_code_enabled = _env_bool(
            "DEV_AGENT_CLAUDE_CODE_ENABLED", cfg.claude_code_enabled
        )
        cfg.annotations_enabled = _env_bool(
            "DEV_AGENT_ANNOTATIONS_ENABLED", cfg.annotations_enabled
        )
        cfg.git_enabled = _env_bool("DEV_AGENT_GIT_ENABLED", cfg.git_enabled)
        cfg.container_monitor_enabled = _env_bool(
            "DEV_AGENT_CONTAINER_MONITOR_ENABLED", cfg.container_monitor_enabled
        )
        cfg.cleanup_enabled = _env_bool("DEV_AGENT_CLEANUP_ENABLED", cfg.cleanup_enabled)
        cfg.cleanup_hour = _env_int("DEV_AGENT_CLEANUP_HOUR", cfg.cleanup_hour)
        cfg.cleanup_minute = _env_int("DEV_AGENT_CLEANUP_MINUTE", cfg.cleanup_minute)
        cfg.approval_reprompt_hours = _env_int(
            "DEV_AGENT_APPROVAL_REPROMPT_HOURS", cfg.approval_reprompt_hours
        )
        cfg.api_host = os.environ.get("DEV_AGENT_API_HOST", cfg.api_host)
        cfg.api_port = _env_int("DEV_AGENT_API_PORT", cfg.api_port)
        cfg.api_token = os.environ.get("DEV_AGENT_API_TOKEN", cfg.api_token)
        cfg.api_tls_cert_path = os.environ.get(
            "DEV_AGENT_API_TLS_CERT_PATH",
            cfg.api_tls_cert_path,
        )
        cfg.api_tls_key_path = os.environ.get(
            "DEV_AGENT_API_TLS_KEY_PATH",
            cfg.api_tls_key_path,
        )
        cfg.internal_tls_ca_path = os.environ.get(
            "DEV_AGENT_INTERNAL_TLS_CA_PATH",
            cfg.internal_tls_ca_path,
        )
        cfg.api_require_client_cert = _env_bool(
            "DEV_AGENT_API_REQUIRE_CLIENT_CERT",
            cfg.api_require_client_cert,
        )
        cfg.database_path = os.environ.get("DEV_AGENT_DATABASE_PATH", cfg.database_path)
        cfg.bootstrap_secret = os.environ.get("DEV_AGENT_BOOTSTRAP_SECRET", cfg.bootstrap_secret)
        cfg.bootstrap_require_once = _env_bool(
            "DEV_AGENT_BOOTSTRAP_REQUIRE_ONCE", cfg.bootstrap_require_once
        )
        cfg.worker_base_url = os.environ.get("DEV_AGENT_WORKER_BASE_URL", cfg.worker_base_url)
        cfg.worker_relay_base_url = os.environ.get(
            "DEV_AGENT_WORKER_RELAY_BASE_URL",
            cfg.worker_relay_base_url,
        )
        cfg.worker_relay_secret = os.environ.get(
            "DEV_AGENT_WORKER_RELAY_SECRET",
            cfg.worker_relay_secret,
        )
        cfg.worker_control_plane = os.environ.get(
            "DEV_AGENT_WORKER_CONTROL_PLANE",
            cfg.worker_control_plane,
        )
        cfg.worker_scope_id = os.environ.get("DEV_AGENT_WORKER_SCOPE_ID", cfg.worker_scope_id)
        cfg.worker_tenant_id = os.environ.get("DEV_AGENT_WORKER_TENANT_ID", cfg.worker_tenant_id)
        cfg.worker_node_id = os.environ.get("DEV_AGENT_WORKER_NODE_ID", cfg.worker_node_id)
        cfg.worker_node_name = os.environ.get("DEV_AGENT_WORKER_NODE_NAME", cfg.worker_node_name)
        cfg.worker_bootstrap_secret = os.environ.get(
            "DEV_AGENT_WORKER_BOOTSTRAP_SECRET",
            cfg.worker_bootstrap_secret,
        )
        cfg.worker_capabilities = _env_list(
            "DEV_AGENT_WORKER_CAPABILITIES", cfg.worker_capabilities
        )
        cfg.worker_claim_required_capabilities = _env_list(
            "DEV_AGENT_WORKER_CLAIM_REQUIRED_CAPABILITIES",
            cfg.worker_claim_required_capabilities,
        )
        cfg.worker_poll_after_seconds = _env_int(
            "DEV_AGENT_WORKER_POLL_AFTER_SECONDS",
            cfg.worker_poll_after_seconds,
        )
        cfg.worker_heartbeat_interval_seconds = _env_int(
            "DEV_AGENT_WORKER_HEARTBEAT_INTERVAL_SECONDS",
            cfg.worker_heartbeat_interval_seconds,
        )
        cfg.worker_runner = os.environ.get("DEV_AGENT_WORKER_RUNNER", cfg.worker_runner)
        cfg.worker_execution_backend = os.environ.get(
            "DEV_AGENT_WORKER_EXECUTION_BACKEND",
            cfg.worker_execution_backend,
        )
        cfg.worker_workspace_root = os.environ.get(
            "DEV_AGENT_WORKER_WORKSPACE_ROOT",
            cfg.worker_workspace_root,
        )
        cfg.worker_runtime_root = os.environ.get(
            "DEV_AGENT_WORKER_RUNTIME_ROOT",
            cfg.worker_runtime_root,
        )
        cfg.worker_docker_backend = os.environ.get(
            "DEV_AGENT_WORKER_DOCKER_BACKEND",
            cfg.worker_docker_backend,
        )
        cfg.worker_wsl_distribution = os.environ.get(
            "DEV_AGENT_WORKER_WSL_DISTRIBUTION",
            cfg.worker_wsl_distribution,
        )
        cfg.worker_cleanup_enabled = _env_bool(
            "DEV_AGENT_WORKER_CLEANUP_ENABLED",
            cfg.worker_cleanup_enabled,
        )
        cfg.worker_cleanup_low_disk_free_bytes = _env_int(
            "DEV_AGENT_WORKER_CLEANUP_LOW_DISK_FREE_BYTES",
            cfg.worker_cleanup_low_disk_free_bytes,
        )
        cfg.worker_cleanup_target_free_bytes = _env_int(
            "DEV_AGENT_WORKER_CLEANUP_TARGET_FREE_BYTES",
            cfg.worker_cleanup_target_free_bytes,
        )
        cfg.worker_cleanup_artifact_retention_hours = _env_int(
            "DEV_AGENT_WORKER_CLEANUP_ARTIFACT_RETENTION_HOURS",
            cfg.worker_cleanup_artifact_retention_hours,
        )
        cfg.worker_cleanup_log_retention_days = _env_int(
            "DEV_AGENT_WORKER_CLEANUP_LOG_RETENTION_DAYS",
            cfg.worker_cleanup_log_retention_days,
        )
        cfg.worker_allowed_repo_roots = _env_list(
            "DEV_AGENT_WORKER_ALLOWED_REPO_ROOTS",
            cfg.worker_allowed_repo_roots,
        )
        cfg.worker_denied_repo_roots = _env_list(
            "DEV_AGENT_WORKER_DENIED_REPO_ROOTS",
            cfg.worker_denied_repo_roots,
        )
        cfg.worker_allowed_actions = _env_list(
            "DEV_AGENT_WORKER_ALLOWED_ACTIONS",
            cfg.worker_allowed_actions,
        )
        cfg.worker_allowed_commands = _env_list(
            "DEV_AGENT_WORKER_ALLOWED_COMMANDS",
            cfg.worker_allowed_commands,
        )
        cfg.worker_max_runtime_seconds = _env_int(
            "DEV_AGENT_WORKER_MAX_RUNTIME_SECONDS",
            cfg.worker_max_runtime_seconds,
        )
        cfg.worker_max_memory_mb = _env_int(
            "DEV_AGENT_WORKER_MAX_MEMORY_MB",
            cfg.worker_max_memory_mb,
        )
        cfg.worker_max_artifact_bytes = _env_int(
            "DEV_AGENT_WORKER_MAX_ARTIFACT_BYTES",
            cfg.worker_max_artifact_bytes,
        )
        cfg.worker_log_dir = os.environ.get("DEV_AGENT_WORKER_LOG_DIR", cfg.worker_log_dir)
        return cfg

    def save(self) -> None:
        """Save config to disk."""
        self.ensure_api_token()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        lines = [
            f"webhook_url = {_toml_basic_string(self.webhook_url)}",
            f"agent_name = {_toml_basic_string(self.agent_name)}",
            f"repos = {_toml_string_array(self.repos)}",
            f"scan_interval = {self.scan_interval}",
            f"claude_code_enabled = {'true' if self.claude_code_enabled else 'false'}",
            f"annotations_enabled = {'true' if self.annotations_enabled else 'false'}",
            f"git_enabled = {'true' if self.git_enabled else 'false'}",
            f"container_monitor_enabled = {'true' if self.container_monitor_enabled else 'false'}",
            f"cleanup_enabled = {'true' if self.cleanup_enabled else 'false'}",
            f"cleanup_hour = {self.cleanup_hour}",
            f"cleanup_minute = {self.cleanup_minute}",
            f"approval_reprompt_hours = {self.approval_reprompt_hours}",
            f"api_host = {_toml_basic_string(self.api_host)}",
            f"api_port = {self.api_port}",
            f"api_token = {_toml_basic_string(self.api_token)}",
            f"api_tls_cert_path = {_toml_basic_string(self.api_tls_cert_path)}",
            f"api_tls_key_path = {_toml_basic_string(self.api_tls_key_path)}",
            f"internal_tls_ca_path = {_toml_basic_string(self.internal_tls_ca_path)}",
            f"api_require_client_cert = {'true' if self.api_require_client_cert else 'false'}",
            f"database_path = {_toml_basic_string(self.database_path)}",
            f"bootstrap_secret = {_toml_basic_string(self.bootstrap_secret)}",
            f"bootstrap_require_once = {'true' if self.bootstrap_require_once else 'false'}",
            f"worker_base_url = {_toml_basic_string(self.worker_base_url)}",
            f"worker_relay_base_url = {_toml_basic_string(self.worker_relay_base_url)}",
            f"worker_relay_secret = {_toml_basic_string(self.worker_relay_secret)}",
            f"worker_control_plane = {_toml_basic_string(self.worker_control_plane)}",
            f"worker_scope_id = {_toml_basic_string(self.worker_scope_id)}",
            f"worker_tenant_id = {_toml_basic_string(self.worker_tenant_id)}",
            f"worker_node_id = {_toml_basic_string(self.worker_node_id)}",
            f"worker_node_name = {_toml_basic_string(self.worker_node_name)}",
            f"worker_bootstrap_secret = {_toml_basic_string(self.worker_bootstrap_secret)}",
            f"worker_capabilities = {_toml_string_array(self.worker_capabilities)}",
            "worker_claim_required_capabilities = "
            f"{_toml_string_array(self.worker_claim_required_capabilities)}",
            f"worker_poll_after_seconds = {self.worker_poll_after_seconds}",
            ("worker_heartbeat_interval_seconds = " f"{self.worker_heartbeat_interval_seconds}"),
            f"worker_runner = {_toml_basic_string(self.worker_runner)}",
            f"worker_execution_backend = {_toml_basic_string(self.worker_execution_backend)}",
            f"worker_workspace_root = {_toml_basic_string(self.worker_workspace_root)}",
            f"worker_runtime_root = {_toml_basic_string(self.worker_runtime_root)}",
            f"worker_docker_backend = {_toml_basic_string(self.worker_docker_backend)}",
            f"worker_wsl_distribution = {_toml_basic_string(self.worker_wsl_distribution)}",
            f"worker_cleanup_enabled = {'true' if self.worker_cleanup_enabled else 'false'}",
            ("worker_cleanup_low_disk_free_bytes = " f"{self.worker_cleanup_low_disk_free_bytes}"),
            ("worker_cleanup_target_free_bytes = " f"{self.worker_cleanup_target_free_bytes}"),
            (
                "worker_cleanup_artifact_retention_hours = "
                f"{self.worker_cleanup_artifact_retention_hours}"
            ),
            f"worker_cleanup_log_retention_days = {self.worker_cleanup_log_retention_days}",
            f"worker_allowed_repo_roots = {_toml_string_array(self.worker_allowed_repo_roots)}",
            f"worker_denied_repo_roots = {_toml_string_array(self.worker_denied_repo_roots)}",
            f"worker_allowed_actions = {_toml_string_array(self.worker_allowed_actions)}",
            f"worker_allowed_commands = {_toml_string_array(self.worker_allowed_commands)}",
            f"worker_max_runtime_seconds = {self.worker_max_runtime_seconds}",
            f"worker_max_memory_mb = {self.worker_max_memory_mb}",
            f"worker_max_artifact_bytes = {self.worker_max_artifact_bytes}",
            f"worker_log_dir = {_toml_basic_string(self.worker_log_dir)}",
        ]
        CONFIG_FILE.write_text("\n".join(lines) + "\n")

    def ensure_api_token(self) -> bool:
        """Ensure a local API token exists.

        Returns True when a new token was created.
        """
        if self.api_token:
            return False
        self.api_token = secrets.token_urlsafe(32)
        return True
