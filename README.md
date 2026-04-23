# 飞书 IELTS 学习监督 Bot

基于 **飞书 CLI**（[`larksuite/cli`](https://github.com/larksuite/cli)）的本地自动化监督工具：定时推送、模式与任务编排、打卡与状态、写作批改、初始化测评与学习计划。适合个人备考节奏管理与比赛/开源展示。

---

## 中文

### 项目介绍

解决什么问题：

- **规律推送**：每日固定时间提醒当日学习模式，降低「忘记开始」的摩擦。
- **任务可执行**：根据所选模式与 Cambridge IELTS 进度生成具体听/读/写任务。
- **闭环反馈**：打卡解析完成率，结合连续表现输出状态（🟢 正常 / 🟡 不稳定 / 🔴 差）与 **Recovery Mode**（连续不佳时次日任务减半，避免崩盘放弃）。
- **写作批改**：`#写作提交` 触发 Gemini 四维评分（TR / CC / LR / GRA）与建议；失败时引导手动 `#批改结果` 回填。
- **起点测评**：未完成 `initial_scores` 时，08:30 前先推送 **Placement** 问卷；`#我的成绩` 解析后生成约 60 天维度的个性化学习节奏建议（时长、弱项加权、Cam 册数倒推等）。

### 系统架构（示意）

```
┌─────────────────────────────────────────────────────────────┐
│                     macOS launchd                            │
│  ┌──────────────────────┐    ┌────────────────────────────┐ │
│  │ 08:30 daily-push     │    │ periodic message-router    │ │
│  │ send-daily-modes.sh  │    │ message-router.py          │ │
│  └──────────┬───────────┘    └─────────────┬──────────────┘ │
└─────────────┼──────────────────────────────┼───────────────┘
              │                              │
              ▼                              ▼
     ┌────────────────┐              ┌──────────────┐
     │ lark-cli       │              │ lark-cli     │
     │ im +messages   │              │ im list/send │
     └────────┬───────┘              └──────┬───────┘
              │                              │
              └──────────────┬───────────────┘
                             ▼
                    ┌────────────────┐
                    │ 飞书 IM 会话    │
                    └────────────────┘

     message-router.py ──► user_state.json（fcntl 锁）
     写作分支 ──► Google Gemini API（google-generativeai）
```

### 安装步骤

1. **系统**：macOS（当前调度依赖 `launchd`）。
2. **Node.js**：用于安装/运行飞书 CLI（建议 LTS）。
3. **飞书 CLI**：
   ```bash
   npm i -g @larksuite/cli
   # 或按官方文档安装后，确保 lark-cli 在 PATH 中
   lark-cli --version
   ```
4. **登录与授权**（用户身份发消息）：
   ```bash
   lark-cli auth login --as user
   ```
5. **Python**：系统或 Homebrew Python 3 即可。
6. **Python 依赖**（写作批改）：
   ```bash
   pip install --user google-generativeai
   ```
   （如使用代理，注意 `lark-cli` 会提示 `HTTPS_PROXY`；可设 `LARK_CLI_NO_PROXY=1` 按需关闭。）

### 配置说明

| 变量 / 配置项 | 说明 |
|---------------|------|
| `FEISHU_IELTS_CHAT_ID` | 目标群聊 `chat_id`（LaunchAgent 或脚本环境） |
| `GEMINI_API_KEY` | Google AI Studio / Gemini API Key（**勿提交仓库**） |
| `GEMINI_MODEL` | 例如 `gemini-1.5-flash-8b`（按账号可用模型调整） |
| `HOME` / `USER` / `LOGNAME` / `PATH` | `launchd` 与脚本中已导出，保证 `lark-cli` 可访问钥匙串与配置 |
| `STATE_FILE` | 可选，覆盖默认 `user_state.json` 路径 |
| `LOG_FILE` | 可选，各脚本日志路径 |
| `DRY_RUN` | `send-daily-modes.sh` 设为 `1` 时不真实发消息 |

**LaunchAgent 示例路径**（按本机用户调整）：

- `~/Library/LaunchAgents/com.youngkit.ielts-daily-push.plist` — 每日 08:30 推送
- `~/Library/LaunchAgents/com.youngkit.ielts-message-router.plist` — 消息轮询与路由

加载示例：

```bash
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.youngkit.ielts-daily-push.plist
launchctl enable "gui/$(id -u)/com.youngkit.ielts-daily-push"
```

### 使用方法（日常流程）

1. **早间**：08:30 若未完成初始化，先收到 **初始化问卷**；否则收到 **1/2/3 模式** 推送。
2. **初始化**（首次）：按提示回复  
   `#我的成绩 L:x.x R:x.x W:x.x S:x.x 目标:6.5 天数:60`  
   收到个性化计划；`#重新初始化` 可清空后重来。
3. **选模式**：在会话中回复 `1`、`2` 或 `3`，Bot 返回当日任务（含 Cambridge 听/读进度；Recovery Mode 下任务减半）。
4. **打卡**：`打卡 x/y` 或 `打卡：x/y`，更新完成率与状态灯。
5. **写作**：`#写作提交` + 正文；失败时按提示用外部 AI 后 `#批改结果` + 标准格式回填。

### 功能截图

> 截图待补充

### 技术栈

- Shell + **macOS launchd**（定时）
- **Python 3**（路由、状态、Gemini 调用）
- **飞书 CLI**（IM 读发消息）
- **JSON 本地状态** + `fcntl` 文件锁
- **Google Generative AI**（`google-generativeai`）

### 飞书 CLI Skill 说明

开发与排障时可配合 Cursor / Claude 的 **Lark 系列 Skill**（例如 `lark-shared` 做登录与权限、`lark-im` 做收发消息说明），便于对照 OpenAPI 与 CLI 子命令。本仓库逻辑以 **`lark-cli` 命令行** 为准，不直接嵌入开放平台 HTTP 调用。

### 作者与参赛信息

- **作者**：youngkit  
- **活动**：Mini Camp 第一期  

---

## English

### Overview

A **Feishu (Lark) CLI**–based local bot for IELTS study supervision: scheduled pushes, mode-aware task generation with **Cambridge IELTS 10–18**-style progress, check-in and state (🟢 / 🟡 / 🔴) with **Recovery Mode**, Gemini writing review (TR/CC/LR/GRA), and an **initialization placement** flow with a ~**60-day** study plan outline.

### Architecture (ASCII)

Same as the Chinese section: `launchd` runs `send-daily-modes.sh` at 08:30 and `message-router.py` on an interval; both call `lark-cli` for IM; state lives in `user_state.json` with file locking; writing uses the Gemini API.

### Installation

1. macOS + `launchd` for scheduling.  
2. **Node.js** (LTS recommended).  
3. Install **Lark CLI** globally and run `lark-cli auth login --as user`.  
4. **Python 3** + `pip install --user google-generativeai` for writing correction.

### Configuration

Use environment variables in LaunchAgents or your shell (see Chinese table). **Never commit real API keys.** Set `FEISHU_IELTS_CHAT_ID`, `GEMINI_API_KEY`, and optionally `GEMINI_MODEL`.

### Daily workflow

Morning push → optional placement → reply `1`/`2`/`3` → complete tasks → `打卡 x/y` → `#写作提交` / `#批改结果` as needed.

### Screenshots

> Screenshots to be added.

### Tech stack

Shell, launchd, Python 3, Lark CLI, JSON + `fcntl`, Google Generative AI SDK.

### Lark CLI Skills

Use community **lark-*** skills (e.g. `lark-shared`, `lark-im`) for auth and IM workflows; this repo executes **`lark-cli`**, not raw HTTP clients.

### Author & program

**youngkit** · **Mini Camp — Phase 1**
