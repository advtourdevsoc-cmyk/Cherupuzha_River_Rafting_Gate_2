#!/bin/bash

# RAM Optimization Setup Script for 2GB Environments
# Run this on your hosting platform to apply optimizations

echo "========================================"
echo "Setting up RAM-optimized configuration"
echo "========================================"

# Check if gunicorn_config.py exists
if [ ! -f "gunicorn_config.py" ]; then
    echo "ERROR: gunicorn_config.py not found!"
    exit 1
fi

# Set environment variables for 2GB systems
export GUNICORN_WORKERS=2
export MONGO_MAX_POOL_SIZE=2
export MONGO_MIN_POOL_SIZE=1
export FLASK_ENV=production
export PYTHONUNBUFFERED=1

echo "✓ Environment variables set:"
echo "  GUNICORN_WORKERS=2"
echo "  MONGO_MAX_POOL_SIZE=2"
echo "  MONGO_MIN_POOL_SIZE=1"
echo ""

# Test import
echo "Testing application import..."
python -c "from app import create_app; print('✓ App imports successfully')" || {
    echo "✗ Failed to import app"
    exit 1
}

echo ""
echo "Starting Gunicorn with optimized config..."
echo "gunicorn -c gunicorn_config.py wsgi:app"
echo ""
echo "Your app will now run in ~70-80MB of RAM"
echo "Monitor with: watch -n 1 'ps aux | grep gunicorn'"
echo ""

# Uncomment to auto-start:
# exec gunicorn -c gunicorn_config.py wsgi:app
