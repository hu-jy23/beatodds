#!/usr/bin/env python3
"""Run a live paper-trading pass from the full BeatOdds pipeline.

The command scans live Polymarket markets, forecasts fair YES probability,
decides whether to buy YES or NO, simulates fills from visible CLOB ask depth,
records orders/fills/positions in DuckDB, and appends every decision to JSONL.
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.types import CandidateMarket, EvalRecord
from beatodds.data.clob_client import ClobReadClient
from beatodds.evaluation.paper_store import (
    create_paper_account,
    load_paper_account,
    load_paper_positions,
    record_paper_buy,
    update_risk_params,
)
from beatodds.evaluation.store import save_eval_records
from beatodds.evaluation.workflow_store import save_forecast_run
from beatodds.evidence.forecaster import LLMForecaster
from beatodds.evidence.retriever import EvidenceRetriever
from beatodds.resolution_parser.parser import ResolutionParser
from beatodds.scanner.scanner import Scanner

DEFAULT_ACCOUNT_ID = "paper-live-1000"
DEFAULT_LOG_PATH = Path("data") / "paper_decisions.jsonl"

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


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _json_default(value):
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _append_log(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, default=_json_default, ensure_ascii=True) + "\n")


def _ensure_account(args) -> None:
    account = load_paper_account(args.account_id)
    if account is None:
        create_paper_account(
            account_id=args.account_id,
            name="BeatOdds Live $1000 Paper Account",
            initial_cash=args.initial_cash,
            risk_profile="paper_live",
            sizing_mode="fraction",
            order_fraction=args.order_fraction,
            max_order_notional=args.max_order_notional,
            max_market_exposure=args.max_market_exposure,
            max_event_exposure=args.max_event_exposure,
            max_category_exposure=args.max_category_exposure,
            max_total_exposure=args.max_total_exposure,
            min_cash_buffer=args.min_cash_buffer,
            fee_rate_bps=args.fee_rate_bps,
            slippage_bps=args.slippage_bps,
            notes="Created by scripts/run_paper_trader.py for live forward evaluation.",
        )
        return
    update_risk_params(
        args.account_id,
        risk_profile="paper_live",
        sizing_mode="fraction",
        order_fraction=args.order_fraction,
        max_order_notional=args.max_order_notional,
        max_market_exposure=args.max_market_exposure,
        max_event_exposure=args.max_event_exposure,
        max_category_exposure=args.max_category_exposure,
        max_total_exposure=args.max_total_exposure,
        min_cash_buffer=args.min_cash_buffer,
        fee_rate_bps=args.fee_rate_bps,
        slippage_bps=args.slippage_bps,
    )


def _level_price(level) -> float | None:
    value = level.get("price") if isinstance(level, dict) else getattr(level, "price", None)
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _level_size(level) -> float | None:
    for key in ("size", "shares", "amount"):
        value = level.get(key) if isinstance(level, dict) else getattr(level, key, None)
        try:
            return float(value)
        except (TypeError, ValueError):
            pass
    return None


def _ask_levels(book: dict | None) -> list[tuple[float, float]]:
    asks = (book or {}).get("asks") or []
    levels: list[tuple[float, float]] = []
    for level in asks:
        price = _level_price(level)
        size = _level_size(level)
        if price is None or size is None or price <= 0 or size <= 0:
            continue
        levels.append((price, size))
    return sorted(levels, key=lambda item: item[0])


def _simulate_buy(
    levels: list[tuple[float, float]],
    target_notional: float,
) -> tuple[list[tuple[float, float]], float, float]:
    remaining = target_notional
    fills: list[tuple[float, float]] = []
    for price, available_shares in levels:
        if remaining <= 0:
            break
        max_notional = price * available_shares
        take_notional = min(remaining, max_notional)
        shares = take_notional / price
        fills.append((price, shares))
        remaining -= take_notional
    filled_notional = sum(price * shares for price, shares in fills)
    filled_shares = sum(shares for _, shares in fills)
    return fills, filled_notional, filled_shares


def _current_exposure(account_id: str) -> dict[str, float]:
    positions = load_paper_positions(account_id)
    exposure = {
        "total": 0.0,
    }
    for position in positions:
        exposure["total"] += position.cost_basis
        exposure[f"market:{position.condition_id}"] = (
            exposure.get(f"market:{position.condition_id}", 0.0) + position.cost_basis
        )
        if position.event_id:
            exposure[f"event:{position.event_id}"] = (
                exposure.get(f"event:{position.event_id}", 0.0) + position.cost_basis
            )
        if position.category:
            exposure[f"category:{position.category}"] = (
                exposure.get(f"category:{position.category}", 0.0) + position.cost_basis
            )
    return exposure


def _sized_notional(candidate: CandidateMarket, args, account, edge: float) -> tuple[float, str]:
    available_cash = max(0.0, account.cash_balance - account.min_cash_buffer)
    exposure = _current_exposure(account.account_id)
    market_used = exposure.get(f"market:{candidate.market.condition_id}", 0.0)
    event_used = exposure.get(f"event:{candidate.market.event_id}", 0.0)
    category_used = exposure.get(f"category:{candidate.market.category}", 0.0)
    total_used = exposure.get("total", 0.0)
    risk_fraction = min(
        args.max_order_fraction,
        max(args.min_order_fraction, abs(edge) * args.edge_size_multiplier),
    )
    target = account.cash_balance * risk_fraction * account.order_fraction
    caps = [
        account.max_order_notional,
        available_cash,
        account.max_market_exposure - market_used,
        account.max_event_exposure - event_used,
        account.max_category_exposure - category_used,
        account.max_total_exposure - total_used,
    ]
    notional = min(target, *caps)
    if notional < args.min_order_notional:
        return 0.0, f"size below minimum: ${notional:.2f} < ${args.min_order_notional:.2f}"
    return round(notional, 2), "risk sizing accepted"


def _choose_side(candidate: CandidateMarket, forecast, clob: ClobReadClient) -> dict:
    yes_book = clob.get_order_book(candidate.market.token_yes_id)
    no_book = clob.get_order_book(candidate.market.token_no_id)
    yes_asks = _ask_levels(yes_book)
    no_asks = _ask_levels(no_book)
    yes_ask = yes_asks[0][0] if yes_asks else None
    no_ask = no_asks[0][0] if no_asks else None
    yes_edge = forecast.p_f - yes_ask if yes_ask is not None else None
    no_edge = (1.0 - forecast.p_f) - no_ask if no_ask is not None else None
    options = []
    if yes_edge is not None:
        options.append(("YES", yes_edge, yes_ask, yes_asks, candidate.market.token_yes_id))
    if no_edge is not None:
        options.append(("NO", no_edge, no_ask, no_asks, candidate.market.token_no_id))
    if not options:
        return {"status": "skip", "reason": "no executable YES or NO ask depth"}
    side, edge, ask, levels, token_id = max(options, key=lambda item: item[1])
    return {
        "status": "ok",
        "side": side,
        "gross_edge": edge,
        "best_ask": ask,
        "levels": levels,
        "token_id": token_id,
        "side_fair_prob": forecast.p_f if side == "YES" else 1.0 - forecast.p_f,
    }


def _parse_args():
    parser = argparse.ArgumentParser(description="Run live BeatOdds paper trading")
    parser.add_argument("--account-id", default=DEFAULT_ACCOUNT_ID)
    parser.add_argument("--initial-cash", type=float, default=1_000.0)
    parser.add_argument("--top", type=int, default=12)
    parser.add_argument("--trial-aggressive", action="store_true",
                        help="Use a broader, more purchase-friendly trial policy")
    parser.add_argument("--exclude-sports", action="store_true", default=False)
    parser.add_argument("--include-sports", action="store_false", dest="exclude_sports")
    parser.add_argument("--min-prob", type=float, default=0.05)
    parser.add_argument("--max-prob", type=float, default=0.95)
    parser.add_argument("--max-spread", type=float, default=0.08)
    parser.add_argument("--min-edge", type=float, default=0.015)
    parser.add_argument("--min-net-edge", type=float, default=0.0)
    parser.add_argument("--min-confidence", type=float, default=0.15)
    parser.add_argument("--min-order-notional", type=float, default=2.0)
    parser.add_argument("--max-order-notional", type=float, default=60.0)
    parser.add_argument("--order-fraction", type=float, default=1.0)
    parser.add_argument("--min-order-fraction", type=float, default=0.01)
    parser.add_argument("--max-order-fraction", type=float, default=0.06)
    parser.add_argument("--edge-size-multiplier", type=float, default=1.25)
    parser.add_argument("--max-market-exposure", type=float, default=180.0)
    parser.add_argument("--max-event-exposure", type=float, default=400.0)
    parser.add_argument("--max-category-exposure", type=float, default=800.0)
    parser.add_argument("--max-total-exposure", type=float, default=1_000.0)
    parser.add_argument("--min-cash-buffer", type=float, default=0.0)
    parser.add_argument("--fee-rate-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--evidence-results-per-query", type=int, default=8)
    parser.add_argument("--forecast-evidence-items", type=int, default=16)
    parser.add_argument("--forecast-max-tokens", type=int, default=512)
    parser.add_argument("--log-path", type=Path, default=DEFAULT_LOG_PATH)
    args = parser.parse_args()
    if args.trial_aggressive:
        args.top = max(args.top, 20)
        args.max_spread = max(args.max_spread, 0.10)
        args.min_edge = min(args.min_edge, 0.005)
        args.min_net_edge = min(args.min_net_edge, -0.005)
        args.min_confidence = min(args.min_confidence, 0.10)
        args.min_order_notional = min(args.min_order_notional, 1.0)
        args.max_order_notional = min(args.max_order_notional, 40.0)
        args.min_order_fraction = min(args.min_order_fraction, 0.005)
        args.max_order_fraction = min(args.max_order_fraction, 0.04)
        args.evidence_results_per_query = max(args.evidence_results_per_query, 10)
        args.forecast_evidence_items = max(args.forecast_evidence_items, 20)
        args.forecast_max_tokens = max(args.forecast_max_tokens, 768)
    return args


def main() -> None:
    args = _parse_args()
    cfg = get_settings()
    _ensure_account(args)
    run_id = str(uuid4())
    run_started_at = _now()
    _append_log(args.log_path, {
        "type": "run_start",
        "run_id": run_id,
        "created_at": run_started_at,
        "account_id": args.account_id,
        "initial_cash": args.initial_cash,
        "rules": vars(args),
        "llm_backend": cfg.llm_backend,
    })

    scanner = Scanner()
    candidates = scanner.scan()
    tradeable = [c for c in candidates if c.snapshot.spread <= args.max_spread]
    if args.exclude_sports:
        tradeable = [
            c for c in tradeable
            if not _is_sports(c.market.question, c.market.category)
        ]
    tradeable = [
        c for c in tradeable
        if args.min_prob <= c.snapshot.midpoint <= args.max_prob
    ]
    targets = tradeable[:args.top]
    logger.info(f"Paper trader: {len(candidates)} scanned, {len(targets)} targets")

    rp = ResolutionParser()
    retriever = EvidenceRetriever()
    forecaster = LLMForecaster(
        max_evidence_items=args.forecast_evidence_items,
        max_tokens=args.forecast_max_tokens,
    )
    clob = ClobReadClient()
    eval_records: list[EvalRecord] = []
    trades = 0

    print(f"\nPAPER TRADING RUN {run_id}")
    print(f"account={args.account_id} targets={len(targets)} log={args.log_path}")
    for i, candidate in enumerate(targets, start=1):
        decision = {
            "type": "decision",
            "run_id": run_id,
            "created_at": _now(),
            "rank": i,
            "account_id": args.account_id,
            "condition_id": candidate.market.condition_id,
            "event_id": candidate.market.event_id,
            "category": candidate.market.category,
            "question": candidate.market.question,
            "p_m_yes": candidate.snapshot.midpoint,
            "spread": candidate.snapshot.spread,
            "priority_score": candidate.priority_score,
            "scan_flags": candidate.scan_flags,
        }
        try:
            features = rp.parse(candidate.market)
            evidence, frozen_at = retriever.retrieve(
                candidate,
                features,
                max_results_per_query=args.evidence_results_per_query,
            )
            forecast = forecaster.forecast(candidate, evidence, frozen_at)
            forecast_run_id = save_forecast_run(
                candidate=candidate,
                features=features,
                evidence=evidence,
                forecast=forecast,
                evidence_frozen_at=frozen_at,
            )
            eval_records.append(EvalRecord(
                condition_id=candidate.market.condition_id,
                snapshot_time=candidate.snapshot.snapshot_time,
                p_m=candidate.snapshot.midpoint,
                p_f=forecast.p_f,
                evidence_frozen_at=frozen_at,
                signal_type="search_only_llm",
                model_version=forecast.model,
            ))
            side = _choose_side(candidate, forecast, clob)
            decision.update({
                "forecast_run_id": forecast_run_id,
                "p_f_yes": forecast.p_f,
                "confidence": forecast.confidence,
                "forecast_direction": forecast.forecast_direction,
                "model": forecast.model,
                "evidence_count": len(evidence),
                "reasoning": forecast.reasoning,
            })
            if side["status"] != "ok":
                decision.update({"action": "skip", "skip_reason": side["reason"]})
                print(f"[{i}] SKIP no depth  {candidate.market.question[:70]}")
                _append_log(args.log_path, decision)
                continue

            fee_rate = args.fee_rate_bps / 10_000
            slip = args.slippage_bps / 10_000
            net_edge = side["gross_edge"] - fee_rate - slip
            decision.update({
                "side": side["side"],
                "token_id": side["token_id"],
                "best_ask": side["best_ask"],
                "side_fair_prob": side["side_fair_prob"],
                "gross_edge": side["gross_edge"],
                "net_edge": net_edge,
            })
            if side["gross_edge"] < args.min_edge:
                decision.update({"action": "skip", "skip_reason": "edge below threshold"})
                print(
                    f"[{i}] SKIP edge={side['gross_edge']:+.3f}  "
                    f"{candidate.market.question[:70]}"
                )
                _append_log(args.log_path, decision)
                continue
            if net_edge < args.min_net_edge:
                decision.update({"action": "skip", "skip_reason": "net edge below threshold"})
                print(f"[{i}] SKIP net={net_edge:+.3f}  {candidate.market.question[:70]}")
                _append_log(args.log_path, decision)
                continue
            if forecast.confidence < args.min_confidence:
                decision.update({"action": "skip", "skip_reason": "confidence below threshold"})
                print(
                    f"[{i}] SKIP conf={forecast.confidence:.2f}  "
                    f"{candidate.market.question[:70]}"
                )
                _append_log(args.log_path, decision)
                continue

            account = load_paper_account(args.account_id)
            if account is None:
                raise RuntimeError(f"paper account disappeared: {args.account_id}")
            notional, size_reason = _sized_notional(candidate, args, account, side["gross_edge"])
            if notional <= 0:
                decision.update({"action": "skip", "skip_reason": size_reason})
                print(f"[{i}] SKIP size  {candidate.market.question[:70]}")
                _append_log(args.log_path, decision)
                continue

            fills, filled_notional, filled_shares = _simulate_buy(side["levels"], notional)
            if filled_notional < args.min_order_notional or not fills:
                decision.update({
                    "action": "skip",
                    "skip_reason": "insufficient visible ask depth",
                    "requested_notional": notional,
                    "filled_notional_available": filled_notional,
                })
                print(f"[{i}] SKIP depth  {candidate.market.question[:70]}")
                _append_log(args.log_path, decision)
                continue

            order = record_paper_buy(
                account_id=args.account_id,
                run_id=run_id,
                condition_id=candidate.market.condition_id,
                token_id=side["token_id"],
                side=side["side"],
                requested_notional=notional,
                fill_levels=fills,
                p_m_yes=candidate.snapshot.midpoint,
                p_f_yes=forecast.p_f,
                side_fair_prob=side["side_fair_prob"],
                gross_edge=side["gross_edge"],
                net_edge=net_edge,
                confidence=forecast.confidence,
                event_id=candidate.market.event_id,
                category=candidate.market.category,
                question=candidate.market.question,
                forecast_run_id=forecast_run_id,
                decision_reason=forecast.reasoning,
                fee_rate_bps=args.fee_rate_bps,
            )
            trades += 1
            decision.update({
                "action": "buy",
                "order_id": order.order_id,
                "status": order.status,
                "requested_notional": order.requested_notional,
                "filled_notional": order.filled_notional,
                "filled_shares": order.filled_shares,
                "avg_price": order.avg_price,
                "fee": order.fee,
                "fills": [{"price": price, "shares": shares} for price, shares in fills],
            })
            print(
                f"[{i}] BUY {order.side} ${order.filled_notional:.2f} "
                f"avg={order.avg_price:.3f} net={order.net_edge:+.3f} "
                f"{candidate.market.question[:58]}"
            )
            _append_log(args.log_path, decision)
        except Exception as exc:
            decision.update({"action": "error", "error": str(exc)})
            _append_log(args.log_path, decision)
            logger.exception(f"Paper decision failed for {candidate.market.condition_id}")

    if eval_records:
        save_eval_records(eval_records)
    account = load_paper_account(args.account_id)
    _append_log(args.log_path, {
        "type": "run_end",
        "run_id": run_id,
        "created_at": _now(),
        "account_id": args.account_id,
        "targets": len(targets),
        "trades": trades,
        "cash_balance": account.cash_balance if account else None,
        "reserved_cash": account.reserved_cash if account else None,
    })
    print(f"\nDone. trades={trades} cash=${account.cash_balance if account else 0:.2f}")


if __name__ == "__main__":
    main()
