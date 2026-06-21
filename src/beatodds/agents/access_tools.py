"""Executable access tools used by the China forecast harness."""

from __future__ import annotations

import html
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

import httpx

from beatodds.agents.models import AgentRunContext, AgentToolResult, utc_now
from beatodds.agents.platform_source_access import _platform_cookies
from beatodds.agents.video_source_access import inspect_video_resource
from beatodds.agents.workspace import ChinaForecastWorkspace
from beatodds.common.config import get_settings
from beatodds.common.db import get_db, init_schema
from beatodds.evidence.china_sources import ChinaSource, load_china_sources

DEFAULT_VIDEO_RENDER_SUBAGENT_MODEL = "gpt-5.4-mini"
VIDEO_RENDER_LOCK_FILES = ("video_render.lock.json", "asr.lock.json")
TERMINAL_LOCK_STATUSES = {
    "done",
    "complete",
    "completed",
    "failed",
    "error",
    "cancelled",
    "stale",
}
VIDEO_RENDER_SKILL_PATHS = {
    "bilibili-render-pdf": "/home/hjy/.codex/skills/bilibili-render-pdf/SKILL.md",
    "youtube-render-pdf": "/home/hjy/.codex/skills/youtube-render-pdf/SKILL.md",
}


class SourceRegistryExportTool:
    name = "export_source_registry"

    def __call__(
        self,
        workspace: ChinaForecastWorkspace | None = None,
        source_category: str = "official",
        **_: Any,
    ) -> AgentToolResult:
        started_at = utc_now()
        sources = list(load_china_sources())
        payload = {
            "source_count": len(sources),
            "sources": [source.model_dump(mode="json") for source in sources],
            "category_counts": _count_by(sources, "source_type"),
            "topic_counts": _topic_counts(sources),
        }
        artifacts = []
        if workspace is not None:
            json_path = workspace.paths.run_dir / "source_registry.json"
            md_path = workspace.paths.run_dir / "source_registry.md"
            _write_json(json_path, payload)
            md_path.write_text(_render_source_registry(sources), encoding="utf-8")
            artifacts.extend([str(json_path), str(md_path)])
        return AgentToolResult(
            tool_name=self.name,
            source_category=source_category,
            status="ok",
            started_at=started_at,
            finished_at=utc_now(),
            artifact_paths=artifacts,
            payload=payload,
        )


class ChinaQueryGenerationTool:
    name = "generate_china_queries"

    def __call__(
        self,
        context: AgentRunContext,
        workspace: ChinaForecastWorkspace | None = None,
        query: str = "",
        source_category: str = "generic_search_tools",
        **_: Any,
    ) -> AgentToolResult:
        started_at = utc_now()
        text = " ".join([
            context.event_title,
            context.market_question,
            context.resolution_text,
            query,
        ])
        entities = _infer_china_entities(text)
        specs = _build_recommended_searches(text=text, entities=entities)
        payload = {
            "entities": entities,
            "queries": [item["query"] for item in specs],
            "recommended_searches": specs,
        }
        artifacts = []
        if workspace is not None:
            json_path = workspace.paths.run_dir / "generated_queries.json"
            md_path = workspace.paths.run_dir / "generated_queries.md"
            _write_json(json_path, payload)
            md_path.write_text(_render_generated_queries(payload), encoding="utf-8")
            artifacts.extend([str(json_path), str(md_path)])
        return AgentToolResult(
            tool_name=self.name,
            source_category=source_category,
            query=query or context.market_question,
            status="ok",
            started_at=started_at,
            finished_at=utc_now(),
            artifact_paths=artifacts,
            payload=payload,
        )


class PolymarketContextTool:
    name = "read_polymarket_context"

    def __call__(
        self,
        context: AgentRunContext,
        workspace: ChinaForecastWorkspace | None = None,
        source_category: str = "prediction_sources",
        **_: Any,
    ) -> AgentToolResult:
        started_at = utc_now()
        payload = {
            "event_title": context.event_title,
            "market_question": context.market_question,
            "condition_id": context.condition_id,
            "event_id": context.event_id,
            "p_m": context.p_m,
            "deadline": context.deadline.isoformat() if context.deadline else "",
            "resolution_text": context.resolution_text,
            "local_db_market": _read_local_market(context.condition_id),
        }
        artifacts = []
        if workspace is not None:
            json_path = workspace.paths.run_dir / "polymarket_context.json"
            md_path = workspace.paths.run_dir / "polymarket_context.md"
            _write_json(json_path, payload)
            md_path.write_text(_render_polymarket_context(payload), encoding="utf-8")
            artifacts.extend([str(json_path), str(md_path)])
        return AgentToolResult(
            tool_name=self.name,
            source_category=source_category,
            status="ok",
            started_at=started_at,
            finished_at=utc_now(),
            artifact_paths=artifacts,
            payload=payload,
        )


class ModelBaselineForecastTool:
    name = "model_baseline_forecast"

    def __init__(
        self,
        enable_llm: bool = False,
        model: str | None = None,
        client: Any | None = None,
    ):
        self.enable_llm = enable_llm
        self.model = model
        self._client = client

    def __call__(
        self,
        context: AgentRunContext,
        workspace: ChinaForecastWorkspace | None = None,
        source_category: str = "model_baselines",
        **_: Any,
    ) -> AgentToolResult:
        started_at = utc_now()
        payload = self._build_payload(context=context, workspace=workspace)
        artifacts = []
        if workspace is not None:
            json_path = workspace.paths.run_dir / "model_baseline.json"
            md_path = workspace.paths.run_dir / "model_baseline.md"
            _write_json(json_path, payload)
            md_path.write_text(_render_model_baseline(payload), encoding="utf-8")
            artifacts.extend([str(json_path), str(md_path)])
        return AgentToolResult(
            tool_name=self.name,
            source_category=source_category,
            status="ok",
            started_at=started_at,
            finished_at=utc_now(),
            artifact_paths=artifacts,
            payload=payload,
        )

    def _build_payload(
        self,
        context: AgentRunContext,
        workspace: ChinaForecastWorkspace | None,
    ) -> dict[str, Any]:
        p_m = 0.5 if context.p_m is None else context.p_m
        if not self.enable_llm:
            return _market_anchored_baseline_payload(p_m)
        try:
            return self._call_llm_baseline(context=context, workspace=workspace, p_m=p_m)
        except Exception as exc:
            payload = _market_anchored_baseline_payload(p_m)
            payload.update({
                "fallback_reason": str(exc),
                "llm_enabled": True,
            })
            return payload

    def _call_llm_baseline(
        self,
        context: AgentRunContext,
        workspace: ChinaForecastWorkspace | None,
        p_m: float,
    ) -> dict[str, Any]:
        cfg = get_settings()
        model = self.model or cfg.deepseek_model or cfg.openai_cheap_model
        prompt = _render_baseline_prompt(context, workspace, p_m)
        client = self._client or _openai_compatible_client(cfg)
        response = client.chat.completions.create(
            model=model,
            max_tokens=900,
            temperature=0.1,
            response_format={"type": "json_object"},
            messages=[
                {
                    "role": "system",
                    "content": (
                        "你是独立 prediction-market baseline forecaster。"
                        "只返回简洁 JSON。不要输出 hidden chain-of-thought。"
                    ),
                },
                {"role": "user", "content": prompt},
            ],
        )
        raw = response.choices[0].message.content
        data = _json_loads(raw or "{}")
        p_f = _clamp_probability(data.get("p_f", p_m))
        confidence = min(_clamp_probability(data.get("confidence", 0.2)), 0.75)
        return {
            "model": model,
            "p_f": p_f,
            "confidence": confidence,
            "reasoning": str(data.get("reasoning") or "未返回 reasoning。")[:2000],
            "calibration_status": "uncalibrated",
            "llm_enabled": True,
            "evidence_used": data.get("evidence_used", []),
        }


class ResourceProcessorTool:
    name = "process_resource"

    def __call__(
        self,
        url: str = "",
        workspace: ChinaForecastWorkspace | None = None,
        source_category: str = "expert_social",
        max_comments: int = 10,
        fetch_youtube_comments: bool = False,
        render_timeout_seconds: int = 900,
        orientation: str = "forecast_evidence",
        **_: Any,
    ) -> AgentToolResult:
        started_at = utc_now()
        resource_type = _resource_type(url)
        status = "ok" if url else "skipped"
        error = "" if url else "未提供 URL。"
        payload = _resource_stub_payload(url, resource_type)
        if url and resource_type in {"youtube_video", "bilibili_video"}:
            try:
                payload = inspect_video_resource(
                    url,
                    max_comments=max_comments,
                    fetch_youtube_comments=fetch_youtube_comments,
                )
                payload.update({
                    "resource_type": resource_type,
                    "processor_status": "metadata_comments_checked",
                    "expected_outputs": _expected_resource_outputs(resource_type),
                })
            except Exception as exc:
                status = "error"
                error = str(exc)
                payload.update({
                    "processor_status": "error",
                    "error": error,
                })
        elif url and resource_type in {"web_page", "pdf"}:
            try:
                payload = inspect_text_resource(url, resource_type)
            except Exception as exc:
                status = "error"
                error = str(exc)
                payload.update({
                    "processor_status": "error",
                    "error": error,
                })
        artifacts = []
        if workspace is not None and url:
            out_dir = workspace.paths.artifacts_dir / "resources" / _safe_name(url)
            out_dir.mkdir(parents=True, exist_ok=True)
            if resource_type in {"youtube_video", "bilibili_video"}:
                payload["render"] = _video_render_contract(
                    payload=payload,
                    workspace=workspace,
                    out_dir=out_dir,
                    orientation=orientation,
                    timeout_seconds=render_timeout_seconds,
                )
            json_path = out_dir / "resource_processor.json"
            md_path = out_dir / "source_card.md"
            render_request_path = out_dir / "render_request.json"
            render_prompt_path = out_dir / "video_report_prompt.md"
            subagent_prompt_path = out_dir / "subagent_spawn_prompt.md"
            artifact_index_path = out_dir / "artifact_index.md"
            _write_json(json_path, payload)
            md_path.write_text(_render_resource_processor(payload), encoding="utf-8")
            if payload.get("render"):
                _write_json(render_request_path, payload["render"])
                render_prompt_path.write_text(
                    _render_video_report_prompt(payload, workspace),
                    encoding="utf-8",
                )
                subagent_prompt_path.write_text(
                    _render_subagent_spawn_prompt(payload),
                    encoding="utf-8",
                )
                artifact_index_path.write_text(
                    _render_resource_artifact_index(payload, out_dir),
                    encoding="utf-8",
                )
                artifacts.extend([
                    str(render_request_path),
                    str(render_prompt_path),
                    str(subagent_prompt_path),
                    str(artifact_index_path),
                ])
            artifacts.extend([str(json_path), str(md_path)])
        return AgentToolResult(
            tool_name=self.name,
            source_category=source_category,
            query=url,
            status=status,
            started_at=started_at,
            finished_at=utc_now(),
            artifact_paths=artifacts,
            payload=payload,
            error=error,
        )


def _read_local_market(condition_id: str) -> dict[str, Any] | None:
    if not condition_id:
        return None
    try:
        conn = get_db()
        init_schema(conn)
        row = conn.execute(
            """SELECT condition_id, event_id, question, description, resolution_text,
                      category, token_yes_id, token_no_id, close_time, volume_24h,
                      liquidity, slug
               FROM markets WHERE condition_id = ?""",
            [condition_id],
        ).fetchone()
        if not row:
            return None
        columns = [desc[0] for desc in conn.description]
        return dict(zip(columns, row, strict=False))
    except Exception:
        return None


def _infer_china_entities(text: str) -> list[str]:
    lowered = text.lower()
    entities = []
    mapping = {
        "china": "中国",
        "chinese": "中国",
        "taiwan": "台湾",
        "xi": "习近平",
        "hong kong": "香港",
        "semiconductor": "半导体",
        "rare earth": "稀土",
        "export control": "出口管制",
    }
    for key, value in mapping.items():
        if key in lowered or value in text:
            entities.append(value)
    return _dedupe(entities or ["中国"])


def _topic_templates(text: str) -> dict[str, list[str]]:
    lowered = text.lower()
    if (
        "xi jinping" in lowered
        or "习近平" in text
        or "succession" in lowered
        or "general secretary" in lowered
        or "leadership" in lowered
    ):
        return {
            "social": ["2026 习近平 职务 时政博主", "领导层 动向 B站", "习近平 继任 YouTube 中文"],
            "market": [
                "2026 习近平 职务 专家 分析",
                "领导层 稳定性 中文智库",
                "中央政治局 人事 研判",
            ],
            "media": ["2026 习近平 职务 深度报道", "领导层 动向 中文媒体"],
            "official": ["2026 习近平 职务", "中央政治局 人事 任免"],
            "foreign": ["2026 习近平 职务 外媒 交叉检查"],
        }
    if (
        "taiwan" in lowered
        or "台湾" in text
        or "台海" in text
        or "invasion" in lowered
    ):
        return {
            "social": [
                "台海 风险 B站 时政博主",
                "台海 风险 YouTube 中文 分析",
                "台海 军事风险 知乎 微博",
            ],
            "market": ["台海 风险 专家 分析", "台海 军事风险 中文智库", "台海 风险 市场人士"],
            "media": ["台海 风险 中文媒体 深度报道", "台海 军演 中文新闻 分析"],
            "official": ["国防部 台湾", "外交部 台湾", "军演 台海"],
            "foreign": ["台海 风险 外媒 交叉检查"],
        }
    if "export" in lowered or "rare earth" in lowered:
        return {
            "social": ["稀土 出口管制 B站 分析", "出口管制 专家 解读"],
            "market": ["稀土 出口管制 研报", "出口管制 产业链 分析"],
            "media": ["稀土 政策 中文媒体 解读", "出口管制 深度报道"],
            "official": ["出口管制 商务部", "海关 公告", "稀土 政策"],
            "foreign": ["稀土 出口管制 外媒 交叉检查"],
        }
    return {
        "social": ["事件 分析 B站", "事件 分析 YouTube 中文", "专家 解读 知乎 微博"],
        "market": ["事件 专家 分析", "事件 市场人士 解读"],
        "media": ["事件 中文媒体 深度报道", "政策 解读 中文媒体"],
        "official": ["官方 通报", "政策 解读"],
        "foreign": ["外媒 交叉检查"],
    }


def _build_recommended_searches(text: str, entities: list[str]) -> list[dict[str, Any]]:
    templates = _topic_templates(text)
    prefixes = entities[:2] or ["中国"]
    specs: list[dict[str, Any]] = []
    for entity in prefixes:
        specs.extend([
            _search_spec(f"{entity} {query}", "expert_social", 0.45)
            for query in templates["social"]
        ])
        specs.extend([
            _search_spec(
                f"{entity} {templates['social'][0]} B站 视频",
                "expert_social",
                0.45,
                tool_name="search_video_sources",
                platforms="bilibili",
            ),
            _search_spec(
                f"{entity} {templates['social'][1]} YouTube 中文视频",
                "expert_social",
                0.40,
                tool_name="search_video_sources",
                platforms="youtube",
            ),
            _search_spec(
                f"{entity} {templates['social'][-1]} 微博 知乎 公众号",
                "expert_social",
                0.45,
                tool_name="search_chinese_platforms",
                platforms="weibo,zhihu,wechat",
            ),
        ])
    for entity in prefixes:
        specs.extend([
            _search_spec(f"{entity} {query}", "market_professional", 0.55)
            for query in templates["market"]
        ])
        specs.append(_search_spec(
            f"{entity} {templates['market'][0]} 雪球 研报",
            "market_professional",
            0.55,
            tool_name="search_chinese_platforms",
            platforms="xueqiu,research_reports",
        ))
    for entity in prefixes:
        specs.extend([
            _search_spec(f"{entity} {query}", "professional_media", 0.60)
            for query in templates["media"]
        ])
        specs.append(_search_spec(
            f"{entity} {templates['media'][0]} 新闻社",
            "semi_official",
            0.65,
            tool_name="search_chinese_platforms",
            platforms="newswire",
        ))
    for domain in _priority_domains(text):
        for entity in prefixes:
            specs.append(_search_spec(
                f"site:{domain} {entity} {templates['official'][0]}",
                _suggest_category(f"site:{domain} {entity} {templates['official'][0]}"),
                0.75,
            ))
    for entity in prefixes:
        specs.extend([
            _search_spec(f"{entity} {query}", "official", 0.70)
            for query in templates["official"]
        ])
    for entity in prefixes:
        specs.extend([
            _search_spec(f"{entity} {query}", "foreign_crosscheck", 0.35)
            for query in templates["foreign"]
        ])
    return _dedupe_search_specs(specs)


def _search_spec(
    query: str,
    source_category: str,
    reliability_prior: float,
    tool_name: str = "search_web",
    platforms: str = "",
) -> dict[str, Any]:
    output = {
        "query": query,
        "source_category": source_category,
        "reliability_prior": reliability_prior,
        "tool_name": tool_name,
    }
    if platforms:
        output["platforms"] = platforms
    return output


def _dedupe_search_specs(specs: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    output = []
    for spec in specs:
        query = str(spec["query"]).strip()
        if not query or query in seen:
            continue
        seen.add(query)
        output.append({**spec, "query": query})
    return output


def _priority_domains(text: str) -> list[str]:
    lowered = text.lower()
    if (
        "xi jinping" in lowered
        or "习近平" in text
        or "succession" in lowered
        or "general secretary" in lowered
        or "leadership" in lowered
    ):
        return ["gov.cn", "xinhuanet.com", "people.com.cn"]
    if (
        "taiwan" in lowered
        or "台湾" in text
        or "台海" in text
        or "invasion" in lowered
    ):
        return ["mfa.gov.cn", "mnd.gov.cn", "gov.cn"]
    if "export" in lowered or "rare earth" in lowered:
        return ["mofcom.gov.cn", "customs.gov.cn", "gov.cn"]
    return ["gov.cn"]


def _suggest_category(query: str) -> str:
    lowered = query.lower()
    if query.startswith("site:xinhuanet.com") or query.startswith("site:people.com.cn"):
        return "semi_official"
    if query.startswith("site:"):
        return "official"
    if "reuters" in lowered or "bloomberg" in lowered or "外媒" in lowered:
        return "foreign_crosscheck"
    if any(token in query for token in ["官方", "国防部", "外交部", "商务部"]):
        return "official"
    return "generic_search_tools"


def _suggest_reliability(query: str) -> float:
    category = _suggest_category(query)
    return {
        "official": 0.8,
        "foreign_crosscheck": 0.65,
        "generic_search_tools": 0.3,
    }.get(category, 0.3)


def _resource_type(url: str) -> str:
    lowered = url.lower()
    if "youtube.com" in lowered or "youtu.be" in lowered:
        return "youtube_video"
    if "bilibili.com" in lowered or "b23.tv" in lowered:
        return "bilibili_video"
    if lowered.endswith(".pdf") or ".pdf?" in lowered:
        return "pdf"
    return "web_page" if url else "unknown"


def _expected_resource_outputs(resource_type: str) -> list[str]:
    if resource_type in {"youtube_video", "bilibili_video"}:
        return [
            "video_metadata.json",
            "video_parse_report.md",
            "claims.jsonl",
            "evidence_card.md",
            "video_report.tex",
            "video_report.pdf",
            "artifact_index.md",
        ]
    if resource_type == "pdf":
        return ["document_report.md", "claims.md", "source_card.md"]
    if resource_type == "web_page":
        return ["source_card.md", "claims.md"]
    return []


def _resource_stub_payload(url: str, resource_type: str) -> dict[str, Any]:
    return {
        "url": url,
        "resource_type": resource_type,
        "processor_status": "stub",
        "expected_outputs": _expected_resource_outputs(resource_type),
    }


def inspect_text_resource(url: str, resource_type: str) -> dict[str, Any]:
    special_payload = _inspect_special_text_resource(url, resource_type)
    if special_payload is not None:
        return special_payload
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
    }
    cookies = _cookies_for_url(url)
    with httpx.Client(
        headers=headers,
        cookies=cookies or None,
        follow_redirects=True,
        timeout=30.0,
    ) as client:
        response = client.get(url, headers={**headers, "Referer": _referer_for_url(url)})
    content_type = response.headers.get("content-type", "")
    body_text = ""
    processor_status = "text_fetch_http_error"
    if response.status_code == 200:
        if resource_type == "pdf" or "application/pdf" in content_type:
            body_text = _extract_pdf_text(response.content)
            processor_status = "pdf_text_extracted" if body_text else "pdf_text_empty"
        else:
            body_text = _extract_html_body(response.text)
            processor_status = "html_text_extracted" if body_text else "html_text_empty"
    blocked_reason = _blocked_text_reason(response.text, body_text)
    if blocked_reason:
        body_text = ""
        processor_status = blocked_reason
    title = _infer_text_title(response.text if response.status_code == 200 else "", url)
    body_excerpt = body_text[:5000]
    content_access = {
        "metadata": True,
        "body": bool(body_text),
        "body_status": processor_status,
        "cookie_file_used": bool(cookies),
        "http_status": response.status_code,
        "content_type": content_type,
    }
    return {
        "url": url,
        "final_url": str(response.url),
        "resource_type": resource_type,
        "processor_status": processor_status,
        "expected_outputs": _expected_resource_outputs(resource_type),
        "title": title,
        "source": urlparse(str(response.url)).netloc,
        "body_char_count": len(body_text),
        "body_excerpt": body_excerpt,
        "content_access": content_access,
        "assessment": {
            "decision": "accept_for_review" if body_text else "body_unavailable",
            "risks": _text_resource_risks(response.status_code, content_type, body_text),
        },
    }


def _cookies_for_url(url: str) -> dict[str, str]:
    domain = urlparse(url).netloc.lower()
    if "zhihu.com" in domain:
        return _platform_cookies("zhihu")
    if "weibo.com" in domain or "weibo.cn" in domain:
        return _platform_cookies("weibo")
    if "xueqiu.com" in domain:
        return _platform_cookies("xueqiu")
    return {}


def _inspect_special_text_resource(url: str, resource_type: str) -> dict[str, Any] | None:
    domain = urlparse(url).netloc.lower()
    if "weibo.com" in domain:
        return _inspect_weibo_resource(url, resource_type)
    if "mp.weixin.qq.com" in domain:
        return _inspect_wechat_resource(url, resource_type)
    if "xueqiu.com" in domain:
        return _inspect_xueqiu_resource(url, resource_type)
    return None


def _inspect_weibo_resource(url: str, resource_type: str) -> dict[str, Any] | None:
    parsed = urlparse(url)
    parts = [item for item in parsed.path.split("/") if item]
    if len(parts) < 2:
        return None
    status_id = parts[-1]
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://weibo.com/",
    }
    cookies = _platform_cookies("weibo")
    with httpx.Client(
        headers=headers,
        cookies=cookies or None,
        follow_redirects=True,
        timeout=20.0,
    ) as client:
        response = client.get(
            "https://weibo.com/ajax/statuses/show",
            params={"id": status_id},
        )
    if response.status_code != 200:
        return None
    try:
        data = response.json()
    except ValueError:
        return None
    body_text = _clean_text_resource(data.get("text_raw") or data.get("text") or "")
    author = (data.get("user") or {}).get("screen_name") or ""
    title = body_text[:80] or "微博正文"
    content_access = {
        "metadata": True,
        "body": bool(body_text),
        "body_status": "weibo_ajax_status_extracted" if body_text else "weibo_ajax_empty",
        "cookie_file_used": bool(cookies),
        "http_status": response.status_code,
        "content_type": response.headers.get("content-type", ""),
    }
    return {
        "url": url,
        "final_url": url,
        "resource_type": resource_type,
        "processor_status": content_access["body_status"],
        "expected_outputs": _expected_resource_outputs(resource_type),
        "title": title,
        "source": author or "weibo.com",
        "body_char_count": len(body_text),
        "body_excerpt": body_text[:5000],
        "content_access": content_access,
        "assessment": {
            "decision": "accept_for_review" if body_text else "body_unavailable",
            "risks": _text_resource_risks(
                response.status_code,
                response.headers.get("content-type", ""),
                body_text,
            ),
        },
        "raw_metadata": {
            "created_at": data.get("created_at"),
            "mblogid": data.get("mblogid"),
            "reposts": data.get("reposts_count"),
            "comments": data.get("comments_count"),
            "likes": data.get("attitudes_count"),
        },
    }


def _inspect_xueqiu_resource(url: str, resource_type: str) -> dict[str, Any] | None:
    parsed = urlparse(url)
    parts = [item for item in parsed.path.split("/") if item]
    if len(parts) < 2 or not parts[-1].isdigit():
        return None
    status_id = parts[-1]
    cookies = _platform_cookies("xueqiu")
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/125.0 Safari/537.36"
        ),
        "Accept-Language": "zh-CN,zh;q=0.9,en;q=0.8",
        "Referer": "https://xueqiu.com/",
    }
    api_payload = _fetch_xueqiu_json_status(status_id, headers, cookies)
    if api_payload is not None:
        body_text = _clean_text_resource(
            api_payload.get("text") or api_payload.get("description") or "",
        )
        author = (api_payload.get("user") or {}).get("screen_name") or "雪球"
        title = _clean_text_resource(api_payload.get("title") or body_text[:80] or "雪球讨论")
        return _xueqiu_payload(
            url=url,
            resource_type=resource_type,
            title=title,
            source=author,
            body_text=body_text,
            status="xueqiu_json_status_extracted" if body_text else "xueqiu_json_empty",
            http_status=200,
            content_type="application/json",
            cookie_file_used=bool(cookies),
            raw_metadata={
                "created_at": api_payload.get("created_at"),
                "comments": api_payload.get("reply_count"),
                "likes": api_payload.get("like_count"),
                "retweets": api_payload.get("retweet_count"),
            },
        )

    html_text, browser_status = _dump_dom_with_chrome(url, timeout_seconds=35)
    if not html_text:
        return _xueqiu_payload(
            url=url,
            resource_type=resource_type,
            title="雪球讨论",
            source="xueqiu.com",
            body_text="",
            status=f"xueqiu_browser_{browser_status}",
            http_status=0,
            content_type="browser/dom",
            cookie_file_used=bool(cookies),
            raw_metadata={},
        )
    title, body_text = _extract_xueqiu_browser_text(html_text, url)
    blocked_reason = _blocked_text_reason(html_text, body_text)
    status = "xueqiu_browser_dom_extracted" if body_text and not blocked_reason else (
        blocked_reason or "xueqiu_browser_dom_empty"
    )
    if blocked_reason:
        body_text = ""
    return _xueqiu_payload(
        url=url,
        resource_type=resource_type,
        title=title,
        source="xueqiu.com",
        body_text=body_text,
        status=status,
        http_status=200,
        content_type="browser/dom",
        cookie_file_used=bool(cookies),
        raw_metadata={"browser_status": browser_status},
    )


def _inspect_wechat_resource(url: str, resource_type: str) -> dict[str, Any] | None:
    html_text, browser_status = _dump_dom_with_chrome(
        url,
        timeout_seconds=45,
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
            "Mobile/15E148 Safari/604.1 MicroMessenger/8.0.49"
        ),
        window_size="390,844",
    )
    if not html_text:
        return _wechat_payload(
            url=url,
            resource_type=resource_type,
            title="微信公众号文章",
            body_text="",
            status=f"wechat_browser_{browser_status}",
            raw_metadata={},
        )
    title, body_text = _extract_wechat_browser_text(html_text, url)
    blocked_reason = _blocked_text_reason(html_text, body_text)
    status = "wechat_mobile_browser_extracted" if body_text and not blocked_reason else (
        blocked_reason or "wechat_mobile_browser_empty"
    )
    if blocked_reason:
        body_text = ""
    return _wechat_payload(
        url=url,
        resource_type=resource_type,
        title=title,
        body_text=body_text,
        status=status,
        raw_metadata={"browser_status": browser_status},
    )


def _fetch_xueqiu_json_status(
    status_id: str,
    headers: dict[str, str],
    cookies: dict[str, str],
) -> dict[str, Any] | None:
    try:
        with httpx.Client(
            headers=headers,
            cookies=cookies or None,
            follow_redirects=True,
            timeout=20.0,
        ) as client:
            response = client.get(
                "https://xueqiu.com/statuses/show.json",
                params={"id": status_id},
            )
    except Exception:
        return None
    if response.status_code != 200 or "json" not in response.headers.get("content-type", ""):
        return None
    try:
        payload = response.json()
    except ValueError:
        return None
    return payload if isinstance(payload, dict) else None


def _dump_dom_with_chrome(
    url: str,
    timeout_seconds: int,
    user_agent: str | None = None,
    window_size: str | None = None,
) -> tuple[str, str]:
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
    if user_agent:
        cmd.append(f"--user-agent={user_agent}")
    if window_size:
        cmd.append(f"--window-size={window_size}")
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
    if completed.returncode != 0 and not completed.stdout:
        return "", f"error:{completed.returncode}"
    return completed.stdout or "", "ok"


def _extract_xueqiu_browser_text(html_text: str, url: str) -> tuple[str, str]:
    title = _meta_content(html_text, "keywords") or _infer_text_title(html_text, url)
    description = _meta_content(html_text, "description")
    body_text = _clean_text_resource(description) if description else _extract_html_body(html_text)
    return _clean_text_resource(title)[:180] or "雪球讨论", body_text


def _extract_wechat_browser_text(html_text: str, url: str) -> tuple[str, str]:
    title = _extract_element_text(html_text, r'id=["\']activity-name["\']')
    if not title:
        title = _extract_element_text(html_text, r'class=["\'][^"\']*rich_media_title[^"\']*["\']')
    if not title:
        title = _infer_text_title(html_text, url)
    content = _extract_element_text(html_text, r'id=["\']js_content["\']')
    if not content:
        content = _extract_html_body(html_text)
    return _clean_text_resource(title)[:180] or "微信公众号文章", content


def _extract_element_text(html_text: str, attr_pattern: str) -> str:
    match = re.search(
        rf"<(?P<tag>[a-z0-9]+)[^>]*{attr_pattern}[^>]*>(?P<body>.*?)</(?P=tag)>",
        html_text,
        flags=re.S | re.I,
    )
    if not match:
        return ""
    return _clean_text_resource(match.group("body"))


def _wechat_payload(
    *,
    url: str,
    resource_type: str,
    title: str,
    body_text: str,
    status: str,
    raw_metadata: dict[str, Any],
) -> dict[str, Any]:
    content_access = {
        "metadata": True,
        "body": bool(body_text),
        "body_status": status,
        "cookie_file_used": False,
        "http_status": 200 if body_text else 0,
        "content_type": "browser/dom",
    }
    return {
        "url": url,
        "final_url": url,
        "resource_type": resource_type,
        "processor_status": status,
        "expected_outputs": _expected_resource_outputs(resource_type),
        "title": title,
        "source": "mp.weixin.qq.com",
        "body_char_count": len(body_text),
        "body_excerpt": body_text[:5000],
        "content_access": content_access,
        "assessment": {
            "decision": "accept_for_review" if body_text else "body_unavailable",
            "risks": _text_resource_risks(content_access["http_status"], "browser/dom", body_text),
        },
        "raw_metadata": raw_metadata,
    }


def _meta_content(html_text: str, name: str) -> str:
    patterns = [
        rf'<meta[^>]+name=["\']{re.escape(name)}["\'][^>]+content=["\']([^"\']+)["\']',
        rf'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']{re.escape(name)}["\']',
    ]
    for pattern in patterns:
        match = re.search(pattern, html_text, flags=re.S | re.I)
        if match:
            return html.unescape(match.group(1)).strip()
    return ""


def _xueqiu_payload(
    *,
    url: str,
    resource_type: str,
    title: str,
    source: str,
    body_text: str,
    status: str,
    http_status: int,
    content_type: str,
    cookie_file_used: bool,
    raw_metadata: dict[str, Any],
) -> dict[str, Any]:
    content_access = {
        "metadata": True,
        "body": bool(body_text),
        "body_status": status,
        "cookie_file_used": cookie_file_used,
        "http_status": http_status,
        "content_type": content_type,
    }
    return {
        "url": url,
        "final_url": url,
        "resource_type": resource_type,
        "processor_status": status,
        "expected_outputs": _expected_resource_outputs(resource_type),
        "title": title,
        "source": source,
        "body_char_count": len(body_text),
        "body_excerpt": body_text[:5000],
        "content_access": content_access,
        "assessment": {
            "decision": "accept_for_review" if body_text else "body_unavailable",
            "risks": _text_resource_risks(http_status, content_type, body_text),
        },
        "raw_metadata": raw_metadata,
    }


def _referer_for_url(url: str) -> str:
    domain = urlparse(url).netloc.lower()
    if "zhihu.com" in domain:
        return "https://www.zhihu.com/"
    if "weibo.com" in domain:
        return "https://s.weibo.com/"
    if "xueqiu.com" in domain:
        return "https://xueqiu.com/"
    return f"{urlparse(url).scheme}://{domain}/" if domain else ""


def _extract_html_body(text: str) -> str:
    text = re.sub(r"<script.*?</script>", " ", text, flags=re.S | re.I)
    text = re.sub(r"<style.*?</style>", " ", text, flags=re.S | re.I)
    candidates = re.findall(
        r'<(?:article|main|div|section)[^>]+(?:class|id)="[^"]*(?:RichContent|Post-RichText|article|content|detail|txt|main)[^"]*"[^>]*>(.*?)</(?:article|main|div|section)>',
        text,
        flags=re.S | re.I,
    )
    raw = " ".join(candidates) if candidates else text
    clean = html.unescape(re.sub(r"<[^>]+>", " ", raw))
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


def _blocked_text_reason(raw_text: str, body_text: str) -> str:
    compact = _clean_text_resource(raw_text)[:2000].lower()
    if "aliyun_waf" in compact or "_waf_" in compact:
        return "blocked_waf_page"
    if "验证码" in compact or "verifycode" in compact or "captcha" in compact:
        return "blocked_captcha_page"
    if "当前请求存在异常" in compact or "40362" in compact:
        return "blocked_abnormal_request"
    if len(_clean_text_resource(body_text)) < 80:
        return "body_too_thin"
    return ""


def _infer_text_title(text: str, url: str) -> str:
    match = re.search(r"<title[^>]*>(.*?)</title>", text, flags=re.S | re.I)
    if match:
        title = html.unescape(re.sub(r"\s+", " ", match.group(1))).strip()
        if title:
            return title[:180]
    path_tail = urlparse(url).path.rstrip("/").rsplit("/", 1)[-1]
    return path_tail or urlparse(url).netloc


def _extract_pdf_text(content: bytes) -> str:
    if not content:
        return ""
    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = Path(tmpdir) / "source.pdf"
        txt_path = Path(tmpdir) / "source.txt"
        pdf_path.write_bytes(content)
        result = subprocess.run(
            ["pdftotext", "-layout", str(pdf_path), str(txt_path)],
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if result.returncode != 0 or not txt_path.exists():
            return ""
        return txt_path.read_text(encoding="utf-8", errors="ignore").strip()


def _text_resource_risks(status_code: int, content_type: str, body_text: str) -> list[str]:
    risks = []
    if status_code != 200:
        risks.append(f"http_status_{status_code}")
    if not body_text:
        risks.append("body_unavailable")
    if "pdf" in content_type.lower() and not body_text:
        risks.append("pdf_text_extraction_failed")
    if len(body_text) < 400:
        risks.append("thin_body")
    return risks


def _clean_text_resource(value: object) -> str:
    clean = html.unescape(str(value or ""))
    clean = re.sub(r"<script.*?</script>", " ", clean, flags=re.S | re.I)
    clean = re.sub(r"<style.*?</style>", " ", clean, flags=re.S | re.I)
    clean = re.sub(r"<[^>]+>", " ", clean)
    clean = re.sub(r"\s+", " ", clean)
    return clean.strip()


def _video_render_contract(
    payload: dict[str, Any],
    workspace: ChinaForecastWorkspace,
    out_dir: Path,
    orientation: str,
    timeout_seconds: int,
) -> dict[str, Any]:
    resource_type = str(payload.get("resource_type") or "")
    skill_name = (
        "bilibili-render-pdf"
        if resource_type == "bilibili_video"
        else "youtube-render-pdf"
    )
    skill_path = VIDEO_RENDER_SKILL_PATHS[skill_name]
    expected = _expected_resource_outputs(resource_type)
    contract = {
        "render_status": "required",
        "skill_name": skill_name,
        "skill_path": skill_path,
        "orientation": orientation or "forecast_evidence",
        "output_dir": str(out_dir),
        "subagent_prompt_path": str(out_dir / "video_report_prompt.md"),
        "subagent_spawn_prompt_path": str(out_dir / "subagent_spawn_prompt.md"),
        "source_url": payload.get("url", ""),
        "event_title": workspace.context.event_title,
        "market_question": workspace.context.market_question,
        "resolution_text": workspace.context.resolution_text,
        "timeout_seconds": max(60, int(timeout_seconds or 900)),
        "expected_outputs": expected,
        "existing_outputs": [],
        "missing_outputs": expected,
        "main_agent_model": workspace.context.agent_model or workspace.context.agent_name,
        "subagent_model": DEFAULT_VIDEO_RENDER_SUBAGENT_MODEL,
        "spawn_agent_tool": "multi_agent_v1.spawn_agent",
        "spawn_agent_args": {
            "agent_type": "worker",
            "model": DEFAULT_VIDEO_RENDER_SUBAGENT_MODEL,
            "reasoning_effort": "high",
            "items": [
                {
                    "type": "skill",
                    "name": skill_name,
                    "path": skill_path,
                },
                {
                    "type": "text",
                    "text": (
                        "请读取 output_dir/video_report_prompt.md，使用该 skill 处理视频，"
                        "只写入 output_dir，不修改 repo 代码。"
                    ),
                },
            ],
        },
        "wait_policy": (
            "主 agent 用 wait_agent 等待 subagent。若超过 timeout_seconds，"
            "停止等待并在 audit.md 记录 coverage gap。"
        ),
        "subagent_write_scope": str(out_dir),
        "completion_check": [
            "video_report.pdf exists",
            "evidence_card.md exists",
            "artifact_index.md exists",
            "no active video_render.lock.json or asr.lock.json remains unless status is terminal",
        ],
        "lock_paths": {
            "video_render_lock": str(out_dir / "video_render.lock.json"),
            "asr_lock": str(out_dir / "asr.lock.json"),
        },
        "status_poll_files": [
            str(out_dir / "resource_processor.json"),
            str(out_dir / "artifact_index.md"),
            str(out_dir / "video_render.lock.json"),
            str(out_dir / "asr.lock.json"),
            str(out_dir / "download_log.md"),
        ],
        "lock_policy": (
            "Worker 开始视频下载/render 前写 video_render.lock.json；开始 Whisper/ASR 前写 "
            "asr.lock.json。lock 至少包含 status、pid、started_at、updated_at、command。"
            "完成后把 status 改为 complete；失败后改为 failed 并写 error。"
        ),
        "main_agent_takeover_policy": (
            "主 agent 在接手视频处理、重跑下载、ffmpeg 或 Whisper 前，必须先运行 "
            "sync_resource_status，并检查 video_render.lock.json、asr.lock.json、"
            "download_log.md 和活跃进程。若发现 active lock 或仍在运行的 worker/ASR，"
            "只能等待、轮询或记录超时 gap，禁止启动第二个 ASR。"
        ),
        "fallback_policy": (
            "若到 timeout 仍未生成 video_report.pdf/evidence_card.md，主 agent 必须把"
            "该视频记录为 coverage gap 或 low-signal source，然后继续 synthesis；"
            "不得无限等待视频 ASR/render，也不得在 active lock 存在时重复启动 ASR。"
        ),
        "subagent_instruction": (
            f"启动 `{DEFAULT_VIDEO_RENDER_SUBAGENT_MODEL}`，通过 items[type=skill] 传入"
            f"`{skill_name}` skill，orientation=`{orientation or 'forecast_evidence'}`，"
            "只写入 output_dir。worker 必须写 video_render.lock.json；若需要 Whisper/ASR，"
            "必须写 asr.lock.json。最终产出 video_report.pdf、video_parse_report.md、"
            "claims.jsonl、evidence_card.md 和 artifact_index.md。"
        ),
    }
    payload["render"] = contract
    _apply_video_render_artifact_state(
        payload,
        out_dir,
        assume_artifact_index_exists=True,
    )
    return payload["render"]


def sync_video_render_artifacts(
    resource_dir: Path,
    *,
    rewrite_artifact_index: bool = False,
) -> dict[str, Any]:
    """Refresh a video resource processor manifest from files actually on disk."""
    resource_dir = Path(resource_dir)
    json_path = resource_dir / "resource_processor.json"
    if not json_path.exists():
        raise FileNotFoundError(f"Missing resource_processor.json: {json_path}")
    payload = json.loads(json_path.read_text(encoding="utf-8"))
    if payload.get("resource_type") not in {"youtube_video", "bilibili_video"}:
        return payload
    _apply_video_render_artifact_state(payload, resource_dir)
    _write_json(json_path, payload)
    render = payload.get("render")
    if render:
        _write_json(resource_dir / "render_request.json", render)
    (resource_dir / "source_card.md").write_text(
        _render_resource_processor(payload),
        encoding="utf-8",
    )
    if rewrite_artifact_index:
        (resource_dir / "artifact_index.md").write_text(
            _render_resource_artifact_index(payload, resource_dir),
            encoding="utf-8",
        )
    return payload


def _apply_video_render_artifact_state(
    payload: dict[str, Any],
    out_dir: Path,
    *,
    assume_artifact_index_exists: bool = False,
) -> None:
    resource_type = str(payload.get("resource_type") or "")
    expected = _expected_resource_outputs(resource_type)
    existing = [
        item for item in expected
        if (out_dir / item).exists()
        or (assume_artifact_index_exists and item == "artifact_index.md")
    ]
    missing = [item for item in expected if item not in existing]
    transcript_exists = _has_transcript_artifact(out_dir)
    body_report_exists = any(
        (out_dir / name).exists()
        for name in ["video_parse_report.md", "evidence_card.md", "video_report.pdf"]
    )
    locks = _scan_video_render_locks(out_dir)
    active_locks = [item for item in locks if item["active"]]
    complete_core = all(
        (out_dir / name).exists()
        for name in ["video_report.pdf", "evidence_card.md", "artifact_index.md"]
    ) and any(
        (out_dir / name).exists()
        for name in ["video_parse_report.md", "claims.jsonl", "transcript.srt", "audio.srt"]
    )
    artifact_index_text = _read_text(out_dir / "artifact_index.md").lower()
    coverage_gap = any(
        marker in artifact_index_text
        for marker in [
            "coverage_gap",
            "coverage gap",
            "render_status=coverage_gap",
            "render_status: coverage_gap",
            "下载失败",
            "asr 失败",
            "subtitle failure",
        ]
    )
    if complete_core:
        render_status = "complete"
    elif active_locks:
        render_status = "in_progress"
    elif body_report_exists or transcript_exists:
        render_status = "partial"
    elif coverage_gap:
        render_status = "coverage_gap"
    else:
        render_status = "required"

    render = payload.get("render") or {}
    render.update({
        "render_status": render_status,
        "expected_outputs": expected,
        "existing_outputs": existing,
        "missing_outputs": missing,
        "completion_checked_at": utc_now().isoformat(),
        "completion_source": "local_artifact_scan",
        "lock_files": locks,
        "active_locks": active_locks,
        "asr_lock_active": any(item["path"] == "asr.lock.json" for item in active_locks),
        "body_artifacts": {
            "transcript_srt": (out_dir / "transcript.srt").exists(),
            "audio_srt": (out_dir / "audio.srt").exists(),
            "video_parse_report": (out_dir / "video_parse_report.md").exists(),
            "evidence_card": (out_dir / "evidence_card.md").exists(),
            "video_report_pdf": (out_dir / "video_report.pdf").exists(),
        },
    })
    payload["render"] = render

    access = dict(payload.get("content_access") or {})
    if render_status == "complete":
        access["video_body"] = True
        access["video_body_status"] = "complete_report"
    elif render_status == "partial":
        access["video_body"] = True
        access["video_body_status"] = "partial_artifact"
    elif render_status == "coverage_gap":
        access["video_body"] = False
        access["video_body_status"] = "coverage_gap"
    elif render_status == "in_progress":
        access.setdefault("video_body", False)
        access["video_body_status"] = "render_in_progress"
    else:
        access.setdefault("video_body", False)
        access["video_body_status"] = "required"
    access["asr_in_progress"] = any(item["path"] == "asr.lock.json" for item in active_locks)
    if transcript_exists:
        access["transcript"] = True
        access["requires_asr"] = False
        access["asr_completed"] = True
    elif access["asr_in_progress"]:
        access["requires_asr"] = True
        access["asr_completed"] = False
    payload["content_access"] = access
    if render_status in {"complete", "partial", "coverage_gap", "in_progress"}:
        payload["processor_status"] = f"video_render_{render_status}"


def _scan_video_render_locks(out_dir: Path) -> list[dict[str, Any]]:
    locks = []
    for name in VIDEO_RENDER_LOCK_FILES:
        path = out_dir / name
        if not path.exists():
            continue
        raw = _read_text(path)
        try:
            data = json.loads(raw) if raw.strip() else {}
            if not isinstance(data, dict):
                data = {}
        except json.JSONDecodeError:
            data = {"parse_error": "invalid_json"}
        status = str(data.get("status") or "running").strip().lower()
        mtime = path.stat().st_mtime
        pid = data.get("pid")
        pid_alive = _pid_is_alive(pid)
        stale = status not in TERMINAL_LOCK_STATUSES and pid is not None and not pid_alive
        active = status not in TERMINAL_LOCK_STATUSES and not stale
        if stale:
            status = "stale"
        locks.append({
            "path": name,
            "status": status,
            "active": active,
            "pid": pid,
            "pid_alive": pid_alive,
            "stale": stale,
            "command": data.get("command", ""),
            "started_at": data.get("started_at", ""),
            "updated_at": data.get("updated_at", ""),
            "error": data.get("error", ""),
            "mtime": mtime,
        })
    return locks


def _has_transcript_artifact(out_dir: Path) -> bool:
    transcript_names = ["transcript.srt", "audio.srt", "transcript.txt", "audio.txt"]
    if any((out_dir / name).exists() for name in transcript_names):
        return True
    return any(path.is_file() for path in out_dir.glob("*.srt"))


def _pid_is_alive(pid: Any) -> bool | None:
    if pid is None:
        return None
    try:
        value = int(pid)
    except (TypeError, ValueError):
        return None
    if value <= 0:
        return False
    try:
        os.kill(value, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def _render_video_report_prompt(
    payload: dict[str, Any],
    workspace: ChinaForecastWorkspace,
) -> str:
    render = payload.get("render") or {}
    return "\n".join([
        "# Video Render Task",
        "",
        f"- skill: `{render.get('skill_name', '')}`",
        f"- skill_path: `{render.get('skill_path', '')}`",
        f"- subagent_model: `{render.get('subagent_model', '')}`",
        f"- orientation: `{render.get('orientation', 'forecast_evidence')}`",
        f"- source_url: {payload.get('url', '')}",
        f"- output_dir: `{render.get('output_dir', '')}`",
        f"- timeout_seconds: `{render.get('timeout_seconds', '')}`",
        f"- video_render_lock: `{render.get('lock_paths', {}).get('video_render_lock', '')}`",
        f"- asr_lock: `{render.get('lock_paths', {}).get('asr_lock', '')}`",
        "",
        "## Forecast Context",
        "",
        f"- event_title: {workspace.context.event_title}",
        f"- market_question: {workspace.context.market_question}",
        "",
        "## Resolution",
        "",
        workspace.context.resolution_text or "未提供 resolution。",
        "",
        "## Required Output",
        "",
        "- `video_metadata.json`",
        "- `video_parse_report.md`",
        "- `claims.jsonl`",
        "- `evidence_card.md`",
        "- `video_report.tex`",
        "- `video_report.pdf`",
        "- `artifact_index.md`",
        "",
        "## Rules",
        "",
        "- 全部自然语言使用中文。",
        "- 本任务由 `gpt-5.4-mini` worker 执行；必须使用上方指定的 video render skill。",
        "- 写入范围只限 output_dir；不要修改 repo 代码或其他 forecast workspace。",
        "- 所有面向人类的报告、证据卡和 artifact index 必须优先使用视频标题；"
        "BVID、YouTube id 和 URL 只作为括号内辅助标识，不得只用 BV 号称呼视频。",
        "- 开始下载/render 前必须写 `video_render.lock.json`，状态为 `running`；"
        "完成后改为 `complete`，失败后改为 `failed` 并写明 error。",
        "- 如果需要 Whisper/ASR，启动前必须写 `asr.lock.json`，状态为 `running`；"
        "ASR 完成后改为 `complete`，失败后改为 `failed`。",
        "- 如果发现 output_dir 已有 active `video_render.lock.json` 或 `asr.lock.json`，"
        "不得重复启动下载、ffmpeg 或 Whisper；只能等待、复用已生成 artifact、"
        "或在超时后记录失败原因。",
        "- 不要把标题、简介、评论或 metadata 当作 video-body evidence。",
        "- 必须区分视频中可见事实、口播/字幕主张、作者解释、评论区反应、未验证部分。",
        "- 说明该视频与 forecast resolution 的关系。",
        (
            "- 如果字幕/ASR/下载失败，写明失败原因，产出 `artifact_index.md` "
            "和可用的 metadata/card。"
        ),
        (
            "- 到 timeout 仍未完成时，主 agent 继续 forecast synthesis，并在 "
            "`audit.md` 记录 coverage gap。"
        ),
        "",
    ])


def _render_subagent_spawn_prompt(payload: dict[str, Any]) -> str:
    render = payload.get("render") or {}
    args = render.get("spawn_agent_args") or {}
    args_json = json.dumps(args, ensure_ascii=False, indent=2)
    return "\n".join([
        "# Subagent Spawn Prompt",
        "",
        "主 agent 在需要完整视频正文证据时，按这个任务启动 worker：",
        "",
        f"- spawn_tool: `{render.get('spawn_agent_tool', 'multi_agent_v1.spawn_agent')}`",
        f"- worker_model: `{render.get('subagent_model', DEFAULT_VIDEO_RENDER_SUBAGENT_MODEL)}`",
        f"- skill: `{render.get('skill_name', '')}`",
        f"- skill_path: `{render.get('skill_path', '')}`",
        f"- source_url: {render.get('source_url', payload.get('url', ''))}",
        f"- output_dir: `{render.get('output_dir', '')}`",
        f"- prompt_path: `{render.get('subagent_prompt_path', '')}`",
        f"- video_render_lock: `{render.get('lock_paths', {}).get('video_render_lock', '')}`",
        f"- asr_lock: `{render.get('lock_paths', {}).get('asr_lock', '')}`",
        "",
        "## spawn_agent args",
        "",
        "```json",
        args_json,
        "```",
        "",
        "## Worker Message",
        "",
        "请执行以下任务：",
        "",
        f"1. 阅读 `{render.get('subagent_prompt_path', '')}`。",
        f"2. 使用 `{render.get('skill_name', '')}` skill 处理视频。",
        f"3. 只写入 `{render.get('output_dir', '')}`。",
        "4. 开始下载/render 前写 `video_render.lock.json`，需要 Whisper/ASR 时写 "
        "`asr.lock.json`；完成后把对应 lock 的 status 改为 `complete`，失败后改为 "
        "`failed` 并写 error。",
        "5. 输出 `video_report.pdf`、`video_parse_report.md`、`claims.jsonl`、"
        "`evidence_card.md` 和 `artifact_index.md`。",
        "6. 如果字幕、ASR、下载或渲染失败，仍写出可用 metadata/card，并在 "
        "`artifact_index.md` 说明失败原因。",
        "",
        "## Parent Validation",
        "",
        "- `video_report.pdf` 存在。",
        "- `evidence_card.md` 存在。",
        "- `artifact_index.md` 存在。",
        "- 若 lock 仍为 active，主 agent 不得自行重复 ASR；必须等待、轮询 "
        "`sync_resource_status --all` 或记录 timeout gap。",
        "- 若未完成，主 agent 在 `audit.md` 记录 coverage gap，并继续 synthesis。",
        "",
    ])


def _render_resource_artifact_index(payload: dict[str, Any], out_dir: Path) -> str:
    render = payload.get("render") or {}
    lines = [
        "# Resource Artifact Index",
        "",
        f"- url: {payload.get('url', '')}",
        f"- resource_type: `{payload.get('resource_type', '')}`",
        f"- processor_status: `{payload.get('processor_status', '')}`",
        f"- render_status: `{render.get('render_status', '')}`",
        f"- skill_name: `{render.get('skill_name', '')}`",
        f"- skill_path: `{render.get('skill_path', '')}`",
        f"- subagent_model: `{render.get('subagent_model', '')}`",
        f"- output_dir: `{out_dir}`",
        f"- video_render_lock: `{render.get('lock_paths', {}).get('video_render_lock', '')}`",
        f"- asr_lock: `{render.get('lock_paths', {}).get('asr_lock', '')}`",
        "",
        "## Expected Outputs",
        "",
    ]
    for item in render.get("expected_outputs") or payload.get("expected_outputs") or []:
        status = "exists" if (out_dir / item).exists() else "missing"
        lines.append(f"- `{item}`: `{status}`")
    lines.extend([
        "",
        "## Lock Policy",
        "",
        render.get("lock_policy", ""),
        "",
        "## Main Agent Takeover Policy",
        "",
        render.get("main_agent_takeover_policy", ""),
        "",
        "## Fallback Policy",
        "",
        render.get("fallback_policy", ""),
        "",
    ])
    return "\n".join(lines)


def _market_anchored_baseline_payload(p_m: float) -> dict[str, Any]:
    return {
        "model": "market_anchored_stub",
        "p_f": p_m,
        "confidence": 0.15,
        "reasoning": (
            "验证 stub：没有调用独立模型。baseline 暂时保持在 p_m。"
        ),
        "calibration_status": "uncalibrated",
        "llm_enabled": False,
    }


def _render_baseline_prompt(
    context: AgentRunContext,
    workspace: ChinaForecastWorkspace | None,
    p_m: float,
) -> str:
    claims = ""
    source_summaries = ""
    if workspace is not None:
        claims = _read_text(workspace.paths.run_dir / "claims.md")[-3000:]
        cards = []
        for path in sorted(workspace.paths.sources_dir.glob("*/*.md"))[-8:]:
            cards.append(f"## {path}\n{_read_text(path)[:1200]}")
        source_summaries = "\n\n".join(cards)[-7000:]
    return "\n".join([
        "返回 JSON，key 使用：p_f, confidence, calibration_status, reasoning, evidence_used。",
        "可以把 p_m 当作后置参考；只有证据支持时才调整。",
        "除非使用了经验校准，否则 calibration_status 必须是 uncalibrated。",
        "confidence 表示证据置信度，不是事件发生确定性；避免过度自信。",
        "reasoning 使用中文，保持简洁、可审计。",
        "",
        "## 市场",
        f"event_title: {context.event_title}",
        f"market_question: {context.market_question}",
        f"condition_id: {context.condition_id}",
        f"resolution_text: {context.resolution_text}",
        f"p_m: {p_m}",
        "",
        "## Claims",
        claims or "暂无 claims 记录。",
        "",
        "## 来源卡片",
        source_summaries or "暂无 source cards 记录。",
    ])


def _openai_compatible_client(cfg):
    import openai

    if cfg.deepseek_api_key:
        return openai.OpenAI(api_key=cfg.deepseek_api_key, base_url=cfg.deepseek_base_url)
    if cfg.openai_api_key:
        return openai.OpenAI(api_key=cfg.openai_api_key)
    raise RuntimeError("No DeepSeek/OpenAI key configured for model baseline.")


def _json_loads(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        cleaned = parts[1] if len(parts) > 1 else cleaned
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    data = json.loads(cleaned.strip())
    if not isinstance(data, dict):
        raise RuntimeError("Baseline LLM response must be a JSON object.")
    return data


def _clamp_probability(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.5
    return max(0.0, min(1.0, number))


def _read_text(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8")


def _count_by(sources: list[ChinaSource], field: str) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources:
        key = str(getattr(source, field))
        counts[key] = counts.get(key, 0) + 1
    return counts


def _topic_counts(sources: list[ChinaSource]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for source in sources:
        for topic in source.topics:
            counts[topic] = counts.get(topic, 0) + 1
    return counts


def _dedupe(values: list[str]) -> list[str]:
    seen = set()
    output = []
    for value in values:
        if value and value not in seen:
            seen.add(value)
            output.append(value)
    return output


def _safe_name(value: str) -> str:
    from beatodds.agents.models import slugify

    return slugify(value, fallback="resource", max_len=72)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2, default=str),
        encoding="utf-8",
    )


def _render_source_registry(sources: list[ChinaSource]) -> str:
    lines = ["# 来源 Registry", ""]
    for source in sources:
        lines.append(
            f"- `{source.source_type}` {source.name} ({source.domain}), "
            f"可靠性先验={source.reliability_prior:.2f}, topics={', '.join(source.topics)}"
        )
    lines.append("")
    return "\n".join(lines)


def _render_generated_queries(payload: dict[str, Any]) -> str:
    lines = ["# 生成的中国相关 Queries", "", "## 实体", ""]
    lines.extend(f"- {entity}" for entity in payload["entities"])
    lines.extend(["", "## 推荐搜索", ""])
    for item in payload["recommended_searches"]:
        lines.append(
            f"- `{item.get('tool_name', 'search_web')}` / `{item['source_category']}` "
            f"可靠性先验={item['reliability_prior']:.2f}: {item['query']}"
        )
    lines.append("")
    return "\n".join(lines)


def _render_polymarket_context(payload: dict[str, Any]) -> str:
    return "\n".join([
        "# Polymarket 上下文",
        "",
        f"- event_title: {payload['event_title']}",
        f"- market_question: {payload['market_question']}",
        f"- condition_id: `{payload['condition_id']}`",
        f"- p_m: `{payload['p_m']}`",
        f"- deadline: `{payload['deadline']}`",
        "",
        "## Resolution 规则",
        "",
        payload["resolution_text"] or "",
        "",
    ])


def _render_model_baseline(payload: dict[str, Any]) -> str:
    return "\n".join([
        "# 模型 Baseline Forecast",
        "",
        f"- model: `{payload['model']}`",
        f"- p_f: `{payload['p_f']:.4f}`",
        f"- confidence: `{payload['confidence']:.2f}`",
        f"- calibration_status: `{payload['calibration_status']}`",
        "",
        payload["reasoning"],
        "",
    ])


def _render_resource_processor(payload: dict[str, Any]) -> str:
    if payload.get("resource_type") in {"web_page", "pdf"}:
        return _render_text_resource_processor(payload)
    if payload.get("processor_status") != "stub":
        return _render_video_resource_processor(payload)
    return "\n".join([
        "# 资源处理 Stub",
        "",
        f"- url: {payload['url']}",
        f"- resource_type: `{payload['resource_type']}`",
        f"- processor_status: `{payload['processor_status']}`",
        "",
        "## 预期输出",
        "",
        *[f"- `{item}`" for item in payload["expected_outputs"]],
        "",
    ])


def _render_text_resource_processor(payload: dict[str, Any]) -> str:
    access = payload.get("content_access") or {}
    assessment = payload.get("assessment") or {}
    lines = [
        "# 文本资源 Source Card",
        "",
        f"- url: {payload.get('url', '')}",
        f"- final_url: {payload.get('final_url', '')}",
        f"- resource_type: `{payload.get('resource_type', '')}`",
        f"- processor_status: `{payload.get('processor_status', '')}`",
        f"- title: {payload.get('title', '')}",
        f"- source: `{payload.get('source', '')}`",
        f"- body_char_count: `{payload.get('body_char_count', 0)}`",
        "",
        "## 内容访问状态",
        "",
        f"- metadata: `{access.get('metadata', False)}`",
        f"- body: `{access.get('body', False)}`",
        f"- body_status: `{access.get('body_status', '')}`",
        f"- cookie_file_used: `{access.get('cookie_file_used', False)}`",
        f"- http_status: `{access.get('http_status', '')}`",
        f"- content_type: `{access.get('content_type', '')}`",
        "",
        "## 资源评价",
        "",
        f"- decision: `{assessment.get('decision', '')}`",
        f"- risks: `{', '.join(assessment.get('risks') or [])}`",
        "",
        "## 正文摘录",
        "",
        payload.get("body_excerpt", "") or "- 未能读取正文。",
        "",
        "## 进入 forecast 的限制",
        "",
        "- 只有 `body=true` 的文本资源可作为正文 evidence。",
        "- 搜索摘要和标题只能作为候选筛选依据。",
        "",
    ]
    return "\n".join(lines)


def _render_video_resource_processor(payload: dict[str, Any]) -> str:
    access = payload.get("content_access") or {}
    assessment = payload.get("assessment") or {}
    render = payload.get("render") or {}
    render_prompt = (
        Path(render.get("output_dir", "")) / "video_report_prompt.md"
        if render
        else ""
    )
    stats = payload.get("stats") or {}
    comments = payload.get("comments") or []
    subtitles = payload.get("subtitles") or payload.get("automatic_captions") or []
    lines = [
        "# 视频资源 Source Card",
        "",
        f"- platform: `{payload.get('platform', '')}`",
        f"- url: {payload.get('url', '')}",
        f"- resource_type: `{payload.get('resource_type', '')}`",
        f"- processor_status: `{payload.get('processor_status', '')}`",
        f"- render_status: `{render.get('render_status', '')}`",
        f"- render_skill: `{render.get('skill_name', '')}`",
        f"- active_locks: `{len(render.get('active_locks') or [])}`",
        f"- asr_lock_active: `{render.get('asr_lock_active', False)}`",
        f"- title: {payload.get('title', '')}",
        f"- author: {payload.get('author', '')}",
        f"- published_at: `{payload.get('published_at', '')}`",
        f"- duration_seconds: `{payload.get('duration_seconds', '')}`",
        f"- view_count: `{stats.get('view_count', '')}`",
        f"- like_count: `{stats.get('like_count', '')}`",
        f"- comment_count: `{stats.get('comment_count', '')}`",
        "",
        "## 内容访问状态",
        "",
        f"- metadata: `{access.get('metadata', False)}`",
        f"- comments: `{access.get('comments', False)}`",
        f"- transcript: `{access.get('transcript', False)}`",
        f"- video_body: `{access.get('video_body', False)}`",
        f"- requires_asr: `{access.get('requires_asr', False)}`",
        f"- comments_status: `{payload.get('comments_status', '')}`",
        f"- subtitles_status: `{payload.get('subtitles_status', '')}`",
        f"- render_timeout_seconds: `{render.get('timeout_seconds', '')}`",
        "",
        "## 资源评价",
        "",
        f"- decision: `{assessment.get('decision', '')}`",
        f"- signals: `{', '.join(assessment.get('signals') or [])}`",
        f"- risks: `{', '.join(assessment.get('risks') or [])}`",
        "",
        "## 字幕 / 自动字幕",
        "",
    ]
    if subtitles:
        for item in subtitles[:8]:
            lines.append(f"- `{item}`")
    else:
        lines.append("- 未发现可直接读取的字幕；需要 ASR 或视频 render。")
    lines.extend(["", "## 评论区样本", ""])
    if comments:
        for index, item in enumerate(comments[:12], start=1):
            message = " ".join(str(item.get("message", "")).split())
            lines.append(
                f"{index}. {item.get('author', '')}: {message[:220]} "
                f"(like={item.get('like_count', '')}, replies={item.get('reply_count', '')})"
            )
    else:
        lines.append("- 未获取评论样本；只能使用评论计数作为弱信号。")
    lines.extend([
        "",
        "## Render / ASR 任务",
        "",
        f"- output_dir: `{render.get('output_dir', '')}`",
        f"- prompt: `{render_prompt}`",
        f"- missing_outputs: `{', '.join(render.get('missing_outputs') or [])}`",
        f"- lock_policy: {render.get('lock_policy', '')}",
        f"- main_agent_takeover_policy: {render.get('main_agent_takeover_policy', '')}",
        f"- fallback_policy: {render.get('fallback_policy', '')}",
        "",
        "## 进入 forecast 的限制",
        "",
        "- 搜索结果和标题只允许作为候选。",
        "- 若 `video_body=false`，该视频不能作为完整正文 evidence。",
        "- 若 `requires_asr=true`，下一步应调用视频 render/ASR，再生成 resource report。",
        "- 若 render timeout 后仍缺 `video_report.pdf`，必须写入 audit gap 并继续 synthesis。",
        "",
    ])
    return "\n".join(lines)


def settings_have_search_key() -> bool:
    return bool(get_settings().tavily_api_key)
