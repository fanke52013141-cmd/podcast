"""利落化处理 —— 解决 TTS 说话"黏糊拖沓"的两类来源：

1. 长停顿：轮间/句间的死空气（≥0.6s）压到 0.4s，保留呼吸感去掉拖沓
2. 绵软尾音：SoulX 会忠实模仿参考音的绵软语气（如温暖女声参考 → 拖长尾），
   按音色 speed_feel 做不变调变速（fast→1.06 / normal→1.0 / slow→0.96），
   逐轮处理（轮边界来自词级对齐），atempo 不变调、无音高伪影。

时间轴变化后轮次边界按变速比例解析换算，字幕无需二次对齐。
处理都在混 BGM 之前的裸人声上进行。
"""
from __future__ import annotations
import subprocess

SPEED_BY_FEEL = {"fast": 1.06, "normal": 1.0, "slow": 0.96}


def _ffmpeg() -> str:
    import shutil
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"


def tighten_silence(wav: str, out_wav: str, max_gap: float = 0.6,
                    keep: float = 0.4) -> str:
    """长静音压缩：≥max_gap 的静音截短为 keep；开头静音留 0.25s。"""
    af = (f"silenceremove=start_periods=1:start_threshold=-45dB:start_silence=0.25:"
          f"stop_periods=-1:stop_duration={max_gap}:stop_threshold=-40dB:"
          f"stop_silence={keep}")
    subprocess.run([_ffmpeg(), "-y", "-i", wav, "-af", af,
                    "-ar", "24000", "-ac", "1", out_wav],
                   check=True, capture_output=True)
    return out_wav


def shift_bounds(bounds: list[float], tempos: list[float]) -> list[float]:
    """变速后的轮次边界换算：每轮时长除以 tempo，累计平移。"""
    out, t_src, t_dst = [0.0], 0.0, 0.0
    for i in range(len(tempos)):
        dur = bounds[i + 1] - bounds[i]
        t_src += dur
        t_dst += dur / tempos[i]
        out.append(t_dst)
    return out


def apply_tempo(wav: str, out_wav: str, bounds: list[float],
                tempos: list[float]) -> str:
    """按轮次边界逐轮 atempo（不变调），tempos 与轮次一一对应。全 1.0 时直接复制。"""
    if not tempos or all(abs(t - 1.0) < 1e-3 for t in tempos):
        import shutil
        shutil.copyfile(wav, out_wav)
        return out_wav
    parts, labels = [], []
    n = len(tempos)
    for i, (f, a, b) in enumerate(zip(tempos, bounds[:-1], bounds[1:])):
        if b - a < 0.05:                                 # 空轮保护
            continue
        parts.append(f"[0:a]atrim={a:.3f}:{b:.3f},asetpts=PTS-STARTPTS,"
                     f"atempo={max(0.5, min(2.0, f))}[s{i}]")
        labels.append(f"[s{i}]")
    fc = ";".join(parts) + ";" + "".join(labels) + \
        f"concat=n={len(labels)}:v=0:a=1[out]"
    subprocess.run([_ffmpeg(), "-y", "-i", wav, "-filter_complex", fc,
                    "-map", "[out]", "-ar", "24000", "-ac", "1", out_wav],
                   check=True, capture_output=True)
    return out_wav


def run(wav: str, out_wav: str, script_dict: dict,
        bounds: list[float] | None) -> dict:
    """利落化主入口。bounds 为词级对齐轮次边界（len = 轮数+1）；对齐失败只压静音。

    返回 {"wav": 实际输出路径, "bounds_shifted": 换算后边界或 None, "tempos": ...}
    """
    tighten_silence(wav, out_wav)
    tempos = [1.0] * len(script_dict["text"])
    if bounds and len(bounds) == len(tempos) + 1:
        feel = {s["id"]: SPEED_BY_FEEL.get(s.get("speed_feel", "normal"), 1.0)
                for s in script_dict["speakers"]}
        tempos = [feel.get(sid, 1.0) for sid, _ in script_dict["text"]]
        apply_tempo(out_wav, out_wav + ".tempo.wav", bounds, tempos)
        import os
        os.replace(out_wav + ".tempo.wav", out_wav)
        return {"wav": out_wav, "bounds_shifted": shift_bounds(bounds, tempos),
                "tempos": tempos}
    return {"wav": out_wav, "bounds_shifted": None, "tempos": tempos}
