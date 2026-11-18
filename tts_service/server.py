"""TTS FastAPI 服务 —— 部署在 5080 机器（WSL2）。

启动:  uvicorn tts_service.server:app --host 0.0.0.0 --port 8600
接口:
  GET  /health          模型/显存状态
  GET  /voices          音色库列表（供编排层 Agent 自动选音色）
  POST /synthesize      soulx_script.json -> wav（SSE 进度可选 /synthesize/sse）
借鉴 indextts2-studio: 懒加载 + 显存分档 + SSE 分步进度。
"""
from __future__ import annotations
import asyncio, json, os, shutil, time, uuid
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from core import logging_setup as logsys
from core.logging_setup import read_env_file

tlog = logsys.setup("tts")

# 启动时注入 .env（MiniMax/豆包 Key、账号池等；不依赖 source .env）
for _k, _v in read_env_file().items():
    if _v and not os.environ.get(_k):
        os.environ[_k] = _v


def tlog_event(event, **ctx):
    logsys.log_event(tlog, event, **ctx)


from tts_service.soulx_wrapper import synthesize as _soulx_synthesize

TTS_ENGINE = os.getenv("TTS_ENGINE", "soulx")   # soulx | indextts | minimax | doubao
ENGINES = ("soulx", "indextts", "minimax", "doubao")


def synthesize(script, out_wav, on_progress=None, seed=7):
    """引擎路由：soulx(WSL·默认) | indextts(ComfyUI) | minimax(API) | doubao(API)。"""
    if TTS_ENGINE == "soulx":
        return _soulx_synthesize(script, out_wav, on_progress=on_progress, seed=seed)
    if TTS_ENGINE == "minimax":
        from tts_service import minimax_engine
        return minimax_engine.synthesize(script, out_wav, on_progress=on_progress, seed=seed)
    if TTS_ENGINE == "doubao":
        from tts_service import doubao_engine
        return doubao_engine.synthesize(script, out_wav, on_progress=on_progress, seed=seed)
    from tts_service import indextts_wrapper
    return indextts_wrapper.synthesize(script, out_wav, on_progress=on_progress, seed=seed)


app = FastAPI(title="Podcast Studio TTS Service", version="0.3.0")
# 绝对路径：合成子进程 cwd 在 SoulX 仓库，相对路径会写错位置
OUT_DIR = Path(os.getenv("TTS_OUT_DIR", "tts_out")).resolve(); OUT_DIR.mkdir(parents=True, exist_ok=True)
VOICE_LIB = Path(os.getenv("VOICE_LIB_DIR", "voice_lib"))

_state = {"loaded": False, "last_vram_gb": None}


@app.get("/health")
def health():
    if TTS_ENGINE == "indextts":
        comfy = None
        try:
            from tts_service import indextts_wrapper
            import requests
            r = requests.get(f"{indextts_wrapper.COMFY_URL}/system_stats", timeout=5)
            comfy = r.status_code == 200
        except Exception:
            comfy = False
        return {"engine": "indextts2.5", "comfyui": comfy,
                "loaded": _state["loaded"], "vram_free_gb": None,
                "last_vram_gb": _state["last_vram_gb"]}
    if TTS_ENGINE in ("minimax", "doubao"):
        if TTS_ENGINE == "minimax":
            from tts_service import minimax_engine as eng
            ok = bool(eng.api_key())
        else:
            from tts_service import doubao_engine as eng
            ok = bool(eng.api_key())
        return {"engine": TTS_ENGINE, "api_key_set": ok,
                "loaded": True, "vram_free_gb": None, "last_vram_gb": None}
    vram_free = None
    try:  # WSL2 下 nvidia-smi 可用
        import subprocess
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        vram_free = int(r.stdout.strip()) / 1024
    except Exception:
        pass
    return {"engine": "soulx", "model": os.getenv("SOULX_MODEL_PATH", "soulx-1.7b"),
            "loaded": _state["loaded"], "vram_free_gb": vram_free,
            "last_vram_gb": _state["last_vram_gb"]}


@app.post("/voices/design")
def voice_design_ep(payload: dict):
    """MiniMax 音色设计：文字描述 → voice_id + 试听 mp3（hex 解码落盘）。"""
    from tts_service import minimax_engine as mx
    from webapp import voice_store
    try:
        vid, buf = mx.design_voice(payload.get("prompt", ""),
                                   payload.get("preview_text", "你好，欢迎收听本期节目，希望今天的分享对你有启发。"),
                                   payload.get("voice_id", ""))
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    rel = voice_store.save_trial_audio(vid, buf, name=payload.get("name", ""))
    entry = voice_store.find_by_minimax(vid) or voice_store.create({
        "id": payload.get("voice_id_local") or f"vd_{vid[:12]}".lower().replace("-", "_"),
        "name": payload.get("name") or f"MiniMax设计·{vid[:8]}",
        "gender": payload.get("gender", "female"),
        "source": "minimax_designed", "license_status": "generated",
        "prompt_audio": rel, "prompt_text": payload.get("preview_text", ""),
    })
    entry.setdefault("engines", {})["minimax"] = {"voice_id": vid}
    voice_store.update_engines(entry["id"], entry["engines"])
    return {"voice_id": vid, "voice": entry, "trial_audio": rel}


@app.post("/voices/clone")
def voice_clone_ep(payload: dict):
    """MiniMax 快速复刻：本地音色参考音上传 → 登记 voice_id 写回 engines。"""
    from tts_service import minimax_engine as mx
    from webapp import voice_store
    v = voice_store.get(payload.get("voice_id", ""))
    if not v:
        raise HTTPException(404, f"音色不存在: {payload.get('voice_id')}")
    p = voice_store.audio_path(v["id"])
    if not p:
        raise HTTPException(400, "该音色没有本地参考音频，先上传再复刻")
    remote = payload.get("remote_voice_id") or f"podcast-{v['id']}-{uuid.uuid4().hex[:8]}"
    try:
        buf = p.read_bytes()
        rid = mx.clone_voice(buf, remote, mime="audio/wav",
                             filename=p.name)
    except Exception as e:
        raise HTTPException(400, str(e)) from e
    engines = v.get("engines") or {}
    engines["minimax"] = {"voice_id": rid}
    voice_store.update_engines(v["id"], engines)
    return {"ok": True, "remote_voice_id": rid}


@app.get("/voices")
def voices():
    mf = VOICE_LIB / "manifest.json"
    if not mf.exists():
        raise HTTPException(404, "voice_lib/manifest.json 不存在")
    data = json.loads(mf.read_text(encoding="utf-8"))
    return [{"id": v["id"], "name": v["name"], "gender": v["gender"],
             "style_tags": v["style_tags"], "persona_name": v.get("persona_name", "")}
            for v in data["voices"]]


def _route(payload: dict, script: dict, out: str, on_progress=None, seed: int = 7) -> dict:
    """按请求 engine 字段覆盖默认引擎（缺省用 TTS_ENGINE）。"""
    engine = payload.get("engine")
    if engine:
        global TTS_ENGINE
        saved, TTS_ENGINE = TTS_ENGINE, engine
        try:
            return synthesize(script, out, on_progress=on_progress, seed=seed)
        finally:
            TTS_ENGINE = saved
    return synthesize(script, out, on_progress=on_progress, seed=seed)


@app.post("/synthesize")
def synthesize_ep(payload: dict):
    job_id = payload.get("script", {}).get("meta", {}).get("job_id",
                                                           time.strftime("%H%M%S"))
    logsys.set_job_id(job_id)
    engine = payload.get("engine") or TTS_ENGINE
    turns = len(payload.get("script", {}).get("text", []))
    tlog_event("synthesize.start", engine=engine, job_id=job_id, turns=turns,
               seed=payload.get("seed", 7))
    out = str(OUT_DIR / f"{job_id}.wav")
    if not shutil.which("ffmpeg"):
        tlog_event("synthesize.fail", engine=engine, reason="ffmpeg missing")
        raise HTTPException(500, "ffmpeg 未安装（apt install ffmpeg）")
    if engine not in ENGINES:
        tlog_event("synthesize.fail", engine=engine, reason=f"unknown engine {engine}")
        raise HTTPException(400, f"未知引擎: {engine}（可选 {', '.join(ENGINES)}）")
    t0 = time.time()
    try:
        meta = _route(payload, payload["script"], out, seed=int(payload.get("seed", 7)))
    except HTTPException as e:
        tlog_event("synthesize.fail", engine=engine, reason=e.detail[:300])
        raise
    except Exception as e:
        tlog.error(f"{type(e).__name__}: {e}", exc_info=True,
                   extra={"event": "synthesize.fail",
                          "ctx": {"engine": engine, "job_id": job_id}})
        raise HTTPException(500, f"合成失败: {e}") from e
    meta["total_sec"] = round(time.time() - t0, 1)
    tlog_event("synthesize.done", engine=engine, job_id=job_id,
               duration_sec=meta.get("duration_sec"), segments=meta.get("segments"),
               elapsed_sec=meta.get("total_sec"))
    return meta


@app.post("/synthesize/sse")
async def synthesize_sse(payload: dict):
    """SSE 进度版：分段合成时逐步上报（借鉴 indextts2-studio 五步进度）。"""
    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        def on_progress(i, n):
            q.put_nowait({"step": "synthesizing", "segment": i, "total": n})
        job = payload.get("script", {}).get("meta", {}).get("job_id", "job")
        seed = int(payload.get("seed", 7))
        task = asyncio.get_event_loop().run_in_executor(
            None, lambda: _route(payload, payload["script"],
                                 str(OUT_DIR / f"{job}.wav"),
                                 on_progress=on_progress, seed=seed))
        while not task.done() or not q.empty():
            try:
                evt = await asyncio.wait_for(q.get(), timeout=0.5)
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
        result = await task
        yield f"data: {json.dumps({'step': 'done', **result}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
