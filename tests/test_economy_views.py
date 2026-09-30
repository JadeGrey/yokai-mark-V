"""Tests for the leaderboard paginator view."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from yokai.economy.models import Wallet
from yokai.ui.views import LeaderboardPaginator


def _bot_with_pages(pages: dict[int, tuple[list[Wallet], int]]) -> MagicMock:
    bot = MagicMock()

    async def get_leaderboard(limit: int, offset: int) -> tuple[list[Wallet], int]:
        page = offset // limit + 1
        return pages[page]

    bot.db.get_leaderboard = AsyncMock(side_effect=get_leaderboard)
    return bot


def _wallet(user_id: int, balance: int) -> Wallet:
    return Wallet(
        user_id=user_id,
        balance=balance,
        last_daily_ts=None,
        daily_streak=0,
        created_ts=0,
    )


@pytest.mark.asyncio
async def test_build_embed_shows_ranked_entries_from_the_first_page() -> None:
    bot = _bot_with_pages({1: ([_wallet(10, 500), _wallet(20, 300)], 2)})
    view = LeaderboardPaginator(bot=bot, author_id=1, page_size=10)
    embed = await view.build_embed()
    assert "<@10>" in embed.description
    assert "#1" in embed.description


@pytest.mark.asyncio
async def test_anyone_can_use_the_leaderboard_buttons() -> None:
    bot = _bot_with_pages({1: ([_wallet(10, 500)], 1)})
    view = LeaderboardPaginator(bot=bot, author_id=1, page_size=10)
    interaction = MagicMock()
    interaction.user.id = 999999  # not the author
    assert await view.interaction_check(interaction) is True


@pytest.mark.asyncio
async def test_next_and_prev_buttons_move_between_pages() -> None:
    bot = _bot_with_pages(
        {
            1: ([_wallet(10, 500)], 2),
            2: ([_wallet(20, 300)], 2),
        }
    )
    view = LeaderboardPaginator(bot=bot, author_id=1, page_size=1)
    await view.build_embed()  # establishes total_pages = 2
    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()

    await view.next_button.callback(interaction)
    assert view.current_page == 2
    embed = interaction.response.edit_message.await_args.kwargs["embed"]
    assert "<@20>" in embed.description

    await view.prev_button.callback(interaction)
    assert view.current_page == 1


@pytest.mark.asyncio
async def test_next_button_does_not_pass_the_last_page() -> None:
    bot = _bot_with_pages({1: ([_wallet(10, 500)], 1)})
    view = LeaderboardPaginator(bot=bot, author_id=1, page_size=10)
    await view.build_embed()
    interaction = MagicMock()
    interaction.response.edit_message = AsyncMock()
    await view.next_button.callback(interaction)
    assert view.current_page == 1
