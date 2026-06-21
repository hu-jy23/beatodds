"""Markdown-first local-agent harness helpers for China forecast runs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from beatodds.agents.models import AgentRunContext, AgentToolSpec
from beatodds.agents.workspace import ChinaForecastWorkspace

LOCAL_MAIN_AGENT = "codex:gpt-5.4-mini"
VIDEO_RENDER_SUBAGENT = "gpt-5.4-mini"
BILIBILI_RENDER_SKILL_PATH = "/home/hjy/.codex/skills/bilibili-render-pdf/SKILL.md"
YOUTUBE_RENDER_SKILL_PATH = "/home/hjy/.codex/skills/youtube-render-pdf/SKILL.md"


def write_local_agent_bootstrap(
    workspace: ChinaForecastWorkspace,
    tools: list[AgentToolSpec],
    harness_doc_path: Path | None = None,
) -> tuple[Path, Path, Path]:
    """Write task.md, tool_manifest.md/json, and codex_prompt.md for local runs."""
    task_path = workspace.paths.run_dir / "task.md"
    manifest_md_path = workspace.paths.run_dir / "tool_manifest.md"
    manifest_json_path = workspace.paths.run_dir / "tool_manifest.json"
    prompt_path = workspace.paths.run_dir / "codex_prompt.md"

    manifest = build_tool_manifest(tools=tools, workspace=workspace)
    manifest_json_path.write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    manifest_md_path.write_text(render_tool_manifest_md(manifest), encoding="utf-8")
    task_path.write_text(
        render_task_md(
            context=workspace.context,
            workspace=workspace,
            manifest_path=manifest_md_path,
            harness_doc_path=harness_doc_path,
        ),
        encoding="utf-8",
    )
    prompt_path.write_text(render_codex_prompt(workspace), encoding="utf-8")
    return task_path, manifest_md_path, prompt_path


def build_tool_manifest(
    tools: list[AgentToolSpec],
    workspace: ChinaForecastWorkspace,
) -> dict[str, Any]:
    """Return a local-agent-readable manifest of Codex and repo tools."""
    main_agent = workspace.context.agent_model or LOCAL_MAIN_AGENT
    repo_tool_commands = []
    for tool in tools:
        command = _command_for_tool(tool.name, workspace.paths.run_dir)
        repo_tool_commands.append({
            "name": tool.name,
            "description": tool.description,
            "source_categories": tool.source_categories,
            "available": tool.available,
            "metadata": tool.metadata,
            "command_template": command,
        })
    repo_tool_commands.append({
        "name": "agent_review",
        "description": (
            "保存本地主 agent 阅读一个 evidence artifact 后的可展示输出：观察、评估、"
            "实际阅读材料、可展示推理札记、信息缺口、下一步搜索决策、停止或继续规则。"
            "这里记录可审计 rationale，不保存 hidden chain-of-thought。"
        ),
        "source_categories": ["trajectory"],
        "available": True,
        "metadata": {
            "output": (
                "agent_reviews/*.md、full_trajectory.md 和 trajectory analyze step"
            )
        },
        "command_template": agent_review_command(workspace.paths.run_dir),
    })
    repo_tool_commands.append({
        "name": "finalize_video_report",
        "description": (
            "当视频 worker 已留下 metadata、字幕/ASR 或本地视频文件，但未生成 "
            "video_report.pdf/evidence_card.md 时，从已有本地材料补齐视频正文报告。"
        ),
        "source_categories": ["expert_social"],
        "available": True,
        "metadata": {
            "output": (
                "video_metadata.json、video_parse_report.md、claims.jsonl、"
                "evidence_card.md、video_report.tex、video_report.pdf、artifact_index.md"
            ),
            "rule": (
                "只能用于已有本地正文材料的 resource_dir；不得把标题/简介当正文 evidence。"
            ),
        },
        "command_template": finalize_video_report_command(workspace.paths.run_dir),
    })
    repo_tool_commands.append({
        "name": "render_forecast_report_pdf",
        "description": (
            "用 xelatex 把 forecast_report.md/json 渲染为 forecast_report.tex/pdf，"
            "并在 artifacts/report_charts/ 下生成概率摘要图。"
        ),
        "source_categories": ["reporting"],
        "available": True,
        "metadata": {"output": "forecast_report.tex and forecast_report.pdf"},
        "command_template": report_pdf_command(workspace.paths.run_dir),
    })
    return {
        "main_agent": main_agent,
        "workspace": str(workspace.paths.run_dir),
        "tool_policy": {
            "main_forecaster": (
                "task.md 生成后，本地 Codex agent 负责完整 workflow：维护 workspace、"
                "规划搜索、调用工具、复盘证据、判断停止、写报告、渲染 PDF。"
            ),
            "repo_tools": (
                "Repo tools 是可选能力，不构成固定流水线。只有当前 evidence gap 需要时才调用；"
                "每次调用都必须落盘。"
            ),
            "china_source_priority": (
                "中国相关事件必须先写 source_plan.md。开局先看中文互联网内容："
                "expert_social、微博、知乎、公众号、B站/YouTube 中文视频、雪球、研报、"
                "market_professional、professional_media。"
                "official/semi_official 主要做口径校验。Taiwan-side、regional、foreign media "
                "全部后置为 foreign_crosscheck，并写 bias note。"
            ),
            "source_routing": (
                "repo tools 已在工具层实现 domain allowlist/denylist。若 search_web "
                "返回结果被过滤，"
                "agent 应查看 metadata.rejected_category，并换用更合适的 source_category 或 "
                "search_video_sources / search_chinese_platforms。"
            ),
            "market_price_delay": (
                "早期不要查看或强调 p_m。先形成 evidence-first forecast，"
                "再读取市场价格做最终对比。"
            ),
            "prediction_exploration": (
                "预测任务不能只聚合当前信息。Agent 必须主动探索结算日前 outcome "
                "可能如何变化，包括未来催化因素、参与者动机、行动窗口、发布节奏、"
                "政策/产业/技术变化、低信噪预测讨论和分歧观点。不要把这写成固定流水线；"
                "应在每轮 evidence review 中自问当前信息是否只描述现状，下一步是否需要"
                "寻找未来变化机制。"
            ),
            "strong_report_thesis": (
                "最终报告必须形成 forecast thesis：检查战略一致性、外交日程、资源成本、"
                "主流价值、历史路径、resolution 边界、反方最强论点和市场错价机制。"
                "若 evidence 支持极低概率，可以给出接近 0 的 p_f；不要机械保留尾部风险。"
            ),
            "mispricing_verdict": (
                "最终必须给出 mispricing verdict：no_edge、mild_mispricing、"
                "material_overestimate、absolute_overestimate、material_underestimate "
                "或 absolute_underestimate。absolute verdict 必须由机制错配支撑，"
                "不能只因 p_f 低。"
            ),
            "full_trajectory_logging": (
                "每次 evidence review 必须写长版可展示札记，并通过 agent_review "
                "追加到 full_trajectory.md。最终报告 PDF 附录必须全文嵌入该完整轨迹，"
                "forecast_report.md 的完整轨迹附录也必须直接包含 full_trajectory.md "
                "的实质正文，不能只链接或提示读者另开 full_trajectory.md。禁止写"
                "“见 full_trajectory.md”“渲染脚本会自动嵌入”等占位句。"
            ),
            "final_self_review_hook": (
                "停止前必须重新渲染 forecast_report.pdf，运行 "
                "scripts/audit_china_harness_run.py，确认 forecast_report.md 的"
                "完整轨迹附录已直接嵌入 full_trajectory.md 正文，确认没有软链接式"
                "占位句，并运行 sync_resource_status --all 确认无 active lock。"
            ),
            "direct_web_search": (
                "只有 repo search_web 不足时才直接使用 web search。若使用，必须手动写入 "
                "search_actions/manual_*.md 和 source card，之后才能把证据用于报告。"
            ),
            "subagents": (
                "可用于边界清楚的资源处理、独立 rollout、或审计。Subagent 输出必须复制到 "
                "artifacts/ 或 audit.md。"
            ),
            "video_render_subagents": (
                "对 B站/YouTube 具体视频调用 process_resource 后，主 agent 必须读取 "
                "render_request.json 和 subagent_spawn_prompt.md。若 render_status=required，"
                f"启动 `{VIDEO_RENDER_SUBAGENT}` worker，并通过 items[type=skill] 传入 "
                "`bilibili-render-pdf` 或 `youtube-render-pdf` 的 SKILL.md。Worker 只写入"
                "该视频的 output_dir；worker 必须维护 video_render.lock.json 和 "
                "asr.lock.json。主 agent 有界等待，接手前必须检查 lock、"
                "sync_resource_status 和活跃进程，禁止重复启动 ASR。若 worker 已留下 "
                "transcript/srt 或本地视频但未收尾，先调用 finalize_video_report 补齐报告；"
                "完全没有正文材料时再记录 coverage gap。"
            ),
        },
        "codex_tools": [
            {
                "name": "exec_command",
                "use": (
                    "运行 repo tool 命令、检查文件、执行测试，并在本地 workspace 内操作。"
                ),
                "forecast_run_rule": "允许使用。证据搜索优先使用 repo tool 命令。",
            },
            {
                "name": "apply_patch",
                "use": "在 harness 开发阶段编辑 repo 代码或文档。",
                "forecast_run_rule": (
                    "forecast run 期间避免编辑 repo 代码。workspace artifact 用普通文件写入。"
                ),
            },
            {
                "name": "web.run",
                "use": "当 repo search provider 不足时搜索或打开网页。",
                "forecast_run_rule": "允许使用，但必须手动记录 artifact。",
            },
            {
                "name": "multi_agent_v1",
                "use": "为资源处理或独立审计启动本地 subagent。",
                "forecast_run_rule": (
                    "任务边界清楚且输出落盘时允许使用。视频正文解析优先启动 "
                    f"`{VIDEO_RENDER_SUBAGENT}` worker。"
                ),
            },
            {
                "name": "youtube-render-pdf",
                "use": "把 YouTube 视频处理成压缩报告和 PDF。",
                "skill_path": YOUTUBE_RENDER_SKILL_PATH,
                "forecast_run_rule": (
                    "通过 process_resource 生成的 render_request.json 使用；"
                    f"主 agent 把该 skill 作为 items[type=skill] 交给 `{VIDEO_RENDER_SUBAGENT}`。"
                ),
            },
            {
                "name": "bilibili-render-pdf",
                "use": "把 Bilibili 视频处理成中文压缩报告和 PDF。",
                "skill_path": BILIBILI_RENDER_SKILL_PATH,
                "forecast_run_rule": (
                    "通过 process_resource 生成的 render_request.json 使用；"
                    f"主 agent 把该 skill 作为 items[type=skill] 交给 `{VIDEO_RENDER_SUBAGENT}`。"
                ),
            },
            {
                "name": "GitHub",
                "use": "仓库协作。",
                "forecast_run_rule": "forecast run 内禁用，除非用户明确要求。",
            },
            {
                "name": "image_gen",
                "use": "图像生成。",
                "forecast_run_rule": "forecast run 内禁用。",
            },
        ],
        "repo_tools": repo_tool_commands,
    }


def render_tool_manifest_md(manifest: dict[str, Any]) -> str:
    lines = [
        "# 工具清单",
        "",
        f"- main_agent: `{manifest['main_agent']}`",
        f"- workspace: `{manifest['workspace']}`",
        "",
        "## 工具使用协议",
        "",
    ]
    for key, value in manifest["tool_policy"].items():
        lines.append(f"- `{key}`: {value}")
    lines.extend(["", "## Codex 工具", ""])
    for tool in manifest["codex_tools"]:
        tool_lines = [
            f"### {tool['name']}",
            "",
            f"- 用途: {tool['use']}",
        ]
        if tool.get("skill_path"):
            tool_lines.append(f"- skill_path: `{tool['skill_path']}`")
        tool_lines.extend([
            f"- forecast_run_rule: {tool['forecast_run_rule']}",
            "",
        ])
        lines.extend(tool_lines)
    lines.extend(["## Repo 工具", ""])
    for tool in manifest["repo_tools"]:
        lines.extend([
            f"### {tool['name']}",
            "",
            f"- 说明: {tool['description']}",
            f"- source_categories: `{', '.join(tool['source_categories'])}`",
            "",
            "命令模板：",
            "",
            "```bash",
            tool["command_template"],
            "```",
            "",
        ])
    return "\n".join(lines)


def render_task_md(
    context: AgentRunContext,
    workspace: ChinaForecastWorkspace,
    manifest_path: Path,
    harness_doc_path: Path | None,
) -> str:
    harness_line = str(harness_doc_path) if harness_doc_path else context.harness_doc_path
    tool_cmd = f'uv run scripts/china_harness_tool.py --workspace "{workspace.paths.run_dir}"'
    main_agent = context.agent_model or LOCAL_MAIN_AGENT
    return "\n".join([
        "# Markdown 定义的预测 Harness 任务",
        "",
        "## Run 身份",
        "",
        f"- main_agent: `{main_agent}`",
        f"- agent_run_id: `{workspace.context.agent_run_id}`",
        f"- workspace: `{workspace.paths.run_dir}`",
        f"- tool_manifest: `{manifest_path}`",
        f"- harness_plan: `{harness_line}`",
        "",
        "## 市场上下文",
        "",
        f"- event_title: {context.event_title}",
        f"- market_question: {context.market_question}",
        f"- condition_id: `{context.condition_id}`",
        f"- event_id: `{context.event_id}`",
        "- market_price: 仅允许在后置市场对比阶段读取",
        f"- deadline: `{context.deadline.isoformat() if context.deadline else ''}`",
        "",
        "## Resolution 规则",
        "",
        context.resolution_text or "未提供 resolution 文本。",
        "",
        "## 语言协议",
        "",
        "- 除 JSON key、工具名、source category id、文件名、URL、命令行参数外，"
        "所有自然语言内容必须使用中文。",
        "- 这条规则覆盖 `source_plan.md`、`plan.md`、`trajectory.md`、"
        "`agent_reviews/*.md`、`search_actions/*.md`、`sources/**/*.md`、"
        "`claims.md`、`audit.md`、`forecast_report.md`、`forecast_report.tex`、"
        "`forecast_report.pdf`。",
        "- 如果引用英文材料，先用中文归纳事实主张、偏见风险、和对 resolution 的影响。",
        "- 如果 event_title、market_question、resolution_text 是英文，workspace 可以保留原文；"
        "agent 必须在 `source_plan.md` 和 `forecast_report.md` 中先写中文释义。",
        f"- {context.agent_name or main_agent} 的可展示输出、阶段性判断、报告正文都必须使用中文。",
        "",
        "## Harness 协议",
        "",
        "- 这个 Markdown 文件定义本次 workflow；代码 controller 只负责创建 workspace 和工具入口。",
        "- 用户提供 Q + resolution rule 后，只等待报告 artifacts。",
        "- 主 agent 必须从本文件开始端到端完成本次 run。",
        "- 每次搜索后不要把决策交回父级 pipeline。",
        "- 不得使用 `LLMChinaAgent`、DeepSeek API、OpenAI API 作为 main agent loop。",
        "- Python tools 是可选能力，不是固定顺序。",
        "- 每个 evidence action 在用于报告前必须先落盘到本 workspace。",
        "- 写出 evidence-first forecast draft 前，不要读取市场价格。",
        "- Polymarket 页面本身不作为事实 evidence。",
        "- 预测 run 不能停留在当前状态聚合；必须探索结算日前 outcome 可能如何变化。",
        "- 未来导向探索不是固定 pipeline。Agent 必须根据 market 自主寻找变化机制、"
        "参与者动机、发布节奏、政策窗口、行动窗口、预测讨论或分歧观点。",
        "- 低信噪预测材料不能直接采信，也不能因噪声高而完全回避；必须记录筛选和降权理由。",
        "- 中国相关 run 必须中文 source 优先，并覆盖多类 source。",
        "- 开局先找中文社交媒体、微博、知乎、公众号、B站/YouTube 中文时政或专业分析、"
        "专业博主、市场人士、雪球和研报。",
        "- 文本/blog source 分层使用：`T1=知乎、微博`，`T2=雪球`，"
        "`T3=公众号、研报库、新闻社数据库`。T1/T2 是主战场，必须优先做好候选池筛选、"
        "正文深读和作者/互动质量判断；T3 作为补充和交叉背景，不得平均用力。",
        (
            "- 微博/知乎/公众号/雪球/研报/新闻社数据库优先使用 "
            "`search_chinese_platforms`。"
        ),
        "- 每次 `search_chinese_platforms` 后必须阅读 `source_visits/*.md`，"
        "查看各平台 internal_status、browser_status、browser_raw、fallback_raw、"
        "筛选前候选池、tier、engagement、author_quality、quality、选中和拒绝原因。",
        (
            "- API/HTTP 失败时，`search_chinese_platforms` 应先尝试 browser search，"
            "再进入 Tavily/domain fallback；`tavily_domain_fallback` 只能标为外部发现候选。"
        ),
        (
            "- 对 T1/T2 候选，不能停在摘要层。只要知乎、微博、雪球产生 selected URL，"
            "停止前每个平台至少 1 条 selected URL 必须完成 `process_resource`，并在 "
            "trajectory 中说明：为什么这条候选值得细读、互动/回复/收藏/点赞/作者质量如何、"
            "正文读到了什么、为什么使用或拒绝。"
        ),
        (
            "- 对微博/知乎/公众号/雪球/研报/新闻社候选，选中后必须对具体 URL 调用 "
            "`process_resource`；只要某个平台产生 selected URL，停止前该平台至少 1 条 "
            "selected URL 必须有 `process_resource` 记录，并写明 processor_status、"
            "body、body_char_count、是否使用 cookie/browser/fallback、是否进入 evidence；"
            "只有 source card 中 `body=true` 的文本资源可作为正文 evidence。"
            "若没有 selected URL 或正文不可读，写成 coverage gap，不能冒充正文证据。"
        ),
        (
            "- 公众号候选如果来自 `mp.weixin.qq.com/s/...`，搜索标题可能只是 URL；"
            "必须先 `process_resource`，再根据 source card 正文判断相关性。"
            "正文不相关时只能作为 rejected evidence 记录。"
        ),
        (
            "- 雪球候选可能只提取到短正文或投资叙事摘要；如果 `body=true` 但内容很薄，"
            "只能作为市场人士弱信号，不能当作机制证据。"
        ),
        (
            "- 新闻社/半官方材料若宽 query 命中少，应主动改用 `国台办`、`两岸`、"
            "`台独`、`涉台`、`新闻发布会` 等专门 query 重新搜索。"
        ),
        (
            "- B站/YouTube 中文视频优先使用 `search_video_sources`，"
            "再对具体 URL 使用 `process_resource`。"
        ),
        "- 每次 `search_video_sources` 后必须阅读 `source_visits/*.md`，"
        "并在 `agent_review` 中说明筛选前候选集包含哪些视频、播放/评论/收藏/点赞等指标、"
        "为什么选中或拒绝。不能只记录最终入选视频。",
        "- 视频筛选必须显式考虑播放量、评论数、收藏/点赞、发布时间、作者质量、"
        "排序来源和标题党风险。若选择低播放视频，必须说明它为什么仍值得深处理。",
        "- 引用视频时必须优先使用人类可读标题。报告正文、source 覆盖、trajectory、"
        "evidence label 里不得只写 BV 号或 YouTube id；统一写成 `《视频标题》（BVxxxx）` "
        "或 `《视频标题》（YouTube: id/channel）`。",
        "- 每个 Evidence Review 开头必须先写 `Source：材料名`，例如 "
        "`Source：B站《台湾问题分析和演化》（BVxxxx）`。"
        "`review_path`、`evidence_path`、`candidate_set_path` 必须使用当前 run 目录内"
        "相对路径，例如 `./agent_reviews/...`、`./source_visits/...`，不得写整段"
        "`workspace/...` 或绝对路径。",
        "- 若 evidence 来自视频候选池，`full_trajectory.md` 只保留 `candidate_set_path` "
        "和 `候选池入口`，指向 `./source_visits/...`；不要在报告附录里展开完整候选表。",
        "- 如果制作 HTML PPT，主题必须是 trajectory 和 research process：展示 agent 每轮具体看了"
        "什么、如何筛选、为何细读、为何拒绝、读完后下一步怎么变。不要把 "
        "`forecast_report.md` 正文简单改成 slides。",
        "- `process_resource` 会生成视频 render manifest/prompt；主 agent 必须读取 "
        "`render_request.json`、`video_report_prompt.md` 和 `subagent_spawn_prompt.md`。",
        "- 若 `render_status=required`，主 agent 启动 `gpt-5.4-mini` worker，"
        "并通过 `items[type=skill]` 传入 `bilibili-render-pdf` 或 `youtube-render-pdf`。",
        "- worker 开始下载/render 前必须写 `video_render.lock.json`；需要 Whisper/ASR 时"
        "必须写 `asr.lock.json`。完成后把对应 lock 的 status 改为 `complete`，"
        "失败后改为 `failed` 并写 error。",
        "- 主 agent 接手任何视频下载、ffmpeg 或 Whisper 之前，必须先运行 "
        "`sync_resource_status --all`，检查 `resource_processor.json`、`artifact_index.md`、"
        "`download_log.md`、`video_render.lock.json`、`asr.lock.json` 和活跃进程。",
        "- 若存在 active lock 或 worker/ASR 进程仍在运行，主 agent 只能等待、轮询、"
        "读取已有 artifact 或在超时后写 coverage gap，禁止启动第二个 ASR。",
        "- 若 `sync_resource_status` 显示没有活跃进程，且资源目录已有 `.srt`、"
        "`transcript.srt`、`audio.srt` 或本地视频文件但缺 `video_report.pdf`，"
        "调用 `finalize_video_report` 补齐 `video_report.pdf` 和 `evidence_card.md`。",
        "- 只有生成 `video_report.pdf` 和 `evidence_card.md` 后，视频正文才能作为完整 evidence。",
        "- 若视频 render/ASR 超时或失败，必须在 `audit.md` 写 coverage gap，"
        "不要无限等待视频资源处理。",
        "- official/semi_official 主要做口径和背景校验，不作为默认第一主线。",
        "- Taiwan-side、regional、foreign media 全部作为后置 foreign_crosscheck，并写 bias note。",
        "- 只保存可审计 rationale，不保存 hidden chain-of-thought。",
        "",
        "## Agent 责任",
        "",
        "Agent 必须：",
        "",
        "- 阅读本 task 和 harness protocol；",
        "- 第一次 evidence search 前写 `source_plan.md`；",
        "- 自行维护 `plan.md`、`trajectory.md`、`claims.md`、`audit.md`；",
        "- 自行维护 `full_trajectory.md` 和 `thesis_review.md`；",
        "- 基于当前 evidence gap 选择每次 search/tool call；",
        "- 每次产生 evidence 后写入 `agent_reviews/`；",
        "- 自行判断证据是否足够；",
        "- 写 `forecast_report.md` 和 `forecast_report.json`；",
        "- 渲染 `forecast_report.tex` 和 `forecast_report.pdf`；",
        "- 停止前执行 final self-review hook：重新渲染 PDF、运行 harness audit、"
        "确认完整轨迹附录不是软链接占位、确认视频资源无 active lock；",
        "- 只有报告 artifacts 已存在，或 `audit.md` 记录真实 blocker 后才能停止。",
        "",
        "## 必需工作文件",
        "",
        "- `source_plan.md`: 本次 run 的 source 策略和覆盖要求。",
        "- `plan.md`: 初始计划和后续计划更新。",
        "- `trajectory.md`: 每次行动、观察、分析、下一步决定。",
        "- `full_trajectory.md`: 每次阅读材料和可展示推理札记的完整留档。",
        "- `search_actions/`: 每次 search/tool action 的落盘记录。",
        "- `agent_reviews/`: 每个 evidence step 后的主 agent 可展示输出。",
        "- `sources/`: 按 source category 分组的 source cards。",
        "- `claims.md`: 与 forecast 相关的 claims、方向、source path。",
        "- `thesis_review.md`: 最终报告前的战略一致性、反方论点和市场错价复盘。",
        "- `audit.md`: source 质量、自引用、resolution、confidence、gap 检查。",
        "- `forecast_report.md`: 最终中文可读报告。",
        "- `forecast_report.json`: 最终机器可读报告。",
        "- `forecast_report.tex`: 最终展示报告的 LaTeX 源码。",
        "- `forecast_report.pdf`: LaTeX 渲染的最终展示报告，可包含图表。",
        "",
        "## Agentic Loop 要求",
        "",
        "第一次 evidence search 前，`source_plan.md` 必须写清：",
        "",
        "- `expert_social`、video/social analysis、`market_professional`、"
        "`professional_media`、`official`、`semi_official`、`foreign_crosscheck` "
        "的预期价值和访问计划；",
        "- 哪些中文社媒、微博、知乎、公众号、视频、专业人士、市场人士、雪球、"
        "研报和新闻社 source 会优先尝试；",
        "- 缺失 source category 如何在 `audit.md` 中记录；",
        "- 何时读取市场价格做最终对比。",
        "",
        "中国相关事件的默认 source 优先级：",
        "",
        "```text",
        "第一层：expert_social, video/social analysis, market_professional",
        "第一层工具：search_chinese_platforms, search_video_sources",
        "第二层：professional_media, generic_search_tools",
        "第三层：official, semi_official",
        "第四层：foreign_crosscheck，包括 Taiwan-side / regional / foreign media",
        "第五层：prediction_sources, model_baselines",
        "```",
        "",
        "每个搜索循环必须在 `trajectory.md` 中显式呈现：",
        "",
        "```text",
        "证据 k -> 分析 k -> 搜索 k+1 -> 证据 k+1",
        "```",
        "",
        "每个 evidence step 后写清：",
        "",
        "- 正在复盘的已落盘 evidence path；",
        "- 主 agent 对这一步的可展示输出；",
        "- 当前 evidence 支持或削弱了什么；",
        "- 实际读到的 source title、URL、摘要、视频报告、评论样本或文件路径；",
        "- 视频材料必须用标题称呼；BV 号、YouTube id、URL 只作为括号内辅助标识；",
        "- 可展示推理札记，长度不受摘要限制；",
        "- 还剩什么信息缺口；",
        "- 哪些材料被拒绝或降权，以及原因；",
        "- 当前 evidence 是否只说明现状，是否还缺少面向未来变化机制的探索；",
        "- 为什么选择下一类 source/tool；",
        "- 什么结果会让 agent 停止或继续。",
        "",
        "## Prediction-oriented Exploration 要求",
        "",
        "预测任务必须回答两类问题：",
        "",
        "- 当前状态是什么；",
        "- 到结算时，哪些机制可能改变当前状态。",
        "",
        "Agent 每轮复盘时必须主动检查：",
        "",
        "- outcome 会被哪些未来事件、发布、政策、行动、样本累积、对手反应或市场机制改变；",
        "- 已读 evidence 是现状证据、变化机制证据、预测观点证据、还是价格信号；",
        "- 是否需要更发散地搜索低信噪但可能有高价值的预测讨论；",
        (
            "- 哪些预测材料被拒绝，拒绝原因是标题党、无来源、过旧、"
            "和 resolution 不匹配、还是立场/营销偏差；"
        ),
        "- 继续搜索最可能发现什么，为什么值得或不值得继续。",
        "",
        "`audit.md` 必须包含：",
        "",
        "- `current_state_evidence`",
        "- `future_change_mechanisms`",
        "- `future_or_prediction_sources_attempted`",
        "- `low_signal_sources_rejected`",
        "- `why_future_exploration_is_sufficient_or_blocked`",
        "- `what_new_information_would_change_forecast`",
        "",
        "每次 `search_web`、`search_video_sources`、`search_chinese_platforms` "
        "或 `process_resource` 后，"
        "必须先调用 `agent_review`，"
        "再发起下一次 evidence search。reviewer 主要阅读这部分可展示输出；"
        "不要写 hidden chain-of-thought。",
        "",
        "每次 `agent_review` 必须尽量填写这些长字段：",
        "",
        "- `raw_materials_seen`: 实际阅读了哪些文件、搜索结果、标题、URL、摘要、"
        "视频报告、评论样本。",
        "- `source_excerpt_or_summary`: 关键材料的压缩摘要或短摘录。",
        "- `visible_reasoning_memo`: 面向用户可展示的推理札记，可很长。",
        "- `source_selection_notes`: 为什么选择该 source/query/tool。",
        "- `rejected_or_downweighted`: 哪些材料被拒绝或降权，原因是什么。",
        "",
        "`agent_review` 会把以上内容追加到 `full_trajectory.md`。最终报告附录不能只写"
        "示意性 Think/Evidence/Next 摘要；必须把 `full_trajectory.md` 的实质内容"
        "全文嵌入报告和 PDF 附录。",
        "",
        "## Strong Thesis Review 要求",
        "",
        "最终给出概率前，必须写 `thesis_review.md`，并逐项回答：",
        "",
        "- `strategic_fit`: outcome 与长期国家战略是否一致。",
        "- `diplomatic_calendar`: 未来窗口内是否有外交缓和、会谈、选举、国际会议、"
        "贸易/科技谈判等关键日程。",
        "- `resource_cost`: outcome 是否消耗大量政治、军事、经济、外交资源。",
        "- `normative_fit`: outcome 是否符合中国公开主流叙事、价值观和国际倡议。",
        "- `path_dependency`: 历史轨迹支持突然 spike，还是支持平滑推进。",
        "- `resolution_specificity`: 市场 resolution 要求的动作是否比一般紧张局势更强。",
        "- `base_rate_and_floor`: 给出概率底线，说明为什么这个 floor 合理。",
        "- `probability_floor_decomposition`: 拆分误判、突发触发、黑箱信息、"
        "机制缺口分别贡献多少概率。",
        "- `strongest_countercase`: 反方最强论点是什么。",
        "- `mispricing_claim`: 市场是否错价，错价幅度是否足够支撑交易判断。",
        "- `conviction_scale`: low_conviction / directional / material / absolute。",
        "- `paper_trade_view`: 买 YES、买 NO 或不交易；说明近似收益、最大损失和失效条件。",
        "",
        "强低概率结论是允许的。若证据显示 outcome 缺少现实机制、战略上不顺、"
        "机会成本极高、且 resolution 要求极强，可以给出接近 0 的 p_f。"
        "不要因为泛泛的 tail risk 存在，就机械保留 5% 或 10%。",
        "",
        "## Video Render Subagent 协议",
        "",
        "当 `process_resource` 处理 B站/YouTube URL 时，会在该资源目录写出：",
        "",
        "- `render_request.json`: 机器可读 contract，包含 `skill_name`、"
        "`skill_path`、`subagent_model`、`spawn_agent_args`、`timeout_seconds`、"
        "`completion_check`。",
        "- `video_report_prompt.md`: 给视频 worker 的具体任务。",
        "- `subagent_spawn_prompt.md`: 主 agent 可照抄执行的 subagent 启动说明。",
        "",
        "主 agent 的执行规则：",
        "",
        "- 读取 `render_request.json`。",
        "- 若 `render_status=complete`，直接读取 `evidence_card.md` 和 `video_report.pdf`。",
        "- 若 `render_status=required`，启动 `gpt-5.4-mini` worker。",
        "- 启动 worker 时必须把 `skill_path` 作为 `items[type=skill]` 传入。",
        "- worker 只允许写该资源的 `output_dir`。",
        "- worker 必须用 `video_render.lock.json` 和 `asr.lock.json` 标记下载/render/ASR "
        "的运行状态。",
        "- 主 agent 接手视频处理前必须检查 lock 文件、`download_log.md`、"
        "`resource_processor.json` 和活跃进程；active lock 存在时不得重复 ASR。",
        "- 主 agent 等待 `timeout_seconds` 内完成；完成后读取 `evidence_card.md`。",
        "- 超时或失败后，在 `audit.md` 写 coverage gap，不把视频正文当成已读 evidence。",
        "",
        "## 工具示例",
        "",
        "这些命令只是可用能力示例，不代表固定顺序：",
        "",
        "```bash",
        f"{tool_cmd} export_source_registry",
        f'{tool_cmd} generate_china_queries --query "{context.market_question}"',
        f'{tool_cmd} search_web --query "<B站/中文时政博主/专家分析 query>" '
        '--source-category "expert_social"',
        f'{tool_cmd} search_video_sources --query "<中文视频 query>" '
        '--platforms "bilibili,youtube" --max-results 6',
        f'{tool_cmd} search_chinese_platforms --query "<中文社媒/专业讨论 query>" '
        '--platforms "weibo,zhihu,wechat" --max-results 5',
        f'{tool_cmd} search_chinese_platforms --query "<市场人士/研报 query>" '
        '--platforms "xueqiu,research_reports" --max-results 5',
        f'{tool_cmd} search_chinese_platforms --query "<新闻社/半官方 query>" '
        '--platforms "newswire" --max-results 5',
        f'{tool_cmd} search_web --query "<中文市场人士/专业讨论 query>" '
        '--source-category "market_professional"',
        f'{tool_cmd} search_web --query "<中文新闻/专业媒体 query>" '
        '--source-category "professional_media"',
        f'{tool_cmd} process_resource --url "<B站、YouTube 中文视频或专业人士 URL>" '
        '--source-category "expert_social" --orientation "forecast_evidence" '
        '--render-timeout-seconds 900',
        f'{tool_cmd} finalize_video_report --resource-dir "<resources/xxx 或绝对 resource_dir>"',
        f"{tool_cmd} read_polymarket_context  # evidence-first forecast 后再调用",
        "```",
        "",
        "只有在写清当前信息缺口和选择理由之后，才调用命令。",
        "",
        "## Report 协议",
        "",
        "`forecast_report.md` 必须包含以下中文 section：",
        "",
        "- 结论先行：给出最终 thesis、p_f、p_m_delta、是否存在市场错价",
        "- Mispricing Verdict：明确 verdict 与原因",
        "- Paper Trade View：如果存在错价，说明交易方向、近似收益和失效条件",
        "- 市场概况",
        "- Resolution 解释",
        "- Source 覆盖",
        "- Source 覆盖必须先写中文社媒/视频/专业讨论和中文专业媒体，"
        "再写官方口径，最后才写 Taiwan-side / regional / foreign cross-check",
        "- Source 覆盖中的视频必须写标题，例如 `《别被美军的兵推给骗了》（BV1...）`；"
        "不得只写 `BV1...`、`BV 号` 或裸 URL",
        "- 核心论证链：用连续因果链解释判断，不允许只堆 evidence bullet",
        "- 中国语境与战略一致性：国家战略、外交日程、资源成本、主流价值、历史路径",
        "- Resolution 边界：哪些高张力动作仍不满足结算条件",
        "- p_evidence",
        "- p_f",
        "- confidence",
        "- calibration_status = `uncalibrated`，除非使用了经验校准",
        "- 市场错判在哪里：如果 p_f 与 p_m 差异明显，说明 mispricing 来源",
        "- 尾部风险处理：具体触发机制、缺失机制、概率上限",
        "- Probability Floor Decomposition：拆解概率底线来源，不机械保留尾部风险",
        "- 支持 YES 的关键证据",
        "- 支持 NO 的关键证据",
        "- 最强反方论点及驳回",
        "- 主要不确定性",
        "- 什么会改变预测",
        "- 完整轨迹附录：全文嵌入 `full_trajectory.md` 的实质内容，展示每步读了什么、"
        "如何评价、为什么进入下一步；不能只写“见 full_trajectory.md”",
        "- 完整轨迹附录里的每个 Evidence Review 必须以 `Source：...` 开头，路径必须是"
        "`./...` run 内短路径；视频候选池保留 `./source_visits/...` 入口，不展开长表。",
        "- 完整轨迹附录不能写“渲染脚本会自动嵌入”“见下方嵌入的完整轨迹正文”等占位句；"
        "报告 Markdown 本身必须包含完整轨迹正文",
        "- 最终终止决策：说明是谁判断停止、停止前完成了哪些动作、为什么不继续搜索、"
        "剩余缺口是否已写入 audit",
        "- 证据文件列表",
        "- 最后的市场对比：p_m 和 p_m_delta",
        "",
        "`forecast_report.json` 必须包含：",
        "",
        "```json",
        "{",
        '  "condition_id": "",',
        '  "p_evidence": 0.0,',
        '  "p_f": 0.0,',
        '  "p_m": 0.0,',
        '  "p_m_delta": 0.0,',
        '  "confidence": 0.0,',
        '  "calibration_status": "uncalibrated",',
        '  "mispricing_verdict": "",',
        '  "paper_trade_view": {},',
        '  "evidence_paths": [],',
        f'  "model": "{main_agent}",',
        '  "metadata": {}',
        "}",
        "```",
        "",
        "`forecast_report.tex` 和 `forecast_report.pdf` 必须从最终 Markdown/JSON 报告生成：",
        "",
        "```bash",
        f'uv run scripts/render_forecast_report_pdf.py --workspace "{workspace.paths.run_dir}"',
        "```",
        "",
        "PDF 必须是 LaTeX 渲染报告。若 `forecast_report.json` 含数值字段，"
        "PDF 应包含中文正文、摘要表、概率/置信度图表。",
        "渲染后必须运行 `scripts/audit_china_harness_run.py`。若 audit 报告 "
        "`softlink_trajectory_appendix` 或 `missing_full_trajectory_appendix`，"
        "必须修复 `forecast_report.md` 后重渲染。",
        "",
    ])


def render_codex_prompt(workspace: ChinaForecastWorkspace) -> str:
    return "\n".join([
        "运行 Markdown 定义的预测 harness，直到完成：",
        "",
        f"`{workspace.paths.run_dir / 'task.md'}`",
        "",
        "你是本地主 forecaster。用户只提供 Q + resolution，然后等待报告 artifacts。",
        f"只有当前 evidence gap 需要时，才使用 `{workspace.paths.run_dir / 'tool_manifest.md'}` "
        "中的 repo tools。",
        "所有决策和 evidence 都必须落盘到 workspace。",
        "第一次 evidence search 前先写 `source_plan.md`。",
        "每次 evidence review 都要用 `agent_review` 写长版可展示札记；"
        "`full_trajectory.md` 要能复盘你实际读了什么、如何评价、为什么继续。",
        "中文互联网内容和中国语境 source 优先：社媒、视频、专业博主、市场人士先行；"
        "official 和 foreign_crosscheck 后置。market price 必须后置到 "
        "evidence-first forecast 之后。",
        "不要只聚合当前信息。每轮 evidence review 必须检查结算日前 outcome 可能如何变化，"
        "并主动寻找未来催化因素、发布节奏、行动窗口、政策/产业/技术变化、低信噪预测讨论或分歧观点。"
        "如果拒绝这类 source，必须写明拒绝理由。",
        "除 JSON key、工具名、source category id、文件名、URL、命令行参数外，"
        "所有自然语言输出必须使用中文。",
        "如果输入的 market/resolution 是英文，保留原文并先写中文释义，后续分析用中文。",
        "最终概率前写 `thesis_review.md`，检查战略一致性、外交日程、资源成本、"
        "主流价值、历史路径、resolution 边界、反方最强论点和市场错价机制。",
        "最后写入 `forecast_report.md`、`forecast_report.json`，并渲染 "
        "`forecast_report.tex` 和 `forecast_report.pdf`。",
        "停止前必须运行 `scripts/audit_china_harness_run.py`，并确认 "
        "`forecast_report.md` 的完整轨迹附录直接嵌入 `full_trajectory.md` 正文，"
        "不是链接、占位或“渲染时自动嵌入”的承诺。",
        "停止前还必须检查每个 Evidence Review 是否有 `Source：...`，路径是否为"
        "`./...`，视频候选池是否保留 `./source_visits/...` 入口且没有展开长表。",
        "停止前必须检查知乎/微博/雪球每个有 selected URL 的平台至少 1 条 selected URL "
        "已完成 `process_resource`，且 trajectory 写明互动/作者质量、正文摘要、使用或拒绝理由。",
        "停止前必须检查微博/知乎/公众号/雪球/研报/新闻社中，每个有 selected URL 的平台"
        "是否至少 1 条 selected URL 已完成 `process_resource`；未完成的平台必须在 "
        "`audit.md` 写明 coverage gap 和原因。",
        "",
    ])


def _command_for_tool(tool_name: str, run_dir: Path) -> str:
    base = f'uv run scripts/china_harness_tool.py --workspace "{run_dir}" {tool_name}'
    if tool_name == "search_web":
        return (
            f'{base} --query "<中文或后置交叉检查 query>" --source-category "<category>" '
            "--max-results 5"
        )
    if tool_name == "search_video_sources":
        return (
            f'{base} --query "<B站/YouTube 中文视频 query>" '
            '--platforms "bilibili,youtube" --max-results 6'
        )
    if tool_name == "search_chinese_platforms":
        return (
            f'{base} --query "<中文平台 query>" '
            '--platforms "weibo,zhihu,wechat,xueqiu,research_reports,newswire" '
            "--max-results 5"
        )
    if tool_name == "generate_china_queries":
        return f'{base} --query "<market 或 evidence-driven query>"'
    if tool_name == "process_resource":
        return (
            f'{base} --url "<url>" --source-category "<category>" '
            '--orientation "forecast_evidence" --render-timeout-seconds 900'
        )
    if tool_name == "finalize_video_report":
        return f'{base} --resource-dir "<resources/xxx 或绝对 resource_dir>"'
    if tool_name == "model_baseline_forecast":
        return f"{base}  # 可选 baseline；默认市场锚定，不调用 API"
    return base


def report_pdf_command(run_dir: Path) -> str:
    return f'uv run scripts/render_forecast_report_pdf.py --workspace "{run_dir}"'


def agent_review_command(run_dir: Path) -> str:
    return " \\\n".join([
        f'uv run scripts/china_harness_tool.py --workspace "{run_dir}" agent_review',
        '  --evidence-path "<search action、source visit、resource 或 source card 路径>"',
        (
            '  --evidence-label "<人类可读材料名，例如：'
            'B站视频：《标题》（BVxxxx）；国务院报告：标题 日期>"'
        ),
        '  --source-url "<可选：原始 URL>"',
        '  --candidate-set-path "<可选：source_visits/xxx.md，说明该证据从哪个候选集筛出>"',
        '  --agent-output "<主 agent 对该证据的中文可展示输出>"',
        '  --observation "<证据具体说了什么>"',
        '  --assessment "<它如何影响 YES/NO/relevance 判断>"',
        '  --raw-materials-seen "<实际读到的文件、标题、URL、摘要、视频报告、评论样本>"',
        '  --source-excerpt-or-summary "<关键材料的压缩摘要或短摘录>"',
        '  --visible-reasoning-memo "<长版可展示推理札记，不写 hidden chain-of-thought>"',
        '  --source-selection-notes "<为什么选择该 source/query/tool>"',
        '  --rejected-or-downweighted "<被拒绝或降权的材料及原因>"',
        '  --information-gap "<还缺什么信息>"',
        '  --next-search-decision "<下一步 query/source/tool 或报告决策>"',
        '  --stop-or-continue "<继续原因或停止原因>"',
        '  --confidence-note "<可选：对 confidence 的影响>"',
    ])


def finalize_video_report_command(run_dir: Path) -> str:
    return (
        f'uv run scripts/china_harness_tool.py --workspace "{run_dir}" '
        'finalize_video_report --resource-dir "<resources/xxx 或绝对 resource_dir>"'
    )
