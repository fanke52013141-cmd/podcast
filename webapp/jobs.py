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
from webapp import style_store, skill_store

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


def _generate_show_notes(job_id: str, script) -> str:
    """Show Notes（时间轴+要点摘要）。LLM 生成要点；无账号时仅时间轴兜底。

    script 可传 SoulXScript 对象或其 to_dict() 结果（meta/speakers/text）。
    """
    if hasattr(script, "to_dict"):
        script = script.to_dict()
    script = script or {}
    turns = script.get("text", [])
    if not turns:
        return ""
    import re as _re
    meta = script.get("meta", {})
    job = get_job(job_id) or {}
    topic = job.get("topic") or meta.get("topic") or job_id
    style = job.get("style") or meta.get("style", "")
    minutes = job.get("minutes") or meta.get("target_minutes", "")
    # 时间轴：从 SRT 读每轮起始（若有），否则按 4.2 字/秒估算
    srt_p = JOBS_DIR / job_id / "subtitles.srt"
    bounds = []
    if srt_p.exists():
        txt = srt_p.read_text(encoding="utf-8", errors="replace")
        def _sec(t):
            h, m, s = t.replace(",", ".").split(":")
            return int(h) * 3600 + int(m) * 60 + float(s)
        bounds = sorted({_sec(t) for t in _re.findall(
            r"(\d+:\d+:\d+[,.]\d+) --> ", txt)})
    lines_out = [f"# {topic}", "",
                 f"> {style} · 双主播 · {minutes} 分钟".rstrip(" ·"), ""]
    # SRT 边界数与轮数对得上才用真实时间轴，否则按字数估算
    have_bounds = bool(bounds) and len(bounds) >= len(turns)
    elapsed = 0.0
    for i, (sid, t) in enumerate(turns, 1):
        start = bounds[i - 1] if have_bounds else round(elapsed, 1)
        stamp = f"{int(start // 60):02d}:{int(start % 60):02d}"
        clean = _re.sub(r"<\|[^|]*\|>", "", t)
        lines_out.append(f"- **[{stamp}] {sid}** {clean[:60]}{'…' if len(clean) > 60 else ''}")
        elapsed += len(t) / 4.2
    # 要点摘要（LLM 可用时）：全稿截短喂入，避免只看点开头
    points = []
    try:
        from orchestrator.nodes import call_llm
        diag_txt = "\n".join(f"- {t[:40]}" for _, t in turns)[:1600]
        r = call_llm(f"从以下播客对话中提炼 3-5 条听众要点，每条一行，不超过 30 字：\n{diag_txt}",
                     "你是播客编辑，只输出要点列表，不要解释。", max_retry=1)
        points = [ln.lstrip("- •") for ln in r.text.splitlines() if ln.strip()]
    except Exception:
        points = []
    lines_out += ["", "## 本期要点"]
    for pt in points[:5]:
        lines_out.append(f"- {pt}")
    return "\n".join(lines_out)


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
               tts_url: str = "", bgm: str = "", tts_engine: str = "",
               creator_account_id: str = "default") -> dict:
    job = Job(topic=topic, style=style, minutes=minutes, provider=provider)
    job_dir = JOBS_DIR / job.job_id
    job_dir.mkdir(exist_ok=True)
    job.paths = {"dir": str(job_dir)}
    _dump(job)
    style_conf = style_store.get_style(style, account_id=creator_account_id)
    skill_notes = skill_store.prompt_block(creator_account_id)
    _set(job.job_id, bgm=bgm, creator_account_id=creator_account_id,
         style_desc=style_conf.get("desc", ""),
         style_n_interrupt=style_conf.get("n_interrupt", 4),
         skill_notes=skill_notes)
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
    return {"job_id": job.job_id, "status": job.status,
            "creator_account_id": creator_account_id}


def list_jobs(creator_account_id: str = "default") -> list[dict]:
    out = []
    for d in sorted(JOBS_DIR.iterdir(), reverse=True):
        jf = d / "job.json"
        if jf.exists():
            try:
                j = json.loads(jf.read_text(encoding="utf-8"))
                if j.get("creator_account_id", "default") != creator_account_id:
                    continue
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
                            + " " + ":".join(d.name.split("-")[1:3]),
                            "creator_account_id": j.get("creator_account_id", "default")})
            except Exception:
                continue
    return out[:50]


def get_job(job_id: str, creator_account_id: str | None = None) -> dict | None:
    jf = JOBS_DIR / job_id / "job.json"
    if not jf.exists():
        return None
    data = json.loads(jf.read_text(encoding="utf-8"))
    if creator_account_id is not None and \
            data.get("creator_account_id", "default") != creator_account_id:
        return None
    sf = JOBS_DIR / job_id / "soulx_script.json"
    if sf.exists():
        data["script"] = json.loads(sf.read_text(encoding="utf-8"))
    return data


def _run(job_id: str, topic: str, style: str, minutes: int, material: str,
         provider: str, voice_map: dict, tts_url: str, bgm: str = "",
         tts_engine: str = ""):
    job = Job(topic=topic, style=style, minutes=minutes,
              job_id=job_id, provider=provider)
    persisted = get_job(job_id) or {}
    try:
        _set(job_id, status="running", step="research")
        st = PipelineState(topic=topic, style=style, minutes=minutes,
                           material=material, job=job, provider=provider,
                           voice_map=voice_map, tts_url=tts_url,
                           style_desc=persisted.get("style_desc", ""),
                           style_n_interrupt=persisted.get("style_n_interrupt"))
        if provider == "agent":
            # Deep Agent 写稿：自主规划+工具循环（体检/校验/术语内置），一次交付
            _set(job_id, step="draft")
            from orchestrator.writing_agent import run_writing_agent
            t0 = time.time()
            res = run_writing_agent(topic, minutes=minutes,
                                    material=st.material, style=style,
                                    style_desc=persisted.get("style_desc", ""),
                                    skill_notes=persisted.get("skill_notes", ""))
            trace(job_id, "step_ms", step="agent_write",
                  ms=int((time.time() - t0) * 1000),
                  lint_score=res["lint"].get("score"),
                  tokens_out=res["tokens_out"])
            st.turns = res["turns"]
            st.humanize = res["lint"]
            job.llm_tokens_in += res["tokens_in"]
            job.llm_tokens_out += res["tokens_out"]
        else:
            _set(job_id, step="research")
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
        if getattr(st, "humanize", None):
            _set(job_id, humanize=st.humanize)
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
        wav_path = meta.get("wav_path", "")
        # TTS 在 WSL 时返回 /mnt/d/... 路径，转成 Windows 路径供后期链使用
        import re as _re
        m = _re.match(r"^/mnt/([a-zA-Z])/(.*)$", wav_path)
        if m:
            wav_path = f"{m.group(1).upper()}:/{m.group(2)}"
        paths = {"dir": str(JOBS_DIR / job_id), "script": str(sf),
                 "wav": wav_path}
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
        try:                                        # Show Notes（kaikou/Podcastfy 借鉴）：要点+时间轴
            _t1 = time.time()
            sn = _generate_show_notes(job_id, script.to_dict())
            if sn:
                _set(job_id, paths={**(_final_paths(job_id)), "show_notes": str(JOBS_DIR / job_id / "show_notes.md")})
                trace(job_id, "step_ms", step="show_notes", ms=int((time.time() - _t1) * 1000))
        except Exception as se:
            trace(job_id, "error", value=f"show_notes: {se}")
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
    if not script:                              # 新任务 script 不落 job.json，从磁盘读
        sp = (data.get("paths") or {}).get("script")
        if sp and os.path.exists(sp):
            try:
                script = json.loads(Path(sp).read_text(encoding="utf-8"))
            except Exception:
                script = None
    if not script:
        raise ValueError("缺少脚本数据")
    _set(job_id, status="postprocessing", step="postprocess")
    arts = _finalize(job_id, script, wav, data.get("style", ""))
    paths = {**(data.get("paths") or {}), **_final_paths(job_id)}
    _set(job_id, status="done", step="done", paths=paths,
         tts_seconds=arts.get("duration_sec", data.get("tts_seconds", 0)))
    try:                                         # Show Notes（后期重跑也刷新）
        _t1 = time.time()
        sn = _generate_show_notes(job_id, script)
        if sn:
            _set(job_id, paths={**(get_job(job_id).get("paths") or {}), "show_notes": str(JOBS_DIR / job_id / "show_notes.md")})
            trace(job_id, "step_ms", step="show_notes", ms=int((time.time() - _t1) * 1000))
    except Exception as se:
        trace(job_id, "error", value=f"show_notes: {se}")
    qc_job(job_id)                               # P3-1 骨架：后期完成顺带质检
    return arts


def regen_show_notes(job_id: str) -> dict:
    """手动（重）生成 Show Notes —— 兼容功能上线前的存量 done 任务。"""
    data = get_job(job_id)
    if not data:
        raise ValueError("任务不存在")
    sp = (data.get("paths") or {}).get("script")
    script = None
    if sp and os.path.exists(sp):
        try:
            script = json.loads(Path(sp).read_text(encoding="utf-8"))
        except Exception:
            script = None
    sn = _generate_show_notes(job_id, script or {})
    if not sn:
        raise ValueError("缺少脚本数据，无法生成 Show Notes")
    out = JOBS_DIR / job_id / "show_notes.md"
    out.write_text(sn, encoding="utf-8")
    _set(job_id, paths={**(data.get("paths") or {}), "show_notes": str(out)})
    return {"path": str(out)}


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
    if not script:                              # 新任务 script 不落 job.json，从磁盘读
        sp = (data.get("paths") or {}).get("script")
        if sp and os.path.exists(sp):
            try:
                script = json.loads(Path(sp).read_text(encoding="utf-8"))
            except Exception:
                script = None
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
                       bgm=sch.get("bgm", ""),
                       creator_account_id=sch.get("creator_account_id", "default"))
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
