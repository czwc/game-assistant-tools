@echo off
chcp 65001 >nul
cd /d "F:\游戏辅助工具"
python "军师之眼.py"
if errorlevel 1 pause
