import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from beatodds.agents.access_tools import sync_video_render_artifacts
from beatodds.agents.controller import ChinaAgentLoopController
from beatodds.agents.llm_agent import LLMChinaAgent
from beatodds.agents.local_harness import LOCAL_MAIN_AGENT, write_local_agent_bootstrap
from beatodds.agents.models import (
    AgentRunContext,
    ForecastOutcomeProbability,
    MultiOutcomeForecast,
    TrajectoryStep,
    agent_workspace_name,
)
from beatodds.agents.source_routing import is_video_source_url
from beatodds.agents.tool_registry import default_china_tool_registry
from beatodds.agents.video_reporter import finalize_video_resource_report
from beatodds.agents.video_source_access import _subtitle_summary
from beatodds.agents.workspace import ChinaForecastWorkspace
from beatodds.evidence.providers.base import SearchResult
from beatodds.evidence.providers.mock_provider import MockSearchProvider


def _context() -> AgentRunContext:
    return AgentRunContext(
        event_title="Will China invade Taiwan by 2026?",
        market_question="Will China invade Taiwan by end of 2026?",
        condition_id="0xtaiwan",
        resolution_text="Resolves YES if China launches an invasion of Taiwan by Dec 31, 2026.",
        p_m=0.0625,
        agent_name="test_agent",
        agent_run_id="test_agent_20260609_000001",
        created_at=datetime(2026, 6, 9, 8, 0, tzinfo=timezone.utc),
    )


def test_workspace_initializes_event_market_and_run_files(tmp_path) -> None:
    workspace = ChinaForecastWorkspace.create(_context(), root_dir=tmp_path)

    assert workspace.paths.event_dir.name == "will_china_invade_taiwan_by_2026"
    assert workspace.paths.market_dir.name == "will_china_invade_taiwan_by_end_of_2026"
    assert workspace.paths.run_dir.name == "test_agent_20260609_000001"
    assert (workspace.paths.event_dir / "event.md").exists()
    assert (workspace.paths.market_dir / "market.md").exists()
    assert (workspace.paths.market_dir / "resolution.md").exists()
    assert (workspace.paths.run_dir / "market.md").exists()
    assert (workspace.paths.run_dir / "resolution.md").exists()
    assert (workspace.paths.run_dir / "run.md").exists()
    assert (workspace.paths.run_dir / "source_plan.md").exists()
    assert (workspace.paths.run_dir / "trajectory.md").exists()
    assert workspace.paths.agent_reviews_dir.exists()
    assert (workspace.paths.run_dir / "run_context.json").exists()
    market_md = (workspace.paths.market_dir / "market.md").read_text(encoding="utf-8")
    assert "- p_m: `0.0625`" not in market_md
    assert "后置市场对比" in market_md


def test_workspace_uses_agent_name_when_run_id_is_omitted(tmp_path) -> None:
    context = AgentRunContext(
        event_title="Will China invade Taiwan by 2026?",
        market_question="Will China invade Taiwan by end of 2026?",
        resolution_text="Resolves YES if China launches an invasion of Taiwan by Dec 31, 2026.",
        p_m=0.0625,
        agent_name="gpt-5.4-mini",
        agent_model="codex:gpt-5.4-mini",
    )

    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)

    assert agent_workspace_name(agent_model="codex:gpt-5.4-mini") == "gpt-5.4-mini"
    assert workspace.paths.run_dir.name == "gpt-5.4-mini"


def test_local_harness_bootstrap_writes_task_and_manifest(tmp_path) -> None:
    workspace = ChinaForecastWorkspace.create(_context(), root_dir=tmp_path)
    registry = default_china_tool_registry(provider=MockSearchProvider())

    task_path, manifest_path, prompt_path = write_local_agent_bootstrap(
        workspace=workspace,
        tools=registry.list_tools(),
    )

    task = task_path.read_text(encoding="utf-8")
    manifest = manifest_path.read_text(encoding="utf-8")
    prompt = prompt_path.read_text(encoding="utf-8")
    assert LOCAL_MAIN_AGENT in task
    assert "这个 Markdown 文件定义本次 workflow" in task
    assert "代码 controller 只负责创建 workspace 和工具入口" in task
    assert "主 agent 必须从本文件开始端到端完成本次 run" in task
    assert "source_plan.md" in task
    assert "所有自然语言内容必须使用中文" in task
    assert "先写中文释义" in task
    assert "B站/YouTube 中文" in task
    assert "《视频标题》（BVxxxx）" in task
    assert "不得只写 `BV1...`" in task
    assert "Taiwan-side、regional、foreign media" in task
    assert "后置市场对比" in task
    assert "- p_m: `0.0625`" not in task
    assert "不得使用 `LLMChinaAgent`" in task
    assert "证据 k -> 分析 k -> 搜索 k+1 -> 证据 k+1" in task
    assert "forecast_report.tex" in task
    assert "forecast_report.pdf" in task
    assert "final self-review hook" in task
    assert "softlink_trajectory_appendix" in task
    assert "agent_reviews/" in task
    assert "agent_review" in manifest
    assert "official/semi_official 主要做口径校验" in manifest
    assert "scripts/china_harness_tool.py" in manifest
    assert "render_forecast_report_pdf" in manifest
    assert "search_web" in manifest
    assert str(task_path) in prompt
    assert "运行 Markdown 定义的预测 harness，直到完成" in prompt
    assert "先写中文释义" in prompt


def test_run_china_harness_prepares_md_first_workspace(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_china_harness.py",
            "--event-title",
            "Will China invade Taiwan by 2026?",
            "--market",
            "Will China invade Taiwan by end of 2026?",
            "--condition-id",
            "0xtaiwan_launcher",
            "--resolution",
            "Resolves YES if China launches an invasion of Taiwan by Dec 31, 2026.",
            "--p-m",
            "0.0625",
            "--workspace-root",
            str(tmp_path),
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "status=ok" in result.stdout
    run_dirs = [path for path in tmp_path.glob("*/*/*") if (path / "task.md").exists()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert (run_dir / "task.md").exists()
    assert (run_dir / "tool_manifest.md").exists()
    assert (run_dir / "codex_prompt.md").exists()
    assert "gpt-5.4-mini" in result.stdout
    task_text = (run_dir / "task.md").read_text(encoding="utf-8")
    manifest_text = (run_dir / "tool_manifest.md").read_text(encoding="utf-8")
    assert "Video Render Subagent 协议" in task_text
    assert "items[type=skill]" in task_text
    assert "subagent_spawn_prompt.md" in task_text
    assert "asr.lock.json" in task_text
    assert "禁止启动第二个 ASR" in task_text
    assert "finalize_video_report" in task_text
    assert "finalize_video_report" in manifest_text
    assert "/home/hjy/.codex/skills/bilibili-render-pdf/SKILL.md" in manifest_text
    assert "/home/hjy/.codex/skills/youtube-render-pdf/SKILL.md" in manifest_text
    assert "china_forecasts" not in result.stdout
    assert not (run_dir / "forecast_report.md").exists()


def test_run_china_harness_accepts_explicit_short_slugs(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            sys.executable,
            "scripts/run_china_harness.py",
            "--event-title",
            "Will China invade Taiwan by 2026?",
            "--market",
            "Will China invade Taiwan by end of 2026?",
            "--condition-id",
            "0xtaiwan_launcher_short",
            "--event-slug",
            "will_china_invade_taiwan_by",
            "--market-slug",
            "2026",
            "--resolution",
            "Resolves YES if China launches an invasion of Taiwan by Dec 31, 2026.",
            "--p-m",
            "0.0625",
            "--workspace-root",
            str(tmp_path),
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "status=ok" in result.stdout
    run_dirs = [path for path in tmp_path.glob("*/*/*") if (path / "task.md").exists()]
    assert len(run_dirs) == 1
    run_dir = run_dirs[0]
    assert run_dir.parent.name == "2026"
    assert run_dir.parent.parent.name == "will_china_invade_taiwan_by"


def test_render_forecast_report_pdf_outputs_pdf_and_chart(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = ChinaForecastWorkspace.create(_context(), root_dir=tmp_path)
    (workspace.paths.run_dir / "forecast_report.md").write_text(
        "\n".join([
            "# 预测报告",
            "",
            "## 市场概况",
            "",
            "- p_m: 0.0625",
            "- p_f: 0.0500",
            "",
            "## 理由",
            "",
            "测试报告正文。",
            "",
            "| agent | p_f | verdict |",
            "|---|---:|---|",
            "| gpt-5.4-1 | 0.030 | material_overestimate |",
            "",
            "## 完整轨迹附录",
            "",
            "完整轨迹见 `full_trajectory.md`。",
        ]),
        encoding="utf-8",
    )
    (workspace.paths.run_dir / "full_trajectory.md").write_text(
        "\n".join([
            "# 完整轨迹留档",
            "",
            "## Evidence Review 1",
            "",
            "### 实际阅读材料",
            "",
            "测试 evidence artifact。",
        ]),
        encoding="utf-8",
    )
    (workspace.paths.run_dir / "forecast_report.json").write_text(
        json.dumps({
            "condition_id": "0xtaiwan",
            "p_evidence": 0.045,
            "p_m": 0.0625,
            "p_f": 0.05,
            "p_m_delta": -0.0125,
            "confidence": 0.3,
            "calibration_status": "uncalibrated",
            "model": "codex:gpt-5.4-mini",
        }),
        encoding="utf-8",
    )

    result = subprocess.run(
        [
            sys.executable,
            "scripts/render_forecast_report_pdf.py",
            "--workspace",
            str(workspace.paths.run_dir),
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    pdf_path = workspace.paths.run_dir / "forecast_report.pdf"
    tex_path = workspace.paths.run_dir / "forecast_report.tex"
    chart_path = workspace.paths.artifacts_dir / "report_charts" / "probability_summary.png"
    assert "status=ok" in result.stdout
    assert tex_path.exists()
    tex_text = tex_path.read_text(encoding="utf-8")
    md_text = (workspace.paths.run_dir / "forecast_report.md").read_text(encoding="utf-8")
    assert "ctexart" in tex_text
    assert "tabularx" in tex_text
    assert "gpt-5.4-1" in tex_text
    assert "预测报告" in tex_text
    assert "Evidence Review 1" in tex_text
    assert "测试 evidence artifact" in tex_text
    assert "完整轨迹见 `full_trajectory.md`" not in md_text
    assert "测试 evidence artifact" in md_text
    assert "已直接嵌入报告正文和 PDF 附录" in md_text
    assert pdf_path.exists()
    assert pdf_path.stat().st_size > 1000
    assert chart_path.exists()
    assert chart_path.stat().st_size > 1000


def test_audit_rejects_softlink_trajectory_appendix(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = ChinaForecastWorkspace.create(_context(), root_dir=tmp_path)
    run_dir = workspace.paths.run_dir
    required_content = {
        "source_plan.md": "# 来源计划\n\nsource query 候选\n",
        "plan.md": "# 计划\n\n下一步\n",
        "trajectory.md": "# 轨迹\n\nEvidence -> Think -> Next\n",
        "full_trajectory.md": (
            "# 完整轨迹留档\n\n"
            "## Evidence Review 1\n\n"
            "### 实际阅读材料\n\nartifact\n\n"
            "### 可展示推理札记\n\nmemo\n"
        ),
        "thesis_review.md": "# Thesis Review\n\nProbability Floor\nPaper Trade View\n",
        "audit.md": "\n".join([
            "## current_state_evidence",
            "## future_change_mechanisms",
            "## future_or_prediction_sources_attempted",
            "## low_signal_sources_rejected",
            "## why_future_exploration_is_sufficient_or_blocked",
            "## what_new_information_would_change_forecast",
        ]),
        "forecast_report.md": "\n".join([
            "# 预测报告",
            "",
            "## 结论先行",
            "",
            "Mispricing Verdict / Paper Trade View / Probability Floor / 最强反方",
            "",
            "## 最终终止决策",
            "",
            "触发终止的主体：主 agent。停止。",
            "",
            "## 完整轨迹附录",
            "",
            "见 `full_trajectory.md`。",
            "",
            "## Evidence Review 1",
            "",
            "- 实际阅读材料: 见下方嵌入的完整轨迹正文。",
        ]),
    }
    for name, content in required_content.items():
        (run_dir / name).write_text(content, encoding="utf-8")
    (run_dir / "forecast_report.json").write_text(
        json.dumps({
            "condition_id": "0xtaiwan",
            "p_f": 0.01,
            "p_m": 0.06,
            "p_m_delta": -0.05,
            "confidence": 0.7,
            "calibration_status": "uncalibrated",
            "mispricing_verdict": "absolute_overestimate",
            "paper_trade_view": {"direction": "buy_no"},
            "evidence_paths": ["a", "b", "c"],
        }),
        encoding="utf-8",
    )
    (run_dir / "forecast_report.pdf").write_bytes(b"%PDF-1.4\nplaceholder\n")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/audit_china_harness_run.py",
            "--workspace",
            str(run_dir),
            "--json",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["status"] == "fail"
    assert "softlink_trajectory_appendix" in payload["failed"]
    assert "missing_full_trajectory_appendix" in payload["failed"]


def test_audit_rejects_non_human_readable_evidence_reviews(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = ChinaForecastWorkspace.create(_context(), root_dir=tmp_path)
    run_dir = workspace.paths.run_dir
    required_content = {
        "source_plan.md": "# 来源计划\n\nsource query 候选\n",
        "plan.md": "# 计划\n\n下一步\n",
        "trajectory.md": "# 轨迹\n\nEvidence -> Think -> Next\n",
        "full_trajectory.md": (
            "# 完整轨迹留档\n\n"
            "## Evidence Review 1\n\n"
            "- evidence_label: B站候选池\n"
            "- review_path: `workspace/will_china_invade_taiwan_by/2026/run/agent_reviews/001.md`\n"
            "- evidence_path: "
            "`workspace/will_china_invade_taiwan_by/2026/run/source_visits/001.md`\n\n"
            "### 实际阅读材料\n\nartifact\n\n"
            "### 材料摘录或压缩摘要\n\nB站材料。\n\n"
            "### 可展示推理札记\n\nmemo\n\n"
            "### 拒绝或降权材料\n\nnone\n"
        ),
        "thesis_review.md": "# Thesis Review\n\nProbability Floor\nPaper Trade View\n",
        "audit.md": "\n".join([
            "## current_state_evidence",
            "## future_change_mechanisms",
            "## future_or_prediction_sources_attempted",
            "## low_signal_sources_rejected",
            "## why_future_exploration_is_sufficient_or_blocked",
            "## what_new_information_would_change_forecast",
        ]),
        "forecast_report.md": "\n".join([
            "# 预测报告",
            "",
            "## 结论先行",
            "",
            "Mispricing Verdict / Paper Trade View / Probability Floor / 最强反方",
            "",
            "## 最终终止决策",
            "",
            "触发终止的主体：主 agent。停止。",
            "",
            "## 完整轨迹附录",
            "",
            "## Evidence Review 1",
            "",
            "- evidence_label: B站候选池",
            "- review_path: `workspace/will_china_invade_taiwan_by/2026/run/agent_reviews/001.md`",
            "",
            "### 实际阅读材料",
            "",
            "artifact",
            "",
            "### 材料摘录或压缩摘要",
            "",
            "B站材料。",
            "",
            "### 可展示推理札记",
            "",
            "memo",
            "",
            "### 拒绝或降权材料",
            "",
            "none",
        ]),
    }
    for name, content in required_content.items():
        (run_dir / name).write_text(content, encoding="utf-8")
    (run_dir / "forecast_report.json").write_text(
        json.dumps({
            "condition_id": "0xtaiwan",
            "p_f": 0.01,
            "p_m": 0.06,
            "p_m_delta": -0.05,
            "confidence": 0.7,
            "calibration_status": "uncalibrated",
            "mispricing_verdict": "absolute_overestimate",
            "paper_trade_view": {"direction": "buy_no"},
            "evidence_paths": ["a", "b", "c"],
        }),
        encoding="utf-8",
    )
    (run_dir / "forecast_report.pdf").write_bytes(b"%PDF-1.4\nplaceholder\n")

    result = subprocess.run(
        [
            sys.executable,
            "scripts/audit_china_harness_run.py",
            "--workspace",
            str(run_dir),
            "--json",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )
    payload = json.loads(result.stdout)

    assert payload["status"] == "fail"
    assert "non_human_readable_evidence_reviews" in payload["failed"]
    assert "long_workspace_paths_in_evidence_reviews" in payload["failed"]
    assert "missing_video_candidate_set_entry" in payload["failed"]


def test_workspace_records_tool_results_and_source_cards(tmp_path) -> None:
    workspace = ChinaForecastWorkspace.create(_context(), root_dir=tmp_path)
    registry = default_china_tool_registry(provider=MockSearchProvider())

    result = registry.run(
        "search_web",
        query="Taiwan invasion 2026 official assessment",
        source_category="foreign_crosscheck",
        max_results=2,
        reliability_prior=0.4,
    )
    paths = workspace.record_tool_result(result)
    workspace.append_trajectory(
        TrajectoryStep(
            loop_index=1,
            phase="tool",
            summary="Ran a foreign crosscheck search.",
            action="search_web",
            observation=f"results={len(result.results)}",
            analysis="Result saved as source card.",
            next_decision="Review source card.",
            tool_name=result.tool_name,
            tool_action_id=result.action_id,
        )
    )

    assert result.status == "ok"
    assert len(paths) == 3
    assert len(list((workspace.paths.search_actions_dir).glob("*.json"))) == 1
    assert len(list((workspace.paths.search_actions_dir).glob("*.md"))) == 1
    source_cards = list((workspace.paths.sources_dir / "foreign_crosscheck").glob("*.md"))
    assert len(source_cards) == 1
    assert "Taiwan invasion" in source_cards[0].read_text(encoding="utf-8")

    trajectory = (workspace.paths.run_dir / "trajectory.md").read_text(encoding="utf-8")
    assert "Ran a foreign crosscheck search" in trajectory
    jsonl = (workspace.paths.run_dir / "trajectory.jsonl").read_text(encoding="utf-8")
    assert result.action_id in jsonl


def test_china_harness_tool_persists_search_action(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = ChinaForecastWorkspace.create(_context(), root_dir=tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/china_harness_tool.py",
            "--workspace",
            str(workspace.paths.run_dir),
            "--mock",
            "search_web",
            "--query",
            "Taiwan invasion 2026 official assessment",
            "--source-category",
            "foreign_crosscheck",
            "--max-results",
            "1",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "status=ok" in result.stdout
    assert len(list(workspace.paths.search_actions_dir.glob("*.json"))) == 1
    assert len(list((workspace.paths.sources_dir / "foreign_crosscheck").glob("*.md"))) == 1
    trajectory = (workspace.paths.run_dir / "trajectory.md").read_text(encoding="utf-8")
    assert "本地工具调用：search_web" in trajectory
    claims = (workspace.paths.run_dir / "claims.md").read_text(encoding="utf-8")
    assert "模拟结果" in claims


def test_china_harness_tool_records_agent_review(tmp_path) -> None:
    repo_root = Path(__file__).resolve().parents[1]
    workspace = ChinaForecastWorkspace.create(_context(), root_dir=tmp_path)

    result = subprocess.run(
        [
            sys.executable,
            "scripts/china_harness_tool.py",
            "--workspace",
            str(workspace.paths.run_dir),
            "agent_review",
            "--evidence-path",
            "sources/official/001_mock_result.md",
            "--agent-output",
            "该来源与姿态相关，但不能直接证明入侵意图。",
            "--observation",
            "该 artifact 提到军事姿态和台湾相关表述。",
            "--assessment",
            "弱证据；不直接改变预测。",
            "--information-gap",
            "还需要关于能力和近期预警信号的独立专业评估。",
            "--next-search-decision",
            "后置搜索 foreign_crosscheck，查看 2026 入侵风险评估。",
            "--stop-or-continue",
            "继续，因为当前证据不能决定 resolution。",
            "--confidence-note",
            "置信度仍然较低。",
        ],
        cwd=repo_root,
        check=True,
        capture_output=True,
        text=True,
    )

    assert "tool=agent_review" in result.stdout
    review_files = list(workspace.paths.agent_reviews_dir.glob("*.md"))
    assert len(review_files) == 1
    review = review_files[0].read_text(encoding="utf-8")
    assert "下一步搜索决策" in review
    assert "foreign_crosscheck" in review
    trajectory = (workspace.paths.run_dir / "trajectory.md").read_text(encoding="utf-8")
    assert "Agent 证据复盘" in trajectory
    assert "弱证据" in trajectory


def test_registry_exposes_resource_processor_placeholders() -> None:
    registry = default_china_tool_registry(provider=MockSearchProvider())
    tools = {tool.name: tool for tool in registry.list_tools()}

    assert tools["search_web"].available is True
    assert tools["search_video_sources"].available is True
    assert tools["search_chinese_platforms"].available is True
    assert tools["process_resource"].available is True
    assert tools["search_video_sources"].metadata["platforms"] == ["bilibili", "youtube"]
    assert tools["search_chinese_platforms"].metadata["platforms"] == [
        "weibo",
        "zhihu",
        "wechat",
        "xueqiu",
        "research_reports",
        "newswire",
    ]
    assert "youtube-render-pdf" in tools["process_resource"].metadata["supported_skills"]
    assert "bilibili-render-pdf" in tools["process_resource"].metadata["supported_skills"]
    assert tools["process_resource"].metadata["subagent_model"] == "gpt-5.4-mini"
    assert tools["process_resource"].metadata["spawn_agent_tool"] == "multi_agent_v1.spawn_agent"
    assert (
        tools["process_resource"].metadata["skill_paths"]["youtube-render-pdf"]
        == "/home/hjy/.codex/skills/youtube-render-pdf/SKILL.md"
    )
    assert "subagent_spawn_prompt.md" in tools["process_resource"].metadata["render_contract"]


def test_youtube_live_chat_is_not_counted_as_transcript() -> None:
    assert _subtitle_summary({"live_chat": [{"ext": "json"}]}) == []
    assert _subtitle_summary({
        "live_chat": [{"ext": "json"}],
        "zh-Hans": [{"ext": "vtt"}],
    }) == [{"language": "zh-Hans", "formats": ["vtt"], "track_count": 1}]


def test_search_web_filters_polymarket_self_reference() -> None:
    provider = MockSearchProvider(results_by_query={
        "taiwan": [
            SearchResult(
                query="taiwan",
                title="Polymarket page",
                summary="Market page should not be factual evidence.",
                url="https://polymarket.com/event/will-china-invade-taiwan-before-2027",
                source="polymarket.com",
            ),
            SearchResult(
                query="taiwan",
                title="PolyPredict page",
                summary="Prediction-market derivative page should not be factual evidence.",
                url="https://polypredict.ai/polymarket/taiwan",
                source="polypredict.ai",
            ),
            SearchResult(
                query="taiwan",
                title="External analysis",
                summary="Taiwan invasion external evidence.",
                url="https://example.com/taiwan",
                source="example.com",
            ),
        ]
    })
    registry = default_china_tool_registry(provider=provider)

    result = registry.run("search_web", query="taiwan", source_category="foreign_crosscheck")

    assert len(result.results) == 1
    assert result.results[0].source == "example.com"
    assert result.metadata["filtered_self_reference_count"] == 2


def test_search_web_filters_social_video_from_foreign_crosscheck() -> None:
    provider = MockSearchProvider(results_by_query={
        "xi": [
            SearchResult(
                query="xi",
                title="Xi YouTube speculation",
                summary="Xi leadership video result.",
                url="https://www.youtube.com/watch?v=test",
                source="youtube.com",
            ),
            SearchResult(
                query="xi",
                title="Xi newswire analysis",
                summary=(
                    "Xi leadership external evidence with enough detail to pass "
                    "quality filter."
                ),
                url="https://example.com/xi",
                source="example.com",
            ),
        ]
    })
    registry = default_china_tool_registry(provider=provider)

    foreign = registry.run("search_web", query="xi", source_category="foreign_crosscheck")
    social = registry.run("search_web", query="xi", source_category="expert_social")

    assert [item.source for item in foreign.results] == ["example.com"]
    assert foreign.metadata["filtered_category_mismatch_count"] == 1
    assert [item.source for item in social.results] == ["youtube.com"]


def test_search_web_filters_foreign_media_from_chinese_buckets() -> None:
    provider = MockSearchProvider(results_by_query={
        "王楚钦 比赛 中文媒体 深度报道": [
            SearchResult(
                query="王楚钦 比赛 中文媒体 深度报道",
                title="王楚钦比赛深度报道",
                summary="中文媒体对王楚钦比赛和赛后讨论进行了充分报道和背景分析。",
                url="https://www.thepaper.cn/newsDetail_forward_123",
                source="thepaper.cn",
                relevance_score=0.7,
            ),
            SearchResult(
                query="王楚钦 比赛 中文媒体 深度报道",
                title="VOA Chinese sports commentary",
                summary="王楚钦比赛相关外部中文评论。",
                url="https://www.voachinese.com/a/sports-commentary/123.html",
                source="voachinese.com",
                relevance_score=0.9,
            ),
            SearchResult(
                query="王楚钦 比赛 中文媒体 深度报道",
                title="DW 中文报道",
                summary="王楚钦比赛相关外媒中文报道。",
                url="https://www.dw.com/zh/test/a-123",
                source="dw.com",
                relevance_score=0.8,
            ),
        ]
    })
    registry = default_china_tool_registry(provider=provider)

    result = registry.run(
        "search_web",
        query="王楚钦 比赛 中文媒体 深度报道",
        source_category="professional_media",
    )

    assert [item.source for item in result.results] == ["thepaper.cn"]
    assert result.metadata["filtered_category_mismatch_count"] == 2
    assert {item["source"] for item in result.metadata["rejected_category"]} == {
        "voachinese.com",
        "dw.com",
    }


def test_search_web_official_uses_gov_cn_allowlist() -> None:
    provider = MockSearchProvider(results_by_query={
        "site:gov.cn 体育 总局 通知": [
            SearchResult(
                query="site:gov.cn 体育 总局 通知",
                title="体育总局通知",
                summary="国家体育总局发布比赛管理通知，涉及国内体育赛事安排。",
                url="https://www.sport.gov.cn/n315/n20001395/c123/content.html",
                source="sport.gov.cn",
                relevance_score=0.7,
            ),
            SearchResult(
                query="site:gov.cn 体育 总局 通知",
                title="台湾主管机关新闻稿",
                summary="台湾主管机关发布体育新闻稿。",
                url="https://www.sa.gov.tw/news/123",
                source="sa.gov.tw",
                relevance_score=0.8,
            ),
            SearchResult(
                query="site:gov.cn 体育 总局 通知",
                title="普通网页",
                summary="普通网页转载体育总局通知。",
                url="https://example.com/sport-gov-notice",
                source="example.com",
                relevance_score=0.8,
            ),
        ]
    })
    registry = default_china_tool_registry(provider=provider)

    result = registry.run(
        "search_web",
        query="site:gov.cn 体育 总局 通知",
        source_category="official",
    )

    assert [item.source for item in result.results] == ["sport.gov.cn"]
    assert result.metadata["filtered_category_mismatch_count"] == 2


def test_search_video_sources_keeps_only_bilibili_and_youtube() -> None:
    provider = MockSearchProvider(results_by_query={
        "site:bilibili.com 王楚钦 夺冠 分析 B站 中文": [
            SearchResult(
                query="site:bilibili.com 王楚钦 夺冠 分析 B站 中文",
                title="王楚钦夺冠复盘 B站视频",
                summary="B站中文视频对王楚钦夺冠进行复盘分析，讨论比赛走势和舆论反应。",
                url="https://www.bilibili.com/video/BV1test",
                source="bilibili.com",
                relevance_score=0.8,
            ),
            SearchResult(
                query="site:bilibili.com 王楚钦 夺冠 分析 B站 中文",
                title="王楚钦文字报道",
                summary="中文媒体文字报道王楚钦夺冠。",
                url="https://www.thepaper.cn/newsDetail_forward_456",
                source="thepaper.cn",
                relevance_score=0.9,
            ),
        ],
        "site:youtube.com 王楚钦 夺冠 分析 YouTube 中文": [
            SearchResult(
                query="site:youtube.com 王楚钦 夺冠 分析 YouTube 中文",
                title="王楚钦夺冠中文讲评 YouTube",
                summary="YouTube 中文频道对王楚钦夺冠进行讲评分析。",
                url="https://www.youtube.com/watch?v=test",
                source="youtube.com",
                relevance_score=0.7,
            ),
        ],
    })
    registry = default_china_tool_registry(provider=provider)

    result = registry.run(
        "search_video_sources",
        query="王楚钦 夺冠 分析",
        platforms="bilibili,youtube",
        max_results=5,
    )

    assert [item.source for item in result.results] == ["bilibili.com", "youtube.com"]
    assert result.metadata["filtered_non_video_count"] == 1
    assert result.metadata["issued_queries"] == [
        "site:bilibili.com 王楚钦 夺冠 分析 B站 中文",
        "site:youtube.com 王楚钦 夺冠 分析 YouTube 中文",
    ]


def test_video_source_url_requires_actual_video_page() -> None:
    assert is_video_source_url("https://www.bilibili.com/video/BV1test")
    assert is_video_source_url("https://b23.tv/abc123")
    assert is_video_source_url("https://www.youtube.com/watch?v=test")
    assert is_video_source_url("https://www.youtube.com/shorts/test")
    assert not is_video_source_url("https://search.bilibili.com/all?keyword=台海")
    assert not is_video_source_url("https://m.bilibili.com/search?keyword=台海")
    assert not is_video_source_url("https://www.youtube.com/results?search_query=台海")


def test_search_video_sources_writes_candidate_set_visit(tmp_path) -> None:
    provider = MockSearchProvider(results_by_query={
        "site:bilibili.com 王楚钦 夺冠 分析 B站 中文": [
            SearchResult(
                query="site:bilibili.com 王楚钦 夺冠 分析 B站 中文",
                title="高播放王楚钦复盘",
                summary="B站中文视频对王楚钦夺冠进行复盘分析。",
                url="https://www.bilibili.com/video/BV1high",
                source="bilibili.com",
                relevance_score=0.8,
                raw_metadata={
                    "platform": "bilibili",
                    "author": "乒乓频道",
                    "play_count": 120000,
                    "comment_count": 300,
                    "search_order": "click",
                },
            ),
            SearchResult(
                query="site:bilibili.com 王楚钦 夺冠 分析 B站 中文",
                title="B站搜索页噪声",
                summary="这不是视频页，不能作为视频候选。",
                url="https://search.bilibili.com/all?keyword=王楚钦",
                source="search.bilibili.com",
                relevance_score=0.9,
            ),
        ],
    })
    context = AgentRunContext(
        event_title="王楚钦比赛事件",
        market_question="王楚钦是否会夺冠？",
        condition_id="0xsports",
        resolution_text="Resolves YES if Wang Chuqin wins the event.",
        p_m=0.5,
    )
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=provider)

    result = registry.run(
        "search_video_sources",
        context=context,
        workspace=workspace,
        query="王楚钦 夺冠 分析",
        platforms="bilibili",
        max_results=5,
    )

    assert [item.title for item in result.results] == ["高播放王楚钦复盘"]
    assert result.metadata["filtered_non_video_count"] == 1
    visit_files = list((workspace.paths.run_dir / "source_visits").glob("*.md"))
    assert len(visit_files) == 1
    visit = visit_files[0].read_text(encoding="utf-8")
    assert "Candidate Set Before Final Selection" in visit
    assert "高播放王楚钦复盘" in visit
    assert "120000" in visit
    assert "selected" in visit


def test_search_chinese_platforms_writes_candidate_set_visit(tmp_path) -> None:
    provider = MockSearchProvider(results_by_query={
        "site:weibo.com OR site:m.weibo.cn 王楚钦 商业价值 微博": [
            SearchResult(
                query="site:weibo.com OR site:m.weibo.cn 王楚钦 商业价值 微博",
                title="微博体育博主讨论王楚钦商业价值",
                summary="微博讨论王楚钦商业价值、赛事表现和粉丝舆论。",
                url="https://weibo.com/123/abc",
                source="weibo.com",
                relevance_score=0.8,
            ),
        ],
        "site:mp.weixin.qq.com 王楚钦 商业价值 公众号": [
            SearchResult(
                query="site:mp.weixin.qq.com 王楚钦 商业价值 公众号",
                title="公众号：王楚钦商业价值分析",
                summary="公众号文章分析王楚钦商业价值和赞助潜力。",
                url="https://mp.weixin.qq.com/s/test",
                source="mp.weixin.qq.com",
                relevance_score=0.75,
            ),
        ],
    })
    context = AgentRunContext(
        event_title="王楚钦商业事件",
        market_question="王楚钦是否会获得新代言？",
        condition_id="0xsocial",
        resolution_text="Resolves YES if Wang Chuqin signs a new endorsement.",
        p_m=0.5,
    )
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=provider)

    result = registry.run(
        "search_chinese_platforms",
        context=context,
        workspace=workspace,
        query="王楚钦 商业价值",
        platforms="weibo,wechat",
        max_results=3,
    )

    assert result.status == "ok"
    assert [item.source for item in result.results] == ["weibo.com", "mp.weixin.qq.com"]
    assert {item.source_type for item in result.results} == {"expert_social"}
    assert result.metadata["platforms"] == ["weibo", "wechat"]
    visit_files = list((workspace.paths.run_dir / "source_visits").glob("*.md"))
    assert len(visit_files) == 1
    visit = visit_files[0].read_text(encoding="utf-8")
    assert "中文平台搜索候选集" in visit
    assert "weibo" in visit
    assert "wechat" in visit
    assert "王楚钦商业价值分析" in visit
    assert "Candidate Set Before Final Selection" in visit
    assert "Platform Tier Rule" in visit
    assert "engagement" in visit
    assert "author_quality" in visit
    assert "`T1`: 知乎、微博" in visit


def test_search_chinese_platforms_uses_browser_before_domain_fallback(
    tmp_path,
    monkeypatch,
) -> None:
    import beatodds.agents.tool_registry as tool_registry_module

    class Provider:
        name = "test_provider"

        def __init__(self):
            self.queries = []

        def search(self, query, max_results=5):
            self.queries.append(query)
            return []

    def fake_internal(platform, query, max_results=6):
        return [], "zhihu_api_blocked:400"

    def fake_browser(platform, query, max_results=6):
        return [
            SearchResult(
                query=query,
                title="知乎：当前国乒国家队谁实力最厉害",
                summary="知乎站内搜索结果，讨论樊振东、王楚钦、孙颖莎。",
                url="https://www.zhihu.com/question/123",
                source="zhihu.com",
                relevance_score=0.9,
                provider="browser_platform_search",
                source_type="expert_social",
                raw_metadata={
                    "platform": "zhihu",
                    "access_method": "browser_platform_search",
                },
            )
        ], "ok"

    monkeypatch.setattr(tool_registry_module, "search_platform_internal", fake_internal)
    monkeypatch.setattr(tool_registry_module, "search_platform_browser", fake_browser)
    context = AgentRunContext(
        event_title="当前国乒国家队谁实力最厉害",
        market_question="当前国乒国家队谁实力最厉害？",
        condition_id="0xtabletennis",
        resolution_text="Resolves to the strongest current Chinese national table tennis player.",
        p_m=0.5,
    )
    provider = Provider()
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=provider)

    result = registry.run(
        "search_chinese_platforms",
        context=context,
        workspace=workspace,
        query="当前国乒国家队谁实力最厉害",
        platforms="zhihu",
        max_results=3,
    )

    assert result.status == "ok"
    assert result.results[0].provider == "browser_platform_search"
    assert provider.queries == []
    status = result.metadata["platform_statuses"][0]
    assert status["internal_status"] == "zhihu_api_blocked:400"
    assert status["browser_status"] == "ok"
    assert status["browser_raw_count"] == 1
    assert status["fallback_raw_count"] == 0
    visit = next((workspace.paths.run_dir / "source_visits").glob("*.md")).read_text(
        encoding="utf-8",
    )
    assert "browser_status" in visit
    assert "browser_platform_search" in visit
    assert "知乎：当前国乒国家队谁实力最厉害" in visit


def test_search_chinese_platforms_marks_domain_fallback_after_browser_block(
    tmp_path,
    monkeypatch,
) -> None:
    import beatodds.agents.tool_registry as tool_registry_module

    class Provider:
        name = "test_provider"

        def __init__(self):
            self.queries = []

        def search(self, query, max_results=5):
            self.queries.append(query)
            return [
                SearchResult(
                    query=query.query,
                    title="外部发现：知乎 当前国乒国家队谁实力最厉害 讨论",
                    summary="外部搜索引擎发现的知乎候选，讨论当前国乒国家队谁实力最厉害。",
                    url="https://www.zhihu.com/question/456",
                    source="zhihu.com",
                    relevance_score=0.7,
                    provider="test_provider",
                    source_type=query.source_type,
                )
            ]

    def fake_internal(platform, query, max_results=6):
        return [], "zhihu_api_blocked:400"

    def fake_browser(platform, query, max_results=6):
        return [], "zhihu_browser_blocked:zhihu_40362"

    monkeypatch.setattr(tool_registry_module, "search_platform_internal", fake_internal)
    monkeypatch.setattr(tool_registry_module, "search_platform_browser", fake_browser)
    context = AgentRunContext(
        event_title="当前国乒国家队谁实力最厉害",
        market_question="当前国乒国家队谁实力最厉害？",
        condition_id="0xtabletennis",
        resolution_text="Resolves to the strongest current Chinese national table tennis player.",
        p_m=0.5,
    )
    provider = Provider()
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=provider)

    result = registry.run(
        "search_chinese_platforms",
        context=context,
        workspace=workspace,
        query="当前国乒国家队谁实力最厉害",
        platforms="zhihu",
        max_results=3,
    )

    assert result.status == "ok"
    assert result.results[0].raw_metadata["access_method"] == "tavily_domain_fallback"
    assert provider.queries
    status = result.metadata["platform_statuses"][0]
    assert status["browser_status"] == "zhihu_browser_blocked:zhihu_40362"
    assert status["fallback_raw_count"] == 1
    visit = next((workspace.paths.run_dir / "source_visits").glob("*.md")).read_text(
        encoding="utf-8",
    )
    assert "zhihu_browser_blocked:zhihu_40362" in visit
    assert "tavily_domain_fallback" in visit


def test_search_web_filters_boilerplate_and_ranks_quality() -> None:
    provider = MockSearchProvider(results_by_query={
        "site:gov.cn 习近平 任期 2027": [
            SearchResult(
                query="site:gov.cn 习近平 任期 2027",
                title="中华人民共和国司法部",
                summary=(
                    "网站首页 机构设置 法院资讯 办事服务 公众互动 关于我们 "
                    "新时代廉洁文化建设三年行动计划（2025—2027年）制定出台。"
                ),
                url="https://www.moj.gov.cn/noisy.html",
                source="moj.gov.cn",
                relevance_score=0.6,
            ),
            SearchResult(
                query="site:gov.cn 习近平 任期 2027",
                title="习近平在全军高级干部培训班开班式上发表重要讲话",
                summary=(
                    "中共中央总书记、国家主席、中央军委主席习近平出席开班式并"
                    "发表重要讲话，强调深化政治整训。"
                ),
                url="https://www.mod.gov.cn/relevant.html",
                source="mod.gov.cn",
                relevance_score=0.5,
            ),
        ]
    })
    registry = default_china_tool_registry(provider=provider)

    result = registry.run(
        "search_web",
        query="site:gov.cn 习近平 任期 2027",
        source_category="official",
    )

    assert len(result.results) == 1
    assert result.results[0].source == "mod.gov.cn"
    assert result.metadata["filtered_quality_count"] == 1
    quality = result.results[0].raw_metadata["search_quality"]
    assert quality["score"] > 0.2
    assert any(reason.startswith("query_overlap") for reason in quality["reasons"])


def test_search_quality_uses_market_context_for_broad_official_query() -> None:
    context = AgentRunContext(
        event_title="Xi Jinping leadership before 2027",
        market_question="Xi Jinping out before 2027?",
        condition_id="0xxi",
        resolution_text=(
            "Resolves YES if Xi Jinping is no longer General Secretary of the Chinese "
            "Communist Party, President of China, or Chairman of the Central Military "
            "Commission before Jan 1, 2027."
        ),
        p_m=0.0695,
    )
    provider = MockSearchProvider(results_by_query={
        "site:gov.cn 中国 中央 政治局 人事 任免": [
            SearchResult(
                query="site:gov.cn 中国 中央 政治局 人事 任免",
                title="邓小平",
                summary="1956年在八届一中全会上，当选为中央政治局常务委员。",
                url="https://www.cppcc.gov.cn/deng.html",
                source="cppcc.gov.cn",
                relevance_score=0.9,
            ),
            SearchResult(
                query="site:gov.cn 中国 中央 政治局 人事 任免",
                title="习近平主席重要外事活动",
                summary=(
                    "习近平同外方领导人会谈。中共中央总书记、国家主席、"
                    "中央军委主席习近平继续履职。"
                ),
                url="https://www.mfa.gov.cn/xi.html",
                source="mfa.gov.cn",
                relevance_score=0.4,
            ),
        ]
    })
    registry = default_china_tool_registry(provider=provider)

    result = registry.run(
        "search_web",
        query="site:gov.cn 中国 中央 政治局 人事 任免",
        source_category="official",
        context=context,
    )

    assert [item.source for item in result.results] == ["mfa.gov.cn"]
    assert result.metadata["filtered_quality_count"] == 1
    assert result.metadata["rejected_quality"][0]["source"] == "cppcc.gov.cn"
    assert "context_entity_missing" in result.metadata["rejected_quality"][0]["reasons"]


def test_access_tools_write_payload_artifacts(tmp_path) -> None:
    context = _context()
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=MockSearchProvider())

    registry_result = registry.run("export_source_registry", context=context, workspace=workspace)
    query_result = registry.run(
        "generate_china_queries",
        context=context,
        workspace=workspace,
        query=context.market_question,
    )
    market_result = registry.run("read_polymarket_context", context=context, workspace=workspace)
    baseline_result = registry.run("model_baseline_forecast", context=context, workspace=workspace)
    resource_result = registry.run(
        "process_resource",
        context=context,
        workspace=workspace,
        query="https://www.youtube.com/watch?v=test",
        url="https://www.youtube.com/watch?v=test",
    )

    assert registry_result.payload["source_count"] > 0
    assert "recommended_searches" in query_result.payload
    recommended_tools = {
        item["tool_name"] for item in query_result.payload["recommended_searches"]
    }
    assert "search_video_sources" in recommended_tools
    assert "search_chinese_platforms" in recommended_tools
    assert market_result.payload["p_m"] == context.p_m
    assert baseline_result.payload["p_f"] == context.p_m
    assert resource_result.payload["resource_type"] == "youtube_video"
    assert (workspace.paths.run_dir / "source_registry.json").exists()
    assert (workspace.paths.run_dir / "generated_queries.json").exists()
    assert (workspace.paths.run_dir / "polymarket_context.json").exists()
    assert (workspace.paths.run_dir / "model_baseline.json").exists()


def test_process_resource_writes_video_render_contract(tmp_path, monkeypatch) -> None:
    def fake_inspect(url, max_comments=10, fetch_youtube_comments=False):
        return {
            "platform": "youtube",
            "url": url,
            "title": "测试视频",
            "author": "测试频道",
            "content_access": {
                "metadata": True,
                "comments": False,
                "transcript": False,
                "video_body": False,
                "requires_asr": True,
            },
            "assessment": {
                "decision": "needs_video_render",
                "signals": ["metadata_available"],
                "risks": ["no_transcript"],
            },
            "stats": {},
            "comments": [],
            "subtitles": [],
        }

    monkeypatch.setattr("beatodds.agents.access_tools.inspect_video_resource", fake_inspect)
    context = _context()
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=MockSearchProvider())

    result = registry.run(
        "process_resource",
        context=context,
        workspace=workspace,
        query="https://www.youtube.com/watch?v=test",
        url="https://www.youtube.com/watch?v=test",
        render_timeout_seconds=120,
        orientation="forecast_evidence",
    )

    resource_dirs = list(workspace.paths.artifacts_dir.glob("resources/*"))
    assert len(resource_dirs) == 1
    render_request = json.loads((resource_dirs[0] / "render_request.json").read_text())
    prompt = (resource_dirs[0] / "video_report_prompt.md").read_text(encoding="utf-8")
    subagent_prompt = (
        resource_dirs[0] / "subagent_spawn_prompt.md"
    ).read_text(encoding="utf-8")
    source_card = (resource_dirs[0] / "source_card.md").read_text(encoding="utf-8")

    assert result.payload["render"]["skill_name"] == "youtube-render-pdf"
    assert result.payload["render"]["render_status"] == "required"
    assert "artifact_index.md" not in result.payload["render"]["missing_outputs"]
    assert render_request["timeout_seconds"] == 120
    assert render_request["subagent_model"] == "gpt-5.4-mini"
    assert render_request["skill_path"] == "/home/hjy/.codex/skills/youtube-render-pdf/SKILL.md"
    assert render_request["spawn_agent_tool"] == "multi_agent_v1.spawn_agent"
    assert render_request["spawn_agent_args"]["items"][0]["type"] == "skill"
    assert render_request["spawn_agent_args"]["items"][0]["name"] == "youtube-render-pdf"
    assert "不得无限等待视频 ASR/render" in render_request["fallback_policy"]
    assert "asr.lock.json" in render_request["lock_paths"]["asr_lock"]
    assert "禁止启动第二个 ASR" in render_request["main_agent_takeover_policy"]
    assert "video_render.lock.json" in render_request["lock_policy"]
    assert "Video Render Task" in prompt
    assert "gpt-5.4-mini" in prompt
    assert "video_report.pdf" in prompt
    assert "必须优先使用视频标题" in prompt
    assert "不得只用 BV 号称呼视频" in prompt
    assert "asr.lock.json" in prompt
    assert "不得重复启动下载、ffmpeg 或 Whisper" in prompt
    assert "Subagent Spawn Prompt" in subagent_prompt
    assert "items" in subagent_prompt
    assert "asr.lock.json" in subagent_prompt
    assert "render_status" in source_card
    assert "asr_lock_active" in source_card


def test_process_resource_marks_video_render_complete(tmp_path, monkeypatch) -> None:
    def fake_inspect(url, max_comments=10, fetch_youtube_comments=False):
        return {
            "platform": "bilibili",
            "url": url,
            "title": "测试 B站视频",
            "author": "测试UP",
            "content_access": {
                "metadata": True,
                "comments": True,
                "transcript": True,
                "video_body": True,
                "requires_asr": False,
            },
            "assessment": {
                "decision": "ready_for_resource_report",
                "signals": ["transcript_available"],
                "risks": [],
            },
            "stats": {},
            "comments": [],
            "subtitles": ["zh"],
        }

    monkeypatch.setattr("beatodds.agents.access_tools.inspect_video_resource", fake_inspect)
    context = _context()
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=MockSearchProvider())
    url = "https://www.bilibili.com/video/BV1test"

    registry.run(
        "process_resource",
        context=context,
        workspace=workspace,
        query=url,
        url=url,
    )
    resource_dir = next(workspace.paths.artifacts_dir.glob("resources/*"))
    for name in [
        "video_metadata.json",
        "video_parse_report.md",
        "claims.jsonl",
        "evidence_card.md",
        "video_report.tex",
        "video_report.pdf",
        "artifact_index.md",
    ]:
        (resource_dir / name).write_text("ok", encoding="utf-8")

    result = registry.run(
        "process_resource",
        context=context,
        workspace=workspace,
        query=url,
        url=url,
    )

    assert result.payload["render"]["skill_name"] == "bilibili-render-pdf"
    assert result.payload["render"]["render_status"] == "complete"
    assert result.payload["render"]["missing_outputs"] == []


def test_sync_video_render_artifacts_updates_stale_required_status(
    tmp_path,
    monkeypatch,
) -> None:
    def fake_inspect(url, max_comments=10, fetch_youtube_comments=False):
        return {
            "platform": "youtube",
            "url": url,
            "title": "测试长视频",
            "author": "测试频道",
            "content_access": {
                "metadata": True,
                "comments": False,
                "transcript": False,
                "video_body": False,
                "requires_asr": True,
            },
            "assessment": {
                "decision": "needs_video_render",
                "signals": ["metadata_available"],
                "risks": ["no_transcript"],
            },
            "stats": {},
            "comments": [],
            "subtitles": [],
        }

    monkeypatch.setattr("beatodds.agents.access_tools.inspect_video_resource", fake_inspect)
    context = _context()
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=MockSearchProvider())
    url = "https://www.youtube.com/watch?v=test"

    registry.run("process_resource", context=context, workspace=workspace, query=url, url=url)
    resource_dir = next(workspace.paths.artifacts_dir.glob("resources/*"))
    resource_json = resource_dir / "resource_processor.json"
    initial_payload = json.loads(resource_json.read_text(encoding="utf-8"))
    assert initial_payload["render"]["render_status"] == "required"
    for name in [
        "video_metadata.json",
        "video_parse_report.md",
        "claims.jsonl",
        "evidence_card.md",
        "video_report.tex",
        "video_report.pdf",
        "audio.srt",
    ]:
        (resource_dir / name).write_text("ok", encoding="utf-8")
    (resource_dir / "artifact_index.md").write_text("custom manual index", encoding="utf-8")

    synced = sync_video_render_artifacts(resource_dir)
    saved = json.loads(resource_json.read_text(encoding="utf-8"))
    source_card = (resource_dir / "source_card.md").read_text(encoding="utf-8")

    assert synced["render"]["render_status"] == "complete"
    assert saved["render"]["render_status"] == "complete"
    assert saved["content_access"]["video_body"] is True
    assert saved["content_access"]["transcript"] is True
    assert saved["content_access"]["requires_asr"] is False
    assert saved["content_access"]["video_body_status"] == "complete_report"
    assert "render_status: `complete`" in source_card
    assert (resource_dir / "artifact_index.md").read_text(encoding="utf-8") == (
        "custom manual index"
    )


def test_sync_video_render_artifacts_detects_active_asr_lock(
    tmp_path,
    monkeypatch,
) -> None:
    def fake_inspect(url, max_comments=10, fetch_youtube_comments=False):
        return {
            "platform": "bilibili",
            "url": url,
            "title": "测试 B站长视频",
            "author": "测试UP",
            "content_access": {
                "metadata": True,
                "comments": True,
                "transcript": False,
                "video_body": False,
                "requires_asr": True,
            },
            "assessment": {
                "decision": "needs_video_render",
                "signals": ["metadata_available"],
                "risks": ["no_transcript"],
            },
            "stats": {},
            "comments": [],
            "subtitles": [],
        }

    monkeypatch.setattr("beatodds.agents.access_tools.inspect_video_resource", fake_inspect)
    context = _context()
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=MockSearchProvider())
    url = "https://www.bilibili.com/video/BV1locktest"

    registry.run("process_resource", context=context, workspace=workspace, query=url, url=url)
    resource_dir = next(workspace.paths.artifacts_dir.glob("resources/*"))
    (resource_dir / "asr.lock.json").write_text(
        json.dumps({
            "status": "running",
            "pid": os.getpid(),
            "started_at": "2026-06-13T00:00:00Z",
            "updated_at": "2026-06-13T00:01:00Z",
            "command": "whisper audio.wav --model small --language zh",
        }),
        encoding="utf-8",
    )

    synced = sync_video_render_artifacts(resource_dir)
    saved = json.loads((resource_dir / "resource_processor.json").read_text())

    assert synced["render"]["render_status"] == "in_progress"
    assert saved["processor_status"] == "video_render_in_progress"
    assert saved["render"]["asr_lock_active"] is True
    assert saved["render"]["active_locks"][0]["path"] == "asr.lock.json"
    assert saved["content_access"]["asr_in_progress"] is True
    assert saved["content_access"]["video_body_status"] == "render_in_progress"
    source_card = (resource_dir / "source_card.md").read_text(encoding="utf-8")
    assert "asr_lock_active: `True`" in source_card

    (resource_dir / "asr.lock.json").write_text(
        json.dumps({"status": "complete", "pid": 12345}),
        encoding="utf-8",
    )
    for name in [
        "video_metadata.json",
        "video_parse_report.md",
        "claims.jsonl",
        "evidence_card.md",
        "video_report.tex",
        "video_report.pdf",
        "artifact_index.md",
        "audio.srt",
    ]:
        (resource_dir / name).write_text("ok", encoding="utf-8")

    synced = sync_video_render_artifacts(resource_dir)

    assert synced["render"]["render_status"] == "complete"
    assert synced["render"]["active_locks"] == []
    assert synced["content_access"]["asr_in_progress"] is False


def test_sync_video_render_artifacts_marks_dead_pid_lock_stale(
    tmp_path,
    monkeypatch,
) -> None:
    def fake_inspect(url, max_comments=10, fetch_youtube_comments=False):
        return {
            "platform": "youtube",
            "url": url,
            "title": "死锁测试视频",
            "author": "测试频道",
            "content_access": {
                "metadata": True,
                "comments": False,
                "transcript": False,
                "video_body": False,
                "requires_asr": True,
            },
            "assessment": {
                "decision": "needs_video_render",
                "signals": ["metadata_available"],
                "risks": ["no_transcript"],
            },
            "stats": {},
            "comments": [],
            "subtitles": [],
        }

    monkeypatch.setattr("beatodds.agents.access_tools.inspect_video_resource", fake_inspect)
    context = _context()
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=MockSearchProvider())
    url = "https://www.youtube.com/watch?v=deadpid"

    registry.run("process_resource", context=context, workspace=workspace, query=url, url=url)
    resource_dir = next(workspace.paths.artifacts_dir.glob("resources/*"))
    (resource_dir / "asr.lock.json").write_text(
        json.dumps({
            "status": "running",
            "pid": 99999999,
            "started_at": "2026-06-13T00:00:00Z",
            "updated_at": "2026-06-13T00:01:00Z",
            "command": "whisper stale.mp4 --model small --language zh",
        }),
        encoding="utf-8",
    )
    (resource_dir / "陈破空 [deadpid].srt").write_text(
        "1\n00:00:00,000 --> 00:00:03,000\n习近平 接班 总书记\n",
        encoding="utf-8",
    )

    synced = sync_video_render_artifacts(resource_dir)

    assert synced["render"]["render_status"] == "partial"
    assert synced["render"]["active_locks"] == []
    assert synced["render"]["lock_files"][0]["status"] == "stale"
    assert synced["content_access"]["transcript"] is True
    assert synced["content_access"]["asr_in_progress"] is False


def test_finalize_video_resource_report_creates_core_outputs(
    tmp_path,
    monkeypatch,
) -> None:
    resource_dir = tmp_path / "resource"
    resource_dir.mkdir()
    (resource_dir / "resource_processor.json").write_text(
        json.dumps({
            "platform": "youtube",
            "url": "https://www.youtube.com/watch?v=test",
            "video_id": "test",
            "title": "测试习近平接班视频",
            "author": "测试频道",
            "published_at": "20260613",
            "duration_seconds": 60,
            "stats": {"view_count": 1000, "like_count": 10, "comment_count": 2},
            "content_access": {
                "metadata": True,
                "transcript": False,
                "video_body": False,
                "requires_asr": True,
            },
            "resource_type": "youtube_video",
            "processor_status": "video_render_partial",
            "render": {"render_status": "partial"},
        }),
        encoding="utf-8",
    )
    (resource_dir / "video_metadata_raw.json").write_text(
        json.dumps({
            "id": "test",
            "title": "测试习近平接班视频",
            "channel": "测试频道",
            "upload_date": "20260613",
            "duration": 60,
            "view_count": 1000,
            "webpage_url": "https://www.youtube.com/watch?v=test",
        }),
        encoding="utf-8",
    )
    (resource_dir / "测试习近平接班视频 [test].srt").write_text(
        "\n\n".join([
            "1\n00:00:00,000 --> 00:00:03,000\n习近平 总书记 接班 问题",
            "2\n00:00:03,000 --> 00:00:06,000\n二十一大 前 是否 下台",
        ]),
        encoding="utf-8",
    )

    def fake_compile(resource_dir_arg, tex_path):
        pdf_path = resource_dir_arg / "video_report.pdf"
        pdf_path.write_bytes(b"%PDF-1.4\n%test\n")
        return pdf_path

    monkeypatch.setattr("beatodds.agents.video_reporter._compile_tex", fake_compile)

    result = finalize_video_resource_report(resource_dir)

    assert result["render_status"] == "complete"
    for name in [
        "video_metadata.json",
        "video_parse_report.md",
        "claims.jsonl",
        "evidence_card.md",
        "video_report.tex",
        "video_report.pdf",
        "artifact_index.md",
        "transcript.srt",
    ]:
        assert (resource_dir / name).exists()
    saved = json.loads((resource_dir / "resource_processor.json").read_text())
    assert saved["render"]["render_status"] == "complete"
    assert saved["content_access"]["video_body_status"] == "complete_report"


def test_model_baseline_can_use_fake_llm_client(tmp_path) -> None:
    context = _context()
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(
        provider=MockSearchProvider(),
        enable_model_baseline_llm=True,
        baseline_model="deepseek-chat",
        baseline_client=_FakeLLMClient([
            {
                "p_f": 0.04,
                "confidence": 0.95,
                "calibration_status": "well-calibrated",
                "reasoning": "Evidence lowers the baseline relative to p_m.",
                "evidence_used": ["mock source"],
            }
        ]),
    )

    result = registry.run("model_baseline_forecast", context=context, workspace=workspace)

    assert result.payload["llm_enabled"] is True
    assert result.payload["p_f"] == 0.04
    assert result.payload["confidence"] == 0.75
    assert result.payload["calibration_status"] == "uncalibrated"
    assert "Evidence lowers" in result.payload["reasoning"]
    assert (workspace.paths.run_dir / "model_baseline.json").exists()


def test_multi_outcome_forecast_schema_accepts_probability_distribution() -> None:
    forecast = MultiOutcomeForecast(
        condition_id="multi",
        market_question="Which company wins?",
        top_outcome="Alibaba",
        outcomes=[
            ForecastOutcomeProbability(outcome="Alibaba", p_f=0.33, p_m=0.69),
            ForecastOutcomeProbability(outcome="Z.ai", p_f=0.30, p_m=0.115),
        ],
        model="codex:gpt-5.4",
    )

    assert forecast.p_f_total == 0.63
    assert forecast.outcomes[0].outcome == "Alibaba"


def test_generate_china_queries_does_not_turn_xi_leadership_into_taiwan_case(
    tmp_path,
) -> None:
    context = AgentRunContext(
        event_title="Xi Jinping leadership before 2027",
        market_question="Xi Jinping out before 2027?",
        condition_id="0xxi",
        resolution_text=(
            "Resolves YES if Xi Jinping is no longer General Secretary of the "
            "Chinese Communist Party, President of China, or Chairman of the "
            "Central Military Commission before Jan 1, 2027."
        ),
        p_m=0.0695,
        agent_name="test_agent",
        agent_run_id="test_agent_20260609_000002",
        created_at=datetime(2026, 6, 9, 8, 0, tzinfo=timezone.utc),
    )
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=MockSearchProvider())

    result = registry.run(
        "generate_china_queries",
        context=context,
        workspace=workspace,
        query="Xi Jinping leadership stability 2026 succession risk",
    )
    queries = result.payload["queries"]

    assert any("领导层" in query or "中央 政治局" in query for query in queries)
    assert any("2026" in query and "习近平" in query for query in queries)
    assert not any("台海" in query or "台湾" in query for query in queries)


def test_generate_china_queries_prioritizes_chinese_social_sources(tmp_path) -> None:
    context = _context()
    workspace = ChinaForecastWorkspace.create(context, root_dir=tmp_path)
    registry = default_china_tool_registry(provider=MockSearchProvider())

    result = registry.run(
        "generate_china_queries",
        context=context,
        workspace=workspace,
        query=context.market_question,
    )
    searches = result.payload["recommended_searches"]
    categories = [item["source_category"] for item in searches]
    queries = [item["query"] for item in searches]

    assert categories[:5] == ["expert_social"] * 5
    assert any("B站" in query for query in queries[:8])
    assert any("YouTube" in query for query in queries[:8])
    assert any(item.get("tool_name") == "search_video_sources" for item in searches[:8])
    assert categories.index("official") > categories.index("professional_media")
    assert categories.index("foreign_crosscheck") > categories.index("official")


def test_scripted_controller_runs_full_harness_report(tmp_path) -> None:
    context = _context()
    registry = default_china_tool_registry(provider=MockSearchProvider())
    controller = ChinaAgentLoopController(registry=registry)

    report, workspace = controller.run(context, workspace_root=tmp_path)

    assert report.p_f == context.p_m
    assert report.confidence is not None
    assert (workspace.paths.run_dir / "forecast_report.md").exists()
    assert (workspace.paths.run_dir / "forecast_report.json").exists()
    assert len(list(workspace.paths.search_actions_dir.glob("*.json"))) >= 6
    assert len(list(workspace.paths.sources_dir.glob("*/*.md"))) >= 1
    trajectory = (workspace.paths.run_dir / "trajectory.md").read_text(encoding="utf-8")
    assert "生成最终 forecast report" in trajectory


def test_llm_controller_runs_with_fake_deepseek_client(tmp_path) -> None:
    context = _context()
    registry = default_china_tool_registry(provider=MockSearchProvider(results_by_query={
        "中国 国防部 台湾": [
            SearchResult(
                query="中国 国防部 台湾",
                title="国防部回应台湾相关问题",
                summary="国防部就台湾相关问题和军事活动作出公开回应。",
                url="https://www.mod.gov.cn/gfbw/qwfb/123.html",
                source="mod.gov.cn",
                relevance_score=0.7,
            )
        ]
    }))
    agent = LLMChinaAgent(model="deepseek-chat", client=_FakeLLMClient([
        {
            "phase": "tool",
            "summary": "导出 source registry。",
            "action": "调用 export_source_registry。",
            "analysis": "规划搜索前需要 source map。",
            "next_decision": "生成 queries。",
            "tool_call": {"tool_name": "export_source_registry"},
        },
        {
            "phase": "tool",
            "summary": "生成中国相关 queries。",
            "action": "调用 generate_china_queries。",
            "analysis": "需要中国相关候选 query。",
            "next_decision": "搜索 evidence。",
            "tool_call": {
                "tool_name": "generate_china_queries",
                "query": context.market_question,
            },
        },
        {
            "phase": "tool",
            "summary": "搜索官方 evidence。",
            "action": "调用 search_web。",
            "analysis": "需要一个 official 类型 source card。",
            "next_decision": "如果足够则报告。",
            "tool_call": {
                "tool_name": "search_web",
                "query": "中国 国防部 台湾",
                "source_category": "official",
                "args": {"max_results": 1},
            },
        },
        {
            "phase": "report",
            "summary": "写 forecast。",
            "action": "写报告。",
            "analysis": "模拟 evidence 偏中性；预测接近市场。",
            "next_decision": "停止。",
            "report": {
                "p_f": 0.07,
                "confidence": 0.25,
                "calibration_status": "uncalibrated",
                "report_markdown": "# 预测报告\n\n- p_f: `0.0700`\n",
            },
        },
    ]))
    controller = ChinaAgentLoopController(registry=registry, agent=agent)

    report, workspace = controller.run_llm(context, workspace_root=tmp_path, max_steps=6)

    assert report.p_f == 0.07
    assert report.p_m_delta == 0.007500000000000007
    assert report.model == "deepseek-chat"
    assert (workspace.paths.run_dir / "forecast_report.md").exists()
    assert len(list(workspace.paths.sources_dir.glob("*/*.md"))) == 1
    trajectory = (workspace.paths.run_dir / "trajectory.md").read_text(encoding="utf-8")
    assert "导出 source registry" in trajectory
    assert "写 forecast" in trajectory


def test_llm_controller_passes_process_resource_url_field(tmp_path) -> None:
    context = _context()
    registry = default_china_tool_registry(provider=MockSearchProvider())
    agent = LLMChinaAgent(model="deepseek-chat", client=_FakeLLMClient([
        {
            "phase": "tool",
            "summary": "处理一个资源。",
            "action": "处理 URL。",
            "analysis": "需要保留 URL tool-call 参数。",
            "next_decision": "报告。",
            "tool_call": {
                "tool_name": "process_resource",
                "url": "https://www.youtube.com/watch?v=test",
                "source_category": "expert_social",
            },
        },
        {
            "phase": "report",
            "summary": "写 forecast。",
            "action": "写报告。",
            "analysis": "resource processor 接受了 URL。",
            "next_decision": "停止。",
            "report": {
                "p_f": 0.07,
                "confidence": 0.25,
                "calibration_status": "uncalibrated",
                "report_markdown": "# 预测报告\n\n- p_f: `0.0700`\n",
            },
        },
    ]))
    controller = ChinaAgentLoopController(registry=registry, agent=agent)

    _, workspace = controller.run_llm(context, workspace_root=tmp_path, max_steps=4)

    resource_json = list(workspace.paths.artifacts_dir.glob("resources/*/resource_processor.json"))
    assert len(resource_json) == 1
    assert "youtube" in resource_json[0].read_text(encoding="utf-8")


def test_llm_controller_uses_url_query_for_process_resource(tmp_path) -> None:
    context = _context()
    registry = default_china_tool_registry(provider=MockSearchProvider())
    agent = LLMChinaAgent(model="deepseek-chat", client=_FakeLLMClient([
        {
            "phase": "tool",
            "summary": "处理一个资源。",
            "action": "处理 query 中的 URL。",
            "analysis": "需要支持 process_resource 把 URL 放在 query 字段。",
            "next_decision": "报告。",
            "tool_call": {
                "tool_name": "process_resource",
                "query": "https://example.com/report.pdf",
                "source_category": "market_professional",
            },
        },
        {
            "phase": "report",
            "summary": "写 forecast。",
            "action": "写报告。",
            "analysis": "resource processor 接受了 query 中的 URL。",
            "next_decision": "停止。",
            "report": {
                "p_f": 0.07,
                "confidence": 0.25,
                "calibration_status": "uncalibrated",
                "report_markdown": "# 预测报告\n\n- p_f: `0.0700`\n",
            },
        },
    ]))
    controller = ChinaAgentLoopController(registry=registry, agent=agent)

    _, workspace = controller.run_llm(context, workspace_root=tmp_path, max_steps=4)

    resource_json = list(workspace.paths.artifacts_dir.glob("resources/*/resource_processor.json"))
    assert len(resource_json) == 1
    assert "report.pdf" in resource_json[0].read_text(encoding="utf-8")


class _FakeLLMClient:
    def __init__(self, payloads):
        self.chat = _FakeChat(payloads)


class _FakeChat:
    def __init__(self, payloads):
        self.completions = _FakeCompletions(payloads)


class _FakeCompletions:
    def __init__(self, payloads):
        self.payloads = list(payloads)

    def create(self, **kwargs):
        import json
        from types import SimpleNamespace

        payload = self.payloads.pop(0)
        message = SimpleNamespace(content=json.dumps(payload))
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])
