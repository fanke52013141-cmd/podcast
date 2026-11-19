"""节目模板（P3-7）—— 借鉴 VoiceStudio 的 recipes 概念。

模板 = {名称, 风格, 主持音色, 搭档音色, BGM, 默认分钟} 的可复用组合，
工作台一键回填；模板同时供夜间队列（P3-5）引用。
"""
from __future__ import annotations
import json
import time
from pathlib import Path

from webapp import creator_store

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PATH = BASE_DIR / "templates.json"

SEEDS = [
    {"id": "tpl_midnight", "name": "深夜电台", "style": "深夜电台",
     "host_voice": "v01_female_warm", "partner_voice": "v02_male_deep",
     "bgm": "深夜电台_warm.mp3", "minutes": 12},
    {"id": "tpl_tech", "name": "科技周报", "style": "热点解读",
     "host_voice": "v04_male_bright", "partner_voice": "v06_female_professor",
     "bgm": "热点解读_pulse.mp3", "minutes": 10},
    {"id": "tpl_fun", "name": "幽默闲聊", "style": "幽默对谈",
     "host_voice": "v03_female_lively", "partner_voice": "v02_male_deep",
     "bgm": "幽默对谈_light.mp3", "minutes": 10},
]


def _path(p=None, *, account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> Path:
    return Path(p) if p else creator_store.templates_path(account_id)


def list_templates(p=None, *, account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> list[dict]:
    path = _path(p, account_id=account_id)
    if not path.exists():
        path.write_text(json.dumps({"templates": SEEDS}, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return json.loads(path.read_text(encoding="utf-8"))["templates"]


def create_template(data: dict, p=None, *, account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> dict:
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("模板名称不能为空")
    templates = list_templates(p, account_id=account_id)
    tpl = {
        "id": data.get("id") or f"tpl_{int(time.time() * 1000)}",
        "name": name,
        "style": data.get("style", "幽默对谈"),
        "host_voice": data.get("host_voice", ""),
        "partner_voice": data.get("partner_voice", ""),
        "bgm": data.get("bgm", ""),
        "minutes": int(data.get("minutes", 10)),
    }
    templates.append(tpl)
    path = _path(p, account_id=account_id)
    path.write_text(json.dumps({"templates": templates}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
    return tpl


def delete_template(tid: str, p=None, *, account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> None:
    templates = [t for t in list_templates(p, account_id=account_id) if t["id"] != tid]
    path = _path(p, account_id=account_id)
    path.write_text(json.dumps({"templates": templates}, ensure_ascii=False, indent=2),
                    encoding="utf-8")
