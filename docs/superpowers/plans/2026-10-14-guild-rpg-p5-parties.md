# Guild RPG Phase 5 (Parties and Raids) Implementation Plan

> **For agentic workers:** executed natively and inline (the owner delegated plans and execution). Each task is test-first.

**Goal:** Up to four members can run a mission together from a lobby, and the board offers raids: multi-stage boss missions that need a party and pay more.

**Architecture:** A lobby is in-memory state owned by a `PartyLobbyView` in the channel. Eligibility (rank, attempts, not fighting) is checked by pure functions in `rpg/party.py` at join and again at start. Starting charges every member's attempts in one `begin_fight` transaction (already multi-member), opens one thread, and hands several `Participant`s to the existing `FightRunner`, which already supports multiple players. Raids are `kind = "raid"` missions drawn onto the board next to regular ones.

**Spec:** `docs/superpowers/specs/2026-10-14-guild-rpg-design.md` (sections 8, 9, 11, 12 and phase 5 of 16, plus the notes in section 18).

## Decisions made while planning

- **Entry point:** `/guild mission mission [party]`. Raids always open a lobby; any other mission opens one when `party` is true, otherwise it starts solo exactly as before. No new command.
- **Raids on the board:** one raid per rank from C upward each day (two per rank in the pool), shown with a "Raid:" prefix. They cost 3 attempts (C, B) or 4 (A and above), have three stages, need a party of at least 2 (`min_party`), and count as boss missions for drops.
- **Lobby:** the leader's message in the channel lists members with rank and level. Anyone may press **Join** (checked on the spot), members may **Leave**, only the leader may **Start** or **Cancel**. It expires after 3 minutes, disabling its buttons and saying so. Maximum 4.
- **Start re-checks everyone:** members who became ineligible (rank, attempts, already fighting) are removed and named, and the leader must press Start again, so nobody is charged by surprise.
- **Rewards:** each member earns their own XP, Renown and drops (already per-participant). A member below the mission's rank earns the reduced share; a party is limited to missions every member may enter.
- **Gear warnings:** parties skip the private "your gear is worn" confirm; a joiner with worn gear gets a private heads-up instead.
- **Calibration:** raids are tuned for a party of three with the gear they could afford at the rank's mid band (about 70% wins). Larger and smaller parties follow the existing party scaling.

## Review Focus

1. **Nobody is charged twice or partially:** the start is one transaction; a member without attempts makes the whole start refuse and change nothing (store test already exists, plus a lobby test).
2. **Double start / double join:** pressing Start twice starts one fight; joining twice or joining a full lobby is refused kindly (view tests).
3. **A member who started another fight meanwhile** cannot be dragged into the party (start re-check test).
4. **Lobby abandoned or leader leaves:** timeout disables the buttons; cancel disables them; nothing is charged either way (view tests).
5. **A party with a lower-ranked member** cannot enter missions above that member's rank (join test).
6. **A thread that cannot be created** refunds everyone (launch test).
7. **A solo raid attempt** is refused with a clear message (min party), including via the leader pressing Start alone.

## Tasks

1. Content and rules: `min_party`, raid content, raids on the board, raid drops, party errors, pure `party.py`.
2. Launching a party fight (shared with the solo launcher) and the lobby view/embeds.
3. `/guild mission` party option, raid labels on the board and autocomplete.
4. Raid calibration and tests.
5. Docs, QA rows, spec notes, full verification.
