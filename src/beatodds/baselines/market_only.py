"""Baseline 1: market-only. p_f = p_m, edge = 0.

This is the floor. Any useful signal must beat this Brier score.
"""

from __future__ import annotations

from datetime import datetime, timezone

from beatodds.common.types import CandidateMarket, EdgeScore


def market_only_scores(candidates: list[CandidateMarket]) -> list[EdgeScore]:
    now = datetime.now(timezone.utc)
    scores = []
    for c in candidates:
        p_m = c.snapshot.midpoint
        scores.append(EdgeScore(
            condition_id=c.market.condition_id,
            scored_at=now,
            p_m=p_m,
            p_f=p_m,
            edge=0.0,
            spread=c.snapshot.spread,
            net_edge=0.0,
            signal_type="ensemble",
            explanation="market_only baseline: p_f = p_m",
            confidence=1.0,
            priority=0,
        ))
    return scores
