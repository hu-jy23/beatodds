"""Market Scanner — filters active markets and flags candidates.

Outputs CandidateMarket list for downstream modules.
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.types import CandidateMarket, MarketMeta, PriceSnapshot
from beatodds.data.clob_client import ClobReadClient
from beatodds.data.gamma_client import GammaClient


class Scanner:
    def __init__(self, market_limit: int | None = None):
        self.cfg = get_settings()
        self.market_limit = market_limit or self.cfg.scanner_market_limit

    def scan(self) -> list[CandidateMarket]:
        """Pull active markets, take price snapshots, return filtered candidates."""
        logger.info("Scanner: starting scan")
        candidates: list[CandidateMarket] = []

        with GammaClient() as gamma:
            raw_dicts = gamma.get_liquid_markets(
                limit=self.market_limit,
                min_volume_24h=self.cfg.scanner_min_volume_24h,
                page_limit=self.cfg.scanner_gamma_page_limit,
            )
            raw_markets: list[MarketMeta] = []
            for raw in raw_dicts:
                try:
                    raw_markets.append(gamma.parse_market(raw))
                except Exception:
                    pass

        logger.info(f"Scanner: fetched {len(raw_markets)} markets from Gamma")

        clob = ClobReadClient()
        now = datetime.now(timezone.utc)

        for market in raw_markets:
            flags = self._compute_flags(market)

            # Skip already-closing markets
            if (
                market.close_time
                and (market.close_time - now).total_seconds() / 86400
                < self.cfg.scanner_min_days_to_close
            ):
                continue

            # Take snapshot (use YES token for binary markets)
            token_id = market.token_yes_id or market.token_no_id
            if not token_id:
                continue

            snapshot = clob.get_snapshot(market.condition_id, token_id)
            if snapshot is None:
                continue
            if snapshot.spread > self.cfg.scanner_max_spread:
                flags.append("wide_spread")

            priority = self._priority(market, snapshot, flags)

            candidates.append(CandidateMarket(
                market=market,
                snapshot=snapshot,
                scan_flags=flags,
                priority_score=priority,
            ))

        candidates.sort(key=lambda c: c.priority_score, reverse=True)
        logger.info(f"Scanner: {len(candidates)} candidates after filtering")
        return candidates

    def _compute_flags(self, market: MarketMeta) -> list[str]:
        flags: list[str] = []
        if market.neg_risk:
            flags.append("neg_risk")
        if market.outcome_count > 2:
            flags.append("multi_outcome")
        if market.volume_24h < 500:
            flags.append("low_volume")
        return flags

    def _priority(
        self, market: MarketMeta, snapshot: PriceSnapshot, flags: list[str]
    ) -> float:
        score = 0.0
        # neg_risk markets → structural signal opportunity
        if "neg_risk" in flags:
            score += 2.0
        # Multi-outcome → more structural constraints to check
        if "multi_outcome" in flags:
            score += 1.5
        # Tight spread → executable (wide spread is strong negative signal)
        if snapshot.spread < self.cfg.scanner_max_spread:
            score += (self.cfg.scanner_max_spread - snapshot.spread) * 10
        else:
            score -= (snapshot.spread - self.cfg.scanner_max_spread) * 2
        # Recent volume
        score += min(market.volume_24h / 10000, 1.0)
        return score
