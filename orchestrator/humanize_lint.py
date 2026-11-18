"""写稿体检器 —— 移植 kaikou tools/lint_script.py 的"人话"规则（MIT，只移植思路与规则表）。

目标：把"一听就是 AI"的特征变成可跑的检查，写稿后自动体检，
问题清单附回 LLM 自我修正（rewrite_node 的 err_hint 机制）。

规则与依据（kaikou docs/05_改文案.md）：
- 一口气（两个停顿标点之间）≤ 22 字
- AI 高频套话："有两个/只有一个"总结框架、"在…的今天"、"随着…的发展"、
  "日益/不断提升/至关重要/值得注意的是"
- "不是……而是……"句式
- 书面单字词：已/需/仅/均/亦/皆/较/此/将/其（建议换双字词）
- 句长变异系数 ≥ 0.3（长短穿插，太整齐像念口号）
- 语气词（吧呢嘛啊呀啦哈）每千字 3-5 个
- 破折号（MiniMax 会读成哔音）
"""
from __future__ import annotations
import re
import statistics

HAN = re.compile(r"[㐀-鿿]")
BREAK = "，。？！；：、,.?!;:"
MAX_BREATH = 22

AI_FRAME = [
    (r"(有|分为|就)(两|三|四)(个|点|条|种)", "总结框架「有两个…」"),
    (r"只有一个", "总结框架「只有一个」"),
    (r"在[^，。]{2,14}的今天", "套话「在…的今天」"),
    (r"随着[^，。]{2,14}的(快速)?发展", "套话「随着…的发展」"),
    (r"日益|不断提升|至关重要|值得注意的是", "套话「日益/至关重要/值得注意的是」"),
    (r"不是[^，。？！]{1,14}[，,]?(而是|是)", "「不是…而是…」句式"),
]

# 书面单字词 → 双字建议（替换用）。
BOOK1 = [
    (r"(?<!而)已(?!经)", "已经"), (r"(?<![必急所无])需(?![要求])", "需要"),
    (r"仅(?!仅)", "只"), (r"(?<!平)均(?![匀衡值])", "都"),
    (r"(?<![比计])较(?!量)", "比较"), (r"亦", "也"), (r"皆", "都"),
    (r"因此", "所以"), (r"此外", "另外"),
]

TICS_OK = 5.0        # 语气词每千字上限（超过拖沓）
CV_MIN = 0.30        # 句长变异系数下限（低于则太整齐）


def _breath_issues(text: str) -> list[str]:
    out = []
    for ph in re.split(f"[{BREAK}]", text):
        n = len(HAN.findall(ph)) + len(re.findall(r"[A-Za-z0-9]", ph))
        if n > MAX_BREATH:
            out.append(f"一口气 {n} 字：「{ph[:22]}…」")
    return out


def check_humanize(turns: list[tuple[str, str]]) -> dict:
    """返回 {"problems": [...], "stats": {...}, "score": 0-100}。
    problems 是给 LLM 自我修正的清单（rewrite_node 的 err_hint 直接可用）。"""
    problems: list[str] = []
    all_text = "".join(t for _, t in turns)
    n_han = max(1, len(HAN.findall(all_text)))

    # 1) AI 高频套话 / 句式
    frame_hits = 0
    for sid, txt in turns:
        for pat, why in AI_FRAME:
            for m in re.finditer(pat, txt):
                frame_hits += 1
                problems.append(f"（{sid}）{why}：「{m.group(0)[:20]}」，换口语说法")
    # 2) 一口气太长
    breath = 0
    for sid, txt in turns:
        for issue in _breath_issues(txt):
            breath += 1
            problems.append(f"（{sid}）一口气太长：{issue}——拆短句")
    # 3) 书面单字词
    book_hits = 0
    for pat, fix in BOOK1:
        for m in re.finditer(pat, all_text):
            book_hits += 1
    if book_hits >= 5:
        problems.append(f"书面单字词 {book_hits} 处（已/需/仅/均/亦/皆/因此/此外…），"
                        "换成双字口语（测试到/需要/只/都/所以/另外），留少数单字即可")
    # 4) 句长变异系数（长短穿插）
    sents = []
    for _, txt in turns:
        for sent in re.split(r"[。？！]", txt):
            if len(HAN.findall(sent)) >= 2:
                sents.append(len(HAN.findall(sent)))
    cv = None
    if len(sents) >= 5:
        cv = statistics.pstdev(sents) / max(1, statistics.mean(sents))
        if cv < CV_MIN:
            problems.append(f"句子长短太整齐（变异系数 {cv:.2f} < 0.3），穿插一两句短的")
    # 5) 语气词密度（每千字 3-5 个，句尾）
    parts = len(re.findall(r"[吧呢嘛啊呀啦哈](?=[，。？！；]|$)", all_text))
    tic_density = parts / n_han * 1000
    if tic_density < 3:
        problems.append(f"语气词偏少（每千字 {tic_density:.1f} 个，建议 3-5），"
                        "在问句和反应句句尾补（吧/呢/啊/哈）")
    elif tic_density > 6:
        problems.append(f"语气词偏多（每千字 {tic_density:.1f} 个，建议 3-5），删掉一些")
    # 6) 破折号（MiniMax 读成哔音）
    dash = all_text.count("—") + all_text.count("…")
    if dash:
        problems.append(f"破折号/省略号 {dash} 处会合成出哔音，换成逗号或句号")

    score = max(0, 100 - len(problems) * 8 - frame_hits * 2 - breath * 3)
    return {"problems": problems[:20], "score": score,
            "stats": {"chars": n_han, "frames": frame_hits, "long_breath": breath,
                      "book_words": book_hits, "cv": round(cv, 2) if cv else None,
                      "tic_per_k": round(tic_density, 1), "dash": dash}}
