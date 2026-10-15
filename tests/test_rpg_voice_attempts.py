"""Extra daily attempts for time spent in a call with another person."""

from __future__ import annotations

import os
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from tests.moderation_helpers import fake_moderation
from tests.test_rpg_cog_missions import make_bot, make_interaction, make_runtime
from yokai.cogs.rpg import RpgCog
from yokai.config import load_config
from yokai.economy.voice_tracker import VoiceEarnTracker
from yokai.errors import NoAttemptsError
from yokai.rpg import guild
from yokai.rpg.content import load_content
from yokai.rpg.models import NO_VOICE, Character, StreakRules, VoiceRules
from yokai.rpg.store import RpgStore
from yokai.rpg.wiki import WikiSettings, build_wiki
from yokai.storage.db import CURRENT_SCHEMA_VERSION, Database

RULES = StreakRules(days_per_bonus=3, bonus_cap=3, drop_per_missed_day=3)
VOICE = VoiceRules(minutes_per_attempt=30, max_bonus=8)
DAY = "2026-10-14"
HALF_HOUR = 30 * 60


def character(**overrides: object) -> Character:
    base = Character(
        user_id=1, level=1, xp=0, rank="E", battles_won=0, battles_lost=0, created_ts=0
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


# ------------------------------------------------------------------------------ pure rules


@pytest.mark.parametrize(
    ("seconds", "bonus"),
    [
        (0, 0),
        (HALF_HOUR - 1, 0),
        (HALF_HOUR, 1),
        (HALF_HOUR * 2 + 599, 2),
        (HALF_HOUR * 8, 8),
        (HALF_HOUR * 20, 8),
        (-500, 0),
    ],
)
def test_every_full_half_hour_gives_one_attempt_up_to_the_cap(seconds: int, bonus: int) -> None:
    assert guild.voice_bonus(seconds, VOICE) == bonus


def test_voice_attempts_can_be_turned_off_by_either_setting() -> None:
    assert guild.voice_bonus(HALF_HOUR * 10, NO_VOICE) == 0
    assert guild.voice_bonus(HALF_HOUR * 10, VoiceRules(0, 8)) == 0
    assert guild.voice_bonus(HALF_HOUR * 10, VoiceRules(30, 0)) == 0


def test_time_to_the_next_voice_attempt_counts_down_and_vanishes_at_the_cap() -> None:
    assert guild.voice_seconds_to_next(0, VOICE) == HALF_HOUR
    assert guild.voice_seconds_to_next(1000, VOICE) == HALF_HOUR - 1000
    assert guild.voice_seconds_to_next(HALF_HOUR, VOICE) == HALF_HOUR
    assert guild.voice_seconds_to_next(HALF_HOUR * 8, VOICE) is None
    assert guild.voice_seconds_to_next(100, NO_VOICE) is None


def test_the_allowance_adds_voice_time_to_base_and_streak() -> None:
    keen = character(
        streak=6, last_active_day="2026-10-13", voice_seconds=HALF_HOUR * 3, voice_day=DAY
    )
    status = guild.attempt_status(keen, DAY, 8, RULES, VOICE)
    assert (status.bonus, status.voice_bonus, status.allowance) == (2, 3, 13)
    assert status.voice_seconds_to_next == HALF_HOUR


def test_voice_time_from_another_day_does_not_count() -> None:
    yesterday = character(voice_seconds=HALF_HOUR * 4, voice_day="2026-10-13")
    status = guild.attempt_status(yesterday, DAY, 8, RULES, VOICE)
    assert status.voice_bonus == 0 and status.allowance == 8


def test_without_voice_rules_the_allowance_is_unchanged() -> None:
    voiced = character(voice_seconds=HALF_HOUR * 4, voice_day=DAY)
    assert guild.attempt_status(voiced, DAY, 8, RULES).allowance == 8


def test_voice_attempts_can_pay_for_a_mission_the_base_cannot() -> None:
    spent = character(attempts_used=8, attempts_day=DAY, voice_seconds=HALF_HOUR * 2, voice_day=DAY)
    plan = guild.plan_spend(spent, 2, DAY, 8, RULES, voice=VOICE)
    assert plan.used == 10
    with pytest.raises(NoAttemptsError):
        guild.plan_spend(spent, 3, DAY, 8, RULES, voice=VOICE)
    with pytest.raises(NoAttemptsError):
        guild.plan_spend(spent, 1, DAY, 8, RULES)


# ---------------------------------------------------------------------------------- the store


async def fresh(tmp_path: Path) -> tuple[Database, RpgStore]:
    db = Database(tmp_path / "voice.db")
    await db.initialize()
    return db, RpgStore(db)


@pytest.mark.asyncio
async def test_voice_time_adds_up_within_a_day_and_starts_over_on_the_next(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        assert await store.add_voice_time([1], 300, DAY) == 1
        assert await store.add_voice_time([1], 300, DAY) == 1
        mine = await store.get_character(1)
        assert mine is not None and (mine.voice_seconds, mine.voice_day) == (600, DAY)
        await store.add_voice_time([1], 300, "2026-10-15")
        mine = await store.get_character(1)
        assert mine is not None and (mine.voice_seconds, mine.voice_day) == (300, "2026-10-15")
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_voice_time_for_several_members_skips_those_without_a_character(
    tmp_path: Path,
) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        await store.create_character(2, "E", now=1)
        assert await store.add_voice_time([1, 2, 3], 300, DAY) == 2
        assert await store.get_character(3) is None
        for user_id in (1, 2):
            member = await store.get_character(user_id)
            assert member is not None and member.voice_seconds == 300
        assert await store.add_voice_time([], 300, DAY) == 0
        assert await store.add_voice_time([1], 0, DAY) == 0
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_fight_can_spend_attempts_earned_by_voice_time(tmp_path: Path) -> None:
    db, store = await fresh(tmp_path)
    try:
        await store.create_character(1, "E", now=1)
        await store.add_voice_time([1], HALF_HOUR * 2, DAY)

        async def begin(cost: int, voice: VoiceRules) -> int:
            return await store.begin_fight(
                mission_id="e_pest_control", kind="mission", user_ids=[1], cost=cost,
                today=DAY, base_attempts=1, streak_rules=RULES, voice_rules=voice,
            )  # fmt: skip

        with pytest.raises(NoAttemptsError):
            await begin(3, NO_VOICE)
        await begin(3, VOICE)
        mine = await store.get_character(1)
        assert mine is not None and mine.attempts_used == 3
        with pytest.raises(NoAttemptsError):
            await begin(1, VOICE)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_v13_database_gains_the_voice_columns_and_keeps_its_characters(
    tmp_path: Path,
) -> None:
    db, store = await fresh(tmp_path)
    await store.create_character(1, "E", now=1)
    await db.conn.execute("ALTER TABLE rpg_characters DROP COLUMN voice_seconds;")
    await db.conn.execute("ALTER TABLE rpg_characters DROP COLUMN voice_day;")
    await db.conn.execute("PRAGMA user_version = 13;")
    await db.conn.commit()
    await db.close()

    reopened = Database(tmp_path / "voice.db")
    await reopened.initialize()
    try:
        async with reopened.conn.execute("PRAGMA user_version;") as cursor:
            row = await cursor.fetchone()
        assert row is not None and row[0] == CURRENT_SCHEMA_VERSION >= 14
        mine = await RpgStore(reopened).get_character(1)
        assert mine is not None and (mine.voice_seconds, mine.voice_day) == (0, None)
    finally:
        await reopened.close()


# ------------------------------------------------------------------------------ the tracker


def _member(user_id: int, is_bot: bool = False) -> MagicMock:
    member = MagicMock()
    member.id = user_id
    member.bot = is_bot
    return member


def _channel(members: list[MagicMock], channel_id: int = 10) -> MagicMock:
    channel = MagicMock()
    channel.id = channel_id
    channel.members = members
    return channel


def _tracker_bot(
    channels: list[MagicMock],
    *,
    afk: MagicMock | None = None,
    blacklisted: tuple[int, ...] = (),
    minutes: int = 30,
    cap: int = 8,
) -> MagicMock:
    bot = MagicMock()
    bot.config.guild_id = 1
    bot.config.economy_welcome_bonus = 200
    bot.config.rpg_tz = "UTC"
    bot.config.rpg_voice_minutes_per_attempt = minutes
    bot.config.rpg_voice_max_bonus = cap
    guild_ = MagicMock()
    guild_.voice_channels = channels
    guild_.afk_channel = afk
    bot.get_guild.return_value = guild_
    bot.db.credit_wallet = AsyncMock()
    bot.moderation = fake_moderation(owner_id=0, blacklisted=blacklisted)
    bot.rpg.store.add_voice_time = AsyncMock(return_value=0)
    return bot


def _recorded(bot: MagicMock) -> list[tuple[list[int], int]]:
    return [(list(c.args[0]), c.args[1]) for c in bot.rpg.store.add_voice_time.await_args_list]


@pytest.mark.asyncio
async def test_a_tick_records_the_tick_length_for_everyone_in_company_in_one_call() -> None:
    bot = _tracker_bot(
        [_channel([_member(1), _member(2), _member(99, True)]), _channel([_member(3)], 11)]
    )
    await VoiceEarnTracker(bot, interval_s=300, reward=5).tick()
    assert _recorded(bot) == [([1, 2], 300)]
    day = bot.rpg.store.add_voice_time.await_args.args[2]
    assert len(day) == 10 and day[4] == "-" and day[7] == "-"


@pytest.mark.asyncio
async def test_a_lone_human_the_afk_channel_and_the_blacklisted_earn_no_voice_time() -> None:
    alone = _tracker_bot([_channel([_member(1), _member(99, True)])])
    await VoiceEarnTracker(alone, interval_s=300, reward=5).tick()
    alone.rpg.store.add_voice_time.assert_not_awaited()

    parked = _channel([_member(1), _member(2)])
    afk = _tracker_bot([parked], afk=parked)
    await VoiceEarnTracker(afk, interval_s=300, reward=5).tick()
    afk.rpg.store.add_voice_time.assert_not_awaited()

    banned = _tracker_bot([_channel([_member(1), _member(2)])], blacklisted=(2,))
    await VoiceEarnTracker(banned, interval_s=300, reward=5).tick()
    assert _recorded(banned) == [([1], 300)]


@pytest.mark.asyncio
async def test_voice_time_is_recorded_even_when_voice_renown_is_off() -> None:
    bot = _tracker_bot([_channel([_member(1), _member(2)])])
    await VoiceEarnTracker(bot, interval_s=300, reward=0).tick()
    bot.db.credit_wallet.assert_not_awaited()
    assert _recorded(bot) == [([1, 2], 300)]


@pytest.mark.asyncio
async def test_nothing_is_recorded_when_voice_attempts_or_the_rpg_are_off() -> None:
    for minutes, cap in ((0, 8), (30, 0)):
        bot = _tracker_bot([_channel([_member(1), _member(2)])], minutes=minutes, cap=cap)
        await VoiceEarnTracker(bot, interval_s=300, reward=5).tick()
        bot.rpg.store.add_voice_time.assert_not_awaited()

    no_rpg = _tracker_bot([_channel([_member(1), _member(2)])])
    no_rpg.rpg = None
    await VoiceEarnTracker(no_rpg, interval_s=300, reward=5).tick()
    assert no_rpg.db.credit_wallet.await_count == 2


@pytest.mark.asyncio
async def test_a_failing_voice_time_write_does_not_stop_renown_or_challenge_minutes() -> None:
    bot = _tracker_bot([_channel([_member(1), _member(2)])])
    bot.rpg.store.add_voice_time = AsyncMock(side_effect=RuntimeError("db down"))
    await VoiceEarnTracker(bot, interval_s=300, reward=5).tick()
    assert bot.db.credit_wallet.await_count == 2
    assert bot.challenges.emit.call_count == 2


# ----------------------------------------------------------------------------------- display


@pytest.mark.asyncio
async def test_the_board_shows_voice_attempts_and_the_time_to_the_next(tmp_path: Path) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        today, _ = cog._day()
        await runtime.store.add_voice_time([111], HALF_HOUR * 2 + 600, today)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.board.callback(cog, make_interaction())
        description = sent.await_args.kwargs["embed"].description or ""
        assert "**10/10**" in description
        assert "Voice time: **+2** attempts today." in description
        assert "20 min in a call with someone gives the next one." in description
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_board_is_quiet_about_voice_when_there_is_none_or_it_is_maxed(
    tmp_path: Path,
) -> None:
    db, runtime = await make_runtime(tmp_path)
    try:
        cog = RpgCog(make_bot(runtime))
        today, _ = cog._day()
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.board.callback(cog, make_interaction())
        text = sent.await_args.kwargs["embed"].description or ""
        assert "Voice time" not in text and "**8/8**" in text
        assert "30 min in a call" in text

        await runtime.store.add_voice_time([111], HALF_HOUR * 9, today)
        with patch("yokai.cogs.rpg.send", new=AsyncMock()) as sent:
            await cog.board.callback(cog, make_interaction())
        text = sent.await_args.kwargs["embed"].description or ""
        assert "**16/16**" in text and "Voice time: **+8**" in text
        assert "min in a call" not in text
    finally:
        await db.close()


def test_the_wiki_explains_voice_attempts_with_the_configured_numbers() -> None:
    content = load_content(Path("content/rpg"))

    def start_text(settings: WikiSettings) -> str:
        wiki = build_wiki(content, settings)
        return "\n".join(p.body for p in wiki.pages if p.section_id == "start")

    on = start_text(WikiSettings(voice_minutes_per_attempt=20, voice_max_bonus=5))
    assert "Voice time" in on and "**20 minutes**" in on and "**+5**" in on
    assert "Voice time" not in start_text(WikiSettings(voice_minutes_per_attempt=0))
    assert "Voice time" not in start_text(WikiSettings(voice_max_bonus=0))


# ------------------------------------------------------------------------------------ config


def _load(extra: dict[str, str]) -> object:
    env = {"DISCORD_TOKEN": "x", "GUILD_ID": "1", "OWNER_ID": "2", **extra}
    with patch.dict(os.environ, env, clear=True):
        return load_config(env_path=Path("non_existent_env"))


def test_voice_attempt_settings_default_to_half_an_hour_and_eight() -> None:
    config = _load({})
    assert (config.rpg_voice_minutes_per_attempt, config.rpg_voice_max_bonus) == (30, 8)  # type: ignore[attr-defined]
    assert guild.voice_rules_from_config(config) == VOICE  # type: ignore[arg-type]


def test_voice_attempt_settings_can_be_changed_or_switched_off() -> None:
    config = _load({"RPG_VOICE_MINUTES_PER_ATTEMPT": "15", "RPG_VOICE_MAX_BONUS": "4"})
    assert guild.voice_rules_from_config(config) == VoiceRules(15, 4)  # type: ignore[arg-type]
    off = _load({"RPG_VOICE_MINUTES_PER_ATTEMPT": "0"})
    assert not guild.voice_rules_from_config(off).enabled  # type: ignore[arg-type]
    negative = _load({"RPG_VOICE_MINUTES_PER_ATTEMPT": "-5", "RPG_VOICE_MAX_BONUS": "-1"})
    assert not guild.voice_rules_from_config(negative).enabled  # type: ignore[arg-type]
