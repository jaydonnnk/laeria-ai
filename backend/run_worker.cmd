@echo off
REM laeria.ai monitor worker launcher (Windows).
REM The worker loops internally every 30 min; this starts it and logs output.
cd /d "%~dp0"
".venv\Scripts\python.exe" -m workers.monitor_worker >> "%~dp0worker.log" 2>&1
