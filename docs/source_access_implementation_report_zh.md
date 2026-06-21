# Source Access 实现与三轮验证报告

## 1. 本轮结论

本轮把视频 / 社交 source 从 Tavily 摘要升级为平台内部访问工具。

已完成：

- 安装并验证 `deno`，用于 `yt-dlp` 的 YouTube JS runtime。
- 安装 Linux 静态 `ffmpeg/ffprobe` 到用户目录，替代 Windows `ffmpeg.exe`。
- 安装并验证 `openai-whisper` 临时环境。
- 新增 B站 / YouTube 平台访问模块。
- `search_video_sources` 优先使用平台内部 adapter。
- `process_resource` 从 stub 升级为真实 source card 生成器。
- 做了三轮 live source 测试，并根据结果连续优化 assessment。
- 新增本地 Codex skill：`/home/hjy/.codex/skills/chinese-video-source-research/`。

关键边界：

- 搜索结果仍只是候选。
- 已能读取平台元数据、评论样本、字幕状态。
- 无字幕视频会标记 `requires_asr=true`。
- YouTube 30 秒片段已验证可用 Whisper ASR。
- B站视频下载仍会遇到 412，需要 cookies / 浏览器下载 adapter；B站搜索、元数据、评论已可用。

## 2. 代码入口

核心实现：

- `src/beatodds/agents/video_source_access.py`
- `src/beatodds/agents/tool_registry.py`
- `src/beatodds/agents/access_tools.py`
- `src/beatodds/agents/source_quality.py`
- `scripts/china_harness_tool.py`

验证 workspace：

```text
workspace/source_access_validation_for_chinese_platforms/can_the_harness_access_and_assess_chinese_video_social_sources/source_access_validation/
```

## 3. B站实现方式

### 3.1 搜索

实现函数：

```text
search_bilibili_videos()
```

访问流程：

1. 先访问 `https://search.bilibili.com/all?keyword=...` 预热 session。
2. 调用 `https://api.bilibili.com/x/web-interface/search/type`。
3. 使用 `search_type=video`。
4. 合并多个排序：`totalrank / click / stow / pubdate`。
5. 按 `bvid` 去重。
6. 生成 `SearchResult`，保留 `bvid / author / play_count / comment_count / duration / pubdate / tag / search_order`。

这样做的原因：

- 直接抓 B站搜索页只能拿到前端 shell。
- 裸调 API 可能 412，先访问搜索页拿 cookie 后更稳定。
- 单一排序会漏掉高互动旧内容；多 order 合并能找到更好的社交反馈 source。

### 3.2 资源处理

实现函数：

```text
inspect_bilibili_video()
```

访问流程：

1. 从 URL 提取 `BVID`。
2. 调 `x/web-interface/view` 获取视频元数据。
3. 用 `aid` 调 `x/v2/reply` 获取评论样本。
4. 用 `bvid + cid` 调 `x/player/v2` 检查字幕。
5. 写 `source_card.md` 和 `resource_processor.json`。

source card 包含：

- 标题、作者、发布时间、时长。
- 播放、点赞、收藏、分享、评论、弹幕。
- 评论区高赞样本。
- 字幕状态。
- `content_access`。
- `assessment.decision`。

### 3.3 细节考量

B站对中国内容尤其重要，因为中文互联网讨论密度更高，评论区和弹幕能暴露观众反应、粉圈噪声、争议点和补充事实。

B站当前能力：

- 搜索：可用。
- 元数据：可用。
- 评论区：可用。
- 字幕检测：可用。
- 正文视频解析：需要下载视频或音频后 ASR。

当前限制：

- `yt-dlp` 下载 B站视频在测试中遇到 HTTP 412。
- 后续需要 `cookies-from-browser`、Playwright 浏览器下载或更深的 B站下载 adapter。
- 在正文未 ASR 前，B站视频只能作为候选或弱信号，不能作为完整 evidence。

## 4. YouTube 实现方式

### 4.1 搜索

实现函数：

```text
search_youtube_videos()
```

访问流程：

1. 使用 `uvx yt-dlp`。
2. 加 `--js-runtimes deno`。
3. 用 `ytsearchN:<query>` 做 YouTube 内部搜索。
4. 解析 JSON lines。
5. 提取标题、频道、认证、播放量、评论计数、时长。

这样做的原因：

- 直接解析 YouTube 搜索网页成本高且不稳定。
- `yt-dlp ytsearch` 能返回结构化候选。
- `deno` 能降低 YouTube extractor 的 JS runtime 缺失问题。

### 4.2 资源处理

实现函数：

```text
inspect_youtube_video()
```

访问流程：

1. `yt-dlp --dump-single-json --skip-download` 获取 metadata。
2. 提取频道、认证、粉丝、播放、点赞、评论计数、标签、分类。
3. 检查 `subtitles` 和 `automatic_captions`。
4. 保存 heatmap 片段，供长视频抽样使用。
5. 评论抓取作为 best-effort，可显式打开。

### 4.3 细节考量

YouTube 中文内容有价值，但要单独记录账号背景和立场风险。对于台海等中国涉政事件，台媒 / 区域媒体 / 海外中文内容会被标记：

```text
taiwan_side_or_regional_crosscheck_should_be_late_stage
```

当前能力：

- 搜索：可用。
- 元数据：可用。
- 字幕检测：可用。
- heatmap：可用。
- 评论抓取：不稳定，测试中超时。
- ASR：可用，已通过 30 秒片段 smoke test。

当前限制：

- YouTube 评论抓取慢且容易 timeout，默认不开启。
- 许多中文视频没有字幕，需要 ASR。
- 海外中文内容不应过早主导 forecast 主线。

## 5. Tavily / 通用网页搜索实现方式

现有入口：

```text
SearchTool
TavilyProvider
```

用途：

- 官方网站。
- 专业媒体。
- 研报 / PDF / 新闻。
- broad recall。
- source discovery。

细节考量：

- Tavily 返回内容适合候选发现，不适合替代正文阅读。
- 对中国事件，Tavily 常召回官方、SEO、外媒和摘要页。
- 进入 forecast 前仍需要 source card 或正文摘录。
- `search_web` 保留 domain routing、quality filtering 和 prediction-market self-reference 过滤。

## 6. Assessment 优化

本轮加入和优化了这些规则：

- 中文核心实体过滤：缺核心实体的候选降权。
- 支持中文 + 数字实体，例如 `哪吒2`。
- 精确实体标题降权：query 有 `哪吒2`，标题不含 `哪吒2 / 魔童闹海 / Ne Zha 2` 会降权。
- 平台互动评分：播放量、评论量、认证状态进入 candidate score。
- 标题风险：`笑喷 / 笑噴 / 反贼 / 反賊 / 炸裂 / 封神` 等标题党风险进入 assessment。
- 台媒 / 区域媒体 bias note：台海 query 下，TVBS、中天、三立、台灣等频道标记为后置 cross-check。
- 搜索结果和 resource report 分层：候选 metadata 不等于正文 evidence。

## 7. 三轮测试

### 7.1 Round 1：体育

query：

```text
王楚钦 近期 比赛 赛后 分析
```

结果：

- B站和 YouTube 内部 adapter 都返回候选。
- 处理 B站视频 `BV1DTCCBUEU1` 成功。
- 读到元数据、评论样本、字幕为空。
- source card 标记 `requires_asr=true`。

代表性 artifact：

```text
artifacts/resources/https_www_bilibili_com_video_bv1dtccbueu1/source_card.md
```

### 7.2 Round 2：娱乐

query：

```text
哪吒2 票房 口碑 影评 分析
```

第一次问题：

- 泛词 `票房 / 口碑` 权重过高。
- 无关电影候选进入结果。

优化：

- 加入核心实体过滤。
- 支持 `哪吒2` 这种中文数字实体。
- B站多排序合并。

结果：

- 国内 B站高互动候选进入前列。
- YouTube 电影解析候选可用。
- 处理 B站 `BV1sAGxz8EAd` 成功，评论样本可读。
- 处理 YouTube `3jW3k9maDAo` 成功，评论抓取 timeout，字幕为空，ASR required。

代表性 artifact：

```text
artifacts/resources/https_www_bilibili_com_video_bv1sagxz8ead/source_card.md
artifacts/resources/https_www_youtube_com_watch_v_3jw3k9madao/source_card.md
```

### 7.3 Round 3：台海

query：

```text
台海 风险 2026 军事分析
```

第一次问题：

- query 太窄时召回不足。
- 台媒 / 区域媒体 YouTube 候选容易排到前面。

优化：

- 平台词、内容类型词从核心实体中过滤掉。
- 台媒 / 区域媒体加 bias note 和 penalty。

结果：

- B站中文军事 / 时政内容可召回。
- YouTube 台媒候选被标记为后置 cross-check。
- 处理 B站 `BV1EcEE6cEB6` 成功，评论样本可读，字幕为空，ASR required。

代表性 artifact：

```text
artifacts/resources/https_www_bilibili_com_video_bv1ecee6ceb6/source_card.md
```

## 8. ASR smoke test

安装项：

- `deno 2.8.2`
- Linux static `ffmpeg 7.0.2`
- `openai-whisper`

测试：

```text
YouTube video: https://www.youtube.com/watch?v=3jW3k9maDAo
clip: 00:00--00:30
model: whisper tiny
device: cpu
```

结果：

- 30 秒音频下载成功。
- Whisper 转写成功。
- 转写质量是 tiny 级别，有错字，但足以证明 pipeline 可通。

样例转写片段：

```text
本期视频分三部分
一会先微训剧头进行观感点名
在深入聚集
聊聊电影是否还能做得更好
```

结论：

- YouTube 无字幕视频可以进入 ASR 深处理。
- B站视频下载仍需 cookies / browser adapter。

## 9. 当前每个 source 的状态

| Source | 搜索 | 元数据 | 评论 | 字幕检测 | 正文/ASR | 主要风险 |
|---|---:|---:|---:|---:|---:|---|
| B站 | 可用 | 可用 | 可用 | 可用 | 下载层待修 | 412、无字幕、粉圈噪声 |
| YouTube | 可用 | 可用 | 不稳定 | 可用 | 可用 | 评论 timeout、海外中文 bias |
| Tavily/web | 可用 | 摘要级 | 不适用 | 不适用 | 需另做正文抓取 | 摘要单薄、source 混杂 |
| official/professional media | 通过 search_web | 取决于网页 | 不适用 | 不适用 | 需正文抓取 | 套话、paywall、二手消息 |

## 10. 下一步建议

优先级最高：

- B站下载 adapter：cookies-from-browser 或 Playwright。
- `process_resource --deep-asr`：把下载、ASR、transcript、resource report 串起来。
- YouTube 评论抓取替代方案：限制页数、换 extractor 参数、或使用浏览器自动化。
- Source card 升级为 resource report：对视频正文做 claims extraction。

评估规则继续优化：

- 分开排序 `domestic_mainline` 和 `foreign_crosscheck`。
- 对长视频加入 heatmap / 评论高赞引导的重点片段选择。
- 评论区采样支持多排序、多页、代表性摘要。
- candidate score 避免高互动内容全部饱和到 0.95。

## 11. Skill 化后的定位

B站 / YouTube access 不应只依赖硬编码 tool。当前 tool 负责执行常见路径，skill 负责指导 agent：

- 如何进入平台内部。
- 如何多 query / 多排序探索。
- 如何筛选候选。
- 如何判断 latest 和 most-related 的风险。
- 如何处理评论区、字幕、ASR、作者背景。
- 如何把候选升级为 resource card。
- 如何判断 source research 是否可以停止。

本地 skill 路径：

```text
/home/hjy/.codex/skills/chinese-video-source-research/
```

后续 agent run 应优先用该 skill 作为行为协议，再按需调用 repo tools 或临时写 adapter。
