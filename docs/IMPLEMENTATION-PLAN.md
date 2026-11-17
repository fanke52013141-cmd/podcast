# AI 播客生成系统 · 最终实施方案 v1.0

> 定案：**LLM 走 API ｜ Agent 编排用 LangChain(LangGraph) ｜ TTS 用 SoulX-Podcast 本地部署（RTX 5080）｜ 后期链本地 CPU**
> 前置文档：《AI播客开源项目调研报告》《SoulX-Podcast基底融合方案分析》《RTX5080-SoulX本地方案》
> 本文档 = 可直接开工的工程方案：架构、模块设计、数据契约、代码骨架、开源借鉴清单、实施计划

---

## 1. 系统总览

```
用户输入（主题 / PDF / URL / 文档）
        │
        ▼
┌─────────────────────────────────────────────────────────────┐
│  编排层：LangGraph Agent（跑在一台普通服务器/本机即可，无 GPU）   │
│                                                              │
│   [research]──[draft]──[rewrite]──[validate]──┐              │
│     抽取素材    戏剧化    口语化+     schema校验 │不通过→回rewrite │
│                改写      富标签嵌入    ↓白名单   │ (最多3次)     │
│                                              ▼              │
│                                        soulx_script.json     │
└──────────────────────┬──────────────────────────────────────┘
                       │ HTTP（内网）
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  合成层：SoulX TTS 服务（RTX 5080, WSL2, FastAPI 包装）         │
│   SoulX-Podcast-1.7B (hf 后端, bf16, ~8-10GB) + 本地音色库      │
└──────────────────────┬──────────────────────────────────────┘
                       │ 24kHz WAV
                       ▼
┌─────────────────────────────────────────────────────────────┐
│  后期层：ffmpeg 混音 · WhisperX 字幕 · AudioSeal 水印 · 质检闭环  │
└──────────────────────┬──────────────────────────────────────┘
                       ▼
              成品（48kHz mp3 + SRT/VTT + 元数据）
```

**一句话流程**：Agent 调 LLM API 写出带副语言标签的对话稿（严格 JSON 契约）→ 发给 5080 上的 SoulX 服务整段合成 → ffmpeg/WhisperX 后期 → 成品。

---

## 2. 技术选型定案 + 一个重要澄清

### 2.1 选型表

| 层 | 选型 | 定案理由 |
|---|---|---|
| 写稿 LLM | **API**：主 GLM-4.x / DeepSeek-V3，备 Qwen-Max（LangChain `ChatOpenAI` 换 `base_url` 即可切换） | SoulX 未训练任何脚本 LLM；API 质量高、每集几分钱 |
| Agent 框架 | **LangChain + LangGraph** | 状态机式流水线（而非自由 ReAct），步骤可控、可重试、可断点续跑 |
| TTS | **SoulX-Podcast-1.7B 本地**（唯一本地大模型） | 它全部价值在权重里（130万小时训练），无 API 替代 |
| 字幕 | WhisperX（GPU int8 分时） | 词级时间戳 + 说话人对齐 |
| 混音 | ffmpeg | 参数照抄 ai-podcast-workflow 实测值 |
| 水印 | AudioSeal | 商用合规 |
| 观测 | LangSmith（或 Langfuse 自托管） | 每次 写稿→成品 全链路追踪，A/B prompt 必备 |

### 2.2 ⚠️ 澄清：LangChain 没有 Claude 式的 "SKILL.md" 机制

LangChain 生态里和"skill"对应的是三件东西的组合，我们的"写稿 skill"就按这个拆：

| 你想要的概念 | LangChain 里的对应物 | 我们怎么用 |
|---|---|---|
| skill（一套固定打法） | **LangGraph 子图（subgraph）** + Prompt 模板 + 结构化输出 | `ScriptWriterSkill` = 一个可复用子图：research→draft→rewrite→validate |
| skill 里的规范约束 | **Pydantic 结构化输出**（`with_structured_output`）+ 白名单校验器 | 5 个副语言标签白名单、说话人 schema 强校验 |
| 工具调用 | **Tool**（`@tool` 装饰器） | TTS 服务、音色库查询、质检都封装成 Tool |

好消息正如你说的：**我们流程不复杂，LangGraph 的有向图完全够用**，不需要更重的框架（pipecat/TEN 是为实时语音对话设计的，留给未来交互版，见 §8）。

---

## 3. 编排层详细设计（LangGraph）

### 3.1 状态机

```python
# orchestrator/graph.py 骨架
from langgraph.graph import StateGraph, END
from pydantic import BaseModel

class PodcastState(BaseModel):
    job_id: str
    source_type: str            # topic | pdf | url | doc
    source_content: str         # 抽取后的素材文本
    style: str                  # 九种风格之一，见 §3.2
    target_minutes: int
    voice_ids: list[str]        # 引用音色库
    draft_script: dict | None   # SoulX JSON（契约见 §5.1）
    validate_errors: list[str]
    retry_count: int = 0
    wav_path: str | None
    subtitle_path: str | None
    final_path: str | None

def build_graph():
    g = StateGraph(PodcastState)
    g.add_node("research", research_node)    # 素材抽取(trafilatura/pymupdf)
    g.add_node("draft",     draft_node)      # Step A: 戏剧化改写
    g.add_node("rewrite",   rewrite_node)    # Step B: 口语化+富标签+JSON 化
    g.add_node("validate",  validate_node)   # schema+白名单校验
    g.add_node("synthesize", synthesize_node) # 调本地 TTS Tool
    g.add_node("postprocess", post_node)      # 混音/字幕/水印 Tool
    g.add_node("qc",        qc_node)          # 双ASR质检，失败句重生成

    g.add_edge("research", "draft")
    g.add_edge("draft", "rewrite")
    g.add_edge("rewrite", "validate")
    g.add_conditional_edges("validate",
        lambda s: "rewrite" if (s.validate_errors and s.retry_count < 3) else "synthesize")
    g.add_edge("synthesize", "postprocess")
    g.add_edge("postprocess", "qc")
    g.add_conditional_edges("qc", lambda s: END if s.final_path else "synthesize")
    return g.compile()
```

### 3.2 写稿 Skill 的两步 Prompt（借鉴 NotebookLlama，这是拟真度第一杠杆）

**Step A · 戏剧化改写**（严禁摘要腔）：

```
你是一位资深播客主编。把下面的素材改写成播客对话蓝本。
要求：
1. 不是摘要！是"两个人真的在聊这个话题"——有观点碰撞、有例子、有跑题又拉回来
2. 轮次长短不均：有的回合一句话接梗，有的回合三段论展开
3. 至少 3 处自然打断/抢话（用"哎等等——""我先说一句"开头）
4. 保留 2-3 个具体数字/细节做记忆点
5. 节奏：开头 30 秒钩子 → 中段展开 → 结尾一个收束金句
风格：{style}；时长：{minutes} 分钟（约 {words} 字）
```

**Step B · 口语化 + 富标签 + 结构化输出**（`with_structured_output`，schema 见 §5.1）：

```
把蓝本加工成最终播客脚本 JSON：
1. 口语化：书面词换成说话的方式（"因此"→"所以你看"，"此外"→"还有一点"）
2. 语气词自然撒布（嗯/哎/哈/对吧），每分钟 2-4 个，不许堆
3. 副语言标签（只能用这 5 个，插在触发词后）：
   <|laughter|> <|sigh|> <|breathing|> <|coughing|> <|throat_clearing|>
   规则：笑仅用于真笑点；叹气用于无奈/感慨；每 5 分钟不超过 3 个标签
4. LLM 富标签降级映射（借鉴 Dia 词汇表，由校验器自动执行）：
   (gasp)→删除+前加"哎"；(mumbles)→文本改"那什么来着"；(pause)→"，……，"
5. 输出严格符合 schema：speakers 引用给定音色 id；text 数组 [说话人, 内容]
```

**9 种风格模板**（直接抄 ai-podcast-workflow 的分类）：幽默对谈 / 漫才 / 深夜电台 / 访谈 / 辩论 / 实战课堂 / 故事剧场 / 纪实探索 / 热点解读——每种一个 system prompt 变体（人设、语速感、BGM 建议）。

### 3.3 校验器（validate_node，纯代码不耗 token）

1. jsonschema 校验结构；
2. 白名单：正则抓所有 `<|...|>`，非 5 标签即报错；
3. 富标签降级映射（§3.2 第 4 条）在 rewrite 输出后自动执行；
4. 说话人 id ∈ 音色库 manifest；
5. 字数/时长估算（中文语速 ~4.5 字/秒）偏差 >20% 报错；
6. 错误清单回传 rewrite（附上具体错误，模型自我修正）。

---

## 4. 合成层详细设计（5080 上的 TTS 服务）

### 4.1 部署形态

- WSL2 Ubuntu 24.04 + torch 2.7.1+**cu128**（5080 是 sm_120，cu126 会报 no kernel image）；
- `llm_engine=hf` 起步（魔改 vLLM 留作 Phase 3 提速项）；
- **FastAPI 薄包装**（~100 行），模型常驻显存，Agent 通过 HTTP 调用：

```
POST /synthesize
{ "script": <soulx_script.json>, "voice_overrides": {}, "seed": 7 }
→ { "wav_path": "/data/out/{job_id}.wav", "duration_sec": 612.4, "peak_vram_gb": 9.2 }

GET  /health → { "model": "soulx-1.7b", "vram_free": 6.1 }
GET  /voices → 音色库列表
```

### 4.2 长稿分段策略（借鉴 MOSS-TTSD / SoulX 论文训练分布）

- SoulX 训练时对话段落约 5 分钟粒度 → 我们**按 5-8 分钟章节切段推理**，段间插 300ms 静音 + BGM 起伏遮缝；
- 好处：显存平稳、失败只重跑单段（QC 重生成也按段）；
- 注意：每段推理时携带该说话人的 `prompt_audio`（音色锚定每段重复注入，防止长程漂移）。

### 4.3 音色库设计

```
lib/voices/
  manifest.json        # id/名称/性别/语言/风格标签/授权状态
  v01_female_warm.wav  # 5-10s 参考音频（24kHz 单声道）
  v01.prompt_text.txt  # 参考音频的准确转写（克隆必需）
```
- 来源三档：① 自录授权声（最稳）② 公开许可声线 ③ Phase 3 用 MOSS-VoiceGenerator 分时造（造完存库，平时不加载）；
- 建议初始 6-8 个音色（3 女 3 男 + 2 特色），覆盖九种风格的人设需求。

---

## 5. 数据契约（三个 JSON，全系统只认这三份文件）

### 5.1 `soulx_script.json`（Agent→TTS 核心契约）

```json
{
  "meta": { "job_id": "ep001", "style": "深夜电台", "target_minutes": 10 },
  "speakers": [
    { "id": "S1", "voice_id": "v01_female_warm",
      "prompt_audio": "lib/voices/v01_female_warm.wav",
      "prompt_text": "参考音频的转写文本……" },
    { "id": "S2", "voice_id": "v02_male_deep", "prompt_audio": "…", "prompt_text": "…" }
  ],
  "text": [
    ["S1", "哈喽，欢迎回到午夜电波<|laughter|>，今天我们聊一个……"],
    ["S2", "对，这个话题我憋很久了，哎等等——我先说个背景……"],
    ["S1", "嗯……<|sigh|>，你这么一说我想起我自己的经历……"]
  ]
}
```
（格式与 SoulX 官方 `example/podcast_script/script_mandarin.json` 逐字段对齐，`voice_id` 是我们的扩展字段，TTS 服务负责解析成 `prompt_audio`。）

### 5.2 `voice_manifest.json`（音色库） / 5.3 `job.json`（任务与产物追踪，含成本埋点）

job.json 记录每次运行的 LLM token 用量/费用、TTS 耗时、QC 结果——**成本透明化直接抄 ai-podcast-workflow 的做法**（它把每步成本实时打到终端，用户感知极好）。

---

## 6. 开源项目借鉴清单（按模块落位，含 star 数）

| # | 借鉴对象 | Stars | 借鉴什么（具体到参数/做法） | 落位 |
|---|---|---|---|---|
| 1 | **NotebookLlama**(Meta) | cookbook | 两步改写法：先"戏剧化改写"再"润色语气"——Meta 实测这是脚本自然度最大单项提升 | §3.2 写稿 skill 核心 |
| 2 | **ai-podcast-workflow** | 62（价值>star） | ① 混音参数：BGM 0.25/人声 1.0、`loudnorm=-16LUFS:TP=-1.5`、前奏3s尾奏5s ② 九种风格模板 ③ 成本埋点 ④ 音色配置文件结构 ⑤ 按轮次断句出字幕 | 后期链 + 风格库 |
| 3 | **Podever**(MOSS 生态) | — | PDF/URL/长文本→对话稿的抽取管线参考（trafilatura 抽正文 + 分块投喂） | research_node |
| 4 | **Dia** | 19.4k | 富标签词汇表 `(laughs)(gasp)(mumbles)(pause)` + "标签滥用出伪影"经验 → 我们的降级映射表 | rewrite + validate |
| 5 | **MOSS-TTSD** | 1.4k | ① 分段生成策略（~5min 段）② Doubao_Podcast 式客观评测集思路 ③ SGLang 部署经验（Phase 3 提速备选） | TTS 服务 + QC |
| 6 | **SoulX 论文数据管线**（反哺） | — | **双 ASR 质检**：成品用 Paraformer+Whisper 各转一遍，CER>5% 句子自动重生成（换 seed）——论文清洗训练数据的方法直接用于推理质检 | qc_node |
| 7 | **WhisperX** | 25.4k | 词级时间戳 + 说话人 diarization → SRT/VTT 说话人字幕 | post_node |
| 8 | **FunASR/SenseVoice** | 21k/9.8k | 中文 ASR 质检主力（比 whisper 中文准），Paraformer 即出自此处 | qc_node |
| 9 | **MOSS-VoiceGenerator** | 家族4.3k | 文字描述造音色（"30岁男声，低沉沙哑"）→ 音色工厂，分时加载不挤显存 | Phase 3 音色库 |
| 10 | **AudioSeal**(Meta) | 803 | AI 语音水印嵌入 + 检测，商用合规必备 | post_node |
| 11 | **GPT-SoVITS** | 65.2k | 音色库素材精修：少量目标人声→高质量参考音频（给 SoulX 当 prompt_audio 用；注意商用授权） | 音色库建设（可选） |
| 12 | **ai-podcast-workflow 封面 + Seedream** | — | 封面提示词同轮生成（脚本生成时顺带出封面 prompt），接任意文生图 API | 加分项 |
| 13 | **pipecat / TEN** | 17.3k/11.2k | **不现在用**——它们是实时语音对话框架；留作未来"可交互播客"扩展（见 §8） | 未来 |
| 14 | **LangSmith/Langfuse** | — | 全链路 trace + prompt 版本 A/B；Langfuse 可自托管免数据外流 | 观测 |

**不借鉴的（明确排除）**：ChatTTS（过时）、IndexTTS-2（许可证卡死，仅吸收"音色-情绪解耦"思想进未来微调）、VibeVoice（官方已删 TTS 代码）、coqui TTS（归档）。

---

## 7. 实施计划（更新版）

| 阶段 | 工期 | 内容 | 验收 |
|---|---|---|---|
| **P0 环境验证** | 1-2 天 | WSL2 + cu128 + SoulX 官方示例跑通（hf 后端）；实测显存峰值/RTF | outputs wav 正常；记录 `peak_vram`、每分钟音频生成耗时 |
| **P1 最小管线** | 1 周 | LangGraph 四节点（research/draft/rewrite/validate）+ FastAPI TTS 服务 + 两步 prompt + 校验器 | 一条命令：主题→10 分钟双人中文播客 WAV；盲测 MOS≥4.0 |
| **P2 后期链** | 1-2 周 | ffmpeg 混音模板、WhisperX 字幕、分段生成、AudioSeal、job 成本埋点、Langfuse 接入 | 成品含 BGM/字幕/水印/元数据；响度 -16LUFS 达标 |
| **P3 质量产能** | 2-3 周 | 双 ASR 质检闭环、音色工厂（VoiceGenerator 分时）、批量夜间队列、prompt A/B | QC 自动拦截率可统计；音色库 ≥8 个；日产能 10+ 集 |
| **P4（远期）** | — | vLLM 提速 / SoulX 微调扩标签（CosyVoice 训练栈）/ 实时交互版（pipecat） | — |

**代码仓库结构**（建议）：

```
podcast-studio/
├─ orchestrator/        # LangGraph：graph.py nodes/ prompts/ validators/
├─ tts_service/         # FastAPI 包装 SoulX：server.py soulx_wrapper.py
├─ voice_lib/           # 音色库 + manifest
├─ postprocess/         # ffmpeg.py subtitles.py watermark.py qc.py
├─ contracts/           # 三个 JSON schema（pydantic + jsonschema 双份）
├─ styles/              # 九种风格模板
└─ jobs/                # 任务产物 + 成本记录
```

---

## 8. 边界与风险（快照）

1. **5080 显存纪律**：同一时刻一个大模型；VoiceGenerator 与 SoulX 分时复用；Whisper 用 int8；
2. **Blackwell 兼容**：torch 必须 cu128 轮子；魔改 vLLM 先不用；P0 即暴露所有环境风险；
3. **实时与拟真是跷跷板**：本方案产出的是"离线精制"播客（对标豆包 AI 播客的生成形态，非其实时通话形态）；未来做"可插话播客"再引 pipecat + MOSS-Realtime/FireRedTTS2-Stream；
4. **合规**：SoulX Apache 2.0 可商用，但克隆真人声需授权文书；成品带 AudioSeal 水印 + "AI 生成"披露；
5. **回退**：若 SoulX 在 Blackwell 栽跟头 → 换 VoxCPM2(8GB)/FireRedTTS2(9GB)，只动 `tts_service/soulx_wrapper.py` 一个适配文件，上层契约不变。

## 9. 成本与产能（P0 实测后校准）

| 项 | 每集(10min) | 说明 |
|---|---|---|
| LLM API | ¥0.02-0.1 | 两步改写约 6-10k tokens |
| TTS | ¥0（电费） | 本地 5080；RTF 待实测（预估 0.3-0.8 → 每集 3-8 分钟生成） |
| 后期 | ¥0 | CPU 为主 |
| **合计** | **≈ ¥0.1 内** | 对比全 API 方案 ~¥2/10min |

---

# v1.1 增补（2026-11-17）

## A. 决策记录（已锁定）

| 决策项 | 定案 |
|---|---|
| 写稿 LLM | **DeepSeek（主）**，GLM 备用（LangChain `ChatOpenAI` 换 `base_url` 即切） |
| P0 | 已启动。**注意：当前操作机是 RTX 3050 4GB（笔记本）**，5080 环境验证需在 5080 机器上执行 `p0/p0_validate.sh`（已写好，产物 p0_report.log 回传分析） |
| 音色库 | 起步 6-8 个（3女3男+2特色），授权来源自录/公开许可 |
| P3 音色设计 | 必做：文字描述→生成音色→入库（完整设计见 §C） |

## B. 用户新增 5 个项目的借鉴分析

### B1. indextts2-studio（173★，lfandrh）—— 工程价值最高，三条直接进 P0/P1

| 借鉴点 | 内容 | 落位 |
|---|---|---|
| **no_grad 审计** ⭐ | 作者发现 BigVGAN 声码器漏包 `torch.no_grad()` 导致 RTF 11.87→1.31（**9 倍免费提速**） | **P0 新增检查项**：跑通示例后审计 SoulX 推理链（vocoder/flow matching 是否漏 no_grad/inference_mode）。这可能是 5080 上最大的零成本提速机会 |
| **参考音频 md5 特征缓存** | 同一参考音频第二次起跳过特征提取 | P1 音色库：每个音色的 prompt 特征**预提取并持久缓存**（音色库固定，命中率 100%，省掉每集重复提取） |
| **术语词汇表** | 自定义读音（"5090"→"五零九零"） | P1 写稿层：人名/品牌/术语读音修正表，rewrite 前做文本替换 |
| 显存分档 vram_tier + 懒加载 + gc/empty_cache | 低显存稳定运行 | P0/P2 5080 显存纪律实现 |
| 敢砍负优化 | 实测移除 accel/torch_compile/deepspeed 等无效开关 | P0 调优方法论 |
| FastAPI + SSE 分步进度 | 模型加载→特征→GPT→声学→声码器五步上报 | P1 TTS 服务（10 分钟稿要合成几分钟，进度可视化必需） |
| 任务簿记可恢复 + 诊断报告打包 | 刷新找回任务；报错打包给 AI 排查 | P2 job.json + 错误报告 |
| AI Agent Skill（26 子命令：探活→选音色→合成→交回路径） | agent 友好的 CLI | 印证我们"TTS 服务 Tool 化"设计，P1 参照其命令面 |

⚠️ 许可注意：该项目遵循 IndexTTS2 的 B站协议——**只借鉴工程模式与思想，不把其代码/IndexTTS2 模型引入商用链**。

### B2. indextts2-longform-workbench（104★，Mars-1438，MIT）—— 长篇与后期对位的蓝本

| 借鉴点 | 内容 | 落位 |
|---|---|---|
| **句级管理** | 每句独立生成/重做，只改音色不动情感参数（参数分离保存） | P3 QC：重生成粒度到句，且保留该句已验证的参数 |
| **人物库（角色-音色绑定）** ⭐ | 人名+别名+属性标签+绑定音色 | **P1 音色 manifest 升级**：节目人设库——"主持人小雅"固定绑 v01，**跨集音色一致**（系列播客刚需） |
| **"句子局部比例"音效对位** ⭐ | 换音色/重生成后时长变化，音效按"句内相对位置"重排（实测 26 个音效换音色后全部落位） | P2 后期：转场/音效时间戳锚定到句+句内比例，而非绝对时间——重合成后自动重对位 |
| 长音频按音量自动切片 + SRT 智能对齐 | 分段与字幕工程 | P2 参考实现 |
| 可插拔降级 | 大件依赖缺失时功能退化不崩溃 | fallback 设计模式 |
| 补丁不侵入宿主 + 一键还原 | 扩展层与上游完全解耦 | 我们对 SoulX 官方仓库零改动，全部包装外置 |
| 批量索引优化 | 1005 段入库 2min→4.9s（整批一次读写） | P3 批量调度 |

### B3. CoAudio-Public（15★，FreshSoul）—— P3 音色库管理层参考

| 借鉴点 | 内容 | 落位 |
|---|---|---|
| **CLAP 语义检索** ⭐ | 音频-文本联合嵌入 + hnswlib 索引："温暖低沉的男声"→语义搜音色 | P3 音色库：为每个入库音色生成 CLAP 嵌入，Agent 可按描述自动选音色（配合 VoiceGenerator 形成"设计→检索"闭环） |
| 素材库索引不搬文件 + 元数据（UCS 分类） | 管理模式 | 音色库 manifest 字段设计（风格/性别/年龄/语速/授权状态） |
| LLM 批量打标 | 自然语言解析批量元数据 | P3：新音色入库自动打标 |

（该仓库本身是游戏/影视音效管理工具，与播客无直接关系；仅吸收音色库管理设计。）

### B4. VoiceStar（317★，jasonppy，MIT 代码 + CC-BY-4.0 权重）

| 借鉴点 | 内容 | 落位 |
|---|---|---|
| **精确时长控制**（`--target_duration`，840M，Encodec，可外推超训练时长） | 全网少见的"定时语音"能力 | **特定场景专用引擎**：片头 15s / 广告位 30s / 结尾 10s 等时长强约束片段（SoulX 时长由文本决定估不准）。840M 很小，不挤显存 |
| 长度外推思想 | 超训练长度仍稳定 | 印证我们的分段策略 |

定位：不进主链，作为 P2+ 的"定时片段"可选工具与 fallback 候选。

### B5. VoiceStudio（112k★！debpalash/voicestudio，AGPL-3.0）—— P3 音色设计工作台的 UX 蓝本

- 定位：完全本地的 "ElevenLabs 替代"，Electron+Python，宣称 646 语言，默认引擎 k2-fsa/OmniVoice，模型管理器可聚合数十引擎（含 CosyVoice、whisper.cpp），语音克隆+**语音设计**（文字描述生成音色）+配音+转写，recipes 复用配方、批量队列、watch folders、MCP 集成。
- **借鉴（UX 与产品形态）**：模型管理器（下载前显示许可证）→ 我们的模型/音色管理页；recipes → "节目模板"（风格+音色组合+BGM 一键复用）；批量队列/watch folders → P3 产能调度。
- **借鉴（能力验证）**：它把 CosyVoice 等多引擎聚合为一个工作台——印证我们 `tts_service` 适配层（一接口多引擎）的正确性；其 voice design 文档（docs/electron-voice-design.md）P3 时细读。
- ⚠️ **AGPL-3.0 红线**：代码不可拷入我们闭源产品；作为独立工具使用没问题。产品形态上我们做"音色设计面板"时只借鉴交互，不引其源码。

### B6. 五项目借鉴汇总（并入 §6 清单后的增量）

| 来源 | 最高价值借鉴 | 落位阶段 |
|---|---|---|
| indextts2-studio | no_grad 审计（潜在 9× 提速）/ md5 特征缓存 / 术语词汇表 / SSE 进度 | P0/P1 |
| longform-workbench | 人物库绑定音色（跨集一致）/ 句内比例音效对位 / 句级重做 | P1/P2/P3 |
| CoAudio | CLAP 语义检索音色库 | P3 |
| VoiceStar | 定时片段专用引擎（片头/广告位） | P2+ |
| VoiceStudio | 音色设计工作台 UX / recipes / 多引擎聚合模式 | P3 |

## C. P3 音色设计与音色库（用户需求定稿：设计音色→成库→可检索→跨集复用）

```
[入口1] 文字描述造音色: "35岁男声,低沉沙哑,略带京腔"
[入口2] 真人授权录音: 5-10s 参考
        │
        ▼
┌─ 音色工厂（分时加载, 造完卸载）─────────────────────┐
│ MOSS-VoiceGenerator(1.7B)  主力: 描述→音色wav        │
│ VoxCPM2 VoiceDesign        备选                     │
│ 质检: 双ASR回测CER + 人耳抽检                        │
└──────────────┬──────────────────────────────────┘
               ▼
┌─ 音色库（manifest + 资产 + 索引）────────────────────┐
│ v07_male_narrator.wav + prompt_text + 特征缓存(md5)  │
│ 元数据: 性别/年龄/风格标签/语速感/授权状态/来源        │
│ 人物绑定: "主持人老张"→v07（跨集一致）                │
│ CLAP嵌入: 语义检索 "温暖女声"→v01                    │
└──────────────────────────────────────────────────┘
               ▼
  TTS服务 /voices 暴露列表; Agent 按节目人设自动选音色
```

## D. P0 现状与下一步

- **本机探测结果（2026-11-17）**：当前操作机 GPU 为 **RTX 3050 4GB**（笔记本，驱动 576.83 / CUDA 12.9），WSL2 Ubuntu 已装（stopped）。**3050 4GB 跑不动 SoulX（需 8-10GB）**——P0 的 GPU 验证必须在 5080 那台机器上做。
- 已交付：`p0/p0_validate.sh`（在 5080 机器 WSL2 内 `bash p0_validate.sh 2>&1 | tee p0_report.log`，10 步全自动：GPU→WSL直通→cu128 torch→sm_120 确认→克隆→权重下载→官方示例→**显存/耗时实测**）。
- 5080 机器上的驱动要求：≥570（cu128 轮子需要 CUDA 12.8 运行时；驱动 576.83+ 均满足）。
- 回传 `p0_report.log` 后：确认 sm_120 ✅ → 进入 no_grad 审计（B1 借鉴项）→ 记录真实 RTF → 校准产能模型。
