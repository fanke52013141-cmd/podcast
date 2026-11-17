"""混音模板 —— 参数照抄 ai-podcast-workflow 实测值（播客平台标准）。

- BGM 音量 0.25，人声 1.0，BGM 前奏 3s / 尾奏 5s
- loudnorm -16 LUFS / -1.5 dBTP（Spotify/小宇宙等平台响度标准）
- 输出 48kHz 立体声 mp3 320k
"""
from __future__ import annotations
import subprocess
from pathlib import Path


def build_command(voice_wav: str, bgm_path: str | None, out_mp3: str,
                  intro_sec: float = 3.0, outro_sec: float = 5.0,
                  bgm_gain: float = 0.25) -> list[str]:
    if not bgm_path:
        return [_ffmpeg_exe(), "-y", "-i", voice_wav,
                "-af", "loudnorm=I=-16:TP=-1.5:LRA=11",
                "-ar", "48000", "-ac", "2", "-b:a", "320k", out_mp3]

    # BGM：sidechain 鸭化（人声出现时自动压低 BGM）+ 首尾淡入淡出
    dur = _duration(voice_wav)
    total = dur + intro_sec + outro_sec
    ms = int(intro_sec * 1000)
    af = (
        f"[0:a]adelay={ms}|{ms},loudnorm=I=-16:TP=-1.5:LRA=11,asplit=2[voice][key];"
        f"[1:a]volume={bgm_gain},atrim=0:{total},afade=t=in:d=2,"
        f"afade=t=out:st={max(0,total-3)}:d=3[bgm];"
        f"[bgm][key]sidechaincompress=threshold=0.03:ratio=6:attack=150:release=800[duck];"
        f"[voice][duck]amix=inputs=2:duration=first:dropout_transition=3[out]"
    )
    return [_ffmpeg_exe(), "-y",
            "-i", voice_wav, "-i", bgm_path,
            "-filter_complex", af, "-map", "[out]",
            "-t", str(total), "-ar", "48000", "-ac", "2",
            "-b:a", "320k", out_mp3]


def run_mix(voice_wav: str, bgm_path: str | None, out_mp3: str) -> str:
    cmd = build_command(voice_wav, bgm_path, out_mp3)
    subprocess.run(cmd, check=True, capture_output=True)
    return out_mp3


def _ffmpeg_exe() -> str:
    """优先 PATH，退化到 imageio-ffmpeg 自带二进制。"""
    import shutil
    if shutil.which("ffmpeg"):
        return "ffmpeg"
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"                                       # 让 subprocess 报原始错误


def _duration(path: str) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                        "format=duration", "-of", "csv=p=0", str(path)],
                       check=True, capture_output=True, text=True)
    return float(r.stdout.strip())
