"""Data models for GitHub Management Skill.

Defines dataclasses and enums for issues, PRs, repositories, workflows,
and the autonomy configuration system.
"""

import contextlib
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class IssueState(Enum):
    """State of a GitHub issue."""

    OPEN = "open"
    CLOSED = "closed"


class PRState(Enum):
    """State of a GitHub pull request."""

    OPEN = "open"
    CLOSED = "closed"
    MERGED = "merged"


class WorkflowStatus(Enum):
    """Status of a GitHub Actions workflow run."""

    QUEUED = "queued"
    IN_PROGRESS = "in_progress"
    COMPLETED = "completed"
    WAITING = "waiting"
    REQUESTED = "requested"
    PENDING = "pending"


class WorkflowConclusion(Enum):
    """Conclusion of a completed workflow run."""

    SUCCESS = "success"
    FAILURE = "failure"
    CANCELLED = "cancelled"
    SKIPPED = "skipped"
    TIMED_OUT = "timed_out"
    ACTION_REQUIRED = "action_required"
    NEUTRAL = "neutral"
    STALE = "stale"


class AutonomyLevel(Enum):
    """Autonomy level for GitHub actions.

    - AUTONOMOUS: Execute without asking
    - ASK: Always ask for confirmation
    - ALWAYS_ASK: Cannot be overridden to autonomous (safety)
    """

    AUTONOMOUS = "autonomous"
    ASK = "ask"
    ALWAYS_ASK = "always_ask"


class ActionType(Enum):
    """Types of actions that can be taken on GitHub."""

    # Read-only (always autonomous)
    LIST_ISSUES = "list_issues"
    LIST_PRS = "list_prs"
    GET_ISSUE = "get_issue"
    GET_PR = "get_pr"
    GET_PR_DIFF = "get_pr_diff"
    CHECK_WORKFLOW_STATUS = "check_workflow_status"
    LIST_LABELS = "list_labels"
    LIST_MILESTONES = "list_milestones"
    GET_REPO_INFO = "get_repo_info"

    # Low-risk mutations (configurable)
    ADD_LABEL = "add_label"
    REMOVE_LABEL = "remove_label"
    ADD_COMMENT = "add_comment"
    ASSIGN_ISSUE = "assign_issue"
    UNASSIGN_ISSUE = "unassign_issue"
    REQUEST_REVIEW = "request_review"
    ADD_REACTION = "add_reaction"

    # High-risk mutations (default to ask)
    CREATE_ISSUE = "create_issue"
    UPDATE_ISSUE = "update_issue"
    CLOSE_ISSUE = "close_issue"
    REOPEN_ISSUE = "reopen_issue"
    CREATE_PR = "create_pr"
    MERGE_PR = "merge_pr"
    CLOSE_PR = "close_pr"
    CREATE_RELEASE = "create_release"
    DELETE_BRANCH = "delete_branch"
    CREATE_LABEL = "create_label"
    DELETE_LABEL = "delete_label"

    # Dangerous (always ask, cannot override)
    FORCE_PUSH = "force_push"
    DELETE_REPO = "delete_repo"
    TRANSFER_REPO = "transfer_repo"
    UPDATE_BRANCH_PROTECTION = "update_branch_protection"


# Default autonomy settings
DEFAULT_AUTONOMY: dict[ActionType, AutonomyLevel] = {
    # Read-only: always autonomous
    ActionType.LIST_ISSUES: AutonomyLevel.AUTONOMOUS,
    ActionType.LIST_PRS: AutonomyLevel.AUTONOMOUS,
    ActionType.GET_ISSUE: AutonomyLevel.AUTONOMOUS,
    ActionType.GET_PR: AutonomyLevel.AUTONOMOUS,
    ActionType.GET_PR_DIFF: AutonomyLevel.AUTONOMOUS,
    ActionType.CHECK_WORKFLOW_STATUS: AutonomyLevel.AUTONOMOUS,
    ActionType.LIST_LABELS: AutonomyLevel.AUTONOMOUS,
    ActionType.LIST_MILESTONES: AutonomyLevel.AUTONOMOUS,
    ActionType.GET_REPO_INFO: AutonomyLevel.AUTONOMOUS,
    # Low-risk: autonomous by default
    ActionType.ADD_LABEL: AutonomyLevel.AUTONOMOUS,
    ActionType.REMOVE_LABEL: AutonomyLevel.AUTONOMOUS,
    ActionType.ADD_COMMENT: AutonomyLevel.AUTONOMOUS,
    ActionType.ASSIGN_ISSUE: AutonomyLevel.AUTONOMOUS,
    ActionType.UNASSIGN_ISSUE: AutonomyLevel.AUTONOMOUS,
    ActionType.REQUEST_REVIEW: AutonomyLevel.ASK,
    ActionType.ADD_REACTION: AutonomyLevel.AUTONOMOUS,
    # High-risk: ask by default
    ActionType.CREATE_ISSUE: AutonomyLevel.ASK,
    ActionType.UPDATE_ISSUE: AutonomyLevel.ASK,
    ActionType.CLOSE_ISSUE: AutonomyLevel.ASK,
    ActionType.REOPEN_ISSUE: AutonomyLevel.ASK,
    ActionType.CREATE_PR: AutonomyLevel.ASK,
    ActionType.MERGE_PR: AutonomyLevel.ASK,
    ActionType.CLOSE_PR: AutonomyLevel.ASK,
    ActionType.CREATE_RELEASE: AutonomyLevel.ASK,
    ActionType.DELETE_BRANCH: AutonomyLevel.ASK,
    ActionType.CREATE_LABEL: AutonomyLevel.ASK,
    ActionType.DELETE_LABEL: AutonomyLevel.ASK,
    # Dangerous: always ask (cannot override)
    ActionType.FORCE_PUSH: AutonomyLevel.ALWAYS_ASK,
    ActionType.DELETE_REPO: AutonomyLevel.ALWAYS_ASK,
    ActionType.TRANSFER_REPO: AutonomyLevel.ALWAYS_ASK,
    ActionType.UPDATE_BRANCH_PROTECTION: AutonomyLevel.ALWAYS_ASK,
}

# Actions that can never be set to autonomous
ALWAYS_ASK_ACTIONS: set[ActionType] = {
    ActionType.FORCE_PUSH,
    ActionType.DELETE_REPO,
    ActionType.TRANSFER_REPO,
    ActionType.UPDATE_BRANCH_PROTECTION,
}


class GitHubEventType(Enum):
    """Types of events emitted by the GitHub skill."""

    # Issue events
    ISSUE_CREATED = "issue_created"
    ISSUE_UPDATED = "issue_updated"
    ISSUE_CLOSED = "issue_closed"
    ISSUE_REOPENED = "issue_reopened"
    ISSUE_LABELED = "issue_labeled"
    DUPLICATE_DETECTED = "duplicate_detected"

    # PR events
    PR_CREATED = "pr_created"
    PR_REVIEWED = "pr_reviewed"
    PR_APPROVED = "pr_approved"
    PR_MERGED = "pr_merged"
    PR_CLOSED = "pr_closed"
    REVIEW_COMMENT_POSTED = "review_comment_posted"

    # Security events
    VULNERABILITY_DETECTED = "vulnerability_detected"
    SECURITY_PR_CREATED = "security_pr_created"
    ADVISORY_PUBLISHED = "advisory_published"

    # Wiki events
    WIKI_UPDATED = "wiki_updated"
    DOCS_SYNCED = "docs_synced"

    # Project events
    PROJECT_ITEM_MOVED = "project_item_moved"
    RELEASE_CREATED = "release_created"
    MILESTONE_COMPLETED = "milestone_completed"

    # CI events
    WORKFLOW_FAILED = "workflow_failed"
    WORKFLOW_FIXED = "workflow_fixed"
    CI_REPORT_GENERATED = "ci_report_generated"


@dataclass
class User:
    """GitHub user."""

    login: str
    id: int
    avatar_url: str = ""
    html_url: str = ""
    name: str | None = None
    email: str | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "User":
        """Create from GitHub API response."""
        return cls(
            login=data["login"],
            id=data["id"],
            avatar_url=data.get("avatar_url", ""),
            html_url=data.get("html_url", ""),
            name=data.get("name"),
            email=data.get("email"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "login": self.login,
            "id": self.id,
            "avatar_url": self.avatar_url,
            "html_url": self.html_url,
            "name": self.name,
            "email": self.email,
        }


@dataclass
class Label:
    """GitHub label."""

    name: str
    color: str = "ededed"
    description: str = ""
    id: int | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Label":
        """Create from GitHub API response."""
        return cls(
            name=data["name"],
            color=data.get("color", "ededed"),
            description=data.get("description", "") or "",
            id=data.get("id"),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "name": self.name,
            "color": self.color,
            "description": self.description,
            "id": self.id,
        }


@dataclass
class Repository:
    """GitHub repository."""

    owner: str
    name: str
    full_name: str = ""
    description: str = ""
    html_url: str = ""
    default_branch: str = "main"
    private: bool = False
    fork: bool = False
    archived: bool = False
    open_issues_count: int = 0
    stargazers_count: int = 0
    forks_count: int = 0

    def __post_init__(self) -> None:
        """Set full_name if not provided."""
        if not self.full_name:
            self.full_name = f"{self.owner}/{self.name}"

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Repository":
        """Create from GitHub API response."""
        owner = data.get("owner", {})
        return cls(
            owner=owner.get("login", "") if isinstance(owner, dict) else str(owner),
            name=data["name"],
            full_name=data.get("full_name", ""),
            description=data.get("description", "") or "",
            html_url=data.get("html_url", ""),
            default_branch=data.get("default_branch", "main"),
            private=data.get("private", False),
            fork=data.get("fork", False),
            archived=data.get("archived", False),
            open_issues_count=data.get("open_issues_count", 0),
            stargazers_count=data.get("stargazers_count", 0),
            forks_count=data.get("forks_count", 0),
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "owner": self.owner,
            "name": self.name,
            "full_name": self.full_name,
            "description": self.description,
            "html_url": self.html_url,
            "default_branch": self.default_branch,
            "private": self.private,
            "fork": self.fork,
            "archived": self.archived,
            "open_issues_count": self.open_issues_count,
            "stargazers_count": self.stargazers_count,
            "forks_count": self.forks_count,
        }


@dataclass
class Issue:
    """GitHub issue."""

    number: int
    title: str
    body: str = ""
    state: IssueState = IssueState.OPEN
    html_url: str = ""
    user: User | None = None
    assignees: list[User] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    milestone: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    comments: int = 0
    repository: str = ""  # owner/repo format

    @classmethod
    def from_api(cls, data: dict[str, Any], repository: str = "") -> "Issue":
        """Create from GitHub API response."""
        user_data = data.get("user")
        user = User.from_api(user_data) if user_data else None

        assignees = [User.from_api(a) for a in data.get("assignees", [])]
        labels = [Label.from_api(lbl) for lbl in data.get("labels", [])]

        milestone = None
        if data.get("milestone"):
            milestone = data["milestone"].get("title")

        def parse_dt(val: str | None) -> datetime | None:
            if not val:
                return None
            return datetime.fromisoformat(val.replace("Z", "+00:00"))

        # Derive repository from URL if not provided
        if not repository and data.get("html_url"):
            # https://github.com/owner/repo/issues/123
            parts = data["html_url"].split("/")
            if len(parts) >= 5:
                repository = f"{parts[3]}/{parts[4]}"

        return cls(
            number=data["number"],
            title=data["title"],
            body=data.get("body", "") or "",
            state=IssueState(data.get("state", "open")),
            html_url=data.get("html_url", ""),
            user=user,
            assignees=assignees,
            labels=labels,
            milestone=milestone,
            created_at=parse_dt(data.get("created_at")),
            updated_at=parse_dt(data.get("updated_at")),
            closed_at=parse_dt(data.get("closed_at")),
            comments=data.get("comments", 0),
            repository=repository,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "state": self.state.value,
            "html_url": self.html_url,
            "user": self.user.to_dict() if self.user else None,
            "assignees": [a.to_dict() for a in self.assignees],
            "labels": [lbl.to_dict() for lbl in self.labels],
            "milestone": self.milestone,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "comments": self.comments,
            "repository": self.repository,
        }

    def format_summary(self) -> str:
        """Format a short summary for display."""
        labels_str = ", ".join(lbl.name for lbl in self.labels) if self.labels else "none"
        assignees_str = (
            ", ".join(f"@{a.login}" for a in self.assignees) if self.assignees else "unassigned"
        )
        return (
            f"#{self.number} '{self.title}'\n"
            f"  State: {self.state.value} | Labels: {labels_str}\n"
            f"  Assignees: {assignees_str} | Comments: {self.comments}"
        )


@dataclass
class PullRequest:
    """GitHub pull request."""

    number: int
    title: str
    body: str = ""
    state: PRState = PRState.OPEN
    html_url: str = ""
    user: User | None = None
    assignees: list[User] = field(default_factory=list)
    labels: list[Label] = field(default_factory=list)
    milestone: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None
    closed_at: datetime | None = None
    merged_at: datetime | None = None
    head_ref: str = ""
    base_ref: str = ""
    draft: bool = False
    mergeable: bool | None = None
    mergeable_state: str = ""
    additions: int = 0
    deletions: int = 0
    changed_files: int = 0
    commits: int = 0
    comments: int = 0
    review_comments: int = 0
    repository: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any], repository: str = "") -> "PullRequest":
        """Create from GitHub API response."""
        user_data = data.get("user")
        user = User.from_api(user_data) if user_data else None

        assignees = [User.from_api(a) for a in data.get("assignees", [])]
        labels = [Label.from_api(lbl) for lbl in data.get("labels", [])]

        milestone = None
        if data.get("milestone"):
            milestone = data["milestone"].get("title")

        def parse_dt(val: str | None) -> datetime | None:
            if not val:
                return None
            return datetime.fromisoformat(val.replace("Z", "+00:00"))

        # Determine state
        state = PRState.OPEN
        if data.get("merged_at"):
            state = PRState.MERGED
        elif data.get("state") == "closed":
            state = PRState.CLOSED

        # Get branch refs
        head = data.get("head", {})
        base = data.get("base", {})

        # Derive repository from URL if not provided
        if not repository and data.get("html_url"):
            parts = data["html_url"].split("/")
            if len(parts) >= 5:
                repository = f"{parts[3]}/{parts[4]}"

        return cls(
            number=data["number"],
            title=data["title"],
            body=data.get("body", "") or "",
            state=state,
            html_url=data.get("html_url", ""),
            user=user,
            assignees=assignees,
            labels=labels,
            milestone=milestone,
            created_at=parse_dt(data.get("created_at")),
            updated_at=parse_dt(data.get("updated_at")),
            closed_at=parse_dt(data.get("closed_at")),
            merged_at=parse_dt(data.get("merged_at")),
            head_ref=head.get("ref", "") if isinstance(head, dict) else "",
            base_ref=base.get("ref", "") if isinstance(base, dict) else "",
            draft=data.get("draft", False),
            mergeable=data.get("mergeable"),
            mergeable_state=data.get("mergeable_state", ""),
            additions=data.get("additions", 0),
            deletions=data.get("deletions", 0),
            changed_files=data.get("changed_files", 0),
            commits=data.get("commits", 0),
            comments=data.get("comments", 0),
            review_comments=data.get("review_comments", 0),
            repository=repository,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "number": self.number,
            "title": self.title,
            "body": self.body,
            "state": self.state.value,
            "html_url": self.html_url,
            "user": self.user.to_dict() if self.user else None,
            "assignees": [a.to_dict() for a in self.assignees],
            "labels": [lbl.to_dict() for lbl in self.labels],
            "milestone": self.milestone,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "closed_at": self.closed_at.isoformat() if self.closed_at else None,
            "merged_at": self.merged_at.isoformat() if self.merged_at else None,
            "head_ref": self.head_ref,
            "base_ref": self.base_ref,
            "draft": self.draft,
            "mergeable": self.mergeable,
            "mergeable_state": self.mergeable_state,
            "additions": self.additions,
            "deletions": self.deletions,
            "changed_files": self.changed_files,
            "commits": self.commits,
            "comments": self.comments,
            "review_comments": self.review_comments,
            "repository": self.repository,
        }

    def format_summary(self) -> str:
        """Format a short summary for display."""
        changes = f"+{self.additions}/-{self.deletions}"
        age = ""
        if self.created_at:
            days = (datetime.now(self.created_at.tzinfo) - self.created_at).days
            age = f"{days} day{'s' if days != 1 else ''} old"

        status_parts = []
        if self.draft:
            status_parts.append("draft")
        if self.mergeable is False:
            status_parts.append("has conflicts")
        if self.state == PRState.MERGED:
            status_parts.append("merged")

        status = ", ".join(status_parts) if status_parts else self.state.value

        return (
            f"#{self.number} '{self.title}'\n"
            f"  {self.head_ref} -> {self.base_ref} | {changes} | {self.changed_files} files\n"
            f"  Status: {status} | {age}"
        )


@dataclass
class WorkflowRun:
    """GitHub Actions workflow run."""

    id: int
    name: str
    workflow_id: int
    head_branch: str = ""
    head_sha: str = ""
    status: WorkflowStatus = WorkflowStatus.QUEUED
    conclusion: WorkflowConclusion | None = None
    html_url: str = ""
    created_at: datetime | None = None
    updated_at: datetime | None = None
    run_started_at: datetime | None = None
    run_attempt: int = 1
    event: str = ""  # push, pull_request, schedule, etc.
    repository: str = ""

    @classmethod
    def from_api(cls, data: dict[str, Any], repository: str = "") -> "WorkflowRun":
        """Create from GitHub API response."""

        def parse_dt(val: str | None) -> datetime | None:
            if not val:
                return None
            return datetime.fromisoformat(val.replace("Z", "+00:00"))

        conclusion = None
        if data.get("conclusion"):
            with contextlib.suppress(ValueError):
                conclusion = WorkflowConclusion(data["conclusion"])

        return cls(
            id=data["id"],
            name=data.get("name", ""),
            workflow_id=data.get("workflow_id", 0),
            head_branch=data.get("head_branch", ""),
            head_sha=data.get("head_sha", "")[:7] if data.get("head_sha") else "",
            status=WorkflowStatus(data.get("status", "queued")),
            conclusion=conclusion,
            html_url=data.get("html_url", ""),
            created_at=parse_dt(data.get("created_at")),
            updated_at=parse_dt(data.get("updated_at")),
            run_started_at=parse_dt(data.get("run_started_at")),
            run_attempt=data.get("run_attempt", 1),
            event=data.get("event", ""),
            repository=repository,
        )

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "id": self.id,
            "name": self.name,
            "workflow_id": self.workflow_id,
            "head_branch": self.head_branch,
            "head_sha": self.head_sha,
            "status": self.status.value,
            "conclusion": self.conclusion.value if self.conclusion else None,
            "html_url": self.html_url,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
            "run_started_at": (self.run_started_at.isoformat() if self.run_started_at else None),
            "run_attempt": self.run_attempt,
            "event": self.event,
            "repository": self.repository,
        }

    def format_summary(self) -> str:
        """Format a short summary for display."""
        status = self.conclusion.value if self.conclusion else self.status.value
        return (
            f"{self.name} (#{self.id})\n"
            f"  Branch: {self.head_branch} ({self.head_sha})\n"
            f"  Status: {status} | Event: {self.event} | Attempt: {self.run_attempt}"
        )


@dataclass
class GitHubEvent:
    """Event emitted by the GitHub skill."""

    event_type: GitHubEventType
    repository: str
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: datetime = field(default_factory=datetime.now)
    user_id: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        return {
            "event_type": self.event_type.value,
            "repository": self.repository,
            "data": self.data,
            "timestamp": self.timestamp.isoformat(),
            "user_id": self.user_id,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GitHubEvent":
        """Create from dictionary."""
        return cls(
            event_type=GitHubEventType(data["event_type"]),
            repository=data["repository"],
            data=data.get("data", {}),
            timestamp=datetime.fromisoformat(data["timestamp"])
            if data.get("timestamp")
            else datetime.now(),
            user_id=data.get("user_id", ""),
        )


@dataclass
class AutonomyConfig:
    """Configuration for action autonomy levels."""

    settings: dict[ActionType, AutonomyLevel] = field(
        default_factory=lambda: dict(DEFAULT_AUTONOMY)
    )

    def get_level(self, action: ActionType) -> AutonomyLevel:
        """Get the autonomy level for an action."""
        return self.settings.get(action, AutonomyLevel.ASK)

    def set_level(self, action: ActionType, level: AutonomyLevel) -> bool:
        """Set the autonomy level for an action.

        Returns False if the action cannot be set to the requested level
        (e.g., trying to set ALWAYS_ASK actions to AUTONOMOUS).
        """
        if action in ALWAYS_ASK_ACTIONS and level != AutonomyLevel.ALWAYS_ASK:
            return False
        self.settings[action] = level
        return True

    def is_autonomous(self, action: ActionType) -> bool:
        """Check if an action can be executed autonomously."""
        return self.get_level(action) == AutonomyLevel.AUTONOMOUS

    def requires_confirmation(self, action: ActionType) -> bool:
        """Check if an action requires user confirmation."""
        return self.get_level(action) in (AutonomyLevel.ASK, AutonomyLevel.ALWAYS_ASK)

    def to_dict(self) -> dict[str, str]:
        """Convert to dictionary for storage."""
        return {action.value: level.value for action, level in self.settings.items()}

    @classmethod
    def from_dict(cls, data: dict[str, str]) -> "AutonomyConfig":
        """Create from dictionary."""
        settings = {}
        for action_str, level_str in data.items():
            try:
                action = ActionType(action_str)
                level = AutonomyLevel(level_str)
                settings[action] = level
            except ValueError:
                continue
        return cls(settings=settings if settings else dict(DEFAULT_AUTONOMY))
