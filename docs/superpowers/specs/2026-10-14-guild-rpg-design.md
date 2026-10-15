# Adventurers' Guild RPG design

Date: 2026-10-14. Status: draft, pending owner review. Builds on the economy core
(`2026-09-29-economy-core-design.md`), the gambling and multiplayer table designs, and the challenges
design. Where they differ, this document wins for the RPG.

## 1. Goal

Give the ~10 people on the server a long-running, turn-based loot RPG that lives inside Yokai.
Players level a character, collect and trade gear, take ranked missions from an adventurers' guild
alone or in parties of up to four, and climb from rank E to SS+. Renown is the only currency: it is
earned from missions and spent on repairs and the guild shop. This makes the RPG the economy's first
real sink for Renown, which today only leaves through gambling losses and lottery tickets.

Success criteria:
- A player can create a character, take a mission, fight it turn by turn in a thread, win or lose,
  and see XP, Renown, drops and gear wear applied exactly once, even under concurrent button presses
  or a crash.
- A player who uses their daily attempts reaches rank A in about 30 days and rank SS+ in about 90 days.
  A pacing simulator test asserts this and fails when a balance change breaks it.
- Gear, Renown and attempts can never be duplicated or lost by a trade, a purchase, a repair or a
  restart.
- Updates (new items, tags, missions, enemies, a higher level cap) are content-file changes, not code
  changes, and never orphan items players already own.
- A bug in the RPG can never break playback, bet settlement or voice crediting.
- `pytest` and `ruff` clean; nothing hits the network.

## 2. Decisions (owner-approved 2026-10-14)

Setting and structure
- **Name and theme:** the "Adventurers' Guild", with Japanese-folklore tags. No Ubisoft artwork, audio
  or quotes (AGENTS.md).
- **Fights:** turn-based and interactive, in a **thread per fight**. No timed or idle expeditions.
- **Round shape:** all players choose at once (about 45 seconds), actions resolve in Speed order, then
  enemies act. Solo play is the same flow with one player.
- **Missions:** one shared daily board of missions ranked E, D, C, B, A, SS and SS+. A player may enter
  missions at their rank and below; missions below their rank pay a reduced reward. A party is limited
  by its lowest-ranked member.
- **Pacing:** a daily attempt budget. Unused attempts vanish. A streak of consecutive active days adds
  bonus attempts, and a broken streak is softened instead of reset to zero.
- **Parties:** up to 4. Raids are multi-stage boss missions at higher ranks that cost more attempts.
  Every member gets their own XP, Renown and drops. Enemies scale with party size.
- **Ranks:** reach the level threshold, then win a **solo** promotion fight against the next rank.
  After a loss there is a **6-hour cooldown**.
- **Top end:** SS+ is open-ended. The level cap rises with updates, and each update adds a gear tier
  with higher level requirements and harder missions.

Characters and gear
- **Stats (7):** HP, Attack, Magic, Defense, Resist, Speed, Crit. Defense blocks Attack; Resist blocks
  Magic. Levels raise base stats; gear adds to or subtracts from them.
- **Classless.** Archetypes emerge from **tags** on gear. Wearing 2 or 4 matching pieces gives a set
  bonus and unlocks a skill. All skills come from gear.
- **Tags (8):** Oni, Kitsune, Tengu, Kappa, Yuki-onna, Tsukumogami, Jorogumo, Baku (section 7).
- **Slots (5):** Weapon, Head, Body, Boots, Accessory.
- **Items** have a level requirement, are tradeable, and have rolled stats (about +/-10% around the
  base) and a durability value.
- **Durability** is a cliff: full strength until 0%, then the item's stats, tag bonuses and skills are
  off until repaired. Warnings at 50%, 25% and 0%. Repair costs Renown and scales with the item's value.
- **Sources:** mission drops, direct trades, and a rotating guild shop with a daily per-member
  purchase cap. Salvage and crafting are a later update.
- **Trading:** direct two-sided trades only: gear for gear, gear for Renown, or a gift. A public
  listing board may come later.

Economy and social
- **One shared Renown wallet** with everything else (gambling, `/daily`, challenges).
- **Player card** anyone can view, and an RPG **leaderboard** sortable by rank, level and battles won,
  separate from the Renown leaderboard.

Architecture
- A new `yokai/rpg/` package in the same SQLite database (new migrations from V9), so item and Renown
  changes share one transaction.
- Game content in **TOML files** read with stdlib `tomllib`. No new dependency.
- Fights live in memory. A restart mid-fight refunds the attempts (like `refund_open_bets`).

## 3. Out of scope for the first release

Consumable items; salvage and crafting; a listing board and auctions; trade fees; affix perks on items
(rolled stat ranges only); revives; level-based universal skills; seasons and prestige; world-boss
events; Discord roles per rank; admin web panel pages for the RPG. The data model leaves room for each
(item instances store their rolls; content ids are permanent; the fight log is generic).

## 4. Package layout

New package `src/yokai/rpg/`:

| Module | Responsibility |
|---|---|
| `models.py` | Dataclasses: `Character`, `ItemInstance`, `Stats`, `Mission`, `Enemy`, `Skill`, `Tag`. |
| `content/` | `loader.py` reads and validates the TOML files; `registry.py` exposes read-only lookups. |
| `stats.py` | Pure: final stats from level, gear rolls, tag bonuses and durability. |
| `combat/` | Pure fight engine: `engine.py`, `actions.py`, `ai.py`, `events.py`. No Discord code. |
| `guild.py` | Daily board generation, attempt budget and streak, rank and promotion rules, mission eligibility. |
| `economy.py` | Rewards, repair cost, shop stock and prices, drop rolls. |
| `store.py` | All database access for the RPG tables, using the shared connection and transaction. |
| `trade.py` | The two-sided trade session and its atomic commit. |
| `party.py` | Party lobby state and eligibility. |
| `fights.py` | Runs a fight: creates the thread, collects actions, advances rounds, settles. |

Elsewhere: `cogs/rpg.py` (commands), `ui/rpg_views.py` (fight, party, trade, shop, inventory views),
embed helpers in `ui/embeds.py`, typed errors in `errors.py`, and `content/rpg/*.toml` at the repo root.
Features depend on the engine and store only; nothing else in the bot imports `rpg.combat`.

The one change to existing code is a small public transaction hook on `Database`, so the RPG store can
run its reads and writes, including Renown ledger rows, inside the same `BEGIN IMMEDIATE` write
transaction that `_write_tx` provides. The exact shape is decided in the phase 1 plan after reading
`credit_wallet` and `admin_adjust_wallet`.

## 5. Data model (migrations from V9)

Each phase adds its own migration. Sketch of the tables:

```sql
CREATE TABLE rpg_characters (
  user_id INTEGER PRIMARY KEY,
  level INTEGER NOT NULL DEFAULT 1,
  xp INTEGER NOT NULL DEFAULT 0,
  rank TEXT NOT NULL DEFAULT 'E',
  battles_won INTEGER NOT NULL DEFAULT 0,
  battles_lost INTEGER NOT NULL DEFAULT 0,
  attempts_used INTEGER NOT NULL DEFAULT 0,
  attempts_day TEXT,               -- day key the counter belongs to
  streak INTEGER NOT NULL DEFAULT 0,
  last_active_day TEXT,
  promo_cooldown_until INTEGER,
  created_ts INTEGER NOT NULL
);

CREATE TABLE rpg_items (
  id INTEGER PRIMARY KEY,
  owner_id INTEGER NOT NULL,
  content_id TEXT NOT NULL,        -- permanent id from items.toml
  rolls TEXT NOT NULL,             -- JSON: rolled stat values
  durability INTEGER NOT NULL,
  created_ts INTEGER NOT NULL
);
CREATE INDEX idx_rpg_items_owner ON rpg_items(owner_id);

CREATE TABLE rpg_equipment (
  user_id INTEGER NOT NULL,
  slot TEXT NOT NULL,
  item_id INTEGER NOT NULL UNIQUE, -- an item can be in one slot only
  PRIMARY KEY (user_id, slot)
);

CREATE TABLE rpg_boards (
  day_key TEXT PRIMARY KEY,
  mission_ids TEXT NOT NULL,       -- JSON list, fixed once created
  shop_ids TEXT NOT NULL,          -- JSON list of daily shop stock
  created_ts INTEGER NOT NULL
);

CREATE TABLE rpg_shop_purchases (
  user_id INTEGER NOT NULL,
  day_key TEXT NOT NULL,
  count INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (user_id, day_key)
);

CREATE TABLE rpg_fights (
  id INTEGER PRIMARY KEY,
  ts INTEGER NOT NULL,
  mission_id TEXT NOT NULL,
  kind TEXT NOT NULL,              -- 'mission' | 'raid' | 'promotion'
  status TEXT NOT NULL,            -- 'open' | 'won' | 'lost' | 'aborted'
  participants TEXT NOT NULL,      -- JSON: user ids and the attempts each paid
  thread_id INTEGER
);
```

Renown movements use the existing `renown_transactions` ledger with the reasons `rpg_reward`,
`rpg_repair`, `rpg_shop`, `rpg_trade` and `rpg_gift`. The board is stored when first created, so a
content edit mid-day never changes today's missions or stock. Old boards and finished fight rows are
purged by the scheduler after 30 days. "Battles won" is a counter on the character, so purging the
fight log never changes the leaderboard; the log is an audit trail and the restart-recovery record.

Phase 1 (implemented) creates `rpg_characters` with only `user_id, level, xp, rank, battles_won,
battles_lost, created_ts`. The attempt, streak and cooldown columns shown above are added by the phase
that first uses them, with `_add_column_if_missing`. The public transaction hook is
`Database.transaction()`.

At startup, any `rpg_fights` row still `open` is marked `aborted` and its participants' attempts are
refunded, mirroring `refund_open_bets`.

## 6. Content files (`content/rpg/`)

| File | Contents |
|---|---|
| `tags.toml` | Each tag: name, the 2-piece stat bonus, the 4-piece skill id, a short description. |
| `items.toml` | Id, name, slot, tag, rarity, level requirement, base stats (may be negative), shop price, max durability, optional weapon skill, `retired` flag. |
| `skills.toml` | Id, name, kind (damage, heal, buff, debuff, damage over time), power, target, cooldown in rounds. |
| `enemies.toml` | Id, stats (including Defense and Resist), weighted action list, targeting rule. |
| `missions.toml` | Id, rank, stages (enemy groups), attempt cost, base XP and Renown, drop table. |
| `ranks.toml` | The ordered ranks, level thresholds, and the promotion boss for each step. |
| `levels.toml` | XP per level, base stat growth per level, the current level cap, party scaling, reward multipliers. |

Rules enforced by the loader and a validator test:
- Ids are unique and **permanent**. Updates retire content with `retired = true`: it stops dropping or
  appearing in the shop but keeps working for players who own it. Removing an id that any owned item
  references is a startup error in tests.
- Every reference resolves (an item's tag and skill, a mission's enemies and drops).
- A bad content file fails startup with a clear message, never a half-loaded game.

## 7. Characters, stats and tags

- **Final stats** = base stats at the character's level + each equipped, working item's rolled stats
  + active tag bonuses. An item at 0% durability contributes nothing, and neither do its tag count or
  skill. Equipping requires the item's level requirement to be met; gear above your level can be owned
  and traded but not worn.
- **Rolled stats:** when an item is created, each stat is rolled within about +/-10% of the base using
  the RNG, then stored on the instance.
- **Tags:** wearing 2 items with the same tag gives that tag's 2-piece stat bonus; 4 items unlock its
  skill and a stronger bonus. Weapons may also carry their own skill. Five slots mean a 4-piece set
  leaves one slot free for a different tag.

| Tag | Role | Leans toward |
|---|---|---|
| Oni | Bruiser | Attack, HP |
| Kitsune | Trickster mage | Magic, Speed |
| Tengu | Striker | Crit, Speed, Attack |
| Kappa | Healer | HP, Resist, Magic |
| Yuki-onna | Controller | Magic, Resist |
| Tsukumogami | Tank | Defense, Resist, HP |
| Jorogumo | Damage over time | Speed, Crit, Attack |
| Baku | Support | Resist, Speed, Magic |

The exact bonuses and skills are content, tuned during the gear phase. A "leans toward" tag is a
tendency in the item stats, not a hard rule.

## 8. Combat

The engine is pure Python with a seeded random generator. It accepts one action per living player per
round and returns a list of events that the UI renders.

Round:
1. **Choose:** each player picks Attack, Magic, Defend or a Skill within `RPG_TURN_SECONDS` (default
   45). A player who has not chosen in time auto-Defends. After two missed rounds in a row a player is
   marked away and the fight stops waiting for them.
2. **Resolve:** all chosen actions play out from highest Speed to lowest. Ties are broken by the RNG.
3. **Enemy phase:** enemies act in order, using their weighted action list and targeting rule.

Rules:
- Attack uses Attack against Defense; Magic uses Magic against Resist. Crit chance comes from the Crit
  stat. The damage formula lives in one function and is covered by tests.
- Defend reduces damage taken until the player's next action.
- Skills use round cooldowns. There is no mana.
- A character at 0 HP is **downed** and cannot act. The fight is lost when every player is downed.
  There are no revives in the first release.
- Raid stages run in sequence. HP carries over with a small recovery between stages.
- Enemy HP and count scale with party size.
- No consumable items in the first release.

Settlement, once per fight and inside a single transaction:
- **Win:** each participant gets XP, Renown, drops (rolled per member) and a small durability loss on
  every equipped item. `battles_won` increases.
- **Loss:** a larger durability loss, no rewards. `battles_lost` increases.
- Attempts are spent when the fight starts. They are refunded only if the bot restarts mid-fight.
- Level-ups apply the level's base stat growth; the character cannot exceed the level cap.

## 9. The guild: board, attempts, ranks

- **Day key:** the date in `RPG_TZ` (default `UTC`). The board and shop rotate at midnight in that zone.
- **Board:** each day a fixed number of missions per rank is drawn from the pool, seeded by the day key.
  The same board is shown to everyone. Missions can be repeated within a player's attempts.
- **Attempts:** a base of `RPG_BASE_ATTEMPTS` per day plus a streak bonus. Normal missions cost 1,
  higher-rank missions 2, and raids 3 to 4. Counters reset when the day key changes.
- **Streak:** a day counts when at least one attempt was used. Starting values: +1 attempt per 3
  consecutive days up to +3. A missed day drops the streak by 3 days instead of zeroing it.
- **Eligibility:** a mission's rank must be at or below the player's rank. For a party, every member
  must be eligible. Missions below the player's rank pay a reduced share of Renown and XP.
- **Promotion:** at the level threshold for the next rank, `/guild rank` offers the promotion fight, a
  solo boss. A loss sets `promo_cooldown_until` to six hours later. A win sets the new rank. The rank
  is on the character and is not an item, so it cannot be traded.

Starting values for thresholds, attempts and rewards are placeholders tuned by the simulator in
section 14.

## 10. Gear economy

- **Shop:** each day a limited stock of common and uncommon gear (filtered to level bands) is offered
  for Renown. Each member can buy at most `RPG_SHOP_DAILY_CAP` items a day. Rare gear is drop-only.
- **Drops:** each mission has a drop table with rarity weights. Each party member rolls independently.
- **Repair:** cost = item value x missing durability fraction x a content-defined factor, so strong gear
  costs more to maintain. `/gear repair` handles one item or all of them.
- **Warnings:** the fight result lists any item that crossed 50%, 25% or 0%. The mission start screen
  also lists low and broken gear before the player commits an attempt; it warns but does not block.
- **Trading:** all gear is tradeable, including drops. Equipped items must be unequipped first, and a
  player in a fight cannot trade. Durability travels with the item.

## 11. Parties, raids and trading

**Party lobby:** `/guild mission` on a party-capable mission opens a lobby in the channel. Members join
with a button. Eligibility (rank and attempts) is checked at join and again at start. The leader
starts the fight, the attempts are charged to every member at that moment, and the bot creates the
thread. Views time out, disable themselves, and only act for members.

**Trade session:** `/trade @member` creates a session in memory. Each side adds items and Renown to an
offer, then confirms. Changing an offer clears both confirmations. When both confirm, one transaction
rechecks that every item still belongs to its offerer, is unequipped and is not in a fight, that both
wallets cover their offered Renown, and then swaps everything or nothing. A gift is a one-sided trade
that the recipient accepts. Sessions time out and disable their buttons.

## 12. Commands

Slash commands only, guild-scoped, grouped to stay well under Discord's limits. All responses go
through `ui/embeds.py`, errors are ephemeral, and input is validated before deferring.

| Command | Purpose |
|---|---|
| `/guild join` | Create your character. |
| `/guild board` | Today's missions by rank. |
| `/guild mission` | Pick a mission to run solo or open a party lobby. |
| `/guild rank` | Rank progress and the promotion fight. |
| `/guild leaderboard` | Sort by rank, level or battles won. |
| `/card [member]` | The public player card. |
| `/gear inventory\|equip\|unequip\|repair\|shop\|buy` | Manage and buy gear. |
| `/trade @member` | Open a trade session. |
| `/admin rpg ...` | Owner and admin tools: grant an item, set level or rank, reset a character. Audited. |

`/admin diag` gains an RPG check: content loaded, tables present, and the permissions to create threads.
The bot has the Administrator permission on the server, so thread permissions are expected to be
present, but the check stays as a safety net. It arrives with phase 2, when fights first need thread
permissions.

## 13. Configuration (document all in `.env.example`)

Operational settings only. Balance numbers live in the TOML files.

| Variable | Default | Meaning |
|---|---|---|
| `RPG_ENABLED` | `true` | Master switch; when false the commands are not registered. |
| `RPG_CONTENT_DIR` | `content/rpg` | Folder with the TOML content files, relative to where the bot starts. |
| `RPG_TZ` | `UTC` | Time zone for the day key. |
| `RPG_BASE_ATTEMPTS` | `8` | Attempts per day before streak bonuses. |
| `RPG_TURN_SECONDS` | `45` | Time to choose an action each round. |
| `RPG_PROMOTION_COOLDOWN_HOURS` | `6` | Wait after losing a promotion fight. |
| `RPG_SHOP_DAILY_CAP` | `3` | Items a member can buy from the shop per day. |

## 14. Testing

- **Combat engine:** seeded replays of exact fights, one test per rule (speed order, Defend, crit,
  downed, away players, multi-stage raids, party scaling).
- **Stats and tags:** final stats with and without broken gear, 2-piece and 4-piece thresholds,
  negative stat gear.
- **Store:** a temporary SQLite file for every table. Equipment uniqueness, attempt and streak
  rollover on the day key, shop caps, and refund of `open` fights at startup.
- **Trades:** atomicity (a failure midway leaves both sides unchanged), no item in two places,
  ownership and balance rechecked at commit, concurrent attempts to trade the same item.
- **Content validator:** unique ids, every reference resolves, retired content stays valid, bad files
  fail loudly.
- **Pacing simulator:** a scripted policy player plays the curve and asserts about 30 days to A and
  about 90 days to SS+. It also reports net Renown per active day to show whether the RPG inflates the
  economy.
- **Views:** button permissions, timeouts and disabling.
- A manual QA checklist for the Discord side (threads, buttons, real timing), in the style of the
  challenges checklist.

## 15. Risks

- **Balance** is the largest risk and the reason for the simulator. Expect tuning after the first real
  play, which is why numbers are in TOML and not in code.
- **Content updates orphaning items:** prevented by permanent ids and the validator test.
- **Duplication exploits** through trades or restarts: one transaction per trade, ownership recheck, and
  `open` fights aborted and refunded at startup.
- **Discord rate limits:** one embed edit per round per fight keeps several simultaneous fights safe.
- **Concurrent button presses:** one lock per fight; action submission is idempotent per player per round.
- **Write contention** with the admin panel process: all writes use `BEGIN IMMEDIATE` through the
  shared transaction hook.
- **Scope:** this is a large feature. The phases below each ship something playable or testable and
  stop for review.

## 16. Phases

Each phase gets its own plan, ends with `pytest` and `ruff` clean, and stops for owner review.

1. **Foundation:** content loader and validator, migrations, character creation, stat calculation, the
   transaction hook, `/guild join` and `/card`.
2. **Solo missions:** the combat engine, the daily board, the attempt budget and streak, thread fights,
   rewards and level-ups, durability and repair. One person can play.
3. **Gear:** inventory and equipping, the eight tags with bonuses and skills, the guild shop and drops.
4. **Ranks:** thresholds, promotion fights with the cooldown, and the leaderboard.
5. **Parties and raids:** party lobbies, multi-stage raids and enemy scaling.
6. **Trading:** the trade session for gear, Renown and gifts.

Acceptance criteria for each phase are written in its plan. Phases 1 and 2 are written first; later
plans are written when their phase starts, so that balance learned from play can feed in.

## 17. Open tuning items (decided in plans or by the simulator, not design questions)

XP curve and level thresholds; the exact attempt costs and streak numbers; reward and drop numbers;
repair and shop prices; the damage formula constants; per-tag bonuses and skills; the below-rank reward
share; how many missions of each rank appear per day.

## 18. Implementation notes (kept current as phases ship)

**Phase 2 (solo missions) changed these details from the sections above:**
- **Durability and repair moved to phase 3.** They act on equipped gear, which does not exist until
  phase 3. Phase 2 fights deduct no durability. Phase 3 also re-tunes `enemy_power` once gear adds
  player power.
- **Rewards are formulas, not per-mission numbers.** XP = `xp_to_next(top stage level) x rank xp_share
  x attempt cost`, Renown = `renown_per_level x top stage level x attempt cost`, both scaled by the
  below-rank share. Missions in `missions.toml` carry no reward numbers.
- **Enemy stats are derived.** An enemy definition holds percentage multipliers; a stage gives the
  level; the rank's `enemy_power` (in `ranks.toml`) scales every stat of that rank's enemies.
  Stats = `stats_at_level(level) x mult x enemy_power`.
- **New content files:** `balance.toml` (combat rules, XP curve, reward rules, party scaling, streak
  rules), `skills.toml`, `enemies.toml`, `missions.toml` (28 missions, four per rank). `ranks.toml`
  gained `xp_share` and `enemy_power`.
- **Skills:** the engine, the content format and the Skill button exist, but no player has a skill
  until tag sets arrive in phase 3. Enemies already use skills (damage over time, debuffs, area hits).
- **Promotion** arrives in phase 4; until then every character stays rank E. The pacing simulator
  promotes instantly at the level threshold.
- **Config added:** `RPG_TZ`, `RPG_BASE_ATTEMPTS`, `RPG_TURN_SECONDS`.
- **Schema v10** adds the attempt and streak columns to `rpg_characters`, `rpg_boards` (without the
  shop column, which phase 3 adds) and `rpg_fights`. History older than 30 days is purged at startup.
- **Away players:** the runner does not wait for a player marked away. When nobody is waited for, the
  round pauses `idle_delay` (1.5 seconds) before resolving, which is also the window in which an away
  player can act to rejoin.
- **Calibration:** with base stats and always-attack play, the simulator reaches level 30 (rank A) on
  about day 28 and the level cap on about day 91 (spec targets: 30 and 90), with a win rate of about
  80% right after each promotion and about 97% overall. Renown income is about 340 a day in the first
  month, rising to about 1,200 at the cap. Phase 3 prices (shop, repair) are set against these.

**Phase 3 (gear) added or changed these details:**
- **Four rarities** (common, uncommon, rare, epic) and **five gear tiers** with level requirements 1,
  12, 24, 38 and 52. The 800 item definitions are generated by `src/yokai/rpg/itemgen.py`
  (`scripts/gen_rpg_items.py` writes `content/rpg/items.toml`); a test keeps the file in sync.
  Retiring an item means adding its id to `RETIRED` in the generator.
- **Item stats:** a budget in "level equivalents" (`0.12 x level requirement x slot weight x rarity
  multiplier`) spread over the tag's lean stats, minus a tag-specific drawback stat (the nerf).
  Instances store rolled stats, +/-10% around the base.
- **Tags** gained `bonus_2`, `bonus_4`, `skill` and `drawback` in `tags.toml`. Set bonuses are
  percentages of the final stat (Crit bonuses are flat points). Four working pieces grant the tag's
  skill; epic weapons of six tags carry one extra skill. Eight player skills were added to
  `skills.toml`.
- **Durability:** out of 100 (110, 125 and 150 for uncommon, rare and epic). Wear is 3 per fight won
  and 8 per fight lost, applied to every equipped item at settlement. Warnings at 50%, 25% and 0%
  appear in the result and, for items at 25% or below, as a confirm step before a mission
  (`/guild mission` shows Start anyway / Cancel and spends nothing until confirmed). A full repair
  costs 35% of the item's price, pro rata; selling pays 30%; the inventory holds 60 items and extra
  drops are sold automatically.
- **Loot:** 35% drop chance per member per win (bosses 100%) from items within 15 levels below the
  mission's top level; rarity weights differ for bosses. A rarity with nothing to give falls back to
  the next one down.
- **Shop:** stock is stored in `rpg_shops` (a separate table, not a column on `rpg_boards`), three
  common or uncommon items per gear tier, drawn per guild day. `RPG_SHOP_DAILY_CAP` (default 3)
  limits purchases per member per day. A member may buy gear above their level (it simply cannot be
  worn yet).
- **Schema v11:** `rpg_items`, `rpg_equipment`, `rpg_shops`, `rpg_shop_purchases`. Renown ledger
  reasons: `rpg_shop`, `rpg_repair`, `rpg_sale` (plus the existing `rpg_reward`).
- **Admin tools** (`/admin rpg grant`, `level`, `rank`, `reset`) arrived in this phase and write to
  the admin audit log inside the same transaction as the change.
- **Calibration:** missions gained a `power` field (percent on top of the rank's `enemy_power`) so
  missions within a rank are comparably hard. E and D are tuned for a member with no gear; C and
  above assume the gear the simulator's player can afford. The simulator models gear purchases and
  daily repairs. With the shipped numbers a daily player reaches level 30 on about day 31 and the
  cap on about day 91, wins about 65 to 90% of fights in the first days after each promotion, earns
  about 330 Renown a day in the first month and 1,000 at the cap, and spends about 70% of income in
  the first month (buying a set for each tier) and about 40% later. The late-game surplus (about
  +600 Renown a day) is deliberate headroom for the sinks later phases add (trade fees, prestige);
  raise prices or `repair_percent` if inflation shows up in play.

**Phase 4 (ranks) added or changed these details:**

- **Promotion trials** live in `ranks.toml` as a `[rank.promotion]` table per rank (name,
  `attempt_cost`, `power` relative to the rank's `enemy_power`, stages). They run as solo fights of
  kind `promotion`, are validated atomically with the attempt spend (rank, level, cooldown and no
  open trial are re-checked inside `begin_fight`) and pay Renown only: no XP, no drops. Gear still
  wears.
- **Cooldown:** a lost trial stores `promo_cooldown_until` (schema v12) for
  `RPG_PROMOTION_COOLDOWN_HOURS` (default 6, 0 disables). A win clears it and sets the new rank in
  the same transaction as the settlement. An aborted trial refunds the attempts and sets no cooldown.
- **Commands:** `/guild rank` (private; Challenge button for the owner only) and `/guild
  leaderboard [sort] [page]` (public; sort by rank, level or battles won). `/card` mentions when a
  promotion is ready or recovering. A rank id missing from the content files is handled without
  crashing (the screen says the rank is unknown; admins can set it).
- **Calibration:** each trial is tuned to be won about 70% of the time at the threshold level with
  the gear a member could have bought by then (none for D). The simulator now fights real trials
  (a loss blocks further tries that day, a little harsher than the real cooldown). With the shipped
  numbers a daily player reaches rank A on about day 31, level 30 on day 30 to 32 and the cap on
  day 93 to 94. SS missions were nudged harder (`enemy_power` 54 to 55) because the first
  post-promotion days there were too easy once trials took a few attempts.

**Phase 5 (parties and raids) added or changed these details:**

- **Entry point:** `/guild mission mission [party]`. Raids always open a lobby; other missions
  open one when `party` is true. The lobby is a public message with Join, Leave, Start and Cancel
  buttons; it lives in memory, closes after three minutes and charges nothing until the leader
  starts it. Eligibility lives in the pure `rpg/party.py` and is checked when someone joins and
  again for everyone at Start (members who are no longer eligible are removed and named, and the
  leader presses Start again).
- **Launch:** `launch_party_fight` charges all members in one `begin_fight` transaction, opens one
  thread, adds every member and starts the same `FightRunner` used for solo fights. A thread that
  cannot be created refunds everyone. Each member is rewarded and wears gear separately.
- **Raids** are `kind = "raid"` missions with `min_party = 2` (the parser rejects a raid that
  allows a party of one). The board draws one raid per rank from C upward each day from a pool of
  two, using its own seed so regular missions are never reshuffled by raid changes. They cost 3 or
  4 attempts, have three stages with a boss, and use the boss drop table.
- **Party scaling changed.** The placeholder scaling (extra copies of the weakest foe) let a party
  face several copies of a lone monster and made a trio far harder than a pair or a quartet. Now
  extra foes join only stages that already have a group, and `balance.toml [party]` is
  `hp_percent = [100, 120, 230, 255]`, `extra_foes = [0, 1, 1, 2]`. Raid escorts were added so
  every boss stage can grow.
- **Calibration:** raids are tuned (per-mission `power`) for about 70% wins with a party of three
  wearing typical gear for the band; with the shipped numbers an always-attack party wins about
  66% of raids with two members, 87% with three and 74% with four. Ordinary missions are slightly
  safer in a party than alone. Win rate versus enemy strength is a cliff for a scripted party, so
  real play (skills, defending, healing) will differ; adjust per-raid `power` if it does.
- **Not included:** parties skip the private worn-gear confirmation (a joiner gets a heads-up),
  and there is no matchmaking or invite list. Trading is phase 6.

