from datetime import datetime, timezone

from beatodds.baselines.market_only import market_only_scores
from beatodds.calibrator.ranker import Ranker
from beatodds.common.types import CandidateMarket, MarketMeta, PriceSnapshot


def test_core_modules_import_and_score() -> None:
    market = MarketMeta(
        condition_id="0xabc",
        question="Will this smoke test pass?",
        token_yes_id="yes-token",
        token_no_id="no-token",
    )
    snapshot = PriceSnapshot(
        condition_id=market.condition_id,
        token_id=market.token_yes_id,
        snapshot_time=datetime.now(timezone.utc),
        midpoint=0.55,
        best_bid=0.54,
        best_ask=0.56,
        spread=0.02,
    )
    candidate = CandidateMarket(market=market, snapshot=snapshot)

    baseline = market_only_scores([candidate])
    assert baseline[0].p_f == snapshot.midpoint
    assert baseline[0].edge == 0.0

    ranked = Ranker().rank([candidate], forecasts={}, violations=[])
    assert ranked[0].edge_score.explanation == "market_only"
    assert ranked[0].market.condition_id == market.condition_id
