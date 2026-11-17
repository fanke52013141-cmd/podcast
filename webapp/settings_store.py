"""设置存储 —— .env 持久化 + os.environ 即时生效（无需重启服务）。

安全约定：
- API Key 只在 .env 落盘（本地），API 返回一律掩码（sk-****7f3a 形式）
- POST 时传入掩码串（含 •）视为"未修改"，不会覆盖真实值
"""
from __future__ import annotations
import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"

SETTINGS_KEYS = {
    "DEEPSEEK_API_KEY", "DEEPSEEK_BASE_URL", "DEEPSEEK_MODEL",
    "GLM_API_KEY", "GLM_BASE_URL", "GLM_MODEL",
    "TTS_URL", "SOULX_MODEL_PATH", "SEGMENT_MINUTES", "PARALING_TAGS",
    "DEFAULT_STYLE", "DEFAULT_MINUTES", "DEFAULT_HOST_VOICE", "DEFAULT_PARTNER_VOICE",
    "VOICESTAR_URL", "VOICESTAR_CMD",
}

PARALING_ALL = ["laughter", "sigh", "breathing", "coughing", "throat_clearing"]


def read_env_file() -> dict[str, str]:
    data: dict[str, str] = {}
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip().strip('"')
    return data


def write_env(updates: dict[str, str]) -> None:
    """按键更新 .env（保留其余行），并同步 os.environ 让当前进程立即生效。"""
    updates = {k: str(v) for k, v in updates.items() if k in SETTINGS_KEYS}
    lines = ENV_PATH.read_text(encoding="utf-8").splitlines() if ENV_PATH.exists() else []
    seen = set()
    out = []
    for line in lines:
        m = line.strip()
        key = m.partition("=")[0].strip() if ("=" in m and not m.startswith("#")) else None
        if key in updates:
            out.append(f"{key}={updates[key]}")
            seen.add(key)
        else:
            out.append(line)
    for k, v in updates.items():
        if k not in seen:
            out.append(f"{k}={v}")
    ENV_PATH.write_text("\n".join(out) + "\n", encoding="utf-8")
    for k, v in updates.items():
        os.environ[k] = v


def masked(secret: str | None) -> str:
    if not secret:
        return ""
    tail = secret[-4:] if len(secret) > 8 else ""
    return f"{secret[:3]}••••••{tail}"


def snapshot() -> dict:
    env = read_env_file()
    g = lambda k, d="": env.get(k) or os.getenv(k, d)          # noqa: E731
    paring = [t for t in (g("PARALING_TAGS") or ",".join(PARALING_ALL)).split(",") if t]
    return {
        "llm": {
            "provider": "DeepSeek（主）+ GLM（备）",
            "api_key_set": bool(g("DEEPSEEK_API_KEY")),
            "api_key_masked": masked(g("DEEPSEEK_API_KEY")),
            "glm_key_set": bool(g("GLM_API_KEY")),
            "model": g("DEEPSEEK_MODEL", "deepseek-chat"),
            "base_url": g("DEEPSEEK_BASE_URL", "https://api.deepseek.com"),
            "temperature": 0.9,
        },
        "tts": {
            "url": g("TTS_URL", ""),
            "model_path": g("SOULX_MODEL_PATH", "pretrained_models/SoulX-Podcast-1.7B"),
            "segment_minutes": int(g("SEGMENT_MINUTES", "7") or 7),
            "paraling": {t: (t in paring) for t in PARALING_ALL},
            "voicestar_url": g("VOICESTAR_URL", ""),
            "voicestar_cmd": g("VOICESTAR_CMD", ""),
        },
        "defaults": {
            "style": g("DEFAULT_STYLE", "幽默对谈"),
            "minutes": int(g("DEFAULT_MINUTES", "10") or 10),
            "host_voice": g("DEFAULT_HOST_VOICE", "v01_female_warm"),
            "partner_voice": g("DEFAULT_PARTNER_VOICE", "v02_male_deep"),
        },
        "paths": {
            "jobs": str(BASE_DIR / "jobs"),
            "env": str(ENV_PATH),
        },
    }
