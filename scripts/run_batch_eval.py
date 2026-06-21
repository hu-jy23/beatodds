#!/usr/bin/env python3
"""Batch forecast + EvalRecord accumulation.

Runs forecast on top N markets, saves results to DuckDB, prints edge stats.

Usage:
    uv run scripts/run_batch_eval.py --top 20
    uv run scripts/run_batch_eval.py --top 20 --exclude-sports --min-prob 0.05 --max-prob 0.95
    uv run scripts/run_batch_eval.py --show-stored          (print stored records)
    uv run scripts/run_batch_eval.py --show-workflow        (print workflow DB state)
    uv run scripts/run_batch_eval.py --show-market <condition_id>
    uv run scripts/run_batch_eval.py --show-due --stale-hours 24
    uv run scripts/run_batch_eval.py --resolve <condition_id> --outcome 1   (mark resolved YES)
    uv run scripts/run_batch_eval.py --resolve <condition_id> --outcome 0   (mark resolved NO)
    uv run scripts/run_batch_eval.py --compute-bss           (BSS from resolved records)
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger

from beatodds.common.types import EvalRecord
from beatodds.evaluation.metrics import compute_predictive
from beatodds.evaluation.store import (
    edge_distribution_summary,
    load_eval_records,
    mark_resolved,
    save_eval_records,
)
from beatodds.evaluation.workflow_store import (
    load_due_markets,
    load_evidence_for_run,
    load_forecast_runs,
    load_market_snapshots,
    load_resolution_features,
    load_tracked_market,
    load_tracked_markets,
    mark_outcome,
    save_forecast_run,
    workflow_summary,
)
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


def _fmt_time(value) -> str:
    return value.isoformat() if hasattr(value, "isoformat") else str(value or "?")


def _print_market_history(condition_id: str) -> None:
    market = load_tracked_market(condition_id)
    snapshots = load_market_snapshots(condition_id, limit=10)
    runs = load_forecast_runs(condition_id, limit=10)
    features = load_resolution_features(condition_id)

    if market is None and not snapshots and not runs:
        print(f"No workflow history found for {condition_id}")
        return

    print(f"\n{'='*70}")
    print("MARKET WORKFLOW HISTORY")
    print(f"{'='*70}")
    print(f"  condition_id = {condition_id}")
    if market:
        print(f"  status = {market['tracking_status']}  resolved={market['resolved_outcome']}")
        print(f"  first_seen = {_fmt_time(market['first_seen_at'])}")
        print(f"  last_seen = {_fmt_time(market['last_seen_at'])}")
        print(f"  question = {market['question']}")
    if features:
        queries = "; ".join(features.search_queries[:4])
        print(f"  condition_type = {features.condition_type}")
        print(f"  event_type = {features.event_type}  china = {features.china_relevance}")
        if features.source_routing_hints:
            print(f"  source_routes = {'; '.join(features.source_routing_hints[:4])}")
        print(f"  search_queries = {queries or '?'}")

    print(f"\n  Snapshots ({len(snapshots)} shown)")
    for s in snapshots:
        flags = ",".join(s["scan_flags"]) or "-"
        print(
            f"  {_fmt_time(s['snapshot_time'])}  p_m={s['midpoint']:.3f}  "
            f"bid={s['best_bid']:.3f}  ask={s['best_ask']:.3f}  "
            f"spread={s['spread']:.3f}  flags={flags}"
        )

    print(f"\n  Forecast Runs ({len(runs)} shown)")
    for r in runs:
        print(
            f"  {r['run_id'][:8]}  {_fmt_time(r['snapshot_time'])}  "
            f"p_m={r['p_m']:.3f}  p_f={r['p_f']:.3f}  "
            f"edge={r['edge']:+.3f}  conf={r['confidence']:.2f}  "
            f"{r['signal_type']}:{r['model_version']}"
        )
        print(f"    {r['reasoning'][:140]}")

    if runs:
        evidence = load_evidence_for_run(runs[0]["run_id"])[:5]
        print(f"\n  Latest Run Evidence ({len(evidence)} shown)")
        for item in evidence:
            print(
                f"  score={item.relevance_score:.3f}  type={item.source_type}  "
                f"query={item.query[:70] or '?'}"
            )
            print(f"    {item.source}: {item.title[:110]}")


def _print_due_markets(stale_hours: float, limit: int) -> None:
    due = load_due_markets(stale_after_hours=stale_hours, limit=limit)
    print(f"\n{'='*70}")
    print("DUE MARKETS")
    print(f"{'='*70}")
    print(f"  stale_after_hours = {stale_hours:.2f}")
    print(f"  due_count = {len(due)}")
    if not due:
        print("  No tracked markets are due for a new forecast.")
        return

    for market in due:
        latest_p_f = (
            f"{market['latest_p_f']:.3f}"
            if market["latest_p_f"] is not None else "?"
        )
        latest_edge = (
            f"{market['latest_edge']:+.3f}"
            if market["latest_edge"] is not None else "?"
        )
        p_m = f"{market['p_m']:.3f}" if market["p_m"] is not None else "?"
        spread = f"{market['spread']:.3f}" if market["spread"] is not None else "?"
        print(f"  condition_id={market['condition_id']}")
        print(
            f"    reason={market['due_reason']}  p_m={p_m}  spread={spread}  "
            f"last_p_f={latest_p_f}  last_edge={latest_edge}"
        )
        print(
            f"    last_seen={_fmt_time(market['last_seen_at'])}  "
            f"latest_forecast={_fmt_time(market['latest_forecast_at'])}"
        )
        print(f"    {market['question'][:100]}")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--top", type=int, default=10)
    parser.add_argument("--scan-limit", type=int,
                        help="Number of liquid Gamma markets to scan before eval filters")
    parser.add_argument("--exclude-sports", action="store_true",
                        help="Skip World Cup / NBA and other sports markets")
    parser.add_argument("--min-prob", type=float, default=0.0,
                        help="Minimum market midpoint probability (default 0 = no filter)")
    parser.add_argument("--max-prob", type=float, default=1.0,
                        help="Maximum market midpoint probability (default 1 = no filter)")
    parser.add_argument("--china-info", action="store_true",
                        help="Enable China-specific query expansion and source routing")
    parser.add_argument("--show-stored", action="store_true",
                        help="Print all stored EvalRecords from DuckDB and exit")
    parser.add_argument("--show-workflow", action="store_true",
                        help="Print tracked market workflow state and exit")
    parser.add_argument("--show-market", metavar="CONDITION_ID",
                        help="Print workflow history for one tracked market and exit")
    parser.add_argument("--show-due", action="store_true",
                        help="Print tracked markets due for a new forecast and exit")
    parser.add_argument("--stale-hours", type=float, default=24.0,
                        help="Forecast staleness threshold for --show-due")
    parser.add_argument("--resolve", metavar="CONDITION_ID",
                        help="Mark a condition_id as resolved (combine with --outcome)")
    parser.add_argument("--outcome", type=float, choices=[0.0, 1.0],
                        help="Resolution outcome: 1.0=YES, 0.0=NO")
    parser.add_argument("--compute-bss", action="store_true",
                        help="Compute Brier Skill Score from all resolved records")
    args = parser.parse_args()

    if args.resolve:
        if args.outcome is None:
            print("Error: --resolve requires --outcome 0 or --outcome 1")
            sys.exit(1)
        n = mark_resolved(args.resolve, args.outcome)
        mark_outcome(args.resolve, args.outcome, source="manual")
        print(f"Updated {n} EvalRecord(s) and workflow outcome for {args.resolve} → "
              f"outcome={args.outcome}")
        return

    if args.compute_bss:
        records = load_eval_records(resolved_only=True)
        if not records:
            print("No resolved records yet. Run after markets settle and use --resolve.")
            return
        metrics = compute_predictive(records)
        print(f"\nBRIER SKILL SCORE REPORT  (n={metrics.n} resolved markets)")
        print(f"  Brier Score (ours):   {metrics.brier_score:.4f}")
        print(f"  Brier Score (market): {metrics.brier_market:.4f}")
        print(f"  Brier Skill Score:    {metrics.brier_skill_score:+.4f}  (>0 beats market)")
        print(f"  Log Loss:             {metrics.log_loss:.4f}")
        print(f"  Mean edge:            {metrics.mean_edge:+.4f}")
        print(f"  Mean |edge|:          {metrics.mean_abs_edge:.4f}")
        return

    if args.show_stored:
        records = load_eval_records()
        if not records:
            print("No EvalRecords stored yet.")
            return
        print(f"\n{'='*70}")
        print(f"STORED EVAL RECORDS ({len(records)} total)")
        print(f"{'='*70}")
        for r in records[:50]:
            edge = r.p_f - r.p_m
            print(f"  condition_id={r.condition_id}")
            print(f"    p_m={r.p_m:.3f}  p_f={r.p_f:.3f}  edge={edge:+.3f}  "
                  f"model={r.model_version}  resolved={r.resolved_outcome}")
        stats = edge_distribution_summary(records)
        print(f"\nEdge stats: {stats}")
        return

    if args.show_workflow:
        summary = workflow_summary()
        print(f"\n{'='*70}")
        print("WORKFLOW STATE")
        print(f"{'='*70}")
        print(f"  tracked_markets = {summary['tracked_markets']}")
        print(f"  market_snapshots = {summary['market_snapshots']}")
        print(f"  forecast_runs = {summary['forecast_runs']}")
        print(f"  evidence_items = {summary['evidence_items']}")
        print(f"  outcomes = {summary['outcomes']}")
        for market in load_tracked_markets(limit=20):
            print(f"  condition_id={market['condition_id']}")
            print(f"    status={market['tracking_status']}  "
                  f"resolved={market['resolved_outcome']}  {market['question'][:60]}")
        return

    if args.show_market:
        _print_market_history(args.show_market)
        return

    if args.show_due:
        _print_due_markets(args.stale_hours, args.top)
        return

    # --- Scan ---
    scanner = Scanner(market_limit=args.scan_limit)
    candidates = scanner.scan()
    tradeable = [c for c in candidates if c.snapshot.spread < 0.05]
    logger.info(f"Scanner: {len(candidates)} candidates, {len(tradeable)} tradeable")

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
        logger.warning("No candidates after filters.")
        return

    # --- Parse + Retrieve + Forecast ---
    rp = ResolutionParser()
    retriever = EvidenceRetriever()
    forecaster = LLMForecaster()
    eval_records: list[EvalRecord] = []

    label = f"top {len(targets)}" + (" non-sports" if args.exclude_sports else "")
    print(f"\n{'='*70}")
    print(f"BATCH FORECAST  ({label} markets)")
    print(f"{'='*70}")

    for i, c in enumerate(targets):
        q = c.market.question
        logger.info(f"[{i+1}/{len(targets)}] {q[:60]}")

        features = rp.parse(c.market)
        evidence, frozen_at = retriever.retrieve(
            c,
            features,
            enable_china_info=args.china_info,
        )
        forecast = forecaster.forecast(c, evidence, frozen_at)
        signal_type = "china_enhanced_llm" if args.china_info else "search_only_llm"
        save_forecast_run(
            candidate=c,
            features=features,
            evidence=evidence,
            forecast=forecast,
            evidence_frozen_at=frozen_at,
            signal_type=signal_type,
        )

        p_m = c.snapshot.midpoint
        edge = forecast.p_f - p_m

        print(f"\n[{i+1}] {q[:65]}")
        print(f"     cat={c.market.category or '?'}  p_m={p_m:.3f}  "
              f"p_f={forecast.p_f:.3f}  edge={edge:+.3f}  "
              f"conf={forecast.confidence:.2f}  ev={len(evidence)}")
        if args.china_info:
            print(
                f"     china={features.china_relevance}  event={features.event_type}  "
                f"routes={len(features.source_routing_hints)}"
            )
        print(f"     {forecast.reasoning[:100]}")

        eval_records.append(EvalRecord(
            condition_id=c.market.condition_id,
            snapshot_time=c.snapshot.snapshot_time,
            p_m=p_m,
            p_f=forecast.p_f,
            evidence_frozen_at=frozen_at,
            signal_type="china_enhanced" if args.china_info else "evidence",
            model_version=forecast.model,
        ))

    # --- Save + Stats ---
    save_eval_records(eval_records)

    stats = edge_distribution_summary(eval_records)

    print(f"\n{'='*70}")
    print("EDGE DISTRIBUTION SUMMARY")
    print(f"{'='*70}")
    print(f"  n={stats['n']}")
    print(f"  mean_edge       = {stats['mean_edge']:+.4f}  (bias check; should be near 0)")
    print(f"  mean_abs_edge   = {stats['mean_abs_edge']:.4f}  (signal strength)")
    print(f"  max_edge        = {stats['max_edge']:+.4f}")
    print(f"  min_edge        = {stats['min_edge']:+.4f}")
    print(f"  pct |edge|>3%   = {stats['pct_edge_gt_3pct']:.1%}")
    print("\n  Records and workflow state saved to data/eval.duckdb")
    print("  Run --show-stored to inspect, then --resolve <id> --outcome 1/0 to compute BSS")


if __name__ == "__main__":
    main()
