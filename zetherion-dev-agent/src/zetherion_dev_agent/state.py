"""Persistent scan state for the dev agent."""

from __future__ import annotations

import json
from dataclasses import dataclass, field

from zetherion_dev_agent.config import STATE_FILE


@dataclass
class ScanState:
    """Tracks what has been scanned so far to avoid duplicate events."""

    # Per-repo last known commit SHA
    last_commit_sha: dict[str, str] = field(default_factory=dict)
    # Per-repo last known annotations (file:line -> content)
    known_annotations: dict[str, dict[str, str]] = field(default_factory=dict)
    # Last processed Claude Code session timestamp (ISO format)
    last_session_time: str = ""
    # Per-repo last known tags
    known_tags: dict[str, list[str]] = field(default_factory=dict)

    @classmethod
    def load(cls) -> ScanState:
        """Load state from disk."""
        if not STATE_FILE.exists():
            return cls()
        try:
            data = json.loads(STATE_FILE.read_text())
            return cls(
                last_commit_sha=data.get("last_commit_sha", {}),
                known_annotations=data.get("known_annotations", {}),
                last_session_time=data.get("last_session_time", ""),
                known_tags=data.get("known_tags", {}),
            )
        except (json.JSONDecodeError, KeyError):
            return cls()

    def save(self) -> None:
        """Persist state to disk."""
        STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        data = {
            "last_commit_sha": self.last_commit_sha,
            "known_annotations": self.known_annotations,
            "last_session_time": self.last_session_time,
            "known_tags": self.known_tags,
        }
        STATE_FILE.write_text(json.dumps(data, indent=2) + "\n")
