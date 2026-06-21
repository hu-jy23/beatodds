"""Source-card rendering for workspace artifacts."""

from __future__ import annotations

from typing import Any

from beatodds.agents.models import SourceCard, slugify, utc_now
from beatodds.evidence.providers.base import SearchResult


def source_card_from_search_result(
    result: SearchResult,
    source_category: str | None = None,
    retrieved_at=None,
) -> SourceCard:
    return SourceCard(
        title=result.title,
        url=result.url,
        source=result.source,
        source_category=source_category or result.source_type,
        provider=result.provider,
        query=result.query,
        summary=result.summary,
        published_at=result.published_at,
        retrieved_at=retrieved_at or utc_now(),
        relevance_score=result.relevance_score,
        reliability_prior=result.reliability_prior,
        raw_metadata=result.raw_metadata,
    )


def source_card_filename(card: SourceCard, index: int | None = None) -> str:
    prefix = f"{index:03d}_" if index is not None else ""
    base = slugify(card.title or card.source or card.url, fallback="source", max_len=80)
    return f"{prefix}{base}.md"


def render_source_card(card: SourceCard) -> str:
    published = card.published_at.isoformat() if card.published_at else ""
    retrieved = card.retrieved_at.isoformat()
    return "\n".join([
        "# 来源卡片",
        "",
        f"- 标题: {card.title}",
        f"- url: {card.url}",
        f"- 来源: `{card.source}`",
        f"- category: `{card.source_category}`",
        f"- provider: `{card.provider}`",
        f"- query: {card.query}",
        f"- published_at: `{published}`",
        f"- retrieved_at: `{retrieved}`",
        f"- relevance_score: `{card.relevance_score:.3f}`",
        f"- reliability_prior: `{card.reliability_prior:.3f}`",
        "",
        "## 摘要",
        "",
        card.summary or "",
        "",
        "## 原始 Metadata",
        "",
        _render_metadata(card.raw_metadata),
        "",
    ])


def _render_metadata(metadata: dict[str, Any]) -> str:
    if not metadata:
        return "{}"
    lines = []
    for key in sorted(metadata):
        lines.append(f"- {key}: `{metadata[key]}`")
    return "\n".join(lines)
