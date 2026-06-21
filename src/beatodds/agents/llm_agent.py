"""LLM-backed China forecast agent."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from beatodds.agents.models import (
    AgentDecision,
    AgentRunContext,
    AgentToolSpec,
    ForecastReportDraft,
)
from beatodds.agents.workspace import ChinaForecastWorkspace
from beatodds.common.config import get_settings


class LLMChinaAgent:
    """Agent that reads the runtime harness protocol and emits structured decisions."""

    name = "llm_china_agent"

    def __init__(
        self,
        model: str | None = None,
        client: Any | None = None,
        max_tokens: int = 1600,
    ):
        self.cfg = get_settings()
        self.model = model or self.cfg.deepseek_model or self.cfg.openai_model
        self._client = client
        self.max_tokens = max_tokens

    def decide(
        self,
        context: AgentRunContext,
        workspace: ChinaForecastWorkspace,
        tools: list[AgentToolSpec],
        loop_index: int,
        last_observation: str = "",
    ) -> AgentDecision:
        raw = self._call_llm(
            system_prompt=_system_prompt(),
            user_prompt=_user_prompt(
                context=context,
                workspace=workspace,
                tools=tools,
                loop_index=loop_index,
                last_observation=last_observation,
            ),
        )
        decision = _parse_decision(raw, context=context, loop_index=loop_index, model=self.model)
        if decision.loop_index != loop_index:
            decision = decision.model_copy(update={"loop_index": loop_index})
        return decision

    def _get_client(self):
        if self._client is not None:
            return self._client
        import openai

        if self.cfg.llm_backend == "deepseek":
            self._client = openai.OpenAI(
                api_key=self.cfg.deepseek_api_key,
                base_url=self.cfg.deepseek_base_url,
            )
        elif self.cfg.llm_backend == "openai":
            self._client = openai.OpenAI(api_key=self.cfg.openai_api_key)
        else:
            raise RuntimeError("LLMChinaAgent requires DEEPSEEK_API_KEY or OPENAI_API_KEY.")
        return self._client

    def _call_llm(self, system_prompt: str, user_prompt: str) -> str:
        client = self._get_client()
        response = client.chat.completions.create(
            model=self.model,
            max_tokens=self.max_tokens,
            temperature=0.2,
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise RuntimeError("LLM returned empty decision.")
        return content


def _system_prompt() -> str:
    return """你是中国相关 prediction market forecasting agent。

你必须遵守 runtime harness protocol。你不直接写文件。
你每轮只输出一个 JSON object。controller 会执行 tool call，
并落盘 trajectory、source cards、claims、reports。

除 JSON key、工具名、source category id、文件名、URL、命令行参数外，
所有自然语言字段必须使用中文。
不要输出 hidden chain-of-thought。使用简洁、可审计的 rationale。
核心审计要求是 evidence-driven search：每次拿到 evidence 后，
下一次 tool decision 必须说明当前 evidence 说明了什么、还缺什么、
为什么下一次搜索能补上该缺口。
"""


def _user_prompt(
    context: AgentRunContext,
    workspace: ChinaForecastWorkspace,
    tools: list[AgentToolSpec],
    loop_index: int,
    last_observation: str,
) -> str:
    protocol = _read_optional(workspace.paths.run_dir / "harness_protocol.md")
    state = _workspace_state(workspace)
    return f"""## Runtime Protocol
{protocol}

## Run Context
{json.dumps(context.model_dump(mode="json"), ensure_ascii=False, indent=2)}

## Available Tools
{json.dumps([tool.model_dump(mode="json") for tool in tools], ensure_ascii=False, indent=2)}

## Workspace State
{json.dumps(state, ensure_ascii=False, indent=2)}

## Last Observation
{last_observation}

## Required Output
只返回一个 JSON object。JSON key 保持英文，所有自然语言 value 使用中文。

如果要调用工具：
{{
  "phase": "tool",
  "summary": "中文短摘要",
  "action": "你将做什么",
  "analysis": "为什么这是当前最有用的下一步",
  "next_decision": "观察结果后预计要判断什么",
  "tool_call": {{
    "tool_name": "一个可用工具名",
    "query": "query 或 URL",
    "url": "tool_name 为 process_resource 时填写 URL，否则留空",
    "source_category": "一个 source category",
    "args": {{}}
  }}
}}

如果已有足够信息生成报告：
{{
  "phase": "report",
  "summary": "中文短摘要",
  "action": "写 forecast report",
  "analysis": "中文简洁 rationale",
  "next_decision": "停止。",
  "report": {{
    "p_f": 0.0,
    "confidence": 0.0,
    "calibration_status": "uncalibrated",
    "report_markdown": "# 预测报告\\n..."
  }}
}}

审计要求：
- 不要机械执行固定 checklist。
- 如果已经有 source cards 或 claims，下一次搜索必须基于它们。
- `analysis` 必须明确当前 evidence state 和剩余信息缺口。
- `next_decision` 必须说明什么 evidence 会让你继续或停止。
- 生成报告前，trajectory 必须体现 evidence -> analysis -> next search，
  或明确说明为什么继续搜索价值不足。
- 中国相关 market 在工具可用时，报告前优先覆盖多类 source category。
- 除非实际使用经验校准，否则 `calibration_status` 必须是 "uncalibrated"。
- Confidence 是证据置信度，不是 YES/NO 结果确定性；地缘政治和精英政治不要过度自信。

当前 loop_index = {loop_index}.
"""


def _workspace_state(workspace: ChinaForecastWorkspace) -> dict[str, Any]:
    return {
        "run_dir": str(workspace.paths.run_dir),
        "source_cards": [str(path) for path in sorted(workspace.paths.sources_dir.glob("*/*.md"))],
        "search_actions": [
            str(path) for path in sorted(workspace.paths.search_actions_dir.glob("*.md"))
        ],
        "claims_tail": _tail(workspace.paths.run_dir / "claims.md", max_chars=2500),
        "trajectory_tail": _tail(workspace.paths.run_dir / "trajectory.md", max_chars=3500),
        "generated_queries": (
            _read_optional(workspace.paths.run_dir / "generated_queries.md")[:2500]
        ),
        "polymarket_context": (
            _read_optional(workspace.paths.run_dir / "polymarket_context.md")[:2500]
        ),
        "model_baseline": _read_optional(workspace.paths.run_dir / "model_baseline.md")[:1500],
        "recent_source_card_contents": _recent_source_card_contents(workspace),
    }


def _parse_decision(
    raw: str,
    context: AgentRunContext,
    loop_index: int,
    model: str,
) -> AgentDecision:
    data = _json_loads(raw)
    if "loop_index" not in data:
        data["loop_index"] = loop_index
    if data.get("phase") == "report":
        report_payload = data.get("report") or {}
        default_p = context.p_m if context.p_m is not None else 0.5
        p_f = _clamp_probability(report_payload.get("p_f", default_p))
        confidence = _clamp_probability(report_payload.get("confidence", 0.3))
        report = ForecastReportDraft(
            condition_id=context.condition_id,
            p_m=context.p_m,
            p_f=p_f,
            confidence=confidence,
            p_m_delta=None if context.p_m is None else p_f - context.p_m,
            calibration_status=_normalize_calibration_status(
                report_payload.get("calibration_status", "uncalibrated")
            ),
            report_markdown=report_payload.get("report_markdown") or _fallback_report_md(
                context,
                p_f,
                confidence,
                data.get("analysis", ""),
            ),
            model=model,
            evidence_paths=report_payload.get("evidence_paths", []),
            metadata={"agent": LLMChinaAgent.name},
        )
        data["report"] = report.model_dump(mode="python")
    try:
        decision = AgentDecision(**data)
    except ValidationError as exc:
        raise RuntimeError(f"Invalid LLM decision JSON: {exc}\nraw={raw}") from exc
    if decision.phase == "tool" and decision.tool_call is None:
        raise RuntimeError(f"LLM selected tool phase without tool_call: {raw}")
    return decision


def _json_loads(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    if cleaned.startswith("```"):
        parts = cleaned.split("```")
        cleaned = parts[1] if len(parts) > 1 else cleaned
        if cleaned.startswith("json"):
            cleaned = cleaned[4:]
    data = json.loads(cleaned.strip())
    if not isinstance(data, dict):
        raise RuntimeError("LLM decision must be a JSON object.")
    return data


def _clamp_probability(value: Any) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.5
    return max(0.0, min(1.0, number))


def _normalize_calibration_status(value: Any) -> str:
    status = str(value or "").strip().lower()
    if status in {"uncalibrated", "not_calibrated", "not calibrated"}:
        return "uncalibrated"
    return "uncalibrated"


def _fallback_report_md(
    context: AgentRunContext,
    p_f: float,
    confidence: float,
    analysis: str,
) -> str:
    delta = "" if context.p_m is None else f"{p_f - context.p_m:+.4f}"
    return "\n".join([
        "# 预测报告",
        "",
        f"- 市场: {context.market_question}",
        f"- condition_id: `{context.condition_id}`",
        f"- p_m: `{'' if context.p_m is None else f'{context.p_m:.4f}'}`",
        f"- p_f: `{p_f:.4f}`",
        f"- p_m_delta: `{delta}`",
        f"- confidence: `{confidence:.2f}`",
        "- calibration_status: `uncalibrated`",
        "",
        "## 评估",
        "",
        analysis or "未提供额外评估。",
        "",
    ])


def _read_optional(path: Path) -> str:
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _tail(path: Path, max_chars: int) -> str:
    text = _read_optional(path)
    return text[-max_chars:]


def _recent_source_card_contents(workspace: ChinaForecastWorkspace) -> list[dict[str, str]]:
    cards = sorted(workspace.paths.sources_dir.glob("*/*.md"))[-5:]
    return [
        {
            "path": str(path),
            "content": _read_optional(path)[:1800],
        }
        for path in cards
    ]
