#!/usr/bin/env python3
import json
import os
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN_DIR = Path(__file__).resolve().parent
if str(BIN_DIR) not in sys.path:
    sys.path.insert(0, str(BIN_DIR))
from checkin_common import (  # noqa: E402
    CHECKIN_FORMAT_HINT,
    apply_checkin_to_state,
    classify_checkin_message,
    composite_completion_rate,
    evaluate_state,
    format_structured_checkin_reply,
    parse_structured_checkin,
)
from checkin_motivation_image import try_send_checkin_motivation  # noqa: E402

LOG_DIR = ROOT / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_FILE = Path(os.environ.get("LOG_FILE", str(LOG_DIR / "checkin.log")))
STATE_FILE = Path(os.environ.get("STATE_FILE", str(ROOT / "user_state.json")))

CHAT_ID = os.environ.get("FEISHU_IELTS_CHAT_ID", "oc_99000aba52da6814c200481c4dedf1ea")

os.environ["PATH"] = f"/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:{os.environ.get('PATH', '')}"
default_home = str(ROOT.parent.parent)
os.environ["HOME"] = os.environ.get("HOME", default_home)
os.environ["USER"] = os.environ.get("USER", Path(os.environ["HOME"]).name)
os.environ["LOGNAME"] = os.environ.get("LOGNAME", os.environ["USER"])

LARK_CLI = os.environ.get("LARK_CLI") or shutil.which("lark-cli") or "/opt/homebrew/bin/lark-cli"


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


def send_feedback_message(text: str) -> None:
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


def load_state() -> dict:
    if not STATE_FILE.exists():
        return {}
    try:
        with STATE_FILE.open("r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def atomic_write_state(state: dict) -> None:
    tmp_file = STATE_FILE.with_suffix(".json.tmp")
    with tmp_file.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp_file.replace(STATE_FILE)


def ensure_defaults(state: dict) -> None:
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
    if "prev_week_listening_avg" not in state:
        state["prev_week_listening_avg"] = None
    if "prev_week_reading_errors_avg" not in state:
        state["prev_week_reading_errors_avg"] = None
    if "prev_week_writing_avg" not in state:
        state["prev_week_writing_avg"] = {}
    if "last_checkin_motivation_image" not in state:
        state["last_checkin_motivation_image"] = ""


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

    latest = messages[0]
    message_id = latest.get("message_id", "")
    msg_type = latest.get("msg_type", "")
    content = (latest.get("content") or "").strip()

    state = load_state()
    ensure_defaults(state)
    if state.get("last_checkin_message_id") == message_id:
        log(f"NOOP: already processed message_id={message_id}")
        return 0

    if msg_type != "text":
        log(f"NOOP: latest msg_type={msg_type}, not text")
        return 0

    kind = classify_checkin_message(content)
    if kind == "none":
        log(f"NOOP: latest text not checkin content={content[:80]!r}")
        return 0

    if kind == "structured":
        parsed = parse_structured_checkin(content)
        assert parsed is not None
        completion_rate = composite_completion_rate(parsed)
        evaluated = evaluate_state(completion_rate, int(state.get("fail_streak", 0) or 0), int(state.get("streak", 0) or 0))
        today_str = datetime.now().strftime("%Y-%m-%d")
        updates = apply_checkin_to_state(state, parsed, evaluated, completion_rate, today_str, message_id)
        state.update(updates)
        atomic_write_state(state)
        feedback_message = format_structured_checkin_reply(parsed, state, evaluated, completion_rate)
    elif kind == "legacy":
        state["last_checkin_message_id"] = message_id
        atomic_write_state(state)
        feedback_message = CHECKIN_FORMAT_HINT
    else:
        state["last_checkin_message_id"] = message_id
        atomic_write_state(state)
        feedback_message = (
            CHECKIN_FORMAT_HINT
            + "\n\n（当前内容无法解析，请检查四项是否齐全、换行，以及听力/阅读完成时的正确率与错题数。）"
        )

    try:
        send_feedback_message(feedback_message)
    except Exception as exc:
        log(f"ERROR: send feedback failed: {exc}")
        return 1

    if kind == "structured":
        sent = try_send_checkin_motivation(state, CHAT_ID, LARK_CLI, log)
        if sent:
            fresh = load_state()
            ensure_defaults(fresh)
            if fresh.get("last_checkin_message_id") == message_id:
                fresh["last_checkin_motivation_image"] = sent
                atomic_write_state(fresh)
            else:
                log("motivation state write skip: last_checkin_message_id drifted")

    log(f"OK: checkin kind={kind} message_id={message_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
