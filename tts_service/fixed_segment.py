"""VoiceStar 定长片段适配器（P2-7）—— 精确时长 TTS（片头/广告位等强约束片段）。

VoiceStar（jasonppy/VoiceStar，840M，MIT 代码 + CC-BY-4.0 权重）能力：
    --target_duration 秒级精确控制 + 超训练长度外推。
本项目通过「命令模板」或「HTTP 服务」二选一接入（权重较大，未默认下载）：

配置方式 A（命令行，本机或 5080）:
    VOICESTAR_CMD=python /path/to/VoiceStar/inference.py \
        --text "{text}" --target_duration {duration} \
        --ref_audio "{ref}" --output "{out}"
    （{text} {duration} {ref} {out} 为占位符，按需调整脚本参数）

配置方式 B（HTTP）:
    VOICESTAR_URL=http://127.0.0.1:8700   # 期望 POST /synthesize → 返回 {wav_path}

未配置时返回可操作错误信息，不阻塞主链。
"""
from __future__ import annotations
import os
import subprocess
import time
from pathlib import Path

OUT_DIR = Path(__file__).resolve().parent.parent / "outputs" / "fixed"


def available() -> dict:
    return {"cmd": bool(os.getenv("VOICESTAR_CMD")),
            "url": os.getenv("VOICESTAR_URL", "")}


def synthesize_fixed(text: str, target_duration: float,
                     ref_audio: str = "") -> dict:
    """定长语音合成。返回 {wav_path, duration_sec, engine}。"""
    text = (text or "").strip()
    if not text:
        raise ValueError("文本不能为空")
    if not (0.5 <= float(target_duration) <= 120):
        raise ValueError("target_duration 需在 0.5-120 秒之间")

    cmd_t = os.getenv("VOICESTAR_CMD", "").strip()
    url = os.getenv("VOICESTAR_URL", "").strip()

    if url:
        import requests
        r = requests.post(f"{url.rstrip('/')}/synthesize",
                          json={"text": text, "target_duration": target_duration,
                                "ref_audio": ref_audio}, timeout=300)
        r.raise_for_status()
        data = r.json()
        return {"wav_path": data.get("wav_path", ""),
                "duration_sec": data.get("duration_sec", target_duration),
                "engine": "voicestar-http"}

    if cmd_t:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        out = OUT_DIR / f"fixed-{time.strftime('%Y%m%d-%H%M%S')}.wav"
        cmd = (cmd_t.replace("{text}", text.replace('"', "'"))
                    .replace("{duration}", str(float(target_duration)))
                    .replace("{ref}", ref_audio or "")
                    .replace("{out}", str(out)))
        subprocess.run(cmd, shell=True, check=True, capture_output=True)
        if not out.exists():
            raise RuntimeError(f"VoiceStar 命令执行完成但未找到输出文件: {out}")
        return {"wav_path": str(out), "duration_sec": float(target_duration),
                "engine": "voicestar-cmd"}

    raise RuntimeError(
        "VoiceStar 未配置。二选一：\n"
        "1) 设置 VOICESTAR_URL（HTTP 服务地址）\n"
        "2) 设置 VOICESTAR_CMD（命令模板，含 {text} {duration} {ref} {out} 占位符）\n"
        "权重与用法见 https://github.com/jasonppy/VoiceStar（840M，约 3GB）")
