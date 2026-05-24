"""Relation Miner — structural mispricing detection.

Detects:
1. Bundle long/short arbitrage (YES + NO price sum deviation)
2. neg_risk group inconsistencies
3. Multi-outcome price sum violations

Reference: ref/strategy-execution/polymarket-arbitrage/core/arb_engine.py (bundle detection)
           Saguillo et al. 2025 (combinatorial arbitrage)
"""

from __future__ import annotations

from itertools import combinations

from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.types import (
    CandidateMarket,
    ConsistencyViolation,
    RelationEdge,
    RelationGraph,
)
from beatodds.data.clob_client import ClobReadClient
from beatodds.data.gamma_client import GammaClient


class RelationMiner:
    def __init__(self, complete_neg_risk_groups: bool = True):
        self.cfg = get_settings()
        self._fee = self.cfg.bundle_taker_fee_bps / 10000  # 0.015
        self._gas = self.cfg.bundle_gas_per_order           # 0.02
        self._min_edge = self.cfg.bundle_min_edge           # 0.01
        self._complete_neg_risk_groups_enabled = complete_neg_risk_groups

    def mine(self, candidates: list[CandidateMarket]) -> RelationGraph:
        nodes = [c.market.condition_id for c in candidates]
        edges: list[RelationEdge] = []
        violations: list[ConsistencyViolation] = []

        # Build neg_risk groups
        neg_risk_groups: dict[str, list[CandidateMarket]] = {}
        for c in candidates:
            if c.market.neg_risk and c.market.neg_risk_market_id:
                gid = c.market.neg_risk_market_id
                neg_risk_groups.setdefault(gid, []).append(c)

        # Add neg_risk edges
        for gid, members in neg_risk_groups.items():
            for a, b in combinations(members, 2):
                edges.append(RelationEdge(
                    source_id=a.market.condition_id,
                    target_id=b.market.condition_id,
                    relation="neg_risk",
                    weight=1.0,
                ))

        # Bundle arbitrage: YES + NO ≠ 1.0 for binary markets
        clob = ClobReadClient()
        for c in candidates:
            market = c.market
            if not market.token_yes_id or not market.token_no_id:
                continue
            violations.extend(self._check_binary_bundle(clob, c))

        if self._complete_neg_risk_groups_enabled:
            complete_groups = self._complete_neg_risk_groups(neg_risk_groups, clob)
            for gid, members in complete_groups.items():
                violations.extend(self._check_neg_risk_group(members))

        violations.sort(key=lambda v: v.net_edge, reverse=True)
        logger.info(
            f"RelationMiner: {len(edges)} edges, {len(violations)} violations found"
        )
        return RelationGraph(nodes=nodes, edges=edges, violations=violations)

    def _complete_neg_risk_groups(
        self,
        partial_groups: dict[str, list[CandidateMarket]],
        clob: ClobReadClient,
    ) -> dict[str, list[CandidateMarket]]:
        """For each neg_risk group, fetch all markets in the event to get complete pricing."""
        complete: dict[str, list[CandidateMarket]] = {}

        with GammaClient() as gamma:
            for gid, members in partial_groups.items():
                # Use event_id from first member to get full group
                event_id = members[0].market.event_id if members else ""
                if not event_id:
                    complete[gid] = members
                    continue

                all_markets = gamma.get_event_markets(event_id)
                if len(all_markets) <= len(members):
                    # Already complete or fetch failed
                    complete[gid] = members
                    continue

                # Build snapshot for new markets not in scanner results
                known_ids = {c.market.condition_id for c in members}
                full_members = list(members)
                for m in all_markets:
                    if m.condition_id in known_ids or not m.token_yes_id:
                        continue
                    snap = clob.get_snapshot(m.condition_id, m.token_yes_id)
                    if snap is None:
                        continue
                    full_members.append(CandidateMarket(
                        market=m,
                        snapshot=snap,
                        scan_flags=["neg_risk", "from_complete_fetch"],
                        priority_score=0.0,
                    ))

                complete[gid] = full_members
                logger.debug(
                    f"neg_risk group {gid[:16]}: {len(members)} → {len(full_members)} markets"
                )

        return complete

    def _check_binary_bundle(
        self, clob: ClobReadClient, candidate: CandidateMarket
    ) -> list[ConsistencyViolation]:
        """Detect YES+NO bundle arbitrage for a binary market."""
        market = candidate.market
        violations: list[ConsistencyViolation] = []

        snap_yes = clob.get_snapshot(market.condition_id, market.token_yes_id)
        snap_no = clob.get_snapshot(market.condition_id, market.token_no_id)
        if not snap_yes or not snap_no:
            return violations

        # Bundle LONG: buy YES + buy NO; profitable if total_ask < 1 - fees
        total_ask = snap_yes.best_ask + snap_no.best_ask
        fee_cost = self._fee * total_ask + self._gas * 2
        bundle_long_edge = 1.0 - total_ask - fee_cost

        if bundle_long_edge > self._min_edge:
            violations.append(ConsistencyViolation(
                market_ids=[market.condition_id],
                violation_type="bundle_long",
                expected=1.0,
                actual=total_ask,
                edge_estimate=1.0 - total_ask,
                net_edge=bundle_long_edge,
                explanation=(
                    f"Bundle long: YES ask {snap_yes.best_ask:.3f} + "
                    f"NO ask {snap_no.best_ask:.3f} = {total_ask:.3f} < 1. "
                    f"Net edge: {bundle_long_edge:.4f}"
                ),
            ))

        # Bundle SHORT: sell YES + sell NO; profitable if total_bid > 1 + fees
        total_bid = snap_yes.best_bid + snap_no.best_bid
        fee_cost_short = self._fee * total_bid + self._gas * 2
        bundle_short_edge = total_bid - 1.0 - fee_cost_short

        if bundle_short_edge > self._min_edge:
            violations.append(ConsistencyViolation(
                market_ids=[market.condition_id],
                violation_type="bundle_short",
                expected=1.0,
                actual=total_bid,
                edge_estimate=total_bid - 1.0,
                net_edge=bundle_short_edge,
                explanation=(
                    f"Bundle short: YES bid {snap_yes.best_bid:.3f} + "
                    f"NO bid {snap_no.best_bid:.3f} = {total_bid:.3f} > 1. "
                    f"Net edge: {bundle_short_edge:.4f}"
                ),
            ))

        return violations

    def _check_neg_risk_group(
        self, members: list[CandidateMarket]
    ) -> list[ConsistencyViolation]:
        """In a neg_risk group, the sum of all YES midpoints should equal the group total.

        Only include markets with tight spreads (<0.5) — empty order books produce
        midpoint=0.5 by construction and cause spurious sum violations.
        """
        violations: list[ConsistencyViolation] = []
        # Filter to markets with tradeable prices
        liquid = [c for c in members if c.snapshot.spread < 0.5]
        if len(liquid) < 2:
            return violations

        midpoints = [c.snapshot.midpoint for c in liquid]
        total_mid = sum(midpoints)

        market_ids = [c.market.condition_id for c in liquid]

        # Overpriced: sum > 1.0 → sell all YES tokens
        overpricing = total_mid - 1.0
        if overpricing > self._min_edge:
            fee_cost = self._fee * total_mid + self._gas * len(liquid)
            net_edge = overpricing - fee_cost
            if net_edge > 0:
                violations.append(ConsistencyViolation(
                    market_ids=market_ids,
                    violation_type="neg_risk_overpriced",
                    expected=1.0,
                    actual=total_mid,
                    edge_estimate=overpricing,
                    net_edge=net_edge,
                    explanation=(
                        f"neg_risk group sum = {total_mid:.3f} > 1.0 "
                        f"({len(liquid)} liquid markets). Net edge: {net_edge:.4f}"
                    ),
                ))

        # Underpriced: sum < 1.0 → buy all YES tokens (bundle long)
        underpricing = 1.0 - total_mid
        if underpricing > self._min_edge:
            fee_cost = self._fee * total_mid + self._gas * len(liquid)
            net_edge = underpricing - fee_cost
            if net_edge > 0:
                violations.append(ConsistencyViolation(
                    market_ids=market_ids,
                    violation_type="neg_risk_underpriced",
                    expected=1.0,
                    actual=total_mid,
                    edge_estimate=underpricing,
                    net_edge=net_edge,
                    explanation=(
                        f"neg_risk group sum = {total_mid:.3f} < 1.0 "
                        f"({len(liquid)} liquid markets). Bundle long net edge: {net_edge:.4f}"
                    ),
                ))

        return violations
