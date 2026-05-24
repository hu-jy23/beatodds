#!/usr/bin/env python3
"""Run the scanner once and print top candidates + structural violations.

Usage:
    uv run scripts/run_scanner.py
    uv run scripts/run_scanner.py --top 20
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))


from beatodds.relation_miner.miner import RelationMiner
from beatodds.scanner.scanner import Scanner


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument(
        "--mine-all",
        action="store_true",
        help="Run RelationMiner on all candidates instead of the displayed top-N",
    )
    parser.add_argument(
        "--complete-groups",
        action="store_true",
        help="Fetch full neg_risk event groups before checking group-sum violations",
    )
    args = parser.parse_args()

    scanner = Scanner()
    candidates = scanner.scan()

    print(f"\n{'='*60}")
    print(f"TOP {args.top} CANDIDATES  (total: {len(candidates)})")
    print(f"{'='*60}")
    for c in candidates[:args.top]:
        m = c.market
        s = c.snapshot
        print(f"\n[{c.priority_score:.2f}] {m.question[:70]}")
        print(f"  cid={m.condition_id[:16]}  mid={s.midpoint:.3f}  "
              f"spread={s.spread:.3f}  flags={c.scan_flags}")

    mine_targets = candidates if args.mine_all else candidates[:args.top]
    miner = RelationMiner(complete_neg_risk_groups=args.complete_groups)
    graph = miner.mine(mine_targets)

    if graph.violations:
        print(f"\n{'='*60}")
        scope = "all candidates" if args.mine_all else f"top {len(mine_targets)} candidates"
        print(f"STRUCTURAL VIOLATIONS  ({len(graph.violations)} found in {scope})")
        print(f"{'='*60}")
        for v in graph.violations[:20]:
            print(f"\n[net_edge={v.net_edge:.4f}] {v.violation_type}")
            print(f"  {v.explanation}")
    else:
        print("\nNo structural violations found in this scan.")


if __name__ == "__main__":
    main()
