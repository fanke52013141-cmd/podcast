"""音色库存储——manifest.json 的读写/增删改，供 API 使用。

默认创作账号沿用 voice_lib/manifest.json；其他账号使用独立工作区。
历史音频路径保持兼容，新上传音频落在对应创作账号目录。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from webapp import creator_store

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PATH = BASE_DIR / "voice_lib" / "manifest.json"
AUDIO_DIR = BASE_DIR / "voice_lib" / "audio"

EDITABLE = {"name", "gender", "age_range", "style_tags", "language", "speed_feel",
            "license_status", "source", "prompt_audio", "prompt_text",
            "persona_name", "aliases"}


def _manifest(path: str | Path | None = None, *,
              account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> Path:
    return Path(path) if path else creator_store.voice_manifest_path(account_id)


def _audio_dir(path: str | Path | None = None, *,
               account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> Path:
    return Path(path).parent / "audio" if path else creator_store.voice_audio_dir(account_id)


def _load(path: str | Path | None = None, *,
          account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> dict:
    return json.loads(_manifest(path, account_id=account_id).read_text(encoding="utf-8"))


def _save(data: dict, path: str | Path | None = None, *,
          account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> None:
    _manifest(path, account_id=account_id).write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_voices(path: str | Path | None = None, *,
                account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> list[dict]:
    return _load(path, account_id=account_id)["voices"]


def get(voice_id: str, path: str | Path | None = None, *,
        account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> dict | None:
    for v in list_voices(path, account_id=account_id):
        if v["id"] == voice_id:
            return v
    return None


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip().lower())
    return s.strip("_")[:16] or "voice"


def create(data: dict, path: str | Path | None = None, *,
           account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> dict:
    raw = _load(path, account_id=account_id)
    voices = raw["voices"]
    n = len(voices) + 1
    vid = data.get("id") or f"v{n:02d}_{_slug(data.get('name', 'voice'))}"
    if any(v["id"] == vid for v in voices):
        raise ValueError(f"音色 id 已存在: {vid}")
    audio_dir = _audio_dir(path, account_id=account_id)
    try:
        default_audio = str((audio_dir / f"{vid}.wav").relative_to(BASE_DIR)).replace("\\", "/")
    except ValueError:
        default_audio = f"voice_lib/audio/{vid}.wav"
    entry = {
        "id": vid,
        "name": data.get("name", vid),
        "gender": data.get("gender", "female"),
        "age_range": data.get("age_range", "26-32"),
        "style_tags": data.get("style_tags", []),
        "language": data.get("language", "zh"),
        "speed_feel": data.get("speed_feel", "normal"),
        "license_status": data.get("license_status", "authorized"),
        "source": data.get("source", "self_recorded"),
        "prompt_audio": data.get("prompt_audio", default_audio),
        "prompt_text": data.get("prompt_text", ""),
        "persona_name": data.get("persona_name", ""),
        "aliases": data.get("aliases", []),
    }
    voices.append(entry)
    _save(raw, path, account_id=account_id)
    return entry


def update(voice_id: str, patch: dict, path: str | Path | None = None, *,
           account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> dict:
    raw = _load(path, account_id=account_id)
    for v in raw["voices"]:
        if v["id"] == voice_id:
            for k, val in patch.items():
                if k in EDITABLE:
                    v[k] = val
            _save(raw, path, account_id=account_id)
            return v
    raise ValueError(f"音色不存在: {voice_id}")


def delete(voice_id: str, path: str | Path | None = None,
           remove_audio: bool = False, *,
           account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> None:
    raw = _load(path, account_id=account_id)
    voices = raw["voices"]
    idx = next((i for i, v in enumerate(voices) if v["id"] == voice_id), None)
    if idx is None:
        raise ValueError(f"音色不存在: {voice_id}")
    entry = voices.pop(idx)
    _save(raw, path, account_id=account_id)
    if remove_audio:
        p = (BASE_DIR / entry.get("prompt_audio", "")).resolve()
        try:
            if account_id != creator_store.DEFAULT_ACCOUNT_ID:
                # 克隆音色可共享默认音频；新账号只能删除自己工作区内后来上传的音频。
                p.relative_to(creator_store.voice_audio_dir(account_id).resolve())
            if p.exists():
                p.unlink()
        except ValueError:
            pass
        except OSError:
            pass


def audio_path(voice_id: str, *, account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> Path | None:
    v = get(voice_id, account_id=account_id)
    if not v or not v.get("prompt_audio"):
        return None
    p = BASE_DIR / v["prompt_audio"]
    return p if p.exists() else None


def save_audio(voice_id: str, content: bytes, suffix: str = ".wav", *,
               account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> str:
    """保存上传的参考音频并更新当前账号 manifest。返回相对路径。"""
    v = get(voice_id, account_id=account_id)
    if not v:
        raise ValueError(f"音色不存在: {voice_id}")
    audio_dir = _audio_dir(account_id=account_id)
    audio_dir.mkdir(parents=True, exist_ok=True)
    rel = str((audio_dir / f"{voice_id}{suffix}").relative_to(BASE_DIR)).replace("\\", "/")
    (BASE_DIR / rel).write_bytes(content)
    update(voice_id, {"prompt_audio": rel}, account_id=account_id)
    return rel


# ---------- 第三方引擎音色兼容 ----------

def update_engines(voice_id: str, engines: dict, path: str | Path | None = None, *,
                   account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> dict:
    raw = _load(path, account_id=account_id)
    for v in raw["voices"]:
        if v["id"] == voice_id:
            v["engines"] = engines
            _save(raw, path, account_id=account_id)
            return v
    raise ValueError(f"音色不存在: {voice_id}")


def find_by_minimax(remote_voice_id: str, path: str | Path | None = None, *,
                    account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> dict | None:
    for v in list_voices(path, account_id=account_id):
        if ((v.get("engines") or {}).get("minimax") or {}).get("voice_id") == remote_voice_id:
            return v
    return None


def save_trial_audio(remote_voice_id: str, content: bytes,
                     name: str = "", *, account_id: str = creator_store.DEFAULT_ACCOUNT_ID) -> str:
    audio_dir = _audio_dir(account_id=account_id)
    audio_dir.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", remote_voice_id)[:32] or "voice"
    path = audio_dir / f"trial_{safe}.mp3"
    path.write_bytes(content)
    return str(path.relative_to(BASE_DIR)).replace("\\", "/")


def supported_engines(voice: dict) -> set[str]:
    """音色可用的引擎集合（音色×引擎亲和校验）。"""
    eng = {"soulx", "indextts", "doubao", "minimax"} if voice.get("prompt_audio") \
        and (BASE_DIR / voice["prompt_audio"]).exists() else set()
    if ((voice.get("engines") or {}).get("minimax") or {}).get("voice_id"):
        eng.add("minimax")
    return eng
