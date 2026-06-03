"""Parquet + DuckDB storage layer.

Pattern from: ref/data-backtesting/prediction-market-analysis/src/common/storage.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import duckdb
import pandas as pd
from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.types import MarketMeta, PriceSnapshot

CHUNK_SIZE = 5000


# ---------------------------------------------------------------------------
# Markets
# ---------------------------------------------------------------------------

class MarketStorage:
    def __init__(self, data_dir: Path | None = None):
        cfg = get_settings()
        self.dir = data_dir or cfg.markets_dir
        self.dir.mkdir(parents=True, exist_ok=True)
        self._seen: set[str] | None = None

    def _load_seen(self) -> set[str]:
        if self._seen is not None:
            return self._seen
        self._seen = set()
        parquets = list(self.dir.glob("markets_*.parquet"))
        if parquets:
            try:
                glob = str(self.dir / "markets_*.parquet")
                result = duckdb.sql(f"SELECT DISTINCT condition_id FROM '{glob}'").fetchall()
                self._seen = {r[0] for r in result if r[0]}
            except Exception as e:
                logger.warning(f"Could not load seen markets: {e}")
        return self._seen

    def _next_chunk_path(self) -> Path:
        existing = sorted(self.dir.glob("markets_*.parquet"))
        start = 0
        if existing:
            parts = existing[-1].stem.split("_")
            start = int(parts[2]) if len(parts) >= 3 else 0
        return self.dir / f"markets_{start}_{start + CHUNK_SIZE}.parquet"

    def upsert(self, markets: list[MarketMeta]) -> int:
        seen = self._load_seen()
        new = [m for m in markets if m.condition_id not in seen]
        if not new:
            return 0

        fetched_at = datetime.now(timezone.utc)
        records = []
        for m in new:
            records.append({
                "condition_id": m.condition_id,
                "event_id": m.event_id,
                "question": m.question,
                "description": m.description,
                "resolution_text": m.resolution_text,
                "category": m.category,
                "neg_risk": m.neg_risk,
                "neg_risk_market_id": m.neg_risk_market_id,
                "token_yes_id": m.token_yes_id,
                "token_no_id": m.token_no_id,
                "outcome_count": m.outcome_count,
                "outcomes_json": str(m.outcomes),
                "outcome_prices_json": str(m.outcome_prices),
                "close_time": m.close_time,
                "created_time": m.created_time,
                "volume_24h": m.volume_24h,
                "liquidity": m.liquidity,
                "active": m.active,
                "slug": m.slug,
                "_fetched_at": fetched_at,
            })
            seen.add(m.condition_id)

        df = pd.DataFrame(records)
        existing = sorted(self.dir.glob("markets_*.parquet"))

        if not existing:
            df.to_parquet(self.dir / f"markets_0_{len(records)}.parquet", index=False)
        else:
            last = existing[-1]
            last_df = pd.read_parquet(last)
            combined = pd.concat([last_df, df], ignore_index=True)
            if len(combined) <= CHUNK_SIZE:
                combined.to_parquet(last, index=False)
            else:
                combined.iloc[:CHUNK_SIZE].to_parquet(last, index=False)
                remaining = combined.iloc[CHUNK_SIZE:]
                start = int(last.stem.split("_")[1]) + CHUNK_SIZE
                remaining.to_parquet(
                    self.dir / f"markets_{start}_{start + CHUNK_SIZE}.parquet",
                    index=False,
                )

        logger.info(f"Stored {len(new)} new markets (total seen: {len(seen)})")
        return len(new)

    def count(self) -> int:
        return len(self._load_seen())


# ---------------------------------------------------------------------------
# Price Snapshots (DuckDB append)
# ---------------------------------------------------------------------------

def append_snapshots(conn: duckdb.DuckDBPyConnection, snapshots: list[PriceSnapshot]) -> None:
    if not snapshots:
        return
    records = [
        (
            f"{s.condition_id}_{int(s.snapshot_time.timestamp())}",
            s.condition_id, s.token_id, s.snapshot_time,
            s.midpoint, s.best_bid, s.best_ask, s.spread,
            s.volume_24h, s.source,
        )
        for s in snapshots
    ]
    conn.executemany(
        """INSERT INTO price_snapshots
           (id, condition_id, token_id, snapshot_time, midpoint,
            best_bid, best_ask, spread, volume_24h, source)
           VALUES (?,?,?,?,?,?,?,?,?,?)""",
        records,
    )
    conn.commit()
    logger.debug(f"Appended {len(snapshots)} price snapshots")


# ---------------------------------------------------------------------------
# DuckDB market upsert (for live updates)
# ---------------------------------------------------------------------------

def upsert_markets_db(conn: duckdb.DuckDBPyConnection, markets: list[MarketMeta]) -> None:
    from datetime import timezone
    fetched_at = datetime.now(timezone.utc)
    for m in markets:
        conn.execute(
            """INSERT OR REPLACE INTO markets
               (condition_id, event_id, question, description, resolution_text, category,
                neg_risk, neg_risk_market_id, token_yes_id, token_no_id,
                outcome_count, outcomes_json, outcome_prices_json, close_time, created_time,
                volume_24h, liquidity, active, slug, fetched_at)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (m.condition_id, m.event_id, m.question, m.description, m.resolution_text, m.category,
             m.neg_risk, m.neg_risk_market_id, m.token_yes_id, m.token_no_id,
             m.outcome_count, str(m.outcomes), str(m.outcome_prices), m.close_time, m.created_time,
             m.volume_24h, m.liquidity, m.active, m.slug, fetched_at),
        )
    conn.commit()
