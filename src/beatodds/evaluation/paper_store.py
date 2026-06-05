"""Paper trading account storage.

This module owns the account and cash-ledger foundation for simulated trading.
Orders, fills, positions, marks, and settlements should reference account_id
from this layer when they are implemented.
"""

from __future__ import annotations

import threading
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from beatodds.common.config import get_settings
from beatodds.common.types import PaperAccount, PaperAccountTransaction

DEFAULT_ACCOUNT_ID = "demo"
_SCHEMA_LOCK = threading.Lock()


def _db_path() -> Path:
    cfg = get_settings()
    path = Path(cfg.data_dir) / "eval.duckdb"
    path.parent.mkdir(parents=True, exist_ok=True)
    return path


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _db_time(value: datetime | None) -> datetime | None:
    normalized = _as_utc(value)
    return normalized.replace(tzinfo=None) if normalized else None


def _connect():
    import duckdb

    conn = duckdb.connect(str(_db_path()))
    with _SCHEMA_LOCK:
        ensure_schema(conn)
    return conn


def ensure_schema(conn) -> None:
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_accounts (
            account_id             TEXT PRIMARY KEY,
            name                   TEXT,
            base_currency          TEXT,
            initial_cash           DOUBLE,
            cash_balance           DOUBLE,
            reserved_cash          DOUBLE,
            status                 TEXT,
            risk_profile           TEXT,
            sizing_mode            TEXT,
            order_fraction         DOUBLE,
            auto_trade_enabled     BOOLEAN,
            max_order_notional     DOUBLE,
            max_market_exposure    DOUBLE,
            max_event_exposure     DOUBLE,
            max_category_exposure  DOUBLE,
            max_total_exposure     DOUBLE,
            min_cash_buffer        DOUBLE,
            fee_rate_bps           DOUBLE,
            slippage_bps           DOUBLE,
            created_at             TIMESTAMP,
            updated_at             TIMESTAMP,
            notes                  TEXT,
            icon_url               TEXT
        )
    """)
    _ensure_account_columns(conn)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS paper_account_transactions (
            transaction_id   TEXT PRIMARY KEY,
            account_id       TEXT,
            transaction_type TEXT,
            cash_delta       DOUBLE,
            reserved_delta   DOUBLE,
            cash_before      DOUBLE,
            cash_after       DOUBLE,
            reserved_before  DOUBLE,
            reserved_after   DOUBLE,
            ref_type         TEXT,
            ref_id           TEXT,
            memo             TEXT,
            created_at       TIMESTAMP
        )
    """)
    conn.commit()


def _ensure_account_columns(conn) -> None:
    columns = {row[1] for row in conn.execute("PRAGMA table_info('paper_accounts')").fetchall()}
    additions = {
        "sizing_mode": "TEXT DEFAULT 'all_in'",
        "order_fraction": "DOUBLE DEFAULT 1.0",
        "auto_trade_enabled": "BOOLEAN DEFAULT FALSE",
        "icon_url": "TEXT DEFAULT ''",
    }
    for name, spec in additions.items():
        if name not in columns:
            conn.execute(f"ALTER TABLE paper_accounts ADD COLUMN {name} {spec}")
    conn.execute("""
        UPDATE paper_accounts
        SET sizing_mode = 'all_in'
        WHERE sizing_mode IS NULL OR sizing_mode = ''
    """)
    conn.execute("UPDATE paper_accounts SET order_fraction = 1.0 WHERE order_fraction IS NULL")
    conn.execute("""
        UPDATE paper_accounts
        SET auto_trade_enabled = FALSE
        WHERE auto_trade_enabled IS NULL
    """)


def create_paper_account(
    account_id: str = DEFAULT_ACCOUNT_ID,
    name: str = "Demo Paper Account",
    initial_cash: float = 10_000.0,
    base_currency: str = "USD",
    icon_url: str = "",
    risk_profile: str = "demo",
    sizing_mode: str = "all_in",
    order_fraction: float = 1.0,
    auto_trade_enabled: bool = False,
    max_order_notional: float | None = None,
    max_market_exposure: float = 250.0,
    max_event_exposure: float = 500.0,
    max_category_exposure: float = 1000.0,
    max_total_exposure: float = 5000.0,
    min_cash_buffer: float = 0.0,
    fee_rate_bps: float = 0.0,
    slippage_bps: float = 0.0,
    notes: str = "",
    created_at: datetime | None = None,
) -> PaperAccount:
    """Create a paper account and record its opening cash ledger row."""
    if not account_id:
        raise ValueError("account_id is required")
    if initial_cash < 0:
        raise ValueError("initial_cash must be non-negative")
    _validate_sizing_mode(sizing_mode)
    _validate_fraction(order_fraction)
    max_order_notional = initial_cash if max_order_notional is None else max_order_notional
    created_at = created_at or _now()
    conn = _connect()
    existing = conn.execute(
        "SELECT account_id FROM paper_accounts WHERE account_id = ?",
        [account_id],
    ).fetchone()
    if existing:
        conn.close()
        raise ValueError(f"paper account already exists: {account_id}")

    conn.execute("""
        INSERT INTO paper_accounts (
            account_id, name, base_currency, initial_cash, cash_balance,
            reserved_cash, status, risk_profile, max_order_notional,
            max_market_exposure, max_event_exposure, max_category_exposure,
            max_total_exposure, min_cash_buffer, fee_rate_bps, slippage_bps,
            created_at, updated_at, notes, sizing_mode, order_fraction,
            auto_trade_enabled, icon_url
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        account_id,
        name,
        base_currency,
        initial_cash,
        initial_cash,
        0.0,
        "active",
        risk_profile,
        max_order_notional,
        max_market_exposure,
        max_event_exposure,
        max_category_exposure,
        max_total_exposure,
        min_cash_buffer,
        fee_rate_bps,
        slippage_bps,
        _db_time(created_at),
        _db_time(created_at),
        notes,
        sizing_mode,
        order_fraction,
        auto_trade_enabled,
        icon_url,
    ])
    _insert_transaction(
        conn,
        account_id=account_id,
        transaction_type="create",
        cash_delta=initial_cash,
        reserved_delta=0.0,
        cash_before=0.0,
        cash_after=initial_cash,
        reserved_before=0.0,
        reserved_after=0.0,
        memo="initial paper capital",
        created_at=created_at,
    )
    conn.close()
    loaded = load_paper_account(account_id)
    if loaded is None:
        raise RuntimeError(f"paper account creation failed: {account_id}")
    return loaded


def ensure_default_paper_account(initial_cash: float = 10_000.0) -> PaperAccount:
    account = load_paper_account(DEFAULT_ACCOUNT_ID)
    if account:
        return account
    return create_paper_account(initial_cash=initial_cash)


def load_paper_account(account_id: str = DEFAULT_ACCOUNT_ID) -> PaperAccount | None:
    conn = _connect()
    row = conn.execute("""
        SELECT account_id, name, base_currency, initial_cash, cash_balance,
               reserved_cash, status, risk_profile, max_order_notional,
               max_market_exposure, max_event_exposure, max_category_exposure,
               max_total_exposure, min_cash_buffer, fee_rate_bps, slippage_bps,
               created_at, updated_at, notes, sizing_mode, order_fraction,
               auto_trade_enabled, icon_url
        FROM paper_accounts
        WHERE account_id = ?
    """, [account_id]).fetchone()
    conn.close()
    return _account_from_row(row) if row else None


def load_paper_accounts(limit: int = 50) -> list[PaperAccount]:
    conn = _connect()
    rows = conn.execute("""
        SELECT account_id, name, base_currency, initial_cash, cash_balance,
               reserved_cash, status, risk_profile, max_order_notional,
               max_market_exposure, max_event_exposure, max_category_exposure,
               max_total_exposure, min_cash_buffer, fee_rate_bps, slippage_bps,
               created_at, updated_at, notes, sizing_mode, order_fraction,
               auto_trade_enabled, icon_url
        FROM paper_accounts
        ORDER BY created_at DESC
        LIMIT ?
    """, [limit]).fetchall()
    conn.close()
    return [_account_from_row(row) for row in rows]


def update_risk_params(
    account_id: str = DEFAULT_ACCOUNT_ID,
    *,
    risk_profile: str | None = None,
    sizing_mode: str | None = None,
    order_fraction: float | None = None,
    auto_trade_enabled: bool | None = None,
    max_order_notional: float | None = None,
    max_market_exposure: float | None = None,
    max_event_exposure: float | None = None,
    max_category_exposure: float | None = None,
    max_total_exposure: float | None = None,
    min_cash_buffer: float | None = None,
    fee_rate_bps: float | None = None,
    slippage_bps: float | None = None,
    status: str | None = None,
    notes: str | None = None,
    updated_at: datetime | None = None,
) -> PaperAccount:
    """Update paper account risk controls and return the updated account."""
    account = load_paper_account(account_id)
    if account is None:
        raise ValueError(f"paper account not found: {account_id}")

    updates = {
        "risk_profile": risk_profile,
        "sizing_mode": sizing_mode,
        "order_fraction": order_fraction,
        "auto_trade_enabled": auto_trade_enabled,
        "max_order_notional": max_order_notional,
        "max_market_exposure": max_market_exposure,
        "max_event_exposure": max_event_exposure,
        "max_category_exposure": max_category_exposure,
        "max_total_exposure": max_total_exposure,
        "min_cash_buffer": min_cash_buffer,
        "fee_rate_bps": fee_rate_bps,
        "slippage_bps": slippage_bps,
        "status": status,
        "notes": notes,
    }
    values = {key: value for key, value in updates.items() if value is not None}
    if not values:
        return account
    for key, value in values.items():
        if key == "sizing_mode":
            _validate_sizing_mode(str(value))
        elif key == "order_fraction":
            _validate_fraction(float(value))
        elif key not in {"status", "risk_profile", "notes", "auto_trade_enabled"}:
            _require_non_negative(float(value), key)
    if status is not None and status not in {"active", "paused", "closed"}:
        raise ValueError("status must be active, paused, or closed")

    updated_at = updated_at or _now()
    assignments = ", ".join(f"{key} = ?" for key in values)
    params = [*values.values(), _db_time(updated_at), account_id]
    conn = _connect()
    conn.execute(
        f"""
        UPDATE paper_accounts
        SET {assignments}, updated_at = ?
        WHERE account_id = ?
        """,
        params,
    )
    conn.close()
    loaded = load_paper_account(account_id)
    if loaded is None:
        raise RuntimeError(f"paper account disappeared: {account_id}")
    return loaded


def update_account_profile(
    account_id: str = DEFAULT_ACCOUNT_ID,
    *,
    name: str | None = None,
    icon_url: str | None = None,
    notes: str | None = None,
    updated_at: datetime | None = None,
) -> PaperAccount:
    account = load_paper_account(account_id)
    if account is None:
        raise ValueError(f"paper account not found: {account_id}")
    values = {
        "name": name.strip() if name is not None else None,
        "icon_url": icon_url.strip() if icon_url is not None else None,
        "notes": notes if notes is not None else None,
    }
    values = {key: value for key, value in values.items() if value is not None}
    if not values:
        return account
    if values.get("name") == "":
        raise ValueError("name is required")
    updated_at = updated_at or _now()
    assignments = ", ".join(f"{key} = ?" for key in values)
    conn = _connect()
    conn.execute(
        f"""
        UPDATE paper_accounts
        SET {assignments}, updated_at = ?
        WHERE account_id = ?
        """,
        [*values.values(), _db_time(updated_at), account_id],
    )
    conn.close()
    loaded = load_paper_account(account_id)
    if loaded is None:
        raise RuntimeError(f"paper account disappeared: {account_id}")
    return loaded


def deposit_cash(
    account_id: str,
    amount: float,
    memo: str = "",
    ref_type: str = "",
    ref_id: str = "",
) -> PaperAccountTransaction:
    _require_positive(amount, "amount")
    return _apply_account_transaction(
        account_id=account_id,
        transaction_type="deposit",
        cash_delta=amount,
        reserved_delta=0.0,
        memo=memo,
        ref_type=ref_type,
        ref_id=ref_id,
    )


def withdraw_cash(
    account_id: str,
    amount: float,
    memo: str = "",
    ref_type: str = "",
    ref_id: str = "",
) -> PaperAccountTransaction:
    _require_positive(amount, "amount")
    return _apply_account_transaction(
        account_id=account_id,
        transaction_type="withdraw",
        cash_delta=-amount,
        reserved_delta=0.0,
        memo=memo,
        ref_type=ref_type,
        ref_id=ref_id,
    )


def reserve_cash(
    account_id: str,
    amount: float,
    memo: str = "",
    ref_type: str = "",
    ref_id: str = "",
) -> PaperAccountTransaction:
    _require_positive(amount, "amount")
    return _apply_account_transaction(
        account_id=account_id,
        transaction_type="reserve",
        cash_delta=-amount,
        reserved_delta=amount,
        memo=memo,
        ref_type=ref_type,
        ref_id=ref_id,
    )


def release_reserved_cash(
    account_id: str,
    amount: float,
    memo: str = "",
    ref_type: str = "",
    ref_id: str = "",
) -> PaperAccountTransaction:
    _require_positive(amount, "amount")
    return _apply_account_transaction(
        account_id=account_id,
        transaction_type="release",
        cash_delta=amount,
        reserved_delta=-amount,
        memo=memo,
        ref_type=ref_type,
        ref_id=ref_id,
    )


def adjust_cash(
    account_id: str,
    cash_delta: float,
    memo: str = "",
    ref_type: str = "",
    ref_id: str = "",
) -> PaperAccountTransaction:
    if cash_delta == 0:
        raise ValueError("cash_delta must be non-zero")
    return _apply_account_transaction(
        account_id=account_id,
        transaction_type="adjust",
        cash_delta=cash_delta,
        reserved_delta=0.0,
        memo=memo,
        ref_type=ref_type,
        ref_id=ref_id,
    )


def load_account_transactions(
    account_id: str = DEFAULT_ACCOUNT_ID,
    limit: int = 50,
) -> list[PaperAccountTransaction]:
    conn = _connect()
    rows = conn.execute("""
        SELECT transaction_id, account_id, transaction_type, cash_delta,
               reserved_delta, cash_before, cash_after, reserved_before,
               reserved_after, ref_type, ref_id, memo, created_at
        FROM paper_account_transactions
        WHERE account_id = ?
        ORDER BY created_at DESC
        LIMIT ?
    """, [account_id, limit]).fetchall()
    conn.close()
    return [_transaction_from_row(row) for row in rows]


def account_summary() -> dict:
    conn = _connect()
    accounts = conn.execute("SELECT COUNT(*) FROM paper_accounts").fetchone()[0]
    transactions = conn.execute(
        "SELECT COUNT(*) FROM paper_account_transactions"
    ).fetchone()[0]
    cash = conn.execute(
        "SELECT COALESCE(SUM(cash_balance), 0), COALESCE(SUM(reserved_cash), 0) "
        "FROM paper_accounts"
    ).fetchone()
    conn.close()
    return {
        "paper_accounts": accounts,
        "paper_account_transactions": transactions,
        "total_cash_balance": float(cash[0] or 0),
        "total_reserved_cash": float(cash[1] or 0),
    }


def _apply_account_transaction(
    account_id: str,
    transaction_type: str,
    cash_delta: float,
    reserved_delta: float,
    memo: str = "",
    ref_type: str = "",
    ref_id: str = "",
    created_at: datetime | None = None,
) -> PaperAccountTransaction:
    created_at = created_at or _now()
    conn = _connect()
    row = conn.execute("""
        SELECT cash_balance, reserved_cash
        FROM paper_accounts
        WHERE account_id = ?
    """, [account_id]).fetchone()
    if row is None:
        conn.close()
        raise ValueError(f"paper account not found: {account_id}")
    cash_before = float(row[0] or 0)
    reserved_before = float(row[1] or 0)
    cash_after = cash_before + cash_delta
    reserved_after = reserved_before + reserved_delta
    if cash_after < 0:
        conn.close()
        raise ValueError("cash balance cannot go negative")
    if reserved_after < 0:
        conn.close()
        raise ValueError("reserved cash cannot go negative")

    conn.execute("""
        UPDATE paper_accounts
        SET cash_balance = ?, reserved_cash = ?, updated_at = ?
        WHERE account_id = ?
    """, [_round_cash(cash_after), _round_cash(reserved_after), _db_time(created_at), account_id])
    transaction = _insert_transaction(
        conn,
        account_id=account_id,
        transaction_type=transaction_type,
        cash_delta=cash_delta,
        reserved_delta=reserved_delta,
        cash_before=cash_before,
        cash_after=cash_after,
        reserved_before=reserved_before,
        reserved_after=reserved_after,
        ref_type=ref_type,
        ref_id=ref_id,
        memo=memo,
        created_at=created_at,
    )
    conn.close()
    return transaction


def _insert_transaction(
    conn,
    account_id: str,
    transaction_type: str,
    cash_delta: float,
    reserved_delta: float,
    cash_before: float,
    cash_after: float,
    reserved_before: float,
    reserved_after: float,
    ref_type: str = "",
    ref_id: str = "",
    memo: str = "",
    created_at: datetime | None = None,
) -> PaperAccountTransaction:
    created_at = created_at or _now()
    transaction = PaperAccountTransaction(
        transaction_id=str(uuid4()),
        account_id=account_id,
        transaction_type=transaction_type,
        cash_delta=_round_cash(cash_delta),
        reserved_delta=_round_cash(reserved_delta),
        cash_before=_round_cash(cash_before),
        cash_after=_round_cash(cash_after),
        reserved_before=_round_cash(reserved_before),
        reserved_after=_round_cash(reserved_after),
        ref_type=ref_type,
        ref_id=ref_id,
        memo=memo,
        created_at=_as_utc(created_at),
    )
    conn.execute("""
        INSERT INTO paper_account_transactions (
            transaction_id, account_id, transaction_type, cash_delta,
            reserved_delta, cash_before, cash_after, reserved_before,
            reserved_after, ref_type, ref_id, memo, created_at
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
    """, [
        transaction.transaction_id,
        transaction.account_id,
        transaction.transaction_type,
        transaction.cash_delta,
        transaction.reserved_delta,
        transaction.cash_before,
        transaction.cash_after,
        transaction.reserved_before,
        transaction.reserved_after,
        transaction.ref_type,
        transaction.ref_id,
        transaction.memo,
        _db_time(transaction.created_at),
    ])
    conn.commit()
    return transaction


def _account_from_row(row) -> PaperAccount:
    return PaperAccount(
        account_id=row[0],
        name=row[1],
        icon_url=row[22] or "",
        base_currency=row[2],
        initial_cash=float(row[3] or 0),
        cash_balance=float(row[4] or 0),
        reserved_cash=float(row[5] or 0),
        status=row[6],
        risk_profile=row[7],
        max_order_notional=float(row[8] or 0),
        max_market_exposure=float(row[9] or 0),
        max_event_exposure=float(row[10] or 0),
        max_category_exposure=float(row[11] or 0),
        max_total_exposure=float(row[12] or 0),
        min_cash_buffer=float(row[13] or 0),
        fee_rate_bps=float(row[14] or 0),
        slippage_bps=float(row[15] or 0),
        created_at=_as_utc(row[16]),
        updated_at=_as_utc(row[17]),
        notes=row[18] or "",
        sizing_mode=row[19] or "all_in",
        order_fraction=float(row[20] if row[20] is not None else 1.0),
        auto_trade_enabled=bool(row[21]),
    )


def _transaction_from_row(row) -> PaperAccountTransaction:
    return PaperAccountTransaction(
        transaction_id=row[0],
        account_id=row[1],
        transaction_type=row[2],
        cash_delta=float(row[3] or 0),
        reserved_delta=float(row[4] or 0),
        cash_before=float(row[5] or 0),
        cash_after=float(row[6] or 0),
        reserved_before=float(row[7] or 0),
        reserved_after=float(row[8] or 0),
        ref_type=row[9] or "",
        ref_id=row[10] or "",
        memo=row[11] or "",
        created_at=_as_utc(row[12]),
    )


def _require_positive(value: float, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _require_non_negative(value: float, name: str) -> None:
    if value < 0:
        raise ValueError(f"{name} must be non-negative")


def _validate_sizing_mode(value: str) -> None:
    if value not in {"all_in", "fixed", "fraction"}:
        raise ValueError("sizing_mode must be all_in, fixed, or fraction")


def _validate_fraction(value: float) -> None:
    if value <= 0 or value > 1:
        raise ValueError("order_fraction must be in (0, 1]")


def _round_cash(value: float) -> float:
    return round(float(value), 8)
