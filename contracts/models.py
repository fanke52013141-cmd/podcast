"""数据契约 —— 全系统只认的三份 JSON（纯标准库，任何环境可跑）。

1) soulx_script.json : 编排层 -> TTS 服务（核心契约，字段与 SoulX 官方
   example/podcast_script/script_mandarin.json 对齐，meta/voice_id 为本系统扩展）
2) voice_manifest.json: 音色库（含人物绑定，保证跨集音色一致）
3) job.json           : 任务簿记（成本埋点/产物路径/QC 结果）
"""
from __future__ import annotations
import json, time, uuid
from dataclasses import dataclass, field, asdict

# ---------------- 1. soulx_script ----------------

ALLOWED_TAGS = ("laughter", "sigh", "breathing", "coughing", "throat_clearing")

@dataclass
class Speaker:
    id: str                      # "S1" / "S2"
    voice_id: str                # 音色库 id，如 "v01_female_warm"
    prompt_audio: str            # 参考音频路径
    prompt_text: str             # 参考音频转写（克隆必需）

    def to_official(self) -> dict:
        """转成 SoulX 官方格式（去掉扩展字段）"""
        return {"id": self.id, "prompt_audio": self.prompt_audio,
                "prompt_text": self.prompt_text}

@dataclass
class SoulXScript:
    job_id: str
    style: str
    target_minutes: int
    speakers: list[Speaker]
    text: list[tuple[str, str]]          # [(说话人id, 内容含副语言标签), ...]

    # ---- 序列化 ----
    def to_dict(self) -> dict:
        return {
            "meta": {"job_id": self.job_id, "style": self.style,
                     "target_minutes": self.target_minutes,
                     "schema": "soulx-script/1"},
            "speakers": [s.to_official() for s in self.speakers],
            "text": [[sid, t] for sid, t in self.text],
        }

    def to_voice_map(self) -> dict:
        """voice_id -> Speaker（TTS 服务解析扩展字段用）"""
        return {s.voice_id: s for s in self.speakers}

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, ensure_ascii=False, indent=2)

    # ---- 统计 ----
    def total_chars(self) -> int:
        return sum(len(t) for _, t in self.text)

    def est_seconds(self) -> float:
        """中文语速 ~4.5 字/秒（标点折半）的粗估"""
        return round(self.total_chars() / 4.5, 1)


# ---------------- 2. voice_manifest ----------------

@dataclass
class VoiceEntry:
    id: str                      # v01_female_warm
    name: str                    # 人设名，如 "小雅"
    gender: str
    age_range: str
    style_tags: list[str]
    language: str = "zh"
    speed_feel: str = "normal"   # slow/normal/fast
    license_status: str = "authorized"   # authorized|public|generated
    source: str = "self_recorded"        # self_recorded|public|voicegen
    prompt_audio: str = ""
    prompt_text: str = ""
    persona_name: str = ""       # 人物绑定："主持人小雅"（跨集一致）
    aliases: list[str] = field(default_factory=list)
    # 第三方引擎登记（PLAN §2.0）：{"minimax": {"voice_id": ...}, "doubao": {...}}
    engines: dict = field(default_factory=dict)


def load_voice_manifest(path: str) -> dict[str, VoiceEntry]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    return {v["id"]: VoiceEntry(**v) for v in raw["voices"]}


# ---------------- 3. job ----------------

@dataclass
class Job:
    topic: str
    style: str
    minutes: int
    job_id: str = field(default_factory=lambda: time.strftime("%Y%m%d-%H%M%S")
                        + "-" + uuid.uuid4().hex[:6])
    status: str = "queued"       # queued|running|script_ready|synthesizing|done|failed
    step: str = ""               # research|draft|rewrite|validate|synthesize|postprocess
    error: str = ""
    provider: str = "api"        # api|mock
    llm_tokens_in: int = 0
    llm_tokens_out: int = 0
    llm_cost_yuan: float = 0.0
    tts_seconds: float = 0.0
    paths: dict = field(default_factory=dict)
    qc: dict = field(default_factory=dict)

    def save(self, path: str) -> None:
        with open(path, "w", encoding="utf-8") as f:
            json.dump(asdict(self), f, ensure_ascii=False, indent=2, default=str)
