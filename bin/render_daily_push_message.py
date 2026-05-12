#!/usr/bin/env python3
"""为 send-daily-modes.sh 生成完整推送正文（stdout）。参数: STATE_FILE need_init(0|1)

已初始化时，「Day x / N」与总进度条与 mode_tasks.completed_checkin_days_in_plan 同口径：
计划窗口内、结构化打卡写入 checkin_history 的不重复日期数（legacy 不计入）。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bin"))
from mode_tasks import completed_checkin_days_in_plan  # noqa: E402
from progress_bar import make_bar  # noqa: E402

INIT_MESSAGE = """【IELTS 监督助手】初始化问卷

请输入你最近一次雅思模考成绩（没有则估分）：
格式：#我的成绩 L:x.x R:x.x W:x.x S:x.x 目标:6.5 天数:60"""

INIT_REMINDER_AFTER_MOCK = """【IELTS 监督助手】

模拟测试材料已推送。请完成阅读/写作/口语自评（听力请用 Cambridge 真题），对照最后一条消息中的阅读答案后，回复：

#我的成绩 L:x.x R:x.x W:x.x S:x.x 目标:6.5 天数:60

（将分数、目标分、备考天数改为你的真实值）"""

MODE_BODY = """【IELTS 监督助手】今日任务模式（08:30）

请在群内回复 1 / 2 / 3 选择今日模式：

【1】标准模式
- 建议时长：5–7 小时
- 核心内容：听读写全套练习 + 词汇 + 复盘
- XP 权重：1.0

【2】减负模式
- 建议时长：2–4 小时
- 核心内容：1 套听力 + 1 套阅读 + 词汇
- XP 权重：0.7

【3】极简模式
- 建议时长：约 1 小时
- 核心内容：1 篇阅读 + 词汇（维持手感）
- XP 权重：0.4

说明：本阶段为定时固定模板推送；选择结果请自行执行与记录，Bot 暂不自动归档。"""


def main() -> None:
    state_path = Path(sys.argv[1])
    need_init = sys.argv[2] == "1"
    if need_init:
        state: dict = {}
        if state_path.exists():
            try:
                state = json.loads(state_path.read_text(encoding="utf-8"))
            except Exception:
                state = {}
        if state.get("mock_test_sent"):
            print(INIT_REMINDER_AFTER_MOCK, end="")
        else:
            print(INIT_MESSAGE, end="")
        return

    state: dict = {}
    if state_path.exists():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except Exception:
            state = {}

    study_days = int(state.get("study_days") or 60)
    if study_days < 1:
        study_days = 60

    comp = completed_checkin_days_in_plan(state)
    pct = round(min(100.0, float(comp) / float(study_days) * 100.0)) if study_days else 0
    bar = make_bar(float(comp), float(study_days))
    header = f"📚 今日任务（Day {comp}/{study_days}）\n总进度：{bar}  {pct}%\n────────────────\n\n"
    print(header + MODE_BODY, end="")


if __name__ == "__main__":
    main()
