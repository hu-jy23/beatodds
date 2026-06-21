#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 ]]; then
  echo "Usage: $0 <RUN_WORKSPACE> [event_title] [market_question] [resolution_rule]" >&2
  exit 2
fi

RUN_WORKSPACE="$1"
EVENT_TITLE="${2:-}"
MARKET_QUESTION="${3:-}"
RESOLUTION_RULE="${4:-}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PROMPT_FILE="$REPO_ROOT/scripts/top_level_china_main_agent_prompt.md"

if [[ ! -f "$PROMPT_FILE" ]]; then
  echo "Missing prompt file: $PROMPT_FILE" >&2
  exit 1
fi

cat <<EOF | codex \
  --model "${CODEX_MODEL:-gpt-5.4}" \
  --search \
  --sandbox danger-full-access \
  --ask-for-approval never \
  --no-alt-screen \
  -C "$REPO_ROOT" \
  -
请读取并遵守 \`scripts/top_level_china_main_agent_prompt.md\`。

本次运行参数：

\`\`\`text
EVENT_TITLE: $EVENT_TITLE
MARKET_QUESTION: $MARKET_QUESTION
RESOLUTION_RULE: $RESOLUTION_RULE
RUN_WORKSPACE: $RUN_WORKSPACE
\`\`\`

如果 EVENT_TITLE、MARKET_QUESTION 或 RESOLUTION_RULE 为空，请先读取 RUN_WORKSPACE 下已有的 run_context.json、run.md、market.md、resolution.md 来补齐；若旧 workspace 没有 market.md / resolution.md，则从 task.md 或父级 market 目录补齐，并在 audit.md 记录。

启动后第一步必须做 capability check，并写入 \`$RUN_WORKSPACE/capability_check.md\`：

1. 当前是否能使用 subagent / multi-agent worker。
2. 当前能否使用 bilibili-render-pdf、youtube-render-pdf、chinese-video-source-research skill。
3. \`data/secrets/www.bilibili.com_cookies.txt\` 是否存在。
4. B站下载是否会使用 \`--cookies data/secrets/www.bilibili.com_cookies.txt\`。

随后按 md-defined harness 开始运行，最终产出 \`$RUN_WORKSPACE/forecast_report.pdf\`。
EOF
