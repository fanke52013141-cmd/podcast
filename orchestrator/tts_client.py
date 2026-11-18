"""TTS HTTP 客户端 —— 编排层 → 5080 机器 tts_service。

- synthesize(): 普通版；synthesize_stream(): SSE 进度版（分段合成时逐步上报）
- 失败自动重试 1 次；超时按分钟数动态放大（合成 10min 音频可能要几分钟）
"""
from __future__ import annotations
import json
import requests


class TTSClient:
    def __init__(self, base_url: str, timeout_connect: int = 10):
        self.base = base_url.rstrip("/")
        self.timeout_connect = timeout_connect

    # ---------- 状态 ----------
    def health(self) -> dict:
        return self._get("/health")

    def voices(self) -> list[dict]:
        return self._get("/voices")

    # ---------- 合成 ----------
    def synthesize(self, script_dict: dict, on_progress=None, engine: str = "") -> dict:
        """SSE 优先（有进度），服务端不支持时自动降级普通版。engine 可选覆盖服务端默认。"""
        try:
            return self._synthesize_sse(script_dict, on_progress, engine)
        except requests.HTTPError:
            raise
        except Exception:
            return self._synthesize_plain(script_dict, engine)

    def _synthesize_plain(self, script_dict: dict, engine: str = "") -> dict:
        minutes = script_dict.get("meta", {}).get("target_minutes", 10)
        payload = {"script": script_dict}
        if engine:
            payload["engine"] = engine
        r = requests.post(f"{self.base}/synthesize", json=payload,
                          timeout=self.timeout_connect + minutes * 300)
        r.raise_for_status()
        return r.json()

    def _synthesize_sse(self, script_dict: dict, on_progress=None, engine: str = "") -> dict:
        minutes = script_dict.get("meta", {}).get("target_minutes", 10)
        payload = {"script": script_dict}
        if engine:
            payload["engine"] = engine
        result = None
        with requests.post(f"{self.base}/synthesize/sse",
                           json=payload,
                           stream=True,
                           timeout=(self.timeout_connect, minutes * 300)) as r:
            r.raise_for_status()
            for line in r.iter_lines(decode_unicode=True):
                if not line or not line.startswith("data:"):
                    continue                      # 跳过 keepalive 注释行
                evt = json.loads(line[len("data:"):].strip())
                if evt.get("step") == "done":
                    result = evt
                    break
                if on_progress:
                    on_progress(evt)
        if result is None:
            raise RuntimeError("SSE 流提前结束，未收到 done 事件")
        return result

    # ---------- 内部 ----------
    def _get(self, path: str):
        r = requests.get(f"{self.base}{path}", timeout=self.timeout_connect)
        r.raise_for_status()
        return r.json()


def print_progress(evt: dict) -> None:
    """CLI 进度打印（分段合成示例: segment 2/5）。"""
    if evt.get("step") == "synthesizing":
        print(f"\r  [TTS] 段 {evt['segment']}/{evt['total']} ...", end="", flush=True)
    elif evt.get("step") == "done":
        d = evt.get("duration_sec", "?")
        print(f"\n  [TTS] 完成: {evt.get('wav_path')} ({d}s, "
              f"耗时 {evt.get('total_sec', '?')}s, {evt.get('segments', 1)} 段)")
