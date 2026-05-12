#!/usr/bin/env python3
"""结构化打卡成功后：从仓库 pictures/ 随机选图发送（尽量均匀、且不与上一张同名）。"""
from __future__ import annotations

import os
import random
import subprocess
import time
from pathlib import Path
from typing import Any, Callable, Dict, Optional

ROOT = Path(__file__).resolve().parent.parent
PICTURES_DIR = ROOT / "pictures"
ALLOWED_SUFFIX = frozenset({".jpg", ".jpeg", ".png"})
TEXT_LINE = "今日能量补给 💪"


def list_motivation_images() -> list[Path]:
    if not PICTURES_DIR.is_dir():
        return []
    return [
        p
        for p in PICTURES_DIR.iterdir()
        if p.is_file() and p.suffix.lower() in ALLOWED_SUFFIX
    ]


def pick_motivation_image(state: Dict[str, Any]) -> Optional[Path]:
    files = list_motivation_images()
    if not files:
        return None
    exclude = str(state.get("last_checkin_motivation_image") or "").strip()
    pool = [p for p in files if p.name != exclude] if exclude else list(files)
    if not pool:
        pool = list(files)
    return random.choice(pool)


def send_motivation_pair(lark_cli: str, chat_id: str, image_path: Path) -> None:
    resolved = image_path.resolve()
    root = ROOT.resolve()
    if not resolved.is_file():
        raise FileNotFoundError(str(resolved))
    try:
        rel = resolved.relative_to(root)
    except ValueError:
        rel = Path(os.path.relpath(str(resolved), str(root)))
    rel_str = rel.as_posix()
    if not rel_str.startswith("./"):
        rel_str = "./" + rel_str
    base_cmd = [
        lark_cli,
        "im",
        "+messages-send",
        "--as",
        "user",
        "--chat-id",
        chat_id,
    ]
    r1 = subprocess.run(
        [*base_cmd, "--text", TEXT_LINE],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    if r1.returncode != 0:
        raise RuntimeError(
            f"motivation text send exit={r1.returncode} stderr={r1.stderr!r} stdout={r1.stdout!r}"
        )
    time.sleep(0.35)
    r2 = subprocess.run(
        [*base_cmd, "--image", rel_str],
        capture_output=True,
        text=True,
        cwd=str(root),
    )
    if r2.returncode != 0:
        raise RuntimeError(
            f"motivation image send exit={r2.returncode} stderr={r2.stderr!r} stdout={r2.stdout!r}"
        )


def try_send_checkin_motivation(
    state: Dict[str, Any],
    chat_id: str,
    lark_cli: str,
    log: Callable[[str], None],
) -> Optional[str]:
    """
    先发一行字再发图（lark-cli 同条命令不能同时 --text 与 --image）。
    成功返回所选图片 basename；目录无图或失败则返回 None（仅打日志）。
    """
    if not Path(lark_cli).exists():
        log(f"motivation skip: lark-cli not found at {lark_cli}")
        return None
    path = pick_motivation_image(state)
    if path is None:
        log("motivation skip: no images under pictures/")
        return None
    try:
        send_motivation_pair(lark_cli, chat_id, path)
    except Exception as exc:
        log(f"motivation error: {exc}")
        return None
    log(f"motivation ok: image={path.name!r}")
    return path.name
