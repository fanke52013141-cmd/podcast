"""字幕生成 —— 两级精度：

1. precise: faster-whisper 词级时间戳 + 轮次边界吸附（CPU int8 可跑，模型可配 ASR_MODEL）
2. approximate: 按轮次字数比例估时间戳（零依赖兜底）

借鉴 longform-workbench：轮次是"锚"，字幕/音效都按轮次对齐。
"""
from __future__ import annotations
import os
import subprocess

_model_cache: dict[str, object] = {}


def _wav_duration(path: str) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", path],
                           check=True, capture_output=True, text=True)
        return max(0.1, float(r.stdout.strip()))
    except Exception:
        return 0.1


def _get_model(size: str):
    if size not in _model_cache:
        from faster_whisper import WhisperModel
        _model_cache[size] = WhisperModel(size, device=os.getenv("ASR_DEVICE", "cpu"),
                                          compute_type=os.getenv("ASR_COMPUTE", "int8"))
    return _model_cache[size]


def _load_audio(path: str):
    """ffmpeg 解码为 16kHz mono float32 —— 绕开 faster-whisper 的 PyAV 解码路径
    （av 版本兼容问题在这条路上不会出现）。"""
    import numpy as np
    r = subprocess.run(["ffmpeg", "-v", "error", "-i", path,
                        "-f", "s16le", "-ac", "1", "-ar", "16000", "-"],
                       capture_output=True, check=True)
    return np.frombuffer(r.stdout, dtype=np.int16).astype("float32") / 32768.0


def precise_srt(wav: str, script_dict: dict) -> tuple[str, str]:
    """返回 (srt文本, 模式)。任何失败自动降级 approximate。"""
    total = _wav_duration(wav)
    try:
        model = _get_model(os.getenv("ASR_MODEL", "tiny"))
        audio = _load_audio(wav)
        segments, _info = model.transcribe(audio, language="zh",
                                           word_timestamps=True, vad_filter=True)
        words: list[tuple[float, float]] = []
        for seg in segments:
            for w in (seg.words or []):
                words.append((float(w.start), float(w.end)))
        if len(words) < max(3, len(script_dict["text"]) // 2):
            return approximate_srt(script_dict, total), "approx"   # 识别结果过少不可信
        bounds = _snap_boundaries(script_dict, total, words)
        return _srt_from_bounds(script_dict, bounds), "precise"
    except Exception:
        return approximate_srt(script_dict, total), "approx"


def _snap_boundaries(script_dict: dict, total: float,
                     words: list[tuple[float, float]]) -> list[float]:
    """轮次理论边界（按字数比例）吸附到最近的词间隙中心。"""
    turns = script_dict["text"]
    chars = [max(1, len(t)) for _, t in turns]
    n = len(turns)
    theory, acc = [0.0], 0.0
    s = sum(chars)
    for c in chars[:-1]:
        acc += c
        theory.append(total * acc / s)
    theory.append(total)
    gaps: list[float] = []
    for (_, e1), (s2, _) in zip(words, words[1:]):
        if s2 - e1 >= 0.12:                              # 词间停顿 >120ms 视为候选切点
            gaps.append((e1 + s2) / 2)
    snapped = [0.0]
    for t in theory[1:-1]:
        cand = [g for g in gaps if abs(g - t) <= max(2.0, total * 0.12)]
        snapped.append(min(cand, key=lambda g: abs(g - t)) if cand else t)
    snapped.append(total)
    for i in range(1, n + 1):                            # 单调性保护
        snapped[i] = max(snapped[i], snapped[i - 1] + 0.15)
    snapped[-1] = max(snapped[-1], snapped[-2] + 0.15) if n > 1 else total
    return snapped


def bounds_from_srt(srt_path: str) -> list[float] | None:
    """从已生成的 SRT 反解轮次边界（precise 模式下即词级对齐结果）。

    供剪辑/音效对位复用，避免"字幕精确、剪辑估算"两条时间轴。
    """
    import re as _re
    from pathlib import Path as _P
    try:
        text = _P(srt_path).read_text(encoding="utf-8")
    except OSError:
        return None
    starts, end = [], None
    for block in text.strip().split("\n\n"):
        m = _re.search(r"(\d{2}):(\d{2}):(\d{2}),(\d{3}) --> "
                       r"(\d{2}):(\d{2}):(\d{2}),(\d{3})", block)
        if not m:
            continue
        h1, m1, s1, ms1, h2, m2, s2, ms2 = map(int, m.groups())
        starts.append(h1 * 3600 + m1 * 60 + s1 + ms1 / 1000)
        end = h2 * 3600 + m2 * 60 + s2 + ms2 / 1000
    if not starts or end is None:
        return None
    return starts + [end]


def _srt_from_bounds(script_dict: dict, bounds: list[float]) -> str:
    lines = []
    for i, (sid, txt) in enumerate(script_dict["text"]):
        lines.append(f"{i + 1}\n{_ts(bounds[i])} --> {_ts(bounds[i + 1])}\n"
                     f"[{sid}] {txt}\n")
    return "\n".join(lines)


def approximate_srt(script_dict: dict, total_sec: float) -> str:
    """按每轮字数占比切时间轴。approx 模式，误差 ~秒级。"""
    turns = script_dict["text"]
    chars = [max(1, len(t)) for _, t in turns]
    total_chars = sum(chars)
    speakers = {s["id"]: i + 1 for i, s in enumerate(script_dict["speakers"])}

    lines, t = [], 0.0
    for i, ((sid, txt), c) in enumerate(zip(turns, chars), start=1):
        dur = total_sec * c / total_chars
        lines.append(f"{i}\n{_ts(t)} --> {_ts(t + dur)}\n"
                     f"[{script_dict['speakers'][speakers[sid]-1]['id']}] {txt}\n")
        t += dur
    return "\n".join(lines)


def _ts(sec: float) -> str:
    ms = int(sec * 1000)
    h, ms = divmod(ms, 3600000); m, ms = divmod(ms, 60000); s, ms = divmod(ms, 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
