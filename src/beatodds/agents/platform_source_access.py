"""Platform-specific Chinese source discovery beyond Bilibili/YouTube."""

from __future__ import annotations

import html
import math
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

import httpx

from beatodds.evidence.providers.base import SearchResult

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
    ),
    "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
}

PLATFORM_ALIASES = {
    "weibo": "weibo",
    "微博": "weibo",
    "zhihu": "zhihu",
    "知乎": "zhihu",
    "wechat": "wechat",
    "weixin": "wechat",
    "公众号": "wechat",
    "微信": "wechat",
    "xueqiu": "xueqiu",
    "雪球": "xueqiu",
    "research": "research_reports",
    "research_reports": "research_reports",
    "research-report": "research_reports",
    "研报": "research_reports",
    "研报库": "research_reports",
    "news": "newswire",
    "newswire": "newswire",
    "新闻社": "newswire",
    "新闻社数据库": "newswire",
}

DEFAULT_PLATFORMS = [
    "weibo",
    "zhihu",
    "wechat",
    "xueqiu",
    "research_reports",
    "newswire",
]

PLATFORM_TIERS = {
    "weibo": "T1",
    "zhihu": "T1",
    "xueqiu": "T2",
    "wechat": "T3",
    "research_reports": "T3",
    "newswire": "T3",
}

PLATFORM_TIER_RATIONALE = {
    "T1": "自媒体讨论更自由，优先看观点、论证、互动和作者质量",
    "T2": "管理更严格但质量下限较高，适合市场人士与条件推演",
    "T3": "内容纷杂或网页性质不适合主观论证，作为补充和交叉背景",
}

PLATFORM_SOURCE_CATEGORY = {
    "weibo": "expert_social",
    "zhihu": "expert_social",
    "wechat": "expert_social",
    "xueqiu": "market_professional",
    "research_reports": "market_professional",
    "newswire": "semi_official",
}

PLATFORM_RELIABILITY_PRIOR = {
    "weibo": 0.35,
    "zhihu": 0.40,
    "wechat": 0.45,
    "xueqiu": 0.55,
    "research_reports": 0.60,
    "newswire": 0.65,
}

PLATFORM_BROWSER_DOMAINS = {
    "weibo": {"weibo.com", "m.weibo.cn", "weibo.cn"},
    "zhihu": {"zhihu.com"},
    "wechat": {"mp.weixin.qq.com", "weixin.sogou.com"},
    "xueqiu": {"xueqiu.com"},
    "research_reports": {
        "cninfo.com.cn",
        "dfcfw.com",
        "eastmoney.com",
        "finance.sina.com.cn",
        "report.eastmoney.com",
        "stock.finance.sina.com.cn",
    },
    "newswire": {
        "chinanews.com.cn",
        "news.cn",
        "people.com.cn",
        "xinhuanet.com",
    },
}

PLATFORM_SEARCH_DOMAINS = {
    "weibo": {"s.weibo.com"},
    "zhihu": set(),
    "wechat": {"weixin.sogou.com"},
    "xueqiu": set(),
    "research_reports": {"so.eastmoney.com"},
    "newswire": {"so.news.cn"},
}


def parse_chinese_platforms(platforms: str | list[str] | None) -> list[str]:
    if isinstance(platforms, list):
        raw = platforms
    else:
        raw = (platforms or ",".join(DEFAULT_PLATFORMS)).replace(";", ",").split(",")
    output = []
    for item in raw:
        normalized = PLATFORM_ALIASES.get(str(item).strip().lower())
        if normalized and normalized not in output:
            output.append(normalized)
    return output or list(DEFAULT_PLATFORMS)


def platform_fallback_queries(query: str, platform: str) -> list[str]:
    base = query.strip()
    if platform == "weibo":
        return [f"site:weibo.com OR site:m.weibo.cn {base} 微博"]
    if platform == "zhihu":
        return [f"site:zhihu.com OR site:zhuanlan.zhihu.com {base} 知乎"]
    if platform == "wechat":
        return [
            f"site:mp.weixin.qq.com {base} 公众号",
            f"site:mp.weixin.qq.com/s {base} 公众号",
            f"site:mp.weixin.qq.com {base} 微信 公众号",
            f"\"{base}\" \"mp.weixin.qq.com\"",
        ]
    if platform == "xueqiu":
        return [
            f"site:xueqiu.com {base} 雪球 讨论",
            f"site:xueqiu.com {base} 雪球 台海 军工",
        ]
    if platform == "research_reports":
        return [
            (
                "site:pdf.dfcfw.com OR site:report.eastmoney.com "
                f"OR site:stock.finance.sina.com.cn OR site:cninfo.com.cn {base} 研报"
            ),
            f"site:pdf.dfcfw.com {base} 行业研究 PDF",
            f"site:stock.finance.sina.com.cn {base} 研报 PDF",
        ]
    if platform == "newswire":
        return [
            (
                "site:news.cn OR site:xinhuanet.com OR site:chinanews.com.cn "
                f"OR site:people.com.cn {base}"
            ),
            f"site:news.cn {base} 新华社",
            f"site:chinanews.com.cn {base} 中新社",
        ]
    return [base]


def search_platform_internal(
    platform: str,
    query: str,
    max_results: int = 6,
) -> tuple[list[SearchResult], str]:
    if platform == "weibo":
        return _search_weibo(query, max_results)
    if platform == "wechat":
        return _search_wechat_sogou(query, max_results)
    if platform == "xueqiu":
        return _search_xueqiu(query, max_results)
    if platform == "zhihu":
        return _search_zhihu(query, max_results)
    if platform == "research_reports":
        return _search_eastmoney_page(query, max_results)
    if platform == "newswire":
        return _search_news_page(query, max_results)
    return [], f"unsupported_platform:{platform}"


def search_platform_browser(
    platform: str,
    query: str,
    max_results: int = 6,
    timeout_seconds: int = 35,
) -> tuple[list[SearchResult], str]:
    """Best-effort browser-rendered search before external domain fallback."""
    search_url = platform_search_url(platform, query)
    if not search_url:
        return [], f"{platform}_browser_no_search_url"
    html_text, browser_status = _browser_dump_dom(search_url, timeout_seconds=timeout_seconds)
    if browser_status != "ok":
        return [], f"{platform}_browser_{browser_status}"
    blocked = _browser_block_reason(html_text)
    if blocked:
        return [], f"{platform}_browser_blocked:{blocked}"
    rows = _extract_browser_search_results(
        html_text=html_text,
        search_url=search_url,
        query=query,
        platform=platform,
        max_results=max_results,
    )
    status = "ok" if rows else f"{platform}_browser_no_parseable_results"
    return rows, status


def platform_candidate_row(
    result: SearchResult,
    rank: int,
    status: str,
    reason: str | list[str],
) -> dict:
    metadata = result.raw_metadata or {}
    quality = platform_quality_fields(result)
    return {
        "rank": rank,
        "status": status,
        "reason": reason,
        "title": result.title,
        "url": result.url,
        "platform": metadata.get("platform") or "",
        "source_category": result.source_type,
        "source": result.source,
        "author": metadata.get("author") or metadata.get("account") or result.source,
        "published_at": metadata.get("published_at") or (
            result.published_at.isoformat() if result.published_at else ""
        ),
        "metrics": metadata.get("metrics") or {},
        "tier": quality["tier"],
        "tier_rationale": quality["tier_rationale"],
        "engagement_summary": quality["engagement_summary"],
        "author_quality": quality["author_quality"],
        "quality_score": quality["quality_score"],
        "deep_read_priority": quality["deep_read_priority"],
        "quality_signals": quality["quality_signals"],
        "quality_risks": quality["quality_risks"],
        "selection_note": quality["selection_note"],
        "provider": result.provider,
        "score": round(result.relevance_score, 3),
        "summary": result.summary,
        "access_method": metadata.get("access_method") or result.provider,
    }


def platform_deep_read_score(result: SearchResult) -> float:
    """Rank candidates by topical search quality plus platform/source quality signals."""
    metadata = result.raw_metadata or {}
    search_quality = metadata.get("search_quality") or {}
    base = float(search_quality.get("score") or result.relevance_score or 0.0)
    quality = platform_quality_fields(result)
    return round(base + float(quality["quality_score"]) * 0.45, 4)


def platform_quality_fields(result: SearchResult) -> dict:
    metadata = result.raw_metadata or {}
    platform = metadata.get("platform") or _platform_from_url(result.url)
    tier = PLATFORM_TIERS.get(platform, "T3")
    metrics = metadata.get("metrics") or {}
    content_access = metadata.get("content_access") or {}
    metric_values = _normalized_metrics(metrics)
    engagement_total = sum(
        value for key, value in metric_values.items()
        if key in {
            "likes",
            "like_count",
            "upvotes",
            "voteup_count",
            "comments",
            "comment_count",
            "reply_count",
            "replies",
            "retweets",
            "reposts",
            "favorites",
            "collects",
            "shares",
        }
        and value is not None
    )
    followers = next(
        (
            metric_values[key]
            for key in ["followers", "followers_count", "follower_count", "friends_count"]
            if metric_values.get(key) is not None
        ),
        None,
    )
    tier_bonus = {"T1": 0.32, "T2": 0.24, "T3": 0.10}.get(tier, 0.08)
    engagement_bonus = min(math.log10(engagement_total + 1) / 5.0, 0.28)
    author_bonus = min(math.log10((followers or 0) + 1) / 8.0, 0.18) if followers else 0.0
    body_bonus = (
        0.10 if content_access.get("post_body") or content_access.get("article_body") else 0.0
    )
    quality_score = min(1.0, tier_bonus + engagement_bonus + author_bonus + body_bonus)
    signals = [
        f"tier={tier}",
        f"platform={platform or 'unknown'}",
    ]
    if engagement_total:
        signals.append(f"engagement_total={engagement_total}")
    if followers:
        signals.append(f"followers={followers}")
    if content_access:
        signals.append("content_access=" + ",".join(
            key for key, value in content_access.items() if value
        ))
    risks = []
    if tier == "T3":
        risks.append("lower_priority_source_tier")
    if not engagement_total:
        risks.append("missing_engagement_metrics")
    if len((result.summary or "").strip()) < 60:
        risks.append("thin_preview")
    priority = "high" if tier in {"T1", "T2"} else "medium"
    if risks and tier == "T3":
        priority = "low"
    selection_note = (
        "T1/T2 候选若被 selected，必须先结合互动/作者质量判断，再 process_resource 深读正文"
        if tier in {"T1", "T2"}
        else "T3 候选只作补充；正文成功且相关后才进入 evidence"
    )
    return {
        "platform": platform,
        "tier": tier,
        "tier_rationale": PLATFORM_TIER_RATIONALE.get(tier, ""),
        "engagement_summary": _format_metric_summary(metric_values),
        "author_quality": _format_author_quality(metric_values, metadata),
        "quality_score": round(quality_score, 3),
        "deep_read_priority": priority,
        "quality_signals": signals,
        "quality_risks": risks,
        "selection_note": selection_note,
    }


def _search_weibo(query: str, max_results: int) -> tuple[list[SearchResult], str]:
    url = "https://m.weibo.cn/api/container/getIndex"
    params = {
        "containerid": f"100103type=1&q={query}",
        "page_type": "searchall",
        "page": "1",
    }
    cookies = _platform_cookies("weibo")
    with httpx.Client(
        headers=DEFAULT_HEADERS,
        cookies=cookies or None,
        follow_redirects=True,
        timeout=20.0,
    ) as client:
        response = client.get(url, params=params)
        content_type = response.headers.get("content-type", "")
        if "json" not in content_type:
            html_results, html_status = _search_weibo_html(
                client,
                query,
                max_results=max_results,
                cookies_used=bool(cookies),
            )
            if html_results:
                return html_results, html_status
            return [], f"weibo_internal_blocked:{response.status_code}:{response.url};{html_status}"
        payload = response.json()
    cards = (payload.get("data") or {}).get("cards") or []
    results = []
    for card in cards:
        mblog = card.get("mblog") or {}
        if not mblog:
            continue
        post_id = str(mblog.get("id") or "")
        title = _clean_text(mblog.get("text") or "")[:80] or "微博搜索结果"
        author = (mblog.get("user") or {}).get("screen_name") or "微博"
        summary = _clean_text(mblog.get("text") or "")
        results.append(SearchResult(
            query=query,
            title=title,
            summary=summary[:500],
            url=f"https://m.weibo.cn/detail/{post_id}" if post_id else "https://m.weibo.cn/",
            source=author,
            relevance_score=_text_relevance(query, title, summary),
            provider="weibo_internal",
            source_type=PLATFORM_SOURCE_CATEGORY["weibo"],
            reliability_prior=PLATFORM_RELIABILITY_PRIOR["weibo"],
            raw_metadata={
                "platform": "weibo",
                "author": author,
                "published_at": mblog.get("created_at"),
                "metrics": {
                    "reposts": mblog.get("reposts_count"),
                    "comments": mblog.get("comments_count"),
                    "likes": mblog.get("attitudes_count"),
                    "followers_count": (mblog.get("user") or {}).get("followers_count"),
                    "friends_count": (mblog.get("user") or {}).get("follow_count"),
                },
                "author_profile": {
                    "verified": (mblog.get("user") or {}).get("verified"),
                    "verified_type": (mblog.get("user") or {}).get("verified_type"),
                    "description": (mblog.get("user") or {}).get("description"),
                },
                "access_method": "m.weibo.cn api container/getIndex",
                "content_access": {"search_result": True, "post_body": True},
            },
        ))
    return results[:max_results], "ok" if results else "weibo_no_results"


def _search_weibo_html(
    client: httpx.Client,
    query: str,
    max_results: int,
    cookies_used: bool,
) -> tuple[list[SearchResult], str]:
    response = client.get(
        "https://s.weibo.com/weibo",
        params={"q": query},
        headers={**DEFAULT_HEADERS, "Referer": "https://s.weibo.com/"},
    )
    if response.status_code != 200:
        return [], f"weibo_html_error:{response.status_code}"
    blocks = re.findall(
        r'<div class="card-wrap"[\s\S]*?(?=<div class="card-wrap"|<div class="m-page"|</body>)',
        response.text,
    )
    results = []
    for block in blocks:
        text_match = re.search(
            r'<p class="txt"[^>]*node-type="feed_list_content"[^>]*>(.*?)</p>',
            block,
            re.S,
        )
        if not text_match:
            continue
        summary = _clean_text(text_match.group(1))
        if not summary:
            continue
        author_match = re.search(
            r'<a[^>]+href="//weibo\.com/\d+[^"]*"[^>]*>(.*?)</a>',
            block,
            re.S,
        )
        author = _clean_text(author_match.group(1)) if author_match else "微博"
        time_match = re.search(
            r'<a[^>]+href="(//weibo\.com/[^"]+)"[^>]*>\s*([^<]*\d[^<]*)</a>',
            block,
            re.S,
        )
        url_value = f"https:{time_match.group(1)}" if time_match else "https://s.weibo.com/weibo"
        published_at = _clean_text(time_match.group(2)) if time_match else ""
        metrics = _weibo_html_metrics(block)
        author_profile = _weibo_html_author_profile(block)
        results.append(SearchResult(
            query=query,
            title=summary[:80],
            summary=summary[:500],
            url=url_value,
            source=author,
            relevance_score=_text_relevance(query, summary[:80], summary),
            provider="weibo_internal",
            source_type=PLATFORM_SOURCE_CATEGORY["weibo"],
            reliability_prior=PLATFORM_RELIABILITY_PRIOR["weibo"],
            raw_metadata={
                "platform": "weibo",
                "author": author,
                "published_at": published_at,
                "metrics": metrics,
                "author_profile": author_profile,
                "access_method": (
                    "s.weibo.com html search with cookies"
                    if cookies_used else "s.weibo.com html search"
                ),
                "cookie_file_used": cookies_used,
                "content_access": {"search_result": True, "post_body": True},
            },
        ))
        if len(results) >= max_results:
            break
    return results, "ok" if results else "weibo_html_no_parseable_results"


def _weibo_html_metrics(block: str) -> dict[str, int | None]:
    clean = _clean_text(block)
    numbers = [int(item) for item in re.findall(r"(?<!\d)(\d{1,6})(?!\d)", clean)]
    if len(numbers) >= 3:
        return {"reposts": numbers[-3], "comments": numbers[-2], "likes": numbers[-1]}
    if len(numbers) == 2:
        return {"reposts": None, "comments": numbers[0], "likes": numbers[1]}
    if len(numbers) == 1:
        return {"reposts": None, "comments": None, "likes": numbers[0]}
    return {"reposts": None, "comments": None, "likes": None}


def _weibo_html_author_profile(block: str) -> dict[str, object]:
    clean = _clean_text(block)
    return {
        "verified": "v_plus" in block or "微博认证" in clean or "认证" in clean,
        "raw_profile_hint": clean[:160],
    }


def _search_wechat_sogou(query: str, max_results: int) -> tuple[list[SearchResult], str]:
    url = "https://weixin.sogou.com/weixin"
    params = {"type": "2", "query": query, "page": "1"}
    with httpx.Client(headers=DEFAULT_HEADERS, follow_redirects=True, timeout=20.0) as client:
        response = client.get(url, params=params)
        if response.status_code != 200:
            return [], f"wechat_sogou_error:{response.status_code}"
        text = response.text
    rows = []
    for block in re.findall(r"<li[^>]*>(.*?)</li>", text, flags=re.S):
        title_match = re.search(r"<h3[^>]*>\s*<a[^>]+href=\"([^\"]+)\"[^>]*>(.*?)</a>", block, re.S)
        if not title_match:
            continue
        title = _clean_text(title_match.group(2))
        url_value = _normalize_sogou_url(title_match.group(1))
        url_value = _resolve_sogou_wechat_link(url_value)
        summary_match = re.search(r"<p[^>]+class=\"txt-info\"[^>]*>(.*?)</p>", block, re.S)
        account_match = re.search(
            r"<a[^>]+uigs=\"account_name_[^\"]*\"[^>]*>(.*?)</a>",
            block,
            re.S,
        )
        date_match = re.search(r"<span[^>]+class=\"s2\"[^>]*>(.*?)</span>", block, re.S)
        summary = _clean_text(summary_match.group(1)) if summary_match else ""
        account = _clean_text(account_match.group(1)) if account_match else "搜狗微信"
        published_at = _clean_text(date_match.group(1)) if date_match else ""
        rows.append(SearchResult(
            query=query,
            title=title,
            summary=summary,
            url=url_value,
            source=account,
            relevance_score=_text_relevance(query, title, summary),
            provider="sogou_weixin_internal",
            source_type=PLATFORM_SOURCE_CATEGORY["wechat"],
            reliability_prior=PLATFORM_RELIABILITY_PRIOR["wechat"],
            raw_metadata={
                "platform": "wechat",
                "account": account,
                "published_at": published_at,
                "access_method": "weixin.sogou.com article search",
                "content_access": {"search_result": True, "article_body": False},
            },
        ))
    return rows[:max_results], "ok" if rows else "wechat_sogou_no_results"


def _search_xueqiu(query: str, max_results: int) -> tuple[list[SearchResult], str]:
    url = "https://xueqiu.com/statuses/search.json"
    params = {
        "q": query,
        "count": str(max_results),
        "page": "1",
        "sort": "relevance",
        "source": "all",
    }
    cookies = _platform_cookies("xueqiu")
    with httpx.Client(
        headers=DEFAULT_HEADERS,
        cookies=cookies or None,
        follow_redirects=True,
        timeout=20.0,
    ) as client:
        client.get("https://xueqiu.com/")
        response = client.get(url, params=params, headers={**DEFAULT_HEADERS, "Referer": "https://xueqiu.com/"})
        content_type = response.headers.get("content-type", "")
        if response.status_code != 200 or "json" not in content_type:
            return [], f"xueqiu_api_blocked:{response.status_code}"
        payload = response.json()
    statuses = payload.get("list") or payload.get("statuses") or []
    results = []
    for item in statuses:
        status_id = item.get("id") or item.get("target") or ""
        title = _clean_text(
            item.get("title") or item.get("description") or item.get("text") or "",
        )[:90]
        summary = _clean_text(item.get("text") or item.get("description") or "")
        user = item.get("user") or {}
        author = user.get("screen_name") or item.get("screen_name") or "雪球"
        results.append(SearchResult(
            query=query,
            title=title or "雪球讨论",
            summary=summary[:500],
            url=f"https://xueqiu.com/{user.get('id', '')}/{status_id}".rstrip("/"),
            source=author,
            relevance_score=_text_relevance(query, title, summary),
            provider="xueqiu_internal",
            source_type=PLATFORM_SOURCE_CATEGORY["xueqiu"],
            reliability_prior=PLATFORM_RELIABILITY_PRIOR["xueqiu"],
            raw_metadata={
                "platform": "xueqiu",
                "author": author,
                "published_at": item.get("created_at"),
                "metrics": {
                    "comments": item.get("reply_count"),
                    "likes": item.get("like_count"),
                    "retweets": item.get("retweet_count"),
                    "followers_count": user.get("followers_count"),
                    "friends_count": user.get("friends_count"),
                },
                "author_profile": {
                    "verified": user.get("verified"),
                    "verified_type": user.get("verified_type"),
                    "description": user.get("description"),
                },
                "access_method": "xueqiu statuses/search.json",
                "cookie_file_used": bool(cookies),
                "content_access": {"search_result": True, "post_body": True},
            },
        ))
    return results[:max_results], "ok" if results else "xueqiu_no_results"


def _search_zhihu(query: str, max_results: int) -> tuple[list[SearchResult], str]:
    url = "https://www.zhihu.com/api/v4/search_v3"
    params = {
        "t": "general",
        "q": query,
        "correction": "1",
        "offset": "0",
        "limit": str(max_results),
    }
    cookies = _platform_cookies("zhihu")
    with httpx.Client(
        headers=DEFAULT_HEADERS,
        cookies=cookies or None,
        follow_redirects=True,
        timeout=20.0,
    ) as client:
        response = client.get(url, params=params, headers={**DEFAULT_HEADERS, "Referer": "https://www.zhihu.com/"})
        if response.status_code != 200:
            return [], f"zhihu_api_blocked:{response.status_code}"
        try:
            payload = response.json()
        except Exception:
            return [], "zhihu_api_non_json"
    data = payload.get("data") or []
    results = []
    for item in data:
        obj = item.get("object") or item
        title, summary, url_value, author = _zhihu_result_fields(obj)
        url_value = _normalize_zhihu_url(url_value, obj)
        if not title and not url_value:
            continue
        results.append(SearchResult(
            query=query,
            title=title or "知乎搜索结果",
            summary=summary[:500],
            url=url_value or "https://www.zhihu.com/search",
            source=author or "知乎",
            relevance_score=_text_relevance(query, title, summary),
            provider="zhihu_internal",
            source_type=PLATFORM_SOURCE_CATEGORY["zhihu"],
            reliability_prior=PLATFORM_RELIABILITY_PRIOR["zhihu"],
            raw_metadata={
                "platform": "zhihu",
                "author": author,
                "metrics": _zhihu_metrics(obj),
                "author_profile": _zhihu_author_profile(obj),
                "access_method": (
                    "zhihu search_v3 api with cookies"
                    if cookies else "zhihu search_v3 api"
                ),
                "cookie_file_used": bool(cookies),
                "content_access": {"search_result": True, "article_body": False},
            },
        ))
    return results[:max_results], "ok" if results else "zhihu_no_results"


def _search_eastmoney_page(query: str, max_results: int) -> tuple[list[SearchResult], str]:
    url = "https://so.eastmoney.com/web/s"
    with httpx.Client(headers=DEFAULT_HEADERS, follow_redirects=True, timeout=20.0) as client:
        response = client.get(url, params={"keyword": query})
        if response.status_code != 200:
            return [], f"eastmoney_search_error:{response.status_code}"
    rows = _extract_links_from_html(
        html_text=response.text,
        base_url="https://so.eastmoney.com",
        query=query,
        platform="research_reports",
        provider="eastmoney_page",
        source_type=PLATFORM_SOURCE_CATEGORY["research_reports"],
        reliability_prior=PLATFORM_RELIABILITY_PRIOR["research_reports"],
        max_results=max_results,
    )
    return rows, "ok" if rows else "eastmoney_page_no_parseable_results"


def _search_news_page(query: str, max_results: int) -> tuple[list[SearchResult], str]:
    url = "https://so.news.cn/s"
    with httpx.Client(headers=DEFAULT_HEADERS, follow_redirects=True, timeout=20.0) as client:
        response = client.get(url, params={"keyword": query})
        if response.status_code != 200:
            return [], f"news_search_error:{response.status_code}"
    rows = _extract_links_from_html(
        html_text=response.text,
        base_url="https://so.news.cn",
        query=query,
        platform="newswire",
        provider="news_page",
        source_type=PLATFORM_SOURCE_CATEGORY["newswire"],
        reliability_prior=PLATFORM_RELIABILITY_PRIOR["newswire"],
        max_results=max_results,
    )
    return rows, "ok" if rows else "news_page_no_parseable_results"


def _extract_links_from_html(
    html_text: str,
    base_url: str,
    query: str,
    platform: str,
    provider: str,
    source_type: str,
    reliability_prior: float,
    max_results: int,
) -> list[SearchResult]:
    rows = []
    seen = set()
    for match in re.finditer(r"<a[^>]+href=[\"']([^\"']+)[\"'][^>]*>(.*?)</a>", html_text, re.S):
        href, label = match.groups()
        title = _clean_text(label)
        if len(title) < 6:
            continue
        url_value = urljoin(base_url, html.unescape(href))
        if _reject_html_search_link(platform, url_value, title):
            continue
        domain = urlparse(url_value).netloc.lower()
        if not domain or url_value in seen:
            continue
        seen.add(url_value)
        rows.append(SearchResult(
            query=query,
            title=title[:120],
            summary=title,
            url=url_value,
            source=domain[4:] if domain.startswith("www.") else domain,
            relevance_score=_text_relevance(query, title, ""),
            provider=provider,
            source_type=source_type,
            reliability_prior=reliability_prior,
            raw_metadata={
                "platform": platform,
                "access_method": f"{base_url} html search page",
                "content_access": {"search_result": True, "article_body": False},
            },
        ))
        if len(rows) >= max_results:
            break
    return rows


def _browser_dump_dom(url: str, timeout_seconds: int) -> tuple[str, str]:
    chrome_bin = (
        os.environ.get("BEATODDS_CHROME_BIN")
        or shutil.which("google-chrome")
        or shutil.which("chromium")
        or shutil.which("chromium-browser")
    )
    if not chrome_bin:
        return "", "unavailable"

    cmd = [
        chrome_bin,
        "--headless=new",
        "--disable-gpu",
        "--no-sandbox",
        "--disable-dev-shm-usage",
        "--enable-unsafe-swiftshader",
        "--virtual-time-budget=8000",
    ]
    user_data_dir = os.environ.get("BEATODDS_BROWSER_USER_DATA_DIR", "").strip()
    if user_data_dir:
        cmd.append(f"--user-data-dir={user_data_dir}")
    cmd.extend(["--dump-dom", url])
    try:
        completed = subprocess.run(
            cmd,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except subprocess.TimeoutExpired:
        return "", "timeout"
    except OSError as exc:
        return "", f"error:{exc}"
    if completed.returncode != 0:
        stderr = _clean_text(completed.stderr)[0:120]
        return completed.stdout or "", f"error:{completed.returncode}:{stderr}"
    return completed.stdout or "", "ok"


def _browser_block_reason(html_text: str) -> str:
    text = _clean_text(html_text).lower()
    if not text:
        return "empty_dom"
    markers = [
        ("40362", "zhihu_40362"),
        ("zse-ck", "zhihu_zse_ck"),
        ("当前请求存在异常", "abnormal_request"),
        ("安全验证", "security_check"),
        ("登录后", "login_required"),
        ("verify", "verify"),
        ("captcha", "captcha"),
    ]
    for marker, reason in markers:
        if marker.lower() in text:
            return reason
    return ""


def _extract_browser_search_results(
    html_text: str,
    search_url: str,
    query: str,
    platform: str,
    max_results: int,
) -> list[SearchResult]:
    rows: list[SearchResult] = []
    seen: set[str] = set()
    for match in re.finditer(
        r"<a\b(?P<attrs>[^>]*)>(?P<label>.*?)</a>",
        html_text,
        flags=re.S | re.I,
    ):
        attrs = match.group("attrs")
        href_match = re.search(r"href=[\"']([^\"']+)[\"']", attrs, flags=re.I)
        if not href_match:
            continue
        url_value = _normalize_browser_url(href_match.group(1), search_url)
        if not _is_platform_browser_result_url(platform, url_value):
            continue
        if url_value in seen:
            continue
        label = _clean_text(match.group("label"))
        summary = _clean_text(html_text[max(0, match.start() - 240):match.end() + 360])
        title = label or _title_from_url(url_value)
        if len(title) < 4:
            continue
        seen.add(url_value)
        domain = urlparse(url_value).netloc.lower()
        if domain.startswith("www."):
            domain = domain[4:]
        rows.append(SearchResult(
            query=query,
            title=title[:120],
            summary=summary[:500],
            url=url_value,
            source=domain,
            relevance_score=_text_relevance(query, title, summary),
            provider="browser_platform_search",
            source_type=PLATFORM_SOURCE_CATEGORY[platform],
            reliability_prior=PLATFORM_RELIABILITY_PRIOR[platform],
            raw_metadata={
                "platform": platform,
                "access_method": "browser_platform_search",
                "browser_search_url": search_url,
                "platform_search_url": search_url,
                "content_access": {"search_result": True, "article_body": False},
            },
        ))
        if len(rows) >= max_results:
            break
    return rows


def _normalize_browser_url(url: str, base_url: str) -> str:
    value = html.unescape(url).strip()
    if value.startswith("javascript:") or value.startswith("#"):
        return ""
    if value.startswith("//"):
        value = f"https:{value}"
    return urljoin(base_url, value)


def _is_platform_browser_result_url(platform: str, url_value: str) -> bool:
    if not url_value.startswith(("http://", "https://")):
        return False
    parsed = urlparse(url_value)
    domain = parsed.netloc.lower()
    if domain.startswith("www."):
        domain = domain[4:]
    allowed = PLATFORM_BROWSER_DOMAINS.get(platform, set())
    search_domains = PLATFORM_SEARCH_DOMAINS.get(platform, set())
    if not any(domain == item or domain.endswith(f".{item}") for item in allowed | search_domains):
        return False
    if any(domain == item or domain.endswith(f".{item}") for item in search_domains):
        return _is_allowed_search_redirect(platform, parsed.path)
    path = parsed.path.lower()
    if platform == "zhihu" and path.startswith("/search"):
        return False
    if platform == "xueqiu" and path in {"", "/", "/k"}:
        return False
    if platform == "xueqiu":
        stripped = parsed.path.strip("/")
        return bool(re.fullmatch(r"\d+/\d+", stripped))
    return True


def _is_allowed_search_redirect(platform: str, path: str) -> bool:
    if platform == "wechat":
        return path.startswith("/link")
    if platform == "weibo":
        return "/weibo" not in path
    if platform == "research_reports":
        return not path.startswith("/web/s")
    if platform == "newswire":
        return not path.startswith("/s")
    return False


def _title_from_url(url_value: str) -> str:
    parsed = urlparse(url_value)
    tail = parsed.path.rstrip("/").rsplit("/", 1)[-1]
    return unquote(tail) or parsed.netloc


def _normalize_sogou_url(url: str) -> str:
    value = html.unescape(url)
    if value.startswith("//"):
        value = f"https:{value}"
    if value.startswith("/"):
        value = urljoin("https://weixin.sogou.com", value)
    if not value.startswith(("http://", "https://")):
        return "https://weixin.sogou.com/link?url=" + quote(value, safe="")
    parsed = urlparse(value)
    if parsed.netloc.endswith("weixin.sogou.com") and parsed.path.startswith("/link"):
        return value
    query = parse_qs(parsed.query)
    for key in ["url", "target"]:
        if query.get(key):
            return unquote(query[key][0])
    return value


def _resolve_sogou_wechat_link(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.netloc.endswith("weixin.sogou.com") or not parsed.path.startswith("/link"):
        return url
    try:
        with httpx.Client(headers=DEFAULT_HEADERS, follow_redirects=False, timeout=8.0) as client:
            response = client.get(url, headers={**DEFAULT_HEADERS, "Referer": "https://weixin.sogou.com/"})
    except Exception:
        return url
    location = response.headers.get("location", "")
    if "mp.weixin.qq.com" in location:
        return html.unescape(location)
    match = re.search(r"https?://mp\.weixin\.qq\.com/[^\"'<>\s]+", response.text)
    if match:
        return html.unescape(match.group(0))
    return url


def _reject_html_search_link(platform: str, url_value: str, title: str) -> bool:
    if platform != "research_reports":
        return False
    parsed = urlparse(url_value)
    domain = parsed.netloc.lower()
    path = parsed.path.lower()
    text = f"{title} {url_value}".lower()
    noisy_domains = {
        "acttg.eastmoney.com",
        "choice.eastmoney.com",
        "emdesk.eastmoney.com",
        "jywg.18.cn",
    }
    noisy_terms = [
        "东方财富免费版",
        "东方财富level-2",
        "东方财富策略版",
        "choice金融终端",
        "妙想投研助理",
        "在线交易",
        "开户",
    ]
    if domain in noisy_domains or domain.endswith(".18.cn"):
        return True
    if any(item.lower() in text for item in noisy_terms):
        return True
    report_like = [
        ".pdf",
        "report",
        "research",
        "研报",
        "研究",
        "策略",
        "pdf.dfcfw.com",
        "cninfo.com.cn",
        "stock.finance.sina.com.cn",
    ]
    return not any(item.lower() in text or item.lower() in path for item in report_like)


def _zhihu_result_fields(obj: dict) -> tuple[str, str, str, str]:
    question = obj.get("question") or {}
    author = obj.get("author") or {}
    obj_type = str(obj.get("type") or "")
    title = _clean_text(
        obj.get("title")
        or question.get("title")
        or obj.get("name")
        or obj.get("headline")
        or "",
    )
    summary = _clean_text(
        obj.get("excerpt")
        or obj.get("content")
        or obj.get("description")
        or obj.get("excerpt_new")
        or "",
    )
    url_value = str(
        obj.get("url")
        or obj.get("link")
        or obj.get("target_url")
        or "",
    )
    if not url_value:
        obj_id = obj.get("id")
        question_id = question.get("id")
        if obj_type == "answer" and question_id and obj_id:
            url_value = f"https://www.zhihu.com/question/{question_id}/answer/{obj_id}"
        elif obj_type == "question" and obj_id:
            url_value = f"https://www.zhihu.com/question/{obj_id}"
        elif obj_type in {"article", "zvideo"} and obj_id:
            url_value = f"https://zhuanlan.zhihu.com/p/{obj_id}"
    return title, summary, url_value, _clean_text(author.get("name") or "")


def _zhihu_metrics(obj: dict) -> dict[str, object]:
    question = obj.get("question") or {}
    return {
        "voteup_count": obj.get("voteup_count") or obj.get("upvote_count"),
        "comment_count": obj.get("comment_count"),
        "answer_count": question.get("answer_count"),
        "follower_count": question.get("follower_count"),
        "created_time": obj.get("created_time"),
        "updated_time": obj.get("updated_time"),
    }


def _zhihu_author_profile(obj: dict) -> dict[str, object]:
    author = obj.get("author") or {}
    return {
        "headline": author.get("headline"),
        "badge": author.get("badge"),
        "url_token": author.get("url_token"),
        "user_type": author.get("user_type"),
    }


def _normalize_zhihu_url(url_value: str, obj: dict) -> str:
    question = obj.get("question") or {}
    obj_id = obj.get("id")
    question_id = question.get("id")
    parsed = urlparse(url_value or "")
    path = parsed.path.strip("/")
    if path.startswith("answers/"):
        answer_id = path.rsplit("/", 1)[-1]
        if question_id:
            return f"https://www.zhihu.com/question/{question_id}/answer/{answer_id}"
    if path.startswith("articles/"):
        article_id = path.rsplit("/", 1)[-1]
        return f"https://zhuanlan.zhihu.com/p/{article_id}"
    if path.startswith("questions/"):
        question_path = path.replace("questions/", "question/", 1)
        return f"https://www.zhihu.com/{question_path}"
    if url_value.startswith("http://api.zhihu.com") or url_value.startswith("https://api.zhihu.com"):
        return url_value.replace("api.zhihu.com", "www.zhihu.com")
    if not url_value and obj.get("type") == "answer" and question_id and obj_id:
        return f"https://www.zhihu.com/question/{question_id}/answer/{obj_id}"
    return url_value


def _platform_cookies(platform: str) -> dict[str, str]:
    cookie_paths = _platform_cookie_paths(platform)
    for path in cookie_paths:
        if not path.exists():
            continue
        cookies = _load_netscape_cookies(path)
        if cookies:
            return cookies
    return {}


def _platform_cookie_paths(platform: str) -> list[Path]:
    env_map = {
        "zhihu": "BEATODDS_ZHIHU_COOKIES",
        "weibo": "BEATODDS_WEIBO_COOKIES",
        "xueqiu": "BEATODDS_XUEQIU_COOKIES",
    }
    candidates: list[Path] = []
    if env_value := os.environ.get(env_map.get(platform, ""), ""):
        candidates.append(Path(env_value).expanduser())
    repo_root = Path(__file__).resolve().parents[3]
    if platform == "zhihu":
        candidates.extend([
            repo_root / "data" / "secrets" / "www.zhihu.com_cookies.txt",
            repo_root / "data" / "secrets" / "zhihu_cookies.txt",
        ])
    elif platform == "weibo":
        candidates.extend([
            repo_root / "data" / "secrets" / "weibo.com_cookies.txt",
            repo_root / "data" / "secrets" / "www.weibo.com_cookies.txt",
            repo_root / "data" / "secrets" / "weibo_cookies.txt",
        ])
    elif platform == "xueqiu":
        candidates.extend([
            repo_root / "data" / "secrets" / "xueqiu.com_cookies.txt",
            repo_root / "data" / "secrets" / "www.xueqiu.com_cookies.txt",
            repo_root / "data" / "secrets" / "xueqiu_cookies.txt",
        ])
    return candidates


def _load_netscape_cookies(path: Path) -> dict[str, str]:
    cookies: dict[str, str] = {}
    try:
        lines = path.read_text(encoding="utf-8", errors="ignore").splitlines()
    except OSError:
        return cookies
    for line in lines:
        if not line or line.startswith("#"):
            continue
        parts = line.split("\t")
        if len(parts) < 7:
            continue
        name = parts[5].strip()
        value = parts[6].strip()
        if name and value:
            cookies[name] = value
    return cookies


def _text_relevance(query: str, title: str, summary: str) -> float:
    terms = _terms(query)
    if not terms:
        return 0.5
    text = f"{title} {summary}".lower()
    hits = sum(1 for term in terms if term.lower() in text)
    return max(0.05, min(1.0, 0.25 + 0.75 * hits / len(terms)))


def _terms(text: str) -> list[str]:
    return [
        item for item in re.findall(r"[\u4e00-\u9fff]{2,}|[a-zA-Z0-9]{3,}", text)
        if item.lower() not in {"site", "com", "www", "http", "https"}
    ]


def _clean_text(value: object) -> str:
    text = html.unescape(str(value or ""))
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def _platform_from_url(url_value: str) -> str:
    domain = urlparse(url_value or "").netloc.lower()
    if "weibo" in domain:
        return "weibo"
    if "zhihu" in domain:
        return "zhihu"
    if "xueqiu" in domain:
        return "xueqiu"
    if "weixin" in domain or "sogou" in domain:
        return "wechat"
    if "eastmoney" in domain or "dfcfw" in domain or "cninfo" in domain:
        return "research_reports"
    if any(item in domain for item in ["news.cn", "xinhuanet", "chinanews", "people"]):
        return "newswire"
    return ""


def _normalized_metrics(metrics: dict) -> dict[str, int | None]:
    return {str(key): _metric_int(value) for key, value in (metrics or {}).items()}


def _metric_int(value: object) -> int | None:
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(value)
    text = str(value).strip().replace(",", "")
    multiplier = 1
    if text.endswith("万"):
        multiplier = 10_000
        text = text[:-1]
    elif text.endswith("亿"):
        multiplier = 100_000_000
        text = text[:-1]
    match = re.search(r"\d+(?:\.\d+)?", text)
    if not match:
        return None
    return int(float(match.group(0)) * multiplier)


def _format_metric_summary(metrics: dict[str, int | None]) -> str:
    labels = [
        ("likes", "赞"),
        ("like_count", "赞"),
        ("attitudes", "赞"),
        ("voteup_count", "赞同"),
        ("upvotes", "赞同"),
        ("comments", "评论"),
        ("comment_count", "评论"),
        ("reply_count", "回复"),
        ("replies", "回复"),
        ("reposts", "转发"),
        ("retweets", "转发"),
        ("favorites", "收藏"),
        ("collects", "收藏"),
        ("shares", "分享"),
    ]
    parts = []
    seen = set()
    for key, label in labels:
        value = metrics.get(key)
        if value is None or label in seen:
            continue
        parts.append(f"{label}={value}")
        seen.add(label)
    return "，".join(parts) if parts else "无互动指标"


def _format_author_quality(metrics: dict[str, int | None], metadata: dict) -> str:
    author_profile = metadata.get("author_profile") or {}
    followers = next(
        (
            metrics[key]
            for key in ["followers", "followers_count", "follower_count"]
            if metrics.get(key) is not None
        ),
        None,
    )
    pieces = []
    if followers is not None:
        pieces.append(f"粉丝={followers}")
    if author_profile.get("verified"):
        pieces.append("认证作者")
    if headline := author_profile.get("headline"):
        pieces.append(f"简介={_clean_text(str(headline))[:40]}")
    if description := author_profile.get("description"):
        pieces.append(f"简介={_clean_text(str(description))[:40]}")
    return "；".join(pieces) if pieces else "无作者质量指标"


def platform_search_url(platform: str, query: str) -> str:
    encoded = quote(query)
    if platform == "weibo":
        return f"https://s.weibo.com/weibo?q={encoded}"
    if platform == "zhihu":
        return f"https://www.zhihu.com/search?type=content&q={encoded}"
    if platform == "wechat":
        return f"https://weixin.sogou.com/weixin?type=2&query={encoded}"
    if platform == "xueqiu":
        return f"https://xueqiu.com/k?q={encoded}"
    if platform == "research_reports":
        return f"https://so.eastmoney.com/web/s?keyword={encoded}"
    if platform == "newswire":
        return f"https://so.news.cn/s?keyword={encoded}"
    return ""
