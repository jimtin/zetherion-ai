"""End-to-end integration tests for MilestoneSkill.

Exercises MilestoneSkill through both the SkillRegistry (direct request
handling) and over HTTP via SkillsServer + SkillsClient.  All tests run
with ``memory=None`` so no external services are required.
"""

import pytest
import pytest_asyncio
from aiohttp.test_utils import TestServer

from zetherion_ai.skills.base import SkillRequest
from zetherion_ai.skills.client import SkillsClient
from zetherion_ai.skills.milestone import DRAFT_THRESHOLD, PLATFORMS, MilestoneSkill
from zetherion_ai.skills.registry import SkillRegistry
from zetherion_ai.skills.server import SkillsServer

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

TEST_USER = "test-user-milestone"

TAG_EVENT_CONTEXT = {
    "event_type": "tag",
    "project": "zetherion-ai",
    "tag": "v1.0.0",
    "message": "Release v1.0.0 - First stable release with full skill framework",
    "commit_count": "150",
}


# ---------------------------------------------------------------------------
# Registry fixture
# ---------------------------------------------------------------------------


@pytest.fixture()
async def registry() -> SkillRegistry:
    """Build a SkillRegistry with MilestoneSkill registered and initialised."""
    reg = SkillRegistry()
    reg.register(MilestoneSkill(memory=None))

    init_results = await reg.initialize_all()
    assert all(init_results.values()), f"Some skills failed to initialise: {init_results}"

    return reg


# ---------------------------------------------------------------------------
# HTTP fixture (SkillsServer + SkillsClient)
# ---------------------------------------------------------------------------


@pytest_asyncio.fixture()
async def server_and_client() -> tuple[SkillsServer, SkillsClient]:
    """Start SkillsServer with MilestoneSkill on a random port and return (server, client)."""
    reg = SkillRegistry()
    reg.register(MilestoneSkill(memory=None))

    init_results = await reg.initialize_all()
    assert all(init_results.values()), f"Some skills failed to initialise: {init_results}"

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
# Registry-based tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_milestone_list_empty(registry: SkillRegistry) -> None:
    """milestone_list when no milestones exist should return a friendly message."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="milestone_list",
        message="Show my milestones",
        context={},
    )
    response = await registry.handle_request(request)

    assert response.success is True
    assert "No milestones detected" in response.message


@pytest.mark.integration
async def test_milestone_detect(registry: SkillRegistry) -> None:
    """milestone_detect with a tag event should detect a milestone (significance >= 6)."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="milestone_detect",
        message="Release v1.0.0 - First stable release with full skill framework",
        context=TAG_EVENT_CONTEXT,
    )
    response = await registry.handle_request(request)

    assert response.success is True
    assert "Milestone detected" in response.message
    assert "milestone" in response.data
    assert response.data["milestone"]["significance"] >= DRAFT_THRESHOLD
    assert response.data["milestone"]["category"] == "release"
    assert "drafts" in response.data
    assert len(response.data["drafts"]) == len(PLATFORMS)


@pytest.mark.integration
async def test_milestone_detect_then_list(registry: SkillRegistry) -> None:
    """After detecting a milestone it should appear in milestone_list."""
    # Detect
    detect_req = SkillRequest(
        user_id=TEST_USER,
        intent="milestone_detect",
        message="Release v1.0.0 - First stable release with full skill framework",
        context=TAG_EVENT_CONTEXT,
    )
    detect_resp = await registry.handle_request(detect_req)
    assert detect_resp.success is True

    # List
    list_req = SkillRequest(
        user_id=TEST_USER,
        intent="milestone_list",
        message="Show my milestones",
        context={},
    )
    list_resp = await registry.handle_request(list_req)

    assert list_resp.success is True
    assert "milestones" in list_resp.data
    assert len(list_resp.data["milestones"]) >= 1
    milestone_ids = [m["id"] for m in list_resp.data["milestones"]]
    assert detect_resp.data["milestone"]["id"] in milestone_ids


@pytest.mark.integration
async def test_milestone_drafts_empty(registry: SkillRegistry) -> None:
    """milestone_drafts when no drafts exist should return a friendly message."""
    request = SkillRequest(
        user_id=TEST_USER,
        intent="milestone_drafts",
        message="Show my drafts",
        context={},
    )
    response = await registry.handle_request(request)

    assert response.success is True
    assert "No pending promo drafts" in response.message


@pytest.mark.integration
async def test_milestone_drafts_after_detect(registry: SkillRegistry) -> None:
    """After detecting a milestone with significance >= 6, drafts should be created."""
    # Detect a tag event (significance 8 => above DRAFT_THRESHOLD)
    detect_req = SkillRequest(
        user_id=TEST_USER,
        intent="milestone_detect",
        message="Release v1.0.0 - First stable release with full skill framework",
        context=TAG_EVENT_CONTEXT,
    )
    detect_resp = await registry.handle_request(detect_req)
    assert detect_resp.success is True
    assert detect_resp.data["milestone"]["significance"] >= DRAFT_THRESHOLD

    # List drafts
    drafts_req = SkillRequest(
        user_id=TEST_USER,
        intent="milestone_drafts",
        message="Show my drafts",
        context={},
    )
    drafts_resp = await registry.handle_request(drafts_req)

    assert drafts_resp.success is True
    assert "drafts" in drafts_resp.data
    assert len(drafts_resp.data["drafts"]) == len(PLATFORMS)
    platforms_in_drafts = {d["platform"] for d in drafts_resp.data["drafts"]}
    assert platforms_in_drafts == set(PLATFORMS)


@pytest.mark.integration
async def test_milestone_approve_draft(registry: SkillRegistry) -> None:
    """Detect a milestone, get drafts, and approve one."""
    # Detect
    detect_resp = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="milestone_detect",
            message="Release v1.0.0 - First stable release with full skill framework",
            context=TAG_EVENT_CONTEXT,
        )
    )
    assert detect_resp.success is True

    # Get drafts
    drafts_resp = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="milestone_drafts",
            message="Show drafts",
            context={},
        )
    )
    assert drafts_resp.success is True
    draft_id = drafts_resp.data["drafts"][0]["id"]

    # Approve
    approve_resp = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="milestone_approve",
            message="Approve this draft",
            context={"draft_id": draft_id},
        )
    )

    assert approve_resp.success is True
    assert "Approved" in approve_resp.message
    assert approve_resp.data["draft"]["status"] == "approved"
    assert approve_resp.data["draft"]["id"] == draft_id


@pytest.mark.integration
async def test_milestone_reject_draft(registry: SkillRegistry) -> None:
    """Detect a milestone, get drafts, and reject one."""
    # Detect
    detect_resp = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="milestone_detect",
            message="Release v1.0.0 - First stable release with full skill framework",
            context=TAG_EVENT_CONTEXT,
        )
    )
    assert detect_resp.success is True

    # Get drafts
    drafts_resp = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="milestone_drafts",
            message="Show drafts",
            context={},
        )
    )
    assert drafts_resp.success is True
    draft_id = drafts_resp.data["drafts"][0]["id"]

    # Reject
    reject_resp = await registry.handle_request(
        SkillRequest(
            user_id=TEST_USER,
            intent="milestone_reject",
            message="Reject this draft",
            context={"draft_id": draft_id},
        )
    )

    assert reject_resp.success is True
    assert "Rejected" in reject_resp.message
    assert reject_resp.data["draft"]["status"] == "rejected"
    assert reject_resp.data["draft"]["id"] == draft_id


@pytest.mark.integration
async def test_milestone_settings(registry: SkillRegistry) -> None:
    """Verify MilestoneSkill settings via its metadata and module constants."""
    skill = registry.get_skill("milestone_tracker")
    assert skill is not None

    meta = skill.metadata
    assert meta.name == "milestone_tracker"
    assert meta.version == "1.0.0"
    assert set(meta.intents) == {
        "milestone_list",
        "milestone_drafts",
        "milestone_approve",
        "milestone_reject",
        "milestone_detect",
    }
    # Verify module-level configuration constants
    assert DRAFT_THRESHOLD == 6
    assert PLATFORMS == ["x", "linkedin", "github"]


@pytest.mark.integration
async def test_unknown_intent_error(registry: SkillRegistry) -> None:
    """An unknown milestone intent should return an error response."""
    skill = registry.get_skill("milestone_tracker")
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
async def test_registry_routes_milestone_intents(registry: SkillRegistry) -> None:
    """Registry should route all milestone intents to milestone_tracker."""
    intents = registry.list_intents()
    assert intents.get("milestone_list") == "milestone_tracker"
    assert intents.get("milestone_drafts") == "milestone_tracker"
    assert intents.get("milestone_approve") == "milestone_tracker"
    assert intents.get("milestone_reject") == "milestone_tracker"
    assert intents.get("milestone_detect") == "milestone_tracker"


# ---------------------------------------------------------------------------
# HTTP-based tests
# ---------------------------------------------------------------------------


@pytest.mark.integration
async def test_milestone_list_over_http(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """milestone_list over HTTP should succeed."""
    _server, client = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="milestone_list",
        message="Show my milestones",
        context={},
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert "No milestones detected" in response.message


@pytest.mark.integration
async def test_milestone_detect_over_http(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """milestone_detect over HTTP should detect a milestone from a tag event."""
    _server, client = server_and_client
    request = SkillRequest(
        user_id=TEST_USER,
        intent="milestone_detect",
        message="Release v1.0.0 - First stable release with full skill framework",
        context=TAG_EVENT_CONTEXT,
    )
    response = await client.handle_request(request)

    assert response.success is True
    assert "Milestone detected" in response.message
    assert "milestone" in response.data
    assert response.data["milestone"]["significance"] >= DRAFT_THRESHOLD
    assert "drafts" in response.data
    assert len(response.data["drafts"]) == len(PLATFORMS)


@pytest.mark.integration
async def test_milestone_heartbeat_over_http(
    server_and_client: tuple[SkillsServer, SkillsClient],
) -> None:
    """trigger_heartbeat over HTTP should return actions when drafts are pending."""
    _server, client = server_and_client

    # First detect a milestone to create pending drafts
    detect_req = SkillRequest(
        user_id=TEST_USER,
        intent="milestone_detect",
        message="Release v1.0.0 - First stable release with full skill framework",
        context=TAG_EVENT_CONTEXT,
    )
    detect_resp = await client.handle_request(detect_req)
    assert detect_resp.success is True

    # Trigger heartbeat
    actions = await client.trigger_heartbeat([TEST_USER])
    assert isinstance(actions, list)
    assert len(actions) >= 1

    # Find the milestone_drafts_pending action
    milestone_actions = [a for a in actions if a.action_type == "milestone_drafts_pending"]
    assert len(milestone_actions) == 1
    assert milestone_actions[0].skill_name == "milestone_tracker"
    assert milestone_actions[0].data["count"] == len(PLATFORMS)
