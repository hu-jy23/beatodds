from types import SimpleNamespace

import scripts.run_paper_maintainer as maintainer
from beatodds.evaluation.paper_strategy import sell_estimate, simulate_buy


def test_simulate_buy_consumes_visible_depth_by_notional() -> None:
    fills, notional, shares = simulate_buy([(0.2, 50.0), (0.25, 100.0)], 20.0)

    assert fills == [(0.2, 50.0), (0.25, 40.0)]
    assert notional == 20.0
    assert shares == 90.0


def test_sell_estimate_accounts_for_prior_and_exit_fees() -> None:
    estimate = sell_estimate(
        shares=50.0,
        price=0.5,
        position_shares=100.0,
        position_cost_basis=40.0,
        position_fees_paid=0.4,
        fee_rate_bps=100.0,
    )

    assert estimate["gross_proceeds"] == 25.0
    assert estimate["sell_fee"] == 0.25
    assert estimate["net_proceeds"] == 24.75
    assert estimate["realized_pnl"] == 4.55
    assert round(estimate["return_pct"], 6) == 0.225248


def test_sized_notional_counts_existing_topic_exposure_and_fees(monkeypatch) -> None:
    candidate = SimpleNamespace(
        market=SimpleNamespace(condition_id="topic-1", event_id="event-1", category="news")
    )
    args = SimpleNamespace(
        max_order_fraction=0.25,
        min_order_fraction=0.25,
        edge_size_multiplier=1.0,
        min_order_notional=0.1,
        fee_rate_bps=100.0,
    )
    account = SimpleNamespace(
        account_id="paper",
        cash_balance=1000.0,
        min_cash_buffer=0.0,
        max_order_notional=1000.0,
        max_market_exposure=60.0,
        max_event_exposure=1000.0,
        max_category_exposure=1000.0,
        max_total_exposure=1000.0,
    )
    monkeypatch.setattr(
        maintainer,
        "position_exposure",
        lambda account_id: {
            "total": 59.50,
            "market:topic-1": 59.50,
            "event:event-1": 59.50,
            "category:news": 59.50,
        },
    )

    notional, reason, context = maintainer._sized_notional(candidate, args, account, edge=0.5)

    assert notional == 0.49
    assert notional * 1.01 <= 0.50
    assert context["topic_exposure_before"] == 59.5
    assert context["topic_exposure_limit"] == 60.0
    assert "existing topic exposure" in reason
