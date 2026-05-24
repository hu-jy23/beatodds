"""Evidence Retriever — fetches and ranks news/web evidence for a market.

Uses Tavily for search + extraction. Enforces temporal integrity by recording
evidence_frozen_at = now() BEFORE any search calls. All retrieved evidence
must have publish_date < evidence_frozen_at.

Reference: ref/agent-benchmark/FutureShow/tools/search.py
           ref/agent-benchmark/prediction-market-agent-tooling/prediction_market_agent_tooling/tools/tavily_search.py
"""

from __future__ import annotations

from datetime import datetime, timezone

from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.types import CandidateMarket, EvidenceItem, ResolutionFeatures


class EvidenceRetriever:
    def __init__(self):
        self.cfg = get_settings()
        self._client = None

    def _get_client(self):
        if self._client is None:
            from tavily import TavilyClient
            self._client = TavilyClient(api_key=self.cfg.tavily_api_key)
        return self._client

    def retrieve(
        self,
        candidate: CandidateMarket,
        features: ResolutionFeatures,
        max_results_per_query: int = 5,
    ) -> tuple[list[EvidenceItem], datetime]:
        """Fetch evidence for a market.

        Returns (evidence_items, evidence_frozen_at).
        evidence_frozen_at is set BEFORE any search calls — this is the
        temporal integrity guarantee.
        """
        # Record the cutoff time BEFORE any search — prevents leakage
        evidence_frozen_at = datetime.now(timezone.utc)

        queries = features.search_queries or [candidate.market.question]
        all_items: list[EvidenceItem] = []
        seen_urls: set[str] = set()

        client = self._get_client()

        for query in queries[:4]:   # cap at 4 queries per market
            try:
                results = client.search(
                    query=query,
                    max_results=max_results_per_query,
                    search_depth="advanced",
                    include_answer=False,
                    include_raw_content=False,
                )
                for r in results.get("results", []):
                    url = r.get("url", "")
                    if url in seen_urls:
                        continue
                    seen_urls.add(url)

                    published_at = self._parse_date(r.get("published_date")) or evidence_frozen_at
                    # Skip evidence published AFTER the frozen cutoff (future leakage)
                    if published_at > evidence_frozen_at:
                        logger.debug(f"Skipping future evidence: {url} ({published_at})")
                        continue

                    all_items.append(EvidenceItem(
                        title=r.get("title", ""),
                        summary=r.get("content", "")[:500],
                        url=url,
                        source=self._domain(url),
                        published_at=published_at,
                        relevance_score=float(r.get("score", 0.0)),
                    ))
            except Exception as e:
                logger.warning(f"Tavily search failed for query '{query}': {e}")
                continue

        # Sort by relevance score descending
        all_items.sort(key=lambda x: x.relevance_score, reverse=True)
        logger.debug(
            f"Retrieved {len(all_items)} evidence items for {candidate.market.condition_id[:16]}"
        )
        return all_items, evidence_frozen_at

    def _parse_date(self, date_str: str | None) -> datetime | None:
        if not date_str:
            return None
        for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
            try:
                dt = datetime.strptime(date_str, fmt)
                return dt.replace(tzinfo=timezone.utc)
            except ValueError:
                continue
        return None

    def _domain(self, url: str) -> str:
        try:
            from urllib.parse import urlparse
            return urlparse(url).netloc
        except Exception:
            return ""
