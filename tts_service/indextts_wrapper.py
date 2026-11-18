"""IndexTTS 2.5 推理封装 —— 通过 ComfyUI HTTP API（BSAI_IndexTTS2.5 节点）调用。

部署形态：ComfyUI（含 IndexTTS2.5 模型 10.97GB）跑在 Windows（端口 8188），
本服务与其同机时直接 localhost 调用；逐轮合成（单说话人模型）+ 300ms 静音拼接。

接口流程（对齐双人播客项目《本地引擎工作流/README.md》约定）：
  POST /input/upload 传参考音频 → POST /prompt 提交工作流
  → GET /history/{id} 轮询 → GET /view 取回 WAV

设计要点（沿用 soulx_wrapper 的工程约定）：
- 逐轮合成：单轮可独立重做；轮级缓存 md5(文本+参考音频+参数)
- 拼接复用 soulx_wrapper._concat_with_gap / _probe_duration（ffmpeg）
- 对 ComfyUI 工作流零侵入：本地构造 prompt，不反向写入其目录
"""
from __future__ import annotations
import hashlib, json, os, subprocess, time, uuid

import requests

COMFY_URL = os.environ.get("COMFY_URL", "http://127.0.0.1:8188")
GAP_SECONDS = 0.3
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tts_out", "turn_cache")
TIMEOUT_PER_TURN = 600          # 单轮合成上限（含排队）

# 对齐《indextts2.5-tts_参考工作流.json》（API 格式）
def _workflow(text: str, ref_name: str, duration_factor: float) -> dict:
    return {
        "1": {"class_type": "BSAI_IndexTTS2.5Loader",
              "inputs": {"use_bf16_使用BF16": True, "device_设备": "auto",
                         "use_qwen_emo_千问情绪模型": False,
                         "force_reload_强制重载": False}},
        "2": {"class_type": "BSAI_IndexTTS2.5Synthesis",
              "inputs": {
                  "tts_model_TTS模型": ["1", 0],
                  "text_文本": text,
                  "reference_audio_参考音频": ["4", 0],
                  "lang_语言": "ZH",
                  "use_emo_text_启用情绪文本": False,
                  "use_random_随机生成": False,
                  "duration_factor_语速因子": duration_factor,
                  "max_text_tokens_per_segment_每段最大文本Token": 100,
                  "max_mel_tokens_最大MelToken": 1500,
                  "temperature_温度": 0.8, "top_p_核采样": 0.8,
                  "top_k_TopK采样": 30, "length_penalty_长度惩罚": 0.0,
                  "num_beams_束宽": 3, "repetition_penalty_重复惩罚": 10.0,
                  "do_sample_采样模式": True, "verbose_详细日志": False}},
        "3": {"class_type": "BSAI_IndexTTS2.5SaveAudio",
              "inputs": {"audio_音频": ["2", 0],
                         "filename_prefix_文件名前缀": "podcast_indextts",
                         "format_格式": "wav", "mp3_bitrate_MP3码率": 192,
                         "output_gain_输出增益": 1.0}},
        "4": {"class_type": "BSAI_IndexTTS2.5LoadAudio",
              "inputs": {"audio_音频": ref_name}},
    }


def _file_hash(path: str) -> str:
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        h.update(path.encode())
    return h.hexdigest()


def turn_key(text: str, ref_path: str, duration_factor: float) -> str:
    raw = json.dumps({"t": text, "r": _file_hash(ref_path),
                      "d": duration_factor}, ensure_ascii=False)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _upload_ref(ref_path: str) -> str:
    """上传参考音频到 ComfyUI input，返回其内部文件名。"""
    name = f"podcast_ref_{_file_hash(ref_path)[:16]}{os.path.splitext(ref_path)[1]}"
    with open(ref_path, "rb") as f:
        r = requests.post(f"{COMFY_URL}/upload/image",
                          files={"image": (name, f)}, timeout=120)
    r.raise_for_status()
    return r.json().get("name", name)


def _synthesize_turn(text: str, ref_path: str, duration_factor: float,
                     out_wav: str) -> str:
    ref_name = _upload_ref(ref_path)
    prompt_id = requests.post(
        f"{COMFY_URL}/prompt",
        json={"prompt": _workflow(text, ref_name, duration_factor),
              "client_id": uuid.uuid4().hex}, timeout=30).json()["prompt_id"]

    t0 = time.time()
    while time.time() - t0 < TIMEOUT_PER_TURN:
        time.sleep(1.0)
        hist = requests.get(f"{COMFY_URL}/history/{prompt_id}", timeout=15).json()
        entry = hist.get(prompt_id)
        if not entry:
            continue
        if entry.get("status", {}).get("status_str") == "error":
            raise RuntimeError(f"ComfyUI 执行失败: {json.dumps(entry.get('status', {}), ensure_ascii=False)[:400]}")
        outputs = entry.get("outputs", {})
        fname = sub = ftype = None
        for node_out in outputs.values():
            for key in ("images", "audio", "gifs", "results"):
                for item in node_out.get(key, []) or []:
                    fname, sub, ftype = item.get("filename"), item.get("subfolder", ""), item.get("type", "output")
                    break
                if fname: break
            if fname: break
        if fname:
            r = requests.get(f"{COMFY_URL}/view",
                             params={"filename": fname, "subfolder": sub, "type": ftype},
                             timeout=60)
            r.raise_for_status()
            with open(out_wav, "wb") as f:
                f.write(r.content)
            return out_wav
    raise TimeoutError(f"ComfyUI 轮询超时（>{TIMEOUT_PER_TURN}s）")


def _concat_with_gap(parts: list[str], out: str, gap_sec: float):
    """按首段采样率生成静音再拼接（IndexTTS 输出 22050Hz，不能复用 24000Hz 的 soulx 拼接）。"""
    if len(parts) == 1:
        os.replace(parts[0], out)
        return
    rate = "22050"
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "stream=sample_rate", "-of", "csv=p=0", parts[0]],
                           check=True, capture_output=True, text=True)
        rate = r.stdout.strip().splitlines()[0] or "22050"
    except Exception:
        pass
    sil = os.path.join(os.path.dirname(out), f"silence_{gap_sec}s_{rate}.wav")
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"anullsrc=r={rate}:cl=mono", "-t", str(gap_sec),
                    "-c:a", "pcm_s16le", sil],
                   check=True, capture_output=True)
    lst = out + ".list.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for i, p in enumerate(parts):
            f.write(f"file '{os.path.abspath(p)}'\n")
            if i < len(parts) - 1:
                f.write(f"file '{os.path.abspath(sil)}'\n")
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", lst,
                    "-c", "copy", out], check=True, capture_output=True)
    os.unlink(lst)


def _probe_duration(wav: str) -> float:
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries",
                            "format=duration", "-of", "csv=p=0", wav],
                           check=True, capture_output=True, text=True)
        return float(r.stdout.strip())
    except Exception:
        return -1.0


def synthesize(script_dict: dict, out_wav: str,
               on_progress=None, seed: int = 7) -> dict:
    """主入口：系统契约 script dict（数组 speakers）-> WAV。

    seed 仅用于轮缓存键隔离（ComfyUI 侧 use_random=false 固定生成）。
    """
    speakers = {s["id"]: s for s in script_dict["speakers"]}
    turns = script_dict["text"]

    os.makedirs(CACHE_DIR, exist_ok=True)
    out_wav = os.path.abspath(out_wav)
    t0 = time.time()
    parts: list[str] = []
    for i, (sid, text) in enumerate(turns):
        if on_progress:
            on_progress(i + 1, len(turns))
        spk = speakers[sid]
        ref = spk["prompt_audio"]
        if not os.path.isabs(ref):                    # 相对路径 → 项目根解析
            root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
            ref = os.path.join(root, ref)
        key = turn_key(text, ref, 1.0)
        cache = os.path.join(CACHE_DIR, f"{key}_{seed}.wav")
        part = f"{out_wav}.turn{i}.wav"
        if os.path.exists(cache):
            import shutil
            shutil.copyfile(cache, part)
        else:
            _synthesize_turn(text, ref, 1.0, part)
            try:
                import shutil
                shutil.copyfile(part, cache)
            except OSError:
                pass
        parts.append(part)

    _concat_with_gap(parts, out_wav, GAP_SECONDS)
    for p in parts[:-1] if len(parts) > 1 else []:
        try: os.unlink(p)
        except OSError: pass
    if parts and len(parts) > 1:
        try: os.unlink(parts[-1])
        except OSError: pass
    return {"wav_path": out_wav, "duration_sec": round(_probe_duration(out_wav), 1),
            "segments": len(turns), "elapsed_sec": round(time.time() - t0, 1),
            "engine": "indextts2.5"}
