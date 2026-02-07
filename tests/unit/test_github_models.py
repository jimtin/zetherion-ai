"""Tests for GitHub skill data models."""

from datetime import UTC, datetime

from zetherion_ai.skills.github.models import (
    ALWAYS_ASK_ACTIONS,
    DEFAULT_AUTONOMY,
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
    WorkflowConclusion,
    WorkflowRun,
    WorkflowStatus,
)


class TestUser:
    """Tests for User model."""

    def test_from_api(self):
        """Test creating User from API response."""
        data = {
            "login": "testuser",
            "id": 12345,
            "avatar_url": "https://avatars.githubusercontent.com/u/12345",
            "html_url": "https://github.com/testuser",
            "name": "Test User",
            "email": "test@example.com",
        }
        user = User.from_api(data)

        assert user.login == "testuser"
        assert user.id == 12345
        assert user.name == "Test User"
        assert user.email == "test@example.com"

    def test_to_dict(self):
        """Test converting User to dictionary."""
        user = User(login="testuser", id=12345, name="Test User")
        result = user.to_dict()

        assert result["login"] == "testuser"
        assert result["id"] == 12345
        assert result["name"] == "Test User"


class TestLabel:
    """Tests for Label model."""

    def test_from_api(self):
        """Test creating Label from API response."""
        data = {
            "name": "bug",
            "color": "d73a4a",
            "description": "Something isn't working",
            "id": 123,
        }
        label = Label.from_api(data)

        assert label.name == "bug"
        assert label.color == "d73a4a"
        assert label.description == "Something isn't working"

    def test_from_api_missing_description(self):
        """Test Label handles missing/null description."""
        data = {"name": "bug", "color": "d73a4a", "description": None}
        label = Label.from_api(data)

        assert label.description == ""


class TestRepository:
    """Tests for Repository model."""

    def test_from_api(self):
        """Test creating Repository from API response."""
        data = {
            "name": "test-repo",
            "full_name": "owner/test-repo",
            "owner": {"login": "owner"},
            "description": "A test repository",
            "html_url": "https://github.com/owner/test-repo",
            "default_branch": "main",
            "private": False,
            "fork": False,
            "archived": False,
            "open_issues_count": 5,
            "stargazers_count": 100,
            "forks_count": 10,
        }
        repo = Repository.from_api(data)

        assert repo.name == "test-repo"
        assert repo.owner == "owner"
        assert repo.full_name == "owner/test-repo"
        assert repo.open_issues_count == 5

    def test_full_name_auto_generated(self):
        """Test full_name is generated if not provided."""
        repo = Repository(owner="myowner", name="myrepo")
        assert repo.full_name == "myowner/myrepo"


class TestIssue:
    """Tests for Issue model."""

    def test_from_api(self):
        """Test creating Issue from API response."""
        data = {
            "number": 42,
            "title": "Test Issue",
            "body": "This is a test issue",
            "state": "open",
            "html_url": "https://github.com/owner/repo/issues/42",
            "user": {"login": "testuser", "id": 123},
            "labels": [{"name": "bug", "color": "d73a4a"}],
            "assignees": [],
            "comments": 5,
            "created_at": "2024-01-15T10:00:00Z",
            "updated_at": "2024-01-16T10:00:00Z",
        }
        issue = Issue.from_api(data, repository="owner/repo")

        assert issue.number == 42
        assert issue.title == "Test Issue"
        assert issue.state == IssueState.OPEN
        assert issue.repository == "owner/repo"
        assert len(issue.labels) == 1
        assert issue.labels[0].name == "bug"

    def test_format_summary(self):
        """Test issue summary formatting."""
        issue = Issue(
            number=42,
            title="Test Issue",
            state=IssueState.OPEN,
            labels=[Label(name="bug")],
            comments=5,
        )
        summary = issue.format_summary()

        assert "#42" in summary
        assert "Test Issue" in summary
        assert "bug" in summary

    def test_filters_out_prs(self):
        """Test PR detection from URL."""
        issue = Issue(
            number=1,
            title="Test",
            html_url="https://github.com/owner/repo/issues/1",
        )
        assert issue.repository == ""  # Not auto-derived when html_url not parsed


class TestPullRequest:
    """Tests for PullRequest model."""

    def test_from_api_open(self):
        """Test creating open PR from API response."""
        data = {
            "number": 45,
            "title": "Add feature",
            "body": "This adds a new feature",
            "state": "open",
            "html_url": "https://github.com/owner/repo/pull/45",
            "user": {"login": "contributor", "id": 456},
            "head": {"ref": "feature-branch"},
            "base": {"ref": "main"},
            "draft": False,
            "mergeable": True,
            "additions": 100,
            "deletions": 20,
            "changed_files": 5,
            "commits": 3,
        }
        pr = PullRequest.from_api(data, repository="owner/repo")

        assert pr.number == 45
        assert pr.state == PRState.OPEN
        assert pr.head_ref == "feature-branch"
        assert pr.base_ref == "main"
        assert pr.additions == 100

    def test_from_api_merged(self):
        """Test merged PR detection."""
        data = {
            "number": 45,
            "title": "Merged PR",
            "state": "closed",
            "merged_at": "2024-01-15T10:00:00Z",
            "head": {"ref": "feature"},
            "base": {"ref": "main"},
        }
        pr = PullRequest.from_api(data)

        assert pr.state == PRState.MERGED

    def test_format_summary(self):
        """Test PR summary formatting."""
        pr = PullRequest(
            number=45,
            title="Add feature",
            head_ref="feature",
            base_ref="main",
            additions=100,
            deletions=20,
            changed_files=5,
            created_at=datetime.now(UTC),
        )
        summary = pr.format_summary()

        assert "#45" in summary
        assert "+100/-20" in summary
        assert "feature -> main" in summary


class TestWorkflowRun:
    """Tests for WorkflowRun model."""

    def test_from_api(self):
        """Test creating WorkflowRun from API response."""
        data = {
            "id": 12345,
            "name": "CI",
            "workflow_id": 100,
            "head_branch": "main",
            "head_sha": "abc1234567890",
            "status": "completed",
            "conclusion": "success",
            "html_url": "https://github.com/owner/repo/actions/runs/12345",
            "event": "push",
            "run_attempt": 1,
        }
        run = WorkflowRun.from_api(data, repository="owner/repo")

        assert run.id == 12345
        assert run.name == "CI"
        assert run.status == WorkflowStatus.COMPLETED
        assert run.conclusion == WorkflowConclusion.SUCCESS
        assert run.head_sha == "abc1234"  # Truncated

    def test_format_summary(self):
        """Test workflow run summary formatting."""
        run = WorkflowRun(
            id=12345,
            name="CI",
            workflow_id=100,
            head_branch="main",
            head_sha="abc1234",
            status=WorkflowStatus.COMPLETED,
            conclusion=WorkflowConclusion.SUCCESS,
            event="push",
        )
        summary = run.format_summary()

        assert "CI" in summary
        assert "main" in summary
        assert "success" in summary


class TestGitHubEvent:
    """Tests for GitHubEvent model."""

    def test_to_dict_and_from_dict(self):
        """Test round-trip serialization."""
        event = GitHubEvent(
            event_type=GitHubEventType.ISSUE_CREATED,
            repository="owner/repo",
            data={"issue_number": 42},
            user_id="user123",
        )
        data = event.to_dict()
        restored = GitHubEvent.from_dict(data)

        assert restored.event_type == event.event_type
        assert restored.repository == event.repository
        assert restored.data == event.data
        assert restored.user_id == event.user_id


class TestAutonomyConfig:
    """Tests for AutonomyConfig model."""

    def test_default_config(self):
        """Test default autonomy configuration."""
        config = AutonomyConfig()

        # Read-only should be autonomous
        assert config.is_autonomous(ActionType.LIST_ISSUES)
        assert config.is_autonomous(ActionType.GET_PR)

        # High-risk should require confirmation
        assert config.requires_confirmation(ActionType.MERGE_PR)
        assert config.requires_confirmation(ActionType.CLOSE_ISSUE)

        # Dangerous should always ask
        assert config.requires_confirmation(ActionType.DELETE_REPO)
        assert config.get_level(ActionType.DELETE_REPO) == AutonomyLevel.ALWAYS_ASK

    def test_set_level_normal(self):
        """Test setting autonomy level for normal action."""
        config = AutonomyConfig()

        # Can change high-risk to autonomous
        result = config.set_level(ActionType.CLOSE_ISSUE, AutonomyLevel.AUTONOMOUS)
        assert result is True
        assert config.is_autonomous(ActionType.CLOSE_ISSUE)

    def test_set_level_always_ask_blocked(self):
        """Test cannot set ALWAYS_ASK actions to autonomous."""
        config = AutonomyConfig()

        # Cannot change dangerous actions
        result = config.set_level(ActionType.DELETE_REPO, AutonomyLevel.AUTONOMOUS)
        assert result is False
        assert config.get_level(ActionType.DELETE_REPO) == AutonomyLevel.ALWAYS_ASK

    def test_to_dict_and_from_dict(self):
        """Test round-trip serialization."""
        config = AutonomyConfig()
        config.set_level(ActionType.CLOSE_ISSUE, AutonomyLevel.AUTONOMOUS)

        data = config.to_dict()
        restored = AutonomyConfig.from_dict(data)

        assert restored.is_autonomous(ActionType.CLOSE_ISSUE)

    def test_always_ask_actions(self):
        """Test ALWAYS_ASK_ACTIONS contains dangerous operations."""
        assert ActionType.DELETE_REPO in ALWAYS_ASK_ACTIONS
        assert ActionType.FORCE_PUSH in ALWAYS_ASK_ACTIONS
        assert ActionType.TRANSFER_REPO in ALWAYS_ASK_ACTIONS

    def test_default_autonomy_complete(self):
        """Test all ActionTypes have default autonomy."""
        for action in ActionType:
            assert action in DEFAULT_AUTONOMY, f"Missing default for {action}"
