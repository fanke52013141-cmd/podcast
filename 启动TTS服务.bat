@echo off
title AI Podcast TTS Service
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_tts.ps1"
pause
