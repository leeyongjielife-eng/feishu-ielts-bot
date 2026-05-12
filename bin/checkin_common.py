"""
结构化打卡：解析、综合完成率、规则点评、状态字段合并。
与 message-router / parse-checkin 共用。
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional, Tuple

from progress_bar import make_bar

# 阅读「等效正确率」：错题数 → 百分制（与示例 错5题→73% 对齐）
READING_PCT_PER_ERROR = 5.4

CHECKIN_FORMAT_HINT = (
    "打卡请使用以下多行格式（可复制后改数字）：\n"
    "打卡\n"
    "听力：完成 正确率85% 错3题\n"
    "阅读：完成 错5题 用时55分钟\n"
    "写作：完成\n"
    "词汇：完成\n"
    "说明：听力/阅读「未完成」可写「未完成」；完成时请带正确率/错题/用时。"
)


def format_percent(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)}%"
    return f"{value:.2f}%"


def evaluate_state(completion_rate: float, current_fail_streak: int, current_streak: int) -> Dict[str, Any]:
    fail_streak = current_fail_streak + 1 if completion_rate < 50 else 0
    streak = current_streak + 1 if completion_rate >= 80 else 0
    if completion_rate < 50 or fail_streak >= 2:
        return {"state": "🔴 差", "fail_streak": fail_streak, "streak": streak, "recovery_mode": fail_streak >= 2}
    if completion_rate >= 80:
        return {"state": "🟢 正常", "fail_streak": fail_streak, "streak": streak, "recovery_mode": False}
    return {"state": "🟡 不稳定", "fail_streak": fail_streak, "streak": streak, "recovery_mode": False}


def reading_pct_from_errors(errors: int) -> float:
    raw = 100.0 - READING_PCT_PER_ERROR * float(max(0, errors))
    return float(max(0.0, min(100.0, round(raw))))


def plan_day_num(state: Dict[str, Any]) -> Tuple[int, int]:
    study_days = int(state.get("study_days") or 60)
    if study_days < 1:
        study_days = 60
    start_date = state.get("start_date")
    today = date.today()
    if start_date:
        try:
            s0 = datetime.strptime(str(start_date)[:10], "%Y-%m-%d").date()
            elapsed = (today - s0).days + 1
        except ValueError:
            elapsed = 1
    else:
        elapsed = 1
    day_num = max(1, min(elapsed, study_days))
    return day_num, study_days


def _done_flag(line_body: str) -> Optional[bool]:
    if re.search(r"未完成|未做|跳过", line_body):
        return False
    if re.search(r"完成", line_body) and not re.search(r"未完成", line_body):
        return True
    if "✅" in line_body:
        return True
    return None


def parse_structured_checkin(content: str) -> Optional[Dict[str, Any]]:
    raw = (content or "").strip()
    if not raw.startswith("打卡"):
        return None
    lines = [ln.strip() for ln in raw.splitlines() if ln.strip()]
    if len(lines) < 2:
        return None
    if lines[0].replace("：", ":").rstrip(":").strip() != "打卡":
        return None

    sections: Dict[str, str] = {}
    for ln in lines[1:]:
        m = re.match(r"^(听力|阅读|写作|词汇)\s*[:：]\s*(.+)$", ln)
        if not m:
            return None
        sections[m.group(1)] = m.group(2).strip()

    if set(sections.keys()) != {"听力", "阅读", "写作", "词汇"}:
        return None

    listen_body = sections["听力"]
    read_body = sections["阅读"]
    write_body = sections["写作"]
    vocab_body = sections["词汇"]

    lw = _done_flag(listen_body)
    rw = _done_flag(read_body)
    ww = _done_flag(write_body)
    vw = _done_flag(vocab_body)
    if lw is None or rw is None or ww is None or vw is None:
        return None

    listen_acc: Optional[float] = None
    listen_err: Optional[int] = None
    if lw:
        ma = re.search(r"正确率\s*(\d+(?:\.\d+)?)\s*%", listen_body)
        me = re.search(r"错\s*(\d+)\s*题", listen_body)
        if not ma or not me:
            return None
        listen_acc = float(ma.group(1))
        listen_err = int(me.group(1))
        if listen_acc < 0 or listen_acc > 100:
            return None

    read_err: Optional[int] = None
    read_min: Optional[int] = None
    if rw:
        me = re.search(r"错\s*(\d+)\s*题", read_body)
        if not me:
            return None
        read_err = int(me.group(1))
        mt = re.search(r"用时\s*(\d+)\s*分钟", read_body)
        if mt:
            read_min = int(mt.group(1))

    return {
        "listen_done": lw,
        "listen_accuracy": listen_acc,
        "listen_errors": listen_err,
        "read_done": rw,
        "read_errors": read_err,
        "read_minutes": read_min,
        "write_done": ww,
        "vocab_done": vw,
    }


def composite_completion_rate(parsed: Dict[str, Any]) -> float:
    l_pct = float(parsed["listen_accuracy"]) if parsed["listen_done"] and parsed["listen_accuracy"] is not None else 0.0
    r_pct = reading_pct_from_errors(int(parsed["read_errors"])) if parsed["read_done"] and parsed["read_errors"] is not None else 0.0
    w_pct = 100.0 if parsed["write_done"] else 0.0
    v_pct = 100.0 if parsed["vocab_done"] else 0.0
    return round((l_pct + r_pct + w_pct + v_pct) / 4.0, 2)


def _writing_scores_dict(state: Dict[str, Any]) -> Dict[str, float]:
    w = state.get("weekly_writing_avg") or state.get("last_writing_scores") or {}
    if not isinstance(w, dict):
        return {}
    out: Dict[str, float] = {}
    for k in ("TR", "CC", "LR", "GRA"):
        try:
            out[k] = float(w.get(k, 0))
        except (TypeError, ValueError):
            out[k] = 0.0
    return out


def build_checkin_commentary(parsed: Dict[str, Any], state: Dict[str, Any], evaluated: Dict[str, Any]) -> List[str]:
    bullets: List[str] = []

    if parsed["read_done"] and parsed.get("read_minutes") and int(parsed["read_minutes"]) > 50:
        bullets.append("阅读用时偏长，建议练习 skimming 技巧。")

    if parsed["listen_done"] and parsed["listen_accuracy"] is not None:
        acc = float(parsed["listen_accuracy"])
        if acc < 70:
            bullets.append("听力正确率偏低，注意预测答案与信号词定位。")
        elif acc >= 85:
            bullets.append("听力表现优秀，可尝试更难 Section 或倍速精听。")
        else:
            bullets.append("听力表现良好，注意 Section4 细节题与拼写。")

    if parsed["read_done"] and parsed["read_errors"] is not None:
        err = int(parsed["read_errors"])
        if err > 5:
            bullets.append("阅读错题较多，建议加强时间管理与略读抓主旨。")
        elif err <= 3:
            bullets.append("阅读错题控制不错，可在限时条件下提升整体速度。")

    scores = _writing_scores_dict(state)
    if scores:
        for dim, label in (("TR", "TR"), ("CC", "CC"), ("LR", "LR"), ("GRA", "GRA")):
            v = scores.get(dim, 0)
            if 0 < v < 6.0:
                bullets.append(f"写作{label} 未达 6.0，建议对照评分标准专项改写 1 篇并复盘。")

    if parsed["write_done"]:
        bullets.append("坚持写作提交，等待 AI 批改或手动 #批改结果 回填。")

    if int(evaluated.get("fail_streak", 0) or 0) >= 2 or evaluated.get("recovery_mode"):
        bullets.append("已连续多日完成率偏低，温和建议：缩小任务量、优先稳定打卡，必要时进入 Recovery Mode。")

    # 去重保序
    seen = set()
    out: List[str] = []
    for b in bullets:
        if b not in seen:
            seen.add(b)
            out.append(b)
    if not out:
        out.append("保持节奏：四项尽量齐头并进，明天继续打卡记录细项数据。")
    return out[:6]


def format_structured_checkin_reply(
    parsed: Dict[str, Any],
    state: Dict[str, Any],
    evaluated: Dict[str, Any],
    completion_rate: float,
) -> str:
    day_num, study_days = plan_day_num(state)
    lines: List[str] = [
        f"✅ 打卡收到 Day {day_num}/{study_days}",
        "",
        "今日表现：",
    ]

    if parsed["listen_done"] and parsed["listen_accuracy"] is not None:
        acc = float(parsed["listen_accuracy"])
        err = int(parsed["listen_errors"] or 0)
        lines.append(f"听力：{make_bar(acc, 100.0)}  {format_percent(acc)}  (-{err}题)")
    else:
        lines.append("听力：⬜ 未完成")

    if parsed["read_done"] and parsed["read_errors"] is not None:
        rp = reading_pct_from_errors(int(parsed["read_errors"]))
        err = int(parsed["read_errors"])
        lines.append(f"阅读：{make_bar(rp, 100.0)}  {format_percent(rp)}  (-{err}题)")
    else:
        lines.append("阅读：⬜ 未完成")

    lines.append("写作：✅ 完成" if parsed["write_done"] else "写作：⬜ 未完成")
    lines.append("词汇：✅ 完成" if parsed["vocab_done"] else "词汇：⬜ 未完成")
    lines.append("")
    lines.append(f"综合完成率：{make_bar(float(completion_rate), 100.0)}  {format_percent(completion_rate)}")
    lines.append(f"连续天数：{make_bar(float(evaluated['streak']), 10.0)}  {evaluated['streak']}天")
    lines.append(f"当前状态：{evaluated['state']}")
    lines.append("")
    lines.append("📌 点评：")
    for b in build_checkin_commentary(parsed, state, evaluated):
        lines.append(f"- {b}")
    if evaluated.get("recovery_mode"):
        lines.append("")
        lines.append("⚠️ Recovery Mode 已触发：明日任务减半。")
    return "\n".join(lines)


def apply_checkin_to_state(
    state: Dict[str, Any],
    parsed: Dict[str, Any],
    evaluated: Dict[str, Any],
    completion_rate: float,
    today_str: str,
    message_id: str,
) -> Dict[str, Any]:
    history: List[Dict[str, Any]] = list(state.get("checkin_history") or [])
    if not isinstance(history, list):
        history = []

    row: Dict[str, Any] = {
        "date": today_str,
        "completion_rate": float(completion_rate),
        "listening_accuracy": float(parsed["listen_accuracy"]) if parsed["listen_done"] and parsed["listen_accuracy"] is not None else None,
        "listening_errors": int(parsed["listen_errors"]) if parsed["listen_done"] and parsed["listen_errors"] is not None else None,
        "reading_errors": int(parsed["read_errors"]) if parsed["read_done"] and parsed["read_errors"] is not None else None,
        "reading_minutes": int(parsed["read_minutes"]) if parsed.get("read_minutes") is not None else None,
        "reading_pct": reading_pct_from_errors(int(parsed["read_errors"])) if parsed["read_done"] and parsed["read_errors"] is not None else None,
        "writing_done": bool(parsed["write_done"]),
        "vocab_done": bool(parsed["vocab_done"]),
    }

    if history and str(history[-1].get("date")) == today_str:
        history[-1] = row
    else:
        history.append(row)
    if len(history) > 120:
        history = history[-120:]

    tail = history[-7:]
    listen_hist: List[Optional[float]] = []
    read_err_hist: List[Optional[int]] = []
    read_pct_hist: List[float] = []
    for r in tail:
        la = r.get("listening_accuracy")
        if la is None:
            listen_hist.append(None)
        else:
            try:
                listen_hist.append(float(la))
            except (TypeError, ValueError):
                listen_hist.append(None)
        re_ = r.get("reading_errors")
        if re_ is None:
            read_err_hist.append(None)
        else:
            try:
                read_err_hist.append(int(re_))
            except (TypeError, ValueError):
                read_err_hist.append(None)
        rp = r.get("reading_pct")
        if rp is not None:
            try:
                read_pct_hist.append(float(rp))
            except (TypeError, ValueError):
                pass

    listen_for_mean = [x for x in listen_hist if x is not None]
    w_avg = round(sum(listen_for_mean) / len(listen_for_mean), 1) if listen_for_mean else 0.0
    r_avg = round(sum(read_pct_hist) / len(read_pct_hist), 1) if read_pct_hist else 0.0

    return {
        "completion_rate": completion_rate,
        "last_checkin_date": today_str,
        "last_checkin_message_id": message_id,
        "state": evaluated["state"],
        "fail_streak": evaluated["fail_streak"],
        "streak": evaluated["streak"],
        "recovery_mode": evaluated["recovery_mode"],
        "checkin_history": history,
        "listening_accuracy_history": listen_hist,
        "reading_error_history": read_err_hist,
        "weekly_listening_avg": w_avg,
        "weekly_reading_avg": r_avg,
    }


def is_structured_checkin_content(content: str) -> bool:
    return parse_structured_checkin(content) is not None


def classify_checkin_message(content: str) -> str:
    """structured | legacy | hint | none"""
    if parse_structured_checkin(content):
        return "structured"
    c = (content or "").strip()
    if re.match(r"^\s*打卡\s*[:：]?\s*\d+\s*/\s*\d+\s*$", c):
        return "legacy"
    if c.startswith("打卡"):
        return "hint"
    return "none"
