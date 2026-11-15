@echo off
rem Double-click to start the music bot. Close this window to stop it.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo First run: creating a virtual environment...
    py -3 -m venv .venv 2>nul || python -m venv .venv
    if errorlevel 1 (
        echo Could not create a virtual environment. Is Python 3.11+ installed and on PATH?
        pause
        exit /b 1
    )
)

echo Checking dependencies and updating yt-dlp...
".venv\Scripts\python.exe" -m pip install -q -r requirements.txt
".venv\Scripts\python.exe" -m pip install -q -U yt-dlp

echo Starting the bot...
".venv\Scripts\python.exe" -m musicbot.main

echo.
echo The bot has stopped.
pause
