"""TTS FastAPI 服务 —— 部署在 5080 机器（WSL2）。

启动:  uvicorn tts_service.server:app --host 0.0.0.0 --port 8600
接口:
  GET  /health          模型/显存状态
  GET  /voices          音色库列表（供编排层 Agent 自动选音色）
  POST /synthesize      soulx_script.json -> wav（SSE 进度可选 /synthesize/sse）
借鉴 indextts2-studio: 懒加载 + 显存分档 + SSE 分步进度。
"""
from __future__ import annotations
import asyncio, json, os, shutil, time
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse

from tts_service.soulx_wrapper import synthesize

app = FastAPI(title="Podcast Studio TTS Service", version="0.1.0")
OUT_DIR = Path(os.getenv("TTS_OUT_DIR", "tts_out")); OUT_DIR.mkdir(exist_ok=True)
VOICE_LIB = Path(os.getenv("VOICE_LIB_DIR", "voice_lib"))

_state = {"loaded": False, "last_vram_gb": None}


@app.get("/health")
def health():
    vram_free = None
    try:  # WSL2 下 nvidia-smi 可用
        import subprocess
        r = subprocess.run(["nvidia-smi", "--query-gpu=memory.free",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=5)
        vram_free = int(r.stdout.strip()) / 1024
    except Exception:
        pass
    return {"model": os.getenv("SOULX_MODEL_PATH", "soulx-1.7b"),
            "loaded": _state["loaded"], "vram_free_gb": vram_free,
            "last_vram_gb": _state["last_vram_gb"]}


@app.get("/voices")
def voices():
    mf = VOICE_LIB / "manifest.json"
    if not mf.exists():
        raise HTTPException(404, "voice_lib/manifest.json 不存在")
    data = json.loads(mf.read_text(encoding="utf-8"))
    return [{"id": v["id"], "name": v["name"], "gender": v["gender"],
             "style_tags": v["style_tags"], "persona_name": v.get("persona_name", "")}
            for v in data["voices"]]


@app.post("/synthesize")
def synthesize_ep(payload: dict):
    job_id = payload.get("script", {}).get("meta", {}).get("job_id",
                                                           time.strftime("%H%M%S"))
    out = str(OUT_DIR / f"{job_id}.wav")
    if not shutil.which("ffmpeg"):
        raise HTTPException(500, "ffmpeg 未安装（apt install ffmpeg）")
    t0 = time.time()
    try:
        meta = synthesize(payload["script"], out)
    except Exception as e:
        raise HTTPException(500, f"合成失败: {e}") from e
    meta["total_sec"] = round(time.time() - t0, 1)
    return meta


@app.post("/synthesize/sse")
async def synthesize_sse(payload: dict):
    """SSE 进度版：分段合成时逐步上报（借鉴 indextts2-studio 五步进度）。"""
    async def gen():
        q: asyncio.Queue = asyncio.Queue()
        def on_progress(i, n):
            q.put_nowait({"step": "synthesizing", "segment": i, "total": n})
        job = payload.get("script", {}).get("meta", {}).get("job_id", "job")
        task = asyncio.get_event_loop().run_in_executor(
            None, lambda: synthesize(payload["script"],
                                     str(OUT_DIR / f"{job}.wav"),
                                     on_progress=on_progress))
        while not task.done() or not q.empty():
            try:
                evt = await asyncio.wait_for(q.get(), timeout=0.5)
                yield f"data: {json.dumps(evt, ensure_ascii=False)}\n\n"
            except asyncio.TimeoutError:
                yield ": keepalive\n\n"
        result = await task
        yield f"data: {json.dumps({'step': 'done', **result}, ensure_ascii=False)}\n\n"
    return StreamingResponse(gen(), media_type="text/event-stream")
