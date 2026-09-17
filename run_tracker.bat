@echo off
title Antigravity Usage Tracker
cd /d "%~dp0"
echo ======================================================================
echo   Antigravity Usage Tracker
echo ======================================================================
echo.
echo Starting local service on port 8778...
echo Background poller running with live countdown and audit logging.
echo.
start "" "http://127.0.0.1:8778"
python -m tracker.server
pause
