# Podcast Studio（基于 SoulX-Podcast 的 AI 播客生产系统）

> **交接文档入口：[docs/HANDOFF.md](docs/HANDOFF.md)**（现状/运行手册/契约/API/限制/检查清单）
> 方案文档见上级目录《播客系统最终实施方案v1.0.md》。状态看 [ROADMAP.md](ROADMAP.md)。

## 架构

```
DeepSeek API（写稿） ──► LangGraph/编排 ──► SoulX TTS 服务(RTX 5080) ──► 后期链 ──► 成品
      prompts.py              webapp/            tts_service/           postprocess/
```

## 页面（FastAPI + 原生 JS，零构建）

| 路径 | 功能 |
|---|---|
| `/` 工作台 | 主题/链接/PDF、9 风格、双主播音色、RSS 批量、任务列表（封面/状态） |
| `/tasks` 任务中心 | 筛选、删除、统计 |
| `/job/{id}` 详情 | 7 步流水线、波形播放器、后期开关、轮次脚本（质检徽章）、剪辑、音效/转场、导出 |
| `/voices` 音色库 | 8 音色卡、试听、设为主持/搭档（写 .env 即时生效）、检索 |
| `/settings` 设置 | DeepSeek Key（测试连接）、TTS 探活、副语言白名单、VoiceStar、任务默认值、诊断包 |
| `/traces` 运行日志 | 本地 trace 时间线（状态/步骤/耗时/错误） |

## 快速开始（本机，无 GPU 可跑 Mock 全链）

```bash
pip install -r requirements.txt
cp .env.example .env              # 填 DEEPSEEK_API_KEY（可选，Mock 不需要）
py -m uvicorn webapp.server:app --host 127.0.0.1 --port 8765
# 打开 http://127.0.0.1:8765 ，工作台直接创作（引擎选 Mock）
```

## 5080 机器

```bash
bash ../p0/p0_validate.sh          # P0 环境验证（先跑这个）
uvicorn tts_service.server:app --host 0.0.0.0 --port 8600   # SoulX TTS 服务
# 本机设置页把 TTS 地址指向该机器即可全链合成
```

## CLI / 脚本

```bash
py generate.py --topic "..." --style 幽默对谈 --minutes 10 --dry-run   # 免 key 试跑
py generate.py --topic "..." --tts-url http://<5080>:8600              # 全链
py tests/test_validators.py && py tests/test_pipeline_mock.py \
  && py tests/test_extra.py && py tests/test_qc_audio.py               # 49+ 项测试
py scripts/backfill_covers.py      # 为历史任务补封面
powershell -File scripts/sapi_check.ps1   # 生成中文测试语音（质检/字幕验证用）
```

## 关键配置（.env，设置页可视化编辑）

- 写稿：`DEEPSEEK_API_KEY / DEEPSEEK_MODEL`（GLM 备用自动切换）
- TTS：`TTS_URL / SOULX_MODEL_PATH / SEGMENT_MINUTES`
- 约束：`PARALING_TAGS`（副语言白名单，写稿与校验即时生效）
- 定长片段：`VOICESTAR_URL` 或 `VOICESTAR_CMD`（二选一，可选）
- 默认值：`DEFAULT_STYLE / DEFAULT_MINUTES / DEFAULT_*_VOICE`

## 数据

```
jobs/<job_id>/   job.json · soulx_script.json · cover.png · subtitles.srt
                 wav · final.mp3 · final_sfx.mp3 · edited.mp3 · trace 全局在 jobs/trace.jsonl
voice_lib/       manifest.json + audio/*.wav（参考音频，P1-8 待录）
assets/bgm/      4 首程序化 BGM；assets/sfx/ whoosh/ding/pop
```
