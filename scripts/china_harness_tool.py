#!/usr/bin/env python3
"""Run one China harness access tool and persist its artifacts locally."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from beatodds.agents.access_tools import sync_video_render_artifacts
from beatodds.agents.models import TrajectoryStep
from beatodds.agents.tool_registry import default_china_tool_registry
from beatodds.agents.video_reporter import finalize_video_resource_report
from beatodds.agents.workspace import ChinaForecastWorkspace
from beatodds.evidence.providers.mock_provider import MockSearchProvider


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Call one repo access tool for an existing China harness workspace."
    )
    parser.add_argument("--workspace", required=True, help="Agent run workspace path")
    parser.add_argument("--mock", action="store_true", help="Use deterministic mock search")
    parser.add_argument(
        "--enable-llm-baseline",
        action="store_true",
        help="Allow model_baseline_forecast to call configured LLM API.",
    )
    parser.add_argument("--baseline-model", default="")

    subparsers = parser.add_subparsers(dest="tool_name", required=True)
    _add_simple_tool(subparsers, "read_polymarket_context")
    _add_simple_tool(subparsers, "export_source_registry")
    _add_simple_tool(subparsers, "model_baseline_forecast")

    query_parser = _add_simple_tool(subparsers, "generate_china_queries")
    query_parser.add_argument("--query", default="")

    search_parser = _add_simple_tool(subparsers, "search_web")
    search_parser.add_argument("--query", required=True)
    search_parser.add_argument("--source-category", default="generic_search_tools")
    search_parser.add_argument("--max-results", type=int, default=5)
    search_parser.add_argument("--reliability-prior", type=float, default=0.0)
    search_parser.add_argument("--allow-self-reference", action="store_true")

    video_parser = _add_simple_tool(subparsers, "search_video_sources")
    video_parser.add_argument("--query", required=True)
    video_parser.add_argument("--source-category", default="expert_social")
    video_parser.add_argument("--max-results", type=int, default=6)
    video_parser.add_argument("--reliability-prior", type=float, default=0.5)
    video_parser.add_argument("--platforms", default="bilibili,youtube")

    platform_parser = _add_simple_tool(subparsers, "search_chinese_platforms")
    platform_parser.add_argument("--query", required=True)
    platform_parser.add_argument("--source-category", default="generic_search_tools")
    platform_parser.add_argument("--max-results", type=int, default=5)
    platform_parser.add_argument("--reliability-prior", type=float, default=0.0)
    platform_parser.add_argument(
        "--platforms",
        default="weibo,zhihu,wechat,xueqiu,research_reports,newswire",
    )

    resource_parser = _add_simple_tool(subparsers, "process_resource")
    resource_parser.add_argument("--url", required=True)
    resource_parser.add_argument("--source-category", default="expert_social")
    resource_parser.add_argument("--max-comments", type=int, default=10)
    resource_parser.add_argument("--render-timeout-seconds", type=int, default=900)
    resource_parser.add_argument(
        "--orientation",
        default="forecast_evidence",
        choices=["forecast_evidence", "social_analysis", "teaching_note", "general_summary"],
    )
    resource_parser.add_argument(
        "--fetch-youtube-comments",
        action="store_true",
        help="Best-effort YouTube comment fetch; can be slow or fail under throttling.",
    )

    sync_parser = _add_simple_tool(subparsers, "sync_resource_status")
    sync_parser.add_argument("--resource-dir", default="")
    sync_parser.add_argument("--all", action="store_true")
    sync_parser.add_argument("--rewrite-artifact-index", action="store_true")

    finalize_parser = _add_simple_tool(subparsers, "finalize_video_report")
    finalize_parser.add_argument("--resource-dir", required=True)

    review_parser = _add_simple_tool(subparsers, "agent_review")
    review_parser.add_argument("--review-id", default="")
    review_parser.add_argument("--evidence-path", required=True)
    review_parser.add_argument("--evidence-label", default="")
    review_parser.add_argument("--source-url", default="")
    review_parser.add_argument("--candidate-set-path", default="")
    review_parser.add_argument("--agent-output", required=True)
    review_parser.add_argument("--observation", required=True)
    review_parser.add_argument("--assessment", required=True)
    review_parser.add_argument("--raw-materials-seen", default="")
    review_parser.add_argument("--source-excerpt-or-summary", default="")
    review_parser.add_argument("--visible-reasoning-memo", default="")
    review_parser.add_argument("--source-selection-notes", default="")
    review_parser.add_argument("--rejected-or-downweighted", default="")
    review_parser.add_argument("--information-gap", required=True)
    review_parser.add_argument("--next-search-decision", required=True)
    review_parser.add_argument("--stop-or-continue", required=True)
    review_parser.add_argument("--confidence-note", default="")

    args = parser.parse_args()
    workspace = ChinaForecastWorkspace.open_existing(args.workspace)
    if args.tool_name == "agent_review":
        _record_agent_review(workspace, args)
        return
    if args.tool_name == "sync_resource_status":
        _sync_resource_status(workspace, args)
        return
    if args.tool_name == "finalize_video_report":
        _finalize_video_report(workspace, args)
        return

    registry = default_china_tool_registry(
        provider=MockSearchProvider() if args.mock else None,
        enable_model_baseline_llm=args.enable_llm_baseline,
        baseline_model=args.baseline_model or None,
    )

    result = _run_tool(registry, workspace, args)
    recorded_paths = workspace.record_tool_result(result)
    source_paths = [
        path for path in recorded_paths
        if "sources" in path.parts and path.suffix == ".md"
    ]
    for item, path in zip(result.results, source_paths, strict=False):
        workspace.append_claim(
            claim=_compact_claim(item),
            direction="neutral",
            source_path=str(path),
        )
    observed_paths = _combined_artifact_paths(recorded_paths, result.artifact_paths)
    workspace.append_trajectory(
        TrajectoryStep(
            loop_index=_next_loop_index(workspace),
            phase="tool",
            summary=f"本地工具调用：{result.tool_name}",
            action=_action_summary(args),
            observation=_observation_summary(result, observed_paths),
            analysis=(
                "工具输出已经落盘。本地 agent 必须阅读新 artifacts，更新 evidence state，"
                "识别剩余信息缺口，并决定下一次搜索。"
            ),
            next_decision="先阅读已落盘 artifact，再选择下一步行动。",
            tool_name=result.tool_name,
            tool_action_id=result.action_id,
            metadata={"local_harness_tool": True},
        )
    )

    print(f"status={result.status}")
    print(f"tool={result.tool_name}")
    print(f"query={result.query}")
    print(f"results={len(result.results)}")
    print("artifact_paths:")
    for path in observed_paths:
        print(f"- {path}")
    if result.error:
        print(f"error={result.error}")


def _record_agent_review(workspace: ChinaForecastWorkspace, args) -> None:
    evidence_label = args.evidence_label or _guess_evidence_label(
        workspace,
        args.evidence_path,
    )
    payload = {
        "review_id": args.review_id or "",
        "evidence_path": args.evidence_path,
        "evidence_path_short": _short_path(workspace, args.evidence_path),
        "evidence_label": evidence_label,
        "source_url": args.source_url,
        "candidate_set_path": args.candidate_set_path,
        "candidate_set_path_short": _short_path(workspace, args.candidate_set_path),
        "phase": "analyze",
        "agent_output": args.agent_output,
        "observation": args.observation,
        "assessment": args.assessment,
        "raw_materials_seen": args.raw_materials_seen,
        "source_excerpt_or_summary": args.source_excerpt_or_summary,
        "visible_reasoning_memo": args.visible_reasoning_memo,
        "source_selection_notes": args.source_selection_notes,
        "rejected_or_downweighted": args.rejected_or_downweighted,
        "information_gap": args.information_gap,
        "next_search_decision": args.next_search_decision,
        "stop_or_continue": args.stop_or_continue,
        "confidence_note": args.confidence_note,
        "model": workspace.context.agent_model or "codex:gpt-5.4-mini",
    }
    payload["source_display"] = _source_display(payload)
    json_path, md_path = workspace.record_agent_review(payload)
    _append_full_trajectory(workspace, payload, md_path)
    workspace.append_trajectory(
        TrajectoryStep(
            loop_index=_next_loop_index(workspace),
            phase="analyze",
            summary="Agent 证据复盘",
            action=(
                "复盘已落盘证据："
                f"{evidence_label or _short_path(workspace, args.evidence_path)}"
            ),
            observation=args.observation,
            analysis="\n".join([
                f"agent_output: {args.agent_output}",
                f"assessment: {args.assessment}",
                f"raw_materials_seen: {args.raw_materials_seen}",
                f"source_excerpt_or_summary: {args.source_excerpt_or_summary}",
                f"visible_reasoning_memo: {args.visible_reasoning_memo}",
                f"source_selection_notes: {args.source_selection_notes}",
                f"rejected_or_downweighted: {args.rejected_or_downweighted}",
                f"information_gap: {args.information_gap}",
                f"confidence_note: {args.confidence_note}",
            ]).strip(),
            next_decision="\n".join([
                f"next_search_decision: {args.next_search_decision}",
                f"stop_or_continue: {args.stop_or_continue}",
            ]),
            metadata={
                "local_agent_review": True,
                "evidence_path": args.evidence_path,
                "evidence_label": evidence_label,
                "review_paths": [
                    _short_path(workspace, json_path),
                    _short_path(workspace, md_path),
                ],
            },
        )
    )
    print("status=ok")
    print("tool=agent_review")
    print(f"evidence_label={evidence_label}")
    print(f"evidence_path={_short_path(workspace, args.evidence_path)}")
    print("artifact_paths:")
    print(f"- {_short_path(workspace, json_path)}")
    print(f"- {_short_path(workspace, md_path)}")
    print("- full_trajectory.md")


def _sync_resource_status(workspace: ChinaForecastWorkspace, args) -> None:
    resource_dirs = _resource_dirs_to_sync(workspace, args)
    if not resource_dirs:
        print("status=skipped")
        print("reason=no resource directories found")
        return
    synced = []
    skipped = []
    for resource_dir in resource_dirs:
        payload = sync_video_render_artifacts(
            resource_dir,
            rewrite_artifact_index=args.rewrite_artifact_index,
        )
        if payload.get("resource_type") not in {"youtube_video", "bilibili_video"}:
            skipped.append(_short_path(workspace, resource_dir))
            continue
        render = payload.get("render") or {}
        synced.append({
            "path": _short_path(workspace, resource_dir),
            "title": payload.get("title", ""),
            "url": payload.get("url", ""),
            "render_status": render.get("render_status", ""),
            "existing_outputs": render.get("existing_outputs", []),
            "missing_outputs": render.get("missing_outputs", []),
        })
    workspace.append_trajectory(
        TrajectoryStep(
            loop_index=_next_loop_index(workspace),
            phase="tool",
            summary="同步视频资源 render 状态",
            action="扫描 resource_processor.json 和资源目录实际产物。",
            observation=json.dumps(synced, ensure_ascii=False, indent=2),
            analysis=(
                "状态同步只依据本地 artifact，不生成新 evidence；"
                "后续 agent 应以同步后的 render_status 判断视频正文是否可用。"
            ),
            next_decision="读取已同步 source_card/resource_processor，再决定是否补 render。",
            tool_name="sync_resource_status",
            metadata={"local_harness_tool": True, "resource_count": len(synced)},
        )
    )
    print("status=ok")
    print(f"synced={len(synced)}")
    if skipped:
        print(f"skipped_non_video={len(skipped)}")
    for item in synced:
        print(
            f"- {item['path']} | {item['render_status']} | "
            f"{item['title'] or item['url']}"
        )


def _finalize_video_report(workspace: ChinaForecastWorkspace, args) -> None:
    args.all = False
    args.rewrite_artifact_index = False
    resource_dirs = _resource_dirs_to_sync(workspace, args)
    if not resource_dirs:
        print("status=skipped")
        print("reason=no resource directory found")
        return
    resource_dir = resource_dirs[0]
    payload = finalize_video_resource_report(resource_dir)
    relative_dir = _short_path(workspace, resource_dir)
    workspace.append_trajectory(
        TrajectoryStep(
            loop_index=_next_loop_index(workspace),
            phase="tool",
            summary="补齐视频正文报告",
            action=f"finalize_video_report resource_dir={relative_dir}",
            observation=json.dumps(payload, ensure_ascii=False, indent=2),
            analysis=(
                "本地 fallback 从已下载视频、metadata 和字幕/ASR 生成完整 "
                "video_report.pdf 与 evidence_card.md。后续 agent 可以读取这些产物，"
                "再判断该视频对 forecast 的实际意义。"
            ),
            next_decision="读取 evidence_card.md 和 video_parse_report.md，进入 evidence review。",
            tool_name="finalize_video_report",
            metadata={"local_harness_tool": True, "resource_dir": relative_dir},
        )
    )
    print("status=ok")
    print(f"resource_dir={relative_dir}")
    print(f"render_status={payload.get('render_status')}")
    print("artifact_paths:")
    for name in [
        "video_metadata.json",
        "video_parse_report.md",
        "claims.jsonl",
        "evidence_card.md",
        "frame_index.md",
        "video_report.tex",
        "video_report.pdf",
        "artifact_index.md",
    ]:
        print(f"- {_short_path(workspace, resource_dir / name)}")


def _resource_dirs_to_sync(workspace: ChinaForecastWorkspace, args) -> list[Path]:
    resources_root = workspace.paths.artifacts_dir / "resources"
    if args.all:
        return sorted(path.parent for path in resources_root.glob("*/resource_processor.json"))
    if not args.resource_dir:
        return []
    value = Path(args.resource_dir)
    candidates = []
    if value.is_absolute():
        candidates.append(value)
    else:
        candidates.extend([
            workspace.paths.run_dir / value,
            resources_root / value,
        ])
    for candidate in candidates:
        if (candidate / "resource_processor.json").exists():
            return [candidate]
    raise FileNotFoundError(f"Missing resource_processor.json for {args.resource_dir}")


def _append_full_trajectory(
    workspace: ChinaForecastWorkspace,
    payload: dict,
    review_md_path: Path,
) -> None:
    path = workspace.paths.run_dir / "full_trajectory.md"
    index = _review_index_from_path(review_md_path) or len(
        list(workspace.paths.agent_reviews_dir.glob("*.json"))
    )
    evidence_label = str(payload.get("evidence_label", "")).strip()
    evidence_path = str(payload.get("evidence_path_short") or payload.get("evidence_path") or "")
    candidate_set_path = str(
        payload.get("candidate_set_path_short")
        or payload.get("candidate_set_path")
        or "",
    ).strip()
    lines = [
        f"## Evidence Review {index}",
        "",
        f"Source：{payload.get('source_display') or evidence_label or '未填写'}",
        "",
        f"- review_path: `{_short_path(workspace, review_md_path)}`",
        f"- evidence_path: `{evidence_path}`",
        f"- model: `{payload.get('model', '')}`",
        "",
    ]
    if payload.get("source_url"):
        lines.insert(6, f"- source_url: {payload.get('source_url')}")
    if candidate_set_path:
        lines.insert(7, f"- candidate_set_path: `{candidate_set_path}`")
    candidate_lines = _candidate_video_summary_lines(workspace, payload)
    if candidate_lines:
        lines.extend(candidate_lines)
    sections = [
        ("Agent 输出", "agent_output"),
        ("实际阅读材料", "raw_materials_seen"),
        ("材料摘录或压缩摘要", "source_excerpt_or_summary"),
        ("可展示推理札记", "visible_reasoning_memo"),
        ("Source 选择说明", "source_selection_notes"),
        ("评估", "assessment"),
        ("拒绝或降权材料", "rejected_or_downweighted"),
        ("信息缺口", "information_gap"),
        ("下一步搜索决策", "next_search_decision"),
        ("停止或继续", "stop_or_continue"),
        ("置信度备注", "confidence_note"),
    ]
    for title, key in sections:
        value = str(payload.get(key, "")).strip()
        if value:
            lines.extend([f"### {title}", "", value, ""])
    with path.open("a", encoding="utf-8") as f:
        f.write("\n".join(lines).rstrip() + "\n\n")


def _add_simple_tool(subparsers, name: str):
    return subparsers.add_parser(name)


def _run_tool(registry, workspace: ChinaForecastWorkspace, args):
    common = {
        "context": workspace.context,
        "workspace": workspace,
    }
    if args.tool_name == "search_web":
        metadata = {}
        if args.allow_self_reference:
            metadata["allow_self_reference"] = True
        return registry.run(
            "search_web",
            query=args.query,
            source_category=args.source_category,
            max_results=args.max_results,
            reliability_prior=args.reliability_prior,
            metadata=metadata,
            **common,
        )
    if args.tool_name == "search_video_sources":
        return registry.run(
            "search_video_sources",
            query=args.query,
            source_category=args.source_category,
            max_results=args.max_results,
            reliability_prior=args.reliability_prior,
            platforms=args.platforms,
            **common,
        )
    if args.tool_name == "search_chinese_platforms":
        return registry.run(
            "search_chinese_platforms",
            query=args.query,
            source_category=args.source_category,
            max_results=args.max_results,
            reliability_prior=args.reliability_prior,
            platforms=args.platforms,
            **common,
        )
    if args.tool_name == "generate_china_queries":
        return registry.run(
            "generate_china_queries",
            query=args.query or workspace.context.market_question,
            source_category="generic_search_tools",
            **common,
        )
    if args.tool_name == "process_resource":
        return registry.run(
            "process_resource",
            query=args.url,
            url=args.url,
            source_category=args.source_category,
            max_comments=args.max_comments,
            fetch_youtube_comments=args.fetch_youtube_comments,
            render_timeout_seconds=args.render_timeout_seconds,
            orientation=args.orientation,
            **common,
        )
    return registry.run(args.tool_name, **common)


def _next_loop_index(workspace: ChinaForecastWorkspace) -> int:
    jsonl_path = workspace.paths.run_dir / "trajectory.jsonl"
    if not jsonl_path.exists():
        return 1
    return sum(1 for line in jsonl_path.read_text(encoding="utf-8").splitlines() if line) + 1


def _action_summary(args) -> str:
    if args.tool_name == "search_web":
        return (
            f"search_web query={args.query!r}, source_category={args.source_category!r}, "
            f"max_results={args.max_results}"
        )
    if args.tool_name == "search_video_sources":
        return (
            f"search_video_sources query={args.query!r}, "
            f"platforms={args.platforms!r}, max_results={args.max_results}"
        )
    if args.tool_name == "search_chinese_platforms":
        return (
            f"search_chinese_platforms query={args.query!r}, "
            f"platforms={args.platforms!r}, max_results={args.max_results}"
        )
    if args.tool_name == "generate_china_queries":
        return f"generate_china_queries query={args.query!r}"
    if args.tool_name == "process_resource":
        return f"process_resource url={args.url!r}, source_category={args.source_category!r}"
    return args.tool_name


def _observation_summary(result, recorded_paths: list[Path]) -> str:
    lines = [
        f"status={result.status}",
        f"tool={result.tool_name}",
        f"source_category={result.source_category}",
        f"query={result.query}",
        f"result_count={len(result.results)}",
    ]
    if result.metadata:
        lines.append(f"metadata={result.metadata}")
    if recorded_paths:
        lines.append("artifact_paths:")
        lines.extend(f"- {path}" for path in recorded_paths)
    if result.results:
        lines.append("结果摘要:")
        for index, item in enumerate(result.results[:5], start=1):
            lines.append(f"{index}. {item.title} | {item.source} | {item.url}")
    if result.error:
        lines.append(f"error={result.error}")
    return "\n".join(lines)


def _combined_artifact_paths(recorded_paths: list[Path], tool_paths: list[str]) -> list[Path]:
    output: list[Path] = []
    seen = set()
    for path in [*recorded_paths, *[Path(item) for item in tool_paths]]:
        key = str(path)
        if key in seen:
            continue
        seen.add(key)
        output.append(path)
    return output


def _short_path(workspace: ChinaForecastWorkspace, path: str | Path) -> str:
    if not path:
        return ""
    candidate = _resolve_run_path(workspace, path)
    try:
        relative = candidate.relative_to(workspace.paths.run_dir)
    except ValueError:
        return Path(path).as_posix()
    text = relative.as_posix()
    return "." if text == "." else f"./{text}"


def _source_display(payload: dict) -> str:
    label = str(payload.get("evidence_label", "")).strip()
    if label:
        return label
    source_url = str(payload.get("source_url", "")).strip()
    if source_url:
        return f"URL：{source_url}"
    evidence_path = str(
        payload.get("evidence_path_short")
        or payload.get("evidence_path")
        or "",
    ).strip()
    return f"材料：{evidence_path}" if evidence_path else "未填写"


def _candidate_video_summary_lines(
    workspace: ChinaForecastWorkspace,
    payload: dict,
) -> list[str]:
    candidate_path = str(
        payload.get("candidate_set_path")
        or payload.get("evidence_path")
        or "",
    ).strip()
    if not candidate_path:
        return []
    path = _resolve_run_path(workspace, candidate_path)
    if not path.exists() or path.suffix.lower() != ".md":
        return []
    text = path.read_text(encoding="utf-8")
    if not _looks_like_video_candidate_set(text):
        return []
    return [
        "### 候选池入口",
        "",
        (
            f"候选视频池保存在 `{_short_path(workspace, path)}`；"
            "其中包含筛选前标题、平台、作者、播放/评论、状态和选择/拒绝理由。"
        ),
        "",
    ]


def _looks_like_video_candidate_set(markdown: str) -> bool:
    lowered = markdown.lower()
    return (
        "视频搜索候选集" in markdown
        or "candidate set before final selection" in lowered
    ) and ("bilibili" in lowered or "youtube" in lowered)


def _resolve_run_path(workspace: ChinaForecastWorkspace, path: str | Path) -> Path:
    path_obj = Path(path)
    if path_obj.is_absolute():
        return path_obj
    candidates = [
        workspace.paths.run_dir / path_obj,
    ]
    repo_root = _repo_root_from_run_dir(workspace.paths.run_dir)
    if repo_root:
        candidates.append(repo_root / path_obj)
    candidates.append(Path.cwd() / path_obj)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    if repo_root and path_obj.parts and path_obj.parts[0] == "workspace":
        return repo_root / path_obj
    return workspace.paths.run_dir / path_obj


def _repo_root_from_run_dir(run_dir: Path) -> Path | None:
    parts = run_dir.resolve().parts
    if "workspace" not in parts:
        return None
    workspace_index = parts.index("workspace")
    return Path(*parts[:workspace_index])


def _review_index_from_path(path: Path) -> int | None:
    match = re.match(r"(?P<index>\d{3})_", path.name)
    if not match:
        return None
    return int(match.group("index"))


def _extract_video_candidate_rows(markdown: str) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    header: list[str] | None = None
    for line in markdown.splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if not cells:
            continue
        if cells[0] == "#":
            header = cells
            continue
        if header is None or set(cells[0]) <= {"-", ":"}:
            continue
        if len(cells) < len(header):
            cells.extend([""] * (len(header) - len(cells)))
        record = dict(zip(header, cells, strict=False))
        platform = record.get("platform", "").strip().lower()
        if platform not in {"bilibili", "youtube"}:
            continue
        title, url = _split_markdown_link(record.get("title", ""))
        rows.append({
            "status": record.get("status", ""),
            "title": title,
            "url": url,
            "author": record.get("author", ""),
            "platform": platform,
            "views": record.get("views", ""),
            "comments": record.get("comments", ""),
            "score": record.get("score", ""),
            "reason": record.get("reason", ""),
        })
    return rows


def _split_markdown_link(value: str) -> tuple[str, str]:
    match = re.match(r"\[(?P<title>.+?)\]\((?P<url>.+?)\)", value.strip())
    if not match:
        return value.strip(), ""
    return match.group("title").strip(), match.group("url").strip()


def _extract_video_id(url: str) -> str:
    if not url:
        return ""
    if match := re.search(r"/video/(BV[0-9A-Za-z]+)", url):
        return match.group(1)
    if match := re.search(r"[?&]v=([0-9A-Za-z_-]{6,})", url):
        return f"YouTube: {match.group(1)}"
    return ""


def _guess_evidence_label(workspace: ChinaForecastWorkspace, evidence_path: str) -> str:
    if not evidence_path:
        return ""
    path = Path(evidence_path)
    if not path.is_absolute():
        path = workspace.paths.run_dir / path
    json_path = path.with_suffix(".json")
    if not json_path.exists():
        return path.stem
    try:
        data = json.loads(json_path.read_text(encoding="utf-8"))
    except Exception:
        return path.stem
    payload = data.get("payload") or {}
    tool_name = data.get("tool_name") or ""
    if tool_name == "search_video_sources":
        query = data.get("query") or payload.get("query") or ""
        return f"视频候选集：{query}".strip("：")
    title = str(payload.get("title") or "").strip()
    platform = str(payload.get("platform") or "").strip()
    author = str(payload.get("author") or "").strip()
    published_at = str(payload.get("published_at") or "").strip()
    if title:
        prefix = {
            "bilibili": "B站视频",
            "youtube": "YouTube视频",
        }.get(platform, "材料")
        suffix = "，".join(item for item in [author, published_at[:10]] if item)
        return f"{prefix}：《{title}》" + (f"（{suffix}）" if suffix else "")
    query = str(data.get("query") or "").strip()
    if query:
        return f"{tool_name or '工具结果'}：{query}"
    return path.stem


def _compact_claim(item) -> str:
    title = " ".join((item.title or "").split())
    summary = " ".join((item.summary or "").split())
    if title and summary:
        return f"{title}: {summary[:360]}"
    return summary[:360] or title


if __name__ == "__main__":
    main()
