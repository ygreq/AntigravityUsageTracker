@echo off
echo Stopping Antigravity Usage Tracker on port 8778...
for /f "tokens=5" %%a in ('netstat -aon ^| findstr ":8778 "') do (
    taskkill /F /PID %%a >nul 2>&1
)
echo Service stopped successfully.
timeout /t 2 >nul
