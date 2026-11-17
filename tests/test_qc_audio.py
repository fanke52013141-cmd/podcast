"""质检模块测试（P3-1 骨架）: py tests/test_qc_audio.py
需要 faster-whisper + SAPI 中文样本（tests/fixtures/sapi_zh_sample.wav）；
依赖缺失时自动跳过，不阻塞 CI。
"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WAV = os.path.join(BASE, "tests", "fixtures", "sapi_zh_sample.wav")

PASS = FAIL = SKIP = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else:    FAIL += 1; print(f"  ❌ {name}  {detail}")

if not os.path.exists(WAV):
    print("SKIP: 缺少 SAPI 样本，先运行 scripts/sapi_check.ps1")
    sys.exit(0)
try:
    import faster_whisper  # noqa: F401
except ImportError:
    print("SKIP: faster-whisper 未安装")
    sys.exit(0)

from postprocess.qc_audio import qc_audio

zh = ("大家好，欢迎收听本期节目。今天咱们聊一个挺有意思的话题："
      "AI 会不会取代播客主持人。")
zh2 = "我觉得吧，短期内不会，但它会改变做播客的方式。"

good = {"text": [["S1", zh], ["S2", zh2]], "speakers": [{"id": "S1"}, {"id": "S2"}]}
bad = {"text": [["S1", zh], ["S2", "这是一段与音频完全不匹配的文本内容啊啊啊"]],
       "speakers": [{"id": "S1"}, {"id": "S2"}]}

print("== 质检：匹配文本应通过（tiny 模型按 0.35 校准） ==")
os.environ["QC_CER_THRESHOLD"] = "0.35"          # tiny 模型中文底噪较大，见 qc_audio 注释
import importlib
import postprocess.qc_audio as qa
importlib.reload(qa)
r1 = qa.qc_audio(WAV, good)
if r1["mode"] == "unavailable":
    print("SKIP: 模型不可用:", r1.get("note"))
    sys.exit(0)
check("mode=single-asr", r1["mode"] == "single-asr", r1.get("mode"))
check("匹配文本通过(tiny 校准)", r1["pass"] and not r1["bad_turns"], r1)
check("平均 CER 低于 tiny 底噪上限", r1["cer_avg"] < 0.35, r1["cer_avg"])

print("== 质检：不匹配文本应标记坏轮（默认严格阈值 0.05） ==")
os.environ.pop("QC_CER_THRESHOLD", None)
importlib.reload(qa)
r2 = qa.qc_audio(WAV, bad)
check("未通过", not r2["pass"], r2)
check("第 2 轮被标记", 1 in r2["bad_turns"], r2["bad_turns"])
check("坏轮 CER 显著高于匹配轮", 
      r2["cer_detail"][1]["cer"] > r2["cer_detail"][0]["cer"], r2["cer_detail"])

print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
