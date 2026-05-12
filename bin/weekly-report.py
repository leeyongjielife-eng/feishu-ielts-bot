#!/usr/bin/env python3
"""
每周日 21:00 由 launchd 触发：读取 user_state.json，生成并发送本周学习报告。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = Path(__file__).resolve().parent
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
from progress_bar import listen_progress_bar_line, make_bar  # noqa: E402

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = Path(os.environ.get("WEEKLY_REPORT_LOG", str(LOG_DIR / "weekly-report.log")))
STATE_FILE = Path(os.environ.get("STATE_FILE", str(ROOT / "user_state.json")))

CHAT_ID = os.environ.get("FEISHU_IELTS_CHAT_ID", "oc_99000aba52da6814c200481c4dedf1ea")
os.environ["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:{os.environ.get('PATH', '')}"
default_home = str(ROOT.parent.parent)
os.environ["HOME"] = os.environ.get("HOME", default_home)
os.environ["USER"] = os.environ.get("USER", Path(os.environ["HOME"]).name)
os.environ["LOGNAME"] = os.environ.get("LOGNAME", os.environ["USER"])

LARK_CLI = os.environ.get("LARK_CLI") or shutil.which("lark-cli") or "/opt/homebrew/bin/lark-cli"

SKILL_CN = {"L": "听力", "R": "阅读", "W": "写作", "S": "口语"}
DEFAULT_PROGRESS = "Cam10 Test1 Section1"
WD = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]


def log(msg: str) -> None:
    line = f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}] {msg}"
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def load_state() -> Dict[str, Any]:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(state: Dict[str, Any]) -> None:
    tmp_file = STATE_FILE.with_suffix(".json.tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp_file.replace(STATE_FILE)


def monday_sunday_of_calendar_week(today: date) -> Tuple[date, date]:
    monday = today - timedelta(days=today.weekday())
    sunday = monday + timedelta(days=6)
    return monday, sunday


def week_entries_full(history: List[Dict[str, Any]], monday: date, sunday: date) -> List[Dict[str, Any]]:
    d0, d1 = monday.isoformat(), sunday.isoformat()
    out: List[Dict[str, Any]] = []
    for row in history:
        if not isinstance(row, dict):
            continue
        ds = str(row.get("date", ""))
        if d0 <= ds <= d1:
            out.append(dict(row))
    out.sort(key=lambda r: str(r.get("date", "")))
    return out


def plan_week_number(state: Dict[str, Any], monday: date) -> int:
    sd = state.get("start_date")
    if not sd:
        return int(monday.isocalendar()[1])
    try:
        s0 = datetime.strptime(str(sd)[:10], "%Y-%m-%d").date()
    except ValueError:
        return 1
    sm = s0 - timedelta(days=s0.weekday())
    idx = (monday - sm).days // 7 + 1
    return max(1, idx)


def _mean(nums: List[float]) -> Optional[float]:
    if not nums:
        return None
    return round(sum(nums) / len(nums), 2)


def _delta_arrow(prev: Optional[float], cur: Optional[float], *, higher_is_better: bool) -> str:
    if prev is None or cur is None:
        return "→持平"
    diff = round(cur - prev, 2)
    if abs(diff) < 0.05:
        return "→持平"
    if higher_is_better:
        if diff > 0:
            return f"↑较上周+{diff:g}%"
        return f"↓较上周{diff:g}%"
    if diff < 0:
        return f"↓较上周{-diff:g}题"
    return f"↑较上周+{diff:g}题"


def _writing_dim_arrow(prev_w: Dict[str, Any], cur_w: Dict[str, Any], dim: str) -> str:
    if dim not in cur_w:
        return "→持平"
    try:
        c = float(cur_w[dim])
    except (TypeError, ValueError):
        return "→持平"
    if c <= 0:
        return "→持平"
    if not prev_w or dim not in prev_w:
        return "→持平"
    try:
        p = float(prev_w[dim])
    except (TypeError, ValueError):
        return "→持平"
    if p <= 0:
        return "→持平"
    diff = round(c - p, 1)
    if abs(diff) < 0.05:
        return "→持平"
    if diff > 0:
        return f"↑+{diff:g}"
    return f"↓{diff:g}"


def reading_hardest_type(avg_err: float) -> str:
    if avg_err > 5:
        return "TRUE/FALSE/NOT GIVEN"
    if avg_err > 3:
        return "匹配题"
    return "标题配对"


def est_listening_band(avg_pct: float) -> float:
    if avg_pct <= 0:
        return 5.0
    if avg_pct < 60:
        return 5.0
    if avg_pct < 70:
        return 5.5
    if avg_pct < 80:
        return 6.0
    if avg_pct < 85:
        return 6.5
    return min(7.5, 6.5 + (avg_pct - 85.0) * 0.05)


def est_reading_band(avg_err: float) -> float:
    b = 7.0 - avg_err * 0.22
    return max(5.0, min(8.0, round(b, 1)))


def build_next_week_advice(state: str, avg_completion: float, weakest: str) -> str:
    w = SKILL_CN.get(weakest, weakest or "薄弱项")
    if "🔴" in state or avg_completion < 50:
        return (
            "建议切换「极简模式」，优先恢复每日打卡习惯，把完成率稳定在 50% 以上再加码。"
            " 下周以「每天完成最小任务」为主，避免连续断档。"
        )
    if "🟡" in state:
        return (
            f"建议保持「减负模式」，本周把额外时间优先给「{w}」专项（结合错题与精读/精听）。"
            " 保持打卡频率的同时，每周固定复盘一次弱项。"
        )
    if "🟢" in state:
        return (
            "建议挑战「标准模式」，并刻意增加写作 Task2 频次（计时+复盘），冲击更高分段。"
            " 听读可维持当前进度，写作建议每周至少完整计时 2 篇。"
        )
    return "建议先稳定打卡节奏，再按目标分数逐步提高任务强度。"


def build_week_focus(state: Dict[str, Any], read_err_avg: float, listen_avg: float, weakest_type: str) -> List[str]:
    bullets: List[str] = []
    if read_err_avg > 5:
        bullets.append("阅读：每天专项练习判断题，目标错题<3题。")
    elif read_err_avg > 3:
        bullets.append("阅读：限时完成一套题，复盘错题定位同义替换。")
    else:
        bullets.append("阅读：保持错题记录，尝试压缩单篇用时。")

    wavg = state.get("weekly_writing_avg") if isinstance(state.get("weekly_writing_avg"), dict) else {}
    low_dim = None
    for dim in ("TR", "CC", "LR", "GRA"):
        try:
            v = float(wavg.get(dim, 9))
        except (TypeError, ValueError):
            v = 9.0
        if v < 6.0:
            low_dim = dim
            break
    if low_dim:
        bullets.append(f"写作：重点提升 {low_dim} 分数，审题与段落结构要更严谨。")
    else:
        bullets.append("写作：保持每周至少 2 篇计时练习，并回填 #批改结果 以便追踪。")

    if listen_avg > 0 and listen_avg < 70:
        bullets.append("听力：加强预测答案与关键词信号，精听错题句。")
    elif listen_avg >= 85:
        bullets.append("听力：维持优势，可挑战 Section4 满分或 1.2 倍速跟读。")
    else:
        bullets.append("听力：保持现有水平，巩固 Section3/4 细节题与拼写。")

    if int(state.get("fail_streak", 0) or 0) >= 2 or state.get("recovery_mode"):
        bullets.append("状态：已连续多日承压，温和建议缩小日任务量并优先稳定打卡（Recovery Mode）。")
    else:
        bullets.append(f"弱项提醒：本周阅读难点集中在「{weakest_type}」，配套做 10 道同类小题。")
    return bullets[:4]


def format_report(state: Dict[str, Any], report_day: date) -> str:
    monday, sunday = monday_sunday_of_calendar_week(report_day)
    history = state.get("checkin_history") if isinstance(state.get("checkin_history"), list) else []
    entries = week_entries_full(history, monday, sunday)

    study_days = len({e["date"] for e in entries})
    if study_days == 0 and state.get("last_checkin_date"):
        lcd = str(state["last_checkin_date"])
        if monday.isoformat() <= lcd <= sunday.isoformat():
            study_days = 1
            try:
                cr = float(state.get("completion_rate", 0))
            except (TypeError, ValueError):
                cr = 0.0
            entries = [{"date": lcd, "completion_rate": cr}]

    avg_completion = round(sum(float(e.get("completion_rate", 0) or 0) for e in entries) / len(entries), 2) if entries else 0.0
    streak = int(state.get("streak", 0) or 0)
    cur_state = str(state.get("state", "（暂无）"))
    week_n = plan_week_number(state, monday)

    listen_lines: List[str] = []
    for e in entries:
        acc = e.get("listening_accuracy")
        if acc is None:
            continue
        try:
            ds = datetime.strptime(str(e["date"])[:10], "%Y-%m-%d").date()
            label = WD[ds.weekday()]
            pct = float(acc)
            listen_lines.append(f"  {label} {make_bar(pct, 100.0)} {int(round(pct))}%")
        except (TypeError, ValueError, KeyError):
            continue

    listen_vals = [float(e["listening_accuracy"]) for e in entries if e.get("listening_accuracy") is not None]
    this_listen_avg = _mean(listen_vals)
    prev_listen = state.get("prev_week_listening_avg")
    try:
        prev_listen_f = float(prev_listen) if prev_listen is not None else None
    except (TypeError, ValueError):
        prev_listen_f = None
    listen_delta = _delta_arrow(prev_listen_f, this_listen_avg, higher_is_better=True)

    read_errs = []
    for e in entries:
        if e.get("reading_errors") is not None:
            try:
                read_errs.append(float(e["reading_errors"]))
            except (TypeError, ValueError):
                pass
    this_read_err_avg = _mean(read_errs) if read_errs else None
    prev_re = state.get("prev_week_reading_errors_avg")
    try:
        prev_re_f = float(prev_re) if prev_re is not None else None
    except (TypeError, ValueError):
        prev_re_f = None
    read_err_delta = _delta_arrow(prev_re_f, this_read_err_avg, higher_is_better=False) if this_read_err_avg is not None else "→持平"

    avg_err_for_type = float(this_read_err_avg) if this_read_err_avg is not None else 0.0
    weakest_type = reading_hardest_type(avg_err_for_type)

    cur_w = dict(state.get("weekly_writing_avg") or {})
    if not cur_w:
        cur_w = {k: float(v) for k, v in dict(state.get("last_writing_scores") or {}).items() if k in ("TR", "CC", "LR", "GRA")}
    prev_w = state.get("prev_week_writing_avg") if isinstance(state.get("prev_week_writing_avg"), dict) else {}
    writing_trend_lines: List[str] = []
    for dim in ("TR", "CC", "LR", "GRA"):
        try:
            v = float(cur_w.get(dim, 0))
        except (TypeError, ValueError):
            v = 0.0
        if v <= 0:
            writing_trend_lines.append(f"{dim}:  （本周暂无写作四维数据）")
        else:
            arrow = _writing_dim_arrow(prev_w, cur_w, dim)
            writing_trend_lines.append(f"{dim}:  {make_bar(v, 9.0)} {v:g}  {arrow}")

    lw = float(state.get("weekly_listening_avg", 0) or 0) or (this_listen_avg or 0.0)
    re_avg = float(this_read_err_avg) if this_read_err_avg is not None else 0.0
    w_scores = cur_w if cur_w else state.get("last_writing_scores") or {}
    try:
        w_mean = sum(float(w_scores.get(k, 0)) for k in ("TR", "CC", "LR", "GRA")) / 4.0
    except (TypeError, ValueError):
        w_mean = 0.0
    w_band = round(w_mean, 1) if w_mean > 0 else 0.0

    init = state.get("initial_scores") if isinstance(state.get("initial_scores"), dict) else {}
    try:
        init_l = float(init.get("L", 0) or 0)
    except (TypeError, ValueError):
        init_l = 0.0
    try:
        init_r = float(init.get("R", 0) or 0)
    except (TypeError, ValueError):
        init_r = 0.0

    if this_listen_avg is not None:
        l_band = est_listening_band(float(this_listen_avg))
    elif lw > 0:
        l_band = est_listening_band(lw)
    elif init_l > 0:
        l_band = init_l
    else:
        l_band = 0.0

    if this_read_err_avg is not None:
        r_band = est_reading_band(float(this_read_err_avg))
    elif re_avg > 0:
        r_band = est_reading_band(re_avg)
    elif init_r > 0:
        r_band = init_r
    else:
        r_band = 0.0

    try:
        s_band = float(init.get("S", 0)) if init.get("S") is not None else 0.0
    except (TypeError, ValueError):
        s_band = 0.0
    s_display = f"{s_band:g}" if s_band > 0 else "-"

    try:
        tgt = float(state.get("target_score", 6.5) or 6.5)
    except (TypeError, ValueError):
        tgt = 6.5
    parts = [x for x in (l_band, r_band, w_band) if x and x > 0]
    overall = round(sum(parts) / len(parts), 1) if parts else 0.0
    overall_bar = make_bar(overall, tgt) if overall > 0 else make_bar(0, tgt)

    listening = str(state.get("listening_progress") or DEFAULT_PROGRESS)
    reading = str(state.get("reading_progress") or DEFAULT_PROGRESS)
    listen_bar = listen_progress_bar_line(listening)
    read_bar = listen_progress_bar_line(reading)

    focus = build_week_focus(state, re_avg, lw, weakest_type)
    weakest_skill = str(state.get("weakest_skill", "") or "").strip().upper() or "L"
    advice_tail = build_next_week_advice(cur_state, avg_completion, weakest_skill if weakest_skill != "—" else "L")

    lines: List[str] = [
        f"📊 本周学习报告 Week {week_n}",
        "────────────────",
        f"学习天数：{make_bar(float(study_days), 7.0)}  {study_days}/7天",
        f"平均完成率：{make_bar(avg_completion, 100.0)}  {avg_completion}%",
        f"连续天数：{make_bar(float(streak), 10.0)}  {streak}天",
        "",
        "📈 本周各项表现：",
        "听力正确率：",
    ]
    if listen_lines:
        lines.extend(listen_lines)
    else:
        lines.append("  （本周无听力正确率记录）")
    if this_listen_avg is not None:
        lines.append(f"  周均：{make_bar(float(this_listen_avg), 100.0)} {int(round(this_listen_avg))}%  {listen_delta}")
    else:
        lines.append("  周均：（暂无）")

    lines.append("")
    lines.append("阅读错题数：")
    if this_read_err_avg is not None:
        lines.append(f"  周均错题：{this_read_err_avg:g}题  {read_err_delta}")
    else:
        lines.append("  周均错题：（暂无）")
    lines.append(f"  最难题型：{weakest_type}")

    lines.append("")
    lines.append("写作趋势：")
    lines.extend(writing_trend_lines)

    lines.append("")
    lines.append("预估当前分数：")
    lines.append(f"  听力：{l_band:g}  阅读：{r_band:g}  写作：{w_band:g}  口语：{s_display}")
    lines.append(f"  综合估分：{overall_bar}  {overall:g} / 目标{tgt:g}")

    lines.append("")
    lines.append("────────────────")
    lines.append(f"📌 本周弱项：阅读「{weakest_type}」题型")
    lines.append("")
    lines.append("🎯 下周重点：")
    for i, b in enumerate(focus, 1):
        lines.append(f"{i}. {b}")
    lines.append("")
    lines.append(f"任务进度：\n听力：{listen_bar}\n阅读：{read_bar}")
    lines.append("")
    lines.append(f"附：状态参考 — {advice_tail}")
    return "\n".join(lines)


def compute_week_snapshot(entries: List[Dict[str, Any]]) -> Tuple[Optional[float], Optional[float]]:
    listen_vals = [float(e["listening_accuracy"]) for e in entries if e.get("listening_accuracy") is not None]
    read_errs = [float(e["reading_errors"]) for e in entries if e.get("reading_errors") is not None]
    return _mean(listen_vals), _mean(read_errs)


def send_message(text: str, idempotency_key: str) -> None:
    cmd = [
        LARK_CLI,
        "im",
        "+messages-send",
        "--as",
        "user",
        "--chat-id",
        CHAT_ID,
        "--text",
        text,
        "--idempotency-key",
        idempotency_key,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"lark-cli exit={result.returncode} stderr={result.stderr.strip()} stdout={result.stdout.strip()}"
        )


def main() -> int:
    log(f"start chat_id={CHAT_ID} state_file={STATE_FILE}")
    if not Path(LARK_CLI).exists():
        log(f"ERROR: lark-cli not found at {LARK_CLI}")
        return 127

    report_day = date.today()
    monday, sunday = monday_sunday_of_calendar_week(report_day)
    iso_year, iso_week, _ = monday.isocalendar()
    idem = f"ielts-weekly-report-{iso_year}-W{iso_week:02d}"

    state = load_state()
    history = state.get("checkin_history") if isinstance(state.get("checkin_history"), list) else []
    entries = week_entries_full(history, monday, sunday)
    body = format_report(state, report_day)

    try:
        send_message(body, idem)
    except Exception as exc:
        log(f"ERROR: send failed: {exc}")
        return 1

    l_snap, r_snap = compute_week_snapshot(entries)
    state2 = load_state()
    w_snap = state2.get("weekly_writing_avg") if isinstance(state2.get("weekly_writing_avg"), dict) else {}
    state2["prev_week_listening_avg"] = l_snap
    state2["prev_week_reading_errors_avg"] = r_snap
    state2["prev_week_writing_avg"] = {k: round(float(w_snap[k]), 1) for k in ("TR", "CC", "LR", "GRA") if k in w_snap}
    try:
        write_state(state2)
    except Exception as exc:
        log(f"WARN: could not persist week snapshot: {exc}")

    log(f"OK: sent idempotency_key={idem} week={monday.isoformat()}..{sunday.isoformat()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
