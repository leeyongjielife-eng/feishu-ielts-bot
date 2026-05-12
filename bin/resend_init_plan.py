#!/usr/bin/env python3
"""用 user_state.json 里已有成绩重新发送「成绩分析 + 备考路线图」两条消息（验收/补发）。"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
BIN = Path(__file__).resolve().parent
sys.path.insert(0, str(BIN))

from init_plan import format_score_analysis_message, generate_study_roadmap_text  # noqa: E402


def _load_build_placement_plan():
    spec = importlib.util.spec_from_file_location("ielts_message_router", BIN / "message-router.py")
    mod = importlib.util.module_from_spec(spec)
    assert spec.loader
    spec.loader.exec_module(mod)
    return mod.build_placement_plan

STATE_FILE = Path(os.environ.get("STATE_FILE", str(ROOT / "user_state.json")))
CHAT_ID = os.environ.get("FEISHU_IELTS_CHAT_ID", "oc_99000aba52da6814c200481c4dedf1ea")
LARK_CLI = os.environ.get("LARK_CLI") or shutil.which("lark-cli") or "/opt/homebrew/bin/lark-cli"


def send_text(text: str) -> None:
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
        f"ielts-init-plan-resend-{time.time_ns()}",
    ]
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or r.stdout.strip() or f"exit {r.returncode}")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--fallback-only", action="store_true", help="不调用 Gemini，仅用规则路线图")
    ap.add_argument("--commit", action="store_true", help="发送成功后把 study_plan_sent=true 写回 state")
    args = ap.parse_args()

    if not STATE_FILE.exists():
        print("STATE_FILE missing", file=sys.stderr)
        return 2
    state = json.loads(STATE_FILE.read_text(encoding="utf-8"))
    init = state.get("initial_scores")
    if not isinstance(init, dict) or not {"L", "R", "W", "S"}.issubset(init.keys()):
        print("user_state.json 缺少 initial_scores（L/R/W/S）", file=sys.stderr)
        return 2
    try:
        scores = {k: float(init[k]) for k in ("L", "R", "W", "S")}
        target = float(state.get("target_score", 6.5))
        study_days = int(state.get("study_days", 60))
    except (TypeError, ValueError) as e:
        print("分数/目标/天数解析失败:", e, file=sys.stderr)
        return 2

    start = str(state.get("start_date") or "")[:10]
    if not start or len(start) != 10:
        start = "2026-01-01"

    plan = _load_build_placement_plan()(scores, target, study_days)
    msg1 = format_score_analysis_message(plan, scores, target, study_days, start)
    listen_p = str(state.get("listening_progress") or "Cam10 Test1 Section1")
    msg2, src = generate_study_roadmap_text(
        plan, scores, target, study_days, start, listen_p, use_gemini=not args.fallback_only
    )
    print("roadmap_source:", src)
    print("--- msg1 preview ---\n", msg1[:400], "\n...")

    if not Path(LARK_CLI).exists():
        print("lark-cli not found:", LARK_CLI, file=sys.stderr)
        return 127

    send_text(msg1)
    time.sleep(0.5)
    send_text(msg2)
    print("OK: sent 2 messages to", CHAT_ID)

    if args.commit:
        state["study_plan_sent"] = True
        tmp = STATE_FILE.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        tmp.replace(STATE_FILE)
        print("OK: study_plan_sent=true written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
