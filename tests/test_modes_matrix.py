"""模式任务矩阵自检（强化版）：3 模式 × 3 阶段 × recovery 开/关。

通过 start_date 真正驱动 phase。断言要点：
  - 文案骨架（听/读/写/词汇/复盘）符合 PRD：
      Mode 1: 听+读全套 + 写作(Task1+Task2 或提示) + 词汇 + 复盘
      Mode 2: 听+读全套 + 词汇 + (phase 3 或 phase 2 writing_due) 写作
      Mode 3: 1 篇阅读 + 词汇
  - 词汇配额：phase 1 → 30, phase 2/3 → 45；Recovery → 15
  - 推进粒度：
      Mode 1/2 非 recovery: test/test
      Mode 3   非 recovery: none/step
      Mode 1/2 recovery:    none/step
      Mode 3   recovery:    none/none
  - 阶段标题正确（第一/二/三阶段）
  - Mode 1 写作节奏：phase 1 每 3 天 1 篇；phase 2 每 2 天 1 篇；phase 3 每日
  - Mode 3 phase 3 周一（schedule_day % 7 == 1）出现模考提示
"""
from __future__ import annotations

import sys
from datetime import date, timedelta
from pathlib import Path
from typing import Dict, List, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bin"))

from mode_tasks import build_mode_tasks  # noqa: E402

LISTEN = "Cam10 Test1 Section1"
READING = "Cam10 Test1 Passage1"


def make_state(schedule_day: int, weakest: str = "W") -> Dict:
    today = date.today()
    sd = today - timedelta(days=schedule_day - 1)
    return {
        "study_days": 60,
        "listening_progress": LISTEN,
        "reading_progress": READING,
        "weakest_skill": weakest,
        "start_date": sd.isoformat(),
        "checkin_history": [],
    }


def expect(label: str, cond: bool, errors: List[str]) -> None:
    if not cond:
        errors.append(f"  ✗ {label}")


def vocab_expected(phase: int, recovery: bool) -> int:
    if recovery:
        return 15
    return 45 if phase >= 2 else 30


def writing_due(phase: int, day: int) -> bool:
    if phase == 1:
        return day % 3 == 1
    if phase == 2:
        return day % 2 == 1
    return True


def run_case(mode: str, phase: int, recovery: bool) -> Tuple[List[str], str]:
    schedule_day = {1: 1, 2: 21, 3: 43}[phase]
    state = make_state(schedule_day, weakest="W")
    plan = build_mode_tasks(mode, LISTEN, READING, recovery, state)
    msg = plan["message"]
    la, ra = plan.get("listening_advance"), plan.get("reading_advance")
    errs: List[str] = []

    title = {1: "第一阶段：基础巩固", 2: "第二阶段：专项突破", 3: "第三阶段：冲刺模拟"}[phase]
    expect(f"阶段标题 contains '{title}'", title in msg, errs)
    v = vocab_expected(phase, recovery)
    expect(f"词汇配额 {v}", f"词汇：{v}个" in msg, errs)

    if not recovery:
        if mode == "1":
            expect("L1 全套", "听力：Cam10 Test1（全套 Section 1–4）" in msg, errs)
            expect("R1 全套", "阅读：Cam10 Test1（全套 Passage 1–3）" in msg, errs)
            expect("复盘存在", "复盘：" in msg, errs)
            wd = writing_due(phase, schedule_day)
            if wd:
                expect("W1 Task1", "Task 1 占位题目" in msg, errs)
                expect("W1 Task2", "Task 2 占位题目" in msg, errs)
            else:
                expect("W1 提示", "今日无完整计时篇" in msg or "审题提纲" in msg, errs)
            expect("advance=test/test", la == "test" and ra == "test", errs)
        elif mode == "2":
            expect("L2 全套", "听力：Cam10 Test1（全套 Section 1–4）" in msg, errs)
            expect("R2 全套", "阅读：Cam10 Test1（全套 Passage 1–3）" in msg, errs)
            expect("M2 无复盘", "复盘：" not in msg, errs)
            wd = writing_due(phase, schedule_day)
            if phase == 3:
                expect("M2 phase3 必带写作", "Task 2 占位题目" in msg, errs)
            elif phase == 2 and wd:
                expect("M2 phase2 双天带写作", "Task 2 占位题目" in msg, errs)
            else:
                expect("M2 无写作", "Task 2 占位题目" not in msg, errs)
            expect("advance=test/test", la == "test" and ra == "test", errs)
        else:
            expect("M3 无听力", "听力：" not in msg, errs)
            expect("M3 含阅读", "阅读：Cam10 Test1 Passage1" in msg, errs)
            expect("M3 无写作", "Task 2 占位题目" not in msg, errs)
            expect("advance=none/step", la == "none" and ra == "step", errs)
            if phase == 3 and (schedule_day - 1) % 7 == 0:
                expect("M3 phase3 模考提示", "本周模考" in msg, errs)
    else:
        expect("Recovery 标记", "Recovery Mode 生效" in msg, errs)
        if mode in ("1", "2"):
            expect("Rec 无听力", "听力：" not in msg, errs)
            expect("Rec 含阅读", "阅读：Cam10 Test1 Passage1" in msg, errs)
            expect("Rec 词汇=15", "词汇：15个" in msg, errs)
            expect("advance=none/step", la == "none" and ra == "step", errs)
        else:
            expect("Rec3 仅词汇", "听力：" not in msg and "阅读：" not in msg, errs)
            expect("advance=none/none", la == "none" and ra == "none", errs)

    return errs, msg


def main() -> int:
    fails = 0
    for mode in ("1", "2", "3"):
        for phase in (1, 2, 3):
            for rec in (False, True):
                label = f"mode={mode} phase={phase} recovery={rec}"
                errs, msg = run_case(mode, phase, rec)
                print("=" * 70)
                print(f"[{label}]")
                print("-" * 70)
                print(msg)
                if errs:
                    fails += 1
                    print(f"\n❌ FAIL ({label})")
                    for e in errs:
                        print(e)
                else:
                    print(f"\n✅ PASS ({label})")
    print("\n" + "=" * 70)
    if fails:
        print(f"汇总：{fails} 个组合失败")
        return 1
    print("汇总：18 个组合全部通过 ✅")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
