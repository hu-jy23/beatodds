# China-specific Harness 审计日志

状态日期：2026-06-10

## 审计准则

核心检查对象：

```text
Evidence k -> analysis k -> Search k+1 -> Evidence k+1
```

合格 artifact 需要证明 agent 不是按固定工具清单机械执行，而是在每轮搜索后读取 evidence，说明当前 gap，并用该 gap 选择下一步搜索。

## Run 1: Taiwan invasion, initial real run

```text
run_path = workspace/china_forecasts/will_china_invade_taiwan_by_2026/2026/ds_agent_20260609_172700_95e35dc7
case = Will China invade Taiwan by end of 2026?
p_m = 0.0625
p_f = 0.0400
confidence = 0.85
passed = partial
```

发现：

- trajectory 已经出现 evidence-driven loop：官方 search 之后，下一轮转向 foreign crosscheck。
- report 引用了多类 evidence。
- foreign search 夹带 Polymarket 页面，并被当作 evidence 使用。

处理：

- `search_web` 默认过滤 Polymarket URL。
- 只有 `source_category=prediction_sources` 或显式 `allow_self_reference=true` 时允许 Polymarket 页面进入结果。

## Run 2: Taiwan invasion, filtered real run

```text
run_path = workspace/china_forecasts/will_china_invade_taiwan_by_2026/2026/ds_agent_20260609_172920_b1fd5e5d
case = Will China invade Taiwan by end of 2026?
p_m = 0.0625
p_f = 0.0625
confidence = 0.15
passed = pass with issues
```

Evidence chain：

- Step 3 evidence：官方外交部搜索返回中国对台政策表述、和平统一和不承诺放弃武力等材料。
- Step 4 analysis：agent 明确说官方材料提供政策框架，但缺少外部军事/情报评估。
- Step 4 search：转向 foreign crosscheck，查询 Reuters/Bloomberg/analysis 角度。
- Step 4 evidence：返回 ISW/AEI、Lowy 等外部军事和情报评估。
- Step 6 report：综合 p_m、官方表述和外部评估后输出 report。

通过项：

- `trajectory.md` 能看出 Evidence -> analysis -> next search。
- Polymarket 页面被过滤，只保留 market context 里的 p_m baseline。
- `forecast_report.md/json` 包含 p_f、confidence、p_m_delta、calibration_status。

问题：

- 官方 source cards 有 Tavily 摘要噪声，部分是导航/站点 boilerplate。
- `claims.md` 原先直接写长 summary，不够适合人工审计。
- `model_baseline_forecast` 仍是 stub。
- LLM 有时在单步 analysis 里说要执行两个 search，但 controller 每步只执行一个 tool call。

处理：

- `claims.md` 改为保存标题加压缩摘要。
- boilerplate 过滤和 baseline tool 实装留到后续。

## Run 3: Xi leadership, initial real run

```text
run_path = workspace/china_forecasts/xi_jinping_leadership_before_2027/xi_jinping_out_before_2027/ds_agent_20260609_173339_98139383
case = Xi Jinping out before 2027?
p_m = 0.0695
p_f = 0.0200
confidence = 0.85
passed = pass with issues
```

Evidence chain：

- Step 3 evidence：foreign crosscheck 返回 CSIS、Asia Society、Global Security Review 等来源，集中讨论继任、权力集中和制度风险。
- Step 4 analysis：agent 明确指出只有外部来源，缺少中国官方渠道验证。
- Step 5 search：转向官方中文来源，查询习近平 2026 领导活动。
- Step 6 report：综合外部继任分析、官方活动记录和 p_m 后输出报告。

通过项：

- `trajectory.md` 能显示 evidence-driven loop。
- report 解释了低透明度政治事件中的 tail risk。
- `claims.md` 已变成标题加压缩摘要，比原始长 summary 更可读。

问题：

- `generate_china_queries` 因 resolution 里有 `Central Military Commission`，误触发台海模板，生成了大量台湾/台海查询。
- 官方 search result 仍有导航页和站点 boilerplate。

处理：

- Xi leadership / succession 触发条件提前，台海模板只由 Taiwan/台湾/台海/invasion 触发。
- 新增测试：Xi leadership case 不应生成台湾/台海查询。

## Run 4: Xi leadership, process_resource failure run

```text
run_path = workspace/china_forecasts/xi_jinping_leadership_before_2027/xi_jinping_out_before_2027/ds_agent_20260609_173652_c626bf31
case = Xi Jinping out before 2027?
p_m = 0.0695
p_f = 0.0695
confidence = 0.10
passed = fail
```

发现：

- query 生成已不再污染为台海 case。
- LLM 反复想处理 Yahoo/BBC URL，但 tool call 中 URL 没有进入 `process_resource`。
- controller 达到 max_steps 后写 fallback report。

处理：

- `ToolCallRequest` 增加 `url` 字段。
- controller 会把 `tool_call.url` 或 URL 型 `tool_call.query` 归一化为 `url=`。
- LLM prompt 明确要求 `process_resource` 使用 `url` 字段。
- 新增测试覆盖 `tool_call.url` 和 URL 型 `query` 两种情况。

## Run 5: Xi leadership, source-category guarded run

```text
run_path = workspace/china_forecasts/xi_jinping_leadership_before_2027/xi_jinping_out_before_2027/ds_agent_20260609_174303_0bfe1d02
case = Xi Jinping out before 2027?
p_m = 0.0695
p_f = 0.0695
confidence = 0.15
passed = pass
```

Evidence chain：

- Step 4 evidence：foreign crosscheck 返回 The Diplomat、Asia Society、gov.cn 等较干净来源。
- Step 5 analysis：agent 明确说这些来源支持 Xi 延续到 2027，缺少独立量化 baseline。
- Step 5 tool：调用 `model_baseline_forecast`。
- Step 6 report：说明没有可信 YES evidence，p_f 暂时锚定 p_m，confidence 低。

通过项：

- `generated_queries.md` 围绕领导层、继任和政治稳定，没有台海污染。
- `search_web` 记录 `filtered_category_mismatch_count=1`，低质量社交/视频结果被过滤。
- report 不再引用 PolyPredict/YouTube/Facebook。
- trajectory 中能看到 evidence -> analysis -> baseline/report 的决策链。

剩余问题：

- `model_baseline_forecast` 仍是 market-anchored stub。
- 官方/半官方来源仍可能出现站点目录、旧文章和 boilerplate，需要后续清洗和重排。
- agent 有时在 action 文本中说“多次搜索”，但 controller 每轮只执行一个 tool。

## Run 6: Xi leadership, LLM baseline run

```text
run_path = workspace/china_forecasts/xi_jinping_leadership_before_2027/xi_jinping_out_before_2027/ds_agent_20260609_174913_f61a0472
case = Xi Jinping out before 2027?
p_m = 0.0695
p_f = 0.0695
confidence = 0.95
passed = partial
```

通过项：

- `model_baseline_forecast` 已实际调用 DeepSeek，`model_baseline.json` 显示 `llm_enabled=true`。
- baseline artifact 保存了 `p_f`、`confidence`、`reasoning`、`evidence_used`。
- report 引用了 baseline artifact。

问题：

- DeepSeek baseline 返回 `confidence=0.95` 和 `well-calibrated`，但当前没有经验校准，不能接受。
- 最终 report 继承了过高 confidence。

处理：

- baseline tool 将 LLM confidence 上限限制为 0.75。
- baseline tool 强制 `calibration_status=uncalibrated`。
- final report parser 将非经验校准状态归一为 `uncalibrated`。
- prompt 明确：confidence 是 evidence confidence，不是 YES/NO outcome certainty。
- 新增测试覆盖 LLM baseline 过度自信输出会被压回合规范围。

## Run 7: Xi leadership, source quality filter run

```text
run_path = workspace/china_forecasts/xi_jinping_leadership_before_2027/xi_jinping_out_before_2027/ds_agent_20260609_180310_af439da8
case = Xi Jinping out before 2027?
p_m = 0.0695
p_f = 0.0695
confidence = 0.75
passed = pass with issues
```

通过项：

- `search_quality` metadata 已写入 source cards 和 search action JSON。
- `foreign_crosscheck` 记录 `filtered_self_reference_count=1` 和 `filtered_category_mismatch_count=2`。
- official source cards 记录 `context_entity_hit`，说明结果与 market context 中的 Xi/Jinping 核心实体匹配。
- trajectory 保留 evidence-driven loop：foreign evidence 后转向 official search，再调用 baseline，最后 report。
- final confidence 被限制到 0.75，calibration 保持 `uncalibrated`。

问题：

- official query `site:gov.cn 习近平 中央 政治局` 仍会返回较旧材料。
- quality filter 能过滤 context entity 缺失的结果，但不能替代更好的 query construction。

处理：

- 新增 `source_quality.py`：按 query overlap、context entity、snippet 质量、boilerplate 信号、staleness 打分并重排。
- `search_web` metadata 增加 `filtered_quality_count` 和 `rejected_quality`。

## Run 8: Markdown-first gpt-5.4 future-oriented validation

状态日期：2026-06-12

目标：

- 验证 Markdown-defined local harness 是否能支持高自由度、未来导向的 agentic search。
- 检查 run 是否体现 `Think k -> Evidence k -> Next k+1`。
- 检查 agent 是否探索结算日前会改变 outcome 的机制，而非只聚合当前状态。

新增验收：

- `scripts/audit_china_harness_run.py` 增加 7 项 rubric：
  - 时间视角
  - 搜索分支
  - 查询设计
  - 轨迹因果
  - 前瞻 source 覆盖
  - 反证搜索
  - 停止条件
- 通过线：总分 `>=10`，且前 4 个硬门槛不得为 `0`。
- `audit.md` 必须包含：
  - `current_state_evidence`
  - `future_change_mechanisms`
  - `future_or_prediction_sources_attempted`
  - `low_signal_sources_rejected`
  - `why_future_exploration_is_sufficient_or_blocked`
  - `what_new_information_would_change_forecast`

通过的三件事件：

```text
case = Best Chinese AI Company end of July
run_path = workspace/best_chinese_ai_company_end_of_july/which_primarily_chinese_company_will_own_the_highest_ranked_model_on_lmarena_text_arena_overall/gpt-5.4
audit_score = 13/14
top_pick = Alibaba
p_f = 0.33
confidence = 0.52
pdf = forecast_report.pdf
```

```text
case = Will China invade Taiwan by end of 2026?
run_path = workspace/will_china_invade_taiwan_by/2026/gpt-5.4
audit_score = 12/14
p_f = 0.10
confidence = 0.62
pdf = forecast_report.pdf
```

```text
case = Xi Jinping out as CCP General Secretary before 2027?
run_path = workspace/xi_jinping_leadership_before_2027/will_xi_jinping_be_out_as_chinese_communist_party_general_secretary_before_jan_1_2027/gpt-5.4
audit_score = 11/14
p_f = 0.07
confidence = 0.78
pdf = forecast_report.pdf
```

关键发现：

- Best AI run 的质量最高，轨迹明确从当前 LMArena 榜单转向 7 月底前新模型发布节奏、preview 收样、时间泄漏拒绝、foreign cross-check 和后置市场价格。
- Taiwan run 把“封锁/准封锁/隔离”和“符合 resolution 的大规模入侵”分开，避免把高压态势直接映射成 YES。
- Xi run 把 `2027-01-01` 截点和 2027 下半年二十一大窗口错位作为核心机制，避免只说“当前仍在任”。
- Best AI 主 agent 在等待视频 ASR/报告合成时停滞。最终报告由本地基于已落盘 gpt-5.4 trajectory 和 evidence 补写收敛版，并在 `audit.md` 记录 `completion_note`。

工程处理：

- `src/beatodds/agents/local_harness.py` 不再把 task/tool manifest 的主 agent 固定为 `codex:gpt-5.4-mini`；现在使用 run context 里的 `agent_model`。
- `scripts/run_china_harness.py` 输出的 `main_agent` 与 `--agent-model` 保持一致。
- `scripts/audit_china_harness_run.py` 的 query-design rubric 从 AI 专项词扩展为通用预测词，覆盖 `2026/2027`、窗口、选举、APEC、触发器、升级路径、封锁、行动等非 AI 事件。

2026-06-12 后续处理：

- 已给 video/resource processing 增加明确 timeout/fallback contract：未完成时写入 coverage gap，主 agent 继续 synthesis。
- 已把 `process_resource` 从 stub 升级为 render-ready 入口：写入 `render_request.json`、`video_report_prompt.md`、`subagent_spawn_prompt.md`、`artifact_index.md`，并指向 `bilibili-render-pdf` / `youtube-render-pdf`。
- 已新增 `ForecastOutcomeProbability` 和 `MultiOutcomeForecast`，并让 PDF renderer 支持 `metadata.outcome_probabilities` / 顶层 `outcomes`。
- 已把 video render contract 升级为 subagent-ready：`render_request.json` 包含
  `gpt-5.4-mini` worker、render skill 的 `SKILL.md` 路径、
  `multi_agent_v1.spawn_agent` 参数、写入范围、等待策略和完成检查；
  `subagent_spawn_prompt.md` 给 `gpt-5.4` 主 agent 直接使用。

## Run 9-10: Taiwan strong-thesis and full-trajectory iteration

状态日期：2026-06-12

目标：

- 将用户对 Taiwan case 的强判断标准写成通用 harness 协议。
- 让报告能形成清晰 thesis、mispricing verdict 和 paper trade view。
- 让轨迹附录从摘要式链路升级为可审计的详细留档。
- 至少完整跑两轮 Taiwan run，并根据 artifact 审查继续修改。

新增协议：

- 新文档 `docs/china_harness_strong_report_protocol_zh.md` 记录用户原话和工程化协议。
- `task.md` / `tool_manifest.md` 现在要求 `thesis_review.md`、`full_trajectory.md`、强结论 review 和后置市场对比。
- `agent_review` 新增实际阅读材料、材料摘要、可展示推理札记、source 选择说明、拒绝/降权材料等字段。
- `scripts/audit_china_harness_run.py` 新增强结论和完整轨迹 rubric，并检查：
  - `full_trajectory.md`
  - `thesis_review.md`
  - `Mispricing Verdict`
  - `Paper Trade View`
  - `Probability Floor Decomposition`

Round 1：

```text
case = Will China invade Taiwan by end of 2026?
run_path = workspace/will_china_invade_taiwan_by/2026/gpt-5.4-strong-round1
model = codex:gpt-5.4
p_m = 0.068
p_evidence = 0.015
p_f = 0.015
p_m_delta = -0.053
confidence = 0.76
calibration_status = uncalibrated
passed = pass
```

Round 1 发现：

- 报告已经把“台海广义风险”和“2026 年底前 actual invasion attempt”分开。
- 结论从旧版 `p_f=0.08` 降到 `p_f=0.015`。
- 轨迹留档明显更细，但最终报告缺少显式 `Mispricing Verdict`、`Paper Trade View` 和概率底线拆解。

Round 1 后处理：

- 报告协议新增 `Mispricing Verdict`、`Paper Trade View`、`Probability Floor Decomposition`。
- `forecast_report.json` schema 新增 `mispricing_verdict` 和 `paper_trade_view`。
- audit script 将这些 section 设为强报告验收项。

Round 2：

```text
case = Will China invade Taiwan by end of 2026?
run_path = workspace/will_china_invade_taiwan_by/2026/gpt-5.4-strong-round2
model = codex:gpt-5.4
p_m = 0.068
p_evidence = 0.008
p_f = 0.008
p_m_delta = -0.060
confidence = 0.81
calibration_status = uncalibrated
mispricing_verdict = absolute_overestimate
paper_trade = buy_no
audit_score = 16
passed = pass
```

Round 2 关键 artifact：

```text
forecast_report.pdf = workspace/will_china_invade_taiwan_by/2026/gpt-5.4-strong-round2/forecast_report.pdf
forecast_report.md = workspace/will_china_invade_taiwan_by/2026/gpt-5.4-strong-round2/forecast_report.md
forecast_report.json = workspace/will_china_invade_taiwan_by/2026/gpt-5.4-strong-round2/forecast_report.json
full_trajectory.md = workspace/will_china_invade_taiwan_by/2026/gpt-5.4-strong-round2/full_trajectory.md
thesis_review.md = workspace/will_china_invade_taiwan_by/2026/gpt-5.4-strong-round2/thesis_review.md
```

Round 2 通过项：

- `forecast_report.pdf` 约 27 页，包含完整轨迹附录。
- `full_trajectory.md` 507 行，保留每轮实际读到的材料、可展示推理札记、source 选择和下一步动作。
- `thesis_review.md` 覆盖 strategic fit、diplomatic calendar、resource cost、normative fit、path dependency、resolution specificity、probability floor、countercase、mispricing claim 和 paper trade view。
- 最终报告给出 `absolute_overestimate`，并说明市场把广义台海风险、封锁和灰区升级映射到了更窄的 actual invasion attempt 合约。
- Paper trade view 写明 `buy_no`，按 `NO=0.932` 估算到期 NO 结算名义收益约 `0.068`，简单持有回报约 `7.3%`。
- Probability floor 拆成误判、黑箱信息、突发触发和估计误差，避免机械保留 5% 或 10%。
- 后置市场对比没有把 `p_m` 作为前期 anchor。

Round 2 剩余问题：

- 该 run 只证明 Taiwan case 已达到当前强报告标准，还需要在 Xi 2027、Best Chinese AI Company 或新的中国事件上做泛化验证。
- Bilibili 视频正文/ASR 仍可能失败；本轮把它作为 coverage gap 处理。
- `sources/*/source_card.md` 在重复同类搜索时可能覆盖，完整审计主要依赖 `search_actions/`、`agent_reviews/` 和 `full_trajectory.md`。
- audit 的 `time_perspective` 词项对中文表达仍偏粗糙，虽然总体验收通过。

2026-06-12 用户复审后追加诊断：

- B站视频未生成 `video_report.pdf` / `evidence_card.md` 应视为 harness execution defect，
  不只是普通 coverage gap。`process_resource` 已生成 render contract，但该 run 没有真正启动
  video render worker。
- 最后 run 里高相关 B站视频播放量偏低，原因不应只归因于 agent 筛选；工具层也缺少筛选前候选集留档。
- smoke test 暴露一个实际 bug：fallback search 可能返回 `search.bilibili.com` 或
  `m.bilibili.com/search` 搜索页，旧 `is_video_source_url()` 只按域名判断，会把搜索页误当视频候选。

已处理：

- `is_video_source_url()` 改为只接受真实视频页：B站必须含 `/video/`，YouTube 必须是
  `/watch`、`/shorts/` 或 `/live/`，`b23.tv` 短链保留。
- `search_video_sources` 现在写 `source_visits/*.md/json`，保存筛选前候选集，包括标题、作者、
  URL、播放量、评论数、收藏数、点赞数、发布时间、排序来源、得分、selected/rejected 状态和原因。
- `agent_review` 新增 `evidence_label`、`source_url`、`candidate_set_path`；
  `full_trajectory.md` 使用相对 run workspace 的短路径，不再显示整段 workspace 前缀。
- `docs/china_harness_strong_report_protocol_zh.md` 和 `../China-Specific计划.md` 已写入候选全集与 render 完成性规则。
- smoke 验证已生成 `source_visits/002_2026_b.md`，其中包含 13.8 万播放、9018 播放和 2905 播放的 B站候选。
- `tests/test_china_harness.py` 新增覆盖：搜索页不算视频 URL，视频搜索必须写候选集 artifact。

当前结论：

- Taiwan strong-thesis harness 达到本轮目标。
- 下一步不应继续只调 Taiwan，应优先用同一协议跑 Xi 2027 或新的中国事件，检查是否存在对用户 Taiwan 观点的过拟合。

下一步工程改进：

- 在真实 forecast run 中验证主 agent 按 `subagent_spawn_prompt.md` 启动
  `gpt-5.4-mini` worker，并审计 `video_report.pdf` / `evidence_card.md`
  是否被用于后续 trajectory。
- 改进中文专业媒体和 market_professional 的 allowlist/路由，减少“站点存在但 search tool 召回失败”的情况。
- leadership query template 改为带年份和职务信号，如 `2026 习近平 职务`、`2026 中央政治局`。
- 新增测试覆盖：boilerplate 过滤、context entity filtering、leadership query 不回退为台海 query。

## Architecture Pivot: md-first local Codex harness

```text
date = 2026-06-10
status = implemented as new default launcher
main_agent = codex:gpt-5.4-mini
```

原因：

- API-agent controller 能验证 plumbing，但很难保证真正的长程 agentic search。
- China-specific harness 需要大量文件读写、命令执行、artifact 审计和人工可接手上下文。
- 本地 Codex agent 更适合直接维护 `plan.md`、`trajectory.md`、`claims.md`、`audit.md` 和 report。

处理：

- `scripts/run_china_harness.py` 改为只创建 workspace 和 `task.md` / `tool_manifest.md`。
- 新增 `scripts/china_harness_tool.py`，让本地 agent 通过命令调用 repo tools 并自动落盘。
- 新增 `src/beatodds/agents/local_harness.py`，渲染本地 agent 任务合同和工具清单。
- `LLMChinaAgent` / `ChinaAgentLoopController.run_llm()` 保留为 legacy validation，不作为默认主入口。

验收：

- `task.md` 明确禁止把 DeepSeek/OpenAI API agent 作为 main loop。
- `tool_manifest.md` 明确 Codex 内置工具和 repo tools 的使用边界。
- `china_harness_tool.py` 每次调用都会写入 `search_actions/`、`sources/`、`claims.md` 和 `trajectory.md`。

## Run 8: md-first local-agent smoke run

```text
run_path = workspace/china_forecasts/will_china_invade_taiwan_by_2026/2026/codex_agent_smoke_serial
case = Will China invade Taiwan by end of 2026?
p_m = 0.0625
p_f = 0.0625
confidence = 0.15
passed = pass for architecture, not forecast quality
```

通过项：

- `scripts/run_china_harness.py` 生成 `task.md`、`tool_manifest.md/json` 和 `codex_prompt.md`。
- `task.md` 指定本地 Codex / `gpt-5.4-mini` 为 main agent，并禁止 DeepSeek/OpenAI API agent 作为主循环。
- `scripts/china_harness_tool.py` 串行执行并落盘了 `read_polymarket_context`、`export_source_registry`、`generate_china_queries`、两次 `search_web` 和 `model_baseline_forecast`。
- `sources/official/` 和 `sources/foreign_crosscheck/` 均有 source card。
- `claims.md`、`trajectory.md`、`audit.md`、`forecast_report.md/json/pdf` 均存在。
- `scripts/render_forecast_report_pdf.py` 已把 `forecast_report.md/json` 渲染为 54K PDF，并输出 32K 概率图 `artifacts/report_charts/probability_summary.png`。
- `trajectory.md` 的 Local Agent Review 明确记录了 official evidence -> gap analysis -> foreign crosscheck search -> foreign evidence。

限制：

- 搜索使用 `MockSearchProvider`，因此该 run 只验证 harness architecture。
- 真实预测质量仍需用 Tavily/web evidence 跑一次 local-agent run 后再审。
