"""Route China-related markets to source classes and domains."""

from __future__ import annotations

from beatodds.common.types import ResolutionFeatures
from beatodds.evidence.china_sources import ChinaSource, load_china_sources

_SOURCE_TYPE_PREFS = {
    "macro_data": ["central_official"],
    "policy": ["central_official", "official_media", "procurement"],
    "regulation": ["regulator", "central_official"],
    "diplomacy_trade": ["central_official", "official_media"],
    "company": ["company_filing", "exchange_filing", "regulator"],
    "financial_market": ["central_official", "regulator", "exchange_filing", "company_filing"],
    "real_estate": ["central_official", "land_construction", "local_official"],
    "public_health": ["central_official", "regulator", "official_media"],
    "social_incident": ["central_official", "official_media"],
    "technology": ["central_official", "regulator", "procurement"],
    "military_security": ["central_official", "official_media"],
}


def route_china_sources(
    features: ResolutionFeatures,
    sources: tuple[ChinaSource, ...] | None = None,
    max_sources: int = 8,
) -> list[ChinaSource]:
    """Choose likely useful China sources for one parsed market."""
    if features.china_relevance == "low":
        return []

    registry = sources or load_china_sources()
    event_type = features.event_type
    source_type_prefs = _SOURCE_TYPE_PREFS.get(event_type, [])
    hint_text = " ".join([
        features.resolution_source_hint,
        " ".join(features.source_routing_hints),
    ]).lower()

    scored: list[tuple[float, ChinaSource]] = []
    for source in registry:
        score = source.reliability_prior
        if event_type in source.topics:
            score += 1.0
        if source.source_type in source_type_prefs:
            score += 0.35
        if source.domain.lower() in hint_text or source.name.lower() in hint_text:
            score += 1.0
        if event_type == "policy" and source.domain == "gov.cn":
            score += 0.4
        if event_type == "macro_data" and source.domain in {"stats.gov.cn", "pbc.gov.cn"}:
            score += 0.4
        if event_type == "company" and source.source_type in {"company_filing", "exchange_filing"}:
            score += 0.4
        scored.append((score, source))

    scored.sort(key=lambda item: item[0], reverse=True)
    routed = [source for score, source in scored if score >= 1.0]
    return routed[:max_sources]


def routed_source_types(sources: list[ChinaSource]) -> list[str]:
    seen = set()
    types = []
    for source in sources:
        if source.source_type in seen:
            continue
        seen.add(source.source_type)
        types.append(source.source_type)
    return types
