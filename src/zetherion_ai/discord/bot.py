"""Discord bot implementation."""

import asyncio
import contextlib
import json
import re
import time
from datetime import time as clock_time
from pathlib import Path
from typing import Any
from uuid import uuid4

import discord
import httpx
import structlog
from discord import app_commands

from zetherion_ai.agent.core import Agent
from zetherion_ai.agent.inference import ProviderIssueAlert
from zetherion_ai.config import get_dynamic, get_secret, get_settings
from zetherion_ai.constants import KEEP_WARM_INTERVAL_SECONDS, MAX_DISCORD_MESSAGE_LENGTH
from zetherion_ai.discord.security import (
    RateLimiter,
    SecurityPipeline,
    ThreatAction,
    detect_prompt_injection,
)
from zetherion_ai.discord.security.tier2_ai import SecurityAIAnalyzer
from zetherion_ai.discord.user_manager import ROLE_HIERARCHY, UserManager
from zetherion_ai.logging import get_logger
from zetherion_ai.memory.qdrant import QdrantMemory
from zetherion_ai.queue.manager import QueueManager
from zetherion_ai.queue.models import QueuePriority, QueueTaskType
from zetherion_ai.scheduler.actions import ActionExecutor
from zetherion_ai.scheduler.heartbeat import HeartbeatScheduler, QuietHoursWindow
from zetherion_ai.utils import split_text_chunks

log = get_logger("zetherion_ai.discord.bot")


class ZetherionAIBot(discord.Client):
    """Zetherion AI Discord bot."""

    _DEV_WATCHER_TRIGGER_PHRASES = (
        "implement dev watcher",
        "please implement dev watcher",
        "setup dev watcher",
        "set up dev watcher",
        "enable dev watcher",
    )
    _DEV_WATCHER_STATUS_PHRASES = (
        "dev watcher status",
        "dev watcher health",
        "status dev watcher",
    )
    _DEV_WATCHER_HELP_PHRASES = (
        "dev watcher help",
        "help dev watcher",
    )
    _DEV_WATCHER_WIZARD_TIMEOUT_SECONDS = 900

    def __init__(
        self,
        memory: QdrantMemory,
        user_manager: UserManager | None = None,
        settings_manager: object | None = None,
        queue_manager: QueueManager | None = None,
    ) -> None:
        """Initialize the bot.

        Args:
            memory: The memory system.
            user_manager: Optional UserManager for RBAC.
            settings_manager: Optional SettingsManager for runtime config.
            queue_manager: Optional QueueManager for priority message queue.
        """
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True

        super().__init__(intents=intents)

        self._memory = memory
        self._agent: Agent | None = None
        self._tree = app_commands.CommandTree(self)
        self._rate_limiter = RateLimiter()
        self._user_manager = user_manager
        self._settings_manager = settings_manager
        self._queue_manager = queue_manager
        self._keep_warm_task: asyncio.Task[None] | None = None
        self._provider_watch_task: asyncio.Task[None] | None = None
        self._security_pipeline: SecurityPipeline | None = None
        self._security_ai_analyzer: SecurityAIAnalyzer | None = None
        self._heartbeat_scheduler: HeartbeatScheduler | None = None
        self._last_message_time: float = 0.0
        self._dev_watcher_wizards: dict[int, dict[str, Any]] = {}

        self._setup_commands()

    def _setup_commands(self) -> None:
        """Set up slash commands."""

        @self._tree.command(name="ask", description="Ask Zetherion AI a question")
        async def ask_command(
            interaction: discord.Interaction[discord.Client], question: str
        ) -> None:
            await self._handle_ask(interaction, question)

        @self._tree.command(name="remember", description="Ask Zetherion AI to remember something")
        async def remember_command(
            interaction: discord.Interaction[discord.Client], content: str
        ) -> None:
            await self._handle_remember(interaction, content)

        @self._tree.command(name="search", description="Search your memories")
        async def search_command(
            interaction: discord.Interaction[discord.Client], query: str
        ) -> None:
            await self._handle_search(interaction, query)

        @self._tree.command(name="ping", description="Check if Zetherion AI is online")
        async def ping_command(interaction: discord.Interaction[discord.Client]) -> None:
            await interaction.response.send_message(
                f"\U0001f980 Pong! Latency: {round(self.latency * 1000)}ms",
                ephemeral=True,
            )

        @self._tree.command(name="channels", description="List channels Zetherion AI can access")
        async def channels_command(interaction: discord.Interaction[discord.Client]) -> None:
            await self._handle_channels(interaction)

        # RBAC management commands (admin+ only)
        @self._tree.command(name="allow", description="Add a user to the allowlist")
        @app_commands.describe(user="User to allow", role="Role to assign (default: user)")
        async def allow_command(
            interaction: discord.Interaction[discord.Client],
            user: discord.User,
            role: str = "user",
        ) -> None:
            await self._handle_allow(interaction, user, role)

        @self._tree.command(name="deny", description="Remove a user from the allowlist")
        @app_commands.describe(user="User to remove")
        async def deny_command(
            interaction: discord.Interaction[discord.Client],
            user: discord.User,
        ) -> None:
            await self._handle_deny(interaction, user)

        @self._tree.command(name="role", description="Change a user's role")
        @app_commands.describe(user="Target user", role="New role")
        async def role_command(
            interaction: discord.Interaction[discord.Client],
            user: discord.User,
            role: str,
        ) -> None:
            await self._handle_role(interaction, user, role)

        @self._tree.command(name="allowlist", description="List allowed users")
        @app_commands.describe(role="Filter by role (optional)")
        async def allowlist_command(
            interaction: discord.Interaction[discord.Client],
            role: str | None = None,
        ) -> None:
            await self._handle_allowlist(interaction, role)

        @self._tree.command(name="audit", description="View recent audit log")
        @app_commands.describe(limit="Number of entries (default: 20)")
        async def audit_command(
            interaction: discord.Interaction[discord.Client],
            limit: int = 20,
        ) -> None:
            await self._handle_audit(interaction, limit)

        # Runtime configuration commands (admin+ only)
        @self._tree.command(name="config_list", description="List runtime settings")
        @app_commands.describe(namespace="Filter by namespace (optional)")
        async def config_list_command(
            interaction: discord.Interaction[discord.Client],
            namespace: str | None = None,
        ) -> None:
            await self._handle_config_list(interaction, namespace)

        @self._tree.command(name="config_set", description="Update a runtime setting")
        @app_commands.describe(namespace="Setting namespace", key="Setting key", value="New value")
        async def config_set_command(
            interaction: discord.Interaction[discord.Client],
            namespace: str,
            key: str,
            value: str,
        ) -> None:
            await self._handle_config_set(interaction, namespace, key, value)

        @self._tree.command(name="config_reset", description="Reset a setting to default")
        @app_commands.describe(namespace="Setting namespace", key="Setting key")
        async def config_reset_command(
            interaction: discord.Interaction[discord.Client],
            namespace: str,
            key: str,
        ) -> None:
            await self._handle_config_reset(interaction, namespace, key)

    async def setup_hook(self) -> None:
        """Called when the bot is ready to set up."""
        # Initialize agent after bot is ready
        self._agent = Agent(memory=self._memory)

        # Wire provider issue alerts from InferenceBroker to owner DM notifications.
        inference_broker = getattr(self._agent, "_inference_broker", None)
        if inference_broker is not None and hasattr(inference_broker, "set_provider_issue_handler"):
            try:
                maybe = inference_broker.set_provider_issue_handler(
                    self._handle_provider_issue_alert
                )
                if asyncio.iscoroutine(maybe):
                    await maybe
                log.info("provider_issue_alerts_wired")
            except Exception:
                log.exception("provider_issue_alert_wiring_failed")

        # Warm up the configured router backend to avoid first-request latency
        log.info("warming_up_router_backend")
        warmup_success = await self._agent.warmup()
        if warmup_success:
            log.info("router_warmup_successful")
        else:
            log.warning("router_warmup_failed", note="First request may be slow")

        # Start background task to keep model warm
        self._keep_warm_task = asyncio.create_task(self._keep_warm_loop())
        log.info("keep_warm_task_started", interval_seconds=KEEP_WARM_INTERVAL_SECONDS)

        # Periodic paid-provider readiness probes (credit/auth/billing visibility).
        probe_enabled = get_dynamic(
            "notifications",
            "provider_probe_enabled",
            get_settings().provider_probe_enabled,
        )
        if isinstance(probe_enabled, bool) and probe_enabled:
            self._provider_watch_task = asyncio.create_task(self._provider_watch_loop())
            log.info(
                "provider_probe_task_started",
                interval_seconds=get_dynamic(
                    "notifications",
                    "provider_probe_interval_seconds",
                    get_settings().provider_probe_interval_seconds,
                ),
            )

        # Initialize security pipeline (Tier 1 + optional Tier 2 AI)
        try:
            enable_tier2 = bool(get_settings().security_tier2_enabled)
            security_inference = getattr(self._agent, "_inference_broker", None)
            self._security_ai_analyzer = (
                SecurityAIAnalyzer(inference=security_inference) if enable_tier2 else None
            )
            self._security_pipeline = SecurityPipeline(
                ai_analyzer=self._security_ai_analyzer,
                enable_tier2=enable_tier2,
            )
            log.info("security_pipeline_initialized", tier2_enabled=enable_tier2)
        except Exception:
            log.exception("security_pipeline_init_failed")
            self._security_ai_analyzer = None
            self._security_pipeline = None

        # Shared integrations for queue workers and heartbeat scheduler
        skills_client = None
        action_executor = ActionExecutor(message_sender=self)
        if self._agent is not None:
            try:
                skills_client = await self._agent._get_skills_client()
            except Exception:
                log.exception("skills_client_init_failed")

        # Wire queue processors with bot/agent/deps, then start workers
        if self._queue_manager is not None:
            self._queue_manager._processors._bot = self
            self._queue_manager._processors._agent = self._agent
            self._queue_manager._processors._skills_client = skills_client
            self._queue_manager._processors._action_executor = action_executor
            await self._queue_manager.start()
            log.info("queue_manager_started")

        # Start heartbeat scheduler
        self._heartbeat_scheduler = HeartbeatScheduler(
            skills_client=skills_client,
            action_executor=action_executor,
            queue_manager=self._queue_manager,
            quiet_hours_resolver=self._resolve_quiet_hours,
        )
        if self._user_manager is not None:
            try:
                users = await self._user_manager.list_users()
                heartbeat_users = [
                    str(u["discord_user_id"])
                    for u in users
                    if str(u.get("role", "")) != "restricted"
                ]
                self._heartbeat_scheduler.set_user_ids(heartbeat_users)
            except Exception:
                log.exception("heartbeat_user_list_failed")
        await self._heartbeat_scheduler.start()

        # Sync commands
        await self._tree.sync()
        log.info("commands_synced")

    @staticmethod
    def _parse_clock_time(value: Any) -> clock_time | None:
        """Parse ``HH:MM`` clock strings into ``datetime.time``."""
        if not isinstance(value, str):
            return None
        raw = value.strip()
        if not raw:
            return None
        try:
            hour_str, minute_str = raw.split(":")
            hour = int(hour_str)
            minute = int(minute_str)
        except (ValueError, AttributeError):
            return None
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            return None
        return clock_time(hour, minute)

    async def _resolve_quiet_hours(self, user_id: str) -> QuietHoursWindow | None:
        """Resolve per-user quiet-hours from personal profile settings."""
        if self._user_manager is None or not user_id.isdigit():
            return None

        profile = await self._user_manager.get_personal_profile(int(user_id))
        if not profile:
            return None

        timezone = profile.get("timezone")
        timezone_str = str(timezone) if isinstance(timezone, str) and timezone else None

        preferences = profile.get("preferences")
        if isinstance(preferences, str):
            try:
                preferences = json.loads(preferences)
            except json.JSONDecodeError:
                preferences = {}

        if isinstance(preferences, dict):
            quiet = preferences.get("quiet_hours")
            if isinstance(quiet, dict):
                start = self._parse_clock_time(quiet.get("start"))
                end = self._parse_clock_time(quiet.get("end"))
                if start and end:
                    return QuietHoursWindow(
                        start=start,
                        end=end,
                        timezone=timezone_str,
                        enabled=bool(quiet.get("enabled", True)),
                    )

        # Learning fallback: infer quiet-hours as inverse of working hours.
        working_hours = profile.get("working_hours")
        if isinstance(working_hours, str):
            try:
                working_hours = json.loads(working_hours)
            except json.JSONDecodeError:
                working_hours = {}

        if isinstance(working_hours, dict):
            work_start = self._parse_clock_time(working_hours.get("start"))
            work_end = self._parse_clock_time(working_hours.get("end"))
            if work_start and work_end:
                return QuietHoursWindow(
                    start=work_end,
                    end=work_start,
                    timezone=timezone_str,
                    enabled=True,
                )

        return None

    async def _keep_warm_loop(self) -> None:
        """Background task to periodically keep the Ollama model warm."""
        await asyncio.sleep(KEEP_WARM_INTERVAL_SECONDS)
        while True:
            try:
                # Only keep warm if there's been recent activity (last 30 min)
                if self._agent and (time.time() - self._last_message_time < 30 * 60):
                    await self._agent.keep_warm()
            except Exception as e:
                log.warning("keep_warm_error", error=str(e))
            await asyncio.sleep(KEEP_WARM_INTERVAL_SECONDS)

    async def _resolve_owner_alert_user_id(self) -> int | None:
        """Resolve the user ID that should receive critical runtime alerts."""
        owner_id = get_settings().owner_user_id
        if isinstance(owner_id, int) and owner_id > 0:
            return owner_id

        if self._user_manager is not None:
            try:
                users = await self._user_manager.list_users()
                for user in users:
                    role = str(user.get("role", ""))
                    uid = user.get("discord_user_id")
                    if role == "owner" and isinstance(uid, int):
                        return uid
            except Exception:
                log.exception("owner_alert_user_resolution_failed")

        return None

    async def _handle_provider_issue_alert(self, alert: ProviderIssueAlert) -> None:
        """Send paid-provider issue alerts to the configured owner user."""
        if not self.is_ready():
            return

        owner_id = await self._resolve_owner_alert_user_id()
        if owner_id is None:
            log.warning(
                "provider_issue_alert_dropped_no_owner",
                provider=alert.provider.value,
                issue_type=alert.issue_type,
            )
            return

        provider_label = alert.provider.value.upper()
        issue_title = {
            "billing": "Billing/Credit issue detected",
            "auth": "Authentication issue detected",
            "rate_limit": "Rate-limit pressure detected",
        }.get(alert.issue_type, "Provider issue detected")
        action_hint = {
            "billing": "Top up credits or update billing on this provider.",
            "auth": "Rotate/reapply API key and verify permissions.",
            "rate_limit": "Increase limits or adjust routing/traffic.",
        }.get(alert.issue_type, "Review provider status and credentials.")

        truncated_error = (alert.error or "").strip()
        if len(truncated_error) > 500:
            truncated_error = f"{truncated_error[:500]}..."

        body = "\n".join(
            [
                f"**{issue_title}**",
                f"Provider: `{provider_label}`",
                f"Task: `{alert.task_type}`",
                f"Model: `{alert.model or 'n/a'}`",
                f"Failures observed: `{alert.fail_count}`",
                f"Action: {action_hint}",
                "",
                f"Latest error: `{truncated_error or 'unknown'}`",
            ]
        )

        try:
            user = self.get_user(owner_id)
            if user is None:
                user = await self.fetch_user(owner_id)
            if user is None:
                log.warning("provider_issue_alert_user_not_found", owner_id=owner_id)
                return
            await user.send(body)
            log.info(
                "provider_issue_alert_sent",
                owner_id=owner_id,
                provider=alert.provider.value,
                issue_type=alert.issue_type,
            )
        except Exception:
            log.exception(
                "provider_issue_alert_send_failed",
                owner_id=owner_id,
                provider=alert.provider.value,
            )

    async def _provider_watch_loop(self) -> None:
        """Background task to proactively probe paid-provider readiness."""
        await asyncio.sleep(30)
        while True:
            interval = get_dynamic(
                "notifications",
                "provider_probe_interval_seconds",
                get_settings().provider_probe_interval_seconds,
            )
            sleep_seconds = interval if isinstance(interval, int) and interval > 0 else 1800

            try:
                enabled = get_dynamic(
                    "notifications",
                    "provider_probe_enabled",
                    get_settings().provider_probe_enabled,
                )
                if isinstance(enabled, bool) and enabled and self._agent is not None:
                    broker = getattr(self._agent, "_inference_broker", None)
                    if broker is not None and hasattr(broker, "probe_paid_providers"):
                        await broker.probe_paid_providers()
            except Exception as exc:
                log.warning("provider_probe_cycle_failed", error=str(exc))

            await asyncio.sleep(sleep_seconds)

    async def close(self) -> None:
        """Clean up resources when bot is closing."""
        # Stop heartbeat scheduler first so no new queue tasks are generated.
        scheduler = self._heartbeat_scheduler
        if scheduler is not None:
            await scheduler.stop()
            self._heartbeat_scheduler = None
            log.info("heartbeat_scheduler_stopped")

        # Drain queue workers (graceful shutdown)
        if self._queue_manager is not None:
            await self._queue_manager.stop()
            log.info("queue_manager_stopped")

        # Cancel keep-warm task
        task = self._keep_warm_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            log.info("keep_warm_task_stopped")

        probe_task = self._provider_watch_task
        if probe_task is not None and not probe_task.done():
            probe_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await probe_task
            log.info("provider_probe_task_stopped")

        # Clean up agent resources
        if (
            self._agent
            and hasattr(self._agent, "_inference_broker")
            and self._agent._inference_broker
        ):
            await self._agent._inference_broker.close()

        if self._security_ai_analyzer is not None:
            await self._security_ai_analyzer.close()
            self._security_ai_analyzer = None

        await super().close()

    async def on_ready(self) -> None:
        """Called when the bot is fully ready."""
        log.info(
            "bot_ready",
            user=str(self.user),
            guilds=len(self.guilds),
        )

    async def on_message(self, message: discord.Message) -> None:
        """Handle incoming messages."""
        # Ignore own messages
        if message.author == self.user:
            return

        # Handle dev-agent webhook messages (before the general bot filter)
        if message.webhook_id is not None:
            settings = get_settings()
            webhook_name = get_dynamic(
                "dev_agent",
                "webhook_name",
                getattr(settings, "dev_agent_webhook_name", "zetherion-dev-agent"),
            )
            if not isinstance(webhook_name, str):
                webhook_name = "zetherion-dev-agent"
            webhook_id = get_dynamic(
                "dev_agent",
                "webhook_id",
                getattr(settings, "dev_agent_webhook_id", ""),
            )
            if not isinstance(webhook_id, str):
                webhook_id = ""

            name_match = message.author.name == webhook_name
            id_match = not webhook_id.strip() or str(message.webhook_id) == webhook_id.strip()
            if name_match and id_match:
                await self._handle_dev_event(message)
            return  # All other webhooks are ignored

        # Ignore messages from bots (unless explicitly allowed for testing)
        if message.author.bot and not get_settings().allow_bot_messages:
            return

        # Only respond to DMs or mentions
        is_dm = isinstance(message.channel, discord.DMChannel)
        is_mention = self.user in message.mentions if self.user else False

        if not (is_dm or is_mention):
            return

        structlog.contextvars.bind_contextvars(
            request_id=str(uuid4())[:12],
            user_id=message.author.id,
            channel_id=message.channel.id,
        )
        self._last_message_time = time.time()
        try:
            # Check allowlist
            if self._user_manager and not await self._user_manager.is_allowed(message.author.id):
                log.warning("user_not_allowed")
                await message.reply(
                    "Sorry, you're not authorized to use this bot.",
                    mention_author=True,
                )
                return

            # Check rate limit
            allowed, warning = self._rate_limiter.check(message.author.id)
            if not allowed:
                if warning:
                    await message.reply(warning, mention_author=True)
                return

            # Prepare clean content
            content = message.content
            if is_mention and self.user:
                content = content.replace(f"<@{self.user.id}>", "").strip()

            if not content:
                await message.reply(
                    "How can I help you?",
                    mention_author=True,
                )
                return

            # Owner-only DM wizard for secure dev-agent provisioning.
            if is_dm and await self._maybe_handle_dev_watcher_dm(message, content):
                return

            # Check for prompt injection
            intent_hint = self._infer_security_intent_hint(content)
            if await self._is_security_blocked(
                content=content,
                user_id=message.author.id,
                channel_id=message.channel.id,
                intent_hint=intent_hint,
            ):
                await message.reply(
                    "I noticed some unusual patterns in your message. "
                    "Could you rephrase your question?",
                    mention_author=True,
                )
                return

            # Route through queue if enabled, otherwise process inline
            if self._queue_manager is not None and self._queue_manager.is_running:
                await self._enqueue_message(message, content, is_mention)
            else:
                await self._process_message_inline(message, content)
        finally:
            structlog.contextvars.clear_contextvars()

    async def _enqueue_message(
        self,
        message: discord.Message,
        content: str,
        is_mention: bool,
    ) -> None:
        """Enqueue a message for async processing via the priority queue."""
        assert self._queue_manager is not None  # caller checks

        try:
            await self._queue_manager.enqueue(
                task_type=QueueTaskType.DISCORD_MESSAGE,
                user_id=message.author.id,
                channel_id=message.channel.id,
                payload={
                    "channel_id": message.channel.id,
                    "message_id": message.id,
                    "content": content,
                    "user_id": message.author.id,
                    "is_mention": is_mention,
                },
                priority=QueuePriority.INTERACTIVE,
            )
            # Show typing indicator while the queue worker processes
            await message.channel.typing()
        except Exception:
            log.exception("enqueue_failed")
            # Fall back to inline processing
            await self._process_message_inline(message, content)

    async def _process_message_inline(
        self,
        message: discord.Message,
        content: str,
    ) -> None:
        """Process a message directly (fallback when queue is disabled)."""
        async with message.channel.typing():
            if self._agent is None:
                await message.reply(
                    "I'm still starting up. Please try again in a moment.",
                    mention_author=True,
                )
                return

            try:
                response = await self._agent.generate_response(
                    user_id=message.author.id,
                    channel_id=message.channel.id,
                    message=content,
                )
            except Exception:
                log.exception(
                    "response_generation_failed",
                    message_length=len(content),
                )
                await message.reply(
                    "I ran into an issue processing your message. Please try again.",
                    mention_author=True,
                )
                return

            await self._send_long_reply(message, response)

    async def _handle_dev_event(self, message: discord.Message) -> None:
        """Handle a dev-agent webhook message by routing to the dev_watcher skill.

        Parses embed fields from the webhook and sends ingestion requests
        to the agent. No reply is sent back (passive ingestion).
        """
        if self._agent is None:
            log.debug("dev_event_ignored_agent_not_ready")
            return

        for embed in message.embeds:
            event_type = embed.title or "unknown"
            description = embed.description or ""

            # Extract structured fields from embed
            fields: dict[str, str] = {}
            for ef in embed.fields:
                name = ef.name or ""
                value = ef.value or ""
                fields[name] = value

            # Map event type to skill intent
            intent_map = {
                "commit": "dev_ingest_commit",
                "annotation": "dev_ingest_annotation",
                "session": "dev_ingest_session",
                "tag": "dev_ingest_tag",
                "deploy": "dev_ingest_deploy",
                "ci_result": "dev_ingest_ci_result",
                "container_project": "dev_ingest_container_project",
                "cleanup_approval": "dev_ingest_cleanup_approval",
                "cleanup_report": "dev_ingest_cleanup_report",
            }
            intent = intent_map.get(event_type, "dev_ingest_commit")

            # Build context from embed fields
            context: dict[str, Any] = {
                "skill_name": "dev_watcher",
                **fields,
            }

            # Route through skills client if available
            try:
                from zetherion_ai.skills.base import SkillRequest

                client = await self._agent._get_skills_client()
                if client:
                    request = SkillRequest(
                        user_id=str(message.author.id),
                        intent=intent,
                        message=description,
                        context=context,
                    )
                    response = await client.handle_request(request)
                    log.info(
                        "dev_event_ingested",
                        event_type=event_type,
                        success=response.success,
                        project=fields.get("project", ""),
                    )
                else:
                    log.warning("dev_event_skipped_no_skills_client")
            except Exception:
                log.exception("dev_event_ingestion_failed", event_type=event_type)

    async def _maybe_handle_dev_watcher_dm(
        self,
        message: discord.Message,
        content: str,
    ) -> bool:
        """Handle owner-only dev-watcher DM commands and wizard flow."""
        lowered = content.strip().lower()
        if not lowered:
            return False

        if await self._continue_dev_watcher_wizard(message, lowered):
            return True

        if any(phrase in lowered for phrase in self._DEV_WATCHER_STATUS_PHRASES):
            await self._handle_dev_watcher_status_dm(message)
            return True

        if any(phrase in lowered for phrase in self._DEV_WATCHER_HELP_PHRASES):
            await message.reply(
                "Dev watcher DM commands:\n"
                "- `implement dev watcher` to run setup\n"
                "- `dev watcher status` to check health and approvals\n"
                "- `cancel` while selecting a guild to stop the wizard",
                mention_author=True,
            )
            return True

        if any(phrase in lowered for phrase in self._DEV_WATCHER_TRIGGER_PHRASES):
            await self._start_dev_watcher_wizard(message)
            return True

        return False

    async def _is_owner_or_admin(self, user_id: int) -> bool:
        """Return True when user has owner/admin privileges."""
        settings = get_settings()
        if settings.owner_user_id is not None and settings.owner_user_id == user_id:
            return True
        if self._user_manager is None:
            return False
        role = await self._user_manager.get_role(user_id)
        return role is not None and ROLE_HIERARCHY.get(role, 0) >= ROLE_HIERARCHY["admin"]

    async def _start_dev_watcher_wizard(self, message: discord.Message) -> None:
        """Start owner-only provisioning wizard for dev watcher."""
        if not await self._is_owner_or_admin(message.author.id):
            await message.reply(
                "Dev watcher provisioning is owner/admin only.",
                mention_author=True,
            )
            return

        manageable, blocked = await self._select_manageable_guilds(message.author.id)
        if not manageable:
            details = "\n".join(f"- {item}" for item in blocked) if blocked else "- none found"
            await message.reply(
                "I could not find a guild where I can create channels/webhooks for you.\n"
                "Required bot permissions: `Manage Channels` and `Manage Webhooks`.\n"
                f"Guild checks:\n{details}",
                mention_author=True,
            )
            return

        if len(manageable) == 1:
            await self._run_dev_watcher_provisioning(message, manageable[0])
            return

        guild_ids = [g.id for g in manageable]
        self._dev_watcher_wizards[message.author.id] = {
            "state": "awaiting_guild_selection",
            "guild_ids": guild_ids,
            "started_at": time.time(),
        }
        lines = ["Choose a guild for dev watcher setup by replying with a number:"]
        for idx, guild in enumerate(manageable, start=1):
            lines.append(f"{idx}. {guild.name} (`{guild.id}`)")
        lines.append("Reply `cancel` to abort.")
        await self._send_long_reply(message, "\n".join(lines), mention_author=True)

    async def _continue_dev_watcher_wizard(
        self,
        message: discord.Message,
        lowered_content: str,
    ) -> bool:
        """Continue guild-selection wizard when a session is active."""
        session = self._dev_watcher_wizards.get(message.author.id)
        if session is None:
            return False

        started_at = float(session.get("started_at", 0))
        if time.time() - started_at > self._DEV_WATCHER_WIZARD_TIMEOUT_SECONDS:
            self._dev_watcher_wizards.pop(message.author.id, None)
            await message.reply("Dev watcher setup timed out. Send `implement dev watcher` again.")
            return True

        if session.get("state") != "awaiting_guild_selection":
            self._dev_watcher_wizards.pop(message.author.id, None)
            return False

        if lowered_content in {"cancel", "stop", "abort"}:
            self._dev_watcher_wizards.pop(message.author.id, None)
            await message.reply("Dev watcher setup cancelled.", mention_author=True)
            return True

        guild_ids = [int(gid) for gid in session.get("guild_ids", []) if isinstance(gid, int)]
        if not guild_ids:
            self._dev_watcher_wizards.pop(message.author.id, None)
            await message.reply("Dev watcher setup state was invalid. Start again.")
            return True

        selected_id: int | None = None
        if lowered_content.isdigit():
            numeric = int(lowered_content)
            if 1 <= numeric <= len(guild_ids):
                selected_id = guild_ids[numeric - 1]
            elif numeric in guild_ids:
                selected_id = numeric

        if selected_id is None:
            await message.reply("Please reply with a valid guild number (or `cancel`).")
            return True

        guild = self.get_guild(selected_id)
        if guild is None:
            self._dev_watcher_wizards.pop(message.author.id, None)
            await message.reply("Selected guild is no longer available. Start again.")
            return True

        self._dev_watcher_wizards.pop(message.author.id, None)
        await self._run_dev_watcher_provisioning(message, guild)
        return True

    async def _select_manageable_guilds(
        self,
        user_id: int,
    ) -> tuple[list[discord.Guild], list[str]]:
        """Return guilds where the user is present and bot has setup permissions."""
        manageable: list[discord.Guild] = []
        blocked: list[str] = []
        for guild in self.guilds:
            member = await self._resolve_guild_member(guild, user_id)
            if member is None:
                continue

            bot_member = guild.me
            if bot_member is None and self.user is not None:
                bot_member = guild.get_member(self.user.id)
            if bot_member is None:
                blocked.append(f"{guild.name}: bot membership unavailable")
                continue

            perms = bot_member.guild_permissions
            missing: list[str] = []
            if not perms.manage_channels:
                missing.append("Manage Channels")
            if not perms.manage_webhooks:
                missing.append("Manage Webhooks")
            if missing:
                blocked.append(f"{guild.name}: missing {', '.join(missing)}")
                continue

            manageable.append(guild)
        return manageable, blocked

    async def _resolve_guild_member(
        self,
        guild: discord.Guild,
        user_id: int,
    ) -> discord.Member | None:
        """Resolve a user as a member of a guild."""
        member = guild.get_member(user_id)
        if member is not None:
            return member
        try:
            return await guild.fetch_member(user_id)
        except Exception:
            return None

    async def _run_dev_watcher_provisioning(
        self,
        message: discord.Message,
        guild: discord.Guild,
    ) -> None:
        """Execute end-to-end dev-watcher provisioning for one guild."""
        await message.reply(
            f"Starting dev watcher provisioning for **{guild.name}**.",
            mention_author=True,
        )

        available, availability_detail = await self._ensure_dev_agent_available()
        if not available:
            await message.channel.send(f"Provisioning halted: {availability_detail}")
            return

        await message.channel.send("Creating or reusing Discord category/channel/webhook.")
        try:
            discord_assets = await self._ensure_dev_watcher_discord_assets(guild, message.author.id)
        except Exception:
            log.exception("dev_watcher_discord_asset_provision_failed", guild_id=guild.id)
            await message.channel.send("Provisioning halted: failed while creating Discord assets.")
            return
        if discord_assets is None:
            await message.channel.send(
                "Provisioning halted: could not create Discord assets. "
                "Check bot permissions in the selected guild."
            )
            return
        category, channel, webhook = discord_assets

        await message.channel.send("Bootstrapping dev-agent sidecar and rotating API token.")
        bootstrap = await self._bootstrap_dev_agent(
            webhook_url=webhook.url,
            webhook_name=webhook.name or "zetherion-dev-agent",
        )
        if not bootstrap.get("ok"):
            await message.channel.send(
                f"Provisioning halted during bootstrap: {bootstrap.get('error', 'unknown error')}"
            )
            return

        api_token = str(bootstrap.get("api_token", "")).strip()
        if not api_token:
            await message.channel.send(
                "Provisioning halted: bootstrap did not return an API token."
            )
            return

        try:
            await self._persist_dev_watcher_runtime(
                changed_by=message.author.id,
                guild=guild,
                channel=channel,
                webhook=webhook,
                api_token=api_token,
            )
        except Exception as exc:
            log.exception("dev_watcher_runtime_persist_failed")
            await message.channel.send(
                f"Provisioning halted while saving runtime settings/secrets: {exc}"
            )
            return

        await message.channel.send("Running first discovery cycle.")
        discovery = await self._trigger_initial_discovery(api_token)
        pending = discovery.get("pending_approvals", [])
        discovered = discovery.get("projects_discovered", [])

        summary_lines = [
            "Dev watcher setup complete.",
            f"- Guild: **{guild.name}**",
            f"- Category: **{category.name}**",
            f"- Channel: <#{channel.id}>",
            f"- Webhook: `{webhook.name}`",
            f"- Projects discovered: `{len(discovered)}`",
            f"- Pending approvals: `{len(pending)}`",
            "",
            "New projects will continue to be discovered automatically. "
            "Cleanup remains policy-gated (`ask`, `auto_clean`, `never_clean`).",
        ]
        await self._send_long_message(message.channel, "\n".join(summary_lines))

    def _dev_agent_base_url(self) -> str:
        """Resolve dev-agent API base URL from dynamic settings/env."""
        settings = get_settings()
        raw = get_dynamic("dev_agent", "service_url", settings.dev_agent_service_url)
        if not isinstance(raw, str):
            return "http://zetherion-ai-dev-agent:8787"
        return raw.rstrip("/")

    async def _dev_agent_healthcheck(self) -> bool:
        """Check dev-agent health endpoint reachability."""
        url = f"{self._dev_agent_base_url()}/v1/health"
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(url)
            return resp.status_code == 200
        except httpx.RequestError:
            return False

    def _resolve_updater_secret(self) -> str:
        """Resolve updater secret from env or shared secret file."""
        settings = get_settings()
        if settings.updater_secret.strip():
            return settings.updater_secret.strip()
        secret_path = Path(settings.updater_secret_path).expanduser()
        if not secret_path.exists():
            return ""
        try:
            return secret_path.read_text(encoding="utf-8").strip()
        except OSError:
            return ""

    async def _ensure_dev_agent_available(self) -> tuple[bool, str]:
        """Ensure dev-agent service is available, attempting signed update if needed."""
        if await self._dev_agent_healthcheck():
            return True, "dev-agent healthy"

        settings = get_settings()
        if not settings.auto_update_repo.strip():
            return (
                False,
                "dev-agent service is unavailable and AUTO_UPDATE_REPO is not configured",
            )

        from zetherion_ai.updater.manager import UpdateManager, UpdateStatus

        manager = UpdateManager(
            github_repo=settings.auto_update_repo,
            updater_url=settings.updater_service_url,
            updater_secret=self._resolve_updater_secret(),
            github_token=(
                settings.github_token.get_secret_value()
                if settings.github_token is not None
                else None
            ),
            verify_signatures=settings.updater_verify_signatures,
            verify_identity=settings.updater_verify_identity,
            verify_oidc_issuer=settings.updater_verify_oidc_issuer,
            verify_rekor_url=settings.updater_verify_rekor_url,
            release_manifest_asset=settings.updater_release_manifest_asset,
            release_signature_asset=settings.updater_release_signature_asset,
            release_certificate_asset=settings.updater_release_certificate_asset,
        )

        release = await manager.check_for_update()
        if release is None:
            return False, "dev-agent service is unavailable and no newer signed release was found"

        result = await manager.apply_update(release)
        if result.status != UpdateStatus.SUCCESS:
            return False, f"update failed: {result.error or 'unknown error'}"

        for _ in range(12):
            if await self._dev_agent_healthcheck():
                return True, f"updated to {release.version}"
            await asyncio.sleep(10)
        return False, "update succeeded but dev-agent service did not become healthy"

    async def _ensure_dev_watcher_discord_assets(
        self,
        guild: discord.Guild,
        user_id: int,
    ) -> tuple[discord.CategoryChannel, discord.TextChannel, discord.Webhook] | None:
        """Ensure private ops category, channel, and webhook exist."""
        bot_member = guild.me
        if bot_member is None and self.user is not None:
            bot_member = guild.get_member(self.user.id)
        member = await self._resolve_guild_member(guild, user_id)
        if bot_member is None or member is None:
            return None

        category_name = "Zetherion Ops"
        channel_name = "dev-watcher"
        settings = get_settings()
        webhook_name = get_dynamic(
            "dev_agent",
            "webhook_name",
            settings.dev_agent_webhook_name,
        )
        if not isinstance(webhook_name, str) or not webhook_name.strip():
            webhook_name = "zetherion-dev-agent"

        category = discord.utils.get(guild.categories, name=category_name)
        if category is None:
            overwrites: dict[
                discord.Role | discord.Member | discord.Object,
                discord.PermissionOverwrite,
            ] = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                bot_member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_channels=True,
                    manage_webhooks=True,
                ),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                ),
            }
            category = await guild.create_category(
                category_name,
                overwrites=overwrites,
                reason="Dev watcher onboarding",
            )

        channel = next(
            (
                ch
                for ch in guild.text_channels
                if ch.name == channel_name and ch.category_id == category.id
            ),
            None,
        )
        if channel is None:
            channel_overwrites: dict[
                discord.Role | discord.Member | discord.Object,
                discord.PermissionOverwrite,
            ] = {
                guild.default_role: discord.PermissionOverwrite(view_channel=False),
                bot_member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                    manage_webhooks=True,
                ),
                member: discord.PermissionOverwrite(
                    view_channel=True,
                    send_messages=True,
                    read_message_history=True,
                ),
            }
            channel = await guild.create_text_channel(
                channel_name,
                category=category,
                overwrites=channel_overwrites,
                topic="Zetherion dev watcher events and approval prompts",
                reason="Dev watcher onboarding",
            )

        webhooks = await channel.webhooks()
        webhook = next((item for item in webhooks if item.name == webhook_name), None)
        if webhook is None:
            webhook = await channel.create_webhook(
                name=webhook_name,
                reason="Dev watcher onboarding",
            )

        return category, channel, webhook

    async def _bootstrap_dev_agent(
        self,
        *,
        webhook_url: str,
        webhook_name: str,
    ) -> dict[str, Any]:
        """Bootstrap dev-agent with webhook and schedule configuration."""
        settings = get_settings()
        bootstrap_secret = settings.dev_agent_bootstrap_secret.strip()
        if not bootstrap_secret:
            return {"ok": False, "error": "DEV_AGENT_BOOTSTRAP_SECRET is not configured"}

        cleanup_hour = int(
            get_dynamic("dev_agent", "cleanup_hour", settings.dev_agent_cleanup_hour)
        )
        cleanup_minute = int(
            get_dynamic("dev_agent", "cleanup_minute", settings.dev_agent_cleanup_minute)
        )
        reprompt_hours = int(
            get_dynamic(
                "dev_agent",
                "approval_reprompt_hours",
                settings.dev_agent_approval_reprompt_hours,
            )
        )

        payload = {
            "webhook_url": webhook_url,
            "agent_name": webhook_name,
            "cleanup_hour": cleanup_hour,
            "cleanup_minute": cleanup_minute,
            "approval_reprompt_hours": reprompt_hours,
            "container_monitor_enabled": True,
            "cleanup_enabled": True,
            "rotate_api_token": bool(webhook_url or webhook_name),
        }

        url = f"{self._dev_agent_base_url()}/v1/bootstrap"
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(
                    url,
                    json=payload,
                    headers={"X-Bootstrap-Secret": bootstrap_secret},
                )
        except httpx.RequestError as exc:
            return {"ok": False, "error": f"bootstrap request failed: {exc}"}

        try:
            data = resp.json()
        except Exception:
            data = {}

        if resp.status_code == 200:
            if isinstance(data, dict):
                return {"ok": True, **data}
            return {"ok": False, "error": "bootstrap response was not JSON"}

        if resp.status_code == 409:
            existing_token = (get_secret("dev_agent_api_token", "") or "").strip()
            if existing_token:
                return {"ok": True, "api_token": existing_token, "reused": True}
            err = "bootstrap already completed and no stored API token was found"
            return {"ok": False, "error": err}

        error_detail = data.get("error") if isinstance(data, dict) else f"HTTP {resp.status_code}"
        return {"ok": False, "error": str(error_detail)}

    async def _persist_dev_watcher_runtime(
        self,
        *,
        changed_by: int,
        guild: discord.Guild,
        channel: discord.TextChannel,
        webhook: discord.Webhook,
        api_token: str,
    ) -> None:
        """Persist dev-agent settings and API token."""
        settings = get_settings()
        cleanup_hour = int(
            get_dynamic("dev_agent", "cleanup_hour", settings.dev_agent_cleanup_hour)
        )
        cleanup_minute = int(
            get_dynamic("dev_agent", "cleanup_minute", settings.dev_agent_cleanup_minute)
        )
        reprompt_hours = int(
            get_dynamic(
                "dev_agent",
                "approval_reprompt_hours",
                settings.dev_agent_approval_reprompt_hours,
            )
        )

        if self._settings_manager is not None:
            await self._settings_manager.set(  # type: ignore[attr-defined]
                namespace="dev_agent",
                key="enabled",
                value=True,
                changed_by=changed_by,
                data_type="bool",
            )
            await self._settings_manager.set(  # type: ignore[attr-defined]
                namespace="dev_agent",
                key="cleanup_hour",
                value=cleanup_hour,
                changed_by=changed_by,
                data_type="int",
            )
            await self._settings_manager.set(  # type: ignore[attr-defined]
                namespace="dev_agent",
                key="cleanup_minute",
                value=cleanup_minute,
                changed_by=changed_by,
                data_type="int",
            )
            await self._settings_manager.set(  # type: ignore[attr-defined]
                namespace="dev_agent",
                key="approval_reprompt_hours",
                value=reprompt_hours,
                changed_by=changed_by,
                data_type="int",
            )
            await self._settings_manager.set(  # type: ignore[attr-defined]
                namespace="dev_agent",
                key="discord_channel_id",
                value=str(channel.id),
                changed_by=changed_by,
                data_type="string",
            )
            await self._settings_manager.set(  # type: ignore[attr-defined]
                namespace="dev_agent",
                key="discord_guild_id",
                value=str(guild.id),
                changed_by=changed_by,
                data_type="string",
            )
            await self._settings_manager.set(  # type: ignore[attr-defined]
                namespace="dev_agent",
                key="webhook_name",
                value=str(webhook.name or "zetherion-dev-agent"),
                changed_by=changed_by,
                data_type="string",
            )
            await self._settings_manager.set(  # type: ignore[attr-defined]
                namespace="dev_agent",
                key="webhook_id",
                value=str(webhook.id),
                changed_by=changed_by,
                data_type="string",
            )
            await self._settings_manager.set(  # type: ignore[attr-defined]
                namespace="dev_agent",
                key="service_url",
                value=self._dev_agent_base_url(),
                changed_by=changed_by,
                data_type="string",
            )

        await self._persist_dev_agent_secret(changed_by=changed_by, token=api_token)

    async def _persist_dev_agent_secret(self, *, changed_by: int, token: str) -> None:
        """Persist dev-agent API token in encrypted secrets storage."""
        if self._agent is None:
            raise RuntimeError("Agent is not ready")
        client = await self._agent._get_skills_client()
        if client is None:
            raise RuntimeError("Skills service is unavailable for secret storage")
        await client.put_secret(
            name="dev_agent_api_token",
            value=token,
            changed_by=changed_by,
            description="Dev-agent bearer token provisioned by Discord onboarding",
        )

    async def _trigger_initial_discovery(self, api_token: str) -> dict[str, Any]:
        """Run one discovery cycle via dev-agent API."""
        url = f"{self._dev_agent_base_url()}/v1/discovery/run"
        headers = {"Authorization": f"Bearer {api_token}"}
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(url, headers=headers, json={})
        except httpx.RequestError as exc:
            return {"projects_discovered": [], "pending_approvals": [], "error": str(exc)}
        if resp.status_code != 200:
            return {
                "projects_discovered": [],
                "pending_approvals": [],
                "error": f"HTTP {resp.status_code}",
            }
        try:
            data = resp.json()
        except Exception:
            return {"projects_discovered": [], "pending_approvals": [], "error": "invalid JSON"}
        if isinstance(data, dict):
            return data
        return {"projects_discovered": [], "pending_approvals": []}

    async def _handle_dev_watcher_status_dm(self, message: discord.Message) -> None:
        """Return status summary for dev watcher onboarding/runtime."""
        if not await self._is_owner_or_admin(message.author.id):
            await message.reply("Dev watcher status is owner/admin only.", mention_author=True)
            return

        health = await self._dev_agent_healthcheck()
        token = (get_secret("dev_agent_api_token", "") or "").strip()
        projects_count = 0
        pending_count = 0
        if health and token:
            try:
                async with httpx.AsyncClient(timeout=15) as client:
                    projects_resp = await client.get(
                        f"{self._dev_agent_base_url()}/v1/projects",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                    pending_resp = await client.get(
                        f"{self._dev_agent_base_url()}/v1/approvals/pending",
                        headers={"Authorization": f"Bearer {token}"},
                    )
                if projects_resp.status_code == 200:
                    payload = projects_resp.json()
                    if isinstance(payload, dict) and isinstance(payload.get("projects"), list):
                        projects_count = len(payload["projects"])
                if pending_resp.status_code == 200:
                    payload = pending_resp.json()
                    if isinstance(payload, dict) and isinstance(payload.get("pending"), list):
                        pending_count = len(payload["pending"])
            except Exception:
                log.exception("dev_watcher_status_fetch_failed")

        settings = get_settings()
        guild_id = get_dynamic("dev_agent", "discord_guild_id", settings.dev_agent_discord_guild_id)
        channel_id = get_dynamic(
            "dev_agent",
            "discord_channel_id",
            settings.dev_agent_discord_channel_id,
        )
        enabled = bool(get_dynamic("dev_agent", "enabled", settings.dev_agent_enabled))

        lines = [
            "**Dev Watcher Status**",
            f"- Enabled: `{enabled}`",
            f"- API health: `{'ok' if health else 'unreachable'}`",
            f"- Stored API token: `{'yes' if bool(token) else 'no'}`",
            f"- Guild ID: `{guild_id or 'unset'}`",
            f"- Channel ID: `{channel_id or 'unset'}`",
            f"- Projects discovered: `{projects_count}`",
            f"- Pending approvals: `{pending_count}`",
        ]
        await self._send_long_reply(message, "\n".join(lines), mention_author=True)

    async def _check_security(
        self,
        interaction: discord.Interaction[discord.Client],
        content: str | None = None,
    ) -> bool:
        """Run security checks (allowlist, rate limit, injection).

        Returns True if request is allowed, False if blocked (response already sent).
        """
        if self._user_manager and not await self._user_manager.is_allowed(interaction.user.id):
            await interaction.response.send_message(
                "Sorry, you're not authorized to use this bot.",
                ephemeral=True,
            )
            return False

        allowed, warning = self._rate_limiter.check(interaction.user.id)
        if not allowed:
            await interaction.response.send_message(
                warning or "Rate limited. Please wait.",
                ephemeral=True,
            )
            return False

        intent_hint = self._infer_security_intent_hint(content or "")
        if content and await self._is_security_blocked(
            content=content,
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
            intent_hint=intent_hint,
        ):
            await interaction.response.send_message(
                "I noticed some unusual patterns in your message. Could you rephrase it?",
                ephemeral=True,
            )
            return False

        return True

    async def _is_security_blocked(
        self,
        *,
        content: str,
        user_id: int,
        channel_id: int,
        intent_hint: str | None = None,
    ) -> bool:
        """Return True when message content should be blocked by security checks."""
        pipeline = self._security_pipeline
        if pipeline is None:
            return detect_prompt_injection(content)

        try:
            verdict = await pipeline.analyze(
                content,
                user_id=user_id,
                channel_id=channel_id,
                request_id=str(uuid4())[:12],
                intent_hint=intent_hint,
            )
        except Exception:
            log.exception("security_pipeline_failed")
            return detect_prompt_injection(content)

        if verdict.action == ThreatAction.BLOCK:
            return True
        if verdict.action == ThreatAction.FLAG:
            log.warning(
                "message_flagged_by_security",
                user_id=user_id,
                channel_id=channel_id,
                score=verdict.score,
                tier=verdict.tier_reached,
            )
        return False

    @staticmethod
    def _infer_security_intent_hint(content: str) -> str | None:
        """Best-effort hint to help reduce Tier-2 false positives."""
        text = content.strip().lower()
        if not text:
            return None

        memory_patterns = (
            r"^remember(?:\s+that)?\b",
            r"^my\s+\w+\s+is\b",
            r"^i\s+(?:am|work as|prefer|like)\b",
        )
        if any(re.search(pattern, text) for pattern in memory_patterns):
            return "memory_store"

        if text.startswith("what do you know about") or "favorite" in text:
            return "memory_recall"
        return None

    async def _handle_ask(
        self,
        interaction: discord.Interaction[discord.Client],
        question: str,
    ) -> None:
        """Handle /ask command."""
        structlog.contextvars.bind_contextvars(
            request_id=str(uuid4())[:12],
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
        )
        try:
            if not await self._check_security(interaction, question):
                return

            await interaction.response.defer()

            if self._agent is None:
                await interaction.followup.send(
                    "I'm still starting up. Please try again in a moment."
                )
                return

            response = await self._agent.generate_response(
                user_id=interaction.user.id,
                channel_id=interaction.channel_id or 0,
                message=question,
            )

            # Send response
            await self._send_long_interaction_response(interaction, response)
        finally:
            structlog.contextvars.clear_contextvars()

    async def _handle_remember(
        self,
        interaction: discord.Interaction[discord.Client],
        content: str,
    ) -> None:
        """Handle /remember command."""
        structlog.contextvars.bind_contextvars(
            request_id=str(uuid4())[:12],
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
        )
        try:
            if not await self._check_security(interaction, content):
                return

            await interaction.response.defer(ephemeral=True)

            if self._agent is None:
                await interaction.followup.send(
                    "I'm still starting up. Please try again in a moment."
                )
                return

            try:
                confirmation = await self._agent.store_memory_from_request(
                    content,
                    user_id=interaction.user.id,
                )
                await interaction.followup.send(confirmation)
            except Exception as e:
                log.error("remember_command_error", error=str(e))
                await interaction.followup.send(
                    "Sorry, something went wrong while saving that memory. Please try again."
                )
        finally:
            structlog.contextvars.clear_contextvars()

    async def _handle_search(
        self,
        interaction: discord.Interaction[discord.Client],
        query: str,
    ) -> None:
        """Handle /search command."""
        structlog.contextvars.bind_contextvars(
            request_id=str(uuid4())[:12],
            user_id=interaction.user.id,
            channel_id=interaction.channel_id or 0,
        )
        try:
            if not await self._check_security(interaction, query):
                return

            await interaction.response.defer()

            try:
                # Search memories
                memories = await self._memory.search_memories(
                    query=query,
                    limit=5,
                    user_id=interaction.user.id,
                )

                if not memories:
                    await interaction.followup.send("No matching memories found.")
                    return

                # Format results
                lines = ["**Search Results:**\n"]
                for i, mem in enumerate(memories, 1):
                    score_pct = int(mem["score"] * 100)
                    lines.append(f"{i}. [{score_pct}%] {mem['content'][:200]}")

                await interaction.followup.send("\n".join(lines))
            except Exception as e:
                log.error("search_command_error", error=str(e))
                await interaction.followup.send(
                    "Sorry, something went wrong while searching. Please try again."
                )
        finally:
            structlog.contextvars.clear_contextvars()

    async def _handle_channels(
        self,
        interaction: discord.Interaction[discord.Client],
    ) -> None:
        """Handle /channels command - list accessible channels."""
        if self._user_manager and not await self._user_manager.is_allowed(interaction.user.id):
            await interaction.response.send_message(
                "Sorry, you're not authorized to use this bot.",
                ephemeral=True,
            )
            return

        await interaction.response.defer(ephemeral=True)

        if not interaction.guild:
            await interaction.followup.send("This command only works in servers, not DMs.")
            return

        # Get all channels the bot can see
        text_channels = []
        voice_channels = []
        categories = []

        for channel in interaction.guild.channels:
            permissions = channel.permissions_for(interaction.guild.me)

            # Check if bot can view the channel
            if not permissions.view_channel:
                continue

            if isinstance(channel, discord.TextChannel):
                can_send = "\u2713" if permissions.send_messages else "\u2717"
                can_read = "\u2713" if permissions.read_message_history else "\u2717"
                text_channels.append(f"  #{channel.name} (Send: {can_send}, Read: {can_read})")
            elif isinstance(channel, discord.VoiceChannel):
                can_connect = "\u2713" if permissions.connect else "\u2717"
                voice_channels.append(f"  \U0001f50a {channel.name} (Connect: {can_connect})")
            elif isinstance(channel, discord.CategoryChannel):
                categories.append(f"  \U0001f4c1 {channel.name}")

        # Format response
        lines = [
            f"**Channels in {interaction.guild.name}**\n",
            f"**Text Channels ({len(text_channels)}):**",
        ]
        lines.extend(text_channels if text_channels else ["  None"])

        if voice_channels:
            lines.append(f"\n**Voice Channels ({len(voice_channels)}):**")
            lines.extend(voice_channels)

        if categories:
            lines.append(f"\n**Categories ({len(categories)}):**")
            lines.extend(categories)

        lines.append(f"\n**Total Accessible:** {len(text_channels) + len(voice_channels)} channels")

        response = "\n".join(lines)

        await self._send_long_interaction_response(interaction, response)

    async def _send_long_message(
        self,
        channel: discord.abc.Messageable,
        content: str,
        max_length: int = MAX_DISCORD_MESSAGE_LENGTH,
    ) -> None:
        """Send a message, splitting if it exceeds Discord's limit.

        Args:
            channel: The channel to send to.
            content: The message content.
            max_length: Maximum message length.
        """
        if len(content) <= max_length:
            await channel.send(content)
            return

        parts = split_text_chunks(content, max_length=max_length)

        for part in parts:
            if part:
                await channel.send(part)

    async def _send_long_reply(
        self,
        message: discord.Message,
        content: str,
        mention_author: bool = True,
        max_length: int = MAX_DISCORD_MESSAGE_LENGTH,
    ) -> None:
        """Send a reply to a message, splitting if it exceeds Discord's limit.

        First chunk is sent as a reply; subsequent chunks as channel messages.

        Args:
            message: The message to reply to.
            content: The reply content.
            mention_author: Whether to mention the author in the reply.
            max_length: Maximum message length.
        """
        if len(content) <= max_length:
            await message.reply(content, mention_author=mention_author)
            return

        parts = split_text_chunks(content, max_length=max_length)

        for i, part in enumerate(parts):
            if part:
                if i == 0:
                    await message.reply(part, mention_author=mention_author)
                else:
                    await message.channel.send(part)

    async def _send_long_interaction_response(
        self,
        interaction: discord.Interaction[discord.Client],
        content: str,
        max_length: int = MAX_DISCORD_MESSAGE_LENGTH,
    ) -> None:
        """Send an interaction followup, splitting if too long."""
        if len(content) <= max_length:
            await interaction.followup.send(content)
            return

        parts = split_text_chunks(content, max_length=max_length)

        for part in parts:
            if part:
                await interaction.followup.send(part)

    async def send_dm(self, user_id: str, message: str) -> bool:
        """Send a DM to a Discord user ID string."""
        try:
            discord_user_id = int(user_id)
        except (TypeError, ValueError):
            log.warning("send_dm_invalid_user_id", user_id=user_id)
            return False

        try:
            user = self.get_user(discord_user_id)
            if user is None:
                user = await self.fetch_user(discord_user_id)
            if user is None:
                log.warning("send_dm_user_not_found", user_id=user_id)
                return False

            await self._send_long_message(user, message)
            return True
        except Exception:
            log.exception("send_dm_failed", user_id=user_id)
            return False

    # ------------------------------------------------------------------
    # Admin permission helper
    # ------------------------------------------------------------------

    async def _require_admin(
        self,
        interaction: discord.Interaction[discord.Client],
    ) -> bool:
        """Check the caller has admin+ role. Sends ephemeral error if not.

        Returns True if the caller is admin or owner.
        """
        if self._user_manager is None:
            await interaction.response.send_message(
                "User management is not configured.", ephemeral=True
            )
            return False

        caller_role = await self._user_manager.get_role(interaction.user.id)
        if caller_role is None or ROLE_HIERARCHY.get(caller_role, 0) < ROLE_HIERARCHY["admin"]:
            await interaction.response.send_message(
                "You need admin or owner privileges to use this command.",
                ephemeral=True,
            )
            return False
        return True

    # ------------------------------------------------------------------
    # RBAC command handlers
    # ------------------------------------------------------------------

    async def _handle_allow(
        self,
        interaction: discord.Interaction[discord.Client],
        user: discord.User,
        role: str,
    ) -> None:
        """Handle /allow command."""
        if not await self._require_admin(interaction):
            return

        assert self._user_manager is not None  # guarded by _require_admin
        await interaction.response.defer(ephemeral=True)

        success = await self._user_manager.add_user(
            user_id=user.id, role=role, added_by=interaction.user.id
        )
        if success:
            await interaction.followup.send(f"Added {user.mention} with role **{role}**.")
        else:
            await interaction.followup.send(
                f"Could not add {user.mention}. Check role validity and your permissions."
            )

    async def _handle_deny(
        self,
        interaction: discord.Interaction[discord.Client],
        user: discord.User,
    ) -> None:
        """Handle /deny command."""
        if not await self._require_admin(interaction):
            return

        assert self._user_manager is not None
        await interaction.response.defer(ephemeral=True)

        success = await self._user_manager.remove_user(
            user_id=user.id, removed_by=interaction.user.id
        )
        if success:
            await interaction.followup.send(f"Removed {user.mention} from the allowlist.")
        else:
            await interaction.followup.send(
                f"Could not remove {user.mention}. They may be an owner or not in the list."
            )

    async def _handle_role(
        self,
        interaction: discord.Interaction[discord.Client],
        user: discord.User,
        role: str,
    ) -> None:
        """Handle /role command."""
        if not await self._require_admin(interaction):
            return

        assert self._user_manager is not None
        await interaction.response.defer(ephemeral=True)

        success = await self._user_manager.set_role(
            user_id=user.id, new_role=role, changed_by=interaction.user.id
        )
        if success:
            await interaction.followup.send(f"Changed {user.mention}'s role to **{role}**.")
        else:
            await interaction.followup.send(
                f"Could not change {user.mention}'s role. Check role validity and your permissions."
            )

    async def _handle_allowlist(
        self,
        interaction: discord.Interaction[discord.Client],
        role: str | None = None,
    ) -> None:
        """Handle /allowlist command."""
        if not await self._require_admin(interaction):
            return

        assert self._user_manager is not None
        await interaction.response.defer(ephemeral=True)

        users = await self._user_manager.list_users(role_filter=role)
        if not users:
            await interaction.followup.send("No users found.")
            return

        lines = ["**Allowed Users:**\n"]
        for u in users:
            uid = u["discord_user_id"]
            lines.append(f"- <@{uid}> — **{u['role']}** (added {u['created_at']:%Y-%m-%d})")

        await self._send_long_interaction_response(interaction, "\n".join(lines))

    async def _handle_audit(
        self,
        interaction: discord.Interaction[discord.Client],
        limit: int = 20,
    ) -> None:
        """Handle /audit command."""
        if not await self._require_admin(interaction):
            return

        assert self._user_manager is not None
        await interaction.response.defer(ephemeral=True)

        entries = await self._user_manager.get_audit_log(limit=limit)
        if not entries:
            await interaction.followup.send("No audit log entries.")
            return

        lines = [f"**Audit Log** (last {len(entries)} entries):\n"]
        for e in entries:
            ts = e["created_at"]
            lines.append(
                f"- `{ts:%Y-%m-%d %H:%M}` **{e['action']}** "
                f"target=<@{e['target_user_id']}> by=<@{e['performed_by']}>"
            )

        await self._send_long_interaction_response(interaction, "\n".join(lines))

    # ------------------------------------------------------------------
    # Settings command handlers
    # ------------------------------------------------------------------

    async def _handle_config_list(
        self,
        interaction: discord.Interaction[discord.Client],
        namespace: str | None = None,
    ) -> None:
        """Handle /config_list command."""
        if not await self._require_admin(interaction):
            return

        if self._settings_manager is None:
            await interaction.response.send_message(
                "Settings manager is not configured.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        settings = await self._settings_manager.get_all(namespace=namespace)  # type: ignore[attr-defined]
        if not settings:
            await interaction.followup.send(
                f"No settings found{f' in namespace **{namespace}**' if namespace else ''}."
            )
            return

        lines = ["**Runtime Settings:**\n"]
        for ns, entries in sorted(settings.items()):
            lines.append(f"**[{ns}]**")
            for k, v in sorted(entries.items()):
                lines.append(f"  `{k}` = `{v}`")

        await self._send_long_interaction_response(interaction, "\n".join(lines))

    async def _handle_config_set(
        self,
        interaction: discord.Interaction[discord.Client],
        namespace: str,
        key: str,
        value: str,
    ) -> None:
        """Handle /config_set command."""
        if not await self._require_admin(interaction):
            return

        if self._settings_manager is None:
            await interaction.response.send_message(
                "Settings manager is not configured.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        try:
            typed_value, data_type = self._coerce_setting_value(value)
            await self._settings_manager.set(  # type: ignore[attr-defined]
                namespace=namespace,
                key=key,
                value=typed_value,
                changed_by=interaction.user.id,
                data_type=data_type,
            )
            display_value = json.dumps(typed_value) if data_type == "json" else str(typed_value)
            await interaction.followup.send(
                f"Set **{namespace}.{key}** = `{display_value}` (type: `{data_type}`)"
            )
        except ValueError as e:
            await interaction.followup.send(f"Invalid setting: {e}")

    @staticmethod
    def _coerce_setting_value(value: str) -> tuple[Any, str]:
        """Infer a settings value type from slash-command text input."""
        stripped = value.strip()
        lowered = stripped.lower()

        if lowered in {"true", "false", "yes", "no"}:
            return lowered in {"true", "yes"}, "bool"

        if stripped and (
            stripped.isdigit() or (stripped.startswith("-") and stripped[1:].isdigit())
        ):
            try:
                return int(stripped), "int"
            except ValueError:
                pass

        if any(ch in stripped for ch in (".", "e", "E")):
            try:
                return float(stripped), "float"
            except ValueError:
                pass

        if (stripped.startswith("{") and stripped.endswith("}")) or (
            stripped.startswith("[") and stripped.endswith("]")
        ):
            try:
                return json.loads(stripped), "json"
            except json.JSONDecodeError:
                pass

        return value, "string"

    async def _handle_config_reset(
        self,
        interaction: discord.Interaction[discord.Client],
        namespace: str,
        key: str,
    ) -> None:
        """Handle /config_reset command."""
        if not await self._require_admin(interaction):
            return

        if self._settings_manager is None:
            await interaction.response.send_message(
                "Settings manager is not configured.", ephemeral=True
            )
            return

        await interaction.response.defer(ephemeral=True)

        deleted = await self._settings_manager.delete(  # type: ignore[attr-defined]
            namespace=namespace,
            key=key,
            deleted_by=interaction.user.id,
        )
        if deleted:
            await interaction.followup.send(f"Reset **{namespace}.{key}** to default.")
        else:
            await interaction.followup.send(
                f"Setting **{namespace}.{key}** was not found in the database."
            )
