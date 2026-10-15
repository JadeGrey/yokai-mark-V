"""Admin-only slash commands, grouped under /admin."""

from __future__ import annotations

import platform
import random
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import discord
import ytmusicapi
from discord import app_commands
from discord.ext import commands

from yokai.errors import (
    InvalidGameChoiceError,
    InvalidPaymentError,
    NoCharacterError,
    YokaiError,
)
from yokai.gambling.lobby import GAME_SPECS
from yokai.rpg import gear as rpg_gear
from yokai.rpg.gear_store import ItemDrop
from yokai.textutil import escape_md
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.views import DiagView

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

GAME_CHOICES = [
    app_commands.Choice(name=spec.label, value=name) for name, spec in GAME_SPECS.items()
]


class AdminCog(commands.Cog, name="Admin"):
    """Tools for the owner and the adminlist, all under the /admin group."""

    admin = app_commands.Group(name="admin", description="Admin-only tools.")
    rpg = app_commands.Group(name="rpg", description="Guild RPG tools.", parent=admin)

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot
        self._rpg_rng = random.Random()

    def _avatar_url(self) -> Optional[str]:
        return str(self.bot.user.display_avatar.url) if self.bot.user else None

    async def _send_domain_error(self, interaction: discord.Interaction, err: Exception) -> None:
        embed = EmbedFactory.error(
            message=getattr(err, "message", str(err)),
            title=getattr(err, "title", "That didn't work"),
            user_hint=getattr(err, "user_hint", None),
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    async def _deny_unless_admin(self, interaction: discord.Interaction, hint: str) -> bool:
        """Send the ephemeral 'Admins only' refusal and return True when the caller is no admin."""
        if await self.bot.moderation.is_admin(interaction.user.id):
            return False
        embed = EmbedFactory.error(
            message="Access denied: this command is restricted to Yokai admins.",
            title="Admins only",
            user_hint=hint,
            context="Permissions",
            bot_avatar_url=self._avatar_url(),
            include_quip=False,
        )
        await send(interaction, embed=embed, ephemeral=True)
        return True

    @admin.command(name="economy", description="Grant or deduct a member's Renown.")
    @app_commands.describe(user="Member to adjust", amount="Renown to add (negative to deduct)")
    async def economy(
        self, interaction: discord.Interaction, user: discord.Member, amount: int
    ) -> None:
        """Balance correction, always ephemeral, floored at 0."""
        if await self._deny_unless_admin(
            interaction, "Ask the bot owner if you need a balance corrected."
        ):
            return

        if user.bot:
            await self._send_domain_error(
                interaction,
                InvalidPaymentError("You can only adjust another human member of the server."),
            )
            return

        await interaction.response.defer(ephemeral=True)
        applied, new_balance = await self.bot.db.admin_adjust_wallet(
            user.id, amount, welcome_bonus=self.bot.config.economy_welcome_bonus
        )
        embed = EmbedFactory.economy_admin(
            target_mention=f"<@{user.id}>",
            delta_applied=applied,
            new_balance=new_balance,
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @admin.command(name="challenges", description="List, reroll or set the current challenges.")
    @app_commands.describe(
        period="Which slate",
        action="list shows the active ids, reroll draws a new slate, set chooses the ids",
        templates="For 'set': template ids separated by commas (see 'list')",
    )
    @app_commands.choices(
        period=[
            app_commands.Choice(name="daily", value="daily"),
            app_commands.Choice(name="weekly", value="weekly"),
        ],
        action=[
            app_commands.Choice(name="list", value="list"),
            app_commands.Choice(name="reroll", value="reroll"),
            app_commands.Choice(name="set", value="set"),
        ],
    )
    async def challenges(
        self,
        interaction: discord.Interaction,
        period: app_commands.Choice[str],
        action: app_commands.Choice[str],
        templates: Optional[str] = None,
    ) -> None:
        """Admin tools for the challenge slates; changes are visible to everyone."""
        if await self._deny_unless_admin(interaction, "Ask the owner to add you to the adminlist."):
            return
        engine = self.bot.challenges
        ids: list[str] = []
        if action.value == "set":
            ids = [part.strip()[:40] for part in (templates or "").split(",") if part.strip()][:12]
            if not ids:
                await self._send_domain_error(
                    interaction, ValueError("Give at least one template id, separated by commas.")
                )
                return
        await interaction.response.defer(ephemeral=True)
        try:
            if action.value == "list":
                _, current = await engine.active_slate(period.value)
                title = f"Active {period.value} challenges"
            elif action.value == "reroll":
                current = await engine.reroll(period.value)
                title = f"Rerolled the {period.value} challenges"
            else:
                current = await engine.set_slate(period.value, ids)
                title = f"Set the {period.value} challenges"
        except ValueError as err:
            # The engine echoes admin-supplied ids, so neutralise any markdown in them.
            await self._send_domain_error(interaction, ValueError(escape_md(str(err))))
            return
        lines = [f"`{t.id}` · {escape_md(t.title)} ({t.target}, +{t.reward} base)" for t in current]
        build = EmbedFactory.info if action.value == "list" else EmbedFactory.success
        embed = build(
            title=title,
            description="\n".join(lines) or "No challenges.",
            context="Admin",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    # ------------------------------------------------------------------ /admin rpg

    async def _rpg_admin_guard(
        self, interaction: discord.Interaction, user: discord.Member
    ) -> bool:
        """True when the command must stop (not an admin, a bot target, or the RPG is off)."""
        if await self._deny_unless_admin(interaction, "Ask the owner to add you to the adminlist."):
            return True
        if self.bot.rpg is None:
            await self._send_domain_error(
                interaction, YokaiError("The Guild RPG is switched off (RPG_ENABLED=false).")
            )
            return True
        if user.bot:
            await self._send_domain_error(interaction, YokaiError("Bots can't have an adventurer."))
            return True
        return False

    @rpg.command(name="grant", description="Give a member a piece of gear.")
    @app_commands.describe(user="Who receives it", item="Which item")
    async def rpg_grant(
        self, interaction: discord.Interaction, user: discord.Member, item: str
    ) -> None:
        if await self._rpg_admin_guard(interaction, user):
            return
        runtime = self.bot.rpg
        assert runtime is not None
        definition = runtime.content.items.get(item.strip())
        if definition is None:
            await self._send_domain_error(
                interaction, YokaiError("There is no item with that id.", "Use the autocomplete.")
            )
            return
        if await runtime.store.get_character(user.id) is None:
            await self._send_domain_error(interaction, NoCharacterError(own=False))
            return
        rules = runtime.content.balance.gear
        try:
            created = await runtime.gear.grant_item(
                user.id,
                ItemDrop(
                    definition.id,
                    rpg_gear.roll_stats(definition, rules, self._rpg_rng),
                    definition.max_durability,
                ),
                inventory_cap=rules.inventory_cap,
                audit_actor=interaction.user.id,
            )
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        embed = EmbedFactory.success(
            title="Item granted",
            description=(
                f"Gave <@{user.id}> **{escape_md(definition.name)}** (`#{created.id}`, "
                f"{definition.rarity})."
            ),
            context="Admin",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @rpg_grant.autocomplete("item")
    async def rpg_grant_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        runtime = self.bot.rpg
        if runtime is None:
            return []
        needle = current.strip().casefold()
        choices = []
        for definition in runtime.content.items.values():
            if needle and needle not in definition.name.casefold() and needle not in definition.id:
                continue
            label = f"{definition.name} ({definition.rarity}, lvl {definition.req_level})"
            choices.append(app_commands.Choice(name=label[:100], value=definition.id))
            if len(choices) >= 25:
                break
        return choices

    @rpg.command(name="level", description="Set a member's level (clears their XP).")
    @app_commands.describe(user="Whose level", level="The new level")
    async def rpg_level(
        self, interaction: discord.Interaction, user: discord.Member, level: int
    ) -> None:
        if await self._rpg_admin_guard(interaction, user):
            return
        runtime = self.bot.rpg
        assert runtime is not None
        try:
            character = await runtime.store.admin_set_level(
                interaction.user.id, user.id, level, runtime.content.levels.cap
            )
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        embed = EmbedFactory.success(
            title="Level set",
            description=f"<@{user.id}> is now level **{character.level}**.",
            context="Admin",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @rpg.command(name="rank", description="Set a member's guild rank.")
    @app_commands.describe(user="Whose rank", rank="The new rank")
    async def rpg_rank(
        self, interaction: discord.Interaction, user: discord.Member, rank: str
    ) -> None:
        if await self._rpg_admin_guard(interaction, user):
            return
        runtime = self.bot.rpg
        assert runtime is not None
        known = {r.id: r.id for r in runtime.content.ranks}
        chosen = known.get(rank.strip().upper())
        if chosen is None:
            await self._send_domain_error(
                interaction,
                YokaiError("That isn't a guild rank.", f"Ranks: {', '.join(known)}."),
            )
            return
        try:
            character = await runtime.store.admin_set_rank(interaction.user.id, user.id, chosen)
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        embed = EmbedFactory.success(
            title="Rank set",
            description=f"<@{user.id}> is now rank **{escape_md(character.rank)}**.",
            context="Admin",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @rpg_rank.autocomplete("rank")
    async def rpg_rank_autocomplete(
        self, interaction: discord.Interaction, current: str
    ) -> list[app_commands.Choice[str]]:
        runtime = self.bot.rpg
        if runtime is None:
            return []
        needle = current.strip().casefold()
        return [
            app_commands.Choice(name=r.id, value=r.id)
            for r in runtime.content.ranks
            if not needle or needle in r.id.casefold()
        ]

    @rpg.command(name="reset", description="Delete a member's adventurer and gear (not Renown).")
    @app_commands.describe(user="Whose adventurer", confirm="Must be true to go ahead")
    async def rpg_reset(
        self, interaction: discord.Interaction, user: discord.Member, confirm: bool
    ) -> None:
        if await self._rpg_admin_guard(interaction, user):
            return
        runtime = self.bot.rpg
        assert runtime is not None
        if not confirm:
            await self._send_domain_error(
                interaction,
                YokaiError("Nothing was deleted.", "Set confirm to true to reset the adventurer."),
            )
            return
        if user.id in runtime.active_users:
            await self._send_domain_error(
                interaction,
                YokaiError("They are in a fight right now.", "Try again after it ends."),
            )
            return
        existed = await runtime.store.admin_reset_character(interaction.user.id, user.id)
        build = EmbedFactory.success if existed else EmbedFactory.info
        embed = build(
            title="Adventurer reset" if existed else "Nothing to reset",
            description=(
                f"<@{user.id}>'s adventurer and gear were deleted. Their Renown is untouched."
                if existed
                else f"<@{user.id}> has no adventurer."
            ),
            context="Admin",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    def _rpg_status(self) -> tuple[str, bool]:
        """(diagnostics text, healthy) for the Guild RPG: content loaded and thread permissions."""
        runtime = self.bot.rpg
        if runtime is None:
            return "Disabled", True
        content = runtime.content
        server = self.bot.get_guild(self.bot.config.guild_id)
        perms = server.me.guild_permissions if server is not None and server.me else None
        if perms is None:
            threads, ok = "unknown", True
        elif perms.create_public_threads and perms.send_messages_in_threads:
            threads, ok = "ok", True
        else:
            threads, ok = "MISSING (fights need thread permissions)", False
        text = (
            f"{len(content.missions)} missions · {len(content.enemies)} enemies · "
            f"{len(runtime.active_users)} fighting · threads: {threads}"
        )
        return text, ok

    @admin.command(name="diag", description="Run comprehensive system diagnostics.")
    async def diag(self, interaction: discord.Interaction) -> None:
        """Diagnostic overview and yt-dlp updater."""
        avatar_url = self._avatar_url()

        if await self._deny_unless_admin(interaction, "Ask the bot owner if you need diagnostics."):
            return

        await interaction.response.defer(ephemeral=True)

        # 1. Inspect DB size
        db_file = Path(self.bot.config.db_path)
        if db_file.exists():
            size_bytes = db_file.stat().st_size
            if size_bytes < 1024 * 1024:
                db_size_str = f"{size_bytes / 1024:.1f} KB"
            else:
                db_size_str = f"{size_bytes / (1024 * 1024):.2f} MB"
        else:
            db_size_str = "File not created"

        # 2. Inspect Spotify orchestrator status
        sp_status = self.bot.spotify.get_provider_status()
        sp_official_str = (
            f"Configured: {sp_status['official_configured']} · "
            f"Circuit: {'Open' if not sp_status['official_circuit_available'] else 'Closed'} · "
            f"Failures: {sp_status['official_failures']}"
        )
        sp_scraper_str = (
            f"Enabled: {sp_status['scraper_enabled']} · "
            f"Circuit: {'Open' if not sp_status['scraper_circuit_available'] else 'Closed'} · "
            f"Failures: {sp_status['scraper_failures']}"
        )

        # 3. Inspect YouTube resolver health
        yt_health_str = (
            f"Degraded ({self.bot.resolver.consecutive_failures} failures)"
            if getattr(self.bot.resolver, "is_degraded", False)
            else "Healthy"
        )

        # 4. Inspect DAVE status
        dave_str = (
            f"Enabled (davey {self.bot.health.davey_version})"
            if self.bot.health.davey_available
            else "Disabled (davey missing)"
        )

        # 5. Inspect yt-dlp age
        ytdlp_age_str = (
            f"{self.bot.health.ytdlp_version} ({self.bot.health.ytdlp_age_days}d old)"
            if self.bot.health.ytdlp_age_days is not None
            else (self.bot.health.ytdlp_version or "Unknown")
        )
        if self.bot.health.ytdlp_outdated:
            ytdlp_age_str += " (outdated)"

        rpg_text, rpg_ok = self._rpg_status()

        diagnostics = {
            "Python / discord.py": f"{platform.python_version()} / {discord.__version__}",
            "DAVE E2EE": dave_str,
            "FFmpeg Path": self.bot.health.ffmpeg_path or "Missing",
            "Deno Path": self.bot.health.deno_path or "Missing",
            "yt-dlp Version": ytdlp_age_str,
            "YouTube Health": yt_health_str,
            "Spotify Official": sp_official_str,
            "Spotify Scraper": sp_scraper_str,
            "ytmusicapi": getattr(ytmusicapi, "__version__", "Unknown"),
            "Database Size": db_size_str,
            "Guild RPG": rpg_text,
        }

        # Gather recent errors
        recent_errors: list[str] = list(self.bot.error_ring_buffer)
        for err in getattr(self.bot.resolver, "error_ring_buffer", []):
            if err not in recent_errors:
                recent_errors.append(err)

        needs_attention = (
            getattr(self.bot.resolver, "is_degraded", False) is True
            or self.bot.health.ytdlp_outdated is True
            or self.bot.health.davey_available is False
            or not rpg_ok
        )
        embed = EmbedFactory.diag(
            diagnostics=diagnostics,
            recent_errors=recent_errors,
            bot_avatar_url=avatar_url,
            status="warn" if needs_attention else "ok",
        )

        view = DiagView(admin_id=interaction.user.id, bot_avatar_url=avatar_url)
        msg = await send(interaction, embed=embed, view=view, ephemeral=True)
        if isinstance(msg, discord.Message):
            view.message = msg

    @admin.command(
        name="set_multiplayer", description="Make this channel the table channel for a game."
    )
    @app_commands.describe(game="Which game this channel hosts")
    @app_commands.choices(game=GAME_CHOICES)
    async def set_multiplayer(
        self, interaction: discord.Interaction, game: app_commands.Choice[str]
    ) -> None:
        """Dedicate the current channel to a multiplayer game and post its Play embed."""
        if await self._deny_unless_admin(interaction, "Ask the bot owner to set up a table."):
            return
        channel = interaction.channel
        if not isinstance(channel, discord.TextChannel):
            await self._send_domain_error(
                interaction, InvalidGameChoiceError("Run this in a normal text channel.")
            )
            return
        await interaction.response.defer(ephemeral=True)
        tables = self.bot.get_cog("Tables")
        try:
            await tables.set_channel(channel, game.value)  # type: ignore[union-attr]
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        embed = EmbedFactory.success(
            title="Table channel set",
            description=(
                f"This channel now hosts **{game.name}** tables. "
                "Members open one with the Play button."
            ),
            context="Admin",
            bot_avatar_url=self._avatar_url(),
        )
        await send(interaction, embed=embed, ephemeral=True)

    @admin.command(
        name="unset_multiplayer", description="Stop using this channel as a table channel."
    )
    async def unset_multiplayer(self, interaction: discord.Interaction) -> None:
        if await self._deny_unless_admin(interaction, "Ask the bot owner to change tables."):
            return
        await interaction.response.defer(ephemeral=True)
        tables = self.bot.get_cog("Tables")
        try:
            removed = await tables.unset_channel(interaction.channel_id)  # type: ignore[union-attr]
        except YokaiError as err:
            await self._send_domain_error(interaction, err)
            return
        if removed:
            embed = EmbedFactory.success(
                title="Table channel removed",
                description="This channel is no longer a table channel.",
                context="Admin",
                bot_avatar_url=self._avatar_url(),
            )
        else:
            embed = EmbedFactory.info(
                title="Nothing to remove",
                description="This is not a table channel.",
                context="Admin",
                bot_avatar_url=self._avatar_url(),
            )
        await send(interaction, embed=embed, ephemeral=True)


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(AdminCog(bot))
