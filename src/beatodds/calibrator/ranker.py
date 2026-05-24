"""Calibrator + Ranker — combines signals into net EdgeScore and ranked opportunities.

Combines:
1. Structural signal (from RelationMiner violations)
2. Evidence signal (from LLMForecaster)
3. Applies fee model to compute net_edge
4. Ranks by net_edge × confidence

Reference: BUILD_PLAN.md §3.3 Signal Fusion
"""

from __future__ import annotations

from datetime import datetime, timezone

from beatodds.common.config import get_settings
from beatodds.common.types import (
    CandidateMarket,
    ConsistencyViolation,
    EdgeScore,
    ForecastResult,
    RankedOpportunity,
)


class Ranker:
    def __init__(self):
        self.cfg = get_settings()

    def rank(
        self,
        candidates: list[CandidateMarket],
        forecasts: dict[str, ForecastResult],
        violations: list[ConsistencyViolation],
    ) -> list[RankedOpportunity]:
        """Combine structural + evidence signals into ranked opportunities."""
        now = datetime.now(timezone.utc)
        violation_map: dict[str, list[ConsistencyViolation]] = {}
        for v in violations:
            for mid in v.market_ids:
                violation_map.setdefault(mid, []).append(v)

        opportunities: list[RankedOpportunity] = []

        for c in candidates:
            cid = c.market.condition_id
            p_m = c.snapshot.midpoint
            spread = c.snapshot.spread
            viols = violation_map.get(cid, [])
            forecast = forecasts.get(cid)

            # Determine signal type and compute edge
            if forecast is not None:
                edge = forecast.p_f - p_m
                confidence = forecast.confidence
                signal_type = "evidence"
                explanation = forecast.reasoning
                p_f = forecast.p_f
            elif viols:
                best_viol = max(viols, key=lambda v: v.net_edge)
                edge = best_viol.edge_estimate
                confidence = 0.8   # structural signals are more certain
                signal_type = "structural"
                explanation = best_viol.explanation
                p_f = p_m + edge
            else:
                # No signal beyond market price
                edge = 0.0
                confidence = 0.0
                signal_type = "ensemble"
                explanation = "market_only"
                p_f = p_m

            # Net edge after fees
            net_edge = self._net_edge(edge, spread)

            score = EdgeScore(
                condition_id=cid,
                scored_at=now,
                p_m=p_m,
                p_f=p_f,
                edge=edge,
                spread=spread,
                fee_bps=self.cfg.bundle_taker_fee_bps,
                net_edge=net_edge,
                signal_type=signal_type,
                explanation=explanation,
                confidence=confidence,
                priority=0,
            )

            opportunities.append(RankedOpportunity(
                edge_score=score,
                market=c.market,
                snapshot=c.snapshot,
                forecast=forecast,
                structural_violations=viols,
            ))

        # Sort by |net_edge| × confidence (consider both long and short)
        opportunities.sort(
            key=lambda o: abs(o.edge_score.net_edge) * o.edge_score.confidence,
            reverse=True,
        )

        # Assign priority ranks
        for i, op in enumerate(opportunities):
            op.edge_score.priority = i + 1

        return opportunities

    def _net_edge(self, edge: float, spread: float) -> float:
        """Net edge after taker fee and half-spread cost."""
        fee_rate = self.cfg.bundle_taker_fee_bps / 10000
        # Cost model: pay half-spread to cross + fee on trade size
        net = abs(edge) - spread / 2 - fee_rate
        return net if edge >= 0 else -net
