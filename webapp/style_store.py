"""对话风格库 —— 内置九种风格 + 每账号自定义风格。

内置风格来自 orchestrator.prompts.STYLES，保持写稿质量基线；
自定义风格按创作账号独立保存，名称最多 4 个字，描述会真正传入写稿提示词。
"""
from __future__ import annotations

import copy
import json
import time
import uuid
from pathlib import Path

from webapp import creator_store
from orchestrator.prompts import STYLES as BUILTIN_STYLES

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PATH = BASE_DIR / "styles.json"


def _path(account_id: str) -> Path:
    if account_id == creator_store.DEFAULT_ACCOUNT_ID:
        return DEFAULT_PATH
    return creator_store.ensure_workspace(account_id) / "styles.json"


def _load_custom(account_id: str) -> list[dict]:
    path = _path(account_id)
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        items = data.get("styles", []) if isinstance(data, dict) else []
        return [s for s in items if isinstance(s, dict) and s.get("name")]
    except Exception:
        return []


def _save_custom(account_id: str, items: list[dict]) -> None:
    path = _path(account_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"styles": items}, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _builtin() -> list[dict]:
    return [{"id": f"builtin_{name}", "name": name, "desc": conf.get("desc", ""),
             "n_interrupt": conf.get("n_interrupt", 4), "builtin": True}
            for name, conf in BUILTIN_STYLES.items()]


def list_styles(account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> list[dict]:
    return [*copy.deepcopy(_builtin()), *copy.deepcopy(_load_custom(account_id))]


def get_style(name: str, account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> dict:
    for item in list_styles(account_id):
        if item["name"] == name:
            return item
    # 未知风格名回落到内置默认，避免任务创建失败
    return {"id": "builtin_幽默对谈", "name": "幽默对谈",
            "desc": BUILTIN_STYLES["幽默对谈"]["desc"],
            "n_interrupt": BUILTIN_STYLES["幽默对谈"]["n_interrupt"],
            "builtin": True}


def _validate(payload: dict, account_id: str, editing_id: str | None = None) -> tuple[str, str, int]:
    name = str((payload or {}).get("name") or "").strip()
    desc = str((payload or {}).get("desc") or "").strip()
    if not name:
        raise ValueError("风格名称不能为空")
    if len(name) > 4:
        raise ValueError("风格名称最多 4 个字")
    if not desc:
        raise ValueError("请填写风格描述，写稿时会按这段描述生成")
    if len(desc) > 120:
        raise ValueError("风格描述最多 120 字")
    for item in list_styles(account_id):
        if item["name"] == name and item.get("id") != editing_id:
            raise ValueError("已存在同名风格")
    try:
        n_interrupt = int((payload or {}).get("n_interrupt", 4))
    except (TypeError, ValueError):
        n_interrupt = 4
    n_interrupt = max(1, min(8, n_interrupt))
    return name, desc, n_interrupt


def create_style(payload: dict, account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> dict:
    name, desc, n_interrupt = _validate(payload, account_id)
    item = {"id": f"style_{uuid.uuid4().hex[:10]}", "name": name, "desc": desc,
            "n_interrupt": n_interrupt, "builtin": False,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    items = _load_custom(account_id)
    items.append(item)
    _save_custom(account_id, items)
    return item


def update_style(style_id: str, payload: dict,
                 account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> dict:
    items = _load_custom(account_id)
    target = next((s for s in items if s.get("id") == style_id), None)
    if not target:
        raise ValueError("内置风格不可编辑，或该风格不存在")
    name, desc, n_interrupt = _validate(payload, account_id, editing_id=style_id)
    target.update({"name": name, "desc": desc, "n_interrupt": n_interrupt})
    _save_custom(account_id, items)
    return target


def delete_style(style_id: str, account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> None:
    items = _load_custom(account_id)
    remain = [s for s in items if s.get("id") != style_id]
    if len(remain) == len(items):
        raise ValueError("内置风格不可删除，或该风格不存在")
    _save_custom(account_id, remain)
