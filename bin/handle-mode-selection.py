#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = Path(__file__).resolve().parent
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
from checkin_spec import build_checkin_spec_payload, checkin_spec_state_updates, format_checkin_spec_message
from mode_tasks import build_mode_tasks  # noqa: E402
from task_progress import build_today_task_progress  # noqa: E402
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
READING_PROGRESS_RE = re.compile(r"^Cam(\d+)\s+Test(\d+)\s+Passage(\d+)$")
LEGACY_READING_SECTION_RE = re.compile(r"^Cam(\d+)\s+Test(\d+)\s+Section(\d+)$")
DEFAULT_PROGRESS = "Cam10 Test1 Section1"
DEFAULT_READING_PROGRESS = "Cam10 Test1 Passage1"
MAX_BOOK = 18
MAX_TEST_PER_BOOK = 4
MAX_SECTION = 4
MAX_PASSAGE = 3


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


def advance_listening_test(current: str) -> str:
    book, test, _section = parse_progress(current)
    if test < MAX_TEST_PER_BOOK:
        return format_progress((book, test + 1, 1))
    if book < MAX_BOOK:
        return format_progress((book + 1, 1, 1))
    return format_progress((MAX_BOOK, MAX_TEST_PER_BOOK, MAX_SECTION))


def _migrate_reading_progress(value: str) -> str:
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


def parse_reading_progress(raw: str) -> Tuple[int, int, int]:
    s = (raw or "").strip()
    match = READING_PROGRESS_RE.match(s)
    if not match:
        match = READING_PROGRESS_RE.match(_migrate_reading_progress(s))
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
    book, test, passage = parse_reading_progress(current)
    if passage < MAX_PASSAGE:
        return format_reading_progress((book, test, passage + 1))
    if test < MAX_TEST_PER_BOOK:
        return format_reading_progress((book, test + 1, 1))
    if book < MAX_BOOK:
        return format_reading_progress((book + 1, 1, 1))
    return format_reading_progress((MAX_BOOK, MAX_TEST_PER_BOOK, MAX_PASSAGE))


def advance_reading_test(current: str) -> str:
    book, test, _passage = parse_reading_progress(current)
    if test < MAX_TEST_PER_BOOK:
        return format_reading_progress((book, test + 1, 1))
    if book < MAX_BOOK:
        return format_reading_progress((book + 1, 1, 1))
    return format_reading_progress((MAX_BOOK, MAX_TEST_PER_BOOK, MAX_PASSAGE))


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
    rp = state.get("reading_progress")
    if not rp:
        state["reading_progress"] = DEFAULT_READING_PROGRESS
    elif not READING_PROGRESS_RE.match(str(rp).strip()):
        state["reading_progress"] = _migrate_reading_progress(str(rp))


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
    today_str = datetime.now().strftime("%Y-%m-%d")
    mode_changed = (
        str(state.get("last_mode_date") or "") == today_str
        and str(state.get("last_mode_selected") or "") != ""
        and str(state.get("last_mode_selected") or "") != mode
    )
    task_plan = build_mode_tasks(mode, listening_progress, reading_progress, recovery_mode, state)
    spec_payload = build_checkin_spec_payload(
        mode, listening_progress, reading_progress, recovery_mode, state, spec_date=today_str
    )
    task_message = task_plan["message"]
    spec_message = format_checkin_spec_message(spec_payload, mode_change_notice=mode_changed)

    try:
        send_task_message(task_message)
        time.sleep(0.45)
        send_task_message(spec_message)
    except Exception as exc:
        log(f"ERROR: send task message failed: {exc}")
        return 1

    state["today_task_progress"] = build_today_task_progress(
        listening_progress,
        reading_progress,
        task_plan,
        today_str,
        state.get("today_task_progress"),
    )
    state["last_mode_message_id"] = message_id
    state["last_mode_selected"] = mode
    state["last_mode_date"] = today_str
    state.update(checkin_spec_state_updates(spec_payload))
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
