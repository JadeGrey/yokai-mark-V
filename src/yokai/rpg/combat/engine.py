"""The turn-based fight: choose, resolve in speed order, enemies act, end-of-round upkeep."""

from __future__ import annotations

import math
import random
from typing import Mapping, Optional, Sequence

from yokai.errors import InvalidCombatActionError
from yokai.rpg.combat.ai import choose_enemy_action
from yokai.rpg.combat.formulas import dot_damage, heal_amount, roll_damage
from yokai.rpg.combat.models import (
    ACTION_ATTACK,
    ACTION_DEFEND,
    ACTION_KINDS,
    ACTION_MAGIC,
    ACTION_SKILL,
    OUTCOME_LOST,
    OUTCOME_WON,
    SIDE_PLAYERS,
    Action,
    Combatant,
    Event,
    Status,
)
from yokai.rpg.models import CombatRules, SkillDef


class Fight:
    """One fight: a party against one or more stages of foes.

    The caller submits an action for each living player, then calls `resolve_round`. A player
    without an action defends. All randomness comes from `rng`, so a seeded fight replays exactly.
    """

    def __init__(
        self,
        players: Sequence[Combatant],
        stages: Sequence[Sequence[Combatant]],
        skills: Mapping[str, SkillDef],
        rules: CombatRules,
        rng: random.Random,
    ) -> None:
        if not players:
            raise ValueError("A fight needs at least one player.")
        if not stages or any(not stage for stage in stages):
            raise ValueError("A fight needs at least one stage, and every stage needs foes.")
        self.players: list[Combatant] = list(players)
        self._stages: list[list[Combatant]] = [list(stage) for stage in stages]
        self.skills = skills
        self.rules = rules
        self.rng = rng
        self.stage_index = 0
        self.foes: list[Combatant] = self._stages[0]
        self.round_no = 1
        self.outcome: Optional[str] = None
        self._pending: dict[str, Action] = {}

    # ------------------------------------------------------------------ inspection

    @property
    def finished(self) -> bool:
        return self.outcome is not None

    @property
    def stage_count(self) -> int:
        return len(self._stages)

    def living_players(self) -> list[Combatant]:
        return [p for p in self.players if p.alive]

    def living_foes(self) -> list[Combatant]:
        return [f for f in self.foes if f.alive]

    def awaiting(self) -> list[Combatant]:
        """Living players who have not chosen an action yet."""
        return [p for p in self.living_players() if p.id not in self._pending]

    def usable_skills(self, actor: Combatant) -> list[SkillDef]:
        """The actor's skills that are off cooldown."""
        found = [self.skills[s] for s in actor.skills if s in self.skills]
        return [s for s in found if actor.cooldowns.get(s.id, 0) == 0]

    # ------------------------------------------------------------------ choosing

    def submit(self, actor_id: str, action: Action) -> None:
        """Record a player's action for this round. Raises InvalidCombatActionError if illegal."""
        if self.finished:
            raise InvalidCombatActionError("The fight is already over.")
        actor = next((p for p in self.players if p.id == actor_id), None)
        if actor is None:
            raise InvalidCombatActionError("That fighter is not in this fight.")
        if not actor.alive:
            raise InvalidCombatActionError("You are down and can't act.")
        if action.kind not in ACTION_KINDS:
            raise InvalidCombatActionError("That isn't an action.")
        target_kind = "foe"
        if action.kind == ACTION_SKILL:
            skill = self.skills.get(action.skill_id or "")
            if skill is None or skill.id not in actor.skills:
                raise InvalidCombatActionError("You don't know that skill.")
            remaining = actor.cooldowns.get(skill.id, 0)
            if remaining:
                raise InvalidCombatActionError(
                    f"{skill.name} needs {remaining} more round(s) to recharge."
                )
            target_kind = skill.target
        self._validate_target(target_kind, action)
        self._pending[actor_id] = action

    def _validate_target(self, target_kind: str, action: Action) -> None:
        if action.target_id is None or action.kind == ACTION_DEFEND:
            return
        if target_kind == "foe" and all(f.id != action.target_id for f in self.living_foes()):
            raise InvalidCombatActionError("That target is not standing.")
        if target_kind == "ally" and all(p.id != action.target_id for p in self.living_players()):
            raise InvalidCombatActionError("That ally can't be targeted.")

    # ------------------------------------------------------------------ resolving

    def resolve_round(self) -> list[Event]:
        """Play out one round and return what happened, in order."""
        if self.finished:
            raise InvalidCombatActionError("The fight is already over.")
        events: list[Event] = []
        for player in self.living_players():
            self._pending.setdefault(player.id, Action(ACTION_DEFEND))

        keyed = [(-p.effective("speed"), self.rng.random(), p) for p in self.living_players()]
        keyed.sort(key=lambda item: (item[0], item[1]))
        stage_at_start = self.stage_index

        for _, _, actor in keyed:
            if self._round_over(stage_at_start):
                break
            if actor.alive:
                self._act(actor, self._pending[actor.id], events)
                self._check_end(events)

        for foe in list(self.foes):
            if self._round_over(stage_at_start):
                break
            if foe.alive:
                self._act(foe, choose_enemy_action(foe, self, self.rng), events)
                self._check_end(events)

        if not self.finished:
            self._end_of_round(events)
        if not self.finished and self.round_no >= self.rules.max_rounds:
            self.outcome = OUTCOME_LOST
            events.append(Event("timeout"))
            events.append(Event("lost"))
        self._pending.clear()
        self.round_no += 1
        return events

    def _round_over(self, stage_at_start: int) -> bool:
        return self.finished or self.stage_index != stage_at_start

    def _act(self, actor: Combatant, action: Action, events: list[Event]) -> None:
        if action.kind == ACTION_DEFEND:
            actor.defending = True
            events.append(Event("defend", actor=actor.name, side=actor.side))
        elif action.kind in (ACTION_ATTACK, ACTION_MAGIC):
            self._basic_attack(actor, action, events)
        elif action.kind == ACTION_SKILL and action.skill_id in self.skills:
            self._use_skill(actor, self.skills[action.skill_id], action, events)

    def _basic_attack(self, actor: Combatant, action: Action, events: list[Event]) -> None:
        targets = self._targets(actor, "foe", action.target_id)
        if not targets:
            return
        target = targets[0]
        physical = action.kind == ACTION_ATTACK
        amount, crit = roll_damage(
            self.rules,
            self.rng,
            offense=actor.effective("attack" if physical else "magic"),
            power=100,
            defense=target.effective("defense" if physical else "resist"),
            crit_chance=actor.effective("crit"),
            defending=target.defending,
        )
        self._deal(target, amount)
        events.append(Event(action.kind, actor.name, target.name, amount, crit, side=actor.side))
        self._note_downed(target, events)

    def _use_skill(
        self, actor: Combatant, skill: SkillDef, action: Action, events: list[Event]
    ) -> None:
        targets = self._targets(actor, skill.target, action.target_id)
        if not targets:
            return
        if skill.cooldown:
            # Stored one higher because this same round's upkeep ticks it down once: a cooldown
            # of 3 leaves the skill unavailable for the next three rounds.
            actor.cooldowns[skill.id] = skill.cooldown + 1
        for target in targets:
            if skill.kind == "damage":
                self._skill_damage(actor, skill, target, events)
            elif skill.kind == "dot":
                self._skill_dot(actor, skill, target, events)
            elif skill.kind in ("buff", "debuff"):
                self._skill_modifier(actor, skill, target, events)
            elif skill.kind == "heal":
                amount = min(
                    heal_amount(magic=actor.effective("magic"), power=skill.power),
                    target.max_hp - target.hp,
                )
                target.hp += amount
                events.append(
                    Event(
                        "heal", actor.name, target.name, amount, label=skill.name, side=actor.side
                    )
                )
            elif skill.kind == "cleanse":
                before = len(target.statuses)
                target.statuses = [s for s in target.statuses if s.kind == "buff"]
                events.append(
                    Event(
                        "cleanse",
                        actor.name,
                        target.name,
                        before - len(target.statuses),
                        label=skill.name,
                        side=actor.side,
                    )
                )

    def _skill_damage(
        self, actor: Combatant, skill: SkillDef, target: Combatant, events: list[Event]
    ) -> None:
        physical = skill.scaling == "attack"
        amount, crit = roll_damage(
            self.rules,
            self.rng,
            offense=actor.effective("attack" if physical else "magic"),
            power=skill.power,
            defense=target.effective("defense" if physical else "resist"),
            crit_chance=actor.effective("crit"),
            defending=target.defending,
        )
        self._deal(target, amount)
        events.append(
            Event("skill_damage", actor.name, target.name, amount, crit, skill.name, actor.side)
        )
        self._note_downed(target, events)

    def _skill_dot(
        self, actor: Combatant, skill: SkillDef, target: Combatant, events: list[Event]
    ) -> None:
        physical = skill.scaling == "attack"
        per_round = dot_damage(
            self.rules,
            offense=actor.effective("attack" if physical else "magic"),
            power=skill.power,
            defense=target.effective("defense" if physical else "resist"),
        )
        self._apply_status(
            target,
            Status(skill.id, skill.name, "dot", skill.duration, damage=per_round),
        )
        events.append(
            Event(
                "dot_apply", actor.name, target.name, per_round, label=skill.name, side=actor.side
            )
        )

    def _skill_modifier(
        self, actor: Combatant, skill: SkillDef, target: Combatant, events: list[Event]
    ) -> None:
        sign = 1 if skill.kind == "buff" else -1
        self._apply_status(
            target,
            Status(
                skill.id,
                skill.name,
                skill.kind,
                skill.duration,
                stat=skill.stat,
                percent=sign * skill.percent,
            ),
        )
        events.append(
            Event(
                skill.kind,
                actor.name,
                target.name,
                skill.percent,
                label=skill.name,
                side=actor.side,
            )
        )

    @staticmethod
    def _apply_status(target: Combatant, status: Status) -> None:
        """Reapplying a skill's effect refreshes it instead of stacking."""
        target.statuses = [s for s in target.statuses if s.key != status.key]
        target.statuses.append(status)

    # ------------------------------------------------------------------ targeting

    def _opponents(self, actor: Combatant) -> list[Combatant]:
        return self.living_foes() if actor.side == SIDE_PLAYERS else self.living_players()

    def _allies(self, actor: Combatant) -> list[Combatant]:
        return self.living_players() if actor.side == SIDE_PLAYERS else self.living_foes()

    def _targets(
        self, actor: Combatant, target_kind: str, chosen_id: Optional[str]
    ) -> list[Combatant]:
        opponents = self._opponents(actor)
        allies = self._allies(actor)
        if target_kind == "all_foes":
            return list(opponents)
        if target_kind == "all_allies":
            return list(allies)
        if target_kind == "self":
            return [actor] if actor.alive else []
        if target_kind == "foe":
            picked = next((c for c in opponents if c.id == chosen_id), None)
            return [picked] if picked else opponents[:1]
        if target_kind == "ally":
            picked = next((c for c in allies if c.id == chosen_id), None)
            if picked:
                return [picked]
            return [min(allies, key=lambda c: c.hp / c.max_hp)] if allies else []
        return []

    # ------------------------------------------------------------------ damage, ends, upkeep

    @staticmethod
    def _deal(target: Combatant, amount: int) -> None:
        target.hp = max(0, target.hp - amount)

    @staticmethod
    def _note_downed(target: Combatant, events: list[Event]) -> None:
        if not target.alive:
            events.append(Event("downed", target=target.name, side=target.side))

    def _check_end(self, events: list[Event]) -> None:
        """After any action: the fight is lost if the party is down, or a stage may be cleared."""
        if self.finished:
            return
        if not self.living_players():
            self.outcome = OUTCOME_LOST
            events.append(Event("lost"))
        elif not self.living_foes():
            self._stage_cleared(events)

    def _stage_cleared(self, events: list[Event]) -> None:
        events.append(Event("stage_cleared", amount=self.stage_index + 1))
        if self.stage_index + 1 >= len(self._stages):
            self.outcome = OUTCOME_WON
            events.append(Event("won"))
            return
        self.stage_index += 1
        self.foes = self._stages[self.stage_index]
        for player in self.living_players():
            heal = math.ceil(player.max_hp * self.rules.stage_recovery_percent / 100)
            player.hp = min(player.max_hp, player.hp + heal)
            player.statuses = []
            player.defending = False
        events.append(Event("stage_start", amount=self.stage_index + 1))

    def _end_of_round(self, events: list[Event]) -> None:
        for fighter in [*self.players, *self.foes]:
            if not fighter.alive:
                continue
            for status in fighter.statuses:
                if status.kind == "dot":
                    self._deal(fighter, status.damage)
                    events.append(
                        Event(
                            "dot_tick",
                            target=fighter.name,
                            amount=status.damage,
                            label=status.name,
                            side=fighter.side,
                        )
                    )
                    if not fighter.alive:
                        self._note_downed(fighter, events)
                        break
            for status in fighter.statuses:
                status.remaining -= 1
            fighter.statuses = [s for s in fighter.statuses if s.remaining > 0]
            fighter.cooldowns = {k: v - 1 for k, v in fighter.cooldowns.items() if v > 1}
            fighter.defending = False
        self._check_end(events)
