#!/usr/bin/env python3
"""Bootstrap a Markdown-defined local Codex forecast task."""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from beatodds.agents.local_harness import LOCAL_MAIN_AGENT, write_local_agent_bootstrap
from beatodds.agents.models import AgentRunContext
from beatodds.agents.tool_registry import default_china_tool_registry
from beatodds.agents.workspace import ChinaForecastWorkspace


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "根据 Q + resolution 准备 Markdown 定义的 forecast workspace。"
            "task.md 写入后，本地 Codex agent 负责 workflow。"
        )
    )
    parser.add_argument("--event-title", required=True)
    parser.add_argument("--market", required=True, help="市场问题")
    parser.add_argument("--condition-id", default="")
    parser.add_argument("--event-id", default="")
    parser.add_argument("--event-slug", default="")
    parser.add_argument("--market-slug", default="")
    parser.add_argument("--resolution", default="")
    parser.add_argument("--p-m", type=float, default=None)
    parser.add_argument("--agent-name", default="")
    parser.add_argument("--agent-run-id", default="")
    parser.add_argument("--agent-model", default=LOCAL_MAIN_AGENT)
    parser.add_argument("--workspace-root", default="workspace")
    parser.add_argument(
        "--harness-doc",
        default="../China-Specific计划.md",
        help="可用时复制进 run workspace 的概念 harness 计划。",
    )
    args = parser.parse_args()

    context = AgentRunContext(
        event_title=args.event_title,
        market_question=args.market,
        condition_id=args.condition_id,
        event_id=args.event_id,
        event_slug=args.event_slug,
        market_slug=args.market_slug,
        resolution_text=args.resolution,
        p_m=args.p_m,
        agent_name=args.agent_name or _agent_name_from_model(args.agent_model),
        agent_run_id=args.agent_run_id,
        agent_model=args.agent_model,
        harness_doc_path=args.harness_doc,
    )
    workspace = ChinaForecastWorkspace.create(context, root_dir=args.workspace_root)
    harness_doc_path = _resolve_harness_doc(args.harness_doc)
    _copy_harness_doc(workspace, harness_doc_path)

    registry = default_china_tool_registry(enable_model_baseline_llm=False)
    task_path, manifest_path, prompt_path = write_local_agent_bootstrap(
        workspace=workspace,
        tools=registry.list_tools(),
        harness_doc_path=harness_doc_path,
    )

    print(f"workspace={workspace.paths.run_dir}")
    print(f"task={task_path}")
    print(f"tool_manifest={manifest_path}")
    print(f"codex_prompt={prompt_path}")
    print(f"main_agent={args.agent_model}")
    print(
        "next=用 codex_prompt.md 启动本地 Codex agent；"
        "然后等待 forecast_report.md/json/pdf。"
    )
    print("status=ok")


def _agent_name_from_model(model: str) -> str:
    return model.split(":", 1)[1] if ":" in model else model


def _resolve_harness_doc(path_text: str) -> Path | None:
    path = Path(path_text)
    candidates = [
        path,
        Path.cwd() / path,
        Path(__file__).parent.parent / path,
        Path(__file__).parent.parent.parent / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate.resolve()
    return None


def _copy_harness_doc(workspace: ChinaForecastWorkspace, source: Path | None) -> None:
    if source is None:
        return
    target = workspace.paths.run_dir / "harness_protocol.md"
    shutil.copyfile(source, target)


if __name__ == "__main__":
    main()
