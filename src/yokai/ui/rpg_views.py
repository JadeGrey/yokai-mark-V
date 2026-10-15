"""The guild fight's buttons and the private target and skill prompts."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING, Awaitable, Callable, Optional

import discord

from yokai.errors import InvalidCombatActionError, NotInFightError
from yokai.moderation.gate import screen
from yokai.rpg.combat.engine import Fight
from yokai.rpg.combat.models import (
    ACTION_ATTACK,
    ACTION_DEFEND,
    ACTION_MAGIC,
    ACTION_SKILL,
    Action,
    Combatant,
)
from yokai.rpg.models import SkillDef
from yokai.ui.embeds import EmbedFactory, send
from yokai.ui.table_common import avatar_url, reply_error

if TYPE_CHECKING:
    from yokai.bot import YokaiBot

logger = logging.getLogger(__name__)

PROMPT_TIMEOUT = 60.0
MAX_SELECT_OPTIONS = 25


class FightView(discord.ui.View):
    """The buttons under the live fight message. One view per round.

    Only the adventurers in the fight may press them. `waiting_for` are the players the round
    waits on (everyone who is not marked away); once they have all chosen, `all_chosen` is set.
    """

    def __init__(
        self,
        *,
        bot: YokaiBot,
        fight: Fight,
        player_ids: set[int],
        waiting_for: set[int],
        refresh: Callable[[], Awaitable[None]],
    ) -> None:
        super().__init__(timeout=None)
        self.bot = bot
        self.fight = fight
        self.player_ids = player_ids
        self.waiting_for = set(waiting_for)
        self.refresh = refresh
        self.round_no = fight.round_no
        self.closed = False
        self.all_chosen = asyncio.Event()
        if not any(p.skills for p in fight.players):
            self.remove_item(self.skill_button)
        self._check_done()

    # ------------------------------------------------------------------ plumbing

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if await screen(interaction):
            return False
        if interaction.user.id not in self.player_ids:
            await reply_error(self.bot, interaction, NotInFightError())
            return False
        return True

    def close(self) -> None:
        """Stop accepting presses and disable the buttons (the message edit is the caller's)."""
        self.closed = True
        for item in self.children:
            if isinstance(item, discord.ui.Button):
                item.disabled = True
        self.stop()

    def _combatant(self, user_id: int) -> Optional[Combatant]:
        return next((p for p in self.fight.players if p.user_id == user_id), None)

    def _check_done(self) -> None:
        pending = [
            p
            for p in self.fight.living_players()
            if p.user_id in self.waiting_for and not self.fight.has_chosen(p.id)
        ]
        if not pending:
            self.all_chosen.set()

    async def _guard(self, interaction: discord.Interaction) -> Optional[Combatant]:
        """The pressing player's combatant, or None after telling them why they cannot act."""
        combatant = self._combatant(interaction.user.id)
        problem: Optional[str] = None
        if self.closed or self.fight.finished or self.fight.round_no != self.round_no:
            problem = "That round has already been resolved."
        elif combatant is None:
            problem = "You are not in this fight."
        elif not combatant.alive:
            problem = "You are down and can't act."
        elif self.fight.has_chosen(combatant.id):
            problem = "You've already locked in this round."
        if problem is not None:
            await reply_error(self.bot, interaction, InvalidCombatActionError(problem))
            return None
        return combatant

    async def submit(
        self,
        interaction: discord.Interaction,
        combatant: Combatant,
        action: Action,
        *,
        from_prompt: bool = False,
    ) -> None:
        """Lock in an action, acknowledge the click, and wake the runner if everyone is ready."""
        if self.closed or self.fight.round_no != self.round_no:
            await reply_error(
                self.bot, interaction, InvalidCombatActionError("That round has already ended.")
            )
            return
        try:
            self.fight.submit(combatant.id, action)
        except InvalidCombatActionError as err:
            await reply_error(self.bot, interaction, err)
            return
        if from_prompt:
            locked = EmbedFactory.info(
                title="Locked in",
                description="Your action is set. Waiting for the others.",
                context="Guild",
                bot_avatar_url=avatar_url(self.bot),
            )
            await interaction.response.edit_message(embed=locked, view=None)
        else:
            await interaction.response.defer()
        self._check_done()
        if not self.all_chosen.is_set():
            await self.refresh()

    # ------------------------------------------------------------------ buttons

    async def _basic(self, interaction: discord.Interaction, kind: str) -> None:
        combatant = await self._guard(interaction)
        if combatant is None:
            return
        foes = self.fight.living_foes()
        if len(foes) > 1:
            await self._prompt_targets(interaction, combatant, kind, None, foes)
            return
        await self.submit(interaction, combatant, Action(kind))

    @discord.ui.button(label="Attack", style=discord.ButtonStyle.primary)
    async def attack_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._basic(interaction, ACTION_ATTACK)

    @discord.ui.button(label="Magic", style=discord.ButtonStyle.primary)
    async def magic_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        await self._basic(interaction, ACTION_MAGIC)

    @discord.ui.button(label="Defend", style=discord.ButtonStyle.secondary)
    async def defend_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        combatant = await self._guard(interaction)
        if combatant is not None:
            await self.submit(interaction, combatant, Action(ACTION_DEFEND))

    @discord.ui.button(label="Skill", style=discord.ButtonStyle.secondary)
    async def skill_button(
        self, interaction: discord.Interaction, button: discord.ui.Button
    ) -> None:
        combatant = await self._guard(interaction)
        if combatant is None:
            return
        usable = self.fight.usable_skills(combatant)
        if not usable:
            await reply_error(
                self.bot, interaction, InvalidCombatActionError("None of your skills are ready.")
            )
            return
        prompt = PromptView(interaction.user.id)
        prompt.add_item(SkillSelect(self, combatant, usable[:MAX_SELECT_OPTIONS]))
        embed = EmbedFactory.info(
            title="Choose a skill",
            description="Pick a skill to use this round.",
            context="Guild",
            bot_avatar_url=avatar_url(self.bot),
        )
        await send(interaction, embed=embed, view=prompt, ephemeral=True)

    # ------------------------------------------------------------------ target prompts

    async def _prompt_targets(
        self,
        interaction: discord.Interaction,
        combatant: Combatant,
        kind: str,
        skill_id: Optional[str],
        candidates: list[Combatant],
        *,
        edit: bool = False,
    ) -> None:
        prompt = PromptView(interaction.user.id)
        prompt.add_item(TargetSelect(self, combatant, kind, skill_id, candidates))
        embed = EmbedFactory.info(
            title="Choose a target",
            description="Who should it hit?",
            context="Guild",
            bot_avatar_url=avatar_url(self.bot),
        )
        if edit:
            await interaction.response.edit_message(embed=embed, view=prompt)
        else:
            await send(interaction, embed=embed, view=prompt, ephemeral=True)


class PromptView(discord.ui.View):
    """A private, short-lived view holding one select, usable only by the player it is for."""

    def __init__(self, owner_id: int) -> None:
        super().__init__(timeout=PROMPT_TIMEOUT)
        self.owner_id = owner_id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        return interaction.user.id == self.owner_id


class TargetSelect(discord.ui.Select):  # type: ignore[type-arg]
    """Pick which living combatant an attack, skill or heal is aimed at."""

    def __init__(
        self,
        fight_view: FightView,
        combatant: Combatant,
        kind: str,
        skill_id: Optional[str],
        candidates: list[Combatant],
    ) -> None:
        super().__init__(
            placeholder="Choose a target",
            options=[
                discord.SelectOption(label=c.name[:100], value=c.id)
                for c in candidates[:MAX_SELECT_OPTIONS]
            ],
        )
        self.fight_view = fight_view
        self.combatant = combatant
        self.kind = kind
        self.skill_id = skill_id

    async def callback(self, interaction: discord.Interaction) -> None:
        action = Action(self.kind, skill_id=self.skill_id, target_id=self.values[0])
        await self.fight_view.submit(interaction, self.combatant, action, from_prompt=True)


class SkillSelect(discord.ui.Select):  # type: ignore[type-arg]
    """Pick one of the player's ready skills."""

    def __init__(self, fight_view: FightView, combatant: Combatant, skills: list[SkillDef]) -> None:
        super().__init__(
            placeholder="Choose a skill",
            options=[
                discord.SelectOption(label=s.name[:100], value=s.id, description=s.kind)
                for s in skills
            ],
        )
        self.fight_view = fight_view
        self.combatant = combatant
        self.skills = {s.id: s for s in skills}

    async def callback(self, interaction: discord.Interaction) -> None:
        view = self.fight_view
        skill = self.skills[self.values[0]]
        candidates: list[Combatant] = []
        if skill.target == "foe":
            candidates = view.fight.living_foes()
        elif skill.target == "ally":
            candidates = view.fight.living_players()
        if len(candidates) > 1:
            await view._prompt_targets(
                interaction, self.combatant, ACTION_SKILL, skill.id, candidates, edit=True
            )
            return
        target_id = candidates[0].id if candidates else None
        action = Action(ACTION_SKILL, skill_id=skill.id, target_id=target_id)
        await view.submit(interaction, self.combatant, action, from_prompt=True)
