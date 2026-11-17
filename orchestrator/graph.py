"""LangGraph 接线（生产版）+ 简单顺序执行（CI/无依赖版）。

图结构（方案 v1.0 §3.1）：
  research -> draft -> rewrite -> validate --(通过)--> synthesize -> postprocess -> qc -> END
                                  ^  |
                                  +--(不通过, retry<3)
"""
from __future__ import annotations


def build_graph():
    """LangGraph 版。需要 langgraph + langchain-openai 已安装。

    synthesize 节点已接通 TTS 服务（P1-9）；postprocess/qc 在 P2/P3 接入。
    """
    import os
    from langgraph.graph import StateGraph, END
    from typing_extensions import TypedDict

    from orchestrator.nodes import (research_node, draft_node, rewrite_node,
                                    validate_node, needs_rewrite, PipelineState)
    from orchestrator.validators import build_script
    from orchestrator.tts_client import TTSClient, print_progress
    from contracts.models import load_voice_manifest

    _MANIFEST = None
    def _manifest():
        nonlocal _MANIFEST
        if _MANIFEST is None:
            path = os.path.join(os.path.dirname(os.path.dirname(
                os.path.abspath(__file__))), "voice_lib", "manifest.json")
            _MANIFEST = load_voice_manifest(path)
        return _MANIFEST

    class GState(TypedDict, total=False):
        topic: str; style: str; minutes: int; material: str
        draft: str; turns: list; errors: list; retry_count: int
        voice_map: dict; tts_url: str; wav_path: str

    def _n(fn):
        def wrap(st: GState) -> dict:
            s = _to_state(st)
            s = fn(s)
            return _to_dict(s)
        return wrap

    def synthesize_node(st: GState) -> dict:
        s = _to_state(st)
        script = build_script(s.topic.replace(" ", "_")[:20], s.style, s.minutes,
                              s.turns, s.voice_map,
                              {"by_id": dict(_manifest())})
        client = TTSClient(s.tts_url or os.getenv("TTS_URL", "http://127.0.0.1:8600"))
        meta = client.synthesize(script.to_dict(), on_progress=print_progress)
        s.wav_path = meta.get("wav_path")
        return _to_dict(s)

    def _route(st: GState) -> str:
        return "rewrite" if needs_rewrite(_to_state(st)) else "synthesize"

    g = StateGraph(GState)
    g.add_node("research", _n(research_node))
    g.add_node("draft", _n(draft_node))
    g.add_node("rewrite", _n(rewrite_node))
    g.add_node("validate", _n(validate_node))
    g.add_node("synthesize", synthesize_node)
    g.add_node("postprocess", _placeholder_node)     # P2: 字幕+混音+水印
    g.add_node("qc", _placeholder_node)              # P3: 双ASR质检+句级重做
    g.set_entry_point("research")
    g.add_edge("research", "draft"); g.add_edge("draft", "rewrite")
    g.add_edge("rewrite", "validate")
    g.add_conditional_edges("validate", _route)
    g.add_edge("synthesize", "postprocess"); g.add_edge("postprocess", "qc")
    g.add_edge("qc", END)
    return g.compile()


def _placeholder_node(st):
    """非生产路径占位：synthesize/postprocess/qc 的完整编排在 webapp/jobs.py
    （生产入口）；LangGraph 路径仅用于脚本流水线实验，此处透传不中断。"""
    return st


def _to_state(d: dict):
    from orchestrator.nodes import PipelineState
    keys = ("topic", "style", "minutes", "material", "draft",
            "turns", "errors", "retry_count")
    s = PipelineState(**{k: d[k] for k in keys if k in d})
    for extra in ("voice_map", "tts_url", "wav_path"):
        if extra in d and d[extra] is not None:
            setattr(s, extra, d[extra])
    return s


def _to_dict(s) -> dict:
    out = {k: getattr(s, k) for k in
           ("topic", "style", "minutes", "material", "draft",
            "turns", "errors", "retry_count")}
    for extra in ("voice_map", "tts_url", "wav_path"):
        v = getattr(s, extra)
        if v is not None:
            out[extra] = v
    return out
