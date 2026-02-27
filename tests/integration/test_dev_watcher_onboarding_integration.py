"""Integration tests for dev-watcher onboarding orchestration.

These tests exercise Discord bot provisioning/status flows against a real
in-process HTTP dev-agent API (aiohttp TestServer), while mocking Discord
objects and external Discord API mutations.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
import pytest_asyncio
from aiohttp import web
from aiohttp.test_utils import TestServer

from zetherion_ai.discord.bot import ZetherionAIBot
from zetherion_ai.memory.qdrant import QdrantMemory


def _dev_settings(base_url: str, **overrides):
    defaults = {
        "owner_user_id": 123456789,
        "dev_agent_service_url": base_url,
        "dev_agent_bootstrap_secret": "bootstrap-secret",
        "dev_agent_cleanup_hour": 2,
        "dev_agent_cleanup_minute": 30,
        "dev_agent_approval_reprompt_hours": 24,
        "dev_agent_webhook_name": "zetherion-dev-agent",
        "dev_agent_enabled": True,
        "dev_agent_discord_guild_id": "",
        "dev_agent_discord_channel_id": "",
        "auto_update_repo": "owner/repo",
        "updater_service_url": "http://updater:9090",
        "updater_secret": "",
        "updater_secret_path": "/tmp/not-used",
        "github_token": None,
        "updater_verify_signatures": True,
        "updater_verify_identity": "https://example.com/workflows/release.yml@refs/tags/*",
        "updater_verify_oidc_issuer": "https://token.actions.githubusercontent.com",
        "updater_verify_rekor_url": "https://rekor.sigstore.dev",
        "updater_release_manifest_asset": "release-manifest.json",
        "updater_release_signature_asset": "release-manifest.sig",
        "updater_release_certificate_asset": "release-manifest.pem",
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def bot() -> ZetherionAIBot:
    memory = AsyncMock(spec=QdrantMemory)
    memory.initialize = AsyncMock()
    user_manager = AsyncMock()
    user_manager.is_allowed = AsyncMock(return_value=True)
    user_manager.get_role = AsyncMock(return_value="admin")
    bot_instance = ZetherionAIBot(
        memory=memory,
        user_manager=user_manager,
        settings_manager=AsyncMock(),
    )
    mock_user = MagicMock(spec=discord.ClientUser)
    mock_user.id = 999999999
    mock_user.name = "ZetherionAIBot"
    bot_instance._connection.user = mock_user
    bot_instance._tree.sync = AsyncMock()
    return bot_instance


@pytest.fixture
def dm_message():
    message = MagicMock(spec=discord.Message)
    message.author = MagicMock(spec=discord.User)
    message.author.id = 123456789
    message.channel = MagicMock(spec=discord.DMChannel)
    message.channel.send = AsyncMock()
    message.reply = AsyncMock()
    return message


@pytest_asyncio.fixture
async def dev_agent_server() -> tuple[TestServer, dict[str, object]]:
    state: dict[str, object] = {}

    async def handle_health(_request: web.Request) -> web.Response:
        return web.json_response({"status": "ok"})

    async def handle_bootstrap(request: web.Request) -> web.Response:
        state["bootstrap_header"] = request.headers.get("X-Bootstrap-Secret", "")
        state["bootstrap_payload"] = await request.json()
        return web.json_response({"ok": True, "api_token": "api-token"})

    async def handle_discovery(request: web.Request) -> web.Response:
        state["discovery_auth"] = request.headers.get("Authorization", "")
        return web.json_response(
            {
                "ok": True,
                "projects_discovered": ["proj-a", "proj-b"],
                "pending_approvals": [{"project_id": "proj-a"}],
            }
        )

    async def handle_projects(request: web.Request) -> web.Response:
        state["projects_auth"] = request.headers.get("Authorization", "")
        return web.json_response({"projects": [{"project_id": "proj-a"}, {"project_id": "proj-b"}]})

    async def handle_pending(request: web.Request) -> web.Response:
        state["pending_auth"] = request.headers.get("Authorization", "")
        return web.json_response({"pending": [{"project_id": "proj-a"}]})

    app = web.Application()
    app.router.add_get("/v1/health", handle_health)
    app.router.add_post("/v1/bootstrap", handle_bootstrap)
    app.router.add_post("/v1/discovery/run", handle_discovery)
    app.router.add_get("/v1/projects", handle_projects)
    app.router.add_get("/v1/approvals/pending", handle_pending)

    server = TestServer(app)
    await server.start_server()
    try:
        yield server, state
    finally:
        await server.close()


@pytest.mark.integration
@pytest.mark.asyncio
async def test_onboarding_flow_bootstraps_and_persists_runtime(
    bot: ZetherionAIBot,
    dm_message,
    dev_agent_server: tuple[TestServer, dict[str, object]],
) -> None:
    server, state = dev_agent_server
    settings = _dev_settings(str(server.make_url("")).rstrip("/"))

    bot._settings_manager = AsyncMock()
    bot._settings_manager.set = AsyncMock()

    skills_client = AsyncMock()
    skills_client.put_secret = AsyncMock()
    bot._agent = AsyncMock()
    bot._agent._get_skills_client = AsyncMock(return_value=skills_client)

    guild = MagicMock(spec=discord.Guild)
    guild.id = 404
    guild.name = "My Guild"
    category = MagicMock(spec=discord.CategoryChannel)
    category.name = "Zetherion Ops"
    channel = MagicMock(spec=discord.TextChannel)
    channel.id = 202
    channel.name = "dev-watcher"
    webhook = MagicMock(spec=discord.Webhook)
    webhook.id = 303
    webhook.name = "zetherion-dev-agent"
    webhook.url = "https://discord.test/webhook"

    with (
        patch("zetherion_ai.discord.bot.get_settings", return_value=settings),
        patch(
            "zetherion_ai.discord.bot.get_dynamic",
            side_effect=lambda _ns, _key, default=None: default,
        ),
        patch.object(
            bot,
            "_ensure_dev_watcher_discord_assets",
            new_callable=AsyncMock,
            return_value=(category, channel, webhook),
        ),
        patch.object(bot, "_send_long_message", new_callable=AsyncMock) as send_long,
    ):
        await bot._run_dev_watcher_provisioning(dm_message, guild)

    assert state["bootstrap_header"] == "bootstrap-secret"
    bootstrap_payload = state["bootstrap_payload"]
    assert isinstance(bootstrap_payload, dict)
    assert bootstrap_payload["webhook_url"] == "https://discord.test/webhook"
    assert state["discovery_auth"] == "Bearer api-token"
    assert bot._settings_manager.set.await_count >= 9
    skills_client.put_secret.assert_awaited_once()
    send_long.assert_awaited_once()
    summary = send_long.call_args[0][1]
    assert "Projects discovered: `2`" in summary
    assert "Pending approvals: `1`" in summary


@pytest.mark.integration
@pytest.mark.asyncio
async def test_status_flow_reads_remote_project_counts(
    bot: ZetherionAIBot,
    dm_message,
    dev_agent_server: tuple[TestServer, dict[str, object]],
) -> None:
    server, state = dev_agent_server
    settings = _dev_settings(
        str(server.make_url("")).rstrip("/"),
        dev_agent_enabled=True,
        dev_agent_discord_guild_id="404",
        dev_agent_discord_channel_id="202",
    )

    with (
        patch.object(bot, "_is_owner_or_admin", new_callable=AsyncMock, return_value=True),
        patch("zetherion_ai.discord.bot.get_settings", return_value=settings),
        patch(
            "zetherion_ai.discord.bot.get_dynamic",
            side_effect=lambda _ns, _key, default=None: default,
        ),
        patch("zetherion_ai.discord.bot.get_secret", return_value="api-token"),
        patch.object(bot, "_send_long_reply", new_callable=AsyncMock) as send_reply,
    ):
        await bot._handle_dev_watcher_status_dm(dm_message)

    assert state["projects_auth"] == "Bearer api-token"
    assert state["pending_auth"] == "Bearer api-token"
    send_reply.assert_awaited_once()
    body = send_reply.call_args[0][1]
    assert "Projects discovered: `2`" in body
    assert "Pending approvals: `1`" in body
    assert "Stored API token: `yes`" in body


@pytest.mark.integration
@pytest.mark.asyncio
async def test_onboarding_halts_when_discord_assets_cannot_be_provisioned(
    bot: ZetherionAIBot,
    dm_message,
    dev_agent_server: tuple[TestServer, dict[str, object]],
) -> None:
    server, _state = dev_agent_server
    settings = _dev_settings(str(server.make_url("")).rstrip("/"))

    guild = MagicMock(spec=discord.Guild)
    guild.id = 404
    guild.name = "My Guild"

    with (
        patch("zetherion_ai.discord.bot.get_settings", return_value=settings),
        patch(
            "zetherion_ai.discord.bot.get_dynamic",
            side_effect=lambda _ns, _key, default=None: default,
        ),
        patch.object(
            bot,
            "_ensure_dev_watcher_discord_assets",
            new_callable=AsyncMock,
            return_value=None,
        ),
    ):
        await bot._run_dev_watcher_provisioning(dm_message, guild)

    sent_messages = [str(call.args[0]) for call in dm_message.channel.send.await_args_list]
    assert any("could not create Discord assets" in msg for msg in sent_messages)
