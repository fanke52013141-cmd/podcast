# AI 播客 TTS 服务启动器（引擎可在启动时选择：soulx | indextts）
#   soulx    = SoulX-Podcast 1.7B，跑在 WSL(5080)，双人对话感更自然（默认）
#   indextts = IndexTTS 2.5，走 ComfyUI(8188)，逐轮重做/语速控制更灵活
param(
    [string]$Engine = "soulx"
)
$ErrorActionPreference = "Stop"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$ComfyBat = "D:\软件\Wan2.2-ReMix-SVI2-V3\Wan2.2-ReMix-SVI2-V3\启动-IndexTTS25-ComfyUI.bat"
$Proj = "D:\Program Files (x86)\podcast"

function Test-Comfy {
    try {
        $r = Invoke-WebRequest -UseBasicParsing -TimeoutSec 3 "http://127.0.0.1:8188/system_stats"
        return $r.StatusCode -eq 200
    } catch { return $false }
}

Write-Host "== AI 播客 TTS 服务（引擎: $Engine）=="

if ($Engine -eq "indextts") {
    if (-not (Test-Comfy)) {
        Write-Host "[TTS] ComfyUI 未运行，自动启动..."
        Start-Process -FilePath $ComfyBat -WorkingDirectory (Split-Path $ComfyBat)
        $waited = 0
        while (-not (Test-Comfy)) {
            Start-Sleep -Seconds 10
            $waited += 10
            Write-Host "[TTS] 等待 ComfyUI 就绪... ${waited}s"
            if ($waited -gt 600) { Write-Host "[TTS] 超时：ComfyUI 10 分钟未就绪，请手动检查"; exit 1 }
        }
    }
    Write-Host "[TTS] ComfyUI 就绪，启动 TTS 服务 (http://127.0.0.1:8600)"
    $env:TTS_ENGINE = "indextts"
    Set-Location $Proj
    python -m uvicorn tts_service.server:app --host 0.0.0.0 --port 8600
} else {
    # SoulX：跑在 WSL（torch cu128 + 模型都在 /root）
    Write-Host "[TTS] 以 SoulX 引擎在 WSL 启动 (http://127.0.0.1:8600)"
    Write-Host "[TTS] 提示：若显存紧张，请先关闭 ComfyUI 窗口（16GB 显存两者同时跑可能吃紧）"
    $bashCmd = @'
source /root/podenv/bin/activate && cd '/mnt/d/Program Files (x86)/podcast' && export SOULX_REPO_DIR=/root/SoulX-Podcast SOULX_MODEL_PATH=/root/SoulX-Podcast/pretrained_models/SoulX-Podcast-1.7B PYTHONPATH='/mnt/d/Program Files (x86)/podcast' TTS_ENGINE=soulx && exec python -m uvicorn tts_service.server:app --host 0.0.0.0 --port 8600
'@
    wsl -d Podcast-Ubuntu -- bash -c $bashCmd
}
