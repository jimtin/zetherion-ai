"""Tests for GitHub API client."""

import io
import zipfile
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

    @pytest.mark.asyncio
    async def test_installation_archive_compare_and_branch_protection_helpers(self, client):
        with patch.object(
            client,
            "_request",
            AsyncMock(
                side_effect=[
                    {
                        "repositories": [
                            {
                                "id": 1,
                                "name": "repo",
                                "full_name": "owner/repo",
                                "private": True,
                                "default_branch": "main",
                            }
                        ]
                    },
                    {"ahead_by": 1},
                    {"required_status_checks": {"strict": True}},
                ]
            ),
        ) as mock_request:
            repositories = await client.list_installation_repositories()
            comparison = await client.compare_commits(
                "owner",
                "repo",
                base="main",
                head="feature",
            )
            protection = await client.get_branch_protection(
                "owner",
                "repo",
                branch="main",
            )

        assert repositories[0].full_name == "owner/repo"
        assert comparison["ahead_by"] == 1
        assert protection == {"required_status_checks": {"strict": True}}
        assert mock_request.await_count == 3

        with pytest.raises(ValueError, match="base and head are required"):
            await client.compare_commits("owner", "repo", base="", head="feature")
        with pytest.raises(ValueError, match="branch is required"):
            await client.get_branch_protection("owner", "repo", branch="")

        with patch.object(
            client,
            "_request",
            AsyncMock(side_effect=GitHubNotFoundError("missing")),
        ):
            assert await client.get_branch_protection("owner", "repo", branch="main") is None

    @pytest.mark.asyncio
    async def test_archive_workflow_logs_and_commit_status_helpers(self, client):
        archive_response = MagicMock()
        archive_response.content = b"archive-bytes"

        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            archive.writestr("logs/unit.txt", "line 1\nline 2\n")
            archive.writestr("logs/worker.txt", "worker_error\n")
        logs_response = MagicMock()
        logs_response.content = buffer.getvalue()

        with patch.object(
            client,
            "_send",
            AsyncMock(side_effect=[archive_response, logs_response]),
        ) as mock_send:
            archive_bytes = await client.get_repository_archive(
                "owner",
                "repo",
                ref="main",
                archive_format="tarball",
            )
            logs = await client.download_workflow_run_logs("owner", "repo", 123)

        assert archive_bytes == b"archive-bytes"
        assert logs["entries"][0]["name"] == "logs/unit.txt"
        assert "worker_error" in logs["combined_text"]
        assert logs["archive_size_bytes"] > 0
        assert mock_send.await_count == 2
        with pytest.raises(ValueError, match="archive_format"):
            await client.get_repository_archive("owner", "repo", archive_format="rar")

        with patch.object(
            client,
            "_request",
            AsyncMock(
                side_effect=[
                    {"id": 123},
                    {"jobs": [{"id": 1}]},
                    {"artifacts": [{"id": 2}]},
                    {"id": 999},
                ]
            ),
        ) as mock_request:
            workflow_run = await client.get_workflow_run("owner", "repo", 123)
            jobs = await client.list_workflow_jobs("owner", "repo", 123)
            artifacts = await client.list_workflow_run_artifacts("owner", "repo", 123)
            status = await client.create_commit_status(
                "owner",
                "repo",
                "a" * 40,
                state="success",
                context="zetherion/merge-readiness",
                description="ready",
                target_url="https://cgs.example.com",
            )

        assert workflow_run["id"] == 123
        assert jobs[0]["id"] == 1
        assert artifacts[0]["id"] == 2
        assert status["id"] == 999
        status_payload = mock_request.await_args.kwargs["json_body"]
        assert status_payload["target_url"] == "https://cgs.example.com"

    @pytest.mark.asyncio
    async def test_security_alert_helpers_return_raw_alert_lists(self, client):
        with patch.object(
            client,
            "_request",
            AsyncMock(
                side_effect=[
                    [{"number": 1, "security_vulnerability": {"severity": "high"}}],
                    [{"number": 2, "rule": {"security_severity_level": "medium"}}],
                ]
            ),
        ) as mock_request:
            dependabot_alerts = await client.list_dependabot_alerts("owner", "repo")
            code_scanning_alerts = await client.list_code_scanning_alerts("owner", "repo")

        assert dependabot_alerts[0]["number"] == 1
        assert code_scanning_alerts[0]["number"] == 2
        first_call = mock_request.await_args_list[0]
        second_call = mock_request.await_args_list[1]
        assert first_call.args[:2] == ("GET", "/repos/owner/repo/dependabot/alerts")
        assert first_call.kwargs["params"] == {"state": "open", "per_page": 100}
        assert second_call.args[:2] == ("GET", "/repos/owner/repo/code-scanning/alerts")
        assert second_call.kwargs["params"] == {"state": "open", "per_page": 100}


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
    async def test_get_client_recreates_closed_client(self, client):
        """A closed cached client should be replaced on the next access."""
        closed_client = MagicMock()
        closed_client.is_closed = True
        client._client = closed_client

        recreated = await client._get_client()

        assert recreated is not closed_client
        assert client._client is recreated

        open_client = MagicMock()
        open_client.is_closed = False
        client._client = open_client

        reused = await client._get_client()

        assert reused is open_client

    @pytest.mark.asyncio
    async def test_get_client_reuses_open_cached_client_without_recreating(self, client):
        """An open cached client should be returned without constructing a new one."""
        cached_client = MagicMock()
        cached_client.is_closed = False
        client._client = cached_client

        with patch("zetherion_ai.skills.github.client.httpx.AsyncClient") as mock_http_client:
            reused = await client._get_client()

        assert reused is cached_client
        mock_http_client.assert_not_called()

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
    async def test_repository_issue_and_pull_request_success_and_empty_paths(self, client):
        with patch.object(
            client,
            "_request",
            AsyncMock(
                side_effect=[
                    {
                        "id": 1,
                        "name": "repo",
                        "full_name": "owner/repo",
                        "private": False,
                        "default_branch": "main",
                    },
                    {"number": 1, "title": "Issue", "state": "open"},
                    {"number": 12, "title": "PR", "state": "open"},
                    [{"id": 1, "name": "repo", "full_name": "owner/repo"}],
                    {"required_status_checks": {"strict": True}},
                ]
            ),
        ) as mock_request:
            repository = await client.get_repository("owner", "repo")
            issue = await client.get_issue("owner", "repo", 1)
            pull_request = await client.get_pull_request("owner", "repo", 12)
            repositories = await client.list_repositories()
            protection = await client.update_branch_protection(
                "owner",
                "repo",
                branch="main",
                payload={"required_status_checks": {"strict": True}},
            )

        assert repository.full_name == "owner/repo"
        assert issue.number == 1
        assert pull_request.number == 12
        assert repositories[0].full_name == "owner/repo"
        assert protection["required_status_checks"]["strict"] is True
        assert mock_request.await_count == 5

        with patch.object(client, "_request", AsyncMock(return_value=[])):
            with pytest.raises(GitHubAPIError):
                await client.get_repository("owner", "repo")
            with pytest.raises(GitHubAPIError):
                await client.get_issue("owner", "repo", 1)
            with pytest.raises(GitHubAPIError):
                await client.get_pull_request("owner", "repo", 12)
            with pytest.raises(GitHubAPIError):
                await client.update_branch_protection(
                    "owner",
                    "repo",
                    branch="main",
                    payload={"required_status_checks": {"strict": True}},
                )

        with patch.object(client, "list_pull_requests", AsyncMock(return_value=[])):
            assert (
                await client.find_open_pull_request(
                    "owner",
                    "repo",
                    head="feature",
                    base="main",
                )
                is None
            )

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

    @pytest.mark.asyncio
    async def test_get_reference_and_create_reference_paths(self, client):
        with patch.object(
            client,
            "_request",
            AsyncMock(return_value={"ref": "refs/heads/main", "object": {"sha": "abc"}}),
        ) as mock_req:
            ref = await client.get_reference("owner", "repo", "refs/heads/main")
            assert ref is not None
            assert ref["ref"] == "refs/heads/main"
            assert mock_req.await_args.args[1] == "/repos/owner/repo/git/ref/heads/main"

        with patch.object(
            client,
            "_request",
            AsyncMock(side_effect=GitHubNotFoundError("missing")),
        ):
            ref = await client.get_reference("owner", "repo", "heads/missing")
            assert ref is None

        with pytest.raises(ValueError, match="ref is required"):
            await client.create_reference("owner", "repo", ref="", sha="abc")
        with pytest.raises(ValueError, match="sha is required"):
            await client.create_reference("owner", "repo", ref="heads/feature", sha="")

        with patch.object(
            client,
            "_request",
            AsyncMock(return_value={"ref": "refs/heads/feature", "object": {"sha": "abc"}}),
        ) as mock_req:
            created = await client.create_reference(
                "owner",
                "repo",
                ref="heads/feature",
                sha="abc",
            )
            assert created["ref"] == "refs/heads/feature"
            payload = mock_req.await_args.kwargs["json"]
            assert payload == {"ref": "refs/heads/feature", "sha": "abc"}

        with pytest.raises(ValueError, match="ref is required"):
            await client.get_reference("owner", "repo", "")

        with patch.object(client, "_request", AsyncMock(return_value=[])):
            with pytest.raises(GitHubAPIError):
                await client.create_reference(
                    "owner",
                    "repo",
                    ref="heads/feature",
                    sha="abc",
                )

    @pytest.mark.asyncio
    async def test_ensure_branch_existing_and_create_paths(self, client):
        with patch.object(
            client,
            "get_reference",
            AsyncMock(return_value={"ref": "refs/heads/feature", "object": {"sha": "abc"}}),
        ):
            ensured = await client.ensure_branch(
                "owner",
                "repo",
                branch="feature",
                source_ref="main",
            )
            assert ensured["created"] is False
            assert ensured["sha"] == "abc"

        with (
            patch.object(
                client,
                "get_reference",
                AsyncMock(
                    side_effect=[
                        None,
                        {"ref": "refs/heads/main", "object": {"sha": "source-sha"}},
                    ]
                ),
            ),
            patch.object(
                client,
                "create_reference",
                AsyncMock(
                    return_value={"ref": "refs/heads/feature", "object": {"sha": "source-sha"}}
                ),
            ),
        ):
            ensured = await client.ensure_branch(
                "owner",
                "repo",
                branch="feature",
                source_ref="main",
            )
            assert ensured["created"] is True
            assert ensured["sha"] == "source-sha"

        with pytest.raises(ValueError, match="branch is required"):
            await client.ensure_branch("owner", "repo", branch="", source_ref="main")

        with (
            patch.object(client, "get_reference", AsyncMock(return_value=None)),
            pytest.raises(ValueError, match="source_ref is required"),
        ):
            await client.ensure_branch("owner", "repo", branch="feature", source_ref="")

        with patch.object(
            client,
            "get_reference",
            AsyncMock(return_value={"ref": "refs/heads/feature", "object": "bad"}),
        ):
            ensured = await client.ensure_branch(
                "owner",
                "repo",
                branch="feature",
                source_ref="main",
            )
            assert ensured["sha"] == ""

        with (
            patch.object(
                client,
                "get_reference",
                AsyncMock(
                    side_effect=[
                        None,
                        None,
                    ]
                ),
            ),
            pytest.raises(GitHubNotFoundError, match="Source ref not found"),
        ):
            await client.ensure_branch(
                "owner",
                "repo",
                branch="feature",
                source_ref="refs/heads/main",
            )

        with (
            patch.object(
                client,
                "get_reference",
                AsyncMock(
                    side_effect=[
                        None,
                        {"ref": "refs/heads/main", "object": "bad"},
                    ]
                ),
            ),
            pytest.raises(GitHubAPIError, match="Source ref payload is malformed"),
        ):
            await client.ensure_branch("owner", "repo", branch="feature", source_ref="heads/main")

        with (
            patch.object(
                client,
                "get_reference",
                AsyncMock(
                    side_effect=[
                        None,
                        {"ref": "refs/heads/main", "object": {"sha": ""}},
                    ]
                ),
            ),
            pytest.raises(GitHubAPIError, match="Source ref did not include an object sha"),
        ):
            await client.ensure_branch("owner", "repo", branch="feature", source_ref="main")

        with (
            patch.object(
                client,
                "get_reference",
                AsyncMock(
                    side_effect=[
                        None,
                        {"ref": "refs/heads/main", "object": {"sha": "source-sha"}},
                    ]
                ),
            ),
            patch.object(
                client,
                "create_reference",
                AsyncMock(return_value={"ref": "refs/heads/feature"}),
            ),
        ):
            ensured = await client.ensure_branch(
                "owner",
                "repo",
                branch="feature",
                source_ref="main",
            )
            assert ensured["sha"] == "source-sha"

    @pytest.mark.asyncio
    async def test_installation_compare_and_branch_protection_unexpected_response_paths(
        self,
        client,
    ):
        with patch.object(
            client,
            "_request",
            AsyncMock(side_effect=[{"repositories": {}}, [], []]),
        ):
            repositories = await client.list_installation_repositories()
            assert repositories == []

            with pytest.raises(GitHubAPIError):
                await client.compare_commits("owner", "repo", base="main", head="feature")

            with pytest.raises(GitHubAPIError):
                await client.get_branch_protection("owner", "repo", branch="main")

        with pytest.raises(ValueError, match="branch is required"):
            await client.update_branch_protection(
                "owner",
                "repo",
                branch="",
                payload={"required_status_checks": {"strict": True}},
            )

        with patch.object(client, "_request", AsyncMock(return_value=[])):
            assert await client.list_installation_repositories() == []

    @pytest.mark.asyncio
    async def test_create_find_pr_files_and_check_runs_paths(self, client):
        with pytest.raises(ValueError, match="title is required"):
            await client.create_pull_request(
                "owner",
                "repo",
                title="",
                head="feature",
                base="main",
            )
        with pytest.raises(ValueError, match="head and base are required"):
            await client.create_pull_request(
                "owner",
                "repo",
                title="t",
                head="",
                base="main",
            )

        with patch.object(
            client,
            "_request",
            AsyncMock(return_value={"number": 9, "title": "PR", "state": "open"}),
        ):
            pr = await client.create_pull_request(
                "owner",
                "repo",
                title="PR",
                head="feature",
                base="main",
            )
            assert pr.number == 9

        with patch.object(
            client,
            "list_pull_requests",
            AsyncMock(return_value=[MagicMock(number=9)]),
        ):
            pr = await client.find_open_pull_request(
                "owner",
                "repo",
                head="feature",
                base="main",
            )
            assert pr is not None
            assert pr.number == 9

        with patch.object(client, "_request", AsyncMock(return_value=[])):
            files = await client.list_pull_request_files("owner", "repo", 1)
            assert files == []

        with patch.object(
            client,
            "_request",
            AsyncMock(return_value=[{"filename": "src/app.py"}]),
        ):
            files = await client.list_pull_request_files("owner", "repo", 1)
            assert files[0]["filename"] == "src/app.py"

        with patch.object(
            client,
            "_request",
            AsyncMock(return_value=[{"filename": "src/app.py"}, "bad-row"]),
        ):
            files = await client.list_pull_request_files("owner", "repo", 1)
            assert files == [{"filename": "src/app.py"}]

        with pytest.raises(ValueError, match="ref is required"):
            await client.list_check_runs("owner", "repo", ref="")

        with patch.object(
            client,
            "_request",
            AsyncMock(return_value={"check_runs": [{"name": "CI/CD Pipeline"}]}),
        ):
            runs = await client.list_check_runs("owner", "repo", ref="main")
            assert runs[0]["name"] == "CI/CD Pipeline"

        with patch.object(client, "_request", AsyncMock(return_value={})):
            runs = await client.list_check_runs("owner", "repo", ref="main")
            assert runs == []

        with patch.object(client, "_request", AsyncMock(return_value={"check_runs": "bad"})):
            runs = await client.list_check_runs("owner", "repo", ref="main")
            assert runs == []

    @pytest.mark.asyncio
    async def test_github_client_remaining_helper_branches(self, client):
        with patch.object(client, "_request", AsyncMock(return_value=[])):
            with pytest.raises(GitHubAPIError):
                await client.get_reference("owner", "repo", "heads/main")
            with pytest.raises(GitHubAPIError):
                await client.create_pull_request(
                    "owner",
                    "repo",
                    title="PR",
                    head="feature",
                    base="main",
                )
            with pytest.raises(GitHubAPIError):
                await client.get_workflow_run("owner", "repo", 1)
            with pytest.raises(GitHubAPIError):
                await client.create_commit_status(
                    "owner",
                    "repo",
                    "a" * 40,
                    state="success",
                    context="zetherion/check",
                    description="ready",
                )

        with patch.object(
            client,
            "_request",
            AsyncMock(return_value={"id": 1}),
        ) as mock_request:
            status = await client.create_commit_status(
                "owner",
                "repo",
                "a" * 40,
                state="success",
                context="zetherion/check",
                description="ready",
            )
            assert status["id"] == 1
            assert "target_url" not in mock_request.await_args.kwargs["json_body"]

        diff_response = MagicMock(spec=httpx.Response)
        diff_response.status_code = 200
        diff_response.text = "diff --git a/file b/file"
        diff_client = AsyncMock()
        diff_client.get.return_value = diff_response
        with patch.object(client, "_get_client", AsyncMock(return_value=diff_client)):
            diff = await client.get_pr_diff("owner", "repo", 9)
            assert "diff --git" in diff

        with patch.object(client, "_request", AsyncMock(return_value={"jobs": "bad"})):
            assert await client.list_workflow_jobs("owner", "repo", 7) == []

        with patch.object(
            client,
            "_request",
            AsyncMock(return_value={"jobs": [{"id": 1}, "bad"]}),
        ):
            assert await client.list_workflow_jobs("owner", "repo", 7) == [{"id": 1}]

        with patch.object(client, "_request", AsyncMock(return_value={"artifacts": "bad"})):
            assert await client.list_workflow_run_artifacts("owner", "repo", 7) == []

        with patch.object(
            client,
            "_request",
            AsyncMock(return_value={"artifacts": [{"id": 2}, "bad"]}),
        ):
            assert await client.list_workflow_run_artifacts("owner", "repo", 7) == [{"id": 2}]

        with patch.object(
            client,
            "_request",
            AsyncMock(return_value=[{"name": "triage", "color": "cccccc"}]),
        ):
            labels = await client.list_labels("owner", "repo")
            assert labels[0].name == "triage"

    @pytest.mark.asyncio
    async def test_download_workflow_run_logs_handles_empty_and_truncated_archives(self, client):
        empty_response = MagicMock(spec=httpx.Response)
        empty_response.content = b""

        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("logs/", "")
            archive.writestr("logs/unit.txt", "A" * 20)
            archive.writestr("logs/worker.txt", "B" * 20)

        archive_response = MagicMock(spec=httpx.Response)
        archive_response.content = archive_buffer.getvalue()

        with patch.object(
            client,
            "_send",
            AsyncMock(side_effect=[empty_response, archive_response]),
        ):
            empty_logs = await client.download_workflow_run_logs("owner", "repo", 1)
            truncated_logs = await client.download_workflow_run_logs(
                "owner",
                "repo",
                2,
                max_bytes=10,
            )

        assert empty_logs == {"entries": [], "combined_text": "", "truncated": False}
        assert truncated_logs["truncated"] is True
        assert len(truncated_logs["entries"]) == 1
        assert truncated_logs["entries"][0]["name"] == "logs/unit.txt"
        assert all(entry["name"] != "logs/" for entry in truncated_logs["entries"])

    @pytest.mark.asyncio
    async def test_remaining_github_client_response_shape_guards(self, client):
        with patch.object(
            client,
            "_request",
            AsyncMock(
                side_effect=[
                    [],
                    "bad",
                    [],
                    {},
                    "bad",
                    {"jobs": "bad"},
                    "bad",
                    {"artifacts": "bad"},
                    "bad",
                    {"check_runs": "bad"},
                    [],
                    {"id": 1},
                    [],
                ]
            ),
        ) as mock_request:
            with pytest.raises(GitHubAPIError, match="Unexpected response format"):
                await client.get_reference("owner", "repo", "heads/main")

            assert await client.list_installation_repositories() == []

            with pytest.raises(GitHubAPIError, match="Unexpected response format"):
                await client.create_pull_request(
                    "owner",
                    "repo",
                    title="PR",
                    head="feature",
                    base="main",
                )

            assert await client.list_pull_request_files("owner", "repo", 7) == []
            assert await client.list_workflow_jobs("owner", "repo", 7) == []
            assert await client.list_workflow_jobs("owner", "repo", 7) == []
            assert await client.list_workflow_run_artifacts("owner", "repo", 7) == []
            assert await client.list_workflow_run_artifacts("owner", "repo", 7) == []
            assert await client.list_check_runs("owner", "repo", ref="main") == []
            assert await client.list_check_runs("owner", "repo", ref="main") == []

            with pytest.raises(GitHubAPIError, match="Unexpected response format"):
                await client.create_commit_status(
                    "owner",
                    "repo",
                    "a" * 40,
                    state="success",
                    context="zetherion/check",
                    description="ready",
                )

            status = await client.create_commit_status(
                "owner",
                "repo",
                "a" * 40,
                state="success",
                context="zetherion/check",
                description="ready",
                target_url="https://cgs.example.com/runs/1",
            )
            assert status["id"] == 1
            assert mock_request.await_args_list[11].kwargs["json_body"]["target_url"].startswith(
                "https://cgs.example.com/"
            )

            assert await client.list_labels("owner", "repo") == []

    @pytest.mark.asyncio
    async def test_remaining_github_client_success_edge_paths(self, client):
        diff_response = MagicMock(spec=httpx.Response)
        diff_response.status_code = 200
        diff_response.text = "diff --git a/file b/file\n+added"

        diff_client = AsyncMock()
        diff_client.get.return_value = diff_response

        empty_logs_response = MagicMock(spec=httpx.Response)
        empty_logs_response.content = b""

        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("logs/", "")
            archive.writestr("logs/unit.txt", "A" * 20)
            archive.writestr("logs/worker.txt", "B" * 20)

        archive_response = MagicMock(spec=httpx.Response)
        archive_response.content = archive_buffer.getvalue()

        with (
            patch.object(client, "_get_client", AsyncMock(return_value=diff_client)),
            patch.object(
                client,
                "_send",
                AsyncMock(side_effect=[empty_logs_response, archive_response]),
            ),
            patch.object(
                client,
                "_request",
                AsyncMock(
                    side_effect=[
                        {"id": 77},
                        [{"name": "triage", "color": "cccccc"}],
                    ]
                ),
            ) as mock_request,
        ):
            diff = await client.get_pr_diff("owner", "repo", 9)
            empty_logs = await client.download_workflow_run_logs("owner", "repo", 1)
            truncated_logs = await client.download_workflow_run_logs(
                "owner",
                "repo",
                2,
                max_bytes=10,
            )
            status = await client.create_commit_status(
                "owner",
                "repo",
                "b" * 40,
                state="success",
                context="zetherion/check",
                description="ready",
            )
            labels = await client.list_labels("owner", "repo")

        assert diff.startswith("diff --git")
        assert empty_logs == {"entries": [], "combined_text": "", "truncated": False}
        assert truncated_logs["truncated"] is True
        assert truncated_logs["entries"][0]["name"] == "logs/unit.txt"
        assert all(entry["name"] != "logs/" for entry in truncated_logs["entries"])
        assert status["id"] == 77
        assert "target_url" not in mock_request.await_args_list[0].kwargs["json_body"]
        assert labels[0].name == "triage"
