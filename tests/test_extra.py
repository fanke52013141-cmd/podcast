"""新增模块单测（P2-2 / P1-14 / UI-6 边界逻辑）: py tests/test_extra.py
纯标准库 + 本地夹具，不依赖网络/GPU/模型。
"""
from __future__ import annotations
import os, sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orchestrator.extract import rss_items
from postprocess.subtitles import _snap_boundaries, _srt_from_bounds
from postprocess.edit import _turn_bounds

PASS = FAIL = 0
def check(name, cond, detail=""):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else:    FAIL += 1; print(f"  ❌ {name}  {detail}")

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

print("== 1. RSS 解析（本地夹具） ==")
feed = rss_items(os.path.join(BASE, "tests", "fixtures", "sample_feed.xml"), limit=10)
check("feed 标题", feed["feed_title"] == "测试播客源 · 科技周报", feed.get("feed_title"))
check("3 条 item", len(feed["items"]) == 3, len(feed["items"]))
check("字段完整", all(feed["items"][0].get(k) for k in ("title", "link", "summary")))

print("== 2. 字幕边界吸附（P2-2） ==")
script = {"text": [["S1", "这是一段较长的第一轮内容" * 5],
                   ["S2", "这是第二轮" * 3]],
          "speakers": [{"id": "S1"}, {"id": "S2"}]}
words = [(0.0, 3.0), (3.2, 6.0), (6.5, 9.0),       # 9.2-11.8 长停顿
         (11.8, 14.0), (14.2, 16.0)]
bounds = _snap_boundaries(script, 16.0, words)
gap_centers = [(3.0 + 3.2) / 2, (6.0 + 6.5) / 2, (9.0 + 11.8) / 2, (14.0 + 14.2) / 2]
check("边界吸附到真实词间隙", any(abs(bounds[1] - g) < 0.05 for g in gap_centers), bounds)
check("边界单调", bounds[0] < bounds[1] < bounds[2], bounds)
srt = _srt_from_bounds(script, bounds)
check("SRT 含说话人标签", "[S1]" in srt and "[S2]" in srt)

print("== 3. 剪辑轮次边界（UI-6） ==")
b = _turn_bounds(script, 30.0)
check("起止为 0/total", b[0] == 0.0 and abs(b[-1] - 30.0) < 1e-6, b)
check("单调递增", all(b[i] < b[i + 1] for i in range(len(b) - 1)), b)
check("按字数比例(第一轮更长)", b[1] > 15.0, b)

print("== 4. 音效锚点解析（P2-6） ==")
from postprocess.sfx import resolve_anchors
anchors = [{"turn": 0, "ratio": 0.5, "name": "whoosh", "gain_db": -6},
           {"turn": 1, "ratio": 0.0, "name": "ding", "gain_db": -3}]
res = resolve_anchors(script, 30.0, anchors)
check("锚点数量", len(res) == 2, len(res))
check("turn0 ratio0.5 在首轮中点", abs(res[0]["abs_time"] - b[1] * 0.5) < 0.01, res[0])
check("turn1 ratio0 在第二轮起点", abs(res[1]["abs_time"] - b[1]) < 0.01, res[1])
res2 = resolve_anchors(script, 60.0, anchors)
check("时长翻倍→锚点按比例漂移(重对位)", abs(res2[0]["abs_time"] - res[0]["abs_time"] * 2) < 0.01,
      (res[0]["abs_time"], res2[0]["abs_time"]))

print("== 5. VoiceStar 接口（P2-7，未配置时应给出可操作错误） ==")
import os as _os
_os.environ.pop("VOICESTAR_URL", None)
_os.environ.pop("VOICESTAR_CMD", None)
from tts_service.fixed_segment import synthesize_fixed
try:
    synthesize_fixed("测试", 15)
    check("未配置时抛错", False)
except RuntimeError as e:
    check("未配置时抛 RuntimeError", "VoiceStar 未配置" in str(e), str(e)[:60])

print("== 6. 段缓存键（P1-12） ==")
from tts_service.soulx_wrapper import segment_key
payload = {"text": [["S1", "你好世界"], ["S2", "再见"]],
           "speakers": [{"id": "S1", "prompt_audio": "", "prompt_text": ""},
                        {"id": "S2", "prompt_audio": "", "prompt_text": ""}]}
k1 = segment_key(payload, 7)
k2 = segment_key(payload, 7)
k3 = segment_key({"text": [["S1", "你好世界"]],
                  "speakers": payload["speakers"]}, 7)
k4 = segment_key(payload, 8)
check("同内容同 seed → 同键", k1 == k2)
check("文本不同 → 键不同", k1 != k3)
check("seed 不同 → 键不同", k1 != k4)

print("== 7. 副语言白名单即时生效（设置页接线） ==")
from orchestrator.validators import (validate_script_text, parse_llm_script,
                                     allowed_tags_from_env)
_os.environ["PARALING_TAGS"] = "laughter"
check("env 白名单读取", allowed_tags_from_env() == ("laughter",), allowed_tags_from_env())
errs = validate_script_text([("S1", "哈哈<|laughter|>"), ("S2", "唉<|sigh|>")],
                            target_minutes=1, speaker_ids=["S1", "S2"])
check("白名单外 sigh 被拦截", any("sigh" in e for e in errs), errs)
turns = parse_llm_script('{"turns":[{"speaker":"S1","text":"好(laughs)唉(sighs)"}]}')
check("解析时剥离白名单外标签", "<|laughter|>" in turns[0][1] and "<|sigh|>" not in turns[0][1], turns)
_os.environ.pop("PARALING_TAGS", None)

print("== 8. 音色库 CRUD（P3-4） ==")
import json as _json, tempfile as _tf, shutil as _sh
from webapp import voice_store as vs
_tmpdir = _tf.mkdtemp()
try:
    mp = os.path.join(_tmpdir, "manifest.json")
    _sh.copyfile(os.path.join(BASE, "voice_lib", "manifest.json"), mp)
    newv = vs.create({"name": "测试音色", "gender": "male", "style_tags": ["测试"]}, path=mp)
    check("新增音色", newv["id"].startswith("v09_") or newv["id"].startswith("v0"), newv["id"])
    upd = vs.update(newv["id"], {"name": "改名后", "license_status": "public"}, path=mp)
    check("更新音色", upd["name"] == "改名后" and upd["license_status"] == "public")
    vs.delete(newv["id"], path=mp)
    check("删除音色", vs.get(newv["id"], path=mp) is None)
finally:
    _sh.rmtree(_tmpdir, ignore_errors=True)

print("== 9. 节目模板（P3-7） ==")
from webapp import templates_store as ts
_tmp2 = _tf.mkdtemp()
try:
    tp = os.path.join(_tmp2, "templates.json")
    seeds = ts.list_templates(tp)
    check("种子模板 3 条", len(seeds) == 3, len(seeds))
    t1 = ts.create_template({"name": "测试模板", "style": "访谈", "minutes": 15}, p=tp)
    check("新建模板", any(t["id"] == t1["id"] for t in ts.list_templates(tp)))
    ts.delete_template(t1["id"], p=tp)
    check("删除模板", not any(t["id"] == t1["id"] for t in ts.list_templates(tp)))
finally:
    _sh.rmtree(_tmp2, ignore_errors=True)

print("== 10. 夜间队列调度（P3-5） ==")
from webapp import schedules_store as ss
_tmp3 = _tf.mkdtemp()
try:
    sp = os.path.join(_tmp3, "schedules.json")
    s1 = ss.create_schedule({"time": "02:00", "topics": ["A", "B"]}, p=sp)
    check("新建 schedule", s1["topics"] == ["A", "B"])
    check("到点命中", len(ss.due_schedules(now="02:00", p=sp)) == 1)
    check("非到点不命中", len(ss.due_schedules(now="03:00", p=sp)) == 0)
    ss.mark_run(s1["id"], p=sp)
    check("当天已跑不重复触发", len(ss.due_schedules(now="02:00", p=sp)) == 0)
    try:
        ss.create_schedule({"time": "2:00", "topics": ["x"]}, p=sp)
        check("非法时间被拒", False)
    except ValueError:
        check("非法时间被拒", True)
finally:
    _sh.rmtree(_tmp3, ignore_errors=True)

print("== 11. 时间轴一致性：SRT → 边界反解（precise 复用） ==")
from postprocess.subtitles import bounds_from_srt
import tempfile as _tf2
_tmp4 = _tf2.mkdtemp()
try:
    srt_path = os.path.join(_tmp4, "t.srt")
    orig = [0.0, 3.5, 20.0]                      # 2 轮 → 3 个边界
    srt_text = _srt_from_bounds(script, orig)
    open(srt_path, "w", encoding="utf-8").write(srt_text)
    parsed = bounds_from_srt(srt_path)
    check("往返边界一致", parsed is not None and len(parsed) == len(orig)
          and all(abs(a - b) < 0.002 for a, b in zip(parsed, orig)), parsed)
finally:
    _sh.rmtree(_tmp4, ignore_errors=True)

print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
sys.exit(1 if FAIL else 0)
