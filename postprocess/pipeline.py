"""后期制作管线（P2-4）—— 纯函数：脚本 + 裸人声 wav → 成品交付物。

产物：final.mp3（响度标准化 ±BGM）、subtitles.srt（说话人字幕）
编排层（webapp/jobs.py）负责调用与状态回写；本模块不碰任务状态。
"""
from __future__ import annotations
from pathlib import Path

from postprocess import mix, crisp
from postprocess.subtitles import (approximate_srt, align_bounds,
                                   _srt_from_bounds, _wav_duration)


def pick_bgm(style: str, bgm_dir: str | Path | None = None,
             explicit: str | None = None) -> str | None:
    """按风格挑 BGM；explicit（模板指定文件名）优先；目录空/缺失返回 None。"""
    d = Path(bgm_dir) if bgm_dir else Path(__file__).resolve().parent.parent / "assets" / "bgm"
    if explicit:
        p = d / explicit
        if p.exists():
            return str(p)
    if not d.exists():
        return None
    audio = sorted(list(d.glob("*.mp3")) + list(d.glob("*.wav")) + list(d.glob("*.m4a")))
    if not audio:
        return None
    for a in audio:                                   # 文件名含风格关键词优先
        if style and style in a.stem:
            return str(a)
    return str(audio[0])


def run(script_dict: dict, wav_path: str, out_dir: str | Path,
        style: str = "", with_bgm: bool = True,
        bgm_override: str | None = None) -> dict:
    """执行后期链，返回产物清单。任何一步失败抛异常，由编排层决定降级。"""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    final_mp3 = str(out / "final.mp3")
    srt_path = str(out / "subtitles.srt")

    # 利落化（crisp）：压长停顿 + 按音色 speed_feel 逐轮不变调变速，
    # 解决 SoulX 模仿绵软参考音导致的"尾音黏糊、节奏拖沓"
    voice_wav = str(out / "voice_crisp.wav")
    srt_mode = "approx"
    try:
        bounds = align_bounds(wav_path, script_dict)
        res = crisp.run(wav_path, voice_wav, script_dict, bounds)
        voice_wav = res["wav"]
        if res["bounds_shifted"]:
            srt_text = _srt_from_bounds(script_dict, res["bounds_shifted"])
            srt_mode = "precise"
        else:
            srt_text = approximate_srt(script_dict, _wav_duration(voice_wav))
    except Exception:
        voice_wav = wav_path                          # 利落化失败不阻塞主链
        srt_text = approximate_srt(script_dict, _wav_duration(voice_wav))

    bgm = pick_bgm(style, explicit=bgm_override) if with_bgm else None
    mix.run_mix(voice_wav, bgm, final_mp3)            # loudnorm -16LUFS 恒定生效

    Path(srt_path).write_text(srt_text, encoding="utf-8")
    vtt_path = str(out / "subtitles.vtt")
    import re as _re
    Path(vtt_path).write_text(
        "WEBVTT\n\n" + _re.sub(r"(\d{2}:\d{2}:\d{2}),(\d{3})", r"\1.\2", srt_text),
        encoding="utf-8")

    duration = mix._duration(final_mp3)
    return {"final_mp3": final_mp3, "srt": srt_path, "vtt": vtt_path,
            "duration_sec": round(duration, 1), "bgm_used": bool(bgm),
            "srt_mode": srt_mode}
