"""The weekly draw is one atomic, idempotent transaction."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from yokai.storage.db import Database

NOW = 1_000_000
WEEK = 7 * 24 * 3600
DUE = NOW + WEEK


async def _db(tmp_path: Path) -> Database:
    db = Database(tmp_path / "draw.db")
    await db.initialize()
    for user_id in (1, 2, 3):
        await db.ensure_wallet(user_id, 1000)
    await db.ensure_lottery_schedule(NOW, WEEK)
    return db


async def _buy(db: Database, user_id: int, numbers: list[int]) -> None:
    await db.buy_lottery_tickets(
        user_id, numbers, price=100, max_per_user=10, welcome_bonus=0, now=NOW + 5
    )


async def _balance(db: Database, user_id: int) -> int:
    wallet = await db.get_wallet(user_id)
    assert wallet is not None
    return wallet.balance


@pytest.mark.asyncio
async def test_one_winner_takes_the_pot_and_the_next_draw_starts_empty(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7, 8])
        await _buy(db, 2, [9])
        draw = await db.run_lottery_draw(7, WEEK, now=DUE)
        assert draw is not None
        assert (draw.draw_no, draw.winning_number, draw.pot) == (1, 7, 300)
        assert (draw.winners, draw.prize_each, draw.announced) == ((1,), 300, False)
        assert await _balance(db, 1) == 800 + 300
        assert await db.get_pot() == 0
        state = await db.get_lottery_state()
        assert (state.draw_no, state.next_draw_ts) == (2, DUE + WEEK)
        assert await db.lottery_tickets_sold() == 0  # the new draw has no tickets
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_holders_of_the_winning_number_split_it_and_the_remainder_stays(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        await _buy(db, 2, [7])
        await _buy(db, 3, [7, 8])  # three tickets on 7 -> pot 400 is split three ways
        draw = await db.run_lottery_draw(7, WEEK, now=DUE)
        assert draw is not None
        assert draw.winners == (1, 2, 3) and draw.prize_each == 133
        assert await db.get_pot() == 1  # 400 - 3 * 133
        assert await _balance(db, 1) == 900 + 133
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_no_holder_rolls_the_pot_over_unchanged(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        draw = await db.run_lottery_draw(12, WEEK, now=DUE)
        assert draw is not None and draw.winners == () and draw.prize_each == 0
        assert await db.get_pot() == 100
        assert await _balance(db, 1) == 900
        async with db.conn.execute(
            "SELECT COUNT(*) FROM renown_transactions WHERE reason = 'win:lottery';"
        ) as cur:
            assert (await cur.fetchone())[0] == 0  # type: ignore[index]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_rolled_over_pot_is_won_by_a_later_draw_with_new_tickets_only(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])  # draw 1
        await db.run_lottery_draw(12, WEEK, now=DUE)  # nobody holds 12: the pot (100) rolls over
        await db.buy_lottery_tickets(  # draw 2
            2, [5], price=100, max_per_user=10, welcome_bonus=0, now=DUE + 10
        )
        # user 1's draw-1 ticket on 7 must not count in draw 2
        draw2 = await db.run_lottery_draw(7, WEEK, now=DUE + WEEK)
        assert draw2 is not None and draw2.draw_no == 2 and draw2.winners == ()
        assert await db.get_pot() == 200
        await db.buy_lottery_tickets(  # draw 3
            3, [5], price=100, max_per_user=10, welcome_bonus=0, now=DUE + WEEK + 10
        )
        # user 2's draw-2 ticket on 5 must not count in draw 3; user 3's new one does
        draw3 = await db.run_lottery_draw(5, WEEK, now=DUE + 2 * WEEK)
        assert draw3 is not None and draw3.draw_no == 3
        assert draw3.winners == (3,) and draw3.prize_each == 300  # the rolled-over 200 plus 100
        assert await _balance(db, 3) == 900 + 300 and await _balance(db, 2) == 900
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_draw_that_is_not_due_does_nothing(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        assert await db.run_lottery_draw(7, WEEK, now=DUE - 1) is None
        state = await db.get_lottery_state()
        assert (state.draw_no, state.next_draw_ts, state.pot) == (1, DUE, 100)
        assert await _balance(db, 1) == 900
        await _buy(db, 2, [3])  # the lock was released
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_drawing_twice_pays_once(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        first = await db.run_lottery_draw(7, WEEK, now=DUE)
        second = await db.run_lottery_draw(7, WEEK, now=DUE)
        assert first is not None and second is None
        assert await _balance(db, 1) == 900 + 100
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_offline_bot_draws_once_and_the_schedule_jumps_to_the_future(
    tmp_path: Path,
) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        late = DUE + 3 * WEEK + 500  # offline for three and a bit weeks
        draw = await db.run_lottery_draw(7, WEEK, now=late)
        assert draw is not None
        state = await db.get_lottery_state()
        assert state.next_draw_ts == DUE + 4 * WEEK and state.next_draw_ts > late
        assert await db.run_lottery_draw(7, WEEK, now=late) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_the_ledger_records_each_prize(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        await _buy(db, 2, [7])
        await db.run_lottery_draw(7, WEEK, now=DUE)
        async with db.conn.execute(
            "SELECT user_id, delta, reason FROM renown_transactions "
            "WHERE reason = 'win:lottery' ORDER BY user_id;"
        ) as cur:
            assert await cur.fetchall() == [(1, 100, "win:lottery"), (2, 100, "win:lottery")]
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_failure_part_way_leaves_the_draw_undone(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        real = db._insert_transaction
        calls = {"n": 0}

        async def failing(*args, **kwargs):  # type: ignore[no-untyped-def]
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("boom")
            return await real(*args, **kwargs)

        with patch.object(db, "_insert_transaction", new=failing):
            with pytest.raises(RuntimeError):
                await db.run_lottery_draw(7, WEEK, now=DUE)
        state = await db.get_lottery_state()
        assert (state.draw_no, state.pot) == (1, 100)
        assert await _balance(db, 1) == 900
        assert await db.get_last_draw() is None
        draw = await db.run_lottery_draw(7, WEEK, now=DUE)  # and it can still be drawn
        assert draw is not None and draw.winners == (1,)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_announcements_are_tracked_until_marked(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        await _buy(db, 1, [7])
        await db.run_lottery_draw(7, WEEK, now=DUE)
        pending = await db.get_unannounced_draws()
        assert [d.draw_no for d in pending] == [1]
        await db.mark_draw_announced(1)
        assert await db.get_unannounced_draws() == []
        last = await db.get_last_draw()
        assert last is not None and last.announced is True and last.winners == (1,)
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_a_draw_before_the_clock_has_started_does_nothing(tmp_path: Path) -> None:
    db = Database(tmp_path / "noclock.db")
    await db.initialize()
    try:
        assert await db.run_lottery_draw(7, WEEK, now=DUE) is None
    finally:
        await db.close()


@pytest.mark.asyncio
async def test_an_empty_draw_is_still_recorded_and_advances(tmp_path: Path) -> None:
    db = await _db(tmp_path)
    try:
        draw = await db.run_lottery_draw(3, WEEK, now=DUE)
        assert draw is not None and draw.winners == () and draw.pot == 0
        assert (await db.get_lottery_state()).draw_no == 2
    finally:
        await db.close()
