#!/usr/bin/env python3
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
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
CHECKIN_RE = re.compile(r"^\s*打卡\s*[:：]?\s*(\d+)\s*/\s*(\d+)\s*$")


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


def format_percent(value: float) -> str:
    if float(value).is_integer():
        return f"{int(value)}%"
    return f"{value:.2f}%"


def evaluate_state(completion_rate: float, current_fail_streak: int, current_streak: int) -> dict:
    fail_streak = current_fail_streak + 1 if completion_rate < 50 else 0
    streak = current_streak + 1 if completion_rate >= 80 else 0

    if completion_rate < 50 or fail_streak >= 2:
        return {
            "state": "🔴 差",
            "fail_streak": fail_streak,
            "streak": streak,
            "recovery_mode": fail_streak >= 2,
        }
    if completion_rate >= 80:
        return {
            "state": "🟢 正常",
            "fail_streak": fail_streak,
            "streak": streak,
            "recovery_mode": False,
        }
    return {
        "state": "🟡 不稳定",
        "fail_streak": fail_streak,
        "streak": streak,
        "recovery_mode": False,
    }


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
    if state.get("last_checkin_message_id") == message_id:
        log(f"NOOP: already processed message_id={message_id}")
        return 0

    if msg_type != "text":
        log(f"NOOP: latest msg_type={msg_type}, not text")
        return 0

    match = CHECKIN_RE.match(content)
    if not match:
        log(f"NOOP: latest text not checkin format content={content}")
        return 0

    done = int(match.group(1))
    total = int(match.group(2))
    if total <= 0 or done < 0 or done > total:
        log(f"NOOP: invalid checkin ratio done={done} total={total}")
        return 0

    completion_rate = round(done * 100 / total, 2)
    last_checkin_date = datetime.now().strftime("%Y-%m-%d")
    current_fail_streak = int(state.get("fail_streak", 0) or 0)
    current_streak = int(state.get("streak", 0) or 0)
    evaluated = evaluate_state(completion_rate, current_fail_streak, current_streak)

    state["completion_rate"] = completion_rate
    state["last_checkin_date"] = last_checkin_date
    state["last_checkin_message_id"] = message_id
    state["state"] = evaluated["state"]
    state["fail_streak"] = evaluated["fail_streak"]
    state["streak"] = evaluated["streak"]
    state["recovery_mode"] = evaluated["recovery_mode"]

    atomic_write_state(state)
    feedback_message = (
        f"📊 今日状态：{evaluated['state']}\n"
        f"完成率：{format_percent(completion_rate)}\n"
        f"打卡项数：{done}/{total}\n"
        f"连续完成：{evaluated['streak']}天"
    )
    if evaluated["recovery_mode"]:
        feedback_message += "\n⚠️ Recovery Mode 已触发：明日任务减半。"

    try:
        send_feedback_message(feedback_message)
    except Exception as exc:
        log(f"ERROR: send feedback failed: {exc}")
        return 1

    log(
        "OK: parsed checkin "
        f"done={done} total={total} completion_rate={completion_rate} "
        f"last_checkin_date={last_checkin_date} message_id={message_id} "
        f"state={evaluated['state']} fail_streak={evaluated['fail_streak']} streak={evaluated['streak']}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
