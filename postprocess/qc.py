"""双 ASR 质检（借鉴 SoulX 论文自己的数据清洗管线反哺推理质检）。

原理：成品 wav 用两个 ASR（Paraformer + Whisper/faster-whisper）各自转写，
与脚本原文对齐算 CER；某轮 CER > 阈值 -> 该轮换 seed 重合成（句级重做，
借鉴 longform-workbench）。
P3 在 5080 机器完整实现；本文件先提供 CER 计算（纯标准库）与接口占位。
"""
from __future__ import annotations
import re

CER_THRESHOLD = 0.05   # 单轮字错误率 >5% 判定需重合成


def cer(ref: str, hyp: str) -> float:
    """字错误率（中文按字，英文按词），动态规划编辑距离。"""
    ref = _norm(ref); hyp = _norm(hyp)
    if not ref:
        return 0.0 if not hyp else 1.0
    dp = list(range(len(hyp) + 1))
    for i, r in enumerate(ref, 1):
        prev = dp[0]; dp[0] = i
        for j, h in enumerate(hyp, 1):
            cur = dp[j]
            dp[j] = min(dp[j] + 1, dp[j-1] + 1, prev + (r != h))
            prev = cur
    return dp[-1] / len(ref)


_STRIP_CHARS = "，。！？、：；“”‘’（）【】《》…—·,.!?;:\"'()[]{}<>|/\\- _\t\n"


def _norm(s: str) -> str:
    s = re.sub(r"<\|?[a-zA-Z_]+\|?>", "", s)          # 去副语言标签
    return s.lower().translate({ord(c): None for c in _STRIP_CHARS})


def review_turns(script_dict: dict, asr_results: dict[str, list[str]],
                 threshold: float = CER_THRESHOLD) -> dict:
    """asr_results: {'paraformer': [每轮转写], 'whisper': [每轮转写]}"""
    turns = script_dict["text"]
    report = {"pass": True, "bad_turns": [], "cer_detail": []}
    for i, (sid, txt) in enumerate(turns):
        cers = [cer(txt, asr_results[k][i]) for k in asr_results if i < len(asr_results[k])]
        avg = sum(cers) / len(cers) if cers else 0.0
        report["cer_detail"].append({"turn": i, "cer": round(avg, 4)})
        if avg > threshold:
            report["bad_turns"].append(i); report["pass"] = False
    return report


def resynthesize_bad_turns(job_id: str, bad_turns: list[int], new_seed: int = 8):
    """句级重做（P3：调 tts_service 只重合成坏轮次，参数保留）。"""
    raise NotImplementedError("P3: 分段缓存 + 单轮重合成接口")
