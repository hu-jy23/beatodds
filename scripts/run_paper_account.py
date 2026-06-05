#!/usr/bin/env python3
"""Inspect and maintain paper trading accounts."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from beatodds.evaluation.paper_store import (
    DEFAULT_ACCOUNT_ID,
    account_summary,
    create_paper_account,
    deposit_cash,
    ensure_default_paper_account,
    load_account_transactions,
    load_paper_account,
    load_paper_accounts,
    release_reserved_cash,
    reserve_cash,
    update_risk_params,
    withdraw_cash,
)


def _fmt_money(value: float) -> str:
    return f"${value:,.2f}"


def _print_account(account) -> None:
    print(f"account_id = {account.account_id}")
    print(f"name = {account.name}")
    print(f"status = {account.status}  risk_profile = {account.risk_profile}")
    print(
        f"cash = {_fmt_money(account.cash_balance)}  "
        f"reserved = {_fmt_money(account.reserved_cash)}  "
        f"initial = {_fmt_money(account.initial_cash)}"
    )
    print(
        "risk = "
        f"order {_fmt_money(account.max_order_notional)} | "
        f"market {_fmt_money(account.max_market_exposure)} | "
        f"event {_fmt_money(account.max_event_exposure)} | "
        f"category {_fmt_money(account.max_category_exposure)} | "
        f"total {_fmt_money(account.max_total_exposure)}"
    )
    print(
        f"cash_buffer = {_fmt_money(account.min_cash_buffer)}  "
        f"fee_bps = {account.fee_rate_bps:.1f}  "
        f"slippage_bps = {account.slippage_bps:.1f}"
    )


def _print_transactions(account_id: str, limit: int) -> None:
    transactions = load_account_transactions(account_id, limit=limit)
    if not transactions:
        print("No account transactions.")
        return
    print(f"\nTRANSACTIONS ({len(transactions)} shown)")
    for tx in transactions:
        print(
            f"{tx.created_at.isoformat()}  {tx.transaction_type:<8}  "
            f"cash_delta={tx.cash_delta:+.2f}  reserved_delta={tx.reserved_delta:+.2f}  "
            f"cash={tx.cash_after:.2f}  reserved={tx.reserved_after:.2f}  "
            f"{tx.memo}"
        )


def main() -> None:
    parser = argparse.ArgumentParser(description="Manage BeatOdds paper trading accounts")
    parser.add_argument("--account-id", default=DEFAULT_ACCOUNT_ID)
    parser.add_argument("--create-default", action="store_true")
    parser.add_argument("--create", metavar="NAME", help="Create a new paper account")
    parser.add_argument("--initial-cash", type=float, default=10_000.0)
    parser.add_argument("--show", action="store_true")
    parser.add_argument("--list", action="store_true")
    parser.add_argument("--transactions", action="store_true")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--deposit", type=float)
    parser.add_argument("--withdraw", type=float)
    parser.add_argument("--reserve", type=float)
    parser.add_argument("--release", type=float)
    parser.add_argument("--memo", default="")
    parser.add_argument("--risk-profile")
    parser.add_argument("--max-order-notional", type=float)
    parser.add_argument("--max-market-exposure", type=float)
    parser.add_argument("--max-event-exposure", type=float)
    parser.add_argument("--max-category-exposure", type=float)
    parser.add_argument("--max-total-exposure", type=float)
    parser.add_argument("--min-cash-buffer", type=float)
    parser.add_argument("--fee-rate-bps", type=float)
    parser.add_argument("--slippage-bps", type=float)
    parser.add_argument("--status", choices=["active", "paused", "closed"])
    args = parser.parse_args()

    if args.create_default:
        account = ensure_default_paper_account(initial_cash=args.initial_cash)
        print("Default paper account ready.")
        _print_account(account)
        return

    if args.create:
        account = create_paper_account(
            account_id=args.account_id,
            name=args.create,
            initial_cash=args.initial_cash,
        )
        print("Created paper account.")
        _print_account(account)
        return

    if args.deposit is not None:
        deposit_cash(args.account_id, args.deposit, memo=args.memo)
    if args.withdraw is not None:
        withdraw_cash(args.account_id, args.withdraw, memo=args.memo)
    if args.reserve is not None:
        reserve_cash(args.account_id, args.reserve, memo=args.memo)
    if args.release is not None:
        release_reserved_cash(args.account_id, args.release, memo=args.memo)

    risk_args = {
        "risk_profile": args.risk_profile,
        "max_order_notional": args.max_order_notional,
        "max_market_exposure": args.max_market_exposure,
        "max_event_exposure": args.max_event_exposure,
        "max_category_exposure": args.max_category_exposure,
        "max_total_exposure": args.max_total_exposure,
        "min_cash_buffer": args.min_cash_buffer,
        "fee_rate_bps": args.fee_rate_bps,
        "slippage_bps": args.slippage_bps,
        "status": args.status,
    }
    if any(value is not None for value in risk_args.values()):
        update_risk_params(args.account_id, **risk_args)

    if args.list:
        summary = account_summary()
        print(
            f"paper_accounts={summary['paper_accounts']}  "
            f"transactions={summary['paper_account_transactions']}  "
            f"cash={_fmt_money(summary['total_cash_balance'])}  "
            f"reserved={_fmt_money(summary['total_reserved_cash'])}"
        )
        for account in load_paper_accounts(limit=args.limit):
            print("")
            _print_account(account)
        return

    account = load_paper_account(args.account_id)
    if account is None:
        print(
            "No paper account found. Use "
            "`uv run scripts/run_paper_account.py --create-default` first."
        )
        return
    if args.show or not args.transactions:
        _print_account(account)
    if args.transactions:
        _print_transactions(args.account_id, args.limit)


if __name__ == "__main__":
    main()
