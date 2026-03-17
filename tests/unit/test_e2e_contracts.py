from __future__ import annotations

import importlib


def _reload_test_e2e_module():
    module = importlib.import_module("tests.integration.test_e2e")
    return importlib.reload(module)


def test_test_e2e_uses_router_backend_env_for_required_contract(monkeypatch) -> None:
    monkeypatch.setenv("ROUTER_BACKEND", "groq")
    monkeypatch.setenv("RUN_DISCORD_E2E_REQUIRED", "false")

    module = _reload_test_e2e_module()

    assert module.PRIMARY_ROUTER_BACKEND == "groq"
    assert module.ROUTER_BACKEND_PARAMS[0] == "groq"
    assert module._required_runtime_env_vars() == ["OPENAI_API_KEY", "GROQ_API_KEY"]
    assert module._require_discord_runtime() is False


def test_test_e2e_requires_discord_token_only_when_discord_runtime_enabled(monkeypatch) -> None:
    monkeypatch.setenv("ROUTER_BACKEND", "gemini")
    monkeypatch.setenv("RUN_DISCORD_E2E_REQUIRED", "true")

    module = _reload_test_e2e_module()

    assert module.PRIMARY_ROUTER_BACKEND == "gemini"
    assert module._require_discord_runtime() is True
    assert module._required_runtime_env_vars() == [
        "OPENAI_API_KEY",
        "GEMINI_API_KEY",
        "DISCORD_TOKEN",
    ]
