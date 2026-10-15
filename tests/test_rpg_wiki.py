"""The guild wiki: generated content, paging limits, the viewer and the /guild wiki command."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord
import pytest
from discord import app_commands

from tests.embed_helpers import emoji_count, total_embed_length
from tests.test_bot_wiring import make_bot as make_wiring_bot
from yokai.cogs import admin, economy, gear, rpg, trade
from yokai.cogs.rpg import RpgCog
from yokai.rpg.content import Content, load_content
from yokai.rpg.models import LootRules
from yokai.rpg.party import Lobby  # noqa: F401  (keeps the party module imported with the wiki)
from yokai.rpg.runtime import RpgRuntime
from yokai.rpg.wiki import PAGE_LIMIT, SECTIONS, WikiSettings, build_wiki, paginate
from yokai.storage.db import Database
from yokai.ui.embeds import EmbedFactory
from yokai.ui.party_views import LOBBY_TIMEOUT_S
from yokai.ui.wiki_views import CONTENTS, WikiSectionSelect, WikiView


@pytest.fixture(scope="module")
def content() -> Content:
    return load_content(Path("content/rpg"))


@pytest.fixture(scope="module")
def wiki(content: Content):  # type: ignore[no-untyped-def]
    return build_wiki(content)


def text_of(wiki, section: str) -> str:  # type: ignore[no-untyped-def]
    return "\n".join(p.body for p in wiki.pages if p.section_id == section)


# ------------------------------------------------------------------------------ paging


def test_paginate_packs_blocks_and_never_exceeds_the_limit() -> None:
    pages = paginate(["a" * 40, "b" * 40, "c" * 40], limit=100)
    assert pages == ["a" * 40 + "\n\n" + "b" * 40, "c" * 40]
    assert paginate([]) == [""]


def test_a_block_longer_than_a_page_is_split_on_lines_and_long_lines_are_cut() -> None:
    block = "\n".join(f"line {n} " + "x" * 30 for n in range(20))
    pages = paginate([block], limit=200)
    assert len(pages) > 1 and all(len(p) <= 200 for p in pages)
    assert "".join(pages).count("line") == 20
    cut = paginate(["y" * 500], limit=100)
    assert all(len(p) <= 100 for p in cut)


# ---------------------------------------------------------------------------- structure


def test_every_page_fits_an_embed_and_has_a_title(wiki) -> None:  # type: ignore[no-untyped-def]
    for index, page in enumerate(wiki.pages):
        assert page.title and page.body.strip(), index
        assert len(page.body) <= PAGE_LIMIT, (page.title, len(page.body))
        embed = EmbedFactory.wiki_page(page.title, page.body, index + 1, len(wiki.pages))
        assert total_embed_length(embed) <= 6000 and emoji_count(embed) == 1


def test_contents_lists_every_section_with_the_right_page_number(wiki) -> None:  # type: ignore[no-untyped-def]
    toc = wiki.pages[0]
    assert toc.section_id == "contents" and wiki.first_page["contents"] == 0
    for number, (section_id, title, summary) in enumerate(SECTIONS, start=1):
        assert f"**{title}**" in toc.body and summary in toc.body
        first = wiki.first_page[section_id]
        assert f"`{number}.` **{title}** · page {first + 1}" in toc.body
        assert wiki.pages[first].section_id == section_id


def test_sections_are_contiguous_and_in_contents_order(wiki) -> None:  # type: ignore[no-untyped-def]
    order = [p.section_id for p in wiki.pages[1:]]
    expected = [s for s, _, _ in SECTIONS]
    collapsed = [s for i, s in enumerate(order) if i == 0 or order[i - 1] != s]
    assert collapsed == expected


def test_multi_page_sections_number_their_pages(wiki) -> None:  # type: ignore[no-untyped-def]
    titles = [p.title for p in wiki.pages if p.section_id == "missions"]
    assert len(titles) >= 2
    assert titles[0].endswith(f"(1/{len(titles)})") and titles[-1].endswith(
        f"({len(titles)}/{len(titles)})"
    )


# ------------------------------------------------------------------------------ content


def test_the_commands_page_lists_every_registered_rpg_command(wiki) -> None:  # type: ignore[no-untyped-def]
    import asyncio

    async def names() -> set[str]:
        bot = make_wiring_bot()
        for module in (economy, admin, rpg, gear, trade):
            await module.setup(bot)
        found = set()
        for command in bot.tree.walk_commands():
            if isinstance(command, app_commands.Command):
                found.add(command.qualified_name)
        return found

    registered = asyncio.run(names())
    text = text_of(wiki, "commands")
    wanted = {n for n in registered if n.startswith(("guild ", "gear ", "trade", "card"))}
    assert {"guild wiki", "guild mission", "gear sell", "trade", "card"} <= wanted
    for name in wanted:
        assert f"`/{name}" in text, name
    assert "/admin rpg" in text


def test_ranks_missions_tags_and_enemies_all_appear(content: Content, wiki) -> None:  # type: ignore[no-untyped-def]
    ranks = text_of(wiki, "ranks")
    for rank in content.ranks:
        assert f"Rank {rank.id}" in ranks
        if rank.promotion:
            assert rank.promotion.name in ranks and f"level **{rank.min_level}**" in ranks
    missions = text_of(wiki, "missions")
    for mission in content.missions.values():
        assert f"**{mission.name}**" in missions, mission.id
    assert missions.count("RAID") == sum(m.kind == "raid" for m in content.missions.values())
    gear_text = text_of(wiki, "gear")
    for tag in content.tags.values():
        assert f"**{tag.name}**" in gear_text
        if tag.skill:
            assert content.skills[tag.skill].name in gear_text
    bestiary = text_of(wiki, "bestiary")
    for enemy in content.enemies.values():
        assert f"**{enemy.name}**" in bestiary
    assert bestiary.count("(boss)") == sum(e.boss for e in content.enemies.values())


def test_the_loot_page_quotes_the_real_odds_prices_and_tiers(content: Content, wiki) -> None:  # type: ignore[no-untyped-def]
    loot = text_of(wiki, "loot")
    rules = content.balance.loot
    assert f"**{rules.drop_chance_percent}%**" in loot
    assert f"**{rules.boss_drop_chance_percent}%**" in loot
    total = sum(w for _, w in rules.rarity_weights)
    for rarity, weight in rules.rarity_weights:
        assert f"{rarity} {round(100 * weight / total)}%" in loot
    for tier in content.item_tiers():
        assert f"Level {tier}:" in loot
    for rank in content.ranks:
        if content.missions_for_rank(rank.id):
            assert f"**Rank {rank.id}** drops gear needing level" in loot
    assert str(sum(1 for i in content.items.values() if not i.retired)) in loot
    boss_line = loot.split("**Boss missions**")[1]
    for mission in content.missions.values():
        assert (mission.name in boss_line) == (
            mission.kind == "mission"
            and any(content.enemies[e].boss for st in mission.stages for e in st.enemies)
        ), mission.id


def test_the_wiki_follows_balance_changes(content: Content) -> None:
    changed = replace(
        content,
        balance=replace(
            content.balance,
            loot=LootRules(
                drop_chance_percent=77,
                boss_drop_chance_percent=88,
                tier_window=content.balance.loot.tier_window,
                rarity_weights=content.balance.loot.rarity_weights,
                boss_rarity_weights=content.balance.loot.boss_rarity_weights,
            ),
        ),
    )
    loot = "\n".join(p.body for p in build_wiki(changed).pages if p.section_id == "loot")
    assert "**77%**" in loot and "**88%**" in loot


def test_operator_settings_are_quoted(content: Content) -> None:
    settings = WikiSettings(
        base_attempts=11, turn_seconds=33, promotion_cooldown_hours=9, shop_daily_cap=5,
        timezone="Europe/Oslo", lobby_minutes=7,
    )  # fmt: skip
    built = build_wiki(content, settings)
    text = lambda s: "\n".join(p.body for p in built.pages if p.section_id == s)  # noqa: E731
    assert "**11 attempts**" in text("start") and "Europe/Oslo" in text("start")
    assert "**33 seconds**" in text("combat")
    assert "**9-hour cooldown**" in text("ranks")
    assert "**5** a day" in text("loot")
    assert "**7 minutes**" in text("parties")


def test_defaults_match_the_real_constants(content: Content) -> None:
    from yokai.rpg.trade import MAX_ITEMS_PER_SIDE, SESSION_SECONDS

    assert WikiSettings().lobby_minutes == int(LOBBY_TIMEOUT_S // 60)
    built = build_wiki(content)
    trading = "\n".join(p.body for p in built.pages if p.section_id == "trading")
    assert f"Up to {MAX_ITEMS_PER_SIDE} items" in trading
    assert f"{SESSION_SECONDS // 60} minutes" in trading


# ------------------------------------------------------------------------------ viewer


def click(user_id: int = 1) -> MagicMock:
    interaction = MagicMock(spec=discord.Interaction)
    interaction.user = MagicMock(spec=discord.Member)
    interaction.user.id = user_id
    interaction.response = MagicMock()
    interaction.response.edit_message = AsyncMock()
    interaction.response.send_message = AsyncMock()
    interaction.response.is_done = MagicMock(return_value=False)
    return interaction


def make_view(wiki: Any, index: int = 0) -> WikiView:
    bot = MagicMock()
    bot.user = None
    return WikiView(bot, wiki, 1, index)


@pytest.mark.asyncio
async def test_buttons_page_through_and_stop_at_the_ends(wiki) -> None:  # type: ignore[no-untyped-def]
    view = make_view(wiki)
    assert view.prev_button.disabled and view.contents_button.disabled
    assert not view.next_button.disabled
    first = click()
    await view.next_button.callback(first)
    assert view.index == 1 and not view.prev_button.disabled and not view.contents_button.disabled
    assert first.response.edit_message.await_args.kwargs["view"] is view
    await view.prev_button.callback(click())
    assert view.index == 0
    for _ in range(len(wiki.pages) + 3):
        await view.next_button.callback(click())
    assert view.index == len(wiki.pages) - 1 and view.next_button.disabled
    await view.contents_button.callback(click())
    assert view.index == 0


@pytest.mark.asyncio
async def test_the_menu_jumps_to_a_section_and_marks_it(wiki) -> None:  # type: ignore[no-untyped-def]
    view = make_view(wiki)
    select = view.select
    assert isinstance(select, WikiSectionSelect)
    assert [o.value for o in select.options] == [CONTENTS, *[s for s, _, _ in SECTIONS]]
    assert len(select.options) <= 25
    interaction = click()
    with patch.object(type(select), "values", ["loot"], create=True):
        await select.callback(interaction)
    assert view.index == wiki.first_page["loot"]
    embed = interaction.response.edit_message.await_args.kwargs["embed"]
    assert "Loot and the shop" in (embed.title or "")
    assert [o.value for o in select.options if o.default] == ["loot"]
    with patch.object(type(select), "values", [CONTENTS], create=True):
        await select.callback(click())
    assert view.index == 0 and [o.value for o in select.options if o.default] == [CONTENTS]


def test_the_view_clamps_its_start_page_and_renders_page_numbers(wiki) -> None:  # type: ignore[no-untyped-def]
    assert make_view(wiki, 999).index == len(wiki.pages) - 1
    assert make_view(wiki, -4).index == 0
    embed = make_view(wiki, 2).render()
    assert f"page 3/{len(wiki.pages)}" in (embed.footer.text or "")


@pytest.mark.asyncio
async def test_only_the_reader_can_use_it_and_it_times_out(wiki) -> None:  # type: ignore[no-untyped-def]
    view = make_view(wiki)
    with patch("yokai.moderation.gate.screen", new=AsyncMock(return_value=False)):
        assert await view.interaction_check(click(1)) is True
        stranger = click(2)
        assert await view.interaction_check(stranger) is False
    stranger.response.send_message.assert_awaited_once()
    view.message = MagicMock()
    view.message.edit = AsyncMock()
    await view.on_timeout()
    assert all(item.disabled for item in view.children)
    view.message.edit.assert_awaited_once()


# ------------------------------------------------------------------------------ command


async def make_cog(tmp_path: Path):  # type: ignore[no-untyped-def]
    db = Database(tmp_path / "wiki.db")
    await db.initialize()
    runtime = RpgRuntime.create(db, Path("content/rpg"))
    bot = MagicMock()
    bot.user = None
    bot.rpg = runtime
    bot.config.rpg_tz = "UTC"
    bot.config.rpg_base_attempts = 8
    bot.config.rpg_turn_seconds = 45
    bot.config.rpg_promotion_cooldown_hours = 6
    bot.config.rpg_shop_daily_cap = 3
    return db, RpgCog(bot)


@pytest.mark.asyncio
async def test_the_command_opens_a_private_wiki_at_the_contents(tmp_path: Path) -> None:
    db, cog = await make_cog(tmp_path)
    try:
        interaction = click()
        with patch("yokai.cogs.rpg.send", new=AsyncMock(return_value=MagicMock())) as sent:
            await cog.wiki.callback(cog, interaction, None)
        assert sent.await_args.kwargs["ephemeral"] is True
        view = sent.await_args.kwargs["view"]
        assert isinstance(view, WikiView) and view.index == 0
        assert view.message is sent.return_value
        assert "Contents" in (sent.await_args.kwargs["embed"].title or "")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_command_can_jump_to_a_section(tmp_path: Path) -> None:
    db, cog = await make_cog(tmp_path)
    try:
        choice = app_commands.Choice(name="Commands", value="commands")
        with patch("yokai.cogs.rpg.send", new=AsyncMock(return_value=MagicMock())) as sent:
            await cog.wiki.callback(cog, click(), choice)
        view = sent.await_args.kwargs["view"]
        assert view.wiki.pages[view.index].section_id == "commands"
        assert "/guild mission" in view.wiki.pages[view.index].body
    finally:
        await db.close()


def test_the_section_choices_match_the_wiki_sections() -> None:
    from yokai.cogs.rpg import WIKI_CHOICES

    assert [c.value for c in WIKI_CHOICES] == [s for s, _, _ in SECTIONS]
    assert len(WIKI_CHOICES) <= 25
