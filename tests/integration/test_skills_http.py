"""HTTP integration tests for Skills Server and Client.

Exercises real HTTP communication between ``SkillsClient`` (httpx) and
``SkillsServer`` (aiohttp) without Docker.  An ``aiohttp.test_utils.TestServer``
hosts the server in-process on a random port and a real ``SkillsClient`` is
pointed at it.
"""

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestServer

from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.calendar import CalendarSkill
from zetherion_ai.skills.client import SkillsAuthError, SkillsClient
from zetherion_ai.skills.profile_skill import ProfileSkill
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer
from zetherion_ai.skills.task_manager import TaskManagerSkill

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

TEST_USER = "user-1"


@pytest_asyncio.fixture()
async def real_registry() -> SkillRegistry:
    """Build a SkillRegistry with all three built-in skills initialised."""
    reg = SkillRegistry()

    reg.register(TaskManagerSkill(memory=None))
    reg.register(CalendarSkill(memory=None))
    reg.register(ProfileSkill(memory=None))

    init_results = await reg.initialize_all()
    assert all(init_results.values()), f"Some skills failed to initialise: {init_results}"

    return reg


@pytest_asyncio.fixture()
async def server_and_client(
    real_registry: SkillRegistry,
) -> tuple[SkillsServer, SkillsClient]:
    """Start SkillsServer on a random port and return (server, client)."""
    server = SkillsServer(registry=real_registry, api_secret="test-secret")
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
# Tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_health_check_over_http(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """Health-check endpoint should return True over real HTTP."""
    _server, client = server_and_client
    result = await client.health_check()
    assert result is True


@pytest.mark.integration
async def test_health_bypasses_auth(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """/health must be accessible even without an API secret."""
    server, _client = server_and_client

    # Build a *separate* client with NO api_secret
    no_auth_client = SkillsClient(
        base_url=_client._base_url,
        api_secret=None,
    )
    try:
        result = await no_auth_client.health_check()
        assert result is True
    finally:
        await no_auth_client.close()


@pytest.mark.integration
async def test_handle_request_create_task(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """create_task intent should succeed and return the task data."""
    _server, client = server_and_client
    request = SkillRequest(
        intent="create_task",
        user_id=TEST_USER,
        message="Buy milk",
        context={"title": "Buy milk"},
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert response.data["task"]["title"] == "Buy milk"


@pytest.mark.integration
async def test_handle_request_unknown_intent(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """An unrecognised intent should yield success=False with 'No skill found'."""
    _server, client = server_and_client
    request = SkillRequest(
        intent="nonexistent",
        user_id=TEST_USER,
        message="???",
        context={},
    )
    response = await client.handle_request(request)

    assert response.success is False
    assert "No skill found" in (response.error or "")


@pytest.mark.integration
async def test_auth_rejected_without_secret(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """A client with no api_secret must be rejected on authenticated endpoints."""
    _server, _client = server_and_client

    no_auth_client = SkillsClient(
        base_url=_client._base_url,
        api_secret=None,
    )
    try:
        with pytest.raises(SkillsAuthError):
            await no_auth_client.handle_request(
                SkillRequest(
                    intent="create_task",
                    user_id=TEST_USER,
                    message="Sneaky",
                    context={"title": "Sneaky"},
                )
            )
    finally:
        await no_auth_client.close()


@pytest.mark.integration
async def test_auth_rejected_wrong_secret(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """A client with an incorrect api_secret must be rejected."""
    _server, _client = server_and_client

    bad_client = SkillsClient(
        base_url=_client._base_url,
        api_secret="wrong-secret",
    )
    try:
        with pytest.raises(SkillsAuthError):
            await bad_client.handle_request(
                SkillRequest(
                    intent="create_task",
                    user_id=TEST_USER,
                    message="Sneaky",
                    context={"title": "Sneaky"},
                )
            )
    finally:
        await bad_client.close()


@pytest.mark.integration
async def test_trigger_heartbeat(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """trigger_heartbeat should return a (possibly empty) list."""
    _server, client = server_and_client
    actions = await client.trigger_heartbeat([TEST_USER])
    assert isinstance(actions, list)


@pytest.mark.integration
async def test_list_skills(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """list_skills should return 3 SkillMetadata objects for the built-in skills."""
    _server, client = server_and_client
    skills = await client.list_skills()

    assert len(skills) == 3
    names = {s.name for s in skills}
    assert names == {"task_manager", "calendar", "profile_manager"}


@pytest.mark.integration
async def test_get_skill_found(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """get_skill for an existing skill should return its metadata."""
    _server, client = server_and_client
    meta = await client.get_skill("calendar")

    assert meta is not None
    assert meta.name == "calendar"


@pytest.mark.integration
async def test_get_skill_not_found(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """get_skill for a missing skill should return None."""
    _server, client = server_and_client
    meta = await client.get_skill("nonexistent")
    assert meta is None


@pytest.mark.integration
async def test_get_status(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """get_status should report 3 total skills, all ready, 0 errors."""
    _server, client = server_and_client
    status = await client.get_status()

    assert status["total_skills"] == 3
    assert status["ready_count"] == 3
    assert status["error_count"] == 0


@pytest.mark.integration
async def test_get_prompt_fragments(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """get_prompt_fragments should return a list of strings."""
    _server, client = server_and_client
    fragments = await client.get_prompt_fragments(TEST_USER)
    assert isinstance(fragments, list)
    for frag in fragments:
        assert isinstance(frag, str)


@pytest.mark.integration
async def test_full_task_lifecycle(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """Full lifecycle: create -> list -> complete -> summary (all over HTTP)."""
    _server, client = server_and_client

    # 1. Create a task
    create_resp = await client.handle_request(
        SkillRequest(
            intent="create_task",
            user_id=TEST_USER,
            message="Lifecycle task",
            context={"title": "Lifecycle task"},
        )
    )
    assert create_resp.success is True
    task_id = create_resp.data["task"]["id"]

    # 2. List tasks -- the new task must be present
    list_resp = await client.handle_request(
        SkillRequest(
            intent="list_tasks",
            user_id=TEST_USER,
            message="Show tasks",
            context={},
        )
    )
    assert list_resp.success is True
    assert any(t["id"] == task_id for t in list_resp.data["tasks"])

    # 3. Complete the task
    complete_resp = await client.handle_request(
        SkillRequest(
            intent="complete_task",
            user_id=TEST_USER,
            message="Done",
            context={"task_id": task_id},
        )
    )
    assert complete_resp.success is True
    assert complete_resp.data["task"]["status"] == "done"

    # 4. Summary -- done count must be >= 1
    summary_resp = await client.handle_request(
        SkillRequest(
            intent="task_summary",
            user_id=TEST_USER,
            message="Summary",
            context={},
        )
    )
    assert summary_resp.success is True
    by_status = summary_resp.data["summary"]["by_status"]
    assert by_status.get("done", 0) >= 1
