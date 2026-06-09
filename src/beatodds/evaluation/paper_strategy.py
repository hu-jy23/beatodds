"""Shared paper-trading strategy helpers and JSONL audit logging."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from beatodds.data.clob_client import ClobReadClient
from beatodds.evaluation.paper_store import load_paper_account, load_paper_positions


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=json_default, ensure_ascii=True) + "\n")


def json_default(value: Any) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def account_money_snapshot(account_id: str) -> dict[str, Any]:
    account = load_paper_account(account_id)
    positions = load_paper_positions(account_id) if account else []
    open_cost_basis = sum(position.cost_basis + position.fees_paid for position in positions)
    open_marked_value, marked_count = _open_marked_value(positions)
    marked_total_money = (
        float(account.cash_balance or 0)
        + float(account.reserved_cash or 0)
        + open_marked_value
        if account else None
    )
    return {
        "account_id": account_id,
        "cash_balance": account.cash_balance if account else None,
        "reserved_cash": account.reserved_cash if account else None,
        "open_position_count": len(positions),
        "open_cost_basis": open_cost_basis,
        "open_marked_value": open_marked_value,
        "open_marked_pnl": open_marked_value - open_cost_basis,
        "open_marked_count": marked_count,
        "total_marked_money": marked_total_money,
    }


def _open_marked_value(positions: list[Any]) -> tuple[float, int]:
    if not positions:
        return 0.0, 0
    try:
        clob = ClobReadClient()
    except Exception:
        return (
            sum(position.cost_basis + position.fees_paid for position in positions),
            0,
        )
    value = 0.0
    marked_count = 0
    bid_cache: dict[str, float | None] = {}
    for position in positions:
        token_id = str(getattr(position, "token_id", "") or "")
        bid = bid_cache.get(token_id)
        if token_id and token_id not in bid_cache:
            try:
                bid, _ = best_bid_ask(clob.get_order_book(token_id))
            except Exception:
                bid = None
            bid_cache[token_id] = bid
        if bid is None:
            value += position.cost_basis + position.fees_paid
            continue
        value += float(position.shares or 0) * bid
        marked_count += 1
    return value, marked_count


def level_price(level: Any) -> float | None:
    value = level.get("price") if isinstance(level, dict) else getattr(level, "price", None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def level_size(level: Any) -> float | None:
    for key in ("size", "shares", "amount"):
        value = level.get(key) if isinstance(level, dict) else getattr(level, key, None)
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return None


def ask_levels(book: dict[str, Any] | None) -> list[tuple[float, float]]:
    asks = (book or {}).get("asks") or []
    levels: list[tuple[float, float]] = []
    for level in asks:
        price = level_price(level)
        size = level_size(level)
        if price is not None and size is not None and price > 0 and size > 0:
            levels.append((price, size))
    return sorted(levels, key=lambda item: item[0])


def best_bid_ask(book: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not book:
        return None, None
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    best_bid = level_price(bids[-1]) if bids else None
    best_ask = level_price(asks[-1]) if asks else None
    return best_bid, best_ask


def simulate_buy(
    levels: list[tuple[float, float]],
    target_notional: float,
) -> tuple[list[tuple[float, float]], float, float]:
    remaining = target_notional
    fills: list[tuple[float, float]] = []
    for price, available_shares in levels:
        if remaining <= 0:
            break
        max_notional = price * available_shares
        take_notional = min(remaining, max_notional)
        shares = take_notional / price
        fills.append((price, shares))
        remaining -= take_notional
    filled_notional = sum(price * shares for price, shares in fills)
    filled_shares = sum(shares for _, shares in fills)
    return fills, filled_notional, filled_shares


def position_exposure(account_id: str) -> dict[str, float]:
    exposure = {"total": 0.0}
    for position in load_paper_positions(account_id):
        open_cost = position.cost_basis + position.fees_paid
        exposure["total"] += open_cost
        exposure[f"market:{position.condition_id}"] = (
            exposure.get(f"market:{position.condition_id}", 0.0) + open_cost
        )
        if position.event_id:
            exposure[f"event:{position.event_id}"] = (
                exposure.get(f"event:{position.event_id}", 0.0) + open_cost
            )
        if position.category:
            exposure[f"category:{position.category}"] = (
                exposure.get(f"category:{position.category}", 0.0) + open_cost
            )
    return exposure


def sell_estimate(
    *,
    shares: float,
    price: float,
    position_shares: float,
    position_cost_basis: float,
    position_fees_paid: float,
    fee_rate_bps: float,
) -> dict[str, float]:
    gross_proceeds = shares * price
    sell_fee = gross_proceeds * max(0.0, fee_rate_bps) / 10_000
    cost_fraction = shares / position_shares if position_shares else 0.0
    cost_basis = position_cost_basis * cost_fraction
    prior_fees = position_fees_paid * cost_fraction
    net_proceeds = gross_proceeds - sell_fee
    realized_pnl = net_proceeds - cost_basis - prior_fees
    invested = cost_basis + prior_fees
    return {
        "gross_proceeds": gross_proceeds,
        "sell_fee": sell_fee,
        "net_proceeds": net_proceeds,
        "realized_pnl": realized_pnl,
        "return_pct": realized_pnl / invested if invested else 0.0,
        "cost_basis_sold": cost_basis,
        "prior_fees_sold": prior_fees,
    }
