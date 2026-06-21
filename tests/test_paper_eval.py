import json

import duckdb

from beatodds.evaluation.paper_eval import (
    load_paper_decisions,
    mark_decisions_to_market,
    paper_mark_summary,
    select_decisions_by_confidence,
)


class FakeClob:
    def __init__(self, bids_by_token: dict[str, float]):
        self.bids_by_token = bids_by_token

    def get_order_book(self, token_id: str):
        bid = self.bids_by_token.get(token_id)
        if bid is None:
            return {"bids": [], "asks": []}
        return {
            "bids": [{"price": "0.01"}, {"price": str(bid)}],
            "asks": [{"price": "0.99"}, {"price": str(min(0.99, bid + 0.02))}],
        }


def _write_jsonl(path, rows) -> None:
    path.write_text(
        "".join(json.dumps(row) + "\n" for row in rows),
        encoding="utf-8",
    )


def test_selects_top_k_buy_decisions_by_confidence(tmp_path) -> None:
    log_path = tmp_path / "paper_decisions.jsonl"
    _write_jsonl(log_path, [
        {"type": "run_start", "run_id": "run-1"},
        {
            "type": "decision",
            "action": "buy",
            "order_id": "low",
            "run_id": "run-1",
            "account_id": "paper",
            "condition_id": "cond-low",
            "question": "Low confidence?",
            "side": "YES",
            "token_id": "token-low",
            "confidence": 0.2,
            "filled_notional": 20.0,
            "filled_shares": 100.0,
            "avg_price": 0.2,
        },
        {
            "type": "decision",
            "action": "skip",
            "confidence": 0.99,
        },
        {
            "type": "decision",
            "action": "buy",
            "order_id": "high",
            "run_id": "run-1",
            "account_id": "paper",
            "condition_id": "cond-high",
            "question": "High confidence?",
            "side": "NO",
            "token_id": "token-high",
            "confidence": 0.8,
            "filled_notional": 10.0,
            "filled_shares": 20.0,
            "avg_price": 0.5,
        },
    ])

    decisions = load_paper_decisions(log_path, account_id="paper")
    selected = select_decisions_by_confidence(decisions, top_k=1)

    assert len(decisions) == 2
    assert selected[0].order_id == "high"


def test_marks_selected_decisions_to_current_bid(tmp_path) -> None:
    log_path = tmp_path / "paper_decisions.jsonl"
    _write_jsonl(log_path, [
        {
            "type": "decision",
            "action": "buy",
            "order_id": "winner",
            "run_id": "run-1",
            "account_id": "paper",
            "condition_id": "cond-winner",
            "question": "Winner?",
            "side": "YES",
            "token_id": "token-winner",
            "confidence": 0.7,
            "filled_notional": 20.0,
            "filled_shares": 100.0,
            "fee": 1.0,
            "avg_price": 0.2,
        },
        {
            "type": "decision",
            "action": "buy",
            "order_id": "loser",
            "run_id": "run-1",
            "account_id": "paper",
            "condition_id": "cond-loser",
            "question": "Loser?",
            "side": "NO",
            "token_id": "token-loser",
            "confidence": 0.6,
            "filled_notional": 50.0,
            "filled_shares": 100.0,
            "avg_price": 0.5,
        },
    ])

    decisions = select_decisions_by_confidence(load_paper_decisions(log_path), top_k=None)
    marks = mark_decisions_to_market(
        decisions,
        clob=FakeClob({"token-winner": 0.3, "token-loser": 0.4}),
    )
    summary = paper_mark_summary(marks)

    assert marks[0].decision.order_id == "winner"
    assert marks[0].current_value == 30.0
    assert marks[0].pnl == 9.0
    assert marks[1].pnl == -10.0
    assert summary["marked"] == 2
    assert summary["pnl"] == -1.0
    assert all(mark.mark_source == "live_clob" for mark in marks)


def test_falls_back_to_latest_exact_token_workflow_quote(tmp_path) -> None:
    log_path = tmp_path / "paper_decisions.jsonl"
    _write_jsonl(log_path, [{
        "type": "decision",
        "action": "buy",
        "order_id": "history",
        "run_id": "run-1",
        "account_id": "paper",
        "condition_id": "cond-history",
        "question": "History?",
        "side": "YES",
        "token_id": "token-history",
        "confidence": 0.7,
        "filled_notional": 20.0,
        "filled_shares": 100.0,
        "avg_price": 0.2,
    }])
    conn = duckdb.connect(str(tmp_path / "eval.duckdb"))
    conn.execute(
        """
        CREATE TABLE market_snapshots (
            condition_id VARCHAR,
            token_id VARCHAR,
            snapshot_time TIMESTAMP,
            best_bid DOUBLE,
            best_ask DOUBLE
        )
        """
    )
    conn.executemany(
        "INSERT INTO market_snapshots VALUES (?, ?, ?, ?, ?)",
        [
            ("cond-history", "token-history", "2026-06-10 10:00:00", 0.31, 0.33),
            ("cond-history", "other-token", "2026-06-12 10:00:00", 0.90, 0.91),
            ("cond-history", "token-history", "2026-06-11 10:00:00", 0.35, 0.37),
        ],
    )
    conn.close()

    decisions = load_paper_decisions(log_path)
    marks = mark_decisions_to_market(
        decisions,
        clob=FakeClob({}),
        data_dir=tmp_path,
    )

    assert marks[0].status == "marked"
    assert marks[0].current_bid == 0.35
    assert marks[0].current_ask == 0.37
    assert marks[0].current_value == 35.0
    assert marks[0].pnl == 15.0
    assert marks[0].mark_source == "workflow_history"
    assert marks[0].quote_time is not None


def test_history_fallback_does_not_use_other_token(tmp_path) -> None:
    log_path = tmp_path / "paper_decisions.jsonl"
    _write_jsonl(log_path, [{
        "type": "decision",
        "action": "buy",
        "condition_id": "cond-history",
        "side": "NO",
        "token_id": "missing-token",
        "filled_notional": 10.0,
        "filled_shares": 20.0,
    }])
    conn = duckdb.connect(str(tmp_path / "eval.duckdb"))
    conn.execute(
        """
        CREATE TABLE market_snapshots (
            condition_id VARCHAR,
            token_id VARCHAR,
            snapshot_time TIMESTAMP,
            best_bid DOUBLE,
            best_ask DOUBLE
        )
        """
    )
    conn.execute(
        "INSERT INTO market_snapshots VALUES (?, ?, ?, ?, ?)",
        ["cond-history", "yes-token", "2026-06-11 10:00:00", 0.8, 0.82],
    )
    conn.close()

    marks = mark_decisions_to_market(
        load_paper_decisions(log_path),
        clob=FakeClob({}),
        data_dir=tmp_path,
    )

    assert marks[0].status == "missing_bid"
    assert marks[0].current_bid is None


def test_falls_back_to_newest_matching_report_quote(tmp_path) -> None:
    log_path = tmp_path / "paper_decisions.jsonl"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    _write_jsonl(log_path, [{
        "type": "decision",
        "action": "buy",
        "order_id": "report-history",
        "account_id": "paper",
        "condition_id": "cond-report",
        "question": "Report history?",
        "side": "NO",
        "token_id": "token-report",
        "filled_notional": 20.0,
        "filled_shares": 50.0,
    }])
    older = {
        "generated_at": "2026-06-10T10:00:00+00:00",
        "account_id": "paper",
        "marks": [{
            "account_id": "paper",
            "condition_id": "cond-report",
            "side": "NO",
            "token_id": "token-report",
            "current_bid": 0.41,
            "current_ask": 0.43,
            "marked_at": "2026-06-10T09:59:00+00:00",
        }],
    }
    newer = {
        "generated_at": "2026-06-11T10:00:00+00:00",
        "account_id": "paper",
        "marks": [{
            "account_id": "paper",
            "condition_id": "cond-report",
            "side": "NO",
            "token_id": "token-report",
            "current_bid": 0.47,
            "current_ask": 0.49,
            "marked_at": "2026-06-11T09:59:00+00:00",
        }],
    }
    (report_dir / "paper_eval_top_5_old.json").write_text(
        json.dumps(older),
        encoding="utf-8",
    )
    (report_dir / "paper_eval_top_5_new.json").write_text(
        json.dumps(newer),
        encoding="utf-8",
    )

    marks = mark_decisions_to_market(
        load_paper_decisions(log_path),
        clob=FakeClob({}),
        data_dir=tmp_path,
        report_dir=report_dir,
    )

    assert marks[0].status == "marked"
    assert marks[0].current_bid == 0.47
    assert marks[0].current_ask == 0.49
    assert marks[0].mark_source == "report_history"
    assert marks[0].current_value == 23.5
    assert marks[0].pnl == 3.5


def test_report_fallback_requires_exact_account_side_and_token(tmp_path) -> None:
    log_path = tmp_path / "paper_decisions.jsonl"
    report_dir = tmp_path / "reports"
    report_dir.mkdir()
    _write_jsonl(log_path, [{
        "type": "decision",
        "action": "buy",
        "account_id": "paper",
        "condition_id": "cond-report",
        "side": "YES",
        "token_id": "yes-token",
        "filled_notional": 10.0,
        "filled_shares": 20.0,
    }])
    payload = {
        "generated_at": "2026-06-11T10:00:00+00:00",
        "account_id": "paper",
        "marks": [
            {
                "account_id": "other-account",
                "condition_id": "cond-report",
                "side": "YES",
                "token_id": "yes-token",
                "current_bid": 0.9,
            },
            {
                "account_id": "paper",
                "condition_id": "cond-report",
                "side": "NO",
                "token_id": "no-token",
                "current_bid": 0.8,
            },
        ],
    }
    (report_dir / "paper_eval_top_5.json").write_text(
        json.dumps(payload),
        encoding="utf-8",
    )

    marks = mark_decisions_to_market(
        load_paper_decisions(log_path),
        clob=FakeClob({}),
        data_dir=tmp_path,
        report_dir=report_dir,
    )

    assert marks[0].status == "missing_bid"
    assert marks[0].current_bid is None
