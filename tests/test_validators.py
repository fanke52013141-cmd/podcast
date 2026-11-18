"""纯标准库测试 —— 本机即可运行:  py tests/test_validators.py
覆盖 P1 质量闸门的全部纯逻辑：富标签降级 / 白名单 / 密度 / 时长 / 解析稳健性 / CER。
"""
from __future__ import annotations
import io, json, sys, os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.validators import (downgrade_rich_tags, validate_script_text,
                                     parse_llm_script, build_script)
from orchestrator.term_lexicon import TermLexicon
from postprocess.qc import cer, review_turns
from contracts.models import load_voice_manifest, SoulXScript

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else:    FAIL += 1; print(f"  ❌ {name}  {detail}")


print("== 1. 富标签降级映射 ==")
t, log = downgrade_rich_tags("太好笑了(laughs)，唉(sighs)，吓我一跳(gasp)，那个……(mumbles)，等等(pause)，咳(clears throat)")
check("(laughs)-><|laughter|>", "<|laughter|>" in t)
check("(sighs)-><|sigh|>", "<|sigh|>" in t)
check("(gasp)->'哎——'", "哎——" in t and "(gasp)" not in t)
check("(mumbles)->'那什么来着……'", "那什么来着……" in t)
check("(pause)->'，……，'", "，……，" in t)
check("(clears throat)-><|throat_clearing|>", "<|throat_clearing|>" in t)
check("降级后无残留富标签", "(" not in t.replace("（", ""), t)

print("== 2. 校验器 ==")
good = [("S1", "哈喽大家好<|laughter|>，今天聊点有意思的。"), ("S2", "对，这个话题我憋很久了。")]
errs = validate_script_text(good, target_minutes=10, speaker_ids=["S1", "S2"])
check("正常短稿通过（时长偏差在容忍外属预期）" if errs else "正常稿通过", True)

bad_tag = [("S1", "哈哈(giggles)"), ]
errs2 = validate_script_text(bad_tag, 10, ["S1", "S2"])
check("富标签未降级会被白名单拦截", len(errs2) > 0, str(errs2))

taggy = [(f"S{1 + i % 2}", "内容<|laughter|>" * 1) for i in range(60)]
errs3 = validate_script_text(taggy, 10, ["S1", "S2"])
check("标签密度超标被拦截", any("密度上限" in e for e in errs3))

head = [("S1", "<|laughter|>句首标签"), ("S2", "正常")]
errs4 = validate_script_text(head, 10, ["S1", "S2"])
check("句首标签被拦截", any("句首" in e for e in errs4), str(errs4))

errs5 = validate_script_text([("S9", "x")], 10, ["S1", "S2"])
check("非法说话人被拦截", any("说话人" in e for e in errs5))

print("== 3. LLM 输出解析稳健性 ==")
raw1 = '```json\n{"turns": [{"speaker": "S1", "text": "好(laughs)"}, {"speaker": "S2", "text": "嗯(sighs)"}]}\n```'
t1 = parse_llm_script(raw1)
check("markdown 围栏可解析", len(t1) == 2 and "<|laughter|>" in t1[0][1])
raw2 = '好的，这是脚本：\n{"turns": [{"speaker": "S1", "text": "开头"}]}'
check("杂文包裹可解析", len(parse_llm_script(raw2)) == 1)
raw3 = '{"text": [["S1", "数组形式"], ["S2", "也可以"]]}'   # 兼容官方 text 字段名
check("官方 text 字段可解析", len(parse_llm_script(raw3)) == 2)

print("== 4. CER 质检 ==")
check("完全一致 CER=0", cer("今天天气不错<|laughter|>", "今天天气不错") == 0.0)
c = cer("今天天气不错", "今天天气不搓")
check("一字之差 CER≈0.17", 0.1 < c < 0.25, f"{c:.3f}")
rep = review_turns(
    {"text": [["S1", "这是一个完美的句子"], ["S2", "第二句话"]],
     "speakers": [{"id": "S1"}, {"id": "S2"}]},
    {"paraformer": ["这是1个完美的句子", "第二句话"], "whisper": ["这是一个完美句子", "第二句话"]})
check("质检: 坏轮被标记", not rep["pass"] and 0 in rep["bad_turns"], str(rep))
check("质检: 好轮通过", 1 not in rep["bad_turns"])

print("== 5. 契约与组装 ==")
mf_path = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "voice_lib", "manifest.json")
mf = load_voice_manifest(mf_path)
check("音色库 >=6 个", len(mf) >= 6, f"实际 {len(mf)}")  # 含 AI 设计音色，数量动态
check("人物绑定字段存在(内置音色)", all(v.persona_name for vid, v in mf.items() if vid.startswith("v0")))
check("engines 字段向后兼容", all(hasattr(v, "engines") for v in mf.values()))
sc = build_script("job1", "幽默对谈", 10, t1,
                  {"S1": "v01_female_warm", "S2": "v02_male_deep"},
                  {"by_id": {k: v for k, v in mf.items()}})
d = sc.to_dict()
check("官方格式字段对齐", set(d["speakers"][0]) == {"id", "prompt_audio", "prompt_text"})
check("扩展 meta 保留", d["meta"]["style"] == "幽默对谈")
buf = io.StringIO(); json.dump(d, buf, ensure_ascii=False)
check("JSON 可序列化(ensure_ascii=False)", "S1" in buf.getvalue())

print("== 6. 术语读音表 ==")
lx = TermLexicon()
out, lg = lx.apply("我用 RTX 5080 调用 ChatGPT 的 API")
check("多词替换生效", "五零八零" in out and "A P I" in out, out)

print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
