"""Mock 全链路测试（无需 API key / GPU）: py tests/test_pipeline_mock.py

验证: mock 写稿 → 校验通过 → 组装 SoulXScript → 保存契约文件
（等价于 Web 后台任务在 provider=mock 时的完整路径）
"""
from __future__ import annotations
import os, sys, tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.nodes import (PipelineState, research_node, draft_node,
                                rewrite_node, validate_node)
from orchestrator.validators import build_script
from orchestrator.extract import extract
from contracts.models import load_voice_manifest

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else:    FAIL += 1; print(f"  ❌ {name}  {detail}")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
mf = load_voice_manifest(os.path.join(BASE, "voice_lib", "manifest.json"))

print("== 1. 素材抽取 ==")
text, st = extract("这是一个关于「AI 编程助手」的原始素材，内容直接可用。")
check("纯文本直通", st == "raw" and "AI 编程助手" in text)
from orchestrator.extract import detect_source
check("URL 路由识别(不联网)", detect_source("https://example.com/post") == "url")
check("raw 路由", detect_source("随便一段话") == "raw")

print("== 2. Mock 写稿全链路 (10 分钟稿) ==")
st = PipelineState(topic="AI 编程助手会取代程序员吗", style="幽默对谈",
                   minutes=10, provider="mock")
st = research_node(st); st = draft_node(st)
check("draft 产出蓝本", len(st.draft) > 200, f"{len(st.draft)} 字符")
st = rewrite_node(st); st = validate_node(st)
check("mock 稿一次通过校验", not st.errors, str(st.errors[:2]))
check("轮次交替合法", {t[0] for t in st.turns} == {"S1", "S2"})
n_tags = sum("<|" in t for _, t in st.turns)
check("标签密度合规(≤6/5min)", n_tags <= 6, f"{n_tags} 个")
est_min = sum(len(t) for _, t in st.turns) / 4.5 / 60
check("时长偏差 ±20% 内", 8 <= est_min <= 12, f"{est_min:.1f}min")

print("== 3. 契约组装与落盘 ==")
script = build_script(st.job_id if st.job else "test-job", st.style, 10,
                      st.turns, {"S1": "v01_female_warm", "S2": "v02_male_deep"},
                      {"by_id": {k: v for k, v in mf.items()}})
with tempfile.TemporaryDirectory() as td:
    p = os.path.join(td, "soulx_script.json")
    script.save(p)
    import json
    d = json.load(open(p, encoding="utf-8"))
    check("官方 schema 字段齐", all(k in d["speakers"][0] for k in ("id", "prompt_audio", "prompt_text")))
    check("音色路径解析到库", d["speakers"][0]["prompt_audio"].startswith("voice_lib/"))

print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
