"""GitHub Management Skill package.

Comprehensive GitHub management via natural language. Supports issues,
PRs, security alerts, wiki, projects, releases, and CI/Actions monitoring.
"""

from zetherion_ai.skills.github.client import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubClient,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
)
from zetherion_ai.skills.github.models import (
    ActionType,
    AutonomyConfig,
    AutonomyLevel,
    GitHubEvent,
    GitHubEventType,
    Issue,
    IssueState,
    Label,
    PRState,
    PullRequest,
    Repository,
    User,
    WorkflowRun,
    WorkflowStatus,
)
from zetherion_ai.skills.github.skill import GitHubSkill

__all__ = [
    # Skill
    "GitHubSkill",
    # Client
    "GitHubClient",
    "GitHubAPIError",
    "GitHubAuthError",
    "GitHubNotFoundError",
    "GitHubRateLimitError",
    "GitHubValidationError",
    # Enums
    "ActionType",
    "AutonomyLevel",
    "GitHubEventType",
    "IssueState",
    "PRState",
    "WorkflowStatus",
    # Models
    "AutonomyConfig",
    "GitHubEvent",
    "Issue",
    "Label",
    "PullRequest",
    "Repository",
    "User",
    "WorkflowRun",
]
