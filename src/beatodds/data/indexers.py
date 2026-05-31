"""Simple live Gamma market indexer for README pipeline commands."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.db import ensure_schema
from beatodds.common.types import MarketMeta
from beatodds.data.gamma_client import GammaClient


class MarketIndexer:
    def __init__(self):
        self.cfg = get_settings()

    def run_incremental(self, limit: int = 500) -> int:
        """Fetch current liquid markets and upsert them into DuckDB and Parquet."""
        with GammaClient() as gamma:
            raw_markets = gamma.get_liquid_markets(
                limit=limit,
                min_volume_24h=self.cfg.scanner_min_volume_24h,
            )
            markets = [gamma.parse_market(raw) for raw in raw_markets]

        self._write_duckdb(markets)
        self._write_parquet(raw_markets)
        return len(markets)

    def run_backfill(self) -> int:
        return self.run_incremental(limit=500)

    def _write_duckdb(self, markets: list[MarketMeta]) -> None:
        fetched_at = datetime.now(timezone.utc)
        conn = ensure_schema()
        try:
            for market in markets:
                conn.execute(
                    "DELETE FROM markets WHERE condition_id = ?",
                    [market.condition_id],
                )
                conn.execute(
                    """
                    INSERT INTO markets (
                        condition_id, question, description, resolution_text, category,
                        neg_risk, neg_risk_market_id, token_yes_id, token_no_id,
                        outcome_count, outcomes_json, close_time, created_time,
                        volume_24h, liquidity, active, slug, fetched_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        market.condition_id,
                        market.question,
                        market.description,
                        market.resolution_text,
                        market.category,
                        market.neg_risk,
                        market.neg_risk_market_id,
                        market.token_yes_id,
                        market.token_no_id,
                        market.outcome_count,
                        json.dumps(market.outcomes),
                        market.close_time,
                        market.created_time,
                        market.volume_24h,
                        market.liquidity,
                        market.active,
                        market.slug,
                        fetched_at,
                    ],
                )
            conn.commit()
        finally:
            conn.close()

    def _write_parquet(self, raw_markets: list[dict]) -> None:
        self.cfg.markets_dir.mkdir(parents=True, exist_ok=True)
        if not raw_markets:
            return
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.cfg.markets_dir / f"gamma_markets_{ts}.parquet"
        pd.DataFrame(raw_markets).to_parquet(path, index=False)
        logger.info(f"Wrote {len(raw_markets)} raw market rows to {path}")
