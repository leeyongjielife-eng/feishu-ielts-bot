"""
初始化后两条消息：成绩分析 + 备考路线图（Gemini 生成，失败则规则模板）。
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any, Dict, Tuple

from ai_caller import call_ai_with_fallback
from progress_bar import make_bar

SKILL_CN = {"L": "听力", "R": "阅读", "W": "写作", "S": "口语"}


def _phase_bounds(study_days: int) -> Tuple[int, int, int, int]:
    """返回 (p1_end, p2_start, p2_end, p3_start)，均为闭区间边界内的最后/第一天索引（从 1 起）。"""
    n = max(3, int(study_days))
    p1 = n // 3
    p2 = (2 * n) // 3
    return p1, p1 + 1, p2, p2 + 1


def format_score_analysis_message(
    plan: Dict[str, Any],
    scores: Dict[str, float],
    target_score: float,
    study_days: int,
    start_date_str: str,
) -> str:
    l, r, w, s = scores["L"], scores["R"], scores["W"], scores["S"]
    avg = float(plan["avg"])
    gap = max(0.0, round(float(target_score) - avg, 2))
    gap_s = f"+{gap:g}" if gap > 0 else f"{gap:g}"
    weak = SKILL_CN.get(plan["weakest_skill"], plan["weakest_skill"])
    try:
        s0 = datetime.strptime(str(start_date_str)[:10], "%Y-%m-%d").date()
        end_d = s0 + timedelta(days=max(0, int(study_days) - 1))
        end_str = end_d.isoformat()
    except ValueError:
        end_str = "（日期无效）"
    return (
        "📊 你的雅思基础分析\n"
        "────────────────\n"
        f"听力：{l:g}  {make_bar(l, 9.0)}\n"
        f"阅读：{r:g}  {make_bar(r, 9.0)}\n"
        f"写作：{w:g}  {make_bar(w, 9.0)}\n"
        f"口语：{s:g}  {make_bar(s, 9.0)}\n"
        f"均分：{avg:g} → 目标：{target_score:g}\n"
        f"差距：{gap_s}分\n"
        "\n"
        f"最弱项：{weak} ← 重点突破\n"
        f"预计达标时间：{int(study_days)}天（{end_str}）"
    )


def format_study_roadmap_fallback(
    plan: Dict[str, Any],
    scores: Dict[str, float],
    target_score: float,
    study_days: int,
    listening_progress: str,
) -> str:
    p1_end, p2_s, p2_end, p3_s = _phase_bounds(study_days)
    weak = SKILL_CN.get(plan["weakest_skill"], plan["weakest_skill"])
    gap = max(0.0, round(target_score - float(plan["avg"]), 2))
    l_h = plan["weekly_hours"]["L"]
    r_h = plan["weekly_hours"]["R"]
    w_h = plan["weekly_hours"]["W"]
    s_h = plan["weekly_hours"]["S"]
    return (
        f"🗓 你的{int(study_days)}天备考计划\n"
        "────────────────\n"
        f"第1-{p1_end}天（基础巩固）\n"
        "- 听力：每天1套，重点 Section 3/4；错题精听跟读。\n"
        "- 阅读：每天2篇，主攻判断题/配对题与定位速度。\n"
        "- 写作：每3天1篇 Task2，先搭框架再限时成文。\n"
        "- 词汇：每天30个，优先场景/学术主题词。\n"
        "\n"
        f"第{p2_s}-{p2_end}天（专项突破）\n"
        f"- 重点加强「{weak}」× 1.5 时间（当前均分 {plan['avg']}，目标 {target_score:g}，差距约 {gap:g} 分）。\n"
        "- 听力+阅读开始严格计时，复盘错因与同义替换。\n"
        "- 写作：Task1 与 Task2 交替，提交后使用 AI / #批改结果 追踪四维。\n"
        f"- 口语：每周不少于 {max(3, int(round(s_h)))} 次录音+复述（约 {s_h:g} 小时/周）。\n"
        "\n"
        f"第{p3_s}-{int(study_days)}天（冲刺模拟）\n"
        "- 每周至少1套完整计时模拟（听读连做），记录体力与注意力曲线。\n"
        "- 重点复盘错题类型与时间分配。\n"
        "- 写作提高频次，针对最弱维度改写范文句式。\n"
        "\n"
        "教材顺序：Cam10→Cam11→…→Cam18\n"
        f"当前进度：{listening_progress}\n"
        "────────────────\n"
        f"建议周总学时约 {round(sum(plan['weekly_hours'].values()), 1)} 小时（均分约 {plan['daily_hours']} 小时/日）。\n"
        "每天08:30我会根据你的状态动态调整任务。\n"
        "加油！💪"
    )


def build_gemini_study_plan_prompt(
    plan: Dict[str, Any],
    scores: Dict[str, float],
    target_score: float,
    study_days: int,
    start_date_str: str,
) -> str:
    l, r, w, s = scores["L"], scores["R"], scores["W"], scores["S"]
    weak = SKILL_CN.get(plan["weakest_skill"], plan["weakest_skill"])
    gaps = {k: round(target_score - scores[k], 2) for k in ("L", "R", "W", "S")}
    try:
        s0 = datetime.strptime(str(start_date_str)[:10], "%Y-%m-%d").date()
        end_d = s0 + timedelta(days=max(0, int(study_days) - 1))
        end_str = end_d.isoformat()
    except ValueError:
        end_str = "未知"
    return (
        "你是一位专业雅思教师。请基于以下真实数据，用简体中文给出可执行的备考路线图正文。\n\n"
        f"学生当前成绩：听力{l:g}、阅读{r:g}、写作{w:g}、口语{s:g}；四项与目标分差分别为："
        f"听力{gaps['L']:+g}、阅读{gaps['R']:+g}、写作{gaps['W']:+g}、口语{gaps['S']:+g}（正数表示距目标{target_score:g}分还需提升的幅度）。\n"
        f"四项均分约 {plan['avg']}，最薄弱项为「{weak}」（规划上应给予约 1.5 倍训练权重）。\n"
        f"目标总分：{target_score:g}；备考周期：{int(study_days)} 天，自 {start_date_str} 起，至约 {end_str}。\n"
        f"系统估算每日可承受学习时长约 {plan['daily_hours']} 小时（仅供参考，可在计划中微调）。\n\n"
        "请完成：\n"
        "1）用 2–4 句概括听、读、写、口各自主要薄弱原因（结合分数与常见失分点）。\n"
        f"2）将 {int(study_days)} 天划分为三阶段（基础巩固 / 专项突破 / 冲刺模拟），每阶段用条目列出训练重点、频率与计时要求；"
        f"在「专项突破」阶段必须明确写出如何重点加强「{weak}」。\n"
        "3）结尾单独两行：一行写「教材顺序：Cam10→Cam11→…→Cam18」；一行写「当前进度：[当前进度由系统同步]」（保留方括号原文，便于系统替换）。\n"
        "4）最后一行鼓励语 +「每天08:30我会根据你的状态动态调整任务。」\n\n"
        "输出格式要求：\n"
        "- 第一行必须是：🗓 你的N天备考计划（N 用数字替换）。\n"
        "- 下一行「────────────────」分隔线。\n"
        "- 不要输出「成绩分析」或与分数条重复的内容；不要 JSON、不要代码围栏；总长度建议不超过 2200 字。\n"
    )


def sanitize_roadmap_text(raw: str, study_days: int) -> str:
    text = (raw or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```[a-zA-Z]*\s*", "", text)
        text = re.sub(r"\s*```$", "", text).strip()
    if len(text) > 4000:
        text = text[:3990] + "\n…（内容过长已截断）"
    if not text.startswith("🗓"):
        lines = text.splitlines()
        text = f"🗓 你的{int(study_days)}天备考计划\n────────────────\n" + text
    return text.strip()


def replace_progress_placeholder(text: str, listening_progress: str) -> str:
    return text.replace("[当前进度由系统同步]", listening_progress)


def generate_study_roadmap_text(
    plan: Dict[str, Any],
    scores: Dict[str, float],
    target_score: float,
    study_days: int,
    start_date_str: str,
    listening_progress: str,
    *,
    use_gemini: bool = True,
) -> Tuple[str, str]:
    """
    Returns: (roadmap_body, source) where source is 'gemini' or 'fallback'.
    """
    if not use_gemini:
        fb = format_study_roadmap_fallback(plan, scores, target_score, study_days, listening_progress)
        return fb, "fallback"
    prompt = build_gemini_study_plan_prompt(plan, scores, target_score, study_days, start_date_str)
    result = call_ai_with_fallback(prompt)
    if result.get("ok") and (result.get("text") or "").strip():
        body = sanitize_roadmap_text(result["text"], study_days)
        body = replace_progress_placeholder(body, listening_progress)
        return body, "gemini"
    fb = format_study_roadmap_fallback(plan, scores, target_score, study_days, listening_progress)
    return fb, "fallback"
