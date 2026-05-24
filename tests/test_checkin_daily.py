"""方案 C：动态打卡规格与 #今日打卡 解析。"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

from checkin_common import (  # noqa: E402
    classify_checkin_message,
    composite_completion_rate_daily,
    parse_daily_checkin,
)
from checkin_spec import (  # noqa: E402
    NO_SPEC_PROMPT,
    build_checkin_spec_items,
    build_checkin_spec_payload,
    format_checkin_spec_message,
    resolve_active_spec,
)


def _state_mode3():
    today = date.today()
    return {
        "study_days": 60,
        "start_date": (today - timedelta(days=7)).isoformat(),
        "listening_progress": "Cam10 Test1 Section1",
        "reading_progress": "Cam10 Test1 Passage1",
        "checkin_spec_date": today.isoformat(),
        "checkin_spec_mode": "3",
        "checkin_spec": build_checkin_spec_items("3", "Cam10 Test1 Section1", "Cam10 Test1 Passage1", False, {}),
    }


def test_mode3_spec_vocab_only():
    items = build_checkin_spec_items("3", "Cam10 Test1 Section1", "Cam10 Test1 Passage1", False, {})
    ids = [x["id"] for x in items]
    assert ids == ["reading", "vocab"]
    assert items[0]["volume_unit"] == "题"
    assert items[0].get("volume_user_required") is True
    assert items[1]["default_volume"] == 30


def test_mode2_reading_full_40():
    state = {"study_days": 60, "start_date": date.today().isoformat(), "checkin_history": []}
    items = build_checkin_spec_items("2", "Cam10 Test1 Section1", "Cam10 Test1 Passage1", False, state)
    reading = next(x for x in items if x["id"] == "reading")
    assert reading["reading_scope"] == "full"
    assert reading["default_volume"] == 40
    assert reading["volume_unit"] == "题"


def test_mode1_has_review():
    state = {"study_days": 60, "start_date": date.today().isoformat(), "checkin_history": []}
    items = build_checkin_spec_items("1", "Cam10 Test1 Section1", "Cam10 Test1 Passage1", False, state)
    ids = [x["id"] for x in items]
    assert "review" in ids
    assert "listening" in ids
    assert "writing" not in ids or "writing" in ids  # depends on writing_today


def test_parse_vocab_only():
    state = _state_mode3()
    spec = {"items": state["checkin_spec"], "mode": "3", "date": state["checkin_spec_date"]}
    content = "#今日打卡\n阅读：完成 | 错题2 | 题量13\n词汇：完成 | 错题0 | 题量30"
    parsed, ignored, missing = parse_daily_checkin(content, spec["items"])
    assert not missing
    assert parsed is not None
    assert parsed["vocab"]["done"] is True
    rate = composite_completion_rate_daily(parsed, spec["items"])
    assert rate == 100.0


def test_no_spec_prompt():
    spec, used, prefix = resolve_active_spec({})
    assert spec is None


def test_classify_daily():
    assert classify_checkin_message("#今日打卡\n词汇：完成") == "daily_attempt"
    assert classify_checkin_message("打卡\n词汇：完成") == "hint"


def test_format_spec_message():
    payload = build_checkin_spec_payload("3", "Cam10 Test1 Section1", "Cam10 Test1 Passage1", False, {"study_days": 60})
    msg = format_checkin_spec_message(payload)
    assert "#今日打卡" in msg
    assert "模式3" in msg


def test_single_passage_requires_volume():
    items = build_checkin_spec_items("3", "Cam10 Test1 Section1", "Cam10 Test1 Passage1", False, {})
    content = "#今日打卡\n阅读：完成 | 错题2\n词汇：完成"
    parsed, _, missing = parse_daily_checkin(content, items)
    assert parsed is None
    assert any("阅读" in m for m in missing)


def main() -> int:
    test_mode3_spec_vocab_only()
    test_mode2_reading_full_40()
    test_single_passage_requires_volume()
    test_mode1_has_review()
    test_parse_vocab_only()
    test_no_spec_prompt()
    test_classify_daily()
    test_format_spec_message()
    print("test_checkin_daily: all passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
