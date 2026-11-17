"""转场音效对位（P2-6）—— "句子锚点 + 句内比例" 算法（借鉴 longform-workbench）。

为什么要锚点而不是绝对时间：
    重新合成/换音色/变速后音频总长会变，绝对时间戳会漂移；
    锚定「第几轮 + 该轮内的相对位置(0-1)」，重合成后自动落到新位置。

锚点示例：{"turn": 3, "ratio": 0.15, "name": "whoosh", "gain_db": -6}
   → 第 3 轮开头 15% 处放一个 whoosh，音量 -6dB
"""
from __future__ import annotations
import subprocess
from pathlib import Path

from postprocess.mix import _ffmpeg_exe, _duration

SFX_DIR = Path(__file__).resolve().parent.parent / "assets" / "sfx"
BUILTIN = {"whoosh": "whoosh.mp3", "ding": "ding.mp3", "pop": "pop.mp3"}


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


def resolve_anchors(script_dict: dict, total: float,
                    anchors: list[dict],
                    bounds: list[float] | None = None) -> list[dict]:
    """把 (turn, ratio) 锚点解析成绝对时间（秒）。
    bounds 传入精确 SRT 边界时按精确时间轴；否则退回字数比例估算。"""
    turns = script_dict["text"]
    n = len(turns)
    if not bounds or len(bounds) != n + 1:
        bounds = _turn_bounds(script_dict, total)
    out = []
    for a in anchors:
        t = int(a.get("turn", 0))
        if not (0 <= t < n):
            continue
        ratio = min(1.0, max(0.0, float(a.get("ratio", 0.0))))
        start, end = bounds[t], bounds[t + 1]
        out.append({**a, "abs_time": round(start + (end - start) * ratio, 3)})
    return out


def sfx_path(name: str) -> Path:
    fname = BUILTIN.get(name, name if name.endswith((".mp3", ".wav")) else "")
    if not fname:
        raise ValueError(f"未知音效: {name}（可用: {list(BUILTIN)}）")
    p = SFX_DIR / fname
    if not p.exists():
        raise ValueError(f"音效文件不存在: {p}")
    return p


def apply_sfx(base_audio: str, resolved: list[dict], out_path: str | Path) -> dict:
    """在 base_audio 上叠加全部音效，输出新文件（不动 base）。"""
    if not resolved:
        raise ValueError("没有音效锚点")
    inputs = [_ffmpeg_exe(), "-y", "-i", base_audio]
    filters = []
    labels = "[0:a]"
    for i, a in enumerate(resolved):
        p = sfx_path(a.get("name", "whoosh"))
        inputs += ["-i", str(p)]
        gain = float(a.get("gain_db", -6))
        delay_ms = int(float(a["abs_time"]) * 1000)
        filters.append(f"[{i+1}:a]volume={gain}dB,adelay={delay_ms}|{delay_ms}[s{i}]")
        labels += f"[s{i}]"
    n_in = len(resolved) + 1
    filters.append(f"{labels}amix=inputs={n_in}:duration=first:dropout_transition=0[out]")
    cmd = inputs + ["-filter_complex", ";".join(filters), "-map", "[out]",
                    "-ar", "48000", "-ac", "2", "-b:a", "320k", str(out_path)]
    try:
        subprocess.run(cmd, check=True, capture_output=True)
    except subprocess.CalledProcessError as e:
        err = e.stderr.decode(errors="ignore")
        tail = "\n".join(err.strip().splitlines()[-4:])
        raise RuntimeError(f"ffmpeg 音效混入失败: {tail}") from e
    return {"out": str(out_path), "duration_sec": round(_duration(str(out_path)), 1),
            "count": len(resolved)}
