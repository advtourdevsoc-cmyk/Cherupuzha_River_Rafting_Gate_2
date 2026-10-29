# Render.com Deployment Guide

This Flask + MongoDB Atlas application is production-ready for deployment on Render.com.

## Prerequisites

- **Render.com account** (https://render.com)
- **MongoDB Atlas cluster** (https://www.mongodb.com/cloud/atlas)
- **GitHub repository** with this code (Render deploys from Git)
- **Environment variables** configured in Render dashboard

## Step-by-Step Deployment

### 1. Prepare MongoDB Atlas

1. Go to [MongoDB Atlas](https://www.mongodb.com/cloud/atlas)
2. Create a cluster or use existing one
3. Create a database user with strong password
4. Whitelist Render IP addresses (or allow all: `0.0.0.0/0`)
5. Get your connection string: `mongodb+srv://username:password@cluster.mongodb.net/database_name?retryWrites=true&w=majority&appName=appname`

### 2. Push Code to GitHub

```bash
# Initialize git (if not already done)
git init
git add .
git commit -m "Prepare for production deployment"
git remote add origin https://github.com/YOUR_USERNAME/YOUR_REPO.git
git push -u origin main
```

**Important:** Verify `.gitignore` prevents committed secrets:
- ✅ `.env` is in `.gitignore` (secrets not committed)
- ✅ `venv/` is ignored
- ✅ `__pycache__/` is ignored

### 3. Create Render Web Service

1. Go to [Render Dashboard](https://dashboard.render.com)
2. Click **"New +"** → **"Web Service"**
3. Connect your GitHub repository
4. Configure:

   | Setting | Value |
   |---------|-------|
   | **Name** | `raft-booking-app` (or your choice) |
   | **Environment** | `Python 3` |
   | **Build Command** | `pip install -r requirements.txt` |
   | **Start Command** | `gunicorn app:app` |
   | **Instance Type** | `Free` (for testing) or `Starter` ($7/month) |

### 4. Add Environment Variables

In Render Dashboard → Service → **Environment**:

```
MONGO_URI=mongodb+srv://username:password@cluster.mongodb.net/raft_booking?retryWrites=true&w=majority&appName=rafting

SECRET_KEY=<generate-a-strong-key-here>

ENVIRONMENT=production

DEBUG=false
```

**Generate a strong SECRET_KEY:**
```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

### 5. Deploy

1. Click **"Deploy"** in Render dashboard
2. Monitor logs for any errors
3. Once deployment succeeds, your app is live at: `https://your-app-name.onrender.com`

## Verification

### Health Check Endpoint

Test your deployment:
```bash
curl https://your-app-name.onrender.com/health
```

**Expected response:**
```json
{
  "status": "ok",
  "db": "connected",
  "environment": "production"
}
```

If MongoDB connection fails, check:
- ✓ MONGO_URI is correct
- ✓ MongoDB Atlas network access allows Render IPs
- ✓ Database credentials are valid
- ✓ Database exists in MongoDB Atlas

### Test Application Features

1. **Home Page:** https://your-app-name.onrender.com/
2. **Login:** https://your-app-name.onrender.com/login
3. **Booking:** https://your-app-name.onrender.com/book

## Troubleshooting

### Application Won't Start
Check Render logs for errors:
1. Render Dashboard → Service → **Logs**
2. Look for Python errors or MongoDB connection issues
3. Verify environment variables are set correctly

### MongoDB Connection Timeout
```
Errors: 'NoneType' object is not iterable
```
**Solution:**
- Check `MONGO_URI` is set in Render environment variables
- Verify MongoDB Atlas network access allows Render IPs
- Test connection locally: `python -c "import pymongo; print(pymongo.MongoClient('YOUR_MONGO_URI').server_info())"`

### Import Errors
If you see: `ModuleNotFoundError: No module named 'flask'`
- Render may be using wrong Python version
- Check `runtime.txt` exists with `python-3.11.10`
- Redeploy or manually trigger build

### Static Files Not Loading
Static files are served by Gunicorn in limited mode. For production:
- Use a CDN (Cloudflare, AWS CloudFront)
- Or add a static file handler
- Render auto-serves from `static/` folder

## Production Features

✅ **Security Hardened:**
- Debug mode disabled
- Secure session cookies
- Hardened Flask configuration

✅ **Environment Management:**
- No hardcoded secrets
- Configuration via environment variables
- `.env.example` for reference

✅ **Monitoring:**
- Health check endpoint `/health`
- Structured logging for Render logs
- Error handling for database failures

✅ **Database:**
- MongoDB Atlas for reliable hosting
- Connection pooling via PyMongo
- Automatic failover with replica sets

## Scaling & Improvements

### 1. Add Custom Domain
Render Dashboard → Service → **Settings** → Add Custom Domain

### 2. Enable Auto-Deploy
Render → Service → **Settings** → Auto-Deploy to latest commit

### 3. Monitor Performance
- Check Render metrics (CPU, memory)
- Monitor MongoDB Atlas metrics
- Set up Render alerts for failures

### 4. Set Up Database Backups
MongoDB Atlas Backup Options:
- Automated backups (Free with M0 cannot restore)
- Snapshots (M2+ plans)
- Manual export to S3

### 5. Use Redis for Caching (Optional)
For improved performance, add Redis:
1. Render → New Service → **Redis**
2. Configure Flask-Caching to use Redis
3. Update requirements.txt with `redis==5.0.0`

## Common Issues & Solutions

| Issue | Cause | Solution |
|-------|-------|----------|
| 502 Bad Gateway | App crash or timeout | Check logs, verify MONGO_URI |
| 503 Database Down | MongoDB unavailable | Check Atlas status |
| Slow response | No caching | Add Redis or static file CDN |
| Can't connect to DB | Wrong credentials | Verify MONGO_URI and whitelist IPs |

## Local Development vs Production

### Development (run locally)
```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt
# Create .env with your MongoDB credentials
python app.py
```

### Production (Render)
- Uses `gunicorn app:app`
- No debug mode
- Secure cookies enabled
- All credentials from environment variables

## Monitoring & Maintenance

### View Logs
```bash
# In Render dashboard
Service → Logs → Last 100 lines
```

### Restart Service
```bash
# In Render dashboard
Service → Settings → Restart
```

### Redeploy Latest Code
```bash
# Push to GitHub, Render auto-deploys if enabled
# Or manually trigger in Render dashboard
```

## Cleanup & Removal

To delete the Render service:
1. Render Dashboard → Service → **Settings** → **Delete**
2. Confirm deletion
3. Remove from GitHub if desired

---

**Last Updated:** February 2026
**Framework:** Flask 3.1.2
**Database:** MongoDB Atlas
**Server:** Gunicorn on Render
