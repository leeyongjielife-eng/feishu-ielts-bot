#!/usr/bin/env python3
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

from ai_caller import call_ai_with_fallback
from checkin_common import (
    CHECKIN_FORMAT_HINT,
    apply_checkin_to_state,
    apply_daily_checkin_to_state,
    classify_checkin_message,
    composite_completion_rate,
    composite_completion_rate_daily,
    evaluate_state,
    format_daily_checkin_reply,
    format_structured_checkin_reply,
    parse_daily_checkin,
    parse_structured_checkin,
)
from checkin_spec import (
    NO_SPEC_PROMPT,
    build_checkin_spec_payload,
    checkin_spec_state_updates,
    format_checkin_spec_message,
    resolve_active_spec,
)
from checkin_motivation_image import try_send_checkin_motivation
from init_plan import format_score_analysis_message, generate_study_roadmap_text
from mode_tasks import build_mode_tasks
from progress_bar import make_bar
from task_progress import build_today_task_progress, progress_updates_after_checkin

ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = Path(__file__).resolve().parent
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = Path(os.environ.get("LOG_FILE", str(LOG_DIR / "message-router.log")))
STATE_FILE = Path(os.environ.get("STATE_FILE", str(ROOT / "user_state.json")))
LOCK_FILE = Path(os.environ.get("STATE_LOCK_FILE", str(ROOT / "user_state.json.lock")))

CHAT_ID = os.environ.get("FEISHU_IELTS_CHAT_ID", "oc_99000aba52da6814c200481c4dedf1ea")

os.environ["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:{os.environ.get('PATH', '')}"
default_home = str(ROOT.parent.parent)
os.environ["HOME"] = os.environ.get("HOME", default_home)
os.environ["USER"] = os.environ.get("USER", Path(os.environ["HOME"]).name)
os.environ["LOGNAME"] = os.environ.get("LOGNAME", os.environ["USER"])

LARK_CLI = os.environ.get("LARK_CLI") or shutil.which("lark-cli") or "/opt/homebrew/bin/lark-cli"
WRITING_RE = re.compile(r"^\s*#写作提交\b", re.IGNORECASE)
MANUAL_REVIEW_RE = re.compile(r"^\s*#批改结果\b", re.IGNORECASE)
INIT_RESET_RE = re.compile(r"^\s*#重新初始化\s*$", re.IGNORECASE)
INIT_SCORES_RE = re.compile(
    r"^\s*#我的成绩\s+L\s*:\s*([0-9](?:\.[05])?)\s+R\s*:\s*([0-9](?:\.[05])?)\s+W\s*:\s*([0-9](?:\.[05])?)\s+S\s*:\s*([0-9](?:\.[05])?)\s+目标\s*:\s*([0-9](?:\.[05])?)\s+天数\s*:\s*(\d{1,3})\s*$",
    re.IGNORECASE,
)
PROGRESS_RE = re.compile(r"^Cam(\d+)\s+Test(\d+)\s+Section(\d+)$")
READING_PROGRESS_RE = re.compile(r"^Cam(\d+)\s+Test(\d+)\s+Passage(\d+)$")
LEGACY_READING_SECTION_RE = re.compile(r"^Cam(\d+)\s+Test(\d+)\s+Section(\d+)$")
MODE_RE = re.compile(r"^\s*([123])\s*$")

DEFAULT_PROGRESS = "Cam10 Test1 Section1"
DEFAULT_READING_PROGRESS = "Cam10 Test1 Passage1"
MAX_BOOK = 18
MAX_TEST_PER_BOOK = 4
MAX_SECTION = 4
MAX_PASSAGE = 3
WRITING_UNAVAILABLE_MESSAGE = (
    "⚠️ AI批改暂时不可用。请将你的作文粘贴到任意AI，附上评分标准：\n"
    "按雅思四维评分：TR(任务回应)/CC(连贯衔接)/LR(词汇丰富度)/GRA(语法准确性)，每项0-9分，输出评分+3条改进建议。\n"
    "完成后请回复 #批改结果 + 评分内容"
)
MANUAL_FORMAT_HINT = (
    "格式不正确，请使用以下模板（分数行可不带进度条）：\n"
    "#批改结果\n"
    "TR: x.x\n"
    "CC: x.x\n"
    "LR: x.x\n"
    "GRA: x.x\n"
    "建议1: xxx\n"
    "建议2: xxx\n"
    "建议3: xxx"
)
INIT_PROMPT = (
    "📌 初始化问卷\n"
    "请输入你最近一次雅思模考成绩（没有则估分）：\n"
    "格式：#我的成绩 L:x.x R:x.x W:x.x S:x.x 目标:6.5 天数:60"
)


def log(message: str) -> None:
    line = f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}] {message}"
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def run_lark_list() -> Dict:
    cmd = [
        LARK_CLI,
        "im",
        "+chat-messages-list",
        "--as",
        "user",
        "--chat-id",
        CHAT_ID,
        "--page-size",
        "50",
        "--format",
        "json",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"lark-cli exit={result.returncode} stderr={result.stderr.strip()} stdout={result.stdout.strip()}"
        )
    return json.loads(result.stdout)


def send_message(text: str) -> None:
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
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"lark-cli exit={result.returncode} stderr={result.stderr.strip()} stdout={result.stdout.strip()}"
        )


@contextmanager
def state_lock():
    LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    with LOCK_FILE.open("a+") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def load_state() -> Dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def write_state(state: Dict) -> None:
    tmp_file = STATE_FILE.with_suffix(".json.tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp_file.replace(STATE_FILE)


def _migrate_reading_progress(value: str) -> str:
    """旧版阅读使用 Section1..4；阅读改为 Passage1..3（Section4 截顶为 Passage3）。"""
    if not value:
        return DEFAULT_READING_PROGRESS
    s = value.strip()
    if READING_PROGRESS_RE.match(s):
        return s
    m = LEGACY_READING_SECTION_RE.match(s)
    if not m:
        return DEFAULT_READING_PROGRESS
    book, test, section = int(m.group(1)), int(m.group(2)), int(m.group(3))
    passage = min(MAX_PASSAGE, max(1, section))
    return f"Cam{book} Test{test} Passage{passage}"


def ensure_defaults(state: Dict) -> None:
    if not state.get("listening_progress"):
        state["listening_progress"] = DEFAULT_PROGRESS
    rp = state.get("reading_progress")
    if not rp:
        state["reading_progress"] = DEFAULT_READING_PROGRESS
    elif not READING_PROGRESS_RE.match(str(rp).strip()):
        state["reading_progress"] = _migrate_reading_progress(str(rp))
    if "fail_streak" not in state:
        state["fail_streak"] = 0
    if "streak" not in state:
        state["streak"] = 0
    if "recovery_mode" not in state:
        state["recovery_mode"] = False
    if "listening_accuracy_history" not in state:
        state["listening_accuracy_history"] = []
    if "reading_error_history" not in state:
        state["reading_error_history"] = []
    if "weekly_listening_avg" not in state:
        state["weekly_listening_avg"] = 0.0
    if "weekly_reading_avg" not in state:
        state["weekly_reading_avg"] = 0.0
    if "weekly_writing_avg" not in state:
        state["weekly_writing_avg"] = {}
    if "study_plan_sent" not in state:
        state["study_plan_sent"] = False
    if "prev_week_listening_avg" not in state:
        state["prev_week_listening_avg"] = None
    if "prev_week_reading_errors_avg" not in state:
        state["prev_week_reading_errors_avg"] = None
    if "prev_week_writing_avg" not in state:
        state["prev_week_writing_avg"] = {}
    if "last_checkin_motivation_image" not in state:
        state["last_checkin_motivation_image"] = ""
    if "missed_days" not in state:
        state["missed_days"] = 0
    if "missed_checkin_record_dates" not in state or not isinstance(state.get("missed_checkin_record_dates"), list):
        state["missed_checkin_record_dates"] = []
    if "last_missed_checkin_processed_for" not in state:
        state["last_missed_checkin_processed_for"] = ""
    if "checkin_spec_date" not in state:
        state["checkin_spec_date"] = ""
    if "checkin_spec_mode" not in state:
        state["checkin_spec_mode"] = ""
    if "checkin_spec_recovery_mode" not in state:
        state["checkin_spec_recovery_mode"] = False
    if not isinstance(state.get("checkin_spec"), list):
        state["checkin_spec"] = []
    if not isinstance(state.get("today_task_progress"), dict):
        state["today_task_progress"] = {}


def _initialized_for_daily(state: Dict) -> bool:
    scores = state.get("initial_scores")
    return isinstance(scores, dict) and {"L", "R", "W", "S"}.issubset(scores.keys())


def _local_today_yesterday() -> Tuple[date, date, str]:
    today = datetime.now().astimezone().date()
    yesterday = today - timedelta(days=1)
    return today, yesterday, yesterday.strftime("%Y-%m-%d")


def _yesterday_in_plan_window(state: Dict, yesterday: date) -> bool:
    sd = state.get("start_date")
    if not sd:
        return False
    try:
        s0 = datetime.strptime(str(sd)[:10], "%Y-%m-%d").date()
    except ValueError:
        return False
    return yesterday >= s0


def _had_checkin_on_date(state: Dict, day: date) -> bool:
    ds = day.strftime("%Y-%m-%d")
    hist = state.get("checkin_history")
    if isinstance(hist, list):
        for row in hist:
            if isinstance(row, dict) and str(row.get("date", ""))[:10] == ds:
                return True
    lcd = str(state.get("last_checkin_date") or "")[:10]
    return len(lcd) == 10 and lcd == ds


def _missed_checkin_month_count(state: Dict, ystr: str) -> int:
    prefix = ystr[:7]
    dates = state.get("missed_checkin_record_dates")
    if not isinstance(dates, list):
        return 1
    return sum(1 for d in dates if str(d)[:7] == prefix) + 1


def format_missed_checkin_notice(yesterday: date, month_count: int, fail_streak: int) -> str:
    zh = f"{yesterday.month}月{yesterday.day}日"
    return (
        "📋 昨日未打卡记录\n"
        f"昨天（{zh}）未检测到打卡记录\n"
        f"已记录为漏打卡（本月第{month_count}次）\n"
        f"连续未完成：{fail_streak}天\n"
        "\n"
        "没关系，今天重新开始 💪\n"
        "今日任务即将推送..."
    )


def run_daily_missed_checkin() -> int:
    """由 send-daily-modes.sh 在 08:30 推送前调用：若计划已开始且昨天无打卡，则累计并提醒。"""
    with state_lock():
        state = load_state()
        ensure_defaults(state)
        if not _initialized_for_daily(state):
            log("INFO: daily-missed-checkin skip (not initialized)")
            return 0

        _, yesterday, ystr = _local_today_yesterday()
        processed = str(state.get("last_missed_checkin_processed_for") or "")
        if processed == ystr:
            log(f"INFO: daily-missed-checkin skip (already processed {ystr})")
            return 0

        if not _yesterday_in_plan_window(state, yesterday):
            log("INFO: daily-missed-checkin skip (yesterday before start_date or no start_date)")
            return 0

        if _had_checkin_on_date(state, yesterday):
            log(f"INFO: daily-missed-checkin skip (checkin present on {ystr})")
            return 0

        month_count = _missed_checkin_month_count(state, ystr)
        missed_days = int(state.get("missed_days", 0) or 0) + 1
        fail_streak = int(state.get("fail_streak", 0) or 0) + 1
        recovery_mode = fail_streak >= 2

        dates = state.get("missed_checkin_record_dates")
        if not isinstance(dates, list):
            dates = []
        else:
            dates = list(dates)
        dates.append(ystr)

        state["missed_days"] = missed_days
        state["fail_streak"] = fail_streak
        state["recovery_mode"] = recovery_mode
        state["missed_checkin_record_dates"] = dates
        state["last_missed_checkin_processed_for"] = ystr
        if recovery_mode:
            state["state"] = "🔴 差"

        write_state(state)
        notice = format_missed_checkin_notice(yesterday, month_count, fail_streak)

    log(
        f"INFO: daily-missed-checkin recorded ystr={ystr} missed_days={missed_days} "
        f"fail_streak={fail_streak} recovery_mode={recovery_mode}"
    )
    try:
        send_message(notice)
    except Exception as exc:
        log(f"ERROR: daily-missed-checkin send failed: {exc}")
        return 1
    return 0


def parse_progress(raw: str) -> Tuple[int, int, int]:
    """听力进度：Cam{book} Test{test} Section{1..4}。"""
    match = PROGRESS_RE.match(raw.strip())
    if not match:
        return parse_progress(DEFAULT_PROGRESS)
    book = int(match.group(1))
    test = int(match.group(2))
    section = int(match.group(3))
    if book < 10 or book > MAX_BOOK or test < 1 or test > MAX_TEST_PER_BOOK or section < 1 or section > MAX_SECTION:
        return parse_progress(DEFAULT_PROGRESS)
    return book, test, section


def format_progress(progress: Tuple[int, int, int]) -> str:
    book, test, section = progress
    return f"Cam{book} Test{test} Section{section}"


def advance_progress(current: str) -> str:
    """听力推进：每 Test 4 个 Section。"""
    book, test, section = parse_progress(current)
    if section < MAX_SECTION:
        return format_progress((book, test, section + 1))
    if test < MAX_TEST_PER_BOOK:
        return format_progress((book, test + 1, 1))
    if book < MAX_BOOK:
        return format_progress((book + 1, 1, 1))
    return format_progress((MAX_BOOK, MAX_TEST_PER_BOOK, MAX_SECTION))


def advance_listening_test(current: str) -> str:
    """听力跳一整套 Test：模式 1/2 当日完成 4 个 Section 后到下个 Test 的 Section 1。"""
    book, test, _section = parse_progress(current)
    if test < MAX_TEST_PER_BOOK:
        return format_progress((book, test + 1, 1))
    if book < MAX_BOOK:
        return format_progress((book + 1, 1, 1))
    return format_progress((MAX_BOOK, MAX_TEST_PER_BOOK, MAX_SECTION))


def parse_reading_progress(raw: str) -> Tuple[int, int, int]:
    """阅读进度：Cam{book} Test{test} Passage{1..3}。"""
    s = (raw or "").strip()
    match = READING_PROGRESS_RE.match(s)
    if not match:
        migrated = _migrate_reading_progress(s)
        match = READING_PROGRESS_RE.match(migrated)
        if not match:
            return 10, 1, 1
    book = int(match.group(1))
    test = int(match.group(2))
    passage = int(match.group(3))
    if book < 10 or book > MAX_BOOK or test < 1 or test > MAX_TEST_PER_BOOK or passage < 1 or passage > MAX_PASSAGE:
        return parse_reading_progress(DEFAULT_READING_PROGRESS)
    return book, test, passage


def format_reading_progress(progress: Tuple[int, int, int]) -> str:
    book, test, passage = progress
    return f"Cam{book} Test{test} Passage{passage}"


def advance_reading_progress(current: str) -> str:
    """阅读推进：每 Test 3 个 Passage。"""
    book, test, passage = parse_reading_progress(current)
    if passage < MAX_PASSAGE:
        return format_reading_progress((book, test, passage + 1))
    if test < MAX_TEST_PER_BOOK:
        return format_reading_progress((book, test + 1, 1))
    if book < MAX_BOOK:
        return format_reading_progress((book + 1, 1, 1))
    return format_reading_progress((MAX_BOOK, MAX_TEST_PER_BOOK, MAX_PASSAGE))


def advance_reading_test(current: str) -> str:
    """阅读跳一整套 Test：模式 1/2 当日完成 3 个 Passage 后到下个 Test 的 Passage 1。"""
    book, test, _passage = parse_reading_progress(current)
    if test < MAX_TEST_PER_BOOK:
        return format_reading_progress((book, test + 1, 1))
    if book < MAX_BOOK:
        return format_reading_progress((book + 1, 1, 1))
    return format_reading_progress((MAX_BOOK, MAX_TEST_PER_BOOK, MAX_PASSAGE))


def _progress_updates_after_checkin(state: Dict, today_str: str) -> Dict[str, Any]:
    return progress_updates_after_checkin(
        state,
        today_str,
        default_listen=DEFAULT_PROGRESS,
        default_read=DEFAULT_READING_PROGRESS,
        advance_listening_test=advance_listening_test,
        advance_progress=advance_progress,
        advance_reading_test=advance_reading_test,
        advance_reading_progress=advance_reading_progress,
    )


def build_placement_plan(scores: Dict[str, float], target_score: float, study_days: int) -> Dict:
    avg = round((scores["L"] + scores["R"] + scores["W"] + scores["S"]) / 4, 2)
    weakest_skill = min(scores, key=scores.get)
    gap = max(0.0, round(target_score - avg, 2))

    if avg < 5.5:
        level_factor = 0.8
        xp_threshold = 60
    elif avg < 6.5:
        level_factor = 1.0
        xp_threshold = 80
    else:
        level_factor = 1.2
        xp_threshold = 100

    base_daily_hours = 1.5 + gap * 1.8
    daily_hours = round(min(6.5, max(1.5, base_daily_hours)), 1)

    skill_weights = {"L": 1.0, "R": 1.0, "W": 1.0, "S": 1.0}
    skill_weights[weakest_skill] = 1.5
    total_weight = sum(skill_weights.values())
    weekly_hours = {
        k: round(daily_hours * 7 * (v / total_weight), 1) for k, v in skill_weights.items()
    }

    # 粗略倒推Cambridge完成量：差距每1.0分约需4册密集训练
    books_needed = max(1.0, round(gap * 4, 1))
    books_per_day = round(books_needed / max(1, study_days), 3)
    books_per_week = round(books_per_day * 7, 2)

    weekly_targets = {
        "L": f"听力真题 {max(2, int(round(weekly_hours['L'] / 1.5)))} 套/周",
        "R": f"阅读真题 {max(2, int(round(weekly_hours['R'] / 1.5)))} 套/周",
        "W": f"写作Task2 {max(3, int(round(weekly_hours['W'] / 1.2)))} 篇/周",
        "S": f"口语录音练习 {max(4, int(round(weekly_hours['S'] / 0.8)))} 次/周",
    }

    return {
        "avg": avg,
        "weakest_skill": weakest_skill,
        "level_factor": level_factor,
        "xp_threshold": xp_threshold,
        "daily_hours": daily_hours,
        "books_needed": books_needed,
        "books_per_day": books_per_day,
        "books_per_week": books_per_week,
        "weekly_hours": weekly_hours,
        "weekly_targets": weekly_targets,
    }


def extract_writing_body(content: str) -> str:
    cleaned = re.sub(r"^\s*#写作提交\b", "", content, count=1, flags=re.IGNORECASE).strip()
    return cleaned


def extract_manual_review_body(content: str) -> str:
    cleaned = re.sub(r"^\s*#批改结果\b", "", content, count=1, flags=re.IGNORECASE).strip()
    return cleaned


def build_writing_prompt(essay: str) -> str:
    return (
        "你是IELTS写作考官。请只输出JSON，不要任何额外文本。\n"
        '格式: {"scores":{"TR":x,"CC":x,"LR":x,"GRA":x},"improvements":["建议1","建议2","建议3"]}\n'
        "要求: 四项分数0-9, improvements必须恰好3条且具体可执行。\n"
        "作文:\n"
        f"{essay}"
    )


def extract_json_text(raw_text: str) -> str:
    text = (raw_text or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```$", "", text)
        text = text.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        return text[start : end + 1]
    return text


def normalize_review_json(data: Dict) -> Dict:
    scores = data.get("scores", {})
    improvements = data.get("improvements", [])
    normalized_scores = {
        "TR": float(scores.get("TR", 0)),
        "CC": float(scores.get("CC", 0)),
        "LR": float(scores.get("LR", 0)),
        "GRA": float(scores.get("GRA", 0)),
    }
    normalized_improvements = [str(x).strip() for x in improvements if str(x).strip()][:3]
    while len(normalized_improvements) < 3:
        normalized_improvements.append("请补充一条更具体的改进动作（如句式替换、连接词优化、例证展开）。")
    return {"scores": normalized_scores, "improvements": normalized_improvements}


def format_writing_feedback(review: Dict) -> str:
    scores = review["scores"]
    improvements = review["improvements"]
    tr, cc, lr, gra = scores["TR"], scores["CC"], scores["LR"], scores["GRA"]
    return (
        "📝 写作批改结果\n"
        f"TR:  {make_bar(tr, 9.0)}  {tr}\n"
        f"CC:  {make_bar(cc, 9.0)}  {cc}\n"
        f"LR:  {make_bar(lr, 9.0)}  {lr}\n"
        f"GRA: {make_bar(gra, 9.0)}  {gra}\n"
        "────────────────\n"
        "改进建议：\n"
        f"1) {improvements[0]}\n"
        f"2) {improvements[1]}\n"
        f"3) {improvements[2]}"
    )


def parse_manual_review(content: str) -> Optional[Dict]:
    text = extract_manual_review_body(content)
    if not text:
        return None

    score_matches = dict(
        (k.upper(), v)
        for k, v in re.findall(
            r"^\s*(TR|CC|LR|GRA)\s*[:：]\s*(?:[█░]+\s+)?([0-9](?:\.\d+)?)\s*$",
            text,
            flags=re.IGNORECASE | re.MULTILINE,
        )
    )
    if set(score_matches.keys()) != {"TR", "CC", "LR", "GRA"}:
        return None

    suggestions: List[str] = []
    for i in range(1, 4):
        match = re.search(rf"^\s*建议{i}\s*[:：]\s*(.+)\s*$", text, flags=re.MULTILINE)
        if not match:
            return None
        suggestion = match.group(1).strip()
        if not suggestion:
            return None
        suggestions.append(suggestion)

    try:
        scores = {k: float(v) for k, v in score_matches.items()}
    except ValueError:
        return None

    if any(score < 0 or score > 9 for score in scores.values()):
        return None

    return {"scores": scores, "improvements": suggestions}


def get_latest_match(messages: List[Dict], matcher) -> Optional[Dict]:
    for message in messages:
        if message.get("msg_type") != "text":
            continue
        content = (message.get("content") or "").strip()
        if matcher(content):
            return message
    return None


def pick_route(messages: List[Dict], state: Dict) -> Optional[Tuple[str, Dict]]:
    last_auto_writing_id = state.get("last_auto_writing_message_id") or state.get("last_writing_message_id")
    last_manual_writing_id = state.get("last_manual_writing_message_id") or state.get("last_writing_message_id")

    latest_writing = get_latest_match(messages, lambda c: bool(WRITING_RE.match(c)))
    if latest_writing and last_auto_writing_id != latest_writing.get("message_id"):
        return "writing", latest_writing

    latest_init_reset = get_latest_match(messages, lambda c: bool(INIT_RESET_RE.match(c)))
    if latest_init_reset and state.get("last_init_reset_message_id") != latest_init_reset.get("message_id"):
        return "init_reset", latest_init_reset

    latest_init_scores = get_latest_match(messages, lambda c: bool(INIT_SCORES_RE.match(c)))
    if latest_init_scores and state.get("last_init_scores_message_id") != latest_init_scores.get("message_id"):
        return "init_scores", latest_init_scores

    latest_manual_review = get_latest_match(messages, lambda c: bool(MANUAL_REVIEW_RE.match(c)))
    if latest_manual_review and last_manual_writing_id != latest_manual_review.get("message_id"):
        return "manual_writing", latest_manual_review

    latest_checkin = get_latest_match(messages, lambda c: classify_checkin_message(c) != "none")
    if latest_checkin and state.get("last_checkin_message_id") != latest_checkin.get("message_id"):
        return "checkin", latest_checkin

    latest_mode = get_latest_match(messages, lambda c: bool(MODE_RE.match(c)))
    if latest_mode and state.get("last_mode_message_id") != latest_mode.get("message_id"):
        return "mode", latest_mode

    return None


def handle_init_reset(state: Dict, message: Dict) -> Tuple[Dict, str]:
    updates = {
        "last_init_reset_message_id": message.get("message_id", ""),
        "last_init_scores_message_id": "",
        "initial_scores": {},
        "weakest_skill": "",
        "target_score": "",
        "study_days": "",
        "start_date": "",
        "initial_level_factor": "",
        "xp_daily_threshold": "",
        "study_plan_sent": False,
        "last_checkin_motivation_image": "",
        "mock_test_sent": False,
        "checkin_spec_date": "",
        "checkin_spec_mode": "",
        "checkin_spec_recovery_mode": False,
        "checkin_spec": [],
    }
    return updates, f"已重置初始化信息。\n\n{INIT_PROMPT}"


def handle_init_scores(state: Dict, message: Dict) -> Tuple[Dict, Union[str, List[str]]]:
    content = (message.get("content") or "").strip()
    match = INIT_SCORES_RE.match(content)
    if not match:
        return {"last_init_scores_message_id": message.get("message_id", "")}, INIT_PROMPT

    l_score = float(match.group(1))
    r_score = float(match.group(2))
    w_score = float(match.group(3))
    s_score = float(match.group(4))
    target_score = float(match.group(5))
    study_days = int(match.group(6))

    if not (1 <= study_days <= 365):
        return {"last_init_scores_message_id": message.get("message_id", "")}, "天数请填写 1-365 之间的整数。"

    scores = {"L": l_score, "R": r_score, "W": w_score, "S": s_score}
    if any(v < 0 or v > 9 for v in scores.values()) or target_score < 0 or target_score > 9:
        return {"last_init_scores_message_id": message.get("message_id", "")}, "分数请填写 0-9 之间（可用 .0/.5）。"

    plan = build_placement_plan(scores, target_score, study_days)
    start_date_str = datetime.now().strftime("%Y-%m-%d")
    listen_p = str(state.get("listening_progress") or DEFAULT_PROGRESS)
    msg1 = format_score_analysis_message(plan, scores, target_score, study_days, start_date_str)
    roadmap, roadmap_src = generate_study_roadmap_text(
        plan, scores, target_score, study_days, start_date_str, listen_p
    )
    log(f"INFO: init study roadmap source={roadmap_src} message_id={message.get('message_id', '')}")
    updates = {
        "last_init_scores_message_id": message.get("message_id", ""),
        "initial_scores": scores,
        "weakest_skill": plan["weakest_skill"],
        "target_score": target_score,
        "study_days": study_days,
        "start_date": start_date_str,
        "initial_level_factor": plan["level_factor"],
        "xp_daily_threshold": plan["xp_threshold"],
        "study_plan_sent": True,
    }
    return updates, [msg1, roadmap]


def handle_writing_submission(state: Dict, message: Dict) -> Tuple[Dict, str]:
    content = (message.get("content") or "").strip()
    body = extract_writing_body(content)
    provider_used = "none"
    fallback_triggered = False
    latency_ms = 0
    normalized: Optional[Dict] = None
    reply = ""
    if not body:
        reply = "📝 已收到写作提交请求，但正文为空。请使用“#写作提交 + 正文内容”重新发送。"
    else:
        raw = ""
        try:
            result = call_ai_with_fallback(build_writing_prompt(body))
            provider_used = result.get("provider_used", "none")
            fallback_triggered = bool(result.get("fallback_triggered", False))
            latency_ms = int(result.get("latency_ms", 0))
            if not result.get("ok", False):
                log(
                    "ERROR: writing route ai failed "
                    f"message_id={message.get('message_id','')} "
                    f"provider_used={provider_used} "
                    f"fallback_triggered={fallback_triggered} "
                    f"latency_ms={latency_ms} "
                    f"err={result.get('error','unknown')}"
                )
                reply = WRITING_UNAVAILABLE_MESSAGE
                updates = {
                    "last_writing_message_id": message.get("message_id", ""),
                    "last_auto_writing_message_id": message.get("message_id", ""),
                    "last_writing_date": datetime.now().strftime("%Y-%m-%d"),
                }
                return updates, reply
            raw = result.get("text", "")
            json_text = extract_json_text(raw)
            parsed = json.loads(json_text)
            normalized = normalize_review_json(parsed)
            reply = format_writing_feedback(normalized)
        except json.JSONDecodeError:
            log(
                "WARN: writing route JSON parse failed "
                f"message_id={message.get('message_id','')} "
                f"provider_used={provider_used} "
                f"fallback_triggered={fallback_triggered} "
                f"latency_ms={latency_ms} raw={raw}"
            )
            reply = (
                "📝 写作AI批改结果（原始输出）\n"
                "模型返回未严格遵循 JSON，以下为原始文本：\n"
                f"{raw}"
            )
        except Exception as exc:
            log(
                "ERROR: writing route unexpected error "
                f"message_id={message.get('message_id','')} "
                f"provider_used={provider_used} "
                f"fallback_triggered={fallback_triggered} "
                f"latency_ms={latency_ms} err={exc}"
            )
            reply = WRITING_UNAVAILABLE_MESSAGE
    log(
        "INFO: writing route result "
        f"message_id={message.get('message_id','')} "
        f"provider_used={provider_used} "
        f"fallback_triggered={fallback_triggered} "
        f"latency_ms={latency_ms}"
    )
    scores_snap = dict(normalized["scores"]) if normalized and normalized.get("scores") else dict(state.get("last_writing_scores") or {})
    updates: Dict[str, Any] = {
        "last_writing_message_id": message.get("message_id", ""),
        "last_auto_writing_message_id": message.get("message_id", ""),
        "last_writing_date": datetime.now().strftime("%Y-%m-%d"),
        "last_writing_scores": scores_snap,
    }
    if scores_snap:
        updates["weekly_writing_avg"] = {k: round(float(v), 1) for k, v in scores_snap.items()}
    return updates, reply


def handle_manual_writing_result(state: Dict, message: Dict) -> Tuple[Dict, str]:
    content = (message.get("content") or "").strip()
    parsed = parse_manual_review(content)
    if not parsed:
        return {
            "last_writing_message_id": message.get("message_id", ""),
            "last_manual_writing_message_id": message.get("message_id", ""),
        }, MANUAL_FORMAT_HINT

    updates = {
        "last_writing_message_id": message.get("message_id", ""),
        "last_manual_writing_message_id": message.get("message_id", ""),
        "last_writing_date": datetime.now().strftime("%Y-%m-%d"),
        "last_writing_scores": parsed["scores"],
        "weekly_writing_avg": {k: round(float(v), 1) for k, v in parsed["scores"].items()},
    }
    return updates, format_writing_feedback(parsed)


def handle_checkin(state: Dict, message: Dict) -> Tuple[Dict, str]:
    content = (message.get("content") or "").strip()
    kind = classify_checkin_message(content)
    message_id = message.get("message_id", "")
    today_str = datetime.now().strftime("%Y-%m-%d")

    if kind == "daily_attempt":
        spec, _used_yesterday, prefix = resolve_active_spec(state)
        if not spec:
            return {}, NO_SPEC_PROMPT
        parsed, ignored, missing = parse_daily_checkin(content, spec["items"])
        if missing:
            body = prefix + f"还缺：{'、'.join(missing)}\n\n" + format_checkin_spec_message(spec)
            return {"last_checkin_message_id": message_id}, body
        if not parsed:
            body = prefix + format_checkin_spec_message(spec) + "\n\n（请按上方模板填写各项）"
            return {"last_checkin_message_id": message_id}, body
        completion_rate = composite_completion_rate_daily(parsed, spec["items"])
        evaluated = evaluate_state(
            completion_rate, int(state.get("fail_streak", 0) or 0), int(state.get("streak", 0) or 0)
        )
        updates = apply_daily_checkin_to_state(
            state, parsed, spec["items"], spec, evaluated, completion_rate, today_str, message_id
        )
        updates.update(_progress_updates_after_checkin({**state, **updates}, today_str))
        reply = format_daily_checkin_reply(
            parsed,
            spec["items"],
            spec,
            state,
            evaluated,
            completion_rate,
            prefix=prefix,
            ignored_labels=ignored or None,
        )
        return updates, reply

    if kind == "structured":
        parsed = parse_structured_checkin(content)
        assert parsed is not None
        completion_rate = composite_completion_rate(parsed)
        evaluated = evaluate_state(completion_rate, int(state.get("fail_streak", 0) or 0), int(state.get("streak", 0) or 0))
        updates = apply_checkin_to_state(state, parsed, evaluated, completion_rate, today_str, message_id)
        updates.update(_progress_updates_after_checkin({**state, **updates}, today_str))
        reply = format_structured_checkin_reply(parsed, state, evaluated, completion_rate)
        return updates, reply

    if kind == "legacy":
        return {"last_checkin_message_id": message_id}, NO_SPEC_PROMPT

    if kind == "hint":
        spec, _, _prefix = resolve_active_spec(state)
        if not spec:
            return {"last_checkin_message_id": message_id}, NO_SPEC_PROMPT
        body = format_checkin_spec_message(spec) + "\n\n（请按模板补全各行，首行 #今日打卡）"
        return {"last_checkin_message_id": message_id}, body

    return {}, ""


def handle_mode_selection(state: Dict, message: Dict) -> Tuple[Dict, Union[str, List[str]]]:
    mode = MODE_RE.match((message.get("content") or "").strip()).group(1)
    listening_progress = state["listening_progress"]
    reading_progress = state["reading_progress"]
    recovery_mode = bool(state.get("recovery_mode", False))
    today_str = datetime.now().strftime("%Y-%m-%d")
    mode_changed = (
        str(state.get("last_mode_date") or "") == today_str
        and str(state.get("last_mode_selected") or "") != ""
        and str(state.get("last_mode_selected") or "") != mode
    )

    plan = build_mode_tasks(mode, listening_progress, reading_progress, recovery_mode, state)
    spec_payload = build_checkin_spec_payload(
        mode, listening_progress, reading_progress, recovery_mode, state, spec_date=today_str
    )
    updates = {
        "last_mode_message_id": message.get("message_id", ""),
        "last_mode_selected": mode,
        "last_mode_date": today_str,
    }
    updates.update(checkin_spec_state_updates(spec_payload))
    updates["today_task_progress"] = build_today_task_progress(
        listening_progress,
        reading_progress,
        plan,
        today_str,
        state.get("today_task_progress"),
    )
    if recovery_mode:
        updates["recovery_mode"] = False
    spec_msg = format_checkin_spec_message(spec_payload, mode_change_notice=mode_changed)
    return updates, [plan["message"], spec_msg]


def main() -> int:
    log(f"start chat_id={CHAT_ID} lark_cli={LARK_CLI}")
    if not Path(LARK_CLI).exists():
        log(f"ERROR: lark-cli not found at {LARK_CLI}")
        return 127

    try:
        payload = run_lark_list()
    except Exception as exc:
        log(f"ERROR: fetch messages failed: {exc}")
        return 1

    messages = payload.get("data", {}).get("messages", [])
    if not messages:
        log("NOOP: no messages")
        return 0

    with state_lock():
        state = load_state()
        ensure_defaults(state)
        route = pick_route(messages, state)
    if not route:
        log("NOOP: no routable new message")
        return 0

    route_type, message = route
    message_id = message.get("message_id", "")

    if route_type == "writing":
        updates, reply = handle_writing_submission(state, message)
    elif route_type == "init_reset":
        updates, reply = handle_init_reset(state, message)
    elif route_type == "init_scores":
        updates, reply = handle_init_scores(state, message)
    elif route_type == "manual_writing":
        updates, reply = handle_manual_writing_result(state, message)
    elif route_type == "checkin":
        updates, reply = handle_checkin(state, message)
    else:
        updates, reply = handle_mode_selection(state, message)

    if isinstance(reply, list):
        reply_parts = [p for p in reply if isinstance(p, str) and p.strip()]
    else:
        reply_parts = [reply] if isinstance(reply, str) and reply.strip() else []

    if not reply_parts:
        log(f"NOOP: route={route_type} message_id={message_id} produced empty reply")
        return 0

    try:
        for i, chunk in enumerate(reply_parts):
            send_message(chunk)
            if i < len(reply_parts) - 1:
                time.sleep(0.45)
    except Exception as exc:
        log(f"ERROR: route={route_type} send failed message_id={message_id} err={exc}")
        return 1

    with state_lock():
        latest_state = load_state()
        ensure_defaults(latest_state)
        # Final idempotency guard before commit
        if route_type == "writing":
            last_auto_writing_id = latest_state.get("last_auto_writing_message_id") or latest_state.get("last_writing_message_id")
            if last_auto_writing_id == message_id:
                log(f"NOOP: route=writing message_id={message_id} already committed")
                return 0
        if route_type == "manual_writing":
            last_manual_writing_id = latest_state.get("last_manual_writing_message_id") or latest_state.get("last_writing_message_id")
            if last_manual_writing_id == message_id:
                log(f"NOOP: route=manual_writing message_id={message_id} already committed")
                return 0
        if route_type == "init_reset" and latest_state.get("last_init_reset_message_id") == message_id:
            log(f"NOOP: route=init_reset message_id={message_id} already committed")
            return 0
        if route_type == "init_scores" and latest_state.get("last_init_scores_message_id") == message_id:
            log(f"NOOP: route=init_scores message_id={message_id} already committed")
            return 0
        if route_type == "checkin" and latest_state.get("last_checkin_message_id") == message_id:
            log(f"NOOP: route=checkin message_id={message_id} already committed")
            return 0
        if route_type == "mode" and latest_state.get("last_mode_message_id") == message_id:
            log(f"NOOP: route=mode message_id={message_id} already committed")
            return 0
        latest_state.update(updates)
        write_state(latest_state)
    log(f"OK: route={route_type} message_id={message_id}")

    checkin_kind = classify_checkin_message((message.get("content") or "").strip())
    if route_type == "checkin" and checkin_kind in ("structured", "daily_attempt"):
        merged = load_state()
        ensure_defaults(merged)
        sent = try_send_checkin_motivation(merged, CHAT_ID, LARK_CLI, log)
        if sent:
            with state_lock():
                st = load_state()
                ensure_defaults(st)
                if st.get("last_checkin_message_id") != message_id:
                    log("motivation state write skip: last_checkin_message_id drifted")
                else:
                    st["last_checkin_motivation_image"] = sent
                    write_state(st)
    return 0


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "daily-missed-checkin":
        sys.exit(run_daily_missed_checkin())
    sys.exit(main())
