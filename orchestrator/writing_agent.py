"""写稿 Deep Agent —— 基于 langchain deepagents（create_deep_agent）。

架构：Agent + skills。Agent 拿到素材/主题后自主决定调用顺序，
工具（skill）封装本项目既有能力，模型在循环里"写→体检→改→存"直到合格。

skills（tools/ 参数）：
  - extract_material：素材抽取（URL/PDF/DOCX/TXT）
  - apply_lexicon：术语读音表修正
  - lint_humanize：写稿体检（kaikou 规则：AI 套话/一口气/语气词/破折号…）
  - validate_script：结构校验（说话人/字数/时长）
  - save_script：把最终稿写进虚拟文件系统 final_script.json

provider 映射：
  provider="agent"  → Deep Agent（本文件）
  provider="api"    → 旧两步 prompt 流水线（保留）
  provider="mock"   → Mock
"""
from __future__ import annotations
import json
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


# ---------- Agent 工具（skill 集合） ----------

def _tool_extract_material(source: str) -> str:
    """从 URL/PDF/DOCX/TXT 抽取正文素材。source 是链接或本地路径；纯文本直接返回。"""
    from orchestrator.extract import extract
    text, kind = extract(source)
    return f"[来源类型: {kind}] {text[:12000]}"


def _tool_lint_humanize(script_json: str) -> str:
    """写稿体检（kaikou 规则）：AI 套话/一口气/书面单字/句长变异/语气词密度/
    破折号哔音。输入最终脚本 JSON（{"turns": [["S1","…"], …]}），返回问题清单与评分。
    体检不合格就改稿再体检，直到 score≥80 且 problems 为空。"""
    from orchestrator.humanize_lint import check_humanize
    data = json.loads(script_json)
    turns = [(t["speaker"], t["text"]) if isinstance(t, dict) else tuple(t)
             for t in data.get("turns", [])]
    import json as _json
    return _json.dumps(check_humanize(turns), ensure_ascii=False)


def _tool_validate_script(script_json: str, minutes: float = 3) -> str:
    """结构校验：说话人只能是 S1/S2、每轮 1-4 句、总字数匹配目标时长。
    输入脚本 JSON，返回 "OK" 或错误清单（错误必须修复）。"""
    from orchestrator.validators import validate_script_text
    data = json.loads(script_json)
    turns = [(t["speaker"], t["text"]) if isinstance(t, dict) else tuple(t)
             for t in data.get("turns", [])]
    errs = validate_script_text(turns, int(minutes), ["S1", "S2"])
    return "OK" if not errs else "；".join(errs[:8])


def _tool_apply_lexicon(script_json: str) -> str:
    """术语读音表修正：对 turns 应用项目术语表（专有名词读音），返回修正后的 JSON。"""
    from orchestrator.term_lexicon import TermLexicon
    data = json.loads(script_json)
    turns = [(t["speaker"], t["text"]) if isinstance(t, dict) else tuple(t)
             for t in data.get("turns", [])]
    fixed = TermLexicon().apply_to_turns(turns)
    return json.dumps({"turns": [[sid, t] for sid, t in fixed]}, ensure_ascii=False)


def _tool_read_style_rules() -> str:
    """读取写稿规矩全文：口语规则、结构规矩、双字词/语气词/套话清单（kaikou 规矩）。"""
    rules = Path(BASE_DIR / "docs" / "writing_rules.md")
    if rules.exists():
        return rules.read_text(encoding="utf-8")
    return "（规矩文件缺失，按系统提示词里的规矩写）"


# ---------- Agent 组装 ----------

WRITING_RULES_MD = """# 播客写稿规矩（kaikou 规则 + 项目约定）

## 人话标准
念出来不拗口、听一遍就懂、像一个具体的人在说话。

## 句子规矩（体检器会逐条检查）
1. 双字词占七八成：已→已经、需→需要、仅→只、均→都、因此→所以、此外→另外；故意留两三个单字
2. 一口气（两个停顿标点之间）≤22 字；长句短句穿插（句长变异系数≥0.3）
3. 语气词（吧/呢/啊/哈/呀）每千字 3-5 个，只放问句和反应句句尾；"呃""嗯"不写
4. 禁止 AI 高频写法：「有两个/只有一个」总结框架、「在…的今天」「随着…的发展」、
   「日益」「至关重要」「值得注意的是」、「不是…而是…」句式
5. 成语俗语一段最多一处；一段最多一个好记的数字
6. 句尾标点一律句号或问号；禁止破折号（——）和省略号（……）（合成会出哔音）
7. 句子说完整：主语、量词、连接词不省

## 整篇结构
- 开头 30 秒钩子，然后 30 秒"给地图"：讲什么、分几步、听完得到什么
- 先总后分；每个要点用一个观众心里的问题开头再回答
- 每段结尾留钩子；埋一两个悬念后揭晓
- 每个要点配一个具体例子

## 工作流
1. 有素材/链接先用 extract_material 抽正文
2. 写对话蓝本（先戏剧化：观点碰撞/抢话/长短轮次不均——参考 NotebookLlama 两步法）
3. 把蓝本口语化成 JSON（turns 数组，speaker 只能 S1/S2）
4. lint_humanize 自查 → 有问题就改 → 再 lint（目标 score≥80 且 problems 空）
5. validate_script 结构校验通过后，save_script 存盘
"""


def build_writing_agent(model=None, minutes: int = 3):
    """组装写稿 Deep Agent。model 传 langchain ChatOpenAI 实例（账号池供给）。"""
    from deepagents import create_deep_agent

    tools = [_tool_extract_material, _tool_lint_humanize,
             _tool_validate_script, _tool_apply_lexicon,
             _tool_read_style_rules]

    words = int(minutes * 60 * 4.2)
    system = f"""你是「AI 播客」的写稿主编 Agent，负责产出双人播客脚本（说话人 S1 主持人 / S2 搭档）。

任务：根据用户给的主题/素材，写出 {minutes} 分钟的双人对谈脚本，并通过全部检查后存盘。

## 工作流（用 write_todos 规划后逐步执行）
1. 素材是链接/文件路径时先调 extract_material；纯主题就围绕主题组织内容
2. 先写"对话蓝本"（戏剧化：观点碰撞、抢话、长短轮次不均），再口语化成最终 JSON
3. 调 lint_humanize 自查（kaikou 人话规则），有问题改稿重查，直到 score≥80 且 problems 为空
4. 调 validate_script 校验结构（说话人 S1/S2、时长匹配），报错就修
5. 有术语调 apply_lexicon
6. 全部通过后，把最终 JSON 用 write_file 写到 final_script.json（格式：{{"turns": [["S1","…"],["S2","…"]]}}）

## 脚本 JSON 格式
{{"turns": [["S1", "第一句"], ["S2", "第二句"], …]}}
- speaker 只能 S1/S2；S1 开场收尾，S2 负责抬杠/补刀/案例
- 每轮 1-4 句；总字数约 {words} 字

## 写稿规矩
先用 read_style_rules 读全文规矩再动笔；核心：双字词七八成、一口气≤22字、
语气词每千字3-5个、禁 AI 套话、破折号省略号禁止、每段结尾留钩子。

不要问用户问题，自主完成。最终交付物只有 final_script.json。"""

    agent = create_deep_agent(model=model, tools=tools, system_prompt=system)
    return agent


def run_writing_agent(topic: str, minutes: int = 3, material: str = "",
                      style: str = "幽默对谈") -> dict:
    """执行写稿 Agent，返回 {"turns", "tokens_in", "tokens_out", "lint"}。"""
    from langchain_openai import ChatOpenAI

    # 账号池 → 激活账号
    from orchestrator.nodes import _accounts
    pool = _accounts()
    if not pool:
        raise RuntimeError("未配置任何 LLM 账号（设置页 → 账号池）")
    a = pool[0]
    model = ChatOpenAI(model=a.get("model", "deepseek-chat"),
                       api_key=a["api_key"],
                       base_url=a.get("base_url", "https://api.deepseek.com"),
                       temperature=0.9, max_tokens=8192)

    agent = build_writing_agent(model=model, minutes=minutes)

    user_task = (f"写一期 {minutes} 分钟的播客，风格：{style}。\n"
                 f"主题：{topic}\n"
                 + (f"\n素材（正文）：\n{material[:6000]}" if material else ""))
    result = agent.invoke(
        {"messages": [{"role": "user", "content": user_task}]},
        config={"recursion_limit": 80})

    # 从虚拟文件系统取 final_script.json
    msgs = result.get("messages", [])
    turns, tokens_in, tokens_out = [], 0, 0
    files = (result.get("files") or {})
    data = None
    if "final_script.json" in files:
        raw = files["final_script.json"]
        raw = raw if isinstance(raw, str) else str(raw)
        data = json.loads(raw)
    if data is None:                                     # 兜底：从最后一条 AI 消息抠 JSON
        for m in reversed(msgs):
            content = m.content if isinstance(m.content, str) else str(m.content)
            if '"turns"' in content:
                import re
                mm = re.search(r'\{[^{}]*"turns".*', content, re.S)
                if mm:
                    data = json.loads(mm.group(0))
                    break
    if data and data.get("turns"):
        turns = [(t[0] if isinstance(t, list) else t.get("speaker"),
                  t[1] if isinstance(t, list) else t.get("text"))
                 for t in data["turns"]]
    for m in msgs:
        meta = getattr(m, "response_metadata", {}) or {}
        u = meta.get("token_usage", {}) or {}
        tokens_in += u.get("prompt_tokens", 0)
        tokens_out += u.get("completion_tokens", 0)

    from orchestrator.humanize_lint import check_humanize
    lint = check_humanize(turns) if turns else {"problems": ["Agent 未产出脚本"],
                                                "score": 0, "stats": {}}
    return {"turns": turns, "tokens_in": tokens_in, "tokens_out": tokens_out,
            "lint": lint}
