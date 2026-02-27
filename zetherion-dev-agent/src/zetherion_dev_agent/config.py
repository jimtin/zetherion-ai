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
    database_path: str = str(DATABASE_FILE)
    bootstrap_secret: str = ""
    bootstrap_require_once: bool = True

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
            database_path=data.get("database_path", str(DATABASE_FILE)),
            bootstrap_secret=data.get("bootstrap_secret", ""),
            bootstrap_require_once=data.get("bootstrap_require_once", True),
        )
        return cls._apply_env_overrides(loaded)

    @classmethod
    def _apply_env_overrides(cls, cfg: AgentConfig) -> AgentConfig:
        cfg.webhook_url = os.environ.get("DEV_AGENT_WEBHOOK_URL", cfg.webhook_url)
        cfg.agent_name = os.environ.get("DEV_AGENT_NAME", cfg.agent_name)
        cfg.repos = _env_list("DEV_AGENT_REPOS", cfg.repos)
        cfg.scan_interval = _env_int("DEV_AGENT_SCAN_INTERVAL", cfg.scan_interval)
        cfg.claude_code_enabled = _env_bool("DEV_AGENT_CLAUDE_CODE_ENABLED", cfg.claude_code_enabled)
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
        cfg.database_path = os.environ.get("DEV_AGENT_DATABASE_PATH", cfg.database_path)
        cfg.bootstrap_secret = os.environ.get("DEV_AGENT_BOOTSTRAP_SECRET", cfg.bootstrap_secret)
        cfg.bootstrap_require_once = _env_bool(
            "DEV_AGENT_BOOTSTRAP_REQUIRE_ONCE", cfg.bootstrap_require_once
        )
        return cfg

    def save(self) -> None:
        """Save config to disk."""
        self.ensure_api_token()
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        lines = [
            f'webhook_url = "{self.webhook_url}"',
            f'agent_name = "{self.agent_name}"',
            f"repos = {self.repos!r}",
            f"scan_interval = {self.scan_interval}",
            f"claude_code_enabled = {'true' if self.claude_code_enabled else 'false'}",
            f"annotations_enabled = {'true' if self.annotations_enabled else 'false'}",
            f"git_enabled = {'true' if self.git_enabled else 'false'}",
            f"container_monitor_enabled = {'true' if self.container_monitor_enabled else 'false'}",
            f"cleanup_enabled = {'true' if self.cleanup_enabled else 'false'}",
            f"cleanup_hour = {self.cleanup_hour}",
            f"cleanup_minute = {self.cleanup_minute}",
            f"approval_reprompt_hours = {self.approval_reprompt_hours}",
            f'api_host = "{self.api_host}"',
            f"api_port = {self.api_port}",
            f'api_token = "{self.api_token}"',
            f'database_path = "{self.database_path}"',
            f'bootstrap_secret = "{self.bootstrap_secret}"',
            f"bootstrap_require_once = {'true' if self.bootstrap_require_once else 'false'}",
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
