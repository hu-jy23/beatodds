"""Deterministic provider for unit tests and fixture-driven development."""

from __future__ import annotations

from beatodds.evidence.providers.base import SearchQuery, SearchResult


class MockSearchProvider:
    name = "mock"

    def __init__(self, results_by_query: dict[str, list[SearchResult]] | None = None):
        self.results_by_query = results_by_query or {}
        self.queries: list[SearchQuery] = []

    def search(self, query: SearchQuery, max_results: int = 5) -> list[SearchResult]:
        self.queries.append(query)
        results = self.results_by_query.get(query.query, [])
        if results:
            return results[:max_results]
        return [
            SearchResult(
                query=query.query,
                title=f"模拟结果：{query.query}",
                summary="模拟证据摘要。",
                url=f"https://example.com/{_slug(query.query)}",
                source="example.com",
                relevance_score=0.5,
                provider=self.name,
                source_type=query.source_type,
                reliability_prior=query.reliability_prior,
                raw_metadata={"query_metadata": query.metadata},
            )
        ][:max_results]


def _slug(value: str) -> str:
    return "".join(char.lower() if char.isalnum() else "-" for char in value).strip("-")[:80]
