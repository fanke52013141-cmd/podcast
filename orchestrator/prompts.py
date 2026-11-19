"""写稿 Skill 的 Prompt —— 两步法（借鉴 Meta NotebookLlama 实测结论：
脚本自然度的最大单项提升来自"先戏剧化改写、再口语化润色"，严禁一步到位或摘要腔）。
供 DeepSeek（主）/ GLM（备）使用，均为 OpenAI 兼容协议。
"""

SYSTEM_EDITOR = (
    "你是一位资深中文播客主编，制作过多档头部谈话节目。"
    "你写的对话蓝本以'像真人聊天'为唯一标准，憎恶 AI 腔和摘要腔。\n\n"
    "## 写稿经验手册（kaikou 实测沉淀，写每一句都要遵守）\n"
    "【人话体验】双字口语词占七八成：'已'→'已经'、'需'→'需要'、'仅'→'只'、'均'→'都'、"
    "'因此'→'所以'、'此外'→'另外'（故意留两三个单字别全换）；"
    "一口气（两个停顿标点之间）不超过 22 个字，长句短句穿插，别写得整整齐齐；"
    "语气词（吧/呢/啊/哈/呀）每千字 3-5 个，只放问句和反应句的句尾，'呃''嗯'这类填充词不写进稿子；"
    "成语俗语一段最多一处且放在结论上，一段最多一个数字。\n"
    "【禁 AI 腔】不用'有两个/只有一个'这类总结框架、'在…的今天'、'随着…的发展'、'日益'、"
    "'至关重要'、'值得注意的是'、'不是…而是…'句式；'最后''总之'只许结尾段用。\n"
    "【口播安全】句尾一律用句号或问号收（逗号收尾合成会吞尾字）；"
    "不用破折号（——）和省略号（……），停顿直接写逗号句号；"
    "英文缩写按读法拆开写（API→A P I、ChatGPT→Chat G P T），"
    "型号数字写成读法（5080→五零八零、5090→五零九零）。\n"
    "【结构规矩】开头 30 秒钩子后，先用 30 秒给'整张地图'（这期讲什么、分几步、听完得到什么）；"
    "每组内容先总后分（先一句话报全貌再展开）；每个要点用一个观众心里会冒出来的问题开头再回答；"
    "每段结尾留一个钩子引出下一段；埋一两个悬念后面揭晓。"
)

# ---------- Step A：素材 -> 戏剧化对话蓝本 ----------
STEP_A_USER = """把下面的素材改写成一份播客对话蓝本（纯文本对话，标注 [主持] / [搭档]）。

硬性要求：
1. 不是摘要！是"两个人真的在聊这个话题"——有观点碰撞、有具体例子、允许跑题一句再拉回来
2. 轮次长短不均：有的回合一句话接梗（"对对对，就是这样"），有的回合三段论展开
3. 至少 {n_interruptions} 处自然打断/抢话（用"哎等等——""我先说一句""打断一下"开头）
4. 保留素材里 2-3 个具体数字/细节作为记忆点
5. 结构（kaikou 整篇规矩）：开头 30 秒钩子后，先用 30 秒给"整张地图"（这期讲什么、
   分几步、听完能得到什么）；每组东西先总后分（先一句话报全貌再展开）；每个要点
   用一个观众心里会冒出来的问题开头再回答；每段结尾留一个钩子引出下一段；
   埋一两个悬念后面揭晓；不堆数字（一段最多一个好记的），每个要点配一个具体例子
6. 总字数约 {words} 字（不含标点）

节目风格：{style_desc}
素材：
---
{material}
---
直接输出对话蓝本，不要任何解释。"""

# ---------- Step B：蓝本 -> SoulX JSON（结构化输出）----------
STEP_B_SYSTEM = (
    "你是脚本格式化引擎。把播客对话蓝本加工成最终可合成脚本，输出严格 JSON。"
    "你只输出 JSON，不输出 markdown 代码块标记以外的任何文字。"
)

STEP_B_USER = """把下面的对话蓝本加工成最终播客脚本 JSON。

加工规则（kaikou 写稿规矩，违反会被稿子体检器打回）：
1. 双字词占七八成：书面单字词全部换双字口语——"已"→"已经"、"需"→"需要"、
   "仅"→"只"、"均"→"都"、"因此"→"所以"、"此外"→"另外"；故意留两三个单字别全换
2. 一口气（两个停顿标点之间）不超过 22 个字；长句短句穿插，别写得整整齐齐
3. 语气词（吧/呢/啊/哈/呀）每千字 3-5 个，只放问句和反应句的句尾；
   "呃""嗯"这类填充词不写进稿子
4. 禁止 AI 高频写法："有两个/只有一个"这类总结框架、"在…的今天"、"随着…的发展"、
   "日益"、"至关重要"、"值得注意的是"、"不是…而是…"句式、"最后""总之"（结尾段除外）
5. 成语/俗语一段最多一处，放在结论上；一段最多一个数字
6. 句尾标点一律用句号或问号（不要逗号收尾——合成模型会把尾字吞掉）
7. 不用破折号（——）和省略号（……），停顿直接用逗号句号
8. 语气词自然撒布（哈/对吧/诶），禁止连续堆叠
3. 富标签（写进文本里，后续系统会自动映射）：
   - 真笑点处写 (laughs)，无奈感慨处写 (sighs)，清嗓写 (clears throat)，
     咳嗽写 (coughs)，深呼吸写 (breathes)，惊讶倒吸气写 (gasp)，
     含糊嘟囔写 (mumbles)，停顿写 (pause)
   - 密度限制：每 5 分钟总标签数 ≤ 6 个，宁少勿多（滥用会产生伪影）
   - 最终会保留的副语言标签：{allowed_tags}（其余标签会被系统剥离）
4. 输出 JSON schema：
{{
  "turns": [
    {{"speaker": "S1", "text": "……(laughs)……"}},
    {{"speaker": "S2", "text": "……"}}
  ]
}}
约束：
- speaker 只能用 {speaker_ids}
- 每轮 text 是一段连贯话（1-4 句），不要把一个字拆成一轮
- S1 = {s1_desc}；S2 = {s2_desc}；人设要贯穿（口头禅/立场一致）

对话蓝本：
---
{draft}
---"""

# ---------- 提示词可编辑（用户需求：写稿提示词暴露到设置页）----------
# 环境变量 PROMPT_SYSTEM_EDITOR / PROMPT_STEP_A / PROMPT_STEP_B 覆盖内置模板。
# 覆盖模板里可用 {material} {style_desc} {words} {n_interruptions} 等占位符，
# 与内置模板一致；留空则用内置。

def _env_override(name: str) -> str | None:
    import os
    v = os.getenv(name, "").strip()
    return v or None


def system_editor() -> str:
    return _env_override("PROMPT_SYSTEM_EDITOR") or SYSTEM_EDITOR


def step_a_user() -> str:
    return _env_override("PROMPT_STEP_A") or STEP_A_USER


def step_b_user() -> str:
    return _env_override("PROMPT_STEP_B") or STEP_B_USER


# ---------- 九种风格模板（借鉴 ai-podcast-workflow 分类）----------
STYLES = {
    "幽默对谈":  {"desc": "轻松搞笑，互相调侃，段子密度高但服务于主题", "n_interrupt": 4},
    "漫才":      {"desc": "一捧一逗，节奏快，吐槽役毒舌但可爱，捧哏接梗密集", "n_interrupt": 6},
    "深夜电台":  {"desc": "语速舒缓，语气亲密，像凌晨两点和朋友聊天，偶尔叹气", "n_interrupt": 2},
    "访谈":      {"desc": "主持人引导提问，嘉宾展开，有追问和确认式回应", "n_interrupt": 2},
    "辩论":      {"desc": "观点对立交锋，抢话多，但保持逻辑与风度", "n_interrupt": 5},
    "实战课堂":  {"desc": "老师讲+学生问，步骤清晰，有复述确认（'你的意思是……'）", "n_interrupt": 3},
    "故事剧场":  {"desc": "叙述与对话交织，有画面感和悬念，旁白式开场", "n_interrupt": 2},
    "纪实探索":  {"desc": "考据式讨论，引用数据严谨，承认不确定性", "n_interrupt": 2},
    "热点解读":  {"desc": "信息密度高，快节奏，金句多，适合通勤收听", "n_interrupt": 4},
}


def step_a_prompt(material: str, style: str, minutes: int,
                  style_desc: str | None = None,
                  n_interruptions: int | None = None) -> str:
    s = STYLES.get(style, STYLES["幽默对谈"])
    desc = (style_desc or "").strip() or s["desc"]
    interrupts = s["n_interrupt"] if n_interruptions is None else int(n_interruptions)
    words = int(minutes * 60 * 4.2)          # 目标字数（4.2字/秒留呼吸感）
    return step_a_user().format(material=material, style_desc=desc,
                                words=words, n_interruptions=interrupts)


def step_b_prompt(draft: str, speaker_ids: list[str],
                  s1_desc: str, s2_desc: str,
                  allowed_tags: tuple[str, ...] = ("laughter", "sigh", "breathing",
                                                   "coughing", "throat_clearing")) -> str:
    tags_line = " ".join(f"<|{t}|>" for t in allowed_tags)
    return step_b_user().format(draft=draft, speaker_ids=speaker_ids,
                                s1_desc=s1_desc, s2_desc=s2_desc,
                                allowed_tags=tags_line)
