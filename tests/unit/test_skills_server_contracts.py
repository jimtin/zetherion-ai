"""Static contract checks for skills server wiring."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_SERVER_PATH = REPO_ROOT / "src" / "zetherion_ai" / "skills" / "server.py"
MAIN_PATH = REPO_ROOT / "src" / "zetherion_ai" / "main.py"
DEV_AGENT_DOCKERFILE_PATH = REPO_ROOT / "Dockerfile.dev-agent"
UPDATER_DOCKERFILE_PATH = REPO_ROOT / "Dockerfile.updater"


def test_personal_model_pool_uses_postgres_tls_context() -> None:
    server_text = SKILLS_SERVER_PATH.read_text(encoding="utf-8")

    assert "personal_db_pool = await _personal_pool_factory(" in server_text
    assert "ssl=settings.postgres_ssl_context," in server_text


def test_main_bootstraps_embedding_model_setting() -> None:
    main_text = MAIN_PATH.read_text(encoding="utf-8")

    assert '("embedding_model", getattr(settings, "embedding_model", None))' in main_text


def test_sidecar_healthchecks_use_https_with_client_cert() -> None:
    dev_agent_text = DEV_AGENT_DOCKERFILE_PATH.read_text(encoding="utf-8")
    updater_text = UPDATER_DOCKERFILE_PATH.read_text(encoding="utf-8")

    assert 'https://localhost:8787/v1/health' in dev_agent_text
    assert '--cacert", "/app/data/certs/internal/ca.pem"' in dev_agent_text
    assert '--cert", "/app/data/certs/internal/client.pem"' in dev_agent_text
    assert '--key", "/app/data/certs/internal/client-key.pem"' in dev_agent_text

    assert 'https://localhost:9090/health' in updater_text
    assert '--cacert", "/app/data/certs/internal/ca.pem"' in updater_text
    assert '--cert", "/app/data/certs/internal/client.pem"' in updater_text
    assert '--key", "/app/data/certs/internal/client-key.pem"' in updater_text
