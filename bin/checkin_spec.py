"""
当日打卡规格：与 mode_tasks 任务清单对齐，选模式后下发消息 B。
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any, Dict, List

from mode_tasks import _vocab_base, _writing_due, compute_day_phase

NO_SPEC_PROMPT = "请先回复 1/2/3 获取今日打卡模板"
CROSS_DAY_PREFIX = "今日任务未生成（尚未选 1/2/3）。已按昨日规格记录，选模式后将更新规格。"

ITEM_IDS = ("listening", "reading", "writing", "vocab", "review")


def _today_str() -> str:
    return date.today().strftime("%Y-%m-%d")


def _yesterday_str() -> str:
    return (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")


READING_QUESTIONS_FULL_SET = 40
READING_QUESTIONS_SINGLE_HINT = 14


def _reading_scope(mode: str, recovery_mode: bool) -> str:
    """full=全套 Passage1-3（题量默认40题）；single=单篇（题量须自填）。"""
    if not recovery_mode and mode in ("1", "2"):
        return "full"
    return "single"


def _writing_volume_default(mode: str, writing_today: bool, phase: int) -> int:
    if mode == "1" and writing_today:
        return 2
    return 1


def _item(
    item_id: str,
    label: str,
    default_volume: int,
    volume_unit: str,
    *,
    default_errors: int = 0,
    errors_na: bool = False,
    reading_scope: str = "",
    volume_user_required: bool = False,
    volume_hint: str = "",
) -> Dict[str, Any]:
    row: Dict[str, Any] = {
        "id": item_id,
        "label": label,
        "default_volume": int(default_volume),
        "default_errors": int(default_errors),
        "volume_unit": volume_unit,
        "errors_na": bool(errors_na),
        "required": True,
    }
    if reading_scope:
        row["reading_scope"] = reading_scope
    if volume_user_required:
        row["volume_user_required"] = True
    if volume_hint:
        row["volume_hint"] = volume_hint
    return row


def _reading_item(mode: str, recovery_mode: bool) -> Dict[str, Any]:
    scope = _reading_scope(mode, recovery_mode)
    if scope == "full":
        return _item(
            "reading",
            "阅读",
            READING_QUESTIONS_FULL_SET,
            "题",
            reading_scope="full",
            volume_hint="全套约40题",
        )
    return _item(
        "reading",
        "阅读",
        0,
        "题",
        reading_scope="single",
        volume_user_required=True,
        volume_hint="单篇请填实际题数",
    )


def build_checkin_spec_items(
    mode: str,
    listening_progress: str,
    reading_progress: str,
    recovery_mode: bool,
    state: Dict[str, Any],
) -> List[Dict[str, Any]]:
    """根据当日任务生成打卡项列表（与 build_mode_tasks 骨架一致）。"""
    _done, _study, phase, _ptitle, schedule_day = compute_day_phase(state)
    vocab_n = _vocab_base(phase, recovery_mode)
    writing_today = _writing_due(phase, schedule_day)
    items: List[Dict[str, Any]] = []

    if not recovery_mode:
        if mode in ("1", "2"):
            items.append(_item("listening", "听力", 40, "题"))
            items.append(_reading_item(mode, False))
            if mode == "1" and writing_today:
                items.append(_item("writing", "写作", 2, "篇", errors_na=True))
            elif mode == "2" and (phase == 3 or (phase == 2 and writing_today)):
                items.append(
                    _item(
                        "writing",
                        "写作",
                        _writing_volume_default(mode, True, phase),
                        "篇",
                        errors_na=True,
                    )
                )
            items.append(_item("vocab", "词汇", vocab_n, "个", errors_na=True))
            if mode == "1":
                items.append(_item("review", "复盘", 25, "分钟", errors_na=True))
        else:
            items.append(_reading_item("3", False))
            items.append(_item("vocab", "词汇", vocab_n, "个", errors_na=True))
        return items

    rvocab = 15
    if mode in ("1", "2"):
        items.append(_reading_item(mode, True))
        items.append(_item("vocab", "词汇", rvocab, "个", errors_na=True))
    else:
        items.append(_item("vocab", "词汇", rvocab, "个", errors_na=True))
    return items


def build_checkin_spec_payload(
    mode: str,
    listening_progress: str,
    reading_progress: str,
    recovery_mode: bool,
    state: Dict[str, Any],
    *,
    spec_date: str | None = None,
) -> Dict[str, Any]:
    d = spec_date or _today_str()
    return {
        "date": d,
        "mode": str(mode),
        "recovery_mode": bool(recovery_mode),
        "items": build_checkin_spec_items(mode, listening_progress, reading_progress, recovery_mode, state),
    }


def _example_line(item: Dict[str, Any]) -> str:
    label = item["label"]
    dv = item["default_volume"]
    de = item["default_errors"]
    unit = item["volume_unit"]
    hint = str(item.get("volume_hint") or "").strip()
    if item.get("errors_na"):
        tail = f"{unit}，{hint}" if hint else f"{unit}，默认{dv}"
        return f"{label}：完成 | 错题- | 题量{dv}（{tail}）"
    if item.get("volume_user_required"):
        sample = READING_QUESTIONS_SINGLE_HINT
        tail = hint or "单篇请填实际题数"
        return f"{label}：完成 | 错题{de} | 题量{sample}（{unit}，{tail}）"
    tail = hint or f"默认{dv}"
    return f"{label}：完成 | 错题{de} | 题量{dv}（{unit}，{tail}）"


def format_checkin_spec_message(payload: Dict[str, Any], *, mode_change_notice: bool = False) -> str:
    d = payload.get("date") or _today_str()
    mode = payload.get("mode", "?")
    items = payload.get("items") or []
    lines = [
        f"📋 今日打卡规格（{d} · 模式{mode}）",
    ]
    if mode_change_notice:
        lines.append("已更新今日任务与打卡规格；若已打卡，请按新模板重新发送 #今日打卡。")
        lines.append("")
    lines.append("请复制下面模板，改数字后发送：")
    lines.append("")
    lines.append("#今日打卡")
    for it in items:
        lines.append(_example_line(it))
    lines.extend(
        [
            "",
            "说明：",
            "· 完成：完成 / 未完成",
            "· 错题：听力/阅读填错题数；词汇/写作/复盘填 0 或 -",
            "· 题量：可省略则按括号内默认（听力=题；阅读全套=40题，单篇须自填题数；词汇=个；复盘=分钟）",
            "· 阅读错题：在「本次实际做题数」里错了几题（不是按篇粗算）",
            "· 规格外的行将忽略",
        ]
    )
    return "\n".join(lines)


def spec_from_state(state: Dict[str, Any]) -> Dict[str, Any] | None:
    items = state.get("checkin_spec")
    if not isinstance(items, list) or not items:
        return None
    return {
        "date": str(state.get("checkin_spec_date") or ""),
        "mode": str(state.get("checkin_spec_mode") or ""),
        "recovery_mode": bool(state.get("checkin_spec_recovery_mode")),
        "items": items,
    }


def resolve_active_spec(state: Dict[str, Any]) -> tuple[Dict[str, Any] | None, bool, str]:
    """
    返回 (spec_payload, used_yesterday, reply_prefix)。
    used_yesterday 时 prefix 为跨日提示。
    """
    today = _today_str()
    payload = spec_from_state(state)
    if not payload:
        return None, False, ""
    spec_date = str(payload.get("date") or "")
    if spec_date == today:
        return payload, False, ""
    yesterday = _yesterday_str()
    if spec_date == yesterday:
        return payload, True, CROSS_DAY_PREFIX + "\n\n"
    return None, False, ""


def checkin_spec_state_updates(
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    return {
        "checkin_spec_date": payload["date"],
        "checkin_spec_mode": payload["mode"],
        "checkin_spec_recovery_mode": payload.get("recovery_mode", False),
        "checkin_spec": payload["items"],
    }


def is_daily_checkin_attempt(content: str) -> bool:
    raw = (content or "").strip()
    if not raw:
        return False
    first = raw.splitlines()[0].strip().replace("：", ":").rstrip(":")
    if first in ("#今日打卡", "今日打卡"):
        return True
    if raw.startswith("#今日打卡") or raw.startswith("今日打卡"):
        return True
    return False
