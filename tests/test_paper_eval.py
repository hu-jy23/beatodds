import json

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
