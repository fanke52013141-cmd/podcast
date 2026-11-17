"""编排节点实现：research -> draft -> rewrite -> validate -> synthesize -> postprocess。

LLM 调用走 OpenAI 兼容协议（DeepSeek 主 / GLM 备），langchain 懒加载；
langchain 未安装时 CLI 可用 --dry-run（内置样稿）跑通除 LLM 外的全链路。
"""
from __future__ import annotations
import os, json, time
from dataclasses import dataclass, field

from contracts.models import SoulXScript, Job, load_voice_manifest
from orchestrator.prompts import step_a_prompt, step_b_prompt, SYSTEM_EDITOR, STEP_B_SYSTEM
from orchestrator.validators import validate_script_text, parse_llm_script, build_script
from orchestrator.term_lexicon import TermLexicon


# ---------------- LLM 客户端（OpenAI 兼容，主备切换） ----------------

@dataclass
class LLMResult:
    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    provider: str = ""


def _make_client():
    """返回 (client, model, provider_name)；langchain 懒加载。"""
    try:
        from langchain_openai import ChatOpenAI
    except ImportError as e:
        raise RuntimeError("需要 langchain-openai：pip install -r requirements.txt "
                           "（或用 --dry-run 免依赖试跑）") from e

    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if deepseek_key:
        return (ChatOpenAI(model=os.getenv("DEEPSEEK_MODEL", "deepseek-chat"),
                           api_key=deepseek_key,
                           base_url=os.getenv("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
                           temperature=0.9), "deepseek")
    glm_key = os.getenv("GLM_API_KEY")
    if glm_key:
        return (ChatOpenAI(model=os.getenv("GLM_MODEL", "glm-4-plus"),
                           api_key=glm_key,
                           base_url=os.getenv("GLM_BASE_URL",
                                              "https://open.bigmodel.cn/api/paas/v4"),
                           temperature=0.9), "glm")
    raise RuntimeError("未配置 DEEPSEEK_API_KEY / GLM_API_KEY（.env 或环境变量）")


def call_llm(prompt: str, system: str, max_retry: int = 2) -> LLMResult:
    client, provider = _make_client()
    last_err = None
    for _ in range(max_retry + 1):
        try:
            resp = client.invoke([("system", system), ("user", prompt)])
            meta = getattr(resp, "response_metadata", {}) or {}
            usage = meta.get("token_usage", {}) or {}
            return LLMResult(resp.content, usage.get("prompt_tokens", 0),
                             usage.get("completion_tokens", 0), provider)
        except Exception as e:                     # 主通道失败 -> 备通道
            last_err = e
            os.environ["DEEPSEEK_API_KEY"] = ""     # 触发 _make_client 切到 GLM
            client, provider = _make_client()
    raise RuntimeError(f"LLM 调用失败（含备用通道）: {last_err}")


# ---------------- 状态对象 ----------------

@dataclass
class PipelineState:
    topic: str
    style: str
    minutes: int
    material: str = ""
    draft: str = ""
    turns: list = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    retry_count: int = 0
    job: Job | None = None
    lexicon: TermLexicon = field(default_factory=TermLexicon)
    voice_map: dict = field(default_factory=lambda: {"S1": "v01_female_warm",
                                                     "S2": "v02_male_deep"})
    tts_url: str = ""
    wav_path: str | None = None
    provider: str = "api"        # api | mock（无 key 联调用）


# ---------------- 各节点 ----------------

def research_node(s: PipelineState) -> PipelineState:
    """素材抽取。P1 支持直接把 topic 当素材；PDF/URL 在 P2 接 trafilatura/pymupdf。"""
    s.material = s.material or s.topic
    return s


def draft_node(s: PipelineState) -> PipelineState:
    """Step A：戏剧化改写（自然度最大杠杆，借鉴 NotebookLlama）"""
    if s.provider == "mock":
        from orchestrator.mock_llm import mock_draft
        s.draft = mock_draft(s.material or s.topic, s.minutes, s.style)
        return s
    r = call_llm(step_a_prompt(s.material, s.style, s.minutes), SYSTEM_EDITOR)
    s.draft = r.text
    if s.job:
        s.job.llm_tokens_in += r.tokens_in; s.job.llm_tokens_out += r.tokens_out
    return s


def rewrite_node(s: PipelineState) -> PipelineState:
    """Step B：口语化 + 富标签 + JSON 化（校验错误清单附回，模型自我修正）"""
    if s.provider == "mock":
        from orchestrator.mock_llm import mock_turns_json
        from orchestrator.validators import parse_llm_script
        s.turns = parse_llm_script(mock_turns_json(s.draft, s.minutes))
        s.turns = s.lexicon.apply_to_turns(s.turns)
        return s
    from orchestrator.validators import allowed_tags_from_env
    err_hint = ""
    if s.errors:
        err_hint = "\n\n上一版未通过校验，务必修复：\n- " + "\n- ".join(s.errors)
    prompt = step_b_prompt(s.draft, ["S1", "S2"],
                           "主持人小雅（温暖亲切，负责开场/收尾/控场）",
                           "搭档老张（低沉沉稳，负责案例/抬杠/补刀）",
                           allowed_tags=allowed_tags_from_env()) + err_hint
    r = call_llm(prompt, STEP_B_SYSTEM)
    s.turns = parse_llm_script(r.text, allowed_tags=allowed_tags_from_env())
    s.turns = s.lexicon.apply_to_turns(s.turns)      # 术语读音修正
    if s.job:
        s.job.llm_tokens_in += r.tokens_in; s.job.llm_tokens_out += r.tokens_out
    return s


def validate_node(s: PipelineState) -> PipelineState:
    s.errors = validate_script_text(s.turns, s.minutes, ["S1", "S2"])
    s.retry_count += 1
    return s


def needs_rewrite(s: PipelineState) -> bool:
    return bool(s.errors) and s.retry_count < 3


# ---------------- 全链路（简单顺序版；LangGraph 接线见 graph.py） ----------------

def run_script_chain(s: PipelineState) -> PipelineState:
    """research -> draft -> (rewrite <-> validate 循环)"""
    s = research_node(s)
    s = draft_node(s)
    s = rewrite_node(s)
    s = validate_node(s)
    while needs_rewrite(s):
        print(f"  [validate] 第{s.retry_count}次未过: {s.errors[:2]}... 回炉")
        s = rewrite_node(s)
        s = validate_node(s)
    if s.errors:
        raise ValueError(f"3 次重试后仍未通过校验: {s.errors}")
    return s


def synth_offline_stub(s: PipelineState) -> SoulXScript:
    """无 TTS 服务时的占位说明（真实合成走 tts_service 或 --tts-url）。"""
    raise RuntimeError("未配置 TTS_URL。请先在 5080 机器上启动 tts_service.server "
                       "后用 --tts-url 指向它（P0 验证通过后）。")
