"""Data models for the Guild RPG."""

from __future__ import annotations

from dataclasses import dataclass, field

STAT_NAMES: tuple[str, ...] = ("hp", "attack", "magic", "defense", "resist", "speed", "crit")

STAT_LABELS: dict[str, str] = {
    "hp": "HP",
    "attack": "Attack",
    "magic": "Magic",
    "defense": "Defense",
    "resist": "Resist",
    "speed": "Speed",
    "crit": "Crit",
}

# Stats a buff or debuff may change (HP is never modified by a status).
MODIFIABLE_STATS: tuple[str, ...] = tuple(name for name in STAT_NAMES if name != "hp")

SKILL_KINDS: tuple[str, ...] = ("damage", "heal", "buff", "debuff", "dot", "cleanse")
SKILL_TARGETS: tuple[str, ...] = ("foe", "all_foes", "ally", "all_allies", "self")
ENEMY_TARGET_RULES: tuple[str, ...] = ("random", "lowest_hp")
BASIC_ACTIONS: tuple[str, ...] = ("attack", "magic")
MISSION_KINDS: tuple[str, ...] = ("mission", "raid")
SLOTS: tuple[str, ...] = ("weapon", "head", "body", "boots", "accessory")
RARITIES: tuple[str, ...] = ("common", "uncommon", "rare", "epic")


@dataclass(frozen=True, slots=True)
class Stats:
    """The seven combat stats. `crit` is a percentage (5 means 5%)."""

    hp: int = 0
    attack: int = 0
    magic: int = 0
    defense: int = 0
    resist: int = 0
    speed: int = 0
    crit: int = 0

    def __add__(self, other: Stats) -> Stats:
        return Stats(*(getattr(self, name) + getattr(other, name) for name in STAT_NAMES))

    def scaled(self, factor: int) -> Stats:
        """Every stat multiplied by a whole number (used for per-level growth)."""
        return Stats(*(getattr(self, name) * factor for name in STAT_NAMES))


@dataclass(frozen=True, slots=True)
class Tag:
    """A gear tag, such as Oni or Kitsune.

    `bonus_2` and `bonus_4` are the set bonuses for wearing 2 or 4 working pieces: percentages of
    the final stat, except Crit, which is flat points. Wearing 4 also grants `skill`. `drawback`
    is the stat this tag's gear costs a little of (the nerf side of its trade-off).
    """

    id: str
    name: str
    role: str
    leans: tuple[str, ...]
    description: str
    bonus_2: Stats = field(default_factory=Stats)
    bonus_4: Stats = field(default_factory=Stats)
    skill: str | None = None
    drawback: str | None = None


@dataclass(frozen=True, slots=True)
class Rank:
    """A guild rank, the level at which promotion into it opens, and its XP reward share."""

    id: str
    min_level: int
    xp_share: float = 0.1
    enemy_power: int = 100  # percent applied to every stat of the enemies in this rank's missions


@dataclass(frozen=True, slots=True)
class LevelTable:
    """The level cap and the base stat curve: base at level 1, plus growth per level above 1."""

    cap: int
    base: Stats
    growth: Stats


@dataclass(frozen=True, slots=True)
class Character:
    """A member's adventurer, as stored in the database."""

    user_id: int
    level: int
    xp: int
    rank: str
    battles_won: int
    battles_lost: int
    created_ts: int
    attempts_used: int = 0
    attempts_day: str | None = None
    streak: int = 0
    last_active_day: str | None = None


@dataclass(frozen=True, slots=True)
class SkillDef:
    """A combat skill. Which fields matter depends on `kind` (see the content loader)."""

    id: str
    name: str
    kind: str
    target: str
    scaling: str = "attack"
    power: int = 0
    cooldown: int = 0
    stat: str | None = None
    percent: int = 0
    duration: int = 0


@dataclass(frozen=True, slots=True)
class EnemyDef:
    """An enemy archetype. `mult` holds percentage multipliers on the stats of its level."""

    id: str
    name: str
    mult: Stats
    actions: tuple[tuple[str, int], ...]
    target: str = "random"
    boss: bool = False


@dataclass(frozen=True, slots=True)
class Stage:
    """One wave of a mission: which enemies, at which level."""

    enemies: tuple[str, ...]
    level: int


@dataclass(frozen=True, slots=True)
class Mission:
    """A mission or raid on the guild board."""

    id: str
    name: str
    rank: str
    kind: str
    attempt_cost: int
    stages: tuple[Stage, ...]
    description: str = ""
    power: int = 100  # percent on top of the rank's enemy power, to even out difficulty

    @property
    def top_level(self) -> int:
        return max(stage.level for stage in self.stages)


@dataclass(frozen=True, slots=True)
class CombatRules:
    defense_k: int
    crit_multiplier_percent: int
    crit_cap: int
    variance_percent: int
    defend_percent: int
    stage_recovery_percent: int
    max_rounds: int


@dataclass(frozen=True, slots=True)
class XpCurve:
    base: float
    exponent: float

    def xp_to_next(self, level: int) -> int:
        """XP needed to go from `level` to `level + 1`."""
        return max(1, round(self.base * max(1, level) ** self.exponent))


@dataclass(frozen=True, slots=True)
class RewardRules:
    renown_per_level: int
    below_rank_step: float
    below_rank_floor: float


@dataclass(frozen=True, slots=True)
class PartyScaling:
    """Foe scaling by party size (index 0 is a party of one)."""

    hp_percent: tuple[int, ...]
    extra_foes: tuple[int, ...]


@dataclass(frozen=True, slots=True)
class StreakRules:
    days_per_bonus: int
    bonus_cap: int
    drop_per_missed_day: int


@dataclass(frozen=True, slots=True)
class GearRules:
    """How gear is priced, worn down and stored."""

    sell_percent: int
    repair_percent: int
    win_wear: int
    loss_wear: int
    inventory_cap: int
    roll_percent: int


@dataclass(frozen=True, slots=True)
class LootRules:
    """What missions drop."""

    drop_chance_percent: int
    boss_drop_chance_percent: int
    tier_window: int
    rarity_weights: tuple[tuple[str, int], ...]
    boss_rarity_weights: tuple[tuple[str, int], ...]


@dataclass(frozen=True, slots=True)
class ShopRules:
    """The daily guild shop."""

    stock_per_tier: int


@dataclass(frozen=True, slots=True)
class Balance:
    """Tunable numbers from balance.toml."""

    combat: CombatRules
    xp: XpCurve
    rewards: RewardRules
    party: PartyScaling
    streak: StreakRules
    gear: GearRules
    loot: LootRules
    shop: ShopRules


@dataclass(frozen=True, slots=True)
class ItemDef:
    """A piece of gear as defined in items.toml. `stats` may include negative values (nerfs)."""

    id: str
    name: str
    slot: str
    tag: str
    rarity: str
    req_level: int
    stats: Stats
    price: int
    max_durability: int
    skill: str | None = None
    retired: bool = False


@dataclass(frozen=True, slots=True)
class ItemInstance:
    """One owned piece of gear: its rolled stats and current durability."""

    id: int
    owner_id: int
    content_id: str
    rolls: Stats
    durability: int
    created_ts: int
    equipped_slot: str | None = None
