"""10 遍全流程批量测试：引擎×风格矩阵，每遍产出 1 个音频 + 记录问题。

第 1 遍已由浏览器真实点击完成（Mock+SoulX）。
本脚本执行第 2-10 遍（9 个音频），每遍：
  建任务 → 轮询状态 → 记录 {遍数, 写稿, 合成引擎, 风格, 轮数, 音频秒数, 问题}

组合矩阵（写稿 / 合成 / 风格）：
  2. mock    / minimax  / 漫才
  3. mock    / soulx    / 访谈
  4. api     / minimax  / 辩论
  5. agent   / minimax  / 热点解读
  6. mock    / soulx    / 实战课堂
  7. api     / minimax  / 故事剧场
  8. mock    / minimax  / 纪实探索
  9. agent   / soulx    / 幽默对谈
 10. mock    / minimax  / 深夜电台
"""
import json, time, urllib.request, sys, traceback

BASE = "http://127.0.0.1:8765"
TTS = "http://127.0.0.1:8600"

def api(path, method="GET", body=None, timeout=60):
    req = urllib.request.Request(BASE + path,
        data=json.dumps(body).encode() if body else None,
        headers={"Content-Type": "application/json"}, method=method)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=timeout).read()), None
    except urllib.error.HTTPError as e:
        return None, f"HTTP {e.code}: {e.read().decode('utf-8','replace')[:150]}"
    except Exception as e:
        return None, str(e)[:150]

RUNS = [
    (2,  "mock",  "minimax", "漫才",     1),
    (3,  "mock",  "soulx",   "访谈",     1),
    (4,  "api",   "minimax", "辩论",     1),
    (5,  "agent", "minimax", "热点解读", 1),
    (6,  "mock",  "soulx",   "实战课堂", 1),
    (7,  "api",   "minimax", "故事剧场", 1),
    (8,  "mock",  "minimax", "纪实探索", 1),
    (9,  "agent", "soulx",   "幽默对谈", 1),
    (10, "mock",  "minimax", "深夜电台", 1),
]

def run_one(n, provider, engine, style, minutes):
    rec = {"n": n, "provider": provider, "engine": engine, "style": style,
           "issues": [], "audio_sec": None, "turns": None}
    body = {"topic": f"第{n}遍流程测试：{style}风格的 token 话题", "minutes": minutes,
            "provider": provider, "tts_engine": engine,
            "tts_url": TTS, "style": style}
    d, err = api("/api/jobs", "POST", body, timeout=30)
    if err:
        rec["issues"].append(f"建任务失败: {err}")
        return rec
    jid = d["job_id"]; rec["job_id"] = jid
    # 轮询（写稿+合成最长 25 分钟）
    for i in range(150):
        time.sleep(10)
        d, e2 = api(f"/api/jobs/{jid}", timeout=15)
        if e2: rec["issues"].append(f"查询失败 {e2}"); return rec
        st = d.get("status")
        if st == "done":
            rec["audio_sec"] = d.get("tts_seconds")
            rec["turns"] = len((d.get("script") or {}).get("text", []))
            rec["artifacts"] = [k for k, v in (d.get("paths") or {}).items() if v]
            break
        if st == "failed":
            rec["issues"].append("任务 failed: " + (d.get("error") or "")[:150])
            # 有脚本但没有音频 → 直接补合成（模拟用户点重跑）
            if (d.get("paths") or {}).get("script"):
                rec["issues"].append("尝试补合成…")
            return rec
        if i in (5, 15, 30): print(f"  [{n}] {st}/{d.get('step')} synth={d.get('synth','-')}", flush=True)
    else:
        rec["issues"].append("轮询超时（25 分钟）")
    return rec

def main():
    results = []
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    for n, provider, engine, style, minutes in RUNS:
        if only and str(n) not in only:
            continue
        print(f"—— 第 {n} 遍：{provider}+{engine}+{style} ——", flush=True)
        try:
            rec = run_one(n, provider, engine, style, minutes)
        except Exception as e:
            rec = {"n": n, "issues": [f"异常: {traceback.format_exc()[-200:]}"]}
        results.append(rec)
        print(f"  => {json.dumps(rec, ensure_ascii=False)[:200]}", flush=True)
    json.dump(results, open("tests/matrix_results.json", "w", encoding="utf-8"),
              ensure_ascii=False, indent=2)
    print("\n=== 汇总 ===")
    okc = sum(1 for r in results if r.get("audio_sec"))
    print(f"完成 {okc}/{len(results)} 遍出音频")
    for r in results:
        tag = "✅" if r.get("audio_sec") else "❌"
        print(f"  {tag} 第{r['n']}遍 {r.get('provider')}+{r.get('engine')}: {r.get('audio_sec','-')}s "
              f"| {'; '.join(r.get('issues', []))[:120]}")

if __name__ == "__main__":
    main()
