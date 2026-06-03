"""Simple live Gamma market indexer for README pipeline commands."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pandas as pd
from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.db import ensure_schema
from beatodds.common.types import EventMeta, MarketMeta
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
            events = self._parse_events(gamma, raw_markets, markets)

        self._write_duckdb(markets, events)
        self._write_parquet(raw_markets)
        return len(markets)

    def run_backfill(self) -> int:
        return self.run_incremental(limit=500)

    def _parse_events(
        self,
        gamma: GammaClient,
        raw_markets: list[dict],
        markets: list[MarketMeta],
    ) -> list[EventMeta]:
        events_by_id: dict[str, EventMeta] = {}
        for raw, market in zip(raw_markets, markets, strict=False):
            raw_events = raw.get("events") or []
            if isinstance(raw_events, list) and raw_events:
                raw_event = raw_events[0]
                if isinstance(raw_event, dict):
                    event = gamma.parse_event(raw_event)
                    if event.event_id:
                        events_by_id[event.event_id] = event
                        continue
            if market.event_id:
                events_by_id[market.event_id] = EventMeta(
                    event_id=market.event_id,
                    title=market.question,
                    slug=market.slug,
                    category=market.category,
                    end_time=market.close_time,
                    volume_24h=market.volume_24h,
                    liquidity=market.liquidity,
                    active=market.active,
                    market_count=1,
                )
        for event_id in list(events_by_id):
            try:
                raw_event = gamma.get_event(event_id)
                if raw_event:
                    detailed = gamma.parse_event(raw_event)
                    if detailed.event_id:
                        events_by_id[detailed.event_id] = detailed
            except Exception as exc:
                logger.debug(f"Could not enrich event {event_id}: {exc}")
        return list(events_by_id.values())

    def _write_duckdb(self, markets: list[MarketMeta], events: list[EventMeta]) -> None:
        fetched_at = datetime.now(timezone.utc)
        conn = ensure_schema()
        try:
            for event in events:
                conn.execute(
                    """
                    INSERT OR REPLACE INTO events (
                        event_id, title, slug, ticker, description, image, icon,
                        category, tags_json, start_time, end_time, volume_24h,
                        liquidity, active, closed, archived, neg_risk, market_count,
                        fetched_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        event.event_id,
                        event.title,
                        event.slug,
                        event.ticker,
                        event.description,
                        event.image,
                        event.icon,
                        event.category,
                        json.dumps(event.tags),
                        event.start_time,
                        event.end_time,
                        event.volume_24h,
                        event.liquidity,
                        event.active,
                        event.closed,
                        event.archived,
                        event.neg_risk,
                        event.market_count,
                        fetched_at,
                    ],
                )
            for market in markets:
                conn.execute(
                    "DELETE FROM markets WHERE condition_id = ?",
                    [market.condition_id],
                )
                conn.execute(
                    """
                    INSERT INTO markets (
                        condition_id, event_id, question, description, resolution_text, category,
                        neg_risk, neg_risk_market_id, token_yes_id, token_no_id,
                        outcome_count, outcomes_json, outcome_prices_json,
                        close_time, created_time,
                        volume_24h, liquidity, active, slug, fetched_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        market.condition_id,
                        market.event_id,
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
                        json.dumps(market.outcome_prices),
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
