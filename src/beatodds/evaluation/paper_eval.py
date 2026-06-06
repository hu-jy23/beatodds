"""Evaluate paper-trading decision logs against current order-book quotes."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from beatodds.common.config import get_settings
from beatodds.data.clob_client import ClobReadClient


@dataclass(frozen=True)
class PaperDecision:
    raw: dict[str, Any]
    order_id: str
    run_id: str
    account_id: str
    condition_id: str
    question: str
    side: str
    token_id: str
    confidence: float
    filled_notional: float
    filled_shares: float
    fee: float
    avg_price: float
    created_at: datetime | None


@dataclass(frozen=True)
class PaperDecisionMark:
    decision: PaperDecision
    token_id: str
    current_bid: float | None
    current_ask: float | None
    current_value: float | None
    cost_basis: float
    pnl: float | None
    return_pct: float | None
    status: str
    marked_at: datetime


def load_paper_decisions(
    log_path: Path,
    *,
    account_id: str | None = None,
    run_id: str | None = None,
) -> list[PaperDecision]:
    """Load buy decisions from the JSONL paper-trading log."""
    decisions: list[PaperDecision] = []
    if not log_path.exists():
        raise FileNotFoundError(f"paper decision log not found: {log_path}")
    with log_path.open("r", encoding="utf-8") as handle:
        for line_no, line in enumerate(handle, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"invalid JSONL at {log_path}:{line_no}") from exc
            if row.get("type") != "decision" or row.get("action") != "buy":
                continue
            if account_id and row.get("account_id") != account_id:
                continue
            if run_id and row.get("run_id") != run_id:
                continue
            decisions.append(_decision_from_row(row))
    return decisions


def select_decisions_by_confidence(
    decisions: list[PaperDecision],
    *,
    top_k: int | None = None,
) -> list[PaperDecision]:
    """Return all decisions or the top-k buy decisions by forecast confidence."""
    ordered = sorted(
        decisions,
        key=lambda item: (
            item.confidence,
            item.created_at or datetime.min.replace(tzinfo=timezone.utc),
        ),
        reverse=True,
    )
    if top_k is None:
        return ordered
    if top_k <= 0:
        raise ValueError("top_k must be positive")
    return ordered[:top_k]


def mark_decisions_to_market(
    decisions: list[PaperDecision],
    *,
    clob: ClobReadClient | None = None,
    data_dir: Path | None = None,
) -> list[PaperDecisionMark]:
    """Fetch current quotes and compute liquidation-style unrealized PnL."""
    clob = clob or ClobReadClient()
    marked_at = datetime.now(timezone.utc)
    marks: list[PaperDecisionMark] = []
    for decision in decisions:
        token_id = decision.token_id or resolve_decision_token_id(
            decision,
            data_dir=data_dir,
        )
        cost_basis = decision.filled_notional + decision.fee
        if not token_id:
            marks.append(PaperDecisionMark(
                decision=decision,
                token_id="",
                current_bid=None,
                current_ask=None,
                current_value=None,
                cost_basis=cost_basis,
                pnl=None,
                return_pct=None,
                status="missing_token_id",
                marked_at=marked_at,
            ))
            continue

        book = clob.get_order_book(token_id)
        best_bid, best_ask = _best_bid_ask(book)
        if best_bid is None:
            marks.append(PaperDecisionMark(
                decision=decision,
                token_id=token_id,
                current_bid=None,
                current_ask=best_ask,
                current_value=None,
                cost_basis=cost_basis,
                pnl=None,
                return_pct=None,
                status="missing_bid",
                marked_at=marked_at,
            ))
            continue
        current_value = decision.filled_shares * best_bid
        pnl = current_value - cost_basis
        marks.append(PaperDecisionMark(
            decision=decision,
            token_id=token_id,
            current_bid=best_bid,
            current_ask=best_ask,
            current_value=current_value,
            cost_basis=cost_basis,
            pnl=pnl,
            return_pct=pnl / cost_basis if cost_basis else None,
            status="marked",
            marked_at=marked_at,
        ))
    return marks


def paper_mark_summary(marks: list[PaperDecisionMark]) -> dict[str, float | int]:
    marked = [mark for mark in marks if mark.status == "marked"]
    invested = sum(mark.cost_basis for mark in marked)
    value = sum(float(mark.current_value or 0) for mark in marked)
    pnl = value - invested
    winners = sum(1 for mark in marked if (mark.pnl or 0) > 0)
    return {
        "selected": len(marks),
        "marked": len(marked),
        "unmarked": len(marks) - len(marked),
        "invested": invested,
        "current_value": value,
        "pnl": pnl,
        "return_pct": pnl / invested if invested else 0.0,
        "winners": winners,
        "losers": len(marked) - winners,
    }


def resolve_decision_token_id(
    decision: PaperDecision,
    *,
    data_dir: Path | None = None,
) -> str:
    """Resolve missing token IDs from the paper ledger or local market DBs."""
    cfg = get_settings()
    root = Path(data_dir or cfg.data_dir)
    token_id = _resolve_from_eval_db(decision, root / "eval.duckdb")
    if token_id:
        return token_id
    return _resolve_from_market_db(decision, root / "beatodds.duckdb")


def _decision_from_row(row: dict[str, Any]) -> PaperDecision:
    return PaperDecision(
        raw=row,
        order_id=str(row.get("order_id") or ""),
        run_id=str(row.get("run_id") or ""),
        account_id=str(row.get("account_id") or ""),
        condition_id=str(row.get("condition_id") or ""),
        question=str(row.get("question") or ""),
        side=str(row.get("side") or "").upper(),
        token_id=str(row.get("token_id") or ""),
        confidence=_as_float(row.get("confidence")),
        filled_notional=_as_float(row.get("filled_notional")),
        filled_shares=_as_float(row.get("filled_shares")),
        fee=_as_float(row.get("fee")),
        avg_price=_as_float(row.get("avg_price")),
        created_at=_parse_time(row.get("created_at")),
    )


def _resolve_from_eval_db(decision: PaperDecision, path: Path) -> str:
    if not path.exists():
        return ""
    import duckdb

    conn = duckdb.connect(str(path), read_only=True)
    try:
        has_orders = _table_exists(conn, "paper_orders")
        if has_orders and decision.order_id:
            row = conn.execute(
                "SELECT token_id FROM paper_orders WHERE order_id = ?",
                [decision.order_id],
            ).fetchone()
            if row and row[0]:
                return str(row[0])
        if has_orders and decision.condition_id and decision.side:
            row = conn.execute(
                """
                SELECT token_id
                FROM paper_orders
                WHERE condition_id = ? AND side = ?
                ORDER BY created_at DESC
                LIMIT 1
                """,
                [decision.condition_id, decision.side],
            ).fetchone()
            if row and row[0]:
                return str(row[0])
        if decision.condition_id and decision.side:
            if _table_exists(conn, "tracked_markets"):
                token_col = "token_yes_id" if decision.side == "YES" else "token_no_id"
                row = conn.execute(
                    f"SELECT {token_col} FROM tracked_markets WHERE condition_id = ?",
                    [decision.condition_id],
                ).fetchone()
                if row and row[0]:
                    return str(row[0])
    finally:
        conn.close()
    return ""


def _resolve_from_market_db(decision: PaperDecision, path: Path) -> str:
    if not path.exists() or not decision.condition_id or not decision.side:
        return ""
    import duckdb

    token_col = "token_yes_id" if decision.side == "YES" else "token_no_id"
    conn = duckdb.connect(str(path), read_only=True)
    try:
        if not _table_exists(conn, "markets"):
            return ""
        row = conn.execute(
            f"SELECT {token_col} FROM markets WHERE condition_id = ?",
            [decision.condition_id],
        ).fetchone()
    finally:
        conn.close()
    return str(row[0]) if row and row[0] else ""


def _table_exists(conn: Any, table: str) -> bool:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM information_schema.tables
        WHERE table_name = ?
        """,
        [table],
    ).fetchone()
    return bool(row and row[0])


def _best_bid_ask(book: dict[str, Any] | None) -> tuple[float | None, float | None]:
    if not book:
        return None, None
    bids = book.get("bids") or []
    asks = book.get("asks") or []
    # CLOB v2 returns bids ascending and asks descending; the best level is last.
    best_bid = _level_price(bids[-1]) if bids else None
    best_ask = _level_price(asks[-1]) if asks else None
    return best_bid, best_ask


def _level_price(level: Any) -> float | None:
    value = level.get("price") if isinstance(level, dict) else getattr(level, "price", None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _as_float(value: Any) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)
