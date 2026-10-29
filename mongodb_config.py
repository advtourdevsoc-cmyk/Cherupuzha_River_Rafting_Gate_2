# Optimized MongoDB Connection Configuration
# Save this file and import in your app.py to reduce RAM usage

MONGO_CONFIG = {
    # Connection pooling - CRITICAL for 2GB RAM
    'maxPoolSize': 2,          # Reduce from default 50 to save RAM (~25MB)
    'minPoolSize': 1,          # Minimum connections to keep open
    'maxIdleTimeMS': 30000,    # Close idle connections after 30 seconds
    'waitQueueTimeoutMS': 5000,
    
    # Connection timeouts (prevent hung connections)
    'serverSelectionTimeoutMS': 10000,
    'socketTimeoutMS': 10000,
    'connectTimeoutMS': 10000,
    
    # Write/read settings for better performance
    'retryWrites': True,
    'w': 1,  # Write concern - balance between safety and speed
}

# For 2GB with modest traffic:
# maxPoolSize=2, minPoolSize=1

# For 2GB with moderate traffic:
# maxPoolSize=5, minPoolSize=2

# For 2GB with high traffic:
# maxPoolSize=10, minPoolSize=5
# (In this case, consider adding Redis caching instead)
