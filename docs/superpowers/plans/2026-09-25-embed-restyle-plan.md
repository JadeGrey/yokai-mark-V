# Yokai Embed Restyle Implementation Plan (Phase 3)

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give every message Yokai sends one refined, consistent look: specific titles, a proper now-playing card, compact queue and recommendation lists, aligned status grids, and no more than one emoji per embed.

**Architecture:** Part A restyles the builders in `ui/embeds.py` in a backward-compatible way (same builder names, new optional arguments), so the bot keeps working after every task. It ends with an HTML preview of every embed that the owner must approve. Part B, only after that approval, wires the new information (track links, requester, source, queue ETA, specific error titles) into the cogs, views and the error handler.

**Tech Stack:** Python 3.12+, discord.py 2.7.1 (`discord.Embed`), pytest + pytest-asyncio, ruff. No new dependencies.

**Spec:** `docs/superpowers/specs/2026-09-24-yokai-overhaul-design.md` section 4 (owner-approved). Owner decisions that bind this plan: keep the cyan brand color; restyle embeds only (no interactive player panel); keep the persona rules from `AGENTS.md` (at most one emoji per embed, at most one short quip, everything through `EmbedFactory`, error embeds ephemeral); the owner approves the look from a preview before any call site changes.

**Deliberate deviation from spec section 4:** the import summaries show Loaded, Queued and Now playing (plus `N of M` when a Spotify collection was only partly loaded). They have no "skipped tracks" field: unmatched imported tracks are only discovered lazily during playback, and the existing failure notice already reports them once per burst, so a skipped list would need a producer that does not exist yet.

## Global Constraints

- Python 3.12+; every source file starts with `from __future__ import annotations`, is fully type-hinted, uses `logging` (never `print` in `src/`), no bare `except`.
- Every user-visible message goes through `EmbedFactory` (`ui/embeds.py`); `tests/test_code_standards.py` greps for raw text sends and must stay green. Error embeds are ephemeral. Buttons and views time out, disable themselves and only act for allowed users (unchanged).
- Persona (AGENTS.md / SPEC.md section 3): confident, a little cocky, never mean; at most one short quip (about 8 words or fewer) per message; **at most one emoji per embed**, taken from the fixed set in `theme.py`; error text stays plain and specific, the quip stays separate from the explanation; no Ubisoft artwork, audio or quotes.
- Discord embed limits enforced centrally by `clamp()`/`_create_base`: title 256, description 4096, field name 256, field value 1024, footer 2048, 25 fields, 6000 characters total. Truncate with an ellipsis; never raise.
- Colors, emoji, progress glyphs, separators and label text live in `theme.py`; the brand color stays cyan `0x00E5FF`.
- Default `pytest` makes no network calls. Test command (repo root): `.venv/Scripts/python.exe -m pytest -q`. Lint: `.venv/Scripts/python.exe -m ruff check .` and `.venv/Scripts/python.exe -m ruff format --check .`. Ruff: line length 100; run `ruff format` on files you create or change; if `ruff check` reports only import order (`I001`) fix with `ruff check --fix <file>`. `docs/` is excluded from ruff.
- Work on `main`. One commit per task, message ends with the trailer `Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>`. Never commit `.env`, tokens, databases, cookies, `data/` or `.superpowers/`.
- Emoji literals in the test code below (the title prefixes for play, queue, radio, error, warning and drone) must equal the `theme.Emoji` values exactly, including invisible variation selectors. If a title assertion fails only because of such an invisible difference, build the expected string from the constant (`f"{Emoji.PLAY} ..."`) instead of the literal.
- Baseline before this plan: 191 tests pass, ruff clean, tag `checkpoint/phase2-complete` is the revert point. Each part ends with a stop for the owner's review (AGENTS.md).

## Review Focus

Inputs the spec implies but no task's tests would obviously exercise. Each has a named test in the owning task.

1. **Markdown injection through track titles.** A YouTube title such as `[free gift](http://evil.example)` or one containing `**`, backticks, `<@123>` or `@everyone` must render as inert text in every list, link and field, never as a live link or a broken layout. (Task 2 `test_escape_md_neutralises_links_and_mentions`; Task 3 `test_track_text_is_escaped_in_lists_and_links`.)
2. **Hostile length and shape.** 10,000-character titles and artists, 50 tracks, empty strings, `None` artist, duration 0 or `None`, position 999: no exception, every limit respected, total under 6000. (Tasks 2-4 hostile-limit tests.)
3. **Imported Spotify tracks that are not matched yet** have ids like `sp:abc` and no YouTube page: they must never produce an embed link. (Task 7 `test_pending_spotify_track_gets_no_link`.)
4. **The one-emoji rule across every builder**, including text that used to smuggle a second emoji in (the old `💡`, the `🔘` progress head, `⚠️` in the yt-dlp update and diagnostics text). (Task 2 `test_at_most_one_emoji_per_embed`; Task 5 samples test; Task 8.)
5. **Empty and out-of-range states:** empty queue, nothing playing, a page beyond the last, a queue whose total runtime is unknown. (Task 3 `test_queue_page_empty_and_out_of_range_states`.)

---

# PART A: The look (backward compatible)

### Task 1: Theme tokens and a shared test helper

**Files:**
- Modify: `src/yokai/theme.py`
- Create: `tests/embed_helpers.py`
- Modify: `tests/test_theme.py`

**Interfaces:**
- Produces (in `yokai.theme`): `PROGRESS_LENGTH: int = 14`, `PROGRESS_FILLED = "━"`, `PROGRESS_HEAD = "●"`, `PROGRESS_EMPTY = "─"`, `SEP = " · "`, `LIST_PAGE_SIZE: int = 10`, `SOURCE_LABELS: Mapping[str, str]`, `source_label(origin: str) -> str`.
- Produces (in `tests/embed_helpers.py`): `EMOJI_RE`, `emoji_count(embed: discord.Embed) -> int`, `total_embed_length(embed: discord.Embed) -> int`, `all_embed_text(embed) -> str`.

- [ ] **Step 1: Write the failing tests**

Create `tests/embed_helpers.py`:

```python
"""Shared helpers for embed tests: emoji counting and total-length measurement."""

from __future__ import annotations

import re

import discord

# Emoji blocks plus the geometric shapes that only become emoji with a variation selector.
EMOJI_RE = re.compile(
    "[\U0001f300-\U0001faff☀-➿⌀-⏿⬀-⯿]️?|[■-◿]️"
)


def all_embed_text(embed: discord.Embed) -> str:
    data = embed.to_dict()
    parts: list[str] = [data.get("title", ""), data.get("description", "")]
    footer = data.get("footer") or {}
    parts.append(footer.get("text", ""))
    author = data.get("author") or {}
    parts.append(author.get("name", ""))
    for field in data.get("fields", []):
        parts.extend([field.get("name", ""), field.get("value", "")])
    return "\n".join(parts)


def emoji_count(embed: discord.Embed) -> int:
    return len(EMOJI_RE.findall(all_embed_text(embed)))


def total_embed_length(embed: discord.Embed) -> int:
    data = embed.to_dict()
    total = len(data.get("title", "")) + len(data.get("description", ""))
    total += len((data.get("footer") or {}).get("text", ""))
    total += len((data.get("author") or {}).get("name", ""))
    for field in data.get("fields", []):
        total += len(field.get("name", "")) + len(field.get("value", ""))
    return total
```

Replace the import line at the top of `tests/test_theme.py` (`from yokai.theme import QUIP_VARIANTS, QuipBank`) with:

```python
from tests.embed_helpers import EMOJI_RE
from yokai.theme import (
    LIST_PAGE_SIZE,
    PROGRESS_EMPTY,
    PROGRESS_FILLED,
    PROGRESS_HEAD,
    PROGRESS_LENGTH,
    QUIP_VARIANTS,
    SEP,
    Emoji,
    QuipBank,
    source_label,
)
```

and append to `tests/test_theme.py`:

```python
def test_progress_glyphs_are_single_non_emoji_characters() -> None:
    assert (PROGRESS_FILLED, PROGRESS_HEAD, PROGRESS_EMPTY) == ("━", "●", "─")
    for glyph in (PROGRESS_FILLED, PROGRESS_HEAD, PROGRESS_EMPTY):
        assert len(glyph) == 1
        assert not EMOJI_RE.search(glyph)
    assert PROGRESS_LENGTH >= 10
    assert SEP == " · "
    assert LIST_PAGE_SIZE == 10


def test_fixed_emoji_set_is_still_single_glyphs() -> None:
    names = [n for n in vars(Emoji) if not n.startswith("_")]
    assert names, "Emoji set must not be empty"
    for name in names:
        assert EMOJI_RE.search(getattr(Emoji, name)), f"{name} must be a real emoji"


def test_source_label_known_and_unknown_origins() -> None:
    assert source_label("link") == "YouTube link"
    assert source_label("search") == "Search"
    assert source_label("import") == "Spotify import"
    assert source_label("recommendation") == "Recommendation"
    assert source_label("something_new") == "Something new"
    assert source_label("") == "Unknown"
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_theme.py -q`
Expected: FAIL (`ImportError: cannot import name 'LIST_PAGE_SIZE' from 'yokai.theme'`).

- [ ] **Step 3: Implement**

In `src/yokai/theme.py`, add directly below the `Emoji` class (before the `# Persona Line Bank` comment):

```python
# Layout tokens shared by every embed builder
PROGRESS_LENGTH: Final[int] = 14
PROGRESS_FILLED: Final[str] = "━"
PROGRESS_HEAD: Final[str] = "●"
PROGRESS_EMPTY: Final[str] = "─"
SEP: Final[str] = " · "
LIST_PAGE_SIZE: Final[int] = 10

# Where a track came from (Track.origin) as shown to people
SOURCE_LABELS: Final[Mapping[str, str]] = {
    "link": "YouTube link",
    "search": "Search",
    "import": "Spotify import",
    "recommendation": "Recommendation",
}


def source_label(origin: str) -> str:
    """Human-readable label for a `Track.origin` value."""
    if origin in SOURCE_LABELS:
        return SOURCE_LABELS[origin]
    return origin.replace("_", " ").capitalize() or "Unknown"
```

- [ ] **Step 4: Verify and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_theme.py -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q`
Expected: theme tests pass, full suite green (191 + 3 new), ruff clean.

```bash
git add src/yokai/theme.py tests/embed_helpers.py tests/test_theme.py
git commit -m "feat(theme): add layout tokens, source labels and embed test helpers" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Embed core: safe text, field grid, and the new error shape

**Files:**
- Modify: `src/yokai/ui/embeds.py`
- Modify: `tests/test_embeds.py`

**Interfaces:**
- Consumes: `yokai.theme` tokens from Task 1; `tests/embed_helpers.py`.
- Produces (in `yokai.ui.embeds`): `FieldSpec = tuple[str, str, bool]`; `escape_md(text: str) -> str`; `format_runtime(seconds: int | None) -> str`; `_link(text: str, url: str | None) -> str`; `_grid(fields: Sequence[FieldSpec]) -> list[FieldSpec]`; `_list_line(pos, title, artist, duration_s) -> str`; new `_render_progress_bar(current, total, length=PROGRESS_LENGTH)`; `EmbedFactory._create_base(..., url=None, fields=(), footer_quip=None)`; `EmbedFactory.info/success/warning(..., fields=())`; `EmbedFactory.error(message, user_hint=None, context="System", include_quip=True, bot_avatar_url=None, title="That didn't work")` (hint becomes a "Try" field, quip goes in the footer, no 💡).
- Later tasks call all of these.

- [ ] **Step 1: Write the failing tests**

In `tests/test_embeds.py` replace the imports and the two helpers at the top (everything above `def test_clamp_basic`) with:

```python
"""Tests for universal embed system, clamping rules and the shared layout helpers."""

from __future__ import annotations

import discord
import pytest

from tests.embed_helpers import emoji_count, total_embed_length
from yokai.ui.embeds import (
    MAX_DESC_LEN,
    MAX_FIELD_NAME_LEN,
    MAX_FIELD_VAL_LEN,
    MAX_FIELDS_COUNT,
    MAX_FOOTER_LEN,
    MAX_TITLE_LEN,
    MAX_TOTAL_LEN,
    EmbedFactory,
    _grid,
    _link,
    _list_line,
    _render_progress_bar,
    clamp,
    escape_md,
    format_runtime,
)


def _total_embed_length(embed: discord.Embed) -> int:
    return total_embed_length(embed)
```

Replace the whole `test_error_embed_shape` function (the last test in the file) with:

```python
def test_error_embed_shape() -> None:
    """Fixed shape: specific title, plain explanation, a separate 'Try' field, quip in the footer."""
    err_embed = EmbedFactory.error(
        message="Cannot access the requested voice channel.",
        user_hint="Check permissions.",
        title="Missing permission",
    )
    assert err_embed.title == "❌ Missing permission"
    assert err_embed.description == "Cannot access the requested voice channel."
    assert [(f.name, f.value, f.inline) for f in err_embed.fields] == [
        ("Try", "Check permissions.", False)
    ]
    assert "💡" not in (err_embed.description or "")
    assert err_embed.footer.text.startswith("Yokai · System")
    # the persona quip is present but never mixed into the explanation
    assert err_embed.footer.text != "Yokai · System"
    assert "slipped" not in (err_embed.description or "").lower()


def test_error_embed_without_hint_or_quip_is_plain() -> None:
    err_embed = EmbedFactory.error(message="Boom.", include_quip=False)
    assert err_embed.title == "❌ That didn't work"
    assert err_embed.description == "Boom."
    assert len(err_embed.fields) == 0
    assert err_embed.footer.text == "Yokai · System"


def test_titles_are_never_generic() -> None:
    for embed in (
        EmbedFactory.error(message="x"),
        EmbedFactory.success(title="Skipped", description="x"),
        EmbedFactory.warning(title="Queue too short", description="x"),
    ):
        assert (embed.title or "").split(" ", 1)[-1] not in ("Error", "Success", "Warning")


def test_at_most_one_emoji_per_embed() -> None:
    hostile = "X" * 5000
    embeds = [
        EmbedFactory.info(title="Nothing playing", description="Idle."),
        EmbedFactory.success(title="Skipped", description="Skipped **Song**."),
        EmbedFactory.warning(title="Queue too short", description="Need two tracks."),
        EmbedFactory.error(message="Boom.", user_hint="Try again.", title="Broke"),
        EmbedFactory.error(message=hostile, user_hint=hostile, context=hostile),
    ]
    for embed in embeds:
        assert emoji_count(embed) <= 1, embed.to_dict()


def test_escape_md_neutralises_links_and_mentions() -> None:
    nasty = "[free gift](http://evil.example) **bold** `code` <@123> @everyone _it_ ~x~ |s|"
    escaped = escape_md(nasty)
    assert "[free gift](" not in escaped
    assert "\\[free gift\\]" in escaped
    assert "<@123>" not in escaped
    assert "\\*\\*bold\\*\\*" in escaped
    assert "\\`code\\`" in escaped
    assert escape_md("plain title") == "plain title"
    assert escape_md("") == ""


def test_link_escapes_text_and_omits_missing_url() -> None:
    assert _link("Song", "https://youtu.be/x") == "**[Song](https://youtu.be/x)**"
    assert _link("Song", None) == "**Song**"
    linked = _link("a](http://evil) b", "https://youtu.be/x")
    assert "](http://evil)" not in linked


def test_format_runtime() -> None:
    assert format_runtime(45) == "45s"
    assert format_runtime(440) == "7m 20s"
    assert format_runtime(65) == "1m 05s"
    assert format_runtime(4320) == "1h 12m"
    assert format_runtime(None) == "unknown"
    assert format_runtime(-5) == "unknown"


def test_progress_bar_shape_and_no_emoji() -> None:
    for current, total in [(0, 200), (100, 200), (200, 200), (500, 200), (0, 0), (5, -1)]:
        bar = _render_progress_bar(current, total)
        assert len(bar) == 14
        assert bar.count("●") == 1
        assert "🔘" not in bar
    assert _render_progress_bar(0, 200).startswith("●")
    assert _render_progress_bar(200, 200).endswith("●")
    assert _render_progress_bar(100, 200).index("●") == 7


def test_grid_pads_partial_rows_of_inline_fields() -> None:
    five = [(f"F{i}", "v", True) for i in range(5)]
    padded = _grid(five)
    assert len(padded) == 6
    assert padded[-1][0] == "​" and padded[-1][2] is True

    mixed = [("A", "1", True), ("B", "2", True), ("Wide", "3", False)]
    padded = _grid(mixed)
    assert [f[0] for f in padded] == ["A", "B", "​", "Wide"]

    assert _grid([]) == []
    full = [(f"F{i}", "v", True) for i in range(3)]
    assert _grid(full) == full


def test_list_line_format_and_clamping() -> None:
    assert _list_line(1, "Song", "Artist", 221) == "` 1.` **Song** — Artist · `3:41`"
    assert _list_line(12, "Song", None, 0) == "`12.` **Song**"
    long_line = _list_line(3, "T" * 500, "A" * 500, 100)
    assert len(long_line) < 200
    assert "\\[" in _list_line(1, "[x](y)", None, None)


def test_hostile_fields_respect_limits() -> None:
    giant = "X" * 10000
    embed = EmbedFactory.info(
        title=giant,
        description=giant,
        context=giant,
        fields=[(giant, giant, True)] * 40,
    )
    assert len(embed.fields) <= MAX_FIELDS_COUNT
    assert total_embed_length(embed) <= MAX_TOTAL_LEN
    for field in embed.fields:
        assert len(field.name) <= MAX_FIELD_NAME_LEN
        assert len(field.value) <= MAX_FIELD_VAL_LEN


@pytest.mark.parametrize("url", ["https://www.youtube.com/watch?v=abc", None])
def test_base_embed_accepts_url(url: str | None) -> None:
    embed = EmbedFactory._create_base(
        title="T", description="D", color=0x00E5FF, url=url, fields=[("A", "1", True)]
    )
    assert (embed.url or None) == url
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embeds.py -q`
Expected: FAIL (ImportError for `_grid` etc.).

- [ ] **Step 3: Implement**

Edit `src/yokai/ui/embeds.py`:

1. Replace the whole import block (from `import logging` through the closing `)` of the `from yokai.theme import (...)`) with:

```python
import logging
import re
from typing import Any, Mapping, Optional, Sequence

import discord

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
```

2. Replace everything from the line `MAX_TOTAL_LEN = 6000` through the end of the `warning` classmethod AND the whole `error` classmethod (that is: the `clamp` function, `class EmbedFactory:`, `_create_base`, `info`, `success`, `warning`, `error`) with the block below. Leave `now_playing`, `queued`, `queue_page`, `recommendations`, `ping`, `usage`, `about`, `diag` (later tasks replace them) and the module-level helpers below the class untouched for now.

```python
MAX_TOTAL_LEN = 6000

FieldSpec = tuple[str, str, bool]  # (name, value, inline)

ZERO_WIDTH = "​"
FIELDS_BUDGET = 3000  # characters reserved for fields so the description always has room
LIST_BUDGET = 3000  # characters of track lines in a list embed

_MD_SPECIAL = re.compile(r"([\\*_`~|\[\]<>()])")


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


def escape_md(text: str) -> str:
    """Escape Discord markdown so user-supplied text (track titles) renders as plain text.

    Backslash-escapes `\\ * _ ` ~ | [ ] ( ) < >`, which neutralises live links, mentions, code
    spans and formatting that a YouTube title could otherwise inject into an embed.
    """
    return _MD_SPECIAL.sub(r"\\\1", text)


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
```

3. Replace the module-level `_render_progress_bar` function (near the bottom, above `_format_duration`) with:

```python
def _render_progress_bar(current: int, total: int, length: int = PROGRESS_LENGTH) -> str:
    """A bar of `length` glyphs with one head marker; never an emoji."""
    if total <= 0:
        return PROGRESS_HEAD + PROGRESS_EMPTY * (length - 1)
    progress = min(max(current / total, 0.0), 1.0)
    head = min(int(progress * length), length - 1)
    return PROGRESS_FILLED * head + PROGRESS_HEAD + PROGRESS_EMPTY * (length - 1 - head)
```

(`ping`, `usage`, `about`, `diag` still call `embed.add_field(...)` after `_create_base`; that keeps working.)

- [ ] **Step 4: Verify**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embeds.py -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q`
Expected: embed tests pass; full suite green. The existing `test_hostile_embed_limits` still passes (error hint is now a field). If `tests/test_utility.py` or another test breaks only because it asserted the old `💡 **What to try:**` text or an old progress glyph, update that assertion to the new shape and mention it in the commit body.

- [ ] **Step 5: Commit**

```bash
git add src/yokai/ui/embeds.py tests/test_embeds.py
git commit -m "feat(embeds): safe markdown, grid padding, new error shape, emoji-free progress bar" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Music builders: now playing, queued, queue page, recommendations, import summary

**Files:**
- Modify: `src/yokai/ui/embeds.py`
- Modify: `tests/test_embeds.py`

**Interfaces:**
- Consumes: Task 2 helpers (`_link`, `_grid`, `_list_line`, `format_runtime`, `escape_md`, `FieldSpec`, `LIST_BUDGET`, `_render_progress_bar`, `_format_duration`), `source_label`.
- Produces (all keyword-friendly, new arguments optional, old call sites keep working):
  - `EmbedFactory.now_playing(title, artist, duration_s, position_s, requester_mention, thumbnail_url=None, bot_avatar_url=None, url=None, source=None)`
  - `EmbedFactory.queued(title, position, artist=None, duration_s=None, bot_avatar_url=None, url=None, eta_s=None, requester_mention=None, source=None, thumbnail_url=None)`
  - `EmbedFactory.queue_page(tracks, page, total_pages, total_tracks, loop_mode="off", bot_avatar_url=None, now_playing=None, total_runtime_s=None)` where `now_playing: tuple[str, str | None, int | None] | None` is `(title, url, duration_s)`
  - `EmbedFactory.recommendations(tracks, seed_title, bot_avatar_url=None)` (same arguments as before)
  - `EmbedFactory.import_summary(kind, name, loaded, total, queued, now_playing=None, partial=False, bot_avatar_url=None)` with `name: str | None`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_embeds.py`:

```python
def _fields(embed: discord.Embed) -> dict[str, str]:
    return {f.name: f.value for f in embed.fields if f.name != "​"}


def test_now_playing_layout() -> None:
    embed = EmbedFactory.now_playing(
        title="Never Gonna Give You Up",
        artist="Rick Astley",
        duration_s=213,
        position_s=65,
        requester_mention="<@42>",
        thumbnail_url="https://img.example/t.jpg",
        url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        source="search",
    )
    assert embed.title == "▶️ Never Gonna Give You Up"
    assert embed.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    assert "**Rick Astley**" in embed.description
    assert "●" in embed.description and "🔘" not in embed.description
    assert "`1:05 / 3:33`" in embed.description
    assert _fields(embed) == {
        "Duration": "`3:33`",
        "Requested by": "<@42>",
        "Source": "Search",
    }
    assert len(embed.fields) == 3  # already a full row, so no padding
    assert embed.thumbnail.url == "https://img.example/t.jpg"
    assert emoji_count(embed) == 1


def test_now_playing_without_optional_data() -> None:
    embed = EmbedFactory.now_playing(
        title="Song", artist=None, duration_s=0, position_s=0, requester_mention="<@1>"
    )
    assert embed.url is None
    assert "`0:00 / --:--`" in embed.description or "0:00 / 0:00" in embed.description
    assert "Source" not in _fields(embed)
    assert emoji_count(embed) == 1


def test_queued_layout_with_eta() -> None:
    embed = EmbedFactory.queued(
        title="Song A",
        position=3,
        artist="Artist",
        duration_s=200,
        url="https://www.youtube.com/watch?v=abc",
        eta_s=440,
        requester_mention="<@7>",
        source="import",
    )
    assert embed.title == "📑 Queued"
    assert "**[Song A](https://www.youtube.com/watch?v=abc)**" in embed.description
    assert "by Artist" in embed.description
    assert _fields(embed) == {
        "Position": "`#3`",
        "Plays in": "`~7m 20s`",
        "Duration": "`3:20`",
        "Requested by": "<@7>",
        "Source": "Spotify import",
    }
    assert emoji_count(embed) == 1


def test_queued_old_call_style_still_works() -> None:
    embed = EmbedFactory.queued(title="Song", position=1, artist="A", duration_s=100)
    assert _fields(embed)["Position"] == "`#1`"
    assert "Plays in" not in _fields(embed)


def test_track_text_is_escaped_in_lists_and_links() -> None:
    nasty = "[free gift](http://evil.example) <@123> **x**"
    queued = EmbedFactory.queued(title=nasty, position=1, artist=nasty, url=None)
    assert "[free gift](" not in queued.description
    now = EmbedFactory.now_playing(
        title=nasty, artist=nasty, duration_s=10, position_s=1, requester_mention="<@1>"
    )
    assert "[free gift](" not in now.description
    page = EmbedFactory.queue_page(
        tracks=[(1, nasty, nasty, 10)], page=1, total_pages=1, total_tracks=1
    )
    assert "[free gift](" not in page.description
    recs = EmbedFactory.recommendations(tracks=[(nasty, nasty, 10)], seed_title=nasty)
    assert "[free gift](" not in recs.description


def test_queue_page_compact_list_with_header() -> None:
    tracks = [(i, f"Song {i}", f"Artist {i}", 180 + i) for i in range(1, 4)]
    embed = EmbedFactory.queue_page(
        tracks=tracks,
        page=1,
        total_pages=2,
        total_tracks=13,
        loop_mode="queue",
        now_playing=("Current Song", "https://www.youtube.com/watch?v=cur", 200),
        total_runtime_s=3900,
    )
    assert embed.title == "📑 Up next"
    assert len(embed.fields) == 0
    lines = embed.description.splitlines()
    assert "**Now playing**" in lines[0] and "[Current Song](" in lines[0]
    assert "**13** up next" in lines[1]
    assert "1h 05m total" in lines[1]
    assert "loop queue" in lines[1]
    assert "` 1.` **Song 1** — Artist 1 · `3:01`" in embed.description
    assert embed.footer.text == "Yokai · Page 1/2"
    assert emoji_count(embed) == 1


def test_queue_page_empty_and_out_of_range_states() -> None:
    empty = EmbedFactory.queue_page(tracks=[], page=1, total_pages=1, total_tracks=0)
    assert "The queue is empty." in empty.description
    assert "**0** up next" in empty.description
    assert emoji_count(empty) <= 1

    beyond = EmbedFactory.queue_page(tracks=[], page=9, total_pages=0, total_tracks=0)
    assert beyond.footer.text == "Yokai · Page 9/1"


def test_queue_page_truncates_long_lists_within_limits() -> None:
    giant = "G" * 10000
    tracks = [(i, giant, giant, 180) for i in range(1, 51)]
    embed = EmbedFactory.queue_page(
        tracks=tracks,
        page=1,
        total_pages=5,
        total_tracks=50,
        now_playing=(giant, "https://www.youtube.com/watch?v=x", 10),
    )
    assert len(embed.description) <= MAX_DESC_LEN
    assert total_embed_length(embed) <= MAX_TOTAL_LEN
    assert "more on this page" in embed.description
    assert len(embed.fields) == 0


def test_recommendations_use_the_shared_list_style() -> None:
    embed = EmbedFactory.recommendations(
        tracks=[("Song A", "Artist A", 221), ("Song B", "Artist B", 0)], seed_title="Seed Song"
    )
    assert embed.title == "📻 Radio recommendations"
    assert "Seeded from **Seed Song**" in embed.description
    assert "` 1.` **Song A** — Artist A · `3:41`" in embed.description
    assert "` 2.` **Song B** — Artist B" in embed.description
    assert emoji_count(embed) == 1


def test_import_summary_layout_and_partial_note() -> None:
    embed = EmbedFactory.import_summary(
        kind="Spotify album",
        name="Discovery",
        loaded=14,
        total=14,
        queued=13,
        now_playing="One More Time",
    )
    assert embed.title == "📑 Spotify album loaded"
    assert embed.description == "**Discovery**"
    assert _fields(embed) == {"Loaded": "`14`", "Queued": "`13`", "Now playing": "One More Time"}

    partial = EmbedFactory.import_summary(
        kind="Spotify playlist", name="Big", loaded=50, total=120, queued=50, partial=True
    )
    assert _fields(partial)["Loaded"] == "`50 of 120`"
    assert "Now playing" not in _fields(partial)

    unnamed = EmbedFactory.import_summary(
        kind="Playlist", name=None, loaded=3, total=None, queued=2, now_playing="Song 0"
    )
    assert "Loaded from the playlist link." in unnamed.description
    assert emoji_count(embed) == 1
    assert emoji_count(unnamed) == 1


def test_music_builders_hostile_limits_and_emoji() -> None:
    giant = "X" * 10000
    embeds = [
        EmbedFactory.now_playing(
            title=giant,
            artist=giant,
            duration_s=10**9,
            position_s=-5,
            requester_mention=giant,
            source=giant,
            url="https://www.youtube.com/watch?v=x",
        ),
        EmbedFactory.queued(
            title=giant,
            position=999,
            artist=giant,
            duration_s=None,
            eta_s=10**9,
            requester_mention=giant,
            source="link",
        ),
        EmbedFactory.recommendations(tracks=[(giant, giant, 200)] * 10, seed_title=giant),
        EmbedFactory.import_summary(
            kind=giant, name=giant, loaded=1, total=2, queued=1, now_playing=giant, partial=True
        ),
    ]
    for embed in embeds:
        assert len(embed.title) <= MAX_TITLE_LEN
        assert len(embed.description) <= MAX_DESC_LEN
        assert total_embed_length(embed) <= MAX_TOTAL_LEN
        assert emoji_count(embed) <= 1
```

Also change the existing `test_hostile_embed_limits` assertions for section 4 (queue page) so it does not rely on fields: replace

```python
    assert len(queue_embed.fields) <= MAX_FIELDS_COUNT
    for f in queue_embed.fields:
        assert len(f.name) <= MAX_FIELD_NAME_LEN
        assert len(f.value) <= MAX_FIELD_VAL_LEN
```

with

```python
    assert len(queue_embed.description) <= MAX_DESC_LEN
    assert _total_embed_length(queue_embed) <= MAX_TOTAL_LEN
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embeds.py -q`
Expected: FAIL (`import_summary` missing, old layouts).

- [ ] **Step 3: Implement**

In `src/yokai/ui/embeds.py`, replace the four existing classmethods `now_playing`, `queued`, `queue_page`, `recommendations` (they are contiguous) with the block below, and add `import_summary` right after `recommendations`:

```python
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
        return cls._create_base(
            title=f"{Emoji.QUEUE} {clamp(kind, 60)} loaded",
            description=description,
            color=COLOR_BRAND,
            context="Import",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )
```

- [ ] **Step 4: Verify**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embeds.py -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q`
Expected: embed tests pass. Full suite: everything green EXCEPT possibly tests that assert the old queue/recommendation text or fields (search for `Upcoming Queue`, `Radio Recommendations`, `Now Playing:`). If such a test fails only because it encodes the old layout, update it to the new layout in this commit and list it in the commit body. `tests/test_music_cog.py` (`Playlist Loaded`) is updated in Part B, Task 7; it must still pass now because the cog does not call `import_summary` yet.

- [ ] **Step 5: Commit**

```bash
git add src/yokai/ui/embeds.py tests/test_embeds.py
git commit -m "feat(embeds): restyle now playing, queued, queue pages, recommendations, imports" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Meta builders: aligned grids and status color

**Files:**
- Modify: `src/yokai/ui/embeds.py`
- Modify: `tests/test_embeds.py`

**Interfaces:**
- Consumes: `_grid`, `FieldSpec`, `Emoji`.
- Produces: `EmbedFactory.ping/usage/about` keep their signatures; `EmbedFactory.diag(diagnostics, recent_errors, bot_avatar_url=None, status="ok")` where `status` is `"ok"` (green, drone emoji) or `"warn"` (amber, warning emoji). Titles unchanged: `System Latencies`, `Yokai Usage Statistics`, `About Yokai v<version>`, `Diagnostic Telemetry` (existing tests assert them).

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_embeds.py`:

```python
def test_ping_fields_are_grid_aligned_and_status_colored() -> None:
    good = EmbedFactory.ping(gateway_ms=40.0, rest_ms=60.0, db_ms=1.0, voice_ms=30.0, resolver_ms=20.0)
    assert "System Latencies" in good.title
    assert len(good.fields) % 3 == 0  # 5 real fields + 1 spacer
    assert good.fields[-1].name == "​"
    assert {"Gateway (WS)", "REST API", "Database", "Voice", "Resolver"} <= {
        f.name for f in good.fields
    }
    slow = EmbedFactory.ping(gateway_ms=500.0, rest_ms=None, db_ms=None)
    assert slow.color.value != good.color.value
    assert emoji_count(good) == 1 and emoji_count(slow) == 1


def test_about_stack_grid_is_aligned_and_notice_is_wide() -> None:
    embed = EmbedFactory.about(
        version="0.1.0",
        uptime_str="1h 2m",
        host_os="Windows 11",
        stack_info={"Python": "3.14", "discord.py": "2.7.1", "yt-dlp": "x", "ytmusicapi": "y"},
        owner_id=42,
    )
    inline = [f for f in embed.fields if f.inline]
    assert len(inline) % 3 == 0
    assert embed.fields[-1].name == "Notice" and embed.fields[-1].inline is False
    assert emoji_count(embed) == 1


def test_diag_status_changes_color_and_emoji() -> None:
    ok = EmbedFactory.diag(diagnostics={"Python": "3.14"}, recent_errors=[])
    warn = EmbedFactory.diag(diagnostics={"Python": "3.14"}, recent_errors=["boom"], status="warn")
    assert "Diagnostic Telemetry" in ok.title
    assert ok.title.startswith("🛸") and warn.title.startswith("⚠️")
    assert ok.color.value != warn.color.value
    assert emoji_count(ok) == 1 and emoji_count(warn) == 1
    inline = [f for f in ok.fields if f.inline]
    assert len(inline) % 3 == 0


def test_usage_embed_still_has_period_and_two_fields() -> None:
    embed = EmbedFactory.usage(top_users=[(1, 5, 2)], top_commands=[("play", 9)], period="Last 7 Days")
    assert "Last 7 Days" in embed.description
    assert [f.name for f in embed.fields] == ["Top Operators", "Top Commands"]
    assert emoji_count(embed) == 1
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embeds.py -q`
Expected: FAIL (no grid padding yet, `status` argument missing).

- [ ] **Step 3: Implement**

In `src/yokai/ui/embeds.py`:

1. In `ping`, replace the block that builds `embed = cls._create_base(...)` and the following five `embed.add_field(...)` calls plus `return embed` (everything after the `_fmt` helper) with:

```python
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
```

2. In `about`, replace everything after the `desc = (...)` block (the `embed = cls._create_base(...)` call, the `for component, ver in stack_info.items()` loop, the `Notice` field and `return embed`) with:

```python
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
```

3. Replace the whole `diag` method with:

```python
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
            fields.append(
                (f"Recent Exceptions ({len(recent_errors)})", "\n".join(lines), False)
            )
        else:
            fields.append(("Recent Exceptions", "*No errors recorded.*", False))

        return cls._create_base(
            title=f"{Emoji.WARN if needs_attention else Emoji.DRONE} Diagnostic Telemetry",
            description="Owner-only operational diagnostic overview.",
            color=COLOR_WARNING if needs_attention else COLOR_SUCCESS,
            context="Diagnostic",
            bot_avatar_url=bot_avatar_url,
            fields=_grid(fields),
        )
```

4. `usage` needs no change (it already builds two non-inline fields); leave it.

Remove any import that became unused (`COLOR_IDLE` if nothing else uses it: check with ruff; if `COLOR_IDLE` is now unused in `embeds.py`, delete it from the import list).

- [ ] **Step 4: Verify and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_embeds.py tests/test_utility.py -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q`
Expected: all green. `tests/test_utility.py` asserts `"System Latencies"`, `"Diagnostic Telemetry"`, field names `Voice`/`Resolver`, and `Recent Exceptions` field names; the new code keeps every one of those strings.

```bash
git add src/yokai/ui/embeds.py tests/test_embeds.py
git commit -m "feat(embeds): aligned grids and status color for /ping, /about and /diag" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: HTML preview of every embed, then the owner's approval gate

**Files:**
- Create: `scripts/preview_embeds.py`
- Create: `tests/test_preview_embeds.py`
- Create (generated): `docs/preview/embeds.html`

**Interfaces:**
- Consumes: every `EmbedFactory` builder; `tests/embed_helpers.py`.
- Produces: `scripts/preview_embeds.py` with `build_samples() -> list[tuple[str, discord.Embed]]`, `md_to_html(text: str) -> str`, `render_embed(embed: discord.Embed, label: str) -> str`, `render_page(samples) -> str`, `main() -> int` (writes `docs/preview/embeds.html`, a document fragment that starts with `<meta charset="utf-8">`, `<title>` and `<style>` and has no `<html>`, `<head>` or `<body>` tags so it can also be published as an Artifact).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_preview_embeds.py`:

```python
"""Tests for the embed HTML preview tool."""

from __future__ import annotations

import html
import importlib.util
from pathlib import Path
from types import ModuleType

import pytest

from tests.embed_helpers import emoji_count, total_embed_length


def _load() -> ModuleType:
    path = Path(__file__).resolve().parent.parent / "scripts" / "preview_embeds.py"
    spec = importlib.util.spec_from_file_location("preview_embeds", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


preview = _load()


def test_md_to_html_escapes_html_and_renders_basic_markdown() -> None:
    html_out = preview.md_to_html("<script>alert(1)</script> **bold** *it* `code` <@42>")
    assert "<script>" not in html_out
    assert "&lt;script&gt;" in html_out
    assert "<strong>bold</strong>" in html_out
    assert "<em>it</em>" in html_out
    assert "<code>code</code>" in html_out
    assert "@user" in html_out and "&lt;@42&gt;" not in html_out


def test_md_to_html_links_and_escaped_brackets() -> None:
    linked = preview.md_to_html("**[Song](https://www.youtube.com/watch?v=abc)**")
    assert '<a href="https://www.youtube.com/watch?v=abc"' in linked
    escaped = preview.md_to_html("\\[not a link\\](http://evil.example)")
    assert "<a " not in escaped
    assert "[not a link]" in escaped


def test_every_sample_embed_obeys_the_layout_rules() -> None:
    samples = preview.build_samples()
    assert len(samples) >= 14
    labels = [label for label, _ in samples]
    assert len(labels) == len(set(labels))
    for label, embed in samples:
        assert emoji_count(embed) <= 1, label
        assert total_embed_length(embed) <= 6000, label
        assert len(embed.fields) <= 25, label


def test_render_page_is_a_fragment_containing_every_embed() -> None:
    samples = preview.build_samples()
    page = preview.render_page(samples)
    assert page.lstrip().startswith('<meta charset="utf-8">')
    assert "<html" not in page and "<body" not in page and "<head" not in page
    for label, embed in samples:
        assert label in page
        if embed.title:
            assert html.escape(embed.title.split(" ", 1)[-1][:20]) in page


def test_render_embed_shows_fields_in_a_grid_and_color_bar() -> None:
    samples = dict(preview.build_samples())
    embed = next(e for label, e in samples.items() if "Now playing" in label)
    rendered = preview.render_embed(embed, "Now playing")
    assert "border-left-color:#00e5ff" in rendered.lower().replace(" ", "")
    assert "Requested by" in rendered and "Duration" in rendered
    assert 'class="field' in rendered


def test_main_writes_the_preview_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out = tmp_path / "preview" / "embeds.html"
    monkeypatch.setattr("sys.argv", ["preview_embeds.py", "--out", str(out)])
    assert preview.main() == 0
    assert out.exists() and out.stat().st_size > 2000
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_preview_embeds.py -q`
Expected: FAIL (`FileNotFoundError` for `scripts/preview_embeds.py`).

- [ ] **Step 3: Implement `scripts/preview_embeds.py`**

```python
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
import re
import sys
from pathlib import Path

import discord

from yokai.music.models import Track
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


def md_to_html(text: str) -> str:
    """A tiny Discord-markdown subset -> safe HTML (escapes first, so no markup can leak in)."""
    out = html.escape(text, quote=False)
    out = re.sub(r"&lt;@!?\d+&gt;", '<span class="mention">@user</span>', out)
    out = re.sub(
        r"\*\*\[((?:\\.|[^\]\\])+?)\]\((https?://[^)\s]+)\)\*\*",
        r'<a href="\2"><strong>\1</strong></a>',
        out,
    )
    out = re.sub(
        r"\[((?:\\.|[^\]\\])+?)\]\((https?://[^)\s]+)\)", r'<a href="\2">\1</a>', out
    )
    out = re.sub(r"\*\*(.+?)\*\*", r"<strong>\1</strong>", out)
    out = re.sub(r"(?<!\*)\*(?!\*)(.+?)(?<!\*)\*(?!\*)", r"<em>\1</em>", out)
    out = re.sub(r"`([^`]+)`", r"<code>\1</code>", out)
    out = out.replace("\\[", "[").replace("\\]", "]").replace("\\*", "*").replace("\\_", "_")
    out = out.replace("\\`", "`").replace("\\~", "~").replace("\\|", "|")
    out = out.replace("\\(", "(").replace("\\)", ")")
    out = out.replace("\\&lt;", "&lt;").replace("\\&gt;", "&gt;").replace("\\\\", "\\")
    return out.replace("\n", "<br>")


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
        if data.get("url"):
            title_html = f'<a href="{html.escape(data["url"])}">{title_html}</a>'
        parts.append(f'<div class="title">{title_html}</div>')
    if data.get("description"):
        parts.append(f'<div class="desc">{md_to_html(data["description"])}</div>')
    fields = data.get("fields", [])
    if fields:
        parts.append('<div class="fields">')
        for field in fields:
            klass = "field" if field.get("inline") else "field wide"
            name = md_to_html(field.get("name", ""))
            value = md_to_html(field.get("value", ""))
            parts.append(
                f'<div class="{klass}"><div class="fname">{name}</div>'
                f'<div class="fval">{value}</div></div>'
            )
        parts.append("</div>")
    footer = (data.get("footer") or {}).get("text")
    if footer:
        parts.append(f'<div class="footer"><span class="icon"></span>{html.escape(footer)}</div>')
    parts.append("</div></div></section>")
    return "".join(parts)


def build_samples() -> list[tuple[str, discord.Embed]]:
    """Every embed shape Yokai can send, with realistic data."""
    yt = "https://www.youtube.com/watch?v="
    thumb = "https://img.example/thumb.jpg"
    avatar = None
    tracks = [
        Track(video_id="dQw4w9WgXcQ", title="Never Gonna Give You Up", artist="Rick Astley",
              duration_s=213, requester_id=1, origin="search"),
        Track(video_id="lYBUbBu4W08", title="Never Gonna Give You Up", artist="Rick Astley",
              duration_s=214, requester_id=1, origin="import"),
        Track(video_id="9bZkp7q19f0", title="Gangnam Style", artist="PSY",
              duration_s=253, requester_id=2, origin="recommendation"),
    ]
    rows = [(i, t.title, t.artist, t.duration_s) for i, t in enumerate(tracks * 3, start=1)]
    samples: list[tuple[str, discord.Embed]] = [
        ("Now playing", EmbedFactory.now_playing(
            title="Never Gonna Give You Up", artist="Rick Astley", duration_s=213,
            position_s=65, requester_mention="<@42>", thumbnail_url=thumb,
            url=yt + "dQw4w9WgXcQ", source="search", bot_avatar_url=avatar)),
        ("Now playing (no artist, unknown length)", EmbedFactory.now_playing(
            title="Untitled upload", artist=None, duration_s=0, position_s=0,
            requester_mention="<@42>", source="link")),
        ("Queued", EmbedFactory.queued(
            title="Gangnam Style", position=3, artist="PSY", duration_s=253,
            url=yt + "9bZkp7q19f0", eta_s=440, requester_mention="<@42>",
            source="recommendation", thumbnail_url=thumb)),
        ("Queue page", EmbedFactory.queue_page(
            tracks=rows[:10], page=1, total_pages=2, total_tracks=13, loop_mode="queue",
            now_playing=("Never Gonna Give You Up", yt + "dQw4w9WgXcQ", 213),
            total_runtime_s=3900)),
        ("Queue page (empty)", EmbedFactory.queue_page(
            tracks=[], page=1, total_pages=1, total_tracks=0)),
        ("Recommendations", EmbedFactory.recommendations(
            tracks=[(t.title, t.artist or "Unknown", t.duration_s) for t in tracks],
            seed_title="Never Gonna Give You Up")),
        ("Playlist loaded", EmbedFactory.import_summary(
            kind="Playlist", name=None, loaded=42, total=None, queued=41,
            now_playing="Never Gonna Give You Up")),
        ("Spotify album loaded", EmbedFactory.import_summary(
            kind="Spotify album", name="Discovery", loaded=14, total=14, queued=13,
            now_playing="One More Time")),
        ("Spotify playlist (partial)", EmbedFactory.import_summary(
            kind="Spotify playlist", name="Road Trip Mix", loaded=50, total=120, queued=50,
            partial=True)),
        ("Success (skipped)", EmbedFactory.success(
            title="Skipped", description="Skipped **Gangnam Style**.", context="Controls",
            quip="Gone. Next.")),
        ("Info (nothing playing)", EmbedFactory.info(
            title="Nothing playing", description="The player is currently idle.",
            context="Status")),
        ("Warning", EmbedFactory.warning(
            title="Track skipped",
            description="Skipping **Some Song**. YouTube blocked the request (HTTP 403).",
            context="Playback")),
        ("Error (with hint and quip)", EmbedFactory.error(
            message="Could not find a confident YouTube match for 'Some Rare Song'.",
            user_hint="Try searching with the song title and artist, or paste a YouTube link.",
            title="No match found", context="System")),
        ("Error (plain, no quip)", EmbedFactory.error(
            message="You must be in a voice channel to use music commands.",
            user_hint="Join a voice channel and try again.", title="Voice channel needed",
            include_quip=False)),
        ("Ping (fast)", EmbedFactory.ping(
            gateway_ms=48.2, rest_ms=71.5, db_ms=0.8, voice_ms=32.0, voice_avg_ms=35.1,
            resolver_ms=1804.0)),
        ("Ping (slow)", EmbedFactory.ping(gateway_ms=520.0, rest_ms=610.0, db_ms=2.0)),
        ("Usage", EmbedFactory.usage(
            top_users=[(1, 120, 88), (2, 64, 40), (3, 12, 5)],
            top_commands=[("play", 150), ("skip", 60), ("queue", 22)], period="Last 30 Days")),
        ("About", EmbedFactory.about(
            version="0.1.0", uptime_str="2d 3h 12m", host_os="Windows 11", owner_id=42,
            stack_info={"Python": "3.14.0", "discord.py": "2.7.1", "yt-dlp": "2026.8.19",
                        "ytmusicapi": "1.12.3", "FFmpeg": "2025-11-27"})),
        ("Diagnostics (healthy)", EmbedFactory.diag(
            diagnostics={"Python / discord.py": "3.14.0 / 2.7.1", "DAVE E2EE": "Enabled",
                         "YouTube Health": "Healthy", "Database Size": "0.31 MB"},
            recent_errors=[])),
        ("Diagnostics (needs attention)", EmbedFactory.diag(
            diagnostics={"Python / discord.py": "3.14.0 / 2.7.1", "DAVE E2EE": "Enabled",
                         "YouTube Health": "Degraded (3 failures)",
                         "yt-dlp Version": "2026.8.19 (37d old) (outdated)"},
            recent_errors=["/play: BotCheckError: YouTube blocked the request"], status="warn")),
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
    target = Path(args.out)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_page(build_samples()), encoding="utf-8")
    sys.stdout.write(f"wrote {target}\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run tests, lint, generate the preview**

Run: `.venv/Scripts/python.exe -m pytest tests/test_preview_embeds.py -q && .venv/Scripts/python.exe -m ruff format scripts tests && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe scripts/preview_embeds.py && .venv/Scripts/python.exe -m pytest -q`
Expected: preview tests pass; `wrote docs/preview/embeds.html`; full suite green.

- [ ] **Step 5: Commit**

```bash
git add scripts/preview_embeds.py tests/test_preview_embeds.py docs/preview/embeds.html
git commit -m "feat(ui): add an HTML preview of every embed for design review" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git tag -a checkpoint/phase3-look -m "Phase 3 part A: restyled builders and embed preview (awaiting owner look approval)"
```

- [ ] **Step 6: STOP: owner approves the look (controller step, not the implementer's)**

The controller loads the `artifact-design` skill, publishes `docs/preview/embeds.html` as a private Artifact (the file is already a fragment: `<meta charset>`, `<title>`, `<style>`, content), gives the owner the link, and asks them to check: the now-playing card (title link, artist line, progress bar, three fields), the queued card (position and ETA), the queue and recommendation lists, the import summaries, the new error shape (specific title, plain text, a Try field, quip in the footer), and the status colors on ping and diagnostics. Do not start Part B until the owner approves the look or requests changes (changes are Part A rework tasks against `embeds.py` and `theme.py`, each followed by regenerating the preview).

---

# PART B: Wiring (only after the owner approves the look)

### Task 6: Specific error titles: domain errors, error handler, views, meta

**Files:**
- Modify: `src/yokai/errors.py`
- Modify: `src/yokai/bot.py`
- Modify: `src/yokai/ui/views.py`
- Modify: `src/yokai/cogs/meta.py`
- Test: `tests/test_errors.py`, `tests/test_bot_wiring.py`

**Interfaces:**
- Consumes: `EmbedFactory.error(..., title=...)` (Task 2).
- Produces: `YokaiError.title: str` (class attribute, default `"That didn't work"`), overridden per subclass; `on_tree_error` renders `cause.title`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_errors.py` (put the two new imports, `import pytest` and `from yokai import errors as err_mod`, at the top of the file with the existing imports; a mid-file import fails ruff E402):

```python
import pytest

from yokai import errors as err_mod


@pytest.mark.parametrize(
    ("cls", "expected"),
    [
        (err_mod.NotFoundError, "Nothing found"),
        (err_mod.LoginRequiredError, "Login required"),
        (err_mod.UnavailableError, "Track unavailable"),
        (err_mod.BotCheckError, "YouTube blocked the request"),
        (err_mod.ExtractionTimeoutError, "Took too long"),
        (err_mod.UnsupportedError, "Not supported"),
        (err_mod.SpotifyResolutionError, "Spotify lookup failed"),
        (err_mod.VoiceChannelError, "Voice channel needed"),
    ],
)
def test_domain_errors_carry_a_specific_title(cls: type, expected: str) -> None:
    assert cls().title == expected


def test_special_domain_error_titles() -> None:
    assert err_mod.UnmatchedTrackError("Song X").title == "No match found"
    assert err_mod.VoicePermissionError("Connect", "General").title == "Missing permission"
    assert err_mod.VoiceError("boom").title == "Voice problem"
    assert err_mod.YokaiError("boom").title == "That didn't work"
```

Append to `tests/test_bot_wiring.py` (merge the new imports into the import block at the top of the file; a mid-file import fails ruff E402):

```python
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from yokai.errors import NotFoundError


@pytest.mark.asyncio
async def test_tree_error_uses_the_domain_error_title() -> None:
    bot = make_bot()
    interaction = MagicMock()
    interaction.command.qualified_name = "play"
    interaction.user.id = 7
    error = discord.app_commands.CommandInvokeError(MagicMock(), NotFoundError())
    with (
        patch.object(bot.db, "log_command", new=AsyncMock()),
        patch("yokai.bot.send", new=AsyncMock()) as sent,
    ):
        await bot.on_tree_error(interaction, error)
    embed = sent.await_args.kwargs["embed"]
    assert embed.title == "❌ Nothing found"
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_tree_error_for_unexpected_exceptions_is_generic_but_specific_enough() -> None:
    bot = make_bot()
    interaction = MagicMock()
    interaction.command.qualified_name = "play"
    interaction.user.id = 7
    error = discord.app_commands.CommandInvokeError(MagicMock(), RuntimeError("kaboom"))
    with (
        patch.object(bot.db, "log_command", new=AsyncMock()),
        patch("yokai.bot.send", new=AsyncMock()) as sent,
    ):
        await bot.on_tree_error(interaction, error)
    embed = sent.await_args.kwargs["embed"]
    assert embed.title == "❌ Something broke"
    assert "kaboom" not in (embed.description or "")
```

(`pytest` is already imported at the top of `tests/test_bot_wiring.py`; if the new imports duplicate existing ones, merge them and let `ruff --fix` sort them.)

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_errors.py tests/test_bot_wiring.py -q`
Expected: FAIL (`AttributeError: ... has no attribute 'title'`).

- [ ] **Step 3: Implement**

`src/yokai/errors.py`: add `title: str = "That didn't work"` as the first statement after the docstring of `YokaiError`, and one line `title = "..."` right after the docstring of each subclass:

| class | `title` |
|---|---|
| `NotFoundError` | `"Nothing found"` |
| `LoginRequiredError` | `"Login required"` |
| `UnavailableError` | `"Track unavailable"` |
| `BotCheckError` | `"YouTube blocked the request"` |
| `ExtractionTimeoutError` | `"Took too long"` |
| `UnsupportedError` | `"Not supported"` |
| `SpotifyResolutionError` | `"Spotify lookup failed"` |
| `UnmatchedTrackError` | `"No match found"` |
| `VoiceError` | `"Voice problem"` |
| `VoicePermissionError` | `"Missing permission"` |
| `VoiceChannelError` | `"Voice channel needed"` |

`src/yokai/bot.py`, in `on_tree_error`: in the two `EmbedFactory.error(...)` calls add `title=cause.title,` to the first (the `YokaiError` branch) and `title="Something broke",` to the second (unexpected error branch).

`src/yokai/ui/views.py`: add a `title=` to each `EmbedFactory.error(...)` call, keeping all other arguments:
- `BaseView.interaction_check` (message "You do not have permission to interact with this control.") -> `title="Not your control",`
- `BaseView.on_error` -> `title="Something broke",`
- `RecommendView.interaction_check` -> `title="Not your control",`
- `DiagView` (two calls inside `update_ytdlp_button`: "Failed to update yt-dlp: ..." and "Failed to execute pip update: ...") -> `title="Update failed",`

`src/yokai/cogs/meta.py`: in `diag`, the access-denied `EmbedFactory.error(...)` -> add `title="Owner only",`.

- [ ] **Step 4: Verify and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_errors.py tests/test_bot_wiring.py tests/test_utility.py -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q`
Expected: all green.

```bash
git add src tests
git commit -m "feat(ui): specific titles for every error embed" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: Music cog: now playing, queued, imports, queue pages and error titles

**Files:**
- Modify: `src/yokai/cogs/music.py`
- Modify: `src/yokai/ui/views.py`
- Modify: `tests/test_music_cog.py`
- Create: `tests/test_queue_paginator.py`

**Interfaces:**
- Consumes: `EmbedFactory.now_playing/queued/queue_page/import_summary/error` (Tasks 2-3); `Track.url`, `Track.is_pending_match`, `Track.origin`.
- Produces: module functions `_track_url(track) -> str | None` and `_eta_seconds(player, position) -> int` in `yokai.cogs.music`; `QueuePaginator(queue, author_id, initial_page=1, bot_avatar_url=None, timeout=180.0, current_track=None)`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_music_cog.py`, in `test_playlist_on_idle_player_queues_every_track_before_playing` replace everything from the line `embed = send.await_args.kwargs["embed"]` to the end of the function (the embed assignment and the two assertions after it) with:

```python
    embed = send.await_args.kwargs["embed"]
    assert embed.title == "📑 Playlist loaded"
    fields = {f.name: f.value for f in embed.fields if f.name != "​"}
    assert fields == {"Loaded": "`3`", "Queued": "`2`", "Now playing": "Song 0"}
```

and append these tests (they reuse `make_interaction`, `MusicCog`, `MusicQueue`, `PlayerState`, `Track` from the top of the file; add `from yokai.cogs.music import _eta_seconds, _track_url` to the imports):

```python
def make_cog_and_player(state: PlayerState = PlayerState.IDLE) -> tuple[MusicCog, MagicMock]:
    player = MagicMock()
    player.state = state
    player.voice_client = None
    player.current_track = None
    player.elapsed_seconds = 0
    player.queue = MusicQueue(max_size=50)
    player.connect = AsyncMock()
    player.warm_stream = MagicMock()
    player.maybe_prefetch = MagicMock()
    player.resolver.resolve_query = AsyncMock()

    async def play_next() -> None:
        player.queue.pop_next()

    player.play_next = AsyncMock(side_effect=play_next)
    bot = MagicMock()
    bot.user = None
    bot.config.max_playlist_tracks = 100
    bot.config.max_queue_size = 50
    bot.get_player.return_value = player
    return MusicCog(bot), player


def test_track_url_hides_pending_spotify_imports() -> None:
    real = Track(video_id="abc12345678", title="T")
    pending = Track(video_id="sp:xyz", title="T", is_pending_match=True)
    assert _track_url(real) == "https://www.youtube.com/watch?v=abc12345678"
    assert _track_url(pending) is None


def test_pending_spotify_track_gets_no_link() -> None:
    pending = Track(video_id="sp:xyz", title="T", is_pending_match=True)
    assert _track_url(pending) is None


def test_eta_counts_remaining_current_plus_tracks_ahead() -> None:
    player = MagicMock()
    player.current_track = Track(video_id="cur00000001", title="C", duration_s=200)
    player.elapsed_seconds = 50
    player.queue = MusicQueue(max_size=10)
    for i in range(3):
        player.queue.add(Track(video_id=f"q{i}000000000", title=f"Q{i}", duration_s=100))
    assert _eta_seconds(player, 1) == 150
    assert _eta_seconds(player, 3) == 150 + 200
    player.current_track = None
    assert _eta_seconds(player, 2) == 100


@pytest.mark.asyncio
async def test_single_track_idle_play_shows_a_linked_now_playing_card() -> None:
    cog, player = make_cog_and_player(PlayerState.IDLE)
    track = Track(
        video_id="dQw4w9WgXcQ", title="Never Gonna", artist="Rick", duration_s=213,
        requester_id=111, origin="search",
    )
    player.resolver.resolve_query.return_value = track
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.play.callback(cog, make_interaction(), query="never gonna")
    embed = send.await_args.kwargs["embed"]
    assert embed.title == "▶️ Never Gonna"
    assert embed.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    fields = {f.name: f.value for f in embed.fields if f.name != "​"}
    assert fields["Requested by"] == "<@111>"
    assert fields["Source"] == "Search"


@pytest.mark.asyncio
async def test_queued_track_shows_position_eta_and_requester() -> None:
    cog, player = make_cog_and_player(PlayerState.PLAYING)
    player.current_track = Track(video_id="cur00000001", title="Current", duration_s=200)
    player.elapsed_seconds = 50
    track = Track(
        video_id="dQw4w9WgXcQ", title="Next Up", artist="Rick", duration_s=213,
        requester_id=111, origin="link",
    )
    player.resolver.resolve_query.return_value = track
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.play.callback(cog, make_interaction(), query="next up")
    embed = send.await_args.kwargs["embed"]
    assert embed.title == "📑 Queued"
    fields = {f.name: f.value for f in embed.fields if f.name != "​"}
    assert fields["Position"] == "`#1`"
    assert fields["Plays in"] == "`~2m 30s`"
    assert fields["Requested by"] == "<@111>"
    assert fields["Source"] == "YouTube link"
    assert "[Next Up](https://www.youtube.com/watch?v=dQw4w9WgXcQ)" in embed.description


@pytest.mark.asyncio
async def test_queue_full_error_has_a_specific_title() -> None:
    cog, player = make_cog_and_player(PlayerState.PLAYING)
    player.queue = MusicQueue(max_size=0)
    player.resolver.resolve_query.return_value = Track(video_id="dQw4w9WgXcQ", title="T")
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.play.callback(cog, make_interaction(), query="something")
    assert send.await_args.kwargs["embed"].title == "❌ Queue is full"


@pytest.mark.asyncio
async def test_unsupported_link_has_a_specific_title_and_does_not_join_voice() -> None:
    cog, player = make_cog_and_player(PlayerState.IDLE)
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.play.callback(cog, make_interaction(), query="https://evil.example/x")
    assert send.await_args.kwargs["embed"].title == "❌ Unsupported link"
    assert send.await_args.kwargs["ephemeral"] is True
    player.connect.assert_not_called()


@pytest.mark.asyncio
async def test_nowplaying_command_links_and_labels_the_source() -> None:
    cog, player = make_cog_and_player(PlayerState.PLAYING)
    player.current_track = Track(
        video_id="dQw4w9WgXcQ", title="Now", artist="Rick", duration_s=100,
        requester_id=5, origin="recommendation",
    )
    player.elapsed_seconds = 30
    with patch("yokai.cogs.music.send", new=AsyncMock()) as send:
        await cog.nowplaying.callback(cog, make_interaction())
    embed = send.await_args.kwargs["embed"]
    assert embed.url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"
    fields = {f.name: f.value for f in embed.fields if f.name != "​"}
    assert fields["Source"] == "Recommendation"
```

Create `tests/test_queue_paginator.py`:

```python
"""Tests for the queue paginator's embed (now playing header, runtime, pending imports)."""

from __future__ import annotations

import pytest

from yokai.music.models import LoopMode, MusicQueue, Track
from yokai.ui.views import QueuePaginator


def make_queue(count: int) -> MusicQueue:
    queue = MusicQueue(max_size=100)
    for i in range(count):
        queue.add(Track(video_id=f"vid{i:08d}", title=f"Song {i}", artist="A", duration_s=120))
    return queue


@pytest.mark.asyncio
async def test_paginator_embed_shows_now_playing_runtime_and_loop() -> None:
    queue = make_queue(12)
    queue.loop_mode = LoopMode.QUEUE
    current = Track(video_id="cur00000001", title="Current", duration_s=200)
    view = QueuePaginator(queue=queue, author_id=1, current_track=current)
    embed = view.build_embed()
    assert "[Current](https://www.youtube.com/watch?v=cur00000001)" in embed.description
    assert "**12** up next" in embed.description
    assert "24m 00s total" in embed.description
    assert "loop queue" in embed.description
    assert embed.footer.text == "Yokai · Page 1/2"


@pytest.mark.asyncio
async def test_paginator_pending_import_current_track_has_no_link() -> None:
    queue = make_queue(1)
    pending = Track(video_id="sp:abc", title="Imported", is_pending_match=True)
    embed = QueuePaginator(queue=queue, author_id=1, current_track=pending).build_embed()
    assert "**Imported**" in embed.description
    assert "youtube.com" not in embed.description.splitlines()[0]


@pytest.mark.asyncio
async def test_paginator_without_current_track_or_tracks() -> None:
    embed = QueuePaginator(queue=make_queue(0), author_id=1).build_embed()
    assert "The queue is empty." in embed.description
    assert "Now playing" not in embed.description
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_music_cog.py tests/test_queue_paginator.py -q`
Expected: FAIL (`ImportError: cannot import name '_eta_seconds'`).

- [ ] **Step 3: Implement**

`src/yokai/ui/views.py`, `QueuePaginator`:
- constructor signature: add `current_track: Optional[Track] = None,` as the last parameter (after `timeout`), and store `self.current_track = current_track` (before `self._update_buttons()`).
- replace `build_embed` with:

```python
    def build_embed(self) -> discord.Embed:
        tracks, total_pages, total_tracks = self.queue.get_page(self.current_page)
        now_playing = None
        if self.current_track is not None:
            current = self.current_track
            url = None if current.is_pending_match else current.url
            now_playing = (current.title, url, current.duration_s)
        runtime = sum(t.duration_s for t in self.queue.upcoming if t.duration_s) or None
        return EmbedFactory.queue_page(
            tracks=[(pos, t.title, t.artist, t.duration_s) for pos, t in tracks],
            page=self.current_page,
            total_pages=total_pages,
            total_tracks=total_tracks,
            loop_mode=self.queue.loop_mode.value,
            bot_avatar_url=self.bot_avatar_url,
            now_playing=now_playing,
            total_runtime_s=runtime,
        )
```

`src/yokai/cogs/music.py`:

1. Add these two functions immediately above `class MusicCog(commands.Cog, name="Music"):`:

```python
def _track_url(track: Track) -> Optional[str]:
    """YouTube page URL for embeds; None for imported tracks that are not matched yet."""
    return None if track.is_pending_match else track.url


def _eta_seconds(player: GuildPlayer, position: int) -> int:
    """Seconds until the track at 1-based queue `position` starts playing."""
    remaining = 0
    current = player.current_track
    if current is not None and current.duration_s > 0:
        remaining = max(current.duration_s - player.elapsed_seconds, 0)
    ahead = sum(t.duration_s for t in list(player.queue.upcoming)[: max(position - 1, 0)])
    return remaining + ahead
```

2. Give each error embed in `play()` and `recommend()`/`remove()` a specific title. For each line below, keep the line and insert the `title=` line directly above it with the same indentation:
   - `message=classified.error_message or "Unsupported input.",` -> `title="Unsupported link",`
   - `message=f"Could not find a confident YouTube match for '{meta.title}'.",` -> `title="No match found",`
   - `message=f"No playable tracks found in Spotify {entity_type}.",` -> `title="Nothing to play",`
   - `message="No playable audio tracks were found.",` -> `title="Nothing to play",`
   - `message=f"Queue limit reached ({self.bot.config.max_queue_size} tracks).",` -> `title="Queue is full",`
   - `message=str(err),` (in `remove`) -> `title="No such position",`
   - `message=f"No playable tracks in Spotify {e_type}.",` -> `title="Nothing to seed from",`
   - `message=classified.error_message or "Unsupported seed input.",` -> `title="Unsupported seed",`

3. In `play()` replace the `EmbedFactory.now_playing(...)` call in the single-track idle branch with:

```python
                embed = EmbedFactory.now_playing(
                    title=track.title,
                    artist=track.artist,
                    duration_s=track.duration_s,
                    position_s=0,
                    requester_mention=f"<@{interaction.user.id}>",
                    thumbnail_url=track.thumbnail_url,
                    bot_avatar_url=avatar_url,
                    url=_track_url(track),
                    source=track.origin,
                )
```

4. Replace the `EmbedFactory.queued(...)` call (the one in the `else:` after `player.maybe_prefetch()` / `pos = len(player.queue)`) with:

```python
                    embed = EmbedFactory.queued(
                        title=track.title,
                        position=pos,
                        artist=track.artist,
                        duration_s=track.duration_s,
                        bot_avatar_url=avatar_url,
                        url=_track_url(track),
                        eta_s=_eta_seconds(player, pos),
                        requester_mention=f"<@{interaction.user.id}>",
                        source=track.origin,
                        thumbnail_url=track.thumbnail_url,
                    )
```

5. Replace the whole playlist branch, from `# Playlist queueing: queue everything first` through the final `await send(interaction, embed=embed)` of `play()` (that is the entire `else:` block that handles `is_playlist`), with:

```python
        else:
            # Playlist queueing: queue everything first, then start playback if idle, so the
            # worker moves on to the next track if the first one fails to load.
            first = tracks_to_queue[0]
            now_playing_title: Optional[str] = None
            if player.state == PlayerState.IDLE:
                total_loaded = player.queue.add_many(tracks_to_queue)
                await player.play_next()

                player.maybe_prefetch()
                queued_count = max(total_loaded - 1, 0)
                now_playing_title = first.title
            else:
                total_loaded = player.queue.add_many(tracks_to_queue)
                player.maybe_prefetch()
                queued_count = total_loaded

            if collection_info:
                c_type, c_name, c_count, c_total, c_partial = collection_info
                embed = EmbedFactory.import_summary(
                    kind=f"Spotify {c_type}",
                    name=c_name,
                    loaded=c_count,
                    total=c_total,
                    queued=queued_count,
                    now_playing=now_playing_title,
                    partial=c_partial,
                    bot_avatar_url=avatar_url,
                )
            else:
                embed = EmbedFactory.import_summary(
                    kind="Playlist",
                    name=None,
                    loaded=total_loaded,
                    total=None,
                    queued=queued_count,
                    now_playing=now_playing_title,
                    bot_avatar_url=avatar_url,
                )
            await send(interaction, embed=embed)
```

6. In `nowplaying`, replace the `EmbedFactory.now_playing(...)` call with:

```python
        embed = EmbedFactory.now_playing(
            title=track.title,
            artist=track.artist,
            duration_s=track.duration_s,
            position_s=player.elapsed_seconds,
            requester_mention=f"<@{track.requester_id}>",
            thumbnail_url=track.thumbnail_url,
            bot_avatar_url=avatar_url,
            url=_track_url(track),
            source=track.origin,
        )
```

7. In `queue`, add `current_track=player.current_track,` to the `QueuePaginator(...)` call.

- [ ] **Step 4: Verify and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_music_cog.py tests/test_queue_paginator.py tests/test_recommend.py -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q`
Expected: all green. If `tests/test_recommend.py` asserts the recommendation embed text, update it to the new layout.

```bash
git add src tests
git commit -m "feat(ui): linked now-playing cards, queue ETA, import summaries and specific errors" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Diagnostics wording, the one-emoji sweep, and the persona failure notice

**Files:**
- Modify: `src/yokai/cogs/meta.py`
- Modify: `src/yokai/ui/views.py`
- Modify: `tests/test_utility.py`
- Create: `tests/test_one_emoji_rule.py`

**Interfaces:**
- Consumes: `EmbedFactory.diag(..., status=...)` (Task 4).
- Produces: `/diag` passes `status="warn"` when YouTube is degraded, yt-dlp is outdated, or DAVE is missing; no user-visible string in `meta.py` or `views.py` carries a second emoji.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_one_emoji_rule.py`:

```python
"""Guards the persona rule: at most one emoji per embed, including hand-written text."""

from __future__ import annotations

import re
from pathlib import Path

from tests.embed_helpers import EMOJI_RE

SRC = Path("src/yokai")
CHECKED = ["cogs/meta.py", "cogs/music.py", "ui/views.py", "music/player.py", "bot.py"]


def test_feature_modules_contain_no_emoji_in_embed_text() -> None:
    """Emoji live in theme.Emoji only; feature code must not hand-write them into strings.

    Button labels/emoji arguments (`emoji="..."`) are components, not embed text, and are exempt.
    """
    offenders: list[str] = []
    for relative in CHECKED:
        for number, line in enumerate((SRC / relative).read_text(encoding="utf-8").splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#") or "emoji=" in stripped:
                continue
            if EMOJI_RE.search(line):
                offenders.append(f"{relative}:{number}: {stripped}")
    assert not offenders, "Emoji in feature code:\n" + "\n".join(offenders)


def test_pattern_sanity() -> None:
    assert re.search(EMOJI_RE, "⚠️ text")
    assert not re.search(EMOJI_RE, "plain — text · ● ━")
```

Append to `tests/test_utility.py` (it already imports `AsyncMock`, `MagicMock`, `patch`, `pytest`, `discord`, `MetaCog`):

```python
@pytest.mark.asyncio
async def test_diag_marks_attention_with_status_color_not_extra_emoji() -> None:
    bot = MagicMock()
    bot.config.owner_id = 42
    bot.config.db_path = ":memory:"
    bot.user = None
    bot.error_ring_buffer = []
    bot.resolver.is_degraded = True
    bot.resolver.consecutive_failures = 3
    bot.resolver.error_ring_buffer = []
    bot.health.davey_available = True
    bot.health.davey_version = "0.1.6"
    bot.health.ytdlp_version = "2026.8.19"
    bot.health.ytdlp_age_days = 40
    bot.health.ytdlp_outdated = True
    bot.health.ffmpeg_path = "ffmpeg"
    bot.health.deno_path = "deno"
    bot.spotify.get_provider_status.return_value = {
        "official_configured": False,
        "official_circuit_available": True,
        "official_failures": 0,
        "scraper_enabled": True,
        "scraper_circuit_available": True,
        "scraper_failures": 0,
    }
    cog = MetaCog(bot)
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.defer = AsyncMock()
    with patch("yokai.cogs.meta.send", new=AsyncMock()) as sent:
        await cog.diag.callback(cog, interaction)
    embed = sent.await_args.kwargs["embed"]
    assert embed.title.startswith("⚠️")
    values = " ".join(f.value for f in embed.fields)
    assert "(outdated)" in values
    assert "⚠️" not in values
```

- [ ] **Step 2: Run to verify failure**

Run: `.venv/Scripts/python.exe -m pytest tests/test_one_emoji_rule.py tests/test_utility.py -q`
Expected: FAIL (`meta.py` contains `⚠️ Outdated`; `views.py` contains `⚠️ **Restart Required:**`; `diag` does not pass `status`).

- [ ] **Step 3: Implement**

`src/yokai/cogs/meta.py`, in `diag`:
- replace `ytdlp_age_str += " ⚠️ Outdated"` with `ytdlp_age_str += " (outdated)"`;
- immediately above `embed = EmbedFactory.diag(`, add:

```python
        needs_attention = (
            getattr(self.bot.resolver, "is_degraded", False) is True
            or self.bot.health.ytdlp_outdated is True
            or self.bot.health.davey_available is False
        )
```

- add `status="warn" if needs_attention else "ok",` as an argument of `EmbedFactory.diag(...)`.

`src/yokai/ui/views.py`, in `DiagView.update_ytdlp_button`, replace the success description string:

```python
                        description=(
                            "yt-dlp was successfully updated to the latest release.\n\n"
                            "**Restart Required:** Please restart the Yokai process "
                            "to load the updated package into memory."
                        ),
```

(the `⚠️ ` before `**Restart Required:**` is removed; the existing test asserts the words `Restart Required`).

- [ ] **Step 4: Verify and commit**

Run: `.venv/Scripts/python.exe -m pytest tests/test_one_emoji_rule.py tests/test_utility.py -q && .venv/Scripts/python.exe -m ruff format src tests && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m pytest -q`
Expected: all green. If `tests/test_one_emoji_rule.py` reports another hand-written emoji in a checked module, remove it (the emoji belongs in `theme.Emoji`) unless it is a button `emoji=` argument.

```bash
git add src tests
git commit -m "feat(ui): status-colored diagnostics and a guard for the one-emoji rule" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 9: Docs, QA rows, final verification, checkpoint

**Files:**
- Modify: `SPEC.md` (section 5), `QA.md`, `README.md` (only if it shows an old screenshot or embed text), `AGENTS.md` (no change expected)
- Create: none

- [ ] **Step 1: Update the docs**

1. `SPEC.md` section 5: after the requirements list, add a short subsection `### Layout (v2)` describing: specific titles (no "Error"/"Success"); the error embed shape (title, plain message, a `Try` field, quip in the footer); the now-playing card (linked title, artist line, progress bar with `●`, fields Duration / Requested by / Source); the queued card (Position, Plays in, Duration, Requested by, Source); queue and recommendation lists (`` ` 1.` **Title** — Artist · `3:41` ``), imports summarised in Loaded / Queued / Now playing fields; grid padding for inline fields; `escape_md` for all user-supplied track text; the preview tool `scripts/preview_embeds.py`. Keep it under 20 lines.
2. `QA.md`: read the QA execution matrix table at the top and add rows in the same format for these checks: (a) `/play` a song by name while idle: the card has a clickable title, artist, progress bar, Duration / Requested by / Source, and one emoji; (b) `/play` a second song while one plays: the Queued card shows Position and "Plays in" close to the real wait; (c) `/queue` with more than 10 tracks: header with Now playing, total runtime and loop state, ten compact lines, Prev/Next work; (d) `/queue` when empty; (e) `/play` a Spotify playlist: the import summary shows Loaded / Queued / Now playing; (f) trigger an error (unsupported link, `/play` while not in voice): specific title, plain text, a Try field, quip only in the footer; (g) a track whose title contains markdown or a bracketed link (search for such a title if you can): it renders as plain text, never a live link; (h) `/ping`, `/about`, `/diag`: aligned three-column grids, and `/diag` is amber when YouTube is degraded or yt-dlp is outdated, green otherwise. Update the "Automated Test Baseline" numbers to the real current counts.
3. Regenerate the preview so the committed file matches the final builders: `.venv/Scripts/python.exe scripts/preview_embeds.py`.

- [ ] **Step 2: Final verification**

Run: `.venv/Scripts/python.exe -m pytest -q && .venv/Scripts/python.exe -m ruff check . && .venv/Scripts/python.exe -m ruff format --check .`
Expected: all tests pass (191 baseline plus this phase's new tests), ruff clean. Also confirm no raw text sends slipped in: `tests/test_code_standards.py` is part of the suite.

- [ ] **Step 3: Commit and tag**

```bash
git add SPEC.md QA.md README.md docs/preview/embeds.html
git commit -m "docs: document the embed layout, add visual QA rows, refresh the preview" -m "Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
git tag -a checkpoint/phase3-restyle -m "Phase 3 complete: embed restyle wired through every command"
```

- [ ] **Step 4: STOP for owner review**

Report: the before/after look (link the final preview Artifact again), the list of changed titles, what was verified (tests, preview, the one-emoji guard) and what was not (how the embeds actually render in Discord, thumbnails, mobile layout). Point the owner to the new QA rows. Do not start Phase 4 until they approve.
