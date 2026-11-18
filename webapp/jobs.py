"""后台任务执行器 —— 单工作线程（显存纪律：同一时刻只跑一个 TTS 任务）。

状态机: queued → running(research→draft→rewrite⇄validate) → script_ready
        → synthesizing(可选, 需 TTS_URL) → done / failed
状态持久化到 jobs/<id>/job.json，前端轮询或 SSE 读取。
"""
from __future__ import annotations
import json, os, threading, time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from contracts.models import Job, SoulXScript, load_voice_manifest
from orchestrator.nodes import (PipelineState, research_node, draft_node,
                                rewrite_node, validate_node)
from orchestrator.validators import build_script

BASE_DIR = Path(__file__).resolve().parent.parent
JOBS_DIR = BASE_DIR / "jobs"
JOBS_DIR.mkdir(exist_ok=True)
TRACE_FILE = JOBS_DIR / "trace.jsonl"
MANIFEST = load_voice_manifest(BASE_DIR / "voice_lib" / "manifest.json")

_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="podcast-job")


def trace(job_id: str, event: str, **meta):
    """本地观测（P2-5）：追加一行 JSONL，/traces 页读取。"""
    rec = {"ts": time.strftime("%Y-%m-%dT%H:%M:%S"), "job": job_id, "event": event}
    rec.update(meta)
    try:
        with open(TRACE_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass
    try:                                    # 双写统一日志（结构化、可检索）
        from core import logging_setup as _ls
        _ls.set_job_id(job_id)
        _ls.log_event(_ls.setup("webapp"), f"job.{event}", **meta)
    except Exception:
        pass


def read_traces(job_id: str = "", limit: int = 200) -> list[dict]:
    if not TRACE_FILE.exists():
        return []
    lines = TRACE_FILE.read_text(encoding="utf-8").splitlines()[-1000:]
    out = []
    for ln in reversed(lines):
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        if job_id and rec.get("job") != job_id:
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out


def create_job(topic: str, style: str, minutes: int, material: str = "",
               provider: str = "mock", voice_map: dict | None = None,
               tts_url: str = "", bgm: str = "", tts_engine: str = "") -> dict:
    job = Job(topic=topic, style=style, minutes=minutes, provider=provider)
    job_dir = JOBS_DIR / job.job_id
    job_dir.mkdir(exist_ok=True)
    job.paths = {"dir": str(job_dir)}
    _dump(job)
    _set(job.job_id, bgm=bgm)                       # 模板指定的 BGM（空=按风格自动）
    try:                                    # 封面生成失败不阻塞任务（P2-8）
        from postprocess.cover import generate
        cover = generate(topic, style, minutes, job_dir / "cover.png")
        _set(job.job_id, paths={"dir": str(job_dir), "cover": cover})
    except Exception:
        pass
    _pool.submit(_run, job.job_id, topic, style, minutes, material,
                 provider, voice_map or {"S1": "v01_female_warm",
                                         "S2": "v02_male_deep"}, tts_url, bgm,
                 tts_engine)
    return {"job_id": job.job_id, "status": job.status}


def list_jobs() -> list[dict]:
    out = []
    for d in sorted(JOBS_DIR.iterdir(), reverse=True):
        jf = d / "job.json"
        if jf.exists():
            try:
                j = json.loads(jf.read_text(encoding="utf-8"))
                out.append({"job_id": j.get("job_id", d.name),
                            "topic": j.get("topic", ""),
                            "style": j.get("style", ""),
                            "minutes": j.get("minutes", 10),
                            "status": j.get("status", "unknown"),
                            "step": j.get("step", ""),
                            "error": j.get("error", ""),
                            "llm_cost_yuan": j.get("llm_cost_yuan", 0.0),
                            "est_seconds": _est(j),
                            "created": d.name.split("-")[0]
                            + " " + ":".join(d.name.split("-")[1:3])})
            except Exception:
                continue
    return out[:50]


def get_job(job_id: str) -> dict | None:
    jf = JOBS_DIR / job_id / "job.json"
    if not jf.exists():
        return None
    data = json.loads(jf.read_text(encoding="utf-8"))
    sf = JOBS_DIR / job_id / "soulx_script.json"
    if sf.exists():
        data["script"] = json.loads(sf.read_text(encoding="utf-8"))
    return data


def _run(job_id: str, topic: str, style: str, minutes: int, material: str,
         provider: str, voice_map: dict, tts_url: str, bgm: str = "",
         tts_engine: str = ""):
    job = Job(topic=topic, style=style, minutes=minutes,
              job_id=job_id, provider=provider)
    try:
        _set(job_id, status="running", step="research")
        st = PipelineState(topic=topic, style=style, minutes=minutes,
                           material=material, job=job, provider=provider,
                           voice_map=voice_map, tts_url=tts_url)
        t0 = time.time(); st = research_node(st)
        trace(job_id, "step_ms", step="research", ms=int((time.time() - t0) * 1000))
        _set(job_id, step="draft")
        t0 = time.time(); st = draft_node(st)
        trace(job_id, "step_ms", step="draft", ms=int((time.time() - t0) * 1000))
        for attempt in range(3):
            _set(job_id, step="rewrite", retry=attempt + 1)
            t0 = time.time(); st = rewrite_node(st)
            trace(job_id, "step_ms", step=f"rewrite#{attempt + 1}", ms=int((time.time() - t0) * 1000))
            _set(job_id, step="validate")
            st = validate_node(st)
            if not st.errors:
                break
        if st.errors:
            _set(job_id, status="failed",
                 error=f"3 次回炉仍未通过校验: {st.errors[:3]}")
            return

        script = build_script(job_id, style, minutes, st.turns, voice_map,
                              {"by_id": dict(load_voice_manifest(
                                  BASE_DIR / "voice_lib" / "manifest.json"))})
        sf = JOBS_DIR / job_id / "soulx_script.json"
        script.save(str(sf))
        # 成本估算（DeepSeek 参考价：输入 ¥2/M、输出 ¥8/M；Mock 为 0）
        cost = round(job.llm_tokens_in / 1e6 * 2 + job.llm_tokens_out / 1e6 * 8, 4)
        _set(job_id, paths={"dir": str(JOBS_DIR / job_id), "script": str(sf)},
             llm_tokens_in=job.llm_tokens_in,
             llm_tokens_out=job.llm_tokens_out,
             llm_cost_yuan=cost,
             turns=len(st.turns), est_seconds=script.est_seconds())

        if not tts_url:
            _set(job_id, status="script_ready",
                 step="done", hint="脚本就绪。配置 TTS_URL 后自动合成音频")
            return
        _set(job_id, status="synthesizing", step="synthesize")
        from orchestrator.tts_client import TTSClient
        client = TTSClient(tts_url)

        def _on_synth(evt):     # SSE 分段进度 → job.json（UI-4 步骤条实时显示）
            if evt.get("step") == "synthesizing":
                _set(job_id, synth=f"{evt.get('segment')}/{evt.get('total')}")
        t0 = time.time()
        meta = client.synthesize(script.to_dict(), on_progress=_on_synth,
                                 engine=tts_engine)
        trace(job_id, "step_ms", step="synthesize", ms=int((time.time() - t0) * 1000))
        paths = {"dir": str(JOBS_DIR / job_id), "script": str(sf),
                 "wav": meta.get("wav_path", "")}
        _set(job_id, paths=paths, tts_seconds=meta.get("duration_sec", 0))
        try:
            t0 = time.time()
            arts = _finalize(job_id, script.to_dict(), paths["wav"], style,
                             bgm=bgm)                     # P2-4 后期链（含模板 BGM）
            trace(job_id, "step_ms", step="postprocess", ms=int((time.time() - t0) * 1000))
            _set(job_id, srt_mode=arts.get("srt_mode", ""), bgm_used=arts.get("bgm_used"))
            if (get_job(job_id) or {}).get("sfx"):
                try:
                    apply_sfx_job(job_id)          # P2-6 有锚点时自动混入
                except Exception as se:
                    trace(job_id, "error", value=f"sfx: {se}")
            try:
                qc_job(job_id)                     # P3-1 骨架：质检（CPU whisper）
            except Exception as qe:
                trace(job_id, "error", value=f"qc: {qe}")
        except Exception as pe:                     # 后期失败不吞掉合成成果
            trace(job_id, "error", value=f"postprocess: {pe}")
            _set(job_id, status="done", step="postprocess", error=f"后期失败: {pe}",
                 paths={**paths, "final_mp3": "", "srt": ""})
            return
        _set(job_id, status="done", step="done", paths={**paths, **_final_paths(job_id)})
    except Exception as e:
        _set(job_id, status="failed", error=f"{type(e).__name__}: {e}")


def postprocess_job(job_id: str) -> dict:
    """手动触发后期链（已有 wav 的任务；P2-4 手动重跑入口）。"""
    data = get_job(job_id)
    if not data:
        raise ValueError("任务不存在")
    wav = (data.get("paths") or {}).get("wav")
    if not wav or not os.path.exists(wav):
        raise ValueError("音频尚未合成（等待 5080 TTS 服务）")
    script = data.get("script")
    if not script:
        raise ValueError("缺少脚本数据")
    _set(job_id, status="postprocessing", step="postprocess")
    arts = _finalize(job_id, script, wav, data.get("style", ""))
    paths = {**(data.get("paths") or {}), **_final_paths(job_id)}
    _set(job_id, status="done", step="done", paths=paths,
         tts_seconds=arts.get("duration_sec", data.get("tts_seconds", 0)))
    qc_job(job_id)                               # P3-1 骨架：后期完成顺带质检
    return arts


def qc_job(job_id: str) -> dict:
    """ASR 回测质检（单 ASR CPU；P3 完整版双 ASR）。结果写入 job.qc。"""
    data = get_job(job_id)
    if not data:
        raise ValueError("任务不存在")
    wav = (data.get("paths") or {}).get("wav")
    if not wav or not os.path.exists(wav):
        raise ValueError("音频尚未合成")
    script = data.get("script") or {}
    from postprocess.qc_audio import qc_audio
    t0 = time.time()
    report = qc_audio(wav, script)
    report["elapsed_sec"] = round(time.time() - t0, 1)
    _set(job_id, qc=report)
    trace(job_id, "qc", mode=report.get("mode"), passed=report.get("pass"),
          bad=len(report.get("bad_turns", [])))
    return report


def _finalize(job_id: str, script: dict, wav: str, style: str,
              bgm: str = "") -> dict:
    """执行后期链并回写产物路径。"""
    from postprocess.pipeline import run as pp_run
    arts = pp_run(script, wav, JOBS_DIR / job_id, style=style,
                  bgm_override=bgm or None)
    _set(job_id, paths={"dir": str(JOBS_DIR / job_id),
                        "final_mp3": arts["final_mp3"], "srt": arts["srt"],
                        "bgm_used": arts["bgm_used"]})
    return arts


def _final_paths(job_id: str) -> dict:
    d = JOBS_DIR / job_id
    return {"dir": str(d),
            "final_mp3": str(d / "final.mp3") if (d / "final.mp3").exists() else "",
            "srt": str(d / "subtitles.srt") if (d / "subtitles.srt").exists() else "",
            "vtt": str(d / "subtitles.vtt") if (d / "subtitles.vtt").exists() else ""}


def edit_job(job_id: str, trim_start=None, trim_end=None,
             speed: float = 1.0, turn_gains: dict | None = None) -> dict:
    """UI-6 剪辑：轮次音量 + trim + speed → edited.mp3。"""
    data = get_job(job_id)
    if not data:
        raise ValueError("任务不存在")
    wav = (data.get("paths") or {}).get("wav")
    if not wav or not os.path.exists(wav):
        raise ValueError("音频尚未合成（等待 5080 TTS 服务）")
    script = data.get("script")
    if not script:
        raise ValueError("缺少脚本数据")
    from postprocess.edit import edit
    from postprocess.subtitles import bounds_from_srt
    out = JOBS_DIR / job_id / "edited.mp3"
    paths = data.get("paths") or {}
    bounds = None
    if paths.get("srt") and os.path.exists(paths["srt"]):
        bounds = bounds_from_srt(paths["srt"])           # 复用精确时间轴
    res = edit(script, wav, out, trim_start=trim_start, trim_end=trim_end,
               speed=speed, turn_gains={int(k): v for k, v in (turn_gains or {}).items()},
               bounds=bounds)
    _set(job_id, paths={"edited_mp3": res["edited"]})
    trace(job_id, "edit", **{k: res[k] for k in ("duration_sec", "speed")})
    return res


# ---------- P2-6 音效/转场对位 ----------

def list_sfx(job_id: str) -> dict:
    data = get_job(job_id) or {}
    anchors = data.get("sfx") or []
    script = data.get("script") or {"text": [], "speakers": []}
    paths = data.get("paths") or {}
    wav = paths.get("wav")
    total = _duration_of(wav) if wav and os.path.exists(wav) else 0.0
    from postprocess.subtitles import bounds_from_srt
    from postprocess.sfx import resolve_anchors
    bounds = None
    if paths.get("srt") and os.path.exists(paths["srt"]):
        bounds = bounds_from_srt(paths["srt"])       # 有精确字幕 → 复用精确时间轴
    resolved = resolve_anchors(script, total, anchors, bounds=bounds) if total else []
    return {"anchors": anchors, "resolved": resolved,
            "total_sec": round(total, 1),
            "timeline": "precise" if bounds else "estimated"}


def add_sfx(job_id: str, turn: int, ratio: float, name: str,
            gain_db: float = -6) -> dict:
    data = get_job(job_id) or {}
    script = data.get("script") or {}
    n = len(script.get("text", []))
    if not (0 <= turn < n):
        raise ValueError(f"轮次越界（共 {n} 轮）")
    from postprocess.sfx import sfx_path
    sfx_path(name)                                   # 校验音效名
    anchors = data.get("sfx") or []
    anchors.append({"turn": int(turn), "ratio": round(min(1.0, max(0.0, ratio)), 3),
                    "name": name, "gain_db": gain_db})
    _set(job_id, sfx=anchors)
    trace(job_id, "sfx_add", turn=turn, ratio=ratio, name=name)
    return list_sfx(job_id)


def remove_sfx(job_id: str, idx: int) -> dict:
    data = get_job(job_id) or {}
    anchors = data.get("sfx") or []
    if not (0 <= idx < len(anchors)):
        raise ValueError("锚点索引越界")
    anchors.pop(idx)
    _set(job_id, sfx=anchors)
    return list_sfx(job_id)


def apply_sfx_job(job_id: str) -> dict:
    """按句内比例解析锚点并混入成品 → final_sfx.mp3（auto apply 也用此函数）。"""
    data = get_job(job_id) or {}
    anchors = data.get("sfx") or []
    if not anchors:
        raise ValueError("还没有音效锚点")
    script = data.get("script") or {}
    paths = data.get("paths") or {}
    wav = paths.get("wav")
    final = paths.get("final_mp3")
    base = final if final and os.path.exists(final) else wav
    if not base or not os.path.exists(base):
        raise ValueError("音频尚未合成")
    from postprocess.sfx import resolve_anchors, apply_sfx
    from postprocess.subtitles import bounds_from_srt
    voice_total = _duration_of(wav) if wav and os.path.exists(wav) else _duration_of(base)
    bounds = None
    if paths.get("srt") and os.path.exists(paths["srt"]):
        bounds = bounds_from_srt(paths["srt"])
    resolved = resolve_anchors(script, voice_total, anchors, bounds=bounds)
    offset = 3.0 if (data.get("bgm_used") and base == final) else 0.0   # BGM 前奏偏移
    for r in resolved:
        r["abs_time"] = round(r["abs_time"] + offset, 3)
    out = JOBS_DIR / job_id / "final_sfx.mp3"
    res = apply_sfx(base, resolved, out)
    _set(job_id, paths={"final_sfx": str(out)})
    trace(job_id, "sfx_apply", count=res["count"], duration=res["duration_sec"])
    return res


def _duration_of(path: str) -> float:
    from postprocess.mix import _duration
    try:
        return _duration(path)
    except Exception:
        return 0.0


def create_jobs_from_schedule(sch: dict) -> list[str]:
    """执行一条 schedule：为每个主题创建一个任务（单 worker 排队）。"""
    created = []
    voice_map = {"S1": sch.get("host_voice", "v01_female_warm"),
                 "S2": sch.get("partner_voice", "v02_male_deep")}
    for topic in sch.get("topics") or []:
        r = create_job(topic, sch.get("style", "幽默对谈"),
                       int(sch.get("minutes", 10)),
                       provider=os.getenv("DEFAULT_PROVIDER", "mock"),
                       voice_map=voice_map,
                       tts_url=os.getenv("TTS_URL", ""),
                       bgm=sch.get("bgm", ""))
        created.append(r["job_id"])
    trace("scheduler", "schedule_run", schedule=sch.get("id"),
          count=len(created))
    return created


def _scheduler_loop():
    """P3-5 夜间队列轮询：每 20s 检查到期 schedule。"""
    from webapp.schedules_store import due_schedules, mark_run
    while True:
        try:
            for sch in due_schedules():
                mark_run(sch["id"])
                create_jobs_from_schedule(sch)
        except Exception as e:
            trace("scheduler", "error", value=str(e)[:200])
        time.sleep(20)


def start_scheduler() -> None:
    if os.getenv("DISABLE_SCHEDULER") == "1":
        return
    t = threading.Thread(target=_scheduler_loop, name="night-queue", daemon=True)
    t.start()


# ---------- 持久化 helpers ----------

def _dump(job: Job):
    (JOBS_DIR / job.job_id).mkdir(exist_ok=True)
    job.save(str(JOBS_DIR / job.job_id / "job.json"))


def _set(job_id: str, **kw):
    jf = JOBS_DIR / job_id / "job.json"
    data = json.loads(jf.read_text(encoding="utf-8")) if jf.exists() else {}
    for k, v in kw.items():
        if k == "paths" and isinstance(v, dict):        # paths 深合并，避免覆盖 cover 等既有键
            data["paths"] = {**(data.get("paths") or {}), **v}
        else:
            data[k] = v
    data.setdefault("job_id", job_id)
    jf.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    # 观测：状态/步骤变化落 trace
    for k in ("status", "step", "error"):
        if k in kw and kw[k] not in ("", None):
            trace(job_id, k, value=kw[k] if k != "error" else str(kw[k])[:200])


def _est(j: dict) -> float:
    return j.get("est_seconds", j.get("minutes", 10) * 60)
