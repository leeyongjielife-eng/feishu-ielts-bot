#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
mkdir -p "$ROOT/logs"
LOG_FILE="${LOG_FILE:-$ROOT/logs/daily-push.log}"

# 必须早于任何「日志」调用；且勿命名为 log，否则会误调 macOS 的 /usr/bin/log。
ielts_daily_log() {
  local line="[$(date '+%Y-%m-%d %H:%M:%S %z')] $*"
  printf '%s\n' "$line" | tee -a "$LOG_FILE"
}

export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
# cron 环境可能缺少 HOME/USER，导致 lark-cli 读取不到本地配置与凭据
DEFAULT_HOME="$(cd "$ROOT/../.." && pwd)"
export HOME="${HOME:-$DEFAULT_HOME}"
export USER="${USER:-$(basename "$HOME")}"
export LOGNAME="${LOGNAME:-$USER}"

if command -v lark-cli >/dev/null 2>&1; then
  LARK_CLI="${LARK_CLI:-$(command -v lark-cli)}"
else
  LARK_CLI="${LARK_CLI:-/opt/homebrew/bin/lark-cli}"
fi

CHAT_ID="${FEISHU_IELTS_CHAT_ID:-oc_99000aba52da6814c200481c4dedf1ea}"
DAY_KEY="$(date '+%Y-%m-%d')"
IDEMPOTENCY_KEY="ielts-daily-modes-${DAY_KEY}"
STATE_FILE="${STATE_FILE:-$ROOT/user_state.json}"

INIT_MESSAGE=$(cat <<'MSG'
【IELTS 监督助手】初始化问卷

请输入你最近一次雅思模考成绩（没有则估分）：
格式：#我的成绩 L:x.x R:x.x W:x.x S:x.x 目标:6.5 天数:60
MSG
)

MESSAGE=$(cat <<'MSG'
【IELTS 监督助手】今日任务模式（08:30）

请在群内回复 1 / 2 / 3 选择今日模式：

【1】标准模式
- 建议时长：5–7 小时
- 核心内容：听读写全套练习 + 词汇 + 复盘
- XP 权重：1.0

【2】忙碌模式
- 建议时长：2–4 小时
- 核心内容：1 套听力 + 1 套阅读 + 词汇
- XP 权重：0.7

【3】极简模式
- 建议时长：约 1 小时
- 核心内容：1 篇阅读 + 词汇（维持手感）
- XP 权重：0.4

说明：本阶段为定时固定模板推送；选择结果请自行执行与记录，Bot 暂不自动归档。
MSG
)

need_init=1
if [[ -f "$STATE_FILE" ]]; then
  if python3 - "$STATE_FILE" <<'PY'
import json
import sys

path = sys.argv[1]
try:
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    scores = data.get("initial_scores")
    if isinstance(scores, dict) and {"L", "R", "W", "S"}.issubset(scores.keys()):
        print("initialized")
        raise SystemExit(0)
except Exception:
    pass
raise SystemExit(1)
PY
  then
    need_init=0
  fi
fi

if [[ "$need_init" -eq 1 ]]; then
  MESSAGE="$INIT_MESSAGE"
  IDEMPOTENCY_KEY="ielts-init-questionnaire-${DAY_KEY}"
  ielts_daily_log "initial_scores missing -> send initialization questionnaire first"
fi

ielts_daily_log "start chat_id=${CHAT_ID} idempotency_key=${IDEMPOTENCY_KEY} lark_cli=${LARK_CLI}"

if [[ ! -f "$LARK_CLI" ]]; then
  ielts_daily_log "ERROR: lark-cli not found at ${LARK_CLI}"
  exit 127
fi

dry_run_flag=()
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  dry_run_flag=(--dry-run)
  ielts_daily_log "DRY_RUN=1, will pass --dry-run to lark-cli"
fi

set +e
out="$("$LARK_CLI" im +messages-send \
  --as user \
  --chat-id "$CHAT_ID" \
  --text "$MESSAGE" \
  --idempotency-key "$IDEMPOTENCY_KEY" \
  ${dry_run_flag[@]+"${dry_run_flag[@]}"} 2>&1)"
rc=$?
set -e

if [[ "$rc" -ne 0 ]]; then
  ielts_daily_log "ERROR: lark-cli exit=${rc} output=${out}"
  exit "$rc"
fi

ielts_daily_log "OK: ${out}"
exit 0
