"""Evaluation metrics. Predictive and trading metrics kept separate."""

from __future__ import annotations

import math
from dataclasses import dataclass

from beatodds.common.types import EvalRecord


@dataclass
class PredictiveMetrics:
    n: int
    brier_score: float
    brier_market: float
    brier_skill_score: float    # 1 - brier/brier_market; >0 beats market
    log_loss: float
    mean_edge: float
    mean_abs_edge: float


def compute_predictive(records: list[EvalRecord]) -> PredictiveMetrics:
    resolved = [r for r in records if r.resolved_outcome is not None]
    if not resolved:
        raise ValueError("No resolved records")

    n = len(resolved)
    brier = sum((r.p_f - r.resolved_outcome) ** 2 for r in resolved) / n
    brier_mkt = sum((r.p_m - r.resolved_outcome) ** 2 for r in resolved) / n
    bss = 1.0 - brier / brier_mkt if brier_mkt > 0 else 0.0

    eps = 1e-9
    ll = -sum(
        r.resolved_outcome * math.log(max(r.p_f, eps))
        + (1 - r.resolved_outcome) * math.log(max(1 - r.p_f, eps))
        for r in resolved
    ) / n

    edges = [r.p_f - r.p_m for r in resolved]
    mean_edge = sum(edges) / n
    mean_abs_edge = sum(abs(e) for e in edges) / n

    return PredictiveMetrics(
        n=n,
        brier_score=round(brier, 6),
        brier_market=round(brier_mkt, 6),
        brier_skill_score=round(bss, 6),
        log_loss=round(ll, 6),
        mean_edge=round(mean_edge, 6),
        mean_abs_edge=round(mean_abs_edge, 6),
    )


def check_temporal_integrity(records: list[EvalRecord]) -> list[str]:
    """Verify no time leakage: evidence_frozen_at must be < snapshot_time."""
    violations = []
    for r in records:
        if r.evidence_frozen_at >= r.snapshot_time:
            violations.append(
                f"{r.condition_id}: frozen_at {r.evidence_frozen_at} >= "
                f"snapshot_time {r.snapshot_time} — TIME LEAKAGE"
            )
    return violations
