"""
统一 ASCII 进度条：10 格，█ 实心，░ 空心。
make_bar(value, max_value, width=10) — ratio = clamp(value/max_value, 0..1)。
"""
from __future__ import annotations

import re
from typing import Tuple

DEFAULT_PROGRESS = "Cam10 Test1 Section1"
DEFAULT_READING_PROGRESS = "Cam10 Test1 Passage1"
PROGRESS_RE = re.compile(r"^Cam(\d+)\s+Test(\d+)\s+Section(\d+)$")
READING_PROGRESS_RE = re.compile(r"^Cam(\d+)\s+Test(\d+)\s+Passage(\d+)$")
MAX_BOOK = 18
MAX_TEST_PER_BOOK = 4
MAX_SECTION = 4
MAX_PASSAGE = 3


def make_bar(value: float, max_value: float, width: int = 10) -> str:
    """满格表示 value 达到 max_value（超出按满格显示）。"""
    if width < 1:
        return ""
    if max_value <= 0:
        return "░" * width
    try:
        v = float(value)
        m = float(max_value)
    except (TypeError, ValueError):
        return "░" * width
    ratio = max(0.0, min(1.0, v / m))
    filled = int(round(ratio * width))
    filled = max(0, min(width, filled))
    return "█" * filled + "░" * (width - filled)


def _parse_listen_progress(raw: str) -> Tuple[int, int, int]:
    m = PROGRESS_RE.match((raw or "").strip())
    if not m:
        m = PROGRESS_RE.match(DEFAULT_PROGRESS)
    if not m:
        return 10, 1, 1
    book, test, sec = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if book < 10 or book > MAX_BOOK or test < 1 or test > MAX_TEST_PER_BOOK or sec < 1 or sec > MAX_SECTION:
        return _parse_listen_progress(DEFAULT_PROGRESS)
    return book, test, sec


def listen_progress_bar_line(listening_progress: str) -> str:
    """听力进度条 + CamX TestY 标签（不含 Section）。"""
    book, test, sec = _parse_listen_progress(listening_progress)
    cur = (book - 10) * (MAX_TEST_PER_BOOK * MAX_SECTION) + (test - 1) * MAX_SECTION + (sec - 1)
    max_i = (MAX_BOOK - 10 + 1) * (MAX_TEST_PER_BOOK * MAX_SECTION) - 1
    bar = make_bar(float(cur), float(max_i))
    return f"学习进度：{bar}  Cam{book} Test{test}"


def _parse_reading_progress(raw: str) -> Tuple[int, int, int]:
    m = READING_PROGRESS_RE.match((raw or "").strip())
    if not m:
        m = READING_PROGRESS_RE.match(DEFAULT_READING_PROGRESS)
    if not m:
        return 10, 1, 1
    book, test, passage = int(m.group(1)), int(m.group(2)), int(m.group(3))
    if book < 10 or book > MAX_BOOK or test < 1 or test > MAX_TEST_PER_BOOK or passage < 1 or passage > MAX_PASSAGE:
        return _parse_reading_progress(DEFAULT_READING_PROGRESS)
    return book, test, passage


def read_progress_bar_line(reading_progress: str) -> str:
    """阅读进度条 + CamX TestY 标签（不含 Passage）。"""
    book, test, passage = _parse_reading_progress(reading_progress)
    cur = (book - 10) * (MAX_TEST_PER_BOOK * MAX_PASSAGE) + (test - 1) * MAX_PASSAGE + (passage - 1)
    max_i = (MAX_BOOK - 10 + 1) * (MAX_TEST_PER_BOOK * MAX_PASSAGE) - 1
    bar = make_bar(float(cur), float(max_i))
    return f"学习进度：{bar}  Cam{book} Test{test}"
