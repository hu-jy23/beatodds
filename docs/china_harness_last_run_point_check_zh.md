# 最后一次 China-specific Harness Run 要点对照

状态日期：2026-06-13

最后一次 run：

```text
case = Will China invade Taiwan by end of 2026?
workspace = workspace/will_china_invade_taiwan_by/2026/gpt-5.4-rerun-20260613
model = codex:gpt-5.4
p_m = 0.068
p_evidence = 0.025
p_f = 0.030
p_m_delta = -0.038
confidence = 0.67
calibration_status = uncalibrated
mispricing_verdict = material_overestimate
paper_trade = buy_no_small
audit_score = 18
audit_status = pass
```

核心产物：

```text
forecast_report.pdf
forecast_report.md
forecast_report.json
full_trajectory.md
thesis_review.md
audit.md
source_visits/*.md
artifacts/resources/*/video_report.pdf
artifacts/resources/*/evidence_card.md
```

## 本轮已修复

| 要点 | 本轮处理 | 状态 |
|---|---|---|
| workspace 路径过长 | 旧路径已迁移到 `workspace/will_china_invade_taiwan_by/2026`，新 run 也使用该短路径。 | 已处理 |
| launcher 不能指定短 slug | `run_china_harness.py` 支持 `--event-slug` 和 `--market-slug`，测试覆盖短路径。 | 已处理 |
| 候选视频筛选前集合不可见 | 生成 `source_visits/001_2026.md` 和 `source_visits/002_video_search.md`，包含标题、作者、平台、播放量、评论量、排序、分数、选择/拒绝理由。 | 已处理 |
| evidence track 路径太长 | 新 run 的 `full_trajectory.md` 使用 run 内短路径，并写 `evidence_label`。 | 已处理 |
| B站视频只停留在链接/简介 | 三条 B站视频均生成 `video_report.pdf`、`video_parse_report.md`、`evidence_card.md`、`claims.jsonl`。 | 部分处理 |
| p_m 前置锚定 | `thesis_review.md` 写明先做 evidence-first 判断，再读取 Polymarket。 | 已处理 |
| 轨迹附录太示意 | `full_trajectory.md` 620 行，包含实际 query、候选集、材料阅读、降权理由和下一步动作。 | 已处理 |
| future exploration 不足 | `audit.md` 明确写 `future_change_mechanisms`、预测 source 尝试、低信号拒绝和改变预测的信息。 | 已处理 |
| PDF 可读性 | 已生成 `forecast_report.pdf`，并带概率图表；完整 trajectory 已直接嵌入 PDF 附录，不再只链接 `full_trajectory.md`。 | 已处理 |

## 本轮报告质量

报告主线比上一轮更稳健：

- 先从中文视频和中文专业讨论入手，再补官方和 foreign cross-check。
- 明确区分 `台海高压升级`、`封锁/隔离`、`实际入侵`。
- 解释市场错价机制：市场把 generic Taiwan risk 定价成 actual invasion risk。
- 写出失效条件：异常渡海集结、有限夺占准备、官方叙事转向短期行动窗口等。
- 给出 probability floor decomposition，而不是机械保留 5% 或 10%。

本轮没有给出上一轮的 `absolute_overestimate / p_f=0.008`。它给出 `material_overestimate / p_f=0.030`，理由是 resolution 对“尝试夺占台湾控制领土”的宽口径保留了有限夺占尾部风险。

## 视频处理状态

本轮处理了三条 B站视频：

```text
BV1WwVz6HEB8  别被美军兵推骗了
BV1RuT8zVEJW  台海登陆革命
BV1maUDBVEux  登陆四条件短评
```

每条都有资源包：

```text
video_report.pdf
video_parse_report.md
evidence_card.md
claims.jsonl
download_log.md
frame_index.md
```

视频处理仍有缺陷：

- 没有稳定获得逐字字幕或 ASR。
- 两条视频主要是视觉抽帧级证据。
- 高热长视频 `BV1WwVz6HEB8` 仍被降权为受限背景材料。

这次报告正确地没有把未转写视频当作完整正文证据。

## 主要缺口

- B站视频 render 已能产出 PDF，但 ASR/字幕链路仍不稳定。
- `market_professional` 搜索仍容易被 YouTube 视频结果劫持，需要更窄的 source 访问方式。
- 报告质量提高，但强 thesis 没达到用户期望的 `0.068 是 absolute overestimate`。
- 新结论更保守，是否接受取决于后续是否能用更强中文机制证据压低有限夺占尾部风险。
- PDF 已改为全文内嵌 `full_trajectory.md` 附录；audit 现在会检查 `forecast_report.md` 中是否真的包含 `Evidence Review`、`实际阅读材料`、`可展示推理札记` 等实质轨迹内容。

## 验证结果

```text
uvx ruff check scripts/run_china_harness.py src/beatodds/agents tests/test_china_harness.py
status = pass

uv run pytest -q tests/test_china_harness.py
status = 30 passed, 1 warning

uv run python scripts/audit_china_harness_run.py --workspace workspace/will_china_invade_taiwan_by/2026/gpt-5.4-rerun-20260613 --json
status = pass
rubric_total = 18
```
