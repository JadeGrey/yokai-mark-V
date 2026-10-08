# Multiplayer Phase M1: Command Groups Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move every admin command under a `/admin` group and create the `/owner` group, so later phases can add `/admin set_multiplayer` and owner tools in the right place.

**Architecture:** A new `cogs/admin.py` holds an `app_commands.Group` named `admin` as a class attribute of `AdminCog`, with `economy` (was `/economy-admin`) and `diag` (was `/diag`) as its subcommands. A new `cogs/owner.py` defines the `owner` group and registers it only once it has a subcommand (Discord rejects empty groups). The old top-level commands are deleted, and every user-facing mention of `/diag` is updated.

**Tech Stack:** Python 3.12, discord.py 2.7.1 (`app_commands.Group` inside a `commands.Cog`), pytest with `asyncio_mode = "auto"`, ruff (line length 100).

**Spec:** `docs/superpowers/specs/2026-10-07-multiplayer-tables-design.md` (§3 phasing, §4 command groups). `set_multiplayer` and `unset_multiplayer` belong to M2, not this plan.

## Global Constraints

- Slash commands only, guild-scoped; no privileged intents (`AGENTS.md`).
- Admin gate is `bot.moderation.is_admin(user_id)` (owner or adminlist member, a user on both lists is not an admin); denial is the existing plain ephemeral "Admins only" embed through `EmbedFactory.error` (`include_quip=False`).
- Every user-visible message goes through `ui/embeds.py`; errors are ephemeral. No raw `send_message("text")`.
- `from __future__ import annotations`, full type hints, `logging` (no `print`), no bare `except`.
- No new dependency. No schema change in this phase.
- The old `/economy-admin` and `/diag` are removed outright (breaking rename); tests, `README.md`, `SPEC.md`, `TASKS.md` are updated.
- Work directly on `main`; create the checkpoint tag first (owner workflow); commit per task; end with `pytest -q`, `ruff check .`, `ruff format --check .` clean, then stop for owner review.

## Review Focus

- A non-admin running `/admin economy` or `/admin diag` is refused ephemerally and nothing is written or computed (tests moved with the commands).
- `/admin economy` targeting a bot account must not create a wallet (moved test).
- `/admin diag` buttons answer only to the admin who ran it (moved test).
- `/owner` must not be registered while it has no subcommands (Discord rejects an empty group); a test pins this.
- The old top-level `diag` and `economy-admin` must be gone from the command tree, and every `/admin` subcommand description must fit Discord's 100-character limit (tree-shape test).
- Admin commands are logged by their qualified name (`admin diag`) and must never earn the music-command Renown reward.

---

## File Structure

| File | Responsibility |
|---|---|
| `src/yokai/cogs/admin.py` (create) | `AdminCog` with the `admin` group: `economy`, `diag` |
| `src/yokai/cogs/owner.py` (create) | `OwnerCog` with the `owner` group; conditional `setup` |
| `src/yokai/cogs/meta.py` (modify) | remove `diag` and its now-unused imports |
| `src/yokai/cogs/economy.py` (modify) | remove `economy_admin` |
| `src/yokai/bot.py` (modify) | load the two new extensions; update the `/diag` hint |
| `src/yokai/errors.py`, `health.py`, `music/player.py`, `music/spotify/orchestrator.py` (modify) | `/diag` text becomes `/admin diag` |
| `tests/test_admin_cog.py` (create) | moved and adapted admin and diag tests |
| `tests/test_owner_cog.py` (create) | owner group registration rule |
| `tests/test_command_tree.py` (create) | final tree shape |
| `tests/test_economy_cog.py`, `tests/test_utility.py`, `tests/test_bot_wiring.py` (modify) | drop moved tests; assert the new extensions load |
| `README.md`, `SPEC.md`, `TASKS.md` (modify) | document the rename and add the phase |

---

### Task 0: Checkpoint tag

- [ ] **Step 1: Confirm a clean tree on main and tag it**

Run (PowerShell, repo root):
```powershell
git status --short; git branch --show-current
git tag -a checkpoint/pre-multiplayer-m1 -m "Before multiplayer M1 (command groups)"
git tag | Select-String multiplayer
```
Expected: no modified files, branch `main`, the tag listed.

---

### Task 1: `AdminCog` with `/admin economy`

**Files:**
- Create: `src/yokai/cogs/admin.py`
- Create: `tests/test_admin_cog.py`
- Modify: `src/yokai/cogs/economy.py` (remove `economy_admin`, lines 153-190, and unused imports)
- Modify: `tests/test_economy_cog.py` (remove the four `economy_admin` tests)

**Interfaces:**
- Produces: `AdminCog(bot)` with class attribute `admin: app_commands.Group`, subcommand `AdminCog.economy(interaction, user: discord.Member, amount: int)`, and helpers `_avatar_url() -> Optional[str]`, `_send_domain_error(interaction, err) -> None`, `_deny_unless_admin(interaction, hint: str) -> bool` (returns True after sending the denial). Task 2 adds `diag` to the same class and reuses the helpers.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_admin_cog.py`:
```python
"""Tests for the /admin command group."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest

from tests.moderation_helpers import fake_moderation
from yokai.cogs.admin import AdminCog


def make_bot() -> MagicMock:
    bot = MagicMock()
    bot.user = None
    bot.config.owner_id = 999
    bot.moderation = fake_moderation(owner_id=999, admins={555})
    bot.config.economy_welcome_bonus = 200
    return bot


def make_interaction(user_id: int = 111) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    user = MagicMock(spec=discord.Member)
    user.id = user_id
    user.bot = False
    interaction.user = user
    interaction.response.defer = AsyncMock()
    return interaction


def make_target(user_id: int = 222, *, bot: bool = False) -> MagicMock:
    target = MagicMock(spec=discord.Member)
    target.id = user_id
    target.bot = bot
    return target


def test_the_admin_group_is_named_admin() -> None:
    assert AdminCog.admin.name == "admin"


@pytest.mark.asyncio
async def test_economy_refuses_non_admins() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock()
    cog = AdminCog(bot)
    interaction = make_interaction(111)  # neither owner (999) nor admin (555)

    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.economy.callback(cog, interaction, user=make_target(), amount=100)

    bot.db.admin_adjust_wallet.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_economy_allows_the_owner() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock(return_value=(100, 300))
    cog = AdminCog(bot)
    interaction = make_interaction(999)

    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.economy.callback(cog, interaction, user=make_target(), amount=100)

    bot.db.admin_adjust_wallet.assert_awaited_once_with(222, 100, welcome_bonus=200)
    assert sent.await_args.kwargs["ephemeral"] is True


@pytest.mark.asyncio
async def test_economy_allows_an_adminlist_member() -> None:
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock(return_value=(100, 300))
    cog = AdminCog(bot)
    interaction = make_interaction(555)  # admin, not the owner

    with patch("yokai.cogs.admin.send", new=AsyncMock()):
        await cog.economy.callback(cog, interaction, user=make_target(), amount=100)

    bot.db.admin_adjust_wallet.assert_awaited_once()


@pytest.mark.asyncio
async def test_economy_rejects_a_bot_target() -> None:
    """Review Focus: /admin economy must not be able to create a wallet for a bot."""
    bot = make_bot()
    bot.db.admin_adjust_wallet = AsyncMock()
    cog = AdminCog(bot)
    interaction = make_interaction(999)

    with patch("yokai.cogs.admin.send", new=AsyncMock()) as sent:
        await cog.economy.callback(cog, interaction, user=make_target(bot=True), amount=100)

    bot.db.admin_adjust_wallet.assert_not_awaited()
    interaction.response.defer.assert_not_awaited()
    assert sent.await_args.kwargs["ephemeral"] is True
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `python -m pytest tests/test_admin_cog.py -q`
Expected: collection ERROR, `ModuleNotFoundError: No module named 'yokai.cogs.admin'`.

- [ ] **Step 3: Create `src/yokai/cogs/admin.py`**

```python
"""Admin-only slash commands, grouped under /admin."""

from __future__ import annotations

from typing import TYPE_CHECKING, Optional

import discord
from discord import app_commands
from discord.ext import commands

from yokai.errors import InvalidPaymentError
from yokai.ui.embeds import EmbedFactory, send

if TYPE_CHECKING:
    from yokai.bot import YokaiBot


class AdminCog(commands.Cog, name="Admin"):
    """Tools for the owner and the adminlist, all under the /admin group."""

    admin = app_commands.Group(name="admin", description="Admin-only tools.")

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot

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


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint."""
    await bot.add_cog(AdminCog(bot))
```

- [ ] **Step 4: Run the tests to verify they pass**

Run: `python -m pytest tests/test_admin_cog.py -q`
Expected: 5 passed. If `cog.economy.callback` raises `AttributeError`, print `type(cog.economy)` and adapt the call (the earlier cogs call `cog.<command>.callback(cog, ...)` and this must too).

- [ ] **Step 5: Remove the old command and its tests**

In `src/yokai/cogs/economy.py` delete the `economy_admin` method (the `@app_commands.command(name="economy-admin", ...)` block through its final `await send(...)`), then run `python -m ruff check src/yokai/cogs/economy.py` and delete any import it now flags as unused (likely `InvalidPaymentError` only if nothing else uses it; keep it if `pay` still does).

In `tests/test_economy_cog.py` delete these four tests: `test_economy_admin_refuses_non_owners`, `test_economy_admin_allows_the_owner`, `test_economy_admin_rejects_a_bot_target`, `test_economy_admin_allows_an_adminlist_member`.

- [ ] **Step 6: Run the suite and lint**

Run: `python -m pytest -q; python -m ruff check .; python -m ruff format --check .`
Expected: all green. Fix anything flagged (`ruff format .` for layout).

- [ ] **Step 7: Commit**

```powershell
git add src/yokai/cogs/admin.py src/yokai/cogs/economy.py tests/test_admin_cog.py tests/test_economy_cog.py
git commit -m "feat(admin): add the /admin group with /admin economy" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 2: Move `/diag` to `/admin diag`

**Files:**
- Modify: `src/yokai/cogs/admin.py` (add `diag`)
- Modify: `src/yokai/cogs/meta.py` (remove `diag`, lines 179-283, and unused imports)
- Modify: `tests/test_admin_cog.py` (add the diag tests)
- Modify: `tests/test_utility.py` (remove the moved tests)

**Interfaces:**
- Consumes: `AdminCog._deny_unless_admin(interaction, hint)`, `AdminCog._avatar_url()` from Task 1.
- Produces: `AdminCog.diag(interaction)`, patchable as `yokai.cogs.admin.send`.

- [ ] **Step 1: Add the failing tests**

Append to `tests/test_admin_cog.py`, and add `from yokai.theme import COLOR_WARNING` and `from yokai.ui.views import DiagView` to its imports:
```python
def make_diag_bot(*, owner_id: int = 123456789, admins: set[int] | None = None) -> MagicMock:
    bot = MagicMock()
    bot.config.owner_id = owner_id
    bot.moderation = fake_moderation(owner_id=owner_id, admins=admins or set())
    bot.config.db_path = ":memory:"
    bot.user.display_avatar.url = "https://example.com/avatar.png"
    bot.spotify.get_provider_status.return_value = {
        "official_configured": True,
        "official_circuit_available": True,
        "official_failures": 0,
        "scraper_enabled": True,
        "scraper_circuit_available": True,
        "scraper_failures": 0,
    }
    bot.resolver.consecutive_failures = 0
    bot.resolver.is_degraded = False
    bot.health.davey_available = True
    bot.health.davey_version = "1.0.0"
    bot.health.ffmpeg_path = "C:/ffmpeg/ffmpeg.exe"
    bot.health.ffmpeg_version = "7.1"
    bot.health.deno_path = "C:/deno/deno.exe"
    bot.health.deno_version = "2.0.0"
    bot.health.ytdlp_version = "2026.03.01"
    bot.health.ytdlp_age_days = 5
    bot.health.ytdlp_outdated = False
    bot.error_ring_buffer = ["Error 1"]
    bot.resolver.error_ring_buffer = ["Error 2"]
    return bot


def make_followup_interaction(user_id: int) -> MagicMock:
    interaction = MagicMock()
    interaction.user.id = user_id
    interaction.response.defer = AsyncMock()
    interaction.response.is_done.return_value = True
    interaction.followup.send = AsyncMock()
    return interaction


@pytest.mark.asyncio
async def test_diag_denies_non_admins_with_the_admins_only_embed() -> None:
    bot = make_diag_bot()
    cog = AdminCog(bot)
    interaction = MagicMock()
    interaction.user.id = 999999999  # not the owner, not an admin
    interaction.response.is_done.return_value = False
    interaction.response.send_message = AsyncMock()

    await cog.diag.callback(cog, interaction)

    interaction.response.send_message.assert_awaited_once()
    kwargs = interaction.response.send_message.call_args[1]
    assert kwargs["ephemeral"] is True
    assert "Access denied" in (kwargs["embed"].description or "")
    assert "Yokai admins" in (kwargs["embed"].description or "")
    assert "Admins only" in (kwargs["embed"].title or "")


@pytest.mark.asyncio
async def test_diag_runs_for_the_owner_and_attaches_the_diag_view() -> None:
    bot = make_diag_bot()
    cog = AdminCog(bot)
    interaction = make_followup_interaction(123456789)

    await cog.diag.callback(cog, interaction)

    interaction.followup.send.assert_awaited_once()
    kwargs = interaction.followup.send.call_args[1]
    assert "Diagnostic Telemetry" in (kwargs["embed"].title or "")
    assert isinstance(kwargs.get("view"), DiagView)


@pytest.mark.asyncio
async def test_diag_runs_for_an_adminlist_member_and_locks_buttons_to_them() -> None:
    bot = make_diag_bot(admins={555})
    cog = AdminCog(bot)
    interaction = make_followup_interaction(555)  # admin, not the owner

    await cog.diag.callback(cog, interaction)

    view = interaction.followup.send.call_args[1]["view"]
    assert isinstance(view, DiagView)
    assert view.allowed_user_ids == {555}


@pytest.mark.asyncio
async def test_diag_marks_attention_with_status_color_not_extra_emoji() -> None:
    bot = make_diag_bot(owner_id=42)
    bot.user = None
    bot.error_ring_buffer = []
    bot.resolver.error_ring_buffer = []
    bot.resolver.is_degraded = True
    bot.resolver.consecutive_failures = 3
    bot.health.ytdlp_age_days = 40
    bot.health.ytdlp_outdated = True
    cog = AdminCog(bot)
    interaction = MagicMock()
    interaction.user.id = 42
    interaction.response.defer = AsyncMock()
    message = MagicMock(spec=discord.Message)

    with patch("yokai.cogs.admin.send", new=AsyncMock(return_value=message)) as sent:
        await cog.diag.callback(cog, interaction)

    assert sent.await_args.kwargs["view"].message is message  # so on_timeout can disable it
    embed = sent.await_args.kwargs["embed"]
    assert embed.color.value == COLOR_WARNING and "Diagnostic Telemetry" in embed.title
    assert embed.title.startswith("⚠️")
    values = " ".join(f.value for f in embed.fields)
    assert "(outdated)" in values
    assert "⚠️" not in values
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_admin_cog.py -q`
Expected: the four new tests FAIL with `AttributeError: 'AdminCog' object has no attribute 'diag'`.

- [ ] **Step 3: Add `diag` to `AdminCog`**

In `src/yokai/cogs/admin.py` extend the imports:
```python
import platform
from pathlib import Path
from typing import TYPE_CHECKING, Optional

import discord
import ytmusicapi
from discord import app_commands
from discord.ext import commands

from yokai.errors import InvalidPaymentError
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.views import DiagView
```
and add this method after `economy` (it is the body of the old `MetaCog.diag`, with the denial replaced by the shared helper):
```python
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
```
(The old denial passed `bot_avatar_url=avatar_url` computed as `str(self.bot.user.display_avatar.url) if self.bot.user else None`; `_avatar_url()` is identical.)

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_admin_cog.py -q`
Expected: 9 passed.

- [ ] **Step 5: Remove the old command and its tests**

In `src/yokai/cogs/meta.py` delete the whole `diag` method (from `@app_commands.command(name="diag", ...)` to the end of the method, just before `async def setup`). Remove the now-unused imports `from pathlib import Path` and `from yokai.ui.views import DiagView`; keep `platform`, `ytmusicapi`, `math`, `time`, `Literal`, which other commands still use.

In `tests/test_utility.py` delete: `test_diag_command_access_denial_for_non_owner`, `test_diag_command_owner_execution`, `test_diag_marks_attention_with_status_color_not_extra_emoji`, `test_diag_command_access_denial_message_names_admins`, `test_diag_command_runs_for_an_adminlist_member_and_locks_buttons_to_them`. Then run `python -m ruff check tests/test_utility.py` and delete any import it flags (probably `COLOR_WARNING`, possibly `fake_moderation`); keep `DiagView`, which the updater tests use.

- [ ] **Step 6: Run the suite and lint**

Run: `python -m pytest -q; python -m ruff check .; python -m ruff format --check .`
Expected: all green.

- [ ] **Step 7: Commit**

```powershell
git add src/yokai/cogs/admin.py src/yokai/cogs/meta.py tests/test_admin_cog.py tests/test_utility.py
git commit -m "feat(admin): move /diag to /admin diag" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 3: The `/owner` group, registered only when it has a subcommand

**Files:**
- Create: `src/yokai/cogs/owner.py`
- Create: `tests/test_owner_cog.py`

**Interfaces:**
- Produces: `OwnerCog(bot)` with class attribute `owner: app_commands.Group`; `setup(bot)` adds the cog only when `OwnerCog.owner.commands` is non-empty.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_owner_cog.py`:
```python
"""Tests for the /owner command group."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from yokai.cogs import owner as owner_module
from yokai.cogs.owner import OwnerCog


def test_the_owner_group_is_named_owner() -> None:
    assert OwnerCog.owner.name == "owner"


@pytest.mark.asyncio
async def test_an_empty_owner_group_is_not_registered() -> None:
    """Review Focus: Discord rejects a command group with no subcommands."""
    bot = MagicMock()
    bot.add_cog = AsyncMock()
    assert not OwnerCog.owner.commands  # nothing owner-only exists yet

    await owner_module.setup(bot)

    bot.add_cog.assert_not_awaited()
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_owner_cog.py -q`
Expected: collection ERROR, `cannot import name 'owner' from 'yokai.cogs'`.

- [ ] **Step 3: Create `src/yokai/cogs/owner.py`**

```python
"""Owner-only slash commands, grouped under /owner."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from discord import app_commands
from discord.ext import commands

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)


class OwnerCog(commands.Cog, name="Owner"):
    """Tools only the bot owner may use, all under the /owner group.

    Subcommands must check `bot.config.owner_id` themselves.
    """

    owner = app_commands.Group(name="owner", description="Owner-only tools.")

    def __init__(self, bot: YokaiBot) -> None:
        self.bot = bot


async def setup(bot: YokaiBot) -> None:
    """Extension setup entrypoint; an empty group is skipped because Discord rejects it."""
    if not OwnerCog.owner.commands:
        logger.info("No /owner subcommands yet; the group is not registered.")
        return
    await bot.add_cog(OwnerCog(bot))
```

- [ ] **Step 4: Run to verify they pass**

Run: `python -m pytest tests/test_owner_cog.py -q`
Expected: 2 passed.

- [ ] **Step 5: Lint and commit**

```powershell
python -m ruff check .; python -m ruff format --check .
git add src/yokai/cogs/owner.py tests/test_owner_cog.py
git commit -m "feat(admin): add the /owner group, registered once it has a subcommand" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```
Expected: lint clean.

---

### Task 4: Wire the groups into the bot and update `/diag` mentions

**Files:**
- Modify: `src/yokai/bot.py:138-141` (load the extensions) and `:226` (hint text)
- Modify: `src/yokai/errors.py:68`, `src/yokai/health.py:131`, `src/yokai/music/player.py:559`, `src/yokai/music/spotify/orchestrator.py:73`
- Create: `tests/test_command_tree.py`
- Modify: `tests/test_bot_wiring.py`

**Interfaces:**
- Consumes: `yokai.cogs.admin` and `yokai.cogs.owner` extensions (Tasks 1 to 3).

- [ ] **Step 1: Write the failing tests**

Create `tests/test_command_tree.py`:
```python
"""The registered slash-command tree has the shape the owner asked for."""

from __future__ import annotations

import pytest
from discord import app_commands

from tests.test_bot_wiring import make_bot


async def _loaded_tree() -> app_commands.CommandTree:
    bot = make_bot()
    for name in ("meta", "economy", "admin", "owner"):
        await bot.load_extension(f"yokai.cogs.{name}")
    return bot.tree


@pytest.mark.asyncio
async def test_admin_commands_live_under_the_admin_group() -> None:
    tree = await _loaded_tree()
    admin = tree.get_command("admin")
    assert isinstance(admin, app_commands.Group)
    assert {c.name for c in admin.commands} == {"economy", "diag"}


@pytest.mark.asyncio
async def test_the_old_top_level_admin_commands_are_gone() -> None:
    tree = await _loaded_tree()
    assert tree.get_command("diag") is None
    assert tree.get_command("economy-admin") is None


@pytest.mark.asyncio
async def test_the_empty_owner_group_is_not_in_the_tree() -> None:
    tree = await _loaded_tree()
    assert tree.get_command("owner") is None


@pytest.mark.asyncio
async def test_admin_subcommand_descriptions_fit_discords_limit() -> None:
    tree = await _loaded_tree()
    admin = tree.get_command("admin")
    assert isinstance(admin, app_commands.Group)
    assert all(0 < len(c.description) <= 100 for c in admin.commands)


@pytest.mark.asyncio
async def test_admin_commands_never_earn_the_music_reward() -> None:
    """Admin commands are logged as 'admin diag' / 'admin economy', not music commands."""
    from yokai.cogs.music import MUSIC_COMMAND_NAMES

    assert not {"admin diag", "admin economy", "diag", "economy-admin"} & set(MUSIC_COMMAND_NAMES)
```

Append to `tests/test_bot_wiring.py`:
```python
@pytest.mark.asyncio
async def test_setup_hook_loads_the_admin_and_owner_extensions() -> None:
    bot = make_bot()
    bot.db.initialize = AsyncMock()
    bot.db.refund_open_bets = AsyncMock(return_value=0)
    bot.audio_buffer.start = AsyncMock()
    bot.searcher.warm_up = AsyncMock()
    bot.load_extension = AsyncMock()
    bot.tree.sync = AsyncMock(return_value=[])
    with patch.object(bot.voice_earn_tracker, "start"):
        await bot.setup_hook()
    loaded = [call.args[0] for call in bot.load_extension.await_args_list]
    assert "yokai.cogs.admin" in loaded
    assert "yokai.cogs.owner" in loaded
```

- [ ] **Step 2: Run to verify they fail**

Run: `python -m pytest tests/test_command_tree.py tests/test_bot_wiring.py -q`
Expected: `test_setup_hook_loads_the_admin_and_owner_extensions` FAILS (extensions not loaded). The tree tests pass already because they load the extensions themselves; they pin the final shape.

- [ ] **Step 3: Load the extensions and fix the hint**

In `src/yokai/bot.py`, after `await self.load_extension("yokai.cogs.gambling")` add:
```python
        await self.load_extension("yokai.cogs.admin")
        await self.load_extension("yokai.cogs.owner")
```
and change line 226 to `user_hint="Check /admin diag or contact the bot owner if the issue persists.",`.

- [ ] **Step 4: Update the other user-facing mentions**

- `src/yokai/errors.py:68`: `"The bot owner can check /admin diag or update yt-dlp."`
- `src/yokai/health.py:131`: `... Consider updating via /admin diag or pip."`
- `src/yokai/music/player.py:559`: ``"\nYouTube is giving me trouble. The owner can check `/admin diag`."``
- `src/yokai/music/spotify/orchestrator.py:73`: docstring `... for /admin diag."`

Then check no test pins the old strings: `python -m pytest -q`. If a test fails on one of these strings, update that test's expected text to the new one.

- [ ] **Step 5: Run the suite and lint**

Run: `python -m pytest -q; python -m ruff check .; python -m ruff format --check .`
Expected: all green.

- [ ] **Step 6: Commit**

```powershell
git add src tests
git commit -m "feat(admin): load the admin and owner groups and point hints at /admin diag" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

---

### Task 5: Documentation and phase record

**Files:**
- Modify: `README.md`, `SPEC.md`, `TASKS.md`

- [ ] **Step 1: Rename the command in README and SPEC**

Replace every ``/diag`` with ``/admin diag`` in `README.md` (lines 21 and 168) and `SPEC.md` (lines 15, 106, 132, 160, 161, 215, 248, 346). Use:
```powershell
(Get-Content README.md -Raw) -replace '`/diag`', '`/admin diag`' | Set-Content README.md -NoNewline -Encoding utf8
(Get-Content SPEC.md -Raw) -replace '`/diag`', '`/admin diag`' | Set-Content SPEC.md -NoNewline -Encoding utf8
git diff --stat README.md SPEC.md
```
Expected: only those lines change. Also in `README.md` line 21 change "Owner-only" to "Admin-only" (the command is gated by `is_admin`), and add a line below it: ``- `/admin economy`: Admin-only Renown grant or deduction (was `/economy-admin`).``

- [ ] **Step 2: Add the phase to `TASKS.md`**

Append after Phase 9:
```markdown
---

## Phase 10 — Multiplayer tables and lottery

**Goal:** Dedicated-channel multiplayer tables, a house-funded lottery, and tidy `/admin` and `/owner`
command groups. Spec: `docs/superpowers/specs/2026-10-07-multiplayer-tables-design.md`. Phases M1 to
M4 each get their own plan and a stop for owner review.

### M1 — Command groups (plan: `docs/superpowers/plans/2026-10-07-multiplayer-m1-command-groups.md`)

- [ ] M1.1 `/admin` group with `economy` (was `/economy-admin`).
- [ ] M1.2 `/admin diag` (was `/diag`).
- [ ] M1.3 `/owner` group, registered only once it has a subcommand.
- [ ] M1.4 Extensions loaded; `/diag` mentions updated; docs updated.

**Acceptance criteria (M1)**
- `/admin economy` and `/admin diag` work for the owner and for adminlist members and refuse others
  with the ephemeral "Admins only" embed; the old `/economy-admin` and `/diag` no longer exist.
- `/owner` is absent from the command tree until it has a subcommand.
- `pytest`, `ruff check` and `ruff format --check` are clean.
- *(manual, real Discord)* Restart the bot; `/admin` offers `economy` and `diag`; `/diag` and
  `/economy-admin` are gone; `/admin diag` shows the **Update yt-dlp** button; a non-admin is refused.

**STOP — owner review.**

(M2 tables and duel, M3 jackpot, crash and blackjack table, M4 lottery: planned after M1 is reviewed.)
```
Also add one sentence under the existing Phase 9 acceptance line that mentions `/diag` and `/economy-admin` (TASKS.md around line 270): `(renamed /admin diag and /admin economy in Phase 10 M1)`.

- [ ] **Step 3: Final verification**

Run: `python -m pytest -q; python -m ruff check .; python -m ruff format --check .`
Expected: all clean. Tick the four M1 checkboxes in `TASKS.md`.

- [ ] **Step 4: Commit**

```powershell
git add README.md SPEC.md TASKS.md
git commit -m "docs(admin): document the /admin group and record multiplayer M1" -m "Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>" -m "Claude-Session: https://claude.ai/code/session_01BxUkTAJGP4uNByTtyvj8n9"
```

**STOP. Report what changed, list the unverified manual Discord checks, and wait for owner review before planning M2.**
