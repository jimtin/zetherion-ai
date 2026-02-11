"""Docker E2E tests for the auto-update system.

Exercises the UpdateCheckerSkill through the real skills server running inside
the Docker Compose test environment (``docker-compose.test.yml``).  Tests hit
the HTTP endpoints exposed on port 18080 with ``httpx`` sync calls.

Requires the full Docker test stack to be running.  When Docker is not
available or ``SKIP_INTEGRATION_TESTS=true``, all tests are skipped gracefully.
"""

from __future__ import annotations

import os
import subprocess

import httpx
import pytest

SKILLS_URL = "http://localhost:18080"
SKIP_INTEGRATION = os.getenv("SKIP_INTEGRATION_TESTS", "false").lower() == "true"


# ---------------------------------------------------------------------------
# Helpers / Fixtures
# ---------------------------------------------------------------------------


def _get_api_secret() -> str | None:
    """Try to extract API secret from the skills container environment."""
    try:
        result = subprocess.run(
            [
                "docker",
                "exec",
                "zetherion-ai-test-skills",
                "printenv",
                "SKILLS_API_SECRET",
            ],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return None


def _headers(secret: str | None) -> dict[str, str]:
    """Build request headers including the API secret when available."""
    if secret:
        return {"X-API-Secret": secret}
    return {}


@pytest.fixture(scope="module")
def api_secret() -> str | None:
    """Module-scoped fixture that verifies Docker connectivity and returns the API secret.

    Skips the entire module when the integration stack is unreachable or
    integration tests are disabled via environment variable.
    """
    if SKIP_INTEGRATION:
        pytest.skip("Integration tests disabled")

    # Verify skills service is reachable
    try:
        resp = httpx.get(f"{SKILLS_URL}/health", timeout=5)
        if resp.status_code != 200:
            pytest.skip("Skills service not reachable")
    except httpx.RequestError:
        pytest.skip("Skills service not reachable")

    return _get_api_secret()


# ---------------------------------------------------------------------------
# 1. UpdateCheckerSkill is registered
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_update_checker_registered(api_secret: str | None) -> None:
    """GET /status should list update_checker as a registered skill."""
    resp = httpx.get(
        f"{SKILLS_URL}/status",
        headers=_headers(api_secret),
        timeout=10,
    )
    assert resp.status_code == 200, f"Unexpected status: {resp.status_code}"

    data = resp.json()
    # The status summary includes a by_status map; update_checker must appear
    # in at least one of the skill lists (ideally "ready").
    by_status = data.get("by_status", {})
    all_skill_names: list[str] = []
    for skill_list in by_status.values():
        if isinstance(skill_list, list):
            all_skill_names.extend(skill_list)

    assert (
        "update_checker" in all_skill_names
    ), f"update_checker not found in status skills: {by_status}"


# ---------------------------------------------------------------------------
# 2. update_status intent
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_update_status_intent(api_secret: str | None) -> None:
    """POST /handle with intent=update_status returns version info."""
    resp = httpx.post(
        f"{SKILLS_URL}/handle",
        headers=_headers(api_secret),
        json={
            "user_id": "test",
            "intent": "update_status",
            "message": "Show update status",
        },
        timeout=10,
    )
    assert resp.status_code == 200

    body = resp.json()
    assert body.get("success") is True, f"Expected success, got: {body}"
    data = body.get("data", {})
    assert "current_version" in data, f"Missing current_version in: {data}"
    assert "enabled" in data, f"Missing enabled in: {data}"
    assert "repo" in data, f"Missing repo in: {data}"


# ---------------------------------------------------------------------------
# 3. check_update intent
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_check_update_intent(api_secret: str | None) -> None:
    """POST /handle with intent=check_update returns up-to-date or available info.

    In the test environment the skill may not have a real GitHub repo
    configured, so it might respond with an error or an 'up to date' message.
    Either is acceptable as long as the response structure is valid.
    """
    resp = httpx.post(
        f"{SKILLS_URL}/handle",
        headers=_headers(api_secret),
        json={
            "user_id": "test",
            "intent": "check_update",
            "message": "Check for updates",
        },
        timeout=15,
    )
    assert resp.status_code == 200

    body = resp.json()
    # The skill returns success=True with "up to date" when no newer release
    # exists, or an error response if the manager is not configured.
    # Both are valid in the Docker test environment.
    message = body.get("message", "").lower()
    if body.get("success"):
        assert (
            "up to date" in message or "update available" in message
        ), f"Unexpected check_update message: {message}"
    else:
        # Not configured is acceptable in test environment
        assert "not configured" in message or "error" in body, f"Unexpected error response: {body}"


# ---------------------------------------------------------------------------
# 4. apply_update with no pending release
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_apply_update_no_pending(api_secret: str | None) -> None:
    """POST /handle with intent=apply_update when there is no pending update."""
    resp = httpx.post(
        f"{SKILLS_URL}/handle",
        headers=_headers(api_secret),
        json={
            "user_id": "test",
            "intent": "apply_update",
            "message": "Apply the update",
        },
        timeout=15,
    )
    assert resp.status_code == 200

    body = resp.json()
    message = body.get("message", "").lower()
    # When there is nothing to apply the skill says "No update available",
    # returns an error if the manager is not configured, or simply returns
    # success=False with no message.
    assert (
        "no update" in message or "not configured" in message or body.get("success") is False
    ), f"Expected 'no update', 'not configured', or success=False, got: {body}"


# ---------------------------------------------------------------------------
# 5. rollback_update with no history
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_rollback_no_history(api_secret: str | None) -> None:
    """POST /handle with intent=rollback_update when there is no rollback history."""
    resp = httpx.post(
        f"{SKILLS_URL}/handle",
        headers=_headers(api_secret),
        json={
            "user_id": "test",
            "intent": "rollback_update",
            "message": "Rollback",
        },
        timeout=15,
    )
    assert resp.status_code == 200

    body = resp.json()
    # The skill should return success=False since there is no history
    # or error if not configured.
    message = body.get("message", "").lower()
    error = (body.get("error") or "").lower()
    combined = f"{message} {error}"
    assert (
        "no update history" in combined
        or "not configured" in combined
        or "no history" in combined
        or body.get("success") is False
    ), f"Expected rollback rejection, got: {body}"


# ---------------------------------------------------------------------------
# 6. All update intents listed in /intents
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_update_intents_present(api_secret: str | None) -> None:
    """GET /intents should include all four update-related intents."""
    resp = httpx.get(
        f"{SKILLS_URL}/intents",
        headers=_headers(api_secret),
        timeout=10,
    )
    assert resp.status_code == 200

    intents = resp.json().get("intents", {})
    for intent in ("check_update", "apply_update", "rollback_update", "update_status"):
        assert (
            intent in intents
        ), f"Intent '{intent}' not found in /intents response: {list(intents.keys())}"
        assert (
            intents[intent] == "update_checker"
        ), f"Intent '{intent}' mapped to '{intents[intent]}', expected 'update_checker'"


# ---------------------------------------------------------------------------
# 7. Heartbeat returns 200
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_heartbeat_update_check(api_secret: str | None) -> None:
    """POST /heartbeat should return 200.

    The update check runs every 6th heartbeat, so the first beat will not
    trigger an actual update check.  We just verify the endpoint is functional
    and returns a well-formed response.
    """
    resp = httpx.post(
        f"{SKILLS_URL}/heartbeat",
        headers=_headers(api_secret),
        json={"user_ids": ["test"]},
        timeout=10,
    )
    assert resp.status_code == 200

    body = resp.json()
    assert "actions" in body, f"Missing 'actions' key in heartbeat response: {body}"
    assert isinstance(
        body["actions"], list
    ), f"Expected 'actions' to be a list, got: {type(body['actions'])}"


# ---------------------------------------------------------------------------
# 8. Version info in prompt fragments
# ---------------------------------------------------------------------------


@pytest.mark.integration
def test_version_in_prompt_fragments(api_secret: str | None) -> None:
    """GET /prompt-fragments?user_id=test should include version info."""
    resp = httpx.get(
        f"{SKILLS_URL}/prompt-fragments",
        params={"user_id": "test"},
        headers=_headers(api_secret),
        timeout=10,
    )
    assert resp.status_code == 200

    body = resp.json()
    fragments = body.get("fragments", [])
    assert isinstance(fragments, list), f"Expected list, got: {type(fragments)}"

    # The UpdateCheckerSkill injects a "[Version] v..." fragment
    version_fragments = [f for f in fragments if "version" in f.lower()]
    assert len(version_fragments) >= 1, f"No version info found in prompt fragments: {fragments}"
