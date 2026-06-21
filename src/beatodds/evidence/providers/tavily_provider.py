"""Tavily-backed search provider."""

from __future__ import annotations

from datetime import datetime, timezone
from urllib.parse import urlparse

from beatodds.common.config import get_settings
from beatodds.evidence.providers.base import SearchQuery, SearchResult


class TavilyProvider:
    name = "tavily"

    def __init__(self, api_key: str | None = None, client=None):
        self.api_key = api_key
        self._client = client

    def _get_client(self):
        if self._client is None:
            from tavily import TavilyClient

            self._client = TavilyClient(api_key=self.api_key or get_settings().tavily_api_key)
        return self._client

    def search(self, query: SearchQuery, max_results: int = 5) -> list[SearchResult]:
        results = self._get_client().search(
            query=query.query,
            max_results=max_results,
            search_depth="advanced",
            include_answer=False,
            include_raw_content=False,
        )
        output = []
        for item in results.get("results", []):
            url = item.get("url", "")
            output.append(SearchResult(
                query=query.query,
                title=item.get("title", ""),
                summary=item.get("content", "")[:500],
                url=url,
                source=_domain(url),
                published_at=_parse_date(item.get("published_date")),
                relevance_score=float(item.get("score", 0.0)),
                provider=self.name,
                source_type=query.source_type,
                reliability_prior=query.reliability_prior,
                raw_metadata={
                    "query_metadata": query.metadata,
                    "source_domain": query.source_domain,
                },
            ))
        return output


def _parse_date(date_str: str | None) -> datetime | None:
    if not date_str:
        return None
    for fmt in ("%Y-%m-%dT%H:%M:%SZ", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d"):
        try:
            dt = datetime.strptime(date_str, fmt)
            return dt.replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc
        return domain[4:] if domain.startswith("www.") else domain
    except Exception:
        return ""
