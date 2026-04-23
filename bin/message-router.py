#!/usr/bin/env python3
import fcntl
import json
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from ai_caller import call_ai_with_fallback

ROOT = Path(__file__).resolve().parent.parent
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
CHECKIN_RE = re.compile(r"^\s*打卡\s*[:：]?\s*(\d+)\s*/\s*(\d+)\s*$")
MODE_RE = re.compile(r"^\s*([123])\s*$")
PROGRESS_RE = re.compile(r"^Cam(\d+)\s+Test(\d+)\s+Section(\d+)$")

DEFAULT_PROGRESS = "Cam10 Test1 Section1"
MAX_BOOK = 18
MAX_TEST_PER_BOOK = 4
MAX_SECTION = 4
TASK2_PLACEHOLDER = (
    "Task 2 占位题目：Some people think that online learning will replace traditional classroom learning. "
    "Discuss both views and give your own opinion."
)
WRITING_UNAVAILABLE_MESSAGE = (
    "⚠️ AI批改暂时不可用。请将你的作文粘贴到任意AI，附上评分标准：\n"
    "按雅思四维评分：TR(任务回应)/CC(连贯衔接)/LR(词汇丰富度)/GRA(语法准确性)，每项0-9分，输出评分+3条改进建议。\n"
    "完成后请回复 #批改结果 + 评分内容"
)
MANUAL_FORMAT_HINT = (
    "格式不正确，请使用以下模板：\n"
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


def ensure_defaults(state: Dict) -> None:
    if not state.get("listening_progress"):
        state["listening_progress"] = DEFAULT_PROGRESS
    if not state.get("reading_progress"):
        state["reading_progress"] = DEFAULT_PROGRESS
    if "fail_streak" not in state:
        state["fail_streak"] = 0
    if "streak" not in state:
        state["streak"] = 0
    if "recovery_mode" not in state:
        state["recovery_mode"] = False


def parse_progress(raw: str) -> Tuple[int, int, int]:
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
    book, test, section = parse_progress(current)
    if section < MAX_SECTION:
        return format_progress((book, test, section + 1))
    if test < MAX_TEST_PER_BOOK:
        return format_progress((book, test + 1, 1))
    if book < MAX_BOOK:
        return format_progress((book + 1, 1, 1))
    return format_progress((MAX_BOOK, MAX_TEST_PER_BOOK, MAX_SECTION))


def format_percent(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)}%"
    return f"{value:.2f}%"


def evaluate_state(completion_rate: float, current_fail_streak: int, current_streak: int) -> Dict:
    fail_streak = current_fail_streak + 1 if completion_rate < 50 else 0
    streak = current_streak + 1 if completion_rate >= 80 else 0
    if completion_rate < 50 or fail_streak >= 2:
        return {"state": "🔴 差", "fail_streak": fail_streak, "streak": streak, "recovery_mode": fail_streak >= 2}
    if completion_rate >= 80:
        return {"state": "🟢 正常", "fail_streak": fail_streak, "streak": streak, "recovery_mode": False}
    return {"state": "🟡 不稳定", "fail_streak": fail_streak, "streak": streak, "recovery_mode": False}


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


def format_placement_plan(plan: Dict, scores: Dict[str, float], target_score: float, study_days: int) -> str:
    weakest_map = {"L": "听力", "R": "阅读", "W": "写作", "S": "口语"}
    return (
        "🎯 个性化学习计划（Placement Test）\n"
        f"当前分数：L {scores['L']} / R {scores['R']} / W {scores['W']} / S {scores['S']}\n"
        f"当前均分：{plan['avg']}，薄弱项：{weakest_map[plan['weakest_skill']]}（1.5x时长）\n"
        f"目标分数：{target_score}，备考天数：{study_days} 天\n\n"
        f"每日建议学习时长：{plan['daily_hours']} 小时\n"
        f"Cambridge 倒推进度：约 {plan['books_needed']} 册，总计约 {plan['books_per_day']} 册/天（{plan['books_per_week']} 册/周）\n\n"
        "每周专项目标：\n"
        f"- 听力：{plan['weekly_targets']['L']}（约 {plan['weekly_hours']['L']} 小时）\n"
        f"- 阅读：{plan['weekly_targets']['R']}（约 {plan['weekly_hours']['R']} 小时）\n"
        f"- 写作：{plan['weekly_targets']['W']}（约 {plan['weekly_hours']['W']} 小时）\n"
        f"- 口语：{plan['weekly_targets']['S']}（约 {plan['weekly_hours']['S']} 小时）\n\n"
        f"初始强度系数 InitialLevelFactor：{plan['level_factor']}\n"
        f"每日XP门槛：{plan['xp_threshold']}"
    )


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
    return (
        "#批改结果\n"
        f"TR: {scores['TR']}\n"
        f"CC: {scores['CC']}\n"
        f"LR: {scores['LR']}\n"
        f"GRA: {scores['GRA']}\n"
        f"建议1: {improvements[0]}\n"
        f"建议2: {improvements[1]}\n"
        f"建议3: {improvements[2]}"
    )


def parse_manual_review(content: str) -> Optional[Dict]:
    text = extract_manual_review_body(content)
    if not text:
        return None

    score_matches = dict(
        (k.upper(), v)
        for k, v in re.findall(r"^\s*(TR|CC|LR|GRA)\s*[:：]\s*([0-9](?:\.\d+)?)\s*$", text, flags=re.IGNORECASE | re.MULTILINE)
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


def build_mode_tasks(mode: str, listening_progress: str, reading_progress: str, recovery_mode: bool) -> Dict:
    if not recovery_mode:
        header = f"已收到模式选择：{mode}\n今日任务如下："
        if mode == "1":
            body = (
                f"- 听力：{listening_progress}\n"
                f"- 阅读：{reading_progress}\n"
                f"- 写作：{TASK2_PLACEHOLDER}\n"
                "- 词汇：30个"
            )
            return {"message": f"{header}\n{body}", "advance_listening": True, "advance_reading": True}
        if mode == "2":
            body = (
                f"- 听力：{listening_progress}\n"
                f"- 阅读：{reading_progress}\n"
                "- 词汇：30个"
            )
            return {"message": f"{header}\n{body}", "advance_listening": True, "advance_reading": True}
        body = f"- 阅读：{reading_progress}\n- 词汇：30个"
        return {"message": f"{header}\n{body}", "advance_listening": False, "advance_reading": True}

    header = f"已收到模式选择：{mode}\n⚠️ Recovery Mode 生效（明日任务减半）\n今日任务如下："
    if mode in ("1", "2"):
        body = f"- 阅读：{reading_progress}\n- 词汇：15个"
        return {"message": f"{header}\n{body}", "advance_listening": False, "advance_reading": True}
    body = "- 词汇：15个"
    return {"message": f"{header}\n{body}", "advance_listening": False, "advance_reading": False}


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

    latest_checkin = get_latest_match(messages, lambda c: bool(CHECKIN_RE.match(c)))
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
    }
    return updates, f"已重置初始化信息。\n\n{INIT_PROMPT}"


def handle_init_scores(state: Dict, message: Dict) -> Tuple[Dict, str]:
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
    updates = {
        "last_init_scores_message_id": message.get("message_id", ""),
        "initial_scores": scores,
        "weakest_skill": plan["weakest_skill"],
        "target_score": target_score,
        "study_days": study_days,
        "start_date": datetime.now().strftime("%Y-%m-%d"),
        "initial_level_factor": plan["level_factor"],
        "xp_daily_threshold": plan["xp_threshold"],
    }
    return updates, format_placement_plan(plan, scores, target_score, study_days)


def handle_writing_submission(state: Dict, message: Dict) -> Tuple[Dict, str]:
    content = (message.get("content") or "").strip()
    body = extract_writing_body(content)
    provider_used = "none"
    fallback_triggered = False
    latency_ms = 0
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
    updates = {
        "last_writing_message_id": message.get("message_id", ""),
        "last_auto_writing_message_id": message.get("message_id", ""),
        "last_writing_date": datetime.now().strftime("%Y-%m-%d"),
        "last_writing_scores": normalized["scores"] if body and "normalized" in locals() else state.get("last_writing_scores", {}),
    }
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
    }
    return updates, format_writing_feedback(parsed)


def handle_checkin(state: Dict, message: Dict) -> Tuple[Dict, str]:
    content = (message.get("content") or "").strip()
    match = CHECKIN_RE.match(content)
    if not match:
        return {}, ""

    done = int(match.group(1))
    total = int(match.group(2))
    if total <= 0 or done < 0 or done > total:
        return {}, "打卡格式有效但数值不合法，请使用如“打卡 3/4”。"

    completion_rate = round(done * 100 / total, 2)
    evaluated = evaluate_state(completion_rate, int(state.get("fail_streak", 0) or 0), int(state.get("streak", 0) or 0))
    updates = {
        "completion_rate": completion_rate,
        "last_checkin_date": datetime.now().strftime("%Y-%m-%d"),
        "last_checkin_message_id": message.get("message_id", ""),
        "state": evaluated["state"],
        "fail_streak": evaluated["fail_streak"],
        "streak": evaluated["streak"],
        "recovery_mode": evaluated["recovery_mode"],
    }
    reply = (
        f"📊 今日状态：{evaluated['state']}\n"
        f"完成率：{format_percent(completion_rate)}\n"
        f"打卡项数：{done}/{total}\n"
        f"连续完成：{evaluated['streak']}天"
    )
    if evaluated["recovery_mode"]:
        reply += "\n⚠️ Recovery Mode 已触发：明日任务减半。"
    return updates, reply


def handle_mode_selection(state: Dict, message: Dict) -> Tuple[Dict, str]:
    mode = MODE_RE.match((message.get("content") or "").strip()).group(1)
    listening_progress = state["listening_progress"]
    reading_progress = state["reading_progress"]
    recovery_mode = bool(state.get("recovery_mode", False))

    plan = build_mode_tasks(mode, listening_progress, reading_progress, recovery_mode)
    updates = {
        "last_mode_message_id": message.get("message_id", ""),
        "last_mode_selected": mode,
        "last_mode_date": datetime.now().strftime("%Y-%m-%d"),
    }
    if plan["advance_listening"]:
        updates["listening_progress"] = advance_progress(listening_progress)
    if plan["advance_reading"]:
        updates["reading_progress"] = advance_progress(reading_progress)
    if recovery_mode:
        updates["recovery_mode"] = False
    return updates, plan["message"]


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

    if not reply:
        log(f"NOOP: route={route_type} message_id={message_id} produced empty reply")
        return 0

    try:
        send_message(reply)
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
    return 0


if __name__ == "__main__":
    sys.exit(main())
