"""剪辑管线（UI-6）—— 波形裁剪 / 单轮音量 / 变速，ffmpeg filter_complex 实现。

思路（借鉴 longform-workbench 的"轮次即锚"）：
先把整段按轮次边界切成 N 段（边界用字数比例估算），逐段应用音量，
concat 后再做全局 trim + atempo。产物 edited.mp3 不覆盖原文件。
"""
from __future__ import annotations
import subprocess
from pathlib import Path

from postprocess.mix import _ffmpeg_exe, _duration


def _turn_bounds(script_dict: dict, total: float) -> list[float]:
    turns = script_dict["text"]
    chars = [max(1, len(t)) for _, t in turns]
    s = sum(chars)
    bounds, acc = [0.0], 0.0
    for c in chars[:-1]:
        acc += c
        bounds.append(total * acc / s)
    bounds.append(total)
    return bounds


def edit(script_dict: dict, wav: str, out_path: str | Path,
         trim_start: float | None = None, trim_end: float | None = None,
         speed: float = 1.0, turn_gains: dict[int, float] | None = None,
         bounds: list[float] | None = None) -> dict:
    """turn_gains: {轮次索引: 增益dB}，如 {0: -6, 3: 3}。
    bounds: 传入精确 SRT 边界时按精确时间轴切分；否则用字数比例估算。"""
    turns = script_dict["text"]
    total = _duration(wav)
    if not bounds or len(bounds) != len(turns) + 1:
        bounds = _turn_bounds(script_dict, total)
    gains = turn_gains or {}

    parts = []
    for i in range(len(turns)):
        g = float(gains.get(i, 0.0))
        parts.append(f"[0:a]atrim={bounds[i]:.3f}:{bounds[i+1]:.3f},"
                     f"volume={g}dB,asetpts=PTS-STARTPTS[a{i}]")
    concat = "".join(f"[a{i}]" for i in range(len(turns))) + \
        f"concat=n={len(turns)}:v=0:a=1[cat]"
    chain = []
    if abs(speed - 1.0) > 1e-3:
        chain.append(f"atempo={min(2.0, max(0.5, speed)):.3f}")
    if trim_start is not None or trim_end is not None:
        ts = max(0.0, trim_start or 0.0)
        te = trim_end if trim_end is not None else total
        chain.append(f"atrim={ts:.3f}:{te:.3f},asetpts=PTS-STARTPTS")
    tail = "[cat]" + (",".join(chain) if chain else "anull") + "[out]"

    cmd = [_ffmpeg_exe(), "-y", "-i", wav,
           "-filter_complex", ";".join(parts + [concat, tail]),
           "-map", "[out]", "-ar", "48000", "-ac", "2", "-b:a", "320k", str(out_path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="ignore")
        tail = "\n".join(err.strip().splitlines()[-4:])
        raise RuntimeError(f"ffmpeg 剪辑失败: {tail}") from e

    dur = _duration(str(out_path))
    return {"edited": str(out_path), "duration_sec": round(dur, 1),
            "speed": speed, "turn_gains": {str(k): v for k, v in gains.items()},
            "trim": [trim_start, trim_end]}
