@echo off
chcp 65001 >nul
title AI 播客
cd /d "D:\Program Files (x86)\podcast"

echo ============================================
echo   AI 播客
echo   服务地址: http://127.0.0.1:8765
echo   关闭本窗口即可停止服务
echo ============================================
echo.

rem 8 秒后自动打开浏览器（等待服务就绪）
start "" /min cmd /c "timeout /t 8 /nobreak >nul & start """" http://127.0.0.1:8765"

python -m uvicorn webapp.server:app --host 127.0.0.1 --port 8765
