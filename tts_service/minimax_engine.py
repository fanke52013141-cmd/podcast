"""MiniMax TTS 引擎 —— t2a_v2 同步合成 + voice_design/voice_clone 音色能力。

协议对照 free-creation src/main/gateway/audio.ts 与 voice.ts（只读研究，未改动该项目）：
- 合成: POST {base}/v1/t2a_v2，Bearer 鉴权，响应 data.audio 为 hex 编码音频
- 异步: POST /v1/t2a_async_v2 → 轮询 /v1/query/t2a_async_query_v2 → /v1/files/retrieve
  （下载产物可能是 TAR 归档，必须解包取音频成员——实测坑）
- voice_id 必填，缺失上游直接拒绝（invalid params, voice id wrong）
- 错误统一看 base_resp.status_code != 0；HTML 错误页归一化为可行动错误
- voice_design: {prompt, preview_text, voice_id?, aigc_watermark} → {voice_id, trial_audio(hex)}
- voice_clone: /v1/files/upload(purpose=voice_clone) → /v1/voice_clone；
  回执 base_resp=0 但 input_sensitive=true 时音色并未登记，必须拦截

配置（.env）：MINIMAX_API_KEY / MINIMAX_BASE_URL(默认 https://api.minimaxi.com)
            MINIMAX_MODEL(默认 speech-02-hd) / DEFAULT_MINIMAX_VOICE(默认音色)
"""
from __future__ import annotations
import base64, hashlib, json, os, re, subprocess, time, uuid

import requests

# MiniMax 遇到不可发音的特殊符号（破折号/省略号等）会输出"哔"音。
# 合成前净化为可发音的停顿标点。
_SPECIAL_CHARS = [
    ("——", "，"), ("—", "，"), ("――", "，"),
    ("……", "。"), ("···", "。"),
    ("·", "，"), ("―", "，"), ("–", "，"), ("‒", "，"),
    ("~", "，"), ("～", "，"),
]


def sanitize_text(text: str) -> str:
    for a, b in _SPECIAL_CHARS:
        text = text.replace(a, b)
    text = re.sub(r"[，。]{2,}", lambda m: m.group(0)[0], text)  # 连续停顿收敛
    return text.strip()

from core import logging_setup as _ls

elog = _ls.setup("tts")

BASE = os.environ.get("MINIMAX_BASE_URL", "https://api.minimaxi.com").rstrip("/")
MODEL = os.environ.get("MINIMAX_MODEL", "speech-02-hd")
DEFAULT_VOICE = os.environ.get("DEFAULT_MINIMAX_VOICE", "male-qn-qingse")
TIMEOUT_SYNTH = 120
TIMEOUT_POLL = 600
POLL_INTERVAL = 3
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tts_out", "turn_cache")
VALID_VOICE_ID = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{7,254}$")


class MiniMaxError(RuntimeError):
    """统一可行动错误：文案带上游原文与下一步建议。"""


def api_key() -> str:
    return os.environ.get("MINIMAX_API_KEY", "").strip()


# ---------------- 协议纯函数（wire 断言用，不改全局状态） ----------------

def build_t2a_body(text: str, voice_id: str, *, speed: float = 1.0,
                   vol: float = 1.0, pitch: int = 0,
                   sample_rate: int = 32000, format_: str = "mp3",
                   bitrate: int = 128000, channel: int = 1,
                   aigc_watermark: bool = True,
                   english_normalization: bool = True) -> dict:
    """同步 t2a_v2 请求体。voice_setting.voice_id 必填（上游硬校验）。"""
    if not (text or "").strip():
        raise MiniMaxError("合成文本不能为空")
    return {
        "model": MODEL,
        "text": text.strip(),
        "voice_setting": {
            "voice_id": voice_id or DEFAULT_VOICE,
            "speed": speed, "vol": vol, "pitch": pitch,
            "english_normalization": english_normalization,
        },
        "audio_setting": {
            "audio_sample_rate": sample_rate, "bitrate": bitrate,
            "format": format_, "channel": channel,
        },
        "aigc_watermark": aigc_watermark,
    }


def build_voice_clone_body(file_id: int, voice_id: str, *,
                           need_noise_reduction: bool = True,
                           need_volume_normalization: bool = True,
                           aigc_watermark: bool = True) -> dict:
    """voice_clone 请求体（voice_id 命名规则上游校验，本地先拦）。"""
    if not VALID_VOICE_ID.match(voice_id or ""):
        raise MiniMaxError("voice_id 需 8~256 位、字母开头、仅字母数字与 - _，末位不能是 - 或 _")
    return {
        "file_id": file_id, "voice_id": voice_id, "model": MODEL,
        "need_noise_reduction": need_noise_reduction,
        "need_volume_normalization": need_volume_normalization,
        "aigc_watermark": aigc_watermark,
    }


def build_voice_design_body(prompt: str, preview_text: str,
                            voice_id: str = "", aigc_watermark: bool = True) -> dict:
    if not (prompt or "").strip():
        raise MiniMaxError("音色描述不能为空")
    if not (preview_text or "").strip():
        raise MiniMaxError("试听文本不能为空")
    if voice_id and not VALID_VOICE_ID.match(voice_id):
        raise MiniMaxError("自定义 voice_id 需 8~256 位、字母开头、仅字母数字与 - _")
    body: dict = {"prompt": prompt.strip(), "preview_text": preview_text.strip(),
                  "aigc_watermark": aigc_watermark}
    if voice_id:
        body["voice_id"] = voice_id
    return body


# ---------------- 回执归一化 ----------------

def assert_ok(payload: dict | None, prefix: str) -> None:
    """base_resp.status_code != 0 → 可行动错误；HTML 错误页也归一化。"""
    if payload is None:
        raise MiniMaxError(f"{prefix}：上游返回了无法解析的内容（可能网关错误页）")
    base = payload.get("base_resp") or {}
    code = base.get("status_code")
    if isinstance(code, int) and code != 0:
        raise MiniMaxError(f"{prefix}：{base.get('status_msg') or f'错误码 {code}'}"
                           "（请检查 MiniMax API Key/余额/参数）")


def looks_like_tar(buf: bytes) -> bool:
    return len(buf) > 512 and buf[257:262] == b"ustar"


def pick_audio_from_tar(buf: bytes) -> bytes | None:
    """从 ustar 归档里取第一个非空成员（异步下载产物实测是 TAR）。"""
    off = 0
    while off + 512 <= len(buf):
        header = buf[off:off + 512]
        name = header[:100].split(b"\0")[0].decode("utf-8", "replace")
        try:
            size = int(header[124:136].split(b"\0")[0] or b"0", 8)
        except ValueError:
            return None
        if name and size > 0 and off + 512 + size <= len(buf):
            data = buf[off + 512:off + 512 + size]
            if name.lower().endswith((".mp3", ".wav", ".flac", ".m4a", ".aac")):
                return data
        off += 512 + ((size + 511) // 512 * 512 if size else 512)
    return None


# ---------------- HTTP 调用 ----------------

def _headers() -> dict:
    if not api_key():
        raise MiniMaxError("MINIMAX_API_KEY 未配置（设置页填写后重试）")
    return {"Authorization": f"Bearer {api_key()}", "Content-Type": "application/json"}


def t2a_sync(text: str, voice_id: str) -> bytes:
    """同步合成：返回 mp3 字节（data.audio 为 hex 编码）。"""
    text = sanitize_text(text)
    _ls.log_event(elog, "minimax.t2a.request", voice_id=voice_id, text_len=len(text))
    r = requests.post(f"{BASE}/v1/t2a_v2", headers=_headers(),
                      json=build_t2a_body(text, voice_id), timeout=TIMEOUT_SYNTH)
    if r.status_code >= 400:
        _ls.log_event(elog, "minimax.t2a.http_error", status=r.status_code,
                      body=r.text[:300], voice_id=voice_id)
    if r.status_code != 0 and r.status_code >= 400:
        raise MiniMaxError(f"MiniMax 合成失败 HTTP {r.status_code}：{r.text[:200]}")
    payload = r.json() if "json" in r.headers.get("Content-Type", "") else None
    assert_ok(payload, "MiniMax 合成失败")
    hex_audio = ((payload or {}).get("data") or {}).get("audio", "")
    if not hex_audio:
        raise MiniMaxError("MiniMax 未返回音频数据")
    buf = bytes.fromhex(hex_audio)
    if not buf:
        raise MiniMaxError("MiniMax 返回的音频数据无效")
    return buf


def upload_file(purpose: str, buf: bytes, mime: str, filename: str) -> int:
    """上传参考音频，返回 file_id（voice_clone 用）。"""
    r = requests.post(f"{BASE}/v1/files/upload", headers={"Authorization": f"Bearer {api_key()}"},
                      data={"purpose": purpose},
                      files={"file": (filename, buf, mime)}, timeout=120)
    if r.status_code >= 400:
        raise MiniMaxError(f"MiniMax 上传音频失败 HTTP {r.status_code}：{r.text[:200]}")
    payload = r.json() if "json" in r.headers.get("Content-Type", "") else None
    assert_ok(payload, "MiniMax 上传音频失败")
    fid = ((payload or {}).get("file") or {}).get("file_id")
    fid = int(fid) if isinstance(fid, (int, float, str)) and str(fid).isdigit() else None
    if not fid:
        raise MiniMaxError("MiniMax 未返回有效 file_id")
    return fid


def clone_voice(reference: bytes, voice_id: str, *,
                mime: str = "audio/wav", filename: str = "ref.wav") -> str:
    """快速复刻：上传参考音 → 登记音色 → 返回 voice_id（input_sensitive 拦截）。"""
    fid = upload_file("voice_clone", reference, mime, filename)
    r = requests.post(f"{BASE}/v1/voice_clone", headers=_headers(),
                      json=build_voice_clone_body(fid, voice_id), timeout=TIMEOUT_SYNTH)
    payload = r.json() if "json" in r.headers.get("Content-Type", "") else None
    assert_ok(payload, "MiniMax 创建克隆音色失败")
    if (payload or {}).get("input_sensitive") is True:
        raise MiniMaxError("MiniMax 判定参考音频内容敏感，未登记该音色；请换一段干净的录音")
    return voice_id


def _persistent_voice_id(temp_vid: str) -> str:
    """从临时试听 id 派生符合命名规则的持久音色 id。"""
    import random
    suffix = "".join(random.choices("abcdefghijklmnopqrstuvwxyz0123456789", k=10))
    return "pd-" + re.sub(r"[^A-Za-z0-9]", "", temp_vid)[-12:] + suffix


def design_voice(prompt: str, preview_text: str, voice_id: str = "") -> tuple[str, bytes]:
    """音色设计：返回 (voice_id, 试听 mp3 字节)。trial_audio 为 hex 编码。"""
    _ls.log_event(elog, "minimax.voice_design.request", prompt_len=len(prompt),
                  preview_len=len(preview_text))
    r = requests.post(f"{BASE}/v1/voice_design", headers=_headers(),
                      json=build_voice_design_body(prompt, preview_text, voice_id),
                      timeout=TIMEOUT_SYNTH)
    payload = r.json() if "json" in r.headers.get("Content-Type", "") else None
    assert_ok(payload, "MiniMax 音色设计失败")
    vid = (payload or {}).get("voice_id", "")
    hex_audio = (payload or {}).get("trial_audio", "")
    _ls.log_event(elog, "minimax.voice_design.done", voice_id=vid,
                  trial_bytes=len(hex_audio) // 2)

    # 方案 B：ttv-voice 是短期试听音色（数小时后回收）。立刻用试听音频
    # voice_clone 登记为持久音色，返回的持久 id 供长期使用。
    permanent_id = vid
    trial_bytes = bytes.fromhex(hex_audio) if hex_audio else b""
    if trial_bytes:
        permanent_id = _promote(vid, trial_bytes, prompt, preview_text)
        if permanent_id != vid:
            return permanent_id, trial_bytes
        # 转正失败：回退短期 id（不阻塞设计流程），已带日志
    return vid, trial_bytes


def _promote(temp_vid: str, trial_bytes: bytes, prompt: str, preview_text: str) -> str:
    """把短期试听音色转正为长期克隆。试听音频太短时先用该音色合成
    一段 60s 长参考（同音色、时长满足克隆要求），再克隆。"""
    promote_id = _persistent_voice_id(temp_vid)
    import random
    filler = (preview_text + " " + prompt + " ") * 3 +         "这就是我们这一期的全部内容了，感谢你的收听，我们下期再见。"
    ref_candidates = [
        (trial_bytes, "audio/mpeg", "trial.mp3"),
        (t2a_sync(filler[:280], temp_vid), "audio/mpeg", "ref.mp3"),
    ]
    for buf, mime, name in ref_candidates:
        try:
            got = clone_voice(buf, promote_id, mime=mime, filename=name)
            _ls.log_event(elog, "minimax.voice_design.cloned",
                          temp=temp_vid, permanent=got)
            return got
        except Exception as e:
            _ls.log_event(elog, "minimax.voice_design.clone_retry",
                          ref=name, error=str(e)[:160])
    return temp_vid
    if not vid:
        raise MiniMaxError("MiniMax 未返回设计出的 voice_id")
    if not hex_audio:
        raise MiniMaxError("MiniMax 未返回试听音频")
    return vid, bytes.fromhex(hex_audio)


# ---------------- 引擎主入口（与其它引擎同契约） ----------------

def _turn_to_wav(src: str, dst: str) -> None:
    subprocess.run(["ffmpeg", "-y", "-i", src, "-ar", "24000", "-ac", "1", dst],
                   check=True, capture_output=True)


def _probe_duration(wav: str) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", wav],
                           check=True, capture_output=True, text=True)
        return float(r.stdout.strip())
    except Exception:
        return -1.0


def _turn_cache_key(text: str, voice_id: str, seed: int) -> str:
    raw = json.dumps({"t": text, "v": voice_id, "m": MODEL, "e": "minimax", "s": seed},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _resolve_voice(spk: dict) -> str:
    return ((spk.get("engines") or {}).get("minimax") or {}).get("voice_id") \
        or spk.get("minimax_voice_id") or DEFAULT_VOICE


def synthesize(script_dict: dict, out_wav: str,
               on_progress=None, seed: int = 7) -> dict:
    """主入口：系统契约 script dict（数组 speakers，engines.minimax.voice_id 选音色）。"""
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
        vid = _resolve_voice(spk)
        key = _turn_cache_key(text, vid, seed)
        cache_mp3 = os.path.join(CACHE_DIR, f"mx_{key}.mp3")
        cache_wav = os.path.join(CACHE_DIR, f"mx_{key}.wav")
        wav_i = f"{out_wav}.mx{i}.wav"
        if not os.path.exists(cache_wav):
            if not os.path.exists(cache_mp3):
                buf = t2a_sync(text, vid)
                with open(cache_mp3, "wb") as f:
                    f.write(buf)
            _turn_to_wav(cache_mp3, cache_wav)
        import shutil
        shutil.copyfile(cache_wav, wav_i)
        parts.append(wav_i)

    _concat_with_gap(parts, out_wav, 0.3)
    for p in parts:
        try: os.unlink(p)
        except OSError: pass
    return {"wav_path": out_wav, "duration_sec": round(_probe_duration(out_wav), 1),
            "segments": len(turns), "elapsed_sec": round(time.time() - t0, 1),
            "engine": "minimax"}
