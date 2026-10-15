"""Print a pacing report for the Guild RPG: how long a daily player takes to rank up.

Usage: python scripts/rpg_sim.py [--days 150] [--attempts 8] [--seeds 5] [--content content/rpg]
"""

from __future__ import annotations

import argparse
import statistics
from pathlib import Path

from yokai.rpg.content import load_content
from yokai.rpg.sim import simulate

MILESTONES = (5, 12, 20, 30, 45, 60)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--days", type=int, default=150)
    parser.add_argument("--attempts", type=int, default=8)
    parser.add_argument("--seeds", type=int, default=5)
    parser.add_argument("--content", default="content/rpg")
    args = parser.parse_args()

    content = load_content(Path(args.content))
    runs = [
        simulate(content, base_attempts=args.attempts, days=args.days, seed=s)
        for s in range(1, args.seeds + 1)
    ]
    print(f"Pacing over {args.seeds} seeds, {args.attempts} base attempts a day")
    print("level  median day  (min-max)")
    for level in MILESTONES:
        found = [r.level_day[level] for r in runs if level in r.level_day]
        if found:
            print(f"{level:>5}  {statistics.median(found):>10.0f}  ({min(found)}-{max(found)})")
        else:
            print(f"{level:>5}  not reached in {args.days} days")
    print("\nrank  median day reached")
    for rank in content.ranks:
        found = [r.rank_day[rank.id] for r in runs if rank.id in r.rank_day]
        label = f"{statistics.median(found):.0f}" if found else "never"
        print(f"{rank.id:>4}  {label}")
    first = runs[0]
    print(f"\nwin rate (seed 1): {first.win_rate():.0%} overall")
    for low, high in ((1, 30), (31, 60), (61, 90), (91, args.days)):
        print(
            f"  days {low:>3}-{high:<3} win rate {first.win_rate(low, high):.0%}, "
            f"Renown/day {first.renown_per_day(low, high):,.0f}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
