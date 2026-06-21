#!/usr/bin/env python3
"""Evaluate paper-trading decisions from data/paper_decisions.jsonl."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from beatodds.evaluation.paper_eval import (
    load_paper_decisions,
    mark_decisions_to_market,
    paper_mark_summary,
    select_decisions_by_confidence,
)
from beatodds.evaluation.paper_store import (
    load_paper_account,
    load_paper_positions,
    record_paper_sell,
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
        "mark_source": mark.mark_source,
        "quote_time": mark.quote_time.isoformat() if mark.quote_time else "",
        "created_at": decision.created_at.isoformat() if decision.created_at else "",
        "marked_at": mark.marked_at.isoformat(),
    }


def _report_text(
    *,
    selector: str,
    log_path: Path,
    account_id: str | None,
    run_id: str | None,
    summary: dict,
    marks: list,
    top_k: int | None,
) -> str:
    generated_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    lines = [
        "# Paper Trading Mark-to-Market Report",
        "",
        f"- Generated at: {generated_at}",
        f"- Decision log: `{log_path}`",
        f"- Selection: {selector}",
        f"- Top K: {top_k}",
        "- Market prices: live CLOB quotes when available; latest exact-token "
        "history otherwise",
    ]
    if account_id:
        lines.append(f"- Account: `{account_id}`")
    if run_id:
        lines.append(f"- Run: `{run_id}`")
    lines.extend([
        "",
        "## Summary",
        "",
        f"- Selected buy decisions: {summary['selected']}",
        f"- Marked decisions: {summary['marked']}",
        f"- Unmarked decisions: {summary['unmarked']}",
        f"- Invested capital: {_fmt_money(float(summary['invested']))}",
        f"- Current liquidation value: {_fmt_money(float(summary['current_value']))}",
        f"- Unrealized PnL: {_fmt_money(float(summary['pnl']))}",
        f"- Unrealized return: {_fmt_pct(float(summary['return_pct']))}",
        f"- Winners / losers: {summary['winners']} / {summary['losers']}",
        "",
        "## Decisions",
        "",
        "| # | Status | Source | Side | Confidence | Cost | Bid | PnL | Return | Question |",
        "|---:|---|---|---|---:|---:|---:|---:|---:|---|",
    ])
    for idx, mark in enumerate(marks, start=1):
        decision = mark.decision
        bid = f"{mark.current_bid:.3f}" if mark.current_bid is not None else "?"
        question = decision.question.replace("|", "\\|").replace("\n", " ")
        lines.append(
            f"| {idx} | {mark.status} | {mark.mark_source or '-'} | {decision.side} | "
            f"{decision.confidence:.2f} | "
            f"{_fmt_money(mark.cost_basis)} | {bid} | {_fmt_money(mark.pnl)} | "
            f"{_fmt_pct(mark.return_pct)} | {question[:140]} |"
        )
    lines.extend([
        "",
        "Note: PnL is mark-to-market using current best bids as liquidation value. "
        "It is not final resolution PnL.",
        "",
    ])
    return "\n".join(lines)


def _write_reports(
    report_dir: Path,
    *,
    top_k: int | None,
    selector: str,
    log_path: Path,
    account_id: str | None,
    run_id: str | None,
    summary: dict,
    marks: list,
) -> tuple[Path, Path]:
    report_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    if top_k is not None:
        markdown_path = report_dir / f"paper_eval_top_{top_k}_{stamp}.md"
        json_path = report_dir / f"paper_eval_top_{top_k}_{stamp}.json"
    else:
        markdown_path = report_dir / f"paper_eval_{stamp}.md"
        json_path = report_dir / f"paper_eval_{stamp}.json"
    text = _report_text(
        selector=selector,
        log_path=log_path,
        account_id=account_id,
        run_id=run_id,
        summary=summary,
        marks=marks,
        top_k=top_k,
    )
    payload = {
        "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
        "selector": selector,
        "log_path": str(log_path),
        "account_id": account_id,
        "run_id": run_id,
        "summary": summary,
        "marks": [_mark_to_dict(mark) for mark in marks],
        "top_k": top_k,
    }
    markdown_path.write_text(text, encoding="utf-8")
    json_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    (report_dir / "paper_eval_latest.md").write_text(text, encoding="utf-8")
    (report_dir / "paper_eval_latest.json").write_text(
        json.dumps(payload, indent=2),
        encoding="utf-8",
    )
    return markdown_path, json_path


def _sell_strategy_text(args) -> str:
    parts = [f"return >= {_fmt_pct(args.sell_min_return)}"]
    if args.sell_min_score is not None:
        parts.append(f"(current_bid - avg_entry_price) * confidence >= {args.sell_min_score:.4f}")
    return " OR ".join(parts)


def _sell_candidates(
    marks: list,
    *,
    account_id: str,
    condition_id: str | None,
    side: str | None,
    sell_fraction: float,
    fee_rate_bps: float,
    min_return: float,
    min_score: float | None,
) -> list[dict]:
    positions = load_paper_positions(account_id)
    mark_by_key = {}
    for mark in marks:
        decision = mark.decision
        key = (decision.condition_id, decision.side)
        current = mark_by_key.get(key)
        if current is None or decision.confidence > current.decision.confidence:
            mark_by_key[key] = mark

    candidates = []
    for position in positions:
        if condition_id and position.condition_id != condition_id:
            continue
        if side and position.side != side:
            continue
        mark = mark_by_key.get((position.condition_id, position.side))
        if mark is None or mark.status != "marked" or mark.current_bid is None:
            candidates.append({
                "position": position,
                "mark": mark,
                "eligible": False,
                "reason": "no current bid mark",
            })
            continue
        if mark.mark_source != "live_clob":
            candidates.append({
                "position": position,
                "mark": mark,
                "eligible": False,
                "reason": "historical mark is not executable",
            })
            continue

        shares_to_sell = round(position.shares * sell_fraction, 8)
        gross_proceeds = shares_to_sell * mark.current_bid
        sell_fee = gross_proceeds * max(0.0, fee_rate_bps) / 10_000
        cost_fraction = shares_to_sell / position.shares if position.shares else 0.0
        cost_basis = position.cost_basis * cost_fraction
        prior_fees = position.fees_paid * cost_fraction
        net_proceeds = gross_proceeds - sell_fee
        realized_pnl = net_proceeds - cost_basis - prior_fees
        return_pct = realized_pnl / (cost_basis + prior_fees) if cost_basis + prior_fees else 0.0
        score = (mark.current_bid - position.avg_price) * mark.decision.confidence
        profit_ok = return_pct >= min_return
        score_ok = min_score is not None and score >= min_score
        candidates.append({
            "position": position,
            "mark": mark,
            "shares_to_sell": shares_to_sell,
            "price": mark.current_bid,
            "gross_proceeds": gross_proceeds,
            "sell_fee": sell_fee,
            "net_proceeds": net_proceeds,
            "realized_pnl": realized_pnl,
            "return_pct": return_pct,
            "score": score,
            "eligible": profit_ok or score_ok,
            "reason": (
                "profit target"
                if profit_ok
                else "score target"
                if score_ok
                else "requirements not met"
            ),
        })
    return candidates


def _execute_sells(
    candidates: list[dict],
    *,
    account_id: str,
    run_id: str,
    fee_rate_bps: float,
    dry_run: bool,
) -> list[dict]:
    results = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    sell_run_id = run_id or f"paper-eval-sell-{stamp}"
    for candidate in candidates:
        if not candidate.get("eligible"):
            continue
        position = candidate["position"]
        if dry_run:
            results.append({**candidate, "order": None, "sold": False})
            continue
        order = record_paper_sell(
            account_id=account_id,
            run_id=sell_run_id,
            condition_id=position.condition_id,
            token_id=position.token_id,
            side=position.side,
            shares=float(candidate["shares_to_sell"]),
            price=float(candidate["price"]),
            event_id=position.event_id,
            category=position.category,
            question=position.question,
            fee_rate_bps=fee_rate_bps,
            decision_reason=(
                "run_paper_eval.py --sell; "
                f"{candidate['reason']}; realized_return={candidate['return_pct']:+.2%}; "
                f"score={candidate['score']:+.4f}"
            ),
        )
        results.append({**candidate, "order": order, "sold": True})
    return results


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate paper decision earnings")
    parser.add_argument("--log-path", type=Path, default=Path("data") / "paper_decisions.jsonl")
    parser.add_argument("--account-id", help="Only evaluate one paper account")
    parser.add_argument("--run-id", help="Only evaluate one paper-trader run")
    parser.add_argument("--top-k", type=int, help="Evaluate top K buy decisions by confidence")
    parser.add_argument("--all", action="store_true", help="Evaluate all buy decisions")
    parser.add_argument("--condition-id", help="Only evaluate/sell one market condition id")
    parser.add_argument("--side", choices=["YES", "NO"], help="Only evaluate/sell one held side")
    parser.add_argument(
        "--sell",
        action="store_true",
        help="Sell open positions that meet exit rules",
    )
    parser.add_argument(
        "--sell-all-eligible",
        action="store_true",
        help="Allow --sell to apply across all eligible open positions",
    )
    parser.add_argument(
        "--sell-min-return",
        type=float,
        default=0.05,
        help="Minimum net realized return required for selling; default 0.05",
    )
    parser.add_argument(
        "--sell-min-score",
        type=float,
        help="Optional score trigger: (current_bid - avg_entry_price) * confidence",
    )
    parser.add_argument(
        "--sell-fraction",
        type=float,
        default=1.0,
        help="Fraction of each eligible open position to sell; default 1.0",
    )
    parser.add_argument(
        "--sell-fee-bps",
        type=float,
        help="Override account fee bps for sell fills",
    )
    parser.add_argument(
        "--sell-dry-run",
        action="store_true",
        help="Show eligible sells without recording them",
    )
    parser.add_argument("--json-out", type=Path, help="Write detailed mark report JSON")
    parser.add_argument(
        "--report-dir",
        type=Path,
        help="Write timestamped English reports into this directory",
    )
    args = parser.parse_args()

    if args.top_k is not None and args.all:
        print("Error: choose either --top-k or --all, not both.")
        sys.exit(1)
    if args.sell and not args.account_id:
        print("Error: --sell requires --account-id.")
        sys.exit(1)
    if args.sell and not args.condition_id and not args.sell_all_eligible:
        print("Error: --sell requires --condition-id or --sell-all-eligible.")
        sys.exit(1)
    if args.sell_fraction <= 0 or args.sell_fraction > 1:
        print("Error: --sell-fraction must be in (0, 1].")
        sys.exit(1)

    decisions = load_paper_decisions(
        args.log_path,
        account_id=args.account_id,
        run_id=args.run_id,
    )
    top_k = None if args.all or args.top_k is None else args.top_k
    selected = select_decisions_by_confidence(decisions, top_k=top_k)
    if args.condition_id:
        selected = [decision for decision in selected if decision.condition_id == args.condition_id]
    if args.side:
        selected = [decision for decision in selected if decision.side == args.side]
    marks = mark_decisions_to_market(
        selected,
        report_dir=args.report_dir,
    )
    summary = paper_mark_summary(marks)

    selector = "all" if top_k is None else f"top {top_k} by confidence"
    print(f"\nPAPER DECISION EVAL ({selector})")
    print(f"log = {args.log_path}")
    if args.account_id:
        print(f"account = {args.account_id}")
    if args.run_id:
        print(f"run_id = {args.run_id}")
    if args.condition_id:
        print(f"condition_id = {args.condition_id}")
    if args.side:
        print(f"side = {args.side}")
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
            f"bid={bid} pnl={pnl} ret={ret} source={mark.mark_source or '-'}"
        )
        print(f"    {decision.question[:100]}")

    sell_results = []
    if args.sell:
        account = load_paper_account(args.account_id)
        if account is None:
            print(f"\nError: paper account not found: {args.account_id}")
            sys.exit(1)
        fee_rate_bps = args.sell_fee_bps if args.sell_fee_bps is not None else account.fee_rate_bps
        candidates = _sell_candidates(
            marks,
            account_id=args.account_id,
            condition_id=args.condition_id,
            side=args.side,
            sell_fraction=args.sell_fraction,
            fee_rate_bps=fee_rate_bps,
            min_return=args.sell_min_return,
            min_score=args.sell_min_score,
        )
        sell_results = _execute_sells(
            candidates,
            account_id=args.account_id,
            run_id=args.run_id or "",
            fee_rate_bps=fee_rate_bps,
            dry_run=args.sell_dry_run,
        )
        sold_count = sum(1 for result in sell_results if result.get("sold"))
        eligible_count = sum(1 for result in candidates if result.get("eligible"))
        earned = sum(float(result.get("net_proceeds") or 0) for result in sell_results)
        realized_pnl = sum(float(result.get("realized_pnl") or 0) for result in sell_results)
        print("\nSELL STRATEGY")
        print(f"rule = {_sell_strategy_text(args)}")
        print(f"fraction = {args.sell_fraction:.2f}  fee_bps = {fee_rate_bps:.2f}")
        print(f"eligible={eligible_count}  sold={sold_count}  dry_run={args.sell_dry_run}")
        print(f"cash earned={_fmt_money(earned)}  realized_pnl={_fmt_money(realized_pnl)}")
        for candidate in candidates:
            position = candidate["position"]
            status = "SELL" if candidate.get("eligible") else "HOLD"
            print(
                f"{status:<4} {position.side:<3} price={candidate.get('price', 0) or 0:.3f} "
                f"shares={candidate.get('shares_to_sell', 0) or 0:.4f} "
                f"earned={_fmt_money(candidate.get('net_proceeds'))} "
                f"pnl={_fmt_money(candidate.get('realized_pnl'))} "
                f"ret={_fmt_pct(candidate.get('return_pct'))} "
                f"score={candidate.get('score', 0) or 0:+.4f} "
                f"reason={candidate.get('reason')}"
            )
            print(f"    {position.condition_id}  {position.question[:100]}")

    if args.json_out:
        args.json_out.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "summary": summary,
            "marks": [_mark_to_dict(mark) for mark in marks],
            "sell": [
                {
                    "condition_id": result["position"].condition_id,
                    "side": result["position"].side,
                    "shares_to_sell": result.get("shares_to_sell"),
                    "price": result.get("price"),
                    "gross_proceeds": result.get("gross_proceeds"),
                    "sell_fee": result.get("sell_fee"),
                    "net_proceeds": result.get("net_proceeds"),
                    "realized_pnl": result.get("realized_pnl"),
                    "return_pct": result.get("return_pct"),
                    "score": result.get("score"),
                    "eligible": result.get("eligible"),
                    "sold": result.get("sold", False),
                    "order_id": getattr(result.get("order"), "order_id", ""),
                    "reason": result.get("reason"),
                }
                for result in sell_results
            ],
        }
        args.json_out.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        print(f"\nJSON report written to {args.json_out}")

    if args.report_dir:
        markdown_path, json_path = _write_reports(
            args.report_dir,
            top_k=top_k,
            selector=selector,
            log_path=args.log_path,
            account_id=args.account_id,
            run_id=args.run_id,
            summary=summary,
            marks=marks,
        )
        print(f"\nMarkdown report written to {markdown_path}")
        print(f"JSON report written to {json_path}")


if __name__ == "__main__":
    main()
