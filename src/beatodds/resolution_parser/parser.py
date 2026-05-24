"""Resolution Parser — extracts semantic features from market resolution text.

Supports both Anthropic (Claude haiku) and OpenAI (gpt-4o-mini) backends.
Backend is auto-selected from config: Anthropic preferred, OpenAI as fallback.

Reference: ref/agent-benchmark/FutureShow/tools/resolution_analyzer.py (prompt design)
"""

from __future__ import annotations

import json
from datetime import datetime, timezone

from loguru import logger

from beatodds.common.config import get_settings
from beatodds.common.types import MarketMeta, ResolutionFeatures

_SYSTEM_PROMPT = """You are a prediction market analyst.
Given a market question and resolution criteria, extract structured information
to guide evidence search.

Respond with ONLY valid JSON matching this schema:
{
  "condition_type": "<price_threshold|event_occurrence|date_range|election|sports|other>",
  "key_entities": ["<entity1>", "<entity2>"],
  "search_queries": ["<query1>", "<query2>", "<query3>"],
  "has_explicit_deadline": <true|false>,
  "deadline_str": "<YYYY-MM-DD or null>",
  "oracle_type": "<UMA|admin|external_feed|unknown>",
  "exception_clauses": ["<clause1>"],
  "ambiguity_score": <0.0-1.0>,
  "risk_flags": ["<flag1>"]
}

Rules:
- search_queries: 2-4 specific, current-news-oriented queries (include dates if relevant)
- key_entities: proper nouns (people, orgs, tickers, places) that define the market
- ambiguity_score: 0.0 = unambiguous criterion, 1.0 = highly subjective/unclear
- risk_flags: e.g. "early_resolution", "admin_discretion", "ambiguous_wording"
"""


class ResolutionParser:
    def __init__(self):
        self.cfg = get_settings()
        self._client = None

    def _get_client(self):
        if self._client is not None:
            return self._client
        backend = self.cfg.llm_backend
        if backend == "anthropic":
            import anthropic
            self._client = ("anthropic", anthropic.Anthropic(api_key=self.cfg.anthropic_api_key))
        elif backend == "deepseek":
            import openai
            self._client = ("openai", openai.OpenAI(
                api_key=self.cfg.deepseek_api_key,
                base_url=self.cfg.deepseek_base_url,
            ))
        elif backend == "openai":
            import openai
            self._client = ("openai", openai.OpenAI(api_key=self.cfg.openai_api_key))
        else:
            raise RuntimeError(
                "No LLM API key configured. Set DEEPSEEK_API_KEY or "
                "ANTHROPIC_API_KEY in .env"
            )
        return self._client

    def parse(self, market: MarketMeta) -> ResolutionFeatures:
        """Extract resolution features from a market."""
        now = datetime.now(timezone.utc)
        resolution_text = market.resolution_text or market.description or market.question

        try:
            features = self._call_llm(market.question, resolution_text, market.condition_id)
            features.parsed_at = now
            return features
        except Exception as e:
            logger.warning(f"ResolutionParser failed for {market.condition_id}: {e}")
            return ResolutionFeatures(
                condition_id=market.condition_id,
                search_queries=[market.question],
                key_entities=[],
                parsed_at=now,
            )

    def parse_batch(self, markets: list[MarketMeta]) -> list[ResolutionFeatures]:
        results = []
        for i, market in enumerate(markets):
            logger.debug(f"Parsing {i+1}/{len(markets)}: {market.question[:50]}")
            results.append(self.parse(market))
        return results

    def _call_llm(
        self,
        question: str,
        resolution_text: str,
        condition_id: str,
    ) -> ResolutionFeatures:
        backend, client = self._get_client()

        user_text = f"Market question: {question}\n\nResolution criteria:\n{resolution_text}"

        if backend == "anthropic":
            raw = self._call_anthropic(client, user_text)
        else:  # openai-compatible (openai or deepseek)
            model = (self.cfg.deepseek_cheap_model if self.cfg.llm_backend == "deepseek"
                     else self.cfg.openai_cheap_model)
            raw = self._call_openai(client, user_text, model)

        if raw.startswith("```"):
            raw = raw.split("```")[1]
            if raw.startswith("json"):
                raw = raw[4:]
        data = json.loads(raw.strip())

        deadline_date = None
        if data.get("deadline_str"):
            try:
                deadline_date = datetime.fromisoformat(data["deadline_str"])
            except (ValueError, TypeError):
                pass

        return ResolutionFeatures(
            condition_id=condition_id,
            condition_type=data.get("condition_type", "unknown"),
            key_entities=data.get("key_entities", []),
            search_queries=data.get("search_queries", [question]),
            has_explicit_deadline=bool(data.get("has_explicit_deadline", False)),
            deadline_date=deadline_date,
            oracle_type=data.get("oracle_type", "unknown"),
            exception_clauses=data.get("exception_clauses", []),
            ambiguity_score=float(data.get("ambiguity_score", 0.0)),
            risk_flags=data.get("risk_flags", []),
        )

    def _call_anthropic(self, client, user_text: str) -> str:
        response = client.messages.create(
            model=self.cfg.anthropic_cheap_model,
            max_tokens=512,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": [
                {"type": "text", "text": user_text, "cache_control": {"type": "ephemeral"}},
            ]}],
        )
        return response.content[0].text

    def _call_openai(self, client, user_text: str, model: str | None = None) -> str:
        response = client.chat.completions.create(
            model=model or self.cfg.openai_cheap_model,
            max_tokens=512,
            messages=[
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": user_text},
            ],
            response_format={"type": "json_object"},
        )
        return response.choices[0].message.content
