"""校验器 + 富标签降级映射（纯标准库，P1 的质量闸门）。

流水线：LLM 富标签输出 --downgrade_tags--> SoulX 5标签 --validate--> 通过/回炉
借鉴：Dia 的标签语法词汇表 + "标签滥用产生伪影"经验（密度限制）。
"""
from __future__ import annotations
import json, os, re
from contracts.models import ALLOWED_TAGS, SoulXScript, Speaker

TAG_RE = re.compile(r"<\|([a-zA-Z_]+)\|>")
RICH_RE = re.compile(r"\((laughs|sighs|clears throat|coughs|breathes|gasp|mumbles|pause)\)")


def allowed_tags_from_env() -> tuple[str, ...]:
    """设置页 PARALING_TAGS 即时生效：逗号分隔，非法值过滤；空/缺失回退全部。"""
    raw = os.getenv("PARALING_TAGS", "")
    tags = tuple(t.strip() for t in raw.split(",") if t.strip() in ALLOWED_TAGS)
    return tags or ALLOWED_TAGS

# Dia 式富标签 -> SoulX 5 标签的降级映射表
#   值为 None 表示 SoulX 无对应标签：按策略改写文本（保留"语气感"而非删除了事）
DOWNGRADE_MAP: dict[str, tuple[str | None, str]] = {
    # rich tag          (SoulX tag | None, 文本改写策略)
    "laughs":         ("<|laughter|>",        "原位替换"),
    "sighs":          ("<|sigh|>",            "原位替换"),
    "clears throat":  ("<|throat_clearing|>", "原位替换"),
    "coughs":         ("<|coughing|>",        "原位替换"),
    "breathes":       ("<|breathing|>",       "原位替换"),
    "gasp":           (None,                  "删除标签,前加'哎——'"),
    "mumbles":        (None,                  "删除标签,文本改'那什么来着……'"),
    "pause":          (None,                  "删除标签,改为'，……，'"),
}

# 每 5 分钟允许的标签密度上限（借鉴 Dia 经验：宁少勿多）
MAX_TAGS_PER_5MIN = 6


def downgrade_rich_tags(text: str) -> tuple[str, list[str]]:
    """把 LLM 富标签降级为 SoulX 兼容形式。返回 (新文本, 动作日志)"""
    log: list[str] = []

    def _sub(m: re.Match) -> str:
        tag = m.group(1)
        soulx_tag, policy = DOWNGRADE_MAP[tag]
        if soulx_tag:
            log.append(f"{tag}->{soulx_tag}")
            return soulx_tag
        log.append(f"{tag} [{policy}]")
        return {"gasp": "哎——", "mumbles": "那什么来着……",
                "pause": "，……，"}.get(tag, "")

    return RICH_RE.sub(_sub, text), log


def validate_script_text(text: list[tuple[str, str]], target_minutes: int,
                         speaker_ids: list[str],
                         allowed_tags: tuple[str, ...] | None = None) -> list[str]:
    """返回错误清单；空列表 = 通过。allowed_tags 缺省读 PARALING_TAGS 设置。"""
    allowed = allowed_tags or allowed_tags_from_env()
    errors: list[str] = []
    all_text = "".join(t for _, t in text)

    # 1) 说话人合法
    for i, (sid, _) in enumerate(text):
        if sid not in speaker_ids:
            errors.append(f"第{i}轮说话人 {sid} 不在 {speaker_ids}")

    # 2) 标签白名单（非白名单一律打回）
    for m in TAG_RE.finditer(all_text):
        if m.group(1) not in allowed:
            errors.append(f"非法标签 <|{m.group(1)}|>（白名单: {allowed}）")

    # 3) 标签密度（宁少勿多，>上限打回让 LLM 删）
    n_tags = len(TAG_RE.findall(all_text))
    max_allowed = max(2, int(target_minutes / 5 * MAX_TAGS_PER_5MIN))
    if n_tags > max_allowed:
        errors.append(f"标签数 {n_tags} 超过密度上限 {max_allowed}（每5分钟≤{MAX_TAGS_PER_5MIN}），请删减到真笑点/真叹气处")

    # 4) 标签必须挂在句中而非句首（句首标签易产生伪影）
    for i, (sid, t) in enumerate(text):
        if t.startswith("<|"):
            errors.append(f"第{i}轮标签在句首，请移到触发词后")

    # 5) 时长偏差（中文 ~4.5 字/秒；±20% 之外打回）
    chars = len(all_text)
    est_min = chars / 4.5 / 60
    lo, hi = target_minutes * 0.8, target_minutes * 1.2
    if not (lo <= est_min <= hi):
        errors.append(f"预计时长 {est_min:.1f}min 偏离目标 {target_minutes}min "
                      f"(±20%)，当前 {chars} 字，目标约 {int(target_minutes*60*4.2)} 字")
    # 6) 空轮次
    for i, (sid, t) in enumerate(text):
        if not t.strip():
            errors.append(f"第{i}轮内容为空")
    return errors


def parse_llm_script(llm_output: str,
                     allowed_tags: tuple[str, ...] | None = None) -> list[tuple[str, str]]:
    """从 LLM 输出稳健提取 turns（容忍 ```json 围栏/前后杂文）。
    allowed_tags 缺省读 PARALING_TAGS：不在白名单的标签自动剥离。"""
    allowed = allowed_tags or allowed_tags_from_env()
    s = llm_output.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", s, re.S)
    if m:
        s = m.group(1)
    else:
        start, end = s.find("{"), s.rfind("}")
        if start >= 0 and end > start:
            s = s[start:end + 1]
    data = json.loads(s)
    turns = data["turns"] if "turns" in data else data["text"]
    out = []
    for t in turns:
        sid, txt = (t["speaker"], t["text"]) if isinstance(t, dict) else (t[0], t[1])
        # 富标签降级在解析时自动完成
        txt, _ = downgrade_rich_tags(str(txt))
        # 白名单外标签剥离（例如用户在设置页关掉了"咳嗽"）
        txt = TAG_RE.sub(lambda mm: mm.group(0) if mm.group(1) in allowed else "", txt)
        out.append((str(sid), txt))
    return out


def build_script(job_id: str, style: str, minutes: int,
                 turns: list[tuple[str, str]],
                 voice_map: dict[str, str],       # speaker_id -> voice_id
                 manifest: dict) -> SoulXScript:
    """组装 SoulXScript（音色路径从 manifest 解析）。"""
    voices = manifest["by_id"]
    speakers = []
    for sid, vid in voice_map.items():
        v = voices[vid]
        speakers.append(Speaker(id=sid, voice_id=vid, prompt_audio=v.prompt_audio,
                                prompt_text=v.prompt_text))
    return SoulXScript(job_id=job_id, style=style, target_minutes=minutes,
                       speakers=speakers, text=turns)
