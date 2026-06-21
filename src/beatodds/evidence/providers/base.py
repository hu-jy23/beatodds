"""Provider-neutral search request and result models."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol

from pydantic import BaseModel, Field


class SearchQuery(BaseModel):
    query: str
    provider: str = "tavily"
    source_type: str = "western_source"
    source_domain: str = ""
    reliability_prior: float = 0.0
    metadata: dict[str, Any] = Field(default_factory=dict)


class SearchResult(BaseModel):
    query: str
    title: str
    summary: str
    url: str
    source: str
    published_at: datetime | None = None
    relevance_score: float = 0.0
    provider: str = "tavily"
    source_type: str = "western_source"
    reliability_prior: float = 0.0
    raw_metadata: dict[str, Any] = Field(default_factory=dict)


class SearchProvider(Protocol):
    name: str

    def search(self, query: SearchQuery, max_results: int = 5) -> list[SearchResult]:
        """Search one provider for one query."""
        ...
