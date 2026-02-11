"""Unit tests for the interactive setup script.

Tests the generate_env_file() function to verify that encryption passphrase,
Ollama model configuration, and default values are written correctly.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest


@pytest.fixture
def setup_module() -> ModuleType:
    """Import the interactive-setup module from scripts/.

    The script contains ``callable | None`` annotations which require
    deferred evaluation (PEP 563).  We compile the source with the
    ``annotations`` future flag so it works on every Python 3.12+ version.
    """
    script_path = Path(__file__).resolve().parents[2] / "scripts" / "interactive-setup.py"
    source = script_path.read_text()

    # Prepend the future import so annotations are strings and `callable | None` is fine.
    source = "from __future__ import annotations\n" + source

    code = compile(source, str(script_path), "exec")
    module = ModuleType("interactive_setup")
    module.__file__ = str(script_path)
    exec(code, module.__dict__)  # noqa: S102
    return module


@pytest.fixture
def env_example_content() -> str:
    """Minimal .env.example content for testing."""
    return (
        "DISCORD_TOKEN=\n"
        "GEMINI_API_KEY=\n"
        "ANTHROPIC_API_KEY=\n"
        "OPENAI_API_KEY=\n"
        "ROUTER_BACKEND=gemini\n"
        "OLLAMA_ROUTER_MODEL=llama3.2:3b\n"
        "OLLAMA_GENERATION_MODEL=llama3.1:8b\n"
        "OLLAMA_DOCKER_MEMORY=8\n"
        "ENCRYPTION_PASSPHRASE=\n"
    )


class TestGenerateEnvFile:
    """Tests for the generate_env_file() function."""

    def test_encryption_passphrase_written(
        self, tmp_path, setup_module, env_example_content, monkeypatch
    ) -> None:
        """ENCRYPTION_PASSPHRASE should be written to .env when configured."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text(env_example_content)

        config = {
            "discord_token": "tok_123",
            "gemini_key": "AIzaSyXXX",
            "router_backend": "gemini",
            "encryption_passphrase": "my-super-secret-passphrase-32",
        }

        result = setup_module.generate_env_file(config)
        assert result is True

        env_content = (tmp_path / ".env").read_text()
        assert "ENCRYPTION_PASSPHRASE=my-super-secret-passphrase-32" in env_content

    def test_ollama_generation_model_written_when_ollama_backend(
        self, tmp_path, setup_module, env_example_content, monkeypatch
    ) -> None:
        """OLLAMA_GENERATION_MODEL should be written when router_backend=ollama."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text(env_example_content)

        config = {
            "discord_token": "tok_123",
            "gemini_key": "AIzaSyXXX",
            "router_backend": "ollama",
            "ollama_model": "mistral:7b",
            "docker_memory": 16,
            "encryption_passphrase": "test-passphrase-long-enough",
        }

        result = setup_module.generate_env_file(config)
        assert result is True

        env_content = (tmp_path / ".env").read_text()
        assert "OLLAMA_GENERATION_MODEL=mistral:7b" in env_content

    def test_ollama_router_model_keeps_default(
        self, tmp_path, setup_module, env_example_content, monkeypatch
    ) -> None:
        """OLLAMA_ROUTER_MODEL should retain its default value from .env.example."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text(env_example_content)

        config = {
            "discord_token": "tok_123",
            "gemini_key": "AIzaSyXXX",
            "router_backend": "ollama",
            "ollama_model": "llama3.1:70b",
            "docker_memory": 16,
            "encryption_passphrase": "test-passphrase-long-enough",
        }

        result = setup_module.generate_env_file(config)
        assert result is True

        env_content = (tmp_path / ".env").read_text()
        # The router model line should be unchanged from the example
        assert "OLLAMA_ROUTER_MODEL=llama3.2:3b" in env_content

    def test_generation_model_unchanged_when_gemini_backend(
        self, tmp_path, setup_module, env_example_content, monkeypatch
    ) -> None:
        """OLLAMA_GENERATION_MODEL should keep default when router_backend is gemini."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text(env_example_content)

        config = {
            "discord_token": "tok_123",
            "gemini_key": "AIzaSyXXX",
            "router_backend": "gemini",
            "encryption_passphrase": "test-passphrase-long-enough",
        }

        result = setup_module.generate_env_file(config)
        assert result is True

        env_content = (tmp_path / ".env").read_text()
        # Should keep the default from .env.example
        assert "OLLAMA_GENERATION_MODEL=llama3.1:8b" in env_content

    def test_returns_false_when_env_example_missing(
        self, tmp_path, setup_module, monkeypatch
    ) -> None:
        """generate_env_file should return False when .env.example is missing."""
        monkeypatch.chdir(tmp_path)

        config = {
            "discord_token": "tok_123",
            "gemini_key": "AIzaSyXXX",
            "router_backend": "gemini",
            "encryption_passphrase": "test-passphrase-long-enough",
        }

        result = setup_module.generate_env_file(config)
        assert result is False

    def test_discord_token_written(
        self, tmp_path, setup_module, env_example_content, monkeypatch
    ) -> None:
        """DISCORD_TOKEN should be written from config."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text(env_example_content)

        config = {
            "discord_token": "my-discord-token-value",
            "gemini_key": "AIzaSyXXX",
            "router_backend": "gemini",
            "encryption_passphrase": "test-passphrase-long-enough",
        }

        setup_module.generate_env_file(config)

        env_content = (tmp_path / ".env").read_text()
        assert "DISCORD_TOKEN=my-discord-token-value" in env_content

    def test_docker_memory_written_for_ollama(
        self, tmp_path, setup_module, env_example_content, monkeypatch
    ) -> None:
        """OLLAMA_DOCKER_MEMORY should be updated when router_backend=ollama."""
        monkeypatch.chdir(tmp_path)
        (tmp_path / ".env.example").write_text(env_example_content)

        config = {
            "discord_token": "tok_123",
            "gemini_key": "AIzaSyXXX",
            "router_backend": "ollama",
            "ollama_model": "llama3.1:8b",
            "docker_memory": 24,
            "encryption_passphrase": "test-passphrase-long-enough",
        }

        setup_module.generate_env_file(config)

        env_content = (tmp_path / ".env").read_text()
        assert "OLLAMA_DOCKER_MEMORY=24" in env_content
