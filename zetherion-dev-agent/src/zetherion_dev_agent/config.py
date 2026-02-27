"""Configuration for the dev agent."""

from __future__ import annotations

import secrets
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".zetherion-dev-agent"
CONFIG_FILE = CONFIG_DIR / "config.toml"
STATE_FILE = CONFIG_DIR / "state.json"
DATABASE_FILE = CONFIG_DIR / "daemon.db"


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

    @classmethod
    def load(cls) -> AgentConfig:
        """Load config from ~/.zetherion-dev-agent/config.toml."""
        if not CONFIG_FILE.exists():
            return cls()
        with CONFIG_FILE.open("rb") as f:
            data = tomllib.load(f)
        return cls(
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
        )

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
