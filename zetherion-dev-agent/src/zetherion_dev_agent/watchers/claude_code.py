"""Claude Code session watcher — parses session logs for context."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Claude Code stores session data here
CLAUDE_PROJECTS_DIR = Path.home() / ".claude" / "projects"

# Max characters of session content to send
MAX_SESSION_CONTENT = 2000


@dataclass
class SessionSummary:
    """Summary of a Claude Code session."""

    session_id: str
    project: str
    summary: str
    timestamp: str  # ISO format
    tools_used: int
    messages: int


def find_project_sessions(project_path: str) -> Path | None:
    """Find the Claude Code project directory for a given repo path.

    Claude Code stores sessions under ~/.claude/projects/ with the path
    encoded as dashes (e.g., -Users-james-Documents-MyProject/).
    """
    encoded = project_path.replace("/", "-")
    candidate = CLAUDE_PROJECTS_DIR / encoded
    if candidate.exists():
        return candidate

    # Try with leading dash
    if not encoded.startswith("-"):
        candidate = CLAUDE_PROJECTS_DIR / f"-{encoded}"
        if candidate.exists():
            return candidate

    return None


def get_new_sessions(
    project_path: str,
    since: str | None = None,
) -> list[SessionSummary]:
    """Get new Claude Code sessions for a project since a given timestamp.

    Args:
        project_path: Absolute path to the project.
        since: ISO timestamp to filter sessions after. If None, returns recent sessions.

    Returns:
        List of session summaries.
    """
    project_dir = find_project_sessions(project_path)
    if not project_dir:
        return []

    since_dt = datetime.fromisoformat(since) if since else None
    project_name = Path(project_path).name
    summaries = []

    # Look for session memory summaries
    for summary_file in project_dir.glob("**/session-memory/summary.md"):
        session_id = summary_file.parent.parent.name

        # Check modification time
        mtime = datetime.fromtimestamp(summary_file.stat().st_mtime)
        if since_dt and mtime <= since_dt:
            continue

        content = summary_file.read_text(errors="ignore")[:MAX_SESSION_CONTENT]
        if not content.strip():
            continue

        summaries.append(
            SessionSummary(
                session_id=session_id,
                project=project_name,
                summary=content.strip(),
                timestamp=mtime.isoformat(),
                tools_used=0,
                messages=0,
            )
        )

    # Also check JSONL transcript logs for richer data
    for jsonl_file in project_dir.glob("*.jsonl"):
        session_id = jsonl_file.stem
        mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime)
        if since_dt and mtime <= since_dt:
            continue

        # Check if we already have a summary for this session
        if any(s.session_id == session_id for s in summaries):
            continue

        summary = _parse_jsonl_session(jsonl_file, project_name)
        if summary:
            summaries.append(summary)

    # Sort newest first
    summaries.sort(key=lambda s: s.timestamp, reverse=True)
    return summaries


def _parse_jsonl_session(jsonl_path: Path, project_name: str) -> SessionSummary | None:
    """Parse a JSONL session transcript for a brief summary."""
    messages = 0
    tools_used = 0
    first_user_msg = ""

    try:
        with jsonl_path.open() as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                if entry.get("role") == "user":
                    messages += 1
                    if not first_user_msg and isinstance(entry.get("content"), str):
                        first_user_msg = entry["content"][:200]
                elif entry.get("role") == "assistant":
                    messages += 1
                    # Count tool uses
                    content = entry.get("content", "")
                    if isinstance(content, list):
                        tools_used += sum(
                            1
                            for c in content
                            if isinstance(c, dict) and c.get("type") == "tool_use"
                        )
    except OSError:
        return None

    if messages < 2:
        return None

    mtime = datetime.fromtimestamp(jsonl_path.stat().st_mtime)
    summary_text = first_user_msg or f"Session with {messages} messages"

    return SessionSummary(
        session_id=jsonl_path.stem,
        project=project_name,
        summary=summary_text,
        timestamp=mtime.isoformat(),
        tools_used=tools_used,
        messages=messages,
    )
