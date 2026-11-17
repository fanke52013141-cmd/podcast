# HANDOFF · AI 播客生产系统交接文档

> 版本：v0.1（2026-11-17）｜代码状态：**功能完备，全链以占位音频验证通过，等待 RTX 5080 硬件验收**
> 关联文档：`docs/` 下调研、融合分析、实施方案、串联排查报告

---

## 1. 一句话现状

基于 **SoulX-Podcast（本地 TTS）+ DeepSeek API（写稿）+ ffmpeg/Whisper 后期链** 的完整 AI 播客生产系统已经写完：
**6 个页面、73 项测试、全链路 Mock 验证通过**；唯一没做的是"真音频"——等你在 5080 机器上跑完 P0 环境验证并录好音色。

## 2. 系统能力总览

```
节目模板 / 手动创作 / RSS 批量 / 夜间定时队列
        │
        ▼
写稿层：DeepSeek(主)/GLM(备) → 两步改写(NotebookLlama法)
        → 富标签降级(Dia 词汇表) → 5标签白名单(按设置) → 术语读音表
        │  soulx_script.json（契约）
        ▼
合成层：tts_service（5080 机器，FastAPI + SSE）
        → SoulX-Podcast 1.7B，按 ~7min 切段，段缓存(md5)可秒续
        ▼
后期层：ffmpeg
        → BGM(4首程序化曲库, sidechain 鸭化) + loudnorm(-16LUFS)
        → 精确字幕 SRT/VTT（faster-whisper 词级 + 轮次边界吸附）
        → 封面(1080²) → 质检(单ASR CER 徽章) → 剪辑(轮次音量/裁剪/变速)
        → 音效/转场(轮次+句内比例锚点，重合成自动重对位)
        ▼
交付：final.mp3 / final_sfx.mp3 / edited.mp3 / subtitles.srt+.vtt / cover.png
```

## 3. 目录与关键文件地图

```
podcast-studio/
├─ webapp/                 # Web 服务（FastAPI + 原生JS 零构建）
│  ├─ server.py            # 全部 HTTP API（路由总入口）
│  ├─ jobs.py              # 任务执行器：状态机/段进度/后期链/音效/质检/调度
│  ├─ settings_store.py    # .env 读写（掩码），即时生效
│  ├─ voice_store.py       # 音色管理 CRUD + 音频上传
│  ├─ templates_store.py   # 节目模板 recipes（VoiceStudio 借鉴）
│  ├─ schedules_store.py   # 夜间队列持久化 + due 判定（纯函数可测）
│  └─ static/              # 6 个页面：index/tasks/job/voices/settings/traces
├─ orchestrator/
│  ├─ prompts.py           # 两步写稿 Prompt + 9 风格模板
│  ├─ nodes.py             # research/draft/rewrite/validate 节点（api|mock）
│  ├─ validators.py        # 富标签降级 + 白名单/密度/时长校验（PARALING_TAGS 生效）
│  ├─ term_lexicon.py      # 术语读音表
│  ├─ extract.py           # URL/PDF/DOCX/TXT/RSS 抽取（SSL 降级）
│  ├─ graph.py             # LangGraph 接线（生产路径在 webapp/jobs.py）
│  ├─ tts_client.py        # TTS HTTP 客户端（SSE 进度/超时/降级）
│  └─ mock_llm.py          # 免 key 联调引擎
├─ tts_service/
│  ├─ server.py            # 部署在 5080：/synthesize /synthesize/sse /health /voices
│  ├─ soulx_wrapper.py     # 调 SoulX 官方 cli，切段+段缓存+拼接
│  ├─ fixed_segment.py     # VoiceStar 定长片段适配（URL/CMD 双模式，未配置 503）
│  └─ __init__.py
├─ postprocess/
│  ├─ pipeline.py          # 后期总编排（混音→字幕→VTT）
│  ├─ mix.py               # BGM 鸭化 + loudnorm（ffmpeg filter_complex）
│  ├─ subtitles.py         # precise_srt（whisper 词级吸附）+ bounds_from_srt
│  ├─ qc_audio.py          # 质检（ASR CER，阈值可配 QC_CER_THRESHOLD）
│  ├─ qc.py                # CER 算法（纯标准库）
│  ├─ edit.py              # 剪辑（轮次音量/裁剪/变速，复用 precise 边界）
│  ├─ sfx.py               # 音效锚点（轮次+句内比例）解析与混入
│  └─ cover.py             # PIL 封面生成
├─ contracts/models.py     # SoulXScript / VoiceEntry / Job 数据契约
├─ voice_lib/manifest.json # 音色库（8 个占位，人物绑定）
├─ assets/bgm|sfx/         # 程序化资产（不入库，scripts/gen_assets.sh 重建）
├─ scripts/                # p0_validate.sh / gen_assets.sh / sapi_check.ps1 / backfill_covers.py
├─ tests/                  # 73 项（见 §8）
├─ jobs/                   # 运行时产物（gitignore）
└─ ROADMAP.md              # 任务看板
```

## 4. 运行手册

### 4.1 本机快速跑（无 GPU，Mock 全链）

```bash
pip install -r requirements.txt          # 含 fastapi/pillow/faster-whisper/av
bash scripts/gen_assets.sh               # 重建 BGM/SFX（仓库不含音频资产）
py -m uvicorn webapp.server:app --host 127.0.0.1 --port 8765
# 打开 http://127.0.0.1:8765 ，引擎选 Mock，直接创作
```
无 key 也能跑：写稿走 mock，TTS 不接时任务停在"脚本就绪"。

### 4.2 5080 真机验收（三步）

```bash
# ① P0 环境验证（WSL2 Ubuntu 内，torch cu128 关键）
bash scripts/p0_validate.sh 2>&1 | tee p0_report.log
# 关注：sm_120 是否在 arch list、显存峰值、RTF

# ② 启动 TTS 服务（模型首次需下载 ~3-4GB）
uvicorn tts_service.server:app --host 0.0.0.0 --port 8600

# ③ 本机设置页：填 DeepSeek Key（测试连接→绿），TTS 地址填 5080 的 IP:8600（探活→绿）
#    工作台引擎切 DeepSeek API → 生成 → 详情页听到真实音频
```

### 4.3 日常生产

- **节目模板**：工作台下拉一键回填（风格+双主播+BGM+时长），“存为模板”可新增
- **RSS 批量**：工作台折叠面板，预览后一键建 N 集（使用当前界面风格/音色）
- **夜间队列**：设置页配置时间+模板+主题列表，到点自动批量建任务；支持“立即执行”
- **音色录入**：音色库 → 编辑 → 上传 5-10s 参考音频 + 填逐字转写（P1-8 流程）

## 5. 数据契约（改代码前必读）

| 文件 | 位置 | 说明 |
|---|---|---|
| `soulx_script.json` | `jobs/<id>/` | 写稿→TTS 核心契约。字段与 SoulX 官方示例对齐：`speakers[{id,prompt_audio,prompt_text}]` + `text[[S1,"..."],…]`，扩展 `meta` |
| `manifest.json` | `voice_lib/` | 音色库：id/性别/风格标签/人物绑定/授权状态/prompt_audio/prompt_text |
| `job.json` | `jobs/<id>/` | 任务簿记：status/step/provider/synth 段进度/paths(bgm_cover…)/llm tokens+cost/qc/sfx 锚点/srt_mode |
| `templates.json` | 根 | 节目模板（首次运行自动生成 3 个种子） |
| `schedules.json` | 根 | 夜间队列（last_run 防重） |
| `trace.jsonl` | `jobs/` | 观测事件流（状态/步骤/耗时/错误/剪辑/质检） |
| `.env` | 根 | 配置（掩码展示，**永不入库**） |

## 6. API 速查

| 方法 路径 | 作用 |
|---|---|
| POST `/api/jobs` | 建任务（topic 支持 URL 自动抓正文；material 支持上传后路径） |
| GET `/api/jobs` `/api/jobs/{id}` | 列表/详情（含 script/qc/sfx/paths） |
| POST `/api/jobs/batch` | RSS 批量（透传风格/音色/provider/BGM） |
| GET `/api/jobs/{id}/export|audio|srt|vtt|edited|sfx-audio` | 各类产物下载 |
| POST `/api/jobs/{id}/postprocess|qc|apply-sfx|edit` | 手动后期/质检/音效/剪辑 |
| GET/POST/DELETE `/api/sfx` … | 音效锚点 CRUD（见 server.py） |
| GET/POST/PUT/DELETE `/api/voices*` | 音色 CRUD + `/audio` 上传 + `/set-role` |
| GET/POST/DELETE `/api/templates` | 节目模板 |
| GET/POST/DELETE `/api/schedules` + `/run-now` | 夜间队列 |
| POST `/api/upload` | 素材文件上传（PDF/DOCX/TXT/MD → 返回路径） |
| GET/POST `/api/settings` + `/test-llm` `/test-tts` | 配置与探活 |
| POST `/api/diag` | 诊断包 zip（脱敏） |
| GET `/api/traces` | 观测事件 |
| POST `/api/fixed-segment` | VoiceStar 定长片段（未配置返回 503+指引） |

## 7. 关键设计决策（为什么这么做）

1. **LLM 走 API、TTS 走本地**：SoulX 未训练任何脚本模型（源码确认），它的价值全在 TTS 权重；脚本层用 DeepSeek 成本 ~¥0.07/集。
2. **时间轴单一真源**：字幕（precise 词级）→ `bounds_from_srt` 反解 → 剪辑/音效共用，避免“字幕准、剪辑飘”。
3. **锚点而非绝对时间**：音效存 (轮次, 句内比例)，重合成/变速后自动重对位（longform-workbench 法）。
4. **段缓存**：md5(文本+参考音频+seed)，中断重跑秒续（indextts2-studio 法）。
5. **BGM 鸭化**：sidechaincompress（人声出现时自动压低），非恒定音量。
6. **任务串行**：单 worker（5080 显存纪律，同时只跑一个合成）。
7. **模板 BGM 贯通**：模板指定 BGM → job.json → 混音覆盖，不靠猜风格。

## 8. 测试与验收

```bash
py tests/test_validators.py     # 25 项：降级映射/白名单/密度/时长/解析/CER/组装
py tests/test_pipeline_mock.py  # 10 项：mock 全链+素材抽取+契约落盘
py tests/test_extra.py          # 32 项：RSS/吸附/剪辑边界/音效锚点/VoiceStar/段缓存/白名单/音色CRUD/模板/队列/SRT往返
py tests/test_qc_audio.py       # 6 项（需 faster-whisper + SAPI 样本）：质检通过/坏轮标记
```
真实音频验收标准（P1-10）：可听、音色稳定、无爆音、字幕偏差 <200ms、质检 CER 达标。

## 9. 已知限制与待办（按等待条件）

**等你（硬件/素材）**
- P0 环境验证（脚本已备）→ no_grad 审计（可能 9× 提速）
- 音色录音（6-8 个 5-10s）→ P1-10 首跑
- DeepSeek Key（设置页填入）

**等 5080/权重**：AudioSeal 水印 · 双 ASR（Paraformer）· 句级重做 · 多引擎路由 · 音色工厂 · SoundEffect · 混响 · 特征缓存

**产品范围**：仅双主播（SoulX 可多人，扩展需改契约+UI）· 非 RSS 列表页 · 术语表仅文件编辑 · VoiceStar 仅接口

**技术债**：夜间队列精确到分钟（该分钟内服务需在跑）· 上传参考音频不自动转写 · 质检阈值需按模型校准（tiny→0.35，好模型→0.05）

**明确不做**：Agent/MCP 对接、内置提示词优化（另有方案）

## 10. 安全与合规

- `.env` 含 API Key，**已 gitignore**；界面上 Key 一律掩码；诊断包脱敏
- 声音克隆需授权文书；成品建议注入 AudioSeal 水印（P2-3 待接）+ “AI 生成”披露
- 前端无外链、无遥测；所有数据本地

## 11. 故障排查

| 症状 | 处理 |
|---|---|
| torch 报 no kernel image | 必须是 **cu128** 轮子（5080=sm_120），见 p0 脚本 |
| ffmpeg not found | 装 ffmpeg 到 PATH，或 `pip install imageio-ffmpeg`（mix.py 有回退） |
| faster-whisper 报 av metadata_errors | 已用 ffmpeg 解码绕开；无需处理 av 版本 |
| TTS 探活失败 | 5080 防火墙/IP/端口；`curl http://IP:8600/health` |
| 任务卡“脚本就绪” | 正常：未配 TTS_URL；配好后“重跑后期”或新任务自动合成 |
| 音效/剪辑边界不对 | 确认 job 有 subtitles.srt（重跑后期生成 precise 版） |

## 12. 交接检查清单

- [ ] 本机跑通：`uvicorn` + 工作台 Mock 生成 + 详情页导出（占位音频）
- [ ] `tests` 全绿 73 项
- [ ] 5080：P0 脚本全绿，记录 RTF/显存
- [ ] 5080：TTS 服务启动，本机探活绿
- [ ] 设置页填 DeepSeek Key，测试连接绿
- [ ] 录 4-8 个音色（含转写），音色库上传并试听
- [ ] 首集真实音频（P1-10）→ 质检徽章检查 → 导出带音效版

---

## 13. UI 主题与前端缓存约定（Kosmik 换肤轮增补）

### 13.1 当前主题：Kosmik 暖阳纸风（浅色）

2026-11-17 晚全站从"深色录音室"换肤为 **Kosmik 暖阳纸风**（参考 kosmik.app，Stitch 产出
"Warm Atelier" 设计系统 + 工作台设计稿，见 Stitch 项目 16470948066377376786）：

- 背景暖米纸感 `#FAF3E6`（径向暖色纹理）；卡片暖白 `#FFFDF6`，1.5px 边框 `#E6DCBE`，圆角 14
- 主橙 `#EE8A31`，深橙描边/硬底阴影 `#B45309`；成功 `#0F9D6B`，警示 `#D9534F`
- 超粗墨色标题（900 字重 #1C1710）；正文 `#2B251C`，次要 `#8A7F6A`
- 实现：style.css 顶部 tokens + 末尾"Kosmik 重构层"；深色值已全量清扫

**换肤/调色只动 `:root` tokens 与末尾覆盖层即可**，页面结构与 JS 无需改动。

### 13.2 前端缓存约定（重要）

静态资源与页面带版本参数 `?v=0.2`（style.css/app.js/voices.js/… 与页间导航链接），
且 server.py 有 no-cache 中间件。**改动任何前端文件后：把相关 `?v=` 参数 bump 一位**
（如 0.2 → 0.3），否则浏览器启发式缓存会继续给用户旧 JS。

### 13.3 音色库 v2 设计要点（voices 页）

- hero 大标题 + 药丸搜索条；卡片：陶瓷声纹瓷砖头像（每音色精选柔和色板 HUES[]）+
  细波形条 + 无边框标签药丸 + 分段式「主持人/搭档」切换（写入 DEFAULT_*_VOICE 即时生效）
- 许可证/来源小标；未上传参考音频的音色显示"⚠ 待录参考音频"
- 若再次重设计：只改 voices.html 结构 + voices.js render() + style.css 末尾覆盖层
