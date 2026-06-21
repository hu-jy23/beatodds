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
  "event_type": "<macro_data|policy|regulation|diplomacy_trade|company|financial_market|...>",
  "china_relevance": "<high|medium|low>",
  "key_entities": ["<entity1>", "<entity2>"],
  "search_queries": ["<query1>", "<query2>", "<query3>"],
  "geography": ["<country/region/city>"],
  "resolution_source_hint": "<short source hint or empty string>",
  "source_routing_hints": ["<source type/domain hint>"],
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
- china_relevance: high only when Chinese official/local information is likely material
- event_type: use the China routing taxonomy when possible
- ambiguity_score: 0.0 = unambiguous criterion, 1.0 = highly subjective/unclear
- risk_flags: e.g. "early_resolution", "admin_discretion", "ambiguous_wording"
"""

_EVENT_TYPES = {
    "macro_data",
    "policy",
    "regulation",
    "diplomacy_trade",
    "company",
    "financial_market",
    "real_estate",
    "public_health",
    "social_incident",
    "technology",
    "military_security",
    "sports",
    "election",
    "other",
}

_CHINA_RELEVANCE = {"high", "medium", "low"}

_CHINA_TERMS = [
    "china",
    "chinese",
    "prc",
    "beijing",
    "shanghai",
    "shenzhen",
    "hong kong",
    "macau",
    "taiwan",
    "xi jinping",
    "ccp",
    "pla",
    "pboc",
    "pboc",
    "nbs",
    "mofcom",
    "csrc",
    "yuan",
    "renminbi",
    "中国",
    "北京",
    "上海",
    "深圳",
    "香港",
    "澳门",
    "台湾",
    "习近平",
    "央行",
    "国家统计局",
    "商务部",
    "证监会",
]

_GEOGRAPHY_TERMS = {
    "china": "China",
    "prc": "China",
    "中国": "China",
    "beijing": "Beijing",
    "北京": "Beijing",
    "shanghai": "Shanghai",
    "上海": "Shanghai",
    "shenzhen": "Shenzhen",
    "深圳": "Shenzhen",
    "hong kong": "Hong Kong",
    "香港": "Hong Kong",
    "macau": "Macau",
    "澳门": "Macau",
    "taiwan": "Taiwan",
    "台湾": "Taiwan",
}

_EVENT_KEYWORDS = {
    "macro_data": [
        "gdp",
        "cpi",
        "ppi",
        "pmi",
        "inflation",
        "exports",
        "imports",
        "trade balance",
        "social financing",
        "国家统计局",
        "海关",
        "社融",
    ],
    "policy": [
        "policy",
        "stimulus",
        "subsidy",
        "five-year plan",
        "state council",
        "发改委",
        "财政部",
        "国务院",
        "政策",
    ],
    "regulation": [
        "regulation",
        "ban",
        "probe",
        "fine",
        "antitrust",
        "cyberspace",
        "监管",
        "处罚",
        "专项整治",
        "网信办",
    ],
    "diplomacy_trade": [
        "tariff",
        "sanction",
        "export control",
        "trade deal",
        "mofcom",
        "foreign ministry",
        "关税",
        "制裁",
        "出口管制",
        "商务部",
        "外交部",
    ],
    "company": [
        "earnings",
        "merger",
        "ipo",
        "shares",
        "stock",
        "company",
        "公告",
        "巨潮资讯",
        "上交所",
        "深交所",
        "港交所",
    ],
    "financial_market": [
        "yuan",
        "renminbi",
        "pboc",
        "interest rate",
        "reserve requirement",
        "stock index",
        "央行",
        "人民币",
        "降准",
        "降息",
    ],
    "real_estate": [
        "property",
        "real estate",
        "housing",
        "home price",
        "developer",
        "土地",
        "房地产",
        "住建",
        "土拍",
    ],
    "public_health": [
        "covid",
        "outbreak",
        "hospital",
        "vaccine",
        "health commission",
        "卫健委",
        "疾控",
        "疫情",
        "医院",
    ],
    "social_incident": ["protest", "strike", "riot", "事故", "抗议", "舆情"],
    "technology": ["chip", "semiconductor", "ai", "battery", "ev", "芯片", "半导体", "人工智能"],
    "military_security": [
        "military",
        "invade",
        "war",
        "navy",
        "missile",
        "taiwan",
        "PLA",
        "军演",
        "台湾",
        "国防部",
    ],
}

_ROUTE_HINTS_BY_EVENT_TYPE = {
    "macro_data": ["stats.gov.cn", "customs.gov.cn", "pbc.gov.cn"],
    "policy": ["gov.cn", "ndrc.gov.cn", "mof.gov.cn"],
    "regulation": ["csrc.gov.cn", "samr.gov.cn", "cac.gov.cn"],
    "diplomacy_trade": ["mofcom.gov.cn", "mfa.gov.cn", "customs.gov.cn"],
    "company": ["cninfo.com.cn", "sse.com.cn", "szse.cn", "hkexnews.hk"],
    "financial_market": ["pbc.gov.cn", "csrc.gov.cn", "sse.com.cn"],
    "real_estate": ["mohurd.gov.cn", "mnr.gov.cn", "gov.cn"],
    "public_health": ["nhc.gov.cn", "nmpa.gov.cn", "chinacdc.cn"],
    "social_incident": ["gov.cn", "people.com.cn", "xinhuanet.com"],
    "technology": ["most.gov.cn", "miit.gov.cn", "cac.gov.cn"],
    "military_security": ["mnd.gov.cn", "mfa.gov.cn", "xinhuanet.com"],
}

_SOURCE_HINTS = [
    ("stats.gov.cn", "National Bureau of Statistics"),
    ("national bureau of statistics", "National Bureau of Statistics"),
    ("pbc.gov.cn", "People's Bank of China"),
    ("pboc", "People's Bank of China"),
    ("people's bank of china", "People's Bank of China"),
    ("mofcom.gov.cn", "Ministry of Commerce"),
    ("mofcom", "Ministry of Commerce"),
    ("mfa.gov.cn", "Ministry of Foreign Affairs"),
    ("foreign ministry", "Ministry of Foreign Affairs"),
    ("csrc.gov.cn", "China Securities Regulatory Commission"),
    ("csrc", "China Securities Regulatory Commission"),
    ("cninfo.com.cn", "CNINFO"),
    ("sse.com.cn", "Shanghai Stock Exchange"),
    ("szse.cn", "Shenzhen Stock Exchange"),
    ("hkexnews.hk", "HKEXnews"),
    ("nhc.gov.cn", "National Health Commission"),
    ("nmpa.gov.cn", "National Medical Products Administration"),
]


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
            return _complete_china_fields(features, market)
        except Exception as e:
            logger.warning(f"ResolutionParser failed for {market.condition_id}: {e}")
            features = ResolutionFeatures(
                condition_id=market.condition_id,
                search_queries=[market.question],
                key_entities=[],
                parsed_at=now,
            )
            return _complete_china_fields(features, market)

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

        features = ResolutionFeatures(
            condition_id=condition_id,
            condition_type=data.get("condition_type", "unknown"),
            event_type=_safe_event_type(data.get("event_type")),
            china_relevance=_safe_china_relevance(data.get("china_relevance")),
            key_entities=data.get("key_entities", []),
            search_queries=data.get("search_queries", [question]),
            geography=data.get("geography", []),
            resolution_source_hint=data.get("resolution_source_hint", ""),
            source_routing_hints=data.get("source_routing_hints", []),
            has_explicit_deadline=bool(data.get("has_explicit_deadline", False)),
            deadline_date=deadline_date,
            oracle_type=data.get("oracle_type", "unknown"),
            exception_clauses=data.get("exception_clauses", []),
            ambiguity_score=float(data.get("ambiguity_score", 0.0)),
            risk_flags=data.get("risk_flags", []),
        )
        return features

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


def _complete_china_fields(features: ResolutionFeatures, market: MarketMeta) -> ResolutionFeatures:
    text = " ".join([
        market.question,
        market.description,
        market.resolution_text,
        market.category,
        " ".join(features.key_entities),
        " ".join(features.search_queries),
    ])
    lowered = text.lower()

    event_type = features.event_type
    if event_type == "other":
        event_type = _infer_event_type(lowered)

    china_relevance = features.china_relevance
    if china_relevance == "low" and _contains_china_signal(text, lowered):
        china_relevance = "high"
    elif china_relevance == "low" and event_type in {"diplomacy_trade", "military_security"}:
        if "taiwan" in lowered or "香港" in text or "台湾" in text:
            china_relevance = "high"

    geography = _dedupe(features.geography + _infer_geography(text, lowered))
    resolution_source_hint = features.resolution_source_hint or _infer_source_hint(lowered)
    routing_hints = _dedupe(
        features.source_routing_hints + _ROUTE_HINTS_BY_EVENT_TYPE.get(event_type, [])
    )
    if resolution_source_hint:
        routing_hints = _dedupe(routing_hints + [resolution_source_hint])

    return features.model_copy(update={
        "event_type": event_type,
        "china_relevance": china_relevance,
        "geography": geography,
        "resolution_source_hint": resolution_source_hint,
        "source_routing_hints": routing_hints,
    })


def _safe_event_type(value: str | None) -> str:
    return value if value in _EVENT_TYPES else "other"


def _safe_china_relevance(value: str | None) -> str:
    return value if value in _CHINA_RELEVANCE else "low"


def _contains_china_signal(text: str, lowered: str) -> bool:
    if any("\u4e00" <= char <= "\u9fff" for char in text):
        return True
    return any(term in lowered for term in _CHINA_TERMS if term.isascii())


def _infer_event_type(lowered: str) -> str:
    scores = {
        event_type: sum(1 for keyword in keywords if keyword.lower() in lowered)
        for event_type, keywords in _EVENT_KEYWORDS.items()
    }
    best_type, best_score = max(scores.items(), key=lambda item: item[1])
    return best_type if best_score > 0 else "other"


def _infer_geography(text: str, lowered: str) -> list[str]:
    found = []
    for term, label in _GEOGRAPHY_TERMS.items():
        haystack = text if any("\u4e00" <= char <= "\u9fff" for char in term) else lowered
        if term in haystack:
            found.append(label)
    return _dedupe(found)


def _infer_source_hint(lowered: str) -> str:
    for token, hint in _SOURCE_HINTS:
        if token in lowered:
            return hint
    return ""


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    deduped = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        deduped.append(cleaned)
    return deduped
