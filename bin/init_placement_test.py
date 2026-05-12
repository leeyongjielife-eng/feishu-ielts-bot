#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""首次初始化：发送雅思学术模拟测试材料（6 条飞书消息），并写入 mock_test_sent。

每条 ≤3000 字符；消息间隔 2 秒。由 send-daily-modes.sh 在未完成 initial_scores 且未发送过模拟卷时调用。
内容自 ielts_academic_mock_exam.md 固化；运行时不再读取外部文件。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import fcntl
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict

ROOT = Path(__file__).resolve().parent.parent

MSG_1 = '👋 欢迎使用飞书IELTS学习监督Bot！\n\n在开始之前，请先完成一次模拟测试来评估你的当前水平。\n\n📋 测试说明：\n- 阅读：60分钟，40题（附在下方文档）\n- 写作：60分钟，Task1+Task2\n- 听力：请自行准备Cambridge IELTS真题（Cam10-18任意一套）\n- 口语：根据Part2题目自我评估\n\n完成后请对照答案自评，然后回复：\n#我的成绩 L:x.x R:x.x W:x.x S:x.x 目标:6.5 天数:60\n（可将「目标」「天数」改为你的真实目标与备考天数）'

MSG_2 = '【模拟卷 2/6】阅读 Passage 1 + Questions 1–13\n\n### READING PASSAGE 1\n*You should spend about 20 minutes on Questions 1-13, which are based on Reading Passage 1 below.*\n\n**The Return of the Sea Otter**\n[A] Sea otters, marine mammals native to the coasts of the northern and eastern North Pacific Ocean, were once hunted to the brink of extinction for their thick, luxurious fur. By the early 20th century, their population had dropped from an estimated 300,000 to fewer than 2,000 worldwide. However, recent conservation efforts have sparked a remarkable recovery in several coastal regions.\n[B] The ecological impact of this recovery has been profound. Sea otters are considered a "keystone species," meaning their presence is crucial to maintaining the balance of their ecosystem. Their primary diet consists of sea urchins. Without otters to keep the urchin population in check, urchins consume the holdfasts of giant kelp, devastating kelp forests. These forests are vital nurseries for numerous fish species and act as significant carbon sinks.\n[C] Interestingly, the return of the sea otter has not been universally welcomed. Commercial fishers, particularly those harvesting shellfish like crab and sea urchins, view the otters as direct competitors. In areas where otter populations have rebounded, there has been a noticeable decline in the commercial catch of certain shellfish, leading to economic and political friction.\n\n**Questions 1-5: TRUE / FALSE / NOT GIVEN**\n*Do the following statements agree with the information given in Reading Passage 1?*\n* **TRUE** if the statement agrees with the information\n* **FALSE** if the statement contradicts the information\n* **NOT GIVEN** if there is no information on this\n\n1. At their lowest point, the global sea otter population was under 2,000.\n2. Sea urchins are the only food source for sea otters.\n3. Kelp forests help to reduce the amount of carbon dioxide in the atmosphere.\n4. The fishing industry has actively supported the reintroduction of sea otters.\n5. Crabs are a type of shellfish harvested by commercial fishers.\n\n**Questions 6-13: Notes Completion**\n*Complete the notes below. Choose **NO MORE THAN TWO WORDS** from the passage for each answer.*\n\n**The Sea Otter Ecosystem**\n* Historic decline caused by the demand for their **6** __________.\n* Classified as a **7** __________ due to their importance to the environment.\n* They control the numbers of **8** __________.\n* This prevents the destruction of **9** __________, which serve as **10** __________ for many types of fish.\n* Conflict arises with **11** __________ who catch shellfish.\n* Competition for resources has caused a reduction in the **12** __________ of shellfish in some regions.\n* This situation has resulted in both economic and **13** __________ issues.\n\n---'

MSG_3 = "【模拟卷 3/6】阅读 Passage 2 + Questions 14–26\n\n### READING PASSAGE 2\n*You should spend about 20 minutes on Questions 14-26, which are based on Reading Passage 2 below.*\n\n**The Evolution of Workplace Architecture**\n**A.** The physical design of office spaces has undergone radical transformations over the past century. In the early 1900s, the dominant philosophy was Taylorism, which treated the office like a factory. Desks were arranged in rigid rows, supervised by managers in enclosed offices. \n**B.** The 1960s saw the introduction of the 'Bürolandschaft' (office landscape) in Germany. This concept broke down walls and arranged desks in organic, curved groupings separated by potted plants and screens, aiming to foster communication and a more egalitarian atmosphere.\n**C.** In the 1980s, the 'cubicle farm' emerged as a cost-effective compromise. It provided a degree of privacy and standardization but often led to employee dissatisfaction due to isolation and lack of natural light. Today, the pendulum has swung towards open-plan offices, though recent studies suggest they may actually decrease face-to-face interaction as workers retreat into headphones to escape the noise.\n\n**Questions 14-18: Matching Headings**\n*Choose the correct heading for each paragraph from the list of headings below.*\n**List of Headings:**\ni. The rise of the cubicle\nii. A return to factory conditions\niii. Organic layouts for equality\niv. The modern open-plan paradox\nv. Efficiency above all: the early 20th century\n\n14. Paragraph A\n15. Paragraph B\n16. Paragraph C\n\n*(Note: In a full-length passage, there would be 5-8 paragraphs. For this mock layout, remaining heading questions are skipped to fit the format, but questions 17 and 18 refer to hypothetical Paragraphs D and E).*\n17. Paragraph D (The role of technology in design) - *Hypothetical*\n18. Paragraph E (Future predictions for hybrid work) - *Hypothetical*\n\n**Questions 19-22: Matching Features**\n*Match each office design (A-C) with its description (19-22).*\n**A** Taylorism  **B** Bürolandschaft  **C** Cubicle farm\n19. Intended to create a sense of equality among staff.\n20. Criticized for cutting employees off from sunlight.\n21. Treated office workers similarly to industrial workers.\n22. Used natural elements to separate workspaces.\n\n**Questions 23-26: Multiple Choice**\n*Choose the correct letter, A, B, C, or D.*\n23. The main goal of Taylorism in office design was:\n    A) Employee comfort  B) Maximum productivity  C) Creative collaboration  D) Cost reduction\n24. According to recent studies, open-plan offices:\n    A) Increase productivity significantly.\n    B) Make workers more social.\n    C) Can result in less direct communication.\n    D) Are universally disliked.\n25. The Bürolandschaft concept originated in:\n    A) The United States  B) The 1980s  C) Germany  D) The 1920s\n26. Cubicles were initially adopted because they:\n    A) Looked modern.  B) Were a cheaper middle-ground.  C) Increased noise levels.  D) Promoted teamwork.\n\n---"

MSG_4 = '【模拟卷 4/6】阅读 Passage 3 + Questions 27–40\n\n### READING PASSAGE 3\n*You should spend about 20 minutes on Questions 27-40, which are based on Reading Passage 3.*\n\n**Neuroplasticity: The Adaptable Brain**\nFor centuries, scientists believed that the adult human brain was essentially a static organ. The prevailing dogma was that once we reached adulthood, the brain\'s physical structure was fixed, and any lost brain cells were gone forever. However, the discovery of neuroplasticity has revolutionized neuroscience. Neuroplasticity refers to the brain\'s ability to reorganize itself by forming new neural connections throughout life. This adaptability allows the neurons (nerve cells) in the brain to compensate for injury and disease and to adjust their activities in response to new situations or changes in their environment.\n\n**Questions 27-32: YES / NO / NOT GIVEN**\n*Do the following statements agree with the claims of the writer?*\n27. Historically, it was believed that adult brains could not physically change.\n28. Neuroplasticity is a concept that has been understood for over a century.\n29. The brain can form new connections even after a severe injury.\n30. Mental exercises are more effective than physical exercises for neuroplasticity.\n31. Neurons are unable to adjust to environmental changes.\n32. The discovery of neuroplasticity changed the field of neuroscience significantly.\n\n**Questions 33-37: Summary Completion**\n*Complete the summary using the list of words, A-I, below.*\nThe traditional scientific view held that the adult brain was **33** __________. It was thought that damaged cells could not be replaced. The concept of **34** __________ changed this belief, showing that the brain can create new **35** __________. This process is crucial because it helps the brain recover from **36** __________ and adapt to new **37** __________.\n\nA. flexible  B. fixed  C. injury  D. environments  E. neurons  F. static  G. connections  H. neuroplasticity  I. diseases\n\n**Questions 38-40: Multiple Choice**\n*Choose the correct letter, A, B, C, or D.*\n38. The word "dogma" in the first paragraph refers to:\n    A) A new scientific discovery  B) An established, unquestioned belief  C) A type of brain scan  D) A medical treatment\n39. What is the primary function of neuroplasticity?\n    A) To prevent aging  B) To reorganize neural pathways  C) To increase the brain\'s size  D) To produce more cerebrospinal fluid\n40. What is the writer\'s main purpose in this passage?\n    A) To explain a major shift in our understanding of the brain.\n    B) To argue against modern neuroscience.\n    C) To provide a medical guide for brain injuries.\n    D) To compare animal and human brains.\n\n---'

MSG_5 = '【模拟卷 5/6】写作 + 口语\n\n## SECTION 2: WRITING\n**Time Allowed:** 60 minutes\n**Scoring:** Task 2 carries twice the weight of Task 1. (Assessed on Task Achievement, Coherence & Cohesion, Lexical Resource, Grammatical Range & Accuracy).\n\n### WRITING TASK 1\n**Recommended Time:** 20 minutes\n**Word Count:** Minimum 150 words\n\n**The chart below shows the percentage of the adult population who were overweight or obese in four different countries from 1980 to 2010.**\n*(Since images cannot be displayed, assume a line graph showing: USA rising steeply from 30% to 70%; UK rising from 25% to 60%; Australia rising from 25% to 55%; Japan remaining relatively stable between 10% and 15%.)*\n\n**Summarise the information by selecting and reporting the main features, and make comparisons where relevant.**\n\n### WRITING TASK 2\n**Recommended Time:** 40 minutes\n**Word Count:** Minimum 250 words\n\n**In today’s world, private companies rather than the government pay for and conduct most scientific research. Do you think the advantages of this outweigh the disadvantages?**\n\n**Give reasons for your answer and include any relevant examples from your own knowledge or experience.**\n\n---\n\n## SECTION 3: SPEAKING\n**Time Allowed:** 11 - 14 minutes\n**Format:** One-to-one interview\n\n### PART 1: Introduction and Interview (4-5 minutes)\n*(The examiner will ask you familiar questions about yourself.)*\n**Topic: Work or Study**\n* Do you work or are you a student?\n* What do you like most about your job/studies?\n* Is there anything you dislike about your daily routine?\n\n**Topic: Weather**\n* What kind of weather do you like the most?\n* Does the weather affect your mood?\n* Do you prefer hot or cold countries?\n\n### PART 2: Long Turn (3-4 minutes)\n*(You will be given exactly 1 minute to prepare, and you must speak for 1 to 2 minutes.)*\n\n**CUE CARD**\n**Describe a time when you received good news.**\nYou should say:\n* What the news was\n* Who gave you the news\n* When you received it\n* And explain why this news made you feel happy.\n\n### PART 3: Two-Way Discussion (4-5 minutes)\n*(The examiner will ask abstract questions related to the topic in Part 2.)*\n**Topic: News and Media**\n* How do most people in your country get their news today compared to the past?\n* Do you think we can trust all the news we see on the internet? Why or why not?\n* How does the spread of "fake news" affect society?\n* Should the government control or censor the news media?'

MSG_6 = '【模拟卷 6/6】答案（阅读对照）\n\n📝 阅读参考答案\nPassage 1: 1.TRUE 2.NOT GIVEN 3.TRUE 4.FALSE 5.NOT GIVEN\n6.fur 7.keystone species 8.sea urchins 9.kelp forests 10.nurseries\n11.commercial fishers 12.commercial catch 13.political\n\nPassage 2: 14.v 15.iii 16.i 19.B 20.C 21.A 22.B\n23.B 24.C 25.C 26.B\n\nPassage 3: 27.YES 28.NO 29.YES 30.NOT GIVEN 31.NO 32.YES\n33.F 34.H 35.G 36.C 37.D 38.B 39.B 40.A\n\n写作和口语请根据雅思评分标准自评，或提交写作用 #写作提交 获取AI批改。'



PLACEMENT_MESSAGES: tuple[str, ...] = (MSG_1, MSG_2, MSG_3, MSG_4, MSG_5, MSG_6)


@contextmanager
def _state_lock(lock_file: Path):
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with lock_file.open("a+") as lockf:
        fcntl.flock(lockf.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lockf.fileno(), fcntl.LOCK_UN)


def _lark_send_text(chat_id: str, lark_cli: str, text: str, idempotency_key: str) -> None:
    dry = os.environ.get("DRY_RUN", "").strip() in ("1", "true", "yes")
    cmd = [
        lark_cli,
        "im",
        "+messages-send",
        "--as",
        "user",
        "--chat-id",
        chat_id,
        "--text",
        text,
        "--idempotency-key",
        idempotency_key,
    ]
    if dry:
        cmd.append("--dry-run")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"lark-cli exit={result.returncode} stderr={result.stderr.strip()} stdout={result.stdout.strip()}"
        )


def _load_state(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}


def _write_state(path: Path, state: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    tmp.replace(path)


def send_placement_test(state_path: Path, chat_id: str, lark_cli: str) -> bool:
    """若应发送模拟卷：依次发送 MSG_1..6，写入 mock_test_sent。已发送过则返回 False。"""
    lock_file = Path(os.environ.get("STATE_LOCK_FILE", str(ROOT / "user_state.json.lock")))
    with _state_lock(lock_file):
        state = _load_state(state_path)
        scores = state.get("initial_scores")
        if isinstance(scores, dict) and {"L", "R", "W", "S"}.issubset(scores.keys()):
            return False
        if state.get("mock_test_sent"):
            return False

    day_key = datetime.now().astimezone().strftime("%Y-%m-%d")
    for idx, body in enumerate(PLACEMENT_MESSAGES, start=1):
        key = f"ielts-mock-placement-{day_key}-part{idx}"
        _lark_send_text(chat_id, lark_cli, body, key)
        if idx < len(PLACEMENT_MESSAGES):
            time.sleep(2.0)

    with _state_lock(lock_file):
        state = _load_state(state_path)
        scores = state.get("initial_scores")
        if isinstance(scores, dict) and {"L", "R", "W", "S"}.issubset(scores.keys()):
            return True
        state["mock_test_sent"] = True
        _write_state(state_path, state)
    return True


def try_send_cli() -> int:
    """供 send-daily-modes.sh 调用：需发送模拟卷则发送并 exit 0；否则 exit 1（走日常提醒文案）。"""
    state_path = Path(sys.argv[2]) if len(sys.argv) > 2 else ROOT / "user_state.json"
    chat_id = os.environ.get("FEISHU_IELTS_CHAT_ID", "oc_99000aba52da6814c200481c4dedf1ea")
    lark_cli = os.environ.get("LARK_CLI") or shutil.which("lark-cli") or "/opt/homebrew/bin/lark-cli"
    if not Path(lark_cli).exists():
        print(f"lark-cli not found: {lark_cli}", file=sys.stderr)
        return 127
    sent = send_placement_test(state_path, chat_id, lark_cli)
    return 0 if sent else 1


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "try-send":
        raise SystemExit(try_send_cli())
    if len(sys.argv) >= 2 and sys.argv[1] == "send-placement-test":
        if len(sys.argv) < 3:
            print("usage: init_placement_test.py send-placement-test STATE_FILE", file=sys.stderr)
            raise SystemExit(2)
        state_path = Path(sys.argv[2])
        chat_id = os.environ.get("FEISHU_IELTS_CHAT_ID", "oc_99000aba52da6814c200481c4dedf1ea")
        lark_cli = os.environ.get("LARK_CLI") or shutil.which("lark-cli") or "/opt/homebrew/bin/lark-cli"
        raise SystemExit(0 if send_placement_test(state_path, chat_id, lark_cli) else 1)
    print("usage: init_placement_test.py try-send STATE_FILE", file=sys.stderr)
    raise SystemExit(2)
