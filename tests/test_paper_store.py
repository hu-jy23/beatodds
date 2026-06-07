from beatodds.common import config as config_module
from beatodds.evaluation.paper_store import (
    DEFAULT_ACCOUNT_ID,
    account_summary,
    create_paper_account,
    deposit_cash,
    ensure_default_paper_account,
    load_account_transactions,
    load_paper_account,
    load_paper_orders,
    load_paper_positions,
    record_paper_buy,
    record_paper_sell,
    release_reserved_cash,
    reserve_cash,
    update_risk_params,
    withdraw_cash,
)


def _reset_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    config_module._settings = None


def test_paper_account_create_and_default_idempotent(tmp_path, monkeypatch) -> None:
    _reset_settings(tmp_path, monkeypatch)

    account = ensure_default_paper_account(initial_cash=12_500.0)
    same = ensure_default_paper_account(initial_cash=99_000.0)

    assert account.account_id == DEFAULT_ACCOUNT_ID
    assert same.account_id == DEFAULT_ACCOUNT_ID
    assert same.initial_cash == 12_500.0
    assert same.cash_balance == 12_500.0
    assert same.sizing_mode == "all_in"
    assert same.order_fraction == 1.0
    assert same.fee_rate_bps == 0.0
    assert same.max_order_notional == 12_500.0

    summary = account_summary()
    assert summary["paper_accounts"] == 1
    assert summary["paper_account_transactions"] == 1
    assert summary["total_cash_balance"] == 12_500.0

    transactions = load_account_transactions(DEFAULT_ACCOUNT_ID)
    assert len(transactions) == 1
    assert transactions[0].transaction_type == "create"
    assert transactions[0].cash_after == 12_500.0

    config_module._settings = None


def test_paper_account_risk_params_and_cash_ledger(tmp_path, monkeypatch) -> None:
    _reset_settings(tmp_path, monkeypatch)
    create_paper_account(
        account_id="research",
        name="Research Account",
        initial_cash=1_000.0,
        max_order_notional=50.0,
    )

    updated = update_risk_params(
        "research",
        risk_profile="conservative",
        sizing_mode="fraction",
        order_fraction=0.25,
        auto_trade_enabled=True,
        max_order_notional=25.0,
        max_event_exposure=150.0,
        min_cash_buffer=100.0,
        fee_rate_bps=150.0,
    )
    assert updated.risk_profile == "conservative"
    assert updated.sizing_mode == "fraction"
    assert updated.order_fraction == 0.25
    assert updated.auto_trade_enabled is True
    assert updated.max_order_notional == 25.0
    assert updated.max_event_exposure == 150.0
    assert updated.min_cash_buffer == 100.0
    assert updated.fee_rate_bps == 150.0

    deposit = deposit_cash("research", 250.0, memo="top up")
    assert deposit.cash_before == 1_000.0
    assert deposit.cash_after == 1_250.0

    withdraw = withdraw_cash("research", 100.0, memo="rebalance out")
    assert withdraw.cash_after == 1_150.0

    reserve = reserve_cash("research", 300.0, memo="paper order reserve")
    assert reserve.cash_after == 850.0
    assert reserve.reserved_after == 300.0

    release = release_reserved_cash("research", 125.0, memo="partial release")
    assert release.cash_after == 975.0
    assert release.reserved_after == 175.0

    loaded = load_paper_account("research")
    assert loaded is not None
    assert loaded.cash_balance == 975.0
    assert loaded.reserved_cash == 175.0

    transactions = load_account_transactions("research", limit=10)
    assert [tx.transaction_type for tx in transactions[:4]] == [
        "release",
        "reserve",
        "withdraw",
        "deposit",
    ]

    summary = account_summary()
    assert summary["paper_accounts"] == 1
    assert summary["paper_account_transactions"] == 5
    assert summary["total_cash_balance"] == 975.0
    assert summary["total_reserved_cash"] == 175.0

    config_module._settings = None


def test_paper_account_rejects_negative_balances(tmp_path, monkeypatch) -> None:
    _reset_settings(tmp_path, monkeypatch)
    create_paper_account(account_id="risk-test", name="Risk Test", initial_cash=100.0)

    try:
        withdraw_cash("risk-test", 101.0)
    except ValueError as exc:
        assert "cash balance" in str(exc)
    else:
        raise AssertionError("withdraw_cash should reject negative cash")

    reserve_cash("risk-test", 50.0)
    try:
        release_reserved_cash("risk-test", 51.0)
    except ValueError as exc:
        assert "reserved cash" in str(exc)
    else:
        raise AssertionError("release_reserved_cash should reject negative reserved cash")

    loaded = load_paper_account("risk-test")
    assert loaded is not None
    assert loaded.cash_balance == 50.0
    assert loaded.reserved_cash == 50.0

    config_module._settings = None


def test_record_paper_buy_creates_order_transaction_and_position(tmp_path, monkeypatch) -> None:
    _reset_settings(tmp_path, monkeypatch)
    create_paper_account(
        account_id="live",
        name="Live Test",
        initial_cash=1_000.0,
        max_order_notional=100.0,
    )

    order = record_paper_buy(
        account_id="live",
        run_id="run-1",
        condition_id="cond-1",
        token_id="token-yes",
        side="YES",
        requested_notional=40.0,
        fill_levels=[(0.4, 50.0), (0.42, 47.61904762)],
        p_m_yes=0.39,
        p_f_yes=0.47,
        side_fair_prob=0.47,
        gross_edge=0.07,
        net_edge=0.06,
        confidence=0.5,
        event_id="event-1",
        category="Politics",
        question="Will test happen?",
        forecast_run_id="forecast-1",
        fee_rate_bps=100.0,
    )

    assert order.status == "filled"
    assert order.filled_notional == 40.0
    assert order.filled_shares == 97.61904762
    assert order.fee == 0.4

    account = load_paper_account("live")
    assert account is not None
    assert account.cash_balance == 959.6

    transactions = load_account_transactions("live", limit=5)
    assert transactions[0].transaction_type == "trade"
    assert transactions[0].cash_delta == -40.4
    assert transactions[0].ref_id == order.order_id

    orders = load_paper_orders("live")
    assert len(orders) == 1
    assert orders[0].order_id == order.order_id
    assert orders[0].forecast_run_id == "forecast-1"

    positions = load_paper_positions("live")
    assert len(positions) == 1
    assert positions[0].shares == 97.61904762
    assert positions[0].cost_basis == 40.0
    assert positions[0].fees_paid == 0.4

    record_paper_buy(
        account_id="live",
        run_id="run-2",
        condition_id="cond-1",
        token_id="token-yes",
        side="YES",
        requested_notional=10.0,
        fill_levels=[(0.5, 20.0)],
        p_m_yes=0.48,
        p_f_yes=0.56,
        side_fair_prob=0.56,
        gross_edge=0.06,
        net_edge=0.05,
        confidence=0.4,
    )
    positions = load_paper_positions("live")
    assert len(positions) == 1
    assert positions[0].shares == 117.61904762
    assert positions[0].cost_basis == 50.0

    config_module._settings = None


def test_record_paper_sell_credits_cash_and_reduces_position(tmp_path, monkeypatch) -> None:
    _reset_settings(tmp_path, monkeypatch)
    create_paper_account(
        account_id="seller",
        name="Seller Test",
        initial_cash=1_000.0,
        max_order_notional=100.0,
    )
    record_paper_buy(
        account_id="seller",
        run_id="run-buy",
        condition_id="cond-sell",
        token_id="token-yes",
        side="YES",
        requested_notional=40.0,
        fill_levels=[(0.4, 100.0)],
        p_m_yes=0.4,
        p_f_yes=0.5,
        side_fair_prob=0.5,
        gross_edge=0.1,
        net_edge=0.1,
        confidence=0.6,
        fee_rate_bps=100.0,
    )

    sell = record_paper_sell(
        account_id="seller",
        run_id="run-sell",
        condition_id="cond-sell",
        token_id="token-yes",
        side="YES",
        shares=40.0,
        price=0.5,
        fee_rate_bps=100.0,
        decision_reason="profit target",
    )

    assert sell.action == "sell"
    assert sell.status == "partial"
    assert sell.filled_notional == 20.0
    assert sell.fee == 0.2

    account = load_paper_account("seller")
    assert account is not None
    assert account.cash_balance == 979.4

    positions = load_paper_positions("seller")
    assert len(positions) == 1
    assert positions[0].shares == 60.0
    assert positions[0].cost_basis == 24.0
    assert positions[0].fees_paid == 0.24

    close = record_paper_sell(
        account_id="seller",
        run_id="run-sell",
        condition_id="cond-sell",
        side="YES",
        shares=60.0,
        price=0.55,
    )
    assert close.status == "filled"
    assert load_paper_positions("seller") == []

    transactions = load_account_transactions("seller", limit=5)
    assert transactions[0].cash_delta == 33.0
    assert transactions[1].cash_delta == 19.8

    config_module._settings = None
