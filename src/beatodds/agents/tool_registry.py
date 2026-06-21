"""Tool registry and access-tool wrappers for the China forecast harness."""

from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from typing import Callable
from urllib.parse import urlparse

from beatodds.agents.access_tools import (
    ChinaQueryGenerationTool,
    ModelBaselineForecastTool,
    PolymarketContextTool,
    ResourceProcessorTool,
    SourceRegistryExportTool,
)
from beatodds.agents.models import AgentToolResult, AgentToolSpec, slugify
from beatodds.agents.platform_source_access import (
    PLATFORM_RELIABILITY_PRIOR,
    PLATFORM_SOURCE_CATEGORY,
    parse_chinese_platforms,
    platform_candidate_row,
    platform_deep_read_score,
    platform_fallback_queries,
    platform_search_url,
    search_platform_browser,
    search_platform_internal,
)
from beatodds.agents.source_quality import filter_and_rank_results
from beatodds.agents.source_routing import (
    is_video_source_url,
    matches_source_category,
    route_results_by_source_category,
)
from beatodds.agents.video_source_access import search_bilibili_videos, search_youtube_videos
from beatodds.evidence.providers.base import SearchProvider, SearchQuery, SearchResult
from beatodds.evidence.providers.tavily_provider import TavilyProvider

ToolRunner = Callable[..., AgentToolResult]


class ChinaToolRegistry:
    """Small executable registry for harness tools.

    The registry exposes tool capabilities to the agent while keeping concrete
    implementation details in Python. Routing decisions remain with the agent.
    """

    def __init__(self):
        self._specs: dict[str, AgentToolSpec] = {}
        self._runners: dict[str, ToolRunner] = {}

    def register(self, spec: AgentToolSpec, runner: ToolRunner | None = None) -> None:
        self._specs[spec.name] = spec
        if runner is not None:
            self._runners[spec.name] = runner

    def list_tools(self) -> list[AgentToolSpec]:
        return [self._specs[name] for name in sorted(self._specs)]

    def get(self, name: str) -> AgentToolSpec:
        return self._specs[name]

    def run(self, name: str, **kwargs) -> AgentToolResult:
        if name not in self._runners:
            return AgentToolResult(
                tool_name=name,
                source_category=kwargs.get("source_category", "other"),
                query=kwargs.get("query", ""),
                status="skipped",
                error=f"Tool '{name}' has no executable runner.",
            )
        return self._runners[name](**kwargs)


class SearchTool:
    """Provider-neutral search tool wrapper."""

    name = "search_web"

    def __init__(self, provider: SearchProvider | None = None):
        self.provider = provider or TavilyProvider()

    def __call__(
        self,
        query: str,
        source_category: str = "generic_search_tools",
        max_results: int = 5,
        reliability_prior: float = 0.0,
        metadata: dict | None = None,
        context: object | None = None,
        **_: object,
    ) -> AgentToolResult:
        started_at = datetime.now(timezone.utc)
        search_query = SearchQuery(
            query=query,
            provider=self.provider.name,
            source_type=source_category,
            reliability_prior=reliability_prior,
            metadata=metadata or {},
        )
        try:
            provider_max_results = max(max_results * 4, max_results)
            results = self.provider.search(search_query, max_results=provider_max_results)
            allow_self_reference = bool((metadata or {}).get("allow_self_reference"))
            filtered_results = [
                result for result in results
                if allow_self_reference
                or source_category == "prediction_sources"
                or not _is_prediction_market_reference_url(result.url)
            ]
            category_filtered_results, rejected_category = route_results_by_source_category(
                filtered_results,
                source_category,
            )
            quality_filtered_results, rejected_quality = filter_and_rank_results(
                category_filtered_results,
                query=query,
                source_category=source_category,
                context_text=_context_text(context),
            )
            quality_filtered_results = quality_filtered_results[:max_results]
            return AgentToolResult(
                tool_name=self.name,
                source_category=source_category,
                query=query,
                status="ok",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                results=quality_filtered_results,
                metadata={
                    "provider": self.provider.name,
                    "max_results": max_results,
                    "provider_max_results": provider_max_results,
                    "filtered_self_reference_count": len(results) - len(filtered_results),
                    "filtered_category_mismatch_count": (
                        len(filtered_results) - len(category_filtered_results)
                    ),
                    "rejected_category": rejected_category[:8],
                    "filtered_quality_count": len(rejected_quality),
                    "rejected_quality": rejected_quality[:5],
                },
            )
        except Exception as exc:
            return AgentToolResult(
                tool_name=self.name,
                source_category=source_category,
                query=query,
                status="error",
                started_at=started_at,
                finished_at=datetime.now(timezone.utc),
                error=str(exc),
                metadata={
                    "provider": self.provider.name,
                    "max_results": max_results,
                },
            )


class VideoSourceSearchTool:
    """Dedicated Chinese Bilibili/YouTube video discovery entrypoint."""

    name = "search_video_sources"

    def __init__(self, provider: SearchProvider | None = None):
        self.provider = provider or TavilyProvider()

    def __call__(
        self,
        query: str,
        source_category: str = "expert_social",
        max_results: int = 6,
        reliability_prior: float = 0.5,
        platforms: str = "bilibili,youtube",
        workspace: object | None = None,
        context: object | None = None,
        **_: object,
    ) -> AgentToolResult:
        started_at = datetime.now(timezone.utc)
        platform_list = _parse_platforms(platforms)
        issued_queries = _video_queries(query, platform_list)
        raw_results = []
        errors = []
        use_internal_platforms = getattr(self.provider, "name", "") != "mock"
        if use_internal_platforms and "bilibili" in platform_list:
            try:
                raw_results.extend(
                    search_bilibili_videos(
                        query=query,
                        max_results=max_results,
                        source_category=source_category,
                        reliability_prior=reliability_prior,
                    )
                )
            except Exception as exc:
                errors.append(f"bilibili_internal:{exc}")
        if use_internal_platforms and "youtube" in platform_list:
            try:
                raw_results.extend(
                    search_youtube_videos(
                        query=query,
                        max_results=max_results,
                        source_category=source_category,
                        reliability_prior=reliability_prior,
                    )
                )
            except Exception as exc:
                errors.append(f"youtube_internal:{exc}")

        fallback_raw_result_count = 0
        try:
            if not raw_results:
                for issued_query in issued_queries:
                    search_query = SearchQuery(
                        query=issued_query,
                        provider=self.provider.name,
                        source_type=source_category,
                        reliability_prior=reliability_prior,
                        metadata={
                            "video_search": True,
                            "platforms": platform_list,
                            "original_query": query,
                            "fallback": True,
                        },
                    )
                    fallback_results = self.provider.search(
                        search_query,
                        max_results=max(max_results * 3, max_results),
                    )
                    fallback_raw_result_count += len(fallback_results)
                    raw_results.extend(fallback_results)
        except Exception as exc:
            errors.append(f"fallback_provider:{exc}")

        deduped = _dedupe_results(raw_results)
        video_results = [result for result in deduped if is_video_source_url(result.url)]
        routed_results, rejected_category = route_results_by_source_category(
            video_results,
            source_category,
        )
        quality_ranked_results, rejected_quality = filter_and_rank_results(
            routed_results,
            query=query,
            source_category=source_category,
            context_text=_context_text(context),
            min_quality_score=0.05,
        )
        selected_results = quality_ranked_results[:max_results]
        quality_filtered_results = [
            result.model_copy(
                update={
                    "source_type": source_category,
                    "reliability_prior": reliability_prior,
                    "raw_metadata": {
                        **result.raw_metadata,
                        "video_search": {
                            "platforms": platform_list,
                            "original_query": query,
                        },
                    },
                }
            )
            for result in selected_results
        ]
        candidate_set = _video_candidate_set(
            video_results=video_results,
            selected_results=quality_filtered_results,
            ranked_results=quality_ranked_results,
            rejected_category=rejected_category,
            rejected_quality=rejected_quality,
        )
        visit_artifacts = _write_video_source_visit(
            workspace=workspace,
            query=query,
            platforms=platform_list,
            issued_queries=issued_queries,
            candidate_set=candidate_set,
            selected_results=quality_filtered_results,
            errors=errors,
        )
        return AgentToolResult(
            tool_name=self.name,
            source_category=source_category,
            query=query,
            status="error" if errors and not quality_filtered_results else "ok",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            results=quality_filtered_results,
            error="; ".join(errors),
            metadata={
                "provider": self.provider.name,
                "primary_providers": [
                    item for item in ["bilibili_internal", "youtube_internal"]
                    if use_internal_platforms and item.split("_", 1)[0] in platform_list
                ],
                "internal_platforms_enabled": use_internal_platforms,
                "max_results": max_results,
                "platforms": platform_list,
                "issued_queries": issued_queries,
                "raw_result_count": len(raw_results),
                "candidate_set_count": len(candidate_set),
                "candidate_set": candidate_set[:50],
                "fallback_raw_result_count": fallback_raw_result_count,
                "filtered_non_video_count": len(deduped) - len(video_results),
                "filtered_category_mismatch_count": len(video_results) - len(routed_results),
                "rejected_category": rejected_category[:8],
                "filtered_quality_count": len(rejected_quality),
                "rejected_quality": rejected_quality[:5],
            },
            artifact_paths=visit_artifacts,
        )


class ChinesePlatformSearchTool:
    """Platform-aware entrypoint for non-video Chinese source discovery."""

    name = "search_chinese_platforms"

    def __init__(self, provider: SearchProvider | None = None):
        self.provider = provider or TavilyProvider()

    def __call__(
        self,
        query: str,
        source_category: str = "generic_search_tools",
        max_results: int = 5,
        reliability_prior: float = 0.0,
        platforms: str = (
            "weibo,zhihu,wechat,xueqiu,research_reports,newswire"
        ),
        workspace: object | None = None,
        context: object | None = None,
        **_: object,
    ) -> AgentToolResult:
        started_at = datetime.now(timezone.utc)
        platform_list = parse_chinese_platforms(platforms)
        use_internal_platforms = getattr(self.provider, "name", "") != "mock"
        errors: list[str] = []
        statuses: list[dict] = []
        issued_queries: list[dict] = []
        raw_results: list[SearchResult] = []
        rejected_category: list[dict] = []
        rejected_quality: list[dict] = []
        selected_results: list[SearchResult] = []
        candidate_set: list[dict] = []

        for platform in platform_list:
            category = PLATFORM_SOURCE_CATEGORY[platform]
            prior = reliability_prior or PLATFORM_RELIABILITY_PRIOR[platform]
            platform_results: list[SearchResult] = []
            internal_status = "skipped_mock_provider"
            if use_internal_platforms:
                try:
                    internal_results, internal_status = search_platform_internal(
                        platform,
                        query,
                        max_results=max_results,
                    )
                    platform_results.extend(internal_results)
                except Exception as exc:
                    internal_status = f"{platform}_internal_error:{exc}"
                    errors.append(internal_status)

            browser_status = "not_attempted"
            browser_raw_count = 0
            fallback_raw_count = 0
            if use_internal_platforms and _should_run_browser_search(
                internal_status,
                platform_results,
            ):
                try:
                    browser_results, browser_status = search_platform_browser(
                        platform,
                        query,
                        max_results=max(max_results * 3, max_results),
                    )
                    browser_raw_count = len(browser_results)
                    platform_results.extend(browser_results)
                    issued_queries.append({
                        "platform": platform,
                        "query": platform_search_url(platform, query),
                        "access_method": "browser_platform_search",
                        "status": browser_status,
                    })
                except Exception as exc:
                    browser_status = f"{platform}_browser_error:{exc}"
                    errors.append(browser_status)

            if _should_run_domain_fallback(
                internal_status,
                browser_status,
                platform_results,
            ):
                fallback_raw_count += _extend_with_platform_fallback(
                    provider=self.provider,
                    platform_results=platform_results,
                    platform=platform,
                    query=query,
                    category=category,
                    prior=prior,
                    max_results=max_results,
                    issued_queries=issued_queries,
                    errors=errors,
                )

            platform_results = _dedupe_results(platform_results)
            raw_results.extend(platform_results)
            routed_results, platform_rejected_category = _route_mixed_platform_results(
                platform_results,
                category,
            )
            routed_results, platform_rejected_platform_quality = (
                _filter_platform_specific_quality(routed_results, platform, query)
            )
            ranked_results, platform_rejected_quality = filter_and_rank_results(
                routed_results,
                query=query,
                source_category=category,
                context_text=_context_text(context),
                min_quality_score=0.05,
            )
            platform_rejected_quality = [
                *platform_rejected_platform_quality,
                *platform_rejected_quality,
            ]
            ranked_results = sorted(
                ranked_results,
                key=platform_deep_read_score,
                reverse=True,
            )
            platform_selected = ranked_results[:max_results]
            if not platform_selected and fallback_raw_count == 0:
                fallback_raw_count += _extend_with_platform_fallback(
                    provider=self.provider,
                    platform_results=platform_results,
                    platform=platform,
                    query=query,
                    category=category,
                    prior=prior,
                    max_results=max_results,
                    issued_queries=issued_queries,
                    errors=errors,
                )
                platform_results = _dedupe_results(platform_results)
                routed_results, platform_rejected_category = _route_mixed_platform_results(
                    platform_results,
                    category,
                )
                routed_results, platform_rejected_platform_quality = (
                    _filter_platform_specific_quality(routed_results, platform, query)
                )
                ranked_results, platform_rejected_quality = filter_and_rank_results(
                    routed_results,
                    query=query,
                    source_category=category,
                    context_text=_context_text(context),
                    min_quality_score=0.05,
                )
                platform_rejected_quality = [
                    *platform_rejected_platform_quality,
                    *platform_rejected_quality,
                ]
                ranked_results = sorted(
                    ranked_results,
                    key=platform_deep_read_score,
                    reverse=True,
                )
                platform_selected = ranked_results[:max_results]
            selected_results.extend(platform_selected)
            rejected_category.extend(platform_rejected_category)
            rejected_quality.extend(platform_rejected_quality)
            candidate_set.extend(
                _platform_candidate_set(
                    platform_results=platform_results,
                    selected_results=platform_selected,
                    ranked_results=ranked_results,
                    rejected_category=platform_rejected_category,
                    rejected_quality=platform_rejected_quality,
                )
            )
            statuses.append({
                "platform": platform,
                "source_category": category,
                "internal_status": internal_status,
                "internal_enabled": use_internal_platforms,
                "raw_count": len(platform_results),
                "browser_status": browser_status,
                "browser_raw_count": browser_raw_count,
                "fallback_raw_count": fallback_raw_count,
                "selected_count": len(platform_selected),
                "platform_search_url": platform_search_url(platform, query),
            })

        quality_filtered_results = _dedupe_results(selected_results)
        visit_artifacts = _write_platform_source_visit(
            workspace=workspace,
            query=query,
            platforms=platform_list,
            issued_queries=issued_queries,
            candidate_set=candidate_set,
            selected_results=quality_filtered_results,
            statuses=statuses,
            errors=errors,
        )
        return AgentToolResult(
            tool_name=self.name,
            source_category=source_category,
            query=query,
            status="error" if errors and not quality_filtered_results else "ok",
            started_at=started_at,
            finished_at=datetime.now(timezone.utc),
            results=quality_filtered_results,
            error="; ".join(errors),
            metadata={
                "provider": self.provider.name,
                "internal_platforms_enabled": use_internal_platforms,
                "max_results_per_platform": max_results,
                "platforms": platform_list,
                "platform_statuses": statuses,
                "issued_queries": issued_queries,
                "raw_result_count": len(raw_results),
                "candidate_set_count": len(candidate_set),
                "candidate_set": candidate_set[:80],
                "filtered_category_mismatch_count": len(rejected_category),
                "rejected_category": rejected_category[:12],
                "filtered_quality_count": len(rejected_quality),
                "rejected_quality": rejected_quality[:8],
            },
            artifact_paths=visit_artifacts,
        )


def _video_candidate_set(
    video_results: list[SearchResult],
    selected_results: list[SearchResult],
    ranked_results: list[SearchResult],
    rejected_category: list[dict],
    rejected_quality: list[dict],
) -> list[dict]:
    selected_urls = {item.url for item in selected_results}
    ranked_urls = {item.url for item in ranked_results}
    category_reasons = {
        str(item.get("url", "")): str(item.get("reason", "category_rejected"))
        for item in rejected_category
    }
    quality_reasons = {
        str(item.get("url", "")): item.get("reasons", [])
        for item in rejected_quality
    }
    rows = []
    for rank, result in enumerate(video_results, start=1):
        status = "not_selected"
        reason: str | list[str] = "ranked_below_selected_cutoff"
        if result.url in selected_urls:
            status = "selected"
            reason = "selected_by_quality_rank"
        elif result.url in category_reasons:
            status = "rejected_category"
            reason = category_reasons[result.url]
        elif result.url in quality_reasons:
            status = "rejected_quality"
            reason = quality_reasons[result.url]
        elif result.url in ranked_urls:
            status = "not_selected_rank_limit"
            reason = "passed_filter_but_outside_max_results"
        rows.append(_video_candidate_row(result, rank, status, reason))
    return rows


def _platform_candidate_set(
    platform_results: list[SearchResult],
    selected_results: list[SearchResult],
    ranked_results: list[SearchResult],
    rejected_category: list[dict],
    rejected_quality: list[dict],
) -> list[dict]:
    selected_urls = {item.url for item in selected_results}
    ranked_urls = {item.url for item in ranked_results}
    category_reasons = {
        str(item.get("url", "")): str(item.get("reason", "category_rejected"))
        for item in rejected_category
    }
    quality_reasons = {
        str(item.get("url", "")): item.get("reasons", [])
        for item in rejected_quality
    }
    rows = []
    for rank, result in enumerate(platform_results, start=1):
        status = "not_selected"
        reason: str | list[str] = "ranked_below_selected_cutoff"
        if result.url in selected_urls:
            status = "selected"
            reason = "selected_by_quality_rank"
        elif result.url in category_reasons:
            status = "rejected_category"
            reason = category_reasons[result.url]
        elif result.url in quality_reasons:
            status = "rejected_quality"
            reason = quality_reasons[result.url]
        elif result.url in ranked_urls:
            status = "not_selected_rank_limit"
            reason = "passed_filter_but_outside_max_results"
        rows.append(platform_candidate_row(result, rank, status, reason))
    return rows


def _video_candidate_row(
    result: SearchResult,
    rank: int,
    status: str,
    reason: str | list[str],
) -> dict:
    metadata = result.raw_metadata or {}
    view_count = metadata.get("view_count", metadata.get("play_count"))
    return {
        "rank": rank,
        "status": status,
        "reason": reason,
        "title": result.title,
        "url": result.url,
        "platform": metadata.get("platform") or _video_platform_from_url(result.url),
        "author": metadata.get("author") or metadata.get("channel") or result.source,
        "view_count": view_count,
        "comment_count": metadata.get("comment_count"),
        "favorite_count": metadata.get("favorite_count"),
        "like_count": metadata.get("like_count"),
        "duration": metadata.get("duration") or metadata.get("duration_seconds"),
        "published_at": metadata.get("pubdate") or metadata.get("published_at"),
        "search_order": metadata.get("search_order"),
        "provider": result.provider,
        "score": round(result.relevance_score, 3),
        "summary": result.summary,
    }


def _write_video_source_visit(
    workspace: object | None,
    query: str,
    platforms: list[str],
    issued_queries: list[str],
    candidate_set: list[dict],
    selected_results: list[SearchResult],
    errors: list[str],
) -> list[str]:
    if workspace is None or not hasattr(workspace, "paths"):
        return []
    run_dir = getattr(workspace.paths, "run_dir")
    visit_dir = run_dir / "source_visits"
    visit_dir.mkdir(parents=True, exist_ok=True)
    index = len(list(visit_dir.glob("*.json"))) + 1
    slug = slugify(query, fallback="video_search", max_len=48)
    base = f"{index:03d}_{slug}"
    payload = {
        "query": query,
        "platforms": platforms,
        "issued_queries": issued_queries,
        "candidate_count": len(candidate_set),
        "selected_count": len(selected_results),
        "candidates": candidate_set,
        "selected_urls": [item.url for item in selected_results],
        "errors": errors,
    }
    json_path = visit_dir / f"{base}.json"
    md_path = visit_dir / f"{base}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_video_source_visit(payload), encoding="utf-8")
    return [str(json_path), str(md_path)]


def _should_run_browser_search(
    internal_status: str,
    platform_results: list[SearchResult],
) -> bool:
    if internal_status != "ok":
        return True
    if not platform_results:
        return True
    return False


def _should_run_domain_fallback(
    internal_status: str,
    browser_status: str,
    platform_results: list[SearchResult],
) -> bool:
    if platform_results:
        return False
    if internal_status.startswith("skipped_mock_provider"):
        return True
    if browser_status in {"not_attempted", "ok"}:
        return True
    return True


def _extend_with_platform_fallback(
    provider: SearchProvider,
    platform_results: list[SearchResult],
    platform: str,
    query: str,
    category: str,
    prior: float,
    max_results: int,
    issued_queries: list[dict],
    errors: list[str],
) -> int:
    fallback_raw_count = 0
    for fallback_query in platform_fallback_queries(query, platform):
        issued_queries.append({
            "platform": platform,
            "query": fallback_query,
            "access_method": "tavily_domain_fallback",
        })
        try:
            search_query = SearchQuery(
                query=fallback_query,
                provider=provider.name,
                source_type=category,
                reliability_prior=prior,
                metadata={
                    "platform_search": True,
                    "platform": platform,
                    "original_query": query,
                    "fallback": True,
                    "platform_search_url": platform_search_url(platform, query),
                },
            )
            fallback_results = provider.search(
                search_query,
                max_results=max(max_results * 3, max_results),
            )
            fallback_raw_count += len(fallback_results)
            platform_results.extend(
                _tag_platform_results(
                    fallback_results,
                    platform=platform,
                    source_category=category,
                    reliability_prior=prior,
                    access_method="tavily_domain_fallback",
                    fallback_query=fallback_query,
                    original_query=query,
                )
            )
        except Exception as exc:
            errors.append(f"{platform}_fallback_provider:{exc}")
    return fallback_raw_count


def _tag_platform_results(
    results: list[SearchResult],
    platform: str,
    source_category: str,
    reliability_prior: float,
    access_method: str,
    fallback_query: str,
    original_query: str,
) -> list[SearchResult]:
    output = []
    for result in results:
        output.append(result.model_copy(update={
            "source_type": source_category,
            "reliability_prior": reliability_prior,
            "raw_metadata": {
                **(result.raw_metadata or {}),
                "platform": platform,
                "access_method": access_method,
                "fallback_query": fallback_query,
                "original_query": original_query,
                "fallback": True,
                "platform_search_url": platform_search_url(platform, original_query),
            },
        }))
    return output


def _filter_platform_specific_quality(
    results: list[SearchResult],
    platform: str,
    query: str,
) -> tuple[list[SearchResult], list[dict]]:
    kept: list[SearchResult] = []
    rejected: list[dict] = []
    for result in results:
        reason = _platform_specific_reject_reason(result, platform, query)
        if reason:
            rejected.append({
                "title": result.title,
                "url": result.url,
                "source": result.source,
                "source_category": result.source_type,
                "reasons": [reason],
            })
            continue
        kept.append(_normalize_platform_candidate(result, platform))
    return kept, rejected


def _platform_specific_reject_reason(
    result: SearchResult,
    platform: str,
    query: str,
) -> str:
    text = _clean_candidate_text(result)
    if platform == "xueqiu":
        return _xueqiu_reject_reason(result, query, text)
    if platform == "research_reports":
        return _research_report_reject_reason(result, query, text)
    if platform == "newswire":
        return _newswire_reject_reason(result, query, text)
    if platform == "wechat":
        return _wechat_reject_reason(result, text)
    if platform in {"weibo", "zhihu"} and _is_taiwan_query(query):
        if not any(term in text for term in ["台海", "台湾", "两岸", "武统", "统一", "台独"]):
            return f"{platform}_no_taiwan_topic_signal"
    return ""


def _normalize_platform_candidate(result: SearchResult, platform: str) -> SearchResult:
    if platform == "wechat":
        parsed = urlparse(result.url or "")
        if "mp.weixin.qq.com" in parsed.netloc.lower():
            title = result.title or ""
            summary = result.summary or ""
            metadata = result.raw_metadata or {}
            original_query = (
                metadata.get("original_query") or metadata.get("fallback_query") or result.query
            )
            if title.startswith("http") or not summary.strip():
                return result.model_copy(
                    update={
                        "title": f"微信公众号候选文章：{original_query}",
                        "summary": (
                            "Direct mp.weixin candidate discovered for query: "
                            f"{original_query}"
                        ),
                    }
                )
        return result
    if platform != "xueqiu":
        return result
    title = (result.title or "").strip()
    summary = (result.summary or "").strip()
    if _looks_like_xueqiu_timestamp(title) and summary:
        replacement = re.sub(r"^\d{2}-\d{2}\s+\d{2}:\d{2}\s+·\s+来自\S+\s*", "", summary)
        title = replacement[:80] or summary[:80]
        return result.model_copy(update={"title": title})
    return result


def _xueqiu_reject_reason(result: SearchResult, query: str, text: str) -> str:
    parsed = urlparse(result.url or "")
    domain = parsed.netloc.lower()
    path = parsed.path.strip("/")
    if domain.startswith(("broker.", "stockn.", "stockmc.")):
        return "xueqiu_broker_or_stock_quote_noise"
    if not path or path in {"k", "today", "hq"}:
        return "xueqiu_search_or_home_page"
    if path.startswith((
        "about",
        "law",
        "cms/help",
        "edu",
        "snowman/terms",
        "hashtag",
        "S/",
    )):
        return "xueqiu_non_discussion_page"
    if re.fullmatch(r"\d+", path):
        return "xueqiu_profile_only_page"
    if not re.fullmatch(r"\d+/\d+", path):
        return "xueqiu_not_status_discussion_url"
    boilerplate = [
        "开户",
        "投资者教育",
        "风险提示书",
        "服务协议",
        "隐私政策",
        "投诉指引",
        "雪球客服",
        "违法和不良信息",
    ]
    if any(item in text for item in boilerplate):
        return "xueqiu_platform_boilerplate"
    if len(text) < 24:
        return "xueqiu_candidate_too_thin"
    if _is_taiwan_query(query):
        if not any(term in text for term in ["台海", "台湾", "两岸", "武统", "军工"]):
            return "xueqiu_no_taiwan_topic_signal"
        return ""
    if not _has_topic_signal(
        text,
        query,
        extra_terms=["台海", "台湾", "军工", "统一", "两岸", "武统"],
    ):
        return "xueqiu_no_topic_signal"
    return ""


def _research_report_reject_reason(result: SearchResult, query: str, text: str) -> str:
    parsed = urlparse(result.url or "")
    domain = parsed.netloc.lower()
    path = parsed.path.lower()
    noisy_domains = {
        "acttg.eastmoney.com",
        "choice.eastmoney.com",
        "jywg.18.cn",
        "emdesk.eastmoney.com",
    }
    if domain in noisy_domains or domain.endswith(".18.cn"):
        return "research_report_ad_or_terminal_page"
    noisy_terms = [
        "东方财富免费版",
        "东方财富level-2",
        "东方财富策略版",
        "choice金融终端",
        "妙想投研助理",
        "在线交易",
        "证券开户",
    ]
    if any(item.lower() in text.lower() for item in noisy_terms):
        return "research_report_platform_noise"
    taiwan_noise = [
        "台海核电",
        "年度报告",
        "半年报点评",
        "业绩高增长",
        "招股说明书",
        "股东会会议材料",
        "基金",
        "lof",
        "環境、 社會及管治",
        "环境、社会及管治",
    ]
    if any(item.lower() in text.lower() for item in taiwan_noise):
        return "research_report_company_or_fund_noise"
    report_signals = [
        ".pdf",
        "report",
        "research",
        "研报",
        "证券研究",
        "行业研究",
        "宏观研究",
        "策略",
        "pdf.dfcfw.com",
        "cninfo.com.cn",
        "stock.finance.sina.com.cn",
    ]
    haystack = f"{domain} {path} {text}".lower()
    if not any(signal.lower() in haystack for signal in report_signals):
        return "research_report_not_report_like"
    if _is_taiwan_query(query) and not any(
        term in text for term in ["台海局势", "台湾", "两岸", "台海南海", "军工", "地缘"]
    ):
        return "research_report_no_taiwan_topic_signal"
    return ""


def _newswire_reject_reason(result: SearchResult, query: str, text: str) -> str:
    parsed = urlparse(result.url or "")
    domain = parsed.netloc.lower()
    path = parsed.path.lower()
    if len(text) < 20:
        return "newswire_candidate_too_thin"
    if path in {"", "/"}:
        return "newswire_home_page"
    if any(item in text for item in ["网站地图", "版权声明", "招聘", "广告服务"]):
        return "newswire_site_boilerplate"
    allowed = ("news.cn", "xinhuanet.com", "chinanews.com.cn", "people.com.cn")
    if not any(domain == item or domain.endswith(f".{item}") for item in allowed):
        return "newswire_domain_not_allowed"
    if _is_taiwan_query(query) and not any(
        term in text for term in ["台海", "台湾", "两岸", "国台办", "台独", "对台", "涉台"]
    ):
        return "newswire_no_taiwan_topic_signal"
    return ""


def _wechat_reject_reason(result: SearchResult, text: str) -> str:
    parsed = urlparse(result.url or "")
    domain = parsed.netloc.lower()
    if domain.endswith("weixin.sogou.com") and parsed.path.startswith("/link"):
        return "wechat_sogou_redirect_unresolved"
    if "mp.weixin.qq.com" not in domain and "weixin.sogou.com" not in domain:
        return "wechat_domain_not_allowed"
    if len(text) < 16:
        return "wechat_candidate_too_thin"
    if any(item in text for item in ["验证码", "微信公众平台", "安全验证"]):
        return "wechat_verify_or_boilerplate"
    return ""


def _clean_candidate_text(result: SearchResult) -> str:
    return re.sub(r"\s+", " ", " ".join([
        result.title or "",
        result.summary or "",
        result.source or "",
    ])).strip()


def _has_topic_signal(text: str, query: str, extra_terms: list[str]) -> bool:
    if any(term and term in text for term in extra_terms):
        return True
    terms = [term for term in re.split(r"\s+", query) if len(term) >= 2]
    return any(term in text for term in terms)


def _is_taiwan_query(query: str) -> bool:
    return any(term in query for term in ["台海", "台湾", "两岸", "武统", "统一"])


def _looks_like_xueqiu_timestamp(title: str) -> bool:
    return bool(re.fullmatch(r"\d{2}-\d{2}\s+\d{2}:\d{2}\s+·\s+来自\S+", title.strip()))


def _route_mixed_platform_results(
    results: list[SearchResult],
    source_category: str,
) -> tuple[list[SearchResult], list[dict]]:
    kept = []
    rejected = []
    for result in results:
        category = result.source_type or source_category
        accepted, reason = matches_source_category(result.url, category)
        if accepted:
            kept.append(result)
            continue
        rejected.append({
            "title": result.title,
            "url": result.url,
            "source": result.source,
            "source_category": category,
            "reason": reason,
        })
    return kept, rejected


def _write_platform_source_visit(
    workspace: object | None,
    query: str,
    platforms: list[str],
    issued_queries: list[dict],
    candidate_set: list[dict],
    selected_results: list[SearchResult],
    statuses: list[dict],
    errors: list[str],
) -> list[str]:
    if workspace is None or not hasattr(workspace, "paths"):
        return []
    run_dir = getattr(workspace.paths, "run_dir")
    visit_dir = run_dir / "source_visits"
    visit_dir.mkdir(parents=True, exist_ok=True)
    index = len(list(visit_dir.glob("*.json"))) + 1
    slug = slugify(query, fallback="platform_search", max_len=48)
    base = f"{index:03d}_{slug}"
    payload = {
        "query": query,
        "platforms": platforms,
        "issued_queries": issued_queries,
        "candidate_count": len(candidate_set),
        "selected_count": len(selected_results),
        "platform_statuses": statuses,
        "candidates": candidate_set,
        "selected_urls": [item.url for item in selected_results],
        "errors": errors,
    }
    json_path = visit_dir / f"{base}.json"
    md_path = visit_dir / f"{base}.md"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    md_path.write_text(_render_platform_source_visit(payload), encoding="utf-8")
    return [str(json_path), str(md_path)]


def _render_platform_source_visit(payload: dict) -> str:
    lines = [
        "# 中文平台搜索候选集",
        "",
        f"- query: {payload.get('query', '')}",
        f"- platforms: `{', '.join(payload.get('platforms') or [])}`",
        f"- candidate_count: `{payload.get('candidate_count', 0)}`",
        f"- selected_count: `{payload.get('selected_count', 0)}`",
        "",
        "## Platform Status",
        "",
        (
            "| platform | category | internal_status | browser_status | browser_raw | "
            "fallback_raw | selected | search_url |"
        ),
        "|---|---|---|---|---:|---:|---:|---|",
    ]
    for row in payload.get("platform_statuses") or []:
        lines.append(
            (
                "| {platform} | {category} | {status} | {browser_status} | "
                "{browser_raw} | {fallback_raw} | {selected} | {url} |"
            ).format(
                platform=_escape_table(row.get("platform", "")),
                category=_escape_table(row.get("source_category", "")),
                status=_escape_table(row.get("internal_status", "")),
                browser_status=_escape_table(row.get("browser_status", "")),
                browser_raw=_num(row.get("browser_raw_count")),
                fallback_raw=_num(row.get("fallback_raw_count")),
                selected=_num(row.get("selected_count")),
                url=_link_title("platform search", row.get("platform_search_url", "")),
            )
        )
    lines.extend(["", "## Issued Search/Fallback Queries", ""])
    for item in payload.get("issued_queries") or []:
        lines.append(
            "- `{platform}` `{access}`: {query}".format(
                platform=item.get("platform", ""),
                access=item.get("access_method", ""),
                query=item.get("query", ""),
            )
        )
    if payload.get("errors"):
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {item}" for item in payload["errors"])
    lines.extend([
        "",
        "## Candidate Set Before Final Selection",
        "",
        (
            "| # | status | tier | platform | title | author/source | engagement | "
            "author_quality | quality | priority | access | reason |"
        ),
        "|---|---|---|---|---|---|---|---|---:|---|---|---|",
    ])
    for row in payload.get("candidates") or []:
        lines.append(
            "| {rank} | {status} | {tier} | {platform} | {title} | {author} | "
            "{engagement} | {author_quality} | {quality} | {priority} | {access} | "
            "{reason} |".format(
                rank=row.get("rank", ""),
                status=_escape_table(row.get("status", "")),
                tier=_escape_table(row.get("tier", "")),
                platform=_escape_table(row.get("platform", "")),
                title=_link_title(row.get("title", ""), row.get("url", "")),
                author=_escape_table(row.get("author") or row.get("source", "")),
                engagement=_escape_table(row.get("engagement_summary", "")),
                author_quality=_escape_table(row.get("author_quality", "")),
                quality=_escape_table(row.get("quality_score", "")),
                priority=_escape_table(row.get("deep_read_priority", "")),
                access=_escape_table(row.get("access_method", "")),
                reason=_escape_table(row.get("reason", "")),
            )
        )
    lines.extend([
        "",
        "## Platform Tier Rule",
        "",
        "- `T1`: 知乎、微博。优先深筛，必须结合正文、互动、回复/评论、作者质量判断。",
        "- `T2`: 雪球。优先处理高相关市场人士或条件推演，注意正文偏薄风险。",
        "- `T3`: 公众号、研报库、新闻社数据库。作为补充和交叉背景，"
        "正文成功且相关后才进入 evidence。",
        "- `engagement` / `author_quality` 是筛选是否值得细读的依据，不等于材料可信度。",
        "",
    ])
    return "\n".join(lines) + "\n"


def _render_video_source_visit(payload: dict) -> str:
    lines = [
        "# 视频搜索候选集",
        "",
        f"- query: {payload.get('query', '')}",
        f"- platforms: `{', '.join(payload.get('platforms') or [])}`",
        f"- candidate_count: `{payload.get('candidate_count', 0)}`",
        f"- selected_count: `{payload.get('selected_count', 0)}`",
        "",
        "## Issued Queries",
        "",
    ]
    for item in payload.get("issued_queries") or []:
        lines.append(f"- {item}")
    if payload.get("errors"):
        lines.extend(["", "## Errors", ""])
        lines.extend(f"- {item}" for item in payload["errors"])
    lines.extend([
        "",
        "## Candidate Set Before Final Selection",
        "",
        (
            "| # | status | title | author | platform | views | comments | favorites | "
            "likes | order | score | reason |"
        ),
        "|---|---|---|---|---|---:|---:|---:|---:|---|---:|---|",
    ])
    for row in payload.get("candidates") or []:
        lines.append(
            "| {rank} | {status} | {title} | {author} | {platform} | {views} | {comments} | "
            "{favorites} | {likes} | {order} | {score} | {reason} |".format(
                rank=row.get("rank", ""),
                status=_escape_table(row.get("status", "")),
                title=_link_title(row.get("title", ""), row.get("url", "")),
                author=_escape_table(row.get("author", "")),
                platform=_escape_table(row.get("platform", "")),
                views=_num(row.get("view_count")),
                comments=_num(row.get("comment_count")),
                favorites=_num(row.get("favorite_count")),
                likes=_num(row.get("like_count")),
                order=_escape_table(row.get("search_order", "")),
                score=row.get("score", ""),
                reason=_escape_table(row.get("reason", "")),
            )
        )
    return "\n".join(lines) + "\n"


def _video_platform_from_url(url: str) -> str:
    domain = urlparse(url or "").netloc.lower()
    if "bilibili" in domain or "b23.tv" in domain:
        return "bilibili"
    if "youtube" in domain or "youtu.be" in domain:
        return "youtube"
    return domain


def _link_title(title: object, url: object) -> str:
    safe_title = _escape_table(title)
    safe_url = str(url or "").replace(")", "%29")
    return f"[{safe_title}]({safe_url})" if safe_url else safe_title


def _escape_table(value: object) -> str:
    if isinstance(value, list):
        value = ", ".join(str(item) for item in value)
    return str(value or "").replace("|", "\\|").replace("\n", " ")


def _num(value: object) -> str:
    if value is None or value == "":
        return ""
    return str(value)


def default_china_tool_registry(
    provider: SearchProvider | None = None,
    enable_model_baseline_llm: bool = False,
    baseline_model: str | None = None,
    baseline_client: object | None = None,
) -> ChinaToolRegistry:
    registry = ChinaToolRegistry()
    search_tool = SearchTool(provider=provider)
    video_search_tool = VideoSourceSearchTool(provider=provider)
    platform_search_tool = ChinesePlatformSearchTool(provider=provider)
    source_registry_tool = SourceRegistryExportTool()
    query_tool = ChinaQueryGenerationTool()
    polymarket_tool = PolymarketContextTool()
    baseline_tool = ModelBaselineForecastTool(
        enable_llm=enable_model_baseline_llm,
        model=baseline_model,
        client=baseline_client,
    )
    resource_tool = ResourceProcessorTool()
    registry.register(
        AgentToolSpec(
            name=source_registry_tool.name,
            description="把配置好的中国 source registry 导出到当前 workspace。",
            source_categories=["official", "semi_official", "professional_media"],
        ),
        runner=source_registry_tool,
    )
    registry.register(
        AgentToolSpec(
            name=query_tool.name,
            description=(
                "生成中国相关候选 query；只提供搜索候选，不决定 forecast 逻辑。"
            ),
            source_categories=[
                "official",
                "foreign_crosscheck",
                "generic_search_tools",
            ],
        ),
        runner=query_tool,
    )
    registry.register(
        AgentToolSpec(
            name=SearchTool.name,
            description="搜索网页、新闻、域名 source，并返回压缩 search results。",
            source_categories=[
                "official",
                "semi_official",
                "professional_media",
                "market_professional",
                "expert_social",
                "foreign_crosscheck",
                "generic_search_tools",
            ],
        ),
        runner=search_tool,
    )
    registry.register(
        AgentToolSpec(
            name=video_search_tool.name,
            description=(
                "专门搜索 B站/YouTube 中文视频候选，并只保留视频平台域名结果。"
            ),
            source_categories=["expert_social"],
            metadata={
                "platforms": ["bilibili", "youtube"],
                "domain_filter": "bilibili.com, b23.tv, youtube.com, youtu.be",
                "use_case": "中文视频、时政/娱乐/体育博主、访谈、讲评类 source discovery",
            },
        ),
        runner=video_search_tool,
    )
    registry.register(
        AgentToolSpec(
            name=platform_search_tool.name,
            description=(
                "专门搜索微博、知乎、微信公众号、雪球、研报库、新闻社数据库。"
                "优先尝试平台入口；若登录/反爬失败，记录失败并使用域名 fallback。"
            ),
            source_categories=[
                "expert_social",
                "market_professional",
                "semi_official",
                "generic_search_tools",
            ],
            metadata={
                "platforms": [
                    "weibo",
                    "zhihu",
                    "wechat",
                    "xueqiu",
                    "research_reports",
                    "newswire",
                ],
                "platform_category_map": PLATFORM_SOURCE_CATEGORY,
                "access_policy": (
                    "internal platform search first; browser search when API/HTTP fails; "
                    "tavily domain fallback only after browser is blocked or empty"
                ),
                "artifact": "source_visits/*.md candidate set with platform status",
                "use_case": (
                    "微博/知乎/公众号 expert_social，雪球/研报 market_professional，"
                    "新华社/中新社/人民网等 semi_official"
                ),
            },
        ),
        runner=platform_search_tool,
    )
    registry.register(
        AgentToolSpec(
            name=resource_tool.name,
            description=(
                "识别 URL 资源类型，检查视频 metadata/comment/subtitle 状态，"
                "并创建可交给 B站/YouTube render skill 的 manifest、prompt 和 fallback policy。"
            ),
            source_categories=["expert_social"],
            metadata={
                "supported_skills": ["youtube-render-pdf", "bilibili-render-pdf"],
                "subagent_model": "gpt-5.4-mini",
                "spawn_agent_tool": "multi_agent_v1.spawn_agent",
                "skill_paths": {
                    "youtube-render-pdf": (
                        "/home/hjy/.codex/skills/youtube-render-pdf/SKILL.md"
                    ),
                    "bilibili-render-pdf": (
                        "/home/hjy/.codex/skills/bilibili-render-pdf/SKILL.md"
                    ),
                },
                "render_contract": [
                    "render_request.json",
                    "video_report_prompt.md",
                    "subagent_spawn_prompt.md",
                    "artifact_index.md",
                ],
                "expected_outputs": [
                    "video_metadata.json",
                    "video_parse_report.md",
                    "claims.jsonl",
                    "evidence_card.md",
                    "video_report.tex",
                    "video_report.md",
                    "video_report.pdf",
                ],
                "fallback_policy": (
                    "video render/ASR 到 timeout 仍未完成时，记录 coverage gap，"
                    "主 agent 继续 synthesis。"
                ),
            },
        ),
        runner=resource_tool,
    )
    registry.register(
        AgentToolSpec(
            name=polymarket_tool.name,
            description=(
                "从 run context 和本地 Polymarket DB 读取 market/event context。"
            ),
            source_categories=["prediction_sources"],
        ),
        runner=polymarket_tool,
    )
    registry.register(
        AgentToolSpec(
            name=baseline_tool.name,
            description="返回一个市场锚定的验证 baseline forecast。",
            source_categories=["model_baselines"],
        ),
        runner=baseline_tool,
    )
    return registry


def _is_prediction_market_reference_url(url: str) -> bool:
    lowered = (url or "").lower()
    if "polymarket" in lowered:
        return True
    try:
        domain = urlparse(url).netloc.lower()
    except Exception:
        return False
    return domain in {"polypredict.ai"}


def _context_text(context: object | None) -> str:
    if context is None:
        return ""
    fields = ["event_title", "market_question", "resolution_text"]
    return " ".join(str(getattr(context, field, "") or "") for field in fields)


def _parse_platforms(platforms: str) -> list[str]:
    allowed = {"bilibili", "youtube"}
    parsed = [
        item.strip().lower()
        for item in (platforms or "bilibili,youtube").replace(";", ",").split(",")
        if item.strip()
    ]
    output = [item for item in parsed if item in allowed]
    return output or ["bilibili", "youtube"]


def _video_queries(query: str, platforms: list[str]) -> list[str]:
    base_query = query.strip()
    queries = []
    if "bilibili" in platforms:
        queries.append(f"site:bilibili.com {base_query} B站 中文")
    if "youtube" in platforms:
        queries.append(f"site:youtube.com {base_query} YouTube 中文")
    return queries


def _dedupe_results(results: list) -> list:
    seen = set()
    output = []
    for result in results:
        key = result.url or f"{result.source}:{result.title}"
        if key in seen:
            continue
        seen.add(key)
        output.append(result)
    return output
