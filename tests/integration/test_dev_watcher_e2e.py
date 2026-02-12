"""End-to-end integration tests for DevWatcherSkill.

Exercises DevWatcherSkill through the SkillRegistry (registry-based tests)
and through real HTTP communication via SkillsServer + SkillsClient
(HTTP-based tests).  All tests run with ``memory=None`` so no external
services are required.
"""

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestServer

from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.client import SkillsClient
from zetherion_ai.skills.dev_watcher import DevWatcherSkill
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_USER = "test-user-dev"


@pytest.fixture()
async def registry() -> SkillRegistry:
    """Build a SkillRegistry with DevWatcherSkill registered and initialised."""
    reg = SkillRegistry()

    reg.register(DevWatcherSkill(memory=None))

    init_results = await reg.initialize_all()
    assert all(init_results.values()), f"Some skills failed to initialise: {init_results}"

    return reg


@pytest_asyncio.fixture()
async def server_and_client() -> tuple[SkillsServer, SkillsClient]:
    """Start SkillsServer on a random port and return (server, client)."""
    reg = SkillRegistry()
    reg.register(DevWatcherSkill(memory=None))
    init_results = await reg.initialize_all()
    assert all(init_results.values())

    server = SkillsServer(registry=reg, api_secret="test-secret")
    app = server.create_app()

    test_server = TestServer(app)
    await test_server.start_server()

    client = SkillsClient(
        base_url=f"http://{test_server.host}:{test_server.port}",
        api_secret="test-secret",
    )

    yield server, client  # type: ignore[misc]

    await client.close()
    await test_server.close()


# ---------------------------------------------------------------------------
# Registry-based tests — ingestion
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_dev_ingest_commit(registry: SkillRegistry) -> None:
    """dev_ingest_commit should ingest a commit and return success with data."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="dev_ingest_commit",
        message="feat: add new feature",
        context={
            "event_type": "commit",
            "project": "zetherion-ai",
            "sha": "abc123def456",
            "message": "feat: add new feature",
            "files_changed": "3",
            "diff_summary": "+45 -12",
        },
    )

    response = await registry.handle_request(request)

    assert response.success is True
    assert "Ingested commit" in response.message
    assert "feat: add new feature" in response.message


@pytest.mark.integration
async def test_dev_ingest_annotation(registry: SkillRegistry) -> None:
    """dev_ingest_annotation should ingest an annotation event."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="dev_ingest_annotation",
        message="Consider using async here",
        context={
            "event_type": "annotation",
            "project": "zetherion-ai",
            "annotation_type": "IDEA",
            "file": "src/main.py",
            "line": "42",
            "text": "Consider using async here",
        },
    )

    response = await registry.handle_request(request)

    assert response.success is True
    assert "IDEA" in response.message
    assert "annotation" in response.message.lower()


@pytest.mark.integration
async def test_dev_ingest_session(registry: SkillRegistry) -> None:
    """dev_ingest_session should ingest a Claude Code session."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="dev_ingest_session",
        message="Refactored auth module and added tests",
        context={
            "event_type": "session",
            "project": "zetherion-ai",
            "summary": "Refactored auth module",
            "session_id": "sess-001",
            "duration_minutes": 45,
            "tools_used": 12,
        },
    )

    response = await registry.handle_request(request)

    assert response.success is True
    assert "session" in response.message.lower()


@pytest.mark.integration
async def test_dev_ingest_tag(registry: SkillRegistry) -> None:
    """dev_ingest_tag should ingest a tag event."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="dev_ingest_tag",
        message="Release v1.2.0",
        context={
            "event_type": "tag",
            "project": "zetherion-ai",
            "tag_name": "v1.2.0",
            "sha": "def789",
        },
    )

    response = await registry.handle_request(request)

    assert response.success is True
    assert "v1.2.0" in response.message


# ---------------------------------------------------------------------------
# Registry-based tests — queries
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_dev_status(registry: SkillRegistry) -> None:
    """dev_status with no data should return a message about no activity."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="dev_status",
        message="What am I working on?",
        context={},
    )

    response = await registry.handle_request(request)

    assert response.success is True
    assert "No recent development activity" in response.message


@pytest.mark.integration
async def test_dev_next(registry: SkillRegistry) -> None:
    """dev_next with no data should return suggestions message."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="dev_next",
        message="What should I work on next?",
        context={},
    )

    response = await registry.handle_request(request)

    assert response.success is True
    # Either suggestions or a "no open items" message
    assert "Suggestions" in response.message or "No open items" in response.message


@pytest.mark.integration
async def test_dev_ideas(registry: SkillRegistry) -> None:
    """dev_ideas with no data should indicate no active ideas."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="dev_ideas",
        message="What ideas have I had?",
        context={},
    )

    response = await registry.handle_request(request)

    assert response.success is True
    assert "No active ideas" in response.message


@pytest.mark.integration
async def test_dev_journal(registry: SkillRegistry) -> None:
    """dev_journal with no data should return a no-entries message."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="dev_journal",
        message="Show my journal",
        context={},
    )

    response = await registry.handle_request(request)

    assert response.success is True
    assert "No journal entries" in response.message


@pytest.mark.integration
async def test_dev_summary(registry: SkillRegistry) -> None:
    """dev_summary with no data should return a no-activity message."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="dev_summary",
        message="Give me a dev summary",
        context={},
    )

    response = await registry.handle_request(request)

    assert response.success is True
    assert "No dev activity" in response.message


# ---------------------------------------------------------------------------
# Registry-based tests — stateful flows
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_dev_commit_then_status(registry: SkillRegistry) -> None:
    """After ingesting a commit, dev_status should include it."""
    # Ingest a commit
    commit_req = SkillRequest(
        user_id=TEST_USER,
        intent="dev_ingest_commit",
        message="fix: resolve login bug",
        context={
            "event_type": "commit",
            "project": "zetherion-ai",
            "sha": "aaa111bbb",
            "message": "fix: resolve login bug",
            "files_changed": "2",
            "diff_summary": "+10 -5",
        },
    )
    commit_resp = await registry.handle_request(commit_req)
    assert commit_resp.success is True

    # Query status
    status_req = SkillRequest(
        user_id=TEST_USER,
        intent="dev_status",
        message="What am I working on?",
        context={},
    )
    status_resp = await registry.handle_request(status_req)

    assert status_resp.success is True
    assert "Current Dev Activity" in status_resp.message
    assert "fix: resolve login bug" in status_resp.message
    assert "entries" in status_resp.data
    assert len(status_resp.data["entries"]) >= 1


@pytest.mark.integration
async def test_dev_annotation_then_ideas(registry: SkillRegistry) -> None:
    """After ingesting an IDEA annotation, dev_ideas should list it."""
    # Ingest an IDEA annotation
    annotation_req = SkillRequest(
        user_id=TEST_USER,
        intent="dev_ingest_annotation",
        message="Consider using async here",
        context={
            "event_type": "annotation",
            "project": "zetherion-ai",
            "annotation_type": "IDEA",
            "file": "src/main.py",
            "line": "42",
            "text": "Consider using async here",
        },
    )
    annotation_resp = await registry.handle_request(annotation_req)
    assert annotation_resp.success is True

    # Query ideas
    ideas_req = SkillRequest(
        user_id=TEST_USER,
        intent="dev_ideas",
        message="What ideas have I had?",
        context={},
    )
    ideas_resp = await registry.handle_request(ideas_req)

    assert ideas_resp.success is True
    assert "Captured Ideas" in ideas_resp.message
    assert "Consider using async here" in ideas_resp.message
    assert "ideas" in ideas_resp.data
    assert len(ideas_resp.data["ideas"]) >= 1


# ---------------------------------------------------------------------------
# Registry-based tests — error handling & routing
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_unknown_intent_error(registry: SkillRegistry) -> None:
    """An unknown intent should return an error response."""
    skill = registry.get_skill("dev_watcher")
    assert skill is not None

    response = await skill.safe_handle(
        SkillRequest(
            user_id=TEST_USER,
            intent="nonexistent_intent",
            message="bad intent",
            context={},
        )
    )

    assert response.success is False
    assert "Unknown intent" in (response.error or response.message)


@pytest.mark.integration
async def test_registry_routes_dev_intents(registry: SkillRegistry) -> None:
    """Registry should route all dev intents to dev_watcher."""
    intents = registry.list_intents()

    expected_intents = [
        "dev_ingest_commit",
        "dev_ingest_annotation",
        "dev_ingest_session",
        "dev_ingest_tag",
        "dev_status",
        "dev_next",
        "dev_ideas",
        "dev_journal",
        "dev_summary",
    ]

    for intent in expected_intents:
        assert intents.get(intent) == "dev_watcher", f"Intent '{intent}' not routed to dev_watcher"


# ---------------------------------------------------------------------------
# HTTP-based tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_dev_ingest_commit_over_http(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """dev_ingest_commit should succeed over real HTTP."""
    _server, client = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="dev_ingest_commit",
        message="feat: add HTTP test",
        context={
            "event_type": "commit",
            "project": "zetherion-ai",
            "sha": "http123",
            "message": "feat: add HTTP test",
            "files_changed": "1",
            "diff_summary": "+20 -0",
        },
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert "Ingested commit" in response.message


@pytest.mark.integration
async def test_dev_status_over_http(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """dev_status should succeed over real HTTP (initially empty)."""
    _server, client = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="dev_status",
        message="What am I working on?",
        context={},
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert "No recent development activity" in response.message


@pytest.mark.integration
async def test_dev_heartbeat_over_http(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """trigger_heartbeat should return a list over real HTTP."""
    _server, client = server_and_client
    actions = await client.trigger_heartbeat([TEST_USER])
    assert isinstance(actions, list)
