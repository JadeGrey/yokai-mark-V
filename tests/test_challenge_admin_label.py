"""The admin panel shows challenge payouts in plain words."""

from __future__ import annotations

from yokai_admin.modules.users.service import reason_label


def test_challenge_reason_is_labelled() -> None:
    assert reason_label("challenge:d_music_3") == "Challenge (d_music_3)"
