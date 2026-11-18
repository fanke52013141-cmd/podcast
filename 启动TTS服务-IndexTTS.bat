@echo off
title AI Podcast TTS (IndexTTS 2.5)
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_tts.ps1" -Engine indextts
pause
