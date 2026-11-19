"""本机创作账号与工作区存储。

这是无需密码的本机创作身份，与 settings 中的 LLM API 账号池无关。
默认账号沿用现有数据；新增账号自动切换，并克隆当前音色/模板作为独立起点。
任务仍保留原目录，通过 job.json 的 creator_account_id 做归属隔离。
"""
from __future__ import annotations

import copy
import json
import shutil
import time
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STORE_PATH = BASE_DIR / "creator_accounts.json"
DATA_DIR = BASE_DIR / "creator_data"
DEFAULT_ACCOUNT_ID = "default"
DEFAULT_ACCOUNT_NAME = "我的账号"


def _default_account(saved: dict | None = None) -> dict:
    account = {"id": DEFAULT_ACCOUNT_ID, "name": DEFAULT_ACCOUNT_NAME,
               "created_at": "", "is_default": True}
    if saved:
        name = str(saved.get("name") or "").strip()
        if name:
            account["name"] = name
        account["created_at"] = saved.get("created_at", "")
    return account


def _load() -> dict:
    data = {"active_id": DEFAULT_ACCOUNT_ID, "accounts": []}
    saved_accounts: list[dict] = []
    if STORE_PATH.exists():
        try:
            loaded = json.loads(STORE_PATH.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                data.update(loaded)
                saved_accounts = [a for a in loaded.get("accounts", []) if isinstance(a, dict)]
        except Exception:
            pass
    saved_default = next((a for a in saved_accounts if a.get("id") == DEFAULT_ACCOUNT_ID), None)
    accounts = [a for a in saved_accounts
                if a.get("id") and a.get("id") != DEFAULT_ACCOUNT_ID]
    data["accounts"] = [_default_account(saved_default), *accounts]
    if not any(a["id"] == data.get("active_id") for a in data["accounts"]):
        data["active_id"] = DEFAULT_ACCOUNT_ID
    return data


def _save(data: dict) -> None:
    STORE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_accounts() -> list[dict]:
    return copy.deepcopy(_load()["accounts"])


def active_id() -> str:
    return _load()["active_id"]


def get(account_id: str) -> dict | None:
    return next((a for a in _load()["accounts"] if a["id"] == account_id), None)


def select(account_id: str) -> dict:
    account = get(account_id)
    if not account:
        raise ValueError("创作账号不存在")
    data = _load()
    data["active_id"] = account_id
    _save(data)
    ensure_workspace(account_id)
    return account


def create(payload: dict) -> dict:
    name = str((payload or {}).get("name") or "").strip()
    if not name:
        raise ValueError("账号名称不能为空")
    if len(name) > 30:
        raise ValueError("账号名称不能超过 30 个字符")
    data = _load()
    if any(a["name"] == name for a in data["accounts"]):
        raise ValueError("已存在同名创作账号")
    account = {"id": uuid.uuid4().hex[:12], "name": name,
               "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
               "is_default": False}
    data["accounts"].append(account)
    data["active_id"] = account["id"]
    _save(data)
    ensure_workspace(account["id"])
    return account


def update(account_id: str, payload: dict) -> dict:
    """重命名创作账号；默认账号也可改名并持久化。"""
    name = str((payload or {}).get("name") or "").strip()
    if not name:
        raise ValueError("账号名称不能为空")
    if len(name) > 30:
        raise ValueError("账号名称不能超过 30 个字符")
    data = _load()
    target = next((a for a in data["accounts"] if a["id"] == account_id), None)
    if not target:
        raise ValueError("创作账号不存在")
    if any(a["id"] != account_id and a["name"] == name for a in data["accounts"]):
        raise ValueError("已存在同名创作账号")
    target["name"] = name
    _save(data)
    return target


def delete(account_id: str) -> dict:
    """删除创作账号。默认账号不可删除；工作区移入回收目录，避免误删历史数据。"""
    if account_id == DEFAULT_ACCOUNT_ID:
        raise ValueError("默认账号不可删除")
    data = _load()
    target = next((a for a in data["accounts"] if a["id"] == account_id), None)
    if not target:
        raise ValueError("创作账号不存在")
    data["accounts"] = [a for a in data["accounts"] if a["id"] != account_id]
    if data.get("active_id") == account_id:
        data["active_id"] = DEFAULT_ACCOUNT_ID
    _save(data)
    source = DATA_DIR / account_id
    archived = ""
    if source.exists():
        trash = DATA_DIR / ".trash"
        trash.mkdir(parents=True, exist_ok=True)
        stamp = time.strftime("%Y%m%d-%H%M%S")
        dest = trash / f"{account_id}-{stamp}"
        shutil.move(str(source), str(dest))
        archived = str(dest)
    return {"account": target, "active_id": data["active_id"], "archived": archived}


def workspace(account_id: str) -> Path:
    if account_id == DEFAULT_ACCOUNT_ID:
        return BASE_DIR
    account = get(account_id)
    if not account:
        raise ValueError("创作账号不存在")
    return DATA_DIR / account_id


def ensure_workspace(account_id: str) -> Path:
    root = workspace(account_id)
    if account_id == DEFAULT_ACCOUNT_ID:
        return root
    voice_dir = root / "voice_lib"
    audio_dir = voice_dir / "audio"
    audio_dir.mkdir(parents=True, exist_ok=True)
    manifest = voice_dir / "manifest.json"
    if not manifest.exists():
        source = BASE_DIR / "voice_lib" / "manifest.json"
        data = json.loads(source.read_text(encoding="utf-8")) if source.exists() else {"voices": []}
        manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    templates = root / "templates.json"
    if not templates.exists():
        source = BASE_DIR / "templates.json"
        if source.exists():
            data = json.loads(source.read_text(encoding="utf-8"))
        else:
            from webapp.templates_store import SEEDS
            data = {"templates": SEEDS}
        templates.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return root


def voice_manifest_path(account_id: str) -> Path:
    if account_id == DEFAULT_ACCOUNT_ID:
        return BASE_DIR / "voice_lib" / "manifest.json"
    return ensure_workspace(account_id) / "voice_lib" / "manifest.json"


def voice_audio_dir(account_id: str) -> Path:
    if account_id == DEFAULT_ACCOUNT_ID:
        return BASE_DIR / "voice_lib" / "audio"
    return ensure_workspace(account_id) / "voice_lib" / "audio"


def templates_path(account_id: str) -> Path:
    if account_id == DEFAULT_ACCOUNT_ID:
        return BASE_DIR / "templates.json"
    return ensure_workspace(account_id) / "templates.json"
