"""The player tells the challenge engine who heard a finished track."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from tests.test_player_reliability import make_player, make_track
from yokai.challenges.models import TrackListened
from yokai.music.models import Track


def _member(user_id: int, bot: bool = False) -> MagicMock:
    member = MagicMock()
    member.id = user_id
    member.bot = bot
    return member


def _connect(player, members: list[MagicMock], *, afk: bool = False, connected: bool = True):
    channel = MagicMock()
    channel.id = 77
    channel.members = members
    channel.guild.afk_channel = SimpleNamespace(id=77) if afk else None
    player.voice_client = SimpleNamespace(is_connected=lambda: connected, channel=channel)
    player.text_channel = SimpleNamespace(id=555)


async def test_a_completed_track_credits_every_human_with_company() -> None:
    player, _, bot = make_player()
    _connect(player, [_member(1), _member(2), _member(99, bot=True)])
    track = make_track("vid1")
    track.artist = "Band"

    player._emit_challenge(track, "completed", 180)

    bot.challenges.emit.assert_called_once_with(
        TrackListened(user_ids=(1, 2), video_id="vid1", artist="Band", channel_id=555)
    )


async def test_spotify_derived_tracks_never_pass_their_artist() -> None:
    player, _, bot = make_player()
    _connect(player, [_member(1), _member(2)])
    by_id = make_track("vid1")
    by_id.artist = "Spotify Artist"
    by_id.spotify_id = "sp1"
    by_origin = make_track("vid2")
    by_origin.artist = "Spotify Artist"
    by_origin.origin = "import"

    player._emit_challenge(by_id, "completed", 180)
    player._emit_challenge(by_origin, "completed", 180)

    emitted = [call.args[0] for call in bot.challenges.emit.call_args_list]
    assert emitted == [
        TrackListened(user_ids=(1, 2), video_id="vid1", artist=None, channel_id=555),
        TrackListened(user_ids=(1, 2), video_id="vid2", artist=None, channel_id=555),
    ]


async def test_skip_counts_only_past_half() -> None:
    player, _, bot = make_player()
    _connect(player, [_member(1), _member(2)])
    player._emit_challenge(make_track("a", duration_s=200), "skipped", 99)
    bot.challenges.emit.assert_not_called()
    player._emit_challenge(make_track("b", duration_s=200), "skipped", 100)
    assert bot.challenges.emit.call_count == 1


async def test_nothing_is_emitted_alone_stopped_afk_disconnected_or_unmatched() -> None:
    player, _, bot = make_player()
    track = make_track("vid1")

    _connect(player, [_member(1), _member(99, bot=True)])  # one human plus the bot
    player._emit_challenge(track, "completed", 180)

    _connect(player, [_member(1), _member(2)])
    player._emit_challenge(track, "stopped", 180)
    player._emit_challenge(track, "error", 180)

    _connect(player, [_member(1), _member(2)], afk=True)
    player._emit_challenge(track, "completed", 180)

    _connect(player, [_member(1), _member(2)], connected=False)
    player._emit_challenge(track, "completed", 180)

    _connect(player, [_member(1), _member(2)])
    player._emit_challenge(Track(video_id="sp:abc", title="x"), "completed", 180)
    player._emit_challenge(
        Track(video_id="pending", title="x", is_pending_match=True), "completed", 180
    )

    player.voice_client = None
    player._emit_challenge(track, "completed", 180)

    bot.challenges.emit.assert_not_called()


async def test_an_engine_error_never_reaches_playback() -> None:
    player, _, bot = make_player()
    _connect(player, [_member(1), _member(2)])
    bot.challenges.emit.side_effect = RuntimeError("engine bug")
    player._emit_challenge(make_track("vid1"), "completed", 180)  # must not raise


async def test_retiring_a_track_emits_once() -> None:
    player, _, bot = make_player()
    _connect(player, [_member(1), _member(2)])
    player.current_track = make_track("vid1")
    player._retire_current("completed", listened=180)
    bot.challenges.emit.assert_called_once()
