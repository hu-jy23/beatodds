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
