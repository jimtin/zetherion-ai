"""Static contract checks for skills server wiring."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[2]
SKILLS_SERVER_PATH = REPO_ROOT / "src" / "zetherion_ai" / "skills" / "server.py"


def test_personal_model_pool_uses_postgres_tls_context() -> None:
    server_text = SKILLS_SERVER_PATH.read_text(encoding="utf-8")

    assert "personal_db_pool = await _personal_pool_factory(" in server_text
    assert "ssl=settings.postgres_ssl_context," in server_text
