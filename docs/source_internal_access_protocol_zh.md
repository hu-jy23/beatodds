# Source 内部访问协议与样例验收

本文档用于修正 China-specific harness 的 source 使用方式。核心原则：Tavily 只能承担候选发现；source 判断必须来自平台内部访问、候选筛选、内容解析、评论区/作者背景检查和 resource-level 评价。

## 1. 总原则

每个 source 都要先建立自己的访问协议。搜索结果标题、网页摘要、Tavily 摘要只算候选线索，不能直接进入 forecast evidence。

一个可进入 forecast 的 source 至少需要生成一份 resource card，回答以下问题：

- `source_entry`: 从哪个平台入口进入，例如 B站搜索页、YouTube 搜索页、微博站内搜索、微信公众号文章页。
- `search_path`: 实际搜索了什么关键词，是否使用站内搜索、API、浏览器、cookie/session、第三方索引。
- `candidate_screening`: 看到了哪些候选，为什么选中或丢弃。
- `content_access`: 正文、字幕、视频、音频、评论区是否真正读取。
- `author_background`: 作者/频道身份、粉丝量、认证状态、历史内容方向、流量激励。
- `claim_extraction`: 该内容提出了哪些可用于预测的判断、事实、机制。
- `evidence_quality`: 是否有论证链、是否引用数据、是否提供反例、是否只是在情绪表达。
- `china_fit`: 对中国环境、平台语境、政策表述、舆论生态的理解是否合理。
- `comment_reaction`: 评论区是否支持、反驳、补充事实，评论量是否足够。
- `novelty`: 是否提供其他 source 没有的观点。
- `bias_risk`: 标题党、政治立场、粉圈立场、流量叙事、海外媒体滤镜等风险。
- `decision`: `accept_for_forecast`、`deep_process_required`、`weak_signal_only`、`reject`。

## 2. Source 访问动作模板

### 2.1 平台入口

agent 必须先进入具体平台的内部环境。不同 source 的入口不同：

- B站：搜索页、视频页、专栏页、评论区、弹幕、UP 主主页。
- YouTube：搜索页、视频页、频道页、评论区、字幕、章节、热度片段。
- 微信公众号：微信搜索、搜狗微信、文章页、账号主页、历史文章。
- 微博：站内搜索、热搜、超话、博主主页、评论区、转发链。
- 知乎：问题页、回答页、作者主页、评论区。
- 小红书/抖音：站内搜索、话题页、视频页、评论区、作者主页。
- 官方/专业媒体：站内搜索、新闻页、专题页、发布时间序列。

### 2.1.1 API 失败后的 browser search fallback

微博、知乎、公众号、雪球、研报库、新闻社数据库这类 source 的访问顺序固定为：

1. 平台内部 API/HTTP 入口。
2. browser search：用真实浏览器渲染平台搜索页，解析候选 DOM。
3. Tavily/domain fallback：只作为外部发现候选。

如果 API/HTTP 返回 403、400、登录墙、反爬、空结果，不能直接跳到 Tavily/domain fallback。必须先尝试 browser search，并在 `source_visits/*.md` 里记录：

- `internal_status`
- `browser_status`
- `browser_raw`
- `fallback_raw`
- browser search URL
- domain fallback query

只有 `browser_status=ok` 且候选的 `access_method=browser_platform_search` 时，才可以称为平台内部搜索。`access_method=tavily_domain_fallback` 只能称为外部发现候选，不能写成“已阅读知乎/微博/公众号内部内容”。

### 2.2 候选筛选

候选筛选不能只按排名。每个平台至少记录：

- 标题是否直接命中事件。
- 发布时间是否符合 prediction time。
- 作者是否长期覆盖该领域。
- 播放/阅读/评论是否足够支持社区反应分析。
- 内容形式是否可解析：文字优先，视频需要字幕或 ASR。
- 是否存在明显标题党或立场先行。

### 2.3 内容解析

文字内容要直接阅读正文。视频内容按顺序处理：

- 先读元数据：标题、简介、发布时间、作者、播放量、评论量、标签。
- 再查字幕：官方字幕、自动字幕、弹幕文本。
- 没有字幕时使用视频 render/ASR skill。
- 对长视频先看章节、热度片段、评论区高赞提示，再决定重点处理区间。
- 输出 resource report，禁止只根据标题写 evidence。

### 2.4 评价标准

每个 text/video 都要评价：

- 客观性：是否区分事实、推断、情绪。
- 独特性：是否有其他渠道没有的观察。
- 论证支持：是否给出数据、时间线、机制、反例。
- 中国环境适配：是否理解中文互联网语境、政策语境、行业规则。
- 作者背景：专业性、利益相关、历史准确性。
- 评论区反应：是否形成反驳、补充、共识或争议。
- 可预测性：是否能改变 forecast 的变量，而非泛泛背景。

## 3. B站 dry-run：体育样例

### 3.1 目标

样例问题：近期王楚钦比赛状态如何，中文体育内容能否提供比普通搜索更细的状态判断。

搜索入口：

- B站搜索页：`https://search.bilibili.com/all?keyword=王楚钦%20近期%20比赛%20赛后%20分析`
- B站搜索 API：`https://api.bilibili.com/x/web-interface/search/type`

### 3.2 访问发现

直接抓 B站搜索网页只能拿到前端 shell，不能稳定拿到结果。裸调搜索 API 返回过 412。实际可用路径是：

1. 先访问 B站搜索页，拿到基础 cookie/session。
2. 带 `User-Agent`、`Referer` 和 session 调搜索 API。
3. 用搜索 API 返回 `title / author / bvid / play / review / duration` 做候选筛选。
4. 对选中 BVID 调 `x/web-interface/view` 获取视频元数据。
5. 对 `aid` 调 `x/v2/reply` 获取评论区样本。

这说明 B站不能按普通 website search 处理。它需要平台内 session 预热和专用 adapter。

### 3.3 候选结果

搜索 `王楚钦 近期 比赛 赛后 分析` 返回的前几项包括：

- `BV1W7Ev6WEjW`：标题涉及 `20260611《体坛零距离》中国男乒·必胜的信念`，播放约 3659，评论 24。
- `BV1am1oYTE3q`：标题是事业运势类，播放约 7473，评论 17。该候选与竞技分析弱相关，应该丢弃。
- `BV1DTCCBUEU1`：标题涉及赛后采访，播放约 6365，评论 75。该候选更接近 primary/interview。
- `BV1X8d3BoEEN`：标题是解说评价类，播放约 16708，评论 7。可以作为弱观点源。
- `BV1muEv6ZEzd`：标题涉及男队队长回应，播放约 476，评论 4。互动不足。

### 3.4 具体视频检查

检查视频：`https://www.bilibili.com/video/BV183DWYnEfd`

元数据：

- 标题：`【乒乓与生活11.04】法兰克福王楚钦赛后总结 比赛方式的变化 练了核心力量`
- UP 主：`是饺不是脚야`
- 发布时间：2024-11-04
- 时长：约 607 秒
- 播放：6452
- 点赞：122
- 评论：20
- 收藏：47
- 简介：只有 `-`
- 字幕：未发现可用字幕

评论区样本：

- 高赞评论里有粉圈防御型表达，说明评论区存在立场维护。
- 有评论讨论打法变化，认为打法更聪明、更强调落点和选择。
- 有评论提到对肩膀消耗和健康完赛的关注。

### 3.5 评价

该视频标题高度相关，适合分析运动员状态和打法变化。当前只读到元数据和评论区，未读取视频正文，所以不能直接进入 forecast evidence。

resource decision：

- `deep_process_required`
- 原因：无字幕、简介为空，需要 ASR 或视频 render skill 生成内容报告。
- 可用弱信号：评论区提示“打法变化”“健康/肩膀消耗”是值得进一步验证的变量。
- 风险：UP 主身份专业性不明，播放量中等，评论区粉圈噪音明显。

## 4. B站 dry-run：娱乐样例

### 4.1 目标

样例问题：《哪吒2》的票房和口碑是否存在后验反噬，中文视频平台能否提供真实观众反馈。

搜索入口：

- B站搜索页：`https://search.bilibili.com/all?keyword=哪吒2%20票房%20口碑%20影评%20分析`

### 4.2 候选结果

搜索 `哪吒2 票房 口碑 影评 分析` 返回的前几项包括：

- `BV1r3NfeME6q`：票房可视化，播放约 14163，评论 18，偏数据展示。
- `BV1sAGxz8EAd`：标题涉及下映后口碑反噬，播放约 1061923，评论约 10041，互动强。
- `BV1r1AheWEyt`：票房每日统计，播放约 7512，评论 7，偏数据更新。
- `BV1bvoNYCE5s`：爆票房原因，播放约 9364，评论 43。
- `BV1aPNGe4EaM`：世界票房排名，播放约 71894，评论 322，偏数据盘点。

### 4.3 具体视频检查

检查视频：`https://www.bilibili.com/video/BV1sAGxz8EAd`

元数据：

- 标题：`《哪吒2》下映后！口碑反噬这一块！`
- UP 主：`是这样的不知道`
- 时长：约 428 秒
- 播放：约 106.2 万
- 点赞：约 5.98 万
- 评论：约 1.00 万
- 弹幕：3809
- 收藏：7557
- 简介：一句强烈负面评价

评论区样本：

- 高赞评论集中在剧情动机、观影后回想、网络夸赞与个人感受落差。
- 评论区争议度高，说明该视频适合作为观众反向反馈源。
- 评论不是单纯复读标题，包含对剧情和口碑机制的补充。

### 4.4 评价

该视频具备高互动、高争议、高相关性，适合做娱乐事件中的社交反馈 source。它不适合作为票房事实源，适合作为口碑变化和观众情绪 source。

resource decision：

- `deep_process_required`
- 原因：需要读取视频正文、弹幕/评论更多样本，拆分“剧情批评”“营销反感”“国漫叙事疲劳”等机制。
- 可用弱信号：存在大规模口碑反向讨论。
- 风险：标题和简介情绪强，UP 主可能以争议流量为目标。

## 5. YouTube dry-run：娱乐样例

### 5.1 目标

检查 YouTube 中文内容对《哪吒2》票房和口碑分析的可用性。

搜索入口：

- YouTube 搜索页：`https://www.youtube.com/results?search_query=哪吒2%20票房%20口碑%20影评%20分析`
- 命令行候选检索：`yt-dlp ytsearch5:哪吒2 票房 口碑 影评 分析`

### 5.2 候选结果

候选包括：

- `https://www.youtube.com/watch?v=dylCF3lWNCE`：频道 `Leonard`，标题政治化，播放约 105.9 万。适合作为立场型 cross-check，不适合作为第一信息源。
- `https://www.youtube.com/watch?v=3jW3k9maDAo`：频道 `大聪看电影`，认证频道，粉丝约 65.6 万，播放约 63.1 万，时长约 27 分钟。适合深读。
- `https://www.youtube.com/watch?v=16B9r0uzWCI`：频道 `老范讲故事`，长直播切片，播放约 9170。需要判断内容密度。
- 其他候选播放和相关性较弱。

### 5.3 具体视频检查

检查视频：`https://www.youtube.com/watch?v=3jW3k9maDAo`

元数据：

- 标题：`《哪吒2》萬字解析！憑什麼能衝百億票房！腦洞解析第三部！`
- 频道：`大聪看电影`
- 认证：是
- 粉丝：约 65.6 万
- 发布时间：2025-02-07
- 时长：约 1671 秒
- 播放：约 63.1 万
- 点赞：约 8609
- 评论：约 1000
- 标签包含 `哪吒2`、`国漫`、`中国动画`、`中国电影票房第一`、`2025春节档`
- 字幕：未发现可用官方字幕或自动字幕
- 热度片段：存在多段高热度区间，可用于优先抽帧/转写

### 5.4 评价

该视频是高质量候选：频道长期做电影解析，认证和粉丝量支持作者背景，长视频结构适合抽取论证链。

resource decision：

- `deep_process_required`
- 原因：没有字幕，需要 ASR 或视频 render skill；评论抓取在当前环境不稳定。
- 可用弱信号：长视频、认证频道、高播放和明确解析主题支持进一步处理。
- 风险：YouTube 中文内容可能受海外中文舆论结构影响，需要和 B站/中文票房数据/国内评论区交叉。

## 6. YouTube dry-run：体育样例

搜索入口：

- YouTube 搜索页：`https://www.youtube.com/results?search_query=王楚钦%20近期%20比赛%20赛后%20分析`
- 命令行候选检索：`yt-dlp ytsearch5:王楚钦 近期 比赛 赛后 分析`

候选特点：

- 搜索结果中有多个低播放、标题情绪化或时间不匹配的视频。
- `China Today 中国头条` 的赛后采访更接近 primary source，但发布时间较早，不适合近期预测。
- 一些频道名和内容类型与体育专业分析关系弱，应该丢弃。

评价：

- YouTube 对该体育样例的中文结果质量弱于 B站。
- 适合补充海外中文视角，不适合作为首要 source。
- 当前没有找到可直接进入 forecast 的高质量体育视频，需要扩大关键词和平台。

## 7. 当前工具缺口

本次 dry-run 暴露出以下缺口：

- B站搜索需要专用 adapter：先访问搜索页预热 session，再调搜索 API。
- B站视频页元数据和评论区可用；搜索 API 可以返回候选；视频正文需要字幕或 ASR。
- YouTube 搜索和元数据可用；评论抓取不稳定，当前环境缺 JavaScript runtime。
- `ffmpeg` 当前不可用，视频 render/pdf skill 不能稳定跑完整。
- 样例视频多无字幕，需要 Whisper/ASR 路径。
- 评论区目前只抓了首页/高赞样本，还没有做分页、多排序、代表性采样。

## 8. 给 gpt-5.4-mini 的 harness 改造要求

### 8.1 入口

agent 收到 `Q + resolution rule` 后，先创建 event workspace：

```text
workspace/<event_slug>/
```

workspace 内至少包含：

- `run_prompt.md`
- `trajectory.md`
- `source_profiles/`
- `source_visits/`
- `resources/`
- `forecast_report.md`
- `final_report.pdf`

### 8.2 Source profile

每个平台先有一份 profile：

- `source_profiles/bilibili.md`
- `source_profiles/youtube.md`
- `source_profiles/weibo.md`
- `source_profiles/wechat.md`

profile 要写清：

- 平台入口。
- 搜索方法。
- 可用工具。
- 失败模式。
- 是否需要 cookie/session。
- 是否需要视频 render/ASR。
- 评论区如何采样。
- 什么情况下候选可以升级为 resource。

### 8.3 Source visit

每次搜索写一份 visit：

```text
source_visits/<step_id>_<platform>_<query>.md
```

visit 要记录：

- 搜索意图。
- 搜索关键词。
- 候选列表。
- 筛选理由。
- 下一步要深读的 resource。
- 本轮没有解决的问题。

### 8.4 Resource report

每个被深读的内容写一份：

```text
resources/<platform>_<resource_id>.md
```

视频 resource 必须包含：

- 元数据。
- 字幕/ASR/视频处理状态。
- 评论区摘要。
- 作者背景。
- claims。
- 质量评价。
- 对 forecast variable 的影响。

### 8.5 Loop 规则

agent 每一轮必须按这个结构行动：

1. 读取当前 `trajectory.md` 和已有 resources。
2. 写本轮 search intent。
3. 进入具体 source 内部搜索。
4. 生成 source visit。
5. 选择需要深读的 resource。
6. 对 resource 做内容解析。
7. 写 agent review：本轮新 evidence 改变了什么。
8. 决定下一轮 search，或进入 forecast report。

关键约束：

- 搜索结果不能直接进入 forecast。
- 没有正文/字幕/ASR的 video 只能作为候选或弱信号。
- 外部/海外中文内容用于 cross-check，不能过早主导中文事件判断。
- 市场价格 `p_m` 后置读取，避免早期锚定。
- 对中国相关事件，优先使用中文互联网内部内容，尤其是社交平台、专业博主、行业人士和评论区。

## 9. 下一步实现建议

优先做工具层：

- `bilibili_search_adapter`: session 预热、搜索 API、候选结构化。
- `bilibili_video_adapter`: 元数据、评论区分页、弹幕、字幕检查。
- `youtube_search_adapter`: `yt-dlp ytsearch` 结构化候选。
- `youtube_video_adapter`: 元数据、字幕检查、热度片段、评论抓取修复。
- `resource_report_template`: 统一 text/video resource card。

然后做 agent 约束：

- 在 `run_prompt.md` 中明确 source visit 和 resource report 是必填产物。
- 在 validation 中检查每个 forecast claim 是否能追溯到 resource report。
- 若只有 title/snippet，validation 必须标记为失败。
- 若视频未完成正文解析，validation 必须标记为 `deep_process_required`。
