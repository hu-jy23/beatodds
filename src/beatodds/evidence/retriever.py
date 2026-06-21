"""Evidence Retriever — fetches and ranks news/web evidence for a market.

Uses provider-based search. Tavily remains the default baseline provider.
Enforces temporal integrity by recording evidence_frozen_at = now() BEFORE any
search calls. All retrieved evidence must have publish_date < evidence_frozen_at.

Reference: ref/agent-benchmark/FutureShow/tools/search.py
           ref/agent-benchmark/prediction-market-agent-tooling/prediction_market_agent_tooling/tools/tavily_search.py
"""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.types import CandidateMarket, EvidenceItem, ResolutionFeatures
from beatodds.evidence.china_query import build_china_query_bundle
from beatodds.evidence.china_router import route_china_sources
from beatodds.evidence.china_sources import source_for_domain
from beatodds.evidence.providers.base import SearchProvider, SearchQuery, SearchResult
from beatodds.evidence.providers.tavily_provider import TavilyProvider


class EvidenceRetriever:
    def __init__(self, provider: SearchProvider | None = None):
        self.cfg = get_settings()
        self.provider = provider or TavilyProvider(api_key=self.cfg.tavily_api_key)

    def retrieve(
        self,
        candidate: CandidateMarket,
        features: ResolutionFeatures,
        max_results_per_query: int = 5,
        enable_china_info: bool = False,
    ) -> tuple[list[EvidenceItem], datetime]:
        """Fetch evidence for a market.

        Returns (evidence_items, evidence_frozen_at).
        evidence_frozen_at is set BEFORE any search calls — this is the
        temporal integrity guarantee.
        """
        # Record the cutoff time BEFORE any search — prevents leakage
        evidence_frozen_at = datetime.now(timezone.utc)

        queries = self._build_queries(candidate, features, enable_china_info)
        all_items: list[EvidenceItem] = []
        seen_urls: set[str] = set()

        for query in queries:
            try:
                results = self.provider.search(query, max_results=max_results_per_query)
                for result in results:
                    dedupe_key = _dedupe_key(result)
                    if dedupe_key in seen_urls:
                        continue
                    seen_urls.add(dedupe_key)

                    published_at = result.published_at or evidence_frozen_at
                    # Skip evidence published AFTER the frozen cutoff (future leakage)
                    if published_at > evidence_frozen_at:
                        logger.debug(
                            f"Skipping future evidence: {result.url} ({published_at})"
                        )
                        continue

                    source_type, reliability_prior = self._resolve_source_type(result, query)
                    all_items.append(
                        EvidenceItem(
                            query=result.query or query.query,
                            title=result.title,
                            summary=result.summary[:500],
                            url=result.url,
                            source=result.source or _domain(result.url),
                            published_at=published_at,
                            retrieved_at=evidence_frozen_at,
                            relevance_score=result.relevance_score,
                            provider=result.provider or self.provider.name,
                            source_type=source_type,
                            reliability_prior=reliability_prior,
                            resolution_relevance=_resolution_relevance(source_type, query),
                            dedupe_key=dedupe_key,
                            raw_metadata=result.raw_metadata,
                        )
                    )
            except Exception as e:
                logger.warning(f"{self.provider.name} search failed for query '{query.query}': {e}")
                continue

        # Sort by relevance score descending
        all_items.sort(
            key=lambda x: (
                x.relevance_score,
                x.reliability_prior,
                x.resolution_relevance,
            ),
            reverse=True,
        )
        logger.debug(
            f"Retrieved {len(all_items)} evidence items for {candidate.market.condition_id[:16]}"
        )
        return all_items, evidence_frozen_at

    def _build_queries(
        self,
        candidate: CandidateMarket,
        features: ResolutionFeatures,
        enable_china_info: bool,
    ) -> list[SearchQuery]:
        baseline = features.search_queries or [candidate.market.question]
        queries = [
            SearchQuery(
                query=query,
                provider=self.provider.name,
                source_type="western_source",
                reliability_prior=0.2,
                metadata={"route": "baseline"},
            )
            for query in baseline[:4]
        ]

        if not enable_china_info or features.china_relevance == "low":
            return queries

        routed_sources = route_china_sources(features)
        bundle = build_china_query_bundle(candidate.market, features, routed_sources=routed_sources)

        queries.extend(
            SearchQuery(
                query=query,
                provider=self.provider.name,
                source_type="china_general",
                reliability_prior=0.35,
                metadata={"route": "china_general", "entities_cn": bundle.entities_cn},
            )
            for query in bundle.chinese_queries[:4]
        )
        queries.extend(
            SearchQuery(
                query=query,
                provider=self.provider.name,
                source_type="central_official",
                reliability_prior=0.6,
                metadata={"route": "official_query", "entities_cn": bundle.entities_cn},
            )
            for query in bundle.official_queries[:3]
        )
        queries.extend(self._site_queries(bundle.site_queries, routed_sources))
        return _dedupe_queries(queries)

    def _site_queries(
        self,
        site_queries: list[str],
        routed_sources,
    ) -> list[SearchQuery]:
        source_by_domain = {source.domain: source for source in routed_sources}
        queries: list[SearchQuery] = []
        for query in site_queries:
            domain = _site_domain(query)
            source = source_by_domain.get(domain) or source_for_domain(domain)
            queries.append(
                SearchQuery(
                    query=query,
                    provider=self.provider.name,
                    source_type=source.source_type if source else "other",
                    source_domain=domain,
                    reliability_prior=source.reliability_prior if source else 0.45,
                    metadata={
                        "route": "china_site_query",
                        "source_name": source.name if source else "",
                    },
                )
            )
        return queries

    def _resolve_source_type(
        self,
        result: SearchResult,
        query: SearchQuery,
    ) -> tuple[str, float]:
        source = source_for_domain(result.url or result.source or query.source_domain)
        if source:
            return source.source_type, max(result.reliability_prior, source.reliability_prior)
        return result.source_type or query.source_type, result.reliability_prior


def _dedupe_key(result: SearchResult) -> str:
    if result.url:
        return result.url.rstrip("/")
    return f"{result.source}:{result.title}".lower()


def _dedupe_queries(queries: list[SearchQuery]) -> list[SearchQuery]:
    seen = set()
    deduped = []
    for query in queries:
        cleaned = query.query.strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(query.model_copy(update={"query": cleaned}))
    return deduped


def _site_domain(query: str) -> str:
    for token in query.split():
        if token.startswith("site:"):
            return token.removeprefix("site:").strip()
    return ""


def _resolution_relevance(source_type: str, query: SearchQuery) -> float:
    if query.metadata.get("route") == "china_site_query":
        return 0.8
    if source_type in {"central_official", "regulator", "company_filing", "exchange_filing"}:
        return 0.7
    if source_type in {"official_media", "china_general"}:
        return 0.45
    return 0.3


def _domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc
        return domain[4:] if domain.startswith("www.") else domain
    except Exception:
        return ""
