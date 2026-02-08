"""Discord bot implementation."""

import asyncio
import contextlib
import time
from uuid import uuid4

import discord
import structlog
from discord import app_commands

from zetherion_ai.agent.core import Agent
from zetherion_ai.config import get_settings
from zetherion_ai.constants import KEEP_WARM_INTERVAL_SECONDS, MAX_DISCORD_MESSAGE_LENGTH
from zetherion_ai.discord.security import (
    RateLimiter,
    detect_prompt_injection,
)
from zetherion_ai.discord.user_manager import ROLE_HIERARCHY, UserManager
from zetherion_ai.logging import get_logger
from zetherion_ai.memory.qdrant import QdrantMemory

log = get_logger("zetherion_ai.discord.bot")


class ZetherionAIBot(discord.Client):
    """Zetherion AI Discord bot."""

    def __init__(
        self,
        memory: QdrantMemory,
        user_manager: UserManager | None = None,
        settings_manager: object | None = None,
    ) -> None:
        """Initialize the bot.

        Args:
            memory: The memory system.
            user_manager: Optional UserManager for RBAC.
            settings_manager: Optional SettingsManager for runtime config.
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
        self._keep_warm_task: asyncio.Task[None] | None = None
        self._last_message_time: float = 0.0

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

        # Warm up the Ollama model to avoid cold start delays
        log.info("warming_up_ollama_model")
        warmup_success = await self._agent.warmup()
        if warmup_success:
            log.info("ollama_warmup_successful")
        else:
            log.warning("ollama_warmup_failed", note="First request may be slow")

        # Start background task to keep model warm
        self._keep_warm_task = asyncio.create_task(self._keep_warm_loop())
        log.info("keep_warm_task_started", interval_seconds=KEEP_WARM_INTERVAL_SECONDS)

        # Sync commands
        await self._tree.sync()
        log.info("commands_synced")

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

    async def close(self) -> None:
        """Clean up resources when bot is closing."""
        # Cancel keep-warm task
        task = self._keep_warm_task
        if task is not None and not task.done():
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task
            log.info("keep_warm_task_stopped")

        # Clean up agent resources
        if (
            self._agent
            and hasattr(self._agent, "_inference_broker")
            and self._agent._inference_broker
        ):
            await self._agent._inference_broker.close()

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

            # Check for prompt injection
            if detect_prompt_injection(message.content):
                await message.reply(
                    "I noticed some unusual patterns in your message. "
                    "Could you rephrase your question?",
                    mention_author=True,
                )
                return

            # Generate response with timed operations
            async with message.channel.typing():
                content = message.content
                # Remove bot mention from content
                if is_mention and self.user:
                    content = content.replace(f"<@{self.user.id}>", "").strip()

                if not content:
                    await message.reply(
                        "How can I help you?",
                        mention_author=True,
                    )
                    return

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

                # Send response as a threaded reply
                await self._send_long_reply(message, response)
        finally:
            structlog.contextvars.clear_contextvars()

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

        if content and detect_prompt_injection(content):
            await interaction.response.send_message(
                "I noticed some unusual patterns in your message. Could you rephrase it?",
                ephemeral=True,
            )
            return False

        return True

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

        # Split if too long (Discord 2000 char limit)
        if len(response) > MAX_DISCORD_MESSAGE_LENGTH:
            # Send first batch
            first_batch = text_channels[:20] if len(text_channels) > 0 else []
            await interaction.followup.send(
                f"**Text Channels ({len(text_channels)}):**\n" + "\n".join(first_batch)
            )
            # Send remaining if needed
            if len(text_channels) > 20:
                remaining = text_channels[20:40]
                await interaction.followup.send("\n".join(remaining))
        else:
            await interaction.followup.send(response)

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

        # Split on paragraph boundaries if possible
        parts = []
        current = ""

        for line in content.split("\n"):
            if len(current) + len(line) + 1 <= max_length:
                current += line + "\n"
            else:
                if current:
                    parts.append(current.strip())
                current = line + "\n"

        if current:
            parts.append(current.strip())

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

        # Split on paragraph boundaries if possible
        parts: list[str] = []
        current = ""

        for line in content.split("\n"):
            if len(current) + len(line) + 1 <= max_length:
                current += line + "\n"
            else:
                if current:
                    parts.append(current.strip())
                current = line + "\n"

        if current:
            parts.append(current.strip())

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

        parts = []
        current = ""
        for line in content.split("\n"):
            if len(current) + len(line) + 1 <= max_length:
                current += line + "\n"
            else:
                if current:
                    parts.append(current.strip())
                current = line + "\n"
        if current:
            parts.append(current.strip())

        for part in parts:
            if part:
                await interaction.followup.send(part)

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
                f"Could not change {user.mention}'s role. "
                "Check role validity and your permissions."
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
            await self._settings_manager.set(  # type: ignore[attr-defined]
                namespace=namespace,
                key=key,
                value=value,
                changed_by=interaction.user.id,
            )
            await interaction.followup.send(f"Set **{namespace}.{key}** = `{value}`")
        except ValueError as e:
            await interaction.followup.send(f"Invalid setting: {e}")

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
