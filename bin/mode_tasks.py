"""
模式任务生成：结合 start_date / study_days / weakest_skill 计算阶段并调整任务文案。
"""
from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Dict, List, Tuple

import re

from progress_bar import listen_progress_bar_line

LISTEN_RE = re.compile(r"^(Cam\d+\s+Test\d+)\s+Section\d+$")
READ_RE = re.compile(r"^(Cam\d+\s+Test\d+)\s+Passage\d+$")


def _listen_test_label(listening_progress: str) -> str:
    """提取 'Cam10 Test1 Section?' → 'Cam10 Test1'，无匹配则原样返回。"""
    m = LISTEN_RE.match((listening_progress or "").strip())
    return m.group(1) if m else (listening_progress or "")


def _read_test_label(reading_progress: str) -> str:
    """提取 'Cam10 Test1 Passage?' → 'Cam10 Test1'，无匹配则原样返回。"""
    m = READ_RE.match((reading_progress or "").strip())
    return m.group(1) if m else (reading_progress or "")

TASK2_PLACEHOLDER = (
    "Task 2 占位题目：Some people think that online learning will replace traditional classroom learning. "
    "Discuss both views and give your own opinion."
)
TASK1_PLACEHOLDER = (
    "Task 1 占位题目：The chart below shows changes in the average household expenditure on selected categories "
    "in a country between 2000 and 2020. Summarise the information by selecting and reporting the main features, "
    "and make comparisons where relevant."
)

SKILL_CN = {"L": "听力", "R": "阅读", "W": "写作", "S": "口语"}
PHASE_TITLE = {
    1: "第一阶段：基础巩固",
    2: "第二阶段：专项突破",
    3: "第三阶段：冲刺模拟",
}


def calendar_study_day(state: Dict) -> int:
    """自 start_date 起算的日历备考日（1..study_days），无 start_date 时视为第 1 天。"""
    study_days = int(state.get("study_days") or 60)
    if study_days < 1:
        study_days = 60
    sd = state.get("start_date")
    today = date.today()
    if not sd:
        return 1
    try:
        s0 = datetime.strptime(str(sd)[:10], "%Y-%m-%d").date()
        d = (today - s0).days + 1
    except ValueError:
        return 1
    return max(1, min(d, study_days))


def completed_checkin_days_in_plan(state: Dict) -> int:
    """在 [start_date, start_date+study_days-1] 窗口内，有结构化打卡记录的不重复日期数。"""
    sd = state.get("start_date")
    study_days = int(state.get("study_days") or 60)
    if not sd or study_days < 1:
        return 0
    try:
        s0 = datetime.strptime(str(sd)[:10], "%Y-%m-%d").date()
    except ValueError:
        return 0
    end = s0 + timedelta(days=max(0, study_days - 1))
    hist = state.get("checkin_history")
    if not isinstance(hist, list):
        return 0
    seen: set[str] = set()
    for row in hist:
        if not isinstance(row, dict):
            continue
        ds = str(row.get("date", ""))[:10]
        if len(ds) != 10:
            continue
        try:
            d = datetime.strptime(ds, "%Y-%m-%d").date()
        except ValueError:
            continue
        if s0 <= d <= end:
            seen.add(ds)
    return len(seen)


def compute_day_phase(state: Dict) -> Tuple[int, int, int, str, int]:
    """
    返回 (已完成打卡天数, study_days, phase, phase_title, schedule_day)。
    schedule_day：有打卡记录时用「已完成打卡天数」驱动阶段与写作节奏；否则回退为日历备考日。
    """
    study_days = int(state.get("study_days") or 60)
    if study_days < 1:
        study_days = 60
    cal = calendar_study_day(state)
    comp = completed_checkin_days_in_plan(state)
    schedule_day = comp if comp > 0 else cal
    if schedule_day <= 20:
        phase = 1
    elif schedule_day <= 40:
        phase = 2
    else:
        phase = 3
    return comp, study_days, phase, PHASE_TITLE[phase], schedule_day


def _weak_label(state: Dict) -> str:
    w = str(state.get("weakest_skill", "") or "").strip().upper()
    return SKILL_CN.get(w, w) if w in SKILL_CN else "（尚未测评）"


def _vocab_base(phase: int, recovery: bool) -> int:
    if recovery:
        return 15
    if phase >= 2:
        return 45
    return 30


def _writing_due(phase: int, day_number: int) -> bool:
    if phase == 1:
        return day_number % 3 == 1
    if phase == 2:
        return day_number % 2 == 1
    return True


def _weak_extra_lines(weakest: str, mode: str, phase: int, recovery: bool) -> List[str]:
    if recovery or phase != 2:
        return []
    w = str(weakest or "").strip().upper()
    lines: List[str] = []
    if w == "L" and mode in ("1", "2"):
        lines.append("- 弱项加练·听力：在今日任务基础上增加半套精听/错题复盘（×1.5）")
    if w == "R" and mode in ("1", "2", "3"):
        lines.append("- 弱项加练·阅读：加一篇限时精读或错题同义替换整理（×1.5）")
    if w == "W" and mode == "1":
        lines.append("- 弱项加练·写作：主任务后再做提纲/句式扩展 15 分钟（×1.5）")
    if w == "S":
        lines.append("- 弱项加练·口语：额外 1 轮 Part2 计时录音+复盘（×1.5）")
    return lines


def _mock_hint_line(phase: int, day_number: int) -> str:
    if phase != 3:
        return ""
    if (day_number - 1) % 7 != 0:
        return ""
    return "- 【本周模考】若尚未完成，请安排一次听读连做计时模拟（整套）"


def _phase_header(state: Dict) -> str:
    done_n, study_d, phase, ptitle, _sched = compute_day_phase(state)
    weak = _weak_label(state)
    return f"📅 Day {done_n}/{study_d} · {ptitle}\n重点突破：{weak}\n"


def build_mode_tasks(mode: str, listening_progress: str, reading_progress: str, recovery_mode: bool, state: Dict) -> Dict:
    """返回 {message, listening_advance, reading_advance}。
    推进粒度：
      - "test"：消耗一整套 Test（听力 4 个 Section / 阅读 3 个 Passage）。
      - "step"：仅推进 1 个 Section / Passage。
      - "none"：不推进。
    """
    _done_days, _study_days, phase, _ptitle, schedule_day = compute_day_phase(state)
    weakest = str(state.get("weakest_skill", "") or "").strip().upper()
    vocab_n = _vocab_base(phase, recovery_mode)
    writing_today = _writing_due(phase, schedule_day)

    listen_label = _listen_test_label(listening_progress)
    read_label = _read_test_label(reading_progress)

    head = _phase_header(state)
    banner = listen_progress_bar_line(listening_progress)
    top = f"{head}📋 今日任务已生成\n{banner}\n────────────────\n"

    if not recovery_mode:
        header = f"{top}已收到模式选择：{mode}\n今日任务如下："
        extras = _weak_extra_lines(weakest, mode, phase, recovery_mode)
        mock_line = _mock_hint_line(phase, schedule_day)

        if mode == "1":
            lines: List[str] = [
                f"- 听力：{listen_label}（全套 Section 1–4）",
                f"- 阅读：{read_label}（全套 Passage 1–3）",
            ]
            if writing_today:
                lines.append(f"- 写作：{TASK1_PLACEHOLDER}")
                lines.append(f"- 写作：{TASK2_PLACEHOLDER}")
            else:
                if phase == 1:
                    lines.append("- 写作：今日无完整计时篇（阶段1：每3天1篇），可做提纲/句式积累 15 分钟")
                elif phase == 2:
                    lines.append("- 写作：今日无完整计时篇（阶段2：每2天1篇），可做提纲/句式积累 15 分钟")
                else:
                    lines.append("- 写作：今日建议仍以审题提纲为主（若已写过正文可复盘改写）")
            lines.append(f"- 词汇：{vocab_n}个")
            lines.append("- 复盘：错题精听 / 同义替换 / 病句改写，约 20–30 分钟")
            lines.extend(extras)
            if mock_line:
                lines.append(mock_line)
            body = "\n".join(lines)
            return {"message": f"{header}\n{body}", "listening_advance": "test", "reading_advance": "test"}

        if mode == "2":
            lines = [
                f"- 听力：{listen_label}（全套 Section 1–4）",
                f"- 阅读：{read_label}（全套 Passage 1–3）",
            ]
            if phase == 3:
                lines.append(f"- 写作：{TASK2_PLACEHOLDER}")
            elif phase == 2 and writing_today:
                lines.append(f"- 写作：{TASK2_PLACEHOLDER}")
            lines.append(f"- 词汇：{vocab_n}个")
            lines.extend(extras)
            if mock_line:
                lines.append(mock_line)
            body = "\n".join(lines)
            return {"message": f"{header}\n{body}", "listening_advance": "test", "reading_advance": "test"}

        # mode 3 极简：1 篇阅读 + 词汇
        lines = [f"- 阅读：{reading_progress}", f"- 词汇：{vocab_n}个"]
        if phase == 2 and weakest == "R":
            lines.append("- 弱项加练·阅读：加 1 篇错题类型专练（×1.5）")
        if phase >= 2 and weakest in ("L", "S", "W"):
            lines.append(f"- 碎片补弱：「{SKILL_CN.get(weakest, weakest)}」加练约 15 分钟（极简模式）")
        if mock_line:
            lines.append(mock_line)
        body = "\n".join(lines)
        return {"message": f"{header}\n{body}", "listening_advance": "none", "reading_advance": "step"}

    # Recovery
    header = f"{top}已收到模式选择：{mode}\n⚠️ Recovery Mode 生效（明日任务减半）\n今日任务如下："
    rvocab = 15
    if mode in ("1", "2"):
        lines = [f"- 阅读：{reading_progress}", f"- 词汇：{rvocab}个"]
        if phase == 2 and weakest == "R":
            lines.append("- 弱项加练·阅读：错题回顾 10 分钟（Recovery）")
        body = "\n".join(lines)
        return {"message": f"{header}\n{body}", "listening_advance": "none", "reading_advance": "step"}
    body = f"- 词汇：{rvocab}个"
    return {"message": f"{header}\n{body}", "listening_advance": "none", "reading_advance": "none"}
