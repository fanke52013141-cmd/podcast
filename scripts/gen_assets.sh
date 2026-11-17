#!/usr/bin/env bash
# 重建程序化音频资产（BGM 4 首 12 分钟 loop + 3 个转场音效）
# 仓库不包含 mp3，克隆后运行一次即可；需要 PATH 中有 ffmpeg。
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p assets/bgm assets/sfx

echo "== BGM (4 × 12min) =="
ffmpeg -y -f lavfi -i "sine=frequency=220:duration=720" -f lavfi -i "sine=frequency=277:duration=720" -f lavfi -i "sine=frequency=330:duration=720" -filter_complex "[0][1][2]amix=inputs=3,tremolo=f=0.3:d=0.6,aecho=0.8:0.7:120:0.35,volume=0.9" -b:a 96k "assets/bgm/深夜电台_warm.mp3" 2>/dev/null
ffmpeg -y -f lavfi -i "sine=frequency=392:duration=720" -f lavfi -i "sine=frequency=494:duration=720" -filter_complex "[0][1]amix=inputs=2,tremolo=f=2.5:d=0.5,aecho=0.6:0.4:80:0.3,volume=0.7" -b:a 96k "assets/bgm/幽默对谈_light.mp3" 2>/dev/null
ffmpeg -y -f lavfi -i "sine=frequency=440:duration=720" -f lavfi -i "sine=frequency=554:duration=720" -f lavfi -i "sine=frequency=659:duration=720" -filter_complex "[0][1][2]amix=inputs=3,tremolo=f=4:d=0.7,volume=0.65" -b:a 96k "assets/bgm/热点解读_pulse.mp3" 2>/dev/null
ffmpeg -y -f lavfi -i "sine=frequency=165:duration=720" -f lavfi -i "sine=frequency=247:duration=720" -filter_complex "[0][1]amix=inputs=2,tremolo=f=0.2:d=0.8,aecho=0.9:0.8:200:0.4,volume=0.95" -b:a 96k "assets/bgm/故事剧场_cinematic.mp3" 2>/dev/null

echo "== SFX (3) =="
ffmpeg -y -f lavfi -i "anoisesrc=color=pink:duration=0.7" -af "lowpass=f=2600,highpass=f=300,afade=t=in:d=0.18,afade=t=out:st=0.4:d=0.3,volume=0.8" -b:a 128k assets/sfx/whoosh.mp3 2>/dev/null
ffmpeg -y -f lavfi -i "sine=frequency=880:duration=1.2" -f lavfi -i "sine=frequency=1320:duration=1.2" -filter_complex "[0][1]amix=inputs=2,afade=t=out:st=0.05:d=1.1,volume=0.5" -b:a 128k assets/sfx/ding.mp3 2>/dev/null
ffmpeg -y -f lavfi -i "sine=frequency=160:duration=0.12" -af "afade=t=out:st=0.02:d=0.09,volume=0.9" -b:a 128k assets/sfx/pop.mp3 2>/dev/null

echo "done: $(ls assets/bgm | wc -l) bgm, $(ls assets/sfx | wc -l) sfx"
