"""The bot owns a challenge engine and forwards settled bets to it as RoundPlayed events."""

from __future__ import annotations

from unittest.mock import MagicMock

from tests.test_bot_wiring import make_bot
from yokai.challenges.engine import ChallengeEngine
from yokai.challenges.models import RoundPlayed
from yokai.economy.models import BetSettlement


async def test_the_bot_has_an_engine_configured_from_settings() -> None:
    bot = make_bot(challenge_min_wager=25, challenge_reward_scale=2.0, challenge_enabled=True)
    assert isinstance(bot.challenges, ChallengeEngine)
    assert bot.challenges.min_wager == 25 and bot.challenges.reward_scale == 2.0
    assert bot.db.bet_listener == bot._on_bet_settled


async def test_a_settled_bet_becomes_a_round_event_keyed_by_group_or_bet() -> None:
    bot = make_bot()
    bot.challenges.emit = MagicMock()  # type: ignore[method-assign]
    bot._on_bet_settled(BetSettlement(9, 1, "slots", 50, 0, None))
    bot._on_bet_settled(BetSettlement(10, 1, "blackjack", 50, 100, "g7"))
    first, second = (call.args[0] for call in bot.challenges.emit.call_args_list)
    assert first == RoundPlayed(1, "slots", 50, 0, "bet9")
    assert second == RoundPlayed(1, "blackjack", 50, 100, "g7")


async def test_the_bot_owns_a_challenge_scheduler() -> None:
    from yokai.challenges.scheduler import ChallengeScheduler

    bot = make_bot()
    assert isinstance(bot.challenge_scheduler, ChallengeScheduler)
