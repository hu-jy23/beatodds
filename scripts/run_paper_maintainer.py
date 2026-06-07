#!/usr/bin/env python3
"""Maintain a paper account by dynamically selling and buying."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path
from uuid import uuid4

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.types import CandidateMarket, EvalRecord
from beatodds.data.clob_client import ClobReadClient
from beatodds.evaluation.paper_eval import load_paper_decisions, mark_decisions_to_market
from beatodds.evaluation.paper_store import (
    create_paper_account,
    load_paper_account,
    load_paper_positions,
    record_paper_buy,
    record_paper_sell,
    update_risk_params,
)
from beatodds.evaluation.paper_strategy import (
    account_money_snapshot,
    append_jsonl,
    ask_levels,
    now_utc,
    position_exposure,
    sell_estimate,
    simulate_buy,
)
from beatodds.evaluation.store import save_eval_records
from beatodds.evaluation.workflow_store import save_forecast_run
from beatodds.evidence.forecaster import LLMForecaster
from beatodds.evidence.retriever import EvidenceRetriever
from beatodds.resolution_parser.parser import ResolutionParser
from beatodds.scanner.scanner import Scanner

DEFAULT_ACCOUNT_ID = "paper-wise-1000"
DEFAULT_DECISION_LOG_PATH = Path("data") / "paper_decisions.jsonl"
DEFAULT_STRATEGY_LOG_PATH = Path("data") / "paper_strategy_runs.jsonl"

SPORTS_KEYWORDS = [
    "world cup", "fifa", "nba", "nfl", "nhl", "mlb", "premier league",
    "bundesliga", "la liga", "serie a", "champions league", "wimbledon",
    "super bowl", "stanley cup", "march madness", "ncaa", "cavaliers",
    "knicks", "celtics", "lakers", "warriors", "heat", "cricket",
    "tennis", "golf", "formula 1", "formula one", "ufc",
]


def _is_sports(question: str, category: str) -> bool:
    text = f"{question} {category}".lower()
    return "sport" in category.lower() or any(keyword in text for keyword in SPORTS_KEYWORDS)


def _strategy_params(args) -> dict:
    keys = [
        "top", "scan_limit", "exclude_sports", "min_prob", "max_prob", "max_spread",
        "min_edge", "min_net_edge", "min_confidence", "min_order_notional",
        "max_order_notional", "min_order_fraction", "max_order_fraction",
        "edge_size_multiplier", "max_market_exposure", "max_event_exposure",
        "max_category_exposure", "max_total_exposure", "min_cash_buffer",
        "fee_rate_bps", "slippage_bps", "sell_min_return", "sell_min_score",
        "sell_max_loss", "sell_fraction", "sell_all_eligible",
    ]
    return {key: getattr(args, key) for key in keys}


def _append_decision_log(args, row: dict) -> None:
    append_jsonl(args.decision_log_path, row)


def _ensure_account(args) -> None:
    account = load_paper_account(args.account_id)
    if account is None:
        create_paper_account(
            account_id=args.account_id,
            name=args.account_name,
            initial_cash=args.initial_cash,
            risk_profile="wise",
            sizing_mode="fraction",
            order_fraction=1.0,
            max_order_notional=args.max_order_notional,
            max_market_exposure=args.max_market_exposure,
            max_event_exposure=args.max_event_exposure,
            max_category_exposure=args.max_category_exposure,
            max_total_exposure=args.max_total_exposure,
            min_cash_buffer=args.min_cash_buffer,
            fee_rate_bps=args.fee_rate_bps,
            slippage_bps=args.slippage_bps,
            notes="Wise paper maintainer account.",
        )
        return
    update_risk_params(
        args.account_id,
        risk_profile="wise",
        sizing_mode="fraction",
        order_fraction=1.0,
        max_order_notional=args.max_order_notional,
        max_market_exposure=args.max_market_exposure,
        max_event_exposure=args.max_event_exposure,
        max_category_exposure=args.max_category_exposure,
        max_total_exposure=args.max_total_exposure,
        min_cash_buffer=args.min_cash_buffer,
        fee_rate_bps=args.fee_rate_bps,
        slippage_bps=args.slippage_bps,
    )


def _side_choice(candidate: CandidateMarket, forecast, clob: ClobReadClient) -> dict:
    yes_book = clob.get_order_book(candidate.market.token_yes_id)
    no_book = clob.get_order_book(candidate.market.token_no_id)
    yes_asks = ask_levels(yes_book)
    no_asks = ask_levels(no_book)
    options = []
    if yes_asks:
        yes_ask = yes_asks[0][0]
        options.append({
            "side": "YES",
            "token_id": candidate.market.token_yes_id,
            "best_ask": yes_ask,
            "gross_edge": forecast.p_f - yes_ask,
            "side_fair_prob": forecast.p_f,
            "levels": yes_asks,
        })
    if no_asks:
        no_ask = no_asks[0][0]
        options.append({
            "side": "NO",
            "token_id": candidate.market.token_no_id,
            "best_ask": no_ask,
            "gross_edge": (1.0 - forecast.p_f) - no_ask,
            "side_fair_prob": 1.0 - forecast.p_f,
            "levels": no_asks,
        })
    if not options:
        return {"status": "skip", "reason": "no executable ask depth"}
    return {"status": "ok", **max(options, key=lambda item: item["gross_edge"])}


def _sized_notional(candidate: CandidateMarket, args, account, edge: float) -> tuple[float, str]:
    available_cash = max(0.0, account.cash_balance - account.min_cash_buffer)
    exposure = position_exposure(account.account_id)
    risk_fraction = min(
        args.max_order_fraction,
        max(args.min_order_fraction, abs(edge) * args.edge_size_multiplier),
    )
    target = account.cash_balance * risk_fraction
    caps = [
        account.max_order_notional,
        available_cash,
        account.max_market_exposure - exposure.get(f"market:{candidate.market.condition_id}", 0.0),
        account.max_event_exposure - exposure.get(f"event:{candidate.market.event_id}", 0.0),
        account.max_category_exposure - exposure.get(f"category:{candidate.market.category}", 0.0),
        account.max_total_exposure - exposure.get("total", 0.0),
    ]
    notional = min(target, *caps)
    if notional < args.min_order_notional:
        return 0.0, f"size below minimum: ${notional:.2f}"
    return round(notional, 2), "risk sizing accepted"


def _sell_phase(args, run_id: str, strategy_params: dict) -> tuple[int, float, float]:
    if not args.decision_log_path.exists():
        decisions = []
    else:
        decisions = load_paper_decisions(args.decision_log_path, account_id=args.account_id)
    marks = mark_decisions_to_market(decisions)
    mark_by_key = {}
    for mark in marks:
        key = (mark.decision.condition_id, mark.decision.side)
        current = mark_by_key.get(key)
        if current is None or mark.decision.confidence > current.decision.confidence:
            mark_by_key[key] = mark

    sold = 0
    earned = 0.0
    realized_pnl = 0.0
    for position in load_paper_positions(args.account_id):
        mark = mark_by_key.get((position.condition_id, position.side))
        row = {
            "type": "strategy_decision",
            "run_id": run_id,
            "created_at": now_utc(),
            "phase": "sell",
            "account_id": args.account_id,
            "condition_id": position.condition_id,
            "side": position.side,
            "strategy": "wise_exit",
            "params": strategy_params,
            "money_before": account_money_snapshot(args.account_id),
        }
        if mark is None or mark.status != "marked" or mark.current_bid is None:
            row.update({"action": "hold", "reason": "no current bid mark"})
            append_jsonl(args.strategy_log_path, row)
            continue
        shares_to_sell = round(position.shares * args.sell_fraction, 8)
        estimate = sell_estimate(
            shares=shares_to_sell,
            price=mark.current_bid,
            position_shares=position.shares,
            position_cost_basis=position.cost_basis,
            position_fees_paid=position.fees_paid,
            fee_rate_bps=args.fee_rate_bps,
        )
        score = (mark.current_bid - position.avg_price) * mark.decision.confidence
        profit_ok = estimate["return_pct"] >= args.sell_min_return
        score_ok = score >= args.sell_min_score
        stop_ok = estimate["return_pct"] <= args.sell_max_loss
        eligible = profit_ok or score_ok or stop_ok
        reason = (
            "profit target" if profit_ok else "score target" if score_ok else
            "stop loss" if stop_ok else "requirements not met"
        )
        row.update({
            "action": "sell" if eligible else "hold",
            "reason": reason,
            "current_bid": mark.current_bid,
            "confidence": mark.decision.confidence,
            "shares_to_sell": shares_to_sell,
            "score": score,
            **estimate,
        })
        if eligible and not args.dry_run:
            order = record_paper_sell(
                account_id=args.account_id,
                run_id=run_id,
                condition_id=position.condition_id,
                token_id=position.token_id,
                side=position.side,
                shares=shares_to_sell,
                price=mark.current_bid,
                event_id=position.event_id,
                category=position.category,
                question=position.question,
                fee_rate_bps=args.fee_rate_bps,
                decision_reason=f"wise maintainer sell: {reason}",
            )
            row["order_id"] = order.order_id
            _append_decision_log(args, {
                "type": "decision",
                "run_id": run_id,
                "created_at": now_utc(),
                "account_id": args.account_id,
                "condition_id": position.condition_id,
                "event_id": position.event_id,
                "category": position.category,
                "question": position.question,
                "action": "sell",
                "order_id": order.order_id,
                "status": order.status,
                "side": position.side,
                "token_id": position.token_id,
                "filled_notional": order.filled_notional,
                "filled_shares": order.filled_shares,
                "avg_price": order.avg_price,
                "fee": order.fee,
                "realized_pnl": estimate["realized_pnl"],
                "cash_earned": estimate["net_proceeds"],
                "reason": reason,
            })
            sold += 1
            earned += estimate["net_proceeds"]
            realized_pnl += estimate["realized_pnl"]
        row["money_after"] = account_money_snapshot(args.account_id)
        append_jsonl(args.strategy_log_path, row)
    return sold, earned, realized_pnl


def _buy_phase(args, run_id: str, strategy_params: dict) -> tuple[int, list[EvalRecord]]:
    scanner = Scanner(market_limit=args.scan_limit)
    candidates = scanner.scan()
    tradeable = [
        candidate for candidate in candidates
        if candidate.snapshot.spread <= args.max_spread
    ]
    if args.exclude_sports:
        tradeable = [
            candidate for candidate in tradeable
            if not _is_sports(candidate.market.question, candidate.market.category)
        ]
    tradeable = [
        candidate for candidate in tradeable
        if args.min_prob <= candidate.snapshot.midpoint <= args.max_prob
    ]
    targets = tradeable[:args.top]
    logger.info(f"Maintainer buy phase: {len(candidates)} scanned, {len(targets)} targets")

    parser = ResolutionParser()
    retriever = EvidenceRetriever()
    forecaster = LLMForecaster(
        max_evidence_items=args.forecast_evidence_items,
        max_tokens=args.forecast_max_tokens,
    )
    clob = ClobReadClient()
    eval_records: list[EvalRecord] = []
    buys = 0
    for rank, candidate in enumerate(targets, start=1):
        row = {
            "type": "strategy_decision",
            "run_id": run_id,
            "created_at": now_utc(),
            "phase": "buy",
            "rank": rank,
            "account_id": args.account_id,
            "condition_id": candidate.market.condition_id,
            "event_id": candidate.market.event_id,
            "category": candidate.market.category,
            "question": candidate.market.question,
            "strategy": "wise_entry",
            "params": strategy_params,
            "p_m_yes": candidate.snapshot.midpoint,
            "spread": candidate.snapshot.spread,
            "money_before": account_money_snapshot(args.account_id),
        }
        try:
            features = parser.parse(candidate.market)
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
            side = _side_choice(candidate, forecast, clob)
            row.update({
                "forecast_run_id": forecast_run_id,
                "p_f_yes": forecast.p_f,
                "confidence": forecast.confidence,
                "forecast_direction": forecast.forecast_direction,
                "model": forecast.model,
                "evidence_count": len(evidence),
                "reasoning": forecast.reasoning,
            })
            if side["status"] != "ok":
                row.update({"action": "skip_buy", "reason": side["reason"]})
                append_jsonl(args.strategy_log_path, row)
                continue
            net_edge = side["gross_edge"] - args.fee_rate_bps / 10_000 - args.slippage_bps / 10_000
            row.update({
                "side": side["side"],
                "token_id": side["token_id"],
                "best_ask": side["best_ask"],
                "side_fair_prob": side["side_fair_prob"],
                "gross_edge": side["gross_edge"],
                "net_edge": net_edge,
            })
            if side["gross_edge"] < args.min_edge:
                row.update({"action": "skip_buy", "reason": "edge below threshold"})
                append_jsonl(args.strategy_log_path, row)
                continue
            if net_edge < args.min_net_edge:
                row.update({"action": "skip_buy", "reason": "net edge below threshold"})
                append_jsonl(args.strategy_log_path, row)
                continue
            if forecast.confidence < args.min_confidence:
                row.update({"action": "skip_buy", "reason": "confidence below threshold"})
                append_jsonl(args.strategy_log_path, row)
                continue
            account = load_paper_account(args.account_id)
            if account is None:
                raise RuntimeError(f"paper account disappeared: {args.account_id}")
            notional, size_reason = _sized_notional(candidate, args, account, side["gross_edge"])
            if notional <= 0:
                row.update({"action": "skip_buy", "reason": size_reason})
                append_jsonl(args.strategy_log_path, row)
                continue
            fills, filled_notional, _ = simulate_buy(side["levels"], notional)
            if filled_notional < args.min_order_notional or not fills:
                row.update({"action": "skip_buy", "reason": "insufficient ask depth"})
                append_jsonl(args.strategy_log_path, row)
                continue
            if args.dry_run:
                row.update({"action": "buy_dry_run", "requested_notional": notional})
                append_jsonl(args.strategy_log_path, row)
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
            row.update({
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
            _append_decision_log(args, {
                "type": "decision",
                "run_id": run_id,
                "created_at": now_utc(),
                "rank": rank,
                "account_id": args.account_id,
                "condition_id": candidate.market.condition_id,
                "event_id": candidate.market.event_id,
                "category": candidate.market.category,
                "question": candidate.market.question,
                "action": "buy",
                "order_id": order.order_id,
                "status": order.status,
                "side": order.side,
                "token_id": order.token_id,
                "p_m_yes": candidate.snapshot.midpoint,
                "p_f_yes": forecast.p_f,
                "side_fair_prob": side["side_fair_prob"],
                "confidence": forecast.confidence,
                "forecast_direction": forecast.forecast_direction,
                "gross_edge": side["gross_edge"],
                "net_edge": net_edge,
                "filled_notional": order.filled_notional,
                "filled_shares": order.filled_shares,
                "avg_price": order.avg_price,
                "fee": order.fee,
                "reasoning": forecast.reasoning,
            })
            buys += 1
        except Exception as exc:
            row.update({"action": "error", "error": str(exc)})
            logger.exception(f"Maintainer buy decision failed for {candidate.market.condition_id}")
        row["money_after"] = account_money_snapshot(args.account_id)
        append_jsonl(args.strategy_log_path, row)
    return buys, eval_records


def _parse_args():
    parser = argparse.ArgumentParser(description="Maintain a BeatOdds paper account")
    parser.add_argument("--account-id", default=DEFAULT_ACCOUNT_ID)
    parser.add_argument("--account-name", default="BeatOdds Wise $1000 Paper Account")
    parser.add_argument("--initial-cash", type=float, default=1_000.0)
    parser.add_argument("--decision-log-path", type=Path, default=DEFAULT_DECISION_LOG_PATH)
    parser.add_argument("--strategy-log-path", type=Path, default=DEFAULT_STRATEGY_LOG_PATH)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--init-only", action="store_true")
    parser.add_argument("--sell-only", action="store_true")
    parser.add_argument("--buy-only", action="store_true")
    parser.add_argument("--top", type=int, default=6)
    parser.add_argument("--scan-limit", type=int, default=300)
    parser.add_argument("--exclude-sports", action="store_true", default=True)
    parser.add_argument("--include-sports", action="store_false", dest="exclude_sports")
    parser.add_argument("--min-prob", type=float, default=0.08)
    parser.add_argument("--max-prob", type=float, default=0.92)
    parser.add_argument("--max-spread", type=float, default=0.05)
    parser.add_argument("--min-edge", type=float, default=0.025)
    parser.add_argument("--min-net-edge", type=float, default=0.01)
    parser.add_argument("--min-confidence", type=float, default=0.25)
    parser.add_argument("--min-order-notional", type=float, default=3.0)
    parser.add_argument("--max-order-notional", type=float, default=25.0)
    parser.add_argument("--min-order-fraction", type=float, default=0.005)
    parser.add_argument("--max-order-fraction", type=float, default=0.025)
    parser.add_argument("--edge-size-multiplier", type=float, default=0.75)
    parser.add_argument("--max-market-exposure", type=float, default=60.0)
    parser.add_argument("--max-event-exposure", type=float, default=120.0)
    parser.add_argument("--max-category-exposure", type=float, default=300.0)
    parser.add_argument("--max-total-exposure", type=float, default=600.0)
    parser.add_argument("--min-cash-buffer", type=float, default=250.0)
    parser.add_argument("--fee-rate-bps", type=float, default=0.0)
    parser.add_argument("--slippage-bps", type=float, default=0.0)
    parser.add_argument("--sell-min-return", type=float, default=0.08)
    parser.add_argument("--sell-min-score", type=float, default=0.02)
    parser.add_argument("--sell-max-loss", type=float, default=-0.20)
    parser.add_argument("--sell-fraction", type=float, default=1.0)
    parser.add_argument("--sell-all-eligible", action="store_true", default=True)
    parser.add_argument("--evidence-results-per-query", type=int, default=6)
    parser.add_argument("--forecast-evidence-items", type=int, default=12)
    parser.add_argument("--forecast-max-tokens", type=int, default=512)
    args = parser.parse_args()
    if args.sell_only and args.buy_only:
        parser.error("choose only one of --sell-only or --buy-only")
    if args.init_only and (args.sell_only or args.buy_only):
        parser.error("--init-only cannot be combined with --sell-only or --buy-only")
    if args.sell_fraction <= 0 or args.sell_fraction > 1:
        parser.error("--sell-fraction must be in (0, 1]")
    return args


def main() -> None:
    args = _parse_args()
    cfg = get_settings()
    _ensure_account(args)
    run_id = str(uuid4())
    strategy_params = _strategy_params(args)
    append_jsonl(args.strategy_log_path, {
        "type": "strategy_run_start",
        "run_id": run_id,
        "created_at": now_utc(),
        "account_id": args.account_id,
        "strategy": "wise_maintainer",
        "params": strategy_params,
        "llm_backend": cfg.llm_backend,
        "dry_run": args.dry_run,
        "money": account_money_snapshot(args.account_id),
    })
    sold = 0
    earned = 0.0
    realized_pnl = 0.0
    buys = 0
    eval_records: list[EvalRecord] = []
    print(f"\nPAPER MAINTAINER RUN {run_id}")
    print(f"account={args.account_id} strategy_log={args.strategy_log_path}")
    if args.init_only:
        money = account_money_snapshot(args.account_id)
        append_jsonl(args.strategy_log_path, {
            "type": "strategy_run_end",
            "run_id": run_id,
            "created_at": now_utc(),
            "account_id": args.account_id,
            "strategy": "wise_maintainer",
            "init_only": True,
            "money": money,
        })
        print(
            f"account initialized. cash=${float(money['cash_balance'] or 0):.2f} "
            f"open_cost=${money['open_cost_basis']:.2f}"
        )
        return
    if not args.buy_only:
        sold, earned, realized_pnl = _sell_phase(args, run_id, strategy_params)
        print(f"sell phase: sold={sold} earned=${earned:.2f} realized_pnl=${realized_pnl:.2f}")
    if not args.sell_only:
        buys, eval_records = _buy_phase(args, run_id, strategy_params)
        print(f"buy phase: buys={buys}")
    if eval_records and not args.dry_run:
        save_eval_records(eval_records)
    money = account_money_snapshot(args.account_id)
    append_jsonl(args.strategy_log_path, {
        "type": "strategy_run_end",
        "run_id": run_id,
        "created_at": datetime.now().astimezone(),
        "account_id": args.account_id,
        "strategy": "wise_maintainer",
        "sold": sold,
        "cash_earned": earned,
        "realized_pnl": realized_pnl,
        "buys": buys,
        "money": money,
    })
    print(
        f"done. cash=${float(money['cash_balance'] or 0):.2f} "
        f"open_cost=${money['open_cost_basis']:.2f} positions={money['open_position_count']}"
    )


if __name__ == "__main__":
    main()
