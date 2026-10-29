# Quick Reference - Admin Login Setup

## 🚀 Quick Start

### 1. Initialize Admin User
```bash
python scripts/init_db.py
```

### 2. Create Sub-Admin User (Optional)
```bash
python scripts/create_subadmin.py
```

### 3. Test Connection
```bash
python test_atlas_connection.py
```

### 4. Test Admin Login
```bash
python test_admin_login.py
```

### 5. Start Application
```bash
python app.py
```

## 📋 Login Credentials

Credentials are configured in the `.env` file. Do not expose or commit credentials to version control.

| Role | Email | Password |
|------|-------|----------|
| Supreme Admin | See `.env` (SUPREME_ADMIN_EMAIL) | See `.env` (SUPREME_ADMIN_PASSWORD) |
| Sub-Admin | See `.env` (SUBADMIN_EMAIL) | See `.env` (SUBADMIN_PASSWORD) |

**To update credentials:** Edit the `.env` file and run:
```bash
python scripts/init_db.py
python scripts/create_subadmin.py
```

## 🔗 Important URLs

| Page | URL |
|------|-----|
| Login | http://localhost:5000/login |
| Admin Dashboard | http://localhost:5000/admin/dashboard |
| Calendar | http://localhost:5000/admin/calendar |
| Settings | http://localhost:5000/admin/settings |
| Health Check | http://localhost:5000/health |

## 🔧 MongoDB Collections

```
raft_booking
├── users       - Admin/Sub-admin credentials
├── settings    - System configuration
├── bookings    - Booking records
└── rafts       - Raft occupancy data
```

## ✅ What's Fixed

- ✅ Database initialization now connects to MongoDB Atlas
- ✅ Admin user created in MongoDB Atlas
- ✅ Sub-admin user can be created in MongoDB Atlas
- ✅ Login page queries MongoDB Atlas
- ✅ Admin dashboard accesses MongoDB Atlas
- ✅ All admin operations persist to MongoDB Atlas

## 🐛 Troubleshooting

### Connection Issues
```bash
python test_atlas_connection.py
```

### Login Issues
```bash
python test_admin_login.py
```

### Reset Admin
```bash
python scripts/init_db.py
```

## 📝 Files Modified

- `scripts/init_db.py` ✏️
- `scripts/create_subadmin.py` ✏️

## 🔐 Security Notes

✅ **All admin credentials are now managed through the `.env` file**
- Supreme Admin credentials: `SUPREME_ADMIN_EMAIL` and `SUPREME_ADMIN_PASSWORD`
- Sub-admin credentials: `SUBADMIN_EMAIL` and `SUBADMIN_PASSWORD`
- Never commit `.env` to version control
- Use strong, unique passwords in production
- Rotate credentials regularly

### Environment Variables
```
SUPREME_ADMIN_EMAIL="your-admin@example.com"
SUPREME_ADMIN_PASSWORD="strong-secure-password"
SUBADMIN_EMAIL="your-subadmin@example.com"
SUBADMIN_PASSWORD="strong-secure-password"
```

## 🎯 Next Steps

1. Test login with credentials above
2. Configure custom admin/sub-admin passwords
3. Set up MongoDB Atlas backups
4. Configure production authentication

---

**Status**: ✅ Ready to use!
