"""Deterministic Chinese query expansion for China-related markets."""

from __future__ import annotations

from pydantic import BaseModel, Field

from beatodds.common.types import MarketMeta, ResolutionFeatures
from beatodds.evidence.china_sources import ChinaSource


class ChinaQueryBundle(BaseModel):
    baseline_queries: list[str] = Field(default_factory=list)
    chinese_queries: list[str] = Field(default_factory=list)
    official_queries: list[str] = Field(default_factory=list)
    site_queries: list[str] = Field(default_factory=list)
    entities_cn: list[str] = Field(default_factory=list)


_ENTITY_CN = {
    "china": ["中国"],
    "chinese": ["中国"],
    "prc": ["中国"],
    "taiwan": ["台湾"],
    "hong kong": ["香港"],
    "macau": ["澳门"],
    "beijing": ["北京"],
    "shanghai": ["上海"],
    "shenzhen": ["深圳"],
    "xi jinping": ["习近平"],
    "pboc": ["中国人民银行", "央行"],
    "people's bank of china": ["中国人民银行", "央行"],
    "nbs": ["国家统计局"],
    "national bureau of statistics": ["国家统计局"],
    "mofcom": ["商务部"],
    "ministry of commerce": ["商务部"],
    "csrc": ["证监会"],
    "gdp": ["GDP", "国内生产总值"],
    "cpi": ["CPI", "居民消费价格"],
    "ppi": ["PPI", "工业生产者出厂价格"],
    "pmi": ["PMI", "采购经理指数"],
    "tariff": ["关税"],
    "sanction": ["制裁"],
    "export control": ["出口管制"],
    "rare earth": ["稀土"],
    "real estate": ["房地产"],
    "property": ["房地产"],
    "semiconductor": ["半导体"],
    "chip": ["芯片"],
    "electric vehicle": ["新能源汽车"],
    "ev": ["新能源汽车"],
}

_EVENT_TEMPLATES = {
    "policy": ["实施方案", "通知", "征求意见稿", "政策解读", "国务院", "发改委", "财政部"],
    "macro_data": ["国家统计局", "海关总署", "央行", "社融", "PMI", "CPI", "PPI", "进出口"],
    "regulation": ["处罚决定书", "监管函", "约谈", "专项整治", "证监会", "市监总局", "网信办"],
    "diplomacy_trade": ["商务部公告", "外交部", "海关总署", "出口管制", "反倾销", "反补贴"],
    "company": ["公告", "巨潮资讯", "上交所", "深交所", "港交所", "投资者关系"],
    "financial_market": ["中国人民银行", "央行", "人民币", "降准", "降息", "证监会"],
    "real_estate": ["住建局", "自然资源局", "公积金中心", "土拍", "预售证", "限购"],
    "public_health": ["卫健委", "疾控", "发热门诊", "通报", "停课", "医院公告"],
    "social_incident": ["官方通报", "情况通报", "人民网", "新华社"],
    "technology": ["工信部", "科技部", "网信办", "半导体", "人工智能", "新能源汽车"],
    "military_security": ["国防部", "外交部", "台海", "军演", "新华社"],
}


def build_china_query_bundle(
    market: MarketMeta | None,
    features: ResolutionFeatures,
    routed_sources: list[ChinaSource] | None = None,
    max_baseline_queries: int = 4,
    max_chinese_queries: int = 6,
    max_site_queries: int = 10,
) -> ChinaQueryBundle:
    baseline = _dedupe(features.search_queries + ([market.question] if market else []))
    entities_cn = _infer_entities_cn(market, features)
    if features.china_relevance != "low" and not entities_cn:
        entities_cn = ["中国"]

    keywords = _EVENT_TEMPLATES.get(features.event_type, ["官方通报", "公告", "政策"])
    entity_prefix = " ".join(entities_cn[:3]) if entities_cn else "中国"
    chinese_queries = _dedupe([
        f"{entity_prefix} {keyword}"
        for keyword in keywords
    ])

    official_queries = _dedupe([
        f"{entity_prefix} {keyword} 官方"
        for keyword in keywords[:4]
    ])

    site_queries = []
    for source in routed_sources or []:
        for keyword in keywords[:2]:
            site_queries.append(f"site:{source.domain} {entity_prefix} {keyword}")

    return ChinaQueryBundle(
        baseline_queries=baseline[:max_baseline_queries],
        chinese_queries=chinese_queries[:max_chinese_queries],
        official_queries=official_queries[:max_chinese_queries],
        site_queries=_dedupe(site_queries)[:max_site_queries],
        entities_cn=entities_cn,
    )


def _infer_entities_cn(market: MarketMeta | None, features: ResolutionFeatures) -> list[str]:
    text = " ".join([
        market.question if market else "",
        market.description if market else "",
        market.resolution_text if market else "",
        " ".join(features.key_entities),
        " ".join(features.search_queries),
        " ".join(features.geography),
    ]).lower()
    entities: list[str] = []
    for token, translated in _ENTITY_CN.items():
        if token in text:
            entities.extend(translated)
    for value in features.geography:
        if any("\u4e00" <= char <= "\u9fff" for char in value):
            entities.append(value)
    return _dedupe(entities)


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    result = []
    for value in values:
        cleaned = str(value).strip()
        if not cleaned or cleaned in seen:
            continue
        seen.add(cleaned)
        result.append(cleaned)
    return result
