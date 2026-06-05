from beatodds.common import config as config_module
from beatodds.evaluation.paper_store import (
    DEFAULT_ACCOUNT_ID,
    account_summary,
    create_paper_account,
    deposit_cash,
    ensure_default_paper_account,
    load_account_transactions,
    load_paper_account,
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
