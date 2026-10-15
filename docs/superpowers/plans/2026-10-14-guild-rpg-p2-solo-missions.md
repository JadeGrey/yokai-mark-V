# Guild RPG Phase 2 (Solo Missions) Implementation Plan

> **For agentic workers:** executed natively and inline (the owner delegated plan approval and the execution method). Steps use checkbox syntax. Each task is test-first: write the tests, watch them fail, implement, run, lint, commit.

**Goal:** One person can play: take a ranked mission from the daily board, fight it turn by turn in a thread, and receive XP, Renown and level-ups, within a daily attempt budget with a streak.

**Architecture:** A pure combat engine (`rpg/combat/`) with seeded randomness; pure guild rules (`rpg/guild.py`, `rpg/economy.py`); store methods on the shared database for attempts, boards, fights and settlement; a fight runner that drives the engine from a Discord thread with buttons. Content (enemies, skills, missions, balance) is TOML.

**Spec:** `docs/superpowers/specs/2026-10-14-guild-rpg-design.md` (sections 6 to 9, 14, 15 and phase 2 of 16).

## Deviations from the spec (decided here, recorded in the spec in Task 9)

- **Durability and repair move to phase 3.** The spec lists them in phase 2, but they act on equipped gear, which does not exist until phase 3. Phase 2 fights deduct no durability.
- **Player skills arrive in phase 3.** The engine, content format, `Skill` button and prompt are built and tested now (with test skills); no player has a skill until tag sets exist. Enemies already use skills.
- **Enemy stats are derived, not hand-written.** An enemy definition holds percentage multipliers; a mission stage gives the level. Stats = `stats_at_level(level)` x multiplier. This keeps 28 missions maintainable and keeps enemies on the same curve as players.
- **Rewards are formulas, not per-mission numbers** (section 6 of the spec said missions carry base XP and Renown). A mission's XP is `xp_to_next(top stage level) x rank xp_share x attempt cost`; Renown is `renown_per_level x top stage level x attempt cost`. Both are tuned by `balance.toml` and `ranks.toml`.
- **Promotion is not available yet** (phase 4), so the live bot keeps every character at rank E until then. The pacing simulator models promotion as instant at the level threshold; phase 4 replaces that with real fights.
- **Balance is tuned for characters without gear.** Phase 3 adds gear power and re-tunes enemy levels and multipliers with the simulator.

## Global constraints

Same as phase 1: Python 3.12+, `from __future__ import annotations`, type hints, ruff line length 100, slash commands guild-scoped, every message through `EmbedFactory`, at most one emoji per embed, ephemeral errors, validate before deferring, mentions none, `logging` only, no new dependencies, content ids permanent, commit per task with the `Co-Authored-By: Claude Sonnet 5.5 <noreply@anthropic.com>` trailer. Run `python -m ruff format` on touched files before linting.

## Review Focus

1. **Double-spend of attempts:** two simultaneous `/guild mission` calls with one attempt left must start exactly one fight (store test).
2. **Settling twice:** a fight settled twice (a retry, a duplicate task) pays rewards once (store test).
3. **Restart mid-fight:** open fights are aborted and their attempts refunded at startup, but only for the same day (store test).
4. **A day rollover mid-session:** attempts and streak must follow the day key, and a missed day drops the streak by 3, never below 0 (guild tests).
5. **A player who walks away:** auto-defend after the turn timer, "away" after two missed rounds, and an all-away party never stalls the fight past `max_rounds` (engine and runner tests).
6. **Engine edge cases:** a skill on cooldown, a dead target, a downed actor, an empty foe side after a damage-over-time tick, and cleared stages mid-round must never raise (engine tests).
7. **A player clicking someone else's fight, or a blacklisted user:** ignored with a private message (view tests).
8. **Level cap:** XP never pushes past the cap and a capped character gains no XP (guild tests).

## Tasks

1. Content: `ranks.xp_share`, `balance.toml`, `skills.toml`, `enemies.toml`, `missions.toml` with loaders and validators and the real content.
2. Combat engine (pure) with formulas, statuses, skills, enemy AI and stage handling.
3. Guild rules (pure): day keys, streak, attempts, board draw, XP and level-ups, rewards, eligibility.
4. Store (migration V10 and settlement): attempt spending, boards, fights, abort and purge, transaction-scoped Renown credit.
5. Errors and embeds: board, mission start, fight round, fight result.
6. Fight runner and views (thread, buttons, target and skill prompts, turn timer, away rules).
7. Cog commands (`/guild board`, `/guild mission` with autocomplete), bot wiring (startup abort, purge), `/admin diag` RPG field.
8. Pacing simulator, tuning, and the pacing test.
9. Docs, QA rows, spec update, full verification.

Detailed rules for each task are in the code and tests; the formulas that matter are fixed here so later phases can rely on them.

## Fixed formulas and rules

**Damage** (`combat/formulas.py`): `raw = stat x power / 100`, where `stat` is the actor's effective Attack (physical) or Magic (magic) after status modifiers. `mitigation = K / (K + defense_stat)` with `K = balance.combat.defense_k`, using Defense for physical and Resist for magic. `damage = max(1, round(raw x mitigation x variance x crit x defend))`, with `variance` uniform in `1 +/- variance_percent/100`, `crit = crit_multiplier_percent/100` when `rng.random() < min(crit_cap, effective Crit)/100`, and `defend = defend_percent/100` when the target is defending.

**Heal:** `max(1, round(Magic x power / 100))`, capped at missing HP. **Damage over time:** per-round damage computed at application from the caster's scaling stat and the target's mitigation, then fixed for the status. **Buff/debuff:** a percentage modifier to one stat for N rounds; effective stat = `max(0, round(base x (1 + sum(mods)/100)))` (HP is never modified).

**Round order:** player actions in Speed order (ties by RNG, Speed uses effective Speed), then enemies in list order, then end of round (damage over time, status and cooldown ticks, defend cleared). `Defend` halves damage taken until the end of the round. A downed combatant (0 HP) never acts. Missing player actions default to Defend.

**Stages:** when every foe is down the stage is cleared; living players recover `stage_recovery_percent` of max HP and lose statuses; the next stage spawns. The fight is won after the last stage and lost when every player is down or `max_rounds` is exceeded.

**Party scaling** (`balance.party`): foe HP multiplier and number of extra copies of the stage's weakest non-boss foe, indexed by party size (1 to 4).

**Attempts:** allowance = `RPG_BASE_ATTEMPTS + min(streak_bonus_cap, streak // streak_per_bonus)`. On the first spend of a new day the streak updates: for each fully missed day it drops by `streak_drop_per_missed_day` (floor 0), then +1 for today. Counters reset when the day key changes.

**Rewards:** XP and Renown only on a win. Missions below the player's rank pay `max(floor, step_share ** steps_below)` of both.

## Verification

`python -m pytest -q`, `python -m ruff check .`, `python -m ruff format --check .`, and the pacing simulator test must pass. Manual Discord checks are recorded as QA rows 37 and later.
