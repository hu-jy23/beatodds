"""Agent loop controller for the China forecast harness."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from beatodds.agents.models import (
    AgentDecision,
    AgentRunContext,
    ForecastReportDraft,
    ToolCallRequest,
    TrajectoryStep,
)
from beatodds.agents.tool_registry import ChinaToolRegistry
from beatodds.agents.workspace import ChinaForecastWorkspace


class ScriptedChinaAgent:
    """Validation agent that follows the harness loop without external LLM calls."""

    name = "scripted_china_agent"

    def plan(self, context: AgentRunContext) -> AgentDecision:
        return AgentDecision(
            loop_index=1,
            phase="plan",
            summary="创建中国相关初始研究计划。",
            action="规划 source map、query generation、market context、search、baseline steps。",
            analysis=(
                "本 run 应先暴露 source map，再生成中国相关 queries，"
                "再收集至少一个 official/generic source 和一个 cross-check source。"
            ),
            next_decision="导出 source registry。",
            metadata={"condition_id": context.condition_id},
        )

    def tool_sequence(self, context: AgentRunContext) -> list[AgentDecision]:
        return [
            AgentDecision(
                loop_index=2,
                phase="tool",
                summary="导出本次 run 的 source registry。",
                action="调用 export_source_registry。",
                next_decision="生成中国相关 queries。",
                tool_call=ToolCallRequest(tool_name="export_source_registry"),
            ),
            AgentDecision(
                loop_index=3,
                phase="tool",
                summary="生成中国相关 search queries。",
                action="调用 generate_china_queries。",
                next_decision="读取 Polymarket context。",
                tool_call=ToolCallRequest(
                    tool_name="generate_china_queries",
                    query=context.market_question,
                ),
            ),
            AgentDecision(
                loop_index=4,
                phase="tool",
                summary="读取市场 baseline context。",
                action="调用 read_polymarket_context。",
                next_decision="搜索外部 sources。",
                tool_call=ToolCallRequest(tool_name="read_polymarket_context"),
            ),
        ]

    def search_decisions(
        self,
        generated_queries: list[dict[str, Any]],
        fallback_query: str,
        start_index: int,
    ) -> list[AgentDecision]:
        chosen = _choose_searches(generated_queries, fallback_query)
        decisions = []
        for offset, item in enumerate(chosen):
            decisions.append(
                AgentDecision(
                    loop_index=start_index + offset,
                    phase="tool",
                    summary=f"搜索 {item['source_category']} evidence。",
                    action=f"用 query 调用 search_web：{item['query']}",
                    next_decision="复盘结果；如果 evidence 不足则继续。",
                    tool_call=ToolCallRequest(
                        tool_name="search_web",
                        query=item["query"],
                        source_category=item["source_category"],
                        args={
                            "max_results": 3,
                            "reliability_prior": item.get("reliability_prior", 0.3),
                            "metadata": {"scripted_loop": True},
                        },
                    ),
                )
            )
        return decisions

    def baseline_decision(self, loop_index: int) -> AgentDecision:
        return AgentDecision(
            loop_index=loop_index,
            phase="tool",
            summary="运行市场锚定 model baseline stub。",
            action="调用 model_baseline_forecast。",
            next_decision="生成最终 forecast report。",
            tool_call=ToolCallRequest(tool_name="model_baseline_forecast"),
        )

    def report(
        self,
        context: AgentRunContext,
        tool_payloads: list[dict[str, Any]],
        evidence_paths: list[str],
        loop_index: int,
    ) -> AgentDecision:
        p_m = 0.5 if context.p_m is None else context.p_m
        baseline = _last_payload(tool_payloads, "model_baseline_forecast")
        p_f = float(baseline.get("p_f", p_m))
        confidence = _report_confidence(evidence_paths, baseline)
        report = ForecastReportDraft(
            condition_id=context.condition_id,
            p_m=context.p_m,
            p_f=p_f,
            confidence=confidence,
            p_m_delta=None if context.p_m is None else p_f - context.p_m,
            calibration_status="uncalibrated",
            model=self.name,
            evidence_paths=evidence_paths,
            report_markdown=_render_report(context, p_f, confidence, evidence_paths, tool_payloads),
            metadata={
                "agent": self.name,
                "tool_payload_count": len(tool_payloads),
                "evidence_count": len(evidence_paths),
            },
        )
        return AgentDecision(
            loop_index=loop_index,
            phase="report",
            summary="生成最终 forecast report。",
            action="写入 forecast_report.md 和 forecast_report.json。",
            analysis=(
                "在真实 model forecasting 接入前，validation run 保持市场锚定。"
            ),
            next_decision="停止。",
            report=report,
        )


class ChinaAgentLoopController:
    """Runs agent decisions, executes tool calls, and persists every step."""

    def __init__(
        self,
        registry: ChinaToolRegistry,
        agent: Any | None = None,
        harness_doc_path: str | Path | None = None,
    ):
        self.registry = registry
        self.agent = agent or ScriptedChinaAgent()
        self.harness_doc_path = Path(harness_doc_path) if harness_doc_path else None

    def run(
        self,
        context: AgentRunContext,
        workspace_root: str | Path = "workspace",
    ) -> tuple[ForecastReportDraft, ChinaForecastWorkspace]:
        workspace = ChinaForecastWorkspace.create(context, root_dir=workspace_root)
        self._copy_harness_protocol(workspace)

        tool_payloads: list[dict[str, Any]] = []
        generated_searches: list[dict[str, Any]] = []

        plan = self.agent.plan(workspace.context)
        workspace.append_plan(_decision_to_plan_md(plan))
        self._append_decision(workspace, plan)

        for decision in self.agent.tool_sequence(workspace.context):
            result = self._execute_tool(workspace, decision)
            tool_payloads.append({"tool_name": result.tool_name, **result.payload})
            if result.tool_name == "generate_china_queries":
                generated_searches = result.payload.get("recommended_searches", [])

        next_index = 5
        search_decisions = self.agent.search_decisions(
            generated_searches,
            workspace.context.market_question,
            start_index=next_index,
        )
        for decision in search_decisions:
            result = self._execute_tool(workspace, decision)
            tool_payloads.append({"tool_name": result.tool_name, **result.payload})

        baseline = self.agent.baseline_decision(next_index + len(search_decisions))
        result = self._execute_tool(workspace, baseline)
        tool_payloads.append({"tool_name": result.tool_name, **result.payload})

        evidence_paths = [
            str(path)
            for path in sorted(workspace.paths.sources_dir.glob("*/*.md"))
        ]
        report_decision = self.agent.report(
            workspace.context,
            tool_payloads,
            evidence_paths,
            loop_index=baseline.loop_index + 1,
        )
        if report_decision.report is None:
            raise RuntimeError("Agent did not produce a forecast report.")
        workspace.write_forecast_report(report_decision.report)
        self._append_decision(workspace, report_decision)
        return report_decision.report, workspace

    def run_llm(
        self,
        context: AgentRunContext,
        workspace_root: str | Path = "workspace",
        max_steps: int = 10,
    ) -> tuple[ForecastReportDraft, ChinaForecastWorkspace]:
        workspace = ChinaForecastWorkspace.create(context, root_dir=workspace_root)
        self._copy_harness_protocol(workspace)

        if not hasattr(self.agent, "decide"):
            raise TypeError("run_llm requires an agent with decide().")

        last_observation = "Initialized workspace. Start with planning or tool discovery."
        for loop_index in range(1, max_steps + 1):
            decision = self.agent.decide(
                context=workspace.context,
                workspace=workspace,
                tools=self.registry.list_tools(),
                loop_index=loop_index,
                last_observation=last_observation,
            )
            if decision.phase == "report":
                if decision.report is None:
                    raise RuntimeError("LLM report decision missing report payload.")
                evidence_paths = [
                    str(path)
                    for path in sorted(workspace.paths.sources_dir.glob("*/*.md"))
                ]
                report = decision.report.model_copy(
                    update={
                        "condition_id": workspace.context.condition_id,
                        "p_m": workspace.context.p_m,
                        "p_m_delta": (
                            None if workspace.context.p_m is None
                            else decision.report.p_f - workspace.context.p_m
                        ),
                        "evidence_paths": decision.report.evidence_paths or evidence_paths,
                    }
                )
                workspace.write_forecast_report(report)
                self._append_decision(workspace, decision)
                return report, workspace

            if decision.phase == "plan":
                workspace.append_plan(_decision_to_plan_md(decision))
                self._append_decision(workspace, decision)
                last_observation = "计划已记录。"
                continue

            if decision.phase != "tool":
                self._append_decision(workspace, decision)
                last_observation = f"已记录 {decision.phase} step。"
                continue

            result = self._execute_tool(workspace, decision)
            last_observation = result.metadata.get("observation_digest", "")

        report = _fallback_max_step_report(workspace.context, workspace, max_steps=max_steps)
        workspace.write_forecast_report(report)
        self._append_decision(
            workspace,
            AgentDecision(
                loop_index=max_steps + 1,
                phase="report",
                summary="达到最大 LLM 步数后的 fallback report。",
                action="写入 fallback forecast report。",
                analysis="LLM loop 在输出 report 前达到了 max_steps。",
                next_decision="停止。",
                report=report,
            ),
        )
        return report, workspace

    def _execute_tool(
        self,
        workspace: ChinaForecastWorkspace,
        decision: AgentDecision,
    ):
        if decision.tool_call is None:
            raise ValueError("Tool decision missing tool_call.")
        call = decision.tool_call
        call_args = _tool_call_args(call)
        result = self.registry.run(
            call.tool_name,
            query=call.query,
            source_category=call.source_category,
            context=workspace.context,
            workspace=workspace,
            **call_args,
        )
        recorded_paths = workspace.record_tool_result(result)
        source_paths = [str(path) for path in recorded_paths if "/sources/" in str(path)]
        for item, path in zip(result.results, source_paths, strict=False):
            workspace.append_claim(
                claim=_compact_claim(item),
                direction="neutral",
                source_path=path,
            )
        observation = _render_tool_observation(result, recorded_paths)
        result = result.model_copy(
            update={
                "metadata": {
                    **result.metadata,
                    "observation_digest": observation,
                }
            }
        )
        self._append_decision(
            workspace,
            decision,
            observation=observation,
            tool_name=result.tool_name,
            tool_action_id=result.action_id,
        )
        if result.status == "error":
            workspace.append_trajectory(
                TrajectoryStep(
                    loop_index=decision.loop_index,
                    phase="review",
                    summary=f"Tool error from {result.tool_name}.",
                    observation=result.error,
                    next_decision="Continue with available evidence.",
                    tool_name=result.tool_name,
                    tool_action_id=result.action_id,
                )
            )
        return result

    def _append_decision(
        self,
        workspace: ChinaForecastWorkspace,
        decision: AgentDecision,
        observation: str = "",
        tool_name: str = "",
        tool_action_id: str = "",
    ) -> None:
        workspace.append_trajectory(
            TrajectoryStep(
                loop_index=decision.loop_index,
                phase=decision.phase,
                summary=decision.summary,
                action=decision.action,
                observation=observation,
                analysis=decision.analysis,
                next_decision=decision.next_decision,
                tool_name=tool_name,
                tool_action_id=tool_action_id,
                metadata=decision.metadata,
            )
        )

    def _copy_harness_protocol(self, workspace: ChinaForecastWorkspace) -> None:
        if not self.harness_doc_path or not self.harness_doc_path.exists():
            return
        text = self.harness_doc_path.read_text(encoding="utf-8")
        protocol_path = workspace.paths.run_dir / "harness_protocol.md"
        protocol_path.write_text(_extract_runtime_protocol(text), encoding="utf-8")


def _choose_searches(generated: list[dict[str, Any]], fallback_query: str) -> list[dict[str, Any]]:
    if not generated:
        return [
            {
                "query": fallback_query,
                "source_category": "generic_search_tools",
                "reliability_prior": 0.3,
            },
            {
                "query": f"{fallback_query} Reuters Bloomberg",
                "source_category": "foreign_crosscheck",
                "reliability_prior": 0.65,
            },
        ]
    official = next((item for item in generated if item["source_category"] == "official"), None)
    crosscheck = next(
        (item for item in generated if item["source_category"] == "foreign_crosscheck"),
        None,
    )
    generic = next(
        (item for item in generated if item["source_category"] == "generic_search_tools"),
        None,
    )
    return [item for item in [official, crosscheck, generic] if item][:3]


def _last_payload(payloads: list[dict[str, Any]], tool_name: str) -> dict[str, Any]:
    for payload in reversed(payloads):
        if payload.get("tool_name") == tool_name:
            return payload
    return {}


def _report_confidence(evidence_paths: list[str], baseline: dict[str, Any]) -> float:
    base = float(baseline.get("confidence", 0.15))
    evidence_bonus = min(0.25, len(evidence_paths) * 0.05)
    return min(0.6, base + evidence_bonus)


def _render_report(
    context: AgentRunContext,
    p_f: float,
    confidence: float,
    evidence_paths: list[str],
    tool_payloads: list[dict[str, Any]],
) -> str:
    p_delta = "" if context.p_m is None else f"{p_f - context.p_m:+.4f}"
    lines = [
        "# 预测报告",
        "",
        f"- 市场: {context.market_question}",
        f"- condition_id: `{context.condition_id}`",
        f"- p_m: `{'' if context.p_m is None else f'{context.p_m:.4f}'}`",
        f"- p_f: `{p_f:.4f}`",
        f"- p_m_delta: `{p_delta}`",
        f"- confidence: `{confidence:.2f}`",
        "- calibration_status: `uncalibrated`",
        "",
        "## 评估",
        "",
        "这是 scripted harness agent 生成的验证报告。它用于确认完整 loop 可以创建 "
        "workspace、暴露工具、执行搜索、保存 evidence、写出机器可读 forecast report。"
        "在真实 forecasting agent 接入前，概率保持市场锚定。",
        "",
        "## 证据文件",
        "",
    ]
    lines.extend(f"- `{path}`" for path in evidence_paths)
    lines.extend([
        "",
        "## Tool Payload 摘要",
        "",
    ])
    for payload in tool_payloads:
        lines.append(f"- `{payload.get('tool_name', '')}` keys={sorted(payload.keys())}")
    lines.append("")
    return "\n".join(lines)


def _decision_to_plan_md(decision: AgentDecision) -> str:
    return "\n".join([
        "## 初始计划",
        "",
        f"- summary: {decision.summary}",
        f"- action: {decision.action}",
        f"- analysis: {decision.analysis}",
        f"- next_decision: {decision.next_decision}",
    ])


def _extract_runtime_protocol(text: str) -> str:
    marker = "### 3.11 运行时 Harness Prompt 草稿"
    if marker in text:
        return text[text.index(marker):].strip() + "\n"
    return text


def _render_tool_observation(result, recorded_paths: list[Path]) -> str:
    lines = [
        f"status={result.status}",
        f"tool={result.tool_name}",
        f"source_category={result.source_category}",
        f"query={result.query}",
        f"results={len(result.results)}",
        f"artifacts={len(recorded_paths)}",
    ]
    if result.error:
        lines.append(f"error={result.error}")
    if recorded_paths:
        lines.append("artifact_paths:")
        lines.extend(f"- {path}" for path in recorded_paths[:8])
    if result.results:
        lines.append("evidence_digest:")
        for idx, item in enumerate(result.results[:5], start=1):
            lines.extend([
                f"{idx}. 标题={item.title}",
                f"   来源={item.source}",
                f"   url={item.url}",
                f"   summary={item.summary[:500]}",
            ])
    if result.payload:
        keys = sorted(result.payload.keys())
        lines.append(f"payload_keys={keys}")
    return "\n".join(lines)


def _compact_claim(item) -> str:
    title = " ".join((item.title or "").split())
    summary = " ".join((item.summary or "").split())
    if title and summary:
        return f"{title}: {summary[:360]}"
    return summary[:360] or title


def _tool_call_args(call) -> dict:
    args = dict(call.args)
    if call.url and "url" not in args:
        args["url"] = call.url
    if (
        call.tool_name == "process_resource"
        and "url" not in args
        and call.query.startswith(("http://", "https://"))
    ):
        args["url"] = call.query
    return args


def _fallback_max_step_report(
    context: AgentRunContext,
    workspace: ChinaForecastWorkspace,
    max_steps: int,
) -> ForecastReportDraft:
    p_m = 0.5 if context.p_m is None else context.p_m
    evidence_paths = [
        str(path)
        for path in sorted(workspace.paths.sources_dir.glob("*/*.md"))
    ]
    return ForecastReportDraft(
        condition_id=context.condition_id,
        p_m=context.p_m,
        p_f=p_m,
        confidence=0.1,
        p_m_delta=None if context.p_m is None else 0.0,
        calibration_status="uncalibrated",
        model="llm_china_agent_fallback",
        evidence_paths=evidence_paths,
        report_markdown="\n".join([
        "# 预测报告",
        "",
            f"- 市场: {context.market_question}",
            f"- condition_id: `{context.condition_id}`",
            f"- p_m: `{context.p_m}`",
            f"- p_f: `{p_m:.4f}`",
            "- p_m_delta: `+0.0000`",
            "- confidence: `0.10`",
            "- calibration_status: `uncalibrated`",
            "",
            "## 评估",
            "",
            f"达到 max_steps={max_steps} 后生成 fallback report。",
            "",
        ]),
        metadata={"fallback_reason": "max_steps"},
    )
