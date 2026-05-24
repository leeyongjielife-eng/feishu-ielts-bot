"""今日任务进度快照与打卡后推进（选模式不推进，打卡成功才推进）。"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


def advance_by_plan(
    listening: str,
    reading: str,
    listening_mode: str,
    reading_mode: str,
    *,
    advance_listening_test,
    advance_progress,
    advance_reading_test,
    advance_reading_progress,
) -> Tuple[str, str]:
    new_listen = listening
    new_read = reading
    if listening_mode == "test":
        new_listen = advance_listening_test(listening)
    elif listening_mode == "step":
        new_listen = advance_progress(listening)
    if reading_mode == "test":
        new_read = advance_reading_test(reading)
    elif reading_mode == "step":
        new_read = advance_reading_progress(reading)
    return new_listen, new_read


def build_today_task_progress(
    listening_progress: str,
    reading_progress: str,
    plan: Dict[str, Any],
    today_str: str,
    existing: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    advanced = False
    if (
        isinstance(existing, dict)
        and str(existing.get("date", "")) == today_str
        and bool(existing.get("advanced"))
    ):
        advanced = True
    return {
        "listening": listening_progress,
        "reading": reading_progress,
        "date": today_str,
        "advanced": advanced,
        "listening_advance": str(plan.get("listening_advance", "none")),
        "reading_advance": str(plan.get("reading_advance", "none")),
    }


def progress_updates_after_checkin(
    state: Dict[str, Any],
    today_str: str,
    *,
    default_listen: str,
    default_read: str,
    advance_listening_test,
    advance_progress,
    advance_reading_test,
    advance_reading_progress,
) -> Dict[str, Any]:
    ttp = state.get("today_task_progress")
    if not isinstance(ttp, dict):
        return {}
    if str(ttp.get("date", "")) != today_str:
        return {}
    if bool(ttp.get("advanced")):
        return {}
    listen = str(state.get("listening_progress") or default_listen)
    read = str(state.get("reading_progress") or default_read)
    new_listen, new_read = advance_by_plan(
        listen,
        read,
        str(ttp.get("listening_advance", "none")),
        str(ttp.get("reading_advance", "none")),
        advance_listening_test=advance_listening_test,
        advance_progress=advance_progress,
        advance_reading_test=advance_reading_test,
        advance_reading_progress=advance_reading_progress,
    )
    new_ttp = dict(ttp)
    new_ttp["advanced"] = True
    return {
        "listening_progress": new_listen,
        "reading_progress": new_read,
        "today_task_progress": new_ttp,
    }
