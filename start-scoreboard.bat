@echo off
cd /d "%~dp0"
start "" "http://127.0.0.1:8000/obs_multisport_scoreboard.html?mode=control"
py server.py
if errorlevel 1 python server.py
pause
