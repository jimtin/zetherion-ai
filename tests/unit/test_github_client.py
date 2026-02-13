"""Tests for GitHub API client."""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from zetherion_ai.skills.github.client import (
    GitHubAPIError,
    GitHubAuthError,
    GitHubClient,
    GitHubNotFoundError,
    GitHubRateLimitError,
    GitHubValidationError,
)
from zetherion_ai.skills.github.models import IssueState


@pytest.fixture
def client():
    """Create a GitHubClient instance."""
    return GitHubClient(token="test-token")


@pytest.fixture
def mock_response():
    """Create a mock httpx.Response."""

    def _create(status_code: int, json_data: dict | list | None = None, text: str = ""):
        response = MagicMock(spec=httpx.Response)
        response.status_code = status_code
        response.text = text
        response.headers = httpx.Headers(
            {
                "x-ratelimit-limit": "5000",
                "x-ratelimit-remaining": "4999",
                "x-ratelimit-reset": "1700000000",
                "x-ratelimit-used": "1",
            }
        )
        if json_data is not None:
            response.json.return_value = json_data
        return response

    return _create


class TestGitHubClient:
    """Tests for GitHubClient."""

    @pytest.mark.asyncio
    async def test_close(self, client):
        """Test closing the client."""
        # Get client to initialize it
        await client._get_client()
        assert client._client is not None

        await client.close()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_rate_limit_tracking(self, client, mock_response):
        """Test rate limit info is extracted from headers."""
        response = mock_response(200, {"login": "testuser", "id": 123})

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            await client._request("GET", "/user")

            assert client.rate_limit is not None
            assert client.rate_limit.limit == 5000
            assert client.rate_limit.remaining == 4999


class TestGitHubClientErrors:
    """Tests for error handling."""

    @pytest.mark.asyncio
    async def test_auth_error(self, client, mock_response):
        """Test 401 raises GitHubAuthError."""
        response = mock_response(401, text="Bad credentials")

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            with pytest.raises(GitHubAuthError):
                await client._request("GET", "/user")

    @pytest.mark.asyncio
    async def test_not_found_error(self, client, mock_response):
        """Test 404 raises GitHubNotFoundError."""
        response = mock_response(404, text="Not Found")

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            with pytest.raises(GitHubNotFoundError):
                await client._request("GET", "/repos/owner/nonexistent")

    @pytest.mark.asyncio
    async def test_rate_limit_error(self, client, mock_response):
        """Test 403 with rate limit message raises GitHubRateLimitError."""
        response = mock_response(403, text="API rate limit exceeded")

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            with pytest.raises(GitHubRateLimitError):
                await client._request("GET", "/user")

    @pytest.mark.asyncio
    async def test_validation_error(self, client, mock_response):
        """Test 422 raises GitHubValidationError."""
        response = mock_response(
            422, {"message": "Validation Failed", "errors": [{"field": "title"}]}
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            with pytest.raises(GitHubValidationError) as exc_info:
                await client._request("POST", "/repos/owner/repo/issues")

            assert "Validation Failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_error(self, client):
        """Test network error raises GitHubAPIError."""
        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.side_effect = httpx.RequestError("Connection failed")
            mock_get.return_value = mock_client

            with pytest.raises(GitHubAPIError) as exc_info:
                await client._request("GET", "/user")

            assert "Connection failed" in str(exc_info.value)


class TestGitHubClientAuthentication:
    """Tests for authentication methods."""

    @pytest.mark.asyncio
    async def test_verify_token_valid(self, client, mock_response):
        """Test verify_token returns True for valid token."""
        response = mock_response(200, {"login": "testuser", "id": 123})

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            result = await client.verify_token()
            assert result is True

    @pytest.mark.asyncio
    async def test_verify_token_invalid(self, client, mock_response):
        """Test verify_token returns False for invalid token."""
        response = mock_response(401, text="Bad credentials")

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            result = await client.verify_token()
            assert result is False

    @pytest.mark.asyncio
    async def test_get_authenticated_user(self, client, mock_response):
        """Test getting authenticated user."""
        response = mock_response(
            200,
            {
                "login": "testuser",
                "id": 12345,
                "name": "Test User",
                "email": "test@example.com",
            },
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            user = await client.get_authenticated_user()

            assert user.login == "testuser"
            assert user.id == 12345


class TestGitHubClientIssues:
    """Tests for issue operations."""

    @pytest.mark.asyncio
    async def test_list_issues(self, client, mock_response):
        """Test listing issues."""
        response = mock_response(
            200,
            [
                {"number": 1, "title": "Issue 1", "state": "open"},
                {"number": 2, "title": "Issue 2", "state": "open"},
            ],
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            issues = await client.list_issues("owner", "repo")

            assert len(issues) == 2
            assert issues[0].number == 1
            assert issues[1].number == 2

    @pytest.mark.asyncio
    async def test_list_issues_filters_prs(self, client, mock_response):
        """Test that PRs are filtered out of issues list."""
        response = mock_response(
            200,
            [
                {"number": 1, "title": "Issue 1", "state": "open"},
                {
                    "number": 2,
                    "title": "PR 1",
                    "state": "open",
                    "pull_request": {"url": "..."},
                },
            ],
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            issues = await client.list_issues("owner", "repo")

            assert len(issues) == 1
            assert issues[0].number == 1

    @pytest.mark.asyncio
    async def test_create_issue(self, client, mock_response):
        """Test creating an issue."""
        response = mock_response(
            201,
            {
                "number": 42,
                "title": "New Issue",
                "body": "Issue body",
                "state": "open",
                "html_url": "https://github.com/owner/repo/issues/42",
            },
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            issue = await client.create_issue("owner", "repo", "New Issue", body="Issue body")

            assert issue.number == 42
            assert issue.title == "New Issue"

    @pytest.mark.asyncio
    async def test_close_issue(self, client, mock_response):
        """Test closing an issue."""
        response = mock_response(
            200,
            {"number": 42, "title": "Issue", "state": "closed"},
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            issue = await client.close_issue("owner", "repo", 42)

            assert issue.state == IssueState.CLOSED

    @pytest.mark.asyncio
    async def test_add_labels(self, client, mock_response):
        """Test adding labels to an issue."""
        response = mock_response(
            200,
            [
                {"name": "bug", "color": "d73a4a"},
                {"name": "priority", "color": "0052cc"},
            ],
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            labels = await client.add_labels("owner", "repo", 42, ["bug", "priority"])

            assert len(labels) == 2
            assert labels[0].name == "bug"


class TestGitHubClientPullRequests:
    """Tests for pull request operations."""

    @pytest.mark.asyncio
    async def test_list_pull_requests(self, client, mock_response):
        """Test listing pull requests."""
        response = mock_response(
            200,
            [
                {
                    "number": 1,
                    "title": "PR 1",
                    "state": "open",
                    "head": {"ref": "feature-1"},
                    "base": {"ref": "main"},
                },
                {
                    "number": 2,
                    "title": "PR 2",
                    "state": "open",
                    "head": {"ref": "feature-2"},
                    "base": {"ref": "main"},
                },
            ],
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            prs = await client.list_pull_requests("owner", "repo")

            assert len(prs) == 2
            assert prs[0].head_ref == "feature-1"

    @pytest.mark.asyncio
    async def test_merge_pull_request(self, client, mock_response):
        """Test merging a pull request."""
        response = mock_response(
            200,
            {"sha": "abc123", "merged": True, "message": "Pull Request successfully merged"},
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            result = await client.merge_pull_request("owner", "repo", 45)

            assert result["merged"] is True


class TestGitHubClientWorkflows:
    """Tests for workflow operations."""

    @pytest.mark.asyncio
    async def test_list_workflow_runs(self, client, mock_response):
        """Test listing workflow runs."""
        response = mock_response(
            200,
            {
                "total_count": 2,
                "workflow_runs": [
                    {
                        "id": 1,
                        "name": "CI",
                        "workflow_id": 100,
                        "head_branch": "main",
                        "status": "completed",
                        "conclusion": "success",
                    },
                    {
                        "id": 2,
                        "name": "CI",
                        "workflow_id": 100,
                        "head_branch": "feature",
                        "status": "in_progress",
                    },
                ],
            },
        )

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            runs = await client.list_workflow_runs("owner", "repo")

            assert len(runs) == 2
            assert runs[0].name == "CI"

    @pytest.mark.asyncio
    async def test_rerun_workflow(self, client, mock_response):
        """Test re-running a workflow."""
        response = mock_response(201, {})

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            # Should not raise
            await client.rerun_workflow("owner", "repo", 12345)


class TestGitHubClientAdditionalCoverage:
    """Additional branch-focused tests for GitHub client methods."""

    @pytest.mark.asyncio
    async def test_close_without_client_is_noop(self, client):
        """Closing an uninitialized client should be a no-op."""
        await client.close()
        assert client._client is None

    @pytest.mark.asyncio
    async def test_forbidden_non_rate_limit_raises_api_error(self, client, mock_response):
        response = mock_response(403, text="Forbidden")

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            with pytest.raises(GitHubAPIError) as exc_info:
                await client._request("GET", "/user")
            assert "Forbidden" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_validation_error_falls_back_to_response_text(self, client, mock_response):
        response = mock_response(422, text="Validation failed (text fallback)")
        response.json.side_effect = ValueError("invalid json")

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            with pytest.raises(GitHubValidationError) as exc_info:
                await client._request("POST", "/repos/owner/repo/issues")
            assert "Validation failed (text fallback)" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_http_error_falls_back_to_response_text(self, client, mock_response):
        response = mock_response(500, text="Internal server error")
        response.json.side_effect = ValueError("invalid json")

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            with pytest.raises(GitHubAPIError) as exc_info:
                await client._request("GET", "/repos/owner/repo")
            assert "Internal server error" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_request_returns_empty_dict_for_204(self, client, mock_response):
        response = mock_response(204, None, "")

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            result = await client._request("POST", "/repos/owner/repo/actions/runs/1/rerun")
            assert result == {}

    @pytest.mark.asyncio
    async def test_request_returns_empty_dict_when_json_parse_fails(self, client, mock_response):
        response = mock_response(200, text="not-json")
        response.json.side_effect = ValueError("invalid json")

        with patch.object(client, "_get_client") as mock_get:
            mock_client = AsyncMock()
            mock_client.request.return_value = response
            mock_get.return_value = mock_client

            result = await client._request("GET", "/user")
            assert result == {}

    @pytest.mark.asyncio
    async def test_get_authenticated_user_unexpected_response_raises(self, client):
        with patch.object(client, "_request", AsyncMock(return_value=[])):
            with pytest.raises(GitHubAPIError):
                await client.get_authenticated_user()

    @pytest.mark.asyncio
    async def test_get_repository_and_get_issue_unexpected_response_raise(self, client):
        with patch.object(client, "_request", AsyncMock(return_value=[])):
            with pytest.raises(GitHubAPIError):
                await client.get_repository("owner", "repo")
            with pytest.raises(GitHubAPIError):
                await client.get_issue("owner", "repo", 1)

    @pytest.mark.asyncio
    async def test_list_repositories_returns_empty_for_non_list(self, client):
        with patch.object(
            client, "_request", AsyncMock(return_value={"unexpected": True})
        ) as mock_req:
            repos = await client.list_repositories(per_page=10, page=2, sort="created")
            assert repos == []
            mock_req.assert_awaited_once_with(
                "GET",
                "/user/repos",
                params={"per_page": 10, "page": 2, "sort": "created"},
            )

    @pytest.mark.asyncio
    async def test_list_issues_includes_labels_and_assignee_filters(self, client):
        with patch.object(client, "_request", AsyncMock(return_value=[])) as mock_req:
            await client.list_issues(
                "owner",
                "repo",
                state=IssueState.OPEN,
                labels=["bug", "high-priority"],
                assignee="octocat",
            )

            params = mock_req.await_args.kwargs["params"]
            assert params["labels"] == "bug,high-priority"
            assert params["assignee"] == "octocat"

    @pytest.mark.asyncio
    async def test_list_issues_returns_empty_for_non_list(self, client):
        with patch.object(client, "_request", AsyncMock(return_value={"unexpected": True})):
            issues = await client.list_issues("owner", "repo")
            assert issues == []

    @pytest.mark.asyncio
    async def test_create_issue_includes_optional_fields(self, client):
        issue_data = {"number": 2, "title": "x", "state": "open"}
        with patch.object(client, "_request", AsyncMock(return_value=issue_data)) as mock_req:
            issue = await client.create_issue(
                "owner",
                "repo",
                "Issue title",
                labels=["bug"],
                assignees=["alice"],
                milestone=3,
            )
            assert issue.number == 2
            payload = mock_req.await_args.kwargs["json"]
            assert payload["title"] == "Issue title"
            assert payload["labels"] == ["bug"]
            assert payload["assignees"] == ["alice"]
            assert payload["milestone"] == 3
            assert "body" not in payload

    @pytest.mark.asyncio
    async def test_create_issue_unexpected_response_raises(self, client):
        with patch.object(client, "_request", AsyncMock(return_value=[])):
            with pytest.raises(GitHubAPIError):
                await client.create_issue("owner", "repo", "Issue title")

    @pytest.mark.asyncio
    async def test_update_issue_includes_all_optional_fields(self, client):
        issue_data = {"number": 42, "title": "Updated", "state": "closed"}
        with patch.object(client, "_request", AsyncMock(return_value=issue_data)) as mock_req:
            issue = await client.update_issue(
                "owner",
                "repo",
                42,
                title="Updated",
                body="Details",
                state=IssueState.CLOSED,
                labels=["bug"],
                assignees=["octocat"],
                milestone=8,
            )
            assert issue.number == 42
            payload = mock_req.await_args.kwargs["json"]
            assert payload == {
                "title": "Updated",
                "body": "Details",
                "state": "closed",
                "labels": ["bug"],
                "assignees": ["octocat"],
                "milestone": 8,
            }

    @pytest.mark.asyncio
    async def test_update_issue_unexpected_response_raises(self, client):
        with patch.object(client, "_request", AsyncMock(return_value=[])):
            with pytest.raises(GitHubAPIError):
                await client.update_issue("owner", "repo", 1, title="x")

    @pytest.mark.asyncio
    async def test_reopen_issue_uses_open_state(self, client):
        with patch.object(
            client, "update_issue", AsyncMock(return_value=MagicMock())
        ) as mock_update:
            await client.reopen_issue("owner", "repo", 7)
            mock_update.assert_awaited_once_with("owner", "repo", 7, state=IssueState.OPEN)

    @pytest.mark.asyncio
    async def test_add_labels_returns_empty_for_non_list(self, client):
        with patch.object(client, "_request", AsyncMock(return_value={"unexpected": True})):
            labels = await client.add_labels("owner", "repo", 1, ["bug"])
            assert labels == []

    @pytest.mark.asyncio
    async def test_remove_label_sends_delete_request(self, client):
        with patch.object(client, "_request", AsyncMock(return_value={})) as mock_req:
            await client.remove_label("owner", "repo", 5, "bug")
            mock_req.assert_awaited_once_with(
                "DELETE",
                "/repos/owner/repo/issues/5/labels/bug",
            )

    @pytest.mark.asyncio
    async def test_add_comment_returns_dict_or_empty(self, client):
        with patch.object(client, "_request", AsyncMock(return_value={"id": 11, "body": "ok"})):
            result = await client.add_comment("owner", "repo", 1, "hello")
            assert result["id"] == 11

        with patch.object(client, "_request", AsyncMock(return_value=[])):
            result = await client.add_comment("owner", "repo", 1, "hello")
            assert result == {}

    @pytest.mark.asyncio
    async def test_list_pull_requests_filters_and_non_list(self, client):
        with patch.object(client, "_request", AsyncMock(return_value=[])) as mock_req:
            await client.list_pull_requests("owner", "repo", head="alice:feature", base="main")
            params = mock_req.await_args.kwargs["params"]
            assert params["head"] == "alice:feature"
            assert params["base"] == "main"

        with patch.object(client, "_request", AsyncMock(return_value={"unexpected": True})):
            prs = await client.list_pull_requests("owner", "repo")
            assert prs == []

    @pytest.mark.asyncio
    async def test_get_pull_request_unexpected_response_raises(self, client):
        with patch.object(client, "_request", AsyncMock(return_value=[])):
            with pytest.raises(GitHubAPIError):
                await client.get_pull_request("owner", "repo", 12)

    @pytest.mark.asyncio
    async def test_get_pr_diff_raises_for_non_200(self, client):
        response = MagicMock(spec=httpx.Response)
        response.status_code = 500
        response.text = "boom"

        mock_http_client = AsyncMock()
        mock_http_client.get.return_value = response
        with patch.object(client, "_get_client", AsyncMock(return_value=mock_http_client)):
            with pytest.raises(GitHubAPIError) as exc_info:
                await client.get_pr_diff("owner", "repo", 9)
            assert "Failed to get diff" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_merge_pull_request_optional_fields_and_non_dict(self, client):
        with patch.object(client, "_request", AsyncMock(return_value={"merged": True})) as mock_req:
            result = await client.merge_pull_request(
                "owner",
                "repo",
                101,
                commit_title="Title",
                commit_message="Message",
                merge_method="squash",
            )
            assert result["merged"] is True
            payload = mock_req.await_args.kwargs["json"]
            assert payload == {
                "merge_method": "squash",
                "commit_title": "Title",
                "commit_message": "Message",
            }

        with patch.object(client, "_request", AsyncMock(return_value=[])):
            result = await client.merge_pull_request("owner", "repo", 101)
            assert result == {}

    @pytest.mark.asyncio
    async def test_list_workflow_runs_with_filters_and_non_dict(self, client):
        with patch.object(
            client,
            "_request",
            AsyncMock(return_value={"workflow_runs": [{"id": 1, "status": "queued"}]}),
        ) as mock_req:
            runs = await client.list_workflow_runs(
                "owner",
                "repo",
                workflow_id="ci.yml",
                branch="main",
                event="push",
                status="completed",
                per_page=5,
                page=3,
            )
            assert len(runs) == 1
            args = mock_req.await_args
            assert args.args[1] == "/repos/owner/repo/actions/workflows/ci.yml/runs"
            params = args.kwargs["params"]
            assert params["branch"] == "main"
            assert params["event"] == "push"
            assert params["status"] == "completed"

        with patch.object(client, "_request", AsyncMock(return_value=[])):
            runs = await client.list_workflow_runs("owner", "repo")
            assert runs == []

    @pytest.mark.asyncio
    async def test_list_labels_non_list_and_create_label_success_and_error(self, client):
        with patch.object(client, "_request", AsyncMock(return_value={"unexpected": True})):
            labels = await client.list_labels("owner", "repo")
            assert labels == []

        with patch.object(
            client,
            "_request",
            AsyncMock(return_value={"name": "triage", "color": "cccccc"}),
        ):
            label = await client.create_label("owner", "repo", "triage", "cccccc", "desc")
            assert label.name == "triage"

        with patch.object(client, "_request", AsyncMock(return_value=[])):
            with pytest.raises(GitHubAPIError):
                await client.create_label("owner", "repo", "triage")
