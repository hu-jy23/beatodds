from datetime import datetime, timezone

from beatodds.common.types import CandidateMarket, MarketMeta, PriceSnapshot, ResolutionFeatures
from beatodds.evidence.china_query import build_china_query_bundle
from beatodds.evidence.china_router import route_china_sources
from beatodds.evidence.providers.mock_provider import MockSearchProvider
from beatodds.evidence.retriever import EvidenceRetriever
from beatodds.resolution_parser.parser import ResolutionParser


def _china_market() -> MarketMeta:
    return MarketMeta(
        condition_id="0xchina",
        question="Will China announce new export controls on rare earths by July 31?",
        description="Resolves YES if MOFCOM announces rare earth export controls.",
        resolution_text="Use official Chinese government or Ministry of Commerce announcements.",
        category="Politics",
        token_yes_id="yes",
        token_no_id="no",
    )


def _candidate() -> CandidateMarket:
    market = _china_market()
    return CandidateMarket(
        market=market,
        snapshot=PriceSnapshot(
            condition_id=market.condition_id,
            token_id=market.token_yes_id,
            snapshot_time=datetime(2026, 6, 5, 8, 0, tzinfo=timezone.utc),
            midpoint=0.35,
            best_bid=0.34,
            best_ask=0.36,
            spread=0.02,
        ),
    )


def test_resolution_parser_fallback_marks_china_fields(monkeypatch) -> None:
    def _raise(*args, **kwargs):
        raise RuntimeError("skip network")

    monkeypatch.setattr(ResolutionParser, "_call_llm", _raise)

    features = ResolutionParser().parse(_china_market())

    assert features.china_relevance == "high"
    assert features.event_type == "diplomacy_trade"
    assert "China" in features.geography
    assert "mofcom.gov.cn" in features.source_routing_hints


def test_china_query_bundle_and_router_use_official_trade_sources() -> None:
    market = _china_market()
    features = ResolutionFeatures(
        condition_id=market.condition_id,
        event_type="diplomacy_trade",
        china_relevance="high",
        key_entities=["China", "MOFCOM", "rare earth export control"],
        search_queries=["China rare earth export controls MOFCOM"],
        geography=["China"],
        resolution_source_hint="Ministry of Commerce",
        source_routing_hints=["mofcom.gov.cn"],
    )

    routed = route_china_sources(features)
    bundle = build_china_query_bundle(market, features, routed_sources=routed)

    assert any(source.domain == "mofcom.gov.cn" for source in routed)
    assert any("出口管制" in query for query in bundle.chinese_queries)
    assert any("site:mofcom.gov.cn" in query for query in bundle.site_queries)


def test_retriever_china_info_adds_provenance_without_network() -> None:
    candidate = _candidate()
    features = ResolutionFeatures(
        condition_id=candidate.market.condition_id,
        event_type="diplomacy_trade",
        china_relevance="high",
        key_entities=["China", "MOFCOM", "rare earth export control"],
        search_queries=["China rare earth export controls MOFCOM"],
        geography=["China"],
        resolution_source_hint="Ministry of Commerce",
        source_routing_hints=["mofcom.gov.cn"],
    )

    provider = MockSearchProvider()
    evidence, frozen_at = EvidenceRetriever(provider=provider).retrieve(
        candidate,
        features,
        max_results_per_query=1,
        enable_china_info=True,
    )

    assert frozen_at.tzinfo is not None
    assert len(provider.queries) > 1
    assert any(query.query.startswith("site:mofcom.gov.cn") for query in provider.queries)
    assert any(item.source_type == "central_official" for item in evidence)
    assert all(item.provider == "mock" for item in evidence)
    assert all(item.retrieved_at == frozen_at for item in evidence)
