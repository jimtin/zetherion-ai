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
    User,
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
