"""Regression checks for the Windows worker bridge loopback contract."""

from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
DOCKER_COMPOSE_PATH = REPO_ROOT / "docker-compose.yml"
TRAEFIK_ROUTES_PATH = REPO_ROOT / "config" / "traefik" / "dynamic" / "updater-routes.yml"


def test_traefik_publishes_owner_ci_loopback_entrypoint() -> None:
    rendered = DOCKER_COMPOSE_PATH.read_text(encoding="utf-8")

    assert "container_name: zetherion-ai-traefik" in rendered
    assert "--entrypoints.skills.address=:8080" in rendered
    assert '"127.0.0.1:${OWNER_CI_LOOPBACK_PORT:-18443}:8443"' in rendered
    assert '"127.0.0.1:${TRAEFIK_DASHBOARD_PORT:-18080}:8080"' in rendered


def test_traefik_routes_worker_bridge_paths_to_skills_service() -> None:
    rendered = TRAEFIK_ROUTES_PATH.read_text(encoding="utf-8")

    assert "owner-ci-worker-bridge:" in rendered
    assert 'rule: "PathPrefix(`/owner/ci/worker/v1`)"' in rendered
    assert "worker-bridge:" in rendered
    assert 'rule: "PathPrefix(`/worker/v1`)"' in rendered
    assert "service: skills-blue" in rendered
