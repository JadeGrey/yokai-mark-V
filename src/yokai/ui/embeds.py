"""Universal embed system for Yokai with strict clamping and semantic builders."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

import discord

from yokai.theme import (
    COLOR_BRAND,
    COLOR_ERROR,
    COLOR_IDLE,
    COLOR_SUCCESS,
    COLOR_WARNING,
    Emoji,
    quip_bank,
)

logger = logging.getLogger(__name__)

# Discord Embed Limits
MAX_TITLE_LEN = 256
MAX_DESC_LEN = 4096
MAX_FIELD_NAME_LEN = 256
MAX_FIELD_VAL_LEN = 1024
MAX_FOOTER_LEN = 2048
MAX_FIELDS_COUNT = 25
MAX_TOTAL_LEN = 6000


def clamp(text: Optional[str], max_len: int) -> str:
    """Clamp string to max_len, appending an ellipsis if truncated."""
    if not text:
        return ""
    text = text.strip()
    if len(text) <= max_len:
        return text
    if max_len <= 1:
        return text[:max_len]
    return text[: max_len - 1].rstrip() + "…"


class EmbedFactory:
    """Factory creating uniform Discord embeds with enforced limits."""

    @staticmethod
    def _create_base(
        title: str,
        description: str,
        color: int,
        context: str = "Recon",
        bot_avatar_url: Optional[str] = None,
        quip: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
    ) -> discord.Embed:
        clamped_title = clamp(title, MAX_TITLE_LEN)
        footer_text = clamp(f"Yokai · {context}", MAX_FOOTER_LEN)

        desc = description
        if quip:
            desc = f"*{clamp(quip, 100)}*\n\n{desc}" if desc else f"*{clamp(quip, 100)}*"

        # Reserve budget for title, footer, and safety margin
        desc_budget = min(MAX_DESC_LEN, MAX_TOTAL_LEN - len(clamped_title) - len(footer_text) - 10)
        clamped_desc = clamp(desc, max(desc_budget, 10))

        embed = discord.Embed(
            title=clamped_title,
            description=clamped_desc,
            color=color,
        )

        embed.set_footer(
            text=footer_text,
            icon_url=bot_avatar_url,
        )

        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)

        return embed

    @classmethod
    def info(
        cls,
        title: str,
        description: str,
        context: str = "Info",
        quip: Optional[str] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        return cls._create_base(
            title=title,
            description=description,
            color=COLOR_BRAND,
            context=context,
            bot_avatar_url=bot_avatar_url,
            quip=quip,
        )

    @classmethod
    def success(
        cls,
        title: str,
        description: str,
        context: str = "Success",
        quip: Optional[str] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        return cls._create_base(
            title=f"{Emoji.CHECK} {title}",
            description=description,
            color=COLOR_SUCCESS,
            context=context,
            bot_avatar_url=bot_avatar_url,
            quip=quip,
        )

    @classmethod
    def warning(
        cls,
        title: str,
        description: str,
        context: str = "Warning",
        quip: Optional[str] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        return cls._create_base(
            title=f"{Emoji.WARN} {title}",
            description=description,
            color=COLOR_WARNING,
            context=context,
            bot_avatar_url=bot_avatar_url,
            quip=quip,
        )

    @classmethod
    def error(
        cls,
        message: str,
        user_hint: Optional[str] = None,
        context: str = "System",
        include_quip: bool = True,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        quip = quip_bank.get("error_lead") if include_quip else None
        desc_parts = [message]
        if user_hint:
            desc_parts.append(f"\n💡 **What to try:** {user_hint}")

        return cls._create_base(
            title=f"{Emoji.CROSS} Error",
            description="\n".join(desc_parts),
            color=COLOR_ERROR,
            context=context,
            bot_avatar_url=bot_avatar_url,
            quip=quip,
        )

    @classmethod
    def now_playing(
        cls,
        title: str,
        artist: Optional[str],
        duration_s: int,
        position_s: int,
        requester_mention: str,
        thumbnail_url: Optional[str] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        artist_str = f"by **{clamp(artist, 100)}**\n" if artist else ""
        progress_bar = _render_progress_bar(position_s, duration_s)
        time_str = f"`{_format_duration(position_s)} / {_format_duration(duration_s)}`"

        desc = f"{artist_str}\n{progress_bar} {time_str}\n\nRequested by {requester_mention}"

        return cls._create_base(
            title=f"{Emoji.PLAY} Now Playing: {clamp(title, 180)}",
            description=desc,
            color=COLOR_BRAND,
            context="Playback",
            bot_avatar_url=bot_avatar_url,
            thumbnail_url=thumbnail_url,
        )

    @classmethod
    def queued(
        cls,
        title: str,
        position: int,
        artist: Optional[str] = None,
        duration_s: Optional[int] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        quip = quip_bank.get("queued")
        details = []
        if artist:
            details.append(f"Artist: **{clamp(artist, 80)}**")
        if duration_s:
            details.append(f"Duration: `{_format_duration(duration_s)}`")
        details.append(f"Position in queue: **#{position}**")

        return cls._create_base(
            title=f"{Emoji.QUEUE} Queued: {clamp(title, 200)}",
            description="\n".join(details),
            color=COLOR_BRAND,
            context="Queue",
            bot_avatar_url=bot_avatar_url,
            quip=quip,
        )

    @classmethod
    def queue_page(
        cls,
        tracks: Sequence[tuple[int, str, Optional[str], Optional[int]]],
        page: int,
        total_pages: int,
        total_tracks: int,
        loop_mode: str = "off",
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        quip = quip_bank.get("empty_queue") if total_tracks == 0 else None
        embed = cls._create_base(
            title=f"{Emoji.QUEUE} Upcoming Queue ({total_tracks} tracks)",
            description="" if tracks else "The queue is empty.",
            color=COLOR_BRAND,
            context=f"Page {page}/{max(total_pages, 1)} · Loop: {loop_mode}",
            bot_avatar_url=bot_avatar_url,
            quip=quip,
        )

        for pos, title, artist, dur in tracks[:MAX_FIELDS_COUNT]:
            dur_str = f" `{_format_duration(dur)}`" if dur else ""
            artist_str = f" — {artist}" if artist else ""
            val = f"{clamp(title, 80)}{clamp(artist_str, 60)}{dur_str}"
            embed.add_field(
                name=f"#{pos}",
                value=clamp(val, MAX_FIELD_VAL_LEN),
                inline=False,
            )

        return embed

    @classmethod
    def recommendations(
        cls,
        tracks: Sequence[tuple[str, str, int]],  # (title, artist, duration_s)
        seed_title: str,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        quip = quip_bank.get("recs_intro")
        lines = []
        for i, (title, artist, dur) in enumerate(tracks, start=1):
            dur_str = f" `{_format_duration(dur)}`" if dur else ""
            lines.append(f"`{i}.` **{clamp(title, 60)}** — {clamp(artist, 40)}{dur_str}")

        return cls._create_base(
            title=f"{Emoji.RADIO} Radio Recommendations",
            description="\n".join(lines),
            color=COLOR_BRAND,
            context=f"Seed: {clamp(seed_title, 40)}",
            bot_avatar_url=bot_avatar_url,
            quip=quip,
        )

    @classmethod
    def ping(
        cls,
        gateway_ms: Optional[float],
        rest_ms: Optional[float],
        db_ms: Optional[float],
        voice_ms: Optional[float] = None,
        resolver_ms: Optional[float] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        latencies = [
            val for val in (gateway_ms, rest_ms, db_ms, voice_ms, resolver_ms) if val is not None
        ]
        worst = max(latencies) if latencies else 0.0

        if worst < 150.0:
            color = COLOR_SUCCESS
            status_text = "Fast. As expected."
        elif worst < 400.0:
            color = COLOR_WARNING
            status_text = "Acceptable signal latency."
        else:
            color = COLOR_ERROR
            status_text = "High latency detected."

        def _fmt(val: Optional[float]) -> str:
            return f"`{val:.1f} ms`" if val is not None else "*N/A*"

        embed = cls._create_base(
            title=f"{Emoji.PING} System Latencies",
            description=f"**Status:** {status_text}",
            color=color,
            context="Diagnostics",
            bot_avatar_url=bot_avatar_url,
        )

        embed.add_field(name="Gateway (WS)", value=_fmt(gateway_ms), inline=True)
        embed.add_field(name="REST API", value=_fmt(rest_ms), inline=True)
        embed.add_field(name="Database", value=_fmt(db_ms), inline=True)
        embed.add_field(name="Voice", value=_fmt(voice_ms), inline=True)
        embed.add_field(name="Resolver", value=_fmt(resolver_ms), inline=True)

        return embed

    @classmethod
    def usage(
        cls,
        top_users: Sequence[tuple[int, int, int]],  # (user_id, command_count, track_count)
        top_commands: Sequence[tuple[str, int]],  # (cmd_name, count)
        period: str = "all",
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        embed = cls._create_base(
            title=f"{Emoji.DRONE} Yokai Usage Statistics",
            description=f"Activity period: **{period}**",
            color=COLOR_BRAND,
            context="Analytics",
            bot_avatar_url=bot_avatar_url,
        )

        user_lines = []
        for i, (uid, cmd_count, track_count) in enumerate(top_users, start=1):
            user_lines.append(f"`#{i}` <@{uid}>: **{cmd_count}** cmds · **{track_count}** tracks")
        embed.add_field(
            name="Top Operators",
            value="\n".join(user_lines) if user_lines else "No user records found.",
            inline=False,
        )

        cmd_lines = []
        for cmd, count in top_commands:
            cmd_lines.append(f"`/{cmd}`: **{count}** runs")
        embed.add_field(
            name="Top Commands",
            value="\n".join(cmd_lines) if cmd_lines else "No commands recorded.",
            inline=False,
        )

        return embed

    @classmethod
    def about(
        cls,
        version: str,
        uptime_str: str,
        host_os: str,
        stack_info: Mapping[str, str],
        owner_id: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        desc = (
            "*Recon drone with opinions about your music taste. "
            "I find it, I play it, I quietly judge it.*\n\n"
            f"**Uptime:** {uptime_str}\n"
            f"**Host OS:** {host_os}\n"
            f"**Maintained by:** <@{owner_id}>"
        )

        embed = cls._create_base(
            title=f"{Emoji.DRONE} About Yokai v{version}",
            description=desc,
            color=COLOR_BRAND,
            context="About",
            bot_avatar_url=bot_avatar_url,
        )

        for component, ver in stack_info.items():
            embed.add_field(name=clamp(component, 50), value=f"`{clamp(ver, 50)}`", inline=True)

        embed.add_field(
            name="Notice",
            value=(
                "*Unaffiliated fan project inspired by Echo's Yokai drone "
                "from Rainbow Six Siege. © Ubisoft.*"
            ),
            inline=False,
        )

        return embed

    @classmethod
    def diag(
        cls,
        diagnostics: Mapping[str, Any],
        recent_errors: Sequence[str],
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        embed = cls._create_base(
            title=f"{Emoji.WARN} Diagnostic Telemetry",
            description="Owner-only operational diagnostic overview.",
            color=COLOR_IDLE,
            context="Diagnostic",
            bot_avatar_url=bot_avatar_url,
        )

        for key, val in diagnostics.items():
            embed.add_field(name=clamp(key, 50), value=clamp(str(val), 200), inline=True)

        if recent_errors:
            err_summary = "\n".join(f"• {clamp(e, 100)}" for e in recent_errors[-5:])
            embed.add_field(name="Recent Exceptions", value=clamp(err_summary, 1000), inline=False)

        return embed


def _render_progress_bar(current: int, total: int, length: int = 14) -> str:
    if total <= 0:
        return "━" * length
    progress = min(max(current / total, 0.0), 1.0)
    filled = int(progress * length)
    unfilled = length - filled
    return "━" * filled + "🔘" + "─" * max(unfilled - 1, 0)


def _format_duration(seconds: Optional[int]) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


async def send(
    interaction: discord.Interaction,
    embed: discord.Embed,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = False,
) -> Optional[discord.Message]:
    """Uniform interaction responder handling lifecycle, ephemeral state, and views."""
    allowed_mentions = discord.AllowedMentions.none()

    try:
        if interaction.response.is_done():
            return await interaction.followup.send(
                embed=embed,
                view=view or discord.utils.MISSING,
                ephemeral=ephemeral,
                allowed_mentions=allowed_mentions,
            )
        else:
            await interaction.response.send_message(
                embed=embed,
                view=view or discord.utils.MISSING,
                ephemeral=ephemeral,
                allowed_mentions=allowed_mentions,
            )
            return await interaction.original_response()
    except Exception as exc:
        logger.error("Failed to send interaction response: %s", exc)
        return None
