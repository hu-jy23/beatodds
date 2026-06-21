# China-specific Harness 测试与审计计划

状态日期：2026-06-10

## 核心验收对象

当前要测试的不是单纯“能否跑完”，而是 artifacts 是否证明 agent 在执行 agentic search：

```text
Evidence k -> analysis k -> Search k+1 -> Evidence k+1
```

一次合格 run 应该能从 `trajectory.md` 中看出：

- agent 当前掌握了哪些 evidence。
- agent 如何分析这些 evidence。
- agent 认为还缺什么信息。
- agent 为什么选择下一次 search。
- agent 为什么停止搜索并生成 report。

## 必看 artifacts

每次 run 后人工检查：

```text
trajectory.md
trajectory.jsonl
generated_queries.md
search_actions/*.md
sources/*/*.md
claims.md
forecast_report.md
forecast_report.json
forecast_report.pdf
```

## 通过标准

### 1. Loop 行为

- 不是固定流水线。
- 至少一次 search 之后，下一轮 decision 的 `analysis` 引用已有 evidence 或 claims。
- 下一次 search 的 query/source category 能对应上一轮 evidence 的缺口。
- report 之前能看到继续或停止搜索的理由。

### 2. Source 使用

- 使用了至少两个不同信息角度，除非 agent 明确解释为什么一个角度已足够。
- `source_category` 与 query 大体匹配。
- Polymarket p_m 只作为 baseline，不作为事实 evidence。

### 3. Artifact 质量

- `search_actions/*.md` 有 query、tool、status、result 摘要。
- `sources/*/*.md` 有 title、url、source、summary。
- `claims.md` 至少包含从 evidence 压缩出来的 claim。
- `forecast_report.md` 引用 evidence，而不是凭空判断。
- `forecast_report.pdf` 存在，并包含从 `forecast_report.json` 生成的概率/置信度图表。

### 4. 概率输出

- `forecast_report.json` 包含 `p_f`、`confidence`、`p_m_delta`、`calibration_status`。
- `forecast_report.pdf` 是最终展示报告；`md/json` 是其源文件和机器接口。
- 如果 `p_f` 靠近 `p_m`，报告要解释是 evidence 中性、稀疏，还是 baseline 约束。
- 如果 `p_f` 偏离 `p_m`，报告要解释哪条 evidence 推动偏离。

## 失败标准

出现以下情况应视为 harness 不合格：

- trajectory 只是工具调用列表。
- agent 搜索后没有阅读 evidence 就继续机械搜索。
- agent report 中引用不存在的 evidence。
- search result 只保存了数量，没有保存可读摘要。
- source card 无法追溯到 query。
- LLM 输出 report 过早，缺少停止理由。

## 第一批真实测试 case

### Case 1: geopolitics_military

```text
event_title = Will China invade Taiwan by 2026?
market = Will China invade Taiwan by end of 2026?
p_m = 0.0625
```

目标：检查 agent 是否围绕军事、官方表态、外媒交叉验证做 iterative search。

### Case 2: elite_politics

```text
event_title = Xi Jinping leadership before 2027
market = Xi Jinping out before 2027?
p_m = 0.0695
```

目标：检查 agent 是否能处理低透明度政治事件，并识别 rumor / signal 风险。

### Case 3: diplomacy_trade / industrial_policy

```text
event_title = China rare earth export controls
market = Will China announce new rare earth export controls by July 31?
p_m = 0.35
```

目标：检查 agent 是否使用官方 source、商务部/海关、外部产业分析交叉验证。

## 推荐命令

从 Q + resolution bootstrap md-first local-agent workspace：

```bash
uv run scripts/run_china_harness.py \
  --event-title "<event>" \
  --market "<market>" \
  --condition-id "<id>" \
  --p-m <probability> \
  --resolution "<resolution>"
```

下面是 agent 可用的 repo tool 能力示例，不是测试执行流水线：

```bash
uv run scripts/china_harness_tool.py --workspace "<run_dir>" read_polymarket_context
uv run scripts/china_harness_tool.py --workspace "<run_dir>" export_source_registry
uv run scripts/china_harness_tool.py --workspace "<run_dir>" generate_china_queries \
  --query "<market or evidence-driven query>"
uv run scripts/china_harness_tool.py --workspace "<run_dir>" search_web \
  --query "<query>" \
  --source-category "<source_category>" \
  --max-results 5
```

旧 DeepSeek/API controller 路径只作为 legacy validation，不作为主 harness 验收对象。

主 harness 验收边界：

- `gpt-5.4-mini` 读取 `task.md` 后自己维护 workspace。
- 每次 search 前能说明 information gap 和 source 选择理由。
- 每次 evidence 后有 `agent_reviews/`。
- `trajectory.md` 证明 `Evidence k -> analysis k -> Search k+1 -> Evidence k+1`。
- `forecast_report.md/json/pdf` 由 agent run 生成，不能由外部 controller 拼接。

## 审计记录模板

```text
run_path:
case:
passed:

loop_evidence_chain:
- Evidence 1:
- Analysis 1:
- Search 2:
- Evidence 2:

source_quality:
- official:
- foreign_crosscheck:
- generic:

report_quality:
- p_m:
- p_f:
- confidence:
- evidence-backed?:

issues:
-

next_fix:
-
```
