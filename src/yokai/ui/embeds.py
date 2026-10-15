"""Universal embed system for Yokai with strict clamping and semantic builders."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

import discord

from yokai.textutil import escape_md
from yokai.theme import (
    COLOR_BRAND,
    COLOR_ERROR,
    COLOR_IDLE,
    COLOR_SUCCESS,
    COLOR_WARNING,
    PROGRESS_EMPTY,
    PROGRESS_FILLED,
    PROGRESS_HEAD,
    PROGRESS_LENGTH,
    SEP,
    Emoji,
    quip_bank,
    source_label,
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

FieldSpec = tuple[str, str, bool]  # (name, value, inline)

ZERO_WIDTH = "​"
FIELDS_BUDGET = 3000  # characters reserved for fields so the description always has room
LIST_BUDGET = 3000  # characters of track lines in a list embed


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


def format_runtime(seconds: Optional[int]) -> str:
    """Compact total time: 45s, 7m 20s, 1h 12m."""
    if seconds is None or seconds < 0:
        return "unknown"
    if seconds < 60:
        return f"{seconds}s"
    minutes, secs = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes:02d}m"
    return f"{minutes}m {secs:02d}s"


def _link(text: str, url: Optional[str]) -> str:
    """Bold, escaped text; a markdown link when a URL is known."""
    safe = escape_md(text)
    return f"**[{safe}]({url})**" if url else f"**{safe}**"


def _grid(fields: Sequence[FieldSpec]) -> list[FieldSpec]:
    """Pad every run of inline fields to a multiple of three so Discord's grid stays aligned."""
    out: list[FieldSpec] = []
    run = 0

    def flush() -> None:
        nonlocal run
        if run % 3:
            out.extend([(ZERO_WIDTH, ZERO_WIDTH, True)] * (3 - run % 3))
        run = 0

    for name, value, inline in fields:
        if inline:
            run += 1
        else:
            flush()
        out.append((name, value, inline))
    flush()
    return out


def _list_line(pos: int, title: str, artist: Optional[str], duration_s: Optional[int]) -> str:
    """One compact numbered track line: ` 1. **Title** — Artist · `3:41``."""
    artist_part = f" — {escape_md(clamp(artist, 40))}" if artist else ""
    dur_part = f"{SEP}`{_format_duration(duration_s)}`" if duration_s else ""
    return f"`{pos:>2}.` **{escape_md(clamp(title, 60))}**{artist_part}{dur_part}"


def _signed(amount: int) -> str:
    """Signed whole number with thousands separators: +490, -10, 0."""
    if amount > 0:
        return f"+{amount:,}"
    return f"{amount:,}" if amount < 0 else "0"


def _result_style(stake: int, payout: int) -> tuple[int, str]:
    """Accent colour and quip key for a settled game, from stake versus total payout."""
    if payout > stake:
        return COLOR_SUCCESS, "game_win"
    if payout == stake:
        return COLOR_BRAND, "game_push"
    return COLOR_WARNING, "game_loss"


_BLACKJACK_VERDICTS = {
    "blackjack": "Blackjack!",
    "win": "You win",
    "push": "Push",
    "lose": "Dealer wins",
    "bust": "Bust",
}


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
        url: Optional[str] = None,
        fields: Sequence[FieldSpec] = (),
        footer_quip: Optional[str] = None,
    ) -> discord.Embed:
        clamped_title = clamp(title, MAX_TITLE_LEN)
        footer_text = f"Yokai{SEP}{context}"
        if footer_quip:
            footer_text += f"{SEP}{clamp(footer_quip, 80)}"
        footer_text = clamp(footer_text, MAX_FOOTER_LEN)

        field_items: list[FieldSpec] = []
        fields_len = 0
        for name, value, inline in fields[:MAX_FIELDS_COUNT]:
            f_name = clamp(name, MAX_FIELD_NAME_LEN) or ZERO_WIDTH
            f_value = clamp(value, MAX_FIELD_VAL_LEN) or ZERO_WIDTH
            if fields_len + len(f_name) + len(f_value) > FIELDS_BUDGET:
                break
            field_items.append((f_name, f_value, inline))
            fields_len += len(f_name) + len(f_value)

        desc = description
        if quip:
            desc = f"*{clamp(quip, 100)}*\n\n{desc}" if desc else f"*{clamp(quip, 100)}*"

        # Reserve budget for title, footer, fields and a safety margin
        desc_budget = min(
            MAX_DESC_LEN, MAX_TOTAL_LEN - len(clamped_title) - len(footer_text) - fields_len - 10
        )
        clamped_desc = clamp(desc, max(desc_budget, 10))

        embed = discord.Embed(
            title=clamped_title,
            description=clamped_desc,
            color=color,
            url=url or None,
        )
        embed.set_footer(text=footer_text, icon_url=bot_avatar_url)
        if thumbnail_url:
            embed.set_thumbnail(url=thumbnail_url)
        for f_name, f_value, inline in field_items:
            embed.add_field(name=f_name, value=f_value, inline=inline)
        return embed

    @classmethod
    def info(
        cls,
        title: str,
        description: str,
        context: str = "Info",
        quip: Optional[str] = None,
        bot_avatar_url: Optional[str] = None,
        fields: Sequence[FieldSpec] = (),
    ) -> discord.Embed:
        return cls._create_base(
            title=title,
            description=description,
            color=COLOR_BRAND,
            context=context,
            bot_avatar_url=bot_avatar_url,
            quip=quip,
            fields=fields,
        )

    @classmethod
    def success(
        cls,
        title: str,
        description: str,
        context: str = "Success",
        quip: Optional[str] = None,
        bot_avatar_url: Optional[str] = None,
        fields: Sequence[FieldSpec] = (),
    ) -> discord.Embed:
        return cls._create_base(
            title=f"{Emoji.CHECK} {title}",
            description=description,
            color=COLOR_SUCCESS,
            context=context,
            bot_avatar_url=bot_avatar_url,
            quip=quip,
            fields=fields,
        )

    @classmethod
    def warning(
        cls,
        title: str,
        description: str,
        context: str = "Warning",
        quip: Optional[str] = None,
        bot_avatar_url: Optional[str] = None,
        fields: Sequence[FieldSpec] = (),
    ) -> discord.Embed:
        return cls._create_base(
            title=f"{Emoji.WARN} {title}",
            description=description,
            color=COLOR_WARNING,
            context=context,
            bot_avatar_url=bot_avatar_url,
            quip=quip,
            fields=fields,
        )

    @classmethod
    def error(
        cls,
        message: str,
        user_hint: Optional[str] = None,
        context: str = "System",
        include_quip: bool = True,
        bot_avatar_url: Optional[str] = None,
        title: str = "That didn't work",
    ) -> discord.Embed:
        """Fixed shape: specific title, plain explanation, a 'Try' field, quip in the footer."""
        fields: list[FieldSpec] = [("Try", user_hint, False)] if user_hint else []
        return cls._create_base(
            title=f"{Emoji.CROSS} {title}",
            description=message,
            color=COLOR_ERROR,
            context=context,
            bot_avatar_url=bot_avatar_url,
            fields=fields,
            footer_quip=quip_bank.get("error_lead") if include_quip else None,
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
        url: Optional[str] = None,
        source: Optional[str] = None,
    ) -> discord.Embed:
        lines: list[str] = []
        if artist:
            lines.append(f"**{escape_md(clamp(artist, 100))}**")
        bar = _render_progress_bar(position_s, duration_s)
        elapsed = _format_duration(position_s)
        total = _format_duration(duration_s)
        lines.append(f"{bar}  `{elapsed} / {total}`")

        fields: list[FieldSpec] = [
            ("Duration", f"`{_format_duration(duration_s)}`", True),
            ("Requested by", requester_mention, True),
        ]
        if source:
            fields.append(("Source", source_label(source), True))

        return cls._create_base(
            title=f"{Emoji.PLAY} {clamp(title, 200)}",
            description="\n".join(lines),
            color=COLOR_BRAND,
            context="Now playing",
            bot_avatar_url=bot_avatar_url,
            thumbnail_url=thumbnail_url,
            url=url,
            fields=_grid(fields),
        )

    @classmethod
    def queued(
        cls,
        title: str,
        position: int,
        artist: Optional[str] = None,
        duration_s: Optional[int] = None,
        bot_avatar_url: Optional[str] = None,
        url: Optional[str] = None,
        eta_s: Optional[int] = None,
        requester_mention: Optional[str] = None,
        source: Optional[str] = None,
        thumbnail_url: Optional[str] = None,
    ) -> discord.Embed:
        lines = [_link(clamp(title, 200), url)]
        if artist:
            lines.append(f"by {escape_md(clamp(artist, 100))}")

        fields: list[FieldSpec] = [("Position", f"`#{position}`", True)]
        if eta_s is not None:
            fields.append(("Plays in", f"`~{format_runtime(eta_s)}`", True))
        if duration_s:
            fields.append(("Duration", f"`{_format_duration(duration_s)}`", True))
        if requester_mention:
            fields.append(("Requested by", requester_mention, True))
        if source:
            fields.append(("Source", source_label(source), True))

        return cls._create_base(
            title=f"{Emoji.QUEUE} Queued",
            description="\n".join(lines),
            color=COLOR_BRAND,
            context="Queue",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("queued"),
            thumbnail_url=thumbnail_url,
            fields=_grid(fields),
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
        now_playing: Optional[tuple[str, Optional[str], Optional[int]]] = None,
        total_runtime_s: Optional[int] = None,
    ) -> discord.Embed:
        header: list[str] = []
        if now_playing is not None:
            np_title, np_url, np_duration = now_playing
            np_time = f"{SEP}`{_format_duration(np_duration)}`" if np_duration else ""
            header.append(f"**Now playing** {_link(clamp(np_title, 80), np_url)}{np_time}")
        summary = f"**{total_tracks}** up next"
        if total_tracks and total_runtime_s:
            summary += f"{SEP}{format_runtime(total_runtime_s)} total"
        summary += f"{SEP}loop {loop_mode}"
        header.append(summary)

        lines: list[str] = []
        used = 0
        for index, (pos, track_title, artist, dur) in enumerate(tracks):
            line = _list_line(pos, track_title, artist, dur)
            if used + len(line) + 1 > LIST_BUDGET:
                lines.append(f"… and {len(tracks) - index} more on this page")
                break
            lines.append(line)
            used += len(line) + 1
        body = "\n".join(lines) if lines else "The queue is empty."

        return cls._create_base(
            title=f"{Emoji.QUEUE} Up next",
            description="\n".join(header) + "\n\n" + body,
            color=COLOR_BRAND,
            context=f"Page {page}/{max(total_pages, 1)}",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("empty_queue") if total_tracks == 0 else None,
        )

    @classmethod
    def recommendations(
        cls,
        tracks: Sequence[tuple[str, str, int]],  # (title, artist, duration_s)
        seed_title: str,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        lines: list[str] = []
        used = 0
        for i, (title, artist, dur) in enumerate(tracks, start=1):
            line = _list_line(i, title, artist, dur)
            if used + len(line) + 1 > LIST_BUDGET:
                lines.append(f"… and {len(tracks) - i + 1} more")
                break
            lines.append(line)
            used += len(line) + 1

        description = f"Seeded from **{escape_md(clamp(seed_title, 80))}**\n\n" + "\n".join(lines)
        return cls._create_base(
            title=f"{Emoji.RADIO} Radio recommendations",
            description=description,
            color=COLOR_BRAND,
            context="Recommendations",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("recs_intro"),
        )

    @classmethod
    def import_summary(
        cls,
        kind: str,
        name: Optional[str],
        loaded: int,
        total: Optional[int],
        queued: int,
        now_playing: Optional[str] = None,
        partial: bool = False,
        bot_avatar_url: Optional[str] = None,
        note: Optional[str] = None,
    ) -> discord.Embed:
        loaded_text = f"`{loaded} of {total}`" if partial and total else f"`{loaded}`"
        fields: list[FieldSpec] = [
            ("Loaded", loaded_text, True),
            ("Queued", f"`{queued}`", True),
        ]
        if now_playing:
            fields.append(("Now playing", escape_md(clamp(now_playing, 100)), True))
        description = (
            f"**{escape_md(clamp(name, 120))}**" if name else "Loaded from the playlist link."
        )
        if note:
            description += f"\n{note}"
        return cls._create_base(
            title=f"{Emoji.QUEUE} {clamp(kind, 60)} loaded",
            description=description,
            color=COLOR_BRAND,
            context="Import",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def ping(
        cls,
        gateway_ms: Optional[float],
        rest_ms: Optional[float],
        db_ms: Optional[float],
        voice_ms: Optional[float] = None,
        voice_avg_ms: Optional[float] = None,
        resolver_ms: Optional[float] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        # The resolver figure is shown but not counted: a first-time YouTube resolve takes about
        # 2 s by nature and would keep the status red forever.
        latencies = [val for val in (gateway_ms, rest_ms, db_ms, voice_ms) if val is not None]
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

        voice_val = "*Not connected*"
        if voice_ms is not None:
            voice_val = f"`{voice_ms:.1f} ms`"
            if voice_avg_ms is not None:
                voice_val = f"`{voice_ms:.1f} ms` (avg `{voice_avg_ms:.1f} ms`)"
        resolver_val = f"`{resolver_ms:.1f} ms`" if resolver_ms is not None else "*No data yet*"

        fields: list[FieldSpec] = [
            ("Gateway (WS)", _fmt(gateway_ms), True),
            ("REST API", _fmt(rest_ms), True),
            ("Database", _fmt(db_ms), True),
            ("Voice", voice_val, True),
            ("Resolver", resolver_val, True),
        ]
        return cls._create_base(
            title=f"{Emoji.PING} System Latencies",
            description=f"**Status:** {status_text}",
            color=color,
            context="Diagnostics",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

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

        fields: list[FieldSpec] = [
            (clamp(component, 50), f"`{clamp(ver, 50)}`", True)
            for component, ver in stack_info.items()
        ]
        fields.append(
            (
                "Notice",
                "*Unaffiliated fan project inspired by Echo's Yokai drone "
                "from Rainbow Six Siege. © Ubisoft.*",
                False,
            )
        )
        return cls._create_base(
            title=f"{Emoji.DRONE} About Yokai v{version}",
            description=desc,
            color=COLOR_BRAND,
            context="About",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def diag(
        cls,
        diagnostics: Mapping[str, Any],
        recent_errors: Sequence[str],
        bot_avatar_url: Optional[str] = None,
        status: str = "ok",
    ) -> discord.Embed:
        needs_attention = status == "warn"
        fields: list[FieldSpec] = [
            (clamp(key, 50), clamp(str(val), 200), True) for key, val in diagnostics.items()
        ]

        if recent_errors:
            lines: list[str] = []
            char_count = 0
            for err in recent_errors[-20:]:
                line = f"• {clamp(err, 120)}"
                if char_count + len(line) + 1 > 1000:
                    lines.append("• ... (truncated)")
                    break
                lines.append(line)
                char_count += len(line) + 1
            fields.append((f"Recent Exceptions ({len(recent_errors)})", "\n".join(lines), False))
        else:
            fields.append(("Recent Exceptions", "*No errors recorded.*", False))

        return cls._create_base(
            title=f"{Emoji.WARN if needs_attention else Emoji.DRONE} Diagnostic Telemetry",
            description="Admin-only operational diagnostic overview.",
            color=COLOR_WARNING if needs_attention else COLOR_SUCCESS,
            context="Diagnostic",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def challenge_board(
        cls,
        *,
        period: str,
        rows: Sequence[tuple[str, int, int, int, bool]],
        resets_ts: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A user's daily or weekly challenges: title, progress bar, count and reward."""
        lines = [f"Resets <t:{resets_ts}:R>", ""]
        if not rows:
            lines.append("There are no challenges right now.")
        for title, progress, target, reward, completed in rows:
            filled = 10 if completed else min(10, (progress * 10) // max(1, target))
            bar = PROGRESS_FILLED * filled + PROGRESS_EMPTY * (10 - filled)
            status = " · done" if completed else ""
            lines.append(f"**{escape_md(clamp(title, 120))}**")
            lines.append(f"`{bar}` {progress}/{target}{status} · +{reward:,} Renown")
        return cls._create_base(
            title=f"{Emoji.COIN} {period.capitalize()} challenges",
            description="\n".join(lines),
            color=COLOR_BRAND,
            context="Challenges",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def challenge_notice(
        cls,
        *,
        lines: Sequence[str],
        extra: int = 0,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A short, batched 'challenge complete' announcement."""
        body = list(lines)
        if extra > 0:
            body.append(f"...and {extra} more.")
        return cls._create_base(
            title="Challenge complete" if len(lines) + extra == 1 else "Challenges complete",
            description="\n".join(body),
            color=COLOR_SUCCESS,
            context="Challenges",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def balance(
        cls,
        user_mention: str,
        balance: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        return cls._create_base(
            title=f"{Emoji.COIN} Renown balance",
            description=f"{user_mention} has **{balance:,}** Renown.",
            color=COLOR_BRAND,
            context="Economy",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def daily_claim(
        cls,
        user_mention: str,
        payout: int,
        streak_day: int,
        new_balance: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        fields: list[FieldSpec] = [
            ("Streak", f"Day `{streak_day}`", True),
            ("New balance", f"**{new_balance:,}**", True),
        ]
        return cls._create_base(
            title=f"{Emoji.COIN} Daily claimed",
            description=f"{user_mention} claimed **{payout:,}** Renown.",
            color=COLOR_SUCCESS,
            context="Economy",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("daily_claim"),
            fields=_grid(fields),
        )

    @classmethod
    def pay_sent(
        cls,
        sender_mention: str,
        recipient_mention: str,
        amount: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        return cls._create_base(
            title=f"{Emoji.COIN} Renown sent",
            description=f"{sender_mention} sent **{amount:,}** Renown to {recipient_mention}.",
            color=COLOR_SUCCESS,
            context="Economy",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("pay_sent"),
        )

    @classmethod
    def leaderboard_page(
        cls,
        entries: Sequence[tuple[int, int, int]],  # (rank, user_id, balance)
        page: int,
        total_pages: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        lines = [f"`#{rank}` <@{uid}> — **{bal:,}** Renown" for rank, uid, bal in entries]
        body = "\n".join(lines) if lines else "No wallets yet."
        return cls._create_base(
            title=f"{Emoji.COIN} Renown leaderboard",
            description=body,
            color=COLOR_BRAND,
            context=f"Page {page}/{max(total_pages, 1)}",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def economy_admin(
        cls,
        target_mention: str,
        delta_applied: int,
        new_balance: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        verb = "Granted" if delta_applied >= 0 else "Deducted"
        fields: list[FieldSpec] = [("New balance", f"**{new_balance:,}**", True)]
        return cls._create_base(
            title=f"{Emoji.COIN} Balance adjusted",
            description=f"{verb} **{abs(delta_applied):,}** Renown for {target_mention}.",
            color=COLOR_SUCCESS,
            context="Economy admin",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def guild_joined(
        cls,
        user_mention: str,
        rank: str,
        level: int,
        stats: Sequence[tuple[str, str]],  # (label, display value)
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        fields: list[FieldSpec] = [(label, f"**{value}**", True) for label, value in stats]
        return cls._create_base(
            title=f"{Emoji.SWORDS} Welcome to the guild",
            description=(
                f"{user_mention} signed the roster as a **rank {escape_md(rank)}** adventurer "
                f"at level **{level}**. Use /card to see your adventurer card."
            ),
            color=COLOR_SUCCESS,
            context="Guild",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("guild_join"),
            fields=_grid(fields),
        )

    @classmethod
    def player_card(
        cls,
        user_mention: str,
        rank: str,
        level: int,
        level_cap: int,
        stats: Sequence[tuple[str, str]],  # (label, display value)
        wins: int,
        losses: int,
        joined_ts: int,
        next_rank: Optional[tuple[str, int]] = None,  # (rank id, level it opens at)
        top_rank: bool = False,
        thumbnail_url: Optional[str] = None,
        bot_avatar_url: Optional[str] = None,
        gear: Sequence[tuple[str, str]] = (),  # (slot label, "item name · 80%" or "empty")
        bonus_lines: Sequence[str] = (),
        promotion_note: Optional[str] = None,
        xp: Optional[tuple[int, int]] = None,  # (XP now, XP needed for the next level); None at cap
    ) -> discord.Embed:
        fields: list[FieldSpec] = [
            ("Rank", f"**{escape_md(rank)}**", True),
            ("Level", f"**{level}** / {level_cap}", True),
            ("Record", f"**{wins:,}**W{SEP}**{losses:,}**L", True),
        ]
        fields.extend((label, f"**{value}**", True) for label, value in stats)
        if next_rank is not None:
            next_id, next_level = next_rank
            promotion = f"Rank **{escape_md(next_id)}** at level **{next_level}**"
            if promotion_note:
                promotion += f"{SEP}{promotion_note}"
            fields.append(("Next promotion", promotion, False))
        elif top_rank:
            fields.append(("Next promotion", "Top rank reached.", False))
        if xp is not None:
            have, needed = xp
            percent = min(100, int(100 * have / needed)) if needed > 0 else 100
            fields.append(
                (
                    "Experience",
                    f"{_hp_bar(have, needed)} **{have:,}** / {needed:,} XP{SEP}{percent}% "
                    f"to level {level + 1}",
                    False,
                )
            )
        elif level >= level_cap:
            fields.append(("Experience", "Max level reached.", False))
        if gear:
            gear_text = "\n".join(f"**{label}**{SEP}{escape_md(value)}" for label, value in gear)
            fields.append(("Gear", clamp(gear_text, 1000), False))
        if bonus_lines:
            bonus_text = "\n".join(escape_md(line) for line in bonus_lines)
            fields.append(("Set bonuses", clamp(bonus_text, 1000), False))
        return cls._create_base(
            title=f"{Emoji.SWORDS} Adventurer card",
            description=f"{user_mention}{SEP}joined <t:{joined_ts}:D>",
            color=COLOR_BRAND,
            context="Guild",
            bot_avatar_url=bot_avatar_url,
            thumbnail_url=thumbnail_url,
            fields=_grid(fields),
        )

    @classmethod
    def guild_board(
        cls,
        groups: Sequence[tuple[str, bool, Sequence[tuple[str, int, int, int, int]]]],
        your_rank: str,
        attempts_text: str,
        resets_ts: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """Today's missions by rank. Each mission is (name, attempt cost, level, XP, Renown)."""
        fields: list[FieldSpec] = []
        for rank_id, is_open, missions in groups:
            lines: list[str] = []
            for name, cost, level, xp, renown in missions:
                if is_open:
                    lines.append(
                        f"**{escape_md(clamp(name, 40))}**{SEP}lvl {level}{SEP}cost {cost}"
                        f"{SEP}+{xp:,} XP{SEP}+{renown:,} Renown"
                    )
                else:
                    lines.append(f"{escape_md(clamp(name, 40))}{SEP}lvl {level}")
            heading = f"Rank {escape_md(rank_id)}" + ("" if is_open else " (locked)")
            fields.append((heading, "\n".join(lines) or "No missions today.", False))
        return cls._create_base(
            title=f"{Emoji.SWORDS} Guild board",
            description=(
                f"You are rank **{escape_md(your_rank)}**. {attempts_text}\n"
                f"The board refreshes <t:{resets_ts}:R>. Start one with /guild mission."
            ),
            color=COLOR_BRAND,
            context="Guild",
            bot_avatar_url=bot_avatar_url,
            fields=fields,
        )

    @classmethod
    def wiki_page(
        cls,
        title: str,
        body: str,
        page: int,
        total_pages: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """One page of the guild wiki."""
        return cls._create_base(
            title=f"{Emoji.SWORDS} {escape_md(clamp(title, 200))}",
            description=body,
            color=COLOR_BRAND,
            context=f"Guild wiki{SEP}page {page}/{max(total_pages, 1)}",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def trade_session(
        cls,
        sides: Sequence[tuple[str, str, Sequence[str], int, bool]],
        expires_ts: int,
        is_gift: bool,
        note: Optional[str] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """An open trade. Each side is (name, mention, item lines, Renown offered, confirmed)."""
        fields: list[FieldSpec] = []
        for name, mention, lines, renown, confirmed in sides:
            body = list(lines)
            if renown:
                body.append(f"**{renown:,}** Renown")
            listing = "\n".join(body) if body else "Nothing yet"
            value = f"{mention}\n{listing}"
            tag = " (confirmed)" if confirmed else ""
            fields.append((f"{escape_md(clamp(name, 30))}{tag}", clamp(value, 1000), True))
        if note:
            fields.append(("Heads up", clamp(note, 1000), False))
        kind = "a gift" if is_gift else "a trade"
        return cls._create_base(
            title=f"{Emoji.SWORDS} Trade",
            description=(
                f"This is {kind} in progress. Add items or Renown, then both press Confirm. "
                f"Changing an offer clears the confirmations. Closes <t:{expires_ts}:R>."
            ),
            color=COLOR_BRAND,
            context="Guild",
            bot_avatar_url=bot_avatar_url,
            fields=fields,
        )

    @classmethod
    def party_lobby(
        cls,
        mission_name: str,
        rank: str,
        level: int,
        cost: int,
        members: Sequence[tuple[str, str, int]],
        min_party: int,
        max_party: int,
        expires_ts: int,
        is_raid: bool,
        note: Optional[str] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The open lobby: the mission, who has joined (mention, rank, level) and when it closes."""
        seats = [
            f"{number}. {mention}{SEP}rank {escape_md(member_rank)}{SEP}level {member_level}"
            for number, (mention, member_rank, member_level) in enumerate(members, start=1)
        ]
        need = f"{min_party} to {max_party}" if min_party > 1 else f"up to {max_party}"
        fields: list[FieldSpec] = [(f"Party ({len(members)}/{max_party})", "\n".join(seats), False)]
        if note:
            fields.append(("Heads up", note, False))
        return cls._create_base(
            title=f"{Emoji.SWORDS} {'Raid' if is_raid else 'Party'} lobby",
            description=(
                f"**{escape_md(clamp(mission_name, 60))}**{SEP}rank {escape_md(rank)}"
                f"{SEP}level {level}{SEP}{cost} attempts each\n"
                f"Needs {need} members"
                + (" (3 or 4 recommended)" if is_raid else "")
                + f". Press Join to take a seat; the leader presses Start. "
                f"This lobby closes <t:{expires_ts}:R>."
            ),
            color=COLOR_BRAND,
            context="Guild",
            bot_avatar_url=bot_avatar_url,
            fields=fields,
        )

    @classmethod
    def rank_status(
        cls,
        rank: str,
        level: int,
        lines: Sequence[str],
        ready: bool,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The rank screen: where the member stands and what the next trial involves."""
        return cls._create_base(
            title=f"{Emoji.SWORDS} Guild rank",
            description=(
                f"Rank **{escape_md(rank)}**{SEP}level **{level}**\n\n" + "\n".join(lines)
            ),
            color=COLOR_SUCCESS if ready else COLOR_BRAND,
            context="Guild",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def guild_leaderboard(
        cls,
        rows: Sequence[tuple[int, int, str, int, int]],
        sort_label: str,
        page: int,
        total_pages: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A page of the guild standings. Rows are (position, user id, rank, level, battles won)."""
        lines = [
            f"`#{position}` <@{user_id}>{SEP}rank **{escape_md(rank)}**{SEP}lvl {level}"
            f"{SEP}{wins:,} win(s)"
            for position, user_id, rank, level, wins in rows
        ]
        return cls._create_base(
            title=f"{Emoji.SWORDS} Guild standings",
            description="\n".join(lines) if lines else "Nobody has joined the guild yet.",
            color=COLOR_BRAND,
            context=f"By {sort_label}{SEP}page {page}/{max(total_pages, 1)}",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def inventory_page(
        cls,
        rows: Sequence[tuple[int, str, str, str, int, str, bool, bool]],
        page: int,
        total_pages: int,
        total_items: int,
        cap: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """One page of a member's gear. Rows are (id, name, slot, rarity, level requirement,
        condition text, equipped, wearable at your level)."""
        lines = []
        for item_id, name, slot, rarity, req, condition, equipped, wearable in rows:
            tail = " (equipped)" if equipped else ("" if wearable else " (too high a level)")
            lines.append(
                f"`#{item_id}` **{escape_md(clamp(name, 40))}**{SEP}{slot}{SEP}{rarity}"
                f"{SEP}lvl {req}{SEP}{condition}{tail}"
            )
        body = "\n".join(lines) if lines else "You don't own any gear yet. Try /gear shop."
        return cls._create_base(
            title=f"{Emoji.SWORDS} Inventory",
            description=f"**{total_items}/{cap}** items.\n{body}",
            color=COLOR_BRAND,
            context=f"Page {page}/{max(total_pages, 1)}",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def shop_panel(
        cls,
        tiers: Sequence[tuple[int, Sequence[tuple[str, str, str, str, int, str]]]],
        balance: int,
        left: int,
        daily_cap: int,
        resets_ts: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """Today's guild shop. Each tier is (level requirement, items); an item is
        (name, tag, slot, rarity, price, stats text)."""
        fields: list[FieldSpec] = []
        for req, items in tiers:
            lines = [
                f"**{escape_md(clamp(name, 40))}**{SEP}{slot}{SEP}{rarity}{SEP}{price:,} Renown"
                f"\n{escape_md(stats)}"
                for name, _tag, slot, rarity, price, stats in items
            ]
            fields.append((f"Level {req}+ gear", "\n".join(lines), False))
        return cls._create_base(
            title=f"{Emoji.COIN} Guild shop",
            description=(
                f"You have **{balance:,}** Renown and **{left}/{daily_cap}** "
                "purchases left today.\n"
                f"Stock changes <t:{resets_ts}:R>. Buy with /gear buy."
            ),
            color=COLOR_BRAND,
            context="Guild",
            bot_avatar_url=bot_avatar_url,
            fields=fields,
        )

    @classmethod
    def gear_warning(
        cls, lines: Sequence[str], bot_avatar_url: Optional[str] = None
    ) -> discord.Embed:
        """Shown before a mission when equipped gear is low or broken."""
        body = "\n".join(f"{escape_md(line)}" for line in lines)
        return cls._create_base(
            title="Your gear is worn",
            description=(
                f"{clamp(body, 1500)}\n\nBroken gear gives you nothing. "
                "Repair it with /gear repair, or start anyway."
            ),
            color=COLOR_WARNING,
            context="Guild",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def fight_round(
        cls,
        mission_name: str,
        stage_text: str,
        round_no: int,
        players: Sequence[tuple[str, int, int, Sequence[str], str]],
        foes: Sequence[tuple[str, int, int, Sequence[str]]],
        log_lines: Sequence[str],
        deadline_ts: Optional[int] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The live fight board. Players are (name, hp, max hp, statuses, state)."""
        party_lines = []
        for name, hp, max_hp, statuses, state in players:
            note = f"{SEP}{', '.join(escape_md(s) for s in statuses)}" if statuses else ""
            tag = {"waiting": "choosing", "ready": "ready", "away": "away", "down": "down"}.get(
                state, state
            )
            party_lines.append(
                f"**{escape_md(clamp(name, 24))}** `{_hp_bar(hp, max_hp)}` {hp}/{max_hp}"
                f"{SEP}{tag}{note}"
            )
        foe_lines = []
        for name, hp, max_hp, statuses in foes:
            note = f"{SEP}{', '.join(escape_md(s) for s in statuses)}" if statuses else ""
            foe_lines.append(
                f"**{escape_md(clamp(name, 24))}** `{_hp_bar(hp, max_hp)}` {hp}/{max_hp}{note}"
            )
        prompt = f"Choose your action <t:{deadline_ts}:R>." if deadline_ts else "Round resolving."
        fields: list[FieldSpec] = [
            ("Party", "\n".join(party_lines) or "-", False),
            ("Foes", "\n".join(foe_lines) or "-", False),
        ]
        if log_lines:
            fields.append(("Last round", clamp("\n".join(log_lines), 900), False))
        return cls._create_base(
            title=f"{Emoji.SWORDS} {escape_md(clamp(mission_name, 60))}",
            description=f"{stage_text}\n{prompt}",
            color=COLOR_BRAND,
            context=f"Round {round_no}",
            bot_avatar_url=bot_avatar_url,
            fields=fields,
        )

    @classmethod
    def fight_result(
        cls,
        won: bool,
        mission_name: str,
        rounds: int,
        rewards: Sequence[tuple[str, int, int, int]],
        log_lines: Sequence[str],
        loot_lines: Sequence[str] = (),
        gear_lines: Sequence[str] = (),
        bot_avatar_url: Optional[str] = None,
        promotion_to: Optional[str] = None,
        cooldown_ts: Optional[int] = None,
    ) -> discord.Embed:
        """The end of a fight. Rewards are (mention, XP, Renown, levels gained).

        For a promotion trial pass `promotion_to` (the rank sought) and, after a loss, the time the
        cooldown ends.
        """
        lines = []
        for mention, xp, renown, levels in rewards:
            parts = [f"+{xp:,} XP", f"+{renown:,} Renown"]
            if levels:
                parts.append(f"level up x{levels}" if levels > 1 else "level up")
            lines.append(f"{mention}{SEP}{SEP.join(parts)}")
        fields: list[FieldSpec] = []
        if won and lines:
            heading = "Reward" if promotion_to else "Rewards"
            fields.append((heading, clamp("\n".join(lines), 1000), False))
        if loot_lines:
            fields.append(("Loot", clamp("\n".join(loot_lines), 1000), False))
        if gear_lines:
            fields.append(("Gear", clamp("\n".join(gear_lines), 1000), False))
        if log_lines:
            fields.append(("Final round", clamp("\n".join(log_lines), 900), False))
        name = escape_md(clamp(mission_name, 60))
        if promotion_to is not None:
            verdict = "Promotion passed" if won else "Promotion failed"
            if won:
                body = (
                    f"**{name}** cleared in {rounds} round(s). "
                    f"You are now rank **{escape_md(promotion_to)}**."
                )
            else:
                retry = f" You can try again <t:{cooldown_ts}:R>." if cooldown_ts else ""
                body = f"**{name}** was lost after {rounds} round(s).{retry}"
        else:
            verdict = "Mission complete" if won else "Mission failed"
            body = (
                f"**{name}** cleared in {rounds} round(s)."
                if won
                else f"**{name}** was lost after {rounds} round(s). No rewards this time."
            )
        return cls._create_base(
            title=f"{Emoji.SWORDS} {verdict}",
            description=body,
            color=COLOR_SUCCESS if won else COLOR_WARNING,
            context="Guild",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("fight_win" if won else "fight_loss"),
            fields=fields,
        )

    @classmethod
    def spinning(
        cls,
        title: str,
        description: str,
        icon: str,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        return cls._create_base(
            title=f"{icon} {title}",
            description=description,
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def slots_result(
        cls,
        user_mention: str,
        reels: Sequence[str],
        stake: int,
        payout: int,
        new_balance: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        color, quip_key = _result_style(stake, payout)
        reel_text = " ".join(f"[{name}]" for name in reels)
        fields: list[FieldSpec] = [
            ("Stake", f"**{stake:,}**", True),
            ("Net", f"**{_signed(payout - stake)}**", True),
            ("Balance", f"**{new_balance:,}**", True),
        ]
        return cls._create_base(
            title=f"{Emoji.SLOTS} Slots",
            description=f"{user_mention}\n`{reel_text}`",
            color=color,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get(quip_key),
            fields=_grid(fields),
        )

    @classmethod
    def roulette_result(
        cls,
        user_mention: str,
        bet_label: str,
        pocket: int,
        pocket_colour: str,
        stake: int,
        payout: int,
        new_balance: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        color, quip_key = _result_style(stake, payout)
        fields: list[FieldSpec] = [
            ("Stake", f"**{stake:,}**", True),
            ("Net", f"**{_signed(payout - stake)}**", True),
            ("Balance", f"**{new_balance:,}**", True),
        ]
        return cls._create_base(
            title=f"{Emoji.DICE} Roulette",
            description=(
                f"{user_mention}\nBet: **{escape_md(bet_label)}**\n"
                f"The ball landed on **{pocket}** ({pocket_colour})."
            ),
            color=color,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get(quip_key),
            fields=_grid(fields),
        )

    @classmethod
    def blackjack_hand(
        cls,
        user_mention: str,
        player_cards: Sequence[str],
        player_total: int,
        dealer_cards: Sequence[str],
        dealer_total: Optional[int],
        stake: int,
        verdict: Optional[str] = None,
        payout: Optional[int] = None,
        new_balance: Optional[int] = None,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        dealer_suffix = f" ({dealer_total})" if dealer_total is not None else ""
        lines = [
            user_mention,
            f"**Your hand** `{' '.join(player_cards)}` ({player_total})",
            f"**Dealer** `{' '.join(dealer_cards)}`{dealer_suffix}",
        ]
        fields: list[FieldSpec] = [("Stake", f"**{stake:,}**", True)]
        color = COLOR_BRAND
        quip: Optional[str] = None
        if verdict is not None:
            returned = payout if payout is not None else 0
            color, quip_key = _result_style(stake, returned)
            if verdict == "blackjack":
                quip_key = "game_blackjack"
            elif verdict == "bust":
                quip_key = "game_bust"
            quip = quip_bank.get(quip_key)
            lines.append(f"**{_BLACKJACK_VERDICTS[verdict]}**")
            fields.append(("Net", f"**{_signed(returned - stake)}**", True))
            if new_balance is not None:
                fields.append(("Balance", f"**{new_balance:,}**", True))
        return cls._create_base(
            title=f"{Emoji.DICE} Blackjack",
            description="\n".join(lines),
            color=color,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip,
            fields=_grid(fields),
        )

    @classmethod
    def gamble_stats(
        cls,
        user_mention: str,
        rows: Sequence[tuple[str, int, int, int]],  # (game, plays, wagered, returned)
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        lines = [
            f"`{game}` — **{plays:,}** plays · wagered **{wagered:,}** · "
            f"net **{_signed(returned - wagered)}**"
            for game, plays, wagered, returned in rows
        ]
        body = "\n".join(lines) if lines else "No settled games yet."
        total_net = sum(returned - wagered for _, _, wagered, returned in rows)
        fields: list[FieldSpec] = [("Total net", f"**{_signed(total_net)}**", True)] if rows else []
        return cls._create_base(
            title=f"{Emoji.DICE} Gambling record",
            description=f"{user_mention}\n{body}",
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def table_play(
        cls,
        game_label: str,
        rules: str,
        min_bet: int,
        max_bet: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The persistent embed in a table channel, above its Play button."""
        fields: list[FieldSpec] = [
            ("Stake", f"**{min_bet:,}** to **{max_bet:,}** Renown", True),
        ]
        return cls._create_base(
            title=f"{Emoji.DICE} {game_label} table",
            description=f"{rules}\
\
Press **Play** to open a table.",
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def table_lobby(
        cls,
        game_label: str,
        seats: Sequence[tuple[int, int]],  # (user_id, stake)
        min_players: int,
        max_players: int,
        closes_ts: int,
        pot: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """An open table: who is seated, the pot, and a live countdown to the start."""
        listed = "\
".join(f"<@{user_id}> — **{stake:,}**" for user_id, stake in seats)
        fields: list[FieldSpec] = [
            ("Players", f"**{len(seats)}/{max_players}**", True),
            ("Pot", f"**{pot:,}**", True),
            ("Starts", f"<t:{closes_ts}:R>", True),
        ]
        return cls._create_base(
            title=f"{Emoji.DICE} {game_label} table",
            description=(
                f"**Seats**\
{listed or 'Nobody yet.'}\
\
"
                f"Press **Join** to take a seat. Needs {min_players} to start."
            ),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def table_notice(
        cls, title: str, description: str, bot_avatar_url: Optional[str] = None
    ) -> discord.Embed:
        """A table that ended without a game: cancelled, or closed by a restart."""
        return cls._create_base(
            title=title,
            description=description,
            color=COLOR_IDLE,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def duel_picking(
        cls,
        seat_ids: Sequence[int],
        stake: int,
        pot: int,
        rake_percent: int,
        deadline_ts: int,
        picked: Sequence[int],
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The duel while players choose; picks stay secret, only who has locked in shows."""
        locked = " ".join(f"<@{user_id}>" for user_id in picked) or "Nobody yet."
        fields: list[FieldSpec] = [
            ("Stake", f"**{stake:,}** each", True),
            ("Pot", f"**{pot:,}**", True),
            ("Picks close", f"<t:{deadline_ts}:R>", True),
        ]
        rake_note = f" The house keeps {rake_percent}%." if rake_percent else ""
        return cls._create_base(
            title=f"{Emoji.DICE} Duel",
            description=(
                f"{' vs '.join(f'<@{user_id}>' for user_id in seat_ids)}\
"
                "A hidden number from **1** to **10** has been drawn. Press **Pick your number** "
                "to lock in a secret pick. The pick closest to the hidden number wins the pot."
                f"{rake_note}\
\
**Locked in:** {locked}"
            ),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def duel_result(
        cls,
        picks: Mapping[int, int],
        target: int,
        winner_id: Optional[int],
        prize: int,
        rake: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A finished duel: the hidden number, both picks, and who won or that it tied."""
        lines = [f"The hidden number was **{target}**."]
        lines.extend(f"<@{user_id}> picked **{number}**" for user_id, number in picks.items())
        fields: list[FieldSpec] = []
        if winner_id is None:
            lines.append("**It's a tie.** Both stakes were returned.")
            color, quip_key = COLOR_BRAND, "game_push"
        else:
            lines.append(f"**<@{winner_id}> wins {prize:,} Renown.**")
            color, quip_key = COLOR_SUCCESS, "game_win"
            fields.append(("Prize", f"**{prize:,}**", True))
            if rake:
                fields.append(("Rake", f"**{rake:,}**", True))
        return cls._create_base(
            title=f"{Emoji.DICE} Duel",
            description="\
".join(lines),
            color=color,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get(quip_key),
            fields=_grid(fields),
        )

    @classmethod
    def jackpot_drawing(
        cls,
        entries: Sequence[tuple[int, int]],  # (user_id, stake)
        pot: int,
        rake_percent: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The jackpot while the winner is being drawn: everyone's stake and chance."""
        lines = [
            f"<@{user_id}> — **{stake:,}** ({stake * 100 / pot:.0f}%)" for user_id, stake in entries
        ]
        rake_note = f" The house keeps {rake_percent}%." if rake_percent else ""
        fields: list[FieldSpec] = [
            ("Pot", f"**{pot:,}**", True),
            ("Players", f"**{len(entries)}**", True),
        ]
        return cls._create_base(
            title=f"{Emoji.SLOTS} Jackpot",
            description="\n".join(lines) + f"\n\nDrawing a winner, weighted by stake.{rake_note}",
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def jackpot_result(
        cls,
        entries: Sequence[tuple[int, int]],
        winner_id: int,
        prize: int,
        rake: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A drawn jackpot: the winner, the prize, and every entrant's chance."""
        pot = sum(stake for _, stake in entries)
        lines = [
            f"<@{user_id}> — **{stake:,}** ({stake * 100 / pot:.0f}%)" for user_id, stake in entries
        ]
        fields: list[FieldSpec] = [("Prize", f"**{prize:,}**", True)]
        if rake:
            fields.append(("Rake", f"**{rake:,}**", True))
        return cls._create_base(
            title=f"{Emoji.SLOTS} Jackpot",
            description=f"**<@{winner_id}> wins {prize:,} Renown.**\n\n" + "\n".join(lines),
            color=COLOR_SUCCESS,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("game_win"),
            fields=_grid(fields),
        )

    @classmethod
    def crash_flying(
        cls,
        rows: Sequence[tuple[int, int, Optional[int]]],  # (user_id, stake, cashed out at)
        multiplier: int,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A crash round in flight. Multipliers are whole hundredths (231 is 2.31x)."""
        lines = [
            f"<@{user_id}> — **{stake:,}** — "
            + ("still in" if cashed is None else f"out at **{cashed / 100:.2f}x**")
            for user_id, stake, cashed in rows
        ]
        return cls._create_base(
            title=f"{Emoji.DRONE} Crash",
            description=f"`{multiplier / 100:.2f}x`\n\n" + "\n".join(lines),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def crash_result(
        cls,
        rows: Sequence[tuple[int, int, Optional[int]]],
        crash_point: int,
        payouts: Mapping[int, int],
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A finished crash round: where it crashed and each player's net."""
        lines = []
        for user_id, stake, cashed in rows:
            net = _signed(payouts.get(user_id, 0) - stake)
            if cashed is None:
                lines.append(f"<@{user_id}> — crashed — **{net}**")
            else:
                lines.append(f"<@{user_id}> — out at **{cashed / 100:.2f}x** — **{net}**")
        anyone_won = any(cashed is not None for _, _, cashed in rows)
        return cls._create_base(
            title=f"{Emoji.DRONE} Crash",
            description=f"It crashed at **{crash_point / 100:.2f}x**.\n\n" + "\n".join(lines),
            color=COLOR_SUCCESS if anyone_won else COLOR_WARNING,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("game_win" if anyone_won else "game_loss"),
        )

    @classmethod
    def bjtable_hand(
        cls,
        seats: Sequence[tuple[int, int, Sequence[str], int, str]],
        dealer_cards: Sequence[str],
        dealer_total: Optional[int],
        deadline_ts: Optional[int],
        finished: bool,
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The shared-dealer table: every hand with a status, and the dealer's cards."""
        lines = [
            f"<@{user_id}> (**{stake:,}**) `{' '.join(hand)}` ({total}) — {status}"
            for user_id, stake, hand, total, status in seats
        ]
        dealer_suffix = f" ({dealer_total})" if dealer_total is not None else ""
        lines.append(f"**Dealer** `{' '.join(dealer_cards)}`{dealer_suffix}")
        fields: list[FieldSpec] = []
        if deadline_ts is not None and not finished:
            fields.append(("Turn ends", f"<t:{deadline_ts}:R>", True))
        return cls._create_base(
            title=f"{Emoji.DICE} Blackjack table",
            description="\n".join(lines),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def poker_table(
        cls,
        hand_no: int,
        max_hands: int,
        street: str,
        board: Sequence[str],
        pot: int,
        small_blind: int,
        big_blind: int,
        seats: Sequence[
            tuple[int, int, int, str, bool]
        ],  # (user, chips, street bet, status, button)
        deadline_ts: Optional[int],
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A poker hand in play: community cards, the pot and every seat. No hole cards."""
        board_line = f"**Board** `{' '.join(board)}`" if board else "No community cards yet."
        lines = [f"Hand {hand_no}/{max_hands} · {street.capitalize()}", board_line, ""]
        for user_id, chips, bet, status, is_button in seats:
            marker = " (D)" if is_button else ""
            bet_part = f" · bet {bet:,}" if bet else ""
            status_part = f" · {status}" if status else ""
            lines.append(f"<@{user_id}>{marker} — **{chips:,}**{bet_part}{status_part}")
        fields: list[FieldSpec] = [
            ("Pot", f"**{pot:,}**", True),
            ("Blinds", f"**{small_blind:,}/{big_blind:,}**", True),
        ]
        if deadline_ts is not None:
            fields.append(("Turn ends", f"<t:{deadline_ts}:R>", True))
        return cls._create_base(
            title=f"{Emoji.DICE} Poker table",
            description="\n".join(lines),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def poker_hand_result(
        cls,
        hand_no: int,
        max_hands: int,
        board: Sequence[str],
        awards: Sequence[tuple[int, Sequence[int], bool]],  # (amount, winners, returned)
        shown: Mapping[int, tuple[str, str]],  # user -> (hole cards, hand name) at a showdown
        stacks: Mapping[int, int],
        left: Sequence[tuple[int, int, str]],  # (user, chips paid out, bust/left/inactive)
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """How a hand ended: who took each pot, the cards shown, the stacks and who left."""
        lines = [f"Hand {hand_no}/{max_hands} is over."]
        if board:
            lines.append(f"**Board** `{' '.join(board)}`")
        for amount, winners, returned in awards:
            names = ", ".join(f"<@{user_id}>" for user_id in winners)
            if returned:
                lines.append(f"{names} gets **{amount:,}** back (uncalled)")
            elif len(winners) == 1:
                lines.append(f"{names} wins **{amount:,}**")
            else:
                lines.append(f"{names} split **{amount:,}**")
        for user_id, (cards_text, hand_name) in shown.items():
            lines.append(f"<@{user_id}> `{cards_text}` — {hand_name}")
        phrases = {"bust": "busted", "left": "left the table", "inactive": "sat out too long"}
        for user_id, chips, reason in left:
            lines.append(f"<@{user_id}> {phrases.get(reason, reason)} and was paid **{chips:,}**")
        remaining = [f"<@{user_id}> **{chips:,}**" for user_id, chips in stacks.items() if chips]
        if remaining:
            lines.append("**Stacks** " + " · ".join(remaining))
        return cls._create_base(
            title=f"{Emoji.DICE} Poker table",
            description="\n".join(lines),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
        )

    @classmethod
    def poker_final(
        cls,
        hands_played: int,
        max_hands: int,
        buy_in: int,
        results: Sequence[tuple[int, int]],  # (user, Renown paid out)
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """A closed poker table: what every player bought in for and left with."""
        lines = [f"The table closed after **{hands_played}/{max_hands}** hands.", ""]
        profit = False
        for user_id, paid in results:
            net = paid - buy_in
            profit = profit or net > 0
            change = f"+{net:,}" if net > 0 else (f"-{-net:,}" if net < 0 else "even")
            lines.append(
                f"<@{user_id}> — bought in **{buy_in:,}**, left with **{paid:,}** ({change})"
            )
        return cls._create_base(
            title=f"{Emoji.DICE} Poker table",
            description="\n".join(lines),
            color=COLOR_SUCCESS if profit else COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get("game_win") if profit else None,
        )

    @classmethod
    def lottery_panel(
        cls,
        pot: int,
        tickets_sold: int,
        number_range: int,
        price: int,
        max_tickets: int,
        next_draw_ts: Optional[int],
        last_draw: Optional[
            tuple[int, Sequence[int], int]
        ],  # (winning number, winners, prize each)
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The always-on lottery embed: the pot, the odds ingredients and the next draw."""
        lines = [
            f"Pick numbers from **1 to {number_range}** (up to {max_tickets} tickets each) at "
            f"**{price:,}** Renown a ticket. One number is drawn; everyone holding it splits "
            "the pot. If nobody holds it, the pot rolls over and grows.",
            "",
            "Press **Buy tickets** to pick your numbers or take quick picks.",
        ]
        if last_draw is not None:
            number, winners, prize_each = last_draw
            if winners:
                names = " ".join(f"<@{user_id}>" for user_id in winners[:10])
                lines.append(f"\n**Last draw:** {number} — {names} won **{prize_each:,}** each.")
            else:
                lines.append(f"\n**Last draw:** {number} — nobody held it.")
        fields: list[FieldSpec] = [
            ("Pot", f"**{pot:,}**", True),
            ("Tickets sold", f"**{tickets_sold:,}**", True),
            ("Next draw", f"<t:{next_draw_ts}:R>" if next_draw_ts else "Not started", True),
        ]
        return cls._create_base(
            title=f"{Emoji.COIN} Lottery",
            description="\n".join(lines),
            color=COLOR_BRAND,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def lottery_receipt(
        cls,
        numbers: Sequence[int],
        cost: int,
        pot: int,
        balance: int,
        next_draw_ts: Optional[int],
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The private confirmation after buying tickets."""
        fields: list[FieldSpec] = [
            ("Cost", f"**{cost:,}**", True),
            ("Pot", f"**{pot:,}**", True),
            ("Balance", f"**{balance:,}**", True),
        ]
        when = f" Next draw <t:{next_draw_ts}:R>." if next_draw_ts else ""
        return cls._create_base(
            title=f"{Emoji.COIN} Tickets bought",
            description=f"Your numbers: **{', '.join(str(n) for n in numbers)}**.{when}",
            color=COLOR_SUCCESS,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )

    @classmethod
    def lottery_announcement(
        cls,
        draw_no: int,
        winning_number: int,
        pot: int,
        winners: Sequence[int],
        prize_each: int,
        next_pot: int,
        next_draw_ts: Optional[int],
        bot_avatar_url: Optional[str] = None,
    ) -> discord.Embed:
        """The public result of a draw."""
        when = f"\nNext draw <t:{next_draw_ts}:R>." if next_draw_ts else ""
        if winners:
            shown = " ".join(f"<@{user_id}>" for user_id in winners[:20])
            more = f" and {len(winners) - 20} more" if len(winners) > 20 else ""
            body = (
                f"The winning number is **{winning_number}**.\n"
                f"**{len(winners)}** held it: {shown}{more}\n"
                f"Each wins **{prize_each:,}** Renown.{when}"
            )
            color, quip_key = COLOR_SUCCESS, "game_win"
        else:
            body = (
                f"The winning number is **{winning_number}**.\n"
                f"Nobody held it, so the pot rolls over. It now stands at **{next_pot:,}**.{when}"
            )
            color, quip_key = COLOR_BRAND, "game_loss"
        fields: list[FieldSpec] = [("Pot", f"**{pot:,}**", True)]
        return cls._create_base(
            title=f"{Emoji.COIN} Lottery draw #{draw_no}",
            description=body,
            color=color,
            context="Gambling",
            bot_avatar_url=bot_avatar_url,
            quip=quip_bank.get(quip_key),
            fields=_grid(fields),
        )


def _render_progress_bar(current: int, total: int, length: int = PROGRESS_LENGTH) -> str:
    """A bar of `length` glyphs with one head marker; never an emoji."""
    if total <= 0:
        return PROGRESS_HEAD + PROGRESS_EMPTY * (length - 1)
    progress = min(max(current / total, 0.0), 1.0)
    head = min(int(progress * length), length - 1)
    return PROGRESS_FILLED * head + PROGRESS_HEAD + PROGRESS_EMPTY * (length - 1 - head)


def _hp_bar(hp: int, max_hp: int, length: int = 10) -> str:
    """A short health bar: filled segments for the share of HP left (empty at 0)."""
    if max_hp <= 0 or hp <= 0:
        return PROGRESS_EMPTY * length
    filled = max(1, min(length, round(hp / max_hp * length)))
    return PROGRESS_FILLED * filled + PROGRESS_EMPTY * (length - filled)


def _format_duration(seconds: Optional[int]) -> str:
    if seconds is None or seconds < 0:
        return "--:--"
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def _add_footer_icon(interaction: discord.Interaction, embed: discord.Embed) -> None:
    """Give a footer without an icon the bot's avatar, so every footer looks the same.

    Tolerant of mocks and a missing bot user: anything but a string URL leaves it unchanged.
    """
    footer = embed.footer
    if not footer.text or footer.icon_url:
        return
    user = getattr(getattr(interaction, "client", None), "user", None)
    url = getattr(getattr(user, "display_avatar", None), "url", None)
    if isinstance(url, str) and url:
        embed.set_footer(text=footer.text, icon_url=url)


async def send(
    interaction: discord.Interaction,
    embed: discord.Embed,
    view: Optional[discord.ui.View] = None,
    ephemeral: bool = False,
) -> Optional[discord.Message]:
    """Uniform interaction responder handling lifecycle, ephemeral state, and views."""
    allowed_mentions = discord.AllowedMentions.none()
    _add_footer_icon(interaction, embed)

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
