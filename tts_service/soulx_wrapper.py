"""SoulX-Podcast 推理封装 —— 只在 5080 机器上运行。

设计要点（借鉴 indextts2-studio 的工程实践）：
- 模型进程内常驻（懒加载一次），不做每次加载
- 官方 cli/podcast.py 为入口，本模块生成临时 JSON 后以子进程调用 —— 对 SoulX
  官方仓库零侵入（借鉴 longform-workbench "补丁不侵入宿主"）
- 长稿按 5-8 分钟章节切段（对齐 SoulX 训练分布），段间 300ms 静音
- 显存纪律：本服务独占 GPU；音色工厂(MOSS-VoiceGenerator)等服务分时复用
- TODO(P0后): 审计 SoulX 推理链是否漏 torch.no_grad/inference_mode
  （indextts2-studio 实测修复后 RTF 11.87->1.31）
- TODO(P0后): 参考音频 md5 特征缓存（同一音色第二次起跳过特征提取）
"""
from __future__ import annotations
import hashlib, json, math, os, subprocess, sys, tempfile, time

REPO_DIR = os.environ.get("SOULX_REPO_DIR", "SoulX-Podcast")
MODEL_PATH = os.environ.get("SOULX_MODEL_PATH",
                            "SoulX-Podcast/pretrained_models/SoulX-Podcast-1.7B")
SEGMENT_SECONDS = 420          # 7 分钟/段（5-8min 区间取中）
GAP_SECONDS = 0.3
CACHE_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                         "tts_out", "seg_cache")


def _file_hash(path: str) -> str:
    """参考音频内容哈希（音色固定 → 命中 100%，借鉴 indextts2-studio md5 缓存）。"""
    h = hashlib.md5()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(1 << 20), b""):
                h.update(chunk)
    except OSError:
        h.update(path.encode())
    return h.hexdigest()


def segment_key(payload: dict, seed: int) -> str:
    """段缓存键 = 文本 + 说话人参考音频哈希 + seed。"""
    canon = []
    for sid, text in payload.get("text", []):
        canon.append([sid, text])
    spk = {s["id"]: _file_hash(s["prompt_audio"]) for s in payload.get("speakers", [])}
    raw = json.dumps({"text": canon, "speakers": spk, "seed": seed},
                     ensure_ascii=False, sort_keys=True)
    return hashlib.md5(raw.encode("utf-8")).hexdigest()


def _split_turns_into_segments(turns: list, chars_per_sec: float = 4.5) -> list[list]:
    """把对话轮次按目标时长切成多段（段边界尽量落在说话人切换处）。"""
    target_chars = SEGMENT_SECONDS * chars_per_sec
    segs, cur, acc = [], [], 0
    for t in turns:
        cur.append(t); acc += len(t[1])
        if acc >= target_chars:
            segs.append(cur); cur, acc = [], 0
    if cur:
        segs.append(cur)
    return segs or [[]]


def synthesize(script_dict: dict, out_wav: str,
               on_progress=None, seed: int = 7) -> dict:
    """主入口：官方格式 script dict -> 24kHz wav。返回元信息。"""
    from contracts.models import ALLOWED_TAGS  # noqa: F401  (导入自检契约可用)
    turns = script_dict["text"]
    speakers = {s["id"]: s for s in script_dict["speakers"]}

    segs = _split_turns_into_segments(turns)
    t0 = time.time()
    wavs = []
    for i, seg in enumerate(segs):
        if on_progress:
            on_progress(i + 1, len(segs))
        payload = {
            "speakers": [speakers[sid] for sid in dict.fromkeys(s for s, _ in seg)
                         if sid in speakers],
            "text": [[s, t] for s, t in seg],
        }
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False,
                                         encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            tmp_json = f.name
        seg_wav = f"{out_wav}.seg{i}.wav"
        key = segment_key(payload, seed)
        cache_path = os.path.join(CACHE_DIR, key + ".wav")
        if os.path.exists(cache_path):              # P1-12 段缓存：已合成段秒续
            import shutil
            shutil.copyfile(cache_path, seg_wav)
            wavs.append(seg_wav)
            os.unlink(tmp_json)
            continue
        cmd = [sys.executable, os.path.join(REPO_DIR, "cli", "podcast.py"),
               "--json_path", tmp_json, "--model_path", MODEL_PATH,
               "--output_path", seg_wav, "--seed", str(seed)]
        subprocess.run(cmd, check=True, cwd=REPO_DIR,
                       stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
        os.makedirs(CACHE_DIR, exist_ok=True)
        try:                                        # 命中失败不影响主流程
            import shutil
            shutil.copyfile(seg_wav, cache_path)
        except OSError:
            pass
        wavs.append(seg_wav)
        os.unlink(tmp_json)

    _concat_with_gap(wavs, out_wav, GAP_SECONDS)
    duration = _probe_duration(out_wav)
    return {"wav_path": out_wav, "duration_sec": round(duration, 1),
            "segments": len(segs), "elapsed_sec": round(time.time() - t0, 1)}


def _concat_with_gap(parts: list[str], out: str, gap_sec: float):
    """ffmpeg 拼接 + 段间静音。"""
    if len(parts) == 1:
        os.replace(parts[0], out); return
    lst = out + ".list.txt"
    with open(lst, "w", encoding="utf-8") as f:
        for p in parts:
            f.write(f"file '{os.path.abspath(p)}'\n")
            f.write(f"file 'silence_{gap_sec}s.wav'\n" if p != parts[-1] else "")
    # 生成静音文件（24kHz 单声道，与 SoulX 输出对齐）
    subprocess.run(["ffmpeg", "-y", "-f", "lavfi", "-i",
                    f"anullsrc=r=24000:cl=mono", "-t", str(gap_sec),
                    "-q:a", "9", f"silence_{gap_sec}s.wav"],
                   check=True, capture_output=True)
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
