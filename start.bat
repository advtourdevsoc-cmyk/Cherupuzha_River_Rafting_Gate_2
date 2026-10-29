@echo off
title Raft Booking System - Auto Launcher
cd /d "%~dp0"
echo Checking MongoDB service...
sc query MongoDB | find "RUNNING" > nul
if errorlevel 1 (
    echo MongoDB is not running. Attempting to start it...
    net start MongoDB
    if errorlevel 1 (
        echo Could not start MongoDB. Please start it manually as Administrator.
        pause
        exit /b
    )
) else (
    echo MongoDB is running.
)

if not exist "venv" (
    echo Creating virtual environment...
    python -m venv venv
)

echo Activating virtual environment...
call venv\Scripts\activate

if not exist "venv\Lib\site-packages\flask" (
    echo Installing dependencies...
    pip install -r requirements.txt
)

echo Initializing database (admin + settings)...
python scripts\init_db.py

echo Starting Flask app...
start "" http://127.0.0.1:5000
python app.py
pause
