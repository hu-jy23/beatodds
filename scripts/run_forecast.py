#!/usr/bin/env python3
"""Run the full signal pipeline: scan → parse → retrieve → forecast.

Usage:
    uv run scripts/run_forecast.py
    uv run scripts/run_forecast.py --top 5 --dry-run  (scan + parse only)
    uv run scripts/run_forecast.py --top 5          (requires API keys in .env)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger

from beatodds.common.types import EvalRecord
from beatodds.evaluation.metrics import check_temporal_integrity
from beatodds.evidence.forecaster import LLMForecaster
from beatodds.evidence.retriever import EvidenceRetriever
from beatodds.resolution_parser.parser import ResolutionParser
from beatodds.scanner.scanner import Scanner

_SPORTS_KEYWORDS = [
    "world cup", "fifa", "nba", "nfl", "nhl", "mlb", "premier league",
    "bundesliga", "la liga", "serie a", "champions league", "wimbledon",
    "super bowl", "stanley cup", "march madness", "ncaa",
    "cavaliers", "knicks", "celtics", "lakers", "warriors", "heat",
    "cricket", "tennis", "golf", "formula 1", "formula one", "ufc",
]


def _is_sports(question: str, category: str) -> bool:
    q = question.lower()
    cat = category.lower()
    if "sport" in cat or "soccer" in cat or "football" in cat or "basketball" in cat:
        return True
    return any(kw in q for kw in _SPORTS_KEYWORDS)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=5, help="Number of markets to forecast")
    parser.add_argument("--scan-limit", type=int,
                        help="Number of liquid Gamma markets to scan before forecast filters")
    parser.add_argument("--dry-run", action="store_true",
                        help="Scan and parse only; skips Tavily retrieval and final forecasting")
    parser.add_argument("--backtest", action="store_true",
                        help="Enable strict temporal integrity check (for replay/backtest mode)")
    parser.add_argument("--exclude-sports", action="store_true",
                        help="Skip World Cup / NBA and other sports markets")
    parser.add_argument("--min-prob", type=float, default=0.0,
                        help="Minimum market midpoint probability (default 0 = no filter)")
    parser.add_argument("--max-prob", type=float, default=1.0,
                        help="Maximum market midpoint probability (default 1 = no filter)")
    args = parser.parse_args()

    # --- Scan ---
    scanner = Scanner(market_limit=args.scan_limit)
    candidates = scanner.scan()
    # Filter to tradeable (tight spread) markets for forecasting
    tradeable = [c for c in candidates if c.snapshot.spread < 0.05]
    logger.info(f"Scanner: {len(candidates)} candidates, {len(tradeable)} tradeable (spread<5¢)")

    if args.exclude_sports:
        tradeable = [
            c for c in tradeable
            if not _is_sports(c.market.question, c.market.category)
        ]
        logger.info(f"After excluding sports: {len(tradeable)} candidates")

    if args.min_prob > 0 or args.max_prob < 1.0:
        tradeable = [
            c for c in tradeable
            if args.min_prob <= c.snapshot.midpoint <= args.max_prob
        ]
        logger.info(
            f"After prob filter [{args.min_prob:.2f}, {args.max_prob:.2f}]: "
            f"{len(tradeable)} candidates"
        )

    targets = tradeable[:args.top]
    if not targets:
        logger.warning("No tradeable candidates. Try lowering spread threshold.")
        return

    # --- Resolution Parse ---
    rp = ResolutionParser()
    features_map = {}
    for c in targets:
        f = rp.parse(c.market)
        features_map[c.market.condition_id] = f
        logger.info(f"Parsed: {c.market.question[:50]}")
        logger.info(f"  queries: {f.search_queries[:2]}")

    if args.dry_run:
        print("\n[dry-run] Stopping before evidence retrieval.")
        return

    # --- Evidence + Forecast ---
    retriever = EvidenceRetriever()
    forecaster = LLMForecaster()
    eval_records: list[EvalRecord] = []

    print(f"\n{'='*60}")
    print(f"FORECAST RESULTS  (top {len(targets)} tradeable markets)")
    print(f"{'='*60}")

    for c in targets:
        features = features_map[c.market.condition_id]

        evidence, frozen_at = retriever.retrieve(c, features)
        forecast = forecaster.forecast(c, evidence, frozen_at)

        p_m = c.snapshot.midpoint
        edge = forecast.p_f - p_m

        print(f"\n{c.market.question[:70]}")
        print(f"  p_m={p_m:.3f}  p_f={forecast.p_f:.3f}  edge={edge:+.3f}  "
              f"conf={forecast.confidence:.2f}")
        print(f"  evidence={len(evidence)} items  model={forecast.model}")
        print(f"  reasoning: {forecast.reasoning[:120]}")

        eval_records.append(EvalRecord(
            condition_id=c.market.condition_id,
            snapshot_time=c.snapshot.snapshot_time,
            p_m=p_m,
            p_f=forecast.p_f,
            evidence_frozen_at=frozen_at,
            signal_type="evidence",
            model_version=forecast.model,
        ))

    # --- Temporal Integrity Check ---
    # In live mode: snapshot_time < evidence_frozen_at is expected (scan then search).
    # Only enforce in --backtest mode where evidence must predate the snapshot.
    if args.backtest:
        violations = check_temporal_integrity(eval_records)
        if violations:
            print(f"\n[TEMPORAL INTEGRITY FAILURES — {len(violations)} violations]")
            for v in violations:
                print(f"  {v}")
        else:
            print(f"\n✓ Temporal integrity OK ({len(eval_records)} records, 0 violations)")
    else:
        print(f"\n✓ Live mode: {len(eval_records)} records. "
              f"(Use --backtest for strict temporal integrity check)")


if __name__ == "__main__":
    main()
