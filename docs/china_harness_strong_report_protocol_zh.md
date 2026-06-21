# China-specific Harness 强报告与全轨迹协议

状态日期：2026-06-12

本文记录用户对 China-specific forecast harness 的新要求，并把它转成可执行的
工程协议。目标是提升最终报告的判断质量和轨迹留档质量。

## 1. 用户原话

> 我不这样认为。1、台海问题是中美关系中的一颗棋子，牵一发而动全身，一旦武力涉入，就给了西方世界搅动中国政治的抓手，于国际舆论有大不利。2、中美关系成为全球第一大关系，中美聚焦贸易战科技战，同时特朗普访华，习近平访美预定，外交缓和，不会在台海问题上浪费时间和资源。3、武力不符合中国的主流价值观，也不符合中国在国际上提倡的主流思想。4、台海问题有来许久，不差这一年半载，虽然习近平主席希望在任期内解决这件事，但也是平滑地，而不会突然采取这样一种，在历史轨迹上留下一个spike的方式。因此，可能性为0. 市场价0.068是绝对高估。因此存在半年6.8%的收益。
>
> 这是我要看到的报告的最终形态。我们看当前报告：“p_evidence
> • 0.09
> 我在不看市场价格前的证据判断约为 9%。理由是：
> • 当前最强公开证据不支持一个已经进入倒计时的 2026 年底前全面入侵计划。
> • 官方口径更像“条件触发式威慑”，而不是“既定日期表”。
> • 外部主流情报评估也更支持持续胁迫、灰区施压、封锁威胁或其他非入侵动作比直接入侵更可
> 能。
> • 但尾部风险仍然存在，因为一旦北京认定“台独”越线，或发生重大危机误判，升级可能非常快。
> p_f
> • 0.08
> 最终概率略低于 evidence-first 草案，因为在后置看市场后，我认为市场对尾部风险的折价方向是合
> 理的；同时现有中文主线证据并没有提供足够理由把概率维持在明显高于市场的位置。”
> 对于一个刚做好的harness来说，还可以。但是和我上面那段话比起来，就差得远了。你仔细思考，如何让agent最后写出我那样的结论。
>
> 最后，我要的轨迹附录不是“ Think 1 -> 先找中文视频候选 -> Evidence 1: 找到 6 条相关视频，但全是候选级信息 -> Next
> 2: 对最高相关 B 站视频做资源处理”这种示意性质的，而是具体的，agent每次读了什么，做了什么thinking，让agent全程留档，最后写在report里。可以长达数十页，并没有关系。相当于，我要录屏agent的眼睛，把他看到的所有东西，和他的thinking过程，全部复制留档，懂了吗？
>
> 上面这两个任务，具体做法我没有明确，但我传递了最核心的思想。写成md，先记录我的原话，再按你的规范完成md。因为没有明确，所以需要多次的探索和尝试，这是一个longterm，肯定会多次试错的任务，你仍然是多轮不断迭代，每一次新想法/优化落地，都要完整跑一个run，然后检查过程和artifacts，找出问题，如此循环。我暂且不规定轮次，但至少2轮，两个任务分别。如果你能理解我前面台海问题那段话好在哪里，那么你可以按照你的标准，运行更多轮次，直到最后报告中的分析足够优秀。我要求：agent能发现0.068是一个absolutely的overestimate。但注意，你不要reward hack，这个结果应该通过agent广泛探索-> 熟悉中国国情和事情->最后缜密推理得到。只hack答案是没用的，因为我还会test其他时间，包括Xi 的2027是否连任等等。
> 现在你已经直到了目标边界判定要求等等，到底是写一个goal运行还是以其他方式，你来做，开始。

## 2. 提炼后的目标

### 2.1 报告质量目标

最终报告应能形成有判断力的 forecast thesis。合格报告不只列证据和概率，还要说明：

- 这个 resolution 在中国语境、国家战略、外交日程、制度约束和历史路径中是否顺。
- 相关行动是否符合参与者的主流利益、主流价值、长期叙事和现实机会成本。
- 市场价格错在哪里，若存在错价，错价来自哪类外部叙事、尾部风险误读或 resolution 边界误读。
- 低概率结论可以非常低，甚至接近 0；前提是 agent 证明该 outcome 缺少现实机制，而非只说没有看到证据。
- 每个强结论都必须给出最强反方论点，并说明为什么反方不足以支撑市场价格。

### 2.2 全轨迹目标

最终报告附录应接近“录屏 agent 的眼睛”。报告可以很长。合格附录应包含：

- 每一步 agent 想解决的信息缺口。
- 每一步实际读到的文件、搜索结果、source card、视频报告或网页摘要。
- agent 对该材料的可展示 reasoning memo。
- 哪些材料被采信、降权、拒绝，原因是什么。
- 为什么下一步选择某个 source/tool/query。
- 形成最终概率时，哪些 evidence 进入主论证，哪些只进入背景，哪些作为 coverage gap。

这里保存的是可展示、可审计的 reasoning，不保存模型 hidden chain-of-thought。

## 3. 报告协议

`forecast_report.md` 必须新增或强化以下 section：

- `结论先行`: 用 3 到 8 句话给出最终 thesis、p_f、p_m_delta、是否存在市场错价。
- `Mispricing Verdict`: 明确写 `no_edge`、`mild_mispricing`、
  `material_overestimate`、`absolute_overestimate`、`material_underestimate`
  或 `absolute_underestimate`。如果使用 `absolute_*`，必须证明市场价格与
  reality mechanism 的错配是结构性的，而不是只因为模型概率低。
- `Paper Trade View`: 如果存在明显错价，说明交易方向、市场隐含收益和主要失效条件。
- `核心论证链`: 用连续因果链解释判断，不允许只堆 bullet evidence。
- `中国语境与战略一致性`: 检查事件是否符合中国长期战略、外交节奏、国内政治逻辑、国际舆论成本和主流价值。
- `Resolution 边界`: 说明哪些高张力动作仍不满足结算条件。
- `市场错判在哪里`: 若 p_f 与 p_m 差异明显，说明市场为什么高估或低估。
- `尾部风险处理`: 说明 tail risk 的具体触发机制、缺失机制、概率上限，不允许用“尾部风险存在”直接抬高 p_f。
- `Probability Floor Decomposition`: 将低概率 floor 拆成误判/突发/黑箱/触发机制等来源，
  说明为什么不是机械保留 5% 或 10%，也为什么不是无条件写 0。
- `最强反方论点`: 写出支持相反方向的最强材料，并给出驳回或降权理由。
- `什么会推翻本报告`: 列出会显著改变判断的新信息。
- `完整轨迹附录`: 全文嵌入 `full_trajectory.md` 的实质内容，保留多轮 evidence review 的详细内容；不能只链接或提示读者另开 md。
- `Source 覆盖` 和所有视频相关段落：视频必须用标题称呼，例如 `《别被美军的兵推给骗了》（BV1...）`。BV 号、YouTube id、URL 只能作为辅助标识，不能单独代替视频名。

## 4. 强结论形成规则

Agent 每次形成概率前必须完成一个 `thesis_review`：

- `strategic_fit`: outcome 与长期国家战略是否一致。
- `diplomatic_calendar`: 未来窗口内是否有外交缓和、会谈、选举、国际会议、贸易/科技谈判等关键日程。
- `resource_cost`: outcome 是否消耗大量政治、军事、经济、外交资源。
- `normative_fit`: outcome 是否符合中国公开主流叙事、价值观和国际倡议。
- `path_dependency`: 历史轨迹是否支持突然 spike，还是更支持平滑推进。
- `resolution_specificity`: 市场 resolution 要求的动作是否比一般紧张局势更强。
- `base_rate_and_floor`: 给出概率底线，说明为什么不是机械留 5% 或 10%。
- `strongest_countercase`: 反方最强论点是什么。
- `mispricing_claim`: 市场是否错价，错价幅度是否足够支撑交易判断。
- `conviction_scale`: 结论强度。至少区分：
  - `low_conviction`: evidence sparse，不能强交易。
  - `directional`: 方向有优势，但错价幅度不大。
  - `material`: 方向与错价都明显。
  - `absolute`: 市场价格与 resolution / 现实机制明显错配，且反方不能解释价格。
- `paper_trade_view`: 若是 prediction market，说明买 YES / 买 NO / 不交易，
  以及近似收益、最大损失和主要失效条件。

该 review 适用于所有中国相关事件。Taiwan case 的“0.068 绝对高估”必须由这些检查自然推出，不能写死。

## 5. 轨迹留档协议

每次 `agent_review` 应保存以下字段：

- `evidence_path`
- `evidence_label`: 人类可读材料名，例如 `B站视频：《标题》（BVxxxx）`、
  `国务院报告：标题 日期`、`视频候选集：query`。
- `Source：...`: 每个 Evidence Review 开头必须先给人类可读 source，例如
  `Source：B站《台湾问题分析和演化》（BVxxxx）`。
- `review_path`、`evidence_path`、`candidate_set_path`: 必须使用当前 agent run
  目录内相对短路径，例如 `./agent_reviews/...`、`./source_visits/...`，不得写
  整段 `workspace/...` 或绝对路径。
- `candidate_set_path`: 若证据来自视频/社媒筛选，必须指向筛选前候选集。
  `full_trajectory.md` 和报告附录只保留这个入口，不展开完整候选表；完整标题、
  互动指标和筛选理由留在 `source_visits/*.md` 原文。
- `raw_materials_seen`: agent 实际读到哪些文件、搜索结果、标题、摘要、视频报告、评论样本。
- `source_excerpt_or_summary`: 对每个关键材料的可展示摘录或压缩摘要。
- `visible_reasoning_memo`: agent 对这些材料的可展示推理札记。
- `source_selection_notes`: 为什么选择此 source/query/tool。
- `assessment`: 该材料如何影响 YES/NO/relevance。
- `information_gap`
- `next_search_decision`
- `rejected_or_downweighted`: 被拒绝或降权的材料与原因。
- `stop_or_continue`
- `confidence_note`

`full_trajectory.md` 应自动或手工汇总这些 review，最终报告附录可直接引用。

## 5.1 视频候选集留档规则

B站 / YouTube 这类高噪声 source 不能只保存最终选中的视频。每次平台内搜索必须保存筛选前候选集，至少包括：

- query 和平台。
- 排序方式，例如 B站 `totalrank`、`click`、`stow`、`pubdate`。
- 视频标题、作者、URL。
- 后续报告引用视频时，标题是主标识，BV 号或 YouTube id 只放在标题后括号里。
- 播放量、评论数、收藏数、点赞数；缺失时留空。
- 发布时间、时长。
- 初始排名、工具得分。
- 最终状态：`selected`、`rejected_quality`、`rejected_category`、`not_selected_rank_limit` 等。
- 选择或拒绝理由。

主 agent 做视频筛选时必须说明它是在什么 candidate set 中筛选，为什么低播放视频仍值得处理，或为什么高播放视频被降权。

视频标题、简介、metadata、评论区只能作为候选或弱信号。只有字幕、ASR、人工深读或 `video_report.pdf` / `evidence_card.md` 产物完成后，视频正文才能作为完整 evidence。

若 `process_resource` 只生成 render contract，但没有生成 `video_report.pdf` 和 `evidence_card.md`，应标为 harness execution defect 或 coverage gap，不能把该视频当作已读正文。

## 6. 验收标准

每轮 run 后至少检查：

- 报告是否形成清晰 thesis。
- 是否回答“市场错判在哪里”。
- 是否给出 mispricing verdict 和 paper trade view。
- 如果声称 absolute overestimate / underestimate，是否有机制性论证支撑。
- 是否拆解 probability floor，而不是机械留尾部风险。
- 是否将 resolution 边界与普通紧张局势分开。
- 是否允许强低概率结论，并给出机制性理由。
- 是否写出最强反方及驳回。
- 是否包含完整轨迹附录，而非摘要式轨迹。
- `agent_reviews/` 是否足够详细，能复盘 agent 每步读了什么、想了什么、为什么继续。
- 是否避免把用户期待的 Taiwan 答案硬编码进通用 harness。

## 7. 迭代方式

这是一项 longterm harness 任务。每次改动后：

1. 完整跑一个 forecast run。
2. 审查 `forecast_report.md/pdf`、`forecast_report.json`、`trajectory.md`、
   `full_trajectory.md`、`agent_reviews/`、`audit.md`。
3. 判断报告是否接近强 thesis 形态。
4. 判断轨迹是否足够接近“录屏”。
5. 把问题写入 audit log。
6. 修改 harness prompt、agent_review schema、report protocol 或 audit rubric。
7. 进入下一轮。
