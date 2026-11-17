# RTX 5080 + SoulX-Podcast 本地方案（架构澄清 + 详细实施方案）

> 前置文档：《AI播客开源项目调研报告》《SoulX-Podcast基底融合方案分析》
> 本文档回答：SoulX 到底是不是专门训练的模型？LLM 要不要本地部署？5080 怎么跑？

---

## 1. 先回答你的核心问题（已查证源码，非推测）

### Q1：SoulX 是"专门训练的播客模型"，还是"通用底座套壳"？

**是前者——专门从零训练的播客 TTS 模型，而且训练规模很大。** 论文（arXiv:2510.23541）明确：

- 它拿 **Qwen3-1.7B 只做初始化骨架**（借它的语言能力），然后**扩展词表**（加入语音 token + `<|laughter|>` 等副语言 token + 方言 token），再用 **130 万小时数据**（100 万独白 + 30 万对话）完整训练成了语音合成模型；
- 训练后它**已经不是通用 LLM**——它只会"读剧本"，不会写剧本、不会聊天。Qwen3 底座的价值（语言理解）被"冻结"进了语音生成能力里；
- 这正是它本地部署的价值所在：**它的全部优势都在权重文件里**（对话轮替的韵律衔接、副语言渲染、90 分钟音色稳定），这些能力没有任何 API 可以替代。

一句话：**SoulX = 专门为"播客式多人对话朗读"训练的专用模型，不是底座套壳。**

### Q2：它有没有做特殊的微调/训练？——分两层说清楚

| 层 | 有没有专门训练 | 结论 |
|---|---|---|
| **语音合成模型（SoulX 本体）** | ✅ 有，130 万小时完整训练（非 LoRA 级微调），其中 30 万小时专门是多人对话数据，副语言标注 1000 小时 | 这就是本地部署它的全部理由 |
| **脚本大语言模型（写对话稿的）** | ❌ **完全没有。仓库里根本没有这个模块** | 已查证 `cli/podcast.py`：纯 TTS 推理脚本，输入成品 JSON → 输出 WAV，**零 LLM 调用**。`llm_engine=hf/vllm` 参数指的是 TTS 自己的推理后端，跟脚本生成无关 |

### Q3：那 LLM 用本地还是 API？

**你的判断完全正确：脚本层用 API，不要本地。** 理由：
1. SoulX 没对任何脚本 LLM 做过适配/微调——它只认一个 JSON 格式（`speakers[]{id, prompt_audio, prompt_text}` + `text: [[S1,"..."],[S2,"..."]]`），谁来写这个 JSON 都行；
2. 写好播客稿需要强 LLM（口语化改写、幽默感、长上下文），本地跑同等水平的模型（70B 级）5080 根本放不下，而 API 成本极低（一集 10 分钟播客的脚本约 3-5k tokens 输出，几分钱）；
3. 唯一例外：如果你要求"全离线/数据不出内网"，才考虑本地小模型（Qwen3-14B/32B 量化），但脚本质量会明显下降——不建议。

**注意一个澄清**：SoulX 自己**只能本地部署**（权重在 HuggingFace，无官方商用 API；HF Spaces 那个在线 demo 只是演示用）。所以最终形态是：**脚本层 API + 语音层本地**，正好各取所长。

---

## 2. RTX 5080 适配性评估（16GB GDDR7，Blackwell sm_120）

### 2.1 显存预算（估算 + 依据）

| 组件 | 显存 | 依据 |
|---|---|---|
| SoulX 1.7B LLM 部分（bf16 权重） | ~3.4 GB | 1.7B × 2 bytes |
| Flow Matching + vocoder + s3tokenizer | ~2-4 GB | CosyVoice 系两阶段标配（CosyVoice2-0.5B 全套约 4GB，等比放大） |
| KV cache + 长文本运行时（10 分钟稿） | ~2-3 GB | 交错序列逐句生成，缓存随长度增长 |
| **SoulX 合计** | **约 8-10 GB** | 16GB 卡留有余量 ✅ |
| faster-whisper large-v3（int8，字幕用） | ~1.5-2 GB | 或纯 CPU 跑，不占显存 |
| AudioSeal 水印（可选） | ~0.5-1 GB | |
| **峰值合计** | **~12 GB** | 16GB 内 ✅（官方未公布 VRAM，此为架构推算，Phase 0 实测校准） |

**结论：5080 16GB 跑 SoulX 单模型管线绰绰有余。** 但注意：**不能同时跑两个大模型**（比如 SoulX + MOSS-VoiceGenerator 同时推理会爆显存）——解决方案见 §3.3"分时复用"。

### 2.2 Blackwell 兼容性（重要，有坑）

| 依赖 | 官方锁定版本 | sm_120 (Blackwell) 兼容性 |
|---|---|---|
| torch / torchaudio | 2.7.1 | ✅ **但必须装 cu128 轮子**。坑：`pip install torch==2.7.1` 默认拉 cu126 版，**cu126 不支持 5080**，会报 "no kernel image available"。必须 `--index-url https://download.pytorch.org/whl/cu128` |
| triton | ≥3.0 | ✅ torch 2.7.1+cu128 自带 triton 3.3（Blackwell 已支持） |
| transformers | 4.57.1 | ✅ 无架构相关限制 |
| onnxruntime-gpu | 未锁版本 | ⚠️ 装 GPU 版时确认 CUDA 12.x + Blackwell 支持，或先用 CPU 版 |
| vLLM（runtime/vllm Docker） | 修改版 0.10.1 | ⚠️ vLLM 0.10.x 本身支持 sm_120，但**官方镜像默认 CUDA 12.8 才行，且 SoulX 用的是魔改版**——Blackwell 上风险未知。**策略：先用 `llm_engine=hf` 后端跑通，vLLM 作为 Phase 2 提速项** |

### 2.3 操作系统（你当前是 Windows）

- **推荐：WSL2 + Ubuntu 22.04/24.04**。原因：triton 原生不支持 Windows（需要社区版 triton-windows，坑多）；s3tokenizer/flash 类 CUDA 扩展在 Linux 下编译链最顺；
- WSL2 的 CUDA 直通已经很成熟（宿主装 NVIDIA 驱动即可，WSL 内不装驱动只装 toolkit）；
- 备选：双系统 Ubuntu（性能最稳）或 Docker Desktop + WSL2 后端。

---

## 3. 目标架构（5080 单卡版）

### 3.1 架构图

```
┌─────────────────── 云端（API，按量付费）───────────────────┐
│  脚本生成 LLM：GLM / DeepSeek / Qwen API（任选，建议主备双开） │
│  ① 素材理解+戏剧化改写(两步法)  ② 输出 SoulX JSON + 富标签    │
└──────────────────────────┬───────────────────────────────┘
                           │ soulx_script.json（1个文件，含说话人+对话稿+标签）
                           ▼
┌─────────────── 本地 RTX 5080（WSL2，唯一大模型驻留）──────────┐
│  SoulX-Podcast-1.7B（llm_engine=hf，bf16，~8-10GB）           │
│  + 本地音色库 lib/voices/*.wav（提前用参考音频/VoiceGenerator 造好）│
└──────────────────────────┬───────────────────────────────┘
                           │ 24kHz WAV（裸人声）
                           ▼
┌─────────────── 本地 CPU/轻量 GPU（后期制作链）───────────────┐
│  ffmpeg：BGM(0.25) + loudnorm(-16LUFS/-1.5dBTP) + 48kHz 重采样 │
│  faster-whisper(int8)：词级时间戳 → SRT/VTT 说话人字幕          │
│  AudioSeal：AI 水印（可选，商用必备）                           │
└──────────────────────────┬───────────────────────────────┘
                           ▼
                    成品播客（音频+字幕+封面）
```

### 3.2 各层选型确认

| 层 | 选型 | 为什么 |
|---|---|---|
| 脚本 LLM | **API：GLM-4.x / DeepSeek-V3 / Qwen-Max 任选其一 + 一个备用** | 中文口语化能力强、长上下文、成本每集几分钱；SoulX 未训练过任何脚本模型，API 即最优解 |
| TTS | **SoulX 本地（hf 后端起步）** | 它的全部价值在本地权重里；5080 够用 |
| 字幕 ASR | faster-whisper large-v3 int8（GPU 分时）或 FunASR Paraformer（CPU） | SoulX 按句生成但无词级时间戳，需要回贴 |
| 混音 | ffmpeg（CPU） | 零成本，参数照抄 ai-podcast-workflow |
| 音色 | **离线音色库**：自录授权声 / 公开许可声 / Phase 3 用 VoiceGenerator 分时造 | 避免双模型同时驻留显存 |

### 3.3 显存分时复用策略（5080 单卡的关键纪律）

16GB 只允许**一个时刻一个大模型**。管线按"阶段换模型"设计：

```
T1: LLM API 写稿（不占本地显存）
T2: [如需造音色] 加载 MOSS-VoiceGenerator(1.7B) → 生成音色wav → 存库 → 卸载
T3: 加载 SoulX → 整段合成 → 卸载（或常驻，如果做批量）
T4: faster-whisper(int8) 出字幕（此时 SoulX 可卸载或共用，int8 只占~2GB）
T5: ffmpeg/水印（CPU 为主）
```
音色是**一次性资产**：造好存 `lib/voices/`，之后 T2 阶段直接跳过。

---

## 4. 实施计划（四个阶段，可直接开工）

### Phase 0：环境验证（1-2 天）—— 目的：确认 5080 能跑通官方示例

```bash
# WSL2 (Ubuntu 24.04) 内：
# 1. 确认 CUDA 可见（宿主机已装 NVIDIA 驱动）
nvidia-smi   # 应显示 RTX 5080, CUDA Version >= 12.8

# 2. 建环境（关键是 cu128 源！）
conda create -n soulx python=3.10 -y
conda activate soulx
pip install torch==2.7.1 torchaudio==2.7.1 \
  --index-url https://download.pytorch.org/whl/cu128
python -c "import torch; print(torch.cuda.get_device_name(0), torch.cuda.is_arch_list())"
# 确认输出包含 sm_120

# 3. 装 SoulX
git clone https://github.com/Soul-AILab/SoulX-Podcast && cd SoulX-Podcast
pip install -r requirements.txt   # torch 行会已满足，跳过重装

# 4. 下载权重（二选一）
huggingface-cli download Soul-AILab/SoulX-Podcast-1.7B --local-dir pretrained_models/SoulX-Podcast-1.7B

# 5. 跑通官方中文示例（用 hf 后端，不用 vLLM）
bash example/infer_dialogue.sh   # 首次可把脚本里 llm_engine 改为 hf
```

**验收**：`outputs/mandarin.wav` 出现且试听正常；`nvidia-smi` 记录实际显存峰值（校准 §2.1 估算）。
**风险预案**：若 s3tokenizer/triton 在 Blackwell 报编译错 → 提 issue + 尝试 `triton==3.3`；若实在不行，fallback 到 VoxCPM2（8GB，Apache 2.0）或 FireRedTTS2（9GB）——管线其他部分不变，只换 TTS 引擎适配层。

### Phase 1：最小可用管线（1 周）

1. **写 LLM API 层**（Python，~200 行）：
   - 两步 prompt：Step A 素材→戏剧化口语稿（禁摘要腔，要求语气词/打断/轮次长短不均）；Step B 口语稿→SoulX JSON（严格 schema：speakers 数组引用音色库 id + text 二元组，副语言标签只允许 5 个白名单标签，附验证器）；
   - 输出 schema 校验（jsonschema）+ 失败自动重试。
2. **本地音色库 v0**：挑 2-4 个有授权的参考音频（各 5-10 秒 + 转写文本），建 `lib/voices/manifest.json`。
3. **串起来**：`generate.py --topic "xxx" --style 深夜电台 --len 10min` → 一条命令出 WAV。

**验收**：10 分钟中文双人播客，盲测 5 人，MOS ≥ 4.0/5。

### Phase 2：后期制作链（1-2 周）

- ffmpeg 混音模板（BGM 0.25、前奏 3s/尾奏 5s、`loudnorm=-16 LUFS:TP=-1.5`、重采样 48kHz 立体声）；
- faster-whisper int8 对齐出 SRT/VTT（按 SoulX 句边界 + 说话人 ID 合并）；
- 分段生成策略：长稿按 5-8 分钟章节切段推理（对齐论文训练的 ~5 分钟段），段间 200ms 静音 + BGM 过渡遮缝；
- AudioSeal 水印接入（商用合规）。

### Phase 3：质量与产能（2-3 周，持续）

- **双 ASR 质检**：Paraformer + Whisper 各转写一遍成品，CER > 5% 的句子自动重生成（seed 换一档）；
- **音色工厂**：分时加载 MOSS-VoiceGenerator，文字描述批量造音色入库存档；
- **批量调度**：夜间批量跑列表（5080 单卡约 1 小时产能估算——待 Phase 0 实测 RTF 后校准）；
- **A/B 基线**：保留豆包样例做对照盲测，每两周回归一次。

---

## 5. 需要盯住的三个风险

1. **Blackwell 长尾兼容**（Phase 0 就能暴露）：torch cu128 装错版本、s3tokenizer 编译、vLLM 魔改版不可用。都有明确预案（hf 后端 / fallback 引擎），不致命；
2. **SoulX 官方无 RTF 数据**：生成速度未知（预估：1.7B + flow matching，4080/5080 级别卡大概率 RTF 0.3-0.8，即 10 分钟音频 3-8 分钟生成）。Phase 0 实测后若 >1.5，再启动 vLLM 提速或换 fp16_flow；
3. **许可**：SoulX Apache 2.0 可商用，但克隆真人声音需授权文书；产品侧保留"AI 生成"披露 + AudioSeal 水印。

---

## 6. 成本小结

| 项 | 成本 |
|---|---|
| LLM API（每 10 分钟一集） | ≈ ¥0.02-0.1（GLM/DeepSeek 百万 token 几块到几十块量级） |
| TTS | ¥0（本地 5080，电费） |
| 后期 | ¥0（ffmpeg/CPU） |
| 一次性投入 | 你的时间：Phase 0-2 约 3-4 周 |

对比 ai-podcast-workflow 走火山 API 全链（~¥0.7/3.5min ≈ ¥2/10min）：**本地方案在第几千集后反超，且音色/风格完全自主可控。**
