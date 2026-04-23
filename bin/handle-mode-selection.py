#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = Path(os.environ.get("LOG_FILE", str(LOG_DIR / "mode-selection.log")))
STATE_FILE = Path(os.environ.get("STATE_FILE", str(ROOT / "user_state.json")))

CHAT_ID = os.environ.get("FEISHU_IELTS_CHAT_ID", "oc_99000aba52da6814c200481c4dedf1ea")

os.environ["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:{os.environ.get('PATH', '')}"
default_home = str(ROOT.parent.parent)
os.environ["HOME"] = os.environ.get("HOME", default_home)
os.environ["USER"] = os.environ.get("USER", Path(os.environ["HOME"]).name)
os.environ["LOGNAME"] = os.environ.get("LOGNAME", os.environ["USER"])

LARK_CLI = os.environ.get("LARK_CLI") or shutil.which("lark-cli") or "/opt/homebrew/bin/lark-cli"
MODE_RE = re.compile(r"^\s*([123])\s*$")
PROGRESS_RE = re.compile(r"^Cam(\d+)\s+Test(\d+)\s+Section(\d+)$")
DEFAULT_PROGRESS = "Cam10 Test1 Section1"
MAX_BOOK = 18
MAX_TEST_PER_BOOK = 4
MAX_SECTION = 4
TASK2_PLACEHOLDER = "Task 2 占位题目：Some people think that online learning will replace traditional classroom learning. Discuss both views and give your own opinion."


def log(message: str) -> None:
    line = f"[{datetime.now().astimezone().strftime('%Y-%m-%d %H:%M:%S %z')}] {message}"
    with LOG_FILE.open("a", encoding="utf-8") as f:
        f.write(line + "\n")
    print(line)


def run_lark_list() -> dict:
    cmd = [
        LARK_CLI,
        "im",
        "+chat-messages-list",
        "--as",
        "user",
        "--chat-id",
        CHAT_ID,
        "--page-size",
        "20",
        "--format",
        "json",
    ]
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"lark-cli exit={result.returncode} stderr={result.stderr.strip()} stdout={result.stdout.strip()}"
        )
    return json.loads(result.stdout)


def send_task_message(text: str) -> None:
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


def load_state() -> Dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def atomic_write_state(state: Dict) -> None:
    tmp_file = STATE_FILE.with_suffix(".json.tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp_file.replace(STATE_FILE)


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


def build_tasks(mode: str, listening_progress: str, reading_progress: str, recovery_mode: bool) -> Dict:
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
        body = (
            f"- 阅读：{reading_progress}\n"
            "- 词汇：30个"
        )
        return {"message": f"{header}\n{body}", "advance_listening": False, "advance_reading": True}

    # Recovery Mode：明日任务减半（一次性生效）
    header = f"已收到模式选择：{mode}\n⚠️ Recovery Mode 生效（明日任务减半）\n今日任务如下："
    if mode == "1":
        body = (
            f"- 阅读：{reading_progress}\n"
            "- 词汇：15个"
        )
        return {"message": f"{header}\n{body}", "advance_listening": False, "advance_reading": True}
    if mode == "2":
        body = (
            f"- 阅读：{reading_progress}\n"
            "- 词汇：15个"
        )
        return {"message": f"{header}\n{body}", "advance_listening": False, "advance_reading": True}
    body = "- 词汇：15个"
    return {"message": f"{header}\n{body}", "advance_listening": False, "advance_reading": False}


def get_latest_mode_message(messages: list) -> Optional[Tuple[str, str]]:
    for message in messages:
        if message.get("msg_type") != "text":
            continue
        content = (message.get("content") or "").strip()
        match = MODE_RE.match(content)
        if match:
            return message.get("message_id", ""), match.group(1)
    return None


def ensure_defaults(state: Dict) -> None:
    if not state.get("listening_progress"):
        state["listening_progress"] = DEFAULT_PROGRESS
    if not state.get("reading_progress"):
        state["reading_progress"] = DEFAULT_PROGRESS


def main() -> int:
    log(f"start chat_id={CHAT_ID} lark_cli={LARK_CLI}")
    if not Path(LARK_CLI).exists():
        log(f"ERROR: lark-cli not found at {LARK_CLI}")
        return 127

    state = load_state()
    ensure_defaults(state)

    try:
        payload = run_lark_list()
    except Exception as exc:
        log(f"ERROR: fetch messages failed: {exc}")
        return 1

    messages = payload.get("data", {}).get("messages", [])
    if not messages:
        log("NOOP: no messages")
        return 0

    latest = get_latest_mode_message(messages)
    if not latest:
        log("NOOP: no mode selection found in latest messages")
        return 0

    message_id, mode = latest
    if not message_id:
        log("NOOP: matched mode message but message_id empty")
        return 0

    if state.get("last_mode_message_id") == message_id:
        log(f"NOOP: already processed mode message_id={message_id}")
        return 0

    listening_progress = state["listening_progress"]
    reading_progress = state["reading_progress"]
    recovery_mode = bool(state.get("recovery_mode", False))
    task_plan = build_tasks(mode, listening_progress, reading_progress, recovery_mode)
    task_message = task_plan["message"]

    try:
        send_task_message(task_message)
    except Exception as exc:
        log(f"ERROR: send task message failed: {exc}")
        return 1

    if task_plan["advance_listening"]:
        state["listening_progress"] = advance_progress(listening_progress)
    if task_plan["advance_reading"]:
        state["reading_progress"] = advance_progress(reading_progress)

    state["last_mode_message_id"] = message_id
    state["last_mode_selected"] = mode
    state["last_mode_date"] = datetime.now().strftime("%Y-%m-%d")
    if recovery_mode:
        # 明日减半仅消费一次，任务下发成功后关闭
        state["recovery_mode"] = False

    atomic_write_state(state)
    log(
        "OK: mode processed "
        f"mode={mode} message_id={message_id} "
        f"listening_progress={state['listening_progress']} "
        f"reading_progress={state['reading_progress']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
