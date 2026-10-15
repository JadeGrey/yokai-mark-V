"""The guild wiki: a table of contents and sections, built from the live content files.

Pure text with no Discord objects. Numbers and lists (ranks, loot odds, set bonuses, missions)
are generated from the loaded content and settings, so the wiki follows any balance change; the
explanations around them are written here. `build_wiki` returns pages already split to fit an
embed, and the wiki view just shows them.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Optional

from yokai.rpg import guild
from yokai.rpg.content import Content
from yokai.rpg.gear import is_boss_mission
from yokai.rpg.models import (
    MAX_PARTY,
    RARITIES,
    SLOTS,
    STAT_LABELS,
    STAT_NAMES,
    Mission,
    Stats,
)
from yokai.rpg.trade import MAX_ITEMS_PER_SIDE, SESSION_SECONDS

PAGE_LIMIT = 3300  # characters of body per page, well inside an embed description
SEP = " · "

# (id, title, one-line summary) in contents order. The command's choices come from this too.
SECTIONS: tuple[tuple[str, str, str], ...] = (
    ("start", "Getting started", "Joining, attempts, the daily board and streaks"),
    ("commands", "Commands", "Every guild command and what it does"),
    ("combat", "Combat", "Stats, actions, turn order and winning a fight"),
    ("ranks", "Ranks and promotion", "Levels, rank trials and the cooldown"),
    ("gear", "Gear", "Slots, rarities, tags, set bonuses and durability"),
    ("loot", "Loot and the shop", "Drop odds, which gear drops where, prices"),
    ("missions", "Missions and raids", "Every mission and raid, by rank"),
    ("bestiary", "Bestiary", "The enemies you will meet"),
    ("parties", "Parties and raids", "Lobbies, party size and how raids work"),
    ("trading", "Trading", "Swapping gear and Renown safely"),
    ("faq", "Tips and FAQ", "Quick answers to common questions"),
)


@dataclass(frozen=True, slots=True)
class WikiSettings:
    """The operator-set values the wiki quotes (from the environment, not the content files)."""

    base_attempts: int = 8
    turn_seconds: int = 45
    promotion_cooldown_hours: int = 6
    shop_daily_cap: int = 3
    timezone: str = "UTC"
    lobby_minutes: int = 3


@dataclass(frozen=True, slots=True)
class WikiPage:
    section_id: str
    section_title: str
    title: str
    body: str


@dataclass(frozen=True, slots=True)
class Wiki:
    """All pages in reading order. Page 0 is the table of contents."""

    pages: tuple[WikiPage, ...]
    first_page: dict[str, int]

    def section_of(self, index: int) -> str:
        return self.pages[index].section_id


# --------------------------------------------------------------------------- paging helpers


def paginate(blocks: list[str], limit: int = PAGE_LIMIT) -> list[str]:
    """Pack blocks (paragraphs) into pages of at most `limit` characters.

    A block longer than a page is split on its line breaks, and a single line longer than a
    page is cut, so no page can ever exceed the limit.
    """
    pages: list[str] = []
    current = ""
    for block in blocks:
        for piece in _split_block(block, limit):
            joined = f"{current}\n\n{piece}" if current else piece
            if len(joined) <= limit:
                current = joined
            else:
                pages.append(current)
                current = piece
    if current:
        pages.append(current)
    return pages or [""]


def _split_block(block: str, limit: int) -> list[str]:
    if len(block) <= limit:
        return [block]
    pieces: list[str] = []
    current = ""
    for line in block.split("\n"):
        line = line[:limit]
        joined = f"{current}\n{line}" if current else line
        if len(joined) <= limit:
            current = joined
        else:
            pieces.append(current)
            current = line
    if current:
        pieces.append(current)
    return pieces


def _bonus(bonus: Stats) -> str:
    parts = []
    for name in STAT_NAMES:
        value = getattr(bonus, name)
        if value:
            parts.append(
                f"+{value} {STAT_LABELS[name]}"
                if name == "crit"
                else f"+{value}% {STAT_LABELS[name]}"
            )
    return ", ".join(parts) or "none"


def _plural(count: int, word: str) -> str:
    return f"{count} {word}{'' if count == 1 else 's'}"


# ------------------------------------------------------------------------------- sections


def _start(content: Content, s: WikiSettings) -> list[str]:
    streak = content.balance.streak
    regular = [m.attempt_cost for m in content.missions.values() if m.kind == "mission"]
    raids = [m.attempt_cost for m in content.missions.values() if m.kind == "raid"]
    xp = content.balance.xp
    raid_ranks = [
        r.id
        for r in content.ranks
        if any(m.kind == "raid" for m in content.missions_for_rank(r.id))
    ]
    return [
        "**Welcome to the Adventurers' Guild.** Join with `/guild join`, pick a mission from the "
        "daily board, fight it turn by turn in its own thread, and earn XP, Renown and gear. "
        f"Climb from rank **{content.ranks[0].id}** to **{content.ranks[-1].id}** and level "
        f"**{content.levels.cap}**. Everyone starts as a classless adventurer; your gear decides "
        "your style.",
        f"**Attempts.** Each guild day gives you **{s.base_attempts} attempts** (the day resets at "
        f"midnight, {s.timezone}). Missions cost {min(regular)} to {max(regular)} attempts, raids "
        f"{min(raids)} to {max(raids)} and promotion trials 2. A fight that is cancelled by a bot "
        "restart refunds its attempts.",
        f"**Streaks.** Using at least one attempt in a day extends your streak. Every "
        f"**{streak.days_per_bonus} days** adds **+1 attempt** a day, up to "
        f"**+{streak.bonus_cap}**. "
        f"A fully missed day costs {_plural(streak.drop_per_missed_day, 'day')} of streak.",
        f"**The board.** It is the same for everyone and changes every guild day: "
        f"{guild.MISSIONS_PER_RANK} missions per rank, plus {guild.RAIDS_PER_RANK} raid per rank "
        f"from {raid_ranks[0]} up. You can take missions at your rank or below; lower-rank "
        "missions pay less. See `/guild board` for rewards and your attempts left.",
        f"**Experience.** Winning pays XP and Renown. Reaching the next level needs "
        f"`round({xp.base:g} x level^{xp.exponent:g})` XP. Renown is the guild's currency "
        "(it's the same Renown used across Yokai) and buys gear, repairs it, and is traded "
        "between members.",
        "**Losing** a fight pays nothing, costs the attempt and wears your gear down more than "
        "winning does. You never lose levels or items.",
    ]


def _commands(content: Content, s: WikiSettings) -> list[str]:
    return [
        "**Guild**\n"
        "`/guild join` Create your adventurer.\n"
        "`/guild board` Today's missions, rewards, your attempts and streak (private).\n"
        "`/guild mission <mission> [party]` Start a mission; `party: true` opens a lobby.\n"
        "`/guild rank` Your rank, what the next one needs, and the Challenge button.\n"
        "`/guild leaderboard [sort] [page]` Standings by rank, level or battles won.\n"
        "`/guild wiki [section]` This guide.\n"
        "`/card [member]` Anyone's adventurer card.",
        "**Gear**\n"
        "`/gear inventory [page]` The gear you own.\n"
        "`/gear equip <item>` Wear a piece (replaces the one in its slot).\n"
        "`/gear unequip <slot>` Take off the gear in a slot.\n"
        "`/gear repair [item]` Repair one item, or everything damaged, for Renown.\n"
        "`/gear shop` Today's guild shop.\n"
        "`/gear buy <item>` Buy from the shop.\n"
        "`/gear sell <item>` Sell an unequipped item to the guild.",
        "**Trading**\n"
        "`/trade <member>` Open a trade (gear for gear, gear for Renown, or a gift).\n\n"
        "**Admins only**\n"
        "`/admin rpg grant|level|rank|reset` Owner and adminlist tools. Each change is audited.\n\n"
        "Item and mission fields **autocomplete**: start typing a name or `#id`. Errors are "
        "private to you; fights, lobbies and trades are public so your friends can join in.",
    ]


def _combat(content: Content, s: WikiSettings) -> list[str]:
    c = content.balance.combat
    labels = {
        "hp": "How much damage you can take. At 0 you are **downed** and can't act.",
        "attack": "Power of the Attack action.",
        "magic": "Power of the Magic action and healing skills.",
        "defense": "Reduces damage from Attack.",
        "resist": "Reduces damage from Magic.",
        "speed": "Who acts first each round.",
        "crit": f"Chance (%) of a critical hit, capped at {c.crit_cap}%.",
    }
    stats = "\n".join(f"**{STAT_LABELS[n]}** {labels[n]}" for n in STAT_NAMES)
    stats_note = (
        f"Damage is scaled by `{c.defense_k} / ({c.defense_k} + defense)`, varies by about "
        f"±{c.variance_percent}%, and a critical hit does **{c.crit_multiplier_percent}%** damage."
    )
    return [
        "**How a fight works.** Each round everyone chooses an action at the same time "
        f"(**{s.turn_seconds} seconds**), then actions resolve from fastest to slowest, then the "
        "enemies act. Fights run in a public thread with one live message and buttons.",
        "**Actions**\n"
        "**Attack** hits with Attack against Defense.\n"
        "**Magic** hits with Magic against Resist.\n"
        f"**Defend** cuts the damage you take to **{c.defend_percent}%** until your next action.\n"
        "**Skill** appears when you wear 4 pieces of one tag (or an epic weapon with a skill); "
        "skills have cooldowns measured in rounds. There is no mana.",
        f"**Stats**\n{stats}\n\n{stats_note}",
        "**Win and lose.** You win by defeating every foe in every stage; you lose when every "
        f"player is downed or the fight passes **{c.max_rounds} rounds**. There are no revives. "
        f"Between stages living players recover **{c.stage_recovery_percent}%** of their max HP.",
        "**If you stop acting.** A player who doesn't choose in time defends. After 2 missed "
        "rounds you are marked **away** and the fight stops waiting for you, but you can rejoin "
        "by pressing a button. Your teammates still win for you.",
    ]


def _ranks(content: Content, s: WikiSettings) -> list[str]:
    r = content.balance.rewards
    lines = []
    for rank in content.ranks:
        if rank.promotion is None:
            lines.append(f"**Rank {rank.id}** Starting rank, from level {rank.min_level}.")
            continue
        trial = rank.promotion
        lines.append(
            f"**Rank {rank.id}** Opens at level **{rank.min_level}**. Trial: *{trial.name}* "
            f"({_plural(len(trial.stages), 'stage')}, {_plural(trial.attempt_cost, 'attempt')})."
        )
    return [
        f"The level cap is **{content.levels.cap}**. Your rank decides which missions you may "
        "enter; your level decides your stats.",
        "\n".join(lines),
        "**Promotion.** When you reach a rank's level, run `/guild rank` and press **Challenge**. "
        "It is a solo fight against a tougher set of foes that costs attempts like any mission. "
        "A win promotes you at once and pays Renown (no XP, no gear drops). A loss starts a "
        f"**{s.promotion_cooldown_hours}-hour cooldown** before you may try again; `/guild rank` "
        "and your card show when. Gear still wears. You can't skip ranks.",
        f"**Lower-rank missions.** Each rank a mission sits below yours multiplies its reward by "
        f"{r.below_rank_step:g}, and never below {round(r.below_rank_floor * 100)}% of the full "
        "reward. Rewards scale with the mission's top level and its attempt cost; at your own "
        f"rank, Renown is {r.renown_per_level} x top level x attempts.",
    ]


def _gear(content: Content, s: WikiSettings) -> list[str]:
    g = content.balance.gear
    durability = {}
    for item in content.items.values():
        durability.setdefault(item.rarity, item.max_durability)
    dur = ", ".join(f"{r} {durability[r]}" for r in RARITIES if r in durability)
    tiers = ", ".join(str(t) for t in content.item_tiers())
    tag_blocks = []
    for tag in content.tags.values():
        skill = content.skills.get(tag.skill) if tag.skill else None
        tag_blocks.append(
            f"**{tag.name}** ({tag.role}) {tag.description}\n"
            f"Leans: {', '.join(STAT_LABELS.get(n, n) for n in tag.leans)}"
            f"{SEP}Costs a little: {STAT_LABELS.get(tag.drawback or '', 'nothing')}\n"
            f"2 pieces: {_bonus(tag.bonus_2)}\n4 pieces: {_bonus(tag.bonus_4)}"
            + (f" and the skill **{skill.name}**" if skill else "")
        )
    return [
        f"**Slots.** {', '.join(SLOTS)}. You can wear one piece in each. Rarities, from common "
        f"to best: {', '.join(RARITIES)}. Gear has a **level requirement**; the five tiers need "
        f"levels {tiers}. You can own gear above your level, but you can't wear it yet.",
        f"**Rolled stats.** Every piece you get has its own stats, varying up to "
        f"±{g.roll_percent}% "
        "around the base, and most pieces cost a little of one stat (the trade-off for their "
        "strength). `/gear inventory` lists your pieces by `#id`.",
        f"**Durability.** Every equipped piece loses {g.win_wear} after a win and {g.loss_wear} "
        f"after a loss (maximums: {dur}). The result message warns when a piece reaches 50%, "
        "25% and 0%; starting a mission with worn gear asks you to confirm. A **broken** (0%) "
        "piece counts for nothing, including set bonuses.",
        f"**Repair and selling.** A full repair costs {g.repair_percent}% of an item's price, "
        f"pro rata to the damage (`/gear repair`). Selling pays {g.sell_percent}% of the price. "
        f"You can hold {g.inventory_cap} items; a drop into a full inventory is sold "
        "automatically.",
        "**Tags and set bonuses.** Each piece belongs to a folklore tag. Wearing **2** matching "
        "working pieces gives a small bonus; **4** gives a bigger one and unlocks the tag's "
        "skill. Bonuses are percentages of your final stat (Crit is flat points).",
        *tag_blocks,
    ]


def _tiers_for(content: Content, mission: Mission) -> list[int]:
    window = content.balance.loot.tier_window
    top = mission.top_level
    pool = [i for i in content.items.values() if not i.retired]
    near = {i.req_level for i in pool if top - window <= i.req_level <= top}
    return sorted(near or {i.req_level for i in pool if i.req_level <= top})


def _loot(content: Content, s: WikiSettings) -> list[str]:
    loot = content.balance.loot

    def odds(weights: tuple[tuple[str, int], ...]) -> str:
        total = sum(w for _, w in weights)
        return ", ".join(f"{name} {round(100 * w / total)}%" for name, w in weights)

    prices: dict[str, tuple[int, int]] = {}
    counts: dict[int, int] = {}
    for item in content.items.values():
        if item.retired:
            continue
        counts[item.req_level] = counts.get(item.req_level, 0) + 1
        if item.rarity in ("common", "uncommon"):
            key = f"{item.req_level}:{item.rarity}"
            low, high = prices.get(key, (item.price, item.price))
            prices[key] = (min(low, item.price), max(high, item.price))
    rank_lines = []
    for rank in content.ranks:
        missions = content.missions_for_rank(rank.id)
        if not missions:
            continue
        tiers = sorted({t for m in missions for t in _tiers_for(content, m)})
        rank_lines.append(
            f"**Rank {rank.id}** drops gear needing level {' or '.join(map(str, tiers))}"
        )
    shop_lines = []
    for tier in content.item_tiers():
        common = prices.get(f"{tier}:common")
        uncommon = prices.get(f"{tier}:uncommon")
        if common and uncommon:
            shop_lines.append(
                f"Level {tier}: common {common[0]:,}-{common[1]:,}, uncommon "
                f"{uncommon[0]:,}-{uncommon[1]:,} Renown"
            )
    bosses = sorted(
        {
            m.name
            for m in content.missions.values()
            if m.kind == "mission" and is_boss_mission(content, m)
        }
    )
    return [
        f"**Mission drops.** Each member of a winning party rolls on their own. A normal "
        f"mission drops a piece **{loot.drop_chance_percent}%** of the time; a boss mission or "
        f"any raid **{loot.boss_drop_chance_percent}%**. Rarity odds for normal missions: "
        f"{odds(loot.rarity_weights)}. Boss missions and raids: {odds(loot.boss_rarity_weights)}.",
        f"**What can drop.** Gear whose level requirement is within **{loot.tier_window} levels** "
        "below the mission's top level, so higher ranks drop better gear:\n"
        + "\n".join(rank_lines),
        f"**The shop.** `/gear shop` offers **{content.balance.shop.stock_per_tier} common and "
        "uncommon pieces per gear tier** each guild day. You may buy "
        f"**{s.shop_daily_cap}** a day. Rare and epic gear is drop-only. Price ranges:\n"
        + "\n".join(shop_lines),
        "**The gear pool.** "
        + ", ".join(f"level {t}: {n} pieces" for t, n in sorted(counts.items()))
        + f" ({sum(counts.values())} in total across the {len(content.tags)} tags).",
        "**Boss missions** (always drop; raids do too): " + (", ".join(bosses) or "none") + ".",
    ]


def _enemy_names(content: Content, mission: Mission) -> str:
    names: list[str] = []
    for stage in mission.stages:
        for enemy_id in stage.enemies:
            name = content.enemies[enemy_id].name
            if name not in names:
                names.append(name)
    return ", ".join(names)


def _missions(content: Content, s: WikiSettings) -> list[str]:
    blocks = [
        "Every mission and raid in the pool; the board draws a few of them each day. "
        "Rewards shown are for a member at that rank."
    ]
    for rank in content.ranks:
        missions = content.missions_for_rank(rank.id)
        if not missions:
            continue
        lines = [f"**Rank {rank.id}** (opens at level {rank.min_level})"]
        for mission in missions:
            reward = guild.mission_reward(content, mission, rank.id)
            tag = (
                f"RAID, party of {mission.min_party}-{MAX_PARTY}{SEP}"
                if mission.kind == "raid"
                else ""
            )
            boss = (
                "boss" + SEP if mission.kind != "raid" and is_boss_mission(content, mission) else ""
            )
            lines.append(
                f"**{mission.name}**{SEP}{tag}{boss}lvl {mission.top_level}{SEP}"
                f"{_plural(mission.attempt_cost, 'attempt')}{SEP}{len(mission.stages)} stage(s)"
                f"{SEP}+{reward.xp:,} XP, +{reward.renown:,} Renown\n"
                f"  {_enemy_names(content, mission)}"
            )
        blocks.append("\n".join(lines))
    return blocks


def _bestiary(content: Content, s: WikiSettings) -> list[str]:
    seen: dict[str, set[str]] = {}
    for mission in content.missions.values():
        for stage in mission.stages:
            for enemy_id in stage.enemies:
                seen.setdefault(enemy_id, set()).add(mission.rank)
    for rank in content.ranks:
        if rank.promotion:
            for stage in rank.promotion.stages:
                for enemy_id in stage.enemies:
                    seen.setdefault(enemy_id, set()).add(rank.id)
    order = {r.id: i for i, r in enumerate(content.ranks)}
    lines = []

    def first_rank(enemy_id: str) -> int:
        return min((order[r] for r in seen.get(enemy_id, ())), default=len(order))

    for enemy in sorted(content.enemies.values(), key=lambda e: (first_rank(e.id), e.name)):
        ranks = sorted(seen.get(enemy.id, set()), key=lambda r: order[r])
        where = ", ".join(ranks) if ranks else "not on any board"
        lines.append(f"**{enemy.name}**{' (boss)' if enemy.boss else ''}{SEP}rank {where}")
    return [
        "Enemy strength depends on the mission's level and its rank. Bosses are never duplicated "
        "by party scaling.",
        "\n".join(lines),
    ]


def _parties(content: Content, s: WikiSettings) -> list[str]:
    scaling = content.balance.party
    table = "\n".join(
        f"{size} member{'s' if size > 1 else ''}: foes have {scaling.hp_percent[size - 1]}% HP"
        + (f", +{scaling.extra_foes[size - 1]} escort(s)" if scaling.extra_foes[size - 1] else "")
        for size in range(1, MAX_PARTY + 1)
    )
    raids = [m for m in content.missions.values() if m.kind == "raid"]
    return [
        f"**Lobbies.** Run `/guild mission <mission> party:true` to post a lobby in the channel. "
        f"Up to **{MAX_PARTY}** members press **Join** (rank, attempts and fights are checked on "
        "the spot); the leader presses **Start**. Everyone's attempts are charged at that moment "
        "and one thread opens for the group. Nothing is charged if the lobby is cancelled or "
        f"expires after **{s.lobby_minutes} minutes**. At Start everyone is checked again, and "
        "anyone who can no longer go is removed so the leader can start again.",
        "**Rewards.** Everyone earns their own XP, Renown and drop roll. The party is limited by "
        "its lowest-ranked member.",
        f"**Scaling.** Enemy damage doesn't grow with the party, so bigger parties meet tougher "
        f"foes:\n{table}\nEscorts are extra copies of a stage's weakest non-boss foe, added only "
        "to stages that already have a group.",
        f"**Raids.** {len(raids)} raids, from rank {raids[0].rank} up. Each has three stages and "
        f"a boss, costs {min(r.attempt_cost for r in raids)}-{max(r.attempt_cost for r in raids)} "
        f"attempts, needs a party of at least {raids[0].min_party}, and always drops gear from the "
        "boss table. A party of **three or four** is recommended; two is a stretch.",
    ]


def _trading(content: Content, s: WikiSettings) -> list[str]:
    return [
        "Run `/trade <member>` to open a trade with another adventurer. It is one public "
        "message; each side adds what they offer, then **both press Confirm**.",
        f"**Offers.** Up to {MAX_ITEMS_PER_SIDE} items and any amount of Renown per side. "
        "**Add item** opens a private picker of your unequipped gear; **Set Renown** takes an "
        "amount (0 takes it back). A **gift** is simply a trade where one side offers nothing; "
        "the receiver still has to confirm.",
        "**Safety.** Changing an offer clears both confirmations, so you never confirm one "
        "deal and receive another. When both have confirmed, the swap is checked again and is "
        "all-or-nothing: items must still be yours and unequipped, Renown must still be there, "
        f"and nobody may end up over {content.balance.gear.inventory_cap} items. If something "
        "went stale it is taken out of the offer and you confirm again.",
        f"**Rules.** Equipped gear can't be offered. Nobody can trade during a guild fight. "
        f"Durability travels with the item. A trade closes after {SESSION_SECONDS // 60} minutes "
        "or when either side cancels. There is no trade fee. Ranks and levels can't be traded.",
    ]


def _faq(content: Content, s: WikiSettings) -> list[str]:
    return [
        "**I can't start a mission.** Check your rank (missions at your rank or below), your "
        "attempts (`/guild board`) and that you aren't already in a fight.",
        "**Why does my gear say broken?** It reached 0% durability. Broken gear counts for "
        "nothing; repair it with `/gear repair`.",
        "**I'm out of attempts.** They refill at midnight guild time. A streak of "
        f"{content.balance.streak.days_per_bonus * content.balance.streak.bonus_cap} days earns "
        f"the full +{content.balance.streak.bonus_cap} bonus attempts a day.",
        "**I lost the trial.** Wait out the cooldown (shown in `/guild rank`), grind a few levels "
        "and gear up from the shop or drops, then try again.",
        "**What's the best way to get stronger?** Level up, wear a full 4-piece set for its "
        "skill, keep your gear repaired and bring friends to raids.",
        "**Where do I see someone's gear and rank?** `/card <member>`.",
    ]


_BUILDERS: dict[str, Callable[[Content, WikiSettings], list[str]]] = {
    "start": _start,
    "commands": _commands,
    "combat": _combat,
    "ranks": _ranks,
    "gear": _gear,
    "loot": _loot,
    "missions": _missions,
    "bestiary": _bestiary,
    "parties": _parties,
    "trading": _trading,
    "faq": _faq,
}


# ------------------------------------------------------------------------------- the wiki


def build_wiki(content: Content, settings: Optional[WikiSettings] = None) -> Wiki:
    """Build every page. Page 0 is the contents; the rest follow `SECTIONS` order."""
    settings = settings or WikiSettings()
    body_pages: list[WikiPage] = []
    first_page: dict[str, int] = {}
    for section_id, title, _ in SECTIONS:
        pages = paginate(_BUILDERS[section_id](content, settings))
        first_page[section_id] = 1 + len(body_pages)
        for number, body in enumerate(pages, start=1):
            suffix = f" ({number}/{len(pages)})" if len(pages) > 1 else ""
            body_pages.append(WikiPage(section_id, title, f"{title}{suffix}", body))
    toc_lines = [
        f"`{number}.` **{title}**{SEP}page {first_page[section_id] + 1}\n    {summary}"
        for number, (section_id, title, summary) in enumerate(SECTIONS, start=1)
    ]
    toc = WikiPage(
        "contents",
        "Contents",
        "Contents",
        "Everything about the Adventurers' Guild in one place. Use the menu below to jump to a "
        "section, or the arrows to read in order.\n\n" + "\n".join(toc_lines),
    )
    return Wiki((toc, *body_pages), {"contents": 0, **first_page})
