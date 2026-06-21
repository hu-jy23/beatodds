"""Search-result quality scoring for auditable source-card creation."""

from __future__ import annotations

import re
from urllib.parse import urlparse

from beatodds.evidence.providers.base import SearchResult

_STOPWORDS = {
    "a",
    "an",
    "and",
    "analysis",
    "before",
    "bloomberg",
    "by",
    "com",
    "for",
    "in",
    "is",
    "news",
    "of",
    "on",
    "or",
    "reuters",
    "site",
    "the",
    "to",
    "will",
    "with",
    "www",
    "中文",
    "分析",
    "口碑",
    "近期",
    "视频",
    "赛后",
    "赛事",
    "比赛",
    "票房",
    "解读",
    "博主",
    "时政",
    "时政博主",
    "军事",
    "军事分析",
    "b站",
}

_BOILERPLATE_MARKERS = {
    "about us",
    "app",
    "contact us",
    "cookie",
    "copyright",
    "footer",
    "home",
    "login",
    "menu",
    "privacy policy",
    "subscribe",
    "terms of service",
    "网站首页",
    "字号",
    "打印本页",
    "当前位置",
    "关于我们",
    "机构设置",
    "联系我们",
    "版权所有",
    "登录",
    "首页",
}

_LOW_VALUE_TITLES = {
    "source",
    "讲话",
    "讲话全文",
    "重要新闻",
    "领导人活动",
}

_CHINESE_PHRASE_HINTS = [
    "习近平",
    "台湾",
    "台海",
    "稀土",
    "出口管制",
    "商务部",
    "国乒",
    "国家队",
    "王楚钦",
    "樊振东",
    "孙颖莎",
    "王曼昱",
    "陈梦",
    "马龙",
    "哪吒2",
    "哪吒",
    "阿里巴巴",
    "通义千问",
    "智谱",
    "月之暗面",
    "deepseek",
]

_CHINESE_QUERY_FILLERS = {
    "当前",
    "现在",
    "谁",
    "是否",
    "会不会",
    "概率",
    "最厉害",
}


def filter_and_rank_results(
    results: list[SearchResult],
    query: str,
    source_category: str,
    context_text: str = "",
    min_quality_score: float = 0.2,
) -> tuple[list[SearchResult], list[dict]]:
    """Return quality-ranked results and audit records for rejected results."""
    scored = [
        _score_result(
            result,
            query=query,
            source_category=source_category,
            context_text=context_text,
        )
        for result in results
    ]
    kept = [
        result.model_copy(
            update={
                "raw_metadata": {
                    **result.raw_metadata,
                    "search_quality": {
                        "score": round(score, 3),
                        "reasons": reasons,
                    },
                }
            }
        )
        for result, score, reasons in scored
        if score >= min_quality_score
    ]
    rejected = [
        {
            "title": result.title,
            "url": result.url,
            "source": result.source,
            "quality_score": round(score, 3),
            "reasons": reasons,
        }
        for result, score, reasons in scored
        if score < min_quality_score
    ]
    kept.sort(
        key=lambda item: item.raw_metadata.get("search_quality", {}).get("score", 0.0),
        reverse=True,
    )
    return kept, rejected


def _score_result(
    result: SearchResult,
    query: str,
    source_category: str,
    context_text: str = "",
) -> tuple[SearchResult, float, list[str]]:
    text = " ".join([result.title, result.summary, result.source, _domain(result.url)])
    normalized_text = _normalize(text)
    query_terms = _query_terms(query)
    context_terms = _query_terms(context_text)
    matched_terms = [term for term in query_terms if term in normalized_text]

    score = 0.35
    reasons: list[str] = []

    if result.relevance_score:
        score += min(result.relevance_score, 1.0) * 0.25
        reasons.append("provider_relevance")

    if query_terms:
        coverage = len(matched_terms) / len(query_terms)
        score += coverage * 0.35
        if matched_terms:
            reasons.append(f"query_overlap={','.join(matched_terms[:5])}")
        else:
            score -= 0.35
            reasons.append("no_query_overlap")

    if _has_primary_entity_miss(query_terms, normalized_text):
        score -= 0.3
        reasons.append("primary_entity_missing")

    if _has_distinctive_query_miss(query_terms, normalized_text):
        score = min(score - 0.3, 0.29)
        reasons.append("distinctive_query_entity_missing")

    if _has_context_entity_miss(context_terms, normalized_text):
        score = min(score - 0.35, 0.19)
        reasons.append("context_entity_missing")
    elif _has_context_entity_hit(context_terms, normalized_text):
        score += 0.2
        reasons.append("context_entity_hit")

    if _is_short_or_empty(result):
        score -= 0.25
        reasons.append("thin_snippet")

    if _looks_like_boilerplate(result):
        score -= 0.3
        reasons.append("boilerplate_or_directory")

    if source_category in {"official", "semi_official"} and _is_old_or_undated(result):
        score -= 0.1
        reasons.append("undated_or_stale")

    return result, max(0.0, min(score, 1.0)), reasons or ["baseline"]


def _query_terms(query: str) -> list[str]:
    lowered = query.lower()
    cleaned = re.sub(r"site:[^\s]+", " ", lowered)
    raw_terms = re.findall(
        r"[\u4e00-\u9fff]+[0-9]+|[0-9]+[\u4e00-\u9fff]+|[\u4e00-\u9fff]{2,}|[a-z0-9]{3,}",
        cleaned,
    )
    terms = []
    for term in raw_terms:
        if term in _STOPWORDS or term.isdigit():
            continue
        split_terms = _split_chinese_query_term(term)
        terms.extend(split_terms or [term])
    if "xi jinping" in lowered:
        terms.append("jinping")
    if "taiwan" in lowered:
        terms.append("taiwan")
    if "rare earth" in lowered:
        terms.append("稀土")
    if "export control" in lowered or "export controls" in lowered:
        terms.append("出口管制")
    if "mofcom" in lowered or "ministry of commerce" in lowered:
        terms.append("mofcom")
    return _dedupe(terms)


def _normalize(text: str) -> str:
    return " ".join(re.findall(
        r"[\u4e00-\u9fff]+[0-9]+|[0-9]+[\u4e00-\u9fff]+|[\u4e00-\u9fff]{2,}|[a-z0-9]+",
        text.lower(),
    ))


def _has_primary_entity_miss(query_terms: list[str], normalized_text: str) -> bool:
    primary_terms = _primary_terms(query_terms)
    if not primary_terms:
        return False
    return not any(_term_present(term, normalized_text) for term in primary_terms[:3])


def _has_context_entity_miss(context_terms: list[str], normalized_text: str) -> bool:
    named_terms = _named_context_terms(context_terms)
    if not named_terms:
        return False
    return not any(_term_present(term, normalized_text) for term in named_terms[:5])


def _has_context_entity_hit(context_terms: list[str], normalized_text: str) -> bool:
    named_terms = _named_context_terms(context_terms)
    return any(_term_present(term, normalized_text) for term in named_terms[:5])


def _primary_terms(terms: list[str]) -> list[str]:
    return [
        term for term in terms
        if term in {"习近平", "台湾", "台海", "稀土", "出口管制", "mofcom", "jinping"}
        or (len(term) >= 6 and not re.fullmatch(r"[\u4e00-\u9fff]+", term))
    ]


def _has_distinctive_query_miss(query_terms: list[str], normalized_text: str) -> bool:
    distinctive = _distinctive_query_terms(query_terms)
    if not distinctive:
        return False
    return not any(_term_present(term, normalized_text) for term in distinctive[:4])


def _distinctive_query_terms(terms: list[str]) -> list[str]:
    generic = {
        "事件",
        "专家",
        "官方",
        "市场",
        "政策",
        "中文",
        "分析",
        "口碑",
        "近期",
        "视频",
        "赛后",
        "赛事",
        "比赛",
        "票房",
        "解读",
        "风险",
        "博主",
        "时政",
        "时政博主",
        "军事",
        "军事分析",
        "b站",
    }
    output = []
    for term in terms:
        if term in generic:
            continue
        if term in {"bilibili", "youtube"}:
            continue
        if re.fullmatch(r"20\d{2}|[0-9]+", term):
            continue
        if len(term) >= 2:
            output.append(term)
    return output


def _named_context_terms(terms: list[str]) -> list[str]:
    named = {
        "习近平",
        "台湾",
        "台海",
        "稀土",
        "出口管制",
        "mofcom",
        "jinping",
        "taiwan",
        "哪吒",
        "哪吒2",
        "王楚钦",
    }
    return [term for term in terms if term in named]


def _split_chinese_query_term(term: str) -> list[str]:
    if not re.fullmatch(r"[\u4e00-\u9fff]+", term) or len(term) <= 6:
        return [term]
    hits = [phrase for phrase in _CHINESE_PHRASE_HINTS if phrase.lower() in term.lower()]
    if hits:
        return [item for item in hits if item not in _STOPWORDS]
    chunks = [
        chunk for chunk in re.split(
            r"当前|现在|谁|是否|会不会|概率|最厉害|最强|最好|一个|这个|那个",
            term,
        )
        if len(chunk) >= 2 and chunk not in _CHINESE_QUERY_FILLERS
    ]
    return chunks[:6]


def _term_present(term: str, normalized_text: str) -> bool:
    equivalents = {
        "jinping": ["jinping", "习近平"],
        "taiwan": ["taiwan", "台湾", "台海"],
        "mofcom": ["mofcom", "商务部"],
        "哪吒2": ["哪吒2", "哪吒 2", "ne zha 2", "魔童闹海", "魔童鬧海"],
    }
    return any(candidate in normalized_text for candidate in equivalents.get(term, [term]))


def _is_short_or_empty(result: SearchResult) -> bool:
    summary = re.sub(r"\s+", "", result.summary or "")
    title = re.sub(r"\s+", "", result.title or "")
    return len(summary) < 40 and len(title) < 20


def _looks_like_boilerplate(result: SearchResult) -> bool:
    title = _normalize(result.title)
    summary = _normalize(result.summary)
    marker_hits = sum(1 for marker in _BOILERPLATE_MARKERS if marker in result.summary.lower())
    if marker_hits >= 5:
        return True
    if title in _LOW_VALUE_TITLES and len(summary) < 120:
        return True
    nav_terms = {"首页", "机构设置", "办事服务", "公众互动", "关于我们", "联系我们"}
    return len([term for term in nav_terms if term in result.summary]) >= 4


def _is_old_or_undated(result: SearchResult) -> bool:
    if result.published_at is None:
        return True
    return result.published_at.year < 2024


def _domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc.lower()
        return domain[4:] if domain.startswith("www.") else domain
    except Exception:
        return ""


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        output.append(value)
    return output
