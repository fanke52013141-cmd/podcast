"""Mock LLM —— 无 API key 时的全链路开发替身。

用途：UI/管线联调。按目标字数生成结构合规的对话稿（含语气词、打断、
少量副语言标签），保证能通过 validators 的全部校验（白名单/密度/时长）。
真实效果仍以 DeepSeek 为准——本模块只为打通工程链路。
"""
from __future__ import annotations
import json, random, re

random.seed(1988)  # 复现性

_S1_OPEN = [
    "哈喽，欢迎回到本期节目，今天咱们聊「{t}」这件事",
    "哎，来了来了，今天这个话题我憋了很久了——「{t}」",
    "开场先抛个问题：关于「{t}」，你站哪边？",
]
_S1_MID = [
    "我先说个数据啊，{t}这个事最近的热度是实打实的",
    "对，这里有个细节特别有意思，你别急，听我讲完",
    "哎等等——打断一下，这个前提我得先质疑一下",
    "嗯……你这么一说，我想起一个身边的例子",
    "所以你看，事情没这么简单，它其实是分层的",
    "对吧，这个逻辑一捋顺就通了，关键在于执行层",
    "咱把话说透：短期看热闹，长期看门道",
]
_S2_MID = [
    "哎对对对，就是这么回事，我补一个反面案例",
    "等会儿，我先说一句——这个结论下早了",
    "嗯……(sighs) 你这么一说我倒是有点动摇了",
    "我讲个具体的：就拿我上个月碰到的事来说",
    "哈哈，你这个角度刁钻，但还真有道理(laughs)",
    "打断一下啊，数据归数据，体感是另一回事",
    "所以我的看法比较直接：先干起来，再迭代",
]
_S1_CLOSE = [
    "好，那今天咱们就聊到这，一句话总结：「{t}」值得持续关注",
    "收个尾：关于「{t}」，别急着下结论，让子弹再飞一会儿",
]
_S2_CLOSE = [
    "对，下期咱们接着聊它的后续，记得订阅",
    "行，那今天就到这，我是老张，下期见",
]

TAG_LIMIT = "TAGS"


def mock_draft(material: str, minutes: int, style: str) -> str:
    """Step A 产物：对话蓝本（[主持]/[搭档] 行式）。按字数预算累积到目标长度。"""
    topic = _topic_of(material)
    target = minutes * 270                      # 4.5字/秒 × 60秒，正中校验区间
    lines = [f"[主持] {_pick(_S1_OPEN).format(t=topic)}"]
    n = 0
    while n < target:
        l1 = f"[主持] {_pick(_S1_MID).format(t=topic)}"
        l2 = f"[搭档] {_pick(_S2_MID)}"
        lines += [l1, l2]
        n += len(l1) + len(l2)
    lines.append(f"[主持] {_pick(_S1_CLOSE).format(t=topic)}")
    lines.append(f"[搭档] {_pick(_S2_CLOSE)}")
    return "\n".join(lines)


def mock_turns_json(draft: str, minutes: int) -> str:
    """Step B 产物：严格 schema 的 turns JSON（含少量合法副语言标签）。"""
    turns = []
    for line in draft.splitlines():
        m = re.match(r"\[(主持|搭档)\]\s*(.+)", line.strip())
        if not m:
            continue
        sid = "S1" if m.group(1) == "主持" else "S2"
        txt = m.group(2)
        turns.append({"speaker": sid, "text": txt})
    # 标签密度控制（宁少勿多）：总量 ≤ min(4, 密度上限)，且不在句首
    max_tags = min(4, max(2, int(minutes / 5 * 6)))
    placed = sum("<|" in t["text"] for t in turns)
    for t in turns:
        if placed >= max_tags:
            t["text"] = t["text"].replace("(sighs)", "").replace("(laughs)", "")
        elif "(sighs)" in t["text"]:
            t["text"] = t["text"].replace("(sighs)", "<|sigh|>"); placed += 1
        elif "(laughs)" in t["text"]:
            t["text"] = t["text"].replace("(laughs)", "<|laughter|>"); placed += 1
    return json.dumps({"turns": turns}, ensure_ascii=False)


def mock_llm(prompt: str, system: str) -> str:
    """按 prompt 特征路由到 Step A / Step B 的 mock 实现。"""
    if "最终播客脚本 JSON" in prompt or "schema" in prompt:
        # 从蓝本反推时长（草稿行数/2 ≈ 分钟）
        minutes = max(3, min(30, len([l for l in prompt.splitlines()
                                      if l.startswith(("[主持]", "[搭档]"))]) // 2 + 3))
        # 蓝本在 prompt 尾部，直接复用
        draft = _extract_draft(prompt)
        return mock_turns_json(draft, minutes)
    return prompt.split("---", 2)[-1].strip() or mock_draft("测试主题", 10, "幽默对谈")


def mock_draft_from(material: str, minutes: int, style: str) -> str:
    return mock_draft(material, minutes, style)


def _extract_draft(prompt: str) -> str:
    if "---" in prompt:
        parts = prompt.split("---")
        if len(parts) >= 3:
            return parts[-2].strip()
    return mock_draft("测试主题", 10, "幽默对谈")


def _topic_of(material: str) -> str:
    m = re.search(r"「(.+?)」", material)
    if m:
        return m.group(1)
    s = material.strip().splitlines()[0] if material.strip() else "这个话题"
    return s[:24]


def _pick(lst):
    return random.choice(lst)
