"""Docker project discovery and safe cleanup helpers for dev autopilot."""

from __future__ import annotations

import json
import re
import subprocess  # nosec B404
from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ContainerSnapshot:
    """Snapshot of a single container relevant to cleanup decisions."""

    container_id: str
    name: str
    image: str
    state: str
    status: str
    project_id: str
    labels: dict[str, str]


@dataclass(frozen=True)
class ProjectSnapshot:
    """Aggregated container state for a Docker project."""

    project_id: str
    containers: list[ContainerSnapshot]

    @property
    def total_containers(self) -> int:
        return len(self.containers)

    @property
    def running_containers(self) -> int:
        return sum(1 for c in self.containers if c.state.lower() == "running")


@dataclass
class CleanupAction:
    """Single cleanup step planned or executed."""

    action_type: str
    target: str
    command: list[str]
    executed: bool = False
    success: bool = True
    output: str = ""
    error: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action_type": self.action_type,
            "target": self.target,
            "command": self.command,
            "executed": self.executed,
            "success": self.success,
            "output": self.output,
            "error": self.error,
        }


@dataclass
class CleanupResult:
    """Cleanup outcome for one project."""

    project_id: str
    dry_run: bool
    actions: list[CleanupAction] = field(default_factory=list)
    success: bool = True
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "project_id": self.project_id,
            "dry_run": self.dry_run,
            "actions": [action.to_dict() for action in self.actions],
            "success": self.success,
            "error": self.error,
        }


class DockerMonitor:
    """Discovers Docker projects and performs bounded cleanup actions."""

    PROJECT_LABEL = "com.docker.compose.project"

    def discover_projects(self) -> dict[str, ProjectSnapshot]:
        """Return current projects keyed by project_id."""
        containers = self._list_containers()
        projects: dict[str, list[ContainerSnapshot]] = {}
        for container in containers:
            projects.setdefault(container.project_id, []).append(container)
        return {
            project_id: ProjectSnapshot(project_id=project_id, containers=members)
            for project_id, members in projects.items()
        }

    def plan_cleanup(
        self,
        project: ProjectSnapshot,
        *,
        exited_older_than_hours: int = 24,
    ) -> list[CleanupAction]:
        """Plan safe cleanup actions for a single project."""
        actions: list[CleanupAction] = []

        removable_containers = [
            container
            for container in project.containers
            if self._is_container_removable(
                container,
                exited_older_than_hours=exited_older_than_hours,
            )
        ]
        if removable_containers:
            ids = [container.container_id for container in removable_containers]
            names = ", ".join(container.name for container in removable_containers)
            actions.append(
                CleanupAction(
                    action_type="remove_containers",
                    target=names,
                    command=["docker", "rm", *ids],
                )
            )

        network_lines = self._list_project_networks(project.project_id)
        for network_id, network_name in network_lines:
            actions.append(
                CleanupAction(
                    action_type="remove_network",
                    target=network_name,
                    command=["docker", "network", "rm", network_id],
                )
            )
        return actions

    def run_cleanup(
        self,
        project: ProjectSnapshot,
        *,
        dry_run: bool,
        exited_older_than_hours: int = 24,
    ) -> CleanupResult:
        """Execute planned cleanup actions for a project."""
        result = CleanupResult(project_id=project.project_id, dry_run=dry_run)
        result.actions = self.plan_cleanup(project, exited_older_than_hours=exited_older_than_hours)
        if dry_run:
            return result
        for action in result.actions:
            action.executed = True
            code, stdout, stderr = self._run_cmd(action.command)
            action.success = code == 0
            action.output = stdout.strip()
            action.error = stderr.strip()
            if not action.success:
                result.success = False
                if result.error is None:
                    result.error = action.error or f"Failed command: {' '.join(action.command)}"
        return result

    # ------------------------------------------------------------------
    # Container discovery
    # ------------------------------------------------------------------

    def _list_containers(self) -> list[ContainerSnapshot]:
        code, stdout, _stderr = self._run_cmd(
            ["docker", "ps", "-a", "--format", "{{json .}}"],
            timeout=30,
        )
        if code != 0:
            return []

        containers: list[ContainerSnapshot] = []
        for line in stdout.splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                data = json.loads(raw)
            except json.JSONDecodeError:
                continue
            labels = _parse_labels(str(data.get("Labels", "")))
            name = str(data.get("Names") or "")
            project_id = labels.get(self.PROJECT_LABEL) or _infer_project_id_from_name(name)
            if not project_id:
                project_id = "unknown"
            containers.append(
                ContainerSnapshot(
                    container_id=str(data.get("ID") or ""),
                    name=name,
                    image=str(data.get("Image") or ""),
                    state=str(data.get("State") or ""),
                    status=str(data.get("Status") or ""),
                    project_id=project_id,
                    labels=labels,
                )
            )
        return containers

    def _list_project_networks(self, project_id: str) -> list[tuple[str, str]]:
        code, stdout, _stderr = self._run_cmd(
            [
                "docker",
                "network",
                "ls",
                "--filter",
                f"label={self.PROJECT_LABEL}={project_id}",
                "--format",
                "{{.ID}} {{.Name}}",
            ],
            timeout=20,
        )
        if code != 0:
            return []
        networks: list[tuple[str, str]] = []
        for line in stdout.splitlines():
            stripped = line.strip()
            if not stripped:
                continue
            parts = stripped.split(maxsplit=1)
            if len(parts) != 2:
                continue
            networks.append((parts[0], parts[1]))
        return networks

    # ------------------------------------------------------------------
    # Safety checks
    # ------------------------------------------------------------------

    def _is_container_removable(
        self,
        container: ContainerSnapshot,
        *,
        exited_older_than_hours: int,
    ) -> bool:
        state = container.state.lower()
        if state == "running":
            return False
        if state == "dead":
            return True
        if state not in {"exited", "created"} and "exited" not in container.status.lower():
            return False

        age_hours = _status_age_hours(container.status)
        if age_hours is None:
            return False
        return age_hours >= max(1, exited_older_than_hours)

    # ------------------------------------------------------------------
    # Command execution
    # ------------------------------------------------------------------

    @staticmethod
    def _run_cmd(args: list[str], *, timeout: int = 60) -> tuple[int, str, str]:
        try:
            proc = subprocess.run(  # nosec B603
                args,
                capture_output=True,
                text=True,
                timeout=timeout,
                check=False,
            )
            return proc.returncode, proc.stdout, proc.stderr
        except Exception as exc:
            return 1, "", str(exc)


def _parse_labels(raw: str) -> dict[str, str]:
    labels: dict[str, str] = {}
    for pair in raw.split(","):
        item = pair.strip()
        if not item or "=" not in item:
            continue
        key, value = item.split("=", 1)
        labels[key.strip()] = value.strip()
    return labels


def _infer_project_id_from_name(name: str) -> str:
    """Best-effort project id inference when compose labels are missing."""
    if "_" in name:
        return name.split("_", 1)[0]
    if "-" in name:
        parts = name.split("-")
        # Prefer preserving service names that often end with numeric suffix.
        if len(parts) >= 3 and parts[-1].isdigit():
            return "-".join(parts[:-2])
        return parts[0]
    return name


_STATUS_AGE_PATTERN = re.compile(r"(\d+)\s+(second|minute|hour|day|week|month|year)s?\s+ago")
_STATUS_LESS_THAN_SECOND = re.compile(r"less than a second ago", re.IGNORECASE)


def _status_age_hours(status: str) -> float | None:
    """Parse Docker status string into approximate hours."""
    lowered = status.lower()
    if _STATUS_LESS_THAN_SECOND.search(lowered):
        return 0.0
    match = _STATUS_AGE_PATTERN.search(lowered)
    if not match:
        return None
    value = float(match.group(1))
    unit = match.group(2)
    if unit == "second":
        return value / 3600.0
    if unit == "minute":
        return value / 60.0
    if unit == "hour":
        return value
    if unit == "day":
        return value * 24.0
    if unit == "week":
        return value * 24.0 * 7.0
    if unit == "month":
        return value * 24.0 * 30.0
    if unit == "year":
        return value * 24.0 * 365.0
    return None
