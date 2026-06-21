"""Domain routing rules for China-specific source categories."""

from __future__ import annotations

from dataclasses import dataclass, field
from urllib.parse import urlparse

from beatodds.evidence.providers.base import SearchResult

SOCIAL_VIDEO_DOMAINS = {
    "b23.tv",
    "bilibili.com",
    "douyin.com",
    "ixigua.com",
    "kuaishou.com",
    "m.weibo.cn",
    "mp.weixin.qq.com",
    "toutiao.com",
    "weibo.cn",
    "weibo.com",
    "weixin.sogou.com",
    "weixin.qq.com",
    "xiaohongshu.com",
    "xueqiu.com",
    "youtube.com",
    "youtu.be",
    "zhihu.com",
}

VIDEO_DOMAINS = {
    "b23.tv",
    "bilibili.com",
    "youtube.com",
    "youtu.be",
}

MARKET_PROFESSIONAL_DOMAINS = {
    "21jingji.com",
    "caixin.com",
    "cls.cn",
    "cninfo.com.cn",
    "data.eastmoney.com",
    "eastmoney.com",
    "finance.sina.cn",
    "finance.sina.com.cn",
    "gelonghui.com",
    "pdf.dfcfw.com",
    "report.eastmoney.com",
    "stock.finance.sina.com.cn",
    "wallstreetcn.com",
    "xueqiu.com",
    "yicai.com",
}

PROFESSIONAL_MEDIA_DOMAINS = {
    "21jingji.com",
    "36kr.com",
    "caixin.com",
    "cls.cn",
    "guancha.cn",
    "ifeng.com",
    "jiemian.com",
    "nandu.com",
    "sina.cn",
    "sina.com.cn",
    "sohu.com",
    "stcn.com",
    "thepaper.cn",
    "tmtpost.com",
    "yicai.com",
}

OFFICIAL_DOMAIN_SUFFIXES = {
    "gov.cn",
}

OFFICIAL_DOMAINS = {
    "customs.gov.cn",
    "mfa.gov.cn",
    "mofcom.gov.cn",
    "mnd.gov.cn",
    "mod.gov.cn",
    "ndrc.gov.cn",
    "pbc.gov.cn",
    "stats.gov.cn",
}

SEMI_OFFICIAL_DOMAINS = {
    "ce.cn",
    "china.com.cn",
    "chinadiplomacy.org.cn",
    "chinadaily.com.cn",
    "chinanews.com.cn",
    "cctv.cn",
    "cctv.com",
    "cnr.cn",
    "cri.cn",
    "gmw.cn",
    "huanqiu.com",
    "news.cn",
    "people.com.cn",
    "xinhuanet.com",
    "youth.cn",
}

FOREIGN_OR_TAIWAN_CROSSCHECK_DOMAINS = {
    "aei.org",
    "aljazeera.com",
    "apnews.com",
    "bbc.co.uk",
    "bbc.com",
    "bloomberg.com",
    "cna.com.tw",
    "cnn.com",
    "csis.org",
    "defenseone.com",
    "dw.com",
    "foreignpolicy.com",
    "gmfus.org",
    "nytimes.com",
    "rand.org",
    "reuters.com",
    "rfi.fr",
    "scmp.com",
    "taipeitimes.com",
    "tfc-taiwan.org.tw",
    "understandingwar.org",
    "voachinese.com",
    "wsj.com",
}


@dataclass(frozen=True)
class SourceRoutingRule:
    allow_domains: set[str] = field(default_factory=set)
    allow_suffixes: set[str] = field(default_factory=set)
    deny_domains: set[str] = field(default_factory=set)
    deny_suffixes: set[str] = field(default_factory=set)
    allow_other: bool = False


SOURCE_ROUTING_RULES = {
    "expert_social": SourceRoutingRule(
        allow_domains=SOCIAL_VIDEO_DOMAINS,
        deny_domains=FOREIGN_OR_TAIWAN_CROSSCHECK_DOMAINS,
    ),
    "market_professional": SourceRoutingRule(
        allow_domains=MARKET_PROFESSIONAL_DOMAINS | SOCIAL_VIDEO_DOMAINS,
        deny_domains=FOREIGN_OR_TAIWAN_CROSSCHECK_DOMAINS,
    ),
    "professional_media": SourceRoutingRule(
        allow_domains=PROFESSIONAL_MEDIA_DOMAINS,
        allow_suffixes={"com.cn", "cn"},
        deny_domains=(
            FOREIGN_OR_TAIWAN_CROSSCHECK_DOMAINS
            | SOCIAL_VIDEO_DOMAINS
            | SEMI_OFFICIAL_DOMAINS
            | OFFICIAL_DOMAINS
        ),
        deny_suffixes={"gov.cn", "gov.tw", "com.tw"},
    ),
    "official": SourceRoutingRule(
        allow_domains=OFFICIAL_DOMAINS,
        allow_suffixes=OFFICIAL_DOMAIN_SUFFIXES,
        deny_domains=FOREIGN_OR_TAIWAN_CROSSCHECK_DOMAINS | SOCIAL_VIDEO_DOMAINS,
        deny_suffixes={"gov.tw"},
    ),
    "semi_official": SourceRoutingRule(
        allow_domains=SEMI_OFFICIAL_DOMAINS,
        deny_domains=FOREIGN_OR_TAIWAN_CROSSCHECK_DOMAINS | SOCIAL_VIDEO_DOMAINS,
        deny_suffixes={"gov.tw"},
    ),
    "foreign_crosscheck": SourceRoutingRule(
        deny_domains=SOCIAL_VIDEO_DOMAINS,
        allow_other=True,
    ),
    "prediction_sources": SourceRoutingRule(allow_other=True),
    "generic_search_tools": SourceRoutingRule(allow_other=True),
}


def route_results_by_source_category(
    results: list[SearchResult],
    source_category: str,
) -> tuple[list[SearchResult], list[dict]]:
    kept = []
    rejected = []
    for result in results:
        accepted, reason = matches_source_category(result.url, source_category)
        if accepted:
            kept.append(result)
            continue
        rejected.append({
            "title": result.title,
            "url": result.url,
            "source": result.source,
            "reason": reason,
        })
    return kept, rejected


def matches_source_category(url: str, source_category: str) -> tuple[bool, str]:
    rule = SOURCE_ROUTING_RULES.get(source_category)
    if rule is None:
        return True, "no_rule"

    domain = normalize_domain(url)
    if not domain:
        return rule.allow_other, "missing_domain"

    if _matches_any(domain, rule.deny_domains) or _matches_suffix(domain, rule.deny_suffixes):
        return False, "denylist_domain"

    if _matches_any(domain, rule.allow_domains) or _matches_suffix(domain, rule.allow_suffixes):
        return True, "allowlist_domain"

    if rule.allow_other:
        return True, "allow_other"

    return False, "not_in_allowlist"


def is_video_source_url(url: str) -> bool:
    domain = normalize_domain(url)
    parsed_path = urlparse(url or "").path.lower()
    if _matches_any(domain, {"b23.tv"}):
        return True
    if _matches_any(domain, {"bilibili.com"}):
        return "/video/" in parsed_path
    if _matches_any(domain, {"youtu.be"}):
        return bool(parsed_path.strip("/"))
    if _matches_any(domain, {"youtube.com"}):
        return (
            parsed_path == "/watch"
            or parsed_path.startswith("/shorts/")
            or parsed_path.startswith("/live/")
        )
    return False


def normalize_domain(url: str) -> str:
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        domain = ""
    if not domain and "://" not in (url or ""):
        domain = (url or "").lower().split("/", 1)[0]
    if domain.startswith("www."):
        domain = domain[4:]
    return domain.split(":", 1)[0]


def _matches_any(domain: str, patterns: set[str]) -> bool:
    return any(domain == pattern or domain.endswith(f".{pattern}") for pattern in patterns)


def _matches_suffix(domain: str, suffixes: set[str]) -> bool:
    return any(domain == suffix or domain.endswith(f".{suffix}") for suffix in suffixes)
