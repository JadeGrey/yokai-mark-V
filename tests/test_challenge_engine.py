"""ChallengeEngine: matching events to slates, anti-farm rules, payout and override."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from tests.challenge_helpers import DAY, FakeClock, make_engine, tpl
from yokai.challenges.models import RoundPlayed, TrackListened, VoiceMinutes
from yokai.storage.db import Database


@pytest.fixture
async def db(tmp_path: Path):
    database = Database(tmp_path / "t.db")
    try:
        await database.initialize()
        yield database
    finally:
        await database.close()


async def balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    return wallet.balance if wallet else 0


def listened(*users: int, video: str = "v1", artist: str | None = "Band", channel: int = 5):
    return TrackListened(user_ids=tuple(users), video_id=video, artist=artist, channel_id=channel)


def round_(user: int, game: str = "slots", stake: int = 50, payout: int = 0, key: str = "r1"):
    return RoundPlayed(user_id=user, game=game, stake=stake, payout=payout, round_key=key)


async def test_every_listener_advances_and_completion_pays_and_queues(db: Database) -> None:
    engine = make_engine(db, tpl("d_tracks", "tracks", 2, 40))
    await engine.record(listened(1, 2, video="a"))
    await engine.record(listened(1, 2, video="b"))

    assert await balance(db, 1) == 200 + 40 and await balance(db, 2) == 200 + 40
    done = engine.drain_completions()
    assert {(c.user_id, c.template_id, c.reward, c.channel_id) for c in done} == {
        (1, "d_tracks", 40, 5),
        (2, "d_tracks", 40, 5),
    }
    assert engine.drain_completions() == []


async def test_a_looped_track_does_not_farm_progress(db: Database) -> None:
    engine = make_engine(db, tpl("d_tracks", "tracks", 2, 40))
    for _ in range(10):
        await engine.record(listened(1, video="same"))
    _, templates = await engine.active_slate("daily")
    rows = await engine.view(1, "daily")
    assert rows[0].progress == 1 and not rows[0].completed
    assert await balance(db, 1) == 0 and templates


async def test_artists_are_distinct_case_insensitive_and_missing_artist_is_ignored(
    db: Database,
) -> None:
    engine = make_engine(db, tpl("w_artists", "artists", 2, 50, period="weekly"))
    await engine.record(listened(1, video="a", artist="Daft Punk"))
    await engine.record(listened(1, video="b", artist=" daft punk "))
    await engine.record(listened(1, video="c", artist=None))
    assert (await engine.view(1, "weekly"))[0].progress == 1
    await engine.record(listened(1, video="d", artist="Justice"))
    assert (await engine.view(1, "weekly"))[0].completed


async def test_rounds_below_the_wager_floor_are_ignored(db: Database) -> None:
    engine = make_engine(
        db, tpl("d_rounds", "rounds_played", 2, 30, category="games"), min_wager=10
    )
    await engine.record(round_(1, stake=9, key="a"))
    await engine.record(round_(1, stake=10, key="b"))
    assert (await engine.view(1, "daily"))[0].progress == 1


async def test_a_blackjack_double_is_one_round_and_one_win(db: Database) -> None:
    engine = make_engine(
        db,
        tpl("d_rounds", "rounds_played", 5, 30, category="games"),
        tpl("d_win", "rounds_won", 5, 30, category="voice"),  # second slate slot, any category
    )
    for _ in range(2):  # the original bet and the doubled bet settle with the same key
        await engine.record(round_(1, game="blackjack", stake=50, payout=100, key="g1"))
    rows = {r.template.id: r for r in await engine.view(1, "daily")}
    assert rows["d_rounds"].progress == 1 and rows["d_win"].progress == 1


async def test_a_push_is_not_a_win_and_the_game_filter_applies(db: Database) -> None:
    engine = make_engine(
        db, tpl("d_win_slots", "rounds_won", 1, 35, category="games", game=frozenset({"slots"}))
    )
    await engine.record(round_(1, game="slots", stake=50, payout=50, key="push"))
    await engine.record(round_(1, game="blackjack", stake=50, payout=100, key="bj"))
    assert (await engine.view(1, "daily"))[0].progress == 0
    await engine.record(round_(1, game="slots", stake=50, payout=100, key="win"))
    assert (await engine.view(1, "daily"))[0].completed


async def test_the_blackjack_win_template_counts_the_table_but_not_slots(db: Database) -> None:
    engine = make_engine(
        db,
        tpl(
            "d_win_blackjack",
            "rounds_won",
            1,
            35,
            category="games",
            game=frozenset({"blackjack", "bjtable"}),
        ),
    )
    await engine.record(round_(1, game="slots", stake=50, payout=100, key="s"))
    assert (await engine.view(1, "daily"))[0].progress == 0
    await engine.record(round_(1, game="bjtable", stake=50, payout=100, key="t"))
    assert (await engine.view(1, "daily"))[0].completed


async def test_distinct_games_metric(db: Database) -> None:
    engine = make_engine(db, tpl("w_kinds", "games", 2, 60, period="weekly", category="games"))
    await engine.record(round_(1, game="slots", key="a"))
    await engine.record(round_(1, game="slots", key="b"))
    assert (await engine.view(1, "weekly"))[0].progress == 1
    await engine.record(round_(1, game="roulette", key="c"))
    assert (await engine.view(1, "weekly"))[0].completed


async def test_voice_minutes_accumulate(db: Database) -> None:
    engine = make_engine(db, tpl("d_voice", "voice_minutes", 10, 30, category="voice"))
    await engine.record(VoiceMinutes(1, 5))
    await engine.record(VoiceMinutes(1, 5))
    assert (await engine.view(1, "daily"))[0].completed


async def test_blacklisted_users_earn_nothing_and_a_disabled_engine_ignores_everything(
    db: Database,
) -> None:
    engine = make_engine(db, tpl("d_voice", "voice_minutes", 5, 30), blacklisted=(1,))
    await engine.record(VoiceMinutes(1, 5))
    assert await balance(db, 1) == 0
    off = make_engine(db, tpl("d_voice", "voice_minutes", 5, 30), enabled=False)
    await off.record(VoiceMinutes(2, 5))
    off.emit(VoiceMinutes(2, 5))
    await off.close()
    assert await balance(db, 2) == 0


async def test_a_new_user_gets_the_welcome_bonus_plus_exactly_one_reward(db: Database) -> None:
    engine = make_engine(db, tpl("d_voice", "voice_minutes", 5, 30))
    await asyncio.gather(*(engine.record(VoiceMinutes(7, 1)) for _ in range(12)))
    assert await balance(db, 7) == 200 + 30
    assert len(engine.drain_completions()) == 1


async def test_reward_scale_applies_and_never_drops_below_one(db: Database) -> None:
    big = make_engine(db, tpl("d_voice", "voice_minutes", 1, 30), reward_scale=2.0)
    await big.record(VoiceMinutes(1, 1))
    assert await balance(db, 1) == 200 + 60
    tiny = make_engine(db, tpl("d_voice", "voice_minutes", 1, 3), reward_scale=0.1)
    assert tiny.reward_for(tpl("x", "voice_minutes", 1, 3)) == 1


async def test_the_slate_survives_a_restart(db: Database) -> None:
    pool = (
        tpl("d_a", "tracks", 5, 10),
        tpl("d_b", "tracks", 6, 10),
        tpl("d_c", "tracks", 7, 10),
    )
    first = make_engine(db, *pool)
    key, picked = await first.active_slate("daily")
    # Store a slate that pick_slate would not choose, so only the stored row can explain it.
    other = next(t.id for t in pool if t.id not in {p.id for p in picked})
    await db.replace_challenge_slate("daily", key, [other])
    # A new engine with a reordered, larger pool must return exactly the stored slate.
    second = make_engine(db, tpl("d_z", "tracks", 9, 10), *reversed(pool))
    _, again = await second.active_slate("daily")
    assert [t.id for t in again] == [other]
    assert other not in {t.id for t in picked}


async def test_a_new_day_gets_a_fresh_slate_and_progress(db: Database) -> None:
    clock = FakeClock()
    engine = make_engine(db, tpl("d_voice", "voice_minutes", 10, 30), clock=clock)
    await engine.record(VoiceMinutes(1, 6))
    clock.now += DAY
    assert (await engine.view(1, "daily"))[0].progress == 0
    clock.now -= DAY
    assert (await engine.view(1, "daily"))[0].progress == 6


async def test_unknown_template_ids_in_a_stored_slate_are_dropped(db: Database) -> None:
    clock = FakeClock()
    pool = (tpl("d_a", "tracks", 5, 10), tpl("d_b", "tracks", 6, 10))
    engine = make_engine(db, *pool, clock=clock)
    key, picked = await engine.active_slate("daily")
    kept = next(t.id for t in pool if t.id not in {p.id for p in picked})
    await db.replace_challenge_slate("daily", key, [kept, "removed_from_pool"])
    fresh = make_engine(db, *pool, clock=clock)
    _, templates = await fresh.active_slate("daily")
    assert [t.id for t in templates] == [kept]


async def test_emit_never_raises_even_when_the_database_fails(
    db: Database, caplog: pytest.LogCaptureFixture
) -> None:
    engine = make_engine(db, tpl("d_voice", "voice_minutes", 5, 30))
    db.advance_challenge = AsyncMock(side_effect=RuntimeError("disk on fire"))  # type: ignore[method-assign]
    with caplog.at_level("ERROR"):
        engine.emit(VoiceMinutes(1, 5))
        tasks = list(engine._tasks)
        assert tasks
        await asyncio.gather(*tasks)  # a leaked exception would raise here
        await engine.close()
    assert any("Challenge engine failed" in r.getMessage() for r in caplog.records)


async def test_set_slate_validates_everything_and_changes_nothing_on_error(db: Database) -> None:
    engine = make_engine(
        db,
        tpl("d_a", "tracks", 5, 10),
        tpl("d_b", "voice_minutes", 5, 10, category="voice"),
        tpl("w_a", "tracks", 5, 10, period="weekly"),
    )
    _, before = await engine.active_slate("daily")
    bad_inputs = [
        [],
        ["nope"],
        ["d_a", "d_a"],
        ["w_a"],  # wrong period
        ["d_a"] * 50,
        ["d_а"],  # Cyrillic 'а'
        ["d_a", "d_b", "d_a", "d_b", "d_a", "d_b", "d_a"],
    ]
    for ids in bad_inputs:
        with pytest.raises(ValueError):
            await engine.set_slate("daily", ids)
    _, after = await engine.active_slate("daily")
    assert [t.id for t in after] == [t.id for t in before]

    chosen = await engine.set_slate("daily", ["d_b"])
    assert [t.id for t in chosen] == ["d_b"]
    assert [t.id for t in (await engine.active_slate("daily"))[1]] == ["d_b"]


async def test_reroll_keeps_retained_progress_and_picks_from_the_pool(db: Database) -> None:
    pool = (tpl("d_a", "voice_minutes", 50, 10), tpl("d_b", "voice_minutes", 50, 10))
    engine = make_engine(db, *pool)
    await engine.record(VoiceMinutes(1, 5))
    rerolled = await engine.reroll("daily")
    assert len(rerolled) == 1 and rerolled[0].id in {"d_a", "d_b"}
    assert (await engine.active_slate("daily"))[1] == rerolled


async def test_next_reset_matches_the_period(db: Database) -> None:
    engine = make_engine(db, tpl("d_a", "tracks", 5, 10))
    assert engine.next_reset("daily") - 0 > 0
    assert engine.next_reset("weekly") > engine.next_reset("daily")


async def test_after_close_emit_and_record_do_nothing(db: Database) -> None:
    engine = make_engine(db, tpl("d_tracks", "tracks", 1, 40))
    await engine.close()
    engine.emit(listened(1))
    assert not engine._tasks
    await engine.record(listened(1))
    assert await balance(db, 1) == 0
