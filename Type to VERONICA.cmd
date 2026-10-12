@echo off
title VERONICA
cd /d "%~dp0"
".venv\Scripts\python.exe" veronica.py %*
pause
