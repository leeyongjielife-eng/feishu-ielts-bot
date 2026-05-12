# IELTS 飞书学习监督助手 — 产品需求文档（PRD）

**文档版本**：1.2（对齐当前仓库实现）  
**更新日期**：2026-05-12  
**仓库**：`ielts-lark-daily-push`  
**形态**：本地脚本 + macOS `launchd` + 飞书 CLI（用户身份发消息），非云端常驻 Bot 服务。

---

## 1. 产品定位

面向 **雅思 Academic 自主备考** 的单用户监督工具：在指定飞书群聊中，通过 **定时推送、模式化任务、打卡闭环、写作批改与周报**，把「今天要做什么」「做得怎么样」「弱项与状态如何」压缩为可执行的对话流，降低坚持成本。

**核心价值**

- **节律**：早间固定推送，减少「忘记开局」。
- **可执行**：按 Cambridge 进度与模式生成当日听/读/写/词汇任务。
- **闭环**：结构化打卡 → 完成率与状态灯 → Recovery Mode 自动减负。
- **个性化起点**：初始化测评后生成约 N 天（默认 60）维度的备考节奏与阶段任务。
- **打卡后随机配图**：结构化打卡成功后，在群内追加 **一张** 来自本地 `pictures/` 的随机配图（与上一张尽量不重复、候选集内尽量均匀随机）；与早间「任务正文」分离。

**明确非目标（当前实现）**

- 非多租户 SaaS；状态为单机 JSON。
- 不托管用户作文与聊天记录，依赖飞书会话历史与本地 `user_state.json`。
- 周报中的「点评/下周重点」为 **规则引擎生成**，非云端大模型长文（与初始化路线图中的 AI 能力区分见下文）。

---

## 2. 用户与场景


| 维度       | 说明                                                                                                                                                 |
| -------- | -------------------------------------------------------------------------------------------------------------------------------------------------- |
| **目标用户** | 已安装飞书 CLI 并完成 **用户身份授权** 的 macOS 备考者（当前为作者自用场景抽象）。                                                                                                 |
| **主场景**  | 每日 08:30 收到模式选择或初始化引导 → 回复 `1`/`2`/`3` 领取任务 → 完成后 **结构化打卡** → 群内收到 **打卡反馈**，成功时可能再收到 **随机配图** → 按需 `#写作提交` / `#批改结果` → 周日查看周报。                     |
| **前置条件** | `lark-cli`、`python3`、可选 `google-generativeai`（写作批改）；环境变量 `FEISHU_IELTS_CHAT_ID`、`GEMINI_API_KEY` 等；发图片需开放平台 **用户身份** 下 IM 资源上传相关权限并已 `auth login`。 |


---

## 3. 核心功能模块

### 3.1 每日 08:30 任务推送（launchd）

- **入口脚本**：`bin/send-daily-modes.sh`
- **逻辑**：若 `user_state.json` 中缺少完整 `initial_scores`（L/R/W/S 四项），则推送 **初始化问卷**；否则由 `bin/render_daily_push_message.py` 生成 **含 Day x/N 总进度条** 的「1/2/3 模式」说明并发送。
- **幂等**：通过 `lark-cli` 的 `--idempotency-key` 按日区分（初始化问卷 vs 日常模式）。
- **部署说明**：仓库内提供周报用 `launchd` 示例 plist；**每日 08:30** 的 plist 通常置于本机 `~/Library/LaunchAgents/`（详见 `README.md`），由用户自行配置 `ProgramArguments` 指向 `send-daily-modes.sh`。

### 3.2 三种学习模式（标准 / 减负 / 极简）

- **用户操作**：在群内回复 **单独一行** `1`、`2` 或 `3`（与 `bin/handle-mode-selection.py` 中 `MODE_RE` 一致）。
- **文案侧名称**（`render_daily_push_message.py`）：**标准模式、减负模式、极简模式**（产品口语中「忙碌」可与「减负」对应，以界面文案为准）。
- **路由**：`bin/message-router.py` 拉取最近消息并路由至模式分支，调用 `bin/mode_tasks.py` 的 `build_mode_tasks` 生成任务清单。

### 3.3 Cambridge 进度追踪（Cam10 → Cam18）

- **进度串格式**：`Cam{10..18} Test{1..4} Section{1..4}`，由 `message-router` 中 `PROGRESS_RE` 校验；默认 `Cam10 Test1 Section1`。
- **推进规则**：依模式在完成任务后 **推进听力与/或阅读** 进度（`advance_progress`，书本上限 18）；与当日任务文案中的「当前一套」对齐。

### 3.4 60 天阶段化任务（3 阶段）

- **计划长度**：`study_days` 由初始化 `#我的成绩 … 天数:N` 写入，默认 60，范围 1–365。
- **阶段划分**（`mode_tasks.compute_day_phase`）：在计划窗口内按 **已累计结构化打卡的不重复天数** 驱动阶段（无打卡记录时回退为日历备考日）；**第 1–20 天 → 阶段一；21–40 → 阶段二；41+ → 阶段三**（与「总天数均分三阶段」的文案型计划可并存，代码以打卡日阈值为准）。
- **阶段标题**：基础巩固 / 专项突破 / 冲刺模拟。
- **差异化**：词汇量基线、写作是否排篇、阶段三模考周提示、弱项加练行等随阶段与 `weakest_skill` 变化；**Recovery Mode** 下任务减半（见 3.7）。

### 3.5 打卡系统

- **主路径（推荐）**：**结构化打卡** — 固定多行模板：`打卡` + `听力/阅读/写作/词汇` 四行；听力完成须含 **正确率%** 与 **错题数**；阅读完成须含 **错题数** 与 **用时**；写作/词汇用「完成/未完成」等标记。解析与回复见 `bin/checkin_common.py`。
- **兼容路径**：`打卡 x/y` 单行 **legacy** 格式（`classify_checkin_message`）；无法解析且以「打卡」开头时返回 **hint** 与格式说明。
- **处理入口**：`bin/message-router.py`（轮询历史消息）；`bin/parse-checkin.py` 为可选「仅处理会话最新一条」的补充路径。
- **反馈内容**：分项进度条（听力正确率、阅读等效正确率）、**综合完成率** 进度条、连续天数条、状态灯、规则化 **点评** 列表；触发 Recovery 时附提示。

### 3.6 完成率与进度条

- **综合完成率**：听力正确率%、阅读由错题数换算的等效%、写作/词汇完成各按 0/100% 计入，四项算术平均（`composite_completion_rate`）。
- **展示**：统一使用 `bin/progress_bar.py` 的 `make_bar` / `listen_progress_bar_line` 等生成文本条形图。

### 3.7 状态系统与 Recovery Mode

- **状态灯**：`evaluate_state` 依据综合完成率与连续表现得到 **🟢 正常 / 🟡 不稳定 / 🔴 差**（文案含 emoji）。
- **fail_streak / streak**：低完成率累积失败 streak；高完成率累积正向 streak。
- **Recovery Mode**：连续不佳或达到阈值时 `recovery_mode=true`，次日 `build_mode_tasks` 走减半任务分支；用户选择模式并成功下发任务后 **清除** `recovery_mode`（当日任务已按减负生成）。

### 3.8 写作 AI 批改与手动回填

- **自动批改**：消息以 `#写作提交` 开头触发；`bin/ai_caller.py` 调用 **Gemini**（`google-generativeai`），要求模型输出 JSON 四维分（TR/CC/LR/GRA）与 3 条建议；解析成功后格式化回复并更新 `last_writing_scores`、`weekly_writing_avg` 等。
- **降级**：API 失败、超时或 JSON 不合规时，推送 **人工批改指引**（`WRITING_UNAVAILABLE_MESSAGE`），引导用户使用外部 AI 后通过 `#批改结果` + 固定模板回填；`#批改结果` 由路由解析并合并分数。

### 3.9 初始化测评与个性化计划

- **触发**：未完成初始化时 08:30 问卷；或用户发送 `#我的成绩 L:x.x R:x.x W:x.x S:x.x 目标:x.x 天数:N`（分数为 0–9，步长 0.5）。
- **输出 1**：`init_plan.format_score_analysis_message` — 各科条形图、均分与目标差距、最弱项、预计达标日期范围。
- **输出 2**：`init_plan.generate_study_roadmap_text` — 优先 **Gemini 生成** 个性化路线图，失败则 **规则模板** `format_study_roadmap_fallback`（按 `study_days` 三等分阶段叙述，含 Cam10→Cam18 教材顺序说明）。
- **重置**：`#重新初始化` 清空初始化相关字段及 `last_checkin_motivation_image` 等（见代码 `handle_init_reset`）。

### 3.10 每日打卡完成后的随机配图

#### 3.10.1 产品意图

在 **不改变** 08:30 每日任务推送正文、**不改变** 结构化打卡反馈（文字点评、进度条、状态灯等）的前提下，于用户 **成功完成一次结构化打卡** 后，在同一会话内追加 **一张随机配图**，作为完成闭环的视觉正反馈。配图来自用户自备图库，**不参与** 评分或任务逻辑。

**交付边界**：本文档在打卡后场景中 **仅** 定义「随机配图」一项能力；不定义打卡后其它类型消息的产品规格。

#### 3.10.2 用户侧可见流程（群内消息顺序）

1. **结构化打卡正文**（用户发送多行打卡）。
2. **Bot 打卡反馈**（`format_structured_checkin_reply`：分项条、综合完成率、连续天数、状态灯、点评、Recovery 提示等）。
3. **随机配图**（一条图片消息）：从本地 `pictures/` 中选出的一张 `.jpg`/`.jpeg`/`.png`。

若第 3 步因无图库、发图失败或权限问题未发出，**不影响** 第 2 步已完成的打卡落库与状态更新（失败仅记日志，不阻断主流程）。

#### 3.10.3 触发边界（何时有 / 何时没有）


| 打卡类型                                                           | 是否发送随机配图              |
| -------------------------------------------------------------- | --------------------- |
| **结构化打卡**（`classify_checkin_message` = `structured`，解析成功并正常回复） | **是**（在无图或失败时为「否」，见上） |
| **legacy** `打卡 x/y`                                            | **否**                 |
| **hint**（格式不对、多出行等导致无法结构化解析）                                   | **否**                 |
| **none**（不匹配打卡）                                                | **否**                 |


#### 3.10.4 媒体与图库规范

- **图库路径**：仓库根目录下 **`pictures/`**；仅扫描 **直接子文件**（非递归子目录）；扩展名 **`.jpg` / `.jpeg` / `.png`**，**大小写不敏感**；忽略非图片文件。  
- **目录或候选为空**：**静默跳过**（不打断打卡），不写 `last_checkin_motivation_image`。  
- **与 `lark-cli` 的约束**：发图需使用 **相对仓库根** 的路径（如 `./pictures/xxx.png`），子进程 **`cwd`** 设为仓库根。

#### 3.10.5 选图与「不与上一张重复」

- **状态字段**：`user_state.json` 中 `last_checkin_motivation_image` 存 **上一张已成功发出的图片文件名**（basename，不含路径）。  
- **选图规则**：在全部合法图片中，先 **排除** 与 `last_checkin_motivation_image` 同名的文件；若排除后集合为空（例如全库仅一张图），则 **回退为全库随机**（允许与上一张同名）。  
- **随机性**：在最终候选集合上使用 **均匀随机**（`random.choice`），使各张在长期统计下被抽中概率接近。  
- **持久化时机**：仅在 **配图发送流程按当前工程定义整体成功** 后，将本次 basename 写回 `last_checkin_motivation_image`（`message-router` 二次加锁写入；`parse-checkin` 路径为 `atomic_write_state` 更新）。  
- **重置**：用户发送 `#重新初始化` 时清空该字段，避免与旧计划混淆。

#### 3.10.6 实现与运行依赖

- **实现模块**：`bin/checkin_motivation_image.py`（枚举、`pick`、`try_send_checkin_motivation`）。  
- **调用链**：`bin/message-router.py`（主路径）、`bin/parse-checkin.py`（仅最新一条消息的补充路径）在 **结构化打卡成功且反馈已发出** 后调用。  
- **工作目录与路径**：发图使用 **相对仓库根** 路径（如 `./pictures/xxx.png`），子进程 **`cwd`** 为仓库根，以满足 `lark-cli` 校验。  
- **飞书权限**：发图走 IM 资源上传，需开放平台为应用开通 **用户身份** 下相关权限，且本机执行 **`lark-cli auth login`** 携带对应 scope；否则配图失败（仍不反写 `last_checkin_motivation_image`）。

#### 3.10.7 与「每日任务推送」的关系（需求澄清）

- **早间 08:30 推送**：仍仅为模式选择 / 初始化问卷 + Day 进度条等 **纯文本**，**不包含** 随机图。  
- **随机配图**：仅绑定 **「当日结构化打卡成功」** 事件，**一天内多次结构化打卡**（若发生）按每次成功各触发一轮（以消息幂等与状态更新为准）。

### 3.11 每周日 21:00 周报

- **脚本**：`bin/weekly-report.py`
- **调度**：仓库示例 `launchd/com.youngkit.ielts-weekly-report.plist`（`Weekday=0` 表示周日，`21:00`）。
- **内容**：本周学习天数与进度条、平均完成率、连续天数、听力逐日正确率条、阅读周均错题与最难题型、写作四维趋势条与环比箭头、**预估当前分数**（听力/阅读由数据估分，写作为四维均值，口语沿用初始化）、综合估分条、弱项与 **下周重点**（`build_week_focus` 等 **规则逻辑**）、Cam 听力/阅读进度条、状态参考句。
- **副作用**：发送成功后写入 `prev_week_listening_avg`、`prev_week_reading_errors_avg`、`prev_week_writing_avg` 供下周对比；使用按 ISO 周生成的 **幂等 key** 防重复发。

### 3.12 统一消息路由

- **脚本**：`bin/message-router.py`
- **机制**：`lark-cli im +chat-messages-list` 拉取最近 50 条，按优先级匹配：**#写作提交** → **#重新初始化** → **#我的成绩** → **#批改结果** → **打卡类** → **模式 1/2/3**。
- **事务顺序**：先 `send_message` 回复，再在 `fcntl` **文件锁** 内合并 `updates` 并 **原子写入** `user_state.json`；结构化打卡成功后再尝试发 **打卡随机配图** 并二次更新 `last_checkin_motivation_image`。

### 3.13 并发安全：文件锁与原子写入

- **锁文件**：默认 `user_state.json.lock`，`state_lock()` 使用独占锁包裹读-改-写。
- **原子写**：`write_state` / `atomic_write_state` 采用 **临时文件 `*.json.tmp` 再 replace**（`parse-checkin` / `weekly-report` 等同理），避免写到一半进程崩溃导致 JSON 损坏。

---

## 4. 数据结构（`user_state.json` 摘要）

以下为 **主要字段**（非穷举；以 `ensure_defaults`、`apply_checkin_to_state`、初始化与周报写入为准）。


| 分类      | 字段示例                                                                                                                                           | 含义                               |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------------- | -------------------------------- |
| 初始化     | `initial_scores`, `target_score`, `study_days`, `start_date`, `weakest_skill`, `initial_level_factor`, `xp_daily_threshold`, `study_plan_sent` | 测评结果与计划元数据                       |
| 进度      | `listening_progress`, `reading_progress`                                                                                                       | Cambridge 当前套题位置                 |
| 打卡      | `checkin_history`, `completion_rate`, `last_checkin_date`, `last_checkin_message_id`                                                           | 历史行（含听力正确率、阅读错题、写作/词汇布尔等）与最近一次打卡 |
| 状态      | `state`, `fail_streak`, `streak`, `recovery_mode`                                                                                              | 状态灯与 Recovery                    |
| 滑动统计    | `listening_accuracy_history`, `reading_error_history`, `weekly_listening_avg`, `weekly_reading_avg`, `weekly_writing_avg`                      | 近 7 日窗口与周均，供打卡点评与周报              |
| 周报环比    | `prev_week_listening_avg`, `prev_week_reading_errors_avg`, `prev_week_writing_avg`                                                             | 上周快照                             |
| 写作      | `last_writing_message_id`, `last_auto_writing_message_id`, `last_manual_writing_message_id`, `last_writing_date`, `last_writing_scores`        | 幂等与最新分数                          |
| 模式      | `last_mode_message_id`, `last_mode_selected`, `last_mode_date`                                                                                 | 最近一次模式选择                         |
| 初始化消息幂等 | `last_init_scores_message_id`, `last_init_reset_message_id`                                                                                    | 防重复处理                            |
| 打卡配图    | `last_checkin_motivation_image`                                                                                                                | 上一张已成功发出的随机配图文件名（basename）       |


`checkin_history` 单行结构含：`date`, `completion_rate`, `listening_accuracy`, `listening_errors`, `reading_errors`, `reading_minutes`, `reading_pct`, `writing_done`, `vocab_done` 等（见 `apply_checkin_to_state`）。

---

## 5. 系统架构

```
┌─────────────────────────────────────────────────────────────────┐
│ macOS launchd                                                    │
│  · 每日 08:30 → send-daily-modes.sh（用户配置 LaunchAgent）        │
│  · 周期性/手动 → message-router.py（拉会话、路由、写状态、发打卡随机配图）   │
│  · 每周日 21:00 → weekly-report.py（示例 plist 在仓库 launchd/）    │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ lark-cli（--as user）                                             │
│  im +messages-send / +chat-messages-list                         │
└────────────────────────────┬────────────────────────────────────┘
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│ 飞书 IM 群聊（FEISHU_IELTS_CHAT_ID）                              │
└─────────────────────────────────────────────────────────────────┘

本地：user_state.json（fcntl 锁 + 原子写）│ pictures/（打卡随机配图）
外部：Google Gemini API（写作批改、初始化路线图 AI 分支）
```

**关键脚本一览**


| 组件      | 路径                                 |
| ------- | ---------------------------------- |
| 早间推送壳   | `bin/send-daily-modes.sh`          |
| 早间正文    | `bin/render_daily_push_message.py` |
| 消息路由    | `bin/message-router.py`            |
| 打卡共用逻辑  | `bin/checkin_common.py`            |
| 模式任务    | `bin/mode_tasks.py`                |
| 初始化计划   | `bin/init_plan.py`                 |
| AI 调用封装 | `bin/ai_caller.py`                 |
| 打卡随机配图  | `bin/checkin_motivation_image.py`  |
| 周报      | `bin/weekly-report.py`             |
| 可选单消息打卡 | `bin/parse-checkin.py`             |
| 可选仅模式   | `bin/handle-mode-selection.py`     |


---

## 6. MVP 范围（当前已交付）

- 飞书群聊单向/对话式监督（用户身份 CLI）。
- 08:30 推送初始化问卷或模式选择 + Day 进度。
- 模式 `1/2/3` → 分阶段、分 Recovery 的任务生成 + Cam 进度推进。
- 结构化打卡 + legacy 打卡 + 格式 hint。
- 完成率、状态灯、Recovery、打卡点评与历史落盘。
- 写作 Gemini 批改 + 失败降级与 `#批改结果`。
- 初始化测评 + 双消息（分析 + 路线图，AI/规则降级）。
- **打卡后随机配图**：结构化打卡成功后从 `pictures/` 发送随机图（排除上一张 basename、均匀随机；无图/失败静默跳过）；与早间任务推送解耦（详见 **§3.10**）。
- 周日报发送与周环比快照字段更新。
- 统一路由、文件锁、原子写、关键链路的幂等消息 ID。

---

## 7. 未来规划（建议 backlog）


| 方向       | 说明                                                                             |
| -------- | ------------------------------------------------------------------------------ |
| **工程化**  | 将每日 08:30 plist 模板纳入仓库；`message-router` 与 `handle-mode-selection` 正则常量统一，避免漂移。 |
| **周报增强** | 可选接入大模型生成「教师口吻」长点评（当前为规则引擎）。                                                   |
| **多用户**  | 按 `chat_id` 或用户维度拆分状态文件与服务化路由。                                                 |
| **可观测性** | 结构化日志、指标上报、失败告警（而非仅本地 `logs/*.log`）。                                           |
| **测试**   | 对 `checkin_common`、`mode_tasks`、路由优先级做单元测试与 golden file。                       |
| **依赖**   | Gemini SDK 迁移至官方推荐的 `google.genai`；`lark-cli` 版本跟进与 scope 文档同步。                |
| **安全**   | 密钥仅环境变量/钥匙串，CI 中禁止打印 state。                                                    |


---

## 8. 附录：环境变量（与 README 对齐）


| 变量                                | 用途                                 |
| --------------------------------- | ---------------------------------- |
| `FEISHU_IELTS_CHAT_ID`            | 目标群聊                               |
| `GEMINI_API_KEY` / `GEMINI_MODEL` | 写作与路线图 AI                          |
| `STATE_FILE` / `STATE_LOCK_FILE`  | 状态与锁路径覆盖                           |
| `LOG_FILE` / `WEEKLY_REPORT_LOG`  | 日志路径                               |
| `LARK_CLI`                        | CLI 可执行文件路径                        |
| `DRY_RUN`                         | `send-daily-modes.sh` 为 `1` 时不真实发送 |


---

*本文档描述以仓库当前代码为准；若实现变更，请同步更新本 PRD。*