#!/usr/bin/env python3
"""Evaluate paper-trading decisions from data/paper_decisions.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from beatodds.evaluation.paper_eval import (
    load_paper_decisions,
    mark_decisions_to_market,
    paper_mark_summary,
    select_decisions_by_confidence,
)


def _fmt_money(value: float | None) -> str:
    if value is None:
        return "?"
    return f"${value:,.2f}"


def _fmt_pct(value: float | None) -> str:
    if value is None:
        return "?"
    return f"{value:+.2%}"


def _mark_to_dict(mark) -> dict:
    decision = mark.decision
    return {
        "status": mark.status,
        "order_id": decision.order_id,
        "run_id": decision.run_id,
        "account_id": decision.account_id,
        "condition_id": decision.condition_id,
        "question": decision.question,
        "side": decision.side,
        "token_id": mark.token_id,
        "confidence": decision.confidence,
        "filled_notional": decision.filled_notional,
        "filled_shares": decision.filled_shares,
        "avg_price": decision.avg_price,
        "current_bid": mark.current_bid,
        "current_ask": mark.current_ask,
        "current_value": mark.current_value,
        "cost_basis": mark.cost_basis,
        "pnl": mark.pnl,
        "return_pct": mark.return_pct,
        "created_at": decision.created_at.isoformat() if decision.created_at else "",
        "marked_at": mark.marked_at.isoformat(),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate paper decision earnings")
    parser.add_argument("--log-path", type=Path, default=Path("data") / "paper_decisions.jsonl")
    parser.add_argument("--account-id", help="Only evaluate one paper account")
    parser.add_argument("--run-id", help="Only evaluate one paper-trader run")
    parser.add_argument("--top-k", type=int, help="Evaluate top K buy decisions by confidence")
    parser.add_argument("--all", action="store_true", help="Evaluate all buy decisions")
    parser.add_argument("--json-out", type=Path, help="Write detailed mark report JSON")
    args = parser.parse_args()

    if args.top_k is not None and args.all:
        print("Error: choose either --top-k or --all, not both.")
        sys.exit(1)

    decisions = load_paper_decisions(
        args.log_path,
        account_id=args.account_id,
        run_id=args.run_id,
    )
    top_k = None if args.all or args.top_k is None else args.top_k
    selected = select_decisions_by_confidence(decisions, top_k=top_k)
    marks = mark_decisions_to_market(selected)
    summary = paper_mark_summary(marks)

    selector = "all" if top_k is None else f"top {top_k} by confidence"
    print(f"\nPAPER DECISION EVAL ({selector})")
    print(f"log = {args.log_path}")
    if args.account_id:
        print(f"account = {args.account_id}")
    if args.run_id:
        print(f"run_id = {args.run_id}")
    print(
        f"selected={summary['selected']}  marked={summary['marked']}  "
        f"unmarked={summary['unmarked']}"
    )
    print(
        f"invested={_fmt_money(float(summary['invested']))}  "
        f"value={_fmt_money(float(summary['current_value']))}  "
        f"pnl={_fmt_money(float(summary['pnl']))}  "
        f"return={_fmt_pct(float(summary['return_pct']))}"
    )
    print(f"winners={summary['winners']}  losers={summary['losers']}")

    print("\nDECISIONS")
    for idx, mark in enumerate(marks, start=1):
        decision = mark.decision
        pnl = _fmt_money(mark.pnl)
        ret = _fmt_pct(mark.return_pct)
        bid = f"{mark.current_bid:.3f}" if mark.current_bid is not None else "?"
        print(
            f"[{idx}] {mark.status:<16} conf={decision.confidence:.2f} "
            f"{decision.side:<3} cost={_fmt_money(mark.cost_basis)} "
            f"bid={bid} pnl={pnl} ret={ret}"
        )
        print(f"    {decision.question[:100]}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "summary": summary,
            "marks": [_mark_to_dict(mark) for mark in marks],
        }
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nJSON report written to {args.json_out}")


if __name__ == "__main__":
    main()
