"""Small valid RPG content samples and a helper that writes them to a folder."""

from __future__ import annotations

from pathlib import Path

LEVELS = """\
cap = 60

[base]
hp = 100
attack = 10
magic = 10
defense = 5
resist = 5
speed = 10
crit = 5

[growth]
hp = 12
attack = 2
magic = 2
defense = 1
resist = 1
speed = 1
crit = 0
"""

RANKS = """\
[[rank]]
id = "E"
min_level = 1
xp_share = 0.1

[[rank]]
id = "D"
min_level = 5
xp_share = 0.1

[rank.promotion]
name = "Trial of Claws"
stages = [{ enemies = ["cat"], level = 5 }]

[[rank]]
id = "C"
min_level = 12
xp_share = 0.08

[rank.promotion]
name = "Trial of Fangs"
attempt_cost = 3
power = 120
stages = [
  { enemies = ["cat", "gaki"], level = 12 },
  { enemies = ["cat"], level = 13 },
]
"""

TAGS = """\
[[tag]]
id = "oni"
name = "Oni"
role = "Bruiser"
leans = ["attack", "hp"]
drawback = "speed"
description = "Ogres of folklore."
bonus_2 = { attack = 8 }
bonus_4 = { attack = 12, hp = 10 }
skill = "rend"

[[tag]]
id = "kitsune"
name = "Kitsune"
role = "Trickster mage"
leans = ["magic", "speed"]
description = "Fox spirits."
"""

BALANCE = """\
[combat]
defense_k = 40
crit_multiplier_percent = 150
crit_cap = 75
variance_percent = 10
defend_percent = 50
stage_recovery_percent = 10
max_rounds = 40

[xp]
base = 30
exponent = 1.5

[rewards]
renown_per_level = 4
below_rank_step = 0.6
below_rank_floor = 0.1

[party]
hp_percent = [100, 170, 240, 310]
extra_foes = [0, 0, 1, 1]

[streak]
days_per_bonus = 3
bonus_cap = 3
drop_per_missed_day = 3

[gear]
sell_percent = 30
repair_percent = 35
win_wear = 3
loss_wear = 8
inventory_cap = 60
roll_percent = 10

[loot]
drop_chance_percent = 35
boss_drop_chance_percent = 100
tier_window = 15
rarity_weights = { common = 60, uncommon = 28, rare = 10, epic = 2 }
boss_rarity_weights = { common = 15, uncommon = 35, rare = 38, epic = 12 }

[shop]
stock_per_tier = 3
"""

ITEMS = """[[item]]
id = "oni_weapon_t1_common"
name = "Worn Oni Kanabo"
slot = "weapon"
tag = "oni"
rarity = "common"
req_level = 1
price = 60
max_durability = 100
stats = { hp = 2, attack = 1, speed = -1 }

[[item]]
id = "oni_head_t1_common"
name = "Worn Oni Mask"
slot = "head"
tag = "oni"
rarity = "common"
req_level = 1
price = 36
max_durability = 100
stats = { hp = 1, attack = 1 }

[[item]]
id = "kitsune_head_t2_uncommon"
name = "Honed Kitsune Mask"
slot = "head"
tag = "kitsune"
rarity = "uncommon"
req_level = 12
price = 270
max_durability = 110
stats = { magic = 3, speed = 2, defense = -1 }

[[item]]
id = "oni_weapon_t2_epic"
name = "Gilded Oni Kanabo"
slot = "weapon"
tag = "oni"
rarity = "epic"
req_level = 12
price = 2160
max_durability = 150
stats = { hp = 30, attack = 9, speed = -2 }
skill = "hex"
"""

SKILLS = """\
[[skill]]
id = "rend"
name = "Rend"
kind = "dot"
target = "foe"
scaling = "attack"
power = 40
duration = 3
cooldown = 3

[[skill]]
id = "hex"
name = "Hex"
kind = "debuff"
target = "foe"
stat = "attack"
percent = 25
duration = 2
cooldown = 4
"""

ENEMIES = """\
[[enemy]]
id = "gaki"
name = "Gaki"
mult = { hp = 70, attack = 85, magic = 50, defense = 70, resist = 70, speed = 85, crit = 80 }
actions = [{ do = "attack", weight = 1 }]

[[enemy]]
id = "cat"
name = "Bakeneko"
mult = { hp = 85, attack = 110, magic = 80, defense = 80, resist = 80, speed = 130, crit = 150 }
actions = [{ do = "attack", weight = 2 }, { do = "rend", weight = 1 }]
target = "lowest_hp"
"""

MISSIONS = """\
[[mission]]
id = "e_pests"
name = "Pests"
rank = "E"
attempt_cost = 1
stages = [{ enemies = ["gaki", "gaki"], level = 1 }]

[[mission]]
id = "e_cats"
name = "Cats"
rank = "E"
attempt_cost = 1
stages = [
  { enemies = ["gaki"], level = 2 },
  { enemies = ["cat"], level = 3 },
]

[[mission]]
id = "d_cats"
name = "More Cats"
rank = "D"
attempt_cost = 2
stages = [{ enemies = ["cat", "cat"], level = 6 }]
"""

DEFAULTS = {
    "levels.toml": LEVELS,
    "ranks.toml": RANKS,
    "tags.toml": TAGS,
    "balance.toml": BALANCE,
    "skills.toml": SKILLS,
    "enemies.toml": ENEMIES,
    "missions.toml": MISSIONS,
    "items.toml": ITEMS,
}


def write_content_dir(tmp_path: Path, **overrides: str) -> Path:
    """Write a small valid content folder. Override a file with `name=text` (no `.toml`)."""
    for file_name, text in DEFAULTS.items():
        key = file_name.removesuffix(".toml")
        (tmp_path / file_name).write_text(overrides.get(key, text), encoding="utf-8")
    return tmp_path


def mutate(text: str, old: str, new: str) -> str:
    assert old in text, f"test bug: {old!r} not in the sample text"
    return text.replace(old, new, 1)
