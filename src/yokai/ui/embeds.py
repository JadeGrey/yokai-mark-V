"""Universal embed system for Yokai with strict clamping and semantic builders."""

from __future__ import annotations

import logging
from typing import Any, Mapping, Optional, Sequence

import discord

from yokai.textutil import escape_md
from yokai.theme import (
    COLOR_BRAND,
    COLOR_ERROR,
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


def _render_progress_bar(current: int, total: int, length: int = PROGRESS_LENGTH) -> str:
    """A bar of `length` glyphs with one head marker; never an emoji."""
    if total <= 0:
        return PROGRESS_HEAD + PROGRESS_EMPTY * (length - 1)
    progress = min(max(current / total, 0.0), 1.0)
    head = min(int(progress * length), length - 1)
    return PROGRESS_FILLED * head + PROGRESS_HEAD + PROGRESS_EMPTY * (length - 1 - head)


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
