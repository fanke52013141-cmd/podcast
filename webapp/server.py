"""Web 服务 —— 工作台 UI + REST API（FastAPI + 静态页零构建，indextts2-studio 模式）。

启动:  py -m uvicorn webapp.server:app --port 8765
打开:  http://127.0.0.1:8765
"""
from __future__ import annotations
import asyncio, json, os, re
from pathlib import Path

from fastapi import FastAPI, HTTPException, UploadFile, File
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles


# ---------- 统一日志（PLAN · 日志系统 2026-11-18） ----------
import logging
import time

from core import logging_setup as logsys
from core.logging_setup import LOG_DIR

log = logsys.setup("webapp")

def _new_rid() -> str:
    import uuid as _u
    return _u.uuid4().hex[:12]

from pydantic import BaseModel

from orchestrator.extract import extract
from webapp import jobs as jobmgr
from webapp import settings_store

# 启动时把 .env 注入 os.environ：保证 os.getenv 在任意进程重启后都能读到
# （此前只有设置页保存时才注入，重启后写稿/TTS 引擎拿不到 Key）
for _k, _v in settings_store.read_env_file().items():
    if _v and not os.environ.get(_k):
        os.environ[_k] = _v


def log_event(logger, event, **ctx):
    """便捷包装（request_id/job_id 由 contextvars 自动带上）。"""
    logsys.log_event(logger, event, **ctx)


BASE_DIR = Path(__file__).resolve().parent.parent
app = FastAPI(title="AI 播客工坊", version="0.1.0")


@app.on_event("startup")
def _startup():
    jobmgr.start_scheduler()          # P3-5 夜间队列轮询（DISABLE_SCHEDULER=1 可关）


@app.middleware("http")
async def logging_context(request, call_next):
    """每请求分配 request_id（响应头 X-Request-ID）+ 记录方法/路径/状态/耗时。"""
    rid = logsys.set_request_id(_new_rid())
    t0 = time.perf_counter()
    try:
        resp = await call_next(request)
    except Exception as e:
        log.error(f"{type(e).__name__}: {e}", exc_info=True,
                  extra={"event": "http.unhandled_error",
                         "ctx": {"method": request.method, "path": request.url.path}})
        raise
    dur = round((time.perf_counter() - t0) * 1000, 1)
    resp.headers["X-Request-ID"] = rid
    if not request.url.path.startswith("/static"):
        log_event(log, "http.request", method=request.method,
                  path=request.url.path, status=resp.status_code, duration_ms=dur)
    return resp


@app.middleware("http")
async def no_cache_for_dev(request, call_next):
    """开发期静态资源禁用启发式缓存：每次都带条件请求，改动即时可见。"""
    resp = await call_next(request)
    p = request.url.path
    if p.startswith("/static") or p in ("/", "/tasks", "/voices", "/settings", "/traces") \
            or p.startswith("/job/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/")
def index():
    return FileResponse(BASE_DIR / "webapp" / "static" / "index.html")


@app.get("/settings")
def settings_page():
    return FileResponse(BASE_DIR / "webapp" / "static" / "settings.html")


@app.get("/voices")
def voices_page():
    return FileResponse(BASE_DIR / "webapp" / "static" / "voices.html")


@app.get("/tasks")
def tasks_page():
    return FileResponse(BASE_DIR / "webapp" / "static" / "tasks.html")


@app.get("/traces")
def traces_page():
    return FileResponse(BASE_DIR / "webapp" / "static" / "traces.html")


@app.get("/logs")
def logs_page():
    return FileResponse(BASE_DIR / "webapp" / "static" / "logs.html")


@app.get("/job/{job_id}")
def job_page(job_id: str):
    if jobmgr.get_job(job_id) is None:
        raise HTTPException(404, "任务不存在")
    return FileResponse(BASE_DIR / "webapp" / "static" / "job.html")


app.mount("/static", StaticFiles(directory=BASE_DIR / "webapp" / "static"), name="static")


# ---------- API ----------

class CreateReq(BaseModel):
    topic: str
    style: str = "幽默对谈"
    minutes: int = 10
    material: str = ""            # URL / 文件路径 / 原始素材文本
    provider: str = "mock"        # mock | api（api 需配置 DEEPSEEK_API_KEY）
    tts_url: str = ""             # 留空则只产出脚本
    bgm: str = ""                 # 模板指定 BGM 文件名（空=按风格自动）
    tts_engine: str = ""          # soulx | indextts | minimax | doubao（空=服务端默认）
    host_voice: str = ""          # 主持人音色（空=用默认值）
    partner_voice: str = ""       # 搭档音色（空=用默认值）


@app.post("/api/jobs")
def create_job(req: CreateReq):
    if not req.topic.strip():
        raise HTTPException(400, "主题不能为空")
    material = req.material.strip()
    try:
        if material:
            material, source_type = extract(material)
        elif re.match(r"^https?://", req.topic.strip()):
            material, source_type = extract(req.topic.strip())   # 主题即链接 → 抓正文
            print(f"[extract] {req.topic.strip()} -> {source_type}, {len(material)} chars")
    except Exception as e:
        raise HTTPException(400, f"素材抽取失败: {e}")
    voice_map = {"S1": req.host_voice or os.getenv("DEFAULT_HOST_VOICE", "v01_female_warm"),
                 "S2": req.partner_voice or os.getenv("DEFAULT_PARTNER_VOICE", "v02_male_deep")}
    tts_url = req.tts_url or os.getenv("TTS_URL", "")
    # 音色×引擎亲和校验（PLAN §2.0）：引擎指定且 TTS 可用时不兼容组合直接拦截
    if req.tts_engine and tts_url:
        from webapp import voice_store
        bad = []
        for sid, vid in voice_map.items():
            v = voice_store.get(vid)
            sup = voice_store.supported_engines(v) if v else set()
            if sup and req.tts_engine not in sup:
                bad.append(f"{sid}={vid}（该音色不支持 {req.tts_engine}，支持: {', '.join(sorted(sup))}）")
        if bad:
            raise HTTPException(400, "引擎与音色不兼容: " + "; ".join(bad))
    return jobmgr.create_job(req.topic.strip(), req.style, req.minutes,
                             material=material, provider=req.provider,
                             voice_map=voice_map, tts_url=tts_url, bgm=req.bgm,
                             tts_engine=req.tts_engine)


@app.post("/api/upload")
async def upload_material(file: UploadFile = File(...)):
    """工作台素材上传：保存到 uploads/ 并返回服务器路径（供 material 使用）。"""
    import re
    import time as _t
    if not file.filename.lower().endswith((".pdf", ".docx", ".txt", ".md")):
        raise HTTPException(400, "支持 PDF / DOCX / TXT / MD")
    content = await file.read()
    if len(content) > 30 * 1024 * 1024:
        raise HTTPException(400, "文件超过 30MB")
    updir = BASE_DIR / "uploads"
    updir.mkdir(exist_ok=True)
    safe = re.sub(r"[^\w.\-\u4e00-\u9fff]+", "_", file.filename)
    p = updir / f"{_t.strftime('%Y%m%d-%H%M%S')}-{safe}"
    p.write_bytes(content)
    return {"path": str(p), "name": file.filename, "size": len(content)}


# ---------- 节目模板（P3-7） ----------

@app.get("/api/templates")
def get_templates():
    from webapp import templates_store
    return templates_store.list_templates()


class TemplateReq(BaseModel):
    name: str
    style: str = "幽默对谈"
    host_voice: str = ""
    partner_voice: str = ""
    bgm: str = ""
    minutes: int = 10


@app.post("/api/templates")
def create_template(req: TemplateReq):
    from webapp import templates_store
    try:
        return templates_store.create_template(req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/templates/{tid}")
def delete_template(tid: str):
    from webapp import templates_store
    templates_store.delete_template(tid)
    return {"ok": True}


# ---------- 夜间批量队列（P3-5） ----------

@app.get("/api/schedules")
def get_schedules():
    from webapp import schedules_store
    return schedules_store.list_schedules()


class ScheduleReq(BaseModel):
    time: str
    topics: list[str] = []
    style: str = "幽默对谈"
    host_voice: str = "v01_female_warm"
    partner_voice: str = "v02_male_deep"
    minutes: int = 10
    bgm: str = ""
    enabled: bool = True


@app.post("/api/schedules")
def create_schedule(req: ScheduleReq):
    from webapp import schedules_store
    try:
        return schedules_store.create_schedule(req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/schedules/{sid}")
def delete_schedule(sid: str):
    from webapp import schedules_store
    schedules_store.delete_schedule(sid)
    return {"ok": True}


@app.post("/api/schedules/{sid}/run-now")
def run_schedule_now(sid: str):
    from webapp import schedules_store
    for s in schedules_store.list_schedules():
        if s["id"] == sid:
            created = jobmgr.create_jobs_from_schedule(s)
            return {"ok": True, "created": created, "count": len(created)}
    raise HTTPException(404, "schedule 不存在")


@app.get("/api/jobs")
def list_jobs():
    return jobmgr.list_jobs()


@app.get("/api/jobs/{job_id}")
def get_job(job_id: str):
    j = jobmgr.get_job(job_id)
    if j is None:
        raise HTTPException(404, "任务不存在")
    return j


@app.get("/api/jobs/{job_id}/stream")
async def stream(job_id: str):
    """SSE：任务状态变化时推送（1s 轮询 job.json，够用且零依赖）。"""
    async def gen():
        last = None
        for _ in range(1800):                     # 最长 30 分钟
            j = jobmgr.get_job(job_id)
            cur = json.dumps({"status": j.get("status"), "step": j.get("step"),
                              "error": j.get("error")}, ensure_ascii=False) if j else "{}"
            if cur != last:
                last = cur
                yield f"data: {cur}\n\n"
            if j and j.get("status") in ("done", "failed", "script_ready"):
                break
            await asyncio.sleep(1)
    return StreamingResponse(gen(), media_type="text/event-stream")


@app.get("/api/jobs/{job_id}/audio")
def job_audio(job_id: str):
    j = jobmgr.get_job(job_id) or {}
    wav = (j.get("paths") or {}).get("wav")
    if not wav or not os.path.exists(wav):
        raise HTTPException(404, "音频尚未合成（等待 5080 TTS 服务）")
    return FileResponse(wav, media_type="audio/wav", filename=f"{job_id}.wav")


@app.get("/api/jobs/{job_id}/srt")
def job_srt(job_id: str):
    p = (jobmgr.get_job(job_id) or {}).get("paths", {}).get("srt")
    if not p or not os.path.exists(p):
        raise HTTPException(404, "字幕尚未生成")
    return FileResponse(p, media_type="text/plain; charset=utf-8",
                        filename=f"{job_id}.srt")


@app.get("/api/jobs/{job_id}/vtt")
def job_vtt(job_id: str):
    p = (jobmgr.get_job(job_id) or {}).get("paths", {}).get("vtt")
    if not p or not os.path.exists(p):
        raise HTTPException(404, "VTT 字幕尚未生成")
    return FileResponse(p, media_type="text/vtt; charset=utf-8",
                        filename=f"{job_id}.vtt")


@app.post("/api/jobs/{job_id}/qc")
def run_qc(job_id: str):
    try:
        return jobmgr.qc_job(job_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs/{job_id}/postprocess")
def run_postprocess(job_id: str):
    try:
        return jobmgr.postprocess_job(job_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/jobs/{job_id}/export")
def job_export(job_id: str):
    j = jobmgr.get_job(job_id) or {}
    paths = j.get("paths") or {}
    for key, mime, ext in (("final_sfx", "audio/mpeg", "mp3"),
                           ("final_mp3", "audio/mpeg", "mp3"),
                           ("wav", "audio/wav", "wav")):
        p = paths.get(key)
        if p and os.path.exists(p):
            return FileResponse(p, media_type=mime, filename=f"{job_id}.{ext}")
    raise HTTPException(404, "成品尚未生成")


@app.get("/api/jobs/{job_id}/play")
def job_play(job_id: str):
    """列表内联播放：优先成品 MP3，不带 attachment，浏览器可直接流式播放。"""
    j = jobmgr.get_job(job_id) or {}
    paths = j.get("paths") or {}
    for key, mime in (("final_sfx", "audio/mpeg"),
                      ("final_mp3", "audio/mpeg"),
                      ("wav", "audio/wav")):
        p = paths.get(key)
        if p and os.path.exists(p):
            return FileResponse(p, media_type=mime)
    raise HTTPException(404, "音频尚未生成")


@app.get("/api/jobs/{job_id}/cover")
def job_cover(job_id: str):
    p = (jobmgr.get_job(job_id) or {}).get("paths", {}).get("cover")
    if not p or not os.path.exists(p):
        raise HTTPException(404)
    return FileResponse(p, media_type="image/png")


@app.delete("/api/jobs/{job_id}")
def delete_job(job_id: str):
    import shutil
    d = Path(jobmgr.JOBS_DIR) / job_id
    if not d.exists():
        raise HTTPException(404, "任务不存在")
    shutil.rmtree(d)
    return {"ok": True, "deleted": job_id}


class DiagReq(BaseModel):
    job_id: str = ""


# ---------- RSS 批量（P1-14） ----------

class RssReq(BaseModel):
    url: str
    limit: int = 5


@app.post("/api/rss/preview")
def rss_preview(req: RssReq):
    from orchestrator.extract import rss_items
    try:
        return rss_items(req.url, limit=req.limit)
    except Exception as e:
        raise HTTPException(400, f"RSS 解析失败: {e}")


class BatchReq(BaseModel):
    url: str
    limit: int = 5
    style: str = "幽默对谈"
    minutes: int = 10
    provider: str = "mock"
    bgm: str = ""
    host_voice: str = "v01_female_warm"
    partner_voice: str = "v02_male_deep"


@app.post("/api/jobs/batch")
def create_batch(req: BatchReq):
    """RSS 每一条创建一个播客任务，使用界面当前风格/时长/音色（单 worker 排队）。"""
    from orchestrator.extract import rss_items
    try:
        feed = rss_items(req.url, limit=req.limit)
    except Exception as e:
        raise HTTPException(400, f"RSS 解析失败: {e}")
    created = []
    for it in feed["items"]:
        material = f"{it['title']}\n{it['summary']}"
        r = jobmgr.create_job(it["title"], req.style, req.minutes,
                              material=material, provider=req.provider,
                              voice_map={"S1": req.host_voice, "S2": req.partner_voice},
                              tts_url=os.getenv("TTS_URL", ""), bgm=req.bgm)
        created.append(r["job_id"])
    return {"feed_title": feed.get("feed_title", ""), "created": created,
            "count": len(created)}


# ---------- 本地观测（P2-5） ----------

@app.get("/api/traces")
def get_traces(job_id: str = "", limit: int = 200):
    return jobmgr.read_traces(job_id=job_id, limit=limit)


# ---------- 剪辑（UI-6） ----------

class EditReq(BaseModel):
    trim_start: float | None = None
    trim_end: float | None = None
    speed: float = 1.0
    turn_gains: dict = {}


@app.post("/api/jobs/{job_id}/edit")
def edit_job(job_id: str, req: EditReq):
    try:
        return jobmgr.edit_job(job_id, req.trim_start, req.trim_end,
                               req.speed, req.turn_gains)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/jobs/{job_id}/edited")
def edited_audio(job_id: str):
    p = (jobmgr.get_job(job_id) or {}).get("paths", {}).get("edited_mp3")
    if not p or not os.path.exists(p):
        raise HTTPException(404, "剪辑版尚未生成")
    return FileResponse(p, media_type="audio/mpeg", filename=f"{job_id}-edited.mp3")


# ---------- 音效/转场对位（P2-6） ----------

class SfxReq(BaseModel):
    turn: int
    ratio: float = 0.0
    name: str = "whoosh"
    gain_db: float = -6


@app.get("/api/jobs/{job_id}/sfx")
def get_sfx(job_id: str):
    return jobmgr.list_sfx(job_id)


@app.post("/api/jobs/{job_id}/sfx")
def add_sfx(job_id: str, req: SfxReq):
    try:
        return jobmgr.add_sfx(job_id, req.turn, req.ratio, req.name, req.gain_db)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete("/api/jobs/{job_id}/sfx/{idx}")
def del_sfx(job_id: str, idx: int):
    try:
        return jobmgr.remove_sfx(job_id, idx)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.post("/api/jobs/{job_id}/apply-sfx")
def apply_sfx(job_id: str):
    try:
        return jobmgr.apply_sfx_job(job_id)
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get("/api/jobs/{job_id}/sfx-audio")
def sfx_audio(job_id: str):
    p = (jobmgr.get_job(job_id) or {}).get("paths", {}).get("final_sfx")
    if not p or not os.path.exists(p):
        raise HTTPException(404, "带音效的成品尚未生成")
    return FileResponse(p, media_type="audio/mpeg", filename=f"{job_id}-sfx.mp3")


# ---------- VoiceStar 定长片段（P2-7 接口） ----------

class FixedReq(BaseModel):
    text: str
    target_duration: float
    voice_id: str = ""


@app.post("/api/fixed-segment")
def fixed_segment(req: FixedReq):
    from tts_service.fixed_segment import synthesize_fixed
    try:
        return synthesize_fixed(req.text, req.target_duration)
    except ValueError as e:
        raise HTTPException(400, str(e))
    except RuntimeError as e:
        raise HTTPException(503, str(e))
    except Exception as e:
        raise HTTPException(500, f"{type(e).__name__}: {e}")


@app.post("/api/settings/test-minimax")
def test_minimax(payload: dict = {}):
    """MiniMax 探活：4 字最小合成（成本可忽略），验证 Key/网络。"""
    import time as _t
    from tts_service import minimax_engine as mx
    user_key = (payload or {}).get("api_key", "").strip()
    saved = None
    if user_key and "•" not in user_key:
        saved = os.environ.get("MINIMAX_API_KEY")
        os.environ["MINIMAX_API_KEY"] = user_key
    t0 = _t.time()
    try:
        mx.t2a_sync("你好。", mx.DEFAULT_VOICE)
    except Exception as e:
        if saved is not None:
            os.environ["MINIMAX_API_KEY"] = saved
        return {"ok": False, "error": str(e)[:120]}
    if saved is not None:
        os.environ["MINIMAX_API_KEY"] = saved
    return {"ok": True, "latency_ms": int((_t.time() - t0) * 1000)}


@app.post("/api/settings/test-doubao")
def test_doubao(payload: dict = {}):
    """豆包探活：最小合成，验证 Key/网络。"""
    import time as _t
    from tts_service import doubao_engine as db
    user_key = (payload or {}).get("api_key", "").strip()
    saved = None
    if user_key and "•" not in user_key:
        saved = os.environ.get("DOUBAO_API_KEY")
        os.environ["DOUBAO_API_KEY"] = user_key
    t0 = _t.time()
    try:
        db.create_speech("你好。", [], "")
    except Exception as e:
        if saved is not None:
            os.environ["DOUBAO_API_KEY"] = saved
        return {"ok": False, "error": str(e)[:120]}
    if saved is not None:
        os.environ["DOUBAO_API_KEY"] = saved
    return {"ok": True, "latency_ms": int((_t.time() - t0) * 1000)}


# ---------- LLM 账号管理（借鉴 PPT_presentation_video 账号体系） ----------
from webapp import accounts_store as acc_store


@app.get("/api/llm-accounts")
def llm_accounts_list():
    act = acc_store.active_id()
    return {"accounts": acc_store.list_accounts(), "active_id": act}


@app.get("/api/llm-accounts/current")
def llm_accounts_current():
    a = acc_store.get_active()
    if not a:
        raise HTTPException(404, "还没有账号，先创建一个")
    return {"account": acc_store.list_accounts and
            [x for x in acc_store.list_accounts() if x["id"] == a["id"]][0]}


@app.post("/api/llm-accounts")
def llm_accounts_create(payload: dict):
    try:
        a = acc_store.create(payload or {})
    except ValueError as e:
        raise HTTPException(422, str(e))
    return {"account": acc_store._to_dict(a)}


@app.put("/api/llm-accounts/{account_id}")
def llm_accounts_update(account_id: str, payload: dict):
    try:
        acc_store.update(account_id, payload or {})
    except ValueError as e:
        raise HTTPException(404 if "不存在" in str(e) else 422, str(e))
    return {"ok": True}


@app.post("/api/llm-accounts/{account_id}/select")
def llm_accounts_select(account_id: str):
    try:
        a = acc_store.select(account_id)
    except ValueError as e:
        raise HTTPException(404, str(e))
    return {"ok": True, "selected": a["name"], "account": a}


@app.delete("/api/llm-accounts/{account_id}")
def llm_accounts_delete(account_id: str):
    try:
        return acc_store.delete(account_id)
    except ValueError as e:
        raise HTTPException(409, str(e))


@app.get("/api/logs")
def query_logs(source: str = "all", limit: int = 500, level: str = "",
               q: str = "", rid: str = "", jid: str = "", logger_name: str = ""):
    """系统日志查询（倒序）：source=webapp|tts|all；level/q/rid/jid/logger 过滤。"""
    limit = max(1, min(int(limit), 2000))
    files = {"webapp": LOG_DIR / "webapp.jsonl", "tts": LOG_DIR / "tts.jsonl"}
    picks = list(files.values()) if source == "all" else ([files[source]] if source in files else [])
    merged: list[dict] = []
    for f in picks:
        merged.extend(logsys.read_jsonl(f, limit=limit, level=level, q=q,
                                        rid=rid, jid=jid, logger_name=logger_name))
    merged.sort(key=lambda r: r.get("ts", ""), reverse=True)
    return {"count": len(merged), "items": merged[:limit],
            "files": {k: {"path": str(v), "exists": v.exists(),
                          "size": v.stat().st_size if v.exists() else 0}
                      for k, v in files.items()}}


@app.post("/api/logs/level")
def set_log_level(payload: dict):
    """运行时调级：{"logger": "webapp|tts", "level": "DEBUG|INFO|WARNING|ERROR"}。"""
    name = str((payload or {}).get("logger", "webapp"))
    level = str((payload or {}).get("level", "INFO")).upper()
    if level not in ("DEBUG", "INFO", "WARNING", "ERROR"):
        raise HTTPException(400, f"非法级别: {level}")
    if name not in ("webapp", "tts"):
        raise HTTPException(400, f"未知日志器: {name}")
    logging.getLogger(name).setLevel(getattr(logging, level))
    log_event(log, "log.level_changed", target=name, level=level)
    return {"ok": True, "logger": name, "level": level}


@app.get("/api/logs/level")
def get_log_levels():
    return {n: logging.getLevelName(logging.getLogger(n).level) for n in ("webapp", "tts")}


@app.post("/api/diag")
def make_diag(req: DiagReq):
    """一键诊断包（P2-11）：任务产物 + 脱敏配置 → zip。"""
    import json as _json
    import time as _t
    import zipfile
    ts = _t.strftime("%Y%m%d-%H%M%S")
    zpath = Path(jobmgr.JOBS_DIR) / f"diag-{ts}.zip"
    snap = settings_store.snapshot()
    snap["llm"]["api_key_masked"] = "***"
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("system.json", _json.dumps({
            "platform": os.sys.platform, "python": os.sys.version,
            "settings": snap}, ensure_ascii=False, indent=2, default=str))
        targets = [req.job_id] if req.job_id else \
            [d.name for d in sorted(Path(jobmgr.JOBS_DIR).iterdir(), reverse=True)
             if d.is_dir() and not d.name.startswith("diag")][:5]
        for jid in targets:
            d = Path(jobmgr.JOBS_DIR) / jid
            if not d.exists():
                continue
            for f in d.iterdir():
                if f.suffix in (".json", ".srt", ".log", ".txt"):
                    z.write(f, f"{jid}/{f.name}")
    return {"ok": True, "zip": str(zpath), "size_kb": round(zpath.stat().st_size / 1024, 1)}


@app.get("/api/voices")
def voices():
    from webapp import voice_store
    return [{"id": v["id"], "name": v["name"], "gender": v["gender"],
             "age_range": v["age_range"], "style_tags": v["style_tags"],
             "persona_name": v.get("persona_name", ""),
             "license_status": v.get("license_status", ""),
             "source": v.get("source", ""),
             "speed_feel": v.get("speed_feel", "normal"),
             "language": v.get("language", "zh"),
             "prompt_audio": v.get("prompt_audio", ""),
             "prompt_text": v.get("prompt_text", "")}
            for v in voice_store.list_voices()]


class VoiceReq(BaseModel):
    name: str = ""
    gender: str = "female"
    age_range: str = "26-32"
    style_tags: list[str] = []
    speed_feel: str = "normal"
    license_status: str = "authorized"
    source: str = "self_recorded"
    prompt_text: str = ""
    persona_name: str = ""
    aliases: list[str] = []


@app.post("/api/voices")
def create_voice(req: VoiceReq):
    from webapp import voice_store
    try:
        return voice_store.create(req.model_dump())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.put("/api/voices/{vid}")
def update_voice(vid: str, req: VoiceReq):
    from webapp import voice_store
    try:
        return voice_store.update(vid, req.model_dump())
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.delete("/api/voices/{vid}")
def delete_voice(vid: str):
    from webapp import voice_store
    defaults = settings_store.snapshot()["defaults"]
    if vid in (defaults["host_voice"], defaults["partner_voice"]):
        raise HTTPException(409, "该音色正作为默认主持人/搭档，请先更换默认音色再删除")
    try:
        voice_store.delete(vid, remove_audio=True)
        return {"ok": True, "deleted": vid}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.post("/api/voices/{vid}/audio")
async def upload_voice_audio(vid: str, file: UploadFile = File(...)):
    from webapp import voice_store
    if not file.filename.lower().endswith((".wav", ".mp3", ".m4a")):
        raise HTTPException(400, "仅支持 wav/mp3/m4a 参考音频")
    content = await file.read()
    if len(content) > 20 * 1024 * 1024:
        raise HTTPException(400, "文件超过 20MB")
    suffix = "." + file.filename.rsplit(".", 1)[-1].lower()
    try:
        rel = voice_store.save_audio(vid, content, suffix)
        return {"ok": True, "path": rel}
    except ValueError as e:
        raise HTTPException(404, str(e))


@app.get("/api/voices/{vid}/audio")
def voice_audio(vid: str):
    from webapp import voice_store
    if not voice_store.get(vid):
        raise HTTPException(404, "音色不存在")
    audio = voice_store.audio_path(vid)
    if not audio:
        raise HTTPException(404, "参考音频尚未录制（P1-8）")
    return FileResponse(audio, media_type="audio/wav", filename=f"{vid}.wav")


class SetRoleReq(BaseModel):
    role: str                                        # host | partner


@app.post("/api/voices/{vid}/set-role")
def set_voice_role(vid: str, req: SetRoleReq):
    from webapp import voice_store
    if not voice_store.get(vid):
        raise HTTPException(404, "音色不存在")
    if req.role not in ("host", "partner"):
        raise HTTPException(400, "role 只能是 host 或 partner")
    from webapp.settings_store import write_env
    key = "DEFAULT_HOST_VOICE" if req.role == "host" else "DEFAULT_PARTNER_VOICE"
    write_env({key: vid})
    return {"ok": True, "role": req.role, "voice_id": vid}


class ExtractReq(BaseModel):
    source: str


@app.post("/api/extract")
def do_extract(req: ExtractReq):
    try:
        text, source_type = extract(req.source)
        return {"source_type": source_type, "chars": len(text),
                "preview": text[:400]}
    except Exception as e:
        raise HTTPException(400, f"{e}")


# ---------- 设置（UI-7）----------

@app.get("/api/settings")
def get_settings():
    return settings_store.snapshot()


@app.post("/api/settings")
def save_settings(payload: dict):
    """扁平键值更新；含 • 的掩码值视为未修改跳过。"""
    updates = {}
    for k, v in (payload or {}).items():
        if k not in settings_store.SETTINGS_KEYS:
            continue
        v = str(v).strip()
        if "•" in v and not k.startswith("PROMPT_"):   # 掩码占位 → 不覆盖（提示词除外）
            continue
        updates[k] = v
    if not updates:
        return {"saved": 0}
    settings_store.write_env(updates)
    return {"saved": len(updates), "keys": list(updates)}


class TestLLMReq(BaseModel):
    api_key: str = ""                      # 可选：测试未保存的新 key
    base_url: str = ""
    model: str = ""


@app.post("/api/settings/test-llm")
def test_llm(req: TestLLMReq):
    import time as _t
    import requests
    key = req.api_key or os.getenv("DEEPSEEK_API_KEY", "")
    base = (req.base_url or os.getenv("DEEPSEEK_BASE_URL",
            "https://api.deepseek.com")).rstrip("/")
    model = req.model or os.getenv("DEEPSEEK_MODEL", "deepseek-chat")
    if not key:
        return {"ok": False, "error": "未配置 API Key"}
    try:
        t0 = _t.time()
        r = requests.post(f"{base}/chat/completions",
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": model,
                                "messages": [{"role": "user", "content": "ping"}],
                                "max_tokens": 1},
                          timeout=10)
        ms = int((_t.time() - t0) * 1000)
        if r.status_code == 200:
            return {"ok": True, "latency_ms": ms, "model": model}
        return {"ok": False, "error": f"HTTP {r.status_code}: {r.text[:120]}"}
    except Exception as e:
        return {"ok": False, "error": f"{type(e).__name__}: {e}"}


class TestTTSReq(BaseModel):
    url: str = ""


@app.post("/api/settings/test-tts")
def test_tts(req: TestTTSReq):
    import requests
    url = (req.url or os.getenv("TTS_URL", "")).rstrip("/")
    if not url:
        return {"ok": False, "error": "未配置 TTS 服务地址"}
    try:
        r = requests.get(f"{url}/health", timeout=5)
        r.raise_for_status()
        d = r.json()
        vram = d.get("vram_free_gb")
        extra = f" · 显存空闲 {vram:.1f}GB" if isinstance(vram, (int, float)) else ""
        return {"ok": True, "detail": f"在线 · {d.get('model', 'soulx')}{extra}"}
    except Exception as e:
        return {"ok": False, "error": f"探活失败: {e}"}
