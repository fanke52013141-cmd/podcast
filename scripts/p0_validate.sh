#!/usr/bin/env bash
# ============================================================
# P0 环境验证脚本 —— 在装有 RTX 5080 的机器上运行（WSL2 Ubuntu 内）
# 用法: bash p0_validate.sh 2>&1 | tee p0_report.log
# 产物: p0_report.log（发回给助手分析）
# ============================================================
set -uo pipefail
step(){ echo; echo "===== [$1] ====="; }

step "0. 基础信息"
date; uname -a
free -h | head -2
df -h ~ | tail -1

step "1. GPU 检查（期望: RTX 5080, 显存 16GB, 驱动 CUDA >= 12.8）"
nvidia-smi || { echo "FATAL: nvidia-smi 不可用，检查 Windows 宿主驱动"; exit 1; }
nvidia-smi --query-gpu=name,memory.total,driver_version --format=csv

step "2. WSL CUDA 直通检查"
ls /usr/lib/wsl/lib/ | grep -i cuda || echo "WARN: 未见 wsl cuda lib"
/usr/lib/wsl/lib/nvidia-smi -L 2>/dev/null || true

step "3. Python 环境"
python3 --version 2>&1
command -v conda && conda --version || echo "no conda"
command -v uv && uv --version || echo "no uv (建议: curl -LsSf https://astral.sh/uv/install.sh | sh)"

step "4. 创建隔离环境 + 安装 torch cu128（Blackwell 关键步骤）"
if command -v uv >/dev/null; then
  uv venv .venv-p0 --python 3.10 && source .venv-p0/bin/activate
  uv pip install torch==2.7.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128
else
  python3 -m venv .venv-p0 && source .venv-p0/bin/activate
  pip install --upgrade pip
  pip install torch==2.7.1 torchaudio==2.7.1 --index-url https://download.pytorch.org/whl/cu128
fi

step "5. torch 可用性 + sm_120 架构确认（必须包含 sm_120）"
python3 - <<'EOF'
import torch
print("torch:", torch.__version__, "| cuda build:", torch.version.cuda)
print("device:", torch.cuda.get_device_name(0))
print("arch list:", torch.cuda.is_arch_list())
x = torch.randn(1024,1024,device="cuda"); y = x@x
torch.cuda.synchronize()
print("matmul OK, mem allocated(MB):", torch.cuda.memory_allocated()/1e6)
EOF
[ $? -ne 0 ] && echo "FATAL: cu128 torch 在本卡不可用 —— 立即把本报告发回分析"

step "6. 克隆 SoulX-Podcast 并安装依赖"
git clone https://github.com/Soul-AILab/SoulX-Podcast.git 2>/dev/null || (cd SoulX-Podcast && git pull)
cd SoulX-Podcast
pip install -r requirements.txt 2>&1 | tail -5   # torch 行已满足会跳过

step "7. 下载模型权重（约 3-4GB）"
pip install -q "huggingface_hub[cli]"
huggingface-cli download Soul-AILab/SoulX-Podcast-1.7B --local-dir pretrained_models/SoulX-Podcast-1.7B

step "8. 跑通官方中文双人对话示例（hf 后端）+ 显存/耗时实测"
nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits > /tmp/vram_before
START=$(date +%s)
# 官方示例默认 engine 以脚本内为准；如报显存不足, 在 cli/podcast.py 调用处加 fp16_flow
bash example/infer_dialogue.sh || echo "!! 示例失败, 完整报错见上方"
END=$(date +%s)
nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits > /tmp/vram_after
echo "生成耗时: $((END-START)) 秒（含模型加载）"
echo "显存: before=$(cat /tmp/vram_before)MB after=$(cat /tmp/vram_after)MB"

step "9. 输出音频检查"
ls -la outputs/ 2>/dev/null
ffprobe -v error -show_entries format=duration -of csv=p=0 outputs/mandarin.wav 2>/dev/null \
  && echo "✅ 音频可读" || echo "WARN: 需要 ffprobe (apt install ffmpeg)"

step "10. 结论"
echo "把 p0_report.log 全文发回。重点看: 步骤5是否含sm_120 / 步骤8是否产出wav / 实测显存与耗时。"
