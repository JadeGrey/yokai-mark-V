# Guild RPG Phase 4 (Ranks) Implementation Plan

> **For agentic workers:** executed natively and inline (the owner delegated plans and execution). Each task is test-first.

**Goal:** Ranks mean something. A member reaches a level threshold, then wins a solo promotion fight to advance; a loss costs a 6-hour cooldown. A leaderboard sorts the guild by rank, level or battles won.

**Architecture:** Promotion bosses are content (`[rank.promotion]` tables in `ranks.toml`) and run through the same fight runner as missions, as a synthetic mission of kind `promotion`. The store validates a promotion atomically when the fight starts (level, previous rank, cooldown, attempts) and applies the rank change or the cooldown when it settles. The leaderboard is a store query ordered by rank index, level or wins, shown in a paginated view. The simulator now plays real promotion fights.

**Spec:** `docs/superpowers/specs/2026-10-14-guild-rpg-design.md` (sections 8, 9, 12 and phase 4 of 16, plus the notes in section 18).

## Decisions made while planning

- **Promotion content:** every rank after the first has a `[rank.promotion]` table: a name, an attempt cost (default 2), an optional `power` percent, and one to three stages like a mission. Bosses are calibrated for roughly a 70% win rate at the threshold level for a member wearing the gear they can afford.
- **Cost and rewards:** a promotion fight spends attempts like any fight and wears gear. A win pays the mission-formula Renown only (no XP and no drops, so milestones stay personal); a loss pays nothing.
- **Cooldown:** a lost promotion sets `promo_cooldown_until` to `RPG_PROMOTION_COOLDOWN_HOURS` (default 6) later. A win clears it.
- **Starting it:** `/guild rank` shows progress, and when you are eligible a **Challenge** button starts the fight (with the same low-gear warning as missions). There is no separate command.
- **Rank is not tradeable** and is shown on the card, which also says when you are ready to promote.
- **The leaderboard** (`/guild leaderboard [sort]`) is open to everyone and has a dropdown to switch between rank, level and battles won, plus Prev/Next. Ties break deterministically.
- **Admin `rank`** still sets any rank and clears the cooldown.

## Review Focus

1. **Cannot skip ranks or levels:** a promotion only starts from the immediately previous rank at or above the threshold level, checked atomically with the attempt spend (store tests).
2. **Double promotion:** two simultaneous challenges produce one fight; settling twice promotes once (store tests).
3. **Cooldown:** a loss blocks a new challenge until the time passes; a win clears it; the cooldown survives a restart because it is stored (store tests).
4. **A restart mid-promotion** refunds the attempts and sets no cooldown (store tests).
5. **Content drift:** a character with a rank no longer in the content files still sees a card and a rank screen instead of an error (cog tests).
6. **Leaderboard ordering:** rank order follows the content ladder (not alphabetical), ties are stable, an empty guild and out-of-range pages are handled (store and view tests).
7. **Top rank:** a member at the top rank gets a friendly message, not a button (cog tests).

## Tasks

1. Content: promotion tables (models, loader, the real content), calibration, the cooldown setting, new errors.
2. Pure rank logic and the store: schema v12 (cooldown column), promotion-aware `begin_fight` and `settle_fight`, the leaderboard query.
3. Fights and commands: promotion in the runner and result, `/guild rank` with its Challenge button, `/guild leaderboard`, card hint.
4. Simulator with real promotion fights; pacing tests updated.
5. Docs, QA rows, spec notes, full verification.
