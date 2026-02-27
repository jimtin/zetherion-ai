"""Coverage and behavior test for package module entrypoint."""

from __future__ import annotations

import runpy


def test_package_main_entrypoint_invokes_run(monkeypatch) -> None:
    called = {"value": False}

    def fake_run() -> None:
        called["value"] = True

    monkeypatch.setattr("zetherion_ai.main.run", fake_run)
    runpy.run_module("zetherion_ai.__main__", run_name="__main__")
    assert called["value"] is True
