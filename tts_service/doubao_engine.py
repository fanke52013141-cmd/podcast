"""豆包（火山引擎语音合成 1.0）TTS 引擎 —— seed-audio-1.0 零样本克隆。

协议对照 free-creation src/main/gateway/audio.ts buildVolcSpeechBody / generateViaVolc
（只读研究，未改动该项目）：
- POST {base}/api/v3/tts/create，头 X-Api-Key + X-Api-Request-Id(UUID)
- 参考音零样本克隆：references:[{audio_data: base64}] 与 speaker 可同时传；
  text_prompt 前缀提示参考音用法
- 响应同步：{code, message?, audio: base64, subtitle?}，code != 0 报错
- audio_config.enable_subtitle=true 时返回字幕（后期时间轴可复用，本期先落盘保存）

配置（.env）：DOUBAO_API_KEY / DOUBAO_BASE_URL(默认 https://openspeech.bytedance.com)
            DEFAULT_DOUBAO_SPEAKER(可选系统音色)
"""
from __future__ import annotations
import base64, hashlib, json, os, subprocess, time, uuid

import requests

from core import logging_setup as _ls

elog = _ls.setup("tts")

BASE = os.environ.get("DOUBAO_BASE_URL", "https://openspeech.bytedance.com").rstrip("/")
MODEL = "seed-audio-1.0"
DEFAULT_SPEAKER = os.environ.get("DEFAULT_DOUBAO_SPEAKER", "")
TEXT_LIMIT = 10000
TIMEOUT = 180
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tts_out", "turn_cache")


class DoubaoError(RuntimeError):
    pass


def api_key() -> str:
    return os.environ.get("DOUBAO_API_KEY", "").strip()


def reference_prompt_prefix(n_refs: int) -> str:
    if n_refs <= 0:
        return ""
    return f"请使用提供的{n_refs}段参考音频的音色和语气朗读以下内容：\n"


def build_speech_body(text: str, reference_b64: list[str] | None = None,
                      speaker: str = "", *, sample_rate: int = 24000,
                      speech_rate: float = 0.0, enable_subtitle: bool = True,
                      aigc_watermark: bool = True) -> dict:
    """请求体纯函数（wire 断言用）。references 与 speaker 可同时传。"""
    if not (text or "").strip():
        raise DoubaoError("合成文本不能为空")
    refs = [r for r in (reference_b64 or []) if r]
    prompt = f"{reference_prompt_prefix(len(refs))}{text.strip()}"
    if len(prompt) > TEXT_LIMIT:
        raise DoubaoError(f"豆包语音合成提示词超过 {TEXT_LIMIT} 字符（参考音频引用也计入上限）")
    body: dict = {
        "model": MODEL,
        "text_prompt": prompt,
        "audio_config": {
            "format": "wav", "sample_rate": sample_rate,
            "speech_rate": speech_rate, "loudness_rate": 0.0, "pitch_rate": 0.0,
            "enable_subtitle": enable_subtitle,
        },
        "watermark": {"aigc_watermark": aigc_watermark, "aigc_metadata": {"enable": False}},
    }
    if refs:
        body["references"] = [{"audio_data": r} for r in refs]
    if speaker:
        body["speaker"] = speaker
    return body


def create_speech(text: str, reference_b64: list[str] | None = None,
                  speaker: str = "") -> tuple[bytes, dict | None]:
    """一次请求同步返回音频（wav 字节）与可选字幕。code != 0 报可行动错误。"""
    if not api_key():
        raise DoubaoError("DOUBAO_API_KEY 未配置（设置页填写后重试）")
    body = build_speech_body(text, reference_b64, speaker)
    _ls.log_event(elog, "doubao.tts.request", speaker=bool(speaker),
                  refs=len(reference_b64 or []), text_len=len(text))
    r = requests.post(f"{BASE}/api/v3/tts/create",
                      headers={"X-Api-Key": api_key(),
                               "X-Api-Request-Id": str(uuid.uuid4()),
                               "Content-Type": "application/json"},
                      json=body, timeout=TIMEOUT)
    if r.status_code >= 400:
        _ls.log_event(elog, "doubao.tts.http_error", status=r.status_code,
                      body=r.text[:300])
        raise DoubaoError(f"豆包语音合成失败 HTTP {r.status_code}：{r.text[:200]}")
    payload = r.json() if "json" in r.headers.get("Content-Type", "") else None
    if payload is None:
        raise DoubaoError("豆包语音合成：上游返回了无法解析的内容")
    code = payload.get("code")
    if isinstance(code, int) and code != 0:
        raise DoubaoError(f"豆包语音合成失败：{payload.get('message') or f'错误码 {code}'}"
                          "（请检查 DOUBAO_API_KEY/额度）")
    audio_b64 = payload.get("audio", "")
    if not audio_b64:
        raise DoubaoError("豆包未返回音频数据")
    buf = base64.b64decode(audio_b64)
    if not buf:
        raise DoubaoError("豆包返回的音频数据无效")
    return buf, payload.get("subtitle")


# ---------------- 引擎主入口（与其它引擎同契约） ----------------

def _probe_duration(wav: str) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", wav],
                           check=True, capture_output=True, text=True)
        return float(r.stdout.strip())
    except Exception:
        return -1.0


def _turn_cache_key(text: str, ref_hash: str, speaker: str, seed: int) -> str:
    raw = json.dumps({"t": text, "r": ref_hash, "s": speaker, "e": "doubao", "seed": seed},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _ref_b64(ref_path: str | None) -> str:
    if not ref_path or not os.path.exists(ref_path):
        return ""
    with open(ref_path, "rb") as f:
        return base64.b64encode(f.read()).decode("ascii")


def _resolve_refs(spk: dict, root: str) -> tuple[list[str], str]:
    """豆包零样本克隆：本地参考音直接转 base64（engines.doubao.reference 可覆盖）。"""
    rel = ((spk.get("engines") or {}).get("doubao") or {}).get("reference") \
        or spk.get("prompt_audio") or ""
    p = rel if os.path.isabs(rel) else os.path.join(root, rel)
    if p and os.path.exists(p):
        h = hashlib.md5(open(p, "rb").read()).hexdigest()
        return [_ref_b64(p)], h
    return [], "norefs"


def synthesize(script_dict: dict, out_wav: str,
               on_progress=None, seed: int = 7) -> dict:
    """主入口：系统契约 script dict（数组 speakers，本地参考音零样本克隆）。"""
    from tts_service.indextts_wrapper import _concat_with_gap
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    out_wav = os.path.abspath(out_wav)
    speakers = {s["id"]: s for s in script_dict["speakers"]}
    turns = script_dict["text"]

    os.makedirs(CACHE_DIR, exist_ok=True)
    t0 = time.time()
    parts: list[str] = []
    for i, (sid, text) in enumerate(turns):
        if on_progress:
            on_progress(i + 1, len(turns))
        spk = speakers[sid]
        refs, ref_hash = _resolve_refs(spk, root)
        speaker = ((spk.get("engines") or {}).get("doubao") or {}).get("speaker") \
            or DEFAULT_SPEAKER
        key = _turn_cache_key(text, ref_hash, speaker, seed)
        cache_wav = os.path.join(CACHE_DIR, f"db_{key}.wav")
        wav_i = f"{out_wav}.db{i}.wav"
        if not os.path.exists(cache_wav):
            buf, _sub = create_speech(text, refs, speaker)
            with open(cache_wav, "wb") as f:
                f.write(buf)
        import shutil
        shutil.copyfile(cache_wav, wav_i)
        parts.append(wav_i)

    _concat_with_gap(parts, out_wav, 0.3)
    for p in parts:
        try: os.unlink(p)
        except OSError: pass
    return {"wav_path": out_wav, "duration_sec": round(_probe_duration(out_wav), 1),
            "segments": len(turns), "elapsed_sec": round(time.time() - t0, 1),
            "engine": "doubao"}
