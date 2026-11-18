"""音色库存储（P3-4）—— manifest.json 的读写/增删改，供 API 使用。

与 contracts.load_voice_manifest 的关系：那份是只读 dataclass 视图（管线用），
本模块面向管理操作，保留 JSON 原结构并支持热更新（无需重启服务）。
"""
from __future__ import annotations
import json
import re
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
DEFAULT_PATH = BASE_DIR / "voice_lib" / "manifest.json"
AUDIO_DIR = BASE_DIR / "voice_lib" / "audio"

EDITABLE = {"name", "gender", "age_range", "style_tags", "language", "speed_feel",
            "license_status", "source", "prompt_audio", "prompt_text",
            "persona_name", "aliases"}


def _load(path: str | Path | None = None) -> dict:
    p = Path(path) if path else DEFAULT_PATH
    return json.loads(p.read_text(encoding="utf-8"))


def _save(data: dict, path: str | Path | None = None) -> None:
    p = Path(path) if path else DEFAULT_PATH
    p.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def list_voices(path: str | Path | None = None) -> list[dict]:
    return _load(path)["voices"]


def get(voice_id: str, path: str | Path | None = None) -> dict | None:
    for v in list_voices(path):
        if v["id"] == voice_id:
            return v
    return None


def _slug(name: str) -> str:
    s = re.sub(r"[^a-zA-Z0-9_]+", "_", name.strip().lower())
    return s.strip("_")[:16] or "voice"


def create(data: dict, path: str | Path | None = None) -> dict:
    raw = _load(path)
    voices = raw["voices"]
    n = len(voices) + 1
    vid = data.get("id") or f"v{n:02d}_{_slug(data.get('name', 'voice'))}"
    if any(v["id"] == vid for v in voices):
        raise ValueError(f"音色 id 已存在: {vid}")
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
        "prompt_audio": data.get("prompt_audio", f"voice_lib/audio/{vid}.wav"),
        "prompt_text": data.get("prompt_text", ""),
        "persona_name": data.get("persona_name", ""),
        "aliases": data.get("aliases", []),
    }
    voices.append(entry)
    _save(raw, path)
    return entry


def update(voice_id: str, patch: dict, path: str | Path | None = None) -> dict:
    raw = _load(path)
    for v in raw["voices"]:
        if v["id"] == voice_id:
            for k, val in patch.items():
                if k in EDITABLE:
                    v[k] = val
            _save(raw, path)
            return v
    raise ValueError(f"音色不存在: {voice_id}")


def delete(voice_id: str, path: str | Path | None = None,
           remove_audio: bool = False) -> None:
    raw = _load(path)
    voices = raw["voices"]
    idx = next((i for i, v in enumerate(voices) if v["id"] == voice_id), None)
    if idx is None:
        raise ValueError(f"音色不存在: {voice_id}")
    entry = voices.pop(idx)
    _save(raw, path)
    if remove_audio:
        p = BASE_DIR / entry.get("prompt_audio", "")
        try:
            if p.exists():
                p.unlink()
        except OSError:
            pass


def audio_path(voice_id: str) -> Path | None:
    v = get(voice_id)
    if not v or not v.get("prompt_audio"):
        return None
    p = BASE_DIR / v["prompt_audio"]
    return p if p.exists() else None


def save_audio(voice_id: str, content: bytes, suffix: str = ".wav") -> str:
    """保存上传的参考音频并更新 manifest。返回相对路径。"""
    v = get(voice_id)
    if not v:
        raise ValueError(f"音色不存在: {voice_id}")
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    rel = f"voice_lib/audio/{voice_id}{suffix}"
    (BASE_DIR / rel).write_bytes(content)
    update(voice_id, {"prompt_audio": rel})
    return rel


# ---------- 第三方引擎音色兼容（MiniMax 设计/复刻，PLAN §2.0） ----------

def update_engines(voice_id: str, engines: dict, path: str | Path | None = None) -> dict:
    """写回音色的 engines 字段（如 {"minimax": {"voice_id": "..."}}）。"""
    raw = _load(path)
    for v in raw["voices"]:
        if v["id"] == voice_id:
            v["engines"] = engines
            _save(raw, path)
            return v
    raise ValueError(f"音色不存在: {voice_id}")


def find_by_minimax(remote_voice_id: str, path: str | Path | None = None) -> dict | None:
    """按远端 voice_id 查本地音色（防重复登记）。"""
    for v in list_voices(path):
        if ((v.get("engines") or {}).get("minimax") or {}).get("voice_id") == remote_voice_id:
            return v
    return None


def save_trial_audio(remote_voice_id: str, content: bytes,
                     name: str = "") -> str:
    """保存 MiniMax 设计音色的试听音频（试听接口优先播放它）。"""
    AUDIO_DIR.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9_-]+", "_", remote_voice_id)[:32] or "voice"
    rel = f"voice_lib/audio/trial_{safe}.mp3"
    (BASE_DIR / rel).write_bytes(content)
    return rel


def supported_engines(voice: dict) -> set[str]:
    """音色可用的引擎集合（音色×引擎亲和校验，PLAN §2.0）。

    本地参考音 → soulx/indextts/doubao（豆包零样本克隆）可用；
    engines.minimax 登记过 → minimax 用登记音色；
    未登记 → minimax 仍可用（引擎回落 DEFAULT_MINIMAX_VOICE 系统音色）。
    """
    eng = {"soulx", "indextts", "doubao", "minimax"} if voice.get("prompt_audio") \
        and (BASE_DIR / voice["prompt_audio"]).exists() else set()
    if ((voice.get("engines") or {}).get("minimax") or {}).get("voice_id"):
        eng.add("minimax")
    return eng
