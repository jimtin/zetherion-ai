"""Configuration for the dev agent."""

from __future__ import annotations

import tomllib
from dataclasses import dataclass, field
from pathlib import Path

CONFIG_DIR = Path.home() / ".zetherion-dev-agent"
CONFIG_FILE = CONFIG_DIR / "config.toml"
STATE_FILE = CONFIG_DIR / "state.json"


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
        )

    def save(self) -> None:
        """Save config to disk."""
        CONFIG_DIR.mkdir(parents=True, exist_ok=True)
        lines = [
            f'webhook_url = "{self.webhook_url}"',
            f'agent_name = "{self.agent_name}"',
            f"repos = {self.repos!r}",
            f"scan_interval = {self.scan_interval}",
            f"claude_code_enabled = {'true' if self.claude_code_enabled else 'false'}",
            f"annotations_enabled = {'true' if self.annotations_enabled else 'false'}",
            f"git_enabled = {'true' if self.git_enabled else 'false'}",
        ]
        CONFIG_FILE.write_text("\n".join(lines) + "\n")
