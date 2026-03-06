"""Unit tests for optional service guard check script."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "check-optional-service-guards.py"
    )
    spec = importlib.util.spec_from_file_location(
        "check_optional_service_guards_module", module_path
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_collect_violations_accepts_profile_gated_optional_services(tmp_path: Path) -> None:
    module = _load_module()
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        """services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    profiles:
      - cloudflared
  zetherion-ai-whatsapp-bridge:
    image: local/bridge
    profiles:
      - whatsapp-bridge
volumes:
  data:
""",
        encoding="utf-8",
    )

    assert module.collect_violations(compose_path) == []


def test_collect_violations_flags_missing_profile_block(tmp_path: Path) -> None:
    module = _load_module()
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        """services:
  cloudflared:
    image: cloudflare/cloudflared:latest
  zetherion-ai-whatsapp-bridge:
    image: local/bridge
    profiles:
      - whatsapp-bridge
""",
        encoding="utf-8",
    )

    violations = module.collect_violations(compose_path)
    assert violations == ["cloudflared: missing profiles block"]


def test_collect_violations_flags_wrong_profile_name(tmp_path: Path) -> None:
    module = _load_module()
    compose_path = tmp_path / "docker-compose.yml"
    compose_path.write_text(
        """services:
  cloudflared:
    image: cloudflare/cloudflared:latest
    profiles:
      - wrong-profile
  zetherion-ai-whatsapp-bridge:
    image: local/bridge
    profiles:
      - whatsapp-bridge
""",
        encoding="utf-8",
    )

    violations = module.collect_violations(compose_path)
    assert violations == ["cloudflared: missing required profile line '- cloudflared'"]
