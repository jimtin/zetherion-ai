from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


def _load_module():
    module_path = (
        Path(__file__).resolve().parents[2] / "scripts" / "windows" / "export-live-env-manifest.py"
    )
    spec = importlib.util.spec_from_file_location(
        "export_live_env_manifest_test_module",
        module_path,
    )
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_export_writes_sanitized_manifests(tmp_path, capsys):
    module = _load_module()
    cgs_env = tmp_path / "cgs.env"
    z_env = tmp_path / "zetherion.env"
    out_dir = tmp_path / "out"

    cgs_env.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://db.internal/live",
                "CGS_AI_TOKEN_SIGNING_SECRET=token-secret",
                "ENCRYPTION_PASSPHRASE=enc-passphrase",
                "CRON_SECRET=cron-secret",
                "NEXT_PUBLIC_BASE_URL=https://catalystgroup.solutions",
                "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_abc",
                "CLERK_SECRET_KEY=sk_live_abc",
                "CLERK_WEBHOOK_SECRET=whsec_live",
                "CGS_AUTH_ISSUER=https://issuer.cgs.internal",
                "CGS_AUTH_JWKS_URL=https://issuer.cgs.internal/.well-known/jwks.json",
                "STRIPE_SECRET_KEY=sk_live",
                "STRIPE_WEBHOOK_SECRET=whsec_stripe",
                "CGS_STRIPE_DEFAULT_PRICE_ID=price_live",
                "VERCEL_WEBHOOK_SECRET=vercel-secret",
                "VERCEL_API_TOKEN=vercel-token",
                "EDGE_CONFIG_ID=edge-123",
                "GITHUB_WEBHOOK_SECRET=github-secret",
                "ZETHERION_PUBLIC_API_BASE_URL=https://zetherion.internal",
                "ZETHERION_SKILLS_API_BASE_URL=https://zetherion.internal/skills",
                "ZETHERION_SKILLS_API_SECRET=skills-secret",
                "ZETHERION_OWNER_CI_WORKER_BASE_URL=https://zetherion.internal/owner/ci/worker/v1",
                "CGS_CI_RELAY_SECRET=relay-secret",
                "CGS_AI_CI_RATE_RUN_MINUTE_USD=0.25",
            ]
        ),
        encoding="utf-8",
    )
    z_env.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=openai-live",
                "GROQ_API_KEY=groq-live",
                "API_JWT_SECRET=jwt-secret",
                "CGS_AUTH_JWKS_URL=https://issuer.cgs.internal/.well-known/jwks.json",
                "ZETHERION_SKILLS_API_SECRET=skills-secret",
                "DOCKER_SOCKET_PATH=//./pipe/docker_engine",
                "ZETHERION_HOST_WORKSPACE_ROOT=C:\\ZetherionCI\\workspaces",
                "ZETHERION_WORKSPACE_MOUNT_TARGET=/workspace",
                "DISCORD_TOKEN=discord-live",
                "ANNOUNCEMENT_EMIT_ENABLED=true",
                "ANNOUNCEMENT_API_URL=http://127.0.0.1:8080/announcements/events",
                "ANNOUNCEMENT_API_SECRET=announce-secret",
                "OBJECT_STORAGE_BACKEND=s3",
                "OBJECT_STORAGE_BUCKET=zetherion-replays",
                "WINDOWS_DISCORD_CANARY_ENABLED=true",
                "WINDOWS_DISCORD_CANARY_TARGET_TOKEN=discord-live",
                "ROUTER_BACKEND=gemini",
                "OPENAI_MODEL=gpt-5.2",
            ]
        ),
        encoding="utf-8",
    )

    code = module.main(
        [
            "--cgs-env-file",
            str(cgs_env),
            "--zetherion-env-file",
            str(z_env),
            "--out-dir",
            str(out_dir),
            "--host-label",
            "windows-box",
            "--strict",
        ]
    )

    assert code == 0
    stdout = json.loads(capsys.readouterr().out.strip())
    assert stdout["status"] == "ok"
    assert (out_dir / "cgs-live-env-manifest.json").exists()
    assert (out_dir / "zetherion-live-env-manifest.json").exists()
    assert (out_dir / "shared-cross-system-env-map.json").exists()
    summary_text = (out_dir / "windows-live-env-summary.md").read_text(encoding="utf-8")
    assert "Blocking Before First Windows Certification" in summary_text
    assert "TradeOxy keys remain warning-only" in summary_text

    cgs_manifest = json.loads((out_dir / "cgs-live-env-manifest.json").read_text(encoding="utf-8"))
    assert cgs_manifest["summary"]["blocking_missing"] == []
    assert cgs_manifest["pattern_matches"][0]["matches"] == ["CGS_AI_CI_RATE_RUN_MINUTE_USD"]

    z_manifest = json.loads(
        (out_dir / "zetherion-live-env-manifest.json").read_text(encoding="utf-8")
    )
    pattern_matches = {item["pattern"]: item["matches"] for item in z_manifest["pattern_matches"]}
    assert pattern_matches[r"^WINDOWS_DISCORD_CANARY_[A-Z0-9_]+$"] == [
        "WINDOWS_DISCORD_CANARY_ENABLED",
        "WINDOWS_DISCORD_CANARY_TARGET_TOKEN",
    ]


def test_export_marks_placeholders_and_missing_blockers(tmp_path, capsys):
    module = _load_module()
    cgs_env = tmp_path / "cgs.env"
    z_env = tmp_path / "zetherion.env"
    out_dir = tmp_path / "out"

    cgs_env.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://db.internal/live",
                "CGS_AI_TOKEN_SIGNING_SECRET=replace-with-32-char-random-secret",
                "ENCRYPTION_PASSPHRASE=replace-with-strong-passphrase",
                "NEXT_PUBLIC_BASE_URL=https://example.com",
            ]
        ),
        encoding="utf-8",
    )
    z_env.write_text("OPENAI_API_KEY=\n", encoding="utf-8")

    code = module.main(
        [
            "--cgs-env-file",
            str(cgs_env),
            "--zetherion-env-file",
            str(z_env),
            "--out-dir",
            str(out_dir),
            "--strict",
        ]
    )

    assert code == 1
    stdout = json.loads(capsys.readouterr().out.strip())
    assert stdout["status"] == "blocking_missing"
    assert "CGS_AI_TOKEN_SIGNING_SECRET" in stdout["blocking_missing"]
    assert "OPENAI_API_KEY" in stdout["blocking_missing"]

    cgs_manifest = json.loads((out_dir / "cgs-live-env-manifest.json").read_text(encoding="utf-8"))
    core_entries = {
        entry["name"]: entry
        for group in cgs_manifest["groups"]
        for entry in group["entries"]
        if group["group"] == "core"
    }
    assert core_entries["CGS_AI_TOKEN_SIGNING_SECRET"]["status"] == "placeholder"
    assert core_entries["CGS_PUBLIC_BASE_URL"]["status"] == "placeholder"


def test_powershell_wrapper_points_at_windows_output_bundle():
    script_text = (
        Path(__file__).resolve().parents[2] / "scripts" / "windows" / "export-live-env-manifest.ps1"
    ).read_text(encoding="utf-8")

    assert '[string]$DeployPath = "C:\\ZetherionAI"' in script_text
    assert 'Join-Path $DeployPath "data\\windows-live-env"' in script_text
    assert 'Join-Path $DeployPath ".env"' in script_text
    assert "export-live-env-manifest.py" in script_text


def test_export_respects_core_fallbacks_and_optional_webhook_paths(tmp_path, capsys):
    module = _load_module()
    cgs_env = tmp_path / "cgs.env"
    z_env = tmp_path / "zetherion.env"
    out_dir = tmp_path / "out"

    cgs_env.write_text(
        "\n".join(
            [
                "DATABASE_URL=postgresql://db.internal/live",
                "CGS_AI_TOKEN_SIGNING_SECRET=token-secret",
                "ENCRYPTION_PASSPHRASE=enc-passphrase",
                "CRON_SECRET=cron-secret",
                "NEXT_PUBLIC_BASE_URL=https://catalystgroup.solutions",
                "NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=pk_live_dGVzdC5jbGVyay5hY2NvdW50cy5kZXYk",
                "CLERK_SECRET_KEY=sk_live_abc",
                "VERCEL_API_TOKEN=vercel-token",
                "STRIPE_SECRET_KEY=sk_live",
                "STRIPE_WEBHOOK_SECRET=whsec_stripe",
                "CGS_STRIPE_DEFAULT_PRICE_ID=price_live",
                "ZETHERION_PUBLIC_API_BASE_URL=https://zetherion.internal",
                "ZETHERION_SKILLS_API_BASE_URL=https://zetherion.internal/skills",
                "ZETHERION_SKILLS_API_SECRET=skills-secret",
            ]
        ),
        encoding="utf-8",
    )
    z_env.write_text(
        "\n".join(
            [
                "OPENAI_API_KEY=openai-live",
                "GROQ_API_KEY=groq-live",
                "API_JWT_SECRET=jwt-secret",
                "CGS_AUTH_JWKS_URL=https://issuer.cgs.internal/.well-known/jwks.json",
                "ZETHERION_SKILLS_API_SECRET=skills-secret",
                "DISCORD_TOKEN=discord-live",
                "ANNOUNCEMENT_API_SECRET=announce-secret",
            ]
        ),
        encoding="utf-8",
    )

    code = module.main(
        [
            "--cgs-env-file",
            str(cgs_env),
            "--zetherion-env-file",
            str(z_env),
            "--out-dir",
            str(out_dir),
            "--strict",
        ]
    )

    assert code == 0
    stdout = json.loads(capsys.readouterr().out.strip())
    assert stdout["status"] == "ok"

    cgs_manifest = json.loads((out_dir / "cgs-live-env-manifest.json").read_text(encoding="utf-8"))
    auth_entries = {
        entry["name"]: entry
        for group in cgs_manifest["groups"]
        for entry in group["entries"]
        if group["group"] == "auth"
    }
    integration_entries = {
        entry["name"]: entry
        for group in cgs_manifest["groups"]
        for entry in group["entries"]
        if group["group"] == "vercel_integration"
    }

    assert auth_entries["CGS_AUTH_ISSUER"]["status"] == "present"
    assert auth_entries["CGS_AUTH_ISSUER"]["matched_key"] == "derived:clerk_frontend_api"
    assert auth_entries["CGS_AUTH_JWKS_URL"]["status"] == "present"
    assert auth_entries["CGS_AUTH_JWKS_URL"]["matched_key"] == "derived:clerk_frontend_api"
    assert integration_entries["ZETHERION_OWNER_CI_WORKER_BASE_URL"]["status"] == "present"
    assert (
        integration_entries["ZETHERION_OWNER_CI_WORKER_BASE_URL"]["matched_key"]
        == "derived:ZETHERION_SKILLS_API_BASE_URL"
    )
    assert "CLERK_WEBHOOK_SIGNING_SECRET" not in cgs_manifest["summary"]["blocking_missing"]
    assert "VERCEL_WEBHOOK_SECRET" not in cgs_manifest["summary"]["blocking_missing"]
    assert "GITHUB_WEBHOOK_SECRET" not in cgs_manifest["summary"]["blocking_missing"]
    assert "CGS_CI_RELAY_SECRET" not in cgs_manifest["summary"]["blocking_missing"]
