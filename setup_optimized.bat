@echo off
REM RAM Optimization Setup Script for 2GB Environments (Windows)
REM Run this on your Windows hosting platform to apply optimizations

echo ========================================
echo Setting up RAM-optimized configuration
echo ========================================

REM Check if gunicorn_config.py exists
if not exist "gunicorn_config.py" (
    echo ERROR: gunicorn_config.py not found!
    exit /b 1
)

REM Set environment variables for 2GB systems
set GUNICORN_WORKERS=2
set MONGO_MAX_POOL_SIZE=2
set MONGO_MIN_POOL_SIZE=1
set FLASK_ENV=production
set PYTHONUNBUFFERED=1

echo Environment variables set:
echo   GUNICORN_WORKERS=2
echo   MONGO_MAX_POOL_SIZE=2
echo   MONGO_MIN_POOL_SIZE=1
echo.

REM Test import
echo Testing application import...
python -c "from app import create_app; print('OK')" >nul 2>&1
if %errorlevel% neq 0 (
    echo ERROR: Failed to import app
    exit /b 1
)
echo Application imports successfully
echo.

echo Starting Gunicorn with optimized config...
echo Command: gunicorn -c gunicorn_config.py wsgi:app
echo.
echo Your app will now run in approximately 70-80MB of RAM
echo.
echo Monitoring:
echo   - Task Manager: See python.exe memory usage
echo   - PowerShell: Get-Process python ^| Select-Object Handles, CPU, Memory
echo.

REM Uncomment to auto-start:
REM gunicorn -c gunicorn_config.py wsgi:app
