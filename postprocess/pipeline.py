"""后期制作管线（P2-4）—— 纯函数：脚本 + 裸人声 wav → 成品交付物。

产物：final.mp3（响度标准化 ±BGM）、subtitles.srt（说话人字幕）
编排层（webapp/jobs.py）负责调用与状态回写；本模块不碰任务状态。
"""
from __future__ import annotations
from pathlib import Path

from postprocess import mix
from postprocess.subtitles import approximate_srt, precise_srt


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

    bgm = pick_bgm(style, explicit=bgm_override) if with_bgm else None
    mix.run_mix(wav_path, bgm, final_mp3)             # loudnorm -16LUFS 恒定生效

    # P2-2：优先 faster-whisper 词级对齐，失败自动降级近似
    srt_text, srt_mode = precise_srt(wav_path, script_dict)
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
