# Guild RPG Phase 6 (Trading) Implementation Plan

> **For agentic workers:** executed natively and inline (the owner delegated plans and execution). Each task is test-first.

**Goal:** Two members can trade gear for gear, gear for Renown, or give a gift, in a session both must confirm, with no way to duplicate or lose an item or any Renown.

**Architecture:** A trade session is in-memory state (`rpg/trade.py`, pure and tested alone) shown as one public message with buttons. When both sides have confirmed, one database transaction in `GearStore.commit_trade` re-checks everything (ownership, equipped, fights, balances, inventory room), moves the items, moves the Renown through the ledger and writes a history row. Anything wrong rolls the whole thing back and the session reopens with the confirmations cleared.

**Spec:** `docs/superpowers/specs/2026-10-14-guild-rpg-design.md` (sections 5, 10, 11, 12 and phase 6 of 16, plus the notes in section 18).

## Decisions made while planning

- **Command:** `/trade member:@x` (top level, as in the spec). It posts a public message with both sides' offers and buttons: **Add item**, **Remove item**, **Set Renown**, **Confirm**, **Cancel**. Item and amount entry are private to the member (a picker and a modal).
- **Gifts need no special mode:** a gift is a trade where one side offers nothing. Both members still press Confirm, so the recipient always accepts. If one side's offer is empty the ledger reason is `rpg_gift`, otherwise `rpg_trade`.
- **Changing an offer clears both confirmations.** A trade with both offers empty cannot be confirmed.
- **Limits:** up to 6 items per side; the session lasts 5 minutes; one open trade per member; no fee in this release (the spec lists fees as future work).
- **Eligibility:** both members need a character; neither may be in a fight (checked at the start, on every action and again at commit); equipped items cannot be offered, and an item that gets equipped, sold or traded while offered makes the commit refuse and drops it from the offer.
- **Inventory room:** a trade is refused if it would take either member over the 60-item cap (trades never auto-sell).
- **History:** a new `rpg_trades` table (schema v13) records each completed trade for dispute resolution.

## Review Focus

1. **Duplication and loss:** an item can never end up with two owners or none, and Renown is conserved, even under a failure midway or two trades racing for the same item (store tests, including a randomized series).
2. **Stale offers:** an item sold, equipped or given away while it sits in an offer is caught at commit and removed with a clear message (store and view tests).
3. **Changing the deal after confirming:** any edit clears both confirmations, so nobody confirms one deal and gets another (session and view tests).
4. **Strangers:** only the two members can press the buttons; both cannot open a second trade; a bot or yourself cannot be traded with (command tests).
5. **Fights:** nobody can trade while in a fight, and a fight that starts mid-session blocks the commit (store and view tests).
6. **Full inventory and low funds on the other side** produce an error that names who is short, not a generic failure (store tests).
7. **Timeout and cancel:** buttons disable, nothing moves, and the member is free to open another trade (view tests).

## Tasks

1. Pure session model, trade errors, schema v13, and the atomic `commit_trade` with its tests.
2. Trade embeds, the session view with its item picker and Renown modal, the `/trade` command, runtime registry and bot wiring.
3. Docs, QA rows, spec notes, previews, full verification and the final report.
