"""Tests for promotion trials in ranks.toml, the synthetic promotion mission and the new errors."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from tests.rpg_helpers import RANKS, mutate, write_content_dir
from yokai.config import load_config
from yokai.errors import (
    PromotionCooldownError,
    PromotionNotReadyError,
    PromotionStaleError,
    TopRankError,
    YokaiError,
)
from yokai.rpg.content import ContentError, load_content
from yokai.rpg.models import PROMOTION_KIND


def test_sample_trials_load_with_defaults_and_overrides(tmp_path: Path) -> None:
    content = load_content(write_content_dir(tmp_path))
    assert content.ranks[0].promotion is None
    claws = content.ranks[1].promotion
    assert claws is not None and claws.name == "Trial of Claws"
    assert (claws.attempt_cost, claws.power) == (2, 100)
    assert claws.stages[0].enemies == ("cat",) and claws.stages[0].level == 5
    fangs = content.ranks[2].promotion
    assert fangs is not None and (fangs.attempt_cost, fangs.power) == (3, 120)
    assert len(fangs.stages) == 2


def test_the_promotion_mission_is_a_playable_mission(tmp_path: Path) -> None:
    content = load_content(write_content_dir(tmp_path))
    assert content.promotion_mission("E") is None
    mission = content.promotion_mission("C")
    assert mission is not None
    assert (mission.id, mission.kind, mission.rank) == ("promotion_C", PROMOTION_KIND, "C")
    assert (mission.name, mission.attempt_cost, mission.power) == ("Trial of Fangs", 3, 120)
    assert mission.top_level == 13
    with pytest.raises(KeyError):
        content.promotion_mission("Z")
    assert "promotion_C" not in content.missions  # trials never appear on the board


@pytest.mark.parametrize(
    ("old", "new", "message"),
    [
        ('name = "Trial of Claws"', 'name = ""', "name must be non-empty text"),
        ('stages = [{ enemies = ["cat"], level = 5 }]', "stages = []", "stages must be a list"),
        ('enemies = ["cat"], level = 5', 'enemies = ["ghost"], level = 5', "unknown enemy: ghost"),
        ('enemies = ["cat"], level = 5', 'enemies = ["cat"], level = 99', "must be at most 60"),
        (
            'name = "Trial of Fangs"\nattempt_cost = 3',
            'name = "Trial of Fangs"\nattempt_cost = 0',
            "attempt_cost",
        ),
        ("power = 120", "power = 5", "power must be at least 10"),
    ],
)
def test_bad_trials_are_rejected(tmp_path: Path, old: str, new: str, message: str) -> None:
    with pytest.raises(ContentError, match=message):
        load_content(write_content_dir(tmp_path, ranks=mutate(RANKS, old, new)))


def test_every_rank_after_the_first_needs_a_trial_and_the_first_cannot_have_one(
    tmp_path: Path,
) -> None:
    without = RANKS.replace(
        '\n[rank.promotion]\nname = "Trial of Claws"\n'
        'stages = [{ enemies = ["cat"], level = 5 }]\n',
        "",
    )
    assert without != RANKS
    with pytest.raises(ContentError, match=r"needs a \[rank.promotion\] trial"):
        load_content(write_content_dir(tmp_path, ranks=without))
    first = mutate(
        RANKS,
        'id = "E"\nmin_level = 1\nxp_share = 0.1\n',
        'id = "E"\nmin_level = 1\nxp_share = 0.1\n\n[rank.promotion]\nname = "x"\n'
        'stages = [{ enemies = ["cat"], level = 1 }]\n',
    )
    with pytest.raises(ContentError, match="cannot have a promotion trial"):
        load_content(write_content_dir(tmp_path, ranks=first))


def test_the_shipped_trials_sit_at_their_rank_thresholds() -> None:
    content = load_content(Path("content/rpg"))
    for rank in content.ranks[1:]:
        mission = content.promotion_mission(rank.id)
        assert mission is not None and mission.kind == PROMOTION_KIND
        levels = [stage.level for stage in mission.stages]
        assert rank.min_level <= min(levels) and max(levels) <= rank.min_level + 5, rank.id
        assert 1 <= len(mission.stages) <= 3 and 20 <= mission.power <= 400
        assert mission.attempt_cost >= 2


def test_promotion_errors_carry_useful_text() -> None:
    not_ready = PromotionNotReadyError("D", 5, 3)
    assert "Rank D opens at level 5" in not_ready.message and "level 3" in not_ready.message
    cooldown = PromotionCooldownError(1_900_000_000)
    assert "<t:1900000000:R>" in (cooldown.user_hint or "") and cooldown.until_ts == 1_900_000_000
    for error in (PromotionStaleError(), TopRankError(), not_ready, cooldown):
        assert isinstance(error, YokaiError) and error.title and error.message


def test_the_promotion_cooldown_setting() -> None:
    base = {"DISCORD_TOKEN": "t.o.k", "GUILD_ID": "1", "OWNER_ID": "2"}
    with patch.dict(os.environ, base, clear=True):
        assert load_config(env_path=Path("nope")).rpg_promotion_cooldown_hours == 6
    with patch.dict(os.environ, {**base, "RPG_PROMOTION_COOLDOWN_HOURS": "12"}, clear=True):
        assert load_config(env_path=Path("nope")).rpg_promotion_cooldown_hours == 12
    with patch.dict(os.environ, {**base, "RPG_PROMOTION_COOLDOWN_HOURS": "-4"}, clear=True):
        assert load_config(env_path=Path("nope")).rpg_promotion_cooldown_hours == 0
