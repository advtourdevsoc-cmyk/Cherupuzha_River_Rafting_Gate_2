@echo off
echo ===================================================
echo   RAFT BOOKING SYSTEM - LOCAL LAUNCHER
echo ===================================================
echo.

IF NOT EXIST "venv" (
    echo [INFO] Creating virtual environment...
    python -m venv venv
)

echo [INFO] Activating virtual environment...
call venv\Scripts\activate

echo [INFO] Installing/Updating dependencies...
pip install -r requirements.txt
IF %ERRORLEVEL% NEQ 0 (
    echo [ERROR] Failed to install dependencies.
    echo Please check your internet connection or permissions.
    pause
    exit /b
)

echo [INFO] Setting environment variables...
set FLASK_APP=app.py
set FLASK_DEBUG=1

echo.
echo [INFO] ⚠️  WARNING: Admin credentials should be set in .env file, not hardcoded here!

echo.
echo [INFO] Starting Server...
echo [INFO] Open your browser to: http://127.0.0.1:5000
echo ===================================================
python app.py
pause
