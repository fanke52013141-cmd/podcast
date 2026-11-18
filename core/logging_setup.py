"""统一日志核心 —— JSONL 结构化 + contextvars 上下文 + 按天滚动（零依赖）。

设计（docs/PLAN · 日志系统 2026-11-18）：
- 每行一个 JSON 对象：ts(毫秒)/level/logger/event/message/request_id/job_id/ctx/exc
- request_id / job_id 用 contextvars 传播：HTTP 中间件设置 request_id，
  任务执行器设置 job_id，线程池 worker 继承副本，整条调用链可串联
- 双 handler：logs/<app>.jsonl（机器检索）+ stderr（人类可读单行）
- 按天滚动 backupCount=14（TimedRotatingFileHandler，单进程单文件安全）
- 便捷入口：log_event(logger, event, level=..., message=..., **ctx)
"""
from __future__ import annotations
import contextvars
import json
import logging
import os
import sys
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = Path(os.environ.get("LOG_DIR", BASE_DIR / "logs"))

_request_id: contextvars.ContextVar[str] = contextvars.ContextVar("request_id", default="")
_job_id: contextvars.ContextVar[str] = contextvars.ContextVar("job_id", default="")


def set_request_id(v: str) -> str:
    _request_id.set(v or "")
    return v


def get_request_id() -> str:
    return _request_id.get()


def set_job_id(v: str) -> str:
    _job_id.set(v or "")
    return v


def get_job_id() -> str:
    return _job_id.get()


def _new_request_id() -> str:
    return time.strftime("%H%M%S") + "-" + os.urandom(4).hex()


class JsonlFormatter(logging.Formatter):
    """机器格式：一行一个 JSON（解析失败的行直接跳过，绝不崩）。"""

    def format(self, record: logging.LogRecord) -> str:
        try:
            msg = record.getMessage()
            rec = {
                "ts": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(record.created))
                      + f".{int(record.msecs):03d}",
                "level": record.levelname,
                "logger": record.name,
                "event": getattr(record, "event", "") or msg[:60] or "log",
                "message": msg,
                "request_id": _request_id.get(),
                "job_id": _job_id.get(),
            }
            ctx = getattr(record, "ctx", None)
            if ctx:
                rec["ctx"] = ctx
            if record.exc_info:
                rec["exc"] = self.formatException(record.exc_info)
            return json.dumps(rec, ensure_ascii=False, default=str)
        except Exception:                                   # 日志器自身永不抛
            return json.dumps({"ts": "", "level": record.levelname,
                               "logger": record.name, "event": "format_error",
                               "message": record.getMessage()})


class ConsoleFormatter(logging.Formatter):
    """人类格式：HH:MM:SS LEVEL [logger] event — message  {ctx 摘要}"""

    def format(self, record: logging.LogRecord) -> str:
        ts = time.strftime("%H:%M:%S", time.localtime(record.created))
        event = getattr(record, "event", "") or record.getMessage()[:40]
        line = f"{ts} {record.levelname:<7} [{record.name}] {event}"
        if record.getMessage() and record.getMessage() != event:
            line += f" — {record.getMessage()}"
        ctx = getattr(record, "ctx", None)
        if ctx:
            brief = " ".join(f"{k}={str(v)[:48]}" for k, v in list(ctx.items())[:6])
            line += f"  {{{brief}}}"
        rid = _request_id.get()
        if rid:
            line += f" rid={rid}"
        if record.exc_info:
            line += "\n" + self.formatException(record.exc_info)
        return line


_CONFIGURED: set[str] = set()


def setup(app_name: str, level: str = "INFO") -> logging.Logger:
    """初始化 logs/<app_name>.jsonl + 控制台；幂等（同名 app 只配一次）。"""
    logger = logging.getLogger(app_name)
    if app_name in _CONFIGURED:
        return logger
    LOG_DIR.mkdir(parents=True, exist_ok=True)

    logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    logger.propagate = False

    jsonl = TimedRotatingFileHandler(LOG_DIR / f"{app_name}.jsonl",
                                     when="midnight", backupCount=14,
                                     encoding="utf-8")
    jsonl.setFormatter(JsonlFormatter())
    logger.addHandler(jsonl)

    cons = logging.StreamHandler(sys.stderr)
    cons.setFormatter(ConsoleFormatter())
    logger.addHandler(cons)

    # uvicorn.access 丢弃（我们自己记 http.request，避免重复刷屏）；error 汇入同一 JSONL
    access = logging.getLogger("uvicorn.access")
    access.handlers.clear()
    access.propagate = False
    access.setLevel(logging.CRITICAL)
    for uv in ("uvicorn.error",):
        ul = logging.getLogger(uv)
        ul.handlers.clear()
        ul.propagate = False
        ul.addHandler(jsonl)
        ul.addHandler(cons)
        ul.setLevel(logging.INFO)

    _CONFIGURED.add(app_name)
    return logger


def log_event(logger: logging.Logger, event: str, *,
              _level: int = logging.INFO, message: str = "", **ctx) -> None:
    """结构化事件入口：log_event(log, "synthesize.done", duration_ms=12.3)。
    级别用 _level（下划线避免与业务字段 level 撞名），其余 kwargs 全进 ctx。"""
    logger.log(_level, message or event, extra={"event": event, "ctx": ctx or None})


def read_jsonl(path: Path, *, limit: int = 500, level: str = "", q: str = "",
               rid: str = "", jid: str = "", logger_name: str = "") -> list[dict]:
    """倒序读取并过滤 JSONL（尾部 limit*4 行内匹配，容忍坏行）。"""
    if not path.exists():
        return []
    lines = path.read_text(encoding="utf-8", errors="replace").splitlines()[-limit * 4:]
    out: list[dict] = []
    lv = level.upper()
    for ln in reversed(lines):
        if not ln.strip():
            continue
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        if lv and rec.get("level") != lv:
            continue
        if logger_name and rec.get("logger") != logger_name:
            continue
        if rid and rec.get("request_id") != rid:
            continue
        if jid and rec.get("job_id") != jid:
            continue
        if q and q.lower() not in json.dumps(rec, ensure_ascii=False).lower():
            continue
        out.append(rec)
        if len(out) >= limit:
            break
    return out
