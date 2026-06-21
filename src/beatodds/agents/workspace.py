"""File workspace for event / market / agent-run forecast work."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from beatodds.agents.models import (
    AgentRunContext,
    AgentToolResult,
    ForecastReportDraft,
    SourceCard,
    TrajectoryStep,
    slugify,
)
from beatodds.agents.source_cards import (
    render_source_card,
    source_card_filename,
    source_card_from_search_result,
)


class WorkspacePaths(BaseModel):
    root_dir: Path
    event_dir: Path
    market_dir: Path
    run_dir: Path
    search_actions_dir: Path
    agent_reviews_dir: Path
    sources_dir: Path
    artifacts_dir: Path


class ChinaForecastWorkspace:
    """Readable, replayable workspace for one China-specific forecast run."""

    def __init__(self, context: AgentRunContext, root_dir: str | Path):
        run_id = context.normalized_agent_run_id
        self.context = context.model_copy(update={"agent_run_id": run_id})
        root = Path(root_dir)
        event_dir = root / self.context.normalized_event_slug
        market_dir = event_dir / self.context.normalized_market_slug
        run_dir = market_dir / run_id
        self.paths = WorkspacePaths(
            root_dir=root,
            event_dir=event_dir,
            market_dir=market_dir,
            run_dir=run_dir,
            search_actions_dir=run_dir / "search_actions",
            agent_reviews_dir=run_dir / "agent_reviews",
            sources_dir=run_dir / "sources",
            artifacts_dir=run_dir / "artifacts",
        )

    @classmethod
    def create(
        cls,
        context: AgentRunContext,
        root_dir: str | Path = "workspace",
    ) -> "ChinaForecastWorkspace":
        workspace = cls(context, root_dir)
        workspace.initialize()
        return workspace

    @classmethod
    def open_existing(cls, run_dir: str | Path) -> "ChinaForecastWorkspace":
        """Open an existing workspace without reinitializing its files."""
        run_path = Path(run_dir)
        context_path = run_path / "run_context.json"
        if not context_path.exists():
            raise FileNotFoundError(f"Missing run_context.json under {run_path}")
        context = AgentRunContext(**json.loads(context_path.read_text(encoding="utf-8")))
        workspace = cls.__new__(cls)
        workspace.context = context
        workspace.paths = WorkspacePaths(
            root_dir=run_path.parents[2],
            event_dir=run_path.parents[1],
            market_dir=run_path.parent,
            run_dir=run_path,
            search_actions_dir=run_path / "search_actions",
            agent_reviews_dir=run_path / "agent_reviews",
            sources_dir=run_path / "sources",
            artifacts_dir=run_path / "artifacts",
        )
        return workspace

    def initialize(self) -> None:
        for path in [
            self.paths.event_dir,
            self.paths.event_dir / "shared_sources",
            self.paths.event_dir / "shared_artifacts",
            self.paths.market_dir,
            self.paths.market_dir / "market_snapshots",
            self.paths.run_dir,
            self.paths.search_actions_dir,
            self.paths.agent_reviews_dir,
            self.paths.sources_dir,
            self.paths.artifacts_dir,
        ]:
            path.mkdir(parents=True, exist_ok=True)

        self._write_if_missing(self.paths.event_dir / "event.md", self._render_event_md())
        self._write_json(
            self.paths.event_dir / "event_metadata.json",
            self.context.model_dump(mode="json"),
        )
        self._write_if_missing(self.paths.market_dir / "market.md", self._render_market_md())
        self._write_if_missing(
            self.paths.market_dir / "resolution.md",
            self._render_resolution_md(),
        )
        self._write_if_missing(self.paths.run_dir / "market.md", self._render_market_md())
        self._write_if_missing(
            self.paths.run_dir / "resolution.md",
            self._render_resolution_md(),
        )
        self._write_text(self.paths.run_dir / "run.md", self._render_run_md())
        self._write_if_missing(self.paths.run_dir / "source_plan.md", "# 来源计划\n\n")
        self._write_if_missing(self.paths.run_dir / "plan.md", "# 计划\n\n")
        self._write_if_missing(self.paths.run_dir / "trajectory.md", "# 轨迹\n\n")
        self._write_if_missing(
            self.paths.run_dir / "full_trajectory.md",
            "# 完整轨迹留档\n\n",
        )
        self._write_if_missing(self.paths.run_dir / "claims.md", "# 判断记录\n\n")
        self._write_if_missing(self.paths.run_dir / "audit.md", "# 审计\n\n")
        self._write_json(
            self.paths.run_dir / "run_context.json",
            self.context.model_dump(mode="json"),
        )

    def append_trajectory(self, step: TrajectoryStep) -> None:
        md_path = self.paths.run_dir / "trajectory.md"
        jsonl_path = self.paths.run_dir / "trajectory.jsonl"
        with md_path.open("a", encoding="utf-8") as f:
            f.write(_render_step_markdown(step))
        with jsonl_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(step.model_dump(mode="json"), ensure_ascii=False) + "\n")

    def append_plan(self, content: str) -> None:
        with (self.paths.run_dir / "plan.md").open("a", encoding="utf-8") as f:
            f.write(content.rstrip() + "\n\n")

    def append_audit(self, content: str) -> None:
        with (self.paths.run_dir / "audit.md").open("a", encoding="utf-8") as f:
            f.write(content.rstrip() + "\n\n")

    def append_claim(self, claim: str, direction: str = "neutral", source_path: str = "") -> None:
        with (self.paths.run_dir / "claims.md").open("a", encoding="utf-8") as f:
            f.write(f"- `{direction}` {claim}")
            if source_path:
                f.write(f"  source: `{source_path}`")
            f.write("\n")

    def record_agent_review(self, payload: dict[str, Any]) -> tuple[Path, Path]:
        """Persist one visible agent reasoning summary for evidence-driven review."""
        index = len(list(self.paths.agent_reviews_dir.glob("*.json"))) + 1
        label = (
            payload.get("review_id")
            or payload.get("evidence_label")
            or Path(str(payload.get("evidence_path") or "")).stem
            or f"review_{index}"
        )
        slug = slugify(str(label), fallback=f"review_{index}", max_len=80)
        base_name = f"{index:03d}_{slug}"
        json_path = self.paths.agent_reviews_dir / f"{base_name}.json"
        md_path = self.paths.agent_reviews_dir / f"{base_name}.md"
        self._write_json(json_path, payload)
        self._write_text(md_path, _render_agent_review_markdown(payload))
        return json_path, md_path

    def record_tool_result(self, result: AgentToolResult) -> list[Path]:
        slug_source = f"{result.tool_name}_{result.query}" if result.query else result.tool_name
        action_slug = slugify(
            slug_source,
            fallback=result.action_id,
            max_len=90,
        )
        action_slug = f"{action_slug}_{result.action_id[:8]}"
        json_path = self.paths.search_actions_dir / f"{action_slug}.json"
        md_path = self.paths.search_actions_dir / f"{action_slug}.md"

        source_paths = self.write_source_cards(result)
        artifact_paths = [*result.artifact_paths, *[str(path) for path in source_paths]]
        result_with_paths = result.model_copy(
            update={"artifact_paths": artifact_paths},
        )
        self._write_json(json_path, result_with_paths.model_dump(mode="json"))
        self._write_text(md_path, _render_tool_result_markdown(result_with_paths))
        return [json_path, md_path, *source_paths]

    def write_source_cards(self, result: AgentToolResult) -> list[Path]:
        paths = []
        for idx, search_result in enumerate(result.results, start=1):
            card = source_card_from_search_result(
                search_result,
                source_category=search_result.source_type or result.source_category,
                retrieved_at=result.finished_at,
            )
            paths.append(self.write_source_card(card, index=idx))
        return paths

    def write_source_card(self, card: SourceCard, index: int | None = None) -> Path:
        category = slugify(card.source_category, fallback="other", max_len=48)
        category_dir = self.paths.sources_dir / category
        category_dir.mkdir(parents=True, exist_ok=True)
        path = category_dir / source_card_filename(card, index=index)
        self._write_text(path, render_source_card(card))
        self._write_json(path.with_suffix(".json"), card.model_dump(mode="json"))
        return path

    def write_forecast_report(self, report: ForecastReportDraft) -> tuple[Path, Path]:
        md_path = self.paths.run_dir / "forecast_report.md"
        json_path = self.paths.run_dir / "forecast_report.json"
        self._write_text(md_path, report.report_markdown)
        self._write_json(json_path, report.model_dump(mode="json"))
        return md_path, json_path

    def _render_event_md(self) -> str:
        return "\n".join([
            "# 事件",
            "",
            f"- 标题: {self.context.event_title}",
            f"- event_id: `{self.context.event_id}`",
            f"- event_slug: `{self.context.normalized_event_slug}`",
            "",
        ])

    def _render_market_md(self) -> str:
        deadline = self.context.deadline.isoformat() if self.context.deadline else ""
        return "\n".join([
            "# 市场",
            "",
            f"- 问题: {self.context.market_question}",
            f"- condition_id: `{self.context.condition_id}`",
            f"- market_slug: `{self.context.normalized_market_slug}`",
            "- market_price: 仅允许在后置市场对比阶段读取",
            f"- deadline: `{deadline}`",
            "",
        ])

    def _render_resolution_md(self) -> str:
        return "\n".join([
            "# Resolution 规则",
            "",
            self.context.resolution_text or "未提供 resolution 文本。",
            "",
        ])

    def _render_run_md(self) -> str:
        return "\n".join([
            "# Agent 运行",
            "",
            f"- agent_run_id: `{self.context.agent_run_id}`",
            f"- agent_name: `{self.context.agent_name}`",
            f"- agent_model: `{self.context.agent_model}`",
            f"- created_at: `{self.context.created_at.isoformat()}`",
            f"- harness_doc_path: `{self.context.harness_doc_path}`",
            "",
        ])

    def _write_if_missing(self, path: Path, content: str) -> None:
        if not path.exists():
            self._write_text(path, content)

    def _write_text(self, path: Path, content: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def _write_json(self, path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _render_step_markdown(step: TrajectoryStep) -> str:
    lines = [
        f"## 第 {step.loop_index} 步：{step.phase}",
        "",
        f"- step_id: `{step.step_id}`",
        f"- created_at: `{step.created_at.isoformat()}`",
    ]
    if step.tool_name:
        lines.append(f"- tool: `{step.tool_name}`")
    if step.tool_action_id:
        lines.append(f"- tool_action_id: `{step.tool_action_id}`")
    lines.extend([
        "",
        f"摘要：{step.summary}",
        "",
    ])
    for label, value in [
        ("行动", step.action),
        ("观察", step.observation),
        ("分析", step.analysis),
        ("下一步决定", step.next_decision),
    ]:
        if value:
            lines.extend([f"### {label}", "", value, ""])
    return "\n".join(lines)


def _render_tool_result_markdown(result: AgentToolResult) -> str:
    lines = [
        "# 工具结果",
        "",
        f"- action_id: `{result.action_id}`",
        f"- tool_name: `{result.tool_name}`",
        f"- source_category: `{result.source_category}`",
        f"- query: {result.query}",
        f"- status: `{result.status}`",
        f"- started_at: `{result.started_at.isoformat()}`",
        f"- finished_at: `{result.finished_at.isoformat()}`",
        f"- result_count: `{len(result.results)}`",
    ]
    if result.error:
        lines.append(f"- error: {result.error}")
    lines.extend(["", "## 结果", ""])
    for idx, item in enumerate(result.results, start=1):
        lines.extend([
            f"### {idx}. {item.title}",
            "",
            f"- url: {item.url}",
            f"- source: `{item.source}`",
            f"- provider: `{item.provider}`",
            f"- score: `{item.relevance_score:.3f}`",
            "",
            item.summary,
            "",
            ])
    if result.payload:
        lines.extend([
            "",
            "## Payload",
            "",
            "```json",
            json.dumps(result.payload, ensure_ascii=False, indent=2),
            "```",
            "",
        ])
    if result.artifact_paths:
        lines.extend(["", "## 产物", ""])
        for path in result.artifact_paths:
            lines.append(f"- `{path}`")
    return "\n".join(lines)


def _render_agent_review_markdown(payload: dict[str, Any]) -> str:
    evidence_label = str(payload.get("evidence_label", "")).strip()
    source_display = str(
        payload.get("source_display")
        or evidence_label
        or "未填写",
    ).strip()
    evidence_path = str(
        payload.get("evidence_path_short")
        or payload.get("evidence_path")
        or "",
    ).strip()
    lines = [
        "# Agent 证据复盘",
        "",
        f"Source：{source_display}",
        "",
        f"- review_id: `{payload.get('review_id', '')}`",
        f"- evidence_path: `{evidence_path}`",
        f"- phase: `{payload.get('phase', 'analyze')}`",
        "",
    ]
    candidate_set_path = str(
        payload.get("candidate_set_path_short")
        or payload.get("candidate_set_path")
        or "",
    ).strip()
    if payload.get("source_url"):
        lines.insert(5, f"- source_url: {payload.get('source_url')}")
    if candidate_set_path:
        lines.insert(6, f"- candidate_set_path: `{candidate_set_path}`")
    labels = [
        ("agent_output", "Agent 输出"),
        ("observation", "观察"),
        ("assessment", "评估"),
        ("raw_materials_seen", "实际阅读材料"),
        ("source_excerpt_or_summary", "材料摘录或压缩摘要"),
        ("visible_reasoning_memo", "可展示推理札记"),
        ("source_selection_notes", "Source 选择说明"),
        ("rejected_or_downweighted", "拒绝或降权材料"),
        ("information_gap", "信息缺口"),
        ("next_search_decision", "下一步搜索决策"),
        ("stop_or_continue", "停止或继续"),
        ("confidence_note", "置信度备注"),
    ]
    for key, label in labels:
        value = str(payload.get(key, "")).strip()
        if value:
            lines.extend([f"## {label}", "", value, ""])
    return "\n".join(lines)
