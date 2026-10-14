"""Challenge embeds: content, completion marks and Discord size limits."""

from __future__ import annotations

from tests.embed_helpers import emoji_count, total_embed_length
from yokai.ui.embeds import EmbedFactory


def _rows():
    return [
        ("Listen to 5 tracks in a call", 3, 5, 40, False),
        ("Play 3 gambling rounds", 3, 3, 30, True),
        ("Spend 20 minutes in voice with company", 0, 20, 30, False),
    ]


def test_board_shows_titles_progress_rewards_and_the_reset_timer() -> None:
    embed = EmbedFactory.challenge_board(period="daily", rows=_rows(), resets_ts=1_800_000_000)
    text = embed.description or ""
    assert "Daily" in (embed.title or "")
    for title in ("Listen to 5 tracks in a call", "Play 3 gambling rounds"):
        assert title in text
    assert "3/5" in text and "0/20" in text and "+40" in text
    assert "<t:1800000000:R>" in text


def test_board_marks_finished_challenges_in_words() -> None:
    text = (
        EmbedFactory.challenge_board(
            period="weekly", rows=_rows(), resets_ts=1_800_000_000
        ).description
        or ""
    )
    done_line = next(line for line in text.splitlines() if "3/3" in line)
    assert "done" in done_line.lower()
    open_line = next(line for line in text.splitlines() if "3/5" in line)
    assert "done" not in open_line.lower()


def test_board_with_no_rows_says_so() -> None:
    embed = EmbedFactory.challenge_board(period="daily", rows=[], resets_ts=1_800_000_000)
    assert "no challenges" in (embed.description or "").lower()


def test_board_stays_inside_discords_limits() -> None:
    rows = [("x" * 400, 1, 2, 3, False)] * 6
    embed = EmbedFactory.challenge_board(period="daily", rows=rows, resets_ts=1_800_000_000)
    assert len(embed) <= 6000 and len(embed.description or "") <= 4096
    assert total_embed_length(embed) <= 6000


def test_notice_lists_lines_and_the_overflow_count() -> None:
    embed = EmbedFactory.challenge_notice(lines=["<@1> finished **A** · +30 Renown"], extra=3)
    assert "<@1> finished **A**" in (embed.description or "")
    assert "3 more" in (embed.description or "")
    plain = EmbedFactory.challenge_notice(lines=["a"])
    assert "more" not in (plain.description or "")


def test_each_embed_carries_at_most_one_emoji() -> None:
    board = EmbedFactory.challenge_board(period="daily", rows=_rows(), resets_ts=1_800_000_000)
    notice = EmbedFactory.challenge_notice(lines=["a"], extra=2)
    assert emoji_count(board) <= 1 and emoji_count(notice) <= 1
