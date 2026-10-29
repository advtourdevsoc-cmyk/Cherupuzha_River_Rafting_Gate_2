# Gunicorn configuration optimized for 2GB RAM environment
# Usage: gunicorn -c gunicorn_config.py wsgi:app

import multiprocessing
import os

# Get available CPU cores
cpu_cores = multiprocessing.cpu_count()

# ============================================================================
# WORKER CONFIGURATION - RAM OPTIMIZED FOR 2GB
# ============================================================================
# Formula: (Total RAM - 512MB buffer) / (RAM per worker - overhead)
# With 2GB: (2048 - 512) / (50 + 20) = ~25 workers possible, but we're conservative

# For 2GB RAM environment:
# - Use fewer workers to save RAM
# - Typical production: 4-6 workers
# - Conservative (low RAM): 2-3 workers
workers = max(2, cpu_cores // 2)  # At least 2, typically 2-4 workers

# Worker timeout (seconds) - time before worker is killed if hung
timeout = 30

# Worker class - use "sync" for memory efficiency
worker_class = 'sync'

# Max requests per worker before restart (helps prevent memory leaks)
max_requests = 1000
max_requests_jitter = 50

# ============================================================================
# MEMORY OPTIMIZATION
# ============================================================================

# Graceful timeout (give worker time to finish current request before killing)
graceful_timeout = 30

# Server-wide keep-alive for persistent connections
keepalive = 5

# Limit size of HTTP request body (prevent memory exhaustion from large uploads)
limit_request_fields = 100
limit_request_field_size = 8190  # bytes

# Limit the number of concurrent connections per worker
limit_request_line = 8190  # bytes

# ============================================================================
# CONNECTION POOLING
# ============================================================================

# Backlog for listen socket (connection queue)
backlog = 2048

# ============================================================================
# PERFORMANCE & STABILITY
# ============================================================================

# Bind to all interfaces on port 5000
bind = ['0.0.0.0:5000']

# Access and error logs
accesslog = 'logs/gunicorn_access.log'
errorlog = 'logs/gunicorn_error.log'
loglevel = 'info'

# Pre-fork vs post-fork (worker spawn method)
# 'fork' is default and more efficient than 'spawn'
preload_app = False  # Set to True if you want to share app across workers (saves memory)

# ============================================================================
# SETTINGS FOR 2GB ENVIRONMENT
# ============================================================================

# If you have exactly 2GB and want maximum efficiency:
# Uncomment the line below
# workers = 2
# max_requests = 500

# If you have 2GB and expect moderate traffic:
# KEEP DEFAULT ABOVE (2-4 workers)

# If you have 2GB and expect high traffic:
# Add load balancing/caching (nginx, redis) instead of more workers

# ============================================================================
# ENVIRONMENT VARIABLE OVERRIDES
# ============================================================================

workers = int(os.environ.get('GUNICORN_WORKERS', workers))
