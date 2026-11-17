"""夜间批量队列（P3-5）—— 简单持久化定时调度。

语义（v1，务实版）：
    每条 schedule = {time:"HH:MM", topics:[...], style/host/partner/minutes/bgm,
                     enabled, last_run: "YYYY-MM-DD"}
    每天到点（进程内 20s 轮询）把列表中的主题各创建一个任务；
    last_run 防止同一天重复触发。支持"立即执行一次"用于验证与手动补跑。

注意：进程需常驻（uvicorn 运行期间生效）；不做跨进程锁（单实例部署）。
"""
from __future__ import annotations
import json
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PATH = BASE_DIR / "schedules.json"


def _path(p=None) -> Path:
    return Path(p) if p else DEFAULT_PATH


def list_schedules(p=None) -> list[dict]:
    path = _path(p)
    if not path.exists():
        path.write_text(json.dumps({"schedules": []}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))["schedules"]


def _save(schedules: list[dict], p=None) -> None:
    _path(p).write_text(json.dumps({"schedules": schedules}, ensure_ascii=False, indent=2),
                        encoding="utf-8")


def create_schedule(data: dict, p=None) -> dict:
    t = (data.get("time") or "").strip()
    if not (len(t) == 5 and t[2] == ":"):
        raise ValueError("时间格式应为 HH:MM")
    topics = [x.strip() for x in (data.get("topics") or []) if str(x).strip()]
    if not topics:
        raise ValueError("主题列表不能为空")
    sch = {
        "id": data.get("id") or f"sch_{int(time.time() * 1000)}",
        "time": t,
        "topics": topics,
        "style": data.get("style", "幽默对谈"),
        "host_voice": data.get("host_voice", "v01_female_warm"),
        "partner_voice": data.get("partner_voice", "v02_male_deep"),
        "minutes": int(data.get("minutes", 10)),
        "bgm": data.get("bgm", ""),
        "enabled": bool(data.get("enabled", True)),
        "last_run": "",
    }
    schedules = list_schedules(p)
    schedules.append(sch)
    _save(schedules, p)
    return sch


def delete_schedule(sid: str, p=None) -> None:
    _save([s for s in list_schedules(p) if s["id"] != sid], p)


def mark_run(sid: str, p=None, day: str | None = None) -> None:
    schedules = list_schedules(p)
    for s in schedules:
        if s["id"] == sid:
            s["last_run"] = day or time.strftime("%Y-%m-%d")
    _save(schedules, p)


def due_schedules(now: str | None = None, p=None) -> list[dict]:
    """纯函数便于测试：now="HH:MM"，返回今天尚未执行的到期 schedule。"""
    now = now or time.strftime("%H:%M")
    today = time.strftime("%Y-%m-%d")
    return [s for s in list_schedules(p)
            if s.get("enabled") and s.get("time") == now and s.get("last_run") != today]
