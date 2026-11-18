"""日志系统验证（PLAN · 日志系统 2026-11-18）。

覆盖：JSONL 结构/上下文传播/滚动器/过滤读取；真实 webapp 的
request_id 注入、http.request 事件、日志 API 过滤与级别调整。
运行: python tests/test_logging.py
"""
from __future__ import annotations
import json, os, sys, tempfile, time, urllib.request
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

PASS = FAIL = 0
def ok(cond, name):
    global PASS, FAIL
    if cond: PASS += 1; print(f"  ✅ {name}")
    else: FAIL += 1; print(f"  ❌ {name}")

def main():
    from core import logging_setup as ls

    tmp = Path(tempfile.mkdtemp(prefix="podlog_"))
    ls.LOG_DIR = tmp                                  # 重定向到临时目录
    log = ls.setup("webapp")
    import logging as _logging
    log.setLevel(_logging.DEBUG)                      # 放行 DEBUG 事件以验证结构
    ok((tmp / "webapp.jsonl").parent.exists(), "日志目录创建")

    print("== 1. JSONL 结构 ==")
    rid = ls.set_request_id("req-abc-123")
    jid = ls.set_job_id("job-xyz")
    ls.log_event(log, "unit.demo", _level=10, message="hello", k1="v1", n=2)
    ls.set_request_id("")
    log.error("boom", exc_info=ValueError("ctx fail"),
              extra={"event": "unit.error", "ctx": {"op": "test"}})
    time.sleep(0.05)
    recs = ls.read_jsonl(tmp / "webapp.jsonl", limit=10)
    ok(len(recs) == 2, f"两条事件落盘（实际 {len(recs)}）")
    d1 = recs[-1]
    ok(d1["level"] == "DEBUG" and d1["event"] == "unit.demo", "级别与 event 字段")
    ok(d1["request_id"] == "req-abc-123" and d1["job_id"] == "job-xyz", "contextvars 传播")
    ok(d1["ctx"]["k1"] == "v1" and d1["ctx"]["n"] == 2, "ctx 结构化字段")
    ok(all(r["ts"].startswith("20") and "." in r["ts"] for r in recs), "毫秒级 ts")
    ok(all(r.get("exc") and "ValueError" in r["exc"] for r in recs if "exc" in r), "异常堆栈入库且可序列化")

    print("== 2. 过滤读取 ==")
    ok(len(ls.read_jsonl(tmp / "webapp.jsonl", level="ERROR")) == 1, "level 过滤")
    ok(len(ls.read_jsonl(tmp / "webapp.jsonl", q="hello")) == 1, "关键字过滤")
    ok(len(ls.read_jsonl(tmp / "webapp.jsonl", rid="nope")) == 0, "rid 不匹配为空")
    ok(ls.read_jsonl(tmp / "nope.jsonl") == [], "缺失文件安全返回空")

    print("== 3. 真实 webapp：request_id / http.request / 日志 API ==")
    import subprocess
    env = dict(os.environ, PYTHONPATH=os.path.abspath("."),
               LOG_DIR=str(tmp), DISABLE_SCHEDULER="1")
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "webapp.server:app",
                             "--host", "127.0.0.1", "--port", "8799"], env=env,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = "http://127.0.0.1:8799"
        for _ in range(50):
            time.sleep(0.2)
            try:
                urllib.request.urlopen(base + "/api/settings", timeout=2)
                break
            except Exception:
                continue
        r = urllib.request.urlopen(base + "/api/voices", timeout=10)
        rid_out = r.headers.get("X-Request-ID", "")
        ok(bool(rid_out), f"响应头 X-Request-ID（{rid_out[:10]}…）")
        time.sleep(0.4)
        lr = json.loads(urllib.request.urlopen(
            base + f"/api/logs?source=webapp&limit=50&rid={rid_out}", timeout=10).read())
        evs = [i["event"] for i in lr["items"]]
        ok("http.request" in evs, "http.request 事件已记录")
        ok(all(i["request_id"] == rid_out for i in lr["items"] if i["event"] == "http.request"),
           "API 按 rid 过滤")
        lv = json.loads(urllib.request.urlopen(base + "/api/logs/level", timeout=10).read())
        ok(lv.get("webapp") == "INFO", "级别查询")
        rq = urllib.request.Request(base + "/api/logs/level",
            data=json.dumps({"logger": "webapp", "level": "DEBUG"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        ok(json.loads(urllib.request.urlopen(rq, timeout=10).read())["ok"], "运行时调级")
        # 任务创建 → job.* 事件进入统一日志
        rq = urllib.request.Request(base + "/api/jobs",
            data=json.dumps({"topic": "日志系统验证任务", "minutes": 1,
                             "provider": "mock"}).encode(),
            headers={"Content-Type": "application/json"}, method="POST")
        job = json.loads(urllib.request.urlopen(rq, timeout=30).read())
        time.sleep(2.5)
        lr = json.loads(urllib.request.urlopen(
            base + f"/api/logs?source=webapp&limit=50&jid={job['job_id']}", timeout=10).read())
        ok(any(i["event"].startswith("job.") for i in lr["items"]), "job.* 事件带 job_id 入统一日志")
    finally:
        proc.terminate()

    print(f"\n结果: {PASS} 通过 / {FAIL} 失败")
    sys.exit(1 if FAIL else 0)

if __name__ == "__main__":
    main()
