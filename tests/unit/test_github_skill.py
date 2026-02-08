"""Tests for GitHub Management Skill."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from zetherion_ai.skills.base import SkillRequest, SkillStatus
from zetherion_ai.skills.github.client import GitHubNotFoundError
from zetherion_ai.skills.github.models import (
    ActionType,
    AutonomyLevel,
    Issue,
    IssueState,
    Label,
    PullRequest,
    Repository,
    User,
    WorkflowRun,
)
from zetherion_ai.skills.github.skill import GitHubSkill


@pytest.fixture
def skill():
    """Create a GitHubSkill instance."""
    return GitHubSkill(github_token="test-token", default_repo="owner/repo")


@pytest.fixture
def mock_client():
    """Create a mock GitHubClient."""
    client = AsyncMock()
    client.verify_token.return_value = True
    client.get_authenticated_user.return_value = User(login="testuser", id=123)
    return client


class TestGitHubSkillMetadata:
    """Tests for skill metadata."""

    def test_metadata_name(self, skill):
        """Test skill name."""
        assert skill.metadata.name == "github_management"

    def test_metadata_version(self, skill):
        """Test skill version."""
        assert skill.metadata.version == "1.0.0"

    def test_metadata_collections(self, skill):
        """Test required collections."""
        assert "skill_github_config" in skill.metadata.collections
        assert "skill_github_audit" in skill.metadata.collections

    def test_metadata_intents(self, skill):
        """Test supported intents."""
        intents = skill.metadata.intents
        assert "list_issues" in intents
        assert "create_issue" in intents
        assert "list_prs" in intents
        assert "merge_pr" in intents


class TestGitHubSkillInitialization:
    """Tests for skill initialization."""

    @pytest.mark.asyncio
    async def test_initialize_success(self, skill, mock_client):
        """Test successful initialization."""
        with patch.object(skill, "_client", mock_client):
            skill._client = None  # Reset to trigger creation
            with patch("zetherion_ai.skills.github.skill.GitHubClient", return_value=mock_client):
                result = await skill.initialize()

                assert result is True
                assert skill.status == SkillStatus.READY

    @pytest.mark.asyncio
    async def test_initialize_no_token(self):
        """Test initialization fails without token."""
        skill = GitHubSkill(github_token=None)
        result = await skill.initialize()

        assert result is False
        assert skill.status == SkillStatus.ERROR
        assert "No GitHub token" in skill.error

    @pytest.mark.asyncio
    async def test_initialize_invalid_token(self, skill):
        """Test initialization fails with invalid token."""
        mock_client = AsyncMock()
        mock_client.verify_token.return_value = False

        with patch("zetherion_ai.skills.github.skill.GitHubClient", return_value=mock_client):
            result = await skill.initialize()

            assert result is False
            assert skill.status == SkillStatus.ERROR


class TestGitHubSkillRepoParser:
    """Tests for repository parsing."""

    def test_parse_repo_with_default(self, skill):
        """Test parsing uses default repo."""
        owner, repo = skill._parse_repo(None)
        assert owner == "owner"
        assert repo == "repo"

    def test_parse_repo_explicit(self, skill):
        """Test parsing explicit repo."""
        owner, repo = skill._parse_repo("other/project")
        assert owner == "other"
        assert repo == "project"

    def test_parse_repo_invalid(self, skill):
        """Test parsing invalid format."""
        skill._default_repo = None
        with pytest.raises(ValueError, match="No repository specified"):
            skill._parse_repo(None)

    def test_parse_repo_bad_format(self, skill):
        """Test parsing bad format raises error."""
        with pytest.raises(ValueError, match="Invalid repository format"):
            skill._parse_repo("invalid-format")


class TestGitHubSkillAutonomy:
    """Tests for autonomy configuration."""

    @pytest.mark.asyncio
    async def test_check_autonomy_autonomous(self, skill):
        """Test autonomous actions proceed immediately."""
        skill._autonomy_config.set_level(ActionType.LIST_ISSUES, AutonomyLevel.AUTONOMOUS)

        can_proceed, pending_id = await skill._check_autonomy(
            ActionType.LIST_ISSUES,
            "user123",
            "List issues",
            "handle_list_issues",
            {},
        )

        assert can_proceed is True
        assert pending_id is None

    @pytest.mark.asyncio
    async def test_check_autonomy_ask(self, skill):
        """Test ASK actions create pending action."""
        skill._autonomy_config.set_level(ActionType.CLOSE_ISSUE, AutonomyLevel.ASK)

        can_proceed, pending_id = await skill._check_autonomy(
            ActionType.CLOSE_ISSUE,
            "user123",
            "Close issue #42",
            "_execute_close_issue",
            {"issue_number": 42},
        )

        assert can_proceed is False
        assert pending_id is not None
        assert "user123" in skill._pending_actions
        assert len(skill._pending_actions["user123"]) == 1

    @pytest.mark.asyncio
    async def test_cancel_action(self, skill):
        """Test canceling a pending action."""
        # Create pending action
        _, pending_id = await skill._check_autonomy(
            ActionType.MERGE_PR,
            "user123",
            "Merge PR #45",
            "_execute_merge_pr",
            {},
        )

        # Cancel it
        result = skill.cancel_action("user123", pending_id)
        assert result is True

        # Should be gone
        result = skill.cancel_action("user123", pending_id)
        assert result is False


class TestGitHubSkillHandlers:
    """Tests for intent handlers."""

    @pytest.mark.asyncio
    async def test_handle_unknown_intent(self, skill, mock_client):
        """Test handling unknown intent."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(intent="unknown_intent", user_id="user123")
        response = await skill.handle(request)

        assert response.success is False
        assert "Unknown intent" in response.error

    @pytest.mark.asyncio
    async def test_handle_list_issues(self, skill, mock_client):
        """Test listing issues."""
        mock_client.list_issues.return_value = [
            Issue(number=1, title="Issue 1", state=IssueState.OPEN),
            Issue(number=2, title="Issue 2", state=IssueState.OPEN),
        ]
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="list_issues",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert len(response.data["issues"]) == 2
        assert "2 open issue(s)" in response.message

    @pytest.mark.asyncio
    async def test_handle_list_issues_empty(self, skill, mock_client):
        """Test listing issues when none exist."""
        mock_client.list_issues.return_value = []
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="list_issues",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "No open issues found" in response.message

    @pytest.mark.asyncio
    async def test_handle_get_issue(self, skill, mock_client):
        """Test getting a specific issue."""
        mock_client.get_issue.return_value = Issue(
            number=42,
            title="Test Issue",
            state=IssueState.OPEN,
            labels=[Label(name="bug")],
        )
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_issue",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["issue"]["number"] == 42

    @pytest.mark.asyncio
    async def test_handle_get_issue_not_found(self, skill, mock_client):
        """Test getting non-existent issue."""
        mock_client.get_issue.side_effect = GitHubNotFoundError("Not found")
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_issue",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 999},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "not found" in response.error.lower()

    @pytest.mark.asyncio
    async def test_handle_create_issue_autonomous(self, skill, mock_client):
        """Test creating issue when autonomous."""
        mock_client.create_issue.return_value = Issue(
            number=42,
            title="New Issue",
            html_url="https://github.com/owner/repo/issues/42",
        )
        skill._client = mock_client
        skill._status = SkillStatus.READY
        skill._autonomy_config.set_level(ActionType.CREATE_ISSUE, AutonomyLevel.AUTONOMOUS)

        request = SkillRequest(
            intent="create_issue",
            user_id="user123",
            context={"repository": "owner/repo", "title": "New Issue", "body": "Details"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["issue"]["number"] == 42
        assert "#42" in response.message

    @pytest.mark.asyncio
    async def test_handle_create_issue_requires_confirmation(self, skill, mock_client):
        """Test creating issue requires confirmation when not autonomous."""
        skill._client = mock_client
        skill._status = SkillStatus.READY
        skill._autonomy_config.set_level(ActionType.CREATE_ISSUE, AutonomyLevel.ASK)

        request = SkillRequest(
            intent="create_issue",
            user_id="user123",
            context={"repository": "owner/repo", "title": "New Issue"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data.get("requires_confirmation") is True
        assert "pending_action_id" in response.data

    @pytest.mark.asyncio
    async def test_handle_list_prs(self, skill, mock_client):
        """Test listing pull requests."""
        mock_client.list_pull_requests.return_value = [
            PullRequest(number=1, title="PR 1", head_ref="feature", base_ref="main"),
            PullRequest(number=2, title="PR 2", head_ref="bugfix", base_ref="main"),
        ]
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="list_prs",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert len(response.data["pull_requests"]) == 2

    @pytest.mark.asyncio
    async def test_handle_merge_pr_requires_confirmation(self, skill, mock_client):
        """Test merging PR requires confirmation."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="merge_pr",
            user_id="user123",
            context={"repository": "owner/repo", "pr_number": 45},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data.get("requires_confirmation") is True

    @pytest.mark.asyncio
    async def test_handle_add_label(self, skill, mock_client):
        """Test adding labels (autonomous by default)."""
        mock_client.add_labels.return_value = [
            Label(name="bug"),
            Label(name="priority"),
        ]
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="add_label",
            user_id="user123",
            context={
                "repository": "owner/repo",
                "issue_number": 42,
                "labels": ["bug", "priority"],
            },
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "bug, priority" in response.message

    @pytest.mark.asyncio
    async def test_handle_add_comment(self, skill, mock_client):
        """Test adding a comment."""
        mock_client.add_comment.return_value = {
            "id": 123,
            "html_url": "https://github.com/owner/repo/issues/42#issuecomment-123",
        }
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="add_comment",
            user_id="user123",
            context={
                "repository": "owner/repo",
                "issue_number": 42,
                "body": "Thanks for the report!",
            },
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "#42" in response.message


class TestGitHubSkillAutonomyHandlers:
    """Tests for autonomy configuration handlers."""

    @pytest.mark.asyncio
    async def test_handle_set_autonomy(self, skill, mock_client):
        """Test setting autonomy level."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="set_autonomy",
            user_id="user123",
            context={"action": "close_issue", "level": "autonomous"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert skill._autonomy_config.is_autonomous(ActionType.CLOSE_ISSUE)

    @pytest.mark.asyncio
    async def test_handle_set_autonomy_blocked(self, skill, mock_client):
        """Test cannot set dangerous action to autonomous."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="set_autonomy",
            user_id="user123",
            context={"action": "delete_repo", "level": "autonomous"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "requires confirmation" in response.error.lower()

    @pytest.mark.asyncio
    async def test_handle_get_autonomy(self, skill, mock_client):
        """Test getting autonomy configuration."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_autonomy",
            user_id="user123",
            context={},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "autonomy" in response.data
        assert "Autonomous" in response.message


class TestGitHubSkillSystemPrompt:
    """Tests for system prompt fragment."""

    def test_get_system_prompt_ready(self, skill):
        """Test system prompt when ready."""
        skill._status = SkillStatus.READY
        fragment = skill.get_system_prompt_fragment("user123")
        assert "[GitHub: Ready]" in fragment

    def test_get_system_prompt_pending(self, skill):
        """Test system prompt with pending actions."""
        skill._status = SkillStatus.READY
        skill._pending_actions["user123"] = {uuid4(): MagicMock()}

        fragment = skill.get_system_prompt_fragment("user123")
        assert "1 action(s) pending" in fragment

    def test_get_system_prompt_not_ready(self, skill):
        """Test system prompt when not ready."""
        skill._status = SkillStatus.ERROR
        fragment = skill.get_system_prompt_fragment("user123")
        assert fragment is None


class TestGitHubSkillCleanup:
    """Tests for cleanup."""

    @pytest.mark.asyncio
    async def test_cleanup(self, skill, mock_client):
        """Test cleanup closes client."""
        skill._client = mock_client

        await skill.cleanup()

        mock_client.close.assert_called_once()
        assert skill._client is None


class TestGitHubSkillUpdateIssue:
    """Tests for handle_update_issue and _execute_update_issue."""

    @pytest.mark.asyncio
    async def test_handle_update_issue_no_client(self, skill, mock_client):
        """Test update issue fails when client is not initialized."""
        skill._client = None
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="update_issue",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42, "title": "Updated"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "not initialized" in response.error.lower()

    @pytest.mark.asyncio
    async def test_handle_update_issue_no_issue_number(self, skill, mock_client):
        """Test update issue fails when issue_number is missing."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="update_issue",
            user_id="user123",
            context={"repository": "owner/repo", "title": "Updated"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "Issue number required" in response.error

    @pytest.mark.asyncio
    async def test_handle_update_issue_confirmation(self, skill, mock_client):
        """Test update issue requires confirmation in ASK mode."""
        skill._client = mock_client
        skill._status = SkillStatus.READY
        skill._autonomy_config.set_level(ActionType.UPDATE_ISSUE, AutonomyLevel.ASK)

        request = SkillRequest(
            intent="update_issue",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42, "title": "Updated"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data.get("requires_confirmation") is True
        assert "pending_action_id" in response.data

    @pytest.mark.asyncio
    async def test_handle_update_issue_autonomous(self, skill, mock_client):
        """Test update issue executes autonomously."""
        mock_client.update_issue.return_value = Issue(
            number=42,
            title="Updated Title",
            html_url="https://github.com/owner/repo/issues/42",
        )
        skill._client = mock_client
        skill._status = SkillStatus.READY
        skill._autonomy_config.set_level(ActionType.UPDATE_ISSUE, AutonomyLevel.AUTONOMOUS)

        request = SkillRequest(
            intent="update_issue",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42, "title": "Updated Title"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "Updated issue" in response.message


class TestGitHubSkillCloseIssue:
    """Tests for handle_close_issue and _execute_close_issue."""

    @pytest.mark.asyncio
    async def test_handle_close_issue_no_client(self, skill, mock_client):
        """Test close issue fails when client is not initialized."""
        skill._client = None
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="close_issue",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "not initialized" in response.error.lower()

    @pytest.mark.asyncio
    async def test_handle_close_issue_no_issue_number(self, skill, mock_client):
        """Test close issue fails when issue_number is missing."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="close_issue",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "Issue number required" in response.error

    @pytest.mark.asyncio
    async def test_handle_close_issue_autonomous(self, skill, mock_client):
        """Test close issue executes autonomously."""
        mock_client.close_issue.return_value = Issue(
            number=42,
            title="Test Issue",
            state=IssueState.CLOSED,
            html_url="https://github.com/owner/repo/issues/42",
        )
        skill._client = mock_client
        skill._status = SkillStatus.READY
        skill._autonomy_config.set_level(ActionType.CLOSE_ISSUE, AutonomyLevel.AUTONOMOUS)

        request = SkillRequest(
            intent="close_issue",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "Closed issue #42" in response.message


class TestGitHubSkillReopenIssue:
    """Tests for handle_reopen_issue and _execute_reopen_issue."""

    @pytest.mark.asyncio
    async def test_handle_reopen_issue_no_client(self, skill, mock_client):
        """Test reopen issue fails when client is not initialized."""
        skill._client = None
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="reopen_issue",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "not initialized" in response.error.lower()

    @pytest.mark.asyncio
    async def test_handle_reopen_issue_no_issue_number(self, skill, mock_client):
        """Test reopen issue fails when issue_number is missing."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="reopen_issue",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "Issue number required" in response.error

    @pytest.mark.asyncio
    async def test_handle_reopen_issue_confirmation(self, skill, mock_client):
        """Test reopen issue requires confirmation in ASK mode."""
        skill._client = mock_client
        skill._status = SkillStatus.READY
        skill._autonomy_config.set_level(ActionType.REOPEN_ISSUE, AutonomyLevel.ASK)

        request = SkillRequest(
            intent="reopen_issue",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data.get("requires_confirmation") is True
        assert "pending_action_id" in response.data

    @pytest.mark.asyncio
    async def test_handle_reopen_issue_autonomous(self, skill, mock_client):
        """Test reopen issue executes autonomously."""
        mock_client.reopen_issue.return_value = Issue(
            number=42,
            title="Test Issue",
            state=IssueState.OPEN,
            html_url="https://github.com/owner/repo/issues/42",
        )
        skill._client = mock_client
        skill._status = SkillStatus.READY
        skill._autonomy_config.set_level(ActionType.REOPEN_ISSUE, AutonomyLevel.AUTONOMOUS)

        request = SkillRequest(
            intent="reopen_issue",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "Reopened issue #42" in response.message


class TestGitHubSkillRemoveLabel:
    """Tests for handle_remove_label."""

    @pytest.mark.asyncio
    async def test_handle_remove_label_no_client(self, skill, mock_client):
        """Test remove label fails when client is not initialized."""
        skill._client = None
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="remove_label",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42, "label": "bug"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "not initialized" in response.error.lower()

    @pytest.mark.asyncio
    async def test_handle_remove_label_no_issue_number(self, skill, mock_client):
        """Test remove label fails when issue_number is missing."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="remove_label",
            user_id="user123",
            context={"repository": "owner/repo", "label": "bug"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "Issue number required" in response.error

    @pytest.mark.asyncio
    async def test_handle_remove_label_no_label(self, skill, mock_client):
        """Test remove label fails when label is missing."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="remove_label",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "Label required" in response.error

    @pytest.mark.asyncio
    async def test_handle_remove_label_success(self, skill, mock_client):
        """Test remove label succeeds."""
        mock_client.remove_label.return_value = None
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="remove_label",
            user_id="user123",
            context={"repository": "owner/repo", "issue_number": 42, "label": "bug"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "Removed label" in response.message


class TestGitHubSkillPRHandlers:
    """Tests for handle_get_pr, handle_get_pr_diff, and handle_list_prs empty."""

    @pytest.mark.asyncio
    async def test_handle_list_prs_empty(self, skill, mock_client):
        """Test listing pull requests when none exist."""
        mock_client.list_pull_requests.return_value = []
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="list_prs",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "No open pull requests" in response.message or "No" in response.message

    @pytest.mark.asyncio
    async def test_handle_get_pr_no_client(self, skill, mock_client):
        """Test get PR fails when client is not initialized."""
        skill._client = None
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_pr",
            user_id="user123",
            context={"repository": "owner/repo", "pr_number": 10},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "not initialized" in response.error.lower()

    @pytest.mark.asyncio
    async def test_handle_get_pr_no_pr_number(self, skill, mock_client):
        """Test get PR fails when pr_number is missing."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_pr",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "PR number required" in response.error

    @pytest.mark.asyncio
    async def test_handle_get_pr_success(self, skill, mock_client):
        """Test get PR succeeds."""
        mock_client.get_pull_request.return_value = PullRequest(
            number=10,
            title="Test PR",
            head_ref="feature",
            base_ref="main",
        )
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_pr",
            user_id="user123",
            context={"repository": "owner/repo", "pr_number": 10},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["pull_request"]["number"] == 10

    @pytest.mark.asyncio
    async def test_handle_get_pr_diff_no_client(self, skill, mock_client):
        """Test get PR diff fails when client is not initialized."""
        skill._client = None
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_pr_diff",
            user_id="user123",
            context={"repository": "owner/repo", "pr_number": 10},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "not initialized" in response.error.lower()

    @pytest.mark.asyncio
    async def test_handle_get_pr_diff_no_pr_number(self, skill, mock_client):
        """Test get PR diff fails when pr_number is missing."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_pr_diff",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "PR number required" in response.error

    @pytest.mark.asyncio
    async def test_handle_get_pr_diff_success(self, skill, mock_client):
        """Test get PR diff succeeds with a short diff."""
        mock_client.get_pr_diff.return_value = "diff --git a/file.py b/file.py\n+new line"
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_pr_diff",
            user_id="user123",
            context={"repository": "owner/repo", "pr_number": 10},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["truncated"] is False
        assert "diff" in response.message

    @pytest.mark.asyncio
    async def test_handle_get_pr_diff_truncation(self, skill, mock_client):
        """Test get PR diff truncation for large diffs."""
        long_diff = "x" * 20000
        mock_client.get_pr_diff.return_value = long_diff
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_pr_diff",
            user_id="user123",
            context={"repository": "owner/repo", "pr_number": 10},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["truncated"] is True
        assert "truncated" in response.data["diff"]


class TestGitHubSkillMergePR:
    """Tests for _execute_merge_pr."""

    @pytest.mark.asyncio
    async def test_execute_merge_pr_no_client(self, skill, mock_client):
        """Test _execute_merge_pr fails when client is not initialized."""
        skill._client = None
        skill._status = SkillStatus.READY

        from uuid import uuid4

        response = await skill._execute_merge_pr(
            request_id=uuid4(),
            owner="owner",
            repo="repo",
            pr_number=45,
            merge_method="merge",
        )

        assert response.success is False
        assert "not initialized" in response.error.lower()

    @pytest.mark.asyncio
    async def test_execute_merge_pr_success(self, skill, mock_client):
        """Test _execute_merge_pr succeeds."""
        mock_client.merge_pull_request.return_value = {
            "merged": True,
            "message": "Pull Request successfully merged",
        }
        skill._client = mock_client
        skill._status = SkillStatus.READY
        skill._autonomy_config.set_level(ActionType.MERGE_PR, AutonomyLevel.AUTONOMOUS)

        from uuid import uuid4

        response = await skill._execute_merge_pr(
            request_id=uuid4(),
            owner="owner",
            repo="repo",
            pr_number=45,
            merge_method="merge",
        )

        assert response.success is True
        assert "Merged PR" in response.message


class TestGitHubSkillWorkflowHandlers:
    """Tests for handle_list_workflows and handle_rerun_workflow."""

    @pytest.mark.asyncio
    async def test_handle_list_workflows_no_client(self, skill, mock_client):
        """Test list workflows fails when client is not initialized."""
        skill._client = None
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="list_workflows",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "not initialized" in response.error.lower()

    @pytest.mark.asyncio
    async def test_handle_list_workflows_empty(self, skill, mock_client):
        """Test list workflows returns empty result."""
        mock_client.list_workflow_runs.return_value = []
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="list_workflows",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "No workflow runs" in response.message

    @pytest.mark.asyncio
    async def test_handle_list_workflows_success(self, skill, mock_client):
        """Test list workflows returns results."""
        mock_client.list_workflow_runs.return_value = [
            WorkflowRun(
                id=100,
                name="CI",
                workflow_id=1,
                head_branch="main",
                event="push",
            ),
        ]
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="list_workflows",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert len(response.data["workflow_runs"]) == 1
        assert "1 workflow run(s)" in response.message

    @pytest.mark.asyncio
    async def test_handle_rerun_workflow_no_client(self, skill, mock_client):
        """Test rerun workflow fails when client is not initialized."""
        skill._client = None
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="rerun_workflow",
            user_id="user123",
            context={"repository": "owner/repo", "run_id": 100},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "not initialized" in response.error.lower()

    @pytest.mark.asyncio
    async def test_handle_rerun_workflow_no_run_id(self, skill, mock_client):
        """Test rerun workflow fails when run_id is missing."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="rerun_workflow",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "Workflow run ID required" in response.error

    @pytest.mark.asyncio
    async def test_handle_rerun_workflow_success(self, skill, mock_client):
        """Test rerun workflow succeeds."""
        mock_client.rerun_workflow.return_value = None
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="rerun_workflow",
            user_id="user123",
            context={"repository": "owner/repo", "run_id": 100},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert "Re-running workflow" in response.message


class TestGitHubSkillRepoHandler:
    """Tests for handle_get_repo_info."""

    @pytest.mark.asyncio
    async def test_handle_get_repo_info_no_client(self, skill, mock_client):
        """Test get repo info fails when client is not initialized."""
        skill._client = None
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_repo_info",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "not initialized" in response.error.lower()

    @pytest.mark.asyncio
    async def test_handle_get_repo_info_success(self, skill, mock_client):
        """Test get repo info succeeds."""
        mock_client.get_repository.return_value = Repository(
            owner="owner",
            name="repo",
            full_name="owner/repo",
            description="A test repository",
            default_branch="main",
            open_issues_count=5,
            stargazers_count=100,
            forks_count=10,
            private=False,
        )
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="get_repo_info",
            user_id="user123",
            context={"repository": "owner/repo"},
        )
        response = await skill.handle(request)

        assert response.success is True
        assert response.data["repository"]["full_name"] == "owner/repo"
        assert "owner/repo" in response.message


class TestGitHubSkillConfirmAction:
    """Tests for confirm_action."""

    @pytest.mark.asyncio
    async def test_confirm_action_no_pending(self, skill, mock_client):
        """Test confirm action when user has no pending actions."""
        from uuid import uuid4

        action_id = str(uuid4())
        response = await skill.confirm_action("user123", action_id)

        assert response.success is False
        assert "No pending actions" in response.error

    @pytest.mark.asyncio
    async def test_confirm_action_not_found(self, skill, mock_client):
        """Test confirm action with wrong action_id."""
        from uuid import uuid4

        # Create a pending action first
        skill._autonomy_config.set_level(ActionType.CLOSE_ISSUE, AutonomyLevel.ASK)
        _, pending_id = await skill._check_autonomy(
            ActionType.CLOSE_ISSUE,
            "user123",
            "Close issue #42",
            "_execute_close_issue",
            {"request_id": uuid4(), "owner": "owner", "repo": "repo", "issue_number": 42},
        )

        # Try to confirm with a different action_id
        wrong_id = str(uuid4())
        response = await skill.confirm_action("user123", wrong_id)

        assert response.success is False
        assert "Action not found" in response.error

    @pytest.mark.asyncio
    async def test_confirm_action_handler_not_found(self, skill, mock_client):
        """Test confirm action when the execute_fn method does not exist."""
        skill._autonomy_config.set_level(ActionType.CLOSE_ISSUE, AutonomyLevel.ASK)
        _, pending_id = await skill._check_autonomy(
            ActionType.CLOSE_ISSUE,
            "user123",
            "Close issue #42",
            "_nonexistent_handler",
            {},
        )

        response = await skill.confirm_action("user123", pending_id)

        assert response.success is False
        assert "Handler not found" in response.error

    @pytest.mark.asyncio
    async def test_confirm_action_success(self, skill, mock_client):
        """Test confirm action executes successfully."""
        from uuid import uuid4

        mock_client.close_issue.return_value = Issue(
            number=42,
            title="Test Issue",
            state=IssueState.CLOSED,
            html_url="https://github.com/owner/repo/issues/42",
        )
        skill._client = mock_client
        skill._status = SkillStatus.READY
        skill._autonomy_config.set_level(ActionType.CLOSE_ISSUE, AutonomyLevel.ASK)

        _, pending_id = await skill._check_autonomy(
            ActionType.CLOSE_ISSUE,
            "user123",
            "Close issue #42",
            "_execute_close_issue",
            {"request_id": uuid4(), "owner": "owner", "repo": "repo", "issue_number": 42},
        )

        response = await skill.confirm_action("user123", pending_id)

        assert response.success is True
        assert "Closed issue #42" in response.message


class TestGitHubSkillEmitEvent:
    """Tests for _emit_event."""

    @pytest.mark.asyncio
    async def test_emit_event_handler_error(self, skill, mock_client):
        """Test that event handler errors do not propagate."""
        from zetherion_ai.skills.github.models import GitHubEvent, GitHubEventType

        async def failing_handler(event):
            raise RuntimeError("Handler failed")

        skill.register_event_handler(failing_handler)

        # This should NOT raise, the error should be caught internally
        event = GitHubEvent(
            event_type=GitHubEventType.ISSUE_CREATED,
            repository="owner/repo",
            data={"test": True},
        )
        await skill._emit_event(event)
        # If we reach here, the exception was not propagated


class TestGitHubSkillAutonomyEdgeCases:
    """Tests for autonomy edge cases."""

    @pytest.mark.asyncio
    async def test_handle_set_autonomy_missing_params(self, skill, mock_client):
        """Test set autonomy fails when action or level is missing."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="set_autonomy",
            user_id="user123",
            context={},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert "Action and level required" in response.error

    @pytest.mark.asyncio
    async def test_handle_set_autonomy_invalid_enum(self, skill, mock_client):
        """Test set autonomy fails with invalid action string."""
        skill._client = mock_client
        skill._status = SkillStatus.READY

        request = SkillRequest(
            intent="set_autonomy",
            user_id="user123",
            context={"action": "invalid_action_xyz", "level": "autonomous"},
        )
        response = await skill.handle(request)

        assert response.success is False
        assert response.error is not None


class TestGitHubSkillHeartbeat:
    """Tests for on_heartbeat."""

    @pytest.mark.asyncio
    async def test_on_heartbeat_returns_empty(self, skill, mock_client):
        """Test heartbeat returns empty list."""
        result = await skill.on_heartbeat(["user123", "user456"])

        assert result == []


class TestGitHubSkillInitException:
    """Tests for initialization exception handling."""

    @pytest.mark.asyncio
    async def test_initialize_exception(self, skill):
        """Test initialization handles exceptions from verify_token."""
        mock_client = AsyncMock()
        mock_client.verify_token.side_effect = RuntimeError("Connection failed")

        with patch("zetherion_ai.skills.github.skill.GitHubClient", return_value=mock_client):
            result = await skill.initialize()

            assert result is False
            assert skill.status == SkillStatus.ERROR
