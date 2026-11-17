#!/usr/bin/env python3
"""CLI 入口：一条命令从主题到播客脚本（P1），全链路在 TTS 服务就绪后打通。

用法:
  py generate.py --topic "AI 编程助手会取代程序员吗" --style 幽默对谈 --minutes 10
  py generate.py --topic "..." --dry-run          # 免 API key，内置样稿走校验/组装
  py generate.py --topic "..." --tts-url http://192.168.x.x:8600   # 5080 机器合成
"""
from __future__ import annotations
import argparse, os, sys, json

try:  # 可选：从 .env 读 DEEPSEEK_API_KEY 等
    from dotenv import load_dotenv; load_dotenv()
except ImportError:
    pass

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from contracts.models import Job, load_voice_manifest        # noqa: E402
from orchestrator.nodes import PipelineState, run_script_chain  # noqa: E402
from orchestrator.validators import build_script             # noqa: E402

SAMPLE_DRAFT = """[主持] 哈喽，欢迎回来。今天聊个扎心的：AI 编程助手到底会不会取代程序员？
[搭档] 哎等等——先说结论党退散，这问题没有结论，只有变化。
[主持] 对，我发现一个数据挺有意思，GitHub 官方说开发者用 AI 后任务完成速度快了 55%。
[搭档] 但是另一份斯坦福的研究说，入门级岗位的招聘在收缩，这俩放一起看才有意思。
[主持] 所以你看，工具变强了，但门槛没有消失，它只是往上挪了一层。
[搭档] 对对对，就是从"会写代码"挪到"会判断代码好不好"。
[主持] 那新手怎么办？总不能人人都先工作五年再入门吧 (laughs)
[搭档] 哎，这就是我想说的，新手要用 AI 当陪练，别当拐杖。"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", required=True)
    ap.add_argument("--style", default="幽默对谈")
    ap.add_argument("--minutes", type=int, default=10)
    ap.add_argument("--material", default="", help="素材文本（可从文件读，P2 支持 PDF/URL）")
    ap.add_argument("--material-file", default="")
    ap.add_argument("--dry-run", action="store_true", help="免 API key：用内置样稿过校验")
    ap.add_argument("--tts-url", default=os.getenv("TTS_URL", ""),
                    help="5080 机器上的 TTS 服务地址；留空则只产出脚本 JSON")
    ap.add_argument("--out-dir", default="jobs")
    args = ap.parse_args()

    job = Job(topic=args.topic, style=args.style, minutes=args.minutes)
    os.makedirs(args.out_dir, exist_ok=True)
    job_dir = os.path.join(args.out_dir, job.job_id)
    os.makedirs(job_dir, exist_ok=True)
    job.paths["dir"] = job_dir

    material = args.material
    if args.material_file:
        with open(args.material_file, encoding="utf-8") as f:
            material = f.read()

    manifest = load_voice_manifest(os.path.join(os.path.dirname(__file__),
                                                "voice_lib", "manifest.json"))

    if args.dry_run:
        # 内置样稿（含富标签，检验降级映射与校验器）
        from orchestrator.validators import parse_llm_script, validate_script_text
        turns = parse_llm_script("```json\n" + json.dumps(
            {"turns": [{"speaker": "S1" if l.startswith("[主持]") else "S2",
                        "text": l.split("]", 1)[1].strip()}
                       for l in SAMPLE_DRAFT.splitlines()]}, ensure_ascii=False) + "\n```")
        errors = validate_script_text(turns, args.minutes, ["S1", "S2"])
        print(f"[dry-run] 校验结果: {'✅ 通过' if not errors else '❌ ' + str(errors)}")
    else:
        st = PipelineState(topic=args.topic, style=args.style, minutes=args.minutes,
                           material=material, job=job)
        st = run_script_chain(st)
        turns = st.turns
        print(f"[写稿] 完成，共 {len(turns)} 轮，{st.job.llm_tokens_in}+"
              f"{st.job.llm_tokens_out} tokens")

    voice_map = {"S1": "v01_female_warm", "S2": "v02_male_deep"}   # 人物绑定（跨集一致）
    script = build_script(job.job_id, args.style, args.minutes, turns,
                          voice_map, {"by_id": {v.id: v for v in manifest.values()}})
    script_path = os.path.join(job_dir, "soulx_script.json")
    script.save(script_path)
    job.paths["script"] = script_path
    job.save(os.path.join(job_dir, "job.json"))

    print(f"[产出] {script_path}")
    print(f"       {len(turns)} 轮 / {script.total_chars()} 字 / "
          f"预计 {script.est_seconds()/60:.1f} 分钟")
    print(f"       音色: S1={voice_map['S1']} S2={voice_map['S2']}")

    if args.tts_url and not args.dry_run:
        print(f"[合成] 提交到 {args.tts_url} ...")
        from orchestrator.tts_client import TTSClient, print_progress
        client = TTSClient(args.tts_url)
        meta = client.synthesize(script.to_dict(), on_progress=print_progress)
        job.tts_seconds = meta.get("duration_sec", 0)
        job.paths["wav"] = meta.get("wav_path")
        job.save(os.path.join(job_dir, "job.json"))
    else:
        print("[下一步] 在 5080 机器启动 TTS 服务后，加 --tts-url 合成音频")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
