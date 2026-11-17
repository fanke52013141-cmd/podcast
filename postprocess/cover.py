"""封面生成（P2-8）—— 1080×1080 播客封面，PIL 绘制（渐变底 + 声纹条 + 标题排版）。

零网络依赖；中文字体优先 msyh（微软雅黑），缺失时退到系统默认（英文标题兜底）。
"""
from __future__ import annotations
import os
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

W = H = 1080
FONT_CANDIDATES = ["C:/Windows/Fonts/msyhbd.ttc", "C:/Windows/Fonts/msyh.ttc",
                   "C:/Windows/Fonts/simhei.ttf", "/System/Library/Fonts/PingFang.ttc",
                   "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc"]


def _font(size: int):
    for p in FONT_CANDIDATES:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def generate(topic: str, style: str = "", minutes: int = 10,
             out_path: str | Path = "cover.png") -> str:
    topic = (topic or "未命名节目").strip()
    img = Image.new("RGB", (W, H), "#111214")
    d = ImageDraw.Draw(img)
    # 对角渐变底
    for y in range(H):
        t = y / H
        d.line([(0, y), (W, y)],
               fill=(int(17 + 14 * t), int(18 + 14 * t), int(20 + 16 * t)))
    # 琥珀声纹条（确定性伪随机）
    seed = abs(hash(topic)) % 2147483647 or 7
    def rnd():
        nonlocal seed
        seed = (seed * 16807) % 2147483647
        return seed / 2147483647
    x = 70
    while x < W - 70:
        h = 30 + rnd() * 300
        d.rounded_rectangle([x, 800 - h / 2, x + 14, 800 + h / 2], radius=6,
                            fill=(245, 158, 11))
        x += 26
    # 顶部品牌
    f_brand = _font(34)
    d.text((70, 64), "AI 播客工坊", font=f_brand, fill=(245, 158, 11))
    d.text((70, 112), "PODCAST STUDIO", font=_font(20), fill=(156, 163, 175))
    # 主标题（自动换行，最多 3 行）
    f_title = _font(84)
    lines, line = [], ""
    for ch in topic:
        if d.textlength(line + ch, font=f_title) > W - 140:
            lines.append(line); line = ch
            if len(lines) == 2:
                break
        else:
            line += ch
    lines.append(line)
    if len(lines) > 2:
        lines = lines[:2]
        lines[1] = lines[1][:-1] + "…"
    y = 300
    for ln in lines:
        d.text((70, y), ln, font=f_title, fill=(243, 244, 246))
        y += 108
    # 元信息胶囊
    f_meta = _font(36)
    meta = " · ".join(x for x in [style, f"{minutes} min"] if x)
    if meta:
        tw = d.textlength(meta, font=f_meta)
        d.rounded_rectangle([70, y + 24, 70 + tw + 56, y + 96], radius=36,
                            outline=(245, 158, 11), width=3)
        d.text((70 + 28, y + 38), meta, font=f_meta, fill=(245, 158, 11))
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(out_path, quality=92)
    return str(out_path)
