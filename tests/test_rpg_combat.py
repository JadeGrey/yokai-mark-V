"""Tests for the pure combat engine, enemy AI, builders and log formatting."""

from __future__ import annotations

import random
from pathlib import Path
from typing import Optional

import pytest

from yokai.errors import InvalidCombatActionError
from yokai.rpg.combat.builders import (
    MAX_FOES,
    build_stage_foes,
    build_stages,
    enemy_stats,
    player_combatant,
)
from yokai.rpg.combat.describe import describe, describe_all
from yokai.rpg.combat.engine import Fight
from yokai.rpg.combat.models import (
    ACTION_ATTACK,
    ACTION_DEFEND,
    ACTION_MAGIC,
    ACTION_SKILL,
    OUTCOME_LOST,
    OUTCOME_WON,
    SIDE_FOES,
    SIDE_PLAYERS,
    Action,
    Combatant,
    Event,
    Status,
)
from yokai.rpg.content import load_content
from yokai.rpg.models import CombatRules, SkillDef, Stage, Stats
from yokai.rpg.stats import stats_at_level

RULES = CombatRules(
    defense_k=40,
    crit_multiplier_percent=150,
    crit_cap=100,
    variance_percent=0,
    defend_percent=50,
    stage_recovery_percent=10,
    max_rounds=40,
)

SKILLS = {
    "smash": SkillDef("smash", "Smash", "damage", "foe", scaling="attack", power=200, cooldown=3),
    "cleave": SkillDef("cleave", "Cleave", "damage", "all_foes", scaling="attack", power=100),
    "burn": SkillDef("burn", "Burn", "dot", "foe", scaling="magic", power=50, duration=3),
    "sap": SkillDef(
        "sap", "Sap", "debuff", "foe", stat="attack", percent=50, duration=2, cooldown=2
    ),
    "rally": SkillDef(
        "rally", "Rally", "buff", "all_allies", stat="attack", percent=50, duration=2
    ),
    "mend": SkillDef("mend", "Mend", "heal", "ally", scaling="magic", power=100),
    "purify": SkillDef("purify", "Purify", "cleanse", "ally"),
    "hide": SkillDef("hide", "Hide", "buff", "self", stat="defense", percent=100, duration=2),
}


def fighter(
    fid: str,
    side: str = SIDE_PLAYERS,
    *,
    hp: int = 100,
    attack: int = 40,
    magic: int = 40,
    defense: int = 0,
    resist: int = 0,
    speed: int = 10,
    crit: int = 0,
    skills: tuple[str, ...] = (),
    actions: tuple[tuple[str, int], ...] = (("attack", 1),),
    target: str = "random",
    boss: bool = False,
) -> Combatant:
    return Combatant(
        id=fid,
        name=fid.upper(),
        side=side,
        stats=Stats(hp, attack, magic, defense, resist, speed, crit),
        hp=hp,
        skills=skills,
        user_id=int(fid[1:]) if side == SIDE_PLAYERS and fid[1:].isdigit() else None,
        boss=boss,
        ai_actions=actions,
        ai_target=target,
    )


def hero(n: int = 1, **kw: object) -> Combatant:
    return fighter(f"p{n}", SIDE_PLAYERS, **kw)  # type: ignore[arg-type]


def foe(n: int = 1, **kw: object) -> Combatant:
    return fighter(f"f{n}", SIDE_FOES, **kw)  # type: ignore[arg-type]


def make_fight(
    players: list[Combatant],
    stages: list[list[Combatant]],
    *,
    rules: CombatRules = RULES,
    seed: int = 1,
) -> Fight:
    return Fight(players, stages, SKILLS, rules, random.Random(seed))


def passive_foe(n: int = 1, **kw: object) -> Combatant:
    """A foe that never hurts anyone much: attack 0 still deals the 1-damage minimum."""
    kw.setdefault("attack", 0)
    return foe(n, **kw)


def test_a_basic_attack_deals_attack_times_mitigation() -> None:
    fight = make_fight([hero(attack=40)], [[passive_foe(hp=100, defense=0)]])
    fight.submit("p1", Action(ACTION_ATTACK))
    events = fight.resolve_round()
    assert fight.foes[0].hp == 60
    assert events[0].kind == "attack" and events[0].amount == 40


def test_defense_halves_damage_at_defense_k() -> None:
    fight = make_fight([hero(attack=40)], [[passive_foe(hp=100, defense=40)]])
    fight.submit("p1", Action(ACTION_ATTACK))
    fight.resolve_round()
    assert fight.foes[0].hp == 80


def test_magic_uses_magic_against_resist() -> None:
    fight = make_fight([hero(attack=0, magic=40)], [[passive_foe(hp=100, defense=999, resist=40)]])
    fight.submit("p1", Action(ACTION_MAGIC))
    fight.resolve_round()
    assert fight.foes[0].hp == 80


def test_a_crit_multiplies_damage_and_is_flagged() -> None:
    fight = make_fight([hero(attack=40, crit=100)], [[passive_foe(hp=200)]])
    fight.submit("p1", Action(ACTION_ATTACK))
    events = fight.resolve_round()
    assert events[0].crit is True and events[0].amount == 60
    assert fight.foes[0].hp == 140


def test_the_crit_cap_limits_the_chance() -> None:
    capped = CombatRules(40, 150, 0, 0, 50, 10, 40)
    fight = make_fight([hero(crit=100)], [[passive_foe(hp=500)]], rules=capped)
    for _ in range(10):
        fight.submit("p1", Action(ACTION_ATTACK))
        events = fight.resolve_round()
        assert not events[0].crit


def test_damage_is_always_at_least_one() -> None:
    fight = make_fight([hero(attack=0)], [[passive_foe(hp=10, defense=1000)]])
    fight.submit("p1", Action(ACTION_ATTACK))
    fight.resolve_round()
    assert fight.foes[0].hp == 9


def test_defending_halves_damage_and_ends_with_the_round() -> None:
    fight = make_fight([hero(hp=100)], [[foe(attack=40, hp=500)]])
    fight.submit("p1", Action(ACTION_DEFEND))
    fight.resolve_round()
    assert fight.players[0].hp == 80
    assert fight.players[0].defending is False
    fight.submit("p1", Action(ACTION_ATTACK))
    fight.resolve_round()
    assert fight.players[0].hp == 40


def test_a_missing_action_defaults_to_defend() -> None:
    fight = make_fight([hero(hp=100)], [[foe(attack=40, hp=500)]])
    events = fight.resolve_round()
    assert events[0].kind == "defend"
    assert fight.players[0].hp == 80


def test_faster_players_act_first_and_a_cleared_stage_stops_the_round() -> None:
    slow = hero(1, speed=5)
    fast = hero(2, speed=20)
    fight = make_fight([slow, fast], [[passive_foe(hp=10)]])
    fight.submit("p1", Action(ACTION_ATTACK))
    fight.submit("p2", Action(ACTION_ATTACK))
    events = fight.resolve_round()
    attacks = [e for e in events if e.kind == "attack"]
    assert [e.actor for e in attacks] == ["P2"]
    assert fight.outcome == OUTCOME_WON


def test_effective_speed_includes_status_modifiers() -> None:
    a = hero(1, speed=20)
    b = hero(2, speed=15)
    a.statuses.append(Status("x", "Slow", "debuff", 2, stat="speed", percent=-50))
    fight = make_fight([a, b], [[passive_foe(hp=1000)]])
    fight.submit("p1", Action(ACTION_ATTACK))
    fight.submit("p2", Action(ACTION_ATTACK))
    events = fight.resolve_round()
    assert [e.actor for e in events if e.kind == "attack"][:2] == ["P2", "P1"]


def test_speed_ties_are_seeded_and_repeatable() -> None:
    def run(seed: int) -> list[str]:
        fight = make_fight([hero(1), hero(2), hero(3)], [[passive_foe(hp=10_000)]], seed=seed)
        for n in (1, 2, 3):
            fight.submit(f"p{n}", Action(ACTION_ATTACK))
        return [e.actor for e in fight.resolve_round() if e.kind == "attack"][:3]

    assert run(7) == run(7)
    assert len({tuple(run(seed)) for seed in range(20)}) > 1


@pytest.mark.parametrize(
    ("actor", "action", "message"),
    [
        ("nobody", Action(ACTION_ATTACK), "not in this fight"),
        ("p1", Action("dance"), "isn't an action"),
        ("p1", Action(ACTION_SKILL, skill_id="smash"), "don't know that skill"),
        ("p1", Action(ACTION_SKILL, skill_id="missing"), "don't know that skill"),
        ("p1", Action(ACTION_ATTACK, target_id="f9"), "not standing"),
    ],
)
def test_submit_rejects_illegal_actions(actor: str, action: Action, message: str) -> None:
    fight = make_fight([hero()], [[foe(hp=100)]])
    with pytest.raises(InvalidCombatActionError, match=message):
        fight.submit(actor, action)


def test_a_downed_player_cannot_submit_and_a_finished_fight_rejects_everything() -> None:
    fight = make_fight([hero(1, hp=1), hero(2, hp=100)], [[foe(attack=1000, hp=10_000)]])
    fight.players[0].hp = 0
    with pytest.raises(InvalidCombatActionError, match="down"):
        fight.submit("p1", Action(ACTION_ATTACK))
    fight.outcome = OUTCOME_LOST
    with pytest.raises(InvalidCombatActionError, match="over"):
        fight.submit("p2", Action(ACTION_ATTACK))
    with pytest.raises(InvalidCombatActionError, match="over"):
        fight.resolve_round()


def test_a_skill_uses_its_power_and_then_goes_on_cooldown() -> None:
    fight = make_fight([hero(attack=40, skills=("smash",))], [[passive_foe(hp=1000)]])
    fight.submit("p1", Action(ACTION_SKILL, skill_id="smash"))
    events = fight.resolve_round()
    assert events[0].kind == "skill_damage" and events[0].amount == 80
    assert fight.players[0].cooldowns["smash"] == 3
    with pytest.raises(InvalidCombatActionError, match="3 more round"):
        fight.submit("p1", Action(ACTION_SKILL, skill_id="smash"))
    assert fight.usable_skills(fight.players[0]) == []


def test_a_cooldown_of_three_blocks_exactly_three_following_rounds() -> None:
    fight = make_fight([hero(skills=("smash",))], [[passive_foe(hp=100_000)]])
    available: list[bool] = []
    fight.submit("p1", Action(ACTION_SKILL, skill_id="smash"))
    fight.resolve_round()
    for _ in range(5):
        available.append(bool(fight.usable_skills(fight.players[0])))
        fight.resolve_round()
    assert available == [False, False, False, True, True]


def test_an_all_foes_skill_hits_every_living_foe() -> None:
    foes = [passive_foe(1, hp=100), passive_foe(2, hp=100), passive_foe(3, hp=100)]
    foes[2].hp = 0
    fight = make_fight([hero(skills=("cleave",))], [foes])
    fight.submit("p1", Action(ACTION_SKILL, skill_id="cleave"))
    fight.resolve_round()
    assert [f.hp for f in foes] == [60, 60, 0]


def test_damage_over_time_ticks_each_round_for_its_duration() -> None:
    fight = make_fight([hero(magic=40, skills=("burn",))], [[passive_foe(hp=1000)]])
    fight.submit("p1", Action(ACTION_SKILL, skill_id="burn"))
    events = fight.resolve_round()
    assert any(e.kind == "dot_apply" and e.amount == 20 for e in events)
    assert sum(1 for e in events if e.kind == "dot_tick") == 1
    for _ in range(4):
        fight.resolve_round()
    assert fight.foes[0].hp == 1000 - 20 * 3
    assert fight.foes[0].statuses == []


def test_reapplying_a_dot_refreshes_instead_of_stacking() -> None:
    fight = make_fight([hero(magic=40, skills=("burn",))], [[passive_foe(hp=10_000)]])
    for _ in range(2):
        fight.submit("p1", Action(ACTION_SKILL, skill_id="burn"))
        fight.resolve_round()
    assert len(fight.foes[0].statuses) == 1
    assert fight.foes[0].statuses[0].remaining == 2


def test_a_dot_that_kills_at_the_end_of_the_round_wins_the_fight() -> None:
    target = passive_foe(hp=15)
    fight = make_fight([hero(magic=40, attack=0, skills=("burn",))], [[target]])
    fight.submit("p1", Action(ACTION_SKILL, skill_id="burn"))
    fight.resolve_round()
    assert target.hp == 0 and fight.outcome == OUTCOME_WON


def test_a_debuff_lowers_effective_stats_until_it_expires() -> None:
    enemy = passive_foe(hp=1000, attack=40)
    fight = make_fight([hero(skills=("sap",))], [[enemy]])
    fight.submit("p1", Action(ACTION_SKILL, skill_id="sap"))
    fight.resolve_round()
    assert enemy.effective("attack") == 20
    assert enemy.effective("hp") == 1000
    fight.resolve_round()
    assert enemy.effective("attack") == 40


def test_a_buff_raises_effective_stats_for_the_whole_party() -> None:
    a, b = hero(1, skills=("rally",)), hero(2)
    fight = make_fight([a, b], [[passive_foe(hp=10_000)]])
    fight.submit("p1", Action(ACTION_SKILL, skill_id="rally"))
    fight.resolve_round()
    assert a.effective("attack") == 60 and b.effective("attack") == 60


def test_stacked_debuffs_never_push_a_stat_below_zero() -> None:
    target = hero()
    target.statuses = [
        Status(f"s{i}", "x", "debuff", 2, stat="attack", percent=-80) for i in range(3)
    ]
    assert target.effective("attack") == 0


def test_a_heal_targets_the_most_hurt_ally_and_never_overheals() -> None:
    healer = hero(1, magic=40, skills=("mend",))
    hurt = hero(2, hp=100)
    hurt.hp = 90
    fight = make_fight([healer, hurt], [[passive_foe(hp=10_000)]])
    fight.submit("p1", Action(ACTION_SKILL, skill_id="mend"))
    events = fight.resolve_round()
    heal = next(e for e in events if e.kind == "heal")
    assert heal.target == "P2" and heal.amount == 10
    assert hurt.hp <= hurt.max_hp


def test_a_heal_cannot_target_a_downed_ally() -> None:
    healer = hero(1, skills=("mend",))
    downed = hero(2)
    downed.hp = 0
    fight = make_fight([healer, downed], [[passive_foe(hp=10_000)]])
    with pytest.raises(InvalidCombatActionError, match="can't be targeted"):
        fight.submit("p1", Action(ACTION_SKILL, skill_id="mend", target_id="p2"))
    healer.hp = 50
    fight.submit("p1", Action(ACTION_SKILL, skill_id="mend"))
    events = fight.resolve_round()
    assert next(e for e in events if e.kind == "heal").target == "P1"


def test_a_cleanse_removes_harmful_statuses_but_keeps_buffs() -> None:
    target = hero(2)
    target.statuses = [
        Status("burn", "Burn", "dot", 3, damage=5),
        Status("sap", "Sap", "debuff", 2, stat="attack", percent=-50),
        Status("rally", "Rally", "buff", 2, stat="attack", percent=50),
    ]
    fight = make_fight([hero(1, skills=("purify",)), target], [[passive_foe(hp=10_000)]])
    fight.submit("p1", Action(ACTION_SKILL, skill_id="purify", target_id="p2"))
    events = fight.resolve_round()
    assert [s.key for s in target.statuses] == ["rally"]
    assert next(e for e in events if e.kind == "cleanse").amount == 2


def test_a_self_buff_applies_to_the_caster_only() -> None:
    a, b = hero(1, skills=("hide",)), hero(2)
    fight = make_fight([a, b], [[passive_foe(hp=10_000)]])
    fight.submit("p1", Action(ACTION_SKILL, skill_id="hide"))
    fight.resolve_round()
    assert a.effective("defense") == 0 and a.statuses and not b.statuses


def test_a_player_is_downed_and_the_fight_is_lost_when_everyone_falls() -> None:
    fight = make_fight([hero(hp=10)], [[foe(attack=1000, hp=10_000)]])
    fight.submit("p1", Action(ACTION_ATTACK))
    events = fight.resolve_round()
    kinds = [e.kind for e in events]
    assert "downed" in kinds and kinds[-1] == "lost"
    assert fight.outcome == OUTCOME_LOST and fight.finished


def test_a_downed_player_stays_down_and_the_rest_fight_on() -> None:
    weak, strong = hero(1, hp=1), hero(2, hp=10_000)
    fight = make_fight([weak, strong], [[foe(attack=5, hp=10_000, target="lowest_hp")]])
    fight.submit("p1", Action(ACTION_ATTACK))
    fight.submit("p2", Action(ACTION_ATTACK))
    fight.resolve_round()
    assert not weak.alive and not fight.finished
    assert [p.id for p in fight.awaiting()] == ["p2"]
    fight.resolve_round()
    assert not weak.alive


def test_clearing_the_last_stage_wins_and_reports_the_stage() -> None:
    fight = make_fight([hero(attack=400)], [[passive_foe(hp=10)]])
    fight.submit("p1", Action(ACTION_ATTACK))
    events = fight.resolve_round()
    kinds = [e.kind for e in events]
    assert "stage_cleared" in kinds and kinds[-1] == "won"
    assert fight.outcome == OUTCOME_WON


def test_a_cleared_stage_spawns_the_next_one_and_heals_a_little() -> None:
    player = hero(hp=100, attack=400)
    player.hp = 50
    player.statuses = [Status("sap", "Sap", "debuff", 2, stat="attack", percent=-50)]
    stage_two = [passive_foe(2, hp=10_000)]
    fight = make_fight([player], [[passive_foe(1, hp=10)], stage_two])
    fight.submit("p1", Action(ACTION_ATTACK))
    events = fight.resolve_round()
    assert fight.stage_index == 1 and fight.foes[0] is stage_two[0]
    assert not fight.finished
    assert player.hp == 50 + 10 and player.statuses == []
    assert [e.kind for e in events][-2:] == ["stage_cleared", "stage_start"]
    # The new stage's foes did not act in the round that cleared the old one.
    assert stage_two[0].hp == 10_000 and player.hp == 60


def test_downed_players_are_not_revived_between_stages() -> None:
    downed, alive = hero(1, hp=100), hero(2, hp=100, attack=400)
    downed.hp = 0
    fight = make_fight([downed, alive], [[passive_foe(1, hp=10)], [passive_foe(2, hp=10_000)]])
    fight.submit("p2", Action(ACTION_ATTACK))
    fight.resolve_round()
    assert downed.hp == 0 and fight.stage_index == 1


def test_the_fight_is_lost_when_it_exceeds_max_rounds() -> None:
    rules = CombatRules(40, 150, 100, 0, 50, 10, 3)
    fight = make_fight([hero(hp=10_000, attack=1)], [[passive_foe(hp=10_000)]], rules=rules)
    events: list[Event] = []
    while not fight.finished:
        events = fight.resolve_round()
    assert fight.outcome == OUTCOME_LOST and fight.round_no == 4
    assert [e.kind for e in events][-2:] == ["timeout", "lost"]


def test_seeded_fights_replay_identically() -> None:
    def run(seed: int) -> list[tuple[str, str, int]]:
        player = hero(hp=300, attack=30, crit=40)
        enemy = foe(attack=25, hp=400, crit=30)
        fight = make_fight(
            [player], [[enemy]], seed=seed, rules=CombatRules(40, 150, 75, 10, 50, 10, 40)
        )
        log: list[tuple[str, str, int]] = []
        while not fight.finished:
            fight.submit("p1", Action(ACTION_ATTACK))
            log.extend((e.kind, e.actor, e.amount) for e in fight.resolve_round())
        return log

    assert run(3) == run(3)
    assert run(3) != run(4)


# --------------------------------------------------------------------------------- enemy AI


def test_the_ai_skips_skills_on_cooldown_and_falls_back_to_a_plain_attack() -> None:
    enemy = foe(hp=10_000, attack=1, actions=(("smash", 1),))
    enemy.cooldowns["smash"] = 5
    target = hero(hp=10_000)
    fight = make_fight([target], [[enemy]])
    events = fight.resolve_round()
    assert [e.kind for e in events if e.actor == "F1"] == ["attack"]


def test_an_enemy_with_no_actions_still_attacks() -> None:
    enemy = foe(hp=10_000, attack=1, actions=())
    fight = make_fight([hero(hp=10_000)], [[enemy]])
    events = fight.resolve_round()
    assert any(e.kind == "attack" and e.actor == "F1" for e in events)


def test_the_ai_follows_its_weights() -> None:
    enemy = foe(hp=10**9, attack=1, actions=(("attack", 1), ("magic", 9)))
    fight = make_fight([hero(hp=10**9, defense=10**9)], [[enemy]])
    kinds = []
    for _ in range(30):
        kinds.extend(e.kind for e in fight.resolve_round() if e.actor == "F1")
    assert kinds.count("magic") > kinds.count("attack")


def test_a_lowest_hp_enemy_goes_for_the_weakest_player() -> None:
    low, high = hero(1, hp=100), hero(2, hp=100)
    low.hp = 20
    enemy = foe(hp=10_000, attack=1, target="lowest_hp")
    fight = make_fight([low, high], [[enemy]])
    events = fight.resolve_round()
    assert next(e for e in events if e.actor == "F1").target == "P1"


def test_an_enemy_skill_that_hits_everyone_is_supported() -> None:
    enemy = foe(hp=10_000, attack=40, actions=(("cleave", 1),))
    fight = make_fight([hero(1, hp=500), hero(2, hp=500)], [[enemy]])
    fight.resolve_round()
    assert all(p.hp < 500 for p in fight.players)


# --------------------------------------------------------------------------------- builders


@pytest.fixture(scope="module")
def shipped():  # type: ignore[no-untyped-def]
    return load_content(Path("content/rpg"))


def test_enemy_stats_apply_percentages_with_a_floor_of_one() -> None:
    base = Stats(100, 10, 10, 5, 5, 10, 5)
    mult = Stats(70, 85, 50, 70, 70, 85, 1)
    assert enemy_stats(base, mult) == Stats(70, 8, 5, 4, 4, 8, 1)
    assert enemy_stats(Stats(1, 1, 1, 1, 1, 1, 1), Stats(1, 1, 1, 1, 1, 1, 1)) == Stats(
        1, 1, 1, 1, 1, 1, 1
    )


def test_a_solo_stage_uses_the_level_stats_times_the_archetype(shipped) -> None:  # type: ignore[no-untyped-def]
    foes = build_stage_foes(shipped, Stage(("gaki", "gaki"), 1), 1, 1)
    base = stats_at_level(shipped.levels, 1)
    assert len(foes) == 2
    assert foes[0].stats == enemy_stats(base, shipped.enemies["gaki"].mult)
    assert foes[0].hp == foes[0].stats.hp
    assert [f.name for f in foes] == ["Gaki A", "Gaki B"]
    assert len({f.id for f in foes}) == 2


def test_party_scaling_raises_hp_and_adds_the_weakest_non_boss(shipped) -> None:  # type: ignore[no-untyped-def]
    stage = Stage(("ushi_oni", "gaki"), 20)
    scaling = shipped.balance.party
    solo = build_stage_foes(shipped, stage, 1, 1)
    trio = build_stage_foes(shipped, stage, 1, 3)
    assert len(solo) == 2 and len(trio) == 2 + scaling.extra_foes[2]
    assert "Gaki A" in [f.name for f in trio] and "Gaki B" in [f.name for f in trio]
    assert trio[0].max_hp == round(solo[0].max_hp * scaling.hp_percent[2] / 100)


def test_a_lone_foe_only_gains_hp_never_copies_of_itself(shipped) -> None:  # type: ignore[no-untyped-def]
    stage = Stage(("ushi_oni",), 20)
    solo = build_stage_foes(shipped, stage, 1, 1)
    quad = build_stage_foes(shipped, stage, 1, 4)
    assert len(quad) == 1
    assert quad[0].max_hp == round(solo[0].max_hp * shipped.balance.party.hp_percent[3] / 100)


def test_a_boss_with_escorts_gets_more_escorts_not_more_bosses(shipped) -> None:  # type: ignore[no-untyped-def]
    foes = build_stage_foes(shipped, Stage(("hannya", "gashadokuro"), 28), 1, 4)
    assert sum(f.boss for f in foes) == 1
    assert len(foes) == 2 + shipped.balance.party.extra_foes[3]


def test_bosses_are_never_duplicated_by_party_scaling(shipped) -> None:  # type: ignore[no-untyped-def]
    foes = build_stage_foes(shipped, Stage(("gashadokuro",), 24), 1, 4)
    assert len(foes) == 1 and foes[0].boss is True


def test_foe_count_is_capped(shipped) -> None:  # type: ignore[no-untyped-def]
    foes = build_stage_foes(shipped, Stage(("gaki",) * 5, 3), 1, 4)
    assert len(foes) == MAX_FOES


def test_party_size_outside_the_table_is_clamped(shipped) -> None:  # type: ignore[no-untyped-def]
    pair = Stage(("gaki", "gaki"), 2)
    assert len(build_stage_foes(shipped, pair, 1, 0)) == 2
    assert len(build_stage_foes(shipped, pair, 1, 99)) == 2 + shipped.balance.party.extra_foes[3]


def test_build_stages_numbers_the_foe_ids_per_stage(shipped) -> None:  # type: ignore[no-untyped-def]
    mission = shipped.missions["e_midnight_gate"]
    stages = build_stages(shipped, mission.stages, 1)
    assert len(stages) == 2
    assert stages[0][0].id.startswith("f1_") and stages[1][0].id.startswith("f2_")


# --------------------------------------------------------------------------------- describe


def test_every_event_kind_has_a_log_line_and_names_are_escaped() -> None:
    kinds = [
        "attack", "magic", "skill_damage", "dot_apply", "dot_tick", "heal", "buff", "debuff",
        "cleanse", "defend", "downed", "stage_cleared", "stage_start", "timeout", "won", "lost",
    ]  # fmt: skip
    for kind in kinds:
        assert describe(Event(kind, actor="A", target="C", amount=5, label="L")), kind
    for kind in (
        "attack",
        "magic",
        "skill_damage",
        "dot_apply",
        "heal",
        "buff",
        "debuff",
        "cleanse",
    ):
        assert "A\\*B" in describe(Event(kind, actor="A*B", target="C", amount=5, label="L"))
        assert "C\\_D" in describe(Event(kind, actor="A", target="C_D", amount=5, label="L"))
        assert "Sk\\[ill\\]" in describe(Event(kind, actor="A", target="C", label="Sk[ill]")) or (
            kind in ("attack", "magic")
        )
    assert "A\\*B" in describe(Event("defend", actor="A*B"))
    for kind in ("dot_tick", "downed"):
        assert "C\\_D" in describe(Event(kind, target="C_D", amount=5, label="L"))
    assert describe(Event("mystery")) == ""
    assert describe(Event("attack", "A", "B", 3, crit=True)).endswith("(crit)")
    assert describe_all([Event("mystery"), Event("won")]) == ["The party stands victorious"]


# --------------------------------------------------------------------------------- sweep


def _random_action(fight: Fight, player: Combatant, rng: random.Random) -> Optional[Action]:
    usable = fight.usable_skills(player)
    roll = rng.random()
    if usable and roll < 0.4:
        return Action(ACTION_SKILL, skill_id=rng.choice(usable).id)
    if roll < 0.65:
        return Action(ACTION_ATTACK)
    if roll < 0.85:
        return Action(ACTION_MAGIC)
    if roll < 0.95:
        return Action(ACTION_DEFEND)
    return None


def test_random_fights_over_the_shipped_missions_never_raise_and_always_finish(shipped) -> None:  # type: ignore[no-untyped-def]
    all_skills = dict(shipped.skills) | SKILLS
    rng = random.Random(2024)
    missions = list(shipped.missions.values())
    for seed in range(120):
        mission = missions[seed % len(missions)]
        size = 1 + seed % 4
        level = max(1, min(60, mission.top_level + rng.randint(-5, 5)))
        base = stats_at_level(shipped.levels, level)
        players = [
            player_combatant(n, f"Hero {n}", base, tuple(all_skills)) for n in range(1, size + 1)
        ]
        stages = build_stages(shipped, mission.stages, size)
        fight = Fight(players, stages, all_skills, shipped.balance.combat, random.Random(seed))
        rounds = 0
        while not fight.finished:
            rounds += 1
            assert rounds <= shipped.balance.combat.max_rounds + 1
            for player in fight.awaiting():
                action = _random_action(fight, player, rng)
                if action:
                    fight.submit(player.id, action)
            fight.resolve_round()
            for fighter_ in [*fight.players, *fight.foes]:
                assert 0 <= fighter_.hp <= fighter_.max_hp
                assert all(v > 0 for v in fighter_.cooldowns.values())
                assert all(s.remaining > 0 for s in fighter_.statuses)
        assert fight.outcome in (OUTCOME_WON, OUTCOME_LOST)


def test_enemy_power_scales_every_stat_by_a_percentage() -> None:
    base = Stats(100, 20, 20, 10, 10, 10, 4)
    even = Stats(100, 100, 100, 100, 100, 100, 100)
    assert enemy_stats(base, even, 50) == Stats(50, 10, 10, 5, 5, 5, 2)
    assert enemy_stats(base, even) == base
    assert enemy_stats(base, even, 1) == Stats(1, 1, 1, 1, 1, 1, 1)


def test_mission_stages_use_the_missions_rank_power(shipped) -> None:  # type: ignore[no-untyped-def]
    from yokai.rpg.combat.builders import build_mission_stages

    mission = shipped.missions["e_pest_control"]
    power = round(shipped.rank(mission.rank).enemy_power * mission.power / 100)
    foes = build_mission_stages(shipped, mission, 1)[0]
    base = stats_at_level(shipped.levels, mission.stages[0].level)
    assert foes[0].stats == enemy_stats(base, shipped.enemies["gaki"].mult, power)


def test_mission_power_scales_on_top_of_the_rank_power(shipped) -> None:  # type: ignore[no-untyped-def]
    from dataclasses import replace

    from yokai.rpg.combat.builders import build_mission_stages

    mission = replace(shipped.missions["e_pest_control"], power=100)
    harder = replace(mission, power=200)
    softer = replace(mission, power=50)
    normal_hp = build_mission_stages(shipped, mission, 1)[0][0].max_hp
    assert build_mission_stages(shipped, harder, 1)[0][0].max_hp > normal_hp
    assert build_mission_stages(shipped, softer, 1)[0][0].max_hp < normal_hp
    tiny = replace(mission, power=10)
    assert build_mission_stages(shipped, tiny, 1)[0][0].max_hp >= 1
