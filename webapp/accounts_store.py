"""LLM 账号存储 —— 多账号 CRUD + 激活切换（借鉴 PPT_presentation_video
账号体系：可编辑、可切换、至少保留一个账号、当前账号显式选择）。

存储：llm_accounts.json（本地文件，无 DB 依赖）。
账号格式：{"id", "name", "base_url", "api_key", "model", "created_at"}
激活账号 id 存 .env 的 LLM_ACTIVE_ACCOUNT（写稿/探活均用它）。
写回 .env 时同步 os.environ（即时生效）。
"""
from __future__ import annotations
import json
import os
import time
import uuid
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
STORE_PATH = BASE_DIR / "llm_accounts.json"


def _load() -> dict:
    if STORE_PATH.exists():
        try:
            return json.loads(STORE_PATH.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"accounts": []}


def _save(data: dict) -> None:
    STORE_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                          encoding="utf-8")


def _mask(key: str) -> str:
    if not key:
        return ""
    tail = key[-4:] if len(key) > 8 else ""
    return f"{key[:3]}••••••{tail}"


def _to_dict(a: dict, *, mask_key: bool = True) -> dict:
    return {"id": a["id"], "name": a["name"],
            "base_url": a.get("base_url", ""),
            "model": a.get("model", ""),
            "api_key_masked": _mask(a.get("api_key", "")) if mask_key else a.get("api_key", ""),
            "api_key": a.get("api_key", "") if not mask_key else None,
            "created_at": a.get("created_at", "")}


def list_accounts() -> list[dict]:
    return [_to_dict(a) for a in _load()["accounts"]]


def active_id() -> str:
    """当前激活账号 id：读 .env 的 LLM_ACTIVE_ACCOUNT，缺省取第一个。"""
    accs = _load()["accounts"]
    if not accs:
        return ""
    act = os.getenv("LLM_ACTIVE_ACCOUNT", "").strip()
    if any(a["id"] == act for a in accs):
        return act
    return accs[0]["id"]


def get(account_id: str) -> dict | None:
    for a in _load()["accounts"]:
        if a["id"] == account_id:
            return a
    return None


def get_active() -> dict | None:
    return get(active_id())


def create(data: dict) -> dict:
    accs = _load()
    name = (data.get("name") or "").strip()
    if not name:
        raise ValueError("账号名称不能为空")
    acc = {"id": uuid.uuid4().hex[:12],
           "name": name,
           "base_url": (data.get("base_url") or "").strip(),
           "api_key": (data.get("api_key") or "").strip(),
           "model": (data.get("model") or "").strip(),
           "created_at": time.strftime("%Y-%m-%dT%H:%M:%S")}
    accs["accounts"].append(acc)
    _save(accs)
    if len(accs["accounts"]) == 1:
        select(acc["id"])                        # 第一个账号自动激活
    return acc


def update(account_id: str, patch: dict) -> dict:
    data = _load()
    for a in data["accounts"]:
        if a["id"] == account_id:
            for k in ("name", "base_url", "api_key", "model"):
                v = (patch.get(k) or "").strip()
                if v and "•" not in v:           # 掩码占位 = 未修改
                    a[k] = v
            _save(data)
            if active_id() == account_id:
                _sync_env(a)                     # 激活中的账号编辑即时生效
            return a
    raise ValueError("账号不存在")


def delete(account_id: str) -> dict:
    data = _load()
    accs = data["accounts"]
    if len(accs) <= 1:
        raise ValueError("至少保留一个账号，不能删除最后一个账号")
    data["accounts"] = [a for a in accs if a["id"] != account_id]
    _save(data)
    if active_id() == account_id:
        select(data["accounts"][0]["id"])        # 删除激活账号 → 切到第一个
    return {"deleted": account_id, "remaining": len(data["accounts"])}


def select(account_id: str) -> dict:
    """切换激活账号：写 .env LLM_ACTIVE_ACCOUNT + 注入 os.environ（即时生效）。"""
    a = get(account_id)
    if not a:
        raise ValueError("账号不存在")
    from webapp.settings_store import write_env
    write_env({"LLM_ACTIVE_ACCOUNT": account_id})
    _sync_env(a)
    return _to_dict(a)


def _sync_env(a: dict) -> None:
    """激活账号的连接信息注入 os.environ（写稿引擎即时使用）。"""
    for env_key, acc_key in (("DEEPSEEK_API_KEY", "api_key"),
                             ("DEEPSEEK_BASE_URL", "base_url"),
                             ("DEEPSEEK_MODEL", "model")):
        if a.get(acc_key):
            os.environ[env_key] = a[acc_key]
    os.environ["LLM_ACTIVE_ACCOUNT"] = a["id"]
