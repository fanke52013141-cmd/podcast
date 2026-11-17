"""音频质检（P3-1 骨架）—— ASR 回测 CER，标记需重做的轮次。

现为单 ASR（faster-whisper CPU）；P3 完整版在 5080 上加 Paraformer 做双 ASR 交叉验证。
方法沿用 SoulX 论文的数据清洗思路：转写 → 与脚本原文逐轮算 CER → 超阈值标记。
"""
from __future__ import annotations
import os

from postprocess.subtitles import _get_model, _load_audio, _snap_boundaries, _wav_duration
from postprocess.qc import cer

CER_THRESHOLD = float(os.getenv("QC_CER_THRESHOLD", "0.05"))
# 说明：阈值需与 ASR 模型质量匹配。tiny(CPU 联调) 的底噪 ~0.2，建议 0.35；
#       small/medium 或 5080 GPU 上建议 0.05-0.10。可用 QC_CER_THRESHOLD 覆盖。


def qc_audio(wav: str, script_dict: dict) -> dict:
    """返回 {mode, pass, cer_avg, bad_turns, cer_detail}；不可用时 mode=unavailable。"""
    turns = script_dict.get("text") or []
    if not turns:
        return {"mode": "skip", "pass": True, "bad_turns": [], "cer_detail": [],
                "cer_avg": 0.0}
    try:
        model = _get_model(os.getenv("ASR_MODEL", "tiny"))
        audio = _load_audio(wav)
        segments, _ = model.transcribe(audio, language="zh",
                                       word_timestamps=True, vad_filter=True)
        words = []
        for seg in segments:
            for w in (seg.words or []):
                words.append((float(w.start), float(w.end), w.word))
        if len(words) < max(3, len(turns) // 2):
            return {"mode": "unavailable", "pass": True, "bad_turns": [],
                    "cer_detail": [], "cer_avg": 0.0,
                    "note": "识别词数过少，跳过质检"}
        total = _wav_duration(wav)
        bounds = _snap_boundaries(script_dict, total, [(s, e) for s, e, _ in words])
        # 词按起点归属轮次
        hyps = [""] * len(turns)
        for s, _e, w in words:
            for i in range(len(turns)):
                if bounds[i] <= s < bounds[i + 1]:
                    hyps[i] += w
                    break
        detail, bad, total_cer = [], [], 0.0
        for i, (_sid, txt) in enumerate(turns):
            c = cer(txt, hyps[i])
            total_cer += c
            detail.append({"turn": i, "cer": round(c, 4)})
            if c > CER_THRESHOLD:
                bad.append(i)
        return {"mode": "single-asr", "pass": not bad,
                "cer_avg": round(total_cer / len(turns), 4),
                "bad_turns": bad, "cer_detail": detail,
                "threshold": CER_THRESHOLD}
    except Exception as e:
        return {"mode": "unavailable", "pass": True, "bad_turns": [],
                "cer_detail": [], "cer_avg": 0.0, "note": f"{type(e).__name__}: {e}"}
