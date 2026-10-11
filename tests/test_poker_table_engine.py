"""The multi-hand poker table: blinds from the buy-in, the button, busts, leavers, timeouts."""

from __future__ import annotations

import random

import pytest

from yokai.errors import NotAtTableError
from yokai.gambling.engines.poker import HandResult
from yokai.gambling.engines.pokertable import (
    IDLE_LIMIT,
    CashOut,
    PokerTable,
    blinds_for,
)


def make_table(users=(1, 2, 3), buy_in: int = 100, max_hands: int = 30) -> PokerTable:  # type: ignore[no-untyped-def]
    return PokerTable(list(users), buy_in, max_hands, lambda: random.Random(7))


def result(stacks: dict[int, int]) -> HandResult:
    return HandResult(stacks=stacks, pots=(), shown={}, ranks={}, showdown=False)


@pytest.mark.parametrize(
    ("buy_in", "blinds"),
    [(10, (1, 2)), (50, (1, 2)), (100, (1, 2)), (150, (2, 3)), (1000, (10, 20)), (5000, (50, 100))],
)
def test_blinds_are_one_and_two_percent_rounded_up_with_a_floor(
    buy_in: int, blinds: tuple[int, int]
) -> None:
    assert blinds_for(buy_in) == blinds


def test_a_new_table_gives_everyone_the_same_stack_and_the_blinds() -> None:
    table = make_table(buy_in=1000)
    assert table.stacks == {1: 1000, 2: 1000, 3: 1000}
    assert (table.small_blind, table.big_blind) == (10, 20)
    assert table.seated == [1, 2, 3] and table.hand_no == 0 and not table.is_finished


def test_bad_tables_are_rejected() -> None:
    with pytest.raises(ValueError):
        PokerTable([1], 100, 30, lambda: random.Random(1))
    with pytest.raises(ValueError):
        PokerTable([1, 2], 0, 30, lambda: random.Random(1))
    with pytest.raises(ValueError):
        PokerTable([1, 2], 100, 0, lambda: random.Random(1))


def test_the_button_starts_with_the_first_seat_and_moves_left_each_hand() -> None:
    table = make_table()
    buttons = []
    for _ in range(4):
        hand = table.start_hand()
        buttons.append(hand.button)
        table.finish_hand(result(dict(table.stacks)))
    assert buttons == [1, 2, 3, 1]


def test_each_hand_deals_the_current_stacks_with_the_table_blinds() -> None:
    table = make_table(buy_in=1000)
    hand = table.start_hand()
    assert table.hand_no == 1
    assert hand.order == [1, 2, 3] and hand.big_blind == 20
    assert hand.stacks[2] == 990 and hand.stacks[3] == 980  # small blind 2, big blind 3


def test_finishing_a_hand_moves_the_chips() -> None:
    table = make_table()
    table.start_hand()
    table.finish_hand(result({1: 160, 2: 70, 3: 70}))
    assert table.stacks == {1: 160, 2: 70, 3: 70}


def test_finishing_a_hand_that_changes_the_chip_total_is_refused() -> None:
    table = make_table()
    table.start_hand()
    with pytest.raises(ValueError):
        table.finish_hand(result({1: 200, 2: 100, 3: 100}))
    assert table.stacks == {1: 100, 2: 100, 3: 100}


def test_a_busted_player_is_cashed_out_for_nothing_and_leaves_the_table() -> None:
    table = make_table()
    table.start_hand()
    table.finish_hand(result({1: 200, 2: 100, 3: 0}))
    assert table.take_cash_outs() == [CashOut(3, 0, "bust")]
    assert table.seated == [1, 2] and 3 not in table.stacks
    assert table.take_cash_outs() == []


def test_a_player_who_left_is_cashed_out_with_their_chips_after_the_hand() -> None:
    table = make_table()
    table.start_hand()
    table.request_leave(2)  # pressed Leave while the hand was running
    table.finish_hand(result({1: 130, 2: 90, 3: 80}))
    assert table.take_cash_outs() == [CashOut(2, 90, "left")]
    assert table.seated == [1, 3]


def test_leaving_needs_a_seat() -> None:
    table = make_table()
    with pytest.raises(NotAtTableError):
        table.request_leave(9)


def test_the_button_skips_seats_that_are_gone() -> None:
    table = make_table(users=(1, 2, 3, 4))
    table.start_hand()  # button 1
    table.finish_hand(result({1: 100, 2: 0, 3: 100, 4: 200}))
    table.take_cash_outs()
    assert table.start_hand().button == 3  # 2 busted, so the button passes it


def test_consecutive_timeouts_remove_a_player_but_an_action_resets_the_count() -> None:
    table = make_table()
    assert IDLE_LIMIT == 2
    assert table.record_timeout(1) is False
    table.record_action(1)
    assert table.record_timeout(1) is False
    assert table.record_timeout(1) is True
    assert 1 in table.leaving
    assert table.take_cash_outs() == [CashOut(1, 100, "inactive")]


def test_the_table_ends_at_the_hand_cap() -> None:
    table = make_table(max_hands=2)
    for _ in range(2):
        assert not table.is_finished
        table.start_hand()
        table.finish_hand(result(dict(table.stacks)))
    assert table.is_finished


def test_the_table_ends_when_fewer_than_two_players_are_left() -> None:
    table = make_table(users=(1, 2))
    table.start_hand()
    table.finish_hand(result({1: 200, 2: 0}))
    table.take_cash_outs()
    assert table.is_finished and table.final_payouts() == {1: 200}


def test_starting_a_hand_on_a_finished_table_is_refused() -> None:
    table = make_table(max_hands=1)
    table.start_hand()
    table.finish_hand(result(dict(table.stacks)))
    with pytest.raises(ValueError):
        table.start_hand()


def test_starting_a_hand_with_a_broke_or_leaving_seat_still_listed_is_refused() -> None:
    table = make_table()
    table.start_hand()
    table.finish_hand(result({1: 200, 2: 100, 3: 0}))
    with pytest.raises(ValueError):
        table.start_hand()  # player 3 must be cashed out first


def test_chips_are_conserved_across_a_whole_session() -> None:
    table = make_table(users=(1, 2, 3, 4), buy_in=500, max_hands=40)
    rng = random.Random(3)
    paid = 0
    while not table.is_finished:
        for cash in table.take_cash_outs():
            paid += cash.chips
        if table.is_finished:
            break
        hand = table.start_hand()
        while not hand.is_over:
            user = hand.current
            assert user is not None
            if rng.random() < 0.4:
                hand.all_in(user)
            elif hand.legal_actions(user).can_check:
                hand.check(user)
            else:
                hand.call(user)
        table.finish_hand(hand.result())
        if rng.random() < 0.2 and len(table.seated) > 2:
            table.request_leave(rng.choice(table.seated))
    paid += sum(c.chips for c in table.take_cash_outs())
    paid += sum(table.final_payouts().values())
    assert paid == 4 * 500
