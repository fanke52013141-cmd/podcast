"""音色设计工厂验收（PLAN §2.3）—— 5 条文字描述设计 5 个音色，各出试听音频。

运行: python tests/make_design_deliverables.py
产物: webapp/static/compare/design_1.mp3 … design_5.mp3 + manifest 登记汇总
"""
import os, sys, time, json

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tts_service import minimax_engine as mx
from webapp import voice_store

PREVIEW = "夜色渐深，欢迎回到我们的节目。今天我们慢慢聊，希望这段声音，能陪你度过接下来的一段时光。"

DESIGNS = [
    ("design_1", "温柔知性的年轻女声，二十五岁左右，语速偏慢，吐字清晰温润，适合深夜电台，声音有安抚感和距离拉近距离的亲切感", "深夜知性·女"),
    ("design_2", "阳光活力的年轻男声，二十出头，语速明快，充满朝气，略带笑意，适合资讯快节奏节目和早间播报", "阳光快嘴·男"),
    ("design_3", "低沉磁性的中年男声，四十岁左右，沉稳大气，语速从容，字字有力，适合商业访谈和深度对谈", "低沉访谈·男"),
    ("design_4", "清脆伶俐的年轻女声，二十岁出头，伶牙俐齿，节奏跳跃，带一点俏皮，适合漫才里的吐槽担当", "漫才吐槽·女"),
    ("design_5", "温和儒雅的中老年男声，五十五岁左右，学者气质，娓娓道来，语速平缓，适合知识科普与人文讲述", "学者讲述·男"),
]

def main():
    out_dir = os.path.join("webapp", "static", "compare")
    os.makedirs(out_dir, exist_ok=True)
    results = []
    for slug, prompt, label in DESIGNS:
        t0 = time.time()
        try:
            vid, buf = mx.design_voice(prompt, PREVIEW)
            rel = voice_store.save_trial_audio(vid, buf)
            entry = voice_store.find_by_minimax(vid)
            if not entry:
                entry = voice_store.create({
                    "id": f"vd_{slug}",
                    "name": f"{label}（AI设计）",
                    "gender": "male" if "男" in label else "female",
                    "style_tags": [label, "AI设计"],
                    "source": "minimax_designed",
                    "license_status": "generated",
                    "prompt_audio": rel,
                    "prompt_text": PREVIEW,
                })
            engines = entry.get("engines") or {}
            engines["minimax"] = {"voice_id": vid}
            voice_store.update_engines(entry["id"], engines)
            # 同步一份到对比试听页
            with open(os.path.join(out_dir, f"{slug}.mp3"), "wb") as f:
                f.write(buf)
            results.append({"slug": slug, "label": label, "voice_id": vid,
                            "local_id": entry["id"], "trial": rel,
                            "seconds": round(time.time() - t0, 1)})
            print(f"✅ {label}: {vid[:28]}… ({len(buf)}B, {results[-1]['seconds']}s)")
        except Exception as e:
            print(f"❌ {label}: {str(e)[:160]}")
            results.append({"slug": slug, "label": label, "error": str(e)[:200]})
    with open("tts_out/design_deliverables.json", "w", encoding="utf-8") as f:
        json.dump(results, f, ensure_ascii=False, indent=2)
    n_ok = sum(1 for r in results if "voice_id" in r)
    print(f"\n完成: {n_ok}/5 入库，试听页资产已就绪")

if __name__ == "__main__":
    main()
