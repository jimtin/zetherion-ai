"""Async GitHub API client using httpx.

Provides a thin wrapper around the GitHub REST API with proper
error handling, rate limiting awareness, and type-safe responses.
"""

import contextlib
from dataclasses import dataclass
from typing import Any

import httpx

from zetherion_ai.logging import get_logger
from zetherion_ai.skills.github.models import (
    Issue,
    IssueState,
    Label,
    PullRequest,
    Repository,
    User,
    WorkflowRun,
)

log = get_logger("zetherion_ai.skills.github.client")

GITHUB_API_BASE = "https://api.github.com"
DEFAULT_TIMEOUT = 30.0
DEFAULT_PER_PAGE = 30


class GitHubAPIError(Exception):
    """Base exception for GitHub API errors."""

    def __init__(
        self,
        message: str,
        status_code: int | None = None,
        response: dict[str, Any] | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.response = response or {}


class GitHubAuthError(GitHubAPIError):
    """Authentication failed."""

    pass


class GitHubNotFoundError(GitHubAPIError):
    """Resource not found."""

    pass


class GitHubRateLimitError(GitHubAPIError):
    """Rate limit exceeded."""

    def __init__(
        self,
        message: str,
        reset_at: int | None = None,
        remaining: int = 0,
    ):
        super().__init__(message, status_code=403)
        self.reset_at = reset_at
        self.remaining = remaining


class GitHubValidationError(GitHubAPIError):
    """Validation error (422)."""

    pass


@dataclass
class RateLimitInfo:
    """Rate limit information from GitHub API."""

    limit: int
    remaining: int
    reset_at: int  # Unix timestamp
    used: int


class GitHubClient:
    """Async GitHub API client.

    Uses httpx for async HTTP operations with proper error handling
    and rate limit awareness.
    """

    def __init__(
        self,
        token: str,
        base_url: str = GITHUB_API_BASE,
        timeout: float = DEFAULT_TIMEOUT,
    ):
        """Initialize the GitHub client.

        Args:
            token: GitHub personal access token or app token.
            base_url: GitHub API base URL (for enterprise).
            timeout: Request timeout in seconds.
        """
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._rate_limit: RateLimitInfo | None = None

    @property
    def rate_limit(self) -> RateLimitInfo | None:
        """Get the last known rate limit info."""
        return self._rate_limit

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create the HTTP client."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self._base_url,
                timeout=self._timeout,
                headers={
                    "Authorization": f"Bearer {self._token}",
                    "Accept": "application/vnd.github+json",
                    "X-GitHub-Api-Version": "2022-11-28",
                },
            )
        return self._client

    async def close(self) -> None:
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()
            self._client = None

    def _update_rate_limit(self, headers: httpx.Headers) -> None:
        """Update rate limit info from response headers."""
        with contextlib.suppress(ValueError, TypeError):
            self._rate_limit = RateLimitInfo(
                limit=int(headers.get("x-ratelimit-limit", 0)),
                remaining=int(headers.get("x-ratelimit-remaining", 0)),
                reset_at=int(headers.get("x-ratelimit-reset", 0)),
                used=int(headers.get("x-ratelimit-used", 0)),
            )

    async def _request(
        self,
        method: str,
        path: str,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any] | list[dict[str, Any]]:
        """Make a request to the GitHub API.

        Args:
            method: HTTP method.
            path: API path (without base URL).
            params: Query parameters.
            json: JSON body for POST/PUT/PATCH.

        Returns:
            Parsed JSON response.

        Raises:
            GitHubAuthError: Authentication failed.
            GitHubNotFoundError: Resource not found.
            GitHubRateLimitError: Rate limit exceeded.
            GitHubValidationError: Validation error.
            GitHubAPIError: Other API errors.
        """
        client = await self._get_client()

        try:
            response = await client.request(
                method=method,
                url=path,
                params=params,
                json=json,
            )
        except httpx.RequestError as e:
            log.error("github_request_failed", path=path, error=str(e))
            raise GitHubAPIError(f"Request failed: {e}") from e

        self._update_rate_limit(response.headers)

        # Handle error responses
        if response.status_code == 401:
            raise GitHubAuthError("Authentication failed", status_code=401)

        if response.status_code == 403:
            if "rate limit" in response.text.lower():
                raise GitHubRateLimitError(
                    "Rate limit exceeded",
                    reset_at=self._rate_limit.reset_at if self._rate_limit else None,
                    remaining=0,
                )
            raise GitHubAPIError(f"Forbidden: {response.text}", status_code=403)

        if response.status_code == 404:
            raise GitHubNotFoundError("Resource not found", status_code=404)

        if response.status_code == 422:
            try:
                error_data = response.json()
            except Exception:
                error_data = {"message": response.text}
            raise GitHubValidationError(
                error_data.get("message", "Validation failed"),
                status_code=422,
                response=error_data,
            )

        if response.status_code >= 400:
            try:
                error_data = response.json()
            except Exception:
                error_data = {"message": response.text}
            raise GitHubAPIError(
                error_data.get("message", f"HTTP {response.status_code}"),
                status_code=response.status_code,
                response=error_data,
            )

        # Handle empty responses
        if response.status_code == 204:
            return {}

        try:
            result: dict[str, Any] | list[dict[str, Any]] = response.json()
            return result
        except Exception:
            return {}

    # ========== Authentication ==========

    async def get_authenticated_user(self) -> User:
        """Get the authenticated user.

        Returns:
            The authenticated user.
        """
        data = await self._request("GET", "/user")
        if isinstance(data, dict):
            return User.from_api(data)
        raise GitHubAPIError("Unexpected response format")

    async def verify_token(self) -> bool:
        """Verify the token is valid.

        Returns:
            True if the token is valid.
        """
        try:
            await self.get_authenticated_user()
            return True
        except GitHubAuthError:
            return False

    # ========== Repositories ==========

    async def get_repository(self, owner: str, repo: str) -> Repository:
        """Get repository information.

        Args:
            owner: Repository owner.
            repo: Repository name.

        Returns:
            Repository information.
        """
        data = await self._request("GET", f"/repos/{owner}/{repo}")
        if isinstance(data, dict):
            return Repository.from_api(data)
        raise GitHubAPIError("Unexpected response format")

    async def get_reference(self, owner: str, repo: str, ref: str) -> dict[str, Any] | None:
        """Get one git reference by name.

        Args:
            owner: Repository owner.
            repo: Repository name.
            ref: Ref name (for example ``heads/main``).

        Returns:
            The ref payload, or ``None`` when missing.
        """
        normalized = str(ref or "").strip().removeprefix("refs/")
        if not normalized:
            raise ValueError("ref is required")
        try:
            data = await self._request("GET", f"/repos/{owner}/{repo}/git/ref/{normalized}")
        except GitHubNotFoundError:
            return None
        if isinstance(data, dict):
            return data
        raise GitHubAPIError("Unexpected response format")

    async def create_reference(
        self,
        owner: str,
        repo: str,
        *,
        ref: str,
        sha: str,
    ) -> dict[str, Any]:
        """Create a git reference.

        Args:
            owner: Repository owner.
            repo: Repository name.
            ref: Ref name (for example ``heads/feature``).
            sha: Target commit SHA.
        """
        normalized = str(ref or "").strip().removeprefix("refs/")
        if not normalized:
            raise ValueError("ref is required")
        commit_sha = str(sha or "").strip()
        if not commit_sha:
            raise ValueError("sha is required")
        data = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/git/refs",
            json={
                "ref": f"refs/{normalized}",
                "sha": commit_sha,
            },
        )
        if isinstance(data, dict):
            return data
        raise GitHubAPIError("Unexpected response format")

    async def ensure_branch(
        self,
        owner: str,
        repo: str,
        *,
        branch: str,
        source_ref: str,
    ) -> dict[str, Any]:
        """Ensure a branch exists, creating from source ref when needed."""
        branch_name = str(branch or "").strip().removeprefix("refs/heads/").removeprefix("heads/")
        if not branch_name:
            raise ValueError("branch is required")

        target_ref = f"heads/{branch_name}"
        existing = await self.get_reference(owner, repo, target_ref)
        if existing is not None:
            object_payload = existing.get("object")
            existing_sha = ""
            if isinstance(object_payload, dict):
                existing_sha = str(object_payload.get("sha") or "")
            return {
                "created": False,
                "ref": str(existing.get("ref") or f"refs/{target_ref}"),
                "sha": existing_sha,
            }

        raw_source = str(source_ref or "").strip()
        if not raw_source:
            raise ValueError("source_ref is required")
        if raw_source.startswith("refs/"):
            normalized_source = raw_source.removeprefix("refs/")
        elif raw_source.startswith("heads/"):
            normalized_source = raw_source
        else:
            normalized_source = f"heads/{raw_source}"

        source = await self.get_reference(owner, repo, normalized_source)
        if source is None:
            raise GitHubNotFoundError(
                f"Source ref not found: {normalized_source}",
                status_code=404,
            )
        source_obj = source.get("object")
        if not isinstance(source_obj, dict):
            raise GitHubAPIError("Source ref payload is malformed")
        source_sha = str(source_obj.get("sha") or "").strip()
        if not source_sha:
            raise GitHubAPIError("Source ref did not include an object sha")

        created = await self.create_reference(
            owner,
            repo,
            ref=target_ref,
            sha=source_sha,
        )
        created_obj = created.get("object")
        created_sha = ""
        if isinstance(created_obj, dict):
            created_sha = str(created_obj.get("sha") or "")
        return {
            "created": True,
            "ref": str(created.get("ref") or f"refs/{target_ref}"),
            "sha": created_sha or source_sha,
        }

    async def list_repositories(
        self,
        per_page: int = DEFAULT_PER_PAGE,
        page: int = 1,
        sort: str = "updated",
    ) -> list[Repository]:
        """List repositories for the authenticated user.

        Args:
            per_page: Results per page.
            page: Page number.
            sort: Sort field (created, updated, pushed, full_name).

        Returns:
            List of repositories.
        """
        data = await self._request(
            "GET",
            "/user/repos",
            params={"per_page": per_page, "page": page, "sort": sort},
        )
        if isinstance(data, list):
            return [Repository.from_api(r) for r in data]
        return []

    # ========== Issues ==========

    async def list_issues(
        self,
        owner: str,
        repo: str,
        state: IssueState | str = "open",
        labels: list[str] | None = None,
        assignee: str | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        page: int = 1,
        sort: str = "created",
        direction: str = "desc",
    ) -> list[Issue]:
        """List issues for a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            state: Issue state (open, closed, all).
            labels: Filter by labels.
            assignee: Filter by assignee.
            per_page: Results per page.
            page: Page number.
            sort: Sort field (created, updated, comments).
            direction: Sort direction (asc, desc).

        Returns:
            List of issues.
        """
        params: dict[str, Any] = {
            "state": state.value if isinstance(state, IssueState) else state,
            "per_page": per_page,
            "page": page,
            "sort": sort,
            "direction": direction,
        }
        if labels:
            params["labels"] = ",".join(labels)
        if assignee:
            params["assignee"] = assignee

        data = await self._request("GET", f"/repos/{owner}/{repo}/issues", params=params)
        repository = f"{owner}/{repo}"

        if isinstance(data, list):
            # Filter out PRs (they appear in issues endpoint)
            return [
                Issue.from_api(i, repository=repository) for i in data if "pull_request" not in i
            ]
        return []

    async def get_issue(self, owner: str, repo: str, issue_number: int) -> Issue:
        """Get a specific issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            issue_number: Issue number.

        Returns:
            The issue.
        """
        data = await self._request("GET", f"/repos/{owner}/{repo}/issues/{issue_number}")
        if isinstance(data, dict):
            return Issue.from_api(data, repository=f"{owner}/{repo}")
        raise GitHubAPIError("Unexpected response format")

    async def create_issue(
        self,
        owner: str,
        repo: str,
        title: str,
        body: str = "",
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
        milestone: int | None = None,
    ) -> Issue:
        """Create a new issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            title: Issue title.
            body: Issue body.
            labels: Labels to apply.
            assignees: Users to assign.
            milestone: Milestone number.

        Returns:
            The created issue.
        """
        payload: dict[str, Any] = {"title": title}
        if body:
            payload["body"] = body
        if labels:
            payload["labels"] = labels
        if assignees:
            payload["assignees"] = assignees
        if milestone:
            payload["milestone"] = milestone

        data = await self._request("POST", f"/repos/{owner}/{repo}/issues", json=payload)
        if isinstance(data, dict):
            log.info(
                "issue_created",
                repo=f"{owner}/{repo}",
                number=data.get("number"),
                title=title,
            )
            return Issue.from_api(data, repository=f"{owner}/{repo}")
        raise GitHubAPIError("Unexpected response format")

    async def update_issue(
        self,
        owner: str,
        repo: str,
        issue_number: int,
        title: str | None = None,
        body: str | None = None,
        state: IssueState | None = None,
        labels: list[str] | None = None,
        assignees: list[str] | None = None,
        milestone: int | None = None,
    ) -> Issue:
        """Update an existing issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            issue_number: Issue number.
            title: New title (optional).
            body: New body (optional).
            state: New state (optional).
            labels: Labels to set (replaces existing).
            assignees: Assignees to set (replaces existing).
            milestone: Milestone number.

        Returns:
            The updated issue.
        """
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if body is not None:
            payload["body"] = body
        if state is not None:
            payload["state"] = state.value
        if labels is not None:
            payload["labels"] = labels
        if assignees is not None:
            payload["assignees"] = assignees
        if milestone is not None:
            payload["milestone"] = milestone

        data = await self._request(
            "PATCH", f"/repos/{owner}/{repo}/issues/{issue_number}", json=payload
        )
        if isinstance(data, dict):
            log.info(
                "issue_updated",
                repo=f"{owner}/{repo}",
                number=issue_number,
            )
            return Issue.from_api(data, repository=f"{owner}/{repo}")
        raise GitHubAPIError("Unexpected response format")

    async def close_issue(self, owner: str, repo: str, issue_number: int) -> Issue:
        """Close an issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            issue_number: Issue number.

        Returns:
            The closed issue.
        """
        return await self.update_issue(owner, repo, issue_number, state=IssueState.CLOSED)

    async def reopen_issue(self, owner: str, repo: str, issue_number: int) -> Issue:
        """Reopen an issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            issue_number: Issue number.

        Returns:
            The reopened issue.
        """
        return await self.update_issue(owner, repo, issue_number, state=IssueState.OPEN)

    async def add_labels(
        self, owner: str, repo: str, issue_number: int, labels: list[str]
    ) -> list[Label]:
        """Add labels to an issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            issue_number: Issue number.
            labels: Labels to add.

        Returns:
            The issue's labels after adding.
        """
        data = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels",
            json={"labels": labels},
        )
        if isinstance(data, list):
            log.info(
                "labels_added",
                repo=f"{owner}/{repo}",
                issue=issue_number,
                labels=labels,
            )
            return [Label.from_api(lbl) for lbl in data]
        return []

    async def remove_label(self, owner: str, repo: str, issue_number: int, label: str) -> None:
        """Remove a label from an issue.

        Args:
            owner: Repository owner.
            repo: Repository name.
            issue_number: Issue number.
            label: Label to remove.
        """
        await self._request(
            "DELETE",
            f"/repos/{owner}/{repo}/issues/{issue_number}/labels/{label}",
        )
        log.info(
            "label_removed",
            repo=f"{owner}/{repo}",
            issue=issue_number,
            label=label,
        )

    async def add_comment(
        self, owner: str, repo: str, issue_number: int, body: str
    ) -> dict[str, Any]:
        """Add a comment to an issue or PR.

        Args:
            owner: Repository owner.
            repo: Repository name.
            issue_number: Issue or PR number.
            body: Comment body.

        Returns:
            The created comment.
        """
        data = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/issues/{issue_number}/comments",
            json={"body": body},
        )
        if isinstance(data, dict):
            log.info(
                "comment_added",
                repo=f"{owner}/{repo}",
                issue=issue_number,
            )
            return data
        return {}

    # ========== Pull Requests ==========

    async def list_pull_requests(
        self,
        owner: str,
        repo: str,
        state: str = "open",
        head: str | None = None,
        base: str | None = None,
        sort: str = "created",
        direction: str = "desc",
        per_page: int = DEFAULT_PER_PAGE,
        page: int = 1,
    ) -> list[PullRequest]:
        """List pull requests for a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            state: PR state (open, closed, all).
            head: Filter by head ref (user:branch format).
            base: Filter by base ref.
            sort: Sort field (created, updated, popularity, long-running).
            direction: Sort direction (asc, desc).
            per_page: Results per page.
            page: Page number.

        Returns:
            List of pull requests.
        """
        params: dict[str, Any] = {
            "state": state,
            "sort": sort,
            "direction": direction,
            "per_page": per_page,
            "page": page,
        }
        if head:
            params["head"] = head
        if base:
            params["base"] = base

        data = await self._request("GET", f"/repos/{owner}/{repo}/pulls", params=params)
        repository = f"{owner}/{repo}"

        if isinstance(data, list):
            return [PullRequest.from_api(pr, repository=repository) for pr in data]
        return []

    async def get_pull_request(self, owner: str, repo: str, pr_number: int) -> PullRequest:
        """Get a specific pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            pr_number: PR number.

        Returns:
            The pull request.
        """
        data = await self._request("GET", f"/repos/{owner}/{repo}/pulls/{pr_number}")
        if isinstance(data, dict):
            return PullRequest.from_api(data, repository=f"{owner}/{repo}")
        raise GitHubAPIError("Unexpected response format")

    async def create_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        title: str,
        head: str,
        base: str,
        body: str = "",
        draft: bool = False,
    ) -> PullRequest:
        """Create a pull request."""
        title_text = str(title or "").strip()
        if not title_text:
            raise ValueError("title is required")
        head_ref = str(head or "").strip()
        base_ref = str(base or "").strip()
        if not head_ref or not base_ref:
            raise ValueError("head and base are required")
        payload: dict[str, Any] = {
            "title": title_text,
            "head": head_ref,
            "base": base_ref,
            "body": str(body or ""),
            "draft": bool(draft),
        }
        data = await self._request("POST", f"/repos/{owner}/{repo}/pulls", json=payload)
        if isinstance(data, dict):
            log.info(
                "pr_created",
                repo=f"{owner}/{repo}",
                number=data.get("number"),
                head=head_ref,
                base=base_ref,
            )
            return PullRequest.from_api(data, repository=f"{owner}/{repo}")
        raise GitHubAPIError("Unexpected response format")

    async def find_open_pull_request(
        self,
        owner: str,
        repo: str,
        *,
        head: str,
        base: str,
    ) -> PullRequest | None:
        """Find one open pull request for head -> base."""
        head_owner_ref = f"{owner}:{head}"
        prs = await self.list_pull_requests(
            owner=owner,
            repo=repo,
            state="open",
            head=head_owner_ref,
            base=base,
            per_page=1,
        )
        if not prs:
            return None
        return prs[0]

    async def list_pull_request_files(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        *,
        per_page: int = 100,
        page: int = 1,
    ) -> list[dict[str, Any]]:
        """List changed files for one pull request."""
        data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/pulls/{pr_number}/files",
            params={"per_page": per_page, "page": page},
        )
        if isinstance(data, list):
            return [dict(row) for row in data if isinstance(row, dict)]
        return []

    async def get_pr_diff(self, owner: str, repo: str, pr_number: int) -> str:
        """Get the diff for a pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            pr_number: PR number.

        Returns:
            The diff as a string.
        """
        client = await self._get_client()
        response = await client.get(
            f"/repos/{owner}/{repo}/pulls/{pr_number}",
            headers={"Accept": "application/vnd.github.diff"},
        )
        if response.status_code != 200:
            raise GitHubAPIError(
                f"Failed to get diff: {response.text}",
                status_code=response.status_code,
            )
        return response.text

    async def merge_pull_request(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        commit_title: str | None = None,
        commit_message: str | None = None,
        merge_method: str = "merge",  # merge, squash, rebase
    ) -> dict[str, Any]:
        """Merge a pull request.

        Args:
            owner: Repository owner.
            repo: Repository name.
            pr_number: PR number.
            commit_title: Commit title (for squash/merge).
            commit_message: Commit message.
            merge_method: Merge method (merge, squash, rebase).

        Returns:
            Merge result.
        """
        payload: dict[str, Any] = {"merge_method": merge_method}
        if commit_title:
            payload["commit_title"] = commit_title
        if commit_message:
            payload["commit_message"] = commit_message

        data = await self._request(
            "PUT", f"/repos/{owner}/{repo}/pulls/{pr_number}/merge", json=payload
        )
        if isinstance(data, dict):
            log.info(
                "pr_merged",
                repo=f"{owner}/{repo}",
                number=pr_number,
                method=merge_method,
            )
            return data
        return {}

    # ========== Workflows ==========

    async def list_workflow_runs(
        self,
        owner: str,
        repo: str,
        workflow_id: int | str | None = None,
        branch: str | None = None,
        event: str | None = None,
        status: str | None = None,
        per_page: int = DEFAULT_PER_PAGE,
        page: int = 1,
    ) -> list[WorkflowRun]:
        """List workflow runs.

        Args:
            owner: Repository owner.
            repo: Repository name.
            workflow_id: Filter by workflow ID or filename.
            branch: Filter by branch.
            event: Filter by event (push, pull_request, etc.).
            status: Filter by status.
            per_page: Results per page.
            page: Page number.

        Returns:
            List of workflow runs.
        """
        if workflow_id:
            path = f"/repos/{owner}/{repo}/actions/workflows/{workflow_id}/runs"
        else:
            path = f"/repos/{owner}/{repo}/actions/runs"

        params: dict[str, Any] = {"per_page": per_page, "page": page}
        if branch:
            params["branch"] = branch
        if event:
            params["event"] = event
        if status:
            params["status"] = status

        data = await self._request("GET", path, params=params)
        repository = f"{owner}/{repo}"

        if isinstance(data, dict):
            runs = data.get("workflow_runs", [])
            return [WorkflowRun.from_api(r, repository=repository) for r in runs]
        return []

    async def list_check_runs(
        self,
        owner: str,
        repo: str,
        *,
        ref: str,
        per_page: int = 100,
    ) -> list[dict[str, Any]]:
        """List check runs for a ref (branch/sha)."""
        ref_name = str(ref or "").strip()
        if not ref_name:
            raise ValueError("ref is required")
        data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/commits/{ref_name}/check-runs",
            params={"per_page": per_page},
        )
        if not isinstance(data, dict):
            return []
        runs = data.get("check_runs", [])
        if not isinstance(runs, list):
            return []
        return [dict(row) for row in runs if isinstance(row, dict)]

    async def rerun_workflow(self, owner: str, repo: str, run_id: int) -> None:
        """Re-run a workflow.

        Args:
            owner: Repository owner.
            repo: Repository name.
            run_id: Workflow run ID.
        """
        await self._request("POST", f"/repos/{owner}/{repo}/actions/runs/{run_id}/rerun")
        log.info("workflow_rerun", repo=f"{owner}/{repo}", run_id=run_id)

    # ========== Labels ==========

    async def list_labels(
        self,
        owner: str,
        repo: str,
        per_page: int = DEFAULT_PER_PAGE,
        page: int = 1,
    ) -> list[Label]:
        """List labels for a repository.

        Args:
            owner: Repository owner.
            repo: Repository name.
            per_page: Results per page.
            page: Page number.

        Returns:
            List of labels.
        """
        data = await self._request(
            "GET",
            f"/repos/{owner}/{repo}/labels",
            params={"per_page": per_page, "page": page},
        )
        if isinstance(data, list):
            return [Label.from_api(lbl) for lbl in data]
        return []

    async def create_label(
        self,
        owner: str,
        repo: str,
        name: str,
        color: str = "ededed",
        description: str = "",
    ) -> Label:
        """Create a new label.

        Args:
            owner: Repository owner.
            repo: Repository name.
            name: Label name.
            color: Label color (hex without #).
            description: Label description.

        Returns:
            The created label.
        """
        data = await self._request(
            "POST",
            f"/repos/{owner}/{repo}/labels",
            json={"name": name, "color": color, "description": description},
        )
        if isinstance(data, dict):
            log.info("label_created", repo=f"{owner}/{repo}", name=name)
            return Label.from_api(data)
        raise GitHubAPIError("Unexpected response format")
