from datetime import datetime, timezone

from beatodds.common import config as config_module
from beatodds.common.types import (
    CandidateMarket,
    EvidenceItem,
    ForecastResult,
    MarketMeta,
    PriceSnapshot,
    ResolutionFeatures,
)
from beatodds.evaluation.workflow_store import (
    load_due_markets,
    load_evidence_for_run,
    load_forecast_runs,
    load_market_snapshots,
    load_resolution_features,
    load_tracked_market,
    load_tracked_markets,
    mark_outcome,
    save_candidate,
    save_forecast_run,
    workflow_summary,
)


def _reset_settings(tmp_path, monkeypatch) -> None:
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("WORKFLOW_RECORDS_DIR", str(tmp_path / "workflow_records"))
    config_module._settings = None


def _candidate() -> CandidateMarket:
    market = MarketMeta(
        condition_id="0xworkflow",
        question="Will the workflow store persist this market?",
        description="Test market description",
        resolution_text="Resolves YES if the test passes.",
        category="test",
        slug="workflow-store-test",
        event_id="event-1",
        token_yes_id="yes-token",
        token_no_id="no-token",
        outcomes=["YES", "NO"],
        close_time=datetime(2026, 6, 1, tzinfo=timezone.utc),
        volume_24h=1000.0,
        liquidity=500.0,
    )
    snapshot = PriceSnapshot(
        condition_id=market.condition_id,
        token_id=market.token_yes_id,
        snapshot_time=datetime(2026, 5, 25, 10, 0, tzinfo=timezone.utc),
        midpoint=0.42,
        best_bid=0.41,
        best_ask=0.43,
        spread=0.02,
        volume_24h=1000.0,
    )
    return CandidateMarket(
        market=market,
        snapshot=snapshot,
        scan_flags=["test"],
        priority_score=1.5,
    )


def _features(condition_id: str) -> ResolutionFeatures:
    return ResolutionFeatures(
        condition_id=condition_id,
        condition_type="event_occurrence",
        event_type="diplomacy_trade",
        china_relevance="high",
        key_entities=["Workflow Store"],
        search_queries=["workflow store test evidence"],
        geography=["China"],
        resolution_source_hint="Ministry of Commerce",
        source_routing_hints=["mofcom.gov.cn"],
        has_explicit_deadline=True,
        deadline_date=datetime(2026, 6, 1, tzinfo=timezone.utc),
        oracle_type="UMA",
        exception_clauses=["No exceptions"],
        ambiguity_score=0.1,
        risk_flags=["test_flag"],
        parsed_at=datetime(2026, 5, 25, 10, 1, tzinfo=timezone.utc),
    )


def _evidence() -> list[EvidenceItem]:
    return [
        EvidenceItem(
            query="workflow store test evidence",
            title="Workflow store evidence",
            summary="A short evidence summary.",
            url="https://example.com/workflow",
            source="example.com",
            published_at=datetime(2026, 5, 25, 9, 30, tzinfo=timezone.utc),
            retrieved_at=datetime(2026, 5, 25, 10, 2, tzinfo=timezone.utc),
            relevance_score=0.9,
            provider="mock",
            source_type="central_official",
            direction="neutral",
            strength=0.2,
            resolution_relevance=0.7,
            reliability_prior=0.9,
            dedupe_key="https://example.com/workflow",
            raw_metadata={"route": "china_site_query"},
        )
    ]


def test_workflow_store_tracks_market_and_snapshots(tmp_path, monkeypatch) -> None:
    _reset_settings(tmp_path, monkeypatch)
    candidate = _candidate()

    save_candidate(candidate)
    save_candidate(candidate)

    summary = workflow_summary()
    assert summary["tracked_markets"] == 1
    assert summary["market_snapshots"] == 1

    tracked = load_tracked_markets()
    assert tracked[0]["condition_id"] == candidate.market.condition_id
    assert tracked[0]["tracking_status"] == "tracking"

    tracked_one = load_tracked_market(candidate.market.condition_id)
    assert tracked_one is not None
    assert tracked_one["question"] == candidate.market.question

    snapshots = load_market_snapshots(candidate.market.condition_id)
    assert len(snapshots) == 1
    assert snapshots[0]["midpoint"] == 0.42
    assert snapshots[0]["scan_flags"] == ["test"]

    config_module._settings = None


def test_workflow_store_forecast_evidence_and_resolution(tmp_path, monkeypatch) -> None:
    _reset_settings(tmp_path, monkeypatch)
    candidate = _candidate()
    features = _features(candidate.market.condition_id)
    evidence = _evidence()
    forecast = ForecastResult(
        condition_id=candidate.market.condition_id,
        p_f=0.55,
        confidence=0.7,
        evidence_items=evidence,
        reasoning="Evidence shifts the estimate upward.",
        frozen_at=datetime(2026, 5, 25, 10, 2, tzinfo=timezone.utc),
        model="test-model",
    )

    run_id = save_forecast_run(
        candidate=candidate,
        features=features,
        evidence=evidence,
        forecast=forecast,
        evidence_frozen_at=forecast.frozen_at,
    )

    summary = workflow_summary()
    assert summary == {
        "tracked_markets": 1,
        "market_snapshots": 1,
        "forecast_runs": 1,
        "evidence_items": 1,
        "outcomes": 0,
    }

    runs = load_forecast_runs(candidate.market.condition_id)
    assert runs[0]["run_id"] == run_id
    assert round(runs[0]["edge"], 2) == 0.13
    assert runs[0]["signal_type"] == "search_only_llm"

    loaded_features = load_resolution_features(candidate.market.condition_id)
    assert loaded_features is not None
    assert loaded_features.search_queries == features.search_queries
    assert loaded_features.event_type == "diplomacy_trade"
    assert loaded_features.china_relevance == "high"
    assert loaded_features.source_routing_hints == ["mofcom.gov.cn"]

    loaded_evidence = load_evidence_for_run(run_id)
    assert len(loaded_evidence) == 1
    assert loaded_evidence[0].query == evidence[0].query
    assert loaded_evidence[0].url == evidence[0].url
    assert loaded_evidence[0].provider == "mock"
    assert loaded_evidence[0].source_type == "central_official"
    assert loaded_evidence[0].raw_metadata == {"route": "china_site_query"}

    record_dir = tmp_path / "workflow_records"
    json_records = list(record_dir.glob(f"*{run_id[:8]}*.json"))
    md_records = list(record_dir.glob(f"*{run_id[:8]}*.md"))
    assert len(json_records) == 1
    assert len(md_records) == 1
    assert run_id in json_records[0].read_text(encoding="utf-8")
    assert "Workflow store evidence" in md_records[0].read_text(encoding="utf-8")

    assert mark_outcome(candidate.market.condition_id, 1.0, source="manual") == 1
    assert workflow_summary()["outcomes"] == 1
    assert load_tracked_markets()[0]["resolved_outcome"] == 1.0

    config_module._settings = None


def test_workflow_store_due_markets(tmp_path, monkeypatch) -> None:
    _reset_settings(tmp_path, monkeypatch)
    candidate = _candidate()
    features = _features(candidate.market.condition_id)
    evidence = _evidence()
    forecast = ForecastResult(
        condition_id=candidate.market.condition_id,
        p_f=0.55,
        confidence=0.7,
        evidence_items=evidence,
        reasoning="Evidence shifts the estimate upward.",
        frozen_at=datetime(2026, 5, 25, 10, 2, tzinfo=timezone.utc),
        model="test-model",
    )

    save_candidate(candidate)
    due = load_due_markets(
        stale_after_hours=24.0,
        now=datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc),
    )
    assert len(due) == 1
    assert due[0]["condition_id"] == candidate.market.condition_id
    assert due[0]["due_reason"] == "never_forecasted"

    save_forecast_run(
        candidate=candidate,
        features=features,
        evidence=evidence,
        forecast=forecast,
        evidence_frozen_at=forecast.frozen_at,
        created_at=datetime(2026, 5, 25, 10, 3, tzinfo=timezone.utc),
    )
    assert load_due_markets(
        stale_after_hours=24.0,
        now=datetime(2026, 5, 25, 12, 0, tzinfo=timezone.utc),
    ) == []

    stale = load_due_markets(
        stale_after_hours=24.0,
        now=datetime(2026, 5, 26, 12, 30, tzinfo=timezone.utc),
    )
    assert len(stale) == 1
    assert stale[0]["due_reason"].startswith("stale_")
    assert stale[0]["hours_since_forecast"] > 24.0
    assert stale[0]["latest_p_f"] == 0.55

    mark_outcome(candidate.market.condition_id, 1.0, source="manual")
    assert load_due_markets(
        stale_after_hours=24.0,
        now=datetime(2026, 5, 26, 12, 30, tzinfo=timezone.utc),
    ) == []

    config_module._settings = None
