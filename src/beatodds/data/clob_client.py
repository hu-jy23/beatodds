"""Read-only wrapper around Polymarket CLOB v2 order-book APIs."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from loguru import logger
from py_clob_client_v2 import ClobClient

from beatodds.common.config import get_settings
from beatodds.common.types import PriceSnapshot


def _price(level: Any) -> float | None:
    value = level.get("price") if isinstance(level, dict) else getattr(level, "price", None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


class ClobReadClient:
    def __init__(self):
        cfg = get_settings()
        self._client = ClobClient(cfg.polymarket_clob_host, chain_id=cfg.polymarket_chain_id)

    def get_order_book(self, token_id: str) -> dict[str, Any] | None:
        if not token_id:
            return None
        try:
            book = self._client.get_order_book(token_id)
        except Exception as exc:
            logger.debug(f"CLOB order book failed for {token_id[:12]}: {exc}")
            return None
        return book if isinstance(book, dict) else book.__dict__

    def get_snapshot(self, condition_id: str, token_id: str) -> PriceSnapshot | None:
        book = self.get_order_book(token_id)
        if not book:
            return None

        bids = book.get("bids") or []
        asks = book.get("asks") or []
        if not bids or not asks:
            return None

        # CLOB v2 returns bids ascending and asks descending, so the best level is last.
        best_bid = _price(bids[-1])
        best_ask = _price(asks[-1])
        if best_bid is None or best_ask is None:
            return None

        last_trade_price = None
        try:
            last_trade_price = float(book.get("last_trade_price"))
        except (TypeError, ValueError):
            pass

        return PriceSnapshot(
            condition_id=condition_id,
            token_id=token_id,
            snapshot_time=datetime.now(timezone.utc),
            midpoint=(best_bid + best_ask) / 2,
            best_bid=best_bid,
            best_ask=best_ask,
            spread=max(0.0, best_ask - best_bid),
            last_trade_price=last_trade_price,
        )
