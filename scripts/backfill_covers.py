"""维护脚本：为所有缺封面的历史任务补生成 cover.png 并回写 job.json。

用法: py scripts/backfill_covers.py
"""
from __future__ import annotations
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from postprocess.cover import generate          # noqa: E402
from webapp import jobs as jm                  # noqa: E402


def main() -> int:
    fixed = 0
    jobs_dir = os.path.join(ROOT, "jobs")
    for name in sorted(os.listdir(jobs_dir)):
        d = os.path.join(jobs_dir, name)
        jf = os.path.join(d, "job.json")
        if not os.path.isdir(d) or not os.path.exists(jf) or name.startswith("diag"):
            continue
        data = json.load(open(jf, encoding="utf-8"))
        cover = os.path.join(d, "cover.png")
        if not os.path.exists(cover):
            try:
                generate(data.get("topic", "未命名"), data.get("style", ""),
                         data.get("minutes", 10), cover)
                fixed += 1
                print("cover+", name)
            except Exception as e:
                print("cover fail", name, e)
                continue
        jm._set(name, paths={"cover": os.path.abspath(cover)})
    print(f"done, {fixed} generated")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
