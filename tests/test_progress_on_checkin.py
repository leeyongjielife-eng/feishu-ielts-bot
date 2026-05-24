"""验收：选模式不推进，打卡后才推进。"""
from __future__ import annotations

import copy
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

from mode_tasks import build_mode_tasks  # noqa: E402
from task_progress import build_today_task_progress, progress_updates_after_checkin  # noqa: E402

# Import advance fns from message-router module file
import importlib.util

_mr_path = Path(__file__).resolve().parent.parent / "bin" / "message-router.py"
_spec = importlib.util.spec_from_file_location("message_router_mod", _mr_path)
_mr = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mr)


def _base_state():
    return {
        "study_days": 60,
        "start_date": "2026-05-13",
        "listening_progress": "Cam10 Test1 Section1",
        "reading_progress": "Cam10 Test1 Passage1",
        "recovery_mode": False,
        "fail_streak": 0,
        "streak": 0,
        "checkin_history": [],
    }


def test_mode2_then_checkin_then_mode2_same_day():
    today = datetime.now().strftime("%Y-%m-%d")
    state = _base_state()
    listen0 = state["listening_progress"]
    read0 = state["reading_progress"]

    plan = build_mode_tasks("2", listen0, read0, False, state)
    mode_updates = {
        "today_task_progress": build_today_task_progress(
            listen0, read0, plan, today, state.get("today_task_progress")
        ),
    }
    state.update(mode_updates)
    assert state["listening_progress"] == listen0
    assert state["reading_progress"] == read0
    assert state["today_task_progress"]["advanced"] is False

    after_checkin = _mr._progress_updates_after_checkin(state, today)
    state.update(after_checkin)
    assert state["listening_progress"] == "Cam10 Test2 Section1"
    assert state["reading_progress"] == "Cam10 Test2 Passage1"
    assert state["today_task_progress"]["advanced"] is True

    listen1 = state["listening_progress"]
    plan2 = build_mode_tasks("2", listen1, state["reading_progress"], False, state)
    state["today_task_progress"] = build_today_task_progress(
        listen1, state["reading_progress"], plan2, today, state["today_task_progress"]
    )
    assert state["listening_progress"] == listen1
    assert state["today_task_progress"]["advanced"] is True

    again = _mr._progress_updates_after_checkin(state, today)
    assert again == {}
    assert state["listening_progress"] == listen1


def main() -> int:
    test_mode2_then_checkin_then_mode2_same_day()
    print("test_progress_on_checkin: all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
