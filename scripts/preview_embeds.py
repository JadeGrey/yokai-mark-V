"""Render every Yokai embed with realistic sample data into one HTML page for visual review.

Nothing is sent to Discord and nothing touches the network. Run from the repo root (PowerShell):

    .\\.venv\\Scripts\\python.exe scripts\\preview_embeds.py
    start docs\\preview\\embeds.html

The page is a document fragment (meta charset, title, style, content; no html/head/body tags) so it
opens in a browser and can also be published as an Artifact.
"""

from __future__ import annotations

import argparse
import html
import random
import re
import sys
from pathlib import Path

import discord

from yokai.music.models import Track
from yokai.theme import Emoji
from yokai.ui.embeds import EmbedFactory

PAGE_STYLE = """
:root { color-scheme: dark; }
body { margin: 0; background: #1e1f22; color: #dbdee1;
  font: 15px/1.4 "gg sans", "Segoe UI", system-ui, sans-serif; }
main { max-width: 760px; margin: 0 auto; padding: 24px 16px 64px; }
h1 { font-size: 22px; margin: 0 0 4px; color: #f2f3f5; }
p.lead { margin: 0 0 24px; color: #949ba4; }
.card { margin: 0 0 28px; }
.label { margin: 0 0 6px; font-size: 12px; letter-spacing: .06em; text-transform: uppercase;
  color: #949ba4; font-weight: 600; }
.embed { position: relative; background: #2b2d31; border-left: 4px solid #00e5ff;
  border-radius: 4px; padding: 10px 14px 12px; max-width: 520px; overflow: hidden; }
.embed .thumb { position: absolute; top: 12px; right: 12px; width: 72px; height: 72px;
  border-radius: 6px; background: linear-gradient(135deg, #00e5ff33, #78909c55); }
.embed .thumb + .body { margin-right: 88px; }
.title { font-weight: 700; font-size: 16px; color: #f2f3f5; margin: 0 0 4px; }
.title a { color: #00a8fc; text-decoration: none; }
.desc { white-space: normal; word-break: break-word; }
.desc a, .field a { color: #00a8fc; text-decoration: none; }
code { background: #1e1f22; border-radius: 3px; padding: 0 4px; font-size: 13px; }
.mention { background: #5865f24d; color: #c9cdfb; border-radius: 3px; padding: 0 2px; }
.fields { display: grid; grid-template-columns: repeat(3, minmax(0, 1fr)); gap: 8px 12px;
  margin-top: 8px; }
.field.wide { grid-column: 1 / -1; }
.field .fname { font-weight: 700; font-size: 13px; color: #f2f3f5; margin-bottom: 2px; }
.field .fval { font-size: 14px; word-break: break-word; }
.footer { display: flex; align-items: center; gap: 8px; margin-top: 10px; font-size: 12px;
  color: #949ba4; }
.footer .icon { width: 18px; height: 18px; border-radius: 50%;
  background: linear-gradient(135deg, #00e5ff, #0096c7); flex: none; }
"""


_ESCAPED = re.compile(r"\\([\\*_`~|\[\]<>()])")
_OPEN, _CLOSE = chr(0xE000), chr(0xE001)  # private-use placeholder delimiters
_PLACEHOLDER = re.compile(_OPEN + r"(\d+)" + _CLOSE)
_SAFE_URL = re.compile(r"^https?://", re.IGNORECASE)
AVATAR_URL = "https://img.example/avatar.png"
RANDOM_SEED = 20260925


def md_to_html(text: str) -> str:
    """A tiny Discord-markdown subset -> safe HTML (escapes first, so no markup can leak in).

    Backslash-escapes and finished links are swapped for private-use placeholders before the
    formatting regexes run, then restored at the end, so an escaped `\\*` never becomes italics.
    """
    stash: list[str] = []

    def keep(fragment: str) -> str:
        stash.append(fragment)
        return f"{_OPEN}{len(stash) - 1}{_CLOSE}"

    def restore(value: str) -> str:
        return _PLACEHOLDER.sub(lambda m: restore(stash[int(m.group(1))]), value)

    def link(m: re.Match[str]) -> str:
        label, url = m.group(2), m.group(3)
        # `url` is HTML-escaped without quotes, so quotes are the only way out of the attribute.
        href = url.replace('"', "&quot;").replace("'", "&#x27;")
        inner = f"<strong>{label}</strong>" if m.group(1) else label
        return keep(f'<a href="{href}">{inner}</a>')

    out = text.replace(_OPEN, "").replace(_CLOSE, "")
    out = _ESCAPED.sub(lambda m: keep(html.escape(m.group(1), quote=True)), out)
    out = html.escape(out, quote=False)
    out = re.sub(r"&lt;@!?\d+&gt;", '<span class="mention">@user</span>', out)
    out = re.sub(r"(\*\*)?\[([^\]]+?)\]\((https?://[^)\s]+)\)(?(1)\*\*)", link, out)
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    return restore(out).replace("\n", "<br>")


def render_embed(embed: discord.Embed, label: str) -> str:
    data = embed.to_dict()
    color = f"#{data.get('color', 0):06x}"
    parts = [
        f'<section class="card"><h3 class="label">{html.escape(label)}</h3>',
        f'<div class="embed" style="border-left-color:{color}">',
    ]
    if data.get("thumbnail"):
        parts.append('<div class="thumb" title="thumbnail placeholder"></div>')
    parts.append('<div class="body">')
    if data.get("title"):
        title_html = html.escape(data["title"])
        url = data.get("url") or ""
        if _SAFE_URL.match(url):
            title_html = f'<a href="{html.escape(url, quote=True)}">{title_html}</a>'
        parts.append(f'<div class="title">{title_html}</div>')
    if data.get("description"):
        parts.append(f'<div class="desc">{md_to_html(data["description"])}</div>')
    fields = data.get("fields", [])
    if fields:
        parts.append('<div class="fields">')
        for field in fields:
            klass = "field" if field.get("inline") else "field wide"
            # Discord does not render markdown in field names, so only escape them.
            name = html.escape(field.get("name", ""))
            value = md_to_html(field.get("value", ""))
            parts.append(
                f'<div class="{klass}"><div class="fname">{name}</div>'
                f'<div class="fval">{value}</div></div>'
            )
        parts.append("</div>")
    footer = data.get("footer") or {}
    if footer.get("text"):
        icon = '<span class="icon"></span>' if footer.get("icon_url") else ""
        parts.append(f'<div class="footer">{icon}{html.escape(footer["text"])}</div>')
    parts.append("</div></div></section>")
    return "".join(parts)


def build_samples() -> list[tuple[str, discord.Embed]]:
    """Every embed shape Yokai can send, with realistic data."""
    yt = "https://www.youtube.com/watch?v="
    thumb = "https://img.example/thumb.jpg"
    av = AVATAR_URL
    tracks = [
        Track(
            video_id="dQw4w9WgXcQ",
            title="Never Gonna Give You Up",
            artist="Rick Astley",
            duration_s=213,
            requester_id=1,
            origin="search",
        ),
        Track(
            video_id="lYBUbBu4W08",
            title="Never Gonna Give You Up",
            artist="Rick Astley",
            duration_s=214,
            requester_id=1,
            origin="import",
        ),
        Track(
            video_id="9bZkp7q19f0",
            title="Gangnam Style",
            artist="PSY",
            duration_s=253,
            requester_id=2,
            origin="recommendation",
        ),
    ]
    rows = [(i, t.title, t.artist, t.duration_s) for i, t in enumerate(tracks * 3, start=1)]
    long_title = "Extended Director's Cut Official Music Video (Remastered 2024) [4K Ultra HD]"
    long_artist = "The Very Long Named Philharmonic Orchestra and Choir of Somewhere"
    long_rows = [(i, f"{long_title} #{i}", long_artist, 200 + i) for i in range(1, 31)]
    special_title = "Song (Official Video) [HD] *live* `demo`"
    special_artist = "A*rtist [x](http://evil.example)"
    samples: list[tuple[str, discord.Embed]] = [
        (
            "Now playing",
            EmbedFactory.now_playing(
                title="Never Gonna Give You Up",
                artist="Rick Astley",
                duration_s=213,
                position_s=65,
                requester_mention="<@42>",
                thumbnail_url=thumb,
                url=yt + "dQw4w9WgXcQ",
                source="search",
                bot_avatar_url=av,
            ),
        ),
        (
            "Now playing (no artist, unknown length)",
            EmbedFactory.now_playing(
                title="Untitled upload",
                artist=None,
                duration_s=0,
                position_s=0,
                requester_mention="<@42>",
                source="link",
                bot_avatar_url=av,
            ),
        ),
        (
            "Queued",
            EmbedFactory.queued(
                title="Gangnam Style",
                position=3,
                artist="PSY",
                duration_s=253,
                url=yt + "9bZkp7q19f0",
                eta_s=440,
                requester_mention="<@42>",
                source="recommendation",
                thumbnail_url=thumb,
                bot_avatar_url=av,
            ),
        ),
        (
            "Queued (special characters)",
            EmbedFactory.queued(
                title=special_title,
                position=4,
                artist=special_artist,
                duration_s=187,
                url=yt + "9bZkp7q19f0",
                eta_s=900,
                requester_mention="<@42>",
                source="search",
                bot_avatar_url=av,
            ),
        ),
        (
            "Queue page",
            EmbedFactory.queue_page(
                tracks=rows[:10],
                page=1,
                total_pages=2,
                total_tracks=13,
                loop_mode="queue",
                now_playing=("Never Gonna Give You Up", yt + "dQw4w9WgXcQ", 213),
                total_runtime_s=3900,
                bot_avatar_url=av,
            ),
        ),
        (
            "Queue page (long list)",
            EmbedFactory.queue_page(
                tracks=long_rows,
                page=1,
                total_pages=3,
                total_tracks=75,
                loop_mode="off",
                now_playing=(special_title, yt + "dQw4w9WgXcQ", 187),
                total_runtime_s=17400,
                bot_avatar_url=av,
            ),
        ),
        (
            "Queue page (empty)",
            EmbedFactory.queue_page(
                tracks=[], page=1, total_pages=1, total_tracks=0, bot_avatar_url=av
            ),
        ),
        (
            "Recommendations",
            EmbedFactory.recommendations(
                tracks=[(t.title, t.artist or "Unknown", t.duration_s) for t in tracks],
                seed_title="Never Gonna Give You Up",
                bot_avatar_url=av,
            ),
        ),
        (
            "Recommendations (special characters)",
            EmbedFactory.recommendations(
                tracks=[
                    (special_title, special_artist, 187),
                    ("Wait [Remastered] (Live at Wembley)", "Artist *One* & `Two`", 254),
                    ("<b>Not bold</b> | pipe ~ tilde _under_", "Björk", 301),
                ],
                seed_title=special_title,
                bot_avatar_url=av,
            ),
        ),
        (
            "Playlist loaded",
            EmbedFactory.import_summary(
                kind="Playlist",
                name=None,
                loaded=42,
                total=None,
                queued=41,
                now_playing="Never Gonna Give You Up",
                bot_avatar_url=av,
            ),
        ),
        (
            "Spotify album loaded",
            EmbedFactory.import_summary(
                kind="Spotify album",
                name="Discovery",
                loaded=14,
                total=14,
                queued=13,
                now_playing="One More Time",
                bot_avatar_url=av,
            ),
        ),
        (
            "Spotify playlist (partial)",
            EmbedFactory.import_summary(
                kind="Spotify playlist",
                name="Road Trip Mix",
                loaded=50,
                total=120,
                queued=50,
                partial=True,
                bot_avatar_url=av,
            ),
        ),
        (
            "Success (skipped)",
            EmbedFactory.success(
                title="Skipped",
                description="Skipped **Gangnam Style**.",
                context="Controls",
                quip="Gone. Next.",
                bot_avatar_url=av,
            ),
        ),
        (
            "Info (nothing playing)",
            EmbedFactory.info(
                title="Nothing playing",
                description="The player is currently idle.",
                context="Status",
                bot_avatar_url=av,
            ),
        ),
        (
            "Warning",
            EmbedFactory.warning(
                title="Track skipped",
                description="Skipping **Some Song**. YouTube blocked the request (HTTP 403).",
                context="Playback",
                bot_avatar_url=av,
            ),
        ),
        (
            "Error (with hint and quip)",
            EmbedFactory.error(
                message="Could not find a confident YouTube match for 'Some Rare Song'.",
                user_hint="Try searching with the song title and artist, or paste a YouTube link.",
                title="No match found",
                context="System",
                bot_avatar_url=av,
            ),
        ),
        (
            "Error (plain, no quip)",
            EmbedFactory.error(
                message="You must be in a voice channel to use music commands.",
                user_hint="Join a voice channel and try again.",
                title="Voice channel needed",
                include_quip=False,
                bot_avatar_url=av,
            ),
        ),
        (
            "Ping (fast, slow resolver is not counted)",
            EmbedFactory.ping(
                gateway_ms=48.2,
                rest_ms=71.5,
                db_ms=0.8,
                voice_ms=32.0,
                voice_avg_ms=35.1,
                resolver_ms=1804.0,
                bot_avatar_url=av,
            ),
        ),
        (
            "Ping (elevated)",
            EmbedFactory.ping(
                gateway_ms=220.0, rest_ms=130.0, db_ms=1.5, resolver_ms=900.0, bot_avatar_url=av
            ),
        ),
        (
            "Ping (slow)",
            EmbedFactory.ping(gateway_ms=520.0, rest_ms=610.0, db_ms=2.0, bot_avatar_url=av),
        ),
        (
            "Usage",
            EmbedFactory.usage(
                top_users=[(1, 120, 88), (2, 64, 40), (3, 12, 5)],
                top_commands=[("play", 150), ("skip", 60), ("queue", 22)],
                period="Last 30 Days",
                bot_avatar_url=av,
            ),
        ),
        (
            "About",
            EmbedFactory.about(
                version="0.1.0",
                uptime_str="2d 3h 12m",
                host_os="Windows 11",
                owner_id=42,
                stack_info={
                    "Python": "3.14.0",
                    "discord.py": "2.7.1",
                    "yt-dlp": "2026.8.19",
                    "ytmusicapi": "1.12.3",
                    "FFmpeg": "2025-11-27",
                },
                bot_avatar_url=av,
            ),
        ),
        (
            "Diagnostics (healthy)",
            EmbedFactory.diag(
                diagnostics={
                    "Python / discord.py": "3.14.0 / 2.7.1",
                    "DAVE E2EE": "Enabled",
                    "YouTube Health": "Healthy",
                    "Database Size": "0.31 MB",
                },
                recent_errors=[],
                bot_avatar_url=av,
            ),
        ),
        (
            "Diagnostics (needs attention)",
            EmbedFactory.diag(
                diagnostics={
                    "Python / discord.py": "3.14.0 / 2.7.1",
                    "DAVE E2EE": "Enabled",
                    "YouTube Health": "Degraded (3 failures)",
                    "yt-dlp Version": "2026.8.19 (37d old) (outdated)",
                },
                recent_errors=["/play: BotCheckError: YouTube blocked the request"],
                status="warn",
                bot_avatar_url=av,
            ),
        ),
        (
            "Renown balance",
            EmbedFactory.balance(user_mention="<@42>", balance=1250, bot_avatar_url=av),
        ),
        (
            "Daily claimed",
            EmbedFactory.daily_claim(
                user_mention="<@42>", payout=180, streak_day=5, new_balance=1430, bot_avatar_url=av
            ),
        ),
        (
            "Renown sent",
            EmbedFactory.pay_sent(
                sender_mention="<@42>", recipient_mention="<@7>", amount=250, bot_avatar_url=av
            ),
        ),
        (
            "Renown leaderboard",
            EmbedFactory.leaderboard_page(
                entries=[(1, 42, 4200), (2, 7, 3100), (3, 13, 900)],
                page=1,
                total_pages=1,
                bot_avatar_url=av,
            ),
        ),
        (
            "Renown leaderboard (empty)",
            EmbedFactory.leaderboard_page(entries=[], page=1, total_pages=1, bot_avatar_url=av),
        ),
        (
            "Economy admin (grant)",
            EmbedFactory.economy_admin(
                target_mention="<@7>", delta_applied=500, new_balance=3600, bot_avatar_url=av
            ),
        ),
        (
            "Economy admin (deduction)",
            EmbedFactory.economy_admin(
                target_mention="<@7>", delta_applied=-200, new_balance=0, bot_avatar_url=av
            ),
        ),
        (
            "Slots (spinning)",
            EmbedFactory.spinning("Slots", "`[ ? ] [ ? ] [ ? ]`", Emoji.SLOTS, bot_avatar_url=av),
        ),
        (
            "Slots (win)",
            EmbedFactory.slots_result(
                "<@42>", ("SEVEN", "SEVEN", "SEVEN"), 10, 500, 690, bot_avatar_url=av
            ),
        ),
        (
            "Slots (loss)",
            EmbedFactory.slots_result(
                "<@42>", ("BAR", "LEMON", "BELL"), 10, 0, 190, bot_avatar_url=av
            ),
        ),
        (
            "Roulette (straight win)",
            EmbedFactory.roulette_result(
                "<@42>", "straight 17", 17, "black", 10, 360, 550, bot_avatar_url=av
            ),
        ),
        (
            "Blackjack (in play)",
            EmbedFactory.blackjack_hand(
                "<@42>", ["10h", "6c"], 16, ["9d", "??"], None, 10, bot_avatar_url=av
            ),
        ),
        (
            "Blackjack (natural)",
            EmbedFactory.blackjack_hand(
                "<@42>",
                ["As", "Kd"],
                21,
                ["Ks", "9h"],
                19,
                10,
                "blackjack",
                25,
                215,
                bot_avatar_url=av,
            ),
        ),
        (
            "Blackjack (bust)",
            EmbedFactory.blackjack_hand(
                "<@42>",
                ["10h", "6c", "10d"],
                26,
                ["9d", "8s"],
                17,
                10,
                "bust",
                0,
                190,
                bot_avatar_url=av,
            ),
        ),
        (
            "Gambling record",
            EmbedFactory.gamble_stats(
                "<@42>", [("blackjack", 1, 10, 20), ("slots", 2, 20, 40)], bot_avatar_url=av
            ),
        ),
        (
            "Table channel (Play embed)",
            EmbedFactory.table_play(
                "Duel", "Two players, one hidden number from 1 to 10.", 10, 5000, bot_avatar_url=av
            ),
        ),
        (
            "Table lobby (open)",
            EmbedFactory.table_lobby(
                "Duel", [(42, 100)], 2, 2, closes_ts=1_900_000_000, pot=100, bot_avatar_url=av
            ),
        ),
        (
            "Table cancelled",
            EmbedFactory.table_notice(
                "Table cancelled",
                "Not enough players joined in time. Every stake was refunded.",
                bot_avatar_url=av,
            ),
        ),
        (
            "Duel (picking)",
            EmbedFactory.duel_picking(
                [42, 43], 100, 200, 2, 1_900_000_045, [42], bot_avatar_url=av
            ),
        ),
        (
            "Duel (win)",
            EmbedFactory.duel_result({42: 4, 43: 9}, 5, 42, 196, 4, bot_avatar_url=av),
        ),
        (
            "Duel (tie)",
            EmbedFactory.duel_result({42: 3, 43: 7}, 5, None, 0, 0, bot_avatar_url=av),
        ),
    ]
    return samples


def render_page(samples: list[tuple[str, discord.Embed]]) -> str:
    cards = "".join(render_embed(embed, label) for label, embed in samples)
    return (
        '<meta charset="utf-8"><title>Yokai embed preview</title>'
        f"<style>{PAGE_STYLE}</style>"
        "<main><h1>Yokai embed preview</h1>"
        '<p class="lead">Every embed Yokai can send, built by the real EmbedFactory with sample '
        "data. Thumbnails are placeholders. Discord renders slightly differently, but layout, "
        "wording and color are what you will see.</p>"
        f"{cards}</main>"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--out", default="docs/preview/embeds.html")
    args = parser.parse_args()
    random.seed(RANDOM_SEED)  # quips are random; a fixed seed keeps the committed page stable
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_page(build_samples()), encoding="utf-8", newline="\n")
    sys.stdout.write(f"wrote {target}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
