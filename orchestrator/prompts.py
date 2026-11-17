"""写稿 Skill 的 Prompt —— 两步法（借鉴 Meta NotebookLlama 实测结论：
脚本自然度的最大单项提升来自"先戏剧化改写、再口语化润色"，严禁一步到位或摘要腔）。
供 DeepSeek（主）/ GLM（备）使用，均为 OpenAI 兼容协议。
"""

SYSTEM_EDITOR = (
    "你是一位资深中文播客主编，制作过多档头部谈话节目。"
    "你写的对话蓝本以'像真人聊天'为唯一标准，憎恶 AI 腔和摘要腔。"
)

# ---------- Step A：素材 -> 戏剧化对话蓝本 ----------
STEP_A_USER = """把下面的素材改写成一份播客对话蓝本（纯文本对话，标注 [主持] / [搭档]）。

硬性要求：
1. 不是摘要！是"两个人真的在聊这个话题"——有观点碰撞、有具体例子、允许跑题一句再拉回来
2. 轮次长短不均：有的回合一句话接梗（"对对对，就是这样"），有的回合三段论展开
3. 至少 {n_interruptions} 处自然打断/抢话（用"哎等等——""我先说一句""打断一下"开头）
4. 保留素材里 2-3 个具体数字/细节作为记忆点
5. 结构：开头 30 秒钩子（抛问题/抛反常识结论）→ 中段展开（含一处轻微分歧）→ 结尾收束金句
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

加工规则：
1. 口语化：书面词换成说话方式（"因此"→"所以你看"，"此外"→"还有一点就是"）
2. 语气词自然撒布（嗯/哎/哈/对吧/诶），每分钟 2-4 个，禁止连续堆叠
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


def step_a_prompt(material: str, style: str, minutes: int) -> str:
    s = STYLES.get(style, STYLES["幽默对谈"])
    words = int(minutes * 60 * 4.2)          # 目标字数（4.2字/秒留呼吸感）
    return STEP_A_USER.format(material=material, style_desc=s["desc"],
                              words=words, n_interruptions=s["n_interrupt"])


def step_b_prompt(draft: str, speaker_ids: list[str],
                  s1_desc: str, s2_desc: str,
                  allowed_tags: tuple[str, ...] = ("laughter", "sigh", "breathing",
                                                   "coughing", "throat_clearing")) -> str:
    tags_line = " ".join(f"<|{t}|>" for t in allowed_tags)
    return STEP_B_USER.format(draft=draft, speaker_ids=speaker_ids,
                              s1_desc=s1_desc, s2_desc=s2_desc,
                              allowed_tags=tags_line)
