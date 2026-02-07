"""GitHub Management Skill implementation.

Provides comprehensive GitHub management via natural language,
supporting issues, PRs, and workflows with configurable autonomy.
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.base import (
    HeartbeatAction,
    Skill,
    SkillMetadata,
    SkillRequest,
    SkillResponse,
    SkillStatus,
)
from zetherion_ai.skills.github.client import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubClient,
    GitHubNotFoundError,
)
from zetherion_ai.skills.github.models import (
    ActionType,
    AutonomyConfig,
    AutonomyLevel,
    GitHubEvent,
    GitHubEventType,
)
from zetherion_ai.skills.permissions import Permission, PermissionSet

if TYPE_CHECKING:
    from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.skills.github.skill")


# Intent mapping
INTENT_HANDLERS = {
    # Issue intents
    "list_issues": "handle_list_issues",
    "get_issue": "handle_get_issue",
    "create_issue": "handle_create_issue",
    "update_issue": "handle_update_issue",
    "close_issue": "handle_close_issue",
    "reopen_issue": "handle_reopen_issue",
    "add_label": "handle_add_label",
    "remove_label": "handle_remove_label",
    "add_comment": "handle_add_comment",
    # PR intents
    "list_prs": "handle_list_prs",
    "get_pr": "handle_get_pr",
    "get_pr_diff": "handle_get_pr_diff",
    "merge_pr": "handle_merge_pr",
    # Workflow intents
    "list_workflows": "handle_list_workflows",
    "rerun_workflow": "handle_rerun_workflow",
    # Repository intents
    "get_repo_info": "handle_get_repo_info",
    # Autonomy intents
    "set_autonomy": "handle_set_autonomy",
    "get_autonomy": "handle_get_autonomy",
}


@dataclass
class PendingAction:
    """An action waiting for user confirmation."""

    action_type: ActionType
    description: str
    execute_fn: str  # Method name to call
    kwargs: dict[str, Any] = field(default_factory=dict)
    created_at: datetime = field(default_factory=datetime.now)
    expires_at: datetime | None = None


class GitHubSkill(Skill):
    """GitHub Management Skill.

    Provides comprehensive GitHub management via natural language,
    supporting issues, PRs, workflows, and more with configurable
    per-action autonomy levels.
    """

    COLLECTION_CONFIG = "skill_github_config"
    COLLECTION_AUDIT = "skill_github_audit"

    def __init__(
        self,
        memory: "QdrantMemory | None" = None,
        github_token: str | None = None,
        default_repo: str | None = None,
    ):
        """Initialize the GitHub skill.

        Args:
            memory: Optional Qdrant memory for storage.
            github_token: GitHub personal access token.
            default_repo: Default repository (owner/repo format).
        """
        super().__init__(memory)
        self._github_token = github_token
        self._default_repo = default_repo
        self._client: GitHubClient | None = None
        self._autonomy_config = AutonomyConfig()
        self._pending_actions: dict[str, dict[UUID, PendingAction]] = {}  # user_id -> actions
        self._event_handlers: list[Any] = []  # Event listeners

    @property
    def metadata(self) -> SkillMetadata:
        """Return skill metadata."""
        return SkillMetadata(
            name="github_management",
            description="Manage GitHub repositories via natural language",
            version="1.0.0",
            author="Zetherion AI",
            permissions=PermissionSet(
                {
                    Permission.READ_PROFILE,
                    Permission.WRITE_MEMORIES,
                    Permission.SEND_MESSAGES,
                }
            ),
            collections=[self.COLLECTION_CONFIG, self.COLLECTION_AUDIT],
            intents=list(INTENT_HANDLERS.keys()),
        )

    def _parse_repo(self, repo_str: str | None) -> tuple[str, str]:
        """Parse owner/repo string into (owner, repo) tuple.

        Args:
            repo_str: Repository string in owner/repo format.

        Returns:
            Tuple of (owner, repo).

        Raises:
            ValueError: If repo_str is invalid.
        """
        repo = repo_str or self._default_repo
        if not repo:
            raise ValueError("No repository specified and no default set")
        parts = repo.split("/")
        if len(parts) != 2:
            raise ValueError(f"Invalid repository format: {repo}. Expected owner/repo")
        return parts[0], parts[1]

    async def initialize(self) -> bool:
        """Initialize the skill.

        Creates the GitHub client and verifies authentication.

        Returns:
            True if initialization succeeded.
        """
        if not self._github_token:
            log.error("github_skill_init_failed", reason="No GitHub token provided")
            self._set_status(SkillStatus.ERROR, "No GitHub token provided")
            return False

        self._client = GitHubClient(self._github_token)

        try:
            valid = await self._client.verify_token()
            if not valid:
                log.error("github_skill_init_failed", reason="Invalid GitHub token")
                self._set_status(SkillStatus.ERROR, "Invalid GitHub token")
                return False

            user = await self._client.get_authenticated_user()
            log.info("github_skill_initialized", user=user.login)

            # Load autonomy config from memory if available
            if self._memory:
                await self._load_autonomy_config()

            self._set_status(SkillStatus.READY)
            return True

        except Exception as e:
            log.error("github_skill_init_failed", error=str(e))
            self._set_status(SkillStatus.ERROR, str(e))
            return False

    async def _load_autonomy_config(self) -> None:
        """Load autonomy configuration from memory."""
        if not self._memory:
            return

        try:
            # This would load from Qdrant collection
            # For now, use defaults
            pass
        except Exception as e:
            log.warning("failed_to_load_autonomy_config", error=str(e))

    async def _save_autonomy_config(self) -> None:
        """Save autonomy configuration to memory."""
        if not self._memory:
            return

        try:
            # This would save to Qdrant collection
            pass
        except Exception as e:
            log.warning("failed_to_save_autonomy_config", error=str(e))

    async def _emit_event(self, event: GitHubEvent) -> None:
        """Emit an event to registered handlers."""
        for handler in self._event_handlers:
            try:
                await handler(event)
            except Exception as e:
                log.error("event_handler_failed", event_type=event.event_type.value, error=str(e))

    def register_event_handler(self, handler: Any) -> None:
        """Register an event handler."""
        self._event_handlers.append(handler)

    async def _check_autonomy(
        self,
        action: ActionType,
        user_id: str,
        description: str,
        execute_fn: str,
        kwargs: dict[str, Any],
    ) -> tuple[bool, str | None]:
        """Check if an action can proceed autonomously.

        Args:
            action: The action type.
            user_id: The user requesting the action.
            description: Human-readable description.
            execute_fn: Method name to call if approved.
            kwargs: Arguments for the method.

        Returns:
            Tuple of (can_proceed, pending_action_id).
            If can_proceed is False, pending_action_id is the ID to confirm.
        """
        if self._autonomy_config.is_autonomous(action):
            return True, None

        # Create pending action
        from uuid import uuid4

        action_id = uuid4()
        pending = PendingAction(
            action_type=action,
            description=description,
            execute_fn=execute_fn,
            kwargs=kwargs,
        )

        if user_id not in self._pending_actions:
            self._pending_actions[user_id] = {}
        self._pending_actions[user_id][action_id] = pending

        return False, str(action_id)

    async def confirm_action(self, user_id: str, action_id: str) -> SkillResponse:
        """Confirm and execute a pending action.

        Args:
            user_id: The user confirming.
            action_id: The pending action ID.

        Returns:
            Response from executing the action.
        """
        if user_id not in self._pending_actions:
            return SkillResponse(
                request_id=UUID(action_id),
                success=False,
                error="No pending actions found",
            )

        try:
            pending = self._pending_actions[user_id].pop(UUID(action_id))
        except (KeyError, ValueError):
            return SkillResponse(
                request_id=UUID(action_id),
                success=False,
                error="Action not found or already executed",
            )

        # Execute the action
        handler = getattr(self, pending.execute_fn, None)
        if not handler:
            return SkillResponse(
                request_id=UUID(action_id),
                success=False,
                error=f"Handler not found: {pending.execute_fn}",
            )

        result: SkillResponse = await handler(**pending.kwargs)
        return result

    def cancel_action(self, user_id: str, action_id: str) -> bool:
        """Cancel a pending action.

        Args:
            user_id: The user canceling.
            action_id: The pending action ID.

        Returns:
            True if canceled, False if not found.
        """
        if user_id not in self._pending_actions:
            return False
        try:
            self._pending_actions[user_id].pop(UUID(action_id))
            return True
        except (KeyError, ValueError):
            return False

    async def handle(self, request: SkillRequest) -> SkillResponse:
        """Handle a skill request.

        Args:
            request: The incoming request.

        Returns:
            Response with results or error.
        """
        intent = request.intent
        handler_name = INTENT_HANDLERS.get(intent)

        if not handler_name:
            return SkillResponse.error_response(
                request.id,
                f"Unknown intent: {intent}. Available: {list(INTENT_HANDLERS.keys())}",
            )

        handler = getattr(self, handler_name, None)
        if not handler:
            return SkillResponse.error_response(
                request.id,
                f"Handler not implemented: {handler_name}",
            )

        try:
            result: SkillResponse = await handler(request)
            return result
        except GitHubAuthError:
            return SkillResponse.error_response(request.id, "GitHub authentication failed")
        except GitHubNotFoundError:
            return SkillResponse.error_response(request.id, "Resource not found")
        except GitHubAPIError as e:
            return SkillResponse.error_response(request.id, f"GitHub API error: {e}")
        except ValueError as e:
            return SkillResponse.error_response(request.id, str(e))

    # ========== Issue Handlers ==========

    async def handle_list_issues(self, request: SkillRequest) -> SkillResponse:
        """List issues for a repository."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))

        state = context.get("state", "open")
        labels = context.get("labels")
        assignee = context.get("assignee")

        issues = await self._client.list_issues(
            owner=owner,
            repo=repo,
            state=state,
            labels=labels,
            assignee=assignee,
        )

        if not issues:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message=f"No {state} issues found in {owner}/{repo}",
                data={"issues": []},
            )

        summaries = [issue.format_summary() for issue in issues]
        message = f"Found {len(issues)} {state} issue(s) in {owner}/{repo}:\n\n"
        message += "\n\n".join(summaries)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=message,
            data={"issues": [i.to_dict() for i in issues]},
        )

    async def handle_get_issue(self, request: SkillRequest) -> SkillResponse:
        """Get a specific issue."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        issue_number = context.get("issue_number")

        if not issue_number:
            return SkillResponse.error_response(request.id, "Issue number required")

        issue = await self._client.get_issue(owner, repo, int(issue_number))

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=issue.format_summary(),
            data={"issue": issue.to_dict()},
        )

    async def handle_create_issue(self, request: SkillRequest) -> SkillResponse:
        """Create a new issue."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        title = context.get("title")
        body = context.get("body", "")
        labels = context.get("labels")
        assignees = context.get("assignees")

        if not title:
            return SkillResponse.error_response(request.id, "Issue title required")

        # Check autonomy
        can_proceed, pending_id = await self._check_autonomy(
            ActionType.CREATE_ISSUE,
            request.user_id,
            f"Create issue '{title}' in {owner}/{repo}",
            "_execute_create_issue",
            {
                "request_id": request.id,
                "owner": owner,
                "repo": repo,
                "title": title,
                "body": body,
                "labels": labels,
                "assignees": assignees,
            },
        )

        if not can_proceed:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message=f"Action requires confirmation.\n"
                f"Creating issue '{title}' in {owner}/{repo}\n"
                f"Confirm with action ID: {pending_id}",
                data={"pending_action_id": pending_id, "requires_confirmation": True},
            )

        return await self._execute_create_issue(
            request_id=request.id,
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            labels=labels,
            assignees=assignees,
        )

    async def _execute_create_issue(
        self,
        request_id: UUID,
        owner: str,
        repo: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
    ) -> SkillResponse:
        """Execute issue creation."""
        if not self._client:
            return SkillResponse.error_response(request_id, "GitHub client not initialized")

        issue = await self._client.create_issue(
            owner=owner,
            repo=repo,
            title=title,
            body=body,
            labels=labels,
            assignees=assignees,
        )

        # Emit event
        await self._emit_event(
            GitHubEvent(
                event_type=GitHubEventType.ISSUE_CREATED,
                repository=f"{owner}/{repo}",
                data={"issue": issue.to_dict()},
            )
        )

        return SkillResponse(
            request_id=request_id,
            success=True,
            message=f"Created issue #{issue.number}: '{issue.title}'\n{issue.html_url}",
            data={"issue": issue.to_dict()},
        )

    async def handle_update_issue(self, request: SkillRequest) -> SkillResponse:
        """Update an existing issue."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        issue_number = context.get("issue_number")

        if not issue_number:
            return SkillResponse.error_response(request.id, "Issue number required")

        # Check autonomy
        can_proceed, pending_id = await self._check_autonomy(
            ActionType.UPDATE_ISSUE,
            request.user_id,
            f"Update issue #{issue_number} in {owner}/{repo}",
            "_execute_update_issue",
            {
                "request_id": request.id,
                "owner": owner,
                "repo": repo,
                "issue_number": int(issue_number),
                "context": context,
            },
        )

        if not can_proceed:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message=f"Action requires confirmation.\n"
                f"Updating issue #{issue_number} in {owner}/{repo}\n"
                f"Confirm with action ID: {pending_id}",
                data={"pending_action_id": pending_id, "requires_confirmation": True},
            )

        return await self._execute_update_issue(
            request_id=request.id,
            owner=owner,
            repo=repo,
            issue_number=int(issue_number),
            context=context,
        )

    async def _execute_update_issue(
        self,
        request_id: UUID,
        owner: str,
        repo: str,
        issue_number: int,
        context: dict[str, Any],
    ) -> SkillResponse:
        """Execute issue update."""
        if not self._client:
            return SkillResponse.error_response(request_id, "GitHub client not initialized")

        issue = await self._client.update_issue(
            owner=owner,
            repo=repo,
            issue_number=issue_number,
            title=context.get("title"),
            body=context.get("body"),
            labels=context.get("labels"),
            assignees=context.get("assignees"),
        )

        await self._emit_event(
            GitHubEvent(
                event_type=GitHubEventType.ISSUE_UPDATED,
                repository=f"{owner}/{repo}",
                data={"issue": issue.to_dict()},
            )
        )

        return SkillResponse(
            request_id=request_id,
            success=True,
            message=f"Updated issue #{issue.number}\n{issue.html_url}",
            data={"issue": issue.to_dict()},
        )

    async def handle_close_issue(self, request: SkillRequest) -> SkillResponse:
        """Close an issue."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        issue_number = context.get("issue_number")

        if not issue_number:
            return SkillResponse.error_response(request.id, "Issue number required")

        # Check autonomy
        can_proceed, pending_id = await self._check_autonomy(
            ActionType.CLOSE_ISSUE,
            request.user_id,
            f"Close issue #{issue_number} in {owner}/{repo}",
            "_execute_close_issue",
            {
                "request_id": request.id,
                "owner": owner,
                "repo": repo,
                "issue_number": int(issue_number),
            },
        )

        if not can_proceed:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message=f"Action requires confirmation.\n"
                f"Closing issue #{issue_number} in {owner}/{repo}\n"
                f"Confirm with action ID: {pending_id}",
                data={"pending_action_id": pending_id, "requires_confirmation": True},
            )

        return await self._execute_close_issue(
            request_id=request.id,
            owner=owner,
            repo=repo,
            issue_number=int(issue_number),
        )

    async def _execute_close_issue(
        self,
        request_id: UUID,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> SkillResponse:
        """Execute issue close."""
        if not self._client:
            return SkillResponse.error_response(request_id, "GitHub client not initialized")

        issue = await self._client.close_issue(owner, repo, issue_number)

        await self._emit_event(
            GitHubEvent(
                event_type=GitHubEventType.ISSUE_CLOSED,
                repository=f"{owner}/{repo}",
                data={"issue": issue.to_dict()},
            )
        )

        return SkillResponse(
            request_id=request_id,
            success=True,
            message=f"Closed issue #{issue.number}\n{issue.html_url}",
            data={"issue": issue.to_dict()},
        )

    async def handle_reopen_issue(self, request: SkillRequest) -> SkillResponse:
        """Reopen an issue."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        issue_number = context.get("issue_number")

        if not issue_number:
            return SkillResponse.error_response(request.id, "Issue number required")

        # Check autonomy
        can_proceed, pending_id = await self._check_autonomy(
            ActionType.REOPEN_ISSUE,
            request.user_id,
            f"Reopen issue #{issue_number} in {owner}/{repo}",
            "_execute_reopen_issue",
            {
                "request_id": request.id,
                "owner": owner,
                "repo": repo,
                "issue_number": int(issue_number),
            },
        )

        if not can_proceed:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message=f"Action requires confirmation.\n"
                f"Reopening issue #{issue_number} in {owner}/{repo}\n"
                f"Confirm with action ID: {pending_id}",
                data={"pending_action_id": pending_id, "requires_confirmation": True},
            )

        return await self._execute_reopen_issue(
            request_id=request.id,
            owner=owner,
            repo=repo,
            issue_number=int(issue_number),
        )

    async def _execute_reopen_issue(
        self,
        request_id: UUID,
        owner: str,
        repo: str,
        issue_number: int,
    ) -> SkillResponse:
        """Execute issue reopen."""
        if not self._client:
            return SkillResponse.error_response(request_id, "GitHub client not initialized")

        issue = await self._client.reopen_issue(owner, repo, issue_number)

        await self._emit_event(
            GitHubEvent(
                event_type=GitHubEventType.ISSUE_REOPENED,
                repository=f"{owner}/{repo}",
                data={"issue": issue.to_dict()},
            )
        )

        return SkillResponse(
            request_id=request_id,
            success=True,
            message=f"Reopened issue #{issue.number}\n{issue.html_url}",
            data={"issue": issue.to_dict()},
        )

    async def handle_add_label(self, request: SkillRequest) -> SkillResponse:
        """Add labels to an issue."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        issue_number = context.get("issue_number")
        labels = context.get("labels", [])

        if not issue_number:
            return SkillResponse.error_response(request.id, "Issue number required")
        if not labels:
            return SkillResponse.error_response(request.id, "Labels required")

        # Labels are autonomous by default
        result_labels = await self._client.add_labels(owner, repo, int(issue_number), labels)

        await self._emit_event(
            GitHubEvent(
                event_type=GitHubEventType.ISSUE_LABELED,
                repository=f"{owner}/{repo}",
                data={"issue_number": issue_number, "labels_added": labels},
            )
        )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Added labels to issue #{issue_number}: {', '.join(labels)}",
            data={"labels": [lbl.to_dict() for lbl in result_labels]},
        )

    async def handle_remove_label(self, request: SkillRequest) -> SkillResponse:
        """Remove a label from an issue."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        issue_number = context.get("issue_number")
        label = context.get("label")

        if not issue_number:
            return SkillResponse.error_response(request.id, "Issue number required")
        if not label:
            return SkillResponse.error_response(request.id, "Label required")

        await self._client.remove_label(owner, repo, int(issue_number), label)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Removed label '{label}' from issue #{issue_number}",
        )

    async def handle_add_comment(self, request: SkillRequest) -> SkillResponse:
        """Add a comment to an issue or PR."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        issue_number = context.get("issue_number")
        body = context.get("body", "")

        if not issue_number:
            return SkillResponse.error_response(request.id, "Issue/PR number required")
        if not body:
            return SkillResponse.error_response(request.id, "Comment body required")

        # Comments are autonomous by default
        comment = await self._client.add_comment(owner, repo, int(issue_number), body)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Added comment to #{issue_number}\n{comment.get('html_url', '')}",
            data={"comment": comment},
        )

    # ========== PR Handlers ==========

    async def handle_list_prs(self, request: SkillRequest) -> SkillResponse:
        """List pull requests for a repository."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        state = context.get("state", "open")

        prs = await self._client.list_pull_requests(owner=owner, repo=repo, state=state)

        if not prs:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message=f"No {state} pull requests found in {owner}/{repo}",
                data={"pull_requests": []},
            )

        summaries = [pr.format_summary() for pr in prs]
        message = f"Found {len(prs)} {state} PR(s) in {owner}/{repo}:\n\n"
        message += "\n\n".join(summaries)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=message,
            data={"pull_requests": [pr.to_dict() for pr in prs]},
        )

    async def handle_get_pr(self, request: SkillRequest) -> SkillResponse:
        """Get a specific pull request."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        pr_number = context.get("pr_number")

        if not pr_number:
            return SkillResponse.error_response(request.id, "PR number required")

        pr = await self._client.get_pull_request(owner, repo, int(pr_number))

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=pr.format_summary(),
            data={"pull_request": pr.to_dict()},
        )

    async def handle_get_pr_diff(self, request: SkillRequest) -> SkillResponse:
        """Get the diff for a pull request."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        pr_number = context.get("pr_number")

        if not pr_number:
            return SkillResponse.error_response(request.id, "PR number required")

        diff = await self._client.get_pr_diff(owner, repo, int(pr_number))

        # Truncate if too long
        max_len = 10000
        truncated = len(diff) > max_len
        if truncated:
            diff = diff[:max_len] + f"\n\n... (truncated, {len(diff)} total chars)"

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Diff for PR #{pr_number}:\n```diff\n{diff}\n```",
            data={"diff": diff, "truncated": truncated},
        )

    async def handle_merge_pr(self, request: SkillRequest) -> SkillResponse:
        """Merge a pull request."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        pr_number = context.get("pr_number")
        merge_method = context.get("merge_method", "merge")

        if not pr_number:
            return SkillResponse.error_response(request.id, "PR number required")

        # Check autonomy - merge is high-risk
        can_proceed, pending_id = await self._check_autonomy(
            ActionType.MERGE_PR,
            request.user_id,
            f"Merge PR #{pr_number} in {owner}/{repo}",
            "_execute_merge_pr",
            {
                "request_id": request.id,
                "owner": owner,
                "repo": repo,
                "pr_number": int(pr_number),
                "merge_method": merge_method,
            },
        )

        if not can_proceed:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message=f"Action requires confirmation.\n"
                f"Merging PR #{pr_number} in {owner}/{repo} using {merge_method}\n"
                f"Confirm with action ID: {pending_id}",
                data={"pending_action_id": pending_id, "requires_confirmation": True},
            )

        return await self._execute_merge_pr(
            request_id=request.id,
            owner=owner,
            repo=repo,
            pr_number=int(pr_number),
            merge_method=merge_method,
        )

    async def _execute_merge_pr(
        self,
        request_id: UUID,
        owner: str,
        repo: str,
        pr_number: int,
        merge_method: str = "merge",
    ) -> SkillResponse:
        """Execute PR merge."""
        if not self._client:
            return SkillResponse.error_response(request_id, "GitHub client not initialized")

        result = await self._client.merge_pull_request(
            owner, repo, pr_number, merge_method=merge_method
        )

        await self._emit_event(
            GitHubEvent(
                event_type=GitHubEventType.PR_MERGED,
                repository=f"{owner}/{repo}",
                data={"pr_number": pr_number, "merge_method": merge_method},
            )
        )

        return SkillResponse(
            request_id=request_id,
            success=True,
            message=f"Merged PR #{pr_number}: {result.get('message', 'Success')}",
            data={"merge_result": result},
        )

    # ========== Workflow Handlers ==========

    async def handle_list_workflows(self, request: SkillRequest) -> SkillResponse:
        """List workflow runs."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        branch = context.get("branch")
        status = context.get("status")

        runs = await self._client.list_workflow_runs(
            owner=owner, repo=repo, branch=branch, status=status
        )

        if not runs:
            return SkillResponse(
                request_id=request.id,
                success=True,
                message=f"No workflow runs found in {owner}/{repo}",
                data={"workflow_runs": []},
            )

        summaries = [run.format_summary() for run in runs[:10]]
        message = f"Found {len(runs)} workflow run(s) in {owner}/{repo}:\n\n"
        message += "\n\n".join(summaries)

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=message,
            data={"workflow_runs": [r.to_dict() for r in runs]},
        )

    async def handle_rerun_workflow(self, request: SkillRequest) -> SkillResponse:
        """Re-run a workflow."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))
        run_id = context.get("run_id")

        if not run_id:
            return SkillResponse.error_response(request.id, "Workflow run ID required")

        await self._client.rerun_workflow(owner, repo, int(run_id))

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Re-running workflow {run_id} in {owner}/{repo}",
        )

    # ========== Repository Handlers ==========

    async def handle_get_repo_info(self, request: SkillRequest) -> SkillResponse:
        """Get repository information."""
        if not self._client:
            return SkillResponse.error_response(request.id, "GitHub client not initialized")

        context = request.context
        owner, repo = self._parse_repo(context.get("repository"))

        repository = await self._client.get_repository(owner, repo)

        info = (
            f"**{repository.full_name}**\n"
            f"{repository.description or 'No description'}\n\n"
            f"Default branch: {repository.default_branch}\n"
            f"Open issues: {repository.open_issues_count}\n"
            f"Stars: {repository.stargazers_count}\n"
            f"Forks: {repository.forks_count}\n"
            f"Private: {'Yes' if repository.private else 'No'}"
        )

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=info,
            data={"repository": repository.to_dict()},
        )

    # ========== Autonomy Handlers ==========

    async def handle_set_autonomy(self, request: SkillRequest) -> SkillResponse:
        """Set autonomy level for an action."""
        context = request.context
        action_str = context.get("action")
        level_str = context.get("level")

        if not action_str or not level_str:
            return SkillResponse.error_response(request.id, "Action and level required")

        try:
            action = ActionType(action_str)
            level = AutonomyLevel(level_str)
        except ValueError as e:
            return SkillResponse.error_response(request.id, str(e))

        success = self._autonomy_config.set_level(action, level)

        if not success:
            return SkillResponse.error_response(
                request.id,
                f"Cannot set {action.value} to {level.value} - action requires confirmation",
            )

        await self._save_autonomy_config()

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=f"Set {action.value} autonomy to {level.value}",
        )

    async def handle_get_autonomy(self, request: SkillRequest) -> SkillResponse:
        """Get current autonomy configuration."""
        config = self._autonomy_config.to_dict()

        # Group by level
        autonomous = [k for k, v in config.items() if v == "autonomous"]
        ask = [k for k, v in config.items() if v == "ask"]
        always_ask = [k for k, v in config.items() if v == "always_ask"]

        message = "**Autonomy Configuration:**\n\n"
        message += f"**Autonomous** (no confirmation): {', '.join(autonomous) or 'none'}\n\n"
        message += f"**Ask** (requires confirmation): {', '.join(ask) or 'none'}\n\n"
        message += f"**Always Ask** (cannot override): {', '.join(always_ask)}"

        return SkillResponse(
            request_id=request.id,
            success=True,
            message=message,
            data={"autonomy": config},
        )

    # ========== Heartbeat ==========

    async def on_heartbeat(self, user_ids: list[str]) -> list[HeartbeatAction]:
        """Check for proactive actions during heartbeat.

        Currently monitors for:
        - Stale issues (no activity in 7+ days)
        - Failed workflow runs
        """
        # TODO: Implement stale issue detection and workflow monitoring
        return []

    def get_system_prompt_fragment(self, user_id: str) -> str | None:
        """Return context for the agent's system prompt."""
        if self._status != SkillStatus.READY:
            return None

        pending_count = len(self._pending_actions.get(user_id, {}))
        if pending_count > 0:
            return f"[GitHub: {pending_count} action(s) pending confirmation]"

        return "[GitHub: Ready]"

    async def cleanup(self) -> None:
        """Clean up resources."""
        if self._client:
            await self._client.close()
            self._client = None
