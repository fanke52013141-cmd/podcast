"""术语读音表（借鉴 indextts2-studio：'5090'→'五零九零' 式读音修正）。

用法：rewrite 前对脚本做 replace。TTS 模型对中文数字/英文品牌混读不稳，
主动替换成"模型的正确读法文本"。
"""
from __future__ import annotations
import json, os, re

DEFAULT_LEXICON = {
    # 写在稿子里的词 :  替换成（TTS 更容易读对的写法）
    "RTX 5080": "RTX 五零八零",
    "5090": "五零九零",
    "5080": "五零八零",
    "GPT-SoVITS": "GPT So VITS",
    "ChatGPT": "Chat G P T",
    "AGPL": "A G P L",
    "API": "A P I",
    "SoulX": "Soul X",
}

class TermLexicon:
    def __init__(self, extra_path: str | None = None):
        self.mapping = dict(DEFAULT_LEXICON)
        if extra_path and os.path.exists(extra_path):
            with open(extra_path, encoding="utf-8") as f:
                self.mapping.update(json.load(f))

    def apply(self, text: str) -> tuple[str, list[str]]:
        """返回(替换后文本, 日志)。按 key 长度降序避免子串误替换。"""
        log = []
        for k in sorted(self.mapping, key=len, reverse=True):
            if k in text:
                text = text.replace(k, self.mapping[k])
                log.append(f"{k}->{self.mapping[k]}")
        return text, log

    def apply_to_turns(self, turns: list[tuple[str, str]]):
        out = []
        for sid, t in turns:
            new_t, _ = self.apply(t)
            out.append((sid, new_t))
        return out
