# 飞书 IELTS 学习监督 Bot

基于 **飞书 CLI**（[larksuite/cli](https://github.com/larksuite/cli)）的本地自动化监督工具：定时推送、模式与阶段化任务编排、**结构化打卡**与状态、写作批改、初始化测评与学习计划、**周日周报**、**打卡后随机配图**。适合个人备考节奏管理与比赛/开源展示。

**产品说明**：详细行为与数据结构见仓库内 [`飞书文档/PRD.md`](飞书文档/PRD.md)。

---

## 中文

### 项目介绍

解决什么问题：

- **规律推送**：每日 **08:30** 提醒；未完成 `initial_scores` 时，**首次**由 `bin/init_placement_test.py` 经 `send-daily-modes.sh` 推送 **6 条**雅思学术模拟卷与答案（间隔 2 秒，写入 `mock_test_sent`），之后改为 **每日短提醒** 直至用户回复 `#我的成绩 …`；已初始化则推送 **1/2/3 模式**与 Day x/N 总进度。
- **任务可执行**：根据所选模式（**标准 / 减负 / 极简**）、**Cambridge IELTS 10–18 式进度**（`Cam10 Test1 Section1` …）与 **三阶段 + Recovery Mode** 生成当日听/读/写/词汇任务（`bin/mode_tasks.py`）。
- **闭环反馈**：**推荐**使用 **结构化打卡**（多行：听力正确率+错题、阅读错题+用时、写作/词汇完成度）；兼容单行 `打卡 x/y`（legacy）。解析后输出综合完成率、进度条、状态（🟢 正常 / 🟡 不稳定 / 🔴 差）与 **Recovery Mode**（连续不佳时次日任务减半）。
- **打卡后随机配图**：结构化打卡成功后，从仓库 `pictures/`（`.jpg`/`.jpeg`/`.png`）随机选图发到同群；尽量不与上一张重复；无图或失败时静默跳过。发图需飞书 **用户身份** 下 IM 资源相关权限，并建议 `lark-cli auth login --scope "im:resource im:resource:upload"`（以你控制台实际 scope 为准）。
- **写作批改**：`#写作提交` 触发 Gemini 四维评分（TR / CC / LR / GRA）与建议；失败时引导手动 `#批改结果` 回填。
- **起点测评**：模拟卷与提醒中说明格式；用户回复 `#我的成绩 L:x.x R:x.x W:x.x S:x.x 目标:x.x 天数:N`（1–365 天）后生成成绩分析 + **个性化路线图**（Gemini 优先，失败则规则模板）。`#重新初始化` 会清除 `mock_test_sent` 等字段以便重新走模拟卷流程。
- **周报**：**每周日 21:00** 可由 `launchd` 运行 `bin/weekly-report.py`，汇总本周打卡、听力/阅读/写作趋势、估分与规则生成的下周重点（非大模型长文）。

### 系统架构（示意）

```
┌─────────────────────────────────────────────────────────────┐
│                     macOS launchd                            │
│  ┌──────────────────────┐  ┌────────────────────────────┐   │
│  │ 08:30 daily-push     │  │ Sun 21:00 weekly-report     │   │
│  │ send-daily-modes.sh  │  │ weekly-report.py（见 launchd/）│
│  └──────────┬───────────┘  └─────────────┬──────────────┘   │
│  ┌──────────────────────────────────────────────────────┐  │
│  │ periodic message-router.py（+ 可选 parse-checkin）    │  │
│  └──────────────────────────┬───────────────────────────┘  │
└───────────────────────────────┼─────────────────────────────┘
                                ▼
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

     message-router.py ──► user_state.json（fcntl 锁 + 原子写）
     写作 / 初始化路线图 ──► Google Gemini API（google-generativeai）
     打卡配图 ──► pictures/ + lark-cli 发图
```

### 主要脚本


| 脚本                                | 作用                                          |
| --------------------------------- | ------------------------------------------- |
| `bin/send-daily-modes.sh`         | 08:30：未初始化先发模拟卷/提醒或已初始化发模式选择 + Day 进度条正文 |
| `bin/init_placement_test.py`     | 首次未初始化时连发 6 条模拟卷正文并置 `mock_test_sent`        |
| `bin/message-router.py`           | 拉取会话消息，路由写作/初始化/打卡/模式等并写状态                  |
| `bin/parse-checkin.py`            | 可选：只处理会话**最新一条**打卡（与 router 二选一或并存时注意幂等）    |
| `bin/weekly-report.py`            | 生成并发送周日周报                                   |
| `bin/checkin_motivation_image.py` | 结构化打卡成功后的随机配图发送                             |
| `bin/checkin_common.py`           | 打卡解析、完成率、回复与状态字段（与 router/parse-checkin 共用） |


### 安装步骤

1. **系统**：macOS（当前调度依赖 `launchd`）。
2. **Node.js**：用于安装/运行飞书 CLI（建议 LTS）。
3. **飞书 CLI**：
  ```bash
   npm i -g @larksuite/cli
   lark-cli --version
  ```
4. **登录与授权**（用户身份发消息；发图需含 IM 资源上传等 scope）：
  ```bash
   lark-cli auth login --as user
  ```
5. **Python**：系统或 Homebrew Python 3 即可。
6. **Python 依赖**（写作批改 / 初始化 AI）：
  ```bash
   pip install --user google-generativeai
  ```
   （如使用代理，注意 `lark-cli` 会提示 `HTTPS_PROXY`；可设 `LARK_CLI_NO_PROXY=1` 按需关闭。）

### 配置说明


| 变量 / 配置项                             | 说明                                           |
| ------------------------------------ | -------------------------------------------- |
| `FEISHU_IELTS_CHAT_ID`               | 目标群聊 `chat_id`（LaunchAgent 或脚本环境）            |
| `GEMINI_API_KEY`                     | Google AI Studio / Gemini API Key（**勿提交仓库**） |
| `GEMINI_MODEL`                       | 例如 `gemini-1.5-flash`（按账号可用模型调整）             |
| `HOME` / `USER` / `LOGNAME` / `PATH` | `launchd` 与脚本中已导出，保证 `lark-cli` 可访问钥匙串与配置    |
| `STATE_FILE` / `STATE_LOCK_FILE`     | 可选，覆盖默认 `user_state.json` 与锁文件路径             |
| `LOG_FILE` / `WEEKLY_REPORT_LOG`     | 可选，各脚本日志路径                                   |
| `LARK_CLI`                           | 可选，`lark-cli` 可执行文件绝对路径                      |
| `DRY_RUN`                            | `send-daily-modes.sh` 设为 `1` 时不真实发消息         |


**LaunchAgent**：

- 每日 08:30、消息路由等：通常放在 `~/Library/LaunchAgents/`，由你自行配置指向本仓库脚本（示例名如 `com.youngkit.ielts-daily-push.plist`）。
- **周报示例**：仓库内 [`launchd/com.youngkit.ielts-weekly-report.plist`](launchd/com.youngkit.ielts-weekly-report.plist)（周日 21:00，需把其中路径与 `FEISHU_IELTS_CHAT_ID` 改成你的环境）。

加载示例：

```bash
launchctl bootstrap "gui/$(id -u)" ~/Library/LaunchAgents/com.youngkit.ielts-daily-push.plist
launchctl enable "gui/$(id -u)/com.youngkit.ielts-daily-push"
```

### 使用方法（日常流程）

1. **早间**：08:30 若未完成初始化，**首日**收到 **6 条**模拟卷与答案分段消息，之后收到 **短提醒**；已初始化则收到 **1/2/3 模式**推送（含 Day x/N 总进度；群内文案为 1=标准、2=减负、3=极简及建议时长）。
2. **初始化**（首次）：完成模拟卷自评后，按提示一行回复 `#我的成绩 L:x.x R:x.x W:x.x S:x.x 目标:6.5 天数:60`；收到成绩分析 + 备考路线图。`#重新初始化` 可清空后重来（含重新推送模拟卷）。
3. **选模式**：在会话中**单独一行**回复 `1`、`2` 或 `3`，Bot 返回当日任务（Recovery Mode 下任务减半）。
4. **打卡（推荐）**：使用 Bot 提示中的 **多行结构化格式**（`打卡` + 听力/阅读/写作/词汇四行，听力含正确率与错题，阅读含错题与用时等）。成功后可收到 **随机配图**（请将素材放在 `pictures/`）。仍支持 legacy：`打卡 x/y`。
5. **写作**：`#写作提交` + 正文；失败时按提示用外部 AI 后 `#批改结果` + 标准格式回填。
6. **周报**：周日 21:00 若已配置 `weekly-report` 的 `launchd`，会在群内收到本周学习报告。

### 功能截图

> 截图待补充

### 技术栈

- Shell + **macOS launchd**（定时）
- **Python 3**（路由、状态、打卡、周报、Gemini 调用）
- **飞书 CLI**（IM 读发消息、云文档可选）
- **JSON 本地状态** + `fcntl` 文件锁 + 临时文件原子写入
- **Google Generative AI**（`google-generativeai`）

### 飞书 CLI Skill 说明

开发与排障时可配合 Cursor / Claude 的 **Lark 系列 Skill**（例如 `lark-shared` 做登录与权限、`lark-im` 做收发消息说明），便于对照 OpenAPI 与 CLI 子命令。本仓库逻辑以 **lark-cli 命令行**为准，不直接嵌入开放平台 HTTP 调用。

---

## English

### Overview

A **Feishu (Lark) CLI**–based local bot for IELTS study supervision: scheduled pushes (08:30) with a **six-part academic mock exam** on first-time setup, then **mode selection** (1/2/3) with **Day x/N** progress aligned to structured check-in days, mode-aware **staged** tasks with **Cambridge IELTS 10–18**-style progress, **structured check-in** (plus legacy `打卡 x/y`), state (🟢 / 🟡 / 🔴) with **Recovery Mode**, optional **missed-check-in** notice before the morning push, **random post–check-in image** from `pictures/`, Gemini writing review (TR/CC/LR/GRA), initialization placement with an **N-day** study roadmap (AI with rule fallback), and a **Sunday weekly report** (rule-based commentary).

See [`飞书文档/PRD.md`](飞书文档/PRD.md) for the full PRD (Chinese).

### Architecture (ASCII)

Same idea as the Chinese section: `launchd` runs `send-daily-modes.sh` at 08:30, `weekly-report.py` on Sundays (optional plist in `launchd/`), and `message-router.py` on an interval; all use `lark-cli` for IM; state in `user_state.json` with locking; writing/roadmap use Gemini.

### Installation

1. macOS + `launchd` for scheduling.
2. **Node.js** (LTS recommended).
3. Install **Lark CLI** globally and run `lark-cli auth login --as user` (add IM image upload scopes if you use `pictures/` rewards).
4. **Python 3** + `pip install --user google-generativeai` for writing and roadmap AI.

### Configuration

Use environment variables in LaunchAgents or your shell (see Chinese table). **Never commit real API keys.** Set `FEISHU_IELTS_CHAT_ID`, `GEMINI_API_KEY`, and optionally `GEMINI_MODEL`, `STATE_FILE`, `LOG_FILE`, `WEEKLY_REPORT_LOG`, `LARK_CLI`.

### Daily workflow

Morning push → optional `#我的成绩` → reply `1`/`2`/`3` → **structured check-in** (optional random image) → `#写作提交` / `#批改结果` as needed → Sunday weekly report if scheduled.

### Screenshots

> Screenshots to be added.

### Tech stack

Shell, launchd, Python 3, Lark CLI, JSON + `fcntl` + atomic writes, Google Generative AI SDK.

### Lark CLI Skills

Use community **lark-*** skills (e.g. `lark-shared`, `lark-im`) for auth and IM workflows; this repo executes **lark-cli**, not raw HTTP clients.